"""企业版 E4-01 Multi-Agent Runtime 数据模型。

Runtime 是对既有 Task/ExecutionStep/Worker/审批链的**兼容层编排**（不重写团队链）：
在团队任务之上维护 RuntimeTask/RuntimeStep/Checkpoint/Lease 状态，驱动既有 worker 步骤与 E2 审批门禁。
所有记录 tenant-scoped；checkpoint 只存脱敏摘要。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RuntimeTaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_HUMAN = "waiting_human"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RuntimeStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_HUMAN = "waiting_human"


class FailureKind(str, Enum):
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"
    NEEDS_APPROVAL = "needs_approval"
    NEEDS_HUMAN = "needs_human"
    ENVIRONMENT = "environment"


# 企业 Runtime 编排步骤（主链），团队既有 worker 步骤名映射到这些节点。
# workflow_version=1 使用该旧 5 步链（兼容既有任务/旧库/旧测试）。
RUNTIME_STEPS = ("context_fetch", "enterprise_rag_analysis", "plan_generation", "execution", "test_execution")


class AgentType(str, Enum):
    """6 个专业 Agent。每步绑定 agent_type；Agent 只经 ArtifactStore/refs 交互。"""

    REQUIREMENT = "requirement_agent"
    REPOSITORY_RESEARCH = "repository_research_agent"
    PLANNER = "planner_agent"
    CODE_CHANGE = "code_change_agent"
    REVIEW_SECURITY = "review_security_agent"
    TEST_RELEASE = "test_release_agent"


# workflow_version=2 的 6-Agent 主链（每个 step.name 即 agent_type）。
AGENT_WORKFLOW_STEPS = tuple(t.value for t in AgentType)


def steps_for_workflow(version: int) -> tuple[str, ...]:
    """按 workflow_version 返回步骤名序列：v1 旧 5 步；v2 新 6-Agent 链。"""
    try:
        ver = int(version)
    except (TypeError, ValueError):
        ver = 1
    return RUNTIME_STEPS if ver <= 1 else AGENT_WORKFLOW_STEPS


class RuntimeStep(BaseModel):
    step_id: str
    task_id: str
    tenant_id: str
    name: str
    agent_type: str | None = None  # 绑定的 AgentType；v2 步骤必填
    status: RuntimeStepStatus = RuntimeStepStatus.PENDING
    retry_count: int = 0
    max_retries: int = 3
    backoff_seconds: float = 1.0
    failure_kind: FailureKind | None = None
    failure_reason: str | None = None
    output_summary: str = ""  # 脱敏摘要
    idempotency_key: str = ""
    worker_id: str | None = None
    # C4 修复/回退状态：区分第几轮修改与审查（不允许仅靠重置 PENDING 区分轮次）
    repair_round: int = 0
    agent_attempt: int = 0
    return_to: str | None = None  # 回退返回的 step 名
    return_reason: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RuntimeTask(BaseModel):
    id: str
    tenant_id: str
    project_id: str = ""
    repository_id: str = ""
    title: str = ""
    status: RuntimeTaskStatus = RuntimeTaskStatus.QUEUED
    version: int = 1
    workflow_version: int = 1
    checkpoint_version: int = 0
    current_step: str | None = None
    completed_steps: list[str] = Field(default_factory=list)
    retry_count: int = 0
    approval_state: str = "not_required"  # not_required | pending | approved | rejected
    execution_state: str = "idle"  # idle | executing | test
    idempotency_key: str = ""
    wait_human_reason: str | None = None
    takeover: dict | None = None  # {operator, action, reason, at}
    lease_worker: str | None = None
    lease_version: int = 0
    # C4 修复轮次（v2 review/test 回退用；默认最多 2 轮，防无限循环）
    repair_round: int = 0
    max_repair_rounds: int = 2
    steps: list[RuntimeStep] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class RuntimeCheckpoint(BaseModel):
    task_id: str
    step_id: str
    tenant_id: str
    workflow_version: int
    checkpoint_version: int
    completed_steps: list[str]
    current_step: str | None
    retry_count: int
    approval_state: str
    execution_state: str
    idempotency_keys: list[str]
    output_summary: str  # 脱敏
    worker_id: str | None
    lease_version: int
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ExecutionLease(BaseModel):
    task_id: str
    step_id: str
    worker_id: str
    lease_version: int
    expires_at: datetime
    granted_at: datetime = Field(default_factory=utcnow)


class HumanTakeoverAction(str, Enum):
    RESUME = "resume"
    RETRY = "retry"
    CANCEL = "cancel"