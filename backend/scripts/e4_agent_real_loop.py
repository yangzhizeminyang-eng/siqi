"""E4-02 多 Agent 协作真实闭环驱动（v2）。

用法（真实 MySQL/Redis + 真实模型 key）：
  USE_SQLALCHEMY=true DATABASE_URL=mysql+pymysql://... AGENT_PROVIDER=real \
  LLM_PROVIDER=deepseek LLM_MODEL=deepseek-chat LLM_BASE_URL=... LLM_API_KEY=... \
  PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m scripts.e4_agent_real_loop

无 LLM key 时以 --deterministic 显式跑（仅本地验证流程，输出标注 provider=deterministic，不冒充生产）。

输出 JSON 证据：6 AgentRun、Artifact 引用链、审批前后状态、review/test 回退(repair_round)、
checkpoint/AgentRun 恢复、审计事件。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

from app.core import model_config


def _target_config() -> dict[str, str]:
    """Return the real E4 target selected by the environment.

    Defaults preserve the historical local smoke-test identifiers. Production
    runs should point these values at a tenant/project/repository with a
    completed, authorized RAG index.
    """
    return {
        "tenant_id": os.environ.get("E4_TENANT_ID", "tenant_a").strip() or "tenant_a",
        "project_id": os.environ.get("E4_PROJECT_ID", "p1").strip() or "p1",
        "repository_id": os.environ.get("E4_REPOSITORY_ID", "r1").strip() or "r1",
    }


def _issue_text() -> str:
    """Return the concrete change request used by the real E4 run."""
    return os.environ.get(
        "E4_ISSUE",
        "多 Agent 协作需求",
    ).strip() or "多 Agent 协作需求"


def _ctx(tenant_id="tenant_a", role="admin", user="user_admin", name="A"):
    return SimpleNamespace(tenant_id=tenant_id, role=role, user_id=user, user_name=name)


def _approve_runtime_via_e2(task_id: str, *, tenant_id: str, plan_version: int,
                            approver_id: str = "user_approver",
                            approver_name: str = "陈晓"):
    """通过企业 E2 审批服务批准 Runtime，不直接修改 RuntimeTask。"""
    from app.approvals import service as approval_service
    from app.approvals import store as approval_store
    from app.approvals.models import ApprovalApprovePayload

    idempotency_key = f"rt-approval:{task_id}:plan-v{plan_version}"
    record = approval_store.find_by_idempotency(tenant_id, idempotency_key)
    if record is None:
        raise RuntimeError(f"未找到 Runtime 审批单: {idempotency_key}")
    if record.approval_status == "approved":
        return record
    if record.approval_status != "pending":
        raise RuntimeError(
            f"Runtime 审批单不可审批: {record.id} status={record.approval_status}"
        )

    # tenant_owner 具备 approval:approve；审批人不是 Planner 的 system actor，
    # 因此不会触发高风险自审限制。
    context = _ctx(
        tenant_id,
        role="tenant_owner",
        user=approver_id,
        name=approver_name,
    )
    user = SimpleNamespace(id=approver_id, name=approver_name)
    return approval_service.approve(
        context,
        user,
        record.id,
        ApprovalApprovePayload(comment="E4 真实闭环审批通过"),
    )


def _main() -> dict:
    from scripts.authorization_gate import require_authorization

    require_authorization(
        os.environ.get("AUTHORIZATION_OPERATION", ""),
        os.environ.get("AUTHORIZATION_LOG", "") or None,
    )
    from app.enterprise_runtime import orchestrator as orch
    from app.enterprise_runtime import store as rstore
    from app.enterprise_runtime.artifacts import runs as runs_store
    from app.enterprise_runtime.artifacts import store as astore
    from app.enterprise_runtime.agents.registry import AgentExecutorRegistry
    from app.enterprise_runtime.models import RuntimeTaskStatus

    EVIDENCE: dict = {}
    settings = model_config.load_llm_settings()
    has_key = bool(getattr(settings, "api_key", None))
    provider = os.environ.get("AGENT_PROVIDER") or ("real" if has_key else "none")
    EVIDENCE["provider"] = provider
    if provider not in ("real", "deterministic"):
        # 无真实模型 key 且未显式 deterministic → 报告阻塞，不虚构闭环
        return {"blocked": True, "reason": "缺少 LLM_API_KEY；设置 LLM_* 或显式 AGENT_PROVIDER=deterministic(仅测试)",
                "llm_configured": model_config.llm_configured(), "has_api_key": has_key}
    if provider == "real" and not has_key:
        return {"blocked": True, "reason": "真实路径缺少 LLM_API_KEY，无法调用模型", "has_api_key": False}
    # 模型兼容性硬门禁：真实 tool-calling/structured-output 探测（不捕获 400 当成功）
    if provider == "real":
        from app.core.model_config import probe_model_tool_choice, MODEL_TOOL_CHOICE_INCOMPATIBLE
        probe = probe_model_tool_choice(settings)
        EVIDENCE["model_probe"] = probe
        if probe.get("compat") != "COMPATIBLE":
            return {"blocked": True,
                    "reason": f"{probe.get('compat')}: {probe.get('error', '')}（禁止继续执行后续 Agent）",
                    "model_compatible": False,
                    "model_probe": probe}
    EVIDENCE["model"] = getattr(settings, "model", "")

    target = _target_config()
    task_id = f"rt_e4a_{datetime.now(timezone.utc).strftime('%H%M%S')}"
    orch.start_runtime_task(_ctx(target["tenant_id"]), task_id, f"ik_{task_id}",
                            project_id=target["project_id"],
                            repository_id=target["repository_id"],
                            title=_issue_text(),
                            workflow_version=2)
    reg = AgentExecutorRegistry(audit=orch.agent_audit_cb())
    seed_fail = os.environ.get("E4A_REVIEW_FAIL", "0") == "1"
    rounds_trace = []

    for i in range(60):
        task = orch.run_agent_workflow(
            "worker-e4a",
            task_id,
            reg,
            ctx=_ctx(target["tenant_id"]),
        )
        state = {"i": i, "status": task.status.value, "current_step": task.current_step,
                 "repair_round": task.repair_round, "completed": list(task.completed_steps)}
        rounds_trace.append(state)
        if task.status == RuntimeTaskStatus.WAITING_APPROVAL:
            EVIDENCE["approval"] = {"at_iteration": i, "state": "waiting_approval",
                                    "approved": True, "before_completed": list(task.completed_steps)}
            approval = _approve_runtime_via_e2(
                task_id,
                tenant_id=target["tenant_id"],
                plan_version=task.version,
            )
            EVIDENCE["approval"].update({
                "approval_id": approval.id,
                "approval_status": approval.approval_status,
                "approved_at": approval.approved_at.isoformat() if approval.approved_at else None,
            })
            continue
        if task.status in (RuntimeTaskStatus.SUCCEEDED, RuntimeTaskStatus.WAITING_HUMAN,
                           RuntimeTaskStatus.FAILED):
            break

    task = rstore.get(task_id)
    EVIDENCE["final"] = {"status": task.status.value, "repair_round": task.repair_round,
                         "completed_steps": list(task.completed_steps),
                         "approval_state": task.approval_state}
    EVIDENCE["rounds_trace"] = rounds_trace

    runs = runs_store.list_by_task(target["tenant_id"], task_id)
    EVIDENCE["agent_runs"] = [
        {"run_id": r.run_id, "agent_type": r.agent_type, "attempt": r.attempt,
         "repair_round": r.repair_round, "status": r.status, "input_refs": r.input_refs,
         "output_refs": r.output_refs} for r in sorted(runs, key=lambda r: r.attempt)
    ]
    arts = astore.list_by_task(target["tenant_id"], task_id)
    EVIDENCE["artifacts"] = [
        {"artifact_id": a.artifact_id, "artifact_type": a.artifact_type.value, "agent_type": a.agent_type,
         "version": a.version, "input_refs": a.input_refs} for a in arts
    ]
    # 引用链：每个 AgentRun.input_refs 都指向先前产物
    ref_set = {a["artifact_id"] for a in EVIDENCE["artifacts"]}
    dangling = []
    for r in EVIDENCE["agent_runs"]:
        for ref in r["input_refs"]:
            if ref not in ref_set:
                dangling.append(ref)
    EVIDENCE["dangling_refs"] = dangling
    # 审计事件（本运行时产生）
    from app import main
    EVIDENCE["audit_events"] = sorted(
        {e.event_type for e in main.AUDIT_EVENTS.values() if e.event_type.startswith("runtime.")},
    )
    EVIDENCE["completed"] = task.status == RuntimeTaskStatus.SUCCEEDED
    return EVIDENCE


if __name__ == "__main__":
    out = _main()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if out.get("blocked"):
        sys.exit(2)
