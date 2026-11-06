"""Run the fixed enterprise Agent Eval dataset in an isolated deterministic runtime."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Fixed, cwd-independent isolation path for every Agent Eval run.
EVAL_SANDBOX = BACKEND_ROOT / ".data" / "agent-eval-sandbox"


def _guard_provider(provider: str, allow_real_model: bool) -> str:
    if allow_real_model:
        return provider or "provider_from_env"
    if provider != "deterministic":
        raise RuntimeError(
            "Agent Eval requires AGENT_PROVIDER=deterministic "
            "(use --allow-real-model for an explicit opt-in real model run)"
        )
    return provider


def _run_isolated_case(case: dict, allow_real_model: bool = False) -> dict:
    from types import SimpleNamespace

    from app.approvals import store as approval_store
    from app.core.runtime_config import load_runtime_settings
    from app.enterprise_runtime import orchestrator, store as runtime_store
    from app.enterprise_runtime.agents.registry import AgentExecutorRegistry
    from app.enterprise_runtime.artifacts import runs as run_store
    from app.enterprise_runtime.artifacts import store as artifact_store
    from app.enterprise_runtime.models import RuntimeTaskStatus

    settings = load_runtime_settings()
    _guard_provider(os.environ.get("AGENT_PROVIDER", "").strip().lower(), allow_real_model)
    if settings.use_sqlalchemy or settings.celery_broker_url:
        raise RuntimeError("Agent Eval refuses database or Celery connectivity")

    runtime_store.reset_store()
    run_store.reset_store()
    artifact_store.reset_store()
    approval_store.reset_store()

    case_id = str(case["case_id"])
    task_id = f"agent_eval_{case_id}"
    tenant_id = "tenant_agent_eval"
    ctx = SimpleNamespace(tenant_id=tenant_id, user_id="agent-eval", user_name="Agent Eval", role="admin")
    runtime_task = orchestrator.start_runtime_task(
        ctx, task_id, f"agent-eval:{case_id}", project_id="isolated-eval",
        repository_id="isolated-eval", title=str(case.get("issue") or case.get("title") or "Agent Eval"),
        workflow_version=2, dispatch=False,
    )
    registry = AgentExecutorRegistry(audit=lambda *_args, **_kwargs: None)
    approval_events: list[str] = []
    observed_agent_types: list[str] = []
    started = time.perf_counter()

    for _ in range(40):
        runtime_task = orchestrator.run_agent_workflow("agent-eval-worker", task_id, registry, ctx=ctx)
        for run in run_store.list_by_task(tenant_id, task_id):
            if run.agent_type not in observed_agent_types:
                observed_agent_types.append(run.agent_type)
            if run.agent_type == "code_change_agent" and "code_change_started" not in approval_events:
                approval_events.append("code_change_started")
        if runtime_task.status == RuntimeTaskStatus.WAITING_APPROVAL:
            approval_events.append("requested")
            decision = str(case.get("approval_decision", "reject"))
            if decision not in {"approve", "reject"}:
                raise ValueError(f"Unsupported isolated approval decision: {decision}")
            approval_events.append("approved" if decision == "approve" else "rejected")
            runtime_task = orchestrator.approve_runtime_task(
                task_id, decision == "approve", actor="agent-eval", actor_name="Agent Eval",
            )
        if runtime_task.status in {
            RuntimeTaskStatus.SUCCEEDED, RuntimeTaskStatus.FAILED,
            RuntimeTaskStatus.WAITING_HUMAN, RuntimeTaskStatus.CANCELLED,
        }:
            break
    else:
        raise RuntimeError(f"Agent Eval case did not reach a terminal state: {case_id}")

    runs = run_store.list_by_task(tenant_id, task_id)
    policy: dict[str, list[str]] = {}
    trace: dict[str, list[dict]] = {}
    for run in runs:
        policy[run.agent_type] = list(run.policy_context.get("tool_whitelist") or [])
        trace.setdefault(run.agent_type, []).extend(run.usage.get("tool_trace") or [])
    run_metrics = []
    for run in runs:
        if run.status == "succeeded":
            run_failure_class = "none"
        elif str(run.error_reason or "").startswith("BLOCKED:"):
            run_failure_class = "environment_blocked"
        else:
            run_failure_class = "agent_failed"
        run_metrics.append({
            "agent_type": run.agent_type,
            "status": run.status,
            "failure_class": run_failure_class,
            "duration_ms": run.usage.get("duration_ms"),
            "tool_trace": run.usage.get("tool_trace") or [],
        })
    artifacts = artifact_store.list_by_task(tenant_id, task_id)
    if runtime_task.status == RuntimeTaskStatus.SUCCEEDED:
        failure_class = "none"
    elif runtime_task.approval_state == "rejected":
        failure_class = "approval_rejected"
    elif any(str(run.error_reason or "").startswith("BLOCKED:") for run in runs):
        failure_class = "environment_blocked"
    elif runtime_task.status == RuntimeTaskStatus.WAITING_HUMAN:
        failure_class = "human_handoff"
    else:
        failure_class = "agent_failed"

    return {
        "workflow_status": runtime_task.status.value,
        "agent_types": observed_agent_types,
        "artifact_types": [artifact.artifact_type.value for artifact in artifacts],
        "approval_events": approval_events,
        "tool_policy": policy,
        "tool_trace": trace,
        "agent_runs": run_metrics,
        "failure_class": failure_class,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "cost": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the isolated deterministic enterprise Agent Eval")
    parser.add_argument(
        "--dataset",
        default=str(Path(__file__).resolve().parents[1] / "evaluation" / "enterprise_agents" / "e4_agent_eval_v1.json"),
    )
    parser.add_argument("--out", required=True, help="Output report JSON path")
    parser.add_argument("--model-version", default="deterministic-v1")
    parser.add_argument("--prompt-version", default="agent-prompts-v1")
    parser.add_argument("--compare-with", default="", help="Compare against a prior report on the same dataset")
    parser.add_argument(
        "--allow-real-model", action="store_true",
        help="Explicit opt-in: keep a non-deterministic AGENT_PROVIDER from the environment (default: force deterministic)",
    )
    args = parser.parse_args()

    os.environ["APP_ENV"] = "local"
    if not args.allow_real_model:
        os.environ["AGENT_PROVIDER"] = "deterministic"
    os.environ["USE_SQLALCHEMY"] = "false"
    os.environ["USE_CELERY"] = "false"
    os.environ["CELERY_BROKER_URL"] = ""
    os.environ["CELERY_RESULT_BACKEND"] = ""
    EVAL_SANDBOX.mkdir(parents=True, exist_ok=True)
    os.environ["AGENT_WORKSPACE_ROOT"] = str(EVAL_SANDBOX)
    os.environ["MCP_SCAN_ALLOWED_ROOT"] = str(EVAL_SANDBOX)

    from app.enterprise_runtime.evaluation import (
        compare_agent_eval_reports,
        evaluate_agent_dataset,
        load_agent_eval_dataset,
        write_agent_eval_report,
    )

    provider = os.environ.get("AGENT_PROVIDER", "").strip().lower() or "unset"
    dataset, digest = load_agent_eval_dataset(args.dataset)
    report = evaluate_agent_dataset(
        dataset,
        lambda case: _run_isolated_case(case, args.allow_real_model),
        dataset_sha256=digest,
        model_version=args.model_version,
        prompt_version=args.prompt_version,
        environment={
            "agent_provider": provider,
            "workspace_root": str(EVAL_SANDBOX),
            "stores": "in_memory",
            "database": "disabled",
            "celery": "disabled",
            "real_model_opt_in": bool(args.allow_real_model),
        },
    )
    write_agent_eval_report(args.out, report)
    print(f"Agent Eval: {report['summary']['passed_cases']}/{report['summary']['total_cases']} cases passed")
    print(f"dataset_sha256={digest}")
    print(f"report={Path(args.out).resolve()}")
    if args.compare_with:
        import json

        baseline = json.loads(Path(args.compare_with).read_text(encoding="utf-8"))
        print(json.dumps(compare_agent_eval_reports(baseline, report), ensure_ascii=False, indent=2))
    return 0 if report["summary"]["failed_cases"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
