from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from fastapi import HTTPException, status

from ..config import Settings
from ..db import get_connection, utcnow_iso
from ..models import AuthenticatedUser
from .audit_service import log_audit_event
from .media_age_access_service import resolve_age_restriction_movie_group


MAX_GENRES_PER_MOVIE_GROUP = 3
COMMON_MOVIE_GENRES = [
    "Action",
    "Adventure",
    "Animation",
    "Comedy",
    "Crime",
    "Documentary",
    "Drama",
    "Family",
    "Fantasy",
    "Horror",
    "Mystery",
    "Romance",
    "Sci-Fi",
    "Thriller",
    "War",
    "Western",
]


@dataclass(frozen=True, slots=True)
class GenreGroupResolution:
    genre_group_key: str
    display_title: str
    year: int | None


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    keys = getattr(row, "keys", None)
    if callable(keys):
        try:
            if key in row.keys():
                return row[key]
        except Exception:  # noqa: BLE001
            return default
    return getattr(row, key, default)


def _get_media_item_row(settings: Settings, item_id: int):
    with get_connection(settings) as connection:
        return connection.execute(
            """
            SELECT id, title, original_filename, year
            FROM media_items
            WHERE id = ?
            LIMIT 1
            """,
            (item_id,),
        ).fetchone()


def _genre_key_from_age_key(age_group_key: str) -> str:
    if age_group_key.startswith("age:"):
        return f"genre:{age_group_key[4:]}"
    return f"genre:{age_group_key}"


def resolve_genre_movie_group(row_or_item: Any) -> GenreGroupResolution:
    age_group = resolve_age_restriction_movie_group(row_or_item)
    return GenreGroupResolution(
        genre_group_key=_genre_key_from_age_key(age_group.age_group_key),
        display_title=age_group.display_title,
        year=age_group.year,
    )


def normalize_genre_labels(raw_genres: list[object] | object) -> list[str]:
    if raw_genres is None:
        return []
    if not isinstance(raw_genres, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Genres must be a list.",
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_label in raw_genres:
        label = re.sub(r"\s+", " ", str(raw_label or "")).strip()
        if not label:
            continue
        if len(label) > 40:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Genre labels must be 40 characters or fewer.",
            )
        label_key = label.casefold()
        if label_key in seen:
            continue
        normalized.append(label)
        seen.add(label_key)
    if len(normalized) > MAX_GENRES_PER_MOVIE_GROUP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Choose up to {MAX_GENRES_PER_MOVIE_GROUP} genres.",
        )
    return normalized


def _decode_genres_json(value: object) -> list[str]:
    if not value:
        return []
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, list):
        return []
    return normalize_genre_labels(decoded)


def get_media_genre_metadata(settings: Settings, item_id: int) -> dict[str, object]:
    row = _get_media_item_row(settings, item_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    group = resolve_genre_movie_group(row)
    with get_connection(settings) as connection:
        genre_row = connection.execute(
            """
            SELECT genres_json
            FROM media_genre_groups
            WHERE genre_group_key = ?
            LIMIT 1
            """,
            (group.genre_group_key,),
        ).fetchone()
    genres = _decode_genres_json(genre_row["genres_json"] if genre_row else None)
    return {
        "genre_group_key": group.genre_group_key,
        "genre_group_display_title": group.display_title,
        "genre_group_year": group.year,
        "genres": genres,
        "genre_display": ", ".join(genres) if genres else "Unknown",
    }


def media_item_ids_for_genre_group(settings: Settings, genre_group_key: str) -> list[int]:
    with get_connection(settings) as connection:
        rows = connection.execute(
            """
            SELECT id, title, original_filename, year
            FROM media_items
            ORDER BY id ASC
            """
        ).fetchall()
    item_ids: list[int] = []
    for row in rows:
        group = resolve_genre_movie_group(row)
        if group.genre_group_key == genre_group_key:
            item_ids.append(int(_row_value(row, "id")))
    return item_ids


def set_media_genres(
    settings: Settings,
    *,
    item_id: int,
    genres: list[object],
    actor: AuthenticatedUser,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, object]:
    normalized_genres = normalize_genre_labels(genres)
    row = _get_media_item_row(settings, item_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    group = resolve_genre_movie_group(row)
    now = utcnow_iso()
    with get_connection(settings) as connection:
        if normalized_genres:
            connection.execute(
                """
                INSERT INTO media_genre_groups (
                    genre_group_key,
                    display_title,
                    year,
                    genres_json,
                    updated_at,
                    updated_by_user_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(genre_group_key) DO UPDATE SET
                    display_title = excluded.display_title,
                    year = excluded.year,
                    genres_json = excluded.genres_json,
                    updated_at = excluded.updated_at,
                    updated_by_user_id = excluded.updated_by_user_id
                """,
                (
                    group.genre_group_key,
                    group.display_title,
                    group.year,
                    json.dumps(normalized_genres, ensure_ascii=True),
                    now,
                    actor.id,
                ),
            )
        else:
            connection.execute(
                "DELETE FROM media_genre_groups WHERE genre_group_key = ?",
                (group.genre_group_key,),
            )
        connection.commit()

    matching_item_ids = media_item_ids_for_genre_group(settings, group.genre_group_key)
    log_audit_event(
        settings,
        action="admin.media_genres.update",
        outcome="success",
        user_id=actor.id,
        username=actor.username,
        role=actor.role,
        session_id=actor.session_id,
        target_type="media_genre_group",
        target_id=group.genre_group_key,
        media_item_id=item_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details={
            "genres": normalized_genres,
            "display_title": group.display_title,
            "year": group.year,
            "matching_media_item_ids": matching_item_ids,
        },
    )
    return {
        **get_media_genre_metadata(settings, item_id),
        "matching_media_item_ids": matching_item_ids,
    }
