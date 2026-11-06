"""用户域接口：成员列表与详情。

OPT-11：自 app/main.py 迁出（行为不变；路由表由 tests/test_opt11_main_split.py 冻结）。
``ROLE_PERMISSIONS`` 经 ``app.main`` 模块属性在调用时读取，保持与拆分前一致的
monkeypatch 语义。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.main import ApiResponse, UserRecord, api_success, get_current_user, public_user, require_permission

router = APIRouter(tags=["users"])


@router.get("/api/users")
def list_users(user: Annotated[UserRecord, Depends(get_current_user)]) -> ApiResponse:
    require_permission(user, "user:view")
    from app.db.reads import list_users as read_users

    return api_success([public_user(item) for item in read_users()])


@router.get("/api/users/{user_id}")
def get_user(user_id: str, user: Annotated[UserRecord, Depends(get_current_user)]) -> ApiResponse:
    from app import main

    if "user:view" not in main.ROLE_PERMISSIONS[user.role] and user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": 403, "message": "无权查看该用户", "data": None},
        )
    from app.db.reads import get_user as read_user

    target = read_user(user_id)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 404, "message": "用户不存在", "data": None},
        )
    return api_success(public_user(target))
