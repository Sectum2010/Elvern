function coerceNonNegativeNumber(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return 0;
  }
  return numeric;
}

function coerceFiniteNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function isHlsSessionPayload(payload) {
  return payload?.engine_mode === "route2";
}

function hasActiveWindowFields(payload) {
  if (!payload) {
    return false;
  }
  const start = coerceFiniteNumber(payload.active_window_start_seconds);
  const end = coerceFiniteNumber(payload.active_window_end_seconds);
  return start !== null && end !== null && end > start;
}

export function getBrowserPlaybackActiveWindowSeconds(payload) {
  if (!hasActiveWindowFields(payload)) {
    return null;
  }
  const start = Math.max(0, coerceFiniteNumber(payload.active_window_start_seconds) ?? 0);
  const end = Math.max(
    start,
    coerceFiniteNumber(payload.active_window_end_seconds) ?? start,
  );
  return { startSeconds: start, endSeconds: end };
}

export function isNativeHlsWindowPayload(payload) {
  return payload?.selected_hls_engine === "native_hls"
    || payload?.native_hls_window_policy === "native_hls_sliding_window_v1";
}

export function getBrowserPlaybackFullDurationSeconds(payload) {
  const explicitFull = coerceFiniteNumber(payload?.full_duration_seconds);
  if (explicitFull !== null && explicitFull > 0) {
    return explicitFull;
  }
  return coerceNonNegativeNumber(payload?.duration_seconds);
}

export function getBrowserPlaybackTimelineStartSeconds(payload) {
  if (!isHlsSessionPayload(payload)) {
    return 0;
  }
  return coerceNonNegativeNumber(
    payload?.ready_start_seconds ?? payload?.manifest_start_seconds ?? 0,
  );
}

export function getBrowserPlaybackAttachedManifestStartSeconds(payload) {
  if (!isHlsSessionPayload(payload)) {
    return 0;
  }
  const activeWindow = getBrowserPlaybackActiveWindowSeconds(payload);
  if (isNativeHlsWindowPayload(payload) && activeWindow) {
    return activeWindow.startSeconds;
  }
  return getBrowserPlaybackTimelineStartSeconds(payload);
}

export function getBrowserPlaybackTimelineEndSeconds(payload) {
  if (!isHlsSessionPayload(payload)) {
    return 0;
  }
  return Math.max(
    getBrowserPlaybackTimelineStartSeconds(payload),
    coerceNonNegativeNumber(
      payload?.ready_end_seconds ?? payload?.manifest_end_seconds ?? 0,
    ),
  );
}

export function getBrowserPlaybackAttachedManifestEndSeconds(payload) {
  if (!isHlsSessionPayload(payload)) {
    return 0;
  }
  const activeWindow = getBrowserPlaybackActiveWindowSeconds(payload);
  if (isNativeHlsWindowPayload(payload) && activeWindow) {
    return activeWindow.endSeconds;
  }
  return getBrowserPlaybackTimelineEndSeconds(payload);
}

export function shouldForceReattachForManifestWindowRefresh(payload) {
  if (!isHlsSessionPayload(payload)) {
    return true;
  }
  return !isNativeHlsWindowPayload(payload);
}

export function toBrowserPlaybackMediaElementSeconds(payload, absoluteSeconds) {
  const absolute = coerceNonNegativeNumber(absoluteSeconds);
  if (!isHlsSessionPayload(payload)) {
    return absolute;
  }
  return Math.max(0, absolute - getBrowserPlaybackTimelineStartSeconds(payload));
}

export function toBrowserPlaybackAbsoluteSeconds(payload, mediaElementSeconds) {
  const mediaElementTime = coerceNonNegativeNumber(mediaElementSeconds);
  if (!isHlsSessionPayload(payload)) {
    return mediaElementTime;
  }
  return getBrowserPlaybackTimelineStartSeconds(payload) + mediaElementTime;
}

export function isBrowserPlaybackAbsolutePositionReady(
  payload,
  absoluteSeconds,
  {
    headroomSeconds = 0,
  } = {},
) {
  if (!isHlsSessionPayload(payload)) {
    return true;
  }
  const absolute = coerceNonNegativeNumber(absoluteSeconds);
  const safeHeadroom = Math.max(0, Number(headroomSeconds || 0));
  const activeWindow = getBrowserPlaybackActiveWindowSeconds(payload);
  if (activeWindow) {
    const windowEnd = Math.max(activeWindow.startSeconds, activeWindow.endSeconds - safeHeadroom);
    return absolute >= activeWindow.startSeconds && absolute <= windowEnd;
  }
  const readyStart = getBrowserPlaybackTimelineStartSeconds(payload);
  const readyEnd = Math.max(
    readyStart,
    getBrowserPlaybackTimelineEndSeconds(payload) - safeHeadroom,
  );
  return absolute >= readyStart && absolute <= readyEnd;
}
