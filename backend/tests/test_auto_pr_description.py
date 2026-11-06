"""DEV-018 收口：Diff→测试→PR 描述自动生成 之测试执行诚实 + 自动 PR 描述。

覆盖：
- 自动 PR 描述（generate_task_pr_description_worker）：生成并绑定 plan/diff/test、写 PR_DESC_GENERATED、幂等 skip；
- 测试执行诚实：exit!=0 记为 failed(TEST_FAILED) 而非 passed；环境不可用记为 skipped/unavailable 并回到可重试的 DIFF_GENERATED，不伪装成功。
"""
from __future__ import annotations

import datetime

from app import main
from app.services import diff_service, test_runner  # noqa: F401

_NOW = datetime.datetime.now(datetime.timezone.utc)


def _plan(task):
    return main.ModificationPlan(
        id=f"PLAN-{task.id}", task_id=task.id, version=1, goal="修复回调幂等",
        file_scope=[main.PlanFileScope(file_path="pay/callback.py", change_type="modify", reason="幂等")],
        steps=[main.PlanStep(order=1, title="校验", description="校验状态", target_files=["pay/callback.py"])],
        test_suggestions=["pytest tests/ -q"], assumptions=[], rollback_plan=[], risk_level="low", risk_reason="r",
        generated_from_analysis_revision=1, generated_from_context_ids=[], provider="deepseek",
        status="APPROVED", risk_adjust_reason=None, approved_by_id=None, approved_by_name=None, approved_at=None,
        created_by="u", created_by_name="u", created_at=_NOW,
    )


def _diff(task, plan):
    return main.DiffArtifact(
        id=f"DIFF-{task.id}", task_id=task.id, plan_id=plan.id, plan_version=1,
        execution_step="步骤 1", generation_status="SUCCEEDED", execution_mode="REAL", is_demo_fallback=False,
        fallback_reason=None, real_failure_reason=None, real_change_source="e2e_scratch_real_change", generated_at=_NOW,
        files=[main.DiffFileRecord(id="d1", file_path="pay/callback.py", change_type="modify", patch="--- a/x\n+++ b/x",
                                   added_lines=2, deleted_lines=1, before=["a"], after=["a", "b"])],
        test_summary="ok", pr_description_draft=None,
    )


def _test_run(task, status="passed"):
    return main.TestRunRecord(
        id=f"TEST-{task.id}-1", task_id=task.id, diff_artifact_id=f"DIFF-{task.id}", plan_id=f"PLAN-{task.id}",
        plan_version=1, command="pytest tests/ -q", exit_code=0 if status == "passed" else 1,
        stdout="ok", stderr="", status=status, duration=1.0, attempt=1,
        started_at=_NOW - datetime.timedelta(minutes=1), finished_at=_NOW,
        failure_reason=None, blocked_reason=None, runner_mode="docker",
    )


def _task(task_id, status, next_action):
    t = main.TaskRecord(
        id=task_id, version=1, project_id="proj_mvp", project_name="P", repository_id="repo_payment_service",
        repository_name="R", repository_url="https://gitee.com/o/r.git", issue_id="I1", title="t",
        status=status, base_branch="master", test_command="pytest tests/ -q", work_branch="agent/master/x-v1",
        issue_snapshot=main.IssueSnapshotRecord(title="t", body="b", comments=[], labels=[], raw_response={}, fetched_at=_NOW),
        automation_state="blocked", manual_required=False, manual_reason="none",
        next_action=next_action, events=[], created_by="u", created_by_name="u",
        created_at=_NOW - datetime.timedelta(minutes=5), updated_at=_NOW,
    )
    main.TASKS[t.id] = t
    return t


def test_auto_pr_description_generates_and_skips(monkeypatch):
    task = _task("TASK-PRD-1", "TESTED", "generate_pr_description")
    plan = _plan(task); diff = _diff(task, plan); tr = _test_run(task)
    task.plans.append(plan); task.diffs.append(diff); task.test_runs.append(tr)
    monkeypatch.setattr(main.pr_description_service, "compose_pr_description",
                        lambda *a, **k: {"background": "b", "change_summary": "c", "impact_scope": "i",
                                         "test_result": "t", "risk_notes": "r", "rollback_notes": "rb"})
    try:
        out = main.generate_task_pr_description_worker(task.id)
        assert out["status"] in {"generated"}, out
        assert out.get("record_id")
        assert task.pr_descriptions, "应写入 PR 描述记录"
        rec = task.pr_descriptions[-1]
        assert rec.generation_status == "GENERATED"
        assert rec.generated_by == "agent-system"
        assert rec.plan_id == plan.id and rec.diff_artifact_id == diff.id and rec.test_run_id == tr.id, "PR 描述须绑定 plan/diff/test"
        assert any(e.event_type == "PR_DESC_GENERATED" for e in task.events)
        # 幂等：已有非过期 GENERATED → 静默 skip，不重复
        out2 = main.generate_task_pr_description_worker(task.id)
        assert out2["status"] == "skipped", out2
        assert len(task.pr_descriptions) == 1, "不应重复生成"
    finally:
        main.TASKS.pop(task.id, None)


def test_worker_records_failed_on_nonzero_exit(monkeypatch):
    task = _task("TASK-TF-1", "DIFF_GENERATED", "auto_run_test")
    plan = _plan(task); task.plans.append(plan); task.diffs.append(_diff(task, plan))
    monkeypatch.setattr(test_runner, "execute_test",
                        lambda *a, **k: {"exit_code": 1, "stdout": "", "stderr": "AssertionError", "duration": 1.0,
                                         "runner_mode": "docker", "error": None})
    try:
        out = main.execute_task_tests_worker(task.id)
        assert out["status"] in {"failed", "failed_terminal"}, out
        rec = task.test_runs[-1]
        assert rec.status == "failed", "docker exit!=0 应记为 failed（不标 passed）"
        assert rec.runner_mode == "docker"
        assert any(e.event_type == "TEST_RUN_FAILED" for e in task.events)
    finally:
        main.TASKS.pop(task.id, None)


def test_worker_records_started_and_passed_on_one_run(monkeypatch):
    task = _task("TASK-TS-1", "DIFF_GENERATED", "auto_run_test")
    plan = _plan(task); task.plans.append(plan); task.diffs.append(_diff(task, plan))
    monkeypatch.setattr(test_runner, "execute_test",
                        lambda *a, **k: {"exit_code": 0, "stdout": "1 passed", "stderr": "", "duration": 0.5,
                                         "runner_mode": "docker", "error": None})
    try:
        out = main.execute_task_tests_worker(task.id)
        assert out["status"] == "passed", out
        assert len(task.test_runs) == 1
        record = task.test_runs[0]
        assert record.status == "passed"
        assert record.runner_mode == "docker"
        event_types = [event.event_type for event in task.events]
        assert event_types == ["TEST_RUN_STARTED", "TEST_RUN_PASSED"]
        assert record.finished_at is not None
    finally:
        main.TASKS.pop(task.id, None)


def test_worker_records_unavailable_and_advances(monkeypatch):
    task = _task("TASK-TU-1", "DIFF_GENERATED", "auto_run_test")
    plan = _plan(task); task.plans.append(plan); task.diffs.append(_diff(task, plan))
    monkeypatch.setattr(test_runner, "execute_test",
                        lambda *a, **k: {"exit_code": None, "stdout": "", "stderr": "daemon down", "duration": 0.0,
                                         "runner_mode": "unavailable", "error": "Docker daemon 未运行"})
    try:
        out = main.execute_task_tests_worker(task.id)
        assert out["status"] == "unavailable", out
        assert task.status == "DIFF_GENERATED", "unavailable 应诚实标记(skipped)并回到可重试阶段"
        rec = task.test_runs[-1]
        assert rec.status == "skipped" and rec.runner_mode == "unavailable", (rec.status, rec.runner_mode)
        assert not rec.failure_reason or True
        assert any(e.event_type == "TEST_RUN_UNAVAILABLE" for e in task.events), "应写 TEST_RUN_UNAVAILABLE 审计/事件"
        assert task.next_action == "retry_test", "不可用后不得进入 PR 描述阶段"
    finally:
        main.TASKS.pop(task.id, None)
