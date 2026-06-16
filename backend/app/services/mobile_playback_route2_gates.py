from __future__ import annotations

import math
import statistics

from .mobile_playback_models import (
    ROUTE2_ATTACH_READY_SECONDS,
    ROUTE2_ETA_DISPLAY_MAX_VOLATILITY_RATIO,
    ROUTE2_ETA_DISPLAY_MIN_GROWTH_EVENTS,
    ROUTE2_ETA_DISPLAY_MIN_OBSERVATION_SECONDS,
    ROUTE2_ETA_DISPLAY_STICKY_OBSERVATION_SECONDS,
    ROUTE2_LITE_DECIDER_COLD_START_GRACE_SECONDS,
    ROUTE2_LITE_DECIDER_FAST_CONFIRM_SECONDS,
    ROUTE2_LITE_DECIDER_MIN_FRONTIER_SAMPLE_COUNT,
    ROUTE2_LITE_DECIDER_POST_RECOVERY_HOLD_SECONDS,
    ROUTE2_LITE_DECIDER_POST_SEEK_HOLD_SECONDS,
    ROUTE2_LITE_DECIDER_UNDERSUPPLY_CONFIRM_SECONDS,
    ROUTE2_LITE_FAST_START_RUNWAY_SECONDS,
    ROUTE2_LITE_SLOW_START_RUNWAY_SECONDS,
    ROUTE2_LITE_UNDERSUPPLY_START_RUNWAY_SECONDS,
    ROUTE2_RECOVERY_MIN_RUNWAY_SECONDS,
    ROUTE2_RECOVERY_MIN_SUPPLY_RATE_X,
    ROUTE2_RECOVERY_PROJECTION_HORIZON_SECONDS,
    ROUTE2_RECOVERY_RESUME_RUNWAY_SECONDS,
    ROUTE2_STARTUP_MIN_RUNWAY_SECONDS,
    ROUTE2_STARTUP_MIN_SUPPLY_RATE_X,
    ROUTE2_STARTUP_PROJECTION_HORIZON_SECONDS,
    ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS,
    ROUTE2_SUPPLY_RATE_FAST_EMA_ALPHA,
    ROUTE2_SUPPLY_RATE_SLOW_EMA_ALPHA,
    ROUTE2_SUPPLY_SURPLUS_MIN_RATE_X,
    SEGMENT_DURATION_SECONDS,
    MobilePlaybackSession,
    PlaybackEpoch,
)


def _route2_lite_decider_default_fields(
    *,
    state: str = "neutral_45",
    reason: str = "cold_start_or_unknown",
    previous_tier: str | None = None,
    candidate_tier: str | None = None,
    confirmed_tier: str = "lite_uncertain",
    positive_evidence_seconds: float = 0.0,
    negative_evidence_seconds: float = 0.0,
    frontier_sample_count: int = 0,
    frontier_growth_rate_x: float = 0.0,
    effective_supply_rate_x: float = 0.0,
    supply_fast_ema_rate_x: float = 0.0,
    supply_slow_ema_rate_x: float = 0.0,
    supply_median_rate_x: float = 0.0,
    hysteresis_hold_reason: str | None = None,
    cold_start_hold: bool = True,
    post_recovery_hold: bool = False,
    post_seek_hold: bool = False,
) -> dict[str, float | int | str | bool | None]:
    return {
        "lite_threshold_decider_state": state,
        "lite_threshold_previous_tier": previous_tier,
        "lite_threshold_candidate_tier": candidate_tier,
        "lite_threshold_confirmed_tier": confirmed_tier,
        "lite_threshold_decider_reason": reason,
        "lite_positive_evidence_seconds": positive_evidence_seconds,
        "lite_negative_evidence_seconds": negative_evidence_seconds,
        "lite_frontier_sample_count": frontier_sample_count,
        "lite_frontier_growth_rate_x": frontier_growth_rate_x,
        "lite_effective_supply_rate_x": effective_supply_rate_x,
        "lite_supply_fast_ema_rate_x": supply_fast_ema_rate_x,
        "lite_supply_slow_ema_rate_x": supply_slow_ema_rate_x,
        "lite_supply_median_rate_x": supply_median_rate_x,
        "lite_hysteresis_hold_reason": hysteresis_hold_reason,
        "lite_cold_start_hold": cold_start_hold,
        "lite_post_recovery_hold": post_recovery_hold,
        "lite_post_seek_hold": post_seek_hold,
    }


def _ema_rate(values: list[float], *, alpha: float) -> float:
    if not values:
        return 0.0
    current = max(0.0, float(values[0]))
    for value in values[1:]:
        current = (alpha * max(0.0, float(value))) + ((1.0 - alpha) * current)
    return current


def _route2_lite_frontier_evidence(epoch: PlaybackEpoch) -> dict[str, float | int | bool | None]:
    history = list(epoch.frontier_samples)
    sample_count = len(history)
    if sample_count < 2:
        return {
            "sample_count": sample_count,
            "first_sample_ts": None,
            "latest_sample_ts": None,
            "latest_interval_rate_x": 0.0,
            "frontier_growth_rate_x": 0.0,
            "fast_ema_rate_x": 0.0,
            "slow_ema_rate_x": 0.0,
            "median_rate_x": 0.0,
            "volatility_ratio": 0.0,
            "positive_growth_events": 0,
            "positive_suffix_seconds": 0.0,
            "negative_suffix_seconds": 0.0,
            "display_confident": False,
        }

    first_ts, first_end_seconds = history[0]
    latest_ts, latest_end_seconds = history[-1]
    observation_seconds = max(0.0, float(latest_ts) - float(first_ts))
    frontier_growth_rate_x = 0.0
    if observation_seconds >= 0.25:
        frontier_growth_rate_x = max(0.0, (float(latest_end_seconds) - float(first_end_seconds)) / observation_seconds)

    interval_rates: list[float] = []
    interval_pairs: list[tuple[float, float]] = []
    for (previous_ts, previous_end), (current_ts, current_end) in zip(history, history[1:]):
        interval_seconds = max(0.0, float(current_ts) - float(previous_ts))
        frontier_growth_seconds = max(0.0, float(current_end) - float(previous_end))
        if interval_seconds < 0.25:
            continue
        interval_rate = frontier_growth_seconds / interval_seconds
        interval_pairs.append((interval_seconds, interval_rate))
        if frontier_growth_seconds > 0.001:
            interval_rates.append(interval_rate)

    if interval_rates:
        fast_ema_rate_x = _ema_rate(interval_rates, alpha=ROUTE2_SUPPLY_RATE_FAST_EMA_ALPHA)
        slow_ema_rate_x = _ema_rate(interval_rates, alpha=ROUTE2_SUPPLY_RATE_SLOW_EMA_ALPHA)
        median_rate_x = max(0.0, float(statistics.median(interval_rates)))
    else:
        fast_ema_rate_x = frontier_growth_rate_x
        slow_ema_rate_x = frontier_growth_rate_x
        median_rate_x = frontier_growth_rate_x
    rate_candidates = [
        rate
        for rate in (frontier_growth_rate_x, fast_ema_rate_x, slow_ema_rate_x, median_rate_x)
        if rate > 0.0
    ]
    volatility_ratio = (
        (max(rate_candidates) - min(rate_candidates)) / max(max(rate_candidates), 0.001)
        if len(rate_candidates) >= 2
        else 0.0
    )
    positive_growth_events = len(interval_rates)
    display_confident = (
        bool(rate_candidates)
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

    positive_suffix_seconds = 0.0
    negative_suffix_seconds = 0.0
    for interval_seconds, interval_rate in reversed(interval_pairs):
        if interval_rate + 0.001 >= ROUTE2_SUPPLY_SURPLUS_MIN_RATE_X and negative_suffix_seconds <= 0.001:
            positive_suffix_seconds += interval_seconds
            continue
        break
    for interval_seconds, interval_rate in reversed(interval_pairs):
        if interval_rate < 1.0 and positive_suffix_seconds <= 0.001:
            negative_suffix_seconds += interval_seconds
            continue
        break

    latest_interval_rate_x = interval_pairs[-1][1] if interval_pairs else 0.0
    return {
        "sample_count": sample_count,
        "first_sample_ts": float(first_ts),
        "latest_sample_ts": float(latest_ts),
        "latest_interval_rate_x": latest_interval_rate_x,
        "frontier_growth_rate_x": frontier_growth_rate_x,
        "fast_ema_rate_x": fast_ema_rate_x,
        "slow_ema_rate_x": slow_ema_rate_x,
        "median_rate_x": median_rate_x,
        "volatility_ratio": volatility_ratio,
        "positive_growth_events": positive_growth_events,
        "positive_suffix_seconds": positive_suffix_seconds,
        "negative_suffix_seconds": negative_suffix_seconds,
        "display_confident": display_confident,
    }


def _route2_lite_threshold_decider_locked(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
    *,
    supply_rate_x: float,
    observation_seconds: float,
    display_confident: bool,
) -> dict[str, float | int | str | bool | None]:
    browser_session = session.browser_playback
    evidence = _route2_lite_frontier_evidence(epoch)
    sample_count = int(evidence["sample_count"] or 0)
    first_sample_ts = evidence["first_sample_ts"]
    latest_sample_ts = evidence["latest_sample_ts"]
    latest_interval_rate_x = float(evidence["latest_interval_rate_x"] or 0.0)
    positive_suffix_seconds = float(evidence["positive_suffix_seconds"] or 0.0)
    negative_suffix_seconds = float(evidence["negative_suffix_seconds"] or 0.0)
    reference_ts = float(latest_sample_ts) if latest_sample_ts is not None else 0.0

    if latest_sample_ts is not None and not browser_session.lite_threshold_decider_started:
        browser_session.lite_threshold_decider_started = True
        browser_session.lite_threshold_decider_started_at_ts = (
            float(first_sample_ts) if first_sample_ts is not None else float(latest_sample_ts)
        )

    if latest_sample_ts is not None:
        previous_sample_ts = float(browser_session.lite_threshold_decider_last_sample_ts or 0.0)
        previous_positive_seconds = float(browser_session.lite_positive_evidence_seconds or 0.0)
        previous_negative_seconds = float(browser_session.lite_negative_evidence_seconds or 0.0)
        if previous_sample_ts > 0.0 and float(latest_sample_ts) > previous_sample_ts + 0.001:
            elapsed_seconds = max(0.0, float(latest_sample_ts) - previous_sample_ts)
            if latest_interval_rate_x + 0.001 >= ROUTE2_SUPPLY_SURPLUS_MIN_RATE_X:
                positive_evidence_seconds = max(positive_suffix_seconds, previous_positive_seconds + elapsed_seconds)
                negative_evidence_seconds = 0.0
            elif latest_interval_rate_x < 1.0:
                negative_evidence_seconds = max(negative_suffix_seconds, previous_negative_seconds + elapsed_seconds)
                positive_evidence_seconds = 0.0
            else:
                positive_evidence_seconds = positive_suffix_seconds
                negative_evidence_seconds = negative_suffix_seconds
        else:
            positive_evidence_seconds = max(previous_positive_seconds, positive_suffix_seconds)
            negative_evidence_seconds = max(previous_negative_seconds, negative_suffix_seconds)
        browser_session.lite_threshold_decider_last_sample_ts = float(latest_sample_ts)
    else:
        positive_evidence_seconds = 0.0
        negative_evidence_seconds = 0.0

    decider_started_at_ts = float(browser_session.lite_threshold_decider_started_at_ts)
    decider_age_seconds = (
        max(0.0, reference_ts - decider_started_at_ts)
        if reference_ts > 0.0 and browser_session.lite_threshold_decider_started
        else 0.0
    )
    cold_start_hold = decider_age_seconds < ROUTE2_LITE_DECIDER_COLD_START_GRACE_SECONDS
    recovery_signal = bool(
        session.stalled_recovery_requested
        or session.lifecycle_state in {"resuming", "recovering"}
        or str(session.client_playback_stall_reason or "").strip()
    )
    seek_signal = session.pending_target_seconds is not None
    if recovery_signal and reference_ts > 0.0:
        browser_session.lite_recovery_hold_until_ts = max(
            float(browser_session.lite_recovery_hold_until_ts or 0.0),
            reference_ts + ROUTE2_LITE_DECIDER_POST_RECOVERY_HOLD_SECONDS,
        )
    if seek_signal and reference_ts > 0.0:
        browser_session.lite_seek_hold_until_ts = max(
            float(browser_session.lite_seek_hold_until_ts or 0.0),
            reference_ts + ROUTE2_LITE_DECIDER_POST_SEEK_HOLD_SECONDS,
        )
    post_recovery_hold = bool(reference_ts > 0.0 and browser_session.lite_recovery_hold_until_ts > reference_ts)
    post_seek_hold = bool(reference_ts > 0.0 and browser_session.lite_seek_hold_until_ts > reference_ts)

    sample_mature = (
        sample_count >= ROUTE2_LITE_DECIDER_MIN_FRONTIER_SAMPLE_COUNT
        and observation_seconds + 0.001 >= ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS
    )
    fast_signal = bool(
        sample_mature
        and supply_rate_x + 0.001 >= ROUTE2_SUPPLY_SURPLUS_MIN_RATE_X
        and positive_evidence_seconds > 0.0
    )
    undersupply_signal = bool(
        sample_mature
        and supply_rate_x < 1.0
        and negative_evidence_seconds > 0.0
    )
    candidate_tier = None
    if fast_signal:
        candidate_tier = "lite_fast"
    elif undersupply_signal:
        candidate_tier = "lite_undersupply"

    previous_tier = browser_session.lite_threshold_confirmed_tier or "lite_uncertain"
    confirmed_tier = "lite_uncertain"
    state = "neutral_45"
    reason = "lite_neutral_uncertain"
    hysteresis_hold_reason = None
    display_ready = bool(display_confident or evidence["display_confident"])

    if not sample_mature:
        reason = "lite_neutral_insufficient_samples"
        hysteresis_hold_reason = (
            "insufficient_frontier_samples"
            if sample_count < ROUTE2_LITE_DECIDER_MIN_FRONTIER_SAMPLE_COUNT
            else "immature_supply_observation"
        )
    elif cold_start_hold:
        reason = "lite_neutral_cold_start_hold"
        hysteresis_hold_reason = "cold_start_grace"
    elif post_recovery_hold:
        state = "recovery_hold_45"
        reason = "lite_recovery_hold_45"
        hysteresis_hold_reason = "post_recovery_hold"
    elif post_seek_hold:
        state = "recovery_hold_45"
        reason = "lite_seek_hold_45"
        hysteresis_hold_reason = "post_seek_hold"
    elif (
        fast_signal
        and positive_evidence_seconds + 0.001 >= ROUTE2_LITE_DECIDER_FAST_CONFIRM_SECONDS
        and display_ready
    ):
        if previous_tier == "lite_undersupply":
            reason = "lite_recovered_from_undersupply_neutral_45"
            hysteresis_hold_reason = "undersupply_recovery_requires_neutral"
        else:
            confirmed_tier = "lite_fast"
            state = "fast_confirmed_15"
            reason = "lite_fast_confirmed"
    elif (
        undersupply_signal
        and negative_evidence_seconds + 0.001 >= ROUTE2_LITE_DECIDER_UNDERSUPPLY_CONFIRM_SECONDS
    ):
        confirmed_tier = "lite_undersupply"
        state = "undersupply_confirmed_180"
        reason = "lite_undersupply_confirmed"
    elif fast_signal:
        state = "fast_candidate"
        reason = "lite_fast_candidate_pending_confirmation"
    elif undersupply_signal:
        state = "undersupply_candidate"
        reason = "lite_undersupply_candidate_pending_confirmation"

    browser_session.lite_threshold_previous_tier = previous_tier
    browser_session.lite_threshold_candidate_tier = candidate_tier
    browser_session.lite_threshold_confirmed_tier = confirmed_tier
    browser_session.lite_threshold_decider_state = state
    browser_session.lite_threshold_decider_reason = reason
    browser_session.lite_positive_evidence_seconds = positive_evidence_seconds
    browser_session.lite_negative_evidence_seconds = negative_evidence_seconds
    browser_session.lite_hysteresis_hold_reason = hysteresis_hold_reason

    return _route2_lite_decider_default_fields(
        state=state,
        reason=reason,
        previous_tier=previous_tier,
        candidate_tier=candidate_tier,
        confirmed_tier=confirmed_tier,
        positive_evidence_seconds=positive_evidence_seconds,
        negative_evidence_seconds=negative_evidence_seconds,
        frontier_sample_count=sample_count,
        frontier_growth_rate_x=float(evidence["frontier_growth_rate_x"] or 0.0),
        effective_supply_rate_x=supply_rate_x,
        supply_fast_ema_rate_x=float(evidence["fast_ema_rate_x"] or 0.0),
        supply_slow_ema_rate_x=float(evidence["slow_ema_rate_x"] or 0.0),
        supply_median_rate_x=float(evidence["median_rate_x"] or 0.0),
        hysteresis_hold_reason=hysteresis_hold_reason,
        cold_start_hold=cold_start_hold,
        post_recovery_hold=post_recovery_hold,
        post_seek_hold=post_seek_hold,
    )


def _route2_attach_gate_state_locked(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
    *,
    minimum_runway_seconds: float,
    projected_runway_target_seconds: float,
    projection_horizon_seconds: float,
    minimum_supply_rate_x: float,
    reference_position_seconds: float | None = None,
    clamp_time,
    route2_epoch_ready_end_seconds_locked,
    route2_supply_model_locked,
    route2_runtime_supply_metrics_locked,
    route2_projected_runway_seconds_locked,
    route2_required_runway_seconds_locked,
) -> tuple[bool, float | None, float, float, float, bool]:
    if not epoch.init_published or epoch.contiguous_published_through_segment is None:
        return False, None, 0.0, 0.0, 0.0, False
    ready_end_seconds = route2_epoch_ready_end_seconds_locked(session, epoch)
    supply_model = route2_supply_model_locked(epoch)
    (
        published_end_seconds,
        effective_playhead_seconds,
        _runway_seconds,
        _supply_rate_x,
        _observation_seconds,
        manifest_complete,
        _refill_in_progress,
    ) = route2_runtime_supply_metrics_locked(session, epoch)
    supply_rate_x = float(supply_model["effective_rate_x"])
    observation_seconds = float(supply_model["observation_seconds"])
    display_confident = bool(supply_model["display_confident"])
    reference_position_seconds = clamp_time(
        reference_position_seconds if reference_position_seconds is not None else effective_playhead_seconds,
        session.duration_seconds,
    )
    runway_seconds = max(0.0, published_end_seconds - reference_position_seconds)
    projected_runway_seconds = route2_projected_runway_seconds_locked(
        runway_seconds,
        supply_rate_x,
        projection_horizon_seconds=projection_horizon_seconds,
    )
    if not (epoch.epoch_start_seconds <= reference_position_seconds <= ready_end_seconds + 0.001):
        return False, None, supply_rate_x, observation_seconds, projected_runway_seconds, display_confident
    if manifest_complete:
        return True, 0.0, supply_rate_x, observation_seconds, projected_runway_seconds, True
    required_runway_seconds = route2_required_runway_seconds_locked(
        minimum_runway_seconds=minimum_runway_seconds,
        projected_runway_target_seconds=projected_runway_target_seconds,
        projection_horizon_seconds=projection_horizon_seconds,
        supply_rate_x=supply_rate_x,
    )
    observation_ready = observation_seconds >= ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS
    ready = (
        observation_ready
        and runway_seconds + 0.001 >= required_runway_seconds
    )
    if ready:
        return True, 0.0, supply_rate_x, observation_seconds, projected_runway_seconds, True
    if not observation_ready or supply_rate_x <= 0.001:
        return False, None, supply_rate_x, observation_seconds, projected_runway_seconds, False
    observation_deficit_seconds = max(0.0, ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS - observation_seconds)
    published_end_deficit_seconds = max(
        0.0,
        min(session.duration_seconds, reference_position_seconds + required_runway_seconds) - published_end_seconds,
    )
    quantized_published_end_deficit_seconds = (
        math.ceil(published_end_deficit_seconds / SEGMENT_DURATION_SECONDS) * SEGMENT_DURATION_SECONDS
        if published_end_deficit_seconds > 0.001
        else 0.0
    )
    estimate_seconds = max(
        observation_deficit_seconds,
        quantized_published_end_deficit_seconds / supply_rate_x,
    )
    return False, estimate_seconds, supply_rate_x, observation_seconds, projected_runway_seconds, display_confident


def _route2_lite_initial_startup_gate_locked(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
    *,
    route2_attach_gate_state_locked,
    route2_epoch_ready_end_seconds_locked,
) -> dict[str, float | str | bool | None]:
    if not epoch.init_published or epoch.contiguous_published_through_segment is None:
        default_decider_fields = _route2_lite_decider_default_fields()
        return {
            "ready": False,
            "estimate_seconds": None,
            "supply_rate_x": 0.0,
            "supply_observation_seconds": 0.0,
            "required_startup_runway_seconds": min(
                ROUTE2_LITE_SLOW_START_RUNWAY_SECONDS,
                max(0.0, session.duration_seconds - epoch.attach_position_seconds),
            ),
            "actual_startup_runway_seconds": 0.0,
            "gate_reason": "lite_slow_supply_unknown_or_deficit",
            "lite_undersupply_runway_seconds": ROUTE2_LITE_UNDERSUPPLY_START_RUNWAY_SECONDS,
            "lite_undersupply_detected": False,
            "lite_undersupply_reason": None,
            "lite_required_runway_seconds": min(
                ROUTE2_LITE_SLOW_START_RUNWAY_SECONDS,
                max(0.0, session.duration_seconds - epoch.attach_position_seconds),
            ),
            "lite_required_runway_source": "slow_path_45",
            **default_decider_fields,
        }
    (
        _generic_ready,
        _generic_estimate_seconds,
        supply_rate_x,
        observation_seconds,
        _projected_runway_seconds,
        display_confident,
    ) = route2_attach_gate_state_locked(
        session,
        epoch,
        minimum_runway_seconds=ROUTE2_STARTUP_MIN_RUNWAY_SECONDS,
        projected_runway_target_seconds=ROUTE2_ATTACH_READY_SECONDS,
        projection_horizon_seconds=ROUTE2_STARTUP_PROJECTION_HORIZON_SECONDS,
        minimum_supply_rate_x=ROUTE2_STARTUP_MIN_SUPPLY_RATE_X,
        reference_position_seconds=epoch.attach_position_seconds,
    )
    decider_fields = _route2_lite_threshold_decider_locked(
        session,
        epoch,
        supply_rate_x=supply_rate_x,
        observation_seconds=observation_seconds,
        display_confident=display_confident,
    )
    decider_state = str(decider_fields.get("lite_threshold_decider_state") or "neutral_45")
    if decider_state == "fast_confirmed_15":
        required_runway_source = "healthy_fast_start_15"
        required_runway_seconds = ROUTE2_LITE_FAST_START_RUNWAY_SECONDS
        gate_reason = "lite_fast_confirmed"
        lite_undersupply_detected = False
        lite_undersupply_reason = None
    elif decider_state == "undersupply_confirmed_180":
        required_runway_source = "undersupply_180"
        required_runway_seconds = ROUTE2_LITE_UNDERSUPPLY_START_RUNWAY_SECONDS
        gate_reason = "lite_undersupply_confirmed"
        lite_undersupply_detected = True
        lite_undersupply_reason = "sustained_supply_below_1_0"
    else:
        required_runway_source = "slow_path_45"
        required_runway_seconds = ROUTE2_LITE_SLOW_START_RUNWAY_SECONDS
        gate_reason = str(decider_fields.get("lite_threshold_decider_reason") or "lite_neutral_45")
        lite_undersupply_detected = False
        lite_undersupply_reason = None
    required_startup_runway_seconds = min(
        required_runway_seconds,
        max(0.0, session.duration_seconds - epoch.attach_position_seconds),
    )
    actual_startup_runway_seconds = max(
        0.0,
        route2_epoch_ready_end_seconds_locked(session, epoch) - epoch.attach_position_seconds,
    )
    ready = actual_startup_runway_seconds + 0.001 >= required_startup_runway_seconds
    estimate_seconds = 0.0 if ready else None
    if not ready and supply_rate_x > 0.001:
        runway_deficit_seconds = max(0.0, required_startup_runway_seconds - actual_startup_runway_seconds)
        quantized_runway_deficit_seconds = (
            math.ceil(runway_deficit_seconds / SEGMENT_DURATION_SECONDS) * SEGMENT_DURATION_SECONDS
            if runway_deficit_seconds > 0.001
            else 0.0
        )
        estimate_seconds = quantized_runway_deficit_seconds / supply_rate_x
    return {
        "ready": ready,
        "estimate_seconds": estimate_seconds,
        "supply_rate_x": supply_rate_x,
        "supply_observation_seconds": observation_seconds,
        "required_startup_runway_seconds": required_startup_runway_seconds,
        "actual_startup_runway_seconds": actual_startup_runway_seconds,
        "gate_reason": gate_reason,
        "lite_undersupply_runway_seconds": ROUTE2_LITE_UNDERSUPPLY_START_RUNWAY_SECONDS,
        "lite_undersupply_detected": lite_undersupply_detected,
        "lite_undersupply_reason": lite_undersupply_reason,
        "lite_required_runway_seconds": required_startup_runway_seconds,
        "lite_required_runway_source": required_runway_source,
        **decider_fields,
    }


def _route2_epoch_startup_attach_gate_locked(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
    *,
    route2_full_mode_requires_initial_attach_gate_locked,
    route2_full_mode_gate_locked,
    route2_attach_gate_state_locked,
    route2_epoch_ready_end_seconds_locked,
) -> dict[str, float | str | bool | None]:
    if route2_full_mode_requires_initial_attach_gate_locked(session):
        full_mode_gate = route2_full_mode_gate_locked(session, epoch)
        return {
            "ready": bool(full_mode_gate["mode_ready"]),
            "estimate_seconds": full_mode_gate.get("mode_estimate_seconds"),
            "supply_rate_x": float(full_mode_gate.get("supply_rate_x") or 0.0),
            "supply_observation_seconds": float(full_mode_gate.get("supply_observation_seconds") or 0.0),
            "required_startup_runway_seconds": full_mode_gate.get("required_startup_runway_seconds"),
            "actual_startup_runway_seconds": full_mode_gate.get("actual_startup_runway_seconds"),
            "effective_goodput_ratio": full_mode_gate.get("effective_goodput_ratio"),
            "gate_reason": str(full_mode_gate.get("gate_reason") or "full_mode_gate"),
        }
    if session.browser_playback.playback_mode == "lite" and session.browser_playback.client_attach_revision == 0:
        return _route2_lite_initial_startup_gate_locked(
            session,
            epoch,
            route2_attach_gate_state_locked=route2_attach_gate_state_locked,
            route2_epoch_ready_end_seconds_locked=route2_epoch_ready_end_seconds_locked,
        )
    ready, estimate_seconds, supply_rate_x, observation_seconds, _projected_runway_seconds, _display_confident = (
        route2_attach_gate_state_locked(
            session,
            epoch,
            minimum_runway_seconds=ROUTE2_STARTUP_MIN_RUNWAY_SECONDS,
            projected_runway_target_seconds=ROUTE2_ATTACH_READY_SECONDS,
            projection_horizon_seconds=ROUTE2_STARTUP_PROJECTION_HORIZON_SECONDS,
            minimum_supply_rate_x=ROUTE2_STARTUP_MIN_SUPPLY_RATE_X,
            reference_position_seconds=epoch.attach_position_seconds,
        )
    )
    actual_startup_runway_seconds = (
        max(0.0, route2_epoch_ready_end_seconds_locked(session, epoch) - epoch.attach_position_seconds)
        if epoch.init_published and epoch.contiguous_published_through_segment is not None
        else 0.0
    )
    return {
        "ready": ready,
        "estimate_seconds": estimate_seconds,
        "supply_rate_x": supply_rate_x,
        "supply_observation_seconds": observation_seconds,
        "required_startup_runway_seconds": min(
            ROUTE2_ATTACH_READY_SECONDS,
            max(0.0, session.duration_seconds - epoch.attach_position_seconds),
        ),
        "actual_startup_runway_seconds": actual_startup_runway_seconds,
        "gate_reason": "startup_projected_runway",
    }


def _route2_epoch_startup_attach_ready_locked(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
    *,
    route2_full_mode_requires_initial_attach_gate_locked,
    route2_full_mode_gate_locked,
    route2_attach_gate_state_locked,
    route2_epoch_ready_end_seconds_locked,
) -> bool:
    return bool(
        _route2_epoch_startup_attach_gate_locked(
            session,
            epoch,
            route2_full_mode_requires_initial_attach_gate_locked=route2_full_mode_requires_initial_attach_gate_locked,
            route2_full_mode_gate_locked=route2_full_mode_gate_locked,
            route2_attach_gate_state_locked=route2_attach_gate_state_locked,
            route2_epoch_ready_end_seconds_locked=route2_epoch_ready_end_seconds_locked,
        )["ready"]
    )


def _route2_epoch_recovery_ready_locked(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
    *,
    route2_attach_gate_state_locked,
) -> bool:
    ready, _estimate_seconds, _supply_rate_x, _observation_seconds, _projected_runway_seconds, _display_confident = (
        route2_attach_gate_state_locked(
            session,
            epoch,
            minimum_runway_seconds=ROUTE2_RECOVERY_MIN_RUNWAY_SECONDS,
            projected_runway_target_seconds=ROUTE2_RECOVERY_RESUME_RUNWAY_SECONDS,
            projection_horizon_seconds=ROUTE2_RECOVERY_PROJECTION_HORIZON_SECONDS,
            minimum_supply_rate_x=ROUTE2_RECOVERY_MIN_SUPPLY_RATE_X,
        )
    )
    return ready
