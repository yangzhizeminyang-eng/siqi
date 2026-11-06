"""DEV-019 通知服务：审批联动与催办通知的唯一逻辑源。

统一抽取（不复制多套）：main.py / approval_policy / nudge 端点都委托本模块，
保证通知类型/标题/优先级口径一致。懒导入 main（避免循环依赖）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from typing import Any


def notify_approval_required(task: Any, plan: Any, approver_id: str, high_risk: bool = False) -> Any:
    """任务进入人工审批 → 通知审批人；高风险（或计划风险高）标强提醒。

    返回 NotificationRecord。
    """
    from app import main as m

    return m.create_notification(
        recipient_id=approver_id,
        task_id=task.id,
        type="RISK_HIGH" if high_risk else "APPROVAL_REQUIRED",
        title=f"{'【高风险】' if high_risk else ''}任务 #{task.id} 待审批：{task.title}",
        body=f"修改计划 v{getattr(plan, 'version', '?')} 待审批",
        actor_id="system:workflow",
        actor_name="Agent 自动化",
        priority="high" if high_risk else "normal",
    )


def notify_approval_completed(
    task: Any, plan: Any, action: str, actor_id: str, comment: str | None
) -> Any | None:
    """审批完成（approved / rejected / auto_approved）→ 通知任务发起人。

    返回 NotificationRecord；任务无发起人时返回 None。
    """
    from app import main as m

    if not getattr(task, "created_by", None):
        return None
    if action == "rejected":
        title = f"任务 #{task.id} 审批结果：计划已被拒绝，任务终止"
    elif action == "auto_approved":
        title = f"任务 #{task.id} 审批结果：计划已自动通过并继续处理"
    else:
        title = f"任务 #{task.id} 审批结果：计划已通过并继续处理"
    return m.create_notification(
        recipient_id=task.created_by,
        task_id=task.id,
        type="APPROVAL_RESULT",
        title=title,
        body=comment or f"计划 v{getattr(plan, 'version', '?')}",
        actor_id=actor_id,
        actor_name=m.USERS.get(actor_id).name if actor_id and m.USERS.get(actor_id) else (actor_id or "Agent 自动化"),
        priority="high",
    )


def send_approval_reminder(task: Any, actor: Any, now: datetime | None = None) -> Any:
    """超时催办（>24h）：向审批人发送 NUDGE 强提醒通知。

    调用方负责 24h 门禁与审计（见 main.nudge_task）。返回 NotificationRecord。
    """
    from app import main as m

    return m.create_notification(
        recipient_id=task.approval_owner_id,
        task_id=task.id,
        type="NUDGE",
        title=f"【催办】任务 #{task.id} 待审批已超 {m._NUDGE_HOURS} 小时",
        body=m.clip_text(task.title, 160),
        actor_id=actor.id,
        actor_name=actor.name,
        priority="high",
    )