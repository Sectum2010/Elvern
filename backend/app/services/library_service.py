from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .library_home_curation_service import (
    _build_series_rails,
    _decorate_continue_rows,
    _resolve_continue_watching_rows,
    _select_continue_watching_rows,
)
from .library_hidden_service import (
    _apply_global_hidden_filter,
    _apply_manual_hidden_filter,
    _build_visible_representative_context,
    _load_globally_hidden_media_item_ids,
    _load_globally_hidden_movie_keys,
    _load_hidden_media_item_ids,
    _load_hidden_movie_keys,
    hide_media_item_for_user,
    hide_media_item_globally,
    list_globally_hidden_media_items as _list_globally_hidden_media_items,
    list_hidden_media_items as _list_hidden_media_items,
    show_media_item_for_user,
    show_media_item_globally,
)
from .status_service import get_scan_job_summary
from .library_movie_identity_service import (
    _apply_duplicate_filter,
    _dedupe_rows,
    _row_hidden_movie_key,
)
from .library_presentation_service import (
    _poster_directory,
    _parsed_title_payload,
    _resolve_poster_path,
    _row_value,
    _serialize_media_item,
)
from .title_normalization import (
    build_search_index,
    match_search_query,
)
from .user_settings_service import get_user_settings
from .local_library_source_service import ensure_current_shared_local_source_binding
from ..config import Settings
from ..db import get_connection


def _utc_iso_to_epoch_seconds(value: object) -> int:
    if not value:
        return 0
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return int(parsed.timestamp())


def _base_query() -> str:
    return """
        SELECT
            m.id,
            m.title,
            m.original_filename,
            m.file_path,
            COALESCE(m.source_kind, 'local') AS source_kind,
            m.library_source_id,
            m.series_folder_key,
            m.series_folder_name,
            s.display_name AS library_source_name,
            COALESCE(s.is_shared, 0) AS library_source_shared,
            m.file_size,
            m.duration_seconds,
            m.width,
            m.height,
            m.video_codec,
            m.audio_codec,
            m.container,
            m.year,
            m.created_at,
            m.updated_at,
            m.last_scanned_at,
            p.position_seconds AS progress_seconds,
            p.duration_seconds AS progress_duration_seconds,
            p.watch_seconds_total AS watch_seconds_total,
            p.completed AS completed,
            p.updated_at AS progress_updated_at
        FROM media_items m
        LEFT JOIN library_sources s
            ON s.id = m.library_source_id
        LEFT JOIN user_hidden_library_sources hs
            ON hs.library_source_id = s.id
           AND hs.user_id = ?
        LEFT JOIN playback_progress p
            ON p.media_item_id = m.id
           AND p.user_id = ?
        WHERE (
            (
                COALESCE(m.source_kind, 'local') = 'local'
                AND m.library_source_id = ?
            )
            OR (
                s.id IS NOT NULL
                AND hs.id IS NULL
                AND (
                    s.owner_user_id = ?
                    OR s.is_shared = 1
                )
            )
        )
    """


def list_library(settings: Settings, *, user_id: int) -> dict[str, object]:
    user_settings = get_user_settings(settings, user_id=user_id)
    with get_connection(settings) as connection:
        shared_local_source_id = ensure_current_shared_local_source_binding(
            settings,
            connection=connection,
        )
        poster_dir = _poster_directory(settings, connection=connection)
        all_rows = connection.execute(
            _base_query() + " ORDER BY lower(m.title) ASC",
            (user_id, user_id, shared_local_source_id, user_id),
        ).fetchall()
        continue_rows = connection.execute(
            _base_query()
            + """
              AND COALESCE(p.completed, 0) = 0
                AND (
                    COALESCE(p.position_seconds, 0) > 0
                    OR COALESCE(p.watch_seconds_total, 0) > 0
                )
              ORDER BY p.updated_at DESC
              """,
            (user_id, user_id, shared_local_source_id, user_id),
        ).fetchall()
        watch_history_rows = connection.execute(
            """
            SELECT
                media_item_id,
                ROUND(SUM(watched_seconds), 2) AS watch_seconds_total,
                MAX(recorded_at_epoch) AS last_watch_event_epoch
            FROM playback_watch_events
            WHERE user_id = ?
            GROUP BY media_item_id
            """,
            (user_id,),
        ).fetchall()
        tracking_activity_rows = connection.execute(
            """
            SELECT
                media_item_id,
                MAX(recorded_at_epoch) AS last_tracking_event_epoch
            FROM playback_tracking_events
            WHERE user_id = ?
              AND event_type IN ('playback_progress', 'playback_seeked', 'playback_stopped', 'playback_completed')
            GROUP BY media_item_id
            """,
            (user_id,),
        ).fetchall()
        recent_rows = connection.execute(
            _base_query()
            + """
              ORDER BY datetime(m.last_scanned_at) DESC
              LIMIT 12
              """,
            (user_id, user_id, shared_local_source_id, user_id),
        ).fetchall()
        globally_hidden_media_item_ids = _load_globally_hidden_media_item_ids(connection)
        globally_hidden_movie_key_records = _load_globally_hidden_movie_keys(connection)
        hidden_media_item_ids = _load_hidden_media_item_ids(connection, user_id=user_id)
        hidden_movie_key_records = _load_hidden_movie_keys(connection, user_id=user_id)
    watch_seconds_total_by_media_item_id = {
        int(row["media_item_id"]): float(row["watch_seconds_total"] or 0)
        for row in watch_history_rows
    }
    last_watch_event_epoch_by_media_item_id = {
        int(row["media_item_id"]): int(row["last_watch_event_epoch"] or 0)
        for row in watch_history_rows
    }
    last_tracking_event_epoch_by_media_item_id = {
        int(row["media_item_id"]): int(row["last_tracking_event_epoch"] or 0)
        for row in tracking_activity_rows
    }
    visible_context = _build_visible_representative_context(
        rows=list(all_rows),
        hide_duplicate_movies=bool(user_settings["hide_duplicate_movies"]),
        globally_hidden_media_item_ids=globally_hidden_media_item_ids,
        globally_hidden_movie_keys=set(globally_hidden_movie_key_records),
        hidden_media_item_ids=hidden_media_item_ids,
        hidden_movie_keys=set(hidden_movie_key_records),
    )
    visible_all_rows = visible_context["rows"]
    series_rails = _build_series_rails(
        settings,
        rows=list(visible_all_rows),
        poster_dir=poster_dir,
    )
    cloud_series_rails = _build_series_rails(
        settings,
        rows=list(visible_all_rows),
        poster_dir=poster_dir,
        include_cloud=True,
    )
    visible_continue_rows = _select_continue_watching_rows(
        _resolve_continue_watching_rows(
            continue_rows=_decorate_continue_rows(
                list(continue_rows),
                watch_seconds_total_by_media_item_id=watch_seconds_total_by_media_item_id,
                last_watch_event_epoch_by_media_item_id=last_watch_event_epoch_by_media_item_id,
                last_tracking_event_epoch_by_media_item_id=last_tracking_event_epoch_by_media_item_id,
            ),
            visible_context=visible_context,
        ),
        utc_iso_to_epoch_seconds=_utc_iso_to_epoch_seconds,
    )
    if user_settings["hide_duplicate_movies"]:
        visible_recent_rows = _apply_manual_hidden_filter(
            _apply_global_hidden_filter(
                _dedupe_rows(list(recent_rows)),
                globally_hidden_media_item_ids=globally_hidden_media_item_ids,
                globally_hidden_movie_keys=set(globally_hidden_movie_key_records),
            ),
            hidden_media_item_ids=hidden_media_item_ids,
            hidden_movie_keys=set(hidden_movie_key_records),
        )
    else:
        visible_recent_rows = _apply_manual_hidden_filter(
            _apply_global_hidden_filter(
                list(recent_rows),
                globally_hidden_media_item_ids=globally_hidden_media_item_ids,
                globally_hidden_movie_keys=set(globally_hidden_movie_key_records),
            ),
            hidden_media_item_ids=hidden_media_item_ids,
            hidden_movie_keys=set(hidden_movie_key_records),
        )
    return {
        "items": [_serialize_media_item(settings, row, poster_dir=poster_dir) for row in visible_all_rows],
        "series_rails": series_rails,
        "cloud_series_rails": cloud_series_rails,
        "continue_watching": [_serialize_media_item(settings, row, poster_dir=poster_dir) for row in visible_continue_rows],
        "recently_added": [_serialize_media_item(settings, row, poster_dir=poster_dir) for row in visible_recent_rows],
        "total_items": len(visible_all_rows),
    }


def _search_match_score(row, query: str) -> int:
    matched, score = match_search_query(
        query=query,
        search_index=build_search_index(
            title=row["title"],
            year=row["year"],
            original_filename=row["original_filename"],
        ),
    )
    return score if matched else 0


def search_library(settings: Settings, *, user_id: int, query: str) -> dict[str, object]:
    normalized_query = query.strip()
    if not normalized_query:
        return {
            "items": [],
            "series_rails": [],
            "cloud_series_rails": [],
            "continue_watching": [],
            "recently_added": [],
            "query": query,
            "total_items": 0,
        }
    with get_connection(settings) as connection:
        shared_local_source_id = ensure_current_shared_local_source_binding(
            settings,
            connection=connection,
        )
        poster_dir = _poster_directory(settings, connection=connection)
        rows = connection.execute(
            _base_query() + " ORDER BY lower(m.title) ASC",
            (user_id, user_id, shared_local_source_id, user_id),
        ).fetchall()
    scored_rows: list[tuple[int, object]] = []
    for row in rows:
        score = _search_match_score(row, normalized_query)
        if score > 0:
            scored_rows.append((score, row))
    scored_rows.sort(
        key=lambda entry: (
            -entry[0],
            str(entry[1]["title"]).lower(),
            int(entry[1]["id"]),
        )
    )
    matched_rows = [row for _, row in scored_rows]
    visible_rows = _apply_duplicate_filter(settings, user_id=user_id, rows=matched_rows)
    with get_connection(settings) as connection:
        globally_hidden_media_item_ids = _load_globally_hidden_media_item_ids(connection)
        globally_hidden_movie_key_records = _load_globally_hidden_movie_keys(connection)
        hidden_media_item_ids = _load_hidden_media_item_ids(connection, user_id=user_id)
        hidden_movie_key_records = _load_hidden_movie_keys(connection, user_id=user_id)
    visible_rows = _apply_global_hidden_filter(
        visible_rows,
        globally_hidden_media_item_ids=globally_hidden_media_item_ids,
        globally_hidden_movie_keys=set(globally_hidden_movie_key_records),
    )
    visible_rows = _apply_manual_hidden_filter(
        visible_rows,
        hidden_media_item_ids=hidden_media_item_ids,
        hidden_movie_keys=set(hidden_movie_key_records),
    )
    return {
        "items": [_serialize_media_item(settings, row, poster_dir=poster_dir) for row in visible_rows],
        "series_rails": [],
        "cloud_series_rails": [],
        "continue_watching": [],
        "recently_added": [],
        "query": query,
        "total_items": len(visible_rows),
    }


def get_media_item_detail(
    settings: Settings,
    *,
    user_id: int,
    item_id: int,
    allow_globally_hidden: bool = False,
) -> dict[str, object] | None:
    with get_connection(settings) as connection:
        shared_local_source_id = ensure_current_shared_local_source_binding(
            settings,
            connection=connection,
        )
        poster_dir = _poster_directory(settings, connection=connection)
        row = connection.execute(
            _base_query()
            + """
              AND m.id = ?
              LIMIT 1
              """,
            (user_id, user_id, shared_local_source_id, user_id, item_id),
        ).fetchone()
        if row is None:
            return None
        subtitles = connection.execute(
            """
            SELECT id, language, title, codec, disposition_default
            FROM subtitle_tracks
            WHERE media_item_id = ?
            ORDER BY id ASC
            """,
            (item_id,),
        ).fetchall()
        media_row = connection.execute(
            "SELECT file_path FROM media_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        hidden_row = connection.execute(
            """
            SELECT 1
            FROM user_hidden_media_items
            WHERE user_id = ? AND media_item_id = ?
            LIMIT 1
            """,
            (user_id, item_id),
        ).fetchone()
        global_hidden_row = connection.execute(
            """
            SELECT hidden_at
            FROM global_hidden_media_items
            WHERE media_item_id = ?
            LIMIT 1
            """,
            (item_id,),
        ).fetchone()
        hidden_movie_key_records = _load_hidden_movie_keys(connection, user_id=user_id)
        globally_hidden_movie_key_records = _load_globally_hidden_movie_keys(connection)
    movie_key = _row_hidden_movie_key(row)
    hidden_for_user = hidden_row is not None or (movie_key in hidden_movie_key_records if movie_key else False)
    hidden_globally = global_hidden_row is not None or (
        movie_key in globally_hidden_movie_key_records if movie_key else False
    )
    if hidden_globally and not allow_globally_hidden:
        return None
    payload = _serialize_media_item(settings, row, poster_dir=poster_dir)
    payload.update(
        {
            "hidden_for_user": hidden_for_user,
            "hidden_globally": hidden_globally,
            "file_path": media_row["file_path"],
            "stream_url": f"/api/stream/{item_id}",
            "resume_position_seconds": float(row["progress_seconds"] or 0),
            "subtitles": [
                {
                    "id": subtitle["id"],
                    "language": subtitle["language"],
                    "title": subtitle["title"],
                    "codec": subtitle["codec"],
                    "disposition_default": bool(subtitle["disposition_default"]),
                }
                for subtitle in subtitles
            ],
        }
    )
    return payload


def get_media_item_poster_path(
    settings: Settings,
    *,
    user_id: int,
    item_id: int,
    allow_globally_hidden: bool = False,
) -> Path | None:
    with get_connection(settings) as connection:
        shared_local_source_id = ensure_current_shared_local_source_binding(
            settings,
            connection=connection,
        )
        poster_dir = _poster_directory(settings, connection=connection)
        row = connection.execute(
            _base_query()
            + """
              AND m.id = ?
              LIMIT 1
              """,
            (user_id, user_id, shared_local_source_id, user_id, item_id),
        ).fetchone()
        global_hidden_row = connection.execute(
            """
            SELECT 1
            FROM global_hidden_media_items
            WHERE media_item_id = ?
            LIMIT 1
            """,
            (item_id,),
        ).fetchone()
        globally_hidden_movie_key_records = _load_globally_hidden_movie_keys(connection)
    if row is None:
        return None
    movie_key = _row_hidden_movie_key(row)
    hidden_globally = global_hidden_row is not None or (
        movie_key in globally_hidden_movie_key_records if movie_key else False
    )
    if hidden_globally and not allow_globally_hidden:
        return None
    return _resolve_poster_path(
        settings,
        poster_dir=poster_dir,
        title=row["title"],
        year=row["year"],
        original_filename=row["original_filename"],
        source_kind=_row_value(row, "source_kind", "local"),
    )


def get_media_file_path(settings: Settings, *, item_id: int) -> str | None:
    with get_connection(settings) as connection:
        row = connection.execute(
            "SELECT file_path FROM media_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        return row["file_path"] if row else None


def get_media_item_record(settings: Settings, *, item_id: int) -> dict[str, object] | None:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT
                id,
                title,
                original_filename,
                file_path,
                COALESCE(source_kind, 'local') AS source_kind,
                library_source_id,
                external_media_id,
                cloud_mime_type,
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
            FROM media_items
            WHERE id = ?
            LIMIT 1
            """,
            (item_id,),
        ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        parsed_title = _parsed_title_payload(
            title=row["title"],
            year=row["year"],
            original_filename=row["original_filename"],
        )
        payload["parsed_title"] = parsed_title
        payload["title"] = parsed_title["display_title"]
        if parsed_title["parsed_year"] is not None:
            payload["year"] = parsed_title["parsed_year"]
        return payload


def list_last_scan(settings: Settings) -> dict[str, object] | None:
    return get_scan_job_summary(settings)


def list_hidden_media_items(settings: Settings, *, user_id: int) -> list[dict[str, object]]:
    return _list_hidden_media_items(
        settings,
        user_id=user_id,
        base_query_sql=_base_query(),
        utc_iso_to_epoch_seconds=_utc_iso_to_epoch_seconds,
    )


def list_globally_hidden_media_items(settings: Settings) -> list[dict[str, object]]:
    return _list_globally_hidden_media_items(
        settings,
        utc_iso_to_epoch_seconds=_utc_iso_to_epoch_seconds,
    )
