"""企业版 RBAC：企业角色 -> 权限集，集中式 authorize / require_tenant_permission。

不推翻团队版 ApiRole / ROLE_PERMISSIONS（见 app/main.py），此处为企业权限适配层；
默认租户下团队版角色仍按原行为工作，租户隔离由 tenancy/context 决定。
"""

from __future__ import annotations

from app.tenancy.models import (
    EnterpriseRole,
    PermissionDecision,
    TenantContext,
)

# 企业权限标识（E1-01 最小集）
TENANT_CONTEXT = "tenant:context"  # 全员可读自己的租户上下文
TENANT_SWITCH = "tenant:switch"  # 已启用成员可切换至所属租户
MEMBER_VIEW = "member:view"
MEMBER_MANAGE = "member:manage"  # 加成员 / 改启用态 / 改角色
ORG_VIEW = "org:view"
ORG_MANAGE = "org:manage"
TENANT_CREATE = "tenant:create"
AUDIT_VIEW = "audit:view"
RAG_SEARCH = "rag:search"
# OPT-08：触发在线评测的专属授权（消费算力 + 落报告；developer/reviewer/auditor 不持有）
RAG_EVALUATE = "rag:evaluate"

# 企业审批与治理权限（E2-01，复用通用命名）
APPROVAL_VIEW = "approval:view"
APPROVAL_SUBMIT = "approval:submit"
APPROVAL_APPROVE = "approval:approve"
APPROVAL_MANAGE = "approval:manage"
GOVERNANCE_MANAGE = "governance:manage"

# 全员基础权限（仅需是已启用成员）
BASE_PERMISSIONS = frozenset({TENANT_CONTEXT, TENANT_SWITCH})

ENT_ROLE_LABELS: dict[EnterpriseRole, str] = {
    "tenant_owner": "租户所有者",
    "tenant_admin": "企业管理员",
    "security_admin": "安全管理员",
    "project_owner": "项目负责人",
    "developer": "开发人员",
    "reviewer": "审查人",
    "auditor": "审计人员",
}

ENT_ROLE_PERMISSIONS: dict[EnterpriseRole, frozenset[str]] = {
    "tenant_owner": frozenset({
        TENANT_CONTEXT, TENANT_SWITCH,
        MEMBER_VIEW, MEMBER_MANAGE, ORG_VIEW, ORG_MANAGE, TENANT_CREATE, AUDIT_VIEW,
        RAG_SEARCH, RAG_EVALUATE,
        APPROVAL_VIEW, APPROVAL_SUBMIT, APPROVAL_APPROVE, APPROVAL_MANAGE, GOVERNANCE_MANAGE,
    }),
    "tenant_admin": frozenset({
        TENANT_CONTEXT, TENANT_SWITCH,
        MEMBER_VIEW, MEMBER_MANAGE, ORG_VIEW, ORG_MANAGE, AUDIT_VIEW,
        RAG_SEARCH, RAG_EVALUATE,
        APPROVAL_VIEW, APPROVAL_SUBMIT, APPROVAL_APPROVE, APPROVAL_MANAGE,
    }),
    "security_admin": frozenset({
        TENANT_CONTEXT, TENANT_SWITCH, MEMBER_VIEW, ORG_VIEW, AUDIT_VIEW,
        RAG_SEARCH, RAG_EVALUATE,
        APPROVAL_VIEW, APPROVAL_APPROVE, APPROVAL_MANAGE, GOVERNANCE_MANAGE,
    }),
    "project_owner": frozenset({
        TENANT_CONTEXT, TENANT_SWITCH, MEMBER_VIEW, ORG_VIEW,
        RAG_SEARCH, RAG_EVALUATE,
        APPROVAL_VIEW, APPROVAL_SUBMIT, APPROVAL_APPROVE,
    }),
    "developer": frozenset({TENANT_CONTEXT, TENANT_SWITCH, RAG_SEARCH, APPROVAL_VIEW, APPROVAL_SUBMIT}),
    "reviewer": frozenset({TENANT_CONTEXT, TENANT_SWITCH, RAG_SEARCH, APPROVAL_VIEW}),
    "auditor": frozenset({TENANT_CONTEXT, TENANT_SWITCH, MEMBER_VIEW, ORG_VIEW, AUDIT_VIEW, APPROVAL_VIEW}),
}


def permissions_for(role: EnterpriseRole) -> list[str]:
    return sorted(BASE_PERMISSIONS | ENT_ROLE_PERMISSIONS[role])


def authorize(context: TenantContext, resource: str, action: str) -> PermissionDecision:
    """RBAC 判定：资源:动作 是否在当前角色权限集内。"""
    permission = f"{resource}:{action}"
    if permission in permissions_for(context.role):
        return PermissionDecision(allow=True, role=context.role)
    return PermissionDecision(allow=False, role=context.role, missing=permission)


def has_permission(context: TenantContext, permission: str) -> bool:
    return permission in permissions_for(context.role)


def require_tenant_permission(context: TenantContext, permission: str) -> None:
    """不满足则抛 403 并记录审计。最终授权由后端完成，前端只做展示。"""
    if has_permission(context, permission):
        return
    from app.tenancy.context import deny_and_audit, forbidden

    deny_and_audit(context, f"tenant.permission.{permission}", "缺少租户权限")
    raise forbidden()
