import { collectPlaybackDiagnosticCapabilities, unsupportedWebCapabilities } from "./capabilities";
import { estimateTimerResolution } from "./clock";
import {
  PLAYBACK_DIAGNOSTICS_PRE_SPOOL_MAX_BYTES,
  PLAYBACK_DIAGNOSTICS_PRE_SPOOL_MAX_EVENTS,
} from "./constants";
import { HlsJsDiagnosticObserver } from "./hlsObserver";
import { PlaybackLifecycleDiagnosticObserver } from "./lifecycleObserver";
import { MediaElementDiagnosticObserver } from "./mediaObserver";
import { overheadModeRank } from "./overheadMonitor";
import { PlaybackPerformanceDiagnosticObserver } from "./performanceObserver";
import { classifyBrowserPlatform } from "./privacy";
import { captureClientClock } from "./schema";
import { PlaybackDiagnosticsWorkerClient } from "./workerClient";

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
    workerFactory = undefined,
  }) {
    this.playbackSessionId = playbackSessionId;
    this.video = video;
    this.context = { ...context };
    this.hlsEvents = hlsEvents;
    this.windowRef = windowRef;
    this.documentRef = documentRef;
    this.navigatorRef = navigatorRef;
    this.fetchRef = fetchRef;
    this.workerFactory = workerFactory;
    this.dataClient = null;
    this.mediaObserver = null;
    this.lifecycleObserver = null;
    this.performanceObserver = null;
    this.hlsObserver = null;
    this.attachedHls = null;
    this.running = false;
    this.stopping = false;
    this.lastOrigin = null;
    this.timerResolutionUs = null;
    this.playbackAttemptId = playbackAttemptId || null;
    this.preSpool = [];
    this.preSpoolBytes = 0;
    this.preSpoolDrops = 0;
    this.overheadMode = "normal";
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
    this.dataClient = new PlaybackDiagnosticsWorkerClient({
      workerFactory: this.workerFactory,
      options: {
        playbackSessionId: this.playbackSessionId,
        context: this.context,
        playbackAttemptId: this.playbackAttemptId,
        capabilities,
        fetchRef: this.fetchRef,
        bootstrapContext: {
          ...platform,
          device_class: this.context.device_class || "unknown",
          hls_engine: this.context.hls_engine || "unknown",
          client_timer_resolution_us: this.timerResolutionUs,
          capabilities,
        },
      },
      onHealth: () => {},
      onModeChange: (mode) => this.applyOverheadMode(mode),
    });
    const dataPlane = await this.dataClient.start();
    if (!this.running) {
      this.dataClient.close("component_unmounted_during_start");
      return;
    }
    this.record("client_recorder_started", {
      priority: "high",
      payload: { capabilities, client_queue_bytes: 0, state: "active" },
    });
    if (!dataPlane?.persistent) {
      this.record("telemetry_gap", {
        priority: "critical",
        severity: "warning",
        observationKind: "unsupported",
        unavailableReason: "indexeddb_or_worker_unavailable",
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
    this.mediaObserver.setDiagnosticsMode(this.overheadMode);
    this.lifecycleObserver = new PlaybackLifecycleDiagnosticObserver({
      windowRef: this.windowRef,
      documentRef: this.documentRef,
      navigatorRef: this.navigatorRef,
      record: (name, options) => this.record(name, options),
      close: (reason) => this.dataClient?.close(reason),
      recalibrateClock: () => this.dataClient?.wake(),
    });
    if (
      dataPlane?.worker
      && overheadModeRank(this.overheadMode) < overheadModeRank("optional_disabled")
    ) {
      this.performanceObserver = new PlaybackPerformanceDiagnosticObserver({
        windowRef: this.windowRef,
        documentRef: this.documentRef,
        navigatorRef: this.navigatorRef,
        record: (name, options) => this.record(name, options),
      });
    }
    this.mediaObserver.start();
    this.lifecycleObserver.start();
    this.performanceObserver?.start();
  }

  applyOverheadMode(mode) {
    if (overheadModeRank(mode) <= overheadModeRank(this.overheadMode)) return;
    this.overheadMode = mode;
    this.mediaObserver?.setDiagnosticsMode(mode);
    if (overheadModeRank(mode) >= overheadModeRank("optional_disabled")) {
      this.performanceObserver?.stop();
      this.performanceObserver = null;
    }
  }

  stop(reason = "component_unmounted") {
    if (this.stopping) return;
    this.stopping = true;
    this.mediaObserver?.stop();
    this.lifecycleObserver?.stop();
    this.performanceObserver?.stop();
    this.detachHls();
    this.running = false;
    this.dataClient?.close(reason);
  }

  updateContext(nextContext = {}) {
    this.context = { ...this.context, ...nextContext };
    this.dataClient?.updateContext(this.context);
  }

  setPlaybackAttempt(playbackAttemptId) {
    this.playbackAttemptId = playbackAttemptId || this.playbackAttemptId;
    this.dataClient?.setPlaybackAttempt(this.playbackAttemptId);
  }

  replaceVideo(video) {
    if (!video || video === this.video) return;
    this.mediaObserver?.stop();
    this.mediaObserver = null;
    this.detachHls();
    this.video = video;
    if (!this.running) return;
    this.mediaObserver = new MediaElementDiagnosticObserver({
      video: this.video,
      windowRef: this.windowRef,
      documentRef: this.documentRef,
      record: (name, options) => this.record(name, options),
      actionOrigin: () => this.consumeActionOrigin(),
    });
    this.mediaObserver.setDiagnosticsMode(this.overheadMode);
    this.mediaObserver.start();
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
    if (!this.dataClient) {
      return Promise.resolve(this.bufferBeforeSpool({
        eventName,
        options: { ...options, capturedClock: options.capturedClock || captureClientClock() },
      }));
    }
    return Promise.resolve(this.dataClient.capture(eventName, {
      ...options,
      capturedClock: options.capturedClock || captureClientClock(),
      playbackAttemptId: options.playbackAttemptId || this.playbackAttemptId,
    }));
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

}
