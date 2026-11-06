"""数据库持久化辅助。

唯一真源：SQLAlchemy 表。业务对象通过 `persist_*` 写透、`load_*` 读回；
HTTP 层模块级 dict 只是「启动时从 DB 加载的缓存 + 运行时写透」的工作存储。
由 `USE_SQLALCHEMY`/`use_sqlalchemy` 统一开关：测试关闭时所有落库为空操作。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.db.session import session_scope
from app.models.orm import (
    AgentCheckpointORM,
    ApprovalApplicationORM,
    AuditEventORM,
    CodeChunkORM,
    CommentORM,
    DiffORM,
    EnterpriseRagEvaluationRunORM,
    EnterpriseRagIndexLifecycleORM,
    EnterpriseRuntimeCheckpointORM,
    EnterpriseRuntimeTaskORM,
    EnterpriseRuntimeTraceSpanORM,
    ExecutionStepORM,
    HistoricalTaskIndexORM,
    HistoricalTaskSummaryORM,
    IndexSnapshotORM,
    NotificationORM,
    OrganizationORM,
    PasswordResetORM,
    PlanORM,
    PrDescriptionORM,
    ProjectORM,
    RepositoryORM,
    RepositoryIndexORM,
    RetrievalRecordORM,
    TaskEventORM,
    TaskORM,
    TeamRequestORM,
    TenantMembershipORM,
    TenantORM,
    TestRunORM,
    UserORM,
)


def _model_value(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


def persistence_enabled() -> bool:
    """P0 持久化总开关：USE_SQLALCHEMY=false（测试）时所有落库为空操作。"""
    from app.core.runtime_config import load_runtime_settings

    return load_runtime_settings().use_sqlalchemy


def shared_persistence_enabled() -> bool:
    """跨进程共享 store（评测 run / 索引生命周期 / trace / 审计）的持久化开关。

    默认严格跟随 `USE_SQLALCHEMY`，生产行为不变。显式设置
    `ENTERPRISE_SHARED_PERSISTENCE` 可让隔离验收在业务运行时保持内存态的同时，
    仍把跨进程共享状态落到独立数据库（默认不设置 = 跟随 USE_SQLALCHEMY）。
    """
    raw = os.getenv("ENTERPRISE_SHARED_PERSISTENCE")
    if raw is not None and raw.strip():
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return persistence_enabled()


def _js(value: Any) -> Any:
    """把 pydantic 子模型 / 列表 / dict 转成 JSON 安全对象（ISO 字符串），保证往返精确。"""
    if value is None:
        return None
    if hasattr(value, "model_dump_json"):
        return json.loads(value.model_dump_json())
    if isinstance(value, list):
        return [_js(item) for item in value]
    if isinstance(value, dict):
        return {k: _js(v) for k, v in value.items()}
    return value


def _rt_steps_js(value: Any) -> Any:
    """Runtime 步骤专用 JSON 安全序列化：datetime → ISO（避免 JSON 列无法序列化时间）。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump_json"):
        return json.loads(value.model_dump_json())
    if isinstance(value, list):
        return [_rt_steps_js(item) for item in value]
    if isinstance(value, dict):
        return {k: _rt_steps_js(v) for k, v in value.items()}
    return value


def upsert_repository_index(record: Any) -> None:
    if not persistence_enabled():
        return
    with session_scope() as session:
        row = session.get(RepositoryIndexORM, _model_value(record, "id"))
        if row is None:
            row = RepositoryIndexORM(id=_model_value(record, "id"))
            session.add(row)
        for field in [
            "tenant_id",
            "repository_id",
            "project_id",
            "base_branch",
            "commit_sha",
            "status",
            "mode",
            "total_files",
            "processed_files",
            "chunk_count",
            "milvus_collection",
            "error_message",
            "error_stage",
            "requested_by",
            "requested_by_name",
            "retry_count",
            "requested_at",
            "created_at",
            "updated_at",
            "started_at",
            "finished_at",
            "active_key",
        ]:
            setattr(row, field, _model_value(record, field))


def find_running_repository_index(repository_id: str, commit_sha: str) -> dict[str, Any] | None:
    """读取同仓库同 Commit 的唯一运行中索引，供跨进程创建去重使用。"""
    if not persistence_enabled():
        return None
    running = ("PENDING", "SYNCING", "PARSING", "EMBEDDING", "STORING")
    with session_scope() as session:
        row = session.scalars(
            select(RepositoryIndexORM)
            .where(
                RepositoryIndexORM.repository_id == repository_id,
                RepositoryIndexORM.commit_sha == commit_sha,
                RepositoryIndexORM.status.in_(running),
            )
            .order_by(RepositoryIndexORM.created_at.desc(), RepositoryIndexORM.id.desc())
        ).first()
        if row is None:
            return None
        return _row_dict(
            row,
            [
                "id", "repository_id", "project_id", "base_branch", "commit_sha", "status", "mode",
                "total_files", "processed_files", "chunk_count", "milvus_collection", "error_message",
                "error_stage", "requested_by", "requested_by_name", "retry_count", "requested_at",
                "created_at", "updated_at", "started_at", "finished_at", "active_key",
            ],
        )


def upsert_code_chunks(chunks: list[Any]) -> None:
    if not chunks:
        return
    with session_scope() as session:
        for chunk in chunks:
            row = session.get(CodeChunkORM, _model_value(chunk, "chunk_id"))
            if row is None:
                row = CodeChunkORM(chunk_id=_model_value(chunk, "chunk_id"))
                session.add(row)
            for field in [
                "tenant_id",
                "repository_id",
                "index_id",
                "file_path",
                "chunk_index",
                "content",
                "language",
                "symbol_name",
                "start_line",
                "end_line",
                "commit_sha",
                "milvus_collection",
                "milvus_pk",
                "checksum",
            ]:
                setattr(row, field, _model_value(chunk, field))


def upsert_index_snapshot(index_id: str, repository_id: str, commit_sha: str, files: list[Any]) -> None:
    """持久化单索引的仓库代码快照（CodeFile 列表），供非终态重启恢复。"""
    if not persistence_enabled():
        return
    with session_scope() as session:
        row = session.get(IndexSnapshotORM, index_id)
        if row is None:
            row = IndexSnapshotORM(index_id=index_id, created_at=datetime.now(timezone.utc))
            session.add(row)
        row.repository_id = repository_id
        row.commit_sha = commit_sha
        row.files = _js(files)


def load_index_snapshots() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(IndexSnapshotORM)).all()
        out = []
        for r in rows:
            item = _row_dict(r, ["index_id", "repository_id", "commit_sha", "created_at"], convert_tz=False)
            item["files"] = getattr(r, "files") or []
            out.append(item)
        return out


def upsert_retrieval_record(record: Any) -> None:
    with session_scope() as session:
        row = session.get(RetrievalRecordORM, _model_value(record, "id"))
        if row is None:
            row = RetrievalRecordORM(id=_model_value(record, "id"))
            session.add(row)
        for field in [
            "tenant_id",
            "task_id",
            "query_text",
            "repository_id",
            "project_id",
            "commit_sha",
            "index_id",
            "top_k",
            "mode",
            "fresh",
            "threshold",
            "empty_reason",
            "result_chunk_ids",
            "created_at",
        ]:
            setattr(row, field, _model_value(record, field))


def append_task_event(event: Any, task_id: str) -> None:
    with session_scope() as session:
        row = TaskEventORM(
            id=_model_value(event, "id"),
            task_id=task_id,
            event_type=_model_value(event, "event_type"),
            summary=_model_value(event, "summary"),
            diff_summary=list(_model_value(event, "diff_summary", [])),
            created_at=_model_value(event, "created_at", datetime.now(timezone.utc)),
        )
        session.add(row)


def append_execution_step(step: Any) -> None:
    with session_scope() as session:
        row = ExecutionStepORM(
            id=_model_value(step, "id"),
            task_id=_model_value(step, "task_id"),
            event_id=_model_value(step, "event_id"),
            node=_model_value(step, "node"),
            title=_model_value(step, "title"),
            status=_model_value(step, "status"),
            input=_model_value(step, "input"),
            output=_model_value(step, "output"),
            duration_ms=_model_value(step, "duration_ms"),
            failure_reason=_model_value(step, "failure_reason"),
            blocked_reason=_model_value(step, "blocked_reason"),
            created_at=_model_value(step, "created_at", datetime.now(timezone.utc)),
        )
        session.add(row)


def upsert_checkpoint(task_id: str, node_name: str, payload: dict[str, Any], *, status: str = "running", checkpoint_id: str | None = None, graph_name: str = "task_workflow") -> None:
    checkpoint_id = checkpoint_id or f"cp_{task_id}"
    with session_scope() as session:
        row = session.get(AgentCheckpointORM, checkpoint_id)
        if row is None:
            row = AgentCheckpointORM(id=checkpoint_id, task_id=task_id)
            session.add(row)
        row.task_id = task_id
        row.graph_name = graph_name
        row.node_name = node_name
        row.status = status
        row.payload = payload
        row.updated_at = datetime.now(timezone.utc)


# ============================================================================
# P0 业务数据持久化：写透 / 读回（全部受 persistence_enabled() 开关控制）
# ============================================================================


def upsert_user(user: Any) -> None:
    if not persistence_enabled():
        return
    with session_scope() as session:
        row = session.get(UserORM, _model_value(user, "id"))
        if row is None:
            row = UserORM(id=_model_value(user, "id"))
            session.add(row)
        row.name = _model_value(user, "name")
        row.email = _model_value(user, "email")
        row.role = _model_value(user, "role")
        row.password_hash = _model_value(user, "password_hash")


def upsert_password_reset(record: Any) -> None:
    if not persistence_enabled():
        return
    with session_scope() as session:
        row = session.get(PasswordResetORM, _model_value(record, "id"))
        if row is None:
            row = PasswordResetORM(id=_model_value(record, "id"))
            session.add(row)
        row.email = _model_value(record, "email")
        row.user_name = _model_value(record, "user_name")
        row.role_label = _model_value(record, "role_label")
        row.status = _model_value(record, "status")
        row.requested_at = _model_value(record, "requested_at")
        row.handled_by = _model_value(record, "handled_by")
        row.handled_at = _model_value(record, "handled_at")


def upsert_project(project: Any) -> None:
    if not persistence_enabled():
        return
    with session_scope() as session:
        row = session.get(ProjectORM, _model_value(project, "id"))
        if row is None:
            row = ProjectORM(id=_model_value(project, "id"))
            session.add(row)
        row.name = _model_value(project, "name")
        row.tenant_id = _model_value(project, "tenant_id")
        row.description = _model_value(project, "description")
        row.default_test_command = _model_value(project, "default_test_command")
        row.created_at = _model_value(project, "created_at")
        row.updated_at = _model_value(project, "updated_at")


def upsert_repository(repo: Any) -> None:
    if not persistence_enabled():
        return
    with session_scope() as session:
        row = session.get(RepositoryORM, _model_value(repo, "id"))
        if row is None:
            row = RepositoryORM(id=_model_value(repo, "id"))
            session.add(row)
        for field in [
            "tenant_id",
            "project_id",
            "name",
            "url",
            "default_branch",
            "token_alias",
            "token_configured",
            "token_updated_at",
            "test_command",
            "command_whitelist",
            "test_command_fallback",
            "auto_create_task_after_sync",
            "approval_owner_id",
            "approval_owner_name",
            "approval_mode",
            "risk_threshold",
            "max_files",
            "max_retries",
            "blocked_paths",
            "created_at",
            "updated_at",
            "is_active",
            "archived_at",
            "superseded_by_id",
        ]:
            setattr(row, field, _js(_model_value(repo, field)))


def upsert_audit_event(event: Any) -> None:
    if not shared_persistence_enabled():
        return
    with session_scope() as session:
        row = session.get(AuditEventORM, _model_value(event, "id"))
        if row is None:
            row = AuditEventORM(id=_model_value(event, "id"))
            session.add(row)
        row.event_type = _model_value(event, "event_type")
        row.tenant_id = _model_value(event, "tenant_id")
        row.resource_type = _model_value(event, "resource_type")
        row.resource_id = _model_value(event, "resource_id")
        row.actor_id = _model_value(event, "actor_id")
        row.actor_name = _model_value(event, "actor_name")
        row.summary = _model_value(event, "summary")
        row.diff_summary = _js(_model_value(event, "diff_summary"))
        row.created_at = _model_value(event, "created_at")
        row.execution_step_ids = _js(_model_value(event, "execution_step_ids"))


def upsert_team_request(request: Any) -> None:
    if not persistence_enabled():
        return
    with session_scope() as session:
        row = session.get(TeamRequestORM, _model_value(request, "id"))
        if row is None:
            row = TeamRequestORM(id=_model_value(request, "id"))
            session.add(row)
        for field in [
            "tenant_id",
            "request_no",
            "project_id",
            "project_name",
            "repository_id",
            "repository_name",
            "type",
            "title",
            "body",
            "reproduction_steps",
            "acceptance_criteria",
            "priority",
            "labels",
            "target_branch",
            "test_command",
            "sync_status",
            "content_sufficient",
            "validation_warnings",
            "failure_reason",
            "manual_reason",
            "gitee_issue_id",
            "gitee_issue_number",
            "gitee_issue_url",
            "task_id",
            "created_by_id",
            "created_by_name",
            "created_at",
            "updated_at",
        ]:
            setattr(row, field, _js(_model_value(request, field)))


def upsert_comment(comment: Any) -> None:
    if not persistence_enabled():
        return
    with session_scope() as session:
        row = session.get(CommentORM, _model_value(comment, "id"))
        if row is None:
            row = CommentORM(id=_model_value(comment, "id"))
            session.add(row)
        for field in [
            "task_id",
            "project_id",
            "repository_id",
            "author_id",
            "author_name",
            "body",
            "mentioned_user_ids",
            "reference_type",
            "reference_id",
            "created_at",
            "deleted_at",
        ]:
            setattr(row, field, _js(_model_value(comment, field)))


def upsert_notification(notification: Any) -> None:
    if not persistence_enabled():
        return
    with session_scope() as session:
        row = session.get(NotificationORM, _model_value(notification, "id"))
        if row is None:
            row = NotificationORM(id=_model_value(notification, "id"))
            session.add(row)
        for field in [
            "recipient_id",
            "task_id",
            "type",
            "title",
            "body",
            "actor_id",
            "actor_name",
            "priority",
            "read_at",
            "created_at",
        ]:
            setattr(row, field, _js(_model_value(notification, field)))


def upsert_historical_task_summary(record: Any) -> None:
    if not persistence_enabled():
        return
    with session_scope() as session:
        row = session.get(HistoricalTaskSummaryORM, _model_value(record, "id"))
        if row is None:
            row = HistoricalTaskSummaryORM(id=_model_value(record, "id"))
            session.add(row)
        for field in [
            "task_id", "project_id", "repository_id", "source_task_title", "issue_summary",
            "context_summary", "analysis_summary", "plan_summary", "diff_summary",
            "test_summary", "pr_summary", "replay_summary", "searchable_text",
            "summary_version", "completed_at", "created_at", "updated_at",
        ]:
            setattr(row, field, _js(_model_value(record, field)))


def upsert_historical_task_index(record: Any) -> None:
    if not persistence_enabled():
        return
    with session_scope() as session:
        row = session.get(HistoricalTaskIndexORM, _model_value(record, "id"))
        if row is None:
            row = HistoricalTaskIndexORM(id=_model_value(record, "id"))
            session.add(row)
        for field in [
            "summary_id", "task_id", "project_id", "embedding_provider", "embedding_model",
            "vector_document_id", "index_status", "failure_reason", "failure_context",
            "indexed_at", "retry_count", "created_at", "updated_at",
        ]:
            setattr(row, field, _js(_model_value(record, field)))


def load_historical_task_summaries() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(HistoricalTaskSummaryORM)).all()
        return [
            _row_dict(r, [
                "id", "task_id", "project_id", "repository_id", "source_task_title", "issue_summary",
                "context_summary", "analysis_summary", "plan_summary", "diff_summary",
                "test_summary", "pr_summary", "replay_summary", "searchable_text",
                "summary_version", "completed_at", "created_at", "updated_at",
            ]) for r in rows
        ]


def load_historical_task_indexes() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(HistoricalTaskIndexORM)).all()
        return [
            _row_dict(r, [
                "id", "summary_id", "task_id", "project_id", "embedding_provider", "embedding_model",
                "vector_document_id", "index_status", "failure_reason", "failure_context",
                "indexed_at", "retry_count", "created_at", "updated_at",
            ]) for r in rows
        ]


# TaskORM 顶层标量列（其余嵌套字段走 JSON 列）
_TASK_SCALAR_FIELDS = [
    "id",
    "version",
    "tenant_id",
    "project_id",
    "project_name",
    "repository_id",
    "repository_name",
    "repository_url",
    "issue_id",
    "title",
    "status",
    "risk_level",
    "risk_source",
    "base_branch",
    "test_command",
    "approval_owner_id",
    "approval_owner_name",
    "work_branch",
    "automation_state",
    "manual_required",
    "manual_reason",
    "next_action",
    "failure_node",
    "failure_reason",
    "failed_at",
    "retry_count",
    "base_commit_sha",
    "team_request_id",
    "team_request_no",
    "gitee_issue_url",
    "related_index_id",
    "pr_number",
    "pr_url",
    "pr_status",
    "cancelled_at",
    "created_by",
    "created_by_name",
    "created_at",
    "updated_at",
]
_TASK_JSON_FIELDS = [
    "issue_snapshot",
    "analysis",
    "code_contexts",
    "similar_task_refs",
    "events",
    "execution_steps",
]


def upsert_task(task: Any) -> None:
    if not persistence_enabled():
        return
    with session_scope() as session:
        row = session.get(TaskORM, _model_value(task, "id"))
        if row is None:
            row = TaskORM(id=_model_value(task, "id"))
            session.add(row)
        for field in _TASK_SCALAR_FIELDS:
            setattr(row, field, _model_value(task, field))
        for field in _TASK_JSON_FIELDS:
            setattr(row, field, _js(_model_value(task, field)))
    _replace_task_children(task)


def _replace_task_children(task: Any) -> None:
    task_id = _model_value(task, "id")
    plans = _model_value(task, "plans") or []
    diffs = _model_value(task, "diffs") or []
    test_runs = _model_value(task, "test_runs") or []
    pr_descriptions = _model_value(task, "pr_descriptions") or []
    with session_scope() as session:
        session.execute(delete(PlanORM).where(PlanORM.task_id == task_id))
        session.execute(delete(DiffORM).where(DiffORM.task_id == task_id))
        session.execute(delete(TestRunORM).where(TestRunORM.task_id == task_id))
        session.execute(delete(PrDescriptionORM).where(PrDescriptionORM.task_id == task_id))
        for p in plans:
            session.add(_take_plan(p, task_id))
        for d in diffs:
            session.add(_take_diff(d, task_id))
        for t in test_runs:
            session.add(_take_test_run(t, task_id))
        for pr in pr_descriptions:
            session.add(_take_pr_description(pr, task_id))


def _take_plan(p: Any, task_id: str) -> PlanORM:
    return PlanORM(
        id=_model_value(p, "id"),
        task_id=task_id,
        version=_model_value(p, "version"),
        goal=_model_value(p, "goal"),
        file_scope=_js(_model_value(p, "file_scope")),
        steps=_js(_model_value(p, "steps")),
        risk_level=_model_value(p, "risk_level"),
        risk_reason=_model_value(p, "risk_reason"),
        test_suggestions=_js(_model_value(p, "test_suggestions")),
        assumptions=_js(_model_value(p, "assumptions")),
        rollback_plan=_js(_model_value(p, "rollback_plan")),
        generated_from_analysis_revision=_model_value(p, "generated_from_analysis_revision"),
        generated_from_context_ids=_js(_model_value(p, "generated_from_context_ids")),
        analysis_risk_level=_model_value(p, "analysis_risk_level"),
        referenced_historical_tasks=_js(_model_value(p, "referenced_historical_tasks")),
        provider=_model_value(p, "provider"),
        status=_model_value(p, "status"),
        risk_adjust_reason=_model_value(p, "risk_adjust_reason"),
        risk_adjust_from=_model_value(p, "risk_adjust_from"),
        approved_by_id=_model_value(p, "approved_by_id"),
        approved_by_name=_model_value(p, "approved_by_name"),
        approved_at=_model_value(p, "approved_at"),
        approval_comment=_model_value(p, "approval_comment"),
        created_by=_model_value(p, "created_by"),
        created_by_name=_model_value(p, "created_by_name"),
        created_at=_model_value(p, "created_at"),
    )


def _take_diff(d: Any, task_id: str) -> DiffORM:
    return DiffORM(
        id=_model_value(d, "id"),
        task_id=task_id,
        plan_id=_model_value(d, "plan_id"),
        plan_version=_model_value(d, "plan_version"),
        execution_step=_model_value(d, "execution_step"),
        generation_status=_model_value(d, "generation_status"),
        execution_mode=_model_value(d, "execution_mode"),
        real_change_source=_model_value(d, "real_change_source"),
        is_demo_fallback=_model_value(d, "is_demo_fallback"),
        fallback_reason=_model_value(d, "fallback_reason"),
        real_failure_reason=_model_value(d, "real_failure_reason"),
        generated_at=_model_value(d, "generated_at"),
        files=_js(_model_value(d, "files")),
        test_summary=_model_value(d, "test_summary"),
        pr_description_draft=_model_value(d, "pr_description_draft"),
    )


def _take_test_run(t: Any, task_id: str) -> TestRunORM:
    return TestRunORM(
        id=_model_value(t, "id"),
        task_id=task_id,
        diff_artifact_id=_model_value(t, "diff_artifact_id"),
        plan_id=_model_value(t, "plan_id"),
        plan_version=_model_value(t, "plan_version"),
        command=_model_value(t, "command"),
        exit_code=_model_value(t, "exit_code"),
        stdout=_model_value(t, "stdout"),
        stderr=_model_value(t, "stderr"),
        status=_model_value(t, "status"),
        duration=_model_value(t, "duration"),
        attempt=_model_value(t, "attempt"),
        started_at=_model_value(t, "started_at"),
        finished_at=_model_value(t, "finished_at"),
        failure_reason=_model_value(t, "failure_reason"),
        blocked_reason=_model_value(t, "blocked_reason"),
        runner_mode=_model_value(t, "runner_mode"),
    )


def _take_pr_description(pr: Any, task_id: str) -> PrDescriptionORM:
    return PrDescriptionORM(
        id=_model_value(pr, "id"),
        task_id=task_id,
        plan_id=_model_value(pr, "plan_id"),
        plan_version=_model_value(pr, "plan_version"),
        diff_artifact_id=_model_value(pr, "diff_artifact_id"),
        test_run_id=_model_value(pr, "test_run_id"),
        version=_model_value(pr, "version"),
        background=_model_value(pr, "background"),
        change_summary=_model_value(pr, "change_summary"),
        impact_scope=_model_value(pr, "impact_scope"),
        test_result=_model_value(pr, "test_result"),
        risk_notes=_model_value(pr, "risk_notes"),
        rollback_notes=_model_value(pr, "rollback_notes"),
        generation_status=_model_value(pr, "generation_status"),
        failure_reason=_model_value(pr, "failure_reason"),
        generated_at=_model_value(pr, "generated_at"),
        generated_by=_model_value(pr, "generated_by"),
        generated_by_name=_model_value(pr, "generated_by_name"),
    )


# ============================================================================
# 读回：从 DB 恢复为纯数据 dict，供 bootstrap 交给 pydantic 重建 record
# ============================================================================


def _row_dict(row: Any, fields: list[str], *, convert_tz: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in fields:
        value = getattr(row, field)
        if convert_tz and isinstance(value, datetime) and value.tzinfo is None:
            # SQLite 的 DateTime 列读出是 naive；统一按存储口径视为 UTC，保证 pydantic 往返一致
            value = value.replace(tzinfo=timezone.utc)
        out[field] = value
    return out


def load_users() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(UserORM)).all()
        return [_row_dict(r, ["id", "name", "email", "role", "password_hash"]) for r in rows]


def load_repository_indexes() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(RepositoryIndexORM)).all()
        columns = [
            "tenant_id", "id", "repository_id", "project_id", "base_branch", "commit_sha", "status", "mode",
            "total_files", "processed_files", "chunk_count", "milvus_collection", "error_message",
            "error_stage", "requested_by", "requested_by_name", "retry_count", "requested_at",
            "created_at", "updated_at", "started_at", "finished_at", "active_key",
        ]
        return [_row_dict(r, columns) for r in rows]


def load_code_chunks() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(CodeChunkORM)).all()
        columns = [
            "tenant_id", "chunk_id", "repository_id", "index_id", "file_path", "chunk_index", "content",
            "language", "symbol_name", "start_line", "end_line", "commit_sha",
            "milvus_collection", "milvus_pk", "checksum",
        ]
        return [_row_dict(r, columns, convert_tz=False) for r in rows]


def load_retrieval_records() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(RetrievalRecordORM)).all()
        columns = [
            "id", "tenant_id", "task_id", "query_text", "repository_id", "project_id", "commit_sha",
            "index_id", "top_k", "mode", "fresh", "threshold", "empty_reason", "created_at",
        ]
        out = []
        for r in rows:
            item = _row_dict(r, columns)
            item["result_chunk_ids"] = getattr(r, "result_chunk_ids") or []
            out.append(item)
        return out


def load_password_resets() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(PasswordResetORM)).all()
        return [_row_dict(r, ["id", "email", "user_name", "role_label", "status", "requested_at", "handled_by", "handled_at"]) for r in rows]


def load_projects() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(ProjectORM)).all()
        return [_row_dict(r, ["id", "name", "tenant_id", "description", "default_test_command", "created_at", "updated_at"]) for r in rows]


def load_repositories() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(RepositoryORM)).all()
        out = []
        for r in rows:
            item = _row_dict(r, ["id", "tenant_id", "project_id", "name", "url", "default_branch", "token_alias", "token_configured", "token_updated_at", "test_command", "test_command_fallback", "auto_create_task_after_sync", "approval_owner_id", "approval_owner_name", "approval_mode", "risk_threshold", "max_files", "max_retries", "is_active", "archived_at", "superseded_by_id", "created_at", "updated_at"])
            for f in ["command_whitelist", "blocked_paths"]:
                item[f] = getattr(r, f)
            out.append(item)
        return out


def load_audit_events() -> list[dict[str, Any]]:
    if not shared_persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(AuditEventORM)).all()
        out = []
        for r in rows:
            item = _row_dict(r, ["id", "event_type", "tenant_id", "resource_type", "resource_id", "actor_id", "actor_name", "summary", "created_at"])
            item["diff_summary"] = getattr(r, "diff_summary")
            item["execution_step_ids"] = getattr(r, "execution_step_ids")
            out.append(item)
        return out


def delete_audit_events_before(cutoff: datetime, limit: int) -> int:
    """OPT-14 审计保留：删除 created_at < cutoff 的最早至多 limit 条事件，返回实际删除数。

    cutoff 统一转为 naive UTC 比较：SQLite/MySQL DATETIME 列按 UTC 挂钟存 naive，
    列值与限幅同样按最早优先，与内存侧 `prune_audit_events` 的候选排序一致。
    """
    if not shared_persistence_enabled() or limit <= 0:
        return 0
    aware = cutoff if cutoff.tzinfo is not None else cutoff.replace(tzinfo=timezone.utc)
    wall_clock = aware.astimezone(timezone.utc).replace(tzinfo=None)
    with session_scope() as session:
        ids = session.scalars(
            select(AuditEventORM.id)
            .where(AuditEventORM.created_at < wall_clock)
            .order_by(AuditEventORM.created_at.asc())
            .limit(limit)
        ).all()
        if not ids:
            return 0
        session.execute(delete(AuditEventORM).where(AuditEventORM.id.in_(list(ids))))
        return len(ids)


def load_comments() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(CommentORM)).all()
        out = []
        for r in rows:
            item = _row_dict(r, ["id", "task_id", "project_id", "repository_id", "author_id", "author_name", "body", "reference_type", "reference_id", "created_at", "deleted_at"])
            item["mentioned_user_ids"] = getattr(r, "mentioned_user_ids") or []
            out.append(item)
        return out


def load_notifications() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(NotificationORM)).all()
        return _row_dict_list(
            rows,
            ["id", "recipient_id", "task_id", "type", "title", "body", "actor_id", "actor_name", "priority", "read_at", "created_at"],
        )


def _row_dict_list(rows: Any, fields: list[str]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        item = _row_dict(r, fields)
        for f in fields:
            value = getattr(r, f)
            if isinstance(value, datetime) and hasattr(value, "tzinfo") and value.tzinfo is None:
                item[f] = value.replace(tzinfo=timezone.utc)
        out.append(item)
    return out


def load_team_requests() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(TeamRequestORM)).all()
        columns = [
            "id", "request_no", "tenant_id", "project_id", "project_name", "repository_id", "repository_name",
            "type", "title", "body", "reproduction_steps", "priority", "target_branch",
            "test_command", "sync_status", "content_sufficient", "failure_reason", "manual_reason",
            "gitee_issue_id", "gitee_issue_number", "gitee_issue_url", "task_id",
            "created_by_id", "created_by_name", "created_at", "updated_at",
        ]
        out = []
        for r in rows:
            item = _row_dict(r, columns)
            for f in ["acceptance_criteria", "labels", "validation_warnings"]:
                item[f] = getattr(r, f)
            out.append(item)
        return out


def load_tasks() -> list[dict[str, Any]]:
    """把 TaskORM + 子表重建为能直接喂给 main.TaskRecord.model_validate 的 dict。"""
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(TaskORM)).all()
        tasks = []
        for r in rows:
            item = _row_dict(r, _TASK_SCALAR_FIELDS)
            for f in _TASK_JSON_FIELDS:
                item[f] = getattr(r, f)
            item["plans"] = [_plan_dict(p) for p in session.scalars(select(PlanORM).where(PlanORM.task_id == r.id)).all()]
            item["diffs"] = [_diff_dict(d) for d in session.scalars(select(DiffORM).where(DiffORM.task_id == r.id)).all()]
            item["test_runs"] = [_test_run_dict(t) for t in session.scalars(select(TestRunORM).where(TestRunORM.task_id == r.id)).all()]
            item["pr_descriptions"] = [_pr_dict(p) for p in session.scalars(select(PrDescriptionORM).where(PrDescriptionORM.task_id == r.id)).all()]
            tasks.append(item)
        return tasks


def _plan_dict(p: PlanORM) -> dict[str, Any]:
    item = _row_dict(p, ["id", "task_id", "version", "goal", "risk_level", "risk_reason", "generated_from_analysis_revision", "analysis_risk_level", "provider", "status", "risk_adjust_reason", "risk_adjust_from", "approved_by_id", "approved_by_name", "approved_at", "approval_comment", "created_by", "created_by_name", "created_at"])
    for f in ["file_scope", "steps", "test_suggestions", "assumptions", "rollback_plan", "generated_from_context_ids", "referenced_historical_tasks"]:
        item[f] = getattr(p, f)
    return item


def _diff_dict(d: DiffORM) -> dict[str, Any]:
    item = _row_dict(d, ["id", "task_id", "plan_id", "plan_version", "execution_step", "generation_status", "execution_mode", "real_change_source", "is_demo_fallback", "fallback_reason", "real_failure_reason", "generated_at", "test_summary", "pr_description_draft"])
    item["files"] = getattr(d, "files")
    return item


def _test_run_dict(t: TestRunORM) -> dict[str, Any]:
    return _row_dict(t, ["id", "task_id", "diff_artifact_id", "plan_id", "plan_version", "command", "exit_code", "stdout", "stderr", "status", "duration", "attempt", "started_at", "finished_at", "failure_reason", "blocked_reason", "runner_mode"])


def _pr_dict(p: PrDescriptionORM) -> dict[str, Any]:
    return _row_dict(p, ["id", "task_id", "plan_id", "plan_version", "diff_artifact_id", "test_run_id", "version", "background", "change_summary", "impact_scope", "test_result", "risk_notes", "rollback_notes", "generation_status", "failure_reason", "generated_at", "generated_by", "generated_by_name"])


# 把「内存缓存中存在但 DB 缺失」的记录兜底写一次（首次建库种子）
def seed_store(store_name: str, items: list[Any]) -> int:
    upserters = {
        "users": upsert_user,
        "projects": upsert_project,
        "repositories": upsert_repository,
    }
    if store_name not in upserters or not persistence_enabled():
        return 0
    upsert = upserters[store_name]
    count = 0
    for item in items:
        upsert(item)
        count += 1
    return count


# ============================================================================
# 企业版 E1-01 多租户持久化：tenants / organizations / tenant_memberships
# （注：persistence_enabled 关闭即测试内存模式，读回为空，由 tenancy.store 内存真源承担）
# ============================================================================


def upsert_tenant(record: Any) -> None:
    if not persistence_enabled():
        return
    with session_scope() as session:
        row = session.get(TenantORM, _model_value(record, "id"))
        if row is None:
            row = TenantORM(id=_model_value(record, "id"))
            session.add(row)
        row.name = _model_value(record, "name")
        row.description = _model_value(record, "description")
        row.status = _model_value(record, "status")
        row.created_at = _model_value(record, "created_at")
        row.updated_at = _model_value(record, "updated_at")


def load_tenants() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(TenantORM)).all()
        return [_row_dict(r, ["id", "name", "description", "status", "created_at", "updated_at"]) for r in rows]


def upsert_organization(record: Any) -> None:
    if not persistence_enabled():
        return
    with session_scope() as session:
        row = session.get(OrganizationORM, _model_value(record, "id"))
        if row is None:
            row = OrganizationORM(id=_model_value(record, "id"))
            session.add(row)
        row.tenant_id = _model_value(record, "tenant_id")
        row.name = _model_value(record, "name")
        row.description = _model_value(record, "description")
        row.created_at = _model_value(record, "created_at")
        row.updated_at = _model_value(record, "updated_at")


def load_organizations() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(OrganizationORM)).all()
        return [_row_dict(r, ["id", "tenant_id", "name", "description", "created_at", "updated_at"]) for r in rows]


def upsert_membership(record: Any) -> None:
    """租户成员关系写透（含 is_active 激活位，服务端状态的可信源）。"""
    if not persistence_enabled():
        return
    with session_scope() as session:
        row = session.get(TenantMembershipORM, _model_value(record, "id"))
        if row is None:
            row = TenantMembershipORM(
                id=_model_value(record, "id"),
                tenant_id=_model_value(record, "tenant_id"),
                user_id=_model_value(record, "user_id"),
                created_at=_model_value(record, "created_at"),
            )
            session.add(row)
        row.user_name = _model_value(record, "user_name")
        row.organization_id = _model_value(record, "organization_id")
        row.role = _model_value(record, "role")
        row.status = _model_value(record, "status")
        row.is_active = bool(_model_value(record, "is_active"))
        row.updated_at = _model_value(record, "updated_at")


def load_memberships() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(TenantMembershipORM)).all()
        out = []
        for r in rows:
            item = _row_dict(r, [
                "id", "tenant_id", "user_id", "user_name", "organization_id",
                "role", "status", "is_active", "created_at", "updated_at",
            ])
            out.append(item)
        return out


# ============================================================================
# 企业版 E2-01 审批申请持久化
# ============================================================================


def _approval_row_to_dict(row: ApprovalApplicationORM) -> dict[str, Any]:
    item = _row_dict(row, [
        "id", "tenant_id", "actor_id", "actor_name", "resource_type", "resource_id",
        "operation", "title", "risk_level", "decision", "approval_status", "reason",
        "idempotency_key", "created_by", "created_at", "updated_at", "expires_at",
        "approved_by_id", "approved_by_name", "approved_at", "executed_at",
        "executed_by_id", "resource_version", "approval_mode", "secondary_confirmation",
        "version", "execution_attempt_id", "async_result_id", "retry_count",
        "started_at", "finished_at", "execution_error",
    ])
    item["approval_meta"] = getattr(row, "approval_meta") or {}
    for field in ("request_summary", "executor_result", "allowed_approvers"):
        item[field] = getattr(row, field) or ({} if field != "allowed_approvers" else [])
    return item


def upsert_approval(record: Any) -> dict[str, Any] | None:
    """写透审批申请。

    返回 None 表示正常写入；若多副本并发插入撞上 (tenant_id, idempotency_key) 唯一键，
    返回数据库中已存在的审批记录，供服务层转换为幂等成功。
    """
    if not persistence_enabled():
        return None
    tenant_id = _model_value(record, "tenant_id")
    idempotency_key = _model_value(record, "idempotency_key")
    try:
        with session_scope() as session:
            row = session.get(ApprovalApplicationORM, _model_value(record, "id"))
            if row is None:
                row = ApprovalApplicationORM(
                    id=_model_value(record, "id"),
                    tenant_id=tenant_id,
                    actor_id=_model_value(record, "actor_id"),
                    created_at=_model_value(record, "created_at"),
                )
                session.add(row)
            for field in [
                "tenant_id", "actor_id", "actor_name", "resource_type", "resource_id",
                "operation", "title", "request_summary", "risk_level", "decision",
                "approval_status", "reason", "idempotency_key", "created_by", "updated_at",
                "expires_at", "approved_by_id", "approved_by_name", "approved_at",
                "executed_at", "executed_by_id", "executor_result", "resource_version",
                "allowed_approvers", "approval_mode", "approval_meta", "secondary_confirmation", "version",
                "execution_attempt_id", "async_result_id", "retry_count",
                "started_at", "finished_at", "execution_error",
            ]:
                setattr(row, field, _js(_model_value(record, field)))
    except IntegrityError:
        with session_scope() as session:
            existing = session.scalars(
                select(ApprovalApplicationORM).where(
                    ApprovalApplicationORM.tenant_id == tenant_id,
                    ApprovalApplicationORM.idempotency_key == idempotency_key,
                )
            ).first()
            if existing is not None:
                return _approval_row_to_dict(existing)
        raise
    return None


def load_approval(approval_id: str) -> dict[str, Any] | None:
    if not persistence_enabled():
        return None
    with session_scope() as session:
        row = session.get(ApprovalApplicationORM, approval_id)
        return _approval_row_to_dict(row) if row is not None else None


def find_approval_by_idempotency(
    tenant_id: str,
    idempotency_key: str,
) -> dict[str, Any] | None:
    if not persistence_enabled():
        return None
    with session_scope() as session:
        row = session.scalars(
            select(ApprovalApplicationORM).where(
                ApprovalApplicationORM.tenant_id == tenant_id,
                ApprovalApplicationORM.idempotency_key == idempotency_key,
            )
        ).first()
        return _approval_row_to_dict(row) if row is not None else None


def load_approvals() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(ApprovalApplicationORM)).all()
        return [_approval_row_to_dict(row) for row in rows]


def claim_approval_execution(
    approval_id: str,
    expected_version: int,
    attempt_id: str,
    started_at: Any,
) -> bool:
    """多副本抢占执行权：条件 UPDATE（仅源态 + 期望版本命中时置 executing）。

    只有返回 True 的实例允许触发业务副作用；其余实例读回状态后拒绝（409）。
    返回 False 表示抢占失败（已被其他副本抢占 / 已执行 / 版本变化）。
    """
    if not persistence_enabled():
        return False  # 内存模式由 store.claim_execution 自行原子化
    with session_scope() as session:
        result = session.execute(
            update(ApprovalApplicationORM)
            .where(
                ApprovalApplicationORM.id == approval_id,
                ApprovalApplicationORM.approval_status.in_(["approved", "execution_failed"]),
                ApprovalApplicationORM.version == expected_version,
            )
            .values(
                approval_status="executing",
                version=expected_version + 1,
                execution_attempt_id=attempt_id,
                started_at=started_at,
                updated_at=started_at,
            )
        )
        return bool(result.rowcount)


def settle_approval_execution(
    approval_id: str,
    attempt_id: str,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    finished_at: Any,
) -> bool:
    """Worker 执行结果回写：仅当「executing 且 attempt_id 匹配」时生效，保证幂等。

    返回 False 表示该 attempt 已被回写或已过期（重复回写不产生二次副作用）。
    """
    if not persistence_enabled():
        return False  # 内存模式由 store.settle_execution 自行原子化
    values: dict[str, Any] = {
        "approval_status": status,
        "finished_at": finished_at,
        "updated_at": finished_at,
        "version": ApprovalApplicationORM.version + 1,
    }
    if status == "executed":
        values["executor_result"] = result
        values["executed_at"] = finished_at
        values["execution_error"] = None
    else:
        values["execution_error"] = error
        values["executor_result"] = result
    with session_scope() as session:
        result_update = session.execute(
            update(ApprovalApplicationORM)
            .where(
                ApprovalApplicationORM.id == approval_id,
                ApprovalApplicationORM.approval_status == "executing",
                ApprovalApplicationORM.execution_attempt_id == attempt_id,
            )
            .values(**values)
        )
        return bool(result_update.rowcount)


# ============================================================================
# E4-01 Multi-Agent Runtime 持久化（enterprise_runtime_tasks / _checkpoints）
# ============================================================================

def _runtime_task_to_data(task: Any) -> dict:
    return {
        "id": task.id,
        "tenant_id": task.tenant_id,
        "project_id": getattr(task, "project_id", "") or "",
        "repository_id": getattr(task, "repository_id", "") or "",
        "title": getattr(task, "title", "") or "",
        "status": task.status.value if hasattr(task.status, "value") else str(task.status),
        "version": int(getattr(task, "version", 1)),
        "workflow_version": int(getattr(task, "workflow_version", 1)),
        "checkpoint_version": int(getattr(task, "checkpoint_version", 0)),
        "current_step": getattr(task, "current_step", None),
        "completed_steps": list(getattr(task, "completed_steps", []) or []),
        "retry_count": int(getattr(task, "retry_count", 0)),
        "approval_state": getattr(task, "approval_state", "not_required") or "not_required",
        "execution_state": getattr(task, "execution_state", "idle") or "idle",
        "idempotency_key": getattr(task, "idempotency_key", "") or "",
        "wait_human_reason": getattr(task, "wait_human_reason", None),
        "takeover": getattr(task, "takeover", None),
        "lease_worker": getattr(task, "lease_worker", None),
        "lease_version": int(getattr(task, "lease_version", 0)),
        "repair_round": int(getattr(task, "repair_round", 0) or 0),
        "max_repair_rounds": int(getattr(task, "max_repair_rounds", 2) or 2),
        "steps": [_rt_steps_js(getattr(step, "model_dump", lambda: None)() if hasattr(step, "model_dump") else step)
                  for step in (getattr(task, "steps", []) or [])],
        "created_at": getattr(task, "created_at", datetime.now(timezone.utc)),
        "updated_at": getattr(task, "updated_at", datetime.now(timezone.utc)),
    }


def upsert_runtime_task(task: Any) -> None:
    if not persistence_enabled():
        return
    data = _runtime_task_to_data(task)
    with session_scope() as session:
        row = session.get(EnterpriseRuntimeTaskORM, task.id)
        if row is None:
            session.add(EnterpriseRuntimeTaskORM(**data))
        else:
            for key, value in data.items():
                setattr(row, key, value)
        session.commit()


def load_runtime_task(task_id: str) -> dict[str, Any] | None:
    if not persistence_enabled():
        return None
    with session_scope() as session:
        row = session.get(EnterpriseRuntimeTaskORM, task_id)
        if row is None:
            return None
        return {c.name: getattr(row, c.name) for c in EnterpriseRuntimeTaskORM.__table__.columns}


def load_runtime_tasks() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(EnterpriseRuntimeTaskORM)).all()
        return [{c.name: getattr(r, c.name) for c in EnterpriseRuntimeTaskORM.__table__.columns} for r in rows]


def claim_runtime_step(task_id: str, expected_version: int, worker_id: str,
                       lease_ttl_seconds: float = 300.0) -> bool:
    """跨进程原子抢占：仅版本命中且租约空闲/过期/本人持有时更新租约与版本。

    返回 True 表示本实例抢到执行权（可触发副作用）；False 表示已被其他副本抢占/版本变化。
    """
    if not persistence_enabled():
        return False  # 内存模式由 store.claim_step 自行原子化
    from sqlalchemy import or_
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=max(1.0, lease_ttl_seconds))
    with session_scope() as session:
        result = session.execute(
            update(EnterpriseRuntimeTaskORM)
            .where(
                EnterpriseRuntimeTaskORM.id == task_id,
                EnterpriseRuntimeTaskORM.version == expected_version,
                or_(
                    EnterpriseRuntimeTaskORM.lease_worker.is_(None),
                    EnterpriseRuntimeTaskORM.lease_worker == worker_id,
                    EnterpriseRuntimeTaskORM.lease_expires_at.is_(None),
                    EnterpriseRuntimeTaskORM.lease_expires_at < now,
                ),
            )
            .values(
                lease_worker=worker_id,
                lease_version=EnterpriseRuntimeTaskORM.lease_version + 1,
                lease_expires_at=expires,
                version=expected_version + 1,
                updated_at=now,
            )
        )
        return bool(result.rowcount)


def _as_aware_utc(value: datetime) -> datetime:
    """SQLite 等后端读回的时间戳可能无时区信息，统一按 UTC 解释后再比较。"""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def reclaim_stale_runtime_steps(task_id: str, stale_before: datetime | None = None) -> int:
    """崩溃回收（OPT-13）：租约已过期/为空且存在 RUNNING 步骤时，把 RUNNING 步骤重置 PENDING。

    条件 UPDATE（version CAS + 租约过期条件）保证跨进程原子：并发回收只有一个生效
    （rowcount=1），另一个得到 0。返回生效行数（0/1）。
    """
    if not persistence_enabled():
        return 0
    moment = stale_before or datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.get(EnterpriseRuntimeTaskORM, task_id)
        if row is None:
            return 0
        if row.lease_expires_at is not None and _as_aware_utc(row.lease_expires_at) > moment:
            return 0  # 租约未过期：存在活跃执行者，不回收
        new_steps: list[Any] = []
        reclaimed = 0
        for item in list(row.steps or []):
            if isinstance(item, dict) and str(item.get("status")) == "running":
                # agent_attempt 同步前进：崩溃的尝试计入编号，重做拿到新幂等键（OPT-13 F3）
                item = {**item, "status": "pending",
                        "agent_attempt": int(item.get("agent_attempt") or 0) + 1}
                reclaimed += 1
            new_steps.append(item)
        if not reclaimed:
            return 0
        result = session.execute(
            update(EnterpriseRuntimeTaskORM)
            .where(
                EnterpriseRuntimeTaskORM.id == task_id,
                EnterpriseRuntimeTaskORM.version == row.version,
            )
            .values(
                steps=new_steps,
                lease_worker=None,
                lease_expires_at=None,
                version=row.version + 1,
                updated_at=moment,
            )
        )
        return int(result.rowcount)


def upsert_runtime_checkpoint(cp: Any) -> None:
    if not persistence_enabled():
        return
    data = {
        "id": f"cp_{cp.task_id}_{cp.checkpoint_version}",
        "task_id": cp.task_id,
        "tenant_id": cp.tenant_id,
        "step_id": cp.step_id,
        "workflow_version": int(cp.workflow_version),
        "checkpoint_version": int(cp.checkpoint_version),
        "completed_steps": list(cp.completed_steps or []),
        "current_step": cp.current_step,
        "retry_count": int(cp.retry_count),
        "approval_state": cp.approval_state or "not_required",
        "execution_state": cp.execution_state or "idle",
        "idempotency_keys": list(cp.idempotency_keys or []),
        "output_summary": cp.output_summary or "",
        "worker_id": cp.worker_id,
        "lease_version": int(cp.lease_version),
        "created_at": cp.created_at,
        "updated_at": cp.updated_at,
    }
    with session_scope() as session:
        row = session.get(EnterpriseRuntimeCheckpointORM, data["id"])
        if row is None:
            session.add(EnterpriseRuntimeCheckpointORM(**data))
        else:
            for key, value in data.items():
                setattr(row, key, value)
        session.commit()


def load_runtime_checkpoints() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.scalars(select(EnterpriseRuntimeCheckpointORM)).all()
        return [{c.name: getattr(r, c.name) for c in EnterpriseRuntimeCheckpointORM.__table__.columns} for r in rows]


def release_runtime_lease(task_id: str, worker_id: str) -> None:
    """释放 DB 租约（步骤完成后由持有者清除，允许下一 worker 抢占下一步）。"""
    if not persistence_enabled():
        return
    with session_scope() as session:
        session.execute(
            update(EnterpriseRuntimeTaskORM)
            .where(
                EnterpriseRuntimeTaskORM.id == task_id,
                EnterpriseRuntimeTaskORM.lease_worker == worker_id,
            )
            .values(lease_worker=None, lease_expires_at=None,
                    updated_at=datetime.now(timezone.utc))
        )


# ============================================================================
# E4-02 多 Agent 协作：(tenant, idempotency_key) 唯一键幂等 + version 布局兼容
# ============================================================================

def _agent_run_to_data(run: Any) -> dict:
    from app.models.orm import AgentRunORM
    return {
        "id": run.run_id,
        "run_id": run.run_id,
        "task_id": run.task_id,
        "tenant_id": run.tenant_id,
        "agent_type": run.agent_type,
        "step_id": run.step_id,
        "attempt": int(getattr(run, "attempt", 1)),
        "repair_round": int(getattr(run, "repair_round", 0) or 0),
        "status": run.status,
        "prompt_version": getattr(run, "prompt_version", "1") or "1",
        "input_refs": list(getattr(run, "input_refs", []) or []),
        "output_refs": list(getattr(run, "output_refs", []) or []),
        "policy_context": getattr(run, "policy_context", None),
        "model": getattr(run, "model", "") or "",
        "usage": getattr(run, "usage", None),
        "error_reason": getattr(run, "error_reason", None),
        "idempotency_key": getattr(run, "idempotency_key", "") or "",
        "started_at": getattr(run, "started_at", datetime.now(timezone.utc)),
        "finished_at": getattr(run, "finished_at", None),
    }


def upsert_agent_run(run: Any) -> None:
    if not persistence_enabled():
        return
    from app.models.orm import AgentRunORM
    data = _agent_run_to_data(run)
    with session_scope() as session:
        existing = session.get(AgentRunORM, run.run_id)
        if existing is None:
            session.add(AgentRunORM(**data))
        else:
            for key, value in data.items():
                setattr(existing, key, value)
        session.commit()


def load_agent_runs(task_id: str) -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    from app.models.orm import AgentRunORM
    with session_scope() as session:
        rows = session.scalars(select(AgentRunORM).where(AgentRunORM.task_id == task_id)).all()
        return [{c.name: getattr(r, c.name) for c in AgentRunORM.__table__.columns} for r in rows]


def mark_running_agent_runs_failed(task_id: str, reason: str, *, step_id: str | None = None) -> int:
    """崩溃回收兜底（OPT-13 F3）：把任务下悬挂的 running AgentRun 批量落终态 failed。

    DB 侧不受进程内存恢复范围限制：即使新 worker 未恢复 runs 缓存（或悬挂 run 由
    其他进程写入），也能按 task_id 全量清扫。返回更新行数。
    """
    if not persistence_enabled():
        return 0
    from app.models.orm import AgentRunORM

    now = datetime.now(timezone.utc)
    with session_scope() as session:
        stmt = (
            update(AgentRunORM)
            .where(AgentRunORM.task_id == task_id, AgentRunORM.status == "running")
        )
        if step_id:
            stmt = stmt.where(AgentRunORM.step_id == step_id)
        result = session.execute(stmt.values(status="failed", error_reason=reason, finished_at=now))
        return int(result.rowcount or 0)


def load_all_agent_runs() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    from app.models.orm import AgentRunORM
    with session_scope() as session:
        rows = session.scalars(select(AgentRunORM)).all()
        return [{c.name: getattr(r, c.name) for c in AgentRunORM.__table__.columns} for r in rows]


def _agent_artifact_to_data(artifact: Any) -> dict:
    return {
        "id": artifact.artifact_id,
        "artifact_id": artifact.artifact_id,
        "task_id": artifact.task_id,
        "run_id": artifact.run_id,
        "tenant_id": artifact.tenant_id,
        "agent_type": artifact.agent_type,
        "artifact_type": artifact.artifact_type.value if hasattr(artifact.artifact_type, "value") else str(artifact.artifact_type),
        "version": int(getattr(artifact, "version", 1)),
        "schema_version": int(getattr(artifact, "schema_version", 1)),
        "status": getattr(artifact, "status", "active") or "active",
        "input_refs": list(getattr(artifact, "input_refs", []) or []),
        "output_refs": list(getattr(artifact, "output_refs", []) or []),
        "content": getattr(artifact, "content", None),
        "content_ref": getattr(artifact, "content_ref", None),
        "content_hash": getattr(artifact, "content_hash", "") or "",
        "idempotency_key": getattr(artifact, "idempotency_key", "") or "",
        "created_at": getattr(artifact, "created_at", datetime.now(timezone.utc)),
        "updated_at": getattr(artifact, "created_at", datetime.now(timezone.utc)),
    }


def upsert_agent_artifact(artifact: Any) -> None:
    if not persistence_enabled():
        return
    from app.models.orm import AgentArtifactORM
    data = _agent_artifact_to_data(artifact)
    with session_scope() as session:
        existing = session.get(AgentArtifactORM, artifact.artifact_id)
        if existing is None:
            session.add(AgentArtifactORM(**data))
        else:
            for key, value in data.items():
                setattr(existing, key, value)
        session.commit()


def load_agent_artifacts(task_id: str) -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    from app.models.orm import AgentArtifactORM
    with session_scope() as session:
        rows = session.scalars(select(AgentArtifactORM).where(AgentArtifactORM.task_id == task_id)).all()
        return [{c.name: getattr(r, c.name) for c in AgentArtifactORM.__table__.columns} for r in rows]


def load_all_agent_artifacts() -> list[dict[str, Any]]:
    if not persistence_enabled():
        return []
    from app.models.orm import AgentArtifactORM
    with session_scope() as session:
        rows = session.scalars(select(AgentArtifactORM)).all()
        return [{c.name: getattr(r, c.name) for c in AgentArtifactORM.__table__.columns} for r in rows]
