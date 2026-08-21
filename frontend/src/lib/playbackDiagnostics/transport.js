import {
  PLAYBACK_DIAGNOSTICS_CLOCK_RECALIBRATION_MS,
  PLAYBACK_DIAGNOSTICS_DEFAULT_BATCH_MAX_BYTES,
  PLAYBACK_DIAGNOSTICS_DEFAULT_BATCH_MAX_EVENTS,
  PLAYBACK_DIAGNOSTICS_FLUSH_MS,
  PLAYBACK_DIAGNOSTICS_FLUSH_SOON_MS,
} from "./constants";
import { synchronizeDiagnosticClock } from "./clock";

const API_ROOT = "/api/playback-diagnostics";
const BEACON_EVENT_LIMIT = 24;

async function diagnosticFetch(fetchRef, path, data) {
  const response = await fetchRef(`${API_ROOT}${path}`, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = new Error(`Playback diagnostics request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return response.json();
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
  }) {
    this.playbackSessionId = playbackSessionId;
    this.spool = spool;
    this.bootstrapContext = bootstrapContext;
    this.fetchRef = fetchRef;
    this.onMetric = onMetric;
    this.windowRef = windowRef;
    this.documentRef = documentRef;
    this.navigatorRef = navigatorRef;
    this.clientInstanceId = null;
    this.sourceId = null;
    this.clock = {};
    this.batchMaxEvents = PLAYBACK_DIAGNOSTICS_DEFAULT_BATCH_MAX_EVENTS;
    this.batchMaxBytes = PLAYBACK_DIAGNOSTICS_DEFAULT_BATCH_MAX_BYTES;
    this.running = false;
    this.bootstrapInFlight = null;
    this.flushInFlight = null;
    this.clockInFlight = null;
    this.flushTimer = null;
    this.flushSoonTimer = null;
    this.clockTimer = null;
    this.beaconEvents = [];
    this.retries = 0;
    this.handleOnline = () => this.flushSoon();
    this.handleVisibility = () => {
      if (this.documentRef?.visibilityState === "visible") {
        this.synchronizeClock().catch(() => {});
        this.flushSoon();
      }
    };
    this.handlePageHide = () => this.sendBeaconBestEffort();
  }

  async start() {
    if (this.running || typeof this.fetchRef !== "function") return false;
    this.running = true;
    this.clientInstanceId = await this.spool.getOrCreateClientInstanceId(this.playbackSessionId);
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
    if (this.flushTimer != null) this.windowRef?.clearInterval?.(this.flushTimer);
    if (this.flushSoonTimer != null) this.windowRef?.clearTimeout?.(this.flushSoonTimer);
    if (this.clockTimer != null) this.windowRef?.clearInterval?.(this.clockTimer);
    this.flushTimer = null;
    this.flushSoonTimer = null;
    this.clockTimer = null;
    this.windowRef?.removeEventListener?.("online", this.handleOnline);
    this.windowRef?.removeEventListener?.("pagehide", this.handlePageHide);
    this.documentRef?.removeEventListener?.("visibilitychange", this.handleVisibility);
  }

  notePersistedEvent(event) {
    this.beaconEvents.push(event);
    if (this.beaconEvents.length > BEACON_EVENT_LIMIT) {
      this.beaconEvents.splice(0, this.beaconEvents.length - BEACON_EVENT_LIMIT);
    }
  }

  async ensureBootstrap() {
    if (this.sourceId) return this.sourceId;
    if (this.bootstrapInFlight) return this.bootstrapInFlight;
    this.bootstrapInFlight = (async () => {
      const response = await diagnosticFetch(this.fetchRef, "/bootstrap", {
        playback_session_id: this.playbackSessionId,
        client_instance_id: this.clientInstanceId,
        ...this.bootstrapContext,
      });
      this.sourceId = response.source_id;
      this.batchMaxEvents = response.batch_max_events;
      this.batchMaxBytes = response.batch_max_bytes;
      this.spool.setMaxBytes(response.client_spool_max_bytes);
      if (Number(response.ack_watermark) > 0) {
        await this.spool.acknowledge(this.playbackSessionId, response.ack_watermark);
      }
      this.onMetric("recorder_bootstrap_succeeded", {
        clock_algorithm: response.clock_algorithm,
        capacity_state: "server_available",
      });
      await this.synchronizeClock().catch(() => null);
      return this.sourceId;
    })().catch((error) => {
      this.retries += 1;
      this.onMetric("recorder_upload_retry", {
        retries: this.retries,
        error_class: error?.name || "Error",
      });
      throw error;
    }).finally(() => {
      this.bootstrapInFlight = null;
    });
    return this.bootstrapInFlight;
  }

  async synchronizeClock() {
    if (!this.running || this.clockInFlight) return this.clockInFlight;
    if (!this.sourceId) {
      await this.ensureBootstrap();
    }
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
    })).then((clock) => {
      this.clock = clock;
      this.onMetric("clock_synchronized", clock);
      return clock;
    }).finally(() => {
      this.clockInFlight = null;
    });
    return this.clockInFlight;
  }

  async flush() {
    if (!this.running) return null;
    if (this.flushInFlight) return this.flushInFlight;
    this.flushInFlight = (async () => {
      await this.ensureBootstrap();
      const { entries, totalBytes } = await this.spool.readBatch(this.playbackSessionId, {
        maxEvents: this.batchMaxEvents,
        maxBytes: this.batchMaxBytes,
      });
      if (!entries.length) return null;
      const startedAt = performance.now();
      const response = await diagnosticFetch(this.fetchRef, "/batch", {
        diagnostics_session_id: this.playbackSessionId,
        source_id: this.sourceId,
        events: entries.map((entry) => entry.event),
      });
      const ack = await this.spool.acknowledge(
        this.playbackSessionId,
        response.ack_watermark,
      );
      this.retries = 0;
      this.onMetric("recorder_batch_acked", {
        batch_events: entries.length,
        batch_bytes: totalBytes,
        upload_bytes: totalBytes,
        upload_latency_ms: performance.now() - startedAt,
        batches_sent: 1,
        batches_acked: 1,
        duplicate_count: response.duplicate,
        out_of_order_count: response.out_of_order,
        capacity_state: response.capacity_state,
        queue_depth: Math.max(0, entries.length - ack.deletedEvents),
      });
      if (entries.length >= this.batchMaxEvents) this.flushSoon();
      return response;
    })().catch((error) => {
      this.retries += 1;
      this.onMetric("recorder_upload_retry", {
        retries: this.retries,
        error_class: error?.name || "Error",
      });
      return null;
    }).finally(() => {
      this.flushInFlight = null;
    });
    return this.flushInFlight;
  }

  flushSoon() {
    if (!this.running || this.flushSoonTimer != null) return;
    const schedule = this.windowRef?.setTimeout?.bind(this.windowRef) || globalThis.setTimeout;
    this.flushSoonTimer = schedule(() => {
      this.flushSoonTimer = null;
      this.flush().catch(() => {});
    }, PLAYBACK_DIAGNOSTICS_FLUSH_SOON_MS);
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
    if (!this.sourceId) return false;
    try {
      await this.flush();
      const response = await diagnosticFetch(this.fetchRef, "/close", {
        diagnostics_session_id: this.playbackSessionId,
        source_id: this.sourceId,
        reason,
        final_source_sequence: finalSourceSequence,
      });
      if (Number(response.ack_watermark) > 0) {
        await this.spool.acknowledge(this.playbackSessionId, response.ack_watermark);
      }
      return Boolean(response.accepted);
    } catch {
      return false;
    }
  }
}
