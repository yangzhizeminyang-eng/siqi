"""项目域接口：项目列表/创建/详情/更新。

OPT-11：自 app/main.py 迁出（行为不变；路由表由 tests/test_opt11_main_split.py 冻结）。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.main import (
    PROJECTS,
    ApiResponse,
    ProjectPayload,
    ProjectRecord,
    UserRecord,
    _persist_project,
    api_success,
    create_audit_event,
    current_tenant_id,
    get_current_user,
    require_any_permission,
    require_permission,
    require_resource_tenant,
)

router = APIRouter(tags=["projects"])


@router.get("/api/projects")
def list_projects(user: Annotated[UserRecord, Depends(get_current_user)]) -> ApiResponse:
    require_any_permission(user, {"project:manage", "task:create"})
    from app.db.reads import list_projects as read_projects

    return api_success(read_projects(tenant_id=current_tenant_id()))


@router.post("/api/projects")
def create_project(
    payload: ProjectPayload,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    require_permission(user, "project:manage")
    name = payload.name.strip()
    if any(project.name == name for project in PROJECTS.values()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": 409, "message": "项目名称已存在", "data": None},
        )
    now = datetime.now(timezone.utc)
    project = ProjectRecord(
        id=f"proj_{secrets.token_hex(6)}",
        name=name,
        description=payload.description.strip(),
        default_test_command=(payload.default_test_command or "").strip() or None,
        created_at=now,
        updated_at=now,
    )
    PROJECTS[project.id] = project
    _persist_project(project)
    create_audit_event(
        actor=user,
        event_type="PROJECT_CREATED",
        resource_type="project",
        resource_id=project.id,
        summary=f"创建项目「{project.name}」",
        diff_summary=[f"项目名称：{project.name}", f"项目描述：{project.description or '未填写'}"],
    )
    return api_success(project, message="项目已创建")


@router.get("/api/projects/{project_id}")
def get_project(
    project_id: str,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    require_any_permission(user, {"project:manage", "task:create"})
    from app.db import reads

    project = reads.get_project(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 404, "message": "项目不存在", "data": None},
        )
    require_resource_tenant(getattr(project, "tenant_id", None),
                            resource_type="project", resource_id=project_id)
    return api_success(project)


@router.put("/api/projects/{project_id}")
def update_project(
    project_id: str,
    payload: ProjectPayload,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    require_permission(user, "project:manage")
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 404, "message": "项目不存在", "data": None},
        )
    require_resource_tenant(getattr(project, "tenant_id", None),
                            resource_type="project", resource_id=project_id)
    name = payload.name.strip()
    if any(item.id != project_id and item.name == name for item in PROJECTS.values()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": 409, "message": "项目名称已存在", "data": None},
        )
    updated = project.model_copy(
        update={
            "name": name,
            "description": payload.description.strip(),
            "default_test_command": (payload.default_test_command or "").strip() or None,
            "updated_at": datetime.now(timezone.utc),
        }
    )
    PROJECTS[project_id] = updated
    _persist_project(updated)
    create_audit_event(
        actor=user,
        event_type="PROJECT_UPDATED",
        resource_type="project",
        resource_id=project_id,
        summary=f"更新项目「{updated.name}」",
        diff_summary=[
            f"项目名称：{project.name} → {updated.name}" if project.name != updated.name else "项目名称未变化",
            f"项目描述：{project.description or '未填写'} → {updated.description or '未填写'}",
        ],
    )
    return api_success(updated, message="项目已更新")
