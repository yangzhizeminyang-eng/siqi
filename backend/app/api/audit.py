"""审计与审批队列域接口：审计事件、待审批队列、审批人候选。

OPT-11：自 app/main.py 迁出（行为不变；路由表由 tests/test_opt11_main_split.py 冻结）。
``approval_candidate`` 定义在 main.py 的 app 初始化之后，故经 ``app.main`` 惰性读取。

OPT-14：新增 ``GET /api/audit-events/export``（JSONL/CSV 导出，租户作用域 + 审计可见性门 +
导出动作留审计），路由冻结表受控 +1（117→118），见 tests/test_opt11_main_split.py。
"""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response

from app.main import (
    ApiResponse,
    USERS,
    UserRecord,
    api_success,
    current_tenant_id,
    get_current_user,
    public_user,
    require_audit_view,
    require_permission,
)

router = APIRouter(tags=["audit"])
logger = logging.getLogger("backend.api.audit")

# OPT-14 导出列（JSONL 对象键 / CSV 表头，共用同一顺序，防止两种格式字段漂移）
_EXPORT_FIELDS = (
    "id", "event_type", "tenant_id", "resource_type", "resource_id",
    "actor_id", "actor_name", "summary", "diff_summary", "created_at", "execution_step_ids",
)


def _as_utc(value: datetime) -> datetime:
    """统一为 aware UTC：naive 视为 UTC 挂钟（与落库口径一致）。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _serialize_event(event: Any) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for field in _EXPORT_FIELDS:
        value = getattr(event, field, None)
        data[field] = value.isoformat() if isinstance(value, datetime) else value
    return data


def _record_export_audit(user: UserRecord, fmt: str, count: int, truncated: bool) -> None:
    """导出动作本身留审计（失败不阻断导出；不含敏感信息）。"""
    from app import main
    from app.core.clock import utcnow as audit_now

    try:
        event = main.AuditEventRecord(
            id=f"audit_{uuid.uuid4().hex[:16]}",
            event_type="tenant.audit.exported",
            tenant_id=current_tenant_id() or "tenant_default",
            resource_type="audit_export",
            resource_id=fmt,
            actor_id=user.id,
            actor_name=getattr(user, "name", "") or "",
            summary=f"审计导出 format={fmt} count={count} truncated={str(truncated).lower()}"[:500],
            diff_summary=[],
            created_at=audit_now(),
            execution_step_ids=[],
        )
        main.AUDIT_EVENTS[event.id] = event
        from app.tenancy.context import _persist_audit

        _persist_audit(event)
    except Exception:  # noqa: BLE001 审计失败不阻断主流程，但必须可观测
        logger.warning("[audit] 导出审计写入失败 format=%s", fmt, exc_info=True)


@router.get("/api/audit-events")
def list_audit_events(
    user: Annotated[UserRecord, Depends(get_current_user)],
    resource_type: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
) -> ApiResponse:
    require_audit_view(user)
    from app.db.reads import list_audit_events as read_audit_events

    return api_success(read_audit_events(resource_type, resource_id, tenant_id=current_tenant_id()))


@router.get("/api/audit-events/export")
def export_audit_events(
    user: Annotated[UserRecord, Depends(get_current_user)],
    format: Literal["jsonl", "csv"] = Query(default="jsonl"),
    event_type: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=5000),
) -> Response:
    """审计事件导出（租户作用域）：JSONL 默认 / CSV 可选，截断如实标注在响应头。"""
    require_audit_view(user)
    from app.db.reads import list_audit_events as read_audit_events

    events = read_audit_events(resource_type, resource_id, tenant_id=current_tenant_id())
    if event_type:
        events = [e for e in events if e.event_type == event_type]
    if since:
        since_utc = _as_utc(since)
        events = [e for e in events if _as_utc(e.created_at) >= since_utc]
    if until:
        until_utc = _as_utc(until)
        events = [e for e in events if _as_utc(e.created_at) <= until_utc]

    rows = events[:limit]
    truncated = len(events) > len(rows)

    if format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(_EXPORT_FIELDS)
        for event in rows:
            data = _serialize_event(event)
            writer.writerow([data[field] if data[field] is not None else "" for field in _EXPORT_FIELDS])
        body = buffer.getvalue()
        media_type = "text/csv"
    else:
        lines = [json.dumps(_serialize_event(event), ensure_ascii=False) for event in rows]
        body = ("\n".join(lines) + "\n") if lines else ""
        media_type = "application/x-ndjson"

    _record_export_audit(user, format, len(rows), truncated)
    headers = {
        "Content-Disposition": f'attachment; filename="audit-events.{format}"',
        "X-Audit-Export-Count": str(len(rows)),
        "X-Audit-Export-Truncated": "true" if truncated else "false",
        "X-Audit-Export-Format": format,
    }
    return Response(content=body, media_type=media_type, headers=headers)


@router.get("/api/approvals/pending")
def list_pending_approvals(user: Annotated[UserRecord, Depends(get_current_user)]) -> ApiResponse:
    """审批中心队列：仅返回当前用户可审批的待审批任务（管理员可见全部）。"""
    require_permission(user, "approval:view")
    from app.db import reads

    return api_success(reads.list_pending_approvals(user))


@router.get("/api/approvals/candidates")
def list_approval_candidates(user: Annotated[UserRecord, Depends(get_current_user)]) -> ApiResponse:
    """指派/改派审批人的候选用户（管理员/审批人角色）；供无 user:view 的 lead 指派入口取数。"""
    from app import main

    require_permission(user, "approval:assign")
    candidates = sorted((item for item in USERS.values() if main.approval_candidate(item)), key=lambda item: item.name)
    return api_success([public_user(item) for item in candidates])
