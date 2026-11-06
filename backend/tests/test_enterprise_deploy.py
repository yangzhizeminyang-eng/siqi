"""企业版部署启动校验测试。

分层：
- runtime_config（进程内）：与 team/production 同口径——APP_ENV=enterprise 强制 MySQL（USE_SQLALCHEMY=true 时）+ celery_required。
- backend-entrypoint.sh（容器启动门）：企业版缺失 MySQL/JWT/恢复码/Celery/Redis/CORS/Milvus 时启动拒绝（exit 64）。
组合覆盖「启动拒绝」的关键项（缺 MySQL、缺 JWT、缺恢复码、缺 Redis/Celery 配置）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core import runtime_config as rc

_PROJECT = Path(__file__).resolve().parents[1]  # backend/
_ENTRYPOINT = (_PROJECT.parent / "deploy" / "backend-entrypoint.sh").as_posix()

_open = pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX shell sh 不可用")


# ---------------- runtime_config：企业版强制 MySQL + celery_required ----------------

def _full_env(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "enterprise")
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://user:pass@mysql:3306/agent_enterprise?charset=utf8mb4")


def test_enterprise_valid_loads_and_celery_required(monkeypatch):
    _full_env(monkeypatch)
    settings = rc.load_runtime_settings()
    assert settings.app_env == "enterprise"
    assert settings.celery_required() is True


def test_enterprise_missing_mysql_refused(monkeypatch):
    _full_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "")
    with pytest.raises(RuntimeError, match="enterprise"):
        rc.load_runtime_settings()


def test_enterprise_sqlite_refused(monkeypatch):
    _full_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./app.db")
    with pytest.raises(RuntimeError, match="enterprise"):
        rc.load_runtime_settings()


def test_enterprise_in_memory_mode_bypasses_mysql_force(monkeypatch):
    """与团队版一致：USE_SQLALCHEMY=false（内存/测试执行企业门禁）时不强制 MySQL。"""
    monkeypatch.setenv("APP_ENV", "enterprise")
    monkeypatch.setenv("USE_SQLALCHEMY", "false")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = rc.load_runtime_settings()
    assert settings.app_env == "enterprise"


def test_team_env_in_process_not_enterprise_strict(monkeypatch):
    """团队版进程内不强校验 JWT/恢复码（其在部署侧 entrypoint 强制）；确认无回归、不误伤。"""
    monkeypatch.setenv("APP_ENV", "team")
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://user:pass@mysql:3306/agent_team?charset=utf8mb4")
    settings = rc.load_runtime_settings()
    assert settings.app_env == "team"
    assert settings.celery_required() is True


def test_local_env_defaults_not_blocked(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = rc.load_runtime_settings()
    assert settings.app_env == "local"
    assert settings.celery_required() is False


# ---------------- 部署运行加固：worker healthcheck / frontend 非 root / 端口 / Nginx ----------------

import yaml

_REPO = Path(__file__).resolve().parents[2]  # 仓库根
_DEPLOY = _REPO / "deploy"


def _load_compose(name: str) -> dict:
    with (_DEPLOY / name).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _read(rel: str) -> str:
    return (_REPO / rel).read_text(encoding="utf-8")


def test_enterprise_worker_has_broker_aware_healthcheck():
    compose = _load_compose("docker-compose.enterprise.yml")
    worker = compose["services"]["worker"]
    assert "healthcheck" in worker, "enterprise worker 必须定义 healthcheck"
    test = worker["healthcheck"]["test"]
    assert any(isinstance(x, str) and "worker-healthcheck.sh" in x for x in test), test
    # 不要高频探测；interval 下限 30s，避免制造高频 broker 探测流量
    assert int(worker["healthcheck"].get("interval", 0).rstrip("s")) >= 30
    # 保持 worker 不并发迁移
    assert worker["environment"]["RUN_ALEMBIC_UPGRADE"] == "false"


def test_enterprise_worker_healthcheck_script_reflects_broker_and_no_side_effect():
    script = _read("deploy/worker-healthcheck.sh")
    # 反映「worker 应答 + broker 可用」：控制面 ping，非进程存在性检查
    assert "inspect ping" in script and "--destination" in script
    # 只检查可执行行是否触碰业务副作用（注释不做误判来源）
    code_lines = "\n".join(
        line for line in script.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ).lower()
    for banned in ("delete", "reset", "index", "task", "gitee", "sync", "approval", "alembic", "upgrade",
                   "apply_async", "send_task", "destroy", "REPOSITORY_INDEXES", "TEAM_REQUESTS"):
        assert banned not in code_lines, f"worker healthcheck 不应触碰业务副作用: {banned}"
    # 预热放行避免误判
    assert any(k in script for k in ("uptime", "UPTIME", "GRACE", "start_period"))


def test_worker_healthcheck_path_baked_in_backend_image():
    """Compose 引用的 worker healthcheck 路径必须由 backend 镜像实际提供（防路径不一致导致必 unhealthy）。"""
    dockerfile = _read("backend/Dockerfile")
    compose = _load_compose("docker-compose.enterprise.yml")
    cmd = compose["services"]["worker"]["healthcheck"]["test"]
    cmd_paths = [x for x in cmd if isinstance(x, str) and x.startswith("/usr/local/bin/")]
    assert cmd_paths, f"worker healthcheck 需显式绝对路径: {cmd}"
    image_path = f"COPY deploy/worker-healthcheck.sh {cmd_paths[0]}"
    assert image_path in dockerfile, (
        f"backend Dockerfile 必须把 healthcheck 复制到 compose 引用路径 {cmd_paths[0]}，当前未一致"
    )


def test_frontend_image_runs_nonroot_and_internal_8080():
    dockerfile = _read("frontend/Dockerfile")
    assert "USER nginx" in dockerfile, "frontend 镜像必须非 root 运行"
    assert "EXPOSE 8080" in dockerfile, "容器内部必须继续监听 8080"
    assert "nginx.master.conf" in dockerfile or "nginx.conf" in dockerfile
    # 不得用宽松权限/特权绕过
    low = dockerfile.lower()
    for banned in ("--privileged", "cap_add", "chmod -R 777", "chmod 777"):
        assert banned not in low, f"frontend Dockerfile 不得用宽松权限绕过非 root: {banned}"


def test_enterprise_public_8081_and_internal_not_facing_public():
    compose = _load_compose("docker-compose.enterprise.yml")
    ports = compose["services"]["frontend"]["ports"]
    assert any("8081" in str(p) and ":8080" in str(p) for p in ports), ports
    # MySQL / Redis / backend 不对公网发布（frontend 是唯一 published）
    published = []
    for svc, conf in compose["services"].items():
        for p in conf.get("ports", []) or []:
            published.append((svc, str(p)))
    # 只有 frontend 有 published（映射到宿主机）
    assert [s for s, _ in published if s != "frontend"] == [], published


def test_enterprise_milvus_stack_isolated_and_internal_only():
    compose = _load_compose("docker-compose.enterprise.yml")
    services = compose["services"]
    assert {"milvus-enterprise", "etcd-enterprise", "minio-enterprise"} <= set(services)
    assert services["milvus-enterprise"]["image"].startswith("milvusdb/milvus:")
    assert services["milvus-enterprise"]["environment"]["ETCD_ENDPOINTS"] == "etcd-enterprise:2379"
    assert services["milvus-enterprise"]["environment"]["MINIO_ADDRESS"] == "minio-enterprise:9000"
    assert services["milvus-enterprise"].get("ports", []) == []
    assert services["etcd-enterprise"].get("ports", []) == []
    assert services["minio-enterprise"].get("ports", []) == []
    assert "milvus" in services["backend"]["networks"]
    assert "milvus" in services["worker"]["networks"]
    assert services["backend"]["depends_on"]["milvus-enterprise"]["condition"] == "service_healthy"


def test_enterprise_milvus_has_independent_persistent_volumes_and_prefix():
    compose = _load_compose("docker-compose.enterprise.yml")
    volumes = compose["volumes"]
    assert {"enterprise_milvus_etcd_data", "enterprise_milvus_minio_data"} <= set(volumes)
    assert volumes["enterprise_milvus_etcd_data"]["name"] == "agent_enterprise_milvus_etcd_data"
    assert volumes["enterprise_milvus_minio_data"]["name"] == "agent_enterprise_milvus_minio_data"
    env = _read("deploy/env/enterprise.env.example")
    assert "USE_MILVUS=true" in env
    assert "MILVUS_URI=http://milvus-enterprise:19530" in env
    assert "MILVUS_COLLECTION_PREFIX=enterprise" in env


def test_team_compose_frontend_port_and_config_unchanged():
    compose = _load_compose("docker-compose.team.yml")
    ports = compose["services"]["frontend"]["ports"]
    assert any("${TEAM_PUBLIC_PORT:-8080}:8080" in str(p) for p in ports), ports
    # 团队版不变更 worker（可选 healthcheck 可后补，但不得改变其无 healthcheck 或当前配置）
    assert compose["services"]["backend"]["environment"]["APP_ENV"] == "team"


def test_nginx_intergraph_conf_keeps_8080_proxy_and_spa(monkeypatch):
    for rel in ("deploy/nginx.enterprise.conf", "deploy/nginx.team.conf"):
        conf = _read(rel)
        assert "listen 8080" in conf, rel
        assert "location /api/" in conf and "proxy_pass http://backend:8000" in conf, rel
        assert "try_files" in conf and "/index.html" in conf, rel
        assert "client_max_body_size" in conf, rel
    ent = _read("deploy/nginx.enterprise.conf")
    assert "location = /health" in ent


def test_nginx_master_conf_nonroot_compatible():
    master = _read("deploy/nginx.master.conf")
    assert "pid /tmp/nginx.pid" in master, master
    # 非 root 下 master 不得试图 setuid（用户指令会让非 root master 启动失败）
    assert "user nginx" not in master
    for token in ("client_body_temp_path", "proxy_temp_path", "fastcgi_temp_path"):
        assert token in master


# ---------------- backend-entrypoint.sh：企业版启动门 ----------------

def _entry_env(**overrides) -> dict:
    env = {
        "APP_ENV": "enterprise",
        "USE_SQLALCHEMY": "true",
        "DATABASE_URL": "mysql+pymysql://user:pass@mysql:3306/agent_enterprise?charset=utf8mb4",
        "USE_CELERY": "true",
        "CELERY_BROKER_URL": "redis://redis:6379/0",
        "CELERY_RESULT_BACKEND": "redis://redis:6379/1",
        "REDIS_URL": "redis://redis:6379/2",
        "AUTH_JWT_SECRET": "k" * 32,
        "AUTH_RECOVERY_CODE": "r" * 20,
        "FRONTEND_CORS_ORIGINS": "http://127.0.0.1:8081",
        "USE_MILVUS": "false",
        "MILVUS_COLLECTION_PREFIX": "enterprise",
        "EMBEDDING_PROVIDER": "zhipu",
        "ZHIPU_MODEL": "embedding-3",
        "ZHIPU_API_KEY": "test-only-not-a-secret",
        "EMBEDDING_DIMENSIONS": "auto",
    }
    env.update(overrides)
    return {**os.environ, **env}


def _run_entry(env: dict) -> int:
    return subprocess.run(["sh", _ENTRYPOINT, "true"], env=env, capture_output=True, text=True).returncode


@_open
def test_entrypoint_enterprise_ok_starts():
    assert _run_entry(_entry_env()) == 0


@_open
def test_entrypoint_enterprise_missing_mysql_refused():
    assert _run_entry(_entry_env(DATABASE_URL="sqlite:///./x.db")) == 64
    assert _run_entry(_entry_env(DATABASE_URL="")) == 64


@_open
def test_entrypoint_enterprise_missing_jwt_refused():
    assert _run_entry(_entry_env(AUTH_JWT_SECRET="")) == 64
    assert _run_entry(_entry_env(AUTH_JWT_SECRET="short")) == 64


@_open
def test_entrypoint_enterprise_missing_recovery_refused():
    assert _run_entry(_entry_env(AUTH_RECOVERY_CODE="")) == 64
    assert _run_entry(_entry_env(AUTH_RECOVERY_CODE="short")) == 64


@_open
def test_entrypoint_enterprise_missing_celery_refused():
    assert _run_entry(_entry_env(USE_CELERY="false")) == 64
    assert _run_entry(_entry_env(CELERY_BROKER_URL="")) == 64


@_open
def test_entrypoint_enterprise_missing_redis_refused():
    assert _run_entry(_entry_env(REDIS_URL="")) == 64


@_open
def test_entrypoint_enterprise_missing_cors_refused():
    assert _run_entry(_entry_env(FRONTEND_CORS_ORIGINS="")) == 64


@_open
def test_entrypoint_enterprise_milvus_uri_required_when_enabled():
    assert _run_entry(_entry_env(USE_MILVUS="true", MILVUS_URI="")) == 64
    assert _run_entry(_entry_env(USE_MILVUS="true", MILVUS_URI="http://milvus:19530")) == 0


@_open
def test_entrypoint_enterprise_real_milvus_requires_real_embedding_and_isolation():
    base = _entry_env(
        USE_MILVUS="true",
        MILVUS_URI="http://milvus-enterprise:19530",
        ZHIPU_API_KEY="",
        EMBEDDING_PROVIDER="zhipu",
        ZHIPU_MODEL="embedding-3",
        EMBEDDING_DIMENSIONS="auto",
        MILVUS_COLLECTION_PREFIX="enterprise",
    )
    assert _run_entry(base) == 64
    assert _run_entry({**base, "ZHIPU_API_KEY": "configured-but-not-printed"}) == 0
    assert _run_entry({**base, "ZHIPU_API_KEY": "configured-but-not-printed", "ZHIPU_MODEL": "other"}) == 64
    assert _run_entry({**base, "ZHIPU_API_KEY": "configured-but-not-printed", "MILVUS_COLLECTION_PREFIX": "team"}) == 64
