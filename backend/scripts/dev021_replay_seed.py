"""DEV-021 现场截图种子：在 sqlite 真库造一条完成任务 + 历史摘要 + 向量索引（供浏览器面板展示）。"""
from __future__ import annotations

import datetime as _dt
import os
import sys

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass


def main() -> None:
    from app import main
    from app.db import migrate
    from app.services import history_service
    from app.db.session import get_engine, get_session_factory

    get_engine.cache_clear(); get_session_factory.cache_clear()
    migrate.ensure_schema_current(upgrade=True)

    now = _dt.datetime.now(_dt.timezone.utc)
    task = main.TaskRecord(
        id="TASK-HIST-SEED", version=1, project_id="proj_payment", project_name="支付中台",
        repository_id="repo_payment_service", repository_name="payment-service",
        repository_url="https://gitee.com/o/r.git", issue_id="HIST-SEED-1",
        title="支付回调重复入账幂等修复",
        status="COMPLETED", base_branch="main", test_command="pytest tests/ -q",
        approval_owner_id="user_approver", approval_owner_name="王琳", work_branch="agent/main/v1",
        issue_snapshot=main.IssueSnapshotRecord(
            title="支付回调重复入账幂等修复", body="支付回调重复通知导致重复入账，需在队列消费侧加幂等键。",
            comments=[], labels=["支付", "幂等"], raw_response={}, fetched_at=now),
        automation_state="completed", manual_required=False, manual_reason="none",
        next_action="view_replay",
        analysis=main.AnalysisResult(
            task_id="TASK-HIST-SEED", task_version=1,
            decision_summary=["在回调消费入口加幂等表（orderno+eventid 唯一键）"],
            overview="支付回调需幂等处理，防止重复入账", findings=["重复回调时 UPDATE 无行则跳过"],
            affected_areas=["pay-callback"], suggested_risk_level="medium", risk_reason="中等",
            uncertainties=[], questions=[], sources=[], prompt_injection_flags=[],
            generated_at=now, provider="mvp-rule-based",
        ),
        plans=[main.ModificationPlan(
            id="PLAN-HIST-SEED-1", task_id="TASK-HIST-SEED", version=1,
            goal="支付回调幂等", file_scope=[], steps=[], risk_level="medium",
            risk_reason="", test_suggestions=["pytest tests/pay/"], assumptions=[], rollback_plan=[],
            generated_from_analysis_revision=1, generated_from_context_ids=[], provider="mvp-rule-based",
            status="APPROVED", created_by="user_developer", created_by_name="张伟", created_at=now,
        )],
        diffs=[], test_runs=[], pr_descriptions=[],
        code_contexts=[], similar_task_refs=[], events=[], execution_steps=[],
        created_by="user_developer", created_by_name="张伟", created_at=now, updated_at=now,
    )
    main.TASKS[task.id] = task
    main._persist_task(task)

    summary = history_service.generate_for_completed_task(task.id)
    assert summary is not None
    history_service.index(summary.id)
    print(f"[seed] summary={summary.id} version={summary.summary_version} ; index={history_service.get_status(summary.id).index_status}")
    print(f"[seed] index_status={history_service.get_status(summary.id).index_status}")


if __name__ == "__main__":
    main()