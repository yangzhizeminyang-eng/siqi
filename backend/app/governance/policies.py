"""企业版 E2-01：治理决策评估。

decision：allow | deny | approval_required。策略为服务端集中式、可审计，前端不参与最终判定。
"""

from __future__ import annotations

from app.governance.operations import OperationSpec
from app.governance.risk import RISK_ORDER, RiskLevel
from app.tenancy.models import TenantContext


def evaluate(context: TenantContext,
             op: OperationSpec,
             *,
             has_business_permission: bool = True,
             secondary_confirmation: bool = False) -> dict:
    """返回治理决策：
    {
      decision: "allow"|"deny"|"approval_required",
      risk_level, approval_mode, self_approvable,
      reason, hits: [str]
    }
    """
    hits: list[str] = []
    if not has_business_permission:
        return {
            "decision": "deny",
            "risk_level": op.default_risk,
            "approval_mode": op.approval_mode,
            "self_approvable": op.allow_self_approve,
            "required_permission": op.required_permission,
            "reason": f"缺少业务权限 {op.required_permission}，操作被治理拒绝",
            "hits": [f"business:permission_missing:{op.required_permission}"],
        }

    risk = op.default_risk
    hits.append(f"operation:{op.operation}:risk={risk}")

    if RISK_ORDER[risk] <= RISK_ORDER["L1"]:
        return {
            "decision": "allow",
            "risk_level": risk,
            "approval_mode": "single",
            "self_approvable": True,
            "required_permission": op.required_permission,
            "reason": "低风险操作，默认允许并记录治理决策",
            "hits": ["risk:L0/L1:allow"],
        }

    if risk == "L4":
        if not secondary_confirmation:
            return {
                "decision": "approval_required",
                "risk_level": risk,
                "approval_mode": "security",
                "self_approvable": op.allow_self_approve,
                "required_permission": op.required_permission,
                "reason": "L4 高危操作需安全管理员审批并要求二次确认",
                "hits": ["risk:L4:security+confirmation"],
            }
        hits.append("risk:L4:secondary_confirmation")
    if risk == "L3":
        hits.append("risk:L3:dual")
    if risk == "L2":
        hits.append("risk:L2:project_owner_required")

    return {
        "decision": "approval_required",
        "risk_level": risk,
        "approval_mode": op.approval_mode,
        "self_approvable": op.allow_self_approve,
        "required_permission": op.required_permission,
        "reason": "高风险操作需后端审批门禁",
        "hits": hits,
    }


def can_self_approve(op: OperationSpec, risk: RiskLevel) -> bool:
    """审批人能否审批自己提交的申请：仅低风险（L0/L1）或操作显式允许时才能自审。"""
    return op.allow_self_approve or RISK_ORDER[risk] <= RISK_ORDER["L1"]