from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from ..db import utcnow_iso


SHARED_OUTPUT_STORE_METADATA_VERSION = "route2-shared-output-store-v1"
SHARED_OUTPUT_STORE_STATUS = "metadata_only"
SHARED_OUTPUT_STORE_BLOCKERS = [
    "metadata_only",
    "no_global_segment_store",
    "no_segment_writer",
    "no_shared_manifest",
]

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
    status: str = SHARED_OUTPUT_STORE_STATUS,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict[str, object]:
    return {
        "version": SHARED_OUTPUT_STORE_METADATA_VERSION,
        "shared_output_key": validate_shared_output_key(shared_output_key),
        "status": str(status or SHARED_OUTPUT_STORE_STATUS),
        "store_ready_for_segments": False,
        "segment_writer_enabled": False,
        "shared_manifest_enabled": False,
        "created_at": created_at or utcnow_iso(),
        "updated_at": updated_at or utcnow_iso(),
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
