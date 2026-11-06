"""DEV-008 代码 RAG 与索引模块测试套件。

覆盖：四行分流决策（复用/过期重建/运行中等待/FAILED/缺失自动建）、索引七态推进、
阶段失败与重试、任务等待索引自动推进、检索（字段/Top-K/过滤/空结果/手动指定文件）、
fresh 门禁、权限矩阵、可追溯记录与敏感文件过滤。
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main

HEAD_A = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
HEAD_B = "f0e1d2c3b4a5968778695a4b3c2d1e0f1a2b3c4d"
PAYMENT_BODY_TEXT = (
    "支付回调网络重试时重复入账，需要确认幂等策略，涉及金额与记账，可能影响历史数据兼容。"
)
CALLBACK_CONTENT = (
    "def handle_payment_callback(request):\n"
    "    # 支付回调处理：校验签名与金额\n"
    "    order = load_order(request.order_no)\n"
    "    if order.status == 'paid':\n"
    "        return already_handled()\n"
    "    # 幂等判断：重复回调不重复入账\n"
    "    if dedup_key_exists(request.trace_id):\n"
    "        return duplicate_notice()\n"
    "    record = create_ledger_entry(order, request.amount)\n"
    "    mark_dedup(request.trace_id)\n"
    "    return success(record)\n"
    "    # 支付回调重复记账修复目标：兼容历史无交易号的回调\n"
    "    # 回调中缺少 order_no 时按外部流水号补记，避免重复入账影响对账\n"
)
VALIDATOR_CONTENT = (
    "def validate_id_card(card_no):\n"
    "    # 身份证校验：检查长度、生日与校验位\n"
    "    if len(card_no) != 18:\n"
    "        return False\n"
    "    # 兼容 15 位历史身份证与 18 位新证\n"
    "    return checksum(card_no)\n"
)
SECRET_ENV_CONTENT = "PAYMENT_SECRET=demo-secret\nCALLBACK_URL=https://pay.example.com/callback\n"
INFRA_SECRET_CONTENT = "production:\n  db_password: super-secret\n"
README_CONTENT = "project documentation overview\n"
NODE_MODULES_FILE = "node_modules/axios/index.js\nconst axios = {}\n"


def files_of(*paths: str) -> list[main.CodeFile]:
    pool = {
        "services/payment/callback.py": main.CodeFile(
            path="services/payment/callback.py", language="python", content=CALLBACK_CONTENT
        ),
        "services/payment/validator.py": main.CodeFile(
            path="services/payment/validator.py", language="python", content=VALIDATOR_CONTENT
        ),
        "README.md": main.CodeFile(path="README.md", language="markdown", content=README_CONTENT),
        ".env": main.CodeFile(path=".env", language="text", content=SECRET_ENV_CONTENT),
        "infra/production/secret.yaml": main.CodeFile(
            path="infra/production/secret.yaml", language="yaml", content=INFRA_SECRET_CONTENT
        ),
        "node_modules/axios/index.js": main.CodeFile(
            path="node_modules/axios/index.js", language="javascript", content=NODE_MODULES_FILE
        ),
    }
    return [pool[path] for path in paths]


DEFAULT_FILES = files_of(
    "services/payment/callback.py",
    "services/payment/validator.py",
    "README.md",
    ".env",
    "infra/production/secret.yaml",
    "node_modules/axios/index.js",
)

repository = main.REPOSITORIES["repo_payment_service"]
developer = main.USERS["user_developer"]


@pytest.fixture(autouse=True)
def fast_index_and_seams(monkeypatch):
    """默认：索引阶段零等待；分支 head 与代码快照 seam 返回确定性夹具（快照按仓库 blocklist 预过滤）。"""
    monkeypatch.setattr(main, "INDEX_STAGE_SECONDS", 0)
    monkeypatch.setattr(main, "fetch_gitee_branch_head", lambda repo, base_branch=None: HEAD_A)
    monkeypatch.setattr(
        main,
        "sync_repository_snapshot",
        lambda repo, commit_sha: [
            file for file in DEFAULT_FILES if not main.path_blocked(file.path, repo.blocked_paths)
        ],
    )
    yield


def make_index(commit_sha: str = HEAD_A, status: str = "COMPLETED", requested_at: datetime | None = None) -> main.RepositoryIndexRecord:
    now = requested_at or datetime.now(timezone.utc)
    record = main.new_repository_index(
        repository=repository,
        commit_sha=commit_sha,
        base_branch=repository.default_branch,
        requested_by=developer,
        requested_at=now,
    )
    if status == "COMPLETED":
        while record.status in main.INDEX_RUNNING_STAGES:
            main.progress_single_repository_index(record)
    elif status in main.INDEX_RUNNING_STAGES:
        record.status = status
        record.updated_at = now
    elif status == "FAILED":
        main.fail_repository_index(record, stage="SYNCING", message="Gitee 快照同步超时", now=now)
    return record


def seed_task(
    *,
    issue_id: str = "ISTUB101",
    title: str = "修复支付回调重复记账",
    body: str = PAYMENT_BODY_TEXT,
    base_commit: str | None = HEAD_A,
    status: str = "CREATED",
    related_index_id: str | None = None,
    created_ago_seconds: int = 30,
) -> main.TaskRecord:
    now = datetime.now(timezone.utc)
    snapshot = main.IssueSnapshotRecord(
        title=title,
        body=body,
        comments=[],
        labels=["bug", "支付"],
        raw_response={},
        fetched_at=now,
    )
    task = main.TaskRecord(
        id=f"TASK-{9000 + len(main.TASKS)}",
        version=1,
        project_id=repository.project_id,
        project_name=main.PROJECTS[repository.project_id].name,
        repository_id=repository.id,
        repository_name=repository.name,
        repository_url=repository.url,
        issue_id=issue_id,
        title=title,
        status=status,  # type: ignore[arg-type]
        base_branch=repository.default_branch,
        test_command=repository.test_command,
        approval_owner_id=repository.approval_owner_id,
        approval_owner_name=repository.approval_owner_name,
        work_branch=f"agent/main/issue-{issue_id}-v1",
        issue_snapshot=snapshot,
        automation_state="running",
        manual_required=False,
        manual_reason="none",
        next_action="check_index",
        events=[],
        created_by=developer.id,
        created_by_name=developer.name,
        created_at=now - timedelta(seconds=created_ago_seconds),
        updated_at=now,
        base_commit_sha=base_commit,
        related_index_id=related_index_id,
    )
    main.TASKS[task.id] = task
    return task


def test_contexts_prefer_latest_completed_index_with_chunks_after_same_commit_rebuild():
    """同 Commit 重建后，任务不能继续命中已被切片迁移为空的旧关联索引。"""
    old_index = make_index()
    new_index = make_index(requested_at=old_index.created_at + timedelta(seconds=1))
    for chunk_id, chunk in list(main.CODE_CHUNKS.items()):
        if chunk.index_id == old_index.id:
            main.CODE_CHUNKS.pop(chunk_id)

    task = seed_task(related_index_id=old_index.id)

    selected, reason = main.pick_retrieval_index(task)

    assert reason is None
    assert selected is not None
    assert selected.id == new_index.id


def snapshot_of(title: str, body: str, labels: list[str] | None = None) -> main.IssueSnapshotRecord:
    return main.IssueSnapshotRecord(
        title=title,
        body=body,
        comments=[],
        labels=labels or ["bug"],
        raw_response={},
        fetched_at=datetime.now(timezone.utc),
    )


def create_task_via_api(client: TestClient, issue_id: str = "ISTUB101", body: str = PAYMENT_BODY_TEXT):
    main.fetch_gitee_issue_snapshot = lambda repo, issue: snapshot_of(f"修复{issue}重复入账", body)
    return client.post(
        "/api/tasks",
        json={"project_id": repository.project_id, "repository_id": repository.id, "issue_id": issue_id},
    )


def approver_client() -> TestClient:
    test_client = TestClient(main.app)
    login = test_client.post(
        "/api/auth/login",
        json={"account": "approver@company.com", "password": "Approver123!"},
    )
    assert login.status_code == 200
    return test_client


def run_index_to_terminal(record: main.RepositoryIndexRecord) -> main.RepositoryIndexRecord:
    while record.status in main.INDEX_RUNNING_STAGES:
        main.progress_single_repository_index(record)
    return record


# ---------- 分流决策：四行状态机 ----------

def test_create_task_reuses_completed_index_without_prompt(client):
    index = make_index(HEAD_A)
    assert index.status == "COMPLETED"
    response = create_task_via_api(client)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "CONTEXT_INDEXED"
    assert data["related_index_id"] == index.id
    assert data["base_commit_sha"] == HEAD_A
    assert len(data["code_contexts"]) > 0
    assert len(main.REPOSITORY_INDEXES) == 1, "复用命中时不得自动新建索引"
    assert any(event["event_type"] == "INDEX_REUSE_CHECKED" for event in data["events"])
    assert any(event["event_type"] == "CONTEXT_INDEXED" for event in data["events"])
    assert "复用" in response.json()["message"]


def test_create_task_stale_index_auto_rebuilds(client, monkeypatch):
    make_index(HEAD_A)
    monkeypatch.setattr(main, "fetch_gitee_branch_head", lambda repo, base_branch=None: HEAD_B)
    response = create_task_via_api(client)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "ISSUE_FETCHED"
    assert data["next_action"] == "wait_index"
    assert len(main.REPOSITORY_INDEXES) == 2
    new_index = main.REPOSITORY_INDEXES[data["related_index_id"]]
    assert new_index.commit_sha == HEAD_B
    assert new_index.status == "PENDING"
    assert any(event["event_type"] == "INDEX_SCHEDULED" for event in data["events"])


def test_create_task_waits_for_running_same_commit_index(client):
    make_index(HEAD_A, status="SYNCING")
    response = create_task_via_api(client)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "ISSUE_FETCHED"
    assert data["next_action"] == "wait_index"
    assert len(main.REPOSITORY_INDEXES) == 1, "运行中同 Commit 索引不得重复创建"
    assert data["related_index_id"] == list(main.REPOSITORY_INDEXES)[0]


def test_create_task_failed_index_blocks_with_reason(client):
    index = make_index(HEAD_A, status="FAILED")
    response = create_task_via_api(client)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "FAILED"
    assert data["failure_node"] == "代码索引"
    assert "Gitee 快照同步超时" in (data["failure_reason"] or "")
    assert data["manual_required"] is True
    assert data["next_action"] == "retry_or_cancel"
    assert len(main.REPOSITORY_INDEXES) == 1, "FAILED 同 Commit 不得自动反复重建"
    assert index.status == "FAILED"


def test_create_task_missing_index_auto_creates(client):
    response = create_task_via_api(client)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "ISSUE_FETCHED"
    assert data["next_action"] == "wait_index"
    records = main.index_records_of(repository.id)
    assert len(records) == 1
    assert records[0].status == "PENDING"
    assert records[0].commit_sha == HEAD_A
    assert "自动创建" in response.json()["message"]


# ---------- 索引七态推进与等待任务自动推进 ----------

def test_parse_and_chunk_filters_sensitive_files():
    chunks = main.parse_and_chunk(
        DEFAULT_FILES,
        repository_id=repository.id,
        index_id="idx_parse_test",
        commit_sha=HEAD_A,
        blocked_paths=repository.blocked_paths,
    )
    paths = {chunk.file_path for chunk in chunks}
    assert paths == {
        "services/payment/callback.py",
        "services/payment/validator.py",
        "README.md",
    }
    assert not any(
        path.endswith(".env") or "secret" in path or "node_modules" in path or "infra/" in path
        for path in paths
    )
    assert all(chunk.commit_sha == HEAD_A and chunk.checksum for chunk in chunks)
    assert all(chunk.symbol_name for chunk in chunks if chunk.file_path.endswith(".py"))


def test_index_progresses_stages_to_completed_with_counts(client):
    index = make_index(HEAD_A, status="PENDING")
    while index.status in main.INDEX_RUNNING_STAGES:
        main.progress_single_repository_index(index)
    assert index.status == "COMPLETED"
    assert index.total_files == 3, ".env / infra/production / node_modules 应被过滤"
    assert index.processed_files == index.total_files
    assert index.chunk_count >= 2
    assert index.milvus_collection == main.index_collection_name(index.id)
    chunk_paths = {chunk.file_path for chunk in main.CODE_CHUNKS.values() if chunk.index_id == index.id}
    assert chunk_paths == {
        "services/payment/callback.py",
        "services/payment/validator.py",
        "README.md",
    }
    assert len(main.CODE_VECTOR_STORE[index.milvus_collection]) == index.chunk_count
    audit = [event for event in main.AUDIT_EVENTS.values() if event.event_type == "INDEX_COMPLETED"]
    assert len(audit) == 1
    assert audit[0].resource_type == "repository_index"
    assert audit[0].resource_id == index.id
    assert main.progress_single_repository_index(index) is False


def test_waiting_task_auto_advances_after_index_completed(client):
    seed_task(created_ago_seconds=30)
    response = create_task_via_api(client, issue_id="ISTUB102")
    task = main.TASKS[response.json()["data"]["id"]]
    assert task.status == "ISSUE_FETCHED" and task.next_action == "wait_index"
    record = main.REPOSITORY_INDEXES[task.related_index_id]
    run_index_to_terminal(record)
    main.progress_task_automation(task)
    # 一次推进可连续走完 等待→检索注入→CONTEXT_INDEXED→ANALYSIS_QUEUED；ANALYZED 受 8s 闸门控制
    assert task.status == "ANALYSIS_QUEUED"
    assert task.code_contexts, "索引完成后应自动注入检索上下文"
    task.created_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    main.progress_task_automation(task)
    assert task.status == "ANALYZED"
    assert task.analysis is not None
    event_types = [event.event_type for event in task.events]
    assert "CONTEXT_INDEXED" in event_types
    assert "ANALYSIS_QUEUED" in event_types
    assert "ANALYSIS_GENERATED" in event_types


def test_reuse_task_reaches_analysis_and_keeps_context(client):
    make_index(HEAD_A)
    seed_task(issue_id="ISTUB201", created_ago_seconds=30)
    response = create_task_via_api(client, issue_id="ISTUB202")
    task = main.TASKS[response.json()["data"]["id"]]
    assert task.status == "CONTEXT_INDEXED"
    task.created_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    main.progress_task_automation(task)
    assert task.status == "ANALYZED"
    assert task.analysis is not None


def test_index_failed_while_task_waiting_fails_task(client, monkeypatch):
    seed_task(issue_id="ISTUB301", created_ago_seconds=30)
    response = create_task_via_api(client, issue_id="ISTUB302")
    task = main.TASKS[response.json()["data"]["id"]]
    record = main.REPOSITORY_INDEXES[task.related_index_id]

    def failing_sync(repo, commit_sha):
        raise main.HTTPException(status_code=502, detail={"code": 502, "message": "Gitee 网络连接失败", "data": None})

    monkeypatch.setattr(main, "sync_repository_snapshot", failing_sync)
    run_index_to_terminal(record)
    assert record.status == "FAILED"
    assert record.error_stage == "SYNCING"
    assert "Gitee 网络连接失败" in (record.error_message or "")
    main.progress_task_automation(task)
    assert task.status == "FAILED"
    assert task.failure_node == "代码索引"
    assert "Gitee 网络连接失败" in (task.failure_reason or "")


def test_index_parse_stage_failure_records_error_stage(client, monkeypatch):
    response = client.post(
        f"/api/repositories/{repository.id}/index",
        json={"force_rebuild": True},
    )
    index = main.REPOSITORY_INDEXES[response.json()["data"]["index_id"]]

    def failing_parse(files, *, repository_id, index_id, commit_sha, blocked_paths):
        raise RuntimeError("解析服务不可用")

    monkeypatch.setattr(main, "parse_and_chunk", failing_parse)
    run_index_to_terminal(index)
    assert index.status == "FAILED"
    assert index.error_stage == "PARSING"
    assert "解析服务不可用" in (index.error_message or "")


def test_index_list_retry_and_dedupe(client):
    first = make_index(HEAD_A, status="PENDING")
    listing = client.get(f"/api/repositories/{repository.id}/indexes")
    assert listing.status_code == 200
    assert listing.json()["data"][0]["id"] == first.id
    trigger = client.post(f"/api/repositories/{repository.id}/index", json={})
    assert trigger.json()["data"]["index_id"] == first.id
    assert len(main.REPOSITORY_INDEXES) == 1
    forced = client.post(f"/api/repositories/{repository.id}/index", json={"force_rebuild": True})
    assert forced.status_code == 200
    second_id = forced.json()["data"]["index_id"]
    assert second_id != first.id
    assert len(main.REPOSITORY_INDEXES) == 2
    detail = client.get(f"/api/repositories/{repository.id}/indexes/{second_id}")
    assert detail.status_code == 200


def test_index_failed_record_retry_only_for_failed(client):
    failed = make_index(HEAD_A, status="FAILED")
    response = client.post(f"/api/repositories/{repository.id}/indexes/{failed.id}/retry")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "PENDING"
    assert response.json()["data"]["retry_count"] == 1
    assert response.json()["data"]["error_message"] is None
    again = client.post(f"/api/repositories/{repository.id}/indexes/{failed.id}/retry")
    assert again.status_code == 409
    missing = client.post(f"/api/repositories/{repository.id}/indexes/idx_missing/retry")
    assert missing.status_code == 404


# ---------- 任务级重试分流 ----------

def _fail_task_with_index(task: main.TaskRecord, reason: str = "Gitee 快照同步超时"):
    main.mark_task_failed(task, node="代码索引", reason=reason)


def test_task_retry_reuse_after_index_fixed(client):
    task = seed_task()
    _fail_task_with_index(task)
    make_index(HEAD_A)
    response = client.post(f"/api/tasks/{task.id}/retry")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "CONTEXT_INDEXED"
    assert data["retry_count"] == 1
    assert any(event["event_type"] == "TASK_RETRIED" for event in data["events"])


def test_task_retry_stays_failed_when_index_still_failed(client):
    index = make_index(HEAD_A, status="FAILED")
    task = seed_task()
    _fail_task_with_index(task, reason=f"仓库索引处于失败状态：{index.error_message}")
    response = client.post(f"/api/tasks/{task.id}/retry")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "FAILED"
    assert data["failure_node"] == "代码索引"
    assert data["retry_count"] == 1
    assert "重试索引" in response.json()["message"]


def test_task_retry_head_fetch_failure_marks_failed(client, monkeypatch):
    task = seed_task()
    _fail_task_with_index(task)

    def failing_head(repo, base_branch=None):
        raise main.HTTPException(status_code=502, detail={"code": 502, "message": "Gitee 接口请求失败：HTTP 502", "data": None})

    monkeypatch.setattr(main, "fetch_gitee_branch_head", failing_head)
    response = client.post(f"/api/tasks/{task.id}/retry")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "FAILED"
    assert data["failure_node"] == "代码索引"
    assert "HTTP 502" in (data["failure_reason"] or "")


def test_task_retry_waits_when_index_missing(client):
    task = seed_task()
    _fail_task_with_index(task)
    response = client.post(f"/api/tasks/{task.id}/retry")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "ISSUE_FETCHED"
    assert data["next_action"] == "wait_index"
    records = main.index_records_of(repository.id)
    assert records and records[0].status == "PENDING"


# ---------- 检索：字段 / 过滤 / 空结果 / fresh 门禁 / 手动指定 ----------

def test_contexts_endpoint_fields_top_k_and_trace(client):
    make_index(HEAD_A)
    task = seed_task()
    response = client.get(f"/api/tasks/{task.id}/contexts")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["index_id"] == main.latest_completed_index(repository.id).id
    assert data["fresh"] is True
    assert data["empty_reason"] is None
    assert 0 < len(data["contexts"]) <= main.RETRIEVAL_TOP_K_DEFAULT
    for context in data["contexts"]:
        assert context["chunk_id"] in main.CODE_CHUNKS
        assert context["file_path"]
        assert context["start_line"] >= 1
        assert context["end_line"] >= context["start_line"]
        assert context["language"] in {"python", "markdown"}
        assert context["content"]
        assert context["score"] >= main.MIN_CONTEXT_SCORE
        assert context["commit_sha"] == HEAD_A
        assert context["fresh"] is True
        assert context["source"] in {"milvus", "manual", "fallback"}  # 诚实标注：milvus=Milvus；fallback=显式降级；manual=人工指定
    records = main.RETRIEVAL_RECORDS
    assert len(records) == 1
    record = list(records.values())[0]
    assert record.task_id == task.id
    assert record.repository_id == repository.id
    assert record.project_id == repository.project_id
    assert record.index_id == data["index_id"]
    assert record.mode == "auto"
    assert all(chunk_id in main.CODE_CHUNKS for chunk_id in record.result_chunk_ids)


def test_contexts_top_k_upper_bound_and_filtering(client):
    index_a = make_index(HEAD_A)
    make_index(HEAD_B, status="COMPLETED")
    task = seed_task(base_commit=HEAD_A, related_index_id=index_a.id)
    response = client.get(f"/api/tasks/{task.id}/contexts", params={"top_k": 8})
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["contexts"]) <= 8
    assert all(context["commit_sha"] == HEAD_A for context in data["contexts"])


def test_contexts_stale_index_shows_fresh_false(client, monkeypatch):
    index_b = make_index(HEAD_B, status="COMPLETED")
    task = seed_task(base_commit=HEAD_A)
    response = client.get(f"/api/tasks/{task.id}/contexts")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["index_id"] == index_b.id
    assert data["fresh"] is False
    assert all(context["fresh"] is False for context in data["contexts"])


def test_contexts_empty_no_chunks_reason(client):
    make_index(HEAD_A, status="COMPLETED")
    index = main.latest_completed_index(repository.id)
    for chunk in [item for item in main.CODE_CHUNKS.values() if item.index_id == index.id]:
        main.CODE_CHUNKS.pop(chunk.chunk_id, None)
    task = seed_task()
    response = client.get(f"/api/tasks/{task.id}/contexts")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["contexts"] == []
    assert "没有任何代码切片" in (data["empty_reason"] or "")


def test_contexts_low_score_returns_empty_reason(client, monkeypatch):
    def only_readme(repo, commit_sha):
        return files_of("README.md")

    monkeypatch.setattr(main, "sync_repository_snapshot", only_readme)
    make_index(HEAD_A, status="COMPLETED")
    task = seed_task()
    response = client.get(f"/api/tasks/{task.id}/contexts")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["contexts"] == []
    assert "相似度阈值" in (data["empty_reason"] or "")


def test_contexts_no_index_and_running_states(client, monkeypatch):
    task = seed_task(issue_id="ISTUB321")
    response = client.get(f"/api/tasks/{task.id}/contexts")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["contexts"] == []
    assert data["index_id"] is None
    assert "尚无可用索引" in (data["empty_reason"] or "")
    task2 = seed_task(issue_id="ISTUB333")
    running = make_index(HEAD_A, status="PENDING")
    task2.related_index_id = running.id
    monkeypatch.setattr(main, "INDEX_STAGE_SECONDS", 600)
    response2 = client.get(f"/api/tasks/{task2.id}/contexts")
    assert response2.status_code == 200
    assert response2.json()["data"]["empty_reason"].startswith("仓库索引正在处理")


def test_manual_contexts_flow(client):
    index = make_index(HEAD_A)
    task = seed_task(base_commit=HEAD_B)
    response = client.post(
        f"/api/tasks/{task.id}/contexts/manual",
        json={"file_paths": ["services/payment/callback.py", "services/payment/validator.py"]},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["index_id"] == index.id
    assert data["fresh"] is False
    paths = [context["file_path"] for context in data["contexts"]]
    assert "services/payment/callback.py" in paths
    for context in data["contexts"]:
        assert context["source"] == "manual"
        assert context["score"] is None
    record = [item for item in main.RETRIEVAL_RECORDS.values() if item.mode == "manual"]
    assert len(record) == 1
    assert record[0].top_k == 2
    assert any(event.event_type == "MANUAL_CONTEXT_ADDED" for event in task.events)


def test_manual_contexts_validation(client):
    task = seed_task()
    make_index(HEAD_A)
    missing = client.post(
        f"/api/tasks/{task.id}/contexts/manual",
        json={"file_paths": ["services/payment/not_exist.py"]},
    )
    assert missing.status_code == 422
    assert "不在仓库索引中" in missing.json()["detail"]["message"]
    duplicate = client.post(
        f"/api/tasks/{task.id}/contexts/manual",
        json={"file_paths": ["README.md", "README.md"]},
    )
    assert duplicate.status_code == 200
    assert len(duplicate.json()["data"]["contexts"]) == 1
    too_many = client.post(
        f"/api/tasks/{task.id}/contexts/manual",
        json={"file_paths": [f"file{i}.py" for i in range(9)]},
    )
    assert too_many.status_code == 422
    empty = client.post(f"/api/tasks/{task.id}/contexts/manual", json={"file_paths": [""]})
    assert empty.status_code == 422


def test_manual_contexts_requires_completed_index(client):
    task = seed_task(issue_id="ISTUB511")
    make_index(HEAD_A, status="FAILED")
    response = client.post(
        f"/api/tasks/{task.id}/contexts/manual",
        json={"file_paths": ["README.md"]},
    )
    assert response.status_code == 409
    assert "仓库尚无可用索引" in response.json()["detail"]["message"]


def test_fresh_guard_rejects_stale_auto_injection(client):
    index = make_index(HEAD_B)
    task = seed_task(base_commit=HEAD_A)
    with pytest.raises(main.HTTPException) as exc:
        main.run_task_retrieval(task, index, mode="auto", head_commit=HEAD_A, require_fresh=True)
    assert exc.value.status_code == 409
    with pytest.raises(main.HTTPException) as exc2:
        main.assert_fresh_index_for_auto(repository, index)
    assert exc2.value.status_code == 409


# ---------- 权限矩阵与审计 ----------

def test_permissions_matrix(client):
    make_index(HEAD_A)
    task = seed_task()
    approver = approver_client()
    assert approver.post(f"/api/repositories/{repository.id}/index", json={}).status_code == 403
    assert approver.get(f"/api/repositories/{repository.id}/indexes").status_code == 200
    assert approver.get(f"/api/tasks/{task.id}/contexts").status_code == 200
    assert approver.post(f"/api/tasks/{task.id}/contexts/manual", json={"file_paths": ["README.md"]}).status_code == 403
    assert client.post(f"/api/repositories/{repository.id}/index", json={}).status_code == 200


def test_index_audit_trail_and_task_reuse_events(client):
    index = make_index(HEAD_A)
    create_task_via_api(client)
    audit = [
        event
        for event in main.AUDIT_EVENTS.values()
        if event.resource_type == "repository_index" and event.resource_id == index.id
    ]
    assert [event.event_type for event in audit] == ["INDEX_COMPLETED"]
    task = list(main.TASKS.values())[0]
    event_types = [event.event_type for event in task.events]
    # DEV-017：ISSUE_FETCHED 后补 COMMIT_SELECTED，CONTEXT_INDEXED 后补历史相似任务检索事件
    # DEV-018b：任务经 /api/tasks 创建，未直接指定命令 → 记录测试命令来源（仓库默认）
    assert event_types == [
        "TASK_CREATED",
        "ISSUE_FETCHED",
        "COMMIT_SELECTED",
        "INDEX_REUSE_CHECKED",
        "CONTEXT_INDEXED",
        "HISTORICAL_TASKS_MATCHED",
        "TEST_COMMAND_SOURCE",
    ]
