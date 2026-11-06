"""Celery 任务派发：长任务在 Worker 执行，HTTP 只创建任务并返回状态。

降级契约：
- 真实接入：`USE_CELERY=true` 且配置 `CELERY_BROKER_URL`（Redis）→ 分发到 Worker。
- 否则一律 eager 内联（测试/本地降级），并在返回值与日志中明确标记 `mode="eager(test)"`。
eager 路径执行的就是 Worker 里的同一段工作函数，行为与既有内联路径完全一致。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.core.runtime_config import load_runtime_settings

logger = logging.getLogger("backend.celery_dispatch")


def celery_enabled() -> bool:
    """是否真实接入 Celery（USE_CELERY=true 且配置了 broker）。"""
    settings = load_runtime_settings()
    return bool(settings.use_celery and settings.celery_broker_url)


def enqueue_or_run(
    task: Callable[..., Any],
    *,
    kwargs: dict[str, Any] | None = None,
    run_inline: Callable[[], Any],
    label: str = "",
) -> dict[str, Any]:
    """把长任务派发给 Worker；无 Redis 时内联执行并明确标记降级。

    - `task`：Celery task 对象（.delay 走 Redis）。
    - `run_inline`：eager/降级下同步执行的工作函数（必须与 Worker 内执行的同一段逻辑）。
    - 返回：`{enqueued, mode, async_result_id?, label}`。
    """
    kwargs = kwargs or {}
    if celery_enabled():
        result = task.delay(**kwargs)
        logger.info("[celery] 已派发任务 %s -> worker (async_result=%s)", label or task.name, result.id)
        return {"enqueued": True, "mode": "redis", "async_result_id": str(result.id), "label": label}

    _warn_eager_degradation(label or task.name)
    inline_result = run_inline()
    return {
        "enqueued": False,
        "mode": "eager(test)",
        "label": label,
        "result": inline_result,
    }


def _warn_eager_degradation(label: str) -> None:
    settings = load_runtime_settings()
    if settings.use_celery and not settings.celery_broker_url:
        logger.warning(
            "[celery] 检测到 USE_CELERY=true 但未配置 CELERY_BROKER_URL：任务 %s 以降级(eager)模式内联执行，仅用于测试/本地，接生产需配置 Redis。",
            label,
        )
    else:
        logger.info("[celery] 任务 %s 以 eager(test) 模式内联执行（未启用真实 Redis 队列）", label)
