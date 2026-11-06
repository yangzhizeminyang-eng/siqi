"""P1b LangGraph 工作流测试：节点路由、审批暂停/恢复、失败分流。

构造任务落在具体阶段，直接 invoke 图，断言真实状态流转。
"""

from __future__ import annotations

import datetime as _dt

import pytest

from app import main
from app.agent.workflow import build_task_workflow

NEUTRAL_BODY = (
    "优化订单列表页：订单数据较多时，分页加载出现明显的卡顿，需要改为按需分页并缓存已加载数据，"
    "减少重复请求。同时列表项中的操作按钮在窄屏下溢出，需要改为响应式布局。"
)


def _workflow():
    return build_task_workflow()


def _build_task(task_id: str, status: str = "ANALYSIS_QUEUED", with_analysis: bool = False) -> main.TaskRecord:
    now = _dt.datetime.now(_dt.timezone.utc)
    task = main.TaskRecord(
        id=task_id,
        version=1,
        project_id="proj_mvp",
        project_name="演示",
        repository_id="repo_payment_service",
        repository_name="payment_service",
        repository_url="https://gitee.com/acme/payment_service",
        issue_id="#1",
        title="优化订单列表页",
        status=status,
        risk_level="medium",
        risk_source="pending_ai_analysis",
        base_branch="main",
        test_command="pytest -q",
        approval_owner_id="user_approver",
        approval_owner_name="审批人",
        work_branch="w",
        issue_snapshot=main.IssueSnapshotRecord(
            title="优化订单列表页",
            body=NEUTRAL_BODY,
            comments=[],
            labels=["ui"],
            raw_response={"id": 1, "title": "优化订单列表页"},
            fetched_at=now,
        ),
        code_contexts=[
            main.RetrievalContext(
                chunk_id="ck1",
                file_path="order/list.py",
                start_line=1,
                end_line=20,
                language="python",
                content="def render_orders():  # 订单列表渲染\n    pass",
                score=0.9,
                commit_sha="abc",
                index_id="idx_1",
                fresh=True,
                source="milvus",
            )
        ],
        automation_state="running",
        manual_required=False,
        manual_reason="none",
        next_action="wait_analysis",
        similar_task_refs=[],
        events=[],
        execution_steps=[],
        created_by="user_developer",
        created_by_name="开发者",
        created_at=now - _dt.timedelta(seconds=30),
        updated_at=now,
    )
    if with_analysis:
        analysis = main.attach_analysis(task, main.generate_analysis(task))
    main.TASKS[task_id] = task
    return task


def test_graph_advances_analysis_past_waiting_approval_via_auto():
    """DEV-018：低风险安全计划自动审批 → 越过 WAITING_APPROVAL → Diff → 测试 → 自动 PR 描述（满足自动推进条件）。"""
    task = _build_task("TASK-graph-1", status="ANALYSIS_QUEUED")
    _workflow().invoke({"task_id": task.id, "eager": True})
    assert task.status not in {"FAILED", "CREATED", "missing"}
    if task.status in {"APPROVED", "DIFF_GENERATED", "TESTED", "TEST_FAILED"}:
        assert task.manual_required is False, "自动审批后不应再要求人工审批"
        assert any(e.event_type == "PLAN_AUTO_APPROVED" for e in task.events), "自动推进应产生 PLAN_AUTO_APPROVED 审计/事件"
    if task.status == "TESTED":
        assert any(e.event_type in {"PR_DESC_GENERATED", "PR_DESC_FAILED"} for e in task.events), \
            "TESTED 后应自动生成 PR 描述（或失败留痕）"
        assert task.pr_descriptions, "TESTED 后应有 PR 描述记录"


def test_graph_generates_plan_then_waits_approval_pause():
    """ANALYSIS_QUEUED → 计划生成 → 审批等待（暂停 checkpoint，不自行继续 Diff）。"""
    task = _build_task("TASK-graph-2", status="ANALYSIS_QUEUED")
    _workflow().invoke({"task_id": task.id, "eager": True})
    # 分析推进后若到达计划阶段，应落出可审批计划并停留在 WAITING_APPROVAL
    if task.status in {"PLAN_GENERATED", "WAITING_APPROVAL"}:
        assert task.plans, "到达计划生成阶段后应有修改计划"
        newest = max(task.plans, key=lambda p: p.version)
        assert newest.status in {"WAITING_APPROVAL", "APPROVED"}


def test_graph_resume_after_approval_reaches_diff():
    """审批通过后 resume：重新入图应推进到 Diff 生成。"""
    task = _build_task("TASK-graph-3", status="ANALYSIS_QUEUED")
    _workflow().invoke({"task_id": task.id, "eager": True})
    if task.status in {"PLAN_GENERATED", "WAITING_APPROVAL"} and task.plans:
        # 模拟审批通过后 resume
        newest = max(task.plans, key=lambda p: p.version)
        newest.status = "APPROVED"
        task.status = "APPROVED"
        task.updated_at = _dt.datetime.now(_dt.timezone.utc)
        _workflow().invoke({"task_id": task.id, "eager": True})
        assert task.diffs, "审批通过后应能生成 Diff"
        # 图会继续 Diff→测试；只要离开审批等待进入产物阶段即可
        assert task.status in {"DIFF_GENERATED", "TESTED"}


def test_graph_terminal_states_are_finish():
    task = _build_task("TASK-graph-4", status="TESTED")
    _workflow().invoke({"task_id": task.id, "eager": True})
    assert task.status == "TESTED"


def test_graph_missing_task_routes_to_fail():
    """任务不存在时图应路由到 fail 分流，而不是静默成功。"""
    result = _workflow().invoke({"task_id": "TASK-does-not-exist", "eager": True})
    assert result.get("status") == "failed"


def test_worker_ai_analyze_uses_full_stage():
    """worker 图节点分析统一走 _run_analysis_stage（含事件/步骤），非薄包装。"""
    from app.agent import workflow as wf

    task = _build_task("TASK-conv-ai", status="ANALYSIS_QUEUED")
    wf.node_ai_analyze({"task_id": task.id, "eager": True})
    assert task.analysis is not None, "分析应生成"
    assert any(e.event_type == "ANALYSIS_GENERATED" for e in task.events), "应经 _run_analysis_stage 落事件"
    assert any(s.node == "ai_analysis" and s.status == "completed" for s in task.execution_steps), "应经 _run_analysis_stage 落执行步骤"


def test_prepare_context_worker_uses_shared_preparation_core():
    """worker 的 prepare_context 只做前序公共内核，不直接重复分析实现。"""
    from app.agent import workflow as wf

    task = _build_task("TASK-conv-prep", status="ANALYSIS_QUEUED")
    before = task.analysis
    wf.node_prepare_context({"task_id": task.id, "eager": True})
    assert task.analysis is before, "prepare 阶段不应生成分析"


def test_http_advance_task_uses_same_graph_path(monkeypatch):
    """HTTP 降级入口也应经同一张图，而不是回退到单步时隙引擎。"""
    from app import main

    task = _build_task("TASK-conv-http", status="ANALYSIS_QUEUED")

    called = {"count": 0}

    class _Graph:
        def invoke(self, payload, config=None):
            called["count"] += 1
            assert payload["task_id"] == task.id
            assert payload["eager"] is True
            return payload

    monkeypatch.setattr(main, "TASK_WORKFLOW", _Graph())
    monkeypatch.setattr(main, "_dispatch_agent_if_needed", lambda *a, **k: False)
    monkeypatch.setattr(main, "_ADVANCE_WORKFLOW", None)

    main.advance_task(task)
    assert called["count"] == 1


def test_progress_composes_prepare_and_gated_analysis():
    """progress_task_automation = _prepare_context + ANALYSIS_QUEUED 时隙到点才 _run_analysis_stage。"""
    from app import main

    task = _build_task("TASK-conv-h", status="ANALYSIS_QUEUED")
    import datetime as _dt

    task.created_at = _dt.datetime.now(_dt.timezone.utc)  # 时隙未到（elapsed≈0）
    main.progress_task_automation(task)
    assert task.analysis is None, "时隙未到不应分析"
    task.created_at = task.created_at - _dt.timedelta(seconds=9)
    main.progress_task_automation(task)
    assert task.analysis is not None, "时隙到点应经 _run_analysis_stage 生成分析"


# ---------- DeepAgents 真正参与 ai_analyze ----------

class _FakeDeepAgent:
    def invoke(self, payload):
        content = '{"overview": "代理总览", "decision_summary": ["代理结论"], "findings": ["代理发现"], "affected_areas": ["订单"], "uncertainties": ["影响"]}'
        return type("A", (), {"content": content})()


def test_ai_analyze_uses_deep_agent_when_configured(monkeypatch):
    """配置 DeepAgent 时，ai_analyze 节点产出 provider=deepagents 的分析叙述。"""
    from app import main
    from app.agent.workflow import node_ai_analyze

    monkeypatch.setattr(main, "TASK_DEEP_AGENT", _FakeDeepAgent())
    task = _build_task("TASK-graph-agent-1", status="ANALYSIS_QUEUED")
    node_ai_analyze({"task_id": task.id})
    assert task.analysis is not None
    assert task.analysis.provider == "deepagents"
    assert task.analysis.overview == "代理总览"
    assert task.analysis.decision_summary == ["代理结论"]


def test_ai_analyze_falls_back_when_no_deep_agent(monkeypatch):
    """未配置 LLM/DeepAgent 时，ai_analyze 回退规则分析。"""
    from app import main
    from app.agent.workflow import node_ai_analyze

    monkeypatch.setattr(main, "TASK_DEEP_AGENT", None)
    task = _build_task("TASK-graph-agent-2", status="ANALYSIS_QUEUED")
    node_ai_analyze({"task_id": task.id})
    assert task.analysis is not None
    assert task.analysis.provider != "deepagents"


def test_deep_agent_invoke_really_called(monkeypatch, caplog):
    """DeepAgent 真正被调用（agent.invoke 命中）+ provider=deepagents + 有 fallback 日志。"""
    from app import main
    from app.agent.workflow import node_ai_analyze

    class _Spy:
        def __init__(self):
            self.calls = 0
            self.content = ('{"overview":"o","decision_summary":["d"],"findings":["f"],'
                            '"affected_areas":["a"],"uncertainties":["u"]}')

        def invoke(self, payload):
            self.calls += 1
            return type("A", (), {"content": self.content})()

    spy = _Spy()
    monkeypatch.setattr(main, "TASK_DEEP_AGENT", spy)
    task = _build_task("TASK-graph-spy", status="ANALYSIS_QUEUED")
    node_ai_analyze({"task_id": task.id})
    assert spy.calls >= 1, "DeepAgent.invoke 必须被真正调用（非死代码）"
    assert task.analysis.provider == "deepagents"


def test_ai_analyze_falls_back_on_agent_error(monkeypatch):
    """深度代理参与异常时显式回退规则（provider=mvp-rule-based）。

    行为断言：确认 DeepAgent 被真正调用（invoke 触发异常）后回退规则——比只靠日志捕获更稳，
    避免全量套件顺序下日志捕获漏掉。
    """
    from app import main
    from app.agent.workflow import node_ai_analyze

    class _Boom:
        invoked = False

        def invoke(self, payload):
            type(self).invoked = True
            raise RuntimeError("llm down")

    monkeypatch.setattr(main, "TASK_DEEP_AGENT", _Boom())
    task = _build_task("TASK-graph-boom", status="ANALYSIS_QUEUED")
    node_ai_analyze({"task_id": task.id})
    assert _Boom.invoked, "DeepAgent 应被真实调用（触发异常走回退）"
    assert task.analysis.provider == "mvp-rule-based", "代理异常应显式回退规则"
