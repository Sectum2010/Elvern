import test from "node:test";
import assert from "node:assert/strict";

import {
  getBrowserPlaybackTimelineEndSeconds,
  getBrowserPlaybackTimelineStartSeconds,
  isBrowserPlaybackAbsolutePositionReady,
  toBrowserPlaybackAbsoluteSeconds,
  toBrowserPlaybackMediaElementSeconds,
} from "./browserPlaybackTimeline.js";

function buildRoute2Payload(overrides = {}) {
  return {
    engine_mode: "route2",
    ready_start_seconds: 2211,
    ready_end_seconds: 2311,
    ...overrides,
  };
}

test("route2 timeline start uses ready_start_seconds", () => {
  assert.equal(getBrowserPlaybackTimelineStartSeconds(buildRoute2Payload()), 2211);
});

test("route2 timeline end uses ready_end_seconds", () => {
  assert.equal(getBrowserPlaybackTimelineEndSeconds(buildRoute2Payload()), 2311);
});

test("absolute resume time maps into local route2 media element time", () => {
  assert.equal(
    toBrowserPlaybackMediaElementSeconds(buildRoute2Payload(), 2277),
    66,
  );
});

test("local route2 media element time maps back to absolute movie time", () => {
  assert.equal(
    toBrowserPlaybackAbsoluteSeconds(buildRoute2Payload(), 66),
    2277,
  );
});

test("non-route2 payload keeps media element time unchanged", () => {
  const payload = { engine_mode: "legacy" };
  assert.equal(toBrowserPlaybackMediaElementSeconds(payload, 1831), 1831);
  assert.equal(toBrowserPlaybackAbsoluteSeconds(payload, 1831), 1831);
});

test("ready-window checks use absolute movie time for route2 sessions", () => {
  const payload = buildRoute2Payload();
  assert.equal(isBrowserPlaybackAbsolutePositionReady(payload, 2277, { headroomSeconds: 2 }), true);
  assert.equal(isBrowserPlaybackAbsolutePositionReady(payload, 2200, { headroomSeconds: 2 }), false);
  assert.equal(isBrowserPlaybackAbsolutePositionReady(payload, 2310.5, { headroomSeconds: 2 }), false);
});
