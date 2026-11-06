"""企业版 E4-02 Agent 后端：真实 LangChain create_agent / DeepAgent；测试用 deterministic（test-only）。

Invoke 契约：backend.invoke(structured_input: dict) -> dict（经 output_schema 校验的结构化结果）。
真实路径调用配置的模型适配器（与团队版同栈）；生产绝不走 deterministic。
"""

from __future__ import annotations

import os
from typing import Any

from app.enterprise_runtime.agents.agent_definitions import AGENT_DEFINITIONS
from app.enterprise_runtime.agents.schema import INPUT_MODELS, OUTPUT_MODELS


class AgentBackend:
    """Agent 执行后端统一接口：接收结构化输入，返回结构化解。"""

    agent_type: str

    def invoke(self, structured_input: dict) -> dict:
        raise NotImplementedError


class DeterministicAgent(AgentBackend):
    """测试/确定性模型 provider（仅 AGENT_PROVIDER=deterministic 且显式 test 环境；默认禁止）。

    不是“假成功”：依据结构化输入推导出符合 output_schema 的结果；review/test 可经 debug 字段
    由测试用例驱动通过/失败，用于分支演练。生产路径永不构造此类。"""

    def __init__(self, agent_type: str, model_label: str = "deterministic"):
        self.agent_type = agent_type
        self.model = model_label

    def invoke(self, structured_input: dict) -> dict:
        at = self.agent_type
        if at == "requirement_agent":
            return {
                "summary": (structured_input.get("issue") or "")[:120] or "no issue",
                "requirements": [
                    {"id": f"r{i + 1}", "description": (structured_input.get("issue") or f"requirement {i + 1}")[:90],
                     "acceptance": "看得见的行为可验证"} for i in range(2)
                ],
                "open_questions": [],
                "completeness": "complete",
            }
        if at == "repository_research_agent":
            return {
                "findings": [{"file": "src/main.py", "summary": "核心入口", "relevance": "high"}],
                "files": ["src/main.py", "src/service.py"],
                "symbols": ["run_app", "Service.handle"],
                "code_context": [{"file": "src/main.py", "symbol": "run_app", "snippet": "def run_app(): ..."}],
            }
        if at == "planner_agent":
            dec = (structured_input.get("_deterministic_decision") or {})
            return {
                "objectives": ["实现需求", "补充测试"],
                "steps": [
                    {"file": "src/service.py", "change": "新增 handle 逻辑", "reason": "满足需求", "risk": "low"},
                    {"file": "tests/test_service.py", "change": "补充用例", "reason": "回归", "risk": "low"},
                ],
                "test_command": dec.get("test_command") or "pytest tests/test_service.py",
                "approval_request_note": "请求审批执行修改",
            }
        if at == "code_change_agent":
            return {
                "workspace_path": "/isolated/ws",
                "files_changed": ["src/service.py", "tests/test_service.py"],
                "patch": "diff --git a/src/service.py b/src/service.py\n+def handle(): ...",
                "diff_ref": "",
                "test_command": "pytest tests/test_service.py",
            }
        if at == "review_security_agent":
            dec = (structured_input.get("_deterministic_decision") or {})
            approved = bool(dec.get("approved", True))
            return {
                "approved": approved,
                "conclusions": ["通过" if approved else "存在问题，需修复"],
                "issues": [] if approved else [{"severity": "high", "file": "src/service.py",
                                                "desc": "未处理边界", "fix_hint": "补边界校验"}],
                "security_risks": [] if approved else [{"severity": "high", "desc": "潜在的越权风险"}],
            }
        if at == "test_release_agent":
            dec = (structured_input.get("_deterministic_decision") or {})
            passed = bool(dec.get("passed", True))
            return {
                "passed": passed,
                "summary": "全部通过，可以发布" if passed else "测试失败，需修复",
                "test_cases": [{"name": "test_handle", "status": "passed" if passed else "failed"}],
                "pr_description": "PR: 实现需求（deterministic）",
                "release_evidence": {"passed": passed, "summary": "证据摘要", "created_at": "deterministic"},
            }
        return {}


def build_chat_model():
    """真实模型适配器（与团队版 build_task_deep_agent 同栈）；未配置 LLM 时抛清晰错误。"""
    from app.core import model_config
    from langchain_openai import ChatOpenAI

    if not model_config.llm_configured():
        raise RuntimeError("真实 Agent 需要配置 LLM（LLM_PROVIDER/LLM_MODEL/LLM_BASE_URL/LLM_API_KEY）")
    settings = model_config.load_llm_settings()
    provider = (settings.provider or "").lower()
    if provider in {"deepseek", "openai"}:
        return ChatOpenAI(model=settings.model, base_url=settings.base_url,
                          api_key=settings.api_key, temperature=0.1,
                          timeout=settings.timeout_seconds, max_retries=settings.max_retries)
    from langchain.chat_models import init_chat_model
    return init_chat_model(settings.model, model_provider="openai",
                           base_url=settings.base_url, api_key=settings.api_key,
                           timeout=settings.timeout_seconds, max_retries=settings.max_retries)


def build_tools_for(agent_type: str) -> list:
    """为该 agent 发放白名单内的真实工具（只发放白名单工具；无则空列表）。"""
    from app.enterprise_runtime.agents.tools import build_tools
    return build_tools(agent_type)


class _RealLangchainAgent(AgentBackend):
    def __init__(self, agent_type: str, compiled, model_label: str):
        self.agent_type = agent_type
        self._agent = compiled
        self.model = model_label
        self.last_usage: dict = {}

    def invoke(self, structured_input: dict) -> dict:
        output, _tool_trace = self.invoke_with_trace(structured_input)
        return output

    def invoke_with_trace(self, structured_input: dict) -> tuple[dict, list[dict]]:
        out_schema = OUTPUT_MODELS[self.agent_type]
        prompt = (
            f"请依据以下结构化输入完成本 Agent 的任务并以 JSON 输出（符合 schema）:\n"
            f"{_safe_json(structured_input)}\n"
        )
        from langchain_core.messages import HumanMessage
        try:
            recursion_limit = int(os.environ.get("AGENT_RECURSION_LIMIT", "8"))
        except ValueError:
            recursion_limit = 8
        recursion_limit = max(2, min(recursion_limit, 32))
        result = self._agent.invoke(
            {"messages": [HumanMessage(content=prompt)]},
            config={"recursion_limit": recursion_limit},
        )
        tool_trace = []
        if isinstance(result, dict):
            for message in result.get("messages") or []:
                for call in getattr(message, "tool_calls", None) or []:
                    name = str(call.get("name") or "").strip()
                    if name:
                        tool_trace.append({"tool_name": name})
        structured = result.get("structured_response") if isinstance(result, dict) else None
        if structured is not None and hasattr(structured, "model_dump"):
            structured = structured.model_dump()
        if structured is None and isinstance(result, dict):
            for m in reversed(result.get("messages") or []):
                if getattr(m, "type", "") == "ai" and getattr(m, "content", None):
                    structured = _try_parse(getattr(m, "content", ""))
                    if structured is not None:
                        break
        if not isinstance(structured, dict):
            raise ValueError(f"agent {self.agent_type} 未返回结构化响应")
        self.last_usage = self._collect_usage(result)
        return out_schema.model_validate(structured).model_dump(), tool_trace

    @staticmethod
    def _collect_usage(result: object) -> dict:
        """OPT-07：汇总真实 Token 用量（存在才透传；不存在则由调用方如实标 not_collected）。"""
        if not isinstance(result, dict):
            return {}
        for message in reversed(result.get("messages") or []):
            meta = getattr(message, "usage_metadata", None)
            if not meta:
                continue
            tokens = {
                "input_tokens": meta.get("input_tokens"),
                "output_tokens": meta.get("output_tokens"),
                "total_tokens": meta.get("total_tokens"),
            }
            if all(v is not None for v in tokens.values()):
                return {"tokens": tokens}
        return {}


def _try_parse(text: str) -> dict | None:
    import json, re
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None


def _safe_json(obj: Any) -> str:
    import json
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return str(obj)[:2000]


def build_agent(agent_type: str) -> AgentBackend:
    """构建指定 Agent 后端。test-only deterministic 需显式 AGENT_PROVIDER=deterministic。"""
    if agent_type not in AGENT_DEFINITIONS:
        raise KeyError(agent_type)
    definition = AGENT_DEFINITIONS[agent_type]

    if os.environ.get("AGENT_PROVIDER", "").strip().lower() == "deterministic":
        return DeterministicAgent(agent_type)

    # 真实路径：LangChain create_agent（DeepAgent 仅 CodeChange 可选，禁用子 Agent 委派）
    from langchain.agents import create_agent

    model = build_chat_model()
    tools = build_tools_for(agent_type)
    if definition.provider == "deep":
        from deepagents import create_deep_agent
        compiled = create_deep_agent(
            model=model, tools=tools, system_prompt=definition.system_prompt,
            response_format=OUTPUT_MODELS[agent_type], name=definition.name, debug=False,
            subagents=None, skills=[], permissions=[],
        )
    else:
        compiled = create_agent(
            model=model, tools=tools, system_prompt=definition.system_prompt,
            response_format=OUTPUT_MODELS[agent_type], name=definition.name,
        )
    return _RealLangchainAgent(agent_type, compiled,
                               f"{getattr(model, 'model_name', getattr(model, 'model', 'llm'))}")
