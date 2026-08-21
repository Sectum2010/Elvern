import {
  PLAYBACK_DIAGNOSTICS_WORKER_QUEUE_CRITICAL_MESSAGES,
  PLAYBACK_DIAGNOSTICS_WORKER_QUEUE_MAX_MESSAGES,
} from "./constants";
import { PlaybackDiagnosticsDataPlane } from "./dataPlane";
import { PlaybackDiagnosticsOverheadMonitor } from "./overheadMonitor";
import { createDiagnosticId } from "./schema";

const detachedClients = new Set();

function defaultWorkerFactory() {
  return new Worker(new URL("./worker.js", import.meta.url), {
    type: "module",
    name: "elvern-playback-diagnostics",
  });
}

function monotonicNow() {
  return globalThis.performance?.now?.() ?? Date.now();
}

export class PlaybackDiagnosticsWorkerClient {
  constructor({
    options,
    workerFactory = defaultWorkerFactory,
    dataPlaneFactory = (dataPlaneOptions) => new PlaybackDiagnosticsDataPlane(dataPlaneOptions),
    onHealth = () => {},
    onModeChange = () => {},
  }) {
    this.options = { ...options };
    this.workerFactory = workerFactory;
    this.dataPlaneFactory = dataPlaneFactory;
    this.onHealth = onHealth;
    this.onModeChange = onModeChange;
    this.clientId = createDiagnosticId("worker_client");
    this.worker = null;
    this.fallback = null;
    this.pending = new Map();
    this.nextMessageId = 0;
    this.started = false;
    this.closed = false;
    this.closeReason = null;
    this.currentPlaybackAttemptId = options.playbackAttemptId || null;
    this.readyPromise = null;
    this.resolveReady = null;
    this.droppedCaptureCount = 0;
    this.overheadMonitor = new PlaybackDiagnosticsOverheadMonitor({
      onModeChange: (mode, reason) => {
        this.onModeChange(mode, reason);
        try {
          this.worker?.postMessage?.({
            type: "set_overhead_mode",
            clientId: this.clientId,
            mode,
            reason,
          });
        } catch {
          // The regular Worker error path owns fallback activation.
        }
      },
    });
    this.handleMessage = (event) => this.onWorkerMessage(event.data || {});
    this.handleError = () => this.activateFallback("worker_crashed");
  }

  async start() {
    if (this.started) return this.readyPromise;
    this.started = true;
    this.readyPromise = new Promise((resolve) => { this.resolveReady = resolve; });
    try {
      this.worker = this.workerFactory();
      this.worker.addEventListener("message", this.handleMessage);
      this.worker.addEventListener("error", this.handleError);
      this.worker.addEventListener("messageerror", this.handleError);
      const {
        fetchRef: _fetchRef,
        indexedDBRef: _indexedDBRef,
        keyRangeRef: _keyRangeRef,
        runtimeRef: _runtimeRef,
        ...workerOptions
      } = this.options;
      this.post("start", { options: workerOptions }, { critical: true, tracked: false });
    } catch {
      await this.activateFallback("worker_unavailable");
    }
    return this.readyPromise;
  }

  onWorkerMessage(message) {
    if (message.clientId && message.clientId !== this.clientId) return;
    if (message.type === "ack") {
      const pending = this.pending.get(message.messageId);
      this.pending.delete(message.messageId);
      if (pending?.sentAtMs != null) {
        this.overheadMonitor.observeLatency(
          "worker_message_delay_ms",
          monotonicNow() - pending.sentAtMs,
          { p95LimitMs: 250, hardLimitMs: 2_000, minimumSamples: 8 },
        );
      }
      this.flushDroppedCaptures();
    }
    if (message.type === "ready") {
      this.resolveReady?.({ worker: true, persistent: message.persistent });
      this.resolveReady = null;
    }
    if (message.type === "health") this.handleHealth(message.health);
    if (message.type === "failure") {
      this.overheadMonitor.recordError("worker_operation_failed");
      this.onHealth({
        component: "worker",
        reason: "operation_failed",
        details: { error_class: message.errorClass || "Error" },
      });
      if (this.resolveReady) void this.activateFallback("worker_start_failed");
    }
    if (message.type === "close_result" && message.sealed) this.dispose();
  }

  handleHealth(health = {}) {
    if (
      health.component === "overhead"
      && health.reason === "mode_changed"
      && health.details?.mode
    ) {
      this.overheadMonitor.adoptMode(
        health.details.mode,
        health.details.trigger || "worker_pressure",
      );
    }
    this.onHealth(health);
  }

  post(type, payload = {}, { critical = false, tracked = true } = {}) {
    if (this.closed || !this.worker) return false;
    const capacity = PLAYBACK_DIAGNOSTICS_WORKER_QUEUE_MAX_MESSAGES
      + (critical ? PLAYBACK_DIAGNOSTICS_WORKER_QUEUE_CRITICAL_MESSAGES : 0);
    if (tracked && this.pending.size >= capacity) {
      this.onHealth({ component: "worker", reason: "queue_full", details: { critical } });
      return false;
    }
    const messageId = tracked ? ++this.nextMessageId : null;
    const message = { type, clientId: this.clientId, messageId, ...payload };
    if (tracked) {
      this.pending.set(messageId, { type, critical, sentAtMs: monotonicNow() });
      this.overheadMonitor.observeRatio(
        "worker_queue",
        this.pending.size / Math.max(1, PLAYBACK_DIAGNOSTICS_WORKER_QUEUE_MAX_MESSAGES),
        { reducedAt: 0.75, criticalAt: 1 },
      );
    }
    try {
      this.worker.postMessage(message);
      return true;
    } catch {
      if (tracked) this.pending.delete(messageId);
      void this.activateFallback("worker_post_failed");
      return false;
    }
  }

  async activateFallback(reason) {
    if (this.fallback || this.closed) return;
    const dropped = [...this.pending.values()].filter((entry) => entry.type === "capture").length
      + this.droppedCaptureCount;
    this.droppedCaptureCount = 0;
    this.pending.clear();
    this.worker?.removeEventListener?.("message", this.handleMessage);
    this.worker?.removeEventListener?.("error", this.handleError);
    this.worker?.removeEventListener?.("messageerror", this.handleError);
    this.worker?.terminate?.();
    this.worker = null;
    this.fallback = this.dataPlaneFactory({
      ...this.options,
      indexedDBRef: globalThis.indexedDB,
      keyRangeRef: globalThis.IDBKeyRange,
      runtimeRef: globalThis,
      onHealth: (health) => this.handleHealth(health),
      onSealed: () => this.dispose(),
    });
    await this.fallback.start();
    this.fallback.setOverheadMode?.(this.overheadMonitor.mode, reason);
    if (this.currentPlaybackAttemptId) {
      this.fallback.setPlaybackAttempt(this.currentPlaybackAttemptId);
    }
    if (dropped) await this.fallback.declareDropped(dropped, "client_overhead_circuit");
    this.onHealth({ component: "worker", reason, details: { dropped_messages: dropped } });
    this.resolveReady?.({ worker: false, persistent: this.fallback.persistent });
    this.resolveReady = null;
    if (this.closeReason) {
      const sealed = await this.fallback.close(this.closeReason);
      if (sealed) this.dispose();
    }
  }

  capture(eventName, options = {}) {
    if (this.closed) return false;
    const startedAt = monotonicNow();
    const critical = options.priority === "critical"
      || ["completed", "quit", "playback_failed", "telemetry_gap"].includes(eventName);
    if (!this.overheadMonitor.allows(eventName, { critical })) {
      this.noteDroppedCapture();
      return false;
    }
    if (this.fallback) {
      void this.fallback.capture(eventName, options, { queuedAtMs: monotonicNow() });
      this.overheadMonitor.observeLatency(
        "main_capture_latency_ms",
        monotonicNow() - startedAt,
        { p95LimitMs: 0.25, hardLimitMs: 2 },
      );
      return true;
    }
    const accepted = this.post("capture", {
      eventName,
      options,
      queuedAtMs: monotonicNow(),
    }, { critical });
    this.overheadMonitor.observeLatency(
      "main_capture_latency_ms",
      monotonicNow() - startedAt,
      { p95LimitMs: 0.25, hardLimitMs: 2 },
    );
    if (!accepted) {
      this.noteDroppedCapture();
      if (critical) void this.activateFallback("critical_queue_saturated");
    }
    return accepted;
  }

  noteDroppedCapture() {
    this.droppedCaptureCount = Math.min(256, this.droppedCaptureCount + 1);
    this.flushDroppedCaptures();
  }

  flushDroppedCaptures() {
    if (!this.worker || !this.droppedCaptureCount || this.closed) return;
    const count = this.droppedCaptureCount;
    const accepted = this.post("declare_drop", {
      count,
      reasonCode: "client_overhead_circuit",
    }, { critical: true });
    if (accepted) this.droppedCaptureCount = 0;
  }

  updateContext(context) {
    this.options.context = { ...(this.options.context || {}), ...(context || {}) };
    if (this.fallback) this.fallback.updateContext(context);
    else this.post("update_context", { context }, { tracked: false });
  }

  setPlaybackAttempt(playbackAttemptId) {
    this.currentPlaybackAttemptId = playbackAttemptId;
    if (this.fallback) this.fallback.setPlaybackAttempt(playbackAttemptId);
    else this.post("set_attempt", { playbackAttemptId }, { tracked: false });
  }

  wake(options = {}) {
    if (this.fallback) this.fallback.wake(options);
    else this.post("wake", { options }, { tracked: false });
  }

  close(reason = "component_unmounted") {
    if (this.closed) return;
    this.closeReason = reason;
    detachedClients.add(this);
    if (this.fallback) {
      void this.fallback.close(reason).then((sealed) => {
        if (sealed) this.dispose();
      });
      return;
    }
    if (!this.post("close", { reason }, { critical: true })) {
      void this.activateFallback("close_queue_saturated");
    }
  }

  dispose() {
    if (this.closed) return;
    this.closed = true;
    detachedClients.delete(this);
    this.worker?.removeEventListener?.("message", this.handleMessage);
    this.worker?.removeEventListener?.("error", this.handleError);
    this.worker?.removeEventListener?.("messageerror", this.handleError);
    this.worker?.terminate?.();
    this.worker = null;
    this.fallback?.stop();
    this.fallback = null;
    this.pending.clear();
    this.droppedCaptureCount = 0;
  }
}

let recoveryWorker = null;

export function wakePlaybackDiagnosticsRecovery(ownerUserId, { authenticationRestored = false } = {}) {
  if (typeof Worker !== "function" || ownerUserId == null) return false;
  if (recoveryWorker) {
    recoveryWorker.postMessage({ type: "recover_all", ownerUserId, authenticationRestored });
    return true;
  }
  try {
    recoveryWorker = defaultWorkerFactory();
    recoveryWorker.addEventListener("message", (event) => {
      if (!["recovery_complete", "recovery_idle"].includes(event.data?.type)) return;
      if (event.data?.type === "recovery_complete" && Number(event.data?.pending || 0) > 0) return;
      recoveryWorker?.terminate?.();
      recoveryWorker = null;
    });
    recoveryWorker.addEventListener("error", () => {
      recoveryWorker?.terminate?.();
      recoveryWorker = null;
    });
    recoveryWorker.postMessage({ type: "recover_all", ownerUserId, authenticationRestored });
    return true;
  } catch {
    recoveryWorker = null;
    return false;
  }
}
