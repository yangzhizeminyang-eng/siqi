from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_cli(cwd: Path, out: Path, *extra: str) -> subprocess.CompletedProcess:
    backend_root = _backend_root()
    command = [
        sys.executable,
        str(backend_root / "scripts" / "evaluate_enterprise_agents.py"),
        "--out",
        str(out),
        *extra,
    ]
    environment = os.environ.copy()
    environment.update({
        "APP_ENV": "test",
        "AGENT_PROVIDER": "langchain",
        "USE_SQLALCHEMY": "true",
        "USE_CELERY": "true",
        "CELERY_BROKER_URL": "redis://127.0.0.1:1/0",
        "CELERY_RESULT_BACKEND": "redis://127.0.0.1:1/0",
    })
    return subprocess.run(
        command, cwd=cwd, env=environment, capture_output=True, text=True, timeout=120, check=False,
    )


def _strip_timing(node):
    if isinstance(node, dict):
        return {key: _strip_timing(value) for key, value in node.items() if key not in {"duration_ms", "latency_ms"}}
    if isinstance(node, list):
        return [_strip_timing(item) for item in node]
    return node


def test_isolated_agent_eval_cli_runs_fixed_dataset_and_writes_contract_report(tmp_path):
    report_path = tmp_path / "agent-eval-report.json"

    result = _run_cli(tmp_path, report_path)

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["total_cases"] == 2
    assert report["summary"]["passed_cases"] == 2
    assert report["summary"]["cost"] == {"status": "not_collected", "value": None}
    assert report["agent_metrics"]["requirement_agent"]["runs"] == 2
    assert report["agent_metrics"]["requirement_agent"]["success_rate"] == 1.0
    approved, rejected = report["cases"]
    assert approved["approval_boundary"]["passed"] is True
    assert rejected["approval_boundary"]["passed"] is True
    assert "code_change_agent" not in rejected["agent_types"]
    assert approved["artifact_types"]
    assert all("arguments" not in json.dumps(case["tool_trace"]) for case in report["cases"])
    environment = report["environment"]
    assert environment["agent_provider"] == "deterministic"
    assert environment["workspace_root"].endswith(str(Path(".data") / "agent-eval-sandbox"))
    assert environment["stores"] == "in_memory"
    assert environment["database"] == environment["celery"] == "disabled"
    assert environment["real_model_opt_in"] is False


def test_agent_eval_cli_is_reproducible_across_working_directories(tmp_path):
    dataset = _backend_root() / "evaluation" / "enterprise_agents" / "e4_agent_eval_v1.json"
    digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first_report = first_dir / "report.json"
    second_report = second_dir / "report.json"

    first = _run_cli(first_dir, first_report)
    second = _run_cli(second_dir, second_report)

    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    report_a = json.loads(first_report.read_text(encoding="utf-8"))
    report_b = json.loads(second_report.read_text(encoding="utf-8"))
    assert report_a["dataset"]["dataset_sha256"] == digest
    assert _strip_timing(report_a) == _strip_timing(report_b)
    assert report_a["summary"]["passed_cases"] == 2


def test_agent_eval_guard_blocks_accidental_real_model_runs():
    script = _backend_root() / "scripts" / "evaluate_enterprise_agents.py"
    spec = importlib.util.spec_from_file_location("evaluate_enterprise_agents", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module._guard_provider("deterministic", False) == "deterministic"
    try:
        module._guard_provider("langchain", False)
    except RuntimeError as error:
        assert "--allow-real-model" in str(error)
    else:  # pragma: no cover - the guard must reject non-deterministic providers
        raise AssertionError("non-deterministic provider was not rejected without opt-in")
    assert module._guard_provider("langchain", True) == "langchain"
    assert module._guard_provider("", True) == "provider_from_env"
