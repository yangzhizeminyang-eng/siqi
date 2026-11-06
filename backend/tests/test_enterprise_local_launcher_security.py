from __future__ import annotations

from pathlib import Path

import pytest

from scripts import init_mysql


def test_enterprise_database_url_targets_only_local_enterprise_database():
    assert hasattr(init_mysql, "enterprise_database_url")
    transform = init_mysql.enterprise_database_url

    assert transform(
        "mysql+pymysql://demo:secret@127.0.0.1:3306/agent_team"
    ) == "mysql+pymysql://demo:secret@127.0.0.1:3306/agent_enterprise"
    assert transform(
        "mysql+pymysql://demo:secret@localhost:3306/agent_team?charset=utf8mb4"
    ) == "mysql+pymysql://demo:secret@localhost:3306/agent_enterprise?charset=utf8mb4"
    assert transform(
        "mysql+pymysql://demo:secret@127.0.0.1:3306/agent_enterprise?charset=utf8mb4"
    ) == "mysql+pymysql://demo:secret@127.0.0.1:3306/agent_enterprise?charset=utf8mb4"


@pytest.mark.parametrize(
    "url",
    [
        "mysql+pymysql://demo:secret@db.example.com:3306/agent_team",
        "mysql+pymysql://demo:secret@127.0.0.1:3306/other_database",
        "sqlite:///./agent_team.db",
    ],
)
def test_enterprise_database_url_rejects_nonlocal_or_unexpected_targets(url: str):
    assert hasattr(init_mysql, "enterprise_database_url")
    with pytest.raises(ValueError):
        init_mysql.enterprise_database_url(url)


def test_admin_url_can_be_derived_from_configured_local_database_url(monkeypatch):
    monkeypatch.delenv("MYSQL_ADMIN_URL", raising=False)
    monkeypatch.delenv("MYSQL_ROOT_PASSWORD", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "mysql+pymysql://demo:secret@127.0.0.1:3306/agent_enterprise?charset=utf8mb4",
    )

    admin_url = init_mysql.build_admin_url()
    assert admin_url == "mysql+pymysql://demo:secret@127.0.0.1:3306"


def test_local_admin_url_is_derived_without_database_or_query():
    assert hasattr(init_mysql, "admin_database_url")
    assert init_mysql.admin_database_url(
        "mysql+pymysql://demo:secret@127.0.0.1:3306/agent_enterprise?charset=utf8mb4"
    ) == "mysql+pymysql://demo:secret@127.0.0.1:3306"


def test_local_launcher_delegates_database_creation_to_authorized_entrypoint():
    repo_root = Path(__file__).resolve().parents[2]
    launcher = (repo_root / "deploy" / "dev-enterprise-local.ps1").read_text(
        encoding="utf-8"
    )

    assert "scripts.init_mysql" in launcher
    assert "create database" not in launcher.lower()
    assert "$env:DATABASE_NAME = 'agent_enterprise'" in launcher
    assert "admin_database_url" in launcher
