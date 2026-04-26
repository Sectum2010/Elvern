import test from "node:test";
import assert from "node:assert/strict";

import {
  buildBrowserPlaybackSessionCreatePayload,
  createBrowserPlaybackAttempt,
  resolveBrowserPlaybackSessionNotFound,
  SESSION_SOURCE_EXPLICIT_CREATE,
  SESSION_SOURCE_RECOVERY_CREATE,
  SESSION_SOURCE_RESTORE_ACTIVE,
  SESSION_SOURCE_STATUS,
  shouldAcceptBrowserPlaybackSessionPayload,
} from "./browserPlaybackSessionLifecycle.js";

function buildPayload({
  sessionId = "session-1",
  itemId = 1423,
  playbackMode = "lite",
  profile = "mobile_2160p",
  engineMode = "route2",
} = {}) {
  return {
    session_id: sessionId,
    media_item_id: itemId,
    playback_mode: playbackMode,
    profile,
    engine_mode: engineMode,
  };
}

test("fresh create response for current attempt commits session id", () => {
  const attempt = createBrowserPlaybackAttempt({
    attemptId: 3,
    itemId: 1423,
    playbackMode: "lite",
    startPositionSeconds: 1800,
  });

  const decision = shouldAcceptBrowserPlaybackSessionPayload({
    payload: buildPayload({ sessionId: "fresh-lite" }),
    source: SESSION_SOURCE_EXPLICIT_CREATE,
    itemId: 1423,
    responseAttempt: attempt,
    latestAttempt: attempt,
  });

  assert.equal(decision.accept, true);
  assert.equal(decision.identity?.sessionId, "fresh-lite");
  assert.equal(decision.identity?.attemptId, 3);
  assert.equal(decision.identity?.startPositionSeconds, 1800);
});

test("older create response cannot overwrite newer attempt", () => {
  const latestAttempt = createBrowserPlaybackAttempt({
    attemptId: 8,
    itemId: 1423,
    playbackMode: "lite",
  });
  const staleAttempt = createBrowserPlaybackAttempt({
    attemptId: 7,
    itemId: 1423,
    playbackMode: "lite",
  });

  const decision = shouldAcceptBrowserPlaybackSessionPayload({
    payload: buildPayload({ sessionId: "stale-lite" }),
    source: SESSION_SOURCE_EXPLICIT_CREATE,
    itemId: 1423,
    responseAttempt: staleAttempt,
    latestAttempt,
    currentSession: { sessionId: "fresh-lite", itemId: 1423, playbackMode: "lite", attemptId: latestAttempt.attemptId, startPositionSeconds: 0 },
  });

  assert.equal(decision.accept, false);
  assert.equal(decision.reason, "stale_attempt");
});

test("reattach response cannot overwrite a just-created session from another generation", () => {
  const currentSession = {
    sessionId: "fresh-lite",
    itemId: 1423,
    playbackMode: "lite",
    attemptId: 4,
    startPositionSeconds: 1800,
  };
  const latestAttempt = createBrowserPlaybackAttempt({
    attemptId: 4,
    itemId: 1423,
    playbackMode: "lite",
    startPositionSeconds: 1800,
  });

  const decision = shouldAcceptBrowserPlaybackSessionPayload({
    payload: buildPayload({ sessionId: "stale-restore", playbackMode: "full" }),
    source: SESSION_SOURCE_RESTORE_ACTIVE,
    itemId: 1423,
    latestAttempt,
    currentSession,
  });

  assert.equal(decision.accept, false);
  assert.equal(decision.reason, "authoritative_session_exists");
});

test("404 for stale session clears only the stale identity", () => {
  const outcome = resolveBrowserPlaybackSessionNotFound({
    failedSessionId: "stale-session",
    currentSession: {
      sessionId: "fresh-session",
      itemId: 1423,
      playbackMode: "lite",
      attemptId: 5,
      startPositionSeconds: 1800,
    },
  });

  assert.equal(outcome.markDead, true);
  assert.equal(outcome.clearCurrentSession, false);
  assert.equal(outcome.ignoreQuietly, true);
});

test("404 for current session produces a recoverable clear instead of looping", () => {
  const outcome = resolveBrowserPlaybackSessionNotFound({
    failedSessionId: "current-session",
    currentSession: {
      sessionId: "current-session",
      itemId: 1423,
      playbackMode: "lite",
      attemptId: 5,
      startPositionSeconds: 1800,
    },
  });

  assert.equal(outcome.markDead, true);
  assert.equal(outcome.clearCurrentSession, true);
  assert.equal(outcome.ignoreQuietly, false);
});

test("old full session cannot overwrite a new lite session", () => {
  const currentSession = {
    sessionId: "fresh-lite",
    itemId: 1423,
    playbackMode: "lite",
    attemptId: 9,
    startPositionSeconds: 1800,
  };

  const decision = shouldAcceptBrowserPlaybackSessionPayload({
    payload: buildPayload({ sessionId: "full-session", playbackMode: "full" }),
    source: SESSION_SOURCE_STATUS,
    itemId: 1423,
    currentSession,
  });

  assert.equal(decision.accept, false);
  assert.equal(decision.reason, "non_authoritative_session");
});

test("cloud lite resume start passes start_position_seconds and commits returned session", () => {
  const requestPayload = buildBrowserPlaybackSessionCreatePayload({
    itemId: 1423,
    profile: "mobile_2160p",
    startPositionSeconds: 1831,
    playbackMode: "lite",
    engineMode: "route2",
  });
  const attempt = createBrowserPlaybackAttempt({
    attemptId: 11,
    itemId: 1423,
    playbackMode: "lite",
    startPositionSeconds: 1831,
    profile: "mobile_2160p",
    engineMode: "route2",
  });
  const decision = shouldAcceptBrowserPlaybackSessionPayload({
    payload: buildPayload({ sessionId: "resume-lite" }),
    source: SESSION_SOURCE_EXPLICIT_CREATE,
    itemId: 1423,
    responseAttempt: attempt,
    latestAttempt: attempt,
  });

  assert.equal(requestPayload.start_position_seconds, 1831);
  assert.equal(requestPayload.playback_mode, "lite");
  assert.equal(decision.accept, true);
  assert.equal(decision.identity?.startPositionSeconds, 1831);
});

test("same item but different playback mode is a different lifecycle identity", () => {
  const liteAttempt = createBrowserPlaybackAttempt({
    attemptId: 12,
    itemId: 1423,
    playbackMode: "lite",
  });
  const fullAttempt = createBrowserPlaybackAttempt({
    attemptId: 13,
    itemId: 1423,
    playbackMode: "full",
  });

  assert.notEqual(liteAttempt.playbackMode, fullAttempt.playbackMode);
  assert.notEqual(liteAttempt.attemptId, fullAttempt.attemptId);
});

test("other item active session is ignored", () => {
  const decision = shouldAcceptBrowserPlaybackSessionPayload({
    payload: buildPayload({ sessionId: "other-item", itemId: 9999 }),
    source: SESSION_SOURCE_RESTORE_ACTIVE,
    itemId: 1423,
  });

  assert.equal(decision.accept, false);
  assert.equal(decision.reason, "different_item");
});

test("recovery create keeps the explicit playback mode instead of falling back to lite", () => {
  const attempt = createBrowserPlaybackAttempt({
    attemptId: 15,
    itemId: 1265,
    playbackMode: "full",
    startPositionSeconds: 0,
  });

  const decision = shouldAcceptBrowserPlaybackSessionPayload({
    payload: buildPayload({ sessionId: "recovered-full", itemId: 1265, playbackMode: "full" }),
    source: SESSION_SOURCE_RECOVERY_CREATE,
    itemId: 1265,
    responseAttempt: attempt,
    latestAttempt: attempt,
  });

  assert.equal(decision.accept, true);
  assert.equal(decision.identity?.playbackMode, "full");
});

test("dead session ids are not reaccepted after a 404", () => {
  const currentSession = {
    sessionId: "fresh-lite",
    itemId: 1423,
    playbackMode: "lite",
    attemptId: 16,
    startPositionSeconds: 1800,
  };

  const decision = shouldAcceptBrowserPlaybackSessionPayload({
    payload: buildPayload({ sessionId: "dead-session" }),
    source: SESSION_SOURCE_RESTORE_ACTIVE,
    itemId: 1423,
    currentSession,
    deadSessionIds: new Set(["dead-session"]),
  });

  assert.equal(decision.accept, false);
  assert.equal(decision.reason, "dead_session");
});
