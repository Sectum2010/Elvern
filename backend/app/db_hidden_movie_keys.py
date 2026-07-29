from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

from .services.title_normalization import (
    extract_edition_identity_anywhere,
    normalize_title_key,
    normalize_title_source,
    resolve_title_metadata,
)


HIDDEN_COPY_IDENTITY_PREFIX = "copy-v2:"
LOCAL_COPY_EVIDENCE_SAMPLE_BYTES = 64 * 1024
LOCAL_COPY_IDENTITY_RANDOM_BYTES = 32
LOCAL_COPY_EVIDENCE_ALGORITHM_VERSION = "local-copy-evidence-v2"


class LocalCopyEvidenceMemo:
    """Cache bounded content samples for the lifetime of one local scan."""

    def __init__(self) -> None:
        self._sample_hashes: dict[tuple[object, ...], str | None] = {}

    def sample_hash(
        self,
        row,
        *,
        path: Path,
        metadata: os.stat_result,
    ) -> str | None:
        source_kind, source_id = _local_source_scope(row)
        key = (
            LOCAL_COPY_EVIDENCE_ALGORITHM_VERSION,
            source_kind,
            source_id,
            _normalized_local_locator(path),
            int(metadata.st_dev),
            int(metadata.st_ino),
            int(metadata.st_size),
            int(metadata.st_mtime_ns),
            int(metadata.st_ctime_ns),
        )
        if key not in self._sample_hashes:
            self._sample_hashes[key] = _bounded_local_content_hash(path, metadata)
        return self._sample_hashes[key]


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


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _opaque_digest(*parts: object) -> str:
    encoded = json.dumps(
        list(parts),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _local_source_scope(row) -> tuple[str, int]:
    source_kind = str(_row_value(row, "source_kind", "local") or "local").strip().lower()
    source_id = int(_row_value(row, "library_source_id", 0) or 0)
    return source_kind, source_id


def _local_locator_hash(row, *, file_path: object | None = None) -> str | None:
    source_kind, source_id = _local_source_scope(row)
    locator = _normalized_local_locator(
        _row_value(row, "file_path") if file_path is None else file_path
    )
    if source_kind != "local" or not locator:
        return None
    return _opaque_digest("local-locator-v1", source_kind, source_id, locator)


def _bounded_local_content_hash(path: Path, metadata: os.stat_result) -> str | None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_dev != metadata.st_dev
            or before.st_ino != metadata.st_ino
            or before.st_size != metadata.st_size
        ):
            return None
        first = os.read(descriptor, LOCAL_COPY_EVIDENCE_SAMPLE_BYTES)
        last = b""
        if before.st_size > LOCAL_COPY_EVIDENCE_SAMPLE_BYTES:
            os.lseek(
                descriptor,
                max(before.st_size - LOCAL_COPY_EVIDENCE_SAMPLE_BYTES, 0),
                os.SEEK_SET,
            )
            last = os.read(descriptor, LOCAL_COPY_EVIDENCE_SAMPLE_BYTES)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            return None
        digest = hashlib.sha256()
        digest.update(b"local-content-sample-v1\0")
        digest.update(str(before.st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(first)
        digest.update(b"\0")
        digest.update(last)
        return f"sample-v1:{digest.hexdigest()}"
    except OSError:
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def local_copy_evidence_hashes(
    row,
    *,
    file_path: object | None = None,
    file_stat: os.stat_result | None = None,
    evidence_memo: LocalCopyEvidenceMemo | None = None,
    include_content_sample: bool = True,
) -> tuple[str, ...]:
    source_kind, source_id = _local_source_scope(row)
    if source_kind != "local":
        return ()
    raw_path = _row_value(row, "file_path") if file_path is None else file_path
    normalized_path = str(raw_path or "").strip()
    if not normalized_path:
        return ()
    path = Path(normalized_path)
    metadata = file_stat
    if metadata is None:
        try:
            metadata = path.stat()
        except OSError:
            return ()
    if not stat.S_ISREG(metadata.st_mode):
        return ()
    evidence: list[str] = []
    if int(metadata.st_ino) > 0:
        evidence.append(
            "inode-v2:"
            + _opaque_digest(
                "local-inode-v2",
                source_kind,
                source_id,
                int(metadata.st_dev),
                int(metadata.st_ino),
                int(metadata.st_size),
                int(metadata.st_mtime_ns),
                int(metadata.st_ctime_ns),
            )
        )
        evidence.append(
            "inode-v1:"
            + _opaque_digest(
                "local-inode-v1",
                source_kind,
                source_id,
                int(metadata.st_dev),
                int(metadata.st_ino),
            )
        )
    sampled = None
    if include_content_sample:
        sampled = (
            evidence_memo.sample_hash(row, path=path, metadata=metadata)
            if evidence_memo is not None
            else _bounded_local_content_hash(path, metadata)
        )
    if sampled:
        evidence.append(
            "sample-v1:"
            + _opaque_digest(
                "local-sample-scope-v1",
                source_kind,
                source_id,
                sampled,
            )
        )
    return tuple(dict.fromkeys(evidence))


def _new_hidden_copy_identity() -> str:
    return (
        f"{HIDDEN_COPY_IDENTITY_PREFIX}"
        f"{secrets.token_hex(LOCAL_COPY_IDENTITY_RANDOM_BYTES)}"
    )


def _active_identity_owner(
    connection: sqlite3.Connection,
    identity: str,
    *,
    excluding_media_item_id: int = 0,
) -> int | None:
    row = connection.execute(
        """
        SELECT id
        FROM media_items
        WHERE hidden_copy_identity = ?
          AND id != ?
        LIMIT 1
        """,
        (identity, excluding_media_item_id),
    ).fetchone()
    return int(row["id"]) if row is not None else None


def record_local_copy_identity_aliases(
    connection: sqlite3.Connection,
    *,
    media_row,
    identity: str,
    file_path: object | None = None,
    file_stat: os.stat_result | None = None,
    evidence_memo: LocalCopyEvidenceMemo | None = None,
    include_content_sample: bool = True,
) -> None:
    source_kind, source_id = _local_source_scope(media_row)
    locator_hash = _local_locator_hash(media_row, file_path=file_path)
    if source_kind != "local" or locator_hash is None:
        return
    evidence_hashes = local_copy_evidence_hashes(
        media_row,
        file_path=file_path,
        file_stat=file_stat,
        evidence_memo=evidence_memo,
        include_content_sample=include_content_sample,
    )
    if not evidence_hashes:
        return
    now = _utcnow_iso()
    for evidence_hash in evidence_hashes:
        connection.execute(
            """
            INSERT INTO hidden_copy_identity_aliases (
                hidden_copy_identity,
                source_kind,
                library_source_id,
                locator_hash,
                evidence_hash,
                created_at,
                last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                hidden_copy_identity,
                source_kind,
                library_source_id,
                locator_hash,
                evidence_hash
            ) DO UPDATE SET last_seen_at = excluded.last_seen_at
            """,
            (
                identity,
                source_kind,
                source_id,
                locator_hash,
                evidence_hash,
                now,
                now,
            ),
        )


def _matching_alias_identities(
    connection: sqlite3.Connection,
    *,
    media_row,
    file_path: object | None = None,
    file_stat: os.stat_result | None = None,
    require_locator: bool,
    evidence_memo: LocalCopyEvidenceMemo | None = None,
    include_content_sample: bool = True,
    require_sample: bool = False,
) -> tuple[str, ...]:
    source_kind, source_id = _local_source_scope(media_row)
    if source_kind != "local":
        return ()
    evidence_hashes = local_copy_evidence_hashes(
        media_row,
        file_path=file_path,
        file_stat=file_stat,
        evidence_memo=evidence_memo,
        include_content_sample=include_content_sample,
    )
    if not evidence_hashes:
        return ()
    locator_hash = _local_locator_hash(media_row, file_path=file_path)
    if require_locator and locator_hash is None:
        return ()
    rows = []
    for evidence_hash in evidence_hashes:
        if require_locator:
            rows.extend(connection.execute(
                """
                SELECT hidden_copy_identity, evidence_hash
                FROM hidden_copy_identity_aliases
                WHERE source_kind = ?
                  AND library_source_id = ?
                  AND evidence_hash = ?
                  AND locator_hash = ?
                """,
                (source_kind, source_id, evidence_hash, locator_hash),
            ).fetchall())
        else:
            rows.extend(connection.execute(
                """
                SELECT hidden_copy_identity, evidence_hash
                FROM hidden_copy_identity_aliases
                WHERE source_kind = ?
                  AND library_source_id = ?
                  AND evidence_hash = ?
                """,
                (source_kind, source_id, evidence_hash),
            ).fetchall())
    matched_by_identity: dict[str, set[str]] = {}
    for row in rows:
        matched_by_identity.setdefault(
            str(row["hidden_copy_identity"]),
            set(),
        ).add(str(row["evidence_hash"]))
    sample_hashes = {
        evidence_hash
        for evidence_hash in evidence_hashes
        if evidence_hash.startswith("sample-v1:")
    }
    if require_sample and not sample_hashes:
        return ()
    inode_v2_hashes = {
        evidence_hash
        for evidence_hash in evidence_hashes
        if evidence_hash.startswith("inode-v2:")
    }
    inode_v1_hashes = {
        evidence_hash
        for evidence_hash in evidence_hashes
        if evidence_hash.startswith("inode-v1:")
    }
    matches = []
    for identity, matched_hashes in matched_by_identity.items():
        if sample_hashes:
            if not matched_hashes.intersection(sample_hashes):
                continue
        elif inode_v2_hashes and not matched_hashes.intersection(inode_v2_hashes):
            continue
        elif inode_v1_hashes and not matched_hashes.intersection(inode_v1_hashes):
            continue
        matches.append(identity)
    return tuple(sorted(set(matches)))


def find_local_copy_identity_candidates(
    connection: sqlite3.Connection,
    *,
    media_row,
    file_path: object | None = None,
    file_stat: os.stat_result | None = None,
    require_locator: bool,
    evidence_memo: LocalCopyEvidenceMemo | None = None,
    include_content_sample: bool = True,
    require_sample: bool = False,
) -> tuple[str, ...]:
    return _matching_alias_identities(
        connection,
        media_row=media_row,
        file_path=file_path,
        file_stat=file_stat,
        require_locator=require_locator,
        evidence_memo=evidence_memo,
        include_content_sample=include_content_sample,
        require_sample=require_sample,
    )


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
    if material.get("authority") == "local-path":
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
    local_file_path: object | None = None,
    local_file_stat: os.stat_result | None = None,
    evidence_memo: LocalCopyEvidenceMemo | None = None,
    include_content_sample: bool = True,
    record_local_aliases: bool = True,
    disallowed_alias_identities: set[str] | frozenset[str] | None = None,
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
        if record_local_aliases:
            record_local_copy_identity_aliases(
                connection,
                media_row=row,
                identity=existing,
                file_path=local_file_path,
                file_stat=local_file_stat,
                evidence_memo=evidence_memo,
                include_content_sample=include_content_sample,
            )
        return existing
    row_id = int(_row_value(row, "id", media_item_id or 0) or 0)
    source_kind, _source_id = _local_source_scope(row)
    if source_kind == "local":
        candidates = [
            identity
            for identity in find_local_copy_identity_candidates(
                connection,
                media_row=row,
                file_path=local_file_path,
                file_stat=local_file_stat,
                require_locator=True,
                evidence_memo=evidence_memo,
                include_content_sample=include_content_sample,
            )
            if _active_identity_owner(
                connection,
                identity,
                excluding_media_item_id=row_id,
            ) is None
            and identity not in (disallowed_alias_identities or ())
        ]
        identity = candidates[0] if len(candidates) == 1 else _new_hidden_copy_identity()
    else:
        identity = _derive_hidden_copy_identity(row)
    if identity and row_id > 0:
        while True:
            try:
                connection.execute(
                    "UPDATE media_items SET hidden_copy_identity = ? WHERE id = ?",
                    (identity, row_id),
                )
                break
            except sqlite3.IntegrityError:
                if source_kind != "local":
                    raise
                identity = _new_hidden_copy_identity()
        if record_local_aliases:
            record_local_copy_identity_aliases(
                connection,
                media_row=row,
                identity=identity,
                file_path=local_file_path,
                file_stat=local_file_stat,
                evidence_memo=evidence_memo,
                include_content_sample=include_content_sample,
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
    has_legacy_keys = connection.execute(
        """
        SELECT (
            EXISTS(
                SELECT 1
                FROM user_hidden_movie_keys
                WHERE movie_key NOT LIKE 'copy-v2:%'
            )
            OR EXISTS(
                SELECT 1
                FROM global_hidden_movie_keys
                WHERE movie_key NOT LIKE 'copy-v2:%'
            )
        ) AS value
        """
    ).fetchone()
    if not has_legacy_keys or not bool(has_legacy_keys["value"]):
        return {
            "user_legacy_keys_migrated": 0,
            "user_legacy_keys_retained": 0,
            "global_legacy_keys_migrated": 0,
            "global_legacy_keys_retained": 0,
        }
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
    has_user_legacy = False
    if user_id is not None:
        has_user_legacy = any(
            connection.execute(
                """
                SELECT 1
                FROM user_hidden_movie_keys
                WHERE user_id = ?
                  AND movie_key = ?
                LIMIT 1
                """,
                (user_id, legacy_key),
            ).fetchone() is not None
            for legacy_key in matching_legacy_keys
        )
    has_global_legacy = False
    if include_global:
        has_global_legacy = any(
            connection.execute(
                """
                SELECT 1
                FROM global_hidden_movie_keys
                WHERE movie_key = ?
                LIMIT 1
                """,
                (legacy_key,),
            ).fetchone() is not None
            for legacy_key in matching_legacy_keys
        )
    if not has_user_legacy and not has_global_legacy:
        return
    rows = _load_identity_rows(connection)
    candidates_by_key = _legacy_candidate_index(rows)
    for legacy_key in matching_legacy_keys:
        if user_id is not None and has_user_legacy:
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
        if include_global and has_global_legacy:
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


def repair_hidden_copy_identity_collisions(
    connection: sqlite3.Connection,
) -> dict[str, int]:
    """Repair historical active identity collisions before uniqueness is enforced."""
    duplicate_rows = connection.execute(
        """
        SELECT hidden_copy_identity
        FROM media_items
        WHERE hidden_copy_identity LIKE 'copy-v2:%'
        GROUP BY hidden_copy_identity
        HAVING COUNT(*) > 1
        ORDER BY hidden_copy_identity
        """
    ).fetchall()
    repaired_rows = 0
    materialized_user_keys = 0
    materialized_global_keys = 0
    connection.execute("SAVEPOINT hidden_copy_identity_collision_repair")
    try:
        for duplicate in duplicate_rows:
            shared_identity = str(duplicate["hidden_copy_identity"])
            rows = connection.execute(
                """
                SELECT
                    id, title, year, original_filename, file_path,
                    COALESCE(source_kind, 'local') AS source_kind,
                    library_source_id, external_media_id, cloud_resource_key,
                    hidden_copy_identity
                FROM media_items
                WHERE hidden_copy_identity = ?
                ORDER BY id
                """,
                (shared_identity,),
            ).fetchall()
            if len(rows) < 2:
                continue
            user_records = connection.execute(
                """
                SELECT user_id, hidden_at
                FROM user_hidden_movie_keys
                WHERE movie_key = ?
                ORDER BY user_id
                """,
                (shared_identity,),
            ).fetchall()
            global_record = connection.execute(
                """
                SELECT hidden_by_user_id, hidden_at
                FROM global_hidden_movie_keys
                WHERE movie_key = ?
                LIMIT 1
                """,
                (shared_identity,),
            ).fetchone()
            for row in rows[1:]:
                new_identity = _new_hidden_copy_identity()
                while _active_identity_owner(connection, new_identity) is not None:
                    new_identity = _new_hidden_copy_identity()
                connection.execute(
                    "UPDATE media_items SET hidden_copy_identity = ? WHERE id = ?",
                    (new_identity, int(row["id"])),
                )
                payload = resolve_hidden_copy_identity_payload(
                    connection,
                    {
                        **dict(row),
                        "hidden_copy_identity": new_identity,
                    },
                )
                if payload is None:
                    raise RuntimeError("hidden_copy_identity_collision_repair_failed")
                for record in user_records:
                    materialized_user_keys += _insert_user_copy_key(
                        connection,
                        user_id=int(record["user_id"]),
                        payload=payload,
                        hidden_at=str(record["hidden_at"]),
                    )
                if global_record is not None:
                    materialized_global_keys += _insert_global_copy_key(
                        connection,
                        hidden_by_user_id=int(global_record["hidden_by_user_id"]),
                        payload=payload,
                        hidden_at=str(global_record["hidden_at"]),
                    )
                record_local_copy_identity_aliases(
                    connection,
                    media_row=row,
                    identity=new_identity,
                )
                repaired_rows += 1
        connection.execute("RELEASE SAVEPOINT hidden_copy_identity_collision_repair")
    except BaseException:
        connection.execute("ROLLBACK TO SAVEPOINT hidden_copy_identity_collision_repair")
        connection.execute("RELEASE SAVEPOINT hidden_copy_identity_collision_repair")
        raise
    return {
        "identity_collisions_repaired": repaired_rows,
        "user_copy_keys_materialized": materialized_user_keys,
        "global_copy_keys_materialized": materialized_global_keys,
    }


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
    copy_identity = resolve_hidden_copy_identity(
        connection,
        media_row=media_row,
        include_content_sample=False,
        record_local_aliases=False,
    )
    if not copy_identity:
        return {"user_hidden_restored": 0, "global_hidden_restored": 0}
    base_title, resolved_year, edition_identity = _legacy_identity_metadata(
        title=_row_value(media_row, "title"),
        year=_row_value(media_row, "year"),
        original_filename=_row_value(media_row, "original_filename"),
    )
    payload = {
        "movie_key": copy_identity,
        "display_title": (
            str(base_title or _row_value(media_row, "title") or "Untitled").strip()
            or "Untitled"
        ),
        "year": resolved_year if resolved_year is not None else 0,
        "edition_identity": edition_identity,
    }

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
    repair_hidden_copy_identity_collisions(connection)
    migrate_legacy_hidden_movie_keys(connection)
