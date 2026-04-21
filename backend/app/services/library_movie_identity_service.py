from __future__ import annotations

from .title_normalization import (
    extract_edition_identity_anywhere,
    normalize_title_key,
    resolve_title_metadata,
)
from .user_settings_service import get_user_settings
from ..config import Settings


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
