"""OPT-08 RAG 在线评测入口：授权触发异步评测 + 状态查询 + 报告查看（报告复用 E3-08 接口）。

边界（与验收口径逐条对应）：
- 触发：必须持有 rag:evaluate（developer/reviewer/auditor 403 + 审计）；状态/报告查看复用 rag:search；
- 默认隔离：execution_scope=isolated 仅允许内存态运行（USE_SQLALCHEMY=false）且强制内存种子
  （E3-05 脱敏基线），不碰运行态数据；持久化环境必须显式走 production；
- 生产门：execution_scope=production 需要 confirm_production=true 显式确认，未确认 422 + 拒绝审计、
  不落 run；确认后按 require_milvus 语义执行 —— Milvus 不可用 → blocked（绝不静默回退冒称 verified）；
- 异步：Celery 可用时只入队（queued），由 Worker 推进；否则 eager 内联执行同一段工作函数；
- 失败：评测异常 → run failed + 错误文本脱敏 + 失败审计；报告实文件落 REPORTS_DIR（供 /reports 回读）。
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException, status

from app.core.clock import utcnow as audit_now

from app.enterprise_rag import eval_store
from app.enterprise_rag.eval_store import EvaluationRun
from app.enterprise_rag.evaluation import _in_memory_mode, evaluate_dataset, load_cases, write_report
from app.enterprise_rag.models import EnterpriseRagEvaluationPayload
from app.tenancy.permissions import RAG_EVALUATE, RAG_SEARCH, require_tenant_permission
from evaluation.enterprise_rag.seed import seed_baseline as _seed_baseline

logger = logging.getLogger("backend.enterprise_rag.eval")

_DATASET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*\.(jsonl|json)$")
_SUMMARY_KEYS = (
    "total_cases", "passed_cases", "success_cases",
    "recall_at_k", "precision_at_k", "mrr", "ndcg_at_k",
    "latency_p50_ms", "latency_p95_ms",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                         detail={"code": 422, "message": message, "data": None})


def _not_found(message: str = "评测运行不存在") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                         detail={"code": 404, "message": message, "data": None})


# ---------------- 数据集白名单（backend/evaluation/enterprise_rag/） ----------------
def _dataset_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "evaluation" / "enterprise_rag"


def resolve_dataset(name: str) -> Path:
    """评测集必须来自白名单目录的文件名（无路径分隔符、无穿越、必须存在）。"""
    raw = (name or "").strip()
    if not _DATASET_NAME_RE.match(raw):
        raise _unprocessable("评测集名称非法：仅允许 evaluation/enterprise_rag 下的文件名")
    base = _dataset_dir().resolve()
    target = (base / raw).resolve()
    if not str(target).startswith(str(base)):
        raise _unprocessable("非法的评测集路径")
    if not target.is_file():
        raise _unprocessable(f"评测集不存在: {raw}")
    return target


def _reports_dir() -> Path:
    from app.enterprise_rag import routes as rag_routes

    return Path(rag_routes.REPORTS_DIR)


# ---------------- 审计（脱敏；失败不阻断主流程） ----------------
def _audit(ctx, action: str, resource_id: str, summary: str) -> None:
    from app import main

    try:
        event = main.AuditEventRecord(
            id=f"audit_{uuid.uuid4().hex[:16]}",
            event_type=f"tenant.rag.eval.{action}",
            tenant_id=ctx.tenant_id,
            resource_type="rag_evaluation",
            resource_id=(resource_id or "")[:80],
            actor_id=getattr(ctx, "user_id", ""),
            actor_name="",
            summary=summary[:500],
            diff_summary=[],
            created_at=audit_now(),
            execution_step_ids=[],
        )
        main.AUDIT_EVENTS[event.id] = event
        from app.tenancy.context import _persist_audit

        _persist_audit(event)
    except Exception:  # noqa: BLE001 审计失败不阻断主流程
        logger.warning("[rag-eval] 审计写入失败 action=%s run=%s", action, resource_id, exc_info=True)


# ---------------- 运行上下文（Worker 无请求上下文，按触发时角色重建） ----------------
def _execution_ctx(run: EvaluationRun):
    from app.tenancy import store as tenancy_store
    from app.tenancy.models import TenantContext
    from app.tenancy.permissions import permissions_for

    tenant = tenancy_store.get_tenant(run.tenant_id)
    role = run.requested_role or "developer"
    return TenantContext(
        user_id=run.requested_by or "system:rag-eval",
        tenant_id=run.tenant_id,
        tenant_name=getattr(tenant, "name", "") or run.tenant_id,
        role=role,
        permissions=permissions_for(role),
    )


def _summary_brief(summary: dict | None) -> dict:
    summary = summary or {}
    return {key: summary.get(key, 0) for key in _SUMMARY_KEYS}


# ---------------- 触发 ----------------
def trigger_evaluation(ctx, payload: EnterpriseRagEvaluationPayload) -> dict:
    require_tenant_permission(ctx, RAG_EVALUATE)
    resolve_dataset(payload.dataset)

    if payload.execution_scope == "isolated" and not _in_memory_mode():
        raise _unprocessable(
            "隔离评测仅支持内存态运行（USE_SQLALCHEMY=false）；持久化环境请使用 production 并显式确认")
    if payload.execution_scope == "production" and not payload.confirm_production:
        from app.tenancy.context import deny_and_audit

        deny_and_audit(ctx, "rag.eval.production_confirmation_required",
                       "生产评测需显式确认（默认仅隔离数据）",
                       resource_type="rag_evaluation", resource_id=payload.dataset)
        raise _unprocessable("生产评测需显式确认：confirm_production=true（默认仅隔离数据）")

    run = eval_store.create(EvaluationRun(
        run_id=f"rageval_{uuid.uuid4().hex[:12]}",
        tenant_id=ctx.tenant_id,
        dataset=payload.dataset,
        retrieval_mode=payload.retrieval_mode,
        top_k=payload.top_k,
        execution_scope=payload.execution_scope,
        requested_by=getattr(ctx, "user_id", ""),
        requested_role=str(getattr(ctx, "role", "") or "developer"),
    ))

    from app.workers.dispatch import enqueue_or_run
    from app.workers.tasks import run_rag_evaluation as _task

    label = f"run_rag_evaluation:{run.run_id}"
    try:
        dispatch = enqueue_or_run(
            _task, kwargs={"run_id": run.run_id},
            run_inline=lambda: run_evaluation(run.run_id), label=label,
        )
    except Exception:  # noqa: BLE001 派发失败（如 broker 抖动）→ 内联兜底，不丢触发
        logger.exception("[rag-eval] Celery 派发失败，回退内联执行 run=%s", run.run_id)
        run_evaluation(run.run_id)
        dispatch = {"enqueued": False, "mode": "eager(dispatch-fallback)", "label": label}

    _audit(ctx, "triggered", run.run_id,
           f"在线评测触发 scope={payload.execution_scope} dataset={payload.dataset} "
           f"mode={payload.retrieval_mode} dispatch={dispatch.get('mode')}")

    fresh = eval_store.get(run.run_id) or run
    return {
        "run": fresh.to_public(),
        "dispatch": {
            "mode": dispatch.get("mode", ""),
            "enqueued": bool(dispatch.get("enqueued")),
            "label": dispatch.get("label", label),
        },
    }


# ---------------- Worker / 内联执行体（同一段工作函数） ----------------
def run_evaluation(run_id: str) -> dict:
    """推进 run 至终态；重复 delivery/崩溃接管由 claim_for_execution 统一裁决。"""
    outcome, run = eval_store.claim_for_execution(run_id)
    if outcome == "missing" or run is None:
        return {"run_id": run_id, "status": "missing"}
    if outcome == "terminal":
        logger.info("[rag-eval] run=%s 已终态，跳过重复执行", run_id)
        return run.to_public()
    if outcome == "busy":
        # 另一个 Worker 正在租约内执行。重复 delivery 不能直接 ack：
        # 若原 Worker 已崩溃，消息会丢失且 run 永久停在 running。返回剩余租约，
        # 由 Celery 任务按该时间重试，租约过期后即可接管。
        started = run.started_at or _now()
        remaining = (started + timedelta(seconds=eval_store.lease_seconds()) - _now()).total_seconds()
        retry_after = max(1, int(remaining) + 1)
        logger.info("[rag-eval] run=%s claim=busy，%ss 后重试", run_id, retry_after)
        return {"run_id": run_id, "status": "busy", "retry_after_seconds": retry_after}

    ctx = None
    trace_token = None
    try:
        from app.enterprise_runtime import observability as obs

        trace_token = obs.bind_trace(trace_id=run.run_id, tenant_id=run.tenant_id, run_id=run.run_id)
        ctx = _execution_ctx(run)
        if run.execution_scope == "isolated":
            # 持久化业务运行时下的直建 isolated run 必须 fail-closed，绝不读取运行态数据。
            if not _in_memory_mode():
                raise RuntimeError("隔离评测仅支持内存态运行（USE_SQLALCHEMY=false）")
            _seed_baseline()
        with obs.span(
            "worker", "rag.evaluation", trace_id=run.run_id, tenant_id=run.tenant_id,
            attributes={
                "run_id": run.run_id,
                "execution_scope": run.execution_scope,
                "retrieval_mode": run.retrieval_mode,
                "top_k": run.top_k,
                "attempt": run.attempts,
            },
        ):
            cases = load_cases(str(resolve_dataset(run.dataset)))
            report = evaluate_dataset(
                ctx, cases,
                top_k=run.top_k, retrieval_mode=run.retrieval_mode,
                require_milvus=(run.execution_scope == "production"),
            )
            report_name = f"online_{run.run_id}.json"
            reports_dir = _reports_dir()
            reports_dir.mkdir(parents=True, exist_ok=True)
            write_report(report, str(reports_dir / report_name))

            validation = str(report.get("validation_status") or "")
            final_status = "blocked" if validation == "blocked" else "succeeded"
            eval_store.update(run_id, status=final_status, finished_at=_now(),
                              report_name=report_name, validation_status=validation,
                              summary=_summary_brief(report.get("summary")))
        _audit(ctx, "completed", run_id,
               f"在线评测完成 status={final_status} validation={validation} report={report_name}")
    except Exception as error:  # noqa: BLE001 失败落 run + 审计，不重抛（重试=人工重触发）
        from app.enterprise_runtime.agents.tools import redact_secrets

        message = redact_secrets(f"{type(error).__name__}: {error}")[:300]
        try:
            eval_store.update(run_id, status="failed", finished_at=_now(), error=message)
        except Exception:  # noqa: BLE001 极端 DB 故障：至少留下日志，不把异常吞成 queued
            logger.exception("[rag-eval] run=%s 失败终态写入失败", run_id)
        if ctx is None:
            try:
                ctx = _execution_ctx(run)
            except Exception:  # noqa: BLE001 上下文重建失败也要留下失败记录
                ctx = None
        if ctx is not None:
            _audit(ctx, "failed", run_id, f"在线评测失败: {message}")
    finally:
        if trace_token is not None:
            from app.enterprise_runtime import observability as obs

            obs.clear_trace(trace_token)

    fresh = eval_store.get(run_id)
    return fresh.to_public() if fresh else {"run_id": run_id, "status": "missing"}


# ---------------- 查询（rag:search） ----------------
def get_run(ctx, run_id: str) -> dict:
    require_tenant_permission(ctx, RAG_SEARCH)
    run = eval_store.get(run_id)
    if run is None or run.tenant_id != ctx.tenant_id:
        raise _not_found()
    return run.to_public()


def list_runs(ctx, *, limit: int = 50) -> list[dict]:
    require_tenant_permission(ctx, RAG_SEARCH)
    return [run.to_public() for run in eval_store.list_by_tenant(ctx.tenant_id, limit=limit)]
