"""统一 UTC 时钟 seam。

审计事件必须使用「事件发生时刻」，不得复用模块导入时固定的时间常量。
测试可通过 `set_now_provider()` 冻结/推进时钟；生产默认 `datetime.now(timezone.utc)`。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

_NOW_PROVIDER: Callable[[], datetime] | None = None


def utcnow() -> datetime:
    provider = _NOW_PROVIDER
    if provider is not None:
        value = provider()
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def set_now_provider(provider: Callable[[], datetime] | None) -> None:
    global _NOW_PROVIDER
    _NOW_PROVIDER = provider


def reset_now_provider() -> None:
    set_now_provider(None)
