"""读路径仓储层 + MySQL 生产形态测试。

读层读的是「请求内一致的缓存视图」，它由 DB 启动恢复并经写透持续落库，因此：
- 内存态下读层返回与 cache 一致的数据（既有 329 测试环境的语义）；
- 持久化开启 + restore 后，读层读到的正是 DB 恢复出来的数据。
"""

from __future__ import annotations

import datetime as _dt

import pytest

from app import main
from app.db import reads
from app.db import repositories
from app.db import session as db_session


def _make_task(task_id: str) -> main.TaskRecord:
    now = _dt.datetime.now(_dt.timezone.utc)
    return main.TaskRecord.model_validate(
        {
            "id": task_id,
            "version": 1,
            "project_id": "proj_mvp",
            "project_name": "p",
            "repository_id": "repo_payment_service",
            "repository_name": "r",
            "repository_url": "https://gitee.com/acme/r",
            "issue_id": "#1",
            "title": "读层任务",
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
    )


def test_reads_serve_created_task():
    """读层 get_task/list_tasks 返回与 cache 一致的任务。"""
    task = _make_task("TASK-reads-1")
    main.TASKS[task.id] = task
    assert reads.get_task("TASK-reads-1") is task
    assert any(t.id == "TASK-reads-1" for t in reads.list_tasks())


def test_reads_audit_events_filter():
    from app import main as m

    m.AUDIT_EVENTS.clear()
    now = _dt.datetime.now(_dt.timezone.utc)
    ev = m.AuditEventRecord(
        id="ev_r",
        event_type="TASK_CREATED",
        resource_type="task",
        resource_id="TASK-reads-1",
        actor_id="u",
        actor_name="n",
        summary="s",
        diff_summary=[],
        created_at=now,
        execution_step_ids=[],
    )
    m.AUDIT_EVENTS[ev.id] = ev
    assert [e.id for e in reads.list_audit_events(resource_type="task", resource_id="TASK-reads-1")] == ["ev_r"]


def test_reads_repositories_filter(monkeypatch):
    repo = main.REPOSITORIES["repo_payment_service"]
    assert all(r.id == repo.id for r in reads.list_repositories(repo.project_id))


def test_reads_users_and_projects():
    assert reads.list_users(), "读层应能列数用户"
    assert any(u.id for u in reads.list_users())
    assert reads.list_projects(), "读层应能列数项目"
    assert reads.get_project(list(reads.list_projects())[0].id) is not None


def test_reload_from_db_restores_read_view(tmp_path, monkeypatch):
    """路径 B 契约：dict 是运行时读视图、DB 是持久/重启真源。

    清空运行时视图（dict）→ API/读层返回 0（这是读视图语义，非漏数据）；
    经 reload_from_db()（= 启动/重启恢复）把 DB 重灌回视图后，读层即可读到 DB 数据。"""
    from app.db import repositories, session as db_session

    stores = ["USERS", "PASSWORD_RESET_REQUESTS", "PROJECTS", "REPOSITORIES", "TASKS",
              "TEAM_REQUESTS", "AUDIT_EVENTS", "REPOSITORY_INDEXES", "LATEST_INDEX_BY_REPO",
              "CODE_INDEX_SNAPSHOTS", "CODE_CHUNKS", "RETRIEVAL_RECORDS"]
    snap = {n: dict(getattr(main, n)) if isinstance(getattr(main, n, None), dict) else getattr(main, n, None) for n in stores}
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'reads.db').as_posix()}")
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    db_session.create_schema()
    try:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        repositories.upsert_audit_event(main.AuditEventRecord(
            id="ev_b", event_type="X", resource_type="task", resource_id="t1",
            actor_id="u", actor_name="n", summary="s", diff_summary=[], created_at=now, execution_step_ids=[],
        ))
        main.AUDIT_EVENTS.clear()  # 清空运行时读视图
        got_cleared = [e for e in reads.list_audit_events() if e.id == "ev_b"]
        assert got_cleared and got_cleared[0].event_type == "X", \
            "持久化开启时读主源=DB：清空内存读视图后，读层仍应从 DB 真源读到 ev_b（读源唯一）"
        reads.reload_from_db()  # 启动/重启恢复：DB 真源 → 重灌视图
        got = [e for e in reads.list_audit_events() if e.id == "ev_b"]
        assert got and got[0].event_type == "X", "reload_from_db 后读层应从 DB 恢复数据"
    finally:
        for n in stores:
            s = getattr(main, n, None)
            if isinstance(s, dict):
                s.clear()
                s.update(snap.get(n, {}))
        db_session.get_engine.cache_clear()
        db_session.get_session_factory.cache_clear()


def test_reads_indexes_and_retrieval():
    now = _dt.datetime.now(_dt.timezone.utc)
    idx = main.RepositoryIndexRecord(
        id="idx_r", repository_id="repo_payment_service", project_id="proj_mvp", base_branch="main",
        commit_sha="a", status="COMPLETED", requested_by="u", requested_by_name="n",
        requested_at=now, created_at=now, updated_at=now,
    )
    main.REPOSITORY_INDEXES[idx.id] = idx
    rec = main.RetrievalRecord(
        id="rtr", task_id="TASK-reads-1", query_text="q", repository_id="repo_payment_service",
        project_id="proj_mvp", top_k=3, mode="auto", threshold=0.3, result_chunk_ids=[],
        created_at=now,
    )
    main.RETRIEVAL_RECORDS[rec.id] = rec
    assert reads.latest_completed_index("repo_payment_service").id == "idx_r"
    assert [r.id for r in reads.list_retrieval_records("TASK-reads-1")] == ["rtr"]


@pytest.fixture()
def _persist_db(tmp_path, monkeypatch):
    db_file = tmp_path / "reads.db"
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    db_session.create_schema()
    yield db_file
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()


def test_reads_serve_db_restored_data(_persist_db):
    """持久化 + 清内存 + restore 后，读层读到的正是 DB 恢复数据（读源已 DB 化）。"""
    from app.db.bootstrap import restore_from_db

    task = _make_task("TASK-reads-db-1")
    repositories.upsert_task(task)
    main.TASKS.clear()
    counts = restore_from_db()
    assert counts.get("tasks", 0) == 1
    assert reads.get_task("TASK-reads-db-1") is not None
    assert reads.get_task("TASK-reads-db-1").id == "TASK-reads-db-1"


# ---------- MySQL 生产形态 ----------

def test_mysql_url_build_and_detect():
    from app.core import mysql_config as mc

    url = mc.build_mysql_url(host="db.internal", user="app", password="pw", database="agent")
    assert url.startswith("mysql+pymysql://")
    assert mc.is_mysql_url(url) is True
    assert mc.is_mysql_url("sqlite:///x.db") is False


def test_mysql_ready_depends_on_url_and_pymysql(monkeypatch):
    from app.core import mysql_config as mc

    ok, reason = mc.mysql_ready("sqlite:///x.db")
    assert ok is False and "MySQL" in reason
    ok, reason = mc.mysql_ready("mysql+pymysql://u:p@h:3306/d?charset=utf8mb4")
    # pymysql 已声明依赖；断言形态就绪（真实服务连接由 MYSQL_LIVE 冒烟覆盖）
    assert reason.startswith("MySQL(pymysql)")


def test_core_reads_are_db_backed_after_dict_clear(tmp_path, monkeypatch):
    """路径 A 验收：持久化开启时，清空核心业务 dict（TASKS/PROJECTS/TEAM_REQUESTS）后，
    GET /api/tasks、/api/projects、/api/team/requests 仍从 DB 正常读出数据（dict 不再主读源）。"""
    from fastapi.testclient import TestClient

    stores = ["USERS", "PROJECTS", "REPOSITORIES", "TASKS", "TEAM_REQUESTS", "AUDIT_EVENTS",
              "REPOSITORY_INDEXES", "LATEST_INDEX_BY_REPO", "CODE_INDEX_SNAPSHOTS", "CODE_CHUNKS", "RETRIEVAL_RECORDS"]
    snap = {n: dict(main.__dict__[n]) if isinstance(main.__dict__.get(n), dict) else main.__dict__.get(n) for n in stores}
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'db.db').as_posix()}")
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    db_session.create_schema()
    try:
        now = _dt.datetime.now(_dt.timezone.utc)
        repositories.upsert_user(main.UserRecord(id="user_admin", name="A", email="admin@company.com",
                                                 role="admin", password_hash=main.hash_password("Admin123!")))
        repositories.upsert_project(main.ProjectRecord(id="proj_accept", name="P", description="", created_at=now, updated_at=now))
        repositories.upsert_team_request(main.TeamRequestRecord(
            id="REQ-A", request_no="REQ-A", project_id="proj_accept", project_name="P",
            repository_id="repo_payment_service", repository_name="R", type="bug",
            title="req", body="body long enough for content validation.", acceptance_criteria=[],
            sync_status="DRAFT", content_sufficient=True, validation_warnings=[],
            created_by_id="user_admin", created_by_name="A", created_at=now, updated_at=now,
        ))
        repositories.upsert_task(_make_task("TASK-A"))
        # 清空核心业务 dict（保留 USERS 供认证）
        main.TASKS.clear(); main.PROJECTS.clear(); main.REPOSITORIES.clear(); main.TEAM_REQUESTS.clear()
        main.REPOSITORY_INDEXES.clear(); main.AUDIT_EVENTS.clear()
        client = TestClient(main.app)
        login = client.post("/api/auth/login", json={"account": "admin@company.com", "password": "Admin123!"})
        assert login.status_code == 200, login.text
        tasks = client.get("/api/tasks")
        projects = client.get("/api/projects")
        reqs = client.get("/api/team/requests")
        assert tasks.status_code == 200 and len(tasks.json()["data"]) == 1, f"dict 清空后 /api/tasks 应从 DB 读到1：{tasks.text}"
        assert projects.status_code == 200 and len(projects.json()["data"]) == 1, "/api/projects 应从 DB 读到1"
        assert reqs.status_code == 200 and len(reqs.json()["data"]) == 1, "/api/team/requests 应从 DB 读到1"
    finally:
        for n in stores:
            s = main.__dict__.get(n)
            if isinstance(s, dict):
                s.clear()
                s.update(snap.get(n, {}))
        db_session.get_engine.cache_clear()
        db_session.get_session_factory.cache_clear()


def test_reads_audit_index_retrieval_chunks_db_backed_after_clear(tmp_path, monkeypatch):
    """读源唯一（DB 为真源）：持久化开启、清空内存 dict 后，reads 的
    list_audit_events / list_repository_indexes / get_index / list_retrieval_records / code_chunks_of_index
    仍从 DB 读到数据（dict 非主读源）。"""
    from app.db import repositories, session as db_session

    stores = ["USERS", "PROJECTS", "REPOSITORIES", "TASKS", "TEAM_REQUESTS", "AUDIT_EVENTS",
              "REPOSITORY_INDEXES", "LATEST_INDEX_BY_REPO", "CODE_CHUNKS", "RETRIEVAL_RECORDS"]
    snap = {n: dict(main.__dict__[n]) if isinstance(main.__dict__.get(n), dict) else main.__dict__.get(n) for n in stores}
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'reads.db').as_posix()}")
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    db_session.create_schema()
    try:
        now = _dt.datetime.now(_dt.timezone.utc)
        repositories.upsert_audit_event(main.AuditEventRecord(
            id="ev_d", event_type="Y", resource_type="task", resource_id="t1",
            actor_id="u", actor_name="n", summary="s", diff_summary=[], created_at=now, execution_step_ids=[]))
        repositories.upsert_repository_index(main.RepositoryIndexRecord(
            id="idx_d", repository_id="repo_payment_service", project_id="proj_mvp", base_branch="main",
            commit_sha="c", status="COMPLETED", total_files=2, processed_files=2, chunk_count=1,
            milvus_collection="code_idx_d", requested_by="u", requested_by_name="n",
            requested_at=now, created_at=now, updated_at=now, started_at=now, finished_at=now,
            error_message=None, error_stage=None))
        repositories.upsert_retrieval_record(main.RetrievalRecord(
            id="ret_d", task_id="TASK-A", query_text="q", repository_id="repo_payment_service",
            project_id="proj_mvp", index_id="idx_d", top_k=5, mode="auto", fresh=True, threshold=0.0,
            result_chunk_ids=["c_d"], created_at=now))
        repositories.upsert_code_chunks([main.CodeChunkRecord(
            chunk_id="c_d", repository_id="repo_payment_service", index_id="idx_d", file_path="a.py",
            chunk_index=0, content="x", language="python", symbol_name="f", start_line=1, end_line=2,
            commit_sha="c", milvus_collection="code_idx_d", milvus_pk="c_d", checksum="ch")])
        # 清空内存读视图
        main.AUDIT_EVENTS.clear(); main.REPOSITORY_INDEXES.clear(); main.RETRIEVAL_RECORDS.clear(); main.CODE_CHUNKS.clear()
        assert [e.id for e in reads.list_audit_events() if e.id == "ev_d"], "audit 应 DB-backed"
        idx = reads.get_index("idx_d")
        assert idx is not None and idx.status == "COMPLETED", "get_index 应 DB-backed"
        assert [r.id for r in reads.list_repository_indexes("repo_payment_service")] == ["idx_d"], "index list DB-backed"
        assert reads.latest_completed_index("repo_payment_service") is not None, "latest_completed_index DB-backed"
        assert [r.id for r in reads.list_retrieval_records("TASK-A")] == ["ret_d"], "retrieval DB-backed"
        assert [c.chunk_id for c in reads.code_chunks_of_index("idx_d")] == ["c_d"], "chunks DB-backed"
    finally:
        for n in stores:
            s = main.__dict__.get(n)
            if isinstance(s, dict):
                s.clear()
                s.update(snap.get(n, {}))
        db_session.get_engine.cache_clear()
        db_session.get_session_factory.cache_clear()
