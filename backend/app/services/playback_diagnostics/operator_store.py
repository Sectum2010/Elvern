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
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

from .constants import SESSION_VISIBLE_FILES
from .crypto import DiagnosticsKeyStore, decrypt_blob
from .fileio import (
    FILE_MODE,
    list_private_directory,
    open_private_descriptor,
    private_directory_stat,
    private_file_size,
    private_file_stat,
    read_private_bytes,
    resolve_beneath,
    walk_private_tree,
)
from .journal import verify_journal
from .lease import read_lease_status
from .privacy import SAFE_IDENTIFIER_PATTERN, validate_canonical_payload
from .sealing import (
    SEAL_CAPSULE_FIELDS,
    SEAL_GAP_FIELDS,
    SEAL_HOST_FIELDS,
    SEAL_JOURNAL_FIELDS,
    SEAL_SOURCE_FIELDS,
    canonicalize_host_evidence,
    canonicalize_journal_reports,
    canonicalize_source_stats,
    seal_evidence_digest,
    seal_evidence_payload,
)


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
        try:
            raw = read_private_bytes(
                status_path,
                max_bytes=1_000_000,
                trusted_root=self.root,
            )
            try:
                decoded = json.loads(raw.decode("utf-8"))
                if isinstance(decoded, dict):
                    payload["recorder_status"] = decoded
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                payload["status_error"] = exc.__class__.__name__
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
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
        key_store = DiagnosticsKeyStore(
            resolve_beneath(self.root, "keys"),
            read_only=True,
            trusted_root=self.root,
        )
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
        key_store = DiagnosticsKeyStore(
            resolve_beneath(self.root, "keys"),
            read_only=True,
            trusted_root=self.root,
        )
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
                SELECT source_id, source_type, ack_watermark, max_seen_sequence,
                       final_source_sequence, duplicate_count, out_of_order_count,
                       created_at_utc, updated_at_utc
                FROM diagnostic_sources WHERE playback_session_id = ?
                """,
                (playback_session_id,),
            ).fetchall()
            gap_rows = connection.execute(
                """
                SELECT source_id, start_sequence, end_sequence, reason_code,
                       declaration_origin, declared_at_utc,
                       rejected_event_name, rejected_event_hash
                FROM diagnostic_source_gaps
                WHERE source_id IN (
                    SELECT source_id FROM diagnostic_sources
                    WHERE playback_session_id = ?
                )
                ORDER BY source_id, start_sequence, end_sequence
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
        source_stats: list[dict[str, Any]] = []
        for source in sources:
            source_id = str(source["source_id"])
            sequences = sorted(
                sequence for candidate, sequence in raw_sequences if candidate == source_id
            )
            source_gaps = [
                dict(row) for row in gap_rows if str(row["source_id"]) == source_id
            ]
            intervals = [(sequence, sequence) for sequence in sequences]
            intervals.extend(
                (int(row["start_sequence"]), int(row["end_sequence"]))
                for row in source_gaps
            )
            contiguous = 0
            for start, end in sorted(intervals):
                if end <= contiguous:
                    continue
                if start > contiguous + 1:
                    break
                contiguous = max(contiguous, end)
            if contiguous != int(source["ack_watermark"] or 0):
                errors.append(f"ack_watermark_mismatch:{source_id}")
            final_sequence = source["final_source_sequence"]
            if final_sequence is not None and contiguous < int(final_sequence):
                errors.append(f"final_source_watermark_incomplete:{source_id}")
            upper = int(final_sequence or source["max_seen_sequence"] or 0)
            missing_ranges: list[list[int]] = []
            expected = 1
            for start, end in sorted(intervals):
                bounded_end = min(upper, end)
                if bounded_end < expected:
                    continue
                if start > expected:
                    missing_ranges.append([expected, start - 1])
                expected = max(expected, bounded_end + 1)
            if expected <= upper:
                missing_ranges.append([expected, upper])
            source_stats.append(
                {
                    **dict(source),
                    "declared_gaps": source_gaps,
                    "missing_ranges": missing_ranges,
                }
            )

        host_report = self._verify_host_evidence(playback_session_id, key_store)
        errors.extend(host_report["errors"])
        seal_report = self._verify_seal_capsule(
            session_path,
            playback_session_id=playback_session_id,
            sources=source_stats,
            journals=journals,
            host_report=host_report,
        )
        errors.extend(seal_report["errors"])
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
            "seal": seal_report,
            "host_evidence": host_report,
            "declared_gap_count": len(gap_rows),
            "errors": sorted(set(errors)),
        }

    def read_events_for_export(self, playback_session_id: str) -> list[dict[str, Any]]:
        session = self._sealed_session(playback_session_id)
        key_store = DiagnosticsKeyStore(
            resolve_beneath(self.root, "keys"),
            read_only=True,
            trusted_root=self.root,
        )
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
        try:
            journal_names = list_private_directory(raw_path, trusted_root=self.root)
        except FileNotFoundError:
            return events, reports
        for name in journal_names:
            if not name.endswith(".elvd") or Path(name).name != name:
                continue
            journal_path = resolve_beneath(raw_path, name)
            verification, journal_events = verify_journal(
                journal_path,
                key_store,
                include_events=True,
                annotate_events=True,
                expected_playback_session_id=str(session["playback_session_id"]),
                trusted_root=self.root,
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
                payload = validate_canonical_payload(payload)
            except (OSError, TypeError, UnicodeDecodeError, ValueError) as exc:
                raise DiagnosticsOperatorError(
                    f"Linked host evidence is corrupt: {sample_id}"
                ) from exc
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

    @staticmethod
    def _host_link_digest(rows: list[sqlite3.Row]) -> str:
        canonical = [
            {
                "sample_id": str(row["sample_id"]),
                "incident_id": str(row["incident_id"] or ""),
                "incident_phase": str(row["incident_phase"] or ""),
                "observed_wall_time_ns": str(row["observed_wall_time_ns"]),
            }
            for row in rows
        ]
        return hashlib.sha256(
            json.dumps(
                canonical,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def _verify_host_evidence(
        self,
        playback_session_id: str,
        key_store: DiagnosticsKeyStore,
    ) -> dict[str, Any]:
        errors: list[str] = []
        try:
            with self._connect() as connection:
                cutoff = connection.execute(
                    """
                    SELECT * FROM diagnostic_host_link_cutoffs
                    WHERE playback_session_id = ?
                    """,
                    (playback_session_id,),
                ).fetchone()
                rows = connection.execute(
                    """
                    SELECT link.sample_id AS linked_sample_id,
                           observation.sample_id, observation.event_name,
                           observation.observed_wall_time_ns,
                           observation.observed_monotonic_time_ns,
                           observation.encrypted_payload,
                           link.incident_id, link.incident_phase, link.linked_at_utc,
                           session.playback_session_id AS linked_session_id
                    FROM diagnostic_session_host_links AS link
                    LEFT JOIN diagnostic_host_observations AS observation
                      ON observation.sample_id = link.sample_id
                    LEFT JOIN diagnostic_sessions AS session
                      ON session.playback_session_id = link.playback_session_id
                    WHERE link.playback_session_id = ?
                    ORDER BY observation.observed_wall_time_ns, link.sample_id,
                             link.incident_id, link.incident_phase
                    """,
                    (playback_session_id,),
                ).fetchall()
        except sqlite3.OperationalError:
            return {
                "valid": False,
                "link_count": 0,
                "link_digest_sha256": None,
                "errors": ["host_link_cutoff_missing"],
            }
        if cutoff is None:
            errors.append("host_link_cutoff_missing")
        seen: set[tuple[str, str, str]] = set()
        seen_incident_phases: dict[tuple[str, str], str] = {}
        for row in rows:
            linked_sample_id = str(row["linked_sample_id"] or "")
            sample_id = str(row["sample_id"] or "")
            event_name = str(row["event_name"] or "")
            incident_id = str(row["incident_id"] or "")
            phase = str(row["incident_phase"] or "")
            identity = (linked_sample_id, incident_id, phase)
            if identity in seen:
                errors.append("host_link_duplicate")
            seen.add(identity)
            incident_identity = (linked_sample_id, incident_id)
            previous_phase = seen_incident_phases.setdefault(incident_identity, phase)
            if previous_phase != phase:
                errors.append(f"host_link_conflict:{linked_sample_id or 'missing'}")
            if row["linked_session_id"] is None:
                errors.append("host_link_session_missing")
            if not sample_id or sample_id != linked_sample_id:
                errors.append(f"host_link_sample_missing:{linked_sample_id or 'missing'}")
                continue
            if (
                not sample_id.startswith("host_")
                or not sample_id.removeprefix("host_").isalnum()
                or not event_name.replace("_", "").isalnum()
                or not str(row["observed_wall_time_ns"] or "").isdigit()
                or not str(row["observed_monotonic_time_ns"] or "").isdigit()
                or (incident_id and not SAFE_IDENTIFIER_PATTERN.fullmatch(incident_id))
                or phase not in {"", "pre", "post", "trigger"}
            ):
                errors.append(f"host_link_schema_invalid:{sample_id or 'missing'}")
                continue
            try:
                datetime.fromisoformat(str(row["linked_at_utc"] or "").replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"host_link_timestamp_invalid:{sample_id}")
            if cutoff is not None and str(row["linked_at_utc"] or "") > str(
                cutoff["cutoff_at_utc"] or ""
            ):
                errors.append(f"host_link_beyond_cutoff:{sample_id}")
            try:
                decoded = decrypt_blob(
                    key_store,
                    bytes(row["encrypted_payload"]),
                    context=f"playback-diagnostics-host:{sample_id}".encode("utf-8"),
                )
                payload = json.loads(decoded.decode("utf-8"))
                validate_canonical_payload(payload)
            except (KeyError, OSError, TypeError, UnicodeDecodeError, ValueError):
                errors.append(f"host_payload_invalid:{sample_id}")
        digest = self._host_link_digest(rows)
        first_observed = str(rows[0]["observed_wall_time_ns"]) if rows else None
        last_observed = str(rows[-1]["observed_wall_time_ns"]) if rows else None
        if cutoff is not None:
            if int(cutoff["link_count"] or 0) != len(rows):
                errors.append("host_link_count_mismatch")
            if str(cutoff["link_digest_sha256"] or "") != digest:
                errors.append("host_link_digest_mismatch")
            if cutoff["first_observed_wall_time_ns"] != first_observed:
                errors.append("host_link_first_observation_mismatch")
            if cutoff["last_observed_wall_time_ns"] != last_observed:
                errors.append("host_link_last_observation_mismatch")
        return {
            "valid": not errors,
            "link_count": len(rows),
            "link_digest_sha256": digest,
            "cutoff_at_utc": str(cutoff["cutoff_at_utc"]) if cutoff is not None else None,
            "first_observed_wall_time_ns": first_observed,
            "last_observed_wall_time_ns": last_observed,
            "errors": errors,
        }

    def _verify_seal_capsule(
        self,
        session_path: Path,
        *,
        playback_session_id: str,
        sources: list[dict[str, Any]],
        journals: list[dict[str, Any]],
        host_report: dict[str, Any],
    ) -> dict[str, Any]:
        errors: list[str] = []
        seal_path = resolve_beneath(session_path, "seal.json")
        try:
            seal = json.loads(
                read_private_bytes(
                    seal_path,
                    max_bytes=4 * 1024 * 1024,
                    trusted_root=self.root,
                ).decode("utf-8")
            )
        except (OSError, UnicodeDecodeError, ValueError):
            return {"valid": False, "errors": ["seal_capsule_missing_or_invalid"]}
        if seal.get("schema_version") != "playback-diagnostics-seal-v1":
            errors.append("seal_capsule_schema_invalid")
        if set(seal) != SEAL_CAPSULE_FIELDS:
            errors.append("seal_capsule_fields_invalid")
        if seal.get("sealed") is not True:
            errors.append("seal_capsule_not_sealed")
        if seal.get("playback_session_id") != playback_session_id:
            errors.append("seal_session_id_mismatch")
        if seal_evidence_digest(seal_evidence_payload(seal)) != str(
            seal.get("evidence_digest_sha256") or ""
        ):
            errors.append("seal_evidence_digest_mismatch")
        derived_flags = [
            bool(seal.get("derived_artifacts_complete")),
            bool(seal.get("derived_artifacts_deferred_capacity")),
            bool(seal.get("derived_artifacts_failed")),
        ]
        if sum(derived_flags) != 1:
            errors.append("seal_derived_status_invalid")
        derived_status = seal.get("derived_artifact_status")
        expected_derived_flags = {
            "derived_artifacts_complete": [True, False, False],
            "derived_artifacts_deferred_capacity": [False, True, False],
            "derived_artifacts_failed": [False, False, True],
        }
        if expected_derived_flags.get(derived_status) != derived_flags:
            errors.append("seal_derived_status_mismatch")
        host = seal.get("host_evidence") if isinstance(seal.get("host_evidence"), dict) else {}
        if set(host) != SEAL_HOST_FIELDS:
            errors.append("seal_host_fields_invalid")
        if canonicalize_host_evidence(host) != canonicalize_host_evidence(host_report):
            errors.append("seal_host_evidence_mismatch")
        sealed_sources = seal.get("sources") if isinstance(seal.get("sources"), list) else []
        if any(
            not isinstance(source, dict)
            or set(source) != SEAL_SOURCE_FIELDS
            or any(
                not isinstance(gap, dict) or set(gap) != SEAL_GAP_FIELDS
                for gap in source.get("declared_gaps", [])
            )
            for source in sealed_sources
        ):
            errors.append("seal_source_schema_invalid")
        else:
            try:
                if canonicalize_source_stats(sealed_sources) != canonicalize_source_stats(
                    sources
                ):
                    errors.append("seal_source_evidence_mismatch")
            except (TypeError, ValueError):
                errors.append("seal_source_schema_invalid")
        sealed_journals = seal.get("journals") if isinstance(seal.get("journals"), list) else []
        if any(
            not isinstance(journal, dict) or set(journal) != SEAL_JOURNAL_FIELDS
            for journal in sealed_journals
        ):
            errors.append("seal_journal_schema_invalid")
        else:
            try:
                if canonicalize_journal_reports(journals) != canonicalize_journal_reports(
                    sealed_journals
                ):
                    errors.append("post_seal_journal_mutation")
            except (TypeError, ValueError):
                errors.append("seal_journal_schema_invalid")
        return {
            "valid": not errors,
            "derived_artifact_status": seal.get("derived_artifact_status"),
            "errors": errors,
        }

    def _verify_manifest(self, session_path: Path) -> dict[str, Any]:
        errors: list[str] = []
        manifest_path = resolve_beneath(session_path, "manifest.json")
        try:
            manifest = json.loads(
                read_private_bytes(
                    manifest_path,
                    max_bytes=4 * 1024 * 1024,
                    trusted_root=self.root,
                ).decode("utf-8")
            )
        except (OSError, UnicodeDecodeError, ValueError):
            return {"valid": False, "errors": ["manifest_missing_or_invalid"]}
        rows = manifest.get("files")
        if not isinstance(rows, list):
            return {"valid": False, "errors": ["manifest_files_invalid"]}
        listed: set[str] = set()
        manifest_metadata = private_file_stat(
            manifest_path,
            trusted_root=self.root,
        )
        assert manifest_metadata is not None
        manifest_mtime = manifest_metadata.st_mtime_ns
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
            try:
                metadata = private_file_stat(path, trusted_root=self.root)
            except FileNotFoundError:
                errors.append(f"manifest_file_missing:{relative}")
                continue
            digest = hashlib.sha256()
            size_bytes = 0
            descriptor = open_private_descriptor(
                path,
                os.O_RDONLY,
                trusted_root=self.root,
            )
            with os.fdopen(descriptor, "rb") as handle:
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
            if metadata is not None and metadata.st_mtime_ns > manifest_mtime:
                errors.append(f"post_manifest_modification:{relative}")
        required = {"session.json", "seal.json"}
        seal_path = resolve_beneath(session_path, "seal.json")
        try:
            seal = json.loads(
                read_private_bytes(
                    seal_path,
                    max_bytes=4 * 1024 * 1024,
                    trusted_root=self.root,
                ).decode("utf-8")
            )
        except (OSError, UnicodeDecodeError, ValueError):
            seal = {}
        if seal.get("derived_artifacts_complete") is True:
            required.update(
                {"summary.md", "summary.json", "timeline.csv", "completeness.json"}
            )
        for missing in sorted(required - listed):
            errors.append(f"required_visible_file_unlisted:{missing}")
        for name in SESSION_VISIBLE_FILES:
            path = resolve_beneath(session_path, name)
            if name != "manifest.json" and name not in listed:
                try:
                    private_file_size(path, trusted_root=self.root)
                except FileNotFoundError:
                    continue
                errors.append(f"visible_file_unlisted:{name}")
        return {"valid": not errors, "errors": errors, "files_checked": len(listed)}

    def _verify_private_permissions(self, session_path: Path) -> list[str]:
        errors: list[str] = []
        session_metadata = private_directory_stat(
            session_path,
            trusted_root=self.root,
        )
        if stat.S_IMODE(session_metadata.st_mode) != 0o700:
            errors.append("session_directory_permissions_invalid")
        for relative, metadata in walk_private_tree(
            session_path,
            trusted_root=self.root,
        ):
            if stat.S_ISLNK(metadata.st_mode):
                errors.append(f"symlink_rejected:{relative}")
            elif stat.S_ISDIR(metadata.st_mode):
                if stat.S_IMODE(metadata.st_mode) != 0o700:
                    errors.append(f"directory_permissions_invalid:{relative}")
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1:
                    errors.append(f"hardlink_rejected:{relative}")
                if stat.S_IMODE(metadata.st_mode) != FILE_MODE:
                    errors.append(f"file_permissions_invalid:{relative}")
            else:
                errors.append(f"non_regular_file_rejected:{relative}")
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
                metadata = private_file_stat(path, trusted_root=self.root)
            except FileNotFoundError:
                if suffix == "":
                    raise DiagnosticsOperatorError(
                        "Playback diagnostics catalog is unavailable"
                    ) from None
                continue
            if metadata is None or not stat.S_ISREG(metadata.st_mode):
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

    def _copy_regular_file(self, source: Path, destination: Path) -> None:
        try:
            descriptor = open_private_descriptor(
                source,
                os.O_RDONLY,
                trusted_root=self.root,
            )
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
