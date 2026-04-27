from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from ..auth import (
    build_login_client_bucket,
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
    attempted_username = payload.username.strip()
    client_bucket = build_login_client_bucket(request)
    retry_after = rate_limiter.check(client_bucket)
    if retry_after:
        log_audit_event(
            settings,
            action="auth.login",
            outcome="failure",
            username=attempted_username,
            ip_address=resolve_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            details={
                "reason": "device_rate_limited",
                "retry_after": retry_after,
                "attempted_username": attempted_username,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many login attempts from this device. Try again in {retry_after} seconds.",
        )

    user, failure_reason = authenticate_user(settings, attempted_username, payload.password)
    if user is None:
        if failure_reason == "disabled":
            log_audit_event(
                settings,
                action="auth.login",
                outcome="failure",
                username=attempted_username,
                ip_address=resolve_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                details={"reason": "account_disabled"},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account has been disabled",
            )
        lockout = rate_limiter.register_failure(client_bucket)
        if lockout:
            log_audit_event(
                settings,
                action="auth.login",
                outcome="failure",
                username=attempted_username,
                ip_address=resolve_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                details={
                    "reason": "device_rate_limited",
                    "retry_after": lockout,
                    "attempted_username": attempted_username,
                },
            )
            message = f"Too many login attempts from this device. Try again in {lockout} seconds."
            status_code = status.HTTP_429_TOO_MANY_REQUESTS
        else:
            log_audit_event(
                settings,
                action="auth.login",
                outcome="failure",
                username=attempted_username,
                ip_address=resolve_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                details={"reason": "invalid_credentials"},
            )
            message = "Invalid username or password"
            status_code = status.HTTP_401_UNAUTHORIZED
        raise HTTPException(status_code=status_code, detail=message)

    rate_limiter.clear(client_bucket)
    token = create_session(
        settings,
        user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    request.app.state.scan_service.maybe_refresh_local_library(trigger="login")
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
def me(request: Request, user: AuthenticatedUser = CurrentUser) -> AuthUserEnvelope:
    request.app.state.scan_service.maybe_refresh_local_library(trigger="session")
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
