"""仓库域接口：仓库配置的列表/识别/创建/更新。

OPT-11：自 app/main.py 迁出（行为不变；路由表由 tests/test_opt11_main_split.py 冻结）。
`REPOSITORY_TOKENS` 会被测试整体替换（monkeypatch main 属性），统一经 `app.main`
模块属性在调用时读写，保持拆分前的 patch 语义。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.main import (
    PROJECTS,
    REPOSITORIES,
    ApiResponse,
    RepositoryConfigPayload,
    RepositoryConfigRecord,
    RepositoryInspectPayload,
    UserRecord,
    _build_repository_record,
    _persist_repository,
    api_success,
    create_audit_event,
    current_tenant_id,
    get_current_user,
    logger,
    require_any_permission,
    require_permission,
    require_resource_tenant,
    validate_repository_payload,
    validate_repository_url,
)

router = APIRouter(tags=["repositories"])


@router.get("/api/repositories")
def list_repositories(
    user: Annotated[UserRecord, Depends(get_current_user)],
    project_id: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
) -> ApiResponse:
    require_any_permission(user, {"project:manage", "task:create"})
    if project_id and project_id not in PROJECTS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 404, "message": "项目不存在", "data": None},
        )
    from app.db.reads import list_repositories as read_repositories

    return api_success(read_repositories(project_id, include_archived=include_archived,
                                         tenant_id=current_tenant_id()))


@router.post("/api/repositories/inspect")
def inspect_repository(
    payload: RepositoryInspectPayload,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    require_permission(user, "project:manage")
    url = validate_repository_url(payload.url)
    from app.services.repository_inspector import inspect_repository_config

    try:
        result = inspect_repository_config(url, (payload.gitee_token or "").strip() or None)
    except Exception as error:
        logger.warning("仓库智能识别失败 url=%s：%s", url, error)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": 502, "message": "仓库暂时无法识别，请检查地址和授权后重试", "data": None},
        ) from error
    return api_success(result, message="仓库配置识别完成")


@router.post("/api/repositories")
def create_repository(
    payload: RepositoryConfigPayload,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    from app import main

    require_permission(user, "project:manage")
    url, whitelist, owner, project = validate_repository_payload(payload)
    require_resource_tenant(getattr(project, "tenant_id", None),
                            resource_type="project", resource_id=project.id)
    if any(item.is_active and item.project_id == payload.project_id and item.url == url for item in REPOSITORIES.values()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": 409, "message": "该项目已存在相同仓库地址", "data": None},
        )
    now = datetime.now(timezone.utc)
    token = (payload.gitee_token or "").strip()
    repository = RepositoryConfigRecord(
        id=f"repo_{secrets.token_hex(6)}",
        project_id=project.id,
        name=payload.name.strip(),
        url=url,
        default_branch=payload.default_branch.strip(),
        token_alias="gitee-token" if token else None,
        token_configured=bool(token),
        token_updated_at=now if token else None,
        test_command=payload.test_command.strip(),
        command_whitelist=whitelist,
        test_command_fallback=payload.test_command_fallback,
        auto_create_task_after_sync=payload.auto_create_task_after_sync,
        approval_owner_id=owner.id if owner else None,
        approval_owner_name=owner.name if owner else None,
        approval_mode=payload.approval_mode,
        risk_threshold=payload.risk_threshold,
        max_files=payload.max_files,
        max_retries=payload.max_retries,
        blocked_paths=[path.strip() for path in payload.blocked_paths if path.strip()],
        created_at=now,
        updated_at=now,
    )
    REPOSITORIES[repository.id] = repository
    _persist_repository(repository)
    if token:
        main.REPOSITORY_TOKENS[repository.id] = token
    create_audit_event(
        actor=user,
        event_type="REPOSITORY_CREATED",
        resource_type="repository",
        resource_id=repository.id,
        summary=f"为项目「{project.name}」新增仓库「{repository.name}」",
        diff_summary=[
            f"仓库地址：{repository.url}",
            f"默认分支：{repository.default_branch}",
            f"Gitee Token：{'已配置' if repository.token_configured else '未配置'}",
            f"测试命令：{repository.test_command}",
            f"审批策略：{repository.approval_owner_name or '未配置默认审批人'} / {repository.approval_mode}",
        ],
    )
    return api_success(repository, message="仓库配置已保存")


@router.put("/api/repositories/{repository_id}")
def update_repository(
    repository_id: str,
    payload: RepositoryConfigPayload,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    from app import main

    require_permission(user, "project:manage")
    repository = REPOSITORIES.get(repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 404, "message": "仓库配置不存在", "data": None},
        )
    require_resource_tenant(getattr(repository, "tenant_id", None),
                            resource_type="repository", resource_id=repository_id)
    # 更新仓库时其归属项目必须同属激活租户，避免跨租户把仓库挂到他人项目。
    target_project = PROJECTS.get(payload.project_id)
    if target_project is not None:
        require_resource_tenant(getattr(target_project, "tenant_id", None),
                                resource_type="project", resource_id=payload.project_id)
    url, whitelist, owner, project = validate_repository_payload(payload)
    if any(
        item.id != repository_id
        and item.is_active
        and item.project_id == payload.project_id
        and item.url == url
        for item in REPOSITORIES.values()
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": 409, "message": "该项目已存在相同仓库地址", "data": None},
        )
    now = datetime.now(timezone.utc)
    token = (payload.gitee_token or "").strip()
    if payload.gitee_token is None:
        token = main.REPOSITORY_TOKENS.get(repository_id, "")

    # 地址变化代表代码数据源变化：切换到新的 repository_id，避免旧索引、镜像和任务
    # 在同一个数据空间内继续被误认为当前仓库内容。旧配置只归档，不删除，供历史追溯。
    if repository.url != url:
        replacement = _build_repository_record(
            payload,
            project=project,
            url=url,
            whitelist=whitelist,
            owner=owner,
            now=now,
            token=token,
        )
        archived = repository.model_copy(
            update={
                "is_active": False,
                "archived_at": now,
                "superseded_by_id": replacement.id,
                "updated_at": now,
            }
        )
        REPOSITORIES[repository_id] = archived
        REPOSITORIES[replacement.id] = replacement
        _persist_repository(archived)
        _persist_repository(replacement)
        if token:
            main.REPOSITORY_TOKENS[replacement.id] = token
        else:
            main.REPOSITORY_TOKENS.pop(replacement.id, None)
        create_audit_event(
            actor=user,
            event_type="REPOSITORY_REPLACED",
            resource_type="repository",
            resource_id=replacement.id,
            summary=f"仓库「{repository.name}」已切换为新的代码仓库",
            diff_summary=[
                f"旧仓库：{repository.url}",
                f"新仓库：{replacement.url}",
                f"旧仓库 ID：{repository.id}（已归档）",
                f"新仓库 ID：{replacement.id}",
                "旧任务、旧索引和旧镜像保留用于历史追溯，新仓库从独立数据空间开始",
            ],
        )
        return api_success(replacement, message="仓库地址已切换，已创建新的仓库数据空间")

    token_configured = repository.token_configured if payload.gitee_token is None else bool(token)
    updated = repository.model_copy(
        update={
            "project_id": project.id,
            "name": payload.name.strip(),
            "url": url,
            "default_branch": payload.default_branch.strip(),
            "token_alias": repository.token_alias if payload.gitee_token is None else ("gitee-token" if token else None),
            "token_configured": token_configured,
            "token_updated_at": repository.token_updated_at if payload.gitee_token is None else (now if token else None),
            "test_command": payload.test_command.strip(),
            "command_whitelist": whitelist,
            "test_command_fallback": payload.test_command_fallback,
            "auto_create_task_after_sync": payload.auto_create_task_after_sync,
            "approval_owner_id": owner.id if owner else None,
            "approval_owner_name": owner.name if owner else None,
            "approval_mode": payload.approval_mode,
            "risk_threshold": payload.risk_threshold,
            "max_files": payload.max_files,
            "max_retries": payload.max_retries,
            "blocked_paths": [path.strip() for path in payload.blocked_paths if path.strip()],
            "updated_at": now,
        }
    )
    REPOSITORIES[repository_id] = updated
    _persist_repository(updated)
    if payload.gitee_token is not None:
        if token:
            main.REPOSITORY_TOKENS[repository_id] = token
        else:
            main.REPOSITORY_TOKENS.pop(repository_id, None)
    create_audit_event(
        actor=user,
        event_type="REPOSITORY_UPDATED",
        resource_type="repository",
        resource_id=repository_id,
        summary=f"更新仓库「{updated.name}」配置",
        diff_summary=[
            f"仓库地址：{repository.url} → {updated.url}" if repository.url != updated.url else "仓库地址未变化",
            f"默认分支：{repository.default_branch} → {updated.default_branch}" if repository.default_branch != updated.default_branch else "默认分支未变化",
            f"Gitee Token：{'已更新' if payload.gitee_token is not None and token else '保持原配置'}",
            f"测试命令：{repository.test_command} → {updated.test_command}" if repository.test_command != updated.test_command else "测试命令未变化",
            f"审批策略：{updated.approval_owner_name or '未配置默认审批人'} / {updated.approval_mode} / {updated.risk_threshold}",
        ],
    )
    return api_success(updated, message="仓库配置已保存")
