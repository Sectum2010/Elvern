import { collectPlaybackDiagnosticCapabilities, unsupportedWebCapabilities } from "./capabilities";
import { estimateTimerResolution } from "./clock";
import { createDiagnosticSpool } from "./indexedDbSpool";
import { HlsJsDiagnosticObserver } from "./hlsObserver";
import { PlaybackLifecycleDiagnosticObserver } from "./lifecycleObserver";
import { MediaElementDiagnosticObserver } from "./mediaObserver";
import { PlaybackPerformanceDiagnosticObserver } from "./performanceObserver";
import { classifyBrowserPlatform } from "./privacy";
import { createDiagnosticId, createPlaybackDiagnosticEvent } from "./schema";
import { PlaybackDiagnosticsTransport } from "./transport";

const SELF_MONITOR_INTERVAL_MS = 10_000;

function monotonicNow() {
  return globalThis.performance?.now?.() ?? Date.now();
}

export class PlaybackDiagnosticRecorder {
  constructor({
    playbackSessionId,
    video,
    context = {},
    hlsEvents = {},
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
    this.writeChain = Promise.resolve();
    this.selfMonitorTimer = null;
    this.lastOrigin = null;
    this.lastSequence = 0;
    this.gapBeingRecorded = false;
    this.timerResolutionUs = null;
    this.lastRecorderOverheadMs = 0;
    this.playbackAttemptId = createDiagnosticId("attempt");
    this.attachmentId = createDiagnosticId("attachment");
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
    capabilities.client_fragment_loader_detail = this.context.hls_engine === "hls.js";
    this.timerResolutionUs = estimateTimerResolution({
      now: () => this.windowRef?.performance?.now?.() || performance.now(),
    });
    const spoolResult = await createDiagnosticSpool({
      indexedDBRef: this.windowRef?.indexedDB || globalThis.indexedDB,
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
        capabilities,
      },
      onMetric: (eventName, payload) => this.record(eventName, {
        observationKind: "measured_client",
        payload,
      }),
    });
    this.record("client_recorder_started", {
      priority: "high",
      payload: {
        capabilities,
        client_queue_bytes: 0,
        state: "active",
      },
    });
    if (!spoolResult.persistent) {
      this.record("telemetry_gap", {
        priority: "critical",
        severity: "warning",
        observationKind: "unsupported",
        unavailableReason: spoolResult.unavailableReason || "indexeddb_unavailable",
        payload: {
          reason: "indexeddb_unavailable_memory_fallback",
          events_dropped: 0,
        },
      });
    }
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
      record: (name, options) => this.record(name, options),
      actionOrigin: () => this.consumeActionOrigin(),
    });
    this.lifecycleObserver = new PlaybackLifecycleDiagnosticObserver({
      windowRef: this.windowRef,
      documentRef: this.documentRef,
      navigatorRef: this.navigatorRef,
      record: (name, options) => this.record(name, options),
      recalibrateClock: () => this.transport?.synchronizeClock().catch(() => {}),
    });
    this.performanceObserver = new PlaybackPerformanceDiagnosticObserver({
      windowRef: this.windowRef,
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

  stop() {
    this.running = false;
    this.mediaObserver?.stop();
    this.lifecycleObserver?.stop();
    this.performanceObserver?.stop();
    this.detachHls();
    if (this.selfMonitorTimer != null) this.windowRef?.clearInterval?.(this.selfMonitorTimer);
    this.selfMonitorTimer = null;
    this.transport?.sendBeaconBestEffort();
    this.transport?.stop();
    this.writeChain.finally(() => this.spool?.close()).catch(() => {});
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
      this.playbackAttemptId = createDiagnosticId("attempt");
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
    this.lastOrigin = {
      value: String(origin || "unknown"),
      expiresAt: Date.now() + 1_000,
    };
  }

  consumeActionOrigin() {
    const candidate = this.lastOrigin;
    this.lastOrigin = null;
    return candidate && candidate.expiresAt >= Date.now() ? candidate.value : "unknown";
  }

  recordAction(eventName, origin = "inferred_user", payload = {}) {
    this.markActionOrigin(origin);
    this.record(eventName, {
      priority: "high",
      payload: { ...payload, action_origin: origin },
    });
  }

  record(eventName, {
    priority = "normal",
    severity = "info",
    observationKind = "measured_client",
    payload = {},
    incidentId = null,
    decisionId = null,
    playheadMs = null,
    mediaElementTimeMs = null,
    durationMs = null,
    sampleWindowMs = null,
    capabilityAvailable = null,
    unavailableReason = null,
  } = {}) {
    if (!this.running || !this.spool) return;
    const scheduledAt = monotonicNow();
    this.writeChain = this.writeChain.then(async () => {
      const sequence = await this.spool.reserveSequence(this.playbackSessionId);
      this.lastSequence = sequence;
      const offset = this.transport?.clock?.clock_offset_ns;
      const aligned = offset != null
        ? (BigInt(Math.round(Date.now() * 1_000_000)) + BigInt(offset)).toString()
        : null;
      const event = createPlaybackDiagnosticEvent({
        eventName,
        playbackSessionId: this.playbackSessionId,
        eventSequence: sequence,
        priority,
        severity,
        observationKind,
        payload,
        clock: {
          ...this.transport?.clock,
          aligned_wall_time_ns: aligned,
        },
        context: {
          ...this.context,
          playback_attempt_id: this.playbackAttemptId,
          attachment_id: this.attachmentId,
          incident_id: incidentId,
          decision_id: decisionId,
          playhead_ms: playheadMs,
          media_element_time_ms: mediaElementTimeMs,
          duration_ms: durationMs,
          sample_window_ms: sampleWindowMs,
          capability_available: capabilityAvailable,
          unavailable_reason: unavailableReason,
          client_timer_resolution_us: this.timerResolutionUs,
        },
      });
      const stored = await this.spool.enqueue(this.playbackSessionId, event);
      if (stored.stored) {
        this.transport?.notePersistedEvent(event);
        if (["completed", "quit", "playback_failed"].includes(eventName)) {
          this.transport?.closeSession(eventName, sequence).catch(() => {});
        } else {
          this.transport?.flushSoon();
        }
        return;
      }
      if (!this.gapBeingRecorded && eventName !== "telemetry_gap") {
        this.gapBeingRecorded = true;
        this.record("telemetry_gap", {
          priority: "critical",
          severity: "warning",
          payload: {
            reason: stored.reason,
            events_dropped: 1,
            client_queue_bytes: stored.usageBytes ?? stored.queueBytes ?? null,
          },
        });
        this.gapBeingRecorded = false;
      }
    }).catch(() => {
      // Recorder write failures are intentionally isolated from playback.
    });
    this.lastRecorderOverheadMs = Math.max(0, monotonicNow() - scheduledAt);
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
          oldest_event_age_ms: stats.oldestEventAgeMs,
          retries: this.transport?.retries || 0,
          in_flight: Boolean(this.transport?.flushInFlight),
          recorder_overhead_ms: this.lastRecorderOverheadMs,
        },
        sampleWindowMs: SELF_MONITOR_INTERVAL_MS,
      });
    } catch {
      // The queue may be unavailable in strict private browsing modes.
    }
  }
}
