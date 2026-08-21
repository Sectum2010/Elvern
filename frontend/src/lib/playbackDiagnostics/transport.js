import {
  PLAYBACK_DIAGNOSTICS_CLOCK_RECALIBRATION_MS,
  PLAYBACK_DIAGNOSTICS_DEFAULT_BATCH_MAX_BYTES,
  PLAYBACK_DIAGNOSTICS_DEFAULT_BATCH_MAX_EVENTS,
  PLAYBACK_DIAGNOSTICS_FLUSH_MS,
  PLAYBACK_DIAGNOSTICS_FLUSH_SOON_MS,
  PLAYBACK_DIAGNOSTICS_RETRY_BASE_MS,
  PLAYBACK_DIAGNOSTICS_RETRY_MAX_MS,
} from "./constants";
import { synchronizeDiagnosticClock } from "./clock";
import { PlaybackDiagnosticClientStateMachine } from "./stateMachine";

const API_ROOT = "/api/playback-diagnostics";
const BEACON_EVENT_LIMIT = 24;
const CLOSE_FLUSH_ATTEMPTS = 3;

function errorDetail(payload) {
  const detail = payload?.detail;
  if (detail && !Array.isArray(detail) && typeof detail === "object") return detail;
  if (Array.isArray(detail)) {
    return {
      code: "diagnostics_request_validation_failed",
      reason: "request_schema_invalid",
      permanent: true,
      batch_split_allowed: false,
    };
  }
  return {};
}

export class PlaybackDiagnosticsRequestError extends Error {
  constructor(status, category, details = {}) {
    super(`Playback diagnostics request failed (${status})`);
    this.name = "PlaybackDiagnosticsRequestError";
    this.status = status;
    this.category = category;
    this.code = String(details.code || "diagnostics_request_failed");
    this.details = { ...details };
  }
}

export function classifyDiagnosticResponse(status, code = "") {
  const normalizedCode = String(code || "");
  if (status < 400) return "ok";
  if (status === 401 || status === 403) return "authentication_required";
  if (status === 404) return "session_missing";
  if (status === 410 || normalizedCode === "diagnostics_sealed") return "session_sealed";
  if (normalizedCode === "diagnostics_corrupt") return "session_corrupt";
  if (normalizedCode === "diagnostics_closing") return "session_closing";
  if (status === 413) return "request_too_large";
  if (status === 422 || normalizedCode === "diagnostics_invalid_event") return "invalid_event";
  if (status === 429) return "rate_limited";
  if (status === 507) return "capacity_reached";
  if (status === 409) return "identity_conflict";
  if (status === 503 || status >= 500) return "retriable";
  return "permanent_invalid";
}

export async function diagnosticFetch(fetchRef, path, data) {
  const response = await fetchRef(`${API_ROOT}${path}`, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    const details = errorDetail(payload);
    throw new PlaybackDiagnosticsRequestError(
      response.status,
      classifyDiagnosticResponse(response.status, details.code),
      details,
    );
  }
  return response.json();
}

function monotonicNow() {
  return globalThis.performance?.now?.() ?? Date.now();
}

export class PlaybackDiagnosticsTransport {
  constructor({
    playbackSessionId,
    spool,
    bootstrapContext = {},
    fetchRef = globalThis.fetch?.bind(globalThis),
    onMetric = null,
    onHealth = null,
    onSealed = null,
    stateMachine = null,
    windowRef = globalThis.window || globalThis,
    documentRef = globalThis.document || null,
    navigatorRef = globalThis.navigator || null,
    randomRef = Math.random,
  }) {
    this.playbackSessionId = playbackSessionId;
    this.spool = spool;
    this.bootstrapContext = bootstrapContext;
    this.fetchRef = fetchRef;
    this.onHealth = onHealth || onMetric || (() => {});
    this.onSealed = typeof onSealed === "function" ? onSealed : () => {};
    this.stateMachine = stateMachine || new PlaybackDiagnosticClientStateMachine();
    this.windowRef = windowRef || globalThis;
    this.documentRef = documentRef;
    this.navigatorRef = navigatorRef;
    this.randomRef = randomRef;
    this.clientInstanceId = null;
    this.sourceId = null;
    this.bootstrapComplete = false;
    this.clock = {};
    this.batchMaxEvents = PLAYBACK_DIAGNOSTICS_DEFAULT_BATCH_MAX_EVENTS;
    this.batchMaxBytes = PLAYBACK_DIAGNOSTICS_DEFAULT_BATCH_MAX_BYTES;
    this.running = false;
    this.bootstrapInFlight = null;
    this.flushInFlight = null;
    this.clockInFlight = null;
    this.closeInFlight = null;
    this.flushTimer = null;
    this.flushSoonTimer = null;
    this.retryTimer = null;
    this.clockTimer = null;
    this.beaconEvents = [];
    this.retries = 0;
    this.nextRetryAtMs = 0;
    this.closeReason = null;
    this.finalSourceSequence = null;
    this.resumeState = "open";
    this.handleOnline = () => this.wake({ reason: "online" });
    this.handleVisibility = () => {
      if (this.documentRef?.visibilityState === "visible") this.wake({ reason: "visible" });
    };
    this.handlePageHide = () => this.sendBeaconBestEffort();
  }

  transition(nextState) {
    try {
      return this.stateMachine.transition(nextState);
    } catch {
      this.onHealth("transport", "invalid_state_transition", { next_state: nextState });
      return this.stateMachine.state;
    }
  }

  async start() {
    if (this.running || typeof this.fetchRef !== "function") return false;
    this.running = true;
    this.clientInstanceId = await this.spool.getOrCreateClientInstanceId(this.playbackSessionId);
    const recovery = await this.spool.getRecoveryState?.(this.playbackSessionId);
    if (recovery?.client_instance_id === this.clientInstanceId && recovery?.source_id) {
      this.sourceId = recovery.source_id;
    }
    this.closeReason = recovery?.close_reason || null;
    this.finalSourceSequence = recovery?.final_source_sequence ?? null;
    if (recovery?.close_state === "closing" && this.stateMachine.state === "open") {
      this.transition("closing");
    }
    await this.spool.updateRecoveryState?.(this.playbackSessionId, {
      client_instance_id: this.clientInstanceId,
      close_state: recovery?.close_state || this.stateMachine.state,
    });
    this.windowRef?.addEventListener?.("online", this.handleOnline);
    this.windowRef?.addEventListener?.("pagehide", this.handlePageHide);
    this.documentRef?.addEventListener?.("visibilitychange", this.handleVisibility);
    this.flushTimer = this.windowRef?.setInterval?.(
      () => this.flush().catch(() => {}),
      PLAYBACK_DIAGNOSTICS_FLUSH_MS,
    );
    this.clockTimer = this.windowRef?.setInterval?.(
      () => this.synchronizeClock().catch(() => {}),
      PLAYBACK_DIAGNOSTICS_CLOCK_RECALIBRATION_MS,
    );
    await this.ensureBootstrap().catch(() => null);
    if (this.stateMachine.closing && this.finalSourceSequence != null) this.retryPendingClose();
    else this.flushSoon();
    return Boolean(this.sourceId);
  }

  stop() {
    this.running = false;
    [
      [this.flushTimer, "clearInterval"],
      [this.clockTimer, "clearInterval"],
      [this.flushSoonTimer, "clearTimeout"],
      [this.retryTimer, "clearTimeout"],
    ].forEach(([timer, method]) => {
      if (timer != null) this.windowRef?.[method]?.(timer);
    });
    this.flushTimer = null;
    this.flushSoonTimer = null;
    this.retryTimer = null;
    this.clockTimer = null;
    this.windowRef?.removeEventListener?.("online", this.handleOnline);
    this.windowRef?.removeEventListener?.("pagehide", this.handlePageHide);
    this.documentRef?.removeEventListener?.("visibilitychange", this.handleVisibility);
  }

  notePersistedEvent(event) {
    if (this.stateMachine.closing || this.stateMachine.terminal) return;
    this.beaconEvents.push(event);
    if (this.beaconEvents.length > BEACON_EVENT_LIMIT) {
      this.beaconEvents = this.beaconEvents.slice(-BEACON_EVENT_LIMIT);
    }
  }

  pruneBeaconEvents(watermark) {
    const ack = Math.max(0, Number(watermark) || 0);
    this.beaconEvents = this.beaconEvents.filter(
      (event) => Number(event.source_sequence) > ack,
    );
  }

  async ensureBootstrap() {
    if (this.stateMachine.terminal) throw new Error(`Diagnostics transport is ${this.stateMachine.state}`);
    if (this.bootstrapComplete && this.sourceId) return this.sourceId;
    if (this.bootstrapInFlight) return this.bootstrapInFlight;
    this.bootstrapInFlight = (async () => {
      const {
        client_timer_resolution_us: _clientTimerResolutionUs,
        ...wireBootstrapContext
      } = this.bootstrapContext;
      const response = await diagnosticFetch(this.fetchRef, "/bootstrap", {
        playback_session_id: this.playbackSessionId,
        client_instance_id: this.clientInstanceId,
        ...wireBootstrapContext,
      });
      this.sourceId = response.source_id;
      this.bootstrapComplete = true;
      this.batchMaxEvents = Math.max(1, Number(response.batch_max_events) || 1);
      this.batchMaxBytes = Math.max(1, Number(response.batch_max_bytes) || 1);
      this.spool.setMaxBytes?.(response.client_spool_max_bytes);
      await this.spool.updateRecoveryState?.(this.playbackSessionId, {
        client_instance_id: this.clientInstanceId,
        source_id: this.sourceId,
        last_durable_ack: Number(response.ack_watermark) || 0,
        server_source_state: response.state || "active",
      });
      if (Number(response.ack_watermark) > 0) await this.acknowledge(response.ack_watermark);
      this.resetBackoff();
      this.onHealth("transport", "bootstrap_succeeded", { state: response.state || "active" });
      if (!this.stateMachine.closing) await this.synchronizeClock({ force: true }).catch(() => null);
      return this.sourceId;
    })().catch((error) => {
      this.bootstrapComplete = false;
      this.handleFailure(error);
      throw error;
    }).finally(() => {
      this.bootstrapInFlight = null;
    });
    return this.bootstrapInFlight;
  }

  async synchronizeClock({ force = false } = {}) {
    if (
      !this.running
      || this.stateMachine.closing
      || this.stateMachine.terminal
      || this.stateMachine.state === "paused_authentication"
      || this.clockInFlight
    ) return this.clockInFlight;
    if (!force && monotonicNow() < this.nextRetryAtMs) return null;
    if (!this.sourceId) await this.ensureBootstrap();
    this.clockInFlight = synchronizeDiagnosticClock(async ({
      sampleId,
      clientSendWallMs,
      clientSendMonotonicUs,
    }) => diagnosticFetch(this.fetchRef, "/clock", {
      diagnostics_session_id: this.playbackSessionId,
      source_id: this.sourceId,
      sample_id: sampleId,
      client_send_wall_time_ms: clientSendWallMs,
      client_send_monotonic_time_us: clientSendMonotonicUs,
    }), {
      timerResolutionUs: this.bootstrapContext?.client_timer_resolution_us || 0,
      previous: this.clock,
    }).then((clock) => {
      this.clock = clock;
      this.onHealth("clock", clock.clock_valid === false ? "clock_invalid" : "clock_synchronized", {
        generation: clock.clock_generation,
      });
      if (clock.clock_valid === false) this.scheduleClockRecalibration();
      return clock;
    }).catch((error) => {
      if (error?.category === "session_missing") {
        this.sourceId = null;
        this.bootstrapComplete = false;
      }
      this.handleFailure(error);
      return null;
    }).finally(() => {
      this.clockInFlight = null;
    });
    return this.clockInFlight;
  }

  scheduleClockRecalibration() {
    const schedule = this.windowRef?.setTimeout?.bind(this.windowRef) || globalThis.setTimeout;
    schedule(() => this.synchronizeClock({ force: true }).catch(() => {}), 1_000);
  }

  async acknowledge(watermark) {
    const result = await this.spool.acknowledge(this.playbackSessionId, watermark);
    this.pruneBeaconEvents(watermark);
    return result;
  }

  async flushPendingGap() {
    const [gap] = await this.spool.pendingGaps?.(this.playbackSessionId) || [];
    if (!gap) return false;
    const response = await diagnosticFetch(this.fetchRef, "/gap", {
      diagnostics_session_id: this.playbackSessionId,
      source_id: this.sourceId,
      start_sequence: gap.start_sequence,
      end_sequence: gap.end_sequence,
      reason_code: gap.reason_code,
      rejected_event_name: gap.rejected_event_name || null,
      rejected_event_hash: gap.rejected_event_hash || null,
    });
    await this.spool.completeGap?.(this.playbackSessionId, gap, response.ack_watermark);
    await this.acknowledge(response.ack_watermark);
    this.onHealth("transport", "gap_durable", {
      start_sequence: gap.start_sequence,
      end_sequence: gap.end_sequence,
      reason_code: gap.reason_code,
    });
    return true;
  }

  async queueRejectedGap(error, entries) {
    const details = error?.details || {};
    const index = Number(details.event_index);
    const sequence = Number(details.source_sequence);
    const entry = Number.isInteger(index) && index >= 0 ? entries[index] : null;
    if (!entry || !Number.isInteger(sequence) || sequence !== Number(entry.source_sequence)) {
      await this.rejectLocalRecovery(
        error?.code || "diagnostics_invalid_event_unidentified",
      );
      return false;
    }
    try {
      const queued = Boolean(await this.spool.queueGap?.(this.playbackSessionId, {
        start_sequence: sequence,
        end_sequence: sequence,
        reason_code: "client_invalid_event",
        rejected_event_name: String(entry.event?.event_name || "unknown").slice(0, 128),
      }, { sequence }));
      if (!queued) await this.rejectLocalRecovery("diagnostics_gap_ledger_full");
      return queued;
    } catch {
      await this.rejectLocalRecovery("diagnostics_gap_persistence_failed");
      return false;
    }
  }

  async splitOversizedBatch(entries) {
    if (entries.length > 1) {
      this.batchMaxEvents = Math.max(1, Math.floor(entries.length / 2));
      this.batchMaxBytes = Math.max(1_024, Math.floor(this.batchMaxBytes / 2));
      return true;
    }
    const entry = entries[0];
    if (!entry) return false;
    try {
      const queued = Boolean(await this.spool.queueGap?.(this.playbackSessionId, {
        start_sequence: entry.source_sequence,
        end_sequence: entry.source_sequence,
        reason_code: "client_request_too_large",
        rejected_event_name: String(entry.event?.event_name || "unknown").slice(0, 128),
      }, { sequence: entry.source_sequence }));
      if (!queued) await this.rejectLocalRecovery("diagnostics_gap_ledger_full");
      return queued;
    } catch {
      await this.rejectLocalRecovery("diagnostics_gap_persistence_failed");
      return false;
    }
  }

  async rejectLocalRecovery(code) {
    this.transition("terminal_rejected");
    await this.spool.updateRecoveryState?.(this.playbackSessionId, {
      close_state: "terminal_rejected",
      last_close_response_code: code,
    });
    this.onHealth("transport", "local_gap_accounting_failed", { code });
  }

  async flush({ force = false } = {}) {
    if (!this.running || this.stateMachine.terminal) return null;
    if (this.stateMachine.state === "paused_authentication") return null;
    if (!force && monotonicNow() < this.nextRetryAtMs) return null;
    if (this.flushInFlight) return this.flushInFlight;
    this.flushInFlight = (async () => {
      await this.ensureBootstrap();
      while (await this.flushPendingGap()) {
        // Gaps are bounded and become durable before later events are uploaded.
      }
      const { entries, totalBytes } = await this.spool.readBatch(this.playbackSessionId, {
        maxEvents: this.batchMaxEvents,
        maxBytes: this.batchMaxBytes,
      });
      if (!entries.length) return null;
      const startedAt = monotonicNow();
      let response;
      try {
        response = await diagnosticFetch(this.fetchRef, "/batch", {
          diagnostics_session_id: this.playbackSessionId,
          source_id: this.sourceId,
          events: entries.map((entry) => entry.event),
        });
      } catch (error) {
        let recoveryHandled = true;
        if (error?.category === "request_too_large") {
          recoveryHandled = await this.splitOversizedBatch(entries);
        } else if (error?.category === "invalid_event") {
          recoveryHandled = await this.queueRejectedGap(error, entries);
        } else if (error?.category === "session_missing") {
          this.sourceId = null;
          this.bootstrapComplete = false;
          await this.spool.updateRecoveryState?.(this.playbackSessionId, { source_id: null });
        }
        if (!recoveryHandled) return null;
        this.handleFailure(error);
        return null;
      }
      const ack = await this.acknowledge(response.ack_watermark);
      this.resetBackoff();
      this.onHealth("transport", "batch_acked", {
        batch_events: entries.length,
        batch_bytes: totalBytes,
        upload_latency_ms: monotonicNow() - startedAt,
        duplicate_count: response.duplicate,
        out_of_order_count: response.out_of_order,
        queue_depth: Math.max(0, entries.length - ack.deletedEvents),
      });
      if (entries.length >= this.batchMaxEvents) this.flushSoon(0);
      return response;
    })().catch((error) => {
      this.handleFailure(error);
      return null;
    }).finally(() => {
      this.flushInFlight = null;
    });
    return this.flushInFlight;
  }

  handleFailure(error) {
    const category = error?.category || "retriable";
    this.retries += 1;
    this.onHealth("transport", category, { code: error?.code || "diagnostics_request_failed" });
    if (category === "authentication_required") {
      this.resumeState = this.stateMachine.closing ? "closing" : "open";
      this.transition("paused_authentication");
      void this.spool.updateRecoveryState?.(this.playbackSessionId, {
        close_state: "paused_authentication",
        last_close_response_code: error?.code || "authentication_required",
      });
      return;
    }
    if (category === "capacity_reached") {
      this.resumeState = this.stateMachine.closing ? "closing" : "open";
      this.transition("paused_capacity");
      void this.spool.updateRecoveryState?.(this.playbackSessionId, {
        close_state: "paused_capacity",
        last_close_response_code: error?.code || "diagnostics_capacity_reached",
      });
      this.scheduleRetry({ minimumDelayMs: PLAYBACK_DIAGNOSTICS_RETRY_MAX_MS });
      return;
    }
    if (category === "session_sealed") {
      void this.handleServerSealed();
      return;
    }
    if (["session_corrupt", "identity_conflict", "permanent_invalid"].includes(category)) {
      this.transition("terminal_rejected");
      void this.spool.updateRecoveryState?.(this.playbackSessionId, {
        close_state: "terminal_rejected",
        last_close_response_code: error?.code || category,
      });
      return;
    }
    if (category === "session_closing" && this.stateMachine.state === "open") {
      this.transition("closing");
    }
    this.scheduleRetry();
  }

  async handleServerSealed() {
    const stats = await this.spool.stats(this.playbackSessionId);
    const pending = await this.spool.pendingGaps?.(this.playbackSessionId) || [];
    if (stats.queueDepth > 0 || pending.length > 0) {
      this.transition("terminal_rejected");
      await this.spool.updateRecoveryState?.(this.playbackSessionId, {
        close_state: "terminal_rejected",
        last_close_response_code: "sealed_before_local_ack",
      });
      return;
    }
    this.transition("sealed");
    await this.spool.markCloseState?.(this.playbackSessionId, "sealed");
    await this.spool.cleanupSealedSession?.(this.playbackSessionId);
    this.stop();
    this.notifySealed();
  }

  notifySealed() {
    try {
      this.onSealed();
    } catch {
      this.onHealth("transport", "sealed_callback_failed", {});
    }
  }

  resetBackoff() {
    this.retries = 0;
    this.nextRetryAtMs = 0;
    if (this.retryTimer != null) this.windowRef?.clearTimeout?.(this.retryTimer);
    this.retryTimer = null;
  }

  retryDelayMs() {
    const exponential = Math.min(
      PLAYBACK_DIAGNOSTICS_RETRY_MAX_MS,
      PLAYBACK_DIAGNOSTICS_RETRY_BASE_MS * (2 ** Math.min(this.retries, 8)),
    );
    return Math.round(exponential * (0.75 + this.randomRef() * 0.5));
  }

  scheduleRetry({ minimumDelayMs = 0 } = {}) {
    if (!this.running || this.stateMachine.terminal || this.retryTimer != null) return;
    if (this.stateMachine.state === "paused_authentication") return;
    const delay = Math.max(minimumDelayMs, this.retryDelayMs());
    this.nextRetryAtMs = monotonicNow() + delay;
    const schedule = this.windowRef?.setTimeout?.bind(this.windowRef) || globalThis.setTimeout;
    this.retryTimer = schedule(() => {
      this.retryTimer = null;
      this.nextRetryAtMs = 0;
      if (this.stateMachine.state === "paused_capacity") this.transition(this.resumeState);
      if (this.stateMachine.closing) this.retryPendingClose();
      else this.flush({ force: true }).catch(() => {});
    }, delay);
  }

  flushSoon(delay = PLAYBACK_DIAGNOSTICS_FLUSH_SOON_MS) {
    if (
      !this.running
      || this.stateMachine.terminal
      || this.stateMachine.state === "paused_authentication"
      || this.flushSoonTimer != null
    ) return;
    if (monotonicNow() < this.nextRetryAtMs) return;
    const schedule = this.windowRef?.setTimeout?.bind(this.windowRef) || globalThis.setTimeout;
    this.flushSoonTimer = schedule(() => {
      this.flushSoonTimer = null;
      this.flush().catch(() => {});
    }, Math.max(0, delay));
  }

  wake({ authenticationRestored = false } = {}) {
    if (!this.running || this.stateMachine.terminal) return;
    if (authenticationRestored && this.stateMachine.state === "paused_authentication") {
      this.transition(this.resumeState);
    }
    if (this.stateMachine.state === "paused_authentication") return;
    if (this.stateMachine.state === "paused_capacity") this.transition(this.resumeState);
    this.resetBackoff();
    if (this.stateMachine.closing) this.retryPendingClose();
    else {
      this.synchronizeClock({ force: true }).catch(() => {});
      this.flushSoon(0);
    }
  }

  sendBeaconBestEffort() {
    if (
      this.stateMachine.closing
      || this.stateMachine.terminal
      || !this.sourceId
      || !this.beaconEvents.length
      || !this.navigatorRef?.sendBeacon
    ) return false;
    const body = JSON.stringify({
      diagnostics_session_id: this.playbackSessionId,
      source_id: this.sourceId,
      events: this.beaconEvents.slice(-BEACON_EVENT_LIMIT),
    });
    try {
      return this.navigatorRef.sendBeacon(
        `${API_ROOT}/batch`,
        new Blob([body], { type: "application/json" }),
      );
    } catch {
      return false;
    }
  }

  async closeSession(reason, finalSourceSequence) {
    if (this.closeInFlight) return this.closeInFlight;
    this.closeReason = String(reason || "client_closed").slice(0, 128);
    this.finalSourceSequence = Math.max(0, Number(finalSourceSequence) || 0);
    if (this.stateMachine.state === "open") this.transition("closing");
    const priorRecovery = await this.spool.getRecoveryState?.(this.playbackSessionId);
    await this.spool.updateRecoveryState?.(this.playbackSessionId, {
      close_reason: this.closeReason,
      final_source_sequence: this.finalSourceSequence,
      close_state: "closing",
      active_lease_id: null,
      active_lease_expires_at_ms: 0,
      close_requested_timestamp_ms: Date.now(),
      recovery_generation: Number(priorRecovery?.recovery_generation || 0) + 1,
    });
    this.closeInFlight = this.performClose().finally(() => {
      this.closeInFlight = null;
    });
    return this.closeInFlight;
  }

  retryPendingClose() {
    if (this.closeInFlight || this.finalSourceSequence == null || this.stateMachine.terminal) return;
    this.closeInFlight = this.performClose().finally(() => {
      this.closeInFlight = null;
    });
  }

  async performClose() {
    if (this.stateMachine.state === "paused_authentication") return false;
    if (!this.sourceId) {
      await this.ensureBootstrap().catch(() => null);
      if (!this.sourceId) return false;
    }
    for (let attempt = 0; attempt < CLOSE_FLUSH_ATTEMPTS; attempt += 1) {
      await this.flush({ force: true });
      const recovery = await this.spool.getRecoveryState?.(this.playbackSessionId);
      if (Number(recovery?.last_durable_ack || 0) >= Number(this.finalSourceSequence || 0)) break;
    }
    await this.spool.updateRecoveryState?.(this.playbackSessionId, {
      last_close_attempt_timestamp_ms: Date.now(),
    });
    try {
      const response = await diagnosticFetch(this.fetchRef, "/close", {
        diagnostics_session_id: this.playbackSessionId,
        source_id: this.sourceId,
        reason: this.closeReason || "client_closed",
        final_source_sequence: this.finalSourceSequence,
      });
      if (Number(response.ack_watermark) >= 0) await this.acknowledge(response.ack_watermark);
      await this.spool.updateRecoveryState?.(this.playbackSessionId, {
        close_state: response.state || "closing",
        last_close_response_code: response.state || "closing",
      });
      if (response.state === "sealed" && response.finalized === true) {
        this.transition("sealed");
        await this.spool.cleanupSealedSession?.(this.playbackSessionId);
        this.stop();
        this.notifySealed();
        return true;
      }
      this.scheduleRetry();
      return false;
    } catch (error) {
      this.handleFailure(error);
      return false;
    }
  }
}
