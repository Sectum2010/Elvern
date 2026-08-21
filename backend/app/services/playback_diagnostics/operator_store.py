from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

from .constants import SESSION_VISIBLE_FILES
from .crypto import DiagnosticsKeyStore, decrypt_blob
from .fileio import FILE_MODE, resolve_beneath
from .journal import verify_journal
from .lease import read_lease_status


class DiagnosticsOperatorError(RuntimeError):
    """Raised when a local operator command cannot safely inspect the store."""


class PlaybackDiagnosticsReadOnlyStore:
    """Read a diagnostics store without creating or modifying any store object."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.catalog_path = self.root / "catalog.sqlite3"

    def status(self) -> dict[str, Any]:
        status_path = self.root / "recorder-status.json"
        payload: dict[str, Any] = {
            "available": self.root.is_dir() and not self.root.is_symlink(),
            "root": str(self.root),
            "lease": read_lease_status(self.root),
            "recorder_status": None,
        }
        if status_path.is_file() and not status_path.is_symlink():
            try:
                with status_path.open("rb") as handle:
                    raw = handle.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise DiagnosticsOperatorError("Recorder status file is unexpectedly large")
                decoded = json.loads(raw.decode("utf-8"))
                if isinstance(decoded, dict):
                    payload["recorder_status"] = decoded
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                payload["status_error"] = exc.__class__.__name__
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
        with self._connect() as connection:
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

    def inspect_session(self, playback_session_id: str) -> dict[str, Any]:
        session = self._sealed_session(playback_session_id)
        key_store = DiagnosticsKeyStore(resolve_beneath(self.root, "keys"), read_only=True)
        events, journals = self._read_journals(session, key_store)
        with self._connect() as connection:
            sources = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT source_id, source_type, ack_watermark, max_seen_sequence,
                           final_source_sequence, duplicate_count, out_of_order_count,
                           created_at_utc, updated_at_utc
                    FROM diagnostic_sources
                    WHERE playback_session_id = ?
                    ORDER BY created_at_utc ASC
                    """,
                    (playback_session_id,),
                ).fetchall()
            ]
        return {
            "session": session,
            "sources": sources,
            "journals": journals,
            "event_count": len(events),
            "event_names": sorted({str(event.get("event_name")) for event in events}),
        }

    def verify_session(self, playback_session_id: str) -> dict[str, Any]:
        session = self._sealed_session(playback_session_id)
        session_path = resolve_beneath(self.root, str(session["session_relative_path"]))
        key_store = DiagnosticsKeyStore(resolve_beneath(self.root, "keys"), read_only=True)
        events, journals = self._read_journals(session, key_store, include_host=False)
        errors: list[str] = []
        errors.extend(
            f"journal:{Path(report['path']).name}:{report.get('error') or 'invalid'}"
            for report in journals
            if not report.get("valid")
        )

        raw_by_id: dict[str, tuple[str, int, str, int]] = {}
        raw_sequences: set[tuple[str, int]] = set()
        for event in events:
            event_id = str(event.get("event_id") or "")
            source_id = str(event.get("_journal_source_id") or "")
            sequence = int(event.get("source_sequence") or 0)
            journal_path = str(event.get("_journal_relative_path") or "")
            chunk_sequence = int(event.get("_journal_chunk_sequence") or 0)
            identity = (source_id, sequence)
            if not event_id or event_id in raw_by_id:
                errors.append("duplicate_or_missing_raw_event_id")
            if not source_id or sequence <= 0 or identity in raw_sequences:
                errors.append("duplicate_or_invalid_raw_source_sequence")
            raw_by_id[event_id] = (source_id, sequence, journal_path, chunk_sequence)
            raw_sequences.add(identity)

        with self._connect() as connection:
            indexed_rows = connection.execute(
                """
                SELECT event_id, source_id, source_sequence,
                       journal_relative_path, journal_chunk_sequence
                FROM diagnostic_events
                WHERE playback_session_id = ?
                """,
                (playback_session_id,),
            ).fetchall()
            sources = connection.execute(
                """
                SELECT source_id, ack_watermark, max_seen_sequence, final_source_sequence
                FROM diagnostic_sources WHERE playback_session_id = ?
                """,
                (playback_session_id,),
            ).fetchall()
        indexed = {
            str(row["event_id"]): (
                str(row["source_id"]),
                int(row["source_sequence"]),
                str(row["journal_relative_path"]),
                int(row["journal_chunk_sequence"]),
            )
            for row in indexed_rows
        }
        if indexed != raw_by_id:
            errors.append("catalog_journal_mismatch")
        for source in sources:
            source_id = str(source["source_id"])
            sequences = sorted(sequence for candidate, sequence in raw_sequences if candidate == source_id)
            contiguous = 0
            for sequence in sequences:
                if sequence != contiguous + 1:
                    break
                contiguous = sequence
            if contiguous != int(source["ack_watermark"] or 0):
                errors.append(f"ack_watermark_mismatch:{source_id}")
            final_sequence = source["final_source_sequence"]
            if final_sequence is not None and contiguous < int(final_sequence):
                errors.append(f"final_source_watermark_incomplete:{source_id}")

        manifest_report = self._verify_manifest(session_path)
        errors.extend(manifest_report["errors"])
        permission_errors = self._verify_private_permissions(session_path)
        errors.extend(permission_errors)
        return {
            "playback_session_id": playback_session_id,
            "valid": not errors,
            "state": "sealed",
            "journals": journals,
            "event_count": len(events),
            "manifest": manifest_report,
            "errors": sorted(set(errors)),
        }

    def read_events_for_export(self, playback_session_id: str) -> list[dict[str, Any]]:
        session = self._sealed_session(playback_session_id)
        key_store = DiagnosticsKeyStore(resolve_beneath(self.root, "keys"), read_only=True)
        events, _journals = self._read_journals(session, key_store)
        for event in events:
            for internal_key in tuple(key for key in event if key.startswith("_journal_")):
                event.pop(internal_key, None)
        return events

    def _sealed_session(self, playback_session_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM diagnostic_sessions WHERE playback_session_id = ?",
                (playback_session_id,),
            ).fetchone()
        if row is None:
            raise KeyError("Playback diagnostics session not found")
        session = dict(row)
        if str(session.get("state") or "") != "sealed":
            raise DiagnosticsOperatorError(
                f"Playback diagnostics session is {session.get('state') or 'unknown'}; "
                "only sealed sessions may be inspected, verified, or exported"
            )
        try:
            session["metadata"] = json.loads(session.pop("metadata_json"))
        except (TypeError, ValueError) as exc:
            raise DiagnosticsOperatorError("Session metadata is invalid") from exc
        return session

    def _read_journals(
        self,
        session: dict[str, Any],
        key_store: DiagnosticsKeyStore,
        *,
        include_host: bool = True,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        session_path = resolve_beneath(self.root, str(session["session_relative_path"]))
        raw_path = resolve_beneath(session_path, "raw")
        events: list[dict[str, Any]] = []
        reports: list[dict[str, Any]] = []
        if not raw_path.is_dir() or raw_path.is_symlink():
            return events, reports
        for journal_path in sorted(raw_path.glob("*.elvd")):
            verification, journal_events = verify_journal(
                journal_path,
                key_store,
                include_events=True,
                annotate_events=True,
                expected_playback_session_id=str(session["playback_session_id"]),
            )
            relative_path = str(journal_path.relative_to(self.root))
            reports.append(
                {
                    "path": relative_path,
                    "valid": verification.valid,
                    "chunk_count": verification.chunk_count,
                    "event_count": verification.event_count,
                    "last_chunk_hash": verification.last_chunk_hash,
                    "source_id": verification.source_id,
                    "source_type": verification.source_type,
                    "error": verification.error,
                }
            )
            if verification.valid:
                # Journal verification currently returns events in chunk order. Resolve
                # chunk correspondence from the catalog below rather than trusting input.
                for event in journal_events:
                    copied = dict(event)
                    copied["_journal_source_id"] = verification.source_id
                    copied["_journal_relative_path"] = relative_path
                    copied["_journal_chunk_sequence"] = int(
                        event.get("_journal_chunk_sequence") or 0
                    )
                    events.append(copied)
        events.sort(
            key=lambda event: (
                int(str(event.get("aligned_wall_time_ns") or "0")),
                str(event.get("event_source") or ""),
                int(event.get("source_sequence") or 0),
            )
        )
        if include_host:
            events.extend(
                self._read_linked_host_events(
                    str(session["playback_session_id"]),
                    key_store,
                )
            )
        events.sort(
            key=lambda event: (
                int(str(event.get("aligned_wall_time_ns") or "0")),
                str(event.get("event_source") or ""),
                int(event.get("source_sequence") or 0),
            )
        )
        return events, reports

    def _read_linked_host_events(
        self,
        playback_session_id: str,
        key_store: DiagnosticsKeyStore,
    ) -> list[dict[str, Any]]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT observation.sample_id, observation.event_name,
                           observation.observed_wall_time_ns,
                           observation.observed_monotonic_time_ns,
                           observation.encrypted_payload,
                           link.incident_id, link.incident_phase
                    FROM diagnostic_session_host_links AS link
                    JOIN diagnostic_host_observations AS observation
                      ON observation.sample_id = link.sample_id
                    WHERE link.playback_session_id = ?
                    ORDER BY observation.observed_wall_time_ns, observation.sample_id
                    """,
                    (playback_session_id,),
                ).fetchall()
        except sqlite3.OperationalError:
            return []
        events: list[dict[str, Any]] = []
        for row in rows:
            sample_id = str(row["sample_id"])
            try:
                decoded = decrypt_blob(
                    key_store,
                    bytes(row["encrypted_payload"]),
                    context=f"playback-diagnostics-host:{sample_id}".encode("utf-8"),
                )
                payload = json.loads(decoded.decode("utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            incident_phase = str(row["incident_phase"] or "")
            if incident_phase:
                payload["incident_phase"] = incident_phase
            payload["host_sample_id"] = sample_id
            events.append(
                {
                    "event_id": sample_id,
                    "event_name": str(row["event_name"]),
                    "event_source": "host",
                    "playback_session_id": playback_session_id,
                    "source_sequence": 0,
                    "event_sequence": 0,
                    "aligned_wall_time_ns": str(row["observed_wall_time_ns"]),
                    "server_monotonic_time_ns": str(row["observed_monotonic_time_ns"]),
                    "observation_kind": "measured_kernel",
                    "incident_id": str(row["incident_id"] or "") or None,
                    "payload": payload,
                }
            )
        return events

    def _verify_manifest(self, session_path: Path) -> dict[str, Any]:
        errors: list[str] = []
        manifest_path = resolve_beneath(session_path, "manifest.json")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            return {"valid": False, "errors": ["manifest_missing_or_invalid"]}
        rows = manifest.get("files")
        if not isinstance(rows, list):
            return {"valid": False, "errors": ["manifest_files_invalid"]}
        listed: set[str] = set()
        manifest_mtime = manifest_path.stat().st_mtime_ns
        for row in rows:
            if not isinstance(row, dict):
                errors.append("manifest_file_record_invalid")
                continue
            relative = str(row.get("relative_path") or "")
            if relative not in SESSION_VISIBLE_FILES or relative == "manifest.json":
                errors.append("manifest_file_path_invalid")
                continue
            if relative in listed:
                errors.append("manifest_file_duplicate")
                continue
            listed.add(relative)
            path = resolve_beneath(session_path, relative)
            if not path.is_file() or path.is_symlink():
                errors.append(f"manifest_file_missing:{relative}")
                continue
            digest = hashlib.sha256()
            size_bytes = 0
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size_bytes += len(chunk)
            if size_bytes != int(row.get("size_bytes") or -1):
                errors.append(f"manifest_size_mismatch:{relative}")
            if digest.hexdigest() != str(row.get("sha256") or ""):
                errors.append(f"manifest_hash_mismatch:{relative}")
            if path.stat().st_mtime_ns > manifest_mtime:
                errors.append(f"post_manifest_modification:{relative}")
        required = set(SESSION_VISIBLE_FILES) - {"manifest.json"}
        for missing in sorted(required - listed):
            errors.append(f"required_visible_file_unlisted:{missing}")
        for name in SESSION_VISIBLE_FILES:
            path = resolve_beneath(session_path, name)
            if name != "manifest.json" and path.is_file() and name not in listed:
                errors.append(f"visible_file_unlisted:{name}")
        return {"valid": not errors, "errors": errors, "files_checked": len(listed)}

    def _verify_private_permissions(self, session_path: Path) -> list[str]:
        errors: list[str] = []
        if stat.S_IMODE(session_path.stat().st_mode) != 0o700:
            errors.append("session_directory_permissions_invalid")
        for path in session_path.rglob("*"):
            if path.is_symlink():
                errors.append(f"symlink_rejected:{path.name}")
            elif path.is_dir() and stat.S_IMODE(path.stat().st_mode) != 0o700:
                errors.append(f"directory_permissions_invalid:{path.name}")
            elif path.is_file() and stat.S_IMODE(path.stat().st_mode) != FILE_MODE:
                errors.append(f"file_permissions_invalid:{path.name}")
        return errors

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Query a stable private snapshot without touching live SQLite SHM state."""

        with tempfile.TemporaryDirectory(
            prefix="elvern-playback-diagnostics-catalog-",
        ) as temporary:
            snapshot_path = Path(temporary) / self.catalog_path.name
            self._copy_stable_catalog_snapshot(snapshot_path)
            uri = f"file:{quote(str(snapshot_path.resolve()))}?mode=rw"
            connection = sqlite3.connect(uri, uri=True, timeout=5)
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only = ON")
                yield connection
            finally:
                connection.close()

    def _copy_stable_catalog_snapshot(self, destination: Path) -> None:
        source_wal = Path(f"{self.catalog_path}-wal")
        destination_wal = Path(f"{destination}-wal")
        for attempt in range(8):
            before = self._catalog_source_fingerprint()
            self._copy_regular_file(self.catalog_path, destination)
            if "-wal" in before:
                self._copy_regular_file(source_wal, destination_wal)
            else:
                destination_wal.unlink(missing_ok=True)
            after = self._catalog_source_fingerprint()
            if after == before:
                return
            if attempt < 7:
                time.sleep(0.01)
        raise DiagnosticsOperatorError(
            "Playback diagnostics catalog changed continuously; retry the read-only command"
        )

    def _catalog_source_fingerprint(self) -> dict[str, tuple[int, int, int, int]]:
        fingerprint: dict[str, tuple[int, int, int, int]] = {}
        for suffix in ("", "-wal"):
            path = Path(f"{self.catalog_path}{suffix}")
            try:
                metadata = path.stat(follow_symlinks=False)
            except FileNotFoundError:
                if suffix == "":
                    raise DiagnosticsOperatorError(
                        "Playback diagnostics catalog is unavailable"
                    ) from None
                continue
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise DiagnosticsOperatorError(
                    "Playback diagnostics catalog contains an unsafe file"
                )
            fingerprint[suffix] = (
                int(metadata.st_dev),
                int(metadata.st_ino),
                int(metadata.st_size),
                int(metadata.st_mtime_ns),
            )
        return fingerprint

    @staticmethod
    def _copy_regular_file(source: Path, destination: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(source, flags)
        except OSError as exc:
            raise DiagnosticsOperatorError(
                "Playback diagnostics catalog changed during read-only snapshot"
            ) from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise DiagnosticsOperatorError(
                    "Playback diagnostics catalog contains an unsafe file"
                )
            with os.fdopen(descriptor, "rb", closefd=False) as input_handle:
                with destination.open("wb") as output_handle:
                    shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
        finally:
            os.close(descriptor)
