"""OPT-07 全链路可观测性演示（隔离启动：deterministic + 内存态 + 无外部写入）。

单条 trace 串起 6 类 span：Worker（任务步骤）→ 模型（Agent invoke）→ 产物（Artifact 写入）
→ 工具（受控上下文内真实工具调用，含耗时）→ RAG（真实 BM25 检索）→ 审批（E2 服务 approve）。

校验（全部真实执行，不伪造）：
- 6 类 span 齐全且同 trace 串联；耗时字段存在；Token 未采集如实标记 not_collected
- 错误类型可检索（HTTP 404 → error_kind=http_404）
- 脱敏边界：伪造凭据值出现在属性中必须被替换（不得出现在证据 JSON）
- 保留边界：retention 上限/计数/dropped_total 可查询

默认强制隔离（脚本自身设定，不依赖外部环境）：
- AGENT_PROVIDER=deterministic；USE_SQLALCHEMY/USE_CELERY=false；DATA_DIR 固定在 backend/.data/opt07-demo
- 不调用 enterprise_release_service：无 Gitee/GitHub 远端写入

用法（任意 cwd）：
  .venv/Scripts/python.exe -m scripts.opt07_trace_demo --out evidence.json

退出码：0=全部检查通过；2=存在失败检查（如实报告）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

DEMO_ROOT = BACKEND_ROOT / ".data" / "opt07-demo"

TENANT = "tenant_default"
PROJECT_ID = "proj_demo_payment"
REPOSITORY_ID = "repo_demo_payment"
DEMO_TASK_ID = "opt07_demo_trace"
RAG_QUERY = "retry_refund 回调重试"
FAKE_TOKEN = "opt07-demo-fake-token-1234567890"

REMOTE_WRITE_MODULE = "app.services.enterprise_release_service"


def _force_isolated_env() -> None:
    os.environ["APP_ENV"] = "local"
    os.environ["AGENT_PROVIDER"] = "deterministic"
    os.environ["USE_SQLALCHEMY"] = "false"
    os.environ["USE_CELERY"] = "false"
    os.environ["CELERY_BROKER_URL"] = ""
    os.environ["CELERY_RESULT_BACKEND"] = ""
    os.environ["DATA_DIR"] = str(DEMO_ROOT)
    DEMO_ROOT.mkdir(parents=True, exist_ok=True)


def _ctx(tenant_id: str = TENANT, role: str = "tenant_owner",
         user_id: str = "user_demo", user_name: str = "演示操作者"):
    return SimpleNamespace(tenant_id=tenant_id, role=role, user_id=user_id, user_name=user_name)


def _approve_via_e2(task_id: str, tenant_id: str) -> dict:
    """经企业 E2 审批服务批准 Runtime（真实审批链路；显式绑定同 trace 以串联审批 span）。"""
    from app.approvals import service as approval_service
    from app.approvals import store as approval_store
    from app.approvals.models import ApprovalApprovePayload
    from app.enterprise_runtime import observability as obs

    pending = next(
        (r for r in approval_store.APPROVALS.values()
         if r.tenant_id == tenant_id and r.resource_type == "runtime_task"
         and r.resource_id == task_id and r.approval_status == "pending"),
        None,
    )
    if pending is None:
        raise RuntimeError(f"未找到待审批的 Runtime 审批单: {task_id}")
    approver_id, approver_name = "user_approver", "审批人"
    token = obs.bind_trace(trace_id=task_id, tenant_id=tenant_id, run_id="demo-approval")
    try:
        approved = approval_service.approve(
            _ctx(tenant_id, role="tenant_owner", user_id=approver_id, user_name=approver_name),
            SimpleNamespace(id=approver_id, name=approver_name),
            pending.id,
            ApprovalApprovePayload(comment="OPT-07 演示审批通过"),
        )
    finally:
        obs.clear_trace(token)
    return {"approval_id": approved.id, "status": approved.approval_status,
            "decision": approved.approval_status}


def _drive_workflow(task_id: str) -> dict:
    """驱动 6-Agent 工作流（Planner 后经 E2 审批门），直到终态。"""
    from app.enterprise_runtime import orchestrator as orch
    from app.enterprise_runtime.agents.registry import AgentExecutorRegistry
    from app.enterprise_runtime.models import RuntimeStepStatus, RuntimeTaskStatus

    registry = AgentExecutorRegistry(audit=orch.agent_audit_cb())
    live: dict = {"approval": None, "approval_gate_held": False}
    ctx = _ctx()
    for _ in range(60):
        task = orch.run_agent_workflow("demo-worker", task_id, registry, ctx=ctx)
        if task.status == RuntimeTaskStatus.WAITING_APPROVAL:
            cc = next(s for s in task.steps if s.agent_type == "code_change_agent")
            live["approval_gate_held"] = (
                cc.status == RuntimeStepStatus.PENDING
                and "code_change_agent" not in task.completed_steps
            )
            live["approval"] = _approve_via_e2(task_id, TENANT)
            continue
        if task.status in (RuntimeTaskStatus.SUCCEEDED, RuntimeTaskStatus.FAILED,
                           RuntimeTaskStatus.WAITING_HUMAN, RuntimeTaskStatus.CANCELLED):
            break
    else:
        raise RuntimeError(f"演示任务未达终态（>60 轮驱动）: {task_id}")
    return {"final_status": task.status.value, **live}


def _tool_leg(task_id: str) -> dict:
    """工具链路：受控 Runtime 上下文中调用真实发放的工具（wrapper 记真实耗时 span）。"""
    from app.enterprise_runtime.agents import tools as agent_tools

    built = agent_tools.build_tools("requirement_agent")
    read_issue = next(t for t in built if "read_issue" in t.name)
    read_comments = next(t for t in built if "read_comments" in t.name)
    with agent_tools.runtime_tool_context(task_id=task_id, run_id="demo-tools", tenant_id=TENANT):
        issue_out = read_issue.invoke({"issue_text": "OPT-07 工具链路演示"})
        comments_out = read_comments.invoke({"ref": task_id})
    return {"read_issue_echo_ok": issue_out == "OPT-07 工具链路演示",
            "read_comments_output_len": len(comments_out)}


def _rag_leg(task_id: str) -> dict:
    """RAG 链路：真实 BM25 检索（进程内种子语料）；同 trace 绑定 → rag.search span 归入本 trace。"""
    from app.enterprise_rag.models import EnterpriseRagSearchPayload
    from app.enterprise_rag.service import enterprise_rag_search
    from app.enterprise_runtime import observability as obs
    from app.tenancy.models import TenantContext
    from app.tenancy.permissions import permissions_for
    from evaluation.enterprise_rag.seed import BASELINE_COMMIT, seed_baseline

    seed_baseline()
    role = "tenant_owner"
    ctx = TenantContext(user_id="user_demo", tenant_id=TENANT, role=role,
                        permissions=permissions_for(role))
    payload = EnterpriseRagSearchPayload(
        query=RAG_QUERY, project_id=PROJECT_ID, repository_id=REPOSITORY_ID,
        commit_sha=BASELINE_COMMIT, retrieval_mode="bm25",
    )
    token = obs.bind_trace(trace_id=task_id, tenant_id=TENANT, run_id="demo-rag")
    try:
        result = enterprise_rag_search(ctx, payload)
    finally:
        obs.clear_trace(token)
    hits = [{"file": item.get("file_path", ""), "symbol": item.get("symbol_name", "")}
            for item in (result.get("results") or [])]
    return {"mode": result.get("retrieval_mode"), "hit_count": len(hits),
            "expected_hit": any(h["symbol"] == "retry_refund" for h in hits)}


def _error_leg(task_id: str) -> dict:
    """错误类型链路：真实 404（仓库不存在）→ rag span status=error + error_kind=http_404。"""
    from fastapi import HTTPException
    from app.enterprise_rag.models import EnterpriseRagSearchPayload
    from app.enterprise_rag.service import enterprise_rag_search
    from app.enterprise_runtime import observability as obs
    from app.tenancy.models import TenantContext
    from app.tenancy.permissions import permissions_for

    role = "tenant_owner"
    ctx = TenantContext(user_id="user_demo", tenant_id=TENANT, role=role,
                        permissions=permissions_for(role))
    payload = EnterpriseRagSearchPayload(query="x", project_id="proj_missing",
                                         repository_id="repo_missing", commit_sha="0" * 40)
    token = obs.bind_trace(trace_id=task_id, tenant_id=TENANT, run_id="demo-error")
    try:
        try:
            enterprise_rag_search(ctx, payload)
            raised = False
        except HTTPException as exc:
            raised = exc.status_code == 404
    finally:
        obs.clear_trace(token)
    return {"raised_404": raised}


def _redaction_leg(task_id: str) -> dict:
    """脱敏边界：让伪造凭据值出现在属性中，落库必须被替换（不得出现在证据里）。"""
    from app.enterprise_runtime import observability as obs

    os.environ["GITEE_TOKEN"] = FAKE_TOKEN
    try:
        span = obs.record_span(
            "tool", "tool.redaction_probe", trace_id=task_id, tenant_id=TENANT,
            attributes={
                "api_key": FAKE_TOKEN,
                "error": f"clone failed: https://demo:{FAKE_TOKEN}@gitee.com/x.git",
                "nested": [{"note": f"token={FAKE_TOKEN}"}],
            },
        )
    finally:
        os.environ.pop("GITEE_TOKEN", None)
    return {"span_id": span.span_id}


def _run_demo() -> dict:
    from app.approvals import store as approval_store
    from app.enterprise_runtime import observability as obs
    from app.enterprise_runtime import orchestrator as orch
    from app.enterprise_runtime.artifacts import runs as runs_store
    from app.enterprise_runtime.artifacts import store as astore
    from app.enterprise_runtime import store as rstore

    rstore.reset_store()
    runs_store.reset_store()
    astore.reset_store()
    approval_store.reset_store()
    obs.reset_store()

    tool_stats = _tool_leg(DEMO_TASK_ID)
    rag_stats = _rag_leg(DEMO_TASK_ID)
    error_stats = _error_leg(DEMO_TASK_ID)
    _redaction_leg(DEMO_TASK_ID)

    orch.start_runtime_task(_ctx(), DEMO_TASK_ID, f"ik_{DEMO_TASK_ID}", project_id=PROJECT_ID,
                            repository_id=REPOSITORY_ID, title="OPT-07 全链路可观测性演示",
                            workflow_version=2)
    drive = _drive_workflow(DEMO_TASK_ID)

    spans = obs.query_spans(TENANT, trace_id=DEMO_TASK_ID, limit=500)
    cats = sorted({s.category for s in spans})
    by_cat = {c: sum(1 for s in spans if s.category == c) for c in cats}
    model_span = next((s for s in spans if s.category == "model"), None)
    err_span = next((s for s in spans if s.category == "rag" and s.status == "error"), None)
    approval_span = next((s for s in spans if s.category == "approval"), None)
    raw_json = json.dumps([s.model_dump(mode="json") for s in spans], ensure_ascii=False)

    checks = {
        "all_six_categories_present": set(cats) >= {"worker", "model", "artifact", "tool",
                                                    "rag", "approval"},
        "single_trace_chained": len(spans) >= 6 and all(s.trace_id == DEMO_TASK_ID for s in spans),
        "durations_recorded": bool(
            model_span and model_span.duration_ms is not None
            and all(s.duration_ms is not None for s in spans
                    if s.name != "tool.redaction_probe"
                    and not (s.category == "tool" and "agent_tool_trace" in s.attributes))
        ),
        "tokens_not_collected_honest": bool(
            model_span and model_span.tokens.get("status") == "not_collected"
        ),
        "error_kind_searchable": bool(err_span and err_span.error_kind == "http_404"),
        "redaction_enforced": FAKE_TOKEN not in raw_json,
        "retention_visible": obs.retention_info()["max_spans"] > 0,
        "approval_decision_recorded": bool(
            approval_span and approval_span.attributes.get("decision") == "approved"
        ),
        "tool_execution_with_duration": any(
            s.category == "tool" and s.duration_ms is not None for s in spans
        ),
        "workflow_succeeded": drive["final_status"] == "succeeded",
        "error_leg_raised_404": error_stats["raised_404"],
        "no_remote_write_module": REMOTE_WRITE_MODULE not in sys.modules,
    }
    return {
        "demo": "opt07_full_chain_observability",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tenant_id": TENANT,
        "trace_id": DEMO_TASK_ID,
        "environment": {
            "agent_provider": os.environ.get("AGENT_PROVIDER", ""),
            "stores": "in_memory",
            "database": "disabled",
            "celery": "disabled",
            "data_dir": str(DEMO_ROOT),
        },
        "tool_leg": tool_stats,
        "rag_leg": rag_stats,
        "span_summary": {"total": len(spans), "by_category": by_cat,
                         "status_counts": {
                             "ok": sum(1 for s in spans if s.status == "ok"),
                             "error": sum(1 for s in spans if s.status == "error"),
                             "blocked": sum(1 for s in spans if s.status == "blocked"),
                         }},
        "retention": obs.retention_info(),
        "model_span": ({"name": model_span.name, "duration_ms": model_span.duration_ms,
                        "tokens": model_span.tokens,
                        "attributes": {"agent_type": model_span.attributes.get("agent_type")}}
                       if model_span else None),
        "error_span": ({"name": err_span.name, "status": err_span.status,
                        "error_kind": err_span.error_kind} if err_span else None),
        "approval_span": ({"name": approval_span.name,
                           "decision": approval_span.attributes.get("decision")}
                          if approval_span else None),
        "workflow": drive,
        "checks": {**checks, "all_passed": all(checks.values())},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="OPT-07 全链路可观测性隔离演示")
    parser.add_argument("--out", default=str(DEMO_ROOT / "opt07_trace_evidence.json"))
    args = parser.parse_args()

    _force_isolated_env()
    report = _run_demo()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    passed = sum(1 for v in report["checks"].values() if v is True)
    total = len(report["checks"])
    print(f"OPT-07 demo: trace={report['trace_id']} spans={report['span_summary']['total']} "
          f"categories={sorted(report['span_summary']['by_category'])} checks={passed}/{total}")
    print(f"evidence={out.resolve()}")
    if not report["checks"]["all_passed"]:
        print(json.dumps(report["checks"], ensure_ascii=False, indent=2))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
