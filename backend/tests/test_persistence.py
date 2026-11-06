"""P0 业务数据持久化测试：写透 / 往返 / 重启恢复 / 建表幂等。

这些用例显式开启 USE_SQLALCHEMY=true，并把 DATABASE_URL 指向临时 sqlite 文件，
然后复位 SQLAlchemy 引擎/会话缓存——因此不会污染默认库，也不会受 conftest 的
USE_SQLALCHEMY=false 影响。
"""

from __future__ import annotations

import datetime as _dt
import os

import pytest

from app.db import session as db_session
from app.db import repositories
from app.db import bootstrap


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """临时 sqlite 数据库：开启持久化 + 复位缓存，测试结束自动回滚环境。

    同时快照/恢复 main 的模块级业务 dict，避免测试期间的清空+restore 泄漏到
    后续用例（否则会破坏其它测试对用户/项目/仓库的预期）。
    """
    from app import main as m

    stores = [
        "USERS",
        "PROJECTS",
        "REPOSITORIES",
        "TASKS",
        "TEAM_REQUESTS",
        "AUDIT_EVENTS",
        "PASSWORD_RESET_REQUESTS",
        "REPOSITORY_INDEXES",
    ]
    snapshot = {name: dict(getattr(m, name)) if isinstance(getattr(m, name, None), dict) else getattr(m, name, None) for name in stores}

    db_file = tmp_path / "persist.db"
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("DATA_DIR", tmp_path.as_posix())
    # 复位引擎/会话缓存，让新 DATABASE_URL 生效
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    db_session.create_schema()
    yield db_file

    for name in stores:
        store = getattr(m, name, None)
        if isinstance(store, dict):
            store.clear()
            store.update(snapshot[name])
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()


def _task_dict(task_id: str = "TASK-roundtrip-1") -> dict:
    now = "2026-09-07T12:00:00Z"
    return {
        "id": task_id,
        "version": 1,
        "project_id": "proj_a",
        "project_name": "演示项目",
        "repository_id": "repo_a",
        "repository_name": "payment_service",
        "repository_url": "https://gitee.com/acme/payment_service",
        "issue_id": "#12",
        "title": "修复订单列表空指针",
        "status": "PLAN_GENERATED",
        "risk_level": "medium",
        "risk_source": "plan",
        "base_branch": "main",
        "test_command": "pytest -q",
        "approval_owner_id": "user_approver",
        "approval_owner_name": "审批人",
        "work_branch": "feature/task1123",
        "issue_snapshot": {
            "title": "修复订单列表空指针",
            "body": "订单列表页对空订单会 NPE",
            "comments": [{"id": "c1", "user_name": "张三", "body": "已复现", "created_at": None, "raw_response": {}}],
            "labels": ["bug"],
            "raw_response": {"id": 12},
            "fetched_at": now,
        },
        "analysis": {
            "task_id": task_id,
            "task_version": 1,
            "decision_summary": ["确认为空指针"],
            "overview": "概览",
            "findings": ["列表未判空"],
            "affected_areas": ["order/list"],
            "suggested_risk_level": "medium",
            "risk_reason": "改动较小",
            "uncertainties": ["影响面待查"],
            "questions": [
                {"id": "q1", "question": "是否需要兼容空数组", "reason": "边界", "blocking": False, "default_answer": "是", "answer": None, "answered": False}
            ],
            "sources": [{"source_type": "issue_body", "source_ref": "L12", "content": "描述", "relevance": "high"}],
            "prompt_injection_flags": [],
            "generated_at": now,
            "provider": "mvp-rule-based",
            "revision": 1,
        },
        "automation_state": "running",
        "manual_required": False,
        "manual_reason": "none",
        "next_action": "wait_approval",
        "retry_count": 0,
        "base_commit_sha": "abc123",
        "code_contexts": [
            {
                "chunk_id": "ck1",
                "file_path": "order/list.py",
                "start_line": 1,
                "end_line": 10,
                "language": "python",
                "symbol_name": "render_orders",
                "content": "def render_orders():",
                "score": 0.92,
                "commit_sha": "abc123",
                "index_id": "idx_1",
                "fresh": True,
                "source": "milvus",
            }
        ],
        "similar_task_refs": [],
        "plans": [
            {
                "id": "plan_1",
                "task_id": task_id,
                "version": 1,
                "goal": "修复空指针",
                "file_scope": [{"file_path": "order/list.py", "change_type": "modify", "reason": "判空", "risk_hint": None}],
                "steps": [{"order": 1, "title": "加判空", "description": "在渲染前判空", "target_files": ["order/list.py"], "risk_hint": None}],
                "risk_level": "medium",
                "risk_reason": "改动小",
                "test_suggestions": ["补单测"],
                "assumptions": ["行为不变"],
                "rollback_plan": ["回滚提交"],
                "generated_from_analysis_revision": 1,
                "generated_from_context_ids": ["ck1"],
                "analysis_risk_level": "medium",
                "referenced_historical_tasks": [],
                "provider": "mvp-rule-based",
                "status": "WAITING_APPROVAL",
                "risk_adjust_reason": None,
                "risk_adjust_from": None,
                "approved_by_id": None,
                "approved_by_name": None,
                "approved_at": None,
                "approval_comment": None,
                "created_by": "user_developer",
                "created_by_name": "开发者",
                "created_at": now,
            }
        ],
        "diffs": [
            {
                "id": "diff_1",
                "task_id": task_id,
                "plan_id": "plan_1",
                "plan_version": 1,
                "execution_step": "generate_diff",
                "generation_status": "SUCCEEDED",
                "execution_mode": "DEMO_FALLBACK",
                "is_demo_fallback": True,
                "fallback_reason": "demo",
                "real_failure_reason": None,
                "generated_at": now,
                "files": [
                    {
                        "id": "f1",
                        "file_path": "order/list.py",
                        "change_type": "modify",
                        "patch": "--- a\n+++ b\n@@ -1 +1 @@",
                        "added_lines": 2,
                        "deleted_lines": 0,
                        "before": ["a"],
                        "after": ["a", "b"],
                    }
                ],
                "test_summary": None,
                "pr_description_draft": None,
            }
        ],
        "test_runs": [
            {
                "id": "tr1",
                "task_id": task_id,
                "diff_artifact_id": "diff_1",
                "plan_id": "plan_1",
                "plan_version": 1,
                "command": "pytest -q",
                "exit_code": 0,
                "stdout": "1 passed",
                "stderr": "",
                "status": "passed",
                "duration": 1.2,
                "attempt": 1,
                "started_at": now,
                "finished_at": now,
                "failure_reason": None,
                "blocked_reason": None,
                "runner_mode": "mocked_runner",
            }
        ],
        "pr_descriptions": [
            {
                "id": f"PRD-{task_id}-1",
                "task_id": task_id,
                "plan_id": "plan_1",
                "plan_version": 1,
                "diff_artifact_id": "diff_1",
                "test_run_id": "tr1",
                "version": 1,
                "background": "修复空指针",
                "change_summary": "列表判空",
                "impact_scope": "订单列表",
                "test_result": "通过",
                "risk_notes": "无",
                "rollback_notes": "回滚",
                "generation_status": "GENERATED",
                "failure_reason": None,
                "generated_at": now,
                "generated_by": "user_developer",
                "generated_by_name": "开发者",
            }
        ],
        "events": [
            {
                "id": "ev1",
                "event_type": "TASK_CREATED",
                "resource_type": "task",
                "resource_id": task_id,
                "actor_id": "user_developer",
                "actor_name": "开发者",
                "summary": "创建任务",
                "diff_summary": [],
                "created_at": now,
                "execution_step_ids": [],
            }
        ],
        "execution_steps": [
            {
                "id": "st1",
                "task_id": task_id,
                "event_id": "ev1",
                "node": "ai_analysis",
                "title": "AI 分析",
                "status": "completed",
                "input": "input",
                "output": "output",
                "duration_ms": 10.0,
                "failure_reason": None,
                "blocked_reason": None,
                "created_at": now,
            }
        ],
        "created_by": "user_developer",
        "created_by_name": "开发者",
        "created_at": now,
        "updated_at": now,
    }


def test_task_roundtrip_is_exact(fresh_db):
    """TaskRecord（含嵌套 计划/审批/Diff/测试/PR描述/上下文/分析/事件/步骤）落库后重载应逐字段一致。"""
    from app import main

    task = main.TaskRecord.model_validate(_task_dict())
    repositories.upsert_task(task)

    loaded = repositories.load_tasks()
    assert len(loaded) == 1
    rebuilt = main.TaskRecord.model_validate(loaded[0])

    # 逐字段（含嵌套）精确对比：任何漏字段/丢字段都会在这里暴露
    assert rebuilt.model_dump(mode="json") == task.model_dump(mode="json")
    assert rebuilt.id == task.id
    assert len(rebuilt.plans) == 1
    assert rebuilt.plans[0].file_scope[0].file_path == "order/list.py"
    assert len(rebuilt.diffs) == 1
    assert rebuilt.diffs[0].files[0].change_type == "modify"
    assert len(rebuilt.test_runs) == 1
    assert rebuilt.test_runs[0].status == "passed"
    assert rebuilt.pr_descriptions[0].generation_status == "GENERATED"
    assert rebuilt.issue_snapshot.comments[0].body == "已复现"
    assert rebuilt.analysis.questions[0].question == "是否需要兼容空数组"


def test_restart_restore_recovers_core_data(fresh_db):
    """模拟重启：清空内存 dict + 重跑 restore_from_db，核心数据应从 DB 恢复。"""
    from app import main

    # 造数据
    repositories.upsert_task(main.TaskRecord.model_validate(_task_dict("TASK-restart-1")))
    repositories.upsert_user(
        main.UserRecord(id="user_new", name="新人", email="new@company.com", role="developer", password_hash="h1")
    )
    now = _dt_utc()
    repositories.upsert_project(
        main.ProjectRecord(id="proj_restart", name="重启项目", description="恢复", created_at=now, updated_at=now)
    )
    # 代码种子项目也应被持久化（bootstrap 首启时会把它写入 DB）
    seed_project = list(main.PROJECTS.values())[0]
    repositories.upsert_project(seed_project)

    # 模拟重启：清空所有模块级 dict
    main.TASKS.clear()
    main.USERS.clear()
    main.PROJECTS.clear()
    main.REPOSITORIES.clear()
    main.AUDIT_EVENTS.clear()
    main.TEAM_REQUESTS.clear()

    counts = bootstrap.restore_from_db()

    assert counts.get("tasks", 0) == 1
    assert "TASK-restart-1" in main.TASKS
    new_task = main.TASKS["TASK-restart-1"]
    assert new_task.plans[0].status == "WAITING_APPROVAL"
    assert "user_new" in main.USERS
    # 代码种子项目应被持久化并从 DB 恢复（首次建库种子）
    assert main.PROJECTS, "重启后项目不应为空"
    ids = {p.id for p in main.PROJECTS.values()}
    assert "proj_restart" in ids and seed_project.id in ids


def test_create_schema_is_idempotent(fresh_db):
    """create_schema 重复调用应幂等（表已存在不报错）。"""
    from app.models.base import Base

    db_session.create_schema()
    db_session.create_schema()
    from sqlalchemy import inspect

    names = set(inspect(db_session.get_engine()).get_table_names())
    assert "users" in names
    assert "projects" in names
    assert "repositories" in names
    assert "tasks" in names
    assert "plans" in names
    assert "diffs" in names
    assert "test_runs" in names
    assert "pr_descriptions" in names
    assert "team_requests" in names
    assert "audit_events" in names


def test_persistence_disabled_is_noop():
    """USE_SQLALCHEMY=false（测试默认）时，写与读都应为空操作，保持既有用例隔离。"""
    os.environ["USE_SQLALCHEMY"] = "false"
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    assert repositories.load_users() == []
    repositories.upsert_user(
        type("U", (), {"id": "x", "name": "n", "email": "e", "role": "developer", "password_hash": "h"})()
    )
    assert repositories.load_users() == []
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()


def _dt_utc():
    return _dt.datetime(2026, 9, 7, 12, 0, 0, tzinfo=_dt.timezone.utc)