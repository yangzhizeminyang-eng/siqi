"""Offline, reproducible evaluation contracts for the enterprise Agent workflow."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Callable


def load_agent_eval_dataset(path: str | Path) -> tuple[dict, str]:
    raw = Path(path).read_bytes()
    try:
        dataset = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Agent Eval dataset must be valid JSON") from error
    _validate_dataset(dataset)
    return dataset, hashlib.sha256(raw).hexdigest()


def _validate_dataset(dataset: dict) -> None:
    if not isinstance(dataset, dict) or not str(dataset.get("dataset_id", "")).strip():
        raise ValueError("Agent Eval dataset requires dataset_id")
    if int(dataset.get("workflow_version", 0) or 0) < 2:
        raise ValueError("Agent Eval currently requires workflow_version >= 2")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Agent Eval dataset requires at least one case")
    ids = [str(case.get("case_id", "")).strip() for case in cases if isinstance(case, dict)]
    if len(ids) != len(cases) or any(not case_id for case_id in ids) or len(set(ids)) != len(ids):
        raise ValueError("Agent Eval case_id values must be present and unique")
    for case in cases:
        expected = case.get("expected")
        if not isinstance(expected, dict) or not expected.get("workflow_status"):
            raise ValueError(f"Agent Eval case {case['case_id']} requires expected workflow_status")


def _approval_boundary(expected: str, events: list[str], agent_types: list[str]) -> bool:
    if expected == "none":
        return not any(event in {"approved", "rejected", "code_change_started"} for event in events)
    if expected == "approved_before_code_change":
        return ("approved" in events and "code_change_started" in events
                and events.index("approved") < events.index("code_change_started"))
    if expected == "rejected_before_code_change":
        return "rejected" in events and "code_change_started" not in events and "code_change_agent" not in agent_types
    raise ValueError(f"Unknown Agent Eval approval boundary: {expected}")


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 2)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 2)


def _sanitize_tool_trace(trace: object) -> dict[str, list[dict[str, str]]]:
    if not isinstance(trace, dict):
        return {}
    sanitized = {}
    for agent_type, calls in trace.items():
        if not isinstance(calls, (list, tuple)):
            continue
        tool_names = [
            str(call.get("tool_name") or "").strip()
            for call in calls
            if isinstance(call, dict) and str(call.get("tool_name") or "").strip()
        ]
        if tool_names:
            sanitized[str(agent_type)] = [{"tool_name": name} for name in tool_names]
    return sanitized


def _tool_names_by_agent(trace: object) -> dict[str, list[str]]:
    return {
        agent_type: [call["tool_name"] for call in calls]
        for agent_type, calls in _sanitize_tool_trace(trace).items()
    }


def evaluate_agent_dataset(
    dataset: dict,
    run_case: Callable[[dict], dict],
    *,
    dataset_sha256: str,
    model_version: str,
    prompt_version: str,
    environment: dict | None = None,
) -> dict:
    _validate_dataset(dataset)
    if len(dataset_sha256) != 64:
        raise ValueError("dataset_sha256 must be a SHA-256 hex digest")

    cases = []
    latencies: list[float] = []
    agent_metrics: dict[str, dict] = {}
    for spec in dataset["cases"]:
        actual = run_case(spec)
        expected = spec["expected"]
        status = str(actual.get("workflow_status", ""))
        agent_types = list(actual.get("agent_types") or [])
        artifact_types = sorted(set(actual.get("artifact_types") or []))
        approval_events = list(actual.get("approval_events") or [])
        tool_policy = actual.get("tool_policy") or {}
        tool_trace = _sanitize_tool_trace(actual.get("tool_trace"))
        agent_runs = list(actual.get("agent_runs") or [])
        failure_reasons = []

        if status != expected.get("workflow_status"):
            failure_reasons.append("workflow_status_mismatch")
        if agent_types != list(expected.get("agent_types") or []):
            failure_reasons.append("agent_sequence_mismatch")
        if artifact_types != sorted(set(expected.get("artifact_types") or [])):
            failure_reasons.append("artifact_types_mismatch")
        boundary_passed = _approval_boundary(
            str(expected.get("approval_boundary", "none")), approval_events, agent_types
        )
        if not boundary_passed:
            failure_reasons.append("approval_boundary_violation")
        for agent_type, calls in tool_trace.items():
            allowed = set(tool_policy.get(agent_type) or [])
            if any(str(call.get("tool_name") or "") not in allowed for call in calls):
                failure_reasons.append("tool_policy_violation")
                break
        if "tool_trace" in expected and _tool_names_by_agent(expected["tool_trace"]) != _tool_names_by_agent(tool_trace):
            failure_reasons.append("tool_trace_mismatch")
        failure_class = str(actual.get("failure_class", "unknown"))
        if failure_class != str(expected.get("failure_class", "none")):
            failure_reasons.append("failure_class_mismatch")

        run_summaries = []
        for run in agent_runs:
            agent_type = str(run.get("agent_type") or "unknown")
            run_status = str(run.get("status") or "unknown")
            run_failure_class = str(run.get("failure_class") or ("none" if run_status == "succeeded" else "unknown"))
            run_trace = _sanitize_tool_trace({agent_type: run.get("tool_trace") or []}).get(agent_type, [])
            metric = agent_metrics.setdefault(agent_type, {
                "runs": 0, "succeeded": 0, "failed": 0, "latencies": [],
                "tool_calls": 0, "failure_classes": {},
            })
            metric["runs"] += 1
            if run_status == "succeeded":
                metric["succeeded"] += 1
            elif run_status in {"failed", "blocked"}:
                metric["failed"] += 1
            duration = run.get("duration_ms")
            run_duration = None
            if isinstance(duration, (int, float)) and math.isfinite(float(duration)) and duration >= 0:
                metric["latencies"].append(float(duration))
                run_duration = round(float(duration), 2)
            metric["tool_calls"] += len(run_trace)
            failure_classes = metric["failure_classes"]
            failure_classes[run_failure_class] = failure_classes.get(run_failure_class, 0) + 1
            run_summaries.append({
                "agent_type": agent_type,
                "status": run_status,
                "failure_class": run_failure_class,
                "duration_ms": run_duration,
                "tool_call_count": len(run_trace),
            })

        duration = actual.get("duration_ms")
        if isinstance(duration, (int, float)) and math.isfinite(float(duration)) and duration >= 0:
            latencies.append(float(duration))
        cases.append({
            "case_id": spec["case_id"],
            "passed": not failure_reasons,
            "workflow_status": status,
            "agent_types": agent_types,
            "artifact_types": artifact_types,
            "tool_policy": tool_policy,
            "tool_trace": tool_trace,
            "agent_runs": run_summaries,
            "approval_events": approval_events,
            "approval_boundary": {"expected": expected.get("approval_boundary", "none"), "passed": boundary_passed},
            "failure_class": failure_class,
            "failure_reasons": sorted(set(failure_reasons)),
            "duration_ms": round(float(duration), 2) if duration is not None else None,
            "cost": {"status": "not_collected", "value": None},
        })

    passed = sum(1 for case in cases if case["passed"])
    total = len(cases)
    summarized_agent_metrics = {}
    for agent_type, metric in sorted(agent_metrics.items()):
        run_count = metric["runs"]
        summarized_agent_metrics[agent_type] = {
            "runs": run_count,
            "succeeded": metric["succeeded"],
            "failed": metric["failed"],
            "success_rate": round(metric["succeeded"] / run_count, 4),
            "latency_ms": {
                "p50": _percentile(metric["latencies"], 0.50),
                "p95": _percentile(metric["latencies"], 0.95),
            },
            "tool_calls": metric["tool_calls"],
            "failure_classes": dict(sorted(metric["failure_classes"].items())),
        }
    return {
        "schema_version": 1,
        "dataset": {
            "dataset_id": dataset["dataset_id"],
            "dataset_sha256": dataset_sha256,
            "case_count": total,
            "workflow_version": int(dataset["workflow_version"]),
        },
        "versions": {"model": model_version, "prompt": prompt_version},
        "environment": dict(environment or {}),
        "agent_metrics": summarized_agent_metrics,
        "summary": {
            "total_cases": total,
            "passed_cases": passed,
            "failed_cases": total - passed,
            "success_rate": round(passed / total, 4),
            "latency_ms": {"p50": _percentile(latencies, 0.50), "p95": _percentile(latencies, 0.95)},
            "cost": {"status": "not_collected", "value": None},
        },
        "cases": cases,
    }


def compare_agent_eval_reports(baseline: dict, candidate: dict) -> dict:
    base_dataset = baseline.get("dataset") or {}
    next_dataset = candidate.get("dataset") or {}
    if base_dataset.get("dataset_sha256") != next_dataset.get("dataset_sha256"):
        raise ValueError("Agent Eval reports must use the same dataset")
    base_cases = {case["case_id"]: case for case in baseline.get("cases") or []}
    next_cases = {case["case_id"]: case for case in candidate.get("cases") or []}
    if not base_cases or base_cases.keys() != next_cases.keys():
        raise ValueError("Agent Eval reports must contain the same cases")
    regressions = [
        {"case_id": case_id, "baseline_passed": base_cases[case_id]["passed"], "candidate_passed": False,
         "failure_reasons": next_cases[case_id].get("failure_reasons", [])}
        for case_id in sorted(base_cases)
        if base_cases[case_id]["passed"] and not next_cases[case_id]["passed"]
    ]
    improvements = [
        {"case_id": case_id, "baseline_passed": False, "candidate_passed": next_cases[case_id]["passed"]}
        for case_id in sorted(base_cases)
        if not base_cases[case_id]["passed"] and next_cases[case_id]["passed"]
    ]
    base_summary = baseline.get("summary") or {}
    next_summary = candidate.get("summary") or {}
    base_p50 = (base_summary.get("latency_ms") or {}).get("p50")
    next_p50 = (next_summary.get("latency_ms") or {}).get("p50")
    base_agents = baseline.get("agent_metrics") or {}
    next_agents = candidate.get("agent_metrics") or {}
    agent_deltas = {}
    agent_regressions = []
    agent_improvements = []
    for agent_type in sorted(base_agents.keys() | next_agents.keys()):
        base_agent = base_agents.get(agent_type) or {}
        next_agent = next_agents.get(agent_type) or {}
        base_agent_p50 = (base_agent.get("latency_ms") or {}).get("p50")
        next_agent_p50 = (next_agent.get("latency_ms") or {}).get("p50")
        base_failure_classes = base_agent.get("failure_classes") or {}
        next_failure_classes = next_agent.get("failure_classes") or {}
        agent_deltas[agent_type] = {
            "runs_delta": int(next_agent.get("runs", 0)) - int(base_agent.get("runs", 0)),
            "success_rate_delta": round(
                float(next_agent.get("success_rate", 0)) - float(base_agent.get("success_rate", 0)), 4
            ),
            "latency_p50_delta_ms": round(float(next_agent_p50) - float(base_agent_p50), 2)
            if base_agent_p50 is not None and next_agent_p50 is not None else None,
            "tool_calls_delta": int(next_agent.get("tool_calls", 0)) - int(base_agent.get("tool_calls", 0)),
            "failure_classes_delta": {
                failure_class: int(next_failure_classes.get(failure_class, 0)) - int(base_failure_classes.get(failure_class, 0))
                for failure_class in sorted(base_failure_classes.keys() | next_failure_classes.keys())
            },
        }
        success_rate_delta = agent_deltas[agent_type]["success_rate_delta"]
        if success_rate_delta < 0:
            agent_regressions.append({
                "agent_type": agent_type,
                "baseline_success_rate": float(base_agent.get("success_rate", 0)),
                "candidate_success_rate": float(next_agent.get("success_rate", 0)),
                "success_rate_delta": success_rate_delta,
            })
        elif success_rate_delta > 0:
            agent_improvements.append({
                "agent_type": agent_type,
                "baseline_success_rate": float(base_agent.get("success_rate", 0)),
                "candidate_success_rate": float(next_agent.get("success_rate", 0)),
                "success_rate_delta": success_rate_delta,
            })
    return {
        "dataset_sha256": base_dataset.get("dataset_sha256"),
        "baseline_versions": baseline.get("versions"),
        "candidate_versions": candidate.get("versions"),
        "regression_count": len(regressions),
        "regressions": regressions,
        "improvement_count": len(improvements),
        "improvements": improvements,
        "agent_regressions": agent_regressions,
        "agent_improvements": agent_improvements,
        "success_rate_delta": round(
            float(next_summary.get("success_rate", 0)) - float(base_summary.get("success_rate", 0)), 4
        ),
        "latency_p50_delta_ms": round(float(next_p50) - float(base_p50), 2)
        if base_p50 is not None and next_p50 is not None else None,
        "agent_deltas": agent_deltas,
    }


def write_agent_eval_report(path: str | Path, report: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
