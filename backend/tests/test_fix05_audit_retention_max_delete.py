"""FIX-05 审计保留 `max_delete` 契约回归。

验收：`--max-delete 0 --apply` 绝不删除任何事件；0/负数在任何内存/DB 变更前拒绝；
正数仍遵守策略封顶与最早优先；CLI 退出码可区分参数错误。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import main

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def _seed(event_id: str, *, created_at=None, tenant_id: str = "tenant_default"):
    event = main.AuditEventRecord(
        id=event_id,
        event_type="task.analysis_completed",
        tenant_id=tenant_id,
        resource_type="task",
        resource_id=event_id,
        actor_id="user_admin",
        actor_name="管理员",
        summary=f"seed {event_id}",
        diff_summary=[],
        created_at=created_at or (datetime.now(timezone.utc) - timedelta(days=100)),
        execution_step_ids=[],
    )
    main.AUDIT_EVENTS[event.id] = event
    return event


def test_max_delete_zero_and_negative_are_rejected_without_deleting_memory():
    from app.tenancy import audit_retention

    old = datetime.now(timezone.utc) - timedelta(days=100)
    for i in range(3):
        _seed(f"audit_md_{i}", created_at=old + timedelta(minutes=i))

    for invalid in (0, -1, -99):
        result = audit_retention.prune_audit_events(30, dry_run=False, max_delete=invalid)
        assert result["refused"] is True
        assert result["error_code"] == "invalid_max_delete"
        assert result["deleted"] == 0
        assert result["deleted_memory"] == 0 and result["deleted_db"] == 0
        assert len(main.AUDIT_EVENTS) == 3, f"max_delete={invalid} 不得删除任何事件"


def test_positive_max_delete_still_caps_and_deletes_earliest_first():
    from app.tenancy import audit_retention

    base = datetime.now(timezone.utc) - timedelta(days=100)
    for i in range(3):
        _seed(f"audit_pos_{i}", created_at=base + timedelta(minutes=i))

    result = audit_retention.prune_audit_events(30, dry_run=False, max_delete=2)
    assert result["refused"] is False and result["deleted"] == 2
    assert result["effective_max_delete"] == 2 and result["truncated"] is True
    remaining = {eid for eid in main.AUDIT_EVENTS if eid.startswith("audit_pos_")}
    assert remaining == {"audit_pos_2"}, "必须最早优先，仅保留最新一条"


def test_requested_cap_above_policy_is_reported_and_clamped(monkeypatch):
    from app.tenancy import audit_retention

    monkeypatch.setenv("AUDIT_RETENTION_MAX_DELETE", "2")
    base = datetime.now(timezone.utc) - timedelta(days=100)
    for i in range(3):
        _seed(f"audit_clamp_{i}", created_at=base + timedelta(minutes=i))

    result = audit_retention.prune_audit_events(30, dry_run=False, max_delete=99)
    assert result["effective_max_delete"] == 2
    assert result["capped_by_policy"] is True
    assert result["deleted"] == 2


def test_sqlite_apply_with_zero_does_not_delete(persistent_db):
    from app.db.repositories import load_audit_events, upsert_audit_event
    from app.tenancy import audit_retention

    old = datetime.now(timezone.utc) - timedelta(days=100)
    upsert_audit_event(_seed("audit_db_zero", created_at=old))
    before = {row["id"] for row in load_audit_events()}
    assert before == {"audit_db_zero"}

    result = audit_retention.prune_audit_events(30, dry_run=False, max_delete=0)
    assert result["refused"] is True and result["deleted_db"] == 0
    assert {row["id"] for row in load_audit_events()} == before


def test_cli_apply_with_zero_exits_2_and_keeps_database(persistent_db):
    from app.db.repositories import load_audit_events, upsert_audit_event

    old = datetime.now(timezone.utc) - timedelta(days=100)
    upsert_audit_event(_seed("audit_cli_zero", created_at=old))

    env = dict(os.environ)
    env["USE_SQLALCHEMY"] = "true"
    env["ENTERPRISE_SHARED_PERSISTENCE"] = ""
    env["DATABASE_URL"] = persistent_db
    env["ENV_FILE"] = str(BACKEND_ROOT / "tests" / ".env.hermetic-nonexistent")
    proc = subprocess.run(
        [PY, "-m", "scripts.audit_retention", "--days", "90", "--apply",
         "--max-delete", "0", "--json"],
        cwd=str(BACKEND_ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 2, proc.stderr[-1500:]
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["error_code"] == "invalid_max_delete"
    assert payload["deleted"] == 0
    assert {row["id"] for row in load_audit_events()} == {"audit_cli_zero"}


def test_cli_negative_max_delete_exits_2_and_keeps_database(persistent_db):
    from app.db.repositories import load_audit_events, upsert_audit_event

    old = datetime.now(timezone.utc) - timedelta(days=100)
    upsert_audit_event(_seed("audit_cli_negative", created_at=old))

    env = dict(os.environ)
    env["USE_SQLALCHEMY"] = "true"
    env["ENTERPRISE_SHARED_PERSISTENCE"] = ""
    env["DATABASE_URL"] = persistent_db
    env["ENV_FILE"] = str(BACKEND_ROOT / "tests" / ".env.hermetic-nonexistent")
    proc = subprocess.run(
        [PY, "-m", "scripts.audit_retention", "--days", "90", "--apply",
         "--max-delete", "-3", "--json"],
        cwd=str(BACKEND_ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 2, proc.stderr[-1500:]
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["error_code"] == "invalid_max_delete" and payload["deleted"] == 0
    assert {row["id"] for row in load_audit_events()} == {"audit_cli_negative"}
