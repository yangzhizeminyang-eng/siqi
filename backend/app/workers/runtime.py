"""Celery worker 运行时初始化和恢复。

worker 进程不经过 FastAPI lifespan，必须自行 create_schema + restore_from_db
才能读到 DB 中的业务数据（任务/团队需求/索引/审计等）。
ensure_worker_runtime_ready() 幂等、可重复调用且失败不硬崩：restore 失败会纳入日志，
任务随后按「缺失实体→WorkerTaskError(可序列化)」路径处理，而不是抛不可序列化异常砸掉 worker。
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger("backend.celery.runtime")

_lock = threading.Lock()
_schema_done = False


def init_worker_runtime() -> dict[str, int]:
    """一次性：检查 schema 版本（只检查不迁移）+ 建表兜底 + 首次恢复。幂等（_schema_done 守卫）。

    - Worker 不执行迁移：落后时 ensure_schema_current(upgrade=False) 抛错，
      由调用方记日志，绝不并发执行 alembic（避免多 Worker 抢版本锁）。
    - 未开启持久化（测试/eager）→ 空操作。
    """
    global _schema_done
    with _lock:
        if not _schema_done:
            from app.db.migrate import ensure_schema_current
            from app.db.session import create_schema

            ensure_schema_current(upgrade=False)
            create_schema()
            _schema_done = True
    from app.db.bootstrap import restore_from_db

    return restore_from_db()


def ensure_worker_runtime_ready() -> None:
    """每个任务执行前调用：保证 schema 已建、并从 DB 恢复最新业务运行时缓存。

    - 幂等：可对同一进程重复调用（restore 为清空+重灌，无重复副作用）。
    - 失败不硬崩：restore 异常记日志后吞掉，交由任务自身的「实体缺失→WorkerTaskError」路径处理，
      保持异常可序列化，不因 restore 的 DB 层异常把 worker 打崩。"""
    global _schema_done
    try:
        if not _schema_done:
            init_worker_runtime()
        from app.db.bootstrap import restore_from_db

        restore_from_db()
    except Exception as error:  # noqa: BLE001 - 生产任务不得在缓存未恢复时继续执行
        logger.warning("[worker-runtime] restore_from_db 失败（任务将按缺失实体处理）：%s", error)
        from app.core.runtime_config import load_runtime_settings

        if load_runtime_settings().celery_required():
            raise RuntimeError(f"生产 Worker 数据恢复失败，拒绝执行任务：{error}") from error
