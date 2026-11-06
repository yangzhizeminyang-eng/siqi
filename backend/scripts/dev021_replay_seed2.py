from __future__ import annotations
import datetime as _dt, sys
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

def _mk(id_, title, body):
    from app import main
    now = _dt.datetime.now(_dt.timezone.utc)
    return main.TaskRecord(
        id=id_, version=1, project_id="proj_payment", project_name="支付中台",
        repository_id="repo_payment_service", repository_name="payment-service",
        repository_url="https://gitee.com/o/r.git", issue_id=f"I-{id_}", title=title,
        status="COMPLETED", base_branch="main", test_command="pytest tests/ -q",
        approval_owner_id="user_approver", approval_owner_name="王琳", work_branch="agent/main/v1",
        issue_snapshot=main.IssueSnapshotRecord(title=title, body=body, comments=[], labels=["支付"], raw_response={}, fetched_at=now),
        automation_state="completed", manual_required=False, manual_reason="none", next_action="view_replay",
        analysis=main.AnalysisResult(task_id=id_, task_version=1, decision_summary=["修复"], overview="支付回调处理",
            findings=["重复回调"], affected_areas=["pay"], suggested_risk_level="medium", risk_reason="r",
            uncertainties=[], questions=[], sources=[], prompt_injection_flags=[], generated_at=now, provider="mvp-rule-based"),
        plans=[main.ModificationPlan(id=f"PLAN-{id_}-1", task_id=id_, version=1, goal="支付幂等", file_scope=[], steps=[],
            risk_level="medium", risk_reason="", test_suggestions=["pytest tests/pay/"], assumptions=[], rollback_plan=[],
            generated_from_analysis_revision=1, generated_from_context_ids=[], provider="mvp-rule-based",
            status="APPROVED", created_by="user_developer", created_by_name="张伟", created_at=now)],
        diffs=[], test_runs=[], pr_descriptions=[], code_contexts=[], similar_task_refs=[],
        events=[], execution_steps=[], created_by="user_developer", created_by_name="张伟",
        created_at=now, updated_at=now,
    )

def main():
    from app import main
    from app.services import history_service
    for id_, title, body in [
        ("TASK-HIST2", "支付回调对账幂等增强", "对账任务需兼容重复支付回调，确保对账幂等。"),
        ("TASK-HIST3", "退款单复核定时任务", "退款单每日复核，超时自动告警。"),
    ]:
        task = _mk(id_, title, body)
        main.TASKS[task.id] = task
        main._persist_task(task)
        s = history_service.generate_for_completed_task(task.id)
        history_service.index(s.id)
        print(f"[seed2] {task.id} summary={s.id} index={history_service.get_status(s.id).index_status}")

if __name__ == "__main__":
    main()
