from __future__ import annotations

import hashlib
import hmac
import ipaddress
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


SECRET_PATTERNS = (
    re.compile(r"(?i)\bauthorization\s*[:=]"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b(access|refresh|session|id)[_-]?token\s*[:=]"),
    re.compile(r"(?i)\b(client[_-]?secret|totp|password|recovery[_-]?code)\s*[:=]"),
    re.compile(r"(?i)\b(cookie|set-cookie)\s*[:=]"),
    re.compile(r"(?i)[?&](token|key|resourcekey|auth|signature|sig)="),
    re.compile(r"(?i)ya29\.[a-z0-9_-]+"),
)
ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"^(?:/|~[/\\])"),
    re.compile(r"^[a-zA-Z]:[/\\]"),
    re.compile(r"^\\\\"),
)

EVENT_ENVELOPE_FIELDS = frozenset(
    {
        "schema_version",
        "event_id",
        "event_name",
        "event_source",
        "severity",
        "priority",
        "playback_session_id",
        "playback_attempt_id",
        "attachment_id",
        "epoch_id",
        "worker_id",
        "incident_id",
        "decision_id",
        "trace_id",
        "span_id",
        "parent_span_id",
        "event_sequence",
        "source_sequence",
        "client_wall_time_ms",
        "client_monotonic_time_us",
        "client_time_origin_ms",
        "client_timer_resolution_us",
        "server_wall_time_ns",
        "server_monotonic_time_ns",
        "server_received_wall_time_ns",
        "server_received_monotonic_time_ns",
        "aligned_wall_time_ns",
        "clock_offset_ns",
        "clock_uncertainty_ns",
        "network_rtt_ns",
        "playhead_ms",
        "media_element_time_ms",
        "duration_ms",
        "platform",
        "device_class",
        "browser_family",
        "browser_version",
        "os_family",
        "os_version",
        "hls_engine",
        "playback_mode",
        "stream_mode",
        "source_kind",
        "observation_kind",
        "measurement_method",
        "measurement_resolution",
        "measurement_uncertainty",
        "sample_window_ms",
        "capability_available",
        "unavailable_reason",
        "payload",
    }
)

# Payloads remain useful only when their vocabulary is explicit. Unknown keys are
# dropped rather than persisted as arbitrary application data.
SAFE_PAYLOAD_KEYS = frozenset(
    {
        "action_origin", "active", "actual_bytes", "actual_duration_ms",
        "aggregate_interval_ms", "algorithm_version", "alternative_hypotheses",
        "append_latency_ms", "architecture", "attempt_reason", "audio_codec",
        "audio_profile", "audio_stream_index", "audio_track_count", "available_bytes",
        "average_segment_bytes", "back_buffer_seconds", "batch_bytes", "batch_events",
        "batches_acked", "batches_sent", "bit_depth", "bottleneck_class",
        "buffer_hole_count", "buffer_hole_sizes_ms", "buffer_slope", "buffered_ahead_ms",
        "buffered_behind_ms", "buffered_ranges", "bytes", "bytes_read", "bytes_written",
        "cache_control_class", "callback_lateness_ms", "candidate_time_ns", "cancelled", "capabilities",
        "capacity_state", "channel_count", "channel_layout", "chunk_sequence",
        "client_attach_revision", "client_buffer_seconds", "client_fragment_loader_detail",
        "client_queue_bytes", "client_queue_depth", "client_received", "client_throughput_bps",
        "clock_algorithm", "clock_drift_ns", "clock_step_detected", "codec", "color_primaries",
        "color_space", "color_transfer", "command_fingerprint", "committed_playhead_ms",
        "compression_ms", "confidence", "connection_path", "container", "content_range_end",
        "content_range_start", "contiguous_buffered_ahead_ms", "corrupted_frames",
        "cpu_percent", "cpu_seconds", "cpu_seconds_per_media_second", "created_at_utc",
        "current_source_hash", "current_time_ms", "decision_action", "decoded_body_bytes",
        "decoder_delay_ms", "delta", "derp_region", "destination", "device_memory_gib",
        "diagnostics_bytes", "diagnostics_free_bytes", "disk_free_bytes", "disk_total_bytes", "disk_usage_bytes",
        "dropped_event_count", "dropped_frame_ratio", "dropped_frames", "duration_ms",
        "effective_rate_x", "elapsed_ms", "encoded_body_bytes", "encoder_publication_gap_ms",
        "ended", "epoch_revision", "error_class", "error_code", "error_detail_hash",
        "event_loop_lag_ms", "events_dropped", "events_generated", "exception_class",
        "exit_code", "expected_bytes", "expected_display_time_ms", "external_workload_percent",
        "filesystem_free_bytes", "filesystem_state", "first_byte_ms", "first_frame_ms",
        "fatal", "fps", "frame_cadence_ms", "frame_count", "frame_height", "frame_width",
        "free_inodes", "frontier_ms", "fsync_ms", "generated_at_utc", "generation_latency_ms",
        "gpu_decoder_percent", "gpu_encoder_percent", "gpu_memory_bytes", "gpu_temperature_c",
        "gpu_utilization_percent", "hash", "hdr", "height", "hls_event", "hls_level",
        "hls_stats", "host_boot_id_hash", "http_status", "identity", "in_flight",
        "incident_phase", "incident_type", "input_bitrate_bps", "input_bytes", "input_snapshot",
        "io_pressure", "is_adult", "is_muted", "key_id", "last_byte_ms", "last_client_observed_time",
        "last_error_class", "last_observed_ns", "latency_ms", "level", "load_average",
        "loader_stats", "long_animation_frame_ms", "long_task_ms", "longest_gap_ms",
        "manifest_revision", "max_buffer_size_bytes", "max_segment_bytes", "measurement",
        "media_item_id", "media_start_ms", "media_end_ms", "memory_available_bytes",
        "memory_current_bytes", "memory_pressure", "memory_rss_bytes", "memory_pss_bytes",
        "minimum_buffer_ms", "missing_capabilities", "missing_evidence", "muted", "native_hls_internal_cache",
        "network_state", "nonce", "normalized_route", "oldest_event_age_ms", "online",
        "orientation", "out_of_order_count", "out_time_ms", "output_bitrate_bps", "output_bytes",
        "page_state", "path_class", "pause_duration_ms", "paused", "peer_pseudonym",
        "pixel_format", "playback_rate", "playhead_advancement_rate", "policy_version",
        "predicted_duration_ms", "predicted_ready_time_ns", "prediction_id", "prediction_kind",
        "presented_frames", "presentation_time_ms", "priority", "process_state", "profile",
        "progress", "provider_request_id", "provider_throughput_bps", "publish_latency_ms",
        "queue_depth", "queue_wait_ms", "range_end", "range_start", "rate_bps", "ready_state",
        "reason", "received_bytes", "recorder_overhead_ms", "recovery_action", "redirect_count",
        "request_duration_ms", "request_start_ms", "request_video_frame_supported", "resolution",
        "response_end_ms", "response_headers_ready_ms", "retries", "ring_complete", "route_template",
        "runway_ms", "sample_count", "sample_interval_ms", "sample_monotonic_ms", "sample_rate", "samples", "segment_bytes",
        "segment_duration_ms", "segment_index", "segment_media_end_ms", "segment_media_start_ms",
        "selected_threads", "sequence_gap_count", "serialized_bytes", "serialization_ms", "session_state",
        "severity", "signal", "source_fingerprint", "source_original_filename", "source_rate_bps",
        "source_sequence", "speed_x", "stall_confirmed", "stall_duration_ms", "stall_reason",
        "standalone", "status", "storage", "storage_quota_bytes", "storage_usage_bytes", "stream_mode", "subtitle_count",
        "supporting_event_ids", "supporting_signals", "suspension_lower_bound_ns",
        "suspension_upper_bound_ns", "tailscale_health", "target_threads", "thermal_state",
        "throughput_bps", "time_to_first_byte_ms", "time_to_last_byte_ms", "timer_resolution_us",
        "total_buffered_ms", "total_frames", "total_size_bytes", "trace_event", "transfer_bytes",
        "transcode_strategy", "uncertainty_ns", "unsupported_fields", "upload_bytes", "upload_latency_ms",
        "url_hash", "video_codec", "video_level", "video_profile", "visible", "volume", "width",
        "worker_threads", "writer_latency_ms", "writer_queue_depth", "xid", "year",
        "document_was_discarded", "played_range_count", "played_ranges", "seekable_range_count",
        "seekable_ranges", "buffered_range_count", "ranges_truncated", "used_js_heap_bytes",
        "total_js_heap_bytes", "js_heap_limit_bytes", "duplicate_count", "attachment_revision",
        "stream_identity",
        "measured", "method", "available", "unavailable_reason", "value", "unit", "min", "max",
        "p5", "p50", "p95", "avg10", "avg60", "avg300", "some", "full", "total",
        "user", "system", "idle", "iowait", "steal", "per_core", "runnable_tasks",
        "context_switches", "interrupts", "frequency_hz", "swap_total_bytes", "swap_free_bytes",
        "swap_in", "swap_out", "swap_total", "swap_free", "buffers", "cache", "active", "inactive",
        "transcode_free_bytes", "major_faults", "minor_faults", "read_bytes", "write_bytes",
        "cgroup", "cpu", "memory", "io", "psi", "gpu", "tailscale", "process", "ffmpeg",
        "host", "client", "server", "provider", "atc", "recorder", "ranges", "start_ms", "end_ms",
        "size_bytes", "count", "type", "state", "revision", "selected", "current", "previous",
        "requested", "applied", "blocked", "failed", "success", "complete", "final",
        "absolute_error_ms", "relative_error", "signed_bias_ms", "missing_signals",
        "bottleneck_class", "pid", "assigned_threads", "ahead_runway_seconds",
        "cpu_cores_used", "client_goodput_bytes_per_second", "server_goodput_bytes_per_second",
        "starvation_risk", "stalled_recovery_needed", "playback_mode", "session_state",
        "return_code", "effective_playhead_seconds", "observation_seconds",
        "diagnostics_free_inodes", "transcode_free_inodes",
    }
)

SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,256}$")
SAFE_ROUTE_PATTERN = re.compile(r"^/api/browser-playback/[A-Za-z0-9_./:-]{0,480}$")
SAFE_ROUTE_PAYLOAD_KEYS = frozenset({"normalized_route", "route_template"})
STRICT_IDENTIFIER_FIELDS = frozenset(
    {
        "event_id",
        "event_name",
        "event_source",
        "severity",
        "priority",
        "playback_session_id",
        "playback_attempt_id",
        "attachment_id",
        "epoch_id",
        "worker_id",
        "incident_id",
        "decision_id",
        "trace_id",
        "span_id",
        "parent_span_id",
        "observation_kind",
    }
)
DECIMAL_TIMESTAMP_FIELDS = frozenset(
    {
        "server_wall_time_ns",
        "server_monotonic_time_ns",
        "server_received_wall_time_ns",
        "server_received_monotonic_time_ns",
        "aligned_wall_time_ns",
        "clock_offset_ns",
        "clock_uncertainty_ns",
        "network_rtt_ns",
    }
)


class DiagnosticsPrivacyError(ValueError):
    """Raised when an event contains data outside the diagnostics contract."""


def safe_source_basename(value: object) -> str:
    raw = str(value or "")
    if "\x00" in raw:
        raise DiagnosticsPrivacyError("Source basename contains a null byte")
    basename = raw.replace("\\", "/").rsplit("/", 1)[-1]
    if basename in {"", ".", ".."}:
        raise DiagnosticsPrivacyError("Source basename is missing")
    if len(basename.encode("utf-8")) > 4_096:
        raise DiagnosticsPrivacyError("Source basename is too long")
    return basename


def basename_sha256(value: object) -> str:
    return hashlib.sha256(safe_source_basename(value).encode("utf-8")).hexdigest()


def source_fingerprint(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()


def pseudonymize_ip(value: str | None, key: bytes) -> tuple[str, str | None]:
    candidate = str(value or "").strip()
    if not candidate:
        return "unknown", None
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return "unknown", None
    if address.is_loopback:
        path_class = "loopback"
    elif address.is_private:
        path_class = "lan_or_tailnet"
    else:
        path_class = "public"
    digest = hmac.new(key, address.packed, hashlib.sha256).hexdigest()
    return path_class, f"ip_{digest}"


def normalized_route_identity(value: object) -> tuple[str, str]:
    raw = str(value or "")
    split = urlsplit(raw)
    path = split.path if split.scheme or split.netloc else raw.split("?", 1)[0].split("#", 1)[0]
    route = re.sub(r"/[0-9a-fA-F]{24,64}(?=/|$)", "/:id", path)
    route = re.sub(r"/segments/\d+\.(?:m4s|mp4)(?=/|$)", "/segments/:segment", route)
    return route[:512], hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def normalize_user_agent(value: str | None) -> dict[str, str]:
    ua = str(value or "")
    browser_family = "unknown"
    browser_version = ""
    patterns = (
        ("edge", r"Edg(?:A|iOS)?/([0-9.]+)"),
        ("chrome", r"(?:Chrome|CriOS)/([0-9.]+)"),
        ("firefox", r"(?:Firefox|FxiOS)/([0-9.]+)"),
        ("safari", r"Version/([0-9.]+).+Safari/"),
    )
    for family, pattern in patterns:
        match = re.search(pattern, ua)
        if match:
            browser_family = family
            browser_version = match.group(1)
            break
    os_family = "unknown"
    os_version = ""
    os_patterns = (
        ("ios", r"(?:iPhone )?OS ([0-9_]+)"),
        ("macos", r"Mac OS X ([0-9_]+)"),
        ("windows", r"Windows NT ([0-9.]+)"),
        ("android", r"Android ([0-9.]+)"),
        ("linux", r"Linux"),
    )
    for family, pattern in os_patterns:
        match = re.search(pattern, ua)
        if match:
            os_family = family
            os_version = match.group(1).replace("_", ".") if match.lastindex else ""
            break
    return {
        "browser_family": browser_family,
        "browser_version": browser_version,
        "os_family": os_family,
        "os_version": os_version,
    }


def contains_secret(value: object) -> bool:
    text = str(value or "")
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def _looks_like_absolute_path(value: str) -> bool:
    return any(pattern.search(value) for pattern in ABSOLUTE_PATH_PATTERNS)


def _sanitize_scalar(key: str, value: Any) -> Any:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        if key == "source_original_filename":
            return safe_source_basename(value)
        if key in SAFE_ROUTE_PAYLOAD_KEYS:
            if (
                not SAFE_ROUTE_PATTERN.fullmatch(value)
                or ".." in value
                or "?" in value
                or "#" in value
            ):
                raise DiagnosticsPrivacyError(f"Invalid normalized playback route for {key}")
            return value
        if contains_secret(value):
            raise DiagnosticsPrivacyError(f"Secret-like value rejected for {key}")
        if _looks_like_absolute_path(value):
            raise DiagnosticsPrivacyError(f"Absolute path rejected for {key}")
        if "://" in value:
            raise DiagnosticsPrivacyError(f"Full URL rejected for {key}")
        return value[:4_096]
    raise DiagnosticsPrivacyError(f"Unsupported diagnostics value for {key}")


def sanitize_payload(payload: object, *, depth: int = 0) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DiagnosticsPrivacyError("Diagnostics payload must be an object")
    if depth > 4:
        raise DiagnosticsPrivacyError("Diagnostics payload is too deeply nested")
    sanitized: dict[str, Any] = {}
    for raw_key, value in payload.items():
        key = str(raw_key)
        if key not in SAFE_PAYLOAD_KEYS:
            continue
        if isinstance(value, dict):
            sanitized[key] = sanitize_payload(value, depth=depth + 1)
        elif isinstance(value, list):
            if len(value) > 512:
                value = value[:512]
            items: list[Any] = []
            for item in value:
                if isinstance(item, dict):
                    items.append(sanitize_payload(item, depth=depth + 1))
                elif isinstance(item, list):
                    items.append([_sanitize_scalar(key, nested) for nested in item[:16]])
                else:
                    items.append(_sanitize_scalar(key, item))
            sanitized[key] = items
        else:
            sanitized[key] = _sanitize_scalar(key, value)
    return sanitized


def sanitize_event(event: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key in EVENT_ENVELOPE_FIELDS:
        if key not in event:
            continue
        value = event[key]
        if key == "payload":
            sanitized[key] = sanitize_payload(value)
            continue
        if key in DECIMAL_TIMESTAMP_FIELDS:
            if value is not None:
                text = str(value)
                if not text.lstrip("-").isdigit():
                    raise DiagnosticsPrivacyError(f"{key} must be a decimal string")
                sanitized[key] = text
            else:
                sanitized[key] = None
            continue
        if isinstance(value, str):
            if contains_secret(value):
                raise DiagnosticsPrivacyError(f"Secret-like envelope value rejected for {key}")
            if _looks_like_absolute_path(value):
                raise DiagnosticsPrivacyError(f"Absolute path rejected for envelope field {key}")
            if "://" in value:
                raise DiagnosticsPrivacyError(f"Full URL rejected for envelope field {key}")
            if key in STRICT_IDENTIFIER_FIELDS and not SAFE_IDENTIFIER_PATTERN.fullmatch(value):
                raise DiagnosticsPrivacyError(f"Invalid diagnostics identifier for {key}")
        sanitized[key] = value
    return sanitized


def assert_no_forbidden_diagnostics_text(value: object) -> None:
    text = str(value or "")
    if contains_secret(text):
        raise DiagnosticsPrivacyError("Diagnostics output contains a secret-like value")
    for line in text.splitlines():
        if _looks_like_absolute_path(line.strip()):
            raise DiagnosticsPrivacyError("Diagnostics output contains an absolute path")
