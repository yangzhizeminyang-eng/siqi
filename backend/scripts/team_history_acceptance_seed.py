"""Seed a completed task for team-mode history reuse acceptance.

Usage:
    APP_ENV=team USE_SQLALCHEMY=true DATABASE_URL=mysql+pymysql://... \
        python -m scripts.team_history_acceptance_seed

The script does not print credentials. It writes one deterministic completed
task with real-looking artifacts so the history-summary API can be verified
against the configured team database.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from app import main
from app.db import repositories
from app.db.bootstrap import restore_from_db


TASK_ID = "TASK-TEAM-HIST-0909"


def _repo_and_project():
    repo = (
        main.REPOSITORIES.get("repo_5f9ca2d1cb61")
        or main.REPOSITORIES.get("repo_payment_service")
        or next(iter(main.REPOSITORIES.values()))
    )
    project = main.PROJECTS.get(repo.project_id) or next(iter(main.PROJECTS.values()))
    return repo, project


def main_entry() -> None:
    from scripts.authorization_gate import require_authorization

    require_authorization(
        os.environ.get("AUTHORIZATION_OPERATION", ""),
        os.environ.get("AUTHORIZATION_LOG", "") or None,
    )
    restore_from_db()
    now = datetime.now(timezone.utc)
    repo, project = _repo_and_project()

    comment = main.IssueCommentSnapshot(
        id="c-team-hist-1",
        user_name="支付负责人",
        body="重复回调只能记账一次，需要覆盖回归测试。",
        created_at=(now - timedelta(minutes=20)).isoformat(),
        raw_response={"id": "c-team-hist-1"},
    )
    task = main.TaskRecord(
        id=TASK_ID,
        version=1,
        project_id=project.id,
        project_name=project.name,
        repository_id=repo.id,
        repository_name=repo.name,
        repository_url=repo.url,
        issue_id="IHIST0909",
        title="团队验收历史复用：支付回调重复入账修复",
        status="COMPLETED",
        risk_level="medium",
        risk_source="plan",
        base_branch=getattr(repo, "default_branch", None) or "master",
        test_command=getattr(repo, "test_command", None) or "pytest -q",
        approval_owner_id="user_approver",
        approval_owner_name="王琳",
        work_branch="agent/master/issue-ihist0909-v1",
        issue_snapshot=main.IssueSnapshotRecord(
            title="支付回调重复入账",
            body="渠道回调重试时同一事件被重复处理，需要通过幂等键去重并保留审计。",
            comments=[comment],
            labels=["bug", "payment"],
            raw_response={"source": "team-acceptance"},
            fetched_at=now - timedelta(minutes=30),
        ),
        analysis=main.AnalysisResult(
            task_id=TASK_ID,
            task_version=1,
            decision_summary=["入口增加幂等键校验", "重复事件直接返回成功但不重复入账"],
            overview="支付回调入口缺少稳定幂等保护，重试场景会导致重复入账。",
            findings=["回调事件缺少去重记录", "测试需要覆盖重复投递"],
            affected_areas=["payment callback", "order accounting"],
            suggested_risk_level="medium",
            risk_reason="涉及支付链路但改动范围集中。",
            uncertainties=[],
            questions=[],
            sources=[],
            prompt_injection_flags=[],
            generated_at=now - timedelta(minutes=25),
            provider="deepseek",
        ),
        automation_state="completed",
        manual_required=False,
        manual_reason="none",
        next_action="view_replay",
        base_commit_sha="d3177e3a11111111111111111111111111111111",
        code_contexts=[],
        similar_task_refs=[],
        plans=[],
        diffs=[],
        test_runs=[],
        pr_descriptions=[],
        events=[],
        execution_steps=[],
        created_by="user_developer",
        created_by_name="开发",
        created_at=now - timedelta(minutes=30),
        updated_at=now,
    )

    plan = main.ModificationPlan(
        id="PLAN-TEAM-HIST-0909-1",
        task_id=TASK_ID,
        version=1,
        goal="为支付回调增加幂等去重并补充回归测试",
        file_scope=[
            main.PlanFileScope(
                file_path="services/payment/callback.py",
                change_type="modify",
                reason="增加幂等键判断",
            )
        ],
        steps=[
            main.PlanStep(
                order=1,
                title="增加幂等保护",
                description="处理前检查幂等键，命中时直接 ACK。",
                target_files=["services/payment/callback.py"],
            )
        ],
        test_suggestions=["pytest tests/test_callback.py -q"],
        assumptions=[],
        rollback_plan=["回滚 callback.py 相关提交"],
        risk_level="medium",
        risk_reason="支付入口改动，需要重点回归重复投递。",
        generated_from_analysis_revision=1,
        generated_from_context_ids=[],
        provider="deepseek",
        status="APPROVED",
        approved_by_id="user_approver",
        approved_by_name="王琳",
        approved_at=now - timedelta(minutes=18),
        created_by="user_developer",
        created_by_name="开发",
        created_at=now - timedelta(minutes=20),
    )
    diff = main.DiffArtifact(
        id="DIFF-TEAM-HIST-0909-1",
        task_id=TASK_ID,
        plan_id=plan.id,
        plan_version=1,
        execution_step="步骤 1：增加幂等保护",
        generation_status="SUCCEEDED",
        execution_mode="REAL",
        is_demo_fallback=False,
        fallback_reason=None,
        real_failure_reason=None,
        generated_at=now - timedelta(minutes=12),
        files=[
            main.DiffFileRecord(
                id="DIFF-TEAM-HIST-0909-1-1",
                file_path="services/payment/callback.py",
                change_type="modify",
                patch="@@ add idempotency guard",
                added_lines=8,
                deleted_lines=1,
                before=["old"],
                after=["new"],
            )
        ],
        test_summary="真实回归测试通过",
        pr_description_draft=None,
    )
    test_run = main.TestRunRecord(
        id="TEST-TEAM-HIST-0909-1",
        task_id=TASK_ID,
        diff_artifact_id=diff.id,
        plan_id=plan.id,
        plan_version=1,
        command="pytest tests/test_callback.py -q",
        exit_code=0,
        stdout="2 passed",
        stderr="",
        status="passed",
        duration=1.24,
        attempt=1,
        started_at=now - timedelta(minutes=10),
        finished_at=now - timedelta(minutes=9),
        runner_mode="docker",
    )
    prd = main.PrDescriptionRecord(
        id="PRD-TEAM-HIST-0909-1",
        task_id=TASK_ID,
        plan_id=plan.id,
        plan_version=1,
        diff_artifact_id=diff.id,
        test_run_id=test_run.id,
        version=1,
        background="支付回调重试会造成重复入账。",
        change_summary="增加幂等键去重并补充重复投递测试。",
        impact_scope="支付回调入口与订单入账。",
        test_result="Docker Runner 执行 pytest tests/test_callback.py -q 通过。",
        risk_notes="需关注支付链路回归。",
        rollback_notes="回滚本次 callback.py 修改。",
        generation_status="GENERATED",
        generated_at=now - timedelta(minutes=8),
        generated_by="user_developer",
        generated_by_name="开发",
    )
    event = main.AuditEventRecord(
        id="task_evt_team_hist_0909",
        event_type="TASK_COMPLETED",
        resource_type="task",
        resource_id=TASK_ID,
        actor_id="user_developer",
        actor_name="开发",
        summary="团队验收历史复用任务已完成",
        diff_summary=["真实任务数据构造", "不自动套用旧 Diff"],
        created_at=now,
    )
    task.plans.append(plan)
    task.diffs.append(diff)
    task.test_runs.append(test_run)
    task.pr_descriptions.append(prd)
    task.events.append(event)
    task.execution_steps.append(
        main.ExecutionStepRecord(
            id="step_team_hist_0909",
            task_id=TASK_ID,
            event_id=event.id,
            node="complete",
            title="完成任务并沉淀复用摘要",
            status="completed",
            input="team acceptance",
            output="ready for history reuse",
            created_at=now,
        )
    )

    main.TASKS[task.id] = task
    repositories.upsert_task(task)
    print(
        {
            "seeded": task.id,
            "project_id": task.project_id,
            "repository_id": task.repository_id,
            "status": task.status,
        }
    )


if __name__ == "__main__":
    main_entry()
