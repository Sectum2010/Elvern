import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";

import { estimateClockOffset, estimateTimerResolution } from "./clock.js";
import { MemoryDiagnosticSpool } from "./indexedDbSpool.js";
import { PlaybackDiagnosticsTransport } from "./transport.js";

afterEach(() => {
  vi.useRealTimers();
});

function event(sequence, { priority = "normal", body = "x" } = {}) {
  return {
    event_id: `event-${sequence}`,
    source_sequence: sequence,
    priority,
    payload: { reason: body },
  };
}

test("timer resolution and clock estimator use monotonic deltas and reject a high-RTT outlier", () => {
  const ticks = [0, 0.5, 1, 1.5, 2];
  assert.equal(estimateTimerResolution({ samples: 4, now: () => ticks.shift() }), 500);
  const estimate = estimateClockOffset([
    { clientSendWallMs: 1_000, clientReceiveWallMs: 1_010, serverReceiveWallNs: "1005000000", serverSendWallNs: "1005000000" },
    { clientSendWallMs: 2_000, clientReceiveWallMs: 2_008, serverReceiveWallNs: "2004000000", serverSendWallNs: "2004000000" },
    { clientSendWallMs: 3_000, clientReceiveWallMs: 9_000, serverReceiveWallNs: "3004000000", serverSendWallNs: "3004000000" },
  ]);

  assert.equal(estimate.sample_count, 3);
  assert.equal(estimate.network_rtt_ns, "8000000");
  assert.equal(estimate.algorithm_version, "min-rtt-median-offset-v1");
});

test("memory spool has monotonic sequences, critical reserve, and ACK-only deletion", async () => {
  const spool = new MemoryDiagnosticSpool({ maxBytes: 1_000 });
  assert.equal(await spool.reserveSequence("session"), 1);
  assert.equal(await spool.reserveSequence("session"), 2);

  const first = await spool.enqueue("session", event(1, { body: "x".repeat(650) }));
  assert.equal(first.stored, true);
  const normal = await spool.enqueue("session", event(2, { body: "x".repeat(250) }));
  assert.equal(normal.stored, false);
  const critical = await spool.enqueue("session", event(3, { priority: "critical", body: "critical" }));
  assert.equal(critical.stored, true);
  assert.equal((await spool.stats("session")).queueDepth, 2);

  await spool.acknowledge("session", 1);
  const remaining = await spool.readBatch("session", { maxEvents: 10, maxBytes: 10_000 });
  assert.deepEqual(remaining.entries.map((entry) => entry.source_sequence), [3]);
});

test("transport coalesces flushSoon, allows one request in flight, and deletes only after ACK", async () => {
  vi.useFakeTimers();
  const entries = [{ event: event(1), source_sequence: 1, bytes: 100 }];
  let resolveFetch;
  let fetchCount = 0;
  const fetchRef = vi.fn(() => {
    fetchCount += 1;
    return new Promise((resolve) => { resolveFetch = resolve; });
  });
  const acknowledge = vi.fn(async () => ({ deletedEvents: 1 }));
  const spool = {
    readBatch: vi.fn(async () => ({ entries, totalBytes: 100 })),
    acknowledge,
  };
  const transport = new PlaybackDiagnosticsTransport({
    playbackSessionId: "session-00000001",
    spool,
    fetchRef,
    windowRef: window,
    documentRef: document,
    navigatorRef: navigator,
  });
  transport.running = true;
  transport.sourceId = "source-00000001";
  transport.flushSoon();
  transport.flushSoon();
  await vi.advanceTimersByTimeAsync(1_000);
  assert.equal(fetchCount, 1);
  assert.equal(acknowledge.mock.calls.length, 0);

  const secondFlush = transport.flush();
  assert.equal(fetchCount, 1);
  resolveFetch({
    ok: true,
    json: async () => ({ ack_watermark: 1, duplicate: 0, out_of_order: 0, capacity_state: "normal" }),
  });
  await secondFlush;
  assert.equal(acknowledge.mock.calls.length, 1);
  assert.equal(fetchRef.mock.calls[0][0], "/api/playback-diagnostics/batch");
  transport.stop();
});

test("failed batch remains spooled and beacon is best effort without ACK deletion", async () => {
  const acknowledge = vi.fn();
  const spool = {
    readBatch: vi.fn(async () => ({ entries: [{ event: event(1), bytes: 100 }], totalBytes: 100 })),
    acknowledge,
  };
  const fetchRef = vi.fn(async () => { throw new TypeError("offline"); });
  const sendBeacon = vi.fn(() => true);
  const transport = new PlaybackDiagnosticsTransport({
    playbackSessionId: "session-00000001",
    spool,
    fetchRef,
    navigatorRef: { sendBeacon },
  });
  transport.running = true;
  transport.sourceId = "source-00000001";
  transport.notePersistedEvent(event(1));

  assert.equal(await transport.flush(), null);
  assert.equal(acknowledge.mock.calls.length, 0);
  assert.equal(transport.sendBeaconBestEffort(), true);
  assert.equal(sendBeacon.mock.calls.length, 1);
  assert.equal(acknowledge.mock.calls.length, 0);
});
