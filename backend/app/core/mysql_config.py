"""MySQL 生产形态配置校验。

只负责：
- 校验 DATABASE_URL 是否为 MySQL(pymysql) 形态；
- 组装 MySQL 连接串；
- 探测 pymysql 是否可导入（真实连接需有 MySQL 服务，见 scripts/mysql_smoke.py）。
"""

from __future__ import annotations

import os

from app.core.runtime_config import load_runtime_settings

MYSQL_DIALECT_PREFIX = "mysql+pymysql"


def build_mysql_url(*, host: str, port: int = 3306, user: str, password: str, database: str, charset: str = "utf8mb4") -> str:
    return f"{MYSQL_DIALECT_PREFIX}://{user}:{password}@{host}:{port}/{database}?charset={charset}"


def is_mysql_url(url: str | None) -> bool:
    return bool(url and url.startswith(MYSQL_DIALECT_PREFIX))


def pymysql_installed() -> bool:
    try:
        import pymysql  # noqa: F401

        return True
    except Exception:
        return False


def mysql_ready(url: str | None = None) -> tuple[bool, str]:
    """返回 (ok, 说明)：URL 形态 + pymysql 依赖是否就绪；不要求有活 MySQL 服务。"""
    url = url if url is not None else load_runtime_settings().database_url
    if not is_mysql_url(url):
        return False, f"当前 DATABASE_URL 非 MySQL(pymysql) 形态：{url or '(空)'}"
    if not pymysql_installed():
        return False, "依赖 pymysql 未安装（pyproject 已声明，请 uv sync）"
    return True, "MySQL(pymysql) 形态与依赖就绪"


def configured_database_url() -> str:
    return load_runtime_settings().database_url or ""