from __future__ import annotations

import json
import uuid
from pathlib import Path

from ..db import utcnow_iso
from .mobile_playback_models import (
    SEGMENT_DURATION_SECONDS,
    PlaybackEpoch,
)


def _write_route2_epoch_metadata_locked(
    epoch: PlaybackEpoch,
    *,
    write_json_atomic,
) -> None:
    write_json_atomic(
        epoch.metadata_path,
        {
            "epoch_id": epoch.epoch_id,
            "session_id": epoch.session_id,
            "created_at": epoch.created_at,
            "state": epoch.state,
            "target_position_seconds": round(epoch.target_position_seconds, 2),
            "epoch_start_seconds": round(epoch.epoch_start_seconds, 2),
            "attach_position_seconds": round(epoch.attach_position_seconds, 2),
            "segment_duration_seconds": SEGMENT_DURATION_SECONDS,
            "staging_dir": str(epoch.staging_dir),
            "published_dir": str(epoch.published_dir),
            "published_total_bytes": epoch.published_total_bytes,
            "publish_segment_count": epoch.publish_segment_count,
            "publish_init_latency_seconds": epoch.publish_init_latency_seconds,
            "last_publish_latency_seconds": epoch.last_publish_latency_seconds,
            "publish_latency_avg_seconds": (
                epoch.publish_latency_total_seconds / epoch.publish_segment_count
                if epoch.publish_segment_count > 0
                else None
            ),
            "publish_latency_max_seconds": epoch.publish_latency_max_seconds,
            "last_publish_kind": epoch.last_publish_kind,
            "transcoder_completed": epoch.transcoder_completed,
            "active_worker_id": epoch.active_worker_id,
            "drain_started_at_ts": epoch.drain_started_at_ts,
            "drain_target_attach_revision": epoch.drain_target_attach_revision,
            "last_media_access_at_ts": epoch.last_media_access_at_ts,
            "last_error": epoch.last_error,
            "updated_at": utcnow_iso(),
        },
    )


def _write_route2_frontier_locked(
    epoch: PlaybackEpoch,
    *,
    write_json_atomic,
    compress_ranges,
) -> None:
    published_end_seconds = 0.0
    if epoch.init_published and epoch.contiguous_published_through_segment is not None:
        published_end_seconds = epoch.epoch_start_seconds + (
            (epoch.contiguous_published_through_segment + 1) * SEGMENT_DURATION_SECONDS
        )
    write_json_atomic(
        epoch.frontier_path,
        {
            "epoch_id": epoch.epoch_id,
            "state": epoch.state,
            "segment_duration_seconds": SEGMENT_DURATION_SECONDS,
            "init_published": epoch.init_published,
            "published_ranges": compress_ranges(epoch.published_segments),
            "contiguous_published_through_segment": epoch.contiguous_published_through_segment,
            "published_total_bytes": epoch.published_total_bytes,
            "publish_segment_count": epoch.publish_segment_count,
            "last_publish_latency_seconds": epoch.last_publish_latency_seconds,
            "publish_latency_max_seconds": epoch.publish_latency_max_seconds,
            "published_ready_start_seconds": round(epoch.epoch_start_seconds, 2)
            if epoch.init_published and epoch.contiguous_published_through_segment is not None
            else 0.0,
            "published_ready_end_seconds": round(published_end_seconds, 2),
            "transcoder_completed": epoch.transcoder_completed,
            "last_published_at": epoch.last_published_at,
            "last_error": epoch.last_error,
            "updated_at": utcnow_iso(),
        },
    )


def _write_json_atomic(destination: Path, payload: dict[str, object]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(destination)


def _rebuild_route2_published_frontier_locked(
    epoch: PlaybackEpoch,
    *,
    contiguous_segment_frontier,
    record_route2_byte_sample_locked,
    write_route2_frontier_locked,
) -> None:
    epoch.published_dir.mkdir(parents=True, exist_ok=True)
    epoch.init_published = epoch.published_init_path.exists()
    epoch.published_init_bytes = epoch.published_init_path.stat().st_size if epoch.init_published else 0
    published_segments: set[int] = set()
    published_segment_bytes: dict[int, int] = {}
    for child in epoch.published_dir.glob("segment_*.m4s"):
        token = child.stem.removeprefix("segment_")
        try:
            segment_index = int(token)
        except ValueError:
            continue
        published_segments.add(segment_index)
        published_segment_bytes[segment_index] = child.stat().st_size
    epoch.published_segments = published_segments
    epoch.published_segment_bytes = published_segment_bytes
    epoch.published_total_bytes = epoch.published_init_bytes + sum(published_segment_bytes.values())
    epoch.contiguous_published_through_segment = (
        contiguous_segment_frontier(published_segments) if epoch.init_published else None
    )
    if epoch.init_published or epoch.published_segments:
        epoch.last_published_at = utcnow_iso()
    record_route2_byte_sample_locked(epoch)
    write_route2_frontier_locked(epoch)


def _contiguous_segment_frontier(published_segments: set[int]) -> int | None:
    if 0 not in published_segments:
        return None
    frontier = 0
    while frontier + 1 in published_segments:
        frontier += 1
    return frontier


def _route2_segment_destination(epoch: PlaybackEpoch, segment_index: int) -> Path:
    return epoch.published_dir / f"segment_{segment_index:06d}.m4s"
