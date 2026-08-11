from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status

from ..db import get_connection, utcnow_iso
from ..security import verify_password


RECOVERY_RECENT_AUTH_SECONDS = 10 * 60
RECOVERY_RECENT_AUTH_REQUIRED_CODE = "recent_auth_required"
BACKUP_PASSPHRASE_ATTEMPT_LIMIT = 5
BACKUP_PASSPHRASE_ATTEMPT_WINDOW_SECONDS = 15 * 60
BACKUP_PASSPHRASE_BLOCK_SECONDS = 15 * 60


def _iso_at(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def verify_recovery_recent_auth(settings, *, actor, current_admin_password: str) -> dict[str, object]:
    if actor.session_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin session is required")
    now = datetime.now(timezone.utc)
    with get_connection(settings) as connection:
        row = connection.execute(
            "SELECT password_hash, role, enabled FROM users WHERE id = ? LIMIT 1",
            (actor.id,),
        ).fetchone()
        if row is None or not bool(row["enabled"]) or (row["role"] or "") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access is required")
        ok, new_hash = verify_password(current_admin_password, row["password_hash"], settings)
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "recovery_password_invalid",
                    "message": "Current admin password is incorrect.",
                },
            )
        expires_at = now + timedelta(seconds=RECOVERY_RECENT_AUTH_SECONDS)
        if new_hash is not None:
            connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (new_hash, utcnow_iso(), actor.id),
            )
        connection.execute(
            """
            INSERT INTO recovery_recent_auth (
                auth_session_id, user_id, verified_at, expires_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(auth_session_id) DO UPDATE SET
                user_id = excluded.user_id,
                verified_at = excluded.verified_at,
                expires_at = excluded.expires_at
            """,
            (actor.session_id, actor.id, _iso_at(now), _iso_at(expires_at)),
        )
        connection.commit()
    return {
        "verified": True,
        "expires_at": _iso_at(expires_at),
        "expires_in_seconds": RECOVERY_RECENT_AUTH_SECONDS,
    }


def require_recovery_recent_auth(settings, *, actor) -> None:
    if actor.session_id is None:
        _raise_recent_auth_required()
    now = _iso_at(datetime.now(timezone.utc))
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT auth_session_id
            FROM recovery_recent_auth
            WHERE auth_session_id = ? AND user_id = ? AND expires_at > ?
            LIMIT 1
            """,
            (actor.session_id, actor.id, now),
        ).fetchone()
        connection.execute("DELETE FROM recovery_recent_auth WHERE expires_at <= ?", (now,))
        connection.commit()
    if row is None:
        _raise_recent_auth_required()


def get_recovery_recent_auth_status(settings, *, actor) -> dict[str, object]:
    if actor.session_id is None:
        return {"verified": False, "expires_at": None, "expires_in_seconds": 0}
    now_dt = datetime.now(timezone.utc)
    now = _iso_at(now_dt)
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT expires_at
            FROM recovery_recent_auth
            WHERE auth_session_id = ? AND user_id = ? AND expires_at > ?
            LIMIT 1
            """,
            (actor.session_id, actor.id, now),
        ).fetchone()
    if row is None:
        return {"verified": False, "expires_at": None, "expires_in_seconds": 0}
    expires_at = datetime.fromisoformat(str(row["expires_at"]))
    seconds = max(0, int((expires_at - now_dt).total_seconds()))
    return {
        "verified": seconds > 0,
        "expires_at": str(row["expires_at"]),
        "expires_in_seconds": seconds,
    }


def enforce_backup_passphrase_rate_limit(settings, *, actor, checkpoint_id: str) -> None:
    if actor.session_id is None:
        _raise_recent_auth_required()
    now_dt = datetime.now(timezone.utc)
    now = _iso_at(now_dt)
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT failure_count, window_started_at, blocked_until
            FROM backup_passphrase_attempts
            WHERE auth_session_id = ? AND checkpoint_id = ?
            LIMIT 1
            """,
            (actor.session_id, checkpoint_id),
        ).fetchone()
        if row is None:
            return
        blocked_until = _parse_iso(row["blocked_until"])
        if blocked_until is not None and blocked_until > now_dt:
            _raise_backup_passphrase_rate_limited()
        window_started_at = _parse_iso(row["window_started_at"])
        if (
            window_started_at is None
            or (now_dt - window_started_at).total_seconds() >= BACKUP_PASSPHRASE_ATTEMPT_WINDOW_SECONDS
        ):
            connection.execute(
                "DELETE FROM backup_passphrase_attempts WHERE auth_session_id = ? AND checkpoint_id = ?",
                (actor.session_id, checkpoint_id),
            )
            connection.commit()


def record_backup_passphrase_failure(settings, *, actor, checkpoint_id: str) -> None:
    if actor.session_id is None:
        _raise_recent_auth_required()
    now_dt = datetime.now(timezone.utc)
    now = _iso_at(now_dt)
    window_cutoff = now_dt - timedelta(seconds=BACKUP_PASSPHRASE_ATTEMPT_WINDOW_SECONDS)
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT failure_count, window_started_at
            FROM backup_passphrase_attempts
            WHERE auth_session_id = ? AND checkpoint_id = ?
            LIMIT 1
            """,
            (actor.session_id, checkpoint_id),
        ).fetchone()
        window_started_at = _parse_iso(row["window_started_at"]) if row is not None else None
        failure_count = int(row["failure_count"] or 0) if row is not None else 0
        if window_started_at is None or window_started_at <= window_cutoff:
            window_started_at = now_dt
            failure_count = 0
        failure_count += 1
        blocked_until = (
            _iso_at(now_dt + timedelta(seconds=BACKUP_PASSPHRASE_BLOCK_SECONDS))
            if failure_count >= BACKUP_PASSPHRASE_ATTEMPT_LIMIT
            else None
        )
        connection.execute(
            """
            INSERT INTO backup_passphrase_attempts (
                auth_session_id, checkpoint_id, failure_count,
                window_started_at, blocked_until, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(auth_session_id, checkpoint_id) DO UPDATE SET
                failure_count = excluded.failure_count,
                window_started_at = excluded.window_started_at,
                blocked_until = excluded.blocked_until,
                updated_at = excluded.updated_at
            """,
            (
                actor.session_id,
                checkpoint_id,
                failure_count,
                _iso_at(window_started_at),
                blocked_until,
                now,
            ),
        )
        connection.commit()


def clear_backup_passphrase_failures(settings, *, actor, checkpoint_id: str) -> None:
    if actor.session_id is None:
        return
    with get_connection(settings) as connection:
        connection.execute(
            "DELETE FROM backup_passphrase_attempts WHERE auth_session_id = ? AND checkpoint_id = ?",
            (actor.session_id, checkpoint_id),
        )
        connection.commit()


def _parse_iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _raise_backup_passphrase_rate_limited() -> None:
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "code": "backup_passphrase_rate_limited",
            "message": "Too many passphrase attempts. Try again later.",
        },
    )


def _raise_recent_auth_required() -> None:
    raise HTTPException(
        status_code=428,
        detail={
            "code": RECOVERY_RECENT_AUTH_REQUIRED_CODE,
            "message": "Confirm your current admin password to continue.",
        },
    )
