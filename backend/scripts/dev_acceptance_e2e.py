"""P0 验收 E2E：真实 API 新建任务 → DB 落库 → 重启(新进程) → API 仍能读到。

验证「API 不再只依赖内存 dict」，并打印每个业务 store 在 DB 的实际行数/实体名。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "dev_acceptance_e2e.db"


def _cfg():
    os.environ["USE_SQLALCHEMY"] = "true"
    os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH.as_posix()}"
    os.environ["MODEL_MOCK_FALLBACK"] = "true"


def dump_db_counts() -> None:
    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(os.environ["DATABASE_URL"])
    insp = inspect(engine)
    with engine.connect() as conn:
        tables = insp.get_table_names()
        for t in ["users", "projects", "repositories", "tasks", "plans", "diffs",
                  "test_runs", "pr_descriptions", "task_events", "execution_steps",
                  "audit_events", "code_chunks", "repository_indexes", "retrieval_records"]:
            if t in tables:
                n = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
                print(f"[db] {t:<22} rows={n}")


def main() -> None:
    _cfg()
    mode = sys.argv[1] if len(sys.argv) > 1 else "write"

    from fastapi.testclient import TestClient
    from app import main as m
    from app.db.session import create_schema

    create_schema()

    with TestClient(m.app) as client:
        if mode == "write":
            # 种子仓库指向假 gitee URL：mock 拉取（与 314 测试一致），隔离验证持久化主链路
            HEAD = "c0ffee11deadbeef22c0ffee33deadbeef44556677"

            def fake_head(repository, base_branch=None):
                return HEAD

            def fake_snapshot(repository, issue_id):
                return m.IssueSnapshotRecord(
                    title="P0 验收任务",
                    body="验收业务数据持久化：需要落库并能在重启后经真实 API 读到。",
                    comments=[],
                    labels=["bug"],
                    raw_response={"id": int(issue_id.strip("#") or 1)},
                    fetched_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
                )

            m.fetch_gitee_branch_head = fake_head
            m.fetch_gitee_issue_snapshot = fake_snapshot

            login = client.post("/api/auth/login",
                                json={"account": "developer@company.com", "password": "Dev123!"})
            assert login.status_code == 200, login.text
            cookies = login.cookies
            proj = m.PROJECTS.get(next(iter(m.PROJECTS)))
            repo = next((r for r in m.REPOSITORIES.values() if r.project_id == proj.id), None)
            assert proj and repo, "需要种子项目+仓库"
            # 通过真实 API 创建任务
            r = client.post("/api/tasks",
                            json={"project_id": proj.id, "repository_id": repo.id, "issue_id": "4201"},
                            cookies=cookies)
            print("[api] POST /api/tasks ->", r.status_code)
            assert r.status_code == 200, r.text
            task = r.json()["data"]
            task_id = task["id"]
            print(f"[api] created task {task_id} status={task['status']}")
            # 触发一次 poll 让 API 写入/推进（persist 在 handler 内已写透）
            listr = client.get("/api/tasks", cookies=cookies)
            print("[api] GET /api/tasks ->", listr.status_code, "count=", len(listr.json()["data"]))
            open(DB_PATH.parent / "_accepted_task_id.txt", "w").write(task_id)
            print("=== DB down-level (write process) ===")
            dump_db_counts()
            return task_id

        # read（模拟重启：新进程）
        from app.db.bootstrap import restore_from_db
        counts = restore_from_db()
        print("[restart] restore_from_db counts=", counts)
        task_id = open(DB_PATH.parent / "_accepted_task_id.txt").read().strip()
        assert task_id in m.TASKS, "重启后任务应仍从 DB 恢复"
        t = m.TASKS[task_id]
        print(f"[restart] task {task_id} recovered status={t.status} title={t.title!r}")
        with TestClient(m.app) as client:
            login = client.post("/api/auth/login",
                                json={"account": "admin@company.com", "password": "Admin123!"})
            assert login.status_code == 200, login.text
            got = client.get(f"/api/tasks/{task_id}", cookies=login.cookies)
            print("[api] GET /api/tasks/{id} ->", got.status_code, "id=", got.json()["data"]["id"])
            assert got.status_code == 200 and got.json()["data"]["id"] == task_id
        print("=== DB down-level (read/restart process) ===")
        dump_db_counts()
        print("[P0-ACCEPTANCE] ", "PASS：API 重启后可读到原任务（读自 DB 恢复）" if task_id in m.TASKS else "FAIL")


if __name__ == "__main__":
    main()