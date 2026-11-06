from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.services import repository_inspector


def test_inspector_reads_default_branch_and_node_test_script(monkeypatch, tmp_path):
    calls = []

    def fake_git(args, cwd):
        calls.append(args)
        if args[0] == "ls-remote":
            return "ref: refs/heads/develop\tHEAD\nabc123\tHEAD\n"
        checkout = Path(args[-1])
        checkout.mkdir(parents=True, exist_ok=True)
        (checkout / "package.json").write_text(
            '{"scripts":{"test":"vitest run"}}',
            encoding="utf-8",
        )
        (checkout / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n", encoding="utf-8")
        return ""

    monkeypatch.setattr(repository_inspector, "_git", fake_git)
    result = repository_inspector.inspect_repository_config(
        "https://gitee.com/company/payment-service.git",
    )

    assert result["suggested_payload"]["name"] == "payment-service"
    assert result["suggested_payload"]["default_branch"] == "develop"
    assert result["suggested_payload"]["test_command"] == "pnpm test"
    assert result["suggested_payload"]["command_whitelist"] == ["pnpm test"]
    assert result["sources"]["test_command"] == "package.json > scripts.test"
    assert result["confidence"]["default_branch"] == "high"
    assert calls[0][:3] == ["ls-remote", "--symref", "https://gitee.com/company/payment-service.git"]


def test_inspector_keeps_safe_defaults_when_remote_cannot_be_read(monkeypatch):
    def fail_git(args, cwd):
        raise repository_inspector.RepoMirrorError("remote access denied")

    monkeypatch.setattr(repository_inspector, "_git", fail_git)
    result = repository_inspector.inspect_repository_config("git@gitee.com:company/payment-service.git")

    assert result["suggested_payload"]["name"] == "payment-service"
    assert result["suggested_payload"]["default_branch"] == "main"
    assert result["suggested_payload"]["test_command"] == ""
    assert result["suggested_payload"]["command_whitelist"] == ["pytest", "npm test", "pnpm test"]
    assert result["warnings"]
    assert all("token" not in warning.lower() for warning in result["warnings"])


def test_inspect_endpoint_is_admin_only_and_returns_suggestions(monkeypatch):
    monkeypatch.setattr(
        repository_inspector,
        "inspect_repository_config",
        lambda url, token=None: {
            "suggested_payload": {"name": "payment-service", "url": url, "default_branch": "main"},
            "sources": {"name": "仓库地址 owner/repo"},
            "confidence": {"name": "high"},
            "detected_stack": ["Node.js"],
            "warnings": [],
        },
    )
    admin = TestClient(main.app)
    login = admin.post(
        "/api/auth/login",
        json={"account": "admin@company.com", "password": "Admin123!"},
    )
    assert login.status_code == 200

    response = admin.post(
        "/api/repositories/inspect",
        json={"url": "https://gitee.com/company/payment-service.git", "gitee_token": "temporary-token"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["suggested_payload"]["name"] == "payment-service"
    assert "temporary-token" not in response.text
