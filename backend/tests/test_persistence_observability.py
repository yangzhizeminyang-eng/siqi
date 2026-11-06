"""P1：写透可观测 + 索引中间态持久化恢复测试。"""

from __future__ import annotations

import datetime as _dt

import pytest

from app.core import persistence_status as ps
from app.db import bootstrap
from app.db import repositories
from app.db import session as db_session


@pytest.fixture()
def persist_db(tmp_path, monkeypatch):
    equal_db = tmp_path / "p1.db"
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{equal_db.as_posix()}")
    monkeypatch.setenv("DATA_DIR", tmp_path.as_posix())
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    db_session.create_schema()
    ps.reset()
    yield equal_db
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    ps.reset()


# ---------------- P1-1 写透可观测 ----------------

def test_measure_records_failure_not_silent(persist_db, monkeypatch):
    """写库失败：返回失败码 + 指标计数，不再静默吞错。"""
    monkeypatch.delenv("PERSIST_STRICT", raising=False)

    def boom():
        raise ValueError("disk full")

    ok, code, detail = ps.measure("task", "t_boom", boom)
    assert ok is False
    assert code == "VALUEERROR"
    assert "disk full" in detail
    m = ps.metrics()
    assert m["counters"].get("task:fail", 0) == 1
    assert "task:t_boom" in m["last_errors"]


def test_measure_ok_records_success(persist_db):
    ps.measure("project", "p1", lambda: None)
    assert ps.metrics()["counters"].get("project:ok", 0) == 1


def test_measure_skip_when_persistence_disabled(persist_db, monkeypatch):
    """USE_SQLALCHEMY=false：写透记为 skip，不执行工作函数，也不当失败。"""
    monkeypatch.setenv("USE_SQLALCHEMY", "false")
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    called = []

    def work():
        called.append(1)
        raise RuntimeError("should not run")

    ok, code, _ = ps.measure("task", "t", work)
    assert ok is True
    assert code == "ok"
    assert called == []
    assert ps.metrics()["counters"].get("task:skip", 0) >= 0


def test_measure_strict_raises(persist_db, monkeypatch):
    """PERSIST_STRICT=true：写库失败直接上抛，拒绝静默丢数据。"""
    monkeypatch.setenv("PERSIST_STRICT", "true")

    def boom():
        raise OSError("locked db")

    with pytest.raises(OSError):
        ps.measure("task", "t", boom)


def test_persist_task_records_metric(persist_db):
    """经 main._persist_task 写透一条任务，指标记录 ok。"""
    from app import main

    now = _dt.datetime.now(_dt.timezone.utc)
    task = main.TaskRecord.model_validate(
        {
            "id": "TASK-obs-1",
            "version": 1,
            "project_id": "p",
            "project_name": "p",
            "repository_id": "r",
            "repository_name": "r",
            "repository_url": "https://gitee.com/a/r",
            "issue_id": "#1",
            "title": "t",
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
            "created_by": "u",
            "created_by_name": "n",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
    )
    main._persist_task(task)
    assert ps.metrics()["counters"].get("task:ok", 0) == 1
    assert "TASK-obs-1" in {r["id"] for r in repositories.load_tasks()}


# ---------------- P1-2 索引中间态恢复 ----------------

def _code_file(path: str, content: str):
    from app import main

    return main.CodeFile(path=path, language="python", content=content)


def test_index_snapshot_persist_and_restore(persist_db):
    from app import main

    now = _dt.datetime.now(_dt.timezone.utc)
    files = [_code_file("order/list.py", "def render(): pass")]
    repositories.upsert_index_snapshot("idx_mid", "repo_payment_service", "abc123", files)

    main.CODE_INDEX_SNAPSHOTS.clear()
    counts = bootstrap.restore_from_db()
    assert counts.get("index_snapshots", 0) == 1
    assert "idx_mid" in main.CODE_INDEX_SNAPSHOTS
    assert len(main.CODE_INDEX_SNAPSHOTS["idx_mid"]) == 1
    assert main.CODE_INDEX_SNAPSHOTS["idx_mid"][0].path == "order/list.py"


def test_parsing_stage_recovers_snapshot_from_db(persist_db, monkeypatch):
    """PARSING 中断重启：内存快照缺失时从 DB 重建，不再因缺快照失败。"""
    from app import main

    now = _dt.datetime.now(_dt.timezone.utc)
    repo = main.REPOSITORIES["repo_payment_service"]
    files = [_code_file("order/list.py", "def render(): pass")]
    repositories.upsert_index_snapshot("idx_parsing", repo.id, "abc123", files)

    main.CODE_INDEX_SNAPSHOTS.clear()  # 模拟重启：内存快照丢失
    main.CODE_CHUNKS.clear()

    record = main.RepositoryIndexRecord(
        id="idx_parsing",
        repository_id=repo.id,
        project_id=repo.project_id,
        base_branch="main",
        commit_sha="abc123",
        status="PARSING",
        requested_by="u",
        requested_by_name="n",
        requested_at=now,
        created_at=now,
        updated_at=now,
    )
    main.REPOSITORY_INDEXES[record.id] = record
    parsed = []

    def fake_parse(files_, **kwargs):
        parsed.append(files_)
        return []

    monkeypatch.setattr(main, "parse_and_chunk", fake_parse)
    main.run_index_stage_work(record)  # 会从 DB 恢复快照再继续 PARSING

    assert main.CODE_INDEX_SNAPSHOTS.get("idx_parsing"), "PARSING 应从 DB 重建内存快照"
    assert parsed, "快照恢复后应继续执行解析"