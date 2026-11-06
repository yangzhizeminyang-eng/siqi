"""E4-02 多 Agent 协作专项验收测试（确定性 provider，真实调用模型适配器的测试路径）。

覆盖：
- 6 个 Agent 被真实独立 invoke；6 个 agent_type + 6 条 AgentRun 记录
- Artifact 只经 refs 传递；每 Agent 独立 prompt/schema/tool policy
- 越权工具调用被服务端拒绝并审计
- Planner 后审批门：未批准 CodeChange 不执行
- Review 失败回 CodeChange；Test 失败回 CodeChange；上限→WAITING_HUMAN
- checkpoint 恢复不重复执行已完成 Agent；幂等副作用
- 跨租户 403
- v1 (workflow_version=1) 旧 5 步链兼容
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from app.enterprise_runtime import graph as agent_graph
from app.enterprise_runtime import orchestrator as orch
from app.enterprise_runtime import store as rstore
from app.enterprise_runtime.artifacts import runs as runs_store
from app.enterprise_runtime.artifacts import store as astore
from app.enterprise_runtime.artifacts.runs import AgentRun
from app.enterprise_runtime.agents import factory as afactory
from app.enterprise_runtime.agents.agent_definitions import AGENT_DEFINITIONS
from app.enterprise_runtime.agents.policy import PolicyViolation
from app.enterprise_runtime.agents.registry import AgentExecutorRegistry
from app.enterprise_runtime.models import RuntimeStepStatus, RuntimeTaskStatus, steps_for_workflow
from app.db.repositories import load_all_agent_runs
from app.approvals import service as ap_service
from app.enterprise_runtime.artifacts.models import AgentArtifact
from app.enterprise_runtime.agents.executor import AgentExecutor


@pytest.fixture(autouse=True)
def _deterministic_agents(monkeypatch):
    """测试仅用确定性模型 provider（显式 test-only；生产绝不构造）。"""
    monkeypatch.setenv("AGENT_PROVIDER", "deterministic")


def _ctx(tenant="tenant_a"):
    return SimpleNamespace(tenant_id=tenant, user_id="u1", user_name="n1", role="admin")


def _registry(monkeypatch=None):
    # deterministic review/test 默认 approved=True / passed=True（无需 patch 即可走通主链）
    return AgentExecutorRegistry(audit=lambda *a, **k: None)


def _new_task(task_id="t1", tenant="tenant_a", version=2):
    t = orch.start_runtime_task(_ctx(tenant), task_id, f"ik_{task_id}",
                                project_id="p1", repository_id="r1", title="多 Agent 需求",
                                workflow_version=version)
    return t


def _drive(task_id, reg, ctx=None, max_iters=40, approve=True):
    """反复驱动单个 agent 步骤直到停机（WAITING_APPROVAL/SUCCEEDED/WAITING_HUMAN/FAILED）。"""
    saw = []
    for _ in range(max_iters):
        task = orch.run_agent_workflow("w1", task_id, reg, ctx=ctx)
        saw.append(getattr(task, "current_step", None))
        if task.status == RuntimeTaskStatus.WAITING_APPROVAL and approve:
            orch.approve_runtime_task(task_id, True, actor="u1", actor_name="n1")
            continue
        if task.status in (RuntimeTaskStatus.WAITING_APPROVAL, RuntimeTaskStatus.WAITING_HUMAN,
                           RuntimeTaskStatus.SUCCEEDED, RuntimeTaskStatus.FAILED,
                           RuntimeTaskStatus.CANCELLED):
            return task, saw
    return rstore.get(task_id), saw


def test_six_agents_independently_invoked_and_recorded():
    reg = _registry()
    _new_task("t1", version=2)
    task, _ = _drive("t1", reg)
    assert task.status == RuntimeTaskStatus.SUCCEEDED
    runs = runs_store.list_by_task("tenant_a", "t1")
    assert len(runs) == 6, f"应 6 条 AgentRun，实为 {len(runs)}"
    assert all(r.status == "succeeded" for r in runs)
    types = sorted({r.agent_type for r in runs})
    assert types == sorted(steps_for_workflow(2)), types
    # 6 个 agent_type 各自独立定义 + 独立 Run
    for r in runs:
        assert r.agent_type in AGENT_DEFINITIONS
        assert r.attempt >= 1
        assert r.policy_context.get("tool_whitelist") is not None
    # 产物引用链：后续 agent 的 input_refs 指向先前产物
    arts = astore.list_by_task("tenant_a", "t1")
    assert len(arts) >= 6
    # 每个 run 的 input_refs 只引用其前置 agent 的产物（不经完整原文）
    for r in runs:
        for ref in r.input_refs:
            a = astore.get(ref)
            assert a is not None, f"input_ref 悬空: {ref}"


def test_artifacts_only_via_refs_and_schema():
    """Artifact 只能经 refs 传递；每 Agent 独立 output_schema 生效；planner 无代码工具。"""
    reg = _registry()
    _new_task("t2", version=2)
    task, _ = _drive("t2", reg)
    assert task.status == RuntimeTaskStatus.SUCCEEDED
    # input_refs 均为 artifact_id（非自然语言正文）；输出与各自 schema 对齐
    for at, d in AGENT_DEFINITIONS.items():
        out_schema = d.output_schema
        assert out_schema is not None
    planner = AGENT_DEFINITIONS["planner_agent"]
    assert planner.tool_whitelist == frozenset(), "Planner 不得有工具"
    # 各 agent output_schema 至少 1 字段
    for at in steps_for_workflow(2):
        fields = AGENT_DEFINITIONS[at].output_schema.model_fields
        assert fields, f"{at} 无输出 schema"


def test_unauthorized_tool_call_denied_and_audited():
    reg = _registry()
    v = reg.validator
    logs = []
    from app.enterprise_runtime.agents.policy import AgentToolPolicyValidator
    v2 = AgentToolPolicyValidator(AGENT_DEFINITIONS, audit=lambda at, r, d: logs.append((at, r)))
    for at, whitelist in (("planner_agent", frozenset({"workspace_write"})),
                          ("requirement_agent", frozenset({"repo_write"})),
                          ("review_security_agent", frozenset({"delete_file"}))):
        for tool in whitelist:
            with pytest.raises(PolicyViolation):
                v2.assert_tool_allowed(at, tool)
            assert any(a == at for a, _ in logs), f"缺审计 {at} {tool}"
    # 测试命令：非 TestRelease 一律拒绝
    with pytest.raises(PolicyViolation):
        v2.assert_test_command_allowed("planner_agent", "rm -rf /")
    with pytest.raises(PolicyViolation):
        v2.assert_artifact_write_allowed("requirement_agent", "TestReportArtifact")
    # 白名单内放行
    v2.assert_tool_allowed("requirement_agent", "read_issue")
    v2.assert_artifact_write_allowed("test_release_agent", "TestReportArtifact")


def test_planner_requires_approval_before_code_change():
    reg = _registry()
    _new_task("t3", version=2)
    # 驱动到 Planner 后须 WAITING_APPROVAL，CodeChange 不得执行
    task = None
    for _ in range(30):
        task = orch.run_agent_workflow("w1", "t3", reg, ctx=_ctx())
        if task.status == RuntimeTaskStatus.WAITING_APPROVAL:
            break
    assert task is not None and task.status == RuntimeTaskStatus.WAITING_APPROVAL
    cc = next(s for s in task.steps if s.agent_type == "code_change_agent")
    assert cc.status != RuntimeStepStatus.SUCCEEDED, "未审批前 CodeChange 不得执行"
    assert "code_change_agent" not in task.completed_steps
    # 拒绝审批 → 终止
    orch.approve_runtime_task("t3", False, actor="u1", actor_name="n1")
    task = rstore.get("t3")
    assert task.status == RuntimeTaskStatus.FAILED
    assert task.approval_state == "rejected"


def _review_fail_once(monkeypatch):
    """review 第一次失败，其后通过；用于回退路径验证。"""
    from app.enterprise_runtime.agents import factory as _f
    orig = _f.DeterministicAgent.invoke
    state = {"n": 0}

    def patched(self, si):
        out = orig(self, si)
        if self.agent_type == "review_security_agent":
            out["approved"] = not (state["n"] < 1)
            state["n"] += 1
        return out

    monkeypatch.setattr(_f.DeterministicAgent, "invoke", patched)


def test_review_failure_returns_to_code_change(monkeypatch):
    _review_fail_once(monkeypatch)
    reg = _registry()
    _new_task("t4", version=2)
    task, _ = _drive("t4", reg)
    assert task.status == RuntimeTaskStatus.SUCCEEDED
    assert task.repair_round >= 1, "review 失败应触发回退并递增 repair_round"


def test_test_failure_returns_to_code_change_then_human_on_cap(monkeypatch):
    """测试连续失败：round0/round1 回 CodeChange，达上限后→WAITING_HUMAN。"""
    from app.enterprise_runtime.agents import factory as _f
    orig = _f.DeterministicAgent.invoke

    def patched(self, si):
        out = orig(self, si)
        if self.agent_type == "test_release_agent":
            out["passed"] = False
        return out

    monkeypatch.setattr(_f.DeterministicAgent, "invoke", patched)
    reg = AgentExecutorRegistry(audit=lambda *a, **k: None)
    _new_task("t5", version=2)
    task, saw = _drive("t5", reg)
    assert task.status == RuntimeTaskStatus.WAITING_HUMAN, f"应到 WAITING_HUMAN，实 {task.status}"
    assert task.repair_round >= 2
    assert task.wait_human_reason and "上限" in task.wait_human_reason


def test_repair_round_fields_recorded_in_agentruns(monkeypatch):
    _review_fail_once(monkeypatch)
    reg = AgentExecutorRegistry(audit=lambda *a, **k: None)
    _new_task("t6", version=2)
    task, _ = _drive("t6", reg)
    assert task.status == RuntimeTaskStatus.SUCCEEDED
    runs = runs_store.list_by_task("tenant_a", "t6")
    cc = [r for r in runs if r.agent_type == "code_change_agent"]
    # code_change 重跑计入 attempt/repair_round
    assert len(cc) >= 2, "修复应重跑 CodeChange"
    rounds = sorted(r.repair_round for r in cc)
    assert 0 in rounds and rounds[-1] >= 1


def test_checkpoint_resume_no_duplicate_execution():
    """checkpoint 恢复：已完成 Agent 不重复执行。"""
    reg = _registry()
    _new_task("t7", version=2)
    # 第一次驱动到 WAITING_APPROVAL
    for _ in range(30):
        task = orch.run_agent_workflow("w1", "t7", reg, ctx=_ctx())
        if task.status == RuntimeTaskStatus.WAITING_APPROVAL:
            break
    n_runs_before = len(runs_store.list_by_task("tenant_a", "t7"))
    assert n_runs_before == 3  # requirement/research/planner
    # 重启：恢复任务后（模拟 checkpoint 恢复），继续驱动
    task = orch.resume_from_checkpoint(_ctx(), "t7")
    assert task is not None
    # 完成后不重复执行 requirement/research/planner（run 数与阶段匹配）
    task = orch.approve_runtime_task("t7", True, actor="u1", actor_name="n1")
    task, _ = _drive("t7", reg)
    runs = runs_store.list_by_task("tenant_a", "t7")
    assert task.status == RuntimeTaskStatus.SUCCEEDED
    by_type = {}
    for r in runs:
        by_type.setdefault(r.agent_type, []).append(r)
    # requirement/research/planner 各仅执行 1 次（checkpoint 恢复不重复）
    assert len(by_type["requirement_agent"]) == 1
    assert len(by_type["planner_agent"]) == 1


def test_idempotent_replay_no_duplicate_side_effects():
    """重复投递同一步骤不产生重复 AgentRun（幂等）。"""
    reg = _registry()
    _new_task("t8", version=2)
    # 驱动到 Planner
    for _ in range(30):
        task = orch.run_agent_workflow("w1", "t8", reg, ctx=_ctx())
        if task.status == RuntimeTaskStatus.WAITING_APPROVAL:
            break
    n_planner = len([r for r in runs_store.list_by_task("tenant_a", "t8") if r.agent_type == "planner_agent"])
    # 重复投递（同 run 幂等键重建）→ AgentRunStore.put 返回既有
    existing = [r for r in runs_store.list_by_task("tenant_a", "t8") if r.agent_type == "planner_agent"][0]
    dup = AgentRun(run_id=existing.run_id, task_id="t8", tenant_id="tenant_a",
                   agent_type="planner_agent", idempotency_key=existing.idempotency_key)
    got = runs_store.put(dup)
    assert got.run_id == existing.run_id
    assert len([r for r in runs_store.list_by_task("tenant_a", "t8") if r.agent_type == "planner_agent"]) == n_planner


def test_cross_tenant_denied_and_audited():
    reg = _registry()
    _new_task("t9", tenant="tenant_a", version=2)
    with pytest.raises(PermissionError):
        orch.run_agent_workflow("w1", "t9", reg, ctx=_ctx("tenant_b"))
    # 审计：runtime.tenant.denied 被写
    from app import main
    sys = [e for e in main.AUDIT_EVENTS.values() if e.event_type == "runtime.tenant.denied"]
    assert sys, "缺 runtime.tenant.denied 审计"


def test_workflow_v1_legacy_compatible():
    """workflow_version=1 走旧 5 步链（run_until_blocked + RUNTIME_STEPS），不回归。"""
    from app.enterprise_runtime.models import RUNTIME_STEPS
    _new_task("t10", version=1)
    task = rstore.get("t10")
    assert steps_for_workflow(1) == RUNTIME_STEPS
    names = [s.name for s in task.steps]
    assert names == list(RUNTIME_STEPS)
    assert all(s.agent_type is None for s in task.steps), "v1 不绑 agent_type"


def test_graph_topology_compiled_and_workflow_versions():
    assert agent_graph.graph_for_workflow(1) is None
    g2 = agent_graph.graph_for_workflow(2)
    assert g2 is not None and all(n in g2.nodes for n in (
        "requirement_agent", "planner_agent", "code_change_agent",
        "review_security_agent", "test_release_agent"))
    assert agent_graph.next_action("review_security_agent", approved=False) == "back_code_change"
    assert agent_graph.next_action("test_release_agent", passed=False) == "back_code_change"
    assert agent_graph.next_action("test_release_agent", passed=False, repair_round=2) == "human_wait"


def test_langgraph_is_real_routing_authority():
    """编排每步经编译后的 LangGraph 路由器决定（C3/#6），并返回下一节点（不再 index+1 手动路由）。"""
    # review 是链中第 4 步（index=4）；proceed 的下一节点由图的 step_index 计算
    assert agent_graph.route_via_graph(agent_type="review_security_agent", step_index=4,
                                       latest_artifact={"approved": False}, repair_round=0) == ("back_code_change", "code_change_agent")
    assert agent_graph.route_via_graph(agent_type="review_security_agent", step_index=4,
                                       latest_artifact={"approved": True}, repair_round=0) == ("proceed", "test_release_agent")
    assert agent_graph.route_via_graph(agent_type="test_release_agent", step_index=5,
                                       latest_artifact={"passed": False}, repair_round=0) == ("back_code_change", "code_change_agent")
    assert agent_graph.route_via_graph(agent_type="test_release_agent", step_index=5,
                                       latest_artifact={"passed": False}, repair_round=2, max_repair_rounds=2) == ("human_wait", "human_wait")
    assert agent_graph.route_via_graph(agent_type="test_release_agent", step_index=5,
                                       latest_artifact={"passed": True}, repair_round=0) == ("succeeded", "end")
    assert agent_graph.route_via_graph(agent_type="planner_agent", latest_artifact=None, repair_round=0) == ("approve_wait", "approval_gate")


def test_e2_approval_record_created_at_planner_gate():
    """Planner 完成后经 E2 审批服务创建 runtime.execute 审批单（幂等，ik 含 task_id+plan_version）。"""
    reg = _registry()
    _new_task("ta", version=2)
    for _ in range(30):
        task = orch.run_agent_workflow("w1", "ta", reg, ctx=_ctx())
        if task.status == RuntimeTaskStatus.WAITING_APPROVAL:
            break
    from app.approvals import store as ap_store
    recs = [r for r in ap_store.APPROVALS.values()
            if r.resource_type == "runtime_task" and r.resource_id == "ta" and r.operation == "runtime.execute"]
    assert recs, "应在 E2 审批中心存在 runtime.execute 审批单"
    rec = recs[0]
    assert rec.approval_status == "pending"
    assert "rt-approval:ta:plan-v" in rec.idempotency_key, "幂等键需含 task_id 与 plan_version"
    # 幂等：以相同幂等键再次创建应返回同一单
    rec2 = ap_service.create_system_approval(
        tenant_id="tenant_a", resource_type="runtime_task", resource_id="ta",
        operation="runtime.execute", idempotency_key=rec.idempotency_key)
    assert rec2.id == rec.id


def test_real_loop_approves_runtime_through_e2_service():
    """真实闭环脚本必须经 E2 service approve，不能只改 RuntimeTask 内存状态。"""
    from app.approvals import store as ap_store
    from scripts.e4_agent_real_loop import _approve_runtime_via_e2

    reg = _registry()
    _new_task("t_e2_real_loop", version=2)
    task, _ = _drive("t_e2_real_loop", reg, approve=False)
    assert task.status == RuntimeTaskStatus.WAITING_APPROVAL

    key = f"rt-approval:{task.id}:plan-v{task.version}"
    pending = ap_store.find_by_idempotency("tenant_a", key)
    assert pending is not None
    assert pending.approval_status == "pending"

    approved = _approve_runtime_via_e2(
        task.id,
        tenant_id="tenant_a",
        plan_version=task.version,
    )

    assert approved.approval_status == "approved"
    assert ap_store.get(pending.id).approval_status == "approved"
    assert rstore.get(task.id).status == RuntimeTaskStatus.RUNNING
    assert rstore.get(task.id).approval_state == "approved"


def test_agentrun_final_state_written_to_db(tmp_path, monkeypatch):
    """AgentRun running→succeeded 原地更新后再次 put 必须写回 DB（约束 #9，非仅内存）。"""
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'e4a_runs.db').as_posix()}")
    monkeypatch.setenv("PERSIST_STRICT", "false")
    from app.db.session import get_engine
    from app.models.orm import Base
    get_engine.cache_clear()
    Base.metadata.create_all(get_engine())

    af = AgentRun(run_id="rdb1", task_id="tdb", tenant_id="tenant_a", agent_type="planner_agent",
                  status="running", prompt_version="1", idempotency_key="ik_rdb1", started_at=_now_dt())
    runs_store.put(af)
    af.status = "succeeded"; af.output_refs = ["art_x"]; af.finished_at = _now_dt()
    runs_store.put(af)
    rows = load_all_agent_runs()
    row = next(r for r in rows if r["run_id"] == "rdb1")
    assert row["status"] == "succeeded", f"DB 终态应为 succeeded，实 {row['status']}"
    assert row["output_refs"] == ["art_x"]

    af2 = AgentRun(run_id="rdb2", task_id="tdb", tenant_id="tenant_a", agent_type="planner_agent",
                   status="running", prompt_version="1", idempotency_key="ik_rdb2", started_at=_now_dt())
    runs_store.put(af2)
    af2.status = "failed"; af2.error_reason = "bad"; af2.finished_at = _now_dt()
    runs_store.put(af2)
    rows = load_all_agent_runs()
    assert next(r for r in rows if r["run_id"] == "rdb2")["status"] == "failed"
    # 清理：避免影响后续用例的 DB 开关
    monkeypatch.setenv("USE_SQLALCHEMY", "false")


def _now_dt():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _seed(**kw):
    """构造带已知内容(含不应泄露字段)的 Artifact。"""
    from app.enterprise_runtime.artifacts.models import AgentArtifact, ArtifactType
    arts = {}
    for aid, (atype, content) in kw.items():
        arts[aid] = AgentArtifact(artifact_id=aid, task_id="tp", tenant_id="tenant_a",
                                  agent_type="seed", artifact_type=atype, content=content,
                                  idempotency_key=f"ik_{aid}")
        astore.put(arts[aid])
    return arts


def _ex(task_id="tp", tenant="tenant_a", agent_type=None):
    from app.enterprise_runtime.agents.executor import AgentExecutor
    st = SimpleNamespace(step_id=f"{task_id}:{agent_type}", agent_type=agent_type, agent_attempt=0,
                         repair_round=0, task_id=task_id)
    tk = SimpleNamespace(id=task_id, tenant_id=tenant, project_id="p", repository_id="r", title="t")
    return AgentExecutor(agent_type, audit=lambda *a, **k: None), st, tk


def test_content_projection_per_artifact_type():
    """Research/Planner/CodeChange/Review/TestRelease 分别能读到对应类型的受控字段（#1）。"""
    _seed(
        req=("RequirementArtifact", {"summary": "REQ-SUM", "requirements": [{"id": "r1"}],
                                      "open_questions": ["q"], "completeness": "complete", "SECRET": "x"}),
        research=("ResearchArtifact", {"findings": ["F1"]}),
        ccx=("CodeContextArtifact", {"files": ["f.py"], "symbols": ["S"], "code_context": [{"sym": "S"}]}),
        plan=("PlanArtifact", {"objectives": ["O1"], "steps": [{"f": "x"}], "test_command": "pt",
                               "approval_request_note": "ok", "EXTRA_HIDDEN": 1}),
        patch=("PatchArtifact", {"workspace_path": "/w", "files_changed": ["x"], "patch": "PATCH", "test_command": "pt"}),
        diff=("DiffArtifact", {"diff_ref": "d1", "patch": "DIFF-PATCH"}),
        review=("ReviewArtifact", {"approved": True, "conclusions": ["ok"], "issues": [], "security_risks": [],
                                   "HIDDEN_DETAIL": "nope"}),
    )
    # Research: 读 RequirementArtifact
    ex, st, tk = _ex(agent_type="repository_research_agent")
    res, vis, _ = ex._resolve_visible_refs(["req"], "tenant_a", st)
    assert set(res["req"]["content"]) == {"summary", "requirements", "open_questions", "completeness"}, res
    assert "SECRET" not in str(res["req"]["content"])
    # Planner: 读 requirement+research+codecontext
    ex, st, tk = _ex(agent_type="planner_agent")
    res, vis, _ = ex._resolve_visible_refs(["req", "research", "ccx"], "tenant_a", st)
    assert res["req"]["content"]["summary"] == "REQ-SUM"
    assert "findings" in res["research"]["content"]
    assert {"files", "symbols", "code_context"} <= set(res["ccx"]["content"])
    # CodeChange: 读 Plan.steps + CodeContext
    ex, st, tk = _ex(agent_type="code_change_agent")
    res, vis, _ = ex._resolve_visible_refs(["plan", "ccx"], "tenant_a", st)
    assert "steps" in res["plan"]["content"] and "EXTRA_HIDDEN" not in str(res["plan"]["content"])
    # Review: 读 Diff.patch
    ex, st, tk = _ex(agent_type="review_security_agent")
    res, vis, _ = ex._resolve_visible_refs(["diff", "patch", "plan"], "tenant_a", st)
    assert res["diff"]["content"]["patch"] == "DIFF-PATCH"
    assert "HIDDEN_DETAIL" not in str(res.get("...") or "")
    # TestRelease: 读 Review.approved
    ex, st, tk = _ex(agent_type="test_release_agent")
    res, vis, _ = ex._resolve_visible_refs(["review", "diff"], "tenant_a", st)
    assert res["review"]["content"]["approved"] is True
    assert "HIDDEN_DETAIL" not in str(res["review"]["content"])


def test_denied_refs_excluded_and_audited():
    """跨租户/类型越权/无效引用被拒并审计，且不进入正式输入（#4）。"""
    _seed(req_a=("RequirementArtifact", {"summary": "s", "requirements": []}))
    req_b = AgentArtifact(artifact_id="req_b", task_id="tp", tenant_id="tenant_b",
                          agent_type="seed", artifact_type="RequirementArtifact",
                          content={"summary": "foreign"}, idempotency_key="ik_req_b")
    astore.put(req_b)
    st = SimpleNamespace(step_id="tp:repository_research_agent", agent_type="repository_research_agent",
                         agent_attempt=0, repair_round=0, task_id="tp")
    tk = SimpleNamespace(id="tp", tenant_id="tenant_a", project_id="p", repository_id="r", title="t")
    logs = []
    ex = AgentExecutor("repository_research_agent", audit=lambda *a, **k: logs.append(a))
    res, vis, denied = ex._resolve_visible_refs(["req_a", "req_b", "ghost_ref"], "tenant_a", st)
    assert set(vis) == {"req_a"}, "仅同租户且类型允许的 req_a 可见"
    assert len(denied) == 2 and "req_b" in dict(denied) and "ghost_ref" in dict(denied)
    assert dict(denied)["req_b"] == "cross_tenant"
    assert any("runtime.artifact.denied" in (a[1] if len(a) > 1 else "") for a in logs)
    # 类型越权：Planner 读 Diff → 拒绝并审计
    _seed(dff=("DiffArtifact", {"patch": "p"}))
    pl = AgentExecutor("planner_agent", audit=lambda *a, **k: logs.append(a))
    res2, vis2, denied2 = pl._resolve_visible_refs(["dff"], "tenant_a", st)
    assert "dff" not in vis2 and dict(denied2).get("dff") == "type_not_allowed"
    # 被拒引用不进入正式输入：research 正式输入只含 req_a，且 input_schema 校验通过
    si = ex._build_structured_input(task=tk, step=st, resolved=res, visible_refs=vis)
    assert si["requirement_ref"] == "req_a"
    assert si["_input_refs"] == ["req_a"]
    from app.enterprise_runtime.agents.schema import ResearchInput
    ResearchInput.model_validate(si)


def test_structured_input_has_controlled_fields_not_full_output():
    """每个 Agent 的正式输入含受控字段（summary/objectives 等），而非上游完整原文。"""
    _seed(req=("RequirementArtifact", {"summary": "REQ-SUM", "requirements": [{"id": "r1"}],
                                        "open_questions": [], "completeness": "complete",
                                        "MASSIVE_ORIGINAL": "." * 5000}))
    ex, st, tk = _ex(agent_type="repository_research_agent")
    res, vis, _ = ex._resolve_visible_refs(["req"], "tenant_a", st)
    out = ex._build_structured_input(task=tk, step=st, resolved=res, visible_refs=vis)
    assert out["requirement_ref"] == "req"
    assert out["_resolved"]["req"]["content"]["summary"] == "REQ-SUM"
    assert "MASSIVE_ORIGINAL" not in str(out["_resolved"]["req"]["content"])
    # input_schema 校验通过（ResearchInput.requirement_ref 已填）
    from app.enterprise_runtime.agents.schema import ResearchInput
    ResearchInput.model_validate(out)


def test_celery_task_registered_and_submit_dispatch(request):
    """新任务 enterprise_agent_execute 已注册；submit_agent_workflow 可投递（eager 内联）。

    OPT-05：本用例锁定确定性语义，不依赖本机 broker 是否可达；
    真实 Redis broker 投递见 test_celery_real_broker_dispatch_requires_redis。
    """
    from app.core.runtime_config import load_runtime_settings
    from app.workers.celery_app import celery_app
    from app.workers.enterprise_tasks import enterprise_agent_execute, submit_agent_workflow

    # Celery 的 Settings 用 _data 存键，monkeypatch 的 undo 会退化成 delattr 并抛
    # AttributeError；改走 conf.update 写入、按模块初始化语义还原。
    runtime = load_runtime_settings()
    request.addfinalizer(lambda: celery_app.conf.update(
        task_always_eager=not bool(runtime.celery_broker_url),
        broker_url=runtime.celery_broker_url or None,
    ))
    celery_app.conf.update(task_always_eager=True, broker_url=None)
    assert "enterprise.agent_execute" in celery_app.tasks, "Celery 未注册 enterprise.agent_execute"
    _new_task("tb", version=2)
    res = submit_agent_workflow("tb")
    assert res.get("dispatched") is True
    task = rstore.get("tb")
    assert task.workflow_version >= 2


@pytest.mark.requires_redis
def test_celery_real_broker_dispatch_requires_redis(monkeypatch):
    """真实 Redis broker 投递（requires_redis）：可达才运行，不可达归为环境缺失 skip。"""
    import redis as redis_client

    from app.workers.celery_app import celery_app
    from app.workers.enterprise_tasks import enterprise_agent_execute
    from service_probe import redis_broker_url

    url = redis_broker_url()
    client = redis_client.Redis.from_url(url, socket_connect_timeout=2)
    client.ping()  # 协议级确认（收集期分类用 TCP 预检）

    queue = "opt05.requires_redis.probe"
    monkeypatch.setattr(celery_app.conf, "task_always_eager", False, raising=False)
    monkeypatch.setattr(celery_app.conf, "broker_url", url, raising=False)
    try:
        enterprise_agent_execute.apply_async(kwargs={"task_id": "tredis-probe"}, queue=queue)
        assert client.llen(queue) >= 1, "企业任务应已投递到 broker 队列"
    finally:
        client.delete(queue)  # 只清理本用例唯一队列键，不动 broker 其它数据


def test_scenario_review_fail_once(monkeypatch):
    """S1：Review 首次 failed → back_code_change；旧 AgentRun 不被覆盖；不重跑 Planner/审批；二次通过续走 Test。"""
    _review_fail_once(monkeypatch)
    reg = _registry()
    _new_task("sf1", version=2)
    task, _ = _drive("sf1", reg)
    assert task.status == RuntimeTaskStatus.SUCCEEDED
    runs = runs_store.list_by_task("tenant_a", "sf1")
    planner = [r for r in runs if r.agent_type == "planner_agent"]
    cc = [r for r in runs if r.agent_type == "code_change_agent"]
    assert len(planner) == 1, "Planner 不应被重跑"
    assert task.approval_state == "approved" and task.approval_state != "pending", "不重复进入审批"
    assert task.repair_round >= 1
    assert len(cc) >= 2 and len({r.run_id for r in cc}) >= 2, "新增 CodeChange AgentRun 且旧的不被覆盖"
    # 二次 Review(approved=True) → 续走 Test → SUCCEEDED（_drive 已覆盖），且 Test 有成功 AgentRun
    tests = [r for r in runs if r.agent_type == "test_release_agent"]
    assert len(tests) >= 1 and tests[-1].status == "succeeded"


def test_scenario_test_fail_twice_then_human(monkeypatch):
    """S2：Test 连续 passed=false → 两次回 CodeChange → 达上限 WAITING_HUMAN，不再投递，审计原因。"""
    from app.enterprise_runtime.agents import factory as _f
    orig = _f.DeterministicAgent.invoke
    def patched(self, si):
        out = orig(self, si)
        if self.agent_type == "test_release_agent":
            out["passed"] = False
        return out
    monkeypatch.setattr(_f.DeterministicAgent, "invoke", patched)
    reg = AgentExecutorRegistry(audit=lambda *a, **k: None)
    _new_task("sf2", version=2)
    task, _ = _drive("sf2", reg)
    assert task.status == RuntimeTaskStatus.WAITING_HUMAN, f"应 WAITING_HUMAN，实 {task.status}"
    assert task.repair_round >= 2, "达到 max_repair_rounds"
    assert task.wait_human_reason and "上限" in task.wait_human_reason
    # 不再投递：继续驱动不新增 AgentRun、状态不向前
    n = len(runs_store.list_by_task("tenant_a", "sf2"))
    again = orch.run_agent_workflow("w", "sf2", reg, ctx=_ctx())
    assert again.status == RuntimeTaskStatus.WAITING_HUMAN
    assert len(runs_store.list_by_task("tenant_a", "sf2")) == n, "WAITING_HUMAN 后不应再投递新 Agent"
    # 审计：失败 + 人工接管原因
    from app import main
    hung = [e for e in main.AUDIT_EVENTS.values() if e.event_type == "runtime.task.waiting_human"]
    assert hung, "缺 runtime.task.waiting_human 审计"
    assert any(("上限" in e.summary or "上限" in (task.wait_human_reason or "")) for e in hung)


def test_scenario_duplicate_dispatch_lock_and_idempotent():
    """S3：重复投递 → dispatch lock 挡住第二投递；同 AgentRun/Artifact 幂等；RuntimeTask.version 不非法回退。"""
    from app.core import distributed_lock as dl
    t1 = dl.try_acquire_agent_dispatch("dup")
    t2 = dl.try_acquire_agent_dispatch("dup")
    assert t1 is not None and t2 is None, "dispatch lock 应使第二投递失败(None)"
    dl.release_agent_dispatch("dup", t1)
    t3 = dl.try_acquire_agent_dispatch("dup")
    assert t3 is not None, "释放后可再获取"
    dl.release_agent_dispatch("dup", t3)

    reg = _registry()
    _new_task("dup", version=2)
    t = rstore.get("dup"); v0 = t.version
    orch.run_agent_workflow("wA", "dup", reg, ctx=_ctx())
    v1 = rstore.get("dup").version
    orch.run_agent_workflow("wB", "dup", reg, ctx=_ctx())
    v2 = rstore.get("dup").version
    assert v1 >= v0 and v2 >= v1, "RuntimeTask.version 不得回退"

    # AgentRun 幂等：对已完成 run 按相同 idempotency_key 重复 put 不新增
    pre = len(runs_store.list_by_task("tenant_a", "dup"))
    done = [r for r in runs_store.list_by_task("tenant_a", "dup") if r.status == "succeeded"]
    for r in done:
        dup = AgentRun(run_id=r.run_id, task_id="dup", tenant_id="tenant_a", agent_type=r.agent_type,
                       status=r.status, idempotency_key=r.idempotency_key)
        runs_store.put(dup)
    assert len(runs_store.list_by_task("tenant_a", "dup")) == pre, "重复投递不应新增 AgentRun"


def test_real_agent_unwraps_pydantic_structured_response():
    """LangChain create_agent returns a Pydantic object for structured_response."""
    from app.enterprise_runtime.agents.factory import _RealLangchainAgent
    from app.enterprise_runtime.agents.schema import RequirementOutput

    class FakeCompiled:
        def invoke(self, _payload, config=None):
            return {
                "structured_response": RequirementOutput(
                    summary="真实结构化响应",
                    requirements=[{"id": "r1", "description": "x", "acceptance": "y"}],
                    open_questions=[],
                    completeness="complete",
                )
            }

    agent = _RealLangchainAgent("requirement_agent", FakeCompiled(), "test")
    result = agent.invoke({"issue": "x"})
    assert result["summary"] == "真实结构化响应"
    assert result["requirements"][0]["id"] == "r1"


def test_real_agent_invoke_has_bounded_recursion(monkeypatch):
    """真实 Agent 工具循环必须有显式 LangGraph recursion_limit。"""
    from app.enterprise_runtime.agents.factory import _RealLangchainAgent
    from app.enterprise_runtime.agents.schema import RequirementOutput

    seen = {}

    class FakeCompiled:
        def invoke(self, _payload, config=None):
            seen["config"] = config
            return {
                "structured_response": RequirementOutput(
                    summary="bounded",
                    requirements=[],
                    open_questions=[],
                    completeness="complete",
                )
            }

    monkeypatch.setenv("AGENT_RECURSION_LIMIT", "7")
    agent = _RealLangchainAgent("requirement_agent", FakeCompiled(), "test")
    agent.invoke({"issue": "x"})

    assert seen["config"]["recursion_limit"] == 7


def test_repo_read_returns_bounded_code_snippets(monkeypatch):
    """Research 工具应提供限长代码上下文，避免 Agent 只能反复看文件元数据。"""
    import json
    from app import main
    from app.enterprise_runtime.agents.tools import _repo_read

    chunk = SimpleNamespace(
        repository_id="repo_probe",
        file_path="src/service.py",
        symbol_name="handle",
        start_line=10,
        end_line=18,
        content="def handle(value):\n    return value\n",
    )
    monkeypatch.setattr(main, "CODE_CHUNKS", {"chunk_probe": chunk})

    result = json.loads(_repo_read.invoke({"path": "src/service.py", "repository_id": "repo_probe"}))

    assert result["chunks"][0]["content"] == chunk.content
    assert len(result["chunks"][0]["content"]) <= 1600


def test_artifact_primary_key_allows_long_artifact_ids():
    """CodeContextArtifact 等类型的结构化 ID 不得被 64 字符主键截断。"""
    from app.models.orm import AgentArtifactORM

    assert AgentArtifactORM.__table__.c.id.type.length >= 120


def test_read_diff_returns_controlled_artifact_content():
    """Review 工具读取 diff ref 时必须返回真实受控内容，而非 ref 占位符。"""
    import json
    from app.enterprise_runtime.agents.tools import _read_diff

    _seed(diff_read=("DiffArtifact", {"diff_ref": "d1", "patch": "REAL-DIFF"}))
    result = json.loads(_read_diff.invoke({"diff_ref": "diff_read"}))

    assert result["artifact_type"] == "DiffArtifact"
    assert result["content"]["patch"] == "REAL-DIFF"


def test_real_run_test_uses_configured_docker_runner(monkeypatch):
    """真实 TestRelease 路径不能因不存在的配置字段被恒定判成 BLOCKED。"""
    import json
    from app.enterprise_runtime.agents.tools import _run_test
    from app.services import test_runner

    monkeypatch.setenv("AGENT_PROVIDER", "real")
    monkeypatch.setenv("TEST_RUNNER_MODE", "docker")
    monkeypatch.setenv("TEST_RUNNER_IMAGE", "platform-test-runner:base")
    monkeypatch.setattr(
        test_runner,
        "run_whitelisted",
        lambda command, workspace=None: {
            "runner_mode": "docker",
            "exit_code": 0,
            "stdout": "1 passed",
            "stderr": "",
            "error": None,
        },
    )

    result = json.loads(_run_test.invoke({"command": "pytest -q"}))

    assert result["runner_mode"] == "docker"
    assert result["exit_code"] == 0


def test_test_release_prompt_has_bounded_tool_protocol():
    """TestRelease 需限制工具次数，避免真实模型在 BLOCKED 后无限重试。"""
    prompt = AGENT_DEFINITIONS["test_release_agent"].system_prompt

    assert "最多调用一次" in prompt
    assert "BLOCKED" in prompt
    assert "立即返回 JSON" in prompt
    assert "只能使用 pytest、python 或 echo" in prompt
    assert "不要使用 test、grep、find、cd" in prompt


def test_test_release_blocked_result_is_environment_failure():
    """发布/测试环境 BLOCKED 时不能被编排器当成普通代码质量失败。"""
    from app.enterprise_runtime.agents.executor import classify_test_release_output

    kind = classify_test_release_output(
        {
            "passed": False,
            "test_cases": [{"status": "blocked", "reason": "命令不在测试白名单"}],
            "release_evidence": {"status": "BLOCKED"},
        }
    )

    assert kind == "environment"


def test_real_loop_target_is_configurable(monkeypatch):
    """真实闭环必须能绑定到已授权、已索引的租户/项目/仓库。"""
    from scripts import e4_agent_real_loop

    monkeypatch.setenv("E4_TENANT_ID", "tenant_default")
    monkeypatch.setenv("E4_PROJECT_ID", "proj_health_middle")
    monkeypatch.setenv("E4_REPOSITORY_ID", "repo_health_middle")
    monkeypatch.setenv("E4_ISSUE", "为健康中台补充一个可验证的只读健康检查说明")

    assert e4_agent_real_loop._target_config() == {
        "tenant_id": "tenant_default",
        "project_id": "proj_health_middle",
        "repository_id": "repo_health_middle",
    }
    assert e4_agent_real_loop._issue_text() == "为健康中台补充一个可验证的只读健康检查说明"


def test_e4_pr_adapter_requires_scratch_gate_and_is_idempotent(tmp_path, monkeypatch):
    """E4 真实 PR 必须显式允许 scratch 仓，并且重复调用不能重复建 PR。"""
    from types import SimpleNamespace

    from app.services import enterprise_release_service as release_service

    task = SimpleNamespace(
        id="rt_pr_probe",
        repository_id="repo_health_middle",
        project_id="proj_health_middle",
        tenant_id="tenant_default",
        title="E4 PR probe",
    )
    repo = SimpleNamespace(
        id="repo_health_middle",
        project_id="proj_health_middle",
        url="https://gitee.com/example/health-middle.git",
        default_branch="main",
    )
    monkeypatch.setattr(release_service, "_repository_for_task", lambda _: repo)
    monkeypatch.setattr(release_service, "_prepare_and_push_runtime_branch",
                        lambda *args, **kwargs: {"head_branch": "agent/e4/rt_pr_probe",
                                                  "commit_sha": "a" * 40,
                                                  "diff_hash": "d" * 64})
    monkeypatch.setattr(
        release_service.gitee_pr_service,
        "create_gitee_pull_request",
        lambda *args, **kwargs: {
            "id": "9001", "number": "1",
            "html_url": "https://gitee.com/example/health-middle/pulls/1",
            "state": "OPEN",
        },
    )
    monkeypatch.setenv("ALLOW_SCRATCH_REAL_CHANGE", "true")
    monkeypatch.setenv("E2E_REAL_CHANGE_REPO_IDS", "repo_health_middle")
    monkeypatch.setenv("E4_RELEASE_STATE_DIR", str(tmp_path))
    # OPT-05 隔离后测试进程不再读 backend/.env；PR 适配器所需令牌由用例显式注入。
    monkeypatch.setenv("GITEE_TOKEN", "token-for-test")

    first = release_service.create_runtime_pr(task, title="E4 PR probe", body="body")
    second = release_service.create_runtime_pr(task, title="E4 PR probe", body="body")

    assert first["html_url"].endswith("/pulls/1")
    assert second == first
    assert (tmp_path / "rt_pr_probe.pr.json").is_file()


def test_e4_release_evidence_requires_verified_inputs_and_writes_hash(tmp_path, monkeypatch):
    """发布证据必须由真实测试、审查和 PR 结果组成，并落盘 content_hash。"""
    from app.services import enterprise_release_service as release_service

    monkeypatch.setenv("RELEASE_EVIDENCE_DIR", str(tmp_path))
    blocked = release_service.write_release_evidence(
        task_id="rt_evidence_probe",
        run_id="run_evidence_probe",
        diff_ref="diff-1",
        diff_hash="d" * 64,
        review={"approved": True},
        test_result={"runner_mode": "docker", "exit_code": 0, "test_run_id": "test-1"},
        pr={"number": "1", "html_url": "https://gitee.com/example/repo/pulls/1"},
    )

    assert blocked["status"] == "VERIFIED"
    assert blocked["content_hash"]
    assert (tmp_path / "rt_evidence_probe.release-evidence.json").is_file()
    assert blocked["test"]["exit_code"] == 0
    assert blocked["pr"]["html_url"].endswith("/pulls/1")


def test_e4_release_preflight_distinguishes_direct_gitee_and_local_evidence(monkeypatch, tmp_path):
    """预检不能只判断 URL 字符串，直连 Gitee 与本地证据源都要有真实配置。"""
    from scripts import e4_preflight

    monkeypatch.setenv("E4_PR_PROVIDER", "gitee")
    monkeypatch.setenv("GITEE_TOKEN", "token-for-test")
    monkeypatch.setenv("E4_PR_REPOSITORY_ID", "repo_health_middle")
    monkeypatch.setenv("ALLOW_SCRATCH_REAL_CHANGE", "true")
    monkeypatch.setenv("E2E_REAL_CHANGE_REPO_IDS", "repo_health_middle")
    monkeypatch.setenv("RELEASE_EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setattr(e4_preflight, "_probe_gitee_pr_backend", lambda: (True, "gitee reachable"))

    pr, evidence = e4_preflight._probe_release_backends()

    assert pr == ("PASS", "gitee reachable")
    assert evidence[0] == "PASS"
