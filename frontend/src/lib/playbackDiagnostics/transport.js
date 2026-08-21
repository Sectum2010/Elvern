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
import { createDiagnosticId } from "./schema";

const API_ROOT = "/api/playback-diagnostics";
const BEACON_EVENT_LIMIT = 24;
const CLOSE_FLUSH_ATTEMPTS = 3;

export class PlaybackDiagnosticsRequestError extends Error {
  constructor(status, category) {
    super(`Playback diagnostics request failed (${status})`);
    this.name = "PlaybackDiagnosticsRequestError";
    this.status = status;
    this.category = category;
  }
}

export function classifyDiagnosticResponse(status) {
  if (status === 400 || status === 413) return "permanent_invalid";
  if (status === 401 || status === 403) return "authentication_required";
  if (status === 404) return "session_missing";
  if (status === 409 || status === 410) return "session_sealed";
  if (status === 507) return "capacity_reached";
  if (status === 429 || status === 503 || status >= 500) return "retriable";
  return status >= 400 ? "permanent_invalid" : "ok";
}

async function diagnosticFetch(fetchRef, path, data) {
  const response = await fetchRef(`${API_ROOT}${path}`, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    throw new PlaybackDiagnosticsRequestError(
      response.status,
      classifyDiagnosticResponse(response.status),
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
    bootstrapContext,
    fetchRef = globalThis.fetch?.bind(globalThis),
    onMetric = () => {},
    windowRef = globalThis.window,
    documentRef = globalThis.document,
    navigatorRef = globalThis.navigator,
    randomRef = Math.random,
  }) {
    this.playbackSessionId = playbackSessionId;
    this.spool = spool;
    this.bootstrapContext = bootstrapContext;
    this.fetchRef = fetchRef;
    this.onMetric = onMetric;
    this.windowRef = windowRef;
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
    this.terminalState = null;
    this.bootstrapInFlight = null;
    this.flushInFlight = null;
    this.clockInFlight = null;
    this.flushTimer = null;
    this.flushSoonTimer = null;
    this.retryTimer = null;
    this.clockTimer = null;
    this.beaconEvents = [];
    this.retries = 0;
    this.nextRetryAtMs = 0;
    this.handleOnline = () => {
      this.resetBackoff();
      this.flushSoon(0);
    };
    this.handleVisibility = () => {
      if (this.documentRef?.visibilityState === "visible") {
        this.synchronizeClock({ force: true }).catch(() => {});
        this.flushSoon(0);
      }
    };
    this.handlePageHide = () => this.sendBeaconBestEffort();
  }

  async start() {
    if (this.running || typeof this.fetchRef !== "function") return false;
    this.running = true;
    this.clientInstanceId = await this.spool.getOrCreateClientInstanceId(this.playbackSessionId);
    const recovery = await this.spool.getRecoveryState?.(this.playbackSessionId);
    if (recovery?.client_instance_id === this.clientInstanceId && recovery?.source_id) {
      this.sourceId = recovery.source_id;
    }
    await this.spool.updateRecoveryState?.(this.playbackSessionId, {
      client_instance_id: this.clientInstanceId,
      close_state: recovery?.close_state || "open",
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
    this.flushSoon();
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
    if (this.terminalState) throw new Error(`Diagnostics transport is ${this.terminalState}`);
    if (this.bootstrapComplete && this.sourceId) return this.sourceId;
    if (this.bootstrapInFlight) return this.bootstrapInFlight;
    this.bootstrapInFlight = (async () => {
      const response = await diagnosticFetch(this.fetchRef, "/bootstrap", {
        playback_session_id: this.playbackSessionId,
        client_instance_id: this.clientInstanceId,
        ...this.bootstrapContext,
      });
      this.sourceId = response.source_id;
      this.bootstrapComplete = true;
      this.batchMaxEvents = response.batch_max_events;
      this.batchMaxBytes = response.batch_max_bytes;
      this.spool.setMaxBytes?.(response.client_spool_max_bytes);
      await this.spool.updateRecoveryState?.(this.playbackSessionId, {
        client_instance_id: this.clientInstanceId,
        source_id: this.sourceId,
        last_durable_ack: Number(response.ack_watermark) || 0,
      });
      if (Number(response.ack_watermark) > 0) {
        await this.acknowledge(response.ack_watermark);
      }
      this.resetBackoff();
      this.onMetric("recorder_bootstrap_succeeded", {
        clock_algorithm: response.clock_algorithm,
        capacity_state: "server_available",
      });
      await this.synchronizeClock({ force: true }).catch(() => null);
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
    if (!this.running || this.terminalState || this.clockInFlight) return this.clockInFlight;
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
      this.onMetric("clock_synchronized", clock);
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

  async acknowledge(watermark) {
    const result = await this.spool.acknowledge(this.playbackSessionId, watermark);
    this.pruneBeaconEvents(watermark);
    return result;
  }

  async replacePermanentlyRejectedEvent(entry, category) {
    const rejected = entry.event;
    const gap = {
      ...rejected,
      event_id: createDiagnosticId("event"),
      event_name: "telemetry_gap",
      severity: "warning",
      priority: "critical",
      observation_kind: "measured_client",
      payload: {
        reason: `server_rejected_${category}`,
        events_dropped: 1,
        rejected_event_name: String(rejected.event_name || "unknown").slice(0, 128),
      },
    };
    return this.spool.replaceWithGap?.(
      this.playbackSessionId,
      entry.source_sequence,
      gap,
    );
  }

  async flush({ force = false } = {}) {
    if (!this.running || this.terminalState) return null;
    if (!force && monotonicNow() < this.nextRetryAtMs) return null;
    if (this.flushInFlight) return this.flushInFlight;
    this.flushInFlight = (async () => {
      await this.ensureBootstrap();
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
        if (error?.category === "permanent_invalid" && entries.length) {
          await this.replacePermanentlyRejectedEvent(entries[0], error.category);
        }
        if (error?.category === "session_missing") {
          this.sourceId = null;
          this.bootstrapComplete = false;
          await this.spool.updateRecoveryState?.(this.playbackSessionId, { source_id: null });
        }
        this.handleFailure(error);
        return null;
      }
      const ack = await this.acknowledge(response.ack_watermark);
      this.resetBackoff();
      this.onMetric("recorder_batch_acked", {
        batch_events: entries.length,
        batch_bytes: totalBytes,
        upload_bytes: totalBytes,
        upload_latency_ms: monotonicNow() - startedAt,
        batches_sent: 1,
        batches_acked: 1,
        duplicate_count: response.duplicate,
        out_of_order_count: response.out_of_order,
        capacity_state: response.capacity_state,
        queue_depth: Math.max(0, entries.length - ack.deletedEvents),
      });
      if (entries.length >= this.batchMaxEvents) this.flushSoon(0);
      return response;
    })().finally(() => {
      this.flushInFlight = null;
    });
    return this.flushInFlight;
  }

  handleFailure(error) {
    const category = error?.category || "retriable";
    if (["authentication_required", "session_sealed", "capacity_reached"].includes(category)) {
      this.terminalState = category;
    }
    this.retries += 1;
    this.onMetric("recorder_upload_retry", {
      retries: this.retries,
      error_class: error?.name || "Error",
      reason: category,
    });
    if (category === "retriable" || category === "session_missing" || category === "permanent_invalid") {
      this.scheduleRetry();
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

  scheduleRetry() {
    if (!this.running || this.terminalState || this.retryTimer != null) return;
    const delay = this.retryDelayMs();
    this.nextRetryAtMs = monotonicNow() + delay;
    const schedule = this.windowRef?.setTimeout?.bind(this.windowRef) || globalThis.setTimeout;
    this.retryTimer = schedule(() => {
      this.retryTimer = null;
      this.nextRetryAtMs = 0;
      this.flush({ force: true }).catch(() => {});
    }, delay);
  }

  flushSoon(delay = PLAYBACK_DIAGNOSTICS_FLUSH_SOON_MS) {
    if (!this.running || this.terminalState || this.flushSoonTimer != null) return;
    if (monotonicNow() < this.nextRetryAtMs) return;
    const schedule = this.windowRef?.setTimeout?.bind(this.windowRef) || globalThis.setTimeout;
    this.flushSoonTimer = schedule(() => {
      this.flushSoonTimer = null;
      this.flush().catch(() => {});
    }, Math.max(0, delay));
  }

  sendBeaconBestEffort() {
    if (!this.sourceId || !this.beaconEvents.length || !this.navigatorRef?.sendBeacon) return false;
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
    if (!this.sourceId || this.terminalState === "authentication_required") return false;
    await this.spool.markCloseState?.(this.playbackSessionId, "closing");
    for (let attempt = 0; attempt < CLOSE_FLUSH_ATTEMPTS; attempt += 1) {
      await this.flush({ force: true });
      const recovery = await this.spool.getRecoveryState?.(this.playbackSessionId);
      if (Number(recovery?.last_durable_ack || 0) >= Number(finalSourceSequence || 0)) break;
    }
    try {
      const response = await diagnosticFetch(this.fetchRef, "/close", {
        diagnostics_session_id: this.playbackSessionId,
        source_id: this.sourceId,
        reason,
        final_source_sequence: finalSourceSequence,
      });
      if (Number(response.ack_watermark) >= 0) {
        await this.acknowledge(response.ack_watermark);
      }
      await this.spool.markCloseState?.(this.playbackSessionId, response.state || "closing");
      if (response.state === "sealed" && response.finalized === true) {
        await this.spool.cleanupSealedSession?.(this.playbackSessionId);
      }
      return response.state === "sealed" && response.finalized === true;
    } catch (error) {
      this.handleFailure(error);
      return false;
    }
  }
}
