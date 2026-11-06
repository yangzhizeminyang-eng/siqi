"""DEV-019 评论、通知与催办测试套件。

覆盖：任务评论列表/发表/@成员提及生成 MENTION 通知、关联对象校验、评论编辑权限、
软删除保留记录与审计、通知收件箱(拉取)与标记已读、审批联动(进入待审批→通知审批人/
高风险强提醒、审批通过/拒绝→通知发起人)、超时催办(>24h 才允许 + NUDGE 通知 + 审计)、DB 落库恢复。
"""
from __future__ import annotations

import datetime
from datetime import timedelta
from itertools import count

import pytest
from fastapi.testclient import TestClient

from app import main

NEUTRAL_BODY = "列表页在窄屏下布局错乱，需要调整样式并补充回归。"
_seq = count(9700)

repository = main.REPOSITORIES["repo_payment_service"]
developer = main.USERS["user_developer"]
approver = main.USERS["user_approver"]


def authed_client(account: str, password: str) -> TestClient:
    test_client = TestClient(main.app)
    login = test_client.post(
        "/api/auth/login",
        json={"account": account, "password": password},
    )
    assert login.status_code == 200
    return test_client


@pytest.fixture()
def dev_client() -> TestClient:
    return authed_client("developer@company.com", "Dev123!")


@pytest.fixture()
def approver_client() -> TestClient:
    return authed_client("approver@company.com", "Approver123!")


def make_task(
    *,
    status: str = "ANALYZED",
    approval_owner_id: str | None = "user_approver",
    created_by: str = "user_developer",
    plan_age_hours: int | None = None,
) -> main.TaskRecord:
    now = datetime.datetime.now(datetime.timezone.utc)
    task = main.TaskRecord(
        id=f"TASK-{next(_seq)}",
        version=1,
        project_id=repository.project_id,
        project_name=main.PROJECTS[repository.project_id].name,
        repository_id=repository.id,
        repository_name=repository.name,
        repository_url=repository.url,
        issue_id=f"DEV19-{next(_seq) % 10000}",
        title="优化列表页样式",
        status=status,
        base_branch="main",
        test_command="pytest tests/ -q",
        approval_owner_id=approval_owner_id,
        approval_owner_name=(main.USERS[approval_owner_id].name if approval_owner_id else None),
        work_branch="agent/main/v1",
        issue_snapshot=main.IssueSnapshotRecord(
            title="优化列表页样式", body=NEUTRAL_BODY, comments=[], labels=[], raw_response={},
            fetched_at=now,
        ),
        automation_state="running",
        manual_required=False,
        manual_reason="none",
        next_action="check_index",
        events=[],
        created_by=created_by,
        created_by_name=main.USERS[created_by].name if created_by in main.USERS else "开发者",
        created_at=now,
        updated_at=now,
    )
    if status == "WAITING_APPROVAL" and plan_age_hours is not None:
        plan = main.ModificationPlan(
            id=f"PLAN-{task.id.removeprefix('TASK-')}-1",
            task_id=task.id,
            version=1,
            goal="目标",
            file_scope=[],
            steps=[],
            risk_level="medium",
            risk_reason="",
            test_suggestions=[],
            assumptions=[],
            rollback_plan=[],
            generated_from_analysis_revision=1,
            generated_from_context_ids=[],
            provider="mvp-rule-based",
            status="WAITING_APPROVAL",
            created_by=created_by,
            created_by_name=task.created_by_name,
            created_at=now - timedelta(hours=plan_age_hours),
        )
        task.plans.append(plan)
    main.TASKS[task.id] = task
    return task


def test_create_comment_and_mention_generates_notification(dev_client):
    task = make_task()
    body = f"请 @{approver.name} 帮忙确认样式，另请 @{developer.name} 复核"
    resp = dev_client.post(f"/api/tasks/{task.id}/comments", json={"body": body})
    assert resp.status_code == 200, resp.text
    comment = resp.json()["data"]
    assert comment["author_id"] == developer.id
    assert comment["mentioned_user_ids"] == [approver.id]

    # 评论落库于任务时间线 + 全局审计
    assert any(e.event_type == "COMMENT_ADDED" for e in task.events)
    assert any(e.event_type == "COMMENT_ADDED" and e.resource_id == task.id for e in main.AUDIT_EVENTS.values())

    # @提及 → approver 收到 MENTION 通知
    notif = [n for n in main.NOTIFICATIONS.values() if n.recipient_id == approver.id and n.type == "MENTION"]
    assert notif, "被@成员应收到 MENTION 通知"
    assert notif[0].body == body[:160]


def test_mention_skips_self(dev_client):
    task = make_task()
    dev_client.post(f"/api/tasks/{task.id}/comments", json={"body": f"@{developer.name} 自己看"})
    # 作者自己提及自己 → 不产生通知
    assert all(n.recipient_id != developer.id for n in main.NOTIFICATIONS.values())


def test_list_comments_sorted(dev_client):
    task = make_task()
    dev_client.post(f"/api/tasks/{task.id}/comments", json={"body": "第一条"})
    dev_client.post(f"/api/tasks/{task.id}/comments", json={"body": "第二条"})
    resp = dev_client.get(f"/api/tasks/{task.id}/comments")
    assert resp.status_code == 200
    bodies = [c["body"] for c in resp.json()["data"]]
    assert bodies == ["第一条", "第二条"]


def test_comment_reference_validation(dev_client):
    task = make_task()
    bad = dev_client.post(f"/api/tasks/{task.id}/comments", json={"body": "x@1", "reference_type": "plan", "reference_id": "PLAN-NOT-EXIST"})
    assert bad.status_code in {400, 422}
    # 合法：关联任务本身
    ok = dev_client.post(f"/api/tasks/{task.id}/comments", json={"body": "ok", "reference_type": "task", "reference_id": task.id})
    assert ok.status_code == 200


def test_soft_delete_keeps_record_and_audit(dev_client):
    task = make_task()
    created = dev_client.post(f"/api/tasks/{task.id}/comments", json={"body": "待删除"}).json()["data"]
    cid = created["id"]
    resp = dev_client.delete(f"/api/tasks/{task.id}/comments/{cid}")
    assert resp.status_code == 200
    assert resp.json()["data"]["deleted_at"] is not None

    # 列表仍包含该评论（软删除占位），事件与审计留痕
    listed = [c for c in dev_client.get(f"/api/tasks/{task.id}/comments").json()["data"] if c["id"] == cid]
    assert listed and listed[0]["deleted_at"] is not None
    assert any(e.event_type == "COMMENT_DELETED" and e.resource_id == task.id for e in main.AUDIT_EVENTS.values())


def test_delete_foreign_comment_forbidden(approver_client, dev_client):
    # developer 发评论，approver 不能删（非作者非 admin）
    task = make_task()
    cid = dev_client.post(f"/api/tasks/{task.id}/comments", json={"body": "only author"}).json()["data"]["id"]
    resp = approver_client.delete(f"/api/tasks/{task.id}/comments/{cid}")
    assert resp.status_code == 403


def test_notifications_inbox_and_mark_read(dev_client, approver_client):
    task = make_task()
    dev_client.post(f"/api/tasks/{task.id}/comments", json={"body": f"@{approver.name} please approve"})
    approvers = [n for n in main.NOTIFICATIONS.values() if n.recipient_id == approver.id]
    assert approvers, "应产生审批人通知"
    notif_id = approvers[0].id

    # 收件箱只返回当前用户自己的通知（approver 视角）
    inbox = approver_client.get("/api/notifications").json()["data"]
    assert inbox and all(n["recipient_id"] == approver.id and n["read_at"] is None for n in inbox)

    r = approver_client.post(f"/api/notifications/{notif_id}/read")
    assert r.status_code == 200
    unread = approver_client.get("/api/notifications?unread_only=true").json()["data"]
    assert all(n["id"] != notif_id for n in unread)
    count = approver_client.get("/api/notifications/unread-count").json()["data"]["count"]
    assert count == len(approvers) - 1
    # 开发者看不到别人的通知未读数（自己无通知）
    assert dev_client.get("/api/notifications/unread-count").json()["data"]["count"] == 0


def test_task_notifications_scoped(dev_client):
    task = make_task()
    dev_client.post(f"/api/tasks/{task.id}/comments", json={"body": f"@{approver.name} check"})
    scoped = dev_client.get(f"/api/tasks/{task.id}/notifications").json()["data"]
    assert scoped and all(n["task_id"] == task.id for n in scoped)


@pytest.mark.parametrize("risk,expected_type,priority", [
    ("high", "RISK_HIGH", "high"),
    ("medium", "APPROVAL_REQUIRED", "normal"),
])
def test_entering_approval_notifies_approver(dev_client, risk, expected_type, priority):
    task = make_task(status="ANALYZED", approval_owner_id=approver.id)
    # 模拟 attach_plan 到 WAITING_APPROVAL（DEV-019 钩子）
    plan = main.ModificationPlan(
        id=f"PLAN-{task.id.removeprefix('TASK-')}-1", task_id=task.id, version=1,
        goal="g", file_scope=[], steps=[], risk_level=risk, risk_reason="", test_suggestions=[],
        assumptions=[], rollback_plan=[], generated_from_analysis_revision=1, generated_from_context_ids=[],
        provider="mvp-rule-based", status="WAITING_APPROVAL",
        created_by=developer.id, created_by_name=developer.name, created_at=task.created_at,
    )
    task.plans.append(plan)
    task.status = "WAITING_APPROVAL"
    task.risk_level = risk
    main.emit_approval_required_notification(task)
    n = [x for x in main.NOTIFICATIONS.values() if x.recipient_id == approver.id]
    assert n and n[0].type == expected_type and n[0].priority == priority


def test_approval_result_notifies_creator(approver_client):
    task = make_task(status="WAITING_APPROVAL", plan_age_hours=0, created_by=developer.id)
    task.automation_state = "waiting_approval"
    task.manual_required = True
    task.manual_reason = "approval_required"
    task.next_action = "review_plan"
    plan = task.plans[0]
    resp = approver_client.post(f"/api/tasks/{task.id}/plans/{plan.id}/approve", json={"comment": "ok"})
    assert resp.status_code == 200, resp.text
    creator_notif = [n for n in main.NOTIFICATIONS.values() if n.recipient_id == developer.id and n.type == "APPROVAL_RESULT"]
    assert creator_notif, "审批通过应通知任务发起人"
    assert "通过" in creator_notif[0].title or "通过" in creator_notif[0].body or "通过" in creator_notif[0].title


def test_nudge_requires_24h(dev_client):
    fresh = make_task(status="WAITING_APPROVAL", plan_age_hours=1, created_by=developer.id)
    resp = dev_client.post(f"/api/tasks/{fresh.id}/nudge")
    assert resp.status_code == 409


def test_nudge_sends_notification_and_audit(dev_client):
    task = make_task(status="WAITING_APPROVAL", plan_age_hours=30, created_by=developer.id)
    resp = dev_client.post(f"/api/tasks/{task.id}/nudge")
    assert resp.status_code == 200, resp.text

    nudge = [n for n in main.NOTIFICATIONS.values() if n.recipient_id == approver.id and n.type == "NUDGE"]
    assert nudge and nudge[0].priority == "high"
    assert any(e.event_type == "NUDGE_SENT" and e.resource_id == task.id for e in main.AUDIT_EVENTS.values())
    assert any(e.event_type == "NUDGE_SENT" for e in task.events)


def test_nudge_gate_non_waiting(dev_client):
    task = make_task(status="ANALYZED", created_by=developer.id)
    resp = dev_client.post(f"/api/tasks/{task.id}/nudge")
    assert resp.status_code == 409


def test_nudge_is_idempotent(dev_client):
    """重复催办：第二次不重复生成 NUDGE 通知 / NUDGE_SENT 审计 / 任务事件。"""
    task = make_task(status="WAITING_APPROVAL", plan_age_hours=30, created_by=developer.id)
    first = dev_client.post(f"/api/tasks/{task.id}/nudge")
    assert first.status_code == 200, first.text
    assert first.json()["data"]["nudged"] is True

    def snapshot():
        """返回当前 NUDGE 通知 / NUDGE_SENT 全局审计(task_event 也会入 AUDIT_EVENTS) / 任务事件计数。"""
        nudges = len([n for n in main.NOTIFICATIONS.values() if n.type == "NUDGE" and n.task_id == task.id])
        audits = len([e for e in main.AUDIT_EVENTS.values() if e.event_type == "NUDGE_SENT" and e.resource_id == task.id])
        events = len([e for e in task.events if getattr(e, "event_type", None) == "NUDGE_SENT"])
        return nudges, audits, events

    before = snapshot()
    assert before[0] == 1 and before[2] == 1, f"首次催办应产生 1 条 NUDGE 通知 + 1 条任务事件：{before}"
    second = dev_client.post(f"/api/tasks/{task.id}/nudge")
    assert second.status_code == 200, second.text
    assert second.json()["data"]["nudged"] is False, "第二次催办应幂等且不重复生成"
    after = snapshot()
    assert after == before, f"重复催办不应新增 NUDGE 通知/审计/事件：before={before} after={after}"


def test_comments_unknown_task_404(dev_client):
    resp = dev_client.get("/api/tasks/TASK-NOPE/comments")
    assert resp.status_code == 404


def test_notification_read_is_strictly_self_scoped(dev_client, approver_client):
    """非管理员只能读到自己的通知；管理员不默认读取他人个人通知。"""
    task = make_task()
    dev_client.post(f"/api/tasks/{task.id}/comments", json={"body": f"@{approver.name} hey"})
    approver_notifs = [n for n in main.NOTIFICATIONS.values() if n.recipient_id == approver.id]
    mention_id = approver_notifs[0].id

    dev_list = dev_client.get("/api/notifications").json()["data"]
    assert all(n["recipient_id"] == developer.id for n in dev_list), "非管理员只能看到自己的通知"
    assert not any(n["id"] == mention_id for n in dev_list), "开发者不应看到他人的通知"

    admin_client = authed_client("admin@company.com", "Admin123!")
    admin_list = admin_client.get("/api/notifications").json()["data"]
    admin = next(u for u in main.USERS.values() if u.role == "admin")
    assert all(n["recipient_id"] == admin.id for n in admin_list), "管理员列表也只含自己的通知"
    assert not any(n["id"] == mention_id for n in admin_list), "管理员不应默认读到他人个人通知"

    approver_list = approver_client.get("/api/notifications").json()["data"]
    assert approver_list and all(n["recipient_id"] == approver.id for n in approver_list)


def test_notification_service_unified_entry():
    """统一通知服务：三种入口产出约定类型的通知记录。"""
    from app.services.notification_service import (
        notify_approval_completed,
        notify_approval_required,
        send_approval_reminder,
    )

    task = make_task(status="WAITING_APPROVAL", plan_age_hours=30, created_by=developer.id)
    plan = task.plans[0]

    req = notify_approval_required(task, plan, approver.id, high_risk=True)
    assert req.type == "RISK_HIGH" and req.priority == "high"
    assert req.recipient_id == approver.id

    done = notify_approval_completed(task, plan, "approved", "user_approver", "ok")
    assert done is not None and done.type == "APPROVAL_RESULT" and done.recipient_id == developer.id

    nudge = send_approval_reminder(task, developer)
    assert nudge.type == "NUDGE" and nudge.priority == "high" and nudge.recipient_id == approver.id


def test_comments_and_notifications_db_roundtrip(tmp_path, monkeypatch):
    """评论与通知经 upsert/load 落库并恢复（临时 SQLite）。"""
    from app.db.repositories import load_comments, load_notifications, upsert_comment, upsert_notification
    from app.db.session import create_schema, get_engine, get_session_factory

    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'dev19.db').as_posix()}")
    get_engine.cache_clear(); get_session_factory.cache_clear(); create_schema()
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        c = main.CommentRecord(id="cmt_db1", task_id="TASK-1", project_id="p", repository_id="r",
                               author_id="user_developer", author_name="张伟", body="落库评论",
                               mentioned_user_ids=[approver.id], reference_type="task", reference_id="TASK-1",
                               created_at=now, deleted_at=None)
        n = main.NotificationRecord(id="ntf_db1", recipient_id=approver.id, task_id="TASK-1", type="MENTION",
                                    title="被提及", body="body", actor_id="user_developer", actor_name="张伟",
                                    priority="high", read_at=None, created_at=now)
        upsert_comment(c); upsert_notification(n)
        assert any(d["id"] == "cmt_db1" for d in load_comments())
        assert any(d["id"] == "ntf_db1" and d["recipient_id"] == approver.id for d in load_notifications())
    finally:
        get_engine.cache_clear(); get_session_factory.cache_clear()