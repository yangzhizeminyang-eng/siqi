"""企业版 E1-01 租户/组织/成员/RBAC 接口（/api/enterprise）。

所有端点：服务端从会话解析激活租户（见 main.get_current_user -> tenancy.context），
最终授权由 require_tenant_permission 完成后端判定；前端只展示。写操作幂等 + 审计。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, status

from app.tenancy import store
from app.tenancy.context import (
    audit_member_action,
    context_for_current_user,
    forbidden,
)
from app.tenancy.models import (
    OrganizationCreatePayload,
    TenantCreatePayload,
    TenantMembershipPayload,
    TenantMembershipUpdatePayload,
    TenantSwitchPayload,
)
from app.tenancy.permissions import (
    MEMBER_MANAGE,
    MEMBER_VIEW,
    ORG_MANAGE,
    ORG_VIEW,
    ENT_ROLE_LABELS,
    TENANT_CREATE,
    require_tenant_permission,
)

router = APIRouter(prefix="/api/enterprise", tags=["enterprise"])


def _current_user_dependency(agent_session: Annotated[str | None, Cookie()] = None):
    """复用 main.get_current_user（含服务端解析激活租户并写入 contextvar）。"""
    from app import main  # 延迟导入避免循环

    return main.get_current_user(agent_session)


CurrentUser = Annotated[object, Depends(_current_user_dependency)]


def _ok(data=None, message: str = "success") -> dict:
    return {"code": 0, "message": message, "data": data}


def _req_fail(code: int, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": code, "message": message, "data": None})


def _member_payload(m) -> dict:
    base = m.model_dump(mode="json", exclude={"created_at", "updated_at"})
    return {
        **base,
        "role_label": ENT_ROLE_LABELS.get(m.role, m.role),
    }


def _ctx_payload(user_id: str, ctx) -> dict:
    tenants = store.joined_tenants(user_id)
    return {
        "active_tenant": {
            "id": ctx.tenant_id,
            "name": ctx.tenant_name,
            "role": ctx.role,
            "role_label": ENT_ROLE_LABELS.get(ctx.role, ctx.role),
        },
        "tenants": tenants,
        "role": ctx.role,
        "role_label": ENT_ROLE_LABELS.get(ctx.role, ctx.role),
        "permissions": ctx.permissions,
    }


@router.get("/tenant-context")
def get_tenant_context(user: CurrentUser):
    ctx = context_for_current_user()
    return _ok(_ctx_payload(user.id, ctx))


@router.post("/tenant-context/switch")
def switch_tenant(payload: TenantSwitchPayload, user: CurrentUser):
    ctx = context_for_current_user()
    membership = store.membership_of(user.id, payload.tenant_id)
    # 仅允许已启用成员切换至其所属租户：未加入或已禁用 → 403
    if membership is None:
        raise _req_fail(403, "你不是该租户的成员")
    if membership.status != "active":
        raise _req_fail(403, "该租户成员已被禁用，无法切换")
    store.set_active_membership(user.id, payload.tenant_id)
    audit_member_action(ctx, "tenant.switch", payload.tenant_id, f"切换激活租户 -> {payload.tenant_id}")
    switch_ctx = _rebuild_ctx(user.id)
    return _ok(_ctx_payload(user.id, switch_ctx), "已切换租户")


def _rebuild_ctx(user_id: str):
    from app.tenancy.context import build_context

    membership = store.active_membership_for_user(user_id)
    if membership is None:
        raise _req_fail(401, "账号未加入任何可用租户或已被禁用")
    return build_context(membership)


@router.get("/members")
def list_members(user: CurrentUser):
    ctx = context_for_current_user()
    require_tenant_permission(ctx, MEMBER_VIEW)
    members = [_member_payload(m) for m in store.members_of_tenant(ctx.tenant_id)]
    return _ok(members)


@router.post("/members")
def create_member(payload: TenantMembershipPayload, user: CurrentUser):
    ctx = context_for_current_user()
    require_tenant_permission(ctx, MEMBER_MANAGE)
    # 特权角色铸造仅限租户所有者（与 PATCH 对齐，封堵「非 owner 铸造 security_admin」缺口）
    _guard_privileged_role_create(ctx, payload.role)
    from app import main  # 延迟 import

    target = main.USERS.get(payload.user_id)
    if target is None:
        raise _req_fail(422, "用户不存在")
    membership = store.add_member(
        ctx.tenant_id, target.id, target.name, payload.role,
        organization_id=None,
    )
    audit_member_action(ctx, "member.add", membership.id, f"添加成员 {target.name} 角色={payload.role}")
    return _ok(_member_payload(membership), "已添加成员")


def _guard_privileged_role_create(ctx, role) -> None:
    """角色绑定为特权角色时仅限租户所有者。"""
    if role in _PRIVILEGED_ROLES and getattr(ctx, "role", "") != "tenant_owner":
        raise _req_fail(403, "特权角色（租户所有者/管理员/安全管理员）的授予仅限租户所有者")


@router.patch("/members/{membership_id}")
def update_member(membership_id: str, payload: TenantMembershipUpdatePayload, user: CurrentUser):
    ctx = context_for_current_user()
    require_tenant_permission(ctx, MEMBER_MANAGE)
    membership = store.get_membership(membership_id)
    if membership is None or membership.tenant_id != ctx.tenant_id:
        raise _req_fail(403, "成员不存在或不属于当前租户")
    if payload.enabled is False and membership.user_id == ctx.user_id:
        raise _req_fail(422, "不能禁用当前登录成员")
    # 治理门禁：特权角色（租户所有者/管理员/安全管理员）的授予、降级或启停仅限租户所有者。
    # 普通成员角色管理由 tenant_admin/owner（member:manage）承担；特权变化属高权限动作,强审计。
    _guard_privileged_roles(ctx, membership, payload)
    # 单一 owner/admin 不允许被禁用或降级，否则租户失控（守卫在变更前按原角色判断）
    _guard_admin_roll(membership, ctx.tenant_id, payload)
    before_role = membership.role
    before_status = membership.status
    if payload.role is not None:
        membership.role = payload.role
    if payload.enabled is not None:
        membership.status = "active" if payload.enabled else "suspended"
        if not payload.enabled:
            membership.is_active = False
    membership.updated_at = store._now()
    store._persist_membership(membership)
    actor_role = getattr(ctx, "role", "")
    if payload.role is not None and payload.role != before_role:
        privileged = payload.role in _PRIVILEGED_ROLES or before_role in _PRIVILEGED_ROLES
        audit_member_action(ctx, "member.role_changed", membership_id,
                            f"成员角色变更 {before_role} -> {payload.role}"
                            f"{'（特权角色）' if privileged else ''}（操作者 {actor_role}）")
    if payload.enabled is not None and membership.status != before_status:
        audit_member_action(ctx, "member.status_changed", membership_id,
                            f"成员状态变更 {before_status} -> {membership.status}（操作者 {actor_role}）")
    audit_member_action(ctx, "member.update", membership_id,
                        f"更新成员 {membership.user_name} enabled={payload.enabled} role={payload.role}")
    return _ok(_member_payload(membership), "已更新成员")


_PRIVILEGED_ROLES = frozenset({"tenant_owner", "tenant_admin", "security_admin"})


def _guard_privileged_roles(ctx, membership, payload) -> None:
    """特权角色授予/降级/启停仅限租户所有者，杜绝任意 member:manage 授予 security_admin 等旁路。"""
    if getattr(ctx, "role", None) == "tenant_owner":
        return
    touches_privileged = (
        (payload.role is not None and (payload.role in _PRIVILEGED_ROLES or membership.role in _PRIVILEGED_ROLES))
        or (payload.enabled is not None and membership.role in _PRIVILEGED_ROLES)
    )
    if touches_privileged:
        raise _req_fail(403, "特权角色（租户所有者/管理员/安全管理员）的授予、降级或启停仅限租户所有者")


def _guard_admin_roll(membership, tenant_id: str, payload) -> None:
    if membership.role not in {"tenant_owner", "tenant_admin", "security_admin"}:
        return
    active_admins = [m for m in store.members_of_tenant(tenant_id)
                     if m.role in {"tenant_owner", "tenant_admin"}
                     and m.status == "active"]
    only_one = len(active_admins) <= 1
    if not only_one:
        return
    downgrade = payload.role is not None and payload.role not in {"tenant_owner", "tenant_admin"}
    disable = payload.enabled is False
    if downgrade or disable:
        if membership.user_id in {m.user_id for m in active_admins}:
            raise _req_fail(422, "租户必须保留至少一名启用的租户管理员")


def stream_of(x):
    return getattr(x, "id", str(x))


@router.get("/organizations")
def list_organizations(user: CurrentUser):
    ctx = context_for_current_user()
    require_tenant_permission(ctx, ORG_VIEW)
    orgs = [o.model_dump(mode="json", exclude={"created_at", "updated_at"}) for o in store.organizations_of_tenant(ctx.tenant_id)]
    return _ok(orgs)


@router.post("/organizations")
def create_organization(payload: OrganizationCreatePayload, user: CurrentUser):
    ctx = context_for_current_user()
    require_tenant_permission(ctx, ORG_MANAGE)
    existing = next((o for o in store.organizations_of_tenant(ctx.tenant_id) if o.name == payload.name), None)
    if existing is not None:
        return _ok(existing.model_dump(mode="json", exclude={"created_at", "updated_at"}), "组织已存在")
    org_id = store.next_enterprise_id("org")
    org = store.create_organization(ctx.tenant_id, org_id, payload.name, payload.description)
    audit_member_action(ctx, "org.create", org.id, f"创建组织 {payload.name}")
    return _ok(org.model_dump(mode="json", exclude={"created_at", "updated_at"}), "已创建组织")


@router.post("/tenants")
def create_tenant(payload: TenantCreatePayload, user: CurrentUser):
    ctx = context_for_current_user()
    require_tenant_permission(ctx, TENANT_CREATE)
    existing = next((t for t in store.TENANTS.values() if t.name == payload.name), None)
    if existing is not None:
        return _ok({"id": existing.id, "name": existing.name}, "租户已存在")
    tenant = store.create_tenant(store.next_enterprise_id("tenant"), payload.name, payload.description)
    org = store.create_organization(tenant.id, store.next_enterprise_id("org"), "默认组织", "")
    # 创建者成为新租户的所有者并激活
    creator = store.add_member(tenant.id, ctx.user_id, "", "tenant_owner", organization_id=org.id)
    creator.user_name = getattr(user, "name", "")
    creator.is_active = True
    store._persist_membership(creator)
    audit_member_action(ctx, "tenant.create", tenant.id, f"创建租户 {payload.name}")
    return _ok({"id": tenant.id, "name": tenant.name, "organization_id": org.id}, "已创建租户")