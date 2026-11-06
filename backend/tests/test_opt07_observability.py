"""OPT-07 Agent 全链路可观测性验收测试（TDD 红→绿）。

覆盖：
- TraceSpan 存储：emit/query、租户隔离、trace_id/category/status/limit 过滤
- 保留边界：OBSERVABILITY_MAX_SPANS 上限触发最旧淘汰并可查询统计
- 脱敏：敏感 key 掩码、环境凭据值替换、超长截断、嵌套结构保留类型
- 上下文绑定：bind/clear/current + span 自动继承 trace_id/tenant_id
- 执行器埋点：model.invoke / artifact.write / tool.* span（确定性真实运行，非伪造）
- 失败路径：invoke 异常 → status=error + error_kind；usage token 透传/未采集如实标记
- 工具包装：受控上下文内调用真实工具产生带耗时 span；裸调用不产生（观察面不污染）
- RAG 包装：成功 span（模式/命中数/stale）与 HTTPException error span；继承环境 trace
- 审批：approve/reject span 可检索
- Worker：run_agent_workflow worker.step span，与 model/artifact 同 trace 串联
- API：/api/enterprise/runtime/traces 权限（audit:view=403 拒绝）与租户隔离 + 过滤 + 保留元数据
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import main

NOW = datetime.now(timezone.utc)
_COMMIT = "a1b2c3d4e5f678901234567890abcdef12345678"


def _obs():
    from app.enterprise_runtime import observability

    return observability


@pytest.fixture(autouse=True)
def _deterministic_agents(monkeypatch):
    """测试仅用确定性模型 provider（显式 test-only；生产绝不构造）。"""
    monkeypatch.setenv("AGENT_PROVIDER", "deterministic")


# ---------------------------------------------------------------------------
# 1. TraceSpan 存储与查询
# ---------------------------------------------------------------------------


def test_record_and_query_tenant_isolation_and_order():
    obs = _obs()
    obs.record_span("model", "model.invoke", trace_id="t1", tenant_id="tenant_a", duration_ms=12.5)
    obs.record_span("artifact", "artifact.write", trace_id="t1", tenant_id="tenant_a")
    obs.record_span("rag", "rag.search", trace_id="t2", tenant_id="tenant_b")

    a = obs.query_spans("tenant_a")
    assert len(a) == 2
    assert [s.category for s in a] == ["artifact", "model"], "最新 span 在前"
    assert all(s.tenant_id == "tenant_a" for s in a)
    assert a[-1].duration_ms == 12.5
    assert obs.query_spans("tenant_b")[0].trace_id == "t2"
    assert obs.query_spans("tenant_c") == []


def test_query_filters_trace_category_status_and_limit():
    obs = _obs()
    for i in range(6):
        obs.record_span("tool", f"tool.t{i}", trace_id="trace_x" if i < 3 else "trace_y",
                        tenant_id="ta", status="ok")
    obs.record_span("model", "model.invoke", trace_id="trace_x", tenant_id="ta",
                    status="error", error_kind="http_500")

    assert {s.trace_id for s in obs.query_spans("ta", trace_id="trace_x")} == {"trace_x"}
    assert len(obs.query_spans("ta", trace_id="trace_x")) == 4
    assert all(s.category == "tool" for s in obs.query_spans("ta", category="tool"))
    errs = obs.query_spans("ta", status="error")
    assert len(errs) == 1 and errs[0].error_kind == "http_500"
    assert len(obs.query_spans("ta", category="tool", limit=2)) == 2
    # limit 下限/上限保护；offset 前移
    assert len(obs.query_spans("ta", limit=0)) == 1
    assert len(obs.query_spans("ta", limit=10**9)) == 7
    assert len(obs.query_spans("ta", limit=2, offset=6)) == 1
    # 未知 category 返回空（确定性，不报错）
    assert obs.query_spans("ta", category="bogus") == []


def test_retention_cap_drops_oldest_and_tracks_dropped(monkeypatch):
    obs = _obs()
    monkeypatch.setenv("OBSERVABILITY_MAX_SPANS", "5")
    for i in range(9):
        obs.record_span("model", f"m{i}", tenant_id="ta")

    info = obs.retention_info()
    assert info["max_spans"] == 5 and info["count"] == 5
    assert info["dropped_total"] == 4, "超上限必须淘汰最旧并留下可查计数"
    names = [s.name for s in obs.query_spans("ta", limit=100)]
    assert names[0] == "m8" and "m4" in names
    assert "m0" not in names and "m3" not in names, "淘汰的是最旧 span"


# ---------------------------------------------------------------------------
# 2. 脱敏与截断
# ---------------------------------------------------------------------------


def test_scrub_attributes_redacts_sensitive_keys_and_env_values(monkeypatch):
    obs = _obs()
    monkeypatch.setenv("GITEE_TOKEN", "supersecret-token-value")
    attrs = obs.scrub_attributes({
        "api_key": "sk-123",
        "Authorization": "Bearer abc",
        "password": "p",
        "cookie": "c",
        "normal": "keep-me",
        "url": "https://user:supersecret-token-value@gitee.com/x.git",
        "query": "https://x.com/a?access_token=supersecret-token-value&b=1",
        "long": "x" * 500,
        "nested": {"secret_val": "s", "list": ["supersecret-token-value", 7, True]},
        "count": 3,
    })
    for key in ("api_key", "Authorization", "password", "cookie"):
        assert attrs[key] == "***", f"敏感 key {key} 必须掩码"
    assert attrs["normal"] == "keep-me"
    assert "supersecret-token-value" not in attrs["url"], "URL 内凭据必须脱敏"
    assert "supersecret-token-value" not in attrs["query"], "查询串 token 必须脱敏"
    assert len(attrs["long"]) <= obs.MAX_ATTR_VALUE_LEN, "超长属性必须截断"
    assert attrs["nested"]["secret_val"] == "***"
    assert attrs["nested"]["list"][0] == "***", "嵌套值同样脱敏"
    assert attrs["nested"]["list"][1] == 7 and attrs["nested"]["list"][2] is True, "类型保留"
    assert attrs["count"] == 3


def test_scrub_not_collected_tokens_shape():
    """未采集 token 必须如实标记 not_collected，禁止伪造数值。"""
    obs = _obs()
    s = obs.record_span("model", "model.invoke", tenant_id="ta")
    assert s.tokens == {}, "默认不带 token 字段（由调用方如实标记）"
    s2 = obs.record_span("model", "model.invoke", tenant_id="ta", tokens={"status": "not_collected"})
    assert s2.tokens["status"] == "not_collected"


# ---------------------------------------------------------------------------
# 3. 上下文绑定（contextvar）
# ---------------------------------------------------------------------------


def test_bind_trace_and_span_inherits_context():
    obs = _obs()
    assert obs.current_trace() is None
    token = obs.bind_trace(trace_id="task-1", tenant_id="tenant_a", run_id="r1")
    try:
        ctx = obs.current_trace()
        assert ctx["trace_id"] == "task-1" and ctx["tenant_id"] == "tenant_a"
        with obs.span("model", "model.invoke") as sp:
            sp.attributes["agent_type"] = "requirement_agent"
        spans = obs.query_spans("tenant_a", trace_id="task-1", category="model")
        assert len(spans) == 1
        assert spans[0].status == "ok" and spans[0].duration_ms is not None
        assert spans[0].duration_ms >= 0
        assert spans[0].attributes["agent_type"] == "requirement_agent"
    finally:
        obs.clear_trace(token)
    assert obs.current_trace() is None


def test_span_error_kind_recorded_and_exception_reraised():
    obs = _obs()
    token = obs.bind_trace(trace_id="t-err", tenant_id="ta")
    try:
        class Boom(RuntimeError):
            pass

        with pytest.raises(Boom):
            with obs.span("model", "model.invoke"):
                raise Boom("bad")
        span = obs.query_spans("ta", trace_id="t-err")[0]
        assert span.status == "error" and span.error_kind == "Boom"
        assert span.duration_ms is not None
    finally:
        obs.clear_trace(token)


# ---------------------------------------------------------------------------
# 4. 执行器埋点（确定性真实运行）
# ---------------------------------------------------------------------------


def _ex(task_id="tp", tenant="tenant_a", agent_type="requirement_agent"):
    from app.enterprise_runtime.agents.executor import AgentExecutor

    st = SimpleNamespace(step_id=f"{task_id}:{agent_type}", agent_type=agent_type, agent_attempt=0,
                         repair_round=0, task_id=task_id)
    tk = SimpleNamespace(id=task_id, tenant_id=tenant, project_id="p", repository_id="r", title="t")
    return AgentExecutor(agent_type, audit=lambda *a, **k: None), st, tk


def _run(ex, st, tk):
    return ex.run(ctx=SimpleNamespace(user_id="u1", role="developer"), task=tk, step=st)


def test_executor_run_emits_model_and_artifact_spans():
    obs = _obs()
    ex, st, tk = _ex()
    result = _run(ex, st, tk)
    assert result["ok"] is True

    model_spans = obs.query_spans("tenant_a", trace_id="tp", category="model")
    assert len(model_spans) == 1
    ms = model_spans[0]
    assert ms.status == "ok" and ms.duration_ms is not None
    assert ms.tokens.get("status") == "not_collected", "确定性 provider 无真实 token，必须如实标记"
    assert ms.attributes.get("agent_type") == "requirement_agent"
    assert ms.attributes.get("run_id") == result["run_id"]

    art_spans = obs.query_spans("tenant_a", trace_id="tp", category="artifact")
    assert [s.attributes.get("artifact_type") for s in art_spans] == ["RequirementArtifact"]
    assert art_spans[0].status == "ok"
    assert art_spans[0].attributes.get("artifact_id")
    cats = {s.category for s in obs.query_spans("tenant_a", trace_id="tp")}
    assert cats == {"model", "artifact"}, "无工具的确定性运行不产生 tool span"


def test_executor_emits_tool_spans_and_passes_real_usage(monkeypatch):
    obs = _obs()
    import app.enterprise_runtime.agents.executor as exmod

    class FakeBackend:
        def __init__(self):
            self.last_usage = {"tokens": {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8}}

        def invoke_with_trace(self, _structured_input):
            return (
                {"summary": "s", "requirements": [{"id": "r1", "description": "d", "acceptance": "a"}],
                 "open_questions": [], "completeness": "complete"},
                [{"tool_name": "_read_issue"}, {"tool_name": "_read_comments"}],
            )

    monkeypatch.setattr(exmod, "build_agent", lambda _agent_type: FakeBackend())
    ex, st, tk = _ex()
    result = _run(ex, st, tk)
    assert result["ok"] is True

    tools = obs.query_spans("tenant_a", trace_id="tp", category="tool")
    assert sorted(s.name for s in tools) == ["tool.read_comments", "tool.read_issue"]
    ms = obs.query_spans("tenant_a", trace_id="tp", category="model")[0]
    assert ms.tokens["total_tokens"] == 8, "真实 usage 存在时必须透传，不得丢弃"


def test_executor_does_not_duplicate_wrapper_tool_spans(monkeypatch):
    """模型消息里的工具调用与 wrapper 真实执行不得重复计数：以真实执行 span（含耗时）为准。"""
    obs = _obs()
    import app.enterprise_runtime.agents.executor as exmod
    from app.enterprise_runtime.agents import tools as agent_tools

    class ToolCallingBackend:
        def invoke_with_trace(self, _structured_input):
            read_issue = next(t for t in agent_tools.build_tools("requirement_agent")
                              if "read_issue" in t.name)
            read_issue.invoke({"issue_text": "hi"})  # 真实执行（wrapper 记 span + 名字）
            return (
                {"summary": "s", "requirements": [{"id": "r1", "description": "d", "acceptance": "a"}],
                 "open_questions": [], "completeness": "complete"},
                [{"tool_name": "_read_issue"}],
            )

    monkeypatch.setattr(exmod, "build_agent", lambda _agent_type: ToolCallingBackend())
    ex, st, tk = _ex()
    result = _run(ex, st, tk)
    assert result["ok"] is True
    tools = obs.query_spans("tenant_a", trace_id="tp", category="tool")
    assert len(tools) == 1, "同一次工具调用只能有一条 tool span"
    assert tools[0].name == "tool.read_issue"
    assert tools[0].duration_ms is not None, "以真实执行的耗时 span 为准"
    assert tools[0].attributes.get("source") != "agent_tool_trace"


def test_executor_invoke_failure_emits_error_span(monkeypatch):
    obs = _obs()
    import app.enterprise_runtime.agents.executor as exmod

    class BoomBackend:
        def invoke(self, _structured_input):
            raise RuntimeError("model down")

    monkeypatch.setattr(exmod, "build_agent", lambda _agent_type: BoomBackend())
    ex, st, tk = _ex()
    result = _run(ex, st, tk)
    assert result["ok"] is False and result["failure_kind"] == "retryable"

    ms = obs.query_spans("tenant_a", trace_id="tp", category="model")[0]
    assert ms.status == "error" and ms.error_kind == "RuntimeError"
    assert ms.duration_ms is not None


# ---------------------------------------------------------------------------
# 5. 工具包装（build_tools 返回的工具在受控上下文内产生 span）
# ---------------------------------------------------------------------------


def test_built_tool_invocation_emits_tool_span_only_in_context():
    obs = _obs()
    from app.enterprise_runtime.agents import tools as agent_tools

    built = agent_tools.build_tools("requirement_agent")
    read_issue = next(t for t in built if "read_issue" in t.name)
    # 裸调用（无受控 trace 上下文）：不产生 span，观察面不污染
    assert read_issue.invoke({"issue_text": "x"}) == "x"
    assert obs.query_spans("tenant_a") == []

    with agent_tools.runtime_tool_context(task_id="tp", run_id="run1", tenant_id="tenant_a"):
        assert read_issue.invoke({"issue_text": "hello"}) == "hello"
    spans = obs.query_spans("tenant_a", trace_id="tp", category="tool")
    assert len(spans) == 1
    s = spans[0]
    assert s.name == "tool.read_issue" and s.status == "ok"
    assert s.attributes.get("tool_name") == "read_issue"
    assert s.duration_ms is not None and s.duration_ms >= 0
    assert obs.current_trace() is None, "runtime_tool_context 退出后 trace 绑定必须清理"


# ---------------------------------------------------------------------------
# 6. RAG 包装
# ---------------------------------------------------------------------------


def _make_repo(repo_id, project_id, tenant_id, commit):
    main.PROJECTS[project_id] = main.ProjectRecord(
        id=project_id, tenant_id=tenant_id, name=project_id, description="",
        created_at=NOW, updated_at=NOW)
    main.REPOSITORIES[repo_id] = main.RepositoryConfigRecord(
        id=repo_id, tenant_id=tenant_id, project_id=project_id, name=repo_id,
        url=f"https://gitee.com/demo/{repo_id}.git", default_branch="main",
        test_command="pytest", command_whitelist=["pytest"],
        approval_mode="single", risk_threshold="medium", max_files=10, max_retries=1,
        blocked_paths=[], created_at=NOW, updated_at=NOW)
    index = main.RepositoryIndexRecord(
        id=f"idx_{repo_id}", tenant_id=tenant_id, repository_id=repo_id, project_id=project_id,
        base_branch="main", commit_sha=commit, status="COMPLETED",
        requested_by="s", requested_by_name="s", requested_at=NOW, created_at=NOW, updated_at=NOW)
    main.REPOSITORY_INDEXES[index.id] = index
    main.LATEST_INDEX_BY_REPO[repo_id] = index.id
    # FIX-02：向量模式要求生命周期记录 ready（否则 fail-closed）；测试索引显式登记。
    from app.enterprise_rag import index_lifecycle

    index_lifecycle.register_build(
        index_id=index.id, tenant_id=tenant_id, repository_id=repo_id,
        project_id=project_id, base_branch="main", commit_sha=commit,
        collection_name="")
    index_lifecycle.mark_ready(index, chunk_count=1)
    main.CODE_CHUNKS[f"{repo_id}_c1"] = main.CodeChunkRecord(
        chunk_id=f"{repo_id}_c1", repository_id=repo_id, index_id=index.id,
        file_path="src/refund.py", chunk_index=0, content="def handle_refund(): raise ERR_401",
        language="python", symbol_name="handle_refund", start_line=1, end_line=2,
        commit_sha=commit, milvus_collection="", milvus_pk="", checksum="x")
    return index.id


def _default_ctx():
    from app.tenancy import store as ts
    from app.tenancy.context import build_context

    return build_context(ts.active_membership_for_user("user_admin"))


def _payload(repo_id="repo_obs", project_id="proj_obs", commit=_COMMIT):
    from app.enterprise_rag.models import EnterpriseRagSearchPayload

    return EnterpriseRagSearchPayload(query="handle_refund ERR_401", project_id=project_id,
                                      repository_id=repo_id, commit_sha=commit)


def test_rag_search_wrapper_emits_ok_span(monkeypatch):
    obs = _obs()
    import app.retrieval.llamaindex_backend as lib

    monkeypatch.setattr(lib, "_query_vector_results", lambda *a, **k: [])
    _make_repo("repo_obs", "proj_obs", "tenant_default", _COMMIT)
    from app.enterprise_rag.service import enterprise_rag_search

    out = enterprise_rag_search(_default_ctx(), _payload())
    assert "results" in out and "retrieval_mode" in out, "返回值契约不变"

    spans = obs.query_spans("tenant_default", category="rag")
    assert len(spans) == 1
    s = spans[0]
    assert s.name == "rag.search" and s.status == "ok"
    assert s.attributes["repository_id"] == "repo_obs"
    assert s.attributes["retrieval_mode"] == out["retrieval_mode"]
    assert s.attributes["hit_count"] == len(out["results"])
    assert s.attributes["stale"] is False
    assert s.duration_ms is not None


def test_rag_search_wrapper_emits_error_spans_and_inherits_ambient_trace(monkeypatch):
    obs = _obs()
    import app.retrieval.llamaindex_backend as lib
    from fastapi import HTTPException
    from app.enterprise_rag.service import enterprise_rag_search
    from app.enterprise_runtime.agents import tools as agent_tools

    monkeypatch.setattr(lib, "_query_vector_results", lambda *a, **k: [])

    # 404：仓库不存在
    with pytest.raises(HTTPException) as ei:
        enterprise_rag_search(_default_ctx(), _payload(repo_id="repo_missing"))
    assert ei.value.status_code == 404
    # 403：跨租户仓库
    _make_repo("repo_other", "proj_other", "tenant_b", _COMMIT)
    with pytest.raises(HTTPException) as ei2:
        enterprise_rag_search(_default_ctx(), _payload(repo_id="repo_other", project_id="proj_other"))
    assert ei2.value.status_code == 403

    errs = obs.query_spans("tenant_default", category="rag", status="error")
    assert sorted(s.error_kind for s in errs) == ["http_403", "http_404"]

    # 环境 trace 继承：Agent 工具上下文内的 RAG 调用归入同一 trace_id
    _make_repo("repo_amb", "proj_amb", "tenant_default", _COMMIT)
    with agent_tools.runtime_tool_context(task_id="t-rag", run_id="r1", tenant_id="tenant_default"):
        enterprise_rag_search(_default_ctx(), _payload(repo_id="repo_amb", project_id="proj_amb"))
    inherited = obs.query_spans("tenant_default", trace_id="t-rag", category="rag")
    assert len(inherited) == 1 and inherited[0].status == "ok"


# ---------------------------------------------------------------------------
# 7. 审批 span
# ---------------------------------------------------------------------------


def _login_client(account, password):
    from fastapi.testclient import TestClient

    c = TestClient(main.app)
    resp = c.post("/api/auth/login", json={"account": account, "password": password})
    assert resp.status_code == 200, resp.text
    return c


def test_approval_approve_and_reject_emit_spans():
    obs = _obs()
    dev = _login_client("developer@company.com", "Dev123!")
    admin = _login_client("admin@company.com", "Admin123!")

    def _submit(key):
        resp = dev.post("/api/enterprise/approvals", json={
            "operation": "repository.index.rebuild", "resource_type": "repository",
            "resource_id": "repo_payment_service", "title": "t", "idempotency_key": key})
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]

    rec_ok = _submit("ik-obs-1")
    assert admin.post(f"/api/enterprise/approvals/{rec_ok['id']}/approve",
                      json={"comment": "ok"}).status_code == 200
    rec_no = _submit("ik-obs-2")
    assert admin.post(f"/api/enterprise/approvals/{rec_no['id']}/reject",
                      json={"reason": "不同意"}).status_code == 200

    spans = obs.query_spans("tenant_default", category="approval")
    assert sorted(s.name for s in spans) == ["approval.approve", "approval.reject"]
    assert all(s.status == "ok" and s.error_kind == "" for s in spans)
    ap = next(s for s in spans if s.name == "approval.approve")
    assert ap.attributes["approval_id"] == rec_ok["id"]
    assert ap.attributes["operation"] == "repository.index.rebuild"
    assert ap.attributes["decision"] == "approved"
    rj = next(s for s in spans if s.name == "approval.reject")
    assert rj.attributes["decision"] == "rejected"


# ---------------------------------------------------------------------------
# 8. Worker span（run_agent_workflow）
# ---------------------------------------------------------------------------


def test_worker_step_span_chains_workflow_trace():
    obs = _obs()
    from app.enterprise_runtime import orchestrator as orch
    from app.enterprise_runtime.agents.registry import AgentExecutorRegistry

    ctx = SimpleNamespace(tenant_id="tenant_a", user_id="u1", user_name="n1", role="admin")
    orch.start_runtime_task(ctx, "tw", "ik_tw", project_id="p1", repository_id="r1",
                            title="obs", workflow_version=2)
    reg = AgentExecutorRegistry(audit=lambda *a, **k: None)
    task = orch.run_agent_workflow("w1", "tw", reg, ctx=ctx)
    assert getattr(task, "current_step", None), "首步已被真实执行"

    worker = obs.query_spans("tenant_a", trace_id="tw", category="worker")
    assert len(worker) == 1
    ws = worker[0]
    assert ws.name == "worker.step" and ws.status == "ok" and ws.duration_ms is not None
    assert ws.attributes["worker_id"] == "w1"
    assert ws.attributes["agent_type"] == "requirement_agent"
    cats = {s.category for s in obs.query_spans("tenant_a", trace_id="tw")}
    assert {"worker", "model", "artifact"} <= cats, "同一 trace 串联 Worker→模型→产物"


# ---------------------------------------------------------------------------
# 9. Traces API：权限与租户隔离
# ---------------------------------------------------------------------------


def test_traces_api_permission_tenant_scoping_and_filters():
    obs = _obs()
    obs.record_span("model", "model.invoke", trace_id="t1", tenant_id="tenant_default",
                    attributes={"agent_type": "requirement_agent"})
    obs.record_span("rag", "rag.search", trace_id="t2", tenant_id="tenant_other")

    dev = _login_client("developer@company.com", "Dev123!")
    denied = dev.get("/api/enterprise/runtime/traces")
    assert denied.status_code == 403, "developer 无 audit:view，必须 403"

    auditor = _login_client("observer@company.com", "Observer123!")
    resp = auditor.get("/api/enterprise/runtime/traces")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert [s["trace_id"] for s in data["spans"]] == ["t1"], "只可见本租户 span"
    assert data["retention"]["count"] >= 1 and "dropped_total" in data["retention"]

    flt = auditor.get("/api/enterprise/runtime/traces",
                      params={"trace_id": "t1", "category": "model"})
    assert len(flt.json()["data"]["spans"]) == 1
    empty = auditor.get("/api/enterprise/runtime/traces", params={"category": "rag"})
    assert empty.json()["data"]["spans"] == [], "他租户的 rag span 不可见"
    clamped = auditor.get("/api/enterprise/runtime/traces", params={"limit": "0"})
    assert len(clamped.json()["data"]["spans"]) == 1, "limit 下限保护为 1"
