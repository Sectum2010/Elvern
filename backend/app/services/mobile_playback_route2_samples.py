from __future__ import annotations

import time

from .mobile_playback_models import (
    ROUTE2_FULL_GOODPUT_WINDOW_SECONDS,
    ROUTE2_FULL_PROBE_MAX_DURATION_SECONDS,
    ROUTE2_FULL_PROBE_MIN_DURATION_SECONDS,
    ROUTE2_SUPPLY_RATE_WINDOW_SECONDS,
    MobilePlaybackSession,
    PlaybackEpoch,
    SEGMENT_DURATION_SECONDS,
)


def _route2_epoch_ready_end_seconds(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
) -> float:
    if not epoch.init_published or epoch.contiguous_published_through_segment is None:
        return 0.0
    return min(
        session.duration_seconds,
        epoch.epoch_start_seconds + ((epoch.contiguous_published_through_segment + 1) * SEGMENT_DURATION_SECONDS),
    )


def _record_route2_frontier_sample_locked(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
    *,
    route2_epoch_ready_end_seconds_locked,
    now_ts: float | None = None,
) -> None:
    now_ts = now_ts or time.time()
    published_end_seconds = round(route2_epoch_ready_end_seconds_locked(session, epoch), 3)
    history = epoch.frontier_samples
    if (
        not history
        or published_end_seconds > history[-1][1] + 0.001
        or now_ts - history[-1][0] >= 1.0
    ):
        history.append((now_ts, published_end_seconds))
    cutoff_ts = now_ts - ROUTE2_SUPPLY_RATE_WINDOW_SECONDS
    while len(history) > 1 and history[0][0] < cutoff_ts:
        history.pop(0)


def _record_route2_byte_sample_locked(
    epoch: PlaybackEpoch,
    *,
    now_ts: float | None = None,
) -> None:
    now_ts = now_ts or time.time()
    total_bytes = max(0, int(epoch.published_total_bytes))
    history = epoch.byte_samples
    if (
        not history
        or total_bytes > history[-1][1]
        or now_ts - history[-1][0] >= 1.0
    ):
        history.append((now_ts, total_bytes))
    cutoff_ts = now_ts - ROUTE2_FULL_GOODPUT_WINDOW_SECONDS
    while len(history) > 1 and history[0][0] < cutoff_ts:
        history.pop(0)


def _record_route2_client_probe_sample_locked(
    session: MobilePlaybackSession,
    *,
    probe_bytes: int | None,
    probe_duration_ms: int | None,
    now_ts: float | None = None,
) -> None:
    if probe_bytes is None or probe_duration_ms is None:
        return
    duration_seconds = max(0.0, float(probe_duration_ms) / 1000.0)
    if (
        probe_bytes <= 0
        or duration_seconds < ROUTE2_FULL_PROBE_MIN_DURATION_SECONDS
        or duration_seconds > ROUTE2_FULL_PROBE_MAX_DURATION_SECONDS
    ):
        return
    now_ts = now_ts or time.time()
    browser_session = session.browser_playback
    browser_session.client_probe_samples.append((now_ts, int(probe_bytes), duration_seconds))
    cutoff_ts = now_ts - ROUTE2_FULL_GOODPUT_WINDOW_SECONDS
    while (
        len(browser_session.client_probe_samples) > 1
        and browser_session.client_probe_samples[0][0] < cutoff_ts
    ):
        browser_session.client_probe_samples.pop(0)
