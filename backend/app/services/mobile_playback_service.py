from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import statistics
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..config import Settings
from ..db import get_connection, utcnow_iso
from ..media_stream import ensure_media_path_within_root
from .cloud_library_service import ensure_cloud_media_item_provider_access
from .mobile_playback_models import (
    BACKGROUND_EXPANSION_FORWARD_SECONDS,
    FRONTIER_WAIT_SECONDS,
    MANIFEST_ADVANCE_MIN_GROWTH_SECONDS,
    MANIFEST_ADVANCE_TRIGGER_SECONDS,
    MOBILE_PROFILES,
    PLAYBACK_COMMIT_RUNWAY_SECONDS,
    READY_AFTER_TARGET_SECONDS,
    ROUTE2_ATTACH_ACK_WARN_SECONDS,
    ROUTE2_ATTACH_READY_SECONDS,
    ROUTE2_DRAIN_IDLE_GRACE_SECONDS,
    ROUTE2_DRAIN_MAX_SECONDS,
    ROUTE2_ETA_DISPLAY_MAX_VOLATILITY_RATIO,
    ROUTE2_ETA_DISPLAY_MIN_GROWTH_EVENTS,
    ROUTE2_ETA_DISPLAY_MIN_OBSERVATION_SECONDS,
    ROUTE2_ETA_DISPLAY_STICKY_OBSERVATION_SECONDS,
    ROUTE2_FULL_GOODPUT_MIN_SAMPLE_COUNT,
    ROUTE2_FULL_RESERVE_BASE_SECONDS,
    ROUTE2_FULL_RESERVE_MAX_UNCERTAINTY_SECONDS,
    ROUTE2_FULL_RESERVE_MAX_VOLATILITY_SECONDS,
    ROUTE2_FULL_VOLATILITY_HORIZON_SECONDS,
    ROUTE2_RECOVERY_MIN_RUNWAY_SECONDS,
    ROUTE2_RECOVERY_MIN_SUPPLY_RATE_X,
    ROUTE2_RECOVERY_PROJECTION_HORIZON_SECONDS,
    ROUTE2_RECOVERY_RESUME_RUNWAY_SECONDS,
    ROUTE2_REPLACEMENT_RETRY_BACKOFF_SECONDS,
    ROUTE2_STARTUP_MIN_RUNWAY_SECONDS,
    ROUTE2_STARTUP_MIN_SUPPLY_RATE_X,
    ROUTE2_STARTUP_PROJECTION_HORIZON_SECONDS,
    ROUTE2_SUPPLY_RATE_FAST_EMA_ALPHA,
    ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS,
    ROUTE2_SUPPLY_RATE_SLOW_EMA_ALPHA,
    SEEK_PREROLL_SECONDS,
    SEGMENT_DURATION_SECONDS,
    STATUS_POLL_PREPARE_SECONDS,
    TARGET_WINDOW_FORWARD_SECONDS,
    TARGET_WINDOW_PREROLL_SECONDS,
    WATCH_LOW_WATERMARK_SECONDS,
    WATCH_REFILL_TARGET_SECONDS,
    WATCH_STALLED_RECOVERY_RUNWAY_SECONDS,
    BrowserPlaybackSession,
    CacheState,
    MobileClusterJob,
    MobilePlaybackSession,
    PlaybackEpoch,
    Route2WorkerRecord,
)
from .mobile_playback_route2_metrics import (
    _route2_client_goodput_locked as _route2_client_goodput_locked_impl,
    _route2_effective_playhead_seconds_locked as _route2_effective_playhead_seconds_locked_impl,
    _route2_position_in_epoch_locked as _route2_position_in_epoch_locked_impl,
    _route2_recovery_target_locked as _route2_recovery_target_locked_impl,
    _route2_runtime_supply_metrics_locked as _route2_runtime_supply_metrics_locked_impl,
    _route2_server_byte_goodput_locked as _route2_server_byte_goodput_locked_impl,
    _route2_supply_model_locked as _route2_supply_model_locked_impl,
    _route2_supply_rate_x_locked as _route2_supply_rate_x_locked_impl,
)
from .mobile_playback_route2_samples import (
    _record_route2_byte_sample_locked as _record_route2_byte_sample_locked_impl,
    _record_route2_client_probe_sample_locked as _record_route2_client_probe_sample_locked_impl,
    _record_route2_frontier_sample_locked as _record_route2_frontier_sample_locked_impl,
    _route2_epoch_ready_end_seconds as _route2_epoch_ready_end_seconds_impl,
)
from .mobile_playback_route2_readiness import (
    _ahead_runway_seconds as _ahead_runway_seconds_impl,
    _playback_commit_is_ready as _playback_commit_is_ready_impl,
    _stalled_recovery_needed as _stalled_recovery_needed_impl,
    _starvation_risk as _starvation_risk_impl,
    _target_is_ready as _target_is_ready_impl,
    _watch_anchor_position as _watch_anchor_position_impl,
)
from .mobile_playback_route2_full_helpers import (
    _parse_bitrate_bps as _parse_bitrate_bps_impl,
    _route2_full_bootstrap_eta_locked as _route2_full_bootstrap_eta_locked_impl,
    _route2_full_budget_metrics_locked as _route2_full_budget_metrics_locked_impl,
    _route2_full_mode_requires_initial_attach_gate_locked as _route2_full_mode_requires_initial_attach_gate_locked_impl,
    _route2_full_prepare_elapsed_seconds_locked as _route2_full_prepare_elapsed_seconds_locked_impl,
    _route2_full_safe_calibration_ratio_locked as _route2_full_safe_calibration_ratio_locked_impl,
    _route2_profile_floor_bytes_per_second as _route2_profile_floor_bytes_per_second_impl,
    _route2_profile_floor_segment_bytes as _route2_profile_floor_segment_bytes_impl,
)
from .mobile_playback_route2_full_gate import (
    _route2_display_prepare_eta_locked as _route2_display_prepare_eta_locked_impl,
    _route2_full_mode_gate_locked as _route2_full_mode_gate_locked_impl,
)
from .mobile_playback_route2_gates import (
    _route2_attach_gate_state_locked as _route2_attach_gate_state_locked_impl,
    _route2_epoch_recovery_ready_locked as _route2_epoch_recovery_ready_locked_impl,
    _route2_epoch_startup_attach_gate_locked as _route2_epoch_startup_attach_gate_locked_impl,
)
from .mobile_playback_route2_recovery import (
    _route2_low_water_recovery_needed_locked as _route2_low_water_recovery_needed_locked_impl,
)
from .mobile_playback_route2_snapshot import (
    _route2_snapshot_locked as _route2_snapshot_locked_impl,
)
from .mobile_playback_route2_epoch_access import (
    _cleanup_route2_draining_epochs_locked as _cleanup_route2_draining_epochs_locked_impl,
    _prepare_route2_epoch_access_locked as _prepare_route2_epoch_access_locked_impl,
    _route2_epoch_is_draining_expired_locked as _route2_epoch_is_draining_expired_locked_impl,
)
from .mobile_playback_route2_epoch_artifacts import (
    _contiguous_segment_frontier as _contiguous_segment_frontier_impl,
    _rebuild_route2_published_frontier_locked as _rebuild_route2_published_frontier_locked_impl,
    _route2_segment_destination as _route2_segment_destination_impl,
    _write_json_atomic as _write_json_atomic_impl,
    _write_route2_epoch_metadata_locked as _write_route2_epoch_metadata_locked_impl,
    _write_route2_frontier_locked as _write_route2_frontier_locked_impl,
)
from .mobile_playback_route2_epoch_lifecycle import (
    _build_route2_epoch_locked as _build_route2_epoch_locked_impl,
    _discard_route2_epoch_locked as _discard_route2_epoch_locked_impl,
    _ensure_route2_epoch_workspace_locked as _ensure_route2_epoch_workspace_locked_impl,
    _initialize_route2_session_locked as _initialize_route2_session_locked_impl,
    _terminate_route2_epoch_locked as _terminate_route2_epoch_locked_impl,
)
from .mobile_playback_route2_epoch_publication import (
    _publish_route2_epoch_outputs_locked as _publish_route2_epoch_outputs_locked_impl,
    _route2_publish_init_locked as _route2_publish_init_locked_impl,
    _route2_publish_segment_locked as _route2_publish_segment_locked_impl,
)
from .mobile_playback_route2_preflight_service import (
    _build_route2_full_source_bin_bytes as _build_route2_full_source_bin_bytes_impl,
    _ensure_route2_full_preflight_locked as _ensure_route2_full_preflight_locked_impl,
    _load_route2_full_preflight_cache_locked as _load_route2_full_preflight_cache_locked_impl,
    _route2_full_preflight_cache_path as _route2_full_preflight_cache_path_impl,
    _route2_full_preflight_source_input as _route2_full_preflight_source_input_impl,
    _route2_full_scan_packet_bins as _route2_full_scan_packet_bins_impl,
    _run_route2_full_preflight_worker as _run_route2_full_preflight_worker_impl,
)
from .mobile_playback_route2_math import (
    _conservative_goodput_locked as _conservative_goodput_locked_impl,
    _ema_locked as _ema_locked_impl,
    _harmonic_mean_locked as _harmonic_mean_locked_impl,
    _percentile_locked as _percentile_locked_impl,
    _route2_projected_runway_seconds_locked as _route2_projected_runway_seconds_locked_impl,
    _route2_required_runway_seconds_locked as _route2_required_runway_seconds_locked_impl,
)
from .mobile_playback_source_service import (
    _probe_worker_source_input_error as _probe_worker_source_input_error_impl,
    _resolve_duration_seconds as _resolve_duration_seconds_impl,
    _resolve_worker_source_input as _resolve_worker_source_input_impl,
)
from .library_service import get_media_item_detail, get_media_item_record
from .media_technical_metadata_service import resolve_trusted_technical_metadata
from .route2_ffmpeg_command_adapter import (
    Route2FFmpegCommandAdapterInput,
    build_route2_ffmpeg_command_preview,
)
from .route2_adaptive_controller import (
    Route2AdaptiveShadowInput,
    classify_route2_adaptive_shadow,
)
from .route2_transcode_strategy import (
    Route2TranscodeStrategyInput,
    select_route2_transcode_strategy,
)
from .route2_shared_output_store import (
    SHARED_OUTPUT_STORE_BLOCKERS,
    absolute_segment_end_index_exclusive_from_seconds,
    absolute_segment_index_from_seconds,
    build_route2_init_metadata,
    build_shared_output_contract_metadata,
    build_shared_output_metadata,
    build_shared_output_store_capability,
    build_shared_store_write_plan,
    count_shared_output_init_records,
    count_shared_output_metadata_records,
    count_shared_output_ranges_media_bytes_present_records,
    count_shared_output_segment_records,
    write_shared_output_init_media,
    write_shared_output_segment_media,
    write_shared_output_store_metadata,
)


logger = logging.getLogger(__name__)
ROUTE2_TELEMETRY_PROCESS_ATTACH_GRACE_SECONDS = 5.0
ROUTE2_RESOURCE_TELEMETRY_INTERVAL_SECONDS = 1.0
ROUTE2_RESOURCE_SNAPSHOT_STALE_SECONDS = 5.0
ADMIN_TERMINATED_BROWSER_PLAYBACK_COOLDOWN_SECONDS = 30.0
SAME_USER_ACTIVE_PLAYBACK_LIMIT_CODE = "same_user_active_playback_limit"
SERVER_MAX_CAPACITY_CODE = "server_max_capacity"
ACTIVE_WORKER_CONFLICT_CODE = "active_playback_worker_exists"
STANDARD_USER_ROLE = "standard_user"
ADMIN_USER_ROLE = "admin"
ROUTE2_ACTIVE_SUPPLY_HEALTHY_RATE_X = 1.05
ROUTE2_ACTIVE_SUPPLY_LOW_RATE_X = 1.0
ROUTE2_ACTIVE_SUPPLY_STRONGLY_LOW_RATE_X = 0.95
ROUTE2_RUNTIME_DONOR_SUPPLY_RATE_X = 1.2
ROUTE2_CLOSED_LOOP_HEALTH_FLOOR_RATE_X = 1.05
ROUTE2_CLOSED_LOOP_DOWNSHIFT_RATE_X = 1.10
ROUTE2_CLOSED_LOOP_DONOR_RATE_X = 1.50
ROUTE2_FULL_BAD_CONDITION_RESERVE_SECONDS = 1800.0
ROUTE2_BAD_CONDITION_SUPPLY_FLOOR_RATE_X = ROUTE2_STARTUP_MIN_SUPPLY_RATE_X
ROUTE2_BAD_CONDITION_STRONG_SUPPLY_RATE_X = 1.0
ROUTE2_OUTPUT_CONTRACT_VERSION = "route2-output-contract-v1"
ROUTE2_SHARED_SUPPLY_GROUP_VERSION = "route2-shared-supply-group-v2"


@dataclass(slots=True)
class _HostCpuJiffySample:
    total_jiffies: int
    idle_jiffies: int
    total_cpu_cores: int
    sample_monotonic: float


@dataclass(slots=True)
class _HostCpuPressureSnapshot:
    host_cpu_total_cores: int | None
    host_cpu_used_cores: float | None
    host_cpu_used_percent: float | None
    external_cpu_cores_used_estimate: float | None
    external_cpu_percent_estimate: float | None
    external_ffmpeg_process_count: int
    external_ffmpeg_cpu_cores_estimate: float | None
    host_cpu_sample_mature: bool
    route2_worker_ffmpeg_process_count: int = 0
    elvern_owned_ffmpeg_process_count: int = 0
    elvern_owned_ffmpeg_cpu_cores_estimate: float | None = None
    external_pressure_reason: str | None = None


@dataclass(slots=True)
class _FfmpegProcessClassification:
    route2_worker_process_count: int = 0
    elvern_owned_process_count: int = 0
    external_process_count: int = 0
    route2_worker_pids: set[int] = field(default_factory=set)
    elvern_owned_pids: set[int] = field(default_factory=set)
    external_pids: set[int] = field(default_factory=set)


@dataclass(slots=True)
class _Route2WorkerTelemetryReadTarget:
    worker_id: str
    pid: int


@dataclass(slots=True)
class _Route2WorkerTelemetryReadResult:
    worker_id: str
    pid: int
    cpu_seconds: float | None
    memory_bytes: int | None
    io_read_bytes: int | None = None
    io_write_bytes: int | None = None


@dataclass(slots=True)
class _Route2WorkerDisplayStatus:
    status: str
    label: str
    tone: str
    reason: str
    priority: int


@dataclass(slots=True)
class _Route2FfmpegProgressSnapshot:
    out_time_seconds: float | None = None
    speed_x: float | None = None
    fps: float | None = None
    frame: int | None = None
    progress_state: str = "unknown"
    updated_at_ts: float | None = None
    stale: bool = True
    missing_metrics: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _LinuxPressureSnapshot:
    sample_available: bool
    missing_metrics: list[str] = field(default_factory=list)
    cpu_some_avg10: float | None = None
    cpu_full_avg10: float | None = None
    io_some_avg10: float | None = None
    io_full_avg10: float | None = None
    memory_some_avg10: float | None = None
    memory_full_avg10: float | None = None


@dataclass(slots=True)
class _CgroupTelemetrySnapshot:
    pressure_available: bool
    missing_metrics: list[str] = field(default_factory=list)
    cpu_nr_periods: int | None = None
    cpu_nr_throttled: int | None = None
    cpu_throttled_usec: int | None = None
    cpu_throttled_delta: int | None = None
    cpu_throttled_usec_delta: int | None = None
    cpu_some_avg10: float | None = None
    cpu_full_avg10: float | None = None
    io_some_avg10: float | None = None
    io_full_avg10: float | None = None
    memory_some_avg10: float | None = None
    memory_full_avg10: float | None = None


@dataclass(slots=True)
class _Route2ResourceSnapshot:
    sampled_at_ts: float
    sampled_at: str
    sample_mature: bool
    sample_stale: bool
    host_cpu_total_cores: int | None
    host_cpu_used_cores: float | None
    host_cpu_used_percent: float | None
    route2_cpu_cores_used_total: float | None
    route2_cpu_percent_of_host: float | None
    per_user_cpu_cores_used_total: dict[int, float]
    total_memory_bytes: int | None
    route2_memory_bytes_total: int | None
    route2_memory_percent_of_total: float | None
    external_cpu_cores_used_estimate: float | None
    external_cpu_percent_estimate: float | None
    external_ffmpeg_process_count: int
    external_ffmpeg_cpu_cores_estimate: float | None
    external_pressure_level: str
    missing_metrics: list[str]
    route2_worker_ffmpeg_process_count: int = 0
    elvern_owned_ffmpeg_process_count: int = 0
    elvern_owned_ffmpeg_cpu_cores_estimate: float | None = None
    external_pressure_reason: str | None = None


@dataclass(slots=True)
class _Route2AdaptiveSpawnDryRunDecision:
    recommended_threads: int
    reason: str
    blockers: list[str]
    policy: str
    sample_age_seconds: float | None
    sample_mature: bool


@dataclass(slots=True)
class _Route2RealThreadAssignmentDecision:
    assigned_threads: int
    assignment_policy: str
    assignment_reason: str
    assignment_blockers: list[str]
    adaptive_control_enabled: bool
    adaptive_control_applied: bool
    assigned_threads_source: str
    fallback_used: bool


@dataclass(slots=True)
class _Route2SourceFeedRate:
    rate_x: float | None
    available: bool
    mature: bool
    reason: str | None
    missing_reason: str | None
    missing_metrics: list[str]


@dataclass(slots=True)
class _Route2LimitingFactorDecision:
    primary: str
    confidence: float
    scores: dict[str, float]
    supporting_signals: list[str]
    blocking_signals: list[str]
    missing_metrics: list[str]
    published_rate_x: float | None
    encoder_rate_x: float | None
    source_feed_rate_x: float | None
    source_feed_rate_available: bool
    source_feed_rate_mature: bool
    source_feed_rate_reason: str | None
    source_feed_rate_missing_reason: str | None
    publish_efficiency_gap: float | None
    client_delivery_rate_x: float | None


@dataclass(slots=True)
class _Route2ActivePlaybackHealth:
    status: str
    reason: str
    admission_blocking: bool
    worker_id: str | None
    session_id: str | None
    supply_rate_x: float | None
    supply_observation_seconds: float | None
    runway_seconds: float | None
    assigned_threads: int | None
    cpu_thread_limited: bool
    runtime_rebalance_role: str
    runtime_rebalance_reason: str
    runtime_rebalance_target_threads: int | None = None
    runtime_rebalance_can_donate_threads: int = 0
    runtime_rebalance_priority: int = 0


@dataclass(slots=True)
class _Route2ClosedLoopDryRunDecision:
    role: str
    reasons: list[str]
    confidence: float
    prepare_boost_needed: bool
    prepare_boost_target_threads: int | None
    downshift_candidate: bool
    downshift_target_threads: int | None
    needs_resource: bool
    needs_resource_reason: str | None
    donor_candidate: bool
    theoretical_donate_threads: int
    protected_reason: str | None
    admission_should_block_new_users: bool
    admission_block_reason: str | None
    admission_block_reasons: list[str]
    boost_blocked: bool
    boost_blockers: list[str]
    boost_warning_reasons: list[str]
    limiting_factor: _Route2LimitingFactorDecision
    primary_bottleneck: str
    donor_score: float = 0.0


@dataclass(slots=True)
class _Route2SharedSupplyWorkload:
    worker_id: str
    workload_id: str
    session_id: str
    epoch_id: str
    user_id: int
    media_item_id: int
    source_fingerprint: str
    source_kind: str
    profile: str
    playback_mode: str
    output_contract_fingerprint: str | None
    output_contract_version: str
    output_contract_missing_fields: list[str]
    output_contract_summary: dict[str, object]
    init_metadata: dict[str, object]
    group_key: str | None
    permission_status: str
    blockers: list[str]
    notes: list[str]
    epoch_start_seconds: float | None
    target_position_seconds: float
    prepared_ranges: list[list[float]]
    stopped_or_expired: bool


class ActivePlaybackWorkerConflictError(Exception):
    def __init__(self, detail: dict[str, object]) -> None:
        self.detail = dict(detail)
        super().__init__(str(self.detail.get("message") or "An active playback worker already exists"))


class PlaybackAdmissionError(Exception):
    def __init__(self, detail: dict[str, object]) -> None:
        self.detail = dict(detail)
        super().__init__(str(self.detail.get("message") or "Playback admission failed"))


class PlaybackWorkerCooldownError(Exception):
    def __init__(self, detail: dict[str, object]) -> None:
        self.detail = dict(detail)
        super().__init__(str(self.detail.get("message") or "Playback is temporarily unavailable for this movie"))


def _read_text_tail(path: Path, *, max_lines: int = 100) -> str | None:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = [line.rstrip() for line in content.splitlines() if line.strip()]
    if not lines:
        return None
    return "\n".join(lines[-max_lines:])


def _parse_ffmpeg_progress_time_seconds(value: str | None) -> float | None:
    normalized = str(value or "").strip()
    if not normalized or normalized.upper() == "N/A":
        return None
    try:
        if ":" not in normalized:
            return max(0.0, float(normalized) / 1_000_000.0)
        hours_text, minutes_text, seconds_text = normalized.split(":", 2)
        return max(0.0, (int(hours_text) * 3600) + (int(minutes_text) * 60) + float(seconds_text))
    except (TypeError, ValueError):
        return None


def _parse_ffmpeg_progress_speed_x(value: str | None) -> float | None:
    normalized = str(value or "").strip().lower()
    if not normalized or normalized == "n/a":
        return None
    if normalized.endswith("x"):
        normalized = normalized[:-1]
    try:
        return max(0.0, float(normalized))
    except ValueError:
        return None


def _parse_ffmpeg_progress_payload(
    payload: str,
    *,
    updated_at_ts: float | None = None,
    now_ts: float | None = None,
    stale_after_seconds: float = 5.0,
) -> _Route2FfmpegProgressSnapshot:
    values: dict[str, str] = {}
    for raw_line in str(payload or "").splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    missing: list[str] = []
    out_time_value = values.get("out_time_us") or values.get("out_time_ms") or values.get("out_time")
    out_time_seconds = _parse_ffmpeg_progress_time_seconds(out_time_value)
    if out_time_seconds is None:
        missing.append("ffmpeg_progress_out_time")
    speed_x = _parse_ffmpeg_progress_speed_x(values.get("speed"))
    if speed_x is None:
        missing.append("ffmpeg_progress_speed")
    fps = None
    try:
        fps = max(0.0, float(values["fps"])) if "fps" in values else None
    except ValueError:
        fps = None
    if fps is None:
        missing.append("ffmpeg_progress_fps")
    frame = None
    try:
        frame = max(0, int(float(values["frame"]))) if "frame" in values else None
    except ValueError:
        frame = None
    if frame is None:
        missing.append("ffmpeg_progress_frame")
    progress_state = values.get("progress") or "unknown"
    now_value = time.time() if now_ts is None else now_ts
    stale = True
    if updated_at_ts is not None:
        stale = bool(progress_state != "end" and now_value - updated_at_ts > stale_after_seconds)
    return _Route2FfmpegProgressSnapshot(
        out_time_seconds=out_time_seconds,
        speed_x=speed_x,
        fps=fps,
        frame=frame,
        progress_state=progress_state,
        updated_at_ts=updated_at_ts,
        stale=stale,
        missing_metrics=missing,
    )


def _read_ffmpeg_progress_snapshot(
    progress_path: Path,
    *,
    now_ts: float | None = None,
    stale_after_seconds: float = 5.0,
) -> _Route2FfmpegProgressSnapshot:
    try:
        payload = progress_path.read_text(encoding="utf-8", errors="replace")
        updated_at_ts = progress_path.stat().st_mtime
    except OSError:
        return _Route2FfmpegProgressSnapshot(
            progress_state="unknown",
            stale=True,
            missing_metrics=["ffmpeg_progress_file"],
        )
    return _parse_ffmpeg_progress_payload(
        payload,
        updated_at_ts=updated_at_ts,
        now_ts=now_ts,
        stale_after_seconds=stale_after_seconds,
    )


def _detect_total_cpu_cores() -> int:
    return max(1, os.cpu_count() or 1)


def _route2_cpu_upbound_cores_for_total(total_cpu_cores: int, upbound_percent: int) -> int:
    return max(1, math.floor((max(1, total_cpu_cores) * upbound_percent) / 100))


def _route2_display_profile_label(profile: str | None) -> str:
    normalized = str(profile or "").strip()
    if not normalized:
        return "profile unknown"
    label = normalized
    for prefix in ("mobile_", "mobile-"):
        if label.lower().startswith(prefix):
            label = label[len(prefix) :]
            break
    if label.lower().endswith("p") and label[:-1].isdigit():
        return label.lower()
    return label.replace("_", " ").replace("-", " ")


def _clock_ticks_per_second() -> int:
    try:
        return max(1, int(os.sysconf("SC_CLK_TCK")))
    except (AttributeError, ValueError, OSError):
        return 100


def _page_size_bytes() -> int:
    try:
        return max(1, int(os.sysconf("SC_PAGE_SIZE")))
    except (AttributeError, ValueError, OSError):
        return 4096


def _parse_proc_stat_cpu_seconds(payload: str) -> float | None:
    normalized = str(payload or "").strip()
    if not normalized:
        return None
    close_index = normalized.rfind(")")
    if close_index < 0 or close_index + 2 >= len(normalized):
        return None
    tail = normalized[close_index + 2 :].split()
    if len(tail) <= 12:
        return None
    try:
        utime_ticks = int(tail[11])
        stime_ticks = int(tail[12])
    except ValueError:
        return None
    return (utime_ticks + stime_ticks) / _clock_ticks_per_second()


def _parse_proc_stat_host_cpu_jiffies(payload: str) -> tuple[int, int] | None:
    for raw_line in str(payload or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("cpu "):
            continue
        fields = line.split()[1:]
        if len(fields) < 4:
            return None
        try:
            values = [max(0, int(value)) for value in fields]
        except ValueError:
            return None
        total_jiffies = sum(values)
        idle_jiffies = values[3] + (values[4] if len(values) > 4 else 0)
        return total_jiffies, idle_jiffies
    return None


def _read_host_cpu_jiffy_sample(*, sample_monotonic: float) -> _HostCpuJiffySample | None:
    stat_path = Path("/proc/stat")
    try:
        payload = stat_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    parsed = _parse_proc_stat_host_cpu_jiffies(payload)
    if parsed is None:
        return None
    total_jiffies, idle_jiffies = parsed
    return _HostCpuJiffySample(
        total_jiffies=total_jiffies,
        idle_jiffies=idle_jiffies,
        total_cpu_cores=_detect_total_cpu_cores(),
        sample_monotonic=sample_monotonic,
    )


def _proc_comm_is_ffmpeg_like(comm: str | None) -> bool:
    normalized = str(comm or "").strip().lower()
    return normalized in {"ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe"}


def _parse_proc_stat_parent_pid(payload: str) -> int | None:
    normalized = str(payload or "").strip()
    if not normalized:
        return None
    close_index = normalized.rfind(")")
    if close_index < 0 or close_index + 2 >= len(normalized):
        return None
    tail = normalized[close_index + 2 :].split()
    if len(tail) < 2:
        return None
    try:
        return int(tail[1])
    except ValueError:
        return None


def _read_proc_parent_pid(proc_root: Path, pid: int) -> int | None:
    try:
        payload = (proc_root / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return _parse_proc_stat_parent_pid(payload)


def _proc_pid_has_ancestor(
    *,
    proc_root: Path,
    pid: int,
    ancestor_pid: int | None,
    max_depth: int = 8,
) -> bool:
    if ancestor_pid is None or ancestor_pid <= 0:
        return False
    current_pid = int(pid)
    visited: set[int] = set()
    for _ in range(max(1, int(max_depth))):
        if current_pid in visited or current_pid <= 1:
            return False
        visited.add(current_pid)
        parent_pid = _read_proc_parent_pid(proc_root, current_pid)
        if parent_pid is None or parent_pid <= 0:
            return False
        if parent_pid == ancestor_pid:
            return True
        current_pid = parent_pid
    return False


def _classify_ffmpeg_processes(
    *,
    proc_root: Path = Path("/proc"),
    owned_route2_pids: set[int] | None = None,
    backend_pid: int | None = None,
) -> _FfmpegProcessClassification:
    owned_pids = {int(pid) for pid in (owned_route2_pids or set()) if int(pid) > 0}
    resolved_backend_pid = os.getpid() if backend_pid is None else int(backend_pid)
    classification = _FfmpegProcessClassification()
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return classification
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not _proc_comm_is_ffmpeg_like(comm):
            continue
        if pid in owned_pids:
            classification.route2_worker_process_count += 1
            classification.route2_worker_pids.add(pid)
        elif _proc_pid_has_ancestor(
            proc_root=proc_root,
            pid=pid,
            ancestor_pid=resolved_backend_pid,
        ):
            classification.elvern_owned_process_count += 1
            classification.elvern_owned_pids.add(pid)
        else:
            classification.external_process_count += 1
            classification.external_pids.add(pid)
    return classification


def _count_external_ffmpeg_processes(*, proc_root: Path = Path("/proc"), owned_route2_pids: set[int] | None = None) -> int:
    return _classify_ffmpeg_processes(
        proc_root=proc_root,
        owned_route2_pids=owned_route2_pids,
    ).external_process_count


def _read_process_cpu_seconds_for_pids(pids: set[int]) -> dict[int, float]:
    readings: dict[int, float] = {}
    for pid in sorted({int(pid) for pid in pids if int(pid) > 0}):
        cpu_seconds = _read_process_cpu_seconds(pid)
        if cpu_seconds is not None:
            readings[pid] = cpu_seconds
    return readings


def _build_host_cpu_pressure_snapshot(
    *,
    previous_sample: _HostCpuJiffySample | None,
    current_sample: _HostCpuJiffySample | None,
    route2_cpu_cores_used_total: float | None,
    external_ffmpeg_process_count: int,
    route2_worker_ffmpeg_process_count: int = 0,
    elvern_owned_ffmpeg_process_count: int = 0,
    elvern_owned_ffmpeg_cpu_cores_estimate: float | None = None,
) -> _HostCpuPressureSnapshot:
    if current_sample is None or previous_sample is None:
        return _HostCpuPressureSnapshot(
            host_cpu_total_cores=current_sample.total_cpu_cores if current_sample is not None else None,
            host_cpu_used_cores=None,
            host_cpu_used_percent=None,
            external_cpu_cores_used_estimate=None,
            external_cpu_percent_estimate=None,
            external_ffmpeg_process_count=external_ffmpeg_process_count,
            external_ffmpeg_cpu_cores_estimate=None,
            host_cpu_sample_mature=False,
            route2_worker_ffmpeg_process_count=route2_worker_ffmpeg_process_count,
            elvern_owned_ffmpeg_process_count=elvern_owned_ffmpeg_process_count,
            elvern_owned_ffmpeg_cpu_cores_estimate=elvern_owned_ffmpeg_cpu_cores_estimate,
            external_pressure_reason="host_cpu_sample_immature",
        )

    delta_total_jiffies = current_sample.total_jiffies - previous_sample.total_jiffies
    delta_idle_jiffies = current_sample.idle_jiffies - previous_sample.idle_jiffies
    delta_wall_seconds = current_sample.sample_monotonic - previous_sample.sample_monotonic
    if delta_total_jiffies <= 0 or delta_idle_jiffies < 0 or delta_wall_seconds <= 0:
        return _HostCpuPressureSnapshot(
            host_cpu_total_cores=current_sample.total_cpu_cores,
            host_cpu_used_cores=None,
            host_cpu_used_percent=None,
            external_cpu_cores_used_estimate=None,
            external_cpu_percent_estimate=None,
            external_ffmpeg_process_count=external_ffmpeg_process_count,
            external_ffmpeg_cpu_cores_estimate=None,
            host_cpu_sample_mature=False,
            route2_worker_ffmpeg_process_count=route2_worker_ffmpeg_process_count,
            elvern_owned_ffmpeg_process_count=elvern_owned_ffmpeg_process_count,
            elvern_owned_ffmpeg_cpu_cores_estimate=elvern_owned_ffmpeg_cpu_cores_estimate,
            external_pressure_reason="host_cpu_sample_immature",
        )

    used_jiffies = max(0, delta_total_jiffies - delta_idle_jiffies)
    used_seconds = used_jiffies / _clock_ticks_per_second()
    total_cores = max(float(current_sample.total_cpu_cores), 1.0)
    host_cpu_used_cores = min(total_cores, max(0.0, used_seconds / delta_wall_seconds))
    host_cpu_used_percent = host_cpu_used_cores / total_cores
    external_cpu_cores_used_estimate = None
    external_cpu_percent_estimate = None
    if route2_cpu_cores_used_total is not None:
        elvern_helper_cores = (
            max(0.0, float(elvern_owned_ffmpeg_cpu_cores_estimate))
            if elvern_owned_ffmpeg_cpu_cores_estimate is not None
            else 0.0
        )
        elvern_cpu_cores_used_total = float(route2_cpu_cores_used_total) + elvern_helper_cores
        external_cpu_cores_used_estimate = max(0.0, host_cpu_used_cores - elvern_cpu_cores_used_total)
        external_cpu_percent_estimate = external_cpu_cores_used_estimate / total_cores

    return _HostCpuPressureSnapshot(
        host_cpu_total_cores=current_sample.total_cpu_cores,
        host_cpu_used_cores=host_cpu_used_cores,
        host_cpu_used_percent=host_cpu_used_percent,
        external_cpu_cores_used_estimate=external_cpu_cores_used_estimate,
        external_cpu_percent_estimate=external_cpu_percent_estimate,
        external_ffmpeg_process_count=external_ffmpeg_process_count,
        external_ffmpeg_cpu_cores_estimate=None,
        host_cpu_sample_mature=True,
        route2_worker_ffmpeg_process_count=route2_worker_ffmpeg_process_count,
        elvern_owned_ffmpeg_process_count=elvern_owned_ffmpeg_process_count,
        elvern_owned_ffmpeg_cpu_cores_estimate=elvern_owned_ffmpeg_cpu_cores_estimate,
        external_pressure_reason=None,
    )


def _classify_external_pressure(host_cpu_pressure: _HostCpuPressureSnapshot) -> tuple[str, str]:
    if not host_cpu_pressure.host_cpu_sample_mature:
        return "unknown", "host_cpu_sample_immature"
    external_cores = host_cpu_pressure.external_cpu_cores_used_estimate
    external_percent = host_cpu_pressure.external_cpu_percent_estimate
    if external_cores is None or external_percent is None:
        return "unknown", "external_cpu_estimate_missing"
    if (
        external_cores >= 4.0
        or external_percent >= 0.20
    ):
        return "high", "external_cpu_high"
    if (
        host_cpu_pressure.external_ffmpeg_process_count > 0
        or external_cores >= 3.0
        or external_percent >= 0.15
    ):
        if host_cpu_pressure.external_ffmpeg_process_count > 0:
            return "moderate", "external_ffmpeg_detected"
        return "moderate", "external_cpu_moderate"
    return "none", "none"


def _classify_external_pressure_level(host_cpu_pressure: _HostCpuPressureSnapshot) -> str:
    level, _reason = _classify_external_pressure(host_cpu_pressure)
    return level


def _host_cpu_pressure_from_resource_snapshot(snapshot: _Route2ResourceSnapshot | None) -> _HostCpuPressureSnapshot:
    if snapshot is None:
        return _HostCpuPressureSnapshot(
            host_cpu_total_cores=None,
            host_cpu_used_cores=None,
            host_cpu_used_percent=None,
            external_cpu_cores_used_estimate=None,
            external_cpu_percent_estimate=None,
            external_ffmpeg_process_count=0,
            external_ffmpeg_cpu_cores_estimate=None,
            host_cpu_sample_mature=False,
            external_pressure_reason="resource_snapshot_missing",
        )
    return _HostCpuPressureSnapshot(
        host_cpu_total_cores=snapshot.host_cpu_total_cores,
        host_cpu_used_cores=snapshot.host_cpu_used_cores,
        host_cpu_used_percent=snapshot.host_cpu_used_percent,
        external_cpu_cores_used_estimate=snapshot.external_cpu_cores_used_estimate,
        external_cpu_percent_estimate=snapshot.external_cpu_percent_estimate,
        external_ffmpeg_process_count=snapshot.external_ffmpeg_process_count,
        external_ffmpeg_cpu_cores_estimate=snapshot.external_ffmpeg_cpu_cores_estimate,
        host_cpu_sample_mature=bool(snapshot.sample_mature and not snapshot.sample_stale),
        route2_worker_ffmpeg_process_count=snapshot.route2_worker_ffmpeg_process_count,
        elvern_owned_ffmpeg_process_count=snapshot.elvern_owned_ffmpeg_process_count,
        elvern_owned_ffmpeg_cpu_cores_estimate=snapshot.elvern_owned_ffmpeg_cpu_cores_estimate,
        external_pressure_reason=snapshot.external_pressure_reason,
    )


def _read_process_cpu_seconds(pid: int) -> float | None:
    if not isinstance(pid, int) or pid <= 0:
        return None
    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        payload = stat_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return _parse_proc_stat_cpu_seconds(payload)


def _parse_proc_status_rss_bytes(payload: str) -> int | None:
    for raw_line in str(payload or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("VmRSS:"):
            continue
        parts = line.split()
        if len(parts) < 2:
            return None
        try:
            return int(parts[1]) * 1024
        except ValueError:
            return None
    return None


def _parse_proc_statm_rss_bytes(payload: str) -> int | None:
    parts = str(payload or "").strip().split()
    if len(parts) < 2:
        return None
    try:
        resident_pages = int(parts[1])
    except ValueError:
        return None
    return resident_pages * _page_size_bytes()


def _read_process_rss_bytes(pid: int) -> int | None:
    if not isinstance(pid, int) or pid <= 0:
        return None
    proc_root = Path("/proc") / str(pid)
    status_path = proc_root / "status"
    try:
        status_payload = status_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        status_payload = None
    if status_payload is not None:
        status_value = _parse_proc_status_rss_bytes(status_payload)
        if status_value is not None:
            return status_value
    statm_path = proc_root / "statm"
    try:
        statm_payload = statm_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return _parse_proc_statm_rss_bytes(statm_payload)


def _parse_proc_io_bytes(payload: str) -> tuple[int | None, int | None]:
    read_bytes: int | None = None
    write_bytes: int | None = None
    for raw_line in str(payload or "").splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = key.strip()
        try:
            parsed_value = max(0, int(value.strip()))
        except ValueError:
            continue
        if normalized_key == "read_bytes":
            read_bytes = parsed_value
        elif normalized_key == "write_bytes":
            write_bytes = parsed_value
    return read_bytes, write_bytes


def _read_process_io_bytes(pid: int) -> tuple[int | None, int | None]:
    if not isinstance(pid, int) or pid <= 0:
        return None, None
    io_path = Path("/proc") / str(pid) / "io"
    try:
        payload = io_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    return _parse_proc_io_bytes(payload)


def _parse_linux_pressure_payload(payload: str) -> dict[str, dict[str, float]]:
    parsed: dict[str, dict[str, float]] = {}
    for raw_line in str(payload or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if not parts:
            continue
        category = parts[0]
        values: dict[str, float] = {}
        for token in parts[1:]:
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            try:
                values[key] = max(0.0, float(value))
            except ValueError:
                continue
        parsed[category] = values
    return parsed


def _read_linux_pressure_file(path: Path) -> tuple[float | None, float | None, list[str]]:
    try:
        payload = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None, [path.name]
    parsed = _parse_linux_pressure_payload(payload)
    missing: list[str] = []
    some_avg10 = parsed.get("some", {}).get("avg10")
    full_avg10 = parsed.get("full", {}).get("avg10")
    if some_avg10 is None:
        missing.append(f"{path.name}_some")
    if full_avg10 is None:
        missing.append(f"{path.name}_full")
    return some_avg10, full_avg10, missing


def _read_linux_psi_snapshot(*, pressure_root: Path = Path("/proc/pressure")) -> _LinuxPressureSnapshot:
    missing: list[str] = []
    cpu_some, cpu_full, cpu_missing = _read_linux_pressure_file(pressure_root / "cpu")
    io_some, io_full, io_missing = _read_linux_pressure_file(pressure_root / "io")
    memory_some, memory_full, memory_missing = _read_linux_pressure_file(pressure_root / "memory")
    missing.extend(f"psi_{item}" for item in cpu_missing)
    missing.extend(f"psi_{item}" for item in io_missing)
    missing.extend(f"psi_{item}" for item in memory_missing)
    sample_available = any(
        value is not None
        for value in (cpu_some, cpu_full, io_some, io_full, memory_some, memory_full)
    )
    return _LinuxPressureSnapshot(
        sample_available=sample_available,
        missing_metrics=missing,
        cpu_some_avg10=cpu_some,
        cpu_full_avg10=cpu_full,
        io_some_avg10=io_some,
        io_full_avg10=io_full,
        memory_some_avg10=memory_some,
        memory_full_avg10=memory_full,
    )


def _parse_cgroup_cpu_stat(payload: str) -> dict[str, int]:
    parsed: dict[str, int] = {}
    for raw_line in str(payload or "").splitlines():
        parts = raw_line.strip().split()
        if len(parts) != 2:
            continue
        try:
            parsed[parts[0]] = max(0, int(parts[1]))
        except ValueError:
            continue
    return parsed


def _detect_cgroup_v2_path(
    *,
    proc_self_cgroup: Path = Path("/proc/self/cgroup"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> Path | None:
    try:
        payload = proc_self_cgroup.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for raw_line in payload.splitlines():
        parts = raw_line.strip().split(":", 2)
        if len(parts) != 3:
            continue
        hierarchy_id, controllers, relative_path = parts
        if hierarchy_id == "0" and controllers == "":
            return cgroup_root / relative_path.lstrip("/")
    return None


def _read_cgroup_telemetry_snapshot(
    *,
    cgroup_path: Path | None = None,
    previous_cpu_stat: dict[str, int] | None = None,
) -> tuple[_CgroupTelemetrySnapshot, dict[str, int] | None]:
    resolved_path = cgroup_path or _detect_cgroup_v2_path()
    if resolved_path is None:
        return _CgroupTelemetrySnapshot(pressure_available=False, missing_metrics=["cgroup_v2_path"]), None
    missing: list[str] = []
    cpu_stat: dict[str, int] | None = None
    try:
        cpu_stat = _parse_cgroup_cpu_stat((resolved_path / "cpu.stat").read_text(encoding="utf-8", errors="replace"))
    except OSError:
        missing.append("cgroup_cpu_stat")
    cpu_some, cpu_full, cpu_missing = _read_linux_pressure_file(resolved_path / "cpu.pressure")
    io_some, io_full, io_missing = _read_linux_pressure_file(resolved_path / "io.pressure")
    memory_some, memory_full, memory_missing = _read_linux_pressure_file(resolved_path / "memory.pressure")
    missing.extend(f"cgroup_{item}" for item in cpu_missing)
    missing.extend(f"cgroup_{item}" for item in io_missing)
    missing.extend(f"cgroup_{item}" for item in memory_missing)
    nr_throttled = cpu_stat.get("nr_throttled") if cpu_stat is not None else None
    throttled_usec = cpu_stat.get("throttled_usec") if cpu_stat is not None else None
    cpu_throttled_delta = None
    cpu_throttled_usec_delta = None
    if previous_cpu_stat is not None and cpu_stat is not None:
        if nr_throttled is not None and "nr_throttled" in previous_cpu_stat:
            cpu_throttled_delta = max(0, nr_throttled - previous_cpu_stat["nr_throttled"])
        if throttled_usec is not None and "throttled_usec" in previous_cpu_stat:
            cpu_throttled_usec_delta = max(0, throttled_usec - previous_cpu_stat["throttled_usec"])
    return (
        _CgroupTelemetrySnapshot(
            pressure_available=not all(value is None for value in (cpu_some, cpu_full, io_some, io_full, memory_some, memory_full)),
            missing_metrics=missing,
            cpu_nr_periods=cpu_stat.get("nr_periods") if cpu_stat is not None else None,
            cpu_nr_throttled=nr_throttled,
            cpu_throttled_usec=throttled_usec,
            cpu_throttled_delta=cpu_throttled_delta,
            cpu_throttled_usec_delta=cpu_throttled_usec_delta,
            cpu_some_avg10=cpu_some,
            cpu_full_avg10=cpu_full,
            io_some_avg10=io_some,
            io_full_avg10=io_full,
            memory_some_avg10=memory_some,
            memory_full_avg10=memory_full,
        ),
        cpu_stat,
    )


def _read_total_memory_bytes() -> int | None:
    meminfo_path = Path("/proc/meminfo")
    try:
        payload = meminfo_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        payload = None
    if payload is not None:
        for raw_line in payload.splitlines():
            line = raw_line.strip()
            if not line.startswith("MemTotal:"):
                continue
            parts = line.split()
            if len(parts) < 2:
                break
            try:
                return int(parts[1]) * 1024
            except ValueError:
                break
    try:
        page_size = max(1, int(os.sysconf("SC_PAGE_SIZE")))
        phys_pages = max(1, int(os.sysconf("SC_PHYS_PAGES")))
    except (AttributeError, ValueError, OSError):
        return None
    return page_size * phys_pages


def _is_non_retryable_cloud_source_error(error: str | None) -> bool:
    normalized = str(error or "").strip().lower()
    if not normalized:
        return False
    return (
        "provider_auth_required" in normalized
        or "token_expired_or_revoked" in normalized
        or "reauth_required" in normalized
        or "reconnect google drive" in normalized
        or "provider_source_error" in normalized
        or "download quota" in normalized
        or "quota exceeded" in normalized
        or "downloadquotaexceeded" in normalized
        or "provider_quota_exceeded" in normalized
    )


class MobilePlaybackManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._sessions: dict[str, MobilePlaybackSession] = {}
        self._active_session_by_user: dict[int, str] = {}
        self._route2_session_ids_by_user: dict[int, set[str]] = {}
        self._cache_states: dict[str, CacheState] = {}
        self._workers: dict[str, str] = {}
        self._route2_workers: dict[str, Route2WorkerRecord] = {}
        self._last_host_cpu_jiffy_sample: _HostCpuJiffySample | None = None
        self._last_elvern_owned_ffmpeg_cpu_seconds_by_pid: dict[int, float] = {}
        self._last_elvern_owned_ffmpeg_cpu_sample_monotonic: float | None = None
        self._last_cgroup_cpu_stat: dict[str, int] | None = None
        self._route2_resource_snapshot: _Route2ResourceSnapshot | None = None
        self._shared_output_metadata_write_errors: list[str] = []
        self._shared_output_init_write_errors: list[str] = []
        self._shared_output_segment_write_errors: list[str] = []
        self._browser_playback_cooldowns: dict[tuple[int, int], dict[str, object]] = {}
        self._manager_stop = threading.Event()
        self._manager_thread: threading.Thread | None = None
        self._route2_resource_telemetry_thread: threading.Thread | None = None
        self._session_root = self.settings.transcode_dir / "mobile_sessions"
        self._cache_root = self.settings.transcode_dir / "mobile_cache"
        self._route2_root = self.settings.transcode_dir / "browser_playback_route2"

    def start(self) -> None:
        self._session_root.mkdir(parents=True, exist_ok=True)
        self._cache_root.mkdir(parents=True, exist_ok=True)
        self._route2_root.mkdir(parents=True, exist_ok=True)
        (self._route2_root / "preflight").mkdir(parents=True, exist_ok=True)
        self._recover_stale_route2_worker_metadata()
        self._cleanup_orphaned_cache_dirs()
        if self._manager_thread is None:
            self._manager_thread = threading.Thread(
                target=self._manager_loop,
                daemon=True,
                name="elvern-mobile-playback-manager",
            )
            self._manager_thread.start()
        if self._route2_resource_telemetry_thread is None:
            self._route2_resource_telemetry_thread = threading.Thread(
                target=self._route2_resource_telemetry_loop,
                daemon=True,
                name="elvern-route2-resource-telemetry",
            )
            self._route2_resource_telemetry_thread.start()
        logger.info(
            "Mobile playback manager ready: root=%s cache=%s workers=%s",
            self._session_root,
            self._cache_root,
            self.settings.max_concurrent_mobile_workers,
        )

    def shutdown(self) -> None:
        self._manager_stop.set()
        if self._manager_thread and self._manager_thread.is_alive():
            self._manager_thread.join(timeout=2)
        if self._route2_resource_telemetry_thread and self._route2_resource_telemetry_thread.is_alive():
            self._route2_resource_telemetry_thread.join(timeout=2)
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._active_session_by_user.clear()
            self._route2_session_ids_by_user.clear()
            self._workers.clear()
            self._route2_workers.clear()
            self._last_host_cpu_jiffy_sample = None
            self._route2_resource_snapshot = None
            self._browser_playback_cooldowns.clear()
            self._manager_thread = None
            self._route2_resource_telemetry_thread = None
        for session in sessions:
            self._terminate_session(session, remove_session_dir=False)

    def create_session(
        self,
        item: dict[str, object],
        *,
        user_id: int,
        auth_session_id: int | None = None,
        username: str | None = None,
        profile: str = "mobile_1080p",
        start_position_seconds: float = 0.0,
        engine_mode: str | None = None,
        playback_mode: str | None = None,
        user_role: str | None = None,
    ) -> dict[str, object]:
        self._validate_transcoding()
        profile_key = self._normalize_profile(profile)
        selected_engine_mode = self._select_engine_mode(engine_mode)
        selected_playback_mode = self._select_playback_mode(playback_mode)
        normalized_user_role = self._normalize_user_role(user_role)
        if selected_engine_mode != "route2" and selected_playback_mode != "lite":
            raise ValueError("Full Playback requires Browser Playback Route 2")
        source_kind = str(item.get("source_kind") or "local")
        if source_kind == "local":
            source_locator = str(
                ensure_media_path_within_root(Path(str(item["file_path"])), self.settings)
            )
            source_input_kind = "path"
        else:
            source_locator = str(item.get("file_path") or "").strip()
            if not source_locator:
                raise ValueError("Experimental playback requires a valid cloud media source")
            source_input_kind = "url"
        duration_seconds, item = _resolve_duration_seconds_impl(
            self.settings,
            item,
            user_id=user_id,
        )
        if not duration_seconds or duration_seconds <= 0:
            raise ValueError("Experimental playback requires a known duration")
        source_fingerprint = self._source_fingerprint(item, source_locator)
        cache_key = self._build_cache_key(source_fingerprint, profile_key)
        source_width = int(item["width"]) if item.get("width") not in {None, ""} else None
        source_height = int(item["height"]) if item.get("height") not in {None, ""} else None
        source_bit_depth = int(item["bit_depth"]) if item.get("bit_depth") not in {None, ""} else None
        source_audio_channels = int(item["audio_channels"]) if item.get("audio_channels") not in {None, ""} else None
        source_hdr_flag = bool(item["hdr_flag"]) if item.get("hdr_flag") is not None else None
        source_dolby_vision_flag = (
            bool(item["dolby_vision_flag"])
            if item.get("dolby_vision_flag") is not None
            else None
        )

        now = utcnow_iso()
        now_ts = time.time()
        target_position_seconds = self._clamp_time(start_position_seconds, duration_seconds)
        if selected_engine_mode == "route2":
            with self._lock:
                compatible_session: MobilePlaybackSession | None = None
                same_movie_conflicting_session: MobilePlaybackSession | None = None
                other_movie_conflicting_session: MobilePlaybackSession | None = None
                route2_sessions = self._get_user_route2_sessions_locked(user_id)
                for candidate in self._ordered_live_sessions_locked(route2_sessions):
                    if candidate.browser_playback.engine_mode != "route2":
                        continue
                    self._refresh_route2_session_authority_locked(candidate)
                    if (
                        candidate.media_item_id != int(item["id"])
                        or candidate.profile != profile_key
                        or candidate.browser_playback.playback_mode != selected_playback_mode
                        or candidate.source_fingerprint != source_fingerprint
                        or candidate.cache_key != cache_key
                    ):
                        if other_movie_conflicting_session is None:
                            other_movie_conflicting_session = candidate
                        continue
                    self._adopt_session_authority_locked(
                        candidate,
                        auth_session_id=auth_session_id,
                        username=username,
                    )
                    if self._route2_session_can_reuse_target_locked(candidate, target_position_seconds):
                        compatible_session = candidate
                        break
                    if same_movie_conflicting_session is None:
                        same_movie_conflicting_session = candidate
            if compatible_session is not None:
                self.touch_session(compatible_session.session_id, user_id=user_id, media_access=True)
                if (
                    abs(compatible_session.target_position_seconds - target_position_seconds) > SEGMENT_DURATION_SECONDS
                ):
                    return self.seek_session(
                        compatible_session.session_id,
                        user_id=user_id,
                        auth_session_id=auth_session_id,
                        username=username,
                        target_position_seconds=target_position_seconds,
                        last_stable_position_seconds=compatible_session.last_stable_position_seconds,
                        playing_before_seek=False,
                    )
                return self.get_session(
                    compatible_session.session_id,
                    user_id=user_id,
                    auth_session_id=auth_session_id,
                    username=username,
                )
            conflicting_session = same_movie_conflicting_session or other_movie_conflicting_session
            if conflicting_session is not None and normalized_user_role != ADMIN_USER_ROLE:
                raise ActivePlaybackWorkerConflictError(
                    self._build_same_user_active_playback_limit_detail_locked(conflicting_session)
                )
            if source_kind == "cloud":
                ensure_cloud_media_item_provider_access(
                    self.settings,
                    user_id=user_id,
                    item_id=int(item["id"]),
                )
            with self._lock:
                self._raise_if_route2_admission_denied_locked(
                    incoming_user_id=user_id,
                    incoming_user_role=normalized_user_role,
                    source_kind=source_kind,
                )
        else:
            with self._lock:
                existing_session_id = self._active_session_by_user.get(user_id)
                existing_session = self._sessions.get(existing_session_id) if existing_session_id else None
            if (
                existing_session
                and existing_session.browser_playback.engine_mode == "legacy"
                and existing_session.state not in {"failed", "stopped", "expired"}
                and existing_session.media_item_id == int(item["id"])
                and existing_session.profile == profile_key
                and existing_session.browser_playback.playback_mode == selected_playback_mode
            ):
                self.touch_session(existing_session.session_id, user_id=user_id, media_access=True)
                if abs(existing_session.target_position_seconds - target_position_seconds) > SEGMENT_DURATION_SECONDS:
                    return self.seek_session(
                        existing_session.session_id,
                        user_id=user_id,
                        target_position_seconds=target_position_seconds,
                        last_stable_position_seconds=existing_session.last_stable_position_seconds,
                        playing_before_seek=False,
                    )
                return self.get_session(existing_session.session_id, user_id=user_id)
            if existing_session and existing_session.browser_playback.engine_mode == "legacy":
                self.stop_session(existing_session.session_id, user_id=user_id)

        session_id = uuid.uuid4().hex
        session = MobilePlaybackSession(
            session_id=session_id,
            user_id=user_id,
            auth_session_id=auth_session_id,
            username=(username or "").strip() or None,
            media_item_id=int(item["id"]),
            media_title=str(item.get("title") or f"Media Item {item['id']}"),
            profile=profile_key,
            source_kind=source_kind,
            duration_seconds=duration_seconds,
            cache_key=cache_key,
            source_locator=source_locator,
            source_input_kind=source_input_kind,
            source_fingerprint=source_fingerprint,
            created_at=now,
            last_client_seen_at=now,
            last_media_access_at=now,
            target_position_seconds=target_position_seconds,
            last_stable_position_seconds=target_position_seconds,
            committed_playhead_seconds=target_position_seconds,
            actual_media_element_time_seconds=target_position_seconds,
            expires_at_ts=now_ts + (self.settings.mobile_session_ttl_minutes * 60),
            browser_playback=self._build_browser_playback_session(
                engine_mode=selected_engine_mode,
                playback_mode=selected_playback_mode,
            ),
            source_original_filename=(str(item.get("original_filename") or "").strip() or None),
            source_container=(str(item.get("container") or "").strip() or None),
            source_video_codec=(str(item.get("video_codec") or "").strip() or None),
            source_audio_codec=(str(item.get("audio_codec") or "").strip() or None),
            source_width=source_width,
            source_height=source_height,
            source_pixel_format=(str(item.get("pixel_format") or "").strip() or None),
            source_bit_depth=source_bit_depth,
            source_hdr_flag=source_hdr_flag,
            source_dolby_vision_flag=source_dolby_vision_flag,
            source_audio_channels=source_audio_channels,
        )
        if selected_engine_mode == "route2":
            with self._lock:
                self._initialize_route2_session_locked(session)
                self._sessions[session.session_id] = session
                self._register_route2_session_locked(session)
            return self.get_session(session.session_id, user_id=user_id)

        cache_state = self._load_cache_state(
            cache_key=cache_key,
            profile=profile_key,
            duration_seconds=duration_seconds,
            source_fingerprint=source_fingerprint,
        )
        with self._lock:
            self._refresh_ready_window_locked(session, cache_state)
            if not self._target_is_ready(session):
                session.pending_target_seconds = target_position_seconds
                session.active_job = self._build_target_cluster_job(session)
            self._transition_session_state_locked(session)
            self._sessions[session.session_id] = session
            self._active_session_by_user[user_id] = session.session_id
        self._ensure_worker_for_session(session.session_id)
        return self.get_session(session.session_id, user_id=user_id)

    def get_session(
        self,
        session_id: str,
        *,
        user_id: int,
        auth_session_id: int | None = None,
        username: str | None = None,
    ) -> dict[str, object]:
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id)
            self._adopt_session_authority_locked(
                session,
                auth_session_id=auth_session_id,
                username=username,
            )
            if session.browser_playback.engine_mode == "route2":
                self._touch_session_locked(session, media_access=False)
                self._refresh_route2_session_authority_locked(session)
                return self._route2_snapshot_locked(session)
            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            self._touch_session_locked(session, media_access=False)
            self._refresh_ready_window_locked(session, cache_state)
            self._transition_session_state_locked(session)
            return self._snapshot_locked(session, cache_state)

    def get_active_session(
        self,
        *,
        user_id: int,
        auth_session_id: int | None = None,
        username: str | None = None,
    ) -> dict[str, object] | None:
        with self._lock:
            session = self._resolve_preferred_session_locked(user_id)
            if session is None:
                return None
            self._adopt_session_authority_locked(
                session,
                auth_session_id=auth_session_id,
                username=username,
            )
            if session.browser_playback.engine_mode == "route2":
                self._touch_session_locked(session, media_access=False)
                self._refresh_route2_session_authority_locked(session)
                return self._route2_snapshot_locked(session)
            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            self._touch_session_locked(session, media_access=False)
            self._refresh_ready_window_locked(session, cache_state)
            self._transition_session_state_locked(session)
            return self._snapshot_locked(session, cache_state)

    def get_active_session_for_item(
        self,
        item_id: int,
        *,
        user_id: int,
        auth_session_id: int | None = None,
        username: str | None = None,
    ) -> dict[str, object] | None:
        with self._lock:
            session = self._resolve_preferred_session_locked(user_id, item_id=item_id)
            if session is None:
                return None
            self._adopt_session_authority_locked(
                session,
                auth_session_id=auth_session_id,
                username=username,
            )
            if session.browser_playback.engine_mode == "route2":
                self._touch_session_locked(session, media_access=False)
                self._refresh_route2_session_authority_locked(session)
                return self._route2_snapshot_locked(session)
            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            self._touch_session_locked(session, media_access=False)
            self._refresh_ready_window_locked(session, cache_state)
            self._transition_session_state_locked(session)
            return self._snapshot_locked(session, cache_state)

    def seek_session(
        self,
        session_id: str,
        *,
        user_id: int,
        auth_session_id: int | None = None,
        username: str | None = None,
        target_position_seconds: float,
        last_stable_position_seconds: float | None = None,
        playing_before_seek: bool | None = None,
    ) -> dict[str, object]:
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id)
            self._adopt_session_authority_locked(
                session,
                auth_session_id=auth_session_id,
                username=username,
            )
            if session.browser_playback.engine_mode == "route2":
                target = self._clamp_time(target_position_seconds, session.duration_seconds)
                stable_position = self._clamp_time(
                    last_stable_position_seconds
                    if last_stable_position_seconds is not None
                    else session.last_stable_position_seconds,
                    session.duration_seconds,
                )
                browser_session = session.browser_playback
                active_epoch = (
                    browser_session.epochs.get(browser_session.active_epoch_id)
                    if browser_session.active_epoch_id
                    else None
                )
                if active_epoch is None:
                    raise ValueError("Browser Playback Route 2 active epoch is missing")
                self._rebuild_route2_published_frontier_locked(active_epoch)
                session.last_stable_position_seconds = stable_position
                session.committed_playhead_seconds = stable_position
                session.actual_media_element_time_seconds = stable_position
                if playing_before_seek is not None:
                    session.playing_before_seek = bool(playing_before_seek)
                    session.client_is_playing = bool(playing_before_seek)
                session.lifecycle_state = "attached"
                session.stalled_recovery_requested = False
                session.last_error = None
                if self._route2_position_in_epoch_locked(session, active_epoch, target):
                    if browser_session.replacement_epoch_id:
                        self._discard_route2_epoch_locked(session, browser_session.replacement_epoch_id)
                    session.target_position_seconds = target
                    session.pending_target_seconds = None
                    active_epoch.attach_position_seconds = target
                    self._write_route2_epoch_metadata_locked(active_epoch)
                    self._refresh_route2_session_authority_locked(session)
                    return self._route2_snapshot_locked(session)
                self._create_route2_replacement_epoch_locked(
                    session,
                    target_position_seconds=target,
                    reason="out_of_range_seek",
                )
                self._refresh_route2_session_authority_locked(session)
                return self._route2_snapshot_locked(session)
            target = self._clamp_time(target_position_seconds, session.duration_seconds)
            stable_position = self._clamp_time(
                last_stable_position_seconds
                if last_stable_position_seconds is not None
                else session.last_stable_position_seconds,
                session.duration_seconds,
            )
            old_job = session.active_job
            if old_job is not None:
                old_job.superseded = True
                self._terminate_job_locked(session, old_job, remove_output=True)
                session.active_job = None

            session.epoch += 1
            session.target_position_seconds = target
            session.pending_target_seconds = target
            session.manifest_start_segment = None
            session.manifest_end_segment = None
            session.last_stable_position_seconds = stable_position
            session.committed_playhead_seconds = stable_position
            session.actual_media_element_time_seconds = stable_position
            session.last_refill_start_seconds = None
            session.last_refill_end_seconds = None
            if playing_before_seek is not None:
                session.playing_before_seek = bool(playing_before_seek)
                session.client_is_playing = bool(playing_before_seek)
            session.lifecycle_state = "attached"
            session.stalled_recovery_requested = False
            session.last_error = None
            session.worker_state = "idle"
            session.queue_started_ts = None

            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            self._refresh_ready_window_locked(session, cache_state)
            if not self._target_is_ready(session):
                session.active_job = self._build_target_cluster_job(session)
            self._transition_session_state_locked(session)
        self._ensure_worker_for_session(session_id)
        return self.get_session(
            session_id,
            user_id=user_id,
            auth_session_id=auth_session_id,
            username=username,
        )

    def update_runtime(
        self,
        session_id: str,
        *,
        user_id: int,
        auth_session_id: int | None = None,
        username: str | None = None,
        committed_playhead_seconds: float | None = None,
        actual_media_element_time_seconds: float | None = None,
        client_attach_revision: int | None = None,
        client_probe_bytes: int | None = None,
        client_probe_duration_ms: int | None = None,
        lifecycle_state: str | None = None,
        stalled: bool | None = None,
        playing: bool | None = None,
    ) -> dict[str, object]:
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id)
            self._adopt_session_authority_locked(
                session,
                auth_session_id=auth_session_id,
                username=username,
            )
            if session.browser_playback.engine_mode == "route2":
                if committed_playhead_seconds is not None:
                    session.committed_playhead_seconds = self._clamp_time(
                        committed_playhead_seconds,
                        session.duration_seconds,
                    )
                    session.last_stable_position_seconds = session.committed_playhead_seconds
                if actual_media_element_time_seconds is not None:
                    session.actual_media_element_time_seconds = self._clamp_time(
                        actual_media_element_time_seconds,
                        session.duration_seconds,
                    )
                if lifecycle_state:
                    session.lifecycle_state = lifecycle_state
                if playing is not None:
                    session.client_is_playing = bool(playing)
                browser_session = session.browser_playback
                if client_attach_revision is not None:
                    coerced_revision = max(0, int(client_attach_revision))
                    browser_session.client_attach_revision = min(
                        browser_session.attach_revision,
                        max(browser_session.client_attach_revision, coerced_revision),
                    )
                self._record_route2_client_probe_sample_locked(
                    session,
                    probe_bytes=client_probe_bytes,
                    probe_duration_ms=client_probe_duration_ms,
                )
                self._touch_session_locked(session, media_access=bool(playing))
                self._refresh_route2_session_authority_locked(session)
                return self._route2_snapshot_locked(session)
            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            if committed_playhead_seconds is not None:
                committed = self._clamp_time(committed_playhead_seconds, session.duration_seconds)
                session.committed_playhead_seconds = committed
                if session.pending_target_seconds is None:
                    session.last_stable_position_seconds = committed
            if actual_media_element_time_seconds is not None:
                session.actual_media_element_time_seconds = self._clamp_time(
                    actual_media_element_time_seconds,
                    session.duration_seconds,
                )
            if lifecycle_state:
                session.lifecycle_state = lifecycle_state
            if playing is not None:
                session.client_is_playing = bool(playing)
            if stalled is True:
                session.stalled_recovery_requested = True
            elif stalled is False and session.lifecycle_state == "attached":
                session.stalled_recovery_requested = False
            self._touch_session_locked(session, media_access=bool(playing))
            self._refresh_ready_window_locked(session, cache_state)
            self._maybe_advance_manifest_window_locked(session)
            self._transition_session_state_locked(session)
        self._ensure_worker_for_session(session_id)
        return self.get_session(session_id, user_id=user_id)

    def stop_session(self, session_id: str, *, user_id: int) -> bool:
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id, allow_missing=True)
            if session is None:
                return False
            self._sessions.pop(session.session_id, None)
            self._unregister_session_locked(session)
        self._terminate_session(session)
        return True

    def raise_if_browser_playback_cooldown_active(
        self,
        *,
        user_id: int,
        media_item_id: int,
        playback_mode: str | None = None,
    ) -> None:
        selected_playback_mode = self._select_playback_mode(playback_mode)
        if selected_playback_mode not in {"lite", "full"}:
            return
        now_ts = time.time()
        with self._lock:
            self._cleanup_browser_playback_cooldowns_locked(now_ts)
            detail = self._build_browser_playback_cooldown_detail_locked(
                user_id=user_id,
                media_item_id=media_item_id,
                now_ts=now_ts,
            )
        if detail is not None:
            raise PlaybackWorkerCooldownError(detail)

    def terminate_route2_worker(self, worker_id: str, *, apply_admin_cooldown: bool = False) -> bool:
        normalized_worker_id = str(worker_id or "").strip()
        if not normalized_worker_id:
            return False
        with self._lock:
            record = self._route2_workers.get(normalized_worker_id)
            if record is None:
                return False
            session = self._sessions.get(record.session_id)
            if session is None or session.browser_playback.engine_mode != "route2":
                return False
            epoch = session.browser_playback.epochs.get(record.epoch_id)
            if epoch is None or epoch.active_worker_id != normalized_worker_id:
                return False
            owner_user_id = session.user_id
            session_id = session.session_id
            media_item_id = session.media_item_id
        stopped = self.stop_session(session_id, user_id=owner_user_id)
        if stopped and apply_admin_cooldown:
            with self._lock:
                self._record_admin_terminated_browser_playback_cooldown_locked(
                    user_id=owner_user_id,
                    media_item_id=media_item_id,
                )
        return stopped

    def touch_session(self, session_id: str, *, user_id: int, media_access: bool) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.user_id != user_id:
                return
            self._touch_session_locked(session, media_access=media_access)

    def get_manifest_content(self, session_id: str, *, user_id: int) -> str:
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id)
            if session.browser_playback.engine_mode == "route2":
                raise ValueError("Browser Playback Route 2 manifest serving is not active yet")
            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            self._touch_session_locked(session, media_access=True)
            self._refresh_ready_window_locked(session, cache_state)
            self._transition_session_state_locked(session)
            manifest_start_segment, manifest_end_segment, total_segments = self._resolve_manifest_window_locked(
                session,
                cache_state,
            )
            target_position_seconds = session.target_position_seconds
            duration_seconds = session.duration_seconds
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:7",
            f"#EXT-X-TARGETDURATION:{math.ceil(SEGMENT_DURATION_SECONDS)}",
            f"#EXT-X-MEDIA-SEQUENCE:{manifest_start_segment}",
            "#EXT-X-PLAYLIST-TYPE:VOD",
            "#EXT-X-INDEPENDENT-SEGMENTS",
            '#EXT-X-MAP:URI="init.mp4"',
        ]
        manifest_start_seconds = manifest_start_segment * SEGMENT_DURATION_SECONDS
        start_offset_seconds = max(0.0, target_position_seconds - manifest_start_seconds)
        if start_offset_seconds > 0.05:
            lines.append(f"#EXT-X-START:TIME-OFFSET={start_offset_seconds:.3f},PRECISE=YES")
        for index in range(manifest_start_segment, manifest_end_segment + 1):
            duration = min(
                SEGMENT_DURATION_SECONDS,
                max(duration_seconds - (index * SEGMENT_DURATION_SECONDS), 0.0),
            )
            lines.append(f"#EXTINF:{duration:.3f},")
            lines.append(f"segments/{index}.m4s")
        lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines) + "\n"

    def get_route2_epoch_manifest_content(self, epoch_id: str, *, user_id: int) -> str:
        with self._lock:
            session, epoch = self._get_owned_route2_epoch_locked(epoch_id, user_id)
            self._prepare_route2_epoch_access_locked(session, epoch, media_kind="manifest")
            self._rebuild_route2_published_frontier_locked(epoch)
            if not epoch.init_published or epoch.contiguous_published_through_segment is None:
                if epoch.state in {"attach_ready", "active", "draining"}:
                    self._log_route2_truth_violation(
                        "manifest_without_published_frontier",
                        session=session,
                        epoch=epoch,
                    )
                raise FileNotFoundError("Route 2 epoch manifest is not published yet")
            ready_start_seconds = epoch.epoch_start_seconds
            attach_offset_seconds = max(0.0, epoch.attach_position_seconds - ready_start_seconds)
            total_epoch_segments = max(
                1,
                math.ceil(max(session.duration_seconds - epoch.epoch_start_seconds, 0.0) / SEGMENT_DURATION_SECONDS),
            )
            manifest_end_segment = min(epoch.contiguous_published_through_segment, total_epoch_segments - 1)
            manifest_complete = epoch.transcoder_completed and manifest_end_segment >= (total_epoch_segments - 1)
            lines = [
                "#EXTM3U",
                "#EXT-X-VERSION:7",
                f"#EXT-X-TARGETDURATION:{math.ceil(SEGMENT_DURATION_SECONDS)}",
                "#EXT-X-MEDIA-SEQUENCE:0",
                "#EXT-X-PLAYLIST-TYPE:EVENT",
                "#EXT-X-INDEPENDENT-SEGMENTS",
                '#EXT-X-MAP:URI="init.mp4"',
            ]
            # Route 2 manifests stay open while the epoch transcoder publishes ahead.
            # Always emit the authoritative attach point so clients do not drift to the
            # published frontier and start behaving like a live-edge stream.
            lines.append(f"#EXT-X-START:TIME-OFFSET={attach_offset_seconds:.3f},PRECISE=YES")
            for index in range(0, manifest_end_segment + 1):
                segment_start_seconds = epoch.epoch_start_seconds + (index * SEGMENT_DURATION_SECONDS)
                duration = min(
                    SEGMENT_DURATION_SECONDS,
                    max(session.duration_seconds - segment_start_seconds, 0.0),
                )
                lines.append(f"#EXTINF:{duration:.3f},")
                lines.append(f"segments/{index}.m4s")
            if manifest_complete:
                lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines) + "\n"

    def get_init_path(self, session_id: str, *, user_id: int) -> Path:
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id)
            if session.browser_playback.engine_mode == "route2":
                raise ValueError("Browser Playback Route 2 init serving is not active yet")
            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            self._touch_session_locked(session, media_access=True)
            active_output_dir = session.active_job.output_dir if session.active_job else None
        if cache_state.init_path.exists():
            return cache_state.init_path
        if active_output_dir:
            candidate = active_output_dir / "init.mp4"
            if candidate.exists():
                self._publish_init_to_cache(cache_state, candidate)
                return cache_state.init_path if cache_state.init_path.exists() else candidate
        deadline = time.time() + FRONTIER_WAIT_SECONDS
        while time.time() < deadline:
            if cache_state.init_path.exists():
                return cache_state.init_path
            if active_output_dir:
                candidate = active_output_dir / "init.mp4"
                if candidate.exists():
                    self._publish_init_to_cache(cache_state, candidate)
                    return cache_state.init_path if cache_state.init_path.exists() else candidate
            time.sleep(0.1)
        raise FileNotFoundError("Experimental playback init segment is not ready yet")

    def get_route2_epoch_init_path(self, epoch_id: str, *, user_id: int) -> Path:
        with self._lock:
            session, epoch = self._get_owned_route2_epoch_locked(epoch_id, user_id)
            self._prepare_route2_epoch_access_locked(session, epoch, media_kind="init")
            self._rebuild_route2_published_frontier_locked(epoch)
            if not epoch.init_published or not epoch.published_init_path.exists():
                if epoch.state in {"attach_ready", "active", "draining"}:
                    self._log_route2_truth_violation(
                        "published_init_missing",
                        session=session,
                        epoch=epoch,
                        published_init_path=str(epoch.published_init_path),
                    )
                raise FileNotFoundError("Route 2 epoch init segment is not published yet")
            return epoch.published_init_path

    def get_segment_path(
        self,
        session_id: str,
        segment_index: int,
        *,
        user_id: int,
    ) -> Path:
        if segment_index < 0:
            raise FileNotFoundError("Experimental playback segment not found")
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id)
            if session.browser_playback.engine_mode == "route2":
                raise ValueError("Browser Playback Route 2 segment serving is not active yet")
        deadline = time.time() + FRONTIER_WAIT_SECONDS
        while time.time() < deadline:
            should_wait_for_frontier = False
            with self._lock:
                session = self._get_owned_session_locked(session_id, user_id)
                cache_state = self._load_cache_state_locked(
                    cache_key=session.cache_key,
                    profile=session.profile,
                    duration_seconds=session.duration_seconds,
                    source_fingerprint=session.source_fingerprint,
                )
                self._touch_session_locked(session, media_access=True)
                self._refresh_ready_window_locked(session, cache_state)
                self._transition_session_state_locked(session)
                active_job = session.active_job
                active_output_dir = active_job.output_dir if active_job else None
                cached = cache_state.cache_dir / f"segment_{segment_index:06d}.m4s"
                if cached.exists():
                    return cached
                if active_output_dir:
                    candidate = active_output_dir / f"segment_{segment_index:06d}.m4s"
                    if candidate.exists():
                        self._publish_segment_to_cache_locked(cache_state, segment_index, candidate)
                        self._write_cache_metadata_locked(cache_state)
                        return cached if cached.exists() else candidate
                available = self._combined_available_segments_locked(session, cache_state)
                frontier_segment = max(available) if available else -1
                wait_for_segment = (
                    active_job is not None
                    and active_job.prepare_start_segment <= segment_index <= active_job.prepare_end_segment
                )
                should_wait_for_frontier = (
                    session.state in {"queued", "preparing", "retargeting", "ready"}
                    and segment_index > frontier_segment
                    and segment_index <= frontier_segment + math.ceil(WATCH_REFILL_TARGET_SECONDS / SEGMENT_DURATION_SECONDS)
                )
            if not wait_for_segment and not should_wait_for_frontier:
                raise FileNotFoundError("Experimental playback segment is not cached yet")
            if should_wait_for_frontier:
                self._ensure_worker_for_session(session_id)
            time.sleep(0.1)
        raise FileNotFoundError("Experimental playback segment is not ready yet")

    def get_route2_epoch_segment_path(
        self,
        epoch_id: str,
        segment_index: int,
        *,
        user_id: int,
    ) -> Path:
        if segment_index < 0:
            raise FileNotFoundError("Route 2 epoch segment not found")
        with self._lock:
            session, epoch = self._get_owned_route2_epoch_locked(epoch_id, user_id)
            self._prepare_route2_epoch_access_locked(session, epoch, media_kind="segment")
            self._rebuild_route2_published_frontier_locked(epoch)
            if not epoch.init_published or epoch.contiguous_published_through_segment is None:
                if epoch.state in {"attach_ready", "active", "draining"}:
                    self._log_route2_truth_violation(
                        "segment_requested_without_frontier",
                        session=session,
                        epoch=epoch,
                        segment_index=segment_index,
                    )
                raise FileNotFoundError("Route 2 epoch segment is not published yet")
            total_epoch_segments = max(
                1,
                math.ceil(max(session.duration_seconds - epoch.epoch_start_seconds, 0.0) / SEGMENT_DURATION_SECONDS),
            )
            if segment_index >= total_epoch_segments:
                raise FileNotFoundError("Route 2 epoch segment is not published yet")
            if segment_index > epoch.contiguous_published_through_segment:
                raise FileNotFoundError("Route 2 epoch segment is not published yet")
            segment_path = self._route2_segment_destination(epoch, segment_index)
            if not segment_path.exists():
                self._log_route2_truth_violation(
                    "published_segment_missing",
                    session=session,
                    epoch=epoch,
                    segment_index=segment_index,
                    expected_path=str(segment_path),
                )
                raise FileNotFoundError("Route 2 epoch segment is not published yet")
            return segment_path

    def _session_activity_ts(self, session: MobilePlaybackSession) -> float:
        return max(
            self._parse_iso_ts(session.last_client_seen_at),
            self._parse_iso_ts(session.last_media_access_at),
        )

    def _ordered_live_sessions_locked(
        self,
        sessions: list[MobilePlaybackSession],
    ) -> list[MobilePlaybackSession]:
        return sorted(
            [
                session
                for session in sessions
                if session.state not in {"failed", "stopped", "expired"}
            ],
            key=self._session_activity_ts,
            reverse=True,
        )

    def _resolve_preferred_session_locked(
        self,
        user_id: int,
        *,
        item_id: int | None = None,
    ) -> MobilePlaybackSession | None:
        candidates = self._ordered_live_sessions_locked(
            [
                session
                for session in self._sessions.values()
                if session.user_id == user_id
                and (item_id is None or session.media_item_id == item_id)
            ]
        )
        if not candidates:
            self._active_session_by_user.pop(user_id, None)
            return None
        preferred_session_id = self._active_session_by_user.get(user_id)
        if preferred_session_id:
            preferred_session = self._sessions.get(preferred_session_id)
            if (
                preferred_session is not None
                and preferred_session in candidates
                and (item_id is None or preferred_session.media_item_id == item_id)
            ):
                return preferred_session
        preferred_session = candidates[0]
        self._active_session_by_user[user_id] = preferred_session.session_id
        return preferred_session

    def _get_user_route2_sessions_locked(self, user_id: int) -> list[MobilePlaybackSession]:
        session_ids = self._route2_session_ids_by_user.get(user_id, set())
        sessions: list[MobilePlaybackSession] = []
        for session_id in session_ids:
            session = self._sessions.get(session_id)
            if session is None or session.browser_playback.engine_mode != "route2":
                continue
            sessions.append(session)
        return sessions

    def _register_route2_session_locked(self, session: MobilePlaybackSession) -> None:
        session_ids = self._route2_session_ids_by_user.setdefault(session.user_id, set())
        session_ids.add(session.session_id)
        self._active_session_by_user[session.user_id] = session.session_id

    def _unregister_session_locked(self, session: MobilePlaybackSession) -> None:
        if session.browser_playback.engine_mode == "route2":
            session_ids = self._route2_session_ids_by_user.get(session.user_id)
            if session_ids is not None:
                session_ids.discard(session.session_id)
                if not session_ids:
                    self._route2_session_ids_by_user.pop(session.user_id, None)
        if self._active_session_by_user.get(session.user_id) == session.session_id:
            replacement = self._resolve_preferred_session_locked(session.user_id)
            if replacement is None or replacement.session_id == session.session_id:
                self._active_session_by_user.pop(session.user_id, None)
            else:
                self._active_session_by_user[session.user_id] = replacement.session_id

    def _adopt_session_authority_locked(
        self,
        session: MobilePlaybackSession,
        *,
        auth_session_id: int | None = None,
        username: str | None = None,
    ) -> None:
        normalized_username = (username or "").strip() or None
        if auth_session_id is not None:
            session.auth_session_id = auth_session_id
        if normalized_username:
            session.username = normalized_username

    def _route2_conflict_worker_id_locked(self, session: MobilePlaybackSession) -> str | None:
        candidate_ids = [
            session.browser_playback.replacement_epoch_id,
            session.browser_playback.active_epoch_id,
        ]
        for epoch_id in candidate_ids:
            if not epoch_id:
                continue
            epoch = session.browser_playback.epochs.get(epoch_id)
            worker_id = epoch.active_worker_id if epoch is not None else None
            if worker_id:
                return worker_id
        for record in self._route2_workers.values():
            if record.session_id != session.session_id:
                continue
            if record.state in {"queued", "running", "stopping"}:
                return record.worker_id
        return None

    def _build_active_playback_worker_conflict_detail_locked(
        self,
        session: MobilePlaybackSession,
    ) -> dict[str, object]:
        title = session.media_title or f"Media Item {session.media_item_id}"
        return {
            "code": ACTIVE_WORKER_CONFLICT_CODE,
            "active_movie_title": title,
            "active_media_item_id": session.media_item_id,
            "active_playback_mode": session.browser_playback.playback_mode,
            "active_worker_id": self._route2_conflict_worker_id_locked(session),
            "active_session_id": session.session_id,
            "message": f"{title} is still preparing.",
        }

    def _build_same_user_active_playback_limit_detail_locked(
        self,
        session: MobilePlaybackSession,
    ) -> dict[str, object]:
        detail = self._build_active_playback_worker_conflict_detail_locked(session)
        detail.update(
            {
                "code": SAME_USER_ACTIVE_PLAYBACK_LIMIT_CODE,
                "legacy_code": ACTIVE_WORKER_CONFLICT_CODE,
                "message": "You already have an active playback. Stop it or switch before starting another.",
            }
        )
        return detail

    def _build_server_max_capacity_detail_locked(
        self,
        *,
        reason_code: str,
        message: str | None = None,
        active_user_count_after_admission: int | None = None,
        available_reserved_threads: int | None = None,
        admission_min_threads: int | None = None,
    ) -> dict[str, object]:
        return {
            "code": SERVER_MAX_CAPACITY_CODE,
            "reason_code": reason_code,
            "message": message or "Server is busy. Please try again later.",
            "active_route2_user_count_after_admission": active_user_count_after_admission,
            "available_reserved_threads": available_reserved_threads,
            "required_min_threads": admission_min_threads,
            "protected_min_threads_per_active_user": self._route2_protected_min_threads_per_active_user(),
        }

    def _cleanup_browser_playback_cooldowns_locked(self, now_ts: float | None = None) -> None:
        current_ts = float(now_ts if now_ts is not None else time.time())
        expired_keys = [
            key
            for key, entry in self._browser_playback_cooldowns.items()
            if float(entry.get("expires_at_ts") or 0.0) <= current_ts
        ]
        for key in expired_keys:
            self._browser_playback_cooldowns.pop(key, None)

    def _record_admin_terminated_browser_playback_cooldown_locked(
        self,
        *,
        user_id: int,
        media_item_id: int,
        now_ts: float | None = None,
    ) -> None:
        current_ts = float(now_ts if now_ts is not None else time.time())
        self._cleanup_browser_playback_cooldowns_locked(current_ts)
        self._browser_playback_cooldowns[(int(user_id), int(media_item_id))] = {
            "reason": "admin_terminated_worker",
            "expires_at_ts": current_ts + ADMIN_TERMINATED_BROWSER_PLAYBACK_COOLDOWN_SECONDS,
        }

    def _build_browser_playback_cooldown_detail_locked(
        self,
        *,
        user_id: int,
        media_item_id: int,
        now_ts: float | None = None,
    ) -> dict[str, object] | None:
        current_ts = float(now_ts if now_ts is not None else time.time())
        entry = self._browser_playback_cooldowns.get((int(user_id), int(media_item_id)))
        if entry is None:
            return None
        expires_at_ts = float(entry.get("expires_at_ts") or 0.0)
        if expires_at_ts <= current_ts:
            self._browser_playback_cooldowns.pop((int(user_id), int(media_item_id)), None)
            return None
        remaining_seconds = max(1, math.ceil(expires_at_ts - current_ts))
        return {
            "code": "playback_worker_cooldown",
            "media_item_id": int(media_item_id),
            "remaining_seconds": remaining_seconds,
            "message": (
                "Your current quota for this movie has been reached. "
                f"Please try again in {remaining_seconds} seconds."
            ),
        }

    def _route2_session_can_reuse_target_locked(
        self,
        session: MobilePlaybackSession,
        target_position_seconds: float,
    ) -> bool:
        if abs(session.target_position_seconds - target_position_seconds) <= SEGMENT_DURATION_SECONDS:
            return True
        browser_session = session.browser_playback
        active_epoch = (
            browser_session.epochs.get(browser_session.active_epoch_id)
            if browser_session.active_epoch_id
            else None
        )
        if (
            active_epoch is not None
            and active_epoch.init_published
            and active_epoch.contiguous_published_through_segment is not None
            and self._route2_position_in_epoch_locked(session, active_epoch, target_position_seconds)
        ):
            return True
        return (
            session.ready_start_seconds <= target_position_seconds <= session.ready_end_seconds
            and session.ready_end_seconds > session.ready_start_seconds
        )

    def _route2_epoch_prepared_ranges_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> list[list[float]]:
        self._rebuild_route2_published_frontier_locked(epoch)
        if not epoch.init_published or epoch.contiguous_published_through_segment is None:
            return []
        return [[
            round(epoch.epoch_start_seconds, 2),
            round(self._route2_epoch_ready_end_seconds(session, epoch), 2),
        ]]

    def _ensure_route2_worker_record_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> Route2WorkerRecord:
        worker_id = epoch.active_worker_id or uuid.uuid4().hex
        epoch.active_worker_id = worker_id
        record = self._route2_workers.get(worker_id)
        if record is None:
            record = Route2WorkerRecord(
                worker_id=worker_id,
                session_id=session.session_id,
                epoch_id=epoch.epoch_id,
                user_id=session.user_id,
                username=session.username,
                auth_session_id=session.auth_session_id,
                media_item_id=session.media_item_id,
                title=session.media_title,
                playback_mode=session.browser_playback.playback_mode,
                profile=session.profile,
                source_kind=session.source_kind,
                target_position_seconds=round(epoch.attach_position_seconds, 2),
            )
            self._route2_workers[worker_id] = record
        self._sync_route2_worker_record_locked(record, session, epoch)
        return record

    def _mark_route2_worker_runtime_finished_locked(self, record: Route2WorkerRecord) -> None:
        if record.state == "running":
            record.finished_at = None
            return
        if record.started_at and not record.finished_at:
            record.finished_at = utcnow_iso()

    def _sync_route2_worker_record_locked(
        self,
        record: Route2WorkerRecord,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> None:
        record.username = session.username
        record.auth_session_id = session.auth_session_id
        record.title = session.media_title
        record.playback_mode = session.browser_playback.playback_mode
        record.profile = session.profile
        record.source_kind = session.source_kind
        record.target_position_seconds = round(epoch.attach_position_seconds, 2)
        record.last_seen_at = utcnow_iso()
        record.prepared_ranges = self._route2_epoch_prepared_ranges_locked(session, epoch)
        record.stop_requested = epoch.stop_requested
        record.non_retryable_error = epoch.last_error if _is_non_retryable_cloud_source_error(epoch.last_error) else None
        record.replacement_count = session.browser_playback.replacement_epoch_count
        process = epoch.process
        if process is not None and process.poll() is None:
            record.process = process
            record.pid = process.pid
        else:
            record.process = None
            record.pid = None
        self._mark_route2_worker_runtime_finished_locked(record)

    def _finalize_route2_worker_record_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        *,
        state: str,
        increment_failure: bool = False,
        remove: bool = False,
    ) -> None:
        worker_id = epoch.active_worker_id
        if not worker_id:
            return
        record = self._ensure_route2_worker_record_locked(session, epoch)
        record.state = state
        if increment_failure:
            record.failure_count += 1
        self._mark_route2_worker_runtime_finished_locked(record)
        if state != "running":
            record.process = None
            record.pid = None
        self._sync_route2_worker_record_locked(record, session, epoch)
        if remove:
            self._route2_workers.pop(worker_id, None)

    def _remove_route2_worker_record_locked(self, epoch: PlaybackEpoch) -> None:
        worker_id = epoch.active_worker_id
        if not worker_id:
            return
        self._route2_workers.pop(worker_id, None)

    def _route2_running_workers_locked(self, *, user_id: int | None = None) -> list[Route2WorkerRecord]:
        return [
            record
            for record in self._route2_workers.values()
            if record.state == "running" and (user_id is None or record.user_id == user_id)
        ]

    def _route2_queued_workers_locked(self, *, user_id: int | None = None) -> list[Route2WorkerRecord]:
        return [
            record
            for record in self._route2_workers.values()
            if record.state == "queued" and (user_id is None or record.user_id == user_id)
        ]

    def _route2_running_threads_locked(self, *, user_id: int | None = None) -> int:
        return sum(max(0, int(record.assigned_threads)) for record in self._route2_running_workers_locked(user_id=user_id))

    def _clear_route2_worker_telemetry_locked(
        self,
        record: Route2WorkerRecord,
        *,
        sampled_at: str | None = None,
    ) -> None:
        record.process_exists = False
        record.cpu_cores_used = None
        record.cpu_percent_of_total = None
        record.memory_bytes = None
        record.memory_percent_of_total = None
        record.telemetry_sampled = False
        if sampled_at is not None:
            record.last_sampled_at = sampled_at
        record.last_cpu_sample_monotonic = None
        record.last_process_cpu_seconds = None
        record.last_cpu_sample_pid = None
        record.io_read_bytes = None
        record.io_write_bytes = None
        record.io_read_bytes_per_second = None
        record.io_write_bytes_per_second = None
        record.io_observation_seconds = None
        record.io_sample_mature = False
        record.io_sample_stale = True
        record.io_missing_metrics = ["proc_io_unavailable"]
        record.last_io_sample_pid = None
        record.last_io_sample_monotonic = None
        record.last_io_read_bytes = None
        record.last_io_write_bytes = None

    def _mark_route2_worker_unavailable_locked(
        self,
        record: Route2WorkerRecord,
        *,
        sampled_at: str,
    ) -> None:
        if record.state == "running":
            record.state = "stopped" if record.stop_requested else "interrupted"
        self._mark_route2_worker_runtime_finished_locked(record)
        record.process = None
        self._clear_route2_worker_telemetry_locked(record, sampled_at=sampled_at)

    def _apply_route2_worker_telemetry_sample_locked(
        self,
        record: Route2WorkerRecord,
        *,
        pid: int,
        cpu_seconds: float | None,
        memory_bytes: int | None,
        io_read_bytes: int | None,
        io_write_bytes: int | None,
        total_cpu_cores: int,
        total_memory_bytes: int | None,
        sample_monotonic: float,
        sampled_at: str,
    ) -> None:
        record.pid = pid
        record.process_exists = True
        record.last_sampled_at = sampled_at
        record.memory_bytes = memory_bytes
        record.memory_percent_of_total = (
            (memory_bytes / total_memory_bytes) * 100
            if memory_bytes is not None and total_memory_bytes
            else None
        )

        telemetry_sampled = (
            cpu_seconds is not None
            and record.last_cpu_sample_pid == pid
            and record.last_cpu_sample_monotonic is not None
            and record.last_process_cpu_seconds is not None
            and sample_monotonic > record.last_cpu_sample_monotonic
        )
        cpu_cores_used = None
        if telemetry_sampled:
            delta_cpu_seconds = max(0.0, cpu_seconds - record.last_process_cpu_seconds)
            delta_wall_seconds = sample_monotonic - record.last_cpu_sample_monotonic
            if delta_wall_seconds > 0:
                cpu_cores_used = delta_cpu_seconds / delta_wall_seconds
            else:
                telemetry_sampled = False
        record.telemetry_sampled = bool(telemetry_sampled and cpu_cores_used is not None)
        record.cpu_cores_used = cpu_cores_used if record.telemetry_sampled else None
        record.cpu_percent_of_total = (
            (record.cpu_cores_used / total_cpu_cores) * 100
            if record.cpu_cores_used is not None and total_cpu_cores > 0
            else None
        )
        record.last_cpu_sample_pid = pid
        record.last_cpu_sample_monotonic = sample_monotonic
        record.last_process_cpu_seconds = cpu_seconds
        io_missing_metrics: list[str] = []
        if io_read_bytes is None:
            io_missing_metrics.append("proc_io_read_bytes")
        if io_write_bytes is None:
            io_missing_metrics.append("proc_io_write_bytes")
        io_sample_mature = (
            not io_missing_metrics
            and record.last_io_sample_pid == pid
            and record.last_io_sample_monotonic is not None
            and record.last_io_read_bytes is not None
            and record.last_io_write_bytes is not None
            and sample_monotonic > record.last_io_sample_monotonic
        )
        io_read_rate = None
        io_write_rate = None
        io_observation_seconds = None
        if io_sample_mature:
            delta_wall_seconds = sample_monotonic - float(record.last_io_sample_monotonic)
            if delta_wall_seconds > 0:
                io_observation_seconds = delta_wall_seconds
                io_read_rate = max(0.0, float(io_read_bytes - record.last_io_read_bytes) / delta_wall_seconds)
                io_write_rate = max(0.0, float(io_write_bytes - record.last_io_write_bytes) / delta_wall_seconds)
            else:
                io_sample_mature = False
        record.io_read_bytes = io_read_bytes
        record.io_write_bytes = io_write_bytes
        record.io_read_bytes_per_second = io_read_rate
        record.io_write_bytes_per_second = io_write_rate
        record.io_observation_seconds = io_observation_seconds
        record.io_sample_mature = bool(io_sample_mature)
        record.io_sample_stale = bool(io_missing_metrics)
        record.io_missing_metrics = io_missing_metrics
        record.last_io_sample_pid = pid
        record.last_io_sample_monotonic = sample_monotonic
        record.last_io_read_bytes = io_read_bytes
        record.last_io_write_bytes = io_write_bytes

    def _sample_route2_worker_telemetry_locked(
        self,
        record: Route2WorkerRecord,
        *,
        total_cpu_cores: int,
        total_memory_bytes: int | None,
        sample_monotonic: float,
        sample_wall_ts: float,
        sampled_at: str,
    ) -> None:
        process = record.process
        pid = record.pid or (process.pid if process is not None else None)
        if process is None or pid is None:
            started_reference = record.started_at or record.created_at
            if (
                record.state == "running"
                and started_reference
                and (sample_wall_ts - self._parse_iso_ts(started_reference)) <= ROUTE2_TELEMETRY_PROCESS_ATTACH_GRACE_SECONDS
            ):
                self._clear_route2_worker_telemetry_locked(record, sampled_at=sampled_at)
                return
            self._mark_route2_worker_unavailable_locked(record, sampled_at=sampled_at)
            return
        record.pid = pid
        if process.poll() is not None:
            self._mark_route2_worker_unavailable_locked(record, sampled_at=sampled_at)
            return

        cpu_seconds = _read_process_cpu_seconds(pid)
        memory_bytes = _read_process_rss_bytes(pid)
        io_read_bytes, io_write_bytes = _read_process_io_bytes(pid)
        if cpu_seconds is None and process.poll() is not None:
            self._mark_route2_worker_unavailable_locked(record, sampled_at=sampled_at)
            return

        self._apply_route2_worker_telemetry_sample_locked(
            record,
            pid=pid,
            cpu_seconds=cpu_seconds,
            memory_bytes=memory_bytes,
            io_read_bytes=io_read_bytes,
            io_write_bytes=io_write_bytes,
            total_cpu_cores=total_cpu_cores,
            total_memory_bytes=total_memory_bytes,
            sample_monotonic=sample_monotonic,
            sampled_at=sampled_at,
        )

    def _collect_route2_worker_telemetry_targets_locked(
        self,
    ) -> tuple[list[_Route2WorkerTelemetryReadTarget], set[int]]:
        targets: list[_Route2WorkerTelemetryReadTarget] = []
        owned_route2_pids: set[int] = set()
        for record in self._route2_workers.values():
            process = record.process
            pid = record.pid or (process.pid if process is not None else None)
            if isinstance(pid, int) and pid > 0:
                owned_route2_pids.add(pid)
            if record.state != "running" or not isinstance(pid, int) or pid <= 0:
                continue
            targets.append(
                _Route2WorkerTelemetryReadTarget(
                    worker_id=record.worker_id,
                    pid=pid,
                )
            )
        return targets, owned_route2_pids

    def _read_route2_worker_telemetry_targets(
        self,
        targets: list[_Route2WorkerTelemetryReadTarget],
    ) -> dict[str, _Route2WorkerTelemetryReadResult]:
        results: dict[str, _Route2WorkerTelemetryReadResult] = {}
        for target in targets:
            io_read_bytes, io_write_bytes = _read_process_io_bytes(target.pid)
            results[target.worker_id] = _Route2WorkerTelemetryReadResult(
                worker_id=target.worker_id,
                pid=target.pid,
                cpu_seconds=_read_process_cpu_seconds(target.pid),
                memory_bytes=_read_process_rss_bytes(target.pid),
                io_read_bytes=io_read_bytes,
                io_write_bytes=io_write_bytes,
            )
        return results

    def _route2_cpu_total_for_host_pressure_locked(self) -> float | None:
        running_records = [record for record in self._route2_workers.values() if record.state == "running"]
        if not running_records:
            return 0.0
        route2_cpu_cores_used_total = 0.0
        any_cpu_sampled = False
        for record in running_records:
            if record.cpu_cores_used is None:
                continue
            route2_cpu_cores_used_total += record.cpu_cores_used
            any_cpu_sampled = True
        return route2_cpu_cores_used_total if any_cpu_sampled else None

    def _elvern_owned_ffmpeg_cpu_cores_for_host_pressure_locked(
        self,
        *,
        current_cpu_seconds_by_pid: dict[int, float],
        sample_monotonic: float,
    ) -> float | None:
        previous_sample_monotonic = self._last_elvern_owned_ffmpeg_cpu_sample_monotonic
        previous_cpu_seconds_by_pid = self._last_elvern_owned_ffmpeg_cpu_seconds_by_pid
        current_readings = {
            int(pid): float(cpu_seconds)
            for pid, cpu_seconds in current_cpu_seconds_by_pid.items()
            if int(pid) > 0 and cpu_seconds is not None
        }
        self._last_elvern_owned_ffmpeg_cpu_seconds_by_pid = current_readings
        self._last_elvern_owned_ffmpeg_cpu_sample_monotonic = sample_monotonic
        if previous_sample_monotonic is None:
            return None if current_readings else 0.0
        delta_wall_seconds = sample_monotonic - previous_sample_monotonic
        if delta_wall_seconds <= 0:
            return None
        total_cpu_delta_seconds = 0.0
        any_matched_process = False
        for pid, cpu_seconds in current_readings.items():
            previous_cpu_seconds = previous_cpu_seconds_by_pid.get(pid)
            if previous_cpu_seconds is None:
                continue
            total_cpu_delta_seconds += max(0.0, cpu_seconds - previous_cpu_seconds)
            any_matched_process = True
        if not current_readings:
            return 0.0
        return total_cpu_delta_seconds / delta_wall_seconds if any_matched_process else None

    def _store_route2_resource_snapshot_locked(
        self,
        *,
        sampled_at_ts: float,
        sampled_at: str,
        total_memory_bytes: int | None,
        host_cpu_pressure: _HostCpuPressureSnapshot,
    ) -> _Route2ResourceSnapshot:
        running_records = [record for record in self._route2_workers.values() if record.state == "running"]
        route2_cpu_cores_used_total = 0.0
        route2_memory_bytes_total = 0
        per_user_cpu_cores_used_total: dict[int, float] = {}
        any_cpu_sampled = False
        any_memory_sampled = False
        for record in running_records:
            if record.cpu_cores_used is not None:
                route2_cpu_cores_used_total += record.cpu_cores_used
                per_user_cpu_cores_used_total[record.user_id] = (
                    per_user_cpu_cores_used_total.get(record.user_id, 0.0) + record.cpu_cores_used
                )
                any_cpu_sampled = True
            if record.memory_bytes is not None:
                route2_memory_bytes_total += record.memory_bytes
                any_memory_sampled = True

        if any_cpu_sampled:
            route2_cpu_total: float | None = route2_cpu_cores_used_total
        elif running_records:
            route2_cpu_total = None
        else:
            route2_cpu_total = 0.0

        if any_memory_sampled:
            route2_memory_total: int | None = route2_memory_bytes_total
        elif running_records:
            route2_memory_total = None
        else:
            route2_memory_total = 0

        missing_metrics: list[str] = []
        if not host_cpu_pressure.host_cpu_sample_mature:
            missing_metrics.append("host_cpu_sample_mature")
        if running_records and route2_cpu_total is None:
            missing_metrics.append("route2_cpu_cores_used_total")
        if total_memory_bytes is None:
            missing_metrics.append("total_memory_bytes")
        if running_records and route2_memory_total is None:
            missing_metrics.append("route2_memory_bytes_total")

        host_total_cores = host_cpu_pressure.host_cpu_total_cores
        external_pressure_level, external_pressure_reason = _classify_external_pressure(host_cpu_pressure)
        snapshot = _Route2ResourceSnapshot(
            sampled_at_ts=sampled_at_ts,
            sampled_at=sampled_at,
            sample_mature=host_cpu_pressure.host_cpu_sample_mature,
            sample_stale=False,
            host_cpu_total_cores=host_total_cores,
            host_cpu_used_cores=host_cpu_pressure.host_cpu_used_cores,
            host_cpu_used_percent=host_cpu_pressure.host_cpu_used_percent,
            route2_cpu_cores_used_total=route2_cpu_total,
            route2_cpu_percent_of_host=(
                (route2_cpu_total / host_total_cores) * 100
                if route2_cpu_total is not None and host_total_cores
                else None
            ),
            per_user_cpu_cores_used_total=per_user_cpu_cores_used_total,
            total_memory_bytes=total_memory_bytes,
            route2_memory_bytes_total=route2_memory_total,
            route2_memory_percent_of_total=(
                (route2_memory_total / total_memory_bytes) * 100
                if route2_memory_total is not None and total_memory_bytes
                else None
            ),
            external_cpu_cores_used_estimate=host_cpu_pressure.external_cpu_cores_used_estimate,
            external_cpu_percent_estimate=host_cpu_pressure.external_cpu_percent_estimate,
            external_ffmpeg_process_count=host_cpu_pressure.external_ffmpeg_process_count,
            external_ffmpeg_cpu_cores_estimate=host_cpu_pressure.external_ffmpeg_cpu_cores_estimate,
            external_pressure_level=external_pressure_level,
            missing_metrics=missing_metrics,
            route2_worker_ffmpeg_process_count=host_cpu_pressure.route2_worker_ffmpeg_process_count,
            elvern_owned_ffmpeg_process_count=host_cpu_pressure.elvern_owned_ffmpeg_process_count,
            elvern_owned_ffmpeg_cpu_cores_estimate=host_cpu_pressure.elvern_owned_ffmpeg_cpu_cores_estimate,
            external_pressure_reason=external_pressure_reason,
        )
        self._route2_resource_snapshot = snapshot
        return snapshot

    def _latest_route2_resource_snapshot_locked(self, *, now_ts: float | None = None) -> _Route2ResourceSnapshot | None:
        snapshot = self._route2_resource_snapshot
        if snapshot is None:
            return None
        reference_ts = time.time() if now_ts is None else now_ts
        snapshot.sample_stale = (reference_ts - snapshot.sampled_at_ts) > ROUTE2_RESOURCE_SNAPSHOT_STALE_SECONDS
        return snapshot

    def _sample_route2_resource_telemetry(self) -> None:
        sampled_at_ts = time.time()
        sample_monotonic = time.monotonic()
        sampled_at = utcnow_iso()
        total_cpu_cores = _detect_total_cpu_cores()
        total_memory_bytes = _read_total_memory_bytes()
        with self._lock:
            targets, owned_route2_pids = self._collect_route2_worker_telemetry_targets_locked()

        worker_results = self._read_route2_worker_telemetry_targets(targets)
        current_host_sample = _read_host_cpu_jiffy_sample(sample_monotonic=sample_monotonic)
        ffmpeg_processes = _classify_ffmpeg_processes(owned_route2_pids=owned_route2_pids)
        elvern_owned_ffmpeg_cpu_seconds_by_pid = _read_process_cpu_seconds_for_pids(ffmpeg_processes.elvern_owned_pids)

        with self._lock:
            for worker_id, result in worker_results.items():
                record = self._route2_workers.get(worker_id)
                if record is None or record.state != "running" or record.pid != result.pid:
                    continue
                process = record.process
                if process is not None and process.poll() is not None:
                    self._mark_route2_worker_unavailable_locked(record, sampled_at=sampled_at)
                    continue
                if result.cpu_seconds is None and process is not None and process.poll() is not None:
                    self._mark_route2_worker_unavailable_locked(record, sampled_at=sampled_at)
                    continue
                self._apply_route2_worker_telemetry_sample_locked(
                    record,
                    pid=result.pid,
                    cpu_seconds=result.cpu_seconds,
                    memory_bytes=result.memory_bytes,
                    io_read_bytes=result.io_read_bytes,
                    io_write_bytes=result.io_write_bytes,
                    total_cpu_cores=total_cpu_cores,
                    total_memory_bytes=total_memory_bytes,
                    sample_monotonic=sample_monotonic,
                    sampled_at=sampled_at,
                )

            previous_host_sample = self._last_host_cpu_jiffy_sample
            if current_host_sample is not None:
                self._last_host_cpu_jiffy_sample = current_host_sample
            elvern_owned_ffmpeg_cpu_cores_estimate = self._elvern_owned_ffmpeg_cpu_cores_for_host_pressure_locked(
                current_cpu_seconds_by_pid=elvern_owned_ffmpeg_cpu_seconds_by_pid,
                sample_monotonic=sample_monotonic,
            )
            host_cpu_pressure = _build_host_cpu_pressure_snapshot(
                previous_sample=previous_host_sample,
                current_sample=current_host_sample,
                route2_cpu_cores_used_total=self._route2_cpu_total_for_host_pressure_locked(),
                external_ffmpeg_process_count=ffmpeg_processes.external_process_count,
                route2_worker_ffmpeg_process_count=ffmpeg_processes.route2_worker_process_count,
                elvern_owned_ffmpeg_process_count=ffmpeg_processes.elvern_owned_process_count,
                elvern_owned_ffmpeg_cpu_cores_estimate=elvern_owned_ffmpeg_cpu_cores_estimate,
            )
            self._store_route2_resource_snapshot_locked(
                sampled_at_ts=sampled_at_ts,
                sampled_at=sampled_at,
                total_memory_bytes=total_memory_bytes,
                host_cpu_pressure=host_cpu_pressure,
            )

    def _route2_resource_telemetry_loop(self) -> None:
        while not self._manager_stop.is_set():
            try:
                self._sample_route2_resource_telemetry()
            except Exception:
                logger.debug("Route2 resource telemetry sample failed", exc_info=True)
            if self._manager_stop.wait(ROUTE2_RESOURCE_TELEMETRY_INTERVAL_SECONDS):
                break

    def _sample_host_cpu_pressure_locked(
        self,
        *,
        route2_cpu_cores_used_total: float | None,
        owned_route2_pids: set[int],
        sample_monotonic: float,
    ) -> _HostCpuPressureSnapshot:
        ffmpeg_processes = _classify_ffmpeg_processes(owned_route2_pids=owned_route2_pids)
        elvern_owned_ffmpeg_cpu_seconds_by_pid = _read_process_cpu_seconds_for_pids(ffmpeg_processes.elvern_owned_pids)
        current_sample = _read_host_cpu_jiffy_sample(sample_monotonic=sample_monotonic)
        previous_sample = self._last_host_cpu_jiffy_sample
        if current_sample is not None:
            self._last_host_cpu_jiffy_sample = current_sample
        elvern_owned_ffmpeg_cpu_cores_estimate = self._elvern_owned_ffmpeg_cpu_cores_for_host_pressure_locked(
            current_cpu_seconds_by_pid=elvern_owned_ffmpeg_cpu_seconds_by_pid,
            sample_monotonic=sample_monotonic,
        )
        return _build_host_cpu_pressure_snapshot(
            previous_sample=previous_sample,
            current_sample=current_sample,
            route2_cpu_cores_used_total=route2_cpu_cores_used_total,
            external_ffmpeg_process_count=ffmpeg_processes.external_process_count,
            route2_worker_ffmpeg_process_count=ffmpeg_processes.route2_worker_process_count,
            elvern_owned_ffmpeg_process_count=ffmpeg_processes.elvern_owned_process_count,
            elvern_owned_ffmpeg_cpu_cores_estimate=elvern_owned_ffmpeg_cpu_cores_estimate,
        )

    def _route2_budget_summary_locked(self) -> dict[str, object]:
        total_cpu_cores = _detect_total_cpu_cores()
        total_route2_budget_cores = _route2_cpu_upbound_cores_for_total(
            total_cpu_cores,
            self.settings.route2_cpu_budget_percent,
        )
        active_user_ids = sorted(
            {
                record.user_id
                for record in self._route2_workers.values()
                if record.state in {"queued", "running"}
            }
        )
        active_decoding_user_count = len(active_user_ids)
        active_route2_workload_count = len(
            [
                record
                for record in self._route2_workers.values()
                if record.state in {"queued", "running"}
            ]
        )
        per_user_budget_cores = (
            max(1, math.floor(total_route2_budget_cores / active_decoding_user_count))
            if active_decoding_user_count > 0
            else total_route2_budget_cores
        )
        return {
            "cpu_upbound_percent": self.settings.route2_cpu_budget_percent,
            "cpu_budget_percent": self.settings.route2_cpu_budget_percent,
            "total_cpu_cores": total_cpu_cores,
            "route2_cpu_upbound_cores": total_route2_budget_cores,
            "total_route2_budget_cores": total_route2_budget_cores,
            "active_decoding_user_count": active_decoding_user_count,
            "active_route2_workload_count": active_route2_workload_count,
            "active_user_ids": active_user_ids,
            "per_user_budget_cores": per_user_budget_cores,
            "max_worker_threads": self.settings.route2_max_worker_threads,
            "adaptive_max_worker_threads": self.settings.route2_adaptive_max_worker_threads,
            "active_worker_count": len(self._route2_running_workers_locked()),
            "queued_worker_count": len(self._route2_queued_workers_locked()),
        }

    def _route2_protected_min_threads_per_active_user(self) -> int:
        return max(1, int(getattr(self.settings, "route2_protected_min_threads_per_active_user", 2) or 2))

    def _route2_admission_min_worker_threads(self) -> int:
        return max(
            int(self.settings.route2_min_worker_threads),
            self._route2_protected_min_threads_per_active_user(),
        )

    def _route2_reserved_threads_for_admission_locked(self, record: Route2WorkerRecord) -> int:
        admission_min_threads = self._route2_admission_min_worker_threads()
        protected_floor = self._route2_protected_min_threads_per_active_user()
        if record.state == "queued":
            return admission_min_threads
        if record.state in {"running", "stopping"}:
            return max(int(record.assigned_threads or 0), protected_floor)
        return 0

    def _route2_next_runtime_rebalance_target_threads(self, assigned_threads: int) -> int:
        current_threads = max(1, int(assigned_threads or 0))
        if current_threads <= 5:
            return 6
        if current_threads <= 8:
            return 9
        if current_threads <= 11:
            return 12
        return current_threads

    def _route2_record_cpu_thread_limited(self, record: Route2WorkerRecord) -> bool:
        if record.cpu_cores_used is None:
            return False
        current_threads = max(1, int(record.assigned_threads or 0))
        cpu_cores_used = max(0.0, float(record.cpu_cores_used))
        return (
            cpu_cores_used / float(current_threads) >= 0.85
            or cpu_cores_used >= max(1.0, current_threads * 0.85)
        )

    def _route2_runway_delta_status_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> dict[str, object]:
        supply_model = self._route2_supply_model_locked(epoch)
        observation_seconds = max(0.0, float(supply_model["observation_seconds"]))
        mature = observation_seconds >= ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS
        if not mature:
            return {
                "runway_delta_per_second": None,
                "runway_delta_observation_seconds": observation_seconds,
                "runway_delta_mature": False,
            }
        supply_rate_x = max(0.0, float(supply_model["effective_rate_x"]))
        demand_rate_x = 1.0 if session.client_is_playing else 0.0
        return {
            "runway_delta_per_second": supply_rate_x - demand_rate_x,
            "runway_delta_observation_seconds": observation_seconds,
            "runway_delta_mature": True,
        }

    def _route2_bad_condition_reserve_status_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> dict[str, object]:
        actual_ready_end_seconds = self._route2_epoch_ready_end_seconds(session, epoch)
        duration_seconds = max(0.0, float(session.duration_seconds or 0.0))
        reserve_start_seconds = min(
            max(0.0, float(epoch.attach_position_seconds or session.target_position_seconds or 0.0)),
            duration_seconds,
        )
        reserve_target_ready_end_seconds = min(
            duration_seconds,
            reserve_start_seconds + ROUTE2_FULL_BAD_CONDITION_RESERVE_SECONDS,
        )
        reserve_required_seconds = max(0.0, reserve_target_ready_end_seconds - reserve_start_seconds)
        reserve_remaining_seconds = max(0.0, reserve_target_ready_end_seconds - actual_ready_end_seconds)
        runway_delta = self._route2_runway_delta_status_locked(session, epoch)
        supply_model = self._route2_supply_model_locked(epoch)
        supply_rate_x = max(0.0, float(supply_model["effective_rate_x"]))
        observation_seconds = max(0.0, float(supply_model["observation_seconds"]))
        metrics_mature = observation_seconds >= ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS
        is_full_route2 = (
            session.browser_playback.engine_mode == "route2"
            and session.browser_playback.playback_mode == "full"
        )
        coverage_starts_at_reserve = (
            epoch.init_published
            and epoch.contiguous_published_through_segment is not None
            and epoch.epoch_start_seconds <= reserve_start_seconds + 0.001
        )
        manifest_fully_published = (
            duration_seconds <= 0.0
            or actual_ready_end_seconds + 0.001 >= duration_seconds
        )
        reserve_satisfied = bool(
            is_full_route2
            and coverage_starts_at_reserve
            and (
                actual_ready_end_seconds + 0.001 >= reserve_target_ready_end_seconds
                or manifest_fully_published
            )
        )
        bad_condition_required = False
        bad_condition_reason: str | None = None
        if not is_full_route2:
            bad_condition_reason = "not_full_playback"
        elif not metrics_mature:
            bad_condition_reason = "metrics_immature"
        elif supply_rate_x < ROUTE2_BAD_CONDITION_SUPPLY_FLOOR_RATE_X:
            bad_condition_required = True
            bad_condition_reason = (
                "mature_supply_below_1_0"
                if supply_rate_x < ROUTE2_BAD_CONDITION_STRONG_SUPPLY_RATE_X
                else "mature_supply_below_1_05"
            )
        reserve_eta_seconds = None
        if bad_condition_required and not reserve_satisfied and supply_rate_x > 0.001:
            reserve_eta_seconds = reserve_remaining_seconds / supply_rate_x
        return {
            "bad_condition_reserve_required": bad_condition_required,
            "bad_condition_reason": bad_condition_reason,
            "bad_condition_supply_floor": ROUTE2_BAD_CONDITION_SUPPLY_FLOOR_RATE_X,
            "bad_condition_strong": bool(
                is_full_route2
                and metrics_mature
                and supply_rate_x < ROUTE2_BAD_CONDITION_STRONG_SUPPLY_RATE_X
            ),
            "reserve_start_seconds": reserve_start_seconds,
            "reserve_target_ready_end_seconds": reserve_target_ready_end_seconds,
            "reserve_actual_ready_end_seconds": actual_ready_end_seconds,
            "reserve_required_seconds": reserve_required_seconds if is_full_route2 else 0.0,
            "reserve_remaining_seconds": reserve_remaining_seconds if is_full_route2 else 0.0,
            "reserve_satisfied": reserve_satisfied,
            "reserve_blocks_admission": bool(bad_condition_required and not reserve_satisfied),
            "reserve_eta_seconds": reserve_eta_seconds,
            "runway_delta_per_second": runway_delta["runway_delta_per_second"],
            "runway_delta_observation_seconds": runway_delta["runway_delta_observation_seconds"],
            "runway_delta_mature": runway_delta["runway_delta_mature"],
        }

    def _route2_bad_condition_reserve_payload_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> dict[str, object]:
        status = self._route2_bad_condition_reserve_status_locked(session, epoch)
        rounded_payload: dict[str, object] = {
            "bad_condition_reserve_required": status["bad_condition_reserve_required"],
            "bad_condition_reason": status["bad_condition_reason"],
            "bad_condition_supply_floor": round(float(status["bad_condition_supply_floor"]), 3),
            "bad_condition_strong": status["bad_condition_strong"],
            "reserve_start_seconds": round(float(status["reserve_start_seconds"]), 2),
            "reserve_target_ready_end_seconds": round(float(status["reserve_target_ready_end_seconds"]), 2),
            "reserve_actual_ready_end_seconds": round(float(status["reserve_actual_ready_end_seconds"]), 2),
            "reserve_required_seconds": round(float(status["reserve_required_seconds"]), 2),
            "reserve_remaining_seconds": round(float(status["reserve_remaining_seconds"]), 2),
            "reserve_satisfied": status["reserve_satisfied"],
            "reserve_blocks_admission": status["reserve_blocks_admission"],
            "reserve_eta_seconds": (
                round(float(status["reserve_eta_seconds"]), 2)
                if status["reserve_eta_seconds"] is not None
                else None
            ),
            "runway_delta_per_second": (
                round(float(status["runway_delta_per_second"]), 3)
                if status["runway_delta_per_second"] is not None
                else None
            ),
            "runway_delta_observation_seconds": round(float(status["runway_delta_observation_seconds"]), 2),
            "runway_delta_mature": status["runway_delta_mature"],
        }
        return rounded_payload

    def _route2_bad_condition_reserve_protections_locked(self) -> list[dict[str, object]]:
        protections: list[dict[str, object]] = []
        for record in self._route2_workers.values():
            if record.state not in {"running", "stopping"}:
                continue
            session = self._sessions.get(record.session_id)
            if session is None or session.browser_playback.engine_mode != "route2":
                continue
            epoch = session.browser_playback.epochs.get(record.epoch_id)
            if epoch is None:
                continue
            status = self._route2_bad_condition_reserve_status_locked(session, epoch)
            if not bool(status["reserve_blocks_admission"]):
                continue
            (
                _published_end_seconds,
                _effective_playhead_seconds,
                _runway_seconds,
                _supply_rate_x,
                _observation_seconds,
                manifest_complete,
                _refill_in_progress,
            ) = self._route2_runtime_supply_metrics_locked(session, epoch)
            if manifest_complete:
                continue
            protections.append(
                {
                    "worker_id": record.worker_id,
                    "session_id": session.session_id,
                    "reason": status["bad_condition_reason"],
                    "reserve_remaining_seconds": status["reserve_remaining_seconds"],
                }
            )
        return protections

    def _route2_client_limited_locked(self, session: MobilePlaybackSession, epoch: PlaybackEpoch) -> bool:
        client_goodput = self._route2_client_goodput_locked(session)
        if not bool(client_goodput.get("confident")):
            return False
        server_goodput = self._route2_server_byte_goodput_locked(epoch)
        client_rate = float(client_goodput.get("safe_rate") or 0.0)
        server_rate = float(server_goodput.get("safe_rate") or 0.0)
        return (
            bool(server_goodput.get("confident"))
            and server_rate > 0.0
            and client_rate > 0.0
            and client_rate < (server_rate * 0.65)
        )

    def _route2_source_limited_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        *,
        cpu_thread_limited: bool,
    ) -> bool:
        if cpu_thread_limited:
            return False
        server_goodput = self._route2_server_byte_goodput_locked(epoch)
        client_goodput = self._route2_client_goodput_locked(session)
        weak_server_goodput = (
            bool(server_goodput.get("confident"))
            and bool(client_goodput.get("confident"))
            and float(server_goodput.get("safe_rate") or 0.0) > 0.0
            and float(client_goodput.get("safe_rate") or 0.0) > 0.0
            and float(server_goodput.get("safe_rate") or 0.0) <= float(client_goodput.get("safe_rate") or 0.0)
        )
        return session.source_kind == "cloud" or weak_server_goodput

    def _route2_closed_loop_required_runway_seconds(self, playback_mode: str) -> float:
        return 120.0 if playback_mode == "full" else 45.0

    def _route2_closed_loop_comfortable_runway_seconds(self, playback_mode: str) -> float:
        required = self._route2_closed_loop_required_runway_seconds(playback_mode)
        return max(required * 1.5, required + (60.0 if playback_mode == "full" else 20.0))

    def _route2_closed_loop_host_pressure_limited(
        self,
        *,
        host_cpu_pressure: _HostCpuPressureSnapshot | None,
        psi_snapshot: _LinuxPressureSnapshot | None,
        cgroup_snapshot: _CgroupTelemetrySnapshot | None,
    ) -> list[str]:
        reasons: list[str] = []
        if host_cpu_pressure is not None:
            if host_cpu_pressure.external_ffmpeg_process_count > 0:
                reasons.append("external_ffmpeg_process_present")
            external_cpu_cores = host_cpu_pressure.external_cpu_cores_used_estimate
            external_cpu_percent = host_cpu_pressure.external_cpu_percent_estimate
            if external_cpu_cores is not None and external_cpu_cores >= 4.0:
                reasons.append("external_cpu_pressure")
            if external_cpu_percent is not None and external_cpu_percent >= 0.20:
                reasons.append("external_cpu_percent_pressure")
        if psi_snapshot is not None:
            if (psi_snapshot.cpu_some_avg10 or 0.0) >= 5.0:
                reasons.append("psi_cpu_pressure")
            if (psi_snapshot.memory_some_avg10 or 0.0) >= 5.0:
                reasons.append("psi_memory_pressure")
        if cgroup_snapshot is not None:
            if (cgroup_snapshot.cpu_throttled_delta or 0) > 0 or (cgroup_snapshot.cpu_throttled_usec_delta or 0) > 0:
                reasons.append("cgroup_cpu_throttling")
            if (cgroup_snapshot.cpu_some_avg10 or 0.0) >= 5.0:
                reasons.append("cgroup_cpu_pressure")
            if (cgroup_snapshot.memory_some_avg10 or 0.0) >= 5.0:
                reasons.append("cgroup_memory_pressure")
        return reasons

    def _route2_closed_loop_io_publish_limited(
        self,
        *,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        progress: _Route2FfmpegProgressSnapshot | None,
        psi_snapshot: _LinuxPressureSnapshot | None,
        cgroup_snapshot: _CgroupTelemetrySnapshot | None,
    ) -> list[str]:
        actual_ready_end_seconds = self._route2_epoch_ready_end_seconds(session, epoch)
        progress_gap_seconds = 0.0
        if progress is not None and progress.out_time_seconds is not None and not progress.stale:
            progress_ready_end_seconds = epoch.epoch_start_seconds + float(progress.out_time_seconds)
            progress_gap_seconds = max(0.0, progress_ready_end_seconds - actual_ready_end_seconds)
        # Normal HLS/fMP4 publication can lag ffmpeg progress by a few segments.
        # Treat it as IO/publish-bound only with a larger gap plus slow publish,
        # or with independently high host/cgroup IO pressure.
        progress_significantly_ahead = progress_gap_seconds >= max(12.0, SEGMENT_DURATION_SECONDS * 6)
        publish_latency_high = (
            (epoch.publish_latency_max_seconds is not None and epoch.publish_latency_max_seconds >= 1.0)
            or (
                epoch.publish_segment_count > 0
                and (epoch.publish_latency_total_seconds / max(1, epoch.publish_segment_count)) >= 0.5
            )
        )
        reasons: list[str] = []
        if progress_significantly_ahead and publish_latency_high:
            reasons.append("ffmpeg_progress_ahead_of_publish_frontier_with_high_publish_latency")
        if psi_snapshot is not None:
            if (psi_snapshot.io_some_avg10 or 0.0) >= 5.0 or (psi_snapshot.io_full_avg10 or 0.0) >= 1.0:
                reasons.append("psi_io_pressure_high")
        if cgroup_snapshot is not None:
            if (cgroup_snapshot.io_some_avg10 or 0.0) >= 5.0 or (cgroup_snapshot.io_full_avg10 or 0.0) >= 1.0:
                reasons.append("cgroup_io_pressure_high")
        return reasons

    def _route2_estimated_source_bytes_per_media_second_locked(
        self,
        session: MobilePlaybackSession,
        record: Route2WorkerRecord,
    ) -> float | None:
        duration_seconds = float(session.duration_seconds or 0.0)
        if duration_seconds <= 0.0:
            return None
        file_size = 0
        try:
            item = get_media_item_record(self.settings, item_id=record.media_item_id)
        except Exception:  # noqa: BLE001 - diagnostic-only helper must not break status.
            item = None
        if item is not None:
            try:
                file_size = int(item.get("file_size") or 0)
            except (TypeError, ValueError):
                file_size = 0
        if file_size <= 0 and record.source_kind == "local":
            try:
                candidate = Path(session.source_locator)
                if candidate.is_file():
                    file_size = int(candidate.stat().st_size)
            except OSError:
                file_size = 0
        if file_size <= 0:
            return None
        return max(0.0, float(file_size) / duration_seconds)

    def _route2_source_feed_rate_locked(
        self,
        session: MobilePlaybackSession,
        record: Route2WorkerRecord,
    ) -> _Route2SourceFeedRate:
        missing: list[str] = []
        if not record.io_sample_mature or record.io_sample_stale:
            missing.extend(["source_feed_rate", "route2_source_observation_mature"])
            return _Route2SourceFeedRate(
                rate_x=None,
                available=False,
                mature=False,
                reason=None,
                missing_reason="route2_source_observation_not_mature",
                missing_metrics=missing,
            )
        source_bytes_per_second = record.io_read_bytes_per_second
        if source_bytes_per_second is None:
            missing.extend(["source_feed_rate", "route2_source_bytes_per_second"])
            return _Route2SourceFeedRate(
                rate_x=None,
                available=False,
                mature=True,
                reason=None,
                missing_reason="route2_source_bytes_per_second_unavailable",
                missing_metrics=missing,
            )
        estimated_source_bytes_per_media_second = self._route2_estimated_source_bytes_per_media_second_locked(
            session,
            record,
        )
        if estimated_source_bytes_per_media_second is None or estimated_source_bytes_per_media_second <= 0.0:
            missing.extend(["source_feed_rate", "estimated_source_bytes_per_media_second"])
            return _Route2SourceFeedRate(
                rate_x=None,
                available=False,
                mature=True,
                reason=None,
                missing_reason="estimated_source_bytes_per_media_second_unavailable",
                missing_metrics=missing,
            )
        measured_bytes_per_second = max(0.0, float(source_bytes_per_second))
        if record.source_kind == "local" and measured_bytes_per_second <= 0.0:
            # Linux /proc/<pid>/io counts physical storage reads. Local media served from page cache can
            # legitimately report zero physical reads while ffmpeg and the published frontier advance.
            missing.extend(["source_feed_rate", "local_proc_io_read_bytes_zero_page_cache_ambiguous"])
            return _Route2SourceFeedRate(
                rate_x=None,
                available=False,
                mature=True,
                reason=None,
                missing_reason="local_proc_io_zero_page_cache_ambiguous",
                missing_metrics=missing,
            )
        return _Route2SourceFeedRate(
            rate_x=measured_bytes_per_second / estimated_source_bytes_per_media_second,
            available=True,
            mature=True,
            reason="source_feed_measured_zero" if measured_bytes_per_second <= 0.0 else "source_feed_measured",
            missing_reason=None,
            missing_metrics=[],
        )

    def _route2_client_delivery_rate_x_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> tuple[float | None, list[str]]:
        missing: list[str] = []
        client_goodput = self._route2_client_goodput_locked(session)
        if not bool(client_goodput.get("confident")):
            missing.append("client_goodput")
            return None, missing
        server_goodput = self._route2_server_byte_goodput_locked(epoch)
        if not bool(server_goodput.get("confident")) or float(server_goodput.get("safe_rate") or 0.0) <= 0.0:
            missing.append("server_goodput")
            return None, missing
        client_rate = max(0.0, float(client_goodput.get("safe_rate") or 0.0))
        server_rate = max(0.0, float(server_goodput.get("safe_rate") or 0.0))
        return client_rate / server_rate if server_rate > 0.0 else None, missing

    def _route2_publish_efficiency_gap_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        progress: _Route2FfmpegProgressSnapshot | None,
    ) -> float | None:
        if progress is None or progress.out_time_seconds is None or progress.stale:
            return None
        progress_ready_end_seconds = epoch.epoch_start_seconds + float(progress.out_time_seconds)
        actual_ready_end_seconds = self._route2_epoch_ready_end_seconds(session, epoch)
        return max(0.0, progress_ready_end_seconds - actual_ready_end_seconds)

    def _route2_limiting_factor_payload(self, decision: _Route2LimitingFactorDecision) -> dict[str, object]:
        return {
            "limiting_factor_primary": decision.primary,
            "limiting_factor_confidence": round(decision.confidence, 3),
            "limiting_factor_scores": {
                key: round(float(value), 3)
                for key, value in decision.scores.items()
            },
            "limiting_factor_supporting_signals": list(decision.supporting_signals),
            "limiting_factor_blocking_signals": list(decision.blocking_signals),
            "limiting_factor_missing_metrics": list(decision.missing_metrics),
            "published_rate_x": round(float(decision.published_rate_x), 3)
            if decision.published_rate_x is not None
            else None,
            "encoder_rate_x": round(float(decision.encoder_rate_x), 3)
            if decision.encoder_rate_x is not None
            else None,
            "source_feed_rate_x": round(float(decision.source_feed_rate_x), 3)
            if decision.source_feed_rate_x is not None
            else None,
            "source_feed_rate_available": decision.source_feed_rate_available,
            "source_feed_rate_mature": decision.source_feed_rate_mature,
            "source_feed_rate_reason": decision.source_feed_rate_reason,
            "source_feed_rate_missing_reason": decision.source_feed_rate_missing_reason,
            "publish_efficiency_gap": round(float(decision.publish_efficiency_gap), 3)
            if decision.publish_efficiency_gap is not None
            else None,
            "client_delivery_rate_x": round(float(decision.client_delivery_rate_x), 3)
            if decision.client_delivery_rate_x is not None
            else None,
        }

    def _empty_route2_limiting_factor_decision(self, *, reason: str) -> _Route2LimitingFactorDecision:
        return _Route2LimitingFactorDecision(
            primary="metrics_immature",
            confidence=0.5,
            scores={
                "cpu_thread_score": 0.0,
                "source_score": 0.0,
                "io_publish_score": 0.0,
                "client_score": 0.0,
                "host_pressure_score": 0.0,
                "provider_error_score": 0.0,
                "metrics_immature_score": 0.8,
            },
            supporting_signals=[],
            blocking_signals=[],
            missing_metrics=[reason],
            published_rate_x=None,
            encoder_rate_x=None,
            source_feed_rate_x=None,
            source_feed_rate_available=False,
            source_feed_rate_mature=False,
            source_feed_rate_reason=None,
            source_feed_rate_missing_reason=reason,
            publish_efficiency_gap=None,
            client_delivery_rate_x=None,
        )

    def _evaluate_route2_limiting_factor_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        record: Route2WorkerRecord,
        *,
        progress: _Route2FfmpegProgressSnapshot | None = None,
        host_cpu_pressure: _HostCpuPressureSnapshot | None = None,
        psi_snapshot: _LinuxPressureSnapshot | None = None,
        cgroup_snapshot: _CgroupTelemetrySnapshot | None = None,
        adaptive_bottleneck_class: str | None = None,
        route2_cpu_cores_used_total: float | None = None,
        route2_cpu_upbound_cores: int | None = None,
        total_memory_bytes: int | None = None,
        route2_memory_bytes_total: int | None = None,
    ) -> _Route2LimitingFactorDecision:
        (
            _published_end_seconds,
            _effective_playhead_seconds,
            runway_seconds,
            supply_rate_x,
            observation_seconds,
            manifest_complete,
            refill_in_progress,
        ) = self._route2_runtime_supply_metrics_locked(session, epoch)
        reserve_status = self._route2_bad_condition_reserve_status_locked(session, epoch)
        runway_delta_per_second = reserve_status["runway_delta_per_second"]
        runway_delta_mature = bool(reserve_status["runway_delta_mature"])
        metrics_mature = observation_seconds >= ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS
        required_runway_seconds = self._route2_closed_loop_required_runway_seconds(record.playback_mode)
        source_feed = self._route2_source_feed_rate_locked(session, record)
        source_feed_rate_x = source_feed.rate_x
        client_delivery_rate_x, client_missing = self._route2_client_delivery_rate_x_locked(session, epoch)
        encoder_rate_x = (
            float(progress.speed_x)
            if progress is not None and progress.speed_x is not None and not progress.stale
            else None
        )
        publish_efficiency_gap = self._route2_publish_efficiency_gap_locked(session, epoch, progress)
        io_publish_reasons = self._route2_closed_loop_io_publish_limited(
            session=session,
            epoch=epoch,
            progress=progress,
            psi_snapshot=psi_snapshot,
            cgroup_snapshot=cgroup_snapshot,
        )
        host_pressure_reasons = self._route2_closed_loop_host_pressure_limited(
            host_cpu_pressure=host_cpu_pressure,
            psi_snapshot=psi_snapshot,
            cgroup_snapshot=cgroup_snapshot,
        )
        provider_error = bool(record.non_retryable_error or session.last_error)
        assigned_threads = max(1, int(record.assigned_threads or 1))
        cpu_thread_pressure = (
            record.cpu_cores_used is not None and float(record.cpu_cores_used) >= max(1.0, assigned_threads * 0.75)
        ) or adaptive_bottleneck_class in {"CPU_BOUND", "UNDER_SUPPLIED_BUT_CPU_LIMITED"}
        source_kind_factor = "cloud_source" if record.source_kind == "cloud" else "local_source" if record.source_kind == "local" else "source"
        supply_below_floor = supply_rate_x < ROUTE2_CLOSED_LOOP_HEALTH_FLOOR_RATE_X
        runway_declining = bool(
            runway_delta_mature
            and runway_delta_per_second is not None
            and float(runway_delta_per_second) < 0.0
        )
        boost_window_below_target = bool(refill_in_progress and runway_seconds < required_runway_seconds)
        memory_pressure = False
        if total_memory_bytes and route2_memory_bytes_total is not None:
            memory_pressure = (float(route2_memory_bytes_total) / float(total_memory_bytes)) >= 0.90
        pressure_primary = "host_pressure"
        if any(reason.startswith("external_") for reason in host_pressure_reasons):
            pressure_primary = "external_pressure"
        if any("cgroup_cpu" in reason for reason in host_pressure_reasons):
            pressure_primary = "cgroup_throttle"
        if memory_pressure or any("memory" in reason for reason in host_pressure_reasons):
            pressure_primary = "memory_pressure"
        source_confident_low = (
            source_feed.available
            and source_feed.mature
            and source_feed_rate_x is not None
            and source_feed_rate_x < ROUTE2_CLOSED_LOOP_HEALTH_FLOOR_RATE_X
        )
        source_confident_healthy = (
            source_feed.available
            and source_feed.mature
            and source_feed_rate_x is not None
            and source_feed_rate_x >= ROUTE2_CLOSED_LOOP_HEALTH_FLOOR_RATE_X
        )
        client_limited = self._route2_client_limited_locked(session, epoch)
        route2_headroom_cores = (
            float(route2_cpu_upbound_cores) - float(route2_cpu_cores_used_total)
            if route2_cpu_upbound_cores is not None and route2_cpu_cores_used_total is not None
            else None
        )
        headroom_available = route2_headroom_cores is None or route2_headroom_cores >= 1.0

        scores = {
            "cpu_thread_score": 0.0,
            "source_score": 0.0,
            "io_publish_score": 0.0,
            "client_score": 0.0,
            "host_pressure_score": 0.0,
            "provider_error_score": 0.0,
            "metrics_immature_score": 0.0,
        }
        supporting_signals: list[str] = []
        blocking_signals: list[str] = []
        missing_metrics = [*source_feed.missing_metrics, *client_missing]
        if progress is None or progress.stale or progress.speed_x is None:
            missing_metrics.append("ffmpeg_progress_speed_x")
        if not source_feed.available and record.source_kind == "cloud":
            missing_metrics.append("cloud_source_feed_rate_x")
        if route2_headroom_cores is None:
            missing_metrics.append("route2_cpu_headroom")
        if source_feed.reason:
            supporting_signals.append(source_feed.reason)
        if source_feed.missing_reason:
            supporting_signals.append(source_feed.missing_reason)

        if provider_error:
            scores["provider_error_score"] = 1.0
            supporting_signals.append("provider_or_source_error_present")
            return _Route2LimitingFactorDecision(
                primary="provider_error",
                confidence=0.98,
                scores=scores,
                supporting_signals=supporting_signals,
                blocking_signals=blocking_signals,
                missing_metrics=missing_metrics,
                published_rate_x=supply_rate_x,
                encoder_rate_x=encoder_rate_x,
                source_feed_rate_x=source_feed_rate_x,
                source_feed_rate_available=source_feed.available,
                source_feed_rate_mature=source_feed.mature,
                source_feed_rate_reason=source_feed.reason,
                source_feed_rate_missing_reason=source_feed.missing_reason,
                publish_efficiency_gap=publish_efficiency_gap,
                client_delivery_rate_x=client_delivery_rate_x,
            )
        if manifest_complete:
            supporting_signals.append("manifest_complete_or_fully_published")
            return _Route2LimitingFactorDecision(
                primary="manifest_complete",
                confidence=0.95,
                scores=scores,
                supporting_signals=supporting_signals,
                blocking_signals=blocking_signals,
                missing_metrics=missing_metrics,
                published_rate_x=supply_rate_x,
                encoder_rate_x=encoder_rate_x,
                source_feed_rate_x=source_feed_rate_x,
                source_feed_rate_available=source_feed.available,
                source_feed_rate_mature=source_feed.mature,
                source_feed_rate_reason=source_feed.reason,
                source_feed_rate_missing_reason=source_feed.missing_reason,
                publish_efficiency_gap=publish_efficiency_gap,
                client_delivery_rate_x=client_delivery_rate_x,
            )
        if not metrics_mature:
            scores["metrics_immature_score"] = 0.9
            supporting_signals.append("supply_observation_immature")
            return _Route2LimitingFactorDecision(
                primary="metrics_immature",
                confidence=0.85,
                scores=scores,
                supporting_signals=supporting_signals,
                blocking_signals=blocking_signals,
                missing_metrics=missing_metrics,
                published_rate_x=supply_rate_x,
                encoder_rate_x=encoder_rate_x,
                source_feed_rate_x=source_feed_rate_x,
                source_feed_rate_available=source_feed.available,
                source_feed_rate_mature=source_feed.mature,
                source_feed_rate_reason=source_feed.reason,
                source_feed_rate_missing_reason=source_feed.missing_reason,
                publish_efficiency_gap=publish_efficiency_gap,
                client_delivery_rate_x=client_delivery_rate_x,
            )

        encoder_healthy = encoder_rate_x is not None and encoder_rate_x >= ROUTE2_CLOSED_LOOP_HEALTH_FLOOR_RATE_X
        runway_not_declining = (
            not runway_delta_mature
            or runway_delta_per_second is None
            or float(runway_delta_per_second) >= 0.0
        )
        local_output_healthy = (
            record.source_kind == "local"
            and supply_rate_x >= ROUTE2_CLOSED_LOOP_HEALTH_FLOOR_RATE_X
            and runway_not_declining
            and (encoder_rate_x is None or encoder_healthy)
            and not io_publish_reasons
        )
        source_feed_can_explain_limiter = source_confident_low and not local_output_healthy
        if client_limited:
            scores["client_score"] = 0.86
            supporting_signals.append("client_goodput_below_server_goodput")
        if source_feed_can_explain_limiter and not cpu_thread_pressure:
            scores["source_score"] = 0.86
            supporting_signals.append(f"{source_kind_factor}_feed_below_1_05")
        elif source_feed_can_explain_limiter:
            scores["source_score"] = 0.62
            supporting_signals.append(f"{source_kind_factor}_feed_low_with_cpu_pressure")
        elif source_confident_low and local_output_healthy:
            supporting_signals.append("local_source_feed_low_ignored_because_output_healthy")
        elif (
            not source_feed.available
            and record.source_kind == "cloud"
            and supply_below_floor
            and not cpu_thread_pressure
        ):
            supporting_signals.append("cloud_source_feed_missing_with_low_supply_and_low_cpu")
        if io_publish_reasons:
            scores["io_publish_score"] = 0.88
            supporting_signals.extend(io_publish_reasons)
        if host_pressure_reasons or memory_pressure:
            scores["host_pressure_score"] = 0.82 if (supply_below_floor or boost_window_below_target or runway_declining) else 0.55
            blocking_signals.extend(host_pressure_reasons)
            if memory_pressure:
                blocking_signals.append("route2_memory_hard_pressure")
        if (supply_below_floor or boost_window_below_target or runway_declining) and cpu_thread_pressure:
            if not source_feed_can_explain_limiter and not client_limited and not io_publish_reasons:
                if source_confident_healthy:
                    scores["cpu_thread_score"] = 0.86
                elif record.source_kind == "local":
                    scores["cpu_thread_score"] = 0.74
                elif not source_feed.available:
                    scores["cpu_thread_score"] = 0.45
                    blocking_signals.append("cloud_source_feed_missing_limits_cpu_confidence")
                else:
                    scores["cpu_thread_score"] = 0.68
                supporting_signals.append("cpu_thread_pressure_with_supply_or_prepare_need")
                if not headroom_available:
                    blocking_signals.append("route2_cpu_headroom_insufficient")
                    scores["host_pressure_score"] = max(scores["host_pressure_score"], 0.72)
        healthy_supply = (
            supply_rate_x >= ROUTE2_CLOSED_LOOP_HEALTH_FLOOR_RATE_X
            and (not runway_delta_mature or runway_delta_per_second is None or float(runway_delta_per_second) >= 0.0)
            and not self._starvation_risk(session)
            and not self._stalled_recovery_needed(session)
        )
        if healthy_supply and not client_limited and not io_publish_reasons and not source_feed_can_explain_limiter:
            if not boost_window_below_target or not host_pressure_reasons:
                primary = "not_limited"
                confidence = 0.78
            else:
                primary = pressure_primary
                confidence = 0.78
        else:
            primary = "unknown"
            confidence = 0.55
            ranked: list[tuple[float, str]] = [
                (scores["provider_error_score"], "provider_error"),
                (scores["client_score"], "client"),
                (scores["source_score"], source_kind_factor if source_kind_factor in {"cloud_source", "local_source"} else "source"),
                (scores["io_publish_score"], "io_publish"),
                (scores["host_pressure_score"], pressure_primary),
                (scores["cpu_thread_score"], "cpu_thread"),
                (scores["metrics_immature_score"], "metrics_immature"),
            ]
            best_score, best_factor = max(ranked, key=lambda value: value[0])
            if best_score >= 0.55:
                primary = best_factor
                confidence = best_score
        if primary == "not_limited":
            supporting_signals.append("supply_at_or_above_1_05_and_runway_not_declining")
        if primary == "cpu_thread" and record.source_kind == "cloud" and source_confident_healthy:
            supporting_signals.append("cloud_source_feed_healthy_cpu_thread_limited")
        return _Route2LimitingFactorDecision(
            primary=primary,
            confidence=confidence,
            scores=scores,
            supporting_signals=supporting_signals,
            blocking_signals=blocking_signals,
            missing_metrics=list(dict.fromkeys(missing_metrics)),
            published_rate_x=supply_rate_x,
            encoder_rate_x=encoder_rate_x,
            source_feed_rate_x=source_feed_rate_x,
            source_feed_rate_available=source_feed.available,
            source_feed_rate_mature=source_feed.mature,
            source_feed_rate_reason=source_feed.reason,
            source_feed_rate_missing_reason=source_feed.missing_reason,
            publish_efficiency_gap=publish_efficiency_gap,
            client_delivery_rate_x=client_delivery_rate_x,
        )

    def _evaluate_route2_closed_loop_dry_run_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        record: Route2WorkerRecord,
        *,
        active_health: _Route2ActivePlaybackHealth | None = None,
        progress: _Route2FfmpegProgressSnapshot | None = None,
        host_cpu_pressure: _HostCpuPressureSnapshot | None = None,
        psi_snapshot: _LinuxPressureSnapshot | None = None,
        cgroup_snapshot: _CgroupTelemetrySnapshot | None = None,
        adaptive_bottleneck_class: str | None = None,
        route2_cpu_cores_used_total: float | None = None,
        route2_cpu_upbound_cores: int | None = None,
        total_memory_bytes: int | None = None,
        route2_memory_bytes_total: int | None = None,
    ) -> _Route2ClosedLoopDryRunDecision:
        assigned_threads = max(0, int(record.assigned_threads or 0))
        protected_floor = self._route2_protected_min_threads_per_active_user()
        (
            _published_end_seconds,
            _effective_playhead_seconds,
            runway_seconds,
            supply_rate_x,
            observation_seconds,
            manifest_complete,
            refill_in_progress,
        ) = self._route2_runtime_supply_metrics_locked(session, epoch)
        metrics_mature = observation_seconds >= ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS
        reserve_status = self._route2_bad_condition_reserve_status_locked(session, epoch)
        runway_delta_per_second = reserve_status["runway_delta_per_second"]
        runway_delta_mature = bool(reserve_status["runway_delta_mature"])
        required_runway_seconds = self._route2_closed_loop_required_runway_seconds(record.playback_mode)
        comfortable_runway_seconds = self._route2_closed_loop_comfortable_runway_seconds(record.playback_mode)
        limiting_factor = self._evaluate_route2_limiting_factor_locked(
            session,
            epoch,
            record,
            progress=progress,
            host_cpu_pressure=host_cpu_pressure,
            psi_snapshot=psi_snapshot,
            cgroup_snapshot=cgroup_snapshot,
            adaptive_bottleneck_class=adaptive_bottleneck_class,
            route2_cpu_cores_used_total=route2_cpu_cores_used_total,
            route2_cpu_upbound_cores=route2_cpu_upbound_cores,
            total_memory_bytes=total_memory_bytes,
            route2_memory_bytes_total=route2_memory_bytes_total,
        )
        cpu_thread_pressure = self._route2_record_cpu_thread_limited(record) or adaptive_bottleneck_class in {
            "CPU_BOUND",
            "UNDER_SUPPLIED_BUT_CPU_LIMITED",
        }
        provider_error = limiting_factor.primary == "provider_error"
        client_limited = limiting_factor.primary == "client"
        source_limited = limiting_factor.primary in {"source", "cloud_source", "local_source"}
        io_publish_reasons = self._route2_closed_loop_io_publish_limited(
            session=session,
            epoch=epoch,
            progress=progress,
            psi_snapshot=psi_snapshot,
            cgroup_snapshot=cgroup_snapshot,
        )
        io_publish_limited = limiting_factor.primary == "io_publish"
        host_pressure_reasons = self._route2_closed_loop_host_pressure_limited(
            host_cpu_pressure=host_cpu_pressure,
            psi_snapshot=psi_snapshot,
            cgroup_snapshot=cgroup_snapshot,
        )
        host_pressure_limited = limiting_factor.primary in {
            "host_pressure",
            "external_pressure",
            "memory_pressure",
            "cgroup_throttle",
        }
        cpu_thread_factor_plausible = limiting_factor.primary == "cpu_thread" or (
            limiting_factor.primary == "not_limited" and cpu_thread_pressure
        )
        cpu_thread_limited = cpu_thread_factor_plausible and not source_limited and not client_limited and not io_publish_limited
        starvation_risk = self._starvation_risk(session)
        stalled_recovery_needed = self._stalled_recovery_needed(session)
        below_health_floor = supply_rate_x < ROUTE2_CLOSED_LOOP_HEALTH_FLOOR_RATE_X
        declining_low_runway = bool(
            runway_delta_mature
            and runway_delta_per_second is not None
            and float(runway_delta_per_second) < 0.0
            and runway_seconds <= WATCH_REFILL_TARGET_SECONDS
        )
        recovery_at_risk = bool(
            (starvation_risk or stalled_recovery_needed)
            and runway_seconds <= WATCH_LOW_WATERMARK_SECONDS
            and supply_rate_x < ROUTE2_CLOSED_LOOP_HEALTH_FLOOR_RATE_X
        )
        reasons: list[str] = []
        role = "neutral"
        confidence = 0.55
        primary_bottleneck = "unknown"
        needs_resource = False
        needs_resource_reason: str | None = None
        prepare_boost_needed = False
        prepare_boost_target_threads: int | None = None
        downshift_candidate = False
        downshift_target_threads: int | None = None
        donor_candidate = False
        theoretical_donate_threads = 0
        protected_reason: str | None = None
        admission_should_block_new_users = False
        admission_block_reason: str | None = None
        admission_block_reasons: list[str] = []
        boost_blocked = False
        boost_blockers: list[str] = []
        boost_warning_reasons: list[str] = []

        if provider_error:
            role = "provider_error"
            primary_bottleneck = "provider"
            reasons.append("provider_or_source_error_present")
            confidence = 0.95
        elif manifest_complete:
            role = "manifest_complete"
            primary_bottleneck = "complete"
            reasons.append("manifest_complete_or_fully_published")
            confidence = 0.9
        elif not metrics_mature:
            role = "metrics_immature"
            primary_bottleneck = "metrics_immature"
            reasons.append("supply_observation_immature")
            confidence = 0.8
        elif bool(reserve_status["bad_condition_reserve_required"]) and not bool(reserve_status["reserve_satisfied"]):
            role = "protected_bad_condition_reserve"
            primary_bottleneck = "cpu_thread" if cpu_thread_limited else "unknown"
            protected_reason = str(reserve_status["bad_condition_reason"] or "bad_condition_reserve_unsatisfied")
            admission_should_block_new_users = True
            admission_block_reason = "active_bad_condition_reserve_protection"
            admission_block_reasons.append("active_bad_condition_reserve_protection")
            prepare_boost_needed = bool(cpu_thread_limited and not host_pressure_limited and not source_limited and not client_limited)
            prepare_boost_target_threads = (
                self._route2_next_runtime_rebalance_target_threads(assigned_threads)
                if prepare_boost_needed
                else None
            )
            reasons.append("full_bad_condition_reserve_required_unsatisfied")
            confidence = 0.9
        elif below_health_floor or declining_low_runway or recovery_at_risk:
            needs_resource = True
            admission_should_block_new_users = True
            admission_block_reason = "active_stream_health_protection"
            admission_block_reasons.append("active_stream_health_protection")
            if client_limited:
                role = "client_bound"
                primary_bottleneck = "client"
                needs_resource_reason = "client_limited"
                admission_should_block_new_users = False
                admission_block_reason = None
                admission_block_reasons.clear()
                reasons.append("client_goodput_or_stall_limiter")
            elif source_limited:
                role = "source_bound"
                primary_bottleneck = "source"
                needs_resource_reason = "source_limited"
                admission_should_block_new_users = False
                admission_block_reason = None
                admission_block_reasons.clear()
                reasons.append("source_provider_throughput_limiter")
            elif io_publish_limited:
                role = "io_or_publish_bound"
                primary_bottleneck = "io_publish"
                needs_resource_reason = "io_or_publish_limited_supply_below_1_05"
                reasons.extend(io_publish_reasons)
            elif host_pressure_limited:
                role = "host_pressure_limited"
                primary_bottleneck = limiting_factor.primary
                needs_resource_reason = "host_pressure_limited_supply_below_1_05"
                reasons.extend(limiting_factor.blocking_signals or host_pressure_reasons)
            elif cpu_thread_limited:
                role = "needs_resource"
                primary_bottleneck = "cpu_thread"
                needs_resource_reason = (
                    "cpu_thread_limited_supply_below_1_05"
                    if below_health_floor
                    else "cpu_thread_limited_runway_declining_or_recovery"
                )
                prepare_boost_needed = True
                prepare_boost_target_threads = self._route2_next_runtime_rebalance_target_threads(assigned_threads)
                reasons.append(
                    "mature_supply_below_1_05_cpu_thread_limited"
                    if below_health_floor
                    else "runway_declining_or_recovery_cpu_thread_limited"
                )
            else:
                role = "needs_resource"
                primary_bottleneck = "unknown"
                needs_resource_reason = "supply_below_1_05_or_declining_runway"
                reasons.append("mature_supply_below_1_05_without_specific_limiter")
            confidence = 0.82
        elif (
            runway_seconds < required_runway_seconds
            and refill_in_progress
            and (cpu_thread_limited or (host_pressure_limited and cpu_thread_pressure))
            and not source_limited
            and not client_limited
        ):
            if host_pressure_limited:
                role = "host_pressure_limited"
                primary_bottleneck = limiting_factor.primary
                prepare_boost_needed = False
                boost_blocked = True
                boost_blockers.extend(["host_pressure_blocks_prepare_boost", *(limiting_factor.blocking_signals or host_pressure_reasons)])
                reasons.append("host_pressure_blocks_prepare_boost")
                reasons.extend(limiting_factor.blocking_signals or host_pressure_reasons)
                confidence = 0.8
            elif io_publish_limited:
                role = "io_or_publish_bound"
                primary_bottleneck = "io_publish"
                prepare_boost_needed = False
                boost_blocked = True
                boost_blockers.extend(io_publish_reasons)
                reasons.extend(io_publish_reasons)
                confidence = 0.78
            else:
                role = "prepare_boost_needed"
                primary_bottleneck = "cpu_thread"
                prepare_boost_needed = True
                prepare_boost_target_threads = self._route2_next_runtime_rebalance_target_threads(assigned_threads)
                reasons.append("runway_below_startup_target_and_cpu_thread_limited")
                confidence = 0.78
        elif io_publish_limited:
            role = "io_or_publish_bound"
            primary_bottleneck = "io_publish"
            reasons.extend(io_publish_reasons)
            confidence = 0.78
        else:
            role = "steady_state_maintenance"
            primary_bottleneck = "unknown"
            reasons.append("supply_at_or_above_1_05_and_runway_not_declining")
            if host_pressure_reasons:
                reasons.append("host_pressure_warning")
                reasons.extend(limiting_factor.blocking_signals or host_pressure_reasons)
                boost_warning_reasons.extend(limiting_factor.blocking_signals or host_pressure_reasons)
            confidence = 0.72
            if (
                not host_pressure_limited
                and supply_rate_x >= ROUTE2_CLOSED_LOOP_DOWNSHIFT_RATE_X
                and observation_seconds >= 20.0
                and runway_seconds >= comfortable_runway_seconds
                and (
                    not runway_delta_mature
                    or runway_delta_per_second is None
                    or float(runway_delta_per_second) >= 0.0
                )
                and assigned_threads > protected_floor
                and not starvation_risk
                and not stalled_recovery_needed
            ):
                downshift_candidate = True
                downshift_target_threads = protected_floor
                role = "downshift_candidate"
                reasons.append("supply_above_1_10_with_comfortable_runway")
                confidence = 0.82
            if (
                not host_pressure_limited
                and supply_rate_x >= ROUTE2_CLOSED_LOOP_DONOR_RATE_X
                and runway_seconds >= comfortable_runway_seconds
                and assigned_threads > protected_floor
                and not bool(reserve_status["bad_condition_reserve_required"] and not reserve_status["reserve_satisfied"])
                and not source_limited
                and not client_limited
                and not provider_error
            ):
                donor_candidate = True
                theoretical_donate_threads = max(0, assigned_threads - protected_floor)
                role = "donor_candidate"
                reasons.append("high_supply_and_runway_theoretical_donor")
                confidence = 0.86

        donor_score = 0.0
        if donor_candidate:
            donor_score = (
                (max(0.0, supply_rate_x - ROUTE2_CLOSED_LOOP_DONOR_RATE_X) * 100.0)
                + max(0.0, runway_seconds - comfortable_runway_seconds)
                + (theoretical_donate_threads * 10.0)
            )

        if active_health is not None and active_health.admission_blocking:
            admission_should_block_new_users = True
            admission_block_reason = admission_block_reason or "active_stream_health_protection"
            if "active_stream_health_protection" not in admission_block_reasons:
                admission_block_reasons.append("active_stream_health_protection")
            if role in {"steady_state_maintenance", "downshift_candidate", "donor_candidate"}:
                role = "needs_resource"
                primary_bottleneck = "cpu_thread" if active_health.cpu_thread_limited else "unknown"
                needs_resource = True
                needs_resource_reason = active_health.status
                donor_candidate = False
                theoretical_donate_threads = 0
                downshift_candidate = False
                downshift_target_threads = None
                reasons.append("active_health_guard_blocks_admission")

        return _Route2ClosedLoopDryRunDecision(
            role=role,
            reasons=reasons or ["no_specific_closed_loop_reason"],
            confidence=confidence,
            prepare_boost_needed=prepare_boost_needed,
            prepare_boost_target_threads=prepare_boost_target_threads,
            downshift_candidate=downshift_candidate,
            downshift_target_threads=downshift_target_threads,
            needs_resource=needs_resource,
            needs_resource_reason=needs_resource_reason,
            donor_candidate=donor_candidate,
            theoretical_donate_threads=theoretical_donate_threads,
            protected_reason=protected_reason,
            admission_should_block_new_users=admission_should_block_new_users,
            admission_block_reason=admission_block_reason,
            admission_block_reasons=admission_block_reasons,
            boost_blocked=boost_blocked,
            boost_blockers=boost_blockers,
            boost_warning_reasons=boost_warning_reasons,
            limiting_factor=limiting_factor,
            primary_bottleneck=primary_bottleneck,
            donor_score=donor_score,
        )

    def _closed_loop_dry_run_payload(self, decision: _Route2ClosedLoopDryRunDecision) -> dict[str, object]:
        return {
            "closed_loop_role": decision.role,
            "closed_loop_reasons": list(decision.reasons),
            "closed_loop_confidence": round(decision.confidence, 3),
            "closed_loop_prepare_boost_needed": decision.prepare_boost_needed,
            "closed_loop_prepare_boost_target_threads": decision.prepare_boost_target_threads,
            "closed_loop_downshift_candidate": decision.downshift_candidate,
            "closed_loop_downshift_target_threads": decision.downshift_target_threads,
            "closed_loop_needs_resource": decision.needs_resource,
            "closed_loop_needs_resource_reason": decision.needs_resource_reason,
            "closed_loop_donor_candidate": decision.donor_candidate,
            "closed_loop_donor_rank": None,
            "closed_loop_theoretical_donate_threads": decision.theoretical_donate_threads,
            "closed_loop_protected_reason": decision.protected_reason,
            "closed_loop_admission_should_block_new_users": decision.admission_should_block_new_users,
            "closed_loop_admission_hard_block": decision.admission_should_block_new_users,
            "closed_loop_admission_block_reason": decision.admission_block_reason,
            "closed_loop_admission_block_reasons": list(decision.admission_block_reasons),
            "closed_loop_boost_blocked": decision.boost_blocked,
            "closed_loop_boost_blockers": list(decision.boost_blockers),
            "closed_loop_boost_warning_reasons": list(decision.boost_warning_reasons),
            "closed_loop_primary_bottleneck": decision.primary_bottleneck,
            **self._route2_limiting_factor_payload(decision.limiting_factor),
        }

    def _closed_loop_runtime_rebalance_payload(self, decision: _Route2ClosedLoopDryRunDecision) -> dict[str, object]:
        if decision.role == "prepare_boost_needed":
            return {
                "runtime_rebalance_role": "needs_resource",
                "runtime_rebalance_reason": "Closed-loop dry-run says this workload would benefit from prepare boost.",
                "runtime_rebalance_target_threads": decision.prepare_boost_target_threads,
                "runtime_rebalance_can_donate_threads": 0,
                "runtime_rebalance_priority": 70,
            }
        if decision.role == "needs_resource":
            return {
                "runtime_rebalance_role": "needs_resource",
                "runtime_rebalance_reason": "Closed-loop dry-run says this workload needs resource protection.",
                "runtime_rebalance_target_threads": decision.prepare_boost_target_threads,
                "runtime_rebalance_can_donate_threads": 0,
                "runtime_rebalance_priority": 80,
            }
        if decision.role == "protected_bad_condition_reserve":
            return {
                "runtime_rebalance_role": "needs_resource",
                "runtime_rebalance_reason": "Closed-loop dry-run protects this workload's unsatisfied Full bad-condition reserve.",
                "runtime_rebalance_target_threads": decision.prepare_boost_target_threads,
                "runtime_rebalance_can_donate_threads": 0,
                "runtime_rebalance_priority": 90,
            }
        if decision.role in {"downshift_candidate", "donor_candidate"}:
            return {
                "runtime_rebalance_role": "donor_candidate",
                "runtime_rebalance_reason": "Closed-loop dry-run says this workload is only a theoretical future donor.",
                "runtime_rebalance_target_threads": decision.downshift_target_threads,
                "runtime_rebalance_can_donate_threads": decision.theoretical_donate_threads,
                "runtime_rebalance_priority": 20,
            }
        return {
            "runtime_rebalance_role": "neutral",
            "runtime_rebalance_reason": "Closed-loop dry-run does not mark this workload as a donor or recipient.",
            "runtime_rebalance_target_threads": None,
            "runtime_rebalance_can_donate_threads": 0,
            "runtime_rebalance_priority": 0,
        }

    def _route2_shared_supply_output_contract_fingerprint_locked(
        self,
        session: MobilePlaybackSession,
    ) -> dict[str, object]:
        missing_fields: list[str] = []
        notes = ["output_contract_fingerprint_uses_sanitized_route2_output_contract"]
        profile = MOBILE_PROFILES.get(session.profile)
        if profile is None:
            missing_fields.append("profile")
        playback_mode = str(session.browser_playback.playback_mode or session.playback_mode or "").strip()
        if playback_mode not in {"full", "lite"}:
            missing_fields.append("playback_mode")
        if missing_fields:
            summary = {
                "version": ROUTE2_OUTPUT_CONTRACT_VERSION,
                "profile": str(session.profile or ""),
                "playback_mode": playback_mode,
                "status": "incomplete",
            }
            return {
                "fingerprint": None,
                "version": ROUTE2_OUTPUT_CONTRACT_VERSION,
                "missing_fields": sorted(set(missing_fields)),
                "summary": summary,
                "blockers": ["output_contract_incomplete"],
                "notes": notes,
            }
        keyframe_interval = int(SEGMENT_DURATION_SECONDS * 24)
        scale_filter_contract = {
            "max_width": profile.max_width,
            "max_height": profile.max_height,
            "force_original_aspect_ratio": "decrease",
        }
        video_contract = {
            "codec": "libx264",
            "preset": "superfast",
            "profile": "high",
            "level": profile.level,
            "pix_fmt": "yuv420p",
            "crf": profile.crf,
            "maxrate": profile.maxrate,
            "bufsize": profile.bufsize,
            "scale": scale_filter_contract,
            "gop_frames": keyframe_interval,
            "keyint_min": keyframe_interval,
            "sc_threshold": 0,
            "force_key_frames": f"expr:gte(t,n_forced*{SEGMENT_DURATION_SECONDS})",
        }
        audio_contract = {
            "codec": "aac",
            "channels": 2,
            "sample_rate": 48000,
            "bitrate": "160k",
        }
        hls_contract = {
            "format": "hls",
            "segment_duration_seconds": SEGMENT_DURATION_SECONDS,
            "list_size": 0,
            "segment_type": "fmp4",
            "init_filename": "init.mp4",
            "flags": "independent_segments+temp_file",
            "start_number": 0,
        }
        contract = {
            "version": ROUTE2_OUTPUT_CONTRACT_VERSION,
            "engine_mode": "route2",
            "profile": session.profile,
            "playback_mode": playback_mode,
            "active_strategy": "full_transcode",
            "copy_or_remux_active": False,
            "video": video_contract,
            "audio": audio_contract,
            "hls": hls_contract,
            "timestamp_policy": {
                "epoch_seek": "input_ss_before_decode",
                "output_ts_offset": "0.000",
                "muxpreload": "0",
                "muxdelay": "0",
                "timeline_policy": "epoch_relative_zero_offset",
                "segment_numbering": "epoch_relative_start_number_0",
            },
            "stream_selection": {
                "video": "0:v:0",
                "audio": "0:a:0?",
                "subtitles": "disabled",
                "data": "disabled",
            },
            "ffmpeg_progress_telemetry": "enabled_out_of_band",
            "source_identity": "covered_by_media_item_and_source_fingerprint",
        }
        encoded = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
        fingerprint = hashlib.sha256(encoded).hexdigest()[:24]
        summary = {
            "version": ROUTE2_OUTPUT_CONTRACT_VERSION,
            "profile": session.profile,
            "playback_mode": playback_mode,
            "active_strategy": "full_transcode",
            "video": {
                "codec": video_contract["codec"],
                "preset": video_contract["preset"],
                "profile": video_contract["profile"],
                "level": video_contract["level"],
                "pix_fmt": video_contract["pix_fmt"],
                "crf": video_contract["crf"],
                "maxrate": video_contract["maxrate"],
                "bufsize": video_contract["bufsize"],
                "max_width": profile.max_width,
                "max_height": profile.max_height,
            },
            "audio": audio_contract,
            "hls": {
                "segment_duration_seconds": SEGMENT_DURATION_SECONDS,
                "segment_type": hls_contract["segment_type"],
                "init_filename": hls_contract["init_filename"],
                "flags": hls_contract["flags"],
            },
            "timeline": "epoch_relative_zero_offset",
            "source_identity": "media_item_and_source_fingerprint",
            "excluded": [
                "source_path",
                "cloud_url",
                "tokens",
                "cookies",
                "session_id",
                "epoch_id",
                "output_paths",
                "complete_ffmpeg_invocation",
            ],
        }
        return {
            "fingerprint": fingerprint,
            "version": ROUTE2_OUTPUT_CONTRACT_VERSION,
            "missing_fields": [],
            "summary": summary,
            "blockers": [],
            "notes": notes,
        }

    def _route2_shared_supply_group_key_locked(
        self,
        session: MobilePlaybackSession,
    ) -> tuple[str | None, list[str], list[str]]:
        blockers: list[str] = []
        notes = ["level_0_detection_only", "route2_output_is_session_epoch_scoped"]
        if not str(session.source_fingerprint or "").strip():
            blockers.append("missing_source_fingerprint")
        output_contract = self._route2_shared_supply_output_contract_fingerprint_locked(session)
        output_contract_fingerprint = output_contract.get("fingerprint")
        output_contract_blockers = [str(item) for item in output_contract.get("blockers") or []]
        output_contract_notes = [str(item) for item in output_contract.get("notes") or []]
        output_contract_missing_fields = [str(item) for item in output_contract.get("missing_fields") or []]
        blockers.extend(output_contract_blockers)
        if output_contract_missing_fields:
            blockers.append("output_contract_incomplete")
        notes.extend(output_contract_notes)
        if output_contract_fingerprint is None or "missing_source_fingerprint" in blockers:
            return None, sorted(set(blockers)), sorted(set(notes))
        group_payload = {
            "version": ROUTE2_SHARED_SUPPLY_GROUP_VERSION,
            "media_item_id": int(session.media_item_id),
            "source_fingerprint": str(session.source_fingerprint),
            "source_kind": str(session.source_kind),
            "profile": str(session.profile),
            "playback_mode": str(session.browser_playback.playback_mode),
            "cache_key": str(session.cache_key),
            "output_contract_fingerprint": output_contract_fingerprint,
            "segment_duration_seconds": SEGMENT_DURATION_SECONDS,
        }
        encoded = json.dumps(group_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"r2ss:v2:{hashlib.sha256(encoded).hexdigest()[:32]}", sorted(set(blockers)), sorted(set(notes))

    def _route2_shared_supply_cloud_provider_blockers_locked(
        self,
        session: MobilePlaybackSession,
    ) -> list[str]:
        with get_connection(self.settings) as connection:
            row = connection.execute(
                """
                SELECT
                    s.last_error,
                    account.id AS google_account_id,
                    account.refresh_token
                FROM media_items m
                LEFT JOIN library_sources s
                  ON s.id = m.library_source_id
                LEFT JOIN google_drive_accounts account
                  ON account.id = s.google_drive_account_id
                WHERE m.id = ?
                LIMIT 1
                """,
                (session.media_item_id,),
            ).fetchone()
        if row is None:
            return ["permission_unverified"]
        google_account_id = int(row["google_account_id"] or 0)
        if google_account_id <= 0 or not str(row["refresh_token"] or "").strip():
            return ["provider_access_unavailable"]
        last_error = str(row["last_error"] or "").strip()
        if _is_non_retryable_cloud_source_error(last_error):
            return ["provider_access_unavailable"]
        return []

    def _route2_shared_supply_permission_status_locked(
        self,
        session: MobilePlaybackSession,
    ) -> tuple[str, list[str]]:
        try:
            detail = get_media_item_detail(
                self.settings,
                user_id=session.user_id,
                item_id=session.media_item_id,
            )
        except Exception:  # noqa: BLE001
            detail = None
        if detail is None:
            with get_connection(self.settings) as connection:
                exists = connection.execute(
                    "SELECT 1 FROM media_items WHERE id = ? LIMIT 1",
                    (session.media_item_id,),
                ).fetchone()
            if exists is None:
                return "permission_unverified", ["permission_unverified"]
            return "permission_blocked", ["permission_blocked"]
        with get_connection(self.settings) as connection:
            hidden_source = connection.execute(
                """
                SELECT 1
                FROM media_items m
                JOIN user_hidden_library_sources h
                  ON h.library_source_id = m.library_source_id
                 AND h.user_id = ?
                WHERE m.id = ?
                LIMIT 1
                """,
                (session.user_id, session.media_item_id),
            ).fetchone()
        if hidden_source is not None:
            return "permission_blocked", ["permission_blocked"]
        if bool(detail.get("hidden_for_user")) or bool(detail.get("hidden_globally")):
            return "permission_blocked", ["permission_blocked"]
        if session.source_kind == "cloud":
            provider_blockers = self._route2_shared_supply_cloud_provider_blockers_locked(session)
            if provider_blockers:
                if "permission_unverified" in provider_blockers:
                    return "permission_unverified", provider_blockers
                return "provider_access_unavailable", provider_blockers
            return "verified_cloud", []
        return "verified_local", []

    def _route2_shared_supply_workload_locked(
        self,
        record: Route2WorkerRecord,
    ) -> _Route2SharedSupplyWorkload:
        session = self._sessions.get(record.session_id)
        epoch = (
            session.browser_playback.epochs.get(record.epoch_id)
            if session is not None and session.browser_playback.engine_mode == "route2"
            else None
        )
        blockers: list[str] = []
        notes: list[str] = []
        permission_status = "permission_unverified"
        group_key = None
        output_contract_fingerprint = None
        output_contract_missing_fields: list[str] = []
        output_contract_summary: dict[str, object] = {}
        output_contract_version = ROUTE2_OUTPUT_CONTRACT_VERSION
        init_metadata = build_route2_init_metadata(None)
        source_fingerprint = ""
        source_kind = record.source_kind
        profile = record.profile
        playback_mode = record.playback_mode
        epoch_start_seconds = None
        prepared_ranges = list(record.prepared_ranges)
        stopped_or_expired = record.state in {"stopped", "expired", "failed"}
        target_position_seconds = float(record.target_position_seconds or 0.0)
        media_item_id = int(record.media_item_id)
        if session is None:
            blockers.append("route2_session_missing")
        else:
            source_fingerprint = str(session.source_fingerprint or "")
            source_kind = str(session.source_kind or record.source_kind)
            profile = str(session.profile or record.profile)
            playback_mode = str(session.browser_playback.playback_mode or record.playback_mode)
            target_position_seconds = float(session.target_position_seconds or record.target_position_seconds or 0.0)
            media_item_id = int(session.media_item_id)
            permission_status, permission_blockers = self._route2_shared_supply_permission_status_locked(session)
            blockers.extend(permission_blockers)
            output_contract = self._route2_shared_supply_output_contract_fingerprint_locked(session)
            output_contract_fingerprint = (
                str(output_contract.get("fingerprint")) if output_contract.get("fingerprint") else None
            )
            output_contract_missing_fields = [str(item) for item in output_contract.get("missing_fields") or []]
            output_contract_summary = dict(output_contract.get("summary") or {})
            output_contract_version = str(output_contract.get("version") or ROUTE2_OUTPUT_CONTRACT_VERSION)
            group_key, group_blockers, group_notes = self._route2_shared_supply_group_key_locked(session)
            blockers.extend(group_blockers)
            notes.extend(group_notes)
            stopped_or_expired = stopped_or_expired or session.state in {"stopped", "expired", "failed"}
        if epoch is None:
            blockers.append("route2_epoch_missing")
        else:
            epoch_start_seconds = float(epoch.epoch_start_seconds)
            if not prepared_ranges:
                prepared_ranges = self._route2_epoch_prepared_ranges_locked(session, epoch) if session is not None else []
            init_metadata = build_route2_init_metadata(epoch.published_init_path if epoch.init_published else None)
            if epoch.stop_requested:
                blockers.append("explicit_stop_requested")
            if _is_non_retryable_cloud_source_error(epoch.last_error):
                blockers.append("provider_access_unavailable")
                permission_status = "provider_access_unavailable"
        if stopped_or_expired:
            blockers.append("stopped_or_expired_workload")
        return _Route2SharedSupplyWorkload(
            worker_id=record.worker_id,
            workload_id=f"{record.session_id}:{record.epoch_id}",
            session_id=record.session_id,
            epoch_id=record.epoch_id,
            user_id=record.user_id,
            media_item_id=media_item_id,
            source_fingerprint=source_fingerprint,
            source_kind=source_kind,
            profile=profile,
            playback_mode=playback_mode,
            output_contract_fingerprint=output_contract_fingerprint,
            output_contract_version=output_contract_version,
            output_contract_missing_fields=sorted(set(output_contract_missing_fields)),
            output_contract_summary=output_contract_summary,
            init_metadata=init_metadata,
            group_key=group_key,
            permission_status=permission_status,
            blockers=sorted(set(blockers)),
            notes=sorted(set(notes)),
            epoch_start_seconds=epoch_start_seconds,
            target_position_seconds=target_position_seconds,
            prepared_ranges=prepared_ranges,
            stopped_or_expired=stopped_or_expired,
        )

    def _route2_shared_supply_pair_blockers(
        self,
        first: _Route2SharedSupplyWorkload,
        second: _Route2SharedSupplyWorkload,
    ) -> list[str]:
        blockers: list[str] = []
        if first.media_item_id != second.media_item_id:
            blockers.append("media_item_mismatch")
        if first.source_fingerprint != second.source_fingerprint:
            blockers.append("source_fingerprint_mismatch")
        if first.source_kind != second.source_kind:
            blockers.append("source_kind_mismatch")
        if first.profile != second.profile:
            blockers.append("profile_mismatch")
        if first.playback_mode != second.playback_mode:
            blockers.append("playback_mode_mismatch")
        if first.output_contract_missing_fields or second.output_contract_missing_fields:
            blockers.append("output_contract_incomplete")
        if (
            first.output_contract_fingerprint
            and second.output_contract_fingerprint
            and first.output_contract_fingerprint != second.output_contract_fingerprint
        ):
            blockers.append("output_contract_mismatch")
        if first.group_key is None or second.group_key is None or first.group_key != second.group_key:
            blockers.append("shared_supply_group_key_mismatch")
        if first.stopped_or_expired or second.stopped_or_expired:
            blockers.append("stopped_or_expired_workload")
        if first.permission_status not in {"verified_local", "verified_cloud"}:
            blockers.append(first.permission_status)
        if second.permission_status not in {"verified_local", "verified_cloud"}:
            blockers.append(second.permission_status)
        return sorted(set(blockers))

    def _route2_shared_supply_prepared_overlap_seconds(
        self,
        first: _Route2SharedSupplyWorkload,
        second: _Route2SharedSupplyWorkload,
    ) -> float:
        overlap = 0.0
        for first_range in first.prepared_ranges:
            if len(first_range) < 2:
                continue
            first_start = float(first_range[0])
            first_end = float(first_range[1])
            for second_range in second.prepared_ranges:
                if len(second_range) < 2:
                    continue
                second_start = float(second_range[0])
                second_end = float(second_range[1])
                overlap = max(overlap, min(first_end, second_end) - max(first_start, second_start))
        return max(0.0, overlap)

    def _route2_shared_supply_range_covers_target(
        self,
        prepared_ranges: list[list[float]],
        target_position_seconds: float,
    ) -> bool:
        target = float(target_position_seconds)
        return any(
            len(prepared_range) >= 2
            and float(prepared_range[0]) <= target <= float(prepared_range[1])
            for prepared_range in prepared_ranges
        )

    def _route2_shared_supply_pair_level(
        self,
        first: _Route2SharedSupplyWorkload,
        second: _Route2SharedSupplyWorkload,
    ) -> tuple[str, list[str]]:
        if first.epoch_start_seconds is not None and second.epoch_start_seconds is not None:
            if abs(first.epoch_start_seconds - second.epoch_start_seconds) <= 30.0:
                return "overlapping_epoch_candidate", []
        overlap_seconds = self._route2_shared_supply_prepared_overlap_seconds(first, second)
        if overlap_seconds >= 60.0:
            return "overlapping_epoch_candidate", []
        if self._route2_shared_supply_range_covers_target(second.prepared_ranges, first.target_position_seconds) or (
            self._route2_shared_supply_range_covers_target(first.prepared_ranges, second.target_position_seconds)
        ):
            return "cached_region_candidate", ["shared_store_missing"]
        if not first.prepared_ranges or not second.prepared_ranges:
            return "same_group_only", ["insufficient_frontier_data", "shared_store_missing"]
        return "same_group_only", ["epoch_window_mismatch", "non_overlapping_window", "shared_store_missing"]

    def _route2_shared_supply_init_group_status(
        self,
        members: list[_Route2SharedSupplyWorkload],
    ) -> dict[str, object]:
        if not members:
            return {
                "status": "unknown",
                "hashes_match": False,
                "blockers": ["pending_init_compatibility"],
            }
        available_hashes = [
            str(member.init_metadata.get("route2_init_hash_sha256") or "")
            for member in members
            if bool(member.init_metadata.get("route2_init_hash_available"))
            and str(member.init_metadata.get("route2_init_hash_sha256") or "").strip()
        ]
        if len(available_hashes) < len(members):
            pending_blockers = sorted({
                str(blocker)
                for member in members
                for blocker in (member.init_metadata.get("route2_init_compatibility_blockers") or [])
            })
            return {
                "status": "pending" if any(available_hashes) else "pending_init",
                "hashes_match": False,
                "blockers": pending_blockers or ["pending_init_compatibility"],
            }
        if len(set(available_hashes)) == 1:
            return {
                "status": "compatible_by_hash" if len(members) > 1 else "hash_available",
                "hashes_match": True,
                "blockers": [],
            }
        return {
            "status": "mismatch",
            "hashes_match": False,
            "blockers": ["init_mismatch"],
        }

    def _route2_shared_store_write_plan_locked(
        self,
        workload: _Route2SharedSupplyWorkload,
        *,
        init_compatibility_status: str | None = None,
    ) -> dict[str, object]:
        session = self._sessions.get(workload.session_id)
        epoch = (
            session.browser_playback.epochs.get(workload.epoch_id)
            if session is not None and session.browser_playback.engine_mode == "route2"
            else None
        )
        published_segment_indices: list[int] = []
        epoch_start_seconds = workload.epoch_start_seconds or 0.0
        target_position_seconds = workload.target_position_seconds
        if epoch is not None:
            epoch_start_seconds = float(epoch.epoch_start_seconds)
            target_position_seconds = float(epoch.target_position_seconds)
            if epoch.contiguous_published_through_segment is not None:
                published_segment_indices = list(range(0, int(epoch.contiguous_published_through_segment) + 1))
            elif epoch.published_segments:
                published_segment_indices = sorted(epoch.published_segments)
        segment_writer_enabled = bool(
            getattr(self.settings, "route2_shared_output_segment_writer_enabled", False)
        )
        write_plan = build_shared_store_write_plan(
            route2_root=self._route2_root,
            shared_output_key=workload.group_key,
            epoch_id=workload.epoch_id,
            epoch_start_seconds=epoch_start_seconds,
            target_position_seconds=target_position_seconds,
            published_segment_indices=published_segment_indices,
            segment_duration_seconds=SEGMENT_DURATION_SECONDS,
            output_contract_fingerprint=workload.output_contract_fingerprint,
            output_contract_missing_fields=workload.output_contract_missing_fields,
            init_compatibility_validated=False,
            init_compatibility_status=init_compatibility_status,
            permission_status=workload.permission_status,
            metadata_only=not segment_writer_enabled,
            segment_writer_enabled=segment_writer_enabled,
            shared_manifest_enabled=False,
        )
        if epoch is not None:
            for segment_plan in write_plan.get("segment_plans") or []:
                if not isinstance(segment_plan, dict):
                    continue
                segment_index = int(segment_plan["epoch_relative_segment_index"])
                segment_plan["source_segment_path"] = str(self._route2_segment_destination(epoch, segment_index))
        return write_plan

    def _write_route2_shared_output_metadata_locked(
        self,
        workload: _Route2SharedSupplyWorkload,
        write_plan: Mapping[str, object],
        *,
        init_compatibility_status: str | None = None,
    ) -> dict[str, object]:
        if not workload.group_key or not workload.output_contract_fingerprint:
            return {
                "shared_output_metadata_written": False,
                "shared_output_contract_status": "skipped",
                "shared_output_metadata_status": "skipped",
                "shared_output_ranges_status": "skipped",
                "shared_output_range_count": 0,
                "shared_output_media_bytes_present": False,
                "shared_output_store_blockers": list(SHARED_OUTPUT_STORE_BLOCKERS),
                "shared_output_metadata_write_errors": [],
                "shared_init_write_enabled": bool(getattr(self.settings, "route2_shared_output_init_writer_enabled", False)),
                "shared_init_write_attempted": False,
                "shared_init_write_status": "not_ready",
                "shared_init_write_blockers": ["missing_shared_output_key"],
                "shared_init_hash_sha256": None,
                "shared_init_size_bytes": None,
                "shared_init_path_present": False,
                "shared_segments_writer_enabled": bool(getattr(self.settings, "route2_shared_output_segment_writer_enabled", False)),
                "shared_segment_write_attempted": False,
                "shared_segment_write_status": "not_ready",
                "shared_segment_write_count": 0,
                "shared_segment_write_already_present_count": 0,
                "shared_segment_write_conflict_count": 0,
                "shared_segment_write_blockers": ["missing_shared_output_key"],
                "shared_segment_write_last_index": None,
                "shared_segment_write_last_hash": None,
                "shared_segment_write_range_start_index": None,
                "shared_segment_write_range_end_index_exclusive": None,
                "shared_init_write_errors": [],
                "shared_output_segment_write_errors": [],
            }
        if workload.output_contract_missing_fields:
            return {
                "shared_output_metadata_written": False,
                "shared_output_contract_status": "skipped_output_contract_incomplete",
                "shared_output_metadata_status": "skipped",
                "shared_output_ranges_status": "skipped",
                "shared_output_range_count": 0,
                "shared_output_media_bytes_present": False,
                "shared_output_store_blockers": sorted(
                    set(SHARED_OUTPUT_STORE_BLOCKERS) | {"output_contract_incomplete"}
                ),
                "shared_output_metadata_write_errors": [],
                "shared_init_write_enabled": bool(getattr(self.settings, "route2_shared_output_init_writer_enabled", False)),
                "shared_init_write_attempted": False,
                "shared_init_write_status": "not_ready",
                "shared_init_write_blockers": ["output_contract_incomplete"],
                "shared_init_hash_sha256": None,
                "shared_init_size_bytes": None,
                "shared_init_path_present": False,
                "shared_segments_writer_enabled": bool(getattr(self.settings, "route2_shared_output_segment_writer_enabled", False)),
                "shared_segment_write_attempted": False,
                "shared_segment_write_status": "not_ready",
                "shared_segment_write_count": 0,
                "shared_segment_write_already_present_count": 0,
                "shared_segment_write_conflict_count": 0,
                "shared_segment_write_blockers": ["output_contract_incomplete"],
                "shared_segment_write_last_index": None,
                "shared_segment_write_last_hash": None,
                "shared_segment_write_range_start_index": None,
                "shared_segment_write_range_end_index_exclusive": None,
                "shared_init_write_errors": [],
                "shared_output_segment_write_errors": [],
            }
        contract_metadata = build_shared_output_contract_metadata(
            shared_output_key=workload.group_key,
            output_contract_fingerprint=workload.output_contract_fingerprint,
            output_contract_version=workload.output_contract_version,
            profile=workload.profile,
            playback_mode=workload.playback_mode,
            source_fingerprint=workload.source_fingerprint,
            source_kind=workload.source_kind,
            segment_duration_seconds=SEGMENT_DURATION_SECONDS,
            output_contract_summary=workload.output_contract_summary,
        )
        store_metadata = build_shared_output_metadata(
            shared_output_key=workload.group_key,
            output_contract_fingerprint=workload.output_contract_fingerprint,
            source_kind=workload.source_kind,
            profile=workload.profile,
            playback_mode=workload.playback_mode,
            segment_duration_seconds=SEGMENT_DURATION_SECONDS,
        )
        phase_blockers = set(SHARED_OUTPUT_STORE_BLOCKERS)
        hard_range_blockers = {
            str(item)
            for item in (write_plan.get("candidate_range_blockers") or [])
            if str(item) not in phase_blockers
        }
        init_status = str(init_compatibility_status or "").strip()
        init_allows_metadata_range = init_status in {"hash_available", "compatible_by_hash"}
        candidate_range = None
        if (
            init_allows_metadata_range
            and not hard_range_blockers
            and write_plan.get("candidate_confirmed_range_start_index") is not None
            and write_plan.get("candidate_confirmed_range_end_index_exclusive") is not None
        ):
            candidate_range = {
                "start_index": int(write_plan["candidate_confirmed_range_start_index"]),
                "end_index_exclusive": int(write_plan["candidate_confirmed_range_end_index_exclusive"]),
            }
        metadata_result = write_shared_output_store_metadata(
            route2_root=self._route2_root,
            contract_metadata=contract_metadata,
            metadata=store_metadata,
            candidate_range=candidate_range,
            source_session_id=workload.session_id,
            source_epoch_id=workload.epoch_id,
        )
        source_init_path = None
        session = self._sessions.get(workload.session_id)
        epoch = (
            session.browser_playback.epochs.get(workload.epoch_id)
            if session is not None and session.browser_playback.engine_mode == "route2"
            else None
        )
        if epoch is not None and epoch.init_published:
            source_init_path = epoch.published_init_path
        try:
            init_result = write_shared_output_init_media(
                route2_root=self._route2_root,
                shared_output_key=workload.group_key,
                source_init_path=source_init_path,
                writer_enabled=bool(getattr(self.settings, "route2_shared_output_init_writer_enabled", False)),
                output_contract_fingerprint=workload.output_contract_fingerprint,
                metadata_ready=bool(metadata_result["shared_output_metadata_written"]),
                contract_status=str(metadata_result["shared_output_contract_status"]),
                init_compatibility_status=init_compatibility_status,
                expected_init_sha256=(
                    str(workload.init_metadata.get("route2_init_hash_sha256") or "")
                    if workload.init_metadata.get("route2_init_hash_available")
                    else None
                ),
                precondition_blockers=workload.blockers,
                writer_id=workload.worker_id,
            )
        except Exception as exc:  # noqa: BLE001
            init_result = {
                "shared_init_write_enabled": bool(getattr(self.settings, "route2_shared_output_init_writer_enabled", False)),
                "shared_init_write_attempted": True,
                "shared_init_write_status": "failed",
                "shared_init_write_blockers": ["shared_init_write_failed"],
                "shared_init_hash_sha256": None,
                "shared_init_size_bytes": None,
                "shared_init_path_present": False,
                "shared_init_write_errors": [f"shared_init_write_failed:{type(exc).__name__}"],
            }
        try:
            segment_result = write_shared_output_segment_media(
                route2_root=self._route2_root,
                shared_output_key=workload.group_key,
                segment_plans=write_plan.get("segment_plans") or [],
                writer_enabled=bool(getattr(self.settings, "route2_shared_output_segment_writer_enabled", False)),
                output_contract_fingerprint=workload.output_contract_fingerprint,
                metadata_ready=bool(metadata_result["shared_output_metadata_written"]),
                contract_status=str(metadata_result["shared_output_contract_status"]),
                init_compatibility_status=init_compatibility_status,
                segment_duration_seconds=SEGMENT_DURATION_SECONDS,
                precondition_blockers=workload.blockers,
                writer_id=workload.worker_id,
            )
        except Exception as exc:  # noqa: BLE001
            segment_result = {
                "shared_segments_writer_enabled": bool(getattr(self.settings, "route2_shared_output_segment_writer_enabled", False)),
                "shared_segment_write_attempted": True,
                "shared_segment_write_status": "failed",
                "shared_segment_write_count": 0,
                "shared_segment_write_already_present_count": 0,
                "shared_segment_write_conflict_count": 0,
                "shared_segment_write_blockers": ["shared_segment_write_failed"],
                "shared_segment_write_last_index": None,
                "shared_segment_write_last_hash": None,
                "shared_segment_write_range_start_index": None,
                "shared_segment_write_range_end_index_exclusive": None,
                "shared_output_media_bytes_present": bool(metadata_result.get("shared_output_media_bytes_present")),
                "shared_output_segment_write_errors": [f"shared_segment_write_failed:{type(exc).__name__}"],
            }
        if metadata_result.get("shared_output_media_bytes_present") and not segment_result.get(
            "shared_output_media_bytes_present"
        ):
            segment_result["shared_output_media_bytes_present"] = True
        if bool(getattr(self.settings, "route2_shared_output_segment_writer_enabled", False)):
            store_blockers = {
                str(item) for item in metadata_result.get("shared_output_store_blockers") or []
            }
            store_blockers.discard("no_segment_writer")
            store_blockers.discard("metadata_only")
            if segment_result.get("shared_output_media_bytes_present"):
                store_blockers.discard("media_bytes_not_present")
            store_blockers.update(str(item) for item in segment_result.get("shared_segment_write_blockers") or [])
            metadata_result["shared_output_store_blockers"] = [
                blocker for blocker in SHARED_OUTPUT_STORE_BLOCKERS if blocker in store_blockers
            ] + sorted(store_blockers - set(SHARED_OUTPUT_STORE_BLOCKERS))
        return {**metadata_result, **init_result, **segment_result}

    def _apply_route2_shared_supply_status_locked(
        self,
        payloads_by_worker_id: dict[str, dict[str, object]],
    ) -> list[dict[str, object]]:
        metadata_write_errors: list[str] = []
        init_write_errors: list[str] = []
        segment_write_errors: list[str] = []
        workloads = {
            record.worker_id: self._route2_shared_supply_workload_locked(record)
            for record in self._route2_workers.values()
            if record.worker_id in payloads_by_worker_id
        }
        level_order = {
            None: 0,
            "same_group_only": 1,
            "cached_region_candidate": 2,
            "overlapping_epoch_candidate": 3,
        }
        init_status_by_group_key = {
            group_key: self._route2_shared_supply_init_group_status(
                [item for item in workloads.values() if item.group_key == group_key]
            )
            for group_key in sorted({item.group_key for item in workloads.values() if item.group_key})
        }
        for worker_id, payload in payloads_by_worker_id.items():
            workload = workloads.get(worker_id)
            if workload is None:
                continue
            compatible_workloads: list[_Route2SharedSupplyWorkload] = []
            blockers = set(workload.blockers)
            notes = set(workload.notes)
            notes.add("no_copy_hardlink_symlink_attach_or_reuse_implemented")
            level_candidate: str | None = None
            saw_same_media = False
            for other in workloads.values():
                if other.worker_id == worker_id:
                    continue
                pair_blockers = self._route2_shared_supply_pair_blockers(workload, other)
                if pair_blockers:
                    if other.media_item_id == workload.media_item_id:
                        saw_same_media = True
                        blockers.update(pair_blockers)
                    continue
                saw_same_media = True
                compatible_workloads.append(other)
                pair_level, pair_level_blockers = self._route2_shared_supply_pair_level(workload, other)
                blockers.update(pair_level_blockers)
                if level_order[pair_level] > level_order[level_candidate]:
                    level_candidate = pair_level
            if not compatible_workloads:
                if not saw_same_media:
                    blockers.add("no_matching_active_route2_workload")
                level_candidate = "same_group_only" if workload.group_key else None
            absolute_start_candidate = None
            absolute_end_candidate = None
            if workload.epoch_start_seconds is not None:
                absolute_start_candidate = absolute_segment_index_from_seconds(
                    workload.epoch_start_seconds,
                    SEGMENT_DURATION_SECONDS,
                )
            prepared_range_ends = [
                float(prepared_range[1])
                for prepared_range in workload.prepared_ranges
                if len(prepared_range) >= 2
            ]
            if prepared_range_ends:
                prepared_end_seconds = max(prepared_range_ends)
                absolute_end_candidate = absolute_segment_end_index_exclusive_from_seconds(
                    prepared_end_seconds,
                    SEGMENT_DURATION_SECONDS,
                )
            init_group_status = (
                init_status_by_group_key.get(workload.group_key)
                if workload.group_key
                else self._route2_shared_supply_init_group_status([workload])
            ) or {}
            init_status = str(init_group_status.get("status") or workload.init_metadata.get("route2_init_compatibility_status") or "unknown")
            init_blockers = [str(item) for item in init_group_status.get("blockers") or []]
            blockers.update(init_blockers)
            write_plan = self._route2_shared_store_write_plan_locked(
                workload,
                init_compatibility_status=init_status,
            )
            metadata_write_result = self._write_route2_shared_output_metadata_locked(
                workload,
                write_plan,
                init_compatibility_status=init_status,
            )
            metadata_write_errors.extend(
                str(item) for item in metadata_write_result.get("shared_output_metadata_write_errors") or []
            )
            init_write_errors.extend(
                str(item) for item in metadata_write_result.get("shared_init_write_errors") or []
            )
            segment_write_errors.extend(
                str(item) for item in metadata_write_result.get("shared_output_segment_write_errors") or []
            )
            payload["shared_supply_candidate"] = bool(compatible_workloads)
            payload["shared_supply_group_key"] = workload.group_key
            payload["shared_output_key"] = workload.group_key
            payload["absolute_segment_index_start_candidate"] = absolute_start_candidate
            payload["absolute_segment_index_end_candidate"] = absolute_end_candidate
            payload["shared_output_metadata_written"] = bool(
                metadata_write_result["shared_output_metadata_written"]
            )
            payload["shared_output_contract_status"] = metadata_write_result["shared_output_contract_status"]
            payload["shared_output_ranges_status"] = metadata_write_result["shared_output_ranges_status"]
            payload["shared_output_range_count"] = metadata_write_result["shared_output_range_count"]
            payload["shared_output_media_bytes_present"] = bool(
                metadata_write_result["shared_output_media_bytes_present"]
            )
            payload["shared_output_byte_integrity_validated"] = bool(
                metadata_write_result.get("shared_output_byte_integrity_validated", False)
            )
            payload["shared_output_segment_bytes_stable"] = bool(
                metadata_write_result.get("shared_output_segment_bytes_stable", False)
            )
            payload["shared_output_mixed_writer_conflict"] = bool(
                metadata_write_result.get("shared_output_mixed_writer_conflict", False)
            )
            payload["shared_output_conflict_count"] = int(
                metadata_write_result.get("shared_output_conflict_count", 0) or 0
            )
            payload["shared_output_conflict_indexes"] = list(
                metadata_write_result.get("shared_output_conflict_indexes", [])
            )
            payload["shared_output_serving_allowed"] = bool(
                metadata_write_result.get("shared_output_serving_allowed", False)
            )
            payload["shared_output_serving_blocked"] = bool(
                metadata_write_result.get("shared_output_serving_blocked", True)
            )
            payload["shared_output_serving_blocked_reason"] = metadata_write_result.get(
                "shared_output_serving_blocked_reason"
            )
            payload["shared_output_serving_blocked_reasons"] = list(
                metadata_write_result.get("shared_output_serving_blocked_reasons", [])
            )
            payload["shared_output_canonical_generation_required"] = bool(
                metadata_write_result.get("shared_output_canonical_generation_required", True)
            )
            payload["shared_output_canonical_generation_strategy"] = metadata_write_result.get(
                "shared_output_canonical_generation_strategy"
            )
            payload["shared_output_store_blockers"] = list(
                metadata_write_result["shared_output_store_blockers"]
            )
            payload["shared_init_write_enabled"] = bool(metadata_write_result["shared_init_write_enabled"])
            payload["shared_init_write_attempted"] = bool(metadata_write_result["shared_init_write_attempted"])
            payload["shared_init_write_status"] = metadata_write_result["shared_init_write_status"]
            payload["shared_init_write_blockers"] = list(metadata_write_result["shared_init_write_blockers"])
            payload["shared_init_hash_sha256"] = metadata_write_result["shared_init_hash_sha256"]
            payload["shared_init_size_bytes"] = metadata_write_result["shared_init_size_bytes"]
            payload["shared_init_path_present"] = bool(metadata_write_result["shared_init_path_present"])
            payload["shared_segments_writer_enabled"] = bool(metadata_write_result["shared_segments_writer_enabled"])
            payload["shared_segment_write_attempted"] = bool(
                metadata_write_result["shared_segment_write_attempted"]
            )
            payload["shared_segment_write_status"] = metadata_write_result["shared_segment_write_status"]
            payload["shared_segment_write_count"] = metadata_write_result["shared_segment_write_count"]
            payload["shared_segment_write_already_present_count"] = metadata_write_result[
                "shared_segment_write_already_present_count"
            ]
            payload["shared_segment_write_conflict_count"] = metadata_write_result[
                "shared_segment_write_conflict_count"
            ]
            payload["shared_segment_write_blockers"] = list(metadata_write_result["shared_segment_write_blockers"])
            payload["shared_segment_write_last_index"] = metadata_write_result["shared_segment_write_last_index"]
            payload["shared_segment_write_last_hash"] = metadata_write_result["shared_segment_write_last_hash"]
            payload["shared_segment_write_range_start_index"] = metadata_write_result[
                "shared_segment_write_range_start_index"
            ]
            payload["shared_segment_write_range_end_index_exclusive"] = metadata_write_result[
                "shared_segment_write_range_end_index_exclusive"
            ]
            payload["shared_segment_write_conflict_indexes"] = list(
                metadata_write_result.get("shared_segment_write_conflict_indexes", [])
            )
            payload["shared_segment_write_serving_blocked_reason"] = metadata_write_result.get(
                "shared_segment_write_serving_blocked_reason"
            )
            payload["route2_init_available"] = bool(workload.init_metadata["route2_init_available"])
            payload["route2_init_hash_sha256"] = workload.init_metadata["route2_init_hash_sha256"]
            payload["route2_init_hash_available"] = bool(workload.init_metadata["route2_init_hash_available"])
            payload["route2_init_hash_reason"] = workload.init_metadata["route2_init_hash_reason"]
            payload["route2_init_size_bytes"] = workload.init_metadata["route2_init_size_bytes"]
            payload["route2_init_metadata_available"] = bool(workload.init_metadata["route2_init_metadata_available"])
            payload["route2_init_compatibility_status"] = init_status
            payload["route2_init_compatibility_blockers"] = init_blockers or list(
                workload.init_metadata["route2_init_compatibility_blockers"]
            )
            payload["shared_store_write_plan_available"] = bool(write_plan["shared_store_write_plan_available"])
            payload["shared_store_candidate_range_start_index"] = write_plan[
                "candidate_confirmed_range_start_index"
            ]
            payload["shared_store_candidate_range_end_index_exclusive"] = write_plan[
                "candidate_confirmed_range_end_index_exclusive"
            ]
            payload["shared_store_candidate_range_start_seconds"] = write_plan[
                "candidate_confirmed_range_start_seconds"
            ]
            payload["shared_store_candidate_range_end_seconds"] = write_plan[
                "candidate_confirmed_range_end_seconds"
            ]
            payload["shared_store_candidate_segment_count"] = write_plan["candidate_range_segment_count"]
            payload["shared_store_write_candidate_count"] = write_plan["shared_store_write_candidate_count"]
            payload["shared_store_write_blockers"] = sorted(
                set(str(item) for item in write_plan["shared_store_write_blockers"])
                | {
                    str(item)
                    for item in metadata_write_result["shared_output_store_blockers"]
                    if str(item) not in set(SHARED_OUTPUT_STORE_BLOCKERS)
                }
            )
            payload["shared_store_mapping_confidence"] = write_plan["shared_store_mapping_confidence"]
            payload["shared_store_mapping_notes"] = list(write_plan["shared_store_mapping_notes"])
            payload["route2_output_contract_fingerprint"] = workload.output_contract_fingerprint
            payload["route2_output_contract_version"] = workload.output_contract_version
            payload["route2_output_contract_missing_fields"] = list(workload.output_contract_missing_fields)
            payload["route2_output_contract_summary"] = dict(workload.output_contract_summary)
            payload["shared_supply_group_size"] = (
                sum(1 for item in workloads.values() if item.group_key and item.group_key == workload.group_key)
                if workload.group_key
                else 1
            )
            payload["shared_supply_level_candidate"] = level_candidate
            payload["compatible_existing_workload_ids"] = sorted(item.workload_id for item in compatible_workloads)
            payload["compatible_existing_worker_ids"] = sorted(item.worker_id for item in compatible_workloads)
            payload["shared_supply_blockers"] = sorted(blockers)
            payload["shared_supply_permission_status"] = workload.permission_status
            payload["estimated_duplicate_workers_avoided"] = len(compatible_workloads)
            payload["shared_supply_notes"] = sorted(notes)

        summaries: list[dict[str, object]] = []
        for group_key in sorted({item.group_key for item in workloads.values() if item.group_key}):
            members = [item for item in workloads.values() if item.group_key == group_key]
            member_payloads = [
                payloads_by_worker_id[item.worker_id]
                for item in members
                if item.worker_id in payloads_by_worker_id
            ]
            init_group_status = init_status_by_group_key.get(group_key) or {}
            candidate_count = sum(1 for item in member_payloads if bool(item.get("shared_supply_candidate")))
            blockers = sorted({
                blocker
                for item in member_payloads
                for blocker in (item.get("shared_supply_blockers") or [])
            })
            blockers = sorted(set(blockers) | {str(item) for item in init_group_status.get("blockers") or []})
            summaries.append(
                {
                    "group_key": group_key,
                    "workload_count": len(members),
                    "candidate_count": candidate_count,
                    "blockers": blockers,
                    "estimated_duplicate_workers_avoided": max(0, candidate_count - 1),
                    "shared_supply_group_init_compatibility_status": init_group_status.get("status"),
                    "shared_supply_group_init_hashes_match": bool(init_group_status.get("hashes_match")),
                    "shared_supply_group_init_blockers": list(init_group_status.get("blockers") or []),
                }
            )
        self._shared_output_metadata_write_errors = metadata_write_errors
        self._shared_output_init_write_errors = init_write_errors
        self._shared_output_segment_write_errors = segment_write_errors
        return summaries

    def _evaluate_route2_active_playback_health_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        record: Route2WorkerRecord,
    ) -> _Route2ActivePlaybackHealth:
        assigned_threads = max(0, int(record.assigned_threads or 0))
        protected_floor = self._route2_protected_min_threads_per_active_user()
        (
            _published_end_seconds,
            _effective_playhead_seconds,
            runway_seconds,
            supply_rate_x,
            observation_seconds,
            manifest_complete,
            refill_in_progress,
        ) = self._route2_runtime_supply_metrics_locked(session, epoch)
        cpu_thread_limited = self._route2_record_cpu_thread_limited(record)
        is_active_watch = (
            session.browser_playback.engine_mode == "route2"
            and session.lifecycle_state == "attached"
            and session.client_is_playing
            and session.pending_target_seconds is None
        )
        metrics_mature = observation_seconds >= ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS
        starvation_risk = self._starvation_risk(session)
        stalled_recovery_needed = self._stalled_recovery_needed(session)
        runtime_rebalance_role = "neutral"
        runtime_rebalance_reason = "No runtime rebalance action is suggested."
        runtime_rebalance_target_threads: int | None = None
        runtime_rebalance_can_donate_threads = 0
        runtime_rebalance_priority = 0

        if manifest_complete or not refill_in_progress or not is_active_watch:
            status = "complete_or_not_refilling"
            reason = "Playback is complete, not actively watching, or not actively refilling; supply rate is not an admission blocker."
            admission_blocking = False
        elif record.non_retryable_error or session.last_error:
            status = "provider_error"
            reason = "Existing playback has an explicit provider/source error; do not classify it as CPU/thread starvation."
            admission_blocking = False
        elif not metrics_mature:
            status = "metrics_immature"
            reason = "Runtime supply metrics are not mature enough to prove active playback health."
            admission_blocking = False
        elif self._route2_client_limited_locked(session, epoch):
            status = "client_bound"
            reason = "Existing playback appears limited by client goodput rather than Route2 transcode threads."
            admission_blocking = False
        elif (
            supply_rate_x <= ROUTE2_ACTIVE_SUPPLY_LOW_RATE_X
            and self._route2_source_limited_locked(session, epoch, cpu_thread_limited=cpu_thread_limited)
        ):
            status = "source_bound"
            reason = "Existing playback supply is low, but source/provider throughput is the likely limiter rather than CPU threads."
            admission_blocking = False
        elif supply_rate_x <= ROUTE2_ACTIVE_SUPPLY_LOW_RATE_X and cpu_thread_limited:
            status = "cpu_thread_starved"
            reason = "Existing active playback is not sustaining real-time supply and appears CPU/thread limited."
            admission_blocking = True
            runtime_rebalance_role = "needs_resource"
            runtime_rebalance_target_threads = self._route2_next_runtime_rebalance_target_threads(assigned_threads)
            runtime_rebalance_reason = "CPU/thread-starved active playback would be a future rebalance recipient."
            runtime_rebalance_priority = 100
        elif (
            supply_rate_x < ROUTE2_ACTIVE_SUPPLY_STRONGLY_LOW_RATE_X
            or (
                supply_rate_x <= ROUTE2_ACTIVE_SUPPLY_LOW_RATE_X
                and (runway_seconds <= WATCH_LOW_WATERMARK_SECONDS or starvation_risk or stalled_recovery_needed)
            )
        ):
            status = "watch_supply_at_risk"
            reason = "Existing active playback has low real-time supply or low runway and needs protection before admitting more work."
            admission_blocking = True
            runtime_rebalance_role = "needs_resource"
            runtime_rebalance_target_threads = self._route2_next_runtime_rebalance_target_threads(assigned_threads)
            runtime_rebalance_reason = "At-risk active playback would be a future rebalance recipient."
            runtime_rebalance_priority = 80
        elif supply_rate_x > ROUTE2_ACTIVE_SUPPLY_HEALTHY_RATE_X and not starvation_risk and not stalled_recovery_needed:
            status = "healthy"
            reason = "Existing active playback is sustaining real-time supply with margin."
            admission_blocking = False
            if (
                assigned_threads > protected_floor
                and (
                    supply_rate_x >= ROUTE2_RUNTIME_DONOR_SUPPLY_RATE_X
                    or runway_seconds >= WATCH_REFILL_TARGET_SECONDS
                )
            ):
                runtime_rebalance_role = "donor_candidate"
                runtime_rebalance_can_donate_threads = max(0, assigned_threads - protected_floor)
                runtime_rebalance_reason = (
                    "Playback has healthy supply/runway above the protected floor; it is only a theoretical future donor."
                )
                runtime_rebalance_target_threads = protected_floor
                runtime_rebalance_priority = 20
        else:
            status = "watch_supply_at_risk"
            reason = "Existing active playback has not proven enough real-time supply margin for extra work."
            admission_blocking = True
            runtime_rebalance_role = "needs_resource"
            runtime_rebalance_target_threads = self._route2_next_runtime_rebalance_target_threads(assigned_threads)
            runtime_rebalance_reason = "Marginal active playback would be protected before admission."
            runtime_rebalance_priority = 60

        return _Route2ActivePlaybackHealth(
            status=status,
            reason=reason,
            admission_blocking=admission_blocking,
            worker_id=record.worker_id,
            session_id=session.session_id,
            supply_rate_x=supply_rate_x,
            supply_observation_seconds=observation_seconds,
            runway_seconds=runway_seconds,
            assigned_threads=assigned_threads,
            cpu_thread_limited=cpu_thread_limited,
            runtime_rebalance_role=runtime_rebalance_role,
            runtime_rebalance_reason=runtime_rebalance_reason,
            runtime_rebalance_target_threads=runtime_rebalance_target_threads,
            runtime_rebalance_can_donate_threads=runtime_rebalance_can_donate_threads,
            runtime_rebalance_priority=runtime_rebalance_priority,
        )

    def _route2_active_playback_healths_locked(self) -> list[_Route2ActivePlaybackHealth]:
        healths: list[_Route2ActivePlaybackHealth] = []
        for record in self._route2_workers.values():
            if record.state not in {"running", "stopping"}:
                continue
            session = self._sessions.get(record.session_id)
            if session is None or session.browser_playback.engine_mode != "route2":
                continue
            epoch = session.browser_playback.epochs.get(record.epoch_id)
            if epoch is None:
                continue
            healths.append(self._evaluate_route2_active_playback_health_locked(session, epoch, record))
        return healths

    def _raise_if_route2_admission_denied_locked(
        self,
        *,
        incoming_user_id: int,
        incoming_user_role: str,
        source_kind: str,
    ) -> None:
        del incoming_user_role, source_kind
        protected_floor = self._route2_protected_min_threads_per_active_user()
        admission_min_threads = self._route2_admission_min_worker_threads()
        budget = self._route2_budget_summary_locked()
        total_route2_budget_cores = int(budget["total_route2_budget_cores"])
        active_records = [
            record
            for record in self._route2_workers.values()
            if record.state in {"queued", "running", "stopping"}
        ]
        active_user_ids = {record.user_id for record in active_records}
        active_user_count_after_admission = len(active_user_ids | {int(incoming_user_id)})
        per_user_budget_after_admission = (
            max(1, math.floor(total_route2_budget_cores / active_user_count_after_admission))
            if active_user_count_after_admission > 0
            else total_route2_budget_cores
        )

        if int(self.settings.route2_max_worker_threads) < admission_min_threads:
            raise PlaybackAdmissionError(
                self._build_server_max_capacity_detail_locked(
                    reason_code="route2_max_worker_threads_below_protected_floor",
                    active_user_count_after_admission=active_user_count_after_admission,
                    admission_min_threads=admission_min_threads,
                )
            )
        if total_route2_budget_cores < admission_min_threads:
            raise PlaybackAdmissionError(
                self._build_server_max_capacity_detail_locked(
                    reason_code="route2_cpu_upbound_below_protected_floor",
                    active_user_count_after_admission=active_user_count_after_admission,
                    admission_min_threads=admission_min_threads,
                )
            )
        if per_user_budget_after_admission < protected_floor:
            raise PlaybackAdmissionError(
                self._build_server_max_capacity_detail_locked(
                    reason_code="per_user_budget_below_protected_floor",
                    active_user_count_after_admission=active_user_count_after_admission,
                    admission_min_threads=admission_min_threads,
                )
            )

        if self._route2_bad_condition_reserve_protections_locked():
            raise PlaybackAdmissionError(
                self._build_server_max_capacity_detail_locked(
                    reason_code="active_bad_condition_reserve_protection",
                    active_user_count_after_admission=active_user_count_after_admission,
                    admission_min_threads=admission_min_threads,
                )
            )

        reserved_total_threads = 0
        reserved_incoming_user_threads = 0
        for record in active_records:
            reserved_threads = self._route2_reserved_threads_for_admission_locked(record)
            reserved_total_threads += reserved_threads
            if record.user_id == int(incoming_user_id):
                reserved_incoming_user_threads += reserved_threads

        available_reserved_threads = total_route2_budget_cores - reserved_total_threads
        if available_reserved_threads < admission_min_threads:
            raise PlaybackAdmissionError(
                self._build_server_max_capacity_detail_locked(
                    reason_code="no_spare_protected_worker_capacity",
                    active_user_count_after_admission=active_user_count_after_admission,
                    available_reserved_threads=available_reserved_threads,
                    admission_min_threads=admission_min_threads,
                )
            )

        user_remaining_reserved_threads = per_user_budget_after_admission - reserved_incoming_user_threads
        if user_remaining_reserved_threads < admission_min_threads:
            raise PlaybackAdmissionError(
                self._build_server_max_capacity_detail_locked(
                    reason_code="user_budget_protected_capacity_exhausted",
                    active_user_count_after_admission=active_user_count_after_admission,
                    available_reserved_threads=user_remaining_reserved_threads,
                    admission_min_threads=admission_min_threads,
                )
            )

        for active_health in self._route2_active_playback_healths_locked():
            if active_health.admission_blocking:
                raise PlaybackAdmissionError(
                    self._build_server_max_capacity_detail_locked(
                        reason_code="active_stream_protection",
                        active_user_count_after_admission=active_user_count_after_admission,
                        available_reserved_threads=available_reserved_threads,
                        admission_min_threads=admission_min_threads,
                    )
                )
            if (
                active_health.status == "metrics_immature"
                and available_reserved_threads <= admission_min_threads
            ):
                raise PlaybackAdmissionError(
                    self._build_server_max_capacity_detail_locked(
                        reason_code="active_stream_metrics_immature",
                        active_user_count_after_admission=active_user_count_after_admission,
                        available_reserved_threads=available_reserved_threads,
                        admission_min_threads=admission_min_threads,
                    )
                )

        snapshot = self._latest_route2_resource_snapshot_locked()
        if snapshot is None or snapshot.sample_stale:
            return
        if snapshot.total_memory_bytes and snapshot.route2_memory_bytes_total is not None:
            memory_pressure = snapshot.route2_memory_bytes_total / snapshot.total_memory_bytes
            if memory_pressure >= 0.90:
                raise PlaybackAdmissionError(
                    self._build_server_max_capacity_detail_locked(
                        reason_code="route2_memory_hard_pressure",
                        active_user_count_after_admission=active_user_count_after_admission,
                        available_reserved_threads=available_reserved_threads,
                        admission_min_threads=admission_min_threads,
                    )
                )
        if snapshot.external_pressure_level == "high":
            raise PlaybackAdmissionError(
                self._build_server_max_capacity_detail_locked(
                    reason_code="external_host_cpu_pressure_high",
                    message="Server is busy with another task. Please try again later.",
                    active_user_count_after_admission=active_user_count_after_admission,
                    available_reserved_threads=available_reserved_threads,
                    admission_min_threads=admission_min_threads,
                )
            )
        if (
            snapshot.external_ffmpeg_process_count > 0
            and snapshot.external_ffmpeg_cpu_cores_estimate is not None
            and snapshot.external_ffmpeg_cpu_cores_estimate >= 1.0
        ):
            raise PlaybackAdmissionError(
                self._build_server_max_capacity_detail_locked(
                    reason_code="external_ffmpeg_pressure",
                    message="Server is busy with another task. Please try again later.",
                    active_user_count_after_admission=active_user_count_after_admission,
                    available_reserved_threads=available_reserved_threads,
                    admission_min_threads=admission_min_threads,
                )
            )

    def _route2_conservative_spawn_target_locked(
        self,
        *,
        fixed_assigned_threads: int,
        available_total_threads: int,
        user_remaining_threads: int,
    ) -> int:
        baseline = min(
            max(int(self.settings.route2_min_worker_threads), 4),
            max(0, int(fixed_assigned_threads)),
        )
        ceiling = min(
            max(0, int(available_total_threads)),
            max(0, int(user_remaining_threads)),
            max(0, int(self.settings.route2_adaptive_max_worker_threads)),
        )
        if ceiling <= 0:
            return 0
        return min(baseline, ceiling)

    def _build_route2_adaptive_spawn_dry_run_locked(
        self,
        record: Route2WorkerRecord,
        *,
        fixed_assigned_threads: int,
        available_total_threads: int,
        user_remaining_threads: int,
        allocated_cpu_cores: int,
        route2_cpu_upbound_cores: int,
        active_route2_user_count: int,
        active_route2_workload_count: int | None = None,
    ) -> _Route2AdaptiveSpawnDryRunDecision:
        snapshot = self._latest_route2_resource_snapshot_locked()
        sample_age_seconds = (time.time() - snapshot.sampled_at_ts) if snapshot is not None else None
        sample_mature = bool(snapshot is not None and snapshot.sample_mature and not snapshot.sample_stale)
        effective_workload_count = (
            int(active_route2_workload_count)
            if active_route2_workload_count is not None
            else int(active_route2_user_count)
        )
        conservative_target = self._route2_conservative_spawn_target_locked(
            fixed_assigned_threads=fixed_assigned_threads,
            available_total_threads=available_total_threads,
            user_remaining_threads=user_remaining_threads,
        )
        policy = "phase_1h_2_initial_spawn_dry_run"
        blockers: list[str] = []
        reason_parts: list[str] = []

        if record.source_kind != "local":
            blockers.append("cloud_adaptive_spawn_deferred")
            reason_parts.append("cloud real adaptive initial spawn is deferred")
        if effective_workload_count != 1:
            blockers.append("existing_route2_workload_present")
            reason_parts.append("Route2 is not a single active playback workload")
        if not sample_mature:
            blockers.append("telemetry_missing_or_stale")
            reason_parts.append("resource telemetry is missing, immature, or stale")

        route2_memory_pressure = None
        route2_cpu_total = None
        user_cpu_total = None
        if snapshot is not None and not snapshot.sample_stale:
            route2_cpu_total = snapshot.route2_cpu_cores_used_total
            user_cpu_total = snapshot.per_user_cpu_cores_used_total.get(record.user_id, 0.0)
            if snapshot.total_memory_bytes and snapshot.route2_memory_bytes_total is not None:
                route2_memory_pressure = snapshot.route2_memory_bytes_total / snapshot.total_memory_bytes
            external_level = snapshot.external_pressure_level
            if external_level == "high":
                blockers.append("external_host_cpu_pressure_high")
                reason_parts.append("external host CPU pressure is high")
            elif external_level == "moderate":
                blockers.append("external_host_cpu_pressure_moderate")
                reason_parts.append("external host CPU pressure is moderate")
            if snapshot.external_ffmpeg_process_count > 0:
                blockers.append("external_ffmpeg_detected")
                reason_parts.append("external ffmpeg/ffprobe is present")
        if snapshot is None or snapshot.total_memory_bytes is None or snapshot.route2_memory_bytes_total is None:
            blockers.append("memory_metrics_missing")
            reason_parts.append("Route2 memory telemetry is missing")
        elif route2_memory_pressure is not None and route2_memory_pressure >= 0.80:
            blockers.append("route2_memory_pressure")
            reason_parts.append("Route2 memory pressure blocks adaptive initial spawn")
        if route2_cpu_total is None:
            blockers.append("route2_cpu_metrics_missing")
            reason_parts.append("Route2 CPU telemetry is missing")
        if user_cpu_total is None:
            blockers.append("user_cpu_metrics_missing")
            reason_parts.append("per-user Route2 CPU telemetry is missing")

        first_tier_target = max(6, int(self.settings.route2_min_worker_threads))
        adaptive_ceiling = min(
            max(0, int(self.settings.route2_adaptive_max_worker_threads)),
            max(0, int(available_total_threads)),
            max(0, int(user_remaining_threads)),
            max(0, int(route2_cpu_upbound_cores)),
        )
        dry_run_target = min(first_tier_target, adaptive_ceiling)
        if dry_run_target < int(self.settings.route2_min_worker_threads):
            blockers.append("below_min_worker_threads")
            reason_parts.append("adaptive dry-run ceiling is below route2_min_worker_threads")
        if dry_run_target < first_tier_target:
            reason_parts.append("adaptive max or CPU budget caps the first-tier target below 6")
        if route2_cpu_total is not None and (route2_cpu_upbound_cores - route2_cpu_total) < dry_run_target:
            blockers.append("global_cpu_headroom_insufficient")
            reason_parts.append("global Route2 CPU headroom is insufficient")
        if user_cpu_total is not None and (allocated_cpu_cores - user_cpu_total) < dry_run_target:
            blockers.append("user_cpu_headroom_insufficient")
            reason_parts.append("per-user Route2 CPU headroom is insufficient")

        if blockers:
            return _Route2AdaptiveSpawnDryRunDecision(
                recommended_threads=conservative_target,
                reason=(
                    "Initial spawn dry-run remains conservative: "
                    + "; ".join(dict.fromkeys(reason_parts))
                    + ". Real assigned_threads remains fixed."
                ),
                blockers=list(dict.fromkeys(blockers)),
                policy=policy,
                sample_age_seconds=sample_age_seconds,
                sample_mature=sample_mature,
            )

        capped_note = (
            " Adaptive max or CPU budget caps the first-tier target below 6."
            if dry_run_target < first_tier_target
            else ""
        )
        return _Route2AdaptiveSpawnDryRunDecision(
            recommended_threads=dry_run_target,
            reason=(
                f"Initial spawn dry-run would choose {dry_run_target} threads for a local single active "
                "Route2 playback workload with mature telemetry, no external pressure, RAM safe, and enough "
                f"user/global CPU headroom.{capped_note} Real assigned_threads remains fixed."
            ),
            blockers=[],
            policy=policy,
            sample_age_seconds=sample_age_seconds,
            sample_mature=sample_mature,
        )

    def _fixed_route2_thread_assignment_decision(
        self,
        *,
        fixed_assigned_threads: int,
        policy: str,
        reason: str,
        blockers: list[str] | None = None,
        source: str,
        adaptive_enabled: bool,
        fallback_used: bool,
    ) -> _Route2RealThreadAssignmentDecision:
        return _Route2RealThreadAssignmentDecision(
            assigned_threads=max(0, int(fixed_assigned_threads)),
            assignment_policy=policy,
            assignment_reason=reason,
            assignment_blockers=list(dict.fromkeys(blockers or [])),
            adaptive_control_enabled=adaptive_enabled,
            adaptive_control_applied=False,
            assigned_threads_source=source,
            fallback_used=fallback_used,
        )

    def _resolve_route2_real_assigned_threads_locked(
        self,
        record: Route2WorkerRecord,
        *,
        fixed_assigned_threads: int,
        spawn_dry_run: _Route2AdaptiveSpawnDryRunDecision,
    ) -> _Route2RealThreadAssignmentDecision:
        adaptive_enabled = bool(getattr(self.settings, "route2_adaptive_thread_control_enabled", False))
        if not adaptive_enabled:
            return self._fixed_route2_thread_assignment_decision(
                fixed_assigned_threads=fixed_assigned_threads,
                policy="fixed_disabled",
                reason="Adaptive real thread control is disabled; using fixed Route2 assignment.",
                source="fixed_disabled",
                adaptive_enabled=False,
                fallback_used=True,
            )

        blockers: list[str] = []
        reason_parts: list[str] = []
        if record.source_kind == "cloud":
            if bool(getattr(self.settings, "route2_adaptive_thread_control_local_only", True)):
                blockers.append("cloud_adaptive_thread_control_local_only")
                reason_parts.append("cloud real adaptive thread control is blocked by local-only rollout")
            elif not bool(getattr(self.settings, "route2_adaptive_thread_control_cloud_enabled", False)):
                blockers.append("cloud_adaptive_thread_control_disabled")
                reason_parts.append("cloud real adaptive thread control is disabled/deferred")
            else:
                blockers.append("cloud_adaptive_thread_control_deferred")
                reason_parts.append("cloud real adaptive thread control has no real assignment policy in this phase")
        elif record.source_kind == "local":
            pass
        else:
            blockers.append("unsupported_source_kind")
            reason_parts.append("unsupported source kind for real adaptive thread control")

        target_threads = int(spawn_dry_run.recommended_threads or 0)
        if spawn_dry_run.blockers:
            blockers.extend(spawn_dry_run.blockers)
            reason_parts.append("spawn dry-run safety blockers did not pass")
        if not bool(spawn_dry_run.sample_mature):
            blockers.append("telemetry_missing_or_stale")
            reason_parts.append("resource telemetry is missing, immature, or stale")
        if target_threads != 6:
            blockers.append("unsupported_real_adaptive_target")
            reason_parts.append("this phase only permits an initial local 6-thread assignment")
        if int(getattr(self.settings, "route2_adaptive_max_worker_threads", 0) or 0) < 6:
            blockers.append("adaptive_max_below_first_tier")
            reason_parts.append("adaptive max worker threads is below the 6-thread first tier")
        if int(fixed_assigned_threads) < int(self.settings.route2_min_worker_threads):
            blockers.append("fixed_assignment_below_min_worker_threads")
            reason_parts.append("fixed assignment is below route2_min_worker_threads")

        if blockers:
            source = (
                "cloud_disabled"
                if any(blocker.startswith("cloud_adaptive_thread_control") for blocker in blockers)
                else "safety_fallback"
            )
            return self._fixed_route2_thread_assignment_decision(
                fixed_assigned_threads=fixed_assigned_threads,
                policy="adaptive_enabled_fixed_fallback",
                reason=(
                    "Adaptive real thread control is enabled, but fixed assignment is used: "
                    + "; ".join(dict.fromkeys(reason_parts or ["safety gates did not pass"]))
                    + "."
                ),
                blockers=blockers,
                source=source,
                adaptive_enabled=True,
                fallback_used=True,
            )

        return _Route2RealThreadAssignmentDecision(
            assigned_threads=6,
            assignment_policy="adaptive_local_initial_6",
            assignment_reason=(
                "Adaptive real thread control selected 6 threads for an initial local Route2 spawn "
                "after strict telemetry, resource, and safety gates passed."
            ),
            assignment_blockers=[],
            adaptive_control_enabled=True,
            adaptive_control_applied=True,
            assigned_threads_source="adaptive_local_initial_6",
            fallback_used=False,
        )

    def _build_route2_adaptive_shadow_input_locked(
        self,
        record: Route2WorkerRecord,
        *,
        allocated_cpu_cores: int,
        user_cpu_cores_used_total: float | None,
        route2_cpu_cores_used_total: float | None,
        route2_cpu_upbound_cores: int,
        active_route2_user_count: int | None,
        host_cpu_pressure: _HostCpuPressureSnapshot,
        total_memory_bytes: int | None,
        route2_memory_bytes_total: int | None,
    ) -> Route2AdaptiveShadowInput:
        resource_snapshot = self._latest_route2_resource_snapshot_locked()
        if resource_snapshot is not None:
            host_cpu_pressure = _host_cpu_pressure_from_resource_snapshot(resource_snapshot)
            if not resource_snapshot.sample_stale:
                if record.user_id in resource_snapshot.per_user_cpu_cores_used_total:
                    user_cpu_cores_used_total = resource_snapshot.per_user_cpu_cores_used_total[record.user_id]
                if resource_snapshot.route2_cpu_cores_used_total is not None:
                    route2_cpu_cores_used_total = resource_snapshot.route2_cpu_cores_used_total
                if resource_snapshot.total_memory_bytes is not None:
                    total_memory_bytes = resource_snapshot.total_memory_bytes
                if resource_snapshot.route2_memory_bytes_total is not None:
                    route2_memory_bytes_total = resource_snapshot.route2_memory_bytes_total

        session = self._sessions.get(record.session_id)
        if session is None:
            return Route2AdaptiveShadowInput(
                worker_state=record.state,
                playback_mode=record.playback_mode,
                profile=record.profile,
                source_kind=record.source_kind,
                assigned_threads=record.assigned_threads,
                default_threads=4,
                max_threads=self.settings.route2_max_worker_threads,
                adaptive_max_threads=self.settings.route2_adaptive_max_worker_threads,
                cpu_cores_used=record.cpu_cores_used,
                allocated_cpu_cores=allocated_cpu_cores or None,
                user_cpu_cores_used_total=user_cpu_cores_used_total,
                route2_cpu_upbound_cores=route2_cpu_upbound_cores,
                route2_cpu_cores_used_total=route2_cpu_cores_used_total,
                active_route2_user_count=active_route2_user_count,
                host_cpu_total_cores=host_cpu_pressure.host_cpu_total_cores,
                host_cpu_used_cores=host_cpu_pressure.host_cpu_used_cores,
                host_cpu_used_percent=host_cpu_pressure.host_cpu_used_percent,
                external_cpu_cores_used_estimate=host_cpu_pressure.external_cpu_cores_used_estimate,
                external_cpu_percent_estimate=host_cpu_pressure.external_cpu_percent_estimate,
                external_ffmpeg_process_count=host_cpu_pressure.external_ffmpeg_process_count,
                external_ffmpeg_cpu_cores_estimate=host_cpu_pressure.external_ffmpeg_cpu_cores_estimate,
                host_cpu_sample_mature=host_cpu_pressure.host_cpu_sample_mature,
                memory_bytes=record.memory_bytes,
                total_memory_bytes=total_memory_bytes,
                route2_memory_bytes_total=route2_memory_bytes_total,
                non_retryable_error=record.non_retryable_error,
                mode_ready=False,
            )

        browser_session = session.browser_playback
        epoch = browser_session.epochs.get(record.epoch_id)
        if epoch is None:
            return Route2AdaptiveShadowInput(
                worker_state=record.state,
                playback_mode=record.playback_mode,
                profile=record.profile,
                source_kind=record.source_kind,
                assigned_threads=record.assigned_threads,
                default_threads=4,
                max_threads=self.settings.route2_max_worker_threads,
                adaptive_max_threads=self.settings.route2_adaptive_max_worker_threads,
                cpu_cores_used=record.cpu_cores_used,
                allocated_cpu_cores=allocated_cpu_cores or None,
                user_cpu_cores_used_total=user_cpu_cores_used_total,
                route2_cpu_upbound_cores=route2_cpu_upbound_cores,
                route2_cpu_cores_used_total=route2_cpu_cores_used_total,
                active_route2_user_count=active_route2_user_count,
                host_cpu_total_cores=host_cpu_pressure.host_cpu_total_cores,
                host_cpu_used_cores=host_cpu_pressure.host_cpu_used_cores,
                host_cpu_used_percent=host_cpu_pressure.host_cpu_used_percent,
                external_cpu_cores_used_estimate=host_cpu_pressure.external_cpu_cores_used_estimate,
                external_cpu_percent_estimate=host_cpu_pressure.external_cpu_percent_estimate,
                external_ffmpeg_process_count=host_cpu_pressure.external_ffmpeg_process_count,
                external_ffmpeg_cpu_cores_estimate=host_cpu_pressure.external_ffmpeg_cpu_cores_estimate,
                host_cpu_sample_mature=host_cpu_pressure.host_cpu_sample_mature,
                memory_bytes=record.memory_bytes,
                total_memory_bytes=total_memory_bytes,
                route2_memory_bytes_total=route2_memory_bytes_total,
                non_retryable_error=record.non_retryable_error or session.last_error,
                mode_ready=session.state == "ready",
            )

        (
            ready_end_seconds,
            effective_playhead_seconds,
            ahead_runway_seconds,
            supply_rate_x,
            supply_observation_seconds,
            _manifest_complete,
            _refill_in_progress,
        ) = self._route2_runtime_supply_metrics_locked(session, epoch)
        server_goodput = self._route2_server_byte_goodput_locked(epoch)
        client_goodput = self._route2_client_goodput_locked(session)

        return Route2AdaptiveShadowInput(
            worker_state=record.state,
            playback_mode=record.playback_mode,
            profile=record.profile,
            source_kind=record.source_kind,
            assigned_threads=record.assigned_threads,
            default_threads=4,
            max_threads=self.settings.route2_max_worker_threads,
            adaptive_max_threads=self.settings.route2_adaptive_max_worker_threads,
            cpu_cores_used=record.cpu_cores_used,
            allocated_cpu_cores=allocated_cpu_cores or None,
            user_cpu_cores_used_total=user_cpu_cores_used_total,
            route2_cpu_upbound_cores=route2_cpu_upbound_cores,
            route2_cpu_cores_used_total=route2_cpu_cores_used_total,
            active_route2_user_count=active_route2_user_count,
            host_cpu_total_cores=host_cpu_pressure.host_cpu_total_cores,
            host_cpu_used_cores=host_cpu_pressure.host_cpu_used_cores,
            host_cpu_used_percent=host_cpu_pressure.host_cpu_used_percent,
            external_cpu_cores_used_estimate=host_cpu_pressure.external_cpu_cores_used_estimate,
            external_cpu_percent_estimate=host_cpu_pressure.external_cpu_percent_estimate,
            external_ffmpeg_process_count=host_cpu_pressure.external_ffmpeg_process_count,
            external_ffmpeg_cpu_cores_estimate=host_cpu_pressure.external_ffmpeg_cpu_cores_estimate,
            host_cpu_sample_mature=host_cpu_pressure.host_cpu_sample_mature,
            memory_bytes=record.memory_bytes,
            total_memory_bytes=total_memory_bytes,
            route2_memory_bytes_total=route2_memory_bytes_total,
            ready_end_seconds=ready_end_seconds,
            effective_playhead_seconds=effective_playhead_seconds,
            ahead_runway_seconds=ahead_runway_seconds,
            required_startup_runway_seconds=120.0 if record.playback_mode == "full" else 45.0,
            supply_rate_x=supply_rate_x,
            supply_observation_seconds=supply_observation_seconds,
            client_goodput_bytes_per_second=(
                float(client_goodput["safe_rate"]) if float(client_goodput["safe_rate"] or 0.0) > 0.0 else None
            ),
            client_goodput_confident=bool(client_goodput["confident"]),
            server_goodput_bytes_per_second=(
                float(server_goodput["safe_rate"]) if float(server_goodput["safe_rate"] or 0.0) > 0.0 else None
            ),
            server_goodput_confident=bool(server_goodput["confident"]),
            non_retryable_error=record.non_retryable_error or session.last_error,
            starvation_risk=self._starvation_risk(session),
            stalled_recovery_needed=self._stalled_recovery_needed(session),
            mode_ready=session.state == "ready",
        )

    def _build_route2_transcode_strategy_input_locked(
        self,
        record: Route2WorkerRecord,
    ) -> tuple[Route2TranscodeStrategyInput, str, bool]:
        session = self._sessions.get(record.session_id)
        if session is None:
            return (
                Route2TranscodeStrategyInput(
                    profile_key=record.profile,
                    source_kind=record.source_kind,
                ),
                "none",
                False,
            )

        trusted_metadata = None
        if session.source_kind == "local":
            item = get_media_item_record(self.settings, item_id=session.media_item_id)
            if item is not None:
                trusted_metadata = resolve_trusted_technical_metadata(self.settings, item)

        metadata_source = "local_ffprobe" if trusted_metadata is not None else "coarse"
        metadata_trusted = trusted_metadata is not None
        metadata = trusted_metadata or {}

        return (
            Route2TranscodeStrategyInput(
                container=metadata.get("container") or session.source_container,
                video_codec=metadata.get("video_codec") or session.source_video_codec,
                video_profile=metadata.get("video_profile"),
                video_level=metadata.get("video_level"),
                audio_codec=metadata.get("audio_codec") or session.source_audio_codec,
                audio_profile=metadata.get("audio_profile"),
                width=metadata.get("width") if metadata.get("width") is not None else session.source_width,
                height=metadata.get("height") if metadata.get("height") is not None else session.source_height,
                pixel_format=metadata.get("pixel_format") if metadata.get("pixel_format") is not None else session.source_pixel_format,
                bit_depth=metadata.get("bit_depth") if metadata.get("bit_depth") is not None else session.source_bit_depth,
                color_transfer=metadata.get("color_transfer"),
                color_primaries=metadata.get("color_primaries"),
                color_space=metadata.get("color_space"),
                hdr_flag=metadata.get("hdr_detected") if metadata.get("hdr_detected") is not None else session.source_hdr_flag,
                dolby_vision_flag=(
                    metadata.get("dolby_vision_detected")
                    if metadata.get("dolby_vision_detected") is not None
                    else session.source_dolby_vision_flag
                ),
                audio_channels=(
                    metadata.get("audio_channels")
                    if metadata.get("audio_channels") is not None
                    else session.source_audio_channels
                ),
                audio_channel_layout=metadata.get("audio_channel_layout"),
                audio_sample_rate=metadata.get("audio_sample_rate"),
                profile_key=session.profile,
                source_kind=session.source_kind,
                original_filename=session.source_original_filename,
            ),
            metadata_source,
            metadata_trusted,
        )

    def _build_route2_command_adapter_preview_locked(
        self,
        record: Route2WorkerRecord,
        *,
        strategy_input: Route2TranscodeStrategyInput,
        strategy_decision,
        strategy_metadata_source: str,
        strategy_metadata_trusted: bool,
    ):
        session = self._sessions.get(record.session_id)
        epoch = None
        if session is not None and session.browser_playback.active_epoch_id:
            epoch = session.browser_playback.epochs.get(session.browser_playback.active_epoch_id)
        if epoch is None and session is not None and record.epoch_id:
            epoch = session.browser_playback.epochs.get(record.epoch_id)

        return build_route2_ffmpeg_command_preview(
            Route2FFmpegCommandAdapterInput(
                ffmpeg_path=str(self.settings.ffmpeg_path),
                profile_key=(
                    session.profile
                    if session is not None and session.profile in MOBILE_PROFILES
                    else record.profile if record.profile in MOBILE_PROFILES
                    else "mobile_1080p"
                ),
                thread_budget=max(1, int(record.assigned_threads or self.settings.route2_max_worker_threads or 4)),
                source_input=(
                    session.source_locator
                    if session is not None
                    else strategy_input.original_filename or record.title
                ),
                source_input_kind=session.source_input_kind if session is not None else "path",
                epoch_start_seconds=epoch.epoch_start_seconds if epoch is not None else 0.0,
                segment_pattern=str(epoch.staging_dir / "segment_%06d.m4s") if epoch is not None else "segment_%06d.m4s",
                staging_manifest_path=str(epoch.staging_manifest_path) if epoch is not None else "ffmpeg.m3u8",
                strategy=strategy_decision.strategy,
                strategy_confidence=strategy_decision.confidence,
                strategy_reason=strategy_decision.reason,
                video_copy_safe=strategy_decision.video_copy_safe,
                audio_copy_safe=strategy_decision.audio_copy_safe,
                risk_flags=list(strategy_decision.risk_flags),
                missing_metadata=list(strategy_decision.missing_metadata),
                metadata_source=strategy_metadata_source,
                metadata_trusted=strategy_metadata_trusted,
            )
        )

    def _route2_worker_display_status_locked(
        self,
        record: Route2WorkerRecord,
        session: MobilePlaybackSession | None,
        epoch: PlaybackEpoch | None,
        payload: dict[str, object],
    ) -> _Route2WorkerDisplayStatus:
        state = str(record.state or "unknown").strip().lower()
        epoch_state = str(epoch.state if epoch is not None else "").strip().lower()
        lifecycle_state = str(session.lifecycle_state if session is not None else "").strip().lower()
        runtime_health = str(payload.get("runtime_playback_health") or "").strip().lower()
        process_active = bool(record.process_exists)
        if not process_active and record.process is not None:
            process_active = record.process.poll() is None
        if not process_active and epoch is not None and epoch.process is not None:
            process_active = epoch.process.poll() is None

        cleanup_delay_seconds = getattr(record, "cleanup_delay_seconds", None)
        cleanup_delayed = bool(getattr(record, "cleanup_delayed", False))
        if cleanup_delay_seconds is not None:
            cleanup_delayed = cleanup_delayed or cleanup_delay_seconds >= 30.0

        if (
            record.non_retryable_error
            or state in {"failed", "interrupted"}
            or epoch_state == "failed"
            or (session is not None and session.last_error)
            or (epoch is not None and epoch.last_error)
        ):
            reason = (
                record.non_retryable_error
                or (session.last_error if session is not None else None)
                or (epoch.last_error if epoch is not None else None)
                or "Worker or playback epoch reported a failure."
            )
            return _Route2WorkerDisplayStatus("failed", "Failed", "danger", str(reason), 1)

        if record.stop_requested and cleanup_delayed:
            return _Route2WorkerDisplayStatus(
                "cleanup_delayed",
                "Cleanup delayed",
                "danger",
                "Stop was requested, but backend cleanup exceeded the explicit delay threshold.",
                2,
            )

        if record.stop_requested and (process_active or state in {"running", "queued", "stopping"}):
            return _Route2WorkerDisplayStatus(
                "stopping",
                "Stopping",
                "warning",
                "Stop was requested and the worker is still ending.",
                3,
            )

        if state in {"stopped", "cancelled", "closed"} or (
            record.finished_at and state not in {"running", "queued", "completed"}
        ):
            return _Route2WorkerDisplayStatus(
                "stopped",
                "Stopped",
                "neutral",
                "Worker has ended; runtime is frozen at the final timestamp.",
                4,
            )

        if state in {"queued", "waiting"}:
            return _Route2WorkerDisplayStatus(
                "waiting",
                "Waiting",
                "info",
                "Worker is waiting for dispatch, source readiness, or capacity.",
                5,
            )

        if (
            session is not None
            and session.client_is_playing
            and (
                session.stalled_recovery_requested
                or runtime_health in {"cpu_thread_starved", "watch_supply_at_risk", "source_bound", "client_bound"}
                or self._starvation_risk(session)
                or self._stalled_recovery_needed(session)
            )
        ):
            return _Route2WorkerDisplayStatus(
                "buffering",
                "Buffering",
                "warning",
                "Playback is active and backend health indicates stall, starvation, or recovery risk.",
                6,
            )

        if lifecycle_state in {"background-suspended", "background_suspended", "background", "hidden", "suspended"}:
            return _Route2WorkerDisplayStatus(
                "background",
                "Background",
                "neutral",
                "Client lifecycle reports the playback surface is backgrounded or suspended.",
                7,
            )

        if (
            session is not None
            and lifecycle_state == "attached"
            and session.client_is_playing is False
            and state == "running"
        ):
            return _Route2WorkerDisplayStatus(
                "paused",
                "Paused",
                "neutral",
                "Client explicitly reports attached playback is not currently playing.",
                8,
            )

        if (
            state in {"running", "starting", "warming", "preparing"}
            and (
                session is None
                or epoch is None
                or not session.client_is_playing
                or epoch_state in {"warming", "starting", "preparing"}
                or not payload.get("publish_segment_count")
            )
        ):
            return _Route2WorkerDisplayStatus(
                "preparing",
                "Preparing",
                "info",
                "Worker is active but initial readiness or active watch evidence is not established yet.",
                9,
            )

        if state == "completed" or (epoch is not None and epoch.transcoder_completed):
            return _Route2WorkerDisplayStatus(
                "complete",
                "Complete",
                "success",
                "Route2 output completed successfully.",
                10,
            )

        if state == "running":
            return _Route2WorkerDisplayStatus(
                "running",
                "Running",
                "success",
                "Worker is active without stop, failure, buffering, or preparation blockers.",
                11,
            )

        return _Route2WorkerDisplayStatus(
            state or "unknown",
            (state or "unknown").replace("_", " ").capitalize(),
            "neutral",
            "No richer display status was available; using the raw worker state.",
            99,
        )

    def get_route2_worker_status(self) -> dict[str, object]:
        with self._lock:
            budget = self._route2_budget_summary_locked()
            grouped_users: dict[int, dict[str, object]] = {}
            payloads_by_worker_id: dict[str, dict[str, object]] = {}
            now_ts = time.time()
            sample_monotonic = time.monotonic()
            sampled_at = utcnow_iso()
            total_memory_bytes = _read_total_memory_bytes()
            route2_cpu_cores_used = 0.0
            route2_memory_bytes = 0
            any_cpu_sampled = False
            any_memory_sampled = False
            for record in sorted(self._route2_workers.values(), key=lambda value: (value.user_id, value.title, value.worker_id)):
                if record.state == "running":
                    self._sample_route2_worker_telemetry_locked(
                        record,
                        total_cpu_cores=int(budget["total_cpu_cores"]),
                        total_memory_bytes=total_memory_bytes,
                        sample_monotonic=sample_monotonic,
                        sample_wall_ts=now_ts,
                        sampled_at=sampled_at,
                    )
                else:
                    self._clear_route2_worker_telemetry_locked(record)
                group = grouped_users.setdefault(
                    record.user_id,
                    {
                        "user_id": record.user_id,
                        "username": record.username,
                        "allocated_cpu_cores": (
                            budget["per_user_budget_cores"]
                            if record.user_id in budget["active_user_ids"]
                            else 0
                        ),
                        "allocated_budget_cores": (
                            budget["per_user_budget_cores"]
                            if record.user_id in budget["active_user_ids"]
                            else 0
                        ),
                        "cpu_cores_used": 0.0,
                        "cpu_percent_of_user_limit": None,
                        "memory_bytes": 0,
                        "memory_percent_of_total": None,
                        "running_workers": 0,
                        "queued_workers": 0,
                        "total_workers": 0,
                        "items": [],
                    },
                )
                group["total_workers"] += 1
                if record.state == "running":
                    group["running_workers"] += 1
                elif record.state == "queued":
                    group["queued_workers"] += 1
                if record.cpu_cores_used is not None:
                    group["cpu_cores_used"] += record.cpu_cores_used
                    route2_cpu_cores_used += record.cpu_cores_used
                    any_cpu_sampled = True
                if record.memory_bytes is not None:
                    group["memory_bytes"] += record.memory_bytes
                    route2_memory_bytes += record.memory_bytes
                    any_memory_sampled = True
                runtime_seconds = None
                if record.started_at:
                    runtime_end_ts = (
                        self._parse_iso_ts(record.finished_at)
                        if record.finished_at and record.state != "running"
                        else now_ts
                    )
                    runtime_seconds = max(0.0, runtime_end_ts - self._parse_iso_ts(record.started_at))
                payload = {
                    "worker_id": record.worker_id,
                    "session_id": record.session_id,
                    "epoch_id": record.epoch_id,
                    "media_item_id": record.media_item_id,
                    "title": record.title,
                    "playback_mode": record.playback_mode,
                    "profile": record.profile,
                    "transcode_profile_key": record.profile,
                    "display_profile_label": _route2_display_profile_label(record.profile),
                    "source_kind": record.source_kind,
                    "state": record.state,
                    "runtime_seconds": round(runtime_seconds, 2) if runtime_seconds is not None else None,
                    "pid": record.pid,
                    "target_position_seconds": round(record.target_position_seconds, 2),
                    "prepared_ranges": record.prepared_ranges,
                    "stop_requested": record.stop_requested,
                    "cleanup_delayed": record.cleanup_delayed,
                    "cleanup_delay_seconds": (
                        round(record.cleanup_delay_seconds, 3)
                        if record.cleanup_delay_seconds is not None
                        else None
                    ),
                    "non_retryable_error": record.non_retryable_error,
                    "failure_count": record.failure_count,
                    "replacement_count": record.replacement_count,
                    "assigned_threads": record.assigned_threads,
                    "fixed_assigned_threads_at_dispatch": record.fixed_assigned_threads_at_dispatch,
                    "adaptive_spawn_dry_run_enabled": record.adaptive_spawn_dry_run_enabled,
                    "adaptive_spawn_dry_run_threads": record.adaptive_spawn_dry_run_threads,
                    "adaptive_spawn_dry_run_reason": record.adaptive_spawn_dry_run_reason,
                    "adaptive_spawn_dry_run_blockers": list(record.adaptive_spawn_dry_run_blockers),
                    "adaptive_spawn_dry_run_policy": record.adaptive_spawn_dry_run_policy,
                    "adaptive_spawn_dry_run_source": record.adaptive_spawn_dry_run_source,
                    "adaptive_spawn_dry_run_sample_age_seconds": (
                        round(record.adaptive_spawn_dry_run_sample_age_seconds, 3)
                        if record.adaptive_spawn_dry_run_sample_age_seconds is not None
                        else None
                    ),
                    "adaptive_spawn_dry_run_sample_mature": record.adaptive_spawn_dry_run_sample_mature,
                    "adaptive_thread_control_enabled": record.adaptive_thread_control_enabled,
                    "adaptive_thread_control_applied": record.adaptive_thread_control_applied,
                    "adaptive_thread_assignment_policy": record.adaptive_thread_assignment_policy,
                    "adaptive_thread_assignment_reason": record.adaptive_thread_assignment_reason,
                    "adaptive_thread_assignment_blockers": list(record.adaptive_thread_assignment_blockers),
                    "adaptive_thread_assignment_fallback_used": record.adaptive_thread_assignment_fallback_used,
                    "assigned_threads_source": record.assigned_threads_source,
                    "process_exists": record.process_exists,
                    "cpu_cores_used": round(record.cpu_cores_used, 3) if record.cpu_cores_used is not None else None,
                    "cpu_percent_of_total": round(record.cpu_percent_of_total, 3) if record.cpu_percent_of_total is not None else None,
                    "cpu_percent": round(record.cpu_percent_of_total, 3) if record.cpu_percent_of_total is not None else None,
                    "memory_bytes": record.memory_bytes,
                    "memory_percent_of_total": round(record.memory_percent_of_total, 3) if record.memory_percent_of_total is not None else None,
                    "io_read_bytes": record.io_read_bytes,
                    "io_write_bytes": record.io_write_bytes,
                    "io_read_bytes_per_second": (
                        round(record.io_read_bytes_per_second, 3)
                        if record.io_read_bytes_per_second is not None
                        else None
                    ),
                    "io_write_bytes_per_second": (
                        round(record.io_write_bytes_per_second, 3)
                        if record.io_write_bytes_per_second is not None
                        else None
                    ),
                    "io_observation_seconds": (
                        round(record.io_observation_seconds, 3)
                        if record.io_observation_seconds is not None
                        else None
                    ),
                    "io_sample_mature": record.io_sample_mature,
                    "io_sample_stale": record.io_sample_stale,
                    "io_missing_metrics": list(record.io_missing_metrics),
                    "route2_source_bytes_per_second": (
                        round(record.io_read_bytes_per_second, 3)
                        if record.io_sample_mature and record.io_read_bytes_per_second is not None
                        else None
                    ),
                    "route2_source_observation_seconds": (
                        round(record.io_observation_seconds, 3)
                        if record.io_sample_mature and record.io_observation_seconds is not None
                        else None
                    ),
                    "route2_source_status": (
                        "proc_io_read_bytes"
                        if record.io_sample_mature and record.io_read_bytes_per_second is not None
                        else "source_throughput_unavailable"
                    ),
                    "telemetry_sampled": record.telemetry_sampled,
                    "last_sampled_at": record.last_sampled_at,
                    "failure_reason": record.non_retryable_error,
                    "started_at": record.started_at,
                    "last_seen_at": record.last_seen_at,
                }
                session = self._sessions.get(record.session_id)
                epoch = (
                    session.browser_playback.epochs.get(record.epoch_id)
                    if session is not None and session.browser_playback.engine_mode == "route2"
                    else None
                )
                if session is not None and epoch is not None:
                    progress = _read_ffmpeg_progress_snapshot(
                        epoch.epoch_dir / "ffmpeg.progress.log",
                        now_ts=now_ts,
                    )
                    progress_updated_at = (
                        datetime.fromtimestamp(progress.updated_at_ts).astimezone().isoformat()
                        if progress.updated_at_ts is not None
                        else None
                    )
                    payload["ffmpeg_progress_out_time_seconds"] = (
                        round(progress.out_time_seconds, 3)
                        if progress.out_time_seconds is not None
                        else None
                    )
                    payload["ffmpeg_progress_speed_x"] = (
                        round(progress.speed_x, 3)
                        if progress.speed_x is not None
                        else None
                    )
                    payload["ffmpeg_progress_fps"] = (
                        round(progress.fps, 3)
                        if progress.fps is not None
                        else None
                    )
                    payload["ffmpeg_progress_frame"] = progress.frame
                    payload["ffmpeg_progress_updated_at"] = progress_updated_at
                    payload["ffmpeg_progress_state"] = progress.progress_state
                    payload["ffmpeg_progress_stale"] = progress.stale
                    payload["ffmpeg_progress_missing_metrics"] = list(progress.missing_metrics)
                    payload["publish_segment_count"] = epoch.publish_segment_count
                    payload["segment_publish_count"] = epoch.publish_segment_count
                    payload["publish_init_latency_seconds"] = (
                        round(epoch.publish_init_latency_seconds, 6)
                        if epoch.publish_init_latency_seconds is not None
                        else None
                    )
                    payload["last_publish_latency_seconds"] = (
                        round(epoch.last_publish_latency_seconds, 6)
                        if epoch.last_publish_latency_seconds is not None
                        else None
                    )
                    payload["publish_latency_avg_seconds"] = (
                        round(epoch.publish_latency_total_seconds / epoch.publish_segment_count, 6)
                        if epoch.publish_segment_count > 0
                        else None
                    )
                    payload["publish_latency_max_seconds"] = (
                        round(epoch.publish_latency_max_seconds, 6)
                        if epoch.publish_latency_max_seconds is not None
                        else None
                    )
                    payload["last_publish_kind"] = epoch.last_publish_kind
                    active_health = self._evaluate_route2_active_playback_health_locked(session, epoch, record)
                    payload["runtime_playback_health"] = active_health.status
                    payload["runtime_playback_health_reason"] = active_health.reason
                    payload["runtime_supply_rate_x"] = (
                        round(active_health.supply_rate_x, 3)
                        if active_health.supply_rate_x is not None
                        else None
                    )
                    payload["runtime_supply_observation_seconds"] = (
                        round(active_health.supply_observation_seconds, 2)
                        if active_health.supply_observation_seconds is not None
                        else None
                    )
                    payload["runtime_runway_seconds"] = (
                        round(active_health.runway_seconds, 2)
                        if active_health.runway_seconds is not None
                        else None
                    )
                    payload["runtime_rebalance_role"] = active_health.runtime_rebalance_role
                    payload["runtime_rebalance_reason"] = active_health.runtime_rebalance_reason
                    payload["runtime_rebalance_target_threads"] = active_health.runtime_rebalance_target_threads
                    payload["runtime_rebalance_can_donate_threads"] = active_health.runtime_rebalance_can_donate_threads
                    payload["runtime_rebalance_priority"] = active_health.runtime_rebalance_priority
                    payload.update(self._route2_bad_condition_reserve_payload_locked(session, epoch))
                else:
                    payload["runtime_playback_health"] = None
                    payload["runtime_playback_health_reason"] = None
                    payload["runtime_supply_rate_x"] = None
                    payload["runtime_supply_observation_seconds"] = None
                    payload["runtime_runway_seconds"] = None
                    payload["runtime_rebalance_role"] = "neutral"
                    payload["runtime_rebalance_reason"] = None
                    payload["runtime_rebalance_target_threads"] = None
                    payload["runtime_rebalance_can_donate_threads"] = 0
                    payload["runtime_rebalance_priority"] = 0
                    payload["bad_condition_reserve_required"] = False
                    payload["bad_condition_reason"] = None
                    payload["bad_condition_supply_floor"] = ROUTE2_BAD_CONDITION_SUPPLY_FLOOR_RATE_X
                    payload["bad_condition_strong"] = False
                    payload["reserve_start_seconds"] = None
                    payload["reserve_target_ready_end_seconds"] = None
                    payload["reserve_actual_ready_end_seconds"] = None
                    payload["reserve_required_seconds"] = None
                    payload["reserve_remaining_seconds"] = None
                    payload["reserve_satisfied"] = False
                    payload["reserve_blocks_admission"] = False
                    payload["reserve_eta_seconds"] = None
                    payload["runway_delta_per_second"] = None
                    payload["runway_delta_observation_seconds"] = None
                    payload["runway_delta_mature"] = False
                    payload["ffmpeg_progress_out_time_seconds"] = None
                    payload["ffmpeg_progress_speed_x"] = None
                    payload["ffmpeg_progress_fps"] = None
                    payload["ffmpeg_progress_frame"] = None
                    payload["ffmpeg_progress_updated_at"] = None
                    payload["ffmpeg_progress_state"] = "unknown"
                    payload["ffmpeg_progress_stale"] = True
                    payload["ffmpeg_progress_missing_metrics"] = ["ffmpeg_progress_epoch_missing"]
                    payload["publish_segment_count"] = 0
                    payload["segment_publish_count"] = 0
                    payload["publish_init_latency_seconds"] = None
                    payload["last_publish_latency_seconds"] = None
                    payload["publish_latency_avg_seconds"] = None
                    payload["publish_latency_max_seconds"] = None
                    payload["last_publish_kind"] = None
                display_status = self._route2_worker_display_status_locked(record, session, epoch, payload)
                payload["display_status"] = display_status.status
                payload["display_status_label"] = display_status.label
                payload["display_status_tone"] = display_status.tone
                payload["display_status_reason"] = display_status.reason
                payload["display_status_priority"] = display_status.priority
                group["items"].append(payload)
                payloads_by_worker_id[record.worker_id] = payload
            for group in grouped_users.values():
                allocated_cpu_cores = max(0, int(group["allocated_cpu_cores"]))
                cpu_cores_used = float(group["cpu_cores_used"]) if group["cpu_cores_used"] else 0.0
                memory_bytes = int(group["memory_bytes"]) if group["memory_bytes"] else 0
                group["cpu_cores_used"] = round(cpu_cores_used, 3) if cpu_cores_used > 0 else None
                group["cpu_percent_of_user_limit"] = (
                    round((cpu_cores_used / allocated_cpu_cores) * 100, 3)
                    if allocated_cpu_cores > 0 and cpu_cores_used > 0
                    else None
                )
                group["memory_bytes"] = memory_bytes if memory_bytes > 0 else None
                group["memory_percent_of_total"] = (
                    round((memory_bytes / total_memory_bytes) * 100, 3)
                    if total_memory_bytes and memory_bytes > 0
                    else None
                )
            owned_route2_pids = {
                int(record.pid)
                for record in self._route2_workers.values()
                if isinstance(record.pid, int) and record.pid > 0
            }
            host_cpu_pressure = self._sample_host_cpu_pressure_locked(
                route2_cpu_cores_used_total=route2_cpu_cores_used if any_cpu_sampled else None,
                owned_route2_pids=owned_route2_pids,
                sample_monotonic=sample_monotonic,
            )
            resource_snapshot = self._store_route2_resource_snapshot_locked(
                sampled_at_ts=now_ts,
                sampled_at=sampled_at,
                total_memory_bytes=total_memory_bytes,
                host_cpu_pressure=host_cpu_pressure,
            )
            resource_snapshot = self._latest_route2_resource_snapshot_locked(now_ts=now_ts)
            host_cpu_pressure = _host_cpu_pressure_from_resource_snapshot(resource_snapshot)
            psi_snapshot = _read_linux_psi_snapshot()
            cgroup_snapshot, latest_cgroup_cpu_stat = _read_cgroup_telemetry_snapshot(
                previous_cpu_stat=self._last_cgroup_cpu_stat,
            )
            if latest_cgroup_cpu_stat is not None:
                self._last_cgroup_cpu_stat = latest_cgroup_cpu_stat
            closed_loop_donors: list[tuple[float, str]] = []
            for record in sorted(self._route2_workers.values(), key=lambda value: value.worker_id):
                payload = payloads_by_worker_id.get(record.worker_id)
                if payload is None:
                    continue
                group = grouped_users.get(record.user_id)
                allocated_cpu_cores = int(group.get("allocated_cpu_cores") or 0) if group is not None else 0
                user_cpu_cores_used_total = (
                    float(group.get("cpu_cores_used"))
                    if group is not None and group.get("cpu_cores_used") is not None
                    else None
                )
                adaptive_input = self._build_route2_adaptive_shadow_input_locked(
                    record,
                    allocated_cpu_cores=allocated_cpu_cores,
                    user_cpu_cores_used_total=user_cpu_cores_used_total,
                    route2_cpu_cores_used_total=route2_cpu_cores_used if any_cpu_sampled else None,
                    route2_cpu_upbound_cores=int(budget["route2_cpu_upbound_cores"]),
                    active_route2_user_count=int(budget["active_decoding_user_count"]),
                    host_cpu_pressure=host_cpu_pressure,
                    total_memory_bytes=total_memory_bytes,
                    route2_memory_bytes_total=route2_memory_bytes if any_memory_sampled else None,
                )
                adaptive_decision = classify_route2_adaptive_shadow(adaptive_input)
                payload["adaptive_bottleneck_class"] = adaptive_decision.bottleneck_class
                payload["adaptive_bottleneck_confidence"] = round(adaptive_decision.bottleneck_confidence, 3)
                payload["adaptive_recommended_threads"] = adaptive_decision.recommended_threads
                payload["adaptive_current_threads"] = adaptive_decision.current_threads
                payload["adaptive_safe_to_increase_threads"] = adaptive_decision.safe_to_increase_threads
                payload["adaptive_safe_to_decrease_threads"] = adaptive_decision.safe_to_decrease_threads
                payload["adaptive_reason"] = adaptive_decision.reason
                payload["adaptive_missing_metrics"] = adaptive_decision.missing_metrics
                session = self._sessions.get(record.session_id)
                epoch = (
                    session.browser_playback.epochs.get(record.epoch_id)
                    if session is not None and session.browser_playback.engine_mode == "route2"
                    else None
                )
                if session is not None and epoch is not None:
                    closed_loop_progress = _read_ffmpeg_progress_snapshot(
                        epoch.epoch_dir / "ffmpeg.progress.log",
                        now_ts=now_ts,
                    )
                    closed_loop_health = self._evaluate_route2_active_playback_health_locked(session, epoch, record)
                    closed_loop_decision = self._evaluate_route2_closed_loop_dry_run_locked(
                        session,
                        epoch,
                        record,
                        active_health=closed_loop_health,
                        progress=closed_loop_progress,
                        host_cpu_pressure=host_cpu_pressure,
                        psi_snapshot=psi_snapshot,
                        cgroup_snapshot=cgroup_snapshot,
                        adaptive_bottleneck_class=adaptive_decision.bottleneck_class,
                        route2_cpu_cores_used_total=route2_cpu_cores_used if any_cpu_sampled else None,
                        route2_cpu_upbound_cores=int(budget["route2_cpu_upbound_cores"]),
                        total_memory_bytes=total_memory_bytes,
                        route2_memory_bytes_total=route2_memory_bytes if any_memory_sampled else None,
                    )
                else:
                    closed_loop_decision = _Route2ClosedLoopDryRunDecision(
                        role="metrics_immature",
                        reasons=["route2_session_or_epoch_missing"],
                        confidence=0.5,
                        prepare_boost_needed=False,
                        prepare_boost_target_threads=None,
                        downshift_candidate=False,
                        downshift_target_threads=None,
                        needs_resource=False,
                        needs_resource_reason=None,
                        donor_candidate=False,
                        theoretical_donate_threads=0,
                        protected_reason=None,
                        admission_should_block_new_users=False,
                        admission_block_reason=None,
                        admission_block_reasons=[],
                        boost_blocked=False,
                        boost_blockers=[],
                        boost_warning_reasons=[],
                        limiting_factor=self._empty_route2_limiting_factor_decision(
                            reason="route2_session_or_epoch_missing",
                        ),
                        primary_bottleneck="metrics_immature",
                    )
                payload.update(self._closed_loop_dry_run_payload(closed_loop_decision))
                payload.update(self._closed_loop_runtime_rebalance_payload(closed_loop_decision))
                if closed_loop_decision.donor_candidate:
                    closed_loop_donors.append((closed_loop_decision.donor_score, record.worker_id))
                strategy_input, strategy_metadata_source, strategy_metadata_trusted = (
                    self._build_route2_transcode_strategy_input_locked(record)
                )
                strategy_decision = select_route2_transcode_strategy(strategy_input)
                payload["route2_transcode_strategy"] = strategy_decision.strategy
                payload["route2_transcode_strategy_confidence"] = strategy_decision.confidence
                payload["route2_transcode_strategy_reason"] = strategy_decision.reason
                payload["route2_video_copy_safe"] = strategy_decision.video_copy_safe
                payload["route2_audio_copy_safe"] = strategy_decision.audio_copy_safe
                payload["route2_strategy_risk_flags"] = strategy_decision.risk_flags
                payload["route2_strategy_missing_metadata"] = strategy_decision.missing_metadata
                payload["route2_strategy_metadata_source"] = strategy_metadata_source
                payload["route2_strategy_metadata_trusted"] = strategy_metadata_trusted
                command_adapter_preview = self._build_route2_command_adapter_preview_locked(
                    record,
                    strategy_input=strategy_input,
                    strategy_decision=strategy_decision,
                    strategy_metadata_source=strategy_metadata_source,
                    strategy_metadata_trusted=strategy_metadata_trusted,
                )
                payload["route2_command_adapter_preview_strategy"] = command_adapter_preview.adapter_strategy
                payload["route2_command_adapter_active"] = command_adapter_preview.active_enabled
                payload["route2_command_adapter_summary"] = command_adapter_preview.command_preview_summary
                payload["route2_command_adapter_fallback_reason"] = command_adapter_preview.fallback_reason
            for rank, (_score, worker_id) in enumerate(
                sorted(closed_loop_donors, key=lambda value: (-value[0], value[1])),
                start=1,
            ):
                donor_payload = payloads_by_worker_id.get(worker_id)
                if donor_payload is not None:
                    donor_payload["closed_loop_donor_rank"] = rank
            shared_supply_groups = self._apply_route2_shared_supply_status_locked(payloads_by_worker_id)
            route2_cpu_percent_of_total = (
                round((route2_cpu_cores_used / int(budget["total_cpu_cores"])) * 100, 3)
                if any_cpu_sampled
                else None
            )
            route2_cpu_percent_of_upbound = (
                round((route2_cpu_cores_used / int(budget["route2_cpu_upbound_cores"])) * 100, 3)
                if any_cpu_sampled and int(budget["route2_cpu_upbound_cores"]) > 0
                else None
            )
            return {
                **budget,
                **build_shared_output_store_capability(self._route2_root),
                "shared_output_store_records_count": count_shared_output_metadata_records(self._route2_root),
                "shared_output_metadata_write_errors": list(self._shared_output_metadata_write_errors),
                "shared_output_init_records_count": count_shared_output_init_records(self._route2_root),
                "shared_output_init_write_errors": list(self._shared_output_init_write_errors),
                "shared_output_segments_records_count": count_shared_output_segment_records(self._route2_root),
                "shared_output_ranges_media_bytes_present_count": (
                    count_shared_output_ranges_media_bytes_present_records(self._route2_root)
                ),
                "shared_output_segment_write_errors": list(self._shared_output_segment_write_errors),
                "route2_cpu_cores_used": round(route2_cpu_cores_used, 3) if any_cpu_sampled else None,
                "route2_cpu_cores_used_total": round(route2_cpu_cores_used, 3) if any_cpu_sampled else None,
                "route2_cpu_percent_of_total": route2_cpu_percent_of_total,
                "route2_cpu_percent_of_upbound": route2_cpu_percent_of_upbound,
                "host_cpu_total_cores": host_cpu_pressure.host_cpu_total_cores,
                "host_cpu_used_cores": (
                    round(host_cpu_pressure.host_cpu_used_cores, 3)
                    if host_cpu_pressure.host_cpu_used_cores is not None
                    else None
                ),
                "host_cpu_used_percent": (
                    round(host_cpu_pressure.host_cpu_used_percent, 4)
                    if host_cpu_pressure.host_cpu_used_percent is not None
                    else None
                ),
                "external_cpu_cores_used_estimate": (
                    round(host_cpu_pressure.external_cpu_cores_used_estimate, 3)
                    if host_cpu_pressure.external_cpu_cores_used_estimate is not None
                    else None
                ),
                "external_cpu_percent_estimate": (
                    round(host_cpu_pressure.external_cpu_percent_estimate, 4)
                    if host_cpu_pressure.external_cpu_percent_estimate is not None
                    else None
                ),
                "external_ffmpeg_process_count": host_cpu_pressure.external_ffmpeg_process_count,
                "route2_worker_ffmpeg_process_count": host_cpu_pressure.route2_worker_ffmpeg_process_count,
                "elvern_owned_ffmpeg_process_count": host_cpu_pressure.elvern_owned_ffmpeg_process_count,
                "elvern_owned_ffmpeg_cpu_cores_estimate": (
                    round(host_cpu_pressure.elvern_owned_ffmpeg_cpu_cores_estimate, 3)
                    if host_cpu_pressure.elvern_owned_ffmpeg_cpu_cores_estimate is not None
                    else None
                ),
                "external_ffmpeg_cpu_cores_estimate": (
                    round(host_cpu_pressure.external_ffmpeg_cpu_cores_estimate, 3)
                    if host_cpu_pressure.external_ffmpeg_cpu_cores_estimate is not None
                    else None
                ),
                "host_cpu_sample_mature": host_cpu_pressure.host_cpu_sample_mature,
                "resource_sample_age_seconds": (
                    round(now_ts - resource_snapshot.sampled_at_ts, 3)
                    if resource_snapshot is not None
                    else None
                ),
                "route2_resource_sample_age_seconds": (
                    round(now_ts - resource_snapshot.sampled_at_ts, 3)
                    if resource_snapshot is not None
                    else None
                ),
                "resource_sample_mature": (
                    bool(resource_snapshot.sample_mature and not resource_snapshot.sample_stale)
                    if resource_snapshot is not None
                    else False
                ),
                "route2_resource_sample_mature": (
                    bool(resource_snapshot.sample_mature and not resource_snapshot.sample_stale)
                    if resource_snapshot is not None
                    else False
                ),
                "resource_sample_stale": resource_snapshot.sample_stale if resource_snapshot is not None else True,
                "route2_resource_sample_stale": resource_snapshot.sample_stale if resource_snapshot is not None else True,
                "external_pressure_level": (
                    resource_snapshot.external_pressure_level if resource_snapshot is not None else "unknown"
                ),
                "external_pressure_reason": (
                    resource_snapshot.external_pressure_reason if resource_snapshot is not None else "resource_snapshot_missing"
                ),
                "resource_missing_metrics": resource_snapshot.missing_metrics if resource_snapshot is not None else ["resource_snapshot"],
                "route2_resource_missing_metrics": (
                    resource_snapshot.missing_metrics if resource_snapshot is not None else ["resource_snapshot"]
                ),
                "psi_sample_available": psi_snapshot.sample_available,
                "psi_cpu_some_avg10": psi_snapshot.cpu_some_avg10,
                "psi_cpu_full_avg10": psi_snapshot.cpu_full_avg10,
                "psi_io_some_avg10": psi_snapshot.io_some_avg10,
                "psi_io_full_avg10": psi_snapshot.io_full_avg10,
                "psi_memory_some_avg10": psi_snapshot.memory_some_avg10,
                "psi_memory_full_avg10": psi_snapshot.memory_full_avg10,
                "psi_missing_metrics": psi_snapshot.missing_metrics,
                "cgroup_pressure_available": cgroup_snapshot.pressure_available,
                "cgroup_cpu_nr_periods": cgroup_snapshot.cpu_nr_periods,
                "cgroup_cpu_nr_throttled": cgroup_snapshot.cpu_nr_throttled,
                "cgroup_cpu_throttled_usec": cgroup_snapshot.cpu_throttled_usec,
                "cgroup_cpu_throttled_delta": cgroup_snapshot.cpu_throttled_delta,
                "cgroup_cpu_throttled_usec_delta": cgroup_snapshot.cpu_throttled_usec_delta,
                "cgroup_cpu_some_avg10": cgroup_snapshot.cpu_some_avg10,
                "cgroup_cpu_full_avg10": cgroup_snapshot.cpu_full_avg10,
                "cgroup_io_some_avg10": cgroup_snapshot.io_some_avg10,
                "cgroup_io_full_avg10": cgroup_snapshot.io_full_avg10,
                "cgroup_memory_some_avg10": cgroup_snapshot.memory_some_avg10,
                "cgroup_memory_full_avg10": cgroup_snapshot.memory_full_avg10,
                "cgroup_missing_metrics": cgroup_snapshot.missing_metrics,
                "total_memory_bytes": total_memory_bytes,
                "route2_memory_bytes": route2_memory_bytes if any_memory_sampled else None,
                "route2_memory_bytes_total": route2_memory_bytes if any_memory_sampled else None,
                "route2_memory_percent_of_total": (
                    round((route2_memory_bytes / total_memory_bytes) * 100, 3)
                    if any_memory_sampled and total_memory_bytes
                    else None
                ),
                "shared_supply_groups": shared_supply_groups,
                "workers_by_user": sorted(grouped_users.values(), key=lambda value: ((value["username"] or ""), value["user_id"])),
            }

    def invalidate_user_sessions(self, user_id: int, *, reason: str) -> int:
        with self._lock:
            sessions = self._collect_sessions_to_invalidate_locked(
                lambda session: session.user_id == user_id,
                reason=reason,
            )
        self._invalidate_sessions(sessions)
        return len(sessions)

    def invalidate_auth_session(self, auth_session_id: int, *, reason: str) -> int:
        with self._lock:
            sessions = self._collect_sessions_to_invalidate_locked(
                lambda session: session.auth_session_id == auth_session_id,
                reason=reason,
            )
        self._invalidate_sessions(sessions)
        return len(sessions)

    def _collect_sessions_to_invalidate_locked(
        self,
        predicate,
        *,
        reason: str,
    ) -> list[MobilePlaybackSession]:
        sessions: list[MobilePlaybackSession] = []
        for session in list(self._sessions.values()):
            if not predicate(session):
                continue
            session.state = "failed"
            session.last_error = self._session_invalidation_message(reason)
            sessions.append(session)
            self._sessions.pop(session.session_id, None)
            self._unregister_session_locked(session)
        return sessions

    def _invalidate_sessions(self, sessions: list[MobilePlaybackSession]) -> None:
        for session in sessions:
            self._terminate_session(session)

    def _session_invalidation_message(self, reason: str) -> str:
        if reason == "user_disabled":
            return "This account has been disabled. Browser playback preparation was stopped."
        if reason == "admin_revoked":
            return "This signed-in session was revoked. Browser playback preparation was stopped."
        if reason == "self_deleted":
            return "This account was deleted. Browser playback preparation was stopped."
        return "Browser playback preparation was stopped by backend control."

    def _recover_stale_route2_worker_metadata(self) -> None:
        route2_sessions_root = self._route2_root / "sessions"
        if not route2_sessions_root.exists():
            return
        interrupted_error = "Route 2 worker was interrupted by backend restart"
        for metadata_path in route2_sessions_root.glob("*/epochs/*/epoch.json"):
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            claimed_worker = bool(payload.get("active_worker_id"))
            incomplete_state = str(payload.get("state") or "") in {"starting", "warming"}
            if not claimed_worker and not incomplete_state:
                continue
            payload["active_worker_id"] = None
            if not bool(payload.get("transcoder_completed")):
                payload["state"] = "failed"
                payload["last_error"] = interrupted_error
            payload["updated_at"] = utcnow_iso()
            try:
                metadata_path.write_text(
                    json.dumps(payload, ensure_ascii=True, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                continue

    def _validate_transcoding(self) -> None:
        if not self.settings.transcode_enabled:
            raise ValueError("Experimental playback is disabled on this server")
        if not self.settings.ffmpeg_path:
            raise ValueError("ffmpeg was not found on the server")

    def _select_engine_mode(self, value: str | None) -> str:
        candidate = (value or "").strip().lower()
        if not candidate:
            candidate = "route2" if self.settings.browser_playback_route2_enabled else "legacy"
        if candidate not in {"legacy", "route2"}:
            raise ValueError("Unsupported browser playback engine mode")
        if candidate == "route2":
            if not self.settings.browser_playback_route2_enabled:
                raise ValueError("Browser Playback Route 2 is disabled on this server")
        return candidate

    def _select_playback_mode(self, value: str | None) -> str:
        candidate = (value or "").strip().lower()
        if not candidate:
            return "lite"
        if candidate not in {"lite", "full"}:
            raise ValueError("Unsupported browser playback mode")
        return candidate

    def _normalize_user_role(self, value: str | None) -> str:
        candidate = (value or "").strip().lower()
        return ADMIN_USER_ROLE if candidate == ADMIN_USER_ROLE else STANDARD_USER_ROLE

    def _build_browser_playback_session(self, *, engine_mode: str, playback_mode: str) -> BrowserPlaybackSession:
        return BrowserPlaybackSession(
            engine_mode=engine_mode,
            playback_mode=playback_mode,
            state="legacy" if engine_mode == "legacy" else "starting",
        )

    def _initialize_route2_session_locked(self, session: MobilePlaybackSession) -> None:
        _initialize_route2_session_locked_impl(
            session,
            build_route2_epoch_locked=self._build_route2_epoch_locked,
            ensure_route2_epoch_workspace_locked=self._ensure_route2_epoch_workspace_locked,
            ensure_route2_full_preflight_locked=self._ensure_route2_full_preflight_locked,
        )

    def _build_route2_epoch_locked(self, session: MobilePlaybackSession) -> PlaybackEpoch:
        return _build_route2_epoch_locked_impl(
            self._route2_root,
            session,
            clamp_time=self._clamp_time,
        )

    def _log_route2_event(
        self,
        event: str,
        *,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch | None = None,
        level: int = logging.INFO,
        **details: object,
    ) -> None:
        payload: dict[str, object] = {
            "event": event,
            "session_id": session.session_id,
            "media_item_id": session.media_item_id,
            "engine_mode": session.browser_playback.engine_mode,
            "playback_mode": session.browser_playback.playback_mode,
            "session_state": session.state,
            "browser_session_state": session.browser_playback.state,
            "attach_revision": session.browser_playback.attach_revision,
            "client_attach_revision": session.browser_playback.client_attach_revision,
            "active_epoch_id": session.browser_playback.active_epoch_id,
            "replacement_epoch_id": session.browser_playback.replacement_epoch_id,
        }
        if epoch is not None:
            payload.update(
                {
                    "epoch_id": epoch.epoch_id,
                    "epoch_state": epoch.state,
                    "epoch_start_seconds": round(epoch.epoch_start_seconds, 2),
                    "attach_position_seconds": round(epoch.attach_position_seconds, 2),
                    "published_frontier_segment": epoch.contiguous_published_through_segment,
                }
            )
        payload.update(details)
        logger.log(level, "Route2 %s", json.dumps(payload, sort_keys=True, default=str))

    def _log_route2_truth_violation(
        self,
        violation: str,
        *,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        **details: object,
    ) -> None:
        self._log_route2_event(
            "truth_violation",
            session=session,
            epoch=epoch,
            level=logging.WARNING,
            violation=violation,
            **details,
        )

    def _guard_route2_full_attach_boundary_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch | None,
        *,
        attach_eligible: bool,
        guard_path: str,
    ) -> bool:
        browser_session = session.browser_playback
        if not attach_eligible:
            return False
        if (
            browser_session.engine_mode != "route2"
            or browser_session.playback_mode != "full"
            or epoch is None
        ):
            return True
        full_mode_gate = self._route2_full_mode_gate_locked(session, epoch)
        if bool(full_mode_gate["mode_ready"]):
            browser_session.last_full_contract_violation_signature = ""
            return True
        signature = (
            f"{guard_path}:{epoch.epoch_id}:{browser_session.attach_revision}:"
            f"{full_mode_gate.get('mode_state')}"
        )
        if browser_session.last_full_contract_violation_signature != signature:
            browser_session.last_full_contract_violation_signature = signature
            self._log_route2_event(
                "full_contract_violation_blocked",
                session=session,
                epoch=epoch,
                level=logging.ERROR,
                violation="full_attach_without_mode_ready",
                guard_path=guard_path,
                attempted_attach_eligible=attach_eligible,
                mode_ready=False,
                mode_state=str(full_mode_gate.get("mode_state") or "unknown"),
                mode_estimate_seconds=full_mode_gate.get("mode_estimate_seconds"),
                mode_estimate_source=str(full_mode_gate.get("mode_estimate_source") or "none"),
            )
        return False

    def _route2_full_preflight_cache_path(self, session: MobilePlaybackSession) -> Path:
        return _route2_full_preflight_cache_path_impl(
            self._route2_root,
            session,
        )

    def _parse_bitrate_bps(self, value: str) -> int:
        return _parse_bitrate_bps_impl(value)

    def _route2_profile_floor_bytes_per_second(self, profile_key: str) -> float:
        return _route2_profile_floor_bytes_per_second_impl(profile_key)

    def _route2_profile_floor_segment_bytes(self, profile_key: str) -> int:
        return _route2_profile_floor_segment_bytes_impl(profile_key)

    def _route2_full_preflight_source_input(self, session: MobilePlaybackSession) -> tuple[str, str | None, str | None]:
        return _route2_full_preflight_source_input_impl(
            self.settings,
            session,
        )

    def _route2_full_scan_packet_bins(
        self,
        source_input: str,
        *,
        select_stream: str,
        total_segments: int,
    ) -> list[int]:
        return _route2_full_scan_packet_bins_impl(
            self.settings,
            source_input,
            select_stream=select_stream,
            total_segments=total_segments,
        )

    def _build_route2_full_source_bin_bytes(self, session: MobilePlaybackSession) -> list[int]:
        return _build_route2_full_source_bin_bytes_impl(
            self.settings,
            session,
            route2_full_preflight_source_input=self._route2_full_preflight_source_input,
            route2_full_scan_packet_bins=self._route2_full_scan_packet_bins,
            route2_profile_floor_segment_bytes=self._route2_profile_floor_segment_bytes,
        )

    def _load_route2_full_preflight_cache_locked(self, session: MobilePlaybackSession) -> bool:
        return _load_route2_full_preflight_cache_locked_impl(
            session,
            route2_full_preflight_cache_path=self._route2_full_preflight_cache_path,
        )

    def _ensure_route2_full_preflight_locked(self, session: MobilePlaybackSession) -> None:
        _ensure_route2_full_preflight_locked_impl(
            session,
            load_route2_full_preflight_cache_locked=self._load_route2_full_preflight_cache_locked,
            run_route2_full_preflight_worker=self._run_route2_full_preflight_worker,
        )

    def _run_route2_full_preflight_worker(self, session_id: str) -> None:
        def get_route2_session_locked(active_session_id: str) -> MobilePlaybackSession | None:
            with self._lock:
                session = self._sessions.get(active_session_id)
                if session is None or session.browser_playback.engine_mode != "route2":
                    return None
                return session

        _run_route2_full_preflight_worker_impl(
            session_id,
            get_route2_session_locked=get_route2_session_locked,
            build_route2_full_source_bin_bytes=self._build_route2_full_source_bin_bytes,
            route2_full_preflight_cache_path=self._route2_full_preflight_cache_path,
            write_json_atomic=self._write_json_atomic,
        )

    def _issue_route2_attach_revision_locked(
        self,
        session: MobilePlaybackSession,
        *,
        next_revision: int,
        reason: str,
        epoch: PlaybackEpoch | None = None,
    ) -> None:
        browser_session = session.browser_playback
        next_value = max(0, int(next_revision))
        if browser_session.attach_revision == next_value:
            return
        if next_value > 0 and not self._guard_route2_full_attach_boundary_locked(
            session,
            epoch,
            attach_eligible=True,
            guard_path=f"issue_attach_revision:{reason}",
        ):
            return
        browser_session.attach_revision = next_value
        browser_session.attach_revision_issued_at_ts = time.time() if next_value > 0 else 0.0
        browser_session.last_attach_warning_revision = 0
        self._log_route2_event(
            "attach_revision_issued",
            session=session,
            epoch=epoch,
            reason=reason,
            next_revision=next_value,
        )

    def _mark_route2_epoch_draining_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        *,
        reason: str,
        required_client_revision: int | None = None,
    ) -> None:
        previous_state = epoch.state
        now_ts = time.time()
        epoch.state = "draining"
        if epoch.drain_started_at_ts is None:
            epoch.drain_started_at_ts = now_ts
        epoch.last_media_access_at_ts = max(epoch.last_media_access_at_ts, now_ts)
        if required_client_revision is not None:
            epoch.drain_target_attach_revision = max(
                epoch.drain_target_attach_revision,
                int(required_client_revision),
            )
        if previous_state != "draining":
            self._log_route2_event(
                "epoch_draining",
                session=session,
                epoch=epoch,
                reason=reason,
                required_client_revision=epoch.drain_target_attach_revision or None,
            )

    def _route2_epoch_is_draining_expired_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        *,
        now_ts: float | None = None,
    ) -> bool:
        return _route2_epoch_is_draining_expired_locked_impl(
            session,
            epoch,
            now_ts=now_ts,
        )

    def _cleanup_route2_draining_epochs_locked(
        self,
        session: MobilePlaybackSession,
        *,
        now_ts: float | None = None,
    ) -> None:
        _cleanup_route2_draining_epochs_locked_impl(
            session,
            route2_epoch_is_draining_expired_locked=self._route2_epoch_is_draining_expired_locked,
            log_route2_event=self._log_route2_event,
            discard_route2_epoch_locked=self._discard_route2_epoch_locked,
            now_ts=now_ts,
        )

    def _prepare_route2_epoch_access_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        *,
        media_kind: str,
    ) -> None:
        _prepare_route2_epoch_access_locked_impl(
            session,
            epoch,
            media_kind=media_kind,
            touch_session_locked=self._touch_session_locked,
            log_route2_event=self._log_route2_event,
            discard_route2_epoch_locked=self._discard_route2_epoch_locked,
        )

    def _route2_epoch_ready_end_seconds(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> float:
        return _route2_epoch_ready_end_seconds_impl(
            session,
            epoch,
        )

    def _record_route2_frontier_sample_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        *,
        now_ts: float | None = None,
    ) -> None:
        return _record_route2_frontier_sample_locked_impl(
            session,
            epoch,
            route2_epoch_ready_end_seconds_locked=self._route2_epoch_ready_end_seconds,
            now_ts=now_ts,
        )

    def _record_route2_byte_sample_locked(
        self,
        epoch: PlaybackEpoch,
        *,
        now_ts: float | None = None,
    ) -> None:
        return _record_route2_byte_sample_locked_impl(
            epoch,
            now_ts=now_ts,
        )

    def _record_route2_client_probe_sample_locked(
        self,
        session: MobilePlaybackSession,
        *,
        probe_bytes: int | None,
        probe_duration_ms: int | None,
        now_ts: float | None = None,
    ) -> None:
        return _record_route2_client_probe_sample_locked_impl(
            session,
            probe_bytes=probe_bytes,
            probe_duration_ms=probe_duration_ms,
            now_ts=now_ts,
        )

    def _harmonic_mean_locked(self, values: list[float]) -> float:
        return _harmonic_mean_locked_impl(values)

    def _percentile_locked(self, values: list[float], percentile: float) -> float:
        return _percentile_locked_impl(values, percentile)

    def _conservative_goodput_locked(
        self,
        rates: list[float],
        *,
        observation_seconds: float,
    ) -> dict[str, float | int | bool]:
        return _conservative_goodput_locked_impl(
            rates,
            observation_seconds=observation_seconds,
        )

    def _route2_server_byte_goodput_locked(
        self,
        epoch: PlaybackEpoch,
    ) -> dict[str, float | int | bool]:
        return _route2_server_byte_goodput_locked_impl(
            epoch,
            conservative_goodput_locked=self._conservative_goodput_locked,
        )

    def _route2_client_goodput_locked(
        self,
        session: MobilePlaybackSession,
    ) -> dict[str, float | int | bool]:
        return _route2_client_goodput_locked_impl(
            session,
            conservative_goodput_locked=self._conservative_goodput_locked,
        )

    def _route2_supply_rate_x_locked(self, epoch: PlaybackEpoch) -> tuple[float, float]:
        return _route2_supply_rate_x_locked_impl(epoch)

    def _ema_locked(self, values: list[float], *, alpha: float) -> float:
        return _ema_locked_impl(values, alpha=alpha)

    def _route2_supply_model_locked(self, epoch: PlaybackEpoch) -> dict[str, float | int | bool]:
        return _route2_supply_model_locked_impl(epoch)

    def _route2_effective_playhead_seconds_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> float:
        return _route2_effective_playhead_seconds_locked_impl(
            session,
            epoch,
            clamp_time=self._clamp_time,
        )

    def _route2_runtime_supply_metrics_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> tuple[float, float, float, float, float, bool, bool]:
        return _route2_runtime_supply_metrics_locked_impl(
            session,
            epoch,
            route2_epoch_ready_end_seconds_locked=self._route2_epoch_ready_end_seconds,
            route2_effective_playhead_seconds_locked=self._route2_effective_playhead_seconds_locked,
            route2_supply_model_locked=self._route2_supply_model_locked,
        )

    def _route2_projected_runway_seconds_locked(
        self,
        runway_seconds: float,
        supply_rate_x: float,
        *,
        projection_horizon_seconds: float,
        demand_rate_x: float = 1.0,
    ) -> float:
        return _route2_projected_runway_seconds_locked_impl(
            runway_seconds,
            supply_rate_x,
            projection_horizon_seconds=projection_horizon_seconds,
            demand_rate_x=demand_rate_x,
        )

    def _route2_required_runway_seconds_locked(
        self,
        *,
        minimum_runway_seconds: float,
        projected_runway_target_seconds: float,
        projection_horizon_seconds: float,
        supply_rate_x: float,
    ) -> float:
        return _route2_required_runway_seconds_locked_impl(
            minimum_runway_seconds=minimum_runway_seconds,
            projected_runway_target_seconds=projected_runway_target_seconds,
            projection_horizon_seconds=projection_horizon_seconds,
            supply_rate_x=supply_rate_x,
        )

    def _route2_attach_gate_state_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        *,
        minimum_runway_seconds: float,
        projected_runway_target_seconds: float,
        projection_horizon_seconds: float,
        minimum_supply_rate_x: float,
        reference_position_seconds: float | None = None,
    ) -> tuple[bool, float | None, float, float, float, bool]:
        return _route2_attach_gate_state_locked_impl(
            session,
            epoch,
            minimum_runway_seconds=minimum_runway_seconds,
            projected_runway_target_seconds=projected_runway_target_seconds,
            projection_horizon_seconds=projection_horizon_seconds,
            minimum_supply_rate_x=minimum_supply_rate_x,
            reference_position_seconds=reference_position_seconds,
            clamp_time=self._clamp_time,
            route2_epoch_ready_end_seconds_locked=self._route2_epoch_ready_end_seconds,
            route2_supply_model_locked=self._route2_supply_model_locked,
            route2_runtime_supply_metrics_locked=self._route2_runtime_supply_metrics_locked,
            route2_projected_runway_seconds_locked=self._route2_projected_runway_seconds_locked,
            route2_required_runway_seconds_locked=self._route2_required_runway_seconds_locked,
        )

    def _route2_display_prepare_eta_locked(
        self,
        epoch: PlaybackEpoch,
        raw_eta_seconds: float | None,
        *,
        now_ts: float | None = None,
        display_confident: bool = False,
    ) -> float | None:
        return _route2_display_prepare_eta_locked_impl(
            epoch,
            raw_eta_seconds,
            now_ts=now_ts,
            display_confident=display_confident,
        )

    def _route2_full_mode_requires_initial_attach_gate_locked(
        self,
        session: MobilePlaybackSession,
    ) -> bool:
        return _route2_full_mode_requires_initial_attach_gate_locked_impl(session)

    def _route2_full_safe_calibration_ratio_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        source_bin_bytes: list[int],
    ) -> float:
        return _route2_full_safe_calibration_ratio_locked_impl(
            session,
            epoch,
            source_bin_bytes,
            segment_index_for_time=self._segment_index_for_time,
            percentile_locked=self._percentile_locked,
            ema_locked=self._ema_locked,
        )

    def _route2_full_budget_metrics_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> dict[str, float | list[float] | int] | None:
        return _route2_full_budget_metrics_locked_impl(
            session,
            epoch,
            segment_index_for_time=self._segment_index_for_time,
            route2_full_safe_calibration_ratio_locked=self._route2_full_safe_calibration_ratio_locked,
        )

    def _route2_full_prepare_elapsed_seconds_locked(
        self,
        session: MobilePlaybackSession,
        *,
        now_ts: float | None = None,
    ) -> float:
        return _route2_full_prepare_elapsed_seconds_locked_impl(
            session,
            now_ts=now_ts,
        )

    def _route2_full_bootstrap_eta_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        *,
        now_ts: float | None = None,
    ) -> float | None:
        return _route2_full_bootstrap_eta_locked_impl(
            session,
            epoch,
            now_ts=now_ts,
            route2_full_prepare_elapsed_seconds_locked=self._route2_full_prepare_elapsed_seconds_locked,
            route2_epoch_ready_end_seconds=self._route2_epoch_ready_end_seconds,
            route2_supply_model_locked=self._route2_supply_model_locked,
        )

    def _route2_full_mode_gate_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> dict[str, float | str | bool | None]:
        return _route2_full_mode_gate_locked_impl(
            session,
            epoch,
            route2_full_mode_requires_initial_attach_gate_locked=self._route2_full_mode_requires_initial_attach_gate_locked,
            route2_full_prepare_elapsed_seconds_locked=self._route2_full_prepare_elapsed_seconds_locked,
            ensure_route2_full_preflight_locked=self._ensure_route2_full_preflight_locked,
            route2_full_bootstrap_eta_locked=self._route2_full_bootstrap_eta_locked,
            route2_full_budget_metrics_locked=self._route2_full_budget_metrics_locked,
            route2_server_byte_goodput_locked=self._route2_server_byte_goodput_locked,
            route2_client_goodput_locked=self._route2_client_goodput_locked,
            route2_epoch_ready_end_seconds=self._route2_epoch_ready_end_seconds,
            route2_supply_model_locked=self._route2_supply_model_locked,
        )

    def _route2_epoch_startup_attach_gate_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> dict[str, float | str | bool | None]:
        return _route2_epoch_startup_attach_gate_locked_impl(
            session,
            epoch,
            route2_full_mode_requires_initial_attach_gate_locked=self._route2_full_mode_requires_initial_attach_gate_locked,
            route2_full_mode_gate_locked=self._route2_full_mode_gate_locked,
            route2_attach_gate_state_locked=self._route2_attach_gate_state_locked,
            route2_epoch_ready_end_seconds_locked=self._route2_epoch_ready_end_seconds,
        )

    def _route2_epoch_startup_attach_ready_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> bool:
        return bool(self._route2_epoch_startup_attach_gate_locked(session, epoch)["ready"])

    def _route2_epoch_recovery_ready_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> bool:
        return _route2_epoch_recovery_ready_locked_impl(
            session,
            epoch,
            route2_attach_gate_state_locked=self._route2_attach_gate_state_locked,
        )

    def _route2_low_water_recovery_needed_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        *,
        now_ts: float | None = None,
    ) -> tuple[float, float, bool, bool, bool]:
        return _route2_low_water_recovery_needed_locked_impl(
            session,
            epoch,
            route2_runtime_supply_metrics_locked=self._route2_runtime_supply_metrics_locked,
            route2_projected_runway_seconds_locked=self._route2_projected_runway_seconds_locked,
            now_ts=now_ts,
        )

    def _route2_position_in_epoch_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        position_seconds: float,
    ) -> bool:
        return _route2_position_in_epoch_locked_impl(
            session,
            epoch,
            position_seconds,
            route2_epoch_ready_end_seconds_locked=self._route2_epoch_ready_end_seconds,
        )

    def _route2_recovery_target_locked(
        self,
        session: MobilePlaybackSession,
        active_epoch: PlaybackEpoch | None = None,
    ) -> float:
        return _route2_recovery_target_locked_impl(
            session,
            active_epoch,
            clamp_time=self._clamp_time,
        )

    def _terminate_route2_epoch_locked(
        self,
        epoch: PlaybackEpoch,
        *,
        final_state: str = "stopped",
        session: MobilePlaybackSession | None = None,
        remove_worker_record: bool = False,
    ) -> None:
        worker_id = epoch.active_worker_id
        if session is not None and worker_id:
            self._finalize_route2_worker_record_locked(
                session,
                epoch,
                state="stopping",
                remove=False,
            )
        _terminate_route2_epoch_locked_impl(
            epoch,
            workers=self._workers,
        )
        if session is not None and worker_id:
            epoch.active_worker_id = worker_id
            self._finalize_route2_worker_record_locked(
                session,
                epoch,
                state=final_state,
                remove=remove_worker_record,
            )
        elif remove_worker_record and worker_id:
            self._route2_workers.pop(worker_id, None)
        epoch.active_worker_id = None

    def _discard_route2_epoch_locked(
        self,
        session: MobilePlaybackSession,
        epoch_id: str,
    ) -> None:
        epoch = session.browser_playback.epochs.get(epoch_id)
        if epoch is not None:
            self._terminate_route2_epoch_locked(
                epoch,
                session=session,
                final_state="stopped",
                remove_worker_record=True,
            )
        _discard_route2_epoch_locked_impl(
            session,
            epoch_id,
            terminate_route2_epoch_locked=lambda _epoch: None,
        )

    def _create_route2_replacement_epoch_locked(
        self,
        session: MobilePlaybackSession,
        *,
        target_position_seconds: float,
        reason: str,
    ) -> PlaybackEpoch | None:
        browser_session = session.browser_playback
        if browser_session.replacement_epoch_id:
            self._discard_route2_epoch_locked(session, browser_session.replacement_epoch_id)
        if browser_session.replacement_epoch_count >= self.settings.route2_max_replacement_epochs_per_session:
            browser_session.state = "failed"
            session.state = "failed"
            session.last_error = (
                "Browser Playback Route 2 reached the maximum number of replacement epochs for this session."
            )
            self._log_route2_event(
                "replacement_epoch_cap_exceeded",
                session=session,
                level=logging.ERROR,
                replacement_epoch_count=browser_session.replacement_epoch_count,
                configured_cap=self.settings.route2_max_replacement_epochs_per_session,
                reason=reason,
            )
            return None
        session.epoch += 1
        session.target_position_seconds = self._clamp_time(target_position_seconds, session.duration_seconds)
        session.pending_target_seconds = session.target_position_seconds
        replacement_epoch = self._build_route2_epoch_locked(session)
        browser_session.replacement_epoch_id = replacement_epoch.epoch_id
        browser_session.epochs[replacement_epoch.epoch_id] = replacement_epoch
        browser_session.replacement_epoch_count += 1
        self._ensure_route2_epoch_workspace_locked(replacement_epoch)
        browser_session.replacement_retry_not_before_ts = 0.0
        self._log_route2_event(
            "replacement_epoch_created",
            session=session,
            epoch=replacement_epoch,
            reason=reason,
            target_position_seconds=round(session.target_position_seconds, 2),
        )
        return replacement_epoch

    def _promote_route2_replacement_epoch_locked(
        self,
        session: MobilePlaybackSession,
        replacement_epoch: PlaybackEpoch,
    ) -> None:
        browser_session = session.browser_playback
        next_attach_revision = max(1, browser_session.attach_revision + 1)
        previous_active = (
            browser_session.epochs.get(browser_session.active_epoch_id)
            if browser_session.active_epoch_id
            else None
        )
        if previous_active is not None and previous_active.epoch_id != replacement_epoch.epoch_id:
            self._mark_route2_epoch_draining_locked(
                session,
                previous_active,
                reason="replacement_promotion",
                required_client_revision=next_attach_revision,
            )
            self._terminate_route2_epoch_locked(previous_active, session=session)
            previous_active.stop_requested = False
            self._write_route2_epoch_metadata_locked(previous_active)
        browser_session.active_epoch_id = replacement_epoch.epoch_id
        browser_session.replacement_epoch_id = None
        self._issue_route2_attach_revision_locked(
            session,
            next_revision=next_attach_revision,
            reason="replacement_promotion",
            epoch=replacement_epoch,
        )
        session.target_position_seconds = replacement_epoch.attach_position_seconds
        session.pending_target_seconds = replacement_epoch.attach_position_seconds
        session.last_error = None
        self._log_route2_event(
            "replacement_epoch_promoted",
            session=session,
            epoch=replacement_epoch,
            previous_epoch_id=previous_active.epoch_id if previous_active is not None else None,
        )

    def _route2_epoch_needs_worker_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> bool:
        if epoch.state in {"failed", "draining", "ended"}:
            return False
        if epoch.transcoder_completed:
            return False
        record = self._route2_workers.get(epoch.active_worker_id) if epoch.active_worker_id else None
        if record is not None and record.state in {"queued", "running", "stopping"}:
            self._sync_route2_worker_record_locked(record, session, epoch)
            return False
        if epoch.process and epoch.process.poll() is None:
            return False
        if epoch.active_worker_id and record is None:
            epoch.active_worker_id = None
        return True

    def _ensure_route2_epoch_workers_locked(self, session: MobilePlaybackSession) -> None:
        browser_session = session.browser_playback
        waiting_epoch_exists = False
        running_epoch_exists = False
        for epoch_id in (browser_session.active_epoch_id, browser_session.replacement_epoch_id):
            if not epoch_id:
                continue
            epoch = browser_session.epochs.get(epoch_id)
            if epoch is None:
                continue
            if epoch.process and epoch.process.poll() is None:
                record = self._ensure_route2_worker_record_locked(session, epoch)
                record.state = "running"
                self._sync_route2_worker_record_locked(record, session, epoch)
                running_epoch_exists = True
                if epoch.state == "starting":
                    epoch.state = "warming"
                    self._write_route2_epoch_metadata_locked(epoch)
                continue
            record = self._route2_workers.get(epoch.active_worker_id) if epoch.active_worker_id else None
            if record is not None and record.state == "queued":
                waiting_epoch_exists = True
                self._sync_route2_worker_record_locked(record, session, epoch)
                if epoch.state not in {"failed", "draining", "ended"} and epoch.state != "starting":
                    epoch.state = "starting"
                    self._write_route2_epoch_metadata_locked(epoch)
                continue
            if not self._route2_epoch_needs_worker_locked(session, epoch):
                continue
            record = self._ensure_route2_worker_record_locked(session, epoch)
            epoch.stop_requested = False
            record.state = "queued"
            record.assigned_threads = 0
            epoch.state = "starting"
            waiting_epoch_exists = True
            self._write_route2_epoch_metadata_locked(epoch)
        if running_epoch_exists:
            session.worker_state = "running"
            session.queue_started_ts = None
        elif waiting_epoch_exists:
            session.worker_state = "queued"
            if session.queue_started_ts is None:
                session.queue_started_ts = time.time()
        else:
            session.worker_state = "idle"
            session.queue_started_ts = None

    def _ensure_route2_epoch_workspace_locked(self, epoch: PlaybackEpoch) -> None:
        _ensure_route2_epoch_workspace_locked_impl(
            epoch,
            rebuild_route2_published_frontier_locked=self._rebuild_route2_published_frontier_locked,
            write_route2_epoch_metadata_locked=self._write_route2_epoch_metadata_locked,
        )

    def _write_route2_epoch_metadata_locked(self, epoch: PlaybackEpoch) -> None:
        _write_route2_epoch_metadata_locked_impl(
            epoch,
            write_json_atomic=self._write_json_atomic,
        )

    def _write_route2_frontier_locked(self, epoch: PlaybackEpoch) -> None:
        _write_route2_frontier_locked_impl(
            epoch,
            write_json_atomic=self._write_json_atomic,
            compress_ranges=self._compress_ranges,
        )

    def _write_json_atomic(self, destination: Path, payload: dict[str, object]) -> None:
        _write_json_atomic_impl(destination, payload)

    def _rebuild_route2_published_frontier_locked(self, epoch: PlaybackEpoch) -> None:
        _rebuild_route2_published_frontier_locked_impl(
            epoch,
            contiguous_segment_frontier=self._contiguous_segment_frontier,
            record_route2_byte_sample_locked=self._record_route2_byte_sample_locked,
            write_route2_frontier_locked=self._write_route2_frontier_locked,
        )

    def _contiguous_segment_frontier(self, published_segments: set[int]) -> int | None:
        return _contiguous_segment_frontier_impl(published_segments)

    def _route2_segment_destination(self, epoch: PlaybackEpoch, segment_index: int) -> Path:
        return _route2_segment_destination_impl(epoch, segment_index)

    def _route2_publish_init_locked(self, epoch: PlaybackEpoch, staged_init_path: Path) -> Path:
        already_published = epoch.published_init_path.exists()
        started_at = time.monotonic()
        result = _route2_publish_init_locked_impl(
            epoch,
            staged_init_path,
            rebuild_route2_published_frontier_locked=self._rebuild_route2_published_frontier_locked,
            write_route2_epoch_metadata_locked=self._write_route2_epoch_metadata_locked,
        )
        if not already_published:
            latency_seconds = max(0.0, time.monotonic() - started_at)
            epoch.publish_init_latency_seconds = latency_seconds
            epoch.last_publish_latency_seconds = latency_seconds
            epoch.last_publish_kind = "init"
        return result

    def _route2_publish_segment_locked(
        self,
        epoch: PlaybackEpoch,
        segment_index: int,
        staged_segment_path: Path,
    ) -> Path:
        destination = self._route2_segment_destination(epoch, segment_index)
        already_published = destination.exists()
        started_at = time.monotonic()
        result = _route2_publish_segment_locked_impl(
            epoch,
            segment_index,
            staged_segment_path,
            route2_segment_destination=self._route2_segment_destination,
            rebuild_route2_published_frontier_locked=self._rebuild_route2_published_frontier_locked,
            write_route2_epoch_metadata_locked=self._write_route2_epoch_metadata_locked,
        )
        if not already_published:
            latency_seconds = max(0.0, time.monotonic() - started_at)
            epoch.publish_segment_count += 1
            epoch.publish_latency_total_seconds += latency_seconds
            epoch.publish_latency_max_seconds = max(
                latency_seconds,
                epoch.publish_latency_max_seconds or 0.0,
            )
            epoch.last_publish_latency_seconds = latency_seconds
            epoch.last_publish_kind = "segment"
        return result

    def _publish_route2_epoch_outputs_locked(self, epoch: PlaybackEpoch) -> None:
        _publish_route2_epoch_outputs_locked_impl(
            epoch,
            route2_publish_init_locked=self._route2_publish_init_locked,
            route2_publish_segment_locked=self._route2_publish_segment_locked,
        )

    def _build_route2_epoch_ffmpeg_command(
        self,
        *,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        thread_budget: int,
    ) -> list[str]:
        profile = MOBILE_PROFILES[session.profile]
        segment_pattern = epoch.staging_dir / "segment_%06d.m4s"
        scale_filter = (
            f"scale=w='min({profile.max_width},iw)':h='min({profile.max_height},ih)':"
            "force_original_aspect_ratio=decrease"
        )
        keyframe_interval = int(SEGMENT_DURATION_SECONDS * 24)
        source_input, source_input_kind = _resolve_worker_source_input_impl(
            self.settings,
            session,
        )
        command = [
            str(self.settings.ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-y",
            "-stats_period",
            "1",
            "-progress",
            str(epoch.epoch_dir / "ffmpeg.progress.log"),
            "-threads",
            str(max(1, int(thread_budget))),
        ]
        if source_input_kind == "url":
            command.extend(
                [
                    "-reconnect",
                    "1",
                    "-reconnect_streamed",
                    "1",
                    "-reconnect_on_network_error",
                    "1",
                    "-rw_timeout",
                    "15000000",
                ]
            )
        command.extend(
            [
                "-ss",
                f"{epoch.epoch_start_seconds:.3f}",
                "-i",
                source_input,
                "-output_ts_offset",
                "0.000",
                "-muxpreload",
                "0",
                "-muxdelay",
                "0",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-sn",
                "-dn",
                "-vf",
                scale_filter,
                "-c:v",
                "libx264",
                "-preset",
                "superfast",
                "-profile:v",
                "high",
                "-level:v",
                profile.level,
                "-pix_fmt",
                "yuv420p",
                "-crf",
                str(profile.crf),
                "-maxrate",
                profile.maxrate,
                "-bufsize",
                profile.bufsize,
                "-g",
                str(keyframe_interval),
                "-keyint_min",
                str(keyframe_interval),
                "-sc_threshold",
                "0",
                "-force_key_frames",
                f"expr:gte(t,n_forced*{SEGMENT_DURATION_SECONDS})",
                "-c:a",
                "aac",
                "-ac",
                "2",
                "-ar",
                "48000",
                "-b:a",
                "160k",
                "-max_muxing_queue_size",
                "2048",
                "-f",
                "hls",
                "-hls_time",
                f"{SEGMENT_DURATION_SECONDS:.0f}",
                "-hls_list_size",
                "0",
                "-hls_segment_type",
                "fmp4",
                "-hls_fmp4_init_filename",
                "init.mp4",
                "-hls_flags",
                "independent_segments+temp_file",
                "-start_number",
                "0",
                "-hls_segment_filename",
                str(segment_pattern),
                str(epoch.staging_manifest_path),
            ]
        )
        return command

    def _publish_route2_epoch_outputs(self, session_id: str, epoch_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.browser_playback.engine_mode != "route2":
                return
            epoch = session.browser_playback.epochs.get(epoch_id)
            if epoch is None:
                return
            self._publish_route2_epoch_outputs_locked(epoch)
            self._refresh_route2_session_authority_locked(session)

    def _run_route2_epoch_worker(self, session_id: str, epoch_id: str, worker_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.browser_playback.engine_mode != "route2":
                self._route2_workers.pop(worker_id, None)
                return
            epoch = session.browser_playback.epochs.get(epoch_id)
            if epoch is None or epoch.active_worker_id != worker_id:
                self._route2_workers.pop(worker_id, None)
                return
            record = self._route2_workers.get(worker_id)
            if record is None:
                return
            thread_budget = max(
                self.settings.route2_min_worker_threads,
                int(record.assigned_threads or self.settings.route2_min_worker_threads),
            )
            shutil.rmtree(epoch.staging_dir, ignore_errors=True)
            epoch.staging_dir.mkdir(parents=True, exist_ok=True)
            try:
                command = self._build_route2_epoch_ffmpeg_command(
                    session=session,
                    epoch=epoch,
                    thread_budget=thread_budget,
                )
            except Exception as exc:  # noqa: BLE001
                epoch.state = "failed"
                epoch.last_error = str(exc) or "Browser Playback Route 2 could not prepare the source"
                self._finalize_route2_worker_record_locked(
                    session,
                    epoch,
                    state="failed",
                    increment_failure=True,
                )
                self._log_route2_event(
                    "epoch_worker_prepare_failed",
                    session=session,
                    epoch=epoch,
                    level=logging.ERROR,
                    error=epoch.last_error,
                )
                self._write_route2_epoch_metadata_locked(epoch)
                self._refresh_route2_session_authority_locked(session)
                return
            stderr_path = epoch.epoch_dir / "ffmpeg.stderr.log"
            progress_path = epoch.epoch_dir / "ffmpeg.progress.log"
            try:
                progress_path.unlink(missing_ok=True)
            except OSError:
                pass
        logger.info(
            "Starting Browser Playback Route 2 epoch session=%s epoch=%s target=%.2f threads=%s command=%s",
            session_id,
            epoch_id,
            epoch.attach_position_seconds,
            thread_budget,
            " ".join(command),
        )
        stderr_stream = None
        try:
            stderr_stream = stderr_path.open("w", encoding="utf-8", errors="replace")
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr_stream,
                text=True,
            )
        except OSError as exc:
            if stderr_stream is not None:
                stderr_stream.close()
            with self._lock:
                session = self._sessions.get(session_id)
                if not session or session.browser_playback.engine_mode != "route2":
                    self._route2_workers.pop(worker_id, None)
                    return
                epoch = session.browser_playback.epochs.get(epoch_id)
                if epoch is None:
                    self._route2_workers.pop(worker_id, None)
                    return
                epoch.state = "failed"
                epoch.last_error = str(exc)
                self._finalize_route2_worker_record_locked(
                    session,
                    epoch,
                    state="failed",
                    increment_failure=True,
                )
                self._log_route2_event(
                    "epoch_worker_spawn_failed",
                    session=session,
                    epoch=epoch,
                    level=logging.ERROR,
                    error=epoch.last_error,
                )
                self._write_route2_epoch_metadata_locked(epoch)
                self._refresh_route2_session_authority_locked(session)
            return
        finally:
            if stderr_stream is not None:
                stderr_stream.close()

        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.browser_playback.engine_mode != "route2":
                process.terminate()
                self._route2_workers.pop(worker_id, None)
                return
            epoch = session.browser_playback.epochs.get(epoch_id)
            if epoch is None or epoch.active_worker_id != worker_id:
                process.terminate()
                self._route2_workers.pop(worker_id, None)
                return
            epoch.process = process
            if epoch.state == "starting":
                epoch.state = "warming"
            record = self._ensure_route2_worker_record_locked(session, epoch)
            record.state = "running"
            record.assigned_threads = thread_budget
            if not record.started_at:
                record.started_at = utcnow_iso()
            self._sync_route2_worker_record_locked(record, session, epoch)
            self._write_route2_epoch_metadata_locked(epoch)

        while process.poll() is None and not self._manager_stop.is_set():
            self._publish_route2_epoch_outputs(session_id, epoch_id)
            time.sleep(0.35)

        self._publish_route2_epoch_outputs(session_id, epoch_id)
        return_code = process.wait()
        stderr_tail = _read_text_tail(stderr_path)
        source_input = None
        source_input_kind = None
        try:
            input_index = command.index("-i") + 1
        except (ValueError, IndexError):
            input_index = -1
        if input_index > 0 and input_index < len(command):
            source_input = command[input_index]
            source_input_kind = "url" if source_input.startswith(("http://", "https://")) else "path"
        source_input_error = None
        if return_code != 0 and source_input_kind == "url" and source_input:
            source_input_error = _probe_worker_source_input_error_impl(source_input)
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.browser_playback.engine_mode != "route2":
                self._route2_workers.pop(worker_id, None)
                return
            epoch = session.browser_playback.epochs.get(epoch_id)
            if epoch is None:
                self._route2_workers.pop(worker_id, None)
                return
            epoch.process = None
            if epoch.stop_requested or epoch.state in {"draining", "ended"}:
                epoch.stop_requested = False
                self._finalize_route2_worker_record_locked(
                    session,
                    epoch,
                    state="stopped",
                )
                self._write_route2_epoch_metadata_locked(epoch)
                self._refresh_route2_session_authority_locked(session)
                return
            if return_code != 0:
                epoch.state = "failed"
                epoch.last_error = (
                    str(source_input_error).strip()
                    if source_input_error
                    else (
                        "Browser Playback Route 2 epoch transcoder failed "
                        f"(ffmpeg exited with code {return_code})"
                    )
                )
                self._finalize_route2_worker_record_locked(
                    session,
                    epoch,
                    state="failed",
                    increment_failure=True,
                )
                self._log_route2_event(
                    "epoch_worker_failed",
                    session=session,
                    epoch=epoch,
                    level=logging.ERROR,
                    return_code=return_code,
                    error=epoch.last_error,
                    stderr_tail=stderr_tail,
                )
                self._write_route2_epoch_metadata_locked(epoch)
                self._refresh_route2_session_authority_locked(session)
                return
            epoch.transcoder_completed = True
            epoch.last_error = None
            self._finalize_route2_worker_record_locked(
                session,
                epoch,
                state="completed",
            )
            self._log_route2_event(
                "epoch_worker_completed",
                session=session,
                epoch=epoch,
            )
            self._write_route2_epoch_metadata_locked(epoch)
            self._refresh_route2_session_authority_locked(session)

    def _route2_snapshot_locked(self, session: MobilePlaybackSession) -> dict[str, object]:
        return _route2_snapshot_locked_impl(
            session,
            route2_attach_gate_state_locked=self._route2_attach_gate_state_locked,
            route2_display_prepare_eta_locked=self._route2_display_prepare_eta_locked,
            route2_epoch_recovery_ready_locked=self._route2_epoch_recovery_ready_locked,
            route2_epoch_startup_attach_gate_locked=self._route2_epoch_startup_attach_gate_locked,
            guard_route2_full_attach_boundary_locked=self._guard_route2_full_attach_boundary_locked,
            route2_epoch_ready_end_seconds=self._route2_epoch_ready_end_seconds,
            route2_low_water_recovery_needed_locked=self._route2_low_water_recovery_needed_locked,
            route2_full_mode_gate_locked=self._route2_full_mode_gate_locked,
            route2_position_in_epoch_locked=self._route2_position_in_epoch_locked,
            segment_index_for_time=self._segment_index_for_time,
        )

    def _refresh_route2_session_authority_locked(self, session: MobilePlaybackSession) -> None:
        now_ts = time.time()
        browser_session = session.browser_playback
        self._ensure_route2_full_preflight_locked(session)
        self._cleanup_route2_draining_epochs_locked(session, now_ts=now_ts)
        active_epoch = (
            browser_session.epochs.get(browser_session.active_epoch_id)
            if browser_session.active_epoch_id
            else None
        )
        if active_epoch is None:
            browser_session.state = "failed"
            session.state = "failed"
            session.last_error = "Route 2 active epoch is missing"
            self._log_route2_event(
                "authority_missing_active_epoch",
                session=session,
                level=logging.ERROR,
                error=session.last_error,
            )
            return
        self._rebuild_route2_published_frontier_locked(active_epoch)
        self._record_route2_frontier_sample_locked(session, active_epoch, now_ts=now_ts)
        replacement_epoch = (
            browser_session.epochs.get(browser_session.replacement_epoch_id)
            if browser_session.replacement_epoch_id
            else None
        )
        if replacement_epoch is not None:
            self._rebuild_route2_published_frontier_locked(replacement_epoch)
            self._record_route2_frontier_sample_locked(session, replacement_epoch, now_ts=now_ts)
            if replacement_epoch.state == "failed":
                failed_error = replacement_epoch.last_error
                failed_epoch_id = replacement_epoch.epoch_id
                if _is_non_retryable_cloud_source_error(failed_error):
                    self._log_route2_event(
                        "replacement_epoch_non_retryable_source_failure",
                        session=session,
                        epoch=replacement_epoch,
                        level=logging.ERROR,
                        error=failed_error,
                    )
                    self._discard_route2_epoch_locked(session, failed_epoch_id)
                    browser_session.state = "failed"
                    session.state = "failed"
                    session.last_error = failed_error or "Browser Playback Route 2 replacement epoch failed"
                    self._write_route2_epoch_metadata_locked(active_epoch)
                    return
                self._log_route2_event(
                    "replacement_epoch_failed_before_promotion",
                    session=session,
                    epoch=replacement_epoch,
                    level=logging.WARNING,
                    error=failed_error,
                )
                browser_session.replacement_retry_not_before_ts = now_ts + ROUTE2_REPLACEMENT_RETRY_BACKOFF_SECONDS
                self._discard_route2_epoch_locked(session, failed_epoch_id)
                replacement_epoch = None
                if active_epoch.state == "draining" and not self._route2_epoch_startup_attach_ready_locked(session, active_epoch):
                    browser_session.state = "failed"
                    session.state = "failed"
                    session.last_error = failed_error or "Browser Playback Route 2 replacement epoch failed"
                    self._log_route2_event(
                        "recovery_failed_without_authoritative_epoch",
                        session=session,
                        epoch=active_epoch,
                        level=logging.ERROR,
                        error=session.last_error,
                    )
                    self._write_route2_epoch_metadata_locked(active_epoch)
                    return

        if active_epoch.state == "failed":
            self._mark_route2_epoch_draining_locked(
                session,
                active_epoch,
                reason="active_epoch_failure",
            )
            self._write_route2_epoch_metadata_locked(active_epoch)
            if _is_non_retryable_cloud_source_error(active_epoch.last_error):
                browser_session.state = "failed"
                session.state = "failed"
                session.last_error = active_epoch.last_error
                self._log_route2_event(
                    "active_epoch_non_retryable_source_failure",
                    session=session,
                    epoch=active_epoch,
                    level=logging.ERROR,
                    error=session.last_error,
                )
                return
            if replacement_epoch is None and now_ts >= browser_session.replacement_retry_not_before_ts:
                replacement_epoch = self._create_route2_replacement_epoch_locked(
                    session,
                    target_position_seconds=self._route2_recovery_target_locked(session, active_epoch),
                    reason="active_epoch_failure",
                )
                if replacement_epoch is None and session.state == "failed":
                    self._write_route2_epoch_metadata_locked(active_epoch)
                    return
            browser_session.state = "recovering"

        if (
            replacement_epoch is None
            and active_epoch.state == "draining"
            and now_ts >= browser_session.replacement_retry_not_before_ts
        ):
            if _is_non_retryable_cloud_source_error(session.last_error or active_epoch.last_error):
                browser_session.state = "failed"
                session.state = "failed"
                session.last_error = session.last_error or active_epoch.last_error
                return
            replacement_epoch = self._create_route2_replacement_epoch_locked(
                session,
                target_position_seconds=self._route2_recovery_target_locked(session, active_epoch),
                reason="draining_epoch_retry",
            )
            if replacement_epoch is None and session.state == "failed":
                self._write_route2_epoch_metadata_locked(active_epoch)
                return

        if replacement_epoch is not None:
            replacement_attach_ready = self._route2_epoch_startup_attach_ready_locked(session, replacement_epoch)
            replacement_attach_ready = self._guard_route2_full_attach_boundary_locked(
                session,
                replacement_epoch,
                attach_eligible=replacement_attach_ready,
                guard_path="replacement_promotion_check",
            )
            if replacement_attach_ready:
                self._promote_route2_replacement_epoch_locked(session, replacement_epoch)
                active_epoch = replacement_epoch
                replacement_epoch = None
                self._rebuild_route2_published_frontier_locked(active_epoch)
            else:
                if replacement_epoch.active_worker_id or (replacement_epoch.process and replacement_epoch.process.poll() is None):
                    replacement_epoch.state = "warming"
                elif replacement_epoch.state not in {"failed", "ended"}:
                    replacement_epoch.state = "starting"
                self._write_route2_epoch_metadata_locked(replacement_epoch)
                browser_session.state = "recovering" if active_epoch.state == "draining" else "switching"
                session.state = "retargeting" if session.pending_target_seconds is not None else "preparing"
        elif active_epoch.state == "draining":
            browser_session.state = "recovering"

        if browser_session.replacement_epoch_id is None and active_epoch.state == "draining":
            if not self._route2_epoch_startup_attach_ready_locked(session, active_epoch):
                browser_session.state = "recovering"
                session.state = "preparing"
                session.last_error = active_epoch.last_error or "Recovering Browser Playback Route 2 epoch"
                self._write_route2_epoch_metadata_locked(active_epoch)
                self._ensure_route2_epoch_workers_locked(session)
                return

        attach_ready = self._route2_epoch_startup_attach_ready_locked(session, active_epoch)
        attach_ready = self._guard_route2_full_attach_boundary_locked(
            session,
            active_epoch,
            attach_eligible=attach_ready,
            guard_path="refresh_active_attach_ready",
        )
        if attach_ready and browser_session.attach_revision == 0:
            self._issue_route2_attach_revision_locked(
                session,
                next_revision=1,
                reason="initial_attach_ready",
                epoch=active_epoch,
            )

        if session.pending_target_seconds is not None and browser_session.client_attach_revision >= browser_session.attach_revision:
            if abs(session.pending_target_seconds - active_epoch.attach_position_seconds) <= 0.5:
                session.pending_target_seconds = None

        if attach_ready:
            if browser_session.replacement_epoch_id is None:
                if active_epoch.state == "draining":
                    browser_session.state = "recovering"
                elif browser_session.client_attach_revision >= browser_session.attach_revision:
                    browser_session.state = "active"
                    active_epoch.state = "active"
                else:
                    browser_session.state = "starting"
                    active_epoch.state = "attach_ready"
                session.state = "ready" if session.pending_target_seconds is None else "retargeting"
            session.ready_start_seconds = round(active_epoch.epoch_start_seconds, 2)
            session.ready_end_seconds = round(self._route2_epoch_ready_end_seconds(session, active_epoch), 2)
            session.last_error = None
            if (
                browser_session.attach_revision > 0
                and browser_session.client_attach_revision < browser_session.attach_revision
                and browser_session.attach_revision_issued_at_ts > 0
                and now_ts - browser_session.attach_revision_issued_at_ts >= ROUTE2_ATTACH_ACK_WARN_SECONDS
                and browser_session.last_attach_warning_revision < browser_session.attach_revision
            ):
                browser_session.last_attach_warning_revision = browser_session.attach_revision
                self._log_route2_event(
                    "attach_ack_overdue",
                    session=session,
                    epoch=active_epoch,
                    level=logging.WARNING,
                    attach_revision_issued_at_ts=browser_session.attach_revision_issued_at_ts,
                )
            self._write_route2_epoch_metadata_locked(active_epoch)
            self._ensure_route2_epoch_workers_locked(session)
            return

        if active_epoch.active_worker_id or (active_epoch.process and active_epoch.process.poll() is None):
            active_epoch.state = "warming"
        elif active_epoch.state not in {"draining", "failed", "ended"}:
            active_epoch.state = "starting"
        if browser_session.replacement_epoch_id is None:
            browser_session.state = "recovering" if active_epoch.state == "draining" else "starting"
            session.state = "preparing"
        session.ready_start_seconds = 0.0
        session.ready_end_seconds = 0.0
        self._write_route2_epoch_metadata_locked(active_epoch)
        self._ensure_route2_epoch_workers_locked(session)

    def _manifest_window_locked(
        self,
        session: MobilePlaybackSession,
        cache_state: CacheState,
    ) -> tuple[int, int, int]:
        total_segments = max(1, math.ceil(session.duration_seconds / SEGMENT_DURATION_SECONDS))
        available = self._combined_available_segments_locked(session, cache_state)
        if not available:
            anchor_index = self._segment_index_for_time(session.target_position_seconds)
            return anchor_index, anchor_index, total_segments

        # Keep the exposed manifest anchored to the requested target slice.
        # Playback/session state can continue advancing independently, but the
        # playlist itself must stay VOD-like and seekable instead of sliding
        # forward like a live window.
        anchor_position = session.target_position_seconds
        anchor_index = self._segment_index_for_time(anchor_position)

        if anchor_index not in available:
            lower_candidates = [index for index in available if index <= anchor_index]
            upper_candidates = [index for index in available if index >= anchor_index]
            if upper_candidates:
                anchor_index = min(upper_candidates)
            elif lower_candidates:
                anchor_index = max(lower_candidates)
            else:
                anchor_index = min(available)

        manifest_start_segment = anchor_index
        while manifest_start_segment > 0 and (manifest_start_segment - 1) in available:
            manifest_start_segment -= 1

        manifest_end_segment = anchor_index
        max_index = total_segments - 1
        while manifest_end_segment < max_index and (manifest_end_segment + 1) in available:
            manifest_end_segment += 1

        return manifest_start_segment, manifest_end_segment, total_segments

    def _resolve_manifest_window_locked(
        self,
        session: MobilePlaybackSession,
        cache_state: CacheState,
    ) -> tuple[int, int, int]:
        total_segments = max(1, math.ceil(session.duration_seconds / SEGMENT_DURATION_SECONDS))
        if self._target_is_ready(session) and session.pending_target_seconds is None:
            if session.manifest_start_segment is None or session.manifest_end_segment is None:
                (
                    session.manifest_start_segment,
                    _initial_end_segment,
                    _computed_total_segments,
                ) = self._manifest_window_locked(session, cache_state)
                # Keep a single stable VOD manifest for the current epoch so the
                # browser can continue requesting future segments without
                # playlist swaps at each cache-fill boundary.
                session.manifest_end_segment = total_segments - 1
            return session.manifest_start_segment, session.manifest_end_segment, total_segments
        return self._manifest_window_locked(session, cache_state)

    def _maybe_advance_manifest_window_locked(self, session: MobilePlaybackSession) -> None:
        if (
            session.pending_target_seconds is not None
            or session.manifest_start_segment is None
            or session.manifest_end_segment is None
        ):
            return
        current_position = max(
            session.actual_media_element_time_seconds,
            session.committed_playhead_seconds,
            session.last_stable_position_seconds,
            session.target_position_seconds,
        )
        attached_end_seconds = min(
            session.duration_seconds,
            (session.manifest_end_segment + 1) * SEGMENT_DURATION_SECONDS,
        )
        remaining_attached_seconds = max(0.0, attached_end_seconds - current_position)
        additional_ready_seconds = max(0.0, session.ready_end_seconds - attached_end_seconds)
        if remaining_attached_seconds > MANIFEST_ADVANCE_TRIGGER_SECONDS:
            return
        if additional_ready_seconds < MANIFEST_ADVANCE_MIN_GROWTH_SECONDS:
            return
        total_segments = max(1, math.ceil(session.duration_seconds / SEGMENT_DURATION_SECONDS))
        new_end_segment = min(
            total_segments - 1,
            max(self._segment_index_for_time(max(session.ready_end_seconds - 0.001, 0.0)), session.manifest_end_segment),
        )
        if new_end_segment <= session.manifest_end_segment:
            return
        session.target_position_seconds = self._clamp_time(current_position, session.duration_seconds)
        session.manifest_end_segment = new_end_segment

    def _normalize_profile(self, value: str | None) -> str:
        candidate = (value or "mobile_1080p").strip().lower()
        if candidate not in MOBILE_PROFILES:
            raise ValueError("Unsupported mobile playback profile")
        return candidate

    def _source_fingerprint(self, item: dict[str, object], source_locator: str) -> str:
        size_token = int(item.get("file_size") or 0)
        mtime_token = int(float(item.get("file_mtime") or 0))
        raw = f"{source_locator}|{size_token}|{mtime_token}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _build_cache_key(self, source_fingerprint: str, profile: str) -> str:
        return hashlib.sha256(f"{source_fingerprint}:{profile}".encode("utf-8")).hexdigest()[:20]

    def _load_cache_state(
        self,
        *,
        cache_key: str,
        profile: str,
        duration_seconds: float,
        source_fingerprint: str,
    ) -> CacheState:
        with self._lock:
            return self._load_cache_state_locked(
                cache_key=cache_key,
                profile=profile,
                duration_seconds=duration_seconds,
                source_fingerprint=source_fingerprint,
            )

    def _load_cache_state_locked(
        self,
        *,
        cache_key: str,
        profile: str,
        duration_seconds: float,
        source_fingerprint: str,
    ) -> CacheState:
        cache_state = self._cache_states.get(cache_key)
        if cache_state is None:
            cache_dir = self._cache_root / cache_key
            cache_state = CacheState(
                cache_key=cache_key,
                cache_dir=cache_dir,
                metadata_path=cache_dir / "coverage.json",
                init_path=cache_dir / "init.mp4",
                duration_seconds=duration_seconds,
                profile=profile,
                total_segments=max(1, math.ceil(duration_seconds / SEGMENT_DURATION_SECONDS)),
                source_fingerprint=source_fingerprint,
            )
            self._cache_states[cache_key] = cache_state
        if not cache_state.loaded:
            self._hydrate_cache_state_locked(cache_state)
        return cache_state

    def _hydrate_cache_state_locked(self, cache_state: CacheState) -> None:
        cache_state.cache_dir.mkdir(parents=True, exist_ok=True)
        cached_segments: set[int] = set()
        if cache_state.metadata_path.exists():
            try:
                payload = json.loads(cache_state.metadata_path.read_text(encoding="utf-8"))
                for start, end in payload.get("cached_ranges", []):
                    cached_segments.update(range(int(start), int(end) + 1))
            except (OSError, ValueError, TypeError):
                cached_segments.clear()
        if not cached_segments:
            for child in cache_state.cache_dir.glob("segment_*.m4s"):
                token = child.stem.removeprefix("segment_")
                try:
                    cached_segments.add(int(token))
                except ValueError:
                    continue
        cache_state.cached_segments = cached_segments
        cache_state.loaded = True
        self._write_cache_metadata_locked(cache_state)

    def _build_target_cluster_job(
        self,
        session: MobilePlaybackSession,
        *,
        target_segment_index: int | None = None,
    ) -> MobileClusterJob:
        target_segment = (
            target_segment_index
            if target_segment_index is not None
            else self._segment_index_for_time(session.target_position_seconds)
        )
        preroll_segments = math.ceil(TARGET_WINDOW_PREROLL_SECONDS / SEGMENT_DURATION_SECONDS)
        forward_segments = math.ceil(TARGET_WINDOW_FORWARD_SECONDS / SEGMENT_DURATION_SECONDS)
        total_segments = max(1, math.ceil(session.duration_seconds / SEGMENT_DURATION_SECONDS))
        prepare_start_segment = max(0, target_segment - preroll_segments)
        prepare_end_segment = min(total_segments - 1, target_segment + forward_segments)
        prepare_start_seconds = prepare_start_segment * SEGMENT_DURATION_SECONDS
        prepare_end_seconds = min(
            session.duration_seconds,
            (prepare_end_segment + 1) * SEGMENT_DURATION_SECONDS,
        )
        output_dir = self._session_root / session.session_id / f"cluster-{session.epoch}-target"
        return MobileClusterJob(
            generation=session.epoch,
            phase="target",
            target_position_seconds=session.target_position_seconds,
            target_segment_index=target_segment,
            prepare_start_segment=prepare_start_segment,
            prepare_end_segment=prepare_end_segment,
            prepare_start_seconds=prepare_start_seconds,
            prepare_end_seconds=prepare_end_seconds,
            output_dir=output_dir,
            manifest_path=output_dir / "ffmpeg.m3u8",
        )

    def _build_expansion_cluster_job(
        self,
        session: MobilePlaybackSession,
        cache_state: CacheState,
    ) -> MobileClusterJob | None:
        if not self._target_is_ready(session):
            return None
        target_segment = self._segment_index_for_time(session.target_position_seconds)
        total_segments = max(1, math.ceil(session.duration_seconds / SEGMENT_DURATION_SECONDS))
        available = self._combined_available_segments_locked(session, cache_state)
        if target_segment not in available:
            return None
        right = target_segment
        max_index = total_segments - 1
        while right < max_index and (right + 1) in available:
            right += 1
        prepare_start_segment = right + 1
        if prepare_start_segment > max_index:
            return None
        anchor_position = self._watch_anchor_position(session)
        desired_ready_end = min(
            session.duration_seconds,
            anchor_position + WATCH_REFILL_TARGET_SECONDS,
        )
        desired_end_segment = min(
            max_index,
            self._segment_index_for_time(max(desired_ready_end - 0.001, 0.0)),
        )
        if desired_end_segment < prepare_start_segment:
            return None
        prepare_end_segment = desired_end_segment
        prepare_start_seconds = prepare_start_segment * SEGMENT_DURATION_SECONDS
        prepare_end_seconds = min(
            session.duration_seconds,
            (prepare_end_segment + 1) * SEGMENT_DURATION_SECONDS,
        )
        output_dir = self._session_root / session.session_id / f"cluster-{session.epoch}-expand"
        return MobileClusterJob(
            generation=session.epoch,
            phase="expand",
            target_position_seconds=session.target_position_seconds,
            target_segment_index=target_segment,
            prepare_start_segment=prepare_start_segment,
            prepare_end_segment=prepare_end_segment,
            prepare_start_seconds=prepare_start_seconds,
            prepare_end_seconds=prepare_end_seconds,
            output_dir=output_dir,
            manifest_path=output_dir / "ffmpeg.m3u8",
        )

    def _segment_index_for_time(self, position_seconds: float) -> int:
        return max(0, int(math.floor(position_seconds / SEGMENT_DURATION_SECONDS)))

    def _clamp_time(self, position_seconds: float, duration_seconds: float) -> float:
        clamped = max(0.0, float(position_seconds or 0.0))
        if duration_seconds <= 0:
            return clamped
        return min(clamped, max(duration_seconds - 1.0, 0.0))

    def _touch_session_locked(self, session: MobilePlaybackSession, *, media_access: bool) -> None:
        now = utcnow_iso()
        session.last_client_seen_at = now
        if media_access:
            session.last_media_access_at = now
        session.expires_at_ts = time.time() + (self.settings.mobile_session_ttl_minutes * 60)
        self._active_session_by_user[session.user_id] = session.session_id

    def _browser_session_state(self, session: MobilePlaybackSession) -> str:
        browser_session = session.browser_playback
        if browser_session.engine_mode != "legacy":
            return browser_session.state
        if session.state == "failed":
            return "failed"
        if session.state in {"stopped", "expired"}:
            return "stopped"
        return browser_session.state

    def _snapshot_locked(self, session: MobilePlaybackSession, cache_state: CacheState) -> dict[str, object]:
        target_window_ready = self._target_is_ready(session)
        playback_commit_ready = self._playback_commit_is_ready(session)
        ahead_runway_seconds = self._ahead_runway_seconds(session)
        starvation_risk = self._starvation_risk(session)
        stalled_recovery_needed = self._stalled_recovery_needed(session)
        browser_session = session.browser_playback
        active_epoch = (
            browser_session.epochs.get(browser_session.active_epoch_id)
            if browser_session.active_epoch_id
            else None
        )
        manifest_start_segment, manifest_end_segment, _total_segments = self._resolve_manifest_window_locked(
            session,
            cache_state,
        )
        refill_in_progress = (
            session.active_job is not None
            and session.active_job.phase == "expand"
            and session.worker_state == "running"
            and not session.active_job.superseded
        )
        manifest_start_seconds = round(manifest_start_segment * SEGMENT_DURATION_SECONDS, 2)
        manifest_end_seconds = round(
            min(session.duration_seconds, (manifest_end_segment + 1) * SEGMENT_DURATION_SECONDS),
            2,
        )
        return {
            "session_id": session.session_id,
            "media_item_id": session.media_item_id,
            "epoch": session.epoch,
            "manifest_revision": f"{session.epoch}:{manifest_start_segment}:{manifest_end_segment}",
            "state": session.state,
            "profile": session.profile,
            "duration_seconds": round(session.duration_seconds, 2),
            "target_position_seconds": round(session.target_position_seconds, 2),
            "ready_start_seconds": round(session.ready_start_seconds, 2),
            "ready_end_seconds": round(session.ready_end_seconds, 2),
            "can_play_from_target": target_window_ready,
            "manifest_url": f"/api/mobile-playback/sessions/{session.session_id}/index.m3u8",
            "status_url": f"/api/mobile-playback/sessions/{session.session_id}",
            "seek_url": f"/api/mobile-playback/sessions/{session.session_id}/seek",
            "heartbeat_url": f"/api/mobile-playback/sessions/{session.session_id}/heartbeat",
            "stop_url": f"/api/mobile-playback/sessions/{session.session_id}/stop",
            "manifest_start_segment": manifest_start_segment,
            "manifest_end_segment": manifest_end_segment,
            "manifest_start_seconds": manifest_start_seconds,
            "manifest_end_seconds": manifest_end_seconds,
            "last_error": session.last_error,
            "worker_state": session.worker_state,
            "pending_target_seconds": round(session.pending_target_seconds, 2) if session.pending_target_seconds is not None else None,
            "last_stable_position_seconds": round(session.last_stable_position_seconds, 2),
            "playing_before_seek": session.playing_before_seek,
            "target_segment_index": self._segment_index_for_time(session.target_position_seconds),
            "target_cluster_ready": target_window_ready,
            "target_window_ready": target_window_ready,
            "playback_commit_ready": playback_commit_ready,
            "cache_ranges": self._cache_ranges_to_seconds(cache_state),
            "committed_playhead_seconds": round(session.committed_playhead_seconds, 2),
            "actual_media_element_time_seconds": round(session.actual_media_element_time_seconds, 2),
            "ahead_runway_seconds": round(ahead_runway_seconds, 2),
            "supply_rate_x": 0.0,
            "supply_observation_seconds": 0.0,
            "prepare_estimate_seconds": None,
            "refill_in_progress": refill_in_progress,
            "last_refill_start_seconds": round(session.last_refill_start_seconds, 2)
            if session.last_refill_start_seconds is not None
            else None,
            "last_refill_end_seconds": round(session.last_refill_end_seconds, 2)
            if session.last_refill_end_seconds is not None
            else None,
            "starvation_risk": starvation_risk,
            "stalled_recovery_needed": stalled_recovery_needed,
            "lifecycle_state": session.lifecycle_state,
            "status_poll_seconds": (
                STATUS_POLL_PREPARE_SECONDS
                if session.state in {"queued", "preparing", "retargeting"} or starvation_risk or stalled_recovery_needed
                else 5.0
            ),
            "engine_mode": browser_session.engine_mode,
            "playback_mode": browser_session.playback_mode,
            "mode_state": "ready" if playback_commit_ready else ("preparing" if session.state in {"queued", "preparing", "retargeting"} else "estimating"),
            "mode_ready": playback_commit_ready,
            "mode_estimate_seconds": None,
            "mode_estimate_source": "none",
            "attach_revision": browser_session.attach_revision,
            "client_attach_revision": browser_session.client_attach_revision,
            "active_epoch_id": browser_session.active_epoch_id,
            "replacement_epoch_id": browser_session.replacement_epoch_id,
            "active_manifest_url": None,
            "attach_position_seconds": round(session.target_position_seconds, 2),
            "attach_ready": False,
            "browser_session_state": self._browser_session_state(session),
            "active_epoch_state": active_epoch.state if active_epoch is not None else None,
        }

    def _target_is_ready(self, session: MobilePlaybackSession) -> bool:
        return _target_is_ready_impl(session)

    def _playback_commit_is_ready(self, session: MobilePlaybackSession) -> bool:
        return _playback_commit_is_ready_impl(
            session,
            target_is_ready=self._target_is_ready,
        )

    def _watch_anchor_position(self, session: MobilePlaybackSession) -> float:
        return _watch_anchor_position_impl(session)

    def _ahead_runway_seconds(self, session: MobilePlaybackSession) -> float:
        return _ahead_runway_seconds_impl(
            session,
            watch_anchor_position=self._watch_anchor_position,
        )

    def _starvation_risk(self, session: MobilePlaybackSession) -> bool:
        return _starvation_risk_impl(
            session,
            ahead_runway_seconds=self._ahead_runway_seconds,
        )

    def _stalled_recovery_needed(self, session: MobilePlaybackSession) -> bool:
        return _stalled_recovery_needed_impl(
            session,
            ahead_runway_seconds=self._ahead_runway_seconds,
        )

    def _combined_available_segments_locked(self, session: MobilePlaybackSession, cache_state: CacheState) -> set[int]:
        available = set(cache_state.cached_segments)
        active_job = session.active_job
        if active_job and active_job.output_dir.exists():
            for child in active_job.output_dir.glob("segment_*.m4s"):
                token = child.stem.removeprefix("segment_")
                try:
                    available.add(int(token))
                except ValueError:
                    continue
        return available

    def _refresh_ready_window_locked(self, session: MobilePlaybackSession, cache_state: CacheState) -> None:
        target_index = self._segment_index_for_time(session.target_position_seconds)
        available = self._combined_available_segments_locked(session, cache_state)
        if target_index not in available:
            anchor = target_index * SEGMENT_DURATION_SECONDS
            session.ready_start_seconds = anchor
            session.ready_end_seconds = anchor
            return
        left = target_index
        while left > 0 and (left - 1) in available:
            left -= 1
        right = target_index
        max_index = cache_state.total_segments - 1
        while right < max_index and (right + 1) in available:
            right += 1
        session.ready_start_seconds = left * SEGMENT_DURATION_SECONDS
        session.ready_end_seconds = min(session.duration_seconds, (right + 1) * SEGMENT_DURATION_SECONDS)

    def _transition_session_state_locked(self, session: MobilePlaybackSession) -> None:
        if session.last_error:
            session.state = "failed"
            session.worker_state = "idle"
            session.pending_target_seconds = None
            return
        if self._playback_commit_is_ready(session):
            session.state = "ready"
            if session.worker_state != "running":
                session.worker_state = "idle"
            session.queue_started_ts = None
            session.pending_target_seconds = None
            return
        if session.worker_state == "running":
            session.state = "retargeting" if session.epoch > 1 else "preparing"
            return
        if session.queue_started_ts is not None:
            session.state = "queued"
            session.worker_state = "queued"
            return
        session.state = "retargeting" if session.epoch > 1 else "preparing"

    def _ensure_worker_for_session(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.state in {"failed", "stopped", "expired"}:
                return
            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            self._refresh_ready_window_locked(session, cache_state)
            if self._target_is_ready(session):
                active_job = session.active_job
                if active_job is None or active_job.state in {"ready", "failed", "superseded"}:
                    expansion_job = self._build_expansion_cluster_job(session, cache_state)
                    if expansion_job is not None:
                        session.last_refill_start_seconds = expansion_job.prepare_start_seconds
                        session.last_refill_end_seconds = expansion_job.prepare_end_seconds
                        session.active_job = expansion_job
                        active_job = session.active_job
                    else:
                        session.worker_state = "idle"
                        session.queue_started_ts = None
                        self._transition_session_state_locked(session)
                        return
                elif active_job.phase != "expand":
                    self._transition_session_state_locked(session)
                    return
            active_job = session.active_job
            if active_job is None or active_job.generation != session.epoch or active_job.state in {"ready", "failed", "superseded"}:
                session.active_job = self._build_target_cluster_job(session)
                active_job = session.active_job
            if active_job.active_worker_id:
                return
            if len(self._workers) >= self.settings.max_concurrent_mobile_workers:
                if session.queue_started_ts is None:
                    session.queue_started_ts = time.time()
                session.worker_state = "queued"
                self._transition_session_state_locked(session)
                return
            worker_id = uuid.uuid4().hex
            active_job.active_worker_id = worker_id
            session.worker_state = "running"
            session.queue_started_ts = None
            self._workers[worker_id] = session.session_id
            thread = threading.Thread(
                target=self._run_worker,
                args=(session.session_id, session.epoch, worker_id),
                daemon=True,
                name=f"elvern-mobile-worker-{worker_id[:8]}",
            )
            thread.start()

    def _run_worker(self, session_id: str, generation: int, worker_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                self._workers.pop(worker_id, None)
                return
            job = session.active_job
            if job is None or job.generation != generation:
                self._workers.pop(worker_id, None)
                return
            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            shutil.rmtree(job.output_dir, ignore_errors=True)
            job.output_dir.mkdir(parents=True, exist_ok=True)
            try:
                command = self._build_mobile_ffmpeg_command(session=session, job=job)
            except Exception as exc:  # noqa: BLE001
                self._workers.pop(worker_id, None)
                session = self._sessions.get(session_id)
                if session and session.epoch == generation:
                    session.last_error = str(exc) or "Experimental playback could not prepare the cloud source"
                    session.worker_state = "idle"
                    self._transition_session_state_locked(session)
                return
        logger.info(
            "Starting experimental mobile cache fill session=%s generation=%s target=%.2f command=%s",
            session_id,
            generation,
            job.target_position_seconds,
            " ".join(command),
        )
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError as exc:
            with self._lock:
                self._workers.pop(worker_id, None)
                session = self._sessions.get(session_id)
                if session and session.epoch == generation:
                    session.last_error = str(exc)
                    session.worker_state = "idle"
                    self._transition_session_state_locked(session)
            return

        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.active_job is None or session.active_job.generation != generation:
                process.terminate()
                self._workers.pop(worker_id, None)
                return
            session.active_job.process = process
            session.active_job.state = "preparing"
            session.worker_state = "running"

        while process.poll() is None and not self._manager_stop.is_set():
            self._publish_job_outputs(session_id, generation)
            time.sleep(0.35)

        self._publish_job_outputs(session_id, generation)
        return_code = process.wait()
        with self._lock:
            self._workers.pop(worker_id, None)
            session = self._sessions.get(session_id)
            if not session:
                return
            job = session.active_job
            if job is None or job.generation != generation:
                return
            job.process = None
            job.active_worker_id = None
            if job.superseded:
                job.state = "superseded"
                if session.worker_state == "running":
                    session.worker_state = "idle"
                return
            if return_code != 0:
                job.state = "failed"
                session.last_error = f"Experimental playback failed to prepare cache segments (ffmpeg exited with code {return_code})"
                session.worker_state = "idle"
                self._transition_session_state_locked(session)
                return
            job.state = "ready"
            self._refresh_ready_window_locked(session, cache_state)
            if not self._target_is_ready(session):
                session.last_error = "Experimental playback could not prepare the requested seek target"
            session.worker_state = "idle"
            self._transition_session_state_locked(session)
        self._ensure_worker_for_session(session_id)

    def _build_mobile_ffmpeg_command(
        self,
        *,
        session: MobilePlaybackSession,
        job: MobileClusterJob,
    ) -> list[str]:
        profile = MOBILE_PROFILES[session.profile]
        segment_pattern = job.output_dir / "segment_%06d.m4s"
        scale_filter = (
            f"scale=w='min({profile.max_width},iw)':h='min({profile.max_height},ih)':"
            "force_original_aspect_ratio=decrease"
        )
        cluster_duration = max(
            SEGMENT_DURATION_SECONDS,
            job.prepare_end_seconds - job.prepare_start_seconds,
        )
        keyframe_interval = int(SEGMENT_DURATION_SECONDS * 24)
        source_input, source_input_kind = _resolve_worker_source_input_impl(
            self.settings,
            session,
        )
        command = [
            str(self.settings.ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-y",
        ]
        if source_input_kind == "url":
            command.extend(
                [
                    "-reconnect",
                    "1",
                    "-reconnect_streamed",
                    "1",
                    "-reconnect_on_network_error",
                    "1",
                    "-rw_timeout",
                    "15000000",
                ]
            )
        command.extend(
            [
            "-ss",
            f"{job.prepare_start_seconds:.3f}",
            "-i",
            source_input,
            "-t",
            f"{cluster_duration:.3f}",
            # Shift each sparse cluster onto the movie's absolute timeline so
            # stable full-VOD manifests keep monotonically increasing PTS/DTS
            # after far seek instead of resetting segments to local zero.
            "-output_ts_offset",
            f"{job.prepare_start_seconds:.3f}",
            "-muxpreload",
            "0",
            "-muxdelay",
            "0",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-sn",
            "-dn",
            "-vf",
            scale_filter,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-profile:v",
            "high",
            "-level:v",
            profile.level,
            "-pix_fmt",
            "yuv420p",
            "-crf",
            str(profile.crf),
            "-maxrate",
            profile.maxrate,
            "-bufsize",
            profile.bufsize,
            "-g",
            str(keyframe_interval),
            "-keyint_min",
            str(keyframe_interval),
            "-sc_threshold",
            "0",
            "-force_key_frames",
            f"expr:gte(t,n_forced*{SEGMENT_DURATION_SECONDS})",
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-b:a",
            "160k",
            "-max_muxing_queue_size",
            "2048",
            "-f",
            "hls",
            "-hls_time",
            f"{SEGMENT_DURATION_SECONDS:.0f}",
            "-hls_list_size",
            "0",
            "-hls_playlist_type",
            "vod",
            "-hls_segment_type",
            "fmp4",
            "-hls_fmp4_init_filename",
            "init.mp4",
            "-hls_flags",
            "independent_segments+temp_file",
            "-start_number",
            str(job.prepare_start_segment),
            "-hls_segment_filename",
            str(segment_pattern),
            str(job.manifest_path),
            ]
        )
        return command

    def _publish_job_outputs(self, session_id: str, generation: int) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return
            job = session.active_job
            if job is None or job.generation != generation:
                return
            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            self._publish_outputs_locked(cache_state, job.output_dir)
            self._refresh_ready_window_locked(session, cache_state)
            self._transition_session_state_locked(session)

    def _publish_outputs_locked(self, cache_state: CacheState, output_dir: Path) -> None:
        init_candidate = output_dir / "init.mp4"
        if init_candidate.exists():
            self._publish_init_to_cache_locked(cache_state, init_candidate)
        changed = False
        for child in output_dir.glob("segment_*.m4s"):
            token = child.stem.removeprefix("segment_")
            try:
                segment_index = int(token)
            except ValueError:
                continue
            if segment_index in cache_state.cached_segments:
                continue
            self._publish_segment_to_cache_locked(cache_state, segment_index, child)
            changed = True
        if changed:
            self._write_cache_metadata_locked(cache_state)

    def _publish_init_to_cache(self, cache_state: CacheState, candidate: Path) -> None:
        with self._lock:
            self._publish_init_to_cache_locked(cache_state, candidate)

    def _publish_segment_to_cache(self, cache_state: CacheState, segment_index: int, candidate: Path) -> None:
        with self._lock:
            self._publish_segment_to_cache_locked(cache_state, segment_index, candidate)
            self._write_cache_metadata_locked(cache_state)

    def _publish_init_to_cache_locked(self, cache_state: CacheState, candidate: Path) -> None:
        cache_state.cache_dir.mkdir(parents=True, exist_ok=True)
        if cache_state.init_path.exists():
            return
        self._copy_or_link(candidate, cache_state.init_path)

    def _publish_segment_to_cache_locked(self, cache_state: CacheState, segment_index: int, candidate: Path) -> None:
        cache_state.cache_dir.mkdir(parents=True, exist_ok=True)
        destination = cache_state.cache_dir / f"segment_{segment_index:06d}.m4s"
        if destination.exists():
            cache_state.cached_segments.add(segment_index)
            return
        self._copy_or_link(candidate, destination)
        cache_state.cached_segments.add(segment_index)

    def _copy_or_link(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.hardlink_to(source)
        except OSError:
            shutil.copy2(source, destination)

    def _active_cache_jobs_locked(self, cache_key: str) -> list[dict[str, object]]:
        jobs: list[dict[str, object]] = []
        for session in self._sessions.values():
            if session.cache_key != cache_key:
                continue
            job = session.active_job
            if job is None or job.superseded:
                continue
            jobs.append(
                {
                    "session_id": session.session_id,
                    "generation": job.generation,
                    "target_segment_index": job.target_segment_index,
                    "segment_range": [job.prepare_start_segment, job.prepare_end_segment],
                    "state": job.state,
                }
            )
        return jobs

    def _write_cache_metadata_locked(self, cache_state: CacheState) -> None:
        ranges = self._compress_ranges(cache_state.cached_segments)
        payload = {
            "cache_key": cache_state.cache_key,
            "profile": cache_state.profile,
            "duration_seconds": round(cache_state.duration_seconds, 2),
            "segment_duration_seconds": SEGMENT_DURATION_SECONDS,
            "total_segments": cache_state.total_segments,
            "source_fingerprint": cache_state.source_fingerprint,
            "cached_ranges": ranges,
            "active_jobs": self._active_cache_jobs_locked(cache_state.cache_key),
            "updated_at": utcnow_iso(),
        }
        cache_state.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_state.metadata_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

    def _compress_ranges(self, values: set[int]) -> list[list[int]]:
        if not values:
            return []
        ordered = sorted(values)
        ranges: list[list[int]] = []
        start = ordered[0]
        end = ordered[0]
        for value in ordered[1:]:
            if value == end + 1:
                end = value
                continue
            ranges.append([start, end])
            start = end = value
        ranges.append([start, end])
        return ranges

    def _cache_ranges_to_seconds(self, cache_state: CacheState) -> list[list[float]]:
        ranges = self._compress_ranges(cache_state.cached_segments)
        second_ranges: list[list[float]] = []
        for start, end in ranges:
            start_seconds = round(start * SEGMENT_DURATION_SECONDS, 2)
            end_seconds = round(
                min(cache_state.duration_seconds, (end + 1) * SEGMENT_DURATION_SECONDS),
                2,
            )
            second_ranges.append([start_seconds, end_seconds])
        return second_ranges

    def _terminate_session(self, session: MobilePlaybackSession, *, remove_session_dir: bool = True) -> None:
        if session.active_job:
            self._terminate_job(session.active_job)
        if session.browser_playback.engine_mode == "route2":
            with self._lock:
                for epoch in session.browser_playback.epochs.values():
                    self._terminate_route2_epoch_locked(
                        epoch,
                        session=session,
                        final_state="stopped",
                        remove_worker_record=True,
                    )
        if remove_session_dir:
            shutil.rmtree(self._session_root / session.session_id, ignore_errors=True)
            shutil.rmtree(self._route2_root / "sessions" / session.session_id, ignore_errors=True)

    def _terminate_job_locked(
        self,
        session: MobilePlaybackSession,
        job: MobileClusterJob,
        *,
        remove_output: bool = False,
    ) -> None:
        self._terminate_job(job)
        if job.active_worker_id:
            self._workers.pop(job.active_worker_id, None)
            job.active_worker_id = None
        job.process = None
        if remove_output:
            shutil.rmtree(job.output_dir, ignore_errors=True)
        session.worker_state = "idle"

    def _terminate_job(self, job: MobileClusterJob) -> None:
        process = job.process
        if not process or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _get_owned_route2_epoch_locked(
        self,
        epoch_id: str,
        user_id: int,
    ) -> tuple[MobilePlaybackSession, PlaybackEpoch]:
        for session in self._sessions.values():
            if session.user_id != user_id:
                continue
            if session.browser_playback.engine_mode != "route2":
                continue
            epoch = session.browser_playback.epochs.get(epoch_id)
            if epoch is None:
                continue
            return session, epoch
        raise KeyError("Browser playback epoch not found")

    def _get_owned_session_locked(
        self,
        session_id: str,
        user_id: int,
        *,
        allow_missing: bool = False,
    ) -> MobilePlaybackSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            if allow_missing:
                return None
            raise KeyError("Mobile playback session not found")
        if session.user_id != user_id:
            if allow_missing:
                return None
            raise PermissionError("Mobile playback session not found")
        return session

    def _route2_has_background_activity_locked(self, session: MobilePlaybackSession) -> bool:
        return any(
            record.session_id == session.session_id and record.state in {"queued", "running"}
            for record in self._route2_workers.values()
        )

    def _reconcile_managed_session_auth_state(self) -> None:
        with self._lock:
            managed_sessions = [
                (session.user_id, session.auth_session_id)
                for session in self._sessions.values()
            ]
        if not managed_sessions:
            return
        user_ids = sorted({user_id for user_id, _auth_session_id in managed_sessions})
        auth_session_ids = sorted(
            {
                auth_session_id
                for _user_id, auth_session_id in managed_sessions
                if auth_session_id is not None
            }
        )
        disabled_user_ids: set[int] = set()
        revoked_auth_session_ids: set[int] = set()
        with get_connection(self.settings) as connection:
            if user_ids:
                placeholders = ",".join("?" for _ in user_ids)
                for row in connection.execute(
                    f"""
                    SELECT id, enabled
                    FROM users
                    WHERE id IN ({placeholders})
                    """,
                    tuple(user_ids),
                ).fetchall():
                    if not bool(row["enabled"]):
                        disabled_user_ids.add(int(row["id"]))
            if auth_session_ids:
                placeholders = ",".join("?" for _ in auth_session_ids)
                for row in connection.execute(
                    f"""
                    SELECT id, revoked_at, revoked_reason
                    FROM sessions
                    WHERE id IN ({placeholders})
                    """,
                    tuple(auth_session_ids),
                ).fetchall():
                    if row["revoked_at"] is None:
                        continue
                    if str(row["revoked_reason"] or "") == "logout":
                        continue
                    revoked_auth_session_ids.add(int(row["id"]))
        for user_id in sorted(disabled_user_ids):
            self.invalidate_user_sessions(user_id, reason="user_disabled")
        for auth_session_id in sorted(revoked_auth_session_ids):
            self.invalidate_auth_session(auth_session_id, reason="admin_revoked")

    def _dispatch_waiting_route2_workers_locked(self) -> None:
        budget = self._route2_budget_summary_locked()
        available_total_threads = int(budget["total_route2_budget_cores"]) - self._route2_running_threads_locked()
        if available_total_threads < self.settings.route2_min_worker_threads:
            return
        per_user_budget_cores = int(budget["per_user_budget_cores"])
        queued_by_user: dict[int, list[Route2WorkerRecord]] = {}
        for record in sorted(
            self._route2_workers.values(),
            key=lambda value: (self._parse_iso_ts(value.created_at), value.worker_id),
        ):
            if record.state != "queued":
                continue
            session = self._sessions.get(record.session_id)
            if session is None or session.browser_playback.engine_mode != "route2":
                continue
            epoch = session.browser_playback.epochs.get(record.epoch_id)
            if epoch is None:
                continue
            queued_by_user.setdefault(record.user_id, []).append(record)
        if not queued_by_user:
            return

        made_progress = True
        while made_progress and available_total_threads >= self.settings.route2_min_worker_threads:
            made_progress = False
            for user_id in sorted(
                queued_by_user,
                key=lambda value: self._parse_iso_ts(queued_by_user[value][0].created_at) if queued_by_user[value] else 0.0,
            ):
                queue = queued_by_user.get(user_id) or []
                if not queue:
                    continue
                running_user_threads = self._route2_running_threads_locked(user_id=user_id)
                user_remaining_threads = per_user_budget_cores - running_user_threads
                if user_remaining_threads < self.settings.route2_min_worker_threads:
                    continue
                if available_total_threads < self.settings.route2_min_worker_threads:
                    return
                record = queue.pop(0)
                session = self._sessions.get(record.session_id)
                if session is None or session.browser_playback.engine_mode != "route2":
                    continue
                epoch = session.browser_playback.epochs.get(record.epoch_id)
                if epoch is None:
                    continue
                assigned_threads = min(
                    self.settings.route2_max_worker_threads,
                    available_total_threads,
                    user_remaining_threads,
                )
                if assigned_threads < self.settings.route2_min_worker_threads:
                    continue
                spawn_dry_run = self._build_route2_adaptive_spawn_dry_run_locked(
                    record,
                    fixed_assigned_threads=assigned_threads,
                    available_total_threads=available_total_threads,
                    user_remaining_threads=user_remaining_threads,
                    allocated_cpu_cores=per_user_budget_cores,
                    route2_cpu_upbound_cores=int(budget["route2_cpu_upbound_cores"]),
                    active_route2_user_count=int(budget["active_decoding_user_count"]),
                    active_route2_workload_count=int(budget["active_route2_workload_count"]),
                )
                try:
                    thread_assignment = self._resolve_route2_real_assigned_threads_locked(
                        record,
                        fixed_assigned_threads=assigned_threads,
                        spawn_dry_run=spawn_dry_run,
                    )
                except Exception:
                    logger.debug("Route2 adaptive real assignment failed; falling back to fixed assignment", exc_info=True)
                    thread_assignment = self._fixed_route2_thread_assignment_decision(
                        fixed_assigned_threads=assigned_threads,
                        policy="adaptive_assignment_exception_fallback",
                        reason="Adaptive real thread assignment failed; using fixed Route2 assignment.",
                        blockers=["adaptive_assignment_exception"],
                        source="fixed_fallback",
                        adaptive_enabled=bool(getattr(self.settings, "route2_adaptive_thread_control_enabled", False)),
                        fallback_used=True,
                    )
                assigned_threads = thread_assignment.assigned_threads
                if assigned_threads < self.settings.route2_min_worker_threads:
                    continue
                record.state = "running"
                record.fixed_assigned_threads_at_dispatch = min(
                    self.settings.route2_max_worker_threads,
                    available_total_threads,
                    user_remaining_threads,
                )
                record.adaptive_spawn_dry_run_enabled = True
                record.adaptive_spawn_dry_run_threads = spawn_dry_run.recommended_threads
                record.adaptive_spawn_dry_run_reason = spawn_dry_run.reason
                record.adaptive_spawn_dry_run_blockers = spawn_dry_run.blockers
                record.adaptive_spawn_dry_run_policy = spawn_dry_run.policy
                record.adaptive_spawn_dry_run_source = "initial_spawn"
                record.adaptive_spawn_dry_run_sample_age_seconds = spawn_dry_run.sample_age_seconds
                record.adaptive_spawn_dry_run_sample_mature = spawn_dry_run.sample_mature
                record.adaptive_thread_control_enabled = thread_assignment.adaptive_control_enabled
                record.adaptive_thread_control_applied = thread_assignment.adaptive_control_applied
                record.adaptive_thread_assignment_policy = thread_assignment.assignment_policy
                record.adaptive_thread_assignment_reason = thread_assignment.assignment_reason
                record.adaptive_thread_assignment_blockers = thread_assignment.assignment_blockers
                record.adaptive_thread_assignment_fallback_used = thread_assignment.fallback_used
                record.assigned_threads_source = thread_assignment.assigned_threads_source
                record.assigned_threads = assigned_threads
                if not record.started_at:
                    record.started_at = utcnow_iso()
                self._sync_route2_worker_record_locked(record, session, epoch)
                thread = threading.Thread(
                    target=self._run_route2_epoch_worker,
                    args=(session.session_id, epoch.epoch_id, record.worker_id),
                    daemon=True,
                    name=f"elvern-route2-worker-{record.worker_id[:8]}",
                )
                thread.start()
                available_total_threads -= assigned_threads
                made_progress = True

    def _manager_loop(self) -> None:
        while not self._manager_stop.wait(1):
            self._reconcile_managed_session_auth_state()
            self._cleanup_sessions_and_cache()
            self._dispatch_waiting_sessions()

    def _cleanup_sessions_and_cache(self) -> None:
        now_ts = time.time()
        stale_sessions: list[MobilePlaybackSession] = []
        with self._lock:
            for session_id, session in list(self._sessions.items()):
                if session.browser_playback.engine_mode == "route2":
                    self._cleanup_route2_draining_epochs_locked(session, now_ts=now_ts)
                    if self._route2_has_background_activity_locked(session):
                        session.expires_at_ts = max(
                            session.expires_at_ts,
                            now_ts + (self.settings.mobile_session_ttl_minutes * 60),
                        )
                    if session.expires_at_ts <= now_ts:
                        session.state = "expired"
                        stale_sessions.append(session)
                        self._sessions.pop(session_id, None)
                        self._unregister_session_locked(session)
                    continue
                idle_for = now_ts - max(
                    self._parse_iso_ts(session.last_client_seen_at),
                    self._parse_iso_ts(session.last_media_access_at),
                )
                if session.expires_at_ts <= now_ts or idle_for > self.settings.mobile_session_idle_seconds:
                    session.state = "expired"
                    stale_sessions.append(session)
                    self._sessions.pop(session_id, None)
                    self._unregister_session_locked(session)
                elif session.worker_state == "queued" and session.queue_started_ts:
                    if now_ts - session.queue_started_ts > self.settings.mobile_queue_timeout_seconds:
                        session.last_error = "Maximum concurrent mobile workers reached; try again when another experimental playback job finishes."
                        session.state = "failed"
                        session.worker_state = "idle"
                        session.queue_started_ts = None
            self._cleanup_orphaned_cache_dirs_locked(now_ts)
        for session in stale_sessions:
            logger.info("Cleaning up expired mobile playback session=%s", session.session_id)
            self._terminate_session(session)

    def _dispatch_waiting_sessions(self) -> None:
        with self._lock:
            route2_session_ids = [
                session.session_id
                for session in self._sessions.values()
                if session.browser_playback.engine_mode == "route2"
            ]
            legacy_session_ids = [
                session.session_id
                for session in self._sessions.values()
                if session.browser_playback.engine_mode != "route2"
                and session.worker_state == "queued"
                and session.state in {"queued", "ready", "preparing", "retargeting"}
            ]
        for session_id in route2_session_ids:
            with self._lock:
                session = self._sessions.get(session_id)
                if session is None or session.browser_playback.engine_mode != "route2":
                    continue
                self._refresh_route2_session_authority_locked(session)
        with self._lock:
            self._dispatch_waiting_route2_workers_locked()
        for session_id in legacy_session_ids:
            self._ensure_worker_for_session(session_id)

    def _cleanup_orphaned_cache_dirs(self) -> None:
        self._cleanup_orphaned_cache_dirs_locked(time.time())

    def _cleanup_orphaned_cache_dirs_locked(self, now_ts: float) -> None:
        if self._cache_root.exists():
            cutoff = now_ts - (self.settings.mobile_cache_ttl_hours * 3600)
            for child in self._cache_root.iterdir():
                if not child.is_dir():
                    continue
                if child.stat().st_mtime >= cutoff:
                    continue
                logger.info("Removing stale mobile cache directory %s", child)
                shutil.rmtree(child, ignore_errors=True)
        route2_sessions_root = self._route2_root / "sessions"
        if not route2_sessions_root.exists():
            return
        route2_cutoff = now_ts - (self.settings.mobile_session_ttl_minutes * 60)
        protected_route2_session_ids = {
            session.session_id
            for session in self._sessions.values()
            if session.browser_playback.engine_mode == "route2"
            and session.state not in {"stopped", "expired"}
        }
        protected_route2_session_ids.update(
            record.session_id
            for record in self._route2_workers.values()
            if record.state in {"queued", "running", "stopping"}
        )
        for child in route2_sessions_root.iterdir():
            if not child.is_dir():
                continue
            if child.name in protected_route2_session_ids:
                continue
            if child.stat().st_mtime >= route2_cutoff:
                continue
            logger.info("Removing stale Route 2 session directory %s", child)
            shutil.rmtree(child, ignore_errors=True)

    def _parse_iso_ts(self, value: str) -> float:
        try:
            if not value:
                return time.time()
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return time.time()
