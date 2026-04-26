from __future__ import annotations

import math

from .mobile_playback_models import (
    ROUTE2_ATTACH_READY_SECONDS,
    ROUTE2_LITE_FAST_START_RUNWAY_SECONDS,
    ROUTE2_LITE_SLOW_START_RUNWAY_SECONDS,
    ROUTE2_RECOVERY_MIN_RUNWAY_SECONDS,
    ROUTE2_RECOVERY_MIN_SUPPLY_RATE_X,
    ROUTE2_RECOVERY_PROJECTION_HORIZON_SECONDS,
    ROUTE2_RECOVERY_RESUME_RUNWAY_SECONDS,
    ROUTE2_STARTUP_MIN_RUNWAY_SECONDS,
    ROUTE2_STARTUP_MIN_SUPPLY_RATE_X,
    ROUTE2_STARTUP_PROJECTION_HORIZON_SECONDS,
    ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS,
    ROUTE2_SUPPLY_SURPLUS_MIN_OBSERVATION_SECONDS,
    ROUTE2_SUPPLY_SURPLUS_MIN_RATE_X,
    SEGMENT_DURATION_SECONDS,
    MobilePlaybackSession,
    PlaybackEpoch,
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


def _route2_supply_surplus_locked(
    *,
    supply_rate_x: float,
    observation_seconds: float,
) -> bool:
    return (
        observation_seconds + 0.001 >= ROUTE2_SUPPLY_SURPLUS_MIN_OBSERVATION_SECONDS
        and supply_rate_x + 0.001 >= ROUTE2_SUPPLY_SURPLUS_MIN_RATE_X
    )


def _route2_lite_initial_startup_gate_locked(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
    *,
    route2_attach_gate_state_locked,
    route2_epoch_ready_end_seconds_locked,
) -> dict[str, float | str | bool | None]:
    if not epoch.init_published or epoch.contiguous_published_through_segment is None:
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
        }
    (
        _generic_ready,
        _generic_estimate_seconds,
        supply_rate_x,
        observation_seconds,
        _projected_runway_seconds,
        _display_confident,
    ) = route2_attach_gate_state_locked(
        session,
        epoch,
        minimum_runway_seconds=ROUTE2_STARTUP_MIN_RUNWAY_SECONDS,
        projected_runway_target_seconds=ROUTE2_ATTACH_READY_SECONDS,
        projection_horizon_seconds=ROUTE2_STARTUP_PROJECTION_HORIZON_SECONDS,
        minimum_supply_rate_x=ROUTE2_STARTUP_MIN_SUPPLY_RATE_X,
        reference_position_seconds=epoch.attach_position_seconds,
    )
    supply_surplus = _route2_supply_surplus_locked(
        supply_rate_x=supply_rate_x,
        observation_seconds=observation_seconds,
    )
    required_startup_runway_seconds = min(
        ROUTE2_LITE_FAST_START_RUNWAY_SECONDS if supply_surplus else ROUTE2_LITE_SLOW_START_RUNWAY_SECONDS,
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
        "gate_reason": "lite_fast_supply_surplus" if supply_surplus else "lite_slow_supply_unknown_or_deficit",
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
