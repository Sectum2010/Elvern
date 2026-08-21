import { PLAYBACK_DIAGNOSTICS_SCHEMA_VERSION } from "./constants";
import { sanitizeClientDiagnosticPayload } from "./privacy";

export function createDiagnosticId(prefix = "diag") {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `${prefix}_${crypto.randomUUID().replaceAll("-", "")}`;
  }
  const random = Math.random().toString(36).slice(2);
  return `${prefix}_${Date.now().toString(36)}${random}`;
}

export function decimalNanoseconds(value) {
  if (value == null) return null;
  if (typeof value === "bigint") return value.toString(10);
  if (typeof value === "string" && /^-?\d+$/.test(value)) return value;
  if (typeof value === "number" && Number.isSafeInteger(value)) return String(value);
  throw new TypeError("Nanosecond values must be BigInt, safe integers, or decimal strings");
}

export function captureClientClock() {
  const hasPerformance = typeof performance !== "undefined";
  return {
    client_wall_time_ms: Date.now(),
    client_monotonic_time_us: hasPerformance ? performance.now() * 1_000 : null,
    client_time_origin_ms: hasPerformance && Number.isFinite(performance.timeOrigin)
      ? performance.timeOrigin
      : null,
  };
}

export function createPlaybackDiagnosticEvent({
  eventName,
  playbackSessionId,
  eventSequence,
  sourceSequence = eventSequence,
  priority = "normal",
  severity = "info",
  observationKind = "measured_client",
  payload = {},
  clock = {},
  context = {},
  capturedClock = null,
}) {
  const now = capturedClock || captureClientClock();
  const event = {
    schema_version: PLAYBACK_DIAGNOSTICS_SCHEMA_VERSION,
    event_id: createDiagnosticId("event"),
    event_name: String(eventName || "unknown").toLowerCase(),
    event_source: "client",
    severity,
    priority,
    playback_session_id: playbackSessionId,
    playback_attempt_id: context.playback_attempt_id ?? null,
    attachment_id: context.attachment_id ?? null,
    epoch_id: context.epoch_id ?? null,
    worker_id: null,
    incident_id: context.incident_id ?? null,
    decision_id: context.decision_id ?? null,
    trace_id: context.trace_id ?? null,
    span_id: context.span_id ?? null,
    parent_span_id: context.parent_span_id ?? null,
    event_sequence: Number(eventSequence),
    source_sequence: Number(sourceSequence),
    ...now,
    client_timer_resolution_us: context.client_timer_resolution_us ?? null,
    server_wall_time_ns: null,
    server_monotonic_time_ns: null,
    server_received_wall_time_ns: null,
    server_received_monotonic_time_ns: null,
    aligned_wall_time_ns: clock.aligned_wall_time_ns
      ? decimalNanoseconds(clock.aligned_wall_time_ns)
      : null,
    clock_offset_ns: clock.clock_offset_ns != null
      ? decimalNanoseconds(clock.clock_offset_ns)
      : null,
    clock_uncertainty_ns: clock.clock_uncertainty_ns != null
      ? decimalNanoseconds(clock.clock_uncertainty_ns)
      : null,
    network_rtt_ns: clock.network_rtt_ns != null
      ? decimalNanoseconds(clock.network_rtt_ns)
      : null,
    clock_generation: Number.isInteger(Number(clock.clock_generation))
      ? Number(clock.clock_generation)
      : null,
    clock_valid: typeof clock.clock_valid === "boolean" ? clock.clock_valid : null,
    clock_invalid_reason: clock.clock_invalid_reason || null,
    playhead_ms: context.playhead_ms ?? null,
    media_element_time_ms: context.media_element_time_ms ?? null,
    duration_ms: context.duration_ms ?? null,
    platform: context.platform ?? null,
    device_class: context.device_class ?? null,
    browser_family: context.browser_family ?? null,
    browser_version: context.browser_version ?? null,
    os_family: context.os_family ?? null,
    os_version: context.os_version ?? null,
    hls_engine: context.hls_engine ?? null,
    playback_mode: context.playback_mode ?? null,
    stream_mode: context.stream_mode ?? null,
    source_kind: context.source_kind ?? null,
    observation_kind: observationKind,
    measurement_method: context.measurement_method ?? null,
    measurement_resolution: context.measurement_resolution ?? null,
    measurement_uncertainty: context.measurement_uncertainty ?? null,
    sample_window_ms: context.sample_window_ms ?? null,
    capability_available: context.capability_available ?? null,
    unavailable_reason: context.unavailable_reason ?? null,
    payload: sanitizeClientDiagnosticPayload(payload),
  };
  return event;
}
