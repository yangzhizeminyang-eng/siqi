"""OPT-14 企业租户隔离与权限回归（结构冻结 + 跨租户 + 认证头）：

1. ORM 全表租户分类冻结：direct（自带 tenant_id）/ indirect（经父实体外键隔离）/
   global（身份与租户目录）——新增表必须显式分类，否则测试失败（防止悄悄引入无隔离表）；
2. 企业角色权限矩阵冻结（ENT_ROLE_PERMISSIONS 逐角色逐权限对拍）；
3. 跨租户审计隔离：租户 B 内产生的事件，在默认租户的审计列表与导出中不可见；
4. 认证边界：伪造身份请求头（X-Auth-Request-Email / Remote-User 等）不得绕过本地会话。

TDD：本文件先于实现落地（红）。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app import main

ACCOUNTS = {
    "admin": ("admin@company.com", "Admin123!"),
    "developer": ("developer@company.com", "Dev123!"),
}


def _login(who: str) -> TestClient:
    client = TestClient(main.app)
    account, password = ACCOUNTS[who]
    resp = client.post("/api/auth/login", json={"account": account, "password": password})
    assert resp.status_code == 200, resp.text
    return client


# ---------------------------------------------------------------------------
# 1. ORM 租户分类冻结
# ---------------------------------------------------------------------------

DIRECT_TENANT_TABLES = {
    "approval_applications",
    "audit_events",
    "code_chunks",
    "enterprise_agent_artifacts",
    "enterprise_agent_runs",
    "enterprise_rag_evaluation_runs",
    "enterprise_rag_index_lifecycles",
    "enterprise_runtime_checkpoints",
    "enterprise_runtime_tasks",
    "enterprise_runtime_trace_spans",
    "organizations",
    "projects",
    "repositories",
    "repository_indexes",
    "retrieval_records",
    "tasks",
    "team_requests",
    "tenant_memberships",
}

# 经父实体外键隔离（父实体带租户或属全局身份目录；读路径统一经父实体收口）
INDIRECT_TENANT_TABLES = {
    "agent_checkpoints": "task_id / runtime task 作用域",
    "comments": "task_id 作用域",
    "diffs": "task_id 作用域",
    "execution_steps": "task_id 作用域",
    "historical_task_indexes": "任务历史摘要索引（全局检索，任务本身带租户）",
    "historical_task_summaries": "task_id 作用域",
    "index_snapshots": "repository_id 作用域",
    "notifications": "user_id 作用域（全局身份目录）",
    "password_resets": "user_id 作用域（全局身份目录）",
    "plans": "task_id 作用域",
    "pr_descriptions": "task_id 作用域",
    "task_events": "task_id 作用域",
    "test_runs": "task_id 作用域",
}

# 全局目录：租户注册表本身与用户身份目录，不参与租户过滤
GLOBAL_TABLES = {"tenants", "users"}


def test_orm_tenant_classification_is_frozen_and_consistent():
    from app.models.orm import Base

    tables = set(Base.metadata.tables)
    classified = DIRECT_TENANT_TABLES | set(INDIRECT_TENANT_TABLES) | GLOBAL_TABLES
    assert tables == classified, (
        "ORM 表租户分类漂移：新增/删除表必须在本测试显式分类，"
        f"未分类={sorted(tables - classified)} 已失效={sorted(classified - tables)}"
    )

    for name in sorted(DIRECT_TENANT_TABLES):
        assert "tenant_id" in Base.metadata.tables[name].columns, f"{name} 直挂租户列缺失"
    for name in sorted(INDIRECT_TENANT_TABLES):
        assert "tenant_id" not in Base.metadata.tables[name].columns, (
            f"{name} 分类为间接隔离，不应假装自带 tenant_id（需更新分类说明）"
        )
    for name in sorted(GLOBAL_TABLES):
        assert "tenant_id" not in Base.metadata.tables[name].columns, f"{name} 为全局目录表"


# ---------------------------------------------------------------------------
# 2. 企业角色权限矩阵冻结
# ---------------------------------------------------------------------------

EXPECTED_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "tenant_owner": {
        "tenant:context", "tenant:switch", "tenant:create",
        "member:view", "member:manage", "org:view", "org:manage", "audit:view",
        "rag:search", "rag:evaluate",
        "approval:view", "approval:submit", "approval:approve", "approval:manage", "governance:manage",
    },
    "tenant_admin": {
        "tenant:context", "tenant:switch",
        "member:view", "member:manage", "org:view", "org:manage", "audit:view",
        "rag:search", "rag:evaluate",
        "approval:view", "approval:submit", "approval:approve", "approval:manage",
    },
    "security_admin": {
        "tenant:context", "tenant:switch",
        "member:view", "org:view", "audit:view",
        "rag:search", "rag:evaluate",
        "approval:view", "approval:approve", "approval:manage", "governance:manage",
    },
    "project_owner": {
        "tenant:context", "tenant:switch",
        "member:view", "org:view",
        "rag:search", "rag:evaluate",
        "approval:view", "approval:submit", "approval:approve",
    },
    "developer": {
        "tenant:context", "tenant:switch",
        "rag:search",
        "approval:view", "approval:submit",
    },
    "reviewer": {
        "tenant:context", "tenant:switch",
        "rag:search",
        "approval:view",
    },
    "auditor": {
        "tenant:context", "tenant:switch",
        "member:view", "org:view", "audit:view",
        "approval:view",
    },
}


def test_enterprise_role_permission_matrix_is_frozen():
    from app.tenancy.permissions import ENT_ROLE_PERMISSIONS

    current = {role: set(permissions) for role, permissions in ENT_ROLE_PERMISSIONS.items()}
    assert current == EXPECTED_ROLE_PERMISSIONS, (
        "企业角色权限矩阵漂移（任何调整都必须显式改此冻结表并复核越权影响）"
    )


def test_permission_matrix_gates_audit_view_consistently():
    from app.tenancy.models import TenantContext as Ctx
    from app.tenancy.permissions import has_permission

    for role, expected in EXPECTED_ROLE_PERMISSIONS.items():
        ctx = Ctx(user_id="u_x", tenant_id="tenant_x", tenant_name="T", role=role, permissions=[])
        assert has_permission(ctx, "audit:view") is ("audit:view" in expected), role


# ---------------------------------------------------------------------------
# 3. 跨租户审计隔离（列表 + 导出）
# ---------------------------------------------------------------------------


def test_audit_list_and_export_are_tenant_scoped():
    admin = _login("admin")

    # 建租户 B 并切换；在 B 内访问默认租户项目 → 403 且审计事件落在 B
    resp = admin.post("/api/enterprise/tenants", json={"name": "OPT14 Isolation Corp"})
    assert resp.status_code == 200, resp.text
    tenant_b = resp.json()["data"]["id"]
    switch = admin.post("/api/enterprise/tenant-context/switch", json={"tenant_id": tenant_b})
    assert switch.status_code == 200, switch.text
    assert admin.get("/api/projects/proj_payment").status_code == 403

    b_events = admin.get("/api/audit-events").json()["data"]
    b_ids = {e["id"] for e in b_events if e["tenant_id"] == tenant_b}
    assert b_ids, "租户 B 内的越权拒绝必须写入 B 的审计"

    # 回到默认租户：列表与导出均不得出现 B 的事件
    back = admin.post("/api/enterprise/tenant-context/switch", json={"tenant_id": "tenant_default"})
    assert back.status_code == 200, back.text

    default_events = admin.get("/api/audit-events").json()["data"]
    assert default_events, "默认租户应有审计事件"
    assert all(e["tenant_id"] == "tenant_default" for e in default_events)
    assert b_ids.isdisjoint({e["id"] for e in default_events})

    export = admin.get("/api/audit-events/export")
    assert export.status_code == 200, export.text
    rows = [json.loads(line) for line in export.text.strip().splitlines() if line]
    assert rows, "导出应有默认租户事件"
    assert {row["tenant_id"] for row in rows} <= {"tenant_default"}
    assert b_ids.isdisjoint({row["id"] for row in rows}), "跨租户事件不得从导出泄漏"


# ---------------------------------------------------------------------------
# 4. 认证头伪造防护
# ---------------------------------------------------------------------------


def test_spoofed_identity_headers_do_not_bypass_local_auth():
    spoof = {
        "X-Auth-Request-Email": "admin@company.com",
        "X-Forwarded-User": "user_admin",
        "X-Remote-User": "user_admin",
        "Remote-User": "user_admin",
        "X-User": "user_admin",
    }
    anon = TestClient(main.app)
    assert anon.get("/api/auth/me", headers=spoof).status_code == 401
    assert anon.get("/api/audit-events", headers=spoof).status_code == 401

    dev = _login("developer")
    me = dev.get("/api/auth/me", headers=spoof)
    assert me.status_code == 200
    assert me.json()["data"]["id"] == "user_developer", "身份必须来自会话，而不是可伪造的请求头"
