"""企业版 E2-01 审批申请 pydantic 模型与入参。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ApprovalStatus = Literal[
    "pending", "approved", "executing", "executed",
    "rejected", "cancelled", "expired",
]
GovernanceDecision = Literal["allow", "deny", "approval_required"]

DEFAULT_APPROVAL_TTL_SECONDS = 24 * 3600  # 默认 24h 过期
# executing 超时阈值：抢占后超过该时长仍未 settle，视为卡死（如 settle 写库故障），回收为可重试 approved
EXECUTING_TIMEOUT_SECONDS = 5 * 60

# 审批状态机中可发起执行的起点（claim 可抢占的源状态）。
# executing 为 claim 抢占后的瞬时态；执行失败回退 approved（可重试），不引入 execution_failed
# 以避免破坏既有「失败=409+状态保持 approved」合同（见 E2 基线测试）。
CLAIMABLE_STATUSES = frozenset({"approved"})
# 终态：不可再转变
TERMINAL_STATUSES = frozenset({"rejected", "cancelled", "expired", "executed"})


class ApprovalApplicationRecord(BaseModel):
    id: str
    tenant_id: str
    actor_id: str
    actor_name: str = ""
    resource_type: str
    resource_id: str
    operation: str
    title: str = ""
    request_summary: dict = Field(default_factory=dict)
    risk_level: str = "L2"
    decision: GovernanceDecision = "approval_required"
    approval_status: ApprovalStatus = "pending"
    reason: str = ""
    idempotency_key: str = ""
    created_by: str = ""
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    approved_by_id: str | None = None
    approved_by_name: str | None = None
    approved_at: datetime | None = None
    executed_at: datetime | None = None
    executed_by_id: str | None = None
    executor_result: dict = Field(default_factory=dict)
    resource_version: str | None = None
    allowed_approvers: list[str] = Field(default_factory=list)
    approval_mode: str = "single"
    approval_meta: dict = Field(default_factory=dict)
    secondary_confirmation: bool = False
    version: int = 1
    # ---- E2-01 多副本执行追踪（worker 回写/重试/审计追踪）----
    execution_attempt_id: str | None = None
    async_result_id: str | None = None
    retry_count: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    execution_error: str | None = None


class ApprovalSubmitPayload(BaseModel):
    operation: str
    resource_type: str
    resource_id: str
    title: str = Field(default="", max_length=200)
    request_summary: dict | None = None
    idempotency_key: str = Field(min_length=1, max_length=120)
    expires_in_seconds: int | None = Field(default=None, ge=60, le=7 * 24 * 3600)
    secondary_confirmation: bool = False


class ApprovalApprovePayload(BaseModel):
    comment: str | None = Field(default=None, max_length=500)


class ApprovalRejectPayload(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class ApprovalCancelPayload(BaseModel):
    reason: str = Field(default="", max_length=500)


class ApprovalExecutePayload(BaseModel):
    secondary_confirmation: bool = False