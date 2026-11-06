"""系统域接口：健康检查（含依赖探活）。

OPT-11：自 app/main.py 迁出（行为不变；路由表由 tests/test_opt11_main_split.py 冻结）。
依赖探活经 ``app.main`` 模块属性在调用时读取，保证测试对
``main.check_milvus_health`` / ``main._check_redis_health`` 的 monkeypatch 继续生效。
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.main import ApiResponse, api_success

router = APIRouter(tags=["system"])


@router.get("/api/health")
def health(response: Response) -> ApiResponse:
    from app import main
    from app.core.runtime_config import load_runtime_settings

    settings = load_runtime_settings()
    db_status = "unknown"
    deps: dict = {}
    try:
        from app.db.session import check_database_health

        db_health = check_database_health()
        db_status = db_health.get("status", "unknown")
        deps["db"] = db_health
    except Exception as error:
        deps["db"] = {"status": "error", "error": str(error)[:300]}
    try:
        deps["milvus"] = main.check_milvus_health()
    except Exception as error:
        deps["milvus"] = {"status": "error", "error": str(error)[:300]}
    if settings.celery_required():
        deps["redis"] = main._check_redis_health()
    required_statuses = [db_status]
    if settings.celery_required():
        required_statuses.extend(
            deps[name].get("status", "unknown")
            for name in ("redis", "milvus")
        )
    ready = all(item in {"ok", "skipped"} for item in required_statuses)
    overall = "ok" if ready else "degraded"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return api_success({"status": overall, "dependencies": deps})
