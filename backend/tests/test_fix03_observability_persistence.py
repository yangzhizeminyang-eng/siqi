"""FIX-03 Trace span 跨进程持久化、租户隔离、脱敏与保留上限。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.enterprise_runtime import observability as obs

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def _child_env(database_url: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "USE_SQLALCHEMY": "false",
        "ENTERPRISE_SHARED_PERSISTENCE": "true",
        "DATABASE_URL": database_url,
        "ENV_FILE": str(BACKEND_ROOT / "tests" / ".env.hermetic-nonexistent"),
        "APP_ENV": "local",
    })
    for key in ("ZHIPU_API_KEY", "EMBEDDING_DIMENSIONS", "LLM_API_KEY", "MILVUS_URI"):
        env.pop(key, None)
    return env


def _login(who: str = "observer") -> TestClient:
    accounts = {
        "admin": ("admin@company.com", "Admin123!"),
        "developer": ("developer@company.com", "Dev123!"),
        "observer": ("observer@company.com", "Observer123!"),
    }
    client = TestClient(main.app)
    account, password = accounts[who]
    assert client.post("/api/auth/login", json={
        "account": account, "password": password}).status_code == 200
    return client


def test_worker_process_span_visible_to_api_process(shared_db):
    code = (
        "import dotenv as _dotenv\n"
        "_dotenv.load_dotenv = lambda *a, **k: False\n"
        "from app.enterprise_runtime import observability as obs\n"
        "obs.record_span('worker', 'worker.step', trace_id='trace_fix03_child', "
        "tenant_id='tenant_default', attributes={'agent_type': 'requirement_agent'})\n"
        "obs.record_span('rag', 'rag.search', trace_id='trace_fix03_child', "
        "tenant_id='tenant_default', attributes={'hit_count': 2})\n"
        "print(obs.retention_info()['count'])\n"
    )
    proc = subprocess.run(
        [PY, "-c", code], cwd=str(BACKEND_ROOT), env=_child_env(shared_db),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1] == "2"

    obs.reset_store()  # API 进程内存中没有 Worker span
    spans = obs.query_spans("tenant_default", trace_id="trace_fix03_child")
    assert [s.category for s in spans] == ["rag", "worker"], "最新在前且来自共享真源"
    assert spans[-1].attributes["agent_type"] == "requirement_agent"

    auditor = _login("observer")
    resp = auditor.get("/api/enterprise/runtime/traces",
                       params={"trace_id": "trace_fix03_child"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert {s["category"] for s in data["spans"]} == {"worker", "rag"}
    assert data["retention"]["count"] >= 2


def test_cross_tenant_and_trace_isolation(shared_db):
    obs.record_span("model", "model.invoke", trace_id="trace_a", tenant_id="tenant_default")
    obs.record_span("model", "model.invoke", trace_id="trace_a", tenant_id="tenant_other")
    obs.record_span("tool", "tool.read_issue", trace_id="trace_b", tenant_id="tenant_default")

    assert [s.trace_id for s in obs.query_spans("tenant_default", trace_id="trace_a")] == ["trace_a"]
    assert obs.query_spans("tenant_other", trace_id="trace_a")[0].tenant_id == "tenant_other"
    assert obs.query_spans("tenant_default", trace_id="trace_b")[0].category == "tool"


def test_sensitive_attributes_are_scrubbed_before_persisting(shared_db, monkeypatch):
    secret = "fix03-super-secret-token-abcdef"
    monkeypatch.setenv("GITEE_TOKEN", secret)
    obs.record_span(
        "tool", "tool.clone", trace_id="trace_secret", tenant_id="tenant_default",
        attributes={
            "url": f"https://oauth2:{secret}@gitee.com/acme/repo.git",
            "api_key": "sk-live-123",
            "nested": {"password": "p", "keep": "ok"},
        })

    obs.reset_store()
    spans = obs.query_spans("tenant_default", trace_id="trace_secret")
    payload = json.dumps([s.model_dump(mode="json") for s in spans], ensure_ascii=False)
    assert secret not in payload
    assert spans[0].attributes["api_key"] == "***"
    assert spans[0].attributes["nested"]["password"] == "***"
    assert spans[0].attributes["nested"]["keep"] == "ok"


def _rich_tokens(secret: str) -> dict:
    return {
        "input_tokens": 12,
        "output_tokens": 34,
        "total_tokens": 46,
        "prompt_tokens": 12,
        "completion_tokens": 34,
        "secret": secret,
        "token": secret,
        "api_key": "sk-should-be-masked",
        "authorization": f"Bearer {secret}",
        "url": f"https://user:{secret}@gitee.com/acme/repo.git",
        "nested": {
            "password": "nested-password",
            "usage": {"total_tokens": 9, "input_tokens": 4},
            "list": [secret, 7, True],
        },
    }


def _assert_scrubbed_tokens(tokens: dict, secret: str) -> None:
    assert tokens["input_tokens"] == 12 and isinstance(tokens["input_tokens"], int)
    assert tokens["output_tokens"] == 34 and isinstance(tokens["output_tokens"], int)
    assert tokens["total_tokens"] == 46 and isinstance(tokens["total_tokens"], int)
    assert tokens["prompt_tokens"] == 12 and isinstance(tokens["prompt_tokens"], int)
    assert tokens["completion_tokens"] == 34 and isinstance(tokens["completion_tokens"], int)
    for key in ("secret", "token", "api_key", "authorization"):
        assert tokens[key] == "***", f"{key} 必须掩码"
    assert secret not in json.dumps(tokens, ensure_ascii=False)
    assert tokens["url"].startswith("https://user:***@")
    assert tokens["nested"]["password"] == "***"
    assert tokens["nested"]["usage"] == {"total_tokens": 9, "input_tokens": 4}
    assert tokens["nested"]["list"] == ["***", 7, True]


def test_tokens_are_scrubbed_in_memory_store(monkeypatch):
    monkeypatch.setenv("USE_SQLALCHEMY", "false")
    monkeypatch.delenv("ENTERPRISE_SHARED_PERSISTENCE", raising=False)
    secret = "fix03-token-memory-sentinel-0123456789"
    monkeypatch.setenv("GITEE_TOKEN", secret)

    obs.record_span("model", "model.invoke", trace_id="trace_tokens_mem",
                    tenant_id="tenant_default", tokens=_rich_tokens(secret))
    stored = obs.query_spans("tenant_default", trace_id="trace_tokens_mem")[0]
    _assert_scrubbed_tokens(stored.tokens, secret)


def test_tokens_are_scrubbed_before_database_persist(shared_db, monkeypatch):
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models.orm import EnterpriseRuntimeTraceSpanORM

    secret = "fix03-token-db-sentinel-0123456789"
    monkeypatch.setenv("GITEE_TOKEN", secret)
    span = obs.record_span("model", "model.invoke", trace_id="trace_tokens_db",
                           tenant_id="tenant_default", tokens=_rich_tokens(secret))

    obs.reset_store()  # 模拟查询进程清空内存缓存，只从共享真源读
    stored = obs.query_spans("tenant_default", trace_id="trace_tokens_db")[0]
    _assert_scrubbed_tokens(stored.tokens, secret)

    with session_scope() as session:
        row = session.scalar(
            select(EnterpriseRuntimeTraceSpanORM).where(
                EnterpriseRuntimeTraceSpanORM.span_id == span.span_id))
        assert row is not None, "span 必须真实落库"
        raw_json = json.dumps(row.tokens, ensure_ascii=False)
        assert secret not in raw_json, "sentinel 不得以明文落库"
        _assert_scrubbed_tokens(dict(row.tokens), secret)

    auditor = _login("observer")
    resp = auditor.get("/api/enterprise/runtime/traces",
                       params={"trace_id": "trace_tokens_db"})
    assert resp.status_code == 200, resp.text
    assert secret not in resp.text, "Trace API 不得泄漏 sentinel"
    api_tokens = resp.json()["data"]["spans"][0]["tokens"]
    _assert_scrubbed_tokens(api_tokens, secret)


def test_retention_cap_and_dropped_total_are_shared(shared_db, monkeypatch):
    monkeypatch.setenv("OBSERVABILITY_MAX_SPANS", "5")
    for i in range(9):
        obs.record_span("model", f"m{i}", trace_id="trace_ret", tenant_id="tenant_default")

    obs.reset_store()
    info = obs.retention_info()
    assert info["max_spans"] == 5 and info["count"] == 5
    assert info["dropped_total"] == 4
    names = [s.name for s in obs.query_spans("tenant_default", trace_id="trace_ret", limit=100)]
    assert names[0] == "m8" and "m4" in names
    assert "m0" not in names and "m3" not in names


def test_error_status_can_be_queried(shared_db):
    obs.record_span("rag", "rag.search", trace_id="trace_err", tenant_id="tenant_default",
                    status="error", error_kind="http_404")
    errs = obs.query_spans("tenant_default", trace_id="trace_err", status="error")
    assert len(errs) == 1 and errs[0].error_kind == "http_404"


def test_trace_storage_failure_does_not_break_business(shared_db, monkeypatch, caplog):
    from app.db import shared_stores

    def _boom(*args, **kwargs):
        raise OSError("trace db down")

    monkeypatch.setattr(shared_stores, "insert_trace_span", _boom)
    span = obs.record_span("model", "model.invoke", trace_id="trace_fallback",
                           tenant_id="tenant_default")
    assert span.name == "model.invoke", "观测失败不得影响业务调用"
    monkeypatch.setattr(shared_stores, "load_trace_spans", _boom)
    fallback = obs.query_spans("tenant_default", trace_id="trace_fallback")
    assert [s.span_id for s in fallback] == [span.span_id], "DB 不可用时进程内兜底可查"


def test_traces_api_permission_and_limit(shared_db):
    obs.record_span("model", "model.invoke", trace_id="trace_api",
                    tenant_id="tenant_default")
    developer = _login("developer")
    assert developer.get("/api/enterprise/runtime/traces").status_code == 403
    auditor = _login("observer")
    resp = auditor.get("/api/enterprise/runtime/traces", params={"limit": 0})
    assert resp.status_code == 200
    assert len(resp.json()["data"]["spans"]) >= 1
