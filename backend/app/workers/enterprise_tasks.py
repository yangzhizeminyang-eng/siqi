"""企业版 E4-02 Celery worker 任务：接入 AgentExecutorRegistry 驱动多 Agent 协作（v2）。

- 只驱动 workflow_version>=2 的任务；v1 延续既有 run_agent/run_test（团队链）。
- 沿用 RUN_ALEMBIC 单实例迁移语义与 agent_run:{task_id} 分派锁防并发重复执行。
- 每个任务开头 ensure_worker_runtime_ready()：Worker 不过 FastAPI lifespan，
  内存 runtime store 只在进程启动时 restore 一次；不刷新则「启动后新建/更新的任务」
  不可见（OPT-13 S2 实测 KeyError）。与 app/workers/tasks.py 各任务同一约定。
"""

from __future__ import annotations

import logging

from celery.exceptions import MaxRetriesExceededError

from app.workers.celery_app import celery_app
from app.workers.runtime import ensure_worker_runtime_ready
from app.core import distributed_lock

logger = logging.getLogger("backend.enterprise_tasks")


def _build_registry():
    from app.enterprise_runtime.agents.registry import AgentExecutorRegistry
    from app.enterprise_runtime.orchestrator import agent_audit_cb
    return AgentExecutorRegistry(audit=agent_audit_cb())


def _run_until_blocked(worker_id: str, task_id: str) -> dict:
    """连续推进 v2 多 Agent：一直执行 Agent 直到需要审批/人工/完成（constraint #8）。

    由 LangGraph 决策驱动；review/test 回退、agent 推进都在单次投递内持续，
    到 approval_wait/human_wait/succeeded 才停。重复投递由 step 幂等 claim 去重。
    """
    from app.enterprise_runtime import store as rstore
    from app.enterprise_runtime import orchestrator as orch
    from app.enterprise_runtime.models import RuntimeTaskStatus
    from app.enterprise_runtime.agents.registry import AgentExecutorRegistry
    from app.core.runtime_config import load_runtime_settings

    reg = AgentExecutorRegistry(audit=orch.agent_audit_cb())
    blocked = {RuntimeTaskStatus.WAITING_APPROVAL, RuntimeTaskStatus.WAITING_HUMAN,
               RuntimeTaskStatus.SUCCEEDED, RuntimeTaskStatus.FAILED, RuntimeTaskStatus.CANCELLED}
    executed_agents = []
    for _ in range(60):
        task = orch.run_agent_workflow(worker_id, task_id, reg)
        if task is None:
            break
        executed_agents.append(getattr(task, "current_step", None))
        if task.status in blocked:
            break
    task = rstore.get(task_id) if rstore.get(task_id) else task
    return {
        "task_id": task_id,
        "status": task.status.value if hasattr(task, "status") else "unknown",
        "current_step": getattr(task, "current_step", None),
        "completed_steps": list(getattr(task, "completed_steps", []) or []),
        "approval_state": getattr(task, "approval_state", ""),
        "executed_agents": [a for a in executed_agents if a] or None,
    }


@celery_app.task(name="enterprise.agent_execute", bind=True, max_retries=2)
def enterprise_agent_execute(self, task_id: str, worker_id: str | None = None) -> dict:
    """执行 v2 多 Agent 至停机点（幂等分派；重复投递经幂等键去重，不产生重复 AgentRun）。

    派发锁竞争语义（OPT-13）：
    - 持锁期间后台心跳按 TTL 续约，崩溃后锁在 ≤1 个 TTL 内自动过期；
    - 锁被持有（重复投递/其他 Worker 在途）不静默丢弃消息，改为有界重试覆盖 ≥1 个 TTL 窗口：
      持有者正常完成则释放锁、重试消息接棒（空跑幂等）；持有者崩溃则重试消息接棒后
      回收其遗留的 RUNNING 步骤（claim_next_step 内完成）；
    - 重试耗尽说明持有者持续存活（心跳一直续约）→ 返回 skipped，由持有者完成工作。

    缓存刷新语义（OPT-13 F4）：先 ensure_worker_runtime_ready() 再取锁。
    刷新失败（生产 schema 落后/DB 不可达）直接上抛，由 Celery 暴露失败，
    绝不带着陈旧内存缓存继续执行。
    """
    ensure_worker_runtime_ready()
    wid = worker_id or self.request.hostname or "worker"
    ttl = distributed_lock.enterprise_dispatch_ttl_seconds()
    token = distributed_lock.try_acquire_agent_dispatch(task_id, ttl_seconds=ttl)
    if token is None:
        try:
            raise self.retry(countdown=distributed_lock.enterprise_dispatch_retry_countdown(),
                             max_retries=distributed_lock.enterprise_dispatch_retry_budget())
        except MaxRetriesExceededError:
            logger.warning(
                "[enterprise.agent_execute] task=%s 派发锁重试耗尽（持有者持续存活），交由持有者完成",
                task_id)
            return {"task_id": task_id, "skipped": True, "reason": "dispatch-lock-busy"}
    heartbeat = distributed_lock.start_agent_dispatch_heartbeat(task_id, token, ttl_seconds=ttl)
    try:
        return _run_until_blocked(wid, task_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("[enterprise.agent_execute] task=%s err=%s", task_id, e)
        raise self.retry(exc=e, countdown=5) from e
    finally:
        heartbeat.stop()
        distributed_lock.release_agent_dispatch(task_id, token)


def submit_agent_workflow(task_id: str) -> dict:
    """企业版创建 v2 Runtime 任务后自动投递：推给 Celery worker 连续推进（生产入口）。

    无 broker（task_always_eager）则内联连续执行；返回投递结果。
    """
    from app.enterprise_runtime import store as rstore
    task = rstore.get(task_id)
    if task is None:
        raise KeyError(task_id)
    if getattr(task, "workflow_version", 1) < 2:
        raise ValueError("submit_agent_workflow 仅用于 workflow_version>=2")
    result = enterprise_agent_execute.delay(task_id=task_id)
    return {"task_id": task_id, "dispatched": True, "celery_result_id": getattr(result, "id", None)}