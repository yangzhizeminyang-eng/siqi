"""FIX-04 审计事件时间戳：事件发生时间，而非模块导入时刻。

覆盖：
- 拒绝审计 / 审计导出自审计 / RAG 查询审计 / 在线评测触发/完成/失败审计；
- 冻结时钟推进后新事件时间跟随事件发生时刻；
- UTC aware 写入 → SQLite naive 挂钟读回 → aware UTC 比较；
- since/until 过滤与 retention cutoff 边界。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main
from app.core import clock

T0 = datetime(2030, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=3, minutes=7)


def _ctx(role: str = "tenant_owner"):
    from app.tenancy.models import TenantContext
    from app.tenancy.permissions import permissions_for

    return TenantContext(
        user_id="user_admin", tenant_id="tenant_default", tenant_name="默认租户",
        role=role, permissions=permissions_for(role))


def _clock_events(event_type: str) -> list:
    return [e for e in main.AUDIT_EVENTS.values() if e.event_type == event_type]


def test_denial_audit_uses_event_time_and_tracks_clock_advance():
    from app.tenancy.context import deny_and_audit

    clock.set_now_provider(lambda: T0)
    deny_and_audit(_ctx("developer"), "fix04.one", "第一次拒绝", resource_type="probe",
                   resource_id="fix04_p1")
    clock.set_now_provider(lambda: T1)
    deny_and_audit(_ctx("developer"), "fix04.two", "第二次拒绝", resource_type="probe",
                   resource_id="fix04_p2")

    events = [e for e in main.AUDIT_EVENTS.values()
              if e.resource_id in {"fix04_p1", "fix04_p2"}]
    by_id = {e.resource_id: e for e in events}
    assert by_id["fix04_p1"].created_at == T0
    assert by_id["fix04_p2"].created_at == T1
    assert by_id["fix04_p1"].created_at != main.DEFAULT_NOW, "不得复用模块导入时固定常量"


def test_member_and_rag_query_audit_use_event_time():
    from app.enterprise_rag.models import EnterpriseRagSearchPayload
    from app.enterprise_rag.service import _audit_rag_query
    from app.tenancy.context import audit_member_action

    clock.set_now_provider(lambda: T0)
    audit_member_action(_ctx(), "member.fix04", "u1", "成员动作")
    _audit_rag_query(_ctx(), EnterpriseRagSearchPayload(
        query="q", project_id="p", repository_id="r", commit_sha="c"), [], stale=False)

    member = _clock_events("tenant.member.fix04")[0]
    rag = _clock_events("rag.query")[0]
    assert member.created_at == T0 and rag.created_at == T0


def test_audit_export_event_uses_event_time(persistent_db):
    admin = TestClient(main.app)
    assert admin.post("/api/auth/login", json={
        "account": "admin@company.com", "password": "Admin123!"}).status_code == 200

    clock.set_now_provider(lambda: T1)
    assert admin.get("/api/audit-events/export").status_code == 200

    exported = _clock_events("tenant.audit.exported")
    assert exported, "导出动作必须自审计"
    assert exported[-1].created_at == T1


def test_eval_trigger_completed_and_failed_audit_times(monkeypatch):
    from app.enterprise_rag import eval_service, eval_store
    from app.enterprise_rag.eval_store import EvaluationRun
    from app.enterprise_rag.models import EnterpriseRagEvaluationPayload

    monkeypatch.setattr(eval_service, "_in_memory_mode", lambda: True)
    monkeypatch.setattr(eval_service, "_seed_baseline", lambda: None)
    monkeypatch.setattr(eval_service, "load_cases", lambda _path: [])
    monkeypatch.setattr(eval_service, "evaluate_dataset", lambda *a, **k: {
        "validation_status": "local_only",
        "summary": {"total_cases": 0, "passed_cases": 0},
    })

    clock.set_now_provider(lambda: T0)
    out = eval_service.trigger_evaluation(_ctx(), EnterpriseRagEvaluationPayload())
    assert out["run"]["status"] == "succeeded"
    for event_type in ("tenant.rag.eval.triggered", "tenant.rag.eval.completed"):
        events = _clock_events(event_type)
        assert events and events[0].created_at == T0, event_type

    eval_store.create(EvaluationRun(
        run_id="rageval_fix04_fail", tenant_id="tenant_default",
        dataset="e3_05_baseline.jsonl", requested_by="user_admin",
        requested_role="tenant_owner"))

    def _boom(_path):
        raise RuntimeError("fix04 eval boom")

    monkeypatch.setattr(eval_service, "load_cases", _boom)
    clock.set_now_provider(lambda: T1)
    result = eval_service.run_evaluation("rageval_fix04_fail")
    assert result["status"] == "failed"
    failed = _clock_events("tenant.rag.eval.failed")
    assert failed and failed[0].created_at == T1


def test_sqlite_utc_roundtrip_and_export_time_window(persistent_db):
    from app.db.repositories import load_audit_events, upsert_audit_event

    clock.set_now_provider(lambda: T0)
    old = main.AuditEventRecord(
        id="audit_fix04_old", event_type="task.lifecycle", tenant_id="tenant_default",
        resource_type="task", resource_id="old", actor_id="u", actor_name="",
        summary="old", diff_summary=[], created_at=T0 - timedelta(days=10),
        execution_step_ids=[])
    upsert_audit_event(old)
    loaded = {row["id"]: row for row in load_audit_events()}
    assert loaded["audit_fix04_old"]["created_at"].tzinfo is not None
    assert loaded["audit_fix04_old"]["created_at"] == T0 - timedelta(days=10)

    admin = TestClient(main.app)
    assert admin.post("/api/auth/login", json={
        "account": "admin@company.com", "password": "Admin123!"}).status_code == 200
    params = {
        "since": (T0 - timedelta(days=11)).isoformat(),
        "until": (T0 - timedelta(days=9)).isoformat(),
    }
    resp = admin.get("/api/audit-events/export", params=params)
    assert resp.status_code == 200
    ids = [line for line in resp.text.splitlines() if "audit_fix04_old" in line]
    assert ids, "UTC aware/naive 往返后 since/until 必须仍能命中"


def test_retention_cutoff_uses_event_times():
    from app.tenancy import audit_retention

    clock.set_now_provider(lambda: T0)
    for event_id, delta in (("audit_fix04_ancient", 100), ("audit_fix04_recent", 10)):
        main.AUDIT_EVENTS[event_id] = main.AuditEventRecord(
            id=event_id, event_type="task.lifecycle", tenant_id="tenant_default",
            resource_type="task", resource_id=event_id, actor_id="u", actor_name="",
            summary=event_id, diff_summary=[],
            created_at=T0 - timedelta(days=delta), execution_step_ids=[])

    plan = audit_retention.build_plan(30, now=T0)
    assert plan["candidates"] == 1
    assert plan["cutoff"] == (T0 - timedelta(days=30)).isoformat()
    result = audit_retention.prune_audit_events(30, dry_run=False, now=T0)
    assert result["deleted"] == 1
    assert "audit_fix04_ancient" not in main.AUDIT_EVENTS
    assert "audit_fix04_recent" in main.AUDIT_EVENTS


def test_audit_failure_is_logged_not_silently_swallowed(monkeypatch):
    from app.tenancy import context as tenancy_context

    warnings: list[str] = []

    def _boom(**kwargs):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(main, "AuditEventRecord", _boom)
    monkeypatch.setattr(
        tenancy_context.logger, "warning",
        lambda message, *args, **kwargs: warnings.append(message % args if args else message))
    clock.set_now_provider(lambda: T0)
    tenancy_context.deny_and_audit(
        _ctx("developer"), "fix04.fail", "审计失败", resource_type="probe",
        resource_id="fix04_fail")
    assert any("审计" in message for message in warnings), \
        "审计失败必须留下可观测日志，不能悄悄吞掉"
