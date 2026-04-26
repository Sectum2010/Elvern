from __future__ import annotations

import math
import time

from .mobile_playback_models import (
    ROUTE2_ETA_DISPLAY_GRACE_SECONDS,
    ROUTE2_ETA_DISPLAY_MAX_UPWARD_RATIO,
    ROUTE2_ETA_DISPLAY_MAX_UPWARD_STEP_SECONDS,
    ROUTE2_ETA_DISPLAY_UPWARD_BLEND,
    ROUTE2_FULL_BOOTSTRAP_ESTIMATE_DELAY_SECONDS,
    ROUTE2_FULL_FAST_START_RUNWAY_SECONDS,
    ROUTE2_FULL_GOODPUT_MIN_OBSERVATION_SECONDS,
    ROUTE2_SUPPLY_SURPLUS_MIN_OBSERVATION_SECONDS,
    ROUTE2_SUPPLY_SURPLUS_MIN_RATE_X,
    SEGMENT_DURATION_SECONDS,
    MobilePlaybackSession,
    PlaybackEpoch,
)


def _route2_display_prepare_eta_locked(
    epoch: PlaybackEpoch,
    raw_eta_seconds: float | None,
    *,
    now_ts: float | None = None,
    display_confident: bool = False,
) -> float | None:
    now_ts = now_ts or time.time()
    previous_display_eta_seconds = epoch.display_eta_seconds
    previous_updated_at_ts = epoch.display_eta_updated_at_ts
    if raw_eta_seconds is None:
        if (
            epoch.display_eta_stable
            and previous_display_eta_seconds is not None
            and previous_updated_at_ts > 0
            and now_ts - previous_updated_at_ts <= ROUTE2_ETA_DISPLAY_GRACE_SECONDS
        ):
            continued_eta_seconds = max(0.0, previous_display_eta_seconds - (now_ts - previous_updated_at_ts))
            epoch.display_eta_seconds = continued_eta_seconds
            epoch.display_eta_updated_at_ts = now_ts
            return continued_eta_seconds
        epoch.display_eta_seconds = None
        epoch.display_eta_updated_at_ts = now_ts
        epoch.display_eta_stable = False
        return None
    if not epoch.display_eta_stable:
        epoch.display_eta_updated_at_ts = now_ts
        if not display_confident:
            epoch.display_eta_seconds = None
            return None
        epoch.display_eta_seconds = max(0.0, raw_eta_seconds)
        epoch.display_eta_stable = True
        return epoch.display_eta_seconds
    if previous_display_eta_seconds is None or previous_updated_at_ts <= 0:
        epoch.display_eta_seconds = max(0.0, raw_eta_seconds)
        epoch.display_eta_updated_at_ts = now_ts
        epoch.display_eta_stable = True
        return epoch.display_eta_seconds
    elapsed_seconds = max(0.0, now_ts - previous_updated_at_ts)
    predicted_downward_eta_seconds = max(0.0, previous_display_eta_seconds - elapsed_seconds)
    if raw_eta_seconds <= predicted_downward_eta_seconds:
        next_display_eta_seconds = raw_eta_seconds
    else:
        max_upward_step_seconds = max(
            ROUTE2_ETA_DISPLAY_MAX_UPWARD_STEP_SECONDS,
            previous_display_eta_seconds * ROUTE2_ETA_DISPLAY_MAX_UPWARD_RATIO,
        )
        upward_cap_eta_seconds = predicted_downward_eta_seconds + max_upward_step_seconds
        blended_upward_eta_seconds = predicted_downward_eta_seconds + (
            (raw_eta_seconds - predicted_downward_eta_seconds) * ROUTE2_ETA_DISPLAY_UPWARD_BLEND
        )
        next_display_eta_seconds = min(
            raw_eta_seconds,
            max(predicted_downward_eta_seconds, min(upward_cap_eta_seconds, blended_upward_eta_seconds)),
        )
    epoch.display_eta_seconds = max(0.0, next_display_eta_seconds)
    epoch.display_eta_updated_at_ts = now_ts
    epoch.display_eta_stable = True
    return epoch.display_eta_seconds


def _route2_full_mode_gate_result(
    *,
    mode_state: str,
    mode_ready: bool,
    mode_estimate_seconds: float | None,
    mode_estimate_source: str,
    gate_reason: str,
    required_startup_runway_seconds: float | None = None,
    actual_startup_runway_seconds: float | None = None,
    effective_goodput_ratio: float | None = None,
    supply_rate_x: float = 0.0,
    supply_observation_seconds: float = 0.0,
) -> dict[str, float | str | bool | None]:
    return {
        "mode_state": mode_state,
        "mode_ready": mode_ready,
        "mode_estimate_seconds": mode_estimate_seconds,
        "mode_estimate_source": mode_estimate_source,
        "required_startup_runway_seconds": required_startup_runway_seconds,
        "actual_startup_runway_seconds": actual_startup_runway_seconds,
        "effective_goodput_ratio": effective_goodput_ratio,
        "supply_rate_x": supply_rate_x,
        "supply_observation_seconds": supply_observation_seconds,
        "gate_reason": gate_reason,
    }


def _route2_full_mode_gate_locked(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
    *,
    route2_full_mode_requires_initial_attach_gate_locked,
    route2_full_prepare_elapsed_seconds_locked,
    ensure_route2_full_preflight_locked,
    route2_full_bootstrap_eta_locked,
    route2_full_budget_metrics_locked,
    route2_server_byte_goodput_locked,
    route2_client_goodput_locked,
    route2_epoch_ready_end_seconds,
    route2_supply_model_locked,
) -> dict[str, float | str | bool | None]:
    browser_session = session.browser_playback
    if browser_session.playback_mode != "full":
        return _route2_full_mode_gate_result(
            mode_state="ready",
            mode_ready=True,
            mode_estimate_seconds=None,
            mode_estimate_source="none",
            gate_reason="not_full_mode",
        )
    if not route2_full_mode_requires_initial_attach_gate_locked(session):
        return _route2_full_mode_gate_result(
            mode_state="ready",
            mode_ready=True,
            mode_estimate_seconds=None,
            mode_estimate_source="none",
            gate_reason="full_gate_not_required",
        )
    now_ts = time.time()
    prepare_elapsed_seconds = route2_full_prepare_elapsed_seconds_locked(session, now_ts=now_ts)
    ensure_route2_full_preflight_locked(session)
    ready_end_seconds = route2_epoch_ready_end_seconds(session, epoch)
    actual_startup_runway_seconds = max(0.0, ready_end_seconds - epoch.attach_position_seconds)
    required_startup_runway_seconds = min(
        ROUTE2_FULL_FAST_START_RUNWAY_SECONDS,
        max(0.0, session.duration_seconds - epoch.attach_position_seconds),
    )
    supply_model = route2_supply_model_locked(epoch)
    supply_rate_x = float(supply_model["effective_rate_x"])
    supply_observation_seconds = float(supply_model["observation_seconds"])
    supply_surplus = (
        supply_observation_seconds + 0.001 >= ROUTE2_SUPPLY_SURPLUS_MIN_OBSERVATION_SECONDS
        and supply_rate_x + 0.001 >= ROUTE2_SUPPLY_SURPLUS_MIN_RATE_X
    )
    fast_start_wait_seconds = None
    if not supply_surplus or actual_startup_runway_seconds + 0.001 >= required_startup_runway_seconds:
        fast_start_wait_seconds = 0.0 if supply_surplus else None
    elif supply_rate_x > 0.001:
        startup_runway_deficit_seconds = required_startup_runway_seconds - actual_startup_runway_seconds
        quantized_runway_deficit_seconds = (
            math.ceil(startup_runway_deficit_seconds / SEGMENT_DURATION_SECONDS) * SEGMENT_DURATION_SECONDS
            if startup_runway_deficit_seconds > 0.001
            else 0.0
        )
        fast_start_wait_seconds = quantized_runway_deficit_seconds / supply_rate_x
    if browser_session.full_preflight_state != "ready" or not browser_session.full_source_bin_bytes:
        bootstrap_eta_seconds = route2_full_bootstrap_eta_locked(session, epoch, now_ts=now_ts)
        return _route2_full_mode_gate_result(
            mode_state="estimating" if bootstrap_eta_seconds is None else "preparing",
            mode_ready=False,
            mode_estimate_seconds=bootstrap_eta_seconds,
            mode_estimate_source="bootstrap" if bootstrap_eta_seconds is not None else "none",
            gate_reason="full_preflight_bootstrap",
            required_startup_runway_seconds=required_startup_runway_seconds,
            actual_startup_runway_seconds=actual_startup_runway_seconds,
            supply_rate_x=supply_rate_x,
            supply_observation_seconds=supply_observation_seconds,
        )
    budget_metrics = route2_full_budget_metrics_locked(session, epoch)
    if budget_metrics is None:
        bootstrap_eta_seconds = route2_full_bootstrap_eta_locked(session, epoch, now_ts=now_ts)
        return _route2_full_mode_gate_result(
            mode_state="estimating" if bootstrap_eta_seconds is None else "preparing",
            mode_ready=False,
            mode_estimate_seconds=bootstrap_eta_seconds,
            mode_estimate_source="bootstrap" if bootstrap_eta_seconds is not None else "none",
            gate_reason="full_budget_unavailable_bootstrap",
            required_startup_runway_seconds=required_startup_runway_seconds,
            actual_startup_runway_seconds=actual_startup_runway_seconds,
            supply_rate_x=supply_rate_x,
            supply_observation_seconds=supply_observation_seconds,
        )
    server_goodput = route2_server_byte_goodput_locked(epoch)
    client_goodput = route2_client_goodput_locked(session)
    server_safe = float(server_goodput["safe_rate"])
    client_safe = float(client_goodput["safe_rate"])
    server_confident = bool(server_goodput["confident"]) and server_safe > 0.0
    client_confident = bool(client_goodput["confident"]) and client_safe > 0.0
    bootstrap_eta_seconds = route2_full_bootstrap_eta_locked(session, epoch, now_ts=now_ts)
    estimate_safe_goodput = min(
        server_safe,
        client_safe if client_safe > 0.0 else server_safe,
    )
    prepared_bytes = float(budget_metrics["prepared_bytes"])
    reserve_bytes = float(budget_metrics["reserve_bytes"])
    cumulative_budget_bytes = [float(value) for value in budget_metrics["cumulative_budget_bytes"]]
    deadline_seconds = [float(value) for value in budget_metrics["deadline_seconds"]]
    reference_bytes_per_second = max(1.0, float(budget_metrics["reference_bytes_per_second"]))
    effective_goodput_ratio = (
        estimate_safe_goodput / reference_bytes_per_second
        if estimate_safe_goodput > 0.0
        else None
    )
    if cumulative_budget_bytes and prepared_bytes + 0.001 >= cumulative_budget_bytes[-1]:
        return _route2_full_mode_gate_result(
            mode_state="ready",
            mode_ready=True,
            mode_estimate_seconds=0.0,
            mode_estimate_source="true",
            gate_reason="full_budget_complete",
            required_startup_runway_seconds=required_startup_runway_seconds,
            actual_startup_runway_seconds=actual_startup_runway_seconds,
            effective_goodput_ratio=effective_goodput_ratio,
            supply_rate_x=supply_rate_x,
            supply_observation_seconds=supply_observation_seconds,
        )
    estimate_deficit_bytes = 0.0
    for cumulative_required_bytes, deadline_seconds_value in zip(cumulative_budget_bytes, deadline_seconds):
        covered_bytes = prepared_bytes + (estimate_safe_goodput * deadline_seconds_value)
        estimate_deficit_bytes = max(
            estimate_deficit_bytes,
            (cumulative_required_bytes + reserve_bytes) - covered_bytes,
        )
    if client_confident and estimate_deficit_bytes <= 0.001:
        return _route2_full_mode_gate_result(
            mode_state="ready",
            mode_ready=True,
            mode_estimate_seconds=0.0,
            mode_estimate_source="true",
            gate_reason="full_budget_projected_ready",
            required_startup_runway_seconds=required_startup_runway_seconds,
            actual_startup_runway_seconds=actual_startup_runway_seconds,
            effective_goodput_ratio=effective_goodput_ratio,
            supply_rate_x=supply_rate_x,
            supply_observation_seconds=supply_observation_seconds,
        )
    if supply_surplus and actual_startup_runway_seconds + 0.001 >= required_startup_runway_seconds:
        return _route2_full_mode_gate_result(
            mode_state="ready",
            mode_ready=True,
            mode_estimate_seconds=0.0,
            mode_estimate_source="fast_start_supply_surplus",
            gate_reason="full_fast_start_supply_surplus",
            required_startup_runway_seconds=required_startup_runway_seconds,
            actual_startup_runway_seconds=actual_startup_runway_seconds,
            effective_goodput_ratio=effective_goodput_ratio,
            supply_rate_x=supply_rate_x,
            supply_observation_seconds=supply_observation_seconds,
        )
    if not server_confident:
        if fast_start_wait_seconds is not None:
            return _route2_full_mode_gate_result(
                mode_state="preparing",
                mode_ready=False,
                mode_estimate_seconds=fast_start_wait_seconds,
                mode_estimate_source="fast_start_supply_surplus",
                gate_reason="full_fast_start_waiting_for_runway",
                required_startup_runway_seconds=required_startup_runway_seconds,
                actual_startup_runway_seconds=actual_startup_runway_seconds,
                effective_goodput_ratio=effective_goodput_ratio,
                supply_rate_x=supply_rate_x,
                supply_observation_seconds=supply_observation_seconds,
            )
        return _route2_full_mode_gate_result(
            mode_state="estimating" if bootstrap_eta_seconds is None else "preparing",
            mode_ready=False,
            mode_estimate_seconds=bootstrap_eta_seconds,
            mode_estimate_source="bootstrap" if bootstrap_eta_seconds is not None else "none",
            gate_reason="full_bootstrap_server_unknown",
            required_startup_runway_seconds=required_startup_runway_seconds,
            actual_startup_runway_seconds=actual_startup_runway_seconds,
            effective_goodput_ratio=effective_goodput_ratio,
            supply_rate_x=supply_rate_x,
            supply_observation_seconds=supply_observation_seconds,
        )
    if estimate_safe_goodput <= 0.0:
        if fast_start_wait_seconds is not None:
            return _route2_full_mode_gate_result(
                mode_state="preparing",
                mode_ready=False,
                mode_estimate_seconds=fast_start_wait_seconds,
                mode_estimate_source="fast_start_supply_surplus",
                gate_reason="full_fast_start_waiting_for_runway",
                required_startup_runway_seconds=required_startup_runway_seconds,
                actual_startup_runway_seconds=actual_startup_runway_seconds,
                effective_goodput_ratio=effective_goodput_ratio,
                supply_rate_x=supply_rate_x,
                supply_observation_seconds=supply_observation_seconds,
            )
        return _route2_full_mode_gate_result(
            mode_state="estimating" if bootstrap_eta_seconds is None else "preparing",
            mode_ready=False,
            mode_estimate_seconds=bootstrap_eta_seconds,
            mode_estimate_source="bootstrap" if bootstrap_eta_seconds is not None else "none",
            gate_reason="full_bootstrap_effective_goodput_unknown",
            required_startup_runway_seconds=required_startup_runway_seconds,
            actual_startup_runway_seconds=actual_startup_runway_seconds,
            effective_goodput_ratio=effective_goodput_ratio,
            supply_rate_x=supply_rate_x,
            supply_observation_seconds=supply_observation_seconds,
        )
    if not client_confident:
        if fast_start_wait_seconds is not None:
            return _route2_full_mode_gate_result(
                mode_state="preparing",
                mode_ready=False,
                mode_estimate_seconds=fast_start_wait_seconds,
                mode_estimate_source="fast_start_supply_surplus",
                gate_reason="full_fast_start_waiting_for_runway",
                required_startup_runway_seconds=required_startup_runway_seconds,
                actual_startup_runway_seconds=actual_startup_runway_seconds,
                effective_goodput_ratio=effective_goodput_ratio,
                supply_rate_x=supply_rate_x,
                supply_observation_seconds=supply_observation_seconds,
            )
        if bootstrap_eta_seconds is not None:
            return _route2_full_mode_gate_result(
                mode_state="preparing",
                mode_ready=False,
                mode_estimate_seconds=bootstrap_eta_seconds,
                mode_estimate_source="bootstrap",
                gate_reason="full_budget_waiting_for_client_probe",
                required_startup_runway_seconds=required_startup_runway_seconds,
                actual_startup_runway_seconds=actual_startup_runway_seconds,
                effective_goodput_ratio=effective_goodput_ratio,
                supply_rate_x=supply_rate_x,
                supply_observation_seconds=supply_observation_seconds,
            )
        client_observation_seconds = float(client_goodput["observation_seconds"])
        client_observation_deficit_seconds = max(
            0.0,
            ROUTE2_FULL_GOODPUT_MIN_OBSERVATION_SECONDS - client_observation_seconds,
        )
        if estimate_deficit_bytes <= 0.001:
            return _route2_full_mode_gate_result(
                mode_state="estimating" if client_observation_deficit_seconds > 0.0 else "preparing",
                mode_ready=False,
                mode_estimate_seconds=None,
                mode_estimate_source="none",
                gate_reason="full_budget_waiting_for_client_probe",
                required_startup_runway_seconds=required_startup_runway_seconds,
                actual_startup_runway_seconds=actual_startup_runway_seconds,
                effective_goodput_ratio=effective_goodput_ratio,
                supply_rate_x=supply_rate_x,
                supply_observation_seconds=supply_observation_seconds,
            )
        estimate_wait_seconds = max(
            estimate_deficit_bytes / server_safe,
            client_observation_deficit_seconds,
        )
    else:
        estimate_wait_seconds = estimate_deficit_bytes / estimate_safe_goodput if estimate_deficit_bytes > 0.001 else 0.0
    if prepare_elapsed_seconds < ROUTE2_FULL_BOOTSTRAP_ESTIMATE_DELAY_SECONDS:
        return _route2_full_mode_gate_result(
            mode_state="estimating",
            mode_ready=False,
            mode_estimate_seconds=None,
            mode_estimate_source="none",
            gate_reason="full_budget_waiting_for_stable_eta",
            required_startup_runway_seconds=required_startup_runway_seconds,
            actual_startup_runway_seconds=actual_startup_runway_seconds,
            effective_goodput_ratio=effective_goodput_ratio,
            supply_rate_x=supply_rate_x,
            supply_observation_seconds=supply_observation_seconds,
        )
    return _route2_full_mode_gate_result(
        mode_state="preparing",
        mode_ready=False,
        mode_estimate_seconds=estimate_wait_seconds,
        mode_estimate_source="true",
        gate_reason="full_budget_waiting",
        required_startup_runway_seconds=required_startup_runway_seconds,
        actual_startup_runway_seconds=actual_startup_runway_seconds,
        effective_goodput_ratio=effective_goodput_ratio,
        supply_rate_x=supply_rate_x,
        supply_observation_seconds=supply_observation_seconds,
    )
