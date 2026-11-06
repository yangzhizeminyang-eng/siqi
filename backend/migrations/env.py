from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# 注册全部 ORM 模型，让 autogenerate / baseline 看到所有表
import app.models.orm  # noqa: F401
from app.models.base import Base
from app.core.runtime_config import load_runtime_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# 统一从后端运行配置读 DATABASE_URL（支持 SQLite/MySQL），回退到 alembic.ini 的字面配置
settings = load_runtime_settings()
sqlalchemy_url = settings.database_url or config.get_main_option("sqlalchemy.url")
config.set_main_option("sqlalchemy.url", sqlalchemy_url)


def _batchable():
    """SQLite 无 ALTER 原生支持，开启 batch 以支持列演进/回填。"""
    url = config.get_main_option("sqlalchemy.url") or ""
    return url.startswith("sqlite")


def run_migrations_offline() -> None:
    context.configure(
        url=sqlalchemy_url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=_batchable(),
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=_batchable(),
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()