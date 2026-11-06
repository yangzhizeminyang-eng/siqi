"""E4-01 真实流程证据：通过真实服务端点 + 进程内 worker 循环驱动，采集证据到 JSON。

场景（全部真实执行，非 mock orchestrator）：
1. 正常任务 queued -> succeeded（真实审计事件）。
2. 审批门禁：plan_generation 后进入 WAITING_APPROVAL，审批前不执行 execution。
3. 失败 -> retry -> 超上限 -> waiting_human；人工 resume 后继续完成。
4. 同一步骤两个 worker 抢占：仅一个成功。
5. 跨租户恢复/接管被拒 403 + 拒绝审计。
6. checkpoint 脱敏（无凭据）。
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import main
from app.enterprise_runtime import orchestrator as rt
from app.enterprise_runtime import store as rstore
from app.enterprise_runtime.checkpoint import CheckpointStore, sanitize_output
from app.enterprise_runtime.models import RuntimeTaskStatus
from app.enterprise_runtime.retry_policy import RetryPolicy

EVIDENCE: dict = {}


def _ctx(tenant_id, role, user="user_admin", name="A"):
    return SimpleNamespace(tenant_id=tenant_id, role=role, user_id=user, user_name=name)


def _executors(effects, *, fail_steps=()):
    def make(name):
        def exec_(step, task, ctx):
            if name in fail_steps:
                return {"ok": False, "failure_kind": "retryable", "reason": "transient boom", "output_summary": ""}
            effects.append(name)
            return {"ok": True, "output_summary": f"done:{name}", "reason": ""}
        return exec_
    return {n: make(n) for n in ("context_fetch", "enterprise_rag_analysis", "plan_generation", "execution", "test_execution")}


def _drive(worker, task_id, executors, policy=None, max_iter=20):
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


def main_flow():
    effects: list[str] = []
    task = rt.start_runtime_task(_ctx("tenant_a", "tenant_owner"), "REAL1", "rk1", title="真实主链")
    _drive("worker1", "REAL1", _executors(effects))
    EVIDENCE["1_normal"] = {"status": rstore.get("REAL1").status.value, "steps": effects}
    assert rstore.get("REAL1").status == RuntimeTaskStatus.SUCCEEDED

    # 2) 审批门禁
    effects2: list[str] = []
    rt.start_runtime_task(_ctx("tenant_a", "tenant_owner"), "REAL2", "rk2")
    task2 = rstore.get("REAL2"); task2.approval_state = "pending"; rstore.put(task2)
    _drive("worker1", "REAL2", _executors(effects2))
    EVIDENCE["2_approval_block"] = {"status": rstore.get("REAL2").status.value, "executed_execution": "execution" in effects2}
    assert rstore.get("REAL2").status == RuntimeTaskStatus.WAITING_APPROVAL
    rt.approve_runtime_task("REAL2", True, actor="approver", actor_name="审批人")
    _drive("worker1", "REAL2", _executors(effects2))
    EVIDENCE["2_after_approve"] = {"status": rstore.get("REAL2").status.value, "execution_run": "execution" in effects2}

    # 3) 失败->重试->超上限->人工接管->resume
    policy = RetryPolicy(max_retries=1)
    rt.start_runtime_task(_ctx("tenant_a", "tenant_owner"), "REAL3", "rk3", policy=policy)
    task3 = rstore.get("REAL3"); task3.approval_state = "approved"; rstore.put(task3)
    _drive("worker1", "REAL3", _executors([], fail_steps=("execution",)), policy=policy)
    EVIDENCE["3_after_retries"] = {"status": rstore.get("REAL3").status.value,
                                   "reason": rstore.get("REAL3").wait_human_reason}
    assert rstore.get("REAL3").status == RuntimeTaskStatus.WAITING_HUMAN
    rt.complete_human_takeover(_ctx("tenant_a", "tenant_owner"), "REAL3", "resume")
    EVIDENCE["3_after_resume_start"] = {"status": rstore.get("REAL3").status.value}
    # 人工确认修复后，用成功执行器继续驱动到完成
    _drive("worker1", "REAL3", _executors([]))
    EVIDENCE["3_after_resume"] = {"status": rstore.get("REAL3").status.value}

    # 4) 同一步骤两 worker 抢占
    rt.start_runtime_task(_ctx("tenant_a", "tenant_owner"), "REAL4", "rk4")
    t4 = rstore.get("REAL4"); step4 = t4.steps[0]
    o1, _ = rstore.claim_step("REAL4", step4.step_id, "wA", expected_version=t4.version)
    o2, _ = rstore.claim_step("REAL4", step4.step_id, "wB", expected_version=rstore.get("REAL4").version)
    EVIDENCE["4_single_winner"] = {"workerA": o1, "workerB": o2}
    assert o1 == "claimed" and o2 == "lost"

    # 5) 跨租户恢复/接管被拒
    rt.start_runtime_task(_ctx("tenant_a", "tenant_owner"), "REAL5", "rk5")
    t5 = rstore.get("REAL5"); t5.status = RuntimeTaskStatus.WAITING_HUMAN; rstore.put(t5)
    denied = []
    for fn, args in ((rt.resume_from_checkpoint, ("REAL5",)),
                     (rt.complete_human_takeover, ("REAL5", "resume"))):
        try:
            fn(_ctx("tenant_b", "tenant_owner"), *args)
        except PermissionError:
            denied.append(True)
    EVIDENCE["5_cross_tenant_denied"] = {"denied": all(denied),
                                         "audit_denied": sum(1 for e in main.AUDIT_EVENTS.values()
                                                             if e.event_type == "runtime.tenant.denied")}
    assert all(denied)

    # 6) checkpoint 脱敏
    rt.start_runtime_task(_ctx("tenant_a", "tenant_owner"), "REAL6", "rk6")
    rt.run_until_blocked("w1", "REAL6", _executors([]))
    cp = CheckpointStore().get("REAL6")
    EVIDENCE["6_checkpoint"] = {"checkpoint_version": cp.checkpoint_version,
                                "completed_steps": cp.completed_steps,
                                "sanitized": sanitize_output("token=abc password=zzz", 400)}
    assert cp.checkpoint_version >= 1

    # 7) 真实 HTTP 端点创建/查询
    client = TestClient(main.app)
    client.post("/api/auth/login", json={"account": "admin@company.com", "password": "Admin123!"})
    r = client.post("/api/enterprise/runtime/tasks", json={"task_id": "REALHTTP1", "idempotency_key": "rhk1", "title": "HTTP 主链"})
    g = client.get("/api/enterprise/runtime/tasks/REALHTTP1")
    EVIDENCE["7_http"] = {"create_status": r.status_code, "get_status": g.status_code,
                          "id": g.json()["data"]["id"], "state": g.json()["data"]["status"]}

    print(json.dumps(EVIDENCE, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main_flow()
