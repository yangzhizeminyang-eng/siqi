"""P2a RAG 持久化测试：写入 / 重启恢复仍可查 / 索引过期门禁。

复用 P0 的临时 sqlite + USE_SQLALCHEMY 开启 + 恢复全局 store 的方式，保证用例自包含。
"""

from __future__ import annotations

import datetime as _dt

import pytest

from app.db import bootstrap
from app.db import repositories
from app.db import session as db_session


@pytest.fixture()
def rag_db(tmp_path, monkeypatch):
    from app import main

    snapshot = {
        name: dict(getattr(main, name)) for name in main.__dict__ if not name.startswith("_") and isinstance(getattr(main, name, None), dict)
    }
    db_file = tmp_path / "rag.db"
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("DATA_DIR", tmp_path.as_posix())
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    db_session.create_schema()
    yield db_file
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()


def _now():
    return _dt.datetime.now(_dt.timezone.utc)


def test_write_code_chunks_and_restore(rag_db):
    """写入代码切片与索引 -> 清空内存 -> restore 后仍在（重启后仍可检索的前提）。"""
    from app import main

    now = _now()
    index = main.RepositoryIndexRecord(
        id="idx_rag_1",
        repository_id="repo_payment_service",
        project_id="proj_mvp",
        base_branch="main",
        commit_sha="aabbcc",
        status="COMPLETED",
        milvus_collection="code_idx_rag_1",
        requested_by="u",
        requested_by_name="n",
        requested_at=now,
        created_at=now,
        updated_at=now,
        finished_at=now,
    )
    chunk = main.CodeChunkRecord(
        chunk_id="ck_rag_1",
        repository_id="repo_payment_service",
        index_id="idx_rag_1",
        file_path="order/list.py",
        chunk_index=0,
        content="def render_orders(): pass",
        language="python",
        symbol_name="render_orders",
        start_line=1,
        end_line=5,
        commit_sha="aabbcc",
        milvus_collection="code_idx_rag_1",
        milvus_pk="pk_milvus_001",
        checksum="chk1",
    )
    repositories.upsert_repository_index(index)
    repositories.upsert_code_chunks([chunk])

    # 清空内存，模拟重启
    main.REPOSITORY_INDEXES.clear()
    main.CODE_CHUNKS.clear()
    main.RETRIEVAL_RECORDS.clear()
    main.LATEST_INDEX_BY_REPO.clear()

    counts = bootstrap.restore_from_db()

    assert counts.get("repository_indexes", 0) == 1
    assert counts.get("code_chunks", 0) == 1
    assert "idx_rag_1" in main.REPOSITORY_INDEXES
    assert "ck_rag_1" in main.CODE_CHUNKS
    assert main.LATEST_INDEX_BY_REPO.get("repo_payment_service") == "idx_rag_1"
    restored = main.CODE_CHUNKS["ck_rag_1"]
    assert restored.milvus_pk == "pk_milvus_001"
    assert restored.content == "def render_orders(): pass"
    assert restored.commit_sha == "aabbcc"


def test_retrieval_record_roundtrip(rag_db):
    from app import main

    now = _now()
    record = main.RetrievalRecord(
        id="retr_1",
        task_id="TASK-r1",
        query_text="订单列表",
        repository_id="repo_payment_service",
        project_id="proj_mvp",
        commit_sha="aabbcc",
        index_id="idx_rag_1",
        top_k=3,
        mode="auto",
        fresh=True,
        threshold=0.3,
        empty_reason=None,
        result_chunk_ids=["ck_rag_1"],
        created_at=now,
    )
    repositories.upsert_retrieval_record(record)
    main.RETRIEVAL_RECORDS.clear()
    counts = bootstrap.restore_from_db()
    assert counts.get("retrieval_records", 0) == 1
    assert main.RETRIEVAL_RECORDS["retr_1"].result_chunk_ids == ["ck_rag_1"]


def test_task_retrieval_reads_worker_chunks_from_db_after_api_cache_clear(rag_db, monkeypatch):
    """Worker 写库后，即使 API 进程缓存为空，检索仍读取当前索引切片。"""
    from app import main

    now = _now()
    index = main.RepositoryIndexRecord(
        id="idx_worker_written",
        repository_id="repo_worker_written",
        project_id="proj_worker_written",
        base_branch="main",
        commit_sha="commit_worker_written",
        status="COMPLETED",
        milvus_collection="code_idx_worker_written",
        requested_by="u",
        requested_by_name="n",
        requested_at=now,
        created_at=now,
        updated_at=now,
        finished_at=now,
    )
    chunk = main.CodeChunkRecord(
        chunk_id="ck_worker_written",
        repository_id=index.repository_id,
        index_id=index.id,
        file_path="backend/app/main.py",
        chunk_index=0,
        content="def create_app(): return app",
        language="python",
        symbol_name="create_app",
        start_line=10,
        end_line=10,
        commit_sha=index.commit_sha,
        milvus_collection=index.milvus_collection,
        milvus_pk="pk_worker_written",
        checksum="checksum_worker_written",
    )
    repositories.upsert_repository_index(index)
    repositories.upsert_code_chunks([chunk])
    main.CODE_CHUNKS.clear()

    task = main.TaskRecord(
        id="TASK-worker-cache",
        version=1,
        project_id=index.project_id,
        project_name="测试项目",
        repository_id=index.repository_id,
        repository_name="测试仓库",
        repository_url="https://gitee.com/example/worker-written.git",
        issue_id="ISSUE-worker-cache",
        title="定位应用入口",
        status="CREATED",
        base_branch="main",
        test_command="pytest -q",
        work_branch="agent/main/issue-worker-cache-v1",
        issue_snapshot=main.IssueSnapshotRecord(
            title="定位应用入口",
            body="请找到应用入口",
            comments=[],
            labels=[],
            raw_response={},
            fetched_at=now,
        ),
        automation_state="running",
        manual_required=False,
        manual_reason="none",
        next_action="check_index",
        events=[],
        created_by="u",
        created_by_name="n",
        created_at=now,
        updated_at=now,
        base_commit_sha=index.commit_sha,
        related_index_id=index.id,
    )

    def fake_query(chunks, **_kwargs):
        assert [item.chunk_id for item in chunks] == [chunk.chunk_id]
        return [
            main.RetrievedChunk(
                chunk_id=chunk.chunk_id,
                file_path=chunk.file_path,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                language=chunk.language,
                symbol_name=chunk.symbol_name,
                content=chunk.content,
                score=0.99,
                commit_sha=chunk.commit_sha,
                index_id=index.id,
                fresh=True,
                source="milvus",
            )
        ]

    monkeypatch.setattr(main, "query_index_from_chunks", fake_query)
    contexts, record = main.run_task_retrieval(task, index, mode="auto", head_commit=index.commit_sha)

    assert len(contexts) == 1
    assert contexts[0].chunk_id == chunk.chunk_id
    assert record.index_id == index.id
    assert record.empty_reason is None
