import {
  PLAYBACK_DIAGNOSTICS_ACTIVE_LEASE_DURATION_MS,
  PLAYBACK_DIAGNOSTICS_ACTIVE_LEASE_HEARTBEAT_MS,
  PLAYBACK_DIAGNOSTICS_CRITICAL_EVENTS,
  PLAYBACK_DIAGNOSTICS_DEGRADED_SPOOL_MAX_BYTES,
  PLAYBACK_DIAGNOSTICS_WORKER_QUEUE_CRITICAL_MESSAGES,
  PLAYBACK_DIAGNOSTICS_WORKER_QUEUE_MAX_MESSAGES,
} from "./constants";
import { createDiagnosticSpool } from "./indexedDbSpool";
import { PlaybackDiagnosticsOverheadMonitor } from "./overheadMonitor";
import {
  captureClientClock,
  createDiagnosticId,
  createPlaybackDiagnosticEvent,
} from "./schema";
import { PlaybackDiagnosticClientStateMachine } from "./stateMachine";
import { PlaybackDiagnosticsTransport } from "./transport";

const TERMINAL_EVENT_NAMES = new Set(["completed", "quit", "playback_failed"]);
const DEGRADED_SAMPLE_DIVISOR = 4;

function monotonicNow() {
  return globalThis.performance?.now?.() ?? Date.now();
}

function isCriticalObservation(eventName, options = {}) {
  return options.priority === "critical"
    || PLAYBACK_DIAGNOSTICS_CRITICAL_EVENTS.has(String(eventName || ""))
    || TERMINAL_EVENT_NAMES.has(String(eventName || ""));
}

export class PlaybackDiagnosticsDataPlane {
  constructor({
    playbackSessionId,
    context = {},
    capabilities = {},
    playbackAttemptId = null,
    attachmentId = null,
    bootstrapContext = {},
    indexedDBRef = globalThis.indexedDB,
    keyRangeRef = globalThis.IDBKeyRange,
    fetchRef = globalThis.fetch?.bind(globalThis),
    runtimeRef = globalThis,
    onHealth = () => {},
    onSealed = () => {},
  }) {
    this.playbackSessionId = String(playbackSessionId || "");
    const { owner_user_id: ownerUserId = null, ...eventContext } = context;
    this.ownerUserId = ownerUserId;
    this.context = { ...eventContext };
    this.capabilities = { ...capabilities };
    this.playbackAttemptId = playbackAttemptId || createDiagnosticId("attempt");
    this.attachmentId = attachmentId || createDiagnosticId("attachment");
    this.bootstrapContext = { ...bootstrapContext };
    this.indexedDBRef = indexedDBRef;
    this.keyRangeRef = keyRangeRef;
    this.fetchRef = fetchRef;
    this.runtimeRef = runtimeRef;
    this.onHealth = onHealth;
    this.onSealed = onSealed;
    this.spool = null;
    this.transport = null;
    this.stateMachine = new PlaybackDiagnosticClientStateMachine();
    this.writeChain = Promise.resolve();
    this.pendingOperations = 0;
    this.started = false;
    this.persistent = false;
    this.sampleCounter = 0;
    this.lastSequence = 0;
    this.terminalClosePromise = null;
    this.pendingDropCount = 0;
    this.dropDeclarationQueued = false;
    this.activeLeaseId = createDiagnosticId("worker_lease");
    this.activeLeaseTimer = null;
    this.overhead = {
      mode: "normal",
      processing_ms: 0,
      queue_delay_ms: 0,
      dropped: 0,
    };
    this.overheadMonitor = new PlaybackDiagnosticsOverheadMonitor({
      onModeChange: (mode, reason) => {
        this.overhead.mode = mode;
        this.onHealth({
          component: "overhead",
          reason: "mode_changed",
          details: { mode, trigger: reason },
        });
      },
    });
  }

  async start() {
    if (this.started || !this.playbackSessionId) return this.started;
    const opened = await createDiagnosticSpool({
      indexedDBRef: this.indexedDBRef,
      keyRangeRef: this.keyRangeRef,
      degradedMaxBytes: PLAYBACK_DIAGNOSTICS_DEGRADED_SPOOL_MAX_BYTES,
    });
    this.spool = opened.spool;
    this.persistent = opened.persistent;
    if (!opened.persistent) {
      this.overheadMonitor.adoptMode("optional_disabled", "degraded_storage");
    }
    this.transport = new PlaybackDiagnosticsTransport({
      playbackSessionId: this.playbackSessionId,
      spool: this.spool,
      fetchRef: this.fetchRef,
      windowRef: this.runtimeRef,
      documentRef: null,
      navigatorRef: this.runtimeRef?.navigator || null,
      stateMachine: this.stateMachine,
      bootstrapContext: {
        ...this.bootstrapContext,
        capabilities: this.capabilities,
      },
      onHealth: (component, reason, details) => {
        if (reason === "batch_acked") {
          this.overheadMonitor.observeLatency("backend_upload_latency_ms", details?.upload_latency_ms, {
            p95LimitMs: 1_000,
            hardLimitMs: 10_000,
            minimumSamples: 8,
          });
        } else if (!["bootstrap_succeeded", "clock_synchronized", "gap_durable"].includes(reason)) {
          this.overheadMonitor.recordError(`${component}_${reason}`);
        }
        this.onHealth({ component, reason, details });
      },
      onSealed: this.onSealed,
    });
    const ownerScopeHash = await this.spool.getOwnerScopeHash?.(this.ownerUserId);
    await this.spool.updateRecoveryState?.(this.playbackSessionId, {
      owner_user_id: null,
      owner_scope_hash: ownerScopeHash || null,
      bootstrap_context: this.bootstrapContext,
      close_state: "open",
      active_lease_id: this.activeLeaseId,
      active_lease_expires_at_ms: Date.now() + PLAYBACK_DIAGNOSTICS_ACTIVE_LEASE_DURATION_MS,
    });
    this.startActiveLeaseHeartbeat();
    this.started = true;
    void this.transport.start().catch((error) => {
      this.onHealth({
        component: "transport",
        reason: "startup_failed",
        details: { error_class: error?.name || "Error" },
      });
    });
    return true;
  }

  updateContext(nextContext = {}) {
    const { owner_user_id: ownerUserId, ...safeContext } = nextContext;
    if (ownerUserId != null) this.ownerUserId = ownerUserId;
    const previousIdentity = [
      this.context.epoch_id,
      this.context.attachment_revision,
      this.context.stream_identity,
    ].join(":");
    this.context = { ...this.context, ...safeContext };
    const nextIdentity = [
      this.context.epoch_id,
      this.context.attachment_revision,
      this.context.stream_identity,
    ].join(":");
    if (previousIdentity && previousIdentity !== nextIdentity) {
      const previousAttachmentId = this.attachmentId;
      this.attachmentId = createDiagnosticId("attachment");
      void this.capture("attachment_changed", {
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

  setPlaybackAttempt(playbackAttemptId) {
    const normalized = String(playbackAttemptId || "").trim();
    if (normalized) this.playbackAttemptId = normalized;
  }

  shouldSample(eventName, options) {
    const critical = isCriticalObservation(eventName, options);
    if (!this.overheadMonitor.allows(eventName, { critical })) return false;
    if (critical || this.overhead.mode === "normal") return true;
    this.sampleCounter += 1;
    return this.sampleCounter % DEGRADED_SAMPLE_DIVISOR === 0;
  }

  capture(eventName, options = {}, { queuedAtMs = null } = {}) {
    const critical = isCriticalObservation(eventName, options);
    const terminal = TERMINAL_EVENT_NAMES.has(String(eventName || ""));
    if (!this.started || !this.stateMachine.canCapture({ critical })) return Promise.resolve(false);
    const operationCapacity = PLAYBACK_DIAGNOSTICS_WORKER_QUEUE_MAX_MESSAGES
      + (critical ? PLAYBACK_DIAGNOSTICS_WORKER_QUEUE_CRITICAL_MESSAGES : 0);
    if (this.pendingOperations >= operationCapacity) {
      this.overhead.dropped += 1;
      this.onHealth({
        component: "data_plane",
        reason: "operation_queue_full",
        details: { critical, pending_operations: this.pendingOperations },
      });
      if (terminal) return this.closeAfterQueueSaturation(eventName, options);
      return Promise.resolve(false);
    }
    if (!this.shouldSample(eventName, options)) {
      this.overhead.dropped += 1;
      this.queueDeclaredDrop("client_overhead_circuit");
      return Promise.resolve(false);
    }
    if (terminal && !this.stateMachine.closing) {
      try {
        this.stateMachine.transition("closing");
      } catch (error) {
        this.onHealth({
          component: "data_plane",
          reason: "terminal_transition_rejected",
          details: { error_class: error?.name || "Error" },
        });
        return Promise.resolve(false);
      }
    }
    const scheduledAt = Number.isFinite(Number(queuedAtMs)) ? Number(queuedAtMs) : monotonicNow();
    const operation = async () => {
      const processingStartedAt = monotonicNow();
      this.overhead.queue_delay_ms = Math.max(0, processingStartedAt - scheduledAt);
      const capturedClock = options.capturedClock || captureClientClock();
      const offset = this.transport?.clock?.clock_offset_ns;
      const aligned = offset != null
        ? (BigInt(Math.round(capturedClock.client_wall_time_ms * 1_000_000)) + BigInt(offset)).toString()
        : null;
      const storageStartedAt = monotonicNow();
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
            client_timer_resolution_us: this.bootstrapContext.client_timer_resolution_us ?? null,
          },
        }),
        { priority: options.priority || "normal" },
      );
      const storageLatencyMs = Math.max(0, monotonicNow() - storageStartedAt);
      this.overhead.processing_ms = Math.max(0, monotonicNow() - processingStartedAt);
      this.overheadMonitor.observeLatency("indexeddb_latency_ms", storageLatencyMs, {
        p95LimitMs: 25,
        hardLimitMs: 250,
      });
      if (stored.maxBytes) {
        this.overheadMonitor.observeRatio(
          "client_spool",
          Number(stored.globalUsageBytes || 0) / Number(stored.maxBytes),
        );
      }
      if (!stored.stored) {
        this.overhead.dropped += 1;
        const gap = await this.spool.queueGap?.(this.playbackSessionId, {
          reason_code: stored.reason?.includes("capacity")
            ? "client_capacity_drop"
            : "client_storage_failure",
        });
        if (gap) this.lastSequence = gap.end_sequence;
        if (terminal) {
          this.terminalClosePromise = this.transport.closeSession(
            options.terminalReason || eventName,
            this.lastSequence,
          );
          await this.terminalClosePromise;
        }
        return false;
      }
      this.lastSequence = stored.sequence;
      this.transport.notePersistedEvent(stored.event);
      this.transport.flushSoon();
      if (terminal) {
        this.terminalClosePromise = this.transport.closeSession(
          options.terminalReason || eventName,
          stored.sequence,
        );
        await this.terminalClosePromise;
      }
      return true;
    };
    this.pendingOperations += 1;
    const trackedOperation = async () => {
      try {
        return await operation();
      } finally {
        this.pendingOperations = Math.max(0, this.pendingOperations - 1);
      }
    };
    this.writeChain = this.writeChain.then(trackedOperation, trackedOperation).catch((error) => {
      this.onHealth({
        component: "data_plane",
        reason: "capture_failed",
        details: { error_class: error?.name || "Error" },
      });
      return false;
    });
    return this.writeChain;
  }

  closeAfterQueueSaturation(eventName, options = {}) {
    if (this.terminalClosePromise) return this.terminalClosePromise;
    if (!this.stateMachine.closing) {
      try {
        this.stateMachine.transition("closing");
      } catch (error) {
        this.onHealth({
          component: "data_plane",
          reason: "terminal_transition_rejected",
          details: { error_class: error?.name || "Error" },
        });
        return Promise.resolve(false);
      }
    }
    const closeOperation = async () => {
      try {
        const declared = await this.declareDropped(1, "client_overhead_circuit");
        if (!declared) {
          this.onHealth({
            component: "data_plane",
            reason: "terminal_overflow_gap_rejected",
            details: {},
          });
        }
      } catch (error) {
        this.onHealth({
          component: "data_plane",
          reason: "terminal_overflow_gap_failed",
          details: { error_class: error?.name || "Error" },
        });
      }
      this.terminalClosePromise = this.transport.closeSession(
        options.terminalReason || eventName,
        this.lastSequence,
      );
      return this.terminalClosePromise;
    };
    this.terminalClosePromise = this.writeChain.then(closeOperation, closeOperation).catch((error) => {
      this.onHealth({
        component: "data_plane",
        reason: "terminal_overflow_close_failed",
        details: { error_class: error?.name || "Error" },
      });
      return false;
    });
    this.writeChain = this.terminalClosePromise;
    return this.terminalClosePromise;
  }

  async declareDropped(count = 1, reasonCode = "client_overhead_circuit") {
    if (!this.started || this.stateMachine.terminal) return false;
    const boundedCount = Math.max(1, Math.min(256, Number(count) || 1));
    const recovery = await this.spool.getRecoveryState?.(this.playbackSessionId);
    const start = Math.max(
      this.lastSequence,
      Number(recovery?.final_source_sequence) || 0,
    ) + 1;
    const gap = await this.spool.queueGap?.(this.playbackSessionId, {
      start_sequence: start,
      end_sequence: start + boundedCount - 1,
      reason_code: reasonCode,
    });
    if (gap) this.lastSequence = gap.end_sequence;
    return Boolean(gap);
  }

  queueDeclaredDrop(reasonCode, { increment = true } = {}) {
    if (increment) this.pendingDropCount = Math.min(256, this.pendingDropCount + 1);
    if (this.dropDeclarationQueued || this.stateMachine.terminal) return;
    this.dropDeclarationQueued = true;
    const declare = async () => {
      const count = this.pendingDropCount;
      this.pendingDropCount = 0;
      try {
        if (count) await this.declareDropped(count, reasonCode);
      } finally {
        this.dropDeclarationQueued = false;
        if (this.pendingDropCount) this.queueDeclaredDrop(reasonCode, { increment: false });
      }
    };
    this.writeChain = this.writeChain.then(declare, declare).catch((error) => {
      this.dropDeclarationQueued = false;
      this.onHealth({
        component: "data_plane",
        reason: "drop_declaration_failed",
        details: { error_class: error?.name || "Error" },
      });
      return false;
    });
  }

  setOverheadMode(mode, reason = "main_thread_pressure") {
    this.overheadMonitor.adoptMode(mode, reason);
  }

  async close(reason = "component_unmounted") {
    if (!this.started || this.stateMachine.terminal) return false;
    this.stopActiveLeaseHeartbeat();
    if (!this.stateMachine.closing) {
      await this.capture("quit", {
        priority: "critical",
        observationKind: "inferred",
        terminalReason: reason,
        payload: { reason: String(reason || "component_unmounted").slice(0, 128) },
      });
    }
    await this.writeChain.catch(() => {});
    if (this.terminalClosePromise) return this.terminalClosePromise;
    this.terminalClosePromise = this.transport.closeSession(reason, this.lastSequence);
    return this.terminalClosePromise;
  }

  wake({ authenticationRestored = false } = {}) {
    this.transport?.wake({ authenticationRestored });
  }

  stop() {
    this.stopActiveLeaseHeartbeat();
    this.transport?.stop();
    this.spool?.close();
    this.started = false;
  }

  startActiveLeaseHeartbeat() {
    if (!this.persistent || this.activeLeaseTimer != null) return;
    const setIntervalRef = this.runtimeRef?.setInterval?.bind(this.runtimeRef)
      || globalThis.setInterval;
    this.activeLeaseTimer = setIntervalRef(() => {
      if (this.stateMachine.closing || this.stateMachine.terminal) return;
      void this.spool.updateRecoveryState?.(this.playbackSessionId, {
        active_lease_id: this.activeLeaseId,
        active_lease_expires_at_ms: Date.now() + PLAYBACK_DIAGNOSTICS_ACTIVE_LEASE_DURATION_MS,
      }).catch((error) => {
        this.onHealth({
          component: "data_plane",
          reason: "active_lease_refresh_failed",
          details: { error_class: error?.name || "Error" },
        });
      });
    }, PLAYBACK_DIAGNOSTICS_ACTIVE_LEASE_HEARTBEAT_MS);
  }

  stopActiveLeaseHeartbeat() {
    if (this.activeLeaseTimer == null) return;
    const clearIntervalRef = this.runtimeRef?.clearInterval?.bind(this.runtimeRef)
      || globalThis.clearInterval;
    clearIntervalRef(this.activeLeaseTimer);
    this.activeLeaseTimer = null;
  }
}
