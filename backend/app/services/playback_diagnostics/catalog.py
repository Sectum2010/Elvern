from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .constants import CATALOG_SCHEMA_VERSION
from .fileio import FILE_MODE, ensure_private_directory, resolve_beneath


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class CatalogEventResult:
    inserted: bool
    duplicate: bool
    out_of_order: bool
    ack_watermark: int


class DiagnosticsCatalog:
    def __init__(self, root: Path) -> None:
        self.root = ensure_private_directory(Path(root))
        self.path = resolve_beneath(self.root, "catalog.sqlite3")
        if self.path.exists() and self.path.is_symlink():
            raise ValueError("Diagnostics catalog must not be a symlink")
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        os.chmod(self.path, FILE_MODE)
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS catalog_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS diagnostic_sessions (
                    playback_session_id TEXT PRIMARY KEY,
                    owner_hash TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    media_item_id INTEGER NOT NULL,
                    source_original_filename TEXT NOT NULL,
                    source_filename_sha256 TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    device_class TEXT NOT NULL,
                    playback_mode TEXT NOT NULL,
                    stream_mode TEXT NOT NULL,
                    hls_engine TEXT NOT NULL,
                    state TEXT NOT NULL,
                    session_relative_path TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    finalized_at_utc TEXT
                );

                CREATE TABLE IF NOT EXISTS diagnostic_sources (
                    source_id TEXT PRIMARY KEY,
                    playback_session_id TEXT NOT NULL REFERENCES diagnostic_sessions(playback_session_id),
                    source_type TEXT NOT NULL,
                    client_instance_id TEXT,
                    ack_watermark INTEGER NOT NULL DEFAULT 0,
                    max_seen_sequence INTEGER NOT NULL DEFAULT 0,
                    duplicate_count INTEGER NOT NULL DEFAULT 0,
                    out_of_order_count INTEGER NOT NULL DEFAULT 0,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS diagnostic_events (
                    event_id TEXT PRIMARY KEY,
                    playback_session_id TEXT NOT NULL REFERENCES diagnostic_sessions(playback_session_id),
                    source_id TEXT NOT NULL REFERENCES diagnostic_sources(source_id),
                    source_sequence INTEGER NOT NULL,
                    event_name TEXT NOT NULL,
                    event_source TEXT NOT NULL,
                    aligned_wall_time_ns TEXT,
                    journal_relative_path TEXT NOT NULL,
                    journal_chunk_sequence INTEGER NOT NULL,
                    persisted_at_utc TEXT NOT NULL,
                    UNIQUE(source_id, source_sequence)
                );

                CREATE INDEX IF NOT EXISTS idx_diagnostic_sessions_created
                    ON diagnostic_sessions(created_at_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_diagnostic_sessions_basename
                    ON diagnostic_sessions(source_original_filename);
                CREATE INDEX IF NOT EXISTS idx_diagnostic_events_session
                    ON diagnostic_events(playback_session_id, aligned_wall_time_ns);
                CREATE INDEX IF NOT EXISTS idx_diagnostic_events_source_sequence
                    ON diagnostic_events(source_id, source_sequence);
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO catalog_meta(key, value) VALUES ('schema_version', ?)",
                (CATALOG_SCHEMA_VERSION,),
            )
            connection.commit()
        self._chmod_sidecars()

    def _chmod_sidecars(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.path}{suffix}")
            if candidate.exists() and not candidate.is_symlink():
                os.chmod(candidate, FILE_MODE)

    def upsert_session(self, metadata: dict[str, Any]) -> None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO diagnostic_sessions (
                    playback_session_id, owner_hash, subject_id, media_item_id,
                    source_original_filename, source_filename_sha256, source_fingerprint,
                    source_kind, platform, device_class, playback_mode, stream_mode,
                    hls_engine, state, session_relative_path, metadata_json,
                    created_at_utc, updated_at_utc, finalized_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(playback_session_id) DO UPDATE SET
                    platform = excluded.platform,
                    device_class = excluded.device_class,
                    hls_engine = excluded.hls_engine,
                    metadata_json = excluded.metadata_json,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    metadata["playback_session_id"],
                    metadata["owner_hash"],
                    metadata["subject_id"],
                    int(metadata["media_item_id"]),
                    metadata["source_original_filename"],
                    metadata["source_filename_sha256"],
                    metadata["source_fingerprint"],
                    metadata.get("source_kind", "unknown"),
                    metadata.get("platform", "unknown"),
                    metadata.get("device_class", "unknown"),
                    metadata.get("playback_mode", "unknown"),
                    metadata.get("stream_mode", "unknown"),
                    metadata.get("hls_engine", "unknown"),
                    metadata.get("state", "active"),
                    metadata["session_relative_path"],
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    metadata.get("created_at_utc", now),
                    now,
                ),
            )
            connection.commit()
        self._chmod_sidecars()

    def register_source(
        self,
        *,
        playback_session_id: str,
        source_id: str,
        source_type: str,
        client_instance_id: str | None = None,
    ) -> None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO diagnostic_sources (
                    source_id, playback_session_id, source_type, client_instance_id,
                    created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET updated_at_utc = excluded.updated_at_utc
                """,
                (
                    source_id,
                    playback_session_id,
                    source_type,
                    client_instance_id,
                    now,
                    now,
                ),
            )
            connection.commit()

    def session_owned_by(self, playback_session_id: str, owner_hash: str) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT owner_hash FROM diagnostic_sessions WHERE playback_session_id = ?",
                (playback_session_id,),
            ).fetchone()
        return row is not None and str(row["owner_hash"]) == owner_hash

    def source_owned_by(self, source_id: str, playback_session_id: str) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM diagnostic_sources
                WHERE source_id = ? AND playback_session_id = ?
                """,
                (source_id, playback_session_id),
            ).fetchone()
        return row is not None

    def find_client_source(
        self,
        playback_session_id: str,
        client_instance_id: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT source_id, ack_watermark, max_seen_sequence
                FROM diagnostic_sources
                WHERE playback_session_id = ?
                  AND source_type = 'client'
                  AND client_instance_id = ?
                ORDER BY created_at_utc ASC
                LIMIT 1
                """,
                (playback_session_id, client_instance_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def event_exists(self, event_id: str, source_id: str, source_sequence: int) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM diagnostic_events
                WHERE event_id = ? OR (source_id = ? AND source_sequence = ?)
                LIMIT 1
                """,
                (event_id, source_id, int(source_sequence)),
            ).fetchone()
        return row is not None

    def session_has_event_name(self, playback_session_id: str, event_name: str) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM diagnostic_events
                WHERE playback_session_id = ? AND event_name = ?
                LIMIT 1
                """,
                (playback_session_id, event_name),
            ).fetchone()
        return row is not None

    def record_events(
        self,
        *,
        playback_session_id: str,
        source_id: str,
        journal_relative_path: str,
        journal_chunk_sequence: int,
        events: Iterable[dict[str, Any]],
    ) -> tuple[int, int, int]:
        inserted = 0
        duplicate = 0
        out_of_order = 0
        now = _utc_now()
        with self._lock, self._connect() as connection:
            source = connection.execute(
                "SELECT ack_watermark, max_seen_sequence FROM diagnostic_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if source is None:
                raise KeyError("Diagnostics source is not registered")
            watermark_before = int(source["ack_watermark"])
            max_seen = int(source["max_seen_sequence"])
            for event in events:
                sequence = int(event["source_sequence"])
                try:
                    connection.execute(
                        """
                        INSERT INTO diagnostic_events (
                            event_id, playback_session_id, source_id, source_sequence,
                            event_name, event_source, aligned_wall_time_ns,
                            journal_relative_path, journal_chunk_sequence, persisted_at_utc
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event["event_id"],
                            playback_session_id,
                            source_id,
                            sequence,
                            event["event_name"],
                            event["event_source"],
                            event.get("aligned_wall_time_ns"),
                            journal_relative_path,
                            int(journal_chunk_sequence),
                            now,
                        ),
                    )
                except sqlite3.IntegrityError:
                    duplicate += 1
                    continue
                inserted += 1
                if sequence > watermark_before + 1:
                    out_of_order += 1
                max_seen = max(max_seen, sequence)

            watermark = watermark_before
            while True:
                next_row = connection.execute(
                    "SELECT 1 FROM diagnostic_events WHERE source_id = ? AND source_sequence = ?",
                    (source_id, watermark + 1),
                ).fetchone()
                if next_row is None:
                    break
                watermark += 1
            connection.execute(
                """
                UPDATE diagnostic_sources
                SET ack_watermark = ?, max_seen_sequence = ?,
                    duplicate_count = duplicate_count + ?,
                    out_of_order_count = out_of_order_count + ?,
                    updated_at_utc = ?
                WHERE source_id = ?
                """,
                (watermark, max_seen, duplicate, out_of_order, now, source_id),
            )
            connection.execute(
                "UPDATE diagnostic_sessions SET updated_at_utc = ? WHERE playback_session_id = ?",
                (now, playback_session_id),
            )
            connection.commit()
        self._chmod_sidecars()
        return inserted, duplicate, out_of_order

    def ack_watermark(self, source_id: str) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT ack_watermark FROM diagnostic_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return int(row["ack_watermark"]) if row else 0

    def mark_finalized(self, playback_session_id: str, *, state: str = "closed") -> None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE diagnostic_sessions
                SET state = ?, updated_at_utc = ?, finalized_at_utc = ?
                WHERE playback_session_id = ?
                """,
                (state, now, now, playback_session_id),
            )
            connection.commit()

    def get_session(self, playback_session_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM diagnostic_sessions WHERE playback_session_id = ?",
                (playback_session_id,),
            ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        try:
            payload["metadata"] = json.loads(payload.pop("metadata_json"))
        except (TypeError, ValueError):
            payload["metadata"] = {}
        return payload

    def list_sessions(
        self,
        *,
        date: str | None = None,
        basename: str | None = None,
        source: str | None = None,
        platform: str | None = None,
        mode: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        normalized = [
            str(value).strip() if value is not None and str(value).strip() else None
            for value in (date, basename, source, platform, mode)
        ]
        values: list[Any] = []
        for value in normalized:
            values.extend((value, value))
        values.append(max(1, min(int(limit), 5_000)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT playback_session_id, subject_id, media_item_id,
                       source_original_filename, source_kind, platform, device_class,
                       playback_mode, stream_mode, hls_engine, state,
                       session_relative_path, created_at_utc, updated_at_utc,
                       finalized_at_utc
                FROM diagnostic_sessions
                WHERE (? IS NULL OR date(created_at_utc) = ?)
                  AND (? IS NULL OR source_original_filename = ?)
                  AND (? IS NULL OR source_kind = ?)
                  AND (? IS NULL OR platform = ?)
                  AND (? IS NULL OR playback_mode = ?)
                ORDER BY created_at_utc DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_sessions_for_reconcile(self) -> list[dict[str, Any]]:
        """Return every catalog path without applying the interactive CLI limit."""

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT playback_session_id, state, session_relative_path
                FROM diagnostic_sessions
                ORDER BY created_at_utc ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def count_sessions(self) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS session_count FROM diagnostic_sessions"
            ).fetchone()
        return int(row["session_count"]) if row else 0

    def source_stats(self, playback_session_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT source_id, source_type, ack_watermark, max_seen_sequence,
                       duplicate_count, out_of_order_count, created_at_utc, updated_at_utc
                FROM diagnostic_sources
                WHERE playback_session_id = ?
                ORDER BY created_at_utc ASC
                """,
                (playback_session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def remove_missing_session(self, playback_session_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM diagnostic_events WHERE playback_session_id = ?",
                (playback_session_id,),
            )
            connection.execute(
                "DELETE FROM diagnostic_sources WHERE playback_session_id = ?",
                (playback_session_id,),
            )
            connection.execute(
                "DELETE FROM diagnostic_sessions WHERE playback_session_id = ?",
                (playback_session_id,),
            )
            connection.commit()

    def reconcile(self) -> dict[str, int]:
        removed = 0
        sessions = self.list_sessions_for_reconcile()
        for session in sessions:
            relative_path = Path(str(session["session_relative_path"]))
            session_path = resolve_beneath(self.root, relative_path)
            if not session_path.is_dir() or not (session_path / "session.json").is_file():
                self.remove_missing_session(str(session["playback_session_id"]))
                removed += 1
        return {"catalog_sessions_checked": len(sessions), "catalog_sessions_removed": removed}
