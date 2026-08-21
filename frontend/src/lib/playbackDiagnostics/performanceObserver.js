import { diagnosticUrlIdentity } from "./privacy";

const EVENT_LOOP_SAMPLE_MS = 250;
const STORAGE_SAMPLE_MS = 30_000;
const RESOURCE_IDENTITIES_MAX = 2_048;
const BACKGROUND_THROTTLE_THRESHOLD_MS = 500;

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export class PlaybackPerformanceDiagnosticObserver {
  constructor({
    record,
    windowRef = globalThis.window,
    documentRef = globalThis.document,
    navigatorRef = globalThis.navigator,
  }) {
    this.record = record;
    this.windowRef = windowRef;
    this.documentRef = documentRef;
    this.navigatorRef = navigatorRef;
    this.performanceRef = windowRef?.performance || globalThis.performance;
    this.observers = [];
    this.eventLoopTimer = null;
    this.storageTimer = null;
    this.recorderStartMonotonicMs = null;
    this.resourceIdentities = new Set();
    this.resourceIdentityOrder = [];
  }

  start() {
    this.recorderStartMonotonicMs = this.performanceRef?.now?.() || 0;
    this.observeEntries("longtask", (entry) => {
      this.record("long_task", {
        priority: entry.duration >= 200 ? "high" : "normal",
        payload: { long_task_ms: entry.duration, start_ms: entry.startTime },
      });
    });
    this.observeEntries("long-animation-frame", (entry) => {
      this.record("long_animation_frame", {
        priority: entry.duration >= 200 ? "high" : "normal",
        payload: { long_animation_frame_ms: entry.duration, start_ms: entry.startTime },
      });
    });
    this.observeEntries("resource", (entry) => this.recordResourceEntry(entry));
    let expected = this.performanceRef?.now?.() || 0;
    this.eventLoopTimer = this.windowRef?.setInterval?.(() => {
      const now = this.performanceRef?.now?.() || expected + EVENT_LOOP_SAMPLE_MS;
      const lag = Math.max(0, now - expected - EVENT_LOOP_SAMPLE_MS);
      expected = now;
      if (this.documentRef?.visibilityState === "hidden") {
        if (lag >= BACKGROUND_THROTTLE_THRESHOLD_MS) {
          this.record("background_timer_throttle", {
            observationKind: "measured_client",
            payload: {
              timer_callback_gap_ms: lag + EVENT_LOOP_SAMPLE_MS,
              page_state: "hidden",
              sample_interval_ms: EVENT_LOOP_SAMPLE_MS,
            },
            sampleWindowMs: EVENT_LOOP_SAMPLE_MS,
          });
        }
        return;
      }
      this.record("event_loop_aggregate", {
        payload: {
          event_loop_lag_ms: lag,
          sample_interval_ms: EVENT_LOOP_SAMPLE_MS,
          page_state: "visible",
        },
        sampleWindowMs: EVENT_LOOP_SAMPLE_MS,
      });
    }, EVENT_LOOP_SAMPLE_MS);
    this.storageTimer = this.windowRef?.setInterval?.(
      () => this.recordStorageAndMemory(),
      STORAGE_SAMPLE_MS,
    );
    this.recordStorageAndMemory();
  }

  stop() {
    this.observers.forEach((observer) => observer.disconnect());
    this.observers = [];
    if (this.eventLoopTimer != null) this.windowRef?.clearInterval?.(this.eventLoopTimer);
    if (this.storageTimer != null) this.windowRef?.clearInterval?.(this.storageTimer);
    this.eventLoopTimer = null;
    this.storageTimer = null;
    this.resourceIdentities.clear();
    this.resourceIdentityOrder = [];
  }

  observeEntries(type, handler) {
    const Observer = this.windowRef?.PerformanceObserver;
    if (typeof Observer !== "function" || !Observer.supportedEntryTypes?.includes?.(type)) {
      return false;
    }
    try {
      let sampled = false;
      const observer = new Observer((list) => {
        const current = list.getEntries().filter(
          (entry) => Number(entry.startTime) >= this.recorderStartMonotonicMs,
        );
        if (current.length && !sampled) {
          sampled = true;
          this.record("client_capability_state", {
            payload: { type, state: "samples_received", available: true },
          });
        }
        current.forEach(handler);
      });
      observer.observe({ type, buffered: true });
      this.observers.push(observer);
      this.record("client_capability_state", {
        payload: { type, state: "observer_started", available: true },
      });
      return true;
    } catch (error) {
      this.record("client_capability_state", {
        observationKind: "unsupported",
        payload: {
          type,
          state: "observer_failed",
          available: true,
          error_class: error?.name || "Error",
        },
      });
      return false;
    }
  }

  resourceIdentity(entry, normalizedRoute) {
    return [
      Number(entry.startTime).toFixed(3),
      normalizedRoute,
      String(entry.initiatorType || ""),
      Number(entry.duration).toFixed(3),
      Number(entry.transferSize || 0),
      Number(entry.encodedBodySize || 0),
    ].join("|");
  }

  recordResourceEntry(entry) {
    if (Number(entry.startTime) < this.recorderStartMonotonicMs) return;
    const name = String(entry.name || "");
    if (!name.includes("/api/browser-playback/") && !name.includes("/api/stream/")) return;
    const identity = diagnosticUrlIdentity(name);
    const key = this.resourceIdentity(entry, identity.normalized_route);
    if (this.resourceIdentities.has(key)) return;
    this.resourceIdentities.add(key);
    this.resourceIdentityOrder.push(key);
    if (this.resourceIdentityOrder.length > RESOURCE_IDENTITIES_MAX) {
      this.resourceIdentities.delete(this.resourceIdentityOrder.shift());
    }
    this.record("client_resource_timing", {
      payload: {
        ...identity,
        request_start_ms: finite(entry.requestStart),
        response_headers_ready_ms: finite(entry.responseStart),
        response_end_ms: finite(entry.responseEnd),
        request_duration_ms: finite(entry.duration),
        transfer_bytes: finite(entry.transferSize),
        encoded_body_bytes: finite(entry.encodedBodySize),
        decoded_body_bytes: finite(entry.decodedBodySize),
        redirect_count: entry.redirectEnd > entry.redirectStart ? 1 : 0,
        initiator_type: String(entry.initiatorType || "").slice(0, 64),
      },
    });
  }

  async recordStorageAndMemory() {
    const payload = {
      device_memory_gib: finite(this.navigatorRef?.deviceMemory),
      memory: this.performanceRef?.memory
        ? {
          used_js_heap_bytes: finite(this.performanceRef.memory.usedJSHeapSize),
          total_js_heap_bytes: finite(this.performanceRef.memory.totalJSHeapSize),
          js_heap_limit_bytes: finite(this.performanceRef.memory.jsHeapSizeLimit),
        }
        : { available: false, unavailable_reason: "unsupported_web" },
    };
    try {
      const estimate = await this.navigatorRef?.storage?.estimate?.();
      if (estimate) {
        payload.storage_usage_bytes = finite(estimate.usage);
        payload.storage_quota_bytes = finite(estimate.quota);
      } else {
        payload.storage = { available: false, unavailable_reason: "unsupported_web" };
      }
    } catch {
      payload.storage = { available: false, unavailable_reason: "storage_estimate_failed" };
    }
    this.record("client_resource_aggregate", {
      payload,
      sampleWindowMs: STORAGE_SAMPLE_MS,
    });
  }
}
