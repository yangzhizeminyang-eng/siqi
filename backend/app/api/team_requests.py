"""平台需求域接口（DEV-016-A/B/C）：草稿 CRUD、Gitee 同步、事件时间线、创建任务。

OPT-11：自 app/main.py 迁出（行为不变；路由表由 tests/test_opt11_main_split.py 冻结）。
迁移安全约定：
- 定义在 main.py ``app = FastAPI(...)`` 之后的辅助函数（resolve_request_*、find_team_request、
  can_view_team_request、REQUEST_STATUS_LABELS 等）一律经 ``app.main`` 惰性读取；
- 被测试 monkeypatch 的 ``main.create_gitee_issue`` / ``main.maybe_auto_create_task_after_sync`` /
  ``main.find_gitee_issue_by_source_marker`` 同样惰性读取，保证补丁继续生效；
- ``sync_request_to_gitee_worker`` / ``create_task_from_team_request_worker``（Celery Worker 入口）
  仍留在 app/main.py，本模块不做搬运。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.distributed_lock import try_acquire_team_request_lock
from app.main import (
    AUDIT_EVENTS,
    REQUEST_EDITABLE_STATUSES,
    REQUEST_TYPE_LABELS,
    REPOSITORIES,
    TEAM_REQUESTS,
    ApiResponse,
    RequestSyncStatus,
    RequestType,
    TeamRequestCreatePayload,
    TeamRequestRecord,
    TeamRequestUpdatePayload,
    UserRecord,
    _enterprise_approval_for_legacy_operation,
    _persist_team_request,
    api_success,
    create_audit_event,
    current_tenant_id,
    forbidden,
    get_current_user,
    require_permission,
    validation_error,
)

router = APIRouter(tags=["team_requests"])


@router.post("/api/team/requests")
def create_team_request(
    payload: TeamRequestCreatePayload,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    from app import main

    require_permission(user, "request:create")
    project, repository = main.resolve_request_project_repository(payload)
    request_type = main.resolve_request_type(payload.type)
    title = main.require_request_title(payload.title)
    body = main.require_request_body(payload.body)
    test_command, command_warnings = main.resolve_request_test_command(payload.test_command, repository, project)
    content_sufficient, warnings = main.evaluate_request_content(body)
    now = datetime.now(timezone.utc)
    record = TeamRequestRecord(
        id=f"team_req_{secrets.token_hex(6)}",
        request_no=f"REQ-{1000 + len(TEAM_REQUESTS) + 1}",
        project_id=project.id,
        project_name=project.name,
        repository_id=repository.id,
        repository_name=repository.name,
        type=request_type,
        title=title,
        body=body,
        reproduction_steps=(payload.reproduction_steps or "").strip(),
        acceptance_criteria=main.clean_string_list(payload.acceptance_criteria),
        priority=main.resolve_request_priority(payload.priority),
        labels=main.clean_string_list(payload.labels),
        target_branch=(payload.target_branch or "").strip() or None,
        test_command=test_command,
        sync_status="DRAFT",
        content_sufficient=content_sufficient,
        validation_warnings=warnings + command_warnings,
        created_by_id=user.id,
        created_by_name=user.name,
        created_at=now,
        updated_at=now,
    )
    TEAM_REQUESTS[record.id] = record
    _persist_team_request(record)
    main._audit_test_command_warnings(user, record.id, command_warnings)
    create_audit_event(
        actor=user,
        event_type="TEAM_REQUEST_CREATED",
        resource_type="team_request",
        resource_id=record.id,
        summary=f"创建平台需求草稿 {record.request_no}：{title}",
        diff_summary=[
            f"类型：{REQUEST_TYPE_LABELS[request_type]}（{request_type}）",
            f"项目：{project.name} / 仓库：{repository.name}",
            f"正文充足：{'是' if content_sufficient else '否，需补充信息'}",
            f"测试命令：{test_command or '未填写'}",
        ],
    )
    return api_success(record, message=f"平台需求草稿已创建：{record.request_no}")


@router.get("/api/team/requests")
def list_team_requests(
    user: Annotated[UserRecord, Depends(get_current_user)],
    project_id: str | None = Query(default=None),
    repository_id: str | None = Query(default=None),
    sync_status: RequestSyncStatus | None = Query(default=None),
    request_type: RequestType | None = Query(default=None, alias="type"),
    created_by: str | None = Query(default=None),
) -> ApiResponse:
    from app import main
    from app.db import reads

    require_permission(user, "request:view")
    records = reads.list_team_requests(tenant_id=current_tenant_id())
    records = [record for record in records if main.can_view_team_request(record, user)]
    if project_id:
        records = [record for record in records if record.project_id == project_id]
    if repository_id:
        records = [record for record in records if record.repository_id == repository_id]
    if sync_status:
        records = [record for record in records if record.sync_status == sync_status]
    if request_type:
        records = [record for record in records if record.type == request_type]
    if created_by:
        records = [record for record in records if record.created_by_id == created_by]
    return api_success(records)


@router.get("/api/team/requests/{request_id}")
def get_team_request(request_id: str, user: Annotated[UserRecord, Depends(get_current_user)]) -> ApiResponse:
    from app import main

    require_permission(user, "request:view")
    record = main.find_team_request(request_id)
    if not main.can_view_team_request(record, user):
        raise forbidden("您暂无权限查看该平台需求草稿")
    return api_success(record)


@router.patch("/api/team/requests/{request_id}")
def update_team_request(
    request_id: str,
    payload: TeamRequestUpdatePayload,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    from app import main
    from app.db import reads

    require_permission(user, "request:create")
    record = main.find_team_request(request_id)
    if record.sync_status not in REQUEST_EDITABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": 409,
                "message": f"草稿当前状态为「{main.REQUEST_STATUS_LABELS.get(record.sync_status, record.sync_status)}」，"
                "仅 DRAFT（草稿）或 SYNC_FAILED（同步失败）状态允许修改",
                "data": None,
            },
        )
    if user.role == "developer" and record.created_by_id != user.id:
        raise forbidden("开发者只能修改本人创建的平台需求草稿")

    repository = REPOSITORIES.get(record.repository_id)
    project = reads.get_project(repository.project_id) if repository else None
    update_fields: dict[str, object] = {}
    changed_fields: list[str] = []
    command_warnings: list[str] = []

    if payload.type is not None:
        request_type = main.resolve_request_type(payload.type)
        if request_type != record.type:
            changed_fields.append("类型")
        update_fields["type"] = request_type
    if payload.title is not None:
        title = main.require_request_title(payload.title)
        if title != record.title:
            changed_fields.append("标题")
        update_fields["title"] = title
    if payload.body is not None:
        body = main.require_request_body(payload.body)
        if body != record.body:
            changed_fields.append("正文")
        update_fields["body"] = body
    if payload.reproduction_steps is not None:
        value = payload.reproduction_steps.strip()
        if value != record.reproduction_steps:
            changed_fields.append("复现步骤")
        update_fields["reproduction_steps"] = value
    if payload.acceptance_criteria is not None:
        value = main.clean_string_list(payload.acceptance_criteria)
        if value != record.acceptance_criteria:
            changed_fields.append("验收标准")
        update_fields["acceptance_criteria"] = value
    if payload.priority is not None:
        value = main.resolve_request_priority(payload.priority)
        if value != record.priority:
            changed_fields.append("优先级")
        update_fields["priority"] = value
    if payload.labels is not None:
        value = main.clean_string_list(payload.labels)
        if value != record.labels:
            changed_fields.append("标签")
        update_fields["labels"] = value
    if payload.target_branch is not None:
        value = payload.target_branch.strip() or None
        if value != record.target_branch:
            changed_fields.append("期望分支")
        update_fields["target_branch"] = value
    if payload.test_command is not None:
        if not repository:
            raise validation_error("仓库配置不存在，无法校验测试命令白名单")
        value, command_warnings = main.resolve_request_test_command(payload.test_command, repository, project)
        if value != record.test_command:
            changed_fields.append("测试命令")
        update_fields["test_command"] = value

    final_body = str(update_fields.get("body", record.body))
    content_sufficient, content_warnings = main.evaluate_request_content(final_body)
    update_fields["content_sufficient"] = content_sufficient
    update_fields["validation_warnings"] = content_warnings + command_warnings
    update_fields["updated_at"] = datetime.now(timezone.utc)
    updated = record.model_copy(update=update_fields)
    TEAM_REQUESTS[record.id] = updated
    _persist_team_request(updated)
    main._audit_test_command_warnings(user, record.id, command_warnings)
    create_audit_event(
        actor=user,
        event_type="TEAM_REQUEST_UPDATED",
        resource_type="team_request",
        resource_id=record.id,
        summary=f"修改平台需求草稿 {record.request_no}",
        diff_summary=[
            f"修改字段：{'、'.join(changed_fields) if changed_fields else '无内容字段变化'}",
            f"正文充足：{'是' if content_sufficient else '否，需补充信息'}",
        ],
    )
    return api_success(updated, message="平台需求草稿已保存")


@router.post("/api/team/requests/{request_id}/sync-gitee")
def sync_team_request_to_gitee(
    request_id: str,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    """DEV-016-B：平台需求同步创建 Gitee Issue。

    幂等规则（docs/team/01-team-prd.md §8.2）：
    - 仅 DRAFT / SYNC_FAILED 允许发起同步；SYNCING 禁止并发重复同步；
    - SYNCED 直接返回已有映射，不再次调用创建接口；
    - 同步失败保留 REQ-* 草稿，置 SYNC_FAILED 并保存 failure_reason / manual_reason，不创建 Agent Task；
    - Token 过期或无权限 → manual_reason=auth_expired；
    - 网络超时/解析失败 → 重试前先按 REQ-* 来源标识查重，命中则复用映射。
    """
    from app import main

    require_permission(user, "request:create")
    record = main.find_team_request(request_id)
    if user.role == "developer" and record.created_by_id != user.id:
        raise forbidden("开发者只能同步本人创建的平台需求草稿")
    enterprise_approval = _enterprise_approval_for_legacy_operation(
        user=user,
        operation="team_request.sync_gitee",
        resource_type="team_request",
        resource_id=record.id,
        title=f"同步平台需求到 Gitee：{record.request_no}",
        request_summary={"request_no": record.request_no, "title": record.title},
    )
    if enterprise_approval is not None:
        return enterprise_approval

    acquired = try_acquire_team_request_lock("sync", record.id)
    if acquired is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": 409, "message": "该需求正在同步中，请勿重复提交，请稍后重试", "data": None},
        )
    try:
        # 锁内重读：PATCH 以 model_copy 替换对象引用，锁外等待期间可能已更新（分布式锁下跨 Worker 同样成立）。
        record = main.find_team_request(request_id)
        if record.sync_status == "SYNCING":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": 409, "message": "该需求正在同步中，请稍候", "data": None},
            )
        if record.sync_status == "SYNCED":
            return api_success(record, message="该需求已同步 Gitee，无需重复创建")
        if record.sync_status not in REQUEST_EDITABLE_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": 409,
                    "message": f"草稿当前状态为「{main.REQUEST_STATUS_LABELS.get(record.sync_status, record.sync_status)}」，"
                    "仅 DRAFT（草稿）或 SYNC_FAILED（同步失败）状态允许同步",
                    "data": None,
                },
            )
        if not record.content_sufficient:
            raise validation_error("正文内容不足，请先补充后再同步 Gitee")

        now = datetime.now(timezone.utc)
        retrying = record.sync_status == "SYNC_FAILED"
        if retrying:
            create_audit_event(
                actor=user,
                event_type="GITEE_ISSUE_SYNC_RETRIED",
                resource_type="team_request",
                resource_id=record.id,
                summary=f"重试同步平台需求 {record.request_no} 到 Gitee",
                diff_summary=[f"上次失败原因：{record.failure_reason or '未知'}"],
            )

        record = record.model_copy(update={"sync_status": "SYNCING", "updated_at": now})
        TEAM_REQUESTS[record.id] = record
        _persist_team_request(record)

        from app.workers.dispatch import celery_enabled, enqueue_or_run

        if celery_enabled():
            # 生产异步：Gitee 同步真实长任务投递给 Worker，HTTP 只返回 SYNCING+queued，前端轮询
            from app.workers.tasks import sync_gitee_request as sync_task

            outcome = enqueue_or_run(
                sync_task,
                kwargs={"request_id": request_id},
                run_inline=lambda: None,
                label=f"gitee_sync:{request_id}",
            )
            return api_success(
                {"request_id": record.id, "request_no": record.request_no, "sync_status": "SYNCING", "queued": True, "dispatch": outcome},
                message=f"同步已进入队列由 Worker 执行（{outcome.get('mode')}）",
            )

        def mark_sync_failed(failure_reason: str, manual_reason: str) -> ApiResponse:
            failed = record.model_copy(
                update={
                    "sync_status": "SYNC_FAILED",
                    "failure_reason": failure_reason,
                    "manual_reason": manual_reason,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            TEAM_REQUESTS[record.id] = failed
            _persist_team_request(failed)
            create_audit_event(
                actor=user,
                event_type="GITEE_ISSUE_SYNC_FAILED",
                resource_type="team_request",
                resource_id=record.id,
                summary=f"平台需求 {record.request_no} 同步 Gitee 失败",
                diff_summary=[f"失败原因：{failure_reason}", f"处理方式：{manual_reason}"],
            )
            return api_success(failed, message=failure_reason)

        try:
            issue = main.create_gitee_issue(record)
        except HTTPException as error:
            detail = error.detail if isinstance(error.detail, dict) else {}
            error_type = detail.get("error_type", "")
            message = detail.get("message") or "Gitee Issue 创建失败"
            if error_type == "auth_expired":
                return mark_sync_failed("Gitee Token 已过期或无权限，请重新授权后重试", "auth_expired")
            if error_type == "network_timeout":
                try:
                    existing = main.find_gitee_issue_by_source_marker(record.repository_id, record.request_no)
                except HTTPException as dedupe_error:
                    dedupe_detail = dedupe_error.detail if isinstance(dedupe_error.detail, dict) else {}
                    if dedupe_detail.get("error_type") == "auth_expired" or dedupe_error.status_code in (401, 403):
                        return mark_sync_failed("Gitee Token 已过期或无权限，请重新授权后重试", "auth_expired")
                    raise
                if existing:
                    synced = record.model_copy(
                        update={
                            "gitee_issue_id": str(existing.get("id", "")),
                            "gitee_issue_number": str(existing.get("number", "")),
                            "gitee_issue_url": existing.get("html_url") or existing.get("url") or "",
                            "sync_status": "SYNCED",
                            "failure_reason": None,
                            "manual_reason": None,
                            "updated_at": datetime.now(timezone.utc),
                        }
                    )
                    TEAM_REQUESTS[record.id] = synced
                    _persist_team_request(synced)
                    create_audit_event(
                        actor=user,
                        event_type="GITEE_ISSUE_SYNCED",
                        resource_type="team_request",
                        resource_id=record.id,
                        summary=f"平台需求 {record.request_no} 同步 Gitee 成功（超时查重复用）",
                        diff_summary=[f"Gitee Issue：{synced.gitee_issue_url}", "检测到来源标识一致的已存在 Issue，复用映射"],
                    )
                    # DEV-016-C：同步成功后按仓库开关自动创建任务（失败不回滚同步）
                    synced = main.maybe_auto_create_task_after_sync(synced, user)
                    auto_message = "，并已自动创建 Agent 任务" if synced.sync_status == "TASK_CREATED" else ""
                    return api_success(synced, message=f"同步成功（检测到来源标识一致的 Gitee Issue，已复用映射）{auto_message}")
                return mark_sync_failed(message, "gitee_sync_failed")
            return mark_sync_failed(message, "gitee_sync_failed")

        if not isinstance(issue, dict):
            return mark_sync_failed("Gitee Issue 创建成功响应格式异常，无法保存映射关系", "gitee_sync_failed")
        issue_id = str(issue.get("id") or "").strip()
        issue_number = str(issue.get("number") or "").strip()
        issue_url = str(issue.get("html_url") or issue.get("url") or "").strip()
        if not issue_id or not issue_number:
            return mark_sync_failed("Gitee Issue 创建成功响应缺少 Issue 编号，无法保存映射关系", "gitee_sync_failed")
        if not issue_url:
            return mark_sync_failed("Gitee Issue 创建成功响应缺少访问地址，无法保存映射关系", "gitee_sync_failed")

        synced = record.model_copy(
            update={
                "gitee_issue_id": issue_id,
                "gitee_issue_number": issue_number,
                "gitee_issue_url": issue_url,
                "sync_status": "SYNCED",
                "failure_reason": None,
                "manual_reason": None,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        TEAM_REQUESTS[record.id] = synced
        _persist_team_request(synced)
        create_audit_event(
            actor=user,
            event_type="GITEE_ISSUE_SYNCED",
            resource_type="team_request",
            resource_id=record.id,
            summary=f"平台需求 {record.request_no} 同步 Gitee 成功",
            diff_summary=[f"Gitee Issue：{synced.gitee_issue_url}", f"Issue 编号：{synced.gitee_issue_number}"],
        )
        # DEV-016-C：同步成功后按仓库开关自动创建任务（失败不回滚同步，保持 SYNCED 并留手动入口）
        synced = main.maybe_auto_create_task_after_sync(synced, user)
        sync_message = "已同步 Gitee Issue，并已自动创建 Agent 任务" if synced.sync_status == "TASK_CREATED" else "已同步 Gitee Issue"
        return api_success(synced, message=sync_message)
    finally:
        acquired.release()


@router.get("/api/team/requests/{request_id}/events")
def list_team_request_events(
    request_id: str,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    """平台需求同步回放：返回该需求（resource_type=team_request）的全部审计事件时间线，按时间升序。"""
    from app import main

    require_permission(user, "request:view")
    record = main.find_team_request(request_id)
    if not main.can_view_team_request(record, user):
        raise forbidden("您暂无权限查看该平台需求草稿")
    events = [
        event
        for event in AUDIT_EVENTS.values()
        if event.resource_type == "team_request" and event.resource_id == record.id
    ]
    events.sort(key=lambda item: item.created_at)
    return api_success({"request_id": record.id, "request_no": record.request_no, "events": events})


@router.post("/api/team/requests/{request_id}/create-task")
def create_task_from_team_request(
    request_id: str,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    """DEV-016-C：从已同步平台需求一键创建/复用 Agent 任务。

    - 权限走团队版 request:create + 请求归属（不卡旧 /api/tasks 的 task:create 门禁）；
    - 仅 SYNCED / TASK_CREATED 可触发；同 Issue 未结束任务返回 existing_task + jump_url；
    - 已结束任务允许新建版本；同一需求并发创建由 Redis 分布式锁（scope=create_task）+ 任务创建锁双重幂等。
    """
    from app import main

    require_permission(user, "request:create")
    record = main.find_team_request(request_id)
    if user.role == "developer" and record.created_by_id != user.id:
        raise forbidden("开发者只能为本人创建的平台需求创建 Agent 任务")
    if record.sync_status not in {"SYNCED", "TASK_CREATED"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": 409,
                "message": f"需求当前状态为「{main.REQUEST_STATUS_LABELS.get(record.sync_status, record.sync_status)}」，"
                "请先完成 Gitee Issue 同步后再创建 Agent 任务",
                "data": None,
            },
        )

    acquired = try_acquire_team_request_lock("create_task", record.id)
    if acquired is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": 409, "message": "该需求正在创建 Agent 任务，请勿重复提交，请稍后重试", "data": None},
        )
    try:
        # 锁内重读：等待期间同步状态/关联任务可能已变化（分布式锁下跨 Worker 同样成立）
        record = main.find_team_request(request_id)
        if record.sync_status not in {"SYNCED", "TASK_CREATED"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": 409,
                    "message": f"需求当前状态为「{main.REQUEST_STATUS_LABELS.get(record.sync_status, record.sync_status)}」，"
                    "请先完成 Gitee Issue 同步后再创建 Agent 任务",
                    "data": None,
                },
            )
        outcome = main.derive_task_from_team_request(record, user, auto=False)
    finally:
        acquired.release()

    return api_success(
        {
            "task": outcome["task"].model_dump(mode="json"),
            "request": outcome["request"].model_dump(mode="json"),
            "jump_url": outcome["jump_url"],
            "created": outcome["created"],
            "reused": outcome["reused"],
        },
        message=outcome["message"],
    )
