from __future__ import annotations

import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from ..mobile_playback_models import SEGMENT_DURATION_SECONDS
from .event_normalization import ffmpeg_command_fingerprint, ffmpeg_command_shape
from .runtime import observe_runtime_event, record_runtime_health


_progress_lock = threading.RLock()
_progress_last_sample: OrderedDict[str, float] = OrderedDict()
_PROGRESS_SAMPLE_INTERVAL_SECONDS = 1.0
_PROGRESS_MAX_KEYS = 4_096


def route2_frontier_ms(
    frontier_segment: int | None,
    *,
    segment_duration_seconds: float = SEGMENT_DURATION_SECONDS,
) -> int:
    if frontier_segment is None:
        return 0
    duration = max(0.001, float(segment_duration_seconds))
    return round(max(0, int(frontier_segment) + 1) * duration * 1_000)


def observe_ffmpeg_process_spawned(
    *,
    playback_session_id: str,
    command: list[str] | tuple[str, ...],
    pid: int,
    worker_id: str | None,
    epoch_id: str | None,
    selected_threads: int | None,
) -> None:
    try:
        observe_runtime_event(
            "ffmpeg_process_spawned",
            playback_session_id=playback_session_id,
            event_source="ffmpeg",
            observation_kind="measured_server",
            priority="high",
            worker_id=worker_id,
            epoch_id=epoch_id,
            payload={
                "diagnostics_command_shape": ffmpeg_command_shape(command),
                "process": {"pid": max(0, int(pid))},
                "selected_threads": selected_threads,
            },
        )
    except Exception:  # noqa: BLE001 - diagnostics cannot alter process startup.
        record_runtime_health("ffmpeg_observer", "process_spawn_capture_failed")
        return


def observe_ffmpeg_process_completed(
    *,
    playback_session_id: str,
    worker_id: str | None,
    epoch_id: str | None,
    return_code: int,
) -> None:
    try:
        observe_runtime_event(
            "ffmpeg_process_completed",
            playback_session_id=playback_session_id,
            event_source="ffmpeg",
            observation_kind="measured_server",
            priority="high",
            severity="error" if return_code else "info",
            worker_id=worker_id,
            epoch_id=epoch_id,
            payload={
                "exit_code": int(return_code),
                "success": int(return_code) == 0,
            },
        )
    except Exception:  # noqa: BLE001 - diagnostics cannot alter process completion.
        record_runtime_health("ffmpeg_observer", "process_complete_capture_failed")
        return


def observe_ffmpeg_progress_file(
    *,
    playback_session_id: str,
    epoch_id: str,
    worker_id: str | None,
    progress_path: Path,
    reader: Callable[..., Any],
) -> None:
    """Sample the existing FFmpeg progress file at most once per second."""

    try:
        sample_key = f"{playback_session_id}:{epoch_id}"
        now = time.monotonic()
        if not _progress_lock.acquire(blocking=False):
            return
        try:
            previous = _progress_last_sample.get(sample_key)
            if previous is not None and now - previous < _PROGRESS_SAMPLE_INTERVAL_SECONDS:
                return
            _progress_last_sample[sample_key] = now
            _progress_last_sample.move_to_end(sample_key)
            while len(_progress_last_sample) > _PROGRESS_MAX_KEYS:
                _progress_last_sample.popitem(last=False)
        finally:
            _progress_lock.release()
        snapshot = reader(progress_path)
        observe_runtime_event(
            "ffmpeg_progress_sample",
            playback_session_id=playback_session_id,
            event_source="ffmpeg",
            observation_kind="measured_server",
            worker_id=worker_id,
            epoch_id=epoch_id,
            sample_window_ms=_PROGRESS_SAMPLE_INTERVAL_SECONDS * 1_000,
            payload={
                "out_time_ms": (
                    float(snapshot.out_time_seconds) * 1_000
                    if snapshot.out_time_seconds is not None
                    else None
                ),
                "fps": snapshot.fps,
                "speed_x": snapshot.speed_x,
                "frame_count": snapshot.frame,
                "progress": snapshot.progress_state,
                "missing_signals": list(snapshot.missing_metrics),
            },
        )
    except Exception:  # noqa: BLE001 - diagnostics cannot alter publication.
        record_runtime_health("ffmpeg_observer", "progress_capture_failed")
        return


def observe_segment_publication(
    *,
    playback_session_id: str,
    epoch_id: str,
    segment_kind: str,
    segment_index: int | None,
    segment_bytes: int | None,
    publish_latency_seconds: float,
    frontier_segment: int | None,
) -> None:
    try:
        observe_runtime_event(
            "segment_published",
            playback_session_id=playback_session_id,
            event_source="ffmpeg",
            observation_kind="measured_server",
            epoch_id=epoch_id,
            payload={
                "type": segment_kind,
                "segment_index": segment_index,
                "segment_bytes": segment_bytes,
                "publish_latency_ms": max(0.0, float(publish_latency_seconds) * 1_000),
                "frontier_ms": route2_frontier_ms(frontier_segment),
                "segment_duration_ms": SEGMENT_DURATION_SECONDS * 1_000,
            },
        )
    except Exception:  # noqa: BLE001 - diagnostics cannot alter publication.
        record_runtime_health("ffmpeg_observer", "segment_capture_failed")
        return
