"""企业版 E4-02 AgentExecutorRegistry：agent_type -> AgentExecutor（v2 编排的注册表）。

只做执行原语；不改变全局流程（流程由 LangGraph/Orchestrator 决定，C3）。
resolve(step) 按 step.agent_type 返回执行器；带 AgentToolPolicyValidator 服务端强制。
"""

from __future__ import annotations

from app.enterprise_runtime.agents.agent_definitions import AGENT_DEFINITIONS
from app.enterprise_runtime.agents.executor import AgentExecutor
from app.enterprise_runtime.agents.policy import AgentToolPolicyValidator


class AgentExecutorRegistry:
    def __init__(self, *, audit: callable | None = None):
        self._audit = audit
        self.validator = AgentToolPolicyValidator(AGENT_DEFINITIONS, audit=audit)
        self._executors: dict[str, AgentExecutor] = {}

    def get(self, agent_type: str) -> AgentExecutor:
        if agent_type not in AGENT_DEFINITIONS:
            raise KeyError(f"未知 agent_type: {agent_type}")
        ex = self._executors.get(agent_type)
        if ex is None:
            ex = AgentExecutor(agent_type, validator=self.validator, audit=self._audit)
            self._executors[agent_type] = ex
        return ex

    def resolve(self, step) -> AgentExecutor | None:
        at = getattr(step, "agent_type", None)
        if not at:
            return None
        try:
            return self.get(at)
        except KeyError:
            return None

    def agent_types(self) -> list[str]:
        return sorted(AGENT_DEFINITIONS)

    def build_executors(self) -> dict[str, AgentExecutor]:
        return {at: self.get(at) for at in self.agent_types()}