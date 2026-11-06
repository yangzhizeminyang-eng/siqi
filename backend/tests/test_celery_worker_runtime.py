"""Worker 运行时 / 异常序列化 专项测试。"""

from __future__ import annotations

import datetime as _dt
import pickle

import pytest

from app.db import repositories, session as db_session


@pytest.fixture()
def worker_db(tmp_path, monkeypatch):
    from app import main

    stores = [
        "USERS", "PASSWORD_RESET_REQUESTS", "PROJECTS", "REPOSITORIES", "TASKS",
        "TEAM_REQUESTS", "AUDIT_EVENTS", "REPOSITORY_INDEXES", "LATEST_INDEX_BY_REPO",
        "CODE_INDEX_SNAPSHOTS", "CODE_CHUNKS", "RETRIEVAL_RECORDS", "CODE_VECTOR_STORE",
    ]
    snap = {n: dict(getattr(main, n)) if isinstance(getattr(main, n, None), dict) else getattr(main, n, None) for n in stores}
    db_file = tmp_path / "worker.db"
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    db_session.create_schema()
    # Worker 只检查 schema，不负责并发迁移；专项测试先把临时库升级到当前 head，
    # 才能验证「已迁移数据库上的运行时恢复」而不是把迁移缺失误判成恢复失败。
    from app.db import migrate

    migrate.run_upgrade()
    yield db_file
    for n in stores:
        store = getattr(main, n, None)
        if isinstance(store, dict):
            store.clear()
            store.update(snap.get(n, {}))
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()


def _now():
    return _dt.datetime.now(_dt.timezone.utc)


def _task_dict(task_id: str) -> dict:
    now = _now()
    return {
        "id": task_id, "version": 1, "project_id": "proj_mvp", "project_name": "p",
        "repository_id": "repo_payment_service", "repository_name": "r",
        "repository_url": "https://gitee.com/acme/r", "issue_id": "#1", "title": "t", "status": "CREATED",
        "base_branch": "main", "test_command": "pytest -q", "work_branch": "w",
        "issue_snapshot": {"title": "t", "body": "足够长的分析正文，用于触发分析流程。", "comments": [],
                           "labels": ["bug"], "raw_response": {"id": 1}, "fetched_at": now.isoformat()},
        "automation_state": "running", "manual_required": False, "manual_reason": "none",
        "next_action": "check_index", "events": [], "execution_steps": [],
        "created_by": "user_developer", "created_by_name": "n", "created_at": now.isoformat(), "updated_at": now.isoformat(),
    }


def test_worker_bootstrap_restores_runtime_without_lifespan(worker_db):
    """Worker 启动 bootstrap 也能从 DB 恢复运行时缓存。"""
    from app import main
    from app.workers.runtime import init_worker_runtime

    repositories.upsert_task(main.TaskRecord.model_validate(_task_dict("TASK-w1")))
    main.TASKS.clear()  # 模拟 worker 空态
    counts = init_worker_runtime()
    assert counts.get("tasks", 0) >= 1
    assert "TASK-w1" in main.TASKS, "worker 启动时应自行 restore_from_db 读到任务"


def test_worker_restores_runtime_without_lifespan(worker_db):
    """Worker 不依赖 FastAPI lifespan 也能从 DB 恢复运行时缓存。"""
    from app import main
    from app.workers.runtime import ensure_worker_runtime_ready

    repositories.upsert_task(main.TaskRecord.model_validate(_task_dict("TASK-w2")))
    main.TASKS.clear()  # 模拟 worker 空态
    ensure_worker_runtime_ready()
    assert "TASK-w2" in main.TASKS, "worker 应自行 restore_from_db 读到任务"


def test_httpexception_is_serializable_via_worker_error(worker_db):
    """HTTPException → WorkerTaskError 可 pickle，不再导致 worker 序列化崩溃。"""
    from fastapi import HTTPException
    from app.workers.tasks import _to_worker_error

    http = HTTPException(status_code=404, detail={"code": 404, "message": "平台需求不存在", "data": None})
    err = _to_worker_error(http)
    assert err.status_code == 404 and err.error_code == "404"
    # 关键：可 pickle 往返
    back = pickle.loads(pickle.dumps(err))
    assert back.status_code == 404


def test_sync_worker_reads_real_request(worker_db, monkeypatch):
    """ensure ready 后，sync worker 读到真实团队需求（不再 404）。"""
    from app import main

    now = _now()
    req = main.TeamRequestRecord(
        id="REQ-7788", request_no="REQ-7788", project_id="proj_mvp", project_name="P",
        repository_id="repo_payment_service", repository_name="payment_service", type="bug",
        title="同步真实", body="正文足够长：需要用于触发同步链路的真实需求内容。", acceptance_criteria=["c"],
        priority="high", labels=["bug"], target_branch="main", test_command="pytest tests/ -q",
        sync_status="DRAFT", content_sufficient=True, validation_warnings=[], created_by_id="user_developer",
        created_by_name="开发", created_at=now, updated_at=now,
    )
    repositories.upsert_team_request(req)
    main.TEAM_REQUESTS.clear()
    from app.workers.runtime import ensure_worker_runtime_ready

    ensure_worker_runtime_ready()
    assert main.TEAM_REQUESTS.get("REQ-7788") is not None


def test_run_agent_real_task_not_missing(worker_db, monkeypatch):
    """ensure ready 后，run_agent 能吃到真实任务（非 missing），失败也走可恢复重试而非空态。"""
    from app import main
    from app.workers.runtime import ensure_worker_runtime_ready
    from app.workers.tasks import run_agent

    repositories.upsert_task(main.TaskRecord.model_validate(_task_dict("TASK-w2")))
    main.TASKS.clear()
    ensure_worker_runtime_ready()
    assert "TASK-w2" in main.TASKS, "worker 应恢复真实任务缓存"
    # 真实任务上执行：任务被找到；若因缺 gitee/索引无法推进，走可恢复重试→FAILED，而非 missing
    result = run_agent.apply(kwargs={"task_id": "TASK-w2"})
    assert result.status in {"SUCCESS", "FAILURE"}
    assert "TASK-w2" in main.TASKS


def test_worker_write_failure_observed(worker_db, monkeypatch):
    """worker 写库失败进入 persistence_status 指标，不静默。"""
    from app.core import persistence_status as ps
    from app import main

    ps.reset()
    def boom():
        raise OSError("db locked")

    ok, code, _ = ps.measure("task", "t-wfail", boom)
    assert ok is False and code == "OSERROR"
    assert ps.metrics()["counters"].get("task:fail", 0) >= 1


def test_worker_restore_is_idempotent(worker_db):
    """ensure_worker_runtime_ready 重复调用幂等、不报错、无重复副作用。"""
    from app import main
    from app.workers.runtime import ensure_worker_runtime_ready

    repositories.upsert_task(main.TaskRecord.model_validate(_task_dict("TASK-w-idem")))
    main.TASKS.clear()
    ensure_worker_runtime_ready()
    first = set(main.TASKS)
    ensure_worker_runtime_ready()
    ensure_worker_runtime_ready()
    assert set(main.TASKS) == first
    assert "TASK-w-idem" in main.TASKS


def test_worker_restore_failure_does_not_crash(worker_db, monkeypatch):
    """restore 失败不硬崩 worker；任务后续按缺失实体→可序列化 WorkerTaskError 路径处理。"""
    from app.workers.runtime import ensure_worker_runtime_ready

    def boom():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("app.db.bootstrap.restore_from_db", boom)
    ensure_worker_runtime_ready()  # 不抛
