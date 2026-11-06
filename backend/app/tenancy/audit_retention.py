"""OPT-14 审计保留（retention）：策略读取、只读计划与受控清理。

安全口径（默认绝不误删）：
- 策略 `AUDIT_RETENTION_DAYS=0`（默认）= 永久保留；`days<=0` 的清理调用直接拒绝；
- 保留窗口低于 `AUDIT_RETENTION_MIN_DAYS`（默认 30 天）默认拒绝，需显式 `allow_below_min` 放行；
- 单次删除受 `AUDIT_RETENTION_MAX_DELETE`（默认 10000）封顶，显式 `max_delete` 只能收紧不能放宽；
- 默认 dry-run，只有 `dry_run=False` 才真正删除（内存缓存 + 持久化真源同步）。

范围说明：保留是系统级运维动作（全部租户口径），不按租户切分——这是与读接口
（租户作用域）刻意不同的设计，见 docs/project-development/126。

时区口径：事件时间统一按 aware UTC 比较；SQLite/MySQL DATETIME 按 UTC 挂钟存 naive，
读回后由 `_as_utc` 补时区，避免 naive/aware 混比。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _as_utc(value: datetime) -> datetime:
    """统一为 aware UTC：naive 视为 UTC 挂钟（与落库口径一致）。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def retention_policy() -> dict[str, int]:
    """当前保留策略（来自运行时配置，集中出参供 API/CLI/测试读取）。"""
    from app.core.runtime_config import load_runtime_settings

    settings = load_runtime_settings()
    return {
        "days": settings.audit_retention_days,
        "min_days": settings.audit_retention_min_days,
        "max_delete": settings.audit_retention_max_delete,
    }


def _load_events() -> list[Any]:
    """审计事件真源：持久化开启读 DB，否则读内存缓存（与 reads.list_audit_events 同口径）。"""
    from app import main
    from app.db.repositories import load_audit_events, shared_persistence_enabled

    if shared_persistence_enabled():
        return [main.AuditEventRecord(**item) for item in load_audit_events()]
    return list(main.AUDIT_EVENTS.values())


def _scan(days: int, now: datetime | None) -> tuple[datetime, list[Any], list[Any]]:
    """返回 (cutoff, 全部事件, 窗口外事件按最早优先排序)。"""
    moment = _as_utc(now or datetime.now(timezone.utc))
    cutoff = moment - timedelta(days=days)
    events = _load_events()
    expired = sorted(
        (event for event in events if _as_utc(event.created_at) < cutoff),
        key=lambda event: _as_utc(event.created_at),
    )
    return cutoff, events, expired


def build_plan(days: int, now: datetime | None = None) -> dict[str, Any]:
    """只读统计：窗口外（早于 cutoff）事件数量与整体时间范围，不修改任何数据。"""
    cutoff, events, expired = _scan(days, now)
    stamps = [_as_utc(event.created_at) for event in events]
    return {
        "days": days,
        "cutoff": cutoff.isoformat(),
        "total": len(events),
        "candidates": len(expired),
        "oldest": min(stamps).isoformat() if stamps else None,
        "newest": max(stamps).isoformat() if stamps else None,
    }


def prune_audit_events(
    days: int,
    *,
    dry_run: bool = True,
    max_delete: int | None = None,
    allow_below_min: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """受控清理：删除早于 `now - days` 的审计事件（内存缓存 + 持久化真源）。

    返回字段：deleted（实际删除数）、deleted_memory / deleted_db（分侧计数）、
    candidates（窗口外总数）、truncated（是否因封顶未删完）、refused / reason（拒绝原因）、
    error_code（参数/策略拒绝的可区分错误码）、effective_max_delete / capped_by_policy。

    `max_delete=0` 是非法参数（不是「删 1 条」）：显式 0/负数在任何 DB/内存变更前拒绝。
    """
    policy = retention_policy()
    result: dict[str, Any] = {
        "days": days,
        "cutoff": None,
        "total": 0,
        "candidates": 0,
        "deleted": 0,
        "deleted_memory": 0,
        "deleted_db": 0,
        "dry_run": dry_run,
        "truncated": False,
        "refused": False,
        "reason": "",
        "error_code": "",
        "effective_max_delete": policy["max_delete"],
        "capped_by_policy": False,
    }

    if max_delete is not None and int(max_delete) <= 0:
        result.update(
            refused=True,
            error_code="invalid_max_delete",
            reason=(
                f"max_delete={max_delete} 非法：必须为正整数（0 不会解释为删除 1 条）；"
                "已拒绝且未修改任何事件"
            ),
        )
        return result
    if days <= 0:
        result["refused"] = True
        result["error_code"] = "invalid_days"
        result["reason"] = "days<=0：0 表示永久保留（策略语义），拒绝执行清理"
        return result
    if days < policy["min_days"] and not allow_below_min:
        result["refused"] = True
        result["error_code"] = "below_min_days"
        result["reason"] = (
            f"days={days} 低于 min_days={policy['min_days']} 防误删下限，"
            "需显式 allow_below_min 放行"
        )
        return result

    # 显式 max_delete 只能收紧封顶，不能放宽到策略上限之外
    requested_cap = policy["max_delete"] if max_delete is None else int(max_delete)
    cap = min(requested_cap, policy["max_delete"])
    result.update(effective_max_delete=cap, capped_by_policy=requested_cap > policy["max_delete"])

    cutoff, events, expired = _scan(days, now)
    targets = expired[:cap]

    result.update(
        cutoff=cutoff.isoformat(),
        total=len(events),
        candidates=len(expired),
        truncated=len(expired) > cap,
    )
    if dry_run:
        return result

    from app import main

    removed_memory = 0
    for event in targets:
        if main.AUDIT_EVENTS.pop(event.id, None) is not None:
            removed_memory += 1

    from app.db.repositories import delete_audit_events_before, shared_persistence_enabled

    removed_db = 0
    if shared_persistence_enabled():
        removed_db = delete_audit_events_before(cutoff, len(targets))

    # 写透缓存下两侧一致；取较大者，避免内存缓存被外部清空时低报实际删除
    result.update(
        deleted=max(removed_memory, removed_db),
        deleted_memory=removed_memory,
        deleted_db=removed_db,
    )
    return result
