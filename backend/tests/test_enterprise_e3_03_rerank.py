"""企业版 E3-03 Rerank 精排基线测试。

unit 直接测 enterprise_rerank（确定性、可控输入）；端点测安全与可解释字段。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import main
import app.retrieval.llamaindex_backend as lib
from app.enterprise_rag.models import EnterpriseRetrievedChunk
from app.enterprise_rag.reranker import enterprise_rerank

NOW = datetime.now(timezone.utc)
_COMMIT = "a1b2c3d4e5f678901234567890abcdef12345678"


def _chunk(chunk_id, score, fresh=True, symbol="", path=""):
    return EnterpriseRetrievedChunk(
        chunk_id=chunk_id, source_type="code", file_path=path,
        start_line=1, end_line=2, language="python", symbol_name=(symbol or None),
        repository_id="re1", index_id="in1", commit_sha=_COMMIT,
        retrieval_source="hybrid", score=score, fresh=fresh)


def _make_repo(client, repo_id, project_id, tenant_id):
    main.PROJECTS[project_id] = main.ProjectRecord(
        id=project_id, tenant_id=tenant_id, name=project_id, description="",
        created_at=NOW, updated_at=NOW)
    main.REPOSITORIES[repo_id] = main.RepositoryConfigRecord(
        id=repo_id, tenant_id=tenant_id, project_id=project_id, name=repo_id,
        url=f"https://gitee.com/demo/{repo_id}.git", default_branch="main",
        test_command="pytest tests/ -q", command_whitelist=["pytest"],
        approval_mode="single", risk_threshold="medium", max_files=10, max_retries=1,
        blocked_paths=[], created_at=NOW, updated_at=NOW)
    index = main.RepositoryIndexRecord(
        id=f"idx_{repo_id}", tenant_id=tenant_id, repository_id=repo_id, project_id=project_id,
        base_branch="main", commit_sha=_COMMIT, status="COMPLETED",
        requested_by="s", requested_by_name="s", requested_at=NOW, created_at=NOW, updated_at=NOW)
    main.REPOSITORY_INDEXES[index.id] = index
    main.LATEST_INDEX_BY_REPO[repo_id] = index.id
    # FIX-02：测试索引必须显式登记 ready 生命周期，避免把“旧索引无记录”误当兼容。
    from app.enterprise_rag import index_lifecycle

    index_lifecycle.register_build(
        index_id=index.id, tenant_id=tenant_id, repository_id=repo_id,
        project_id=project_id, base_branch="main", commit_sha=_COMMIT,
        collection_name=index.milvus_collection or "")
    index_lifecycle.mark_ready(index, chunk_count=1)
    main.CODE_CHUNKS[f"{repo_id}_c1"] = main.CodeChunkRecord(
        chunk_id=f"{repo_id}_c1", repository_id=repo_id, index_id=index.id,
        file_path="src/refund.py", chunk_index=0, content="def handle_payment_callback(): raise ERR_401",
        language="python", symbol_name="handle_payment_callback", start_line=1, end_line=2,
        commit_sha=_COMMIT, milvus_collection="", milvus_pk="", checksum="x")


def _spy_dense(monkeypatch):
    captured: dict = {"metadata_filters": None, "chunk_ids": None}

    def fake(chunks, **kwargs):
        captured["chunk_ids"] = [getattr(c, "chunk_id", None) for c in chunks]
        captured["metadata_filters"] = kwargs.get("metadata_filters")
        return []
    monkeypatch.setattr(lib, "_query_vector_results", fake)
    return captured


def _login(who):
    c = TestClient(main.app)
    accounts = {"admin": ("admin@company.com", "Admin123!"),
                "observer": ("observer@company.com", "Observer123!")}
    assert c.post("/api/auth/login", json={"account": accounts[who][0], "password": accounts[who][1]}).status_code == 200
    return c


def test_heuristic_symbol_hit_rises_rank():
    a = _chunk("a", score=0.2, fresh=True, symbol="other_func", path="src/x.py")
    b = _chunk("b", score=0.1, fresh=False, symbol="handle_payment_callback", path="src/refund.py")
    reranked, trace = enterprise_rerank([a, b], query_text="handle_payment_callback",
                                        enabled=True, provider="heuristic", top_n=8, timeout_seconds=5)
    ids = [r.chunk_id for r in reranked]
    assert ids[0] == "b"
    b_after = next(r for r in reranked if r.chunk_id == "b")
    assert b_after.original_rank == 2
    assert b_after.reranked_rank == 1
    assert b_after.rerank_score is not None and "符号" in (b_after.rerank_reason or "")
    assert trace.get("enabled") is True and trace.get("reranked") is True


def test_fresh_preferred_over_stale():
    stale = _chunk("s", score=0.2, fresh=False, path="src/a.py")
    fresh = _chunk("f", score=0.19, fresh=True, path="src/b.py")
    reranked, _ = enterprise_rerank([stale, fresh], query_text="some terms not matching",
                                    enabled=True, provider="heuristic", top_n=8, timeout_seconds=5)
    assert reranked[0].chunk_id == "f"


def test_disabled_rerank_keeps_order_and_flags():
    a = _chunk("a", score=0.5, symbol="zzz")
    b = _chunk("b", score=0.5, symbol="aaa")
    reranked, trace = enterprise_rerank([a, b], query_text="nothing", enabled=False,
                                        provider="heuristic", top_n=8, timeout_seconds=5)
    assert [r.chunk_id for r in reranked] == ["a", "b"]
    assert trace.get("enabled") is False


def test_llm_unavailable_falls_back_to_heuristic(monkeypatch):
    monkeypatch.setattr("app.core.model_config.llm_configured", lambda: False)
    a = _chunk("a", score=0.2, symbol="other", path="src/x.py")
    b = _chunk("b", score=0.1, symbol="init_refund", path="src/r.py", fresh=False)
    reranked, trace = enterprise_rerank([a, b], query_text="init_refund",
                                        enabled=True, provider="llm", top_n=8, timeout_seconds=5)
    assert len(reranked) == 2
    assert trace.get("provider") == "heuristic"


def test_llm_rerank_uses_enterprise_timeout_setting(monkeypatch):
    captured: dict = {"timeout": None}

    class FakeDeepSeekTextClient:
        def __init__(self, settings=None):
            captured["timeout"] = getattr(settings, "timeout_seconds", None)

        def generate(self, prompt, *, system=None):
            return '{"ranked_chunk_ids":["b","a"]}'

    monkeypatch.setattr("app.core.model_config.llm_configured", lambda: True)
    monkeypatch.setattr("app.model_clients.deepseek_client.DeepSeekTextClient", FakeDeepSeekTextClient)
    a = _chunk("a", score=0.2, symbol="other", path="src/x.py")
    b = _chunk("b", score=0.1, symbol="init_refund", path="src/r.py", fresh=False)

    reranked, trace = enterprise_rerank([a, b], query_text="init_refund",
                                        enabled=True, provider="llm", top_n=8, timeout_seconds=3)

    assert captured["timeout"] == 3
    assert [r.chunk_id for r in reranked[:2]] == ["b", "a"]
    assert trace.get("provider") == "llm"


def test_trace_contains_no_code_body_or_secret_values():
    a = _chunk("a", score=0.2, symbol="ok_symbol", path="src/key.py")
    _, trace = enterprise_rerank([a], query_text="ok_symbol", enabled=True,
                                 provider="heuristic", top_n=8, timeout_seconds=5)
    reasons = " ".join(item.get("reason", "") for item in trace.get("reasons", []))
    assert "content" not in reasons.lower()
    assert "password" not in reasons.lower()
    assert "def " not in reasons


def test_auditor_403_even_with_rerank(monkeypatch):
    _spy_dense(monkeypatch)
    monkeypatch.setenv("RAG_RERANK_ENABLED", "true")
    _make_repo(_login("admin"), "repo_r1", "proj_r1", "tenant_default")
    obs = _login("observer")
    assert obs.post("/api/enterprise/rag/search", json={
        "query": "handle_payment_callback", "project_id": "proj_r1",
        "repository_id": "repo_r1", "commit_sha": _COMMIT}).status_code == 403


def test_security_admin_search_returns_rerank_fields():
    admin = _login("admin")
    _make_repo(admin, "repo_r2", "proj_r2", "tenant_default")
    assert admin.patch("/api/enterprise/members/mem_user_observer_default",
                       json={"role": "security_admin"}).status_code == 200
    sec = _login("observer")
    resp = sec.post("/api/enterprise/rag/search", json={
        "query": "handle_payment_callback", "project_id": "proj_r2",
        "repository_id": "repo_r2", "commit_sha": _COMMIT})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "rerank_enabled" in data and "rerank_provider" in data and "rerank_trace" in data


def test_rerank_does_not_change_filter_boundaries(monkeypatch):
    captured = _spy_dense(monkeypatch)
    monkeypatch.setenv("RAG_RERANK_ENABLED", "true")
    admin = _login("admin")
    _make_repo(admin, "repo_r3", "proj_r3", "tenant_default")
    admin.post("/api/enterprise/rag/search", json={
        "query": "handle_payment_callback", "project_id": "proj_r3",
        "repository_id": "repo_r3", "commit_sha": _COMMIT})
    assert captured.get("metadata_filters") is not None
    assert captured.get("chunk_ids")
