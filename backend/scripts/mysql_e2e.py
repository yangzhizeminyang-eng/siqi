"""MySQL 生产端到端复验：真实 MySQL 上，API 写入→MySQL 可查→重启恢复。

用法（working dir = backend，MySQL 需可达）：
    $env:DATABASE_URL="mysql+pymysql://user:pass@127.0.0.1:3306/agent_mvp?charset=utf8mb4"
    .\.venv\Scripts\python.exe -m scripts.mysql_e2e

注意：DATABASE_URL 含应用账号凭据，来自环境变量，脚本不打印密码。若库未建，先跑 scripts.init_mysql。
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, inspect, text


def _app_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith("mysql+pymysql://"):
        raise SystemExit("请先设置 DATABASE_URL=mysql+pymysql://...")
    return url


def db_counts() -> dict[str, int]:
    engine = create_engine(_app_url(), connect_args={"connect_timeout": 8})
    out: dict[str, int] = {}
    try:
        with engine.connect() as conn:
            for t in ["tasks", "task_events", "execution_steps", "audit_events", "repository_indexes"]:
                if t in inspect(engine).get_table_names():
                    out[t] = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
    finally:
        engine.dispose()
    return out


def main() -> None:
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

    from fastapi.testclient import TestClient
    from app import main as m
    from app.db.session import create_schema

    create_schema()

    def seams():
        HEAD = "c0ffee11deadbeef22c0ffee33deadbeef44556677"

        def fake_head(repository, base_branch=None):
            return HEAD

        def fake_snapshot(repository, issue_id):
            import datetime as dt

            return m.IssueSnapshotRecord(
                title="MySQL E2E 任务",
                body="验证：API 写入后 MySQL 可查、重启后可恢复。",
                comments=[],
                labels=["bug"],
                raw_response={"id": int(issue_id or 1)},
                fetched_at=dt.datetime.now(dt.timezone.utc),
            )

        m.fetch_gitee_branch_head = fake_head
        m.fetch_gitee_issue_snapshot = fake_snapshot

    seams()
    with TestClient(m.app) as client:
        login = client.post("/api/auth/login", json={"account": "developer@company.com", "password": "Dev123!"})
        assert login.status_code == 200
        cookies = login.cookies
        proj = m.PROJECTS.get(next(iter(m.PROJECTS)))
        repo = next((r for r in m.REPOSITORIES.values() if r.project_id == proj.id), None)
        r = client.post("/api/tasks", json={"project_id": proj.id, "repository_id": repo.id, "issue_id": "4202"}, cookies=cookies)
        assert r.status_code == 200, r.text
        task_id = r.json()["data"]["id"]
        print(f"[api] created task {task_id}")
        print("[mysql] 写进程 DB 各表行数：", db_counts())

    # 模拟重启：新进程 restore + API 读
    from app.db.bootstrap import restore_from_db
    counts = restore_from_db()
    assert task_id in m.TASKS, "重启后应从 MySQL 恢复任务"
    with TestClient(m.app) as client:
        login = client.post("/api/auth/login", json={"account": "admin@company.com", "password": "Admin123!"})
        got = client.get(f"/api/tasks/{task_id}", cookies=login.cookies)
        assert got.status_code == 200 and got.json()["data"]["id"] == task_id
    print("[mysql] 重启进程 restore counts：", counts)
    print("[mysql] 重启进程 DB 各表行数：", db_counts())
    print("[MYSQL-E2E]", "PASS: API 写入→MySQL 可查→重启恢复→API 读回" if task_id in m.TASKS else "FAIL")


if __name__ == "__main__":
    main()