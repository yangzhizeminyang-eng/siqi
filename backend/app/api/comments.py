"""评论域接口：任务评论的增删改查（含 @提及 与关联对象校验）。

OPT-11：自 app/main.py 迁出（行为不变；路由表由 tests/test_opt11_main_split.py 冻结）。
`_persist_task` / `create_notification` / 提及与关联解析器仍留在 app.main
（跨模块共享，且 `_persist_task` 会被测试 monkeypatch），统一经模块属性在调用时读取。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.main import (
    COMMENTS,
    USERS,
    ApiResponse,
    CommentRecord,
    UserRecord,
    _persist_comment,
    api_success,
    clip_text,
    create_audit_event,
    forbidden,
    get_current_user,
    require_permission,
    task_event,
    task_for_mutation,
    validation_error,
)

router = APIRouter(tags=["comments"])


class CommentCreatePayload(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    reference_type: str | None = None
    reference_id: str | None = None


class CommentUpdatePayload(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


def _find_comment(task_id: str, comment_id: str) -> CommentRecord | None:
    from app.db.reads import list_task_comments as read_comments

    return next((c for c in read_comments(task_id) if c.id == comment_id), None)


@router.get("/api/tasks/{task_id}/comments")
def list_task_comments(task_id: str, user: Annotated[UserRecord, Depends(get_current_user)]) -> ApiResponse:
    require_permission(user, "task:view")
    task = task_for_mutation(task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "任务不存在", "data": None})
    from app.db.reads import list_task_comments as read_comments

    return api_success(read_comments(task_id))


@router.post("/api/tasks/{task_id}/comments")
def create_task_comment(
    task_id: str,
    payload: CommentCreatePayload,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    from app import main

    require_permission(user, "task:operate")
    task = task_for_mutation(task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "任务不存在", "data": None})
    body = payload.body.strip()
    if not body:
        raise validation_error("评论内容不能为空")
    reference_type, reference_id = main.resolve_comment_reference(task, payload.reference_type, payload.reference_id)
    now = datetime.now(timezone.utc)
    mentioned = [uid for uid in main.resolve_mentioned_user_ids(body) if uid != user.id]
    comment = CommentRecord(
        id=f"cmt_{secrets.token_hex(6)}",
        task_id=task.id,
        project_id=task.project_id,
        repository_id=task.repository_id,
        author_id=user.id,
        author_name=user.name,
        body=body,
        mentioned_user_ids=mentioned,
        reference_type=reference_type,
        reference_id=reference_id,
        created_at=now,
        deleted_at=None,
    )
    COMMENTS[comment.id] = comment
    _persist_comment(comment)
    reference_line = f"关联：{reference_type} {reference_id}" if reference_type else "关联：任务"
    event = task_event(
        task_id=task.id,
        event_type="COMMENT_ADDED",
        summary=f"{user.name} 发表了评论",
        diff_summary=[f"评论：{clip_text(body, 200)}", reference_line, f"提及 {len(mentioned)} 人"],
        actor_id=user.id,
        actor_name=user.name,
        created_at=now,
    )
    task.events.append(event)
    create_audit_event(
        actor=user,
        event_type="COMMENT_ADDED",
        resource_type="task",
        resource_id=task.id,
        summary=f"任务 {task.id} 发表评论：{clip_text(body, 120)}",
        diff_summary=[reference_line, f"评论ID：{comment.id}"],
    )
    main._persist_task(task)
    for uid in mentioned:
        if uid == user.id:
            continue
        target = USERS.get(uid)
        target_name = target.name if target else uid
        main.create_notification(
            recipient_id=uid,
            task_id=task.id,
            type="MENTION",
            title=f"任务 #{task.id} 中 @了你",
            body=clip_text(body, 160),
            actor_id=user.id,
            actor_name=user.name,
        )
    return api_success(comment, message="评论已发表")


@router.put("/api/tasks/{task_id}/comments/{comment_id}")
def update_task_comment(
    task_id: str,
    comment_id: str,
    payload: CommentUpdatePayload,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    from app import main

    require_permission(user, "task:operate")
    task = task_for_mutation(task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "任务不存在", "data": None})
    comment = _find_comment(task_id, comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "评论不存在", "data": None})
    if comment.deleted_at is not None:
        raise HTTPException(status_code=409, detail={"code": 409, "message": "评论已删除，不能修改", "data": None})
    if comment.author_id != user.id and user.role != "admin":
        raise forbidden("仅评论作者（或管理员）可编辑该评论")
    new_body = payload.body.strip()
    if not new_body:
        raise validation_error("评论内容不能为空")
    comment.body = new_body
    comment.mentioned_user_ids = main.resolve_mentioned_user_ids(new_body)
    _persist_comment(comment)
    task_event(
        task_id=task.id,
        event_type="COMMENT_EDITED",
        summary="评论已编辑",
        diff_summary=[f"评论：{clip_text(new_body, 200)}"],
        actor_id=user.id, actor_name=user.name,
    )
    create_audit_event(actor=user, event_type="COMMENT_EDITED", resource_type="task", resource_id=task.id,
                       summary=f"任务 {task.id} 编辑评论 {comment_id}", diff_summary=[clip_text(new_body, 120)])
    return api_success(comment, message="评论已更新")


@router.delete("/api/tasks/{task_id}/comments/{comment_id}")
def delete_task_comment(
    task_id: str,
    comment_id: str,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    require_permission(user, "task:operate")
    task = task_for_mutation(task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "任务不存在", "data": None})
    comment = _find_comment(task_id, comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "评论不存在", "data": None})
    if comment.deleted_at is not None:
        raise HTTPException(status_code=409, detail={"code": 409, "message": "评论已删除，不能重复删除", "data": None})
    if comment.author_id != user.id and user.role != "admin":
        raise forbidden("仅评论作者（或管理员）可删除该评论")
    # 软删除：保留记录与审计痕迹，回放不破坏历史
    comment.deleted_at = datetime.now(timezone.utc)
    _persist_comment(comment)
    task_event(
        task_id=task.id,
        event_type="COMMENT_DELETED",
        summary="评论已删除",
        diff_summary=[f"评论ID：{comment.id}", "软删除：记录与审计保留"],
        actor_id=user.id, actor_name=user.name,
    )
    create_audit_event(actor=user, event_type="COMMENT_DELETED", resource_type="task", resource_id=task.id,
                       summary=f"任务 {task.id} 删除评论 {comment_id}（软删除）", diff_summary=["历史回放保留该评论占位"])
    return api_success(comment, message="评论已删除")
