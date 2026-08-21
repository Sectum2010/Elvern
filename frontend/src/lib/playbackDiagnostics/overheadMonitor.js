export const PLAYBACK_DIAGNOSTICS_OVERHEAD_MODES = Object.freeze([
  "normal",
  "reduced_sampling",
  "optional_disabled",
  "reduced_aggregates",
  "critical_only",
  "circuit_open",
]);

const MODE_RANK = new Map(
  PLAYBACK_DIAGNOSTICS_OVERHEAD_MODES.map((mode, index) => [mode, index]),
);
const MAX_METRIC_SAMPLES = 128;
const MIN_LATENCY_SAMPLES = 16;
const HIGH_FREQUENCY_EVENTS = new Set([
  "client_incident_pre_frames",
  "client_incident_pre_samples",
  "client_resource_timing",
  "ffmpeg_progress_sample",
  "media_aggregate",
  "performance_aggregate",
  "progress",
]);
const OPTIONAL_EVENTS = new Set([
  "client_capability_unavailable",
  "client_resource_timing",
  "performance_aggregate",
  "resource_sample",
]);
const AGGREGATE_EVENTS = new Set([
  "client_incident_post_aggregate",
  "host_aggregate",
  "media_aggregate",
  "performance_aggregate",
]);
const TERMINAL_OR_GAP_EVENTS = new Set([
  "completed",
  "playback_failed",
  "quit",
  "session_close",
  "telemetry_gap",
]);

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function percentile(values, ratio) {
  if (!values.length) return 0;
  const ordered = [...values].sort((left, right) => left - right);
  return ordered[Math.min(ordered.length - 1, Math.ceil(ordered.length * ratio) - 1)];
}

export function overheadModeRank(mode) {
  return MODE_RANK.get(String(mode || "")) ?? 0;
}

export class PlaybackDiagnosticsOverheadMonitor {
  constructor({ onModeChange = () => {} } = {}) {
    this.mode = "normal";
    this.onModeChange = onModeChange;
    this.samples = new Map();
    this.errorCount = 0;
    this.aggregateCounter = 0;
  }

  adoptMode(mode, reason = "external_pressure") {
    const targetRank = overheadModeRank(mode);
    while (overheadModeRank(this.mode) < targetRank) this.escalate(reason);
    return this.mode;
  }

  escalate(reason) {
    const currentRank = overheadModeRank(this.mode);
    if (currentRank >= PLAYBACK_DIAGNOSTICS_OVERHEAD_MODES.length - 1) return this.mode;
    this.mode = PLAYBACK_DIAGNOSTICS_OVERHEAD_MODES[currentRank + 1];
    this.onModeChange(this.mode, String(reason || "diagnostics_pressure"));
    return this.mode;
  }

  observeLatency(name, value, {
    p95LimitMs,
    hardLimitMs,
    minimumSamples = MIN_LATENCY_SAMPLES,
  }) {
    const latency = finite(value);
    if (latency == null || latency < 0) return this.mode;
    const key = String(name || "latency").slice(0, 64);
    const samples = this.samples.get(key) || [];
    samples.push(latency);
    if (samples.length > MAX_METRIC_SAMPLES) samples.shift();
    this.samples.set(key, samples);
    if (latency > hardLimitMs) return this.escalate(`${key}_hard_limit`);
    if (samples.length >= minimumSamples && percentile(samples, 0.95) > p95LimitMs) {
      return this.escalate(`${key}_p95_limit`);
    }
    return this.mode;
  }

  observeRatio(name, value, { reducedAt = 0.75, criticalAt = 0.95 } = {}) {
    const ratio = finite(value);
    if (ratio == null || ratio < reducedAt) return this.mode;
    const reason = `${String(name || "pressure").slice(0, 64)}_pressure`;
    this.escalate(reason);
    if (ratio >= criticalAt && overheadModeRank(this.mode) < overheadModeRank("critical_only")) {
      this.escalate(reason);
    }
    return this.mode;
  }

  recordError(reason = "diagnostics_error") {
    this.errorCount = Math.min(64, this.errorCount + 1);
    const target = this.errorCount >= 64
      ? "circuit_open"
      : this.errorCount >= 32
        ? "critical_only"
        : this.errorCount >= 16
          ? "reduced_aggregates"
          : this.errorCount >= 8
            ? "optional_disabled"
            : this.errorCount >= 4
              ? "reduced_sampling"
              : "normal";
    return this.adoptMode(target, reason);
  }

  allows(eventName, { critical = false } = {}) {
    const name = String(eventName || "");
    const rank = overheadModeRank(this.mode);
    if (rank >= overheadModeRank("circuit_open")) return TERMINAL_OR_GAP_EVENTS.has(name);
    if (rank >= overheadModeRank("critical_only")) return critical;
    if (rank >= overheadModeRank("reduced_aggregates") && AGGREGATE_EVENTS.has(name)) {
      this.aggregateCounter += 1;
      return this.aggregateCounter % 4 === 0;
    }
    if (rank >= overheadModeRank("optional_disabled") && OPTIONAL_EVENTS.has(name)) return false;
    if (rank >= overheadModeRank("reduced_sampling") && HIGH_FREQUENCY_EVENTS.has(name)) return false;
    return true;
  }

  snapshot() {
    return {
      mode: this.mode,
      error_count: this.errorCount,
      metrics: Object.fromEntries(
        [...this.samples.entries()].map(([name, values]) => [name, {
          count: values.length,
          p95: percentile(values, 0.95),
          max: values.length ? Math.max(...values) : 0,
        }]),
      ),
    };
  }
}
