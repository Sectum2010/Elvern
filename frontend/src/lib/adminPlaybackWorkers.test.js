import test from "node:test";
import assert from "node:assert/strict";

import {
  buildPlaybackWorkersByUserId,
  clampGaugePercent,
  formatCpuGaugeValue,
  formatMemoryGaugeValue,
} from "./adminPlaybackWorkers.js";


test("CPU and RAM display formatting handles null values safely", () => {
  assert.equal(formatCpuGaugeValue(null), "—");
  assert.equal(formatMemoryGaugeValue(null), "—");
});


test("CPU and RAM display formatting renders numeric values", () => {
  assert.equal(formatCpuGaugeValue(42.4), "42%");
  assert.equal(formatMemoryGaugeValue(1024 * 1024 * 256), "256 MB");
});


test("grouping workers by user summarizes totals and resources", () => {
  const byUser = buildPlaybackWorkersByUserId({
    workers_by_user: [
      {
        user_id: 7,
        allocated_budget_cores: 4,
        running_workers: 1,
        queued_workers: 1,
        items: [
          { worker_id: "worker-a", cpu_percent: 22.5, memory_bytes: 1024 * 1024 * 128 },
          { worker_id: "worker-b", cpu_percent: 17.5, memory_bytes: 1024 * 1024 * 64 },
        ],
      },
    ],
  });

  const summary = byUser.get(7);
  assert.ok(summary);
  assert.equal(summary.totalWorkers, 2);
  assert.equal(summary.cpuPercent, 40);
  assert.equal(summary.cpuGaugePercent, 40);
  assert.equal(summary.memoryBytes, 1024 * 1024 * 192);
  assert.equal(summary.memoryGaugePercent, null);
});


test("gauge percent clamping keeps values in range", () => {
  assert.equal(clampGaugePercent(-12), 0);
  assert.equal(clampGaugePercent(38), 38);
  assert.equal(clampGaugePercent(140), 100);
});
