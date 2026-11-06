r"""MySQL 生产数据库初始化入口。

职责：
1. 连接到 MySQL（无默认库），用有 `CREATE DATABASE` 权限的账号创建目标库（若不存在）；
2. 随后对目标库执行 create_all 建全量业务表（不存在则建，幂等）。

使用（working dir = backend）：
    # 方案 A：指定管理员连接（需 CREATE DATABASE 权限），密码走环境变量，不落盘
    $env:MYSQL_ADMIN_URL="mysql+pymysql://root:***@127.0.0.1:3306"   # 或用下列分体变量
    $env:DATABASE_NAME="agent_mvp"
    .\.venv\Scripts\python.exe -m scripts.init_mysql

    # 方案 B：分体变量
    $env:MYSQL_HOST="127.0.0.1"; $env:MYSQL_PORT="3306"; $env:MYSQL_ADMIN_USER="root"
    $env:MYSQL_ROOT_PASSWORD="***"; $env:DATABASE_NAME="agent_mvp"
    .\.venv\Scripts\python.exe -m scripts.init_mysql

说明：
- 若用只读/应用账号（无 CREATE DATABASE），请先由 DBA 用管理员账号跑本脚本或手工建库，
  再把应用库的连接串配给应用。
- 未设置 MYSQL_ROOT_PASSWORD / MYSQL_ADMIN_URL 时脚本会明确报错，不会把你留在“不知道库建没建”里。
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, "").strip() or default


def build_admin_url() -> str:
    if _env("MYSQL_ADMIN_URL"):
        return _env("MYSQL_ADMIN_URL")
    user = _env("MYSQL_ADMIN_USER", "root")
    password = os.getenv("MYSQL_ROOT_PASSWORD", "").strip()
    if not password:
        database_url = _env("DATABASE_URL")
        if database_url:
            try:
                url = make_url(database_url)
            except Exception as exc:  # noqa: BLE001 - report configuration error without credentials
                raise SystemExit("DATABASE_URL 不是有效的 SQLAlchemy URL。") from exc
            if url.drivername != "mysql+pymysql" or not url.host or not url.database:
                raise SystemExit("DATABASE_URL 必须是完整的 mysql+pymysql 连接串。")
            admin_url = URL.create(
                drivername=url.drivername,
                username=url.username,
                password=url.password,
                host=url.host,
                port=url.port,
            )
            return admin_url.render_as_string(hide_password=False)
        raise SystemExit("缺少 MYSQL_ROOT_PASSWORD（或 MYSQL_ADMIN_URL）。创建库需要具备 CREATE DATABASE 权限的账号；" +
                         "若已由 DBA 建好库，可直接把 DATABASE_URL 配给应用并跳过本脚本。")
    host = _env("MYSQL_HOST", "127.0.0.1")
    port = _env("MYSQL_PORT", "3306")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}"


def enterprise_database_url(database_url: str) -> str:
    """Constrain the local enterprise launcher to its loopback enterprise database."""
    try:
        url = make_url(database_url)
    except Exception as exc:  # noqa: BLE001 - do not echo credentials from a malformed URL
        raise ValueError("DATABASE_URL 不是有效的 SQLAlchemy URL。") from exc

    if url.drivername != "mysql+pymysql":
        raise ValueError("本地企业启动仅接受 mysql+pymysql DATABASE_URL。")
    if url.host not in {"127.0.0.1", "localhost", "::1"} or url.port not in {None, 3306}:
        raise ValueError("本地企业启动仅允许 loopback 主机和 3306 端口。")
    if url.database not in {"agent_team", "agent_enterprise"}:
        raise ValueError("DATABASE_URL 数据库名必须是 agent_team 或 agent_enterprise。")

    return url.set(database="agent_enterprise").render_as_string(hide_password=False)


def admin_database_url(database_url: str) -> str:
    """Build a local admin connection URL without selecting a default database."""
    try:
        url = make_url(database_url)
    except Exception as exc:  # noqa: BLE001 - do not echo credentials from malformed input
        raise ValueError("DATABASE_URL 不是有效的 SQLAlchemy URL。") from exc
    if (
        url.drivername != "mysql+pymysql"
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or url.port not in {None, 3306}
        or url.database != "agent_enterprise"
    ):
        raise ValueError("管理员连接必须指向本机 agent_enterprise 配置。")
    return URL.create(
        drivername=url.drivername,
        username=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
    ).render_as_string(hide_password=False)


def database_name() -> str:
    configured = _env("DATABASE_NAME")
    if configured:
        return configured
    url = _env("DATABASE_URL")
    if url:
        try:
            parsed = make_url(url)
        except Exception as exc:  # noqa: BLE001 - keep configuration failure explicit
            raise SystemExit("DATABASE_URL 不是有效的 SQLAlchemy URL。") from exc
        if parsed.drivername == "mysql+pymysql" and parsed.database:
            return parsed.database
    return "agent_mvp"


def main() -> None:
    from scripts.authorization_gate import require_authorization

    require_authorization(
        os.environ.get("AUTHORIZATION_OPERATION", ""),
        os.environ.get("AUTHORIZATION_LOG", "") or None,
    )
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    admin_url = build_admin_url()
    dbname = database_name()
    print(f"[init_mysql] 目标库：{dbname}（host 取自连接串，凭据来自环境变量，不打印）")
    admin = create_engine(admin_url, connect_args={"connect_timeout": 8})
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = :n"), {"n": dbname}
            ).fetchone()
            if not exists:
                print(f"[init_mysql] 目标库 {dbname} 不存在，正在创建（需要 CREATE DATABASE 权限）...")
                conn.execute(
                    text(f"CREATE DATABASE `{dbname}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                )
                print(f"[init_mysql] 已创建数据库 {dbname}（utf8mb4）")
            else:
                print(f"[init_mysql] 目标库 {dbname} 已存在，跳过创建")
    finally:
        admin.dispose()

    # 对目标库执行 create_all 建全量业务表（幂等）
    app_url = os.getenv("DATABASE_URL") or f"mysql+pymysql://{admin_url.split('://',1)[1]}/{dbname}?charset=utf8mb4"
    os.environ["DATABASE_URL"] = app_url
    from app.core.runtime_config import load_runtime_settings
    from app.db.migrate import run_upgrade
    from app.db.session import get_engine

    load_runtime_settings()  # 让 get_engine 读到全新 DATABASE_URL
    get_engine.cache_clear()
    get_session_factory_clear()
    run_upgrade()  # alembic upgrade head：建表 + 既有库安全演进（不只依赖 create_all）
    count = len(inspect(get_engine()).get_table_names())
    print(f"[init_mysql] alembic upgrade head 完成：{count} 张表")
    print("[INIT-MYSQL] PASS: 库已就绪，业务表已就位")


def get_session_factory_clear() -> None:
    from app.db.session import get_session_factory

    get_session_factory.cache_clear()


if __name__ == "__main__":
    main()
