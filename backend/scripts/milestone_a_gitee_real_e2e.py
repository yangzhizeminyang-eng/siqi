"""里程碑 A：真实 Gitee → PR 之 E2E 驱动（HTTP 对真实 FastAPI :8000，team/MySQL/Redis/Celery）。

依赖运行态（另起进程启动，均非 eager）：
- FastAPI  Uvicorn :8000   APP_ENV=team + MySQL agent_team + USE_CELERY=true + broker=redis
- Celery  worker（redis broker，消费 backend.* 任务）
- 环境变量已含 GITEE_TOKEN（由 runtime_config 从 backend/.env 加载，本脚本不硬编码、不打印）。

执行（working dir = backend）：
    .\\.venv\\Scripts\\python.exe -m scripts.milestone_a_gitee_real_e2e

产物：把"真实 Gitee→Issue→自动建 Task→Agent→Diff/Test→PR 描述"证据打印到 stdout（脱敏，无 token），
另可写 manifest 到 backend/.data/e2e_manifest.json 供验收。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")  # Windows 控制台 utf-8
except Exception:
    pass

import httpx

from app.core import runtime_config  # noqa  F401 —— 触发 backend/.env 加载（含 GITEE_TOKEN）

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8000")
TOKEN = os.environ.get("GITEE_TOKEN", "").strip()
GITEE_OWNER = os.environ.get("GITEE_OWNER", "yang-zhizemin")
GITEE_REPO = os.environ.get("GITEE_REPO", "dreamweaver-studio-pro")
MANIFEST: dict = {}

log = print


def flag(k, v):
    MANIFEST[k] = v
    log(f"[E2E] {k} = {v}")


def main() -> int:
    from scripts.authorization_gate import require_authorization

    require_authorization(
        os.environ.get("AUTHORIZATION_OPERATION", ""),
        os.environ.get("AUTHORIZATION_LOG", "") or None,
    )
    if not TOKEN:
        log("[E2E] 缺 GITEE_TOKEN（backend/.env 未配置或为空），退出。")
        return 2
    flag("token_present", True)
    flag("token_fingerprint", __import__("hashlib").sha256(TOKEN.encode()).hexdigest()[:8])
    log(f"[E2E] 目标仓库 {GITEE_OWNER}/{GITEE_REPO}，SERVER={BASE}")

    c = httpx.Client(base_url=BASE, timeout=60.0, follow_redirects=False)

    def j(r):
        try:
            return r.json()
        except Exception:
            return {"_raw": r.text[:200]}

    # 1) 登录 admin（具备 project:manage / request:create / approval 全权限）
    lg = c.post("/api/auth/login", json={"account": os.environ.get("E2E_ADMIN", "admin@company.com"),
                                         "password": os.environ.get("E2E_ADMIN_PWD", "Admin123!")})
    log(f"[1] login admin -> {lg.status_code}")
    if lg.status_code != 200:
        return 2
    c.cookies = lg.cookies

    # 2) 取一个种子项目
    ps = c.get("/api/projects")
    projects = j(ps).get("data", []) if ps.status_code == 200 else []
    if not projects:
        log("[2] 无种子项目，退出。"); return 2
    proj = projects[0]
    flag("project_id", proj["id"])
    log(f"[2] 使用项目 {proj['id']}  name={proj.get('name')}")

    # 3) 建真实仓库（拉到 Gitee；gitee_token 来自环境变量，服务端只存 alias/configured，不落明文）
    repo_payload = {
        "project_id": proj["id"],
        "name": f"gitee-real-{GITEE_REPO}",
        "url": f"https://gitee.com/{GITEE_OWNER}/{GITEE_REPO}.git",
        "default_branch": "master",
        "gitee_token": TOKEN,
        "test_command": "echo ok",
        "command_whitelist": ["echo ok", "echo ok && true"],
        "test_command_fallback": True,
        "auto_create_task_after_sync": True,
        "approval_owner_id": proj.get("owner_id"),
        "approval_mode": "single",
        "risk_threshold": "medium",
        "max_files": 50,
        "max_retries": 2,
        "blocked_paths": [],
    }
    rr = c.post("/api/repositories", json=repo_payload)
    data = j(rr).get("data", {})
    log(f"[3] create repository -> {rr.status_code}")
    if rr.status_code != 200:
        log(f"[3] repo err: {j(rr)}"); return 2
    repo_id = data["id"]
    flag("repository_id", repo_id)
    log(f"[3] repository {repo_id} url={data.get('url')} auto_create={data.get('auto_create_task_after_sync')}")

    # 4) 建团队需求（长 body 保证 content_sufficient）
    body = (
        "【验收·真实 Gitee 闭环】验证平台需求能够在真实 Gitee 仓库创建一个 Issue，"
        "并自动派生 Agent 任务、走分析/计划/审批/Diff/测试/PR 描述全链路。"
        "这是一个足够长的需求描述，用于通过内容充分性校验，避免被判定为上下文不足。"
    )
    req_payload = {
        "project_id": proj["id"],
        "repository_id": repo_id,
        "type": "bug",
        "title": f"真实Gitee闭环E2E {time.strftime('%m%d%H%M%S')}",
        "body": body,
        "reproduction_steps": "1. 在 Gitee 打开该仓库 2. 确认自动建任务 3. 推进 Agent 链路",
        "acceptance_criteria": ["Gitee Issue 真实创建", "自动建 Agent Task", "走完 Diff/Test/PR 描述"],
        "priority": "high",
        "labels": ["bug", "e2e"],
        "target_branch": "master",
        "test_command": "echo ok",
    }
    ar = c.post("/api/team/requests", json=req_payload)
    ad = j(ar).get("data", {})
    log(f"[4] create team request -> {ar.status_code}")
    if ar.status_code != 200:
        log(f"[4] req err: {j(ar)}"); return 2
    req_id = ad["id"]
    flag("team_request_id", req_id)
    log(f"[4] team request {req_id} sync_status={ad.get('sync_status')}")

    # 5) 同步 Gitee（team+Celery：应返回 queued，不阻塞 HTTP）
    sm = c.post(f"/api/team/requests/{req_id}/sync-gitee")
    sd = j(sm).get("data", {})
    log(f"[5] sync-gitee -> {sm.status_code}  data={json.dumps(sd, ensure_ascii=False)[:220]}")
    flag("sync_dispatched", sm.status_code == 200)

    # 6) 轮询需求直至 SYNCED/TASK_CREATED 或 SYNC_FAILED
    req_status = ""
    deadline = time.time() + 180
    while time.time() < deadline:
        g = c.get(f"/api/team/requests/{req_id}")
        d = j(g).get("data", {})
        req_status = d.get("sync_status")
        e = d.get("gitee_issue_url")
        task_id = d.get("task_id")
        if req_status in {"SYNCED", "TASK_CREATED", "SYNC_FAILED"}:
            flag("sync_status", req_status)
            flag("gitee_issue_url", e)
            flag("team_task_id", task_id)
            log(f"[6] request status={req_status} issue={e} task_id={task_id}")
            break
        log(f"[6] polling... status={req_status}")
        time.sleep(4)
    else:
        log("[6] timeout waiting sync status"); return 2
    if req_status == "SYNC_FAILED":
        log("[6] SYNC_FAILED —— 真实同步失败，见审计。" ); return 2

    # 任务可能在上一步就位；否则手动触发 create-task
    task_id = MANIFEST.get("team_task_id")
    if not task_id:
        ct = c.post(f"/api/team/requests/{req_id}/create-task")
        ctd = j(ct).get("data", {})
        task_id = ctd.get("task_id") or ctd.get("id")
        flag("create_task_via_api", True)
        log(f"[6b] manual create-task -> {ct.status_code} task={task_id} msg={ctd.get('message')}")
    if not task_id:
        log("[6b] 无 task_id，退出。"); return 2

    # 7) 取任务详情，观察 agent 推进 & 计划（等待计划生成以批准）
    plan_id = None
    status = ""
    step_nodes = []
    deadline = time.time() + 420
    approved = False
    pr_url = None
    while time.time() < deadline:
        g = c.get(f"/api/tasks/{task_id}")
        d = j(g).get("data", {})
        status = d.get("status")
        next_action = d.get("next_action")
        steps = d.get("execution_steps", [])
        step_nodes = [s.get("node") if isinstance(s, dict) else s for s in steps]
        plans = d.get("plans", [])
        if not plan_id and plans and isinstance(plans, list) and plans:
            first = plans[-1] if isinstance(plans[-1], dict) else {}
            plan_id = first.get("id") or first.get("plan_id")
            flag("plan_id", plan_id)
            log(f"[7] 发现计划 plan_id={plan_id} status={status} next_action={next_action}")
        if plan_id and not approved and status in {"WAIT_APPROVAL", "PENDING_APPROVAL", "IN_REVIEW"}:
            ap = c.post(f"/api/tasks/{task_id}/plans/{plan_id}/approve")
            log(f"[7] approve -> {ap.status_code} {j(ap).get('message','')}")
            approved = ap.status_code == 200
            flag("approved", approved)
        # pr 描述就绪信号
        if d.get("pr_descriptions") or next_action in {"generate_pr_description", "PR_DESC"}:
            log(f"[7] 进入 PR 描述阶段 status={status} next_action={next_action}")
        if status in {"COMPLETED", "FAILED", "CANCELLED"}:
            log(f"[7] 任务到达终态 status={status} next_action={next_action}")
            break
        log(f"[7] poll task status={status} next={next_action} nodes={step_nodes[-4:]}")
        time.sleep(5)
    else:
        log("[7] timeout observing agent chain")

    flag("task_final_status", status)
    flag("execution_step_nodes", step_nodes)

    # 8) PR 描述生成（真实接口，无真实 PR 创建接口——见文档"PR 创建未实现"）
    pr = c.post(f"/api/tasks/{task_id}/pr-description")
    prd = j(pr).get("data", {})
    versions = prd.get("pr_descriptions") or []
    flag("pr_description_generated", pr.status_code == 200 and bool(prd.get("pr_description_text") or versions))
    log(f"[8] pr-description -> {pr.status_code}  versions={len(versions)}")
    if versions and isinstance(versions[0], dict):
        log(f"[8] latest pr desc (truncated, 300 chars): {json.dumps(versions[0].get('text') or versions[0].get('content') or versions[0], ensure_ascii=False)[:300]}")

    # 9) 读 MySQL 审计/步骤落库（只读，脱敏）
    try:
        from sqlalchemy import create_engine, inspect, text
        eng = create_engine(os.environ.get("DATABASE_URL", ""), connect_args={"connect_timeout": 8},
                            pool_pre_ping=True)
        with eng.connect() as conn:
            for t in ["tasks", "plans", "test_runs", "pr_descriptions", "task_events", "execution_steps", "audit_events"]:
                if t in inspect(eng).get_table_names():
                    n = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
                    flag(f"db:{t}.count", n)
            au = conn.execute(text("SELECT COUNT(*) FROM audit_events WHERE event_type LIKE %s"), ("GITEE%",)).scalar() \
                if "audit_events" in inspect(eng).get_table_names() else 0
            flag("db:audit_events.gitee_like", au)
        eng.dispose()
    except Exception as ex:
        log(f"[9] db read warning: {ex}")

    # 10) 写脱敏 manifest（可被验收脚本消费）
    Path(".data").mkdir(parents=True, exist_ok=True)
    p = Path(".data") / "e2e_manifest.json"
    p.write_text(json.dumps(MANIFEST, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[10] manifest -> {p.resolve()}")
    log(f"[E2E-DONE] sync={req_status} task={status} pr_desc_generated={MANIFEST.get('pr_description_generated')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())