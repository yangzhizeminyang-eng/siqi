"""企业版 E4-02 多 Agent 协作图：LangGraph（v2 唯一流程路由）+ 共享路由决策函数。

原则（C3）：
- LangGraph（graph_for_workflow v2）= v2 的唯一流程拓扑/路由来源。
- Orchestrator service = 租约/持久化/权限/审计；按 graph 的决策驱动 step。
- AgentExecutor = 只执行当前 Agent。
- 拓扑：Requirement→Research→Planner→[E2审批]→CodeChange→Review→Test→SUCCEEDED；
  Review 发现问题←CodeChange；Test 失败←CodeChange；repair_round≤max（默认2）；超上限→WAITING_HUMAN。
  decide_next_agent 是 graph 与 orchestrator 共享的“单一路由真源”，防止两处状态机分歧。
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.enterprise_runtime.models import AGENT_WORKFLOW_STEPS, steps_for_workflow

MAX_REPAIR_ROUNDS = 2
NODE_APPROVAL = "approval_gate"

Decision = Literal["proceed", "back_code_change", "approve_wait", "human_wait", "succeeded", "failed"]


class AgentFlowState(TypedDict, total=False):
    agent_type: str
    step_index: int
    decision: Decision
    next_step: str
    repair_round: int
    reason: str
    max_repair_rounds: int
    _latest_artifact: dict | None  # 需声明，LangGraph 才会把该键传入节点（否则被丢弃）


def _chain() -> list[str]:
    return list(AGENT_WORKFLOW_STEPS)


def decide_next_agent(agent_type: str, *, latest_artifact: dict | None,
                      repair_round: int, max_repair_rounds: int = MAX_REPAIR_ROUNDS) -> Decision:
    """依据当前 agent 与最新产物决定下一步（图与编排共享，单一真源）。

    latest_artifact：该 agent 最近产物（含 approved/passed/…）；None 视为继续。
    """
    a = latest_artifact or {}
    if agent_type == "planner_agent":
        return "approve_wait"  # Planner 后必须经 E2 审批
    if agent_type == "review_security_agent":
        if a.get("approved") is False:
            return "back_code_change"  # 发现问题 → 回 CodeChange
        return "proceed"  # → Test
    if agent_type == "test_release_agent":
        if a.get("passed") is False:
            if repair_round < max_repair_rounds:
                return "back_code_change"  # 还有修复机会
            return "human_wait"  # 超上限 → 人工
        return "succeeded"
    if agent_type == "code_change_agent":
        return "proceed"  # → Review
    return "proceed"


def next_action(agent_type: str, *, approved: bool | None = None, passed: bool | None = None,
                repair_round: int = 0, max_repair_rounds: int = MAX_REPAIR_ROUNDS) -> Decision:
    """便捷封装：从已知 approved/passed 直接得到决策（测试友好）。"""
    art: dict[str, Any] = {}
    if agent_type == "review_security_agent" and approved is not None:
        art["approved"] = approved
    if agent_type == "test_release_agent" and passed is not None:
        art["passed"] = passed
    return decide_next_agent(agent_type, latest_artifact=art,
                             repair_round=repair_round, max_repair_rounds=max_repair_rounds)


def build_agent_graph() -> object:
    """v2 LangGraph：以 decide_next_agent 作为条件边路由（拓扑真源）。编译供检查/worker 使用。"""
    chain = _chain()
    g = StateGraph(AgentFlowState)

    def _node(tag: str):
        def _run(st: AgentFlowState) -> AgentFlowState:
            return {**st, "agent_type": tag,
                    "step_index": chain.index(tag) if tag in chain else st.get("step_index", 0)}
        return _run

    for tag in chain:
        g.add_node(tag, _node(tag))
    g.add_node(NODE_APPROVAL, lambda st: st)

    def _route(agent_type: str):
        def _fn(st: AgentFlowState) -> str:
            art: dict | None = st.get("_latest_artifact")
            decision = decide_next_agent(
                agent_type,
                latest_artifact=art if isinstance(art, dict) else None,
                repair_round=int(st.get("repair_round", 0)),
                max_repair_rounds=int(st.get("max_repair_rounds", MAX_REPAIR_ROUNDS)),
            )
            st["decision"] = decision
            if agent_type == "planner_agent":
                return NODE_APPROVAL
            if decision == "back_code_change":
                return "code_change_agent"
            if decision == "human_wait":
                return "test_release_agent"  # 由 Orchestrator 判定停机（WAITING_HUMAN）→ END
            if decision == "succeeded":
                return END
            idx = chain.index(agent_type) if agent_type in chain else 0
            return chain[idx + 1] if idx + 1 < len(chain) else END
        return _fn

    g.add_edge(START, "requirement_agent")
    g.add_edge("requirement_agent", "repository_research_agent")
    g.add_edge("repository_research_agent", "planner_agent")
    # 审批门：Planner 后置 WAITING_APPROVAL（编排停机），通过后进 CodeChange
    g.add_conditional_edges(NODE_APPROVAL, lambda st: "code_change_agent")
    g.add_conditional_edges("planner_agent", _route("planner_agent"))
    g.add_conditional_edges("code_change_agent", _route("code_change_agent"))
    g.add_conditional_edges("review_security_agent", _route("review_security_agent"))
    g.add_conditional_edges("test_release_agent", _route("test_release_agent"))
    return g.compile(name="enterprise_agent_flow")


_V2_GRAPH_CACHE: dict[int, object] = {}
_ROUTER_CACHE: dict[str, object] = {}


def _router_node():
    """LangGraph 路由器节点：由 decide_next_agent 决定 decision 与 next_step（单一真源）。

    next_step 也是图计算得出，constraint #6：Orchestrator 不得再用
    workflow_steps()[index+1] 手动模拟主路由。
    """
    chain = _chain()

    def _run(st: AgentFlowState) -> AgentFlowState:
        at = st.get("agent_type", "")
        art = st.get("_latest_artifact")
        repair = int(st.get("repair_round", 0))
        mx = int(st.get("max_repair_rounds", MAX_REPAIR_ROUNDS))
        decision = decide_next_agent(at, latest_artifact=art if isinstance(art, dict) else None,
                                     repair_round=repair, max_repair_rounds=mx)
        st["decision"] = decision
        if decision == "proceed":
            idx = int(st.get("step_index", 0))
            st["next_step"] = chain[idx + 1] if idx + 1 < len(chain) else "end"
        elif decision == "back_code_change":
            st["next_step"] = "code_change_agent"
        elif decision == "human_wait":
            st["next_step"] = "human_wait"
        elif decision == "approve_wait":
            st["next_step"] = "approval_gate"
        else:  # succeeded
            st["next_step"] = "end"
        return st

    return _run


def _build_router_graph():
    """编译后的 LangGraph 路由器：STAR → router → END。这是 v2 的实际流程路由入口。"""
    g = StateGraph(AgentFlowState)
    g.add_node("router", _router_node())
    g.add_edge(START, "router")
    g.add_edge("router", END)
    return g.compile(name="agent_router")


def route_via_graph(*, agent_type: str, latest_artifact: dict | None, step_index: int = 0,
                    repair_round: int = 0, max_repair_rounds: int = MAX_REPAIR_ROUNDS):
    """编排每步由编译后的 LangGraph 路由器决定下一步（真实 invoke）。

    返回 (decision, next_step)：两者都由图计算，避免 Orchestrator 手动模拟主路由（constraint #6）。
    """
    g = _ROUTER_CACHE.get(agent_type) or _build_router_graph()
    _ROUTER_CACHE.setdefault(agent_type, g)
    state: AgentFlowState = {
        "agent_type": agent_type, "step_index": step_index, "decision": "proceed",
        "next_step": "end", "repair_round": repair_round, "reason": "",
        "_latest_artifact": latest_artifact, "max_repair_rounds": max_repair_rounds,
    }
    out = g.invoke(state)
    decision = (out or {}).get("decision") or "proceed"
    next_step = (out or {}).get("next_step") or "end"
    return decision, next_step


def graph_for_workflow(version: int) -> object | None:
    """v1 → None（走既有 run_until_blocked）；v2 → 编译后的多 Agent LangGraph。"""
    try:
        ver = int(version)
    except (TypeError, ValueError):
        ver = 1
    if ver <= 1:
        return None
    if ver not in _V2_GRAPH_CACHE:
        _V2_GRAPH_CACHE[ver] = build_agent_graph()
    return _V2_GRAPH_CACHE[ver]


def workflow_steps(version: int) -> tuple[str, ...]:
    return steps_for_workflow(version)