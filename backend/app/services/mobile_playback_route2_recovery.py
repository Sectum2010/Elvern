from __future__ import annotations

import time

from .mobile_playback_models import (
    ROUTE2_LOW_WATER_PROJECTION_HORIZON_SECONDS,
    ROUTE2_LOW_WATER_RUNWAY_SECONDS,
    ROUTE2_LOW_WATER_SUSTAIN_SECONDS,
    ROUTE2_RECOVERY_MIN_SUPPLY_RATE_X,
    ROUTE2_RECOVERY_RESUME_RUNWAY_SECONDS,
    ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS,
    MobilePlaybackSession,
    PlaybackEpoch,
)


def _route2_low_water_recovery_needed_locked(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
    *,
    route2_runtime_supply_metrics_locked,
    route2_projected_runway_seconds_locked,
    now_ts: float | None = None,
) -> tuple[float, float, bool, bool, bool]:
    now_ts = now_ts or time.time()
    browser_session = session.browser_playback
    _, _, runway_seconds, supply_rate_x, observation_seconds, manifest_complete, refill_in_progress = (
        route2_runtime_supply_metrics_locked(session, epoch)
    )
    steady_playback_guarded = (
        browser_session.attach_revision > 0
        and browser_session.client_attach_revision >= browser_session.attach_revision
        and session.lifecycle_state == "attached"
        and session.pending_target_seconds is None
        and session.client_is_playing
    )
    starvation_risk = False
    stalled_recovery_needed = False
    if not steady_playback_guarded or manifest_complete:
        epoch.under_supply_started_at_ts = None
        return runway_seconds, supply_rate_x, refill_in_progress, starvation_risk, stalled_recovery_needed
    projected_runway_seconds = route2_projected_runway_seconds_locked(
        runway_seconds,
        supply_rate_x,
        projection_horizon_seconds=ROUTE2_LOW_WATER_PROJECTION_HORIZON_SECONDS,
    )
    starvation_risk = (
        runway_seconds <= ROUTE2_RECOVERY_RESUME_RUNWAY_SECONDS
        and observation_seconds >= ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS
        and supply_rate_x < ROUTE2_RECOVERY_MIN_SUPPLY_RATE_X
    )
    low_water_condition = (
        projected_runway_seconds <= ROUTE2_LOW_WATER_RUNWAY_SECONDS
        and observation_seconds >= ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS
        and supply_rate_x < ROUTE2_RECOVERY_MIN_SUPPLY_RATE_X
    )
    if low_water_condition:
        if epoch.under_supply_started_at_ts is None:
            epoch.under_supply_started_at_ts = now_ts
    elif (
        runway_seconds >= ROUTE2_RECOVERY_RESUME_RUNWAY_SECONDS
        or (
            observation_seconds >= ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS
            and supply_rate_x >= ROUTE2_RECOVERY_MIN_SUPPLY_RATE_X
        )
    ):
        epoch.under_supply_started_at_ts = None
    stalled_recovery_needed = (
        starvation_risk
        and epoch.under_supply_started_at_ts is not None
        and now_ts - epoch.under_supply_started_at_ts >= ROUTE2_LOW_WATER_SUSTAIN_SECONDS
    )
    return runway_seconds, supply_rate_x, refill_in_progress, starvation_risk, stalled_recovery_needed
