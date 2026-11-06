"""企业版 E2-01 审批服务：提交/查询/审批/拒绝/取消/过期/执行 + 幂等 + 并发 + 执行前重校验 + 审计。

所有审批判定集中在本文；租户隔离、自审禁拒、终态不可逆、重复执行一次性均在此约束。
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status

from app.approvals import store
from app.approvals.models import (
    DEFAULT_APPROVAL_TTL_SECONDS,
    EXECUTING_TIMEOUT_SECONDS,
    ApprovalApplicationRecord,
    ApprovalApprovePayload,
    ApprovalCancelPayload,
    ApprovalExecutePayload,
    ApprovalRejectPayload,
    ApprovalSubmitPayload,
)
from app.governance.executor import execute_operation
from app.governance.operations import get_operation
from app.governance.policies import can_self_approve
from app.governance.gate import _has_business_permission, gate as _gate
from app.tenancy.context import forbidden, require_resource_tenant
from app.tenancy.permissions import (
    APPROVAL_APPROVE,
    APPROVAL_MANAGE,
    APPROVAL_SUBMIT,
    APPROVAL_VIEW,
    has_permission,
    require_tenant_permission,
)

_SENSITIVE_HINTS = ("token", "password", "secret", "jwt", "cookie", "recovery", "credential", "private")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return f"appr_{uuid.uuid4().hex[:12]}"


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT,
                         detail={"code": 409, "message": message, "data": None})


def _not_found(message: str = "审批申请不存在") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                         detail={"code": 404, "message": message, "data": None})


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                         detail={"code": 422, "message": message, "data": None})


def _scrub_value(value):
    if isinstance(value, dict):
        return {
            str(key): _scrub_value(item)
            for key, item in value.items()
            if not any(hint in str(key).lower() for hint in _SENSITIVE_HINTS)
        }
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    return value


def _scrub(summary: dict | None) -> dict:
    """提交时清洗敏感字段，审计与审批内容绝不落敏感凭据。"""
    if not summary:
        return {}
    return _scrub_value(summary)


def _audit(context, action: str, resource_id: str, summary: str, result: str = "ok") -> None:
    from app import main  # 延迟导入避免循环

    try:
        event = main.AuditEventRecord(
            id=f"audit_{uuid.uuid4().hex[:16]}",
            event_type=f"approval.{action}",
            tenant_id=context.tenant_id,
            resource_type="approval",
            resource_id=str(resource_id)[:80],
            actor_id=context.user_id,
            actor_name="",
            summary=summary[:500],
            diff_summary=[],
            created_at=_now(),
            execution_step_ids=[],
        )
        main.AUDIT_EVENTS[event.id] = event
        from app.db.repositories import persistence_enabled, upsert_audit_event

        if persistence_enabled():
            upsert_audit_event(event)
    except Exception:  # noqa: BLE001
        pass


def _fingerprint(context, resource_type: str, resource_id: str) -> str | None:
    """资源版本指纹：提交时固化，执行前复算，不一致则拒绝执行旧审批。"""
    if resource_type in {"repository", "repositories"}:
        try:
            from app import main

            repo = main.REPOSITORIES.get(resource_id)
            if repo is not None:
                return f"repo:{repo.updated_at.isoformat()}:ver={getattr(repo, 'version', '1')}"
        except Exception:  # noqa: BLE001
            return None
    if resource_type in {"tenant_member", "membership"}:
        try:
            from app.tenancy import store as tenancy_store

            membership = tenancy_store.get_membership(resource_id)
            if membership is not None:
                return (f"member:{membership.status}:{membership.role}:"
                        f"{membership.is_active}:{membership.updated_at.isoformat()}")
        except Exception:  # noqa: BLE001
            return None
    return None


def _expire_if_needed(record: ApprovalApplicationRecord) -> None:
    if record.expires_at is None or record.approval_status not in {"pending", "approved"}:
        return
    if _now() >= record.expires_at:
        record.approval_status = "expired"
        record.updated_at = _now()
        record.version += 1
        store.put(record)


def _recover_stale_executing(record: ApprovalApplicationRecord) -> None:
    """垃圾回收：抢占后超时仍未 settle 的 executing 视为卡死，回收为可重试 approved。

    覆盖「Worker 执行/投递后 settle 写库失败的窄边沿」：若 settle 的数据库写入恰好失败，
    内存被回写但 DB 行停留在 executing，且无任何后续写入恢复。此处按 started_at 超时惰性
    回收（读/审批/执行路径都会调用），避免 DB 永久卡在 executing 导致单不可执行。
    """
    if record.approval_status != "executing" or record.started_at is None:
        return
    if (_now() - record.started_at).total_seconds() < EXECUTING_TIMEOUT_SECONDS:
        return
    record.approval_status = "approved"
    record.finished_at = _now()
    record.execution_error = f"执行超时回收（{EXECUTING_TIMEOUT_SECONDS}s 未 settle），可重试"
    record.updated_at = _now()
    record.version += 1
    store.put(record)


def _security_required(record: ApprovalApplicationRecord) -> bool:
    return record.approval_mode == "security"


def _is_security_approver(context, user) -> bool:
    return (
        getattr(context, "role", None) in {"tenant_owner", "security_admin"}
        and has_permission(context, APPROVAL_MANAGE)
    )


def _validate_target(context, op, resource_type: str, resource_id: str) -> None:
    """提交和执行前统一验证操作与真实资源的契约及租户归属。"""
    if resource_type != op.resource_type:
        raise _bad_request(
            f"操作 {op.operation} 要求 resource_type={op.resource_type}，实际为 {resource_type}"
        )

    from app import main

    if op.resource_type == "repository":
        resource = main.REPOSITORIES.get(resource_id)
        if resource is None:
            raise _not_found("仓库不存在")
        require_resource_tenant(resource.tenant_id, resource_type="repository", resource_id=resource_id)
        return

    if op.resource_type == "team_request":
        resource = main.TEAM_REQUESTS.get(resource_id)
        if resource is None:
            raise _not_found("平台需求不存在")
        require_resource_tenant(resource.tenant_id, resource_type="team_request", resource_id=resource_id)
        return

    if op.resource_type == "tenant_member":
        from app.tenancy import store as tenancy_store

        resource = tenancy_store.get_membership(resource_id)
        if resource is None:
            raise _not_found("租户成员不存在")
        require_resource_tenant(resource.tenant_id, resource_type="tenant_member", resource_id=resource_id)
        return

    if op.resource_type == "tenant":
        if resource_id != context.tenant_id:
            raise forbidden("无权操作其他租户")
        return

    if op.resource_type == "runtime_task":
        from app.enterprise_runtime import store as runtime_store

        resource = runtime_store.get(resource_id)
        if resource is None:
            raise _not_found("Runtime 任务不存在")
        require_resource_tenant(resource.tenant_id, resource_type="runtime_task", resource_id=resource_id)
        return

    raise _bad_request(f"操作 {op.operation} 未配置资源校验器")


def list_approvals(context, user, *, status_filter: str | None = None,
                   risk_filter: str | None = None, scope: str = "all") -> list[ApprovalApplicationRecord]:
    require_tenant_permission(context, APPROVAL_VIEW)
    for record in list(store.APPROVALS.values()):
        if record.tenant_id == context.tenant_id:
            _expire_if_needed(record)
            _recover_stale_executing(record)
    records = [a for a in store.APPROVALS.values() if a.tenant_id == context.tenant_id]
    if status_filter:
        records = [a for a in records if a.approval_status == status_filter]
    if risk_filter:
        records = [a for a in records if a.risk_level == risk_filter]
    if scope == "mine":
        # 我可审批（已启用审批权限 + 待处理 + 非我提交 + 满足自审规则）
        records = [a for a in records
                   if a.approval_status == "pending" and a.actor_id != user.id]
    elif scope == "submitted":
        records = [a for a in records if a.actor_id == user.id]
    return sorted(records, key=lambda a: a.created_at, reverse=True)


def get_approval(context, user, approval_id: str) -> ApprovalApplicationRecord:
    require_tenant_permission(context, APPROVAL_VIEW)
    record = store.get(approval_id)
    if record is None:
        raise _not_found()
    if record.tenant_id != context.tenant_id:
        raise forbidden("无权查看该审批申请")
    _expire_if_needed(record)
    _recover_stale_executing(record)
    return record


def create_approval(context, user, payload: ApprovalSubmitPayload) -> ApprovalApplicationRecord:
    require_tenant_permission(context, APPROVAL_SUBMIT)
    op = get_operation(payload.operation)
    if op is None:
        raise _bad_request("未知治理操作")
    _validate_target(context, op, payload.resource_type, payload.resource_id)
    decision = _gate(context, user, op.operation)
    if decision["decision"] == "deny":  # gate 已抛 403，此处兜底
        raise forbidden(decision.get("reason"))

    with store.idempotency_lock(context.tenant_id, payload.idempotency_key):
        # 幂等：同 (租户, 幂等键) 返回既有审批单
        existing = store.find_by_idempotency(context.tenant_id, payload.idempotency_key)
        if existing is not None:
            _expire_if_needed(existing)
            return existing

        now = _now()
        expires_in = payload.expires_in_seconds or DEFAULT_APPROVAL_TTL_SECONDS
        expires_at = now + timedelta(seconds=expires_in)
        request_summary = _scrub(payload.request_summary)
        fp = _fingerprint(context, payload.resource_type, payload.resource_id)
        mode = op.approval_mode

        record = ApprovalApplicationRecord(
            id=_new_id(),
            tenant_id=context.tenant_id,
            actor_id=user.id,
            actor_name=getattr(user, "name", ""),
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            operation=op.operation,
            title=payload.title or f"{op.operation}（{op.resource_type}:{payload.resource_id}）",
            request_summary=request_summary,
            risk_level=decision["risk_level"],
            decision=decision["decision"],
            approval_status="pending",
            idempotency_key=payload.idempotency_key,
            created_by=user.id,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            resource_version=fp,
            approval_mode=mode,
            approval_meta={"needed": 2 if mode == "dual" else 1, "approved_by": []},
            secondary_confirmation=(mode == "security"),
            version=1,
        )
        persisted = store.put(record)
        if persisted.id != record.id:
            _expire_if_needed(persisted)
            return persisted
        record = persisted
        _audit(context, "submit", record.id,
               f"提交审批 {record.operation} risk={record.risk_level} mode={record.approval_mode}")
        return record


def create_system_approval(*, tenant_id: str, resource_type: str, resource_id: str, operation: str,
                           idempotency_key: str, title: str = "", request_summary: dict | None = None,
                           actor_id: str = "system", actor_name: str = "PlannerAgent",
                           expires_in_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
                           resource_version: str | None = None) -> ApprovalApplicationRecord:
    """系统编排（Runtime/Planner）幂等创建审批单：不经人工提交权限门禁，但复用同一审批服务。

    - 校验 operation 存在（unknown → 409）。
    - 同 (tenant, idempotency_key) 幂等返回既有单。
    - 走 store.put（含 DB 写透 + 多副本追踪），审计 submit。
    供 6-Agent 链 Planner→E2 审批门调用；不绕过审批服务（constraint #7）。
    """
    op = get_operation(operation)
    if op is None:
        raise _bad_request(f"未知治理操作: {operation}")
    with store.idempotency_lock(tenant_id, idempotency_key):
        existing = store.find_by_idempotency(tenant_id, idempotency_key)
        if existing is not None:
            _expire_if_needed(existing)
            return existing
        now = _now()
        record = ApprovalApplicationRecord(
            id=_new_id(),
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_name=actor_name[:64],
            resource_type=resource_type,
            resource_id=resource_id,
            operation=op.operation,
            title=title or f"{op.operation}（{op.resource_type}:{resource_id}）",
            request_summary=_scrub(request_summary or {}),
            risk_level=op.default_risk,
            decision="approval_required",
            approval_status="pending",
            idempotency_key=idempotency_key,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=expires_in_seconds),
            resource_version=resource_version,
            approval_mode=op.approval_mode,
            approval_meta={"needed": 2 if op.approval_mode == "dual" else 1, "approved_by": []},
            secondary_confirmation=(op.approval_mode == "security"),
            version=1,
        )
        persisted = store.put(record)
        _audit_plain(tenant_id, actor_id, "submit", record.id,
                     f"系统创建审批 {record.operation}（{record.resource_type}:{record.resource_id}）")
        return persisted


def preview_governance(context, user, payload: ApprovalSubmitPayload) -> dict:
    require_tenant_permission(context, APPROVAL_SUBMIT)
    op = get_operation(payload.operation)
    if op is None:
        raise _bad_request("未知治理操作")
    _validate_target(context, op, payload.resource_type, payload.resource_id)
    return _gate(
        context,
        user,
        op.operation,
        secondary_confirmation=payload.secondary_confirmation,
    )


def _forward_runtime_decision(record, *, approved: bool) -> None:
    """E4-01 接线：审批决定回写企业 Runtime（resource_type=runtime_task 时）。

    真实 E2 审批通过/拒绝 → runtime.approve_runtime_task(task_id, approved)。
    任务非 WAITING_APPROVAL 时视为幂等 no-op（不抛错）。
    """
    try:
        if getattr(record, "resource_type", "") != "runtime_task":
            return
        task_id = getattr(record, "resource_id", "")
        if not task_id:
            return
        from app.enterprise_runtime import orchestrator as runtime

        runtime.approve_runtime_task(
            task_id, bool(approved),
            actor=getattr(record, "approved_by_id", "") or "",
            actor_name=getattr(record, "approved_by_name", "") or "",
        )
    except Exception:  # noqa: BLE001 非 runtime 任务/状态不符 → no-op
        pass


def approve(context, user, approval_id: str, payload: ApprovalApprovePayload) -> ApprovalApplicationRecord:
    require_tenant_permission(context, APPROVAL_APPROVE)
    from app.enterprise_runtime import observability as obs

    with obs.span("approval", "approval.approve", tenant_id=str(context.tenant_id),
                  attributes={"approval_id": approval_id, "actor_id": user.id}) as span_obj:
        record = _approve_locked(context, user, approval_id, payload)
        span_obj.attributes["operation"] = record.operation
        span_obj.attributes["risk_level"] = record.risk_level
        span_obj.attributes["decision"] = record.approval_status
        return record


def _approve_locked(context, user, approval_id: str,
                    payload: ApprovalApprovePayload) -> ApprovalApplicationRecord:
    record = store.get(approval_id)
    if record is None:
        raise _not_found()
    if record.tenant_id != context.tenant_id:
        raise forbidden("无权审批该租户的申请")
    with store.row_lock(approval_id):
        record = store.get(approval_id)
        _expire_if_needed(record)
        if record.approval_status != "pending":
            raise _conflict(f"审批仅 pending 态可审批，当前为 {record.approval_status}")

        op = get_operation(record.operation)
        if op is None:
            raise _bad_request("审批申请对应的治理操作已下线")
        _validate_target(context, op, record.resource_type, record.resource_id)
        if _security_required(record) and not _is_security_approver(context, user):
            raise forbidden("L4 高危审批需安全管理员")
        if record.actor_id == user.id and not can_self_approve(op, record.risk_level):
            raise _conflict("不能审批自己提交的高风险申请")

        now = _now()
        meta = dict(record.approval_meta or {})
        approved_by = list(meta.get("approved_by", []))
        if user.id in approved_by:
            raise _conflict("当前用户已审批该申请，不能重复计票")
        approved_by.append(user.id)
        meta["approved_by"] = approved_by
        meta["approve_count"] = len(approved_by)
        record.approval_meta = meta

        if _security_required(record) or meta.get("needed", 1) <= len(approved_by):
            record.approval_status = "approved"
            record.approved_by_id = user.id
            record.approved_by_name = getattr(user, "name", "")
            record.approved_at = now
            record.version += 1
            record.updated_at = now
            store.put(record)
            _audit(context, "approve", record.id, f"审批通过 {record.operation}")
            _forward_runtime_decision(record, approved=True)
        else:
            record.version += 1
            record.updated_at = now
            store.put(record)
            _audit(context, "approve", record.id,
                   f"审批通过(还需 {meta.get('needed', 1) - len(approved_by)} 人) {record.operation}")
    return record


def reject(context, user, approval_id: str, payload: ApprovalRejectPayload) -> ApprovalApplicationRecord:
    require_tenant_permission(context, APPROVAL_APPROVE)
    from app.enterprise_runtime import observability as obs

    with obs.span("approval", "approval.reject", tenant_id=str(context.tenant_id),
                  attributes={"approval_id": approval_id, "actor_id": user.id}) as span_obj:
        record = _reject_locked(context, user, approval_id, payload)
        span_obj.attributes["operation"] = record.operation
        span_obj.attributes["risk_level"] = record.risk_level
        span_obj.attributes["decision"] = record.approval_status
        return record


def _reject_locked(context, user, approval_id: str,
                   payload: ApprovalRejectPayload) -> ApprovalApplicationRecord:
    record = store.get(approval_id)
    if record is None:
        raise _not_found()
    if record.tenant_id != context.tenant_id:
        raise forbidden("无权审批该租户的申请")
    with store.row_lock(approval_id):
        record = store.get(approval_id)
        _expire_if_needed(record)
        if record.approval_status != "pending":
            raise _conflict(f"审批仅 pending 态可拒绝，当前为 {record.approval_status}")
        op = get_operation(record.operation)
        if op is None:
            raise _bad_request("审批申请对应的治理操作已下线")
        _validate_target(context, op, record.resource_type, record.resource_id)
        if record.actor_id == user.id and not can_self_approve(op, record.risk_level):
            raise _conflict("不能对自己提交的高风险申请作出拒绝决定")
        record.approval_status = "rejected"
        record.reason = payload.reason.strip()
        record.updated_at = _now()
        record.version += 1
        store.put(record)
        _audit(context, "reject", record.id, f"拒绝审批 {record.operation}: {payload.reason.strip()[:200]}")
        _forward_runtime_decision(record, approved=False)
    return record


def cancel(context, user, approval_id: str, payload: ApprovalCancelPayload) -> ApprovalApplicationRecord:
    record = store.get(approval_id)
    if record is None:
        raise _not_found()
    if record.tenant_id != context.tenant_id:
        raise forbidden("无权操作该租户的审批")
    # 仅发起人或具备管理权限者可取消
    is_owner = record.actor_id == user.id
    if not is_owner and not has_permission(context, APPROVAL_MANAGE):
        raise forbidden("仅发起人或审批管理者可取消")
    with store.row_lock(approval_id):
        record = store.get(approval_id)
        _expire_if_needed(record)
        if record.approval_status != "pending":
            raise _conflict(f"仅 pending 态可取消，当前为 {record.approval_status}")
        record.approval_status = "cancelled"
        record.reason = (payload.reason or "").strip()
        record.updated_at = _now()
        record.version += 1
        store.put(record)
        _audit(context, "cancel", record.id, f"取消审批 {record.operation}")
    return record


def _audit_plain(tenant_id: str, actor_id: str, action: str, resource_id: str, summary: str, result: str = "ok") -> None:
    """无请求上下文的审计回写（Worker 路径），仍带 tenant_id 归属。"""
    from app import main  # 延迟导入避免循环

    try:
        event = main.AuditEventRecord(
            id=f"audit_{uuid.uuid4().hex[:16]}",
            event_type=f"approval.{action}",
            tenant_id=tenant_id,
            resource_type="approval",
            resource_id=str(resource_id)[:80],
            actor_id=actor_id,
            actor_name="",
            summary=summary[:500],
            diff_summary=[],
            created_at=_now(),
            execution_step_ids=[],
        )
        main.AUDIT_EVENTS[event.id] = event
        from app.db.repositories import persistence_enabled, upsert_audit_event

        if persistence_enabled():
            upsert_audit_event(event)
    except Exception:  # noqa: BLE001
        pass


def execute(context, user, approval_id: str, payload: ApprovalExecutePayload) -> ApprovalApplicationRecord:
    require_tenant_permission(context, APPROVAL_APPROVE)
    record = store.get(approval_id)
    if record is None:
        raise _not_found()
    if record.tenant_id != context.tenant_id:
        raise forbidden("无权执行该租户的审批")

    # —— 执行前重校验（不持锁；互斥由 claim 抢占保证）——
    _expire_if_needed(record)
    _recover_stale_executing(record)
    if record.approval_status == "executing":
        raise _conflict("该审批正在执行中，请稍后查询结果")
    if record.approval_status == "executed":
        raise _conflict("该审批已执行，不能重复执行")
    if record.approval_status != "approved":
        raise _conflict(f"审批当前状态不可执行：{record.approval_status}")
    op = get_operation(record.operation)
    if op is None:
        raise _bad_request("审批申请对应的治理操作已下线")
    _validate_target(context, op, record.resource_type, record.resource_id)
    if _security_required(record):
        if payload.secondary_confirmation is not True:
            raise _bad_request("L4 高危操作需二次确认（secondary_confirmation=true）")
        if not _is_security_approver(context, user):
            raise forbidden("L4 高危执行需安全管理员")
    if record.resource_version is not None:
        current = _fingerprint(context, record.resource_type, record.resource_id)
        if current is not None and current != record.resource_version:
            raise _conflict("审批生效期间目标资源已变化，旧审批不可执行，请重新提交")
    if not _has_business_permission(context, user, op.required_permission):
        raise forbidden(f"已不具备执行所需业务权限 {op.required_permission}")

    # —— 原子抢占执行权（多副本：条件 UPDATE，仅抢到的实例执行副作用）——
    attempt_id = f"att_{uuid.uuid4().hex[:16]}"
    started_at = _now()
    outcome, claimed = store.claim_execution(
        approval_id, expected_version=record.version, attempt_id=attempt_id, started_at=started_at,
    )
    if outcome == "lost" or claimed is None:
        latest = store.refresh_or_get(approval_id)
        if latest is None:
            raise _not_found()
        if latest.approval_status == "executing":
            raise _conflict("该审批正在执行中（已被其他实例抢占）")
        if latest.approval_status == "executed":
            raise _conflict("该审批已执行，不能重复执行")
        raise _conflict(f"审批当前状态不可执行：{latest.approval_status}")

    claimed.executed_by_id = user.id
    claimed.retry_count += 1
    store.put(claimed)

    # —— 仅抢占者执行（幂等 settle；失败回退 approved 可重试，不会误置 executed）——
    try:
        result = execute_operation(context, user, claimed.operation,
                                   claimed.resource_id, claimed.request_summary)
    except ValueError as error:
        store.settle_execution(approval_id, attempt_id, status="approved",
                               error=str(error), finished_at=_now())
        _audit_plain(claimed.tenant_id, user.id, "execution_failed", approval_id,
                     f"审批执行失败(保持可重试): {str(error)[:200]}")
        raise _conflict(f"审批执行失败：{str(error)[:200]}，可重试")
    except Exception as error:  # noqa: BLE001
        store.settle_execution(approval_id, attempt_id, status="approved",
                               error=str(error)[:500], finished_at=_now())
        _audit_plain(claimed.tenant_id, user.id, "execution_failed", approval_id,
                     f"审批执行异常(保持可重试): {str(error)[:200]}")
        raise _conflict("审批执行失败，可重试")

    store.settle_execution(approval_id, attempt_id, status="executed",
                           result=result, finished_at=_now())
    _audit_plain(claimed.tenant_id, user.id, "executed", approval_id,
                 f"执行审批 {claimed.operation}: {(result or {}).get('summary', '')[:200]}")
    record = store.refresh_or_get(approval_id)
    return record or claimed


# ThreadPool safety: 惰性导出一把全局锁供并发测试参考（不参与状态机）
_dummy_lock = threading.Lock()
