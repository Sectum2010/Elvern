export const VIDEO_FIT_MODE_STORAGE_KEY = "elvern_video_fit_mode";
export const VIDEO_FIT_PINCH_THRESHOLD_PX = 28;
export const VIDEO_FIT_DEFAULT = "default-fit";
export const VIDEO_FIT_FILL_COVER = "fill-cover";

export function normalizeVideoFitMode(value) {
  return value === VIDEO_FIT_FILL_COVER || value === "fill" ? VIDEO_FIT_FILL_COVER : VIDEO_FIT_DEFAULT;
}

export function readStoredVideoFitMode(storage = globalThis?.localStorage) {
  void storage;
  return VIDEO_FIT_DEFAULT;
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
  currentMode = VIDEO_FIT_DEFAULT,
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
    return VIDEO_FIT_FILL_COVER;
  }
  if (delta <= -threshold) {
    return VIDEO_FIT_DEFAULT;
  }
  return normalizeVideoFitMode(currentMode);
}
