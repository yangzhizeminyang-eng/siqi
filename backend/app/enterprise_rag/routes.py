"""企业版 E3-01+/E3-08/OPT-08 /api/enterprise/rag 接口：检索 + 报告列表/详情 + 在线评测入口。

所有端点都必须持有 rag:search（后端权威，前端权限仅用于展示/隐藏）；在线评测触发
额外要求 rag:evaluate（OPT-08，消费算力专属授权）。
报告读取仅限 backend/evaluation/enterprise_rag/reports 目录内，做路径穿越防护，
响应只返回脱敏报表字段（无代码正文/token/凭据）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException

from app.enterprise_rag.models import EnterpriseRagEvaluationPayload, EnterpriseRagSearchPayload
from app.enterprise_rag.service import enterprise_rag_search
from app.tenancy.context import context_for_current_user
from app.tenancy.permissions import RAG_SEARCH, require_tenant_permission

router = APIRouter(prefix="/api/enterprise/rag", tags=["enterprise-rag"])

# 企业离线评测报告的规范目录（与 CLI 写出的 reports/ 一致，脱敏 JSON）。
REPORTS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "evaluation" / "enterprise_rag" / "reports"
)

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.\-]+\.json$")


def _current_user_dependency(agent_session: Annotated[str | None, Cookie()] = None):
    """复用 main.get_current_user（含服务端解析激活租户并写入 contextvar）。"""
    from app import main  # 延迟导入避免循环

    return main.get_current_user(agent_session)


CurrentUser = Annotated[object, Depends(_current_user_dependency)]


def _report_meta(report: dict, name: str) -> dict:
    return {
        "name": name,
        "retrieval_mode": report.get("retrieval_mode"),
        "validation_status": report.get("validation_status"),
        "vector_backend": report.get("vector_backend"),
        "milvus_verified": report.get("milvus_verified"),
        "fallback_used": report.get("fallback_used"),
        "needs_improvement": report.get("needs_improvement"),
        "dataset_size": report.get("dataset_size"),
        "commit_sha": report.get("commit_sha"),
        "index_id": report.get("index_id"),
        "generated_at": report.get("generated_at") or report.get("checked_at"),
    }


def _resolve_report_file(name: str) -> Path:
    if not _SAFE_NAME.match(name):
        raise HTTPException(status_code=400, detail="非法的报告名称")
    base = REPORTS_DIR.resolve()
    target = (base / name).resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(status_code=400, detail="非法的报告路径")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="报告不存在")
    return target


def _load_report(name: str) -> dict:
    try:
        with open(_resolve_report_file(name), encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="报告文件损坏") from None


@router.post("/search")
def rag_search(payload: EnterpriseRagSearchPayload, user: CurrentUser):
    ctx = context_for_current_user()
    require_tenant_permission(ctx, RAG_SEARCH)
    data = enterprise_rag_search(ctx, payload)
    return {"code": 0, "message": "success", "data": data}


@router.get("/reports")
def rag_reports(user: CurrentUser):
    ctx = context_for_current_user()
    require_tenant_permission(ctx, RAG_SEARCH)
    if not REPORTS_DIR.is_dir():
        return {"code": 0, "message": "success", "data": {"reports": [], "reports_dir": str(REPORTS_DIR)}}
    items = []
    for path in sorted(REPORTS_DIR.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                report = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        items.append(_report_meta(report, path.name))
    return {"code": 0, "message": "success", "data": {"reports": items, "reports_dir": str(REPORTS_DIR)}}


@router.get("/reports/{name}")
def rag_report_detail(name: str, user: CurrentUser):
    ctx = context_for_current_user()
    require_tenant_permission(ctx, RAG_SEARCH)
    report = _load_report(name)
    projection = {
        **_report_meta(report, name),
        "summary": report.get("summary"),
        "recommended_thresholds": report.get("recommended_thresholds"),
        "mode_comparison": report.get("mode_comparison"),
        "mode_validation": report.get("mode_validation"),
        "metrics_by_tag": report.get("metrics_by_tag"),
        "failed_cases": report.get("failed_cases"),
        "config_snapshot": report.get("config_snapshot"),
        "config_snapshot_sha256": report.get("config_snapshot_sha256"),
        "dataset_sha256": report.get("dataset_sha256"),
        "repository_url": report.get("repository_url"),
    }
    return {"code": 0, "message": "success", "data": projection}


# ---------------- OPT-09 索引生命周期只读接口（rag:search；租户过滤；不含代码正文/凭据） ----------------


@router.get("/indexes")
def rag_indexes(user: CurrentUser):
    from app.enterprise_rag import index_lifecycle

    ctx = context_for_current_user()
    require_tenant_permission(ctx, RAG_SEARCH)
    entries = index_lifecycle.list_by_tenant(ctx.tenant_id)
    return {"code": 0, "message": "success", "data": {"indexes": [e.to_public() for e in entries]}}


@router.get("/indexes/{index_id}")
def rag_index_detail(index_id: str, user: CurrentUser):
    from app.enterprise_rag import index_lifecycle

    ctx = context_for_current_user()
    require_tenant_permission(ctx, RAG_SEARCH)
    entry = index_lifecycle.get(index_id)
    if entry is None or entry.tenant_id != str(ctx.tenant_id):
        # 跨租户与不存在同样 404：不泄漏其它租户的索引存在性
        raise HTTPException(status_code=404, detail={"code": 404, "message": "索引生命周期记录不存在", "data": None})
    return {"code": 0, "message": "success", "data": {"index": entry.to_public()}}


# ---------------- OPT-08 在线评测入口（触发/状态/列表；报告复用上方 /reports 回读） ----------------


@router.post("/evaluations")
def rag_evaluation_trigger(payload: EnterpriseRagEvaluationPayload, user: CurrentUser):
    from app.enterprise_rag import eval_service  # 延迟导入：routes 保持轻量、避免循环

    ctx = context_for_current_user()
    data = eval_service.trigger_evaluation(ctx, payload)
    return {"code": 0, "message": "success", "data": data}


@router.get("/evaluations")
def rag_evaluation_list(user: CurrentUser):
    from app.enterprise_rag import eval_service

    ctx = context_for_current_user()
    runs = eval_service.list_runs(ctx)
    return {"code": 0, "message": "success", "data": {"runs": runs}}


@router.get("/evaluations/{run_id}")
def rag_evaluation_detail(run_id: str, user: CurrentUser):
    from app.enterprise_rag import eval_service

    ctx = context_for_current_user()
    run = eval_service.get_run(ctx, run_id)
    return {"code": 0, "message": "success", "data": {"run": run}}