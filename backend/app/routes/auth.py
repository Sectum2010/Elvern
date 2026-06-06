from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time

from fastapi import APIRouter, HTTPException, Request, Response, status

from ..auth import (
    CurrentHeartbeatUser,
    CurrentUser,
    authenticate_user,
    clear_session_cookie,
    create_session,
    destroy_session,
    get_user_by_session_token,
    is_totp_setup_required,
    is_totp_setup_required_for_values,
    raise_if_exposure_maintenance_locked,
    resolve_client_ip,
    set_session_cookie,
)
from ..db import get_connection, utcnow_iso
from ..models import AuthenticatedUser
from ..services.account_access_service import create_password_help_request, create_user_with_invite
from ..services.audit_service import log_audit_event
from ..services.at_rest_encryption import decrypt_at_rest, encrypt_at_rest
from ..services.exposure_maintenance_service import (
    is_exposure_maintenance_lock_enabled,
    maintenance_lock_message,
)
from ..services.rate_limiter_service import count_recent_failures
from ..services.security_event_service import log_security_event
from ..services.totp_service import (
    CHALLENGE_TOKEN_TTL_SECONDS,
    SKIP_GRACE_DAYS,
    TOTP_ISSUER,
    build_provisioning_uri,
    generate_challenge_token,
    generate_recovery_codes,
    generate_totp_secret,
    hash_challenge_token,
    hash_recovery_code,
    recovery_code_hash_candidates,
    render_qr_svg,
    verify_totp_code,
)
from ..security import verify_password
from ..schemas import (
    AuthLoginRequest,
    AuthSignupRequest,
    AuthUserEnvelope,
    MessageResponse,
    PasswordHelpRequest,
    TotpDisableRequest,
    TotpLoginRequest,
    TotpRecoveryCodesResponse,
    TotpRegenerateRecoveryCodesRequest,
    TotpSetupStartResponse,
    TotpSetupVerifyRequest,
    TotpSetupVerifyResponse,
    TotpSkipResponse,
    TotpStatusResponse,
)


router = APIRouter(prefix="/api/auth", tags=["auth"])


def _auth_envelope(user: AuthenticatedUser, *, totp_setup_required: bool = False) -> AuthUserEnvelope:
    return AuthUserEnvelope(
        session="ok",
        totp_setup_required=totp_setup_required,
        user={
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "enabled": user.enabled,
            "assistant_beta_enabled": user.assistant_beta_enabled,
            "age_credential": user.age_credential,
            "session_id": user.session_id,
        },
    )


def _get_totp_row(settings, *, user_id: int):
    with get_connection(settings) as connection:
        return connection.execute(
            """
            SELECT
                id,
                username,
                password_hash,
                role,
                enabled,
                totp_secret,
                totp_enabled_at,
                totp_last_used_window,
                totp_setup_skipped_at,
                totp_setup_prompt_enabled
            FROM users
            WHERE id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()


def _create_challenge(settings, *, user: AuthenticatedUser, ip_address: str, user_agent: str | None) -> str:
    token = generate_challenge_token()
    token_hash = hash_challenge_token(token, settings.session_secret)
    now = utcnow_iso()
    with get_connection(settings) as connection:
        connection.execute(
            "DELETE FROM login_challenges WHERE expires_at_unix <= ?",
            (time.time(),),
        )
        connection.execute(
            """
            INSERT INTO login_challenges (
                challenge_token_hash,
                user_id,
                created_at,
                expires_at_unix,
                ip_address,
                user_agent
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                token_hash,
                user.id,
                now,
                time.time() + CHALLENGE_TOKEN_TTL_SECONDS,
                ip_address,
                user_agent,
            ),
        )
        connection.commit()
    return token


def _insert_recovery_codes(settings, connection, *, user_id: int, codes: list[str]) -> None:
    now = utcnow_iso()
    connection.executemany(
        """
        INSERT INTO user_recovery_codes (user_id, code_hash, created_at)
        VALUES (?, ?, ?)
        """,
        [(user_id, hash_recovery_code(code, settings), now) for code in codes],
    )


def _consume_recovery_code(settings, connection, *, user_id: int, code: str) -> bool:
    hardened_hash, legacy_hash = recovery_code_hash_candidates(code, settings)
    row = connection.execute(
        """
        SELECT id
        FROM user_recovery_codes
        WHERE user_id = ?
          AND code_hash IN (?, ?)
          AND used_at IS NULL
        LIMIT 1
        """,
        (user_id, hardened_hash, legacy_hash),
    ).fetchone()
    if row is None:
        return False
    connection.execute(
        "UPDATE user_recovery_codes SET used_at = ? WHERE id = ?",
        (utcnow_iso(), row["id"]),
    )
    return True


def _clear_user_totp_for_reenrollment(connection, *, user_id: int) -> None:
    now = utcnow_iso()
    connection.execute(
        """
        UPDATE users
        SET totp_secret = NULL,
            totp_enabled_at = NULL,
            totp_last_used_window = NULL,
            totp_setup_prompt_enabled = 1,
            updated_at = ?
        WHERE id = ?
        """,
        (now, user_id),
    )
    connection.execute("DELETE FROM user_recovery_codes WHERE user_id = ?", (user_id,))


def _resolve_user_totp_secret(settings, connection, *, user_id: int, stored_secret: object) -> str | None:
    if not stored_secret:
        return None
    try:
        secret, was_encrypted = decrypt_at_rest(str(stored_secret), settings)
    except (TypeError, ValueError):
        _clear_user_totp_for_reenrollment(connection, user_id=user_id)
        return None
    if not secret:
        return None
    if not was_encrypted:
        connection.execute(
            "UPDATE users SET totp_secret = ?, updated_at = ? WHERE id = ?",
            (encrypt_at_rest(secret, settings), utcnow_iso(), user_id),
        )
    return secret


def _resolve_pending_totp_secret(settings, connection, *, user_id: int, stored_secret: object) -> str | None:
    if not stored_secret:
        return None
    try:
        secret, was_encrypted = decrypt_at_rest(str(stored_secret), settings)
    except (TypeError, ValueError):
        connection.execute("DELETE FROM totp_pending_secrets WHERE user_id = ?", (user_id,))
        return None
    if secret and not was_encrypted:
        connection.execute(
            "UPDATE totp_pending_secrets SET secret = ? WHERE user_id = ?",
            (encrypt_at_rest(secret, settings), user_id),
        )
    return secret or None


def _verify_user_password(settings, connection, *, user_id: int, password: str) -> bool:
    row = connection.execute(
        "SELECT password_hash FROM users WHERE id = ? LIMIT 1",
        (user_id,),
    ).fetchone()
    if row is None:
        return False
    ok, new_hash = verify_password(password, row["password_hash"], settings)
    if ok and new_hash is not None:
        connection.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (new_hash, utcnow_iso(), user_id),
        )
    return ok


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
    raise_if_exposure_maintenance_locked(settings, user)
    totp_row = _get_totp_row(settings, user_id=user.id)
    if totp_row is not None and totp_row["totp_secret"]:
        with get_connection(settings) as connection:
            fresh_totp_row = connection.execute(
                "SELECT totp_secret FROM users WHERE id = ? LIMIT 1",
                (user.id,),
            ).fetchone()
            totp_secret = _resolve_user_totp_secret(
                settings,
                connection,
                user_id=user.id,
                stored_secret=fresh_totp_row["totp_secret"] if fresh_totp_row else None,
            )
            connection.commit()
        if totp_secret:
            challenge_token = _create_challenge(
                settings,
                user=user,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return AuthUserEnvelope(
                session="pending_totp",
                challenge_token=challenge_token,
                expires_in_seconds=CHALLENGE_TOKEN_TTL_SECONDS,
                totp_setup_required=False,
                user=None,
            )
        totp_row = _get_totp_row(settings, user_id=user.id)
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
    setup_required = False
    if totp_row is not None:
        setup_required = is_totp_setup_required_for_values(
            role=totp_row["role"] or "standard_user",
            totp_secret=totp_row["totp_secret"],
            totp_setup_skipped_at=totp_row["totp_setup_skipped_at"],
            totp_setup_prompt_enabled=totp_row["totp_setup_prompt_enabled"],
        )
    return _auth_envelope(
        AuthenticatedUser(
            id=user.id,
            username=user.username,
            role=user.role,
            enabled=user.enabled,
            assistant_beta_enabled=user.assistant_beta_enabled,
            session_id=None,
        ),
        totp_setup_required=setup_required,
    )


@router.post("/login/totp", response_model=AuthUserEnvelope)
def login_totp(payload: TotpLoginRequest, request: Request, response: Response) -> AuthUserEnvelope:
    settings = request.app.state.settings
    ip_rate_limiter = request.app.state.login_ip_rate_limiter
    username_rate_limiter = request.app.state.login_username_rate_limiter
    ip_address = resolve_client_ip(request)
    user_agent = request.headers.get("user-agent")
    token_hash = hash_challenge_token(payload.challenge_token, settings.session_secret)
    with get_connection(settings) as connection:
        challenge = connection.execute(
            """
            SELECT c.user_id, c.expires_at_unix, u.username, u.role, u.enabled,
                   u.totp_secret, u.totp_last_used_window,
                   COALESCE(a.assistant_beta_enabled, 0) AS assistant_beta_enabled
            FROM login_challenges c
            JOIN users u ON u.id = c.user_id
            LEFT JOIN assistant_user_access a ON a.user_id = u.id
            WHERE c.challenge_token_hash = ?
            LIMIT 1
            """,
            (token_hash,),
        ).fetchone()
        if challenge is None or float(challenge["expires_at_unix"]) <= time.time():
            if challenge is not None:
                connection.execute("DELETE FROM login_challenges WHERE challenge_token_hash = ?", (token_hash,))
                connection.commit()
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_or_expired")
        username = str(challenge["username"])
        ip_retry_after = ip_rate_limiter.check(ip_address)
        username_retry_after = username_rate_limiter.check(username)
        if ip_retry_after or username_retry_after:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many login attempts.")
        if not bool(challenge["enabled"]):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account has been disabled",
            )
        totp_secret = challenge["totp_secret"]
        if not totp_secret:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="totp_not_enabled")
        totp_secret = _resolve_user_totp_secret(
            settings,
            connection,
            user_id=int(challenge["user_id"]),
            stored_secret=challenge["totp_secret"],
        )
        if not totp_secret:
            connection.execute("DELETE FROM login_challenges WHERE challenge_token_hash = ?", (token_hash,))
            connection.commit()
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="totp_setup_required")
        valid, new_window = verify_totp_code(
            totp_secret,
            payload.code.strip(),
            challenge["totp_last_used_window"],
        )
        recovery_used = False
        if not valid:
            recovery_used = _consume_recovery_code(
                settings,
                connection,
                user_id=int(challenge["user_id"]),
                code=payload.code,
            )
            valid = recovery_used
        if not valid:
            username_rate_limiter.register_failure(username)
            ip_rate_limiter.register_failure(ip_address)
            log_security_event(
                settings,
                event_kind="totp_failure",
                actor_user_id=int(challenge["user_id"]),
                actor_username=username,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_totp_code")
        if new_window is not None:
            connection.execute(
                "UPDATE users SET totp_last_used_window = ?, updated_at = ? WHERE id = ?",
                (new_window, utcnow_iso(), int(challenge["user_id"])),
            )
        connection.execute("DELETE FROM login_challenges WHERE challenge_token_hash = ?", (token_hash,))
        connection.commit()
    user = AuthenticatedUser(
        id=int(challenge["user_id"]),
        username=str(challenge["username"]),
        role=str(challenge["role"] or "standard_user"),
        enabled=bool(challenge["enabled"]),
        assistant_beta_enabled=bool(challenge["assistant_beta_enabled"]),
    )
    raise_if_exposure_maintenance_locked(settings, user)
    ip_rate_limiter.clear(ip_address)
    username_rate_limiter.clear(user.username)
    token = create_session(settings, user, ip_address=ip_address, user_agent=user_agent)
    set_session_cookie(response, settings, token)
    log_security_event(
        settings,
        event_kind="login_recovery_code_success" if recovery_used else "login_totp_success",
        actor_user_id=user.id,
        actor_username=user.username,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    log_audit_event(
        settings,
        action="auth.login",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        ip_address=ip_address,
        user_agent=user_agent,
        details={"totp": "recovery" if recovery_used else "totp"},
    )
    return _auth_envelope(user, totp_setup_required=False)


@router.post("/totp/setup", response_model=TotpSetupStartResponse)
def start_totp_setup(request: Request, user: AuthenticatedUser = CurrentUser) -> TotpSetupStartResponse:
    settings = request.app.state.settings
    with get_connection(settings) as connection:
        row = connection.execute(
            "SELECT totp_secret FROM users WHERE id = ? LIMIT 1",
            (user.id,),
        ).fetchone()
        if row is not None and row["totp_secret"]:
            existing_secret = _resolve_user_totp_secret(
                settings,
                connection,
                user_id=user.id,
                stored_secret=row["totp_secret"],
            )
            connection.commit()
            if existing_secret:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already_enabled")
    secret = generate_totp_secret()
    now = utcnow_iso()
    with get_connection(settings) as connection:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        connection.execute("DELETE FROM totp_pending_secrets WHERE created_at < ?", (cutoff,))
        connection.execute(
            """
            INSERT OR REPLACE INTO totp_pending_secrets (user_id, secret, created_at)
            VALUES (?, ?, ?)
            """,
            (user.id, encrypt_at_rest(secret, settings), now),
        )
        connection.commit()
    uri = build_provisioning_uri(secret, user.username)
    return TotpSetupStartResponse(
        secret=secret,
        qr_svg=render_qr_svg(uri),
        issuer=TOTP_ISSUER,
        account=user.username,
    )


@router.post("/totp/setup/verify", response_model=TotpSetupVerifyResponse)
def verify_totp_setup(
    payload: TotpSetupVerifyRequest,
    request: Request,
    user: AuthenticatedUser = CurrentUser,
) -> TotpSetupVerifyResponse:
    settings = request.app.state.settings
    with get_connection(settings) as connection:
        row = connection.execute(
            "SELECT secret, created_at FROM totp_pending_secrets WHERE user_id = ? LIMIT 1",
            (user.id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="setup_expired")
        created_at = datetime.fromisoformat(str(row["created_at"]))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - created_at > timedelta(minutes=10):
            connection.execute("DELETE FROM totp_pending_secrets WHERE user_id = ?", (user.id,))
            connection.commit()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="setup_expired")
        secret = _resolve_pending_totp_secret(
            settings,
            connection,
            user_id=user.id,
            stored_secret=row["secret"],
        )
        if not secret:
            connection.commit()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="setup_expired")
        valid, window = verify_totp_code(secret, payload.code.strip(), None)
        if not valid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_code")
        codes = generate_recovery_codes()
        now = utcnow_iso()
        connection.execute(
            """
            UPDATE users
            SET totp_secret = ?, totp_enabled_at = ?, totp_last_used_window = ?,
                totp_setup_skipped_at = NULL,
                totp_setup_prompt_enabled = 1,
                updated_at = ?
            WHERE id = ?
            """,
            (encrypt_at_rest(secret, settings), now, window, now, user.id),
        )
        connection.execute("DELETE FROM user_recovery_codes WHERE user_id = ?", (user.id,))
        _insert_recovery_codes(settings, connection, user_id=user.id, codes=codes)
        connection.execute("DELETE FROM totp_pending_secrets WHERE user_id = ?", (user.id,))
        connection.commit()
    log_security_event(settings, event_kind="totp_enabled", actor_user_id=user.id, actor_username=user.username)
    return TotpSetupVerifyResponse(enabled=True, recovery_codes=codes)


@router.post("/totp/skip", response_model=TotpSkipResponse)
def skip_totp_setup(request: Request, user: AuthenticatedUser = CurrentUser) -> TotpSkipResponse:
    settings = request.app.state.settings
    now = datetime.now(timezone.utc)
    with get_connection(settings) as connection:
        connection.execute(
            """
            UPDATE users
            SET totp_setup_skipped_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (now.isoformat(), now.isoformat(), user.id),
        )
        connection.commit()
    log_security_event(settings, event_kind="totp_setup_skipped", actor_user_id=user.id, actor_username=user.username)
    return TotpSkipResponse(skipped_until=(now + timedelta(days=SKIP_GRACE_DAYS)).isoformat())


@router.get("/totp/status", response_model=TotpStatusResponse)
def totp_status(request: Request, user: AuthenticatedUser = CurrentUser) -> TotpStatusResponse:
    settings = request.app.state.settings
    with get_connection(settings) as connection:
        row = connection.execute(
            "SELECT totp_secret, totp_enabled_at, totp_setup_prompt_enabled FROM users WHERE id = ? LIMIT 1",
            (user.id,),
        ).fetchone()
        totp_secret = _resolve_user_totp_secret(
            settings,
            connection,
            user_id=user.id,
            stored_secret=row["totp_secret"] if row else None,
        )
        remaining = connection.execute(
            "SELECT COUNT(*) FROM user_recovery_codes WHERE user_id = ? AND used_at IS NULL",
            (user.id,),
        ).fetchone()[0]
        connection.commit()
    return TotpStatusResponse(
        enabled=bool(totp_secret),
        enabled_at=row["totp_enabled_at"] if row and totp_secret else None,
        recovery_codes_remaining=int(remaining),
        setup_required=is_totp_setup_required(settings, user_id=user.id),
        setup_available=bool(row and row["totp_setup_prompt_enabled"] and not totp_secret),
    )


@router.post("/totp/disable", response_model=MessageResponse)
def disable_totp(
    payload: TotpDisableRequest,
    request: Request,
    user: AuthenticatedUser = CurrentUser,
) -> MessageResponse:
    settings = request.app.state.settings
    with get_connection(settings) as connection:
        row = connection.execute(
            "SELECT totp_secret, totp_last_used_window FROM users WHERE id = ? LIMIT 1",
            (user.id,),
        ).fetchone()
        if row is None or not row["totp_secret"]:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="not_enabled")
        if not _verify_user_password(settings, connection, user_id=user.id, password=payload.password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_password")
        totp_secret = _resolve_user_totp_secret(
            settings,
            connection,
            user_id=user.id,
            stored_secret=row["totp_secret"],
        )
        if not totp_secret:
            connection.commit()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="totp_setup_required")
        valid, window = verify_totp_code(totp_secret, payload.totp_or_recovery.strip(), row["totp_last_used_window"])
        if not valid:
            valid = _consume_recovery_code(settings, connection, user_id=user.id, code=payload.totp_or_recovery)
        if not valid:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_totp_code")
        now = utcnow_iso()
        connection.execute(
            """
            UPDATE users
            SET totp_secret = NULL, totp_enabled_at = NULL, totp_last_used_window = NULL,
                totp_setup_skipped_at = ?,
                totp_setup_prompt_enabled = 0,
                updated_at = ?
            WHERE id = ?
            """,
            (now, now, user.id),
        )
        connection.execute("DELETE FROM user_recovery_codes WHERE user_id = ?", (user.id,))
        connection.commit()
    log_security_event(settings, event_kind="totp_disabled", actor_user_id=user.id, actor_username=user.username)
    return MessageResponse(message="Two-factor authentication disabled")


@router.post("/recovery-codes/regenerate", response_model=TotpRecoveryCodesResponse)
def regenerate_recovery_codes(
    payload: TotpRegenerateRecoveryCodesRequest,
    request: Request,
    user: AuthenticatedUser = CurrentUser,
) -> TotpRecoveryCodesResponse:
    settings = request.app.state.settings
    with get_connection(settings) as connection:
        row = connection.execute(
            "SELECT totp_secret, totp_last_used_window FROM users WHERE id = ? LIMIT 1",
            (user.id,),
        ).fetchone()
        if row is None or not row["totp_secret"]:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="not_enabled")
        if not _verify_user_password(settings, connection, user_id=user.id, password=payload.password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_password")
        totp_secret = _resolve_user_totp_secret(
            settings,
            connection,
            user_id=user.id,
            stored_secret=row["totp_secret"],
        )
        if not totp_secret:
            connection.commit()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="totp_setup_required")
        valid, window = verify_totp_code(totp_secret, payload.totp_code.strip(), row["totp_last_used_window"])
        if not valid:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_totp_code")
        codes = generate_recovery_codes()
        connection.execute("DELETE FROM user_recovery_codes WHERE user_id = ?", (user.id,))
        _insert_recovery_codes(settings, connection, user_id=user.id, codes=codes)
        connection.execute(
            "UPDATE users SET totp_last_used_window = ?, updated_at = ? WHERE id = ?",
            (window, utcnow_iso(), user.id),
        )
        connection.commit()
    log_security_event(settings, event_kind="recovery_codes_regenerated", actor_user_id=user.id, actor_username=user.username)
    return TotpRecoveryCodesResponse(recovery_codes=codes)


@router.post("/signup", response_model=AuthUserEnvelope)
def signup(payload: AuthSignupRequest, request: Request, response: Response) -> AuthUserEnvelope:
    settings = request.app.state.settings
    if is_exposure_maintenance_lock_enabled(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=maintenance_lock_message(),
        )
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
    return _auth_envelope(
        AuthenticatedUser(
            id=user.id,
            username=user.username,
            role=user.role,
            enabled=user.enabled,
            assistant_beta_enabled=user.assistant_beta_enabled,
            session_id=None,
        )
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
def logout(request: Request, response: Response) -> MessageResponse:
    settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie_name)
    user = get_user_by_session_token(settings, token or "", touch_mode="activity")
    destroy_session(settings, token)
    clear_session_cookie(response, settings)
    if user is not None:
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
    return _auth_envelope(
        user,
        totp_setup_required=is_totp_setup_required(request.app.state.settings, user_id=user.id),
    )


@router.post("/heartbeat", response_model=MessageResponse)
def heartbeat(user: AuthenticatedUser = CurrentHeartbeatUser) -> MessageResponse:
    del user
    return MessageResponse(message="Session heartbeat recorded")
