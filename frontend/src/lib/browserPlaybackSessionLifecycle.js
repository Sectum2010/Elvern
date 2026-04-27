function getPlaybackMode(mode = "lite") {
  return mode === "full" ? "full" : "lite";
}

export const SESSION_SOURCE_EXPLICIT_CREATE = "explicit_create";
export const SESSION_SOURCE_RECOVERY_CREATE = "recovery_create";
export const SESSION_SOURCE_RESTORE_ACTIVE = "restore_active";
export const SESSION_SOURCE_STATUS = "status";
export const SESSION_SOURCE_SEEK = "seek";

export function createBrowserPlaybackAttempt({
  attemptId,
  itemId,
  playbackMode = "lite",
  startPositionSeconds = 0,
  profile = "",
  engineMode = "",
} = {}) {
  return {
    attemptId: Number.isInteger(attemptId) ? attemptId : 0,
    itemId: Number(itemId || 0),
    playbackMode: getPlaybackMode(playbackMode),
    startPositionSeconds: Math.max(0, Number(startPositionSeconds || 0)),
    profile: typeof profile === "string" ? profile : "",
    engineMode: typeof engineMode === "string" ? engineMode : "",
  };
}

export function buildBrowserPlaybackSessionCreatePayload({
  itemId,
  profile,
  startPositionSeconds = 0,
  playbackMode,
  engineMode,
  clientDeviceClass,
} = {}) {
  const data = {
    item_id: Number(itemId),
    profile,
    start_position_seconds: Math.max(0, Number(startPositionSeconds || 0)),
  };
  if (playbackMode != null) {
    data.playback_mode = getPlaybackMode(playbackMode);
  }
  if (engineMode != null) {
    data.engine_mode = engineMode;
  }
  if (clientDeviceClass != null) {
    data.client_device_class = clientDeviceClass;
  }
  return data;
}

export function buildBrowserPlaybackSessionIdentity({
  payload,
  attempt = null,
  currentSession = null,
} = {}) {
  const sessionId = typeof payload?.session_id === "string" ? payload.session_id.trim() : "";
  if (!sessionId) {
    return null;
  }
  const itemId = Number(payload?.media_item_id ?? attempt?.itemId ?? currentSession?.itemId ?? 0);
  const playbackMode = getPlaybackMode(
    payload?.playback_mode
    ?? attempt?.playbackMode
    ?? currentSession?.playbackMode
    ?? "lite",
  );
  return {
    sessionId,
    itemId,
    playbackMode,
    profile:
      typeof payload?.profile === "string" && payload.profile
        ? payload.profile
        : (attempt?.profile || currentSession?.profile || ""),
    engineMode:
      typeof payload?.engine_mode === "string" && payload.engine_mode
        ? payload.engine_mode
        : (attempt?.engineMode || currentSession?.engineMode || ""),
    startPositionSeconds: Number.isFinite(Number(attempt?.startPositionSeconds))
      ? Math.max(0, Number(attempt.startPositionSeconds))
      : Math.max(0, Number(currentSession?.startPositionSeconds || 0)),
    attemptId: Number.isInteger(attempt?.attemptId)
      ? attempt.attemptId
      : Number.isInteger(currentSession?.attemptId)
        ? currentSession.attemptId
        : 0,
  };
}

export function shouldAcceptBrowserPlaybackSessionPayload({
  payload,
  source,
  itemId,
  responseAttempt = null,
  latestAttempt = null,
  currentSession = null,
  deadSessionIds = new Set(),
} = {}) {
  const identity = buildBrowserPlaybackSessionIdentity({
    payload,
    attempt: responseAttempt,
    currentSession,
  });
  if (!identity) {
    return { accept: false, identity: null, reason: "missing_session_id" };
  }
  if (identity.itemId !== Number(itemId || 0)) {
    return { accept: false, identity, reason: "different_item" };
  }
  if (deadSessionIds.has(identity.sessionId)) {
    return { accept: false, identity, reason: "dead_session" };
  }

  if (source === SESSION_SOURCE_EXPLICIT_CREATE || source === SESSION_SOURCE_RECOVERY_CREATE) {
    if (!latestAttempt || identity.attemptId !== latestAttempt.attemptId) {
      return { accept: false, identity, reason: "stale_attempt" };
    }
    if (identity.playbackMode !== latestAttempt.playbackMode) {
      return { accept: false, identity, reason: "playback_mode_mismatch" };
    }
    return { accept: true, identity, reason: "attempt_session" };
  }

  if (source === SESSION_SOURCE_RESTORE_ACTIVE) {
    if (currentSession) {
      if (currentSession.sessionId !== identity.sessionId) {
        return { accept: false, identity, reason: "authoritative_session_exists" };
      }
      return {
        accept: true,
        identity: {
          ...identity,
          attemptId: currentSession.attemptId,
          startPositionSeconds: currentSession.startPositionSeconds,
        },
        reason: "same_session_restore",
      };
    }
    if (latestAttempt) {
      return { accept: false, identity, reason: "explicit_attempt_active" };
    }
    return { accept: true, identity, reason: "restore_without_authority" };
  }

  if (!currentSession || currentSession.sessionId !== identity.sessionId) {
    return { accept: false, identity, reason: "non_authoritative_session" };
  }

  return {
    accept: true,
    identity: {
      ...identity,
      attemptId: currentSession.attemptId,
      startPositionSeconds: currentSession.startPositionSeconds,
    },
    reason: "authoritative_session_update",
  };
}

export function resolveBrowserPlaybackSessionNotFound({
  failedSessionId,
  currentSession = null,
} = {}) {
  const normalizedSessionId = typeof failedSessionId === "string" ? failedSessionId.trim() : "";
  if (!normalizedSessionId) {
    return {
      markDead: false,
      clearCurrentSession: false,
      ignoreQuietly: true,
      reason: "missing_failed_session",
    };
  }
  if (!currentSession || currentSession.sessionId !== normalizedSessionId) {
    return {
      markDead: true,
      clearCurrentSession: false,
      ignoreQuietly: true,
      reason: "stale_session_not_found",
    };
  }
  return {
    markDead: true,
    clearCurrentSession: true,
    ignoreQuietly: false,
    reason: "current_session_not_found",
  };
}
