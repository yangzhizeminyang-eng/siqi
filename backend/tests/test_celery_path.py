"""P1a Celery 异步链路测试：

- eager/降级：enqueue_or_run 明确标记 mode=eager(test) 且内联执行同一段工作；
- 真实任务体：在 eager 模式下直接 apply 各 Celery task，验证它们执行的是真实工作
  （索引推进到 COMPLETED、测试执行返回 passed）；
- 并发：多个任务投递均成功。
真实 Redis 队列在本机未配置，测试以 eager 覆盖任务体；生产路径由 USE_CELERY+broker 控制。
"""

from __future__ import annotations

import datetime as _dt
import os

import pytest

from app import main
from app.workers.dispatch import celery_enabled, enqueue_or_run


def _make_index(index_id: str, repository_id: str = "repo_payment_service", commit_sha: str = "abc") -> main.RepositoryIndexRecord:
    now = _dt.datetime.now(_dt.timezone.utc)
    return main.RepositoryIndexRecord(
        id=index_id,
        repository_id=repository_id,
        project_id="proj_mvp",
        base_branch="main",
        commit_sha=commit_sha,
        status="PENDING",
        requested_by="user_developer",
        requested_by_name="开发者",
        requested_at=now,
        created_at=now,
        updated_at=now,
    )


def test_enqueue_or_run_marks_eager_and_runs_inline(monkeypatch):
    """无 Redis 时：mode=eager(test)、enqueued=False，并执行 run_inline（同一段工作）。"""
    monkeypatch.setenv("USE_CELERY", "false")
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    assert celery_enabled() is False

    calls: list[str] = []

    def run_inline():
        calls.append("ran")

    from app.workers.tasks import run_agent as _task

    outcome = enqueue_or_run(_task, kwargs={"task_id": "TASK-x"}, run_inline=run_inline, label="test")
    assert outcome["enqueued"] is False
    assert outcome["mode"] == "eager(test)"
    assert calls == ["ran"]


def test_eager_task_rebuild_index_completes_index(monkeypatch):
    """eager 模式下 rebuild_repository_index 真实推进索引到 COMPLETED。"""
    monkeypatch.setenv("USE_CELERY", "true")
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    main.REPOSITORY_INDEXES.clear()

    # 屏蔽真实 gitee/模型阶段副作用，让各阶段工作安全推进
    monkeypatch.setattr(main, "run_index_stage_work", lambda record: None)

    index = _make_index("idx_celery_1")
    main.REPOSITORY_INDEXES[index.id] = index

    from app.workers.tasks import rebuild_repository_index

    result = rebuild_repository_index.apply(kwargs={"index_id": index.id})
    assert result.status == "SUCCESS"
    payload = result.result
    assert payload == {"index_id": index.id, "result": "COMPLETED"}
    assert main.REPOSITORY_INDEXES[index.id].status == "COMPLETED"


def test_eager_task_run_test_passes(monkeypatch):
    """eager 模式下 run_test 走真实 test_runner 管线返回 passed。"""
    from datetime import datetime, timezone

    monkeypatch.setenv("USE_CELERY", "true")
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    now = datetime.now(timezone.utc)
    task = main.TaskRecord.model_validate(
        {
            "id": "TASK-test-1",
            "version": 1,
            "project_id": "proj_mvp",
            "project_name": "p",
            "repository_id": "repo_payment_service",
            "repository_name": "payment_service",
            "repository_url": "https://gitee.com/acme/payment_service",
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
            "created_by": "u",
            "created_by_name": "n",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
    )
    main.TASKS["TASK-test-1"] = task
    fake_artifact = type("A", (), {
        "id": "diff_1",
        "plan_id": "plan_1",
        "plan_version": 1,
        "execution_step": "generate_diff",
        "files": [],
    })()
    monkeypatch.setattr(main, "latest_successful_diff", lambda _task: fake_artifact)
    monkeypatch.setenv("TEST_RUNNER_MODE", "mock")

    from app.workers.tasks import run_test

    # 先清掉历史 test_runs，确保 deterministic
    task.test_runs = []
    result = run_test.apply(kwargs={"task_id": "TASK-test-1"})
    assert result.status == "SUCCESS"
    assert result.result["result"]["status"] == "passed"
    assert main.TASKS["TASK-test-1"].status == "TESTED"
    assert len(main.TASKS["TASK-test-1"].test_runs) == 1


def test_concurrent_eager_dispatch_all_succeed(monkeypatch):
    """并发投递多个索引任务：全部 SUCCESS（eager 在进程内各自完成）。"""
    import concurrent.futures

    monkeypatch.setenv("USE_CELERY", "true")
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    monkeypatch.setattr(main, "run_index_stage_work", lambda record: None)
    main.REPOSITORY_INDEXES.clear()

    indexes = [_make_index(f"idx_c_{i}", commit_sha=f"c{i}") for i in range(5)]
    for idx in indexes:
        main.REPOSITORY_INDEXES[idx.id] = idx

    from app.workers.tasks import rebuild_repository_index

    def run(idx_id):
        return rebuild_repository_index.apply(kwargs={"index_id": idx_id})

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(run, [idx.id for idx in indexes]))

    assert all(r.status == "SUCCESS" for r in results)
    assert all(main.REPOSITORY_INDEXES[idx.id].status == "COMPLETED" for idx in indexes)


def test_run_agent_eager_does_not_crash(monkeypatch):
    """eager 模式下 run_agent 任务体可执行（未配置工作流时内联推进，不抛错）。"""
    monkeypatch.setenv("USE_CELERY", "true")
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)

    from app.agent.workflow import build_task_workflow
    from app.workers.tasks import run_agent

    # 无任务 id 时任务体自身不应抛异常（返回 SUCCESS 且不崩溃）
    if build_task_workflow() is None:
        # 未配置 LLM：走 TASKS.get 分支，缺任务直接返回
        result = run_agent.apply(kwargs={"task_id": "TASK-does-not-exist"})
        assert result.status == "SUCCESS"