"""DEV-015 P1-1 计划重写超限门禁测试套件。

覆盖：重写 v1→v2→v3 后第 3 次重写被 409 拒绝并写 PLAN_REWRITE_BLOCKED 审计
（版本不变、任务仍 WAITING_APPROVAL）；达上限后审批当前版本可继续（人工路径）；
拒绝 → retry 新轮次后 plans 清空、上限随轮次重置（可再重写 2 次、第 3 次再拒）；
守卫回归（403 越权 / 状态 409 优先 / 空原因 422 均不产生超限事件）；
超限事件在任务回放与全局审计可见；低风险审批不触发后端自动绕过或自动重写。
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main

HEAD_A = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
NEUTRAL_BODY = "列表页在窄屏下布局错乱，需要调整样式并补充回归。"
README_CONTENT = "project documentation overview\n"

repository = main.REPOSITORIES["repo_payment_service"]
developer = main.USERS["user_developer"]
approver = main.USERS["user_approver"]


@pytest.fixture(autouse=True)
def fast_index_and_snapshot_seams(monkeypatch):
    """索引阶段零等待 + head/快照 seam，供 retry 复用已建索引场景。"""
    monkeypatch.setattr(main, "INDEX_STAGE_SECONDS", 0)
    monkeypatch.setattr(main, "fetch_gitee_branch_head", lambda repo, base_branch=None: HEAD_A)
    monkeypatch.setattr(
        main,
        "sync_repository_snapshot",
        lambda repo, commit_sha: [
            main.CodeFile(path="README.md", language="markdown", content=README_CONTENT),
            # DEV-017：retry 自动检索需要命中与 Issue（列表页布局/样式）相关的代码片段
            main.CodeFile(
                path="src/list_page.css",
                language="css",
                content="/* 列表页窄屏布局与样式修复，补充回归样式规则 */\n.list-page { display: flex; }\n",
            ),
        ],
    )
    yield


def authed_client(account: str, password: str) -> TestClient:
    test_client = TestClient(main.app)
    login = test_client.post(
        "/api/auth/login",
        json={"account": account, "password": password},
    )
    assert login.status_code == 200
    return test_client


def snapshot_of(title: str, body: str) -> main.IssueSnapshotRecord:
    return main.IssueSnapshotRecord(
        title=title,
        body=body,
        comments=[],
        labels=["ui"],
        raw_response={},
        fetched_at=datetime.now(timezone.utc),
    )


def seed_analyzed_task(issue_id: str = "IRWL900") -> main.TaskRecord:
    now = datetime.now(timezone.utc)
    task = main.TaskRecord(
        id=f"TASK-{9800 + len(main.TASKS)}",
        version=1,
        project_id=repository.project_id,
        project_name=main.PROJECTS[repository.project_id].name,
        repository_id=repository.id,
        repository_name=repository.name,
        repository_url=repository.url,
        issue_id=issue_id,
        title="优化列表页布局",
        status="CREATED",
        base_branch=repository.default_branch,
        test_command=repository.test_command,
        approval_owner_id=approver.id,
        approval_owner_name=approver.name,
        work_branch=f"agent/main/issue-{issue_id}-v1",
        issue_snapshot=snapshot_of("优化列表页布局", NEUTRAL_BODY),
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


def generate_first_plan(client: TestClient, task: main.TaskRecord) -> dict:
    response = client.post(f"/api/tasks/{task.id}/plans")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "WAITING_APPROVAL"
    return data


def latest_plan_id(data: dict) -> str:
    plans = sorted(data["plans"], key=lambda plan: plan["version"])
    return plans[-1]["id"]


def rewrite_once(approver_client: TestClient, task_id: str, plan_id: str, reason: str):
    return approver_client.post(
        f"/api/tasks/{task_id}/plans/{plan_id}/rewrite",
        json={"reason": reason},
    )


def make_completed_index() -> main.RepositoryIndexRecord:
    record = main.new_repository_index(
        repository=repository,
        commit_sha=HEAD_A,
        base_branch=repository.default_branch,
        requested_by=developer,
        requested_at=datetime.now(timezone.utc),
    )
    while record.status in main.INDEX_RUNNING_STAGES:
        main.progress_single_repository_index(record)
    assert record.status == "COMPLETED"
    return record


def blocked_events() -> list[main.AuditEventRecord]:
    return [event for event in main.AUDIT_EVENTS.values() if event.event_type == "PLAN_REWRITE_BLOCKED"]


def rewritten_events(task: main.TaskRecord) -> list[main.AuditEventRecord]:
    return [event for event in task.events if event.event_type == "PLAN_REWRITTEN"]


# ---------- 超限门禁主链路 ----------

def test_rewrite_up_to_limit_then_blocked_with_audit(client):
    task = seed_analyzed_task(issue_id="IRWL901")
    waiting = generate_first_plan(client, task)
    approver_client = authed_client("approver@company.com", "Approver123!")

    first = rewrite_once(approver_client, task.id, latest_plan_id(waiting), "文件范围不够具体，请细化")
    assert first.status_code == 200
    second = rewrite_once(approver_client, task.id, latest_plan_id(first.json()["data"]), "补充数据库兼容说明")
    assert second.status_code == 200
    after_two = second.json()["data"]
    assert [plan["version"] for plan in after_two["plans"]] == [1, 2, 3]
    assert after_two["plans"][-1]["status"] == "WAITING_APPROVAL"

    blocked = rewrite_once(approver_client, task.id, latest_plan_id(after_two), "还想再改一次")
    assert blocked.status_code == 409
    detail = blocked.json()["detail"]
    assert detail["code"] == 409
    assert f"已重写 {main.MAX_PLAN_REWRITES} 次达上限" in detail["message"]
    assert "可审批当前版本或拒绝终止" in detail["message"]
    assert "重试任务开启新轮次" in detail["message"]

    # 超限事件：恰 1 条、由审批人署名、含版本与上限与出路
    events = blocked_events()
    assert len(events) == 1
    event = events[0]
    assert event.actor_id == approver.id
    assert event.actor_name == approver.name
    assert event.summary == f"计划重写次数超限（v{1 + main.MAX_PLAN_REWRITES}）"
    joined = "\n".join(event.diff_summary)
    assert f"v{1 + main.MAX_PLAN_REWRITES}" in joined
    assert f"{main.MAX_PLAN_REWRITES} 次" in joined

    # 无副作用：版本不再递增、任务状态不变、最新计划仍可审批
    latest = main.TASKS[task.id]
    assert [plan.version for plan in latest.plans] == [1, 2, 3]
    assert latest.status == "WAITING_APPROVAL"
    assert len(rewritten_events(latest)) == 2
    assert latest.plans[-1].status == "WAITING_APPROVAL"
    assert latest.plans[-1].version == 3


def test_rewrite_limit_approve_current_version_still_allowed(client):
    task = seed_analyzed_task(issue_id="IRWL902")
    waiting = generate_first_plan(client, task)
    approver_client = authed_client("approver@company.com", "Approver123!")
    first = rewrite_once(approver_client, task.id, latest_plan_id(waiting), "调整文件范围")
    second = rewrite_once(approver_client, task.id, latest_plan_id(first.json()["data"]), "补充回滚说明")
    after_two = second.json()["data"]
    blocked = rewrite_once(approver_client, task.id, latest_plan_id(after_two), "继续修改")
    assert blocked.status_code == 409

    # 人工继续路径：达上限后仍可审批当前 v3
    approve = approver_client.post(
        f"/api/tasks/{task.id}/plans/{latest_plan_id(after_two)}/approve",
        json={},
    )
    assert approve.status_code == 200
    data = approve.json()["data"]
    assert data["status"] == "APPROVED"
    assert data["plans"][-1]["version"] == 3
    assert data["plans"][-1]["status"] == "APPROVED"
    assert len(data["plans"]) == 3


def test_rewrite_limit_reject_then_retry_resets_round(client):
    task = seed_analyzed_task(issue_id="IRWL903")
    waiting = generate_first_plan(client, task)
    approver_client = authed_client("approver@company.com", "Approver123!")
    first = rewrite_once(approver_client, task.id, latest_plan_id(waiting), "方案太宽")
    second = rewrite_once(approver_client, task.id, latest_plan_id(first.json()["data"]), "仍需收敛")
    after_two = second.json()["data"]
    blocked = rewrite_once(approver_client, task.id, latest_plan_id(after_two), "再改一次")
    assert blocked.status_code == 409

    # 人工终止路径：拒绝 → 任务 FAILED → retry 开启新轮次
    reject = approver_client.post(
        f"/api/tasks/{task.id}/plans/{latest_plan_id(after_two)}/reject",
        json={"reason": "重写已达上限，拒绝当前版本终止本轮"},
    )
    assert reject.status_code == 200
    assert reject.json()["data"]["status"] == "FAILED"
    assert reject.json()["data"]["manual_reason"] == "plan_rejected"

    make_completed_index()
    retry = client.post(f"/api/tasks/{task.id}/retry")
    assert retry.status_code == 200
    assert retry.json()["data"]["status"] == "CONTEXT_INDEXED"
    assert retry.json()["data"]["plans"] == []

    # 新轮次：重新分析并生成计划，从 v1 起可再重写 2 次
    main.attach_analysis(main.TASKS[task.id], main.generate_mock_analysis(main.TASKS[task.id]))
    regenerate = client.post(f"/api/tasks/{task.id}/plans")
    assert regenerate.status_code == 200
    new_round = regenerate.json()["data"]
    assert [plan["version"] for plan in new_round["plans"]] == [1]

    round_first = rewrite_once(approver_client, task.id, latest_plan_id(new_round), "新轮次第一改")
    assert round_first.status_code == 200
    round_second = rewrite_once(approver_client, task.id, latest_plan_id(round_first.json()["data"]), "新轮次第二改")
    assert round_second.status_code == 200
    assert [plan["version"] for plan in round_second.json()["data"]["plans"]] == [1, 2, 3]

    again_blocked = rewrite_once(approver_client, task.id, latest_plan_id(round_second.json()["data"]), "新轮次第三次")
    assert again_blocked.status_code == 409
    assert "达上限" in again_blocked.json()["detail"]["message"]
    assert len(blocked_events()) == 2, "两轮各产生 1 条超限事件"
    task_record = main.TASKS[task.id]
    assert [plan.version for plan in task_record.plans] == [1, 2, 3]


# ---------- 守卫回归：门禁不改变既有语义、无副作用 ----------

def test_rewrite_guards_regression_without_blocked_event(client):
    task = seed_analyzed_task(issue_id="IRWL904")
    waiting = generate_first_plan(client, task)
    plan_id = latest_plan_id(waiting)
    approver_client = authed_client("approver@company.com", "Approver123!")

    # 越权（无 approval:manage 的 developer）→ 403，先于超限判定
    intruder = rewrite_once(client, task.id, plan_id, "越权重写")
    assert intruder.status_code == 403

    # 状态守卫优先：审批完成后重写 → 409「仅待审批中的最新计划可要求重写」（非超限文案）
    approve = approver_client.post(f"/api/tasks/{task.id}/plans/{plan_id}/approve", json={})
    assert approve.status_code == 200
    after_approve = rewrite_once(approver_client, task.id, plan_id, "审批后想重写")
    assert after_approve.status_code == 409
    assert "仅待审批中的最新计划可要求重写" in after_approve.json()["detail"]["message"]

    # 空原因 → 422
    empty = approver_client.post(f"/api/tasks/{task.id}/plans/{plan_id}/rewrite", json={"reason": "  "})
    assert empty.status_code == 422

    assert blocked_events() == [], "以上守卫路径均不产生超限事件"


def test_rewrite_blocked_event_visible_in_task_replay_and_global_audit(client):
    task = seed_analyzed_task(issue_id="IRWL905")
    waiting = generate_first_plan(client, task)
    approver_client = authed_client("approver@company.com", "Approver123!")
    first = rewrite_once(approver_client, task.id, latest_plan_id(waiting), "补充范围")
    second = rewrite_once(approver_client, task.id, latest_plan_id(first.json()["data"]), "再细化")
    after_two = second.json()["data"]
    blocked = rewrite_once(approver_client, task.id, latest_plan_id(after_two), "继续")
    assert blocked.status_code == 409

    # 任务详情回放可见：末位事件即 PLAN_REWRITE_BLOCKED
    detail = client.get(f"/api/tasks/{task.id}")
    assert detail.status_code == 200
    events = detail.json()["data"]["events"]
    assert events[-1]["event_type"] == "PLAN_REWRITE_BLOCKED"
    assert events[-1]["actor_name"] == approver.name
    assert "重写次数超限" in events[-1]["summary"]

    # 全局审计管理员可查：resource_type=task、含版本与处置指引
    assert len(blocked_events()) == 1
    global_event = blocked_events()[0]
    assert global_event.resource_type == "task"
    assert global_event.resource_id == task.id


# ---------- P1-2 边界锁定：后端无自动审批/自动重写通道 ----------

def test_low_risk_approve_does_not_auto_rewrite_or_bypass(client):
    task = seed_analyzed_task(issue_id="IRWL906")
    waiting = generate_first_plan(client, task)
    plan_id = latest_plan_id(waiting)
    assert task.plans[-1].risk_level == "low"
    approver_client = authed_client("approver@company.com", "Approver123!")

    approve = approver_client.post(f"/api/tasks/{task.id}/plans/{plan_id}/approve", json={})
    assert approve.status_code == 200
    data = approve.json()["data"]
    assert data["status"] == "APPROVED"
    assert data["plans"][0]["status"] == "APPROVED"
    assert len(data["plans"]) == 1, "低风险审批不自动生成新版本"
    assert data["next_action"] == "auto_generate_diff"

    record = main.TASKS[task.id]
    assert len(record.plans) == 1
    assert record.plans[0].version == 1
    assert rewritten_events(record) == []
    assert blocked_events() == []
    assert not any(event.event_type == "PLAN_APPROVED" and event.actor_id == "agent-system" for event in record.events)
    approved = [event for event in record.events if event.event_type == "PLAN_APPROVED"]
    assert len(approved) == 1
    assert approved[0].actor_id == approver.id, "审批动作由具体审批人署名"
