from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import HTTPException, status

from ..config import Settings
from ..db import get_connection, preserve_hidden_movie_keys_for_media_item, utcnow_iso
from ..media_scan import infer_title_and_year
from .google_drive_service import (
    build_cloud_virtual_path,
    fetch_drive_file_metadata,
    fetch_drive_resource_metadata,
    google_drive_enabled,
    list_drive_media_files,
)
from .library_service import get_media_item_record


logger = logging.getLogger(__name__)


def _coerce_preserved_year(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sync_visible_google_drive_sources(
    settings: Settings,
    *,
    user_id: int,
    provider: str,
    get_access_token_by_account_id: Callable[..., str],
) -> None:
    _sync_visible_google_drive_sources(
        settings,
        user_id=user_id,
        provider=provider,
        get_access_token_by_account_id=get_access_token_by_account_id,
    )


def _sync_visible_google_drive_sources(
    settings: Settings,
    *,
    user_id: int,
    provider: str,
    get_access_token_by_account_id: Callable[..., str],
) -> None:
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
            (user_id, provider, user_id),
        ).fetchall()
    for row in rows:
        if _should_resync_source(row):
            _sync_google_drive_library_source(
                settings,
                source_id=int(row["id"]),
                raise_on_error=False,
                provider=provider,
                get_access_token_by_account_id=get_access_token_by_account_id,
            )


def _should_resync_source(row) -> bool:
    return False


def sync_all_google_drive_sources(
    settings: Settings,
    *,
    provider: str,
    get_access_token_by_account_id: Callable[..., str],
) -> dict[str, int]:
    with get_connection(settings) as connection:
        rows = connection.execute(
            """
            SELECT id
            FROM library_sources
            WHERE provider = ?
            ORDER BY id ASC
            """,
            (provider,),
        ).fetchall()
    sources_synced = 0
    media_rows_written = 0
    for row in rows:
        source_count = _sync_google_drive_library_source(
            settings,
            source_id=int(row["id"]),
            raise_on_error=True,
            provider=provider,
            get_access_token_by_account_id=get_access_token_by_account_id,
        )
        sources_synced += 1
        media_rows_written += source_count
    return {
        "sources_synced": sources_synced,
        "media_rows_written": media_rows_written,
    }


def sync_google_drive_library_source(
    settings: Settings,
    *,
    source_id: int,
    raise_on_error: bool,
    provider: str,
    get_access_token_by_account_id: Callable[..., str],
) -> int:
    return _sync_google_drive_library_source(
        settings,
        source_id=source_id,
        raise_on_error=raise_on_error,
        provider=provider,
        get_access_token_by_account_id=get_access_token_by_account_id,
    )


def _sync_google_drive_library_source(
    settings: Settings,
    *,
    source_id: int,
    raise_on_error: bool,
    provider: str,
    get_access_token_by_account_id: Callable[..., str],
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
            (source_id, provider),
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
        access_token = get_access_token_by_account_id(
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


def refresh_google_drive_media_item_metadata(
    settings: Settings,
    *,
    item_id: int,
    get_access_token_by_account_id: Callable[..., str],
) -> dict[str, object] | None:
    return refresh_cloud_media_item_metadata(
        settings,
        item_id=item_id,
        get_access_token_by_account_id=get_access_token_by_account_id,
    )


def refresh_cloud_media_item_metadata(
    settings: Settings,
    *,
    item_id: int,
    get_access_token_by_account_id: Callable[..., str],
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
    access_token = get_access_token_by_account_id(
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


def _parse_recorded_timestamp(value: object) -> float:
    if not value:
        return 0.0
    raw = str(value).strip()
    if not raw:
        return 0.0
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.timestamp()


def _merge_playback_progress_rows(connection, *, canonical_id: int, duplicate_ids: list[int]) -> None:
    if not duplicate_ids:
        return
    placeholders = ",".join("?" for _ in [canonical_id, *duplicate_ids])
    rows = connection.execute(
        f"""
        SELECT id, user_id, media_item_id, position_seconds, duration_seconds, watch_seconds_total, completed, updated_at
        FROM playback_progress
        WHERE media_item_id IN ({placeholders})
        ORDER BY id ASC
        """,
        (canonical_id, *duplicate_ids),
    ).fetchall()
    rows_by_user: dict[int, list] = {}
    for row in rows:
        rows_by_user.setdefault(int(row["user_id"]), []).append(row)

    for user_rows in rows_by_user.values():
        latest_row = max(
            user_rows,
            key=lambda row: (_parse_recorded_timestamp(row["updated_at"]), int(row["id"])),
        )
        merged_duration = next(
            (
                float(row["duration_seconds"])
                for row in user_rows
                if row["duration_seconds"] is not None
            ),
            None,
        )
        merged_watch_total = max(float(row["watch_seconds_total"] or 0.0) for row in user_rows)
        merged_completed = 1 if any(int(row["completed"] or 0) for row in user_rows) else 0
        canonical_row = next(
            (row for row in user_rows if int(row["media_item_id"]) == canonical_id),
            None,
        )
        if canonical_row is None:
            canonical_row = user_rows[0]
            connection.execute(
                """
                UPDATE playback_progress
                SET media_item_id = ?,
                    position_seconds = ?,
                    duration_seconds = ?,
                    watch_seconds_total = ?,
                    completed = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    canonical_id,
                    float(latest_row["position_seconds"] or 0.0),
                    merged_duration,
                    merged_watch_total,
                    merged_completed,
                    str(latest_row["updated_at"] or utcnow_iso()),
                    int(canonical_row["id"]),
                ),
            )
        else:
            connection.execute(
                """
                UPDATE playback_progress
                SET position_seconds = ?,
                    duration_seconds = ?,
                    watch_seconds_total = ?,
                    completed = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    float(latest_row["position_seconds"] or 0.0),
                    merged_duration,
                    merged_watch_total,
                    merged_completed,
                    str(latest_row["updated_at"] or utcnow_iso()),
                    int(canonical_row["id"]),
                ),
            )

        for row in user_rows:
            if int(row["id"]) == int(canonical_row["id"]):
                continue
            connection.execute(
                "DELETE FROM playback_progress WHERE id = ?",
                (int(row["id"]),),
            )


def _merge_user_hidden_rows(connection, *, canonical_id: int, duplicate_ids: list[int]) -> None:
    if not duplicate_ids:
        return
    placeholders = ",".join("?" for _ in [canonical_id, *duplicate_ids])
    rows = connection.execute(
        f"""
        SELECT id, user_id, media_item_id, hidden_at
        FROM user_hidden_media_items
        WHERE media_item_id IN ({placeholders})
        ORDER BY id ASC
        """,
        (canonical_id, *duplicate_ids),
    ).fetchall()
    rows_by_user: dict[int, list] = {}
    for row in rows:
        rows_by_user.setdefault(int(row["user_id"]), []).append(row)

    for user_rows in rows_by_user.values():
        earliest_row = min(
            user_rows,
            key=lambda row: (_parse_recorded_timestamp(row["hidden_at"]), int(row["id"])),
        )
        canonical_row = next(
            (row for row in user_rows if int(row["media_item_id"]) == canonical_id),
            None,
        )
        if canonical_row is None:
            canonical_row = user_rows[0]
            connection.execute(
                """
                UPDATE user_hidden_media_items
                SET media_item_id = ?, hidden_at = ?
                WHERE id = ?
                """,
                (
                    canonical_id,
                    str(earliest_row["hidden_at"]),
                    int(canonical_row["id"]),
                ),
            )
        else:
            connection.execute(
                """
                UPDATE user_hidden_media_items
                SET hidden_at = ?
                WHERE id = ?
                """,
                (str(earliest_row["hidden_at"]), int(canonical_row["id"])),
            )

        for row in user_rows:
            if int(row["id"]) == int(canonical_row["id"]):
                continue
            connection.execute(
                "DELETE FROM user_hidden_media_items WHERE id = ?",
                (int(row["id"]),),
            )


def _merge_global_hidden_rows(connection, *, canonical_id: int, duplicate_ids: list[int]) -> None:
    if not duplicate_ids:
        return
    placeholders = ",".join("?" for _ in [canonical_id, *duplicate_ids])
    rows = connection.execute(
        f"""
        SELECT id, media_item_id, hidden_by_user_id, hidden_at
        FROM global_hidden_media_items
        WHERE media_item_id IN ({placeholders})
        ORDER BY id ASC
        """,
        (canonical_id, *duplicate_ids),
    ).fetchall()
    if not rows:
        return
    earliest_row = min(
        rows,
        key=lambda row: (_parse_recorded_timestamp(row["hidden_at"]), int(row["id"])),
    )
    canonical_row = next(
        (row for row in rows if int(row["media_item_id"]) == canonical_id),
        None,
    )
    if canonical_row is None:
        canonical_row = rows[0]
        connection.execute(
            """
            UPDATE global_hidden_media_items
            SET media_item_id = ?, hidden_by_user_id = ?, hidden_at = ?
            WHERE id = ?
            """,
            (
                canonical_id,
                int(earliest_row["hidden_by_user_id"]),
                str(earliest_row["hidden_at"]),
                int(canonical_row["id"]),
            ),
        )
    else:
        connection.execute(
            """
            UPDATE global_hidden_media_items
            SET hidden_by_user_id = ?, hidden_at = ?
            WHERE id = ?
            """,
            (
                int(earliest_row["hidden_by_user_id"]),
                str(earliest_row["hidden_at"]),
                int(canonical_row["id"]),
            ),
        )

    for row in rows:
        if int(row["id"]) == int(canonical_row["id"]):
            continue
        connection.execute(
            "DELETE FROM global_hidden_media_items WHERE id = ?",
            (int(row["id"]),),
        )


def _reassign_media_item_rows(
    connection,
    *,
    canonical_id: int,
    duplicate_ids: list[int],
    table_name: str,
) -> None:
    if not duplicate_ids:
        return
    placeholders = ",".join("?" for _ in duplicate_ids)
    connection.execute(
        f"UPDATE {table_name} SET media_item_id = ? WHERE media_item_id IN ({placeholders})",
        (canonical_id, *duplicate_ids),
    )


def _collapse_duplicate_cloud_media_rows(connection, *, canonical_id: int, duplicate_ids: list[int]) -> None:
    if not duplicate_ids:
        return
    _merge_playback_progress_rows(
        connection,
        canonical_id=canonical_id,
        duplicate_ids=duplicate_ids,
    )
    _merge_user_hidden_rows(
        connection,
        canonical_id=canonical_id,
        duplicate_ids=duplicate_ids,
    )
    _merge_global_hidden_rows(
        connection,
        canonical_id=canonical_id,
        duplicate_ids=duplicate_ids,
    )
    for table_name in (
        "playback_watch_events",
        "playback_tracking_events",
        "subtitle_tracks",
        "native_playback_sessions",
        "desktop_vlc_handoffs",
        "audit_logs",
    ):
        _reassign_media_item_rows(
            connection,
            canonical_id=canonical_id,
            duplicate_ids=duplicate_ids,
            table_name=table_name,
        )
    for duplicate_id in duplicate_ids:
        connection.execute("DELETE FROM media_items WHERE id = ?", (duplicate_id,))


def _upsert_cloud_media_item(connection, *, source_id: int, resource_id: str, row: dict[str, object]) -> None:
    name = str(row.get("name") or row.get("id") or "Google Drive file")
    # Keep the source-provided title stem in storage. UI display title and poster
    # identity stay derived so parser changes remain non-destructive.
    title = Path(name).stem or name
    _display_title, inferred_year = infer_title_and_year(Path(name).stem or name)
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
    existing_rows = connection.execute(
        """
        SELECT id, year, file_path
        FROM media_items
        WHERE COALESCE(source_kind, 'local') = 'cloud'
          AND library_source_id = ?
          AND external_media_id = ?
        ORDER BY id ASC
        """,
        (source_id, external_media_id),
    ).fetchall()
    existing_row = existing_rows[0] if existing_rows else None
    resolved_year = inferred_year if inferred_year is not None else _coerce_preserved_year(
        existing_row["year"] if existing_row is not None else None
    )
    container = Path(name).suffix.lower().lstrip(".") or None

    if existing_row is not None:
        duplicate_ids = [int(candidate["id"]) for candidate in existing_rows[1:]]
        _collapse_duplicate_cloud_media_rows(
            connection,
            canonical_id=int(existing_row["id"]),
            duplicate_ids=duplicate_ids,
        )
        connection.execute(
            """
            UPDATE media_items
            SET title = ?,
                original_filename = ?,
                file_path = ?,
                source_kind = 'cloud',
                library_source_id = ?,
                external_media_id = ?,
                cloud_mime_type = ?,
                cloud_resource_key = ?,
                series_folder_key = ?,
                series_folder_name = ?,
                file_size = CASE WHEN ? > 0 THEN ? ELSE file_size END,
                file_mtime = ?,
                duration_seconds = COALESCE(?, duration_seconds),
                width = COALESCE(?, width),
                height = COALESCE(?, height),
                container = COALESCE(?, container),
                year = ?,
                updated_at = ?,
                last_scanned_at = ?
            WHERE id = ?
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
                file_size,
                modified_at,
                duration_seconds,
                width,
                height,
                container,
                resolved_year,
                now,
                now,
                int(existing_row["id"]),
            ),
        )
        return

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
            year = COALESCE(excluded.year, media_items.year),
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
            container,
            resolved_year,
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
