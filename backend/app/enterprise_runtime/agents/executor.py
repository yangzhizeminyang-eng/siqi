"""企业版 E4-02 AgentExecutor：独立调用 Agent，产物写入 ArtifactStore，记录 AgentRun。

- 每次调用产生一条 AgentRun（含 attempt/repair_round）。
- 只经 ArtifactStore/refs 传参：resolve_refs 受控物化 + 按 agent 的 allowed_read_artifact_types 过滤类型；
  下游拿到的是“允许字段投影”，不是上游完整原文（C8）。
- 组装并验证定义好的 input_schema / output_schema。
- 工具/产物写前过 AgentToolPolicyValidator（C7）。
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from datetime import datetime, timezone

from app.enterprise_runtime.artifacts import runs as runs_store
from app.enterprise_runtime.artifacts import store as artifacts_store
from app.enterprise_runtime.artifacts.models import AgentArtifact
from app.enterprise_runtime.artifacts.runs import AgentRun
from app.enterprise_runtime import observability as obs
from app.enterprise_runtime.agents.agent_definitions import AGENT_DEFINITIONS
from app.enterprise_runtime.agents.factory import build_agent
from app.enterprise_runtime.agents.policy import AgentToolPolicyValidator, build_policy_context

# 每个 agent 产出产物类型的 content 投影（对应 allowed_write_artifact_types）。
# 必须是元组（有序）：写入顺序决定 ArtifactStore/审计事件顺序；若用 set，跨进程迭代顺序
# 会随字符串 hash 随机化与 lambda id 地址漂移，破坏 OPT-03 演示的跨进程可复现性。
_PROJECTION = {
    "requirement_agent": (("RequirementArtifact", lambda o: {k: o.get(k) for k in ("summary", "requirements", "open_questions", "completeness")}),),
    "repository_research_agent": (
        ("ResearchArtifact", lambda o: {k: o.get(k) for k in ("findings",)}),
        ("CodeContextArtifact", lambda o: {k: o.get(k) for k in ("files", "symbols", "code_context")}),
    ),
    "planner_agent": (("PlanArtifact", lambda o: {k: o.get(k) for k in ("objectives", "steps", "test_command", "approval_request_note")}),),
    "code_change_agent": (
        ("PatchArtifact", lambda o: {k: o.get(k) for k in ("workspace_path", "files_changed", "patch", "test_command")}),
        ("DiffArtifact", lambda o: {"diff_ref": o.get("diff_ref") or "", "patch": o.get("patch") or ""}),
    ),
    "review_security_agent": (
        ("ReviewArtifact", lambda o: {k: o.get(k) for k in ("approved", "conclusions", "issues")}),
        ("SecurityRiskArtifact", lambda o: {"security_risks": o.get("security_risks") or []}),
    ),
    "test_release_agent": (
        ("TestReportArtifact", lambda o: {k: o.get(k) for k in ("passed", "summary", "test_cases")}),
        ("PRDescriptionArtifact", lambda o: {"pr_description": o.get("pr_description") or ""}),
        ("ReleaseEvidenceArtifact", lambda o: o.get("release_evidence") or {}),
    ),
}

# 下游可见的 content 字段：agent_type -> artifact_type -> allowed_content_fields（#1）
# RepositoryResearch 读 RequirementArtifact 时也要能拿到 summary/requirements 等，故按类型而非整体泛化。
_CONTENT_BY_TYPE = {
    "requirement_agent": {},
    "repository_research_agent": {
        "RequirementArtifact": {"summary", "requirements", "open_questions", "completeness"},
    },
    "planner_agent": {
        "RequirementArtifact": {"summary", "requirements", "open_questions", "completeness"},
        "ResearchArtifact": {"findings"},
        "CodeContextArtifact": {"files", "symbols", "code_context"},
    },
    "code_change_agent": {
        "PlanArtifact": {"objectives", "steps", "test_command", "approval_request_note"},
        "CodeContextArtifact": {"files", "symbols", "code_context"},
    },
    "review_security_agent": {
        "PlanArtifact": {"objectives", "steps", "test_command"},
        "PatchArtifact": {"workspace_path", "files_changed", "patch", "test_command"},
        "DiffArtifact": {"diff_ref", "patch"},
    },
    "test_release_agent": {
        "DiffArtifact": {"diff_ref", "patch"},
        "ReviewArtifact": {"approved", "conclusions", "issues", "security_risks"},
    },
}

# input_schema 的形式字段：把“哪个 artifact 类型的引用映射到哪个 *_ref 字段”
_INPUT_REF_MAP = {
    "requirement_agent": {},
    "repository_research_agent": {"RequirementArtifact": "requirement_ref"},
    "planner_agent": {"RequirementArtifact": "requirement_ref",
                      "ResearchArtifact": "research_ref", "CodeContextArtifact": "context_ref"},
    "code_change_agent": {"PlanArtifact": "plan_ref", "CodeContextArtifact": "context_ref"},
    "review_security_agent": {"PlanArtifact": "plan_ref", "DiffArtifact": "diff_ref"},
    "test_release_agent": {"DiffArtifact": "diff_ref", "ReviewArtifact": "review_ref"},
}

# 每个 agent 的真实运行必须具备的能力；缺环境时 real 运行不得伪造成功（constraint 9/13）。
_REQUIRED_ENV = {
    "repository_research_agent": ("rag",),
    "code_change_agent": ("workspace",),
    "test_release_agent": ("test_runner",),
}


def _reachable(host: str, port: int, t: float = 1.0) -> bool:
    try:
        s = socket.create_connection((host, port), t)
        s.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def required_env_missing(agent_type: str) -> list[str]:
    """真实运行下强制能力检查；缺失返回原因列表（仅 real provider 生效）。"""
    if os.environ.get("AGENT_PROVIDER", "").strip().lower() != "real":
        return []  # deterministic/测试路径不做环境门禁
    miss: list[str] = []
    for cap in _REQUIRED_ENV.get(agent_type, ()):
        if cap == "rag":
            try:
                from app.core.runtime_config import load_runtime_settings
                uri = (getattr(load_runtime_settings(), "milvus_uri", "") or "")
                h = uri.split("://")[-1].split("/")[0]
                host = h.split(":")[0]; port = int(h.split(":")[1]) if ":" in h else 19530
                if not _reachable(host, port):
                    miss.append("rag(milvus 不可达)")
            except Exception:  # noqa: BLE001
                miss.append("rag(配置不可读)")
        elif cap == "workspace":
            root = os.environ.get("AGENT_WORKSPACE_ROOT", "").strip()
            if not root or not os.path.isdir(root):
                miss.append("workspace(AGENT_WORKSPACE_ROOT 未设置/非目录)")
        elif cap == "test_runner":
            r = subprocess.run(["docker", "info"], capture_output=True)
            if r.returncode != 0:
                miss.append("test_runner(docker daemon 未运行)")
    return miss


class AgentExecutor:
    def __init__(self, agent_type: str, *, validator: AgentToolPolicyValidator | None = None,
                 audit: callable | None = None):
        self.agent_type = agent_type
        self.definition = AGENT_DEFINITIONS[agent_type]
        self.validator = validator or AgentToolPolicyValidator(AGENT_DEFINITIONS, audit=audit)
        self._audit = audit

    def _resolve_visible_refs(self, refs: list[str], tenant: str, step):
        """只解析本 agent 允许读取的 artifact 类型（含 Store 侧双重校验，constraint #4）。

        越权/跨租户/无效引用 → 拒绝为 None + runtime.artifact.denied 审计，且不进入正式输入。
        """
        allowed_types = set(self.definition.allowed_read_artifact_types or ())
        by_type = _CONTENT_BY_TYPE.get(self.agent_type, {})
        visible = []
        denied = []
        for r in refs:
            art = artifacts_store.get(r)
            if art is None:
                denied.append((r, "unresolved"))
                self._audit(tenant, "runtime.artifact.denied", step, f"无效引用 {r}")
            elif art.tenant_id != tenant:
                denied.append((r, "cross_tenant"))
                self._audit(tenant, "runtime.artifact.denied", step, f"跨租户引用被拒 {r}")
            elif art.artifact_type.value not in allowed_types:
                denied.append((r, "type_not_allowed"))
                self._audit(tenant, "runtime.artifact.denied", step,
                            f"类型越权 {r}（{art.artifact_type.value} not in {sorted(allowed_types)}）")
            else:
                visible.append(r)
        # Store 侧再校验一次（tenant/引用/类型/字段白名单）；防御纵深，双重权限
        resolved = artifacts_store.resolve_refs(
            visible, requester_tenant_id=tenant, requester_agent_type=self.agent_type,
            allowed_read_artifact_types=allowed_types, allowed_fields_by_type=by_type)
        for r in list(visible):
            if resolved.get(r) is None:
                denied.append((r, "store_denied"))
                self._audit(tenant, "runtime.artifact.denied", step, f"Store 拒绝引用 {r}")
                visible.remove(r)
        resolved = {k: v for k, v in resolved.items() if v is not None}
        return resolved, visible, denied

    def _build_structured_input(self, *, task, step, resolved: dict, visible_refs: list[str]) -> dict:
        base: dict = {
            "task_id": task.id,
            "tenant_id": task.tenant_id,
            "repository_id": getattr(task, "repository_id", "") or "",
            "project_id": getattr(task, "project_id", "") or "",
        }
        # requirement 首 agent：无上游 ref，直接以任务描述作为 issue 输入
        if self.agent_type == "requirement_agent":
            base["issue"] = getattr(task, "title", "") or "实现企业版多 Agent 协作需求"
            base["comments"] = []
            base["history_summary"] = ""
        # 把可见引用映射成 input_schema 的 *_ref 形式字段 + 附加受控内容 _resolved
        ref_map = _INPUT_REF_MAP.get(self.agent_type, {})
        for ref in visible_refs:
            art = artifacts_store.get(ref)
            if art is None:
                continue
            field = ref_map.get(art.artifact_type.value)
            if field:
                base[field] = ref
        # 受控内容（允许字段投影）并入，供下游/真实 agent 使用；不传递被拒绝类型
        base["_resolved"] = {k: v for k, v in resolved.items() if v is not None}
        base["_input_refs"] = visible_refs
        return base

    def run(self, *, ctx, task, step, plan: dict | None = None) -> dict:
        attempt = step.agent_attempt + 1
        repair_round = step.repair_round
        refs = (plan or {}).get("input_refs") or getattr(step, "ready_refs", None) or []
        if not isinstance(refs, (list, tuple)):
            refs = []
        tenant = task.tenant_id

        resolved, visible, _denied = self._resolve_visible_refs(
            [r for r in refs if isinstance(r, str)], tenant, step)
        if _denied:
            self._audit(tenant, "runtime.artifact.denied_count", step,
                        f"{self.agent_type} 拒绝 {len(_denied)} 个引用")
        policy_context = build_policy_context(self.definition, tenant_id=tenant, task_id=task.id)

        run_id = f"run_{task.id}_{self.agent_type}_{attempt}_{datetime.now(timezone.utc).timestamp():.0f}"
        run = AgentRun(
            run_id=run_id, task_id=task.id, tenant_id=tenant, agent_type=self.agent_type,
            step_id=step.step_id, attempt=attempt, repair_round=repair_round,
            status="running", prompt_version="1",
            input_refs=visible,
            policy_context=policy_context, model=(getattr(self.definition, "provider", "") or ""),
            idempotency_key=f"{task.id}:{self.agent_type}:{attempt}:{repair_round}",
        )
        runs_store.put(run)

        # 真实运行强制能力门禁：缺环境 → BLOCKED/failed，不伪造成功（constraint 9/13）
        missing_env = required_env_missing(self.agent_type)
        if missing_env:
            run.status = "failed"
            run.error_reason = "BLOCKED: " + ", ".join(missing_env)
            run.finished_at = _now()
            runs_store.put(run)
            self._audit_event(tenant, "runtime.agent.blocked", step,
                              f"{self.agent_type} 缺真实环境: {', '.join(missing_env)}")
            return {"ok": False, "output_summary": "", "failure_kind": "environment",
                    "reason": run.error_reason, "run_id": run_id, "attempt": attempt}

        structured_input = self._build_structured_input(task=task, step=step,
                                                        resolved=resolved, visible_refs=visible)
        # 声明了 input_schema：组装后校验（不完整即视为协议违规）
        try:
            self.definition.input_schema.model_validate(structured_input)
        except Exception as e:  # noqa: BLE001
            run.status = "failed"; run.error_reason = f"input_schema 校验失败: {e}"; run.finished_at = _now()
            runs_store.put(run)
            return {"ok": False, "output_summary": "", "failure_kind": "non_retryable",
                    "reason": f"input_schema 校验失败: {str(e)[:200]}", "run_id": run_id, "attempt": attempt}

        backend = build_agent(self.agent_type)
        invoke_started = time.perf_counter()
        tool_trace = []
        recorded_tools: set[str] = set()
        try:
            from app.enterprise_runtime.agents import tools as agent_tools

            diff_ref = ""
            review_ref = ""
            for ref in visible:
                referenced = artifacts_store.get(ref)
                if referenced is None:
                    continue
                if referenced.artifact_type.value == "DiffArtifact":
                    diff_ref = ref
                elif referenced.artifact_type.value == "ReviewArtifact":
                    review_ref = ref
            # OPT-07：模型调用 span（耗时/错误类型/Token 如实记录；工具调用经 wrapper 记真实耗时）
            with obs.span(
                "model", "model.invoke", trace_id=task.id, tenant_id=tenant,
                attributes={
                    "agent_type": self.agent_type,
                    "run_id": run_id,
                    "attempt": attempt,
                    "repair_round": repair_round,
                    "provider": getattr(self.definition, "provider", "") or "",
                    "input_refs": len(visible),
                },
            ) as model_span:
                with agent_tools.runtime_tool_context(
                    task_id=task.id,
                    run_id=run_id,
                    diff_ref=diff_ref,
                    review_ref=review_ref,
                    tenant_id=tenant,
                    actor_id=str(getattr(ctx, "user_id", "") or ""),
                    role=str(getattr(ctx, "role", "") or ""),
                    workspace_root=os.environ.get("MCP_SCAN_ALLOWED_ROOT", ".data/mcp-sandbox"),
                    audit=lambda tenant_id, event, _step, summary: self._audit_event(
                        tenant_id, event, step, summary
                    ),
                ) as tool_state:
                    if hasattr(backend, "invoke_with_trace"):
                        output, tool_trace = backend.invoke_with_trace(structured_input)
                    else:
                        output = backend.invoke(structured_input)
                        tool_trace = []
                    if self.agent_type == "test_release_agent":
                        output = agent_tools.finalize_test_release_output(output)
                    recorded_tools = set(tool_state.get("_obs_tool_names") or ())
                usage = getattr(backend, "last_usage", None) or {}
                model_span.tokens = dict(usage.get("tokens") or {"status": "not_collected"})
                model_span.attributes["tool_calls"] = len(tool_trace)
                self.definition.output_schema.model_validate(output)
            invoke_duration_ms = round((time.perf_counter() - invoke_started) * 1000, 2)
            self._emit_tool_spans(tenant, task.id, run_id, tool_trace, recorded_tools)
        except Exception as e:  # noqa: BLE001
            self._emit_tool_spans(tenant, task.id, run_id, tool_trace, recorded_tools)
            run.status = "failed"
            run.error_reason = str(e)[:300]
            run.finished_at = _now()
            run.usage = {
                "attempt": attempt,
                "repair_round": repair_round,
                "input_refs": len(visible),
                "duration_ms": round((time.perf_counter() - invoke_started) * 1000, 2),
                "tool_trace": tool_trace,
                "cost": {"status": "not_collected", "value": None},
            }
            runs_store.put(run)
            self._audit_event(tenant, "runtime.agent.failed", step, f"{self.agent_type} invoke 失败: {str(e)[:120]}")
            return {"ok": False, "output_summary": "", "failure_kind": "retryable",
                    "reason": str(e)[:300], "run_id": run_id, "attempt": attempt}

        if self.agent_type == "test_release_agent" and classify_test_release_output(output) == "environment":
            run.status = "failed"
            run.error_reason = "BLOCKED: TestRelease 依赖或发布证据环境不可用"
            run.finished_at = _now()
            runs_store.put(run)
            self._audit_event(
                tenant,
                "runtime.agent.blocked",
                step,
                "test_release_agent 输出包含环境 BLOCKED，转人工，不回退 CodeChange",
            )
            return {
                "ok": False,
                "output_summary": _summarize(output),
                "failure_kind": "environment",
                "reason": run.error_reason,
                "run_id": run_id,
                "attempt": attempt,
            }

        artifact_ids = []
        projections = _PROJECTION.get(self.agent_type, ())
        for art_type, project in projections:
            self.validator.assert_artifact_write_allowed(self.agent_type, art_type)
            content = project(output)
            art_id = f"art_{task.id}_{self.agent_type}_{art_type}_{attempt}.{repair_round}"
            art = AgentArtifact(
                artifact_id=art_id, task_id=task.id, tenant_id=tenant, agent_type=self.agent_type,
                artifact_type=art_type, run_id=run_id,
                input_refs=visible,
                output_refs=[f"{run_id}"],
                content=content, idempotency_key=f"{task.id}:{self.agent_type}:{attempt}:{repair_round}:{art_type}",
            )
            with obs.span("artifact", "artifact.write", trace_id=task.id, tenant_id=tenant,
                          attributes={"artifact_id": art_id, "artifact_type": art_type,
                                      "agent_type": self.agent_type, "run_id": run_id}):
                stored = artifacts_store.put(art)
            artifact_ids.append(stored.artifact_id)

        run.status = "succeeded"; run.output_refs = artifact_ids; run.finished_at = _now()
        run.usage = {
            "attempt": attempt,
            "repair_round": repair_round,
            "input_refs": len(visible),
            "duration_ms": invoke_duration_ms,
            "tool_trace": tool_trace,
            "cost": {"status": "not_collected", "value": None},
        }
        runs_store.put(run)
        self._audit_event(tenant, "runtime.agent.succeeded", step,
                          f"{self.agent_type} 成功 attempt={attempt} round={repair_round} artifacts={len(artifact_ids)}")

        return {
            "ok": True,
            "output_summary": _summarize(output),
            "output": output,
            "artifact_ids": artifact_ids,
            "run_id": run_id,
            "attempt": attempt,
            "failure_kind": "", "reason": "",
        }

    def _audit_event(self, tenant_id: str, event_type: str, step, summary: str) -> None:
        if self._audit is not None:
            try:
                self._audit(tenant_id, event_type, step, summary)
            except Exception:  # noqa: BLE001
                pass

    def _emit_tool_spans(self, tenant: str, trace_id: str, run_id: str, tool_trace,
                         recorded_tools: set[str]) -> None:
        """工具 span 兜底：仅对「未被工具 wrapper 记录真实执行」的调用记名（不重复计数）。"""
        for entry in list(tool_trace or []):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("tool_name") or "").strip().lstrip("_")
            if not name or name in recorded_tools:
                continue
            obs.record_span(
                "tool", f"tool.{name}", trace_id=trace_id, tenant_id=tenant,
                duration_ms=entry.get("duration_ms"),
                attributes={"tool_name": name, "run_id": run_id, "source": "agent_tool_trace"},
            )


def _summarize(output: dict) -> str:
    import json
    return json.dumps(output, ensure_ascii=False, default=str)[:160]


def classify_test_release_output(output: dict) -> str:
    """Classify TestRelease output before graph routing.

    A real test failure is a repair signal. A runner/PR/evidence environment
    block is a human-action signal and must not trigger another CodeChange loop.
    """
    if not isinstance(output, dict):
        return "retryable"

    cases = output.get("test_cases") or []
    blocked_markers = ("blocked", "unavailable", "not_executed", "未执行", "未配置", "不可用")
    for case in cases:
        if not isinstance(case, dict):
            continue
        status = str(case.get("status") or case.get("result") or "").lower()
        if status in {"failed", "error", "failure"}:
            return "retryable"
        values = " ".join(
            str(case.get(key, ""))
            for key in ("status", "result", "reason", "detail", "failure_cause")
        ).lower()
        if any(marker in values for marker in blocked_markers):
            return "environment"

    evidence = output.get("release_evidence") or {}
    evidence_text = " ".join(
        str(evidence.get(key, ""))
        for key in ("status", "reason", "blocked_reason", "blocked_reasons", "test_status")
    ).lower()
    if any(marker in evidence_text for marker in blocked_markers):
        return "environment"
    return "retryable" if output.get("passed") is False else "success"


def _now():
    return datetime.now(timezone.utc)
