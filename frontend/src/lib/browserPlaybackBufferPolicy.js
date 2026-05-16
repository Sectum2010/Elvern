export const LITE_FAST_FORWARD_BUFFER_SECONDS = 15;
export const LITE_UNCERTAIN_FORWARD_BUFFER_SECONDS = 45;
export const LITE_UNDERSUPPLY_FORWARD_BUFFER_SECONDS = 180;
export const FULL_HEALTHY_FORWARD_BUFFER_SECONDS = 120;
export const FULL_BAD_CONDITION_FORWARD_BUFFER_SECONDS = 900;
export const AUDIO_SWITCH_FORWARD_BUFFER_SECONDS = 15;
export const CLIENT_BACK_BUFFER_SECONDS = 120;
export const PHONE_MAX_BUFFER_SIZE_BYTES = 250 * 1024 * 1024;
export const TABLET_MAX_BUFFER_SIZE_BYTES = 300 * 1024 * 1024;
export const DESKTOP_MAX_BUFFER_SIZE_BYTES = 3 * 1024 * 1024 * 1024;

const BUFFER_TIER_TARGETS = {
  lite_fast: LITE_FAST_FORWARD_BUFFER_SECONDS,
  lite_uncertain: LITE_UNCERTAIN_FORWARD_BUFFER_SECONDS,
  lite_undersupply: LITE_UNDERSUPPLY_FORWARD_BUFFER_SECONDS,
  full_healthy: FULL_HEALTHY_FORWARD_BUFFER_SECONDS,
  full_bad_condition: FULL_BAD_CONDITION_FORWARD_BUFFER_SECONDS,
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

function normalizeBufferTier(session = {}) {
  const explicitTier = typeof session?.buffer_tier === "string" ? session.buffer_tier : "";
  if (BUFFER_TIER_TARGETS[explicitTier]) {
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

export function deriveBufferTargetsFromSession(session = {}, deviceClass = "unknown") {
  const normalizedDeviceClass = normalizeDeviceClass(deviceClass);
  const bufferTier = normalizeBufferTier(session);
  let forwardBufferSeconds = BUFFER_TIER_TARGETS[bufferTier] || LITE_UNCERTAIN_FORWARD_BUFFER_SECONDS;
  if (bufferTier === "lite_uncertain") {
    forwardBufferSeconds = finitePositiveNumber(session?.lite_required_runway_seconds)
      || finitePositiveNumber(session?.client_recommended_forward_buffer_seconds)
      || LITE_UNCERTAIN_FORWARD_BUFFER_SECONDS;
  }
  if (bufferTier === "full_healthy" || bufferTier === "full_bad_condition") {
    forwardBufferSeconds = BUFFER_TIER_TARGETS[bufferTier];
  }

  return {
    forwardBufferSeconds,
    maxForwardBufferSeconds: forwardBufferSeconds,
    backBufferSeconds: CLIENT_BACK_BUFFER_SECONDS,
    maxBufferSizeBytes: maxBufferSizeForDevice(normalizedDeviceClass),
    bufferTier,
    memoryCeilingSource: `${normalizedDeviceClass}_fixed_byte_ceiling`,
    limitedByMemory: Boolean(session?.client_buffer_limited_by_memory),
    policySource: session?.client_buffer_policy_source || `frontend_${bufferTier}`,
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
  return deriveBufferTargetsFromSession(session, deviceClass).forwardBufferSeconds;
}

export function evaluateClientPlaybackReleaseGate({
  session = {},
  clientBufferedAheadSeconds = 0,
  backendPreparedAheadSeconds = 0,
  remainingPlayableSeconds = null,
  deviceClass = "unknown",
  audioSwitch = false,
} = {}) {
  const configuredClientBufferSeconds = resolveClientPlaybackReleaseBufferSeconds(
    session,
    deviceClass,
    { audioSwitch },
  );
  const remainingSeconds = finitePositiveNumber(remainingPlayableSeconds);
  const requiredClientBufferSeconds = remainingSeconds != null
    ? Math.min(configuredClientBufferSeconds, remainingSeconds)
    : configuredClientBufferSeconds;
  const clientAhead = Math.max(0, Number(clientBufferedAheadSeconds) || 0);
  const backendAhead = Math.max(0, Number(backendPreparedAheadSeconds) || 0);
  const clientReady = clientAhead + 0.001 >= requiredClientBufferSeconds;
  const serverReady = backendAhead + 0.001 >= requiredClientBufferSeconds;
  return {
    ready: Boolean(clientReady && serverReady),
    clientReady,
    serverReady,
    requiredClientBufferSeconds,
    configuredClientBufferSeconds,
    clientBufferedAheadSeconds: clientAhead,
    backendPreparedAheadSeconds: backendAhead,
  };
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
    || finitePositiveNumber(session?.client_recommended_forward_buffer_seconds)
    || BUFFER_TIER_TARGETS[normalizeBufferTier(session)]
    || LITE_UNCERTAIN_FORWARD_BUFFER_SECONDS;
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
