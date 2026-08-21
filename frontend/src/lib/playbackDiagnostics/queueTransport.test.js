import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";

import {
  estimateClockOffset,
  estimateTimerResolution,
  synchronizeDiagnosticClock,
} from "./clock.js";
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
    { clientSendWallMs: 1_000, clientSendMonotonicUs: 10_000, clientReceiveMonotonicUs: 20_000, serverReceiveWallNs: "1005000000", serverSendWallNs: "1005000000" },
    { clientSendWallMs: 2_000, clientSendMonotonicUs: 30_000, clientReceiveMonotonicUs: 38_000, serverReceiveWallNs: "2004000000", serverSendWallNs: "2004000000" },
    { clientSendWallMs: 3_000, clientSendMonotonicUs: 40_000, clientReceiveMonotonicUs: 6_040_000, serverReceiveWallNs: "3004000000", serverSendWallNs: "3004000000" },
  ]);

  assert.equal(estimate.sample_count, 3);
  assert.equal(estimate.network_rtt_ns, "8000000");
  assert.equal(estimate.algorithm_version, "monotonic-rtt-median-offset-v2");
});

test("clock synchronization detects wall steps and reports observed drift", async () => {
  const wallTicks = [1_000, 3_005];
  const monotonicTicks = [10, 15];
  vi.spyOn(Date, "now").mockImplementation(() => wallTicks.shift());
  vi.spyOn(performance, "now").mockImplementation(() => monotonicTicks.shift());

  const synchronized = await synchronizeDiagnosticClock(
    async () => ({
      server_receive_wall_time_ns: "1005000000",
      server_send_wall_time_ns: "1005000000",
    }),
    { sampleCount: 1, wallStepThresholdMs: 1_000 },
  );
  assert.equal(synchronized.clock_step_detected, true);

  const drifted = estimateClockOffset([
    {
      clientSendWallMs: 2_000,
      clientSendMonotonicUs: 20_000,
      clientReceiveMonotonicUs: 25_000,
      serverReceiveWallNs: "2010000000",
      serverSendWallNs: "2010000000",
    },
  ], {
    previous: {
      clock_offset_ns: "0",
      client_anchor_monotonic_us: 10_000,
    },
  });
  assert.equal(Number.isFinite(drifted.observed_drift_ppm), true);
});

test("memory spool has monotonic sequences, critical reserve, and ACK-only deletion", async () => {
  const spool = new MemoryDiagnosticSpool({ maxBytes: 1_000 });
  const first = await spool.createAndEnqueue(
    "session",
    (sequence) => event(sequence, { body: "x".repeat(650) }),
  );
  assert.equal(first.stored, true);
  const normal = await spool.createAndEnqueue(
    "session",
    (sequence) => event(sequence, { body: "x".repeat(250) }),
  );
  assert.equal(normal.stored, false);
  const critical = await spool.createAndEnqueue(
    "session",
    (sequence) => event(sequence, { priority: "critical", body: "critical" }),
    { priority: "critical" },
  );
  assert.equal(critical.stored, true);
  assert.equal(first.sequence, 1);
  assert.equal(critical.sequence, 2);
  assert.equal((await spool.stats("session")).queueDepth, 2);

  await spool.acknowledge("session", 1);
  const remaining = await spool.readBatch("session", { maxEvents: 10, maxBytes: 10_000 });
  assert.deepEqual(remaining.entries.map((entry) => entry.source_sequence), [2]);
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
  transport.bootstrapComplete = true;
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
  transport.bootstrapComplete = true;
  transport.notePersistedEvent(event(1));

  assert.equal(await transport.flush(), null);
  assert.equal(acknowledge.mock.calls.length, 0);
  assert.equal(transport.sendBeaconBestEffort(), true);
  assert.equal(sendBeacon.mock.calls.length, 1);
  assert.equal(acknowledge.mock.calls.length, 0);
  transport.stop();
});

test("a recovered source re-bootstraps to obtain the server durable ACK before replay", async () => {
  const spool = {
    setMaxBytes: vi.fn(),
    updateRecoveryState: vi.fn(async () => null),
    acknowledge: vi.fn(async () => ({ deletedEvents: 2 })),
  };
  const fetchRef = vi.fn(async (path) => ({
    ok: true,
    json: async () => path.endsWith("/bootstrap")
      ? {
        source_id: "source-recovered",
        ack_watermark: 2,
        batch_max_events: 64,
        batch_max_bytes: 65_536,
        client_spool_max_bytes: 64_000_000,
        clock_algorithm: "monotonic-rtt-median-offset-v2",
      }
      : {
        server_receive_wall_time_ns: "1000000000",
        server_send_wall_time_ns: "1000000000",
      },
  }));
  const transport = new PlaybackDiagnosticsTransport({
    playbackSessionId: "session-00000001",
    spool,
    fetchRef,
    windowRef: window,
    documentRef: document,
    navigatorRef: navigator,
  });
  transport.running = true;
  transport.clientInstanceId = "client-stable";
  transport.sourceId = "source-recovered";

  await transport.ensureBootstrap();

  assert.equal(fetchRef.mock.calls[0][0], "/api/playback-diagnostics/bootstrap");
  assert.equal(spool.acknowledge.mock.calls[0][1], 2);
  assert.equal(transport.bootstrapComplete, true);
  transport.stop();
});

test("session loss keeps the event queued and replays it through a new source after bootstrap", async () => {
  const spool = new MemoryDiagnosticSpool({ maxBytes: 10_000 });
  await spool.createAndEnqueue("session-00000001", (sequence) => event(sequence));
  const batchSources = [];
  let batchAttempt = 0;
  const fetchRef = vi.fn(async (path, options) => {
    if (path.endsWith("/bootstrap")) {
      return {
        ok: true,
        json: async () => ({
          source_id: "source-after-restart",
          ack_watermark: 0,
          batch_max_events: 64,
          batch_max_bytes: 65_536,
          client_spool_max_bytes: 64_000_000,
          clock_algorithm: "monotonic-rtt-median-offset-v2",
        }),
      };
    }
    if (path.endsWith("/clock")) {
      return {
        ok: true,
        json: async () => ({
          server_receive_wall_time_ns: "1000000000",
          server_send_wall_time_ns: "1000000000",
        }),
      };
    }
    const body = JSON.parse(options.body);
    batchSources.push(body.source_id);
    batchAttempt += 1;
    if (batchAttempt === 1) return { ok: false, status: 404 };
    return {
      ok: true,
      json: async () => ({
        ack_watermark: 1,
        duplicate: 0,
        out_of_order: 0,
        capacity_state: "normal",
      }),
    };
  });
  const transport = new PlaybackDiagnosticsTransport({
    playbackSessionId: "session-00000001",
    spool,
    fetchRef,
    windowRef: window,
    documentRef: document,
    navigatorRef: navigator,
    bootstrapContext: {},
    randomRef: () => 0,
  });
  transport.running = true;
  transport.clientInstanceId = "client-stable";
  transport.sourceId = "source-before-restart";
  transport.bootstrapComplete = true;

  assert.equal(await transport.flush({ force: true }), null);
  assert.equal((await spool.stats("session-00000001")).queueDepth, 1);
  assert.equal(transport.sourceId, null);

  const replay = await transport.flush({ force: true });
  assert.equal(replay.ack_watermark, 1);
  assert.equal((await spool.stats("session-00000001")).queueDepth, 0);
  assert.deepEqual(batchSources, ["source-before-restart", "source-after-restart"]);
  transport.stop();
});

test("close keeps recovery state until the server confirms sealed and finalized", async () => {
  const cleanupSealedSession = vi.fn(async () => true);
  const markCloseState = vi.fn(async () => null);
  const acknowledge = vi.fn(async () => ({ deletedEvents: 0 }));
  const spool = {
    markCloseState,
    getRecoveryState: vi.fn(async () => ({ last_durable_ack: 1 })),
    acknowledge,
    cleanupSealedSession,
  };
  const responses = [
    { ack_watermark: 1, finalized: false, state: "closing" },
    { ack_watermark: 1, finalized: true, state: "sealed" },
  ];
  const fetchRef = vi.fn(async () => ({
    ok: true,
    json: async () => responses.shift(),
  }));
  const transport = new PlaybackDiagnosticsTransport({
    playbackSessionId: "session-00000001",
    spool,
    fetchRef,
  });
  transport.sourceId = "source-00000001";
  transport.flush = vi.fn(async () => null);

  assert.equal(await transport.closeSession("quit", 1), false);
  assert.equal(cleanupSealedSession.mock.calls.length, 0);
  assert.equal(await transport.closeSession("quit", 1), true);
  assert.equal(cleanupSealedSession.mock.calls.length, 1);
  assert.deepEqual(
    markCloseState.mock.calls.map((call) => call[1]),
    ["closing", "closing", "closing", "sealed"],
  );
});
