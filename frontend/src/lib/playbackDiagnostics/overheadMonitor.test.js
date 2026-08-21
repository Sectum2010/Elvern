import assert from "node:assert/strict";
import { test, vi } from "vitest";

import {
  PlaybackDiagnosticsOverheadMonitor,
  PLAYBACK_DIAGNOSTICS_OVERHEAD_MODES,
} from "./overheadMonitor.js";

test("overhead pressure advances through the ordered bounded degradation states", () => {
  const changes = [];
  const monitor = new PlaybackDiagnosticsOverheadMonitor({
    onModeChange: (mode, reason) => changes.push([mode, reason]),
  });

  for (let index = 0; index < 64; index += 1) monitor.recordError("synthetic_failure");

  assert.deepEqual(changes.map(([mode]) => mode), PLAYBACK_DIAGNOSTICS_OVERHEAD_MODES.slice(1));
  assert.equal(monitor.mode, "circuit_open");
  assert.equal(monitor.snapshot().error_count, 64);
});

test("overhead monitor uses rolling p95 and bounds metric history", () => {
  const onModeChange = vi.fn();
  const monitor = new PlaybackDiagnosticsOverheadMonitor({ onModeChange });

  for (let index = 0; index < 160; index += 1) {
    monitor.observeLatency("idb_latency_ms", index < 120 ? 1 : 30, {
      p95LimitMs: 25,
      hardLimitMs: 250,
    });
  }

  const metric = monitor.snapshot().metrics.idb_latency_ms;
  assert.equal(metric.count, 128);
  assert.equal(metric.p95, 30);
  assert.equal(onModeChange.mock.calls.length > 0, true);
});

test("critical-only and circuit modes preserve only their documented evidence classes", () => {
  const monitor = new PlaybackDiagnosticsOverheadMonitor();
  monitor.adoptMode("critical_only");
  assert.equal(monitor.allows("media_aggregate"), false);
  assert.equal(monitor.allows("stall_confirmed", { critical: true }), true);

  monitor.adoptMode("circuit_open");
  assert.equal(monitor.allows("stall_confirmed", { critical: true }), false);
  assert.equal(monitor.allows("telemetry_gap", { critical: true }), true);
  assert.equal(monitor.allows("completed", { critical: true }), true);
});
