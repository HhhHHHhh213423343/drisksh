from __future__ import annotations

from typing import Any
import time

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.auth import SESSION_COOKIE, create_session, verify_password


router = APIRouter(prefix="/auth", tags=["auth"])
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=512)


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    settings = get_settings()
    client_key = (
        request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        or (request.client.host if request.client else "unknown")
    )
    now = time.time()
    recent = [
        attempt
        for attempt in LOGIN_ATTEMPTS.get(client_key, [])
        if now - attempt < LOGIN_WINDOW_SECONDS
    ]
    LOGIN_ATTEMPTS[client_key] = recent
    if settings.auth_required and len(recent) >= LOGIN_MAX_FAILURES:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录失败次数过多，请稍后重试。",
        )
    if not settings.auth_required:
        username = payload.username.strip() or "local-dev"
    elif payload.username.strip() != settings.app_username or not verify_password(payload.password):
        LOGIN_ATTEMPTS[client_key] = [*recent, now]
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码不正确。",
        )
    else:
        username = settings.app_username
        LOGIN_ATTEMPTS.pop(client_key, None)
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    response.set_cookie(
        SESSION_COOKIE,
        create_session(username),
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=request.url.scheme == "https" or forwarded_proto == "https",
        samesite="lax",
        path="/",
    )
    return {"authenticated": True, "username": username}


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"authenticated": False}


@router.get("/me")
def me(request: Request) -> dict[str, Any]:
    return {
        "authenticated": True,
        "username": str(getattr(request.state, "user", "local-dev")),
    }
