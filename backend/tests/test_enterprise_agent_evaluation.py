from __future__ import annotations

import json

import pytest
from types import SimpleNamespace

from app.enterprise_runtime.evaluation import (
    compare_agent_eval_reports,
    evaluate_agent_dataset,
)


def _dataset():
    return {
        "dataset_id": "enterprise-agent-eval-v1",
        "workflow_version": 2,
        "cases": [
            {
                "case_id": "approved-happy-path",
                "expected": {
                    "workflow_status": "succeeded",
                    "agent_types": ["planner_agent", "code_change_agent"],
                    "artifact_types": ["PlanArtifact", "DiffArtifact"],
                    "tool_trace": {
                        "planner_agent": [],
                        "code_change_agent": [{"tool_name": "workspace_write"}],
                    },
                    "approval_boundary": "approved_before_code_change",
                    "failure_class": "none",
                },
            }
        ],
    }


def _execution(**overrides):
    values = {
        "workflow_status": "succeeded",
        "agent_types": ["planner_agent", "code_change_agent"],
        "artifact_types": ["PlanArtifact", "DiffArtifact"],
        "approval_events": ["requested", "approved", "code_change_started"],
        "tool_policy": {
            "planner_agent": [],
            "code_change_agent": ["workspace_read", "workspace_write", "build_diff"],
        },
        "tool_trace": {
            "planner_agent": [],
            "code_change_agent": [{"tool_name": "workspace_write"}],
        },
        "failure_class": "none",
        "duration_ms": 12.5,
        "cost": None,
        "agent_runs": [
            {"agent_type": "planner_agent", "status": "succeeded", "duration_ms": 7.5,
             "failure_class": "none", "tool_trace": []},
            {"agent_type": "code_change_agent", "status": "failed", "duration_ms": 8.5,
             "failure_class": "agent_failed", "tool_trace": [{"tool_name": "workspace_write"}]},
        ],
    }
    values.update(overrides)
    return values


def test_evaluation_records_contract_coverage_and_marks_cost_unavailable():
    report = evaluate_agent_dataset(
        _dataset(), lambda _case: _execution(),
        dataset_sha256="a" * 64,
        model_version="deterministic-v1",
        prompt_version="prompts-2026-10-15",
    )

    assert report["summary"]["passed_cases"] == 1
    assert report["summary"]["success_rate"] == 1.0
    assert report["summary"]["latency_ms"]["p50"] == 12.5
    assert report["summary"]["cost"]["status"] == "not_collected"
    case = report["cases"][0]
    assert case["tool_trace"]["code_change_agent"] == [{"tool_name": "workspace_write"}]
    assert case["approval_boundary"]["passed"] is True
    assert report["agent_metrics"]["planner_agent"] == {
        "runs": 1,
        "succeeded": 1,
        "failed": 0,
        "success_rate": 1.0,
        "latency_ms": {"p50": 7.5, "p95": 7.5},
        "tool_calls": 0,
        "failure_classes": {"none": 1},
    }
    assert report["agent_metrics"]["code_change_agent"]["success_rate"] == 0.0
    assert report["agent_metrics"]["code_change_agent"]["tool_calls"] == 1
    assert report["agent_metrics"]["code_change_agent"]["failure_classes"] == {"agent_failed": 1}
    assert report["versions"] == {
        "model": "deterministic-v1",
        "prompt": "prompts-2026-10-15",
    }


def test_evaluation_fails_for_tool_outside_agent_policy():
    report = evaluate_agent_dataset(
        _dataset(),
        lambda _case: _execution(tool_trace={"planner_agent": [{"tool_name": "workspace_write"}]}),
        dataset_sha256="a" * 64,
        model_version="deterministic-v1",
        prompt_version="p1",
    )

    assert report["summary"]["passed_cases"] == 0
    assert "tool_policy_violation" in report["cases"][0]["failure_reasons"]


def test_evaluation_fails_when_code_change_precedes_approval():
    report = evaluate_agent_dataset(
        _dataset(),
        lambda _case: _execution(approval_events=["requested", "code_change_started", "approved"]),
        dataset_sha256="a" * 64,
        model_version="deterministic-v1",
        prompt_version="p1",
    )

    assert report["summary"]["passed_cases"] == 0
    assert report["cases"][0]["approval_boundary"]["passed"] is False


def test_evaluation_fails_when_tool_trace_differs_from_expected():
    report = evaluate_agent_dataset(
        _dataset(),
        lambda _case: _execution(tool_trace={"code_change_agent": [{"tool_name": "workspace_read"}]}),
        dataset_sha256="a" * 64,
        model_version="deterministic-v1",
        prompt_version="p1",
    )

    assert report["summary"]["passed_cases"] == 0
    assert "tool_trace_mismatch" in report["cases"][0]["failure_reasons"]


def test_evaluation_report_strips_tool_arguments():
    report = evaluate_agent_dataset(
        _dataset(),
        lambda _case: _execution(tool_trace={
            "planner_agent": [],
            "code_change_agent": [{
                "tool_name": "workspace_write",
                "arguments": {"content": "private-issue-secret"},
            }],
        }),
        dataset_sha256="a" * 64,
        model_version="deterministic-v1",
        prompt_version="p1",
    )

    assert report["summary"]["passed_cases"] == 1
    assert report["cases"][0]["tool_trace"]["code_change_agent"] == [
        {"tool_name": "workspace_write"},
    ]
    assert "private-issue-secret" not in json.dumps(report)


def test_agent_metrics_ignore_non_finite_agent_latency():
    report = evaluate_agent_dataset(
        _dataset(),
        lambda _case: _execution(agent_runs=[{
            "agent_type": "planner_agent",
            "status": "succeeded",
            "duration_ms": float("inf"),
            "failure_class": "none",
            "tool_trace": [],
        }]),
        dataset_sha256="a" * 64,
        model_version="deterministic-v1",
        prompt_version="p1",
    )

    assert report["agent_metrics"]["planner_agent"]["latency_ms"] == {"p50": None, "p95": None}
    assert report["cases"][0]["agent_runs"][0]["duration_ms"] is None


def test_evaluation_report_records_environment_and_defaults_empty():
    report = evaluate_agent_dataset(
        _dataset(), lambda _case: _execution(),
        dataset_sha256="a" * 64,
        model_version="deterministic-v1",
        prompt_version="p1",
        environment={"agent_provider": "deterministic", "database": "disabled"},
    )
    default_report = evaluate_agent_dataset(
        _dataset(), lambda _case: _execution(),
        dataset_sha256="a" * 64,
        model_version="deterministic-v1",
        prompt_version="p1",
    )

    assert report["environment"] == {"agent_provider": "deterministic", "database": "disabled"}
    assert default_report["environment"] == {}


def test_report_comparison_requires_same_dataset_and_cases():
    base = evaluate_agent_dataset(
        _dataset(), lambda _case: _execution(),
        dataset_sha256="a" * 64,
        model_version="model-a",
        prompt_version="p1",
    )
    candidate = evaluate_agent_dataset(
        _dataset(), lambda _case: _execution(
            workflow_status="failed",
            failure_class="agent_failed",
            agent_runs=[
                {"agent_type": "planner_agent", "status": "failed", "duration_ms": 9.5,
                 "failure_class": "agent_failed", "tool_trace": []},
                {"agent_type": "code_change_agent", "status": "failed", "duration_ms": 8.5,
                 "failure_class": "agent_failed", "tool_trace": [{"tool_name": "workspace_write"}]},
            ],
        ),
        dataset_sha256="a" * 64,
        model_version="model-b",
        prompt_version="p2",
    )

    comparison = compare_agent_eval_reports(base, candidate)

    assert comparison["regression_count"] == 1
    assert comparison["success_rate_delta"] == -1.0
    assert comparison["regressions"][0]["case_id"] == "approved-happy-path"
    assert comparison["agent_deltas"]["planner_agent"]["success_rate_delta"] == -1.0
    assert comparison["agent_deltas"]["planner_agent"]["tool_calls_delta"] == 0
    assert comparison["improvement_count"] == 0


def test_report_comparison_detects_agent_regression_even_when_case_passes():
    dataset = _dataset()
    baseline = evaluate_agent_dataset(
        dataset, lambda _case: _execution(),
        dataset_sha256="a" * 64, model_version="model-a", prompt_version="p1",
    )
    candidate = evaluate_agent_dataset(
        dataset,
        lambda _case: _execution(agent_runs=[
            {"agent_type": "planner_agent", "status": "failed", "failure_class": "agent_failed",
             "duration_ms": 5.0, "tool_trace": []},
            {"agent_type": "planner_agent", "status": "succeeded", "failure_class": "none",
             "duration_ms": 7.5, "tool_trace": []},
            {"agent_type": "code_change_agent", "status": "failed", "failure_class": "agent_failed",
             "duration_ms": 8.5, "tool_trace": [{"tool_name": "workspace_write"}]},
        ]),
        dataset_sha256="a" * 64, model_version="model-b", prompt_version="p2",
    )

    comparison = compare_agent_eval_reports(baseline, candidate)

    assert candidate["summary"]["success_rate"] == 1.0
    assert comparison["regression_count"] == 0
    assert comparison["agent_regressions"] == [{
        "agent_type": "planner_agent",
        "baseline_success_rate": 1.0,
        "candidate_success_rate": 0.5,
        "success_rate_delta": -0.5,
    }]


def test_report_comparison_lists_case_improvements():
    dataset = _dataset()
    baseline = evaluate_agent_dataset(
        dataset,
        lambda _case: _execution(approval_events=["requested", "code_change_started", "approved"]),
        dataset_sha256="a" * 64, model_version="model-a", prompt_version="p1",
    )
    candidate = evaluate_agent_dataset(
        dataset, lambda _case: _execution(),
        dataset_sha256="a" * 64, model_version="model-b", prompt_version="p2",
    )

    comparison = compare_agent_eval_reports(baseline, candidate)

    assert comparison["improvement_count"] == 1
    assert comparison["improvements"][0]["case_id"] == "approved-happy-path"


def test_report_comparison_rejects_different_dataset_hashes():
    base = evaluate_agent_dataset(
        _dataset(), lambda _case: _execution(),
        dataset_sha256="a" * 64,
        model_version="model-a",
        prompt_version="p1",
    )
    candidate = evaluate_agent_dataset(
        _dataset(), lambda _case: _execution(),
        dataset_sha256="b" * 64,
        model_version="model-b",
        prompt_version="p2",
    )

    with pytest.raises(ValueError, match="same dataset"):
        compare_agent_eval_reports(base, candidate)


def test_agent_executor_persists_observed_tool_names_without_arguments(monkeypatch):
    from app.enterprise_runtime.agents import executor as agent_executor
    from app.enterprise_runtime.artifacts import runs as run_store
    from app.enterprise_runtime.artifacts import store as artifact_store

    run_store.reset_store()
    artifact_store.reset_store()
    monkeypatch.setenv("AGENT_PROVIDER", "deterministic")

    class Backend:
        def invoke_with_trace(self, _structured_input):
            return ({
                "summary": "local issue",
                "requirements": [],
                "open_questions": [],
                "completeness": "complete",
            }, [{"tool_name": "read_issue"}, {"tool_name": "read_comments"}])

    monkeypatch.setattr(agent_executor, "build_agent", lambda _agent_type: Backend())
    task = SimpleNamespace(
        id="eval-trace", tenant_id="tenant-eval", title="Local test issue",
        repository_id="", project_id="",
    )
    step = SimpleNamespace(
        agent_attempt=0, repair_round=0, ready_refs=[], step_id="eval-trace:requirement_agent",
    )

    result = agent_executor.AgentExecutor("requirement_agent").run(
        ctx=SimpleNamespace(user_id="eval-user", role="reviewer"), task=task, step=step,
    )

    run = run_store.get(result["run_id"])
    assert run.usage["tool_trace"] == [
        {"tool_name": "read_issue"}, {"tool_name": "read_comments"},
    ]
    assert "arguments" not in str(run.usage)


def test_agent_executor_preserves_trace_and_latency_when_output_validation_fails(monkeypatch):
    from app.enterprise_runtime.agents import executor as agent_executor
    from app.enterprise_runtime.artifacts import runs as run_store
    from app.enterprise_runtime.artifacts import store as artifact_store

    run_store.reset_store()
    artifact_store.reset_store()
    monkeypatch.setenv("AGENT_PROVIDER", "deterministic")

    class Backend:
        def invoke_with_trace(self, _structured_input):
            return ({"requirements": "not-a-list"}, [{"tool_name": "read_issue"}])

    monkeypatch.setattr(agent_executor, "build_agent", lambda _agent_type: Backend())
    task = SimpleNamespace(
        id="eval-failed-trace", tenant_id="tenant-eval", title="Local test issue",
        repository_id="", project_id="",
    )
    step = SimpleNamespace(
        agent_attempt=0, repair_round=0, ready_refs=[], step_id="eval-failed-trace:requirement_agent",
    )

    result = agent_executor.AgentExecutor("requirement_agent").run(
        ctx=SimpleNamespace(user_id="eval-user", role="reviewer"), task=task, step=step,
    )

    run = run_store.get(result["run_id"])
    assert run.status == "failed"
    assert run.usage["tool_trace"] == [{"tool_name": "read_issue"}]
    assert isinstance(run.usage["duration_ms"], (int, float))
    assert run.usage["duration_ms"] >= 0
    assert run.usage["cost"] == {"status": "not_collected", "value": None}


def test_agent_executor_records_latency_when_backend_raises(monkeypatch):
    from app.enterprise_runtime.agents import executor as agent_executor
    from app.enterprise_runtime.artifacts import runs as run_store
    from app.enterprise_runtime.artifacts import store as artifact_store

    run_store.reset_store()
    artifact_store.reset_store()
    monkeypatch.setenv("AGENT_PROVIDER", "deterministic")

    class Backend:
        def invoke_with_trace(self, _structured_input):
            raise RuntimeError("isolated backend failure")

    monkeypatch.setattr(agent_executor, "build_agent", lambda _agent_type: Backend())
    task = SimpleNamespace(
        id="eval-backend-error", tenant_id="tenant-eval", title="Local test issue",
        repository_id="", project_id="",
    )
    step = SimpleNamespace(
        agent_attempt=0, repair_round=0, ready_refs=[], step_id="eval-backend-error:requirement_agent",
    )

    result = agent_executor.AgentExecutor("requirement_agent").run(
        ctx=SimpleNamespace(user_id="eval-user", role="reviewer"), task=task, step=step,
    )

    run = run_store.get(result["run_id"])
    assert run.status == "failed"
    assert run.usage["tool_trace"] == []
    assert isinstance(run.usage["duration_ms"], (int, float))
    assert run.usage["duration_ms"] >= 0
