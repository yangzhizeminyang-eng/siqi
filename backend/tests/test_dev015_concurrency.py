"""DEV-015 批① 并发与幂等测试套件。

覆盖场景：并发创建同一 Issue（2 线程 / 8 线程）只落库 1 个任务、其余 409(existing_task)；
双审批并发只产生 1 条 PLAN_APPROVED；approve 与 reject 并发互斥终态单一致；
并发 rebuild 同 Commit 合并为同一个索引（不重复落库、INDEX_REBUILT 只 1 条）；
顺序重复创建 409 回归锁定；并发创建不同 Issue 均成功且 ID 不冲突。

写法：每个请求线程独立 TestClient + 登录，threading.Barrier 齐发，
monkeypatch gitee 拉取函数内 sleep 放大锁内窗口；conftest autouse 重置保证用例隔离。
"""
import threading
import time
from datetime import datetime, timezone
from itertools import count

from fastapi.testclient import TestClient

from app import main

repository = main.REPOSITORIES["repo_payment_service"]
developer = main.USERS["user_developer"]
owner = main.USERS["user_approver"]
_task_seq = count(9600)

CREATE_BASE = {"project_id": "proj_payment", "repository_id": "repo_payment_service"}


def authed_client(account: str, password: str) -> TestClient:
    test_client = TestClient(main.app)
    login = test_client.post(
        "/api/auth/login",
        json={"account": account, "password": password},
    )
    assert login.status_code == 200
    return test_client


def snapshot_of(title: str, body: str) -> main.IssueSnapshotRecord:
    return main.IssueSnapshotRecord(
        title=title,
        body=body,
        comments=[],
        labels=["backend"],
        raw_response={},
        fetched_at=datetime.now(timezone.utc),
    )


def patch_gitee_fetches(monkeypatch, *, issue_delay: float = 0.2, head_delay: float = 0.3) -> None:
    """桩化 gitee 拉取并在内部 sleep，放大并发窗口（issue 拉取在创建锁外、head 拉取在锁内）。"""

    def fake_issue_snapshot(repository, issue_id: str) -> main.IssueSnapshotRecord:
        time.sleep(issue_delay)
        return snapshot_of(f"修复 {issue_id}", "并发边界场景：同一 Issue 不应产生重复任务。")

    def fake_head(repository, base_branch: str | None = None) -> str:
        time.sleep(head_delay)
        return "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"

    monkeypatch.setattr(main, "fetch_gitee_issue_snapshot", fake_issue_snapshot)
    monkeypatch.setattr(main, "fetch_gitee_branch_head", fake_head)


def fire_concurrently(actions: list[callable]) -> list:
    """Barrier 齐发各 action（每个 action 独立线程），返回按入参顺序的结果列表。"""
    results: list = [None] * len(actions)
    errors: list = []
    barrier = threading.Barrier(len(actions))

    def run(action: callable, index: int) -> None:
        try:
            barrier.wait(timeout=10)
            results[index] = action()
        except Exception as error:  # noqa: BLE001 - 汇聚线程内异常便于断言
            errors.append(error)

    threads = [threading.Thread(target=run, args=(action, index)) for index, action in enumerate(actions)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors, f"线程执行异常：{errors}"
    return results


def seed_analyzed_task(*, issue_id: str | None = None, title: str = "优化列表页布局") -> main.TaskRecord:
    now = datetime.now(timezone.utc)
    task_id = f"TASK-{next(_task_seq)}"
    task = main.TaskRecord(
        id=task_id,
        version=1,
        project_id=repository.project_id,
        project_name=main.PROJECTS[repository.project_id].name,
        repository_id=repository.id,
        repository_name=repository.name,
        repository_url=repository.url,
        issue_id=issue_id or f"IC15{next(_task_seq) % 100}",
        title=title,
        status="CREATED",
        base_branch=repository.default_branch,
        test_command=repository.test_command,
        approval_owner_id=owner.id,
        approval_owner_name=owner.name,
        work_branch=f"agent/main/issue-{issue_id}-v1",
        issue_snapshot=snapshot_of(title, "并发审批场景种子任务，正文足够长以通过分析。"),
        automation_state="running",
        manual_required=False,
        manual_reason="none",
        next_action="check_index",
        events=[],
        created_by=developer.id,
        created_by_name=developer.name,
        created_at=now,
        updated_at=now,
    )
    main.TASKS[task.id] = task
    analysis = main.generate_mock_analysis(task)
    for question in analysis.questions:
        question.answered = True
        question.answer = question.default_answer or "按默认口径处理"
    main.attach_analysis(task, analysis)
    return task


def waiting_plan_via_api(task: main.TaskRecord) -> dict:
    """经真实 /plans 端点把任务推进到 WAITING_APPROVAL，返回响应 data（含 plans）。"""
    client = authed_client("developer@company.com", "Dev123!")
    response = client.post(f"/api/tasks/{task.id}/plans")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "WAITING_APPROVAL"
    return data


def slow_ensure_assigned_approver(monkeypatch, delay: float = 0.3) -> None:
    original = main.ensure_assigned_approver

    def slow_ensure(task, user) -> None:
        time.sleep(delay)
        original(task, user)

    monkeypatch.setattr(main, "ensure_assigned_approver", slow_ensure)


# ---------- 场景 9/5：并发/顺序创建同一 Issue ----------

def test_concurrent_create_same_issue_only_one_task_registered(monkeypatch):
    patch_gitee_fetches(monkeypatch)
    payload = {**CREATE_BASE, "issue_id": "CC1501"}

    def do_create():
        client = authed_client("developer@company.com", "Dev123!")
        return client.post("/api/tasks", json=payload)

    responses = fire_concurrently([do_create, do_create])
    assert sorted(response.status_code for response in responses) == [200, 409]

    rejected = next(response for response in responses if response.status_code == 409)
    detail = rejected.json()["detail"]
    assert detail["code"] == 409
    assert "已存在未结束任务" in detail["message"]
    assert detail["data"]["existing_task"]["issue_id"] == "CC1501"

    assert len(main.TASKS) == 1
    task = next(iter(main.TASKS.values()))
    assert task.issue_id == "CC1501"
    event_types = [event.event_type for event in task.events]
    assert event_types.count("ISSUE_FETCHED") == 1
    assert event_types.count("TASK_CREATED") == 1
    global_types = [event.event_type for event in main.AUDIT_EVENTS.values()]
    assert global_types.count("ISSUE_FETCHED") == 1
    assert global_types.count("TASK_CREATED") == 1


def test_concurrent_create_same_issue_eight_threads_only_one_success(monkeypatch):
    patch_gitee_fetches(monkeypatch)
    payload = {**CREATE_BASE, "issue_id": "CC1502"}

    def do_create():
        client = authed_client("developer@company.com", "Dev123!")
        return client.post("/api/tasks", json=payload)

    responses = fire_concurrently([do_create] * 8)
    codes = sorted(response.status_code for response in responses)
    assert codes == [200] + [409] * 7
    assert len(main.TASKS) == 1
    assert all(response.json()["detail"]["code"] == 409 for response in responses if response.status_code == 409)


def test_sequential_duplicate_create_still_conflict(monkeypatch):
    """顺序重复创建 409 行为回归锁定（锁外快速查重先拦截）。"""
    patch_gitee_fetches(monkeypatch, issue_delay=0, head_delay=0)
    payload = {**CREATE_BASE, "issue_id": "CC1506"}
    client = authed_client("developer@company.com", "Dev123!")

    first = client.post("/api/tasks", json=payload)
    assert first.status_code == 200
    second = client.post("/api/tasks", json=payload)
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["code"] == 409
    assert detail["data"]["existing_task"]["id"] == first.json()["data"]["id"]
    assert len(main.TASKS) == 1


def test_concurrent_create_different_issues_both_succeed_with_unique_ids(monkeypatch):
    """锁不阻塞不同 Issue 并发创建；task_id 在锁内计算，不与已有任务冲突。"""
    patch_gitee_fetches(monkeypatch)

    def do_create(issue_id: str):
        client = authed_client("developer@company.com", "Dev123!")
        return client.post("/api/tasks", json={**CREATE_BASE, "issue_id": issue_id})

    responses = fire_concurrently([lambda: do_create("CC1507"), lambda: do_create("CC1508")])
    assert sorted(response.status_code for response in responses) == [200, 200]
    ids = [response.json()["data"]["id"] for response in responses]
    assert len(set(ids)) == 2
    assert len(main.TASKS) == 2
    event_types = [event.event_type for event in main.AUDIT_EVENTS.values()]
    assert event_types.count("TASK_CREATED") == 2


# ---------- 场景 10：并发审批同一计划 ----------

def test_concurrent_approve_same_plan_single_approved_event(monkeypatch):
    task = seed_analyzed_task(issue_id="IC1503")
    waiting = waiting_plan_via_api(task)
    plan_id = waiting["plans"][0]["id"]
    slow_ensure_assigned_approver(monkeypatch)

    def do_approve():
        client = authed_client("approver@company.com", "Approver123!")
        return client.post(f"/api/tasks/{task.id}/plans/{plan_id}/approve", json={})

    responses = fire_concurrently([do_approve, do_approve])
    assert sorted(response.status_code for response in responses) == [200, 409]

    failed = next(response for response in responses if response.status_code == 409)
    assert "该计划不在待审批状态，不能审批" in failed.json()["detail"]["message"]

    after = main.TASKS[task.id]
    assert after.status == "APPROVED"
    assert after.plans[0].status == "APPROVED"
    event_types = [event.event_type for event in after.events]
    assert event_types.count("PLAN_APPROVED") == 1
    global_types = [event.event_type for event in main.AUDIT_EVENTS.values()]
    assert global_types.count("PLAN_APPROVED") == 1


# ---------- 场景 10：审批与拒绝并发互斥 ----------

def test_concurrent_approve_and_reject_mutually_exclusive(monkeypatch):
    task = seed_analyzed_task(issue_id="IC1504")
    waiting = waiting_plan_via_api(task)
    plan_id = waiting["plans"][0]["id"]
    slow_ensure_assigned_approver(monkeypatch)

    def do_approve():
        client = authed_client("approver@company.com", "Approver123!")
        return client.post(f"/api/tasks/{task.id}/plans/{plan_id}/approve", json={})

    def do_reject():
        client = authed_client("approver@company.com", "Approver123!")
        return client.post(f"/api/tasks/{task.id}/plans/{plan_id}/reject", json={"reason": "方案缺少回滚预案"})

    responses = fire_concurrently([do_approve, do_reject])
    codes = sorted(response.status_code for response in responses)
    assert codes == [200, 409]
    loser_message = next(
        response.json()["detail"]["message"] for response in responses if response.status_code == 409
    )

    after = main.TASKS[task.id]
    event_types = [event.event_type for event in after.events]
    if after.status == "APPROVED":
        assert "PLAN_REJECTED" not in event_types
        assert "TASK_FAILED" not in event_types
        assert event_types.count("PLAN_APPROVED") == 1
        assert "不能拒绝" in loser_message
    else:
        assert after.status == "FAILED"
        assert after.manual_reason == "plan_rejected"
        assert event_types.count("PLAN_REJECTED") == 1
        assert event_types.count("TASK_FAILED") == 1
        assert event_types.count("PLAN_APPROVED") == 0
        assert "不能审批" in loser_message


# ---------- 场景 11：并发重建同一 Commit 索引 ----------

def test_concurrent_rebuild_same_commit_single_index_record(monkeypatch):
    def fake_head(repository, base_branch: str | None = None) -> str:
        time.sleep(0.25)
        return "c0ffee11deadbeef22c0ffee33deadbeef44"

    monkeypatch.setattr(main, "fetch_gitee_branch_head", fake_head)

    def do_rebuild():
        client = authed_client("admin@company.com", "Admin123!")
        return client.post("/api/repositories/repo_payment_service/index/rebuild", json={})

    responses = fire_concurrently([do_rebuild, do_rebuild])
    assert sorted(response.status_code for response in responses) == [200, 200]
    index_ids = {response.json()["data"]["index_id"] for response in responses}
    assert len(index_ids) == 1
    assert len(main.REPOSITORY_INDEXES) == 1
    messages = [response.json()["message"] for response in responses]
    assert any("索引重建任务已创建" in message for message in messages)
    assert any("已合并本次重建请求" in message for message in messages)
    rebuilt_events = [event for event in main.AUDIT_EVENTS.values() if event.event_type == "INDEX_REBUILT"]
    assert len(rebuilt_events) == 1
