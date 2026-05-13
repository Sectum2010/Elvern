export const VIDEO_FIT_MODE_STORAGE_KEY = "elvern_video_fit_mode";
export const VIDEO_FIT_PINCH_THRESHOLD_PX = 28;
export const VIDEO_FIT_STANDARD = "standard-fit";
export const VIDEO_FIT_ZOOM_FILL = "zoom-fill";

export function normalizeVideoFitMode(value) {
  if (value === VIDEO_FIT_ZOOM_FILL || value === "fill-cover" || value === "fill") {
    return VIDEO_FIT_ZOOM_FILL;
  }
  return VIDEO_FIT_STANDARD;
}

export function readStoredVideoFitMode(storage = globalThis?.localStorage) {
  void storage;
  return VIDEO_FIT_STANDARD;
}

export function persistVideoFitMode(mode, storage = globalThis?.localStorage) {
  void mode;
  void storage;
}

export function measureTouchDistance(touches) {
  if (!touches || touches.length < 2) {
    return null;
  }
  const first = touches[0];
  const second = touches[1];
  if (!first || !second) {
    return null;
  }
  const dx = Number(second.clientX) - Number(first.clientX);
  const dy = Number(second.clientY) - Number(first.clientY);
  if (!Number.isFinite(dx) || !Number.isFinite(dy)) {
    return null;
  }
  return Math.hypot(dx, dy);
}

export function deriveVideoFitModeFromPinch({
  startDistance,
  currentDistance,
  currentMode = VIDEO_FIT_STANDARD,
  thresholdPx = VIDEO_FIT_PINCH_THRESHOLD_PX,
}) {
  if (!Number.isFinite(startDistance) || !Number.isFinite(currentDistance)) {
    return normalizeVideoFitMode(currentMode);
  }
  const threshold = Number.isFinite(thresholdPx) && thresholdPx > 0
    ? thresholdPx
    : VIDEO_FIT_PINCH_THRESHOLD_PX;
  const delta = currentDistance - startDistance;
  if (delta >= threshold) {
    return VIDEO_FIT_ZOOM_FILL;
  }
  if (delta <= -threshold) {
    return VIDEO_FIT_STANDARD;
  }
  return normalizeVideoFitMode(currentMode);
}

export function deriveVideoFitModeGestureChange({
  gesture,
  currentDistance,
  thresholdPx = VIDEO_FIT_PINCH_THRESHOLD_PX,
}) {
  if (!gesture || gesture.hasCommittedModeChange) {
    return {
      changed: false,
      hasCommittedModeChange: Boolean(gesture?.hasCommittedModeChange),
      nextMode: normalizeVideoFitMode(gesture?.startMode),
    };
  }
  const startMode = normalizeVideoFitMode(gesture.startMode);
  const nextMode = deriveVideoFitModeFromPinch({
    startDistance: gesture.startDistance,
    currentDistance,
    currentMode: startMode,
    thresholdPx,
  });
  const changed = nextMode !== startMode;
  return {
    changed,
    hasCommittedModeChange: changed,
    nextMode,
  };
}
