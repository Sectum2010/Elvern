from __future__ import annotations

import hashlib

from ..config import Settings
from ..db import get_connection
from ..models import AuthenticatedUser
from .library_movie_identity_service import _edition_label
from .library_revision_service import get_library_revision


HIDDEN_TITLES_SCHEMA = "settings-hidden-titles-v1"


def _hidden_title_rows(connection, *, user_id: int, scope: str) -> list[dict[str, object]]:
    if scope == "personal":
        key_rows = connection.execute(
            """
            SELECT
                representative_media_item_id AS id,
                display_title AS title,
                year,
                edition_identity,
                hidden_at
            FROM user_hidden_movie_keys
            WHERE user_id = ?
              AND representative_media_item_id IS NOT NULL
            ORDER BY datetime(hidden_at) DESC, lower(display_title) ASC
            """,
            (user_id,),
        ).fetchall()
        direct_rows = connection.execute(
            """
            SELECT
                hidden.media_item_id AS id,
                media.title,
                media.year,
                'standard' AS edition_identity,
                hidden.hidden_at
            FROM user_hidden_media_items hidden
            JOIN media_items media ON media.id = hidden.media_item_id
            WHERE hidden.user_id = ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM user_hidden_movie_keys hidden_key
                  WHERE hidden_key.user_id = hidden.user_id
                    AND hidden_key.representative_media_item_id = hidden.media_item_id
              )
            ORDER BY datetime(hidden.hidden_at) DESC, lower(media.title) ASC
            """,
            (user_id,),
        ).fetchall()
    else:
        key_rows = connection.execute(
            """
            SELECT
                representative_media_item_id AS id,
                display_title AS title,
                year,
                edition_identity,
                hidden_at
            FROM global_hidden_movie_keys
            WHERE representative_media_item_id IS NOT NULL
            ORDER BY datetime(hidden_at) DESC, lower(display_title) ASC
            """
        ).fetchall()
        direct_rows = connection.execute(
            """
            SELECT
                hidden.media_item_id AS id,
                media.title,
                media.year,
                'standard' AS edition_identity,
                hidden.hidden_at
            FROM global_hidden_media_items hidden
            JOIN media_items media ON media.id = hidden.media_item_id
            WHERE NOT EXISTS (
                SELECT 1
                FROM global_hidden_movie_keys hidden_key
                WHERE hidden_key.representative_media_item_id = hidden.media_item_id
            )
            ORDER BY datetime(hidden.hidden_at) DESC, lower(media.title) ASC
            """
        ).fetchall()

    items = [
        {
            "id": int(row["id"]),
            "title": str(row["title"] or "Untitled"),
            "year": int(row["year"]) if row["year"] is not None else None,
            "edition_label": _edition_label(str(row["edition_identity"] or "standard")),
            "hidden_at": str(row["hidden_at"]),
            "scope": scope,
        }
        for row in (*key_rows, *direct_rows)
    ]
    items.sort(key=lambda item: (str(item["hidden_at"]), str(item["title"]).casefold()), reverse=True)
    return items


def get_settings_hidden_titles_revision(
    settings: Settings,
    *,
    user: AuthenticatedUser,
) -> str:
    revision = get_library_revision(settings, user=user)
    revision_material = f"{revision['permission']}:{revision['user_overlay']}:{user.role}"
    return hashlib.sha256(revision_material.encode("ascii")).hexdigest()


def settings_hidden_titles_etag(revision: str) -> str:
    return f'"{HIDDEN_TITLES_SCHEMA}:{revision}"'


def get_settings_hidden_titles_payload(
    settings: Settings,
    *,
    user: AuthenticatedUser,
    revision: str | None = None,
) -> dict[str, object]:
    hidden_revision = revision or get_settings_hidden_titles_revision(settings, user=user)
    with get_connection(settings) as connection:
        personal_items = _hidden_title_rows(
            connection,
            user_id=int(user.id),
            scope="personal",
        )
        global_items = (
            _hidden_title_rows(connection, user_id=int(user.id), scope="global")
            if user.role == "admin"
            else []
        )
    return {
        "schema_version": HIDDEN_TITLES_SCHEMA,
        "revision": hidden_revision,
        "personal": {"count": len(personal_items), "items": personal_items},
        "global": {"count": len(global_items), "items": global_items} if user.role == "admin" else None,
    }
