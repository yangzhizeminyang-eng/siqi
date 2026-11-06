"""DB 连接池加固 + 依赖健康探活 专项测试（压测并发稳定性）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.runtime_config import load_runtime_settings
from app.db import session as db_session
from app.retrieval import llamaindex_backend as rag_backend


@pytest.fixture()
def isolated_sqlite_db(tmp_path, monkeypatch):
    db_file = tmp_path / "pool_health.db"
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    yield db_file
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()


def test_runtime_settings_expose_pool_tuning_fields():
    settings = load_runtime_settings()
    # 默认值面向 2~4 worker 团队版
    assert settings.db_pool_size == 10
    assert settings.db_max_overflow == 20
    assert settings.db_pool_recycle == 1800
    assert settings.db_pool_pre_ping is True
    assert settings.db_pool_timeout == 30


def test_sqlite_engine_health_and_pool_stats(isolated_sqlite_db):
    health = db_session.check_database_health()
    assert health["status"] == "ok"
    assert isinstance(health["latency_ms"], float)
    stats = db_session.pool_stats()
    # sqlite 走文件连接池：size/checkedin 等应可读出（不报错）
    assert isinstance(stats, dict)


def test_mysql_url_applies_pool_parameters(monkeypatch):
    # 不真连 MySQL：create_engine 懒连接，只验证池参数被传进去并落到 pool。
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://u:p@127.0.0.1:3306/nonexist_db?charset=utf8mb4")
    monkeypatch.setenv("DB_POOL_SIZE", "12")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "25")
    monkeypatch.setenv("DB_POOL_PRE_PING", "true")
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    try:
        engine = db_session.get_engine()
        assert engine.pool.size() == 12
        assert engine.pool._max_overflow == 25
        assert engine.pool._pre_ping is True
        # 池统计可观测，便于压测时观察是否被打满/泄漏
        stats = db_session.pool_stats()
        assert "size" in stats and "checkedout" in stats
    finally:
        db_session.get_engine.cache_clear()
        db_session.get_session_factory.cache_clear()


def test_check_database_health_returns_down_without_raising(monkeypatch):
    # 指向一个必然连不上的地址：探活应返回 down，绝不抛出。
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://u:p@127.0.0.1:59999/nonexist?connect_timeout=2")
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    try:
        result = db_session.check_database_health(timeout_ms=2000)
        assert result["status"] == "down"
        assert "error" in result
    finally:
        db_session.get_engine.cache_clear()
        db_session.get_session_factory.cache_clear()


def test_milvus_health_skipped_when_disabled(monkeypatch):
    monkeypatch.setenv("USE_MILVUS", "false")
    result = rag_backend.check_milvus_health()
    assert result["status"] == "skipped"


def test_health_endpoint_reports_dependencies(monkeypatch):
    from app import main

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("USE_MILVUS", "false")
    monkeypatch.setenv("USE_CELERY", "false")
    client = TestClient(main.app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] in {"ok", "degraded"}
    assert "dependencies" in data
    assert "db" in data["dependencies"]
    assert "milvus" in data["dependencies"]
    # 不回显任何凭据 / 完整连接串
    raw = resp.text
    assert "MILVUS_TOKEN" not in raw
    assert "u:p@" not in raw


def test_team_health_returns_503_when_required_dependency_is_down(monkeypatch):
    from app import main

    monkeypatch.setenv("APP_ENV", "team")
    monkeypatch.setenv("USE_CELERY", "true")
    monkeypatch.setenv("USE_MILVUS", "true")
    monkeypatch.setattr(main, "check_milvus_health", lambda: {"status": "down", "error": "offline"})
    monkeypatch.setattr(main, "_check_redis_health", lambda: {"status": "ok"})
    monkeypatch.setattr(
        db_session,
        "check_database_health",
        lambda: {"status": "ok", "latency_ms": 1.0},
    )

    response = TestClient(main.app).get("/api/health")
    assert response.status_code == 503
    assert response.json()["data"]["status"] == "degraded"
