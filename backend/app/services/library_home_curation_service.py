from __future__ import annotations

from pathlib import Path
import re

from .library_movie_identity_service import _dedupe_group_key
from .library_presentation_service import (
    _normalize_cloud_title_and_year,
    _row_value,
    _serialize_media_item,
)
from .local_library_source_service import get_effective_shared_local_library_path
from .title_normalization import clean_title_for_matching, collapse_spaces, normalize_title_key
from ..config import Settings

CONTINUE_WATCHING_MAX_ITEMS = 6
SERIES_PREFIX_TRAILING_TOKENS = {"the", "a", "an", "and"}
SERIES_HEADING_SMALL_WORDS = {"a", "an", "and", "as", "at", "for", "in", "of", "on", "the", "to"}


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


def _select_continue_watching_rows(
    rows: list,
    *,
    utc_iso_to_epoch_seconds,
) -> list:
    if not rows:
        return []

    def historical_watch_seconds(row) -> float:
        return max(
            float(row.get("watch_seconds_total") or 0),
            float(row.get("progress_seconds") or 0),
        )

    def universal_activity_epoch(row) -> int:
        return max(
            utc_iso_to_epoch_seconds(row.get("progress_updated_at")),
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
        media_root = get_effective_shared_local_library_path(settings).resolve()
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
