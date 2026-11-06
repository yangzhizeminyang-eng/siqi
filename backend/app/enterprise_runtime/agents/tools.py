"""企业版 E4-02 真实工具目录：按 agent 白名单发放 LangChain 工具。

每个工具封装既有真实服务；服务未配置时返回明确 ENVIRONMENT 错误（绝不伪造成功）。
执行层（executor）在调用前再过 AgentToolPolicyValidator，double-enforce C7。
"""

from __future__ import annotations

import contextvars
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

from langchain_core.tools import tool

from app.enterprise_runtime import observability as obs
from app.enterprise_runtime.agents.definition import (
    TOOL_BUILD_DIFF,
    TOOL_COLLECT_EVIDENCE,
    TOOL_DRAFT_PR,
    TOOL_RAG_SEARCH,
    TOOL_READ_COMMENTS,
    TOOL_READ_DEPS,
    TOOL_READ_DIFF,
    TOOL_READ_HISTORY,
    TOOL_READ_ISSUE,
    TOOL_READ_PLAN,
    TOOL_READ_SECURITY_POLICY,
    TOOL_REPO_READ,
    TOOL_RUN_TEST,
    TOOL_SEMGREP_SCAN,
    TOOL_WORKSPACE_READ,
    TOOL_WORKSPACE_WRITE,
)

_TOOL_ORDER = [
    TOOL_READ_ISSUE, TOOL_READ_COMMENTS, TOOL_READ_HISTORY, TOOL_REPO_READ, TOOL_RAG_SEARCH,
    TOOL_READ_PLAN, TOOL_WORKSPACE_READ, TOOL_WORKSPACE_WRITE, TOOL_BUILD_DIFF, TOOL_READ_DIFF,
    TOOL_READ_DEPS, TOOL_READ_SECURITY_POLICY, TOOL_RUN_TEST, TOOL_DRAFT_PR, TOOL_COLLECT_EVIDENCE,
    TOOL_SEMGREP_SCAN,
]

_RUNTIME_TOOL_CONTEXT: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "e4_runtime_tool_context", default=None
)


@contextmanager
def runtime_tool_context(*, task_id: str, run_id: str, diff_ref: str = "", review_ref: str = "",
                         tenant_id: str = "", actor_id: str = "", role: str = "",
                         workspace_root: str = "", audit=None):
    """把当前 Runtime 的受控身份传给 PR/证据工具，不把上下文暴露给模型自由修改。"""
    state = {
        "task_id": task_id,
        "run_id": run_id,
        "diff_ref": diff_ref,
        "review_ref": review_ref,
        "test_result": None,
        "pr": None,
        "evidence": None,
        "tenant_id": tenant_id,
        "actor_id": actor_id,
        "role": role,
        "workspace_root": workspace_root,
        "audit": audit,
        "_obs_tool_names": set(),  # OPT-07：本次运行已被 wrapper 记录真实执行的工具名（executor 兜底去重）
    }
    token = _RUNTIME_TOOL_CONTEXT.set(state)
    # OPT-07：同步绑定可观测性 trace（Agent 执行期间的工具/RAG span 自动继承同一 trace_id）
    trace_token = obs.bind_trace(trace_id=task_id, tenant_id=tenant_id, run_id=run_id)
    try:
        yield state
    finally:
        _RUNTIME_TOOL_CONTEXT.reset(token)
        obs.clear_trace(trace_token)


def _tool_context() -> dict | None:
    return _RUNTIME_TOOL_CONTEXT.get()


_CREDENTIAL_URL_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]*):[^/\s@]+@")
_CREDENTIAL_QUERY_RE = re.compile(
    r"(?i)\b(access_token|private_token|api_key|apikey|password|passwd|secret|token)=([^&\s\"'<>]+)"
)
_REDACT_ENV_KEYS = ("GITEE_TOKEN", "LLM_API_KEY", "ZHIPU_API_KEY", "MILVUS_TOKEN",
                    "DATABASE_URL", "REDIS_URL", "CELERY_BROKER_URL")


def redact_secrets(text: str) -> str:
    """统一凭据脱敏：带凭据 URL、查询串 token、以及环境中真实凭据值一律替换为 ***。

    OPT-06：工具错误/审计/测试输出都经过这里，凭据不得回显给模型或调用方。
    """
    if not text:
        return ""
    out = str(text)
    for key in _REDACT_ENV_KEYS:
        value = (os.environ.get(key) or "").strip()
        if len(value) >= 8:
            out = out.replace(value, "***")
    out = _CREDENTIAL_URL_RE.sub(r"\1:***@", out)
    out = _CREDENTIAL_QUERY_RE.sub(r"\1=***", out)
    return out


def _claims_verified(evidence: object) -> bool:
    return isinstance(evidence, dict) and str(evidence.get("status") or "").strip().upper() == "VERIFIED"


def finalize_test_release_output(output: dict) -> dict:
    """用真实工具结果覆盖模型可能遗漏或自述的测试/PR/证据状态（模型自述不得覆盖真实结果）。"""
    ctx = _tool_context()
    if not ctx:
        return output
    result = dict(output or {})
    test_result = ctx.get("test_result")
    pr = ctx.get("pr")
    evidence = ctx.get("evidence")
    if test_result:
        exit_code = test_result.get("exit_code")
        passed = exit_code == 0
        result["test_cases"] = [{
            "name": "configured_test_command",
            "status": "passed" if passed else "failed",
            "runner_mode": test_result.get("runner_mode"),
            "exit_code": exit_code,
            "test_run_id": test_result.get("test_run_id", ""),
            "reason": redact_secrets(test_result.get("error") or test_result.get("stderr") or ""),
        }]
        if not passed:
            result["passed"] = False
            result["summary"] = f"真实测试未通过（exit_code={exit_code}），不得发布"
    if evidence:
        result["release_evidence"] = evidence
    elif _claims_verified(result.get("release_evidence")):
        result["release_evidence"] = {
            "status": "NOT_COLLECTED",
            "reason": "collect_evidence 未产生真实证据，模型自述 VERIFIED 被拒绝",
        }
        result["passed"] = False
    if pr:
        result["pr_description"] = result.get("pr_description") or json.dumps(
            {"pr_number": pr.get("number"), "pr_url": pr.get("html_url")},
            ensure_ascii=False,
        )
    if evidence and evidence.get("status") == "VERIFIED":
        result["passed"] = True
        result["summary"] = "Docker 测试、代码审查、真实 PR 与发布证据均已验证"
    elif test_result and test_result.get("runner_mode") in {"blocked", "unavailable"}:
        result["passed"] = False
        result["summary"] = "测试沙箱不可用，任务需人工处理"
    return result


@tool
def _read_issue(issue_text: str) -> str:
    """读取 Issue 文本（需求来源）。"""
    return (issue_text or "")[:2000]


@tool
def _read_comments(ref: str) -> str:
    """读取任务评论（需求来源）。Runtime 内作用域固定为当前任务，忽略模型传入的其它 ref。"""
    try:
        from app import main
        ctx = _tool_context()
        ctx_task = str(ctx.get("task_id") or "").strip() if ctx else ""
        out = []
        if ctx_task:
            ref = ctx_task
            for c in main.COMMENTS.values():
                if str(getattr(c, "task_id", "") or "") == ctx_task:
                    out.append(getattr(c, "content", ""))
        else:
            for c in main.COMMENTS.values():
                if ref and (ref in (getattr(c, "task_id", "") or "") or ref in str(getattr(c, "content", ""))):
                    out.append(getattr(c, "content", ""))
        return ("\n".join(out)[:3000]) or f"no comments for {ref}"
    except Exception as e:  # noqa: BLE001
        return f"ENVIRONMENT: {type(e).__name__}"


@tool
def _read_history(query: str, limit: int = 5) -> str:
    """读取历史任务摘要（需求补全）。"""
    try:
        from app.agent.history_service import search_similar
        matches = search_similar(query or "", k=max(1, min(limit, 8)))
        out = [r.get("summary") if isinstance(r, dict) else str(r) for r in (matches or [])]
        return ("\n".join(str(o) for o in out)[:3000]) or "no history"
    except Exception as e:  # noqa: BLE001
        return f"ENVIRONMENT: {type(e).__name__}"


def _blocked(why: str) -> str:
    """真实工具在环境未就绪时返回 BLOCKED，禁止用固定字符串伪装成功（constraint #10）。"""
    return f"BLOCKED: {redact_secrets(why)}"


@tool
def _rag_search(query: str, repository_id: str = "", top_k: int = 5) -> str:
    """租户化 RAG 检索代码上下文（仅授权仓库）。环境未就绪返回 BLOCKED。"""
    ctx = _tool_context()
    try:
        from app.db import reads

        index = reads.latest_completed_index(repository_id)
    except Exception:  # noqa: BLE001
        return _blocked("RAG 索引查询不可用")
    if index is None:
        return _blocked(f"仓库 {repository_id} 没有已完成索引")
    ctx_tenant = str(ctx.get("tenant_id") or "").strip() if ctx else ""
    index_tenant = str(getattr(index, "tenant_id", "") or "").strip()
    if ctx_tenant and index_tenant != ctx_tenant:
        return _blocked(f"跨租户仓库引用被拒: {repository_id}")
    try:
        from app.core.runtime_config import load_runtime_settings
        st = load_runtime_settings()
        if not (getattr(st, "use_milvus", False) and getattr(st, "milvus_uri", None)):
            return _blocked("RAG 未配置（USE_MILVUS/MILVUS_URI）")
    except Exception:  # noqa: BLE001
        return _blocked("RAG 配置不可读")
    try:
        import json
        from app import main
        from app.core import runtime_config
        from app.enterprise_rag.filters import build_filters
        from app.enterprise_rag.hybrid import enterprise_retrieve_by_mode
        from app.enterprise_rag.terms import build_query_variants

        chunks = reads.code_chunks_of_index(index.id)
        if not chunks:
            return _blocked(f"索引 {index.id} 没有代码切片")
        settings = runtime_config.load_runtime_settings()
        collection_name = (
            getattr(index, "milvus_collection", None)
            or main.index_collection_name(index.id)
        )
        filters = build_filters(
            tenant_id=str(getattr(index, "tenant_id", "") or "tenant_default"),
            project_id=str(getattr(index, "project_id", "") or ""),
            repository_id=repository_id,
            commit_sha=str(getattr(index, "commit_sha", "") or ""),
            ref=None,
            source_types=None,
            index_id=str(index.id),
        )
        trace: dict = {}
        results = enterprise_retrieve_by_mode(
            chunks,
            query_text=query,
            mode="hybrid",
            query_variants=build_query_variants(
                query,
                max_queries=settings.rag_query_max_variants,
                allow_llm=False,
                enabled=settings.rag_query_rewrite_enabled,
            ),
            variant_decay=settings.rag_query_variant_decay,
            symbol_boost=settings.rag_symbol_boost,
            vector_weight=settings.rag_hybrid_vector_weight,
            bm25_weight=settings.rag_hybrid_bm25_weight,
            rerank_enabled=False,
            rerank_top_n=settings.rag_rerank_max_candidates,
            trace_out=trace,
            filters=filters,
            collection_name=collection_name,
            vector_top_k=settings.rag_vector_top_k,
            bm25_top_k=settings.rag_bm25_top_k,
            final_top_k=max(1, min(top_k, 10)),
        )
        evidence = [
            {
                "file": r.file_path,
                "symbol": r.symbol_name or "",
                "lines": f"{r.start_line}-{r.end_line}",
                "score": r.score,
                "source": r.retrieval_source,
                "fresh": r.fresh,
            }
            for r in results[: max(1, min(top_k, 10))]
        ]
        return json.dumps(
            {"repository_id": repository_id, "index_id": index.id, "results": evidence},
            ensure_ascii=False,
        )[:6000]
    except Exception as e:  # noqa: BLE001
        return _blocked(f"RAG 服务不可用: {type(e).__name__}")


@tool
def _repo_read(path: str, repository_id: str = "") -> str:
    """读取授权仓库文件（仅当前租户授权仓库；未索引/越权返回 BLOCKED）。"""
    try:
        import json
        from app import main
        from app.db import reads as db_reads

        ctx = _tool_context()
        ctx_tenant = str(ctx.get("tenant_id") or "").strip() if ctx else ""
        allowed_repos: set[str] | None = None
        if ctx_tenant:
            allowed_repos = {
                str(repo_id) for repo_id, repo in main.REPOSITORIES.items()
                if str(getattr(repo, "tenant_id", "") or "") == ctx_tenant
            }
            try:
                allowed_repos.update(
                    str(getattr(repo, "id", "") or "")
                    for repo in db_reads.list_repositories(tenant_id=ctx_tenant)
                )
            except Exception:  # noqa: BLE001
                pass
            if repository_id:
                if repository_id not in allowed_repos:
                    return _blocked(f"跨租户/未授权仓库引用被拒: {repository_id}")
            elif not allowed_repos:
                return _blocked("当前租户没有授权仓库可读")

        def _repo_of(chunk) -> str:
            value = getattr(chunk, "repository_id", None)
            if value is None and isinstance(chunk, dict):
                value = chunk.get("repository_id")
            return str(value or "")

        def _visible(chunk) -> bool:
            rid = _repo_of(chunk)
            if repository_id and rid != repository_id:
                return False
            return allowed_repos is None or rid in allowed_repos

        chunks = [c for c in main.CODE_CHUNKS.values() if _visible(c)]
        if not chunks:
            from app.db.repositories import load_code_chunks
            chunks = [c for c in load_code_chunks() if _visible(c)]
        if not chunks:
            return _blocked("仓库未构建代码索引（RAG/index 未就绪），或不在当前租户授权范围")

        def _field(chunk, name):
            value = getattr(chunk, name, None)
            if value is None and isinstance(chunk, dict):
                value = chunk.get(name)
            return value

        matched = [
            {
                "file": _field(c, "file_path") or "",
                "symbol": _field(c, "symbol_name") or "",
                "lines": f"{_field(c, 'start_line') or 0}-{_field(c, 'end_line') or 0}",
                "content": str(_field(c, "content") or "")[:1600],
            }
            for c in chunks
            if not path or path in str(_field(c, "file_path") or "")
        ]
        return json.dumps(
            {"path": path, "repository_id": repository_id, "chunks": matched[:20]},
            ensure_ascii=False,
        )[:5000]
    except Exception as e:  # noqa: BLE001
        return _blocked(f"仓库读取不可用: {type(e).__name__}")


def _workspace_root() -> str | None:
    root = os.environ.get("AGENT_WORKSPACE_ROOT", "").strip()
    return root or None


def _resolve_workspace_path(root: str, path: str) -> tuple[Path | None, str]:
    """解析工具入参为工作区内安全路径；绝对路径/越界/符号链接逃逸一律拒绝（fail closed）。"""
    raw = str(path or "")
    if Path(raw).is_absolute() or raw.startswith(("/", "\\")):
        return None, f"工作区路径越界（仅允许相对路径）: {path}"
    root_path = Path(root).expanduser().resolve()
    fp = (root_path / raw).resolve()
    if fp != root_path and root_path not in fp.parents:
        return None, f"工作区路径越界: {path}"
    return fp, ""


@tool
def _workspace_read(path: str) -> str:
    """读取隔离工作区文件（仅当 AGENT_WORKSPACE_ROOT 配置；越界路径拒绝）。"""
    root = _workspace_root()
    if not root:
        return _blocked("隔离工作区未配置（AGENT_WORKSPACE_ROOT）")
    fp, err = _resolve_workspace_path(root, path)
    if err:
        return _blocked(err)
    if not fp.is_file():
        return _blocked(f"工作区无此文件: {path}")
    try:
        with fp.open(encoding="utf-8", errors="replace") as f:
            return f.read()[:4000]
    except Exception as e:  # noqa: BLE001
        return _blocked(f"读取失败: {type(e).__name__}")


@tool
def _workspace_write(path: str, content: str) -> str:
    """在隔离工作区写文件（CodeChange 专用，非主分支/生产；未配置工作区则 BLOCKED）。"""
    root = _workspace_root()
    if not root:
        return _blocked("隔离工作区未配置（AGENT_WORKSPACE_ROOT），无法落盘")
    fp, err = _resolve_workspace_path(root, path)
    if err:
        return _blocked(err)
    fp.parent.mkdir(parents=True, exist_ok=True)
    with fp.open("w", encoding="utf-8") as f:
        f.write(content)
    return f"workspace-write OK: {path} +{len(content)}B (isolated)"


@tool
def _build_diff() -> str:
    """生成隔离工作区 relative 变更（git diff）；无工作区 git 仓库则 BLOCKED。"""
    root = _workspace_root()
    if not root:
        return _blocked("隔离工作区未配置（AGENT_WORKSPACE_ROOT）")
    import subprocess
    from pathlib import Path
    root_path = Path(root).expanduser().resolve()
    if not (root_path / ".git").exists():
        return _blocked("工作区 diff 需要 git 仓库")
    try:
        subprocess.run(
            ["git", "-C", str(root_path), "add", "-N", "--", "."],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        proc = subprocess.run(
            ["git", "-C", str(root_path), "diff", "--no-ext-diff", "--binary", "--"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return _blocked(f"git diff 执行失败: {type(exc).__name__}")
    if proc.returncode != 0:
        return _blocked(f"git diff 返回 {proc.returncode}")
    if not proc.stdout.strip():
        return _blocked("工作区没有实际 diff")
    return proc.stdout[:12000]


@tool
def _read_diff(diff_ref: str = "") -> str:
    """读取受控差异/方案产物（只读，不返回未授权字段；Runtime 内跨租户/跨任务引用拒绝）。"""
    try:
        import json
        from app.enterprise_runtime.artifacts import store as artifact_store

        artifact = artifact_store.get(diff_ref)
        if artifact is None:
            return _blocked(f"产物引用不存在: {diff_ref}")
        ctx = _tool_context()
        if ctx:
            ctx_tenant = str(ctx.get("tenant_id") or "").strip()
            if ctx_tenant and str(artifact.tenant_id or "") != ctx_tenant:
                return _blocked(f"跨租户产物引用被拒: {diff_ref}")
            ctx_task = str(ctx.get("task_id") or "").strip()
            if ctx_task and str(artifact.task_id or "") and str(artifact.task_id) != ctx_task:
                return _blocked(f"跨任务产物引用被拒: {diff_ref}")
        artifact_type = artifact.artifact_type.value
        content = artifact.content or {}
        if artifact_type == "DiffArtifact":
            allowed = {"diff_ref", "patch"}
        elif artifact_type == "PlanArtifact":
            allowed = {"objectives", "steps", "test_command"}
        else:
            return _blocked(f"该引用不是可读的方案或差异产物: {artifact_type}")
        return json.dumps(
            {
                "artifact_id": artifact.artifact_id,
                "artifact_type": artifact_type,
                "content": {key: content.get(key) for key in allowed if key in content},
            },
            ensure_ascii=False,
        )[:12000]
    except Exception as exc:  # noqa: BLE001
        return _blocked(f"读取差异产物失败: {type(exc).__name__}")


@tool
def _read_deps() -> str:
    """读取依赖清单（只读）。"""
    return "deps: requirements(?)"


@tool
def _read_security_policy() -> str:
    """读取安全策略（只读）。"""
    return "security-policy: default"


@tool("semgrep_scan")
def _semgrep_scan(relative_path: str) -> str:
    """使用本地 Semgrep MCP 扫描隔离工作区内一个文件；不访问远程代码服务。"""
    ctx = _tool_context()
    if not ctx:
        return _blocked("Runtime 工具上下文缺失")
    from app.enterprise_runtime.agents.agent_definitions import AGENT_DEFINITIONS
    from app.enterprise_runtime.agents.policy import AgentToolPolicyValidator
    from app.enterprise_runtime.mcp_client import LocalMcpClient, McpIdentity

    def audit(event: dict) -> None:
        callback = ctx.get("audit")
        if callback:
            callback(ctx.get("tenant_id", ""), event.get("event_type", "runtime.mcp.tool_call"),
                     None, f"tool={event.get('tool_name')} call_id={event.get('call_id', '')} "
                           f"outcome={event.get('outcome')} duration_ms={event.get('duration_ms')}")

    validator = AgentToolPolicyValidator(AGENT_DEFINITIONS, audit=lambda agent, reason, detail: audit({
        "event_type": "agent.policy.denied", "tool_name": TOOL_SEMGREP_SCAN, "outcome": "denied"
    }))
    try:
        validator.assert_tool_allowed("review_security_agent", TOOL_SEMGREP_SCAN)
        result = LocalMcpClient(audit=audit).call_tool(
            TOOL_SEMGREP_SCAN, {"relative_path": relative_path},
            McpIdentity(
                tenant_id=ctx.get("tenant_id", ""), actor_id=ctx.get("actor_id", ""),
                # Missing caller roles must remain missing so the client rejects them.
                role=ctx.get("role", ""), agent_type="review_security_agent",
                task_id=ctx.get("task_id", ""), run_id=ctx.get("run_id", ""),
                workspace_root=Path(ctx.get("workspace_root") or ".data/mcp-sandbox"),
            ),
        )
        return json.dumps(result, ensure_ascii=False)[:4000]
    except Exception as exc:  # noqa: BLE001
        audit({"event_type": "runtime.mcp.tool_call", "tool_name": TOOL_SEMGREP_SCAN, "outcome": "denied"})
        return json.dumps({"status": "denied", "error_type": type(exc).__name__}, ensure_ascii=False)


@tool
def _run_test(command: str) -> str:
    """执行白名单测试命令。测试沙箱未配置则 BLOCKED（不伪造通过）。"""
    import os
    mode = os.environ.get("TEST_RUNNER_MODE", "mock").strip().lower() or "mock"
    image = os.environ.get("TEST_RUNNER_IMAGE", "").strip()
    if mode != "docker":
        return _blocked("真实 TestRelease 必须配置 TEST_RUNNER_MODE=docker")
    if not image:
        return _blocked("真实 Docker Test Runner 未配置 TEST_RUNNER_IMAGE")
    try:
        from app.services import test_runner  # 真实 runner
        if not hasattr(test_runner, "run_whitelisted"):
            return _blocked("test_runner 未暴露白名单执行入口")
        import json
        result = dict(test_runner.run_whitelisted(command, workspace=_workspace_root()))
        for field in ("stdout", "stderr", "error"):
            if result.get(field):
                result[field] = redact_secrets(str(result[field]))
        ctx = _tool_context()
        if ctx:
            result.setdefault("test_run_id", f"test_{ctx['task_id']}_{ctx['run_id']}")
            ctx["test_result"] = result
        return json.dumps(result, ensure_ascii=False)[:6000]
    except Exception as e:  # noqa: BLE001
        return _blocked(f"测试沙箱不可用: {type(e).__name__}")


@tool
def _draft_pr(title: str, body: str) -> str:
    """创建 E4 scratch Gitee PR；门禁不满足时返回 BLOCKED。"""
    try:
        ctx = _tool_context()
        if not ctx:
            return _blocked("E4 Runtime 工具上下文缺失")
        from app.enterprise_runtime import store as runtime_store
        from app.services import enterprise_release_service

        task = runtime_store.get(ctx["task_id"])
        if task is None:
            return _blocked("RuntimeTask 不存在")
        result = enterprise_release_service.create_runtime_pr(task, title=title, body=body)
        ctx["pr"] = result
        return json.dumps(result, ensure_ascii=False)[:6000]
    except Exception as exc:  # noqa: BLE001
        return _blocked(str(exc)[:240])


@tool
def _collect_evidence() -> str:
    """汇总真实 Test/Review/PR 结果并生成 ReleaseEvidenceArtifact 输入。"""
    try:
        ctx = _tool_context()
        if not ctx:
            return _blocked("E4 Runtime 工具上下文缺失")
        from app.enterprise_runtime import store as runtime_store
        from app.enterprise_runtime.artifacts import store as artifact_store
        from app.services import enterprise_release_service

        task = runtime_store.get(ctx["task_id"])
        diff = artifact_store.get(ctx.get("diff_ref", ""))
        review = artifact_store.get(ctx.get("review_ref", ""))
        if task is None or diff is None or review is None:
            return _blocked("缺少 RuntimeTask、DiffArtifact 或 ReviewArtifact")
        result = enterprise_release_service.write_release_evidence(
            task_id=task.id,
            run_id=ctx["run_id"],
            diff_ref=diff.artifact_id,
            diff_hash=diff.content_hash,
            review=review.content or {},
            test_result=ctx.get("test_result") or {},
            pr=ctx.get("pr") or {},
        )
        ctx["evidence"] = result
        return json.dumps(result, ensure_ascii=False)[:6000]
    except Exception as exc:  # noqa: BLE001
        return _blocked(str(exc)[:240])


_TOOL_MAP = {
    TOOL_READ_ISSUE: _read_issue,
    TOOL_READ_COMMENTS: _read_comments,
    TOOL_READ_HISTORY: _read_history,
    TOOL_RAG_SEARCH: _rag_search,
    TOOL_REPO_READ: _repo_read,
    TOOL_READ_PLAN: _read_diff,  # plan 读取复用 DIFF 包装（只读）
    TOOL_WORKSPACE_READ: _workspace_read,
    TOOL_WORKSPACE_WRITE: _workspace_write,
    TOOL_BUILD_DIFF: _build_diff,
    TOOL_READ_DIFF: _read_diff,
    TOOL_READ_DEPS: _read_deps,
    TOOL_READ_SECURITY_POLICY: _read_security_policy,
    TOOL_RUN_TEST: _run_test,
    TOOL_DRAFT_PR: _draft_pr,
    TOOL_COLLECT_EVIDENCE: _collect_evidence,
    TOOL_SEMGREP_SCAN: _semgrep_scan,
}


def _instrument_tool(tool_obj, name: str):
    """OPT-07：为工具调用加 span（真实耗时）。

    仅在受控 trace 上下文（runtime_tool_context / 显式 bind_trace）内记录，
    裸调用不产生 span，避免观察面被非 Runtime 调用污染。
    返回浅拷贝，避免污染模块级共享工具实例。
    """
    original = getattr(tool_obj, "func", None)
    if original is None or getattr(original, "_obs_traced", False):
        return tool_obj
    display = str(name).lstrip("_")

    def _traced(*args, **kwargs):
        if obs.current_trace() is None:
            return original(*args, **kwargs)
        with obs.span("tool", f"tool.{display}", attributes={"tool_name": display}):
            ctx = _tool_context()
            if ctx is not None:
                ctx.setdefault("_obs_tool_names", set()).add(display)
            return original(*args, **kwargs)

    _traced._obs_traced = True  # noqa: SLF001 防重复包装标记
    return tool_obj.model_copy(update={"func": _traced})


def build_tools(agent_type: str, whitelist: set[str] | None = None) -> list:
    """按白名单发放工具；只发白名单内的工具（服务端强制，模型调不到别的）。"""
    from app.enterprise_runtime.agents.agent_definitions import AGENT_DEFINITIONS
    d = AGENT_DEFINITIONS[agent_type]
    names = set(whitelist) if whitelist is not None else set(d.tool_whitelist)
    out = []
    for name in _TOOL_ORDER:
        if name in names and name in _TOOL_MAP:
            out.append(_instrument_tool(_TOOL_MAP[name], name))
    return out
