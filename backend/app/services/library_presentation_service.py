from __future__ import annotations

import hashlib
from pathlib import Path
import re

from ..media_scan import infer_title_and_year
from .app_settings_service import get_poster_reference_location_payload
from .library_movie_identity_service import _edition_label
from .library_quality_rank_service import build_library_quality_rank
from .media_title_parser import parse_media_title
from .poster_index_service import PosterIndexSnapshot, get_poster_index_snapshot
from .title_normalization import (
    apostrophe_title_variants,
    build_poster_candidate_family,
    clean_title_for_matching,
    normalize_poster_title_key,
    poster_singular_plural_title_keys_equivalent,
    resolve_poster_match_identity,
    resolve_title_metadata,
)
from ..config import Settings


def _poster_directory(
    settings: Settings,
    *,
    connection=None,
) -> Path:
    payload = get_poster_reference_location_payload(settings, connection=connection)
    return Path(str(payload["effective_value"]))


def _poster_candidate_names(*, title: object, year: object, original_filename: object) -> list[str]:
    # Poster matching intentionally uses a small family of parser-derived safe
    # equivalence titles, not the UI-cleaned display title.
    poster_family = build_poster_candidate_family(
        title=title,
        year=year,
        original_filename=original_filename,
    )
    normalized_year = poster_family["year"]
    if normalized_year is None:
        return []

    candidates: list[str] = []
    for candidate_title in poster_family["titles"]:
        if not candidate_title:
            continue
        for apostrophe_variant in apostrophe_title_variants(candidate_title):
            base_name = f"{apostrophe_variant} ({normalized_year})"
            for extension in (".jpg", ".png"):
                candidate = f"{base_name}{extension}"
                if candidate not in candidates:
                    candidates.append(candidate)
    return candidates


def _poster_yearful_key_family(*, title: object, year: object, original_filename: object) -> tuple[set[str], int | None]:
    poster_family = build_poster_candidate_family(
        title=title,
        year=year,
        original_filename=original_filename,
    )
    normalized_year = poster_family["year"]
    if normalized_year is None:
        return set(), None
    return (
        {
            f"{title_key}|{normalized_year}"
            for title_key in poster_family["title_keys"]
            if title_key
        },
        normalized_year,
    )


def _poster_yearless_key_family(*, title: object, year: object, original_filename: object) -> set[str]:
    poster_family = build_poster_candidate_family(
        title=title,
        year=year,
        original_filename=original_filename,
    )
    return {str(title_key) for title_key in poster_family["title_keys"] if title_key}


def _poster_filename_key(stem: str) -> tuple[str | None, int | None]:
    match = re.fullmatch(r"(.+)\s+\((\d{4})\)", stem)
    if not match:
        return None, None
    return normalize_poster_title_key(match.group(1)), int(match.group(2))


def _safe_poster_file(poster_path: Path, *, poster_dir: Path) -> bool:
    if poster_path.suffix.lower() not in {".jpg", ".png"}:
        return False
    try:
        if poster_path.is_symlink() or not poster_path.is_file():
            return False
        poster_path.resolve(strict=True).relative_to(poster_dir.resolve(strict=True))
    except (OSError, ValueError):
        return False
    return True


def _safe_sorted_poster_paths(poster_dir: Path) -> list[Path]:
    try:
        candidates = sorted(poster_dir.iterdir(), key=lambda candidate: candidate.name.lower())
    except OSError:
        return []
    return [candidate for candidate in candidates if _safe_poster_file(candidate, poster_dir=poster_dir)]


def _resolve_unique_yearless_poster_match(*, poster_dir: Path, title: object, year: object, original_filename: object) -> Path | None:
    candidate_title_keys = _poster_yearless_key_family(
        title=title,
        year=year,
        original_filename=original_filename,
    )
    if not candidate_title_keys:
        return None

    matches: list[Path] = []
    for poster_path in _safe_sorted_poster_paths(poster_dir):
        if re.fullmatch(r".+\s+\(\d{4}\)", poster_path.stem):
            continue
        poster_key = normalize_poster_title_key(poster_path.stem)
        if poster_key in candidate_title_keys:
            matches.append(poster_path)
    if len(matches) == 1:
        return matches[0]
    return None


def _resolve_normalized_yearful_poster_match(*, poster_dir: Path, title: object, year: object, original_filename: object) -> Path | None:
    candidate_keys, normalized_year = _poster_yearful_key_family(
        title=title,
        year=year,
        original_filename=original_filename,
    )
    if not candidate_keys or normalized_year is None:
        return None

    for poster_path in _safe_sorted_poster_paths(poster_dir):
        title_key, poster_year = _poster_filename_key(poster_path.stem)
        if title_key is None or poster_year is None:
            continue
        poster_key = f"{title_key}|{poster_year}"
        if poster_key in candidate_keys:
            return poster_path
    return None


def _resolve_unique_singular_plural_yearful_poster_match(
    *,
    poster_dir: Path,
    title: object,
    year: object,
    original_filename: object,
) -> Path | None:
    candidate_keys, normalized_year = _poster_yearful_key_family(
        title=title,
        year=year,
        original_filename=original_filename,
    )
    if not candidate_keys or normalized_year is None:
        return None

    candidate_title_keys = {
        str(candidate_key).rsplit("|", 1)[0]
        for candidate_key in candidate_keys
        if "|" in str(candidate_key)
    }
    if not candidate_title_keys:
        return None

    matches: list[Path] = []
    for poster_path in _safe_sorted_poster_paths(poster_dir):
        title_key, poster_year = _poster_filename_key(poster_path.stem)
        if title_key is None or poster_year != normalized_year:
            continue
        if any(
            poster_singular_plural_title_keys_equivalent(candidate_title_key, title_key)
            for candidate_title_key in candidate_title_keys
        ):
            matches.append(poster_path)

    if len(matches) == 1:
        return matches[0]
    return None


def _normalize_cloud_title_and_year(
    *,
    title: object,
    year: object,
    original_filename: object,
    source_kind: object,
) -> tuple[str, int | None, dict[str, object]]:
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
        display_title = str(metadata.get("display_title") or metadata.get("base_title") or raw_title or str(title or "")).strip()
        parsed_year = _coerce_optional_int(metadata.get("parsed_year"))
        return display_title, parsed_year if parsed_year is not None else resolved_year, metadata

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
        metadata.get("display_title")
        or metadata["base_title"]
        or clean_title_for_matching(original_filename, resolved_year)
        or clean_title_for_matching(title, resolved_year)
        or inferred_title
        or raw_title
        or str(original_filename or "Cloud title")
    )
    parsed_year = _coerce_optional_int(metadata.get("parsed_year"))
    return display_title, parsed_year if parsed_year is not None else resolved_year, metadata


def _coerce_optional_int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parsed_title_payload(*, title: object, year: object, original_filename: object) -> dict[str, object]:
    parsed = parse_media_title(
        title=title,
        year=year,
        original_filename=original_filename,
    )
    display_title = str(parsed["display_title"] or "").strip() or str(title or original_filename or "Untitled").strip() or "Untitled"
    base_title = str(parsed.get("base_title") or "").strip() or display_title
    return {
        "display_title": display_title,
        "base_title": base_title,
        "edition_identity": str(parsed["edition_identity"] or "standard"),
        "parsed_year": parsed["parsed_year"],
        "title_source": str(parsed["title_source"] or "fallback"),
        "parse_confidence": str(parsed["parse_confidence"] or "low"),
        "warnings": [str(value) for value in parsed["warnings"]],
        "parser_version": str(parsed.get("parser_version") or ""),
        "suspicious_output": bool(parsed.get("suspicious_output")),
    }


def _resolve_poster_path_legacy(
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
    candidate_names = _poster_candidate_names(
        title=title,
        year=year,
        original_filename=original_filename,
    )
    for candidate_name in candidate_names:
        candidate_path = resolved_poster_dir / candidate_name
        if _safe_poster_file(candidate_path, poster_dir=resolved_poster_dir):
            return candidate_path

    normalized_yearful = _resolve_normalized_yearful_poster_match(
        poster_dir=resolved_poster_dir,
        title=title,
        year=year,
        original_filename=original_filename,
    )
    if normalized_yearful is not None:
        return normalized_yearful

    singular_plural_yearful = _resolve_unique_singular_plural_yearful_poster_match(
        poster_dir=resolved_poster_dir,
        title=title,
        year=year,
        original_filename=original_filename,
    )
    if singular_plural_yearful is not None:
        return singular_plural_yearful

    return _resolve_unique_yearless_poster_match(
        poster_dir=resolved_poster_dir,
        title=title,
        year=year,
        original_filename=original_filename,
    )


def _first_index_entry(entries) -> Path | None:
    if not entries:
        return None
    return min(entries, key=lambda entry: entry.ordinal).path


def _resolve_poster_path_from_index(
    poster_index: PosterIndexSnapshot,
    *,
    title: object,
    year: object,
    original_filename: object,
) -> Path | None:
    if poster_index.entry_count == 0:
        return None

    for candidate_name in _poster_candidate_names(
        title=title,
        year=year,
        original_filename=original_filename,
    ):
        exact_entry = poster_index.exact_filename_map.get(candidate_name)
        if exact_entry is not None:
            return exact_entry.path

    candidate_keys, normalized_year = _poster_yearful_key_family(
        title=title,
        year=year,
        original_filename=original_filename,
    )
    normalized_matches = []
    for candidate_key in candidate_keys:
        normalized_matches.extend(poster_index.normalized_yearful_map.get(candidate_key, ()))
    normalized_match = _first_index_entry(normalized_matches)
    if normalized_match is not None:
        return normalized_match

    if normalized_year is not None:
        candidate_title_keys = {
            candidate_key.rsplit("|", 1)[0]
            for candidate_key in candidate_keys
            if "|" in candidate_key
        }
        singular_plural_matches = [
            entry
            for entry in poster_index.year_entries_map.get(normalized_year, ())
            if entry.title_key and any(
                poster_singular_plural_title_keys_equivalent(candidate_title_key, entry.title_key)
                for candidate_title_key in candidate_title_keys
            )
        ]
        if len(singular_plural_matches) == 1:
            return singular_plural_matches[0].path

    yearless_matches = []
    seen_yearless_paths: set[Path] = set()
    for candidate_key in _poster_yearless_key_family(
        title=title,
        year=year,
        original_filename=original_filename,
    ):
        for entry in poster_index.yearless_map.get(candidate_key, ()):
            if entry.path not in seen_yearless_paths:
                yearless_matches.append(entry)
                seen_yearless_paths.add(entry.path)
    if len(yearless_matches) == 1:
        return yearless_matches[0].path
    return None


_POSTER_INDEX_UNSET = object()


def _resolve_poster_path(
    settings: Settings,
    *,
    poster_dir: Path | None = None,
    poster_index: PosterIndexSnapshot | None | object = _POSTER_INDEX_UNSET,
    title: object,
    year: object,
    original_filename: object,
    source_kind: object = "local",
) -> Path | None:
    del source_kind
    resolved_poster_dir = poster_dir or _poster_directory(settings)
    resolved_index = (
        get_poster_index_snapshot(resolved_poster_dir, settings=settings)
        if poster_index is _POSTER_INDEX_UNSET
        else poster_index
    )
    if isinstance(resolved_index, PosterIndexSnapshot):
        return _resolve_poster_path_from_index(
            resolved_index,
            title=title,
            year=year,
            original_filename=original_filename,
        )
    return _resolve_poster_path_legacy(
        settings,
        poster_dir=resolved_poster_dir,
        title=title,
        year=year,
        original_filename=original_filename,
    )


def _poster_cache_token(*, poster_path: Path, poster_dir: Path) -> str:
    try:
        stat = poster_path.stat()
    except OSError:
        return "missing"
    token_source = "|".join(
        [
            str(poster_dir.resolve()),
            str(poster_path.resolve()),
            str(int(stat.st_mtime_ns)),
            str(int(stat.st_size)),
        ]
    )
    return hashlib.sha1(token_source.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def _poster_url_for_row(
    settings: Settings,
    row,
    *,
    poster_dir: Path | None = None,
    poster_index: PosterIndexSnapshot | None | object = _POSTER_INDEX_UNSET,
    poster_url_memo: dict[int, str | None] | None = None,
    poster_path_memo: dict[int, Path | None] | None = None,
) -> str | None:
    resolved_poster_dir = poster_dir or _poster_directory(settings)
    media_item_id = int(row["id"])
    if poster_url_memo is not None and media_item_id in poster_url_memo:
        return poster_url_memo[media_item_id]
    poster_path = _resolve_poster_path(
        settings,
        poster_dir=resolved_poster_dir,
        poster_index=poster_index,
        title=row["title"],
        year=row["year"],
        original_filename=row["original_filename"],
        source_kind=_row_value(row, "source_kind", "local"),
    )
    if poster_path is None:
        if poster_path_memo is not None:
            poster_path_memo[media_item_id] = None
        if poster_url_memo is not None:
            poster_url_memo[media_item_id] = None
        return None
    if poster_path_memo is not None:
        poster_path_memo[media_item_id] = poster_path
    token = _poster_cache_token(poster_path=poster_path, poster_dir=resolved_poster_dir)
    poster_url = f"/api/library/item/{media_item_id}/poster?v={token}"
    if poster_url_memo is not None:
        poster_url_memo[media_item_id] = poster_url
    return poster_url


def _source_label_for_row(row) -> str:
    source_kind = row["source_kind"] if "source_kind" in row.keys() else "local"
    return "Cloud" if str(source_kind or "local") != "local" else "DGX"


def _row_value(row, key: str, default=None):
    if hasattr(row, "keys") and key in row.keys():
        return row[key]
    if isinstance(row, dict):
        return row.get(key, default)
    return default


def _quality_rank_for_row(
    row,
    *,
    quality_rank_memo: dict[int, dict[str, object]] | None = None,
) -> dict[str, object]:
    media_item_id = int(row["id"])
    if quality_rank_memo is not None and media_item_id in quality_rank_memo:
        return quality_rank_memo[media_item_id]
    quality_rank = _row_value(row, "quality_rank") or build_library_quality_rank(row)
    if quality_rank_memo is not None:
        quality_rank_memo[media_item_id] = quality_rank
    return quality_rank


def _serialize_media_item(
    settings: Settings,
    row,
    *,
    poster_dir: Path | None = None,
    poster_index: PosterIndexSnapshot | None | object = _POSTER_INDEX_UNSET,
    poster_url_memo: dict[int, str | None] | None = None,
    quality_rank_memo: dict[int, dict[str, object]] | None = None,
) -> dict[str, object]:
    source_kind = str(_row_value(row, "source_kind", "local") or "local")
    quality_rank = _quality_rank_for_row(row, quality_rank_memo=quality_rank_memo)
    quality_tier = str(quality_rank["key"])
    parsed_title = _parsed_title_payload(
        title=row["title"],
        year=row["year"],
        original_filename=row["original_filename"],
    )
    display_title, display_year, metadata = _normalize_cloud_title_and_year(
        title=row["title"],
        year=row["year"],
        original_filename=row["original_filename"],
        source_kind=source_kind,
    )
    return {
        "id": row["id"],
        "title": display_title,
        "parsed_title": parsed_title,
        "original_filename": row["original_filename"],
        "source_kind": source_kind,
        "source_label": _source_label_for_row(row),
        "library_source_id": row["library_source_id"] if "library_source_id" in row.keys() else None,
        "library_source_name": row["library_source_name"] if "library_source_name" in row.keys() else None,
        "library_source_shared": bool(row["library_source_shared"]) if "library_source_shared" in row.keys() else False,
        "library_category": _row_value(row, "library_category"),
        "library_category_path": _row_value(row, "library_category_path"),
        "library_category_name": _row_value(row, "library_category_name"),
        "library_folder_role": _row_value(row, "library_folder_role"),
        "library_folder_path": _row_value(row, "library_folder_path"),
        "library_folder_name": _row_value(row, "library_folder_name"),
        "poster_url": _poster_url_for_row(
            settings,
            row,
            poster_dir=poster_dir,
            poster_index=poster_index,
            poster_url_memo=poster_url_memo,
        ),
        "edition_label": _edition_label(metadata["edition_identity"]),
        "quality_tier": quality_tier,
        "quality_label": str(quality_rank["label"]),
        "quality_rank": quality_rank,
        "genres": list(_row_value(row, "genres", []) or []),
        "genre_display": _row_value(row, "genre_display") or "Unknown",
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
