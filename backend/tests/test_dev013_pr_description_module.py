"""DEV-013 PR 描述生成模块测试套件。

覆盖：权限矩阵（approver/lead 403）、门禁（无 Diff / 非 TESTED|TEST_FAILED 状态 /
无结论测试记录 / 计划缺失 409）、成功生成（TESTED→六段字段落库 + 关联 plan/diff/test +
generated_by + 任务状态不变 + PR_DESC_GENERATED 事件）、内容真实性（字段来源于
计划/Diff/测试记录真实数据）、输入未变防重复（409）、测试结果变化后重新生成（新版本 +
旧版保留历史）、Diff/计划版本变化后 stale 标记与重新生成、生成失败（FAILED 记录留痕
不影响任务 + 修复后重试成功）、总览/版本详情端点与 404、事件回放字段。
"""
from datetime import datetime, timedelta, timezone
from itertools import count

import pytest
from fastapi.testclient import TestClient

from app import main

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
def lead_client() -> TestClient:
    return authed_client("lead@company.com", "Lead123!")


def snapshot_of(title: str, body: str) -> main.IssueSnapshotRecord:
    return main.IssueSnapshotRecord(
        title=title,
        body=body,
        comments=[],
        labels=["backend"],
        raw_response={},
        fetched_at=datetime.now(timezone.utc),
    )


def seed_task(
    *,
    status: str = "TESTED",
    plan_version: int = 1,
    records: list[tuple[str, int]] | None = None,
    with_plan: bool = True,
) -> main.TaskRecord:
    """构造含计划/Diff/测试记录的任务；records 为 (测试状态, 尝试次数) 列表，默认一条通过记录。"""
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
        issue_id=f"IDEV013{next(_task_seq) % 100}",
        title="支付回调幂等修复",
        status=status,
        risk_level="high",
        risk_source="plan_approval",
        base_branch=repository.default_branch,
        test_command=repository.test_command,
        approval_owner_id=owner.id,
        approval_owner_name=owner.name,
        work_branch=f"agent/main/issue-dev013-v1",
        issue_snapshot=snapshot_of("支付回调幂等修复", NEUTRAL_BODY),
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
    if with_plan:
        plan = main.ModificationPlan(
            id=f"PLAN-{task_id.removeprefix('TASK-')}-v{plan_version}",
            task_id=task.id,
            version=plan_version,
            goal="修复回调幂等并补充回归覆盖",
            file_scope=[
                main.PlanFileScope(
                    file_path="src/handler.py",
                    change_type="modify",
                    reason="修复重复入账",
                )
            ],
            steps=[main.PlanStep(order=1, title="校验订单状态", description="重复入账前先校验状态", target_files=["src/handler.py"])],
            test_suggestions=["pytest tests/ -q"],
            assumptions=[],
            rollback_plan=["回滚 src/handler.py 至 base_commit"],
            risk_level="high",
            risk_reason="高风险改动，需要审批",
            generated_from_analysis_revision=1,
            generated_from_context_ids=[],
            provider="mvp-rule-based",
            status="APPROVED",
            risk_adjust_reason=None,
            approved_by_id=owner.id,
            approved_by_name=owner.name,
            approved_at=now - timedelta(minutes=4, seconds=30),
            created_by=developer.id,
            created_by_name=developer.name,
            created_at=now - timedelta(minutes=4),
        )
        artifact = main.DiffArtifact(
            id=f"DIFF-{task_id.removeprefix('TASK-')}-v{plan_version}",
            task_id=task.id,
            plan_id=plan.id,
            plan_version=plan_version,
            execution_step="步骤 1：校验订单状态",
            generation_status="SUCCEEDED",
            execution_mode="DEMO_FALLBACK",
            is_demo_fallback=True,
            fallback_reason="MVP 未接入真实执行环境",
            real_failure_reason=None,
            generated_at=now - timedelta(minutes=3),
            files=[
                main.DiffFileRecord(
                    id=f"DIFF-{task_id.removeprefix('TASK-')}-v{plan_version}-1",
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
            pr_description_draft=None,
        )
        task.plans.append(plan)
        task.diffs.append(artifact)
    for index, (run_status, attempt) in enumerate(records or [("passed", 1)], start=1):
        task.test_runs.append(
            main.TestRunRecord(
                id=f"TEST-{task_id.removeprefix('TASK-')}-{attempt}",
                task_id=task.id,
                diff_artifact_id=task.diffs[-1].id,
                plan_id=task.diffs[-1].plan_id,
                plan_version=task.diffs[-1].plan_version,
                command=task.test_command,
                exit_code=0 if run_status == "passed" else 1,
                stdout="12 项测试通过，0 项失败。" if run_status == "passed" else "",
                stderr="" if run_status == "passed" else "FAILED tests/test_callback.py - AssertionError: 重复入账\n",
                status=run_status,
                duration=1.0,
                attempt=attempt,
                started_at=now - timedelta(minutes=2, seconds=index),
                finished_at=now - timedelta(minutes=1, seconds=index),
                failure_reason=None if run_status == "passed" else "AssertionError: 重复入账",
                blocked_reason=None,
                runner_mode="mocked_runner",
            )
        )
    main.TASKS[task.id] = task
    return task


def generate(client: TestClient, task_id: str):
    return client.post(f"/api/tasks/{task_id}/pr-description/generate")


def overview(client: TestClient, task_id: str):
    return client.get(f"/api/tasks/{task_id}/pr-description")


def run_events(task: main.TaskRecord, event_type: str) -> list[dict]:
    return [event for event in task.events if event.event_type == event_type]


def pr_versions(task: main.TaskRecord) -> list[main.PrDescriptionRecord]:
    return sorted(task.pr_descriptions, key=lambda record: record.version)


# ---------- 门禁与权限 ----------

def test_generate_blocked_for_approver_and_lead(approver_client, lead_client):
    task = seed_task()
    for forbidding_client in (approver_client, lead_client):
        response = generate(forbidding_client, task.id)
        assert response.status_code == 403
    assert task.pr_descriptions == []


def test_generate_blocked_without_diff(client):
    task = seed_task()
    task.diffs.clear()
    response = generate(client, task.id)
    assert response.status_code == 409
    assert "没有可用 Diff" in response.json()["detail"]["message"]


def test_generate_blocked_when_no_test_result_yet(client):
    task = seed_task(status="ANALYZED", records=[])
    response = generate(client, task.id)
    assert response.status_code == 409
    assert "仅测试完成或测试失败待重试" in response.json()["detail"]["message"]


def test_generate_blocked_when_only_running_record(client):
    task = seed_task(status="TEST_RUNNING", records=[])
    task.test_runs.append(
        main.TestRunRecord(
            id="TEST-1", task_id=task.id, diff_artifact_id=task.diffs[0].id,
            plan_id=task.diffs[0].plan_id, plan_version=1, command=task.test_command,
            exit_code=None, status="running", attempt=1,
            started_at=datetime.now(timezone.utc) - timedelta(seconds=30),
            finished_at=None, runner_mode="mocked_runner",
        )
    )
    response = generate(client, task.id)
    assert response.status_code == 409


def test_generate_blocked_when_plan_missing(client):
    task = seed_task()
    task.plans.clear()
    response = generate(client, task.id)
    assert response.status_code == 409
    assert "修改计划不存在" in response.json()["detail"]["message"]


# ---------- 成功路径与字段落库 ----------

def test_generate_success_flow_and_record_fields(client):
    task = seed_task()
    response = generate(client, task.id)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    # 生成不改变任务状态（PR 描述是产物，不是任务阶段）
    assert data["status"] == "TESTED"
    assert data["next_action"] == "watch_test_result"
    records = data["pr_descriptions"]
    assert len(records) == 1
    record = records[0]
    assert record["id"] == f"PRD-{task.id.removeprefix('TASK-')}-1"
    assert record["version"] == 1
    assert record["generation_status"] == "GENERATED"
    assert record["failure_reason"] is None
    assert record["plan_id"] == task.plans[0].id
    assert record["plan_version"] == 1
    assert record["diff_artifact_id"] == task.diffs[0].id
    assert record["test_run_id"] == task.test_runs[0].id
    assert record["generated_by"] == developer.id
    assert record["generated_by_name"] == developer.name
    for field in ("background", "change_summary", "impact_scope", "test_result", "risk_notes", "rollback_notes"):
        assert record[field], f"字段 {field} 不应为空"
    assert run_events(task, "PR_DESC_GENERATED")


def test_generate_content_derives_from_real_records(client):
    task = seed_task()
    generate(client, task.id)
    record = task.pr_descriptions[0]
    # 内容来自真实落库产物，不是固定模板
    assert "支付回调幂等修复" in record.background
    assert "修复回调幂等并补充回归覆盖" in record.change_summary
    assert "src/handler.py" in record.impact_scope
    assert "通过" in record.test_result and "exit 0" in record.test_result
    assert task.test_command in record.test_result
    assert "高风险改动" in record.risk_notes
    assert "回滚 src/handler.py" in record.rollback_notes


def test_generate_duplicate_with_same_inputs_rejected(client):
    task = seed_task()
    first = generate(client, task.id)
    assert first.status_code == 200
    second = generate(client, task.id)
    assert second.status_code == 409
    assert "无需重复生成" in second.json()["detail"]["message"]
    assert len(task.pr_descriptions) == 1


# ---------- 重新生成：测试结果 / Diff 与计划版本变化 ----------

def test_regenerate_after_test_result_change_keeps_history(client):
    task = seed_task(status="TEST_FAILED", records=[("failed", 1)])
    first = generate(client, task.id)
    assert first.status_code == 200
    assert task.pr_descriptions[0].test_run_id == task.test_runs[0].id
    # 重试通过：追加新的 passed 记录（模拟 /tests/run 成功路径），任务回到 TESTED
    task.status = "TESTED"
    task.test_runs.append(
        main.TestRunRecord(
            id="TEST-2", task_id=task.id, diff_artifact_id=task.diffs[0].id,
            plan_id=task.diffs[0].plan_id, plan_version=1, command=task.test_command,
            exit_code=0, stdout="12 项测试通过，0 项失败。", stderr="", status="passed",
            duration=1.2, attempt=2,
            started_at=datetime.now(timezone.utc) - timedelta(seconds=40),
            finished_at=datetime.now(timezone.utc) - timedelta(seconds=10),
            runner_mode="mocked_runner",
        )
    )
    overview_before = overview(client, task.id)
    assert overview_before.json()["data"]["stale"] is True
    second = generate(client, task.id)
    assert second.status_code == 200
    versions = pr_versions(task)
    assert [item.version for item in versions] == [1, 2]
    assert versions[1].test_run_id == "TEST-2"
    assert versions[0].test_run_id == task.test_runs[0].id  # 旧版不覆盖
    data = overview(client, task.id).json()["data"]
    assert data["status"] == "GENERATED"
    assert data["stale"] is False
    assert data["current"]["version"] == 2
    assert data["versions"][0]["version"] == 2
    assert len(run_events(task, "PR_DESC_GENERATED")) == 2


def test_regenerate_after_plan_and_diff_version_change(client):
    task = seed_task()
    generate(client, task.id)
    assert len(task.pr_descriptions) == 1
    # 计划 v2 审批通过并生成新 Diff 与测试（模拟后续重写版本主链）
    now = datetime.now(timezone.utc)
    plan2 = main.ModificationPlan(
        id=f"PLAN-{task.id.removeprefix('TASK-')}-v2", task_id=task.id, version=2,
        goal="重写版本：修复幂等并补全边界", file_scope=[], steps=[],
        test_suggestions=[], assumptions=[], rollback_plan=["回滚至 base_commit"],
        risk_level="medium", risk_reason="中风险", generated_from_analysis_revision=2,
        generated_from_context_ids=[], provider="mvp-rule-based", status="APPROVED",
        approved_by_id=owner.id, approved_by_name=owner.name,
        approved_at=now - timedelta(seconds=30), created_by=developer.id,
        created_by_name=developer.name, created_at=now - timedelta(minutes=1),
    )
    artifact2 = main.DiffArtifact(
        id=f"DIFF-{task.id.removeprefix('TASK-')}-v2", task_id=task.id,
        plan_id=plan2.id, plan_version=2, execution_step="步骤 1：重写边界处理",
        generation_status="SUCCEEDED", execution_mode="DEMO_FALLBACK", is_demo_fallback=True,
        fallback_reason="MVP 未接入真实执行环境", real_failure_reason=None,
        generated_at=now - timedelta(seconds=20), files=[
            main.DiffFileRecord(
                id=f"DIFF-{task.id.removeprefix('TASK-')}-v2-1", file_path="src/boundary.py",
                change_type="add", patch="+new file", added_lines=3, deleted_lines=0,
                before=[], after=["a", "b", "c"],
            )
        ],
    )
    task.plans.append(plan2)
    task.diffs.append(artifact2)
    task.test_runs.append(
        main.TestRunRecord(
            id="TEST-2", task_id=task.id, diff_artifact_id=artifact2.id,
            plan_id=plan2.id, plan_version=2, command=task.test_command, exit_code=0,
            stdout="5 项测试通过。", stderr="", status="passed", duration=0.9, attempt=2,
            started_at=now - timedelta(seconds=15), finished_at=now - timedelta(seconds=5),
            runner_mode="mocked_runner",
        )
    )
    task.status = "TESTED"
    data = overview(client, task.id).json()["data"]
    assert data["stale"] is True
    assert "计划" in data["stale_reason"] and "Diff" in data["stale_reason"] and "测试" in data["stale_reason"]
    response = generate(client, task.id)
    assert response.status_code == 200
    versions = pr_versions(task)
    assert [item.version for item in versions] == [1, 2]
    assert versions[1].plan_version == 2
    assert versions[1].diff_artifact_id == artifact2.id
    assert versions[1].test_run_id == "TEST-2"
    # 旧版仍可通过版本端点读取
    detail = client.get(f"/api/tasks/{task.id}/pr-description/versions/{versions[0].id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["plan_version"] == 1


# ---------- 生成失败留痕与重试 ----------

def test_generate_failure_recorded_without_touching_task(client, monkeypatch):
    task = seed_task()
    monkeypatch.setattr(
        main.pr_description_service, "compose_pr_description",
        lambda *args, **kwargs: None,
    )
    response = generate(client, task.id)
    assert response.status_code == 200
    assert "生成失败" in response.json()["message"]
    assert task.status == "TESTED"  # 失败不影响任务状态
    record = task.pr_descriptions[0]
    assert record.generation_status == "FAILED"
    assert "组装失败" in record.failure_reason
    assert run_events(task, "PR_DESC_FAILED")
    # 修复后可重新生成新版本
    monkeypatch.setattr(
        main.pr_description_service, "compose_pr_description",
        lambda *args, **kwargs: {field: f"内容-{field}" for field in
                                 ("background", "change_summary", "impact_scope", "test_result", "risk_notes", "rollback_notes")},
    )
    retry = generate(client, task.id)
    assert retry.status_code == 200
    versions = pr_versions(task)
    assert [item.version for item in versions] == [1, 2]
    assert versions[1].generation_status == "GENERATED"


# ---------- 总览 / 版本详情 / 事件 ----------

def test_overview_pending_when_no_record(client):
    task = seed_task()
    response = overview(client, task.id)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "PENDING"
    assert data["current"] is None
    assert data["stale"] is False
    assert data["versions"] == []


def test_overview_failed_status_when_latest_record_failed(client, monkeypatch):
    task = seed_task()
    monkeypatch.setattr(
        main.pr_description_service, "compose_pr_description",
        lambda *args, **kwargs: None,
    )
    generate(client, task.id)
    data = overview(client, task.id).json()["data"]
    assert data["status"] == "FAILED"
    assert data["current"]["generation_status"] == "FAILED"


def test_view_permissions_and_404s(client, approver_client):
    task = seed_task()
    generate(client, task.id)
    # approver 有 diff:view，可读总览与版本
    assert overview(approver_client, task.id).status_code == 200
    missing_version = client.get(f"/api/tasks/{task.id}/pr-description/versions/PRD-NOPE")
    assert missing_version.status_code == 404
    unknown_task = overview(client, "TASK-9999")
    assert unknown_task.status_code == 404
    unknown_generate = generate(client, "TASK-9999")
    assert unknown_generate.status_code == 404


def test_generation_events_visible_in_task_detail(client):
    task = seed_task()
    generate(client, task.id)
    detail = client.get(f"/api/tasks/{task.id}")
    assert detail.status_code == 200
    event_types = [event["event_type"] for event in detail.json()["data"]["events"]]
    assert "PR_DESC_GENERATED" in event_types
    generated = run_events(task, "PR_DESC_GENERATED")[0]
    summary_text = "；".join(generated.diff_summary)
    assert "第 1 版" in summary_text
    assert task.test_runs[0].id in summary_text
    assert task.diffs[0].id in summary_text
    assert "周航" in summary_text
