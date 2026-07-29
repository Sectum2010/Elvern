from __future__ import annotations

import hashlib
import json
import os
import sqlite3

from .services.title_normalization import (
    extract_edition_identity_anywhere,
    normalize_title_key,
    normalize_title_source,
    resolve_title_metadata,
)


HIDDEN_COPY_IDENTITY_PREFIX = "copy-v2:"


def _row_value(row, key: str, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    if hasattr(row, "keys") and key in row.keys():
        return row[key]
    return default


def _legacy_identity_metadata(
    *,
    title: object,
    year: object,
    original_filename: object,
) -> tuple[str | None, int | None, str]:
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
            for part in dict.fromkeys(
                [*edition_identity.split("|"), *strict_edition_identity.split("|")]
            )
            if part
        )
    return base_title, resolved_year, edition_identity


def _build_hidden_movie_group_key(
    *,
    title: object,
    year: object,
    original_filename: object,
) -> tuple[str | None, str | None, int | None, str]:
    base_title, resolved_year, edition_identity = _legacy_identity_metadata(
        title=title,
        year=year,
        original_filename=original_filename,
    )
    if not base_title or resolved_year is None:
        return None, base_title, resolved_year, edition_identity
    return (
        f"{normalize_title_key(base_title)}|{resolved_year}|{edition_identity}",
        base_title,
        resolved_year,
        edition_identity,
    )


def _build_hidden_movie_key(
    *,
    title: object,
    year: object,
    original_filename: object,
) -> tuple[str | None, str | None, int | None, str]:
    """Build the transitional filename-copy key used before copy-v2."""
    group_key, base_title, resolved_year, edition_identity = _build_hidden_movie_group_key(
        title=title,
        year=year,
        original_filename=original_filename,
    )
    filename_signature = normalize_title_key(
        normalize_title_source(str(original_filename or "").strip())
    )
    if not group_key or not filename_signature:
        return None, base_title, resolved_year, edition_identity
    return (
        f"{group_key}|copy:{filename_signature}",
        base_title,
        resolved_year,
        edition_identity,
    )


def _legacy_hidden_keys_for_row(row) -> tuple[str, ...]:
    group_key, _title, _year, _edition = _build_hidden_movie_group_key(
        title=_row_value(row, "title"),
        year=_row_value(row, "year"),
        original_filename=_row_value(row, "original_filename"),
    )
    filename_key, _title, _year, _edition = _build_hidden_movie_key(
        title=_row_value(row, "title"),
        year=_row_value(row, "year"),
        original_filename=_row_value(row, "original_filename"),
    )
    return tuple(key for key in (group_key, filename_key) if key)


def _normalized_local_locator(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return os.path.normcase(os.path.normpath(raw))


def _copy_identity_material(row) -> dict[str, object] | None:
    source_kind = str(_row_value(row, "source_kind", "local") or "local").strip().lower()
    source_id = int(_row_value(row, "library_source_id", 0) or 0)
    if source_kind == "cloud":
        external_media_id = str(_row_value(row, "external_media_id", "") or "").strip()
        if external_media_id:
            return {
                "authority": "cloud-external-id",
                "source_kind": source_kind,
                "source_id": source_id,
                "external_media_id": external_media_id,
            }
        cloud_resource_key = str(_row_value(row, "cloud_resource_key", "") or "").strip()
        if cloud_resource_key:
            return {
                "authority": "cloud-resource-key",
                "source_kind": source_kind,
                "source_id": source_id,
                "cloud_resource_key": cloud_resource_key,
            }
    locator = _normalized_local_locator(_row_value(row, "file_path"))
    if not locator:
        return None
    return {
        "authority": "local-path" if source_kind == "local" else "source-path",
        "source_kind": source_kind,
        "source_id": source_id,
        "locator": locator,
    }


def _derive_hidden_copy_identity(row) -> str | None:
    material = _copy_identity_material(row)
    if material is None:
        return None
    encoded = json.dumps(
        material,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{HIDDEN_COPY_IDENTITY_PREFIX}{hashlib.sha256(encoded).hexdigest()}"


def resolve_hidden_copy_identity(
    connection: sqlite3.Connection,
    *,
    media_item_id: int | None = None,
    media_row=None,
) -> str | None:
    """Return and persist the one authoritative opaque Hidden copy identity."""
    row = media_row
    if row is None:
        if media_item_id is None:
            raise ValueError("media_item_id or media_row is required")
        row = connection.execute(
            """
            SELECT
                id,
                file_path,
                COALESCE(source_kind, 'local') AS source_kind,
                library_source_id,
                external_media_id,
                cloud_resource_key,
                hidden_copy_identity
            FROM media_items
            WHERE id = ?
            LIMIT 1
            """,
            (media_item_id,),
        ).fetchone()
    if row is None:
        return None
    existing = str(_row_value(row, "hidden_copy_identity", "") or "").strip()
    if existing.startswith(HIDDEN_COPY_IDENTITY_PREFIX):
        return existing
    identity = _derive_hidden_copy_identity(row)
    row_id = int(_row_value(row, "id", media_item_id or 0) or 0)
    if identity and row_id > 0:
        connection.execute(
            "UPDATE media_items SET hidden_copy_identity = ? WHERE id = ?",
            (identity, row_id),
        )
    return identity


def resolve_hidden_copy_identity_payload(
    connection: sqlite3.Connection,
    row,
) -> dict[str, object] | None:
    copy_identity = resolve_hidden_copy_identity(connection, media_row=row)
    if not copy_identity:
        return None
    base_title, resolved_year, edition_identity = _legacy_identity_metadata(
        title=_row_value(row, "title"),
        year=_row_value(row, "year"),
        original_filename=_row_value(row, "original_filename"),
    )
    display_title = str(base_title or _row_value(row, "title") or "Untitled").strip() or "Untitled"
    return {
        "movie_key": copy_identity,
        "display_title": display_title,
        "year": resolved_year if resolved_year is not None else 0,
        "edition_identity": edition_identity,
    }


def hidden_key_candidates_for_row(row) -> tuple[str, ...]:
    current = str(_row_value(row, "hidden_copy_identity", "") or "").strip()
    candidates = [current] if current.startswith(HIDDEN_COPY_IDENTITY_PREFIX) else []
    candidates.extend(_legacy_hidden_keys_for_row(row))
    return tuple(dict.fromkeys(candidates))


def _insert_user_copy_key(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    payload: dict[str, object],
    hidden_at: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO user_hidden_movie_keys (
            user_id, movie_key, display_title, year, edition_identity, hidden_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            payload["movie_key"],
            payload["display_title"],
            payload["year"],
            payload["edition_identity"],
            hidden_at,
        ),
    )
    return max(cursor.rowcount, 0)


def _insert_global_copy_key(
    connection: sqlite3.Connection,
    *,
    hidden_by_user_id: int,
    payload: dict[str, object],
    hidden_at: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO global_hidden_movie_keys (
            movie_key, display_title, year, edition_identity,
            hidden_by_user_id, hidden_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            payload["movie_key"],
            payload["display_title"],
            payload["year"],
            payload["edition_identity"],
            hidden_by_user_id,
            hidden_at,
        ),
    )
    return max(cursor.rowcount, 0)


def _load_identity_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = connection.execute(
        """
        SELECT
            id, title, year, original_filename, file_path,
            COALESCE(source_kind, 'local') AS source_kind,
            library_source_id, external_media_id, cloud_resource_key,
            hidden_copy_identity
        FROM media_items
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        resolve_hidden_copy_identity(connection, media_row=row)
    return connection.execute(
        """
        SELECT
            id, title, year, original_filename, file_path,
            COALESCE(source_kind, 'local') AS source_kind,
            library_source_id, external_media_id, cloud_resource_key,
            hidden_copy_identity
        FROM media_items
        ORDER BY id
        """
    ).fetchall()


def _legacy_candidate_index(rows: list[sqlite3.Row]) -> dict[str, list[sqlite3.Row]]:
    candidates: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        for key in _legacy_hidden_keys_for_row(row):
            candidates.setdefault(key, []).append(row)
    return candidates


def migrate_legacy_hidden_movie_keys(connection: sqlite3.Connection) -> dict[str, int]:
    """Conservatively materialize legacy group/filename records as copy-v2 keys."""
    rows = _load_identity_rows(connection)
    rows_by_id = {int(row["id"]): row for row in rows}
    candidates_by_key = _legacy_candidate_index(rows)
    user_direct: dict[tuple[int, str], list[sqlite3.Row]] = {}
    for direct in connection.execute(
        "SELECT user_id, media_item_id FROM user_hidden_media_items"
    ).fetchall():
        media_row = rows_by_id.get(int(direct["media_item_id"]))
        if media_row is None:
            continue
        for key in _legacy_hidden_keys_for_row(media_row):
            user_direct.setdefault((int(direct["user_id"]), key), []).append(media_row)
    global_direct: dict[str, list[sqlite3.Row]] = {}
    for direct in connection.execute(
        "SELECT media_item_id FROM global_hidden_media_items"
    ).fetchall():
        media_row = rows_by_id.get(int(direct["media_item_id"]))
        if media_row is None:
            continue
        for key in _legacy_hidden_keys_for_row(media_row):
            global_direct.setdefault(key, []).append(media_row)

    migrated_user = 0
    retained_user = 0
    for record in connection.execute(
        """
        SELECT user_id, movie_key, hidden_at
        FROM user_hidden_movie_keys
        WHERE movie_key NOT LIKE 'copy-v2:%'
        """
    ).fetchall():
        user_id = int(record["user_id"])
        legacy_key = str(record["movie_key"])
        direct_matches = user_direct.get((user_id, legacy_key), [])
        matches = direct_matches or candidates_by_key.get(legacy_key, [])
        if not direct_matches and len(matches) != 1:
            retained_user += 1
            continue
        payloads = [
            payload
            for row in matches
            if (payload := resolve_hidden_copy_identity_payload(connection, row)) is not None
        ]
        if len(payloads) != len(matches):
            retained_user += 1
            continue
        for payload in payloads:
            _insert_user_copy_key(
                connection,
                user_id=user_id,
                payload=payload,
                hidden_at=str(record["hidden_at"]),
            )
        connection.execute(
            "DELETE FROM user_hidden_movie_keys WHERE user_id = ? AND movie_key = ?",
            (user_id, legacy_key),
        )
        migrated_user += 1

    migrated_global = 0
    retained_global = 0
    for record in connection.execute(
        """
        SELECT movie_key, hidden_by_user_id, hidden_at
        FROM global_hidden_movie_keys
        WHERE movie_key NOT LIKE 'copy-v2:%'
        """
    ).fetchall():
        legacy_key = str(record["movie_key"])
        direct_matches = global_direct.get(legacy_key, [])
        matches = direct_matches or candidates_by_key.get(legacy_key, [])
        if not direct_matches and len(matches) != 1:
            retained_global += 1
            continue
        payloads = [
            payload
            for row in matches
            if (payload := resolve_hidden_copy_identity_payload(connection, row)) is not None
        ]
        if len(payloads) != len(matches):
            retained_global += 1
            continue
        for payload in payloads:
            _insert_global_copy_key(
                connection,
                hidden_by_user_id=int(record["hidden_by_user_id"]),
                payload=payload,
                hidden_at=str(record["hidden_at"]),
            )
        connection.execute(
            "DELETE FROM global_hidden_movie_keys WHERE movie_key = ?",
            (legacy_key,),
        )
        migrated_global += 1
    return {
        "user_legacy_keys_migrated": migrated_user,
        "user_legacy_keys_retained": retained_user,
        "global_legacy_keys_migrated": migrated_global,
        "global_legacy_keys_retained": retained_global,
    }


def materialize_legacy_hidden_coverage_for_item(
    connection: sqlite3.Connection,
    *,
    media_item_id: int,
    user_id: int | None,
    include_global: bool,
) -> None:
    selected = connection.execute(
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
        (media_item_id,),
    ).fetchone()
    if selected is None:
        return
    matching_legacy_keys = _legacy_hidden_keys_for_row(selected)
    if not matching_legacy_keys:
        return
    rows = _load_identity_rows(connection)
    candidates_by_key = _legacy_candidate_index(rows)
    for legacy_key in matching_legacy_keys:
        if user_id is not None:
            record = connection.execute(
                """
                SELECT hidden_at
                FROM user_hidden_movie_keys
                WHERE user_id = ? AND movie_key = ?
                """,
                (user_id, legacy_key),
            ).fetchone()
            if record is not None:
                candidates = candidates_by_key.get(legacy_key, [])
                payloads = [
                    payload
                    for row in candidates
                    if (payload := resolve_hidden_copy_identity_payload(connection, row)) is not None
                ]
                if len(payloads) == len(candidates) and payloads:
                    for payload in payloads:
                        _insert_user_copy_key(
                            connection,
                            user_id=user_id,
                            payload=payload,
                            hidden_at=str(record["hidden_at"]),
                        )
                    connection.execute(
                        "DELETE FROM user_hidden_movie_keys WHERE user_id = ? AND movie_key = ?",
                        (user_id, legacy_key),
                    )
        if include_global:
            record = connection.execute(
                """
                SELECT hidden_by_user_id, hidden_at
                FROM global_hidden_movie_keys
                WHERE movie_key = ?
                """,
                (legacy_key,),
            ).fetchone()
            if record is not None:
                candidates = candidates_by_key.get(legacy_key, [])
                payloads = [
                    payload
                    for row in candidates
                    if (payload := resolve_hidden_copy_identity_payload(connection, row)) is not None
                ]
                if len(payloads) == len(candidates) and payloads:
                    for payload in payloads:
                        _insert_global_copy_key(
                            connection,
                            hidden_by_user_id=int(record["hidden_by_user_id"]),
                            payload=payload,
                            hidden_at=str(record["hidden_at"]),
                        )
                    connection.execute(
                        "DELETE FROM global_hidden_movie_keys WHERE movie_key = ?",
                        (legacy_key,),
                    )


def preserve_hidden_movie_keys_for_media_item(
    connection: sqlite3.Connection,
    *,
    media_item_id: int,
) -> dict[str, int]:
    media_row = connection.execute(
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
        (media_item_id,),
    ).fetchone()
    if media_row is None:
        return {"user_hidden_restored": 0, "global_hidden_restored": 0}
    payload = resolve_hidden_copy_identity_payload(connection, media_row)
    if payload is None:
        return {"user_hidden_restored": 0, "global_hidden_restored": 0}

    user_count = 0
    for row in connection.execute(
        "SELECT user_id, hidden_at FROM user_hidden_media_items WHERE media_item_id = ?",
        (media_item_id,),
    ).fetchall():
        user_count += _insert_user_copy_key(
            connection,
            user_id=int(row["user_id"]),
            payload=payload,
            hidden_at=str(row["hidden_at"]),
        )
    global_count = 0
    for row in connection.execute(
        """
        SELECT hidden_by_user_id, hidden_at
        FROM global_hidden_media_items
        WHERE media_item_id = ?
        """,
        (media_item_id,),
    ).fetchall():
        global_count += _insert_global_copy_key(
            connection,
            hidden_by_user_id=int(row["hidden_by_user_id"]),
            payload=payload,
            hidden_at=str(row["hidden_at"]),
        )
    return {
        "user_hidden_restored": user_count,
        "global_hidden_restored": global_count,
    }


def prune_recreated_local_hidden_movie_keys(
    connection: sqlite3.Connection,
    *,
    shared_local_source_id: int,
) -> dict[str, int]:
    del shared_local_source_id
    summary = migrate_legacy_hidden_movie_keys(connection)
    return {
        "global_hidden_movie_keys_pruned": summary["global_legacy_keys_migrated"],
        "user_hidden_movie_keys_pruned": summary["user_legacy_keys_migrated"],
    }


def _backfill_hidden_movie_keys(connection: sqlite3.Connection) -> None:
    rows = _load_identity_rows(connection)
    rows_by_id = {int(row["id"]): row for row in rows}
    for record in connection.execute(
        "SELECT user_id, media_item_id, hidden_at FROM user_hidden_media_items"
    ).fetchall():
        row = rows_by_id.get(int(record["media_item_id"]))
        payload = resolve_hidden_copy_identity_payload(connection, row) if row is not None else None
        if payload is not None:
            _insert_user_copy_key(
                connection,
                user_id=int(record["user_id"]),
                payload=payload,
                hidden_at=str(record["hidden_at"]),
            )
    for record in connection.execute(
        """
        SELECT media_item_id, hidden_by_user_id, hidden_at
        FROM global_hidden_media_items
        """
    ).fetchall():
        row = rows_by_id.get(int(record["media_item_id"]))
        payload = resolve_hidden_copy_identity_payload(connection, row) if row is not None else None
        if payload is not None:
            _insert_global_copy_key(
                connection,
                hidden_by_user_id=int(record["hidden_by_user_id"]),
                payload=payload,
                hidden_at=str(record["hidden_at"]),
            )
    migrate_legacy_hidden_movie_keys(connection)
