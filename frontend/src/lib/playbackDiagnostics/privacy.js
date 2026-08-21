const SECRET_PATTERN = /(?:authorization\s*[:=]|bearer\s+[a-z0-9._~+/=-]{12,}|(?:access|refresh|session|id)[_-]?token\s*[:=]|[?&](?:token|key|resourcekey|auth|signature|sig)=)/i;
const ABSOLUTE_PATH_PATTERN = /^(?:\/|~[/\\]|[a-zA-Z]:[/\\]|\\\\)/;
const SAFE_PLAYBACK_ROUTE_PATTERN = /^\/api\/browser-playback\/[A-Za-z0-9_./:-]{0,480}$/;
const SAFE_ROUTE_PAYLOAD_KEYS = new Set(["normalized_route", "route_template"]);

// Client payloads are sanitized before IndexedDB, not only after server upload.
// Keep this vocabulary explicit so arbitrary app state can never enter the spool.
export const SAFE_CLIENT_DIAGNOSTIC_PAYLOAD_KEYS = new Set([
  "action_origin", "active", "actual_duration_ms", "algorithm_version", "available",
  "audio_stream_index", "batch_bytes", "batch_events", "batches_acked", "batches_sent",
  "buffer_hole_count", "buffer_hole_sizes_ms", "buffer_slope", "buffered_ahead_ms",
  "buffered_behind_ms", "buffered_range_count", "buffered_ranges", "bytes",
  "callback_lateness_ms", "callback_monotonic_ms", "candidate_time_ns", "capabilities",
  "capacity_state", "chunk_sequence", "client_anchor_monotonic_us", "client_anchor_wall_ms",
  "client_fragment_loader_detail", "client_queue_bytes", "client_queue_depth",
  "client_throughput_bps", "clock_algorithm", "clock_step_detected", "compute_pressure",
  "contiguous_buffered_ahead_ms", "corrupted_frames", "count", "cumulative_dropped_frames",
  "cumulative_total_frames", "current", "current_time_ms", "decoded_body_bytes",
  "decoder_delay_ms", "delta_dropped_frames", "delta_total_frames", "device_memory",
  "device_memory_gib", "document_was_discarded", "dropped_frame_ratio", "dropped_frames",
  "duplicate_count", "duration_ms", "encoded_body_bytes", "end_ms", "ended", "error_class",
  "error_code", "event_loop_lag_ms", "events_dropped", "expected_display_time_ms", "fatal",
  "final", "first_byte_ms", "frame_cadence_ms", "frame_counter_reset", "frame_ring_complete",
  "frame_samples", "freeze_resume_events", "fullscreen", "global_queue_bytes", "height",
  "hidden_duration_ms", "hls_event", "hls_level", "in_flight", "indexeddb", "initiator_type",
  "js_heap_limit_bytes", "last_byte_ms", "last_client_observed_time", "loader_aborted",
  "logical_request_identity", "long_animation_frame_ms", "long_animation_frame_timing",
  "long_task_ms", "long_task_timing", "manifest_revision", "media_element_time_ms", "memory",
  "minimum_buffer_ms", "muted",
  "native_hls_internal_cache", "network_information", "network_state", "normalized_route",
  "observed_drift_ppm", "offset_spread_ns", "oldest_event_age_ms", "online", "orientation",
  "out_of_order_count", "page_state", "path_hash_sha256", "paused", "performance_memory",
  "performance_observer", "picture_in_picture", "playback_rate", "played_range_count",
  "played_ranges", "playhead_advancement_rate", "presentation_time_ms", "presented_frames",
  "previous", "previous_incident_id", "queue_depth", "ranges_truncated", "ready_state", "reason",
  "recorder_overhead_ms", "recorder_queue_delay_ms", "recovery_action", "recurrence_group_id",
  "redirect_count", "rejected_event_name", "request_attempt", "request_attempt_id",
  "request_duration_ms", "request_start_ms", "request_video_frame_callback", "requested",
  "resource_timing", "response_end_ms", "response_headers_ready_ms", "retries", "retry_attempt",
  "revision", "ring_complete", "sample_count", "sample_interval_ms", "sample_monotonic_ms",
  "sample_window_ms", "samples", "seekable_range_count", "seekable_ranges", "seeking",
  "segment_duration_ms", "segment_index", "segment_media_start_ms", "selected_sample_rtt_ns",
  "serialized_bytes", "server_segment_request_trace", "stall_duration_ms", "stall_reason",
  "standalone", "start_ms", "state", "storage", "storage_estimate", "storage_quota_bytes",
  "storage_usage_bytes", "suspension_lower_bound_ns", "suspension_upper_bound_ns",
  "timer_callback_gap_ms", "total_buffered_ms", "total_frames", "total_js_heap_bytes",
  "transfer_bytes", "type", "unavailable_reason", "upload_bytes", "upload_latency_ms",
  "used_js_heap_bytes", "user_agent_specific_memory", "video_playback_quality", "visible",
  "volume", "width", "window_dropped_frame_ratio",
]);

export function normalizeDiagnosticRoute(value) {
  const raw = String(value || "");
  let pathname = raw;
  try {
    const base = typeof window !== "undefined"
      ? window.location.origin
      : "http://elvern.invalid";
    pathname = new URL(raw, base).pathname;
  } catch {
    pathname = raw.split("?", 1)[0].split("#", 1)[0];
  }
  return pathname
    .replace(/\/[0-9a-f]{24,64}(?=\/|$)/gi, "/:id")
    .replace(/\/segments\/\d+\.(?:m4s|mp4)$/i, "/segments/:segment")
    .slice(0, 512);
}

export function diagnosticUrlIdentity(value) {
  return { normalized_route: normalizeDiagnosticRoute(value) };
}

export async function sha256DiagnosticPath(value, cryptoRef = globalThis.crypto) {
  const pathname = normalizeDiagnosticRoute(value);
  if (!cryptoRef?.subtle?.digest) return null;
  const encoded = new TextEncoder().encode(pathname);
  const digest = await cryptoRef.subtle.digest("SHA-256", encoded);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export function sanitizeDiagnosticString(value, { allowBasename = false } = {}) {
  const text = String(value ?? "");
  if (SECRET_PATTERN.test(text)) {
    throw new Error("Diagnostic value contains secret-like data");
  }
  if (!allowBasename && ABSOLUTE_PATH_PATTERN.test(text)) {
    throw new Error("Diagnostic value contains an absolute path");
  }
  if (!allowBasename && text.includes("://")) {
    throw new Error("Diagnostic value contains a full URL");
  }
  return text.slice(0, 4_096);
}

export function safeDiagnosticBasename(value) {
  const basename = String(value ?? "").replaceAll("\\", "/").split("/").at(-1) || "";
  if (!basename || basename === "." || basename === ".." || basename.includes("\0")) {
    return "unknown-media";
  }
  return sanitizeDiagnosticString(basename, { allowBasename: true });
}

function sanitizeScalar(value, key) {
  if (value == null || typeof value === "boolean" || typeof value === "number") {
    return Number.isFinite(value) || typeof value !== "number" ? value : null;
  }
  if (typeof value === "string") {
    if (SAFE_ROUTE_PAYLOAD_KEYS.has(key)) {
      if (
        !SAFE_PLAYBACK_ROUTE_PATTERN.test(value)
        || value.includes("..")
        || value.includes("?")
        || value.includes("#")
      ) {
        throw new Error("Diagnostic value contains an invalid playback route");
      }
      return value;
    }
    return sanitizeDiagnosticString(value, { allowBasename: key === "source_original_filename" });
  }
  return undefined;
}

export function sanitizeClientDiagnosticPayload(value, depth = 0) {
  if (!value || typeof value !== "object" || Array.isArray(value) || depth > 4) {
    return {};
  }
  const result = {};
  Object.entries(value).forEach(([key, entry]) => {
    if (!SAFE_CLIENT_DIAGNOSTIC_PAYLOAD_KEYS.has(key)) return;
    if (entry && typeof entry === "object" && !Array.isArray(entry)) {
      result[key] = sanitizeClientDiagnosticPayload(entry, depth + 1);
      return;
    }
    if (Array.isArray(entry)) {
      result[key] = entry.slice(0, 512).map((item) => {
        if (item && typeof item === "object" && !Array.isArray(item)) {
          return sanitizeClientDiagnosticPayload(item, depth + 1);
        }
        if (Array.isArray(item)) {
          return item.slice(0, 16).map((nested) => sanitizeScalar(nested, key));
        }
        return sanitizeScalar(item, key);
      }).filter((item) => item !== undefined);
      return;
    }
    const sanitized = sanitizeScalar(entry, key);
    if (sanitized !== undefined) {
      result[key] = sanitized;
    }
  });
  return result;
}

export function classifyBrowserPlatform(navigatorRef = globalThis.navigator) {
  if (!navigatorRef) {
    return {
      platform: "unknown",
      browser_family: "unknown",
      browser_version: "",
      os_family: "unknown",
      os_version: "",
    };
  }
  const ua = String(navigatorRef.userAgent || "");
  const browserPatterns = [
    ["edge", /Edg(?:A|iOS)?\/([0-9.]+)/],
    ["chrome", /(?:Chrome|CriOS)\/([0-9.]+)/],
    ["firefox", /(?:Firefox|FxiOS)\/([0-9.]+)/],
    ["safari", /Version\/([0-9.]+).+Safari\//],
  ];
  const osPatterns = [
    ["ios", /(?:iPhone )?OS ([0-9_]+)/],
    ["macos", /Mac OS X ([0-9_]+)/],
    ["windows", /Windows NT ([0-9.]+)/],
    ["android", /Android ([0-9.]+)/],
    ["linux", /Linux/],
  ];
  const browser = browserPatterns.find(([, pattern]) => pattern.test(ua));
  const os = osPatterns.find(([, pattern]) => pattern.test(ua));
  const browserMatch = browser?.[1].exec(ua);
  const osMatch = os?.[1].exec(ua);
  return {
    platform: os?.[0] || "unknown",
    browser_family: browser?.[0] || "unknown",
    browser_version: browserMatch?.[1] || "",
    os_family: os?.[0] || "unknown",
    os_version: (osMatch?.[1] || "").replaceAll("_", "."),
  };
}
