function normalizePositionSeconds(value) {
  const numeric = Number(value || 0);
  return Number.isFinite(numeric) ? Math.max(0, numeric) : 0;
}

function clampCompletedWindow(positionSeconds, durationSeconds, completionGraceSeconds) {
  const safePosition = normalizePositionSeconds(positionSeconds);
  const safeDuration = normalizePositionSeconds(durationSeconds);
  if (safeDuration > 0 && safePosition >= safeDuration - completionGraceSeconds) {
    return 0;
  }
  return safePosition;
}

export function resolveBrowserPlaybackResumePosition({
  progressPayload = null,
  fallbackResumePositionSeconds = 0,
  durationSeconds = 0,
  completionGraceSeconds = 15,
} = {}) {
  if (progressPayload && progressPayload.completed) {
    return 0;
  }
  const progressPositionSeconds = normalizePositionSeconds(progressPayload?.position_seconds);
  const fallbackResumeSeconds = normalizePositionSeconds(fallbackResumePositionSeconds);
  const candidatePositionSeconds = progressPositionSeconds > 0
    ? progressPositionSeconds
    : fallbackResumeSeconds;
  return clampCompletedWindow(candidatePositionSeconds, durationSeconds, completionGraceSeconds);
}

export function resolveAuthoritativeBrowserPlaybackResumePosition({
  progressPayload = null,
  durationSeconds = 0,
  completionGraceSeconds = 15,
} = {}) {
  if (!progressPayload || progressPayload.completed) {
    return 0;
  }
  return clampCompletedWindow(
    normalizePositionSeconds(progressPayload.position_seconds),
    durationSeconds,
    completionGraceSeconds,
  );
}
