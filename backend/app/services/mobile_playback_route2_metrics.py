from __future__ import annotations

import statistics

from .mobile_playback_models import (
    READY_AFTER_TARGET_SECONDS,
    ROUTE2_ETA_DISPLAY_MAX_VOLATILITY_RATIO,
    ROUTE2_ETA_DISPLAY_MIN_GROWTH_EVENTS,
    ROUTE2_ETA_DISPLAY_MIN_OBSERVATION_SECONDS,
    ROUTE2_ETA_DISPLAY_STICKY_OBSERVATION_SECONDS,
    ROUTE2_FULL_PROBE_MIN_DURATION_SECONDS,
    ROUTE2_SUPPLY_RATE_FAST_EMA_ALPHA,
    ROUTE2_SUPPLY_RATE_SLOW_EMA_ALPHA,
    MobilePlaybackSession,
    PlaybackEpoch,
)
from .mobile_playback_route2_math import (
    _conservative_goodput_locked,
    _ema_locked,
)


def _route2_server_byte_goodput_locked(
    epoch: PlaybackEpoch,
    *,
    conservative_goodput_locked,
) -> dict[str, float | int | bool]:
    history = epoch.byte_samples
    if len(history) < 2:
        return conservative_goodput_locked([], observation_seconds=0.0)
    first_ts = history[0][0]
    last_ts = history[-1][0]
    observation_seconds = max(0.0, last_ts - first_ts)
    interval_rates: list[float] = []
    for (previous_ts, previous_bytes), (current_ts, current_bytes) in zip(history, history[1:]):
        interval_seconds = max(0.0, current_ts - previous_ts)
        interval_bytes = max(0, current_bytes - previous_bytes)
        if interval_seconds < 0.25 or interval_bytes <= 0:
            continue
        interval_rates.append(interval_bytes / interval_seconds)
    return conservative_goodput_locked(interval_rates, observation_seconds=observation_seconds)


def _route2_client_goodput_locked(
    session: MobilePlaybackSession,
    *,
    conservative_goodput_locked,
) -> dict[str, float | int | bool]:
    samples = session.browser_playback.client_probe_samples
    if not samples:
        return conservative_goodput_locked([], observation_seconds=0.0)
    observation_seconds = max(0.0, samples[-1][0] - samples[0][0]) if len(samples) >= 2 else 0.0
    rates = [
        bytes_transferred / duration_seconds
        for _ts, bytes_transferred, duration_seconds in samples
        if bytes_transferred > 0 and duration_seconds >= ROUTE2_FULL_PROBE_MIN_DURATION_SECONDS
    ]
    return conservative_goodput_locked(rates, observation_seconds=observation_seconds)


def _route2_supply_rate_x_locked(epoch: PlaybackEpoch) -> tuple[float, float]:
    if len(epoch.frontier_samples) < 2:
        return 0.0, 0.0
    first_ts, first_end_seconds = epoch.frontier_samples[0]
    last_ts, last_end_seconds = epoch.frontier_samples[-1]
    observation_seconds = max(0.0, last_ts - first_ts)
    if observation_seconds < 0.25:
        return 0.0, observation_seconds
    supply_rate_x = max(0.0, (last_end_seconds - first_end_seconds) / observation_seconds)
    return supply_rate_x, observation_seconds


def _route2_supply_model_locked(epoch: PlaybackEpoch) -> dict[str, float | int | bool]:
    overall_rate_x, observation_seconds = _route2_supply_rate_x_locked(epoch)
    positive_interval_rates: list[float] = []
    history = epoch.frontier_samples
    for (first_ts, first_end_seconds), (last_ts, last_end_seconds) in zip(history, history[1:]):
        interval_seconds = max(0.0, last_ts - first_ts)
        frontier_growth_seconds = max(0.0, last_end_seconds - first_end_seconds)
        if interval_seconds < 0.25 or frontier_growth_seconds <= 0.001:
            continue
        positive_interval_rates.append(frontier_growth_seconds / interval_seconds)
    if positive_interval_rates:
        fast_ema_rate_x = _ema_locked(
            positive_interval_rates,
            alpha=ROUTE2_SUPPLY_RATE_FAST_EMA_ALPHA,
        )
        slow_ema_rate_x = _ema_locked(
            positive_interval_rates,
            alpha=ROUTE2_SUPPLY_RATE_SLOW_EMA_ALPHA,
        )
        median_rate_x = max(0.0, float(statistics.median(positive_interval_rates)))
    else:
        fast_ema_rate_x = overall_rate_x
        slow_ema_rate_x = overall_rate_x
        median_rate_x = overall_rate_x
    rate_candidates = [rate for rate in (overall_rate_x, fast_ema_rate_x, slow_ema_rate_x, median_rate_x) if rate > 0.0]
    effective_rate_x = min(rate_candidates) if rate_candidates else 0.0
    volatility_ratio = (
        (max(rate_candidates) - min(rate_candidates)) / max(max(rate_candidates), 0.001)
        if len(rate_candidates) >= 2
        else 0.0
    )
    positive_growth_events = len(positive_interval_rates)
    display_confident = (
        effective_rate_x > 0.0
        and (
            (
                observation_seconds >= ROUTE2_ETA_DISPLAY_MIN_OBSERVATION_SECONDS
                and positive_growth_events >= ROUTE2_ETA_DISPLAY_MIN_GROWTH_EVENTS
                and volatility_ratio <= ROUTE2_ETA_DISPLAY_MAX_VOLATILITY_RATIO
            )
            or (
                observation_seconds >= ROUTE2_ETA_DISPLAY_STICKY_OBSERVATION_SECONDS
                and positive_growth_events >= ROUTE2_ETA_DISPLAY_MIN_GROWTH_EVENTS + 1
            )
        )
    )
    return {
        "effective_rate_x": effective_rate_x,
        "overall_rate_x": overall_rate_x,
        "fast_ema_rate_x": fast_ema_rate_x,
        "slow_ema_rate_x": slow_ema_rate_x,
        "median_rate_x": median_rate_x,
        "observation_seconds": observation_seconds,
        "positive_growth_events": positive_growth_events,
        "volatility_ratio": volatility_ratio,
        "display_confident": display_confident,
    }


def _route2_effective_playhead_seconds_locked(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
    *,
    clamp_time,
) -> float:
    return clamp_time(
        max(
            epoch.attach_position_seconds,
            session.target_position_seconds,
            session.last_stable_position_seconds,
            session.committed_playhead_seconds,
            session.actual_media_element_time_seconds,
        ),
        session.duration_seconds,
    )


def _route2_runtime_supply_metrics_locked(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
    *,
    route2_epoch_ready_end_seconds_locked,
    route2_effective_playhead_seconds_locked,
    route2_supply_model_locked,
) -> tuple[float, float, float, float, float, bool, bool]:
    published_end_seconds = route2_epoch_ready_end_seconds_locked(session, epoch)
    effective_playhead_seconds = route2_effective_playhead_seconds_locked(session, epoch)
    runway_seconds = max(0.0, published_end_seconds - effective_playhead_seconds)
    supply_model = route2_supply_model_locked(epoch)
    supply_rate_x = float(supply_model["effective_rate_x"])
    observation_seconds = float(supply_model["observation_seconds"])
    manifest_complete = published_end_seconds + 0.001 >= session.duration_seconds or epoch.transcoder_completed
    refill_in_progress = not manifest_complete and bool(
        epoch.active_worker_id or (epoch.process and epoch.process.poll() is None)
    )
    return (
        published_end_seconds,
        effective_playhead_seconds,
        runway_seconds,
        supply_rate_x,
        observation_seconds,
        manifest_complete,
        refill_in_progress,
    )


def _route2_position_in_epoch_locked(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
    position_seconds: float,
    *,
    route2_epoch_ready_end_seconds_locked,
) -> bool:
    ready_end_seconds = route2_epoch_ready_end_seconds_locked(session, epoch)
    required_end_seconds = min(
        session.duration_seconds,
        position_seconds + READY_AFTER_TARGET_SECONDS,
    )
    return (
        epoch.init_published
        and epoch.contiguous_published_through_segment is not None
        and epoch.epoch_start_seconds <= position_seconds <= ready_end_seconds + 0.001
        and ready_end_seconds + 0.001 >= required_end_seconds
    )


def _route2_recovery_target_locked(
    session: MobilePlaybackSession,
    active_epoch: PlaybackEpoch | None = None,
    *,
    clamp_time,
) -> float:
    if session.committed_playhead_seconds > 0:
        return clamp_time(session.committed_playhead_seconds, session.duration_seconds)
    if session.actual_media_element_time_seconds > 0:
        return clamp_time(session.actual_media_element_time_seconds, session.duration_seconds)
    if session.last_stable_position_seconds > 0:
        return clamp_time(session.last_stable_position_seconds, session.duration_seconds)
    if active_epoch is not None:
        return clamp_time(active_epoch.attach_position_seconds, session.duration_seconds)
    return clamp_time(session.target_position_seconds, session.duration_seconds)
