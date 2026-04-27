import test from "node:test";
import assert from "node:assert/strict";

import {
  buildPlaybackWorkerSummaryBubbles,
  buildPlaybackWorkerTerminatePrompt,
  buildPlaybackWorkersByUserId,
  canTerminatePlaybackWorker,
  clampGaugePercent,
  formatCpuCoresUsage,
  formatCpuCoresValue,
  formatCpuGaugeValue,
  formatMemoryGaugeValue,
} from "./adminPlaybackWorkers.js";


test("CPU and RAM display formatting handles null values safely", () => {
  assert.equal(formatCpuGaugeValue(null), "—");
  assert.equal(formatCpuCoresUsage(null, 18), "—");
  assert.equal(formatMemoryGaugeValue(null), "—");
});


test("CPU and RAM display formatting renders numeric values", () => {
  assert.equal(formatCpuGaugeValue(42.4), "42.4%");
  assert.equal(formatCpuCoresValue(7.2), "7.2 cores");
  assert.equal(formatCpuCoresUsage(7.2, 18), "7.2 / 18 cores");
  assert.equal(formatMemoryGaugeValue(1024 * 1024 * 256), "256 MB");
});


test("grouping workers by user summarizes totals and resources", () => {
  const byUser = buildPlaybackWorkersByUserId({
    workers_by_user: [
      {
        user_id: 7,
        allocated_cpu_cores: 9,
        allocated_budget_cores: 4,
        cpu_cores_used: 7.2,
        cpu_percent_of_user_limit: 80,
        memory_bytes: 1024 * 1024 * 256,
        memory_percent_of_total: 3.125,
        running_workers: 1,
        queued_workers: 1,
        total_workers: 2,
        items: [
          { worker_id: "worker-a", cpu_cores_used: 4.5, memory_bytes: 1024 * 1024 * 128 },
          { worker_id: "worker-b", cpu_cores_used: 2.7, memory_bytes: 1024 * 1024 * 64 },
        ],
      },
    ],
  });

  const summary = byUser.get(7);
  assert.ok(summary);
  assert.equal(summary.totalWorkers, 2);
  assert.equal(summary.allocatedCpuCores, 9);
  assert.equal(summary.cpuCoresUsed, 7.2);
  assert.equal(summary.cpuGaugePercent, 80);
  assert.equal(summary.memoryBytes, 1024 * 1024 * 256);
  assert.equal(summary.memoryGaugePercent, 3.125);
  assert.equal(summary.hasRunningWorkers, true);
});


test("gauge percent clamping keeps values in range", () => {
  assert.equal(clampGaugePercent(-12), 0);
  assert.equal(clampGaugePercent(38), 38);
  assert.equal(clampGaugePercent(140), 100);
});


test("top summary bubbles hide CPU and RAM used when no workers are active", () => {
  assert.deepEqual(
    buildPlaybackWorkerSummaryBubbles({
      total_cpu_cores: 20,
      cpu_upbound_percent: 90,
      active_worker_count: 0,
      route2_cpu_percent_of_total: null,
      route2_memory_percent_of_total: null,
    }),
    ["20 Detected CPU cores", "CPU upbound 90%"],
  );
});


test("top summary bubbles stay in detected cores then upbound then CPU then RAM order", () => {
  assert.deepEqual(
    buildPlaybackWorkerSummaryBubbles({
      total_cpu_cores: 20,
      cpu_upbound_percent: 90,
      active_worker_count: 1,
      route2_cpu_percent_of_total: 36,
      route2_memory_percent_of_total: 6.25,
    }),
    ["20 Detected CPU cores", "CPU upbound 90%", "36% CPU used", "6.3% RAM used"],
  );
});


test("top summary bubbles avoid ugly placeholders when active workers lack live samples", () => {
  assert.deepEqual(
    buildPlaybackWorkerSummaryBubbles({
      total_cpu_cores: 20,
      cpu_upbound_percent: 90,
      active_worker_count: 1,
      route2_cpu_percent_of_total: null,
      route2_memory_percent_of_total: null,
    }),
    ["20 Detected CPU cores", "CPU upbound 90%"],
  );
});


test("terminate helper includes the movie title", () => {
  assert.equal(
    buildPlaybackWorkerTerminatePrompt("Two Towers"),
    "Are you sure you want to terminate Two Towers?",
  );
  assert.equal(canTerminatePlaybackWorker("running"), true);
  assert.equal(canTerminatePlaybackWorker("queued"), true);
  assert.equal(canTerminatePlaybackWorker("completed"), false);
});
