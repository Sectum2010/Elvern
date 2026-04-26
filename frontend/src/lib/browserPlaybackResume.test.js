import test from "node:test";
import assert from "node:assert/strict";

import {
  resolveAuthoritativeBrowserPlaybackResumePosition,
  resolveBrowserPlaybackResumePosition,
} from "./browserPlaybackResume.js";

test("shared progress falls back to item resume when in-memory progress is missing", () => {
  assert.equal(
    resolveBrowserPlaybackResumePosition({
      progressPayload: null,
      fallbackResumePositionSeconds: 600,
      durationSeconds: 7200,
    }),
    600,
  );
});

test("authoritative latest progress overrides stale zero state before Lite start", () => {
  assert.equal(
    resolveAuthoritativeBrowserPlaybackResumePosition({
      progressPayload: {
        position_seconds: 1200,
        duration_seconds: 7200,
        completed: false,
      },
      durationSeconds: 7200,
    }),
    1200,
  );
});

test("authoritative completed progress does not fall back to stale item resume", () => {
  assert.equal(
    resolveAuthoritativeBrowserPlaybackResumePosition({
      progressPayload: {
        position_seconds: 7188,
        duration_seconds: 7200,
        completed: true,
      },
      durationSeconds: 7200,
    }),
    0,
  );
});

test("resume positions near completion clamp back to zero", () => {
  assert.equal(
    resolveBrowserPlaybackResumePosition({
      progressPayload: {
        position_seconds: 7192,
        duration_seconds: 7200,
        completed: false,
      },
      fallbackResumePositionSeconds: 600,
      durationSeconds: 7200,
      completionGraceSeconds: 15,
    }),
    0,
  );
});
