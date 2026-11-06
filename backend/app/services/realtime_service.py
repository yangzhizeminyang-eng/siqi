"""团队版实时事件：Redis Pub/Sub + SSE 的轻量事件总线。

职责边界：
- MySQL 仍然是业务事实源；本模块只发布“某个资源发生变化”的信号。
- 发布失败不影响已经完成的业务写入，在线页面会继续依赖原有轮询/手动刷新兜底。
- SSE 连接按登录用户订阅个人通知频道和团队广播频道，浏览器收到事件后重新读取受权限保护的 API。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("backend.realtime")

TEAM_CHANNEL = "team:realtime:broadcast"
USER_CHANNEL_PREFIX = "team:realtime:user:"
HEARTBEAT_SECONDS = 15
REDIS_SOCKET_TIMEOUT_SECONDS = 2.0


def _redis_url() -> str:
    """复用团队版已有 Redis：优先实时专用地址，其次分布式锁，再其次 Celery broker。"""
    return (
        os.getenv("REALTIME_REDIS_URL", "").strip()
        or os.getenv("REDIS_URL", "").strip()
        or os.getenv("CELERY_BROKER_URL", "").strip()
    )


def enabled() -> bool:
    """团队/生产环境默认启用；本地和测试可用 REALTIME_ENABLED 显式打开。"""
    explicit = os.getenv("REALTIME_ENABLED")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    environment = os.getenv("APP_ENV", "local").strip().lower()
    return environment in {"team", "production", "prod"} and bool(_redis_url())


def _client():
    import redis

    return redis.Redis.from_url(
        _redis_url(),
        decode_responses=True,
        socket_connect_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
        socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
    )


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _publish(channel: str, payload: dict[str, Any]) -> bool:
    if not enabled():
        return False
    try:
        _client().publish(channel, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return True
    except Exception as error:  # noqa: BLE001 - 实时通知失败不应回滚已完成的业务写入
        logger.warning("[realtime] Redis 发布失败 channel=%s error=%s", channel, error)
        return False


def publish_resource_update(
    *,
    resource_type: str,
    resource_id: str,
    event_type: str,
    updated_at: datetime | None = None,
    actor_id: str | None = None,
    task_id: str | None = None,
) -> bool:
    """发布不含业务正文的资源变化事件；前端必须重新请求 API 获取完整数据。"""
    payload: dict[str, Any] = {
        "type": event_type,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "task_id": task_id or (resource_id if resource_type == "task" else None),
        "updated_at": (updated_at or datetime.now(timezone.utc)).isoformat(),
        "occurred_at": _iso_now(),
    }
    if actor_id:
        payload["actor_id"] = actor_id
    return _publish(TEAM_CHANNEL, payload)


def publish_notification(
    *,
    notification_id: str,
    recipient_id: str,
    task_id: str,
    notification_type: str,
    created_at: datetime | None = None,
) -> bool:
    """只把通知变化发给收件人；正文仍通过 /api/notifications 按权限读取。"""
    payload = {
        "type": "notification.created",
        "resource_type": "notification",
        "resource_id": notification_id,
        "task_id": task_id,
        "notification_type": notification_type,
        "updated_at": (created_at or datetime.now(timezone.utc)).isoformat(),
        "occurred_at": _iso_now(),
    }
    return _publish(f"{USER_CHANNEL_PREFIX}{recipient_id}", payload)


def iter_events(user_id: str) -> Iterator[dict[str, Any]]:
    """订阅 SSE 事件。

    使用 get_message(timeout=15) 产生心跳，让代理和浏览器知道连接仍然存活；
    Redis 断开时抛出异常，由 SSE endpoint 结束连接，浏览器会自动重连。
    """
    if not enabled():
        yield {
            "type": "realtime.unavailable",
            "resource_type": "system",
            "resource_id": "realtime",
            "reason": "Redis 未配置或实时同步未启用",
            "occurred_at": _iso_now(),
        }
        return

    client = _client()
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    try:
        try:
            client.ping()
        except Exception as error:  # noqa: BLE001 - 向客户端明确报告实时通道不可用
            logger.warning("[realtime] Redis 连接失败 user=%s error=%s", user_id, error)
            yield {
                "type": "realtime.unavailable",
                "resource_type": "system",
                "resource_id": "realtime",
                "reason": "Redis 连接失败，实时同步暂不可用",
                "occurred_at": _iso_now(),
            }
            return
        pubsub.subscribe(TEAM_CHANNEL, f"{USER_CHANNEL_PREFIX}{user_id}")
        while True:
            message = pubsub.get_message(timeout=HEARTBEAT_SECONDS)
            if message is None:
                yield {"type": "realtime.heartbeat", "occurred_at": _iso_now()}
                continue
            if message.get("type") != "message":
                continue
            raw = message.get("data")
            if not isinstance(raw, str):
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("[realtime] 忽略非法 Redis 事件: %s", raw[:200])
                continue
            if isinstance(event, dict):
                yield event
    finally:
        try:
            pubsub.close()
        finally:
            try:
                client.close()
            except Exception:
                pass


def sse_frame(event: dict[str, Any]) -> str:
    """把事件编码为浏览器 EventSource 可消费的 SSE 帧。"""
    if event.get("type") == "realtime.heartbeat":
        return ": heartbeat\n\n"
    return f"event: team-update\ndata: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
