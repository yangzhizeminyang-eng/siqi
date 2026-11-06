"""通知域接口：站内通知列表/未读数/已读、任务通知、SSE 实时事件流与超时催办。

OPT-11：自 app/main.py 迁出（行为不变；路由表由 tests/test_opt11_main_split.py 冻结）。
`_persist_task` 会被测试 monkeypatch，经 `app.main` 模块属性在调用时读取；
`_persist_notification` / `task_event` / `logger` 等共享助手保持模块级引用。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.main import (
    ApiResponse,
    UserRecord,
    _persist_notification,
    api_success,
    create_audit_event,
    forbidden,
    get_current_user,
    logger,
    require_permission,
    task_event,
    task_for_mutation,
)

router = APIRouter(tags=["notifications"])


@router.get("/api/notifications")
def list_notifications(
    user: Annotated[UserRecord, Depends(get_current_user)],
    unread_only: bool = Query(default=False),
) -> ApiResponse:
    from app.db.reads import list_user_notifications as read_notifications

    return api_success(read_notifications(user.id, unread_only=unread_only))


@router.get("/api/notifications/unread-count")
def notifications_unread_count(user: Annotated[UserRecord, Depends(get_current_user)]) -> ApiResponse:
    from app.db.reads import unread_notification_count

    return api_success({"count": unread_notification_count(user.id)})


@router.get("/api/events/stream")
def stream_realtime_events(user: Annotated[UserRecord, Depends(get_current_user)]) -> StreamingResponse:
    """团队版实时事件流：Redis Pub/Sub 转 SSE；断线由浏览器 EventSource 自动重连。"""
    from app.services.realtime_service import iter_events, sse_frame

    def event_stream():
        try:
            for event in iter_events(user.id):
                yield sse_frame(event)
        except GeneratorExit:
            raise
        except Exception as error:  # noqa: BLE001 - 连接异常交给客户端重连
            logger.warning("[realtime] SSE 连接结束 user=%s error=%s", user.id, error)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/tasks/{task_id}/notifications")
def list_task_notifications(task_id: str, user: Annotated[UserRecord, Depends(get_current_user)]) -> ApiResponse:
    require_permission(user, "task:view")
    task = task_for_mutation(task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "任务不存在", "data": None})
    from app.db.reads import list_task_notifications as read_task_notifications

    return api_success(read_task_notifications(task_id))


@router.post("/api/notifications/{notification_id}/read")
def mark_notification_read(notification_id: str, user: Annotated[UserRecord, Depends(get_current_user)]) -> ApiResponse:
    from app.db.reads import get_notification as read_notification

    notification = read_notification(notification_id)
    if not notification:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "通知不存在", "data": None})
    if notification.recipient_id != user.id and user.role != "admin":
        raise forbidden("只能操作自己的通知")
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        _persist_notification(notification)
    return api_success(notification, message="已标记已读")


@router.post("/api/tasks/{task_id}/nudge")
def nudge_task(task_id: str, user: Annotated[UserRecord, Depends(get_current_user)]) -> ApiResponse:
    """超时催办：任务处于待审批且最新待审批计划超过 24 小时时，向审批人发送 NUDGE 通知并写审计。"""
    from app import main

    require_permission(user, "task:operate")
    task = task_for_mutation(task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "任务不存在", "data": None})
    if task.status != "WAITING_APPROVAL":
        raise HTTPException(status_code=409, detail={"code": 409, "message": "仅待审批中的任务可催办", "data": None})
    waiting = [p for p in task.plans if p.status == "WAITING_APPROVAL"]
    if not waiting:
        raise HTTPException(status_code=409, detail={"code": 409, "message": "无待审批计划，不能催办", "data": None})
    plan = max(waiting, key=lambda p: p.created_at)
    age = datetime.now(timezone.utc) - plan.created_at
    if age < timedelta(hours=main._NUDGE_HOURS):
        raise HTTPException(
            status_code=409,
            detail={
                "code": 409,
                "message": f"待审批尚未超过 {main._NUDGE_HOURS} 小时（当前 {int(age.total_seconds() // 3600)} 小时），暂不能催办",
                "data": None,
            },
        )
    if not task.approval_owner_id:
        raise HTTPException(status_code=409, detail={"code": 409, "message": "任务未指派审批人，无法催办", "data": None})
    now = datetime.now(timezone.utc)
    from app.services.notification_service import send_approval_reminder

    # 幂等守卫：同一任务近 24 小时内已对该审批人推送过 NUDGE（DB/内存真源）则不再重复生成通知/审计。
    from app.db.reads import list_user_notifications as read_user_notifications

    already_nudged = any(
        n.type == "NUDGE"
        and n.task_id == task.id
        and (now - n.created_at) < timedelta(hours=main._NUDGE_HOURS)
        for n in read_user_notifications(task.approval_owner_id)
    )
    if already_nudged:
        return api_success({"nudged": False, "plan_id": plan.id}, message="该任务近 24 小时已催办过，请勿重复催办")

    send_approval_reminder(task, user)
    create_audit_event(
        actor=user,
        event_type="NUDGE_SENT",
        resource_type="task",
        resource_id=task.id,
        summary=f"对任务 {task.id} 发送催办通知（待审批超 {main._NUDGE_HOURS} 小时）",
        diff_summary=[f"审批人：{task.approval_owner_id}", f"计划版本：v{plan.version}", f"等待小时数：{int(age.total_seconds() // 3600)}"],
    )
    event = task_event(
        task_id=task.id,
        event_type="NUDGE_SENT",
        summary="已发送催办通知",
        diff_summary=[f"审批人：{task.approval_owner_id}", f"计划版本：v{plan.version}"],
        actor_id=user.id, actor_name=user.name,
        created_at=now,
    )
    task.events.append(event)
    main._persist_task(task)
    return api_success({"nudged": True, "plan_id": plan.id}, message="催办通知已发送")
