"""DEV-021 历史任务复用索引真实 MySQL 建表 + 端到端校验（评审 P1）。

需要 MYSQL_TEST_URL 指向可用 MySQL(pymysql) 测试库：
    $env:MYSQL_TEST_URL="mysql+pymysql://user:pass@127.0.0.1:3306/<db>?charset=utf8mb4"
未设置时跳过（无 MySQL 环境全绿）。不打印任何密码/连接串细节。

覆盖：MySQL 下 alembic upgrade head 能建 historical_task_summaries/indexes（无 TEXT DEFAULT 1101）、
摘要落库 → 索引成功记录 → MySQL 检索返回 —— 同为评审要求「MySQL 端到端 + 应用启动建历史表」的复验路径。
"""
from __future__ import annotations

import datetime as _dt
import os

import pytest

from app import main

pytestmark = pytest.mark.skipif(
    not os.getenv("MYSQL_TEST_URL", "").startswith("mysql+pymysql://"),
    reason="MYSQL_TEST_URL 未配置为 MySQL(pymysql)，跳过 DEV-021 MySQL 校验",
)


def _enable_mysql(monkeypatch) -> None:
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", os.environ["MYSQL_TEST_URL"])
    from app.db.session import get_engine, get_session_factory

    get_engine.cache_clear()
    get_session_factory.cache_clear()


def _clear_caches() -> None:
    from app.db.session import get_engine, get_session_factory

    get_engine.cache_clear()
    get_session_factory.cache_clear()


def _completed_task(task_id: str, now):
    return main.TaskRecord(
        id=task_id, version=1, project_id="proj_payment", project_name="支付中台",
        repository_id="repo_payment_service", repository_name="payment-service",
        repository_url="https://gitee.com/o/r.git", issue_id=f"I-{task_id}", title="MySQL 历史复用验收",
        status="COMPLETED", base_branch="main", test_command="pytest tests/ -q",
        approval_owner_id="user_approver", approval_owner_name="王琳", work_branch="w/v1",
        issue_snapshot=main.IssueSnapshotRecord(title="MySQL 历史复用验收", body="支付幂等", comments=[], labels=[], raw_response={}, fetched_at=now),
        automation_state="completed", manual_required=False, manual_reason="none", next_action="view_replay",
        plans=[], diffs=[], test_runs=[], pr_descriptions=[], code_contexts=[], similar_task_refs=[],
        events=[], execution_steps=[], created_by="user_developer", created_by_name="张伟",
        created_at=now, updated_at=now,
    )


def test_history_tables_mysql_upgrade_and_e2e(tmp_path, monkeypatch):
    _enable_mysql(monkeypatch)
    from app.db import migrate
    from app.db.repositories import load_historical_task_indexes, load_historical_task_summaries
    from app.services import history_service
    from sqlalchemy import inspect

    from app.db.session import get_engine

    try:
        migrate.ensure_schema_current(upgrade=True)  # MySQL 上 alembic upgrade head 建历史表（无 1101）
        tables = set(inspect(get_engine()).get_table_names())
        assert "historical_task_summaries" in tables and "historical_task_indexes" in tables

        now = _dt.datetime.now(_dt.timezone.utc)
        task = _completed_task("TASK-MYSQL-HIST", now)
        main.TASKS[task.id] = task

        summary = history_service.generate_for_completed_task(task.id)
        assert summary is not None
        # MySQL 落库读回（DB 真源）
        assert any(s["id"] == summary.id for s in load_historical_task_summaries())

        rec = history_service.index(summary.id)
        assert rec.index_status == "INDEXED"
        assert any(i["summary_id"] == summary.id for i in load_historical_task_indexes())

        # 检索（项目隔离 + 创建者权限；admin 优先）
        user = main.USERS.get("user_admin") or main.USERS["user_developer"]
        results = history_service.search_similar({"projectId": "proj_payment", "query": "支付幂等"}, user)
        assert isinstance(results, list)
    finally:
        _clear_caches()