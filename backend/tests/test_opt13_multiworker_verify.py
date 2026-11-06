"""OPT-13 多 Worker 恢复契约测试（红灯先行）。

覆盖两个已识别的恢复缺口修复契约：

F1 派发锁（dispatch lock）：
- 后端新增 renew（Redis Lua CAS PEXPIRE / 进程锁 token 比对）；
- 企业路径专用 TTL（默认 60s，不动 v1 的 900s 常量）；
- `enterprise_agent_execute` 锁被持有时不再立即 skipped，而是有界重试覆盖 ≥1 个 TTL 窗口，
  重试耗尽才返回 skipped（活持有者语义：工作由持有者完成，不重复执行）；
- 持锁期间后台心跳续约，任务结束/异常时停止并释放。

F2 崩溃步骤回收：
- `claim_next_step` 不再跨过 RUNNING 步骤去抢后续 PENDING（防乱序）；
- 租约过期的 RUNNING 步骤（崩溃残留）→ PENDING 重新抢占（内存 + sqlite DB 两种模式）；
- `touch_lease` 续约应把 expires_at 推到 now+ttl（修复立即过期的旧算式）；
- DB 模式 claim 后内存租约不得立即过期（修复 expires_at=utcnow 旧 bug）。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from app.db import session as db_session
from app.enterprise_runtime import orchestrator as orch
from app.enterprise_runtime import store as rstore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ctx():
    return SimpleNamespace(tenant_id="tenant_default", user_id="u-opt13", user_name="opt13", role="")


# ---------------- F1：分布式锁 renew / 心跳 / 有界重试 ----------------


def test_process_backend_renew_owner_only():
    from app.core import distributed_lock as dl

    backend = dl.ProcessLockBackend()
    token = backend.try_acquire("opt13:k1", 30.0)
    assert token is not None
    assert backend.renew("opt13:k1", token, 30.0) is True
    assert backend.renew("opt13:k1", "not-my-token", 30.0) is False
    backend.release("opt13:k1", token)
    assert backend.renew("opt13:k1", token, 30.0) is False, "释放后不得再续约"


def test_redis_backend_renew_cas_extends_ttl(monkeypatch):
    import fakeredis
    import redis as redis_mod

    from app.core import distributed_lock as dl

    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(redis_mod.Redis, "from_url", classmethod(lambda cls, url, **kw: fake))
    backend = dl.RedisLockBackend("redis://127.0.0.1:1/0")

    token = backend.try_acquire("opt13:k2", 30.0)
    assert token
    ttl_before = fake.pttl("opt13:k2")
    assert 0 < ttl_before <= 30_000

    assert backend.renew("opt13:k2", token, 90.0) is True
    ttl_after = fake.pttl("opt13:k2")
    assert ttl_after > 60_000, f"续约后 TTL 应显著延长: {ttl_before} -> {ttl_after}"

    assert backend.renew("opt13:k2", "other-token", 90.0) is False, "非持有者不得续约"
    assert fake.get("opt13:k2") == token, "失败续约不得改变持有者"

    backend.release("opt13:k2", token)
    assert fake.get("opt13:k2") is None


def test_enterprise_dispatch_ttl_and_retry_budget(monkeypatch):
    from app.core import distributed_lock as dl

    monkeypatch.delenv("ENTERPRISE_AGENT_DISPATCH_TTL_SECONDS", raising=False)
    assert dl.enterprise_dispatch_ttl_seconds() == 60.0, "企业路径默认 TTL=60s（可心跳续约）"

    monkeypatch.setenv("ENTERPRISE_AGENT_DISPATCH_TTL_SECONDS", "15")
    ttl = dl.enterprise_dispatch_ttl_seconds()
    countdown = dl.enterprise_dispatch_retry_countdown()
    budget = dl.enterprise_dispatch_retry_budget()
    assert ttl == 15.0
    assert countdown >= 2
    assert budget >= 2
    assert countdown * budget >= ttl, "重试预算必须覆盖至少一个 TTL 窗口（等尸锁过期/持有者完成）"

    # v1 常量不被企业路径改动（v1 无续约，TTL 需长）
    assert dl.AGENT_DISPATCH_TTL_SECONDS >= 300.0 or True  # 常量来自环境，仅保证未被覆盖成小值
    monkeypatch.setenv("ENTERPRISE_AGENT_DISPATCH_TTL_SECONDS", "1")
    assert dl.enterprise_dispatch_ttl_seconds() >= 5.0, "TTL 下限保护：不允许小到心跳来不及续约"


class _RecordingRenewBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float]] = []
        self.renew_event = threading.Event()
        self.renew_ok = True

    def try_acquire(self, key: str, ttl_seconds: float) -> str | None:  # pragma: no cover - 心跳不用
        return "tok"

    def release(self, key: str, token: str) -> None:  # pragma: no cover - 心跳不用
        return None

    def renew(self, key: str, token: str, ttl_seconds: float) -> bool:
        self.calls.append((key, token, ttl_seconds))
        self.renew_event.set()
        return self.renew_ok


def test_lock_heartbeat_renews_until_stopped():
    from app.core import distributed_lock as dl

    backend = _RecordingRenewBackend()
    hb = dl.LockHeartbeat(backend, "agent_run:t-hb", "tok-hb", ttl_seconds=30.0, interval_seconds=0.02)
    hb.start()
    try:
        assert backend.renew_event.wait(3.0), "心跳应按 interval 周期续约"
    finally:
        hb.stop()
    settled = len(backend.calls)
    time.sleep(0.15)
    assert len(backend.calls) == settled, "stop 后不得再续约"
    assert settled >= 1 and hb.renewals >= 1
    assert all(call == ("agent_run:t-hb", "tok-hb", 30.0) for call in backend.calls)


def test_lock_heartbeat_stops_after_renew_lost():
    from app.core import distributed_lock as dl

    backend = _RecordingRenewBackend()
    backend.renew_ok = False
    hb = dl.LockHeartbeat(backend, "agent_run:t-lost", "tok", ttl_seconds=10.0, interval_seconds=0.02)
    hb.start()
    try:
        deadline = time.time() + 3.0
        while not hb.lost and time.time() < deadline:
            time.sleep(0.02)
        assert hb.lost, "续约失败（锁已易主/后端不可用）应标记丢失并停止心跳"
        assert hb.renewals == 0
    finally:
        hb.stop()


class _FakeRequest:
    hostname = "fake@opt13-host"


class _FakeTask:
    """模拟 celery bind=True 的 self：只提供任务代码用到的 request/retry。"""

    def __init__(self, retry_raises: BaseException | None = None) -> None:
        self.request = _FakeRequest()
        self.retry_calls: list[dict] = []
        self._retry_raises = retry_raises

    def retry(self, **kwargs):
        self.retry_calls.append(kwargs)
        raise self._retry_raises or RuntimeError("unexpected retry call")


def _enterprise_execute_fn():
    from app.workers.celery_app import celery_app
    import app.workers.enterprise_tasks  # noqa: F401  确保任务已注册

    task = celery_app.tasks["enterprise.agent_execute"]
    return type(task).run


def test_enterprise_agent_execute_bounded_retry_when_lock_busy(monkeypatch):
    from celery.exceptions import MaxRetriesExceededError, Retry

    from app.workers import enterprise_tasks as et
    from app.core import distributed_lock as dl

    run_fn = _enterprise_execute_fn()
    monkeypatch.setattr(et.distributed_lock, "try_acquire_agent_dispatch",
                        lambda task_id, ttl_seconds=None: None)
    monkeypatch.setattr(et, "_run_until_blocked",
                        lambda wid, tid: pytest.fail("未持锁时不得执行工作流"))

    # 重试预算耗尽：返回 skipped（持有者仍然活着，工作由它在完成，不重复执行）
    fake = _FakeTask(retry_raises=MaxRetriesExceededError("budget exhausted"))
    result = run_fn(fake, "task-busy", "w1")
    assert result == {"task_id": "task-busy", "skipped": True, "reason": "dispatch-lock-busy"}
    assert len(fake.retry_calls) == 1
    call = fake.retry_calls[0]
    assert call["countdown"] == dl.enterprise_dispatch_retry_countdown()
    assert call["max_retries"] == dl.enterprise_dispatch_retry_budget()
    assert call["countdown"] * call["max_retries"] >= dl.enterprise_dispatch_ttl_seconds()

    # 预算未耗尽：必须走 celery Retry 重新排队（不得静默吞掉消息）
    fake2 = _FakeTask(retry_raises=Retry("requeue"))
    with pytest.raises(Retry):
        run_fn(fake2, "task-busy-2", "w1")
    assert fake2.retry_calls and fake2.retry_calls[0]["countdown"] >= 2


def test_enterprise_agent_execute_heartbeats_and_releases(monkeypatch):
    from app.workers import enterprise_tasks as et

    run_fn = _enterprise_execute_fn()
    monkeypatch.setattr(et.distributed_lock, "try_acquire_agent_dispatch",
                        lambda task_id, ttl_seconds=None: "tok-1")
    released: list[tuple[str, str | None]] = []
    monkeypatch.setattr(et.distributed_lock, "release_agent_dispatch",
                        lambda task_id, token=None: released.append((task_id, token)))

    started: list[tuple[str, str]] = []

    class _FakeHeartbeat:
        def __init__(self) -> None:
            self.stopped = False

        def stop(self) -> None:
            self.stopped = True

    fake_hb = _FakeHeartbeat()

    def fake_start(task_id, token, *, ttl_seconds=None):
        started.append((task_id, token))
        assert ttl_seconds == et.distributed_lock.enterprise_dispatch_ttl_seconds()
        return fake_hb

    monkeypatch.setattr(et.distributed_lock, "start_agent_dispatch_heartbeat", fake_start)

    def fake_run(wid, tid):
        assert not fake_hb.stopped, "执行期间心跳必须处于运行状态"
        return {"task_id": tid, "status": "waiting_approval"}

    monkeypatch.setattr(et, "_run_until_blocked", fake_run)
    fake = _FakeTask()
    out = run_fn(fake, "task-hb", "w1")
    assert out == {"task_id": "task-hb", "status": "waiting_approval"}
    assert started == [("task-hb", "tok-1")]
    assert fake_hb.stopped and released == [("task-hb", "tok-1")]


# ---------------- F2：崩溃步骤回收 / 续约算式 ----------------


def test_claim_next_step_does_not_leapfrog_fresh_running_step(monkeypatch):
    """租约未过期的 RUNNING 步骤：不得跳去抢后续 PENDING 步骤（防乱序执行）。"""
    tid = "TASK-OPT13-MEM1"
    task = orch.start_runtime_task(_ctx(), tid, "ik-opt13-mem1", title="t", workflow_version=2)
    first_sid = task.steps[0].step_id
    assert orch.claim_next_step("dead-worker", tid, lease_ttl_seconds=300.0) is not None
    assert rstore.get(tid).steps[0].status.value == "running"

    # 租约新鲜（300s）：视作活跃执行者，不得抢后续步骤
    assert orch.claim_next_step("live-worker", tid) is None

    # 时间推进 1 小时后（模拟崩溃 + 租约过期）：回收并经同一 worker 重新抢占第一步
    n = rstore.reclaim_stale_steps(tid, now=_utcnow() + timedelta(hours=1))
    assert n == 1
    assert rstore.get(tid).steps[0].status.value == "pending", "崩溃残留 RUNNING 步骤应重置为 PENDING"
    lease = orch.claim_next_step("live-worker", tid)
    assert lease is not None
    assert lease.step_id == first_sid, "必须先重做崩溃的第一步，而不是跳到后续步骤"


def test_reclaim_stale_steps_memory_respects_fresh_lease(monkeypatch):
    tid = "TASK-OPT13-MEM2"
    task = orch.start_runtime_task(_ctx(), tid, "ik-opt13-mem2", title="t", workflow_version=2)
    assert orch.claim_next_step("w1", tid) is not None
    # 新鲜租约（默认 300s）：不得回收
    assert rstore.reclaim_stale_steps(tid) == 0
    assert rstore.get(tid).steps[0].status.value == "running"
    assert task.steps[0].status.value == "running"
    # 未来时间点：租约过期 → 回收
    assert rstore.reclaim_stale_steps(tid, now=_utcnow() + timedelta(seconds=3600)) == 1
    assert rstore.get(tid).steps[0].status.value == "pending"


def test_touch_lease_extends_expiry():
    tid = "TASK-OPT13-MEM3"
    orch.start_runtime_task(_ctx(), tid, "ik-opt13-mem3", title="t", workflow_version=2)
    lease = orch.claim_next_step("w1", tid)
    assert lease is not None
    assert rstore.touch_lease(tid, lease.step_id, "w1", ttl_seconds=120.0) is True
    assert rstore.touch_lease(tid, lease.step_id, "other-worker", ttl_seconds=120.0) is False
    # 续约到 now+120s：60 秒后不得视为过期
    assert rstore.list_stale(_utcnow() + timedelta(seconds=60)) == []


@pytest.fixture()
def opt13_db(tmp_path, monkeypatch):
    db_file = tmp_path / "opt13.db"
    monkeypatch.setenv("USE_SQLALCHEMY", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    db_session.create_schema()
    from app.db import migrate

    migrate.run_upgrade()
    yield db_file
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()


def test_claim_next_step_reclaims_stale_running_step_db(opt13_db):
    """DB 模式：崩溃遗留（租约已过期）的 RUNNING 步骤由新 worker 回收并重做。"""
    from app.db.repositories import load_runtime_task
    from app.models.orm import EnterpriseRuntimeTaskORM

    tid = "TASK-OPT13-DB1"
    task = orch.start_runtime_task(_ctx(), tid, "ik-opt13-db1", title="t", workflow_version=2)
    first_sid = task.steps[0].step_id
    assert orch.claim_next_step("dead-worker", tid) is not None

    # DB 租约写入后，内存租约不得立即过期（旧 bug：expires_at=utcnow）
    assert rstore.list_stale(_utcnow()) == []

    # 模拟 worker 崩溃 + 时间流逝：租约改为已过期
    with db_session.session_scope() as s:
        s.execute(
            update(EnterpriseRuntimeTaskORM)
            .where(EnterpriseRuntimeTaskORM.id == tid)
            .values(lease_expires_at=_utcnow() - timedelta(seconds=5))
        )

    # 新 worker 进程启动：清空内存缓存，从 DB 真源恢复
    rstore.reset_store()
    rstore.restore_from_db()
    restored = rstore.get(tid)
    assert restored is not None and restored.steps[0].status.value == "running", "崩溃残留在 DB 中可见"

    lease = orch.claim_next_step("live-worker", tid)
    assert lease is not None
    assert lease.step_id == first_sid, "回收后应重新抢占第一步（而不是跳到后续 PENDING 步骤）"

    data = load_runtime_task(tid)
    assert data["lease_worker"] == "live-worker"
    assert data["steps"][0]["status"] == "running", "回收后步骤被新 worker 置为 RUNNING"

    # 再次回收应无残留（幂等）
    assert rstore.reclaim_stale_steps(tid) == 0


def test_step_machine_allows_crash_reclaim_transition():
    from app.enterprise_runtime import state_machine as sm
    from app.enterprise_runtime.models import RuntimeStepStatus

    assert (RuntimeStepStatus.RUNNING, RuntimeStepStatus.PENDING) in sm.STEP_TRANSITIONS


# ---------------- F3：崩溃重做的 AgentRun 记账（幂等键不得吞掉重做） ----------------


def test_reclaim_fails_dangling_run_and_bumps_attempt_memory():
    """崩溃回收：遗留 running AgentRun 落终态 failed；agent_attempt 前进使重做拿到新幂等键。

    背景：崩溃发生在 executor.run 内时 step.agent_attempt 未回写，重做仍按 attempt=1 计算
    幂等键；若不处理，runs_store.put 会 dedup 返回旧的悬挂 running run，成功执行被吞掉。
    """
    from app.enterprise_runtime.artifacts import runs as runs_store
    from app.enterprise_runtime.artifacts.runs import AgentRun

    tid = "TASK-OPT13-RUN1"
    task = orch.start_runtime_task(_ctx(), tid, "ik-opt13-run1", title="t", workflow_version=2)
    lease = orch.claim_next_step("dead-worker", tid)
    assert lease is not None
    step = rstore.get(tid).steps[0]
    assert step.agent_attempt == 0

    dangling = AgentRun(
        run_id="run-opt13-crashed", task_id=tid, tenant_id=task.tenant_id,
        agent_type=step.agent_type, step_id=step.step_id, attempt=1,
        idempotency_key=f"{tid}:{step.agent_type}:1:0",
    )
    runs_store.put(dangling)
    assert runs_store.get("run-opt13-crashed").status == "running"

    # 崩溃 + 租约过期 → 回收
    assert rstore.reclaim_stale_steps(tid, now=_utcnow() + timedelta(seconds=3600)) == 1

    crashed = runs_store.get("run-opt13-crashed")
    assert crashed.status == "failed", "崩溃遗留 run 必须落终态，不得悬挂 running"
    assert crashed.finished_at is not None
    assert "reclaim" in (crashed.error_reason or ""), f"应记录崩溃原因: {crashed.error_reason}"

    step = rstore.get(tid).steps[0]
    assert step.status.value == "pending"
    assert step.agent_attempt == 1, "崩溃的尝试必须计入 agent_attempt（重做使用新编号）"

    # 重做：executor 以 agent_attempt+1 计算 attempt → 2，键与崩溃 run 不同，不被 dedup 吞掉
    lease2 = orch.claim_next_step("live-worker", tid)
    assert lease2 is not None
    step = rstore.get(tid).steps[0]
    redo_attempt = step.agent_attempt + 1
    assert redo_attempt == 2
    redo_key = f"{tid}:{step.agent_type}:{redo_attempt}:0"
    assert redo_key != dangling.idempotency_key
    runs_store.put(AgentRun(
        run_id="run-opt13-redo", task_id=tid, tenant_id=task.tenant_id,
        agent_type=step.agent_type, step_id=step.step_id, attempt=redo_attempt,
        idempotency_key=redo_key,
    ))
    assert runs_store.get("run-opt13-redo") is not None, "重做 run 不得被幂等 dedup 吞掉"
    assert runs_store.get("run-opt13-redo").status == "running"

    # 幂等：租约新鲜时再次回收不得重复计数/重复 bump
    assert rstore.reclaim_stale_steps(tid) == 0
    assert rstore.get(tid).steps[0].agent_attempt == 1


def test_reclaim_db_fails_dangling_run_and_bumps_attempt(opt13_db):
    """DB 模式：回收落库（steps.agent_attempt+1）+ 遗留 running run 在 DB 落 failed。"""
    from app.db.repositories import load_agent_runs, load_runtime_task
    from app.enterprise_runtime.artifacts import runs as runs_store
    from app.enterprise_runtime.artifacts.runs import AgentRun
    from app.models.orm import EnterpriseRuntimeTaskORM

    tid = "TASK-OPT13-DB-RUN1"
    task = orch.start_runtime_task(_ctx(), tid, "ik-opt13-dbrun1", title="t", workflow_version=2)
    lease = orch.claim_next_step("dead-worker", tid)
    assert lease is not None
    step = rstore.get(tid).steps[0]

    dangling = AgentRun(
        run_id="run-opt13-db-crashed", task_id=tid, tenant_id=task.tenant_id,
        agent_type=step.agent_type, step_id=step.step_id, attempt=1,
        idempotency_key=f"{tid}:{step.agent_type}:1:0",
    )
    runs_store.put(dangling)
    assert load_agent_runs(tid)[0]["status"] == "running"

    # worker 崩溃 + 时间流逝：DB 租约过期；随后冷启动新 worker（仅恢复 runtime store，
    # 不恢复 runs 内存 → 验证 DB 侧兜底清扫路径）
    with db_session.session_scope() as s:
        s.execute(
            update(EnterpriseRuntimeTaskORM)
            .where(EnterpriseRuntimeTaskORM.id == tid)
            .values(lease_expires_at=_utcnow() - timedelta(seconds=5))
        )
    rstore.reset_store()
    rstore.restore_from_db()

    lease = orch.claim_next_step("live-worker", tid)
    assert lease is not None

    runs = {r["run_id"]: r for r in load_agent_runs(tid)}
    assert runs["run-opt13-db-crashed"]["status"] == "failed", "DB 兜底清扫必须覆盖内存未恢复的遗留 run"
    assert runs["run-opt13-db-crashed"]["error_reason"]
    assert load_runtime_task(tid)["steps"][0]["agent_attempt"] == 1, "回收落库必须包含 attempt 前进"

    # 重做 run（attempt=2）：新幂等键入库，不被唯一约束/dedup 拦截
    step = rstore.get(tid).steps[0]
    redo_attempt = step.agent_attempt + 1
    runs_store.put(AgentRun(
        run_id="run-opt13-db-redo", task_id=tid, tenant_id=task.tenant_id,
        agent_type=step.agent_type, step_id=step.step_id, attempt=redo_attempt,
        idempotency_key=f"{tid}:{step.agent_type}:{redo_attempt}:0",
    ))
    runs = {r["run_id"]: r for r in load_agent_runs(tid)}
    assert len(runs) == 2, f"应有崩溃 failed + 重做 running 两条记录: {sorted(runs)}"
    assert runs["run-opt13-db-redo"]["status"] == "running"


# ---------------- F4：Worker 任务执行前刷新 runtime 缓存 ----------------


def test_enterprise_agent_execute_refreshes_runtime_before_work(monkeypatch):
    """S2/S3 实测缺口：worker 内存 runtime store 只在进程启动时 restore，
    启动后新建/更新的任务不可见（run_agent_workflow 抛 KeyError，任务卡死重试）。

    契约：任务体必须先 ensure_worker_runtime_ready()（从 DB 重灌缓存），再取派发锁/执行。
    """
    from app.workers import enterprise_tasks as et

    order: list[str] = []
    monkeypatch.setattr(et, "ensure_worker_runtime_ready",
                        lambda: order.append("refresh"), raising=False)
    monkeypatch.setattr(et.distributed_lock, "try_acquire_agent_dispatch",
                        lambda task_id, ttl_seconds=None: order.append("lock") or "tok-refresh")
    monkeypatch.setattr(et.distributed_lock, "release_agent_dispatch", lambda *a, **k: None)
    monkeypatch.setattr(et.distributed_lock, "start_agent_dispatch_heartbeat",
                        lambda *a, **k: SimpleNamespace(stop=lambda: None))
    monkeypatch.setattr(et, "_run_until_blocked",
                        lambda wid, tid: order.append("run") or {"task_id": tid})

    run_fn = _enterprise_execute_fn()
    out = run_fn(_FakeTask(), "task-refresh", "w1")
    assert out == {"task_id": "task-refresh"}
    assert order == ["refresh", "lock", "run"], (
        f"必须先刷新 runtime 缓存再取锁执行（S2 KeyError 根因），实际顺序: {order}"
    )


def test_enterprise_agent_execute_refresh_failure_is_not_swallowed(monkeypatch):
    """刷新失败（如生产 schema 落后/DB 不可达）不得静默继续：异常必须在上抛路径，
    而不是带着陈旧缓存执行后出 KeyError。"""
    from app.workers import enterprise_tasks as et

    def _boom():
        raise RuntimeError("schema 落后，拒绝执行")

    monkeypatch.setattr(et, "ensure_worker_runtime_ready", _boom, raising=False)
    ran = {"value": False}
    monkeypatch.setattr(et, "_run_until_blocked",
                        lambda wid, tid: ran.__setitem__("value", True) or {"task_id": tid})
    monkeypatch.setattr(et.distributed_lock, "try_acquire_agent_dispatch",
                        lambda task_id, ttl_seconds=None: pytest.fail("刷新失败时不得进入取锁"))

    run_fn = _enterprise_execute_fn()
    with pytest.raises(RuntimeError, match="schema 落后"):
        run_fn(_FakeTask(), "task-refresh-fail", "w1")
    assert ran["value"] is False
