"""MySQL 生产接入集成测试。

这些用例需要环境变量 MYSQL_TEST_URL 指向一个 MySQL(pymysql) 库（可新建、可建表）：
    $env:MYSQL_TEST_URL="mysql+pymysql://user:pass@127.0.0.1:3306/agent_mvp_test?charset=utf8mb4"
未设置时全部跳过（本地无 MySQL 的开发/CI 依旧全绿）。库不存在先跑 scripts.init_mysql 或用有 CREATE DATABASE 权限账号创建。
测试不打印任何密码/连接串细节。
"""

from __future__ import annotations

import datetime as _dt
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("MYSQL_TEST_URL", "").startswith("mysql+pymysql://"),
    reason="MYSQL_TEST_URL 未配置为 MySQL(pymysql)，跳过 MySQL 集成测试",
)


@pytest.fixture()
def mysql_engine():
    from sqlalchemy import create_engine

    url = os.environ["MYSQL_TEST_URL"]
    engine = create_engine(url, connect_args={"connect_timeout": 8})
    try:
        yield engine
    finally:
        engine.dispose()


def test_mysql_reachable(mysql_engine):
    from sqlalchemy import text

    with mysql_engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1


def test_team_production_requires_mysql_url(monkeypatch):
    """team / production 不允许默认/回退 sqlite：缺 DATABASE_URL 或给了 sqlite 都应报错。"""
    from app.core import runtime_config

    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("APP_ENV", "team")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        runtime_config.load_runtime_settings()
    monkeypatch.setenv("DATABASE_URL", "sqlite:///.data/x.db")
    with pytest.raises(RuntimeError):
        runtime_config.load_runtime_settings()
    # 本地/local 允许 sqlite
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    s = runtime_config.load_runtime_settings()
    assert s.database_url.startswith("sqlite")


def test_mysql_schema_created(mysql_engine):
    from sqlalchemy import inspect

    names = set(inspect(mysql_engine).get_table_names())
    for t in ["users", "projects", "repositories", "tasks", "plans", "diffs", "test_runs",
              "pr_descriptions", "task_events", "execution_steps", "audit_events",
              "team_requests", "repository_indexes", "code_chunks", "retrieval_records", "index_snapshots"]:
        assert t in names, f"MySQL 缺少表 {t}"


@pytest.fixture()
def mysql_app_env(monkeypatch, tmp_path):
    """把应用引擎指向 MYSQL_TEST_URL 并建表；结束复位，避免污染其它用例。"""
    from app import main
    from app.db import session as db_session
    from app.workers.dispatch import logger

    stores = ["USERS", "PROJECTS", "REPOSITORIES", "TASKS", "TEAM_REQUESTS", "AUDIT_EVENTS",
              "REPOSITORY_INDEXES", "CODE_CHUNKS", "RETRIEVAL_RECORDS", "CODE_INDEX_SNAPSHOTS"]
    snap = {name: dict(getattr(main, name)) for name in stores if hasattr(main, name)}
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", os.environ["MYSQL_TEST_URL"])
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    db_session.create_schema()
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    yield
    for name in stores:
        store = getattr(main, name, None)
        if isinstance(store, dict):
            store.clear()
            store.update(snap.get(name, {}))
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()


def _task_dict(task_id: str) -> dict:
    now = _dt.datetime.now(_dt.timezone.utc)
    return {
        "id": task_id,
        "version": 1,
        "project_id": "proj_mvp",
        "project_name": "p",
        "repository_id": "repo_payment_service",
        "repository_name": "r",
        "repository_url": "https://gitee.com/acme/r",
        "issue_id": "#1",
        "title": "MySQL 任务",
        "status": "CREATED",
        "base_branch": "main",
        "test_command": "pytest -q",
        "work_branch": "w",
        "issue_snapshot": {"title": "t", "body": "b", "comments": [], "labels": [], "raw_response": {}, "fetched_at": now.isoformat()},
        "automation_state": "running",
        "manual_required": False,
        "manual_reason": "none",
        "next_action": "check_index",
        "events": [],
        "execution_steps": [],
        "created_by": "user_developer",
        "created_by_name": "n",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


def test_api_write_then_mysql_queryable(mysql_app_env, mysql_engine):
    """真实 API 建任务后，MySQL 里能查到业务数据（tasks/task_events/audit_events）。"""
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app import main as m

    m.fetch_gitee_branch_head = lambda r, base_branch=None: "c0ffee11deadbeef22c0ffee33deadbeef44556677"
    m.fetch_gitee_issue_snapshot = lambda r, issue_id: m.IssueSnapshotRecord(
        title="t", body="b", comments=[], labels=["bug"], raw_response={"id": int(issue_id)}, fetched_at=_dt.datetime.now(_dt.timezone.utc)
    )
    with TestClient(m.app) as client:
        login = client.post("/api/auth/login", json={"account": "developer@company.com", "password": "Dev123!"})
        assert login.status_code == 200
        proj = m.PROJECTS.get(next(iter(m.PROJECTS)))
        repo = next((r for r in m.REPOSITORIES.values() if r.project_id == proj.id), None)
        # 唯一 issue_id，避免在持久 MySQL 测试库上重复触发 409
        unique_issue = str(int(_dt.datetime.now().timestamp() * 1000))
        resp = client.post("/api/tasks", json={"project_id": proj.id, "repository_id": repo.id, "issue_id": unique_issue})
        assert resp.status_code == 200, resp.text
    with mysql_engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM task_events")).scalar() >= 1
        assert conn.execute(text("SELECT COUNT(*) FROM audit_events")).scalar() >= 1


def test_mysql_restart_recovery(mysql_app_env, mysql_engine):
    """写入 MySQL → 清内存 → restore 恢复，读源 DB 化。"""
    from app import main
    from app.db import bootstrap, repositories

    repositories.upsert_task(main.TaskRecord.model_validate(_task_dict("TASK-mysql-restart-1")))
    main.TASKS.clear()
    counts = bootstrap.restore_from_db()
    assert counts.get("tasks", 0) >= 1
    assert main.TASKS["TASK-mysql-restart-1"].id == "TASK-mysql-restart-1"


def test_mysql_write_failure_is_not_silent(mysql_app_env, monkeypatch, mysql_engine):
    """写库失败时记录指标 + 错误，不静默吞错（PERSIST_STRICT=false 下返回失败标记）。"""
    from app import main
    from app.core import persistence_status as ps
    from app.db import session as db_session

    ps.reset()
    # 指向一个不存在的库：写透应失败并记录
    monkeypatch.setenv("DATABASE_URL", os.environ["MYSQL_TEST_URL"].rsplit("/", 1)[0] + "/no_such_db?charset=utf8mb4")
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    task = main.TaskRecord.model_validate(_task_dict("TASK-mysql-fail-1"))
    main._persist_task(task)  # 失败不回抛但记录
    m = ps.metrics()
    assert m["counters"].get("task:fail", 0) >= 1, "写 MySQL 失败必须计入指标"
    assert m["last_errors"], "写 MySQL 失败必须记录 last_errors"
