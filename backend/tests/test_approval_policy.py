"""DEV-018 审批分级与自动推进测试：低风险安全自动审批、高危/阻塞/结构不全留人工、幂等、事件/步骤/审计。"""
from __future__ import annotations

import datetime

from app import main
from app.services import approval_policy

_NOW = datetime.datetime.now(datetime.timezone.utc)


def _repo():
    return main.REPOSITORIES["repo_payment_service"]  # test_command="pytest tests/ -q", risk_threshold="medium"


def _fresh_context(path="pay/callback.py"):
    return main.RetrievalContext(
        chunk_id="c1", file_path=path, start_line=1, end_line=5, language="python",
        symbol_name="handle", content="def handle(): return", score=0.9, commit_sha="abc",
        index_id="idx1", fresh=True, source="milvus",
    )


def _analysis(task_id, questions=None):
    return main.AnalysisResult(
        task_id=task_id, task_version=1, decision_summary=["ok"], overview="o", findings=["f"],
        affected_areas=["pay"], suggested_risk_level="low", risk_reason="low", uncertainties=[],
        questions=questions or [], sources=[], prompt_injection_flags=[], generated_at=_NOW, provider="deepseek",
    )


def _plan(task, risk="low"):
    return main.ModificationPlan(
        id=f"PLAN-{task.id}" if not task.id.startswith("PLAN") else task.id,
        task_id=task.id, version=1, goal="修复回调幂等（低风险安全）",
        file_scope=[main.PlanFileScope(file_path="pay/callback.py", change_type="modify", reason="幂等修复")],
        steps=[main.PlanStep(order=1, title="校验订单状态", description="重复入账前先校验", target_files=["pay/callback.py"])],
        test_suggestions=["pytest tests/ -q"], assumptions=[], rollback_plan=["回滚 pay/callback.py"],
        risk_level=risk, risk_reason="低风险" if risk == "low" else "高风险关键字",
        generated_from_analysis_revision=1, generated_from_context_ids=["c1"], provider="deepseek",
        status="WAITING_APPROVAL", risk_adjust_reason=None, approved_by_id=None, approved_by_name=None,
        approved_at=None, created_by="u", created_by_name="u", created_at=_NOW,
    )


def _task(repo, risk="low", questions=None, structure_complete=True):
    t = main.TaskRecord(
        id="TASK-DEV018-1", version=1, project_id=repo.project_id, project_name="P", repository_id=repo.id,
        repository_name=repo.name, repository_url=repo.url, issue_id="I1", title="回调幂等修复",
        status="WAITING_APPROVAL", base_branch="master", test_command=repo.test_command,
        work_branch="agent/master/issue-i1-v1",
        issue_snapshot=main.IssueSnapshotRecord(title="t", body="b", comments=[], labels=[], raw_response={}, fetched_at=_NOW),
        automation_state="waiting_approval", manual_required=True, manual_reason="approval_required",
        next_action="review_plan", events=[], code_contexts=[_fresh_context()],
        analysis=_analysis("TASK-DEV018-1", questions or []),
        created_by="u", created_by_name="u", created_at=_NOW - datetime.timedelta(minutes=5), updated_at=_NOW,
    )
    main.TASKS[t.id] = t
    return t


def _lazy_incomplete(plan):
    plan.goal = ""
    return plan


def test_low_risk_auto_approves(monkeypatch):
    repo = _repo()
    task = _task(repo)
    plan = _plan(task)
    task.plans.append(plan)
    persisted = {"n": 0}
    monkeypatch.setattr(main, "_persist_task", lambda t: persisted.update(n=persisted["n"] + 1))
    try:
        decision = approval_policy.evaluate_and_apply(task, plan)
        assert decision == "auto_approved", decision
        assert plan.status == "APPROVED"
        assert task.status == "APPROVED"
        assert task.next_action == "auto_generate_diff"
        assert task.manual_required is False and task.manual_reason == "none"
        assert persisted["n"] >= 1
        events = [e.event_type for e in task.events]
        assert "PLAN_AUTO_APPROVED" in events, events
        assert any(s.node == "plan_approval" for s in task.execution_steps)
        # 全局审计：actor=system:auto_approval + metadata
        auto_events = [a for a in main.AUDIT_EVENTS.values() if a.resource_id == task.id and a.event_type == "PLAN_AUTO_APPROVED"]
        assert auto_events, "应有全局 audit_event（PLAN_AUTO_APPROVED）"
        assert all(a.actor_id == "system:auto_approval" for a in auto_events)
        meta = "\n".join(auto_events[0].diff_summary)
        assert f"task_id={task.id}" in meta and "plan_version=1" in meta
        assert "risk_level=low" in meta and "next_action=" in meta and "reasons=" in meta
    finally:
        main.TASKS.pop(task.id, None)


def test_high_risk_requires_manual(monkeypatch):
    repo = _repo()
    task = _task(repo, risk="high")
    plan = _plan(task, risk="high")
    task.plans.append(plan)
    persisted = {"n": 0}
    monkeypatch.setattr(main, "_persist_task", lambda t: persisted.update(n=persisted["n"] + 1))
    try:
        decision = approval_policy.evaluate_and_apply(task, plan)
        assert decision == "require_manual"
        assert task.status == "WAITING_APPROVAL"
        assert not any(e.event_type == "PLAN_AUTO_APPROVED" for e in task.events)
        assert persisted["n"] == 0, "不自动推进不应写库"
    finally:
        main.TASKS.pop(task.id, None)


def test_blocking_question_blocks_auto():
    repo = _repo()
    q = main.AnalysisQuestion(id="q1", question="确认幂等范围", reason="r", blocking=True, answered=False)
    task = _task(repo, questions=[q])
    plan = _plan(task)
    task.plans.append(plan)
    try:
        decision = approval_policy.evaluate_and_apply(task, plan)
        assert decision == "require_manual"
        assert task.status == "WAITING_APPROVAL"
    finally:
        main.TASKS.pop(task.id, None)


def test_incomplete_structure_blocks_auto():
    repo = _repo()
    task = _task(repo)
    plan = _lazy_incomplete(_plan(task))  # goal="" → structure 不完整
    task.plans.append(plan)
    try:
        decision = approval_policy.evaluate_and_apply(task, plan)
        assert decision == "require_manual"
        assert task.status == "WAITING_APPROVAL"
    finally:
        main.TASKS.pop(task.id, None)


def test_idempotent_no_duplicate(monkeypatch):
    repo = _repo()
    task = _task(repo)
    plan = _plan(task)
    task.plans.append(plan)
    monkeypatch.setattr(main, "_persist_task", lambda t: None)
    try:
        assert approval_policy.evaluate_and_apply(task, plan) == "auto_approved"
        second = approval_policy.evaluate_and_apply(task, plan)
        assert second == "already_approved", second
        assert [e.event_type for e in task.events].count("PLAN_AUTO_APPROVED") == 1
    finally:
        main.TASKS.pop(task.id, None)


def test_sensitive_file_blocks_auto():
    repo = _repo()
    task = _task(repo)
    plan = _plan(task)  # low risk、结构完整
    plan.file_scope[0].file_path = ".env"  # 命中敏感路径
    task.plans.append(plan)
    try:
        decision, reason = approval_policy.evaluate_plan_gate(task, plan)
        assert decision == "require_manual"
        assert "sensitive_file_matched:.env" in reason, reason
        assert approval_policy.evaluate_and_apply(task, plan) == "require_manual"
    finally:
        main.TASKS.pop(task.id, None)


def test_dangerous_command_blocks_auto():
    repo = _repo()
    task = _task(repo)
    plan = _plan(task)
    plan.test_suggestions = ["rm -rf /"]  # 危险命令
    task.plans.append(plan)
    try:
        decision, reason = approval_policy.evaluate_plan_gate(task, plan)
        assert decision == "require_manual"
        assert "dangerous_command_matched:" in reason, reason
    finally:
        main.TASKS.pop(task.id, None)


def _make_waiting(task_id, project_id, owner):
    now = _NOW
    t = main.TaskRecord(
        id=task_id, version=1, project_id=project_id, project_name="P", repository_id="repo_payment_service",
        repository_name="R", repository_url="https://gitee.com/o/r.git", issue_id=f"I-{task_id}", title="t",
        status="WAITING_APPROVAL", base_branch="master", test_command="pytest tests/ -q",
        work_branch="agent/master/x-v1",
        issue_snapshot=main.IssueSnapshotRecord(title="t", body="b", comments=[], labels=[], raw_response={}, fetched_at=now),
        automation_state="waiting_approval", manual_required=True, manual_reason="approval_required",
        next_action="review_plan", approval_owner_id=owner, approval_owner_name=owner,
        events=[], created_by="u", created_by_name="u", created_at=now - datetime.timedelta(minutes=1), updated_at=now,
    )
    main.TASKS[task_id] = t
    return t


def test_pending_approvals_project_lead_scope():
    """DEV-018：lead 项目级可见（即使未指派给自己）；普通用户仅供指派；admin 全量。"""
    from app.db import reads

    now = _NOW
    repo = main.RepositoryConfigRecord(
        id="repo_lead_x", project_id="proj_lead_x", name="rl", url="https://gitee.com/o/rl.git",
        default_branch="master", test_command="pytest tests/ -q", command_whitelist=["pytest"],
        approval_mode="single", risk_threshold="medium", approval_owner_id="user_lead", approval_owner_name="王琳",
        max_files=5, max_retries=1, blocked_paths=[], created_at=now, updated_at=now,
    )
    main.REPOSITORIES[repo.id] = repo
    created = []
    try:
        created.append(_make_waiting("TASK-LEAD-1", "proj_lead_x", "user_approver"))  # lead 负责项目，但指派他人
        created.append(_make_waiting("TASK-LEAD-2", "proj_other", "user_lead"))       # 指派给 lead
        created.append(_make_waiting("TASK-LEAD-3", "proj_other", "user_approver"))    # 指派给 approver
        lead = main.USERS["user_lead"]; ap = main.USERS["user_approver"]; adm = main.USERS["user_admin"]
        lead_ids = {t.id for t in reads.list_pending_approvals(lead)}
        ap_ids = {t.id for t in reads.list_pending_approvals(ap)}
        adm_ids = {t.id for t in reads.list_pending_approvals(adm)}
        assert "TASK-LEAD-1" in lead_ids and "TASK-LEAD-2" in lead_ids, "lead 应看到自己项目 + 指派给自己的"
        assert "TASK-LEAD-3" not in lead_ids, "lead 不应看到他人项目下指派给他人的任务"
        assert "TASK-LEAD-3" in ap_ids and "TASK-LEAD-2" not in ap_ids, "普通审批人仅供指派"
        assert {"TASK-LEAD-1", "TASK-LEAD-2", "TASK-LEAD-3"} <= adm_ids, "admin 全量可见"
    finally:
        for t in created:
            main.TASKS.pop(t.id, None)
        main.REPOSITORIES.pop(repo.id, None)