"""DEV-017 自动上下文补齐测试套件。

覆盖：
- 常规 Issue 创建后自动拉 Issue/选 Commit/复用索引/检索注入/历史相似任务，无需人工进入分析队列；
- 索引缺失自动创建并等待，索引完成后自动检索注入；
- 检索为空或全部低于阈值时任务不失败，置 manual_required=context_missing（与 answer_required 严格区分）；
- resume 重新检索恢复、仍空保持人工态、手动指定文件恢复、answer_required 拒绝 resume、索引运行中提示；
- 历史相似任务只返回引用、不含旧 Diff、检索异常不阻断主链路；
- 回放可见 Issue fetched / Commit selected / Index decision / Retrieval / Manual supplement 节点；
- 权限：resume 需 task:operate，similar 需 task:view。
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main

HEAD_A = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
HEAD_B = "f0e1d2c3b4a5968778695a4b3c2d1e0f1a2b3c4d"

PAYMENT_BODY = "支付回调在弱网重试下会重复入账，需要保证回调链路幂等，涉及支付与记账。"
RELATED_CODE = (
    "def handle_payment_callback(request):\n"
    "    # 支付回调弱网重试时保证幂等，避免重复入账\n"
    "    if dedup_key_exists(request.trace_id):\n"
    "        return duplicate_notice()\n"
    "    return create_ledger_entry(request.amount)\n"
)
UNRELATED_CODE = "const THEME = { primary: '#1f6feb', radius: 8 };\n"

repository = main.REPOSITORIES["repo_payment_service"]
developer = main.USERS["user_developer"]


@pytest.fixture(autouse=True)
def fast_index_and_seams(monkeypatch):
    """索引零等待；head 固定；默认快照含与 Issue 同主题的可命中代码。"""
    monkeypatch.setattr(main, "INDEX_STAGE_SECONDS", 0)
    monkeypatch.setattr(main, "fetch_gitee_branch_head", lambda repo, base_branch=None: HEAD_A)
    monkeypatch.setattr(
        main,
        "sync_repository_snapshot",
        lambda repo, commit_sha: [
            main.CodeFile(path="services/payment/callback.py", language="python", content=RELATED_CODE)
        ],
    )
    yield


def make_index(commit_sha: str = HEAD_A, status: str = "COMPLETED") -> main.RepositoryIndexRecord:
    record = main.new_repository_index(
        repository=repository,
        commit_sha=commit_sha,
        base_branch=repository.default_branch,
        requested_by=developer,
        requested_at=datetime.now(timezone.utc),
    )
    if status == "COMPLETED":
        while record.status in main.INDEX_RUNNING_STAGES:
            main.progress_single_repository_index(record)
    elif status in main.INDEX_RUNNING_STAGES:
        record.status = status
    return record


def snapshot_of(title: str, body: str, labels: list[str] | None = None) -> main.IssueSnapshotRecord:
    return main.IssueSnapshotRecord(
        title=title,
        body=body,
        comments=[],
        labels=labels or ["bug", "支付"],
        raw_response={},
        fetched_at=datetime.now(timezone.utc),
    )


def create_task_via_api(client: TestClient, issue_id: str = "ICTX101", body: str = PAYMENT_BODY):
    main.fetch_gitee_issue_snapshot = lambda repo, issue: snapshot_of("修复支付回调重复入账", body)
    return client.post(
        "/api/tasks",
        json={"project_id": repository.project_id, "repository_id": repository.id, "issue_id": issue_id},
    )


def authed_client(account: str, password: str) -> TestClient:
    test_client = TestClient(main.app)
    login = test_client.post("/api/auth/login", json={"account": account, "password": password})
    assert login.status_code == 200
    return test_client


def event_types(task: main.TaskRecord) -> list[str]:
    return [event.event_type for event in task.events]


def step_nodes(task: main.TaskRecord) -> list[str]:
    return [step.node for step in task.execution_steps]


def use_unrelated_snapshot(monkeypatch) -> None:
    """覆盖 autouse 快照：代码与 Issue 主题无关，自动检索必然为空。"""
    monkeypatch.setattr(
        main,
        "sync_repository_snapshot",
        lambda repo, commit_sha: [
            main.CodeFile(path="src/theme.ts", language="typescript", content=UNRELATED_CODE)
        ],
    )


# ---------- 1. 常规链路：无需人工自动进入分析 ----------

def test_regular_issue_auto_fills_context_and_queues_analysis(client):
    make_index(HEAD_A)
    response = create_task_via_api(client)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "CONTEXT_INDEXED"
    assert data["next_action"] == "auto_queue_analysis"
    assert data["manual_required"] is False
    assert len(data["code_contexts"]) >= 1
    assert data["base_commit_sha"] == HEAD_A

    task = main.TASKS[data["id"]]
    # 自动推进：CONTEXT_INDEXED -> ANALYSIS_QUEUED（分析生成受 8s 闸门，不在本用例范围）
    main.progress_task_automation(task)
    assert task.status == "ANALYSIS_QUEUED"

    types = event_types(task)
    for required in ["ISSUE_FETCHED", "COMMIT_SELECTED", "INDEX_REUSE_CHECKED", "CONTEXT_INDEXED", "HISTORICAL_TASKS_MATCHED"]:
        assert required in types, f"回放缺少事件 {required}"
    nodes = step_nodes(task)
    for required in ["issue_fetch", "commit_select", "index_decision", "context_fetch"]:
        assert required in nodes, f"回放缺少执行节点 {required}"


def test_commit_selected_event_carries_head_and_branch(client):
    make_index(HEAD_A)
    response = create_task_via_api(client)
    task = main.TASKS[response.json()["data"]["id"]]
    commit_event = [event for event in task.events if event.event_type == "COMMIT_SELECTED"][0]
    assert HEAD_A in commit_event.diff_summary[1]
    assert repository.default_branch in commit_event.diff_summary[0]


# ---------- 2. 索引缺失/过期：自动创建并等待，完成后自动检索 ----------

def test_missing_index_auto_created_and_resumes_after_completion(client):
    response = create_task_via_api(client, issue_id="ICTX102")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "ISSUE_FETCHED"
    assert data["next_action"] == "wait_index"
    assert any(event["event_type"] == "INDEX_SCHEDULED" for event in data["events"])

    task = main.TASKS[data["id"]]
    index = main.REPOSITORY_INDEXES[data["related_index_id"]]
    while index.status in main.INDEX_RUNNING_STAGES:
        main.progress_single_repository_index(index)
    main.progress_task_automation(task)
    assert "CONTEXT_INDEXED" in event_types(task), "索引完成后应自动检索注入"
    assert task.status == "ANALYSIS_QUEUED", "上下文就绪后应自动进入分析队列"
    assert len(task.code_contexts) >= 1


# ---------- 3. 检索为空：任务不失败，转 context_missing ----------

def test_empty_retrieval_marks_context_missing_without_failure(client, monkeypatch):
    use_unrelated_snapshot(monkeypatch)
    make_index(HEAD_A)
    response = create_task_via_api(client, issue_id="ICTX103")
    data = response.json()["data"]
    assert response.status_code == 200
    assert data["status"] == "ISSUE_FETCHED", "空检索不得进入分析队列"
    assert data["manual_required"] is True
    assert data["manual_reason"] == "context_missing"
    assert data["next_action"] == "supplement_context"
    assert data["code_contexts"] == []
    types = [event["event_type"] for event in data["events"]]
    assert "CONTEXT_RETRIEVAL_EMPTY" in types
    assert "TASK_MANUAL_REQUIRED" in types
    assert "ANALYSIS_QUEUED" not in types

    task = main.TASKS[data["id"]]
    main.progress_task_automation(task)
    assert task.status == "ISSUE_FETCHED", "自动推进不得把上下文不足任务推入分析"
    assert not task.failure_node, "上下文不足不是任务失败"


# ---------- 4. resume：重新检索恢复 / 仍空保持 ----------

def test_resume_recovers_after_context_becomes_available(client, monkeypatch):
    use_unrelated_snapshot(monkeypatch)
    make_index(HEAD_A)
    created = create_task_via_api(client, issue_id="ICTX104").json()["data"]
    task = main.TASKS[created["id"]]
    index = main.REPOSITORY_INDEXES[created["related_index_id"]]

    # 补充一个同主题切片（等价于重建索引后命中）
    main.CODE_CHUNKS["chunk_ctx104"] = main.CodeChunkRecord(
        chunk_id="chunk_ctx104",
        repository_id=repository.id,
        index_id=index.id,
        file_path="services/payment/callback.py",
        chunk_index=0,
        content=RELATED_CODE,
        language="python",
        symbol_name="handle_payment_callback",
        start_line=1,
        end_line=5,
        commit_sha=HEAD_A,
        milvus_collection=main.index_collection_name(index.id),
        milvus_pk="chunk_ctx104",
        checksum="ctx104",
    )
    resume = client.post(f"/api/tasks/{task.id}/contexts/resume", json={})
    assert resume.status_code == 200, resume.text
    payload = resume.json()
    assert payload["data"]["resumed"] is True
    assert task.status == "ANALYSIS_QUEUED", "恢复后应自动推进到分析队列"
    assert task.manual_required is False
    assert task.manual_reason == "none"
    assert "CONTEXT_SUPPLEMENTED" in event_types(task)


def test_resume_still_empty_keeps_manual_state(client, monkeypatch):
    use_unrelated_snapshot(monkeypatch)
    make_index(HEAD_A)
    created = create_task_via_api(client, issue_id="ICTX105").json()["data"]
    task = main.TASKS[created["id"]]
    resume = client.post(f"/api/tasks/{task.id}/contexts/resume", json={})
    assert resume.status_code == 200
    assert resume.json()["data"]["resumed"] is False
    assert task.manual_required is True
    assert task.manual_reason == "context_missing"


def test_resume_while_index_running_returns_waiting_hint(client, monkeypatch):
    use_unrelated_snapshot(monkeypatch)
    # 阶段耗时拉长，保证 resume 内推进后索引仍停在运行态
    monkeypatch.setattr(main, "INDEX_STAGE_SECONDS", 3600)
    running = make_index(HEAD_A, status="PARSING")
    now = datetime.now(timezone.utc)
    task = main.TaskRecord(
        id="TASK-CTX106",
        version=1,
        project_id=repository.project_id,
        project_name=main.PROJECTS[repository.project_id].name,
        repository_id=repository.id,
        repository_name=repository.name,
        repository_url=repository.url,
        issue_id="ICTX106",
        title="修复支付回调重复入账",
        status="ISSUE_FETCHED",
        base_branch=repository.default_branch,
        test_command=repository.test_command,
        approval_owner_id=repository.approval_owner_id,
        approval_owner_name=repository.approval_owner_name,
        work_branch="agent/main/issue-ICTX106-v1",
        issue_snapshot=snapshot_of("修复支付回调重复入账", PAYMENT_BODY),
        automation_state="blocked",
        manual_required=True,
        manual_reason="context_missing",
        next_action="supplement_context",
        events=[],
        related_index_id=running.id,
        base_commit_sha=HEAD_A,
        created_by=developer.id,
        created_by_name=developer.name,
        created_at=now - timedelta(seconds=30),
        updated_at=now,
    )
    main.TASKS[task.id] = task
    resume = client.post(f"/api/tasks/{task.id}/contexts/resume", json={})
    assert resume.status_code == 200
    assert resume.json()["data"]["resumed"] is False
    assert "处理中" in resume.json()["message"]


# ---------- 5. 手动指定文件恢复 ----------

def test_manual_file_context_resumes_context_missing(client, monkeypatch):
    use_unrelated_snapshot(monkeypatch)
    index = make_index(HEAD_A)
    created = create_task_via_api(client, issue_id="ICTX107").json()["data"]
    task = main.TASKS[created["id"]]
    assert task.manual_reason == "context_missing"
    manual = client.post(
        f"/api/tasks/{task.id}/contexts/manual",
        json={"file_paths": ["src/theme.ts"]},
    )
    assert manual.status_code == 200, manual.text
    assert manual.json()["data"]["resumed"] is True
    assert task.status == "ANALYSIS_QUEUED"
    assert task.manual_required is False
    types = event_types(task)
    assert "MANUAL_CONTEXT_ADDED" in types
    assert "CONTEXT_SUPPLEMENTED" in types


# ---------- 6. context_missing 与 answer_required 严格区分 ----------

def test_resume_rejects_answer_required_task(client):
    now = datetime.now(timezone.utc)
    task = main.TaskRecord(
        id="TASK-CTX108",
        version=1,
        project_id=repository.project_id,
        project_name=main.PROJECTS[repository.project_id].name,
        repository_id=repository.id,
        repository_name=repository.name,
        repository_url=repository.url,
        issue_id="ICTX108",
        title="修复支付回调重复入账",
        status="ANALYZED",
        base_branch=repository.default_branch,
        test_command=repository.test_command,
        approval_owner_id=repository.approval_owner_id,
        approval_owner_name=repository.approval_owner_name,
        work_branch="agent/main/issue-ICTX108-v1",
        issue_snapshot=snapshot_of("修复支付回调重复入账", PAYMENT_BODY),
        automation_state="blocked",
        manual_required=True,
        manual_reason="answer_required",
        next_action="answer_analysis_question",
        events=[],
        created_by=developer.id,
        created_by_name=developer.name,
        created_at=now - timedelta(seconds=30),
        updated_at=now,
    )
    main.TASKS[task.id] = task
    resume = client.post(f"/api/tasks/{task.id}/contexts/resume", json={})
    assert resume.status_code == 409
    assert "answer_required" in resume.text


def test_resume_rejects_failed_task(client):
    now = datetime.now(timezone.utc)
    task = main.TaskRecord(
        id="TASK-CTX109",
        version=1,
        project_id=repository.project_id,
        project_name=main.PROJECTS[repository.project_id].name,
        repository_id=repository.id,
        repository_name=repository.name,
        repository_url=repository.url,
        issue_id="ICTX109",
        title="索引失败后的恢复门禁",
        status="FAILED",
        base_branch=repository.default_branch,
        test_command=repository.test_command,
        approval_owner_id=repository.approval_owner_id,
        approval_owner_name=repository.approval_owner_name,
        work_branch="agent/main/issue-ICTX109-v1",
        issue_snapshot=snapshot_of("索引失败后的恢复门禁", PAYMENT_BODY),
        automation_state="blocked",
        manual_required=True,
        manual_reason="context_missing",
        next_action="supplement_context",
        events=[],
        created_by=developer.id,
        created_by_name=developer.name,
        created_at=now - timedelta(seconds=30),
        updated_at=now,
    )
    main.TASKS[task.id] = task
    resume = client.post(f"/api/tasks/{task.id}/contexts/resume", json={})
    assert resume.status_code == 409
    assert "重试接口" in resume.text


# ---------- 7. 历史相似任务 ----------

def _seed_completed_task(issue_id: str, title: str, body: str) -> main.TaskRecord:
    now = datetime.now(timezone.utc)
    task = main.TaskRecord(
        id=f"TASK-OLD-{len(main.TASKS)}",
        version=1,
        project_id=repository.project_id,
        project_name=main.PROJECTS[repository.project_id].name,
        repository_id=repository.id,
        repository_name=repository.name,
        repository_url=repository.url,
        issue_id=issue_id,
        title=title,
        status="COMPLETED",
        base_branch=repository.default_branch,
        test_command=repository.test_command,
        approval_owner_id=repository.approval_owner_id,
        approval_owner_name=repository.approval_owner_name,
        work_branch=f"agent/main/issue-{issue_id}-v1",
        issue_snapshot=snapshot_of(title, body),
        automation_state="completed",
        manual_required=False,
        manual_reason="none",
        next_action="none",
        events=[],
        created_by=developer.id,
        created_by_name=developer.name,
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(hours=20),
    )
    main.TASKS[task.id] = task
    return task


def test_similar_historical_tasks_attached_and_listed_without_diff(client):
    old = _seed_completed_task("ICTX-OLD", "修复支付回调幂等重复入账", PAYMENT_BODY)
    make_index(HEAD_A)
    created = create_task_via_api(client, issue_id="ICTX109").json()["data"]
    task = main.TASKS[created["id"]]

    assert len(task.similar_task_refs) >= 1
    ref = task.similar_task_refs[0]
    assert ref.task_id == old.id
    assert ref.score >= main.SIMILAR_TASK_MIN_SCORE
    # 引用只含展示字段，绝不携带旧 Diff / 计划
    ref_dump = ref.model_dump()
    assert "diff" not in ref_dump and "plans" not in ref_dump and "code_contexts" not in ref_dump
    assert task.id not in [item.task_id for item in task.similar_task_refs], "相似任务不得包含自己"

    listed = client.get(f"/api/tasks/{task.id}/similar")
    assert listed.status_code == 200
    payload = listed.json()["data"]
    assert payload["threshold"] == main.SIMILAR_TASK_MIN_SCORE
    assert any(item["task_id"] == old.id for item in payload["similar_tasks"])
    matched = [event for event in task.events if event.event_type == "HISTORICAL_TASKS_MATCHED"]
    assert matched and "参考" in matched[-1].summary


def test_similar_task_failure_does_not_block_main_flow(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("similar index unavailable")

    monkeypatch.setattr(main, "find_similar_historical_tasks", boom)
    make_index(HEAD_A)
    response = create_task_via_api(client, issue_id="ICTX110")
    assert response.status_code == 200
    task = main.TASKS[response.json()["data"]["id"]]
    assert task.status == "CONTEXT_INDEXED", "历史相似任务检索失败不得阻断上下文主链路"
    assert task.similar_task_refs == []
    assert "HISTORICAL_TASKS_MATCHED" in event_types(task)


# ---------- 8. 权限 ----------

def test_resume_requires_task_operate_permission(client, monkeypatch):
    use_unrelated_snapshot(monkeypatch)
    make_index(HEAD_A)
    created = create_task_via_api(client, issue_id="ICTX111").json()["data"]
    observer = authed_client("observer@company.com", "Observer123!")
    resume = observer.post(f"/api/tasks/{created['id']}/contexts/resume", json={})
    assert resume.status_code == 403
    # observer 有 task:view，可以查看历史相似任务
    listed = observer.get(f"/api/tasks/{created['id']}/similar")
    assert listed.status_code == 200


# ---------- 9. 回放五类节点齐全（含人工补充） ----------

def test_replay_contains_full_context_fill_timeline(client, monkeypatch):
    use_unrelated_snapshot(monkeypatch)
    make_index(HEAD_A)
    created = create_task_via_api(client, issue_id="ICTX112").json()["data"]
    task = main.TASKS[created["id"]]
    client.post(f"/api/tasks/{task.id}/contexts/manual", json={"file_paths": ["src/theme.ts"]})

    timeline = client.get(f"/api/tasks/{task.id}/timeline")
    assert timeline.status_code == 200
    types = event_types(task)
    # Issue fetched / Commit selected / Index decision / Retrieval / Manual supplement
    assert "ISSUE_FETCHED" in types
    assert "COMMIT_SELECTED" in types
    assert "INDEX_REUSE_CHECKED" in types
    assert "CONTEXT_RETRIEVAL_EMPTY" in types
    assert "CONTEXT_SUPPLEMENTED" in types
    assert set(["issue_fetch", "commit_select", "index_decision", "context_fetch"]).issubset(set(step_nodes(task)))
