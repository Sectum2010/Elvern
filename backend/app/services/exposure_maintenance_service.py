from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from ..config import Settings
from ..db import get_connection, utcnow_iso
from .account_access_service import (
    revoke_download_sessions_for_auth_sessions,
    revoke_download_sessions_for_user,
)
from .app_settings_service import get_global_app_setting, set_global_app_setting


EXPOSURE_MODE_MAINTENANCE_LOCK_KEY = "exposure_mode_maintenance_lock_json"
EXPOSURE_MAINTENANCE_LOCK_MESSAGE = "The server is currently under construction, please try again later"
EXPOSURE_MAINTENANCE_LOCK_REASON = "maintenance_mode"
MAINTENANCE_MODE_REVOKED_REASON = "maintenance_mode"


def maintenance_lock_message() -> str:
    return EXPOSURE_MAINTENANCE_LOCK_MESSAGE


def get_exposure_maintenance_lock(settings: Settings) -> dict[str, Any]:
    try:
        raw_value = get_global_app_setting(settings, key=EXPOSURE_MODE_MAINTENANCE_LOCK_KEY)
    except sqlite3.OperationalError:
        return _disabled_lock()
    if not raw_value:
        return _disabled_lock()
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return _disabled_lock()
    if not isinstance(parsed, dict) or not bool(parsed.get("enabled")):
        return _disabled_lock()
    return {
        "enabled": True,
        "reason": str(parsed.get("reason") or EXPOSURE_MAINTENANCE_LOCK_REASON),
        "message": EXPOSURE_MAINTENANCE_LOCK_MESSAGE,
        "created_by_user_id": _optional_int(parsed.get("created_by_user_id")),
        "created_by_username": _optional_str(parsed.get("created_by_username")),
        "created_at": _optional_str(parsed.get("created_at")),
        "updated_at": _optional_str(parsed.get("updated_at")),
    }


def set_exposure_maintenance_lock(settings: Settings, actor: Any, *, enabled: bool) -> dict[str, Any]:
    if not enabled:
        set_global_app_setting(settings, key=EXPOSURE_MODE_MAINTENANCE_LOCK_KEY, value=None)
        return _disabled_lock()

    now = utcnow_iso()
    existing = get_exposure_maintenance_lock(settings)
    created_at = existing.get("created_at") if existing.get("enabled") else now
    created_by_user_id = existing.get("created_by_user_id") if existing.get("enabled") else getattr(actor, "id", None)
    created_by_username = existing.get("created_by_username") if existing.get("enabled") else getattr(actor, "username", None)
    lock = {
        "enabled": True,
        "reason": EXPOSURE_MAINTENANCE_LOCK_REASON,
        "message": EXPOSURE_MAINTENANCE_LOCK_MESSAGE,
        "created_by_user_id": created_by_user_id,
        "created_by_username": created_by_username,
        "created_at": created_at,
        "updated_at": now,
    }
    set_global_app_setting(
        settings,
        key=EXPOSURE_MODE_MAINTENANCE_LOCK_KEY,
        value=json.dumps(lock, sort_keys=True),
    )
    return lock


def enable_maintenance_mode(
    settings: Settings,
    actor: Any,
    *,
    invalidate_auth_session: Callable[..., object] | None = None,
) -> dict[str, Any]:
    mode = set_exposure_maintenance_lock(settings, actor, enabled=True)
    revoke_summary = revoke_non_admin_sessions_for_maintenance_mode(
        settings,
        invalidate_auth_session=invalidate_auth_session,
    )
    return {
        **mode,
        "revoked_non_admin_sessions": revoke_summary["revoked_non_admin_sessions"],
        "affected_non_admin_users": revoke_summary["affected_non_admin_users"],
    }


def disable_maintenance_mode(settings: Settings, actor: Any) -> dict[str, Any]:
    mode = set_exposure_maintenance_lock(settings, actor, enabled=False)
    return {
        **mode,
        "revoked_non_admin_sessions": 0,
        "affected_non_admin_users": 0,
    }


def revoke_non_admin_sessions_for_maintenance_mode(
    settings: Settings,
    *,
    invalidate_auth_session: Callable[..., object] | None = None,
) -> dict[str, int]:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        rows = connection.execute(
            """
            SELECT s.id, s.user_id
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.revoked_at IS NULL
              AND COALESCE(u.role, 'standard_user') != 'admin'
            ORDER BY s.id
            """
        ).fetchall()
        session_ids = [int(row["id"]) for row in rows]
        user_ids = sorted({int(row["user_id"]) for row in rows if row["user_id"] is not None})
        if session_ids:
            revoke_download_sessions_for_auth_sessions(
                connection,
                session_ids=session_ids,
                now=now,
                reason=MAINTENANCE_MODE_REVOKED_REASON,
            )
            _revoke_auth_session_rows(connection, session_ids=session_ids, now=now)
            _revoke_native_playback_by_auth_sessions(connection, session_ids=session_ids, now=now)
            _revoke_desktop_handoffs_by_auth_sessions(connection, session_ids=session_ids, now=now)
        for user_id in user_ids:
            revoke_download_sessions_for_user(
                connection,
                user_id=user_id,
                now=now,
                reason=MAINTENANCE_MODE_REVOKED_REASON,
            )
        if user_ids:
            _revoke_native_playback_by_users(connection, user_ids=user_ids, now=now)
            _revoke_desktop_handoffs_by_users(connection, user_ids=user_ids, now=now)
        connection.commit()

    if callable(invalidate_auth_session):
        for session_id in session_ids:
            invalidate_auth_session(session_id, reason=MAINTENANCE_MODE_REVOKED_REASON)

    return {
        "revoked_non_admin_sessions": len(session_ids),
        "affected_non_admin_users": len(user_ids),
    }


def is_exposure_maintenance_lock_enabled(settings: Settings) -> bool:
    return bool(get_exposure_maintenance_lock(settings).get("enabled"))


def _disabled_lock() -> dict[str, Any]:
    return {
        "enabled": False,
        "reason": None,
        "message": EXPOSURE_MAINTENANCE_LOCK_MESSAGE,
        "created_by_user_id": None,
        "created_by_username": None,
        "created_at": None,
        "updated_at": None,
        "revoked_non_admin_sessions": 0,
        "affected_non_admin_users": 0,
    }


def _revoke_auth_session_rows(connection: sqlite3.Connection, *, session_ids: list[int], now: str) -> None:
    if not session_ids:
        return
    placeholders = ",".join("?" for _ in session_ids)
    sql = f"""
        UPDATE sessions
        SET revoked_at = ?, revoked_reason = ?, cleanup_confirmed_at = NULL
        WHERE id IN ({placeholders})
        """  # nosec B608 - placeholders generated from trusted session_ids length
    connection.execute(sql, (now, MAINTENANCE_MODE_REVOKED_REASON, *session_ids))


def _revoke_native_playback_by_auth_sessions(connection: sqlite3.Connection, *, session_ids: list[int], now: str) -> None:
    if not session_ids:
        return
    placeholders = ",".join("?" for _ in session_ids)
    by_auth_sql = f"""
        UPDATE native_playback_sessions
        SET revoked_at = ?
        WHERE auth_session_id IN ({placeholders})
          AND revoked_at IS NULL
          AND closed_at IS NULL
        """  # nosec B608 - placeholders generated from trusted session_ids length
    connection.execute(by_auth_sql, (now, *session_ids))
    by_created_sql = f"""
        UPDATE native_playback_sessions
        SET revoked_at = ?
        WHERE created_from_auth_session_id IN ({placeholders})
          AND revoked_at IS NULL
          AND closed_at IS NULL
        """  # nosec B608 - placeholders generated from trusted session_ids length
    connection.execute(by_created_sql, (now, *session_ids))


def _revoke_native_playback_by_users(connection: sqlite3.Connection, *, user_ids: list[int], now: str) -> None:
    if not user_ids:
        return
    placeholders = ",".join("?" for _ in user_ids)
    sql = f"""
        UPDATE native_playback_sessions
        SET revoked_at = ?
        WHERE user_id IN ({placeholders})
          AND revoked_at IS NULL
          AND closed_at IS NULL
        """  # nosec B608 - placeholders generated from trusted user_ids length
    connection.execute(sql, (now, *user_ids))


def _revoke_desktop_handoffs_by_auth_sessions(connection: sqlite3.Connection, *, session_ids: list[int], now: str) -> None:
    if not session_ids:
        return
    placeholders = ",".join("?" for _ in session_ids)
    sql = f"""
        UPDATE desktop_vlc_handoffs
        SET revoked_at = ?
        WHERE auth_session_id IN ({placeholders})
          AND revoked_at IS NULL
        """  # nosec B608 - placeholders generated from trusted session_ids length
    connection.execute(sql, (now, *session_ids))


def _revoke_desktop_handoffs_by_users(connection: sqlite3.Connection, *, user_ids: list[int], now: str) -> None:
    if not user_ids:
        return
    placeholders = ",".join("?" for _ in user_ids)
    sql = f"""
        UPDATE desktop_vlc_handoffs
        SET revoked_at = ?
        WHERE user_id IN ({placeholders})
          AND revoked_at IS NULL
        """  # nosec B608 - placeholders generated from trusted user_ids length
    connection.execute(sql, (now, *user_ids))


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
