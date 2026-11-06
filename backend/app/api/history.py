"""历史复用域接口：完成摘要生成/读取/索引状态 + 相似历史任务检索（DEV-021）。

OPT-11：自 app/main.py 迁出（行为不变；路由表由 tests/test_opt11_main_split.py 冻结）。
``_can_view_replay`` 定义在 main.py 的 app 初始化之后，故经 ``app.main`` 惰性读取。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.main import (
    ApiResponse,
    UserRecord,
    api_success,
    forbidden,
    get_current_user,
    require_permission,
    task_for_mutation,
)

router = APIRouter(tags=["history"])


class HistorySearchPayload(BaseModel):
    projectId: str | None = None
    query: str = Field(default="", max_length=2000)
    taskId: str | None = None
    limit: int | None = Field(default=None, ge=1, le=20)
    minSimilarity: float | None = Field(default=None, ge=0.0, le=1.0)


@router.post("/api/tasks/{task_id}/completed-summary")
def generate_task_completed_summary(task_id: str, user: Annotated[UserRecord, Depends(get_current_user)]) -> ApiResponse:
    """DEV-021 为完成任务生成/重生成历史复用摘要（幂等）。"""
    from app import main
    from app.services import history_service

    require_permission(user, "replay:view")
    task = task_for_mutation(task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "任务不存在", "data": None})
    if not main._can_view_replay(task, user):
        raise forbidden("无权操作该任务")
    if task.status != "COMPLETED":
        raise HTTPException(status_code=409, detail={"code": 409, "message": "仅完成任务可生成历史复用摘要", "data": None})
    summary = history_service.generate_for_completed_task(task_id, user=user)
    return api_success(summary, message="历史复用摘要已生成")


@router.get("/api/tasks/{task_id}/completed-summary")
def get_task_completed_summary(task_id: str, user: Annotated[UserRecord, Depends(get_current_user)]) -> ApiResponse:
    """DEV-021 读取完成任务的历史复用摘要。"""
    from app import main
    from app.services import history_service

    require_permission(user, "replay:view")
    task = task_for_mutation(task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "任务不存在", "data": None})
    if not main._can_view_replay(task, user):
        raise forbidden("无权查看该任务")
    summary = history_service.get_by_task_id(task_id)
    return api_success(summary, message="历史复用摘要")


@router.get("/api/tasks/{task_id}/completed-summary/index")
def get_task_completed_summary_index(task_id: str, user: Annotated[UserRecord, Depends(get_current_user)]) -> ApiResponse:
    """DEV-021 读取历史摘要的向量索引状态（PENDING/EMBEDDING/INDEXED/FAILED + failure_reason）。"""
    from app import main
    from app.services import history_service

    require_permission(user, "replay:view")
    task = task_for_mutation(task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "任务不存在", "data": None})
    if not main._can_view_replay(task, user):
        raise forbidden("无权查看该任务")
    summary = history_service.get_by_task_id(task_id)
    if not summary:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "该任务尚无历史复用摘要", "data": None})
    return api_success(history_service.get_status(summary.id))


@router.post("/api/tasks/{task_id}/completed-summary/reindex")
def reindex_task_completed_summary(task_id: str, user: Annotated[UserRecord, Depends(get_current_user)]) -> ApiResponse:
    """DEV-021 重生成摘要并重试入向量索引（幂等）。"""
    from app import main
    from app.services import history_service

    require_permission(user, "replay:view")
    task = task_for_mutation(task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "任务不存在", "data": None})
    if not main._can_view_replay(task, user):
        raise forbidden("无权操作该任务")
    summary = history_service.generate_for_completed_task(task_id, force=True, user=user)
    if summary is None:
        raise HTTPException(status_code=409, detail={"code": 409, "message": "仅完成任务可重生成历史复用摘要", "data": None})
    history_service.enqueue(summary.id, force=True)
    return api_success(history_service.get_status(summary.id), message="已重新入向量索引")


@router.post("/api/history/search")
def search_historical_tasks(payload: HistorySearchPayload, user: Annotated[UserRecord, Depends(get_current_user)]) -> ApiResponse:
    """DEV-021 相似历史任务检索（项目隔离 + 权限过滤 + 排除自身 + 阈值 + 排序；来源标注『历史任务参考』）。"""
    from app.services import history_service

    require_permission(user, "replay:view")
    results = history_service.search_similar(payload.model_dump(), user)
    return api_success(results)
