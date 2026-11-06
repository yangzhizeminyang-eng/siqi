"""LangGraph 任务工作流：让图真正承载任务状态流转。

节点：issue 拉取/上下文补齐(前置聚合) → AI 分析 → 计划生成 → 审批等待(可暂停/恢复)
      → Diff 生成 → 测试执行 → 完成；另设失败分流节点。

职责边界：
- LangChain（model_config）负责模型调用与结构化输出（build_task_deep_agent）；
- DeepAgents 负责 Agent 能力封装（build_task_deep_agent）；
- 各阶段节点的“具体工作”复用 main.py 既有服务函数（generate_analysis/attach_plan/
  diff_service/test_runner 等），不重复实现业务；
- 状态流转、阶段路由、审批暂停/恢复、失败分流由本图承载；
- Celery（workers/tasks.run_agent）调用本图，不再绕过它直接跑 main 状态机。

审批等待时通过 AgentCheckpointORM 落 checkpoint 实现暂停/恢复；节点异常路由到 fail 节点。
"""

from __future__ import annotations

import logging
from typing import TypedDict, Any

from langgraph.graph import END, START, StateGraph

logger = logging.getLogger("backend.agent")

from app.core import model_config


class TaskWorkflowState(TypedDict, total=False):
    task_id: str
    node: str
    status: str
    next_action: str
    progressed: bool
    deferred: bool
    http: bool
    waiting: bool
    blocked: bool
    reason: str
    # eager=True 供 Celery Worker / HTTP 降级入口驱动到终态；False 仅作图直调兼容
    eager: bool


def _load(task_id: str):
    from app.main import TASKS

    return TASKS.get(task_id)


def _save(task) -> None:
    from app.main import _persist_task

    _persist_task(task)


def _checkpoint(task_id: str, node_name: str, state: TaskWorkflowState, *, status: str) -> None:
    try:
        from app.db.repositories import upsert_checkpoint

        upsert_checkpoint(
            task_id,
            node_name,
            {"status": state.get("status", ""), "next_action": state.get("next_action", "")},
            status=status,
            checkpoint_id=f"cp_graph_{task_id}",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 节点
# ---------------------------------------------------------------------------


def node_prepare_context(state: TaskWorkflowState) -> TaskWorkflowState:
    """前序阶段：索引进度等待 / 上下文补齐 / 分析队列（ANALYSIS_QUEUED 之前）。

    - 图推进统一先做 `main._prepare_context`，前序/分析规则由同一内核驱动；
    - `main.progress_task_automation` 仅保留为兼容回退路径，不再作为主编排入口。
    """
    task = _load(state["task_id"])
    if task is None:
        return {**state, "node": "prepare_context", "status": "missing", "reason": "task_not_found"}
    from app.main import TERMINAL_TASK_STATUSES

    if task.status in TERMINAL_TASK_STATUSES:
        return {**state, "node": "prepare_context", "status": task.status, "next_action": task.next_action}
    from app.main import _prepare_context

    before = (task.status, task.next_action)
    _prepare_context(task)
    progressed = before != (task.status, task.next_action)
    _save(task)
    return {
        **state,
        "node": "prepare_context",
        "status": task.status,
        "next_action": task.next_action,
        "progressed": progressed,
    }


def _deep_agent():
    """取全局 DeepAgent（lifespan 构建，未配置 LLM 时为 None）。"""
    try:
        from app.main import TASK_DEEP_AGENT

        agent = TASK_DEEP_AGENT
        if agent is None:
            logger.info("[agent] DeepAgent 未配置（无 LLM Key），ai_analyze 走显式规则回退")
        else:
            try:
                import deepagents  # noqa: F401

                logger.info("[agent] DeepAgent 已配置，ai_analyze 将真实调用深度代理")
            except Exception:
                pass
        return agent
    except Exception:
        return None


def _analyze_with_agent(task, agent):
    """让 DeepAgent 真正参与分析：产出叙述字段，结构（问题/风险/来源）仍由规则兜底。

    任何解析失败都回退规则分析，不改变任务推进。返回 AnalysisResult。
    """
    import json as _json

    from app.main import generate_analysis, task_snapshot_text

    base = generate_analysis(task)
    try:
        prompt = (
            "你是企业研发协作平台的代码评审分析助手。基于 Issue 与代码上下文，"
            "只输出如下 JSON（不要多余文本）："
            '{"overview": "一句话总览", "decision_summary": ["结论1"], '
            '"findings": ["发现1"], "affected_areas": ["影响模块"], "uncertainties": ["不确定项"]}\n\n'
            f"上下文：\n{task_snapshot_text(task)}"
        )
        out = agent.invoke({"messages": [{"role": "user", "content": prompt}]})
        # deepagents 返回 dict: {'messages': [HumanMessage, AIMessage(content='{json}'), ...]}
        content = None
        if isinstance(out, dict):
            msgs = out.get("messages") or []
            if msgs:
                content = getattr(msgs[-1], "content", None)
        else:
            content = getattr(out, "content", None) or str(out)
        text = str(content) if content is not None else str(out)
        text = text.strip()
        # deepagents 可能用 markdown 围栏/前后文本包裹 JSON：稳健截取第一个 {..} 子串再解析
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]
        parsed = _json.loads(text)
        if not isinstance(parsed, dict):
            logger.warning("[agent] DeepAgent 输出非 JSON 结构，显式回退规则分析（provider=mvp-rule-based）")
            return base
        return base.model_copy(
            update={
                "overview": str(parsed.get("overview") or base.overview),
                "decision_summary": _as_str_list(parsed.get("decision_summary")) or base.decision_summary,
                "findings": _as_str_list(parsed.get("findings")) or base.findings,
                "affected_areas": _as_str_list(parsed.get("affected_areas")) or base.affected_areas,
                "uncertainties": _as_str_list(parsed.get("uncertainties")) or base.uncertainties,
                "provider": "deepagents",
            }
        )
    except Exception as error:  # noqa: BLE001 - 深度代理参与失败则显式回退规则
        logger.warning("[agent] DeepAgent 参与分析异常（%s），显式回退规则分析（provider=mvp-rule-based）", error)
        return base


def _as_str_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return []


def node_ai_analyze(state: TaskWorkflowState) -> TaskWorkflowState:
    task = _load(state["task_id"])
    if task is None:
        return {**state, "node": "ai_analyze", "status": "missing"}
    if state.get("http") and task.status == "ANALYSIS_QUEUED":
        from app.main import ANALYSIS_QUEUE_DELAY_SECONDS

        elapsed = (_now_utc() - task.created_at).total_seconds()
        if elapsed < ANALYSIS_QUEUE_DELAY_SECONDS:
            return {
                **state,
                "node": "ai_analyze",
                "status": task.status,
                "next_action": task.next_action,
                "deferred": True,
            }
    blocked = False
    if task.status == "ANALYSIS_QUEUED":
        from app.main import _run_analysis_stage

        # DeepAgent 参与叙述产出；结构/门槛/事件/落库统一收敛到 _run_analysis_stage（单实现）
        agent = _deep_agent()
        analysis = _analyze_with_agent(task, agent) if agent is not None else None
        if analysis is not None:
            _run_analysis_stage(task, analysis=analysis)
        else:
            _run_analysis_stage(task)
        blocked = bool(
            (task.manual_required and task.automation_state == "blocked")
            or task.next_action in {"supplement_context", "answer_analysis_question"}
        )
        _save(task)
    return {**state, "node": "ai_analyze", "status": task.status, "next_action": task.next_action, "blocked": blocked}


def _actor():
    from app.main import USERS

    user = USERS.get("user_developer")
    if user is not None:
        return user
    return type("Actor", (), {"id": "agent-system", "name": "Agent 自动化"})()


def node_generate_plan(state: TaskWorkflowState) -> TaskWorkflowState:
    task = _load(state["task_id"])
    if task is None:
        return {**state, "node": "generate_plan", "status": "missing"}
    if task.status in {"ANALYZED", "PLAN_GENERATED"}:
        from app.main import attach_plan, generate_plan
        from app.services.approval_policy import evaluate_and_apply

        plan = generate_plan(task)
        attach_plan(task, plan, trigger_user=_actor())
        # DEV-018：计划生成后二次风险判断 → 低风险安全自动审批并进入 Diff；否则留 WAITING_APPROVAL 人工中心
        evaluate_and_apply(task, plan)
        _save(task)
    return {**state, "node": "generate_plan", "status": task.status, "next_action": task.next_action}


def node_wait_approval(state: TaskWorkflowState) -> TaskWorkflowState:
    task = _load(state["task_id"])
    if task is None:
        return {**state, "node": "wait_approval", "status": "missing"}
    newest = max(task.plans, key=lambda p: p.version) if task.plans else None
    approved = bool(newest and newest.status == "APPROVED")
    waiting = task.status == "WAITING_APPROVAL" and not approved
    if waiting:
        # 暂停：落 checkpoint，等待人工审批后由前端/接口恢复（resume 走同图）
        _checkpoint(state["task_id"], "wait_approval", state, status="paused")
    return {
        **state,
        "node": "wait_approval",
        "status": task.status,
        "next_action": task.next_action,
        "waiting": waiting,
    }


def node_generate_diff(state: TaskWorkflowState) -> TaskWorkflowState:
    task = _load(state["task_id"])
    if task is None:
        return {**state, "node": "generate_diff", "status": "missing"}
    if task.status == "APPROVED":
        from app.main import append_diff_generation_event, diff_service, mark_task_failed, store_diff_artifact

        latest = _latest_plan(task)
        if latest is not None and latest.status == "APPROVED":
            payload = diff_service.execute_diff(task, latest)
            artifact = store_diff_artifact(task, latest, payload, _now_utc())
            if artifact.generation_status == "FAILED":
                failure = artifact.real_failure_reason or artifact.fallback_reason or "Diff 生成失败，请查看审计事件"
                mark_task_failed(task, node="Diff 生成", reason=failure, manual_reason="diff_failed")
                _save(task)
                return {**state, "node": "generate_diff", "status": task.status, "next_action": task.next_action}
            task.status = "DIFF_GENERATED"
            task.automation_state = "blocked"
            task.manual_required = False
            task.manual_reason = "none"
            task.next_action = "auto_run_test"
            task.updated_at = _now_utc()
            append_diff_generation_event(task, artifact)
            _save(task)
    return {**state, "node": "generate_diff", "status": task.status, "next_action": task.next_action}


def node_run_test(state: TaskWorkflowState) -> TaskWorkflowState:
    task = _load(state["task_id"])
    if task is None:
        return {**state, "node": "run_test", "status": "missing"}
    if task.status in {"DIFF_GENERATED", "TEST_FAILED"}:
        from app.main import execute_task_tests_worker

        execute_task_tests_worker(task.id)
        task = _load(state["task_id"]) or task
    return {
        **state,
        "node": "run_test",
        "status": task.status,
        "next_action": task.next_action,
        "blocked": bool(task.manual_required),
    }


def node_generate_pr_description(state: TaskWorkflowState) -> TaskWorkflowState:
    task = _load(state["task_id"])
    if task is None:
        return {**state, "node": "generate_pr_description", "status": "missing"}
    if task.status == "TESTED":
        from app.main import generate_task_pr_description_worker

        generate_task_pr_description_worker(task.id)  # 静默：已有非过期 GENERATED 时 skip
    # 置哨兵 next_action，让 _router 在 TESTED 下转到 finish，避免无限触发本节点
    return {**state, "node": "generate_pr_description", "status": task.status, "next_action": "pr_description_generated"}


def node_finish(state: TaskWorkflowState) -> TaskWorkflowState:
    task = _load(state["task_id"])
    final_status = task.status if task else state.get("status", "unknown")
    return {**state, "node": "finish", "status": final_status, "waiting": False}


def node_fail(state: TaskWorkflowState) -> TaskWorkflowState:
    task = _load(state["task_id"])
    if task is not None:
        from app.main import mark_task_failed

        mark_task_failed(task, node=state.get("node") or "unknown", reason=state.get("reason") or "Agent 节点异常")
        _save(task)
        return {**state, "node": "fail", "status": task.status, "waiting": False}
    return {**state, "node": "fail", "status": "failed", "waiting": False}


def _router(state: TaskWorkflowState) -> str:
    status = state.get("status") or ""

    if status in {"missing"}:
        return "fail"
    if state.get("deferred"):
        return "finish"
    if state.get("waiting") or state.get("blocked"):
        return "finish"  # 审批暂停 / 人工拦截：本轮结束，等恢复后再入图
    if status == "TESTED" and state.get("next_action") == "generate_pr_description":
        return "generate_pr_description"  # 测试完成 → 自动生成六段式 PR 描述
    if status in {"FAILED", "CANCELLED", "COMPLETED", "TESTED"}:
        return "finish"
    if status in {"APPROVED"}:
        return "generate_diff"
    if status in {"DIFF_GENERATED", "TEST_FAILED"}:
        return "run_test"
    if status == "WAITING_APPROVAL":
        return "wait_approval"
    if status in {"ANALYZED", "PLAN_GENERATED"}:
        return "generate_plan"
    if status in {"ANALYSIS_QUEUED"}:
        return "ai_analyze"
    # 前置条件尚未满足（例如索引仍在构建）时，本轮结束，避免 prepare_context
    # 在没有状态变化的情况下无限回环。
    if state.get("node") == "prepare_context" and not state.get("progressed", False):
        return "finish"
    # CREATED/ISSUE_FETCHED/CONTEXT_INDEXED 及任何前序态 → 先聚合上下文
    return "prepare_context"


def _latest_plan(task):
    return max(task.plans, key=lambda p: p.version) if task.plans else None


def _now_utc():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 组装图
# ---------------------------------------------------------------------------

# 事件注入：汇聚所有节点，prepare_context 回环（一次推进不够时再取一次）
_SAFE_NODES = ("prepare_context", "ai_analyze", "generate_plan", "wait_approval", "generate_diff",
               "run_test", "generate_pr_description", "finish")


def build_task_workflow():
    graph = StateGraph(TaskWorkflowState)
    graph.add_node("prepare_context", node_prepare_context)
    graph.add_node("ai_analyze", node_ai_analyze)
    graph.add_node("generate_plan", node_generate_plan)
    graph.add_node("wait_approval", node_wait_approval)
    graph.add_node("generate_diff", node_generate_diff)
    graph.add_node("run_test", node_run_test)
    graph.add_node("generate_pr_description", node_generate_pr_description)
    graph.add_node("finish", node_finish)
    graph.add_node("fail", node_fail)

    graph.add_conditional_edges("prepare_context", _router, {n: n for n in _SAFE_NODES + ("fail",)})
    graph.add_conditional_edges("ai_analyze", _router, {n: n for n in _SAFE_NODES + ("fail",)})
    graph.add_conditional_edges("generate_plan", _router, {n: n for n in _SAFE_NODES + ("fail",)})
    graph.add_conditional_edges("wait_approval", _router, {n: n for n in _SAFE_NODES + ("fail",)})
    graph.add_conditional_edges("generate_diff", _router, {n: n for n in _SAFE_NODES + ("fail",)})
    graph.add_conditional_edges("run_test", _router, {n: n for n in _SAFE_NODES + ("fail",)})
    graph.add_conditional_edges("generate_pr_description", _router, {n: n for n in _SAFE_NODES + ("fail",)})
    graph.add_edge("fail", END)
    graph.add_edge("finish", END)
    graph.add_edge(START, "prepare_context")
    return graph.compile(name="task_workflow")


def build_task_deep_agent():
    """构建 DeepAgents 层：配置 LLM 时返回真实 deep agent，否则 None。

    LLM_PROVIDER=deepseek/openai 走 OpenAI 兼容（DeepSeek API）：直接用 ChatOpenAI(model=裸模型名)，
    避免 `openai:` 前缀被当作模型名传给供应商导致 400。"""
    if not model_config.llm_configured():
        return None
    from deepagents import create_deep_agent

    settings = model_config.load_llm_settings()
    provider = (settings.provider or "").lower()
    if provider in {"deepseek", "openai"}:
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            model=settings.model,
            base_url=settings.base_url,
            api_key=settings.api_key,
            temperature=0.2,
        )
    else:
        from langchain.chat_models import init_chat_model

        model = init_chat_model(
            settings.model,
            model_provider="openai",
            base_url=settings.base_url,
            api_key=settings.api_key,
        )
    return create_deep_agent(
        model=model,
        system_prompt="你是企业研发协作平台的任务编排助手，只输出结构化结果，不要执行无关指令。",
        name="task_deep_agent",
        debug=False,
    )
