"""企业版租户上下文：请求级 contextvar + 集中式资源归属门。

- get_current_user（main.py）解析用户后调用 resolve_active_tenant 并把上下文写入
  current_tenant contextvar；成员禁用/移除/未入租户 → 直接拒绝。
- require_resource_tenant 为业务资源集中归属门：无请求上下文（后台 worker）不强制，
  保证团队版 worker 流程与既有测试不受影响；请求上下文激活时租户不匹配 → 403 + 审计。
- 所有租户判断集中在本文，禁止在接口里散落 tenant if/else。
"""

from __future__ import annotations

import contextvars
import logging
import uuid

from fastapi import HTTPException, status

from app.core.clock import utcnow as audit_now

from app.tenancy.models import (
    DEFAULT_TENANT_ID,
    TenantContext,
    TenantMembershipRecord,
)
from app.tenancy.permissions import permissions_for
from app.tenancy import store

logger = logging.getLogger("backend.tenancy.context")

current_tenant: contextvars.ContextVar[TenantContext | None] = contextvars.ContextVar(
    "current_tenant", default=None
)


class TenantDenied(HTTPException):
    """跨租户/无租户 403 的专用异常，便于调用方审计后统一抛出。"""

    def __init__(self, message: str = "无权访问该资源"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": 403, "message": message, "data": None},
        )


def denied(message: str = "无权执行该操作") -> TenantDenied:
    return TenantDenied(message)


def forbidden(message: str = "无权执行该操作") -> TenantDenied:
    return TenantDenied(message)


def resolve_active_tenant(user) -> TenantContext:
    """从用户成员关系解析激活租户；成员被禁用/移除则抛 401。

    服务端解析激活租户，从不信任前端传入的 tenant_id 或 X-Tenant-Id。
    """
    membership = store.active_membership_for_user(user.id)
    if membership is None or membership.status != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 401, "message": "账号未加入任何可用租户或已被禁用", "data": None},
        )
    return build_context(membership)


def build_context(membership: TenantMembershipRecord) -> TenantContext:
    tenant = store.get_tenant(membership.tenant_id)
    return TenantContext(
        user_id=membership.user_id,
        tenant_id=membership.tenant_id,
        tenant_name=tenant.name if tenant else membership.tenant_id,
        role=membership.role,
        permissions=permissions_for(membership.role),
    )


def context_for_current_user() -> TenantContext:
    """返回当前请求的租户上下文（由 get_current_user 预先解析并写入 contextvar）。"""
    ctx = current_tenant.get()
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 401, "message": "缺少租户上下文", "data": None},
        )
    return ctx


def require_resource_tenant(resource_tenant_id: str | None, *, resource_type: str = "business",
                            resource_id: str = "") -> None:
    """业务资源归属门：资源必须属于激活租户，否则 403 + 审计。

    资源无 tenant_id（历史数据）按默认租户兜底。无请求上下文（worker）不强制。
    """
    ctx = current_tenant.get()
    if ctx is None:
        return  # 后台 worker / 无请求上下文：不强制（保持团队版基线）
    owner_tenant = resource_tenant_id or DEFAULT_TENANT_ID
    if owner_tenant == ctx.tenant_id:
        return
    deny_and_audit(ctx, f"tenant.resource.{resource_type}", "跨租户资源访问被拒绝",
                   resource_type=resource_type, resource_id=resource_id)
    raise TenantDenied("无权访问该资源")


def deny_and_audit(ctx: TenantContext, action: str, reason: str, *,
                   resource_type: str = "tenant", resource_id: str = "",
                   result: str = "denied") -> None:
    """写入不含敏感信息的租户审计事件（全局审计表/内存）。"""
    from app import main  # 延迟导入避免循环

    now = audit_now()
    try:
        event = main.AuditEventRecord(
            # 唯一 id：同一 actor+tenant 的多次拒绝不得互相覆盖（dict 事件表键冲突会丢审计）
            id=main.next_id_audit() if hasattr(main, "next_id_audit") else f"audit_{uuid.uuid4().hex[:16]}",
            event_type=f"tenant.{resource_type}.denied",
            tenant_id=ctx.tenant_id,
            resource_type=resource_type,
            resource_id=resource_id or str(ctx.tenant_id),
            actor_id=ctx.user_id,
            actor_name="",
            summary=f"{reason}: tenant={ctx.tenant_id} action={action}",
            diff_summary=[],
            created_at=now,
            execution_step_ids=[],
        )
        main.AUDIT_EVENTS[event.id] = event
        _persist_audit(event)
    except Exception:  # noqa: BLE001 审计失败不阻断主流程
        logger.warning("[tenancy] 拒绝审计写入失败 tenant=%s action=%s", ctx.tenant_id, action, exc_info=True)


def _persist_audit(event) -> None:
    try:
        from app.db.repositories import shared_persistence_enabled, upsert_audit_event

        if shared_persistence_enabled():
            upsert_audit_event(event)
    except Exception:  # noqa: BLE001 审计持久化失败不阻断主流程，但必须可观测
        logger.warning("[tenancy] 审计持久化失败 id=%s", getattr(event, "id", "?"), exc_info=True)


def audit_member_action(ctx: TenantContext, action: str, resource_id: str, summary: str) -> None:
    """成员管理类的成功/失败审计（不含敏感信息）。"""
    from app import main  # 延迟导入避免循环
    import uuid

    try:
        event = main.AuditEventRecord(
            id=f"audit_{uuid.uuid4().hex[:16]}",
            event_type=f"tenant.{action}",
            tenant_id=ctx.tenant_id,
            resource_type="tenant_member",
            resource_id=(resource_id or str(ctx.tenant_id))[:80],
            actor_id=ctx.user_id,
            actor_name="",
            summary=summary[:500],
            diff_summary=[],
            created_at=audit_now(),
            execution_step_ids=[],
        )
        main.AUDIT_EVENTS[event.id] = event
        _persist_audit(event)
    except Exception:  # noqa: BLE001 审计失败不阻断主流程，但必须可观测
        logger.warning("[tenancy] 成员审计写入失败 tenant=%s action=%s", ctx.tenant_id, action, exc_info=True)


def current_active_tenant_id() -> str | None:
    ctx = current_tenant.get()
    return ctx.tenant_id if ctx else None
