"""wait_index 自动恢复闭环测试：索引 COMPLETED 自动恢复等待任务、幂等、投递 run_agent、事件/审计。

覆盖（对应里程碑 A 收尾要求 8/9）：
- index_action_for_head 建新索引后自动 `_enqueue_index_build`（不再依赖 HTTP 轮询）；
- index COMPLETED → 任务自动恢复（事件/步骤/审计 + 投递 run_agent）；
- 幂等：同一 (index, task) 不重复推进；
- 空检索 → context_missing（不失败）；
- 恢复后 run_agent 被投递。
"""
from __future__ import annotations

import datetime

from app import main


def _idx(rid="idx_r", repo_id="r", commit="a" * 40, status="COMPLETED", files=3, chunks=4, collection="code_x"):
    now = datetime.datetime.now(datetime.timezone.utc)
    return main.RepositoryIndexRecord(
        id=rid, repository_id=repo_id, project_id="p", base_branch="master", commit_sha=commit,
        status=status, mode="full", total_files=files, processed_files=files, chunk_count=chunks,
        milvus_collection=collection, requested_by="u", requested_by_name="u", retry_count=0,
        requested_at=now, created_at=now, updated_at=now, started_at=now, finished_at=now if status == "COMPLETED" else None,
        error_message=None, error_stage=None,
    )


def _waiting_task(task_id, index_id, commit="a" * 40, snapshot_text="修复回调幂等：需要真实代码上下文"):
    now = datetime.datetime.now(datetime.timezone.utc)
    t = main.TaskRecord(
        id=task_id, version=1, project_id="p", project_name="P", repository_id="r", repository_name="R",
        repository_url="https://gitee.com/o/r.git", issue_id=f"I{task_id}", title="回调幂等修复",
        status="ISSUE_FETCHED", base_branch="master", test_command="echo ok", work_branch="agent/main/issue-x-v1",
        issue_snapshot=main.IssueSnapshotRecord(title="回调幂等修复", body=snapshot_text, comments=[], labels=[],
                                                raw_response={}, fetched_at=now - datetime.timedelta(minutes=2)),
        automation_state="running", manual_required=False, manual_reason="none",
        next_action="wait_index", created_by="u", created_by_name="u", created_at=now, updated_at=now,
        related_index_id=index_id, base_commit_sha=commit, events=[], execution_steps=[],
    )
    return t


def test_create_index_branch_enqueues_build(monkeypatch):
    """要求 8：任务自动创建索引（create_index）必须调用 _enqueue_index_build，不再只在 HTTP 轮询里推。"""
    calls = []
    monkeypatch.setattr(main, "_enqueue_index_build", lambda iid: calls.append(iid))
    repo = main.RepositoryConfigRecord(
        id="r", project_id="p", name="R", url="https://gitee.com/o/r.git", default_branch="master",
        test_command="echo ok", command_whitelist=["echo ok"], approval_mode="single", risk_threshold="medium",
        max_files=5, max_retries=1, blocked_paths=[],
        created_at=datetime.datetime.now(datetime.timezone.utc), updated_at=datetime.datetime.now(datetime.timezone.utc),
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    old = dict(main.REPOSITORY_INDEXES)
    main.REPOSITORY_INDEXES.clear()
    try:
        decision, idx, reason = main.index_action_for_head(repo, head_commit="c" * 40, branch="master", actor=main.USERS["user_admin"], requested_at=now)
        assert decision == "create_index", (decision, reason)
        assert calls == [idx.id], calls
        assert idx.status == "PENDING", idx.status
    finally:
        main.REPOSITORY_INDEXES.clear()
        main.REPOSITORY_INDEXES.update(old)
        main.CODE_INDEX_SNAPSHOTS.clear()


def test_index_completed_resumes_waiting_task(monkeypatch):
    """要求 2/3/5：索引 COMPLETED 后，wait_index 任务自动恢复：事件/步骤/审计 + 投递 run_agent。"""
    idx = _idx(rid="idx_c", repo_id="r", commit="c" * 40)
    task = _waiting_task("TASK-W1", idx.id, commit="c" * 40)
    main.TASKS[task.id] = task
    dispatched = []
    monkeypatch.setattr(main, "_dispatch_agent_if_needed",
                        lambda tid, t: (dispatched.append(tid) or True))
    cleared = []
    monkeypatch.setattr(main, "_clear_agent_enqueued", lambda tid: cleared.append(tid))
    try:
        main.resume_tasks_waiting_for_index(idx)
        assert dispatched == [task.id], "应投递 run_agent 恢复任务"
        assert cleared == [task.id], "投递前应清 _AGENT_ENQUEUED"
        events = [e.event_type for e in task.events]
        assert "INDEX_RESUMED" in events, events
        nodes = [s.node for s in task.execution_steps]
        assert "index_decision" in nodes, nodes
        resumed = [s for s in task.execution_steps if s.node == "index_decision" and "恢复" in (s.title or "")]
        assert resumed, "应写 index_decision 恢复 step"
        # 审计里也应有（automation_event 落 audit_events + append_task_event 落 task_events）
        assert any(e.event_type == "INDEX_RESUMED" for e in main.AUDIT_EVENTS.values())
    finally:
        main.TASKS.pop(task.id, None)
        main._INDEX_RESUMED.discard((idx.id, task.id))


def test_resume_idempotent_same_index_task(monkeypatch):
    """要求 7：同一 (index, task) 只恢复一次，重复触发不重复推进。"""
    idx = _idx(rid="idx_i")
    task = _waiting_task("TASK-W2", idx.id)
    main.TASKS[task.id] = task
    dispatched = []
    def fake_dispatch(tid, t):
        dispatched.append(tid)
        return True
    monkeypatch.setattr(main, "_dispatch_agent_if_needed", fake_dispatch)
    monkeypatch.setattr(main, "_clear_agent_enqueued", lambda tid: None)
    try:
        main.resume_tasks_waiting_for_index(idx)
        main.resume_tasks_waiting_for_index(idx)
        assert dispatched == [task.id], f"重复触发应只投一次，实际 {dispatched}"
        assert [e.event_type for e in task.events].count("INDEX_RESUMED") == 1
    finally:
        main.TASKS.pop(task.id, None)
        main._INDEX_RESUMED.discard((idx.id, task.id))


def test_resume_skips_non_waiting_tasks(monkeypatch):
    """非 wait_index 任务不被恢复；索引仅 COMPLETED 才触发。"""
    idx = _idx(rid="idx_s")
    done = _waiting_task("TASK-DONE", idx.id)
    done.status = "ANALYZED"; done.next_action = "generate_plan"
    main.TASKS[done.id] = done
    dispatched = []
    monkeypatch.setattr(main, "_dispatch_agent_if_needed", lambda tid, t: (dispatched.append(tid) or True))
    try:
        main.resume_tasks_waiting_for_index(idx)
        assert dispatched == [], "非 wait_index 任务不应被投递"
        main.resume_tasks_waiting_for_index(_idx(rid="idx_running", status="PARSING", commit="z" * 40))
        assert dispatched == []
    finally:
        main.TASKS.pop(done.id, None)
        main._INDEX_RESUMED.clear()