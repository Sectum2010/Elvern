from __future__ import annotations

from .library_movie_identity_service import (
    _dedupe_group_key,
    _dedupe_rows,
    _edition_label,
    _movie_identity_payload,
    _quality_sort_key,
    _row_hidden_movie_key,
)
from .library_presentation_service import _poster_directory, _poster_url_for_row
from .title_normalization import resolve_title_metadata
from ..config import Settings
from ..db import get_connection


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


def list_hidden_media_items(
    settings: Settings,
    *,
    user_id: int,
    base_query_sql: str,
    utc_iso_to_epoch_seconds,
) -> list[dict[str, object]]:
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
            base_query_sql + " ORDER BY lower(m.title) ASC",
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
    payload.sort(key=lambda item: (-utc_iso_to_epoch_seconds(item["hidden_at"]), str(item["title"]).lower()))
    return payload


def list_globally_hidden_media_items(
    settings: Settings,
    *,
    utc_iso_to_epoch_seconds,
) -> list[dict[str, object]]:
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
    payload.sort(key=lambda item: (-utc_iso_to_epoch_seconds(item["hidden_at"]), str(item["title"]).lower()))
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
