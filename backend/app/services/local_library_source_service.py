from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException, status

from ..config import Settings
from ..db import get_connection, utcnow_iso


LOCAL_FILESYSTEM_PROVIDER = "local_filesystem"
LOCAL_LIBRARY_RESOURCE_TYPE = "local_root"
SHARED_LOCAL_LIBRARY_RESOURCE_ID = "shared_default"
SHARED_LOCAL_LIBRARY_DISPLAY_NAME = "Shared Local Library"


def build_private_local_library_resource_id(*, user_id: int) -> str:
    return f"user_private:{user_id}"


def shared_local_library_bootstrap_path(settings: Settings) -> Path:
    return settings.media_root.resolve()


def normalize_local_library_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def get_effective_shared_local_library_path(
    settings: Settings,
    *,
    connection: sqlite3.Connection | None = None,
) -> Path:
    if connection is not None:
        return _get_effective_shared_local_library_path(connection, settings=settings)

    with get_connection(settings) as owned_connection:
        return _get_effective_shared_local_library_path(owned_connection, settings=settings)


def validate_shared_local_library_path(
    settings: Settings,
    *,
    value: str | None,
) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return normalize_local_library_path(shared_local_library_bootstrap_path(settings))

    parsed = urlsplit(candidate)
    if parsed.scheme:
        if parsed.scheme.lower() != "file":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Shared local library path must be an absolute Linux directory path or file:// URI.",
            )
        if parsed.netloc and parsed.netloc.lower() not in {"", "localhost"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Shared local library path must resolve to a local directory on this host.",
            )
        candidate_path = Path(unquote(parsed.path or "")).expanduser()
    else:
        candidate_path = Path(candidate).expanduser()

    if not candidate_path.is_absolute():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Shared local library path must be an absolute Linux directory path.",
        )

    normalized_path = candidate_path.resolve()
    if not normalized_path.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Shared local library path does not exist on this host.",
        )
    if not normalized_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Shared local library path must be a directory.",
        )
    if not os.access(normalized_path, os.R_OK | os.X_OK):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Shared local library path must be a readable directory.",
        )
    return str(normalized_path)


def ensure_shared_local_library_source(
    settings: Settings,
    *,
    connection: sqlite3.Connection | None = None,
) -> int:
    if connection is not None:
        return _ensure_shared_local_library_source(connection, settings=settings)

    with get_connection(settings) as owned_connection:
        source_id = _ensure_shared_local_library_source(owned_connection, settings=settings)
        owned_connection.commit()
    return source_id


def ensure_current_shared_local_source_binding(
    settings: Settings,
    *,
    connection: sqlite3.Connection | None = None,
) -> int:
    if connection is not None:
        return _ensure_current_shared_local_source_binding(connection, settings=settings)

    with get_connection(settings) as owned_connection:
        source_id = _ensure_current_shared_local_source_binding(owned_connection, settings=settings)
        owned_connection.commit()
    return source_id


def bind_unassigned_local_media_items_to_shared_source(
    connection: sqlite3.Connection,
    *,
    shared_source_id: int,
    shared_local_path: str | Path,
) -> int:
    normalized_root = normalize_local_library_path(shared_local_path)
    prefix_pattern = "/%" if normalized_root == "/" else f"{normalized_root.rstrip('/')}/%"
    cursor = connection.execute(
        """
        UPDATE media_items
        SET library_source_id = ?
        WHERE COALESCE(source_kind, 'local') = 'local'
          AND library_source_id IS NULL
          AND (
            file_path = ?
            OR file_path LIKE ?
          )
        """,
        (shared_source_id, normalized_root, prefix_pattern),
    )
    return int(cursor.rowcount or 0)


def update_shared_local_library_path(
    settings: Settings,
    *,
    value: str | None,
    connection: sqlite3.Connection | None = None,
) -> str:
    normalized_path = validate_shared_local_library_path(settings, value=value)
    if connection is not None:
        return _update_shared_local_library_path(connection, settings=settings, normalized_path=normalized_path)

    with get_connection(settings) as owned_connection:
        updated = _update_shared_local_library_path(
            owned_connection,
            settings=settings,
            normalized_path=normalized_path,
        )
        owned_connection.commit()
    return updated


def purge_shared_local_media_items(
    connection: sqlite3.Connection,
    *,
    shared_source_id: int,
) -> int:
    cursor = connection.execute(
        """
        DELETE FROM media_items
        WHERE COALESCE(source_kind, 'local') = 'local'
          AND (
            library_source_id = ?
            OR library_source_id IS NULL
          )
        """,
        (shared_source_id,),
    )
    return int(cursor.rowcount or 0)


def _ensure_shared_local_library_source(
    connection: sqlite3.Connection,
    *,
    settings: Settings,
) -> int:
    owner_user_id = _resolve_shared_local_source_owner_user_id(connection, settings=settings)
    local_path = normalize_local_library_path(
        _get_effective_shared_local_library_path(connection, settings=settings)
    )
    now = utcnow_iso()
    existing = connection.execute(
        """
        SELECT
            id,
            owner_user_id,
            display_name,
            local_path,
            is_shared
        FROM library_sources
        WHERE provider = ?
          AND resource_type = ?
          AND resource_id = ?
        LIMIT 1
        """,
        (
            LOCAL_FILESYSTEM_PROVIDER,
            LOCAL_LIBRARY_RESOURCE_TYPE,
            SHARED_LOCAL_LIBRARY_RESOURCE_ID,
        ),
    ).fetchone()
    if existing is None:
        cursor = connection.execute(
            """
            INSERT INTO library_sources (
                owner_user_id,
                provider,
                google_drive_account_id,
                resource_type,
                resource_id,
                display_name,
                local_path,
                is_shared,
                created_at,
                updated_at,
                last_synced_at,
                last_error
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, 1, ?, ?, NULL, NULL)
            """,
            (
                owner_user_id,
                LOCAL_FILESYSTEM_PROVIDER,
                LOCAL_LIBRARY_RESOURCE_TYPE,
                SHARED_LOCAL_LIBRARY_RESOURCE_ID,
                SHARED_LOCAL_LIBRARY_DISPLAY_NAME,
                local_path,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    if (
        int(existing["owner_user_id"]) != owner_user_id
        or str(existing["display_name"]) != SHARED_LOCAL_LIBRARY_DISPLAY_NAME
        or not bool(existing["is_shared"])
    ):
        connection.execute(
            """
            UPDATE library_sources
            SET owner_user_id = ?,
                display_name = ?,
                local_path = ?,
                is_shared = 1,
                updated_at = ?
            WHERE id = ?
            """,
            (
                owner_user_id,
                SHARED_LOCAL_LIBRARY_DISPLAY_NAME,
                normalize_local_library_path(existing["local_path"] or local_path),
                now,
                int(existing["id"]),
            ),
        )
    return int(existing["id"])


def _ensure_current_shared_local_source_binding(
    connection: sqlite3.Connection,
    *,
    settings: Settings,
) -> int:
    source_id = _ensure_shared_local_library_source(connection, settings=settings)
    bind_unassigned_local_media_items_to_shared_source(
        connection,
        shared_source_id=source_id,
        shared_local_path=_get_effective_shared_local_library_path(connection, settings=settings),
    )
    return source_id


def _get_effective_shared_local_library_path(
    connection: sqlite3.Connection,
    *,
    settings: Settings,
) -> Path:
    existing = connection.execute(
        """
        SELECT local_path
        FROM library_sources
        WHERE provider = ?
          AND resource_type = ?
          AND resource_id = ?
        LIMIT 1
        """,
        (
            LOCAL_FILESYSTEM_PROVIDER,
            LOCAL_LIBRARY_RESOURCE_TYPE,
            SHARED_LOCAL_LIBRARY_RESOURCE_ID,
        ),
    ).fetchone()
    local_path = str(existing["local_path"] or "").strip() if existing is not None else ""
    if local_path:
        return Path(local_path).expanduser().resolve()
    return shared_local_library_bootstrap_path(settings)


def _update_shared_local_library_path(
    connection: sqlite3.Connection,
    *,
    settings: Settings,
    normalized_path: str,
) -> str:
    source_id = _ensure_shared_local_library_source(connection, settings=settings)
    connection.execute(
        """
        UPDATE library_sources
        SET local_path = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            normalized_path,
            utcnow_iso(),
            source_id,
        ),
    )
    return normalized_path


def _resolve_shared_local_source_owner_user_id(
    connection: sqlite3.Connection,
    *,
    settings: Settings,
) -> int:
    row = connection.execute(
        """
        SELECT id
        FROM users
        WHERE username = ?
        LIMIT 1
        """,
        (settings.admin_username,),
    ).fetchone()
    if row is None:
        row = connection.execute(
            """
            SELECT id
            FROM users
            WHERE role = 'admin'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("Cannot ensure the shared local library source before an admin user exists.")
    return int(row["id"])
