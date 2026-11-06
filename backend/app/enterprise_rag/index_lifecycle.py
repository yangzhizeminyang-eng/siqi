"""FIX-02 企业 RAG 索引生命周期：Embedding 指纹记录 + 不兼容/不可信拒绝 + 状态/审计。

验收口径：「记录模型/维度/索引版本；不兼容拒绝；过期、重建、失败恢复有状态和审计」。

真源契约：
- `USE_SQLALCHEMY=true`（或显式 `ENTERPRISE_SHARED_PERSISTENCE=true`）时，
  `enterprise_rag_index_lifecycles` 表是 API 与 Worker 共享的唯一真源；重启/清空内存缓存
  后仍可准确判断兼容性。内存 dict 仅作写穿缓存与纯内存测试路径。
- 向量相关模式（vector/hybrid/rerank）只在 `compatible` 时放行：
  记录缺失、维度未知、指纹缺失或状态非 ready（building/failed/superseded）一律 fail-closed，
  明确 409 原因并写 `tenant.rag.index.incompatible_rejected` 审计。
- bm25 不消费向量，可放行，但响应必须如实标注兼容性与生命周期状态。
- 指纹只读配置与进程内维度缓存，绝不触发网络探测。
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field

LIFECYCLE_STATES = ("building", "ready", "failed", "superseded")
VECTOR_CONSUMING_MODES = ("vector", "hybrid", "rerank")
MOCK_DIMENSION = 64
MOCK_MODEL = "token-hash-64"

logger = logging.getLogger("backend.enterprise_rag.index_lifecycle")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _persistent() -> bool:
    try:
        from app.db.repositories import shared_persistence_enabled

        return shared_persistence_enabled()
    except Exception:  # noqa: BLE001
        return False


class EmbeddingFingerprint(BaseModel):
    """构建/检索时固化的 Embedding 身份：provider/model/dimension + 短指纹。

    dimension=None 表示「配置了真实 provider 但维度未验证（EMBEDDING_DIMENSIONS 未设且
    无探测缓存）」→ 兼容判定必须降级为 unknown，不可冒称兼容。
    """

    provider: str
    model: str
    dimension: int | None = None
    source: str = "mock"  # mock | configured
    fingerprint: str = ""


class IndexLifecycleRecord(BaseModel):
    """单个索引的生命周期记录：模型/维度/索引版本 + 状态推进 + 失败/重试/过期信息。"""

    index_id: str
    tenant_id: str = "tenant_default"
    repository_id: str = ""
    project_id: str = ""
    base_branch: str = ""
    commit_sha: str = ""
    collection_name: str = ""
    embedding: EmbeddingFingerprint
    status: str = "building"  # building | ready | failed | superseded
    chunk_count: int = 0
    retry_count: int = 0
    error_stage: str | None = None
    error_message: str | None = None
    superseded_by: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None

    def to_public(self) -> dict:
        return self.model_dump(mode="json")


def _from_dict(data: dict) -> IndexLifecycleRecord:
    return IndexLifecycleRecord(
        index_id=data["index_id"],
        tenant_id=data.get("tenant_id") or "tenant_default",
        repository_id=data.get("repository_id") or "",
        project_id=data.get("project_id") or "",
        base_branch=data.get("base_branch") or "",
        commit_sha=data.get("commit_sha") or "",
        collection_name=data.get("collection_name") or "",
        embedding=EmbeddingFingerprint(
            provider=data.get("embedding_provider") or "",
            model=data.get("embedding_model") or "",
            dimension=data.get("embedding_dimension"),
            source=data.get("embedding_source") or "",
            fingerprint=data.get("embedding_fingerprint") or "",
        ),
        status=data.get("status") or "building",
        chunk_count=int(data.get("chunk_count") or 0),
        retry_count=int(data.get("retry_count") or 0),
        error_stage=data.get("error_stage"),
        error_message=data.get("error_message"),
        superseded_by=data.get("superseded_by"),
        created_at=data.get("created_at") or _now(),
        updated_at=data.get("updated_at") or _now(),
        completed_at=data.get("completed_at"),
    )


def make_fingerprint(provider: str, model: str, dimension: int | None, source: str) -> EmbeddingFingerprint:
    raw = f"{provider}|{model}|{dimension if dimension is not None else 'auto'}"
    return EmbeddingFingerprint(
        provider=provider,
        model=model,
        dimension=dimension,
        source=source,
        fingerprint=hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16],
    )


def current_embedding_fingerprint() -> EmbeddingFingerprint:
    """当前环境的 Embedding 指纹：未配置真实 Key → mock(token-hash 64)；

    已配置 → 读 settings（EMBEDDING_DIMENSIONS 未设时只读探测缓存，绝不触发网络探测）。
    """
    from app.core import model_config

    settings = model_config.load_embedding_settings()
    if not settings.configured:
        return make_fingerprint("mock", MOCK_MODEL, MOCK_DIMENSION, "mock")
    dimension = settings.dimensions
    if dimension is None:
        from app.services import embedding_service

        dimension = embedding_service.last_real_embedding_dimension()
    return make_fingerprint(settings.provider, settings.model, dimension, "configured")


def assess_compatibility(
    record: "IndexLifecycleRecord | None", current: EmbeddingFingerprint
) -> tuple[str, list[str]]:
    """兼容判定：compatible / incompatible / unknown / untrusted（不可验证绝不冒称兼容）。"""
    if record is None:
        return "unknown", ["no_lifecycle_record"]
    if record.status != "ready":
        return "untrusted", [f"lifecycle_status:{record.status}"]
    recorded = record.embedding
    if not recorded.fingerprint or not current.fingerprint:
        return "untrusted", ["fingerprint_missing"]
    if recorded.dimension is None or current.dimension is None:
        return "unknown", ["dimension_unverified"]
    if recorded.dimension != current.dimension:
        return "incompatible", [
            f"dimension_mismatch: recorded={recorded.dimension} current={current.dimension}"
        ]
    if (recorded.provider, recorded.model) != (current.provider, current.model):
        return "incompatible", [
            f"embedding_model_changed: recorded={recorded.provider}/{recorded.model} "
            f"current={current.provider}/{current.model}"
        ]
    return "compatible", []


def guard_allows_mode(compatibility: str, mode: str) -> bool:
    """向量模式只允许 compatible；bm25 不消费向量，按既定语义放行但需如实标注。"""
    if mode in VECTOR_CONSUMING_MODES:
        return compatibility == "compatible"
    return True


# ---------------- 审计（脱敏；失败不阻断主流程） ----------------
def _audit(*, tenant_id: str, action: str, resource_id: str, summary: str) -> None:
    from app import main

    try:
        event = main.AuditEventRecord(
            id=f"audit_{uuid.uuid4().hex[:16]}",
            event_type=f"tenant.rag.index.{action}",
            tenant_id=tenant_id or "tenant_default",
            resource_type="repository_index",
            resource_id=(resource_id or "")[:80],
            actor_id="",
            actor_name="",
            summary=summary[:500],
            diff_summary=[],
            created_at=_now(),
            execution_step_ids=[],
        )
        main.AUDIT_EVENTS[event.id] = event
        from app.tenancy.context import _persist_audit

        _persist_audit(event)
    except Exception:  # noqa: BLE001 审计失败不阻断主流程，但必须可观测
        logger.warning("[rag-index] 审计写入失败 action=%s index=%s", action, resource_id, exc_info=True)


def audit_incompatible_rejection(ctx, *, entry: "IndexLifecycleRecord", current: EmbeddingFingerprint,
                                 reasons: list[str], compatibility: str = "incompatible") -> None:
    recorded = entry.embedding
    _audit(
        tenant_id=str(getattr(ctx, "tenant_id", "") or ""),
        action="incompatible_rejected",
        resource_id=entry.index_id,
        summary=(
            f"向量检索被拒绝：索引 {entry.index_id} 兼容性={compatibility} | "
            f"记录 {recorded.provider}/{recorded.model}/{recorded.dimension} "
            f"status={entry.status} → "
            f"当前 {current.provider}/{current.model}/{current.dimension} | {'; '.join(reasons)}"
        ),
    )


# ---------------- store（DB 真源 / 内存隔离路径） ----------------
_RECORDS: dict[str, "IndexLifecycleRecord"] = {}


def reset_store() -> None:
    # 只清内存缓存；持久化真源由测试临时库隔离，不得在此删除。
    _RECORDS.clear()


def get(index_id: str) -> IndexLifecycleRecord | None:
    if _persistent():
        from app.db import shared_stores

        try:
            data = shared_stores.load_index_lifecycle(index_id)
        except Exception:  # noqa: BLE001 读取失败 → unknown（fail-closed），不让检索带病放行
            logger.warning("[rag-index] 生命周期读取失败 index=%s（按 unknown 处理）", index_id, exc_info=True)
            return None
        if data is None:
            return None
        entry = _from_dict(data)
        _RECORDS[index_id] = entry
        return entry
    return _RECORDS.get(index_id)


def list_by_tenant(tenant_id: str, *, repository_id: str | None = None,
                   limit: int = 200) -> list[IndexLifecycleRecord]:
    if _persistent():
        from app.db import shared_stores

        try:
            return [_from_dict(item) for item in shared_stores.load_index_lifecycles(
                tenant_id, repository_id=repository_id, limit=limit)]
        except Exception:  # noqa: BLE001
            logger.warning("[rag-index] 生命周期列表读取失败 tenant=%s", tenant_id, exc_info=True)
            return []
    records = [
        r for r in _RECORDS.values()
        if r.tenant_id == tenant_id and (not repository_id or r.repository_id == repository_id)
    ]
    records.sort(key=lambda r: (r.created_at, r.index_id), reverse=True)
    return records[: max(1, int(limit))]


def _persist(entry: IndexLifecycleRecord) -> None:
    if not _persistent():
        _RECORDS[entry.index_id] = entry
        return
    from app.db import shared_stores

    shared_stores.upsert_index_lifecycle(entry)
    _RECORDS[entry.index_id] = entry


def register_build(*, index_id: str, tenant_id: str = "tenant_default", repository_id: str = "",
                   project_id: str = "", base_branch: str = "", commit_sha: str = "",
                   collection_name: str = "") -> IndexLifecycleRecord:
    """索引任务创建即登记（building），固化构建时 Embedding 指纹（索引版本的一部分）。"""
    now = _now()
    existing = get(index_id)
    entry = IndexLifecycleRecord(
        index_id=index_id,
        tenant_id=tenant_id or "tenant_default",
        repository_id=repository_id,
        project_id=project_id,
        base_branch=base_branch,
        commit_sha=commit_sha,
        collection_name=collection_name,
        embedding=current_embedding_fingerprint(),
        status="building",
        retry_count=existing.retry_count if existing else 0,
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
    _persist(entry)
    return entry


def _entry_for(record) -> tuple[IndexLifecycleRecord, bool]:
    """按 main.RepositoryIndexRecord（鸭子类型）取/建生命周期记录。返回 (entry, created)。"""
    index_id = str(getattr(record, "id", "") or "")
    now = _now()
    entry = get(index_id)
    if entry is not None:
        return entry, False
    entry = IndexLifecycleRecord(
        index_id=index_id,
        tenant_id=str(getattr(record, "tenant_id", "") or "tenant_default"),
        repository_id=str(getattr(record, "repository_id", "") or ""),
        project_id=str(getattr(record, "project_id", "") or ""),
        base_branch=str(getattr(record, "base_branch", "") or ""),
        commit_sha=str(getattr(record, "commit_sha", "") or ""),
        collection_name=str(getattr(record, "milvus_collection", "") or ""),
        embedding=current_embedding_fingerprint(),
        created_at=getattr(record, "created_at", None) or now,
        updated_at=now,
    )
    return entry, True


def mark_ready(record, *, chunk_count: int | None = None) -> IndexLifecycleRecord | None:
    """索引 COMPLETED：记录 ready + 切片数/集合；同租户同仓库同分支旧 ready 记录转 superseded（+审计）。"""
    entry, _ = _entry_for(record)
    now = _now()
    entry.status = "ready"
    entry.collection_name = str(getattr(record, "milvus_collection", "") or entry.collection_name)
    entry.chunk_count = int(chunk_count if chunk_count is not None else getattr(record, "chunk_count", 0) or 0)
    entry.error_stage = None
    entry.error_message = None
    entry.completed_at = now
    entry.updated_at = now

    superseded: list[IndexLifecycleRecord] = []
    if _persistent():
        from app.db import shared_stores

        superseded = [_from_dict(item) for item in shared_stores.mark_index_lifecycle_ready(entry)]
        _RECORDS[entry.index_id] = entry
    else:
        _RECORDS[entry.index_id] = entry
        superseded = [
            other for other in _RECORDS.values()
            if other.index_id != entry.index_id and other.status == "ready"
            and other.tenant_id == entry.tenant_id
            and other.repository_id == entry.repository_id and other.base_branch == entry.base_branch
        ]
        for other in superseded:
            other.status = "superseded"
            other.superseded_by = entry.index_id
            other.updated_at = now

    for other in superseded:
        _audit(
            tenant_id=other.tenant_id,
            action="superseded",
            resource_id=other.index_id,
            summary=(
                f"索引 {other.index_id}（commit={other.commit_sha[:12]}）已被新索引 "
                f"{entry.index_id}（commit={entry.commit_sha[:12]}）取代，转为过期"
            ),
        )
    return entry


def mark_failed(record, *, stage: str, message: str) -> IndexLifecycleRecord | None:
    entry, _ = _entry_for(record)
    now = _now()
    entry.status = "failed"
    entry.error_stage = str(stage or "")
    entry.error_message = str(message or "")[:400]
    entry.updated_at = now
    _persist(entry)
    return entry


def mark_retry(record) -> IndexLifecycleRecord | None:
    """失败重试：状态回 building、retry_count 对齐索引记录、清空错误（失败恢复有状态）。"""
    entry, _ = _entry_for(record)
    now = _now()
    entry.status = "building"
    entry.retry_count = int(getattr(record, "retry_count", 0) or 0)
    entry.error_stage = None
    entry.error_message = None
    entry.completed_at = None
    entry.superseded_by = None
    entry.updated_at = now
    _persist(entry)
    return entry
