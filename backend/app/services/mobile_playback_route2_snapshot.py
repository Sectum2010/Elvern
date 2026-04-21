from __future__ import annotations

import time

from .mobile_playback_models import (
    ROUTE2_ATTACH_READY_SECONDS,
    ROUTE2_RECOVERY_MIN_RUNWAY_SECONDS,
    ROUTE2_RECOVERY_MIN_SUPPLY_RATE_X,
    ROUTE2_RECOVERY_PROJECTION_HORIZON_SECONDS,
    ROUTE2_RECOVERY_RESUME_RUNWAY_SECONDS,
    ROUTE2_STARTUP_MIN_RUNWAY_SECONDS,
    ROUTE2_STARTUP_MIN_SUPPLY_RATE_X,
    ROUTE2_STARTUP_PROJECTION_HORIZON_SECONDS,
    STATUS_POLL_PREPARE_SECONDS,
    MobilePlaybackSession,
)


def _route2_snapshot_locked(
    session: MobilePlaybackSession,
    *,
    route2_attach_gate_state_locked,
    route2_display_prepare_eta_locked,
    route2_epoch_recovery_ready_locked,
    route2_epoch_startup_attach_ready_locked,
    guard_route2_full_attach_boundary_locked,
    route2_epoch_ready_end_seconds,
    route2_low_water_recovery_needed_locked,
    route2_full_mode_gate_locked,
    route2_position_in_epoch_locked,
    segment_index_for_time,
) -> dict[str, object]:
    now_ts = time.time()
    browser_session = session.browser_playback
    active_epoch = (
        browser_session.epochs.get(browser_session.active_epoch_id)
        if browser_session.active_epoch_id
        else None
    )
    replacement_epoch = (
        browser_session.epochs.get(browser_session.replacement_epoch_id)
        if browser_session.replacement_epoch_id
        else None
    )
    active_manifest_url = None
    attach_position_seconds = round(session.target_position_seconds, 2)
    attach_ready = False
    ahead_runway_seconds = 0.0
    supply_rate_x = 0.0
    supply_observation_seconds = 0.0
    prepare_estimate_seconds = None
    mode_estimate_seconds = None
    mode_estimate_source = "none"
    mode_state = "preparing"
    mode_ready = False
    refill_in_progress = False
    starvation_risk = False
    stalled_recovery_needed = False
    if active_epoch is not None:
        active_manifest_url = f"/api/mobile-playback/epochs/{active_epoch.epoch_id}/index.m3u8"
        attach_position_seconds = round(active_epoch.attach_position_seconds, 2)
    ready_start_seconds = 0.0
    ready_end_seconds = 0.0
    cache_ranges: list[list[float]] = []
    manifest_end_segment = 0
    controller_epoch = replacement_epoch if replacement_epoch is not None else active_epoch
    recovery_gate = replacement_epoch is None and session.lifecycle_state in {"resuming", "recovering"}
    if controller_epoch and controller_epoch.init_published and controller_epoch.contiguous_published_through_segment is not None:
        (
            _controller_attach_ready,
            raw_prepare_estimate_seconds,
            supply_rate_x,
            supply_observation_seconds,
            _projected_runway_seconds,
            display_confident,
        ) = route2_attach_gate_state_locked(
            session,
            controller_epoch,
            minimum_runway_seconds=(
                ROUTE2_RECOVERY_MIN_RUNWAY_SECONDS if recovery_gate else ROUTE2_STARTUP_MIN_RUNWAY_SECONDS
            ),
            projected_runway_target_seconds=(
                ROUTE2_RECOVERY_RESUME_RUNWAY_SECONDS if recovery_gate else ROUTE2_ATTACH_READY_SECONDS
            ),
            projection_horizon_seconds=(
                ROUTE2_RECOVERY_PROJECTION_HORIZON_SECONDS if recovery_gate else ROUTE2_STARTUP_PROJECTION_HORIZON_SECONDS
            ),
            minimum_supply_rate_x=(
                ROUTE2_RECOVERY_MIN_SUPPLY_RATE_X if recovery_gate else ROUTE2_STARTUP_MIN_SUPPLY_RATE_X
            ),
            reference_position_seconds=None if recovery_gate else controller_epoch.attach_position_seconds,
        )
        prepare_estimate_seconds = route2_display_prepare_eta_locked(
            controller_epoch,
            raw_prepare_estimate_seconds,
            now_ts=now_ts,
            display_confident=display_confident,
        )
    if active_epoch and active_epoch.init_published and active_epoch.contiguous_published_through_segment is not None:
        recovery_attach_ready = route2_epoch_recovery_ready_locked(session, active_epoch)
        startup_attach_ready = route2_epoch_startup_attach_ready_locked(session, active_epoch)
        attach_ready = (
            recovery_attach_ready if session.lifecycle_state in {"resuming", "recovering"} else startup_attach_ready
        ) and browser_session.attach_revision > 0
        attach_ready = guard_route2_full_attach_boundary_locked(
            session,
            active_epoch,
            attach_eligible=attach_ready,
            guard_path="route2_snapshot_attach_ready",
        )
        ready_start_seconds = round(active_epoch.epoch_start_seconds, 2)
        ready_end_seconds = round(route2_epoch_ready_end_seconds(session, active_epoch), 2)
        manifest_end_segment = active_epoch.contiguous_published_through_segment
        cache_ranges = [[ready_start_seconds, ready_end_seconds]]
        (
            ahead_runway_seconds,
            _supply_rate_x,
            refill_in_progress,
            starvation_risk,
            stalled_recovery_needed,
        ) = route2_low_water_recovery_needed_locked(session, active_epoch)
        if replacement_epoch is None:
            supply_rate_x = _supply_rate_x
    if browser_session.playback_mode == "full" and controller_epoch is not None:
        full_mode_gate = route2_full_mode_gate_locked(session, controller_epoch)
        mode_state = str(full_mode_gate["mode_state"])
        mode_ready = bool(full_mode_gate["mode_ready"])
        mode_estimate_source = str(full_mode_gate.get("mode_estimate_source") or "none")
        mode_estimate_seconds = (
            round(float(full_mode_gate["mode_estimate_seconds"]), 2)
            if full_mode_gate["mode_estimate_seconds"] is not None
            else None
        )
        prepare_estimate_seconds = mode_estimate_seconds
    else:
        mode_ready = attach_ready
        mode_estimate_seconds = round(prepare_estimate_seconds, 2) if prepare_estimate_seconds is not None else None
        mode_estimate_source = "true" if mode_estimate_seconds is not None else "none"
        if mode_ready:
            mode_state = "ready"
        elif mode_estimate_seconds is None:
            mode_state = "estimating"
        else:
            mode_state = "preparing"
    can_play_from_target = (
        active_epoch is not None
        and route2_position_in_epoch_locked(session, active_epoch, session.target_position_seconds)
        and session.pending_target_seconds is None
    )
    return {
        "session_id": session.session_id,
        "media_item_id": session.media_item_id,
        "epoch": session.epoch,
        "manifest_revision": (
            f"route2:{browser_session.attach_revision}:{active_epoch.epoch_id}"
            if active_epoch is not None
            else f"route2:{browser_session.attach_revision}:none"
        ),
        "state": session.state,
        "profile": session.profile,
        "duration_seconds": round(session.duration_seconds, 2),
        "target_position_seconds": round(session.target_position_seconds, 2),
        "ready_start_seconds": ready_start_seconds,
        "ready_end_seconds": ready_end_seconds,
        "can_play_from_target": can_play_from_target,
        "manifest_url": (
            active_manifest_url
            if active_manifest_url
            else f"/api/mobile-playback/sessions/{session.session_id}/index.m3u8"
        ),
        "status_url": f"/api/mobile-playback/sessions/{session.session_id}",
        "seek_url": f"/api/mobile-playback/sessions/{session.session_id}/seek",
        "heartbeat_url": f"/api/mobile-playback/sessions/{session.session_id}/heartbeat",
        "stop_url": f"/api/mobile-playback/sessions/{session.session_id}/stop",
        "manifest_start_segment": 0,
        "manifest_end_segment": manifest_end_segment,
        "manifest_start_seconds": ready_start_seconds,
        "manifest_end_seconds": ready_end_seconds,
        "last_error": session.last_error,
        "worker_state": session.worker_state,
        "pending_target_seconds": round(session.pending_target_seconds, 2)
        if session.pending_target_seconds is not None
        else None,
        "last_stable_position_seconds": round(session.last_stable_position_seconds, 2),
        "playing_before_seek": session.playing_before_seek,
        "target_segment_index": segment_index_for_time(session.target_position_seconds),
        "target_cluster_ready": False,
        "target_window_ready": False,
        "playback_commit_ready": False,
        "cache_ranges": cache_ranges,
        "committed_playhead_seconds": round(session.committed_playhead_seconds, 2),
        "actual_media_element_time_seconds": round(session.actual_media_element_time_seconds, 2),
        "ahead_runway_seconds": round(ahead_runway_seconds, 2),
        "supply_rate_x": round(supply_rate_x, 3),
        "supply_observation_seconds": round(supply_observation_seconds, 2),
        "prepare_estimate_seconds": round(prepare_estimate_seconds, 2)
        if prepare_estimate_seconds is not None
        else None,
        "refill_in_progress": refill_in_progress,
        "last_refill_start_seconds": None,
        "last_refill_end_seconds": None,
        "starvation_risk": starvation_risk,
        "stalled_recovery_needed": stalled_recovery_needed,
        "lifecycle_state": session.lifecycle_state,
        "status_poll_seconds": (
            STATUS_POLL_PREPARE_SECONDS
            if browser_session.replacement_epoch_id or not attach_ready or browser_session.client_attach_revision < browser_session.attach_revision
            else 3.0
        ),
        "engine_mode": browser_session.engine_mode,
        "playback_mode": browser_session.playback_mode,
        "mode_state": mode_state,
        "mode_ready": mode_ready,
        "mode_estimate_seconds": mode_estimate_seconds,
        "mode_estimate_source": mode_estimate_source,
        "session_state": browser_session.state,
        "attach_revision": browser_session.attach_revision,
        "client_attach_revision": browser_session.client_attach_revision,
        "active_epoch_id": browser_session.active_epoch_id,
        "replacement_epoch_id": browser_session.replacement_epoch_id,
        "active_manifest_url": active_manifest_url,
        "attach_position_seconds": attach_position_seconds,
        "attach_ready": attach_ready,
        "browser_session_state": browser_session.state,
        "active_epoch_state": active_epoch.state if active_epoch is not None else None,
    }
