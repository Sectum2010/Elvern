import assert from "node:assert/strict";
import { test, vi } from "vitest";

import { MemoryDiagnosticSpool } from "./indexedDbSpool.js";
import { PlaybackDiagnosticsRecoveryCoordinator } from "./recoveryCoordinator.js";

async function seedRecoverableSessions(spool, ownerUserId, count) {
  const ownerScopeHash = await spool.getOwnerScopeHash(ownerUserId);
  for (let index = 0; index < count; index += 1) {
    await spool.updateRecoveryState(`session-${String(index).padStart(3, "0")}`, {
      owner_scope_hash: ownerScopeHash,
      close_state: "closing",
      close_reason: "component_unmounted",
      final_source_sequence: 0,
    });
  }
}

test("recovery registry reads deterministic bounded pages", async () => {
  const spool = new MemoryDiagnosticSpool();
  await seedRecoverableSessions(spool, "owner-1", 5);

  const first = await spool.listRecoverySessions({ limit: 2 });
  const second = await spool.listRecoverySessions({
    afterSessionId: first.at(-1).session_id,
    limit: 2,
  });
  const third = await spool.listRecoverySessions({
    afterSessionId: second.at(-1).session_id,
    limit: 2,
  });

  assert.deepEqual(first.map((row) => row.session_id), ["session-000", "session-001"]);
  assert.deepEqual(second.map((row) => row.session_id), ["session-002", "session-003"]);
  assert.deepEqual(third.map((row) => row.session_id), ["session-004"]);
});

test("recovery coordinator serializes transports and coalesces persistent retries", async () => {
  const ownerUserId = "owner-1";
  const spool = new MemoryDiagnosticSpool();
  await seedRecoverableSessions(spool, ownerUserId, 5);
  const listRecoverySessions = vi.spyOn(spool, "listRecoverySessions");
  const timers = new Map();
  let nextTimer = 0;
  const runtimeRef = {
    setTimeout(callback) {
      const id = ++nextTimer;
      timers.set(id, callback);
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
  };
  let active = 0;
  let maxActive = 0;
  let sealOnAttempt = false;
  const stopped = [];
  const onComplete = vi.fn();
  const onIdle = vi.fn();
  const coordinator = new PlaybackDiagnosticsRecoveryCoordinator({
    runtimeRef,
    spoolFactory: async () => spool,
    pageSize: 2,
    wakeMs: 1_000,
    onComplete,
    onIdle,
    transportFactory: ({ playbackSessionId }) => ({
      async start() {
        active += 1;
        maxActive = Math.max(maxActive, active);
        return true;
      },
      wake: vi.fn(),
      async closeSession() {
        if (!sealOnAttempt) return false;
        await spool.updateRecoveryState(playbackSessionId, { close_state: "sealed" });
        await spool.cleanupSealedSession(playbackSessionId);
        return true;
      },
      stop() {
        active -= 1;
        stopped.push(playbackSessionId);
      },
    }),
  });

  assert.equal(coordinator.wake({ ownerUserId, authenticationRestored: true }), true);
  await coordinator.runPromise;

  assert.equal(maxActive, 1);
  assert.equal(stopped.length, 5);
  assert.equal(timers.size, 1);
  assert.deepEqual(onComplete.mock.calls[0][0], { recovered: 0, pending: 5 });
  assert.equal(listRecoverySessions.mock.calls.every(([options]) => options.limit === 2), true);

  sealOnAttempt = true;
  const [[timerId, retry]] = timers.entries();
  timers.delete(timerId);
  retry();
  await coordinator.runPromise;

  assert.deepEqual(onComplete.mock.calls[1][0], { recovered: 5, pending: 0 });
  assert.equal(timers.size, 0);
  assert.equal(onIdle.mock.calls.length, 1);
  assert.deepEqual(await spool.listRecoverySessions(), []);
});

test("recovery coordinator skips a live open session until its worker lease expires", async () => {
  const ownerUserId = "owner-1";
  const spool = new MemoryDiagnosticSpool();
  const ownerScopeHash = await spool.getOwnerScopeHash(ownerUserId);
  const now = Date.now();
  await spool.updateRecoveryState("session-live", {
    owner_scope_hash: ownerScopeHash,
    close_state: "open",
    active_lease_expires_at_ms: now + 30_000,
  });
  await spool.updateRecoveryState("session-crashed", {
    owner_scope_hash: ownerScopeHash,
    close_state: "open",
    active_lease_expires_at_ms: now - 1,
  });
  const recovered = [];
  const coordinator = new PlaybackDiagnosticsRecoveryCoordinator({
    spoolFactory: async () => spool,
    transportFactory: ({ playbackSessionId }) => ({
      start: vi.fn(async () => true),
      wake: vi.fn(),
      closeSession: vi.fn(async () => {
        recovered.push(playbackSessionId);
        return true;
      }),
      stop: vi.fn(),
    }),
  });

  const result = await coordinator.scan(ownerUserId, true);

  assert.deepEqual(result, { recovered: 1, pending: 0 });
  assert.deepEqual(recovered, ["session-crashed"]);
});
