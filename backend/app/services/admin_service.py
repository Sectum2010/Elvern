from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fastapi import HTTPException, status

from ..auth import (
    cleanup_expired_sessions,
    revoke_session_by_id,
    revoke_sessions_for_user,
    revoke_sessions_for_user_in_connection,
    session_activity_cutoff_iso,
    session_live_cutoff_iso,
)
from ..config import Settings
from ..db import get_connection, utcnow_iso
from ..models import AuthenticatedUser
from ..security import hash_password, verify_password
from .admin_events_service import emit_admin_event
from .assistant_service import build_assistant_access_map
from .audit_service import list_recent_audit_events, log_audit_event


def list_users(settings: Settings) -> list[dict[str, object]]:
    now = datetime.now(timezone.utc)
    live_cutoff = session_live_cutoff_iso(now=now)
    activity_cutoff = session_activity_cutoff_iso(now=now)
    with get_connection(settings) as connection:
        cleanup_expired_sessions(connection, now_iso=now.isoformat())
        connection.commit()
        rows = connection.execute(
            """
            SELECT id, username, role, enabled, created_at, updated_at, last_login_at
            FROM users
            ORDER BY lower(username) ASC
            """
        ).fetchall()
        session_rows = connection.execute(
            """
            SELECT
                user_id,
                created_at,
                expires_at,
                last_seen_at,
                last_activity_at,
                revoked_at,
                revoked_reason,
                cleanup_confirmed_at
            FROM sessions
            ORDER BY datetime(last_seen_at) DESC, id DESC
            """
        ).fetchall()
        assistant_access_by_user = build_assistant_access_map(
            connection,
            user_ids=[int(row["id"]) for row in rows],
        )
    status_by_user = _build_user_status_map(
        users=rows,
        sessions=session_rows,
        now_iso=now.isoformat(),
        live_cutoff_iso=live_cutoff,
        activity_cutoff_iso=activity_cutoff,
    )
    return [
        {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"] or "standard_user",
            "enabled": bool(row["enabled"]),
            "assistant_beta_enabled": bool(assistant_access_by_user[int(row["id"])]["assistant_beta_enabled"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_login_at": row["last_login_at"],
            "status_color": status_by_user[int(row["id"])]["status_color"],
            "status_label": status_by_user[int(row["id"])]["status_label"],
            "active_sessions": status_by_user[int(row["id"])]["active_sessions"],
            "last_seen_at": status_by_user[int(row["id"])]["last_seen_at"],
            "last_activity_at": status_by_user[int(row["id"])]["last_activity_at"],
        }
        for row in rows
    ]


def create_user(
    settings: Settings,
    *,
    username: str,
    password: str,
    role: str,
    enabled: bool,
    actor: AuthenticatedUser,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, object]:
    normalized_username = username.strip()
    if len(normalized_username) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username must be at least 3 characters",
        )
    if len(password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters",
        )
    if role not in {"admin", "standard_user"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported user role",
        )

    now = utcnow_iso()
    with get_connection(settings) as connection:
        try:
            cursor = connection.execute(
                """
                INSERT INTO users (username, password_hash, role, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_username,
                    hash_password(password),
                    role,
                    int(enabled),
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That username already exists",
            ) from exc
        connection.commit()
        user_id = int(cursor.lastrowid)

    payload = _get_user(settings, user_id=user_id)
    log_audit_event(
        settings,
        action="admin.user.create",
        outcome="success",
        user_id=actor.id,
        username=actor.username,
        role=actor.role,
        session_id=actor.session_id,
        target_type="user",
        target_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details={"username": normalized_username, "role": role, "enabled": enabled},
    )
    return payload


def update_user(
    settings: Settings,
    *,
    user_id: int,
    enabled: bool | None,
    role: str | None,
    current_admin_password: str | None,
    actor: AuthenticatedUser,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, object]:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT id, username, role, enabled, created_at, updated_at, last_login_at
            FROM users
            WHERE id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        next_role = role or row["role"] or "standard_user"
        next_enabled = bool(row["enabled"]) if enabled is None else bool(enabled)
        role_changed = next_role != (row["role"] or "standard_user")
        enabled_changed = next_enabled != bool(row["enabled"])
        if next_role not in {"admin", "standard_user"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported user role",
            )
        if actor.id == user_id and enabled is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot disable your own admin account",
            )
        if actor.id == user_id and role_changed and next_role != "admin":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Use self-delete instead of removing your own admin role",
            )
        if role_changed:
            _require_current_admin_password(
                connection,
                actor=actor,
                current_admin_password=current_admin_password,
            )
        if not next_enabled or next_role != "admin":
            admin_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM users
                WHERE role = 'admin' AND enabled = 1
                """
            ).fetchone()[0]
            is_current_enabled_admin = row["role"] == "admin" and bool(row["enabled"])
            if is_current_enabled_admin and int(admin_count) <= 1 and (not next_enabled or next_role != "admin"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Elvern must keep at least one enabled admin user",
                )

        now = utcnow_iso()
        if not role_changed and not enabled_changed:
            return _get_user_from_row(row)
        revoked_session_count = 0
        connection.execute(
            """
            UPDATE users
            SET role = ?, enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (next_role, int(next_enabled), now, user_id),
        )
        if not next_enabled:
            revoked_session_count = revoke_sessions_for_user_in_connection(
                connection,
                user_id=user_id,
                reason="user_disabled",
                now=now,
            )
        connection.commit()

    payload = _get_user(settings, user_id=user_id)
    if enabled_changed:
        emit_admin_event("user_enabled" if payload["enabled"] else "user_disabled", user_id=user_id)
    log_audit_event(
        settings,
        action="admin.user.update",
        outcome="success",
        user_id=actor.id,
        username=actor.username,
        role=actor.role,
        session_id=actor.session_id,
        target_type="user",
        target_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details={
            "enabled": payload["enabled"],
            "role": payload["role"],
            "revoked_session_count": revoked_session_count,
        },
    )
    return payload


def update_user_password(
    settings: Settings,
    *,
    user_id: int,
    new_password: str,
    current_admin_password: str,
    actor: AuthenticatedUser,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, object]:
    if len(new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters",
        )

    with get_connection(settings) as connection:
        _require_current_admin_password(
            connection,
            actor=actor,
            current_admin_password=current_admin_password,
        )
        row = connection.execute(
            """
            SELECT id, username, role, enabled, created_at, updated_at, last_login_at
            FROM users
            WHERE id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        now = utcnow_iso()
        connection.execute(
            """
            UPDATE users
            SET password_hash = ?, updated_at = ?
            WHERE id = ?
            """,
            (hash_password(new_password), now, user_id),
        )
        connection.commit()

    payload = _get_user(settings, user_id=user_id)
    log_audit_event(
        settings,
        action="admin.user.password.update",
        outcome="success",
        user_id=actor.id,
        username=actor.username,
        role=actor.role,
        session_id=actor.session_id,
        target_type="user",
        target_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details={"username": payload["username"]},
    )
    return payload


def delete_self(
    settings: Settings,
    *,
    actor: AuthenticatedUser,
    current_admin_password: str,
    confirm: bool,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    with get_connection(settings) as connection:
        _require_current_admin_password(
            connection,
            actor=actor,
            current_admin_password=current_admin_password,
        )
        other_admin_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE role = 'admin' AND enabled = 1 AND id != ?
            """,
            (actor.id,),
        ).fetchone()[0]
        if int(other_admin_count) < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Create another enabled admin before deleting your own account",
            )
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirm deletion before removing your own account",
        )
    revoke_sessions_for_user(settings, user_id=actor.id, reason="self_deleted")
    log_audit_event(
        settings,
        action="admin.user.self_delete",
        outcome="success",
        user_id=actor.id,
        username=actor.username,
        role=actor.role,
        session_id=actor.session_id,
        target_type="user",
        target_id=actor.id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    with get_connection(settings) as connection:
        connection.execute("DELETE FROM users WHERE id = ?", (actor.id,))
        connection.commit()


def list_active_sessions(settings: Settings) -> list[dict[str, object]]:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    live_cutoff = session_live_cutoff_iso(now=now)
    with get_connection(settings) as connection:
        cleanup_expired_sessions(connection, now_iso=now_iso)
        connection.commit()
        rows = connection.execute(
            """
            SELECT
                s.id,
                s.user_id,
                s.created_at,
                s.expires_at,
                s.last_seen_at,
                s.last_activity_at,
                s.user_agent,
                s.ip_address,
                u.username,
                u.role
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.revoked_at IS NULL
              AND s.expires_at > ?
              AND s.last_seen_at >= ?
              AND u.enabled = 1
            ORDER BY datetime(s.last_seen_at) DESC, datetime(COALESCE(s.last_activity_at, s.last_seen_at)) DESC, s.id DESC
            """,
            (now_iso, live_cutoff),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "user_id": row["user_id"],
            "username": row["username"],
            "role": row["role"] or "standard_user",
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "last_seen_at": row["last_seen_at"],
            "last_activity_at": row["last_activity_at"],
            "user_agent": row["user_agent"],
            "ip_address": row["ip_address"],
        }
        for row in rows
    ]


def revoke_session(
    settings: Settings,
    *,
    session_id: int,
    actor: AuthenticatedUser,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    revoked = revoke_session_by_id(settings, session_id=session_id, reason="admin_revoked")
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    log_audit_event(
        settings,
        action="admin.session.revoke",
        outcome="success",
        user_id=actor.id,
        username=actor.username,
        role=actor.role,
        session_id=actor.session_id,
        target_type="session",
        target_id=session_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def list_audit_log(settings: Settings, *, limit: int = 100) -> list[dict[str, object]]:
    return list_recent_audit_events(settings, limit=limit)


def _get_user(settings: Settings, *, user_id: int) -> dict[str, object]:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT id, username, role, enabled, created_at, updated_at, last_login_at
            FROM users
            WHERE id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _get_user_from_row(row)


def _get_user_from_row(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"] or "standard_user",
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_login_at": row["last_login_at"],
        "status_color": "red" if not bool(row["enabled"]) else "grey",
        "status_label": "Disabled" if not bool(row["enabled"]) else "Offline",
        "active_sessions": 0,
        "last_seen_at": None,
        "last_activity_at": None,
    }


def _require_current_admin_password(
    connection,
    *,
    actor: AuthenticatedUser,
    current_admin_password: str | None,
) -> None:
    if not current_admin_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current admin password is required for this action",
        )
    row = connection.execute(
        """
        SELECT password_hash, enabled, role
        FROM users
        WHERE id = ?
        LIMIT 1
        """,
        (actor.id,),
    ).fetchone()
    if row is None or not bool(row["enabled"]) or (row["role"] or "standard_user") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access is required",
        )
    if not verify_password(current_admin_password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current admin password is incorrect",
        )


def _build_user_status_map(*, users, sessions, now_iso: str, live_cutoff_iso: str, activity_cutoff_iso: str) -> dict[int, dict[str, object]]:
    status_by_user: dict[int, dict[str, object]] = {
        int(user["id"]): {
            "status_color": "red" if not bool(user["enabled"]) else "grey",
            "status_label": "Disabled" if not bool(user["enabled"]) else "Offline",
            "active_sessions": 0,
            "last_seen_at": None,
            "last_activity_at": None,
            "has_orange": False,
            "has_green": False,
            "has_yellow": False,
        }
        for user in users
    }

    for row in sessions:
        user_id = int(row["user_id"])
        current = status_by_user.get(user_id)
        if current is None or current["status_color"] == "red":
            continue

        last_seen_at = row["last_seen_at"]
        last_activity_at = row["last_activity_at"] or last_seen_at or row["created_at"]
        if last_seen_at and (
            current["last_seen_at"] is None or str(last_seen_at) > str(current["last_seen_at"])
        ):
            current["last_seen_at"] = str(last_seen_at)
        if last_activity_at and (
            current["last_activity_at"] is None or str(last_activity_at) > str(current["last_activity_at"])
        ):
            current["last_activity_at"] = str(last_activity_at)

        live_active = (
            row["revoked_at"] is None
            and str(row["expires_at"]) > now_iso
            and bool(last_seen_at)
            and str(last_seen_at) >= live_cutoff_iso
        )
        revocation_pending = (
            row["revoked_at"] is not None
            and row["cleanup_confirmed_at"] is None
            and bool(last_seen_at)
            and str(last_seen_at) >= live_cutoff_iso
        )

        if revocation_pending:
            current["has_orange"] = True
            continue
        if not live_active:
            continue

        current["active_sessions"] = int(current["active_sessions"]) + 1
        if last_activity_at and str(last_activity_at) >= activity_cutoff_iso:
            current["has_green"] = True
        else:
            current["has_yellow"] = True

    for current in status_by_user.values():
        if current["status_color"] == "red":
            continue
        if current["has_orange"]:
            current["status_color"] = "orange"
            current["status_label"] = "Revocation pending"
        elif current["has_green"]:
            current["status_color"] = "green"
            current["status_label"] = "Active now"
        elif current["has_yellow"]:
            current["status_color"] = "yellow"
            current["status_label"] = "Running in background"
        else:
            current["status_color"] = "grey"
            current["status_label"] = "Offline"
        current.pop("has_orange", None)
        current.pop("has_green", None)
        current.pop("has_yellow", None)
    return status_by_user
