import { collectPlaybackDiagnosticCapabilities, unsupportedWebCapabilities } from "./capabilities";
import { estimateTimerResolution } from "./clock";
import {
  PLAYBACK_DIAGNOSTICS_PRE_SPOOL_MAX_BYTES,
  PLAYBACK_DIAGNOSTICS_PRE_SPOOL_MAX_EVENTS,
} from "./constants";
import { createDiagnosticSpool } from "./indexedDbSpool";
import { HlsJsDiagnosticObserver } from "./hlsObserver";
import { PlaybackLifecycleDiagnosticObserver } from "./lifecycleObserver";
import { MediaElementDiagnosticObserver } from "./mediaObserver";
import { PlaybackPerformanceDiagnosticObserver } from "./performanceObserver";
import { classifyBrowserPlatform } from "./privacy";
import {
  captureClientClock,
  createDiagnosticId,
  createPlaybackDiagnosticEvent,
} from "./schema";
import { PlaybackDiagnosticsTransport } from "./transport";

const SELF_MONITOR_INTERVAL_MS = 10_000;

function monotonicNow() {
  return globalThis.performance?.now?.() ?? Date.now();
}

function approximateBytes(value) {
  try {
    return new TextEncoder().encode(JSON.stringify(value)).byteLength;
  } catch {
    return PLAYBACK_DIAGNOSTICS_PRE_SPOOL_MAX_BYTES + 1;
  }
}

export class PlaybackDiagnosticRecorder {
  constructor({
    playbackSessionId,
    video,
    context = {},
    hlsEvents = {},
    provisionalEvents = [],
    playbackAttemptId = null,
    windowRef = globalThis.window,
    documentRef = globalThis.document,
    navigatorRef = globalThis.navigator,
    fetchRef = globalThis.fetch?.bind(globalThis),
  }) {
    this.playbackSessionId = playbackSessionId;
    this.video = video;
    this.context = { ...context };
    this.hlsEvents = hlsEvents;
    this.windowRef = windowRef;
    this.documentRef = documentRef;
    this.navigatorRef = navigatorRef;
    this.fetchRef = fetchRef;
    this.spool = null;
    this.transport = null;
    this.mediaObserver = null;
    this.lifecycleObserver = null;
    this.performanceObserver = null;
    this.hlsObserver = null;
    this.attachedHls = null;
    this.running = false;
    this.stopping = false;
    this.writeChain = Promise.resolve();
    this.selfMonitorTimer = null;
    this.lastOrigin = null;
    this.lastSequence = 0;
    this.gapBeingRecorded = false;
    this.timerResolutionUs = null;
    this.lastRecorderOverheadMs = 0;
    this.lastRecorderQueueDelayMs = 0;
    this.playbackAttemptId = playbackAttemptId || createDiagnosticId("attempt");
    this.attachmentId = createDiagnosticId("attachment");
    this.preSpool = [];
    this.preSpoolBytes = 0;
    this.preSpoolDrops = 0;
    provisionalEvents.forEach((entry) => this.bufferBeforeSpool(entry));
  }

  async start() {
    if (this.running) return;
    this.running = true;
    const platform = classifyBrowserPlatform(this.navigatorRef);
    this.context = { ...this.context, ...platform };
    const capabilities = collectPlaybackDiagnosticCapabilities({
      windowRef: this.windowRef,
      documentRef: this.documentRef,
      navigatorRef: this.navigatorRef,
      video: this.video,
    });
    capabilities.client_fragment_loader_detail = this.context.hls_engine === "hls.js"
      ? "detected"
      : "not_applicable";
    this.timerResolutionUs = estimateTimerResolution({
      now: () => this.windowRef?.performance?.now?.() || performance.now(),
    });
    const spoolResult = await createDiagnosticSpool({
      indexedDBRef: this.windowRef?.indexedDB || globalThis.indexedDB,
      keyRangeRef: this.windowRef?.IDBKeyRange || globalThis.IDBKeyRange,
    });
    if (!this.running) {
      spoolResult.spool.close();
      return;
    }
    this.spool = spoolResult.spool;
    this.transport = new PlaybackDiagnosticsTransport({
      playbackSessionId: this.playbackSessionId,
      spool: this.spool,
      fetchRef: this.fetchRef,
      windowRef: this.windowRef,
      documentRef: this.documentRef,
      navigatorRef: this.navigatorRef,
      bootstrapContext: {
        ...platform,
        device_class: this.context.device_class || "unknown",
        hls_engine: this.context.hls_engine || "unknown",
        client_timer_resolution_us: this.timerResolutionUs,
        capabilities,
      },
      onMetric: (eventName, payload) => this.record(eventName, {
        observationKind: "measured_client",
        payload,
      }),
    });
    this.record("client_recorder_started", {
      priority: "high",
      payload: { capabilities, client_queue_bytes: 0, state: "active" },
    });
    if (!spoolResult.persistent) {
      this.record("telemetry_gap", {
        priority: "critical",
        severity: "warning",
        observationKind: "unsupported",
        unavailableReason: spoolResult.unavailableReason || "indexeddb_unavailable",
        payload: { reason: "indexeddb_unavailable_memory_fallback", events_dropped: 0 },
      });
    }
    if (this.preSpoolDrops) {
      this.record("telemetry_gap", {
        priority: "critical",
        severity: "warning",
        payload: { reason: "pre_spool_capacity_reached", events_dropped: this.preSpoolDrops },
      });
    }
    const buffered = this.preSpool.splice(0);
    this.preSpoolBytes = 0;
    buffered.forEach((entry) => this.record(entry.eventName, entry.options));
    unsupportedWebCapabilities(capabilities).forEach((name) => {
      this.record("client_capability_unavailable", {
        observationKind: "unsupported",
        capabilityAvailable: false,
        unavailableReason: "unsupported_web",
        payload: { type: name, available: false, unavailable_reason: "unsupported_web" },
      });
    });
    this.mediaObserver = new MediaElementDiagnosticObserver({
      video: this.video,
      windowRef: this.windowRef,
      documentRef: this.documentRef,
      record: (name, options) => this.record(name, options),
      actionOrigin: () => this.consumeActionOrigin(),
    });
    this.lifecycleObserver = new PlaybackLifecycleDiagnosticObserver({
      windowRef: this.windowRef,
      documentRef: this.documentRef,
      navigatorRef: this.navigatorRef,
      record: (name, options) => this.record(name, options),
      recalibrateClock: () => this.transport?.synchronizeClock({ force: true }).catch(() => {}),
    });
    this.performanceObserver = new PlaybackPerformanceDiagnosticObserver({
      windowRef: this.windowRef,
      documentRef: this.documentRef,
      navigatorRef: this.navigatorRef,
      record: (name, options) => this.record(name, options),
    });
    this.mediaObserver.start();
    this.lifecycleObserver.start();
    this.performanceObserver.start();
    this.selfMonitorTimer = this.windowRef?.setInterval?.(
      () => this.recordSelfMetrics(),
      SELF_MONITOR_INTERVAL_MS,
    );
    this.transport.start().catch(() => {});
  }

  async stop() {
    if (this.stopping) return this.writeChain;
    this.stopping = true;
    this.running = false;
    this.mediaObserver?.stop();
    this.lifecycleObserver?.stop();
    this.performanceObserver?.stop();
    this.detachHls();
    if (this.selfMonitorTimer != null) this.windowRef?.clearInterval?.(this.selfMonitorTimer);
    this.selfMonitorTimer = null;
    await this.writeChain.catch(() => {});
    this.transport?.sendBeaconBestEffort();
    this.transport?.stop();
    this.spool?.close();
  }

  updateContext(nextContext = {}) {
    const previousAttachmentIdentity = [
      this.context.epoch_id,
      this.context.attachment_revision,
      this.context.stream_identity,
    ].join(":");
    this.context = { ...this.context, ...nextContext };
    const nextAttachmentIdentity = [
      this.context.epoch_id,
      this.context.attachment_revision,
      this.context.stream_identity,
    ].join(":");
    if (previousAttachmentIdentity && previousAttachmentIdentity !== nextAttachmentIdentity) {
      const previousAttachmentId = this.attachmentId;
      this.attachmentId = createDiagnosticId("attachment");
      this.record("attachment_changed", {
        priority: "high",
        payload: {
          previous: previousAttachmentId,
          current: this.attachmentId,
          reason: "observed_session_attachment_identity_change",
          revision: this.context.attachment_revision ?? null,
        },
      });
    }
  }

  markActionOrigin(origin) {
    this.lastOrigin = { value: String(origin || "unknown"), expiresAt: Date.now() + 1_000 };
  }

  consumeActionOrigin() {
    const candidate = this.lastOrigin;
    this.lastOrigin = null;
    return candidate && candidate.expiresAt >= Date.now() ? candidate.value : "unknown";
  }

  recordAction(eventName, origin = "inferred_user", payload = {}, capture = null) {
    this.markActionOrigin(origin);
    return this.record(eventName, {
      priority: "high",
      payload: { ...payload, action_origin: origin },
      capturedClock: capture?.capturedClock || null,
      playbackAttemptId: capture?.playbackAttemptId || null,
    });
  }

  bufferBeforeSpool(entry) {
    const normalized = entry?.eventName
      ? entry
      : { eventName: "telemetry_gap", options: { payload: { reason: "invalid_pre_spool_event" } } };
    const bytes = approximateBytes(normalized);
    if (
      this.preSpool.length >= PLAYBACK_DIAGNOSTICS_PRE_SPOOL_MAX_EVENTS
      || this.preSpoolBytes + bytes > PLAYBACK_DIAGNOSTICS_PRE_SPOOL_MAX_BYTES
    ) {
      this.preSpoolDrops += 1;
      return false;
    }
    this.preSpool.push(normalized);
    this.preSpoolBytes += bytes;
    return true;
  }

  record(eventName, options = {}) {
    if (!this.running) return Promise.resolve(false);
    if (!this.spool) {
      return Promise.resolve(this.bufferBeforeSpool({
        eventName,
        options: { ...options, capturedClock: options.capturedClock || captureClientClock() },
      }));
    }
    const scheduledAt = monotonicNow();
    const operation = async () => {
      const processingStartedAt = monotonicNow();
      this.lastRecorderQueueDelayMs = Math.max(0, processingStartedAt - scheduledAt);
      const offset = this.transport?.clock?.clock_offset_ns;
      const capturedClock = options.capturedClock || captureClientClock();
      const aligned = offset != null
        ? (BigInt(Math.round(capturedClock.client_wall_time_ms * 1_000_000)) + BigInt(offset)).toString()
        : null;
      const stored = await this.spool.createAndEnqueue(
        this.playbackSessionId,
        (sequence) => createPlaybackDiagnosticEvent({
          eventName,
          playbackSessionId: this.playbackSessionId,
          eventSequence: sequence,
          priority: options.priority || "normal",
          severity: options.severity || "info",
          observationKind: options.observationKind || "measured_client",
          payload: options.payload || {},
          capturedClock,
          clock: { ...this.transport?.clock, aligned_wall_time_ns: aligned },
          context: {
            ...this.context,
            playback_attempt_id: options.playbackAttemptId || this.playbackAttemptId,
            attachment_id: this.attachmentId,
            incident_id: options.incidentId || null,
            decision_id: options.decisionId || null,
            playhead_ms: options.playheadMs ?? null,
            media_element_time_ms: options.mediaElementTimeMs ?? null,
            duration_ms: options.durationMs ?? null,
            sample_window_ms: options.sampleWindowMs ?? null,
            capability_available: options.capabilityAvailable ?? null,
            unavailable_reason: options.unavailableReason ?? null,
            measurement_method: options.measurementMethod ?? null,
            measurement_uncertainty: options.measurementUncertainty ?? null,
            client_timer_resolution_us: this.timerResolutionUs,
          },
        }),
        { priority: options.priority || "normal" },
      );
      this.lastRecorderOverheadMs = Math.max(0, monotonicNow() - processingStartedAt);
      if (stored.stored) {
        this.lastSequence = stored.sequence;
        this.transport?.notePersistedEvent(stored.event);
        this.transport?.flushSoon();
        if (["completed", "quit", "playback_failed"].includes(eventName)) {
          await this.transport?.closeSession(eventName, stored.sequence);
        }
        return true;
      }
      if (!this.gapBeingRecorded && eventName !== "telemetry_gap") {
        this.gapBeingRecorded = true;
        try {
          await this.spool.createAndEnqueue(
            this.playbackSessionId,
            (sequence) => createPlaybackDiagnosticEvent({
              eventName: "telemetry_gap",
              playbackSessionId: this.playbackSessionId,
              eventSequence: sequence,
              priority: "critical",
              severity: "warning",
              payload: {
                reason: stored.reason,
                events_dropped: 1,
                client_queue_bytes: stored.usageBytes ?? null,
              },
              context: {
                ...this.context,
                playback_attempt_id: this.playbackAttemptId,
                attachment_id: this.attachmentId,
              },
            }),
            { priority: "critical" },
          );
        } finally {
          this.gapBeingRecorded = false;
        }
      }
      return false;
    };
    this.writeChain = this.writeChain.then(operation, operation).catch(() => false);
    return this.writeChain;
  }

  attachHls(hls) {
    if (this.attachedHls === hls) return;
    this.detachHls();
    if (!hls) return;
    this.attachedHls = hls;
    this.hlsObserver = new HlsJsDiagnosticObserver({
      hls,
      events: this.hlsEvents,
      record: (name, options) => this.record(name, options),
    });
    this.hlsObserver.start();
  }

  detachHls() {
    this.hlsObserver?.stop();
    this.hlsObserver = null;
    this.attachedHls = null;
  }

  async recordSelfMetrics() {
    try {
      const stats = await this.spool?.stats(this.playbackSessionId);
      if (!stats || !this.running) return;
      this.record("recorder_aggregate", {
        payload: {
          queue_depth: stats.queueDepth,
          client_queue_bytes: stats.queueBytes,
          global_queue_bytes: stats.globalQueueBytes,
          oldest_event_age_ms: stats.oldestEventAgeMs,
          retries: this.transport?.retries || 0,
          in_flight: Boolean(this.transport?.flushInFlight),
          recorder_overhead_ms: this.lastRecorderOverheadMs,
          recorder_queue_delay_ms: this.lastRecorderQueueDelayMs,
        },
        sampleWindowMs: SELF_MONITOR_INTERVAL_MS,
      });
    } catch {
      // The queue may be unavailable in strict private browsing modes.
    }
  }
}
