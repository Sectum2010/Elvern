function normalizeTitle(value, fallback = "This movie") {
  const normalized = typeof value === "string" ? value.trim() : "";
  return normalized || fallback;
}

export function getActivePlaybackWorkerConflict(error) {
  const detail = error?.detail || error?.payload?.detail || null;
  if (!detail || typeof detail !== "object" || detail.code !== "active_playback_worker_exists") {
    return null;
  }
  return {
    code: "active_playback_worker_exists",
    activeMovieTitle: normalizeTitle(detail.active_movie_title),
    activeMediaItemId: Number(detail.active_media_item_id || 0),
    activePlaybackMode: String(detail.active_playback_mode || "lite"),
    activeWorkerId: typeof detail.active_worker_id === "string" ? detail.active_worker_id : "",
    activeSessionId: typeof detail.active_session_id === "string" ? detail.active_session_id : "",
    message: normalizeTitle(detail.message, "This movie is still preparing."),
  };
}

export function getPlaybackWorkerCooldown(error) {
  const detail = error?.detail || error?.payload?.detail || null;
  if (!detail || typeof detail !== "object" || detail.code !== "playback_worker_cooldown") {
    return null;
  }
  const remainingSeconds = Number(detail.remaining_seconds || 0);
  const message =
    typeof detail.message === "string" && detail.message.trim()
      ? detail.message.trim()
      : `Your current quota for this movie has been reached. Please try again in ${Math.max(1, Math.ceil(remainingSeconds || 0))} seconds.`;
  return {
    code: "playback_worker_cooldown",
    mediaItemId: Number(detail.media_item_id || 0),
    remainingSeconds: Number.isFinite(remainingSeconds) ? remainingSeconds : 0,
    message,
  };
}

export function getPlaybackAdmissionError(error) {
  const detail = error?.detail || error?.payload?.detail || null;
  if (!detail || typeof detail !== "object") {
    return null;
  }
  const code = typeof detail.code === "string" ? detail.code : "";
  if (!["same_user_active_playback_limit", "server_max_capacity", "provider_source_error"].includes(code)) {
    return null;
  }
  const fallbackMessage = code === "same_user_active_playback_limit"
    ? "You already have an active playback. Stop it or switch before starting another."
    : code === "provider_source_error"
      ? "Playback source is unavailable. Reconnect the provider or try again later."
      : "Server is busy. Please try again later.";
  const message = typeof detail.message === "string" && detail.message.trim()
    ? detail.message.trim()
    : fallbackMessage;
  return {
    code,
    reasonCode: typeof detail.reason_code === "string" ? detail.reason_code : "",
    message,
  };
}

export function buildActivePlaybackConflictPrompt(activeMovieTitle, newMovieTitle) {
  const activeTitle = normalizeTitle(activeMovieTitle);
  const requestedTitle = normalizeTitle(newMovieTitle);
  if (activeTitle === requestedTitle) {
    return `${activeTitle} is still preparing. Terminate it before starting again?`;
  }
  return `${activeTitle} is still preparing. Terminate it before starting ${requestedTitle}?`;
}

export function buildLogoutPlaybackWorkerPrompt(movieTitle) {
  return `${normalizeTitle(movieTitle)} is still running in the background.`;
}
