"""团队版 7.5 AI 分析与修改计划增强测试套件。

覆盖：
1. 分析输入四源（Issue / 代码上下文 / 历史相似任务 / 项目默认规则）；
2. 分析输出四要素（问题摘要 / 影响范围 / 风险初判 / 阻塞问题）；
3. 修改计划五要素与版本递增、重写生成新版本；
4. 重写次数上限（MAX_PLAN_REWRITES）；
5. 风险重算原因与审批调整记录可见；
6. 结构化完整性门禁（残缺计划不进入审批）；
7. 历史相似任务只作参考、不携带旧 Diff/结论。
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main

HEAD_A = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
NEUTRAL_BODY = "列表页在窄屏下布局错乱，需要调整样式并补充回归测试。"
NEUTRAL_CODE = "/* 列表页窄屏布局与样式修复 */\n.list-page { display: flex; }\n"

repository = main.REPOSITORIES["repo_payment_service"]
developer = main.USERS["user_developer"]


@pytest.fixture(autouse=True)
def fast_seams(monkeypatch):
    monkeypatch.setattr(main, "INDEX_STAGE_SECONDS", 0)
    monkeypatch.setattr(main, "fetch_gitee_branch_head", lambda repo, base_branch=None: HEAD_A)
    yield


def snapshot_of(title: str, body: str, labels: list[str] | None = None) -> main.IssueSnapshotRecord:
    return main.IssueSnapshotRecord(
        title=title,
        body=body,
        comments=[],
        labels=labels or ["ui"],
        raw_response={},
        fetched_at=datetime.now(timezone.utc),
    )


def context_of(chunk_index: int = 0) -> main.RetrievalContext:
    return main.RetrievalContext(
        chunk_id=f"chunk_t75_{chunk_index}",
        file_path="src/list_page.css",
        start_line=1,
        end_line=8,
        language="css",
        content=NEUTRAL_CODE,
        score=0.9,
        commit_sha=HEAD_A,
        index_id="idx_t75_completed",
        fresh=True,
        source="milvus",
    )


def similar_ref() -> main.SimilarTaskRef:
    return main.SimilarTaskRef(
        task_id="TASK-HIST-001",
        version=2,
        title="历史：修复列表页布局错位",
        issue_id="IHIST001",
        status="COMPLETED",
        risk_level="low",
        score=0.71,
        summary="历史任务通过调整弹性布局修复同类窄屏问题，仅作参考。",
    )


def seed_analyzed_task(
    *,
    issue_id: str = "IT7501",
    title: str = "优化列表页样式",
    body: str = NEUTRAL_BODY,
    with_context: bool = True,
    with_similar: bool = True,
) -> main.TaskRecord:
    now = datetime.now(timezone.utc)
    task = main.TaskRecord(
        id=f"TASK-{9500 + len(main.TASKS)}",
        version=1,
        project_id=repository.project_id,
        project_name=main.PROJECTS[repository.project_id].name,
        repository_id=repository.id,
        repository_name=repository.name,
        repository_url=repository.url,
        issue_id=issue_id,
        title=title,
        status="CREATED",
        base_branch=repository.default_branch,
        test_command=repository.test_command,
        approval_owner_id=repository.approval_owner_id,
        approval_owner_name=repository.approval_owner_name,
        work_branch=f"agent/main/issue-{issue_id}-v1",
        issue_snapshot=snapshot_of(title, body),
        automation_state="running",
        manual_required=False,
        manual_reason="none",
        next_action="check_index",
        events=[],
        created_by=developer.id,
        created_by_name=developer.name,
        created_at=now - timedelta(seconds=30),
        updated_at=now,
    )
    main.TASKS[task.id] = task
    if with_context:
        task.code_contexts = [context_of()]
    if with_similar:
        task.similar_task_refs = [similar_ref()]
    # 分析输入在生成时拼装，必须先挂好上下文与历史引用
    analysis = main.generate_mock_analysis(task)
    for question in analysis.questions:
        question.answered = True
        question.answer = question.default_answer or "按默认口径处理"
    main.attach_analysis(task, analysis)
    return task


def approver_client() -> TestClient:
    test_client = TestClient(main.app)
    login = test_client.post(
        "/api/auth/login",
        json={"account": "approver@company.com", "password": "Approver123!"},
    )
    assert login.status_code == 200
    return test_client


def generate_plan_via_api(client: TestClient, task: main.TaskRecord) -> main.ModificationPlan:
    response = client.post(f"/api/tasks/{task.id}/plans")
    assert response.status_code == 200, response.text
    return max(task.plans, key=lambda plan: plan.version)


# ---------- 1. 分析输入四源 ----------

def test_analysis_sources_include_issue_context_history_default():
    task = seed_analyzed_task()
    types = {source.source_type for source in task.analysis.sources}
    # Issue（标题/正文/标签）、代码上下文、历史相似任务、项目默认规则四类输入齐备
    assert {"issue_title", "issue_body", "code_context", "history", "project_default"} <= types
    default_source = next(source for source in task.analysis.sources if source.source_type == "project_default")
    assert repository.test_command in default_source.content
    assert "风险阈值" in default_source.content
    code_source = next(source for source in task.analysis.sources if source.source_type == "code_context")
    assert "list_page.css" in code_source.source_ref
    history_source = next(source for source in task.analysis.sources if source.source_type == "history")
    assert "TASK-HIST-001" in history_source.source_ref
    assert "仅参考" in history_source.source_ref


def test_analysis_prompt_contains_history_and_default_rules():
    task = seed_analyzed_task()
    prompt = main._analysis_prompt(task, None)
    assert "历史相似任务" in prompt
    assert "禁止直接照搬" in prompt
    assert "项目默认规则" in prompt


# ---------- 2. 分析输出四要素 ----------

def test_analysis_output_four_elements_complete():
    task = seed_analyzed_task()
    analysis = task.analysis
    assert analysis.decision_summary  # 问题摘要
    assert analysis.affected_areas  # 影响范围
    assert analysis.suggested_risk_level in {"low", "medium", "high"}  # 风险初判
    assert analysis.risk_reason
    assert isinstance(analysis.questions, list)  # 阻塞问题（可为空列表，但字段必须存在）


# ---------- 3/4. 计划五要素、版本递增与重写新版本 ----------

def test_plan_contains_five_elements(client):
    task = seed_analyzed_task()
    plan = generate_plan_via_api(client, task)
    assert plan.goal.strip()
    assert plan.file_scope
    assert plan.steps and all(step.title.strip() and step.description.strip() for step in plan.steps)
    assert plan.risk_level in {"low", "medium", "high"}
    assert plan.test_suggestions


def test_rewrite_generates_new_version_and_supersedes_old(client):
    task = seed_analyzed_task()
    first = generate_plan_via_api(client, task)
    assert first.version == 1
    approver = approver_client()
    response = approver.post(
        f"/api/tasks/{task.id}/plans/{first.id}/rewrite",
        json={"reason": "请补充窄屏断点的具体处理步骤。"},
    )
    assert response.status_code == 200, response.text
    versions = sorted(plan.version for plan in task.plans)
    assert versions == [1, 2]
    old = next(plan for plan in task.plans if plan.version == 1)
    new = max(task.plans, key=lambda plan: plan.version)
    assert old.status == "SUPERSEDED"
    assert new.status == "WAITING_APPROVAL"
    assert any(event.event_type == "PLAN_REWRITTEN" for event in task.events)


# ---------- 5. 重写次数上限 ----------

def test_rewrite_blocked_after_max_rewrites(client):
    task = seed_analyzed_task()
    plan = generate_plan_via_api(client, task)
    approver = approver_client()
    # MAX_PLAN_REWRITES = 2：v1→v2、v2→v3 允许，对 v3 再重写必须被拦截
    for _ in range(main.MAX_PLAN_REWRITES):
        response = approver.post(
            f"/api/tasks/{task.id}/plans/{plan.id}/rewrite",
            json={"reason": "继续调整计划细节。"},
        )
        assert response.status_code == 200, response.text
        plan = max(task.plans, key=lambda item: item.version)
    assert plan.version == 1 + main.MAX_PLAN_REWRITES
    blocked = approver.post(
        f"/api/tasks/{task.id}/plans/{plan.id}/rewrite",
        json={"reason": "还想再改。"},
    )
    assert blocked.status_code == 409
    assert "上限" in blocked.json()["detail"]["message"]
    assert any(event.event_type == "PLAN_REWRITE_BLOCKED" for event in task.events)


# ---------- 6. 风险重算与调整记录可见 ----------

def test_plan_records_risk_recalc_and_approval_adjust(client):
    task = seed_analyzed_task()
    plan = generate_plan_via_api(client, task)
    # 分析初判风险被快照到计划，计划阶段重算原因非空
    assert plan.analysis_risk_level == task.analysis.suggested_risk_level
    assert plan.risk_reason.strip()
    original_risk = plan.risk_level
    approver = approver_client()
    target = "high" if original_risk != "high" else "medium"
    response = approver.post(
        f"/api/tasks/{task.id}/plans/{plan.id}/approve",
        json={"risk_level": target, "risk_adjust_reason": "涉及资金链路，审批上调风险等级。", "comment": "通过"},
    )
    assert response.status_code == 200, response.text
    refreshed = max(task.plans, key=lambda item: item.version)
    assert refreshed.risk_level == target
    assert refreshed.risk_adjust_from == original_risk
    assert "资金链路" in refreshed.risk_adjust_reason


def test_risk_adjust_requires_reason(client):
    task = seed_analyzed_task()
    plan = generate_plan_via_api(client, task)
    approver = approver_client()
    target = "high" if plan.risk_level != "high" else "medium"
    response = approver.post(
        f"/api/tasks/{task.id}/plans/{plan.id}/approve",
        json={"risk_level": target},
    )
    assert response.status_code == 422


# ---------- 7. 结构化完整性门禁 ----------

def test_validate_plan_structure_reports_missing():
    task = seed_analyzed_task()
    broken = main.generate_mock_plan(task)
    broken.goal = ""
    broken.steps = []
    broken.test_suggestions = []
    missing = main.validate_plan_structure(broken)
    assert "计划目标" in missing
    assert "实施步骤" in missing
    assert "测试建议" in missing


def test_attach_plan_raises_on_incomplete_structure():
    task = seed_analyzed_task()
    broken = main.generate_mock_plan(task)
    broken.file_scope = []
    with pytest.raises(main.PlanStructureIncomplete) as exc:
        main.attach_plan(task, broken, trigger_user=developer)
    assert "文件范围" in exc.value.missing
    assert task.plans == []  # 残缺计划不落库
    assert task.status == "ANALYZED"  # 不进入待审批


def test_plan_endpoint_409_when_structure_incomplete(client, monkeypatch):
    task = seed_analyzed_task()
    broken = main.generate_mock_plan(task)
    broken.goal = "   "
    monkeypatch.setattr(main, "generate_plan", lambda _task: broken)
    response = client.post(f"/api/tasks/{task.id}/plans")
    assert response.status_code == 409
    assert "结构化不完整" in response.json()["detail"]["message"]
    assert task.status == "ANALYZED"
    assert task.plans == []


# ---------- 8. 历史任务只作参考 ----------

def test_historical_refs_are_reference_only_without_old_diff(client):
    task = seed_analyzed_task()
    plan = generate_plan_via_api(client, task)
    # 计划快照了历史引用
    assert len(plan.referenced_historical_tasks) == 1
    ref = plan.referenced_historical_tasks[0]
    assert ref.task_id == "TASK-HIST-001"
    # 引用模型不携带旧 Diff / 计划 / 代码上下文，结构上不可能替代当前判断
    forbidden = {"diff", "plans", "code_contexts", "patch"}
    assert not (forbidden & set(main.SimilarTaskRef.model_fields.keys()))
    # 分析中的历史来源只含摘要文本
    history_source = next(source for source in task.analysis.sources if source.source_type == "history")
    assert history_source.content == ref.summary or ref.summary in history_source.content
