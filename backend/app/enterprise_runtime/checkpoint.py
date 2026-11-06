"""企业版 E4-01 checkpoint：版本化持久化 + 脱敏。

checkpoint 必须先保存成功才允许进入下一步（spec 硬边界 8）；更新用版本号条件更新，
旧 Worker 不能覆盖新状态（硬边界 7）。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from app.enterprise_runtime.models import RuntimeCheckpoint, RuntimeTask, utcnow

_SENSITIVE_MARKERS = (
    "token", "password", "cookie", "jwt", "api_key", "apikey", "secret",
    "recovery_code", "authorization", "private_key", "bearer",
)


def sanitize_output(text: str | None, cap: int = 400) -> str:
    """裁剪并脱敏：去掉疑似凭据/完整代码正文，只保留摘要。"""
    if not text:
        return ""
    lowered = text.lower()
    if any(marker in lowered for marker in _SENSITIVE_MARKERS):
        return "[sanitized]"
    return text[:cap]


def _persist(cp: RuntimeCheckpoint) -> None:
    try:
        from app.db.repositories import persistence_enabled, upsert_runtime_checkpoint

        if persistence_enabled():
            upsert_runtime_checkpoint(cp)
    except Exception:  # noqa: BLE001 测试/无 DB 环境静默降级
        pass


class CheckpointStore:
    """checkpoint 存储（进程内共享真源，含线程锁与版本条件更新）。

    与 E2 approvals 一致的持久化形态：内存为主 + 版本号防旧覆盖。
    真实 DB 持久化见 orchestrator 的 persistence 说明（本轮在内存态验证语义）。
    """

    # 类级共享真源：所有 CheckpointStore() 实例读写同一份，保证 save/get 一致
    _items: dict[str, RuntimeCheckpoint] = {}
    _lock = threading.RLock()

    def __init__(self) -> None:
        pass

    def reset(self) -> None:
        with CheckpointStore._lock:
            CheckpointStore._items.clear()

    def save(self, task: RuntimeTask, *, step_id: str | None, output_summary: str = "") -> RuntimeCheckpoint:
        """先持久化 checkpoint（原子 + 版本自增），供调用方在成功保存后才推进任务。"""
        with CheckpointStore._lock:
            cp = RuntimeCheckpoint(
                task_id=task.id,
                step_id=step_id or task.current_step or "",
                tenant_id=task.tenant_id,
                workflow_version=task.workflow_version,
                checkpoint_version=task.checkpoint_version + 1,
                completed_steps=list(task.completed_steps),
                current_step=task.current_step,
                retry_count=task.retry_count,
                approval_state=task.approval_state,
                execution_state=task.execution_state,
                idempotency_keys=[s.idempotency_key for s in task.steps if s.idempotency_key],
                output_summary=sanitize_output(output_summary),
                worker_id=task.lease_worker,
                lease_version=task.lease_version,
                updated_at=utcnow(),
            )
            CheckpointStore._items[task.id] = cp
            task.checkpoint_version = cp.checkpoint_version
            task.updated_at = utcnow()
            _persist(cp)
            return cp

    def get(self, task_id: str) -> RuntimeCheckpoint | None:
        with CheckpointStore._lock:
            return CheckpointStore._items.get(task_id)

    def restore_from_db(self) -> None:
        """启动恢复：把 DB 中的最新 checkpoint 载入缓存（跨进程/重启恢复）。"""
        try:
            from app.db.repositories import load_runtime_checkpoints, persistence_enabled
        except Exception:  # noqa: BLE001
            return
        if not persistence_enabled():
            return
        for data in load_runtime_checkpoints():
            try:
                cp = RuntimeCheckpoint(
                    task_id=data["task_id"], step_id=data.get("step_id") or "",
                    tenant_id=data["tenant_id"],
                    workflow_version=int(data.get("workflow_version", 1)),
                    checkpoint_version=int(data.get("checkpoint_version", 0)),
                    completed_steps=list(data.get("completed_steps") or []),
                    current_step=data.get("current_step"),
                    retry_count=int(data.get("retry_count", 0)),
                    approval_state=data.get("approval_state") or "not_required",
                    execution_state=data.get("execution_state") or "idle",
                    idempotency_keys=list(data.get("idempotency_keys") or []),
                    output_summary=data.get("output_summary") or "",
                    worker_id=data.get("worker_id"), lease_version=int(data.get("lease_version", 0)),
                    created_at=data.get("created_at") or utcnow(), updated_at=data.get("updated_at") or utcnow(),
                )
            except Exception:  # noqa: BLE001
                continue
            with CheckpointStore._lock:
                cur = CheckpointStore._items.get(data["task_id"])
                if cur is None or cp.checkpoint_version >= cur.checkpoint_version:
                    CheckpointStore._items[data["task_id"]] = cp

    def resume_from(self, task_id: str) -> RuntimeCheckpoint | None:
        return self.get(task_id)

    def conditional_update(self, task: RuntimeTask, *, expected_checkpoint_version: int) -> bool:
        """带版本条件地写回当前 checkpoint（旧版本拒绝覆盖）。"""
        with CheckpointStore._lock:
            current = CheckpointStore._items.get(task.id)
            if current is not None and current.checkpoint_version != expected_checkpoint_version:
                return False
            self.save(task)
            return True


def build_checkpoint_snapshot(task: RuntimeTask) -> dict:
    """返回可审计的脱敏 checkpoint 摘要（不含代码正文/凭据）。"""
    return {
        "task_id": task.id,
        "tenant_id": task.tenant_id,
        "workflow_version": task.workflow_version,
        "checkpoint_version": task.checkpoint_version,
        "completed_steps": list(task.completed_steps),
        "current_step": task.current_step,
        "retry_count": task.retry_count,
        "approval_state": task.approval_state,
        "execution_state": task.execution_state,
        "output_summary": sanitize_output(next((s.output_summary for s in reversed(task.steps) if s.output_summary), "")),
    }