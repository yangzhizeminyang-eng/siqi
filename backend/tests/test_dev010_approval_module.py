"""DEV-010 审批模块测试套件。

覆盖：审批中心 owner 范围（approver 只见指派给自己的 / admin 全量 / lead 空 / developer 403）、
审批动作归属校验（非 owner 403、admin 越权、rewrite/reject 同规则）、审批人指派端点
（权限矩阵、目标角色校验、状态守卫、事件留痕、改派后队列可见性变化）、
仓库默认审批人可空 + 无 owner 生成计划 409 → 指派后成功、审批记录落库
（approved_by/approved_at/approval_comment、risk_adjust_from 原始档位）。
"""
from datetime import datetime, timedelta, timezone
from itertools import count

import pytest
from fastapi.testclient import TestClient

from app import main

NEUTRAL_BODY = "列表页在窄屏下布局错乱，需要调整样式并补充回归。"
_task_seq = count(9500)

repository = main.REPOSITORIES["repo_payment_service"]
developer = main.USERS["user_developer"]
owner = main.USERS["user_approver"]
second_owner = main.UserRecord(
    id="user_approver2",
    name="赵敏",
    email="approver2@company.com",
    role="approver",
    password_hash=main.hash_password("Approver123!"),
)


@pytest.fixture(scope="module", autouse=True)
def inject_second_approver():
    main.USERS[second_owner.id] = second_owner
    main.USERS_BY_EMAIL[second_owner.email] = second_owner
    yield
    main.USERS.pop(second_owner.id, None)
    main.USERS_BY_EMAIL.pop(second_owner.email, None)


def authed_client(account: str, password: str) -> TestClient:
    test_client = TestClient(main.app)
    login = test_client.post(
        "/api/auth/login",
        json={"account": account, "password": password},
    )
    assert login.status_code == 200
    return test_client


@pytest.fixture()
def admin_client() -> TestClient:
    return authed_client("admin@company.com", "Admin123!")


@pytest.fixture()
def approver_client() -> TestClient:
    return authed_client("approver@company.com", "Approver123!")


@pytest.fixture()
def approver2_client() -> TestClient:
    return authed_client("approver2@company.com", "Approver123!")


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
    answered_blocking: bool = True,
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
        issue_id=issue_id or f"IPLAN10{next(_task_seq) % 100}",
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
    if answered_blocking:
        for question in analysis.questions:
            question.answered = True
            question.answer = question.default_answer or "按默认口径处理"
    main.attach_analysis(task, analysis)
    return task


def generate_waiting_plan(client: TestClient, task: main.TaskRecord) -> dict:
    response = client.post(f"/api/tasks/{task.id}/plans")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "WAITING_APPROVAL"
    return data


def pending_ids(client: TestClient) -> list[str]:
    response = client.get("/api/approvals/pending")
    assert response.status_code == 200
    return [item["id"] for item in response.json()["data"]]


def approve_payload(*, comment: str | None = None, risk_level: str | None = None, adjust_reason: str | None = None) -> dict:
    return {
        "comment": comment,
        "risk_level": risk_level,
        "risk_adjust_reason": adjust_reason,
    }


# ---------- 审批中心队列（owner 范围） ----------

def test_pending_approvals_scoped_to_assigned_owner(client, approver_client, approver2_client, admin_client):
    mine = seed_analyzed_task(issue_id="IPR0101", owner=owner)
    theirs = seed_analyzed_task(issue_id="IPR0102", owner=second_owner)
    generate_waiting_plan(client, mine)
    generate_waiting_plan(client, theirs)

    assert pending_ids(approver_client) == [mine.id]
    assert pending_ids(approver2_client) == [theirs.id]
    assert sorted(pending_ids(admin_client)) == sorted([mine.id, theirs.id])


def test_pending_approvals_lead_empty_and_developer_forbidden(client, lead_client):
    seed_analyzed_task(issue_id="IPR0103", owner=owner)
    generate_waiting_plan(client, seed_analyzed_task(issue_id="IPR0104", owner=owner))
    response = lead_client.get("/api/approvals/pending")
    assert response.status_code == 200
    assert response.json()["data"] == []
    denied = client.get("/api/approvals/pending")
    assert denied.status_code == 403


# ---------- 审批动作归属校验 ----------

def test_approval_actions_require_assigned_owner(client, approver_client, approver2_client):
    task = seed_analyzed_task(issue_id="IPR0105", owner=owner)
    waiting = generate_waiting_plan(client, task)
    plan_id = waiting["plans"][0]["id"]

    intruder = approver2_client
    for method, path, payload in [
        ("post", f"/api/tasks/{task.id}/plans/{plan_id}/approve", {}),
        ("post", f"/api/tasks/{task.id}/plans/{plan_id}/rewrite", {"reason": "越权重写"}),
        ("post", f"/api/tasks/{task.id}/plans/{plan_id}/reject", {"reason": "越权拒绝"}),
    ]:
        response = getattr(intruder, method)(path, json=payload)
        assert response.status_code == 403, (method, path)
        assert "仅被指派的审批人" in response.json()["detail"]["message"]

    after = client.get(f"/api/tasks/{task.id}")
    data = after.json()["data"]
    assert data["status"] == "WAITING_APPROVAL"
    assert data["plans"][0]["status"] == "WAITING_APPROVAL"


def test_admin_override_can_approve_any_task(client, approver2_client, admin_client):
    task = seed_analyzed_task(issue_id="IPR0106", owner=second_owner)
    waiting = generate_waiting_plan(client, task)
    plan_id = waiting["plans"][0]["id"]
    response = admin_client.post(f"/api/tasks/{task.id}/plans/{plan_id}/approve", json={})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "APPROVED"
    plan = data["plans"][0]
    assert plan["status"] == "APPROVED"
    assert plan["approved_by_id"] == "user_admin"


# ---------- 审批人指派 / 改派 ----------

def test_assign_owner_permission_matrix(client, approver_client, lead_client, admin_client):
    task = seed_analyzed_task(issue_id="IPR0107", owner=None)
    payload = {"user_id": owner.id}
    assert admin_client.post(f"/api/tasks/{task.id}/approval-owner", json=payload).status_code == 200
    assert lead_client.post(f"/api/tasks/{task.id}/approval-owner", json=payload).status_code == 200
    assert approver_client.post(f"/api/tasks/{task.id}/approval-owner", json=payload).status_code == 403
    assert client.post(f"/api/tasks/{task.id}/approval-owner", json=payload).status_code == 403


def test_assign_owner_validates_target_role(client, admin_client):
    task = seed_analyzed_task(issue_id="IPR0108", owner=None)
    for target_id in ["user_developer", "user_lead", "user_missing"]:
        response = admin_client.post(
            f"/api/tasks/{task.id}/approval-owner",
            json={"user_id": target_id},
        )
        assert response.status_code == 422, target_id
        assert "审批权限" in response.json()["detail"]["message"]


def test_approval_candidates_endpoint_scope(client, approver_client, lead_client, admin_client):
    """候选审批人列表：admin/approver 角色可见；lead 无 user:view 也可用（approval:assign 门禁）。"""
    response = admin_client.get("/api/approvals/candidates")
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    assert "user_admin" in ids and owner.id in ids
    assert "user_lead" not in ids and "user_developer" not in ids
    assert lead_client.get("/api/approvals/candidates").status_code == 200
    assert approver_client.get("/api/approvals/candidates").status_code == 403
    assert client.get("/api/approvals/candidates").status_code == 403


def test_assign_owner_blocked_after_approval(client, approver_client, admin_client):
    approved = seed_analyzed_task(issue_id="IPR0109", owner=owner)
    waiting = generate_waiting_plan(client, approved)
    plan_id = waiting["plans"][0]["id"]
    assert approver_client.post(f"/api/tasks/{approved.id}/plans/{plan_id}/approve", json={}).status_code == 200

    response = admin_client.post(
        f"/api/tasks/{approved.id}/approval-owner",
        json={"user_id": second_owner.id},
    )
    assert response.status_code == 409
    assert "无需指派" in response.json()["detail"]["message"]

    failed = seed_analyzed_task(issue_id="IPR0110", owner=second_owner)
    failed.status = "FAILED"
    failed.manual_required = True
    failed.manual_reason = "plan_rejected"
    response = admin_client.post(
        f"/api/tasks/{failed.id}/approval-owner",
        json={"user_id": owner.id},
    )
    assert response.status_code == 200


def test_assign_owner_records_event_and_moves_queue(client, approver_client, approver2_client, admin_client):
    task = seed_analyzed_task(issue_id="IPR0111", owner=owner)
    generate_waiting_plan(client, task)
    assert pending_ids(approver_client) == [task.id]
    assert pending_ids(approver2_client) == []

    response = admin_client.post(
        f"/api/tasks/{task.id}/approval-owner",
        json={"user_id": second_owner.id, "reason": "原审批人休假，改派处理"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["approval_owner_id"] == second_owner.id
    assert data["approval_owner_name"] == second_owner.name
    owner_events = [event for event in data["events"] if event["event_type"] == "APPROVAL_OWNER_CHANGED"]
    assert len(owner_events) == 1
    assert owner_events[0]["actor_id"] == "user_admin"
    assert owner_events[0]["actor_name"] == "林岚"
    assert any(owner.name in item and second_owner.name in item for item in owner_events[0]["diff_summary"])
    assert any("改派处理" in item for item in owner_events[0]["diff_summary"])

    assert pending_ids(approver_client) == []
    assert pending_ids(approver2_client) == [task.id]


# ---------- 仓库默认审批人可空 + 无 owner 生成门禁 ----------

def test_repository_allows_missing_default_owner(admin_client):
    response = admin_client.post(
        "/api/repositories",
        json={
            "project_id": "proj_payment",
            "name": "ownerless-svc",
            "url": "https://gitee.com/company/ownerless-svc.git",
            "default_branch": "main",
            "test_command": "pytest tests/ -q",
            "command_whitelist": ["pytest"],
            "max_files": 12,
            "max_retries": 2,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["approval_owner_id"] is None
    assert data["approval_owner_name"] is None
    main.REPOSITORIES.pop(data["id"], None)


def test_plan_generation_blocked_without_owner_then_assign_succeeds(client, lead_client):
    task = seed_analyzed_task(issue_id="IPR0112", owner=None)
    blocked = client.post(f"/api/tasks/{task.id}/plans")
    assert blocked.status_code == 409
    assert "指派审批人" in blocked.json()["detail"]["message"]

    assign = lead_client.post(
        f"/api/tasks/{task.id}/approval-owner",
        json={"user_id": owner.id, "reason": "仓库未配置默认审批人，手动指派"},
    )
    assert assign.status_code == 200

    waiting = generate_waiting_plan(client, task)
    assert waiting["approval_owner_id"] == owner.id
    assert waiting["plans"][0]["status"] == "WAITING_APPROVAL"


# ---------- 审批记录落库 ----------

def test_approve_persists_approval_record_fields(client, approver_client):
    task = seed_analyzed_task(issue_id="IPR0113", owner=owner)
    waiting = generate_waiting_plan(client, task)
    plan_id = waiting["plans"][0]["id"]
    response = approver_client.post(
        f"/api/tasks/{task.id}/plans/{plan_id}/approve",
        json={"comment": "范围可控，按计划执行"},
    )
    assert response.status_code == 200
    plan = response.json()["data"]["plans"][0]
    assert plan["status"] == "APPROVED"
    assert plan["approved_by_id"] == owner.id
    assert plan["approved_by_name"] == owner.name
    assert plan["approved_at"] is not None
    assert plan["approval_comment"] == "范围可控，按计划执行"
    assert plan["risk_adjust_from"] is None
    listing = client.get(f"/api/tasks/{task.id}/plans")
    listed = listing.json()["data"][0]
    assert listed["approved_by_name"] == owner.name


def test_approve_risk_adjust_persists_original_level(client, approver_client):
    task = seed_analyzed_task(issue_id="IPR0114", owner=owner)
    waiting = generate_waiting_plan(client, task)
    plan_id = waiting["plans"][0]["id"]
    response = approver_client.post(
        f"/api/tasks/{task.id}/plans/{plan_id}/approve",
        json=approve_payload(risk_level="medium", adjust_reason="涉及线上兜底逻辑，建议升级中风险"),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["risk_level"] == "medium"
    assert data["risk_source"] == "plan_approval"
    plan = data["plans"][0]
    assert plan["risk_adjust_from"] == "low"
    assert plan["risk_level"] == "medium"
    assert plan["risk_adjust_reason"] == "涉及线上兜底逻辑，建议升级中风险"
    assert plan["approved_by_id"] == owner.id
    assert plan["approved_at"] is not None
