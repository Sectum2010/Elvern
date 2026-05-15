import assert from "node:assert/strict";
import { describe, test } from "vitest";

import {
  readClientBufferedAheadFromAbsoluteTarget,
  shouldReleaseRetargetFrozenFrame,
} from "./browserPlaybackRetargetReadiness.js";

function makeTimeRanges(ranges) {
  return {
    length: ranges.length,
    start(index) {
      return ranges[index][0];
    },
    end(index) {
      return ranges[index][1];
    },
  };
}

function makeSession(overrides = {}) {
  return {
    engine_mode: "route2",
    ready_start_seconds: 100,
    ready_end_seconds: 180,
    ...overrides,
  };
}

describe("browser playback retarget readiness", () => {
  test("does not release frozen frame when backend is ready but client buffer is below 15s", () => {
    const video = {
      buffered: makeTimeRanges([[20, 31]]),
      currentTime: 20,
      readyState: 4,
    };
    const session = makeSession();

    assert.equal(readClientBufferedAheadFromAbsoluteTarget(video, session, 120), 11);
    assert.equal(shouldReleaseRetargetFrozenFrame({
      sessionPayload: session,
      targetAbsoluteSeconds: 120,
      videoElement: video,
    }), false);
  });

  test("releases frozen frame only after target has at least 15s client buffer", () => {
    const video = {
      buffered: makeTimeRanges([[20, 36]]),
      currentTime: 20,
      readyState: 4,
    };
    const session = makeSession();

    assert.equal(readClientBufferedAheadFromAbsoluteTarget(video, session, 120), 16);
    assert.equal(shouldReleaseRetargetFrozenFrame({
      sessionPayload: session,
      targetAbsoluteSeconds: 120,
      videoElement: video,
    }), true);
  });

  test("does not use disjoint client ranges for retarget release", () => {
    const video = {
      buffered: makeTimeRanges([[0, 5], [60, 90]]),
      currentTime: 20,
      readyState: 4,
    };
    const session = makeSession();

    assert.equal(readClientBufferedAheadFromAbsoluteTarget(video, session, 120), 0);
    assert.equal(shouldReleaseRetargetFrozenFrame({
      sessionPayload: session,
      targetAbsoluteSeconds: 120,
      videoElement: video,
    }), false);
  });
});
