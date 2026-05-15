import { test } from "vitest";
import assert from "node:assert/strict";

import {
  getBrowserPlaybackActiveWindowSeconds,
  getBrowserPlaybackAttachedManifestEndSeconds,
  getBrowserPlaybackFullDurationSeconds,
  getBrowserPlaybackTimelineEndSeconds,
  getBrowserPlaybackTimelineStartSeconds,
  isBrowserPlaybackAbsolutePositionReady,
  isNativeHlsWindowPayload,
  shouldForceReattachForManifestWindowRefresh,
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

test("active window fields are preferred over ready_* in readiness checks", () => {
  // Phase 2: backend exposes a sliding active_window_*; that wins over ready_*.
  const payload = buildRoute2Payload({
    active_window_start_seconds: 2280,
    active_window_end_seconds: 2400,
  });
  const window = getBrowserPlaybackActiveWindowSeconds(payload);
  assert.deepEqual(window, { startSeconds: 2280, endSeconds: 2400 });
  assert.equal(isBrowserPlaybackAbsolutePositionReady(payload, 2350), true);
  assert.equal(isBrowserPlaybackAbsolutePositionReady(payload, 2270), false);
  assert.equal(isBrowserPlaybackAbsolutePositionReady(payload, 2401), false);
});

test("native HLS attached manifest end uses active window end instead of server ready end", () => {
  const payload = buildRoute2Payload({
    selected_hls_engine: "native_hls",
    active_window_start_seconds: 5,
    active_window_end_seconds: 20,
    ready_end_seconds: 300,
  });
  assert.equal(getBrowserPlaybackAttachedManifestEndSeconds(payload), 20);
});

test("native HLS window refresh does not force frontend reattach", () => {
  const payload = buildRoute2Payload({
    selected_hls_engine: "native_hls",
    active_window_start_seconds: 0,
    active_window_end_seconds: 15,
    ready_end_seconds: 300,
  });
  assert.equal(shouldForceReattachForManifestWindowRefresh(payload), false);
});

test("non-native HLS manifest refresh may still force reattach", () => {
  assert.equal(shouldForceReattachForManifestWindowRefresh(buildRoute2Payload({
    selected_hls_engine: "hls_js",
  })), true);
  assert.equal(shouldForceReattachForManifestWindowRefresh({ engine_mode: "legacy" }), true);
});

test("hls.js attached manifest end can still use ready end when no native sliding window is active", () => {
  const payload = buildRoute2Payload({
    selected_hls_engine: "hls_js",
    active_window_start_seconds: 5,
    active_window_end_seconds: 20,
    ready_start_seconds: 0,
    ready_end_seconds: 300,
  });
  assert.equal(getBrowserPlaybackAttachedManifestEndSeconds(payload), 300);
});

test("missing active_window_* falls back to ready_* fields", () => {
  const payload = buildRoute2Payload();
  assert.equal(getBrowserPlaybackActiveWindowSeconds(payload), null);
  // Falls back to ready_start_seconds=2211 / ready_end_seconds=2311.
  assert.equal(isBrowserPlaybackAbsolutePositionReady(payload, 2270), true);
});

test("isNativeHlsWindowPayload detects the native_hls engine and policy label", () => {
  assert.equal(isNativeHlsWindowPayload({ selected_hls_engine: "native_hls" }), true);
  assert.equal(isNativeHlsWindowPayload({ native_hls_window_policy: "native_hls_sliding_window_v1" }), true);
  assert.equal(isNativeHlsWindowPayload({ selected_hls_engine: "hls_js" }), false);
  assert.equal(isNativeHlsWindowPayload(null), false);
});

test("getBrowserPlaybackFullDurationSeconds prefers explicit full_duration_seconds", () => {
  const payload = { duration_seconds: 100, full_duration_seconds: 7200 };
  assert.equal(getBrowserPlaybackFullDurationSeconds(payload), 7200);
});

test("full duration ignores active_window_* (timeline still shows whole movie)", () => {
  const payload = {
    duration_seconds: 7200,
    full_duration_seconds: 7200,
    active_window_start_seconds: 180,
    active_window_end_seconds: 420,
  };
  // Even with a 240s active window, the timeline base is the full movie.
  assert.equal(getBrowserPlaybackFullDurationSeconds(payload), 7200);
});
