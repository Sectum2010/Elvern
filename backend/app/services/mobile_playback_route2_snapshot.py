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
from .mobile_playback_buffer_contract import resolve_buffer_contract_fields
from .route2_native_hls_window import build_active_window_snapshot_fields


def _route2_snapshot_locked(
    session: MobilePlaybackSession,
    *,
    route2_attach_gate_state_locked,
    route2_display_prepare_eta_locked,
    route2_epoch_recovery_ready_locked,
    route2_epoch_startup_attach_gate_locked,
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
    required_startup_runway_seconds = None
    actual_startup_runway_seconds = None
    effective_goodput_ratio = None
    gate_reason = "none"
    lite_undersupply_runway_seconds = None
    lite_undersupply_detected = False
    lite_undersupply_reason = None
    lite_required_runway_seconds = None
    lite_required_runway_source = None
    lite_threshold_fields: dict[str, object] = {
        "lite_threshold_decider_state": None,
        "lite_threshold_previous_tier": None,
        "lite_threshold_candidate_tier": None,
        "lite_threshold_confirmed_tier": None,
        "lite_threshold_decider_reason": None,
        "lite_positive_evidence_seconds": None,
        "lite_negative_evidence_seconds": None,
        "lite_frontier_sample_count": None,
        "lite_frontier_growth_rate_x": None,
        "lite_effective_supply_rate_x": None,
        "lite_supply_fast_ema_rate_x": None,
        "lite_supply_slow_ema_rate_x": None,
        "lite_supply_median_rate_x": None,
        "lite_hysteresis_hold_reason": None,
        "lite_cold_start_hold": False,
        "lite_post_recovery_hold": False,
        "lite_post_seek_hold": False,
    }
    full_bad_condition_fields: dict[str, object] = {
        "full_bad_condition_detected": False,
        "full_bad_condition_reason": None,
        "full_bad_condition_reasons": [],
        "full_bad_condition_confidence": "none",
        "full_bad_condition_mature": False,
        "full_bad_condition_reserve_required_seconds": None,
        "full_bad_condition_reserve_target_seconds": None,
        "full_bad_condition_actual_contiguous_end_seconds": None,
        "full_bad_condition_actual_contiguous_seconds_after_target": None,
        "full_bad_condition_reserve_remaining_seconds": None,
        "full_bad_condition_reserve_satisfied": False,
        "full_bad_condition_reserve_progress_source": None,
        "full_bad_condition_reserve_eta_seconds": None,
        "full_bad_condition_gate_enabled": False,
        "full_bad_condition_gate_dry_run_enabled": True,
        "full_bad_condition_gate_would_block_ready": False,
        "full_bad_condition_gate_blocks_ready": False,
        "full_bad_condition_gate_blockers": [],
    }
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
        startup_gate = route2_epoch_startup_attach_gate_locked(session, active_epoch)
        startup_attach_ready = bool(startup_gate["ready"])
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
        if session.lifecycle_state not in {"resuming", "recovering"}:
            required_startup_runway_seconds = startup_gate.get("required_startup_runway_seconds")
            actual_startup_runway_seconds = startup_gate.get("actual_startup_runway_seconds")
            effective_goodput_ratio = startup_gate.get("effective_goodput_ratio")
            gate_reason = str(startup_gate.get("gate_reason") or "none")
            lite_undersupply_runway_seconds = startup_gate.get("lite_undersupply_runway_seconds")
            lite_undersupply_detected = bool(startup_gate.get("lite_undersupply_detected") or False)
            lite_undersupply_reason = startup_gate.get("lite_undersupply_reason")
            lite_required_runway_seconds = startup_gate.get("lite_required_runway_seconds")
            lite_required_runway_source = startup_gate.get("lite_required_runway_source")
            for key in lite_threshold_fields:
                if key in startup_gate:
                    lite_threshold_fields[key] = startup_gate[key]
            if (
                browser_session.playback_mode == "lite"
                and browser_session.client_attach_revision == 0
                and startup_gate.get("estimate_seconds") is not None
            ):
                prepare_estimate_seconds = float(startup_gate["estimate_seconds"])
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
        required_startup_runway_seconds = full_mode_gate.get("required_startup_runway_seconds")
        actual_startup_runway_seconds = full_mode_gate.get("actual_startup_runway_seconds")
        effective_goodput_ratio = full_mode_gate.get("effective_goodput_ratio")
        gate_reason = str(full_mode_gate.get("gate_reason") or gate_reason)
        prepare_estimate_seconds = mode_estimate_seconds
        for key in full_bad_condition_fields:
            if key in full_mode_gate:
                full_bad_condition_fields[key] = full_mode_gate[key]
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
    buffer_contract_fields = resolve_buffer_contract_fields(
        playback_mode=browser_session.playback_mode,
        client_device_class=session.client_device_class,
        required_startup_runway_seconds=required_startup_runway_seconds,
        full_bad_condition_detected=bool(full_bad_condition_fields["full_bad_condition_detected"]),
        full_bad_condition_reserve_required_seconds=full_bad_condition_fields[
            "full_bad_condition_reserve_required_seconds"
        ],
        lite_required_runway_seconds=lite_required_runway_seconds,
        lite_required_runway_source=lite_required_runway_source,
        lite_undersupply_detected=lite_undersupply_detected,
    )
    active_window_fields = build_active_window_snapshot_fields(
        selected_hls_engine=session.selected_hls_engine,
        duration_seconds=session.duration_seconds,
        buffer_tier=str(buffer_contract_fields.get("buffer_tier") or ""),
        playback_mode=browser_session.playback_mode,
        current_position_seconds=session.client_current_time_seconds,
        target_position_seconds=session.target_position_seconds,
        attach_position_seconds=attach_position_seconds,
        active_window_revision=max(
            browser_session.last_emitted_window_revision,
            browser_session.attach_revision,
        ),
        active_window_reason=(
            browser_session.last_emitted_window_reason or session.lifecycle_state
        ),
    )
    # Phase 2B: when the orchestrator has actually emitted a sliding window
    # (so the .m3u8 is already sliced), surface those persisted bounds so the
    # snapshot's active_window_* fields and the manifest bytes always agree.
    if browser_session.last_emitted_window_initialized:
        active_window_fields = dict(active_window_fields)
        active_window_fields["active_window_start_seconds"] = round(
            browser_session.last_emitted_window_start_seconds, 2,
        )
        active_window_fields["active_window_end_seconds"] = round(
            browser_session.last_emitted_window_end_seconds, 2,
        )
        active_window_fields["active_window_anchor_seconds"] = round(
            browser_session.last_emitted_window_anchor_seconds, 2,
        )
        if browser_session.last_emitted_window_back_seconds > 0:
            active_window_fields["active_window_back_seconds"] = round(
                browser_session.last_emitted_window_back_seconds, 2,
            )
        if browser_session.last_emitted_window_forward_seconds > 0:
            active_window_fields["active_window_forward_seconds"] = round(
                browser_session.last_emitted_window_forward_seconds, 2,
            )
    audio_switch_replacement_epoch = (
        replacement_epoch
        if replacement_epoch is not None and replacement_epoch.replacement_reason == "audio_track_switch"
        else None
    )
    audio_switch_replacement_ready_end_seconds = (
        route2_epoch_ready_end_seconds(session, audio_switch_replacement_epoch)
        if audio_switch_replacement_epoch is not None
        else None
    )
    audio_switch_replacement_audio_stream_index = (
        audio_switch_replacement_epoch.audio_stream_index
        if audio_switch_replacement_epoch is not None
        else None
    )
    audio_switch_replacement_audio_map = (
        f"0:{int(audio_switch_replacement_audio_stream_index)}?"
        if audio_switch_replacement_audio_stream_index is not None
        else None
    )
    audio_switch_candidate_epoch = (
        browser_session.epochs.get(browser_session.audio_switch_candidate_epoch_id)
        if browser_session.audio_switch_candidate_epoch_id
        else None
    )
    if (
        audio_switch_candidate_epoch is None
        and audio_switch_replacement_epoch is not None
        and browser_session.audio_switch_state in {"candidate_preparing", "candidate_ready", "committing"}
    ):
        audio_switch_candidate_epoch = audio_switch_replacement_epoch
    audio_switch_candidate_ready_end_seconds = (
        route2_epoch_ready_end_seconds(session, audio_switch_candidate_epoch)
        if audio_switch_candidate_epoch is not None
        else None
    )
    audio_switch_candidate_stream_index = (
        browser_session.audio_switch_candidate_stream_index
        if browser_session.audio_switch_candidate_stream_index is not None
        else audio_switch_candidate_epoch.audio_stream_index
        if audio_switch_candidate_epoch is not None
        else None
    )
    audio_switch_candidate_manifest_url = (
        f"/api/mobile-playback/epochs/{audio_switch_candidate_epoch.epoch_id}/index.m3u8"
        if audio_switch_candidate_epoch is not None
        else None
    )
    old_epoch_retained = False
    old_epoch_retention_seconds = None
    previous_epoch = (
        browser_session.epochs.get(browser_session.audio_switch_previous_epoch_id)
        if browser_session.audio_switch_previous_epoch_id
        else None
    )
    if previous_epoch is not None and previous_epoch.state == "draining":
        old_epoch_retained = True
        if previous_epoch.drain_started_at_ts:
            old_epoch_retention_seconds = max(0.0, now_ts - previous_epoch.drain_started_at_ts)
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
        "required_startup_runway_seconds": round(float(required_startup_runway_seconds), 2)
        if required_startup_runway_seconds is not None
        else None,
        "actual_startup_runway_seconds": round(float(actual_startup_runway_seconds), 2)
        if actual_startup_runway_seconds is not None
        else None,
        "effective_goodput_ratio": round(float(effective_goodput_ratio), 3)
        if effective_goodput_ratio is not None
        else None,
        "gate_reason": gate_reason,
        "lite_undersupply_runway_seconds": round(float(lite_undersupply_runway_seconds), 2)
        if lite_undersupply_runway_seconds is not None
        else None,
        "lite_undersupply_detected": lite_undersupply_detected,
        "lite_undersupply_reason": lite_undersupply_reason,
        "lite_required_runway_seconds": round(float(lite_required_runway_seconds), 2)
        if lite_required_runway_seconds is not None
        else None,
        "lite_required_runway_source": lite_required_runway_source,
        "lite_threshold_decider_state": lite_threshold_fields["lite_threshold_decider_state"],
        "lite_threshold_previous_tier": lite_threshold_fields["lite_threshold_previous_tier"],
        "lite_threshold_candidate_tier": lite_threshold_fields["lite_threshold_candidate_tier"],
        "lite_threshold_confirmed_tier": lite_threshold_fields["lite_threshold_confirmed_tier"],
        "lite_threshold_decider_reason": lite_threshold_fields["lite_threshold_decider_reason"],
        "lite_positive_evidence_seconds": round(float(lite_threshold_fields["lite_positive_evidence_seconds"]), 2)
        if lite_threshold_fields["lite_positive_evidence_seconds"] is not None
        else None,
        "lite_negative_evidence_seconds": round(float(lite_threshold_fields["lite_negative_evidence_seconds"]), 2)
        if lite_threshold_fields["lite_negative_evidence_seconds"] is not None
        else None,
        "lite_frontier_sample_count": lite_threshold_fields["lite_frontier_sample_count"],
        "lite_frontier_growth_rate_x": round(float(lite_threshold_fields["lite_frontier_growth_rate_x"]), 3)
        if lite_threshold_fields["lite_frontier_growth_rate_x"] is not None
        else None,
        "lite_effective_supply_rate_x": round(float(lite_threshold_fields["lite_effective_supply_rate_x"]), 3)
        if lite_threshold_fields["lite_effective_supply_rate_x"] is not None
        else None,
        "lite_supply_fast_ema_rate_x": round(float(lite_threshold_fields["lite_supply_fast_ema_rate_x"]), 3)
        if lite_threshold_fields["lite_supply_fast_ema_rate_x"] is not None
        else None,
        "lite_supply_slow_ema_rate_x": round(float(lite_threshold_fields["lite_supply_slow_ema_rate_x"]), 3)
        if lite_threshold_fields["lite_supply_slow_ema_rate_x"] is not None
        else None,
        "lite_supply_median_rate_x": round(float(lite_threshold_fields["lite_supply_median_rate_x"]), 3)
        if lite_threshold_fields["lite_supply_median_rate_x"] is not None
        else None,
        "lite_hysteresis_hold_reason": lite_threshold_fields["lite_hysteresis_hold_reason"],
        "lite_cold_start_hold": bool(lite_threshold_fields["lite_cold_start_hold"]),
        "lite_post_recovery_hold": bool(lite_threshold_fields["lite_post_recovery_hold"]),
        "lite_post_seek_hold": bool(lite_threshold_fields["lite_post_seek_hold"]),
        **buffer_contract_fields,
        **active_window_fields,
        "client_buffered_ahead_seconds": round(float(session.client_buffered_ahead_seconds), 2)
        if session.client_buffered_ahead_seconds is not None
        else None,
        "client_target_forward_buffer_seconds": round(float(session.client_target_forward_buffer_seconds), 2)
        if session.client_target_forward_buffer_seconds is not None
        else None,
        "client_ready_state": session.client_ready_state,
        "client_network_state": session.client_network_state,
        "client_current_time_seconds": round(float(session.client_current_time_seconds), 2)
        if session.client_current_time_seconds is not None
        else None,
        "client_time_advancing": session.client_time_advancing,
        "client_playback_stall_reason": session.client_playback_stall_reason,
        "hls_js_config": session.hls_js_config,
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
        "selected_audio_stream_index": browser_session.selected_audio_stream_index,
        "active_audio_stream_index": browser_session.active_audio_stream_index,
        "pending_audio_stream_index": browser_session.pending_audio_stream_index,
        "audio_switch_state": browser_session.audio_switch_state,
        "audio_switch_error": browser_session.audio_switch_error,
        "audio_switch_replacement_epoch_id": (
            audio_switch_replacement_epoch.epoch_id if audio_switch_replacement_epoch is not None else None
        ),
        "audio_switch_replacement_state": (
            audio_switch_replacement_epoch.state if audio_switch_replacement_epoch is not None else None
        ),
        "audio_switch_replacement_reason": (
            audio_switch_replacement_epoch.replacement_reason if audio_switch_replacement_epoch is not None else None
        ),
        "audio_switch_replacement_audio_stream_index": audio_switch_replacement_audio_stream_index,
        "audio_switch_replacement_audio_map": audio_switch_replacement_audio_map,
        "audio_switch_replacement_last_error": (
            audio_switch_replacement_epoch.last_error if audio_switch_replacement_epoch is not None else None
        ),
        "audio_switch_replacement_ready_end_seconds": (
            round(float(audio_switch_replacement_ready_end_seconds), 2)
            if audio_switch_replacement_ready_end_seconds is not None
            else None
        ),
        "audio_switch_replacement_attach_position_seconds": (
            round(float(audio_switch_replacement_epoch.attach_position_seconds), 2)
            if audio_switch_replacement_epoch is not None
            else None
        ),
        "audio_switch_candidate_epoch_id": (
            audio_switch_candidate_epoch.epoch_id if audio_switch_candidate_epoch is not None else None
        ),
        "audio_switch_candidate_state": browser_session.audio_switch_candidate_state,
        "audio_switch_candidate_stream_index": audio_switch_candidate_stream_index,
        "audio_switch_candidate_error": browser_session.audio_switch_candidate_error,
        "audio_switch_candidate_manifest_url": audio_switch_candidate_manifest_url,
        "audio_switch_candidate_ready_end_seconds": (
            round(float(audio_switch_candidate_ready_end_seconds), 2)
            if audio_switch_candidate_ready_end_seconds is not None
            else None
        ),
        "audio_switch_candidate_attach_position_seconds": (
            round(float(audio_switch_candidate_epoch.attach_position_seconds), 2)
            if audio_switch_candidate_epoch is not None
            else None
        ),
        "audio_switch_candidate_expires_at": (
            round(float(browser_session.audio_switch_candidate_expires_at_ts), 3)
            if browser_session.audio_switch_candidate_expires_at_ts > 0
            else None
        ),
        "audio_switch_requires_commit": bool(
            audio_switch_candidate_epoch is not None
            and browser_session.audio_switch_candidate_state == "ready"
        ),
        "audio_switch_commit_url": f"/api/mobile-playback/sessions/{session.session_id}/audio/commit",
        "audio_switch_cancel_url": f"/api/mobile-playback/sessions/{session.session_id}/audio/cancel",
        "audio_switch_previous_epoch_id": browser_session.audio_switch_previous_epoch_id,
        "audio_switch_previous_audio_stream_index": browser_session.audio_switch_previous_audio_stream_index,
        "old_epoch_retained": old_epoch_retained,
        "old_epoch_retention_seconds": (
            round(float(old_epoch_retention_seconds), 3)
            if old_epoch_retention_seconds is not None
            else None
        ),
        **full_bad_condition_fields,
    }
