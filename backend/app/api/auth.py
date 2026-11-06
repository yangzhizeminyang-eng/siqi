"""认证域接口：登录/登出/当前用户/密码找回与应急恢复。

OPT-11：自 app/main.py 迁出（行为不变；路由表由 tests/test_opt11_main_split.py 冻结）。
``AUTH_RECOVERY_CODE`` 经 ``app.main`` 模块属性在调用时读取，保持与拆分前
（main 全局变量）一致的取值时机。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.main import (
    PASSWORD_RESET_REQUESTS,
    REMEMBERED_SESSION_TTL,
    ROLE_LABELS,
    SESSION_TTL,
    USERS_BY_EMAIL,
    AdminPasswordResetRequest,
    ApiResponse,
    EmergencyPasswordResetRequest,
    ForgotPasswordRequest,
    LoginData,
    LoginRequest,
    PasswordResetRecord,
    UserRecord,
    _persist_password_reset,
    _persist_user,
    api_success,
    clear_session_cookie,
    create_token,
    get_current_user,
    hash_password,
    public_user,
    require_permission,
    set_session_cookie,
)

router = APIRouter(tags=["auth"])


@router.post("/api/auth/login")
def login(payload: LoginRequest, response: Response) -> ApiResponse:
    account = payload.account.strip().lower()
    user = USERS_BY_EMAIL.get(account)
    if not user or not secrets.compare_digest(user.password_hash, hash_password(payload.password)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 401, "message": "账号或密码错误", "data": None},
        )

    ttl = REMEMBERED_SESSION_TTL if payload.remember_me else SESSION_TTL
    expires_at = datetime.now(timezone.utc) + ttl
    token = create_token(user.id, expires_at)
    set_session_cookie(response, token, expires_at, payload.remember_me)
    return api_success(LoginData(user=public_user(user), expires_at=expires_at))


@router.post("/api/auth/logout")
def logout(response: Response) -> ApiResponse:
    clear_session_cookie(response)
    return api_success(message="已退出登录")


@router.get("/api/auth/me")
def me(user: Annotated[UserRecord, Depends(get_current_user)]) -> ApiResponse:
    return api_success(public_user(user))


@router.post("/api/auth/password/forgot")
def forgot_password(payload: ForgotPasswordRequest) -> ApiResponse:
    """提交密码找回申请，由企业管理员在账号管理页处理。"""
    email = payload.email.strip().lower()
    user = USERS_BY_EMAIL.get(email)
    if user:
        existing = next(
            (
                item
                for item in PASSWORD_RESET_REQUESTS.values()
                if item.email == email and item.status == "pending"
            ),
            None,
        )
        if not existing:
            request_id = f"pwd_req_{secrets.token_hex(6)}"
            PASSWORD_RESET_REQUESTS[request_id] = PasswordResetRecord(
                id=request_id,
                email=user.email,
                user_name=user.name,
                role_label=ROLE_LABELS[user.role],
                status="pending",
                requested_at=datetime.now(timezone.utc),
            )
            _persist_password_reset(PASSWORD_RESET_REQUESTS[request_id])
    # 不返回账号是否存在，避免通过找回接口枚举企业账号。
    return api_success(
        {"submitted": True},
        message="申请已提交，请联系企业管理员处理；管理员账号请使用应急恢复",
    )


@router.post("/api/auth/password/emergency-reset")
def emergency_reset_password(payload: EmergencyPasswordResetRequest) -> ApiResponse:
    """管理员失去登录能力时，由部署负责人使用应急码协助恢复。"""
    from app import main

    if not main.AUTH_RECOVERY_CODE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": 503, "message": "平台尚未配置管理员应急恢复码，请联系部署负责人", "data": None},
        )
    account = payload.account.strip().lower()
    user = USERS_BY_EMAIL.get(account)
    if not user or user.role != "admin" or not secrets.compare_digest(payload.recovery_code, main.AUTH_RECOVERY_CODE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": 403, "message": "账号或应急恢复码无效", "data": None},
        )
    user.password_hash = hash_password(payload.new_password)
    return api_success(message="管理员密码已重置，请使用新密码登录")


@router.get("/api/auth/password/requests")
def list_password_reset_requests(user: Annotated[UserRecord, Depends(get_current_user)]) -> ApiResponse:
    require_permission(user, "user:view")
    records = sorted(PASSWORD_RESET_REQUESTS.values(), key=lambda item: item.requested_at, reverse=True)
    return api_success(records)


@router.post("/api/auth/password/requests/{request_id}/reset")
def reset_requested_password(
    request_id: str,
    payload: AdminPasswordResetRequest,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> ApiResponse:
    require_permission(user, "user:view")
    reset_request = PASSWORD_RESET_REQUESTS.get(request_id)
    if not reset_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 404, "message": "找回申请不存在", "data": None},
        )
    if reset_request.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": 409, "message": "该申请已经处理", "data": None},
        )
    target = USERS_BY_EMAIL[reset_request.email]
    target.password_hash = hash_password(payload.new_password)
    now = datetime.now(timezone.utc)
    PASSWORD_RESET_REQUESTS[request_id] = reset_request.model_copy(
        update={"status": "completed", "handled_by": user.name, "handled_at": now}
    )
    _persist_password_reset(PASSWORD_RESET_REQUESTS[request_id])
    _persist_user(target)
    return api_success(message=f"{target.name} 的密码已重置，请通过企业内部渠道告知临时密码")
