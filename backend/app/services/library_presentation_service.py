from __future__ import annotations

from pathlib import Path
import re

from ..media_scan import infer_title_and_year
from .app_settings_service import get_poster_reference_location_payload
from .library_movie_identity_service import _edition_label
from .title_normalization import (
    apostrophe_title_variants,
    clean_title_for_matching,
    normalize_title_key,
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
