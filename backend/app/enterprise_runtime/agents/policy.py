"""企业版 E4-02 服务端强制执行工具/产物边界（C7）。

Prompt 只是提示，不是安全边界。所有工具调用、产物写入、测试命令都过这里：
- 模型即便意图调用白名单外工具，也会被拒绝并审计。
- PlannerAgent 全程无写代码工具；Review 无修改工具；Test 命令过白名单。
"""

from __future__ import annotations

from typing import Iterable

from app.enterprise_runtime.agents.definition import AgentDefinition

AUDIT_DENY = "agent.policy.denied"


class PolicyViolation(PermissionError):
    """服务端拒绝越权（工具/产物/命令）。"""


class AgentToolPolicyValidator:
    """基于 AgentDefinition 的服务端策略校验器（无状态，纯判定 + 审计回调）。"""

    def __init__(self, definitions: dict[str, AgentDefinition], audit: callable | None = None):
        self._defs = definitions
        self._audit = audit  # audit(agent_type, reason, detail) 由调用方注入（脱敏）

    def definition(self, agent_type: str) -> AgentDefinition:
        d = self._defs.get(agent_type)
        if d is None:
            raise PolicyViolation(f"未知 agent_type: {agent_type}")
        return d

    # ---- 工具白名单（服务端强制：模型调不到白名单外的工具） ----
    def assert_tool_allowed(self, agent_type: str, tool_name: str) -> None:
        d = self.definition(agent_type)
        ok = tool_name in d.tool_whitelist
        if not ok:
            self._deny(agent_type, f"越权工具调用: {tool_name}",
                       f"agent 无权使用工具 {tool_name}（白名单={sorted(d.tool_whitelist)}）")
        if tool_name not in d.readonly_tools and tool_name in {"workspace_write"}:
            # 写工具即工作区写入；tool_whitelist 已含，仅提示（Read-only 判定在调用层）
            pass

    def assert_write_tool_allowed(self, agent_type: str, tool_name: str) -> None:
        d = self.definition(agent_type)
        if tool_name in d.readonly_tools or tool_name not in d.tool_whitelist:
            self._deny(agent_type, f"拒绝写工具: {tool_name}",
                       f"agent({'readonly' if tool_name in d.readonly_tools else '未授权'}) 调用写工具 {tool_name}")

    # ---- 测试命令白名单（TestRelease：命令必须通过 run_test 的白名单+沙箱） ----
    def assert_test_command_allowed(self, agent_type: str, command: str,
                                    allowlist: Iterable[str] | None = None) -> None:
        d = self.definition(agent_type)
        if agent_type != "test_release_agent" or "run_test" not in d.tool_whitelist:
            # 非 TestRelease 一律禁执行命令
            self._deny(agent_type, "越权执行命令", f"非 test_release_agent 调用命令: {command[:80]}")
        cmd = (command or "").strip().lower().split()
        if not cmd:
            self._deny(agent_type, "空命令", "命令为空")
        allowed = {a.lower() for a in (allowlist or [])}
        if allowed and cmd[0] not in allowed:
            self._deny(agent_type, "命令不在白名单", f"命令 {command[:80]} 不在白名单 {sorted(allowed)}")

    # ---- 产物读写白名单（只能写允许的 artifact 类型） ----
    def assert_artifact_write_allowed(self, agent_type: str, artifact_type: str) -> None:
        d = self.definition(agent_type)
        if artifact_type not in d.allowed_write_artifact_types:
            self._deny(agent_type, "越权写产物",
                       f"agent 无权写 {artifact_type}（允许={sorted(d.allowed_write_artifact_types)}）")

    def assert_artifact_read_allowed(self, agent_type: str, artifact_type: str) -> None:
        d = self.definition(agent_type)
        if d.allowed_read_artifact_types and artifact_type not in d.allowed_read_artifact_types:
            self._deny(agent_type, "越权读产物",
                       f"agent 无权读 {artifact_type}（允许={sorted(d.allowed_read_artifact_types)}）")

    def _deny(self, agent_type: str, reason: str, detail: str) -> None:
        if self._audit is not None:
            try:
                self._audit(agent_type, reason, detail)
            except Exception:  # noqa: BLE001
                pass
        raise PolicyViolation(f"[{AUDIT_DENY}] agent={agent_type} {reason}")


def build_policy_context(definition: AgentDefinition, *, tenant_id: str = "", task_id: str = "") -> dict:
    """由 Orchestrator 注入的 policy_context：Agent 不能自行扩大权限。"""
    return {
        "agent_type": definition.agent_type,
        "tenant_id": tenant_id,
        "task_id": task_id,
        "tool_whitelist": sorted(definition.tool_whitelist),
        "readonly_tools": sorted(definition.readonly_tools),
        "context_scope": definition.context_scope,
        "allowed_read_artifact_types": list(definition.allowed_read_artifact_types),
        "allowed_write_artifact_types": list(definition.allowed_write_artifact_types),
        "provider": definition.provider,
        "system_prompt_digest": hash(definition.system_prompt) & 0xFFFFFFFF,
    }