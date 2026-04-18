from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import HTTPException, status

from ..config import Settings
from ..db import get_connection, preserve_hidden_movie_keys_for_media_item, utcnow_iso
from ..media_scan import infer_title_and_year
from ..models import AuthenticatedUser
from ..security import generate_session_token
from .google_drive_service import (
    build_cloud_virtual_path,
    build_google_drive_authorization_url,
    build_google_drive_provider_auth_required_detail,
    fetch_drive_file_metadata,
    exchange_google_oauth_code,
    fetch_drive_file_resource_key,
    fetch_drive_resource_metadata,
    fetch_google_userinfo,
    get_google_token_expiry_iso,
    google_drive_enabled,
    list_drive_media_files,
    proxy_google_drive_file_response,
    refresh_google_access_token,
    require_google_drive_enabled,
)
from .app_settings_service import get_effective_google_drive_https_origin
from .library_service import get_media_item_record


GOOGLE_DRIVE_PROVIDER = "google_drive"
GOOGLE_STATE_TTL_MINUTES = 15


logger = logging.getLogger(__name__)


def _normalize_google_connect_return_path(return_path: str | None) -> str | None:
    candidate = str(return_path or "").strip()
    if not candidate:
        return None
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return None
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return None
    return urlunsplit(("", "", parsed.path, parsed.query, ""))


def _encode_google_connect_state_payload(*, state_token: str, return_path: str | None) -> str:
    payload = {"token": state_token}
    normalized_return_path = _normalize_google_connect_return_path(return_path)
    if normalized_return_path:
        payload["return_path"] = normalized_return_path
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"elvern:{encoded}"


def resolve_google_connect_state(state_token: str) -> dict[str, str | None]:
    candidate = str(state_token or "").strip()
    if not candidate.startswith("elvern:"):
        return {
            "state_token": candidate,
            "return_path": None,
        }
    encoded = candidate.split(":", 1)[1]
    padding = "=" * ((4 - (len(encoded) % 4)) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{encoded}{padding}".encode("ascii")).decode("utf-8")
        payload = json.loads(decoded)
    except Exception:  # noqa: BLE001
        return {
            "state_token": candidate,
            "return_path": None,
        }
    token = str(payload.get("token") or "").strip() or candidate
    return_path = _normalize_google_connect_return_path(payload.get("return_path"))
    return {
        "state_token": token,
        "return_path": return_path,
    }


def _load_cloud_media_item_provider_context(
    settings: Settings,
    *,
    user_id: int,
    item_id: int,
) -> dict[str, object] | None:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT
                m.id,
                COALESCE(m.source_kind, 'local') AS source_kind,
                s.owner_user_id,
                s.is_shared,
                account.id AS google_account_id
            FROM media_items m
            LEFT JOIN library_sources s
              ON s.id = m.library_source_id
            LEFT JOIN google_drive_accounts account
              ON account.id = s.google_drive_account_id
            LEFT JOIN user_hidden_library_sources h
              ON h.library_source_id = s.id
             AND h.user_id = ?
            WHERE m.id = ?
              AND (
                COALESCE(m.source_kind, 'local') = 'local'
                OR (
                    s.id IS NOT NULL
                AND h.id IS NULL
                AND (
                        s.owner_user_id = ?
                     OR s.is_shared = 1
                    )
                )
              )
            LIMIT 1
            """,
            (user_id, item_id, user_id),
        ).fetchone()
    return dict(row) if row is not None else None


def _current_user_can_manage_cloud_provider_connection(
    provider_context: dict[str, object],
    *,
    user_id: int,
) -> bool:
    if str(provider_context.get("source_kind") or "local") != "cloud":
        return False
    owner_user_id = int(provider_context.get("owner_user_id") or 0)
    google_account_id = int(provider_context.get("google_account_id") or 0)
    return owner_user_id > 0 and owner_user_id == user_id and google_account_id > 0


def get_cloud_libraries_payload(settings: Settings, *, user: AuthenticatedUser) -> dict[str, object]:
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
            (user.id, GOOGLE_DRIVE_PROVIDER, user.id),
        ).fetchall()

    my_libraries: list[dict[str, object]] = []
    shared_libraries: list[dict[str, object]] = []
    for row in rows:
        payload = {
            "id": int(row["id"]),
            "provider": GOOGLE_DRIVE_PROVIDER,
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
        }
        if bool(row["is_shared"]):
            shared_libraries.append(payload)
        else:
            my_libraries.append(payload)

    return {
        "google": {
            "enabled": google_drive_enabled(settings),
            "connected": account_row is not None,
            "account_email": str(account_row["email"]) if account_row and account_row["email"] else None,
            "account_name": str(account_row["display_name"]) if account_row and account_row["display_name"] else None,
        },
        "my_libraries": my_libraries,
        "shared_libraries": shared_libraries,
    }


def build_google_drive_connect_response(
    settings: Settings,
    *,
    user_id: int,
    return_path: str | None = None,
) -> dict[str, str]:
    require_google_drive_enabled(settings)
    state_token = generate_session_token()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=GOOGLE_STATE_TTL_MINUTES)
    with get_connection(settings) as connection:
        connection.execute(
            """
            INSERT INTO google_oauth_states (state_token, user_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (state_token, user_id, now.isoformat(), expires_at.isoformat()),
        )
        connection.commit()
    return {
        "authorization_url": build_google_drive_authorization_url(
            settings,
            state_token=_encode_google_connect_state_payload(
                state_token=state_token,
                return_path=return_path,
            ),
        ),
    }


def complete_google_drive_connect(
    settings: Settings,
    *,
    state_token: str,
    code: str,
) -> dict[str, object]:
    require_google_drive_enabled(settings)
    now_iso = utcnow_iso()
    state_context = resolve_google_connect_state(state_token)
    resolved_state_token = str(state_context["state_token"] or "").strip()
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT state_token, user_id
            FROM google_oauth_states
            WHERE state_token = ? AND expires_at > ?
            LIMIT 1
            """,
            (resolved_state_token, now_iso),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google Drive sign-in state expired.")
        user_id = int(row["user_id"])

    token_payload = exchange_google_oauth_code(settings, code=code)
    access_token = str(token_payload.get("access_token") or "")
    refresh_token = token_payload.get("refresh_token")
    if not access_token:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Google Drive did not return an access token.")
    userinfo = fetch_google_userinfo(access_token)
    google_account_id = str(userinfo.get("sub") or "")
    if not google_account_id:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Google Drive account details were incomplete.")

    access_token_expires_at = get_google_token_expiry_iso(token_payload.get("expires_in"))
    now = utcnow_iso()
    with get_connection(settings) as connection:
        existing = connection.execute(
            """
            SELECT refresh_token
            FROM google_drive_accounts
            WHERE user_id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if refresh_token:
            stored_refresh_token = str(refresh_token)
        elif existing and existing["refresh_token"]:
            stored_refresh_token = str(existing["refresh_token"])
        else:
            stored_refresh_token = ""
        if not stored_refresh_token:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Google Drive did not provide a refresh token. Please try connecting again.",
            )
        connection.execute(
            """
            INSERT INTO google_drive_accounts (
                user_id,
                google_account_id,
                email,
                display_name,
                refresh_token,
                access_token,
                access_token_expires_at,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                google_account_id = excluded.google_account_id,
                email = excluded.email,
                display_name = excluded.display_name,
                refresh_token = excluded.refresh_token,
                access_token = excluded.access_token,
                access_token_expires_at = excluded.access_token_expires_at,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                google_account_id,
                userinfo.get("email"),
                userinfo.get("name") or userinfo.get("email"),
                stored_refresh_token,
                access_token,
                access_token_expires_at,
                now,
                now,
            ),
        )
        connection.execute("DELETE FROM google_oauth_states WHERE state_token = ?", (resolved_state_token,))
        connection.commit()
    return {
        "user_id": user_id,
        "account_email": userinfo.get("email"),
        "account_name": userinfo.get("name") or userinfo.get("email"),
        "return_path": state_context["return_path"],
    }


def add_google_drive_library_source(
    settings: Settings,
    *,
    user: AuthenticatedUser,
    resource_type: Literal["folder", "shared_drive"],
    resource_id: str,
    shared: bool,
) -> dict[str, object]:
    normalized_resource_id = resource_id.strip()
    if not normalized_resource_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google Drive resource ID is required.")
    if shared and user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admins can add shared libraries.")

    access_token, account_row = get_google_drive_account_access_token(settings, user_id=user.id)
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
            (GOOGLE_DRIVE_PROVIDER, resource_type, normalized_resource_id),
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
                GOOGLE_DRIVE_PROVIDER,
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

    _sync_google_drive_library_source(settings, source_id=source_id, raise_on_error=False)
    return _library_source_summary(settings, user=user, source_id=source_id)


def sync_visible_google_drive_sources(settings: Settings, *, user_id: int) -> None:
    _sync_visible_google_drive_sources(settings, user_id=user_id)


def _sync_visible_google_drive_sources(settings: Settings, *, user_id: int) -> None:
    if not google_drive_enabled(settings):
        return
    with get_connection(settings) as connection:
        rows = connection.execute(
            """
            SELECT
                s.id,
                s.last_synced_at,
                s.last_error,
                COUNT(m.id) AS item_count
            FROM library_sources s
            LEFT JOIN media_items m
              ON m.library_source_id = s.id
             AND COALESCE(m.source_kind, 'local') = 'cloud'
            LEFT JOIN user_hidden_library_sources h
              ON h.library_source_id = s.id
             AND h.user_id = ?
            WHERE s.provider = ?
              AND h.id IS NULL
              AND (
                s.owner_user_id = ?
                OR s.is_shared = 1
              )
            GROUP BY s.id, s.last_synced_at, s.last_error
            """,
            (user_id, GOOGLE_DRIVE_PROVIDER, user_id),
        ).fetchall()
    for row in rows:
        if _should_resync_source(row):
            _sync_google_drive_library_source(
                settings,
                source_id=int(row["id"]),
                raise_on_error=False,
            )


def _should_resync_source(row) -> bool:
    return False


def sync_all_google_drive_sources(settings: Settings) -> dict[str, int]:
    with get_connection(settings) as connection:
        rows = connection.execute(
            """
            SELECT id
            FROM library_sources
            WHERE provider = ?
            ORDER BY id ASC
            """,
            (GOOGLE_DRIVE_PROVIDER,),
        ).fetchall()
    sources_synced = 0
    media_rows_written = 0
    for row in rows:
        source_count = _sync_google_drive_library_source(
            settings,
            source_id=int(row["id"]),
            raise_on_error=True,
        )
        sources_synced += 1
        media_rows_written += source_count
    return {
        "sources_synced": sources_synced,
        "media_rows_written": media_rows_written,
    }


def _sync_google_drive_library_source(
    settings: Settings,
    *,
    source_id: int,
    raise_on_error: bool,
) -> int:
    with get_connection(settings) as connection:
        source_row = connection.execute(
            """
            SELECT
                s.id,
                s.resource_type,
                s.resource_id,
                s.display_name,
                s.google_drive_account_id
            FROM library_sources s
            WHERE s.id = ?
              AND s.provider = ?
            LIMIT 1
            """,
            (source_id, GOOGLE_DRIVE_PROVIDER),
        ).fetchone()
    if source_row is None:
        if raise_on_error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud library source not found.")
        return 0

    try:
        google_account_id = int(source_row["google_drive_account_id"] or 0)
        if google_account_id <= 0:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Cloud source is missing its Google Drive connection.",
            )
        access_token = get_google_drive_account_access_token_by_account_id(
            settings,
            google_account_id=google_account_id,
        )
        metadata = fetch_drive_resource_metadata(
            access_token,
            resource_type=str(source_row["resource_type"]),
            resource_id=str(source_row["resource_id"]),
        )
        media_rows = list_drive_media_files(
            access_token,
            resource_type=str(source_row["resource_type"]),
            resource_id=str(source_row["resource_id"]),
            allowed_video_extensions=settings.allowed_video_extensions,
        )
        discovered_media_ids: set[str] = set()
        now = utcnow_iso()
        with get_connection(settings) as connection:
            for row in media_rows:
                discovered_media_ids.add(str(row["id"]))
                _upsert_cloud_media_item(
                    connection,
                    source_id=source_id,
                    resource_id=str(metadata["resource_id"]),
                    row=row,
                )
            stale_rows = connection.execute(
                """
                SELECT id, external_media_id
                FROM media_items
                WHERE COALESCE(source_kind, 'local') = 'cloud'
                  AND library_source_id = ?
                """,
                (source_id,),
            ).fetchall()
            stale_media_ids = [
                int(row["id"])
                for row in stale_rows
                if not discovered_media_ids
                or str(row["external_media_id"] or "") not in discovered_media_ids
            ]
            for stale_media_id in stale_media_ids:
                preserve_hidden_movie_keys_for_media_item(
                    connection,
                    media_item_id=stale_media_id,
                )
            if discovered_media_ids:
                placeholders = ",".join("?" for _ in discovered_media_ids)
                connection.execute(
                    f"""
                    DELETE FROM media_items
                    WHERE COALESCE(source_kind, 'local') = 'cloud'
                      AND library_source_id = ?
                      AND external_media_id NOT IN ({placeholders})
                    """,
                    (source_id, *sorted(discovered_media_ids)),
                )
            else:
                connection.execute(
                    """
                    DELETE FROM media_items
                    WHERE COALESCE(source_kind, 'local') = 'cloud'
                      AND library_source_id = ?
                    """,
                    (source_id,),
                )
            connection.execute(
                """
                UPDATE library_sources
                SET display_name = ?, updated_at = ?, last_synced_at = ?, last_error = NULL
                WHERE id = ?
                """,
                (
                    str(metadata["display_name"]),
                    now,
                    now,
                    source_id,
                ),
            )
            connection.commit()
        return len(media_rows)
    except HTTPException as exc:
        _set_cloud_library_sync_error(settings, source_id=source_id, message=str(exc.detail))
        if raise_on_error:
            raise
        return 0
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.exception("Cloud library sync failed for source %s", source_id)
        _set_cloud_library_sync_error(settings, source_id=source_id, message="Google Drive sync failed.")
        if raise_on_error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Google Drive sync failed.",
            ) from exc
        return 0


def _set_cloud_library_sync_error(settings: Settings, *, source_id: int, message: str) -> None:
    with get_connection(settings) as connection:
        connection.execute(
            """
            UPDATE library_sources
            SET updated_at = ?, last_error = ?
            WHERE id = ?
            """,
            (utcnow_iso(), message, source_id),
        )
        connection.commit()


def hide_shared_library_source_for_user(settings: Settings, *, user: AuthenticatedUser, source_id: int) -> None:
    with get_connection(settings) as connection:
        row = _require_shared_library_source_row(connection, source_id=source_id)
        connection.execute(
            """
            INSERT OR IGNORE INTO user_hidden_library_sources (user_id, library_source_id, hidden_at)
            VALUES (?, ?, ?)
            """,
            (user.id, source_id, utcnow_iso()),
        )
        connection.commit()


def show_shared_library_source_for_user(settings: Settings, *, user: AuthenticatedUser, source_id: int) -> None:
    with get_connection(settings) as connection:
        _require_shared_library_source_row(connection, source_id=source_id)
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
            (source_id, GOOGLE_DRIVE_PROVIDER, user.id),
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

    return _library_source_summary(settings, user=user, source_id=source_id)


def resolve_media_stream_target(
    settings: Settings,
    *,
    user_id: int,
    item_id: int,
) -> dict[str, object] | None:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT
                m.id,
                m.file_path,
                m.original_filename,
                m.source_kind,
                m.external_media_id,
                m.cloud_resource_key,
                s.id AS library_source_id,
                s.owner_user_id,
                s.is_shared,
                account.id AS google_account_id
            FROM media_items m
            LEFT JOIN library_sources s
              ON s.id = m.library_source_id
            LEFT JOIN google_drive_accounts account
              ON account.id = s.google_drive_account_id
            LEFT JOIN user_hidden_library_sources h
              ON h.library_source_id = s.id
             AND h.user_id = ?
            WHERE m.id = ?
              AND (
                COALESCE(m.source_kind, 'local') = 'local'
                OR (
                    s.id IS NOT NULL
                AND h.id IS NULL
                AND (
                        s.owner_user_id = ?
                     OR s.is_shared = 1
                    )
                )
              )
            LIMIT 1
            """,
            (user_id, item_id, user_id),
        ).fetchone()
        if row is None:
            return None
        if str(row["source_kind"] or "local") == "local":
            return {
                "source_kind": "local",
                "file_path": str(row["file_path"]),
                "original_filename": str(row["original_filename"]),
            }
        google_account_id = int(row["google_account_id"] or 0)
        row_payload = dict(row)
    if not google_account_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cloud source is missing its Google Drive connection.",
        )
    access_token = get_google_drive_account_access_token_by_account_id(settings, google_account_id=google_account_id)
    resource_key = str(row_payload["cloud_resource_key"] or "").strip() or None
    if not resource_key:
        resource_key = fetch_drive_file_resource_key(
            access_token,
            file_id=str(row_payload["external_media_id"]),
        )
        if resource_key:
            with get_connection(settings) as connection:
                connection.execute(
                    """
                    UPDATE media_items
                    SET cloud_resource_key = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (resource_key, utcnow_iso(), item_id),
                )
                connection.commit()
    return {
        "source_kind": "cloud",
        "file_id": str(row_payload["external_media_id"]),
        "original_filename": str(row_payload["original_filename"]),
        "resource_key": resource_key,
        "access_token": access_token,
    }


def build_cloud_stream_response(
    settings: Settings,
    *,
    user_id: int,
    item_id: int,
    range_header: str | None,
    stream_validator=None,
):
    target = resolve_media_stream_target(settings, user_id=user_id, item_id=item_id)
    if target is None:
        return None
    if target["source_kind"] == "local":
        return target
    return proxy_google_drive_file_response(
        target["access_token"],
        file_id=str(target["file_id"]),
        filename=str(target["original_filename"]),
        resource_key=target.get("resource_key"),
        range_header=range_header,
        stream_validator=stream_validator,
    )


def refresh_cloud_media_item_metadata(
    settings: Settings,
    *,
    item_id: int,
) -> dict[str, object] | None:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT
                m.id,
                m.original_filename,
                m.external_media_id,
                m.cloud_resource_key,
                m.library_source_id,
                account.id AS google_account_id
            FROM media_items m
            LEFT JOIN library_sources s
              ON s.id = m.library_source_id
            LEFT JOIN google_drive_accounts account
              ON account.id = s.google_drive_account_id
            WHERE m.id = ?
              AND COALESCE(m.source_kind, 'local') = 'cloud'
            LIMIT 1
            """,
            (item_id,),
        ).fetchone()
    if row is None:
        return None
    google_account_id = int(row["google_account_id"] or 0)
    external_media_id = str(row["external_media_id"] or "").strip()
    if google_account_id <= 0 or not external_media_id:
        return get_media_item_record(settings, item_id=item_id)
    access_token = get_google_drive_account_access_token_by_account_id(
        settings,
        google_account_id=google_account_id,
    )
    metadata = fetch_drive_file_metadata(
        access_token,
        file_id=external_media_id,
        resource_key=str(row["cloud_resource_key"] or "").strip() or None,
    )
    video_metadata = metadata.get("videoMediaMetadata") or {}
    duration_seconds = None
    try:
        duration_millis = video_metadata.get("durationMillis")
        if duration_millis is not None:
            duration_seconds = round(float(duration_millis) / 1000.0, 2)
    except (TypeError, ValueError):
        duration_seconds = None
    try:
        width = int(video_metadata.get("width")) if video_metadata.get("width") is not None else None
    except (TypeError, ValueError):
        width = None
    try:
        height = int(video_metadata.get("height")) if video_metadata.get("height") is not None else None
    except (TypeError, ValueError):
        height = None
    try:
        file_size = int(metadata.get("size") or 0)
    except (TypeError, ValueError):
        file_size = 0
    resource_key = str(metadata.get("resourceKey") or "").strip() or None
    modified_at = _parse_google_modified_time(metadata.get("modifiedTime"))
    now = utcnow_iso()
    with get_connection(settings) as connection:
        connection.execute(
            """
            UPDATE media_items
            SET cloud_mime_type = COALESCE(?, cloud_mime_type),
                cloud_resource_key = COALESCE(?, cloud_resource_key),
                file_size = CASE WHEN ? > 0 THEN ? ELSE file_size END,
                file_mtime = ?,
                duration_seconds = COALESCE(?, duration_seconds),
                width = COALESCE(?, width),
                height = COALESCE(?, height),
                updated_at = ?,
                last_scanned_at = ?
            WHERE id = ?
            """,
            (
                str(metadata.get("mimeType") or "").strip() or None,
                resource_key,
                file_size,
                file_size,
                modified_at,
                duration_seconds,
                width,
                height,
                now,
                now,
                item_id,
            ),
        )
        connection.commit()
    return get_media_item_record(settings, item_id=item_id)


def get_google_drive_account_access_token(settings: Settings, *, user_id: int) -> tuple[str, dict[str, object]]:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT id, refresh_token, access_token, access_token_expires_at
            FROM google_drive_accounts
            WHERE user_id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connect Google Drive before adding a cloud library.",
        )
    row_payload = dict(row)
    access_token, updated_row = _ensure_access_token(settings, row=row_payload)
    return access_token, updated_row


def get_google_drive_account_access_token_by_account_id(
    settings: Settings,
    *,
    google_account_id: int,
) -> str:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT id, refresh_token, access_token, access_token_expires_at
            FROM google_drive_accounts
            WHERE id = ?
            LIMIT 1
            """,
            (google_account_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Drive account is no longer available.",
        )
    access_token, _ = _ensure_access_token(settings, row=dict(row))
    return access_token


def _ensure_access_token(settings: Settings, *, row: dict[str, object]) -> tuple[str, dict[str, object]]:
    access_token = str(row.get("access_token") or "")
    access_token_expires_at = str(row.get("access_token_expires_at") or "")
    if access_token and access_token_expires_at:
        try:
            expires_at = datetime.fromisoformat(access_token_expires_at)
        except ValueError:
            expires_at = None
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at > datetime.now(timezone.utc) + timedelta(seconds=30):
                return access_token, row

    refreshed = refresh_google_access_token(settings, refresh_token=str(row["refresh_token"]))
    next_access_token = str(refreshed.get("access_token") or "")
    if not next_access_token:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Google Drive did not return a refreshed access token.",
        )
    access_token_expires_at = get_google_token_expiry_iso(refreshed.get("expires_in"))
    with get_connection(settings) as connection:
        connection.execute(
            """
            UPDATE google_drive_accounts
            SET access_token = ?, access_token_expires_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (next_access_token, access_token_expires_at, utcnow_iso(), int(row["id"])),
        )
        connection.commit()
    row["access_token"] = next_access_token
    row["access_token_expires_at"] = access_token_expires_at
    return next_access_token, row


def ensure_cloud_media_item_provider_access(
    settings: Settings,
    *,
    user_id: int,
    item_id: int,
) -> None:
    provider_context = _load_cloud_media_item_provider_context(
        settings,
        user_id=user_id,
        item_id=item_id,
    )
    if provider_context is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found.")
    if str(provider_context.get("source_kind") or "local") != "cloud":
        return
    try:
        target = resolve_media_stream_target(settings, user_id=user_id, item_id=item_id)
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict) and detail.get("code") == "provider_auth_required":
            allow_reconnect = _current_user_can_manage_cloud_provider_connection(
                provider_context,
                user_id=user_id,
            )
            if allow_reconnect:
                raise
            raise HTTPException(
                status_code=exc.status_code,
                detail=build_google_drive_provider_auth_required_detail(
                    reason=str(detail.get("provider_reason") or "reauth_required"),
                    title="Google Drive connection needs administrator attention",
                    message="Ask an administrator to reconnect Google Drive to continue this action.",
                    allow_reconnect=False,
                    requires_admin=True,
                ),
            ) from exc
        raise
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found.")


def build_google_connect_callback_redirect(
    settings: Settings,
    *,
    success: bool,
    message: str,
    return_path: str | None = None,
) -> str:
    base_origin = (get_effective_google_drive_https_origin(settings) or "").strip().rstrip("/")
    if not base_origin:
        base_origin = (settings.public_app_origin or "").strip().rstrip("/")
    if not base_origin:
        host = settings.frontend_host
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        base_origin = f"http://{host}:{settings.frontend_port}"
    normalized_return_path = _normalize_google_connect_return_path(return_path) or "/settings"
    parsed_return_path = urlsplit(normalized_return_path)
    query_items = [
        item
        for item in parsed_return_path.query.split("&")
        if item and not item.startswith("googleDriveStatus=") and not item.startswith("googleDriveMessage=")
    ]
    query_items.append(
        urlencode(
            {
                "googleDriveStatus": "connected" if success else "error",
                "googleDriveMessage": message,
            }
        )
    )
    merged_query = "&".join(query_items)
    return f"{base_origin}{urlunsplit(('', '', parsed_return_path.path, merged_query, ''))}"


def _library_source_summary(settings: Settings, *, user: AuthenticatedUser, source_id: int) -> dict[str, object]:
    payload = get_cloud_libraries_payload(settings, user=user)
    for row in [*payload["my_libraries"], *payload["shared_libraries"]]:
        if int(row["id"]) == source_id:
            return row
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cloud library source not found.")


def _require_shared_library_source_row(connection, *, source_id: int):
    row = connection.execute(
        """
        SELECT id
        FROM library_sources
        WHERE id = ? AND is_shared = 1 AND provider = ?
        LIMIT 1
        """,
        (source_id, GOOGLE_DRIVE_PROVIDER),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared library source not found.")
    return row


def _upsert_cloud_media_item(connection, *, source_id: int, resource_id: str, row: dict[str, object]) -> None:
    name = str(row.get("name") or row.get("id") or "Google Drive file")
    title = Path(name).stem or name
    inferred_title, inferred_year = infer_title_and_year(Path(name).stem or name)
    if inferred_title:
        title = inferred_title
    external_media_id = str(row["id"])
    cloud_mime_type = str(row.get("mimeType") or "")
    cloud_resource_key = str(row.get("resourceKey") or "").strip() or None
    series_folder_key = str(row.get("seriesFolderKey") or "").strip() or None
    series_folder_name = str(row.get("seriesFolderName") or "").strip() or None
    video_metadata = row.get("videoMediaMetadata") or {}
    duration_millis = video_metadata.get("durationMillis")
    duration_seconds = None
    try:
        if duration_millis is not None:
            duration_seconds = round(float(duration_millis) / 1000.0, 2)
    except (TypeError, ValueError):
        duration_seconds = None
    try:
        width = int(video_metadata.get("width")) if video_metadata.get("width") is not None else None
    except (TypeError, ValueError):
        width = None
    try:
        height = int(video_metadata.get("height")) if video_metadata.get("height") is not None else None
    except (TypeError, ValueError):
        height = None
    try:
        file_size = int(row.get("size") or 0)
    except (TypeError, ValueError):
        file_size = 0
    modified_at = _parse_google_modified_time(row.get("modifiedTime"))
    now = utcnow_iso()
    virtual_path = build_cloud_virtual_path(resource_id=resource_id, file_id=external_media_id, filename=name)
    connection.execute(
        """
        INSERT INTO media_items (
            title,
            original_filename,
            file_path,
            source_kind,
            library_source_id,
            external_media_id,
            cloud_mime_type,
            cloud_resource_key,
            series_folder_key,
            series_folder_name,
            file_size,
            file_mtime,
            duration_seconds,
            width,
            height,
            video_codec,
            audio_codec,
            container,
            year,
            created_at,
            updated_at,
            last_scanned_at
        ) VALUES (?, ?, ?, 'cloud', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            title = excluded.title,
            original_filename = excluded.original_filename,
            source_kind = 'cloud',
            library_source_id = excluded.library_source_id,
            external_media_id = excluded.external_media_id,
            cloud_mime_type = excluded.cloud_mime_type,
            cloud_resource_key = excluded.cloud_resource_key,
            series_folder_key = excluded.series_folder_key,
            series_folder_name = excluded.series_folder_name,
            file_size = excluded.file_size,
            file_mtime = excluded.file_mtime,
            duration_seconds = COALESCE(excluded.duration_seconds, media_items.duration_seconds),
            width = excluded.width,
            height = excluded.height,
            container = excluded.container,
            updated_at = excluded.updated_at,
            last_scanned_at = excluded.last_scanned_at
        """,
        (
            title,
            name,
            virtual_path,
            source_id,
            external_media_id,
            cloud_mime_type,
            cloud_resource_key,
            series_folder_key,
            series_folder_name,
            file_size,
            modified_at,
            duration_seconds,
            width,
            height,
            Path(name).suffix.lower().lstrip(".") or None,
            inferred_year,
            now,
            now,
            now,
        ),
    )


def _parse_google_modified_time(value: object) -> float:
    if not value:
        return datetime.now(timezone.utc).timestamp()
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc).timestamp()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.timestamp()
