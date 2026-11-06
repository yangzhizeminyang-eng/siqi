"""企业版 E2-01 审批申请内存真源 + DB 写透/回载（与 tenancy.store 同款模式）。"""

from __future__ import annotations

from app.approvals.models import ApprovalApplicationRecord, CLAIMABLE_STATUSES

APPROVALS: dict[str, ApprovalApplicationRecord] = {}
# 审批单粒度互斥锁（进程内；多副本需 DB 行锁/版本号兜底，见文档残余风险）
_LOCKS: dict[str, object] = {}
_IDEMPOTENCY_LOCKS: dict[tuple[str, str], object] = {}
_LOCKS_GUARD = __import__("threading").Lock()


def reset_store() -> None:
    with _LOCKS_GUARD:
        APPROVALS.clear()
        _LOCKS.clear()
        _IDEMPOTENCY_LOCKS.clear()


def row_lock(approval_id: str) -> object:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(approval_id)
        if lock is None:
            lock = __import__("threading").Lock()
            _LOCKS[approval_id] = lock
        return lock


def idempotency_lock(tenant_id: str, idempotency_key: str) -> object:
    """同租户同幂等键串行化创建，避免并发请求生成多个审批单。"""
    key = (tenant_id, idempotency_key)
    with _LOCKS_GUARD:
        lock = _IDEMPOTENCY_LOCKS.get(key)
        if lock is None:
            lock = __import__("threading").Lock()
            _IDEMPOTENCY_LOCKS[key] = lock
        return lock


def _persist(record: ApprovalApplicationRecord) -> ApprovalApplicationRecord:
    try:
        from app.db.repositories import persistence_enabled, upsert_approval

        if persistence_enabled():
            existing = upsert_approval(record)
            if existing is not None:
                return ApprovalApplicationRecord(**existing)
    except Exception:  # noqa: BLE001 持久化失败不影响内存真源
        pass
    return record


def put(record: ApprovalApplicationRecord) -> ApprovalApplicationRecord:
    persisted = _persist(record)
    if persisted.id != record.id:
        APPROVALS.pop(record.id, None)
    APPROVALS[persisted.id] = persisted
    return persisted


def get(approval_id: str) -> ApprovalApplicationRecord | None:
    record = APPROVALS.get(approval_id)
    if record is not None:
        return record
    try:
        from app.db.repositories import load_approval, persistence_enabled

        if not persistence_enabled():
            return None
        item = load_approval(approval_id)
        if item is None:
            return None
        record = ApprovalApplicationRecord(**item)
        APPROVALS[record.id] = record
        return record
    except Exception:  # noqa: BLE001 数据库未就绪时保留内存模式语义
        return None


def find_by_idempotency(tenant_id: str, idempotency_key: str) -> ApprovalApplicationRecord | None:
    record = next((a for a in APPROVALS.values()
                   if a.tenant_id == tenant_id and a.idempotency_key == idempotency_key), None)
    if record is not None:
        return record
    try:
        from app.db.repositories import find_approval_by_idempotency, persistence_enabled

        if not persistence_enabled():
            return None
        item = find_approval_by_idempotency(tenant_id, idempotency_key)
        if item is None:
            return None
        record = ApprovalApplicationRecord(**item)
        APPROVALS[record.id] = record
        return record
    except Exception:  # noqa: BLE001 数据库未就绪时保留内存模式语义
        return None


def restore_from_db() -> None:
    """启动时从 DB 恢复审批申请；DB 空则空启动。"""
    try:
        from app.db.repositories import load_approvals, persistence_enabled
    except Exception:  # noqa: BLE001
        return
    if not persistence_enabled():
        return
    try:
        rows = load_approvals()
    except Exception:  # noqa: BLE001
        return
    for item in rows:
        APPROVALS[item["id"]] = ApprovalApplicationRecord(**item)


def refresh_or_get(approval_id: str) -> ApprovalApplicationRecord | None:
    """DB 模式：以 DB 为权威重读并回填内存缓存；内存模式直接返回。"""
    record = APPROVALS.get(approval_id)
    try:
        from app.db.repositories import load_approval, persistence_enabled

        if persistence_enabled():
            item = load_approval(approval_id)
            if item is not None:
                updated = ApprovalApplicationRecord(**item)
                APPROVALS[approval_id] = updated
                return updated
    except Exception:  # noqa: BLE001
        pass
    return record


def claim_execution(approval_id: str, expected_version: int, attempt_id: str, started_at) -> tuple[str, ApprovalApplicationRecord | None]:
    """抢占执行权（多副本安全）：返回 ("claimed"|"lost", record)。

    DB 模式用条件 UPDATE（源态+version）抢占，只有抢到的实例能触发副作用；
    内存模式用行锁 + 版本守卫模拟同一语义。
    """
    try:
        from app.db.repositories import claim_approval_execution, persistence_enabled

        if persistence_enabled():
            ok = claim_approval_execution(approval_id, expected_version, attempt_id, started_at)
            return ("claimed" if ok else "lost"), refresh_or_get(approval_id)
    except Exception:  # noqa: BLE001
        pass
    with row_lock(approval_id):
        record = APPROVALS.get(approval_id)
        if record is None or record.approval_status not in CLAIMABLE_STATUSES or record.version != expected_version:
            return "lost", record
        record.approval_status = "executing"
        record.version += 1
        record.execution_attempt_id = attempt_id
        record.started_at = started_at
        record.updated_at = started_at
        APPROVALS[record.id] = record
        return "claimed", record


def settle_execution(approval_id: str, attempt_id: str, *, status: str,
                     result: dict | None = None, error: str | None = None,
                     finished_at) -> bool:
    """回写执行结果（幂等）：仅 executing 且 attempt 匹配时生效。"""
    try:
        from app.db.repositories import persistence_enabled, settle_approval_execution

        if persistence_enabled():
            ok = settle_approval_execution(approval_id, attempt_id, status=status,
                                           result=result, error=error, finished_at=finished_at)
            if ok:
                refresh_or_get(approval_id)
            return ok
    except Exception:  # noqa: BLE001
        pass
    with row_lock(approval_id):
        record = APPROVALS.get(approval_id)
        if record is None or record.approval_status != "executing" or record.execution_attempt_id != attempt_id:
            return False
        record.approval_status = status
        record.finished_at = finished_at
        record.updated_at = finished_at
        record.version += 1
        if status == "executed":
            record.executor_result = result or {}
            record.executed_at = finished_at
            record.execution_error = None
        else:
            # 失败/回退：状态回到可重试源（approved），失败原因可追踪
            record.execution_error = error
            if result is not None:
                record.executor_result = result
        return True
