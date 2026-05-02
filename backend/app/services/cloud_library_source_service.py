from __future__ import annotations

import ast
import json
from typing import Callable, Literal

from fastapi import HTTPException, status

from ..config import Settings
from ..db import get_connection, utcnow_iso
from ..models import AuthenticatedUser
from .google_drive_service import (
    PROVIDER_AUTH_REQUIRED_CODE,
    build_google_drive_provider_auth_required_detail,
    fetch_drive_resource_metadata,
    google_drive_enabled,
)


def _parse_cloud_source_error_detail(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return value
    if value in {None, ""}:
        return None
    text = str(value).strip()
    if not text:
        return None
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
        except (TypeError, ValueError, SyntaxError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return {"message": text}


def _cloud_source_status_fields(last_error: object) -> dict[str, object]:
    detail = _parse_cloud_source_error_detail(last_error)
    if detail is None:
        return {
            "sync_status": "never_synced",
            "provider_auth_required": False,
            "reconnect_required": False,
            "status_message": None,
            "stale_state_warning": None,
            "last_error_message": None,
        }

    provider_auth_required = str(detail.get("code") or "").strip() == PROVIDER_AUTH_REQUIRED_CODE
    reconnect_required = bool(provider_auth_required or detail.get("reauth_required"))
    if reconnect_required:
        return {
            "sync_status": "reconnect_required",
            "provider_auth_required": provider_auth_required,
            "reconnect_required": reconnect_required,
            "status_message": "Reconnect Google Drive.",
            "stale_state_warning": (
                "Cloud library was not refreshed and may be stale until Google Drive reconnects and the next sync succeeds."
            ),
            "last_error_message": str(
                detail.get("message")
                or detail.get("title")
                or detail.get("detail")
                or "Reconnect Google Drive to continue this action."
            ),
        }

    message = str(
        detail.get("message")
        or detail.get("title")
        or detail.get("detail")
        or "Cloud sync failed."
    ).strip()
    return {
        "sync_status": "error",
        "provider_auth_required": provider_auth_required,
        "reconnect_required": reconnect_required,
        "status_message": message or "Cloud sync failed.",
        "stale_state_warning": "Cloud items from this source may be stale until the next successful sync.",
        "last_error_message": message or "Cloud sync failed.",
    }


def _normalize_cloud_source_status(*, last_synced_at: object, last_error: object) -> dict[str, object]:
    status_fields = _cloud_source_status_fields(last_error)
    if status_fields["sync_status"] == "never_synced":
        if last_synced_at:
            return {
                **status_fields,
                "sync_status": "current",
            }
        return status_fields

    if status_fields["sync_status"] == "error" and last_synced_at:
        return {
            **status_fields,
            "sync_status": "stale",
            "status_message": status_fields["status_message"] or "Cloud sync failed.",
        }
    return status_fields


def _build_google_connection_status(
    *,
    enabled: bool,
    connected: bool,
    account_row,
    visible_sources: list[dict[str, object]],
) -> dict[str, object]:
    if not enabled:
        return {
            "connection_status": "not_configured",
            "provider_auth_required": False,
            "reconnect_required": False,
            "stale_state_warning": None,
            "status_message": "Google Drive OAuth setup is not configured on this server.",
        }
    if any(bool(source.get("reconnect_required")) for source in visible_sources):
        return {
            "connection_status": "reconnect_required",
            "provider_auth_required": any(bool(source.get("provider_auth_required")) for source in visible_sources),
            "reconnect_required": True,
            "stale_state_warning": (
                "Cloud library was not refreshed and may be stale until Google Drive reconnects and the next sync succeeds."
            ),
            "status_message": "Reconnect Google Drive. Cloud library may be stale until you reconnect.",
        }

    if not connected:
        return {
            "connection_status": "not_connected",
            "provider_auth_required": False,
            "reconnect_required": False,
            "stale_state_warning": None,
            "status_message": "Connect Google Drive to add or refresh cloud libraries.",
        }

    stale_sources = [source for source in visible_sources if source.get("sync_status") == "stale"]
    error_sources = [source for source in visible_sources if source.get("sync_status") == "error"]
    if stale_sources or error_sources:
        return {
            "connection_status": "error",
            "provider_auth_required": False,
            "reconnect_required": False,
            "stale_state_warning": "One or more cloud libraries may be stale until the next successful sync.",
            "status_message": "One or more Google Drive sources failed to refresh. Cloud library may be stale.",
        }

    account_label = None
    if account_row is not None:
        account_label = str(account_row["display_name"] or account_row["email"] or "").strip() or None
    if visible_sources:
        message = "Google Drive account connected. Cloud libraries are ready to refresh."
    else:
        message = "Google Drive account connected. Add a Drive folder or shared drive to start syncing cloud movies."
    if account_label:
        message = f"{message} Connected as {account_label}."
    return {
        "connection_status": "connected",
        "provider_auth_required": False,
        "reconnect_required": False,
        "stale_state_warning": None,
        "status_message": message,
    }


def get_google_drive_provider_auth_status_payload(
    settings: Settings,
    *,
    user: AuthenticatedUser,
    provider: str,
    get_access_token_by_account_id: Callable[..., str],
) -> dict[str, object]:
    if not google_drive_enabled(settings):
        return {
            "provider": provider,
            "provider_auth_required": False,
            "reconnect_required": False,
            "requirement": None,
            "sources_checked": 0,
        }

    with get_connection(settings) as connection:
        rows = connection.execute(
            """
            SELECT
                s.id,
                s.display_name,
                s.owner_user_id,
                s.is_shared,
                s.google_drive_account_id,
                CASE WHEN h.id IS NULL THEN 0 ELSE 1 END AS hidden_for_user
            FROM library_sources s
            LEFT JOIN user_hidden_library_sources h
              ON h.library_source_id = s.id
             AND h.user_id = ?
            WHERE s.provider = ?
              AND s.google_drive_account_id IS NOT NULL
              AND (
                s.owner_user_id = ?
                OR s.is_shared = 1
              )
            ORDER BY s.is_shared ASC, datetime(s.created_at) DESC, lower(s.display_name) ASC
            """,
            (user.id, provider, user.id),
        ).fetchall()

    visible_rows = [row for row in rows if not bool(row["hidden_for_user"])]
    for row in visible_rows:
        try:
            get_access_token_by_account_id(
                settings,
                google_account_id=int(row["google_drive_account_id"]),
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            if str(detail.get("code") or "") != PROVIDER_AUTH_REQUIRED_CODE:
                continue
            allow_reconnect = int(row["owner_user_id"]) == int(user.id)
            requirement = build_google_drive_provider_auth_required_detail(
                reason=str(detail.get("provider_reason") or detail.get("reason") or "reauth_required"),
                title=(
                    "Google Drive connection expired"
                    if allow_reconnect
                    else "Google Drive connection needs administrator attention"
                ),
                message=(
                    "Reconnect Google Drive to continue cloud playback."
                    if allow_reconnect
                    else "Ask an administrator to reconnect Google Drive to continue cloud playback."
                ),
                allow_reconnect=allow_reconnect,
                requires_admin=not allow_reconnect,
            )
            return {
                "provider": provider,
                "provider_auth_required": True,
                "reconnect_required": True,
                "requirement": requirement,
                "sources_checked": len(visible_rows),
            }

    return {
        "provider": provider,
        "provider_auth_required": False,
        "reconnect_required": False,
        "requirement": None,
        "sources_checked": len(visible_rows),
    }


def get_cloud_libraries_payload(
    settings: Settings,
    *,
    user: AuthenticatedUser,
    provider: str,
) -> dict[str, object]:
    with get_connection(settings) as connection:
        account_row = connection.execute(
            """
            SELECT email, display_name
            FROM google_drive_accounts
            WHERE user_id = ?
            LIMIT 1
            """,
            (user.id,),
        ).fetchone()
        rows = connection.execute(
            """
            SELECT
                s.id,
                s.provider,
                s.display_name,
                s.resource_type,
                s.resource_id,
                s.is_shared,
                s.created_at,
                s.last_synced_at,
                s.last_error,
                COUNT(m.id) AS item_count,
                CASE WHEN h.id IS NULL THEN 0 ELSE 1 END AS hidden_for_user,
                owner.username AS owner_username,
                account.email AS owner_account_email
            FROM library_sources s
            JOIN users owner
              ON owner.id = s.owner_user_id
            LEFT JOIN google_drive_accounts account
              ON account.id = s.google_drive_account_id
            LEFT JOIN media_items m
              ON m.library_source_id = s.id
            LEFT JOIN user_hidden_library_sources h
              ON h.library_source_id = s.id
             AND h.user_id = ?
            WHERE s.provider = ?
              AND (
                s.owner_user_id = ?
                OR s.is_shared = 1
              )
            GROUP BY
                s.id,
                s.provider,
                s.display_name,
                s.resource_type,
                s.resource_id,
                s.is_shared,
                s.created_at,
                s.last_synced_at,
                s.last_error,
                h.id,
                owner.username,
                account.email
            ORDER BY s.is_shared ASC, datetime(s.created_at) DESC, lower(s.display_name) ASC
            """,
            (user.id, provider, user.id),
        ).fetchall()

    my_libraries: list[dict[str, object]] = []
    shared_libraries: list[dict[str, object]] = []
    visible_sources: list[dict[str, object]] = []
    for row in rows:
        status_fields = _normalize_cloud_source_status(
            last_synced_at=row["last_synced_at"],
            last_error=row["last_error"],
        )
        payload = {
            "id": int(row["id"]),
            "provider": provider,
            "display_name": str(row["display_name"]),
            "resource_type": str(row["resource_type"]),
            "resource_id": str(row["resource_id"]),
            "source_label": "Cloud",
            "is_shared": bool(row["is_shared"]),
            "hidden_for_user": bool(row["hidden_for_user"]),
            "owner_username": row["owner_username"],
            "owner_account_email": row["owner_account_email"],
            "item_count": int(row["item_count"] or 0),
            "created_at": str(row["created_at"]),
            "last_synced_at": row["last_synced_at"],
            "last_error": row["last_error"],
            "sync_status": str(status_fields["sync_status"]),
            "provider_auth_required": bool(status_fields["provider_auth_required"]),
            "reconnect_required": bool(status_fields["reconnect_required"]),
            "status_message": status_fields["status_message"],
            "stale_state_warning": status_fields["stale_state_warning"],
            "last_error_message": status_fields["last_error_message"],
        }
        if not bool(row["hidden_for_user"]):
            visible_sources.append(payload)
        if bool(row["is_shared"]):
            shared_libraries.append(payload)
        else:
            my_libraries.append(payload)

    google_status = _build_google_connection_status(
        enabled=google_drive_enabled(settings),
        connected=account_row is not None,
        account_row=account_row,
        visible_sources=visible_sources,
    )

    return {
        "google": {
            "enabled": google_drive_enabled(settings),
            "connected": account_row is not None,
            "account_email": str(account_row["email"]) if account_row and account_row["email"] else None,
            "account_name": str(account_row["display_name"]) if account_row and account_row["display_name"] else None,
            "connection_status": google_status["connection_status"],
            "reconnect_required": bool(google_status["reconnect_required"]),
            "provider_auth_required": bool(google_status["provider_auth_required"]),
            "stale_state_warning": google_status["stale_state_warning"],
            "status_message": str(google_status["status_message"]),
        },
        "my_libraries": my_libraries,
        "shared_libraries": shared_libraries,
    }


def add_google_drive_library_source(
    settings: Settings,
    *,
    user: AuthenticatedUser,
    resource_type: Literal["folder", "shared_drive"],
    resource_id: str,
    shared: bool,
    provider: str,
    get_access_token: Callable[..., tuple[str, dict[str, object]]],
    sync_source: Callable[..., int],
) -> dict[str, object]:
    source_id = add_google_drive_library_source_record(
        settings,
        user=user,
        resource_type=resource_type,
        resource_id=resource_id,
        shared=shared,
        provider=provider,
        get_access_token=get_access_token,
    )
    sync_source(settings, source_id=source_id, raise_on_error=False)
    return _library_source_summary(
        settings,
        user=user,
        source_id=source_id,
        provider=provider,
    )


def add_google_drive_library_source_record(
    settings: Settings,
    *,
    user: AuthenticatedUser,
    resource_type: Literal["folder", "shared_drive"],
    resource_id: str,
    shared: bool,
    provider: str,
    get_access_token: Callable[..., tuple[str, dict[str, object]]],
) -> int:
    normalized_resource_id = resource_id.strip()
    if not normalized_resource_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google Drive resource ID is required.")
    if shared and user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admins can add shared libraries.")

    access_token, account_row = get_access_token(settings, user_id=user.id)
    with get_connection(settings) as connection:
        duplicate = connection.execute(
            """
            SELECT id, owner_user_id, is_shared
            FROM library_sources
            WHERE provider = ?
              AND resource_type = ?
              AND resource_id = ?
            LIMIT 1
            """,
            (provider, resource_type, normalized_resource_id),
        ).fetchone()
        if duplicate is not None:
            if bool(duplicate["is_shared"]) and int(duplicate["owner_user_id"]) != user.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This resource has already been added by your admin.",
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This Google Drive resource has already been added.",
            )

    metadata = fetch_drive_resource_metadata(
        access_token,
        resource_type=resource_type,
        resource_id=normalized_resource_id,
    )
    now = utcnow_iso()
    with get_connection(settings) as connection:
        cursor = connection.execute(
            """
            INSERT INTO library_sources (
                owner_user_id,
                provider,
                google_drive_account_id,
                resource_type,
                resource_id,
                display_name,
                is_shared,
                created_at,
                updated_at,
                last_synced_at,
                last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                user.id,
                provider,
                int(account_row["id"]),
                metadata["resource_type"],
                metadata["resource_id"],
                metadata["display_name"],
                int(shared),
                now,
                now,
                None,
            ),
        )
        source_id = int(cursor.lastrowid)
        connection.commit()
    return source_id


def hide_shared_library_source_for_user(
    settings: Settings,
    *,
    user: AuthenticatedUser,
    source_id: int,
    provider: str,
) -> None:
    with get_connection(settings) as connection:
        row = _require_shared_library_source_row(connection, source_id=source_id, provider=provider)
        connection.execute(
            """
            INSERT OR IGNORE INTO user_hidden_library_sources (user_id, library_source_id, hidden_at)
            VALUES (?, ?, ?)
            """,
            (user.id, source_id, utcnow_iso()),
        )
        connection.commit()


def show_shared_library_source_for_user(
    settings: Settings,
    *,
    user: AuthenticatedUser,
    source_id: int,
    provider: str,
) -> None:
    with get_connection(settings) as connection:
        _require_shared_library_source_row(connection, source_id=source_id, provider=provider)
        connection.execute(
            """
            DELETE FROM user_hidden_library_sources
            WHERE user_id = ? AND library_source_id = ?
            """,
            (user.id, source_id),
        )
        connection.commit()


def move_google_drive_library_source(
    settings: Settings,
    *,
    user: AuthenticatedUser,
    source_id: int,
    shared: bool,
    provider: str,
) -> dict[str, object]:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admins can move shared libraries.")

    now = utcnow_iso()
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT id, is_shared
            FROM library_sources
            WHERE id = ?
              AND provider = ?
              AND owner_user_id = ?
            LIMIT 1
            """,
            (source_id, provider, user.id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud library source not found.")
        if bool(row["is_shared"]) != bool(shared):
            connection.execute(
                """
                UPDATE library_sources
                SET is_shared = ?, updated_at = ?
                WHERE id = ?
                """,
                (int(shared), now, source_id),
            )
            connection.commit()

    return _library_source_summary(
        settings,
        user=user,
        source_id=source_id,
        provider=provider,
    )


def _library_source_summary(
    settings: Settings,
    *,
    user: AuthenticatedUser,
    source_id: int,
    provider: str,
) -> dict[str, object]:
    payload = get_cloud_libraries_payload(settings, user=user, provider=provider)
    for row in [*payload["my_libraries"], *payload["shared_libraries"]]:
        if int(row["id"]) == source_id:
            return row
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud library source not found.")


def _require_shared_library_source_row(connection, *, source_id: int, provider: str):
    row = connection.execute(
        """
        SELECT id
        FROM library_sources
        WHERE id = ? AND is_shared = 1 AND provider = ?
        LIMIT 1
        """,
        (source_id, provider),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared library source not found.")
    return row
