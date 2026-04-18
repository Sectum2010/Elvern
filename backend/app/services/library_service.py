from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

from ..media_scan import infer_title_and_year
from .status_service import get_scan_job_summary
from .title_normalization import (
    apostrophe_title_variants,
    build_search_index,
    clean_title_for_matching,
    collapse_spaces,
    extract_edition_identity_anywhere,
    normalize_title_key,
    resolve_title_metadata,
    match_search_query,
)
from .app_settings_service import get_poster_reference_location_payload
from .user_settings_service import get_user_settings
from ..config import Settings
from ..db import get_connection

CONTINUE_WATCHING_MAX_ITEMS = 6
SERIES_PREFIX_TRAILING_TOKENS = {"the", "a", "an", "and"}
SERIES_HEADING_SMALL_WORDS = {"a", "an", "and", "as", "at", "for", "in", "of", "on", "the", "to"}


def _poster_directory(
    settings: Settings,
    *,
    connection=None,
) -> Path:
    payload = get_poster_reference_location_payload(settings, connection=connection)
    return Path(str(payload["effective_value"]))


def _resolve_base_title_and_edition(
    *,
    title: object,
    year: object,
    original_filename: object,
) -> tuple[str | None, str]:
    metadata = resolve_title_metadata(
        title=title,
        year=year,
        original_filename=original_filename,
    )
    return metadata["base_title"], metadata["edition_identity"] or "standard"


def _edition_label(edition_identity: str | None) -> str | None:
    if not edition_identity or edition_identity == "standard":
        return None
    labels = {
        "roadshow": "Roadshow",
        "director's cut": "Director's Cut",
        "theatrical": "Theatrical",
        "extended": "Extended",
        "final cut": "Final Cut",
        "ultimate cut": "Ultimate Cut",
        "special edition": "Special Edition",
        "collector's edition": "Collector's Edition",
        "anniversary edition": "Anniversary Edition",
        "unrated": "Unrated",
    }
    return " + ".join(labels.get(part, part.title()) for part in edition_identity.split("|"))


def _has_quality_token(haystack: str, *tokens: str) -> bool:
    return any(token in haystack for token in tokens)


def _quality_source_rank(row) -> int:
    haystack = _quality_haystack(row)
    if _has_quality_token(haystack, "remux"):
        return 5
    if _has_quality_token(haystack, "bluray", "blu-ray", "bdrip", "brrip"):
        return 4
    if _has_quality_token(haystack, "web-dl", "webdl"):
        return 3
    if _has_quality_token(haystack, "webrip", "web-rip"):
        return 2
    if _has_quality_token(haystack, "hdtv", "hdrip", "dvdrip"):
        return 1
    return 0


def _quality_resolution_rank(row) -> int:
    width = int(row["width"] or 0)
    height = int(row["height"] or 0)
    haystack = _quality_haystack(row)
    if width >= 3800 or height >= 2100 or _has_quality_token(haystack, "2160p", "4k", "uhd"):
        return 3
    if width >= 1900 or height >= 1000 or _has_quality_token(haystack, "1080p"):
        return 2
    if width >= 1200 or height >= 700 or _has_quality_token(haystack, "720p"):
        return 1
    return 0


def _quality_audio_rank(row) -> int:
    haystack = _quality_haystack(row)
    if _has_quality_token(haystack, "atmos", "truehd", "dts-hd", "dtshd", "master audio"):
        return 3
    if _has_quality_token(haystack, "dts"):
        return 2
    if _has_quality_token(haystack, "ddp", "eac3", "ac3", "dolby digital"):
        return 1
    return 0


def _quality_haystack(row) -> str:
    return " ".join(
        str(value).lower()
        for value in (
            row["title"],
            row["original_filename"],
            row["audio_codec"],
            row["video_codec"],
            row["container"],
        )
        if value
    )


def _quality_sort_key(row) -> tuple[int, int, int, int, int]:
    # Keep this explicit and deterministic: source quality > resolution > audio > size.
    return (
        _quality_source_rank(row),
        _quality_resolution_rank(row),
        _quality_audio_rank(row),
        int(row["file_size"] or 0),
        int(row["id"]),
    )


def _dedupe_group_key(row) -> str | None:
    base_title, edition_identity = _resolve_base_title_and_edition(
        title=row["title"],
        year=row["year"],
        original_filename=row["original_filename"],
    )
    if not base_title or row["year"] in {None, ""}:
        return None
    return f"{normalize_title_key(base_title)}|{int(row['year'])}|{edition_identity}"


def _movie_identity_payload(
    *,
    title: object,
    year: object,
    original_filename: object,
) -> dict[str, object] | None:
    base_title, edition_identity = _resolve_base_title_and_edition(
        title=title,
        year=year,
        original_filename=original_filename,
    )
    if not base_title or year in {None, ""}:
        return None
    try:
        normalized_year = int(year)
    except (TypeError, ValueError):
        return None
    strict_edition_identity = extract_edition_identity_anywhere(title, original_filename)
    if edition_identity == "standard":
        edition_identity = strict_edition_identity
    elif strict_edition_identity != "standard":
        edition_identity = "|".join(
            part
            for part in dict.fromkeys([*edition_identity.split("|"), *strict_edition_identity.split("|")])
            if part
        )
    return {
        "movie_key": f"{normalize_title_key(base_title)}|{normalized_year}|{edition_identity}",
        "display_title": base_title,
        "year": normalized_year,
        "edition_identity": edition_identity,
    }


def _movie_identity_key(
    *,
    title: object,
    year: object,
    original_filename: object,
) -> str | None:
    payload = _movie_identity_payload(
        title=title,
        year=year,
        original_filename=original_filename,
    )
    if payload is None:
        return None
    return str(payload["movie_key"])


def _row_hidden_movie_key(row) -> str | None:
    return _movie_identity_key(
        title=row["title"],
        year=row["year"],
        original_filename=row["original_filename"],
    )


def _dedupe_rows(rows, *, prefer_progress: bool = False) -> list:
    grouped: dict[str, dict[str, object]] = {}
    passthrough: list[tuple[int, object]] = []
    for index, row in enumerate(rows):
        group_key = _dedupe_group_key(row)
        if not group_key:
            passthrough.append((index, row))
            continue
        bucket = grouped.setdefault(group_key, {"first_index": index, "rows": []})
        bucket["rows"].append(row)
    deduped_rows: list[tuple[int, object]] = passthrough[:]
    for bucket in grouped.values():
        entries = bucket["rows"]
        representative = max(entries, key=_quality_sort_key)
        deduped_rows.append((bucket["first_index"], representative))
    deduped_rows.sort(key=lambda entry: entry[0])
    return [row for _, row in deduped_rows]


def _apply_duplicate_filter(
    settings: Settings,
    *,
    user_id: int,
    rows: list,
    prefer_progress: bool = False,
) -> list:
    user_settings = get_user_settings(settings, user_id=user_id)
    if not user_settings["hide_duplicate_movies"]:
        return rows
    return _dedupe_rows(rows, prefer_progress=prefer_progress)


def _load_hidden_media_item_ids(connection, *, user_id: int) -> set[int]:
    rows = connection.execute(
        """
        SELECT media_item_id
        FROM user_hidden_media_items
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchall()
    return {int(row["media_item_id"]) for row in rows}


def _load_hidden_movie_keys(connection, *, user_id: int) -> dict[str, dict[str, object]]:
    rows = connection.execute(
        """
        SELECT movie_key, display_title, year, edition_identity, hidden_at
        FROM user_hidden_movie_keys
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchall()
    return {
        str(row["movie_key"]): {
            "display_title": str(row["display_title"]),
            "year": int(row["year"]),
            "edition_identity": str(row["edition_identity"] or "standard"),
            "hidden_at": str(row["hidden_at"]),
        }
        for row in rows
    }


def _load_globally_hidden_media_item_ids(connection) -> set[int]:
    rows = connection.execute(
        """
        SELECT media_item_id
        FROM global_hidden_media_items
        """
    ).fetchall()
    return {int(row["media_item_id"]) for row in rows}


def _load_globally_hidden_movie_keys(connection) -> dict[str, dict[str, object]]:
    rows = connection.execute(
        """
        SELECT movie_key, display_title, year, edition_identity, hidden_at
        FROM global_hidden_movie_keys
        """
    ).fetchall()
    return {
        str(row["movie_key"]): {
            "display_title": str(row["display_title"]),
            "year": int(row["year"]),
            "edition_identity": str(row["edition_identity"] or "standard"),
            "hidden_at": str(row["hidden_at"]),
        }
        for row in rows
    }


def _apply_global_hidden_filter(
    rows: list,
    *,
    globally_hidden_media_item_ids: set[int],
    globally_hidden_movie_keys: set[str],
) -> list:
    if not globally_hidden_media_item_ids and not globally_hidden_movie_keys:
        return rows
    visible_rows = []
    for row in rows:
        if int(row["id"]) in globally_hidden_media_item_ids:
            continue
        row_key = _row_hidden_movie_key(row)
        if row_key and row_key in globally_hidden_movie_keys:
            continue
        visible_rows.append(row)
    return visible_rows


def _apply_manual_hidden_filter(
    rows: list,
    *,
    hidden_media_item_ids: set[int],
    hidden_movie_keys: set[str],
) -> list:
    if not hidden_media_item_ids and not hidden_movie_keys:
        return rows
    visible_rows = []
    for row in rows:
        if int(row["id"]) in hidden_media_item_ids:
            continue
        row_key = _row_hidden_movie_key(row)
        if row_key and row_key in hidden_movie_keys:
            continue
        visible_rows.append(row)
    return visible_rows


def _build_visible_representative_context(
    *,
    rows: list,
    hide_duplicate_movies: bool,
    globally_hidden_media_item_ids: set[int],
    globally_hidden_movie_keys: set[str],
    hidden_media_item_ids: set[int],
    hidden_movie_keys: set[str],
) -> dict[str, object]:
    # Keep the effective visibility order deterministic: duplicates first,
    # then admin-level global hide, then per-user manual hide.
    if hide_duplicate_movies:
        visible_rows = _apply_manual_hidden_filter(
            _apply_global_hidden_filter(
                _dedupe_rows(list(rows)),
                globally_hidden_media_item_ids=globally_hidden_media_item_ids,
                globally_hidden_movie_keys=globally_hidden_movie_keys,
            ),
            hidden_media_item_ids=hidden_media_item_ids,
            hidden_movie_keys=hidden_movie_keys,
        )
    else:
        visible_rows = _apply_manual_hidden_filter(
            _apply_global_hidden_filter(
                list(rows),
                globally_hidden_media_item_ids=globally_hidden_media_item_ids,
                globally_hidden_movie_keys=globally_hidden_movie_keys,
            ),
            hidden_media_item_ids=hidden_media_item_ids,
            hidden_movie_keys=hidden_movie_keys,
        )

    representatives_by_group: dict[str, object] = {}
    visible_ids: set[int] = set()
    for row in visible_rows:
        visible_ids.add(int(row["id"]))
        group_key = _dedupe_group_key(row)
        if group_key:
            representatives_by_group[group_key] = row

    return {
        "rows": visible_rows,
        "visible_ids": visible_ids,
        "representatives_by_group": representatives_by_group,
        "hide_duplicate_movies": hide_duplicate_movies,
    }


def _merge_continue_progress_row(representative_row, source_progress_row):
    merged = dict(representative_row)
    merged["progress_seconds"] = source_progress_row["progress_seconds"]
    merged["progress_duration_seconds"] = source_progress_row["progress_duration_seconds"]
    merged["completed"] = source_progress_row["completed"]
    merged["progress_updated_at"] = source_progress_row["progress_updated_at"]
    merged["watch_seconds_total"] = _row_value(source_progress_row, "watch_seconds_total", 0)
    merged["last_watch_event_epoch"] = _row_value(source_progress_row, "last_watch_event_epoch", 0)
    merged["last_tracking_event_epoch"] = _row_value(source_progress_row, "last_tracking_event_epoch", 0)
    return merged


def _resolve_continue_watching_rows(
    *,
    continue_rows: list,
    visible_context: dict[str, object],
) -> list:
    resolved_rows: list = []
    seen_visible_ids: set[int] = set()
    hide_duplicate_movies = bool(visible_context["hide_duplicate_movies"])
    visible_ids: set[int] = visible_context["visible_ids"]
    representatives_by_group: dict[str, object] = visible_context["representatives_by_group"]

    for row in continue_rows:
        target_row = None
        if hide_duplicate_movies:
            group_key = _dedupe_group_key(row)
            if group_key:
                target_row = representatives_by_group.get(group_key)
            elif int(row["id"]) in visible_ids:
                target_row = row
        elif int(row["id"]) in visible_ids:
            target_row = row

        if target_row is None:
            continue

        visible_id = int(target_row["id"])
        if visible_id in seen_visible_ids:
            continue

        seen_visible_ids.add(visible_id)
        resolved_rows.append(_merge_continue_progress_row(target_row, row))

    return resolved_rows


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


def _decorate_continue_rows(
    rows: list,
    *,
    watch_seconds_total_by_media_item_id: dict[int, float],
    last_watch_event_epoch_by_media_item_id: dict[int, int],
    last_tracking_event_epoch_by_media_item_id: dict[int, int],
) -> list[dict[str, object]]:
    decorated_rows: list[dict[str, object]] = []
    for row in rows:
        payload = dict(row)
        media_item_id = int(payload["id"])
        payload["watch_seconds_total"] = max(
            float(payload.get("watch_seconds_total") or 0),
            float(watch_seconds_total_by_media_item_id.get(media_item_id, 0)),
        )
        payload["last_watch_event_epoch"] = int(
            last_watch_event_epoch_by_media_item_id.get(media_item_id, 0)
        )
        payload["last_tracking_event_epoch"] = int(
            last_tracking_event_epoch_by_media_item_id.get(media_item_id, 0)
        )
        decorated_rows.append(payload)
    return decorated_rows


def _select_continue_watching_rows(rows: list) -> list:
    if not rows:
        return []

    def historical_watch_seconds(row) -> float:
        return max(
            float(row.get("watch_seconds_total") or 0),
            float(row.get("progress_seconds") or 0),
        )

    def universal_activity_epoch(row) -> int:
        return max(
            _utc_iso_to_epoch_seconds(row.get("progress_updated_at")),
            int(row.get("last_watch_event_epoch") or 0),
            int(row.get("last_tracking_event_epoch") or 0),
        )

    special_slot = max(
        rows,
        key=lambda row: (
            historical_watch_seconds(row),
            universal_activity_epoch(row),
            int(row["id"]),
        ),
    )

    selected_rows = [special_slot]
    selected_ids = {int(special_slot["id"])}

    remaining_rows = sorted(
        rows,
        key=lambda row: (
            universal_activity_epoch(row),
            historical_watch_seconds(row),
            int(row["id"]),
        ),
        reverse=True,
    )
    for row in remaining_rows:
        media_item_id = int(row["id"])
        if media_item_id in selected_ids:
            continue
        selected_rows.append(row)
        selected_ids.add(media_item_id)
        if len(selected_rows) >= CONTINUE_WATCHING_MAX_ITEMS:
            break

    return selected_rows[:CONTINUE_WATCHING_MAX_ITEMS]


def _poster_candidate_names(title: object, year: object, original_filename: object) -> list[str]:
    if year in {None, ""}:
        return []
    try:
        normalized_year = int(year)
    except (TypeError, ValueError):
        return []

    title_variants: list[str] = []
    resolved_metadata = resolve_title_metadata(
        title=title,
        year=normalized_year,
        original_filename=original_filename,
    )
    candidate_title = resolved_metadata["base_title"] or clean_title_for_matching(title, normalized_year)
    if candidate_title:
        for variant in apostrophe_title_variants(candidate_title):
            if variant not in title_variants:
                title_variants.append(variant)
    if not title_variants:
        fallback_title = clean_title_for_matching(original_filename, normalized_year)
        if fallback_title:
            for variant in apostrophe_title_variants(fallback_title):
                if variant not in title_variants:
                    title_variants.append(variant)

    candidates: list[str] = []
    for candidate_title in title_variants:
        if not candidate_title:
            continue
        base_name = f"{candidate_title} ({normalized_year})"
        for extension in (".jpg", ".png"):
            candidate = f"{base_name}{extension}"
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _normalize_cloud_title_and_year(
    *,
    title: object,
    year: object,
    original_filename: object,
    source_kind: object,
) -> tuple[str, int | None, dict[str, str | None]]:
    raw_title = str(title or "").strip()
    try:
        resolved_year = int(year) if year not in {None, ""} else None
    except (TypeError, ValueError):
        resolved_year = None

    if str(source_kind or "local") != "cloud":
        metadata = resolve_title_metadata(
            title=title,
            year=resolved_year,
            original_filename=original_filename,
        )
        return raw_title or str(title or ""), resolved_year, metadata

    inferred_title = None
    inferred_year = None
    filename_source = str(original_filename or title or "").strip()
    if filename_source:
        inferred_title, inferred_year = infer_title_and_year(Path(filename_source).stem)
    if resolved_year is None:
        resolved_year = inferred_year

    metadata = resolve_title_metadata(
        title=title,
        year=resolved_year,
        original_filename=original_filename,
    )
    display_title = (
        metadata["base_title"]
        or clean_title_for_matching(original_filename, resolved_year)
        or clean_title_for_matching(title, resolved_year)
        or inferred_title
        or raw_title
        or str(original_filename or "Cloud title")
    )
    return display_title, resolved_year, metadata


def _resolve_poster_path(
    settings: Settings,
    *,
    poster_dir: Path | None = None,
    title: object,
    year: object,
    original_filename: object,
    source_kind: object = "local",
) -> Path | None:
    resolved_poster_dir = poster_dir or _poster_directory(settings)
    if not resolved_poster_dir.exists():
        return None
    display_title, display_year, _metadata = _normalize_cloud_title_and_year(
        title=title,
        year=year,
        original_filename=original_filename,
        source_kind=source_kind,
    )
    candidate_names = _poster_candidate_names(display_title, display_year, original_filename)
    for candidate_name in candidate_names:
        candidate_path = resolved_poster_dir / candidate_name
        if candidate_path.is_file():
            return candidate_path

    candidate_keys = set()
    for candidate_name in candidate_names:
        candidate_stem = Path(candidate_name).stem
        match = re.fullmatch(r"(.+)\s+\((\d{4})\)", candidate_stem)
        if not match:
            continue
        candidate_keys.add(f"{normalize_title_key(match.group(1))}|{match.group(2)}")

    if not candidate_keys:
        return None

    for poster_path in resolved_poster_dir.iterdir():
        if not poster_path.is_file():
            continue
        if poster_path.suffix.lower() not in {".jpg", ".png"}:
            continue
        match = re.fullmatch(r"(.+)\s+\((\d{4})\)", poster_path.stem)
        if not match:
            continue
        poster_key = f"{normalize_title_key(match.group(1))}|{match.group(2)}"
        if poster_key in candidate_keys:
            return poster_path
    return None


def _poster_url_for_row(settings: Settings, row, *, poster_dir: Path | None = None) -> str | None:
    if _resolve_poster_path(
        settings,
        poster_dir=poster_dir,
        title=row["title"],
        year=row["year"],
        original_filename=row["original_filename"],
        source_kind=_row_value(row, "source_kind", "local"),
    ) is None:
        return None
    return f"/api/library/item/{int(row['id'])}/poster"


def _source_label_for_row(row) -> str:
    source_kind = row["source_kind"] if "source_kind" in row.keys() else "local"
    return "Cloud" if str(source_kind or "local") != "local" else "DGX"


def _row_value(row, key: str, default=None):
    if hasattr(row, "keys") and key in row.keys():
        return row[key]
    if isinstance(row, dict):
        return row.get(key, default)
    return default


def _serialize_media_item(
    settings: Settings,
    row,
    *,
    poster_dir: Path | None = None,
) -> dict[str, object]:
    source_kind = str(_row_value(row, "source_kind", "local") or "local")
    display_title, display_year, metadata = _normalize_cloud_title_and_year(
        title=row["title"],
        year=row["year"],
        original_filename=row["original_filename"],
        source_kind=source_kind,
    )
    return {
        "id": row["id"],
        "title": display_title,
        "original_filename": row["original_filename"],
        "source_kind": source_kind,
        "source_label": _source_label_for_row(row),
        "library_source_id": row["library_source_id"] if "library_source_id" in row.keys() else None,
        "library_source_name": row["library_source_name"] if "library_source_name" in row.keys() else None,
        "library_source_shared": bool(row["library_source_shared"]) if "library_source_shared" in row.keys() else False,
        "poster_url": _poster_url_for_row(settings, row, poster_dir=poster_dir),
        "edition_label": _edition_label(metadata["edition_identity"]),
        "hidden_for_user": bool(row["hidden_for_user"]) if "hidden_for_user" in row.keys() else False,
        "hidden_globally": bool(row["hidden_globally"]) if "hidden_globally" in row.keys() else False,
        "file_size": row["file_size"],
        "duration_seconds": row["duration_seconds"],
        "width": row["width"],
        "height": row["height"],
        "video_codec": row["video_codec"],
        "audio_codec": row["audio_codec"],
        "container": row["container"],
        "year": display_year,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_scanned_at": row["last_scanned_at"],
        "progress_seconds": row["progress_seconds"],
        "progress_duration_seconds": row["progress_duration_seconds"],
        "completed": bool(row["completed"] or 0),
    }


def _series_folder_key(settings: Settings, row, *, include_cloud: bool = False) -> tuple[str, str] | None:
    source_kind = str(_row_value(row, "source_kind", "local") or "local")
    if source_kind == "cloud":
        if not include_cloud:
            return None
        folder_key = str(_row_value(row, "series_folder_key", "") or "").strip()
        folder_name = str(_row_value(row, "series_folder_name", "") or "").strip()
        if not folder_key or not folder_name:
            return None
        return folder_key, folder_name
    if include_cloud:
        return None
    if source_kind != "local":
        return None
    file_path = str(_row_value(row, "file_path", "") or "").strip()
    if not file_path:
        return None
    try:
        resolved_file = Path(file_path).resolve()
        media_root = settings.media_root.resolve()
    except OSError:
        return None
    if media_root not in resolved_file.parents:
        return None
    parent_folder = resolved_file.parent
    if parent_folder == media_root:
        return None
    folder_name = parent_folder.name.strip()
    if not folder_name:
        return None
    return str(parent_folder), folder_name


def _series_prefix_tokens(value: str) -> list[str]:
    cleaned = collapse_spaces(str(value or "").strip())
    if not cleaned:
        return []
    return cleaned.split()


def _series_token_key(token: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", token.lower())


def _extract_series_heading_from_titles(titles: list[str]) -> str | None:
    token_lists = [_series_prefix_tokens(title) for title in titles if _series_prefix_tokens(title)]
    if len(token_lists) < 2:
        return None
    prefix_tokens: list[str] = []
    for column in zip(*token_lists):
        first_token = column[0]
        if all(_series_token_key(candidate) == _series_token_key(first_token) for candidate in column[1:]):
            prefix_tokens.append(first_token)
            continue
        break
    while prefix_tokens and _series_token_key(prefix_tokens[-1]) in SERIES_PREFIX_TRAILING_TOKENS:
        prefix_tokens.pop()
    heading = collapse_spaces(" ".join(prefix_tokens))
    return heading or None


def _prettify_series_heading(value: str) -> str:
    tokens = collapse_spaces(str(value or "").strip()).split()
    pretty_tokens: list[str] = []
    for index, token in enumerate(tokens):
        lowered = token.lower()
        if re.fullmatch(r"[ivxlcdm]+", lowered):
            pretty_tokens.append(lowered.upper())
            continue
        if index > 0 and lowered in SERIES_HEADING_SMALL_WORDS:
            pretty_tokens.append(lowered)
            continue
        pretty_tokens.append(lowered[:1].upper() + lowered[1:])
    return " ".join(pretty_tokens)


def _clean_series_folder_heading(folder_name: str) -> str | None:
    cleaned = clean_title_for_matching(folder_name, None)
    if cleaned:
        return _prettify_series_heading(cleaned)
    fallback = collapse_spaces(re.sub(r"[-._]+", " ", str(folder_name or "").strip()))
    return _prettify_series_heading(fallback) if fallback else None


def _series_heading_matches_titles(heading: str, titles: list[str]) -> bool:
    heading_tokens = [_series_token_key(token) for token in _series_prefix_tokens(heading)]
    if not heading_tokens:
        return False
    for title in titles:
        title_tokens = [_series_token_key(token) for token in _series_prefix_tokens(title)]
        if len(title_tokens) < len(heading_tokens):
            return False
        if title_tokens[: len(heading_tokens)] != heading_tokens:
            return False
    return True


def _build_series_rails(
    settings: Settings,
    *,
    rows: list[object],
    poster_dir: Path | None = None,
    include_cloud: bool = False,
) -> list[dict[str, object]]:
    grouped_rows: dict[str, dict[str, object]] = {}
    for row in rows:
        folder_metadata = _series_folder_key(settings, row, include_cloud=include_cloud)
        if folder_metadata is None:
            continue
        folder_key, folder_name = folder_metadata
        bucket = grouped_rows.setdefault(
            folder_key,
            {
                "folder_name": folder_name,
                "rows": [],
            },
        )
        bucket["rows"].append(row)

    rails: list[dict[str, object]] = []
    for folder_key, payload in grouped_rows.items():
        group_rows = list(payload["rows"])
        if len(group_rows) < 2:
            continue
        sorted_rows = sorted(
            group_rows,
            key=lambda row: (
                int(_row_value(row, "year", 0) or 0) if _row_value(row, "year", 0) not in {None, ""} else 0,
                str(row["title"]).lower(),
                int(row["id"]),
            ),
        )
        candidate_titles = [
            str(
                _normalize_cloud_title_and_year(
                    title=row["title"],
                    year=row["year"],
                    original_filename=row["original_filename"],
                    source_kind=str(_row_value(row, "source_kind", "local") or "local"),
                )[0]
            )
            for row in sorted_rows
        ]
        folder_heading = _clean_series_folder_heading(str(payload["folder_name"]))
        prefix_heading = _extract_series_heading_from_titles(candidate_titles)
        heading = None
        if folder_heading and _series_heading_matches_titles(folder_heading, candidate_titles):
            heading = folder_heading
        elif prefix_heading:
            heading = _prettify_series_heading(prefix_heading)
        if not heading:
            continue
        serialized_items = [
            _serialize_media_item(settings, row, poster_dir=poster_dir)
            for row in sorted_rows
        ]
        if len(serialized_items) < 2:
            continue
        rails.append(
            {
                "key": normalize_title_key(heading) or normalize_title_key(str(payload["folder_name"])) or folder_key,
                "title": heading,
                "film_count": len(serialized_items),
                "items": serialized_items,
            }
        )
    rails.sort(key=lambda rail: (str(rail["title"]).lower(), str(rail["key"]).lower()))
    return rails


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
            COALESCE(m.source_kind, 'local') = 'local'
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
        poster_dir = _poster_directory(settings, connection=connection)
        all_rows = connection.execute(
            _base_query() + " ORDER BY lower(m.title) ASC",
            (user_id, user_id, user_id),
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
            (user_id, user_id, user_id),
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
            (user_id, user_id, user_id),
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
        )
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
        poster_dir = _poster_directory(settings, connection=connection)
        rows = connection.execute(
            _base_query() + " ORDER BY lower(m.title) ASC",
            (user_id, user_id, user_id),
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
        poster_dir = _poster_directory(settings, connection=connection)
        row = connection.execute(
            _base_query()
            + """
              AND m.id = ?
              LIMIT 1
              """,
            (user_id, user_id, user_id, item_id),
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
        poster_dir = _poster_directory(settings, connection=connection)
        row = connection.execute(
            _base_query()
            + """
              AND m.id = ?
              LIMIT 1
              """,
            (user_id, user_id, user_id, item_id),
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
        return dict(row)


def list_last_scan(settings: Settings) -> dict[str, object] | None:
    return get_scan_job_summary(settings)


def list_hidden_media_items(settings: Settings, *, user_id: int) -> list[dict[str, object]]:
    with get_connection(settings) as connection:
        poster_dir = _poster_directory(settings, connection=connection)
        globally_hidden_media_item_ids = _load_globally_hidden_media_item_ids(connection)
        globally_hidden_movie_key_records = _load_globally_hidden_movie_keys(connection)
        hidden_movie_key_records = _load_hidden_movie_keys(connection, user_id=user_id)
        rows = connection.execute(
            """
            SELECT
                m.id,
                m.title,
                m.original_filename,
                COALESCE(m.source_kind, 'local') AS source_kind,
                m.library_source_id,
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
                p.completed AS completed,
                h.hidden_at
            FROM user_hidden_media_items h
            JOIN media_items m
                ON m.id = h.media_item_id
            LEFT JOIN library_sources s
                ON s.id = m.library_source_id
            LEFT JOIN user_hidden_library_sources hs
                ON hs.library_source_id = s.id
               AND hs.user_id = ?
            LEFT JOIN playback_progress p
                ON p.media_item_id = m.id
               AND p.user_id = ?
            WHERE h.user_id = ?
              AND (
                    COALESCE(m.source_kind, 'local') = 'local'
                    OR (
                        s.id IS NOT NULL
                        AND hs.id IS NULL
                        AND (
                            s.owner_user_id = ?
                            OR s.is_shared = 1
                        )
                    )
                )
            ORDER BY datetime(h.hidden_at) DESC, lower(m.title) ASC
            """,
            (user_id, user_id, user_id, user_id),
        ).fetchall()
        visible_candidate_rows = connection.execute(
            _base_query() + " ORDER BY lower(m.title) ASC",
            (user_id, user_id, user_id),
        ).fetchall()

    payload: list[dict[str, object]] = []
    seen_ids: set[int] = set()
    seen_movie_keys: set[str] = set()
    for row in rows:
        if int(row["id"]) in globally_hidden_media_item_ids:
            continue
        row_key = _row_hidden_movie_key(row)
        if row_key and row_key in globally_hidden_movie_key_records:
            continue
        metadata = resolve_title_metadata(
            title=row["title"],
            year=row["year"],
            original_filename=row["original_filename"],
        )
        seen_ids.add(int(row["id"]))
        if row_key:
            seen_movie_keys.add(row_key)
        payload.append(
            {
                "id": row["id"],
                "title": metadata["base_title"] or row["title"],
                "year": row["year"],
                "edition_label": _edition_label(metadata["edition_identity"]),
                "poster_url": _poster_url_for_row(settings, row, poster_dir=poster_dir),
                "hidden_at": row["hidden_at"],
            }
        )

    representatives_by_key: dict[str, object] = {}
    for row in visible_candidate_rows:
        row_key = _row_hidden_movie_key(row)
        if not row_key or row_key not in hidden_movie_key_records:
            continue
        if row_key in globally_hidden_movie_key_records:
            continue
        current = representatives_by_key.get(row_key)
        if current is None or _quality_sort_key(row) > _quality_sort_key(current):
            representatives_by_key[row_key] = row

    for row_key, row in representatives_by_key.items():
        if row_key in seen_movie_keys or int(row["id"]) in seen_ids:
            continue
        metadata = resolve_title_metadata(
            title=row["title"],
            year=row["year"],
            original_filename=row["original_filename"],
        )
        hidden_meta = hidden_movie_key_records[row_key]
        payload.append(
            {
                "id": row["id"],
                "title": metadata["base_title"] or row["title"],
                "year": row["year"],
                "edition_label": _edition_label(metadata["edition_identity"]),
                "poster_url": _poster_url_for_row(settings, row, poster_dir=poster_dir),
                "hidden_at": str(hidden_meta["hidden_at"]),
            }
        )
    payload.sort(key=lambda item: (-_utc_iso_to_epoch_seconds(item["hidden_at"]), str(item["title"]).lower()))
    return payload


def list_globally_hidden_media_items(settings: Settings) -> list[dict[str, object]]:
    with get_connection(settings) as connection:
        poster_dir = _poster_directory(settings, connection=connection)
        global_hidden_movie_key_records = _load_globally_hidden_movie_keys(connection)
        rows = connection.execute(
            """
            SELECT
                m.id,
                m.title,
                m.original_filename,
                COALESCE(m.source_kind, 'local') AS source_kind,
                m.library_source_id,
                NULL AS library_source_name,
                0 AS library_source_shared,
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
                NULL AS progress_seconds,
                NULL AS progress_duration_seconds,
                0 AS completed,
                h.hidden_at
            FROM global_hidden_media_items h
            JOIN media_items m
                ON m.id = h.media_item_id
            ORDER BY datetime(h.hidden_at) DESC, lower(m.title) ASC
            """
        ).fetchall()
        visible_candidate_rows = connection.execute(
            """
            SELECT
                m.id,
                m.title,
                m.original_filename,
                COALESCE(m.source_kind, 'local') AS source_kind,
                m.library_source_id,
                NULL AS library_source_name,
                0 AS library_source_shared,
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
                NULL AS progress_seconds,
                NULL AS progress_duration_seconds,
                0 AS completed
            FROM media_items m
            ORDER BY lower(m.title) ASC
            """
        ).fetchall()

    payload: list[dict[str, object]] = []
    seen_ids: set[int] = set()
    seen_movie_keys: set[str] = set()
    for row in rows:
        metadata = resolve_title_metadata(
            title=row["title"],
            year=row["year"],
            original_filename=row["original_filename"],
        )
        row_key = _row_hidden_movie_key(row)
        seen_ids.add(int(row["id"]))
        if row_key:
            seen_movie_keys.add(row_key)
        payload.append(
            {
                "id": row["id"],
                "title": metadata["base_title"] or row["title"],
                "year": row["year"],
                "edition_label": _edition_label(metadata["edition_identity"]),
                "poster_url": _poster_url_for_row(settings, row, poster_dir=poster_dir),
                "hidden_at": row["hidden_at"],
            }
        )
    representatives_by_key: dict[str, object] = {}
    for row in visible_candidate_rows:
        row_key = _row_hidden_movie_key(row)
        if not row_key or row_key not in global_hidden_movie_key_records:
            continue
        current = representatives_by_key.get(row_key)
        if current is None or _quality_sort_key(row) > _quality_sort_key(current):
            representatives_by_key[row_key] = row

    for row_key, row in representatives_by_key.items():
        if row_key in seen_movie_keys or int(row["id"]) in seen_ids:
            continue
        metadata = resolve_title_metadata(
            title=row["title"],
            year=row["year"],
            original_filename=row["original_filename"],
        )
        hidden_meta = global_hidden_movie_key_records[row_key]
        payload.append(
            {
                "id": row["id"],
                "title": metadata["base_title"] or row["title"],
                "year": row["year"],
                "edition_label": _edition_label(metadata["edition_identity"]),
                "poster_url": _poster_url_for_row(settings, row, poster_dir=poster_dir),
                "hidden_at": str(hidden_meta["hidden_at"]),
            }
        )
    payload.sort(key=lambda item: (-_utc_iso_to_epoch_seconds(item["hidden_at"]), str(item["title"]).lower()))
    return payload


def hide_media_item_for_user(settings: Settings, *, user_id: int, item_id: int) -> None:
    with get_connection(settings) as connection:
        media_item = connection.execute(
            "SELECT id, title, year, original_filename FROM media_items WHERE id = ? LIMIT 1",
            (item_id,),
        ).fetchone()
        if media_item is None:
            raise ValueError("not_found")
        connection.execute(
            """
            INSERT OR IGNORE INTO user_hidden_media_items (user_id, media_item_id, hidden_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (user_id, item_id),
        )
        movie_identity = _movie_identity_payload(
            title=media_item["title"],
            year=media_item["year"],
            original_filename=media_item["original_filename"],
        )
        if movie_identity is not None:
            connection.execute(
                """
                INSERT OR IGNORE INTO user_hidden_movie_keys (
                    user_id,
                    movie_key,
                    display_title,
                    year,
                    edition_identity,
                    hidden_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    user_id,
                    str(movie_identity["movie_key"]),
                    str(movie_identity["display_title"]),
                    int(movie_identity["year"]),
                    str(movie_identity["edition_identity"]),
                ),
            )
        connection.commit()


def hide_media_item_globally(settings: Settings, *, actor_user_id: int, item_id: int) -> None:
    with get_connection(settings) as connection:
        media_item = connection.execute(
            "SELECT id, title, year, original_filename FROM media_items WHERE id = ? LIMIT 1",
            (item_id,),
        ).fetchone()
        if media_item is None:
            raise ValueError("not_found")
        connection.execute(
            """
            INSERT OR IGNORE INTO global_hidden_media_items (media_item_id, hidden_by_user_id, hidden_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (item_id, actor_user_id),
        )
        movie_identity = _movie_identity_payload(
            title=media_item["title"],
            year=media_item["year"],
            original_filename=media_item["original_filename"],
        )
        if movie_identity is not None:
            connection.execute(
                """
                INSERT OR IGNORE INTO global_hidden_movie_keys (
                    movie_key,
                    display_title,
                    year,
                    edition_identity,
                    hidden_by_user_id,
                    hidden_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    str(movie_identity["movie_key"]),
                    str(movie_identity["display_title"]),
                    int(movie_identity["year"]),
                    str(movie_identity["edition_identity"]),
                    actor_user_id,
                ),
            )
        connection.commit()


def show_media_item_for_user(settings: Settings, *, user_id: int, item_id: int) -> None:
    with get_connection(settings) as connection:
        media_item = connection.execute(
            "SELECT title, year, original_filename FROM media_items WHERE id = ? LIMIT 1",
            (item_id,),
        ).fetchone()
        connection.execute(
            """
            DELETE FROM user_hidden_media_items
            WHERE user_id = ? AND media_item_id = ?
            """,
            (user_id, item_id),
        )
        if media_item is not None:
            movie_identity = _movie_identity_payload(
                title=media_item["title"],
                year=media_item["year"],
                original_filename=media_item["original_filename"],
            )
            if movie_identity is not None:
                connection.execute(
                    """
                    DELETE FROM user_hidden_movie_keys
                    WHERE user_id = ? AND movie_key = ?
                    """,
                    (user_id, str(movie_identity["movie_key"])),
                )
        connection.commit()


def show_media_item_globally(settings: Settings, *, item_id: int) -> None:
    with get_connection(settings) as connection:
        media_item = connection.execute(
            "SELECT title, year, original_filename FROM media_items WHERE id = ? LIMIT 1",
            (item_id,),
        ).fetchone()
        connection.execute(
            """
            DELETE FROM global_hidden_media_items
            WHERE media_item_id = ?
            """,
            (item_id,),
        )
        if media_item is not None:
            movie_identity = _movie_identity_payload(
                title=media_item["title"],
                year=media_item["year"],
                original_filename=media_item["original_filename"],
            )
            if movie_identity is not None:
                connection.execute(
                    """
                    DELETE FROM global_hidden_movie_keys
                    WHERE movie_key = ?
                    """,
                    (str(movie_identity["movie_key"]),),
                )
        connection.commit()
