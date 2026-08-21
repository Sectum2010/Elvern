from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from ..mobile_playback_models import SEGMENT_DURATION_SECONDS
from .runtime import observe_runtime_event


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


def ffmpeg_command_fingerprint(command: list[str] | tuple[str, ...]) -> str:
    """Hash command shape while excluding media paths, URLs, and secret values."""

    normalized: list[str] = []
    redact_next = False
    for index, raw in enumerate(command):
        token = str(raw)
        if redact_next:
            normalized.append("<redacted-value>")
            redact_next = False
            continue
        if token in {"-i", "-headers", "-http_proxy", "-cookies"}:
            normalized.append(token)
            redact_next = True
            continue
        split = urlsplit(token)
        if split.scheme in {"http", "https"}:
            normalized.append("<url>")
        elif os.path.isabs(token) or token.startswith(("~/", "./", "../")):
            normalized.append(f"<path:{Path(token).suffix.lower() or 'none'}>")
        elif index == 0:
            normalized.append(Path(token).name)
        else:
            normalized.append(token[:128])
    encoded = json.dumps(normalized, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
                "command_fingerprint": ffmpeg_command_fingerprint(command),
                "process": {"pid": max(0, int(pid))},
                "selected_threads": selected_threads,
            },
        )
    except Exception:  # noqa: BLE001 - diagnostics cannot alter process startup.
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
        with _progress_lock:
            previous = _progress_last_sample.get(sample_key)
            if previous is not None and now - previous < _PROGRESS_SAMPLE_INTERVAL_SECONDS:
                return
            _progress_last_sample[sample_key] = now
            _progress_last_sample.move_to_end(sample_key)
            while len(_progress_last_sample) > _PROGRESS_MAX_KEYS:
                _progress_last_sample.popitem(last=False)
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
        return
