export const LITE_FAST_SERVER_PREPARE_SECONDS = 15;
export const LITE_UNCERTAIN_SERVER_PREPARE_SECONDS = 45;
export const LITE_UNDERSUPPLY_SERVER_PREPARE_SECONDS = 180;
export const FULL_HEALTHY_SERVER_PREPARE_SECONDS = 120;
export const FULL_BAD_CONDITION_SERVER_PREPARE_SECONDS = 900;
export const LITE_CLIENT_REAL_CACHE_SECONDS = 15;
export const FULL_CLIENT_REAL_CACHE_SECONDS = 30;
export const AUDIO_SWITCH_FORWARD_BUFFER_SECONDS = 15;
export const CLIENT_BACK_BUFFER_SECONDS = 120;
export const AUTOMATIC_RECOVERY_BACKROLL_SECONDS = 2.5;
export const PHONE_MAX_BUFFER_SIZE_BYTES = 250 * 1024 * 1024;
export const TABLET_MAX_BUFFER_SIZE_BYTES = 300 * 1024 * 1024;
export const DESKTOP_MAX_BUFFER_SIZE_BYTES = 3 * 1024 * 1024 * 1024;

const AUTOMATIC_RECOVERY_CURRENT_TIME_TOLERANCE_SECONDS = 0.5;

const SERVER_PREPARE_TARGETS = {
  lite_fast: LITE_FAST_SERVER_PREPARE_SECONDS,
  lite_uncertain: LITE_UNCERTAIN_SERVER_PREPARE_SECONDS,
  lite_undersupply: LITE_UNDERSUPPLY_SERVER_PREPARE_SECONDS,
  full_healthy: FULL_HEALTHY_SERVER_PREPARE_SECONDS,
  full_bad_condition: FULL_BAD_CONDITION_SERVER_PREPARE_SECONDS,
};

function normalizeDeviceClass(deviceClass = "unknown") {
  if (deviceClass === "laptop") {
    return "desktop";
  }
  return ["phone", "tablet", "desktop"].includes(deviceClass) ? deviceClass : "desktop";
}

function maxBufferSizeForDevice(deviceClass = "unknown") {
  const normalized = normalizeDeviceClass(deviceClass);
  if (normalized === "phone") {
    return PHONE_MAX_BUFFER_SIZE_BYTES;
  }
  if (normalized === "tablet") {
    return TABLET_MAX_BUFFER_SIZE_BYTES;
  }
  return DESKTOP_MAX_BUFFER_SIZE_BYTES;
}

function finitePositiveNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
}

function finiteNonNegativeNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric >= 0 ? numeric : null;
}

function normalizeBufferTier(session = {}) {
  const explicitTier = typeof session?.buffer_tier === "string" ? session.buffer_tier : "";
  if (SERVER_PREPARE_TARGETS[explicitTier]) {
    return explicitTier;
  }
  const playbackMode = session?.playback_mode === "full" ? "full" : "lite";
  if (playbackMode === "full") {
    return session?.full_bad_condition_detected ? "full_bad_condition" : "full_healthy";
  }
  const source = String(session?.lite_required_runway_source || "").toLowerCase();
  if (session?.lite_undersupply_detected || source.includes("undersupply")) {
    return "lite_undersupply";
  }
  if (source.includes("healthy_fast")) {
    return "lite_fast";
  }
  return "lite_uncertain";
}

function playbackModeFromSession(session = {}) {
  const tier = String(session?.buffer_tier || "").toLowerCase();
  if (tier.startsWith("full_")) {
    return "full";
  }
  return session?.playback_mode === "full" ? "full" : "lite";
}

export function resolveServerPlaybackPrepareRunwaySeconds(session = {}) {
  const bufferTier = normalizeBufferTier(session);
  if (bufferTier === "full_bad_condition") {
    return finitePositiveNumber(session?.server_reserve_seconds)
      || finitePositiveNumber(session?.full_bad_condition_reserve_required_seconds)
      || SERVER_PREPARE_TARGETS.full_bad_condition;
  }
  if (bufferTier.startsWith("lite_")) {
    return finitePositiveNumber(session?.server_required_runway_seconds)
      || finitePositiveNumber(session?.lite_required_runway_seconds)
      || SERVER_PREPARE_TARGETS[bufferTier]
      || LITE_UNCERTAIN_SERVER_PREPARE_SECONDS;
  }
  return finitePositiveNumber(session?.server_required_runway_seconds)
    || SERVER_PREPARE_TARGETS[bufferTier]
    || FULL_HEALTHY_SERVER_PREPARE_SECONDS;
}

export function resolveClientPlaybackRealCacheSeconds(
  session = {},
  deviceClass = "unknown",
  { audioSwitch = false } = {},
) {
  normalizeDeviceClass(deviceClass);
  if (audioSwitch) {
    return AUDIO_SWITCH_FORWARD_BUFFER_SECONDS;
  }
  return playbackModeFromSession(session) === "full"
    ? FULL_CLIENT_REAL_CACHE_SECONDS
    : LITE_CLIENT_REAL_CACHE_SECONDS;
}

export function deriveBufferTargetsFromSession(session = {}, deviceClass = "unknown") {
  const normalizedDeviceClass = normalizeDeviceClass(deviceClass);
  const bufferTier = normalizeBufferTier(session);
  const serverPrepareSeconds = resolveServerPlaybackPrepareRunwaySeconds(session);
  const forwardBufferSeconds = resolveClientPlaybackRealCacheSeconds(session, normalizedDeviceClass);

  return {
    forwardBufferSeconds,
    maxForwardBufferSeconds: forwardBufferSeconds,
    clientCacheSeconds: forwardBufferSeconds,
    serverPrepareSeconds,
    backBufferSeconds: CLIENT_BACK_BUFFER_SECONDS,
    maxBufferSizeBytes: maxBufferSizeForDevice(normalizedDeviceClass),
    bufferTier,
    memoryCeilingSource: `${normalizedDeviceClass}_fixed_byte_ceiling`,
    limitedByMemory: Boolean(session?.client_buffer_limited_by_memory),
    policySource: session?.client_buffer_policy_source || `frontend_real_cache_${playbackModeFromSession(session)}`,
  };
}

export function resolveClientPlaybackReleaseBufferSeconds(
  session = {},
  deviceClass = "unknown",
  { audioSwitch = false } = {},
) {
  if (audioSwitch) {
    return AUDIO_SWITCH_FORWARD_BUFFER_SECONDS;
  }
  return resolveClientPlaybackRealCacheSeconds(session, deviceClass);
}

export function evaluateClientPlaybackReleaseGate({
  session = {},
  clientBufferedAheadSeconds = 0,
  backendPreparedAheadSeconds = 0,
  remainingPlayableSeconds = null,
  deviceClass = "unknown",
  audioSwitch = false,
} = {}) {
  const configuredServerPrepareSeconds = resolveServerPlaybackPrepareRunwaySeconds(session);
  const configuredClientCacheSeconds = resolveClientPlaybackReleaseBufferSeconds(
    session,
    deviceClass,
    { audioSwitch },
  );
  const remainingSeconds = finitePositiveNumber(remainingPlayableSeconds);
  const requiredServerPrepareSeconds = remainingSeconds != null
    ? Math.min(configuredServerPrepareSeconds, remainingSeconds)
    : configuredServerPrepareSeconds;
  const requiredClientCacheSeconds = remainingSeconds != null
    ? Math.min(configuredClientCacheSeconds, remainingSeconds)
    : configuredClientCacheSeconds;
  const clientAhead = Math.max(0, Number(clientBufferedAheadSeconds) || 0);
  const backendAhead = Math.max(0, Number(backendPreparedAheadSeconds) || 0);
  const clientReady = clientAhead + 0.001 >= requiredClientCacheSeconds;
  const serverReady = backendAhead + 0.001 >= requiredServerPrepareSeconds;
  return {
    ready: Boolean(clientReady && serverReady),
    clientReady,
    serverReady,
    configuredServerPrepareSeconds,
    requiredServerPrepareSeconds,
    configuredClientCacheSeconds,
    requiredClientCacheSeconds,
    requiredClientBufferSeconds: requiredClientCacheSeconds,
    configuredClientBufferSeconds: configuredClientCacheSeconds,
    clientBufferedAheadSeconds: clientAhead,
    backendPreparedAheadSeconds: backendAhead,
  };
}

export function shouldStartClientBufferPrewarm({
  iosMobile = false,
  hasMobileSession = false,
  hasAttachedSource = false,
  mobilePlayerCanPlay = false,
  playbackIntentActive = false,
  releaseGateReady = false,
  seekPending = false,
  retargetTransition = false,
  awaitingTargetSeek = false,
} = {}) {
  return Boolean(
    iosMobile
    && hasMobileSession
    && hasAttachedSource
    && !mobilePlayerCanPlay
    && playbackIntentActive
    && !releaseGateReady
    && !seekPending
    && !retargetTransition
    && !awaitingTargetSeek
  );
}

export function hasVideoFirstFrameForPlaybackRelease(
  video,
  {
    loadedDataSeen = false,
    canPlaySeen = false,
    frameReady = false,
  } = {},
) {
  if (!video) {
    return false;
  }
  const readyState = Number(video.readyState || 0);
  const width = Number(video.videoWidth || 0);
  const height = Number(video.videoHeight || 0);
  if (readyState < 2 || width <= 0 || height <= 0) {
    return false;
  }
  return Boolean(
    loadedDataSeen
    || canPlaySeen
    || frameReady
    || readyState >= 2
  );
}

export function muteVideoForClientPrewarm(video, previousAudioState = null) {
  if (!video) {
    return previousAudioState;
  }
  const audioState = previousAudioState || {
    muted: Boolean(video.muted),
    volume: Number.isFinite(video.volume) ? video.volume : 1,
  };
  video.muted = true;
  return audioState;
}

export function restoreVideoAfterClientPrewarm(video, previousAudioState = null) {
  if (!video || !previousAudioState) {
    return null;
  }
  video.muted = Boolean(previousAudioState.muted);
  try {
    video.volume = Number.isFinite(previousAudioState.volume)
      ? previousAudioState.volume
      : 1;
  } catch {
    // Some WebKit contexts reject volume writes; restoring muted state is enough.
  }
  return null;
}

export function buildHlsConfig({ session = {}, deviceClass = "unknown" } = {}) {
  const targets = deriveBufferTargetsFromSession(session, deviceClass);
  return {
    autoStartLoad: true,
    enableWorker: true,
    lowLatencyMode: false,
    maxBufferLength: targets.forwardBufferSeconds,
    maxMaxBufferLength: targets.maxForwardBufferSeconds,
    maxBufferSize: targets.maxBufferSizeBytes,
    backBufferLength: targets.backBufferSeconds,
    fragLoadingMaxRetry: 6,
    manifestLoadingMaxRetry: 4,
    levelLoadingMaxRetry: 4,
    nudgeMaxRetry: 5,
    bufferTier: targets.bufferTier,
    policySource: targets.policySource,
  };
}

export function compactHlsBufferConfig(config = {}) {
  return {
    maxBufferLength: config.maxBufferLength ?? null,
    maxMaxBufferLength: config.maxMaxBufferLength ?? null,
    maxBufferSize: config.maxBufferSize ?? null,
    backBufferLength: config.backBufferLength ?? null,
    fragLoadingMaxRetry: config.fragLoadingMaxRetry ?? null,
    manifestLoadingMaxRetry: config.manifestLoadingMaxRetry ?? null,
    levelLoadingMaxRetry: config.levelLoadingMaxRetry ?? null,
    nudgeMaxRetry: config.nudgeMaxRetry ?? null,
    lowLatencyMode: config.lowLatencyMode ?? null,
    autoStartLoad: config.autoStartLoad ?? null,
    enableWorker: config.enableWorker ?? null,
    bufferTier: config.bufferTier ?? null,
    policySource: config.policySource ?? null,
  };
}

export function retuneHlsInstance(hls, { session = {}, deviceClass = "unknown" } = {}) {
  if (!hls?.config) {
    return null;
  }
  const nextConfig = buildHlsConfig({ session, deviceClass });
  for (const key of [
    "maxBufferLength",
    "maxMaxBufferLength",
    "maxBufferSize",
    "backBufferLength",
    "fragLoadingMaxRetry",
    "manifestLoadingMaxRetry",
    "levelLoadingMaxRetry",
    "nudgeMaxRetry",
    "lowLatencyMode",
    "autoStartLoad",
    "enableWorker",
  ]) {
    hls.config[key] = nextConfig[key];
  }
  hls.config.bufferTier = nextConfig.bufferTier;
  hls.config.policySource = nextConfig.policySource;
  return nextConfig;
}

export function readClientBufferedAheadSeconds(video) {
  if (!video?.buffered) {
    return 0;
  }
  const currentTime = Number(video.currentTime || 0);
  const ranges = video.buffered;
  for (let index = 0; index < (ranges.length || 0); index += 1) {
    try {
      const start = ranges.start(index);
      const end = ranges.end(index);
      if (currentTime + 0.001 >= start && currentTime <= end + 0.001) {
        return Math.max(0, end - currentTime);
      }
    } catch {
      return 0;
    }
  }
  return 0;
}

export function readClientPlaybackLiveness(video, previousSample = null, nowMs = Date.now()) {
  const currentTimeSeconds = Number(video?.currentTime || 0);
  const readyState = Number(video?.readyState || 0);
  const networkState = Number(video?.networkState || 0);
  const elapsedMs = previousSample?.sampledAtMs != null
    ? Math.max(0, nowMs - previousSample.sampledAtMs)
    : 0;
  const deltaSeconds = previousSample?.currentTimeSeconds != null
    ? Math.max(0, currentTimeSeconds - previousSample.currentTimeSeconds)
    : 0;
  return {
    sampledAtMs: nowMs,
    currentTimeSeconds,
    readyState,
    networkState,
    paused: Boolean(video?.paused),
    bufferedAheadSeconds: readClientBufferedAheadSeconds(video),
    elapsedMs,
    currentTimeDeltaSeconds: deltaSeconds,
    timeAdvancing: elapsedMs > 0 ? deltaSeconds > 0.05 : null,
  };
}

export function resolveBackendPreparedAheadSeconds(session = {}) {
  const direct = finitePositiveNumber(session?.ahead_runway_seconds);
  if (direct != null) {
    return direct;
  }
  const readyEnd = Number(session?.ready_end_seconds);
  const target = Number(session?.target_position_seconds);
  if (Number.isFinite(readyEnd) && Number.isFinite(target)) {
    return Math.max(0, readyEnd - target);
  }
  return 0;
}

export function classifyPlaybackStall({
  session = {},
  livenessSample = {},
  targetForwardBufferSeconds = null,
  firstFrameWindowMs = 4000,
  firstFrameEligible = true,
} = {}) {
  const target = finitePositiveNumber(targetForwardBufferSeconds)
    || resolveClientPlaybackRealCacheSeconds(session);
  const elapsedMs = Number(livenessSample.elapsedMs || 0);
  const currentTimeDeltaSeconds = Number(livenessSample.currentTimeDeltaSeconds || 0);
  const clientBufferedAheadSeconds = Number(livenessSample.bufferedAheadSeconds || 0);
  const backendPreparedAheadSeconds = resolveBackendPreparedAheadSeconds(session);
  const paused = Boolean(livenessSample.paused);
  const timeStalled =
    !paused
    && elapsedMs >= firstFrameWindowMs
    && currentTimeDeltaSeconds < 0.5;
  const firstFrameStall = Boolean(firstFrameEligible && timeStalled);
  const midPlaybackStall = Boolean(!firstFrameEligible && timeStalled);
  const clientBufferSatisfiesTarget = clientBufferedAheadSeconds + 0.001 >= target;
  let stallReason = "";
  if (firstFrameStall) {
    stallReason = "first_frame_stall";
  } else if (midPlaybackStall) {
    stallReason = "mid_playback_stall";
  }
  if (!stallReason && backendPreparedAheadSeconds + 0.001 >= target && !clientBufferSatisfiesTarget) {
    stallReason = stallReason || "client_buffer_starved";
  } else if (!stallReason && backendPreparedAheadSeconds + 0.001 < target) {
    stallReason = stallReason || "backend_supply_waiting";
  }
  return {
    firstFrameStall,
    midPlaybackStall,
    stallReason,
    backendPreparedAheadSeconds,
    clientBufferedAheadSeconds,
    clientTargetForwardBufferSeconds: target,
    clientBufferSatisfiesTarget,
    currentTimeAdvancing: livenessSample.timeAdvancing,
  };
}

export function shouldDisarmFirstFrameStallMonitor({
  attachmentStartSeconds = 0,
  currentAbsolutePositionSeconds = 0,
  successfulTimeupdateCount = 0,
  advancingDurationMs = 0,
} = {}) {
  const start = Number(attachmentStartSeconds || 0);
  const current = Number(currentAbsolutePositionSeconds || 0);
  return Boolean(
    (Number.isFinite(current) && Number.isFinite(start) && current > start + 3)
    || Number(successfulTimeupdateCount || 0) >= 3
    || Number(advancingDurationMs || 0) >= 6000
  );
}

export function resolvePlaybackRecoveryTargetSeconds({
  currentAbsolutePositionSeconds = null,
  committedPlayheadSeconds = null,
  actualMediaElementTimeSeconds = null,
  targetPositionSeconds = null,
} = {}) {
  const candidates = [
    currentAbsolutePositionSeconds,
    committedPlayheadSeconds,
    actualMediaElementTimeSeconds,
    targetPositionSeconds,
  ]
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value) && value >= 0);
  return candidates.length ? Math.max(...candidates) : 0;
}

export function resolveAutomaticPlaybackRecoveryTarget({
  currentAbsolutePositionSeconds = null,
  lastStablePositionSeconds = null,
  committedPlayheadSeconds = null,
  actualMediaElementTimeSeconds = null,
  targetPositionSeconds = null,
  rollbackSeconds = AUTOMATIC_RECOVERY_BACKROLL_SECONDS,
  recoveryReason = "",
} = {}) {
  const currentAbsolutePosition = finiteNonNegativeNumber(currentAbsolutePositionSeconds);
  const lastStablePosition = finiteNonNegativeNumber(lastStablePositionSeconds);
  const committedPlayheadPosition = finiteNonNegativeNumber(committedPlayheadSeconds);
  const actualMediaElementPosition = finiteNonNegativeNumber(actualMediaElementTimeSeconds);
  const targetPosition = finiteNonNegativeNumber(targetPositionSeconds);
  const backrollSeconds = finiteNonNegativeNumber(rollbackSeconds) ?? AUTOMATIC_RECOVERY_BACKROLL_SECONDS;
  const stableCandidates = [
    ["last_stable", lastStablePosition],
    ["committed_playhead", committedPlayheadPosition],
    ["actual_media_element", actualMediaElementPosition],
  ];
  const stableAuthority = stableCandidates.find(([, value]) => value != null && value > 0);
  let authorityPosition = 0;
  let authoritySource = "zero_fallback";
  let avoidedForwardSkip = false;

  if (stableAuthority) {
    const [source, stablePosition] = stableAuthority;
    authorityPosition = stablePosition;
    authoritySource = source;
    if (currentAbsolutePosition != null) {
      if (currentAbsolutePosition <= stablePosition) {
        authorityPosition = currentAbsolutePosition;
        authoritySource = "current_absolute_not_ahead";
      } else if (
        currentAbsolutePosition > stablePosition + AUTOMATIC_RECOVERY_CURRENT_TIME_TOLERANCE_SECONDS
      ) {
        avoidedForwardSkip = true;
      }
    }
  } else if (currentAbsolutePosition != null) {
    authorityPosition = currentAbsolutePosition;
    authoritySource = "current_absolute_fallback";
  } else if (targetPosition != null) {
    authorityPosition = targetPosition;
    authoritySource = "target_fallback";
  }

  const targetBeforeBackrollSeconds = Math.max(0, authorityPosition);
  const targetAfterBackrollSeconds = Math.max(0, targetBeforeBackrollSeconds - backrollSeconds);
  return {
    recoveryReason,
    backrollSeconds,
    authorityPositionSeconds: targetBeforeBackrollSeconds,
    authoritySource,
    targetBeforeBackrollSeconds,
    targetAfterBackrollSeconds,
    targetSeconds: targetAfterBackrollSeconds,
    currentAbsolutePositionSeconds: currentAbsolutePosition,
    committedPlayheadSeconds: committedPlayheadPosition,
    actualMediaElementTimeSeconds: actualMediaElementPosition,
    lastStablePositionSeconds: lastStablePosition,
    avoidedForwardSkip,
  };
}

export function resolveAutomaticPlaybackRecoveryTargetSeconds(options = {}) {
  return resolveAutomaticPlaybackRecoveryTarget(options).targetAfterBackrollSeconds;
}

export function shouldRecoverNativeHlsStalePlaylist({
  hlsJsAttached = false,
  backendPreparedAheadSeconds = 0,
  stallReason = "",
} = {}) {
  const backendAhead = Number(backendPreparedAheadSeconds || 0);
  return Boolean(
    !hlsJsAttached
    && backendAhead > 6
    && (
      stallReason === "client_buffer_starved"
      || stallReason === "mid_playback_stall"
    )
  );
}

export function shouldStartVisibleHlsSupplyRecovery({
  session = {},
  livenessSample = {},
  seekPending = false,
  recoveryInFlight = false,
  lifecycleState = "attached",
  mobilePlayerCanPlay = false,
  videoPaused = false,
  hlsJsAttached = false,
  stalePlaylistStall = false,
} = {}) {
  if (
    seekPending
    || recoveryInFlight
    || lifecycleState !== "attached"
    || !mobilePlayerCanPlay
    || videoPaused
  ) {
    return { start: false, reason: "client_not_recoverable" };
  }
  const backendWantsRecovery = Boolean(
    session?.stalled_recovery_needed
    || stalePlaylistStall
  );
  if (!backendWantsRecovery) {
    return { start: false, reason: "backend_not_recovering" };
  }
  const bufferedAhead = Math.max(0, Number(livenessSample?.bufferedAheadSeconds || 0));
  const elapsedMs = Math.max(0, Number(livenessSample?.elapsedMs || 0));
  const currentTimeDeltaSeconds = Math.max(0, Number(livenessSample?.currentTimeDeltaSeconds || 0));
  const timeAdvancing = livenessSample?.timeAdvancing;
  const stallReason = String(livenessSample?.stallReason || "");
  const clientBufferEmpty = bufferedAhead <= 0.5;
  const timeConfirmedStopped = Boolean(
    elapsedMs >= 2000
    && timeAdvancing === false
    && currentTimeDeltaSeconds < 0.1
  );
  if (!clientBufferEmpty) {
    return { start: false, reason: "client_buffer_playable" };
  }
  if (!timeConfirmedStopped) {
    return { start: false, reason: "client_time_not_confirmed_stopped" };
  }
  return {
    start: true,
    reason: stalePlaylistStall && !hlsJsAttached
      ? "native_hls_playlist_starved"
      : (stallReason || "client_buffer_empty"),
  };
}

export function classifyManifestWindowState({
  absolutePositionSeconds = 0,
  manifestEndSeconds = 0,
  fullDurationSeconds = 0,
  completionGraceSeconds = 15,
  refreshRunwaySeconds = 12,
} = {}) {
  const absolutePosition = Number(absolutePositionSeconds || 0);
  const manifestEnd = Number(manifestEndSeconds || 0);
  const fullDuration = Number(fullDurationSeconds || 0);
  const completionGrace = Math.max(0, Number(completionGraceSeconds || 0));
  const refreshRunway = Math.max(0, Number(refreshRunwaySeconds || 0));
  const fullDurationKnown = Number.isFinite(fullDuration) && fullDuration > 0;
  const realCompletion = Boolean(
    fullDurationKnown
    && Number.isFinite(absolutePosition)
    && absolutePosition >= Math.max(0, fullDuration - completionGrace)
  );
  const nearManifestEnd = Boolean(
    Number.isFinite(absolutePosition)
    && Number.isFinite(manifestEnd)
    && manifestEnd > 0
    && absolutePosition >= Math.max(0, manifestEnd - refreshRunway)
  );
  return {
    realCompletion,
    manifestWindowExhausted: Boolean(nearManifestEnd && !realCompletion),
    shouldRefreshManifest: Boolean(nearManifestEnd && !realCompletion),
  };
}
