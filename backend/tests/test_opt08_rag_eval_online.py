"""OPT-08 RAG 在线评测入口测试（TDD：先红后绿）。

覆盖验收口径「授权触发异步评测、状态查询和报告查看；默认仅隔离数据，生产评测另行确认」：
1. 权限：触发需 rag:evaluate（developer/auditor 403 + 审计）；状态/报告查看需 rag:search；
2. 默认隔离：trigger 默认 isolated（内存种子基线），eager 内联执行 → succeeded/local_only；
3. 异步契约：Celery 开启时触发只入队（queued），Worker 推进到终态；
4. 状态查询：run 详情/列表（时间戳、报告名、简报）与租户过滤；
5. 报告查看：在线报告写入 canonical 目录并复用既有 /reports/{name} 回读；
6. 生产门：production 未显式确认 → 422 + 拒绝审计、不建 run；确认后 require_milvus 语义
   （Milvus 不可用 → blocked，绝不冒称 verified）；
7. 失败路径：评测异常 → run failed，错误文本脱敏。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import main
import app.enterprise_rag.routes as rag_routes

ACCOUNTS = {
    "admin": ("admin@company.com", "Admin123!"),          # tenant_owner
    "developer": ("developer@company.com", "Dev123!"),    # developer（无 rag:evaluate）
    "auditor": ("observer@company.com", "Observer123!"),  # auditor（无 rag:search）
}


def _client(who: str) -> TestClient:
    c = TestClient(main.app)
    email, pwd = ACCOUNTS[who]
    assert c.post("/api/auth/login", json={"account": email, "password": pwd}).status_code == 200
    return c


@pytest.fixture()
def reports(tmp_path, monkeypatch):
    """临时 REPORTS_DIR：在线评测报告不写进真实 reports 目录（与 E3-08 同口径）。"""
    target = tmp_path / "reports"
    target.mkdir()
    monkeypatch.setattr(rag_routes, "REPORTS_DIR", target)
    return target


# ---------------------------------------------------------------------------
# 1. 权限目录
# ---------------------------------------------------------------------------


def test_rag_evaluate_permission_catalog():
    from app.tenancy.permissions import (
        RAG_EVALUATE,
        RAG_SEARCH,
        permissions_for,
    )

    assert RAG_EVALUATE == "rag:evaluate"
    for role in ("tenant_owner", "tenant_admin", "security_admin", "project_owner"):
        assert RAG_EVALUATE in permissions_for(role), role
    for role in ("developer", "reviewer", "auditor"):
        assert RAG_EVALUATE not in permissions_for(role), role
        assert RAG_SEARCH in permissions_for(role) or role == "auditor"


# ---------------------------------------------------------------------------
# 2. 数据集解析（白名单 + 防穿越）
# ---------------------------------------------------------------------------


def test_dataset_allowlist_rejects_traversal_and_missing():
    from fastapi import HTTPException

    from app.enterprise_rag import eval_service

    resolved = eval_service.resolve_dataset("e3_05_baseline.jsonl")
    assert resolved.name == "e3_05_baseline.jsonl"
    assert resolved.is_file()

    for bad in ("../seed.py", "..\\seed.py", "sub/dir.jsonl", "missing.jsonl", "seed.py"):
        with pytest.raises(HTTPException) as excinfo:
            eval_service.resolve_dataset(bad)
        assert excinfo.value.status_code == 422, bad


# ---------------------------------------------------------------------------
# 3. 触发权限（HTTP）
# ---------------------------------------------------------------------------


def test_trigger_requires_rag_evaluate_and_audits_denial(reports):
    dev = _client("developer")
    resp = dev.post("/api/enterprise/rag/evaluations", json={"dataset": "e3_05_baseline.jsonl"})
    assert resp.status_code == 403, resp.text

    denied = [e for e in main.AUDIT_EVENTS.values()
              if "rag:evaluate" in (e.summary or "")]
    assert denied, "触发被拒必须留有审计（权限缺失）"
    assert any("denied" in getattr(e, "event_type", "") for e in denied)

    auditor = _client("auditor")
    assert auditor.post("/api/enterprise/rag/evaluations",
                        json={"dataset": "e3_05_baseline.jsonl"}).status_code == 403


def test_status_and_reports_need_rag_search(reports):
    dev = _client("developer")
    assert dev.get("/api/enterprise/rag/evaluations").status_code == 200
    auditor = _client("auditor")
    assert auditor.get("/api/enterprise/rag/evaluations").status_code == 403


# ---------------------------------------------------------------------------
# 4. 默认隔离：触发 → 状态 → 报告 闭环（eager 内联）
# ---------------------------------------------------------------------------


def test_trigger_isolated_default_end_to_end(reports):
    from app.workers.tasks import run_rag_evaluation as task

    assert task.name == "backend.run_rag_evaluation", "Worker 任务已注册为独立入口"

    admin = _client("admin")
    resp = admin.post("/api/enterprise/rag/evaluations",
                      json={"dataset": "e3_05_baseline.jsonl", "retrieval_mode": "hybrid", "top_k": 8})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    run = data["run"]
    assert data["dispatch"]["mode"] == "eager(test)" and data["dispatch"]["enqueued"] is False
    assert run["execution_scope"] == "isolated" and run["tenant_id"] == "tenant_default"
    assert run["status"] == "succeeded", run
    assert run["validation_status"] == "local_only"
    assert run["report_name"].startswith("online_rageval_") and run["report_name"].endswith(".json")
    assert run["started_at"] and run["finished_at"] and run["created_at"]
    assert run["created_at"] <= run["started_at"] <= run["finished_at"]
    summary = run["summary"]
    assert summary["total_cases"] == 32 and summary["passed_cases"] == 29, summary

    # 状态查询（异步入口语义）
    status = admin.get(f"/api/enterprise/rag/evaluations/{run['run_id']}")
    assert status.status_code == 200
    assert status.json()["data"]["run"]["status"] == "succeeded"

    listed = admin.get("/api/enterprise/rag/evaluations")
    assert listed.status_code == 200
    assert [r["run_id"] for r in listed.json()["data"]["runs"]] == [run["run_id"]]

    # 报告查看（复用 E3-08 既有接口）
    view = admin.get(f"/api/enterprise/rag/reports/{run['report_name']}")
    assert view.status_code == 200, view.text
    report = view.json()["data"]
    assert report["validation_status"] == "local_only"
    assert report["dataset_size"] == 32
    assert report["dataset_sha256"]

    # 审计：触发 + 完成
    types = [e.event_type for e in main.AUDIT_EVENTS.values()]
    assert "tenant.rag.eval.triggered" in types
    assert "tenant.rag.eval.completed" in types


# ---------------------------------------------------------------------------
# 5. 异步契约：Celery 开启时只入队
# ---------------------------------------------------------------------------


def test_trigger_dispatches_async_and_worker_finishes(reports, monkeypatch):
    from app.enterprise_rag import eval_service
    from app.workers import dispatch, tasks as worker_tasks

    calls: dict = {}

    class _FakeAsyncResult:
        id = "fake-async-1"

    def _fake_delay(**kwargs):
        calls.update(kwargs)
        return _FakeAsyncResult()

    monkeypatch.setattr(dispatch, "celery_enabled", lambda: True)
    monkeypatch.setattr(worker_tasks.run_rag_evaluation, "delay", _fake_delay)

    admin = _client("admin")
    resp = admin.post("/api/enterprise/rag/evaluations", json={"dataset": "e3_05_baseline.jsonl"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["dispatch"]["mode"] == "redis" and data["dispatch"]["enqueued"] is True
    run_id = data["run"]["run_id"]
    assert calls == {"run_id": run_id}
    assert data["run"]["status"] == "queued", "真实队列模式下触发只入队，不同步执行"

    # Worker 侧推进同一段工作函数到终态
    finished = eval_service.run_evaluation(run_id)
    assert finished["status"] == "succeeded"
    assert admin.get(f"/api/enterprise/rag/evaluations/{run_id}").json()["data"]["run"]["status"] == "succeeded"


# ---------------------------------------------------------------------------
# 6. 生产门：另行确认
# ---------------------------------------------------------------------------


def test_production_scope_requires_explicit_confirmation(reports):
    admin = _client("admin")
    resp = admin.post("/api/enterprise/rag/evaluations",
                      json={"dataset": "e3_05_baseline.jsonl", "execution_scope": "production"})
    assert resp.status_code == 422, resp.text
    assert "确认" in resp.json()["detail"]["message"]

    denied = [e for e in main.AUDIT_EVENTS.values()
              if getattr(e, "event_type", "") == "tenant.rag_evaluation.denied"
              and "production" in (e.summary or "")]
    assert denied, "生产门拦截必须留有审计"
    assert admin.get("/api/enterprise/rag/evaluations").json()["data"]["runs"] == []


def test_production_confirmed_runs_with_require_milvus_semantics(reports):
    admin = _client("admin")
    resp = admin.post("/api/enterprise/rag/evaluations", json={
        "dataset": "e3_05_baseline.jsonl", "execution_scope": "production", "confirm_production": True})
    assert resp.status_code == 200, resp.text
    run = resp.json()["data"]["run"]
    # 本环境 Milvus 不可用：require_milvus 语义 → blocked（绝不静默回退冒称 verified）
    assert run["status"] == "blocked", run
    assert run["validation_status"] == "blocked"
    assert run["report_name"], "blocked 也要保留证据报告"
    view = admin.get(f"/api/enterprise/rag/reports/{run['report_name']}")
    assert view.json()["data"]["validation_status"] == "blocked"


def test_isolated_scope_requires_in_memory_runtime_and_seeds(reports, monkeypatch):
    """默认隔离语义（fail closed）：isolated 只允许内存态运行且必须内存种子；
    production 不种子（直接吃运行态数据）。"""
    from app.enterprise_rag import eval_service

    monkeypatch.setattr(eval_service, "_in_memory_mode", lambda: False)
    admin = _client("admin")
    resp = admin.post("/api/enterprise/rag/evaluations", json={"dataset": "e3_05_baseline.jsonl"})
    assert resp.status_code == 422, resp.text
    assert "内存" in resp.json()["detail"]["message"]

    monkeypatch.setattr(eval_service, "_in_memory_mode", lambda: True)
    seeded: list[str] = []
    monkeypatch.setattr(eval_service, "_seed_baseline", lambda: seeded.append("seed"))

    assert admin.post("/api/enterprise/rag/evaluations",
                      json={"dataset": "e3_05_baseline.jsonl"}).status_code == 200
    assert seeded == ["seed"], "isolated 必须先种子基线"

    assert admin.post("/api/enterprise/rag/evaluations", json={
        "dataset": "e3_05_baseline.jsonl", "execution_scope": "production",
        "confirm_production": True}).status_code == 200
    assert seeded == ["seed"], "production 不种子隔离数据"


# ---------------------------------------------------------------------------
# 7. 租户隔离（跨租户不可见）
# ---------------------------------------------------------------------------


def test_runs_are_tenant_scoped(reports):
    from app.enterprise_rag import eval_store
    from app.enterprise_rag.eval_store import EvaluationRun

    other = eval_store.create(EvaluationRun(
        run_id="rageval_tenant_b", tenant_id="tenant_b", dataset="e3_05_baseline.jsonl",
        requested_by="u_b", requested_role="tenant_owner"))

    admin = _client("admin")
    runs = admin.get("/api/enterprise/rag/evaluations").json()["data"]["runs"]
    assert all(r["run_id"] != other.run_id for r in runs)
    assert admin.get(f"/api/enterprise/rag/evaluations/{other.run_id}").status_code == 404


# ---------------------------------------------------------------------------
# 8. 失败路径：脱敏 + failed 审计
# ---------------------------------------------------------------------------


def test_run_failure_records_scrubbed_error(reports, monkeypatch):
    import os

    from app.enterprise_rag import eval_service
    from app.enterprise_rag import eval_store
    from app.enterprise_rag.eval_store import EvaluationRun

    secret = "opt08-secret-token-abcdef0123456789"
    monkeypatch.setenv("GITEE_TOKEN", secret)

    run = eval_store.create(EvaluationRun(
        run_id="rageval_boom", tenant_id="tenant_default", dataset="e3_05_baseline.jsonl",
        requested_by="admin@company.com", requested_role="tenant_owner"))

    def _boom(_path):
        raise RuntimeError(f"boom url=https://oauth2:{secret}@example.com/repo.git")

    monkeypatch.setattr(eval_service, "load_cases", _boom)
    result = eval_service.run_evaluation(run.run_id)
    assert result["status"] == "failed"
    assert "RuntimeError" in result["error"]
    assert secret not in result["error"], "失败文本必须脱敏"

    types = [e.event_type for e in main.AUDIT_EVENTS.values()]
    assert "tenant.rag.eval.failed" in types


# ---------------------------------------------------------------------------
# 9. 审计完整性：同一 actor+tenant 的多次拒绝不得互相覆盖（演示取证发现）
# ---------------------------------------------------------------------------


def test_denial_audits_do_not_overwrite_each_other():
    """deny_and_audit 旧兜底 id 固定为 audit_{user}_{tenant}，同一 actor 的多次拒绝
    在 dict 事件表中键冲突互相覆盖（丢审计）。修复为唯一 id 后每条拒绝独立留档。"""
    from app.tenancy.context import deny_and_audit
    from app.tenancy.models import TenantContext

    ctx = TenantContext(user_id="user_opt08_probe", tenant_id="tenant_default",
                        tenant_name="演示租户", role="developer", permissions=[])
    deny_and_audit(ctx, "probe.one", "拒绝一号", resource_type="probe", resource_id="opt08_p1")
    deny_and_audit(ctx, "probe.two", "拒绝二号", resource_type="probe", resource_id="opt08_p2")

    events = [e for e in main.AUDIT_EVENTS.values()
              if getattr(e, "resource_id", "") in ("opt08_p1", "opt08_p2")]
    assert {e.resource_id for e in events} == {"opt08_p1", "opt08_p2"}, \
        "同一 actor+tenant 的多次拒绝必须各自留档，不得被事件表键冲突覆盖"
