"""DEV-019 通知链真实 MySQL 落库校验（评审「高风险/审批完成/24h 催办真实 MySQL 流量」）。

需要环境变量 MYSQL_TEST_URL 指向一个可用 MySQL(pymysql) 测试库：
    $env:MYSQL_TEST_URL="mysql+pymysql://user:pass@127.0.0.1:3306/agent_dev019?charset=utf8mb4"
未设置时跳过（无 MySQL 的 CI/开发仍全绿）。测试不打印任何密码/连接串细节。

内容：备用真实 MySQL 迁移到 head → 生成 RISK_HIGH / APPROVAL_REQUIRED / APPROVAL_RESULT /
NUDGE 四类通知 → 断言写透入 `notifications` 表并可经 reads/DB 真源读回（替代仅内存/mock 证据）。
"""
from __future__ import annotations

import datetime as _dt
import os

import pytest

from app import main

pytestmark = pytest.mark.skipif(
    not os.getenv("MYSQL_TEST_URL", "").startswith("mysql+pymysql://"),
    reason="MYSQL_TEST_URL 未配置为 MySQL(pymysql)，跳过 DEV-019 MySQL 真实落库校验",
)


def _enable_mysql(monkeypatch) -> None:
    url = os.environ["MYSQL_TEST_URL"]
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", url)
    from app.db.session import get_engine, get_session_factory

    get_engine.cache_clear()
    get_session_factory.cache_clear()


def _clear_caches() -> None:
    from app.db.session import get_engine, get_session_factory

    get_engine.cache_clear()
    get_session_factory.cache_clear()


def _task(creator_id: str, creator_name: str, risk: str, now):
    return main.TaskRecord(
        id="TASK-MYSQL-NUDGE", version=1, project_id="proj_payment", project_name="支付中台",
        repository_id="repo_payment_service", repository_name="payment-service",
        repository_url="https://gitee.com/o/r.git", issue_id="MY-1", title="MySQL 真实通知验收",
        status="WAITING_APPROVAL", base_branch="main", test_command="pytest tests/ -q",
        approval_owner_id="user_approver", approval_owner_name="王琳", work_branch="agent/main/v1",
        issue_snapshot=main.IssueSnapshotRecord(title="MySQL 真实通知验收", body="b", comments=[], labels=[], raw_response={}, fetched_at=now),
        automation_state="waiting_approval", manual_required=True, manual_reason="approval_required",
        next_action="review_plan", risk_level=risk, events=[], created_by=creator_id,
        created_by_name=creator_name, created_at=now, updated_at=now,
    )


def _plan(task, now, age_hours: float):
    plan = main.ModificationPlan(
        id=f"PLAN-{task.id}-1", task_id=task.id, version=1, goal="g", file_scope=[], steps=[],
        risk_level=task.risk_level or "medium", risk_reason="r", test_suggestions=[], assumptions=[],
        rollback_plan=[], generated_from_analysis_revision=1, generated_from_context_ids=[],
        provider="mvp-rule-based", status="WAITING_APPROVAL", created_by=task.created_by,
        created_by_name=task.created_by_name,
        created_at=now - _dt.timedelta(hours=age_hours),
    )
    task.plans.append(plan)
    return plan


def test_notification_chain_persists_to_mysql(tmp_path, monkeypatch):
    _enable_mysql(monkeypatch)
    from app.db import migrate
    from app.db.repositories import load_notifications
    from app.services import notification_service

    try:
        migrate.ensure_schema_current(upgrade=True)  # 真实 MySQL 迁移到 head
        now = _dt.datetime.now(_dt.timezone.utc)

        hi = _task("user_developer", "张伟", "high", now); hip = _plan(hi, now, 0)
        hi_n = notification_service.notify_approval_required(hi, hip, "user_approver", high_risk=True)

        lo = _task("user_developer", "张伟", "low", now); lop = _plan(lo, now, 0)
        lo_n = notification_service.notify_approval_required(lo, lop, "user_approver", high_risk=False)
        done_n = notification_service.notify_approval_completed(lo, lop, "approved", "user_approver", "ok")

        nudge = _task("user_developer", "张伟", "medium", now); _plan(nudge, now, 30)
        nu_n = notification_service.send_approval_reminder(nudge, main.USERS["user_developer"])

        # 直接从 MySQL 读回（load_notifications 走 DB 真源）
        rows = list(load_notifications())
        by_id = {r["id"]: r for r in rows}
        assert hi_n.id in by_id and by_id[hi_n.id]["type"] == "RISK_HIGH"
        assert lo_n.id in by_id and by_id[lo_n.id]["type"] == "APPROVAL_REQUIRED"
        assert done_n.id in by_id and by_id[done_n.id]["type"] == "APPROVAL_RESULT" and by_id[done_n.id]["recipient_id"] == "user_developer"
        assert nu_n.id in by_id and by_id[nu_n.id]["type"] == "NUDGE"

        # 清空内存后重启恢复仍是 DB 真源
        main.NOTIFICATIONS.clear()
        from app.db.bootstrap import restore_from_db

        restore_from_db()
        reloaded = {n.id: n for n in main.NOTIFICATIONS.values()}
        assert hi_n.id in reloaded and reloaded[nu_n.id].type == "NUDGE"
    finally:
        _clear_caches()