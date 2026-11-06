"""企业版 E3-02 Query Rewrite + 多路 Query Expansion 测试。

覆盖规格必查项 a-i。向量腿用桩（确定性、快），BM25 用真实 retrieve_bm25；
真实向量链由团队 RAG 全量回归覆盖。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import main
import app.retrieval.llamaindex_backend as lib
from app.enterprise_rag.terms import build_query_variants

NOW = datetime.now(timezone.utc)
_COMMIT = "a1b2c3d4e5f678901234567890abcdef12345678"


def _now():
    return NOW


def _make_repo(client, repo_id: str, project_id: str, tenant_id: str, commit: str) -> str:
    main.PROJECTS[project_id] = main.ProjectRecord(
        id=project_id, tenant_id=tenant_id, name=project_id, description="",
        created_at=_now(), updated_at=_now())
    main.REPOSITORIES[repo_id] = main.RepositoryConfigRecord(
        id=repo_id, tenant_id=tenant_id, project_id=project_id, name=repo_id,
        url=f"https://gitee.com/demo/{repo_id}.git", default_branch="main",
        test_command="pytest tests/ -q", command_whitelist=["pytest"],
        approval_mode="single", risk_threshold="medium", max_files=10, max_retries=1,
        blocked_paths=[], created_at=_now(), updated_at=_now())
    index = main.RepositoryIndexRecord(
        id=f"idx_{repo_id}", tenant_id=tenant_id, repository_id=repo_id, project_id=project_id,
        base_branch="main", commit_sha=commit, status="COMPLETED",
        requested_by="seed", requested_by_name="seed", requested_at=_now(),
        created_at=_now(), updated_at=_now())
    main.REPOSITORY_INDEXES[index.id] = index
    main.LATEST_INDEX_BY_REPO[repo_id] = index.id
    # FIX-02：暂无生命周期记录的索引在向量模式必须拒绝；测试夹具显式登记 ready。
    from app.enterprise_rag import index_lifecycle

    index_lifecycle.register_build(
        index_id=index.id, tenant_id=tenant_id, repository_id=repo_id,
        project_id=project_id, base_branch="main", commit_sha=commit,
        collection_name=index.milvus_collection or "")
    index_lifecycle.mark_ready(index, chunk_count=0)
    return index.id


def _add_chunk(repo_id: str, index_id: str, chunk_id: str, *, symbol=None, path=None,
               content="", commit=_COMMIT) -> None:
    main.CODE_CHUNKS[chunk_id] = main.CodeChunkRecord(
        chunk_id=chunk_id, repository_id=repo_id, index_id=index_id,
        file_path=path or f"src/{chunk_id}.py", chunk_index=0, content=content,
        language="python", symbol_name=symbol, start_line=1, end_line=2,
        commit_sha=commit, milvus_collection="", milvus_pk="", checksum="x")


def _spy_dense(monkeypatch, results=None):
    captured: dict = {"calls": 0, "chunk_ids": None, "metadata_filters": None}

    def fake(chunks, **kwargs):
        captured["calls"] += 1
        captured["chunk_ids"] = [getattr(c, "chunk_id", None) for c in chunks]
        captured["metadata_filters"] = kwargs.get("metadata_filters")
        if not results:
            return []
        return [r for r in results if r.chunk_id in captured["chunk_ids"]]

    monkeypatch.setattr(lib, "_query_vector_results", fake)
    return captured


def _login(who: str) -> TestClient:
    c = TestClient(main.app)
    accounts = {
        "admin": ("admin@company.com", "Admin123!"),
        "developer": ("developer@company.com", "Dev123!"),
        "observer": ("observer@company.com", "Observer123!"),
    }
    assert c.post("/api/auth/login", json={"account": accounts[who][0], "password": accounts[who][1]}).status_code == 200
    return c


def _search(c: TestClient, body: dict):
    return c.post("/api/enterprise/rag/search", json=body)


# ---------------------------------------------------------------------------
# a. 原始 query 始终保留且排第一；b. 确定性扩展；d. 去重/数量/长度
# ---------------------------------------------------------------------------


def test_original_query_always_first():
    variants = build_query_variants("handle_payment_callback in refund.py", allow_llm=False, max_queries=5)
    assert variants
    assert variants[0] == "handle_payment_callback in refund.py"


def test_deterministic_technical_term_expansion():
    variants = " ".join(build_query_variants(
        "fix ERR_401 in payment.callback.retry at backend/app/main.py",
        allow_llm=False, max_queries=8))
    low = variants.lower()
    assert "payment_callback_retry" in low or "payment.callback.retry" in low
    assert "err_401" in low
    assert "main.py" in low


def test_variants_dedup_and_capped():
    variants = build_query_variants(
        "ERR_401 ERR_401 payment.callback.retry payment.callback.retry",
        allow_llm=False, max_queries=3)
    assert len(variants) == len(set(variants))  # 去重
    assert len(variants) <= 3
    for v in variants:
        assert len(v) <= 200  # 单条长度上限


def test_llm_unavailable_or_invalid_stable_fallback(monkeypatch):
    # LLM 不可用 -> 确定性回退
    monkeypatch.setattr("app.enterprise_rag.terms.model_config.llm_configured", lambda: False)
    variants = build_query_variants("look for handle_refund in api/orders", allow_llm=True, max_queries=4)
    assert variants and variants[0].startswith("look for handle_refund")
    # LLM 输出非法 JSON -> 确定性回退
    monkeypatch.setattr("app.enterprise_rag.terms.model_config.llm_configured", lambda: True)
    monkeypatch.setattr(
        "app.enterprise_rag.terms.DeepSeekTextClient.generate",
        lambda self, prompt, system: "这不是 JSON，也不遵循指令",
    )
    variants2 = build_query_variants("look for handle_refund in api/orders", allow_llm=True, max_queries=4)
    assert variants2 and variants2[0].startswith("look for handle_refund")


# ---------------------------------------------------------------------------
# e/f. 每个 variant 同 filters；扩展 query 不能绕过 rag:search
# ---------------------------------------------------------------------------


def test_auditor_cannot_search_even_with_expansion(monkeypatch):
    _spy_dense(monkeypatch)
    admin = _login("admin")
    _make_repo(admin, "repo_a2", "proj_a2", "tenant_default", _COMMIT)
    _add_chunk("repo_a2", "idx_repo_a2", "a1", content="def x(): ERR_401")
    obs = _login("observer")  # auditor（audit:view 但无 rag:search）
    assert _search(obs, {"query": "ERR_401", "project_id": "proj_a2", "repository_id": "repo_a2",
                         "commit_sha": _COMMIT}).status_code == 403


def test_security_admin_can_search_with_expansion(monkeypatch):
    _spy_dense(monkeypatch)
    admin = _login("admin")
    _make_repo(admin, "repo_sec2", "proj_sec2", "tenant_default", _COMMIT)
    _add_chunk("repo_sec2", "idx_repo_sec2", "sec1", content="def audit_policy_probe(): return 'ERR_SEC'")
    promote = admin.patch("/api/enterprise/members/mem_user_observer_default",
                          json={"role": "security_admin"})
    assert promote.status_code == 200

    security_admin = _login("observer")
    resp = _search(security_admin, {"query": "audit_policy_probe ERR_SEC",
                                    "project_id": "proj_sec2", "repository_id": "repo_sec2",
                                    "commit_sha": _COMMIT})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert any(r["chunk_id"] == "sec1" for r in data["results"])


def test_response_exposes_variants_and_trace(monkeypatch):
    _spy_dense(monkeypatch)
    c = _login("admin")
    _make_repo(c, "repo_t2", "proj_t2", "tenant_default", _COMMIT)
    _add_chunk("repo_t2", "idx_repo_t2", "t1", symbol="resolve_deposit", content="def resolve_deposit(): pass")
    data = _search(c, {"query": "resolve_deposit deposit.callback", "project_id": "proj_t2",
                       "repository_id": "repo_t2", "commit_sha": _COMMIT}).json()["data"]
    assert data["query_variants"] and data["query_variants"][0] == "resolve_deposit deposit.callback"
    assert data["variant_count"] == len(data["query_variants"])
    assert isinstance(data["retrieval_trace"], list)


# ---------------------------------------------------------------------------
# g. BM25 命中精确术语（含点分/下划线桥接）
# ---------------------------------------------------------------------------


def test_bm25_hits_exact_terms_and_dotted_underscore_bridge(monkeypatch):
    _spy_dense(monkeypatch, results=None)
    c = _login("admin")
    _make_repo(c, "repo_g2", "proj_g2", "tenant_default", _COMMIT)
    _add_chunk("repo_g2", "idx_repo_g2", "g1", symbol="process_refund", path="src/api/orders.py",
               content="def process_refund(): raise ERR_401")
    _add_chunk("repo_g2", "idx_repo_g2", "g2", symbol="payment_callback_retry", path="src/pay.py",
               content="def payment_callback_retry(): pass")
    # ERR_401 精确命中
    data = _search(c, {"query": "ERR_401", "project_id": "proj_g2", "repository_id": "repo_g2",
                       "commit_sha": _COMMIT}).json()["data"]
    assert any(r["chunk_id"] == "g1" for r in data["results"])
    # 点分 query 桥接下划线符号 chunk
    data2 = _search(c, {"query": "payment.callback.retry", "project_id": "proj_g2",
                        "repository_id": "repo_g2", "commit_sha": _COMMIT}).json()["data"]
    assert any(r["chunk_id"] == "g2" for r in data2["results"])


# ---------------------------------------------------------------------------
# h. 多 query RRF 原 query 优先 + 全局去重
# ---------------------------------------------------------------------------


def test_multi_query_rrf_original_priority_and_dedup(monkeypatch):
    c = _login("admin")
    _make_repo(c, "repo_h2", "proj_h2", "tenant_default", _COMMIT)
    _add_chunk("repo_h2", "idx_repo_h2", "h1", symbol="dedup_sym", path="h/a.py", content="def dedup_sym(): aligned")
    _add_chunk("repo_h2", "idx_repo_h2", "h2", symbol="other", path="h/b.py", content="other")
    dense = [lib.RetrievedChunk(chunk_id="h1", file_path="h/a.py", start_line=1, end_line=2, language="python",
                                symbol_name="dedup_sym", content="", score=0.9, commit_sha=_COMMIT,
                                index_id="idx_repo_h2", fresh=True)]
    _spy_dense(monkeypatch, results=dense)  # 向量路命中 h1（原 query 相关）
    data = _search(c, {"query": "aligned dedup_sym", "project_id": "proj_h2",
                       "repository_id": "repo_h2", "commit_sha": _COMMIT}).json()["data"]
    ids = [r["chunk_id"] for r in data["results"]]
    assert ids.count("h1") <= 1  # 全局去重
    assert data["query_variants"][0] == "aligned dedup_sym"  # 原 query 排第一
    hit = next((r for r in data["results"] if r["chunk_id"] == "h1"), None)
    if data["variant_count"] > 1:
        # 多 variant 必然 hybrid-headed 稀释但仍在结果并可解释来源
        assert hit is not None
        assert "vector" in hit["contributions"] and "bm25" in hit["contributions"]


# ---------------------------------------------------------------------------
# i. stale / 跨租户 / 团队版不回归见全量；stale 在此复验
# ---------------------------------------------------------------------------


def test_stale_and_cross_tenant_unchanged(monkeypatch):
    _spy_dense(monkeypatch, results=None)
    admin = _login("admin")
    _make_repo(admin, "repo_i2", "proj_i2", "tenant_default", _COMMIT)
    _add_chunk("repo_i2", "idx_repo_i2", "i1", symbol="x", content="def x(): pass")
    older = "0000000000000000000000000000000000000000"
    data = _search(admin, {"query": "x", "project_id": "proj_i2", "repository_id": "repo_i2",
                           "commit_sha": older}).json()["data"]
    assert data["stale"] is True and data["fresh"] is False
    resp = admin.post("/api/enterprise/tenants", json={"name": "TenantE32"})
    assert resp.status_code == 200
    tenant_b = resp.json()["data"]["id"]
    assert admin.post("/api/enterprise/tenant-context/switch", json={"tenant_id": tenant_b}).status_code == 200
    assert _search(admin, {"query": "x", "project_id": "proj_i2", "repository_id": "repo_i2",
                           "commit_sha": _COMMIT}).status_code == 403
