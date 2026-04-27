import test from "node:test";
import assert from "node:assert/strict";

import {
  buildActivePlaybackConflictPrompt,
  buildLogoutPlaybackWorkerPrompt,
  getActivePlaybackWorkerConflict,
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
