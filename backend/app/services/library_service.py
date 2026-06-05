from __future__ import annotations

import json
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
    QUALITY_TIER_LABELS,
    QUALITY_TIER_VALUES,
    _apply_duplicate_filter,
    _dedupe_rows,
    _row_hidden_movie_key,
    quality_tier_for_row,
)
from .library_presentation_service import (
    _poster_directory,
    _parsed_title_payload,
    _resolve_poster_path,
    _row_value,
    _serialize_media_item,
)
from .media_age_access_service import resolve_media_age_requirement
from .media_genre_service import _decode_genres_json, get_media_genre_metadata, resolve_genre_movie_group
from .title_normalization import (
    build_search_index,
    match_search_query,
)
from .user_settings_service import get_user_settings
from .local_library_source_service import ensure_current_shared_local_source_binding
from ..config import Settings
from ..db import get_connection


TEXT_SUBTITLE_CODECS = {"ass", "ssa", "subrip", "srt", "text", "webvtt", "vtt", "mov_text", "tx3g"}
IMAGE_SUBTITLE_CODECS = {"dvd_subtitle", "dvdsub", "dvb_subtitle", "hdmv_pgs_subtitle", "pgs", "xsub"}
LIBRARY_CATEGORY_VALUES = ("movies", "tv", "anime", "cartoon")
LIBRARY_CATEGORY_VALUE_SET = set(LIBRARY_CATEGORY_VALUES)
LIBRARY_SOURCE_FILTER_VALUES = ("all", "local", "cloud")
LIBRARY_QUALITY_FILTER_VALUES = ("all", *QUALITY_TIER_VALUES)
LIBRARY_QUALITY_FILTER_RANKS = {
    "wood": 0,
    "bronze": 1,
    "iron": 2,
    "silver": 3,
    "gold": 4,
    "diamond": 5,
}
LIBRARY_SORT_VALUES = (
    "smart",
    "az",
    "za",
    "recent_desc",
    "recent_asc",
    "year_desc",
    "year_asc",
    "size_desc",
    "size_asc",
)


def normalize_library_category(category: str | None = None) -> str:
    normalized = str(category or "").strip().lower() or "movies"
    if normalized not in LIBRARY_CATEGORY_VALUE_SET:
        expected = ", ".join(LIBRARY_CATEGORY_VALUES)
        raise ValueError(f"Invalid library category '{category}'. Expected one of: {expected}.")
    return normalized


def normalize_library_source_filter(source: str | None = None) -> str:
    normalized = str(source or "").strip().lower() or "all"
    if normalized not in LIBRARY_SOURCE_FILTER_VALUES:
        expected = ", ".join(LIBRARY_SOURCE_FILTER_VALUES)
        raise ValueError(f"Invalid library source '{source}'. Expected one of: {expected}.")
    return normalized


def normalize_library_quality_filter(quality: str | None = None) -> str:
    normalized = str(quality or "").strip().lower() or "all"
    if normalized not in LIBRARY_QUALITY_FILTER_VALUES:
        expected = ", ".join(LIBRARY_QUALITY_FILTER_VALUES)
        raise ValueError(f"Invalid library quality '{quality}'. Expected one of: {expected}.")
    return normalized


def normalize_library_sort(sort: str | None = None) -> str:
    normalized = str(sort or "").strip().lower() or "smart"
    if normalized not in LIBRARY_SORT_VALUES:
        expected = ", ".join(LIBRARY_SORT_VALUES)
        raise ValueError(f"Invalid library sort '{sort}'. Expected one of: {expected}.")
    return normalized


def normalize_library_genre_filter(genre: str | None = None) -> str | None:
    normalized = " ".join(str(genre or "").strip().split())
    return normalized or None


def normalize_library_arrange(
    *,
    source: str | None = None,
    genre: str | None = None,
    quality: str | None = None,
    sort: str | None = None,
) -> dict[str, str | None]:
    return {
        "source": normalize_library_source_filter(source),
        "genre": normalize_library_genre_filter(genre),
        "quality": normalize_library_quality_filter(quality),
        "sort": normalize_library_sort(sort),
    }


def _arrange_is_default(arrange: dict[str, str | None]) -> bool:
    return (
        arrange["source"] == "all"
        and arrange["genre"] is None
        and arrange["quality"] == "all"
        and arrange["sort"] == "smart"
    )


def _row_library_category(row) -> str:
    return str(_row_value(row, "library_category", "") or "").strip().lower()


def _matches_library_category(row, category: str) -> bool:
    library_category = _row_library_category(row)
    if library_category:
        return library_category == category
    return category == "movies"


def _filter_rows_for_library_category(rows: list, category: str) -> list:
    return [row for row in rows if _matches_library_category(row, category)]


def _genre_map_from_connection(connection) -> dict[str, list[str]]:
    rows = connection.execute(
        """
        SELECT genre_group_key, genres_json
        FROM media_genre_groups
        """
    ).fetchall()
    return {
        str(row["genre_group_key"]): _decode_genres_json(row["genres_json"])
        for row in rows
    }


def _decorate_rows_with_arrange_metadata(rows: list, genre_map: dict[str, list[str]]) -> list[dict[str, object]]:
    decorated_rows: list[dict[str, object]] = []
    for row in rows:
        payload = dict(row)
        genre_group = resolve_genre_movie_group(payload)
        genres = list(genre_map.get(genre_group.genre_group_key, []))
        quality_tier = quality_tier_for_row(payload)
        payload["genres"] = genres
        payload["genre_display"] = ", ".join(genres) if genres else "Unknown"
        payload["quality_tier"] = quality_tier
        payload["quality_label"] = QUALITY_TIER_LABELS.get(quality_tier, quality_tier.title())
        decorated_rows.append(payload)
    return decorated_rows


def _row_genre_keys(row) -> set[str]:
    return {str(genre).casefold() for genre in (_row_value(row, "genres", []) or [])}


def _apply_library_arrange_filters(rows: list, arrange: dict[str, str | None]) -> list:
    source_filter = str(arrange["source"] or "all")
    genre_filter = arrange["genre"]
    quality_filter = str(arrange["quality"] or "all")
    genre_key = str(genre_filter).casefold() if genre_filter else None
    filtered_rows = []
    for row in rows:
        if source_filter != "all" and str(_row_value(row, "source_kind", "local") or "local") != source_filter:
            continue
        if genre_key and genre_key not in _row_genre_keys(row):
            continue
        if quality_filter != "all" and LIBRARY_QUALITY_FILTER_RANKS.get(
            str(_row_value(row, "quality_tier", "") or ""),
            -1,
        ) < LIBRARY_QUALITY_FILTER_RANKS[quality_filter]:
            continue
        filtered_rows.append(row)
    return filtered_rows


def _available_genres_for_rows(rows: list) -> list[str]:
    labels_by_key: dict[str, str] = {}
    for row in rows:
        for genre in _row_value(row, "genres", []) or []:
            label = " ".join(str(genre or "").strip().split())
            if not label:
                continue
            labels_by_key.setdefault(label.casefold(), label)
    return sorted(labels_by_key.values(), key=lambda label: label.casefold())


def _coerce_int(value: object, fallback: int = 0) -> int:
    if value in {None, ""}:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _library_title_sort_key(row) -> tuple[str, int]:
    return (str(_row_value(row, "title", "") or "").casefold(), int(_row_value(row, "id", 0) or 0))


def _sort_library_rows(rows: list, sort: str) -> list:
    if sort == "smart":
        return list(rows)
    if sort == "az":
        return sorted(rows, key=_library_title_sort_key)
    if sort == "za":
        return sorted(rows, key=_library_title_sort_key, reverse=True)
    if sort == "recent_desc":
        return sorted(
            rows,
            key=lambda row: (
                str(_row_value(row, "last_scanned_at", "") or ""),
                int(_row_value(row, "id", 0) or 0),
            ),
            reverse=True,
        )
    if sort == "recent_asc":
        return sorted(
            rows,
            key=lambda row: (
                str(_row_value(row, "last_scanned_at", "") or ""),
                int(_row_value(row, "id", 0) or 0),
            ),
        )
    if sort == "year_desc":
        return sorted(
            rows,
            key=lambda row: (
                _coerce_int(_row_value(row, "year"), -1),
                int(_row_value(row, "id", 0) or 0),
            ),
            reverse=True,
        )
    if sort == "year_asc":
        return sorted(
            rows,
            key=lambda row: (
                _coerce_int(_row_value(row, "year"), 999999),
                int(_row_value(row, "id", 0) or 0),
            ),
        )
    if sort == "size_desc":
        return sorted(
            rows,
            key=lambda row: (
                _coerce_int(_row_value(row, "file_size"), 0),
                int(_row_value(row, "id", 0) or 0),
            ),
            reverse=True,
        )
    if sort == "size_asc":
        return sorted(
            rows,
            key=lambda row: (
                _coerce_int(_row_value(row, "file_size"), 0),
                int(_row_value(row, "id", 0) or 0),
            ),
        )
    return list(rows)


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


def _stream_tags(stream: dict[str, object]) -> dict[str, object]:
    tags = stream.get("tags")
    return tags if isinstance(tags, dict) else {}


def _stream_disposition(stream: dict[str, object]) -> dict[str, object]:
    disposition = stream.get("disposition")
    return disposition if isinstance(disposition, dict) else {}


def _stream_track_label(
    *,
    fallback: str,
    language: object,
    title: object,
    codec: object,
    channels: object = None,
) -> str:
    channel_label = None
    try:
        channel_count = int(channels) if channels is not None else 0
    except (TypeError, ValueError):
        channel_count = 0
    if channel_count > 0:
        channel_label = f"{channel_count}ch"
    main = str(title or language or fallback)
    details = [str(value) for value in (language if title else None, codec, channel_label) if value]
    return f"{main} ({' / '.join(details)})" if details else main


def _extract_playback_tracks_from_probe_summary(raw_probe_summary_json: object) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if not raw_probe_summary_json:
        return [], []
    try:
        payload = json.loads(str(raw_probe_summary_json))
    except json.JSONDecodeError:
        return [], []
    streams = payload.get("streams")
    if not isinstance(streams, list):
        return [], []
    audio_tracks: list[dict[str, object]] = []
    subtitle_tracks: list[dict[str, object]] = []
    audio_ordinal = 0
    subtitle_ordinal = 0
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        codec_type = stream.get("codec_type")
        if codec_type not in {"audio", "subtitle"}:
            continue
        tags = _stream_tags(stream)
        disposition = _stream_disposition(stream)
        codec = stream.get("codec_name")
        codec_long_name = stream.get("codec_long_name")
        language = tags.get("language")
        title = tags.get("title")
        stream_index = int(stream.get("index") or 0)
        if codec_type == "audio":
            audio_ordinal += 1
            channels = int(stream["channels"]) if stream.get("channels") else None
            audio_tracks.append(
                {
                    "index": stream_index,
                    "codec": codec,
                    "codec_long_name": codec_long_name,
                    "language": language,
                    "title": title,
                    "channels": channels,
                    "disposition_default": bool(disposition.get("default", 0)),
                    "disposition_commentary": bool(disposition.get("comment", 0) or disposition.get("commentary", 0)),
                    "label": _stream_track_label(
                        fallback=f"Audio {audio_ordinal}",
                        language=language,
                        title=title,
                        codec=codec,
                        channels=channels,
                    ),
                    "browser_supported": True,
                    "track_source": "raw_probe_summary_json",
                }
            )
            continue
        subtitle_ordinal += 1
        codec_name = str(codec or "").lower()
        text_based = codec_name in TEXT_SUBTITLE_CODECS
        image_based = codec_name in IMAGE_SUBTITLE_CODECS
        subtitle_tracks.append(
            {
                "index": stream_index,
                "codec": codec,
                "codec_long_name": codec_long_name,
                "language": language,
                "title": title,
                "channels": None,
                "disposition_default": bool(disposition.get("default", 0)),
                "disposition_forced": bool(disposition.get("forced", 0)),
                "disposition_commentary": bool(disposition.get("comment", 0) or disposition.get("commentary", 0)),
                "text_based": text_based,
                "image_based": image_based,
                "browser_supported": text_based,
                "track_source": "raw_probe_summary_json",
                "label": _stream_track_label(
                    fallback=f"Subtitle {subtitle_ordinal}",
                    language=language,
                    title=title,
                    codec=codec,
                ),
            }
        )
    return audio_tracks, subtitle_tracks


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
            m.library_category,
            m.library_category_path,
            m.library_category_name,
            m.library_folder_role,
            m.library_folder_path,
            m.library_folder_name,
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


def list_library(
    settings: Settings,
    *,
    user_id: int,
    category: str = "movies",
    source: str | None = None,
    genre: str | None = None,
    quality: str | None = None,
    sort: str | None = None,
) -> dict[str, object]:
    normalized_category = normalize_library_category(category)
    arrange = normalize_library_arrange(source=source, genre=genre, quality=quality, sort=sort)
    user_settings = get_user_settings(settings, user_id=user_id)
    with get_connection(settings) as connection:
        shared_local_source_id = ensure_current_shared_local_source_binding(
            settings,
            connection=connection,
        )
        poster_dir = _poster_directory(settings, connection=connection)
        genre_map = _genre_map_from_connection(connection)
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
        globally_hidden_media_item_ids = _load_globally_hidden_media_item_ids(connection)
        globally_hidden_movie_key_records = _load_globally_hidden_movie_keys(connection)
        hidden_media_item_ids = _load_hidden_media_item_ids(connection, user_id=user_id)
        hidden_movie_key_records = _load_hidden_movie_keys(connection, user_id=user_id)
    all_rows = _decorate_rows_with_arrange_metadata(list(all_rows), genre_map)
    continue_rows = _decorate_rows_with_arrange_metadata(list(continue_rows), genre_map)
    all_rows = _filter_rows_for_library_category(list(all_rows), normalized_category)
    continue_rows = _filter_rows_for_library_category(list(continue_rows), normalized_category)
    recent_rows = sorted(
        all_rows,
        key=lambda row: str(_row_value(row, "last_scanned_at", "") or ""),
        reverse=True,
    )[:12]
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
    available_genre_rows = _apply_library_arrange_filters(
        list(visible_all_rows),
        {
            **arrange,
            "genre": None,
        },
    )
    available_genres = _available_genres_for_rows(available_genre_rows)
    filtered_all_rows = _apply_library_arrange_filters(list(visible_all_rows), arrange)
    sorted_visible_all_rows = _sort_library_rows(filtered_all_rows, str(arrange["sort"] or "smart"))
    series_rails = _build_series_rails(
        settings,
        rows=list(filtered_all_rows),
        poster_dir=poster_dir,
    )
    cloud_series_rails = _build_series_rails(
        settings,
        rows=list(filtered_all_rows),
        poster_dir=poster_dir,
        include_cloud=True,
    )
    visible_continue_rows = _apply_library_arrange_filters(
        _select_continue_watching_rows(
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
        ),
        arrange,
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
    has_arrange_filters = arrange["source"] != "all" or arrange["genre"] is not None or arrange["quality"] != "all"
    if has_arrange_filters:
        visible_recent_rows = sorted(
            filtered_all_rows,
            key=lambda row: str(_row_value(row, "last_scanned_at", "") or ""),
            reverse=True,
        )[:12]
    return {
        "items": [_serialize_media_item(settings, row, poster_dir=poster_dir) for row in sorted_visible_all_rows],
        "series_rails": series_rails,
        "cloud_series_rails": cloud_series_rails,
        "continue_watching": [_serialize_media_item(settings, row, poster_dir=poster_dir) for row in visible_continue_rows],
        "recently_added": [_serialize_media_item(settings, row, poster_dir=poster_dir) for row in visible_recent_rows],
        "total_items": len(filtered_all_rows),
        "arrange": arrange,
        "available_genres": available_genres,
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


def search_library(
    settings: Settings,
    *,
    user_id: int,
    query: str,
    category: str = "movies",
    source: str | None = None,
    genre: str | None = None,
    quality: str | None = None,
    sort: str | None = None,
) -> dict[str, object]:
    normalized_category = normalize_library_category(category)
    arrange = normalize_library_arrange(source=source, genre=genre, quality=quality, sort=sort)
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
            "arrange": arrange,
            "available_genres": [],
        }
    with get_connection(settings) as connection:
        shared_local_source_id = ensure_current_shared_local_source_binding(
            settings,
            connection=connection,
        )
        poster_dir = _poster_directory(settings, connection=connection)
        genre_map = _genre_map_from_connection(connection)
        rows = connection.execute(
            _base_query() + " ORDER BY lower(m.title) ASC",
            (user_id, user_id, shared_local_source_id, user_id),
        ).fetchall()
    rows = _decorate_rows_with_arrange_metadata(list(rows), genre_map)
    rows = _filter_rows_for_library_category(list(rows), normalized_category)
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
    available_genre_rows = _apply_library_arrange_filters(
        list(visible_rows),
        {
            **arrange,
            "genre": None,
        },
    )
    available_genres = _available_genres_for_rows(available_genre_rows)
    visible_rows = _apply_library_arrange_filters(visible_rows, arrange)
    if arrange["sort"] != "smart":
        visible_rows = _sort_library_rows(visible_rows, str(arrange["sort"] or "smart"))
    return {
        "items": [_serialize_media_item(settings, row, poster_dir=poster_dir) for row in visible_rows],
        "series_rails": [],
        "cloud_series_rails": [],
        "continue_watching": [],
        "recently_added": [],
        "query": query,
        "total_items": len(visible_rows),
        "arrange": arrange,
        "available_genres": available_genres,
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
        technical_row = connection.execute(
            """
            SELECT probe_status, probe_error, metadata_source, raw_probe_summary_json
            FROM media_item_technical_metadata
            WHERE media_item_id = ?
            LIMIT 1
            """,
            (item_id,),
        ).fetchone()
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
    trusted_probe = bool(technical_row and technical_row["probe_status"] == "probed")
    audio_tracks, subtitle_stream_tracks = _extract_playback_tracks_from_probe_summary(
        technical_row["raw_probe_summary_json"]
        if trusted_probe
        else None
    )
    subtitle_payload = [
        {
            "id": subtitle["id"],
            "language": subtitle["language"],
            "title": subtitle["title"],
            "codec": subtitle["codec"],
            "disposition_default": bool(subtitle["disposition_default"]),
        }
        for subtitle in subtitles
    ]
    if not subtitle_stream_tracks:
        subtitle_stream_tracks = [
            {
                "index": index,
                "codec": subtitle.get("codec"),
                "codec_long_name": None,
                "language": subtitle.get("language"),
                "title": subtitle.get("title"),
                "channels": None,
                "disposition_default": bool(subtitle.get("disposition_default")),
                "disposition_forced": False,
                "disposition_commentary": False,
                "text_based": str(subtitle.get("codec") or "").lower() in TEXT_SUBTITLE_CODECS,
                "image_based": str(subtitle.get("codec") or "").lower() in IMAGE_SUBTITLE_CODECS,
                "browser_supported": False,
                "track_source": "subtitle_table_fallback",
                "label": _stream_track_label(
                    fallback=f"Subtitle {index + 1}",
                    language=subtitle.get("language"),
                    title=subtitle.get("title"),
                    codec=subtitle.get("codec"),
                ),
            }
            for index, subtitle in enumerate(subtitle_payload)
        ]
    fallback_audio_tracks: list[dict[str, object]] = []
    if not audio_tracks and row["audio_codec"]:
        fallback_audio_tracks = [
            {
                "index": 0,
                "codec": row["audio_codec"],
                "codec_long_name": None,
                "language": None,
                "title": "Default audio",
                "channels": None,
                "disposition_default": True,
                "disposition_commentary": False,
                "label": _stream_track_label(
                    fallback="Default audio",
                    language=None,
                    title="Default audio",
                    codec=row["audio_codec"],
                ),
                "browser_supported": False,
                "track_source": "media_row_fallback",
            }
        ]
    audio_diagnostic_tracks = [*audio_tracks, *fallback_audio_tracks]
    audio_counts = {
        "total_count": len(audio_diagnostic_tracks),
        "trusted_count": sum(1 for track in audio_diagnostic_tracks if track.get("track_source") == "raw_probe_summary_json"),
        "fallback_count": sum(1 for track in audio_diagnostic_tracks if track.get("track_source") == "media_row_fallback"),
        "commentary_count": sum(1 for track in audio_diagnostic_tracks if track.get("disposition_commentary")),
    }
    subtitle_counts = {
        "text_count": sum(1 for track in subtitle_stream_tracks if track.get("text_based")),
        "image_count": sum(1 for track in subtitle_stream_tracks if track.get("image_based")),
        "unknown_count": sum(
            1
            for track in subtitle_stream_tracks
            if not track.get("text_based") and not track.get("image_based")
        ),
        "total_count": len(subtitle_stream_tracks),
    }
    track_scan_status = str(technical_row["probe_status"] if technical_row else "not_scanned")
    if trusted_probe:
        track_scan_source = "raw_probe_summary_json"
    elif subtitle_payload:
        track_scan_source = "subtitle_table_fallback"
    elif track_scan_status == "failed":
        track_scan_source = "failed"
    else:
        track_scan_source = "not_scanned"
    payload = _serialize_media_item(settings, row, poster_dir=poster_dir)
    age_requirement_payload = resolve_media_age_requirement(settings, item_id)
    genre_payload = get_media_genre_metadata(settings, item_id)
    payload.update(
        {
            "hidden_for_user": hidden_for_user,
            "hidden_globally": hidden_globally,
            "file_path": media_row["file_path"],
            "stream_url": f"/api/stream/{item_id}",
            "resume_position_seconds": float(row["progress_seconds"] or 0),
            "subtitles": subtitle_payload,
            "subtitle_tracks": subtitle_stream_tracks,
            "audio_tracks": audio_tracks,
            "track_scan_status": track_scan_status,
            "track_scan_error": str(technical_row["probe_error"] or "") if technical_row else "",
            "track_scan_source": track_scan_source,
            "audio_track_diagnostics": {
                **audio_counts,
                "track_scan_status": track_scan_status,
                "track_scan_source": "raw_probe_summary_json"
                if trusted_probe
                else ("failed" if track_scan_status == "failed" else "not_scanned"),
                "track_scan_error": str(technical_row["probe_error"] or "") if technical_row else "",
                "tracks": [
                    {
                        "index": track.get("index"),
                        "codec": track.get("codec"),
                        "codec_long_name": track.get("codec_long_name"),
                        "language": track.get("language"),
                        "title": track.get("title"),
                        "channels": track.get("channels"),
                        "disposition_default": bool(track.get("disposition_default")),
                        "disposition_commentary": bool(track.get("disposition_commentary")),
                        "browser_supported": bool(track.get("browser_supported")),
                        "track_source": track.get("track_source"),
                    }
                    for track in audio_diagnostic_tracks
                ],
            },
            "subtitle_track_diagnostics": {
                **subtitle_counts,
                "track_scan_status": track_scan_status,
                "track_scan_source": track_scan_source,
                "track_scan_error": str(technical_row["probe_error"] or "") if technical_row else "",
                "source": track_scan_source,
                "tracks": [
                    {
                        "index": track.get("index"),
                        "codec": track.get("codec"),
                        "codec_long_name": track.get("codec_long_name"),
                        "language": track.get("language"),
                        "title": track.get("title"),
                        "text_based": bool(track.get("text_based")),
                        "image_based": bool(track.get("image_based")),
                        "browser_supported": bool(track.get("browser_supported")),
                        "track_source": track.get("track_source"),
                    }
                    for track in subtitle_stream_tracks
                ],
            },
            **age_requirement_payload,
            **genre_payload,
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
