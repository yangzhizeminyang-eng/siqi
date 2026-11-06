"""E4-01 真实闭环驱动：真实 MySQL + 真实 Redis + 跨进程重启恢复 + 真实 E2 审批接线。

前置：Docker 已起 e4-mysql(3307, db=agent_e4) 与 e4-redis(6379)。
用法（真实 MySQL 持久化）：
  USE_SQLALCHEMY=true DATABASE_URL=mysql+pymysql://root:e4rootpw@127.0.0.1:3307/agent_e4?charset=utf8mb4 \
  REDIS_URL=redis://127.0.0.1:6379/0 \
  PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m scripts.e4_runtime_real_loop

输出 JSON 证据：正常链路、跨进程重启恢复、双 claim 单赢家、失败→重试→接管→resume、真实 E2 审批接线、
跨租户 403+审计、checkpoint 落库。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.enterprise_runtime import orchestrator as rt
from app.enterprise_runtime import store as rstore
from app.enterprise_runtime.checkpoint import CheckpointStore
from app.enterprise_runtime.models import RuntimeTaskStatus
from app.enterprise_runtime.retry_policy import RetryPolicy

EVIDENCE: dict = {}


def _ctx(tenant_id, role, user="user_admin", name="A"):
    return SimpleNamespace(tenant_id=tenant_id, role=role, user_id=user, user_name=name)


def _executors(effects, *, fail_steps=()):
    def make(name):
        def exec_(step, task, ctx):
            if name in fail_steps:
                return {"ok": False, "failure_kind": "retryable", "reason": "transient real failure", "output_summary": ""}
            effects.append(name)
            return {"ok": True, "output_summary": f"real:{name}", "reason": ""}
        return exec_
    return {n: make(n) for n in ("context_fetch", "enterprise_rag_analysis", "plan_generation", "execution", "test_execution")}


def _drive(worker, task_id, executors, policy=None, max_iter=25):
    for _ in range(max_iter):
        task = rstore.get(task_id)
        if task is None:
            break
        if task.status in (RuntimeTaskStatus.SUCCEEDED, RuntimeTaskStatus.FAILED,
                           RuntimeTaskStatus.CANCELLED, RuntimeTaskStatus.WAITING_APPROVAL,
                           RuntimeTaskStatus.WAITING_HUMAN):
            break
        before = rstore.get(task_id).version
        rt.run_until_blocked(worker, task_id, executors, policy=policy)
        if rstore.get(task_id).version == before:
            break
    return rstore.get(task_id)


def _bootstrap():
    from app.db.bootstrap import restore_from_db

    restore_from_db()


def main():
    from scripts.authorization_gate import require_authorization

    require_authorization(
        os.environ.get("AUTHORIZATION_OPERATION", ""),
        os.environ.get("AUTHORIZATION_LOG", "") or None,
    )
    # 0) 迁移已由入口执行（RUN_ALEMBIC_UPGRADE）；确保真源恢复
    rstore.reset_store()
    CheckpointStore().reset()
    _bootstrap()

    # 1) 正常任务（真实 DB 写透）
    effects: list[str] = []
    rt.start_runtime_task(_ctx("tenant_a", "tenant_owner"), "RC1", "rck1", title="真实闭环主链")
    _drive("worker-1", "RC1", _executors(effects))
    EVIDENCE["1_normal"] = {"status": rstore.get("RC1").status.value, "steps": list(effects)}
    assert rstore.get("RC1").status == RuntimeTaskStatus.SUCCEEDED

    # 2) 跨进程重启恢复：RC2 在 context_fetch 后 checkpoint 落库；新进程 bootstrap 从 DB 恢复并完成
    rt.start_runtime_task(_ctx("tenant_a", "tenant_owner"), "RC2", "rck2")
    rt.run_until_blocked("worker-1", "RC2", _executors([]))  # 完成 context_fetch，checkpoint 落库
    cp_before = CheckpointStore().get("RC2")
    EVIDENCE["2_checkpoint_db"] = {"version": cp_before.checkpoint_version, "completed": cp_before.completed_steps}
    proc = subprocess.run([sys.executable, "-m", "scripts.e4_runtime_restart"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240,
                          env={**os.environ, "E4_RESTART_TASK_ID": "RC2"})
    out = "\n".join((proc.stdout or "").splitlines()[-3:] + (["ERR"] + (proc.stderr or "").splitlines()[-5:] if proc.returncode != 0 else []))
    EVIDENCE["2_restart_recovery"] = {"exit": proc.returncode, "last_out": out}
    assert proc.returncode == 0 and "RESTART_FINAL succeeded" in (proc.stdout or ""), (proc.stdout, proc.stderr)

    # 3) 双 claim 单赢家（真实 DB 条件 UPDATE）
    rt.start_runtime_task(_ctx("tenant_a", "tenant_owner"), "RC3", "rck3")
    t3 = rstore.get("RC3"); step3 = t3.steps[0]
    o1, _ = rstore.claim_step("RC3", step3.step_id, "worker-A", expected_version=t3.version)
    o2, _ = rstore.claim_step("RC3", step3.step_id, "worker-B", expected_version=rstore.get("RC3").version)
    EVIDENCE["3_single_winner"] = {"A": o1, "B": o2}
    assert o1 == "claimed" and o2 == "lost"

    # 4) 失败→重试→超上限→waiting_human→人工 resume→succeeded（真实 DB）
    policy = RetryPolicy(max_retries=1)
    rt.start_runtime_task(_ctx("tenant_a", "tenant_owner"), "RC4", "rck4", policy=policy)
    t4 = rstore.get("RC4"); t4.approval_state = "approved"; rstore.put(t4)
    _drive("worker-1", "RC4", _executors([], fail_steps=("execution",)), policy=policy)
    EVIDENCE["4_waiting_human"] = {"status": rstore.get("RC4").status.value, "reason": rstore.get("RC4").wait_human_reason}
    assert rstore.get("RC4").status == RuntimeTaskStatus.WAITING_HUMAN
    rt.complete_human_takeover(_ctx("tenant_a", "tenant_owner"), "RC4", "resume")
    _drive("worker-1", "RC4", _executors([]))
    EVIDENCE["4_resume"] = {"status": rstore.get("RC4").status.value}
    assert rstore.get("RC4").status == RuntimeTaskStatus.SUCCEEDED

    # 5) 真实 E2 审批接线：runtime 任务在 plan_generation 后入 WAITING_APPROVAL，真实 approvals.approve 通过 → hook 回写 → execution
    rt.start_runtime_task(_ctx("tenant_a", "tenant_owner"), "RC5", "rck5")
    t5 = rstore.get("RC5"); t5.approval_state = "pending"; rstore.put(t5)
    _drive("worker-1", "RC5", _executors([]))  # 走 context_fetch/rag/plan → execution 前被审批门禁拦下
    assert rstore.get("RC5").status == RuntimeTaskStatus.WAITING_APPROVAL, rstore.get("RC5").status
    from app.approvals import store as aps
    from app.approvals import service as ap_service
    from app.approvals.models import ApprovalApplicationRecord, ApprovalApprovePayload
    approval = aps.put(ApprovalApplicationRecord(
        id="ap_rc5", tenant_id="tenant_a", actor_id="user_admin", actor_name="A",
        resource_type="runtime_task", resource_id="RC5", operation="runtime.execute",
        title="执行 RC5", risk_level="L2", decision="approval_required", approval_status="pending",
        idempotency_key="aprc5", created_by="user_admin",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)))
    ap_service.approve(_ctx("tenant_a", "tenant_owner"), SimpleNamespace(id="user_approver", name="审批人"),
                       "ap_rc5", ApprovalApprovePayload(comment="ok"))
    EVIDENCE["5_e2_approval"] = {"approval_status": aps.get("ap_rc5").approval_status,
                                 "runtime_status": rstore.get("RC5").status.value,
                                 "runtime_approval_state": rstore.get("RC5").approval_state}
    assert aps.get("ap_rc5").approval_status == "approved"
    assert rstore.get("RC5").status == RuntimeTaskStatus.RUNNING
    _drive("worker-1", "RC5", _executors([]))
    EVIDENCE["5_after_approve_exec"] = {"status": rstore.get("RC5").status.value}
    assert rstore.get("RC5").status == RuntimeTaskStatus.SUCCEEDED

    # 6) 跨租户恢复/接管被拒 + 审计
    rt.start_runtime_task(_ctx("tenant_a", "tenant_owner"), "RC6", "rck6")
    t6 = rstore.get("RC6"); t6.status = RuntimeTaskStatus.WAITING_HUMAN; rstore.put(t6)
    denied = 0
    try:
        rt.resume_from_checkpoint(_ctx("tenant_b", "tenant_owner"), "RC6")
    except PermissionError:
        denied += 1
    try:
        rt.complete_human_takeover(_ctx("tenant_b", "tenant_owner"), "RC6", "resume")
    except PermissionError:
        denied += 1
    from app import main
    EVIDENCE["6_cross_tenant"] = {"denied": denied,
                                  "audit_denied": sum(1 for e in main.AUDIT_EVENTS.values()
                                                      if e.event_type == "runtime.tenant.denied")}
    assert denied == 2

    print(json.dumps(EVIDENCE, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()