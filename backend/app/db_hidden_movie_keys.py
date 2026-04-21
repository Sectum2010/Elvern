from __future__ import annotations

import sqlite3

from .services.title_normalization import (
    extract_edition_identity_anywhere,
    normalize_title_key,
    resolve_title_metadata,
)


def _build_hidden_movie_key(
    *,
    title: object,
    year: object,
    original_filename: object,
) -> tuple[str | None, str | None, int | None, str]:
    try:
        resolved_year = int(year) if year not in {None, ""} else None
    except (TypeError, ValueError):
        resolved_year = None
    metadata = resolve_title_metadata(
        title=title,
        year=resolved_year,
        original_filename=original_filename,
    )
    base_title = metadata["base_title"]
    edition_identity = metadata["edition_identity"] or "standard"
    strict_edition_identity = extract_edition_identity_anywhere(title, original_filename)
    if edition_identity == "standard":
        edition_identity = strict_edition_identity
    elif strict_edition_identity != "standard":
        edition_identity = "|".join(
            part
            for part in dict.fromkeys([*edition_identity.split("|"), *strict_edition_identity.split("|")])
            if part
        )
    if not base_title or resolved_year is None:
        return None, None, None, edition_identity
    return (
        f"{normalize_title_key(base_title)}|{resolved_year}|{edition_identity}",
        base_title,
        resolved_year,
        edition_identity,
    )


def preserve_hidden_movie_keys_for_media_item(
    connection: sqlite3.Connection,
    *,
    media_item_id: int,
) -> dict[str, int]:
    media_row = connection.execute(
        """
        SELECT title, year, original_filename
        FROM media_items
        WHERE id = ?
        LIMIT 1
        """,
        (media_item_id,),
    ).fetchone()
    if media_row is None:
        return {"user_hidden_restored": 0, "global_hidden_restored": 0}

    movie_key, display_title, normalized_year, edition_identity = _build_hidden_movie_key(
        title=media_row["title"],
        year=media_row["year"],
        original_filename=media_row["original_filename"],
    )
    if not movie_key or not display_title or normalized_year is None:
        return {"user_hidden_restored": 0, "global_hidden_restored": 0}

    user_hidden_restored = 0
    user_rows = connection.execute(
        """
        SELECT user_id, hidden_at
        FROM user_hidden_media_items
        WHERE media_item_id = ?
        """,
        (media_item_id,),
    ).fetchall()
    for row in user_rows:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO user_hidden_movie_keys (
                user_id,
                movie_key,
                display_title,
                year,
                edition_identity,
                hidden_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(row["user_id"]),
                movie_key,
                display_title,
                normalized_year,
                edition_identity,
                str(row["hidden_at"]),
            ),
        )
        user_hidden_restored += max(cursor.rowcount, 0)

    global_hidden_restored = 0
    global_rows = connection.execute(
        """
        SELECT hidden_by_user_id, hidden_at
        FROM global_hidden_media_items
        WHERE media_item_id = ?
        """,
        (media_item_id,),
    ).fetchall()
    for row in global_rows:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO global_hidden_movie_keys (
                movie_key,
                display_title,
                year,
                edition_identity,
                hidden_by_user_id,
                hidden_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                movie_key,
                display_title,
                normalized_year,
                edition_identity,
                int(row["hidden_by_user_id"]),
                str(row["hidden_at"]),
            ),
        )
        global_hidden_restored += max(cursor.rowcount, 0)

    return {
        "user_hidden_restored": user_hidden_restored,
        "global_hidden_restored": global_hidden_restored,
    }


def _backfill_hidden_movie_keys(connection: sqlite3.Connection) -> None:
    user_rows = connection.execute(
        """
        SELECT
            h.user_id,
            h.hidden_at,
            m.title,
            m.year,
            m.original_filename
        FROM user_hidden_media_items h
        JOIN media_items m
          ON m.id = h.media_item_id
        """
    ).fetchall()
    for row in user_rows:
        movie_key, display_title, normalized_year, edition_identity = _build_hidden_movie_key(
            title=row["title"],
            year=row["year"],
            original_filename=row["original_filename"],
        )
        if not movie_key or not display_title or normalized_year is None:
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO user_hidden_movie_keys (
                user_id,
                movie_key,
                display_title,
                year,
                edition_identity,
                hidden_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(row["user_id"]),
                movie_key,
                display_title,
                normalized_year,
                edition_identity,
                str(row["hidden_at"]),
            ),
        )

    global_rows = connection.execute(
        """
        SELECT
            h.hidden_by_user_id,
            h.hidden_at,
            m.title,
            m.year,
            m.original_filename
        FROM global_hidden_media_items h
        JOIN media_items m
          ON m.id = h.media_item_id
        """
    ).fetchall()
    for row in global_rows:
        movie_key, display_title, normalized_year, edition_identity = _build_hidden_movie_key(
            title=row["title"],
            year=row["year"],
            original_filename=row["original_filename"],
        )
        if not movie_key or not display_title or normalized_year is None:
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO global_hidden_movie_keys (
                movie_key,
                display_title,
                year,
                edition_identity,
                hidden_by_user_id,
                hidden_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                movie_key,
                display_title,
                normalized_year,
                edition_identity,
                int(row["hidden_by_user_id"]),
                str(row["hidden_at"]),
            ),
        )
