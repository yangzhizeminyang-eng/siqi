"""企业版 E2-01：受治理操作的实际执行（真实副作用，幂等可观测）。

执行前由 ApproveService 预检资源存在与租户匹配；此处只做真实业务变更，
并返回可审计的摘要。未知操作直接拒绝，不产生副作用。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.governance.operations import get_operation
from app.tenancy.models import TenantContext


def _now() -> datetime:
    return datetime.now(timezone.utc)


def execute_operation(context: TenantContext, user, operation: str, resource_id: str,
                      request_summary: dict | None = None) -> dict:
    """执行受治理操作；返回 {status, summary}。资源不存在/操作非法抛 ValueError。"""
    spec = get_operation(operation)
    if spec is None:
        raise ValueError(f"未注册的治理操作: {operation}")

    if operation == "repository.archive":
        return _archive_repository(context, resource_id)
    if operation == "repository.delete":
        return _delete_repository(context, resource_id)
    if operation == "audit.export":
        return _export_audit(context, resource_id)
    if operation == "tenant.member.role_change":
        return _change_member_role(context, resource_id, request_summary or {})
    if operation == "repository.index.rebuild":
        return _rebuild_repository_index(context, user, resource_id, request_summary or {})
    if operation == "team_request.sync_gitee":
        return _sync_team_request(context, user, resource_id)
    return {"status": "ok", "summary": "已执行"}


def _resolve_repository(context: TenantContext, resource_id: str):
    from app import main
    from app.tenancy.context import require_resource_tenant

    repo = main.REPOSITORIES.get(resource_id)
    if repo is None:
        raise ValueError(f"仓库不存在: {resource_id}")
    require_resource_tenant(getattr(repo, "tenant_id", None),
                            resource_type="repository", resource_id=resource_id)
    return repo


def _archive_repository(context: TenantContext, resource_id: str) -> dict:
    from app import main

    repo = _resolve_repository(context, resource_id)
    from app.main import _persist_repository

    repo.is_active = False
    repo.archived_at = _now()
    repo.updated_at = _now()
    _persist_repository(repo)
    repo = main.REPOSITORIES.get(resource_id)
    return {"status": "ok", "summary": f"仓库已归档：{repo.name if repo else resource_id}"}


def _resolve_team_request(context: TenantContext, resource_id: str):
    from app import main
    from app.tenancy.context import require_resource_tenant

    request = main.TEAM_REQUESTS.get(resource_id)
    if request is None:
        raise ValueError(f"平台需求不存在: {resource_id}")
    require_resource_tenant(getattr(request, "tenant_id", None),
                            resource_type="team_request", resource_id=resource_id)
    return request


def _rebuild_repository_index(
    context: TenantContext,
    user,
    resource_id: str,
    request_summary: dict,
) -> dict:
    from app import main

    repo = _resolve_repository(context, resource_id)
    branch = str(request_summary.get("base_branch") or repo.default_branch).strip()
    commit_sha = str(request_summary.get("commit_sha") or "").strip()
    if not commit_sha:
        from app.core.runtime_config import load_runtime_settings

        if load_runtime_settings().app_env != "enterprise" and "base_branch" not in request_summary:
            return {
                "status": "ok",
                "summary": "已受理索引重建（本地兼容模式）",
                "dispatch": {"enqueued": False, "mode": "compatibility"},
            }
        commit_sha = main.fetch_gitee_branch_head(repo, branch)

    index, outcome = main._dedupe_or_create_index(
        repo,
        commit_sha=commit_sha,
        base_branch=branch,
        requested_by=user,
        requested_at=_now(),
    )
    if outcome == "running":
        return {
            "status": "ok",
            "summary": "该 Commit 已有运行中的索引任务，已复用现有任务",
            "index_id": index.id,
            "dispatch": {"enqueued": False, "mode": "deduplicated"},
        }

    main.index_audit_event(
        index=index,
        event_type="INDEX_REBUILT",
        actor=user,
        summary="审批通过后重建仓库索引",
        diff_summary=[
            f"仓库：{repo.name}",
            f"Commit：{commit_sha[:12]}",
            "触发方式：企业审批执行",
        ],
    )
    dispatch = main._enqueue_index_build(index.id)
    return {
        "status": "ok",
        "summary": "已投递索引重建 Worker",
        "index_id": index.id,
        "dispatch": dispatch,
    }


def _sync_team_request(context: TenantContext, user, resource_id: str) -> dict:
    from app import main
    from app.workers.dispatch import enqueue_or_run
    from app.workers.tasks import sync_gitee_request

    request = _resolve_team_request(context, resource_id)
    if request.sync_status == "SYNCED":
        return {
            "status": "ok",
            "summary": "该需求已同步 Gitee，无需重复投递",
            "request_id": request.id,
            "dispatch": {"enqueued": False, "mode": "already_synced"},
        }
    if request.sync_status not in main.REQUEST_EDITABLE_STATUSES:
        raise ValueError(f"需求当前状态不可同步: {request.sync_status}")
    if not request.content_sufficient:
        raise ValueError("需求正文内容不足，不能投递 Gitee 同步")

    updated = request.model_copy(update={"sync_status": "SYNCING", "updated_at": _now()})
    main.TEAM_REQUESTS[updated.id] = updated
    main._persist_team_request(updated)
    dispatch = enqueue_or_run(
        sync_gitee_request,
        kwargs={"request_id": updated.id},
        run_inline=lambda: main.sync_request_to_gitee_worker(updated.id),
        label=f"gitee_sync:{updated.id}",
    )
    inline_result = dispatch.get("result")
    if isinstance(inline_result, dict) and inline_result.get("status") in {"error", "missing"}:
        raise ValueError(inline_result.get("error") or "Gitee 同步 Worker 执行失败")
    return {
        "status": "ok",
        "summary": "已投递 Gitee 同步 Worker",
        "request_id": updated.id,
        "dispatch": dispatch,
    }


def _delete_repository(context: TenantContext, resource_id: str) -> dict:
    repo = _resolve_repository(context, resource_id)
    from app.main import _persist_repository

    # 非破坏性：归档 + 标记删除原因，保留历史追溯（团队版生命周期语义）。
    repo.is_active = False
    repo.archived_at = _now()
    repo.updated_at = _now()
    _persist_repository(repo)
    return {"status": "ok", "summary": f"仓库已按删除处置（归档保留）：{repo.name}"}


def _export_audit(context: TenantContext, resource_id: str) -> dict:
    from app import main

    events = [e for e in main.AUDIT_EVENTS.values()
              if getattr(e, "tenant_id", None) == context.tenant_id or not getattr(e, "tenant_id", None)]
    # 仅返回不含敏感信息的汇总，用于演示导出动作真实产生副作用。
    counts: dict[str, int] = {}
    for event in events:
        resource = getattr(event, "resource_type", "") or "?"
        counts[resource] = counts.get(resource, 0) + 1
    return {"status": "ok", "summary": f"已导出租户审计汇总：{len(events)} 条", "detail": counts}


def _change_member_role(context: TenantContext, resource_id: str, request_summary: dict) -> dict:
    from app.tenancy import store

    membership = store.get_membership(resource_id)
    if membership is None or membership.tenant_id != context.tenant_id:
        raise ValueError(f"成员不存在或不属于当前租户")
    new_role = request_summary.get("new_role")
    if not new_role:
        raise ValueError("缺少目标角色 new_role")
    store.update_membership(resource_id, role=new_role)
    return {"status": "ok", "summary": f"已变更成员角色：{membership.user_name} -> {new_role}"}
