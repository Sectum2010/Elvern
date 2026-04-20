import { apiRequest } from "../../lib/api";

export function fetchPlaybackDecision({ itemId, forceHls = false }) {
  const path = forceHls
    ? `/api/playback/${itemId}?force_hls=1`
    : `/api/playback/${itemId}`;
  return apiRequest(path);
}

export function startPlaybackPreparation({ itemId, forceHls = false }) {
  return apiRequest(`/api/playback/${itemId}/start`, {
    method: "POST",
    data: { force_hls: forceHls },
  });
}

export function stopBrowserPlaybackSession({ stopUrl, browserPlaybackSessionRoot, sessionId }) {
  return apiRequest(
    stopUrl || `${browserPlaybackSessionRoot}/sessions/${sessionId}/stop`,
    { method: "POST" },
  );
}

export function savePlaybackProgress({
  itemId,
  positionSeconds,
  durationSeconds,
  completed,
  playbackMode,
}) {
  return apiRequest(`/api/progress/${itemId}`, {
    method: "POST",
    data: {
      position_seconds: positionSeconds,
      duration_seconds: durationSeconds,
      completed,
      playback_mode: playbackMode,
    },
  });
}

export function recordPlaybackEvent({
  itemId,
  eventType,
  playbackMode,
  positionSeconds,
  durationSeconds,
}) {
  return apiRequest(`/api/progress/${itemId}/event`, {
    method: "POST",
    data: {
      event_type: eventType,
      playback_mode: playbackMode,
      position_seconds: positionSeconds,
      duration_seconds: durationSeconds,
    },
  });
}

export function postOptimizedPlaybackHeartbeat({
  heartbeatUrl,
  browserPlaybackSessionRoot,
  sessionId,
  data,
}) {
  return apiRequest(
    heartbeatUrl || `${browserPlaybackSessionRoot}/sessions/${sessionId}/heartbeat`,
    {
      method: "POST",
      data,
    },
  );
}

export function fetchOptimizedPlaybackSessionStatus({
  statusUrl,
  browserPlaybackSessionRoot,
  sessionId,
}) {
  return apiRequest(
    statusUrl || `${browserPlaybackSessionRoot}/sessions/${sessionId}`,
  );
}

export function createOptimizedPlaybackSession({
  browserPlaybackSessionRoot,
  itemId,
  profile,
  startPositionSeconds,
  playbackMode,
  engineMode,
}) {
  const data = {
    item_id: Number(itemId),
    profile,
    start_position_seconds: startPositionSeconds,
  };
  if (playbackMode != null) {
    data.playback_mode = playbackMode;
  }
  if (engineMode != null) {
    data.engine_mode = engineMode;
  }
  return apiRequest(`${browserPlaybackSessionRoot}/sessions`, {
    method: "POST",
    data,
  });
}

export function seekOptimizedPlaybackSession({
  seekUrl,
  targetPositionSeconds,
  lastStablePositionSeconds,
  playingBeforeSeek,
}) {
  return apiRequest(seekUrl, {
    method: "POST",
    data: {
      target_position_seconds: targetPositionSeconds,
      last_stable_position_seconds: lastStablePositionSeconds,
      playing_before_seek: playingBeforeSeek,
    },
  });
}

export function fetchActiveOptimizedPlaybackSession({
  browserPlaybackSessionRoot,
  itemId,
}) {
  return apiRequest(`${browserPlaybackSessionRoot}/items/${itemId}/active`);
}
