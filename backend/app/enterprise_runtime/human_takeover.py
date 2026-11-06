"""企业版 E4-01 人工接管：权限矩阵 + 动作门禁（resume/retry/cancel）。

权限矩阵（操作者须为治理/项目负责人角色；auditor 明确排除；跨租户一律 403+审计）：
| 角色 | 可接管 |
| --- | --- |
| tenant_owner / tenant_admin / security_admin / project_owner | ✅ |
| developer / reviewer / auditor | ❌（auditor 默认不可接管/执行高风险） |

人工接管不能跳过审批：若下一步是 execution 且审批未通过，resume/retry 会被打回 WAITING_APPROVAL。
"""

from __future__ import annotations

from app.enterprise_runtime import orchestrator

# 允许人工接管的角色（与 orchestrator 一致）
TAKEOVER_ROLES = ("tenant_owner", "tenant_admin", "security_admin", "project_owner")


def can_takeover(ctx) -> bool:
    """前端展示用；最终授权由后端 complete_human_takeover 判定。"""
    return orchestrator._can_takeover(ctx)


def request_takeover(ctx, task_id: str, step_id: str, reason: str):
    return orchestrator.request_human_takeover(ctx, task_id, step_id, reason)


def complete_takeover(ctx, task_id: str, action: str):
    return orchestrator.complete_human_takeover(ctx, task_id, action)