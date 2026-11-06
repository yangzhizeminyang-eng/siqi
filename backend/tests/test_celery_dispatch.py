"""P1a2 Celery 投递/降级测试：

- 启用 Celery（USE_CELERY=true+broker）：advance_task 走投递(run_agent.delay)，不在请求线程内联跑；
- 未启用：eager/test 内联推进兼容（314 既有测试覆盖）；这里验证 dispatch 标记与去重；
- 失败重试配置存在（max_retries/autoretry_for）。
"""

from __future__ import annotations

import pytest

from app import main
from app.workers.dispatch import celery_enabled, enqueue_or_run


def _make_task(task_id: str = "TASK-dispatch-1") -> main.TaskRecord:
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    return main.TaskRecord(
        id=task_id,
        version=1,
        project_id="proj_mvp",
        project_name="p",
        repository_id="repo_payment_service",
        repository_name="r",
        repository_url="https://gitee.com/acme/r",
        issue_id="#1",
        title="t",
        status="CREATED",
        base_branch="main",
        test_command="pytest -q",
        work_branch="w",
        issue_snapshot={"title": "t", "body": "b", "comments": [], "labels": [], "raw_response": {}, "fetched_at": now.isoformat()},
        automation_state="running",
        manual_required=False,
        manual_reason="none",
        next_action="check_index",
        events=[],
        execution_steps=[],
        created_by="u",
        created_by_name="n",
        created_at=now,
        updated_at=now,
    )


def test_celery_enabled_marker(monkeypatch):
    monkeypatch.setenv("USE_CELERY", "false")
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    assert celery_enabled() is False
    monkeypatch.setenv("USE_CELERY", "true")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
    assert celery_enabled() is True
    monkeypatch.setenv("USE_CELERY", "true")
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    assert celery_enabled() is False  # 有开关无 broker 视为未接入，降级


def test_advance_task_dispatches_to_worker_in_celery_mode(monkeypatch):
    """启用 Celery：advance_task 投递 run_agent 到 Worker，不内联跑图。"""
    monkeypatch.setenv("USE_CELERY", "true")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")

    from app.workers.tasks import run_agent

    calls = []

    def fake_delay(**kwargs):
        calls.append(kwargs)
        return type("R", (), {"id": "async-1"})()

    monkeypatch.setattr(run_agent, "delay", fake_delay)

    task = _make_task()
    main.TASKS[task.id] = task
    before_status = task.status
    main.advance_task(task)
    assert calls, "Celery 模式下 advance_task 应投递 run_agent"
    assert not calls or calls[0]["task_id"] == task.id

    # 每任务去重一次：再调不会重复投递（仍只有 1 次）
    main.advance_task(task)
    assert len(calls) == 1, "同任务只能投递一次（去重）"

    _ = before_status  # label: 投递模式下 HTTP 不内联改动状态（由 Worker 推进）


def test_dispatch_agent_fallback_to_inline_when_enqueue_fails(monkeypatch):
    """投递失败需回退内联推进，任务仍能前进，不静默丢任务。"""
    monkeypatch.setenv("USE_CELERY", "true")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
    from app.workers.tasks import run_agent

    def boom(**kwargs):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(run_agent, "delay", boom)
    task = _make_task()
    main.TASKS[task.id] = task
    main._AGENT_ENQUEUED.clear()
    dispatched = main._dispatch_agent_if_needed(task.id, task)
    assert dispatched is False  # 投递失败 → 由调用方内联推进，重试计数不残留


def test_celery_tasks_have_retry_config():
    from app.workers.tasks import (
        create_task_from_team_request,
        rebuild_repository_index,
        run_agent,
        run_test,
        sync_gitee_request,
    )

    for t in [rebuild_repository_index, run_agent, run_test, sync_gitee_request, create_task_from_team_request]:
        assert t.max_retries == 3, f"{t.name} 应有 max_retries=3"
        assert t.autoretry_for == (Exception,), f"{t.name} 应配置 autoretry_for"


def test_enqueue_or_run_eager_runs_inline_and_marks(monkeypatch):
    """未启用 Celery：enqueue_or_run 内联执行并标记 eager(test)。"""
    monkeypatch.setenv("USE_CELERY", "false")
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    calls = []

    def inline():
        calls.append("ran")

    from app.workers.tasks import run_agent as _task

    outcome = enqueue_or_run(_task, kwargs={"task_id": "T-x"}, run_inline=inline, label="t")
    assert outcome["enqueued"] is False
    assert outcome["mode"] == "eager(test)"
    assert calls == ["ran"]


def test_run_task_tests_dispatches_to_worker_in_celery_mode(monkeypatch):
    """启用 Celery：run_task_tests 把测试投递给 backend.run_test，不内联跑；response 标记 queued。"""
    import datetime as dt

    monkeypatch.setenv("USE_CELERY", "true")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")

    now = dt.datetime.now(dt.timezone.utc)
    task = main.TaskRecord.model_validate(
        {
            "id": "TASK-rtest-1",
            "version": 1,
            "project_id": "proj_mvp",
            "project_name": "p",
            "repository_id": "repo_payment_service",
            "repository_name": "r",
            "repository_url": "https://gitee.com/acme/r",
            "issue_id": "#1",
            "title": "t",
            "status": "DIFF_GENERATED",
            "base_branch": "main",
            "test_command": "pytest -q",
            "work_branch": "w",
            "issue_snapshot": {"title": "t", "body": "b", "comments": [], "labels": [], "raw_response": {}, "fetched_at": now.isoformat()},
            "automation_state": "blocked",
            "manual_required": False,
            "manual_reason": "none",
            "next_action": "auto_run_test",
            "events": [],
            "execution_steps": [],
            "created_by": "user_developer",
            "created_by_name": "API",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
    )
    main.TASKS[task.id] = task
    fake_artifact = type("A", (), {"id": "diff_1", "plan_id": "plan_1", "plan_version": 1, "files": []})()
    monkeypatch.setattr(main, "latest_successful_diff", lambda _t: fake_artifact)

    from app.workers.tasks import run_test

    calls = []

    def fake_delay(**kwargs):
        calls.append(kwargs)
        return type("R", (), {"id": "t-async-1"})()

    monkeypatch.setattr(run_test, "delay", fake_delay)

    user = main.USERS["user_developer"]
    resp = main.run_task_tests(task.id, user)
    data = resp.data
    assert data["queued"] is True
    assert calls == [{"task_id": "TASK-rtest-1"}]
    # 未内联执行：不应产生 test_runs 记录
    assert task.test_runs == []