#!/usr/bin/env python3
"""Metadata-only simulator for Route2 canonical generation authority.

This developer-only tool exercises the single-writer authority state machine
under ``dev/artifacts``. It does not run ffmpeg, write media bytes, touch the
production shared output store, or enable serving/reuse.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_BASE = PROJECT_ROOT / "dev" / "artifacts" / "route2-canonical-authority-simulator"
SCRIPT_VERSION = "route2-canonical-authority-simulator-v1"

SERVING_BLOCKED_BASE_REASONS = [
    "serving_disabled",
    "metadata_only",
    "media_bytes_not_present",
    "timestamp_packaging_proof_missing",
]
ACTIVE_STATES = {"pending", "generating", "validating"}
TERMINAL_REPLACABLE_STATES = {"abandoned", "expired", "failed"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _timestamp_for_path() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _stable_id(*parts: object, length: int = 16) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return digest[:length]


def _validate_range(start_index: int, end_index_exclusive: int, segment_duration_seconds: float) -> None:
    if int(start_index) < 0:
        raise ValueError("range_start_index must be non-negative")
    if int(end_index_exclusive) <= int(start_index):
        raise ValueError("range_end_index_exclusive must be greater than range_start_index")
    if float(segment_duration_seconds) <= 0:
        raise ValueError("segment_duration_seconds must be positive")


def _range_relation(
    existing_start: int,
    existing_end: int,
    requested_start: int,
    requested_end: int,
) -> str:
    if requested_start == existing_start and requested_end == existing_end:
        return "exact"
    if existing_start <= requested_start and requested_end <= existing_end:
        return "subrange"
    if requested_start <= existing_start and existing_end <= requested_end:
        return "wider"
    if requested_end == existing_start or requested_start == existing_end:
        return "adjacent"
    if requested_start < existing_end and requested_end > existing_start:
        return "overlap"
    return "disjoint"


@dataclass(slots=True)
class LeaseRecord:
    authority_id: str
    generation_id: str
    writer_id: str
    status: str
    heartbeat_at: datetime
    expires_at: datetime
    range_start_index: int
    range_end_index_exclusive: int

    def heartbeat(self, now: datetime, ttl_seconds: int) -> None:
        self.status = "active"
        self.heartbeat_at = now
        self.expires_at = now + timedelta(seconds=ttl_seconds)

    def is_stale(self, now: datetime) -> bool:
        return now >= self.expires_at and self.status == "active"

    def to_metadata(self, now: datetime) -> dict[str, object]:
        stale = self.is_stale(now)
        return {
            "authority_id": self.authority_id,
            "generation_id": self.generation_id,
            "writer_id": self.writer_id,
            "status": self.status,
            "heartbeat_at": _iso(self.heartbeat_at),
            "expires_at": _iso(self.expires_at),
            "range_start_index": self.range_start_index,
            "range_end_index_exclusive": self.range_end_index_exclusive,
            "stale": stale,
            "expired": now >= self.expires_at,
        }


@dataclass(slots=True)
class AuthorityRecord:
    authority_id: str
    shared_output_key: str
    output_contract_fingerprint: str
    source_fingerprint: str
    profile: str
    playback_mode: str
    segment_duration_seconds: float
    generation_strategy_version: str
    preset: str
    thread_policy: str
    threads: int
    ffmpeg_version: str
    timestamp_policy: str
    range_start_index: int
    range_end_index_exclusive: int
    generation_id: str
    state: str
    created_at: datetime
    updated_at: datetime
    lease: LeaseRecord | None = None
    init_hash_sha256: str | None = None
    init_size_bytes: int | None = None
    segment_hashes: dict[int, str] = field(default_factory=dict)
    byte_integrity_validated: bool = False
    segment_bytes_stable: bool = False
    timestamp_validation_status: str = "not_run_metadata_only"
    keyframe_validation_status: str = "not_run_metadata_only"
    contiguous_range_validated: bool = False
    validation_blockers: list[str] = field(
        default_factory=lambda: ["media_proof_missing", "timestamp_packaging_proof_missing"]
    )
    serving_allowed: bool = False
    serving_blocked: bool = True
    serving_blocked_reasons: list[str] = field(default_factory=lambda: list(SERVING_BLOCKED_BASE_REASONS))
    conflict_indexes: list[int] = field(default_factory=list)
    conflict_count: int = 0
    first_conflict_at: datetime | None = None
    last_conflict_at: datetime | None = None
    segment_hash_conflicts: list[dict[str, object]] = field(default_factory=list)
    mixed_writer_conflict: bool = False

    def same_contract(self, other: "AuthorityRecord") -> bool:
        return (
            self.shared_output_key == other.shared_output_key
            and self.output_contract_fingerprint == other.output_contract_fingerprint
            and self.source_fingerprint == other.source_fingerprint
            and self.profile == other.profile
            and self.playback_mode == other.playback_mode
            and self.segment_duration_seconds == other.segment_duration_seconds
            and self.generation_strategy_version == other.generation_strategy_version
            and self.preset == other.preset
            and self.thread_policy == other.thread_policy
            and self.threads == other.threads
            and self.timestamp_policy == other.timestamp_policy
        )

    def transition(self, state: str, now: datetime) -> None:
        if state not in {"pending", "generating", "validating", "validated", "conflict", "failed", "abandoned", "expired"}:
            raise ValueError(f"Unsupported authority state: {state}")
        self.state = state
        self.updated_at = now

    def validate_metadata_only(self, now: datetime) -> None:
        self.transition("validated", now)
        self.init_hash_sha256 = self.init_hash_sha256 or "sim-init-hash-placeholder"
        self.init_size_bytes = self.init_size_bytes or 1024
        self.segment_hashes = {
            index: f"sim-segment-hash-{index}"
            for index in range(self.range_start_index, self.range_end_index_exclusive)
        }
        self.contiguous_range_validated = True
        self.byte_integrity_validated = False
        self.segment_bytes_stable = False
        self.timestamp_validation_status = "not_run_metadata_only"
        self.keyframe_validation_status = "not_run_metadata_only"
        self.validation_blockers = ["media_proof_missing", "timestamp_packaging_proof_missing"]
        self.serving_allowed = False
        self.serving_blocked = True
        self.serving_blocked_reasons = sorted(set(SERVING_BLOCKED_BASE_REASONS + self.validation_blockers))

    def record_segment_hash_conflict(
        self,
        *,
        index: int,
        existing_hash: str,
        candidate_hash: str,
        now: datetime,
    ) -> None:
        self.transition("conflict", now)
        if index not in self.conflict_indexes:
            self.conflict_indexes.append(index)
            self.conflict_indexes.sort()
        self.conflict_count = len(self.conflict_indexes)
        self.first_conflict_at = self.first_conflict_at or now
        self.last_conflict_at = now
        self.mixed_writer_conflict = True
        self.segment_bytes_stable = False
        self.serving_allowed = False
        self.serving_blocked = True
        self.serving_blocked_reasons = sorted(
            set(self.serving_blocked_reasons + ["segment_hash_conflict", "canonical_generation_required"])
        )
        self.segment_hash_conflicts.append(
            {
                "index": index,
                "existing_sha256": existing_hash,
                "candidate_sha256": candidate_hash,
                "detected_at": _iso(now),
            }
        )

    def to_metadata(self, now: datetime) -> dict[str, object]:
        return {
            "authority_id": self.authority_id,
            "shared_output_key": self.shared_output_key,
            "output_contract_fingerprint": self.output_contract_fingerprint,
            "source_fingerprint": self.source_fingerprint,
            "profile": self.profile,
            "playback_mode": self.playback_mode,
            "segment_duration_seconds": self.segment_duration_seconds,
            "generation_strategy_version": self.generation_strategy_version,
            "preset": self.preset,
            "thread_policy": self.thread_policy,
            "threads": self.threads,
            "ffmpeg_version": self.ffmpeg_version,
            "timestamp_policy": self.timestamp_policy,
            "range_start_index": self.range_start_index,
            "range_end_index_exclusive": self.range_end_index_exclusive,
            "generation_id": self.generation_id,
            "state": self.state,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "lease": self.lease.to_metadata(now) if self.lease else None,
            "init_hash_sha256": self.init_hash_sha256,
            "init_size_bytes": self.init_size_bytes,
            "segment_hashes": {str(index): value for index, value in sorted(self.segment_hashes.items())},
            "byte_integrity_validated": self.byte_integrity_validated,
            "segment_bytes_stable": self.segment_bytes_stable,
            "timestamp_validation_status": self.timestamp_validation_status,
            "keyframe_validation_status": self.keyframe_validation_status,
            "contiguous_range_validated": self.contiguous_range_validated,
            "validation_blockers": self.validation_blockers,
            "media_bytes_present": False,
            "serving_allowed": False,
            "serving_blocked": True,
            "serving_blocked_reasons": sorted(set(self.serving_blocked_reasons)),
            "canonical_generation_required": True,
            "conflict_indexes": self.conflict_indexes,
            "conflict_count": self.conflict_count,
            "first_conflict_at": _iso(self.first_conflict_at) if self.first_conflict_at else None,
            "last_conflict_at": _iso(self.last_conflict_at) if self.last_conflict_at else None,
            "mixed_writer_conflict": self.mixed_writer_conflict,
            "segment_hash_conflicts": self.segment_hash_conflicts,
        }


@dataclass(slots=True)
class AcquireResult:
    action: str
    authority: AuthorityRecord | None
    existing_authority_id: str | None
    independent_writer_allowed: bool
    reasons: list[str]


class AuthoritySimulator:
    def __init__(self) -> None:
        self.now = _utcnow()
        self.lease_ttl_seconds = 30
        self.authorities: dict[str, AuthorityRecord] = {}

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)

    def _new_authority(
        self,
        *,
        range_start_index: int,
        range_end_index_exclusive: int,
        generation_id: str,
        writer_id: str,
        threads: int = 4,
    ) -> AuthorityRecord:
        _validate_range(range_start_index, range_end_index_exclusive, 2.0)
        shared_output_key = "r2ss:sim:local-title:mobile_1080p:full"
        output_contract_fingerprint = "contract:sim:h264-aac-fmp4-superfast-v1"
        generation_strategy_version = "single-writer-contiguous-canonical-v1"
        timestamp_policy = "contiguous_range_timeline_unproven"
        authority_id = "auth:" + _stable_id(
            shared_output_key,
            output_contract_fingerprint,
            generation_strategy_version,
            timestamp_policy,
            range_start_index,
            range_end_index_exclusive,
            generation_id,
        )
        lease = LeaseRecord(
            authority_id=authority_id,
            generation_id=generation_id,
            writer_id=writer_id,
            status="active",
            heartbeat_at=self.now,
            expires_at=self.now + timedelta(seconds=self.lease_ttl_seconds),
            range_start_index=range_start_index,
            range_end_index_exclusive=range_end_index_exclusive,
        )
        return AuthorityRecord(
            authority_id=authority_id,
            shared_output_key=shared_output_key,
            output_contract_fingerprint=output_contract_fingerprint,
            source_fingerprint="source:sim-local-redacted",
            profile="mobile_1080p",
            playback_mode="full",
            segment_duration_seconds=2.0,
            generation_strategy_version=generation_strategy_version,
            preset="superfast",
            thread_policy="fixed_threads",
            threads=threads,
            ffmpeg_version="placeholder-unproven",
            timestamp_policy=timestamp_policy,
            range_start_index=range_start_index,
            range_end_index_exclusive=range_end_index_exclusive,
            generation_id=generation_id,
            state="pending",
            created_at=self.now,
            updated_at=self.now,
            lease=lease,
        )

    def acquire(
        self,
        *,
        range_start_index: int,
        range_end_index_exclusive: int,
        generation_id: str,
        writer_id: str,
    ) -> AcquireResult:
        _validate_range(range_start_index, range_end_index_exclusive, 2.0)
        for existing in self.authorities.values():
            if existing.state in TERMINAL_REPLACABLE_STATES:
                continue
            relation = _range_relation(
                existing.range_start_index,
                existing.range_end_index_exclusive,
                range_start_index,
                range_end_index_exclusive,
            )
            if relation == "exact" and existing.state in ACTIVE_STATES:
                return AcquireResult(
                    action="follow_wait_existing_authority",
                    authority=None,
                    existing_authority_id=existing.authority_id,
                    independent_writer_allowed=False,
                    reasons=["same_range_active_authority"],
                )
            if relation == "exact" and existing.state == "validated":
                return AcquireResult(
                    action="observe_existing_validated_authority",
                    authority=None,
                    existing_authority_id=existing.authority_id,
                    independent_writer_allowed=False,
                    reasons=["same_range_validated_metadata_non_serving"],
                )
            if relation == "subrange":
                return AcquireResult(
                    action="observe_existing_authority",
                    authority=None,
                    existing_authority_id=existing.authority_id,
                    independent_writer_allowed=False,
                    reasons=[f"requested_subrange_inside_{existing.state}_authority"],
                )
            if relation == "wider":
                return AcquireResult(
                    action="requires_planner_decision_no_silent_merge",
                    authority=None,
                    existing_authority_id=existing.authority_id,
                    independent_writer_allowed=False,
                    reasons=[f"requested_wider_range_covers_{existing.state}_authority"],
                )
            if relation == "overlap":
                return AcquireResult(
                    action="blocked_overlapping_authority",
                    authority=None,
                    existing_authority_id=existing.authority_id,
                    independent_writer_allowed=False,
                    reasons=[f"overlap_with_{existing.state}_authority"],
                )
        authority = self._new_authority(
            range_start_index=range_start_index,
            range_end_index_exclusive=range_end_index_exclusive,
            generation_id=generation_id,
            writer_id=writer_id,
        )
        self.authorities[authority.authority_id] = authority
        return AcquireResult(
            action="acquired_new_authority",
            authority=authority,
            existing_authority_id=None,
            independent_writer_allowed=True,
            reasons=["no_active_conflicting_authority"],
        )

    def heartbeat(self, authority: AuthorityRecord) -> None:
        if authority.lease is None:
            raise ValueError("authority has no lease")
        authority.lease.heartbeat(self.now, self.lease_ttl_seconds)
        authority.updated_at = self.now

    def abandon_if_stale(self, authority: AuthorityRecord) -> bool:
        if authority.lease is None or not authority.lease.is_stale(self.now):
            return False
        authority.lease.status = "abandoned"
        authority.transition("abandoned", self.now)
        authority.serving_blocked_reasons = sorted(set(authority.serving_blocked_reasons + ["writer_lease_abandoned"]))
        return True

    def expire(self, authority: AuthorityRecord) -> None:
        if authority.lease:
            authority.lease.status = "expired"
        authority.transition("expired", self.now)
        authority.serving_blocked_reasons = sorted(set(authority.serving_blocked_reasons + ["authority_expired"]))


def _result_payload(
    *,
    scenario_id: str,
    description: str,
    result: AcquireResult | None = None,
    authority: AuthorityRecord | None = None,
    notes: list[str] | None = None,
    simulator: AuthoritySimulator,
) -> dict[str, object]:
    target_authority = authority or (result.authority if result else None)
    return {
        "scenario_id": scenario_id,
        "description": description,
        "action": result.action if result else None,
        "independent_writer_allowed": result.independent_writer_allowed if result else None,
        "existing_authority_id": result.existing_authority_id if result else None,
        "authority_id": target_authority.authority_id if target_authority else None,
        "state": target_authority.state if target_authority else None,
        "serving_allowed": False,
        "serving_blocked": True,
        "reasons": (result.reasons if result else []) + (notes or []),
        "authority_metadata": target_authority.to_metadata(simulator.now) if target_authority else None,
    }


def _write_outputs(artifact_root: Path, scenarios: list[dict[str, object]], simulator: AuthoritySimulator) -> None:
    artifact_root.mkdir(parents=True, exist_ok=False)
    summary = {
        "script_version": SCRIPT_VERSION,
        "created_at": _iso(_utcnow()),
        "artifact_root": str(artifact_root),
        "metadata_only": True,
        "ffmpeg_used": False,
        "media_bytes_written": False,
        "cloud_used": False,
        "production_shared_outputs_touched": False,
        "serving_allowed_everywhere": False,
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
        "final_authorities": {
            authority_id: authority.to_metadata(simulator.now)
            for authority_id, authority in sorted(simulator.authorities.items())
        },
    }
    (artifact_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with (artifact_root / "scenarios.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "scenario_id",
                "action",
                "independent_writer_allowed",
                "authority_id",
                "existing_authority_id",
                "state",
                "serving_allowed",
                "reasons",
            ]
        )
        for scenario in scenarios:
            writer.writerow(
                [
                    scenario["scenario_id"],
                    scenario["action"],
                    scenario["independent_writer_allowed"],
                    scenario["authority_id"],
                    scenario["existing_authority_id"],
                    scenario["state"],
                    scenario["serving_allowed"],
                    ";".join(str(reason) for reason in scenario["reasons"]),
                ]
            )


def run_scenarios() -> tuple[Path, list[dict[str, object]], AuthoritySimulator]:
    simulator = AuthoritySimulator()
    scenarios: list[dict[str, object]] = []

    result_a = simulator.acquire(
        range_start_index=15,
        range_end_index_exclusive=19,
        generation_id="gen-a",
        writer_id="writer-a",
    )
    authority_a = result_a.authority
    if authority_a is None:
        raise RuntimeError("Scenario A failed to acquire authority")
    authority_a.transition("generating", simulator.now)
    scenarios.append(
        _result_payload(
            scenario_id="A",
            description="Acquire new authority for range [15,19).",
            result=result_a,
            authority=authority_a,
            simulator=simulator,
        )
    )

    result_b = simulator.acquire(
        range_start_index=15,
        range_end_index_exclusive=19,
        generation_id="gen-b",
        writer_id="writer-b",
    )
    scenarios.append(
        _result_payload(
            scenario_id="B",
            description="Second writer requests exact same active range [15,19).",
            result=result_b,
            simulator=simulator,
        )
    )

    result_c = simulator.acquire(
        range_start_index=16,
        range_end_index_exclusive=20,
        generation_id="gen-c",
        writer_id="writer-c",
    )
    scenarios.append(
        _result_payload(
            scenario_id="C",
            description="Second writer requests overlapping range [16,20).",
            result=result_c,
            simulator=simulator,
        )
    )

    simulator.advance(5)
    simulator.heartbeat(authority_a)
    authority_a.transition("validating", simulator.now)
    authority_a.validate_metadata_only(simulator.now)
    scenarios.append(
        _result_payload(
            scenario_id="D",
            description="Heartbeat then validate authority metadata; serving remains blocked.",
            authority=authority_a,
            notes=["validated_metadata_only_not_media_proof"],
            simulator=simulator,
        )
    )

    result_e = simulator.acquire(
        range_start_index=24,
        range_end_index_exclusive=28,
        generation_id="gen-e",
        writer_id="writer-e",
    )
    authority_e = result_e.authority
    if authority_e is None:
        raise RuntimeError("Scenario E setup failed to acquire authority")
    authority_e.validate_metadata_only(simulator.now)
    authority_e.record_segment_hash_conflict(
        index=25,
        existing_hash="existing-placeholder-hash-25",
        candidate_hash="candidate-placeholder-hash-25",
        now=simulator.now,
    )
    scenarios.append(
        _result_payload(
            scenario_id="E",
            description="Candidate segment hash placeholder differs; conflict prevents overwrite/serving.",
            authority=authority_e,
            notes=["segment_hash_conflict_recorded", "no_overwrite"],
            simulator=simulator,
        )
    )

    result_f1 = simulator.acquire(
        range_start_index=30,
        range_end_index_exclusive=34,
        generation_id="gen-f-stale",
        writer_id="writer-f",
    )
    authority_f = result_f1.authority
    if authority_f is None:
        raise RuntimeError("Scenario F setup failed to acquire authority")
    authority_f.transition("generating", simulator.now)
    simulator.heartbeat(authority_f)
    simulator.advance(simulator.lease_ttl_seconds + 1)
    stale_detected = authority_f.lease.is_stale(simulator.now) if authority_f.lease else False
    abandoned = simulator.abandon_if_stale(authority_f)
    simulator.expire(authority_f)
    result_f2 = simulator.acquire(
        range_start_index=30,
        range_end_index_exclusive=34,
        generation_id="gen-f-replacement",
        writer_id="writer-f-replacement",
    )
    scenarios.append(
        _result_payload(
            scenario_id="F",
            description="Stale lease is abandoned/expired, then a new generation can acquire the range.",
            result=result_f2,
            authority=result_f2.authority,
            notes=[
                f"stale_detected={str(stale_detected).lower()}",
                f"abandoned={str(abandoned).lower()}",
                "new_generation_allowed_after_explicit_expiry",
            ],
            simulator=simulator,
        )
    )

    result_g = simulator.acquire(
        range_start_index=19,
        range_end_index_exclusive=23,
        generation_id="gen-g",
        writer_id="writer-g",
    )
    scenarios.append(
        _result_payload(
            scenario_id="G",
            description="Adjacent range [19,23) can create separate metadata, but no sparse stitch serving.",
            result=result_g,
            authority=result_g.authority,
            notes=["adjacent_metadata_allowed_non_serving"],
            simulator=simulator,
        )
    )

    result_h = simulator.acquire(
        range_start_index=40,
        range_end_index_exclusive=44,
        generation_id="gen-h",
        writer_id="writer-h",
    )
    scenarios.append(
        _result_payload(
            scenario_id="H",
            description="Disjoint range [40,44) can create separate metadata, non-serving.",
            result=result_h,
            authority=result_h.authority,
            notes=["disjoint_metadata_allowed_non_serving"],
            simulator=simulator,
        )
    )

    result_i = simulator.acquire(
        range_start_index=15,
        range_end_index_exclusive=25,
        generation_id="gen-i",
        writer_id="writer-i",
    )
    scenarios.append(
        _result_payload(
            scenario_id="I",
            description="Wider range [15,25) over existing [15,19) does not silently merge.",
            result=result_i,
            notes=["planner_decision_required"],
            simulator=simulator,
        )
    )

    result_j = simulator.acquire(
        range_start_index=16,
        range_end_index_exclusive=18,
        generation_id="gen-j",
        writer_id="writer-j",
    )
    scenarios.append(
        _result_payload(
            scenario_id="J",
            description="Subrange [16,18) observes existing validated [15,19), no new writer.",
            result=result_j,
            notes=["observe_validated_metadata_non_serving"],
            simulator=simulator,
        )
    )

    artifact_root = ARTIFACT_BASE / _timestamp_for_path()
    _write_outputs(artifact_root, scenarios, simulator)
    return artifact_root, scenarios, simulator


def main() -> int:
    artifact_root, scenarios, simulator = run_scenarios()
    serving_allowed_any = any(bool(scenario["serving_allowed"]) for scenario in scenarios)
    print(
        json.dumps(
            {
                "artifact_root": str(artifact_root),
                "scenario_count": len(scenarios),
                "serving_allowed_any": serving_allowed_any,
                "authority_count": len(simulator.authorities),
            },
            indent=2,
        )
    )
    return 1 if serving_allowed_any else 0


if __name__ == "__main__":
    raise SystemExit(main())
