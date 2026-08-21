from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .capacity import DiagnosticsCapacityError, DiagnosticsCapacityGuard
from .fileio import (
    atomic_write_bytes,
    encode_json_document,
    fsync_directory,
    private_file_size,
    resolve_beneath,
)
from .session_files import build_manifest
from .summaries import build_summary_artifacts


DERIVED_ARTIFACT_RESERVATION_BYTES = 64 * 1024 * 1024
SEAL_CAPSULE_SCHEMA_VERSION = "playback-diagnostics-seal-v1"
SEAL_EVIDENCE_FIELDS = (
    "playback_session_id",
    "close_reason",
    "sources",
    "journals",
    "host_evidence",
    "derived_artifact_status",
)
SEAL_CAPSULE_FIELDS = frozenset(
    {
        "schema_version",
        "sealed_at_utc",
        "sealed",
        "derived_artifacts_complete",
        "derived_artifacts_deferred_capacity",
        "derived_artifacts_failed",
        "evidence_digest_sha256",
        *SEAL_EVIDENCE_FIELDS,
    }
)
SEAL_SOURCE_FIELDS = frozenset(
    {
        "source_id",
        "source_type",
        "ack_watermark",
        "max_seen_sequence",
        "final_source_sequence",
        "duplicate_count",
        "out_of_order_count",
        "created_at_utc",
        "updated_at_utc",
        "declared_gaps",
        "missing_ranges",
    }
)
SEAL_GAP_FIELDS = frozenset(
    {
        "source_id",
        "start_sequence",
        "end_sequence",
        "reason_code",
        "declaration_origin",
        "declared_at_utc",
        "rejected_event_name",
        "rejected_event_hash",
    }
)
SEAL_JOURNAL_FIELDS = frozenset(
    {
        "path",
        "valid",
        "chunk_count",
        "event_count",
        "last_chunk_hash",
        "error",
    }
)
SEAL_HOST_FIELDS = frozenset(
    {
        "cutoff_at_utc",
        "link_count",
        "first_observed_wall_time_ns",
        "last_observed_wall_time_ns",
        "link_digest_sha256",
    }
)
DERIVED_ARTIFACT_NAMES = (
    "summary.md",
    "summary.json",
    "timeline.csv",
    "completeness.json",
)
CRITICAL_SEAL_NAMES = ("session.json", "seal.json", "manifest.json")


def canonicalize_declared_gaps(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps = [
        {
            "source_id": str(row.get("source_id") or ""),
            "start_sequence": int(row.get("start_sequence") or 0),
            "end_sequence": int(row.get("end_sequence") or 0),
            "reason_code": str(row.get("reason_code") or ""),
            "declaration_origin": str(row.get("declaration_origin") or ""),
            "declared_at_utc": str(row.get("declared_at_utc") or ""),
            "rejected_event_name": str(row.get("rejected_event_name") or "") or None,
            "rejected_event_hash": str(row.get("rejected_event_hash") or "") or None,
        }
        for row in rows
    ]
    return sorted(
        gaps,
        key=lambda row: (
            row["source_id"],
            row["start_sequence"],
            row["end_sequence"],
        ),
    )


def canonicalize_source_stats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources = [
        {
            "source_id": str(row.get("source_id") or ""),
            "source_type": str(row.get("source_type") or ""),
            "ack_watermark": int(row.get("ack_watermark") or 0),
            "max_seen_sequence": int(row.get("max_seen_sequence") or 0),
            "final_source_sequence": (
                int(row["final_source_sequence"])
                if row.get("final_source_sequence") is not None
                else None
            ),
            "duplicate_count": int(row.get("duplicate_count") or 0),
            "out_of_order_count": int(row.get("out_of_order_count") or 0),
            "created_at_utc": str(row.get("created_at_utc") or ""),
            "updated_at_utc": str(row.get("updated_at_utc") or ""),
            "declared_gaps": canonicalize_declared_gaps(
                list(row.get("declared_gaps") or [])
            ),
            "missing_ranges": [
                [int(interval[0]), int(interval[1])]
                for interval in list(row.get("missing_ranges") or [])
            ],
        }
        for row in rows
    ]
    return sorted(sources, key=lambda row: row["source_id"])


def canonicalize_journal_reports(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    journals = [
        {
            "path": str(row.get("path") or ""),
            "valid": bool(row.get("valid")),
            "chunk_count": int(row.get("chunk_count") or 0),
            "event_count": int(row.get("event_count") or 0),
            "last_chunk_hash": str(row.get("last_chunk_hash") or ""),
            "error": str(row.get("error") or "") or None,
        }
        for row in rows
    ]
    return sorted(journals, key=lambda row: row["path"])


def canonicalize_host_evidence(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "cutoff_at_utc": row.get("cutoff_at_utc"),
        "link_count": int(row.get("link_count") or 0),
        "first_observed_wall_time_ns": row.get("first_observed_wall_time_ns"),
        "last_observed_wall_time_ns": row.get("last_observed_wall_time_ns"),
        "link_digest_sha256": str(row.get("link_digest_sha256") or ""),
    }


def seal_evidence_payload(capsule: dict[str, Any]) -> dict[str, Any]:
    return {field: capsule.get(field) for field in SEAL_EVIDENCE_FIELDS}


def seal_evidence_digest(evidence: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def build_seal_capsule(
    *,
    playback_session_id: str,
    close_reason: str,
    source_stats: list[dict[str, Any]],
    journal_reports: list[dict[str, Any]],
    host_link_cutoff: dict[str, Any],
    derived_artifact_status: str,
) -> dict[str, Any]:
    """Build the small immutable evidence needed to prove terminal state."""

    journals = canonicalize_journal_reports(journal_reports)
    sources = canonicalize_source_stats(source_stats)
    evidence = {
        "playback_session_id": playback_session_id,
        "close_reason": str(close_reason)[:128],
        "sources": sources,
        "journals": journals,
        "host_evidence": canonicalize_host_evidence(host_link_cutoff),
        "derived_artifact_status": derived_artifact_status,
    }
    evidence_digest = seal_evidence_digest(evidence)
    return {
        "schema_version": SEAL_CAPSULE_SCHEMA_VERSION,
        "sealed_at_utc": datetime.now(timezone.utc).isoformat(),
        "sealed": True,
        "derived_artifacts_complete": derived_artifact_status
        == "derived_artifacts_complete",
        "derived_artifacts_deferred_capacity": derived_artifact_status
        == "derived_artifacts_deferred_capacity",
        "derived_artifacts_failed": derived_artifact_status
        == "derived_artifacts_failed",
        "evidence_digest_sha256": evidence_digest,
        **evidence,
    }


def write_derived_artifacts(
    *,
    session_path: Path,
    metadata: dict[str, Any],
    events: list[dict[str, Any]],
    source_stats: list[dict[str, Any]],
    writer_metrics: dict[str, Any],
    capacity_state: str,
    capacity: DiagnosticsCapacityGuard,
) -> str:
    """Best-effort derived output that never consumes the critical seal reserve."""

    try:
        _, _, generated = build_summary_artifacts(
            metadata,
            events,
            source_stats=source_stats,
            writer_metrics=writer_metrics,
            capacity_state=capacity_state,
        )
    except Exception:  # noqa: BLE001 - the immutable capsule remains authoritative.
        return "derived_artifacts_failed"
    artifacts = {
        name: generated[name]
        for name in DERIVED_ARTIFACT_NAMES
        if name in generated
    }
    new_total = sum(len(payload) for payload in artifacts.values())
    if new_total > DERIVED_ARTIFACT_RESERVATION_BYTES:
        return "derived_artifacts_failed"
    old_total = sum(
        private_file_size(
            resolve_beneath(session_path, name),
            trusted_root=capacity.root,
            missing_ok=True,
        )
        for name in DERIVED_ARTIFACT_NAMES
    )
    try:
        reservation = capacity.reserve(max(1, new_total), critical=False)
    except DiagnosticsCapacityError:
        return "derived_artifacts_deferred_capacity"
    try:
        for name in DERIVED_ARTIFACT_NAMES:
            payload = artifacts.get(name)
            if payload is not None:
                atomic_write_bytes(
                    resolve_beneath(session_path, name),
                    payload,
                    trusted_root=capacity.root,
                )
        fsync_directory(session_path, trusted_root=capacity.root)
        reservation.commit_replacement(
            old_size=old_total,
            new_size=new_total,
            actual_peak_bytes=new_total,
        )
        return "derived_artifacts_complete"
    except Exception:  # noqa: BLE001 - preserve and account any partial derived files.
        current_total = sum(
            private_file_size(
                resolve_beneath(session_path, name),
                trusted_root=capacity.root,
                missing_ok=True,
            )
            for name in DERIVED_ARTIFACT_NAMES
        )
        if not reservation.closed:
            reservation.commit_replacement(
                old_size=old_total,
                new_size=current_total,
                actual_peak_bytes=min(new_total, reservation.reserved_bytes),
            )
        return "derived_artifacts_failed"


def write_critical_seal(
    *,
    root: Path,
    session_relative_path: str,
    metadata: dict[str, Any],
    seal_capsule: dict[str, Any],
    journal_reports: list[dict[str, Any]],
    capacity: DiagnosticsCapacityGuard,
) -> None:
    """Write capsule files, then write the manifest last and fsync the directory."""

    session_path = resolve_beneath(root, session_relative_path)
    artifacts = {
        "session.json": encode_json_document(metadata),
        "seal.json": encode_json_document(seal_capsule),
    }
    manifest = build_manifest(
        root,
        session_relative_path,
        journal_reports=journal_reports,
        content_overrides=artifacts,
    )
    artifacts["manifest.json"] = encode_json_document(manifest)
    old_sizes = {
        name: private_file_size(
            resolve_beneath(session_path, name),
            trusted_root=root,
            missing_ok=True,
        )
        for name in CRITICAL_SEAL_NAMES
    }
    old_total = sum(old_sizes.values())
    new_total = sum(len(artifacts[name]) for name in CRITICAL_SEAL_NAMES)
    current_delta = 0
    peak = 0
    for name in CRITICAL_SEAL_NAMES:
        peak = max(peak, current_delta + len(artifacts[name]))
        current_delta += len(artifacts[name]) - old_sizes[name]
    reservation = capacity.reserve(max(1, peak), critical=True)
    try:
        atomic_write_bytes(
            resolve_beneath(session_path, "session.json"),
            artifacts["session.json"],
            trusted_root=root,
        )
        atomic_write_bytes(
            resolve_beneath(session_path, "seal.json"),
            artifacts["seal.json"],
            trusted_root=root,
        )
        fsync_directory(session_path, trusted_root=root)
        atomic_write_bytes(
            resolve_beneath(session_path, "manifest.json"),
            artifacts["manifest.json"],
            trusted_root=root,
        )
        fsync_directory(session_path, trusted_root=root)
        reservation.commit_replacement(
            old_size=old_total,
            new_size=new_total,
            actual_peak_bytes=peak,
        )
    except Exception:
        current_total = sum(
            private_file_size(
                resolve_beneath(session_path, name),
                trusted_root=root,
                missing_ok=True,
            )
            for name in CRITICAL_SEAL_NAMES
        )
        if not reservation.closed:
            reservation.commit_replacement(
                old_size=old_total,
                new_size=current_total,
                actual_peak_bytes=min(peak, reservation.reserved_bytes),
            )
        raise
