r"""真实 Redis+Celery 业务闭环 smoke：投递真实业务任务，验证 worker 消费后业务状态变化。

前置（本机需真实 Redis + 已启动真实 worker，且 worker 与脚本共用同一 DATABASE_URL）：
    终端1 Redis:  docker run -d --name agent-redis -p 6379:6379 redis:7-alpine   （或已有 Redis）
    终端2 Worker:
        $env:USE_SQLALCHEMY="true"; $env:DATABASE_URL="sqlite:///.data/app.db"
        $env:USE_CELERY="true"; $env:CELERY_BROKER_URL="redis://127.0.0.1:6379/0"; $env:CELERY_RESULT_BACKEND="redis://127.0.0.1:6379/1"; $env:REDIS_URL="redis://127.0.0.1:6379/2"
        ...deploy/dev-worker.ps1      # 日志 logs/celery-worker.out.log
    终端3 本脚本:
        ...scripts.celery_real_smoke
"""

from __future__ import annotations

import datetime as _dt
import os
import time
from pathlib import Path

import redis as _redis_lib


def _env(k, d=""):
    return os.getenv(k, d).strip() or d


def redis_ping():
    broker = _env("CELERY_BROKER_URL")
    try:
        c = _redis_lib.Redis.from_url(broker, socket_connect_timeout=3, socket_timeout=3)
        ok = bool(c.ping())
        ver = c.info("server").get("redis_version")
        c.close()
        return ok, ver
    except Exception as e:
        return False, str(e)[:100]


def wait(r, timeout=30):
    dl = time.time() + timeout
    st = r.state
    res = None
    while time.time() < dl:
        st = r.state
        if st in {"SUCCESS", "FAILURE", "REVOKED"}:
            try:
                res = r.result
            except Exception:
                res = None
            break
        time.sleep(0.5)
    return st, res


def db_summary(suffix=""):
    from sqlalchemy import create_engine, text

    url = _env("DATABASE_URL", "sqlite:///.data/app.db")
    eng = create_engine(url)
    out = {}
    with eng.connect() as conn:
        for t in ["tasks", "team_requests", "task_events", "audit_events", "repository_indexes"]:
            try:
                out[f"{t}{suffix}"] = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
            except Exception as e:
                out[f"{t}{suffix}"] = str(e)[:40]
    eng.dispose()
    return out


def main():
    from scripts.authorization_gate import require_authorization

    require_authorization(
        os.environ.get("AUTHORIZATION_OPERATION", ""),
        os.environ.get("AUTHORIZATION_LOG", "") or None,
    )
    import sys

    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    from app import main as m
    from app.db.session import create_schema
    from app.db import repositories
    from app.workers.celery_app import celery_app

    print("=== Celery 业务闭环 real smoke ===")
    ok, ver = redis_ping()
    if not ok:
        print(f"[FAIL] Redis 不可达：{ver}。请先启动 Redis（docker run -d -p 6379:6379 redis:7-alpine）。")
        raise SystemExit(2)
    print(f"[OK] Redis PONG（version={ver}）")

    try:
        workers = celery_app.control.inspect(timeout=3).ping() or {}
    except Exception as e:
        workers = {}
        print(f"[warn] inspect.ping 异常：{e}")
    online = [k for k, v in (workers or {}).items() if v]
    if not online:
        print("[FAIL] 未发现在线 Worker。请先启动 Worker（\\deploy\\dev-worker.ps1）再运行本脚本。")
        raise SystemExit(2)
    print(f"[OK] 在线 Worker：{online}")

    create_schema()
    now = _dt.datetime.now(_dt.timezone.utc)
    # 真实团队需求(DRAFT)
    req = m.TeamRequestRecord(
        id="REQ-SMK-1", request_no="REQ-SMK-1", project_id="proj_mvp", project_name="P",
        repository_id="repo_payment_service", repository_name="payment_service", type="bug",
        title="smoke 需求", body="正文足够长：用于真实同步链路触发的需求内容，足以通过内容校验。",
        acceptance_criteria=["c"], priority="high", labels=["bug"], target_branch="main",
        test_command="pytest -q", sync_status="DRAFT", content_sufficient=True, validation_warnings=[],
        created_by_id="user_developer", created_by_name="开发", created_at=now, updated_at=now,
    )
    repositories.upsert_team_request(req)
    # 真实任务(CREATED)
    task = m.TaskRecord.model_validate({
        "id": "TASK-SMK-1", "version": 1, "project_id": "proj_mvp", "project_name": "p",
        "repository_id": "repo_payment_service", "repository_name": "r",
        "repository_url": "https://gitee.com/acme/r", "issue_id": "#1", "title": "smoke任务", "status": "CREATED",
        "base_branch": "main", "test_command": "pytest -q", "work_branch": "w",
        "issue_snapshot": {"title": "t", "body": "足够长正文用于分析触发。", "comments": [], "labels": ["bug"],
                           "raw_response": {"id": 1}, "fetched_at": now.isoformat()},
        "automation_state": "running", "manual_required": False, "manual_reason": "none",
        "next_action": "check_index", "events": [], "execution_steps": [],
        "created_by": "user_developer", "created_by_name": "n", "created_at": now.isoformat(), "updated_at": now.isoformat(),
    })
    repositories.upsert_task(task)
    print("[seed] 已持久化真实 REQ-SMK-1 / TASK-SMK-1 到共享 DB")

    from app.workers.tasks import rebuild_repository_index, run_agent, sync_gitee_request

    log_file = Path(__file__).resolve().parents[2] / "logs" / "celery-worker.out.log"
    print(f"[log] worker 日志：{log_file}")

    for name, tr, kw in [
        ("rebuild_repository_index(idx_missing)", rebuild_repository_index, {"index_id": "idx_missing"}),
        ("sync_gitee_request(REQ-SMK-1)", sync_gitee_request, {"request_id": "REQ-SMK-1"}),
        ("run_agent(TASK-SMK-1)", run_agent, {"task_id": "TASK-SMK-1"}),
    ]:
        r = tr.delay(**kw)
        st, res = wait(r)
        print(f"[dispatch] {name} -> state={st} result={res}")

    print("[db] 处理后 DB：", db_summary())
    # 业务状态变化核验：REQ-SMK-1 应变为非 DRAFT（SYNCED/SYNCING/SYNC_FAILED）；TASK-SMK-1 应存在且非“缺失”
    m2 = _reload_entities()
    print("[verify] REQ-SMK-1 sync_status=", m2.get("REQ-SMK-1"))
    print("[verify] TASK-SMK-1 存在=", m2.get("TASK-SMK-1", "missing") != "missing")
    print("[SMOKE] 关键日志片段（worker）：")
    if log_file.exists():
        for line in log_file.read_text(errors="replace").splitlines():
            if any(k in line for k in ["Worker 消费", "TASK-SMK-1", "REQ-SMK-1", "received", "retry", "succeeded"]):
                print("  ", line[:160])


def _reload_entities():
    from app import main as m
    from app.db.bootstrap import restore_from_db

    restore_from_db()
    return {"REQ-SMK-1": m.TEAM_REQUESTS.get("REQ-SMK-1").sync_status if m.TEAM_REQUESTS.get("REQ-SMK-1") else "missing",
            "TASK-SMK-1": m.TASKS.get("TASK-SMK-1").status if m.TASKS.get("TASK-SMK-1") else "missing"}


if __name__ == "__main__":
    main()
