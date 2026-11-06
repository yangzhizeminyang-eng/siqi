"""OPT-14 审计导出 / 审计保留 / 身份认证边界：

1. 审计导出 `GET /api/audit-events/export`：JSONL/CSV、租户作用域、审计可见性门、
   过滤与截断诚实标注（X-Audit-Export-* 头）、导出动作本身留审计；
2. 审计保留：策略读取（AUDIT_RETENTION_*）、plan 只读统计、prune 默认 dry-run、
   非正天数与低于 min_days 拒绝、max_delete 上限、SQLite 持久化往返、CLI dry-run 默认；
3. 身份边界：AUTH_MODE 仅支持 local；非 local（OIDC/MFA 未实现）必须 fail-closed
   拒绝启动，绝不静默降级。

TDD：本文件先于实现落地（红）。
"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

ACCOUNTS = {
    "admin": ("admin@company.com", "Admin123!"),
    "developer": ("developer@company.com", "Dev123!"),
    "observer": ("observer@company.com", "Observer123!"),
}


def _login(who: str) -> TestClient:
    client = TestClient(main.app)
    account, password = ACCOUNTS[who]
    resp = client.post("/api/auth/login", json={"account": account, "password": password})
    assert resp.status_code == 200, resp.text
    return client


def _seed_event(
    event_id: str,
    *,
    tenant_id: str = "tenant_default",
    event_type: str = "task.analysis_completed",
    resource_type: str = "task",
    resource_id: str = "T-1",
    created_at=None,
    summary: str | None = None,
) -> main.AuditEventRecord:
    event = main.AuditEventRecord(
        id=event_id,
        event_type=event_type,
        tenant_id=tenant_id,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_id="user_admin",
        actor_name="管理员",
        summary=summary or f"seed {event_id}",
        diff_summary=[],
        created_at=created_at or main.DEFAULT_NOW,
        execution_step_ids=[],
    )
    main.AUDIT_EVENTS[event.id] = event
    return event


def _jsonl_ids(body: str) -> set[str]:
    return {json.loads(line)["id"] for line in body.strip().splitlines() if line}


@pytest.fixture()
def sqlite_persistence(tmp_path, monkeypatch):
    """启用真实 SQLite 持久化（含引擎缓存清理，避免跨用例污染）。"""
    db_path = tmp_path / "opt14_persist.db"
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("PERSIST_STRICT", "false")
    from app.db import session as db_session
    from app.models.orm import Base

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    Base.metadata.create_all(db_session.get_engine())
    yield db_path
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()


# ---------------------------------------------------------------------------
# 1. 审计导出
# ---------------------------------------------------------------------------


def test_export_jsonl_default_returns_attachment_and_rows():
    admin = _login("admin")
    _seed_event("audit_e1")
    _seed_event("audit_e2", event_type="tenant.member_added")

    resp = admin.get("/api/audit-events/export")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.headers["x-audit-export-count"] == "2"
    assert resp.headers["x-audit-export-truncated"] == "false"
    assert resp.headers["x-audit-export-format"] == "jsonl"

    lines = [json.loads(line) for line in resp.text.strip().splitlines() if line]
    assert {row["id"] for row in lines} == {"audit_e1", "audit_e2"}
    assert {"id", "event_type", "tenant_id", "resource_type", "resource_id",
            "actor_id", "actor_name", "summary", "created_at"} <= set(lines[0])


def test_export_csv_format_has_header_and_rows():
    admin = _login("admin")
    _seed_event("audit_csv1")
    _seed_event("audit_csv2")

    resp = admin.get("/api/audit-events/export", params={"format": "csv"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["x-audit-export-format"] == "csv"

    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0][0] == "id"
    assert {row[0] for row in rows[1:]} == {"audit_csv1", "audit_csv2"}


def test_export_filters_event_type_and_time_window():
    admin = _login("admin")
    old_at = main.DEFAULT_NOW - timedelta(days=10)
    _seed_event("audit_old", event_type="task.cancelled", created_at=old_at)
    _seed_event("audit_new", event_type="task.cancelled")
    _seed_event("audit_other", event_type="tenant.member_added")

    by_type = admin.get("/api/audit-events/export", params={"event_type": "task.cancelled"})
    assert _jsonl_ids(by_type.text) == {"audit_old", "audit_new"}

    # 每次导出都会写一条 tenant.audit.exported 自审计事件（见 test_export_writes_its_own_audit_event），
    # 其 created_at = DEFAULT_NOW 必然命中 since 窗口 —— 精确断言并入此前已产生的自审计事件。
    exported_so_far = {e.id for e in main.AUDIT_EVENTS.values()
                       if e.event_type == "tenant.audit.exported"}
    since_iso = (main.DEFAULT_NOW - timedelta(days=1)).isoformat()
    by_since = admin.get("/api/audit-events/export", params={"since": since_iso})
    assert _jsonl_ids(by_since.text) == {"audit_new", "audit_other"} | exported_so_far

    until_iso = (main.DEFAULT_NOW - timedelta(days=1)).isoformat()
    by_until = admin.get("/api/audit-events/export", params={"until": until_iso})
    assert _jsonl_ids(by_until.text) == {"audit_old"}


def test_export_limit_truncation_is_honest():
    admin = _login("admin")
    for i in range(5):
        _seed_event(f"audit_t{i}")

    resp = admin.get("/api/audit-events/export", params={"limit": 2})
    assert resp.status_code == 200, resp.text
    assert resp.headers["x-audit-export-count"] == "2"
    assert resp.headers["x-audit-export-truncated"] == "true"
    assert len(_jsonl_ids(resp.text)) == 2


def test_export_limit_is_bounded_and_params_validated():
    admin = _login("admin")
    assert admin.get("/api/audit-events/export", params={"limit": 0}).status_code == 422
    assert admin.get("/api/audit-events/export", params={"limit": 99999}).status_code == 422
    assert admin.get("/api/audit-events/export", params={"format": "xml"}).status_code == 422
    assert admin.get("/api/audit-events/export", params={"since": "not-a-date"}).status_code == 422


def test_export_permission_requires_audit_view():
    dev = _login("developer")
    assert dev.get("/api/audit-events/export").status_code == 403

    observer = _login("observer")
    assert observer.get("/api/audit-events/export").status_code == 200


def test_export_writes_its_own_audit_event():
    admin = _login("admin")
    _seed_event("audit_e1")
    assert admin.get("/api/audit-events/export").status_code == 200

    exported = [e for e in main.AUDIT_EVENTS.values() if e.event_type == "tenant.audit.exported"]
    assert exported, "导出动作本身必须留审计"
    assert "count=1" in exported[0].summary


def test_export_reads_from_db_when_persistence_enabled(sqlite_persistence):
    from app.db.repositories import upsert_audit_event

    admin = _login("admin")
    event = _seed_event("audit_db1")
    upsert_audit_event(event)
    main.AUDIT_EVENTS.clear()  # 读取只能来自 DB

    resp = admin.get("/api/audit-events/export")
    assert resp.status_code == 200, resp.text
    assert _jsonl_ids(resp.text) == {"audit_db1"}


# ---------------------------------------------------------------------------
# 2. 审计保留
# ---------------------------------------------------------------------------


def test_retention_policy_defaults_are_safe():
    from app.tenancy import audit_retention

    policy = audit_retention.retention_policy()
    assert policy["days"] == 0, "默认必须永久保留（0=不清理）"
    assert policy["min_days"] >= 1
    assert policy["max_delete"] >= 1


def test_retention_plan_and_dry_run_do_not_delete():
    from app.tenancy import audit_retention

    old = main.DEFAULT_NOW - timedelta(days=100)
    _seed_event("audit_ancient", created_at=old)
    _seed_event("audit_recent")

    plan = audit_retention.build_plan(30)
    assert plan["candidates"] == 1
    assert plan["total"] == 2
    assert plan["oldest"] is not None

    result = audit_retention.prune_audit_events(30, dry_run=True)
    assert result["dry_run"] is True
    assert result["deleted"] == 0
    assert "audit_ancient" in main.AUDIT_EVENTS, "dry-run 不得删除任何事件"


def test_retention_apply_deletes_only_old_events():
    from app.tenancy import audit_retention

    old = main.DEFAULT_NOW - timedelta(days=100)
    _seed_event("audit_ancient", created_at=old)
    _seed_event("audit_recent")

    result = audit_retention.prune_audit_events(30, dry_run=False)
    assert result["deleted"] == 1
    assert "audit_ancient" not in main.AUDIT_EVENTS
    assert "audit_recent" in main.AUDIT_EVENTS


def test_retention_refuses_unsafe_windows():
    from app.tenancy import audit_retention

    # 事件需早于被测试的窗口（7 天）才有资格被删：种子取 10 天前
    old = main.DEFAULT_NOW - timedelta(days=10)
    _seed_event("audit_10d", created_at=old)

    # 0 = 永久保留（策略语义），不得被当作「全部删除」
    refused_zero = audit_retention.prune_audit_events(0, dry_run=False)
    assert refused_zero["refused"] is True
    assert refused_zero["deleted"] == 0
    assert "audit_10d" in main.AUDIT_EVENTS

    # 低于 min_days 的窗口默认拒绝
    refused_min = audit_retention.prune_audit_events(7, dry_run=False)
    assert refused_min["refused"] is True
    assert "audit_10d" in main.AUDIT_EVENTS

    # 显式放行后仍受 max_delete 约束并可执行
    allowed = audit_retention.prune_audit_events(7, dry_run=False, allow_below_min=True)
    assert allowed["refused"] is False
    assert allowed["deleted"] == 1


def test_retention_respects_max_delete_cap():
    from app.tenancy import audit_retention

    old = main.DEFAULT_NOW - timedelta(days=100)
    for i in range(3):
        _seed_event(f"audit_old_{i}", created_at=old + timedelta(minutes=i))

    first = audit_retention.prune_audit_events(30, dry_run=False, max_delete=2)
    assert first["deleted"] == 2
    assert first["truncated"] is True

    second = audit_retention.prune_audit_events(30, dry_run=False, max_delete=2)
    assert second["deleted"] == 1
    remaining_old = [e for e in main.AUDIT_EVENTS.values() if e.id.startswith("audit_old_")]
    assert remaining_old == []


def test_retention_sqlite_roundtrip(sqlite_persistence):
    from app.db.repositories import load_audit_events, upsert_audit_event
    from app.tenancy import audit_retention

    old = main.DEFAULT_NOW - timedelta(days=100)
    upsert_audit_event(_seed_event("audit_db_old", created_at=old))
    upsert_audit_event(_seed_event("audit_db_new"))

    plan = audit_retention.build_plan(30)
    assert plan["candidates"] == 1

    result = audit_retention.prune_audit_events(30, dry_run=False)
    assert result["deleted_db"] == 1
    db_ids = {row["id"] for row in load_audit_events()}
    assert "audit_db_old" not in db_ids
    assert "audit_db_new" in db_ids


def test_retention_cli_defaults_to_dry_run():
    proc = subprocess.run(
        [PY, "-m", "scripts.audit_retention", "--days", "90", "--json"],
        cwd=str(BACKEND_ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["dry_run"] is True
    assert payload["deleted"] == 0

    # 缺少 --days 直接参数错误（不允许「默认全删」式调用）
    bad = subprocess.run(
        [PY, "-m", "scripts.audit_retention", "--apply"],
        cwd=str(BACKEND_ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert bad.returncode == 2


# ---------------------------------------------------------------------------
# 3. 身份认证边界（OIDC/MFA 未实现 -> fail-closed）
# ---------------------------------------------------------------------------


def _run_python(code: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if env_extra is not None:
        env.update(env_extra)
    return subprocess.run(
        [PY, "-c", code], cwd=str(BACKEND_ROOT), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )


def test_auth_mode_defaults_to_local():
    env = {k: v for k, v in os.environ.items() if k != "AUTH_MODE"}
    proc = subprocess.run(
        [PY, "-c", "from app.core.runtime_config import load_runtime_settings as L; print(L().auth_mode)"],
        cwd=str(BACKEND_ROOT), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert proc.stdout.strip() == "local"


def test_auth_mode_non_local_fails_closed():
    proc = _run_python(
        "from app.core.runtime_config import load_runtime_settings as L; L()",
        env_extra={"AUTH_MODE": "oidc"},
    )
    assert proc.returncode != 0, "AUTH_MODE=oidc（未实现）必须 fail-closed 拒绝启动"
    assert "AUTH_MODE" in proc.stderr
    assert "fail-closed" in proc.stderr
