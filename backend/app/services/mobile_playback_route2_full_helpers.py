from __future__ import annotations

import math
import statistics
import time

from .mobile_playback_models import (
    MOBILE_PROFILES,
    ROUTE2_FULL_BOOTSTRAP_ESTIMATE_DELAY_SECONDS,
    ROUTE2_FULL_RESERVE_BASE_SECONDS,
    ROUTE2_FULL_RESERVE_MAX_UNCERTAINTY_SECONDS,
    ROUTE2_FULL_RESERVE_MAX_VOLATILITY_SECONDS,
    ROUTE2_FULL_VOLATILITY_HORIZON_SECONDS,
    ROUTE2_SUPPLY_RATE_SLOW_EMA_ALPHA,
    SEGMENT_DURATION_SECONDS,
    MobilePlaybackSession,
    PlaybackEpoch,
)


def _parse_bitrate_bps(value: str) -> int:
    normalized = (value or "").strip().lower()
    if not normalized:
        return 0
    multiplier = 1
    if normalized.endswith("k"):
        normalized = normalized[:-1]
        multiplier = 1_000
    elif normalized.endswith("m"):
        normalized = normalized[:-1]
        multiplier = 1_000_000
    try:
        return max(0, int(float(normalized) * multiplier))
    except (TypeError, ValueError):
        return 0


def _route2_profile_floor_bytes_per_second(profile_key: str) -> float:
    profile = MOBILE_PROFILES[profile_key]
    video_bps = _parse_bitrate_bps(profile.maxrate)
    audio_bps = 160_000
    return max(1.0, (video_bps + audio_bps) / 8.0)


def _route2_profile_floor_segment_bytes(profile_key: str) -> int:
    return max(1, math.ceil(_route2_profile_floor_bytes_per_second(profile_key) * SEGMENT_DURATION_SECONDS))


def _route2_full_mode_requires_initial_attach_gate_locked(
    session: MobilePlaybackSession,
) -> bool:
    browser_session = session.browser_playback
    return (
        browser_session.engine_mode == "route2"
        and browser_session.playback_mode == "full"
    )


def _route2_full_safe_calibration_ratio_locked(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
    source_bin_bytes: list[int],
    *,
    segment_index_for_time,
    percentile_locked,
    ema_locked,
) -> float:
    source_total_bytes = max(1, sum(source_bin_bytes))
    baseline_output_total_bytes = _route2_profile_floor_bytes_per_second(session.profile) * session.duration_seconds
    baseline_ratio = max(0.0001, baseline_output_total_bytes / source_total_bytes)
    epoch_start_segment = segment_index_for_time(epoch.epoch_start_seconds)
    observed_ratios: list[float] = []
    for relative_index, actual_bytes in epoch.published_segment_bytes.items():
        absolute_index = epoch_start_segment + relative_index
        if absolute_index < 0 or absolute_index >= len(source_bin_bytes):
            continue
        source_bytes = max(source_bin_bytes[absolute_index], 1)
        observed_ratios.append(actual_bytes / source_bytes)
    if not observed_ratios:
        return baseline_ratio
    ratio_p80 = percentile_locked(observed_ratios, 0.80)
    ratio_slow_ema = ema_locked(observed_ratios, alpha=ROUTE2_SUPPLY_RATE_SLOW_EMA_ALPHA)
    ratio_median = max(0.0, float(statistics.median(observed_ratios)))
    return max(baseline_ratio, ratio_p80, ratio_slow_ema, ratio_median)


def _route2_full_budget_metrics_locked(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
    *,
    segment_index_for_time,
    route2_full_safe_calibration_ratio_locked,
) -> dict[str, float | list[float] | int] | None:
    browser_session = session.browser_playback
    source_bin_bytes = browser_session.full_source_bin_bytes
    if not source_bin_bytes:
        return None
    total_segments = max(1, math.ceil(session.duration_seconds / SEGMENT_DURATION_SECONDS))
    epoch_start_segment = segment_index_for_time(epoch.epoch_start_seconds)
    start_segment = segment_index_for_time(epoch.attach_position_seconds)
    if start_segment >= total_segments:
        start_segment = total_segments - 1
    floor_segment_bytes = _route2_profile_floor_segment_bytes(session.profile)
    floor_bytes_per_second = _route2_profile_floor_bytes_per_second(session.profile)
    calibration_ratio = route2_full_safe_calibration_ratio_locked(session, epoch, source_bin_bytes)
    frontier_segment = (
        epoch_start_segment + epoch.contiguous_published_through_segment
        if epoch.init_published and epoch.contiguous_published_through_segment is not None
        else epoch_start_segment - 1
    )

    segment_budget_bytes: list[float] = []
    deadline_seconds: list[float] = []
    estimated_fraction_bytes = 0.0
    remaining_total_bytes = 0.0
    prepared_bytes = 0.0
    for absolute_index in range(start_segment, total_segments):
        relative_index = absolute_index - epoch_start_segment
        actual_bytes = None
        if (
            epoch.contiguous_published_through_segment is not None
            and 0 <= relative_index <= epoch.contiguous_published_through_segment
        ):
            actual_bytes = epoch.published_segment_bytes.get(relative_index)
        if actual_bytes is None:
            predicted_bytes = max(
                floor_segment_bytes,
                math.ceil(max(source_bin_bytes[absolute_index], 1) * calibration_ratio),
            )
            segment_bytes = float(predicted_bytes)
            estimated_fraction_bytes += segment_bytes
        else:
            segment_bytes = float(actual_bytes)
            if absolute_index <= frontier_segment:
                prepared_bytes += segment_bytes
        remaining_total_bytes += segment_bytes
        segment_budget_bytes.append(segment_bytes)
        deadline_seconds.append((len(segment_budget_bytes)) * SEGMENT_DURATION_SECONDS)

    if not segment_budget_bytes:
        return None

    horizon_segments = max(
        1,
        min(
            len(segment_budget_bytes),
            math.ceil(min(ROUTE2_FULL_VOLATILITY_HORIZON_SECONDS, max(session.duration_seconds - epoch.attach_position_seconds, 0.0)) / SEGMENT_DURATION_SECONDS),
        ),
    )
    horizon_budget = segment_budget_bytes[:horizon_segments]
    horizon_mean_bytes = statistics.mean(horizon_budget) if horizon_budget else 0.0
    future_segment_cv = (
        (statistics.pstdev(horizon_budget) / horizon_mean_bytes)
        if horizon_budget and horizon_mean_bytes > 0.0
        else 0.0
    )
    estimated_fraction_remaining = (
        estimated_fraction_bytes / remaining_total_bytes
        if remaining_total_bytes > 0.0
        else 0.0
    )
    reference_rates = [segment_bytes / SEGMENT_DURATION_SECONDS for segment_bytes in horizon_budget if segment_bytes > 0.0]
    predicted_rate_median = max(0.0, float(statistics.median(reference_rates))) if reference_rates else 0.0
    reference_bytes_per_second = max(floor_bytes_per_second, predicted_rate_median)
    reserve_seconds = (
        ROUTE2_FULL_RESERVE_BASE_SECONDS
        + min(ROUTE2_FULL_RESERVE_MAX_VOLATILITY_SECONDS, max(0.0, 8.0 * future_segment_cv))
        + min(ROUTE2_FULL_RESERVE_MAX_UNCERTAINTY_SECONDS, max(0.0, 10.0 * estimated_fraction_remaining))
    )
    reserve_bytes = reserve_seconds * reference_bytes_per_second

    cumulative_budget_bytes: list[float] = []
    cumulative_bytes = 0.0
    for segment_bytes in segment_budget_bytes:
        cumulative_bytes += segment_bytes
        cumulative_budget_bytes.append(cumulative_bytes)

    return {
        "prepared_bytes": prepared_bytes,
        "cumulative_budget_bytes": cumulative_budget_bytes,
        "deadline_seconds": deadline_seconds,
        "reserve_bytes": reserve_bytes,
        "estimated_fraction_remaining": estimated_fraction_remaining,
        "future_segment_cv": future_segment_cv,
        "reference_bytes_per_second": reference_bytes_per_second,
    }


def _route2_full_prepare_elapsed_seconds_locked(
    session: MobilePlaybackSession,
    *,
    now_ts: float | None = None,
) -> float:
    started_at_ts = float(session.browser_playback.full_prepare_started_at_ts or 0.0)
    if started_at_ts <= 0.0:
        return 0.0
    now_ts = now_ts or time.time()
    return max(0.0, now_ts - started_at_ts)


def _route2_full_bootstrap_eta_locked(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
    *,
    now_ts: float | None = None,
    route2_full_prepare_elapsed_seconds_locked,
    route2_epoch_ready_end_seconds,
    route2_supply_model_locked,
) -> float | None:
    now_ts = now_ts or time.time()
    prepare_elapsed_seconds = route2_full_prepare_elapsed_seconds_locked(session, now_ts=now_ts)
    if prepare_elapsed_seconds < ROUTE2_FULL_BOOTSTRAP_ESTIMATE_DELAY_SECONDS:
        return None
    published_end_seconds = route2_epoch_ready_end_seconds(session, epoch)
    prepared_media_seconds = max(0.0, published_end_seconds - epoch.attach_position_seconds)
    if prepared_media_seconds <= 0.001:
        return None
    overall_prepare_rate_x = prepared_media_seconds / max(prepare_elapsed_seconds, 0.001)
    supply_model = route2_supply_model_locked(epoch)
    rate_candidates = [
        overall_prepare_rate_x,
        float(supply_model["overall_rate_x"]),
        float(supply_model["slow_ema_rate_x"]),
        float(supply_model["median_rate_x"]),
    ]
    positive_rate_candidates = [rate for rate in rate_candidates if rate > 0.0]
    if not positive_rate_candidates:
        return None
    bootstrap_prepare_rate_x = min(positive_rate_candidates)
    if bootstrap_prepare_rate_x <= 0.001:
        return None
    remaining_media_seconds = max(0.0, session.duration_seconds - epoch.attach_position_seconds - prepared_media_seconds)
    if remaining_media_seconds <= 0.001:
        return 0.0
    return remaining_media_seconds / bootstrap_prepare_rate_x
