"""Celery 应用配置。"""

from __future__ import annotations

import logging

from celery import Celery
from celery.signals import worker_init, worker_process_init

from app.core.runtime_config import load_runtime_settings

logger = logging.getLogger("backend.celery_app")
settings = load_runtime_settings()

celery_app = Celery(
    "backend",
    broker=settings.celery_broker_url or None,
    backend=settings.celery_result_backend or None,
    include=["app.workers.tasks", "app.workers.enterprise_tasks"],
)

celery_app.conf.update(
    task_always_eager=not bool(settings.celery_broker_url),
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
)


def _bootstrap_worker_runtime(source: str) -> None:
    """Worker 启动时主动恢复 DB 业务运行时缓存（不依赖 FastAPI lifespan）。"""
    from app.workers.runtime import init_worker_runtime

    restored = init_worker_runtime()
    logger.info("[celery] Worker runtime bootstrap via %s 完成: %s", source, restored)


@worker_init.connect(weak=False)
def _on_worker_init(**kwargs) -> None:
    _bootstrap_worker_runtime("worker_init")


@worker_process_init.connect(weak=False)
def _on_worker_process_init(**kwargs) -> None:
    _bootstrap_worker_runtime("worker_process_init")

