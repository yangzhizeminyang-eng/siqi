"""DEV-016-B 平台需求同步创建 Gitee Issue 测试套件。

覆盖：DRAFT 同步成功并保存三个 Gitee 映射字段、SYNCED 重复请求不再次创建、
SYNCING 并发请求不重复创建、Token 过期/字段错误进入 SYNC_FAILED、
失败后可重试、同步失败不创建 Agent Task、成功/失败/重试审计事件、
Token 不出现在响应与审计内容、网络超时按来源标识查重复用、
回放接口返回同步事件、正文不足 422、观察者/他人草稿同步 403。
"""
import json
import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main

REPOSITORY = main.REPOSITORIES["repo_payment_service"]

LONG_BODY = (
    "登录页在刷新后会误跳转到登录页：用户已登录状态下刷新 /dashboard，"
    "页面先渲染工作台内容再跳回 /login，期望刷新后保持当前页面，"
    "仅当登录态失效时才跳转登录，复现概率约 100%。"
)

CREATE_PAYLOAD = {
    "project_id": REPOSITORY.project_id,
    "repository_id": REPOSITORY.id,
    "type": "bug",
    "title": "登录页刷新后误跳转",
    "body": LONG_BODY,
    "reproduction_steps": "1. 登录进入工作台 2. 按 F5 刷新 3. 观察跳转",
    "acceptance_criteria": ["刷新后保持当前页面", "登录态失效时才跳转登录"],
    "priority": "high",
    "labels": ["bug", "frontend"],
    "target_branch": "main",
    "test_command": "pytest tests/ -q",
}

FAKE_ISSUE = {
    "id": 1001,
    "number": "IKD5H1",
    "html_url": "https://gitee.com/example/payment-service/issues/IKD5H1",
}


def authed_client(account: str, password: str) -> TestClient:
    test_client = TestClient(main.app)
    login = test_client.post(
        "/api/auth/login",
        json={"account": account, "password": password},
    )
    assert login.status_code == 200
    return test_client


@pytest.fixture()
def developer_client() -> TestClient:
    return authed_client("developer@company.com", "Dev123!")


@pytest.fixture()
def observer_client() -> TestClient:
    return authed_client("observer@company.com", "Observer123!")


@pytest.fixture()
def second_developer_client() -> TestClient:
    extra = main.UserRecord(
        id="user_developer2",
        name="李冲",
        email="developer2@company.com",
        role="developer",
        password_hash=main.hash_password("Dev123!"),
    )
    main.USERS[extra.id] = extra
    main.USERS_BY_EMAIL[extra.email] = extra
    client = authed_client(extra.email, "Dev123!")
    yield client
    main.USERS.pop(extra.id, None)
    main.USERS_BY_EMAIL.pop(extra.email, None)


def create_request(client: TestClient, **overrides) -> dict:
    payload = {**CREATE_PAYLOAD, **overrides}
    response = client.post("/api/team/requests", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def fake_create_issue(path: str, token: str | None, data: dict) -> dict:
    return dict(FAKE_ISSUE)


def auth_expired_post(path: str, token: str | None, data: dict) -> dict:
    raise HTTPException(
        status_code=401,
        detail={
            "code": 401,
            "message": "Gitee Token 已过期或无权限，请重新授权后重试",
            "error_type": "auth_expired",
            "data": None,
        },
    )


def field_error_post(path: str, token: str | None, data: dict) -> dict:
    raise HTTPException(
        status_code=422,
        detail={
            "code": 422,
            "message": "Gitee Issue 创建失败：标题不能为空",
            "error_type": "field_error",
            "data": None,
        },
    )


def network_timeout_post(path: str, token: str | None, data: dict) -> dict:
    raise HTTPException(
        status_code=504,
        detail={"code": 504, "message": "Gitee 请求超时，请稍后重试", "error_type": "network_timeout", "data": None},
    )


# ---- 1/2. DRAFT 同步成功并保存三个映射字段 ----

def test_draft_sync_success_saves_mapping_fields(developer_client, monkeypatch):
    monkeypatch.setattr(main, "gitee_api_post", fake_create_issue)
    record = create_request(developer_client)

    response = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["sync_status"] == "SYNCED"
    assert data["gitee_issue_id"] == "1001"
    assert data["gitee_issue_number"] == "IKD5H1"
    assert data["gitee_issue_url"] == "https://gitee.com/example/payment-service/issues/IKD5H1"
    assert data["failure_reason"] is None
    assert data["manual_reason"] is None


def test_sync_payload_contains_source_marker(developer_client, monkeypatch):
    captured: dict = {}

    def capture_post(path, token, data):
        captured.update(data)
        return dict(FAKE_ISSUE)

    monkeypatch.setattr(main, "gitee_api_post", capture_post)
    record = create_request(developer_client)
    response = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert response.status_code == 200, response.text

    assert captured["title"] == CREATE_PAYLOAD["title"]
    assert "### 复现步骤" in captured["body"]
    assert "### 验收标准" in captured["body"]
    assert f"来源：平台需求 {record['request_no']}" in captured["body"]
    assert captured["labels"] == "bug,frontend"


# ---- 3. SYNCED 重复请求不再次创建 ----

def test_synced_repeat_request_does_not_recreate(developer_client, monkeypatch):
    calls: list[str] = []

    def counting_post(path, token, data):
        calls.append(path)
        return dict(FAKE_ISSUE)

    monkeypatch.setattr(main, "gitee_api_post", counting_post)
    record = create_request(developer_client)

    first = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert first.status_code == 200
    first_data = first.json()["data"]
    assert first_data["sync_status"] == "SYNCED"

    second = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert second.status_code == 200
    second_data = second.json()["data"]
    assert second_data["sync_status"] == "SYNCED"
    assert second_data["gitee_issue_id"] == first_data["gitee_issue_id"]
    assert second_data["gitee_issue_url"] == first_data["gitee_issue_url"]

    assert len(calls) == 1, "SYNCED 重复请求不应再次调用 Gitee 创建接口"


# ---- 4. SYNCING 并发请求不重复创建 ----

def test_syncing_concurrent_requests_do_not_recreate(developer_client, monkeypatch):
    calls: list[str] = []
    calls_lock = threading.Lock()

    def slow_post(path, token, data):
        time.sleep(0.2)
        with calls_lock:
            calls.append(path)
        return dict(FAKE_ISSUE)

    monkeypatch.setattr(main, "gitee_api_post", slow_post)
    record = create_request(developer_client)

    results: list[int] = []

    def do_sync():
        client = authed_client("developer@company.com", "Dev123!")
        response = client.post(f"/api/team/requests/{record['id']}/sync-gitee")
        results.append(response.status_code)

    threads = [threading.Thread(target=do_sync) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 1, "并发同步只能调用一次 Gitee 创建接口"
    assert all(code in (200, 409) for code in results), f"并发同步不应出现 5xx：{results}"
    assert 200 in results

    detail = authed_client("developer@company.com", "Dev123!")
    final = detail.get(f"/api/team/requests/{record['id']}")
    assert final.json()["data"]["sync_status"] == "SYNCED"


# ---- 4b. Redis 分布式锁后端下并发同步只成功一次（DEV-016 生产前修复）----

class _FakeRedis:
    """最小 redis-py 语义：SET NX PX + Lua CAS 释放，用于在单进程内模拟跨 Worker 互斥。"""

    def __init__(self):
        import time as _time

        self._time = _time
        self.store: dict[str, tuple[str, float | None]] = {}
        self._guard = threading.Lock()

    def set(self, key, value, nx=False, px=None, **kwargs):
        with self._guard:
            now = self._time.monotonic()
            if nx and key in self.store:
                return None
            expire_at = now + px / 1000 if px else None
            self.store[key] = (value, expire_at)
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


def _install_fake_redis_backend(monkeypatch):
    import redis as redis_package
    from app.core import distributed_lock

    fake = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(redis_package.Redis, "from_url", classmethod(lambda cls, *a, **k: fake))
    distributed_lock.set_lock_backend(None)
    return distributed_lock.get_lock_backend()


def test_concurrent_sync_single_success_under_redis_lock(developer_client, monkeypatch):
    from app.core import distributed_lock

    backend = _install_fake_redis_backend(monkeypatch)
    assert isinstance(backend, distributed_lock.RedisLockBackend)

    calls: list[str] = []
    calls_lock = threading.Lock()

    def slow_post(path, token, data):
        time.sleep(0.2)
        with calls_lock:
            calls.append(path)
        return dict(FAKE_ISSUE)

    monkeypatch.setattr(main, "gitee_api_post", slow_post)
    record = create_request(developer_client)
    results: list[int] = []

    def do_sync():
        client = authed_client("developer@company.com", "Dev123!")
        response = client.post(f"/api/team/requests/{record['id']}/sync-gitee")
        results.append(response.status_code)

    threads = [threading.Thread(target=do_sync) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [200, 409]
    conflict = [code for code in results if code == 409][0]
    assert conflict == 409
    assert len(calls) == 1, "Redis 分布式锁下并发同步只能调用一次 Gitee 创建接口"

    # 竞争方重试：首个已完成，直接返回已同步映射，仍不再次创建
    retry = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert retry.status_code == 200
    assert retry.json()["data"]["sync_status"] == "SYNCED"
    assert len(calls) == 1


# ---- 5. Token 过期进入 SYNC_FAILED ----

def test_token_expired_enters_sync_failed(developer_client, monkeypatch):
    monkeypatch.setattr(main, "gitee_api_post", auth_expired_post)
    record = create_request(developer_client)

    response = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["sync_status"] == "SYNC_FAILED"
    assert data["manual_reason"] == "auth_expired"
    assert "过期或无权限" in (data["failure_reason"] or "")
    assert data["gitee_issue_id"] is None
    assert data["gitee_issue_number"] is None
    assert data["gitee_issue_url"] is None
    # 保留原 REQ-* 草稿
    assert data["request_no"].startswith("REQ-")
    assert data["title"] == CREATE_PAYLOAD["title"]


# ---- 6. 字段错误进入 SYNC_FAILED ----

def test_field_error_enters_sync_failed(developer_client, monkeypatch):
    monkeypatch.setattr(main, "gitee_api_post", field_error_post)
    record = create_request(developer_client)

    response = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["sync_status"] == "SYNC_FAILED"
    assert data["manual_reason"] == "gitee_sync_failed"
    assert "标题不能为空" in (data["failure_reason"] or "")


# ---- 7. 失败后可以重试 ----

def test_failed_then_retry_succeeds(developer_client, monkeypatch):
    monkeypatch.setattr(main, "gitee_api_post", field_error_post)
    record = create_request(developer_client)
    first = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert first.json()["data"]["sync_status"] == "SYNC_FAILED"

    monkeypatch.setattr(main, "gitee_api_post", fake_create_issue)
    retry = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert retry.status_code == 200, retry.text
    data = retry.json()["data"]
    assert data["sync_status"] == "SYNCED"
    assert data["gitee_issue_id"] == "1001"
    assert data["failure_reason"] is None
    assert data["manual_reason"] is None


# ---- 8. 同步失败/成功都不创建 Agent Task ----

def test_sync_failure_does_not_create_agent_task(developer_client, monkeypatch):
    monkeypatch.setattr(main, "gitee_api_post", auth_expired_post)
    record = create_request(developer_client)
    developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert main.TASKS == {}, "同步失败不应创建 Agent Task"

    monkeypatch.setattr(main, "gitee_api_post", fake_create_issue)
    developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert main.TASKS == {}, "DEV-016-B 同步成功也不应创建 Agent Task（属于 DEV-016-C）"


# ---- 9. 成功/失败/重试审计事件 ----

def test_success_writes_synced_event(developer_client, monkeypatch):
    monkeypatch.setattr(main, "gitee_api_post", fake_create_issue)
    record = create_request(developer_client)
    developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")

    events = [event for event in main.AUDIT_EVENTS.values() if event.event_type == "GITEE_ISSUE_SYNCED"]
    assert len(events) == 1
    assert events[0].resource_type == "team_request"
    assert events[0].resource_id == record["id"]
    assert "Gitee Issue" in events[0].diff_summary[0]


def test_failure_writes_failed_event(developer_client, monkeypatch):
    monkeypatch.setattr(main, "gitee_api_post", auth_expired_post)
    record = create_request(developer_client)
    developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")

    events = [event for event in main.AUDIT_EVENTS.values() if event.event_type == "GITEE_ISSUE_SYNC_FAILED"]
    assert len(events) == 1
    assert events[0].resource_type == "team_request"
    assert events[0].resource_id == record["id"]
    assert any("auth_expired" in line for line in events[0].diff_summary)


def test_retry_writes_retried_event(developer_client, monkeypatch):
    monkeypatch.setattr(main, "gitee_api_post", field_error_post)
    record = create_request(developer_client)
    developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")

    monkeypatch.setattr(main, "gitee_api_post", fake_create_issue)
    developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")

    retried = [event for event in main.AUDIT_EVENTS.values() if event.event_type == "GITEE_ISSUE_SYNC_RETRIED"]
    assert len(retried) == 1
    assert retried[0].resource_type == "team_request"
    assert retried[0].resource_id == record["id"]

    synced = [event for event in main.AUDIT_EVENTS.values() if event.event_type == "GITEE_ISSUE_SYNCED"]
    assert len(synced) == 1


def test_first_sync_from_draft_has_no_retried_event(developer_client, monkeypatch):
    monkeypatch.setattr(main, "gitee_api_post", fake_create_issue)
    record = create_request(developer_client)
    developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    retried = [event for event in main.AUDIT_EVENTS.values() if event.event_type == "GITEE_ISSUE_SYNC_RETRIED"]
    assert retried == []


# ---- 10. Token 不出现在响应和审计内容 ----

def test_token_not_exposed_in_response_or_audit(developer_client, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        main,
        "gitee_api_post",
        lambda path, token, data: captured.update(token=token) or dict(FAKE_ISSUE),
    )
    monkeypatch.setattr(main, "REPOSITORY_TOKENS", {"repo_payment_service": "super-secret-token-xyz"})
    record = create_request(developer_client)

    response = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert response.status_code == 200, response.text
    assert captured["token"] == "super-secret-token-xyz", "内部调用应使用仓库 Token"

    assert "super-secret-token-xyz" not in response.text
    for event in main.AUDIT_EVENTS.values():
        assert "super-secret-token-xyz" not in event.summary
        assert "super-secret-token-xyz" not in json.dumps(event.diff_summary, ensure_ascii=False)

    replay = developer_client.get(f"/api/team/requests/{record['id']}/events")
    assert replay.status_code == 200
    assert "super-secret-token-xyz" not in replay.text


# ---- 网络超时：按来源标识查重 ----

def test_network_timeout_reuses_existing_issue_by_marker(developer_client, monkeypatch):
    existing = {
        "id": 9001,
        "number": "IKD9X1",
        "html_url": "https://gitee.com/example/payment-service/issues/IKD9X1",
        "body": f"正文内容\n---\n来源：平台需求 REQ-1001（本平台自动同步，请勿手工编辑）",
    }

    def timeout_then_found(path, token, data):
        raise HTTPException(
            status_code=504,
            detail={"code": 504, "message": "Gitee 请求超时，请稍后重试", "error_type": "network_timeout", "data": None},
        )

    def find_by_marker(repository_id, request_no):
        return dict(existing)

    monkeypatch.setattr(main, "gitee_api_post", timeout_then_found)
    monkeypatch.setattr(main, "find_gitee_issue_by_source_marker", find_by_marker)
    record = create_request(developer_client)

    response = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["sync_status"] == "SYNCED"
    assert data["gitee_issue_id"] == "9001"
    assert data["gitee_issue_number"] == "IKD9X1"
    assert data["gitee_issue_url"] == "https://gitee.com/example/payment-service/issues/IKD9X1"

    synced = [event for event in main.AUDIT_EVENTS.values() if event.event_type == "GITEE_ISSUE_SYNCED"]
    assert len(synced) == 1
    assert any("复用映射" in line for line in synced[0].diff_summary)


def test_network_timeout_without_existing_enters_sync_failed(developer_client, monkeypatch):
    monkeypatch.setattr(main, "gitee_api_post", network_timeout_post)
    monkeypatch.setattr(main, "find_gitee_issue_by_source_marker", lambda repository_id, request_no: None)
    record = create_request(developer_client)

    response = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["sync_status"] == "SYNC_FAILED"
    assert data["manual_reason"] == "gitee_sync_failed"
    assert "超时" in (data["failure_reason"] or "")


# ---- 11. 回放接口 ----

def test_replay_events_endpoint_returns_sync_events(developer_client, monkeypatch):
    monkeypatch.setattr(main, "gitee_api_post", fake_create_issue)
    record = create_request(developer_client)
    developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")

    response = developer_client.get(f"/api/team/requests/{record['id']}/events")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["request_id"] == record["id"]
    assert data["request_no"] == record["request_no"]
    event_types = [event["event_type"] for event in data["events"]]
    assert "TEAM_REQUEST_CREATED" in event_types
    assert "GITEE_ISSUE_SYNCED" in event_types
    assert all(event["resource_type"] == "team_request" for event in data["events"])
    assert all(event["resource_id"] == record["id"] for event in data["events"])


def test_replay_events_after_failure(developer_client, monkeypatch):
    monkeypatch.setattr(main, "gitee_api_post", auth_expired_post)
    record = create_request(developer_client)
    developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")

    response = developer_client.get(f"/api/team/requests/{record['id']}/events")
    assert response.status_code == 200
    event_types = [event["event_type"] for event in response.json()["data"]["events"]]
    assert "GITEE_ISSUE_SYNC_FAILED" in event_types


# ---- 边界：正文不足 / 权限 ----

def test_sync_requires_content_sufficient(developer_client, monkeypatch):
    monkeypatch.setattr(main, "gitee_api_post", fake_create_issue)
    record = create_request(developer_client, body="刷新后误跳转登录页，需要修复。")

    response = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert response.status_code == 422
    assert "正文内容不足" in response.json()["detail"]["message"]
    assert main.TASKS == {}


def test_observer_cannot_sync(observer_client, developer_client, monkeypatch):
    monkeypatch.setattr(main, "gitee_api_post", fake_create_issue)
    record = create_request(developer_client)

    response = observer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert response.status_code == 403
    assert main.TASKS == {}


def test_developer_cannot_sync_others_draft(developer_client, second_developer_client, monkeypatch):
    monkeypatch.setattr(main, "gitee_api_post", fake_create_issue)
    record = create_request(developer_client)

    response = second_developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert response.status_code == 403
    assert "开发者只能同步本人创建的平台需求草稿" in response.json()["detail"]["message"]


def test_developer_cannot_view_others_replay_events(developer_client, second_developer_client, monkeypatch):
    monkeypatch.setattr(main, "gitee_api_post", fake_create_issue)
    record = create_request(developer_client)

    response = second_developer_client.get(f"/api/team/requests/{record['id']}/events")
    assert response.status_code == 403


# ---- 修复项：查重范围优化 / 查重失败错误分类 / 响应字段校验 ----

def test_dedupe_stops_on_empty_page(monkeypatch):
    """查重翻页遇到空列表提前停止，不再请求后续页。"""
    calls: list[str] = []
    monkeypatch.setattr(
        main,
        "gitee_api_get",
        lambda path, token: calls.append(path) or [],
    )
    result = main.find_gitee_issue_by_source_marker(REPOSITORY.id, "REQ-1001")
    assert result is None
    assert len(calls) == 1, "空页应提前停止，只请求第一页"


def test_dedupe_uses_max_pages_constant(monkeypatch):
    """查重页数上限使用 GITEE_ISSUE_DEDUPE_MAX_PAGES，不再固定 3 页，且不无限循环。"""
    calls: list[str] = []
    full_page = [
        {"id": i, "number": f"IK{i}", "body": f"普通 Issue 正文 {i}"}
        for i in range(100)
    ]

    def full_page_get(path, token):
        calls.append(path)
        return [dict(item) for item in full_page]

    monkeypatch.setattr(main, "gitee_api_get", full_page_get)
    result = main.find_gitee_issue_by_source_marker(REPOSITORY.id, "REQ-1001")
    assert result is None
    assert len(calls) == main.GITEE_ISSUE_DEDUPE_MAX_PAGES, (
        f"满页无匹配时最多请求 {main.GITEE_ISSUE_DEDUPE_MAX_PAGES} 页，实际 {len(calls)}"
    )


def test_dedupe_requires_exact_marker_match(monkeypatch):
    """来源标识相似但不精确匹配（REQ 号不同 / 缺少完整标记）时不能误判为同一 Issue。"""
    similar_issues = [
        {"id": 1, "number": "IK1", "body": "正文\n---\n来源：平台需求 REQ-100（本平台自动同步，请勿手工编辑）"},
        {"id": 2, "number": "IK2", "body": "正文\n---\n来源：平台需求 REQ-1001"},
        {"id": 3, "number": "IK3", "body": "正文\n---\n来源：平台需求 REQ-10010（本平台自动同步，请勿手工编辑）"},
    ]
    monkeypatch.setattr(
        main,
        "gitee_api_get",
        lambda path, token: [dict(item) for item in similar_issues],
    )
    result = main.find_gitee_issue_by_source_marker(REPOSITORY.id, "REQ-1001")
    assert result is None, "相似但不精确的来源标识不应命中"


def test_network_timeout_dedupe_token_expired_enters_auth_expired(developer_client, monkeypatch):
    """创建超时后，查重接口返回 Token 过期：进入 SYNC_FAILED + manual_reason=auth_expired。"""
    monkeypatch.setattr(main, "gitee_api_post", network_timeout_post)
    monkeypatch.setattr(
        main,
        "gitee_api_get",
        lambda path, token: (_ for _ in ()).throw(
            HTTPException(
                status_code=502,
                detail={"code": 502, "message": "Gitee Token 已过期或无权限", "error_type": "auth_expired", "data": None},
            )
        ),
    )
    record = create_request(developer_client)

    response = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["sync_status"] == "SYNC_FAILED"
    assert data["manual_reason"] == "auth_expired"
    assert "过期或无权限" in (data["failure_reason"] or "")
    assert data["gitee_issue_id"] is None


def test_create_response_missing_id_enters_sync_failed(developer_client, monkeypatch):
    """创建成功响应缺少 id：进入 SYNC_FAILED，不保存半截映射。"""
    monkeypatch.setattr(
        main,
        "gitee_api_post",
        lambda path, token, data: {"number": "IKD5H1", "html_url": "https://gitee.com/example/payment-service/issues/IKD5H1"},
    )
    record = create_request(developer_client)

    response = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["sync_status"] == "SYNC_FAILED"
    assert data["manual_reason"] == "gitee_sync_failed"
    assert "缺少 Issue 编号" in (data["failure_reason"] or "")
    assert data["gitee_issue_id"] is None
    assert data["gitee_issue_number"] is None
    assert data["gitee_issue_url"] is None


def test_create_response_missing_number_enters_sync_failed(developer_client, monkeypatch):
    """创建成功响应缺少 number：进入 SYNC_FAILED。"""
    monkeypatch.setattr(
        main,
        "gitee_api_post",
        lambda path, token, data: {"id": 1001, "html_url": "https://gitee.com/example/payment-service/issues/IKD5H1"},
    )
    record = create_request(developer_client)

    response = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["sync_status"] == "SYNC_FAILED"
    assert data["manual_reason"] == "gitee_sync_failed"
    assert "缺少 Issue 编号" in (data["failure_reason"] or "")
    assert data["gitee_issue_id"] is None
    assert data["gitee_issue_number"] is None


def test_create_response_missing_urls_enters_sync_failed(developer_client, monkeypatch):
    """创建成功响应同时缺少 html_url 与 url：进入 SYNC_FAILED。"""
    monkeypatch.setattr(
        main,
        "gitee_api_post",
        lambda path, token, data: {"id": 1001, "number": "IKD5H1"},
    )
    record = create_request(developer_client)

    response = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["sync_status"] == "SYNC_FAILED"
    assert data["manual_reason"] == "gitee_sync_failed"
    assert "缺少访问地址" in (data["failure_reason"] or "")
    assert data["gitee_issue_url"] is None


def test_create_response_url_fallback_to_url_field(developer_client, monkeypatch):
    """html_url 缺失但 url 存在时，使用 url 字段保存映射（兜底）。"""
    monkeypatch.setattr(
        main,
        "gitee_api_post",
        lambda path, token, data: {"id": 1001, "number": "IKD5H1", "url": "https://gitee.com/example/payment-service/issues/IKD5H1"},
    )
    record = create_request(developer_client)

    response = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["sync_status"] == "SYNCED"
    assert data["gitee_issue_url"] == "https://gitee.com/example/payment-service/issues/IKD5H1"


def test_create_gitee_issue_uses_owner_scope_endpoint(developer_client, monkeypatch):
    """创建 Issue 必须走 Gitee SDK 端点 /repos/{owner}/issues，仓库名作为 body 参数 repo 传入。

    回归：POST /repos/{owner}/{repo}/issues 并非建 Issue 端点，Gitee 会当"项目或企业"返回 404。
    断言捕获到的 path 不含 repo 段，且 payload 携带 repo 与必要的 title/body。
    """
    captured = {}

    def capturing_post(path, token, data):
        captured["path"] = path
        captured["data"] = data
        return dict(FAKE_ISSUE)

    monkeypatch.setattr(main, "gitee_api_post", capturing_post)
    record = create_request(developer_client)

    response = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert response.status_code == 200, response.text
    assert captured, "create_gitee_issue 应调用 gitee_api_post"

    path = captured["path"]  # e.g. /repos/{owner}/issues
    data = captured["data"]
    # 端点是 owner 维度，仓库走 body 参数
    assert path.endswith("/issues"), f"期望 .../issues 端点，实际 {path}"
    assert "/{repo}/issues" not in path and path.count("/") == 3, (
        f"创建 Issue 端点应是 /repos/{{owner}}/issues，不应含仓库路径段，实际 {path}"
    )
    assert path.startswith("/repos/") and path.endswith("/issues")
    assert data.get("repo"), "仓库名必须作为参数 repo 传入创建载荷"
    assert data.get("title") and data.get("body")


def test_create_gitee_issue_owner_endpoint_matches_repository(developer_client, monkeypatch):
    """authority 覆盖：payload 里 repo 与实际仓库配置一致（不是硬编码），端点 owner 来自仓库 URL owner。"""
    from app.retrieval import parsing  # noqa 确保 main 已就绪

    captured = {}

    def capturing_post(path, token, data):
        captured["path"] = path
        captured["data"] = data
        return dict(FAKE_ISSUE)

    monkeypatch.setattr(main, "gitee_api_post", capturing_post)
    record = create_request(developer_client)
    repository_url = main.REPOSITORIES[record["repository_id"]].url
    owner, repo = main.parse_gitee_repository(repository_url)

    response = developer_client.post(f"/api/team/requests/{record['id']}/sync-gitee")
    assert response.status_code == 200, response.text

    assert captured["path"] == f"/repos/{owner}/issues", captured["path"]
    assert captured["data"]["repo"] == repo, "payload 的 repo 应取自仓库 URL 的仓库名"
