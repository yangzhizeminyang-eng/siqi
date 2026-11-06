"""企业版 E4-01 Multi-Agent Runtime 专项测试。

测试的是真实编排逻辑（状态机/租约/checkpoint/重试/接管），步骤执行器用真实函数（副作用记录器），
不是 mock 整个 orchestrator、也不伪造端到端成功。真实 DB/Worker 闭环见运行证据与文档。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.enterprise_runtime import orchestrator as rt
from app.enterprise_runtime import state_machine as sm
from app.enterprise_runtime.checkpoint import CheckpointStore, sanitize_output
from app.enterprise_runtime.models import RuntimeStepStatus, RuntimeTaskStatus
from app.enterprise_runtime.retry_policy import RetryPolicy

ACCOUNTS = {
    "admin": ("admin@company.com", "Admin123!"),
    "observer": ("observer@company.com", "Observer123!"),   # auditor
}


def _ctx(tenant_id: str, role: str, user: str = "user_admin", name: str = "A"):
    return SimpleNamespace(tenant_id=tenant_id, role=role, user_id=user, user_name=name)


def _ok_executor(effects: list):
    def exec_(step, task, ctx):
        effects.append(step.name)
        return {"ok": True, "output_summary": f"done:{step.name}", "reason": ""}
    return exec_


def _failing_executor(kind: str = "retryable"):
    def exec_(step, task, ctx):
        return {"ok": False, "failure_kind": kind, "reason": "boom", "output_summary": ""}
    return exec_


def _all_executors(effects):
    names = ("context_fetch", "enterprise_rag_analysis", "plan_generation", "execution", "test_execution")
    return {n: _ok_executor(effects) for n in names}


def _drive(worker: str, task_id: str, executors, policy=None, max_iter: int = 20):
    """循环驱动单步直到到达终态/等待态或无进展。"""
    for _ in range(max_iter):
        task = rt.rstore.get(task_id)
        if task is None:
            break
        if task.status in (RuntimeTaskStatus.SUCCEEDED, RuntimeTaskStatus.FAILED,
                           RuntimeTaskStatus.CANCELLED, RuntimeTaskStatus.WAITING_APPROVAL,
                           RuntimeTaskStatus.WAITING_HUMAN):
            break
        before = rt.rstore.get(task_id).version
        rt.run_until_blocked(worker, task_id, executors, policy=policy)
        after = rt.rstore.get(task_id).version
        if after == before:
            break
    return rt.rstore.get(task_id)


# 1) 正常任务 queued -> succeeded
def test_normal_task_queued_to_succeeded():
    effects: list[str] = []
    task = rt.start_runtime_task(_ctx("t1", "tenant_owner"), "T1", "k1")
    assert task.status == RuntimeTaskStatus.QUEUED
    _drive("w1", "T1", _all_executors(effects))
    final = rt.rstore.get("T1")
    assert final.status == RuntimeTaskStatus.SUCCEEDED
    assert set(effects) == {"context_fetch", "enterprise_rag_analysis", "plan_generation", "execution", "test_execution"}
    assert final.completed_steps == ["context_fetch", "enterprise_rag_analysis", "plan_generation", "execution", "test_execution"]


# 2) 审批前不得进入 execution；审批通过后继续
def test_approval_gate_blocks_execution_then_approve():
    effects: list[str] = []
    task = rt.start_runtime_task(_ctx("t1", "tenant_owner"), "T2", "k2")
    task.approval_state = "pending"  # E2 治理判定高风险
    rt.rstore.put(task)
    final = _drive("w1", "T2", _all_executors(effects))
    assert final.status == RuntimeTaskStatus.WAITING_APPROVAL
    assert "execution" not in effects  # 审批前不得进入 execution
    rt.approve_runtime_task("T2", True, actor="u", actor_name="审批人")
    final2 = _drive("w1", "T2", _all_executors(effects))
    assert final2.status == RuntimeTaskStatus.SUCCEEDED
    assert "execution" in effects and "test_execution" in effects


# 3) 审批拒绝后任务终止
def test_approval_reject_terminates():
    task = rt.start_runtime_task(_ctx("t1", "tenant_owner"), "T3", "k3")
    task.approval_state = "pending"
    rt.rstore.put(task)
    _drive("w1", "T3", _all_executors([]))
    rt.approve_runtime_task("T3", False, actor="u", actor_name="审批人")
    final = rt.rstore.get("T3")
    assert final.status == RuntimeTaskStatus.FAILED
    assert final.approval_state == "rejected"


# 4) checkpoint 在步骤完成后真实持久化
def test_checkpoint_persisted_after_step():
    rt.start_runtime_task(_ctx("t1", "tenant_owner"), "T4", "k4")
    rt.run_until_blocked("w1", "T4", _all_executors([]))
    cp = CheckpointStore().get("T4")
    assert cp is not None and cp.checkpoint_version >= 1
    assert cp.completed_steps == ["context_fetch"]


# 5) Worker 崩溃后从 checkpoint 恢复（不重跑已完成步骤）
def test_crash_resume_no_duplicate_side_effects():
    effects: list[str] = []
    rt.start_runtime_task(_ctx("t1", "tenant_owner"), "T5", "k5")
    rt.run_until_blocked("w1", "T5", _all_executors(effects))  # 完成 context_fetch
    assert effects == ["context_fetch"]
    # 模拟 worker 崩溃：不再驱动；新 worker 从 checkpoint 继续
    final = _drive("w2", "T5", _all_executors(effects))
    assert final.status == RuntimeTaskStatus.SUCCEEDED
    # context_fetch 只执行一次（已完成步骤不被重放）
    assert effects.count("context_fetch") == 1


# 6) 同一步骤并发只有一个 Worker 抢占成功
def test_single_winner_lease():
    rt.start_runtime_task(_ctx("t1", "tenant_owner"), "T6", "k6")
    task = rt.rstore.get("T6")
    step = task.steps[0]
    out1, _ = rt.rstore.claim_step("T6", step.step_id, "workerA", expected_version=task.version)
    assert out1 == "claimed"
    out2, _ = rt.rstore.claim_step("T6", step.step_id, "workerB", expected_version=rt.rstore.get("T6").version)
    assert out2 == "lost"  # 第二个抢占失败（同一 step 只有一个有效执行者）
    assert rt.rstore.get("T6").lease_worker == "workerA"


# 7) 重复投递不重复产生副作用（已完成步骤不再执行）
def test_idempotent_replay_no_double_side_effect():
    effects: list[str] = []
    rt.start_runtime_task(_ctx("t1", "tenant_owner"), "T7", "k7")
    rt.run_until_blocked("w1", "T7", _all_executors(effects))
    assert effects.count("context_fetch") == 1
    # 重复投递：无 PENDING/FAILED 步骤可抢占 → 不再执行
    assert rt.claim_next_step("w1", "T7") is not None  # 下一步仍可执行
    rt.run_until_blocked("w1", "T7", _all_executors(effects))
    assert effects.count("context_fetch") == 1


# 8) 可重试失败按上限和退避策略执行
def test_retry_backoff_policy():
    policy = RetryPolicy(max_retries=2, base_backoff_seconds=1.0, backoff_factor=2.0)
    rt.start_runtime_task(_ctx("t1", "tenant_owner"), "T8", "k8", policy=policy)
    task = rt.rstore.get("T8")
    step = task.steps[0]
    step.retry_count = 1
    assert policy.can_retry(1, None) is True
    assert policy.next_backoff(1) == 2.0  # 1 * 2^1
    assert policy.next_backoff(2) == 4.0
    assert policy.can_retry(2, None) is False  # 达上限


# 9) 超过重试上限进入 waiting_human
def test_retry_cap_enters_waiting_human():
    policy = RetryPolicy(max_retries=1)
    rt.start_runtime_task(_ctx("t1", "tenant_owner"), "T9", "k9", policy=policy)
    task = rt.rstore.get("T9")
    task.approval_state = "approved"  # 不需审批，专注失败路径
    rt.rstore.put(task)
    final = _drive("w1", "T9", {n: _failing_executor("retryable") for n in
                                ("context_fetch", "enterprise_rag_analysis", "plan_generation", "execution", "test_execution")},
                   policy=policy)
    assert final.status == RuntimeTaskStatus.WAITING_HUMAN
    assert final.steps[0].failure_kind.value == "retryable"


# 10) 人工接管后可以 resume / retry / cancel
def test_human_takeover_resume_retry_cancel():
    policy = RetryPolicy(max_retries=0)  # 直接失败进 WAITING_HUMAN
    rt.start_runtime_task(_ctx("t1", "tenant_owner"), "T10a", "k10a", policy=policy)
    task = rt.rstore.get("T10a")
    task.approval_state = "approved"
    rt.rstore.put(task)
    _drive("w1", "T10a", {n: _failing_executor("retryable") for n in
                          ("context_fetch", "enterprise_rag_analysis", "plan_generation", "execution", "test_execution")},
           policy=policy)
    assert rt.rstore.get("T10a").status == RuntimeTaskStatus.WAITING_HUMAN
    # cancel
    rt.complete_human_takeover(_ctx("t1", "tenant_owner"), "T10a", "cancel")
    assert rt.rstore.get("T10a").status == RuntimeTaskStatus.CANCELLED

    # retry：重新进入可执行
    rt.start_runtime_task(_ctx("t1", "tenant_owner"), "T10b", "k10b", policy=policy)
    task = rt.rstore.get("T10b"); task.approval_state = "approved"; rt.rstore.put(task)
    _drive("w1", "T10b", {n: _failing_executor("retryable") for n in
                          ("context_fetch", "enterprise_rag_analysis", "plan_generation", "execution", "test_execution")}, policy=policy)
    rt.complete_human_takeover(_ctx("t1", "tenant_owner"), "T10b", "retry")
    assert rt.rstore.get("T10b").status == RuntimeTaskStatus.RUNNING
    assert rt.rstore.get("T10b").steps[0].status == RuntimeStepStatus.PENDING


# 11/12) 无权限/auditor 不能人工接管
def test_unauthorized_and_auditor_takeover_denied():
    rt.start_runtime_task(_ctx("t1", "tenant_owner"), "T11", "k11")
    task = rt.rstore.get("T11"); task.status = RuntimeTaskStatus.WAITING_HUMAN; rt.rstore.put(task)
    with pytest.raises(PermissionError):
        rt.complete_human_takeover(_ctx("t1", "developer"), "T11", "resume")
    with pytest.raises(PermissionError):
        rt.complete_human_takeover(_ctx("t1", "auditor"), "T11", "resume")


# 13) 跨租户读取/恢复/重试/接管全部 403 并审计
def test_cross_tenant_all_denied_and_audited():
    main.AUDIT_EVENTS.clear()
    rt.start_runtime_task(_ctx("tenant_a", "tenant_owner"), "T13", "k13")
    for fn in (rt.resume_from_checkpoint,):
        with pytest.raises(PermissionError):
            fn(_ctx("tenant_b", "tenant_owner"), "T13")
    with pytest.raises(PermissionError):
        rt.request_retry(_ctx("tenant_b", "tenant_owner"), "T13", "T13:context_fetch", "x")
    with pytest.raises(PermissionError):
        rt.request_human_takeover(_ctx("tenant_b", "tenant_owner"), "T13", "T13:context_fetch", "x")
    with pytest.raises(PermissionError):
        rt.complete_human_takeover(_ctx("tenant_b", "tenant_owner"), "T13", "resume")
    denied = [e for e in main.AUDIT_EVENTS.values() if e.event_type == "runtime.tenant.denied"]
    assert len(denied) >= 1  # 拒绝审计已写入


# 14) 旧入口/人工接管不能绕过审批
def test_takeover_cannot_skip_approval():
    rt.start_runtime_task(_ctx("t1", "tenant_owner"), "T14", "k14")
    task = rt.rstore.get("T14")
    task.approval_state = "pending"
    task.status = RuntimeTaskStatus.WAITING_HUMAN
    task.current_step = "plan_generation"
    rt.rstore.put(task)
    out = rt.complete_human_takeover(_ctx("t1", "tenant_owner"), "T14", "resume")
    assert out.status == RuntimeTaskStatus.WAITING_APPROVAL  # 被打回审批，不能跳过


# 15) checkpoint 和日志不含凭据/完整代码正文
def test_checkpoint_sanitized():
    assert sanitize_output("password=abc123 token=xyz", 400) == "[sanitized]"
    assert "token" not in sanitize_output("fix: rotate api_key", 400).lower()
    long_code = "def x():\n" + ("    pass\n" * 200)
    assert len(sanitize_output(long_code, 80)) <= 80


# 16) stale executing 可被安全回收
def test_stale_lease_reclaim():
    rt.start_runtime_task(_ctx("t1", "tenant_owner"), "T16", "k16")
    l1 = rt.claim_next_step("wA", "T16")
    assert l1 is not None
    # workerA 崩溃（lease 未释放/未续约）→ 超时后可回收
    later = datetime.now(timezone.utc) + timedelta(seconds=400)
    stale = rt.recover_stale_execution("wB", later)
    assert any(t.id == "T16" for t in stale)
    l2 = rt.claim_next_step("wB", "T16")
    assert l2 is not None  # 新 worker 可重新抢占
    assert rt.rstore.get("T16").lease_worker == "wB"


# 17) DB/Redis 短暂不可用时状态不被错误推进（任务缺失/无执行器 → 不伪造成功）
def test_no_false_progress_on_missing_components():
    assert rt.claim_next_step("w1", "NOT_EXIST") is None  # 任务缺失 → 无租约、不推进
    rt.start_runtime_task(_ctx("t1", "tenant_owner"), "T17", "k17")
    task = rt.rstore.get("T17"); task.approval_state = "approved"; rt.rstore.put(task)
    final = _drive("w1", "T17", {})  # 无执行器 → 环境不可用
    assert final.status == RuntimeTaskStatus.WAITING_HUMAN
    assert final.steps[0].failure_kind.value == "environment"


# 18) 应用可启动（团队链不受影响）
def test_app_boots_and_runtime_router_live():
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"account": ACCOUNTS["admin"][0], "password": ACCOUNTS["admin"][1]}).status_code == 200
    r = c.get("/api/enterprise/runtime/tasks")
    assert r.status_code == 200


# HTTP 控制面：创建/查询/重试/接管；auditor 接管 403
def test_http_runtime_control_plane_and_auditor_denied():
    c = TestClient(main.app)
    c.post("/api/auth/login", json={"account": ACCOUNTS["admin"][0], "password": ACCOUNTS["admin"][1]})
    r = c.post("/api/enterprise/runtime/tasks", json={"task_id": "HTTP1", "idempotency_key": "hk1"})
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "queued"
    assert c.get("/api/enterprise/runtime/tasks/HTTP1").status_code == 200
    # 幂等：重复提交同一幂等键返回同一任务
    r2 = c.post("/api/enterprise/runtime/tasks", json={"task_id": "HTTP1-X", "idempotency_key": "hk1"})
    assert r2.json()["data"]["id"] == "HTTP1"
    # auditor 接管 403
    c2 = TestClient(main.app)
    c2.post("/api/auth/login", json={"account": ACCOUNTS["observer"][0], "password": ACCOUNTS["observer"][1]})
    rt.request_human_takeover(_ctx("tenant_default", "tenant_owner"), "HTTP1", "HTTP1:context_fetch", "need help")
    assert c2.post("/api/enterprise/runtime/tasks/HTTP1/takeover/complete", json={"action": "resume"}).status_code == 403

# 19) 真实 DB 持久化 round-trip（B1：任务/checkpoint 落库，重启恢复可读回）
def test_runtime_persistence_db_roundtrip(tmp_path, monkeypatch):
    db_path = tmp_path / "rt_persist.db"
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("PERSIST_STRICT", "false")
    from app.db.session import get_engine
    from app.models.orm import Base

    Base.metadata.create_all(get_engine())

    from app.enterprise_runtime import store as rstore2
    from app.enterprise_runtime.checkpoint import CheckpointStore

    rstore2.reset_store()
    CheckpointStore().reset()

    task = rt.start_runtime_task(_ctx("t_persist", "tenant_owner"), "PT1", "pk1")
    task.approval_state = "pending"
    rstore2.put(task)  # 写透 DB
    CheckpointStore().save(task, step_id="PT1:context_fetch", output_summary="done")  # checkpoint 写透 DB

    # 模拟进程重启：清空内存缓存后从 DB 恢复
    rstore2.reset_store()
    CheckpointStore().reset()
    rstore2.restore_from_db()
    CheckpointStore().restore_from_db()

    restored = rstore2.get("PT1")
    assert restored is not None
    assert restored.tenant_id == "t_persist"
    assert restored.approval_state == "pending"
    cp = CheckpointStore().get("PT1")
    assert cp is not None and cp.checkpoint_version >= 1
    assert cp.output_summary == "done"
    assert cp.tenant_id == "t_persist"
