"""企业版 E2-01：受治理操作注册表（基于真实业务对象，不虚构）。

每个操作绑定：资源类型、默认风险等级、所需业务权限、审批模式、是否允许自审。
operation code 是审批单和治理决策的唯一标识。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.governance.risk import RiskLevel

ApprovalMode = str  # "single" | "dual" | "security"


@dataclass(frozen=True)
class OperationSpec:
    operation: str
    resource_type: str
    default_risk: RiskLevel
    required_permission: str  # 团队/企业业务权限（如 index:manage / project:manage / member:manage）
    approval_mode: ApprovalMode = "single"
    allow_self_approve: bool = False  # 高风险默认禁止自审
    description: str = ""


OPERATIONS: dict[str, OperationSpec] = {
    "repository.index.rebuild": OperationSpec(
        operation="repository.index.rebuild",
        resource_type="repository",
        default_risk="L2",
        required_permission="index:manage",
        approval_mode="single",
        description="重建/触发仓库代码索引（影响索引元数据与 RAG 检索）。",
    ),
    "team_request.sync_gitee": OperationSpec(
        operation="team_request.sync_gitee",
        resource_type="team_request",
        default_risk="L2",
        required_permission="request:create",
        approval_mode="single",
        description="将平台需求同步到外部 Gitee 并创建 Issue。",
    ),
    "repository.archive": OperationSpec(
        operation="repository.archive",
        resource_type="repository",
        default_risk="L3",
        required_permission="project:manage",
        approval_mode="dual",
        description="归档仓库（退出当前使用，历史任务保留）。",
    ),
    "repository.delete": OperationSpec(
        operation="repository.delete",
        resource_type="repository",
        default_risk="L4",
        required_permission="project:manage",
        approval_mode="security",
        description="删除仓库（高危、需安全管理员 + 二次确认）。",
    ),
    "audit.export": OperationSpec(
        operation="audit.export",
        resource_type="tenant",
        default_risk="L4",
        required_permission="audit:view",
        approval_mode="security",
        description="导出租户审计数据（含敏感范围，需二次确认）。",
    ),
    "tenant.member.role_change": OperationSpec(
        operation="tenant.member.role_change",
        resource_type="tenant_member",
        default_risk="L4",
        required_permission="member:manage",
        approval_mode="security",
        description="变更租户成员角色（高风险、需安全管理员 + 二次确认）。",
    ),
    "runtime.execute": OperationSpec(
        operation="runtime.execute",
        resource_type="runtime_task",
        default_risk="L2",
        required_permission="task:execute",
        approval_mode="single",
        description="企业 Runtime 任务进入执行阶段（审批通过后回写 runtime，进入 execution）。",
    ),
}


def get_operation(operation: str) -> OperationSpec | None:
    return OPERATIONS.get(operation)


def list_operations() -> list[OperationSpec]:
    return sorted(OPERATIONS.values(), key=lambda o: (o.default_risk, o.operation))