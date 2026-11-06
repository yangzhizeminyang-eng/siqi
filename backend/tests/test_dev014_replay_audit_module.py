"""DEV-014 任务回放与审计模块测试套件。

覆盖：task_events / execution_steps 职责分离（结构默认兼容旧构造、人工决策事件不挂步骤）、
关键事件落库（事件双写全局审计表）、真实 API 主链自动埋点（计划生成→审批→Diff→测试→PR 描述
各节点挂步骤）、失败/拦截节点可展开失败原因（测试失败 failed + failure_reason + duration_ms、
测试拦截 blocked + blocked_reason、Diff 失败、PR 描述失败）、时间线/单事件聚合端点（升序内嵌
步骤、404、跨任务事件 404）、只读保证（普通用户不能写审计日志：无写路由 405、全局审计表
project:manage）、索引复用/失败分流步骤、自动推进分析步骤与上下文聚合失败、重试保留旧步骤并追加新步骤。
"""
from datetime import datetime, timedelta, timezone
from itertools import count

import pytest
from fastapi.testclient import TestClient

from app import main
from app.services import diff_service

NEUTRAL_BODY = "支付回调在弱网重试下会重复入账，需要保证幂等。"
_task_seq = count(9800)

repository = main.REPOSITORIES["repo_payment_service"]
developer = main.USERS["user_developer"]
owner = main.USERS["user_approver"]


def authed_client(account: str, password: str) -> TestClient:
    test_client = TestClient(main.app)
    login = test_client.post("/api/auth/login", json={"account": account, "password": password})
    assert login.status_code == 200
    return test_client


@pytest.fixture()
def client() -> TestClient:
    return authed_client("developer@company.com", "Dev123!")


@pytest.fixture()
def approver_client() -> TestClient:
    return authed_client("approver@company.com", "Approver123!")


@pytest.fixture()
def admin_client() -> TestClient:
    return authed_client("admin@company.com", "Admin123!")


def snapshot_of(title: str, body: str) -> main.IssueSnapshotRecord:
    return main.IssueSnapshotRecord(
        title=title,
        body=body,
        comments=[],
        labels=["backend"],
        raw_response={},
        fetched_at=datetime.now(timezone.utc),
    )


def base_task(status: str = "ANALYZED", *, body: str = NEUTRAL_BODY) -> main.TaskRecord:
    now = datetime.now(timezone.utc)
    task_id = f"TASK-{next(_task_seq)}"
    task = main.TaskRecord(
        id=task_id,
        version=1,
        project_id=repository.project_id,
        project_name=main.PROJECTS[repository.project_id].name,
        repository_id=repository.id,
        repository_name=repository.name,
        repository_url=repository.url,
        issue_id=f"IDEV014{next(_task_seq) % 100}",
        title="支付回调幂等修复",
        status=status,
        risk_level="high",
        risk_source="plan_approval",
        base_branch=repository.default_branch,
        test_command=repository.test_command,
        approval_owner_id=owner.id,
        approval_owner_name=owner.name,
        work_branch=f"agent/main/issue-dev014-v1",
        issue_snapshot=snapshot_of("支付回调幂等修复", body),
        automation_state="blocked",
        manual_required=False,
        manual_reason="none",
        next_action="watch_test_result",
        events=[],
        created_by=developer.id,
        created_by_name=developer.name,
        created_at=now - timedelta(minutes=5),
        updated_at=now,
    )
    main.TASKS[task.id] = task
    return task


def seed_analyzed_task() -> main.TaskRecord:
    """ANALYZED 且无阻塞问题、已配置审批人，可走 计划→审批→Diff→测试→PR 主链。"""
    task = base_task(status="CREATED")
    analysis = main.generate_mock_analysis(task)
    for question in analysis.questions:
        question.answered = True
        question.answer = question.default_answer or "按默认口径处理"
    main.attach_analysis(task, analysis)
    return task


def seed_task_with_diff(status: str = "DIFF_GENERATED") -> main.TaskRecord:
    """含审批通过计划 + 成功 Diff 产物（approval/diff 历史不经过本次端点）。"""
    task = base_task(status=status)
    now = datetime.now(timezone.utc)
    plan = main.ModificationPlan(
        id=f"PLAN-{task.id.removeprefix('TASK-')}-v1",
        task_id=task.id,
        version=1,
        goal="修复回调幂等并补充回归",
        file_scope=[
            main.PlanFileScope(file_path="src/handler.py", change_type="modify", reason="修复重复入账")
        ],
        steps=[main.PlanStep(order=1, title="校验订单状态", description="重复入账前先校验状态", target_files=["src/handler.py"])],
        test_suggestions=[repository.test_command],
        assumptions=[],
        rollback_plan=["回滚 src/handler.py 至 base_commit"],
        risk_level="high",
        risk_reason="高风险改动，需要审批",
        generated_from_analysis_revision=1,
        generated_from_context_ids=[],
        provider="mvp-rule-based",
        status="APPROVED",
        approved_by_id=owner.id,
        approved_by_name=owner.name,
        approved_at=now - timedelta(minutes=4, seconds=30),
        created_by=developer.id,
        created_by_name=developer.name,
        created_at=now - timedelta(minutes=4),
    )
    artifact = main.DiffArtifact(
        id=f"DIFF-{task.id.removeprefix('TASK-')}-v1",
        task_id=task.id,
        plan_id=plan.id,
        plan_version=1,
        execution_step="步骤 1：校验订单状态",
        generation_status="SUCCEEDED",
        execution_mode="DEMO_FALLBACK",
        is_demo_fallback=True,
        fallback_reason="MVP 未接入真实执行环境",
        real_failure_reason=None,
        generated_at=now - timedelta(minutes=3),
        files=[
            main.DiffFileRecord(
                id=f"DIFF-{task.id.removeprefix('TASK-')}-v1-1",
                file_path="src/handler.py",
                change_type="modify",
                patch="--- a/src/handler.py\n+++ b/src/handler.py",
                added_lines=2,
                deleted_lines=1,
                before=["def handle(order):", "    return order"],
                after=["def handle(order):", "    assert_processed(order)", "    return order"],
            )
        ],
        test_summary="演示摘要",
        pr_description_draft="演示 PR 草稿",
    )
    task.plans.append(plan)
    task.diffs.append(artifact)
    return task


def completed_index(commit_sha: str) -> main.RepositoryIndexRecord:
    now = datetime.now(timezone.utc)
    index = main.RepositoryIndexRecord(
        id=f"IDX-{next(_task_seq)}",
        repository_id=repository.id,
        project_id=repository.project_id,
        base_branch=repository.default_branch,
        commit_sha=commit_sha,
        status="COMPLETED",
        total_files=2,
        processed_files=2,
        chunk_count=4,
        requested_by=developer.id,
        requested_by_name=developer.name,
        requested_at=now - timedelta(minutes=20),
        created_at=now - timedelta(minutes=20),
        updated_at=now - timedelta(minutes=19),
        started_at=now - timedelta(minutes=19, seconds=30),
        finished_at=now - timedelta(minutes=19),
    )
    main.REPOSITORY_INDEXES[index.id] = index
    return index


def timeline(client: TestClient, task_id: str):
    return client.get(f"/api/tasks/{task_id}/timeline")


def single_event(client: TestClient, task_id: str, event_id: str):
    return client.get(f"/api/tasks/{task_id}/events/{event_id}")


def approve_to_approved(client: TestClient, approver: TestClient, task: main.TaskRecord) -> None:
    waiting = client.post(f"/api/tasks/{task.id}/plans")
    assert waiting.status_code == 200, waiting.text
    plan_id = waiting.json()["data"]["plans"][0]["id"]
    approved = approver.post(f"/api/tasks/{task.id}/plans/{plan_id}/approve", json={"comment": "范围可控，审批通过"})
    assert approved.status_code == 200, approved.text


def events_of(task: main.TaskRecord, event_type: str) -> list[main.AuditEventRecord]:
    return [event for event in task.events if event.event_type == event_type]


def steps_of_event(task: main.TaskRecord, event_id: str) -> list[main.ExecutionStepRecord]:
    return [step for step in task.execution_steps if step.event_id == event_id]


# ---------- 1. 数据结构：职责分离与向后兼容 ----------

def test_new_step_models_default_to_empty_lists():
    """旧构造兼容：直接构造 TaskRecord / AuditEventRecord 不带步骤字段也合法。"""
    task = base_task(status="CREATED")
    created_event = main.task_event(
        task_id=task.id,
        event_type="TASK_CREATED",
        summary="构造事件",
        diff_summary=[],
        created_at=task.created_at,
    )
    assert created_event.execution_step_ids == []
    assert task.execution_steps == []
    assert main.AUDIT_EVENTS[created_event.id].id == created_event.id


def test_step_record_links_both_directions():
    """步骤落库双向一致：task.execution_steps 追加 + 事件 execution_step_ids 关联。"""
    task = base_task(status="CREATED")
    event = main.task_event(task_id=task.id, event_type="ISSUE_FETCHED", summary="拉取快照", diff_summary=[])
    step = main.record_execution_step(
        task,
        event=event,
        node="issue_fetch",
        title="拉取 Issue 快照",
        status="completed",
        input="Issue #1",
        output="标题：修复幂等；评论 2 条",
    )
    assert step.id.startswith("step_")
    assert step.task_id == task.id
    assert step.event_id == event.id
    assert task.execution_steps == [step]
    assert event.execution_step_ids == [step.id]
    assert steps_of_event(task, event.id) == [step]


def test_manual_decision_events_never_carry_steps(client, approver_client):
    """职责边界：审批/取消等人工决策事件不挂 execution_steps。"""
    task = seed_analyzed_task()
    approve_to_approved(client, approver_client, task)
    approved_event = events_of(task, "PLAN_APPROVED")[0]
    assert approved_event.execution_step_ids == []
    cancelled = client.post(f"/api/tasks/{task.id}/cancel")
    assert cancelled.status_code == 200
    assert events_of(task, "TASK_CANCELLED")[0].execution_step_ids == []


# ---------- 2. 真实 API 主链自动埋点（计划→审批→Diff→测试→PR 描述） ----------

def test_full_api_chain_instruments_completed_steps(client, approver_client):
    task = seed_analyzed_task()
    approve_to_approved(client, approver_client, task)

    plan_event = events_of(task, "PLAN_GENERATED")[0]
    plan_steps = steps_of_event(task, plan_event.id)
    assert len(plan_steps) == 1
    step = plan_steps[0]
    assert step.node == "plan_generation"
    assert step.status == "completed"
    assert "第 1 版" in step.input
    assert task.plans[0].id in step.output and "v1" in step.output

    # 审批事件是人工决策，不挂步骤
    assert events_of(task, "PLAN_APPROVED")[0].execution_step_ids == []

    diff_response = client.post(f"/api/tasks/{task.id}/diffs/generate")
    assert diff_response.status_code == 200, diff_response.text
    diff_event = events_of(task, "DIFF_GENERATED")[0]
    diff_steps = steps_of_event(task, diff_event.id)
    assert len(diff_steps) == 1
    assert diff_steps[0].node == "diff_generation"
    assert diff_steps[0].status == "completed"
    assert "DEMO_FALLBACK" in diff_steps[0].output
    assert len(task.diffs[0].files) == 3

    test_response = client.post(f"/api/tasks/{task.id}/tests/run")
    assert test_response.status_code == 200, test_response.text
    passed_event = events_of(task, "TEST_RUN_PASSED")[0]
    passed_steps = steps_of_event(task, passed_event.id)
    assert len(passed_steps) == 1
    assert passed_steps[0].node == "test_execution"
    assert passed_steps[0].status == "completed"
    assert passed_steps[0].duration_ms is not None and passed_steps[0].duration_ms >= 0
    assert "退出码 0" in passed_steps[0].output
    # 进度型事件 TEST_RUN_STARTED 不挂步骤
    assert events_of(task, "TEST_RUN_STARTED")[0].execution_step_ids == []

    pr_response = client.post(f"/api/tasks/{task.id}/pr-description/generate")
    assert pr_response.status_code == 200, pr_response.text
    pr_event = events_of(task, "PR_DESC_GENERATED")[0]
    pr_steps = steps_of_event(task, pr_event.id)
    assert len(pr_steps) == 1
    assert pr_steps[0].node == "pr_description"
    assert pr_steps[0].status == "completed"
    assert "/6" in pr_steps[0].output


def test_events_are_dual_written_to_global_audit_table(client, approver_client):
    """关键事件落库：任务内 events 与全局 AUDIT_EVENTS 双写一致，时间线可追溯。"""
    task = seed_analyzed_task()
    approve_to_approved(client, approver_client, task)
    assert task.events
    for event in task.events:
        assert main.AUDIT_EVENTS.get(event.id) is not None
        assert main.AUDIT_EVENTS[event.id].resource_type == "task"
        assert main.AUDIT_EVENTS[event.id].resource_id == task.id
    types = [event.event_type for event in task.events]
    assert "PLAN_GENERATED" in types and "PLAN_APPROVED" in types
    assert types.index("PLAN_GENERATED") < types.index("PLAN_APPROVED")


def test_association_lines_keep_plan_diff_test_pr_contract(client, approver_client):
    """审批/计划/Diff/测试/PR 关联行回归：事件文本仍编码各产物 id 与版本（既有契约不被破坏）。"""
    task = seed_analyzed_task()
    approve_to_approved(client, approver_client, task)
    client.post(f"/api/tasks/{task.id}/diffs/generate")
    client.post(f"/api/tasks/{task.id}/tests/run")
    client.post(f"/api/tasks/{task.id}/pr-description/generate")

    plan_approve = "\n".join(events_of(task, "PLAN_APPROVED")[0].diff_summary)
    assert "计划版本：v1" in plan_approve and owner.name in plan_approve
    # 计划 ID 级关联由步骤输出承载（事件文本只记版本，避免口径漂移）
    plan_event = events_of(task, "PLAN_GENERATED")[0]
    assert task.plans[0].id in steps_of_event(task, plan_event.id)[0].output

    diff_event = "\n".join(events_of(task, "DIFF_GENERATED")[0].diff_summary)
    assert task.diffs[0].plan_id in diff_event
    assert f"计划版本：v{task.diffs[0].plan_version}" in diff_event
    assert "变更文件：" in diff_event

    test_event = "\n".join(events_of(task, "TEST_RUN_PASSED")[0].diff_summary)
    assert task.test_runs[-1].diff_artifact_id in test_event
    assert "退出码：0" in test_event and "耗时：" in test_event

    pr_event = "\n".join(events_of(task, "PR_DESC_GENERATED")[0].diff_summary)
    assert task.pr_descriptions[0].plan_id in pr_event
    assert task.pr_descriptions[0].diff_artifact_id in pr_event
    assert task.pr_descriptions[0].test_run_id in pr_event
    assert "版本：第 1 版" in pr_event


# ---------- 3. 失败 / 拦截节点的步骤 ----------

@pytest.fixture()
def fail_runner(monkeypatch):
    def fake_execute(task, artifact, command, attempt):
        return {
            "runner_mode": "mocked_runner",
            "exit_code": 1,
            "stdout": "…收集 12 项\n",
            "stderr": "1 项失败\nFAILED tests/test_callback.py::test_idempotent - AssertionError: 重复入账\n",
            "duration": 0.8,
            "error": None,
        }
    monkeypatch.setattr(main.test_runner, "execute_test", fake_execute)


@pytest.fixture()
def unavailable_runner(monkeypatch):
    def fake_execute(task, artifact, command, attempt):
        return {
            "runner_mode": "unavailable",
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "duration": 0.0,
            "error": "Docker daemon 未运行或不可用（docker info 失败），无法执行容器测试",
        }
    monkeypatch.setattr(main.test_runner, "execute_test", fake_execute)


def test_failed_test_node_records_failure_step(client, fail_runner):
    task = seed_task_with_diff()
    response = client.post(f"/api/tasks/{task.id}/tests/run")
    assert response.status_code == 200, response.text
    assert task.status == "TEST_FAILED"
    failed_event = events_of(task, "TEST_RUN_FAILED")[0]
    failed_steps = steps_of_event(task, failed_event.id)
    assert len(failed_steps) == 1
    step = failed_steps[0]
    assert step.node == "test_execution"
    assert step.status == "failed"
    assert "AssertionError: 重复入账" in step.failure_reason
    assert step.duration_ms == 800
    assert step.output is None


def test_blocked_test_node_records_blocked_reason(client, unavailable_runner):
    task = seed_task_with_diff()
    response = client.post(f"/api/tasks/{task.id}/tests/run")
    assert response.status_code == 200, response.text
    blocked_event = events_of(task, "TEST_BLOCKED")[0]
    blocked_steps = steps_of_event(task, blocked_event.id)
    assert len(blocked_steps) == 1
    step = blocked_steps[0]
    assert step.node == "test_execution"
    assert step.status == "blocked"
    assert "Docker daemon" in step.blocked_reason
    # 环境不可用不消耗失败次数：任务回到可重试阶段
    assert task.status == "DIFF_GENERATED"


def test_diff_generation_failure_records_failed_step(client, approver_client, monkeypatch):
    task = seed_analyzed_task()
    approve_to_approved(client, approver_client, task)
    real_error = "真实执行失败：clone 超时（模拟）"
    monkeypatch.setattr(diff_service, "attempt_real_diff", lambda t, p: (None, real_error))
    monkeypatch.setattr(diff_service, "build_demo_diff", lambda t, p: None)
    response = client.post(f"/api/tasks/{task.id}/diffs/generate")
    assert response.status_code == 200, response.text
    failed_event = events_of(task, "DIFF_GENERATION_FAILED")[0]
    failed_steps = steps_of_event(task, failed_event.id)
    assert len(failed_steps) == 1
    step = failed_steps[0]
    assert step.node == "diff_generation"
    assert step.status == "failed"
    assert real_error in step.failure_reason
    # TASK_FAILED 事件（状态变化）不重复挂同节点步骤，避免双重记录
    assert events_of(task, "TASK_FAILED")[0].execution_step_ids == []


def test_pr_description_failure_records_failed_step(client, monkeypatch):
    task = seed_task_with_diff()
    run = client.post(f"/api/tasks/{task.id}/tests/run")
    assert run.status_code == 200, run.text
    assert task.status == "TESTED"
    monkeypatch.setattr(main.pr_description_service, "compose_pr_description", lambda *args: None)
    response = client.post(f"/api/tasks/{task.id}/pr-description/generate")
    assert response.status_code == 200, response.text
    failed_event = events_of(task, "PR_DESC_FAILED")[0]
    failed_steps = steps_of_event(task, failed_event.id)
    assert len(failed_steps) == 1
    step = failed_steps[0]
    assert step.node == "pr_description"
    assert step.status == "failed"
    assert "组装失败" in step.failure_reason
    assert task.status == "TESTED"  # 失败不留痕，任务状态不变


# ---------- 4. 索引决策 / 自动推进 / 重试埋点 ----------

def test_index_reuse_records_decision_and_context_steps(client):
    task = base_task(status="CREATED")
    head_commit = "c" * 40
    completed_index(head_commit)
    decision, index, reason = main.index_action_for_head(
        repository, head_commit=head_commit, branch=repository.default_branch, actor=developer,
        requested_at=task.created_at,
    )
    assert decision == "reuse"
    # DEV-017：自动检索需命中达到阈值的片段才会注入 CONTEXT_INDEXED，补一个与 Issue 同主题的切片
    main.CODE_CHUNKS["chunk_dev014_reuse"] = main.CodeChunkRecord(
        chunk_id="chunk_dev014_reuse",
        repository_id=repository.id,
        index_id=index.id,
        file_path="services/payment/callback.py",
        chunk_index=0,
        content="支付回调在弱网重试时保证幂等，避免重复入账的处理逻辑。",
        language="python",
        symbol_name="handle_payment_callback",
        start_line=1,
        end_line=10,
        commit_sha=head_commit,
        milvus_collection=main.index_collection_name(index.id),
        milvus_pk="chunk_dev014_reuse",
        checksum="dev014-reuse-checksum",
    )
    main.apply_task_index_decision(task, repository, decision=decision, index=index, head_commit=head_commit, reason=reason, created_at=task.created_at)
    decision_event = events_of(task, "INDEX_REUSE_CHECKED")[0]
    decision_steps = steps_of_event(task, decision_event.id)
    assert len(decision_steps) == 1
    assert decision_steps[0].node == "index_decision"
    assert decision_steps[0].status == "completed"
    assert index.id in decision_steps[0].output
    context_event = events_of(task, "CONTEXT_INDEXED")[0]
    context_steps = steps_of_event(task, context_event.id)
    assert len(context_steps) == 1
    assert context_steps[0].node == "context_fetch"
    assert context_steps[0].status == "completed"


def test_index_failed_records_failure_step(client):
    task = base_task(status="CREATED")
    now = datetime.now(timezone.utc)
    failed_index = main.RepositoryIndexRecord(
        id=f"IDX-{next(_task_seq)}",
        repository_id=repository.id,
        project_id=repository.project_id,
        base_branch=repository.default_branch,
        commit_sha="f" * 40,
        status="FAILED",
        error_message="模拟索引失败：源码仓库拉取超时",
        error_stage="clone",
        requested_by=developer.id,
        requested_by_name=developer.name,
        requested_at=now - timedelta(minutes=10),
        created_at=now - timedelta(minutes=10),
        updated_at=now - timedelta(minutes=9),
        finished_at=now - timedelta(minutes=9),
    )
    main.REPOSITORY_INDEXES[failed_index.id] = failed_index
    head_commit = "f" * 40
    decision, index, reason = main.index_action_for_head(
        repository, head_commit=head_commit, branch=repository.default_branch, actor=developer,
        requested_at=task.created_at,
    )
    assert decision == "index_failed"
    main.apply_task_index_decision(task, repository, decision=decision, index=index, head_commit=head_commit, reason=reason, created_at=task.created_at)
    failed_event = events_of(task, "TASK_FAILED")[0]
    failed_steps = steps_of_event(task, failed_event.id)
    assert len(failed_steps) == 1
    assert failed_steps[0].node == "index_decision"
    assert failed_steps[0].status == "failed"
    assert "模拟索引失败" in failed_steps[0].failure_reason


def test_auto_analysis_records_ai_analysis_step(client):
    task = base_task(status="ANALYSIS_QUEUED")
    task.next_action = "wait_analysis"
    task.created_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    main.progress_task_automation(task)
    analysis_event = events_of(task, "ANALYSIS_GENERATED")[0]
    analysis_steps = steps_of_event(task, analysis_event.id)
    assert len(analysis_steps) == 1
    step = analysis_steps[0]
    assert step.node == "ai_analysis"
    assert step.status == "completed"
    assert "第 1 版" in step.title
    assert "风险等级" in step.output


def test_analysis_context_missing_records_blocked_step(client):
    task = base_task(status="ANALYSIS_QUEUED", body="")
    task.title = "短"
    task.issue_snapshot.body = ""
    task.issue_snapshot.labels = []
    task.next_action = "wait_analysis"
    task.created_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    main.progress_task_automation(task)
    blocked_event = events_of(task, "TASK_MANUAL_REQUIRED")[0]
    blocked_steps = steps_of_event(task, blocked_event.id)
    assert len(blocked_steps) == 1
    assert blocked_steps[0].node == "analysis_blocked"
    assert blocked_steps[0].status == "blocked"
    assert "信息过少" in blocked_steps[0].blocked_reason
    assert task.status == "ANALYSIS_QUEUED"
    assert task.manual_required is True


def test_retry_keeps_old_steps_and_appends_new(client, monkeypatch):
    """重试语义：失败节点步骤保留，重试后新节点追加步骤；TASK_RETRIED 本身不挂步骤。"""
    task = base_task(status="FAILED")
    task.automation_state = "blocked"
    task.manual_required = True
    task.manual_reason = "diff_failed"
    task.next_action = "retry_or_cancel"
    task.failure_node = "Diff 生成"
    task.failure_reason = "模拟失败原因"
    head_commit = "c" * 40
    completed_index(head_commit)
    failed_event = main.mark_task_failed(task, node="Diff 生成", reason="模拟失败原因")
    main.record_execution_step(
        task,
        event=failed_event,
        node="diff_generation",
        title="生成 Diff v1",
        status="failed",
        input="审批计划：PLAN-1",
        failure_reason="模拟失败原因",
    )
    old_step_id = failed_event.execution_step_ids[0]

    monkeypatch.setattr(main, "fetch_gitee_branch_head", lambda repo, base_branch=None: head_commit)
    response = client.post(f"/api/tasks/{task.id}/retry")
    assert response.status_code == 200, response.text
    retried_event = events_of(task, "TASK_RETRIED")[0]
    assert retried_event.execution_step_ids == []
    # 旧失败步骤保留
    assert any(step.id == old_step_id for step in task.execution_steps)
    # 新索引复用决策步骤追加（同 Commit 的 COMPLETED 索引 → reuse）
    reuse_events = events_of(task, "INDEX_REUSE_CHECKED")
    assert len(reuse_events) == 1
    new_steps = steps_of_event(task, reuse_events[0].id)
    assert len(new_steps) == 1
    assert new_steps[0].node == "index_decision"
    assert new_steps[0].status == "completed"


# ---------- 5. 时间线 / 单事件聚合端点 ----------

def test_timeline_endpoint_orders_events_and_embeds_steps(client, approver_client):
    task = seed_analyzed_task()
    approve_to_approved(client, approver_client, task)
    client.post(f"/api/tasks/{task.id}/diffs/generate")
    response = timeline(client, task.id)
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["task_id"] == task.id
    events = payload["events"]
    assert [event["event_type"] for event in events] == ["PLAN_GENERATED", "PLAN_APPROVED", "DIFF_GENERATED"]
    # 事件升序 + 每条事件都有 execution_steps 键
    stamps = [event["created_at"] for event in events]
    assert stamps == sorted(stamps)
    assert all("execution_steps" in event for event in events)
    diff_embedded = next(event for event in events if event["event_type"] == "DIFF_GENERATED")
    assert len(diff_embedded["execution_steps"]) == 1
    step = diff_embedded["execution_steps"][0]
    for field in ("id", "task_id", "event_id", "node", "title", "status", "input", "output", "duration_ms", "created_at"):
        assert field in step
    assert step["node"] == "diff_generation"
    assert step["status"] == "completed"
    # 审批事件 steps 为空数组
    approved = next(event for event in events if event["event_type"] == "PLAN_APPROVED")
    assert approved["execution_steps"] == []
    assert timeline(client, "TASK-NOT-EXIST").status_code == 404


def test_single_event_endpoint_returns_event_with_steps(client, approver_client):
    task = seed_analyzed_task()
    approve_to_approved(client, approver_client, task)
    client.post(f"/api/tasks/{task.id}/diffs/generate")
    diff_event = events_of(task, "DIFF_GENERATED")[0]
    response = single_event(client, task.id, diff_event.id)
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["event_type"] == "DIFF_GENERATED"
    assert payload["id"] == diff_event.id
    assert len(payload["execution_steps"]) == 1
    assert payload["execution_steps"][0]["node"] == "diff_generation"
    # 未知事件 / 跨任务事件按不存在处理
    assert single_event(client, task.id, "task_evt_no_such").status_code == 404
    assert single_event(client, "TASK-OTHER", diff_event.id).status_code == 404


def test_timeline_and_event_routes_are_read_only(client, approver_client, admin_client):
    """普通用户不能修改审计日志：无任何写路由，POST 一律 405；写只发生在业务动作内部。"""
    task = seed_task_with_diff()
    assert client.post(f"/api/tasks/{task.id}/timeline").status_code == 405
    assert client.post(f"/api/tasks/{task.id}/events/whatever").status_code == 405
    # 初始任务不含任何事件与步骤：不存在业务动作之外的写入口
    assert task.events == [] and task.execution_steps == []
    # 全局审计查询保留 project:manage（admin），普通用户 403
    assert approver_client.get("/api/audit-events").status_code == 403
    assert admin_client.get("/api/audit-events").status_code == 200
