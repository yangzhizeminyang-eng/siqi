"""企业版 E2-01 审批中心与治理决策接口（/api/enterprise/approvals + /governance）。

所有端点：服务端解析激活租户（main.get_current_user -> 中间件 contextvar），
授权由 require_tenant_permission + 状态机 + 治理门完成后端判定；前端仅展示。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Query

from app.approvals import service
from app.approvals.models import (
    ApprovalApprovePayload,
    ApprovalCancelPayload,
    ApprovalExecutePayload,
    ApprovalRejectPayload,
    ApprovalSubmitPayload,
)
from app.governance.operations import get_operation, list_operations
from app.tenancy.context import context_for_current_user
from app.tenancy.permissions import require_tenant_permission, GOVERNANCE_MANAGE

router = APIRouter(prefix="/api/enterprise", tags=["enterprise-approval"])


def _current_user_dependency(agent_session: Annotated[str | None, Cookie()] = None):
    """复用 main.get_current_user（含服务端解析激活租户并写入 contextvar）。"""
    from app import main  # 延迟导入避免循环

    return main.get_current_user(agent_session)


CurrentUser = Annotated[object, Depends(_current_user_dependency)]


def _ok(data=None, message: str = "success") -> dict:
    return {"code": 0, "message": message, "data": data}


def _dump(record) -> dict:
    return record.model_dump(mode="json")


# ---------------- 审批中心 ----------------

@router.get("/approvals")
def list_approvals(user: CurrentUser,
                   status: str | None = Query(default=None, alias="status"),
                   risk: str | None = Query(default=None, alias="risk"),
                   scope: str = Query(default="all", pattern="^(all|mine|submitted)$")):
    ctx = context_for_current_user()
    records = service.list_approvals(ctx, user, status_filter=status, risk_filter=risk, scope=scope)
    return _ok([_dump(r) for r in records])


@router.get("/approvals/{approval_id}")
def get_approval(approval_id: str, user: CurrentUser):
    ctx = context_for_current_user()
    return _ok(_dump(service.get_approval(ctx, user, approval_id)))


@router.post("/approvals")
def create_approval(payload: ApprovalSubmitPayload, user: CurrentUser):
    ctx = context_for_current_user()
    record = service.create_approval(ctx, user, payload)
    status_code = 200
    return _ok(_dump(record), "审批申请已提交，等待审批")


@router.post("/approvals/{approval_id}/approve")
def approve_approval(approval_id: str, payload: ApprovalApprovePayload, user: CurrentUser):
    ctx = context_for_current_user()
    record = service.approve(ctx, user, approval_id, payload)
    if record.approval_status == "approved":
        return _ok(_dump(record), "审批已通过")
    return _ok(_dump(record), "已记录审批，仍需其他人批准")


@router.post("/approvals/{approval_id}/reject")
def reject_approval(approval_id: str, payload: ApprovalRejectPayload, user: CurrentUser):
    ctx = context_for_current_user()
    record = service.reject(ctx, user, approval_id, payload)
    return _ok(_dump(record), "审批已拒绝")


@router.post("/approvals/{approval_id}/cancel")
def cancel_approval(approval_id: str, payload: ApprovalCancelPayload, user: CurrentUser):
    ctx = context_for_current_user()
    record = service.cancel(ctx, user, approval_id, payload)
    return _ok(_dump(record), "审批已取消")


@router.post("/approvals/{approval_id}/execute")
def execute_approval(approval_id: str, payload: ApprovalExecutePayload, user: CurrentUser):
    ctx = context_for_current_user()
    record = service.execute(ctx, user, approval_id, payload)
    return _ok(_dump(record), "审批操作已执行")


# ---------------- 治理决策 ----------------

@router.post("/governance/decide")
def governance_decision(payload: ApprovalSubmitPayload, user: CurrentUser):
    """治理决策说明（dry-run，不产生审批单）：返回 allow/deny/approval_required 及原因。"""
    ctx = context_for_current_user()
    return _ok(service.preview_governance(ctx, user, payload))


@router.get("/governance/operations")
def governance_operations(user: CurrentUser):
    ctx = context_for_current_user()
    require_tenant_permission(ctx, GOVERNANCE_MANAGE)
    return _ok([{
        "operation": op.operation,
        "resource_type": op.resource_type,
        "risk_level": op.default_risk,
        "approval_mode": op.approval_mode,
        "required_permission": op.required_permission,
        "description": op.description,
    } for op in list_operations()])
