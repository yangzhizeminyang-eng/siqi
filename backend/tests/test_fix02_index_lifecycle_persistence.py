"""FIX-02 索引生命周期持久化、跨进程可见性与迁移兼容。"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.enterprise_rag import index_lifecycle

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def _record(index_id: str = "idx_fix02", **overrides):
    values = {
        "id": index_id,
        "tenant_id": "tenant_default",
        "repository_id": "repo_fix02",
        "project_id": "proj_fix02",
        "base_branch": "main",
        "commit_sha": "c0ffee1234567890",
        "milvus_collection": "col_fix02",
        "chunk_count": 3,
        "retry_count": 0,
        "created_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _child_env(database_url: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "USE_SQLALCHEMY": "true",
        "ENTERPRISE_SHARED_PERSISTENCE": "",
        "DATABASE_URL": database_url,
        "ENV_FILE": str(BACKEND_ROOT / "tests" / ".env.hermetic-nonexistent"),
        "APP_ENV": "local",
    })
    for key in ("ZHIPU_API_KEY", "EMBEDDING_DIMENSIONS", "LLM_API_KEY", "MILVUS_URI"):
        env.pop(key, None)
    return env


def test_ready_record_survives_memory_reset(persistent_db):
    entry = index_lifecycle.register_build(
        index_id="idx_fix02_restart", tenant_id="tenant_default",
        repository_id="repo_fix02", project_id="proj_fix02", base_branch="main",
        commit_sha="c0ffee1234567890", collection_name="col_fix02")
    index_lifecycle.mark_ready(_record("idx_fix02_restart"), chunk_count=3)

    index_lifecycle.reset_store()  # 模拟应用重启 / 清空内存缓存
    loaded = index_lifecycle.get("idx_fix02_restart")
    assert loaded is not None
    assert loaded.status == "ready"
    assert loaded.embedding.dimension == entry.embedding.dimension
    assert loaded.embedding.fingerprint == entry.embedding.fingerprint
    assert loaded.chunk_count == 3
    compat, reasons = index_lifecycle.assess_compatibility(
        loaded, index_lifecycle.current_embedding_fingerprint())
    assert (compat, reasons) == ("compatible", [])


def test_worker_process_update_visible_to_api(persistent_db):
    code = (
        # pymilvus.settings 在导入时调用 load_dotenv()，会无视 ENV_FILE 重新读 backend/.env；
        # 子进程在导入 app 前禁用它，保证隔离环境零凭据。
        "import dotenv as _dotenv\n"
        "_dotenv.load_dotenv = lambda *a, **k: False\n"
        "from datetime import datetime, timezone\n"
        "from types import SimpleNamespace\n"
        "from app.enterprise_rag import index_lifecycle as lc\n"
        "rec = SimpleNamespace(id='idx_fix02_child', tenant_id='tenant_default', "
        "repository_id='repo_fix02', project_id='proj_fix02', base_branch='main', "
        "commit_sha='childcommit', milvus_collection='col_child', chunk_count=5, "
        "retry_count=0, created_at=datetime.now(timezone.utc))\n"
        "lc.register_build(index_id='idx_fix02_child', tenant_id='tenant_default', "
        "repository_id='repo_fix02', project_id='proj_fix02', base_branch='main', "
        "commit_sha='childcommit', collection_name='col_child')\n"
        "lc.mark_ready(rec, chunk_count=5)\n"
        "assert lc.get('idx_fix02_child').embedding.model == lc.MOCK_MODEL\n"
        "print(lc.get('idx_fix02_child').status)\n"
    )
    proc = subprocess.run(
        [PY, "-c", code], cwd=str(BACKEND_ROOT), env=_child_env(persistent_db),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().endswith("ready")

    index_lifecycle.reset_store()  # API 进程内存中没有 Worker 写入
    entry = index_lifecycle.get("idx_fix02_child")
    assert entry is not None and entry.status == "ready"
    assert entry.chunk_count == 5
    assert index_lifecycle.assess_compatibility(
        entry, index_lifecycle.current_embedding_fingerprint()) == ("compatible", [])


def test_supersede_state_is_shared_and_persisted(persistent_db):
    index_lifecycle.register_build(
        index_id="idx_fix02_old", tenant_id="tenant_default", repository_id="repo_fix02",
        project_id="proj_fix02", base_branch="main", commit_sha="old", collection_name="old")
    index_lifecycle.mark_ready(_record("idx_fix02_old", commit_sha="old"), chunk_count=1)

    index_lifecycle.register_build(
        index_id="idx_fix02_new", tenant_id="tenant_default", repository_id="repo_fix02",
        project_id="proj_fix02", base_branch="main", commit_sha="new", collection_name="new")
    index_lifecycle.mark_ready(_record("idx_fix02_new", commit_sha="new"), chunk_count=2)

    index_lifecycle.reset_store()
    old = index_lifecycle.get("idx_fix02_old")
    new = index_lifecycle.get("idx_fix02_new")
    assert old.status == "superseded" and old.superseded_by == "idx_fix02_new"
    assert new.status == "ready"


def test_legacy_record_missing_fields_is_untrusted(persistent_db):
    from app.db.session import session_scope
    from app.models.orm import EnterpriseRagIndexLifecycleORM

    now = datetime.now(timezone.utc)
    with session_scope() as session:
        session.add(EnterpriseRagIndexLifecycleORM(
            index_id="idx_fix02_legacy", tenant_id="tenant_default",
            repository_id="repo_fix02", project_id="proj_fix02", base_branch="main",
            commit_sha="legacy", collection_name="", embedding_provider="mock",
            embedding_model="token-hash-64", embedding_dimension=None,
            embedding_source="mock", embedding_fingerprint="", status="ready",
            chunk_count=1, retry_count=0, error_stage=None, error_message=None,
            superseded_by=None, created_at=now, updated_at=now, completed_at=now))

    entry = index_lifecycle.get("idx_fix02_legacy")
    assert entry is not None
    assert index_lifecycle.assess_compatibility(
        entry, index_lifecycle.current_embedding_fingerprint()) == (
            "untrusted", ["fingerprint_missing"])


def test_dimension_unknown_record_is_not_compatible(persistent_db):
    entry = index_lifecycle.register_build(
        index_id="idx_fix02_unknown_dim", tenant_id="tenant_default",
        repository_id="repo_fix02", project_id="proj_fix02", base_branch="main",
        commit_sha="unknown", collection_name="col")
    entry.embedding.dimension = None
    entry.status = "ready"
    index_lifecycle._persist(entry)

    index_lifecycle.reset_store()
    loaded = index_lifecycle.get("idx_fix02_unknown_dim")
    assert index_lifecycle.assess_compatibility(
        loaded, index_lifecycle.current_embedding_fingerprint()) == (
            "unknown", ["dimension_unverified"])


def _make_ready(index_id: str, tenant_id: str, commit: str) -> None:
    index_lifecycle.register_build(
        index_id=index_id, tenant_id=tenant_id, repository_id="repo_fix02",
        project_id="proj_fix02", base_branch="main", commit_sha=commit,
        collection_name=f"col_{index_id}")
    index_lifecycle.mark_ready(
        _record(index_id, tenant_id=tenant_id, commit_sha=commit), chunk_count=1)


def _supersede_audits(events: list) -> list:
    return [event for event in events
            if getattr(event, "event_type", "") == "tenant.rag.index.superseded"]


def test_supersede_is_tenant_scoped_in_memory():
    """内存路径：同仓库同分支的其它租户索引不得被 A 的新索引 supersede。"""
    from app import main

    _make_ready("idx_a_old", "tenant_a", "a_old")
    _make_ready("idx_b_old", "tenant_b", "b_old")
    _make_ready("idx_a_new", "tenant_a", "a_new")

    assert index_lifecycle.get("idx_a_old").status == "superseded"
    assert index_lifecycle.get("idx_b_old").status == "ready", "B 的 ready 索引不得被 A 淘汰"
    assert index_lifecycle.get("idx_a_new").status == "ready"
    audits = _supersede_audits(list(main.AUDIT_EVENTS.values()))
    assert {event.resource_id for event in audits} == {"idx_a_old"}
    assert {event.tenant_id for event in audits} == {"tenant_a"}

    _make_ready("idx_b_new", "tenant_b", "b_new")
    assert index_lifecycle.get("idx_b_old").status == "superseded"
    assert index_lifecycle.get("idx_a_new").status == "ready", "A 的新索引不得被 B 淘汰"
    audits = _supersede_audits(list(main.AUDIT_EVENTS.values()))
    assert {event.resource_id for event in audits} == {"idx_a_old", "idx_b_old"}
    assert {event.tenant_id for event in audits} == {"tenant_a", "tenant_b"}


def test_supersede_is_tenant_scoped_in_shared_db(persistent_db):
    """DB 路径：直接核对持久化行，B 的记录必须保持 ready。"""
    from app.db import shared_stores
    from app.db.repositories import load_audit_events

    _make_ready("idx_db_a_old", "tenant_a", "a_old")
    _make_ready("idx_db_b_old", "tenant_b", "b_old")
    _make_ready("idx_db_a_new", "tenant_a", "a_new")

    a_old = shared_stores.load_index_lifecycle("idx_db_a_old")
    b_old = shared_stores.load_index_lifecycle("idx_db_b_old")
    a_new = shared_stores.load_index_lifecycle("idx_db_a_new")
    assert a_old["status"] == "superseded" and a_old["superseded_by"] == "idx_db_a_new"
    assert b_old["status"] == "ready", "B 的持久化 ready 行不得被 A 更新"
    assert a_new["status"] == "ready"

    audits = [row for row in load_audit_events()
              if row["event_type"] == "tenant.rag.index.superseded"]
    assert {row["resource_id"] for row in audits} == {"idx_db_a_old"}
    assert {row["tenant_id"] for row in audits} == {"tenant_a"}

    _make_ready("idx_db_b_new", "tenant_b", "b_new")
    assert shared_stores.load_index_lifecycle("idx_db_b_old")["status"] == "superseded"
    assert shared_stores.load_index_lifecycle("idx_db_a_new")["status"] == "ready"
    audits = [row for row in load_audit_events()
              if row["event_type"] == "tenant.rag.index.superseded"]
    assert {row["resource_id"] for row in audits} == {"idx_db_a_old", "idx_db_b_old"}
    assert {row["tenant_id"] for row in audits} == {"tenant_a", "tenant_b"}


def test_migration_creates_shared_tables(tmp_path, monkeypatch):
    from sqlalchemy import inspect

    from app.db import migrate, session as db_session

    db_path = tmp_path / "fix02_migrate.db"
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.delenv("ENTERPRISE_SHARED_PERSISTENCE", raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    try:
        migrate.run_upgrade()
        inspector = inspect(db_session.get_engine())
        tables = set(inspector.get_table_names())
        assert {"enterprise_rag_evaluation_runs", "enterprise_rag_index_lifecycles",
                "enterprise_runtime_trace_spans"} <= tables
        lifecycle_columns = {c["name"] for c in inspector.get_columns("enterprise_rag_index_lifecycles")}
        assert {"index_id", "tenant_id", "repository_id", "base_branch", "commit_sha",
                "collection_name", "embedding_provider", "embedding_model",
                "embedding_dimension", "embedding_fingerprint", "status", "retry_count",
                "error_stage", "error_message", "superseded_by"} <= lifecycle_columns
        trace_columns = {c["name"] for c in inspector.get_columns("enterprise_runtime_trace_spans")}
        assert {"span_id", "trace_id", "tenant_id", "category", "status", "attributes",
                "tokens", "created_at"} <= trace_columns
    finally:
        db_session.get_engine.cache_clear()
        db_session.get_session_factory.cache_clear()
