"""DEV-011 Diff 生成与展示模块测试套件。

覆盖：生成门禁（WAITING_APPROVAL/FAILED/旧版计划 409、无计划 409）、权限矩阵
（developer 可生成 / approver、lead 无 task:operate 403）、成功流转（DIFF_GENERATED +
next_action 测试阶段 + 事件落库 + 产物结构：修改/新增/删除三类文件、行数一致、patch 可复制）、
重复生成防重、真实执行失败保留 real_failure_reason 并演示兜底、真实与兜底都失败任务 FAILED、
GET 列表/单文件/404、旧版计划 Diff 无法冒充最新、任务详情响应内嵌 diffs。
"""
from datetime import datetime, timedelta, timezone
from itertools import count

import pytest
from fastapi.testclient import TestClient

from app import main
from app.services import diff_service

NEUTRAL_BODY = "列表页在窄屏下布局错乱，需要调整样式并补充回归。"
_task_seq = count(9600)

repository = main.REPOSITORIES["repo_payment_service"]
developer = main.USERS["user_developer"]
owner = main.USERS["user_approver"]
DIFF_DEMO_STATUSES = {"SUCCEEDED", "FALLBACK", "FAILED"}


def authed_client(account: str, password: str) -> TestClient:
    test_client = TestClient(main.app)
    login = test_client.post(
        "/api/auth/login",
        json={"account": account, "password": password},
    )
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


@pytest.fixture()
def lead_client() -> TestClient:
    return authed_client("lead@company.com", "Lead123!")


def snapshot_of(title: str, body: str) -> main.IssueSnapshotRecord:
    return main.IssueSnapshotRecord(
        title=title,
        body=body,
        comments=[],
        labels=["ui"],
        raw_response={},
        fetched_at=datetime.now(timezone.utc),
    )


def seed_analyzed_task(
    *,
    issue_id: str | None = None,
    title: str = "优化列表页样式",
    body: str = NEUTRAL_BODY,
    owner: main.UserRecord | None = None,
) -> main.TaskRecord:
    now = datetime.now(timezone.utc)
    task = main.TaskRecord(
        id=f"TASK-{next(_task_seq)}",
        version=1,
        project_id=repository.project_id,
        project_name=main.PROJECTS[repository.project_id].name,
        repository_id=repository.id,
        repository_name=repository.name,
        repository_url=repository.url,
        issue_id=issue_id or f"IDEV011{next(_task_seq) % 100}",
        title=title,
        status="CREATED",
        base_branch=repository.default_branch,
        test_command=repository.test_command,
        approval_owner_id=owner.id if owner else None,
        approval_owner_name=owner.name if owner else None,
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
    analysis = main.generate_mock_analysis(task)
    for question in analysis.questions:
        question.answered = True
        question.answer = question.default_answer or "按默认口径处理"
    main.attach_analysis(task, analysis)
    return task


def approve_task_to_approved(client: TestClient, approver: TestClient, task: main.TaskRecord) -> dict:
    """走通 ANALYZED → 计划生成(WAITING_APPROVAL) → 审批通过(APPROVED) 主链，返回任务 dict。"""
    waiting = client.post(f"/api/tasks/{task.id}/plans")
    assert waiting.status_code == 200, waiting.text
    plan_id = waiting.json()["data"]["plans"][0]["id"]
    approved = approver.post(f"/api/tasks/{task.id}/plans/{plan_id}/approve", json={"comment": "范围可控，审批通过"})
    assert approved.status_code == 200, approved.text
    data = approved.json()["data"]
    assert data["status"] == "APPROVED"
    assert data["next_action"] == "auto_generate_diff"
    return data


def generate_diff(client: TestClient, task_id: str):
    return client.post(f"/api/tasks/{task_id}/diffs/generate")


def diff_events(task: main.TaskRecord, event_type: str) -> list[dict]:
    return [event for event in task.events if event.event_type == event_type]


# ---------- 生成门禁 ----------

def test_generate_blocked_for_unapproved_status(client, approver_client):
    # 状态门禁测试必须用具备 task:operate 的 developer/admin（approver/lead 在权限层即被 403）
    waiting_task = seed_analyzed_task(issue_id="IDEV0111", owner=owner)
    waiting = client.post(f"/api/tasks/{waiting_task.id}/plans")
    assert waiting.status_code == 200
    assert waiting_task.status == "WAITING_APPROVAL"
    blocked = generate_diff(client, waiting_task.id)
    assert blocked.status_code == 409
    assert "审批通过" in blocked.json()["detail"]["message"]

    failed_task = seed_analyzed_task(issue_id="IDEV0112", owner=owner)
    failed_task.status = "FAILED"
    failed_task.manual_required = True
    failed_task.manual_reason = "plan_rejected"
    blocked = generate_diff(client, failed_task.id)
    assert blocked.status_code == 409
    assert "审批通过" in blocked.json()["detail"]["message"]


def test_generate_blocked_without_plan_or_old_approved_plan(client, approver_client):
    no_plan_task = seed_analyzed_task(issue_id="IDEV0113", owner=owner)
    no_plan_task.status = "APPROVED"
    no_plan_task.automation_state = "blocked"
    no_plan_task.manual_reason = "none"
    no_plan_task.next_action = "auto_generate_diff"
    blocked = generate_diff(client, no_plan_task.id)
    assert blocked.status_code == 409
    assert "没有修改计划" in blocked.json()["detail"]["message"]

    approved_data = approve_task_to_approved(client, approver_client, seed_analyzed_task(issue_id="IDEV0114", owner=owner))
    task = main.TASKS[approved_data["id"]]
    older = task.plans[0]
    newer = older.model_copy(update={"id": f"PLAN-{task.id.removeprefix('TASK-')}-2", "version": 2, "status": "WAITING_APPROVAL"})
    task.plans.append(newer)
    task.updated_at = datetime.now(timezone.utc)
    blocked = generate_diff(client, task.id)
    assert blocked.status_code == 409
    assert "旧版本计划" in blocked.json()["detail"]["message"]


# ---------- 权限矩阵 ----------

def test_generate_permission_matrix(client, approver_client, lead_client, admin_client):
    task = seed_analyzed_task(issue_id="IDEV0115", owner=owner)
    approve_task_to_approved(client, approver_client, task)
    assert generate_diff(client, task.id).status_code == 200

    admin_task = seed_analyzed_task(issue_id="IDEV0116", owner=owner)
    approve_task_to_approved(client, approver_client, admin_task)
    assert generate_diff(admin_client, admin_task.id).status_code == 200

    approver_task = seed_analyzed_task(issue_id="IDEV0116", owner=owner)
    approve_task_to_approved(client, approver_client, approver_task)
    assert approver_client.post(f"/api/tasks/{approver_task.id}/diffs/generate").status_code == 403
    assert lead_client.post(f"/api/tasks/{approver_task.id}/diffs/generate").status_code == 403
    # diff:view 全员可见（列表可读，与可生成权限分离）
    assert approver_client.get(f"/api/tasks/{approver_task.id}/diffs").status_code == 200
    assert lead_client.get(f"/api/tasks/{approver_task.id}/diffs").status_code == 200


# ---------- 成功生成与产物结构 ----------

def test_generate_success_flow_and_artifact_shape(client, approver_client):
    task = seed_analyzed_task(issue_id="IDEV0117", owner=owner)
    approve_task_to_approved(client, approver_client, task)

    response = generate_diff(client, task.id)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "DIFF_GENERATED"
    assert data["next_action"] == "auto_run_test"
    assert data["manual_required"] is False
    assert data["manual_reason"] == "none"

    diffs = data["diffs"]
    assert len(diffs) == 1
    artifact = diffs[0]
    assert artifact["task_id"] == task.id
    assert artifact["plan_id"] == task.plans[0].id
    assert artifact["plan_version"] == 1
    assert artifact["generation_status"] == "FALLBACK"
    assert artifact["execution_mode"] == "DEMO_FALLBACK"
    assert artifact["is_demo_fallback"] is True
    assert artifact["fallback_reason"]
    assert artifact["real_failure_reason"] is None
    assert artifact["execution_step"]
    assert artifact["generated_at"]

    files = artifact["files"]
    assert len(files) == 3
    assert {file["change_type"] for file in files} == {"modify", "add", "delete"}
    for file in files:
        assert file["added_lines"] == len(file["after"])
        assert file["deleted_lines"] == len(file["before"])
        assert file["patch"].startswith(f"--- a/{file['file_path']}")
        assert f"+++ b/{file['file_path']}" in file["patch"]
    assert "演示兜底" in artifact["test_summary"]
    assert "演示兜底" in artifact["pr_description_draft"]

    events = diff_events(task, "DIFF_GENERATED")
    assert len(events) == 1
    joined = "\n".join(events[0].diff_summary)
    for expected in [f"任务 ID：{task.id}", "计划 ID：", "计划版本：v1", "执行步骤：", "生成模式：DEMO_FALLBACK", "演示兜底：是"]:
        assert expected in joined


def test_generate_duplicate_attempt_keeps_single_record(client, approver_client):
    task = seed_analyzed_task(issue_id="IDEV0118", owner=owner)
    approve_task_to_approved(client, approver_client, task)
    first = generate_diff(client, task.id)
    assert first.status_code == 200

    # 成功已把任务流转到 DIFF_GENERATED：再次生成被状态门禁拦下，不产生重复产物
    second = generate_diff(client, task.id)
    assert second.status_code == 409
    assert len(task.diffs) == 1
    assert len(diff_events(task, "DIFF_GENERATED")) == 1

    # 防重兜底：即便状态被人为停留在 APPROVED，已有产物时也拒绝重复生成
    crafted = seed_analyzed_task(issue_id="IDEV0119", owner=owner)
    approve_task_to_approved(client, approver_client, crafted)
    crafted.status = "APPROVED"
    existing = crafted.diffs[0] if crafted.diffs else None
    artifact = main.DiffArtifact(
        id=f"DIFF-{crafted.id.removeprefix('TASK-')}-v1",
        task_id=crafted.id,
        plan_id=crafted.plans[0].id,
        plan_version=1,
        execution_step="步骤 3：补充测试并自检",
        generation_status="FALLBACK",
        execution_mode="DEMO_FALLBACK",
        is_demo_fallback=True,
        fallback_reason="pre-seeded",
        generated_at=datetime.now(timezone.utc),
        files=[],
    )
    assert existing is None
    crafted.diffs.append(artifact)
    blocked = generate_diff(client, crafted.id)
    assert blocked.status_code == 409
    assert "请勿重复触发" in blocked.json()["detail"]["message"]
    assert len(crafted.diffs) == 1


def test_generate_task_listing_and_single_file(client, approver_client):
    task = seed_analyzed_task(issue_id="IDEV0120", owner=owner)
    approve_task_to_approved(client, approver_client, task)
    generate_diff(client, task.id)

    listed = client.get(f"/api/tasks/{task.id}/diffs")
    assert listed.status_code == 200
    artifacts = listed.json()["data"]
    assert len(artifacts) == 1
    file_id = artifacts[0]["files"][0]["id"]

    single = client.get(f"/api/tasks/{task.id}/diffs/{file_id}")
    assert single.status_code == 200
    file = single.json()["data"]
    assert file["id"] == file_id
    assert file["patch"]

    missing = client.get(f"/api/tasks/{task.id}/diffs/DIFF-not-exist")
    assert missing.status_code == 404
    unknown_task = client.get("/api/tasks/TASK-NOT-FOUND/diffs")
    assert unknown_task.status_code == 404
    assert client.get("/api/tasks/TASK-NOT-FOUND/diffs/x").status_code == 404


def test_diffs_empty_for_unapproved_task(client, approver_client):
    task = seed_analyzed_task(issue_id="IDEV0121", owner=owner)
    waiting = client.post(f"/api/tasks/{task.id}/plans")
    assert waiting.status_code == 200
    listed = approver_client.get(f"/api/tasks/{task.id}/diffs")
    assert listed.status_code == 200
    assert listed.json()["data"] == []
    assert client.get(f"/api/tasks/{task.id}").json()["data"]["diffs"] == []


def test_task_detail_response_embeds_diffs(client, approver_client):
    task = seed_analyzed_task(issue_id="IDEV0122", owner=owner)
    approve_task_to_approved(client, approver_client, task)
    generate_diff(client, task.id)

    detail = client.get(f"/api/tasks/{task.id}")
    assert detail.status_code == 200
    data = detail.json()["data"]
    assert len(data["diffs"]) == 1
    assert data["diffs"][0]["generation_status"] == "FALLBACK"


# ---------- 真实失败保留原因 + 演示兜底 / 双失败 FAILED ----------

def test_real_failure_keeps_reason_and_uses_demo_fallback(client, approver_client, monkeypatch):
    task = seed_analyzed_task(issue_id="IDEV0123", owner=owner)
    approve_task_to_approved(client, approver_client, task)
    real_error = "真实执行失败：runner exit=1（模拟命令执行异常）"
    monkeypatch.setattr(diff_service, "attempt_real_diff", lambda t, p: (None, real_error))

    response = generate_diff(client, task.id)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "DIFF_GENERATED"
    artifact = data["diffs"][0]
    assert artifact["generation_status"] == "FALLBACK"
    assert artifact["is_demo_fallback"] is True
    assert artifact["real_failure_reason"] == real_error
    assert artifact["fallback_reason"] == real_error
    assert len(artifact["files"]) == 3
    assert "真实执行失败原因" in diff_events(task, "DIFF_GENERATED")[0].diff_summary[-1]


def test_real_and_fallback_both_fail_marks_task_failed(client, approver_client, monkeypatch):
    task = seed_analyzed_task(issue_id="IDEV0124", owner=owner)
    approve_task_to_approved(client, approver_client, task)
    real_error = "真实执行失败：clone 超时（模拟）"
    monkeypatch.setattr(diff_service, "attempt_real_diff", lambda t, p: (None, real_error))
    monkeypatch.setattr(diff_service, "build_demo_diff", lambda t, p: None)

    response = generate_diff(client, task.id)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "FAILED"
    assert data["failure_node"] == "Diff 生成"
    assert real_error in data["failure_reason"]
    assert data["manual_reason"] == "diff_failed"
    assert data["next_action"] == "retry_or_cancel"

    artifact = data["diffs"][0]
    assert artifact["generation_status"] == "FAILED"
    assert artifact["real_failure_reason"] == real_error
    assert "演示兜底产物构造失败" in artifact["fallback_reason"]
    assert artifact["files"] == []
    assert len(diff_events(task, "DIFF_GENERATION_FAILED")) == 1
    assert len(diff_events(task, "TASK_FAILED")) == 1
    assert task.failed_at is not None


# ---------- 旧版产物不能冒充最新 ----------

def test_old_plan_diff_cannot_masquerade_as_latest(client, approver_client):
    """重写产生 v2 后，仅 v2 可生成；v1 的 Diff 即使存在也不会被当作当前产物返回。"""
    task = seed_analyzed_task(issue_id="IDEV0125", owner=owner)
    approve_task_to_approved(client, approver_client, task)

    # 人为回退状态，模拟“旧版已生成 Diff 后再次审批新版”的审计场景
    task.status = "APPROVED"
    task.plans.append(task.plans[0].model_copy(update={"id": f"PLAN-{task.id.removeprefix('TASK-')}-2", "version": 2, "status": "APPROVED"}))
    latest = main.find_latest_plan(task)
    assert latest.version == 2

    response = generate_diff(client, task.id)
    assert response.status_code == 200, response.text
    artifact = response.json()["data"]["diffs"][-1]
    assert artifact["plan_version"] == 2
    assert artifact["plan_id"] == latest.id
    assert len([item for item in task.diffs if item.plan_version == 1]) == 0

    listed = client.get(f"/api/tasks/{task.id}/diffs").json()["data"]
    assert listed[0]["plan_version"] == 2
