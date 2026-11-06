"""企业版 E2-01 加固：多副本执行一致性 / DB 持久化 / 重启恢复 / 幂等 / 敏感清洗。

真实行为优先：并发仅一成功、缓存空 DB 回源、唯一键幂等、抢占失败不执行、
失败可重试不重复副作用、重启恢复、legacy 门禁、跨租户、审计租户归属、敏感递归清洗、字段兼容。
"""

from __future__ import annotations

import datetime as _dt
import threading

import pytest
from fastapi.testclient import TestClient

from app import main
from app.approvals import service as approval_service
from app.approvals import store as approval_store


ACCOUNTS = {
    "admin": ("admin@company.com", "Admin123!"),
    "lead": ("lead@company.com", "Lead123!"),
    "developer": ("developer@company.com", "Dev123!"),
}


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """临时 sqlite 可用 DB：开启持久化、复位引擎缓存；测试后恢复业务 dict + 审批/租户内存。"""
    from app.db import session as db_session
    from app.tenancy import store as tenancy_store

    before_biz = {
        name: (dict(getattr(main, name)) if isinstance(getattr(main, name, None), dict) else getattr(main, name, None))
        for name in ["USERS", "PROJECTS", "REPOSITORIES", "TASKS", "TEAM_REQUESTS", "AUDIT_EVENTS", "REPOSITORY_INDEXES", "CODE_CHUNKS"]
    }
    before_tenancy = (dict(tenancy_store.TENANTS), dict(tenancy_store.ORGANIZATIONS), dict(tenancy_store.MEMBERSHIPS))

    db_file = tmp_path / "approval_persist.db"
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("DATA_DIR", tmp_path.as_posix())
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    db_session.create_schema()
    yield db_file

    for name, snapshot in before_biz.items():
        store = getattr(main, name, None)
        if isinstance(store, dict):
            store.clear()
            store.update(snapshot) if isinstance(snapshot, dict) else None
    tenancy_store.TENANTS.clear(); tenancy_store.TENANTS.update(before_tenancy[0])
    tenancy_store.ORGANIZATIONS.clear(); tenancy_store.ORGANIZATIONS.update(before_tenancy[1])
    tenancy_store.MEMBERSHIPS.clear(); tenancy_store.MEMBERSHIPS.update(before_tenancy[2])
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()


def _login(client: TestClient, who: str) -> TestClient:
    account, password = ACCOUNTS[who]
    resp = client.post("/api/auth/login", json={"account": account, "password": password})
    assert resp.status_code == 200, resp.text
    return client


def _submit(c, op, rt, rid, key, summary=None):
    body = {"operation": op, "resource_type": rt, "resource_id": rid, "title": "t", "idempotency_key": key}
    if summary is not None:
        body["request_summary"] = summary
    resp = c.post("/api/enterprise/approvals", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _approve(c, aid):
    assert c.post(f"/api/enterprise/approvals/{aid}/approve", json={"comment": "ok"}).status_code == 200


def _execute(c, aid, confirm=True):
    return c.post(f"/api/enterprise/approvals/{aid}/execute",
                  json={"secondary_confirmation": confirm}).status_code


# ---------------------------------------------------------------------------
# 1. 并发执行仅一成功、抢占失败不执行
# ---------------------------------------------------------------------------


def test_concurrent_execute_only_one_wins():
    import time as _time

    dev = _login(TestClient(main.app), "developer")
    admin = _login(TestClient(main.app), "admin")
    rec = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service", "k-conc")
    _approve(admin, rec["id"])

    called = {"n": 0}
    import app.approvals.service as svc

    def slow_op(context, user, operation, resource_id, request_summary=None):
        called["n"] += 1
        _time.sleep(0.3)  # 让另一线程有机会并发请求；仅抢占者到达这里
        return {"status": "ok", "summary": "executed-" + operation}
    real_execute_operation = svc.execute_operation
    svc.execute_operation = slow_op
    codes = {}

    def run():
        codes[threading.get_ident()] = _execute(admin, rec["id"])
    threads = [threading.Thread(target=run), threading.Thread(target=run)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    svc.execute_operation = real_execute_operation

    assert called["n"] == 1, f"并发执行应只有一次真实副作用，实际 {called['n']}"
    assert sorted(codes.values()) == [200, 409], codes
    detail = admin.get(f"/api/enterprise/approvals/{rec['id']}").json()["data"]
    assert detail["approval_status"] == "executed"


# ---------------------------------------------------------------------------
# 2/3. DB 缓存空回源 + 唯一键幂等
# ---------------------------------------------------------------------------


def test_cache_miss_single_approval_loaded_from_db(fresh_db):
    dev = _login(TestClient(main.app), "developer")
    rec = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service", "k-cache")
    approval_store.APPROVALS.clear()  # 进程缓存清空
    loaded = approval_store.get(rec["id"])  # DB 回源
    assert loaded is not None
    assert loaded.id == rec["id"]
    assert loaded.approval_status == "pending"


def test_unique_key_conflict_returns_original(fresh_db):
    from app.approvals.store import APPROVALS, put, find_by_idempotency

    APPROVALS.clear()
    dev = _login(TestClient(main.app), "developer")
    first = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service", "k-uniq")
    second = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service", "k-uniq")
    rows = find_by_idempotency("tenant_default", "k-uniq")
    assert rows is not None
    assert first["id"] == second["id"] == rows.id
    assert len([a for a in APPROVALS.values() if a.idempotency_key == "k-uniq"]) == 1


# ---------------------------------------------------------------------------
# 4/5/6/7. 抢占失败不执行；失败可追踪可重试；重试不重复副作用
# ---------------------------------------------------------------------------


def test_execute_failure_stays_approved_and_retryable(fresh_db):
    from app.approvals.service import execute_operation as _real

    dev = _login(TestClient(main.app), "developer")
    admin = _login(TestClient(main.app), "admin")
    rec = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service", "k-fail")
    _approve(admin, rec["id"])

    import app.approvals.service as svc

    def failing_op(context, user, operation, resource_id, request_summary=None):
        raise ValueError("模拟 Worker 失败")
    svc.execute_operation = failing_op
    try:
        assert _execute(admin, rec["id"]) == 409
        assert _execute(admin, rec["id"]) == 409  # 失败可被重复尝试（仍回 409 且不置 executed）
    finally:
        svc.execute_operation = _real

    detail = admin.get(f"/api/enterprise/approvals/{rec['id']}").json()["data"]
    assert detail["approval_status"] == "approved"  # 失败不伪装成 executed
    assert detail["execution_error"]
    assert detail["retry_count"] >= 1


def test_retry_after_failure_succeeds_once(fresh_db):
    from app.approvals.service import execute_operation as _real

    admin = _login(TestClient(main.app), "admin")
    promote(admin, "lead", "security_admin")
    lead = _login(TestClient(main.app), "lead")
    rec = _submit(admin, "tenant.member.role_change", "tenant_member",
                  "mem_user_observer_default", "k-retry", summary={"new_role": "reviewer"})
    assert _approve(lead, rec["id"]) is None  # 提交者=admin，审批人=lead（非自审）
    import app.approvals.service as svc

    calls = {"n": 0}

    def flaky(context, user, operation, resource_id, request_summary=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("第一次失败")
        return _real(context, user, operation, resource_id, request_summary)
    svc.execute_operation = flaky
    try:
        assert _execute(admin, rec["id"], confirm=True) == 409
        assert _execute(admin, rec["id"], confirm=True) == 200
        assert _execute(admin, rec["id"], confirm=True) == 409  # executed 后不可再执行
    finally:
        svc.execute_operation = _real

    from app.tenancy import store as ts

    member = ts.get_membership("mem_user_observer_default")
    assert member.role == "reviewer"  # 副作用只发生一次
    detail = admin.get(f"/api/enterprise/approvals/{rec['id']}").json()["data"]
    assert detail["approval_status"] == "executed"
    assert calls["n"] == 2  # 1 失败 + 1 成功，无重复成功


# ---------------------------------------------------------------------------
# 8. 重启恢复
# ---------------------------------------------------------------------------


def test_restart_restores_approval_state(fresh_db):
    dev = _login(TestClient(main.app), "developer")
    admin = _login(TestClient(main.app), "admin")
    pending = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service", "k-r1")
    run = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service", "k-r2")
    _approve(admin, run["id"])
    assert _execute(admin, run["id"]) == 200

    # 模拟重启：清空进程缓存后从 DB 恢复
    approval_store.APPROVALS.clear()
    approval_store.restore_from_db()
    assert approval_store.get(pending["id"]).approval_status == "pending"
    assert approval_store.get(run["id"]).approval_status == "executed"


# ---------------------------------------------------------------------------
# 9/10. 企业模式旧入口门禁 vs 团队模式兼容
# ---------------------------------------------------------------------------


def test_enterprise_legacy_rebuild_not_bypassed(fresh_db, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr("app.core.runtime_config.load_runtime_settings",
                        lambda: SimpleNamespace(app_env="enterprise", use_celery=False, celery_broker_url=""))
    admin = _login(TestClient(main.app), "admin")
    before = len(approval_store.APPROVALS)
    resp = admin.post("/api/repositories/repo_payment_service/index/rebuild", json={})
    assert resp.status_code == 200, resp.text
    # 企业模式应先建审批单，不直接触发旧入口副作用
    assert len(approval_store.APPROVALS) == before + 1


def test_team_mode_legacy_rebuild_not_gated(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr("app.core.runtime_config.load_runtime_settings",
                        lambda: SimpleNamespace(app_env="local", use_celery=False, celery_broker_url=""))
    admin = _login(TestClient(main.app), "admin")
    before = len(approval_store.APPROVALS)
    resp = admin.post("/api/repositories/repo_payment_service/index/rebuild", json={})
    # 团队模式走原语义（可能网络/离线在兼容模式返回）；关键是不建审批单
    assert resp.status_code in (200, 502, 503), resp.text
    assert len(approval_store.APPROVALS) == before


# ---------------------------------------------------------------------------
# 11/12/13. 跨租户 + 失败审计租户归属 + 敏感递归清洗
# ---------------------------------------------------------------------------


def test_cross_tenant_get_and_execute_denied():
    admin = _login(TestClient(main.app), "admin")
    resp_t = admin.post("/api/enterprise/tenants", json={"name": "TenantZZ"})
    assert resp_t.status_code == 200
    tenant_b = resp_t.json()["data"]["id"]

    rec = _submit(admin, "repository.index.rebuild", "repository", "repo_payment_service", "k-xt")
    switch = admin.post("/api/enterprise/tenant-context/switch", json={"tenant_id": tenant_b})
    assert switch.status_code == 200
    assert admin.get(f"/api/enterprise/approvals/{rec['id']}").status_code == 403
    assert _execute(admin, rec["id"]) == 403


def test_execution_failure_audit_has_tenant(fresh_db):
    from app.approvals.service import execute_operation as _real

    dev = _login(TestClient(main.app), "developer")
    admin = _login(TestClient(main.app), "admin")
    rec = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service", "k-aud")
    _approve(admin, rec["id"])
    import app.approvals.service as svc

    def bad(context, user, operation, resource_id, request_summary=None):
        raise ValueError("boom")
    svc.execute_operation = bad
    try:
        assert _execute(admin, rec["id"]) == 409
    finally:
        svc.execute_operation = _real
    events = [e for e in main.AUDIT_EVENTS.values() if e.event_type and e.event_type.startswith("approval.")]
    assert events
    assert all(e.tenant_id == "tenant_default" for e in events)


def test_sensitive_fields_recursively_scrubbed():
    dev = _login(TestClient(main.app), "developer")
    summary = {
        "note": "ok",
        "body": {"nested_token": "x", "keep": 1},
        "list": [{"password": "y"}, {"safe": 2}],
        "jwt": "zz",
    }
    rec = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service",
                  "k-secret", summary=summary)
    detail = admin = _login(TestClient(main.app), "admin")
    stored = detail.get(f"/api/enterprise/approvals/{rec['id']}").json()["data"]["request_summary"]
    raw = str(stored).lower()
    for forbidden in ("token", "password", "jwt", "secret", "cookie", "recovery", "credential", "private_key"):
        assert forbidden not in raw
    assert stored["note"] == "ok"
    assert stored["body"]["keep"] == 1
    assert stored["list"][1]["safe"] == 2


# ---------------------------------------------------------------------------
# 14. SQLite 字段读写兼容（JSON/Boolean/Datetime）
# ---------------------------------------------------------------------------


def test_sqlite_field_round_trip(fresh_db):
    admin = _login(TestClient(main.app), "admin")
    summary = {"role": "reviewer", "tags": ["a", "b"], "nested": {"x": 1}}
    rec = _submit(admin, "tenant.member.role_change", "tenant_member",
                  "mem_user_observer_default", "k-rt", summary=summary)
    approval_store.APPROVALS.clear()
    loaded = approval_store.get(rec["id"])
    assert loaded.request_summary == summary
    assert loaded.secondary_confirmation in (True, False)
    assert loaded.created_at is not None and loaded.expires_at is not None
    assert loaded.version == 1


# ---------------------------------------------------------------------------
# 15/16. DB 故障内存降级 + stale executing 超时回收
# ---------------------------------------------------------------------------


def test_db_failure_memory_downgrade_create_not_persisted(fresh_db, monkeypatch):
    from app.db import repositories as repos
    from app.db.repositories import load_approval

    def _boom(*args, **kwargs):
        raise RuntimeError("db connection lost")
    monkeypatch.setattr(repos, "upsert_approval", _boom)
    dev = _login(TestClient(main.app), "developer")
    rec = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service", "k-dbfail")
    # 内存真源仍返回 200，但不伪造 executed；
    assert approval_store.get(rec["id"]) is not None
    # DB 故障时未持久化：清除缓存后无法从 DB 读回
    approval_store.APPROVALS.clear()
    assert load_approval(rec["id"]) is None


def test_stale_executing_recovered_on_read(fresh_db):
    dev = _login(TestClient(main.app), "developer")
    admin = _login(TestClient(main.app), "admin")
    rec = _submit(dev, "repository.index.rebuild", "repository", "repo_payment_service", "k-stale")
    _approve(admin, rec["id"])
    record = approval_store.get(rec["id"])
    old = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=10)
    outcome, claimed = approval_store.claim_execution(rec["id"], record.version, "att_stale", old)
    assert outcome == "claimed"
    # 读取详情触发超时回收：executing -> approved（可重试），避免 DB 永久卡 executing
    detail = admin.get(f"/api/enterprise/approvals/{rec['id']}").json()["data"]
    assert detail["approval_status"] == "approved"
    assert "超时回收" in (detail.get("execution_error") or "")


# ---------------------------------------------------------------------------
# 17. L4 全链路（提升 security_admin 后）自动佐证默认种子的端到端可行
# ---------------------------------------------------------------------------


def test_l4_full_chain_with_promoted_security_admin(fresh_db):
    admin = _login(TestClient(main.app), "admin")
    promote(admin, "lead", "security_admin")
    lead = _login(TestClient(main.app), "lead")
    rec = _submit(admin, "tenant.member.role_change", "tenant_member",
                  "mem_user_observer_default", "k-l4chain", summary={"new_role": "reviewer"})
    assert rec["approval_mode"] == "security"
    assert _approve(lead, rec["id"]) is None  # 审批人 security_admin（非自审）
    assert _execute(admin, rec["id"], confirm=False) == 422  # L4 无二次确认被拒
    assert _execute(admin, rec["id"], confirm=True) == 200
    from app.tenancy import store as ts

    assert ts.get_membership("mem_user_observer_default").role == "reviewer"


# ---------------------------------------------------------------------------
# 18. 评审旁路修复：成员角色直改不绕过特权治理门禁
# ---------------------------------------------------------------------------


def test_privileged_role_elevation_is_owner_only_and_audited():
    admin = _login(TestClient(main.app), "admin")
    promote(admin, "lead", "tenant_admin")  # lead 成为非 owner 的租户管理员
    lead = _login(TestClient(main.app), "lead")

    obs_membership = "mem_user_observer_default"
    # tenant_admin（非 owner）改普通成员角色 -> 200
    assert lead.patch(f"/api/enterprise/members/{obs_membership}",
                      json={"role": "developer"}).status_code == 200
    # tenant_admin（非 owner）提升为特权角色 security_admin -> 403（仅 owner）
    assert lead.patch(f"/api/enterprise/members/{obs_membership}",
                      json={"role": "security_admin"}).status_code == 403
    # tenant_admin（非 owner）禁用特权成员（owner）-> 403
    assert lead.patch("/api/enterprise/members/mem_user_admin_default",
                      json={"enabled": False}).status_code == 403
    # owner 可授予特权角色 -> 200
    assert admin.patch(f"/api/enterprise/members/{obs_membership}",
                       json={"role": "security_admin"}).status_code == 200

    # 角色变更进入 tenant-scoped 强审计（tenant.member.role_changed）
    role_events = [e for e in main.AUDIT_EVENTS.values()
                   if e.event_type == "tenant.member.role_changed" and e.resource_id == obs_membership]
    assert role_events, "成员角色变更未写入审计"
    assert all(e.tenant_id == "tenant_default" for e in role_events)
    # 普通成员角色变更由 tenant_admin 完成
    assert any("auditor -> developer" in e.summary and "操作者 tenant_admin" in e.summary for e in role_events)
    # 特权授予由 owner 完成且带「特权角色」标记
    assert any("developer -> security_admin" in e.summary
               and "特权角色" in e.summary
               and "操作者 tenant_owner" in e.summary for e in role_events)


def test_create_privileged_member_is_owner_only():
    admin = _login(TestClient(main.app), "admin")
    promote(admin, "lead", "tenant_admin")
    lead = _login(TestClient(main.app), "lead")
    # 先登录触发租户种子（_ensure_seed 只为 USERS 中既有用户建成员），
    # 再注入不在租户内的合成用户，确保 create 是真正的"非成员铸造"
    main.USERS["user_alpha"] = main.UserRecord(
        id="user_alpha", name="Alpha", email="alpha@company.com", role="developer", password_hash="x")
    main.USERS["user_beta"] = main.UserRecord(
        id="user_beta", name="Beta", email="beta@company.com", role="developer", password_hash="x")

    # 非 owner 的 tenant_admin 不能铸造特权成员 security_admin -> 403
    assert lead.post("/api/enterprise/members",
                     json={"user_id": "user_alpha", "role": "security_admin"}).status_code == 403
    # 非 owner 可添加普通成员 -> 200
    assert lead.post("/api/enterprise/members",
                     json={"user_id": "user_alpha", "role": "developer"}).status_code == 200
    # owner 可铸造特权成员 -> 200
    assert admin.post("/api/enterprise/members",
                      json={"user_id": "user_beta", "role": "security_admin"}).status_code == 200
    from app.tenancy import store as ts

    assert ts.membership_of("user_beta", "tenant_default").role == "security_admin"
    # 清理注入的合成用户，避免污染后续用例
    main.USERS.pop("user_alpha", None)
    main.USERS.pop("user_beta", None)


def promote(admin_c, user_key, role):
    mid = f"mem_user_{user_key}_default"
    assert admin_c.patch(f"/api/enterprise/members/{mid}", json={"role": role}).status_code == 200