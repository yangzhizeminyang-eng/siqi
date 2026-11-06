"""企业版 E5-01 运维/设置状态接口测试（非敏感摘要 + 鉴权）。"""

from fastapi.testclient import TestClient

from app import main
from app.tenancy import store as tenancy_store


def _client(who: str) -> TestClient:
    accounts = {
        "admin": ("admin@company.com", "Admin123!"),
        "observer": ("observer@company.com", "Observer123!"),
        "developer": ("developer@company.com", "Dev123!"),
    }
    c = TestClient(main.app)
    email, pwd = accounts[who]
    assert c.post("/api/auth/login", json={"account": email, "password": pwd}).status_code == 200
    return c


def test_ops_status_authenticated_non_sensitive():
    c = _client("admin")
    r = c.get("/api/enterprise/ops/status")
    assert r.status_code == 200
    data = r.json()["data"]
    assert "tenant_id" in data and "app_env" in data
    assert "rag" in data and "milvus" in data and "migration" in data
    # 非敏感：milvus 只暴露 host，不回显完整连接串/密钥
    assert "://" not in data["milvus"]["host"] or ":" not in data["milvus"]["host"]
    assert "api_key" not in str(data).lower() and "token" not in str(data).lower() and "password" not in str(data).lower()


def test_ops_status_requires_login():
    c = TestClient(main.app)
    assert c.get("/api/enterprise/ops/status").status_code == 401


def test_ops_status_requires_enterprise_org_management():
    observer = _client("observer")
    assert observer.get("/api/enterprise/ops/status").status_code == 403


def test_ops_status_allows_tenant_admin_and_returns_tenant_scoped_data():
    admin = _client("admin")
    developer = _client("developer")
    membership_id = "mem_user_developer_default"
    original = tenancy_store.get_membership(membership_id)
    assert original is not None
    original_role = original.role
    try:
        tenancy_store.update_membership(membership_id, role="tenant_admin")
        response = developer.get("/api/enterprise/ops/status")
        assert response.status_code == 200
        assert response.json()["data"]["tenant_id"] == "tenant_default"
    finally:
        tenancy_store.update_membership(membership_id, role=original_role)
