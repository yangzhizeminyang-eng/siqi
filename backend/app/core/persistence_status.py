"""业务写透可观测：写库不再静默吞错。

提供：
- 计数统计（按操作×结果累加，内存态，测试可用 reset 清零）；
- 结构化日志（含实体标识、错误码、异常详情）；
- 严格模式 PERSIST_STRICT=true 下写库失败时上抛（拒绝静默丢数据）。

用法：
    from app.core.persistence_status import measure
    def _persist_task(task):
        ok_, code, detail = measure(
            "task", getattr(task, "id", "?"),
            lambda: _run_upsert_task(task),
            entity=type(task).__name__,
        )
        if not ok_:
            raise HTTPException(500, ...) if STRICT else None
"""

from __future__ import annotations

import collections
import logging
import os
import threading
from typing import Any, Callable

logger = logging.getLogger("backend.persistence")

_lock = threading.Lock()
_COUNTERS: collections.Counter = collections.Counter()
_LAST_ERROR: dict[str, str] = {}

OUTCOME_OK = "ok"
OUTCOME_FAIL = "fail"
OUTCOME_SKIP = "skip"


def _strict() -> bool:
    return os.getenv("PERSIST_STRICT", "").strip().lower() in {"1", "true", "yes", "on"}


def measure(op: str, identity: str, work: Callable[[], Any], *, entity: str = "") -> tuple[bool, str, str]:
    """执行一次写透并记录结果。返回 (ok, error_code, detail)。

    - 持久化总开关关闭时记为 skip（测试/降级），不当作失败。
    - 失败时记录日志 + 计数；PERSIST_STRICT=true 时上抛，否则返回 False 供调用方决定。
    """
    from app.db.repositories import persistence_enabled

    if not persistence_enabled():
        _bump(op, OUTCOME_SKIP, "PERSIST_DISABLED")
        return True, "ok", "persistence_disabled"

    try:
        work()
    except Exception as error:  # noqa: BLE001 - 统一捕获写库异常
        code = type(error).__name__.upper()
        detail = f"{type(error).__name__}: {error}"
        _bump(op, OUTCOME_FAIL, code)
        with _lock:
            _LAST_ERROR[f"{op}:{identity}"] = detail
        logger.error(
            "[persist] 写库失败 op=%s entity=%s id=%s error_code=%s detail=%s",
            op, entity or "?", identity, code, detail,
        )
        if _strict():
            raise
        return False, code, detail

    _bump(op, OUTCOME_OK, "OK")
    return True, "ok", ""


def _bump(op: str, outcome: str, code: str) -> None:
    with _lock:
        _COUNTERS[(op, outcome)] += 1
        if outcome != OUTCOME_OK:
            _COUNTERS[(op, f"{outcome}:{code}")] += 1


def metrics() -> dict[str, Any]:
    with _lock:
        flat = {f"{op}:{outcome}": n for (op, outcome), n in _COUNTERS.items()}
        last_errors = dict(_LAST_ERROR)
    return {"counters": flat, "last_errors": last_errors, "strict": _strict()}


def reset() -> None:
    with _lock:
        _COUNTERS.clear()
        _LAST_ERROR.clear()