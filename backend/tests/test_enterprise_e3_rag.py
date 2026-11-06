"""企业版 E3-01 企业 RAG 强过滤 + Hybrid 检索测试。

覆盖规格必查项 a-h。向量腿在单测中用桩（确定性、快），真实向量链由既有团队 RAG 测试回归；
BM25 用真实 `retrieve_bm25`（精确符号/路径召回）。
"""

from __future__ import annotations

import datetime as _dt
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import main
import app.retrieval.llamaindex_backend as lib
from app.retrieval.llamaindex_backend import RetrievedChunk

NOW = datetime.now(timezone.utc)
_COMMIT = "a1b2c3d4e5f678901234567890abcdef12345678"


def _now():
    return NOW


def _make_repo(client, repo_id: str, project_id: str, tenant_id: str, commit: str) -> str:
    """在内存态创建项目/仓库/索引/切片（USE_SQLALCHEMY=false）。"""
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
    # FIX-02：向量模式要求生命周期记录 ready；无记录必须 fail-closed，测试索引显式登记。
    from app.enterprise_rag import index_lifecycle

    index_lifecycle.register_build(
        index_id=index.id, tenant_id=tenant_id, repository_id=repo_id,
        project_id=project_id, base_branch="main", commit_sha=commit,
        collection_name=index.milvus_collection or "")
    index_lifecycle.mark_ready(index, chunk_count=0)
    return index.id


def _add_chunk(repo_id: str, index_id: str, chunk_id: str, *, symbol=None, path=None,
               content="", language="python", commit=_COMMIT) -> None:
    main.CODE_CHUNKS[chunk_id] = main.CodeChunkRecord(
        chunk_id=chunk_id, repository_id=repo_id, index_id=index_id,
        file_path=path or f"src/{chunk_id}.py", chunk_index=0, content=content,
        language=language, symbol_name=symbol, start_line=1, end_line=2,
        commit_sha=commit, milvus_collection="", milvus_pk="", checksum="x")


def _spy_dense(monkeypatch, results=None):
    """把向量腿换成确定性桩，并捕获它接收到的候选子集（验证过滤作用于向量候选）。"""
    captured: dict = {}

    def fake(chunks, **kwargs):
        captured["chunk_ids"] = [getattr(c, "chunk_id", None) for c in chunks]
        captured["repository_id"] = kwargs.get("repository_id")
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
# a. 缺边界 / 缺版本被拒绝
# ---------------------------------------------------------------------------


def test_missing_project_or_repo_rejected():
    c = _login("admin")
    assert _search(c, {"query": "how to", "project_id": "proj_e3", "repository_id": "repo_e3"}).status_code == 422


def test_missing_commit_or_ref_rejected():
    c = _login("admin")
    resp = _search(c, {"query": "how to", "project_id": "proj_payment", "repository_id": "repo_payment_service"})
    assert resp.status_code == 422
    assert "ref" in resp.text or "commit" in resp.text.lower()


# ---------------------------------------------------------------------------
# b. 请求体伪造 tenant_id 无效（只信任服务端上下文）
# ---------------------------------------------------------------------------


def test_forged_tenant_id_in_body_ignored(monkeypatch):
    _spy_dense(monkeypatch)
    c = _login("admin")
    _make_repo(c, "repo_e3", "proj_e3", "tenant_default", _COMMIT)
    _add_chunk("repo_e3", "idx_repo_e3", "c1", symbol="compute_tax", path="src/tax.py", content="def compute_tax(x): return 0")
    body = {"query": "compute_tax", "project_id": "proj_e3", "repository_id": "repo_e3",
            "commit_sha": _COMMIT, "tenant_id": "tenant_other_forged"}
    data = _search(c, body).json()["data"]
    assert data["filters"]["tenant_id"] == "tenant_default"


# ---------------------------------------------------------------------------
# c. 跨租户仓库查询 403 + 审计
# ---------------------------------------------------------------------------


def test_cross_tenant_repo_query_is_403_and_audited(monkeypatch):
    _spy_dense(monkeypatch)
    admin = _login("admin")
    _make_repo(admin, "repo_default", "proj_default", "tenant_default", _COMMIT)
    resp = admin.post("/api/enterprise/tenants", json={"name": "TenantE3"})
    assert resp.status_code == 200
    tenant_b = resp.json()["data"]["id"]
    assert admin.post("/api/enterprise/tenant-context/switch", json={"tenant_id": tenant_b}).status_code == 200

    r = _search(admin, {"query": "x", "project_id": "proj_default", "repository_id": "repo_default",
                        "commit_sha": _COMMIT})
    assert r.status_code == 403
    # 跨租户拒绝审计：事件类型 tenant.<resource>.denied，归属于激活租户（B），概要含企业 RAG 标识
    cross = [e for e in main.AUDIT_EVENTS.values()
             if e.event_type == "tenant.repository.denied" and "RAG" in e.summary]
    assert cross, "跨租户查询未写入租户归属审计"
    assert all(e.tenant_id == tenant_b for e in cross)


# ---------------------------------------------------------------------------
# c.1 企业检索权限：auditor 可看审计，但不能检索租户代码
# ---------------------------------------------------------------------------


def test_auditor_cannot_search_enterprise_rag_code():
    admin = _login("admin")
    _make_repo(admin, "repo_rag_authorized", "proj_rag_authorized", "tenant_default", _COMMIT)
    observer = _login("observer")

    response = _search(observer, {
        "query": "sensitive implementation",
        "project_id": "proj_rag_authorized",
        "repository_id": "repo_rag_authorized",
        "commit_sha": _COMMIT,
    })

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# d. MetadataFilters 同时限制 BM25 与向量候选
# ---------------------------------------------------------------------------


def test_metadata_filters_restrict_bm25_and_vector_candidates(monkeypatch):
    captured = _spy_dense(monkeypatch)
    c = _login("admin")
    _make_repo(c, "repo_a", "proj_a", "tenant_default", _COMMIT)
    _make_repo(c, "repo_b", "proj_a", "tenant_default", _COMMIT)
    # repo_a 中含精确符号 hit_token；repo_b 中也含同符号，但查询限定 repo_a
    _add_chunk("repo_a", "idx_repo_a", "a1", symbol="hit_token", path="a/mod.py", content="def hit_token(): pass")
    _add_chunk("repo_b", "idx_repo_b", "b1", symbol="hit_token", path="b/mod.py", content="def hit_token(): pass")

    data = _search(c, {"query": "hit_token", "project_id": "proj_a", "repository_id": "repo_a",
                       "commit_sha": _COMMIT}).json()["data"]
    ids = {r["chunk_id"] for r in data["results"]}
    assert "a1" in ids
    assert "b1" not in ids, "过滤应先作用于候选生成，repo_b 的切片不应进入召回"
    # 向量腿收到的候选子集同样不含 repo_b
    assert "b1" not in (captured.get("chunk_ids") or [])
    assert (captured.get("chunk_ids") or []) == ["a1"]


def test_enterprise_vector_query_receives_commit_and_source_type_filters(monkeypatch):
    captured = _spy_dense(monkeypatch)
    c = _login("admin")
    _make_repo(c, "repo_vector_filters", "proj_vector_filters", "tenant_default", _COMMIT)
    _add_chunk(
        "repo_vector_filters",
        "idx_repo_vector_filters",
        "vf1",
        symbol="filter_target",
        path="src/filter_target.py",
        content="def filter_target(): pass",
    )

    response = _search(c, {
        "query": "filter_target",
        "project_id": "proj_vector_filters",
        "repository_id": "repo_vector_filters",
        "commit_sha": _COMMIT,
        "source_types": ["code"],
    })

    assert response.status_code == 200, response.text
    vector_filters = captured["metadata_filters"]
    filter_values = {item.key: item.value for item in vector_filters.filters}
    assert filter_values["repository_id"] == "repo_vector_filters"
    assert filter_values["index_id"] == "idx_repo_vector_filters"
    assert filter_values["commit_sha"] == _COMMIT
    assert filter_values["source_type"] == ["code"]


# ---------------------------------------------------------------------------
# e. BM25 命中精确符号，向量缺失仍可召回
# ---------------------------------------------------------------------------


def test_bm25_hits_exact_symbol_when_vector_empty(monkeypatch):
    captured = _spy_dense(monkeypatch, results=None)  # 向量腿无结果
    c = _login("admin")
    _make_repo(c, "repo_e", "proj_e", "tenant_default", _COMMIT)
    _add_chunk("repo_e", "idx_repo_e", "e1", symbol="process_refund", path="src/refund.py",
               content="class RefundService: pass", language="python")
    data = _search(c, {"query": "process_refund", "project_id": "proj_e",
                       "repository_id": "repo_e", "commit_sha": _COMMIT}).json()["data"]
    assert len(data["results"]) >= 1
    hit = next(r for r in data["results"] if r["chunk_id"] == "e1")
    assert hit["contributions"]["bm25"] is True
    assert hit["retrieval_source"] in ("bm25", "hybrid")


# ---------------------------------------------------------------------------
# f. RRF 融合、去重、hybrid 来源
# ---------------------------------------------------------------------------


def test_rrf_fusion_dedupe_and_hybrid_source(monkeypatch):
    c = _login("admin")
    _make_repo(c, "repo_f", "proj_f", "tenant_default", _COMMIT)
    _add_chunk("repo_f", "idx_repo_f", "f1", symbol="shared_sym", path="f/a.py", content="def shared_sym(): aligned_with_query")
    dense = [RetrievedChunk(chunk_id="f1", file_path="f/a.py", start_line=1, end_line=2, language="python",
                            symbol_name="shared_sym", content="", score=0.8, commit_sha=_COMMIT,
                            index_id="idx_repo_f", fresh=True)]
    _spy_dense(monkeypatch, results=dense)
    data = _search(c, {"query": "shared_sym aligned_with_query", "project_id": "proj_f",
                       "repository_id": "repo_f", "commit_sha": _COMMIT}).json()["data"]
    # 去重：f1 只出现一次
    ids = [r["chunk_id"] for r in data["results"]]
    assert ids.count("f1") == 1
    hit = next(r for r in data["results"] if r["chunk_id"] == "f1")
    # 同时命中 BM25 与向量 -> hybrid
    assert hit["contributions"]["vector"] is True
    assert hit["contributions"]["bm25"] is True
    assert hit["retrieval_source"] == "hybrid"


# ---------------------------------------------------------------------------
# g. stale index 识别
# ---------------------------------------------------------------------------


def test_stale_index_flagged(monkeypatch):
    _spy_dense(monkeypatch, results=None)
    c = _login("admin")
    _make_repo(c, "repo_g", "proj_g", "tenant_default", _COMMIT)
    _add_chunk("repo_g", "idx_repo_g", "g1", symbol="stale_sym", path="g/a.py", content="def stale_sym(): pass")
    older = "0000000000000000000000000000000000000000"
    data = _search(c, {"query": "stale_sym", "project_id": "proj_g", "repository_id": "repo_g",
                       "commit_sha": older}).json()["data"]
    assert data["fresh"] is False
    assert data["stale"] is True
    assert data["notice"] is not None


# ---------------------------------------------------------------------------
# h. 团队版 RAG 主链路不回归（查询索引仍可用）
# ---------------------------------------------------------------------------


def test_team_rag_chain_still_useable_via_existing_index(monkeypatch):
    # 团队版 task 检索只读链路：确认 REPOSITORY_INDEXES/CODE_CHUNKS 仍可被旧接口读取（回归信号）
    c = _login("admin")
    _make_repo(c, "repo_t", "proj_t", "tenant_default", _COMMIT)
    _add_chunk("repo_t", "idx_repo_t", "t1", symbol="legacy", path="t/x.py", content="def legacy(): return 1")
    from app.db import reads

    assert reads.latest_completed_index("repo_t") is not None
    assert len(reads.code_chunks_of_index("idx_repo_t")) == 1
