import assert from "node:assert/strict";
import { test, vi } from "vitest";

import { PlaybackDiagnosticsDataPlane } from "./dataPlane.js";
import { PLAYBACK_DIAGNOSTICS_WORKER_QUEUE_MAX_MESSAGES } from "./constants.js";
import { createDiagnosticSpool, MemoryDiagnosticSpool } from "./indexedDbSpool.js";
import { PlaybackDiagnosticClientStateMachine } from "./stateMachine.js";
import { PlaybackDiagnosticsWorkerClient } from "./workerClient.js";

function createStartedPlane({ onHealth = () => {} } = {}) {
  const spool = new MemoryDiagnosticSpool({ maxBytes: 1_000_000 });
  const closeSession = vi.fn(async () => false);
  const plane = new PlaybackDiagnosticsDataPlane({
    playbackSessionId: "session-00000001",
    context: { playback_mode: "lite" },
    onHealth,
  });
  plane.started = true;
  plane.spool = spool;
  plane.transport = {
    clock: {},
    closeSession,
    flushSoon: vi.fn(),
    notePersistedEvent: vi.fn(),
    stop: vi.fn(),
  };
  return { plane, spool, closeSession };
}

function failingIndexedDb(errorName, { blocked = false } = {}) {
  return {
    open() {
      const request = {};
      queueMicrotask(() => {
        if (blocked) {
          request.onblocked?.();
          return;
        }
        const error = new Error(errorName);
        error.name = errorName;
        request.error = error;
        request.onerror?.();
      });
      return request;
    },
  };
}

test("blocked IndexedDB startup degrades immediately to the bounded memory spool", async () => {
  const opened = await createDiagnosticSpool({
    indexedDBRef: failingIndexedDb("IndexedDBBlockedError", { blocked: true }),
    degradedMaxBytes: 64_000,
  });

  assert.equal(opened.persistent, false);
  assert.equal(opened.spool instanceof MemoryDiagnosticSpool, true);
  assert.equal(opened.spool.maxBytes, 64_000);
  assert.equal(opened.unavailableReason, "IndexedDBBlockedError");
});

test("IndexedDB quota failure degrades immediately to the bounded memory spool", async () => {
  const opened = await createDiagnosticSpool({
    indexedDBRef: failingIndexedDb("QuotaExceededError"),
    degradedMaxBytes: 64_000,
  });

  assert.equal(opened.persistent, false);
  assert.equal(opened.spool instanceof MemoryDiagnosticSpool, true);
  assert.equal(opened.spool.maxBytes, 64_000);
  assert.equal(opened.unavailableReason, "QuotaExceededError");
});

test("terminal client states reject every later transition and capture", () => {
  for (const terminalState of ["sealed", "orphaned_local", "terminal_rejected"]) {
    const machine = new PlaybackDiagnosticClientStateMachine(terminalState);
    assert.equal(machine.terminal, true);
    assert.equal(machine.canCapture(), false);
    assert.equal(machine.canCapture({ critical: true }), false);
    for (const nextState of ["open", "closing", "paused_authentication", "paused_capacity"]) {
      assert.throws(
        () => machine.transition(nextState),
        new RegExp(`Invalid playback diagnostics transition: ${terminalState}`),
      );
    }
  }
});

test("degraded data-plane fallback bounds pending work and preserves critical reserve", async () => {
  let releaseFirstWrite;
  const firstWriteGate = new Promise((resolve) => { releaseFirstWrite = resolve; });
  const onHealth = vi.fn();
  const { plane, spool } = createStartedPlane({ onHealth });
  const createAndEnqueue = spool.createAndEnqueue.bind(spool);
  let first = true;
  spool.createAndEnqueue = vi.fn(async (...args) => {
    if (first) {
      first = false;
      await firstWriteGate;
    }
    return createAndEnqueue(...args);
  });

  const pending = Array.from(
    { length: PLAYBACK_DIAGNOSTICS_WORKER_QUEUE_MAX_MESSAGES },
    (_, index) => plane.capture("timeupdate", { payload: { index } }),
  );
  await Promise.resolve();

  assert.equal(plane.pendingOperations, PLAYBACK_DIAGNOSTICS_WORKER_QUEUE_MAX_MESSAGES);
  assert.equal(await plane.capture("timeupdate", { payload: { overflow: true } }), false);
  const terminal = plane.capture("completed", { priority: "critical" });
  assert.equal(
    plane.pendingOperations,
    PLAYBACK_DIAGNOSTICS_WORKER_QUEUE_MAX_MESSAGES + 1,
  );
  assert.equal(plane.stateMachine.closing, true);

  releaseFirstWrite();
  await Promise.all(pending);
  assert.equal(await terminal, true);
  assert.equal(plane.pendingOperations, 0);
  assert.equal(
    onHealth.mock.calls.some(([entry]) => entry.reason === "operation_queue_full"),
    true,
  );
});

test("terminal capture closes synchronously to reject same-tick N+1 observations", async () => {
  const { plane, spool, closeSession } = createStartedPlane();

  const terminal = plane.capture("completed", { priority: "critical" });
  const delayedMediaCallback = plane.capture("timeupdate", { payload: { playhead_ms: 1_000 } });

  assert.equal(await delayedMediaCallback, false);
  assert.equal(await terminal, true);
  const batch = await spool.readBatch("session-00000001", {
    maxEvents: 10,
    maxBytes: 1_000_000,
  });
  assert.deepEqual(batch.entries.map((entry) => entry.event.event_name), ["completed"]);
  assert.deepEqual(batch.entries.map((entry) => entry.source_sequence), [1]);
  assert.deepEqual(closeSession.mock.calls[0], ["completed", 1]);
});

test("ordinary component close persists quit as the final source event", async () => {
  const { plane, spool, closeSession } = createStartedPlane();

  assert.equal(await plane.close("component_unmounted"), false);
  assert.equal(await plane.capture("playing"), false);
  const batch = await spool.readBatch("session-00000001", {
    maxEvents: 10,
    maxBytes: 1_000_000,
  });

  assert.equal(batch.entries.length, 1);
  assert.equal(batch.entries[0].event.event_name, "quit");
  assert.equal(batch.entries[0].event.observation_kind, "inferred");
  assert.equal(batch.entries[0].event.payload.reason, "component_unmounted");
  assert.deepEqual(closeSession.mock.calls[0], ["component_unmounted", 1]);
});

test("overhead degradation records a durable local gap without allocating a normal event", async () => {
  const { plane, spool } = createStartedPlane();
  plane.setOverheadMode("critical_only", "synthetic_pressure");

  assert.equal(await plane.capture("media_aggregate", { payload: { sample_count: 1 } }), false);
  await plane.writeChain;

  const [gap] = await spool.pendingGaps("session-00000001");
  assert.equal(gap.start_sequence, 1);
  assert.equal(gap.end_sequence, 1);
  assert.equal(gap.reason_code, "client_overhead_circuit");
  assert.equal(gap.rejected_event_name, null);
  assert.equal(gap.rejected_event_hash, null);
  assert.equal(Number.isFinite(gap.created_at_ms), true);
  const batch = await spool.readBatch("session-00000001", {
    maxEvents: 10,
    maxBytes: 1_000_000,
  });
  assert.equal(batch.entries.length, 0);
});

test("recovery ownership is persisted as a salted scope hash instead of a raw user id", async () => {
  const spool = new MemoryDiagnosticSpool();
  const ownerScopeHash = await spool.getOwnerScopeHash("private-user-42");

  await spool.updateRecoveryState("session-00000001", {
    owner_user_id: null,
    owner_scope_hash: ownerScopeHash,
    close_state: "open",
  });
  const recovery = await spool.getRecoveryState("session-00000001");

  assert.equal(recovery.owner_user_id, null);
  assert.equal(typeof recovery.owner_scope_hash, "string");
  assert.equal(recovery.owner_scope_hash.length > 16, true);
  assert.equal(JSON.stringify(recovery).includes("private-user-42"), false);
});

test("worker-side storage pressure propagates its degradation mode to main observers", () => {
  const worker = new EventTarget();
  worker.postMessage = vi.fn();
  worker.terminate = vi.fn();
  const onModeChange = vi.fn();
  const client = new PlaybackDiagnosticsWorkerClient({
    options: { playbackSessionId: "session-00000001" },
    workerFactory: () => worker,
    onModeChange,
  });
  void client.start();

  worker.dispatchEvent(new MessageEvent("message", {
    data: {
      clientId: client.clientId,
      type: "health",
      health: {
        component: "overhead",
        reason: "mode_changed",
        details: { mode: "reduced_aggregates", trigger: "indexeddb_latency" },
      },
    },
  }));

  assert.equal(client.overheadMonitor.mode, "reduced_aggregates");
  assert.equal(onModeChange.mock.calls.at(-1)[0], "reduced_aggregates");
  assert.equal(
    worker.postMessage.mock.calls.some(([message]) => (
      message.type === "set_overhead_mode" && message.mode === "reduced_aggregates"
    )),
    true,
  );
  client.dispose();
});

test("worker crash preserves pending close and declares unacknowledged captures in fallback", async () => {
  const worker = new EventTarget();
  worker.postMessage = vi.fn();
  worker.terminate = vi.fn();
  const fallback = {
    persistent: true,
    start: vi.fn(async () => true),
    setPlaybackAttempt: vi.fn(),
    setOverheadMode: vi.fn(),
    declareDropped: vi.fn(async () => true),
    close: vi.fn(async () => true),
    stop: vi.fn(),
  };
  const client = new PlaybackDiagnosticsWorkerClient({
    options: {
      playbackSessionId: "session-00000001",
      playbackAttemptId: "attempt-00000001",
    },
    workerFactory: () => worker,
    dataPlaneFactory: () => fallback,
  });
  const ready = client.start();
  assert.equal(client.capture("playing"), true);
  assert.equal(client.capture("waiting"), true);
  client.overheadMonitor.adoptMode("critical_only", "synthetic_worker_pressure");
  client.close("component_unmounted");

  worker.dispatchEvent(new Event("error"));
  assert.deepEqual(await ready, { worker: false, persistent: true });
  await vi.waitFor(() => assert.equal(fallback.close.mock.calls.length, 1));

  assert.deepEqual(fallback.declareDropped.mock.calls[0], [2, "client_overhead_circuit"]);
  assert.deepEqual(fallback.setOverheadMode.mock.calls[0], [
    "critical_only",
    "worker_crashed",
  ]);
  assert.deepEqual(fallback.close.mock.calls[0], ["component_unmounted"]);
  assert.equal(worker.terminate.mock.calls.length, 1);
});
