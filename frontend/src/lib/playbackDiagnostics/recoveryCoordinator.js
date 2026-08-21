import {
  PLAYBACK_DIAGNOSTICS_RECOVERY_PAGE_SIZE,
  PLAYBACK_DIAGNOSTICS_RECOVERY_WAKE_MS,
} from "./constants";
import { createDiagnosticSpool } from "./indexedDbSpool";
import { PlaybackDiagnosticsTransport } from "./transport";

const TERMINAL_LOCAL_STATES = new Set(["sealed", "orphaned_local", "terminal_rejected"]);

function rowNeedsRecovery(row, nowMs = Date.now()) {
  if (TERMINAL_LOCAL_STATES.has(String(row?.close_state || ""))) return false;
  if (Number(row?.active_lease_expires_at_ms || 0) > nowMs) return false;
  return Number(row?.queue_bytes || 0) > 0 || String(row?.close_state || "open") !== "sealed";
}

export class PlaybackDiagnosticsRecoveryCoordinator {
  constructor({
    runtimeRef = globalThis,
    spoolFactory = async () => (await createDiagnosticSpool()).spool,
    transportFactory = (options) => new PlaybackDiagnosticsTransport(options),
    onHealth = () => {},
    onComplete = () => {},
    onIdle = () => {},
    pageSize = PLAYBACK_DIAGNOSTICS_RECOVERY_PAGE_SIZE,
    wakeMs = PLAYBACK_DIAGNOSTICS_RECOVERY_WAKE_MS,
  } = {}) {
    this.runtimeRef = runtimeRef;
    this.spoolFactory = spoolFactory;
    this.transportFactory = transportFactory;
    this.onHealth = onHealth;
    this.onComplete = onComplete;
    this.onIdle = onIdle;
    this.pageSize = Math.max(1, Math.min(256, Number(pageSize) || 1));
    this.wakeMs = Math.max(1_000, Number(wakeMs) || PLAYBACK_DIAGNOSTICS_RECOVERY_WAKE_MS);
    this.spool = null;
    this.runPromise = null;
    this.retryTimer = null;
    this.requestedOwnerUserId = null;
    this.authenticationRestored = false;
    this.stopped = false;
  }

  wake({ ownerUserId, authenticationRestored = false } = {}) {
    if (ownerUserId == null || this.stopped) return false;
    this.requestedOwnerUserId = ownerUserId;
    this.authenticationRestored ||= authenticationRestored;
    this.clearRetryTimer();
    if (!this.runPromise) void this.run();
    return true;
  }

  clearRetryTimer() {
    if (this.retryTimer != null) this.runtimeRef.clearTimeout?.(this.retryTimer);
    this.retryTimer = null;
  }

  scheduleRetry() {
    if (this.stopped || this.retryTimer != null || this.requestedOwnerUserId == null) return;
    this.retryTimer = this.runtimeRef.setTimeout?.(() => {
      this.retryTimer = null;
      if (!this.runPromise) void this.run();
    }, this.wakeMs) ?? null;
  }

  async ensureSpool() {
    if (!this.spool) this.spool = await this.spoolFactory();
    return this.spool;
  }

  async ownsRow(spool, row, ownerUserId, expectedOwnerScope) {
    const recovery = await spool.getRecoveryState(row.session_id);
    const storedOwnerScope = String(recovery?.owner_scope_hash || "");
    if (storedOwnerScope) return storedOwnerScope === expectedOwnerScope ? recovery : null;
    if (String(recovery?.owner_user_id ?? "") !== String(ownerUserId ?? "")) return null;
    await spool.updateRecoveryState(row.session_id, {
      owner_user_id: null,
      owner_scope_hash: expectedOwnerScope,
    });
    return { ...recovery, owner_user_id: null, owner_scope_hash: expectedOwnerScope };
  }

  async recoverRow(spool, row, recovery, authenticationRestored) {
    let transport;
    try {
      transport = this.transportFactory({
        playbackSessionId: row.session_id,
        spool,
        runtimeRef: this.runtimeRef,
        bootstrapContext: recovery?.bootstrap_context || {},
        onHealth: (component, reason, details) => this.onHealth({ component, reason, details }),
      });
      await transport.start();
      if (authenticationRestored) transport.wake({ authenticationRestored: true });
      const finalSequence = Number(
        recovery?.final_source_sequence ?? row.last_sequence ?? row.last_durable_ack ?? 0,
      );
      return await transport.closeSession(
        recovery?.close_reason || "client_recovered_after_interruption",
        finalSequence,
      );
    } finally {
      transport?.stop();
    }
  }

  async scan(ownerUserId, authenticationRestored) {
    const spool = await this.ensureSpool();
    const expectedOwnerScope = await spool.getOwnerScopeHash(ownerUserId);
    let cursor = "";
    let recovered = 0;
    let pending = 0;
    while (!this.stopped) {
      const rows = await spool.listRecoverySessions({
        afterSessionId: cursor,
        limit: this.pageSize,
      });
      if (!rows.length) break;
      for (const row of rows) {
        cursor = String(row.session_id || cursor);
        if (!rowNeedsRecovery(row)) continue;
        let recovery;
        try {
          recovery = await this.ownsRow(spool, row, ownerUserId, expectedOwnerScope);
          if (!recovery) continue;
          if (await this.recoverRow(spool, row, recovery, authenticationRestored)) recovered += 1;
          else pending += 1;
        } catch (error) {
          pending += 1;
          this.onHealth({
            component: "recovery",
            reason: "session_recovery_failed",
            details: { error_class: error?.name || "Error" },
          });
        }
      }
      if (rows.length < this.pageSize) break;
      await Promise.resolve();
    }
    return { recovered, pending };
  }

  run() {
    if (this.runPromise || this.stopped || this.requestedOwnerUserId == null) return this.runPromise;
    const ownerUserId = this.requestedOwnerUserId;
    const authenticationRestored = this.authenticationRestored;
    this.authenticationRestored = false;
    this.runPromise = this.scan(ownerUserId, authenticationRestored)
      .then(({ recovered, pending }) => {
        this.onComplete({ recovered, pending });
        if (pending > 0) this.scheduleRetry();
      })
      .catch((error) => {
        this.onHealth({
          component: "recovery",
          reason: "registry_scan_failed",
          details: { error_class: error?.name || "Error" },
        });
        this.onComplete({ recovered: 0, pending: 1 });
        this.scheduleRetry();
      })
      .finally(() => {
        this.runPromise = null;
        if (
          !this.stopped
          && this.retryTimer == null
          && this.requestedOwnerUserId !== ownerUserId
        ) {
          void this.run();
        } else if (!this.stopped && this.retryTimer == null) {
          this.closeIdle();
        }
      });
    return this.runPromise;
  }

  closeIdle() {
    if (this.runPromise || this.retryTimer != null || !this.spool) return;
    const spool = this.spool;
    this.spool = null;
    spool.close();
    this.onIdle();
  }

  stop() {
    this.stopped = true;
    this.clearRetryTimer();
    this.spool?.close();
    this.spool = null;
  }
}
