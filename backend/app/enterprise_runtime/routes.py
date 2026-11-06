"""企业版 E4-01 Runtime HTTP 接口（全部 tenant-scoped + 服务端解析租户 + 审计）。

worker 不通过 HTTP 调这些接口执行；此接口面向「创建/查询/重试/人工接管」控制面。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException
from pydantic import BaseModel, Field

from app.enterprise_runtime import orchestrator as rt
from app.tenancy.context import context_for_current_user

router = APIRouter(prefix="/api/enterprise/runtime", tags=["enterprise-runtime"])


def _current_user_dependency(agent_session: Annotated[str | None, Cookie()] = None):
    from app import main  # 延迟导入避免循环

    return main.get_current_user(agent_session)


CurrentUser = Annotated[object, Depends(_current_user_dependency)]


class StartTaskPayload(BaseModel):
    task_id: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=1, max_length=200)
    project_id: str = ""
    repository_id: str = ""
    title: str = ""


class RetryPayload(BaseModel):
    step_id: str = Field(min_length=1, max_length=200)
    reason: str = ""


class TakeoverPayload(BaseModel):
    step_id: str = Field(min_length=1, max_length=200)
    reason: str = ""


class TakeoverCompletePayload(BaseModel):
    action: str = Field(pattern="^(resume|retry|cancel)$")


def _ctx(user):
    return context_for_current_user()


@router.post("/tasks")
def start_task(payload: StartTaskPayload, user: CurrentUser):
    ctx = _ctx(user)
    task = rt.start_runtime_task(ctx, payload.task_id, payload.idempotency_key,
                                 project_id=payload.project_id, repository_id=payload.repository_id,
                                 title=payload.title)
    return {"code": 0, "message": "success", "data": task.model_dump(mode="json")}


@router.get("/tasks")
def list_tasks(user: CurrentUser):
    ctx = _ctx(user)
    tasks = rt.rstore.list_by_tenant(str(getattr(ctx, "tenant_id", "") or "tenant_default"))
    return {"code": 0, "message": "success", "data": [t.model_dump(mode="json") for t in tasks]}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, user: CurrentUser):
    ctx = _ctx(user)
    try:
        task = rt.resume_from_checkpoint(ctx, task_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"code": 0, "message": "success", "data": task.model_dump(mode="json")}


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: str, payload: RetryPayload, user: CurrentUser):
    ctx = _ctx(user)
    try:
        task = rt.request_retry(ctx, task_id, payload.step_id, payload.reason)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"code": 0, "message": "success", "data": task.model_dump(mode="json")}


@router.post("/tasks/{task_id}/takeover")
def request_takeover(task_id: str, payload: TakeoverPayload, user: CurrentUser):
    ctx = _ctx(user)
    try:
        task = rt.request_human_takeover(ctx, task_id, payload.step_id, payload.reason)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"code": 0, "message": "success", "data": task.model_dump(mode="json")}


@router.post("/tasks/{task_id}/takeover/complete")
def complete_takeover(task_id: str, payload: TakeoverCompletePayload, user: CurrentUser):
    ctx = _ctx(user)
    from app.enterprise_runtime import state_machine as sm
    try:
        task = rt.complete_human_takeover(ctx, task_id, payload.action)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except sm.IllegalTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"code": 0, "message": "success", "data": task.model_dump(mode="json")}


@router.get("/traces")
def list_traces(user: CurrentUser, trace_id: str = "", category: str = "", status: str = "",
                limit: int = 100, offset: int = 0):
    """OPT-07：全链路 trace 查询（tenant-scoped + audit:view 门禁；只读，不含敏感属性）。"""
    ctx = _ctx(user)
    from app.enterprise_runtime import observability as obs
    from app.tenancy.permissions import AUDIT_VIEW, require_tenant_permission

    require_tenant_permission(ctx, AUDIT_VIEW)
    spans = obs.query_spans(str(ctx.tenant_id), trace_id=trace_id, category=category,
                            status=status, limit=limit, offset=offset)
    return {
        "code": 0, "message": "success",
        "data": {
            "spans": [s.model_dump(mode="json") for s in spans],
            "retention": obs.retention_info(),
            "filters": {"trace_id": trace_id, "category": category, "status": status,
                        "limit": limit, "offset": offset},
        },
    }