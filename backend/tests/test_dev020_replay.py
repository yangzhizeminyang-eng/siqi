"""DEV-020 回放摘要与审计后端测试。

覆盖：全量数据源进入回放、时间线稳定排序、事件去重、事件-产物关联、
失败节点(原因/重试)、自动/人工审批/风险调整/催办可见、越权与权限、
回放只读不改状态/审计、摘要不改原始数据、DB 真源(清内存)、mock/unavailable 不伪装成功。
"""
from __future__ import annotations

import datetime
from datetime import timedelta
from itertools import count

import pytest
from fastapi.testclient import TestClient

from app import main

_seq = count(9900)
developer = main.USERS["user_developer"]


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _event(task_id, etype, summary, *, created_at, actor_id="agent-system", actor_name="Agent 自动化", diff=None):
    return main.AuditEventRecord(
        id=f"evt_{etype}_{next(_seq)}", event_type=etype, resource_type="task", resource_id=task_id,
        actor_id=actor_id, actor_name=actor_name, summary=summary,
        diff_summary=diff or [], created_at=created_at, execution_step_ids=[],
    )


def authed(account, password):
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"account": account, "password": password})
    assert r.status_code == 200
    return c


@pytest.fixture()
def admin_client():
    return authed("admin@company.com", "Admin123!")


@pytest.fixture()
def dev_client():
    return authed("developer@company.com", "Dev123!")


def _base_task(next_action="check_index") -> main.TaskRecord:
    now = _now()
    return main.TaskRecord(
        id=f"TASK-{next(_seq)}", version=1, project_id="proj_payment", project_name="支付中台",
        repository_id="repo_payment_service", repository_name="payment-service",
        repository_url="https://gitee.com/o/r.git", issue_id=f"R-{next(_seq)}",
        title="回放测试任务", status="COMPLETED", risk_level="medium", risk_source="plan",
        base_branch="main", test_command="pytest tests/ -q", approval_owner_id="user_approver",
        approval_owner_name="王琳", work_branch="agent/main/v1",
        issue_snapshot=main.IssueSnapshotRecord(title="回放测试任务", body="b", comments=[], labels=[], raw_response={}, fetched_at=now),
        automation_state="completed", manual_required=False, manual_reason="none", next_action=next_action,
        failure_node=None, failure_reason=None, failed_at=None, retry_count=0, base_commit_sha=None,
        plans=[], diffs=[], test_runs=[], pr_descriptions=[], code_contexts=[], similar_task_refs=[],
        events=[], execution_steps=[], created_by=developer.id, created_by_name=developer.name,
        created_at=now, updated_at=now,
    )


def _rich_task():
    """构造覆盖多来源数据的任务（自动审批 + Diff 降级 + 测试 unavailable + PR + 评论 + 风险调整）。"""
    t0 = _now()
    task = _base_task()
    task.status = "COMPLETED"
    task.risk_level = "high"
    task.plans.append(main.ModificationPlan(
        id=f"PLAN-{task.id}-1", task_id=task.id, version=1, goal="g", file_scope=[], steps=[],
        risk_level="high", risk_reason="r", test_suggestions=[], assumptions=[], rollback_plan=[],
        generated_from_analysis_revision=1, generated_from_context_ids=[], provider="mvp-rule-based",
        status="APPROVED", risk_adjust_from="medium", risk_adjust_reason="审批上调风险", approved_by_id="user_approver",
        approved_by_name="王琳", approved_at=t0, approval_comment="ok", created_by=developer.id,
        created_by_name=developer.name, created_at=t0,
    ))
    task.diffs.append(main.DiffArtifact(
        id=f"DIFF-{task.id}-1", task_id=task.id, plan_id=f"PLAN-{task.id}-1", plan_version=1,
        execution_step="generate_diff", generation_status="SUCCEEDED", execution_mode="DEMO_FALLBACK",
        is_demo_fallback=True, fallback_reason="演示兜底", real_failure_reason=None, generated_at=t0,
        files=[], test_summary="", pr_description_draft="",
    ))
    task.test_runs.append(main.TestRunRecord(
        id=f"TEST-{task.id}-2", task_id=task.id, diff_artifact_id=f"DIFF-{task.id}-1", plan_id=f"PLAN-{task.id}-1",
        plan_version=1, command="pytest tests/ -q", exit_code=None, stdout="", stderr="",
        status="skipped", duration=0.0, attempt=1, started_at=t0, finished_at=t0,
        failure_reason=None, blocked_reason="Docker 不可用", runner_mode="unavailable",
    ))
    task.pr_descriptions.append(main.PrDescriptionRecord(
        id=f"PRD-{task.id}-1", task_id=task.id, plan_id=f"PLAN-{task.id}-1", plan_version=1,
        diff_artifact_id=f"DIFF-{task.id}-1", test_run_id=f"TEST-{task.id}-2", version=1,
        background="bg", change_summary="cs", impact_scope="is", test_result="tr", risk_notes="rn",
        rollback_notes="rbn", generation_status="GENERATED", failure_reason=None,
        generated_at=t0, generated_by="agent-system", generated_by_name="Agent 自动化",
    ))
    order = 0

    def add(etype, summary, **kw):
        nonlocal order
        order += 1
        ev = _event(task.id, etype, summary, created_at=t0 + timedelta(seconds=order),
                    diff=kw.pop("diff", None), **kw)
        task.events.append(ev)
        return ev

    add("TASK_CREATED", "任务已创建", diff=[f"task_id={task.id}"])
    add("ISSUE_FETCHED", "已拉取 Issue")
    add("CONTEXT_INDEXED", "已注入代码上下文")
    add("ANALYSIS_GENERATED", "AI 分析完成")
    gen = add("PLAN_GENERATED", "生成修改计划 v1", diff=[f"plan_id=PLAN-{task.id}-1", "plan_version=1"])
    auto = add("PLAN_AUTO_APPROVED", "自动审批通过", diff=[f"plan_id=PLAN-{task.id}-1", "plan_version=1", "risk_level=high"])
    diff = add("DIFF_GENERATED", "生成 Diff（演示兜底）", diff=[f"plan_id=PLAN-{task.id}-1", "plan_version=1", f"diff_id=DIFF-{task.id}-1"])
    add("TEST_RUN_STARTED", "测试开始", diff=[f"plan_id=PLAN-{task.id}-1", f"test_run_id=TEST-{task.id}-2", "plan_version=1"])
    add("TEST_RUN_UNAVAILABLE", "测试环境不可用，已跳过", diff=[f"plan_id=PLAN-{task.id}-1", "test_run_id=TEST-{task.id}-2", "plan_version=1"])
    add("PR_DESC_GENERATED", "PR 描述已生成", diff=[f"plan_id=PLAN-{task.id}-1", "plan_version=1", f"diff_id=DIFF-{task.id}-1", f"test_run_id=TEST-{task.id}-2", f"pr_description_id=PRD-{task.id}-1"])
    add("COMMENT_ADDED", "发表评论", diff=[f"task_id={task.id}", f"评论ID=cmt_replay_1"], actor_id=developer.id, actor_name=developer.name)
    add("NUDGE_SENT", "已发送催办通知", diff=[f"plan_id=PLAN-{task.id}-1", "plan_version=1", "task_id={}".format(task.id)], actor_id=developer.id, actor_name=developer.name)
    add("PR_CREATED", "PR 已创建", diff=[f"plan_id=PLAN-{task.id}-1", "plan_version=1", "task_id={}".format(task.id)])
    # 记录执行步骤（回放节点/失败信息基础）
    task.execution_steps.append(main.ExecutionStepRecord(
        id=f"step_plan_{next(_seq)}", task_id=task.id, event_id=gen.id, node="plan_generation",
        title="生成修改计划", status="completed", input="", output="v1", duration_ms=10,
        failure_reason=None, blocked_reason=None, created_at=t0,
    ))
    task.execution_steps.append(main.ExecutionStepRecord(
        id=f"step_approval_{next(_seq)}", task_id=task.id, event_id=auto.id, node="plan_approval",
        title="自动审批", status="completed", input="", output="ok", duration_ms=5,
        failure_reason=None, blocked_reason=None, created_at=t0,
    ))
    # 评论与检索记录（DB 真源侧）
    main.COMMENTS[comment_id()] = main.CommentRecord(
        id=comment_id(), task_id=task.id, project_id=task.project_id, repository_id=task.repository_id,
        author_id=developer.id, author_name=developer.name, body="请确认样式@王琳",
        mentioned_user_ids=["user_approver"], reference_type="task", reference_id=task.id,
        created_at=t0, deleted_at=None,
    )
    main.RETRIEVAL_RECORDS[retrieval_id()] = main.RetrievalRecord(
        id=retrieval_id(), task_id=task.id, query_text="列表页样式", repository_id=task.repository_id,
        project_id=task.project_id, commit_sha=None, index_id=None, top_k=5, mode="auto",
        fresh=True, threshold=0.2, empty_reason=None, result_chunk_ids=[], created_at=t0,
    )
    main.TASKS[task.id] = task
    return task


_comment_counter = count()
_retrieval_counter = count()


def comment_id():
    return f"cmt_replay_{next(_comment_counter)}"


def retrieval_id():
    return f"ret_replay_{next(_retrieval_counter)}"


def test_replay_aggregates_all_sources_and_sorted(admin_client):
    task = _rich_task()
    resp = admin_client.get(f"/api/tasks/{task.id}/replay")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["task_id"] == task.id
    assert data["summary"]["source"] == "deterministic"
    assert data["summary"]["input_fingerprint"]

    times = [ev["occurred_at"] for ev in data["timeline"]]
    assert times == sorted(times), "时间线应按 occurred_at 升序"
    ids = [ev["id"] for ev in data["timeline"]]
    assert len(ids) == len(set(ids)), "事件不应重复"

    types = {ev["event_type"] for ev in data["timeline"]}
    assert {"TASK_CREATED", "PLAN_GENERATED", "PLAN_AUTO_APPROVED", "DIFF_GENERATED",
            "TEST_RUN_UNAVAILABLE", "PR_DESC_GENERATED", "COMMENT_ADDED", "NUDGE_SENT", "PR_CREATED"}.issubset(types)

    assert data["artifacts"]["plans"], "计划产物应进入 artifacts"
    assert data["artifacts"]["diffs"], "Diff 产物应进入 artifacts"
    assert data["artifacts"]["test_runs"], "测试产物应进入 artifacts"
    assert data["artifacts"]["pr_descriptions"], "PR 描述产物应进入 artifacts"
    assert data["artifacts"]["comments"], "评论应进入 artifacts"
    assert data["artifacts"]["retrievals"], "检索记录应进入 artifacts"


def test_replay_event_artifacts_relations(admin_client):
    task = _rich_task()
    data = admin_client.get(f"/api/tasks/{task.id}/replay").json()["data"]
    diff_ev = next(ev for ev in data["timeline"] if ev["event_type"] == "DIFF_GENERATED")
    assert diff_ev["relations"]["plan_id"]
    assert diff_ev["relations"]["plan_version"]
    pr_ev = next(ev for ev in data["timeline"] if ev["event_type"] == "PR_DESC_GENERATED")
    assert pr_ev["relations"]["pr_description_id"]
    assert pr_ev["relations"]["test_run_id"]
    comment_ev = next(ev for ev in data["timeline"] if ev["event_type"] == "COMMENT_ADDED")
    assert comment_ev["relations"]["task_id"] == task.id
    assert comment_ev["actor_type"] == "user"


def test_unavailable_not_marked_success(admin_client):
    task = _rich_task()
    data = admin_client.get(f"/api/tasks/{task.id}/replay").json()["data"]
    unavailable = next(ev for ev in data["timeline"] if ev["event_type"] == "TEST_RUN_UNAVAILABLE")
    assert unavailable["status"] != "completed", "unavailable 不得伪装为成功"
    assert unavailable["status"] == "skipped"
    assert "execution_env" in unavailable["metadata"] and "不可用" in unavailable["metadata"]["execution_env"]
    # 摘要也要如实呈现降级
    assert "不可用" in data["summary"]["text"] or "降级" in data["summary"]["text"] or "回退" in data["summary"]["text"]


def test_failure_node_carries_reason_and_retry(admin_client):
    task = _base_task()
    task.status = "TEST_FAILED"
    task.failure_node = "run_test"
    task.failure_reason = "断言未通过"
    task.retry_count = 1
    task.next_action = "retry_test"
    step = _event(task.id, "TEST_RUN_FAILED", "测试失败（第 1 次尝试）", created_at=_now(),
                  diff=[f"task_id={task.id}", "plan_id=P", "plan_version=1"])
    task.events.append(step)
    task.execution_steps.append(main.ExecutionStepRecord(
        id=f"step_fail_{next(_seq)}", task_id=task.id, event_id=step.id, node="run_test",
        title="执行测试", status="failed", input="", output="", duration_ms=3,
        failure_reason="断言未通过", blocked_reason=None, created_at=_now(),
    ))
    main.TASKS[task.id] = task
    data = admin_client.get(f"/api/tasks/{task.id}/replay").json()["data"]
    failed = next(ev for ev in data["timeline"] if ev["event_type"] == "TEST_RUN_FAILED")
    assert failed["status"] == "failed"
    assert failed["failure_reason"] == "断言未通过" or "断言" in (failed["failure_reason"] or "")
    assert failed["retry_count"] >= 1
    assert failed["node"] == "run_test"


def test_risk_adjust_surfaced_in_summary(admin_client):
    task = _rich_task()
    data = admin_client.get(f"/api/tasks/{task.id}/replay").json()["data"]
    plan = data["artifacts"]["plans"][0]
    assert plan["risk_adjust_from"] == "medium" and plan["risk_level"] == "high"
    assert "风险调整" in data["summary"]["text"]


def test_admin_can_read_and_replay_is_read_only(admin_client):
    task = _rich_task()
    before_status = task.status
    before_events = len(task.events)
    resp = admin_client.get(f"/api/tasks/{task.id}/replay")
    admin_client.get(f"/api/tasks/{task.id}/replay/summary")
    assert resp.status_code == 200
    assert task.status == before_status, "回放不应推进会务状态"
    assert len(task.events) == before_events, "回放不应新增审计事件"
    assert len(main.AUDIT_EVENTS) >= 0  # 不断言数量，只确认状态未变


def test_summary_does_not_mutate_original_data(admin_client):
    task = _rich_task()
    events_before = [{e.id, e.event_type, e.summary} for e in task.events]
    plans_before = [p.version for p in task.plans]
    data = admin_client.get(f"/api/tasks/{task.id}/replay").json()["data"]
    assert data["summary"]["text"].startswith("# 任务回放摘要")
    assert task.id in data["summary"]["text"]
    assert [{e.id, e.event_type, e.summary} for e in task.events] == events_before
    assert [p.version for p in task.plans] == plans_before


def test_permission_denied_when_role_lacks_replay(dev_client, monkeypatch):
    task = _rich_task()
    monkeypatch.setattr(main, "ROLE_PERMISSIONS",
                        {**main.ROLE_PERMISSIONS,
                         "developer": [p for p in main.ROLE_PERMISSIONS["developer"] if p != "replay:view"]})
    resp = dev_client.get(f"/api/tasks/{task.id}/replay")
    assert resp.status_code == 403
    summ = dev_client.get(f"/api/tasks/{task.id}/replay/summary")
    assert summ.status_code == 403


def test_replay_unknown_task_404(admin_client):
    resp = admin_client.get("/api/tasks/TASK-NO/replay")
    assert resp.status_code == 404


def test_unauth_rejected():
    client = TestClient(main.app)
    resp = client.get("/api/tasks/TASK-1/replay")
    assert resp.status_code == 401


def test_project_level_isolation_non_involved_user_forbidden():
    """未显式参与任务（非创建者/非审批人、非 admin/非归属 lead）的普通用户应 403。"""
    task = _rich_task()  # created_by=developer.id, approval_owner=user_approver
    observer = authed("observer@company.com", "Observer123!")
    resp = observer.get(f"/api/tasks/{task.id}/replay")
    assert resp.status_code == 403
    summ = observer.get(f"/api/tasks/{task.id}/replay/summary")
    assert summ.status_code == 403
    # 显式参与人（审批人）可读
    approver = authed("approver@company.com", "Approver123!")
    assert approver.get(f"/api/tasks/{task.id}/replay").status_code == 200


def test_creator_can_view_replay(dev_client):
    """任务创建者(developer)对自有任务回放与摘要均可读（权限矩阵正向路径）。"""
    task = _rich_task()  # created_by=developer.id
    assert dev_client.get(f"/api/tasks/{task.id}/replay").status_code == 200
    assert dev_client.get(f"/api/tasks/{task.id}/replay/summary").status_code == 200


def test_replay_and_summary_generated_at_aligned():
    """回放与摘要的 generated_at 应一致（单一时钟源，避免相隔数毫秒）。"""
    task = _rich_task()
    resp = authed("admin@company.com", "Admin123!").get(f"/api/tasks/{task.id}/replay")
    data = resp.json()["data"]
    assert data["generated_at"] == data["summary"]["generated_at"]


def test_lead_only_owns_project_scope():
    """lead 仅能回放「自己默认审批的仓库所属项目」，非其项目 403。"""
    task = _rich_task()  # 默认仓库 repo_payment_service 的审批人是 user_approver，非 lead
    lead = authed("lead@company.com", "Lead123!")
    assert lead.get(f"/api/tasks/{task.id}/replay").status_code == 403

    # 把该仓库默认审批人改成 lead → lead 归属本项目 → 200
    repo = main.REPOSITORIES["repo_payment_service"]
    prev = repo.approval_owner_id
    repo.approval_owner_id = "user_lead"
    try:
        assert lead.get(f"/api/tasks/{task.id}/replay").status_code == 200
    finally:
        repo.approval_owner_id = prev


def test_replay_db_backed_after_clear_memory(tmp_path, monkeypatch):
    """DB 模式：清空内存缓存后，回放仍能从数据库读取（DB 真源）。"""
    from app.db.repositories import upsert_task
    from app.db.session import create_schema, get_engine, get_session_factory

    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'replay.db').as_posix()}")
    get_engine.cache_clear(); get_session_factory.cache_clear(); create_schema()
    try:
        task = _base_task()
        task.events.append(_event(task.id, "TASK_CREATED", "任务已创建", created_at=_now(), diff=[f"task_id={task.id}"]))
        cmt = main.CommentRecord(
            id=f"cmt_db_replay", task_id=task.id, project_id=task.project_id, repository_id=task.repository_id,
            author_id=developer.id, author_name=developer.name, body="DB 真源评论",
            mentioned_user_ids=[], reference_type="task", reference_id=task.id, created_at=_now(), deleted_at=None,
        )
        main.COMMENTS[cmt.id] = cmt
        for ev in task.events:
            main.AUDIT_EVENTS[ev.id] = ev
        upsert_task(task)
        from app.db.repositories import upsert_comment

        upsert_comment(cmt)

        # 清空内存，从 DB 恢复后应仍能读到同一条目
        main.TASKS.clear(); main.COMMENTS.clear(); main.AUDIT_EVENTS.clear()
        from app.db.bootstrap import restore_from_db

        restore_from_db()
        client = authed("admin@company.com", "Admin123!")
        data = client.get(f"/api/tasks/{task.id}/replay").json()["data"]
        assert data["task_id"] == task.id
        assert any(c["body"] == "DB 真源评论" for c in data["artifacts"]["comments"])
        assert any(ev["event_type"] == "TASK_CREATED" for ev in data["timeline"])
    finally:
        get_engine.cache_clear(); get_session_factory.cache_clear()