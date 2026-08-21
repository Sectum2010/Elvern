import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";

import {
  estimateClockOffset,
  estimateTimerResolution,
  synchronizeDiagnosticClock,
} from "./clock.js";
import { PLAYBACK_DIAGNOSTICS_MAX_PENDING_GAPS } from "./constants.js";
import { MemoryDiagnosticSpool } from "./indexedDbSpool.js";
import {
  classifyDiagnosticResponse,
  PlaybackDiagnosticsRequestError,
  PlaybackDiagnosticsTransport,
} from "./transport.js";

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

test("frontend classifies every typed backend diagnostics response", () => {
  const cases = [
    [401, "", "authentication_required"],
    [403, "", "authentication_required"],
    [404, "diagnostics_not_found", "session_missing"],
    [409, "diagnostics_closing", "session_closing"],
    [409, "diagnostics_corrupt", "session_corrupt"],
    [409, "diagnostics_conflict", "identity_conflict"],
    [410, "diagnostics_sealed", "session_sealed"],
    [413, "diagnostics_request_too_large", "request_too_large"],
    [422, "diagnostics_invalid_event", "invalid_event"],
    [429, "diagnostics_budget_exceeded", "rate_limited"],
    [503, "diagnostics_worker_unavailable", "retriable"],
    [507, "diagnostics_capacity_reached", "capacity_reached"],
  ];
  cases.forEach(([status, code, expected]) => {
    assert.equal(classifyDiagnosticResponse(status, code), expected);
  });
});

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

test("pending gap capacity never discards an older gap or consumes a new sequence", async () => {
  const spool = new MemoryDiagnosticSpool({ maxBytes: 1_000_000 });
  for (let index = 0; index < PLAYBACK_DIAGNOSTICS_MAX_PENDING_GAPS; index += 1) {
    const gap = await spool.queueGap("session", { reason_code: "client_storage_failure" });
    assert.equal(gap.start_sequence, index + 1);
  }

  assert.equal(
    await spool.queueGap("session", { reason_code: "client_storage_failure" }),
    null,
  );
  const pending = await spool.pendingGaps("session");
  assert.equal(pending.length, PLAYBACK_DIAGNOSTICS_MAX_PENDING_GAPS);
  assert.equal(pending[0].start_sequence, 1);
  assert.equal(pending.at(-1).end_sequence, PLAYBACK_DIAGNOSTICS_MAX_PENDING_GAPS);

  const stored = await spool.createAndEnqueue("session", (sequence) => event(sequence));
  assert.equal(stored.sequence, PLAYBACK_DIAGNOSTICS_MAX_PENDING_GAPS + 1);
});

test("422 isolates the exact non-first invalid event without changing valid entries", async () => {
  const spool = new MemoryDiagnosticSpool({ maxBytes: 1_000_000 });
  await spool.createAndEnqueue("session", (sequence) => ({
    ...event(sequence),
    event_name: `event_${sequence}`,
  }));
  await spool.createAndEnqueue("session", (sequence) => ({
    ...event(sequence),
    event_name: `event_${sequence}`,
  }));
  const batch = await spool.readBatch("session", { maxEvents: 10, maxBytes: 100_000 });
  const transport = new PlaybackDiagnosticsTransport({ playbackSessionId: "session", spool });
  const error = new PlaybackDiagnosticsRequestError(422, "invalid_event", {
    code: "diagnostics_invalid_event",
    event_index: 1,
    event_id: "event-2",
    source_sequence: 2,
    permanent: true,
  });

  assert.equal(await transport.queueRejectedGap(error, batch.entries), true);
  assert.deepEqual(
    (await spool.pendingGaps("session")).map((gap) => gap.start_sequence),
    [2],
  );
  assert.deepEqual(
    (await spool.readBatch("session", { maxEvents: 10, maxBytes: 100_000 })).entries
      .map((entry) => entry.source_sequence),
    [1, 2],
  );
});

test("413 splits multi-event batches and tombstones only an oversized single event", async () => {
  const spool = new MemoryDiagnosticSpool({ maxBytes: 1_000_000 });
  await spool.createAndEnqueue("session", (sequence) => ({
    ...event(sequence),
    event_name: "oversized_event",
  }));
  await spool.createAndEnqueue("session", (sequence) => event(sequence));
  const batch = await spool.readBatch("session", { maxEvents: 10, maxBytes: 100_000 });
  const transport = new PlaybackDiagnosticsTransport({ playbackSessionId: "session", spool });

  assert.equal(await transport.splitOversizedBatch(batch.entries), true);
  assert.equal(transport.batchMaxEvents, 1);
  assert.equal((await spool.pendingGaps("session")).length, 0);
  assert.equal(await transport.splitOversizedBatch([batch.entries[0]]), true);
  assert.equal((await spool.pendingGaps("session"))[0].reason_code, "client_request_too_large");
});

test("an exact rejected event fails closed when the durable local gap ledger is full", async () => {
  const spool = new MemoryDiagnosticSpool({ maxBytes: 1_000_000 });
  for (let index = 0; index < PLAYBACK_DIAGNOSTICS_MAX_PENDING_GAPS; index += 1) {
    await spool.queueGap("session", { reason_code: "client_storage_failure" });
  }
  const stored = await spool.createAndEnqueue("session", (sequence) => ({
    ...event(sequence),
    event_name: "invalid_event",
  }));
  const batch = await spool.readBatch("session", { maxEvents: 1, maxBytes: 100_000 });
  const transport = new PlaybackDiagnosticsTransport({ playbackSessionId: "session", spool });
  const error = new PlaybackDiagnosticsRequestError(422, "invalid_event", {
    code: "diagnostics_invalid_event",
    event_index: 0,
    event_id: stored.event.event_id,
    source_sequence: stored.sequence,
  });

  assert.equal(await transport.queueRejectedGap(error, batch.entries), false);
  assert.equal(transport.stateMachine.state, "terminal_rejected");
  assert.equal((await spool.getRecoveryState("session")).last_close_response_code, "diagnostics_gap_ledger_full");
  assert.equal((await spool.pendingGaps("session"))[0].start_sequence, 1);
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
  const updateRecoveryState = vi.fn(async () => null);
  const acknowledge = vi.fn(async () => ({ deletedEvents: 0 }));
  const spool = {
    updateRecoveryState,
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
    updateRecoveryState.mock.calls
      .map((call) => call[1].close_state)
      .filter(Boolean),
    ["closing", "closing", "closing", "sealed"],
  );
});

test("authentication loss pauses without deleting queued evidence and resumes on an auth wake", async () => {
  vi.useFakeTimers();
  const spool = new MemoryDiagnosticSpool({ maxBytes: 1_000_000 });
  await spool.createAndEnqueue("session-00000001", (sequence) => event(sequence));
  const transport = new PlaybackDiagnosticsTransport({
    playbackSessionId: "session-00000001",
    spool,
    fetchRef: vi.fn(),
    windowRef: window,
    documentRef: document,
    navigatorRef: navigator,
  });
  transport.running = true;
  transport.sourceId = "source-00000001";
  transport.bootstrapComplete = true;
  transport.synchronizeClock = vi.fn(async () => null);
  transport.flushSoon = vi.fn();

  transport.handleFailure(new PlaybackDiagnosticsRequestError(
    401,
    "authentication_required",
    { code: "authentication_required" },
  ));

  assert.equal(transport.stateMachine.state, "paused_authentication");
  assert.equal(transport.retryTimer, null);
  assert.equal((await spool.stats("session-00000001")).queueDepth, 1);
  assert.equal((await spool.getRecoveryState("session-00000001")).close_state, "paused_authentication");

  transport.wake({ authenticationRestored: true });

  assert.equal(transport.stateMachine.state, "open");
  assert.equal(transport.synchronizeClock.mock.calls.length, 1);
  assert.deepEqual(transport.flushSoon.mock.calls[0], [0]);
  assert.equal((await spool.stats("session-00000001")).queueDepth, 1);
  transport.stop();
});

test("retriable failures coalesce to one retry timer", () => {
  vi.useFakeTimers();
  const transport = new PlaybackDiagnosticsTransport({
    playbackSessionId: "session-00000001",
    spool: new MemoryDiagnosticSpool(),
    windowRef: window,
    randomRef: () => 0,
  });
  transport.running = true;
  const firstError = new PlaybackDiagnosticsRequestError(
    503,
    "retriable",
    { code: "diagnostics_worker_unavailable" },
  );

  transport.handleFailure(firstError);
  const firstTimer = transport.retryTimer;
  transport.handleFailure(firstError);

  assert.notEqual(firstTimer, null);
  assert.equal(transport.retryTimer, firstTimer);
  assert.equal(vi.getTimerCount(), 1);
  transport.stop();
});
