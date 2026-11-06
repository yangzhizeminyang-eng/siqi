"""里程碑 A 收尾：从 TASK-8801 继续，驱动 Agent 全链到 PR 描述（真实 Gitee→PR·除 PR 创建）。

断点续跑：索引已排队（idx_428ed2586b36）→ 等待 COMPLETED → Agent 分析/计划 → 批准 →
Diff → 测试 → PR 描述。HTTP 只读+触发，长任务由真实 Celery worker 消费。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass
import httpx
from app.core import runtime_config  # noqa

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8000")
TASK_ID = os.environ.get("E2E_TASK_ID", "TASK-8801")
REPO_ID = os.environ.get("E2E_REPO_ID", "repo_5f9ca2d1cb61")
IDX_ID = os.environ.get("E2E_INDEX_ID", "idx_428ed2586b36")
MANIFEST = {}

def log(*a):
    print("[E2E]", *a)

def main():
    from scripts.authorization_gate import require_authorization

    require_authorization(
        os.environ.get("AUTHORIZATION_OPERATION", ""),
        os.environ.get("AUTHORIZATION_LOG", "") or None,
    )
    c = httpx.Client(base_url=BASE, timeout=60)
    c.cookies = c.post("/api/auth/login", json={"account": "admin@company.com", "password": "Admin123!"}).cookies
    def jr(r):
        try: return r.json().get("data", {})
        except Exception: return {}
    log(f"task={TASK_ID} index={IDX_ID}")

    # 1) 等索引完成
    idx_status = ""
    t0 = time.time()
    while time.time() - t0 < 900:
        g = c.get(f"/api/repositories/{REPO_ID}/indexes")
        idxs = jr(g)
        cur = None
        if isinstance(idxs, list):
            for x in idxs:
                if x.get("id") == IDX_ID: cur = x; break
        elif isinstance(idxs, dict):
            cur = idxs
        if cur: idx_status = cur.get("status", idx_status)
        log(f"index status={idx_status} ({int(time.time()-t0)}s)")
        if idx_status in {"COMPLETED", "FAILED"}:
            break
        time.sleep(8)
    log("--index-final--", idx_status)
    MANIFEST["index_status"] = idx_status

    # 2) 观察 Agent 到批准点
    status = ""; next_action = ""; plan_id = None; approved = False
    steps_seen = set(); events_seen = []
    t0 = time.time()
    while time.time() - t0 < 900:
        d = jr(c.get(f"/api/tasks/{TASK_ID}"))
        status = d.get("status", status); next_action = d.get("next_action", next_action)
        for s in d.get("execution_steps", []):
            steps_seen.add(s.get("node") if isinstance(s, dict) else s)
        for e in d.get("events", []):
            if isinstance(e, dict) and e.get("event_type"): events_seen.append(e["event_type"])
        plans = d.get("plans", [])
        if not plan_id and plans:
            last = plans[-1] if isinstance(plans[-1], dict) else {}
            plan_id = last.get("id") or last.get("plan_id")
            log(f"plan ready plan_id={plan_id} status={status} next={next_action}")
        if not approved and plan_id and status in {"WAIT_APPROVAL", "PENDING_APPROVAL", "IN_REVIEW", "PLANNING"}:
            ap = c.post(f"/api/tasks/{TASK_ID}/plans/{plan_id}/approve")
            body = ap.json().get("data", {})
            log(f"approve -> {ap.status_code} msg={ap.json().get('message','')} new_status={body.get('status')}")
            approved = ap.status_code == 200
            MANIFEST["approved"] = approved
        if status in {"COMPLETED", "FAILED", "CANCELLED"}:
            log(f"terminal task status={status}")
            break
        if (int(time.time()-t0)) % 30 < 8:
            log(f"task status={status} next={next_action} nodes={sorted(steps_seen)} events={events_seen[-6:]}")
        time.sleep(6)
    MANIFEST["task_status"] = status
    MANIFEST["nodes"] = sorted(steps_seen)
    MANIFEST["events_tail"] = events_seen[-12:]

    # 3) PR 描述生成（真实接口；PR 创建无实现=诚实阻断）
    pr = c.post(f"/api/tasks/{TASK_ID}/pr-description")
    prd = jr(pr)
    latest = (prd.get("pr_descriptions") or [])
    txt = prd.get("pr_description_text") or (latest[-1].get("text") if latest and isinstance(latest[-1], dict) else "")
    MANIFEST["pr_description_generated"] = (pr.status_code == 200) and bool(txt)
    log(f"pr-description -> {pr.status_code} generated={MANIFEST['pr_description_generated']}")
    if txt: log("pr_desc_head:", txt[:200].replace("\n", " "))

    # 4) MySQL 落库核验（只读）
    try:
        from sqlalchemy import create_engine, inspect, text
        eng = create_engine(os.environ.get("DATABASE_URL", ""), connect_args={"connect_timeout": 8}, pool_pre_ping=True)
        with eng.connect() as conn:
            for t in ["tasks","plans","diffs","test_runs","pr_descriptions","task_events","execution_steps","audit_events"]:
                if t in inspect(eng).get_table_names():
                    MANIFEST[f"db:{t}.count"] = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
            MANIFEST["db:task_events_for_task"] = conn.execute(
                text("SELECT COUNT(*) FROM task_events WHERE task_id=:t"), {"t": TASK_ID}).scalar()
            MANIFEST["db:execution_steps_for_task"] = conn.execute(
                text("SELECT COUNT(*) FROM execution_steps WHERE task_id=:t"), {"t": TASK_ID}).scalar()
            MANIFEST["db:gitee_like_audit"] = conn.execute(
                text("SELECT COUNT(*) FROM audit_events WHERE event_type LIKE 'GITEE%'")).scalar()
        eng.dispose()
    except Exception as ex:
        log("db read warn:", ex)

    Path(".data").mkdir(parents=True, exist_ok=True)
    (Path(".data")/"e2e_resume_manifest.json").write_text(json.dumps(MANIFEST, ensure_ascii=False, indent=2), encoding="utf-8")
    log("MANIFEST:", json.dumps(MANIFEST, ensure_ascii=False))
    log("DONE")

if __name__ == "__main__":
    main()