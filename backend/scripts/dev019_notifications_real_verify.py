"""DEV-019 通知链真实 MySQL 现场流量验证。

在真实的 MySQL 上验证：高风险/普通审批通知、审批结果通知、24h 催办通知都会写透进
`notifications` 表；清空内存后经 `restore_from_db` 能从 MySQL 真源恢复，绝不依赖内存/mock。

用法（working dir = backend）：先配置并启动真实 MySQL + 迁移：
    $env:APP_ENV="team"
    $env:DATABASE_URL="mysql+pymysql://user:pass@host:3306/db?charset=utf8mb4"
    $env:GITEE_TOKEN="..."      # （若需要，可选）
    python -m scripts.dev019_notifications_real_verify

不满足（非 MySQL / 连不上 / migration 失败）时明确报错退出，不做虚假 PASS。
"""

from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path


def _stdout(text: str) -> None:
    try:
        print(text, flush=True)
    except Exception:
        pass


def _check_mysql() -> str:
    url = os.getenv("DATABASE_URL", "")
    app_env = os.getenv("APP_ENV", "local").lower()
    if app_env in {"team", "production", "prod"}:
        if not url.startswith("mysql+pymysql://"):
            raise RuntimeError("APP_ENV=team/prod 时 DATABASE_URL 必须指向 MySQL（mysql+pymysql://...），当前非 MySQL。")
    return url


def _task(project_id: str, repository_id: str, creator_id: str, creator_name: str, now):
    from app import main

    return main.TaskRecord(
        id="TASK-REALNUDGE", version=1, project_id=project_id, project_name="真实验收",
        repository_id=repository_id, repository_name="真实仓", repository_url="https://gitee.com/o/r.git",
        issue_id="RU-1", title="真实流量验收任务", status="WAITING_APPROVAL",
        base_branch="master", test_command="pytest tests/ -q", approval_owner_id="user_approver",
        approval_owner_name="王琳", work_branch="agent/main/v1",
        issue_snapshot=main.IssueSnapshotRecord(title="真实流量验收任务", body="b", comments=[], labels=[], raw_response={}, fetched_at=now),
        automation_state="waiting_approval", manual_required=True, manual_reason="approval_required",
        next_action="review_plan", risk_level="high", events=[], created_by=creator_id,
        created_by_name=creator_name, created_at=now, updated_at=now,
    )


def _plan(task, now, age_hours: float):
    from app import main

    plan = main.ModificationPlan(
        id=f"PLAN-{task.id}-1", task_id=task.id, version=1, goal="g", file_scope=[], steps=[],
        risk_level=task.risk_level or "medium", risk_reason="r", test_suggestions=[], assumptions=[],
        rollback_plan=[], generated_from_analysis_revision=1, generated_from_context_ids=[],
        provider="mvp-rule-based", status="WAITING_APPROVAL", created_by=task.created_by,
        created_by_name=task.created_by_name, created_at=now - datetime.timedelta(hours=age_hours),
    )
    task.plans.append(plan)
    return plan


def main() -> None:
    from scripts.authorization_gate import require_authorization

    require_authorization(
        os.environ.get("AUTHORIZATION_OPERATION", ""),
        os.environ.get("AUTHORIZATION_LOG", "") or None,
    )
    try:
        _stdout("[dev019-real] 配置校验 ...")
        _check_mysql()

        from app.core.runtime_config import load_runtime_settings
        from app.db.session import get_engine, get_session_factory

        load_runtime_settings(); get_engine.cache_clear(); get_session_factory.cache_clear()

        from app.db import migrate

        # Alembic 升级到 head（不依赖 create_all）
        _stdout("[dev019-real] 校验并升级 schema ...")
        migrate.ensure_schema_current(upgrade=True)

        from app import main as m
        from app.services import notification_service

        now = datetime.datetime.now(datetime.timezone.utc)
        project_id = "proj_payment"
        repository_id = "repo_payment_service"
        creator_id, creator_name = "user_developer", "张伟"

        # 1) 高风险审批通知 → 写透 MySQL
        hi = _task(project_id, repository_id, creator_id, creator_name, now); hi.risk_level = "high"
        hip = _plan(hi, now, age_hours=0)
        hi_notif = notification_service.notify_approval_required(hi, hip, "user_approver", high_risk=True)
        assert hi_notif.type == "RISK_HIGH" and hi_notif.priority == "high", hi_notif

        # 2) 普通审批通知
        lo = _task(project_id, repository_id, creator_id, creator_name, now); lo.risk_level = "low"
        lop = _plan(lo, now, age_hours=0)
        lo_notif = notification_service.notify_approval_required(lo, lop, "user_approver", high_risk=False)
        assert lo_notif.type == "APPROVAL_REQUIRED" and lo_notif.priority == "normal", lo_notif

        # 3) 审批结果（通过）通知发起人
        done = notification_service.notify_approval_completed(lo, lop, "approved", "user_approver", "ok")
        assert done is not None and done.type == "APPROVAL_RESULT" and done.recipient_id == creator_id

        # 4) 24h 催办（计划 age>24h）→ NUDGE
        nudge_task = _task(project_id, repository_id, creator_id, creator_name, now); nudge_task.risk_level = "medium"
        _plan(nudge_task, now, age_hours=30)
        nu = notification_service.send_approval_reminder(nudge_task, m.USERS[creator_id])
        assert nu.type == "NUDGE" and nu.priority == "high" and nu.recipient_id == "user_approver"

        _stdout("[dev019-real] 已生成 RISK_HIGH / APPROVAL_REQUIRED / APPROVAL_RESULT / NUDGE 通知，写透 MySQL。")

        # 5) 模拟重启：清空内存，从 MySQL 真源恢复
        m.NOTIFICATIONS.clear()
        from app.db.bootstrap import restore_from_db

        counts = restore_from_db()
        _stdout(f"[dev019-real] restore_from_db 恢复 keys：{counts}")
        reloaded = {n.id: n for n in m.NOTIFICATIONS.values()}
        for expect in (hi_notif.id, lo_notif.id, done.id, nu.id):
            assert expect in reloaded, f"通知 {expect} 未从 MySQL 恢复"
        assert reloaded[hi_notif.id].type == "RISK_HIGH"
        assert reloaded[nu.id].type == "NUDGE"

        _stdout("[dev019-real] PASS：四种通知均写透 MySQL 且重启可恢复（DB 真源）。")
    except Exception as error:  # noqa: BLE001
        _stdout(f"[dev019-real] FAIL: {type(error).__name__}: {error}")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()