"""企业版 E4-01 状态机：任务/步骤状态转换集中定义。

禁止在多个 Worker/路由分支散落任意改状态；所有流转必须经过这里校验 + 审计。
"""

from __future__ import annotations

from app.enterprise_runtime.models import RuntimeStepStatus, RuntimeTaskStatus


class IllegalTransitionError(RuntimeError):
    pass


# 任务级允许的转换（key: (from, to)）
TASK_TRANSITIONS: set[tuple[RuntimeTaskStatus, RuntimeTaskStatus]] = {
    (RuntimeTaskStatus.QUEUED, RuntimeTaskStatus.RUNNING),
    (RuntimeTaskStatus.QUEUED, RuntimeTaskStatus.CANCELLED),
    (RuntimeTaskStatus.QUEUED, RuntimeTaskStatus.WAITING_HUMAN),
    (RuntimeTaskStatus.QUEUED, RuntimeTaskStatus.FAILED),
    (RuntimeTaskStatus.RUNNING, RuntimeTaskStatus.WAITING_APPROVAL),
    (RuntimeTaskStatus.RUNNING, RuntimeTaskStatus.WAITING_HUMAN),
    (RuntimeTaskStatus.RUNNING, RuntimeTaskStatus.RETRYING),
    (RuntimeTaskStatus.RUNNING, RuntimeTaskStatus.SUCCEEDED),
    (RuntimeTaskStatus.RUNNING, RuntimeTaskStatus.FAILED),
    (RuntimeTaskStatus.WAITING_APPROVAL, RuntimeTaskStatus.RUNNING),      # 审批通过
    (RuntimeTaskStatus.WAITING_APPROVAL, RuntimeTaskStatus.FAILED),       # 审批拒绝
    (RuntimeTaskStatus.WAITING_APPROVAL, RuntimeTaskStatus.CANCELLED),
    (RuntimeTaskStatus.WAITING_HUMAN, RuntimeTaskStatus.RUNNING),         # 人工 resume/retry
    (RuntimeTaskStatus.WAITING_HUMAN, RuntimeTaskStatus.CANCELLED),       # 人工 cancel
    (RuntimeTaskStatus.WAITING_HUMAN, RuntimeTaskStatus.WAITING_APPROVAL),  # 接管不能跳过审批，打回
    (RuntimeTaskStatus.RETRYING, RuntimeTaskStatus.RUNNING),
    (RuntimeTaskStatus.RETRYING, RuntimeTaskStatus.WAITING_HUMAN),        # 重试上限
    (RuntimeTaskStatus.RETRYING, RuntimeTaskStatus.FAILED),
}

# 步骤级允许的转换
STEP_TRANSITIONS: set[tuple[RuntimeStepStatus, RuntimeStepStatus]] = {
    (RuntimeStepStatus.PENDING, RuntimeStepStatus.RUNNING),
    (RuntimeStepStatus.PENDING, RuntimeStepStatus.SKIPPED),
    (RuntimeStepStatus.PENDING, RuntimeStepStatus.WAITING_HUMAN),  # 可对未启动步骤请求人工接管
    (RuntimeStepStatus.RUNNING, RuntimeStepStatus.SUCCEEDED),
    (RuntimeStepStatus.RUNNING, RuntimeStepStatus.FAILED),
    (RuntimeStepStatus.RUNNING, RuntimeStepStatus.PAUSED),
    (RuntimeStepStatus.RUNNING, RuntimeStepStatus.WAITING_HUMAN),
    (RuntimeStepStatus.RUNNING, RuntimeStepStatus.PENDING),   # 崩溃回收：租约过期后重置重做（OPT-13）
    (RuntimeStepStatus.PAUSED, RuntimeStepStatus.RUNNING),
    (RuntimeStepStatus.PAUSED, RuntimeStepStatus.FAILED),
    (RuntimeStepStatus.WAITING_HUMAN, RuntimeStepStatus.RUNNING),
    (RuntimeStepStatus.WAITING_HUMAN, RuntimeStepStatus.SKIPPED),
    (RuntimeStepStatus.WAITING_HUMAN, RuntimeStepStatus.PENDING),   # 人工 retry
    (RuntimeStepStatus.FAILED, RuntimeStepStatus.PENDING),   # 重试重置
    (RuntimeStepStatus.FAILED, RuntimeStepStatus.SKIPPED),
    (RuntimeStepStatus.FAILED, RuntimeStepStatus.RUNNING),
    (RuntimeStepStatus.FAILED, RuntimeStepStatus.WAITING_HUMAN),  # 超过重试上限 -> 人工
}


def transition_task(task: "RuntimeTaskLike", new_status: RuntimeTaskStatus) -> None:
    cur = task.status if isinstance(task.status, RuntimeTaskStatus) else RuntimeTaskStatus(task.status)
    nxt = new_status if isinstance(new_status, RuntimeTaskStatus) else RuntimeTaskStatus(new_status)
    if cur == nxt:
        return
    if (cur, nxt) not in TASK_TRANSITIONS:
        raise IllegalTransitionError(f"非法的任务状态转换: {cur.value} -> {nxt.value}")
    task.status = nxt


def transition_step(step: "RuntimeStepLike", new_status: RuntimeStepStatus) -> None:
    cur = step.status if isinstance(step.status, RuntimeStepStatus) else RuntimeStepStatus(step.status)
    nxt = new_status if isinstance(new_status, RuntimeStepStatus) else RuntimeStepStatus(new_status)
    if cur == nxt:
        return
    if (cur, nxt) not in STEP_TRANSITIONS:
        raise IllegalTransitionError(f"非法的步骤状态转换: {step.name} {cur.value} -> {nxt.value}")
    step.status = nxt


class RuntimeTaskLike:
    status: object


class RuntimeStepLike:
    status: object
    name: str