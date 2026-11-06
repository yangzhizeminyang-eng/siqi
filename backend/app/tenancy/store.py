"""企业版租户/组织/成员关系的内存真源（与团队版 USERS/PROJECTS dict 同款模式）。

USE_SQLALCHEMY 关闭（测试）时只用 dict；开启时通过 repositories.py 的
load_*/upsert_* 写透/恢复（由 bootstrap 重灌）。所有写操作幂等且记审计（调用方）。

服务端侧的「激活租户」以成员行的 is_active 落库/内存保存，前端不可写；
切换租户仅改写本用户各成员关系的 is_active 位。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from app.tenancy.models import (
    DEFAULT_ORGANIZATION_ID,
    DEFAULT_TENANT_ID,
    EnterpriseRole,
    OrganizationRecord,
    TenantMembershipRecord,
    TenantRecord,
)

# 团队版 ApiRole -> 默认租户下的初始企业角色（兼容适配：不推翻团队版角色）
_TEAM_ROLE_TO_ENT: dict[str, EnterpriseRole] = {
    "admin": "tenant_owner",
    "lead": "project_owner",
    "approver": "reviewer",
    "developer": "developer",
    "observer": "auditor",
}

TENANTS: dict[str, TenantRecord] = {}
ORGANIZATIONS: dict[str, OrganizationRecord] = {}
MEMBERSHIPS: dict[str, TenantMembershipRecord] = {}

_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reset_store() -> None:
    TENANTS.clear()
    ORGANIZATIONS.clear()
    MEMBERSHIPS.clear()


def _persist_tenant(record: TenantRecord) -> None:
    try:
        from app.db.repositories import persistence_enabled, upsert_tenant

        if persistence_enabled():
            upsert_tenant(record)
    except Exception:  # noqa: BLE001 持久化失败不影响内存真源
        pass


def _persist_organization(record: OrganizationRecord) -> None:
    try:
        from app.db.repositories import persistence_enabled, upsert_organization

        if persistence_enabled():
            upsert_organization(record)
    except Exception:  # noqa: BLE001
        pass


def _persist_membership(record: TenantMembershipRecord) -> None:
    try:
        from app.db.repositories import persistence_enabled, upsert_membership

        if persistence_enabled():
            upsert_membership(record)
    except Exception:  # noqa: BLE001
        pass


def _ensure_seed() -> None:
    """DB 空 / 测试内存时幂等种下默认租户 + 默认组织 + 5 个种子用户成员关系。"""
    if MEMBERSHIPS:
        return
    with _lock:
        if MEMBERSHIPS:
            return
        from app import main

        created = _now()
        TENANTS[DEFAULT_TENANT_ID] = TenantRecord(
            id=DEFAULT_TENANT_ID, name="默认租户", created_at=created, updated_at=created
        )
        ORGANIZATIONS[DEFAULT_ORGANIZATION_ID] = OrganizationRecord(
            id=DEFAULT_ORGANIZATION_ID, tenant_id=DEFAULT_TENANT_ID,
            name="默认组织", created_at=created, updated_at=created,
        )
        for user in main.USERS.values():
            role = _TEAM_ROLE_TO_ENT.get(user.role, "developer")
            membership = TenantMembershipRecord(
                id=f"mem_{user.id}_default",
                tenant_id=DEFAULT_TENANT_ID,
                user_id=user.id,
                user_name=user.name,
                organization_id=DEFAULT_ORGANIZATION_ID,
                role=role,
                is_active=True,  # 每个种子用户激活于默认租户
                created_at=created,
                updated_at=created,
            )
            MEMBERSHIPS[membership.id] = membership
        _persist_default_seed()


def _persist_default_seed() -> None:
    """持久化默认租户/组织/成员种子（首次建库写透，重启后由 bootstrap 读回）。"""
    for tenant in TENANTS.values():
        _persist_tenant(tenant)
    for org in ORGANIZATIONS.values():
        _persist_organization(org)
    for membership in MEMBERSHIPS.values():
        _persist_membership(membership)


def membership_of(user_id: str, tenant_id: str) -> TenantMembershipRecord | None:
    _ensure_seed()
    return next((m for m in MEMBERSHIPS.values()
                 if m.user_id == user_id and m.tenant_id == tenant_id), None)


def active_membership_for_user(user_id: str) -> TenantMembershipRecord | None:
    """返回用户当前激活（is_active 且 active）的成员关系；无则 None。

    单成员用户自动激活；多成员用户以 is_active 位为准（服务端状态）。
    """
    _ensure_seed()
    active = [m for m in MEMBERSHIPS.values()
              if m.user_id == user_id and m.status == "active"]
    if not active:
        return None
    chosen = next((m for m in active if m.is_active), None)
    if chosen is not None:
        return chosen
    if len(active) == 1:
        return active[0]
    # 多租户但尚无激活位：默认首条，避免阻断
    return active[0]


def memberships_of_user(user_id: str) -> list[TenantMembershipRecord]:
    _ensure_seed()
    return sorted(
        (m for m in MEMBERSHIPS.values() if m.user_id == user_id),
        key=lambda m: m.created_at,
    )


def joined_tenants(user_id: str) -> list[dict]:
    """用户可切换的（已启用）租户列表：{id,name,role,status}。"""
    out = []
    for m in memberships_of_user(user_id):
        tenant = TENANTS.get(m.tenant_id)
        out.append({
            "id": m.tenant_id,
            "name": tenant.name if tenant else m.tenant_id,
            "role": m.role,
            "status": m.status,
        })
    return out


def members_of_tenant(tenant_id: str) -> list[TenantMembershipRecord]:
    _ensure_seed()
    return sorted(
        (m for m in MEMBERSHIPS.values() if m.tenant_id == tenant_id),
        key=lambda m: m.created_at,
    )


def get_membership(membership_id: str) -> TenantMembershipRecord | None:
    _ensure_seed()
    return MEMBERSHIPS.get(membership_id)


def get_tenant(tenant_id: str) -> TenantRecord | None:
    _ensure_seed()
    return TENANTS.get(tenant_id)


def organizations_of_tenant(tenant_id: str) -> list[OrganizationRecord]:
    _ensure_seed()
    return sorted(
        (o for o in ORGANIZATIONS.values() if o.tenant_id == tenant_id),
        key=lambda o: o.created_at,
    )


def create_tenant(tenant_id: str, name: str, description: str = "") -> TenantRecord:
    _ensure_seed()
    created = _now()
    record = TenantRecord(
        id=tenant_id, name=name, description=description,
        created_at=created, updated_at=created,
    )
    TENANTS[record.id] = record
    _persist_tenant(record)
    return record


def create_organization(tenant_id: str, org_id: str, name: str, description: str = "") -> OrganizationRecord:
    _ensure_seed()
    created = _now()
    record = OrganizationRecord(
        id=org_id, tenant_id=tenant_id, name=name, description=description,
        created_at=created, updated_at=created,
    )
    ORGANIZATIONS[record.id] = record
    _persist_organization(record)
    return record


def add_member(tenant_id: str, user_id: str, user_name: str, role: EnterpriseRole,
               organization_id: str | None = None) -> TenantMembershipRecord:
    """幂等添加成员：已存在同 (tenant,user) 则返回现有记录。"""
    _ensure_seed()
    existing = next((m for m in MEMBERSHIPS.values()
                     if m.tenant_id == tenant_id and m.user_id == user_id), None)
    if existing is not None:
        return existing
    created = _now()
    record = TenantMembershipRecord(
        id=f"mem_{tenant_id}_{user_id}",
        tenant_id=tenant_id,
        user_id=user_id,
        user_name=user_name,
        organization_id=organization_id,
        role=role,
        is_active=False,
        created_at=created,
        updated_at=created,
    )
    MEMBERSHIPS[record.id] = record
    _persist_membership(record)
    return record


def update_membership(membership_id: str, *, enabled: bool | None = None,
                      role: EnterpriseRole | None = None) -> TenantMembershipRecord:
    _ensure_seed()
    record = MEMBERSHIPS[membership_id]
    now = _now()
    if enabled is not None:
        record.status = "active" if enabled else "suspended"
    if role is not None:
        record.role = role
    record.updated_at = now
    if record.status != "active":
        record.is_active = False  # 禁用立即失效（含切租户）
    _persist_membership(record)
    return record


def set_active_membership(user_id: str, tenant_id: str) -> TenantMembershipRecord:
    """切换激活租户：调用方必须已校验该用户是该租户 active 成员。

    仅改写本用户各成员关系的 is_active 位（服务端状态）。
    """
    _ensure_seed()
    membership = membership_of(user_id, tenant_id)
    if membership is None:
        raise KeyError(tenant_id)
    if membership.status != "active":
        raise PermissionError(f"membership disabled: {tenant_id}")
    for m in MEMBERSHIPS.values():
        if m.user_id == user_id:
            if m.id == membership.id:
                m.is_active = True
            else:
                m.is_active = m.status == "active" and False
            _persist_membership(m)
    return membership


def next_enterprise_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def restore_from_db() -> None:
    """启动时从 DB（真源）恢复租户/组织/成员关系；DB 空则种子默认租户。"""
    try:
        from app.db.repositories import (
            persistence_enabled,
            load_memberships,
            load_organizations,
            load_tenants,
        )
    except Exception:  # noqa: BLE001
        return
    if not persistence_enabled():
        _ensure_seed()
        return
    try:
        tenants, orgs, memberships = load_tenants(), load_organizations(), load_memberships()
    except Exception:  # noqa: BLE001
        _ensure_seed()
        return
    TENANTS.clear()
    ORGANIZATIONS.clear()
    MEMBERSHIPS.clear()
    for t in tenants:
        TENANTS[t["id"]] = TenantRecord(**t)
    for o in orgs:
        ORGANIZATIONS[o["id"]] = OrganizationRecord(**o)
    for m in memberships:
        MEMBERSHIPS[m["id"]] = TenantMembershipRecord(**m)
    if not MEMBERSHIPS:
        _ensure_seed()