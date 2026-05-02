import test from "node:test";
import assert from "node:assert/strict";

import {
  buildActivePlaybackConflictPrompt,
  buildLogoutPlaybackWorkerPrompt,
  getActivePlaybackWorkerConflict,
  getPlaybackAdmissionError,
  getPlaybackWorkerCooldown,
} from "./playbackWorkerOwnership.js";


test("active playback conflict prompt includes active and requested movie titles", () => {
  assert.equal(
    buildActivePlaybackConflictPrompt("Coco", "Two Towers"),
    "Coco is still preparing. Terminate it before starting Two Towers?",
  );
});


test("logout playback worker prompt uses the movie title", () => {
  assert.equal(
    buildLogoutPlaybackWorkerPrompt("Coco"),
    "Coco is still running in the background.",
  );
});


test("active playback conflict helper normalizes structured 409 detail", () => {
  const detail = getActivePlaybackWorkerConflict({
    detail: {
      code: "active_playback_worker_exists",
      active_movie_title: "Coco",
      active_media_item_id: 70,
      active_playback_mode: "full",
      active_worker_id: "worker-1",
      active_session_id: "session-1",
      message: "Coco is still preparing.",
    },
  });
  assert.deepEqual(detail, {
    code: "active_playback_worker_exists",
    activeMovieTitle: "Coco",
    activeMediaItemId: 70,
    activePlaybackMode: "full",
    activeWorkerId: "worker-1",
    activeSessionId: "session-1",
    message: "Coco is still preparing.",
  });
});


test("playback worker cooldown helper normalizes structured 409 detail", () => {
  const detail = getPlaybackWorkerCooldown({
    detail: {
      code: "playback_worker_cooldown",
      media_item_id: 70,
      remaining_seconds: 27,
      message: "Your current quota for this movie has been reached. Please try again in 27 seconds.",
    },
  });
  assert.deepEqual(detail, {
    code: "playback_worker_cooldown",
    mediaItemId: 70,
    remainingSeconds: 27,
    message: "Your current quota for this movie has been reached. Please try again in 27 seconds.",
  });
});


test("playback admission helper normalizes same-user active playback limit", () => {
  const detail = getPlaybackAdmissionError({
    detail: {
      code: "same_user_active_playback_limit",
      message: "You already have an active playback. Stop it or switch before starting another.",
    },
  });
  assert.deepEqual(detail, {
    code: "same_user_active_playback_limit",
    reasonCode: "",
    message: "You already have an active playback. Stop it or switch before starting another.",
  });
});


test("playback admission helper normalizes server max capacity", () => {
  const detail = getPlaybackAdmissionError({
    detail: {
      code: "server_max_capacity",
      reason_code: "external_host_cpu_pressure_high",
      message: "Server is busy with another task. Please try again later.",
    },
  });
  assert.deepEqual(detail, {
    code: "server_max_capacity",
    reasonCode: "external_host_cpu_pressure_high",
    message: "Server is busy with another task. Please try again later.",
  });
});


test("playback admission helper does not map provider source fallback to server busy", () => {
  const detail = getPlaybackAdmissionError({
    detail: {
      code: "provider_source_error",
      reason_code: "provider_auth_required",
    },
  });
  assert.deepEqual(detail, {
    code: "provider_source_error",
    reasonCode: "provider_auth_required",
    message: "Playback source is unavailable. Reconnect the provider or try again later.",
  });
});


test("playback admission helper normalizes provider quota without server busy", () => {
  const detail = getPlaybackAdmissionError({
    detail: {
      code: "provider_quota_exceeded",
      reason_code: "downloadQuotaExceeded",
    },
  });
  assert.deepEqual(detail, {
    code: "provider_quota_exceeded",
    reasonCode: "downloadQuotaExceeded",
    message: "The download quota for this file has been exceeded. Try again later or choose another source.",
  });
});


test("playback admission helper ignores provider auth requirement details", () => {
  assert.equal(
    getPlaybackAdmissionError({
      detail: {
        code: "provider_auth_required",
        message: "Reconnect Google Drive.",
      },
    }),
    null,
  );
});
