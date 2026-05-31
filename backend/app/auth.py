from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response, status

from .config import Settings
from .db import get_connection, utcnow_iso
from .models import AuthenticatedUser
from .services.admin_events_service import emit_admin_event
from .services.audit_service import log_audit_event
from .services.rate_limiter_service import SqliteRateLimiter
from .services.security_event_service import log_security_event
from .services.at_rest_encryption import decrypt_at_rest, encrypt_at_rest
from .services.totp_service import SKIP_GRACE_DAYS
from .security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    looks_like_password_hash,
    perform_dummy_verify,
    verify_password,
)


logger = logging.getLogger(__name__)

SESSION_LIVENESS_WINDOW_SECONDS = 90
SESSION_ACTIVITY_WINDOW_SECONDS = 3 * 60 * 60
SESSION_HISTORY_RETENTION_DAYS = 90


def session_live_cutoff_iso(*, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    return (current - timedelta(seconds=SESSION_LIVENESS_WINDOW_SECONDS)).isoformat()


def session_activity_cutoff_iso(*, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    return (current - timedelta(seconds=SESSION_ACTIVITY_WINDOW_SECONDS)).isoformat()


def is_totp_setup_required_for_values(
    *,
    role: str,
    totp_secret: str | None,
    totp_setup_skipped_at: str | None,
    totp_setup_prompt_enabled: bool | int | None = None,
    now: datetime | None = None,
) -> bool:
    del role
    if totp_secret:
        return False
    if not bool(totp_setup_prompt_enabled):
        return False
    if not totp_setup_skipped_at:
        return True
    skipped_at = _parse_iso_datetime(totp_setup_skipped_at)
    if skipped_at is None:
        return True
    current = now or datetime.now(timezone.utc)
    return current - skipped_at >= timedelta(days=SKIP_GRACE_DAYS)


def is_totp_setup_required(settings: Settings, *, user_id: int) -> bool:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT role, totp_secret, totp_setup_skipped_at, totp_setup_prompt_enabled
            FROM users
            WHERE id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return False
        effective_totp_secret = row["totp_secret"]
        if effective_totp_secret:
            try:
                plain_secret, was_encrypted = decrypt_at_rest(str(effective_totp_secret), settings)
            except (TypeError, ValueError):
                effective_totp_secret = None
            else:
                effective_totp_secret = plain_secret or None
                if plain_secret and not was_encrypted:
                    connection.execute(
                        "UPDATE users SET totp_secret = ?, updated_at = ? WHERE id = ?",
                        (encrypt_at_rest(plain_secret, settings), utcnow_iso(), user_id),
                    )
                    connection.commit()
        return is_totp_setup_required_for_values(
            role=row["role"] or "standard_user",
            totp_secret=effective_totp_secret,
            totp_setup_skipped_at=row["totp_setup_skipped_at"],
            totp_setup_prompt_enabled=row["totp_setup_prompt_enabled"],
        )


def _admin_visible_session_state(
    *,
    last_seen_at: str | None,
    last_activity_at: str | None,
    expires_at: str | None,
    revoked_at: str | None,
    now_iso: str,
    live_cutoff_iso: str,
    activity_cutoff_iso: str,
) -> tuple[bool, bool]:
    live = (
        revoked_at is None
        and bool(expires_at)
        and str(expires_at) > now_iso
        and bool(last_seen_at)
        and str(last_seen_at) >= live_cutoff_iso
    )
    recent_activity = live and bool(last_activity_at) and str(last_activity_at) >= activity_cutoff_iso
    return live, recent_activity


def build_login_rate_limiters(settings: Settings) -> tuple[SqliteRateLimiter, SqliteRateLimiter]:
    return (
        SqliteRateLimiter(
            settings,
            bucket_kind="ip",
            window_seconds=300,
            max_attempts=10,
            lockout_seconds=600,
        ),
        SqliteRateLimiter(
            settings,
            bucket_kind="username",
            window_seconds=3600,
            max_attempts=50,
            lockout_seconds=600,
        ),
    )


def resolve_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def ensure_admin_user(settings: Settings) -> None:
    password_hash = settings.admin_password_hash
    if password_hash and not looks_like_password_hash(password_hash):
        raise ValueError(
            "ELVERN_ADMIN_PASSWORD_HASH is not in a supported password hash format"
        )
    if not password_hash and settings.admin_bootstrap_password:
        password_hash = hash_password(settings.admin_bootstrap_password, settings)
    if not password_hash:
        raise ValueError(
            "Set ELVERN_ADMIN_PASSWORD_HASH or ELVERN_ADMIN_BOOTSTRAP_PASSWORD"
        )

    now = utcnow_iso()
    with get_connection(settings) as connection:
        existing = connection.execute(
            "SELECT id, role, enabled, totp_secret, totp_setup_skipped_at FROM users WHERE username = ?",
            (settings.admin_username,),
        ).fetchone()
        if existing:
            if existing["role"] != "admin" or not bool(existing["enabled"]):
                connection.execute(
                    """
                    UPDATE users
                    SET role = 'admin', enabled = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, existing["id"]),
                )
            if not existing["totp_secret"] and not existing["totp_setup_skipped_at"]:
                connection.execute(
                    """
                    UPDATE users
                    SET totp_setup_prompt_enabled = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, existing["id"]),
                )
            connection.commit()
            logger.info("Admin user '%s' already exists", settings.admin_username)
            return
        connection.execute(
            """
            INSERT INTO users (
                username, password_hash, role, enabled, totp_setup_prompt_enabled, created_at, updated_at
            )
            VALUES (?, ?, 'admin', 1, 1, ?, ?)
            """,
            (settings.admin_username, password_hash, now, now),
        )
        connection.commit()
    logger.info("Created bootstrap admin user '%s'", settings.admin_username)


def authenticate_user(
    settings: Settings,
    username: str,
    password: str,
) -> tuple[AuthenticatedUser | None, str | None]:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT
                u.id,
                u.username,
                u.password_hash,
                u.role,
                u.enabled,
                COALESCE(u.age_credential, 18) AS age_credential,
                COALESCE(a.assistant_beta_enabled, 0) AS assistant_beta_enabled
            FROM users u
            LEFT JOIN assistant_user_access a ON a.user_id = u.id
            WHERE u.username = ?
            """,
            (username,),
        ).fetchone()
        if row is None:
            perform_dummy_verify(settings)
            return None, "user_not_found"
        if not bool(row["enabled"]):
            return None, "disabled"
        ok, new_hash = verify_password(password, row["password_hash"], settings)
        if not ok:
            return None, "invalid_credentials"
        if new_hash is not None:
            connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (new_hash, utcnow_iso(), row["id"]),
            )
            connection.commit()
            log_audit_event(
                settings,
                action="password_hash_upgraded",
                outcome="success",
                user_id=int(row["id"]),
                username=str(row["username"]),
                role=str(row["role"] or "standard_user"),
                details={"from": "pbkdf2_sha256", "to": "argon2id"},
            )
        return (
            AuthenticatedUser(
                id=row["id"],
                username=row["username"],
                role=row["role"] or "standard_user",
                enabled=bool(row["enabled"]),
                assistant_beta_enabled=bool(row["assistant_beta_enabled"]),
                age_credential=int(row["age_credential"] or 18),
            ),
            None,
        )


def create_session(
    settings: Settings,
    user: AuthenticatedUser,
    *,
    ip_address: str,
    user_agent: str | None,
) -> str:
    token = generate_session_token()
    token_hash = hash_session_token(token, settings.session_secret)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=settings.session_ttl_hours)
    with get_connection(settings) as connection:
        cleanup_expired_sessions(connection, now_iso=now.isoformat())
        connection.execute(
            """
            INSERT INTO sessions (
                user_id,
                session_token_hash,
                created_at,
                expires_at,
                last_seen_at,
                last_activity_at,
                user_agent,
                ip_address
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user.id,
                token_hash,
                now.isoformat(),
                expires_at.isoformat(),
                now.isoformat(),
                now.isoformat(),
                user_agent,
                ip_address,
            ),
        )
        connection.execute(
            "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
            (now.isoformat(), now.isoformat(), user.id),
        )
        connection.commit()
    emit_admin_event("session_created", user_id=user.id)
    return token


def get_user_by_session_token(
    settings: Settings,
    token: str,
    *,
    touch_mode: str = "activity",
) -> AuthenticatedUser | None:
    if not token:
        return None
    token_hash = hash_session_token(token, settings.session_secret)
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    live_cutoff = session_live_cutoff_iso(now=now_dt)
    activity_cutoff = session_activity_cutoff_iso(now=now_dt)
    should_emit_status_changed = False
    emit_user_id: int | None = None
    emit_session_id: int | None = None
    with get_connection(settings) as connection:
        cleanup_expired_sessions(connection, now_iso=now)
        connection.commit()
        row = connection.execute(
            """
            SELECT
                u.id,
                u.username,
                u.role,
                u.enabled,
                COALESCE(u.age_credential, 18) AS age_credential,
                COALESCE(a.assistant_beta_enabled, 0) AS assistant_beta_enabled,
                s.id AS session_id,
                s.created_at,
                s.expires_at,
                s.last_seen_at,
                s.last_activity_at,
                s.revoked_at
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            LEFT JOIN assistant_user_access a ON a.user_id = u.id
            WHERE s.session_token_hash = ?
              AND s.revoked_at IS NULL
              AND u.enabled = 1
            """,
            (token_hash,),
        ).fetchone()
        if row is None:
            return None
        expires_at = _parse_iso_datetime(row["expires_at"])
        last_seen_at = _parse_iso_datetime(row["last_seen_at"])
        expiry_reason: str | None = None
        if expires_at is None or expires_at <= now_dt:
            expiry_reason = "absolute_ttl"
        elif last_seen_at is None:
            expiry_reason = "missing_last_seen_at"
        else:
            idle_seconds = (now_dt - last_seen_at).total_seconds()
            if idle_seconds > settings.session_idle_timeout_hours * 3600:
                expiry_reason = "idle_timeout"
        if expiry_reason is not None:
            _delete_session_for_auth_expiry(
                connection,
                session_id=int(row["session_id"]),
                now=now,
            )
            connection.commit()
            log_security_event(
                settings,
                event_kind="session_revoked_idle",
                actor_user_id=int(row["id"]),
                actor_username=str(row["username"]),
                details={
                    "reason": expiry_reason,
                    "session_id": int(row["session_id"]),
                },
            )
            emit_admin_event(
                "session_ended",
                user_id=int(row["id"]),
                session_id=int(row["session_id"]),
            )
            return None
        previous_live, previous_recent_activity = _admin_visible_session_state(
            last_seen_at=row["last_seen_at"],
            last_activity_at=row["last_activity_at"] or row["last_seen_at"] or row["created_at"],
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
            now_iso=now,
            live_cutoff_iso=live_cutoff,
            activity_cutoff_iso=activity_cutoff,
        )
        if touch_mode == "heartbeat":
            connection.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
                (now, row["session_id"]),
            )
            next_last_seen_at = now
            next_last_activity_at = row["last_activity_at"] or row["last_seen_at"] or row["created_at"]
        else:
            connection.execute(
                "UPDATE sessions SET last_seen_at = ?, last_activity_at = ? WHERE id = ?",
                (now, now, row["session_id"]),
            )
            next_last_seen_at = now
            next_last_activity_at = now
        connection.commit()
        next_live, next_recent_activity = _admin_visible_session_state(
            last_seen_at=next_last_seen_at,
            last_activity_at=next_last_activity_at,
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
            now_iso=now,
            live_cutoff_iso=live_cutoff,
            activity_cutoff_iso=activity_cutoff,
        )
        should_emit_status_changed = (
            previous_live != next_live or previous_recent_activity != next_recent_activity
        )
        emit_user_id = int(row["id"])
        emit_session_id = int(row["session_id"])
    if should_emit_status_changed:
        emit_admin_event(
            "session_status_changed",
            user_id=emit_user_id,
            session_id=emit_session_id,
        )
    return AuthenticatedUser(
        id=row["id"],
        username=row["username"],
        role=row["role"] or "standard_user",
        enabled=bool(row["enabled"]),
        assistant_beta_enabled=bool(row["assistant_beta_enabled"]),
        age_credential=int(row["age_credential"] or 18),
        session_id=row["session_id"],
    )


def _parse_iso_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _delete_session_for_auth_expiry(connection, *, session_id: int, now: str) -> None:
    from .services.account_access_service import revoke_download_sessions_for_auth_sessions

    revoke_download_sessions_for_auth_sessions(
        connection,
        session_ids=[session_id],
        now=now,
        reason="auth_session_expired",
    )
    connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    connection.execute(
        """
        UPDATE native_playback_sessions
        SET revoked_at = ?
        WHERE auth_session_id = ? AND revoked_at IS NULL AND closed_at IS NULL
        """,
        (now, session_id),
    )
    connection.execute(
        """
        UPDATE desktop_vlc_handoffs
        SET revoked_at = ?
        WHERE auth_session_id = ? AND revoked_at IS NULL
        """,
        (now, session_id),
    )


def get_session_access_failure_reason(
    settings: Settings,
    token: str,
) -> str | None:
    if not token:
        return None
    token_hash = hash_session_token(token, settings.session_secret)
    now_iso = utcnow_iso()
    with get_connection(settings) as connection:
        cleanup_expired_sessions(connection)
        connection.commit()
        row = connection.execute(
            """
            SELECT
                s.id,
                s.user_id,
                s.revoked_reason,
                s.revoked_at,
                s.cleanup_confirmed_at,
                s.expires_at,
                u.enabled
            FROM sessions s
            LEFT JOIN users u ON u.id = s.user_id
            WHERE s.session_token_hash = ?
            LIMIT 1
            """,
            (token_hash,),
        ).fetchone()
        if (
            row is not None
            and row["revoked_at"] is None
            and row["cleanup_confirmed_at"] is None
            and row["expires_at"] is not None
            and str(row["expires_at"]) <= now_iso
        ):
            connection.execute(
                """
                UPDATE sessions
                SET cleanup_confirmed_at = ?
                WHERE id = ? AND cleanup_confirmed_at IS NULL
                """,
                (now_iso, row["id"]),
            )
            connection.commit()
            emit_admin_event(
                "session_ended",
                user_id=int(row["user_id"]) if row["user_id"] is not None else None,
                session_id=int(row["id"]),
            )
        if row is not None and row["revoked_at"] is not None and row["cleanup_confirmed_at"] is None:
            cleanup_confirmed_at = utcnow_iso()
            connection.execute(
                """
                UPDATE sessions
                SET cleanup_confirmed_at = ?
                WHERE id = ? AND cleanup_confirmed_at IS NULL
                """,
                (cleanup_confirmed_at, row["id"]),
            )
            connection.commit()
            emit_admin_event(
                "session_cleanup_confirmed",
                user_id=int(row["user_id"]) if row["user_id"] is not None else None,
                session_id=int(row["id"]),
            )
    if row is None:
        return None
    if row["revoked_reason"] == "user_disabled" or row["enabled"] == 0:
        return "disabled"
    if row["revoked_at"] is not None:
        return "revoked"
    return None


def destroy_session(settings: Settings, token: str | None) -> None:
    if not token:
        return
    token_hash = hash_session_token(token, settings.session_secret)
    with get_connection(settings) as connection:
        now = utcnow_iso()
        row = connection.execute(
            """
            SELECT s.id, s.user_id, u.username, s.ip_address, s.user_agent
            FROM sessions s
            LEFT JOIN users u ON u.id = s.user_id
            WHERE s.session_token_hash = ? AND s.revoked_at IS NULL
            LIMIT 1
            """,
            (token_hash,),
        ).fetchone()
        if row is None:
            return
        connection.execute(
            """
            UPDATE sessions
            SET revoked_at = ?, revoked_reason = ?, cleanup_confirmed_at = ?
            WHERE id = ?
            """,
            (now, "logout", now, row["id"]),
        )
        connection.execute(
            """
            UPDATE native_playback_sessions
            SET revoked_at = ?
            WHERE auth_session_id = ? AND revoked_at IS NULL AND closed_at IS NULL
            """,
            (now, row["id"]),
        )
        connection.execute(
            """
            UPDATE desktop_vlc_handoffs
            SET revoked_at = ?
            WHERE auth_session_id = ? AND revoked_at IS NULL
            """,
            (now, row["id"]),
        )
        from .services.account_access_service import revoke_download_sessions_for_auth_sessions

        revoke_download_sessions_for_auth_sessions(
            connection,
            session_ids=[int(row["id"])],
            now=now,
            reason="auth_session_logout",
        )
        connection.commit()
    log_security_event(
        settings,
        event_kind="session_revoked_manual",
        actor_user_id=int(row["user_id"]),
        actor_username=row["username"],
        ip_address=row["ip_address"],
        user_agent=row["user_agent"],
        details={"reason": "logout", "session_id": int(row["id"])},
    )
    emit_admin_event("session_ended", user_id=int(row["user_id"]), session_id=int(row["id"]))


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def require_authenticated_user(request: Request) -> AuthenticatedUser:
    return _resolve_authenticated_user(request, touch_mode="activity")


def require_authenticated_user_heartbeat(request: Request) -> AuthenticatedUser:
    return _resolve_authenticated_user(request, touch_mode="heartbeat")


def _resolve_authenticated_user(request: Request, *, touch_mode: str) -> AuthenticatedUser:
    settings: Settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie_name)
    failure_reason = get_session_access_failure_reason(settings, token or "")
    user = get_user_by_session_token(settings, token or "", touch_mode=touch_mode)
    if user is None:
        if failure_reason == "disabled":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account has been disabled",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    request.state.totp_setup_required = is_totp_setup_required(settings, user_id=user.id)
    return user


def require_admin_user(user: AuthenticatedUser = Depends(require_authenticated_user)) -> AuthenticatedUser:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access is required",
        )
    return user


def require_admin_user_heartbeat(user: AuthenticatedUser = Depends(require_authenticated_user_heartbeat)) -> AuthenticatedUser:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access is required",
        )
    return user


def revoke_session_by_id(
    settings: Settings,
    *,
    session_id: int,
    reason: str,
) -> bool:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT s.id, s.user_id, u.username, s.ip_address, s.user_agent
            FROM sessions s
            LEFT JOIN users u ON u.id = s.user_id
            WHERE s.id = ? AND s.revoked_at IS NULL
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return False
        _revoke_session_ids(connection, session_ids=[session_id], reason=reason, now=now)
        connection.commit()
    log_security_event(
        settings,
        event_kind="session_revoked_manual",
        actor_user_id=int(row["user_id"]),
        actor_username=row["username"],
        ip_address=row["ip_address"],
        user_agent=row["user_agent"],
        details={"reason": reason, "session_id": int(row["id"])},
    )
    emit_admin_event("session_revoked", user_id=int(row["user_id"]), session_id=int(row["id"]))
    return True


def revoke_sessions_for_user(
    settings: Settings,
    *,
    user_id: int,
    reason: str,
) -> int:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        live_rows = connection.execute(
            """
            SELECT id
            FROM sessions
            WHERE user_id = ? AND revoked_at IS NULL
            """,
            (user_id,),
        ).fetchall()
        revoked_count = revoke_sessions_for_user_in_connection(
            connection,
            user_id=user_id,
            reason=reason,
            now=now,
        )
        connection.commit()
    if revoked_count:
        emit_admin_event(
            "session_revoked",
            user_id=user_id,
            session_id=int(live_rows[0]["id"]) if live_rows else None,
        )
    return revoked_count


def revoke_sessions_for_user_in_connection(
    connection,
    *,
    user_id: int,
    reason: str,
    now: str | None = None,
) -> int:
    rows = connection.execute(
        """
        SELECT id
        FROM sessions
        WHERE user_id = ? AND revoked_at IS NULL
        """,
        (user_id,),
    ).fetchall()
    session_ids = [int(row["id"]) for row in rows]
    current_time = now or utcnow_iso()
    if session_ids:
        _revoke_session_ids(connection, session_ids=session_ids, reason=reason, now=current_time)
    if reason == "user_disabled":
        connection.execute(
            """
            UPDATE native_playback_sessions
            SET revoked_at = ?
            WHERE user_id = ?
              AND revoked_at IS NULL
              AND closed_at IS NULL
            """,
            (current_time, user_id),
        )
        connection.execute(
            """
            UPDATE desktop_vlc_handoffs
            SET revoked_at = ?
            WHERE user_id = ?
              AND revoked_at IS NULL
            """,
            (current_time, user_id),
        )
    if not session_ids:
        return 0
    return len(session_ids)


def _revoke_session_ids(
    connection,
    *,
    session_ids: list[int],
    reason: str,
    now: str,
) -> None:
    if not session_ids:
        return
    placeholders = ",".join("?" for _ in session_ids)
    from .services.account_access_service import revoke_download_sessions_for_auth_sessions

    revoke_download_sessions_for_auth_sessions(
        connection,
        session_ids=session_ids,
        now=now,
        reason=reason,
    )
    revoke_sessions_sql = f"""
        UPDATE sessions
        SET revoked_at = ?, revoked_reason = ?, cleanup_confirmed_at = NULL
        WHERE id IN ({placeholders})
        """  # nosec B608 - placeholders generated from trusted session_ids length
    connection.execute(
        revoke_sessions_sql,
        (now, reason, *session_ids),
    )
    revoke_native_sql = f"""
        UPDATE native_playback_sessions
        SET revoked_at = ?
        WHERE auth_session_id IN ({placeholders})
          AND revoked_at IS NULL
          AND closed_at IS NULL
        """  # nosec B608 - placeholders generated from trusted session_ids length
    connection.execute(
        revoke_native_sql,
        (now, *session_ids),
    )
    revoke_desktop_sql = f"""
        UPDATE desktop_vlc_handoffs
        SET revoked_at = ?
        WHERE auth_session_id IN ({placeholders})
          AND revoked_at IS NULL
        """  # nosec B608 - placeholders generated from trusted session_ids length
    connection.execute(
        revoke_desktop_sql,
        (now, *session_ids),
    )


def cleanup_expired_sessions(connection, *, now_iso: str | None = None) -> None:
    current = now_iso or utcnow_iso()
    retention_cutoff = (
        datetime.fromisoformat(current).astimezone(timezone.utc) - timedelta(days=SESSION_HISTORY_RETENTION_DAYS)
    ).isoformat()
    connection.execute(
        """
        UPDATE download_sessions
        SET revoked_at = COALESCE(revoked_at, ?),
            last_error = COALESCE(last_error, 'auth_session_deleted')
        WHERE auth_session_id IN (
            SELECT id
            FROM sessions
            WHERE (
                    revoked_at IS NOT NULL
                    AND revoked_at <= ?
                  )
               OR (
                    revoked_at IS NULL
                    AND expires_at <= ?
                  )
        )
          AND revoked_at IS NULL
          AND completed_at IS NULL
        """,
        (current, retention_cutoff, retention_cutoff),
    )
    connection.execute(
        """
        DELETE FROM native_playback_sessions
        WHERE expires_at <= ?
           OR closed_at IS NOT NULL
           OR revoked_at IS NOT NULL
        """,
        (current,),
    )
    connection.execute(
        """
        DELETE FROM desktop_vlc_handoffs
        WHERE expires_at <= ?
           OR revoked_at IS NOT NULL
        """,
        (current,),
    )
    connection.execute(
        """
        DELETE FROM sessions
        WHERE (
                revoked_at IS NOT NULL
                AND revoked_at <= ?
              )
           OR (
                revoked_at IS NULL
                AND expires_at <= ?
              )
        """,
        (retention_cutoff, retention_cutoff),
    )


CurrentUser = Depends(require_authenticated_user)
CurrentHeartbeatUser = Depends(require_authenticated_user_heartbeat)
CurrentAdmin = Depends(require_admin_user)
CurrentHeartbeatAdmin = Depends(require_admin_user_heartbeat)
