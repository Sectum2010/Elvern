from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
from typing import Any

from fastapi import HTTPException, status

from ..config import Settings
from ..db import get_connection, utcnow_iso
from ..models import AuthenticatedUser
from ..security import generate_session_token, hash_password
from .audit_service import log_audit_event
from .library_service import get_media_item_detail


INVITE_CODE_LENGTH = 32
INVITE_CODE_TTL_MINUTES = 30
INVITE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*()-_=+[]{};:,.?"
INVITE_CODE_UPPERCASE = "ABCDEFGHJKLMNPQRSTUVWXYZ"
INVITE_CODE_NUMBERS = "23456789"
INVITE_CODE_SYMBOLS = "!@#$%^&*()-_=+[]{};:,.?"
PASSWORD_HELP_SUCCESS_MESSAGE = "Request sent. Expect feedback within the next 48 hours."
PASSWORD_HELP_RETENTION_DAYS = 30
PASSWORD_HELP_USER_COOLDOWN_SECONDS = 30 * 60
PASSWORD_HELP_DEVICE_COOLDOWN_SECONDS = 10 * 60
DOWNLOAD_ACCESS_NONE = "none"
DOWNLOAD_ACCESS_ALL = "all"
DOWNLOAD_ACCESS_SELECTED = "selected"
DOWNLOAD_ACCESS_MODES = {DOWNLOAD_ACCESS_NONE, DOWNLOAD_ACCESS_ALL, DOWNLOAD_ACCESS_SELECTED}
DOWNLOAD_SESSION_TTL_MINUTES = 10


def _secret_hash(settings: Settings, namespace: str, value: str) -> str:
    return hashlib.sha256(
        f"{namespace}\n{settings.session_secret}\n{value}".encode("utf-8")
    ).hexdigest()


def _requester_bucket_hash(settings: Settings, *, ip_address: str | None, user_agent: str | None) -> str:
    normalized_ip = (ip_address or "unknown").strip().lower() or "unknown"
    normalized_agent = " ".join((user_agent or "unknown").strip().lower().split()) or "unknown"
    return _secret_hash(settings, "password-help-device", f"{normalized_ip}\n{normalized_agent}")


def _generate_invite_code() -> str:
    required = [
        secrets.choice(INVITE_CODE_UPPERCASE),
        secrets.choice(INVITE_CODE_NUMBERS),
        secrets.choice(INVITE_CODE_SYMBOLS),
    ]
    remaining = [
        secrets.choice(INVITE_CODE_ALPHABET)
        for _ in range(INVITE_CODE_LENGTH - len(required))
    ]
    characters = required + remaining
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


def _serialize_invite_code(row, *, code: str | None = None) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "code": code,
        "created_by_user_id": int(row["created_by_user_id"]),
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "used_at": row["used_at"],
        "used_by_user_id": row["used_by_user_id"],
        "hidden_at": row["hidden_at"],
    }


def generate_invite_code(
    settings: Settings,
    *,
    actor: AuthenticatedUser,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, object]:
    code = _generate_invite_code()
    code_hash = _secret_hash(settings, "invite-code", code)
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    expires_at = (now_dt + timedelta(minutes=INVITE_CODE_TTL_MINUTES)).isoformat()
    with get_connection(settings) as connection:
        cursor = connection.execute(
            """
            INSERT INTO invite_codes (
                code_hash,
                created_by_user_id,
                created_at,
                expires_at
            ) VALUES (?, ?, ?, ?)
            """,
            (code_hash, actor.id, now, expires_at),
        )
        invite_id = int(cursor.lastrowid)
        row = connection.execute(
            """
            SELECT id, created_by_user_id, created_at, expires_at, used_at, used_by_user_id, hidden_at
            FROM invite_codes
            WHERE id = ?
            """,
            (invite_id,),
        ).fetchone()
        connection.commit()
    log_audit_event(
        settings,
        action="admin.invite_code.generate",
        outcome="success",
        user_id=actor.id,
        username=actor.username,
        role=actor.role,
        target_type="invite_code",
        target_id=invite_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details={"expires_at": expires_at},
    )
    return _serialize_invite_code(row, code=code)


def list_visible_invite_codes(settings: Settings) -> list[dict[str, object]]:
    with get_connection(settings) as connection:
        rows = connection.execute(
            """
            SELECT id, created_by_user_id, created_at, expires_at, used_at, used_by_user_id, hidden_at
            FROM invite_codes
            WHERE hidden_at IS NULL
              AND revoked_at IS NULL
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 50
            """,
        ).fetchall()
    return [_serialize_invite_code(row) for row in rows]


def hide_invite_code_display(
    settings: Settings,
    *,
    invite_id: int,
    actor: AuthenticatedUser,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        row = connection.execute(
            "SELECT id FROM invite_codes WHERE id = ?",
            (invite_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite code not found")
        connection.execute(
            """
            UPDATE invite_codes
            SET hidden_at = COALESCE(hidden_at, ?)
            WHERE id = ?
            """,
            (now, invite_id),
        )
        connection.commit()
    log_audit_event(
        settings,
        action="admin.invite_code.hide",
        outcome="success",
        user_id=actor.id,
        username=actor.username,
        role=actor.role,
        target_type="invite_code",
        target_id=invite_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def create_user_with_invite(
    settings: Settings,
    *,
    username: str,
    password: str,
    confirm_password: str,
    invite_code: str,
    ip_address: str | None,
    user_agent: str | None,
) -> AuthenticatedUser:
    normalized_username = username.strip()
    normalized_invite_code = invite_code.strip()
    now = utcnow_iso()
    if not normalized_invite_code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite code is invalid or expired")
    if password != confirm_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to create account with these details")

    code_hash = _secret_hash(settings, "invite-code", normalized_invite_code)
    with get_connection(settings) as connection:
        invite_row = connection.execute(
            """
            SELECT id, expires_at, used_at, revoked_at
            FROM invite_codes
            WHERE code_hash = ?
            LIMIT 1
            """,
            (code_hash,),
        ).fetchone()
        if (
            invite_row is None
            or invite_row["used_at"] is not None
            or invite_row["revoked_at"] is not None
            or str(invite_row["expires_at"]) <= now
        ):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite code is invalid or expired")
        if len(normalized_username) < 3 or len(password) < 8:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to create account with these details")
        existing = connection.execute(
            "SELECT id FROM users WHERE username = ?",
            (normalized_username,),
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Unable to create account with these details")
        cursor = connection.execute(
            """
            INSERT INTO users (username, password_hash, role, enabled, created_at, updated_at)
            VALUES (?, ?, 'standard_user', 1, ?, ?)
            """,
            (normalized_username, hash_password(password, settings), now, now),
        )
        user_id = int(cursor.lastrowid)
        connection.execute(
            """
            UPDATE invite_codes
            SET used_at = ?, used_by_user_id = ?
            WHERE id = ?
            """,
            (now, user_id, invite_row["id"]),
        )
        connection.commit()
    user = AuthenticatedUser(
        id=user_id,
        username=normalized_username,
        role="standard_user",
        enabled=True,
        assistant_beta_enabled=False,
    )
    log_audit_event(
        settings,
        action="auth.signup",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        target_type="invite_code",
        target_id=invite_row["id"],
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return user


def create_password_help_request(
    settings: Settings,
    *,
    username: str,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, str]:
    normalized_username = username.strip()
    if not normalized_username:
        return {"message": PASSWORD_HELP_SUCCESS_MESSAGE}
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    expires_at = (now_dt + timedelta(days=PASSWORD_HELP_RETENTION_DAYS)).isoformat()
    bucket_hash = _requester_bucket_hash(settings, ip_address=ip_address, user_agent=user_agent)
    user_cooldown_cutoff = (now_dt - timedelta(seconds=PASSWORD_HELP_USER_COOLDOWN_SECONDS)).isoformat()
    bucket_cooldown_cutoff = (now_dt - timedelta(seconds=PASSWORD_HELP_DEVICE_COOLDOWN_SECONDS)).isoformat()
    with get_connection(settings) as connection:
        user_row = connection.execute(
            """
            SELECT id
            FROM users
            WHERE username = ?
            LIMIT 1
            """,
            (normalized_username,),
        ).fetchone()
        if user_row is None:
            return {"message": PASSWORD_HELP_SUCCESS_MESSAGE}
        recent_user_request = connection.execute(
            """
            SELECT id
            FROM password_help_requests
            WHERE user_id = ?
              AND status = 'pending'
              AND created_at >= ?
            LIMIT 1
            """,
            (user_row["id"], user_cooldown_cutoff),
        ).fetchone()
        recent_bucket_request = connection.execute(
            """
            SELECT id
            FROM password_help_requests
            WHERE requester_bucket_hash = ?
              AND created_at >= ?
            LIMIT 1
            """,
            (bucket_hash, bucket_cooldown_cutoff),
        ).fetchone()
        if recent_user_request is None and recent_bucket_request is None:
            connection.execute(
                """
                INSERT INTO password_help_requests (
                    username_snapshot,
                    user_id,
                    requester_bucket_hash,
                    status,
                    created_at,
                    updated_at,
                    expires_at
                ) VALUES (?, ?, ?, 'pending', ?, ?, ?)
                """,
                (normalized_username, user_row["id"], bucket_hash, now, now, expires_at),
            )
            connection.commit()
        else:
            connection.commit()
    return {"message": PASSWORD_HELP_SUCCESS_MESSAGE}


def list_password_help_requests(settings: Settings) -> list[dict[str, object]]:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        rows = connection.execute(
            """
            SELECT id, username_snapshot, user_id, created_at, expires_at, status
            FROM password_help_requests
            WHERE status = 'pending'
              AND expires_at > ?
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 100
            """,
            (now,),
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "username_snapshot": row["username_snapshot"],
            "user_id": int(row["user_id"]),
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "status": row["status"],
        }
        for row in rows
    ]


def dismiss_password_help_request(
    settings: Settings,
    *,
    request_id: int,
    confirm: bool,
    actor: AuthenticatedUser,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    if not confirm:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Confirm dismissal before closing the request")
    now = utcnow_iso()
    with get_connection(settings) as connection:
        row = connection.execute(
            "SELECT id FROM password_help_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Password help request not found")
        connection.execute(
            """
            UPDATE password_help_requests
            SET status = 'dismissed',
                dismissed_at = ?,
                dismissed_by_user_id = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (now, actor.id, now, request_id),
        )
        connection.commit()
    log_audit_event(
        settings,
        action="admin.password_help.dismiss",
        outcome="success",
        user_id=actor.id,
        username=actor.username,
        role=actor.role,
        target_type="password_help_request",
        target_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def _serialize_download_movie(row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "title": row["title"],
        "original_filename": row["original_filename"],
        "source_kind": row["source_kind"] or "local",
        "file_size": int(row["file_size"] or 0),
        "year": row["year"],
    }


def get_download_access_for_user(settings: Settings, *, user_id: int) -> dict[str, object]:
    with get_connection(settings) as connection:
        target_user = connection.execute(
            "SELECT id, role FROM users WHERE id = ? LIMIT 1",
            (user_id,),
        ).fetchone()
        if target_user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        grant = connection.execute(
            """
            SELECT user_id, access_mode, updated_at, updated_by_user_id
            FROM download_access_grants
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        selected_rows = connection.execute(
            """
            SELECT m.id, m.title, m.original_filename, m.source_kind, m.file_size, m.year
            FROM download_access_items d
            JOIN media_items m ON m.id = d.media_item_id
            WHERE d.user_id = ?
            ORDER BY lower(m.title) ASC, m.id ASC
            """,
            (user_id,),
        ).fetchall()
    default_access_mode = (
        DOWNLOAD_ACCESS_ALL
        if (target_user["role"] or "standard_user") == "admin"
        else DOWNLOAD_ACCESS_NONE
    )
    access_mode = grant["access_mode"] if grant else default_access_mode
    return {
        "user_id": user_id,
        "access_mode": access_mode,
        "selected_items": [_serialize_download_movie(row) for row in selected_rows],
        "updated_at": grant["updated_at"] if grant else None,
        "updated_by_user_id": grant["updated_by_user_id"] if grant else None,
    }


def update_download_access_for_user(
    settings: Settings,
    *,
    user_id: int,
    access_mode: str,
    media_item_ids: list[int],
    actor: AuthenticatedUser,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, object]:
    normalized_mode = access_mode if access_mode in DOWNLOAD_ACCESS_MODES else DOWNLOAD_ACCESS_NONE
    selected_ids = sorted({int(item_id) for item_id in media_item_ids if int(item_id) > 0})
    if normalized_mode != DOWNLOAD_ACCESS_SELECTED:
        selected_ids = []
    now = utcnow_iso()
    with get_connection(settings) as connection:
        target_user = connection.execute(
            "SELECT id FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if target_user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        if selected_ids:
            placeholders = ",".join("?" for _ in selected_ids)
            existing_ids = {
                int(row["id"])
                for row in connection.execute(
                    f"SELECT id FROM media_items WHERE id IN ({placeholders})",  # nosec B608 - placeholders generated from validated integer ids
                    selected_ids,
                ).fetchall()
            }
            selected_ids = [item_id for item_id in selected_ids if item_id in existing_ids]
        connection.execute(
            """
            INSERT INTO download_access_grants (user_id, access_mode, created_at, updated_at, updated_by_user_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                access_mode = excluded.access_mode,
                updated_at = excluded.updated_at,
                updated_by_user_id = excluded.updated_by_user_id
            """,
            (user_id, normalized_mode, now, now, actor.id),
        )
        connection.execute("DELETE FROM download_access_items WHERE user_id = ?", (user_id,))
        for media_item_id in selected_ids:
            connection.execute(
                """
                INSERT INTO download_access_items (user_id, media_item_id, granted_by_user_id, granted_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, media_item_id, actor.id, now),
            )
        if normalized_mode == DOWNLOAD_ACCESS_NONE:
            connection.execute(
                """
                UPDATE download_sessions
                SET revoked_at = COALESCE(revoked_at, ?), last_error = 'download_access_revoked'
                WHERE user_id = ? AND revoked_at IS NULL AND completed_at IS NULL
                """,
                (now, user_id),
            )
        connection.commit()
    log_audit_event(
        settings,
        action="admin.download_access.update",
        outcome="success",
        user_id=actor.id,
        username=actor.username,
        role=actor.role,
        target_type="user",
        target_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details={"access_mode": normalized_mode, "media_item_ids": selected_ids},
    )
    return get_download_access_for_user(settings, user_id=user_id)


def is_item_download_allowed(settings: Settings, *, user_id: int, item_id: int) -> bool:
    detail = get_media_item_detail(settings, user_id=user_id, item_id=item_id, allow_globally_hidden=False)
    if detail is None or bool(detail.get("hidden_for_user")) or bool(detail.get("hidden_globally")):
        return False
    with get_connection(settings) as connection:
        grant = connection.execute(
            """
            SELECT u.role, g.access_mode
            FROM users u
            LEFT JOIN download_access_grants g ON g.user_id = u.id
            WHERE u.id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if grant is None:
            return False
        access_mode = grant["access_mode"] if grant["access_mode"] is not None else (
            DOWNLOAD_ACCESS_ALL
            if (grant["role"] or "standard_user") == "admin"
            else DOWNLOAD_ACCESS_NONE
        )
        if access_mode == DOWNLOAD_ACCESS_NONE:
            return False
        if access_mode == DOWNLOAD_ACCESS_ALL:
            return True
        selected = connection.execute(
            """
            SELECT 1
            FROM download_access_items
            WHERE user_id = ? AND media_item_id = ?
            LIMIT 1
            """,
            (user_id, item_id),
        ).fetchone()
        return selected is not None


def _get_download_item_detail(settings: Settings, *, user_id: int, item_id: int) -> dict[str, Any]:
    detail = get_media_item_detail(settings, user_id=user_id, item_id=item_id, allow_globally_hidden=False)
    if detail is None or bool(detail.get("hidden_for_user")) or bool(detail.get("hidden_globally")):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Movie not found")
    if not is_item_download_allowed(settings, user_id=user_id, item_id=item_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Download access is not enabled for this movie")
    return detail


def create_download_session(
    settings: Settings,
    *,
    user: AuthenticatedUser,
    item_id: int,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, object]:
    detail = _get_download_item_detail(settings, user_id=user.id, item_id=item_id)
    token = generate_session_token()
    token_hash = _secret_hash(settings, "download-session", token)
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    expires_at = (now_dt + timedelta(minutes=DOWNLOAD_SESSION_TTL_MINUTES)).isoformat()
    with get_connection(settings) as connection:
        cursor = connection.execute(
            """
            INSERT INTO download_sessions (
                session_token_hash,
                user_id,
                media_item_id,
                auth_session_id,
                created_at,
                expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (token_hash, user.id, item_id, user.session_id, now, expires_at),
        )
        connection.commit()
        session_id = int(cursor.lastrowid)
    log_audit_event(
        settings,
        action="download.started",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        media_item_id=item_id,
        session_id=user.session_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    download_filename = safe_download_filename(str(detail.get("original_filename") or detail.get("title") or "movie"))
    return {
        "session_id": session_id,
        "download_url": f"/api/download/sessions/{token}",
        "controlled_download_url": f"/api/download/session-stream/{session_id}",
        "controlled_complete_url": f"/api/download/session-stream/{session_id}/complete",
        "controlled_failed_url": f"/api/download/session-stream/{session_id}/failed",
        "controlled_terminate_url": f"/api/download/session-stream/{session_id}/terminate",
        "session_token": token,
        "title": str(detail["title"]),
        "download_filename": download_filename,
        "original_filename": download_filename,
        "file_size": int(detail["file_size"] or 0),
        "expires_at": expires_at,
    }


def get_download_filename_for_item(settings: Settings, *, user_id: int, item_id: int) -> str:
    detail = _get_download_item_detail(settings, user_id=user_id, item_id=item_id)
    return safe_download_filename(str(detail.get("original_filename") or detail.get("title") or "movie"))


def validate_download_session(
    settings: Settings,
    *,
    token: str,
    user: AuthenticatedUser,
    session_id: int | None = None,
    allow_expired_download_session: bool = False,
) -> int:
    now = utcnow_iso()
    token_hash = _secret_hash(settings, "download-session", token)
    with get_connection(settings) as connection:
        query = """
            SELECT
                d.id,
                d.user_id,
                d.media_item_id,
                d.auth_session_id,
                d.expires_at,
                d.revoked_at,
                d.completed_at,
                u.enabled AS user_enabled,
                s.expires_at AS auth_session_expires_at,
                s.revoked_at AS auth_session_revoked_at
            FROM download_sessions d
            JOIN users u ON u.id = d.user_id
            LEFT JOIN sessions s ON s.id = d.auth_session_id
            WHERE d.session_token_hash = ?
            """
        params: list[object] = [token_hash]
        if session_id is not None:
            query += " AND d.id = ?"
            params.append(int(session_id))
        query += " LIMIT 1"
        row = connection.execute(
            query,
            params,
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Download session not found")
        if int(row["user_id"]) != int(user.id) or (
            row["auth_session_id"] is not None and int(row["auth_session_id"]) != int(user.session_id or 0)
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Download session is not valid for this session")
        if row["revoked_at"] is not None or row["completed_at"] is not None or (
            not allow_expired_download_session and str(row["expires_at"]) <= now
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Download session is no longer active")
        if not bool(row["user_enabled"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")
        if row["auth_session_id"] is not None and (
            row["auth_session_revoked_at"] is not None
            or row["auth_session_expires_at"] is None
            or str(row["auth_session_expires_at"]) <= now
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Auth session is no longer active")
    if not is_item_download_allowed(settings, user_id=user.id, item_id=int(row["media_item_id"])):
        mark_download_session_failed(
            settings,
            token=token,
            user=user,
            message="download_access_revoked",
            audit_action="download.revoked",
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Download access is no longer available")
    return int(row["media_item_id"])


def is_download_session_still_authorized(
    settings: Settings,
    *,
    token: str,
    user: AuthenticatedUser,
    session_id: int | None = None,
) -> bool:
    try:
        validate_download_session(
            settings,
            token=token,
            user=user,
            session_id=session_id,
            allow_expired_download_session=True,
        )
    except HTTPException:
        return False
    return True


def mark_download_session_completed(
    settings: Settings,
    *,
    token: str,
    user: AuthenticatedUser,
    session_id: int | None = None,
) -> None:
    token_hash = _secret_hash(settings, "download-session", token)
    now = utcnow_iso()
    with get_connection(settings) as connection:
        query = """
            SELECT id, media_item_id
            FROM download_sessions
            WHERE session_token_hash = ? AND user_id = ?
            """
        params: list[object] = [token_hash, user.id]
        if session_id is not None:
            query += " AND id = ?"
            params.append(int(session_id))
        row = connection.execute(query, params).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Download session not found")
        connection.execute(
            """
            UPDATE download_sessions
            SET completed_at = COALESCE(completed_at, ?)
            WHERE id = ?
            """,
            (now, row["id"]),
        )
        connection.commit()
    log_audit_event(
        settings,
        action="download.completed",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        media_item_id=int(row["media_item_id"]),
        session_id=user.session_id,
    )


def mark_download_session_failed(
    settings: Settings,
    *,
    token: str,
    user: AuthenticatedUser,
    message: str | None,
    audit_action: str = "download.failed",
    session_id: int | None = None,
) -> None:
    token_hash = _secret_hash(settings, "download-session", token)
    now = utcnow_iso()
    with get_connection(settings) as connection:
        query = """
            SELECT id, media_item_id
            FROM download_sessions
            WHERE session_token_hash = ? AND user_id = ?
            """
        params: list[object] = [token_hash, user.id]
        if session_id is not None:
            query += " AND id = ?"
            params.append(int(session_id))
        row = connection.execute(query, params).fetchone()
        if row is None:
            return
        connection.execute(
            """
            UPDATE download_sessions
            SET failed_at = COALESCE(failed_at, ?),
                last_error = ?
            WHERE id = ?
            """,
            (now, message or "download_failed", row["id"]),
        )
        connection.commit()
    log_audit_event(
        settings,
        action=audit_action,
        outcome="failure" if audit_action == "download.failed" else "success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        media_item_id=int(row["media_item_id"]),
        session_id=user.session_id,
        details={"message": message or "download_failed"},
    )


def mark_download_session_terminated(
    settings: Settings,
    *,
    token: str,
    user: AuthenticatedUser,
    ip_address: str | None = None,
    user_agent: str | None = None,
    session_id: int | None = None,
) -> None:
    token_hash = _secret_hash(settings, "download-session", token)
    now = utcnow_iso()
    with get_connection(settings) as connection:
        query = """
            SELECT id, user_id, media_item_id, auth_session_id
            FROM download_sessions
            WHERE session_token_hash = ?
            """
        params: list[object] = [token_hash]
        if session_id is not None:
            query += " AND id = ?"
            params.append(int(session_id))
        query += " LIMIT 1"
        row = connection.execute(query, params).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Download session not found")
        if int(row["user_id"]) != int(user.id) or (
            row["auth_session_id"] is not None and int(row["auth_session_id"]) != int(user.session_id or 0)
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Download session is not valid for this session")
        connection.execute(
            """
            UPDATE download_sessions
            SET revoked_at = COALESCE(revoked_at, ?),
                last_error = ?
            WHERE id = ?
            """,
            (now, "download_terminated", row["id"]),
        )
        connection.commit()
    log_audit_event(
        settings,
        action="download.terminated",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        media_item_id=int(row["media_item_id"]),
        session_id=user.session_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def safe_download_filename(value: str | None) -> str:
    filename = PurePath(value or "movie").name.strip()
    return filename or "movie"
