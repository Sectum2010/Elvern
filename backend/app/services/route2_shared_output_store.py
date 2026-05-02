from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from ..db import utcnow_iso


SHARED_OUTPUT_STORE_METADATA_VERSION = "route2-shared-output-store-v1"
SHARED_OUTPUT_STORE_STATUS = "metadata_only"
SHARED_OUTPUT_STORE_BLOCKERS = [
    "metadata_only",
    "no_segment_writer",
    "no_shared_manifest",
    "media_bytes_not_present",
    "serving_disabled",
]
SHARED_OUTPUT_MAPPING_TOLERANCE_SECONDS = 0.001
SHARED_OUTPUT_METADATA_ONLY_RANGE_STATUS = "metadata_only_confirmed_source_session"

_SHARED_OUTPUT_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,160}$")


@dataclass(frozen=True, slots=True)
class SharedOutputIndexRange:
    start_index: int
    end_index_exclusive: int
    start_seconds: float
    end_seconds: float
    segment_count: int
    source: str = "future"

    def as_dict(self) -> dict[str, object]:
        return {
            "start_index": self.start_index,
            "end_index_exclusive": self.end_index_exclusive,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "segment_count": self.segment_count,
            "source": self.source,
        }


def shared_output_store_root(route2_root: Path) -> Path:
    return Path(route2_root) / "shared_outputs"


def validate_shared_output_key(value: str) -> str:
    key = str(value or "").strip()
    if not key or "/" in key or "\\" in key or ".." in key:
        raise ValueError("Shared output key must be a safe path component")
    if not _SHARED_OUTPUT_KEY_RE.match(key):
        raise ValueError("Shared output key contains unsupported characters")
    return key


def shared_output_directory(route2_root: Path, shared_output_key: str) -> Path:
    return shared_output_store_root(route2_root) / validate_shared_output_key(shared_output_key)


def _safe_json_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_bytes(_safe_json_bytes(payload))
        temp_path.replace(path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _write_text_atomic(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(payload, encoding="utf-8")
        temp_path.replace(path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _read_json_mapping(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(payload, dict):
        raise ValueError(f"Shared output metadata file is not a JSON object: {path.name}")
    return dict(payload)


def _contract_conflict_view(payload: Mapping[str, object]) -> dict[str, object]:
    return {
        key: payload.get(key)
        for key in sorted(payload)
        if key not in {"created_at", "updated_at"}
    }


def _ordered_store_blockers(blockers: Iterable[str]) -> list[str]:
    values = {str(item) for item in blockers}
    ordered = [blocker for blocker in SHARED_OUTPUT_STORE_BLOCKERS if blocker in values]
    ordered.extend(sorted(values - set(ordered)))
    return ordered


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size_bytes += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size_bytes


def _read_sha256_file(path: Path) -> str | None:
    try:
        payload = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return payload.split()[0].strip().lower() if payload else None


def _validate_segment_duration(segment_duration_seconds: float) -> float:
    duration = float(segment_duration_seconds)
    if duration <= 0:
        raise ValueError("Segment duration must be positive")
    return duration


def absolute_segment_index_from_seconds(time_seconds: float, segment_duration_seconds: float) -> int:
    duration = _validate_segment_duration(segment_duration_seconds)
    return max(0, int(math.floor(max(0.0, float(time_seconds)) / duration)))


def absolute_segment_end_index_exclusive_from_seconds(time_seconds: float, segment_duration_seconds: float) -> int:
    duration = _validate_segment_duration(segment_duration_seconds)
    return max(0, int(math.ceil(max(0.0, float(time_seconds)) / duration)))


def absolute_segment_time_range(index: int, segment_duration_seconds: float) -> tuple[float, float]:
    segment_index = int(index)
    if segment_index < 0:
        raise ValueError("Segment index must be non-negative")
    duration = _validate_segment_duration(segment_duration_seconds)
    start_seconds = round(segment_index * duration, 6)
    end_seconds = round((segment_index + 1) * duration, 6)
    return start_seconds, end_seconds


def shared_segment_filename(index: int) -> str:
    segment_index = int(index)
    if segment_index < 0:
        raise ValueError("Segment index must be non-negative")
    return f"abs_{segment_index:012d}.m4s"


def build_route2_init_metadata(init_path: Path | None) -> dict[str, object]:
    if init_path is None:
        return {
            "route2_init_available": False,
            "route2_init_hash_sha256": None,
            "route2_init_hash_available": False,
            "route2_init_hash_reason": "pending_init",
            "route2_init_size_bytes": None,
            "route2_init_metadata_available": False,
            "route2_init_compatibility_status": "pending_init",
            "route2_init_compatibility_blockers": ["pending_init_compatibility"],
        }
    path = Path(init_path)
    try:
        stat_result = path.stat()
    except OSError:
        return {
            "route2_init_available": False,
            "route2_init_hash_sha256": None,
            "route2_init_hash_available": False,
            "route2_init_hash_reason": "pending_init",
            "route2_init_size_bytes": None,
            "route2_init_metadata_available": False,
            "route2_init_compatibility_status": "pending_init",
            "route2_init_compatibility_blockers": ["pending_init_compatibility"],
        }
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return {
            "route2_init_available": True,
            "route2_init_hash_sha256": None,
            "route2_init_hash_available": False,
            "route2_init_hash_reason": "hash_unavailable",
            "route2_init_size_bytes": stat_result.st_size,
            "route2_init_metadata_available": False,
            "route2_init_compatibility_status": "unavailable",
            "route2_init_compatibility_blockers": ["init_hash_unavailable"],
        }
    return {
        "route2_init_available": True,
        "route2_init_hash_sha256": digest.hexdigest(),
        "route2_init_hash_available": True,
        "route2_init_hash_reason": "hash_available",
        "route2_init_size_bytes": stat_result.st_size,
        "route2_init_metadata_available": True,
        "route2_init_compatibility_status": "hash_available",
        "route2_init_compatibility_blockers": [],
    }


def _canonical_segment_index_candidate(
    absolute_seconds: float,
    segment_duration_seconds: float,
    *,
    tolerance_seconds: float = SHARED_OUTPUT_MAPPING_TOLERANCE_SECONDS,
) -> tuple[int, bool]:
    duration = _validate_segment_duration(segment_duration_seconds)
    seconds = max(0.0, float(absolute_seconds))
    ratio = seconds / duration
    nearest_index = max(0, int(round(ratio)))
    nearest_boundary_seconds = nearest_index * duration
    if abs(seconds - nearest_boundary_seconds) <= max(0.0, float(tolerance_seconds)):
        return nearest_index, True
    return absolute_segment_index_from_seconds(seconds, duration), False


def build_epoch_relative_segment_mapping(
    *,
    epoch_id: str,
    epoch_start_seconds: float,
    epoch_relative_segment_index: int,
    segment_duration_seconds: float,
    target_position_seconds: float | None = None,
    tolerance_seconds: float = SHARED_OUTPUT_MAPPING_TOLERANCE_SECONDS,
) -> dict[str, object]:
    segment_index = int(epoch_relative_segment_index)
    if segment_index < 0:
        raise ValueError("Epoch-relative segment index must be non-negative")
    duration = _validate_segment_duration(segment_duration_seconds)
    epoch_start = max(0.0, float(epoch_start_seconds))
    relative_start_seconds = round(segment_index * duration, 6)
    relative_end_seconds = round((segment_index + 1) * duration, 6)
    absolute_start_seconds = round(epoch_start + relative_start_seconds, 6)
    absolute_end_seconds = round(epoch_start + relative_end_seconds, 6)
    absolute_index, start_aligned = _canonical_segment_index_candidate(
        absolute_start_seconds,
        duration,
        tolerance_seconds=tolerance_seconds,
    )
    expected_end = round((absolute_index + 1) * duration, 6)
    end_aligned = abs(absolute_end_seconds - expected_end) <= max(0.0, float(tolerance_seconds))
    blockers: list[str] = []
    if not start_aligned or not end_aligned:
        blockers.append("non_canonical_segment_boundary")
    if target_position_seconds is not None and absolute_start_seconds < float(target_position_seconds):
        blockers.extend(["epoch_private_preroll", "preroll_not_shareable"])
    canonical_alignment_status = "aligned" if not blockers or "non_canonical_segment_boundary" not in blockers else "non_canonical_segment_boundary"
    return {
        "epoch_id": str(epoch_id),
        "epoch_start_seconds": round(epoch_start, 6),
        "epoch_relative_segment_index": segment_index,
        "epoch_relative_start_seconds": relative_start_seconds,
        "epoch_relative_end_seconds": relative_end_seconds,
        "absolute_start_seconds": absolute_start_seconds,
        "absolute_end_seconds": absolute_end_seconds,
        "absolute_segment_index_candidate": absolute_index,
        "expected_shared_segment_filename": shared_segment_filename(absolute_index),
        "canonical_alignment_status": canonical_alignment_status,
        "mapping_confidence": "high" if not blockers else "low",
        "mapping_blockers": sorted(set(blockers)),
    }


def _coerce_range_indexes(value: object) -> tuple[int, int]:
    if isinstance(value, SharedOutputIndexRange):
        start_index = value.start_index
        end_index_exclusive = value.end_index_exclusive
    elif isinstance(value, Mapping):
        start_index = int(value["start_index"])
        end_index_exclusive = int(value["end_index_exclusive"])
    else:
        start_index, end_index_exclusive = value  # type: ignore[misc]
        start_index = int(start_index)
        end_index_exclusive = int(end_index_exclusive)
    if start_index < 0 or end_index_exclusive < 0:
        raise ValueError("Range indexes must be non-negative")
    if end_index_exclusive <= start_index:
        raise ValueError("Range end must be greater than range start")
    return start_index, end_index_exclusive


def _range_payload(
    start_index: int,
    end_index_exclusive: int,
    *,
    segment_duration_seconds: float,
    source: str = "future",
) -> dict[str, object]:
    duration = _validate_segment_duration(segment_duration_seconds)
    start_seconds = round(start_index * duration, 6)
    end_seconds = round(end_index_exclusive * duration, 6)
    return SharedOutputIndexRange(
        start_index=start_index,
        end_index_exclusive=end_index_exclusive,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        segment_count=end_index_exclusive - start_index,
        source=str(source or "future"),
    ).as_dict()


def merge_contiguous_ranges(
    ranges: Iterable[object],
    *,
    segment_duration_seconds: float,
    source: str = "future",
) -> list[dict[str, object]]:
    ordered = sorted(_coerce_range_indexes(value) for value in ranges)
    if not ordered:
        return []
    merged: list[tuple[int, int]] = []
    current_start, current_end = ordered[0]
    for start_index, end_index_exclusive in ordered[1:]:
        if start_index <= current_end:
            current_end = max(current_end, end_index_exclusive)
            continue
        merged.append((current_start, current_end))
        current_start, current_end = start_index, end_index_exclusive
    merged.append((current_start, current_end))
    return [
        _range_payload(
            start_index,
            end_index_exclusive,
            segment_duration_seconds=segment_duration_seconds,
            source=source,
        )
        for start_index, end_index_exclusive in merged
    ]


def find_contiguous_range_covering(
    ranges: Iterable[object],
    start_index: int,
    *,
    segment_duration_seconds: float,
) -> dict[str, object] | None:
    target = int(start_index)
    if target < 0:
        raise ValueError("Segment index must be non-negative")
    for confirmed_range in merge_contiguous_ranges(
        ranges,
        segment_duration_seconds=segment_duration_seconds,
    ):
        if int(confirmed_range["start_index"]) <= target < int(confirmed_range["end_index_exclusive"]):
            return confirmed_range
    return None


def find_gaps_for_requested_range(
    ranges: Iterable[object],
    start_index: int,
    end_index_exclusive: int,
    *,
    segment_duration_seconds: float,
) -> list[dict[str, object]]:
    request_start = int(start_index)
    request_end = int(end_index_exclusive)
    if request_start < 0 or request_end < 0 or request_end < request_start:
        raise ValueError("Requested range indexes are invalid")
    if request_end == request_start:
        return []
    gaps: list[tuple[int, int]] = []
    cursor = request_start
    for confirmed_range in merge_contiguous_ranges(
        ranges,
        segment_duration_seconds=segment_duration_seconds,
    ):
        range_start = int(confirmed_range["start_index"])
        range_end = int(confirmed_range["end_index_exclusive"])
        if range_end <= cursor:
            continue
        if range_start >= request_end:
            break
        if range_start > cursor:
            gaps.append((cursor, min(range_start, request_end)))
        cursor = max(cursor, min(range_end, request_end))
        if cursor >= request_end:
            break
    if cursor < request_end:
        gaps.append((cursor, request_end))
    return [
        _range_payload(
            gap_start,
            gap_end,
            segment_duration_seconds=segment_duration_seconds,
            source="gap",
        )
        for gap_start, gap_end in gaps
    ]


def build_ranges_metadata(
    *,
    shared_output_key: str,
    segment_duration_seconds: float,
    confirmed_ranges: Iterable[object] = (),
    sparse_segments: Iterable[int] = (),
    updated_at: str | None = None,
) -> dict[str, object]:
    return {
        "version": SHARED_OUTPUT_STORE_METADATA_VERSION,
        "shared_output_key": validate_shared_output_key(shared_output_key),
        "segment_duration_seconds": _validate_segment_duration(segment_duration_seconds),
        "confirmed_ranges": merge_contiguous_ranges(
            confirmed_ranges,
            segment_duration_seconds=segment_duration_seconds,
        ),
        "sparse_segments": sorted({max(0, int(value)) for value in sparse_segments}),
        "updated_at": updated_at or utcnow_iso(),
    }


def add_confirmed_range(
    ranges_metadata: Mapping[str, object],
    start_index: int,
    end_index_exclusive: int,
    *,
    segment_duration_seconds: float | None = None,
    source: str = "future",
    updated_at: str | None = None,
) -> dict[str, object]:
    shared_output_key = validate_shared_output_key(str(ranges_metadata["shared_output_key"]))
    duration = _validate_segment_duration(
        segment_duration_seconds
        if segment_duration_seconds is not None
        else float(ranges_metadata["segment_duration_seconds"])
    )
    existing = list(ranges_metadata.get("confirmed_ranges") or [])
    existing.append((int(start_index), int(end_index_exclusive)))
    return {
        "version": str(ranges_metadata.get("version") or SHARED_OUTPUT_STORE_METADATA_VERSION),
        "shared_output_key": shared_output_key,
        "segment_duration_seconds": duration,
        "confirmed_ranges": merge_contiguous_ranges(
            existing,
            segment_duration_seconds=duration,
            source=source,
        ),
        "sparse_segments": sorted({max(0, int(value)) for value in ranges_metadata.get("sparse_segments") or []}),
        "updated_at": updated_at or utcnow_iso(),
    }


def build_shared_store_write_plan(
    *,
    route2_root: Path,
    shared_output_key: str | None,
    epoch_id: str,
    epoch_start_seconds: float,
    target_position_seconds: float | None,
    published_segment_indices: Iterable[int],
    segment_duration_seconds: float,
    output_contract_fingerprint: str | None,
    output_contract_missing_fields: Iterable[str] = (),
    init_compatibility_validated: bool = False,
    init_compatibility_status: str | None = None,
    permission_status: str | None = None,
    metadata_only: bool = True,
    segment_writer_enabled: bool = False,
    shared_manifest_enabled: bool = False,
) -> dict[str, object]:
    duration = _validate_segment_duration(segment_duration_seconds)
    segment_indices = sorted({max(0, int(value)) for value in published_segment_indices})
    blockers: set[str] = set()
    notes = {
        "epoch_relative_to_absolute_mapping_candidate",
    }
    if segment_writer_enabled and not metadata_only:
        notes.add("write_only_segment_capture_candidate")
    else:
        notes.update({"dry_run_only", "no_shared_bytes_written"})
    sanitized_key: str | None = None
    shared_output_path: str | None = None
    expected_init_path: str | None = None
    if shared_output_key:
        sanitized_key = validate_shared_output_key(shared_output_key)
        output_dir = shared_output_directory(route2_root, sanitized_key)
        shared_output_path = str(output_dir)
        expected_init_path = str(output_dir / "init.mp4")
    else:
        blockers.add("missing_shared_output_key")
    if not str(output_contract_fingerprint or "").strip():
        blockers.add("missing_output_contract")
    if list(output_contract_missing_fields):
        blockers.add("output_contract_incomplete")
    normalized_init_status = str(init_compatibility_status or "").strip()
    init_compatible = bool(
        init_compatibility_validated
        or normalized_init_status in {"hash_available", "compatible_by_hash"}
    )
    if normalized_init_status == "mismatch":
        blockers.add("init_mismatch")
    elif normalized_init_status in {"pending", "pending_init", "unavailable"}:
        blockers.add("pending_init_compatibility")
    elif not init_compatible:
        blockers.add("missing_init_compatibility")
    if permission_status not in {"verified_local", "verified_cloud"}:
        blockers.add("permission_context_unverified")
    if metadata_only:
        blockers.add("metadata_only")
    if not segment_writer_enabled:
        blockers.add("no_segment_writer")
    if not shared_manifest_enabled:
        blockers.add("no_shared_manifest")
    if metadata_only or not segment_writer_enabled:
        blockers.add("media_bytes_not_present")
    if metadata_only or not shared_manifest_enabled:
        blockers.add("serving_disabled")
    if not segment_indices:
        blockers.add("no_published_segments")

    segment_plans: list[dict[str, object]] = []
    shareable_index_candidates: list[int] = []
    mapping_blockers: set[str] = set()
    for segment_index in segment_indices:
        mapping = build_epoch_relative_segment_mapping(
            epoch_id=epoch_id,
            epoch_start_seconds=epoch_start_seconds,
            epoch_relative_segment_index=segment_index,
            segment_duration_seconds=duration,
            target_position_seconds=target_position_seconds,
        )
        segment_blockers = set(blockers)
        segment_mapping_blockers = {str(item) for item in mapping["mapping_blockers"]}
        segment_blockers.update(segment_mapping_blockers)
        mapping_blockers.update(segment_mapping_blockers)
        segment_candidate_blockers = set(segment_blockers)
        if segment_writer_enabled:
            segment_candidate_blockers.difference_update({"no_shared_manifest", "serving_disabled"})
        absolute_index = int(mapping["absolute_segment_index_candidate"])
        expected_shared_segment_path = None
        if sanitized_key:
            expected_shared_segment_path = str(
                shared_output_directory(route2_root, sanitized_key)
                / "segments"
                / shared_segment_filename(absolute_index)
            )
        if not segment_mapping_blockers:
            shareable_index_candidates.append(absolute_index)
        segment_plans.append(
            {
                **mapping,
                "shared_store_write_candidate": not segment_candidate_blockers,
                "shared_store_write_blockers": sorted(segment_blockers),
                "shared_output_key": sanitized_key,
                "shared_output_contract_fingerprint": output_contract_fingerprint,
                "expected_shared_output_path": shared_output_path,
                "expected_shared_segment_path": expected_shared_segment_path,
                "expected_init_path": expected_init_path,
            }
        )

    candidate_ranges = merge_contiguous_ranges(
        [(index, index + 1) for index in shareable_index_candidates],
        segment_duration_seconds=duration,
        source="dry_run_candidate",
    )
    candidate_range = candidate_ranges[0] if candidate_ranges else None
    range_blockers = set(blockers)
    range_blockers.update(mapping_blockers)
    if candidate_range is None:
        range_blockers.add("no_shareable_segments")
    expected_ranges_update = None
    if sanitized_key and candidate_range is not None:
        expected_ranges_update = add_confirmed_range(
            build_ranges_metadata(
                shared_output_key=sanitized_key,
                segment_duration_seconds=duration,
                confirmed_ranges=[],
            ),
            int(candidate_range["start_index"]),
            int(candidate_range["end_index_exclusive"]),
            segment_duration_seconds=duration,
            source="dry_run_candidate",
        )
    if mapping_blockers:
        mapping_confidence = "low"
    elif segment_indices:
        mapping_confidence = "high"
    else:
        mapping_confidence = "unavailable"
    return {
        "shared_store_write_plan_available": sanitized_key is not None and bool(segment_indices),
        "shared_output_key": sanitized_key,
        "shared_output_contract_fingerprint": output_contract_fingerprint,
        "expected_shared_output_path": shared_output_path,
        "expected_init_path": expected_init_path,
        "segment_plans": segment_plans,
        "expected_ranges_update": expected_ranges_update,
        "candidate_confirmed_range_start_index": (
            int(candidate_range["start_index"]) if candidate_range is not None else None
        ),
        "candidate_confirmed_range_end_index_exclusive": (
            int(candidate_range["end_index_exclusive"]) if candidate_range is not None else None
        ),
        "candidate_confirmed_range_start_seconds": (
            float(candidate_range["start_seconds"]) if candidate_range is not None else None
        ),
        "candidate_confirmed_range_end_seconds": (
            float(candidate_range["end_seconds"]) if candidate_range is not None else None
        ),
        "candidate_range_segment_count": int(candidate_range["segment_count"]) if candidate_range is not None else 0,
        "candidate_range_blockers": sorted(range_blockers),
        "shared_store_write_candidate_count": sum(
            1 for segment_plan in segment_plans if bool(segment_plan["shared_store_write_candidate"])
        ),
        "shared_store_write_blockers": sorted(range_blockers),
        "shared_store_mapping_confidence": mapping_confidence,
        "shared_store_mapping_notes": sorted(notes),
    }


def build_shared_output_contract_metadata(
    *,
    shared_output_key: str,
    output_contract_fingerprint: str,
    output_contract_version: str,
    profile: str,
    playback_mode: str,
    source_fingerprint: str,
    source_kind: str,
    segment_duration_seconds: float,
    output_contract_summary: Mapping[str, object] | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict[str, object]:
    summary = dict(output_contract_summary or {})
    video_summary = dict(summary.get("video") or {})
    audio_summary = dict(summary.get("audio") or {})
    hls_summary = dict(summary.get("hls") or {})
    return {
        "version": SHARED_OUTPUT_STORE_METADATA_VERSION,
        "shared_output_key": validate_shared_output_key(shared_output_key),
        "output_contract_fingerprint": str(output_contract_fingerprint or "").strip(),
        "output_contract_version": str(output_contract_version or "").strip(),
        "profile": str(profile or "").strip(),
        "playback_mode": str(playback_mode or "").strip(),
        "source_fingerprint": str(source_fingerprint or "").strip(),
        "source_kind": str(source_kind or "").strip(),
        "segment_duration_seconds": _validate_segment_duration(segment_duration_seconds),
        "gop_keyframe_contract": {
            "segment_duration_seconds": _validate_segment_duration(segment_duration_seconds),
            "keyframe_alignment": "future_absolute_segment_identity_required",
            "contract_bound_by": "output_contract_fingerprint",
        },
        "output_contract_summary": {
            "video": {
                key: video_summary[key]
                for key in (
                    "codec",
                    "preset",
                    "profile",
                    "level",
                    "pix_fmt",
                    "crf",
                    "maxrate",
                    "bufsize",
                    "max_width",
                    "max_height",
                )
                if key in video_summary
            },
            "audio": {
                key: audio_summary[key]
                for key in ("codec", "channels", "sample_rate", "bitrate")
                if key in audio_summary
            },
            "hls": {
                key: hls_summary[key]
                for key in ("segment_duration_seconds", "segment_type", "init_filename", "flags")
                if key in hls_summary
            },
            "timeline": summary.get("timeline"),
        },
        "init": {
            "init_sha256": None,
            "init_compatibility_validated": False,
            "validation_status": "not_implemented",
        },
        "status": SHARED_OUTPUT_STORE_STATUS,
        "created_at": created_at or utcnow_iso(),
        "updated_at": updated_at or utcnow_iso(),
    }


def build_shared_output_metadata(
    *,
    shared_output_key: str,
    output_contract_fingerprint: str | None = None,
    source_kind: str | None = None,
    profile: str | None = None,
    playback_mode: str | None = None,
    segment_duration_seconds: float | None = None,
    status: str = SHARED_OUTPUT_STORE_STATUS,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": SHARED_OUTPUT_STORE_METADATA_VERSION,
        "shared_output_key": validate_shared_output_key(shared_output_key),
        "output_contract_fingerprint": str(output_contract_fingerprint or "").strip(),
        "metadata_version": SHARED_OUTPUT_STORE_METADATA_VERSION,
        "status": str(status or SHARED_OUTPUT_STORE_STATUS),
        "ready_for_segments": False,
        "store_ready_for_segments": False,
        "writer_policy": "disabled",
        "segment_writer_enabled": False,
        "shared_manifest_enabled": False,
        "serving_enabled": False,
        "media_bytes_present": False,
        "created_at": created_at or utcnow_iso(),
        "updated_at": updated_at or utcnow_iso(),
    }
    if source_kind is not None:
        payload["source_kind"] = str(source_kind or "").strip()
    if profile is not None:
        payload["profile"] = str(profile or "").strip()
    if playback_mode is not None:
        payload["playback_mode"] = str(playback_mode or "").strip()
    if segment_duration_seconds is not None:
        payload["segment_duration_seconds"] = _validate_segment_duration(segment_duration_seconds)
    return payload


def _metadata_only_range_payload(
    start_index: int,
    end_index_exclusive: int,
    *,
    segment_duration_seconds: float,
    source_session_id: str | None = None,
    source_epoch_id: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict[str, object]:
    timestamp = updated_at or created_at or utcnow_iso()
    payload = _range_payload(
        start_index,
        end_index_exclusive,
        segment_duration_seconds=segment_duration_seconds,
        source=SHARED_OUTPUT_METADATA_ONLY_RANGE_STATUS,
    )
    payload.update(
        {
            "range_status": SHARED_OUTPUT_METADATA_ONLY_RANGE_STATUS,
            "media_bytes_present": False,
            "created_at": created_at or timestamp,
            "updated_at": timestamp,
        }
    )
    if source_session_id:
        payload["source_session_id"] = str(source_session_id)
    if source_epoch_id:
        payload["source_epoch_id"] = str(source_epoch_id)
    return payload


def build_metadata_only_ranges_metadata(
    *,
    shared_output_key: str,
    segment_duration_seconds: float,
    confirmed_ranges: Iterable[object] = (),
    source_session_id: str | None = None,
    source_epoch_id: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict[str, object]:
    timestamp = updated_at or utcnow_iso()
    merged = merge_contiguous_ranges(
        confirmed_ranges,
        segment_duration_seconds=segment_duration_seconds,
        source=SHARED_OUTPUT_METADATA_ONLY_RANGE_STATUS,
    )
    metadata_only_ranges = [
        _metadata_only_range_payload(
            int(item["start_index"]),
            int(item["end_index_exclusive"]),
            segment_duration_seconds=segment_duration_seconds,
            source_session_id=source_session_id,
            source_epoch_id=source_epoch_id,
            created_at=created_at or timestamp,
            updated_at=timestamp,
        )
        for item in merged
    ]
    return {
        "version": SHARED_OUTPUT_STORE_METADATA_VERSION,
        "shared_output_key": validate_shared_output_key(shared_output_key),
        "segment_duration_seconds": _validate_segment_duration(segment_duration_seconds),
        "range_status": "metadata_only",
        "media_bytes_present": False,
        "serving_enabled": False,
        "confirmed_ranges": metadata_only_ranges,
        "sparse_segments": [],
        "updated_at": timestamp,
    }


def add_metadata_only_confirmed_range(
    ranges_metadata: Mapping[str, object],
    start_index: int,
    end_index_exclusive: int,
    *,
    segment_duration_seconds: float | None = None,
    source_session_id: str | None = None,
    source_epoch_id: str | None = None,
    updated_at: str | None = None,
) -> dict[str, object]:
    shared_output_key = validate_shared_output_key(str(ranges_metadata["shared_output_key"]))
    duration = _validate_segment_duration(
        segment_duration_seconds
        if segment_duration_seconds is not None
        else float(ranges_metadata["segment_duration_seconds"])
    )
    existing = list(ranges_metadata.get("confirmed_ranges") or [])
    existing.append((int(start_index), int(end_index_exclusive)))
    created_at = str(ranges_metadata.get("created_at") or ranges_metadata.get("updated_at") or utcnow_iso())
    return build_metadata_only_ranges_metadata(
        shared_output_key=shared_output_key,
        segment_duration_seconds=duration,
        confirmed_ranges=existing,
        source_session_id=source_session_id,
        source_epoch_id=source_epoch_id,
        created_at=created_at,
        updated_at=updated_at or utcnow_iso(),
    )


def count_shared_output_metadata_records(route2_root: Path) -> int:
    root = shared_output_store_root(route2_root)
    if not root.exists():
        return 0
    return sum(1 for child in root.iterdir() if child.is_dir() and (child / "metadata.json").exists())


def count_shared_output_init_records(route2_root: Path) -> int:
    root = shared_output_store_root(route2_root)
    if not root.exists():
        return 0
    return sum(
        1
        for child in root.iterdir()
        if child.is_dir()
        and (child / "init.mp4").is_file()
        and (child / "init.sha256").is_file()
    )


def count_shared_output_segment_records(route2_root: Path) -> int:
    root = shared_output_store_root(route2_root)
    if not root.exists():
        return 0
    count = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        segments = _read_json_mapping(child / "segments.json")
        if not segments:
            continue
        if list(segments.get("segments") or []):
            count += 1
    return count


def count_shared_output_ranges_media_bytes_present_records(route2_root: Path) -> int:
    root = shared_output_store_root(route2_root)
    if not root.exists():
        return 0
    count = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        ranges = _read_json_mapping(child / "ranges.json")
        if ranges and bool(ranges.get("media_bytes_present")):
            count += 1
    return count


def _shared_init_result(
    *,
    enabled: bool,
    attempted: bool,
    status: str,
    blockers: Iterable[str] = (),
    init_hash_sha256: str | None = None,
    init_size_bytes: int | None = None,
    path_present: bool = False,
    errors: Iterable[str] = (),
) -> dict[str, object]:
    return {
        "shared_init_write_enabled": bool(enabled),
        "shared_init_write_attempted": bool(attempted),
        "shared_init_write_status": status,
        "shared_init_write_blockers": sorted({str(item) for item in blockers}),
        "shared_init_hash_sha256": init_hash_sha256,
        "shared_init_size_bytes": init_size_bytes,
        "shared_init_path_present": bool(path_present),
        "shared_segments_writer_enabled": False,
        "shared_init_write_errors": [str(item) for item in errors],
    }


def _shared_segment_result(
    *,
    enabled: bool,
    attempted: bool,
    status: str,
    blockers: Iterable[str] = (),
    write_count: int = 0,
    already_present_count: int = 0,
    conflict_count: int = 0,
    last_index: int | None = None,
    last_hash: str | None = None,
    range_start_index: int | None = None,
    range_end_index_exclusive: int | None = None,
    media_bytes_present: bool = False,
    errors: Iterable[str] = (),
) -> dict[str, object]:
    return {
        "shared_segments_writer_enabled": bool(enabled),
        "shared_segment_write_attempted": bool(attempted),
        "shared_segment_write_status": status,
        "shared_segment_write_count": max(0, int(write_count)),
        "shared_segment_write_already_present_count": max(0, int(already_present_count)),
        "shared_segment_write_conflict_count": max(0, int(conflict_count)),
        "shared_segment_write_blockers": sorted({str(item) for item in blockers if str(item)}),
        "shared_segment_write_last_index": last_index,
        "shared_segment_write_last_hash": last_hash,
        "shared_segment_write_range_start_index": range_start_index,
        "shared_segment_write_range_end_index_exclusive": range_end_index_exclusive,
        "shared_output_media_bytes_present": bool(media_bytes_present),
        "shared_output_segment_write_errors": [str(item) for item in errors],
    }


def _update_metadata_for_shared_init(
    *,
    metadata_path: Path,
    init_hash_sha256: str,
    init_size_bytes: int,
    updated_at: str,
) -> None:
    metadata = _read_json_mapping(metadata_path) or {}
    metadata.update(
        {
            "init_status": "present",
            "init_hash_sha256": init_hash_sha256,
            "init_size_bytes": int(init_size_bytes),
            "init_written_at": updated_at,
            "serving_enabled": False,
            "shared_manifest_enabled": False,
            "media_bytes_present": False,
            "segment_writer_enabled": False,
            "writer_policy": "init_writer_only",
            "updated_at": updated_at,
        }
    )
    _write_json_atomic(metadata_path, metadata)


def _segment_record_payload(
    *,
    index: int,
    start_seconds: float,
    end_seconds: float,
    filename: str,
    size_bytes: int,
    sha256: str,
    writer_id: str | None,
    written_at: str,
) -> dict[str, object]:
    return {
        "index": int(index),
        "start_seconds": round(float(start_seconds), 6),
        "end_seconds": round(float(end_seconds), 6),
        "filename": str(filename),
        "size_bytes": int(size_bytes),
        "sha256": str(sha256),
        "written_at": written_at,
        "writer_id": str(writer_id or "").strip() or None,
    }


def _media_bytes_range_payload(
    start_index: int,
    end_index_exclusive: int,
    *,
    segment_duration_seconds: float,
    updated_at: str,
) -> dict[str, object]:
    payload = _range_payload(
        start_index,
        end_index_exclusive,
        segment_duration_seconds=segment_duration_seconds,
        source="shared_segment_writer",
    )
    payload.update(
        {
            "range_status": "media_bytes_present",
            "media_bytes_present": True,
            "updated_at": updated_at,
        }
    )
    return payload


def _build_media_bytes_ranges_metadata(
    *,
    shared_output_key: str,
    segment_duration_seconds: float,
    segment_indices: Iterable[int],
    updated_at: str,
) -> dict[str, object]:
    ranges = merge_contiguous_ranges(
        [(int(index), int(index) + 1) for index in segment_indices],
        segment_duration_seconds=segment_duration_seconds,
        source="shared_segment_writer",
    )
    confirmed_ranges = [
        _media_bytes_range_payload(
            int(item["start_index"]),
            int(item["end_index_exclusive"]),
            segment_duration_seconds=segment_duration_seconds,
            updated_at=updated_at,
        )
        for item in ranges
    ]
    return {
        "version": SHARED_OUTPUT_STORE_METADATA_VERSION,
        "shared_output_key": validate_shared_output_key(shared_output_key),
        "segment_duration_seconds": _validate_segment_duration(segment_duration_seconds),
        "range_status": "media_bytes_present" if confirmed_ranges else "empty",
        "media_bytes_present": bool(confirmed_ranges),
        "serving_enabled": False,
        "confirmed_ranges": confirmed_ranges,
        "sparse_segments": sorted({max(0, int(index)) for index in segment_indices}),
        "updated_at": updated_at,
    }


def _update_shared_segment_metadata(
    *,
    output_dir: Path,
    shared_output_key: str,
    segment_duration_seconds: float,
    segment_records: Mapping[int, Mapping[str, object]],
    updated_at: str,
) -> tuple[int | None, int | None]:
    ordered_records = [dict(segment_records[index]) for index in sorted(segment_records)]
    segments_payload = {
        "version": SHARED_OUTPUT_STORE_METADATA_VERSION,
        "shared_output_key": validate_shared_output_key(shared_output_key),
        "segment_duration_seconds": _validate_segment_duration(segment_duration_seconds),
        "serving_enabled": False,
        "segment_writer_enabled": True,
        "media_bytes_present": bool(ordered_records),
        "segments": ordered_records,
        "updated_at": updated_at,
    }
    _write_json_atomic(output_dir / "segments.json", segments_payload)
    ranges_payload = _build_media_bytes_ranges_metadata(
        shared_output_key=shared_output_key,
        segment_duration_seconds=segment_duration_seconds,
        segment_indices=segment_records.keys(),
        updated_at=updated_at,
    )
    _write_json_atomic(output_dir / "ranges.json", ranges_payload)
    metadata = _read_json_mapping(output_dir / "metadata.json") or {}
    metadata.update(
        {
            "ready_for_segments": True,
            "store_ready_for_segments": True,
            "writer_policy": "segment_writer_only",
            "segment_writer_enabled": True,
            "shared_manifest_enabled": False,
            "serving_enabled": False,
            "media_bytes_present": bool(ordered_records),
            "updated_at": updated_at,
        }
    )
    _write_json_atomic(output_dir / "metadata.json", metadata)
    if not ordered_records:
        return None, None
    indexes = [int(record["index"]) for record in ordered_records]
    ranges = merge_contiguous_ranges(
        [(index, index + 1) for index in indexes],
        segment_duration_seconds=segment_duration_seconds,
    )
    if not ranges:
        return None, None
    first_range = ranges[0]
    return int(first_range["start_index"]), int(first_range["end_index_exclusive"])


def write_shared_output_init_media(
    *,
    route2_root: Path,
    shared_output_key: str | None,
    source_init_path: Path | None,
    writer_enabled: bool,
    output_contract_fingerprint: str | None,
    metadata_ready: bool,
    contract_status: str | None,
    init_compatibility_status: str | None,
    expected_init_sha256: str | None = None,
    precondition_blockers: Iterable[str] = (),
    writer_id: str | None = None,
    updated_at: str | None = None,
) -> dict[str, object]:
    if not writer_enabled:
        return _shared_init_result(
            enabled=False,
            attempted=False,
            status="disabled",
            blockers=["init_writer_disabled"],
        )
    blockers = {str(item) for item in precondition_blockers if str(item)}
    if not shared_output_key:
        blockers.add("missing_shared_output_key")
    if not str(output_contract_fingerprint or "").strip():
        blockers.add("missing_output_contract")
    if not metadata_ready:
        blockers.add("shared_metadata_missing")
    if str(contract_status or "") == "conflict":
        blockers.add("shared_contract_conflict")
    normalized_init_status = str(init_compatibility_status or "").strip()
    if normalized_init_status == "mismatch":
        blockers.add("init_mismatch")
    elif normalized_init_status in {"pending", "pending_init", "unavailable"}:
        blockers.add("pending_init")
    elif normalized_init_status not in {"hash_available", "compatible_by_hash"}:
        blockers.add("missing_init_compatibility")
    source_path = Path(source_init_path) if source_init_path is not None else None
    if source_path is None or not source_path.is_file():
        blockers.add("init_missing")
    hard_blockers = blockers & {
        "missing_shared_output_key",
        "missing_output_contract",
        "output_contract_incomplete",
        "shared_metadata_missing",
        "shared_contract_conflict",
        "init_mismatch",
        "pending_init",
        "missing_init_compatibility",
        "init_missing",
        "provider_access_unavailable",
    }
    if hard_blockers:
        return _shared_init_result(
            enabled=True,
            attempted=False,
            status="conflict" if {"shared_contract_conflict", "init_mismatch"} & hard_blockers else "not_ready",
            blockers=blockers,
            path_present=bool(shared_output_key and (shared_output_directory(route2_root, shared_output_key) / "init.mp4").exists()),
        )

    timestamp = updated_at or utcnow_iso()
    writer_token = re.sub(r"[^A-Za-z0-9_.-]", "_", str(writer_id or uuid.uuid4().hex))[:80] or uuid.uuid4().hex
    try:
        sanitized_key = validate_shared_output_key(str(shared_output_key))
        output_dir = shared_output_directory(route2_root, sanitized_key)
        metadata_path = output_dir / "metadata.json"
        if not metadata_path.exists():
            return _shared_init_result(
                enabled=True,
                attempted=False,
                status="not_ready",
                blockers=sorted(blockers | {"shared_metadata_missing"}),
            )
        staging_dir = output_dir / "staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        staging_path = staging_dir / f"init.{writer_token}.tmp"
        final_init_path = output_dir / "init.mp4"
        final_sha_path = output_dir / "init.sha256"
        shutil.copyfile(source_path, staging_path)
        staged_hash, staged_size = _hash_file(staging_path)
        expected_hash = str(expected_init_sha256 or "").strip().lower()
        if expected_hash and expected_hash != staged_hash:
            staging_path.unlink(missing_ok=True)
            return _shared_init_result(
                enabled=True,
                attempted=True,
                status="failed",
                blockers=sorted(blockers | {"init_hash_mismatch_source"}),
                path_present=final_init_path.exists(),
            )

        existing_sha = _read_sha256_file(final_sha_path)
        if final_init_path.exists():
            final_hash, final_size = _hash_file(final_init_path)
            if existing_sha is not None and existing_sha != staged_hash:
                staging_path.unlink(missing_ok=True)
                return _shared_init_result(
                    enabled=True,
                    attempted=True,
                    status="conflict",
                    blockers=sorted(blockers | {"shared_init_hash_conflict"}),
                    init_hash_sha256=existing_sha,
                    init_size_bytes=final_size,
                    path_present=True,
                )
            if final_hash != staged_hash:
                staging_path.unlink(missing_ok=True)
                return _shared_init_result(
                    enabled=True,
                    attempted=True,
                    status="conflict",
                    blockers=sorted(blockers | {"shared_init_hash_conflict"}),
                    init_hash_sha256=final_hash,
                    init_size_bytes=final_size,
                    path_present=True,
                )
            if existing_sha is None:
                _write_text_atomic(final_sha_path, f"{staged_hash}\n")
            staging_path.unlink(missing_ok=True)
            _update_metadata_for_shared_init(
                metadata_path=metadata_path,
                init_hash_sha256=staged_hash,
                init_size_bytes=final_size,
                updated_at=timestamp,
            )
            return _shared_init_result(
                enabled=True,
                attempted=True,
                status="already_present",
                blockers=blockers,
                init_hash_sha256=staged_hash,
                init_size_bytes=final_size,
                path_present=True,
            )

        if existing_sha is not None and existing_sha != staged_hash:
            staging_path.unlink(missing_ok=True)
            return _shared_init_result(
                enabled=True,
                attempted=True,
                status="conflict",
                blockers=sorted(blockers | {"shared_init_hash_conflict"}),
                init_hash_sha256=existing_sha,
                path_present=False,
            )
        staging_path.rename(final_init_path)
        _write_text_atomic(final_sha_path, f"{staged_hash}\n")
        _update_metadata_for_shared_init(
            metadata_path=metadata_path,
            init_hash_sha256=staged_hash,
            init_size_bytes=staged_size,
            updated_at=timestamp,
        )
        return _shared_init_result(
            enabled=True,
            attempted=True,
            status="written",
            blockers=blockers,
            init_hash_sha256=staged_hash,
            init_size_bytes=staged_size,
            path_present=True,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            staging_path.unlink(missing_ok=True)  # type: ignore[name-defined]
        except Exception:  # noqa: BLE001
            pass
        return _shared_init_result(
            enabled=True,
            attempted=True,
            status="failed",
            blockers=sorted(blockers | {"shared_init_write_failed"}),
            errors=[f"shared_init_write_failed:{type(exc).__name__}"],
        )


def write_shared_output_segment_media(
    *,
    route2_root: Path,
    shared_output_key: str | None,
    segment_plans: Iterable[Mapping[str, object]],
    writer_enabled: bool,
    output_contract_fingerprint: str | None,
    metadata_ready: bool,
    contract_status: str | None,
    init_compatibility_status: str | None,
    segment_duration_seconds: float,
    precondition_blockers: Iterable[str] = (),
    writer_id: str | None = None,
    updated_at: str | None = None,
) -> dict[str, object]:
    if not writer_enabled:
        return _shared_segment_result(
            enabled=False,
            attempted=False,
            status="disabled",
            blockers=["segment_writer_disabled"],
        )

    timestamp = updated_at or utcnow_iso()
    blockers = {str(item) for item in precondition_blockers if str(item)}
    if not shared_output_key:
        blockers.add("missing_shared_output_key")
    if not str(output_contract_fingerprint or "").strip():
        blockers.add("missing_output_contract")
    if not metadata_ready:
        blockers.add("shared_metadata_missing")
    if str(contract_status or "") == "conflict":
        blockers.add("shared_contract_conflict")
    normalized_init_status = str(init_compatibility_status or "").strip()
    if normalized_init_status == "mismatch":
        blockers.add("init_mismatch")
    elif normalized_init_status in {"pending", "pending_init", "unavailable"}:
        blockers.add("pending_init_compatibility")
    elif normalized_init_status not in {"hash_available", "compatible_by_hash"}:
        blockers.add("missing_init_compatibility")

    writer_hard_blockers = {
        "missing_shared_output_key",
        "missing_output_contract",
        "output_contract_incomplete",
        "shared_metadata_missing",
        "shared_contract_conflict",
        "init_mismatch",
        "pending_init_compatibility",
        "missing_init_compatibility",
        "provider_access_unavailable",
        "permission_unverified",
        "permission_blocked",
        "stopped_or_expired_workload",
        "explicit_stop_requested",
    }
    soft_plan_blockers = {
        "no_shared_manifest",
        "serving_disabled",
        "media_bytes_not_present",
    }
    write_count = 0
    already_present_count = 0
    conflict_count = 0
    last_index: int | None = None
    last_hash: str | None = None
    range_start_index: int | None = None
    range_end_index_exclusive: int | None = None
    errors: list[str] = []
    attempted = False
    segment_records: dict[int, dict[str, object]] = {}

    try:
        if not shared_output_key:
            return _shared_segment_result(
                enabled=True,
                attempted=False,
                status="not_ready",
                blockers=blockers,
            )
        sanitized_key = validate_shared_output_key(str(shared_output_key))
        output_dir = shared_output_directory(route2_root, sanitized_key)
        metadata_path = output_dir / "metadata.json"
        if not metadata_path.exists():
            blockers.add("shared_metadata_missing")
        final_init_path = output_dir / "init.mp4"
        final_init_sha_path = output_dir / "init.sha256"
        if not final_init_path.is_file() or _read_sha256_file(final_init_sha_path) is None:
            blockers.add("shared_init_missing")

        existing_segments = _read_json_mapping(output_dir / "segments.json") or {}
        for item in existing_segments.get("segments") or []:
            if not isinstance(item, Mapping):
                continue
            try:
                segment_index = int(item["index"])
            except (KeyError, TypeError, ValueError):
                continue
            segment_records[segment_index] = dict(item)

        segment_writer_hard_blockers = blockers & (writer_hard_blockers | {"shared_init_missing"})
        if segment_writer_hard_blockers:
            return _shared_segment_result(
                enabled=True,
                attempted=False,
                status="conflict"
                if {"shared_contract_conflict", "init_mismatch"} & segment_writer_hard_blockers
                else "not_ready",
                blockers=blockers,
                media_bytes_present=bool(segment_records),
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        segments_dir = output_dir / "segments"
        staging_dir = output_dir / "staging"
        segments_dir.mkdir(exist_ok=True)
        staging_dir.mkdir(exist_ok=True)
        writer_token = re.sub(r"[^A-Za-z0-9_.-]", "_", str(writer_id or uuid.uuid4().hex))[:80] or uuid.uuid4().hex

        candidate_plans = list(segment_plans)
        if not candidate_plans:
            blockers.add("no_published_segments")
            return _shared_segment_result(
                enabled=True,
                attempted=False,
                status="not_ready",
                blockers=blockers,
                media_bytes_present=bool(segment_records),
            )

        for plan in candidate_plans:
            plan_blockers = {str(item) for item in plan.get("shared_store_write_blockers") or []}
            hard_plan_blockers = plan_blockers - soft_plan_blockers
            source_segment_path = plan.get("source_segment_path")
            source_path = Path(str(source_segment_path)) if source_segment_path else None
            if source_path is None or not source_path.is_file():
                hard_plan_blockers.add("published_segment_missing")
            absolute_index = int(plan["absolute_segment_index_candidate"])
            if hard_plan_blockers:
                blockers.update(hard_plan_blockers)
                continue

            attempted = True
            expected_filename = str(plan.get("expected_shared_segment_filename") or shared_segment_filename(absolute_index))
            if expected_filename != shared_segment_filename(absolute_index):
                blockers.add("segment_filename_mismatch")
                continue
            final_segment_path = segments_dir / expected_filename
            staging_path = staging_dir / f"{expected_filename}.{writer_token}.tmp"
            try:
                shutil.copyfile(source_path, staging_path)
                staged_hash, staged_size = _hash_file(staging_path)
                if final_segment_path.exists():
                    if final_segment_path.is_symlink() or not final_segment_path.is_file():
                        blockers.add("shared_segment_path_not_regular")
                        conflict_count += 1
                        staging_path.unlink(missing_ok=True)
                        continue
                    final_hash, final_size = _hash_file(final_segment_path)
                    if final_hash != staged_hash:
                        blockers.add("segment_hash_conflict")
                        conflict_count += 1
                        staging_path.unlink(missing_ok=True)
                        continue
                    staging_path.unlink(missing_ok=True)
                    already_present_count += 1
                    segment_records[absolute_index] = _segment_record_payload(
                        index=absolute_index,
                        start_seconds=float(plan["absolute_start_seconds"]),
                        end_seconds=float(plan["absolute_end_seconds"]),
                        filename=expected_filename,
                        size_bytes=final_size,
                        sha256=final_hash,
                        writer_id=writer_id,
                        written_at=str(segment_records.get(absolute_index, {}).get("written_at") or timestamp),
                    )
                    last_index = absolute_index
                    last_hash = final_hash
                    continue
                staging_path.rename(final_segment_path)
                if final_segment_path.is_symlink() or not final_segment_path.is_file():
                    blockers.add("shared_segment_path_not_regular")
                    conflict_count += 1
                    final_segment_path.unlink(missing_ok=True)
                    continue
                segment_records[absolute_index] = _segment_record_payload(
                    index=absolute_index,
                    start_seconds=float(plan["absolute_start_seconds"]),
                    end_seconds=float(plan["absolute_end_seconds"]),
                    filename=expected_filename,
                    size_bytes=staged_size,
                    sha256=staged_hash,
                    writer_id=writer_id,
                    written_at=timestamp,
                )
                write_count += 1
                last_index = absolute_index
                last_hash = staged_hash
            except Exception as exc:  # noqa: BLE001
                errors.append(f"shared_segment_write_failed:{type(exc).__name__}")
                blockers.add("shared_segment_write_failed")
                try:
                    staging_path.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
                continue

        if segment_records:
            range_start_index, range_end_index_exclusive = _update_shared_segment_metadata(
                output_dir=output_dir,
                shared_output_key=sanitized_key,
                segment_duration_seconds=segment_duration_seconds,
                segment_records=segment_records,
                updated_at=timestamp,
            )

        if conflict_count:
            status = "conflict"
        elif errors and write_count == 0 and already_present_count == 0:
            status = "failed"
        elif write_count > 0:
            status = "written"
        elif already_present_count > 0:
            status = "already_present"
        else:
            status = "not_ready"
        return _shared_segment_result(
            enabled=True,
            attempted=attempted,
            status=status,
            blockers=blockers,
            write_count=write_count,
            already_present_count=already_present_count,
            conflict_count=conflict_count,
            last_index=last_index,
            last_hash=last_hash,
            range_start_index=range_start_index,
            range_end_index_exclusive=range_end_index_exclusive,
            media_bytes_present=bool(segment_records),
            errors=errors,
        )
    except Exception as exc:  # noqa: BLE001
        return _shared_segment_result(
            enabled=True,
            attempted=True,
            status="failed",
            blockers=sorted(blockers | {"shared_segment_write_failed"}),
            errors=[f"shared_segment_write_failed:{type(exc).__name__}"],
        )


def write_shared_output_store_metadata(
    *,
    route2_root: Path,
    contract_metadata: Mapping[str, object],
    metadata: Mapping[str, object],
    candidate_range: Mapping[str, object] | None = None,
    source_session_id: str | None = None,
    source_epoch_id: str | None = None,
    updated_at: str | None = None,
) -> dict[str, object]:
    blockers: set[str] = set(SHARED_OUTPUT_STORE_BLOCKERS)
    errors: list[str] = []
    range_count = 0
    contract_status = "skipped"
    metadata_status = "skipped"
    ranges_status = "skipped"
    metadata_written = False
    media_bytes_present = False
    shared_output_key = validate_shared_output_key(str(contract_metadata["shared_output_key"]))
    output_dir = shared_output_directory(route2_root, shared_output_key)
    contract_payload = dict(contract_metadata)
    metadata_payload = dict(metadata)
    timestamp = updated_at or utcnow_iso()
    contract_payload["updated_at"] = timestamp
    metadata_payload["updated_at"] = timestamp
    contract_path = output_dir / "contract.json"
    metadata_path = output_dir / "metadata.json"
    ranges_path = output_dir / "ranges.json"
    segments_path = output_dir / "segments.json"

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "leases").mkdir(exist_ok=True)
        (output_dir / "staging").mkdir(exist_ok=True)
        existing_segments = _read_json_mapping(segments_path)
        if existing_segments is not None and list(existing_segments.get("segments") or []):
            media_bytes_present = True
            metadata_payload.update(
                {
                    "media_bytes_present": True,
                    "writer_policy": "segment_writer_only",
                    "ready_for_segments": True,
                    "store_ready_for_segments": True,
                }
            )

        existing_contract = _read_json_mapping(contract_path)
        if existing_contract is not None and _contract_conflict_view(existing_contract) != _contract_conflict_view(
            contract_payload
        ):
            blockers.add("shared_contract_conflict")
            contract_status = "conflict"
        else:
            if existing_contract is None:
                contract_status = "written"
            else:
                contract_status = "unchanged" if existing_contract == contract_payload else "updated"
            _write_json_atomic(contract_path, contract_payload)
            metadata_status = "written" if not metadata_path.exists() else "updated"
            _write_json_atomic(metadata_path, metadata_payload)
            metadata_written = True

            existing_ranges = _read_json_mapping(ranges_path)
            if existing_ranges is not None and bool(existing_ranges.get("media_bytes_present")):
                ranges_payload = dict(existing_ranges)
                ranges_payload["updated_at"] = timestamp
                ranges_status = "media_bytes_preserved"
            elif existing_ranges is None:
                ranges_payload = build_metadata_only_ranges_metadata(
                    shared_output_key=shared_output_key,
                    segment_duration_seconds=float(metadata_payload["segment_duration_seconds"]),
                    confirmed_ranges=[],
                    updated_at=timestamp,
                )
            else:
                ranges_payload = dict(existing_ranges)
            if candidate_range is not None:
                ranges_payload = add_metadata_only_confirmed_range(
                    ranges_payload,
                    int(candidate_range["start_index"]),
                    int(candidate_range["end_index_exclusive"]),
                    segment_duration_seconds=float(metadata_payload["segment_duration_seconds"]),
                    source_session_id=source_session_id,
                    source_epoch_id=source_epoch_id,
                    updated_at=timestamp,
                )
            else:
                ranges_payload["updated_at"] = timestamp
                ranges_payload["range_status"] = "metadata_only"
                ranges_payload["media_bytes_present"] = False
                ranges_payload["serving_enabled"] = False
            _write_json_atomic(ranges_path, ranges_payload)
            if ranges_status != "media_bytes_preserved":
                ranges_status = "metadata_only_updated" if existing_ranges is not None else "metadata_only_written"
            range_count = len(ranges_payload.get("confirmed_ranges") or [])
            media_bytes_present = media_bytes_present or bool(ranges_payload.get("media_bytes_present"))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"shared_output_metadata_write_failed:{type(exc).__name__}")
        blockers.add("shared_output_metadata_write_failed")
        metadata_written = False

    return {
        "shared_output_metadata_written": metadata_written,
        "shared_output_contract_status": contract_status,
        "shared_output_metadata_status": metadata_status,
        "shared_output_ranges_status": ranges_status,
        "shared_output_range_count": range_count,
        "shared_output_media_bytes_present": media_bytes_present,
        "shared_output_store_blockers": _ordered_store_blockers(blockers),
        "shared_output_metadata_write_errors": errors,
    }


def build_shared_output_lease_metadata(
    *,
    lease_id: str,
    shared_output_key: str,
    session_id: str,
    user_id: int,
    media_item_id: int,
    purpose: str,
    start_index: int,
    end_index_exclusive: int,
    created_at: str,
    expires_at: str,
    heartbeat_at: str,
    status: str,
) -> dict[str, object]:
    if purpose not in {"reader", "writer", "future"}:
        raise ValueError("Unsupported shared output lease purpose")
    start = int(start_index)
    end = int(end_index_exclusive)
    if start < 0 or end <= start:
        raise ValueError("Lease range indexes are invalid")
    return {
        "version": SHARED_OUTPUT_STORE_METADATA_VERSION,
        "lease_id": str(lease_id or "").strip(),
        "shared_output_key": validate_shared_output_key(shared_output_key),
        "session_id": str(session_id or "").strip(),
        "user_id": int(user_id),
        "media_item_id": int(media_item_id),
        "purpose": purpose,
        "start_index": start,
        "end_index_exclusive": end,
        "created_at": str(created_at or "").strip(),
        "expires_at": str(expires_at or "").strip(),
        "heartbeat_at": str(heartbeat_at or "").strip(),
        "status": str(status or "").strip(),
    }


def validate_shared_output_lease_metadata(payload: Mapping[str, object]) -> dict[str, object]:
    required_fields = {
        "lease_id",
        "shared_output_key",
        "session_id",
        "user_id",
        "media_item_id",
        "purpose",
        "start_index",
        "end_index_exclusive",
        "created_at",
        "expires_at",
        "heartbeat_at",
        "status",
    }
    missing = sorted(field for field in required_fields if field not in payload)
    if missing:
        raise ValueError(f"Shared output lease metadata is missing required fields: {', '.join(missing)}")
    return build_shared_output_lease_metadata(
        lease_id=str(payload["lease_id"]),
        shared_output_key=str(payload["shared_output_key"]),
        session_id=str(payload["session_id"]),
        user_id=int(payload["user_id"]),
        media_item_id=int(payload["media_item_id"]),
        purpose=str(payload["purpose"]),
        start_index=int(payload["start_index"]),
        end_index_exclusive=int(payload["end_index_exclusive"]),
        created_at=str(payload["created_at"]),
        expires_at=str(payload["expires_at"]),
        heartbeat_at=str(payload["heartbeat_at"]),
        status=str(payload["status"]),
    )


def build_shared_output_store_capability(route2_root: Path) -> dict[str, object]:
    return {
        "shared_output_store_enabled": SHARED_OUTPUT_STORE_STATUS,
        "shared_output_root": str(shared_output_store_root(route2_root)),
        "shared_output_metadata_version": SHARED_OUTPUT_STORE_METADATA_VERSION,
        "shared_output_store_ready_for_segments": False,
    }
