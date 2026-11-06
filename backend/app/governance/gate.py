"""企业版 E2-01：集中式治理门政治理门禁。

gate() 是「所有高风险操作必须经过后端治理判断」的统一入口：
- 解析请求级租户上下文与操作注册表；
- 校验业务范围权限 + 资源租户归属；
- 评估策略，记录治理决策审计；
- decision=deny -> 403；allow -> 放行；approval_required -> 由调用方创建审批单。
"""

from __future__ import annotations

from app.governance.operations import OperationSpec, get_operation
from app.governance.policies import evaluate
from app.tenancy.context import deny_and_audit, forbidden
from app.tenancy.models import TenantContext


def _has_business_permission(context: TenantContext, user, permission: str) -> bool:
    from app.tenancy.permissions import has_permission

    if has_permission(context, permission):
        return True
    try:
        from app.main import ROLE_PERMISSIONS

        return permission in ROLE_PERMISSIONS.get(getattr(user, "role", ""), [])
    except Exception:  # noqa: BLE001
        return False


def gate(context: TenantContext, user, operation: str, *, secondary_confirmation: bool = False) -> dict:
    """治理前置门：返回决策 dict（含 decision/risk/reason/hits）。deny 时抛 403 并审计。"""
    spec = get_operation(operation)
    if spec is None:
        deny_and_audit(context, f"governance.gate.{operation}", "未知治理操作",
                       resource_type="governance", resource_id=operation)
        raise forbidden()
    decision = evaluate(
        context, spec,
        has_business_permission=_has_business_permission(context, user, spec.required_permission),
        secondary_confirmation=secondary_confirmation,
    )
    deny_and_audit(
        context, f"governance.decide.{operation}",
        f"治理决策 {decision['decision']}: {decision['reason']}",
        resource_type=spec.resource_type, resource_id=operation, result=decision["decision"],
    )
    if decision["decision"] == "deny":
        raise forbidden(decision["reason"])
    return decision