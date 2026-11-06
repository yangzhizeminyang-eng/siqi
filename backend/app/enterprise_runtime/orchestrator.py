"""企业版 E4-01 Runtime 编排：主链、checkpoint、重试、人工接管、租约恢复。

- 主链：queued -> claim -> context_fetch -> enterprise_rag_analysis -> plan_generation
        -> waiting_approval -> (approve) -> execution -> test_execution -> succeeded
- 失败：记录原因+checkpoint -> 按策略 retry -> 超上限 waiting_human -> 人工 resume/retry/cancel。
- 兼容层：不重写团队链；步骤执行器由调用方注入（真实环境用既有 run_agent/run_test）。
- 所有跨租户读/恢复/重试/接管 403 + 拒绝审计；auditor 不可接管/执行高风险。
"""

from __future__ import annotations

import secrets
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from app.enterprise_runtime import checkpoint as cp
from app.enterprise_runtime import observability as obs
from app.enterprise_runtime import state_machine as sm
from app.enterprise_runtime import store as rstore
from app.enterprise_runtime.models import (
    RUNTIME_STEPS,
    ExecutionLease,
    FailureKind,
    HumanTakeoverAction,
    RuntimeCheckpoint,
    RuntimeStep,
    RuntimeStepStatus,
    RuntimeTask,
    RuntimeTaskStatus,
    steps_for_workflow,
    utcnow,
)
from app.enterprise_runtime.retry_policy import DEFAULT_POLICY, RetryPolicy

# 人工接管允许的角色（auditor 明确排除；操作者需持有治理/接管权限）
_TAKEOVER_ROLES = frozenset({"tenant_owner", "tenant_admin", "security_admin", "project_owner"})

_LOCK = threading.RLock()


def _mark_step_span(span_obj, result: dict) -> None:
    """OPT-07：把步骤执行结果映射到 worker span（ok/error/blocked + failure_kind）。"""
    kind = str((result or {}).get("failure_kind") or "")
    span_obj.attributes["failure_kind"] = kind
    if (result or {}).get("ok"):
        return
    span_obj.status = "blocked" if kind == "environment" else "error"
    span_obj.error_kind = kind or "failed"


# ---------------- 审计（tenant-scoped + 脱敏） ----------------
def _audit(tenant_id: str, event_type: str, *, actor: str, actor_name: str, summary: str,
           resource_id: str = "", extra: dict | None = None) -> None:
    try:
        from app import main
        from app.db.repositories import persistence_enabled, upsert_audit_event
    except Exception:  # noqa: BLE001 纯内存测试环境可能没有 DB
        return
    try:
        record = main.AuditEventRecord(
            # 唯一 ID：同一秒内同类事件（如 6 个 Agent 相继成功）不得互相覆盖
            id=f"audit_{secrets.token_hex(6)}",
            tenant_id=tenant_id, event_type=event_type, resource_type="runtime_task",
            resource_id=resource_id or f"rt_{actor}", actor_id=actor, actor_name=actor_name,
            summary=summary[:200], diff_summary=[],
            created_at=datetime.now(timezone.utc), execution_step_ids=[],
        )
        main.AUDIT_EVENTS[record.id] = record
        if persistence_enabled():
            upsert_audit_event(record)
    except Exception:  # noqa: BLE001
        pass


def _deny_and_audit(ctx, task: RuntimeTask, action: str) -> None:
    _audit(task.tenant_id, "runtime.tenant.denied",
           actor=str(getattr(ctx, "user_id", "") or ""), actor_name=str(getattr(ctx, "user_name", "") or ""),
           summary=f"缺少租户权限: tenant={task.tenant_id} action={action}", resource_id=task.id)


def _tenant_of(ctx) -> str:
    return str(getattr(ctx, "tenant_id", "") or "tenant_default")


def _require_task_tenant(ctx, task: RuntimeTask) -> bool:
    return _tenant_of(ctx) == task.tenant_id


# ---------------- 核心接口 ----------------
def start_runtime_task(ctx, task_id: str, idempotency_key: str, *, project_id: str = "",
                       repository_id: str = "", title: str = "",
                       policy: RetryPolicy | None = None,
                       workflow_version: int = 1, dispatch: bool = False) -> RuntimeTask:
    tenant_id = _tenant_of(ctx)
    if idempotency_key:
        existing = rstore.get_by_idempotency(tenant_id, idempotency_key)
        if existing is not None:
            return existing  # 同一幂等键重复提交返回同一任务，不重复创建
    if rstore.get(task_id) is not None:
        return rstore.get(task_id)
    p = policy or DEFAULT_POLICY
    ver = max(1, int(workflow_version or 1))
    steps = steps_for_workflow(ver)
    task = RuntimeTask(
        id=task_id, tenant_id=tenant_id, project_id=project_id, repository_id=repository_id,
        title=title, status=RuntimeTaskStatus.QUEUED, idempotency_key=idempotency_key,
        workflow_version=ver,
        steps=[RuntimeStep(step_id=f"{task_id}:{name}", task_id=task_id, tenant_id=tenant_id,
                           name=name, agent_type=(name if ver >= 2 else None),
                           max_retries=p.max_retries, backoff_seconds=p.base_backoff_seconds,
                           idempotency_key=f"{task_id}:{name}:{idempotency_key or 'once'}")
               for name in steps],
    )
    rstore.put(task)
    # 生产入口：新建 v2 任务后自动投递 Celery worker（P1 #4）。测试默认不投递。
    if dispatch and ver >= 2:
        try:
            from app.workers.enterprise_tasks import submit_agent_workflow

            submit_agent_workflow(task_id)
        except Exception:  # noqa: BLE001 投递失败不阻断任务创建（worker 可手动接管）
            _audit(tenant_id, "runtime.agent.dispatch.failed",
                   actor=str(getattr(ctx, "user_id", "") or ""),
                   actor_name=str(getattr(ctx, "user_name", "") or ""),
                   summary=f"自动投递失败 {task_id}", resource_id=task_id)
    _audit(tenant_id, "runtime.task.started", actor=str(getattr(ctx, "user_id", "") or ""),
           actor_name=str(getattr(ctx, "user_name", "") or ""), summary=f"创建 Runtime 任务 {task_id}",
           resource_id=task_id)
    return task


def claim_next_step(worker_id: str, task_id: str, *,
                    lease_ttl_seconds: float | None = None) -> ExecutionLease | None:
    """Worker 原子抢占下一个可执行 step；同一 step 只有一个有效执行者。

    OPT-13 恢复语义：
    - 选靶前先回收租约过期的 RUNNING 步骤（崩溃残留）→ PENDING，保证崩溃的步骤被重做；
    - 不得越过未完成的 RUNNING/PAUSED/WAITING_HUMAN 步骤去抢后续 PENDING/Failed 步骤（防乱序）。
    """
    task = rstore.get(task_id)
    if task is None:
        return None
    if task.status not in (RuntimeTaskStatus.QUEUED, RuntimeTaskStatus.RUNNING, RuntimeTaskStatus.RETRYING):
        return None
    if rstore.reclaim_stale_steps(task_id):
        task = rstore.get(task_id) or task
    # 找到第一个可抢占 step：PENDING（新步骤）或 FAILED 且可重试；首个未完成步是边界
    target: RuntimeStep | None = None
    for step in task.steps:
        if step.status in (RuntimeStepStatus.SUCCEEDED, RuntimeStepStatus.SKIPPED):
            continue
        if step.status in (RuntimeStepStatus.PENDING, RuntimeStepStatus.FAILED):
            target = step
        break
    if target is None:
        return None
    outcome, claimed = rstore.claim_step(
        task_id, target.step_id, worker_id,
        expected_version=task.version,
        lease_ttl_seconds=(lease_ttl_seconds or rstore.step_lease_ttl_seconds()),
    )
    if outcome != "claimed" or claimed is None:
        return None
    claimed.current_step = target.name
    sm.transition_task(claimed, RuntimeTaskStatus.RUNNING)
    sm.transition_step(_find_step(claimed, target.step_id), RuntimeStepStatus.RUNNING)
    rstore.put(claimed)
    return ExecutionLease(
        task_id=task_id, step_id=target.step_id, worker_id=worker_id,
        lease_version=claimed.lease_version,
        expires_at=datetime.now(timezone.utc),
    )


def _find_step(task: RuntimeTask, step_id: str) -> RuntimeStep | None:
    for s in task.steps:
        if s.step_id == step_id:
            return s
    return None


def save_checkpoint(task_id: str, step_id: str, state: dict, *, expected_version: int) -> RuntimeCheckpoint:
    task = rstore.get(task_id)
    if task is None or task.version != expected_version:
        # 旧 Worker 用过期版本写入 → 拒绝（不能覆盖新状态）
        raise RuntimeError("checkpoint 版本过期，拒绝覆盖新状态")
    output = state.get("output_summary", "")
    saved = cp.CheckpointStore().save(task, step_id=step_id, output_summary=output)
    _audit(task.tenant_id, "runtime.checkpoint.saved",
           actor="worker", actor_name="worker",
           summary=f"步骤 {step_id} checkpoint v{saved.checkpoint_version}", resource_id=task_id)
    return saved


def resume_from_checkpoint(ctx, task_id: str) -> RuntimeTask:
    task = rstore.get(task_id)
    if task is None:
        return None  # type: ignore[return-value]
    if not _require_task_tenant(ctx, task):
        _deny_and_audit(ctx, task, "runtime.resume")
        raise PermissionError(f"跨租户恢复被拒绝: {task_id}")
    return task


def request_retry(ctx, task_id: str, step_id: str, reason: str) -> RuntimeTask:
    task = rstore.get(task_id)
    if task is None:
        raise KeyError(task_id)
    if not _require_task_tenant(ctx, task):
        _deny_and_audit(ctx, task, "runtime.retry")
        raise PermissionError(f"跨租户重试被拒绝: {task_id}")
    step = _find_step(task, step_id)
    if step is None:
        raise KeyError(step_id)
    if step.status not in (RuntimeStepStatus.FAILED, RuntimeStepStatus.WAITING_HUMAN, RuntimeStepStatus.PAUSED):
        sm.transition_step(step, RuntimeStepStatus.PENDING)  # 幂等：已在 PENDING 可重试
    sm.transition_step(step, RuntimeStepStatus.PENDING)
    step.retry_count += 1
    task.retry_count += 1
    sm.transition_task(task, RuntimeTaskStatus.RETRYING)
    rstore.put(task)
    _audit(task.tenant_id, "runtime.retry.requested", actor=str(getattr(ctx, "user_id", "") or ""),
           actor_name=str(getattr(ctx, "user_name", "") or ""), summary=f"请求重试 {step_id}: {reason[:80]}",
           resource_id=task_id)
    return task


def request_human_takeover(ctx, task_id: str, step_id: str, reason: str) -> RuntimeTask:
    task = rstore.get(task_id)
    if task is None:
        raise KeyError(task_id)
    if not _require_task_tenant(ctx, task):
        _deny_and_audit(ctx, task, "runtime.takeover")
        raise PermissionError(f"跨租户接管被拒绝: {task_id}")
    step = _find_step(task, step_id)
    if step is not None:
        sm.transition_step(step, RuntimeStepStatus.WAITING_HUMAN)
    task.wait_human_reason = reason
    sm.transition_task(task, RuntimeTaskStatus.WAITING_HUMAN)
    rstore.put(task)
    _audit(task.tenant_id, "runtime.takeover.requested", actor=str(getattr(ctx, "user_id", "") or ""),
           actor_name=str(getattr(ctx, "user_name", "") or ""), summary=f"请求人工接管 {step_id}: {reason[:80]}",
           resource_id=task_id)
    return task


def _can_takeover(ctx) -> bool:
    role = str(getattr(ctx, "role", "") or "")
    return role in _TAKEOVER_ROLES


def complete_human_takeover(ctx, task_id: str, action: str) -> RuntimeTask:
    """人工接管动作：resume / retry / cancel。校验租户 + 权限 + WAITING_HUMAN；不能跳过审批。"""
    task = rstore.get(task_id)
    if task is None:
        raise KeyError(task_id)
    if not _require_task_tenant(ctx, task):
        _deny_and_audit(ctx, task, "runtime.takeover.complete")
        raise PermissionError(f"跨租户接管被拒绝: {task_id}")
    if not _can_takeover(ctx):
        _audit(task.tenant_id, "runtime.takeover.denied", actor=str(getattr(ctx, "user_id", "") or ""),
               actor_name=str(getattr(ctx, "user_name", "") or ""),
               summary=f"无权人工接管: tenant={task.tenant_id} action=complete_human_takeover", resource_id=task_id)
        raise PermissionError("无权限人工接管（auditor 或非治理角色不可接管）")
    if task.status != RuntimeTaskStatus.WAITING_HUMAN:
        raise sm.IllegalTransitionError(f"任务当前状态 {task.status.value} 非 WAITING_HUMAN，不能接管")

    action_enum = HumanTakeoverAction(action) if action in HumanTakeoverAction._value2member_map_ else None
    if action_enum is None:
        raise ValueError(f"未知接管动作: {action}")

    task.takeover = {"operator": str(getattr(ctx, "user_id", "") or ""),
                     "action": action_enum.value,
                     "reason": task.wait_human_reason or "", "at": utcnow().isoformat()}

    if action_enum == HumanTakeoverAction.CANCEL:
        sm.transition_task(task, RuntimeTaskStatus.CANCELLED)
        for step in task.steps:
            if step.status not in (RuntimeStepStatus.SUCCEEDED, RuntimeStepStatus.SKIPPED):
                sm.transition_step(step, RuntimeStepStatus.SKIPPED)
        _audit(task.tenant_id, "runtime.takeover.cancelled", actor=str(getattr(ctx, "user_id", "") or ""),
               actor_name=str(getattr(ctx, "user_name", "") or ""), summary=f"人工取消 {task_id}", resource_id=task_id)
        rstore.put(task)
        return task

    # resume / retry：若下一步是 execution 且审批未通过 → 不得跳过审批
    next_pending = _next_step_after_current(task)
    if task.approval_state == "pending" and next_pending == "execution":
        sm.transition_task(task, RuntimeTaskStatus.WAITING_APPROVAL)
        _audit(task.tenant_id, "runtime.takeover.requires_approval",
               actor=str(getattr(ctx, "user_id", "") or ""), actor_name=str(getattr(ctx, "user_name", "") or ""),
               summary="人工接管不能跳过审批：进入 execution 前必须审批通过", resource_id=task_id)
        rstore.put(task)
        return task

    if action_enum == HumanTakeoverAction.RETRY:
        cur = _current_step(task)
        if cur is not None:
            sm.transition_step(cur, RuntimeStepStatus.PENDING)
            cur.retry_count += 1
            task.retry_count += 1
        sm.transition_task(task, RuntimeTaskStatus.RUNNING)
    else:  # resume
        cur = _current_step(task)
        if cur is not None and cur.status in (RuntimeStepStatus.WAITING_HUMAN, RuntimeStepStatus.PAUSED):
            sm.transition_step(cur, RuntimeStepStatus.RUNNING)
        sm.transition_task(task, RuntimeTaskStatus.RUNNING)

    _audit(task.tenant_id, f"runtime.takeover.{action_enum.value}",
           actor=str(getattr(ctx, "user_id", "") or ""), actor_name=str(getattr(ctx, "user_name", "") or ""),
           summary=f"人工 {action_enum.value} {task_id}", resource_id=task_id)
    rstore.put(task)
    return task


def _current_step(task: RuntimeTask) -> RuntimeStep | None:
    if not task.current_step:
        return None
    for s in task.steps:
        if s.name == task.current_step:
            return s
    return None


def _next_step_after_current(task: RuntimeTask) -> str | None:
    if not task.current_step:
        return RUNTIME_STEPS[0] if RUNTIME_STEPS else None
    try:
        idx = RUNTIME_STEPS.index(task.current_step)
    except ValueError:
        return None
    return RUNTIME_STEPS[idx + 1] if idx + 1 < len(RUNTIME_STEPS) else None


def recover_stale_execution(worker_id: str, now: datetime) -> list[RuntimeTask]:
    stale = rstore.list_stale(now)
    for task in stale:
        # worker 崩溃：把执行中的步骤重置回 PENDING，使其可被新 worker 重新抢占（不跳过、不伪造完成）
        for step in task.steps:
            if step.status == RuntimeStepStatus.RUNNING:
                try:
                    sm.transition_step(step, RuntimeStepStatus.PENDING)
                except sm.IllegalTransitionError:
                    step.status = RuntimeStepStatus.PENDING
        task.lease_worker = None
        sm.transition_task(task, RuntimeTaskStatus.RUNNING)
        rstore.put(task)
    return stale


# ---------------- 步骤驱动（兼容层：执行器由调用方注入，真实环境用既有 run_agent/run_test） ----------------
def run_until_blocked(worker_id: str, task_id: str,
                      executors: dict[str, Callable[[RuntimeStep, RuntimeTask, dict], dict]],
                      *, policy: RetryPolicy | None = None) -> RuntimeTask:
    """从当前 checkpoint 开始驱动任务直到需要审批/人工/完成；可重试失败按策略自动重试。

    executors[name] 返回 {"ok": bool, "output_summary": str, "failure_kind": str, "reason": str}。
    """
    p = policy or DEFAULT_POLICY
    task = rstore.get(task_id)
    if task is None:
        raise KeyError(task_id)
    lease = claim_next_step(worker_id, task_id)
    if lease is None:
        return rstore.get(task_id)  # 无下一步可执行

    step = _find_step(task, lease.step_id)
    if step is None:
        return task
    step.worker_id = worker_id
    rstore.put(task)

    executor = executors.get(step.name)
    if executor is None:
        # 无执行器 → 视作环境不可用（不推进），避免伪造成功
        step.failure_kind = FailureKind.ENVIRONMENT
        step.failure_reason = f"缺少执行器: {step.name}"
        sm.transition_step(step, RuntimeStepStatus.FAILED)
        sm.transition_task(task, RuntimeTaskStatus.WAITING_HUMAN)
        rstore.put(task)
        return task

    with obs.span("worker", "worker.step", trace_id=task_id, tenant_id=task.tenant_id,
                  attributes={"step_id": step.step_id, "step_name": step.name,
                              "worker_id": worker_id,
                              "workflow_version": task.workflow_version}) as span_obj:
        result = executor(step, task, {"worker_id": worker_id})
        _mark_step_span(span_obj, result)

    if result.get("ok"):
        step.status = RuntimeStepStatus.SUCCEEDED
        step.output_summary = cp.sanitize_output(result.get("output_summary", ""))
        step.finished_at = utcnow()
        task.completed_steps.append(step.name)
        cp.CheckpointStore().save(task, step_id=step.step_id, output_summary=step.output_summary)
        # 推进 current_step
        nxt = _next_step_after_current(task)
        task.current_step = nxt
        rstore.release_lease(task_id, step.step_id, worker_id)
        if nxt is None:
            sm.transition_task(task, RuntimeTaskStatus.SUCCEEDED)
            rstore.put(task)
            _audit(task.tenant_id, "runtime.task.succeeded", actor="worker", actor_name=worker_id,
                   summary=f"Runtime 任务成功 {task_id}", resource_id=task_id)
            return task
        # 下一站是 execution 且需要审批 → 停到 waiting_approval
        if nxt == "execution" and task.approval_state == "pending":
            sm.transition_task(task, RuntimeTaskStatus.WAITING_APPROVAL)
            rstore.put(task)
            return task
        rstore.put(task)
        return task

    # 失败处理
    kind = p.classify(result.get("failure_kind"), needs_approval=result.get("needs_approval", False),
                      needs_human=result.get("needs_human", False))
    sm.transition_step(step, RuntimeStepStatus.FAILED)  # RUNNING -> FAILED（集中状态机）
    step.failure_kind = kind
    step.failure_reason = cp.sanitize_output(result.get("reason", "step failed"))
    step.retry_count += 1
    task.retry_count += 1
    rstore.release_lease(task_id, step.step_id, worker_id)
    cp.CheckpointStore().save(task, step_id=step.step_id, output_summary="")
    if kind == FailureKind.NEEDS_APPROVAL:
        task.approval_state = "pending"
        sm.transition_task(task, RuntimeTaskStatus.WAITING_APPROVAL)
        rstore.put(task)
        return task
    if kind == FailureKind.NEEDS_HUMAN or not p.can_retry(step.retry_count - 1 if step.retry_count else 0, kind):
        sm.transition_step(step, RuntimeStepStatus.WAITING_HUMAN)
        sm.transition_task(task, RuntimeTaskStatus.WAITING_HUMAN)
        task.wait_human_reason = step.failure_reason
        rstore.put(task)
        _audit(task.tenant_id, "runtime.task.waiting_human", actor="worker", actor_name=worker_id,
               summary=f"步骤 {step.name} 失败需人工接管: {step.failure_reason[:80]}", resource_id=task_id)
        return task
    # 可重试 → 退避后重试
    sm.transition_step(step, RuntimeStepStatus.PENDING)
    step.backoff_seconds = p.next_backoff(step.retry_count)
    sm.transition_task(task, RuntimeTaskStatus.RETRYING)
    rstore.put(task)
    return task


def approve_runtime_task(task_id: str, approved: bool, *, actor: str, actor_name: str) -> RuntimeTask:
    """审批决策接线（真实 E2 审批决定回写）：approved → 继续 execution；拒绝 → 终止。"""
    task = rstore.get(task_id)
    if task is None:
        raise KeyError(task_id)
    if task.status != RuntimeTaskStatus.WAITING_APPROVAL:
        raise sm.IllegalTransitionError(f"任务非 WAITING_APPROVAL: {task.status.value}")
    if approved:
        task.approval_state = "approved"
        sm.transition_task(task, RuntimeTaskStatus.RUNNING)
    else:
        task.approval_state = "rejected"
        sm.transition_task(task, RuntimeTaskStatus.FAILED)
        for step in task.steps:
            if step.status not in (RuntimeStepStatus.SUCCEEDED, RuntimeStepStatus.SKIPPED):
                try:
                    sm.transition_step(step, RuntimeStepStatus.SKIPPED)
                except sm.IllegalTransitionError:
                    pass
    rstore.put(task)
    _audit(task.tenant_id, "runtime.approval.decided", actor=actor, actor_name=actor_name,
           summary=f"审批{'通过' if approved else '拒绝'} {task_id}", resource_id=task_id)
    # 通过后自动续投 CodeChange 及其后续 Agent（仅生产 broker 生效；无 broker 由外部驱动）
    if approved and task.workflow_version >= 2 and _broker_configured():
        try:
            from app.workers.enterprise_tasks import submit_agent_workflow

            submit_agent_workflow(task_id)
        except Exception as e:  # noqa: BLE001 续投失败记录错误审计，不静默
            _audit(task.tenant_id, "runtime.agent.dispatch.failed", actor=actor, actor_name=actor_name,
                   summary=f"审批后续投失败 {task_id}: {str(e)[:120]}", resource_id=task_id)
    return task


# ---------------- E4-02 多 Agent 协作（v2 唯一流程驱动；C3：LangGraph 提供拓扑，此处按决策驱动） ----------------

def agent_audit_cb():
    """给 AgentExecutorRegistry 的审计回调（写 runtime.agent.* 事件，脱敏）。"""
    def _cb(tenant_id: str, event_type: str, step, summary: str) -> None:
        try:
            _audit(tenant_id, event_type, actor="agent",
                   actor_name=str(getattr(step, "agent_type", "") or ""),
                   summary=summary, resource_id=str(getattr(step, "task_id", "") or ""))
        except Exception:  # noqa: BLE001
            pass
    return _cb


def _create_runtime_approval(task: RuntimeTask) -> object | None:
    """Planner 产出 Plan 后，经企业 E2 审批服务创建 runtime.execute 审批单（幂等）。

    走 approvals.service.create_system_approval（不绕过审批服务，constraint #7）；
    idempotency_key 含 task_id 与 plan_version。
    审批中心 approve → E2 service 回写 approve_runtime_task → Graph 恢复 CodeChange。
    若创建失败记错误审计（不静默吞掉）。
    """
    ik = f"rt-approval:{task.id}:plan-v{task.version}"
    try:
        from app.approvals import service as ap_service

        record = ap_service.create_system_approval(
            tenant_id=task.tenant_id, resource_type="runtime_task", resource_id=task.id,
            operation="runtime.execute", idempotency_key=ik,
            title=f"多 Agent 变更审批（{task.id}）",
            request_summary={"task_id": task.id, "plan_version": task.version,
                             "workflow_version": task.workflow_version},
            actor_id="system", actor_name="PlannerAgent",
            resource_version=str(task.version),
        )
        return record
    except Exception as e:  # noqa: BLE001 审批创建失败要留错
        _audit(task.tenant_id, "runtime.approval.create_failed",
               actor="worker", actor_name=self_name(),
               summary=f"创建审批失败 {task.id}: {str(e)[:120]}", resource_id=task.id)
        return None


def self_name() -> str:
    return "agent-runtime"


def _broker_configured() -> bool:
    """生产 broker 已配置（真投递）；无 broker（单机/测试）返回 False，避免内联递归。"""
    try:
        from app.core.runtime_config import load_runtime_settings
        return bool(getattr(load_runtime_settings(), "celery_broker_url", None))
    except Exception:  # noqa: BLE001
        return False


def _latest_input_refs(task: RuntimeTask, step: RuntimeStep) -> list[str]:
    """v2：取同 tenant/task 下、step 之前已完成 agent 的最后一个产物 ref（只传 ref，不传完整原文）。"""
    try:
        from app.enterprise_runtime.artifacts import store as astore
    except Exception:  # noqa: BLE001
        return []
    arts = astore.list_by_task(task.tenant_id, task.id)
    idx = -1
    for i, s in enumerate(task.steps):
        if s.step_id == step.step_id:
            idx = i
            break
    if idx <= 0 and arts:
        # 首个 agent（requirement）无上游；返回空，由 Agent 从 issue/上下文自足
        return []
    prior = [s for s in task.steps[:idx] if s.status == RuntimeStepStatus.SUCCEEDED]
    # 返回全部前置已完成 agent 的产物（executor 会按 agent 允许读取的类型二次过滤）
    kept: list[str] = []
    for s in prior:
        for a in arts:
            if a.agent_type == s.agent_type and a.artifact_id not in kept:
                kept.append(a.artifact_id)
    return kept


def _requeue_from(task: RuntimeTask, step: RuntimeStep, *, reset_agent_types: set[str]) -> None:
    """回退：把 CodeChange 及其后续依赖步骤重置为 PENDING（round 语义），防无限循环。

    review/test 失败都回到 CodeChange，故始终从 code_change 步骤起重置（含 code_change 本身）。
    """
    base_idx = None
    for i, s in enumerate(task.steps):
        if s.agent_type == "code_change_agent":
            base_idx = i
            break
    if base_idx is None:
        return
    for s in task.steps[base_idx:]:
        if s.agent_type in reset_agent_types:
            try:
                sm.transition_step(s, RuntimeStepStatus.PENDING)
            except sm.IllegalTransitionError:
                s.status = RuntimeStepStatus.PENDING
            s.retry_count = 0
            s.finished_at = None
    for s in task.steps:  # 定位 code_change 以设 current_step
        if s.agent_type == "code_change_agent":
            task.current_step = s.name
            break


def run_agent_workflow(worker_id: str, task_id: str, registry, *, ctx=None) -> RuntimeTask:
    """v2 Multi-Agent 驱动：按 LangGraph 拓扑 + decide_next_agent 决策推进 6 Agent 协作。

    - Planner 后置 approval_state=pending -> WAITING_APPROVAL（approve_runtime_task 续接）。
    - Review 发现问题 / Test 失败 → 回 CodeChange（repair_round 递增，≤max，超上限 WAITING_HUMAN）。
    - 只 claim PENDING/FAILED step；保留了租约/checkpoint/幂等机制。
    """
    if ctx is None:
        from types import SimpleNamespace
        task = rstore.get(task_id)
        tenant = getattr(task, "tenant_id", "tenant_default") if task else "tenant_default"
        ctx = SimpleNamespace(tenant_id=tenant, user_id="worker", user_name=worker_id, role="")

    task = rstore.get(task_id)
    if task is None:
        raise KeyError(task_id)
    if not _require_task_tenant(ctx, task):
        _deny_and_audit(ctx, task, "runtime.agent.execute")
        raise PermissionError(f"跨租户 Agent 执行被拒绝: {task_id}")
    if task.workflow_version < 2:
        raise ValueError("run_agent_workflow 仅用于 workflow_version>=2（v1 走 run_until_blocked）")

    lease = claim_next_step(worker_id, task_id)
    if lease is None:
        return rstore.get(task_id)  # 无下一步（含审批/人工停机中的等待）
    task = rstore.get(task_id) or task  # 回收/DB 回读可能替换内存对象，重新取再定位步骤
    step = _find_step(task, lease.step_id)
    if step is None or step.agent_type is None:
        raise RuntimeError(f"v2 step 未绑定 agent_type: {lease.step_id}")

    executor = registry.resolve(step)
    if executor is None:
        step.failure_kind = FailureKind.ENVIRONMENT
        step.failure_reason = f"缺少 agent 执行器: {step.agent_type}"
        sm.transition_step(step, RuntimeStepStatus.FAILED)
        sm.transition_task(task, RuntimeTaskStatus.WAITING_HUMAN)
        rstore.put(task)
        return task

    step.worker_id = worker_id
    step.repair_round = task.repair_round  # 回退重跑时记录当前修复轮次
    rstore.put(task)

    refs = _latest_input_refs(task, step)
    with obs.span("worker", "worker.step", trace_id=task_id, tenant_id=task.tenant_id,
                  attributes={"step_id": step.step_id, "agent_type": step.agent_type,
                              "worker_id": worker_id,
                              "workflow_version": task.workflow_version}) as span_obj:
        result = executor.run(ctx=ctx, task=task, step=step, plan={"input_refs": refs, "input": {}})
        _mark_step_span(span_obj, result)
    # 用 executor 实际使用的 attempt 推进（避免双重 +1）
    step.agent_attempt = int(result.get("attempt") or (step.agent_attempt + 1))

    if not result.get("ok"):
        step.status = RuntimeStepStatus.FAILED
        got_kind = result.get("failure_kind") or "retryable"
        step.failure_kind = FailureKind(got_kind) if got_kind in FailureKind._value2member_map_ else FailureKind.RETRYABLE
        step.failure_reason = cp.sanitize_output(result.get("reason", "agent failed"))
        step.retry_count += 1
        task.retry_count += 1
        rstore.release_lease(task_id, step.step_id, worker_id)
        cp.CheckpointStore().save(task, step_id=step.step_id, output_summary="")
        if step.failure_kind == FailureKind.ENVIRONMENT:
            # 环境缺失 → 立即转人工，不重试（不伪造成功）
            sm.transition_step(step, RuntimeStepStatus.WAITING_HUMAN)
            sm.transition_task(task, RuntimeTaskStatus.WAITING_HUMAN)
            task.wait_human_reason = step.failure_reason
            _audit(task.tenant_id, "runtime.task.waiting_human", actor="worker", actor_name=worker_id,
                   summary=f"环境阻塞: {step.failure_reason[:120]}", resource_id=task_id)
            rstore.put(task)
            return task
        if task.retry_count < 5:
            sm.transition_step(step, RuntimeStepStatus.PENDING)
            sm.transition_task(task, RuntimeTaskStatus.RETRYING)
        else:
            sm.transition_step(step, RuntimeStepStatus.WAITING_HUMAN)
            sm.transition_task(task, RuntimeTaskStatus.WAITING_HUMAN)
            task.wait_human_reason = step.failure_reason
        rstore.put(task)
        return task

    # 成功：由编好的 LangGraph 路由器决定下一步（C3/#6：决策与下一节点都由图计算）
    out = result.get("output") or {}
    art: dict = {}
    if step.agent_type == "review_security_agent":
        art["approved"] = bool(out.get("approved"))
    elif step.agent_type == "test_release_agent":
        art["passed"] = bool(out.get("passed"))
    from app.enterprise_runtime.graph import workflow_steps, route_via_graph
    _seq = workflow_steps(task.workflow_version)
    try:
        _idx = _seq.index(step.name)
    except ValueError:
        _idx = 0
    decision, next_step = route_via_graph(
        agent_type=step.agent_type, latest_artifact=(art if art else None),
        step_index=_idx, repair_round=task.repair_round,
        max_repair_rounds=max(1, task.max_repair_rounds),
    )

    step.status = RuntimeStepStatus.SUCCEEDED
    step.output_summary = cp.sanitize_output(result.get("output_summary", ""))
    step.finished_at = utcnow()
    if step.name not in task.completed_steps:
        task.completed_steps.append(step.name)
    cp.CheckpointStore().save(task, step_id=step.step_id, output_summary=step.output_summary)

    if decision == "approve_wait":
        task.approval_state = "pending"
        task.current_step = step.name
        _create_runtime_approval(task)  # 企业 E2 审批中心入单（幂等）
        rstore.release_lease(task_id, step.step_id, worker_id)
        sm.transition_task(task, RuntimeTaskStatus.WAITING_APPROVAL)
        rstore.put(task)
        return task

    if decision in ("back_code_change", "human_wait"):
        if decision == "back_code_change":
            task.repair_round += 1
            _requeue_from(task, step, reset_agent_types={"code_change_agent", "review_security_agent", "test_release_agent"})
            task.current_step = None
            rstore.release_lease(task_id, step.step_id, worker_id)
            sm.transition_task(task, RuntimeTaskStatus.RUNNING) if task.repair_round <= max(1, task.max_repair_rounds) else sm.transition_task(task, RuntimeTaskStatus.WAITING_HUMAN)
            if task.repair_round > max(1, task.max_repair_rounds):
                task.wait_human_reason = f"修复轮次达上限 {task.max_repair_rounds}"
                sm.transition_task(task, RuntimeTaskStatus.WAITING_HUMAN)
                _audit(task.tenant_id, "runtime.task.waiting_human", actor="worker", actor_name=worker_id,
                       summary=f"{step.agent_type} 回退超上限，转人工: {task.wait_human_reason}", resource_id=task_id)
            rstore.put(task)
            return task
        # human_wait（review/test 修复达上限：不再投递新 Agent，WAITING_HUMAN + 审计）
        task.wait_human_reason = f"review/test 修复轮次达上限 {task.max_repair_rounds}"
        rstore.release_lease(task_id, step.step_id, worker_id)
        sm.transition_task(task, RuntimeTaskStatus.WAITING_HUMAN)
        _audit(task.tenant_id, "runtime.task.waiting_human", actor="worker", actor_name=worker_id,
               summary=f"{step.agent_type} 失败且修复达上限，转人工: {task.wait_human_reason}", resource_id=task_id)
        rstore.put(task)
        return task

    # proceed：由 LangGraph 给出的 next_step 推进（constraint #6：不再用 index+1 手动路由）
    rstore.release_lease(task_id, step.step_id, worker_id)
    if not next_step or next_step in ("end", "human_wait"):
        task.current_step = None
        sm.transition_task(task, RuntimeTaskStatus.SUCCEEDED)
        _audit(task.tenant_id, "runtime.task.succeeded", actor="worker", actor_name=worker_id,
               summary=f"多 Agent 协作成功 {task_id}", resource_id=task_id)
    else:
        task.current_step = next_step
        sm.transition_task(task, RuntimeTaskStatus.RUNNING)
    rstore.put(task)
    return task