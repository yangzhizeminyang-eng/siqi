"""SQLAlchemy session / engine helpers."""

from __future__ import annotations

import time
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.runtime_config import load_runtime_settings
from app.models.base import Base


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = load_runtime_settings()
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        # SQLite 单文件/内存：不走 MySQL 连接池参数，避免对 sqlite 传 pool_size 报错。
        connect_args["check_same_thread"] = False
        return create_engine(settings.database_url, future=True, echo=False, connect_args=connect_args)

    # MySQL/其他网络数据库：显式配置连接池。
    # - pool_size + max_overflow：压测并发建任务顶满默认池（5+10）的直接修复；
    # - pool_recycle：回收空闲连接，规避 MySQL wait_timeout 断开后复用；
    # - pool_pre_ping：取连接前轻量 ping，失效连接自动丢弃重连；
    # - pool_timeout：拿不到连接时快速失败并报错，而不是无限阻塞。
    return create_engine(
        settings.database_url,
        future=True,
        echo=False,
        connect_args=connect_args,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=settings.db_pool_pre_ping,
    )


@lru_cache(maxsize=1)
def get_session_factory():
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)


@contextmanager
def session_scope() -> Session:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_schema() -> None:
    # 保证 ORM 模型注册到 Base.metadata：不依赖调用方是否先 import 过 orm 模块
    import app.models.orm  # noqa: F401

    Base.metadata.create_all(bind=get_engine())


def pool_stats() -> dict:
    """暴露连接池运行态，供健康端点观察连接是否被打满/泄漏。

    SQLite 内存引擎没有这些属性时返回空字典，不报错。
    """
    pool = getattr(get_engine(), "pool", None)
    if pool is None:
        return {}
    stats: dict = {}
    for attr in ("size", "checkedin", "checkedout", "overflow"):
        getter = getattr(pool, attr, None)
        if callable(getter):
            try:
                stats[attr] = int(getter())
            except Exception:
                continue
    return stats


def check_database_health(timeout_ms: int = 2000) -> dict:
    """轻量探活：拿一个连接执行 SELECT 1，返回状态/延迟/池统计。不抛错。"""
    started = time.perf_counter()
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return {"status": "ok", "latency_ms": latency_ms, "pool": pool_stats()}
    except Exception as error:  # noqa: BLE001
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return {
            "status": "down",
            "latency_ms": latency_ms,
            "error": str(error)[:300],
            "pool": pool_stats(),
        }
