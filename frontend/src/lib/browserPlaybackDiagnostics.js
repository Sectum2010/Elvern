export const BROWSER_PLAYBACK_DIAGNOSTIC_MIN_INTERVAL_MS = 7000;

function finiteNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function stringOrNull(value) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function booleanOrNull(value) {
  return value == null ? null : Boolean(value);
}

function pickNumber(source, keys) {
  for (const key of keys) {
    const value = finiteNumber(source?.[key]);
    if (value != null) {
      return value;
    }
  }
  return null;
}

function pickString(source, keys) {
  for (const key of keys) {
    const value = stringOrNull(source?.[key]);
    if (value != null) {
      return value;
    }
  }
  return null;
}

function pickBoolean(source, keys) {
  for (const key of keys) {
    if (source?.[key] != null) {
      return Boolean(source[key]);
    }
  }
  return null;
}

function releaseGateReason(releaseGate, fallbackReason) {
  const fallback = stringOrNull(fallbackReason);
  if (fallback) {
    return fallback;
  }
  if (!releaseGate) {
    return null;
  }
  if (releaseGate.ready) {
    return "ready";
  }
  if (releaseGate.serverReady === false) {
    return "backend_prepared_ahead_below_required";
  }
  if (releaseGate.clientReady === false) {
    return "client_buffer_below_required";
  }
  return "client_release_gate_waiting";
}

export function buildBrowserPlaybackDiagnosticPayload({
  eventReason = "",
  session = null,
  video = null,
  releaseGate = null,
  livenessSample = null,
  clientPlaybackStallReason = "",
  mobilePlayerCanPlay = null,
  mobileLifecycleState = "",
  firstFrameReady = null,
  loadedDataSeen = null,
  canPlaySeen = null,
  frameReady = null,
  releaseGateReason: explicitReleaseGateReason = "",
  recoveryDecision = null,
  recoveryTarget = null,
  staleNativePlaylistStall = null,
} = {}) {
  const videoCurrentTimeSeconds = finiteNumber(video?.currentTime);
  const clientBufferedAheadSeconds =
    finiteNumber(releaseGate?.clientBufferedAheadSeconds)
    ?? finiteNumber(livenessSample?.bufferedAheadSeconds)
    ?? finiteNumber(session?.client_buffered_ahead_seconds);
  const clientReadyState =
    finiteNumber(livenessSample?.readyState)
    ?? finiteNumber(video?.readyState);
  const clientNetworkState =
    finiteNumber(livenessSample?.networkState)
    ?? finiteNumber(video?.networkState);
  const modeReady = session?.mode_ready != null
    ? Boolean(session.mode_ready)
    : booleanOrNull(session?.attach_ready);

  return {
    event_reason: stringOrNull(eventReason),
    session_id: pickString(session, ["session_id"]),
    playback_mode: pickString(session, ["playback_mode"]),
    engine_mode: pickString(session, ["engine_mode"]),
    mode_state: pickString(session, ["mode_state", "state", "status"]),
    mode_ready: modeReady,
    gate_reason: pickString(session, ["gate_reason"]),
    lite_required_runway_source: pickString(session, ["lite_required_runway_source", "buffer_tier"]),
    lite_required_runway_seconds: pickNumber(session, ["lite_required_runway_seconds"]),
    lite_undersupply_detected: pickBoolean(session, ["lite_undersupply_detected"]),
    lite_undersupply_reason: pickString(session, ["lite_undersupply_reason"]),
    required_startup_runway_seconds: pickNumber(session, [
      "required_startup_runway_seconds",
      "server_required_runway_seconds",
    ]),
    actual_startup_runway_seconds: pickNumber(session, ["actual_startup_runway_seconds"]),
    ready_start_seconds: pickNumber(session, ["ready_start_seconds"]),
    ready_end_seconds: pickNumber(session, ["ready_end_seconds"]),
    prepared_through_seconds: pickNumber(session, ["ready_end_seconds"]),
    duration_seconds: pickNumber(session, [
      "duration_seconds",
      "media_duration_seconds",
      "full_duration_seconds",
    ]),
    supply_rate_x: pickNumber(session, ["supply_rate_x"]),
    supply_observation_seconds: pickNumber(session, ["supply_observation_seconds"]),
    ahead_runway_seconds: pickNumber(session, ["ahead_runway_seconds"]),
    starvation_risk: pickBoolean(session, ["starvation_risk"]),
    stalled_recovery_needed: pickBoolean(session, ["stalled_recovery_needed"]),
    refill_in_progress: pickBoolean(session, ["refill_in_progress"]),
    attach_ready: pickBoolean(session, ["attach_ready"]),
    attach_revision: pickNumber(session, ["attach_revision"]),
    client_attach_revision: pickNumber(session, ["client_attach_revision"]),
    active_epoch_id: pickString(session, ["active_epoch_id", "epoch"]),
    active_epoch_state: pickString(session, ["active_epoch_state"]),
    active_manifest_url_exists: Boolean(session?.active_manifest_url),
    client_buffered_ahead_seconds: clientBufferedAheadSeconds,
    client_ready_state: clientReadyState,
    client_network_state: clientNetworkState,
    client_time_advancing: booleanOrNull(livenessSample?.timeAdvancing),
    client_playback_stall_reason:
      stringOrNull(clientPlaybackStallReason)
      ?? pickString(livenessSample, ["stallReason"]),
    mobilePlayerCanPlay: booleanOrNull(mobilePlayerCanPlay),
    mobileLifecycleState: stringOrNull(mobileLifecycleState),
    firstFrameReady: booleanOrNull(firstFrameReady),
    loadedDataSeen: booleanOrNull(loadedDataSeen),
    canPlaySeen: booleanOrNull(canPlaySeen),
    frameReady: booleanOrNull(frameReady),
    releaseGateReady: booleanOrNull(releaseGate?.ready),
    releaseGateReason: releaseGateReason(releaseGate, explicitReleaseGateReason),
    release_gate_client_ready: booleanOrNull(releaseGate?.clientReady),
    release_gate_server_ready: booleanOrNull(releaseGate?.serverReady),
    configured_server_prepare_seconds: finiteNumber(releaseGate?.configuredServerPrepareSeconds),
    required_server_prepare_seconds: finiteNumber(releaseGate?.requiredServerPrepareSeconds),
    configured_client_cache_seconds: finiteNumber(releaseGate?.configuredClientCacheSeconds),
    required_client_cache_seconds: finiteNumber(releaseGate?.requiredClientCacheSeconds),
    required_client_buffer_seconds:
      finiteNumber(releaseGate?.requiredClientCacheSeconds)
      ?? finiteNumber(releaseGate?.requiredClientBufferSeconds),
    configured_client_buffer_seconds:
      finiteNumber(releaseGate?.configuredClientCacheSeconds)
      ?? finiteNumber(releaseGate?.configuredClientBufferSeconds),
    backend_prepared_ahead_seconds:
      finiteNumber(releaseGate?.backendPreparedAheadSeconds)
      ?? finiteNumber(session?.ahead_runway_seconds),
    stale_native_playlist_stall: booleanOrNull(staleNativePlaylistStall),
    recovery_decision_start: booleanOrNull(recoveryDecision?.start),
    recovery_decision_reason: stringOrNull(recoveryDecision?.reason),
    recovery_reason: stringOrNull(recoveryTarget?.recoveryReason),
    recovery_backroll_seconds: finiteNumber(recoveryTarget?.backrollSeconds),
    recovery_authority_position_seconds: finiteNumber(recoveryTarget?.authorityPositionSeconds),
    recovery_target_before_backroll_seconds: finiteNumber(recoveryTarget?.targetBeforeBackrollSeconds),
    recovery_target_after_backroll_seconds: finiteNumber(recoveryTarget?.targetAfterBackrollSeconds),
    recovery_current_absolute_position_seconds: finiteNumber(recoveryTarget?.currentAbsolutePositionSeconds),
    recovery_committed_playhead_seconds: finiteNumber(recoveryTarget?.committedPlayheadSeconds),
    recovery_actual_media_element_time_seconds: finiteNumber(recoveryTarget?.actualMediaElementTimeSeconds),
    recovery_last_stable_position_seconds: finiteNumber(recoveryTarget?.lastStablePositionSeconds),
    recovery_avoided_forward_skip: booleanOrNull(recoveryTarget?.avoidedForwardSkip),
    video_current_time_seconds: videoCurrentTimeSeconds,
    video_ready_state: finiteNumber(video?.readyState),
    video_network_state: finiteNumber(video?.networkState),
    video_paused: booleanOrNull(video?.paused),
    video_video_width: finiteNumber(video?.videoWidth),
    video_video_height: finiteNumber(video?.videoHeight),
  };
}

export function buildBrowserPlaybackDiagnosticKey(eventName, payload = {}) {
  return [
    eventName,
    payload.session_id || "no-session",
    payload.event_reason || payload.releaseGateReason || payload.gate_reason || "unknown",
  ].join(":");
}

export function logBrowserPlaybackDiagnostic({
  eventName,
  payload,
  lastLogMap,
  nowMs = Date.now(),
  minIntervalMs = BROWSER_PLAYBACK_DIAGNOSTIC_MIN_INTERVAL_MS,
  consoleRef = console,
} = {}) {
  if (!eventName || !payload || !lastLogMap || typeof consoleRef?.debug !== "function") {
    return false;
  }
  const key = buildBrowserPlaybackDiagnosticKey(eventName, payload);
  const lastLoggedAt = Number(lastLogMap.get(key) || 0);
  if (lastLoggedAt && nowMs - lastLoggedAt < minIntervalMs) {
    return false;
  }
  lastLogMap.set(key, nowMs);
  consoleRef.debug(eventName, payload);
  return true;
}
