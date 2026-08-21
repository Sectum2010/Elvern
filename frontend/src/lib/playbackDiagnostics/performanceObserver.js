import { diagnosticUrlIdentity } from "./privacy";

const EVENT_LOOP_SAMPLE_MS = 250;
const RESOURCE_SAMPLE_MS = 5_000;
const STORAGE_SAMPLE_MS = 30_000;

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export class PlaybackPerformanceDiagnosticObserver {
  constructor({
    record,
    windowRef = globalThis.window,
    navigatorRef = globalThis.navigator,
  }) {
    this.record = record;
    this.windowRef = windowRef;
    this.navigatorRef = navigatorRef;
    this.performanceRef = windowRef?.performance || globalThis.performance;
    this.observers = [];
    this.eventLoopTimer = null;
    this.resourceTimer = null;
    this.storageTimer = null;
    this.lastResourceStartTime = 0;
  }

  start() {
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
    let expected = this.performanceRef?.now?.() || 0;
    this.eventLoopTimer = this.windowRef?.setInterval?.(() => {
      const now = this.performanceRef?.now?.() || expected + EVENT_LOOP_SAMPLE_MS;
      const lag = Math.max(0, now - expected - EVENT_LOOP_SAMPLE_MS);
      expected = now;
      this.record("event_loop_aggregate", {
        payload: { event_loop_lag_ms: lag, sample_interval_ms: EVENT_LOOP_SAMPLE_MS },
        sampleWindowMs: EVENT_LOOP_SAMPLE_MS,
      });
    }, EVENT_LOOP_SAMPLE_MS);
    this.resourceTimer = this.windowRef?.setInterval?.(
      () => this.recordResourceTiming(),
      RESOURCE_SAMPLE_MS,
    );
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
    if (this.resourceTimer != null) this.windowRef?.clearInterval?.(this.resourceTimer);
    if (this.storageTimer != null) this.windowRef?.clearInterval?.(this.storageTimer);
    this.eventLoopTimer = null;
    this.resourceTimer = null;
    this.storageTimer = null;
  }

  observeEntries(type, handler) {
    const Observer = this.windowRef?.PerformanceObserver;
    if (typeof Observer !== "function" || !Observer.supportedEntryTypes?.includes?.(type)) return;
    try {
      const observer = new Observer((list) => list.getEntries().forEach(handler));
      observer.observe({ type, buffered: true });
      this.observers.push(observer);
    } catch {
      // Capability reporting records unsupported APIs separately.
    }
  }

  recordResourceTiming() {
    const entries = this.performanceRef?.getEntriesByType?.("resource") || [];
    entries.forEach((entry) => {
      if (entry.startTime <= this.lastResourceStartTime) return;
      const name = String(entry.name || "");
      if (!name.includes("/api/browser-playback/") && !name.includes("/api/stream/")) return;
      this.record("client_resource_timing", {
        payload: {
          ...diagnosticUrlIdentity(name),
          request_start_ms: finite(entry.requestStart),
          response_headers_ready_ms: finite(entry.responseStart),
          response_end_ms: finite(entry.responseEnd),
          request_duration_ms: finite(entry.duration),
          transfer_bytes: finite(entry.transferSize),
          encoded_body_bytes: finite(entry.encodedBodySize),
          decoded_body_bytes: finite(entry.decodedBodySize),
          redirect_count: entry.redirectEnd > entry.redirectStart ? 1 : 0,
        },
      });
      this.lastResourceStartTime = Math.max(this.lastResourceStartTime, entry.startTime);
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
