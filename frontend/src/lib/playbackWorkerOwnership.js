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
