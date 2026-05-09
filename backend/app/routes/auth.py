from __future__ import annotations

import time

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
from ..services.account_access_service import create_password_help_request, create_user_with_invite
from ..services.audit_service import log_audit_event
from ..services.rate_limiter_service import count_recent_failures
from ..services.security_event_service import log_security_event
from ..schemas import AuthLoginRequest, AuthSignupRequest, AuthUserEnvelope, MessageResponse, PasswordHelpRequest


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=AuthUserEnvelope)
def login(payload: AuthLoginRequest, request: Request, response: Response) -> AuthUserEnvelope:
    settings = request.app.state.settings
    ip_rate_limiter = request.app.state.login_ip_rate_limiter
    username_rate_limiter = request.app.state.login_username_rate_limiter
    attempted_username = payload.username.strip()
    ip_address = resolve_client_ip(request)
    user_agent = request.headers.get("user-agent")
    ip_retry_after = ip_rate_limiter.check(ip_address)
    if ip_retry_after:
        log_security_event(
            settings,
            event_kind="login_blocked_ip",
            actor_username=attempted_username,
            ip_address=ip_address,
            user_agent=user_agent,
            details={"remaining_seconds": ip_retry_after},
        )
        log_audit_event(
            settings,
            action="auth.login",
            outcome="failure",
            username=attempted_username,
            ip_address=ip_address,
            user_agent=user_agent,
            details={
                "reason": "ip_rate_limited",
                "retry_after": ip_retry_after,
                "attempted_username": attempted_username,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many login attempts from this IP. Try again in {ip_retry_after} seconds.",
        )
    username_retry_after = username_rate_limiter.check(attempted_username)
    if username_retry_after:
        log_security_event(
            settings,
            event_kind="login_blocked_username",
            actor_username=attempted_username,
            ip_address=ip_address,
            user_agent=user_agent,
            details={"remaining_seconds": username_retry_after},
        )
        log_audit_event(
            settings,
            action="auth.login",
            outcome="failure",
            username=attempted_username,
            ip_address=ip_address,
            user_agent=user_agent,
            details={
                "reason": "username_rate_limited",
                "retry_after": username_retry_after,
                "attempted_username": attempted_username,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many login attempts for this username. Try again in {username_retry_after} seconds.",
        )

    user, failure_reason = authenticate_user(settings, attempted_username, payload.password)
    if user is None:
        user_lockout = username_rate_limiter.register_failure(attempted_username)
        ip_lockout = ip_rate_limiter.register_failure(ip_address)
        security_reason = failure_reason or "invalid_credentials"
        log_security_event(
            settings,
            event_kind="login_failure",
            actor_username=attempted_username,
            ip_address=ip_address,
            user_agent=user_agent,
            details={"reason": security_reason},
        )
        if user_lockout or ip_lockout:
            log_security_event(
                settings,
                event_kind="login_lockout",
                actor_username=attempted_username,
                ip_address=ip_address,
                user_agent=user_agent,
                details={
                    "ip_lockout_seconds": ip_lockout,
                    "username_lockout_seconds": user_lockout,
                },
            )
        if failure_reason == "disabled":
            log_audit_event(
                settings,
                action="auth.login",
                outcome="failure",
                username=attempted_username,
                ip_address=ip_address,
                user_agent=user_agent,
                details={"reason": "account_disabled"},
            )
            if user_lockout or ip_lockout:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many login attempts. Try again in 600 seconds.",
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account has been disabled",
            )
        lockout = max(user_lockout, ip_lockout)
        if lockout:
            reason = "username_rate_limited" if user_lockout >= ip_lockout else "ip_rate_limited"
            log_audit_event(
                settings,
                action="auth.login",
                outcome="failure",
                username=attempted_username,
                ip_address=ip_address,
                user_agent=user_agent,
                details={
                    "reason": reason,
                    "retry_after": lockout,
                    "attempted_username": attempted_username,
                },
            )
            message = f"Too many login attempts. Try again in {lockout} seconds."
            status_code = status.HTTP_429_TOO_MANY_REQUESTS
        else:
            log_audit_event(
                settings,
                action="auth.login",
                outcome="failure",
                username=attempted_username,
                ip_address=ip_address,
                user_agent=user_agent,
                details={"reason": "invalid_credentials" if security_reason == "user_not_found" else security_reason},
            )
            message = "Invalid username or password"
            status_code = status.HTTP_401_UNAUTHORIZED
        raise HTTPException(status_code=status_code, detail=message)

    had_recent_username_failures = count_recent_failures(
        settings,
        bucket_kind="username",
        bucket_key=attempted_username,
        since_unix=time.time() - 86400,
    ) > 0
    ip_rate_limiter.clear(ip_address)
    username_rate_limiter.clear(attempted_username)
    if had_recent_username_failures:
        log_security_event(
            settings,
            event_kind="login_success_after_failures",
            actor_user_id=user.id,
            actor_username=user.username,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    token = create_session(
        settings,
        user,
        ip_address=ip_address,
        user_agent=user_agent,
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
        ip_address=ip_address,
        user_agent=user_agent,
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


@router.post("/signup", response_model=AuthUserEnvelope)
def signup(payload: AuthSignupRequest, request: Request, response: Response) -> AuthUserEnvelope:
    settings = request.app.state.settings
    ip_address = resolve_client_ip(request)
    user_agent = request.headers.get("user-agent")
    user = create_user_with_invite(
        settings,
        username=payload.username,
        password=payload.password,
        confirm_password=payload.confirm_password,
        invite_code=payload.invite_code,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    token = create_session(
        settings,
        user,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    request.app.state.scan_service.maybe_refresh_local_library(trigger="signup")
    set_session_cookie(response, settings, token)
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


@router.post("/password-help", response_model=MessageResponse)
def password_help(payload: PasswordHelpRequest, request: Request) -> MessageResponse:
    result = create_password_help_request(
        request.app.state.settings,
        username=payload.username,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return MessageResponse(message=result["message"])


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
