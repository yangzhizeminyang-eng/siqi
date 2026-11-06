"""Celery 任务入口：把长链路（索引/Embedding/Agent/测试/同步）拆出 HTTP 线程，交给 Worker 执行。

关键点：
- Worker 不经过 FastAPI lifespan：每个任务开头调用 `ensure_worker_runtime_ready()`
  建 schema 并从 DB 恢复业务运行时缓存（任务/团队需求/索引/审计等），因此真实实体任务不再 missing/404。
- 异常序列化：worker 边界把 FastAPI HTTPException / 任意异常转为可 pickle 的 `WorkerTaskError`，
  不再因 HTTPException 不可序列化导致 worker 崩溃/exit 127。
- 失败重试语义：非最后尝试只记可恢复态（retry_count/failure_reason/next_action），不置业务终态；
  最后尝试才写终态（暂 task FAILED / 需求 SYNC_FAILED / 索引 FAILED）+ 审计。
- eager/测试：未配置 broker 时 task_always_eager 同步执行（开发/测试降级）。
"""

from __future__ import annotations

import logging

from app.workers.celery_app import celery_app
from app.workers.runtime import ensure_worker_runtime_ready

logger = logging.getLogger("backend.celery")

class WorkerTaskError(Exception):
    """Celery 任务内可序列化业务错误：HTTPException 等在 worker 边界须转成它，避免不可 pickle。"""

    def __init__(self, message: str, *, status_code: int | None = None, error_code: str | None = None, resource_id: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.resource_id = resource_id


# 长任务统一失败重试策略：指数退避，最多 3 次；WorkerTaskError / HTTPException 等异常均走 autoretry
_TASK_RETRY = dict(
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
)


def _to_worker_error(error: Exception) -> WorkerTaskError:
    if isinstance(error, WorkerTaskError):
        return error
    try:
        from fastapi import HTTPException

        if isinstance(error, HTTPException):
            detail = error.detail if isinstance(error.detail, dict) else {}
            return WorkerTaskError(
                str(detail.get("message") or error.detail or "任务失败"),
                status_code=error.status_code,
                error_code=str(detail.get("code") or detail.get("error_type") or "HTTP_ERROR"),
                resource_id=None,
            )
    except Exception:
        pass
    return WorkerTaskError(f"{type(error).__name__}: {error}", error_code=type(error).__name__)


def _is_last(self) -> bool:
    return self.request.retries >= self.max_retries


@celery_app.task(name="backend.smoke", bind=True)
def smoke(self, message: str = "ping") -> dict:
    ensure_worker_runtime_ready()
    logger.info("[celery] Worker 消费 backend.smoke(...) message=%s", message)
    return {"message": message, "echo": True}


@celery_app.task(name="backend.rebuild_repository_index", bind=True, **_TASK_RETRY)
def rebuild_repository_index(self, index_id: str) -> dict:
    ensure_worker_runtime_ready()
    self.update_state(state="STARTED", meta={"index_id": index_id})
    from app.main import REPOSITORY_INDEXES, drive_index_to_completion

    try:
        result = drive_index_to_completion(index_id)
    except Exception as error:
        err = _to_worker_error(error)
        _mark_index_retry(REPOSITORY_INDEXES.get(index_id), err, is_last=_is_last(self))
        raise err
    self.update_state(state="SUCCESS", meta={"index_id": index_id, "result": result})
    return {"index_id": index_id, "result": result}


def _mark_index_retry(index, error, *, is_last: bool) -> None:
    from app.main import fail_repository_index

    if index is None:
        return
    if is_last:
        fail_repository_index(index, stage=index.status, message=str(error))
        logger.error("[celery] rebuild_repository_index(%s) 重试耗尽，索引 FAILED", index.id)
    else:
        logger.warning("[celery] rebuild_repository_index(%s) 失败保留进行态待重试：%s", index.id, error)


@celery_app.task(name="backend.run_agent", bind=True, **_TASK_RETRY)
def run_agent(self, task_id: str, token: str | None = None) -> dict:
    ensure_worker_runtime_ready()
    logger.info("[celery] Worker 消费 backend.run_agent(task_id=%s) retry=%s/%s 开始", task_id, self.request.retries, self.max_retries)
    self.update_state(state="STARTED", meta={"task_id": task_id, "retry": self.request.retries})
    from app.agent.workflow import build_task_workflow
    from app.main import TASKS

    workflow = build_task_workflow()
    try:
        if workflow is not None:
            workflow.invoke({"task_id": task_id, "eager": True}, config={"recursion_limit": 64})
        else:
            task = TASKS.get(task_id)
            if task is None:
                raise ValueError(f"任务不存在: {task_id}")
            from app.main import progress_task_automation

            progress_task_automation(task)
    except Exception as error:
        err = _to_worker_error(error)
        task = TASKS.get(task_id)
        if task is not None:
            from app.main import record_agent_attempt_failure

            is_last = _is_last(self)
            record_agent_attempt_failure(task, error, is_last=is_last)
            if is_last:
                logger.error("[celery] run_agent(%s) 重试耗尽，任务标记 FAILED：%s", task_id, error)
            else:
                logger.warning("[celery] run_agent(%s) 第 %s/%s 次失败，保留可恢复态待重试：%s",
                               task_id, self.request.retries + 1, self.max_retries + 1, error)
        raise err
    finally:
        # worker 完成(成功或异常)都按 token CAS 释放派发锁，避免误删后继持有者；TTL 兜底
        if token:
            from app.core.distributed_lock import release_agent_dispatch

            release_agent_dispatch(task_id, token)
    self.update_state(state="SUCCESS", meta={"task_id": task_id})
    logger.info("[celery] Worker 消费 backend.run_agent(%s) 完成", task_id)
    return {"task_id": task_id}


@celery_app.task(name="backend.run_test", bind=True, **_TASK_RETRY)
def run_test(self, task_id: str) -> dict:
    ensure_worker_runtime_ready()
    self.update_state(state="STARTED", meta={"task_id": task_id})
    from app.main import execute_task_tests_worker

    try:
        result = execute_task_tests_worker(task_id)
    except Exception as error:
        err = _to_worker_error(error)
        raise err
    self.update_state(state="SUCCESS", meta={"task_id": task_id, "result": result})
    return {"task_id": task_id, "result": result}


@celery_app.task(name="backend.sync_gitee_request", bind=True, **_TASK_RETRY)
def sync_gitee_request(self, request_id: str) -> dict:
    ensure_worker_runtime_ready()
    logger.info("[celery] Worker 消费 backend.sync_gitee_request(request_id=%s) 开始", request_id)
    self.update_state(state="STARTED", meta={"request_id": request_id, "retry": self.request.retries})
    from app.main import sync_request_to_gitee_worker, TEAM_REQUESTS

    try:
        result = sync_request_to_gitee_worker(request_id)
    except Exception as error:
        err = _to_worker_error(error)
        _mark_request_retry(TEAM_REQUESTS.get(request_id), err, is_last=_is_last(self), request_id=request_id)
        raise err
    logger.info("[celery] backend.sync_gitee_request(%s) 完成 -> %s", request_id, result.get("status"))
    self.update_state(state="SUCCESS", meta={"request_id": request_id, "result": result})
    return {"request_id": request_id, "result": result}


def _mark_request_retry(record, error, *, is_last: bool, request_id: str) -> None:
    import datetime as _dt

    from app.main import TEAM_REQUESTS, _persist_team_request

    if record is None:
        return
    now = _dt.datetime.now(_dt.timezone.utc)
    if is_last:
        failed = record.model_copy(update={
            "sync_status": "SYNC_FAILED", "failure_reason": str(error), "manual_reason": "gitee_sync_failed", "updated_at": now,
        })
        TEAM_REQUESTS[record.id] = failed
        _persist_team_request(failed)
        logger.error("[celery] sync_gitee_request(%s) 重试耗尽，需求 SYNC_FAILED", request_id)
    else:
        recover = record.model_copy(update={
            "failure_reason": f"Worker 可重试：{error}", "manual_reason": None, "updated_at": now,
        })
        TEAM_REQUESTS[record.id] = recover
        _persist_team_request(recover)
        logger.warning("[celery] sync_gitee_request(%s) 第 %s 次失败，保留 SYNCING 可恢复态待重试", request_id, "")


@celery_app.task(name="backend.create_task_from_team_request", bind=True, **_TASK_RETRY)
def create_task_from_team_request(self, request_id: str) -> dict:
    ensure_worker_runtime_ready()
    logger.info("[celery] Worker 消费 backend.create_task_from_team_request(request_id=%s) 开始", request_id)
    self.update_state(state="STARTED", meta={"request_id": request_id})
    from app.main import create_task_from_team_request_worker, TEAM_REQUESTS

    try:
        result = create_task_from_team_request_worker(request_id)
    except Exception as error:
        err = _to_worker_error(error)
        _mark_request_retry(TEAM_REQUESTS.get(request_id), err, is_last=_is_last(self), request_id=request_id)
        raise err
    logger.info("[celery] backend.create_task_from_team_request(%s) 完成 -> %s", request_id, result.get("status"))
    self.update_state(state="SUCCESS", meta={"request_id": request_id, "result": result})
    return {"request_id": request_id, "result": result}

@celery_app.task(name="backend.index_historical_summary", bind=True)
def index_historical_summary(self, summary_id: str) -> dict:
    """DEV-021 历史任务摘要异步向量索引（Worker 消费；失败在索引记录，不抛错回主链）。"""
    ensure_worker_runtime_ready()
    logger.info("[celery] backend.index_historical_summary(summary_id=%s) 开始", summary_id)
    from app.services import history_service

    rec = history_service.index(summary_id)
    logger.info("[celery] backend.index_historical_summary(%s) 完成 -> %s", summary_id, getattr(rec, "index_status", "?"))
    self.update_state(state="SUCCESS", meta={"summary_id": summary_id, "status": getattr(rec, "index_status", "?")})
    return {"summary_id": summary_id, "status": getattr(rec, "index_status", "?")}


def _retry_if_busy(task, result: dict) -> None:
    """busy 不能 ack：按剩余租约重试，给崩溃 Worker 遗留的 run 一个接管窗口。"""
    if result.get("status") != "busy":
        return
    delay = max(1, int(result.get("retry_after_seconds") or 30))
    raise task.retry(
        countdown=delay,
        max_retries=task.max_retries,
        exc=RuntimeError(f"rag evaluation run busy; retry after {delay}s"),
    )


@celery_app.task(name="backend.run_rag_evaluation", bind=True, **_TASK_RETRY)
def run_rag_evaluation(self, run_id: str) -> dict:
    """OPT-08 在线评测 Worker 入口：推进 run 至终态。

    run_evaluation 内部把失败落到 run + 失败审计，不重抛（重试=人工重新触发），
    因此这里正常返回终态即可；ensure_worker_runtime_ready 保证持久化环境下能重建上下文。
    busy 是唯一例外：按剩余租约重试，避免崩溃 Worker 的 run 永久停在 running。
    """
    ensure_worker_runtime_ready()
    self.update_state(state="STARTED", meta={"run_id": run_id, "retry": self.request.retries})
    from app.enterprise_rag.eval_service import run_evaluation

    result = run_evaluation(run_id)
    _retry_if_busy(self, result)
    logger.info("[celery] backend.run_rag_evaluation(%s) 完成 -> %s", run_id, result.get("status"))
    self.update_state(state="SUCCESS", meta={"run_id": run_id, "status": result.get("status")})
    return {"run_id": run_id, "status": result.get("status")}
