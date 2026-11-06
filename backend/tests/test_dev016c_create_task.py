"""DEV-016-C 从已同步平台需求创建/复用 Agent 任务测试套件。

覆盖（团队 PRD §7.3 验收标准 + 用户点名 8 类场景）：
1. 已同步 request 一键创建任务成功，回写 task_id / TASK_CREATED，任务携带平台需求来源字段；
2. 重复创建返回已有任务与 jump_url，不产生第二个未结束任务；
3. 已结束任务允许新建版本（version 递增）；
4. 仓库开关开启时 sync-gitee 成功后自动创建任务；开关默认关闭；
5. 自动创建失败不回滚同步（保持 SYNCED）并写失败审计；
6. 空 Issue / Issue 拉取失败时任务仍创建并进入 manual_required=context_missing；
7. 并发创建同一 request 不产生重复未结束任务；
8. 权限校验（观察者 403、开发者只能操作本人需求、DRAFT 不允许创建）；
9. 创建/复用/自动创建审计事件写入且可经回放接口查看；
10. 旧 /api/tasks 先建任务时 create-task 同样走复用。
"""
import threading
import time
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main

REPOSITORY = main.REPOSITORIES["repo_payment_service"]

LONG_BODY = (
    "支付回调在网络抖动重试时可能重复入账：同一 out_trade_no 收到两次回调，"
    "当前实现没有幂等保护，期望重复回调只入账一次，并记录第二次为重复请求，复现概率约 80%。"
)

CREATE_PAYLOAD = {
    "project_id": REPOSITORY.project_id,
    "repository_id": REPOSITORY.id,
    "type": "bug",
    "title": "支付回调重复入账",
    "body": LONG_BODY,
    "reproduction_steps": "1. 模拟两次相同回调 2. 观察账户流水",
    "acceptance_criteria": ["同一单号只入账一次", "重复请求留痕"],
    "priority": "high",
    "labels": ["bug", "payment"],
    "target_branch": "main",
    "test_command": "pytest tests/ -q",
}

FAKE_ISSUE = {
    "id": 2002,
    "number": "IKC1601",
    "html_url": "https://gitee.com/example/payment-service/issues/IKC1601",
}

HEAD_COMMIT = "c0ffee11deadbeef22c0ffee33deadbeef44556677"


def authed_client(account: str, password: str) -> TestClient:
    test_client = TestClient(main.app)
    login = test_client.post("/api/auth/login", json={"account": account, "password": password})
    assert login.status_code == 200
    return test_client


@pytest.fixture()
def developer_client() -> TestClient:
    return authed_client("developer@company.com", "Dev123!")


@pytest.fixture()
def observer_client() -> TestClient:
    return authed_client("observer@company.com", "Observer123!")


@pytest.fixture()
def second_developer():
    extra = main.UserRecord(
        id="user_developer2",
        name="李冲",
        email="developer2@company.com",
        role="developer",
        password_hash=main.hash_password("Dev123!"),
    )
    main.USERS[extra.id] = extra
    main.USERS_BY_EMAIL[extra.email] = extra
    yield extra
    main.USERS.pop(extra.id, None)
    main.USERS_BY_EMAIL.pop(extra.email, None)


def patch_gitee_layer(monkeypatch, *, issue_delay: float = 0.0, head_delay: float = 0.0, snapshot_body: str = "Issue 正文足够长，描述了回调幂等缺陷与期望行为。"):
    """桩化 Gitee 适配层：创建 Issue、Issue 快照、分支 head（任务创建必经三处网络调用）。"""

    def fake_post(path, token, data):
        return dict(FAKE_ISSUE)

    def fake_snapshot(repository, issue_id: str):
        if issue_delay:
            time.sleep(issue_delay)
        return main.IssueSnapshotRecord(
            title=f"修复 {issue_id}",
            body=snapshot_body,
            comments=[],
            labels=["bug"],
            raw_response={},
            fetched_at=datetime.now(timezone.utc),
        )

    def fake_head(repository, base_branch: str | None = None):
        if head_delay:
            time.sleep(head_delay)
        return HEAD_COMMIT

    monkeypatch.setattr(main, "gitee_api_post", fake_post)
    monkeypatch.setattr(main, "fetch_gitee_issue_snapshot", fake_snapshot)
    monkeypatch.setattr(main, "fetch_gitee_branch_head", fake_head)
    return fake_snapshot


def create_request(client: TestClient) -> dict:
    response = client.post("/api/team/requests", json=CREATE_PAYLOAD)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def sync_request(client: TestClient, record_id: str) -> dict:
    response = client.post(f"/api/team/requests/{record_id}/sync-gitee")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def create_task_from_request(client: TestClient, record_id: str):
    return client.post(f"/api/team/requests/{record_id}/create-task")


def request_events(client: TestClient, record_id: str) -> list[dict]:
    response = client.get(f"/api/team/requests/{record_id}/events")
    assert response.status_code == 200, response.text
    return response.json()["data"]["events"]


def set_auto_create(monkeypatch, enabled: bool):
    monkeypatch.setattr(REPOSITORY, "auto_create_task_after_sync", enabled)


# ---------- 1. 已同步 request 一键创建任务成功 ----------

def test_synced_request_create_task_success(developer_client, monkeypatch):
    patch_gitee_layer(monkeypatch)
    record = create_request(developer_client)
    synced = sync_request(developer_client, record["id"])
    assert synced["sync_status"] == "SYNCED"

    response = create_task_from_request(developer_client, record["id"])
    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]
    assert data["created"] is True
    assert data["reused"] is False

    task = data["task"]
    assert task["issue_id"] == FAKE_ISSUE["number"]
    assert task["team_request_id"] == record["id"]
    assert task["team_request_no"] == record["request_no"]
    assert task["gitee_issue_url"] == FAKE_ISSUE["html_url"]
    assert task["base_commit_sha"] == HEAD_COMMIT
    assert task["version"] == 1
    assert data["jump_url"] == f"/tasks/{task['id']}"

    refreshed = main.TEAM_REQUESTS[record["id"]]
    assert refreshed.sync_status == "TASK_CREATED"
    assert refreshed.task_id == task["id"]


def test_task_created_event_summary_carries_request_no(developer_client, monkeypatch):
    patch_gitee_layer(monkeypatch)
    record = create_request(developer_client)
    sync_request(developer_client, record["id"])
    response = create_task_from_request(developer_client, record["id"])
    task = response.json()["data"]["task"]
    created_event = next(event for event in task["events"] if event["event_type"] == "TASK_CREATED")
    assert record["request_no"] in created_event["summary"]
    assert any(record["request_no"] in line for line in created_event["diff_summary"])


# ---------- 2. 重复创建返回已有任务与跳转 ----------

def test_duplicate_create_returns_existing_task(developer_client, monkeypatch):
    patch_gitee_layer(monkeypatch)
    record = create_request(developer_client)
    sync_request(developer_client, record["id"])

    first = create_task_from_request(developer_client, record["id"])
    assert first.status_code == 200
    first_task_id = first.json()["data"]["task"]["id"]

    second = create_task_from_request(developer_client, record["id"])
    assert second.status_code == 200, second.text
    data = second.json()["data"]
    assert data["created"] is False
    assert data["reused"] is True
    assert data["task"]["id"] == first_task_id
    assert data["jump_url"] == f"/tasks/{first_task_id}"

    tasks_for_issue = [task for task in main.TASKS.values() if task.issue_id == FAKE_ISSUE["number"]]
    assert len(tasks_for_issue) == 1


# ---------- 3. 已结束任务允许新建版本 ----------

def test_finished_task_allows_new_version(developer_client, monkeypatch):
    patch_gitee_layer(monkeypatch)
    record = create_request(developer_client)
    sync_request(developer_client, record["id"])

    first = create_task_from_request(developer_client, record["id"])
    first_task_id = first.json()["data"]["task"]["id"]
    main.TASKS[first_task_id].status = "COMPLETED"

    second = create_task_from_request(developer_client, record["id"])
    assert second.status_code == 200, second.text
    data = second.json()["data"]
    assert data["created"] is True
    assert data["task"]["version"] == 2
    assert data["task"]["id"] != first_task_id

    versions = sorted(task.version for task in main.TASKS.values() if task.issue_id == FAKE_ISSUE["number"])
    assert versions == [1, 2]


# ---------- 4. 开关开启时同步成功后自动创建 ----------

def test_auto_create_triggered_after_sync_when_enabled(developer_client, monkeypatch):
    patch_gitee_layer(monkeypatch)
    set_auto_create(monkeypatch, True)
    record = create_request(developer_client)

    synced = sync_request(developer_client, record["id"])
    assert synced["sync_status"] == "TASK_CREATED"
    assert synced["task_id"]
    tasks_for_issue = [task for task in main.TASKS.values() if task.issue_id == FAKE_ISSUE["number"]]
    assert len(tasks_for_issue) == 1
    assert tasks_for_issue[0].team_request_id == record["id"]

    events = request_events(developer_client, record["id"])
    event_types = [event["event_type"] for event in events]
    assert event_types.count("TEAM_REQUEST_TASK_AUTO_CREATED") == 1
    assert "TEAM_REQUEST_TASK_CREATED" not in event_types


def test_auto_create_disabled_by_default_keeps_synced(developer_client, monkeypatch):
    patch_gitee_layer(monkeypatch)
    assert REPOSITORY.auto_create_task_after_sync is False
    record = create_request(developer_client)

    synced = sync_request(developer_client, record["id"])
    assert synced["sync_status"] == "SYNCED"
    assert not synced["task_id"]
    assert main.TASKS == {} or all(task.issue_id != FAKE_ISSUE["number"] for task in main.TASKS.values())


def test_auto_create_failure_keeps_synced_and_audits(developer_client, monkeypatch):
    patch_gitee_layer(monkeypatch)
    set_auto_create(monkeypatch, True)

    def broken_head(repository, base_branch: str | None = None):
        raise HTTPException(status_code=502, detail={"code": 502, "message": "Gitee 分支信息拉取失败", "data": None})

    monkeypatch.setattr(main, "fetch_gitee_branch_head", broken_head)
    record = create_request(developer_client)

    synced = sync_request(developer_client, record["id"])
    assert synced["sync_status"] == "SYNCED", "自动创建失败不得回滚 Gitee 同步结果"
    assert not synced["task_id"]
    assert all(task.issue_id != FAKE_ISSUE["number"] for task in main.TASKS.values())

    events = request_events(developer_client, record["id"])
    failed_events = [event for event in events if event["event_type"] == "TEAM_REQUEST_TASK_AUTO_CREATE_FAILED"]
    assert len(failed_events) == 1
    assert "手动创建" in failed_events[0]["diff_summary"][1]

    # 修复后仍可手动创建
    monkeypatch.setattr(main, "fetch_gitee_branch_head", lambda repository, base_branch=None: HEAD_COMMIT)
    manual = create_task_from_request(developer_client, record["id"])
    assert manual.status_code == 200, manual.text
    assert manual.json()["data"]["created"] is True


# ---------- 5. 空 Issue / 上下文不足走人工补充 ----------

def test_issue_fetch_failure_creates_task_into_context_missing(developer_client, monkeypatch):
    patch_gitee_layer(monkeypatch)

    def broken_snapshot(repository, issue_id: str):
        raise HTTPException(status_code=502, detail={"code": 502, "message": "Gitee Issue 不存在或无权限", "data": None})

    monkeypatch.setattr(main, "fetch_gitee_issue_snapshot", broken_snapshot)
    record = create_request(developer_client)
    sync_request(developer_client, record["id"])

    response = create_task_from_request(developer_client, record["id"])
    assert response.status_code == 200, response.text
    task = response.json()["data"]["task"]
    assert task["manual_required"] is True
    assert task["manual_reason"] == "context_missing"
    # 兜底快照来自平台需求内容，不允许空任务
    assert task["issue_snapshot"]["raw_response"]["fallback"] is True
    assert CREATE_PAYLOAD["title"] in task["issue_snapshot"]["title"]


def test_empty_issue_body_creates_task_into_context_missing(developer_client, monkeypatch):
    patch_gitee_layer(monkeypatch, snapshot_body="")
    record = create_request(developer_client)
    sync_request(developer_client, record["id"])

    response = create_task_from_request(developer_client, record["id"])
    task = response.json()["data"]["task"]
    assert task["manual_required"] is True
    assert task["manual_reason"] == "context_missing"
    assert task["issue_snapshot"]["raw_response"]["fallback"] is True


# ---------- 6. 并发创建同一 request 不重复 ----------

def test_concurrent_create_same_request_single_task(developer_client, monkeypatch):
    # 分布式锁非阻塞语义：并发同时提交时只允许一个进入创建，另一个收到 409 锁竞争提示；
    # 竞争方稍后重试应复用已创建任务（reused），最终全局只有一个未结束任务。
    patch_gitee_layer(monkeypatch, issue_delay=0.25)
    record = create_request(developer_client)
    sync_request(developer_client, record["id"])
    barrier = threading.Barrier(2)

    def do_create():
        client = authed_client("developer@company.com", "Dev123!")
        barrier.wait(timeout=10)
        return client.post(f"/api/team/requests/{record['id']}/create-task")

    results: list = [None, None]
    errors: list = []

    def run(index: int):
        try:
            results[index] = do_create()
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors, errors

    statuses = sorted(response.status_code for response in results)
    assert statuses == [200, 409]
    ok_response = next(response for response in results if response.status_code == 200)
    conflict_response = next(response for response in results if response.status_code == 409)
    assert ok_response.json()["data"]["created"] is True
    assert "正在创建 Agent 任务" in conflict_response.json()["detail"]["message"]

    # 锁竞争方重试：首个请求已完成，应复用已有任务而不是新建
    retry = create_task_from_request(developer_client, record["id"])
    assert retry.status_code == 200
    retry_payload = retry.json()["data"]
    assert retry_payload["created"] is False
    assert retry_payload["reused"] is True
    assert retry_payload["task"]["id"] == ok_response.json()["data"]["task"]["id"]

    tasks_for_issue = [task for task in main.TASKS.values() if task.issue_id == FAKE_ISSUE["number"]]
    assert len(tasks_for_issue) == 1


class _FakeRedis:
    """最小 redis-py 语义：SET NX PX + Lua CAS 释放，单进程内模拟跨 Worker 互斥。"""

    def __init__(self):
        self.store: dict[str, tuple[str, float | None]] = {}
        self._guard = threading.Lock()

    def set(self, key, value, nx=False, px=None, **kwargs):
        with self._guard:
            if nx and key in self.store:
                return None
            self.store[key] = (value, None)
            return True

    def get(self, key):
        return self.store.get(key, (None,))[0]

    def delete(self, key):
        with self._guard:
            return 1 if self.store.pop(key, None) is not None else 0

    def register_script(self, _lua):
        def script(keys, args):
            if self.get(keys[0]) == args[0]:
                return self.delete(keys[0])
            return 0

        return script


def test_concurrent_create_single_task_under_redis_lock(developer_client, monkeypatch):
    import redis as redis_package
    from app.core import distributed_lock

    fake = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(redis_package.Redis, "from_url", classmethod(lambda cls, *a, **k: fake))
    distributed_lock.set_lock_backend(None)
    assert isinstance(distributed_lock.get_lock_backend(), distributed_lock.RedisLockBackend)

    patch_gitee_layer(monkeypatch, issue_delay=0.25)
    record = create_request(developer_client)
    sync_request(developer_client, record["id"])
    barrier = threading.Barrier(2)
    results: list = [None, None]

    def do_create(index: int):
        client = authed_client("developer@company.com", "Dev123!")
        barrier.wait(timeout=10)
        results[index] = client.post(f"/api/team/requests/{record['id']}/create-task")

    threads = [threading.Thread(target=do_create, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(response.status_code for response in results) == [200, 409]
    tasks_for_issue = [task for task in main.TASKS.values() if task.issue_id == FAKE_ISSUE["number"]]
    assert len(tasks_for_issue) == 1

    # 锁竞争方重试：复用已创建任务，仍不产生第二个
    retry = create_task_from_request(developer_client, record["id"])
    assert retry.status_code == 200
    assert retry.json()["data"]["reused"] is True
    assert len([task for task in main.TASKS.values() if task.issue_id == FAKE_ISSUE["number"]]) == 1


# ---------- 7. 权限与状态门禁 ----------

def test_observer_cannot_create_task(observer_client, developer_client, monkeypatch):
    patch_gitee_layer(monkeypatch)
    record = create_request(developer_client)
    sync_request(developer_client, record["id"])

    response = create_task_from_request(observer_client, record["id"])
    assert response.status_code == 403
    assert all(task.issue_id != FAKE_ISSUE["number"] for task in main.TASKS.values())


def test_developer_cannot_create_task_for_others_request(developer_client, second_developer, monkeypatch):
    patch_gitee_layer(monkeypatch)
    owner_client = authed_client("developer2@company.com", "Dev123!")
    record = create_request(owner_client)
    sync_request(owner_client, record["id"])

    other_client = authed_client("developer@company.com", "Dev123!")
    response = create_task_from_request(other_client, record["id"])
    assert response.status_code == 403
    assert "本人" in response.json()["detail"]["message"]


def test_draft_request_cannot_create_task(developer_client, monkeypatch):
    patch_gitee_layer(monkeypatch)
    record = create_request(developer_client)
    response = create_task_from_request(developer_client, record["id"])
    assert response.status_code == 409
    assert "先完成 Gitee Issue 同步" in response.json()["detail"]["message"]


def test_lead_can_create_task_for_any_request(developer_client, monkeypatch):
    patch_gitee_layer(monkeypatch)
    record = create_request(developer_client)
    sync_request(developer_client, record["id"])

    lead_client = authed_client("lead@company.com", "Lead123!")
    response = create_task_from_request(lead_client, record["id"])
    assert response.status_code == 200, response.text
    assert response.json()["data"]["created"] is True


# ---------- 8. 审计事件与回放 ----------

def test_create_and_reuse_audit_events_visible_in_replay(developer_client, monkeypatch):
    patch_gitee_layer(monkeypatch)
    record = create_request(developer_client)
    sync_request(developer_client, record["id"])
    create_task_from_request(developer_client, record["id"])
    create_task_from_request(developer_client, record["id"])

    events = request_events(developer_client, record["id"])
    event_types = [event["event_type"] for event in events]
    assert "GITEE_ISSUE_SYNCED" in event_types
    assert event_types.count("TEAM_REQUEST_TASK_CREATED") == 1
    assert event_types.count("TEAM_REQUEST_TASK_REUSED") == 1
    # 回放事件一律绑定 team_request 资源，且不泄露 Token
    for event in events:
        assert event["resource_type"] == "team_request"
        assert event["resource_id"] == record["id"]
        assert "demo-token-not-returned" not in event["summary"]
        assert "demo-token-not-returned" not in "".join(event["diff_summary"])


def test_token_never_leaked_in_create_task_response(developer_client, monkeypatch):
    patch_gitee_layer(monkeypatch)
    record = create_request(developer_client)
    sync_request(developer_client, record["id"])
    response = create_task_from_request(developer_client, record["id"])
    assert "demo-token-not-returned" not in response.text


# ---------- 9. 旧 /api/tasks 先建任务时 create-task 走复用 ----------

def test_legacy_task_first_create_task_reuses(developer_client, monkeypatch):
    patch_gitee_layer(monkeypatch)
    record = create_request(developer_client)
    sync_request(developer_client, record["id"])

    legacy = developer_client.post(
        "/api/tasks",
        json={"project_id": REPOSITORY.project_id, "repository_id": REPOSITORY.id, "issue_id": FAKE_ISSUE["number"]},
    )
    assert legacy.status_code == 200, legacy.text
    legacy_task_id = legacy.json()["data"]["id"]

    response = create_task_from_request(developer_client, record["id"])
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["reused"] is True
    assert data["task"]["id"] == legacy_task_id
    assert main.TEAM_REQUESTS[record["id"]].task_id == legacy_task_id
    assert main.TEAM_REQUESTS[record["id"]].sync_status == "TASK_CREATED"


# ---------- 10. 仓库自动创建开关可经配置保存 ----------

def test_repository_config_persists_auto_create_flag(developer_client, monkeypatch):
    admin_client = authed_client("admin@company.com", "Admin123!")
    payload = {
        "project_id": REPOSITORY.project_id,
        "name": REPOSITORY.name,
        "url": REPOSITORY.url,
        "default_branch": REPOSITORY.default_branch,
        "test_command": REPOSITORY.test_command,
        "command_whitelist": list(REPOSITORY.command_whitelist),
        "approval_owner_id": REPOSITORY.approval_owner_id,
        "approval_mode": REPOSITORY.approval_mode,
        "risk_threshold": REPOSITORY.risk_threshold,
        "max_files": REPOSITORY.max_files,
        "max_retries": REPOSITORY.max_retries,
        "blocked_paths": list(REPOSITORY.blocked_paths),
        "auto_create_task_after_sync": True,
    }
    response = admin_client.put(f"/api/repositories/{REPOSITORY.id}", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["auto_create_task_after_sync"] is True
    # 还原，避免污染其他用例
    monkeypatch.setattr(REPOSITORY, "auto_create_task_after_sync", False)
