"""OPT-03 可复现多 Agent 端到端演示：审计回放完整性 + 隔离 CLI 验收。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_audit_replay_keeps_each_agent_event(monkeypatch):
    """审计回放必须保留每个 Agent 的独立事件，不能被同一秒内的 ID 冲突覆盖。"""
    monkeypatch.setenv("AGENT_PROVIDER", "deterministic")
    from types import SimpleNamespace

    from app import main
    from app.enterprise_runtime import orchestrator as orch
    from app.enterprise_runtime import store as rstore
    from app.enterprise_runtime.agents.registry import AgentExecutorRegistry
    from app.enterprise_runtime.models import RuntimeTaskStatus

    task_id = "opt03_audit_probe"
    ctx = SimpleNamespace(tenant_id="tenant_opt03", user_id="u_opt03", user_name="opt03", role="admin")
    orch.start_runtime_task(ctx, task_id, f"ik_{task_id}", title="审计回放探针", workflow_version=2)
    registry = AgentExecutorRegistry(audit=orch.agent_audit_cb())
    for _ in range(40):
        task = orch.run_agent_workflow("w_opt03", task_id, registry, ctx=ctx)
        if task.status == RuntimeTaskStatus.WAITING_APPROVAL:
            orch.approve_runtime_task(task_id, True, actor="u_opt03", actor_name="opt03")
            continue
        if task.status in (RuntimeTaskStatus.SUCCEEDED, RuntimeTaskStatus.FAILED, RuntimeTaskStatus.WAITING_HUMAN):
            break
    assert rstore.get(task_id).status == RuntimeTaskStatus.SUCCEEDED

    agent_events = [e for e in main.AUDIT_EVENTS.values()
                    if e.event_type == "runtime.agent.succeeded" and e.resource_id == task_id]
    assert len(agent_events) == 6, (
        f"6 个 Agent 应产生 6 条独立审计事件，实际 {len(agent_events)}（ID 冲突会覆盖）")


def _run_demo(cwd: Path, out: Path) -> subprocess.CompletedProcess:
    backend_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    # 敌对环境：真实 provider + DB/Redis/Celery + Gitee/LLM 凭据全部在场，
    # 演示必须自行强制隔离，不能被环境带跑。
    env.update({
        "APP_ENV": "local",
        "AGENT_PROVIDER": "langchain",
        "USE_SQLALCHEMY": "true",
        "DATABASE_URL": "mysql+pymysql://root:hostile@127.0.0.1:1/none",
        "USE_CELERY": "true",
        "CELERY_BROKER_URL": "redis://127.0.0.1:1/0",
        "CELERY_RESULT_BACKEND": "redis://127.0.0.1:1/0",
        "GITEE_TOKEN": "hostile-token",
        "ALLOW_SCRATCH_REAL_CHANGE": "true",
        "E2E_REAL_CHANGE_REPO_IDS": "repo_demo_payment",
        "E4_PR_PROVIDER": "gitee",
        "LLM_API_KEY": "hostile-llm-key",
        "PYTHONIOENCODING": "utf-8",
    })
    return subprocess.run(
        [sys.executable, str(backend_root / "scripts" / "opt03_agent_e2e_demo.py"), "--out", str(out)],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=180, check=False,
    )


_STABLE_DROP_KEYS = {
    "generated_at", "run_id", "approval_id", "created_at", "approved_at", "started_at",
    "finished_at", "duration_ms", "output_refs", "idempotency_key",
}


def _stable_projection(value):
    """去掉时间/随机 ID/耗时后用于跨 cwd 复跑对拍。"""
    if isinstance(value, dict):
        return {k: _stable_projection(v) for k, v in sorted(value.items()) if k not in _STABLE_DROP_KEYS}
    if isinstance(value, list):
        return [_stable_projection(v) for v in value]
    return value


def _load_evidence(out: Path) -> dict:
    return json.loads(out.read_text(encoding="utf-8"))


def test_artifact_projection_order_is_process_stable():
    """产物投影必须是「有序声明」：set 的跨进程迭代顺序不稳定（字符串 hash 随机化 +
    lambda id 地址漂移），会打乱产物写入与审计回放顺序，破坏演示可复现性。"""
    from app.enterprise_runtime.agents.executor import _PROJECTION

    for agent_type, projections in _PROJECTION.items():
        assert isinstance(projections, tuple), f"{agent_type} 投影容器必须是元组（有序）"
    assert [t for t, _ in _PROJECTION["repository_research_agent"]] == [
        "ResearchArtifact", "CodeContextArtifact"]
    assert [t for t, _ in _PROJECTION["code_change_agent"]] == ["PatchArtifact", "DiffArtifact"]


def test_opt03_demo_cli_isolated_runs_and_all_checks_pass(tmp_path):
    out = tmp_path / "opt03_evidence.json"
    result = _run_demo(tmp_path, out)
    assert result.returncode == 0, result.stdout + result.stderr
    report = _load_evidence(out)

    env = report["environment"]
    assert env["agent_provider"] == "deterministic"
    assert env["stores"] == "in_memory"
    assert env["database"] == "disabled"
    assert env["celery"] == "disabled"
    assert env["remote_write_module_loaded"] is False

    assert report["checks"]["all_passed"] is True, report["checks"]

    main = report["scenario_main"]
    assert main["final_status"] == "succeeded"
    assert len(main["agent_runs"]) == 6
    assert [r["agent_type"] for r in main["agent_runs"]] == [
        "requirement_agent", "repository_research_agent", "planner_agent",
        "code_change_agent", "review_security_agent", "test_release_agent",
    ]
    assert main["dangling_refs"] == []
    assert main["approval"]["status"] == "approved"
    assert main["approval"]["code_change_started_after_approval"] is True

    rag = report["rag"]
    assert rag["hit_count"] >= 1
    assert "retry_refund" in {hit["symbol"] for hit in rag["hits"]}

    events = [e["event_type"] for e in main["audit_replay"]]
    assert events.count("runtime.agent.succeeded") == 6
    assert "runtime.task.started" in events
    assert "runtime.approval.decided" in events
    assert "runtime.task.succeeded" in events

    repair = report["scenario_repair"]
    assert repair["final_status"] == "succeeded"
    assert repair["repair_round"] >= 1
    assert repair["planner_run_count"] == 1


def test_opt03_demo_reproducible_across_cwd(tmp_path):
    out_a = tmp_path / "a" / "evidence.json"
    out_b = tmp_path / "b" / "evidence.json"
    out_a.parent.mkdir()
    out_b.parent.mkdir()
    cwd_a = tmp_path / "run_a"
    cwd_b = tmp_path / "run_b"
    cwd_a.mkdir()
    cwd_b.mkdir()

    first = _run_demo(cwd_a, out_a)
    second = _run_demo(cwd_b, out_b)
    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr

    assert _stable_projection(_load_evidence(out_a)) == _stable_projection(_load_evidence(out_b))
