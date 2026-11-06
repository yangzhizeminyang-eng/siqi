"""企业版 E4-02 AgentRun：记录每次独立 Agent 调用的完整轨迹（含修复轮次）。

run_id 全局唯一；幂等键 (tenant_id, idempotency_key) 保证重试/重复投递不产生重复记录。
每次 Agent invoke（含 review/test 修复轮的重新调用）都会新增一条 AgentRun。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

RunStatus = Literal["running", "succeeded", "failed", "blocked"]


class AgentRun(BaseModel):
    run_id: str
    task_id: str
    tenant_id: str
    agent_type: str
    step_id: str | None = None
    attempt: int = 1
    repair_round: int = 0
    status: RunStatus = "running"
    prompt_version: str = "1"
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    policy_context: dict = Field(default_factory=dict)
    model: str = ""
    usage: dict = Field(default_factory=dict)
    error_reason: str | None = None
    idempotency_key: str = ""
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None


_RUNS: dict[str, AgentRun] = {}
_BY_IDEMPOTENCY: dict[tuple[str, str], str] = {}
_LOCK = threading.RLock()


def reset_store() -> None:
    with _LOCK:
        _RUNS.clear()
        _BY_IDEMPOTENCY.clear()


def get(run_id: str) -> AgentRun | None:
    with _LOCK:
        return _RUNS.get(run_id)


def list_by_task(tenant_id: str, task_id: str) -> list[AgentRun]:
    with _LOCK:
        return [r for r in _RUNS.values() if r.tenant_id == tenant_id and r.task_id == task_id]


def put(run: AgentRun) -> AgentRun:
    with _LOCK:
        existing = _RUNS.get(run.run_id)
        if existing is not None:
            # 终态：把（可能原地已改的）终态写回 DB，避免 DB 停在 running（constraint #9）
            if existing.status in ("succeeded", "failed"):
                _persist(existing)
                return existing
            return existing  # 仍在 running 的重复投递：幂等返回，不重复写
        if run.idempotency_key:
            dup = _BY_IDEMPOTENCY.get((run.tenant_id, run.idempotency_key))
            if dup is not None and dup in _RUNS:
                return _RUNS[dup]  # 重复投递 → 返回既有
        _RUNS[run.run_id] = run
        if run.idempotency_key:
            _BY_IDEMPOTENCY[(run.tenant_id, run.idempotency_key)] = run.run_id
        _persist(run)
        return run


def _persist(run: AgentRun) -> None:
    try:
        from app.db.repositories import persistence_enabled, upsert_agent_run

        if persistence_enabled():
            upsert_agent_run(run)
    except Exception:  # noqa: BLE001
        pass


def restore_from_db() -> int:
    try:
        from app.db.repositories import load_all_agent_runs, persistence_enabled
    except Exception:  # noqa: BLE001
        return 0
    if not persistence_enabled():
        return 0
    rows = load_all_agent_runs()
    with _LOCK:
        for item in rows:
            try:
                run = AgentRun(
                    run_id=item["run_id"], task_id=item["task_id"], tenant_id=item["tenant_id"],
                    agent_type=item["agent_type"], step_id=item.get("step_id"),
                    attempt=int(item.get("attempt", 1) or 1), repair_round=int(item.get("repair_round", 0) or 0),
                    status=item.get("status") or "running",
                    prompt_version=item.get("prompt_version") or "1",
                    input_refs=list(item.get("input_refs") or []),
                    output_refs=list(item.get("output_refs") or []),
                    policy_context=item.get("policy_context") or {},
                    model=item.get("model") or "", usage=item.get("usage") or {},
                    error_reason=item.get("error_reason"),
                    idempotency_key=item.get("idempotency_key") or "",
                    started_at=item.get("started_at") or datetime.now(timezone.utc),
                    finished_at=item.get("finished_at"),
                )
            except Exception:  # noqa: BLE001
                continue
            _RUNS[run.run_id] = run
            if run.idempotency_key:
                _BY_IDEMPOTENCY[(run.tenant_id, run.idempotency_key)] = run.run_id
    return len(rows)


def mark_stale_failed(max_running_age_seconds: int = 3600) -> int:
    """未完成 AgentRun 的 stale recovery（C9）：超时仍未 finish 的 running 置为 failed。"""
    now = datetime.now(timezone.utc)
    n = 0
    with _LOCK:
        for run in _RUNS.values():
            if run.status == "running" and run.finished_at is None:
                if (now - run.started_at).total_seconds() > max_running_age_seconds:
                    run.status = "failed"
                    run.error_reason = run.error_reason or "stale: agent run unfinished beyond cutoff"
                    run.finished_at = now
                    n += 1
    return n


CRASH_RECLAIMED_REASON = "crash: step lease reclaimed (OPT-13), superseded by retry"


def fail_dangling_runs(task_id: str, *, reason: str = CRASH_RECLAIMED_REASON,
                       step_id: str | None = None) -> int:
    """崩溃回收（OPT-13）：租约过期判定执行者死亡后，把遗留的 running AgentRun 落终态 failed。

    必要性：崩溃发生在 executor.run 内时 step.agent_attempt 未回写，重做会产生
    agent_attempt+1 的新幂等键；若旧 run 仍悬挂 running，账本会同时留下「永不结束」的
    幽灵记录。进程内命中即更新落库；DB 侧再兜底清扫（覆盖本进程内存未恢复的遗留行）。
    """
    now = datetime.now(timezone.utc)
    n = 0
    with _LOCK:
        for run in _RUNS.values():
            if run.task_id != task_id or run.status != "running":
                continue
            if step_id is not None and run.step_id != step_id:
                continue
            run.status = "failed"
            run.error_reason = reason
            run.finished_at = now
            _persist(run)
            n += 1
    try:
        from app.db.repositories import mark_running_agent_runs_failed, persistence_enabled

        if persistence_enabled():
            n += mark_running_agent_runs_failed(task_id, reason, step_id=step_id)
    except Exception:  # noqa: BLE001 兜底清扫失败不阻断回收主流程
        pass
    return n