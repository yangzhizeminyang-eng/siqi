"""FIX-01 RAG 在线评测 run 持久化、幂等、失败终态与 API 回读。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main
from app.enterprise_rag import eval_service, eval_store
from app.enterprise_rag.eval_store import EvaluationRun

ACCOUNTS = {
    "admin": ("admin@company.com", "Admin123!"),
}


def _client(who: str = "admin") -> TestClient:
    client = TestClient(main.app)
    account, password = ACCOUNTS[who]
    assert client.post("/api/auth/login", json={
        "account": account, "password": password}).status_code == 200
    return client


def _run(run_id: str = "rageval_fix01", **overrides) -> EvaluationRun:
    values = {
        "run_id": run_id,
        "tenant_id": "tenant_default",
        "dataset": "e3_05_baseline.jsonl",
        "retrieval_mode": "bm25",
        "top_k": 8,
        "execution_scope": "isolated",
        "requested_by": "user_admin",
        "requested_role": "tenant_owner",
    }
    values.update(overrides)
    return EvaluationRun(**values)


def _minimal_report() -> dict:
    return {
        "validation_status": "local_only",
        "summary": {
            "total_cases": 2, "passed_cases": 1, "success_cases": 1,
            "recall_at_k": 0.5, "precision_at_k": 0.5, "mrr": 0.5,
            "ndcg_at_k": 0.5, "latency_p50_ms": 1.0, "latency_p95_ms": 2.0,
        },
    }


@pytest.fixture()
def reports(tmp_path, monkeypatch):
    import app.enterprise_rag.routes as rag_routes

    target = tmp_path / "reports"
    target.mkdir()
    monkeypatch.setattr(rag_routes, "REPORTS_DIR", target)
    return target


@pytest.fixture()
def fast_eval(monkeypatch):
    monkeypatch.setattr(eval_service, "_seed_baseline", lambda: None)
    monkeypatch.setattr(eval_service, "load_cases", lambda _path: [])
    monkeypatch.setattr(eval_service, "evaluate_dataset", lambda *a, **k: _minimal_report())


def test_run_is_persisted_and_api_reads_after_memory_reset(shared_db):
    eval_store.create(_run("rageval_fix01_persist"))
    eval_store.reset_store()

    loaded = eval_store.get("rageval_fix01_persist")
    assert loaded is not None and loaded.status == "queued"
    assert loaded.tenant_id == "tenant_default"

    admin = _client()
    resp = admin.get("/api/enterprise/rag/evaluations/rageval_fix01_persist")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["run"]["status"] == "queued"


def test_duplicate_delivery_is_idempotent_and_terminal(shared_db, reports, fast_eval, monkeypatch):
    calls = {"count": 0}

    def _evaluate(*args, **kwargs):
        calls["count"] += 1
        return _minimal_report()

    monkeypatch.setattr(eval_service, "evaluate_dataset", _evaluate)
    eval_store.create(_run("rageval_fix01_idem"))

    first = eval_service.run_evaluation("rageval_fix01_idem")
    second = eval_service.run_evaluation("rageval_fix01_idem")

    assert first["status"] == "succeeded"
    assert second["status"] == "succeeded"
    assert calls["count"] == 1, "重复 delivery 不得重复执行评测"
    persisted = eval_store.get("rageval_fix01_idem")
    assert persisted.attempts == 1
    assert persisted.report_name == "online_rageval_fix01_idem.json"
    assert (reports / persisted.report_name).is_file()


def test_stale_running_run_is_reclaimed_and_attempts_increment(shared_db):
    eval_store.create(_run("rageval_fix01_stale"))
    t0 = datetime.now(timezone.utc)
    outcome, run = eval_store.claim_for_execution("rageval_fix01_stale", now=t0, lease_seconds=5)
    assert outcome == "claimed" and run.attempts == 1
    assert run.started_at == t0

    busy, _ = eval_store.claim_for_execution(
        "rageval_fix01_stale", now=t0 + timedelta(seconds=1), lease_seconds=5)
    assert busy == "busy"

    reclaimed, run2 = eval_store.claim_for_execution(
        "rageval_fix01_stale", now=t0 + timedelta(seconds=10), lease_seconds=5)
    assert reclaimed == "claimed" and run2.attempts == 2
    assert run2.started_at == t0 + timedelta(seconds=10)


def test_busy_run_returns_retry_after_instead_of_ack(shared_db, monkeypatch):
    """Worker 崩溃后消息在租约内重投：busy 必须返回重试时间，不能直接 ack。"""
    monkeypatch.setenv("RAG_EVAL_RUN_LEASE_SECONDS", "30")
    eval_store.create(_run("rageval_fix01_busy"))
    t0 = datetime.now(timezone.utc)
    outcome, _ = eval_store.claim_for_execution(
        "rageval_fix01_busy", now=t0, lease_seconds=30)
    assert outcome == "claimed"

    result = eval_service.run_evaluation("rageval_fix01_busy")
    assert result["status"] == "busy"
    assert result["retry_after_seconds"] >= 1
    persisted = eval_store.get("rageval_fix01_busy")
    assert persisted.status == "running" and persisted.attempts == 1


def test_retry_if_busy_calls_celery_retry_with_remaining_lease():
    from app.workers.tasks import _retry_if_busy

    class _RetrySignal(Exception):
        pass

    class _FakeTask:
        max_retries = 3

        def retry(self, **kwargs):
            raise _RetrySignal(kwargs)

    # 非 busy 不重试
    assert _retry_if_busy(_FakeTask(), {"status": "succeeded"}) is None

    with pytest.raises(_RetrySignal) as excinfo:
        _retry_if_busy(_FakeTask(), {"status": "busy", "retry_after_seconds": 7})
    assert excinfo.value.args[0]["countdown"] == 7
    assert excinfo.value.args[0]["max_retries"] == 3


def test_concurrent_claim_only_one_worker_wins(shared_db):
    from concurrent.futures import ThreadPoolExecutor

    eval_store.create(_run("rageval_fix01_concurrent"))
    t0 = datetime.now(timezone.utc)

    def _claim(_index: int) -> str:
        outcome, _run_obj = eval_store.claim_for_execution(
            "rageval_fix01_concurrent", now=t0, lease_seconds=60)
        return outcome

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(_claim, range(2)))

    assert outcomes.count("claimed") == 1, f"并发 claim 只能有一个赢家：{outcomes}"
    assert outcomes.count("busy") == 1
    persisted = eval_store.get("rageval_fix01_concurrent")
    assert persisted.attempts == 1


def test_worker_failure_marks_failed_with_scrubbed_error(shared_db, reports, fast_eval, monkeypatch):
    secret = "fix01-secret-token-abcdef0123456789"
    monkeypatch.setenv("GITEE_TOKEN", secret)

    def _boom(_path):
        raise RuntimeError(f"boom url=https://oauth2:{secret}@example.com/repo.git")

    monkeypatch.setattr(eval_service, "load_cases", _boom)
    eval_store.create(_run("rageval_fix01_fail"))

    result = eval_service.run_evaluation("rageval_fix01_fail")
    assert result["status"] == "failed"
    assert "RuntimeError" in result["error"]
    assert secret not in result["error"]
    assert result["finished_at"] is not None

    eval_store.reset_store()
    persisted = eval_store.get("rageval_fix01_fail")
    assert persisted.status == "failed" and persisted.error
    failed_audits = [e for e in main.AUDIT_EVENTS.values()
                     if e.event_type == "tenant.rag.eval.failed"]
    assert failed_audits, "失败必须留审计"


def test_report_write_failure_ends_in_failed_not_running(shared_db, reports, fast_eval, monkeypatch):
    def _boom(_report, _path):
        raise OSError("report disk full")

    monkeypatch.setattr(eval_service, "write_report", _boom)
    eval_store.create(_run("rageval_fix01_report_fail"))

    result = eval_service.run_evaluation("rageval_fix01_report_fail")
    assert result["status"] == "failed"
    assert "report disk full" in result["error"]
    assert result["finished_at"] is not None


def test_missing_run_returns_missing(shared_db):
    assert eval_service.run_evaluation("rageval_missing") == {
        "run_id": "rageval_missing", "status": "missing"}


def test_cross_tenant_read_is_denied(shared_db):
    eval_store.create(_run("rageval_fix01_tenant_b", tenant_id="tenant_b"))
    other_ctx = eval_service._execution_ctx(_run("rageval_fix01_tenant_b", tenant_id="tenant_b"))
    assert other_ctx.tenant_id == "tenant_b"

    admin = _client()
    assert admin.get(
        "/api/enterprise/rag/evaluations/rageval_fix01_tenant_b").status_code == 404

    from app.tenancy.models import TenantContext
    from app.tenancy.permissions import permissions_for
    default_ctx = TenantContext(
        user_id="user_admin", tenant_id="tenant_default", tenant_name="默认租户",
        role="tenant_owner", permissions=permissions_for("tenant_owner"))
    with pytest.raises(HTTPException) as excinfo:
        eval_service.get_run(default_ctx, "rageval_fix01_tenant_b")
    assert excinfo.value.status_code == 404


def test_api_trigger_persists_queued_then_worker_terminal_is_visible(shared_db, reports, fast_eval, monkeypatch):
    from app.workers import dispatch, tasks as worker_tasks

    class _FakeAsyncResult:
        id = "fix01-async-1"

    calls: dict = {}

    def _fake_delay(**kwargs):
        calls.update(kwargs)
        return _FakeAsyncResult()

    monkeypatch.setattr(dispatch, "celery_enabled", lambda: True)
    monkeypatch.setattr(worker_tasks.run_rag_evaluation, "delay", _fake_delay)

    admin = _client()
    resp = admin.post("/api/enterprise/rag/evaluations", json={
        "dataset": "e3_05_baseline.jsonl", "retrieval_mode": "bm25"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    run_id = data["run"]["run_id"]
    assert data["dispatch"]["enqueued"] is True
    assert data["run"]["status"] == "queued"

    # API 进程清空内存后仍能从 DB 读到 queued（投递前已持久化）。
    eval_store.reset_store()
    assert eval_store.get(run_id).status == "queued"

    # Worker/独立进程推进终态；API 进程清空内存后回读 DB。
    finished = eval_service.run_evaluation(run_id)
    assert finished["status"] == "succeeded"
    eval_store.reset_store()
    status = admin.get(f"/api/enterprise/rag/evaluations/{run_id}")
    assert status.status_code == 200
    assert status.json()["data"]["run"]["status"] == "succeeded"
    assert status.json()["data"]["run"]["finished_at"]
    assert calls == {"run_id": run_id}
