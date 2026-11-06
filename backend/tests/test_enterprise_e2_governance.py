"""企业版 E2-01 审批与治理门禁验收测试（覆盖规格 22 项清单）。

真实服务行为优先：全部经 HTTP 端点 + 服务端租户上下文 + 真实副作用运行。
种子关系：admin→tenant_owner、lead→project_owner、approver→reviewer、developer→developer、observer→auditor。
L4（role_change，需 member:manage）提交者为 admin；审批人由 lead 提升为 security_admin 承担。
"""

from __future__ import annotations

import datetime as _dt
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import main
from app.approvals import store as approval_store


def _enable_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'e2_approval.db').as_posix()}")
    from app.db.session import create_schema, get_engine, get_session_factory

    get_engine.cache_clear()
    get_session_factory.cache_clear()
    create_schema()


def _clear_db_caches() -> None:
    from app.db.session import get_engine, get_session_factory

    get_engine.cache_clear()
    get_session_factory.cache_clear()


ACCOUNTS = {
    "admin": ("admin@company.com", "Admin123!"),
    "lead": ("lead@company.com", "Lead123!"),
    "approver": ("approver@company.com", "Approver123!"),
    "developer": ("developer@company.com", "Dev123!"),
    "observer": ("observer@company.com", "Observer123!"),
}


def _login(client: TestClient, who: str) -> TestClient:
    account, password = ACCOUNTS[who]
    resp = client.post("/api/auth/login", json={"account": account, "password": password})
    assert resp.status_code == 200, resp.text
    return client


def promote(admin_c: TestClient, user_key: str, role: str) -> None:
    membership_id = f"mem_user_{user_key}_default"
    resp = admin_c.patch(f"/api/enterprise/members/{membership_id}", json={"role": role})
    assert resp.status_code == 200, resp.text


def _submit(c: TestClient, op: str, rt: str, rid: str, key: str,
            summary: dict | None = None, title: str = "t") -> dict:
    body: dict = {"operation": op, "resource_type": rt, "resource_id": rid,
                  "title": title, "idempotency_key": key}
    if summary is not None:
        body["request_summary"] = summary
    resp = c.post("/api/enterprise/approvals", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _approve(c: TestClient, aid: str) -> int:
    return c.post(f"/api/enterprise/approvals/{aid}/approve", json={"comment": "ok"}).status_code


def _reject(c: TestClient, aid: str) -> int:
    return c.post(f"/api/enterprise/approvals/{aid}/reject", json={"reason": "不同意"}).status_code


def _cancel(c: TestClient, aid: str) -> int:
    return c.post(f"/api/enterprise/approvals/{aid}/cancel", json={"reason": "撤回"}).status_code


def _execute(c: TestClient, aid: str, confirm: bool = True) -> int:
    return c.post(f"/api/enterprise/approvals/{aid}/execute",
                  json={"secondary_confirmation": confirm}).status_code


def _mget(membership_id: str):
    from app.tenancy import store as t

    return t.get_membership(membership_id)


INDEX_OP = {"operation": "repository.index.rebuild", "resource_type": "repository",
            "resource_id": "repo_payment_service"}


# ---------------------------------------------------------------------------
# 1. 风险与决策
# ---------------------------------------------------------------------------


def test_l2_triggers_approval_required():
    dev = _login(TestClient(main.app), "developer")
    record = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service", "k-l2-1")
    assert record["risk_level"] == "L2"
    assert record["decision"] == "approval_required"
    assert record["approval_status"] == "pending"


def test_l4_security_mode_and_risk():
    admin = _login(TestClient(main.app), "admin")
    record = _submit(admin, "tenant.member.role_change", "tenant_member",
                     "mem_user_observer_default", "k-l4x", summary={"new_role": "reviewer"})
    assert record["risk_level"] == "L4"
    assert record["approval_mode"] == "security"


def test_submit_validates_operation_target_contract():
    admin = _login(TestClient(main.app), "admin")
    mismatch = admin.post(
        "/api/enterprise/approvals",
        json={
            "operation": "repository.index.rebuild",
            "resource_type": "tenant",
            "resource_id": "tenant_default",
            "idempotency_key": "k-target-mismatch",
        },
    )
    assert mismatch.status_code == 422

    missing = admin.post(
        "/api/enterprise/approvals",
        json={
            **INDEX_OP,
            "resource_id": "repo-does-not-exist",
            "idempotency_key": "k-target-missing",
        },
    )
    assert missing.status_code == 404


def test_governance_dry_run_validates_operation_target_contract():
    admin = _login(TestClient(main.app), "admin")
    mismatch = admin.post(
        "/api/enterprise/governance/decide",
        json={
            "operation": "repository.index.rebuild",
            "resource_type": "tenant",
            "resource_id": "tenant_default",
            "idempotency_key": "k-dry-run-mismatch",
        },
    )
    assert mismatch.status_code == 422

    missing = admin.post(
        "/api/enterprise/governance/decide",
        json={
            **INDEX_OP,
            "resource_id": "repo-dry-run-does-not-exist",
            "idempotency_key": "k-dry-run-missing",
        },
    )
    assert missing.status_code == 404


def test_l4_requires_security_admin_not_tenant_admin():
    admin = _login(TestClient(main.app), "admin")
    promote(admin, "lead", "tenant_admin")
    lead = _login(TestClient(main.app), "lead")
    rec = _submit(
        admin,
        "tenant.member.role_change",
        "tenant_member",
        "mem_user_observer_default",
        "k-l4-tenant-admin",
        summary={"new_role": "reviewer"},
    )
    assert _approve(lead, rec["id"]) == 403


def test_dual_approval_cannot_count_the_same_approver_twice():
    admin = _login(TestClient(main.app), "admin")
    promote(admin, "lead", "project_owner")
    lead = _login(TestClient(main.app), "lead")
    rec = _submit(admin, "repository.archive", "repository", "repo_payment_service", "k-dual-repeat")

    assert _approve(lead, rec["id"]) == 200
    assert _approve(lead, rec["id"]) == 409
    assert admin.get(f"/api/enterprise/approvals/{rec['id']}").json()["data"]["approval_status"] == "pending"


def test_nested_sensitive_fields_are_removed_and_audit_uses_event_time():
    dev = _login(TestClient(main.app), "developer")
    rec = _submit(
        dev,
        "repository.index.rebuild",
        "repository",
        "repo_payment_service",
        "k-nested-secret",
        summary={"outer": {"token": "secret", "note": "keep"}, "items": [{"password": "secret"}]},
    )
    assert rec["request_summary"] == {"outer": {"note": "keep"}, "items": [{}]}

    events = [
        event for event in main.AUDIT_EVENTS.values()
        if event.event_type == "approval.submit" and event.resource_id == rec["id"]
    ]
    assert len(events) == 1
    record_created_at = _dt.datetime.fromisoformat(rec["created_at"].replace("Z", "+00:00"))
    assert events[0].created_at >= record_created_at


def test_same_idempotency_key_is_single_record_under_concurrent_submit():
    def submit_once() -> tuple[int, str | None]:
        client = _login(TestClient(main.app), "developer")
        response = client.post(
            "/api/enterprise/approvals",
            json={**INDEX_OP, "idempotency_key": "k-concurrent-idempotency"},
        )
        data = response.json().get("data") if response.headers.get("content-type", "").startswith("application/json") else None
        return response.status_code, data.get("id") if isinstance(data, dict) else None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: submit_once(), range(4)))

    assert all(status_code == 200 for status_code, _ in results)
    assert len({approval_id for _, approval_id in results}) == 1
    assert len(approval_store.APPROVALS) == 1


def test_db_idempotency_survives_process_cache_miss(tmp_path, monkeypatch):
    _enable_db(tmp_path, monkeypatch)
    try:
        first_client = _login(TestClient(main.app), "developer")
        first = _submit(first_client, "repository.index.rebuild", "repository",
                        "repo_payment_service", "k-db-idem")

        # 模拟第二个应用副本：没有第一个副本的进程内审批缓存，只能依赖数据库唯一键。
        approval_store.reset_store()

        second_client = _login(TestClient(main.app), "developer")
        second = _submit(second_client, "repository.index.rebuild", "repository",
                         "repo_payment_service", "k-db-idem")

        assert second["id"] == first["id"]
        assert len(approval_store.APPROVALS) == 1
    finally:
        monkeypatch.setenv("USE_SQLALCHEMY", "false")
        _clear_db_caches()


def test_db_cached_approval_can_be_read_after_process_cache_miss(tmp_path, monkeypatch):
    _enable_db(tmp_path, monkeypatch)
    try:
        first_client = _login(TestClient(main.app), "developer")
        first = _submit(first_client, "repository.index.rebuild", "repository",
                        "repo_payment_service", "k-db-read")

        approval_store.reset_store()

        second_client = _login(TestClient(main.app), "developer")
        response = second_client.get(f"/api/enterprise/approvals/{first['id']}")

        assert response.status_code == 200, response.text
        assert response.json()["data"]["id"] == first["id"]
    finally:
        monkeypatch.setenv("USE_SQLALCHEMY", "false")
        _clear_db_caches()


def test_db_cached_approval_can_be_approved_after_process_cache_miss(tmp_path, monkeypatch):
    _enable_db(tmp_path, monkeypatch)
    try:
        developer = _login(TestClient(main.app), "developer")
        first = _submit(developer, "repository.index.rebuild", "repository",
                        "repo_payment_service", "k-db-approve")

        approval_store.reset_store()

        admin = _login(TestClient(main.app), "admin")
        response = admin.post(
            f"/api/enterprise/approvals/{first['id']}/approve",
            json={"comment": "database-backed approval"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["approval_status"] == "approved"
    finally:
        monkeypatch.setenv("USE_SQLALCHEMY", "false")
        _clear_db_caches()


def test_enterprise_mode_legacy_rebuild_endpoint_requires_approval(monkeypatch):
    monkeypatch.setenv("APP_ENV", "enterprise")
    dev = _login(TestClient(main.app), "developer")

    response = dev.post("/api/repositories/repo_payment_service/index/rebuild", json={})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["operation"] == "repository.index.rebuild"
    assert data["resource_type"] == "repository"
    assert data["resource_id"] == "repo_payment_service"
    assert data["approval_status"] == "pending"
    assert data["decision"] == "approval_required"
    assert approval_store.get(data["id"]) is not None


def test_enterprise_mode_legacy_sync_gitee_endpoint_requires_approval(monkeypatch):
    monkeypatch.setenv("APP_ENV", "enterprise")
    now = _dt.datetime.now(_dt.timezone.utc)
    main.TEAM_REQUESTS["req_enterprise_sync_gate"] = main.TeamRequestRecord(
        id="req_enterprise_sync_gate",
        request_no="REQ-E2-GATE",
        project_id="proj_payment",
        project_name="支付中台",
        repository_id="repo_payment_service",
        repository_name="payment-service",
        type="demand",
        title="企业模式旧同步入口门禁",
        body="用于验证企业模式下旧 sync-gitee 入口不会绕过审批直接执行。",
        created_by_id="user_developer",
        created_by_name="周航",
        created_at=now,
        updated_at=now,
    )
    dev = _login(TestClient(main.app), "developer")

    response = dev.post("/api/team/requests/req_enterprise_sync_gate/sync-gitee")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["operation"] == "team_request.sync_gitee"
    assert data["resource_type"] == "team_request"
    assert data["resource_id"] == "req_enterprise_sync_gate"
    assert data["approval_status"] == "pending"
    assert data["decision"] == "approval_required"
    assert main.TEAM_REQUESTS["req_enterprise_sync_gate"].sync_status == "DRAFT"


# ---------------------------------------------------------------------------
# 2. 权限边界
# ---------------------------------------------------------------------------


def test_auditor_read_only_no_submit():
    obs = _login(TestClient(main.app), "observer")
    resp = obs.post("/api/enterprise/approvals", json={**INDEX_OP, "idempotency_key": "k-obs"})
    assert resp.status_code == 403  # auditor 无 approval:submit


def test_auditor_can_view_but_not_approve_or_execute():
    obs = _login(TestClient(main.app), "observer")
    admin = _login(TestClient(main.app), "admin")
    rec = _submit(admin, "repository.index.rebuild", "repository", "repo_payment_service", "k-av")
    assert obs.get(f"/api/enterprise/approvals/{rec['id']}").status_code == 200
    assert _approve(obs, rec["id"]) == 403
    assert _execute(obs, rec["id"]) == 403


def test_developer_cannot_submit_governance_denied():
    # developer 具备 index:manage + approval:submit，可提交；无审批权限不能审批
    dev = _login(TestClient(main.app), "developer")
    admin = _login(TestClient(main.app), "admin")
    rec = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service", "k-dev1")
    assert _approve(dev, rec["id"]) == 403  # developer 无 approval:approve


def test_self_high_risk_cannot_approve_own():
    admin = _login(TestClient(main.app), "admin")
    promote(admin, "lead", "tenant_admin")
    lead = _login(TestClient(main.app), "lead")
    rec = _submit(lead, "tenant.member.role_change", "tenant_member",
                  "mem_user_observer_default", "k-self", summary={"new_role": "reviewer"})
    assert rec["risk_level"] == "L4"
    # 自审高危 -> 409/403
    assert _approve(lead, rec["id"]) in (403, 409)


# ---------------------------------------------------------------------------
# 3. 跨租户隔离
# ---------------------------------------------------------------------------


def test_cross_tenant_view_approve_execute_fails():
    admin = _login(TestClient(main.app), "admin")
    resp = admin.post("/api/enterprise/tenants", json={"name": "TenantB"})
    assert resp.status_code == 200
    tenant_b = resp.json()["data"]["id"]

    rec = _submit(admin, "repository.index.rebuild", "repository", "repo_payment_service", "k-x1")
    switch = admin.post("/api/enterprise/tenant-context/switch", json={"tenant_id": tenant_b})
    assert switch.status_code == 200
    assert admin.get(f"/api/enterprise/approvals/{rec['id']}").status_code == 403
    assert _approve(admin, rec["id"]) == 403
    assert _execute(admin, rec["id"]) == 403
    ids_in_b = [a["id"] for a in admin.get("/api/enterprise/approvals").json()["data"]]
    assert rec["id"] not in ids_in_b


# ---------------------------------------------------------------------------
# 4. 审批全生命周期
# ---------------------------------------------------------------------------


def test_approve_then_execute_once():
    dev = _login(TestClient(main.app), "developer")
    admin = _login(TestClient(main.app), "admin")
    rec = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service", "k-flow")
    assert _approve(admin, rec["id"]) == 200
    assert _execute(admin, rec["id"]) == 200
    detail = admin.get(f"/api/enterprise/approvals/{rec['id']}").json()["data"]
    assert detail["approval_status"] == "executed"
    assert detail["executed_at"] is not None


def test_approved_repository_rebuild_is_submitted_to_existing_worker(monkeypatch):
    dispatched: list[str] = []
    monkeypatch.setattr(
        main,
        "fetch_gitee_branch_head",
        lambda repository, branch: "commit-e2-worker",
    )
    monkeypatch.setattr(
        main,
        "_enqueue_index_build",
        lambda index_id: dispatched.append(index_id) or {
            "enqueued": True,
            "mode": "test",
            "label": f"rebuild:{index_id}",
        },
    )

    dev = _login(TestClient(main.app), "developer")
    admin = _login(TestClient(main.app), "admin")
    record = _submit(
        dev,
        "repository.index.rebuild",
        "repository",
        "repo_payment_service",
        "k-worker-index",
        summary={"base_branch": "main"},
    )

    assert _approve(admin, record["id"]) == 200
    assert _execute(admin, record["id"]) == 200
    assert len(dispatched) == 1


def test_approved_gitee_sync_is_submitted_to_existing_worker(monkeypatch):
    from app.workers import dispatch

    now = _dt.datetime.now(_dt.timezone.utc)
    main.TEAM_REQUESTS["req_worker_sync"] = main.TeamRequestRecord(
        id="req_worker_sync",
        request_no="REQ-E2-WORKER",
        project_id="proj_payment",
        project_name="支付中台",
        repository_id="repo_payment_service",
        repository_name="payment-service",
        type="demand",
        title="审批执行进入 Gitee Worker",
        body="用于验证审批执行不会停留在演示级已受理。",
        created_by_id="user_developer",
        created_by_name="周航",
        created_at=now,
        updated_at=now,
    )
    captured: list[dict] = []

    def fake_enqueue(task, *, kwargs=None, run_inline, label=""):
        captured.append({"kwargs": kwargs or {}, "label": label})
        return {"enqueued": True, "mode": "test", "label": label}

    monkeypatch.setattr(dispatch, "enqueue_or_run", fake_enqueue)

    dev = _login(TestClient(main.app), "developer")
    admin = _login(TestClient(main.app), "admin")
    record = _submit(
        dev,
        "team_request.sync_gitee",
        "team_request",
        "req_worker_sync",
        "k-worker-sync",
    )

    assert _approve(admin, record["id"]) == 200
    assert _execute(admin, record["id"]) == 200
    assert captured == [{
        "kwargs": {"request_id": "req_worker_sync"},
        "label": "gitee_sync:req_worker_sync",
    }]
    assert main.TEAM_REQUESTS["req_worker_sync"].sync_status == "SYNCING"


def test_failed_inline_gitee_worker_does_not_mark_approval_executed(monkeypatch):
    from app.workers import dispatch

    now = _dt.datetime.now(_dt.timezone.utc)
    main.TEAM_REQUESTS["req_worker_failed"] = main.TeamRequestRecord(
        id="req_worker_failed",
        request_no="REQ-E2-WORKER-FAIL",
        project_id="proj_payment",
        project_name="支付中台",
        repository_id="repo_payment_service",
        repository_name="payment-service",
        type="demand",
        title="审批执行失败保持可重试",
        body="用于验证 Worker 失败不会伪装成审批执行成功。",
        created_by_id="user_developer",
        created_by_name="周航",
        created_at=now,
        updated_at=now,
    )
    monkeypatch.setattr(
        main,
        "sync_request_to_gitee_worker",
        lambda request_id: {"status": "error", "error": "Gitee 暂不可用"},
    )
    monkeypatch.setenv("USE_CELERY", "false")
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)

    dev = _login(TestClient(main.app), "developer")
    admin = _login(TestClient(main.app), "admin")
    record = _submit(
        dev,
        "team_request.sync_gitee",
        "team_request",
        "req_worker_failed",
        "k-worker-failed",
    )

    assert _approve(admin, record["id"]) == 200
    assert _execute(admin, record["id"]) == 409
    detail = admin.get(f"/api/enterprise/approvals/{record['id']}").json()["data"]
    assert detail["approval_status"] == "approved"


def test_l2_single_approver_and_approval_mode():
    dev = _login(TestClient(main.app), "developer")
    admin = _login(TestClient(main.app), "admin")
    now = _dt.datetime.now(_dt.timezone.utc)
    main.TEAM_REQUESTS["req_1"] = main.TeamRequestRecord(
        id="req_1",
        request_no="REQ-E2-001",
        project_id="proj_payment",
        project_name="支付中台",
        repository_id="repo_payment_service",
        repository_name="payment-service",
        type="bug",
        title="E2 审批测试需求",
        body="用于验证企业审批申请资源校验。",
        created_by_id="user_developer",
        created_by_name="周航",
        created_at=now,
        updated_at=now,
    )
    rec = _submit(dev, "team_request.sync_gitee", "team_request", "req_1", "k-l2b")
    assert rec["approval_mode"] == "single"
    assert _approve(admin, rec["id"]) == 200
    assert admin.get(f"/api/enterprise/approvals/{rec['id']}").json()["data"]["approval_status"] == "approved"


def test_l3_dual_approval_and_observable_once_execute():
    admin = _login(TestClient(main.app), "admin")
    promote(admin, "lead", "project_owner")
    promote(admin, "approver", "tenant_admin")
    lead = _login(TestClient(main.app), "lead")
    approver = _login(TestClient(main.app), "approver")

    rec = _submit(admin, "repository.archive", "repository", "repo_payment_service", "k-arch")
    assert rec["approval_mode"] == "dual"
    assert rec["risk_level"] == "L3"

    assert _approve(lead, rec["id"]) == 200
    assert admin.get(f"/api/enterprise/approvals/{rec['id']}").json()["data"]["approval_status"] == "pending"
    assert _approve(approver, rec["id"]) == 200
    assert admin.get(f"/api/enterprise/approvals/{rec['id']}").json()["data"]["approval_status"] == "approved"

    assert _execute(admin, rec["id"]) == 200
    assert main.REPOSITORIES["repo_payment_service"].is_active is False
    # 重复执行 -> 409，且不产生第二次业务变更
    assert _execute(admin, rec["id"]) == 409
    assert main.REPOSITORIES["repo_payment_service"].is_active is False


def test_repeated_submit_idempotent():
    admin = _login(TestClient(main.app), "admin")
    first = _submit(admin, "repository.index.rebuild", "repository", "repo_payment_service", "k-idem")
    second = _submit(admin, "repository.index.rebuild", "repository", "repo_payment_service", "k-idem")
    assert first["id"] == second["id"]


def test_release_state_machine_transitions():
    dev = _login(TestClient(main.app), "developer")
    admin = _login(TestClient(main.app), "admin")
    rec = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service", "k-sm")
    # reject 后不可执行
    assert _reject(admin, rec["id"]) == 200
    assert _execute(admin, rec["id"]) == 409

    rec2 = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service", "k-sm2")
    assert _cancel(dev, rec2["id"]) == 200  # 发起人可取消
    assert _execute(admin, rec2["id"]) == 409

    rec3 = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service", "k-sm3")
    stored = approval_store.get(rec3["id"])
    stored.expires_at = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=1)
    assert _execute(admin, rec3["id"]) == 409
    assert admin.get(f"/api/enterprise/approvals/{rec3['id']}").json()["data"]["approval_status"] == "expired"


def test_terminal_executed_cannot_transition_again():
    dev = _login(TestClient(main.app), "developer")
    admin = _login(TestClient(main.app), "admin")
    rec = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service", "k-term")
    assert _approve(admin, rec["id"]) == 200
    assert _execute(admin, rec["id"]) == 200
    assert _approve(admin, rec["id"]) == 409
    assert _execute(admin, rec["id"]) == 409


def test_l4_security_secondary_confirmation_and_execute():
    admin = _login(TestClient(main.app), "admin")
    promote(admin, "lead", "security_admin")
    lead = _login(TestClient(main.app), "lead")
    rec = _submit(admin, "tenant.member.role_change", "tenant_member",
                  "mem_user_observer_default", "k-l4a", summary={"new_role": "reviewer"})
    assert rec["approval_mode"] == "security"
    assert _approve(lead, rec["id"]) == 200  # 非自审（sub=admin）
    assert _execute(admin, rec["id"], confirm=False) == 422  # L4 需二次确认
    assert _execute(admin, rec["id"], confirm=True) == 200
    assert _mget("mem_user_observer_default").role == "reviewer"


def test_resource_changed_blocks_stale_execution():
    admin = _login(TestClient(main.app), "admin")
    promote(admin, "lead", "security_admin")
    lead = _login(TestClient(main.app), "lead")
    rec = _submit(admin, "tenant.member.role_change", "tenant_member",
                  "mem_user_observer_default", "k-stale", summary={"new_role": "reviewer"})
    assert _approve(lead, rec["id"]) == 200
    # 资源在审批期间被外部修改 -> fingerprint 变化 -> 旧审批不可执行
    change = admin.patch("/api/enterprise/members/mem_user_observer_default",
                         json={"role": "developer"})
    assert change.status_code == 200, change.text
    assert _execute(admin, rec["id"], confirm=True) == 409


# ---------------------------------------------------------------------------
# 5. 禁成员阻断
# ---------------------------------------------------------------------------


def test_disabled_member_blocked_from_next_request():
    admin = _login(TestClient(main.app), "admin")
    promote(admin, "lead", "tenant_admin")
    lead = _login(TestClient(main.app), "lead")
    # owner 禁用一名租户管理员（非 owner 不能禁特权成员，此场景由特权门禁保障）。
    # 被禁成员的会话应立即失效（下一个请求 401）。
    disable = admin.patch("/api/enterprise/members/mem_user_lead_default", json={"enabled": False})
    assert disable.status_code == 200, disable.text
    assert lead.get("/api/enterprise/approvals").status_code == 401


# ---------------------------------------------------------------------------
# 6. 审计与安全
# ---------------------------------------------------------------------------


def test_audit_events_no_sensitive_fields():
    dev = _login(TestClient(main.app), "developer")
    admin = _login(TestClient(main.app), "admin")
    rec = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service",
                  "k-audit", summary={"token": "secret-token-xyz", "note": "clean"})
    _approve(admin, rec["id"])
    detail = admin.get(f"/api/enterprise/approvals/{rec['id']}").json()["data"]
    assert "token" not in detail["request_summary"]
    assert detail["request_summary"].get("note") == "clean"

    events = [e for e in main.AUDIT_EVENTS.values()
              if e.event_type and e.event_type.startswith("approval.")]
    assert events, "审批流程未写入审计事件"
    for event in events:
        raw = f"{event.summary} {event.event_type}".lower()
        for forbidden in ("token", "password", "jwt", "cookie", "recovery"):
            assert forbidden not in raw
        assert event.tenant_id == "tenant_default"
