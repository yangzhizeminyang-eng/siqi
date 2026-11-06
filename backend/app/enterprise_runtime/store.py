"""企业版 E4-01 runtime store：任务记录 + 原子租约/抢占 + 幂等。

抢占/租约语义与 E2 approvals 一致：条件版本（expected_version）下更新，只有一个实例能抢到。
内存态为主（便于确定性 TDD）；checkpoint 见 checkpoint.py。
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone

from app.enterprise_runtime.models import (
    ExecutionLease,
    RuntimeStepStatus,
    RuntimeTask,
    RuntimeTaskStatus,
    utcnow,
)

_TASKS: dict[str, RuntimeTask] = {}
_BY_IDEMPOTENCY: dict[tuple[str, str], str] = {}  # (tenant_id, idempotency_key) -> task_id
_LOCK = threading.RLock()
_LEASES: dict[tuple[str, str], ExecutionLease] = {}  # (task_id, step_id) -> lease

# 步骤租约 TTL（OPT-13）：崩溃恢复延迟 ≈ TTL；真实长步骤可通过该环境变量调大。
ENTERPRISE_STEP_LEASE_DEFAULT_TTL_SECONDS = 300.0


def step_lease_ttl_seconds() -> float:
    raw = os.getenv("ENTERPRISE_STEP_LEASE_TTL_SECONDS", str(ENTERPRISE_STEP_LEASE_DEFAULT_TTL_SECONDS))
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return ENTERPRISE_STEP_LEASE_DEFAULT_TTL_SECONDS


def reset_store() -> None:
    with _LOCK:
        _TASKS.clear()
        _BY_IDEMPOTENCY.clear()
        _LEASES.clear()


def get(task_id: str) -> RuntimeTask | None:
    with _LOCK:
        return _TASKS.get(task_id)


def get_by_idempotency(tenant_id: str, idempotency_key: str) -> RuntimeTask | None:
    with _LOCK:
        tid = _BY_IDEMPOTENCY.get((tenant_id, idempotency_key))
        return _TASKS.get(tid) if tid else None


def put(task: RuntimeTask) -> RuntimeTask:
    with _LOCK:
        _TASKS[task.id] = task
        if task.idempotency_key:
            _BY_IDEMPOTENCY[(task.tenant_id, task.idempotency_key)] = task.id
        _persist(task)
    return task


def _persist(task: RuntimeTask) -> None:
    try:
        from app.db.repositories import persistence_enabled, upsert_runtime_task

        if persistence_enabled():
            upsert_runtime_task(task)
    except Exception:  # noqa: BLE001 测试/无 DB 环境静默降级
        pass


def list_by_tenant(tenant_id: str) -> list[RuntimeTask]:
    with _LOCK:
        return [t for t in _TASKS.values() if t.tenant_id == tenant_id]


def claim_step(task_id: str, step_id: str, worker_id: str, *, expected_version: int,
               lease_ttl_seconds: float = 300.0) -> tuple[str, RuntimeTask | None]:
    """原子抢占某 step 的执行租约：返回 ("claimed"|"lost", task)。

    同一 step 只有一个 Worker 能抢到（DB 模式用条件 UPDATE；内存模式用单写者锁 + 版本）。
    """
    from app.db.repositories import persistence_enabled, claim_runtime_step
    if persistence_enabled():
        ok = claim_runtime_step(task_id, expected_version, worker_id, lease_ttl_seconds)
        if not ok:
            refreshed = _refresh_from_db(task_id)
            return "lost", refreshed
        with _LOCK:
            task = _TASKS.get(task_id)
            if task is None:
                task = RuntimeTask(id=task_id)
            task.lease_worker = worker_id
            task.lease_version += 1
            task.version = expected_version + 1
            task.updated_at = utcnow()
            _TASKS[task_id] = task
            _LEASES[(task_id, step_id)] = ExecutionLease(
                task_id=task_id, step_id=step_id, worker_id=worker_id,
                lease_version=task.lease_version,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=lease_ttl_seconds))
            return "claimed", task
    with _LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            return "lost", None
        if task.version != expected_version:
            return "lost", task
        now = datetime.now(timezone.utc)
        lease = _LEASES.get((task_id, step_id))
        if lease is not None and lease.expires_at > now and lease.worker_id != worker_id:
            return "lost", task
        task.lease_worker = worker_id
        task.lease_version = task.lease_version + 1
        task.version += 1
        task.updated_at = utcnow()
        _LEASES[(task_id, step_id)] = ExecutionLease(
            task_id=task_id, step_id=step_id, worker_id=worker_id,
            lease_version=task.lease_version,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=lease_ttl_seconds),
        )
        _TASKS[task_id] = task
        _persist(task)
        return "claimed", task


def _steps_from_data(data: dict) -> list:
    from app.enterprise_runtime.models import RuntimeStep, RuntimeStepStatus, FailureKind
    out: list = []
    for item in data.get("steps") or []:
        try:
            out.append(RuntimeStep(
                step_id=item["step_id"], task_id=item.get("task_id") or "", tenant_id=item.get("tenant_id") or "",
                name=item["name"], agent_type=item.get("agent_type"),
                status=RuntimeStepStatus(item.get("status", "pending")),
                retry_count=int(item.get("retry_count", 0)), max_retries=int(item.get("max_retries", 3)),
                backoff_seconds=float(item.get("backoff_seconds", 1.0)),
                failure_kind=FailureKind(item["failure_kind"]) if item.get("failure_kind") else None,
                failure_reason=item.get("failure_reason"), output_summary=item.get("output_summary") or "",
                idempotency_key=item.get("idempotency_key") or "", worker_id=item.get("worker_id"),
                repair_round=int(item.get("repair_round", 0)), agent_attempt=int(item.get("agent_attempt", 0)),
                return_to=item.get("return_to"), return_reason=item.get("return_reason"),
            ))
        except Exception:  # noqa: BLE001
            continue
    return out


def _refresh_from_db(task_id: str) -> RuntimeTask | None:
    """DB 模式下从真源刷新任务（抢占失败/跨进程变更时回读最新状态）。"""
    try:
        from app.db.repositories import load_runtime_task
    except Exception:  # noqa: BLE001
        return _TASKS.get(task_id)
    data = load_runtime_task(task_id)
    if not data:
        return None
    task = RuntimeTask(
        id=data["id"], tenant_id=data["tenant_id"],
        project_id=data.get("project_id") or "", repository_id=data.get("repository_id") or "",
        title=data.get("title") or "", status=RuntimeTaskStatus(data["status"]),
        version=int(data.get("version", 1)), workflow_version=int(data.get("workflow_version", 1)),
        checkpoint_version=int(data.get("checkpoint_version", 0)),
        current_step=data.get("current_step"),
        completed_steps=list(data.get("completed_steps") or []),
        retry_count=int(data.get("retry_count", 0)),
        approval_state=data.get("approval_state") or "not_required",
        execution_state=data.get("execution_state") or "idle",
        idempotency_key=data.get("idempotency_key") or "",
        wait_human_reason=data.get("wait_human_reason"),
        takeover=data.get("takeover"),
        lease_worker=data.get("lease_worker"), lease_version=int(data.get("lease_version", 0)),
        repair_round=int(data.get("repair_round", 0) or 0), max_repair_rounds=int(data.get("max_repair_rounds", 2) or 2),
        created_at=data.get("created_at") or utcnow(), updated_at=data.get("updated_at") or utcnow(),
        steps=_steps_from_data(data),
    )
    with _LOCK:
        _TASKS[task_id] = task
    return task


def release_lease(task_id: str, step_id: str, worker_id: str) -> None:
    with _LOCK:
        lease = _LEASES.get((task_id, step_id))
        if lease is not None and lease.worker_id == worker_id:
            _LEASES.pop((task_id, step_id), None)
        _release_db_lease(task_id, worker_id)


def _release_db_lease(task_id: str, worker_id: str) -> None:
    try:
        from app.db.repositories import persistence_enabled, release_runtime_lease

        if persistence_enabled():
            release_runtime_lease(task_id, worker_id)
    except Exception:  # noqa: BLE001
        pass


def touch_lease(task_id: str, step_id: str, worker_id: str, *, ttl_seconds: float = 300.0) -> bool:
    """续约进程内步骤租约：expires_at 推到 now+ttl（只有持有者可续）。"""
    with _LOCK:
        lease = _LEASES.get((task_id, step_id))
        if lease is None or lease.worker_id != worker_id:
            return False
        lease.expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        return True


def reclaim_stale_steps(task_id: str, *, now: datetime | None = None) -> int:
    """回收崩溃执行者遗留的 RUNNING 步骤（OPT-13）：租约过期/缺失 → 重置 PENDING 供重做。

    - DB 模式走 repositories 的条件 UPDATE（version CAS + 租约过期条件）保证跨进程原子，
      成功后回读真源；
    - 内存模式按进程内租约过期判断（缺租约的 RUNNING 视为崩溃残留）；
    - 返回回收的步骤数。租约未过期的 RUNNING 步骤视作活跃执行者，不回收。

    F3 记账一致性：回收同时把 step.agent_attempt 前进一位（崩溃的尝试计入编号，
    重做按 agent_attempt+1 计算 attempt → 新幂等键，不再与悬挂 run 相撞），并把该任务
    遗留的 running AgentRun 落终态 failed（进程内 + DB 兜底清扫）。
    """
    moment = now or datetime.now(timezone.utc)
    from app.enterprise_runtime.artifacts import runs as runs_store

    try:
        from app.db.repositories import persistence_enabled, reclaim_stale_runtime_steps

        if persistence_enabled():
            reclaimed = reclaim_stale_runtime_steps(task_id, moment)
            if reclaimed:
                _refresh_from_db(task_id)
                runs_store.fail_dangling_runs(task_id)
            return reclaimed
    except Exception:  # noqa: BLE001 DB 异常降级内存判断（不阻断执行）
        pass
    from app.enterprise_runtime import state_machine as sm

    reclaimed_sids: list[str] = []
    with _LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            return 0
        count = 0
        for step in task.steps:
            if step.status != RuntimeStepStatus.RUNNING:
                continue
            lease = _LEASES.get((task_id, step.step_id))
            if lease is not None and lease.expires_at > moment:
                continue
            try:
                sm.transition_step(step, RuntimeStepStatus.PENDING)
            except Exception:  # noqa: BLE001 兜底：状态机异常也不阻断回收
                step.status = RuntimeStepStatus.PENDING
            step.agent_attempt += 1
            _LEASES.pop((task_id, step.step_id), None)
            reclaimed_sids.append(step.step_id)
            count += 1
        if count:
            task.version += 1
            task.updated_at = utcnow()
            _persist(task)
    for sid in reclaimed_sids:
        runs_store.fail_dangling_runs(task_id, step_id=sid)
    return count


def list_stale(now: datetime) -> list[RuntimeTask]:
    """回收过期租约的 executing 任务（worker 崩溃后由新 worker 重新抢占）。"""
    with _LOCK:
        stale: list[RuntimeTask] = []
        for (tid, sid), lease in list(_LEASES.items()):
            if lease.expires_at <= now:
                task = _TASKS.get(tid)
                if task is not None:
                    stale.append(task)
                _LEASES.pop((tid, sid), None)
        return stale


def set_status(task: RuntimeTask, status: RuntimeTaskStatus) -> None:
    with _LOCK:
        task.status = status
        task.version += 1
        task.updated_at = utcnow()
        _TASKS[task.id] = task
        _persist(task)


def restore_from_db() -> None:
    """启动恢复：把 DB 真源中的 runtime 任务载入内存工作缓存（跨进程/重启恢复）。"""
    try:
        from app.db.repositories import load_runtime_tasks, persistence_enabled
    except Exception:  # noqa: BLE001
        return
    if not persistence_enabled():
        return
    with _LOCK:
        for data in load_runtime_tasks():
            task = RuntimeTask(
                id=data["id"], tenant_id=data["tenant_id"],
                project_id=data.get("project_id") or "", repository_id=data.get("repository_id") or "",
                title=data.get("title") or "", status=RuntimeTaskStatus(data["status"]),
                version=int(data.get("version", 1)), workflow_version=int(data.get("workflow_version", 1)),
                checkpoint_version=int(data.get("checkpoint_version", 0)),
                current_step=data.get("current_step"),
                completed_steps=list(data.get("completed_steps") or []),
                retry_count=int(data.get("retry_count", 0)),
                approval_state=data.get("approval_state") or "not_required",
                execution_state=data.get("execution_state") or "idle",
                idempotency_key=data.get("idempotency_key") or "",
                wait_human_reason=data.get("wait_human_reason"),
                takeover=data.get("takeover"),
                lease_worker=data.get("lease_worker"), lease_version=int(data.get("lease_version", 0)),
                created_at=data.get("created_at") or utcnow(), updated_at=data.get("updated_at") or utcnow(),
                steps=_steps_from_data(data),
            )
            _TASKS[task.id] = task
            if task.idempotency_key:
                _BY_IDEMPOTENCY[(task.tenant_id, task.idempotency_key)] = task.id