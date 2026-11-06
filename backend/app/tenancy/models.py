"""企业版 E1 多租户数据模型（pydantic，非 ORM）。

ORM 在 app/models/orm.py；此处为接口层 / 内存真源使用的记录模型。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# 企业角色（E1-01 功能边界第 2 条）
EnterpriseRole = Literal[
    "tenant_owner",
    "tenant_admin",
    "security_admin",
    "project_owner",
    "developer",
    "reviewer",
    "auditor",
]

EnterpriseRoleName = str

# 成员生命周期（docs/enterprise/05 §9）
MembershipStatus = Literal["invited", "active", "suspended", "removed"]

DEFAULT_TENANT_ID = "tenant_default"
DEFAULT_ORGANIZATION_ID = "org_default"


class TenantRecord(BaseModel):
    id: str
    name: str
    description: str = ""
    status: str = "active"
    created_at: datetime
    updated_at: datetime


class OrganizationRecord(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str = ""
    created_at: datetime
    updated_at: datetime


class TenantMembershipRecord(BaseModel):
    id: str
    tenant_id: str
    user_id: str
    user_name: str = ""
    organization_id: str | None = None
    role: EnterpriseRole = "developer"
    status: MembershipStatus = "active"
    is_admin: bool = False
    is_active: bool = False
    created_at: datetime
    updated_at: datetime


class TenantMembershipPayload(BaseModel):
    """POST /api/enterprise/members 入参。"""

    user_id: str
    role: EnterpriseRole = "developer"


class TenantMembershipUpdatePayload(BaseModel):
    """PATCH /api/enterprise/members/{id} 入参：可改启用态与角色。"""

    enabled: bool | None = None
    role: EnterpriseRole | None = None


class TenantSwitchPayload(BaseModel):
    tenant_id: str


class TenantCreatePayload(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = ""


class OrganizationCreatePayload(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = ""


class PermissionDecision(BaseModel):
    allow: bool
    role: EnterpriseRoleName | None = None
    missing: str | None = None


class TenantContext(BaseModel):
    """服务端侧的当前租户上下文（存 contextvar，从不信任前端）。"""

    user_id: str
    tenant_id: str
    tenant_name: str = ""
    role: EnterpriseRole = "developer"
    permissions: list[str] = Field(default_factory=list)