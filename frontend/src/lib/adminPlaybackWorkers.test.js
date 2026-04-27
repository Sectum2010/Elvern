import test from "node:test";
import assert from "node:assert/strict";

import {
  buildPlaybackWorkersByUserId,
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
});


test("gauge percent clamping keeps values in range", () => {
  assert.equal(clampGaugePercent(-12), 0);
  assert.equal(clampGaugePercent(38), 38);
  assert.equal(clampGaugePercent(140), 100);
});
