"""FIX-01 在线评测运行记录（EvaluationRun）store：租户隔离 + 状态推进 + 可重置。

真源契约：
- 持久化开启（`USE_SQLALCHEMY=true`，或显式 `ENTERPRISE_SHARED_PERSISTENCE=true`）：
  SQLAlchemy 表 `enterprise_rag_evaluation_runs` 是跨进程唯一真源；内存 dict 只是写穿缓存，
  `get`/`list` 始终回读数据库。API 创建的 run 因此可被独立 Worker 看到，Worker 的终态也能被 API 读回。
- 持久化关闭（本地/测试默认）：保留原有纯内存路径，行为与历史一致。
- 终态幂等：重复 delivery 不重复执行；`running` 超过租约窗口视为崩溃残留，可被重新 claim。
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

RUN_STATUSES = ("queued", "running", "succeeded", "blocked", "failed")
TERMINAL_STATUSES = frozenset({"succeeded", "blocked", "failed"})
DEFAULT_RUN_LEASE_SECONDS = 300.0

_LOCK = threading.RLock()
_RUNS: dict[str, "EvaluationRun"] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lease_seconds() -> float:
    raw = (os.environ.get("RAG_EVAL_RUN_LEASE_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_RUN_LEASE_SECONDS
    try:
        return max(30.0, float(raw))
    except ValueError:
        return DEFAULT_RUN_LEASE_SECONDS


def lease_seconds() -> float:
    """当前 run 执行租约（秒）；Worker 崩溃后的接管窗口。"""
    return _lease_seconds()


def _persistent() -> bool:
    try:
        from app.db.repositories import shared_persistence_enabled

        return shared_persistence_enabled()
    except Exception:  # noqa: BLE001 无 DB 配置时保持内存路径
        return False


class EvaluationRun(BaseModel):
    """一次在线评测运行：触发即建（queued），Worker/内联推进到终态。

    - status：queued → running → succeeded | blocked | failed（blocked = require_milvus 语义未满足）；
    - validation_status：报告口径 local_only / verified / blocked（与评测引擎一致）；
    - summary：脱敏简报（总数/通过/核心指标/延迟），详情以报告文件为准；
    - attempts：被 claim 的次数（重复 delivery/崩溃接管的可审计计数）。
    """

    run_id: str
    tenant_id: str
    dataset: str
    retrieval_mode: str = "hybrid"
    top_k: int = 8
    execution_scope: str = "isolated"
    status: str = "queued"
    validation_status: str = ""
    report_name: str | None = None
    summary: dict = Field(default_factory=dict)
    error: str = ""
    requested_by: str = ""
    requested_role: str = "developer"
    attempts: int = 0
    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = Field(default_factory=_now)

    def to_public(self) -> dict:
        return self.model_dump(mode="json")


def reset_store() -> None:
    with _LOCK:
        _RUNS.clear()


def _cache(run: EvaluationRun) -> EvaluationRun:
    with _LOCK:
        _RUNS[run.run_id] = run
    return run


def create(run: EvaluationRun) -> EvaluationRun:
    """创建 run；持久化开启时 DB 为真源，同 run_id 重复创建幂等返回原记录。"""
    if _persistent():
        from app.db import shared_stores

        fresh = EvaluationRun.model_validate(shared_stores.create_eval_run(run))
        return _cache(fresh)
    with _LOCK:
        existing = _RUNS.get(run.run_id)
        if existing is not None:
            return existing
        _RUNS[run.run_id] = run
        return run


def get(run_id: str) -> EvaluationRun | None:
    if _persistent():
        from app.db import shared_stores

        data = shared_stores.load_eval_run(run_id)
        if data is None:
            return None
        return _cache(EvaluationRun.model_validate(data))
    with _LOCK:
        return _RUNS.get(run_id)


def save(run: EvaluationRun) -> EvaluationRun:
    return create(run) if _persistent() else _cache(run)


def update(run_id: str, **fields) -> EvaluationRun | None:
    if _persistent():
        from app.db import shared_stores

        data = shared_stores.update_eval_run(run_id, **fields)
        if data is None:
            return None
        return _cache(EvaluationRun.model_validate(data))
    with _LOCK:
        run = _RUNS.get(run_id)
        if run is None:
            return None
        updated = run.model_copy(update=fields)
        _RUNS[run_id] = updated
        return updated


def claim_for_execution(run_id: str, *, now: datetime | None = None, lease_seconds: float | None = None
                        ) -> tuple[str, EvaluationRun | None]:
    """原子 claim：返回 (missing|terminal|busy|claimed, run)。

    - terminal：已成功/失败/blocked，重复投递直接返回终态；
    - busy：另一个 Worker 正在租约内执行，重复 delivery 不并发执行；
    - claimed：queued 或租约过期的 running，可安全推进。
    """
    moment = now or _now()
    stale_before = moment - timedelta(seconds=lease_seconds or _lease_seconds())
    if _persistent():
        from app.db import shared_stores

        outcome, data = shared_stores.claim_eval_run(
            run_id, now=moment, stale_before=stale_before)
        if data is None:
            return outcome, None
        return outcome, _cache(EvaluationRun.model_validate(data))
    with _LOCK:
        run = _RUNS.get(run_id)
        if run is None:
            return "missing", None
        if run.status in TERMINAL_STATUSES:
            return "terminal", run
        if run.status == "running" and run.started_at is not None:
            started = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=timezone.utc)
            if started > stale_before:
                return "busy", run
        updated = run.model_copy(update={
            "status": "running",
            "started_at": moment,
            "finished_at": None,
            "attempts": int(run.attempts or 0) + 1,
            "updated_at": moment,
        })
        _RUNS[run_id] = updated
        return "claimed", updated


def list_by_tenant(tenant_id: str, *, limit: int = 50) -> list[EvaluationRun]:
    if _persistent():
        from app.db import shared_stores

        return [EvaluationRun.model_validate(item)
                for item in shared_stores.load_eval_runs(tenant_id, limit=limit)]
    with _LOCK:
        runs = [r for r in _RUNS.values() if r.tenant_id == tenant_id]
    runs.sort(key=lambda r: r.created_at, reverse=True)
    return runs[: max(1, int(limit))]
