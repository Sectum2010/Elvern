from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from ..auth import (
    CurrentHeartbeatUser,
    CurrentUser,
    authenticate_user,
    clear_session_cookie,
    create_session,
    destroy_session,
    resolve_client_ip,
    set_session_cookie,
)
from ..models import AuthenticatedUser
from ..services.audit_service import log_audit_event
from ..schemas import AuthLoginRequest, AuthUserEnvelope, MessageResponse


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=AuthUserEnvelope)
def login(payload: AuthLoginRequest, request: Request, response: Response) -> AuthUserEnvelope:
    settings = request.app.state.settings
    rate_limiter = request.app.state.login_rate_limiter
    rate_limit_key = f"{resolve_client_ip(request)}:{payload.username.strip().lower()}"
    retry_after = rate_limiter.check(rate_limit_key)
    if retry_after:
        log_audit_event(
            settings,
            action="auth.login",
            outcome="failure",
            username=payload.username.strip(),
            ip_address=resolve_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            details={"reason": "rate_limited", "retry_after": retry_after},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many login attempts. Try again in {retry_after} seconds.",
        )

    user, failure_reason = authenticate_user(settings, payload.username.strip(), payload.password)
    if user is None:
        if failure_reason == "disabled":
            log_audit_event(
                settings,
                action="auth.login",
                outcome="failure",
                username=payload.username.strip(),
                ip_address=resolve_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                details={"reason": "account_disabled"},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account has been disabled",
            )
        log_audit_event(
            settings,
            action="auth.login",
            outcome="failure",
            username=payload.username.strip(),
            ip_address=resolve_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            details={"reason": "invalid_credentials"},
        )
        lockout = rate_limiter.register_failure(rate_limit_key)
        message = "Invalid username or password"
        if lockout:
            message = f"Too many login attempts. Try again in {lockout} seconds."
            status_code = status.HTTP_429_TOO_MANY_REQUESTS
        else:
            status_code = status.HTTP_401_UNAUTHORIZED
        raise HTTPException(status_code=status_code, detail=message)

    rate_limiter.clear(rate_limit_key)
    token = create_session(
        settings,
        user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    set_session_cookie(response, settings, token)
    log_audit_event(
        settings,
        action="auth.login",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return AuthUserEnvelope(
        user={
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "enabled": user.enabled,
            "assistant_beta_enabled": user.assistant_beta_enabled,
            "session_id": None,
        }
    )


@router.post("/logout", response_model=MessageResponse)
def logout(request: Request, response: Response, user: AuthenticatedUser = CurrentUser) -> MessageResponse:
    settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie_name)
    destroy_session(settings, token)
    clear_session_cookie(response, settings)
    log_audit_event(
        settings,
        action="auth.logout",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        session_id=user.session_id,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return MessageResponse(message="Logged out")


@router.get("/me", response_model=AuthUserEnvelope)
def me(user: AuthenticatedUser = CurrentUser) -> AuthUserEnvelope:
    return AuthUserEnvelope(
        user={
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "enabled": user.enabled,
            "assistant_beta_enabled": user.assistant_beta_enabled,
            "session_id": user.session_id,
        }
    )


@router.post("/heartbeat", response_model=MessageResponse)
def heartbeat(user: AuthenticatedUser = CurrentHeartbeatUser) -> MessageResponse:
    del user
    return MessageResponse(message="Session heartbeat recorded")
