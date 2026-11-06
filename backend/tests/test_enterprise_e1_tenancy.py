"""企业版 E1-01 多租户 / 组织成员 / RBAC / 项目隔离验收测试。

覆盖规格强制项：
1. 双租户、双项目、多角色隔离；
2. 成员可切换到已加入租户，不能切换未加入租户；
3. 跨租户读取/更新/任务创建/仓库·索引访问均 403；
4. 禁用成员后会话立即失效；
5. 租户管理员 vs 普通成员权限边界；
6. migration 回填 default tenant 与默认团队版兼容；
7. 团队版回归由全量 suite 覆盖。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.tenancy.context import current_tenant, deny_and_audit
from app.tenancy.models import TenantContext


ACCOUNTS = {
    "admin": ("admin@company.com", "Admin123!"),
    "developer": ("developer@company.com", "Dev123!"),
    "observer": ("observer@company.com", "Observer123!"),
}


@pytest.fixture()
def _fresh_client() -> TestClient:
    return TestClient(main.app)


def _login(client: TestClient, who: str) -> TestClient:
    account, password = ACCOUNTS[who]
    resp = client.post("/api/auth/login", json={"account": account, "password": password})
    assert resp.status_code == 200, resp.text
    return client


def create_tenant_on(c: TestClient, name: str) -> str:
    resp = c.post("/api/enterprise/tenants", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def switch_tenant(c: TestClient, tenant_id: str) -> dict:
    resp = c.post("/api/enterprise/tenant-context/switch", json={"tenant_id": tenant_id})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def create_project(c: TestClient, name: str) -> str:
    resp = c.post("/api/projects", json={"name": name, "description": "isolation test project"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def create_repository(c: TestClient, project_id: str, name: str) -> str:
    payload = {
        "project_id": project_id,
        "name": name,
        "url": "https://gitee.com/demo/tenant-b-repo.git",
        "default_branch": "main",
        "test_command": "pytest tests/ -q",
        "command_whitelist": ["pytest"],
        "approval_owner_id": None,
        "approval_mode": "single",
        "risk_threshold": "medium",
        "max_files": 10,
        "max_retries": 1,
        "blocked_paths": [],
    }
    resp = c.post("/api/repositories", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


# ---------------------------------------------------------------------------
# 1. 双租户 / 双项目 / 多角色隔离
# ---------------------------------------------------------------------------


def test_default_tenant_context_and_seed_membership():
    c = _login(TestClient(main.app), "admin")
    data = c.get("/api/enterprise/tenant-context").json()["data"]
    assert data["active_tenant"]["id"] == "tenant_default"
    assert data["role"] == "tenant_owner"
    assert data["tenants"][0]["id"] == "tenant_default"


def test_two_tenant_project_isolation():
    admin = _login(TestClient(main.app), "admin")
    dev = _login(TestClient(main.app), "developer")

    # 管理员建租户 B 并切到 B，在 B 内建项目与仓库
    tenant_b = create_tenant_on(admin, "Isolation Corp")
    switch_tenant(admin, tenant_b)
    assert admin.get("/api/enterprise/tenant-context").json()["data"]["active_tenant"]["id"] == tenant_b
    project_b = create_project(admin, "Drill-B")
    create_repository(admin, project_b, "drill-b-service")
    tenant_b_data = admin.get("/api/projects").json()["data"]
    assert all(p["tenant_id"] == tenant_b for p in tenant_b_data)
    project_ids_b = {p["id"] for p in tenant_b_data}

    # 管理员回到默认租户：应只见默认项目，不见 B 项目
    switch_tenant(admin, "tenant_default")
    admin_default_ids = {p["id"] for p in admin.get("/api/projects").json()["data"]}
    assert "proj_payment" in admin_default_ids
    assert project_b not in admin_default_ids

    # 开发者在默认租户：仅见默认项目，B 项目不可读（403）
    dev_ids = {p["id"] for p in dev.get("/api/projects").json()["data"]}
    assert "proj_payment" in dev_ids
    assert project_b not in dev_ids
    assert dev.get(f"/api/projects/{project_b}").status_code == 403
    assert admin.get(f"/api/projects/{project_b}").status_code == 403  # 管理员在默认租户也读不到 B


# ---------------------------------------------------------------------------
# 2. 租户切换：可切已加入，不可切未加入
# ---------------------------------------------------------------------------


def test_switch_own_tenant_ok_but_not_unjoined():
    admin = _login(TestClient(main.app), "admin")
    tenant_b = create_tenant_on(admin, "Switch Corp")

    # 未加入 B 的开发者不可切换
    dev = _login(TestClient(main.app), "developer")
    assert dev.post("/api/enterprise/tenant-context/switch",
                    json={"tenant_id": tenant_b}).status_code == 403

    # 管理员把开发者加入 B 后即可切换
    switch_tenant(admin, tenant_b)
    dev_user = main.USERS["user_developer"]
    assert admin.post("/api/enterprise/members",
                      json={"user_id": dev_user.id, "role": "developer"}).status_code == 200
    switch_tenant(admin, "tenant_default")

    data = dev.post("/api/enterprise/tenant-context/switch",
                    json={"tenant_id": tenant_b}).json()["data"]
    assert data["active_tenant"]["id"] == tenant_b

    # 切换到不存在的租户 -> 403
    assert dev.post("/api/enterprise/tenant-context/switch",
                    json={"tenant_id": "tenant_does_not_exist"}).status_code == 403


# ---------------------------------------------------------------------------
# 3. 跨租户读取 / 更新 / 任务创建 / 仓库·索引访问均 403
# ---------------------------------------------------------------------------


def test_cross_tenant_resource_403_and_audit():
    admin = _login(TestClient(main.app), "admin")
    dev = _login(TestClient(main.app), "developer")
    tenant_b = create_tenant_on(admin, "Dark Tenancy")
    switch_tenant(admin, tenant_b)
    project_b = create_project(admin, "Project-B")
    repo_b = create_repository(admin, project_b, "repo-b")

    # 管理员留在默认租户，尝试操作 B 的资源 → 403（租户门，非权限门）
    switch_tenant(admin, "tenant_default")
    assert admin.get(f"/api/projects/{project_b}").status_code == 403
    assert admin.put(f"/api/projects/{project_b}",
                     json={"name": "Rename", "description": "x"}).status_code == 403
    assert admin.put(f"/api/repositories/{repo_b}",
                     json={"project_id": project_b, "name": "rename",
                           "url": "https://gitee.com/demo/tenant-b-repo.git",
                           "default_branch": "main", "test_command": "pytest tests/ -q",
                           "command_whitelist": ["pytest"], "approval_owner_id": None,
                           "approval_mode": "single", "risk_threshold": "medium",
                           "max_files": 10, "max_retries": 1, "blocked_paths": []}).status_code == 403
    assert admin.post(f"/api/repositories/{repo_b}/index", json={}).status_code == 403

    # 跨租户任务创建：开发者(默认)引用 B 项目 → 403
    assert dev.post("/api/tasks",
                    json={"project_id": project_b, "repository_id": repo_b,
                          "issue_id": "1", "title": "cross-tenant task"}).status_code == 403

    # 跨租户仓库/索引访问 → 403
    assert dev.get(f"/api/repositories/{repo_b}/indexes").status_code == 403
    assert dev.get(f"/api/repositories/{repo_b}/indexes/index_x").status_code == 403

    # 每次越权都应留下不含敏感信息的租户审计事件
    denies = [e for e in main.AUDIT_EVENTS.values()
              if e.event_type and e.event_type.startswith("tenant.")]
    assert denies, "跨租户越权未写入租户审计事件"


# ---------------------------------------------------------------------------
# 4. 禁用成员后会话立即失效
# ---------------------------------------------------------------------------


def test_disable_member_blocks_next_request():
    admin = _login(TestClient(main.app), "admin")
    dev = _login(TestClient(main.app), "developer")
    dev_id = main.USERS["user_developer"].id

    members = {m["user_id"]: m["id"] for m in admin.get("/api/enterprise/members").json()["data"]}
    membership_id = members[dev_id]

    resp = admin.patch(f"/api/enterprise/members/{membership_id}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "suspended"

    # 开发者下一个请求立即 401
    assert dev.get("/api/projects").status_code == 401
    # 也无法切换租户
    assert dev.post("/api/enterprise/tenant-context/switch",
                    json={"tenant_id": "tenant_default"}).status_code == 401


# ---------------------------------------------------------------------------
# 5. 租户管理员 vs 普通成员权限边界
# ---------------------------------------------------------------------------


def test_tenant_admin_vs_regular_member_permission_boundary():
    admin = _login(TestClient(main.app), "admin")
    dev = _login(TestClient(main.app), "developer")

    # 普通成员（developer）不能管理成员、组织、创建租户
    assert dev.post("/api/enterprise/members",
                    json={"user_id": "user_observer", "role": "reviewer"}).status_code == 403
    assert dev.post("/api/enterprise/organizations", json={"name": "intruder-org"}).status_code == 403
    assert dev.post("/api/enterprise/tenants", json={"name": "unprivileged"}).status_code == 403

    # 管理员可以添加成员并创建组织
    assert admin.post("/api/enterprise/members",
                      json={"user_id": "user_observer", "role": "reviewer"}).status_code == 200
    assert admin.post("/api/enterprise/organizations", json={"name": "R&D"}).status_code == 200

    # 最后一名租户管理员不允许被降级/禁用
    members = {m["user_id"]: m["id"] for m in admin.get("/api/enterprise/members").json()["data"]}
    owner_membership = members["user_admin"]
    assert admin.patch(f"/api/enterprise/members/{owner_membership}",
                       json={"role": "developer"}).status_code == 422
    assert admin.patch(f"/api/enterprise/members/{owner_membership}",
                       json={"enabled": False}).status_code == 422


# ---------------------------------------------------------------------------
# 6. 组织创建/查询幂等
# ---------------------------------------------------------------------------


def test_organization_create_and_list():
    admin = _login(TestClient(main.app), "admin")
    assert admin.post("/api/enterprise/organizations",
                      json={"name": "Finance"}).status_code == 200
    # 同租户同名组织幂等返回已有
    dup = admin.post("/api/enterprise/organizations", json={"name": "Finance"})
    assert dup.status_code == 200
    names = {o["name"] for o in admin.get("/api/enterprise/organizations").json()["data"]}
    assert "Finance" in names


# ---------------------------------------------------------------------------
# 7. H1 回归：auditor 的 audit:view 必须真正生效（审计列表为企业可见）
# ---------------------------------------------------------------------------


def test_auditor_can_view_audit_list_but_regular_member_cannot():
    # 观察者种子角色 = 企业 auditor（具备 audit:view）→ 可访问审计列表
    observer = _login(TestClient(main.app), "observer")
    assert observer.get("/api/audit-events").status_code == 200

    # 开发者种子角色 = 企业 developer（无 audit:view）→ 403
    dev = _login(TestClient(main.app), "developer")
    assert dev.get("/api/audit-events").status_code == 403


def test_denied_audit_event_keeps_the_active_tenant_id():
    ctx = TenantContext(
        user_id="user_developer",
        tenant_id="tenant_isolation_b",
        tenant_name="Isolation B",
        role="developer",
        permissions=[],
    )
    token = current_tenant.set(ctx)
    try:
        deny_and_audit(
            ctx,
            "tenant.resource.project",
            "跨租户资源访问被拒绝",
            resource_type="project",
            resource_id="proj_b",
        )
    finally:
        current_tenant.reset(token)

    event = next(event for event in main.AUDIT_EVENTS.values() if event.resource_id == "proj_b")
    assert event.tenant_id == "tenant_isolation_b"
