from __future__ import annotations

from datetime import datetime, timezone
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


def _parse_hidden_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed


def prune_recreated_local_hidden_movie_keys(
    connection: sqlite3.Connection,
    *,
    shared_local_source_id: int,
) -> dict[str, int]:
    local_rows = connection.execute(
        """
        SELECT id, title, year, original_filename, created_at
        FROM media_items
        WHERE COALESCE(source_kind, 'local') = 'local'
          AND library_source_id = ?
        """,
        (shared_local_source_id,),
    ).fetchall()
    local_candidates_by_key: dict[str, list[sqlite3.Row]] = {}
    for row in local_rows:
        movie_key, _display_title, normalized_year, _edition_identity = _build_hidden_movie_key(
            title=row["title"],
            year=row["year"],
            original_filename=row["original_filename"],
        )
        if not movie_key or normalized_year is None:
            continue
        local_candidates_by_key.setdefault(movie_key, []).append(row)

    backing_global_keys: set[str] = set()
    global_backing_rows = connection.execute(
        """
        SELECT m.title, m.year, m.original_filename
        FROM global_hidden_media_items h
        JOIN media_items m
          ON m.id = h.media_item_id
        """
    ).fetchall()
    for row in global_backing_rows:
        movie_key, _display_title, normalized_year, _edition_identity = _build_hidden_movie_key(
            title=row["title"],
            year=row["year"],
            original_filename=row["original_filename"],
        )
        if movie_key and normalized_year is not None:
            backing_global_keys.add(movie_key)

    backing_user_keys: dict[int, set[str]] = {}
    user_backing_rows = connection.execute(
        """
        SELECT h.user_id, m.title, m.year, m.original_filename
        FROM user_hidden_media_items h
        JOIN media_items m
          ON m.id = h.media_item_id
        """
    ).fetchall()
    for row in user_backing_rows:
        movie_key, _display_title, normalized_year, _edition_identity = _build_hidden_movie_key(
            title=row["title"],
            year=row["year"],
            original_filename=row["original_filename"],
        )
        if movie_key and normalized_year is not None:
            backing_user_keys.setdefault(int(row["user_id"]), set()).add(movie_key)

    pruned_global = 0
    global_hidden_rows = connection.execute(
        """
        SELECT movie_key, hidden_at
        FROM global_hidden_movie_keys
        """
    ).fetchall()
    for row in global_hidden_rows:
        movie_key = str(row["movie_key"] or "").strip()
        if not movie_key or movie_key in backing_global_keys:
            continue
        candidates = local_candidates_by_key.get(movie_key, [])
        if len(candidates) != 1:
            continue
        created_at = _parse_hidden_timestamp(candidates[0]["created_at"])
        hidden_at = _parse_hidden_timestamp(row["hidden_at"])
        if created_at is None or hidden_at is None or created_at <= hidden_at:
            continue
        connection.execute(
            "DELETE FROM global_hidden_movie_keys WHERE movie_key = ?",
            (movie_key,),
        )
        pruned_global += 1

    pruned_user = 0
    user_hidden_rows = connection.execute(
        """
        SELECT user_id, movie_key, hidden_at
        FROM user_hidden_movie_keys
        """
    ).fetchall()
    for row in user_hidden_rows:
        user_id = int(row["user_id"])
        movie_key = str(row["movie_key"] or "").strip()
        if not movie_key or movie_key in backing_user_keys.get(user_id, set()):
            continue
        candidates = local_candidates_by_key.get(movie_key, [])
        if len(candidates) != 1:
            continue
        created_at = _parse_hidden_timestamp(candidates[0]["created_at"])
        hidden_at = _parse_hidden_timestamp(row["hidden_at"])
        if created_at is None or hidden_at is None or created_at <= hidden_at:
            continue
        connection.execute(
            "DELETE FROM user_hidden_movie_keys WHERE user_id = ? AND movie_key = ?",
            (user_id, movie_key),
        )
        pruned_user += 1

    return {
        "global_hidden_movie_keys_pruned": pruned_global,
        "user_hidden_movie_keys_pruned": pruned_user,
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
