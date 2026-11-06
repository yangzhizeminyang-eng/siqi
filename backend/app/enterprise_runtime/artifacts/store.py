"""企业版 E4-02 ArtifactStore：内存 + DB 写入穿透、租户隔离、版本、引用图、幂等。

- Agent 之间只经 artifact_refs 交换；resolve_refs 按请求者权限返回允许字段，不把上游完整原文塞入 Prompt。
- 幂等：artifact_id 主键 + (tenant_id, idempotency_key) 唯一键；重试/重复投递返回既有行。
"""

from __future__ import annotations

import threading
from datetime import datetime

from app.enterprise_runtime.artifacts.models import AgentArtifact

_ARTIFACTS: dict[str, AgentArtifact] = {}
_BY_IDEMPOTENCY: dict[tuple[str, str], str] = {}  # (tenant_id, idempotency_key) -> artifact_id
_BY_TASK: dict[tuple[str, str], list[str]] = {}  # (tenant_id, task_id) -> [artifact_id]
_LOCK = threading.RLock()


def reset_store() -> None:
    with _LOCK:
        _ARTIFACTS.clear()
        _BY_IDEMPOTENCY.clear()
        _BY_TASK.clear()


def get(artifact_id: str) -> AgentArtifact | None:
    with _LOCK:
        return _ARTIFACTS.get(artifact_id)


def get_by_idempotency(tenant_id: str, idempotency_key: str) -> AgentArtifact | None:
    with _LOCK:
        aid = _BY_IDEMPOTENCY.get((tenant_id, idempotency_key))
        return _ARTIFACTS.get(aid) if aid else None


def list_by_task(tenant_id: str, task_id: str) -> list[AgentArtifact]:
    with _LOCK:
        ids = _BY_TASK.get((tenant_id, task_id), [])
        return [_ARTIFACTS[i] for i in ids if i in _ARTIFACTS]


def put(artifact: AgentArtifact) -> AgentArtifact:
    with _LOCK:
        existing = _ARTIFACTS.get(artifact.artifact_id)
        if existing is not None:
            return existing  # artifact_id 幂等：已存在返回既有
        if artifact.idempotency_key:
            dup = _BY_IDEMPOTENCY.get((artifact.tenant_id, artifact.idempotency_key))
            if dup is not None and dup in _ARTIFACTS:
                return _ARTIFACTS[dup]  # 重复投递 → 返回既有
        _ARTIFACTS[artifact.artifact_id] = artifact
        if artifact.idempotency_key:
            _BY_IDEMPOTENCY[(artifact.tenant_id, artifact.idempotency_key)] = artifact.artifact_id
        _BY_TASK.setdefault((artifact.tenant_id, artifact.task_id), []).append(artifact.artifact_id)
        _persist(artifact)
        return artifact


def _persist(artifact: AgentArtifact) -> None:
    try:
        from app.db.repositories import persistence_enabled, upsert_agent_artifact

        if persistence_enabled():
            upsert_agent_artifact(artifact)
    except Exception:  # noqa: BLE001 测试/无 DB 环境静默降级
        pass


def resolve_refs(refs: list[str], *, requester_tenant_id: str,
                 requester_agent_type: str | None = None,
                 allowed_read_artifact_types: set[str] | None = None,
                 allowed_fields_by_type: dict[str, set[str]] | None = None,
                 allowed_fields: set[str] | None = None,
                 max_payload_chars: int = 4000) -> dict:
    """受控物化（C8/#4）：按请求者权限返回每个引用的”允许字段投影 + 摘要“，并在 Store 侧双重校验。

    校验（缺一即拒为 None，不进入请求者输入）：
      - 引用存在（否则 None）
      - tenant_id == requester_tenant_id（否则 None）
      - requester_agent_type 允许读取该 artifact_type（allowed_read_artifact_types，否则 None）
      - 内容字段白名单按 “artifact_type -> allowed_content_fields” 投影（else 视 allowed_fields 兼容旧用法）
    """
    always = {"artifact_id", "artifact_type", "agent_type", "version", "status",
              "content_hash", "schema_version", "created_at"}
    out: dict[str, dict | None] = {}
    for ref in refs:
        art = _ARTIFACTS.get(ref)
        if art is None or art.tenant_id != requester_tenant_id:
            out[ref] = None
            continue
        atype = art.artifact_type.value if hasattr(art.artifact_type, "value") else str(art.artifact_type)
        if allowed_read_artifact_types and atype not in allowed_read_artifact_types:
            out[ref] = None  # 类型越权：拒绝（requester_agent_type 的边界）
            continue
        # 内容字段：优先按类型白名单（agent_type -> artifact_type -> fields）；无则回落兼容 allowed_fields
        type_fields = (allowed_fields_by_type or {}).get(atype)
        if type_fields is not None:
            content_fields = set(type_fields)
        else:
            content_fields = set(allowed_fields or ())
        fields = set(always) | content_fields
        d: dict = {"artifact_id": art.artifact_id, "artifact_type": atype,
                   "agent_type": art.agent_type, "version": art.version,
                   "status": art.status, "content_hash": art.content_hash,
                   "schema_version": art.schema_version, "created_at": art.created_at.isoformat()}
        if art.content:
            allow_content = {k: v for k, v in art.content.items() if k in fields and k not in always}
            d["content"] = allow_content
            limit = max_payload_chars
            if d["content"] and len(str(d["content"])) > limit:
                d["content"] = str(d["content"])[:limit] + "…<truncated>"
        if art.content_ref:
            d["content_ref"] = art.content_ref
        out[ref] = d
    return out


def restore_from_db() -> int:
    """启动恢复：从 DB 真源载入产物到内存缓存（重启后产物引用链不断，C9）。"""
    try:
        from app.db.repositories import load_all_agent_artifacts, persistence_enabled
    except Exception:  # noqa: BLE001
        return 0
    if not persistence_enabled():
        return 0
    rows = load_all_agent_artifacts()
    with _LOCK:
        for item in rows:
            try:
                art = AgentArtifact(
                    artifact_id=item["artifact_id"], task_id=item["task_id"], tenant_id=item["tenant_id"],
                    agent_type=item["agent_type"], artifact_type=item["artifact_type"],
                    run_id=item.get("run_id"), version=int(item.get("version", 1) or 1),
                    input_refs=list(item.get("input_refs") or []),
                    output_refs=list(item.get("output_refs") or []),
                    status=item.get("status") or "active",
                    schema_version=int(item.get("schema_version", 1) or 1),
                    content=item.get("content"), content_ref=item.get("content_ref"),
                    content_hash=item.get("content_hash") or "",
                    idempotency_key=item.get("idempotency_key") or "",
                    created_at=item.get("created_at") or _utcnow(),
                )
            except Exception:  # noqa: BLE001
                continue
            _ARTIFACTS[art.artifact_id] = art
            if art.idempotency_key:
                _BY_IDEMPOTENCY[(art.tenant_id, art.idempotency_key)] = art.artifact_id
            _BY_TASK.setdefault((art.tenant_id, art.task_id), []).append(art.artifact_id)
    return len(rows)


def _utcnow() -> datetime:
    from app.enterprise_runtime.models import utcnow as _u
    return _u()