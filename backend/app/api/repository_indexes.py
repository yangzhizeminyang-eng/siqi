"""仓库索引域接口：触发/重建/列表/详情/重试。

OPT-11：自 app/main.py 迁出（行为不变；路由表由 tests/test_opt11_main_split.py 冻结）。
`fetch_gitee_branch_head` 与 `_enqueue_index_build` 会被测试 monkeypatch，
经 `app.main` 模块属性在调用时读取；`progress_repository_index_jobs` 等
阶段推进器仍留在 app.main（内部读取 `main.INDEX_STAGE_SECONDS`）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.main import (
    REPOSITORIES,
    REPOSITORY_INDEXES,
    ApiResponse,
    IndexTriggerPayload,
    UserRecord,
    _dedupe_or_create_index,
    _enterprise_approval_for_legacy_operation,
    _persist_repository_index,
    api_success,
    current_tenant_id,
    get_current_user,
    index_audit_event,
    progress_repository_index_jobs,
    require_permission,
    require_resource_tenant,
)

router = APIRouter(tags=["repository_indexes"])


@router.post("/api/repositories/{repository_id}/index")
def trigger_repository_index(
    repository_id: str,
    payload: IndexTriggerPayload,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    from app import main

    require_permission(user, "index:manage")
    repository = REPOSITORIES.get(repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 404, "message": "仓库配置不存在", "data": None},
        )
    require_resource_tenant(getattr(repository, "tenant_id", None),
                            resource_type="repository", resource_id=repository_id)
    branch = (payload.base_branch or "").strip() or repository.default_branch
    now = datetime.now(timezone.utc)
    head_commit = (payload.commit_sha or "").strip() or main.fetch_gitee_branch_head(repository, branch)
    index, outcome = _dedupe_or_create_index(
        repository,
        commit_sha=head_commit,
        base_branch=branch,
        requested_by=user,
        requested_at=now,
        dedupe_completed=not payload.force_rebuild,
    )
    if outcome in {"running", "completed"}:
        return api_success(
            {
                "index_id": index.id,
                "repository_id": repository.id,
                "repository_name": repository.name,
                "status": index.status,
                "commit_sha": index.commit_sha,
                "mode": index.mode,
            },
            message=(
                "该 Commit 已有运行中的索引任务，直接返回现有任务"
                if outcome == "running"
                else "该 Commit 已有完成索引，可直接复用"
            ),
        )
    index_audit_event(
        index=index,
        event_type="INDEX_CREATED",
        actor=user,
        summary="用户手动触发仓库索引",
        diff_summary=[f"仓库：{repository.name}", f"Commit：{head_commit[:12]}", "触发方式：手动", "索引任务已排队，无需停留等待，可稍后查看进度"],
    )
    main._enqueue_index_build(index.id)
    return api_success(
        {
            "index_id": index.id,
            "repository_id": repository.id,
            "repository_name": repository.name,
            "status": index.status,
            "commit_sha": index.commit_sha,
            "mode": index.mode,
        },
        message="索引任务已创建，处理异步进行，可在索引历史中查看进度",
    )


@router.post("/api/repositories/{repository_id}/index/rebuild")
def rebuild_repository_index(
    repository_id: str,
    payload: IndexTriggerPayload,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    from app import main

    require_permission(user, "index:manage")
    repository = REPOSITORIES.get(repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 404, "message": "仓库配置不存在", "data": None},
        )
    require_resource_tenant(getattr(repository, "tenant_id", None),
                            resource_type="repository", resource_id=repository_id)
    enterprise_approval = _enterprise_approval_for_legacy_operation(
        user=user,
        operation="repository.index.rebuild",
        resource_type="repository",
        resource_id=repository.id,
        title=f"重建仓库索引：{repository.name}",
        request_summary={"repository_name": repository.name, "base_branch": repository.default_branch},
    )
    if enterprise_approval is not None:
        return enterprise_approval
    branch = (payload.base_branch or "").strip() or repository.default_branch
    now = datetime.now(timezone.utc)
    head_commit = (payload.commit_sha or "").strip() or main.fetch_gitee_branch_head(repository, branch)
    index, outcome = _dedupe_or_create_index(
        repository,
        commit_sha=head_commit,
        base_branch=branch,
        requested_by=user,
        requested_at=now,
    )
    if outcome == "running":
        return api_success(
            {
                "index_id": index.id,
                "repository_id": repository.id,
                "repository_name": repository.name,
                "status": index.status,
                "commit_sha": index.commit_sha,
                "rebuild_mode": index.mode,
            },
            message="该 Commit 已有运行中的索引任务，已合并本次重建请求，未重复创建",
        )
    index_audit_event(
        index=index,
        event_type="INDEX_REBUILT",
        actor=user,
        summary="用户重建仓库索引",
        diff_summary=[f"仓库：{repository.name}", f"Commit：{head_commit[:12]}", "重建模式：full（增量索引为后续版本能力）"],
    )
    main._enqueue_index_build(index.id)
    return api_success(
        {
            "index_id": index.id,
            "repository_id": repository.id,
            "repository_name": repository.name,
            "status": index.status,
            "commit_sha": index.commit_sha,
            "rebuild_mode": index.mode,
        },
        message="索引重建任务已创建，完成后旧索引仍保留供人工查看来源",
    )


@router.get("/api/repositories/{repository_id}/indexes")
def list_repository_indexes(
    repository_id: str,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    require_permission(user, "index:view")
    from app.db.reads import list_repository_indexes as read_indexes

    repository = REPOSITORIES.get(repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 404, "message": "仓库配置不存在", "data": None},
        )
    require_resource_tenant(getattr(repository, "tenant_id", None),
                            resource_type="repository", resource_id=repository_id)
    progress_repository_index_jobs()
    return api_success(read_indexes(repository.id, tenant_id=current_tenant_id()))


@router.get("/api/repositories/{repository_id}/indexes/{index_id}")
def get_repository_index(
    repository_id: str,
    index_id: str,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    require_permission(user, "index:view")
    from app.db import reads

    repository = reads.get_repository(repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 404, "message": "仓库配置不存在", "data": None},
        )
    require_resource_tenant(getattr(repository, "tenant_id", None),
                            resource_type="repository", resource_id=repository_id)
    progress_repository_index_jobs()
    record = reads.get_index(index_id)
    if not record or record.repository_id != repository.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 404, "message": "索引任务不存在", "data": None},
        )
    require_resource_tenant(getattr(record, "tenant_id", None),
                            resource_type="repository_index", resource_id=index_id)
    return api_success(record)


@router.post("/api/repositories/{repository_id}/indexes/{index_id}/retry")
def retry_repository_index(
    repository_id: str,
    index_id: str,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    require_permission(user, "index:manage")
    repository = REPOSITORIES.get(repository_id)
    if not repository:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 404, "message": "仓库配置不存在", "data": None},
        )
    require_resource_tenant(getattr(repository, "tenant_id", None),
                            resource_type="repository", resource_id=repository_id)
    record = REPOSITORY_INDEXES.get(index_id)
    if not record or record.repository_id != repository.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 404, "message": "索引任务不存在", "data": None},
        )
    require_resource_tenant(getattr(record, "tenant_id", None),
                            resource_type="repository_index", resource_id=index_id)
    if record.status != "FAILED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": 409, "message": "只有失败状态的索引任务可以重试", "data": None},
        )
    now = datetime.now(timezone.utc)
    record.retry_count += 1
    record.status = "PENDING"
    record.error_message = None
    record.error_stage = None
    record.finished_at = None
    record.updated_at = now
    record.active_key = f"{record.repository_id}:{record.commit_sha}"
    try:
        _persist_repository_index(record)
    except Exception:
        record.status = "FAILED"
        record.active_key = None
        raise
    index_audit_event(
        index=record,
        event_type="INDEX_RETRIED",
        actor=user,
        summary="用户重试失败的仓库索引",
        diff_summary=[f"索引 ID：{record.id}", f"Commit：{record.commit_sha[:12]}", f"重试次数：{record.retry_count}"],
    )
    # OPT-09：重试状态同步进生命周期记录（失败恢复有状态）
    from app.enterprise_rag import index_lifecycle

    index_lifecycle.mark_retry(record)
    return api_success(record, message="索引任务已重新进入队列")
