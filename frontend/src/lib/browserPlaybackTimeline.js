function coerceNonNegativeNumber(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return 0;
  }
  return numeric;
}

function isHlsSessionPayload(payload) {
  return payload?.engine_mode === "route2";
}

export function getBrowserPlaybackTimelineStartSeconds(payload) {
  if (!isHlsSessionPayload(payload)) {
    return 0;
  }
  return coerceNonNegativeNumber(
    payload?.ready_start_seconds ?? payload?.manifest_start_seconds ?? 0,
  );
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
  const readyStart = getBrowserPlaybackTimelineStartSeconds(payload);
  const readyEnd = Math.max(
    readyStart,
    getBrowserPlaybackTimelineEndSeconds(payload) - Math.max(0, Number(headroomSeconds || 0)),
  );
  return absolute >= readyStart && absolute <= readyEnd;
}
