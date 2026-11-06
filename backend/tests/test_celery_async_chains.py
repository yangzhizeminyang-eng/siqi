"""Celery 异步主链路测试：Gitee 同步 Worker + 同步 handler 派发 + 审计/自动建任务。"""

from __future__ import annotations

import datetime as _dt

import pytest

from app import main


def _request(request_id: str = "REQ-9001", status: str = "DRAFT") -> main.TeamRequestRecord:
    now = _dt.datetime.now(_dt.timezone.utc)
    return main.TeamRequestRecord(
        id=request_id, request_no=request_id, project_id="proj_mvp", project_name="P",
        repository_id="repo_payment_service", repository_name="payment_service",
        type="bug", title="同步大纲", body="正文足够长：需要保证内容充分，用于触发同步链路。",
        reproduction_steps="", acceptance_criteria=["c"], priority="high", labels=["bug"],
        target_branch="main", test_command="pytest tests/ -q", sync_status=status,
        content_sufficient=True, validation_warnings=[], created_by_id="user_approver",
        created_by_name="审批人", created_at=now, updated_at=now,
    )


def _actor():
    # request:create 权限：开发者/管理员有；admin 不受「只能同步本人」限制
    return main.USERS["user_admin"]


def test_sync_worker_syncs_writes_audit(monkeypatch):
    """Worker 同步成功：请求 SYNCED + 写 GITEE_ISSUE_SYNCED 审计。"""
    from app import main as m

    req = _request()
    m.TEAM_REQUESTS[req.id] = req
    m.AUDIT_EVENTS.clear()
    monkeypatch.setattr(m, "create_gitee_issue", lambda record: {"id": 9001, "number": "IKC9001", "html_url": "https://gitee.com/a/b/issues/IKC9001"})
    monkeypatch.setattr(m, "maybe_auto_create_task_after_sync", lambda record, user: record)

    result = m.sync_request_to_gitee_worker(req.id)
    assert result["status"] == "synced"
    assert m.TEAM_REQUESTS[req.id].sync_status == "SYNCED"
    assert any(ev.event_type == "GITEE_ISSUE_SYNCED" for ev in m.AUDIT_EVENTS.values())


def test_sync_worker_auto_creates_task_after_sync(monkeypatch):
    """DEV-016-C：同步成功后按仓库开关自动创建任务。"""
    from app import main as m

    req = _request()
    m.TEAM_REQUESTS[req.id] = req
    monkeypatch.setattr(m, "create_gitee_issue", lambda record: {"id": 1, "number": "n1", "html_url": "u"})
    calls = []

    def fake_auto(record, user):
        calls.append((record.id, user.id))
        return record

    monkeypatch.setattr(m, "maybe_auto_create_task_after_sync", fake_auto)
    m.sync_request_to_gitee_worker(req.id)
    assert calls, "同步成功后应触发自动建任务"


def test_sync_worker_failure_marks_failed_and_audits(monkeypatch):
    from app import main as m

    req = _request()
    m.TEAM_REQUESTS[req.id] = req
    m.AUDIT_EVENTS.clear()
    monkeypatch.setattr(m, "create_gitee_issue", lambda record: (_ for _ in ()).throw(RuntimeError("net down")))

    result = m.sync_request_to_gitee_worker(req.id)
    assert result["status"] == "error"
    assert m.TEAM_REQUESTS[req.id].sync_status == "SYNC_FAILED"
    assert any(ev.event_type == "GITEE_ISSUE_SYNC_FAILED" for ev in m.AUDIT_EVENTS.values())


def test_sync_handler_dispatches_in_celery_mode(monkeypatch):
    """启用 Celery：sync_team_request_to_gitee 投递给 worker，返回 queued=true（SYNCING）。"""
    monkeypatch.setenv("USE_CELERY", "true")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
    from app.workers.tasks import sync_gitee_request as sync_task

    calls = []

    def fake_delay(**kwargs):
        calls.append(kwargs)
        return type("R", (), {"id": "sync-1"})()

    monkeypatch.setattr(sync_task, "delay", fake_delay)
    from app.api.team_requests import sync_team_request_to_gitee

    req = _request()
    main.TEAM_REQUESTS[req.id] = req
    resp = sync_team_request_to_gitee(req.id, _actor())
    data = resp.data
    assert data["queued"] is True
    assert calls == [{"request_id": req.id}]
    assert main.TEAM_REQUESTS[req.id].sync_status == "SYNCING"


def test_sync_handler_inline_when_eager(monkeypatch):
    """未启用 Celery：同步 handler 仍走内联（本地/测试），并真实完成 SYNCED。"""
    monkeypatch.setenv("USE_CELERY", "false")
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    from app import main as m

    req = _request(status="DRAFT")
    m.TEAM_REQUESTS[req.id] = req
    m.AUDIT_EVENTS.clear()
    monkeypatch.setattr(m, "create_gitee_issue", lambda record: {"id": 1, "number": "n1", "html_url": "u"})
    monkeypatch.setattr(m, "maybe_auto_create_task_after_sync", lambda record, user: record)
    from app.api.team_requests import sync_team_request_to_gitee

    resp = sync_team_request_to_gitee(req.id, _actor())
    assert resp.data.sync_status == "SYNCED"


def test_create_task_worker_noop_unless_synced():
    """自动建任务 worker：未 SYNCED 时 no-op，不强行建任务。"""
    req = _request(request_id="REQ-9002", status="DRAFT")
    main.TEAM_REQUESTS[req.id] = req
    assert main.create_task_from_team_request_worker(req.id)["status"] == "not_synced"


def test_sync_worker_dispatches_auto_create_as_task(monkeypatch):
    """同步成功后自动建任务改为独立任务派发（eager 下同步执行）：worker 结束时请求进入 TASK_CREATED。"""
    from app import main as m

    req = _request(request_id="REQ-9003", status="DRAFT")
    m.TEAM_REQUESTS[req.id] = req
    monkeypatch.setattr(m, "create_gitee_issue", lambda record: {"id": 1, "number": "n1", "html_url": "u"})

    # 自动建任务：SYNCED → 创建/复用任务并把请求置 TASK_CREATED
    def fake_auto(record, user):
        return record.model_copy(update={"sync_status": "TASK_CREATED", "task_id": "TASK-created-1"})

    monkeypatch.setattr(m, "maybe_auto_create_task_after_sync", fake_auto)
    result = m.sync_request_to_gitee_worker(req.id)
    assert result["status"] == "synced"
    assert m.TEAM_REQUESTS[req.id].sync_status == "TASK_CREATED"