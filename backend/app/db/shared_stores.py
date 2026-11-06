"""FIX-01/02/03 跨进程共享 store 的 SQLAlchemy 仓储。

唯一真源是数据库；内存 store 仅作为非持久化测试/本地降级路径与写穿缓存。
`shared_persistence_enabled()` 默认跟随 USE_SQLALCHEMY，显式设置
`ENTERPRISE_SHARED_PERSISTENCE` 可在业务运行时保持内存态的同时共享这些跨进程状态。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.db.repositories import shared_persistence_enabled
from app.db.session import session_scope
from app.models.orm import (
    EnterpriseRagEvaluationRunORM,
    EnterpriseRagIndexLifecycleORM,
    EnterpriseRuntimeTraceSpanORM,
)


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _eval_run_data(run: Any) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "tenant_id": run.tenant_id,
        "dataset": run.dataset,
        "retrieval_mode": getattr(run, "retrieval_mode", "hybrid") or "hybrid",
        "top_k": int(getattr(run, "top_k", 8) or 8),
        "execution_scope": getattr(run, "execution_scope", "isolated") or "isolated",
        "status": getattr(run, "status", "queued") or "queued",
        "validation_status": getattr(run, "validation_status", "") or "",
        "report_name": getattr(run, "report_name", None),
        "summary": dict(getattr(run, "summary", {}) or {}),
        "error": getattr(run, "error", "") or "",
        "requested_by": getattr(run, "requested_by", "") or "",
        "requested_role": getattr(run, "requested_role", "developer") or "developer",
        "attempts": int(getattr(run, "attempts", 0) or 0),
        "created_at": getattr(run, "created_at", None) or datetime.now(timezone.utc),
        "started_at": getattr(run, "started_at", None),
        "finished_at": getattr(run, "finished_at", None),
        "updated_at": getattr(run, "updated_at", None)
        or getattr(run, "created_at", None)
        or datetime.now(timezone.utc),
    }


def _eval_run_dict(row: EnterpriseRagEvaluationRunORM) -> dict[str, Any]:
    data = {c.name: getattr(row, c.name) for c in EnterpriseRagEvaluationRunORM.__table__.columns}
    for key in ("created_at", "started_at", "finished_at", "updated_at"):
        data[key] = _aware_utc(data.get(key))
    data["summary"] = dict(data.get("summary") or {})
    return data


def create_eval_run(run: Any) -> dict[str, Any]:
    """幂等创建评测 run：同一 run_id 已存在时返回原记录（重复投递不覆盖真源）。"""
    data = _eval_run_data(run)
    try:
        with session_scope() as session:
            row = session.get(EnterpriseRagEvaluationRunORM, data["run_id"])
            if row is None:
                row = EnterpriseRagEvaluationRunORM(**data)
                session.add(row)
                session.flush()
            return _eval_run_dict(row)
    except IntegrityError:
        with session_scope() as session:
            row = session.get(EnterpriseRagEvaluationRunORM, data["run_id"])
            if row is None:
                raise
            return _eval_run_dict(row)


def update_eval_run(run_id: str, **fields: Any) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(EnterpriseRagEvaluationRunORM, run_id)
        if row is None:
            return None
        if "updated_at" not in fields:
            fields["updated_at"] = datetime.now(timezone.utc)
        for key, value in fields.items():
            if hasattr(row, key):
                setattr(row, key, value)
        session.flush()
        return _eval_run_dict(row)


def load_eval_run(run_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(EnterpriseRagEvaluationRunORM, run_id)
        return _eval_run_dict(row) if row is not None else None


def load_eval_runs(tenant_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.scalars(
            select(EnterpriseRagEvaluationRunORM)
            .where(EnterpriseRagEvaluationRunORM.tenant_id == tenant_id)
            .order_by(
                EnterpriseRagEvaluationRunORM.created_at.desc(),
                EnterpriseRagEvaluationRunORM.run_id.desc(),
            )
            .limit(max(1, int(limit)))
        ).all()
        return [_eval_run_dict(row) for row in rows]


def claim_eval_run(run_id: str, *, now: datetime, stale_before: datetime) -> tuple[str, dict[str, Any] | None]:
    """条件 UPDATE 原子推进 queued→running；返回 (missing|terminal|busy|claimed, run)。

    - 终态幂等返回 terminal，绝不重复执行；
    - running 且租约未过期 → busy（并发/重复 delivery 只有一个赢家）；
    - running 且 started_at 早于 stale_before / 缺失 → 崩溃残留，重新 claim 并 attempts+1。
    """
    stale_wall = stale_before.astimezone(timezone.utc).replace(tzinfo=None)
    with session_scope() as session:
        row = session.get(EnterpriseRagEvaluationRunORM, run_id)
        if row is None:
            return "missing", None
        if row.status in {"succeeded", "blocked", "failed"}:
            return "terminal", _eval_run_dict(row)

        values = {
            "status": "running",
            "started_at": now,
            "finished_at": None,
            "updated_at": now,
            "attempts": int(row.attempts or 0) + 1,
        }
        if row.status == "queued":
            result = session.execute(
                update(EnterpriseRagEvaluationRunORM)
                .where(
                    EnterpriseRagEvaluationRunORM.run_id == run_id,
                    EnterpriseRagEvaluationRunORM.status == "queued",
                )
                .values(**values)
            )
        else:
            # running：只有租约过期/缺失才能接管，避免两个 Worker 同时执行。
            result = session.execute(
                update(EnterpriseRagEvaluationRunORM)
                .where(
                    EnterpriseRagEvaluationRunORM.run_id == run_id,
                    EnterpriseRagEvaluationRunORM.status == "running",
                    or_(
                        EnterpriseRagEvaluationRunORM.started_at.is_(None),
                        EnterpriseRagEvaluationRunORM.started_at < stale_wall,
                    ),
                )
                .values(**values)
            )
        if result.rowcount == 1:
            session.expire_all()
            fresh = session.get(EnterpriseRagEvaluationRunORM, run_id)
            return "claimed", _eval_run_dict(fresh)
        session.expire_all()
        current = session.get(EnterpriseRagEvaluationRunORM, run_id)
        if current is None:
            return "missing", None
        if current.status in {"succeeded", "blocked", "failed"}:
            return "terminal", _eval_run_dict(current)
        return "busy", _eval_run_dict(current)


def _lifecycle_data(entry: Any) -> dict[str, Any]:
    embedding = entry.embedding
    return {
        "index_id": entry.index_id,
        "tenant_id": entry.tenant_id or "tenant_default",
        "repository_id": entry.repository_id or "",
        "project_id": entry.project_id or "",
        "base_branch": entry.base_branch or "",
        "commit_sha": entry.commit_sha or "",
        "collection_name": entry.collection_name or "",
        "embedding_provider": embedding.provider or "",
        "embedding_model": embedding.model or "",
        "embedding_dimension": embedding.dimension,
        "embedding_source": embedding.source or "",
        "embedding_fingerprint": embedding.fingerprint or "",
        "status": entry.status,
        "chunk_count": int(entry.chunk_count or 0),
        "retry_count": int(entry.retry_count or 0),
        "error_stage": entry.error_stage,
        "error_message": entry.error_message,
        "superseded_by": entry.superseded_by,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
        "completed_at": entry.completed_at,
    }


def _lifecycle_dict(row: EnterpriseRagIndexLifecycleORM) -> dict[str, Any]:
    data = {c.name: getattr(row, c.name) for c in EnterpriseRagIndexLifecycleORM.__table__.columns}
    for key in ("created_at", "updated_at", "completed_at"):
        data[key] = _aware_utc(data.get(key))
    return data


def upsert_index_lifecycle(entry: Any) -> None:
    data = _lifecycle_data(entry)
    with session_scope() as session:
        row = session.get(EnterpriseRagIndexLifecycleORM, data["index_id"])
        if row is None:
            session.add(EnterpriseRagIndexLifecycleORM(**data))
        else:
            for key, value in data.items():
                setattr(row, key, value)
        session.flush()


def load_index_lifecycle(index_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(EnterpriseRagIndexLifecycleORM, index_id)
        return _lifecycle_dict(row) if row is not None else None


def load_index_lifecycles(tenant_id: str, *, repository_id: str | None = None,
                          limit: int = 200) -> list[dict[str, Any]]:
    stmt = select(EnterpriseRagIndexLifecycleORM).where(
        EnterpriseRagIndexLifecycleORM.tenant_id == tenant_id)
    if repository_id:
        stmt = stmt.where(EnterpriseRagIndexLifecycleORM.repository_id == repository_id)
    stmt = stmt.order_by(
        EnterpriseRagIndexLifecycleORM.created_at.desc(),
        EnterpriseRagIndexLifecycleORM.index_id.desc(),
    ).limit(max(1, int(limit)))
    with session_scope() as session:
        return [_lifecycle_dict(row) for row in session.scalars(stmt).all()]


def mark_index_lifecycle_ready(entry: Any) -> list[dict[str, Any]]:
    """单事务：当前记录 ready + 同租户/仓库/分支旧 ready 转 superseded，返回被取代记录。"""
    data = _lifecycle_data(entry)
    superseded: list[dict[str, Any]] = []
    with session_scope() as session:
        row = session.get(EnterpriseRagIndexLifecycleORM, data["index_id"])
        if row is None:
            row = EnterpriseRagIndexLifecycleORM(**data)
            session.add(row)
        else:
            for key, value in data.items():
                setattr(row, key, value)
        others = session.scalars(
            select(EnterpriseRagIndexLifecycleORM).where(
                EnterpriseRagIndexLifecycleORM.index_id != entry.index_id,
                # 租户边界：同仓库同分支也不能跨租户 supersede（否则会误伤其它租户索引）
                EnterpriseRagIndexLifecycleORM.tenant_id == (entry.tenant_id or "tenant_default"),
                EnterpriseRagIndexLifecycleORM.status == "ready",
                EnterpriseRagIndexLifecycleORM.repository_id == (entry.repository_id or ""),
                EnterpriseRagIndexLifecycleORM.base_branch == (entry.base_branch or ""),
            )
        ).all()
        for other in others:
            other.status = "superseded"
            other.superseded_by = entry.index_id
            other.updated_at = entry.updated_at
            superseded.append(_lifecycle_dict(other))
        session.flush()
    return superseded


def insert_trace_span(span: Any, *, max_spans: int) -> int:
    """写入 span 并执行保留上限；返回本次淘汰的旧 span 数。

    repository 层再次脱敏 tokens，保证任何直接调用方也不能把敏感值写入数据库。
    """
    from app.enterprise_runtime.observability import scrub_tokens

    data = {
        "span_id": span.span_id,
        "trace_id": span.trace_id or "",
        "tenant_id": span.tenant_id or "",
        "category": span.category,
        "name": span.name,
        "status": span.status or "ok",
        "error_kind": span.error_kind or "",
        "duration_ms": span.duration_ms,
        "attributes": dict(span.attributes or {}),
        "tokens": scrub_tokens(dict(span.tokens or {})),
        "created_at": span.created_at,
    }
    dropped = 0
    try:
        with session_scope() as session:
            exists = session.scalar(
                select(EnterpriseRuntimeTraceSpanORM.seq).where(
                    EnterpriseRuntimeTraceSpanORM.span_id == span.span_id)
            )
            if exists is not None:
                return 0
            session.add(EnterpriseRuntimeTraceSpanORM(**data))
            session.flush()
            count = int(session.scalar(
                select(func.count()).select_from(EnterpriseRuntimeTraceSpanORM)) or 0)
            excess = max(0, count - max(1, int(max_spans)))
            if excess:
                stale = session.scalars(
                    select(EnterpriseRuntimeTraceSpanORM.span_id)
                    .order_by(EnterpriseRuntimeTraceSpanORM.seq.asc())
                    .limit(excess)
                ).all()
                if stale:
                    session.execute(delete(EnterpriseRuntimeTraceSpanORM).where(
                        EnterpriseRuntimeTraceSpanORM.span_id.in_(list(stale))))
                    dropped = len(stale)
    except IntegrityError:
        return 0
    return dropped


def load_trace_spans(tenant_id: str, *, trace_id: str = "", category: str = "", status: str = "",
                     limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    stmt = select(EnterpriseRuntimeTraceSpanORM).where(
        EnterpriseRuntimeTraceSpanORM.tenant_id == tenant_id)
    if trace_id:
        stmt = stmt.where(EnterpriseRuntimeTraceSpanORM.trace_id == trace_id)
    if category:
        stmt = stmt.where(EnterpriseRuntimeTraceSpanORM.category == category)
    if status:
        stmt = stmt.where(EnterpriseRuntimeTraceSpanORM.status == status)
    stmt = (stmt.order_by(EnterpriseRuntimeTraceSpanORM.seq.desc())
            .offset(max(0, int(offset))).limit(max(1, int(limit))))
    with session_scope() as session:
        out = []
        for row in session.scalars(stmt).all():
            data = {c.name: getattr(row, c.name) for c in EnterpriseRuntimeTraceSpanORM.__table__.columns}
            data["created_at"] = _aware_utc(data.get("created_at"))
            data["attributes"] = dict(data.get("attributes") or {})
            data["tokens"] = dict(data.get("tokens") or {})
            out.append(data)
        return out


def trace_retention_info() -> dict[str, Any]:
    with session_scope() as session:
        count = int(session.scalar(
            select(func.count()).select_from(EnterpriseRuntimeTraceSpanORM)) or 0)
        max_seq = int(session.scalar(select(func.max(EnterpriseRuntimeTraceSpanORM.seq))) or 0)
        rows = session.execute(
            select(EnterpriseRuntimeTraceSpanORM.category, func.count())
            .group_by(EnterpriseRuntimeTraceSpanORM.category)
        ).all()
        return {
            "count": count,
            "by_category": {str(category): int(total) for category, total in rows},
            "dropped_total": max(0, max_seq - count),
        }


__all__ = [
    "shared_persistence_enabled",
    "create_eval_run",
    "update_eval_run",
    "load_eval_run",
    "load_eval_runs",
    "claim_eval_run",
    "upsert_index_lifecycle",
    "load_index_lifecycle",
    "load_index_lifecycles",
    "mark_index_lifecycle_ready",
    "insert_trace_span",
    "load_trace_spans",
    "trace_retention_info",
]
