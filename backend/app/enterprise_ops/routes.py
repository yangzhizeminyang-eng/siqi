"""企业版 E5-01 运维/设置状态接口（只返回非敏感摘要，供前端运维页展示）。

不返回真实密钥/Token/完整环境变量；milvus 仅暴露 host（去协议/端口拆分后的主机名）。
"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends

from app.tenancy.context import context_for_current_user
from app.tenancy.permissions import ORG_MANAGE, require_tenant_permission
from app.enterprise_ops.metrics import metrics_text

router = APIRouter(prefix="/api/enterprise/ops", tags=["enterprise-ops"])


def _current_user_dependency(agent_session: Annotated[str | None, Cookie()] = None):
    from app import main

    return main.get_current_user(agent_session)


CurrentUser = Annotated[object, Depends(_current_user_dependency)]


def _milvus_host(uri: str) -> str:
    """只暴露主机名（去 scheme:// 与端口），避免回显完整连接串。"""
    uri = (uri or "").strip()
    if not uri:
        return ""
    if "://" in uri:
        uri = uri.split("://", 1)[1]
    host = uri.split("/")[0].split(":")[0]
    return host or ""


def _alembic_current() -> str:
    try:
        from alembic.config import Config as AlembicConfig
        from alembic.script import ScriptDirectory
        from app.db.session import get_engine

        cfg = AlembicConfig("alembic.ini")
        script = ScriptDirectory.from_config(cfg)
        with get_engine().connect() as conn:
            current = conn.exec_driver_sql("SELECT version_num FROM alembic_version").fetchone()
        current_rev = current[0] if current else ""
        head = script.get_current_head()
        return f"{current_rev or '(未迁移)'} / head={head}"
    except Exception:  # noqa: BLE001 无 DB/测试环境
        return "(n/a)"


@router.get("/metrics")
def ops_metrics():
    """E6-01 P4：Prometheus 文本指标（非敏感聚合）。"""
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(metrics_text(), media_type="text/plain; version=0.0.4")


@router.get("/status")
def ops_status(user: CurrentUser):
    ctx = context_for_current_user()
    require_tenant_permission(ctx, ORG_MANAGE)
    from app import main
    from app.core.runtime_config import load_runtime_settings

    s = load_runtime_settings()
    deps = {}
    try:
        resp = main.api_health() if hasattr(main, "api_health") else None
        deps = dict((resp or {}).get("dependencies", {})) if isinstance(resp, dict) else {}
    except Exception:  # noqa: BLE001
        pass

    data = {
        "tenant_id": str(getattr(ctx, "tenant_id", "") or "tenant_default"),
        "app_env": s.app_env,
        "app_name": getattr(s, "app_name", "") or "",
        "use_sqlalchemy": bool(getattr(s, "use_sqlalchemy", False)),
        "migration": _alembic_current(),
        "rag": {
            "retrieval_mode": s.rag_retrieval_mode,
            "symbol_boost": float(getattr(s, "rag_symbol_boost", 0.0)),
            "rerank_provider": s.rag_rerank_provider,
            "rerank_enabled": bool(getattr(s, "rag_rerank_enabled", False)),
            "cross_encoder_enabled": bool(getattr(s, "rag_cross_encoder_enabled", False)),
            "vector_top_k": s.rag_vector_top_k,
            "bm25_top_k": s.rag_bm25_top_k,
            "final_top_k": s.rag_final_top_k,
        },
        "milvus": {
            "use_milvus": bool(getattr(s, "use_milvus", False)),
            "collection_prefix": (os.environ.get("MILVUS_COLLECTION_PREFIX", "") or ""),
            "host": _milvus_host(getattr(s, "milvus_uri", "") or ""),
        },
        "counts": {"repositories": len(main.REPOSITORIES), "indexes": len(main.REPOSITORY_INDEXES)},
        "dependencies": deps,
    }
    return {"code": 0, "message": "success", "data": data}
