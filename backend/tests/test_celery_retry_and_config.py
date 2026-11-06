"""Celery 专项：重试语义 / 配置收敛 / _AGENT_ENQUEUED 清理 / smoke 验收。"""

from __future__ import annotations

import datetime as _dt

import pytest

from app import main
from app.core.runtime_config import load_runtime_settings
from app.workers.dispatch import celery_enabled, enqueue_or_run


def _task(task_id: str = "TASK-spec-1", status: str = "CREATED") -> main.TaskRecord:
    now = _dt.datetime.now(_dt.timezone.utc)
    return main.TaskRecord.model_validate(
        {
            "id": task_id, "version": 1, "project_id": "proj_mvp", "project_name": "p",
            "repository_id": "repo_payment_service", "repository_name": "r",
            "repository_url": "https://gitee.com/acme/r", "issue_id": "#1", "title": "t", "status": status,
            "base_branch": "main", "test_command": "pytest -q", "work_branch": "w",
            "issue_snapshot": {"title": "t", "body": "b", "comments": [], "labels": [], "raw_response": {}, "fetched_at": now.isoformat()},
            "automation_state": "running", "manual_required": False, "manual_reason": "none",
            "next_action": "check_index", "events": [], "execution_steps": [],
            "created_by": "user_developer", "created_by_name": "n", "created_at": now.isoformat(), "updated_at": now.isoformat(),
        }
    )


# ---- 1. 配置到 Celery 时四条链路派发 ----

def test_agent_dispatch_when_celery_enabled(monkeypatch):
    monkeypatch.setenv("USE_CELERY", "true")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
    from app.workers.tasks import run_agent

    calls = []

    def fake(**kw):
        calls.append(kw)
        return type("R", (), {"id": "r"})

    monkeypatch.setattr(run_agent, "delay", fake)
    task = _task()
    main.TASKS[task.id] = task
    assert main._dispatch_agent_if_needed(task.id, task) is True
    assert calls and calls[0]["task_id"] == task.id
    assert calls[0].get("token")  # H2: dispatch 携带派发锁 token，供 worker CAS 释放


def test_index_dispatch_when_celery_enabled(monkeypatch):
    monkeypatch.setenv("USE_CELERY", "true")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
    from app.workers.tasks import rebuild_repository_index

    calls = []
    monkeypatch.setattr(rebuild_repository_index, "delay", lambda **kw: (calls.append(kw) or type("R", (), {"id": "i"})()))
    outcome = main._enqueue_index_build("idx-1")
    assert calls == [{"index_id": "idx-1"}]
    assert outcome["mode"] == "redis"


# ---- 2. eager 降级标记清晰 ----

def test_eager_marker_when_celery_disabled(monkeypatch):
    monkeypatch.setenv("USE_CELERY", "false")
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    from app.workers.tasks import run_agent as t

    ran = []
    outcome = enqueue_or_run(t, kwargs={"task_id": "x"}, run_inline=lambda: ran.append(1), label="t")
    assert outcome["enqueued"] is False and outcome["mode"] == "eager(test)" and ran == [1]


# ---- 3. production/team 缺 broker 不误报异步可用 ----

def test_production_missing_broker_not_misreported(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("USE_CELERY", "true")
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    assert celery_enabled() is False          # 不误报异步可用
    assert load_runtime_settings().celery_required() is True  # 但确属必须异步的运行态


# ---- 4/5. run_agent 失败重试语义 ----

def test_first_failure_not_terminal(monkeypatch):
    t = _task()
    main.record_agent_attempt_failure(t, RuntimeError("boom"), is_last=False)
    assert t.status != "FAILED", "非最后尝试不应置终态"
    assert getattr(t, "retry_count", 0) >= 1
    assert t.next_action == "worker_retry"
    assert "可重试" in (t.failure_reason or "")


def test_last_failure_becomes_terminal(monkeypatch):
    t = _task()
    main.record_agent_attempt_failure(t, RuntimeError("boom"), is_last=True)
    assert t.status == "FAILED", "最后一次尝试失败才进入终态"


# ---- 6/7. _AGENT_ENQUEUED 清理与重派发 ----

def test_recovery_redispatch_after_clear(monkeypatch):
    monkeypatch.setenv("USE_CELERY", "true")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
    from app.workers.tasks import run_agent

    calls = []

    def fake(**kw):
        calls.append(kw)
        return type("R", (), {"id": "r"})()

    monkeypatch.setattr(run_agent, "delay", fake)
    task = _task()
    main.TASKS[task.id] = task
    main._AGENT_ENQUEUED.add(task.id)          # 假设已投过
    main._clear_agent_enqueued(task.id)         # 恢复/审批后清一次
    main.advance_task(task)                     # 应重新派发
    assert any(c.get("task_id") == task.id for c in calls), "清理后应允许重新派发 worker"


def test_agent_enqueued_cleared_on_terminal(monkeypatch):
    monkeypatch.setenv("USE_CELERY", "true")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
    task = _task(status="COMPLETED")            # 终态
    main.TASKS[task.id] = task
    main._AGENT_ENQUEUED.add(task.id)
    main.advance_task(task)
    assert task.id not in main._AGENT_ENQUEUED, "终态后应清理去重集合"


# ---- 8. smoke 任务可验证 worker 消费日志 ----

def test_smoke_task_applies(monkeypatch):
    from app.workers.tasks import smoke

    assert smoke.name == "backend.smoke"
    r = smoke.apply(kwargs={"message": "probe"})
    assert r.status == "SUCCESS"
    assert r.result == {"message": "probe", "echo": True}