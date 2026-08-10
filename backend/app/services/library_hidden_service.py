from __future__ import annotations

from .library_movie_identity_service import (
    _dedupe_group_key,
    _dedupe_rows,
    _edition_label,
    _quality_sort_key,
    _row_hidden_movie_key,
    _row_matches_hidden_keys,
)
from .local_library_source_service import ensure_current_shared_local_source_binding
from .library_presentation_service import _poster_directory, _poster_url_for_row
from .title_normalization import resolve_title_metadata
from .audit_service import write_audit_event_in_connection
from ..config import Settings
from ..db import get_connection, utcnow_iso
from ..db_hidden_movie_keys import (
    hidden_key_candidates_for_row,
    materialize_legacy_hidden_coverage_for_item,
    resolve_hidden_copy_identity_payload,
)


def _matching_hidden_key(row, hidden_key_records: dict[str, object]) -> str | None:
    return next(
        (key for key in hidden_key_candidates_for_row(row) if key in hidden_key_records),
        None,
    )


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
        if _row_matches_hidden_keys(row, globally_hidden_movie_keys):
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
        if _row_matches_hidden_keys(row, hidden_movie_keys):
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
        shared_local_source_id = ensure_current_shared_local_source_binding(
            settings,
            connection=connection,
        )
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
                m.hidden_copy_identity,
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
                    (
                        COALESCE(m.source_kind, 'local') = 'local'
                        AND m.library_source_id = ?
                    )
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
            (user_id, user_id, user_id, shared_local_source_id, user_id),
        ).fetchall()
        visible_candidate_rows = connection.execute(
            base_query_sql + " ORDER BY lower(m.title) ASC",
            (user_id, user_id, shared_local_source_id, user_id),
        ).fetchall()

    payload: list[dict[str, object]] = []
    seen_ids: set[int] = set()
    seen_movie_keys: set[str] = set()
    for row in rows:
        if int(row["id"]) in globally_hidden_media_item_ids:
            continue
        row_key = _row_hidden_movie_key(row)
        if _row_matches_hidden_keys(row, set(globally_hidden_movie_key_records)):
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
        matching_key = _matching_hidden_key(row, hidden_movie_key_records)
        row_key = _row_hidden_movie_key(row)
        if not matching_key:
            continue
        if _row_matches_hidden_keys(row, set(globally_hidden_movie_key_records)):
            continue
        current = representatives_by_key.get(matching_key)
        if current is None or _quality_sort_key(row) > _quality_sort_key(current):
            representatives_by_key[matching_key] = row

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
                m.hidden_copy_identity,
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
                m.hidden_copy_identity,
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
        matching_key = _matching_hidden_key(row, global_hidden_movie_key_records)
        row_key = _row_hidden_movie_key(row)
        if not matching_key:
            continue
        current = representatives_by_key.get(matching_key)
        if current is None or _quality_sort_key(row) > _quality_sort_key(current):
            representatives_by_key[matching_key] = row

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


def _load_hidden_media_identity(connection, *, item_id: int):
    media_item = connection.execute(
        """
        SELECT
            id, title, year, original_filename, file_path,
            COALESCE(source_kind, 'local') AS source_kind,
            library_source_id, external_media_id, cloud_resource_key,
            hidden_copy_identity
        FROM media_items
        WHERE id = ?
        LIMIT 1
        """,
        (item_id,),
    ).fetchone()
    if media_item is None:
        return None, None
    return media_item, resolve_hidden_copy_identity_payload(connection, media_item)


def _hide_for_user_in_connection(
    connection,
    *,
    user_id: int,
    item_id: int,
    movie_identity,
    hidden_at: str,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO user_hidden_media_items (user_id, media_item_id, hidden_at)
        VALUES (?, ?, ?)
        """,
        (user_id, item_id, hidden_at),
    )
    if movie_identity is not None:
        connection.execute(
            """
            INSERT INTO user_hidden_movie_keys (
                user_id,
                movie_key,
                display_title,
                year,
                edition_identity,
                representative_media_item_id,
                hidden_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, movie_key) DO UPDATE SET
                representative_media_item_id = excluded.representative_media_item_id
            """,
            (
                user_id,
                str(movie_identity["movie_key"]),
                str(movie_identity["display_title"]),
                int(movie_identity["year"]),
                str(movie_identity["edition_identity"]),
                item_id,
                hidden_at,
            ),
        )


def _show_for_user_in_connection(
    connection,
    *,
    user_id: int,
    item_id: int,
    movie_identity,
) -> None:
    connection.execute(
        """
        DELETE FROM user_hidden_media_items
        WHERE user_id = ? AND media_item_id = ?
        """,
        (user_id, item_id),
    )
    if movie_identity is not None:
        connection.execute(
            """
            DELETE FROM user_hidden_movie_keys
            WHERE user_id = ? AND movie_key = ?
            """,
            (user_id, str(movie_identity["movie_key"])),
        )


def _hide_globally_in_connection(
    connection,
    *,
    actor_user_id: int,
    item_id: int,
    movie_identity,
    hidden_at: str,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO global_hidden_media_items (
            media_item_id,
            hidden_by_user_id,
            hidden_at
        ) VALUES (?, ?, ?)
        """,
        (item_id, actor_user_id, hidden_at),
    )
    if movie_identity is not None:
        connection.execute(
            """
            INSERT INTO global_hidden_movie_keys (
                movie_key,
                display_title,
                year,
                edition_identity,
                representative_media_item_id,
                hidden_by_user_id,
                hidden_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(movie_key) DO UPDATE SET
                representative_media_item_id = excluded.representative_media_item_id
            """,
            (
                str(movie_identity["movie_key"]),
                str(movie_identity["display_title"]),
                int(movie_identity["year"]),
                str(movie_identity["edition_identity"]),
                item_id,
                actor_user_id,
                hidden_at,
            ),
        )


def _show_globally_in_connection(connection, *, item_id: int, movie_identity) -> None:
    connection.execute(
        "DELETE FROM global_hidden_media_items WHERE media_item_id = ?",
        (item_id,),
    )
    if movie_identity is not None:
        connection.execute(
            "DELETE FROM global_hidden_movie_keys WHERE movie_key = ?",
            (str(movie_identity["movie_key"]),),
        )


def _hidden_scope_state(connection, *, user_id: int, item_id: int, movie_identity) -> dict[str, object]:
    personal_item = connection.execute(
        """
        SELECT hidden_at
        FROM user_hidden_media_items
        WHERE user_id = ? AND media_item_id = ?
        """,
        (user_id, item_id),
    ).fetchone()
    global_item = connection.execute(
        "SELECT hidden_at FROM global_hidden_media_items WHERE media_item_id = ?",
        (item_id,),
    ).fetchone()
    personal_key = None
    global_key = None
    if movie_identity is not None:
        movie_key = str(movie_identity["movie_key"])
        personal_key = connection.execute(
            """
            SELECT hidden_at
            FROM user_hidden_movie_keys
            WHERE user_id = ? AND movie_key = ?
            """,
            (user_id, movie_key),
        ).fetchone()
        global_key = connection.execute(
            "SELECT hidden_at FROM global_hidden_movie_keys WHERE movie_key = ?",
            (movie_key,),
        ).fetchone()
    return {
        "personal_item": personal_item,
        "personal_key": personal_key,
        "global_item": global_item,
        "global_key": global_key,
    }


def _scope_is_exact(state: dict[str, object], *, target_scope: str, has_movie_key: bool) -> bool:
    personal_complete = state["personal_item"] is not None and (
        not has_movie_key or state["personal_key"] is not None
    )
    personal_absent = state["personal_item"] is None and (
        not has_movie_key or state["personal_key"] is None
    )
    global_complete = state["global_item"] is not None and (
        not has_movie_key or state["global_key"] is not None
    )
    global_absent = state["global_item"] is None and (
        not has_movie_key or state["global_key"] is None
    )
    return (
        global_complete and personal_absent
        if target_scope == "global"
        else personal_complete and global_absent
    )


def _scope_hidden_at(state: dict[str, object], *, target_scope: str) -> str:
    candidates = (
        (state["global_item"], state["global_key"])
        if target_scope == "global"
        else (state["personal_item"], state["personal_key"])
    )
    for row in candidates:
        if row is not None:
            return str(row["hidden_at"])
    return utcnow_iso()


def _set_hidden_scope_in_connection(
    connection,
    *,
    actor_user_id: int,
    item_id: int,
    target_scope: str,
    movie_identity,
) -> tuple[bool, str]:
    state = _hidden_scope_state(
        connection,
        user_id=actor_user_id,
        item_id=item_id,
        movie_identity=movie_identity,
    )
    if _scope_is_exact(
        state,
        target_scope=target_scope,
        has_movie_key=movie_identity is not None,
    ):
        return False, _scope_hidden_at(state, target_scope=target_scope)

    hidden_at = _scope_hidden_at(state, target_scope=target_scope)
    if target_scope == "global":
        _hide_globally_in_connection(
            connection,
            actor_user_id=actor_user_id,
            item_id=item_id,
            movie_identity=movie_identity,
            hidden_at=hidden_at,
        )
        _show_for_user_in_connection(
            connection,
            user_id=actor_user_id,
            item_id=item_id,
            movie_identity=movie_identity,
        )
    elif target_scope == "personal":
        _hide_for_user_in_connection(
            connection,
            user_id=actor_user_id,
            item_id=item_id,
            movie_identity=movie_identity,
            hidden_at=hidden_at,
        )
        _show_globally_in_connection(
            connection,
            item_id=item_id,
            movie_identity=movie_identity,
        )
    else:
        raise ValueError("invalid_scope")
    return True, hidden_at


def hide_media_item_for_user(settings: Settings, *, user_id: int, item_id: int) -> None:
    with get_connection(settings) as connection:
        try:
            materialize_legacy_hidden_coverage_for_item(
                connection,
                media_item_id=item_id,
                user_id=user_id,
                include_global=False,
            )
            media_item, movie_identity = _load_hidden_media_identity(connection, item_id=item_id)
            if media_item is None:
                raise ValueError("not_found")
            _hide_for_user_in_connection(
                connection,
                user_id=user_id,
                item_id=item_id,
                movie_identity=movie_identity,
                hidden_at=utcnow_iso(),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def hide_media_item_globally(
    settings: Settings,
    *,
    actor_user_id: int,
    item_id: int,
    actor_username: str | None = None,
    actor_role: str | None = None,
    actor_session_id: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    with get_connection(settings) as connection:
        try:
            materialize_legacy_hidden_coverage_for_item(
                connection,
                media_item_id=item_id,
                user_id=None,
                include_global=True,
            )
            media_item, movie_identity = _load_hidden_media_identity(connection, item_id=item_id)
            if media_item is None:
                raise ValueError("not_found")
            _hide_globally_in_connection(
                connection,
                actor_user_id=actor_user_id,
                item_id=item_id,
                movie_identity=movie_identity,
                hidden_at=utcnow_iso(),
            )
            write_audit_event_in_connection(
                connection,
                action="admin.library.hide_global",
                outcome="success",
                user_id=actor_user_id,
                username=actor_username,
                role=actor_role,
                session_id=actor_session_id,
                target_type="media_item",
                target_id=item_id,
                media_item_id=item_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def show_media_item_for_user(settings: Settings, *, user_id: int, item_id: int) -> None:
    with get_connection(settings) as connection:
        try:
            materialize_legacy_hidden_coverage_for_item(
                connection,
                media_item_id=item_id,
                user_id=user_id,
                include_global=False,
            )
            _media_item, movie_identity = _load_hidden_media_identity(connection, item_id=item_id)
            _show_for_user_in_connection(
                connection,
                user_id=user_id,
                item_id=item_id,
                movie_identity=movie_identity,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def show_media_item_globally(
    settings: Settings,
    *,
    item_id: int,
    actor_user_id: int | None = None,
    actor_username: str | None = None,
    actor_role: str | None = None,
    actor_session_id: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    with get_connection(settings) as connection:
        try:
            materialize_legacy_hidden_coverage_for_item(
                connection,
                media_item_id=item_id,
                user_id=None,
                include_global=True,
            )
            _media_item, movie_identity = _load_hidden_media_identity(connection, item_id=item_id)
            _show_globally_in_connection(
                connection,
                item_id=item_id,
                movie_identity=movie_identity,
            )
            write_audit_event_in_connection(
                connection,
                action="admin.library.show_global",
                outcome="success",
                user_id=actor_user_id,
                username=actor_username,
                role=actor_role,
                session_id=actor_session_id,
                target_type="media_item",
                target_id=item_id,
                media_item_id=item_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def set_hidden_media_item_scope(
    settings: Settings,
    *,
    actor_user_id: int,
    actor_username: str,
    actor_role: str,
    actor_session_id: int | None,
    item_id: int,
    target_scope: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, object]:
    if target_scope not in {"personal", "global"}:
        raise ValueError("invalid_scope")
    with get_connection(settings) as connection:
        try:
            materialize_legacy_hidden_coverage_for_item(
                connection,
                media_item_id=item_id,
                user_id=actor_user_id,
                include_global=True,
            )
            media_item, movie_identity = _load_hidden_media_identity(connection, item_id=item_id)
            if media_item is None:
                raise ValueError("not_found")
            changed, hidden_at = _set_hidden_scope_in_connection(
                connection,
                actor_user_id=actor_user_id,
                item_id=item_id,
                target_scope=target_scope,
                movie_identity=movie_identity,
            )
            if changed:
                write_audit_event_in_connection(
                    connection,
                    action="admin.library.set_hidden_scope",
                    outcome="success",
                    user_id=actor_user_id,
                    username=actor_username,
                    role=actor_role,
                    session_id=actor_session_id,
                    target_type="media_item",
                    target_id=item_id,
                    media_item_id=item_id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    details={"target_scope": target_scope},
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    message = (
        "This movie is hidden for everyone."
        if target_scope == "global"
        else "This movie is now hidden only for your account."
    )
    return {
        "item_id": item_id,
        "target_scope": target_scope,
        "changed": changed,
        "hidden_at": hidden_at,
        "message": message,
    }
