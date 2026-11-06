"""MVP 业务 / 检索 / 审计 ORM 模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TenantORM(Base):
    """企业版租户（E1-01）：业务资源归属的顶层边界。"""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OrganizationORM(Base):
    """企业版组织（E1-01）：归属唯一租户。"""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TenantMembershipORM(Base):
    """企业版租户成员关系（E1-01）：用户-租户-企业角色-生命周期/激活位。"""

    __tablename__ = "tenant_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    user_name: Mapped[str] = mapped_column(String(120), default="")
    organization_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    role: Mapped[str] = mapped_column(String(40), default="developer")
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RepositoryIndexORM(Base):
    __tablename__ = "repository_indexes"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(80), index=True, default="tenant_default")
    repository_id: Mapped[str] = mapped_column(String(80), index=True)
    project_id: Mapped[str] = mapped_column(String(80), index=True)
    base_branch: Mapped[str] = mapped_column(String(80))
    commit_sha: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    mode: Mapped[str] = mapped_column(String(20), default="full")
    total_files: Mapped[int] = mapped_column(Integer, default=0)
    processed_files: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    milvus_collection: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(80))
    requested_by_name: Mapped[str] = mapped_column(String(80))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 同一仓库同一 Commit 的运行中索引只能有一条；终态记录为 NULL。
    active_key: Mapped[str | None] = mapped_column(String(180), nullable=True, unique=True)


class CodeChunkORM(Base):
    __tablename__ = "code_chunks"

    chunk_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(80), index=True, default="tenant_default")
    repository_id: Mapped[str] = mapped_column(String(80), index=True)
    index_id: Mapped[str] = mapped_column(String(80), index=True)
    file_path: Mapped[str] = mapped_column(String(500), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(40))
    symbol_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    commit_sha: Mapped[str] = mapped_column(String(80), index=True)
    milvus_collection: Mapped[str] = mapped_column(String(120))
    milvus_pk: Mapped[str] = mapped_column(String(120))
    checksum: Mapped[str] = mapped_column(String(40))


class IndexSnapshotORM(Base):
    """索引 SYNCING 阶段的仓库代码快照（CodeFile 列表），保证 PARSING 中断重启后可恢复。"""

    __tablename__ = "index_snapshots"

    index_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    repository_id: Mapped[str] = mapped_column(String(80), index=True)
    commit_sha: Mapped[str] = mapped_column(String(80))
    files: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RetrievalRecordORM(Base):
    __tablename__ = "retrieval_records"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(80), index=True)
    query_text: Mapped[str] = mapped_column(Text)
    tenant_id: Mapped[str | None] = mapped_column(String(80), index=True, default="tenant_default")
    repository_id: Mapped[str] = mapped_column(String(80), index=True)
    project_id: Mapped[str] = mapped_column(String(80), index=True)
    commit_sha: Mapped[str | None] = mapped_column(String(80), nullable=True)
    index_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    top_k: Mapped[int] = mapped_column(Integer, default=0)
    mode: Mapped[str] = mapped_column(String(20))
    fresh: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    threshold: Mapped[float] = mapped_column(Float, default=0.0)
    empty_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_chunk_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TaskEventORM(Base):
    __tablename__ = "task_events"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(80), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    summary: Mapped[str] = mapped_column(Text)
    diff_summary: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ExecutionStepORM(Base):
    __tablename__ = "execution_steps"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(80), index=True)
    event_id: Mapped[str] = mapped_column(String(120), index=True)
    node: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))
    input: Mapped[str | None] = mapped_column(Text, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AgentCheckpointORM(Base):
    __tablename__ = "agent_checkpoints"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(80), index=True)
    graph_name: Mapped[str] = mapped_column(String(80), default="task_workflow")
    node_name: Mapped[str] = mapped_column(String(80), default="unknown")
    status: Mapped[str] = mapped_column(String(40), default="running")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ============================================================================
# 以下为 P0 业务数据持久化：用户/项目/仓库/任务(计划/审批/Diff/测试/PR描述)/
# 团队请求/审计。rows 是这些 dict 的唯一真源，模块级 dict 只是启动缓存 + 写透。
# ============================================================================


class UserORM(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(160))


class PasswordResetORM(Base):
    __tablename__ = "password_resets"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    email: Mapped[str] = mapped_column(String(200), index=True)
    user_name: Mapped[str] = mapped_column(String(120))
    role_label: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    handled_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectORM(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(80), index=True, default="tenant_default")
    description: Mapped[str] = mapped_column(Text, default="")
    default_test_command: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RepositoryORM(Base):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(80), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(80), index=True, default="tenant_default")
    name: Mapped[str] = mapped_column(String(160))
    url: Mapped[str] = mapped_column(String(500))
    default_branch: Mapped[str] = mapped_column(String(120))
    token_alias: Mapped[str | None] = mapped_column(String(120), nullable=True)
    token_configured: Mapped[bool] = mapped_column(Boolean, default=False)
    token_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    test_command: Mapped[str] = mapped_column(String(400))
    command_whitelist: Mapped[list[str]] = mapped_column(JSON, default=list)
    test_command_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_create_task_after_sync: Mapped[bool] = mapped_column(Boolean, default=False)
    approval_owner_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approval_owner_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approval_mode: Mapped[str] = mapped_column(String(20))
    risk_threshold: Mapped[str] = mapped_column(String(20))
    max_files: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=0)
    blocked_paths: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)


class AuditEventORM(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(80), index=True, default="tenant_default")
    resource_type: Mapped[str] = mapped_column(String(80), index=True)
    resource_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(80))
    actor_name: Mapped[str] = mapped_column(String(120))
    summary: Mapped[str] = mapped_column(Text)
    diff_summary: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    execution_step_ids: Mapped[list[str]] = mapped_column(JSON, default=list)


class TeamRequestORM(Base):
    __tablename__ = "team_requests"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_no: Mapped[str] = mapped_column(String(40), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(80), index=True, default="tenant_default")
    project_id: Mapped[str] = mapped_column(String(80), index=True)
    project_name: Mapped[str] = mapped_column(String(160))
    repository_id: Mapped[str] = mapped_column(String(80), index=True)
    repository_name: Mapped[str] = mapped_column(String(160))
    type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(400))
    body: Mapped[str] = mapped_column(Text)
    reproduction_steps: Mapped[str] = mapped_column(Text, default="")
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSON, default=list)
    priority: Mapped[str | None] = mapped_column(String(20), nullable=True)
    labels: Mapped[list[str]] = mapped_column(JSON, default=list)
    target_branch: Mapped[str | None] = mapped_column(String(120), nullable=True)
    test_command: Mapped[str | None] = mapped_column(String(400), nullable=True)
    sync_status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    content_sufficient: Mapped[bool] = mapped_column(Boolean, default=True)
    validation_warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    manual_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    gitee_issue_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    gitee_issue_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    gitee_issue_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_by_id: Mapped[str] = mapped_column(String(80))
    created_by_name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TaskORM(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    tenant_id: Mapped[str | None] = mapped_column(String(80), index=True, default="tenant_default")
    project_id: Mapped[str] = mapped_column(String(80), index=True)
    project_name: Mapped[str] = mapped_column(String(160))
    repository_id: Mapped[str] = mapped_column(String(80), index=True)
    repository_name: Mapped[str] = mapped_column(String(160))
    repository_url: Mapped[str] = mapped_column(String(500))
    issue_id: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(400))
    status: Mapped[str] = mapped_column(String(30), index=True)
    risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    risk_source: Mapped[str] = mapped_column(String(40), default="pending_ai_analysis")
    base_branch: Mapped[str] = mapped_column(String(120))
    test_command: Mapped[str] = mapped_column(String(400))
    approval_owner_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approval_owner_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    work_branch: Mapped[str] = mapped_column(String(120))
    # 深层嵌套内容以 JSON 落库，保证 pydantic 精确往返（见 db/bootstrap.py）
    issue_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    automation_state: Mapped[str] = mapped_column(String(20), default="running")
    manual_required: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_reason: Mapped[str] = mapped_column(String(40), default="none")
    next_action: Mapped[str] = mapped_column(String(400), default="")
    failure_node: Mapped[str | None] = mapped_column(String(80), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    base_commit_sha: Mapped[str | None] = mapped_column(String(80), nullable=True)
    team_request_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    team_request_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    gitee_issue_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    related_index_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pr_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pr_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    code_contexts: Mapped[list] = mapped_column(JSON, default=list)
    similar_task_refs: Mapped[list] = mapped_column(JSON, default=list)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 任务关联的审计事件与执行步骤同样以 JSON 保留（保证 GET 任务重启后逐条一致），
    # 全局审计查询另由 audit_events 表承担。
    events: Mapped[list] = mapped_column(JSON, default=list)
    execution_steps: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(80))
    created_by_name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlanORM(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    goal: Mapped[str] = mapped_column(Text)
    file_scope: Mapped[list] = mapped_column(JSON, default=list)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    risk_level: Mapped[str] = mapped_column(String(20))
    risk_reason: Mapped[str] = mapped_column(Text)
    test_suggestions: Mapped[list[str]] = mapped_column(JSON, default=list)
    assumptions: Mapped[list[str]] = mapped_column(JSON, default=list)
    rollback_plan: Mapped[list[str]] = mapped_column(JSON, default=list)
    generated_from_analysis_revision: Mapped[int] = mapped_column(Integer, default=1)
    generated_from_context_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    analysis_risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    referenced_historical_tasks: Mapped[list] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), index=True)
    risk_adjust_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_adjust_from: Mapped[str | None] = mapped_column(String(20), nullable=True)
    approved_by_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approved_by_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(80))
    created_by_name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DiffORM(Base):
    __tablename__ = "diffs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(80), index=True)
    plan_id: Mapped[str] = mapped_column(String(80))
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    execution_step: Mapped[str] = mapped_column(String(120))
    generation_status: Mapped[str] = mapped_column(String(20))
    execution_mode: Mapped[str] = mapped_column(String(20))
    real_change_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_demo_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    real_failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    files: Mapped[list] = mapped_column(JSON, default=list)
    test_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    pr_description_draft: Mapped[str | None] = mapped_column(Text, nullable=True)


class TestRunORM(Base):
    __tablename__ = "test_runs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(80), index=True)
    diff_artifact_id: Mapped[str] = mapped_column(String(80))
    plan_id: Mapped[str] = mapped_column(String(80))
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    command: Mapped[str] = mapped_column(String(400))
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout: Mapped[str] = mapped_column(Text, default="")
    stderr: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20))
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    runner_mode: Mapped[str] = mapped_column(String(30))


class PrDescriptionORM(Base):
    __tablename__ = "pr_descriptions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(80), index=True)
    plan_id: Mapped[str] = mapped_column(String(80))
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    diff_artifact_id: Mapped[str] = mapped_column(String(80))
    test_run_id: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer, default=1)
    background: Mapped[str] = mapped_column(Text, default="")
    change_summary: Mapped[str] = mapped_column(Text, default="")
    impact_scope: Mapped[str] = mapped_column(Text, default="")
    test_result: Mapped[str] = mapped_column(Text, default="")
    risk_notes: Mapped[str] = mapped_column(Text, default="")
    rollback_notes: Mapped[str] = mapped_column(Text, default="")
    generation_status: Mapped[str] = mapped_column(String(20))
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    generated_by: Mapped[str] = mapped_column(String(80))
    generated_by_name: Mapped[str] = mapped_column(String(120))


class CommentORM(Base):
    """DEV-019 任务评论：绑定任务 + 关联对象；软删除保留历史回放痕迹。"""

    __tablename__ = "comments"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(80), index=True)
    project_id: Mapped[str] = mapped_column(String(80), index=True)
    repository_id: Mapped[str] = mapped_column(String(80), index=True)
    author_id: Mapped[str] = mapped_column(String(80))
    author_name: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    mentioned_user_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    reference_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationORM(Base):
    """DEV-019 站内通知（按收件人检索，read_at 标记已读；SSE 只推送变化信号）。"""

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    recipient_id: Mapped[str] = mapped_column(String(80), index=True)
    task_id: Mapped[str] = mapped_column(String(80), index=True)
    type: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str] = mapped_column(String(80))
    actor_name: Mapped[str] = mapped_column(String(120))
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class HistoricalTaskSummaryORM(Base):
    """历史任务复用摘要：完成任务的知识资产（真实数据来源，缺失字段「暂无记录」）。"""

    __tablename__ = "historical_task_summaries"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(80), index=True)
    project_id: Mapped[str] = mapped_column(String(80), index=True)
    repository_id: Mapped[str] = mapped_column(String(80), nullable=True)
    source_task_title: Mapped[str] = mapped_column(Text, default="")
    issue_summary: Mapped[str] = mapped_column(Text, default="")
    context_summary: Mapped[str] = mapped_column(Text, default="")
    analysis_summary: Mapped[str] = mapped_column(Text, default="")
    plan_summary: Mapped[str] = mapped_column(Text, default="")
    diff_summary: Mapped[str] = mapped_column(Text, default="")
    test_summary: Mapped[str] = mapped_column(Text, default="")
    pr_summary: Mapped[str] = mapped_column(Text, default="")
    replay_summary: Mapped[str] = mapped_column(Text, default="")
    searchable_text: Mapped[str] = mapped_column(Text, default="")
    summary_version: Mapped[int] = mapped_column(Integer, default=1)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HistoricalTaskIndexORM(Base):
    """历史任务摘要的向量索引记录（幂等；索引失败记录 failure_reason 不阻断主链）。"""

    __tablename__ = "historical_task_indexes"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    summary_id: Mapped[str] = mapped_column(String(80), index=True)
    task_id: Mapped[str] = mapped_column(String(80), index=True)
    project_id: Mapped[str] = mapped_column(String(80), index=True)
    embedding_provider: Mapped[str] = mapped_column(String(40), default="")
    embedding_model: Mapped[str] = mapped_column(String(80), default="")
    vector_document_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    index_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApprovalApplicationORM(Base):
    """企业版 E2-01 治理审批申请（租户隔离、幂等、并发、过期、执行一次）。

    所有高风险业务操作由本实体承载审批与执行生命周期；status 转换由 ApproveService
    严格按状态机控制，非法转换不落库。
    """

    __tablename__ = "approval_applications"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_approval_tenant_idem"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_name: Mapped[str] = mapped_column(String(120))
    resource_type: Mapped[str] = mapped_column(String(80), index=True)
    resource_id: Mapped[str] = mapped_column(String(80), index=True)
    operation: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(200))
    request_summary: Mapped[dict | None] = mapped_column(JSON, default=dict)
    risk_level: Mapped[str] = mapped_column(String(4))
    decision: Mapped[str] = mapped_column(String(20))
    approval_status: Mapped[str] = mapped_column(String(20), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[str] = mapped_column(String(120))
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approved_by_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_by_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    executor_result: Mapped[dict | None] = mapped_column(JSON, default=dict)
    resource_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    allowed_approvers: Mapped[list[str]] = mapped_column(JSON, default=list)
    approval_mode: Mapped[str] = mapped_column(String(20), default="single")
    approval_meta: Mapped[dict | None] = mapped_column(JSON, default=dict)
    secondary_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    # E2-01 多副本执行追踪（worker 回写 / 重试 / 审计追踪）
    execution_attempt_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    async_result_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class EnterpriseRuntimeTaskORM(Base):
    """企业版 E4-01 Multi-Agent Runtime 任务（租户隔离 + 版本化 + 幂等）。"""

    __tablename__ = "enterprise_runtime_tasks"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_runtime_tenant_idem"),)

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    project_id: Mapped[str] = mapped_column(String(120), default="")
    repository_id: Mapped[str] = mapped_column(String(120), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    workflow_version: Mapped[int] = mapped_column(Integer, default=1)
    checkpoint_version: Mapped[int] = mapped_column(Integer, default=0)
    current_step: Mapped[str | None] = mapped_column(String(120), nullable=True)
    completed_steps: Mapped[list[str]] = mapped_column(JSON, default=list)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    approval_state: Mapped[str] = mapped_column(String(40), default="not_required")
    execution_state: Mapped[str] = mapped_column(String(40), default="idle")
    idempotency_key: Mapped[str] = mapped_column(String(200))
    wait_human_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    takeover: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    lease_worker: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_version: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    repair_round: Mapped[int] = mapped_column(Integer, default=0)
    max_repair_rounds: Mapped[int] = mapped_column(Integer, default=2)
    steps: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EnterpriseRuntimeCheckpointORM(Base):
    """企业版 E4-01 Runtime checkpoint（脱敏摘要 + 版本化，跨进程恢复）。"""

    __tablename__ = "enterprise_runtime_checkpoints"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(120), index=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    step_id: Mapped[str] = mapped_column(String(160))
    workflow_version: Mapped[int] = mapped_column(Integer, default=1)
    checkpoint_version: Mapped[int] = mapped_column(Integer, default=0)
    completed_steps: Mapped[list[str]] = mapped_column(JSON, default=list)
    current_step: Mapped[str | None] = mapped_column(String(120), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    approval_state: Mapped[str] = mapped_column(String(40), default="not_required")
    execution_state: Mapped[str] = mapped_column(String(40), default="idle")
    idempotency_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_summary: Mapped[str] = mapped_column(Text, default="")
    worker_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentRunORM(Base):
    """企业版 E4-02 独立 Agent 调用记录（每次 invoke + 修复轮次；租户隔离 + 幂等）。"""

    __tablename__ = "enterprise_agent_runs"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_agent_run_tenant_idem"),)

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    task_id: Mapped[str] = mapped_column(String(120), index=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    agent_type: Mapped[str] = mapped_column(String(64), index=True)
    step_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    repair_round: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), index=True)
    prompt_version: Mapped[str] = mapped_column(String(40), default="1")
    input_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    policy_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str] = mapped_column(String(200), default="")
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentArtifactORM(Base):
    """企业版 E4-02 版本化产物（租户隔离 + 引用图 + 幂等）；Agent 间只经 refs 传递。"""

    __tablename__ = "enterprise_agent_artifacts"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_agent_artifact_tenant_idem"),)

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(String(120), index=True)
    task_id: Mapped[str] = mapped_column(String(120), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    agent_type: Mapped[str] = mapped_column(String(64), index=True)
    artifact_type: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer, default=1)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="active")
    input_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    content: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    content_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    idempotency_key: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EnterpriseRagEvaluationRunORM(Base):
    """FIX-01 在线评测运行真源：API 创建、Worker 推进、API 查询跨进程一致。"""

    __tablename__ = "enterprise_rag_evaluation_runs"

    run_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    dataset: Mapped[str] = mapped_column(String(200))
    retrieval_mode: Mapped[str] = mapped_column(String(20), default="hybrid")
    top_k: Mapped[int] = mapped_column(Integer, default=8)
    execution_scope: Mapped[str] = mapped_column(String(20), default="isolated")
    status: Mapped[str] = mapped_column(String(20), index=True)
    validation_status: Mapped[str] = mapped_column(String(20), default="")
    report_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    requested_by: Mapped[str] = mapped_column(String(120), default="")
    requested_role: Mapped[str] = mapped_column(String(40), default="developer")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EnterpriseRagIndexLifecycleORM(Base):
    """FIX-02 索引 Embedding 生命周期真源：API/Worker 共用，重启不丢失。"""

    __tablename__ = "enterprise_rag_index_lifecycles"

    index_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    repository_id: Mapped[str] = mapped_column(String(80), index=True)
    project_id: Mapped[str] = mapped_column(String(80), index=True)
    base_branch: Mapped[str] = mapped_column(String(120), default="")
    commit_sha: Mapped[str] = mapped_column(String(80), default="")
    collection_name: Mapped[str] = mapped_column(String(200), default="")
    embedding_provider: Mapped[str] = mapped_column(String(80), default="")
    embedding_model: Mapped[str] = mapped_column(String(200), default="")
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_source: Mapped[str] = mapped_column(String(40), default="")
    embedding_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(20), index=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_stage: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    superseded_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EnterpriseRuntimeTraceSpanORM(Base):
    """FIX-03 跨进程 Trace span 真源；seq 自增用于保留上限与 dropped 统计。"""

    __tablename__ = "enterprise_runtime_trace_spans"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    span_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    trace_id: Mapped[str] = mapped_column(String(120), index=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    category: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="ok")
    error_kind: Mapped[str] = mapped_column(String(120), default="")
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    tokens: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
