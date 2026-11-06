"""看板域接口：自动化仪表盘汇总。

OPT-11：自 app/main.py 迁出（行为不变；路由表由 tests/test_opt11_main_split.py 冻结）。
汇总辅助函数定义在 main.py 的 app 初始化之后，故经 ``app.main`` 惰性读取。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.main import (
    ApiResponse,
    UserRecord,
    api_success,
    current_tenant_id,
    get_current_user,
    require_permission,
)

router = APIRouter(tags=["dashboard"])


@router.get("/api/dashboard/automation-summary")
def get_dashboard_automation_summary(
    user: Annotated[UserRecord, Depends(get_current_user)],
    project_id: str | None = Query(default=None),
    project_name: str | None = Query(default=None),
    days: int = Query(default=7, ge=7, le=30),
) -> ApiResponse:
    from app import main
    from app.db import reads

    require_permission(user, "dashboard:view")
    if days not in (7, 30):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": 422, "message": "days 仅支持 7 或 30", "data": None},
        )
    tasks = main._dashboard_task_scope(reads.list_tasks(tenant_id=current_tenant_id()),
                                       project_id=project_id, project_name=project_name)
    return api_success(main._dashboard_summary_payload(user, tasks, trend_days=30 if days == 30 else 7))
