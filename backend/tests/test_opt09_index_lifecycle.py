"""OPT-09 Embedding 与索引生命周期测试（TDD：先红后绿）。

覆盖验收口径「记录模型/维度/索引版本；不兼容拒绝；过期、重建、失败恢复有状态和审计」：
1. Embedding 指纹：mock(token-hash 64) / 真实配置（含维度未知 → unverifiable）；
2. 兼容判定矩阵：compatible / incompatible / unknown（不可验证不冒称兼容）；
3. 生命周期：building → ready（记录模型/维度/版本与切片数）；failed（阶段+原因）；
   retry（retry_count 递增、状态回 building）；superseded（新索引 ready 时旧索引过期 + 审计）；
4. 不兼容拒绝（fail-closed）：向量相关模式（vector/hybrid/rerank）409 +
   tenant.rag.index.incompatible_rejected 审计；bm25 不消费向量 → 放行但如实标注；
   无生命周期记录（旧索引/重启后）→ unknown 放行不拒绝；
5. 只读接口：GET /api/enterprise/rag/indexes(/{index_id})（rag:search + 租户过滤 + 404）；
6. 记录脱敏：不含 api_key/代码正文。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import main

HEAD_A = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
HEAD_B = "f0e1d2c3b4a5968778695a4b3c2d1e0f1a2b3c4d"

ACCOUNTS = {
    "admin": ("admin@company.com", "Admin123!"),          # tenant_owner
    "developer": ("developer@company.com", "Dev123!"),    # developer
    "auditor": ("observer@company.com", "Observer123!"),  # auditor（无 rag:search）
}

CALLBACK_CONTENT = (
    "def handle_payment_callback(request):\n"
    "    # 支付回调幂等处理：重复回调不重复入账\n"
    "    if dedup_key_exists(request.trace_id):\n"
    "        return duplicate_notice()\n"
    "    return success()\n"
)


def _client(who: str) -> TestClient:
    c = TestClient(main.app)
    email, pwd = ACCOUNTS[who]
    assert c.post("/api/auth/login", json={"account": email, "password": pwd}).status_code == 200
    return c


@pytest.fixture()
def fast_index(monkeypatch):
    """索引阶段零等待 + 分支 head/代码快照确定性 seam（与 DEV-008 同口径）。"""
    monkeypatch.setattr(main, "INDEX_STAGE_SECONDS", 0)
    monkeypatch.setattr(main, "fetch_gitee_branch_head", lambda repo, base_branch=None: HEAD_A)
    monkeypatch.setattr(
        main,
        "sync_repository_snapshot",
        lambda repo, commit_sha: [
            main.CodeFile(path="services/payment/callback.py", language="python", content=CALLBACK_CONTENT),
            main.CodeFile(path="README.md", language="markdown", content="project overview"),
        ],
    )
    yield


@pytest.fixture()
def mock_embedding(monkeypatch):
    """显式锁定「未配置真实 embedding」环境：token-hash 64 维，维度缓存清空。"""
    from app.services import embedding_service

    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_DIMENSIONS", raising=False)
    monkeypatch.setattr(embedding_service, "last_real_embedding_dimension", lambda: None)
    yield


def _repo_and_user():
    return main.REPOSITORIES["repo_payment_service"], main.USERS["user_developer"]


def _new_index(commit: str = HEAD_A):
    repo, dev = _repo_and_user()
    return main.new_repository_index(
        repository=repo,
        commit_sha=commit,
        base_branch=repo.default_branch,
        requested_by=dev,
        requested_at=datetime.now(timezone.utc),
    )


def _drive(record) -> None:
    while record.status in main.INDEX_RUNNING_STAGES:
        main.progress_single_repository_index(record)


def _search(c: TestClient, **overrides):
    repo, _ = _repo_and_user()
    body = {
        "query": "支付回调幂等",
        "project_id": repo.project_id,
        "repository_id": repo.id,
        "commit_sha": HEAD_A,
        "retrieval_mode": "bm25",
    }
    body.update(overrides)
    return c.post("/api/enterprise/rag/search", json=body)


def _lifecycle():
    from app.enterprise_rag import index_lifecycle

    return index_lifecycle


# ---------------------------------------------------------------------------
# 1. Embedding 指纹
# ---------------------------------------------------------------------------


def test_mock_fingerprint_records_provider_model_dimension(mock_embedding):
    fp = _lifecycle().current_embedding_fingerprint()
    assert fp.source == "mock"
    assert fp.provider == "mock"
    assert fp.dimension == 64
    assert fp.model
    assert len(fp.fingerprint) == 16 and all(ch in "0123456789abcdef" for ch in fp.fingerprint)


def test_configured_fingerprint_uses_settings_without_network(monkeypatch, mock_embedding):
    from app.services import embedding_service

    monkeypatch.setenv("ZHIPU_API_KEY", "test-key-not-real")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "2048")
    monkeypatch.setattr(embedding_service, "last_real_embedding_dimension", lambda: None)

    fp = _lifecycle().current_embedding_fingerprint()
    assert fp.source == "configured"
    assert fp.provider == "zhipu"
    assert fp.dimension == 2048
    # 与 mock 指纹不同：更换配置后指纹必须变化（否则拒绝逻辑失效）
    assert fp.fingerprint != _lifecycle().make_fingerprint("mock", "x", 64, "mock").fingerprint


def test_configured_unknown_dimension_is_unverifiable(monkeypatch, mock_embedding):
    from app.services import embedding_service

    monkeypatch.setenv("ZHIPU_API_KEY", "test-key-not-real")
    monkeypatch.delenv("EMBEDDING_DIMENSIONS", raising=False)
    monkeypatch.setattr(embedding_service, "last_real_embedding_dimension", lambda: None)

    fp = _lifecycle().current_embedding_fingerprint()
    assert fp.source == "configured" and fp.dimension is None
    assert fp.fingerprint  # 仍可记录，但兼容判定必须是 unknown（不可验证不冒称兼容）


# ---------------------------------------------------------------------------
# 2. 兼容判定矩阵
# ---------------------------------------------------------------------------


def _record_with(fp, status="ready"):
    lc = _lifecycle()
    return lc.IndexLifecycleRecord(index_id=f"idx_{uuid.uuid4().hex[:6]}", embedding=fp, status=status)


def test_compatibility_matrix(mock_embedding):
    lc = _lifecycle()
    mock = lc.make_fingerprint("mock", "token-hash-64", 64, "mock")
    mock2 = lc.make_fingerprint("mock", "token-hash-64", 64, "mock")
    other_dim = lc.make_fingerprint("zhipu", "embedding-3", 2048, "configured")
    same_dim_other_model = lc.make_fingerprint("zhipu", "embedding-4", 64, "configured")
    unverifiable = lc.make_fingerprint("zhipu", "embedding-3", None, "configured")

    assert lc.assess_compatibility(_record_with(mock), mock2) == ("compatible", [])
    compat, reasons = lc.assess_compatibility(_record_with(mock), other_dim)
    assert compat == "incompatible" and reasons
    compat, reasons = lc.assess_compatibility(_record_with(mock), same_dim_other_model)
    assert compat == "incompatible", "同维度不同模型 = 向量空间不同，必须拒绝"
    assert lc.assess_compatibility(_record_with(mock), unverifiable) == ("unknown", ["dimension_unverified"])
    assert lc.assess_compatibility(_record_with(unverifiable), other_dim) == ("unknown", ["dimension_unverified"])
    assert lc.assess_compatibility(None, mock) == ("unknown", ["no_lifecycle_record"])
    # FIX-02：状态不可信（building/failed/superseded）在兼容矩阵中必须判 untrusted，不得放行。
    for status in ("building", "failed", "superseded"):
        assert lc.assess_compatibility(_record_with(mock, status=status), mock) == (
            "untrusted", [f"lifecycle_status:{status}"])


# ---------------------------------------------------------------------------
# 3. 生命周期状态与审计（接真实索引管线）
# ---------------------------------------------------------------------------


def test_build_records_model_dimension_and_index_version(fast_index, mock_embedding):
    record = _new_index()
    entry = _lifecycle().get(record.id)
    assert entry is not None and entry.status == "building"
    assert entry.embedding.dimension == 64 and entry.embedding.source == "mock"
    assert entry.commit_sha == HEAD_A and entry.collection_name

    _drive(record)
    entry = _lifecycle().get(record.id)
    assert entry.status == "ready"
    assert entry.chunk_count == record.chunk_count > 0
    assert entry.completed_at is not None
    assert entry.embedding.fingerprint


def test_failed_index_records_failure_and_retry_state(fast_index, mock_embedding, client, monkeypatch):
    def boom(repo, commit_sha):
        raise RuntimeError("Gitee 快照同步超时")

    monkeypatch.setattr(main, "sync_repository_snapshot", boom)
    record = _new_index()
    main.progress_single_repository_index(record, force=True)
    assert record.status == "FAILED"

    entry = _lifecycle().get(record.id)
    assert entry.status == "failed"
    assert entry.error_stage == "SYNCING" and "超时" in entry.error_message

    # 失败恢复：retry 端点把状态推进回 building 且 retry_count 递增
    resp = client.post(f"/api/repositories/{record.repository_id}/indexes/{record.id}/retry")
    assert resp.status_code == 200, resp.text
    entry = _lifecycle().get(record.id)
    assert entry.retry_count == 1 and entry.status == "building"
    assert entry.error_message in ("", None)


def test_new_ready_index_supersedes_previous(fast_index, mock_embedding):
    first = _new_index(HEAD_A)
    _drive(first)
    assert _lifecycle().get(first.id).status == "ready"

    second = _new_index(HEAD_B)
    _drive(second)

    assert _lifecycle().get(first.id).status == "superseded"
    assert _lifecycle().get(first.id).superseded_by == second.id
    assert _lifecycle().get(second.id).status == "ready"
    superseded = [e for e in main.AUDIT_EVENTS.values()
                  if getattr(e, "event_type", "") == "tenant.rag.index.superseded"]
    assert superseded, "旧索引过期必须留有审计"
    assert superseded[0].resource_id == first.id
    assert superseded[0].tenant_id == first.tenant_id


# ---------------------------------------------------------------------------
# 4. 不兼容拒绝（fail-closed）与放行语义
# ---------------------------------------------------------------------------


def _break_embedding_consistency(monkeypatch):
    from app.services import embedding_service

    monkeypatch.setenv("ZHIPU_API_KEY", "test-key-not-real")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "2048")
    monkeypatch.setattr(embedding_service, "last_real_embedding_dimension", lambda: None)


def test_vector_mode_search_rejected_when_embedding_changed(fast_index, mock_embedding, monkeypatch):
    record = _new_index()
    _drive(record)
    _break_embedding_consistency(monkeypatch)

    admin = _client("admin")
    resp = _search(admin, retrieval_mode="hybrid")
    assert resp.status_code == 409, resp.text
    message = resp.json()["detail"]["message"]
    assert "不兼容" in message and "64" in message and "2048" in message
    assert "results" not in resp.text

    rejected = [e for e in main.AUDIT_EVENTS.values()
                if getattr(e, "event_type", "") == "tenant.rag.index.incompatible_rejected"]
    assert rejected, "不兼容拒绝必须留有审计"
    assert record.id in (rejected[0].summary or "")


def test_bm25_mode_not_blocked_but_annotated(monkeypatch, fast_index, mock_embedding):
    record = _new_index()
    _drive(record)
    _break_embedding_consistency(monkeypatch)

    admin = _client("admin")
    resp = _search(admin, retrieval_mode="bm25")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["index_embedding_compatible"] is False, "bm25 不消费向量：放行但如实标注不兼容"
    assert data["index_embedding_guard_enforced"] is False


def test_compatible_and_unknown_annotations(fast_index, mock_embedding):
    record = _new_index()
    _drive(record)
    admin = _client("admin")
    ok = _search(admin)
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["index_embedding_compatible"] is True
    assert ok.json()["data"]["index_lifecycle_status"] == "ready"

    # 旧索引（无生命周期记录）：bm25 不消费向量 → 放行且如实标注 unknown，不冒称兼容。
    legacy = main.RepositoryIndexRecord(
        id=f"idx_legacy_{uuid.uuid4().hex[:6]}", repository_id=record.repository_id,
        project_id=record.project_id, base_branch="main", commit_sha=HEAD_A,
        status="COMPLETED", requested_by="seed", requested_by_name="seed",
        requested_at=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc))
    main.REPOSITORY_INDEXES.clear()
    main.REPOSITORY_INDEXES[legacy.id] = legacy
    resp = _search(admin)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["index_embedding_compatible"] is None
    assert resp.json()["data"]["index_lifecycle_status"] is None
    assert resp.json()["data"]["index_embedding_compat_state"] == "unknown"


@pytest.mark.parametrize("mode", ["vector", "hybrid", "rerank"])
def test_vector_modes_fail_closed_when_lifecycle_missing(fast_index, mock_embedding, mode):
    """FIX-02：无生命周期记录（旧索引/重启后）不得在向量模式放行。"""
    record = _new_index()
    _drive(record)
    _lifecycle().reset_store()  # 模拟 API/Worker 重启后内存缓存为空

    admin = _client("admin")
    resp = _search(admin, retrieval_mode=mode)
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["data"]["compatibility"] == "unknown"
    assert "no_lifecycle_record" in detail["data"]["reasons"]
    assert "results" not in resp.text
    rejected = [e for e in main.AUDIT_EVENTS.values()
                if e.event_type == "tenant.rag.index.incompatible_rejected"]
    assert rejected and record.id in rejected[-1].summary


@pytest.mark.parametrize("status", ["building", "failed", "superseded"])
def test_vector_modes_fail_closed_when_lifecycle_status_untrusted(
        fast_index, mock_embedding, status):
    record = _new_index()
    _drive(record)
    entry = _lifecycle().get(record.id)
    entry.status = status  # 内存真源模拟状态不可信
    admin = _client("admin")
    resp = _search(admin, retrieval_mode="hybrid")
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["data"]["compatibility"] == "untrusted"
    assert f"lifecycle_status:{status}" in detail["data"]["reasons"]


def test_same_dimension_different_model_rejected_in_vector_mode(
        fast_index, mock_embedding, monkeypatch):
    record = _new_index()
    _drive(record)
    from app.services import embedding_service

    # 同维度（64）但 provider/model 不同：向量空间不同，必须拒绝。
    monkeypatch.setenv("ZHIPU_API_KEY", "test-key-not-real")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "64")
    monkeypatch.setattr(embedding_service, "last_real_embedding_dimension", lambda: None)

    admin = _client("admin")
    resp = _search(admin, retrieval_mode="hybrid")
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["data"]["compatibility"] == "incompatible"
    assert any("embedding_model_changed" in reason for reason in detail["data"]["reasons"])


# ---------------------------------------------------------------------------
# 5. 只读接口：列表 / 详情（rag:search + 租户过滤）
# ---------------------------------------------------------------------------


def test_indexes_api_permission_and_tenant_filtering(fast_index, mock_embedding):
    record = _new_index()
    _drive(record)

    admin = _client("admin")
    listed = admin.get("/api/enterprise/rag/indexes")
    assert listed.status_code == 200, listed.text
    ids = [item["index_id"] for item in listed.json()["data"]["indexes"]]
    assert record.id in ids

    detail = admin.get(f"/api/enterprise/rag/indexes/{record.id}")
    assert detail.status_code == 200
    entry = detail.json()["data"]["index"]
    assert entry["status"] == "ready" and entry["embedding"]["dimension"] == 64
    assert admin.get("/api/enterprise/rag/indexes/idx_missing").status_code == 404

    auditor = _client("auditor")
    assert auditor.get("/api/enterprise/rag/indexes").status_code == 403

    # 租户过滤：其它租户的记录不可见也不可查
    lc = _lifecycle()
    other = lc.register_build(
        index_id=f"idx_other_{uuid.uuid4().hex[:6]}", tenant_id="tenant_other",
        repository_id="repo_other", project_id="proj_other", base_branch="main",
        commit_sha=HEAD_A, collection_name="code_other")
    ids = [item["index_id"] for item in admin.get("/api/enterprise/rag/indexes").json()["data"]["indexes"]]
    assert other.index_id not in ids
    assert admin.get(f"/api/enterprise/rag/indexes/{other.index_id}").status_code == 404


def test_lifecycle_record_has_no_secrets(fast_index, mock_embedding):
    record = _new_index()
    _drive(record)
    dumped = json.dumps(_lifecycle().get(record.id).to_public(), ensure_ascii=False)
    assert "api_key" not in dumped and "ZHIPU" not in dumped
    assert CALLBACK_CONTENT[:20] not in dumped, "不含代码正文"
