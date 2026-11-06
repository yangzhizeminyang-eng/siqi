"""OPT-03 可复现多 Agent 端到端演示（隔离启动：deterministic + 内存态 + 无外部写入）。

链路：Issue → Requirement → Research（本地内存 RAG 检索）→ Planner → E2 人工审批门
      → CodeChange（受控变更产物）→ Review → TestRelease（PR 产物，不写远端）→ 审计回放。
另含修复分支场景：Review 首轮不通过 → 回退 CodeChange（repair_round+1）→ 复审通过 → 成功。

默认强制隔离（脚本自身设定，不依赖外部环境）：
- AGENT_PROVIDER=deterministic（仅 --allow-real-model 显式 opt-in 才保留环境 provider）
- USE_SQLALCHEMY=false / USE_CELERY=false / CELERY_* 清空（不连 DB/Redis/Celery）
- DATA_DIR/工作区固定在 backend/.data/opt03-demo（任意 cwd 可复跑）
- 不调用 enterprise_release_service：无 Gitee/GitHub 远端写入，结束校验该模块未被加载

用法（任意 cwd）：
  .venv/Scripts/python.exe -m scripts.opt03_agent_e2e_demo --out evidence.json

退出码：0=全部检查通过；2=存在失败检查（如实报告，不伪造成功）。
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

DEMO_ROOT = BACKEND_ROOT / ".data" / "opt03-demo"
DEMO_WORKSPACE = DEMO_ROOT / "workspace"

TENANT = "tenant_default"
PROJECT_ID = "proj_demo_payment"
REPOSITORY_ID = "repo_demo_payment"
ISSUE = "为退款回调补充可复现的重试说明与校验（OPT-03 隔离演示）"
RAG_QUERY = "retry_refund 回调重试"

REMOTE_WRITE_MODULE = "app.services.enterprise_release_service"


def _force_isolated_env(allow_real_model: bool) -> None:
    os.environ["APP_ENV"] = "local"
    if not allow_real_model:
        os.environ["AGENT_PROVIDER"] = "deterministic"
    os.environ["USE_SQLALCHEMY"] = "false"
    os.environ["USE_CELERY"] = "false"
    os.environ["CELERY_BROKER_URL"] = ""
    os.environ["CELERY_RESULT_BACKEND"] = ""
    os.environ["DATA_DIR"] = str(DEMO_ROOT)
    DEMO_WORKSPACE.mkdir(parents=True, exist_ok=True)
    os.environ["AGENT_WORKSPACE_ROOT"] = str(DEMO_WORKSPACE)
    os.environ["MCP_SCAN_ALLOWED_ROOT"] = str(DEMO_WORKSPACE)


def _ctx(tenant_id: str = TENANT, role: str = "tenant_owner",
         user_id: str = "user_demo", user_name: str = "演示操作者"):
    return SimpleNamespace(tenant_id=tenant_id, role=role, user_id=user_id, user_name=user_name)


def _rag_leg() -> dict:
    """Research 环节的本地内存 RAG 检索（真实 BM25，进程内种子语料，无 Milvus/无外网）。"""
    from app.enterprise_rag.models import EnterpriseRagSearchPayload
    from app.enterprise_rag.service import enterprise_rag_search
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
    result = enterprise_rag_search(ctx, payload)
    hits = [
        {"file": item.get("file_path", ""), "symbol": item.get("symbol_name", ""),
         "score": item.get("score")}
        for item in (result.get("results") or [])
    ]
    return {"query": RAG_QUERY, "mode": "bm25", "commit_sha": BASELINE_COMMIT,
            "hit_count": len(hits), "hits": hits}


def _approve_via_e2(task_id: str, tenant_id: str) -> dict:
    """经企业 E2 审批服务批准 Runtime（真实审批链路，不直接改 RuntimeTask）。"""
    from app.approvals import service as approval_service
    from app.approvals import store as approval_store
    from app.approvals.models import ApprovalApprovePayload

    pending = next(
        (r for r in approval_store.APPROVALS.values()
         if r.tenant_id == tenant_id and r.resource_type == "runtime_task"
         and r.resource_id == task_id and r.approval_status == "pending"),
        None,
    )
    if pending is None:
        raise RuntimeError(f"未找到待审批的 Runtime 审批单: {task_id}")
    approver_id, approver_name = "user_approver", "审批人"
    approved = approval_service.approve(
        _ctx(tenant_id, role="tenant_owner", user_id=approver_id, user_name=approver_name),
        SimpleNamespace(id=approver_id, name=approver_name),
        pending.id,
        ApprovalApprovePayload(comment="OPT-03 演示审批通过"),
    )
    return {"approval_id": approved.id, "status": approved.approval_status,
            "approved_at": approved.approved_at.isoformat() if approved.approved_at else None}


def _run_order(run):
    """跨进程稳定排序：Store 的产物集合顺序不稳定，时间戳同刻时用元组兜底。"""
    return (run.started_at, run.agent_type, run.attempt, run.repair_round)


def _collect_scenario(task_id: str, tenant_id: str, *, live: dict) -> dict:
    from app import main
    from app.enterprise_runtime import store as rstore
    from app.enterprise_runtime.artifacts import runs as runs_store
    from app.enterprise_runtime.artifacts import store as astore

    task = rstore.get(task_id)
    runs = sorted(runs_store.list_by_task(tenant_id, task_id), key=_run_order)
    artifacts = sorted(astore.list_by_task(tenant_id, task_id), key=lambda a: a.artifact_id)
    ref_ids = {a.artifact_id for a in artifacts}
    dangling = sorted({ref for r in runs for ref in r.input_refs if ref not in ref_ids})
    audit_replay = sorted(
        (e for e in main.AUDIT_EVENTS.values() if e.resource_id == task_id),
        key=lambda e: e.created_at,
    )
    approval = dict(live.get("approval") or {})
    cc_runs = sorted((r for r in runs if r.agent_type == "code_change_agent"), key=_run_order)
    # 同刻 Windows 时钟粒度可能让 approve 与下一步启动落在同一微秒：允许相等，
    # 严格顺序由审批门现场的 approval_gate_held（cc 必须仍为 PENDING）保证。
    approval["code_change_started_after_approval"] = bool(
        approval.get("approved_at") and cc_runs
        and cc_runs[0].started_at.isoformat() >= approval["approved_at"]
    )
    return {
        "task_id": task_id,
        "final_status": task.status.value,
        "repair_round": task.repair_round,
        "approval_state": task.approval_state,
        "steps": [
            {"name": s.name, "agent_type": s.agent_type, "status": s.status.value,
             "attempt": s.agent_attempt, "output_summary": s.output_summary}
            for s in task.steps
        ],
        "agent_runs": [
            {"agent_type": r.agent_type, "attempt": r.attempt, "repair_round": r.repair_round,
             "status": r.status, "run_id": r.run_id, "input_refs": sorted(r.input_refs),
             "output_refs": sorted(r.output_refs)}
            for r in runs
        ],
        "artifacts": [
            {"artifact_id": a.artifact_id, "artifact_type": a.artifact_type.value,
             "agent_type": a.agent_type, "input_refs": sorted(a.input_refs)}
            for a in artifacts
        ],
        "dangling_refs": dangling,
        "approval": approval,
        "audit_replay": [
            {"event_type": e.event_type, "actor_id": e.actor_id, "actor_name": e.actor_name,
             "summary": e.summary, "created_at": e.created_at.isoformat()}
            for e in audit_replay
        ],
    }


def _drive(task_id: str, registry, *, tenant_id: str = TENANT) -> dict:
    """驱动 6-Agent 工作流：Planner 后经 E2 审批门，直到终态。"""
    from app.enterprise_runtime import orchestrator as orch
    from app.enterprise_runtime.models import RuntimeStepStatus, RuntimeTaskStatus

    live: dict = {"approval": None, "checks": {}}
    ctx = _ctx(tenant_id)
    max_iters = 60
    for i in range(max_iters):
        task = orch.run_agent_workflow("demo-worker", task_id, registry, ctx=ctx)
        if task.status == RuntimeTaskStatus.WAITING_APPROVAL:
            cc = next(s for s in task.steps if s.agent_type == "code_change_agent")
            live["checks"]["approval_gate_held"] = (
                cc.status == RuntimeStepStatus.PENDING
                and "code_change_agent" not in task.completed_steps
            )
            live["approval"] = _approve_via_e2(task_id, tenant_id)
            assert task.approval_state == "approved"
            continue
        if task.status in (RuntimeTaskStatus.SUCCEEDED, RuntimeTaskStatus.FAILED,
                           RuntimeTaskStatus.WAITING_HUMAN, RuntimeTaskStatus.CANCELLED):
            break
    else:
        raise RuntimeError(f"演示任务未达终态（>{max_iters} 轮驱动）: {task_id}")
    return live


def _install_review_fail_once() -> None:
    """演示修复分支：Review 首轮不通过，触发回退 CodeChange；后续恢复通过。"""
    from app.enterprise_runtime.agents import factory

    original = factory.DeterministicAgent.invoke
    state = {"review_calls": 0}

    def patched(self, structured_input):
        output = original(self, structured_input)
        if self.agent_type == "review_security_agent":
            output["approved"] = not (state["review_calls"] < 1)
            state["review_calls"] += 1
        return output

    factory.DeterministicAgent.invoke = patched  # type: ignore[method-assign]


def _run_scenarios(allow_real_model: bool = False) -> dict:
    from app.approvals import store as approval_store
    from app.enterprise_runtime import orchestrator as orch
    from app.enterprise_runtime import store as rstore
    from app.enterprise_runtime.agents.registry import AgentExecutorRegistry
    from app.enterprise_runtime.artifacts import runs as runs_store
    from app.enterprise_runtime.artifacts import store as astore
    from app.enterprise_runtime.models import RuntimeTaskStatus

    rstore.reset_store()
    runs_store.reset_store()
    astore.reset_store()
    approval_store.reset_store()

    rag = _rag_leg()

    # 场景 A：主链（Issue → 6 Agent → 审批门 → 受控变更/测试 → 审计回放）
    main_task = "opt03_demo_main"
    orch.start_runtime_task(_ctx(), main_task, f"ik_{main_task}", project_id=PROJECT_ID,
                            repository_id=REPOSITORY_ID, title=ISSUE, workflow_version=2)
    registry = AgentExecutorRegistry(audit=orch.agent_audit_cb())
    live_main = _drive(main_task, registry)
    scenario_main = _collect_scenario(main_task, TENANT, live=live_main)

    main_runs = sorted(runs_store.list_by_task(TENANT, main_task), key=_run_order)
    cc_runs = sorted((r for r in main_runs if r.agent_type == "code_change_agent"), key=_run_order)
    approval_at = live_main["approval"]["approved_at"] if live_main["approval"] else None
    checks_main = {
        "six_agent_runs_succeeded": (
            len(main_runs) == 6 and all(r.status == "succeeded" for r in main_runs)
        ),
        "agent_chain_order_ok": [r.agent_type for r in main_runs] == [
            "requirement_agent", "repository_research_agent", "planner_agent",
            "code_change_agent", "review_security_agent", "test_release_agent",
        ],
        "approval_gate_held": bool(live_main["checks"].get("approval_gate_held")),
        "approval_via_e2_service": bool(
            live_main["approval"] and live_main["approval"]["status"] == "approved"
        ),
        # 与 _collect_scenario 同口径：Windows 时钟粒度可能让 approve 与 CC 启动同微秒（>=），
        # 严格“审批前 CC 不得启动”由审批门现场 approval_gate_held 保证。
        "code_change_started_after_approval": bool(
            approval_at and cc_runs and cc_runs[0].started_at.isoformat() >= approval_at
        ),
        "artifact_chain_no_dangling_refs": scenario_main["dangling_refs"] == [],
        "rag_bm25_expected_hit": any(h["symbol"] == "retry_refund" for h in rag["hits"]),
        "audit_replay_complete": (
            [e["event_type"] for e in scenario_main["audit_replay"]].count("runtime.agent.succeeded") == 6
            and {"runtime.task.started", "runtime.approval.decided", "runtime.task.succeeded"}
            <= {e["event_type"] for e in scenario_main["audit_replay"]}
        ),
        "final_succeeded": scenario_main["final_status"] == RuntimeTaskStatus.SUCCEEDED.value,
    }

    # 场景 B：Review 首轮失败 → 回退 CodeChange（repair_round 递增）→ 复审通过 → 成功
    _install_review_fail_once()
    repair_task = "opt03_demo_repair"
    orch.start_runtime_task(_ctx(), repair_task, f"ik_{repair_task}", project_id=PROJECT_ID,
                            repository_id=REPOSITORY_ID, title=f"{ISSUE}（修复分支）", workflow_version=2)
    registry_b = AgentExecutorRegistry(audit=orch.agent_audit_cb())
    live_repair = _drive(repair_task, registry_b)
    scenario_repair = _collect_scenario(repair_task, TENANT, live=live_repair)

    repair_runs = sorted(runs_store.list_by_task(TENANT, repair_task), key=_run_order)
    planner_runs = [r for r in repair_runs if r.agent_type == "planner_agent"]
    cc_repair = [r for r in repair_runs if r.agent_type == "code_change_agent"]
    rtask = rstore.get(repair_task)
    checks_repair = {
        "repair_loop_triggered": rtask.repair_round >= 1 and len(cc_repair) >= 2,
        "planner_not_rerun": len(planner_runs) == 1,
        "approval_not_duplicated": rtask.approval_state == "approved",
        "final_succeeded": rtask.status == RuntimeTaskStatus.SUCCEEDED,
    }

    remote_loaded = REMOTE_WRITE_MODULE in sys.modules
    environment = {
        "agent_provider": os.environ.get("AGENT_PROVIDER", ""),
        "stores": "in_memory",
        "database": "disabled",
        "celery": "disabled",
        "workspace_root": str(DEMO_WORKSPACE),
        "data_dir": str(DEMO_ROOT),
        "real_model_opt_in": allow_real_model,
        "remote_write_module_loaded": remote_loaded,
    }
    all_checks = {**checks_main, **checks_repair, "no_remote_write_module": not remote_loaded}
    return {
        "demo": "opt03_multi_agent_e2e",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tenant_id": TENANT,
        "seed": {"project_id": PROJECT_ID, "repository_id": REPOSITORY_ID},
        "environment": environment,
        "rag": rag,
        "scenario_main": scenario_main,
        "scenario_repair": {
            **scenario_repair,
            "planner_run_count": len(planner_runs),
        },
        "checks": {**all_checks, "all_passed": all(all_checks.values())},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="OPT-03 隔离多 Agent 端到端演示")
    parser.add_argument("--out", default=str(DEMO_ROOT / "opt03_e2e_evidence.json"))
    parser.add_argument("--allow-real-model", action="store_true",
                        help="显式 opt-in：保留环境中的非 deterministic AGENT_PROVIDER（默认强制确定性）")
    args = parser.parse_args()

    _force_isolated_env(args.allow_real_model)
    report = _run_scenarios(args.allow_real_model)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"OPT-03 demo: main={report['scenario_main']['final_status']} "
          f"repair={report['scenario_repair']['final_status']} "
          f"checks={sum(1 for v in report['checks'].values() if v is True)}/{len(report['checks'])}")
    print(f"evidence={out.resolve()}")
    if not report["checks"]["all_passed"]:
        print(json.dumps(report["checks"], ensure_ascii=False, indent=2))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
