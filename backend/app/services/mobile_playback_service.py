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
from .library_service import (
    IMAGE_SUBTITLE_CODECS,
    TEXT_SUBTITLE_CODECS,
    _extract_playback_tracks_from_probe_summary,
    get_media_item_record,
)
from .mobile_playback_models import (
    BACKGROUND_EXPANSION_FORWARD_SECONDS,
    FRONTIER_WAIT_SECONDS,
    MANIFEST_ADVANCE_MIN_GROWTH_SECONDS,
    MANIFEST_ADVANCE_TRIGGER_SECONDS,
    MOBILE_PROFILES,
    PLAYBACK_COMMIT_RUNWAY_SECONDS,
    READY_AFTER_TARGET_SECONDS,
    ROUTE2_AUDIO_SWITCH_READY_RUNWAY_SECONDS,
    ROUTE2_ATTACH_ACK_WARN_SECONDS,
    ROUTE2_ATTACH_READY_SECONDS,
    ROUTE2_DRAIN_IDLE_GRACE_SECONDS,
    ROUTE2_DRAIN_MAX_SECONDS,
    ROUTE2_ETA_DISPLAY_MAX_VOLATILITY_RATIO,
    ROUTE2_ETA_DISPLAY_MIN_GROWTH_EVENTS,
    ROUTE2_ETA_DISPLAY_MIN_OBSERVATION_SECONDS,
    ROUTE2_ETA_DISPLAY_STICKY_OBSERVATION_SECONDS,
    ROUTE2_FULL_GOODPUT_MIN_SAMPLE_COUNT,
    ROUTE2_FULL_FAST_START_RUNWAY_SECONDS,
    ROUTE2_FULL_RESERVE_BASE_SECONDS,
    ROUTE2_FULL_RESERVE_MAX_UNCERTAINTY_SECONDS,
    ROUTE2_FULL_RESERVE_MAX_VOLATILITY_SECONDS,
    ROUTE2_FULL_VOLATILITY_HORIZON_SECONDS,
    ROUTE2_LITE_FAST_START_RUNWAY_SECONDS,
    ROUTE2_LITE_SLOW_START_RUNWAY_SECONDS,
    ROUTE2_LITE_UNDERSUPPLY_START_RUNWAY_SECONDS,
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
from .mobile_playback_buffer_contract import resolve_buffer_contract_fields
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


def _coerce_audio_stream_index(value: int | None) -> int | None:
    if value is None:
        return None
    coerced = int(value)
    if coerced < 0:
        raise ValueError("selected_audio_stream_index must be non-negative")
    return coerced


def _ffmpeg_audio_map(audio_stream_index: int | None) -> str:
    if audio_stream_index is None:
        return "0:a:0?"
    return f"0:{int(audio_stream_index)}?"


def _normalize_subtitle_codec(value: object) -> str:
    return str(value or "").strip().lower()


def _coerce_subtitle_stream_index(value: int | None) -> int:
    coerced = int(value)
    if coerced < 0:
        raise ValueError("subtitle stream index must be non-negative")
    return coerced


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
ROUTE2_ADAPTIVE_DOWNSHIFT_RETRY_BACKOFF_SECONDS = 30.0
ROUTE2_ADAPTIVE_DOWNSHIFT_MAX_RETRIES_PER_SESSION = 3
ROUTE2_ADAPTIVE_DOWNSHIFT_MODERATE_PRESSURE_MIN_SAMPLES = 3
ROUTE2_ADAPTIVE_DOWNSHIFT_MODERATE_PRESSURE_MIN_SECONDS = 6.0
ROUTE2_ADAPTIVE_RECLAIM_MEASURED_HEADROOM_MARGIN_THREADS = 0
ROUTE2_ADAPTIVE_RECLAIM_RETRY_BACKOFF_SECONDS = 30.0
ROUTE2_ADAPTIVE_RECLAIM_MAX_ATTEMPTS_PER_DONOR = 3
ROUTE2_ADAPTIVE_RECLAIM_PENDING_TTL_SECONDS = 120.0
ROUTE2_ADAPTIVE_RESUPPLY_RETRY_BACKOFF_SECONDS = 30.0
ROUTE2_ADAPTIVE_RESUPPLY_MAX_ATTEMPTS_PER_DONOR = 3
ROUTE2_ADAPTIVE_RESUPPLY_STABILIZATION_DEFAULT_SECONDS = 120
BACKGROUND_PREPARATION_PARK_SECONDS = 300.0
ROUTE2_AUDIO_SWITCH_CANDIDATE_TTL_SECONDS = 90.0
ROUTE2_RECLAIM_ACTIVE_STATES = {
    "consumer_waiting_for_reclaim",
    "donor_selected",
    "donor_downshift_starting",
    "donor_downshift_warming",
    "donor_downshift_switched",
    "measuring_capacity",
}
ROUTE2_RECLAIM_TERMINAL_STATES = {
    "capacity_available",
    "capacity_insufficient",
    "consumer_admitted_after_reclaim",
    "reclaim_aborted",
    "reclaim_failed",
}
ROUTE2_FULL_BAD_CONDITION_RESERVE_SECONDS = 900.0
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
    real_9_prepare_enabled: bool = False
    real_9_prepare_candidate: bool = False
    real_9_prepare_applied: bool = False
    real_9_prepare_blockers: list[str] = field(default_factory=list)
    effective_ladder_target: int | None = None
    lite_adaptive_prepare_candidate: bool = False
    lite_adaptive_prepare_applied: bool = False
    lite_adaptive_prepare_blockers: list[str] = field(default_factory=list)
    cloud_adaptive_prepare_enabled: bool = False
    cloud_adaptive_prepare_candidate: bool = False
    cloud_adaptive_prepare_applied: bool = False
    cloud_adaptive_prepare_blockers: list[str] = field(default_factory=list)
    strict_12_prepare_enabled: bool = False
    strict_12_prepare_candidate: bool = False
    strict_12_prepare_applied: bool = False
    strict_12_prepare_blockers: list[str] = field(default_factory=list)
    strict_12_prepare_reason: str | None = None


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


def _compact_error_text(value: object, *, max_chars: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3].rstrip()}..."


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


def _source_kind_display_label(source_kind: object) -> str:
    normalized = str(source_kind or "").strip().lower()
    if normalized == "cloud":
        return "Cloud"
    if normalized == "local":
        return "Local"
    return "Unknown source"


def _normalize_client_device_class(value: object) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"phone", "tablet", "desktop", "unknown"} else None


def _browser_device_display_from_evidence(
    *,
    client_device_class: object | None,
    user_agent: object | None,
) -> tuple[str, str, str, str]:
    explicit_class = _normalize_client_device_class(client_device_class)
    normalized_user_agent = str(user_agent or "").strip().lower()
    if "iphone" in normalized_user_agent or "ipod" in normalized_user_agent:
        return "phone", "iPhone", "user_agent", "high"
    if "ipad" in normalized_user_agent:
        return "tablet", "iPad", "user_agent", "high"
    if "android" in normalized_user_agent:
        if "mobile" in normalized_user_agent:
            return "phone", "Android phone", "user_agent", "high"
        return "tablet", "Android tablet", "user_agent", "medium"
    if "windows nt" in normalized_user_agent:
        return "desktop", "Windows PC", "user_agent", "high"
    if "macintosh" in normalized_user_agent:
        if explicit_class == "tablet":
            return "tablet", "iPad", "explicit_client_device_class", "medium"
        return "desktop", "Mac", "user_agent", "high"
    if "x11; linux" in normalized_user_agent or "linux x86_64" in normalized_user_agent:
        return "desktop", "Linux desktop", "user_agent", "high"
    if "cros" in normalized_user_agent:
        return "desktop", "Desktop", "user_agent", "medium"
    if explicit_class == "desktop":
        return "desktop", "Desktop", "explicit_client_device_class", "medium"
    if explicit_class == "tablet":
        return "tablet", "Tablet", "explicit_client_device_class", "medium"
    if explicit_class == "phone":
        return "phone", "Phone", "explicit_client_device_class", "medium"
    return "unknown", "Unknown device", "unavailable", "unknown"


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
        self._route2_pending_reclaim_request: dict[str, object] | None = None
        self._manager_stop = threading.Event()
        self._manager_thread: threading.Thread | None = None
        self._route2_resource_telemetry_thread: threading.Thread | None = None
        self._session_root = self.settings.transcode_dir / "mobile_sessions"
        self._cache_root = self.settings.transcode_dir / "mobile_cache"
        self._route2_root = self.settings.transcode_dir / "browser_playback_route2"
        self._subtitle_cache_root = self.settings.transcode_dir / "browser_playback_subtitles"

    def start(self) -> None:
        self._session_root.mkdir(parents=True, exist_ok=True)
        self._cache_root.mkdir(parents=True, exist_ok=True)
        self._route2_root.mkdir(parents=True, exist_ok=True)
        self._subtitle_cache_root.mkdir(parents=True, exist_ok=True)
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
        client_device_class: str | None = None,
        selected_audio_stream_index: int | None = None,
        client_user_agent: str | None = None,
        user_role: str | None = None,
    ) -> dict[str, object]:
        self._validate_transcoding()
        profile_key = self._normalize_profile(profile)
        selected_engine_mode = self._select_engine_mode(engine_mode)
        selected_playback_mode = self._select_playback_mode(playback_mode)
        selected_audio_stream_index = _coerce_audio_stream_index(selected_audio_stream_index)
        normalized_client_device_class = _normalize_client_device_class(client_device_class)
        normalized_client_user_agent = (client_user_agent or "").strip() or None
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
                        or candidate.browser_playback.selected_audio_stream_index != selected_audio_stream_index
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
                        client_device_class=normalized_client_device_class,
                        client_user_agent=normalized_client_user_agent,
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
                    incoming_media_item_id=int(item["id"]),
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
                selected_audio_stream_index=selected_audio_stream_index,
            ),
            client_device_class=normalized_client_device_class,
            client_user_agent=normalized_client_user_agent,
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

    def select_audio_track(
        self,
        session_id: str,
        *,
        user_id: int,
        auth_session_id: int | None = None,
        username: str | None = None,
        selected_audio_stream_index: int,
        current_position_seconds: float | None = None,
        playing_before_switch: bool | None = None,
    ) -> dict[str, object]:
        selected_audio_stream_index = _coerce_audio_stream_index(selected_audio_stream_index)
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id)
            self._adopt_session_authority_locked(
                session,
                auth_session_id=auth_session_id,
                username=username,
            )
            if session.browser_playback.engine_mode != "route2":
                raise ValueError("Audio track switching requires Browser Playback Route 2")
            if selected_audio_stream_index is None:
                raise ValueError("selected_audio_stream_index is required")
            browser_session = session.browser_playback
            if self._route2_audio_switch_in_progress_locked(browser_session):
                self._refresh_route2_session_authority_locked(session)
                return self._route2_snapshot_locked(session)
            selected_audio_track, audio_validation_error = self._resolve_trusted_audio_track_for_switch(
                int(session.media_item_id),
                selected_audio_stream_index,
            )
            if selected_audio_track is None:
                self._set_route2_audio_switch_failed_locked(
                    session,
                    reason=audio_validation_error
                    or f"Selected audio stream {selected_audio_stream_index} was not found in trusted probe metadata.",
                )
                self._refresh_route2_session_authority_locked(session)
                return self._route2_snapshot_locked(session)
            if (
                browser_session.active_audio_stream_index == selected_audio_stream_index
                and browser_session.pending_audio_stream_index is None
            ):
                browser_session.selected_audio_stream_index = selected_audio_stream_index
                browser_session.audio_switch_state = "active"
                browser_session.audio_switch_error = None
                self._refresh_route2_session_authority_locked(session)
                return self._route2_snapshot_locked(session)
            active_epoch = (
                browser_session.epochs.get(browser_session.active_epoch_id)
                if browser_session.active_epoch_id
                else None
            )
            switch_position = self._clamp_time(
                current_position_seconds
                if current_position_seconds is not None
                else self._route2_effective_playhead_seconds_locked(session, active_epoch)
                if active_epoch is not None
                else session.target_position_seconds,
                session.duration_seconds,
            )
            if playing_before_switch is not None:
                session.playing_before_seek = bool(playing_before_switch)
                session.client_is_playing = bool(playing_before_switch)
            session.last_stable_position_seconds = switch_position
            session.committed_playhead_seconds = switch_position
            session.actual_media_element_time_seconds = switch_position
            replacement = self._create_route2_replacement_epoch_locked(
                session,
                target_position_seconds=switch_position,
                reason="audio_track_switch",
                mutate_session_target=False,
            )
            if replacement is not None:
                now_ts = time.time()
                replacement.audio_stream_index = selected_audio_stream_index
                self._write_route2_epoch_metadata_locked(replacement)
                browser_session.selected_audio_stream_index = selected_audio_stream_index
                browser_session.pending_audio_stream_index = selected_audio_stream_index
                browser_session.audio_switch_state = "candidate_preparing"
                browser_session.audio_switch_error = None
                browser_session.audio_switch_candidate_epoch_id = replacement.epoch_id
                browser_session.audio_switch_candidate_stream_index = selected_audio_stream_index
                browser_session.audio_switch_candidate_state = "preparing"
                browser_session.audio_switch_candidate_error = None
                browser_session.audio_switch_candidate_created_at_ts = now_ts
                browser_session.audio_switch_candidate_ready_at_ts = 0.0
                browser_session.audio_switch_candidate_expires_at_ts = (
                    now_ts + ROUTE2_AUDIO_SWITCH_CANDIDATE_TTL_SECONDS
                )
                browser_session.audio_switch_previous_epoch_id = browser_session.active_epoch_id
                browser_session.audio_switch_previous_audio_stream_index = (
                    browser_session.active_audio_stream_index
                )
                browser_session.audio_switch_commit_requested_at_ts = 0.0
                self._log_route2_event(
                    "audio_switch_candidate_prepare_started",
                    session=session,
                    epoch=replacement,
                    active_epoch_id=browser_session.active_epoch_id,
                    previous_audio_stream_index=browser_session.active_audio_stream_index,
                    candidate_audio_stream_index=selected_audio_stream_index,
                )
            else:
                browser_session.selected_audio_stream_index = browser_session.active_audio_stream_index
                browser_session.pending_audio_stream_index = None
                browser_session.audio_switch_state = "failed"
                browser_session.audio_switch_error = (
                    session.last_error or "Could not prepare the selected audio track"
                )
            self._refresh_route2_session_authority_locked(session)
            return self._route2_snapshot_locked(session)

    def commit_audio_track_candidate(
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
            if session.browser_playback.engine_mode != "route2":
                raise ValueError("Audio track switching requires Browser Playback Route 2")
            self._refresh_route2_session_authority_locked(session)
            browser_session = session.browser_playback
            candidate = self._route2_audio_switch_candidate_epoch_locked(session)
            if candidate is None:
                return self._route2_snapshot_locked(session)
            if browser_session.audio_switch_candidate_state != "ready":
                return self._route2_snapshot_locked(session)
            browser_session.audio_switch_candidate_state = "committing"
            browser_session.audio_switch_state = "committing"
            browser_session.audio_switch_commit_requested_at_ts = time.time()
            self._log_route2_event(
                "audio_switch_commit_started",
                session=session,
                epoch=candidate,
                previous_epoch_id=browser_session.audio_switch_previous_epoch_id,
                previous_audio_stream_index=browser_session.audio_switch_previous_audio_stream_index,
                candidate_audio_stream_index=browser_session.audio_switch_candidate_stream_index,
            )
            self._promote_route2_replacement_epoch_locked(session, candidate)
            return self._route2_snapshot_locked(session)

    def cancel_audio_track_candidate(
        self,
        session_id: str,
        *,
        user_id: int,
        auth_session_id: int | None = None,
        username: str | None = None,
        reason: str = "cancelled",
    ) -> dict[str, object]:
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id)
            self._adopt_session_authority_locked(
                session,
                auth_session_id=auth_session_id,
                username=username,
            )
            if session.browser_playback.engine_mode != "route2":
                raise ValueError("Audio track switching requires Browser Playback Route 2")
            self._cancel_route2_audio_switch_candidate_locked(session, reason=reason)
            self._refresh_route2_session_authority_locked(session)
            return self._route2_snapshot_locked(session)

    def _route2_audio_switch_in_progress_locked(self, browser_session: BrowserPlaybackSession) -> bool:
        switch_state = str(browser_session.audio_switch_state or "").strip().lower()
        if switch_state == "committing":
            return browser_session.client_attach_revision < browser_session.attach_revision
        if switch_state in {"preparing", "candidate_preparing", "candidate_ready"}:
            return True
        if browser_session.pending_audio_stream_index is not None:
            return True
        if browser_session.audio_switch_candidate_epoch_id:
            return True
        if browser_session.replacement_epoch_id:
            replacement = browser_session.epochs.get(browser_session.replacement_epoch_id)
            if replacement is not None and replacement.replacement_reason == "audio_track_switch":
                return True
        if (
            switch_state == "active"
            and browser_session.active_audio_stream_index is not None
            and browser_session.attach_revision > 0
            and browser_session.client_attach_revision < browser_session.attach_revision
        ):
            return True
        return False

    def _route2_audio_switch_candidate_epoch_locked(
        self,
        session: MobilePlaybackSession,
    ) -> PlaybackEpoch | None:
        browser_session = session.browser_playback
        candidate_epoch_id = (
            browser_session.audio_switch_candidate_epoch_id
            or browser_session.replacement_epoch_id
        )
        if not candidate_epoch_id:
            return None
        candidate = browser_session.epochs.get(candidate_epoch_id)
        if candidate is None or candidate.replacement_reason != "audio_track_switch":
            return None
        if browser_session.replacement_epoch_id != candidate.epoch_id:
            return None
        return candidate

    def _clear_route2_audio_switch_candidate_locked(
        self,
        browser_session: BrowserPlaybackSession,
    ) -> None:
        browser_session.audio_switch_candidate_epoch_id = None
        browser_session.audio_switch_candidate_stream_index = None
        browser_session.audio_switch_candidate_state = "none"
        browser_session.audio_switch_candidate_error = None
        browser_session.audio_switch_candidate_created_at_ts = 0.0
        browser_session.audio_switch_candidate_ready_at_ts = 0.0
        browser_session.audio_switch_candidate_expires_at_ts = 0.0
        browser_session.audio_switch_commit_requested_at_ts = 0.0

    def _cancel_route2_audio_switch_candidate_locked(
        self,
        session: MobilePlaybackSession,
        *,
        reason: str,
        failed: bool = False,
    ) -> None:
        browser_session = session.browser_playback
        candidate_epoch_id = browser_session.audio_switch_candidate_epoch_id
        candidate = self._route2_audio_switch_candidate_epoch_locked(session)
        if candidate is not None:
            self._log_route2_event(
                "audio_switch_candidate_cancelled" if not failed else "audio_switch_candidate_failed",
                session=session,
                epoch=candidate,
                reason=reason,
                previous_epoch_id=browser_session.audio_switch_previous_epoch_id,
                previous_audio_stream_index=browser_session.audio_switch_previous_audio_stream_index,
                candidate_audio_stream_index=browser_session.audio_switch_candidate_stream_index,
            )
            self._discard_route2_epoch_locked(session, candidate.epoch_id)
        elif candidate_epoch_id:
            self._discard_route2_epoch_locked(session, candidate_epoch_id)
        browser_session.replacement_epoch_id = None
        browser_session.pending_audio_stream_index = None
        browser_session.selected_audio_stream_index = browser_session.active_audio_stream_index
        self._clear_route2_audio_switch_candidate_locked(browser_session)
        if failed:
            browser_session.audio_switch_state = "failed"
            browser_session.audio_switch_error = (reason or "Could not switch audio track").strip()
        else:
            browser_session.audio_switch_state = "active"
            browser_session.audio_switch_error = None

    def _fail_route2_audio_switch_locked(
        self,
        session: MobilePlaybackSession,
        replacement_epoch: PlaybackEpoch,
        *,
        reason: str | None = None,
    ) -> None:
        browser_session = session.browser_playback
        if replacement_epoch.replacement_reason != "audio_track_switch":
            return
        error = (reason or replacement_epoch.last_error or "Could not switch audio track").strip()
        self._set_route2_audio_switch_failed_locked(session, reason=error)
        self._log_route2_event(
            "audio_switch_failed",
            session=session,
            epoch=replacement_epoch,
            level=logging.WARNING,
            error=error,
        )

    def _set_route2_audio_switch_failed_locked(
        self,
        session: MobilePlaybackSession,
        *,
        reason: str,
    ) -> None:
        browser_session = session.browser_playback
        self._clear_route2_audio_switch_candidate_locked(browser_session)
        browser_session.pending_audio_stream_index = None
        browser_session.audio_switch_state = "failed"
        browser_session.audio_switch_error = (reason or "Could not switch audio track").strip()
        if browser_session.active_audio_stream_index is not None:
            browser_session.selected_audio_stream_index = browser_session.active_audio_stream_index

    def _resolve_trusted_audio_track_for_switch(
        self,
        media_item_id: int,
        selected_audio_stream_index: int,
    ) -> tuple[dict[str, object] | None, str | None]:
        audio_tracks, _subtitle_tracks = self._playback_tracks_for_media_item(media_item_id)
        if not audio_tracks:
            return None, "Trusted audio stream metadata is required before Browser Playback audio switching."
        for track in audio_tracks:
            try:
                stream_index = int(track.get("index"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if stream_index != int(selected_audio_stream_index):
                continue
            if str(track.get("track_source") or "") != "raw_probe_summary_json":
                return None, f"Selected audio stream {selected_audio_stream_index} is not trusted probe metadata."
            return track, None
        return None, f"Selected audio stream {selected_audio_stream_index} was not found in trusted probe metadata."

    def _playback_tracks_for_media_item(self, media_item_id: int) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        with get_connection(self.settings) as connection:
            technical_row = connection.execute(
                """
                SELECT raw_probe_summary_json
                FROM media_item_technical_metadata
                WHERE media_item_id = ? AND probe_status = 'probed'
                LIMIT 1
                """,
                (media_item_id,),
            ).fetchone()
            audio_tracks, subtitle_tracks = _extract_playback_tracks_from_probe_summary(
                technical_row["raw_probe_summary_json"] if technical_row else None
            )
            return audio_tracks, subtitle_tracks

    def _resolve_text_subtitle_track(self, media_item_id: int, stream_index: int) -> dict[str, object]:
        _audio_tracks, subtitle_tracks = self._playback_tracks_for_media_item(media_item_id)
        if not subtitle_tracks:
            raise ValueError("Trusted subtitle stream metadata is required before Browser Playback subtitle preparation")
        for track in subtitle_tracks:
            if int(track.get("index") or 0) != stream_index:
                continue
            codec = _normalize_subtitle_codec(track.get("codec"))
            if bool(track.get("image_based")) or codec in IMAGE_SUBTITLE_CODECS:
                raise ValueError("Image subtitles require future burn-in support for Browser Playback")
            if not bool(track.get("text_based")) and codec not in TEXT_SUBTITLE_CODECS:
                raise ValueError("Only text subtitle streams can be prepared for Browser Playback")
            return track
        raise ValueError("Subtitle track was not found for this media item")

    def _subtitle_vtt_cache_path(
        self,
        *,
        source_fingerprint: str,
        stream_index: int,
        codec: str | None,
    ) -> Path:
        digest = hashlib.sha256(
            f"{source_fingerprint}:{stream_index}:{_normalize_subtitle_codec(codec)}".encode("utf-8")
        ).hexdigest()[:32]
        return self._subtitle_cache_root / digest / f"stream-{stream_index}.vtt"

    def prepare_subtitle_track(
        self,
        session_id: str,
        *,
        stream_index: int,
        user_id: int,
        auth_session_id: int | None = None,
        username: str | None = None,
    ) -> dict[str, object]:
        stream_index = _coerce_subtitle_stream_index(stream_index)
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id)
            self._adopt_session_authority_locked(
                session,
                auth_session_id=auth_session_id,
                username=username,
            )
            media_item_id = int(session.media_item_id)
            source_fingerprint = str(session.source_fingerprint)
            source_locator = str(session.source_locator)
            source_input_kind = str(session.source_input_kind)
            source_kind = str(session.source_kind)
            profile = str(session.profile)
            duration_seconds = float(session.duration_seconds)
            cache_key = str(session.cache_key)
        item = get_media_item_record(self.settings, item_id=media_item_id)
        if item is None:
            raise ValueError("Media item not found for subtitle preparation")
        track = self._resolve_text_subtitle_track(media_item_id, stream_index)
        codec = str(track.get("codec") or "")
        output_path = self._subtitle_vtt_cache_path(
            source_fingerprint=source_fingerprint,
            stream_index=stream_index,
            codec=codec,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not output_path.exists() or output_path.stat().st_size <= 0:
            if not self.settings.ffmpeg_path:
                raise ValueError("ffmpeg was not found on the server")
            transient_session = MobilePlaybackSession(
                session_id=session_id,
                user_id=user_id,
                auth_session_id=auth_session_id,
                username=(username or "").strip() or None,
                media_item_id=media_item_id,
                media_title=str(item.get("title") or f"Media Item {media_item_id}"),
                profile=profile,
                source_kind=source_kind,
                duration_seconds=duration_seconds,
                cache_key=cache_key,
                source_locator=source_locator,
                source_input_kind=source_input_kind,
                source_fingerprint=source_fingerprint,
                created_at=utcnow_iso(),
                last_client_seen_at=utcnow_iso(),
                last_media_access_at=utcnow_iso(),
                target_position_seconds=0.0,
                last_stable_position_seconds=0.0,
                committed_playhead_seconds=0.0,
                actual_media_element_time_seconds=0.0,
                expires_at_ts=time.time() + 60.0,
                browser_playback=self._build_browser_playback_session(
                    engine_mode="route2",
                    playback_mode="lite",
                ),
            )
            source_input, source_input_kind = _resolve_worker_source_input_impl(
                self.settings,
                transient_session,
            )
            tmp_path = output_path.with_suffix(".vtt.tmp")
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
                    "-i",
                    source_input,
                    "-map",
                    f"0:{stream_index}",
                    "-vn",
                    "-an",
                    "-dn",
                    "-c:s",
                    "webvtt",
                    str(tmp_path),
                ]
            )
            try:
                completed = subprocess.run(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=120,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise ValueError("Subtitle conversion timed out") from exc
            if completed.returncode != 0 or not tmp_path.exists() or tmp_path.stat().st_size <= 0:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                detail = (completed.stderr or "").strip().splitlines()[-1:] or ["subtitle conversion failed"]
                raise ValueError(f"Subtitle conversion failed: {detail[0]}")
            tmp_path.replace(output_path)
        label = str(track.get("label") or track.get("title") or track.get("language") or f"Subtitle {stream_index}")
        return {
            "stream_index": stream_index,
            "label": label,
            "codec": codec or None,
            "vtt_url": f"/api/browser-playback/sessions/{session_id}/subtitles/{stream_index}.vtt",
            "prepared": True,
        }

    def get_subtitle_vtt_path(
        self,
        session_id: str,
        *,
        stream_index: int,
        user_id: int,
    ) -> Path:
        stream_index = _coerce_subtitle_stream_index(stream_index)
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id)
            media_item_id = int(session.media_item_id)
            source_fingerprint = str(session.source_fingerprint)
        track = self._resolve_text_subtitle_track(media_item_id, stream_index)
        path = self._subtitle_vtt_cache_path(
            source_fingerprint=source_fingerprint,
            stream_index=stream_index,
            codec=str(track.get("codec") or ""),
        )
        if not path.exists() or path.stat().st_size <= 0:
            raise FileNotFoundError("Prepared subtitle track not found")
        return path

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
        selected_hls_engine: str | None = None,
        buffer_tier: str | None = None,
        client_buffered_ahead_seconds: float | None = None,
        client_target_forward_buffer_seconds: float | None = None,
        client_back_buffer_seconds: float | None = None,
        client_max_buffer_size_bytes: int | None = None,
        client_ready_state: int | None = None,
        client_network_state: int | None = None,
        client_current_time_seconds: float | None = None,
        client_time_advancing: bool | None = None,
        client_playback_stall_reason: str | None = None,
        hls_js_config: dict[str, object] | None = None,
    ) -> dict[str, object]:
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id)
            self._adopt_session_authority_locked(
                session,
                auth_session_id=auth_session_id,
                username=username,
            )
            normalized_lifecycle = str(lifecycle_state or "").strip()
            suspended_heartbeat = normalized_lifecycle in {"background-suspended", "background-parked"}
            strongest_stable_position = max(
                session.committed_playhead_seconds,
                session.actual_media_element_time_seconds,
                session.last_stable_position_seconds,
                session.target_position_seconds,
            )
            if (
                suspended_heartbeat
                and strongest_stable_position > 1.0
                and committed_playhead_seconds is not None
                and float(committed_playhead_seconds or 0.0) <= 0.001
            ):
                committed_playhead_seconds = None
            if (
                suspended_heartbeat
                and strongest_stable_position > 1.0
                and actual_media_element_time_seconds is not None
                and float(actual_media_element_time_seconds or 0.0) <= 0.001
            ):
                actual_media_element_time_seconds = None
            if (
                suspended_heartbeat
                and strongest_stable_position > 1.0
                and client_current_time_seconds is not None
                and float(client_current_time_seconds or 0.0) <= 0.001
            ):
                client_current_time_seconds = None
            self._record_client_playback_telemetry_locked(
                session,
                selected_hls_engine=selected_hls_engine,
                client_buffered_ahead_seconds=client_buffered_ahead_seconds,
                client_target_forward_buffer_seconds=client_target_forward_buffer_seconds,
                client_back_buffer_seconds=client_back_buffer_seconds,
                client_max_buffer_size_bytes=client_max_buffer_size_bytes,
                client_ready_state=client_ready_state,
                client_network_state=client_network_state,
                client_current_time_seconds=client_current_time_seconds,
                client_time_advancing=client_time_advancing,
                client_playback_stall_reason=client_playback_stall_reason,
                hls_js_config=hls_js_config,
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
                    self._apply_background_lifecycle_locked(session, lifecycle_state)
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
                self._maybe_advance_native_hls_window_locked(session)
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
                self._apply_background_lifecycle_locked(session, lifecycle_state)
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

    def _apply_background_lifecycle_locked(self, session: MobilePlaybackSession, lifecycle_state: str) -> None:
        normalized = str(lifecycle_state or "").strip()
        now_ts = time.time()
        if normalized == "background-suspended":
            if session.backgrounded_at_ts <= 0:
                session.backgrounded_at_ts = now_ts
            return
        if normalized == "background-parked":
            if session.backgrounded_at_ts <= 0:
                session.backgrounded_at_ts = now_ts
            session.preparation_parked = True
            session.preparation_parked_at_ts = now_ts
            return
        if normalized in {"resuming", "attached"}:
            if session.preparation_parked:
                session.preparation_resumed_at_ts = now_ts
                for record in self._route2_workers.values():
                    if record.session_id != session.session_id:
                        continue
                    if record.state == "paused":
                        record.state = "queued"
                    elif record.state == "stopping":
                        record.state = "running" if record.started_at and not record.finished_at else "queued"
                    record.stop_requested = False
                    epoch = session.browser_playback.epochs.get(record.epoch_id)
                    if epoch is not None:
                        epoch.stop_requested = False
            session.preparation_parked = False
            session.backgrounded_at_ts = 0.0

    def _record_client_playback_telemetry_locked(
        self,
        session: MobilePlaybackSession,
        *,
        selected_hls_engine: str | None = None,
        client_buffered_ahead_seconds: float | None = None,
        client_target_forward_buffer_seconds: float | None = None,
        client_back_buffer_seconds: float | None = None,
        client_max_buffer_size_bytes: int | None = None,
        client_ready_state: int | None = None,
        client_network_state: int | None = None,
        client_current_time_seconds: float | None = None,
        client_time_advancing: bool | None = None,
        client_playback_stall_reason: str | None = None,
        hls_js_config: dict[str, object] | None = None,
    ) -> None:
        if selected_hls_engine:
            session.selected_hls_engine = str(selected_hls_engine)
        if client_buffered_ahead_seconds is not None:
            session.client_buffered_ahead_seconds = max(0.0, float(client_buffered_ahead_seconds))
        if client_target_forward_buffer_seconds is not None:
            session.client_target_forward_buffer_seconds = max(0.0, float(client_target_forward_buffer_seconds))
        if client_back_buffer_seconds is not None:
            session.client_back_buffer_seconds = max(0.0, float(client_back_buffer_seconds))
        if client_max_buffer_size_bytes is not None:
            session.client_max_buffer_size_bytes = max(0, int(client_max_buffer_size_bytes))
        if client_ready_state is not None:
            session.client_ready_state = max(0, int(client_ready_state))
        if client_network_state is not None:
            session.client_network_state = max(0, int(client_network_state))
        if client_current_time_seconds is not None:
            session.client_current_time_seconds = max(0.0, float(client_current_time_seconds))
        if client_time_advancing is not None:
            session.client_time_advancing = bool(client_time_advancing)
        if client_playback_stall_reason is not None:
            session.client_playback_stall_reason = str(client_playback_stall_reason or "")
        if hls_js_config is not None:
            session.hls_js_config = dict(hls_js_config)

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
        from .route2_native_hls_window import (
            is_native_hls_engine,
            render_route2_epoch_manifest_text,
        )

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
            total_epoch_segments = max(
                1,
                math.ceil(max(session.duration_seconds - epoch.epoch_start_seconds, 0.0) / SEGMENT_DURATION_SECONDS),
            )
            manifest_end_segment = min(epoch.contiguous_published_through_segment, total_epoch_segments - 1)
            manifest_complete = epoch.transcoder_completed and manifest_end_segment >= (total_epoch_segments - 1)
            browser_session = session.browser_playback
            window_active = (
                is_native_hls_engine(session.selected_hls_engine)
                and browser_session.last_emitted_window_initialized
            )
            window_start = (
                browser_session.last_emitted_window_start_seconds if window_active else None
            )
            window_end = (
                browser_session.last_emitted_window_end_seconds if window_active else None
            )
            return render_route2_epoch_manifest_text(
                epoch_start_seconds=epoch.epoch_start_seconds,
                attach_position_seconds=epoch.attach_position_seconds,
                manifest_end_segment=manifest_end_segment,
                duration_seconds=session.duration_seconds,
                segment_duration_seconds=SEGMENT_DURATION_SECONDS,
                manifest_complete=manifest_complete,
                window_start_seconds=window_start,
                window_end_seconds=window_end,
            )

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
        client_device_class: str | None = None,
        client_user_agent: str | None = None,
    ) -> None:
        normalized_username = (username or "").strip() or None
        if auth_session_id is not None:
            session.auth_session_id = auth_session_id
        if normalized_username:
            session.username = normalized_username
        normalized_client_device_class = _normalize_client_device_class(client_device_class)
        if normalized_client_device_class is not None:
            session.client_device_class = normalized_client_device_class
        normalized_client_user_agent = (client_user_agent or "").strip() or None
        if normalized_client_user_agent:
            session.client_user_agent = normalized_client_user_agent

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
        reclaim_detail: dict[str, object] | None = None,
    ) -> dict[str, object]:
        detail = {
            "code": SERVER_MAX_CAPACITY_CODE,
            "reason_code": reason_code,
            "message": message or "Server is busy. Please try again later.",
            "active_route2_user_count_after_admission": active_user_count_after_admission,
            "available_reserved_threads": available_reserved_threads,
            "required_min_threads": admission_min_threads,
            "protected_min_threads_per_active_user": self._route2_protected_min_threads_per_active_user(),
        }
        if reclaim_detail:
            detail.update(reclaim_detail)
        return detail

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
        record.adaptive_downshift_pressure_abort_reason = session.browser_playback.adaptive_downshift_pressure_abort_reason
        record.adaptive_downshift_pressure_snapshot = dict(session.browser_playback.adaptive_downshift_pressure_snapshot)
        record.adaptive_downshift_retry_count = int(session.browser_playback.adaptive_downshift_retry_count or 0)
        record.adaptive_downshift_retry_not_before_seconds = self._route2_downshift_retry_seconds_remaining(
            session.browser_playback,
        )
        record.adaptive_downshift_retry_blocker = session.browser_playback.adaptive_downshift_retry_blocker
        record.adaptive_downshift_last_abort_reason = session.browser_playback.adaptive_downshift_last_abort_reason
        record.adaptive_downshift_replacement_epoch_cap_remaining = self._route2_downshift_retry_cap_remaining(
            session.browser_playback,
        )
        browser_session = session.browser_playback
        record.adaptive_downshift_enabled = bool(getattr(self.settings, "route2_adaptive_downshift_enabled", False))
        record.autonomous_maintenance_downshift_enabled = bool(
            getattr(self.settings, "route2_adaptive_maintenance_downshift_enabled", False)
        )
        record.adaptive_downshift_mode = "none"
        record.reclaim_donor_downshift_active = False
        record.donor_reserved_for_reclaim = False
        record.maintenance_downshift_suppressed_by_reclaim = False
        record.adaptive_reclaim_enabled = bool(getattr(self.settings, "route2_adaptive_reclaim_enabled", False))
        record.adaptive_reclaim_dry_run_enabled = bool(
            getattr(self.settings, "route2_adaptive_reclaim_dry_run_enabled", True)
        )
        record.adaptive_reclaim_request_id = browser_session.adaptive_reclaim_request_id
        record.adaptive_reclaim_consumer_worker_id = browser_session.adaptive_reclaim_consumer_worker_id
        record.adaptive_reclaim_consumer_session_id = browser_session.adaptive_reclaim_consumer_session_id
        record.adaptive_reclaim_consumer_user_id = browser_session.adaptive_reclaim_consumer_user_id
        record.adaptive_reclaim_consumer_media_item_id = browser_session.adaptive_reclaim_consumer_media_item_id
        record.adaptive_reclaim_consumer_reason = browser_session.adaptive_reclaim_consumer_reason
        record.adaptive_reclaim_donor_worker_id = browser_session.adaptive_reclaim_donor_worker_id
        record.adaptive_reclaim_donor_session_id = browser_session.adaptive_reclaim_donor_session_id
        record.adaptive_reclaim_downshift_replacement_epoch_id = (
            browser_session.adaptive_reclaim_downshift_replacement_epoch_id
        )
        record.adaptive_reclaim_downshift_replacement_worker_id = (
            browser_session.adaptive_reclaim_downshift_replacement_worker_id
        )
        record.adaptive_reclaim_started_at = browser_session.adaptive_reclaim_started_at
        record.adaptive_reclaim_switched_at = browser_session.adaptive_reclaim_switched_at
        record.adaptive_reclaim_measured_at = browser_session.adaptive_reclaim_measured_at
        record.adaptive_reclaim_completed_at = browser_session.adaptive_reclaim_completed_at
        record.adaptive_reclaim_failed_reason = browser_session.adaptive_reclaim_failed_reason
        record.adaptive_reclaim_released_threads_expected = (
            browser_session.adaptive_reclaim_released_threads_expected
        )
        record.adaptive_reclaim_released_threads_measured = (
            browser_session.adaptive_reclaim_released_threads_measured
        )
        record.adaptive_reclaim_released_cpu_cores_measured = (
            browser_session.adaptive_reclaim_released_cpu_cores_measured
        )
        record.adaptive_reclaim_cpu_headroom_before = browser_session.adaptive_reclaim_cpu_headroom_before
        record.adaptive_reclaim_cpu_headroom_after = browser_session.adaptive_reclaim_cpu_headroom_after
        record.adaptive_reclaim_route2_cpu_cores_used_before = (
            browser_session.adaptive_reclaim_route2_cpu_cores_used_before
        )
        record.adaptive_reclaim_route2_cpu_cores_used_after = (
            browser_session.adaptive_reclaim_route2_cpu_cores_used_after
        )
        record.adaptive_reclaim_user_cpu_cores_used_before = (
            browser_session.adaptive_reclaim_user_cpu_cores_used_before
        )
        record.adaptive_reclaim_user_cpu_cores_used_after = (
            browser_session.adaptive_reclaim_user_cpu_cores_used_after
        )
        record.adaptive_reclaim_host_cpu_used_cores_before = (
            browser_session.adaptive_reclaim_host_cpu_used_cores_before
        )
        record.adaptive_reclaim_host_cpu_used_cores_after = (
            browser_session.adaptive_reclaim_host_cpu_used_cores_after
        )
        record.adaptive_reclaim_host_cpu_spare_cores_before = (
            browser_session.adaptive_reclaim_host_cpu_spare_cores_before
        )
        record.adaptive_reclaim_host_cpu_spare_cores_after = (
            browser_session.adaptive_reclaim_host_cpu_spare_cores_after
        )
        record.adaptive_reclaim_route2_headroom_before = browser_session.adaptive_reclaim_route2_headroom_before
        record.adaptive_reclaim_route2_headroom_after = browser_session.adaptive_reclaim_route2_headroom_after
        record.adaptive_reclaim_memory_pressure_before = browser_session.adaptive_reclaim_memory_pressure_before
        record.adaptive_reclaim_memory_pressure_after = browser_session.adaptive_reclaim_memory_pressure_after
        record.adaptive_reclaim_external_pressure_before = browser_session.adaptive_reclaim_external_pressure_before
        record.adaptive_reclaim_external_pressure_after = browser_session.adaptive_reclaim_external_pressure_after
        record.adaptive_reclaim_capacity_sufficient_for_consumer = (
            browser_session.adaptive_reclaim_capacity_sufficient_for_consumer
        )
        record.adaptive_reclaim_retry_count = int(browser_session.adaptive_reclaim_retry_count or 0)
        record.adaptive_reclaim_retry_not_before_seconds = self._route2_reclaim_retry_seconds_remaining(
            browser_session,
        )
        record.adaptive_reclaim_retry_blocker = browser_session.adaptive_reclaim_retry_blocker
        record.adaptive_reclaim_state = browser_session.adaptive_reclaim_state
        record.adaptive_reclaim_blockers = list(browser_session.adaptive_reclaim_blockers)
        record.adaptive_reclaim_abort_reason = browser_session.adaptive_reclaim_abort_reason
        record.adaptive_resupply_enabled = bool(getattr(self.settings, "route2_adaptive_resupply_enabled", False))
        record.adaptive_resupply_dry_run_enabled = bool(
            getattr(self.settings, "route2_adaptive_resupply_dry_run_enabled", True)
        )
        record.adaptive_resupply_needed = bool(browser_session.adaptive_resupply_needed)
        record.adaptive_resupply_reason = browser_session.adaptive_resupply_reason
        record.adaptive_resupply_target_threads = browser_session.adaptive_resupply_target_threads
        record.adaptive_resupply_state = browser_session.adaptive_resupply_state
        record.adaptive_resupply_request_id = browser_session.adaptive_resupply_request_id
        record.adaptive_resupply_original_reclaim_request_id = (
            browser_session.adaptive_resupply_original_reclaim_request_id
        )
        record.adaptive_resupply_donor_worker_id = browser_session.adaptive_resupply_donor_worker_id
        record.adaptive_resupply_replacement_epoch_id = browser_session.adaptive_resupply_replacement_epoch_id
        record.adaptive_resupply_replacement_worker_id = browser_session.adaptive_resupply_replacement_worker_id
        record.adaptive_resupply_started_at = browser_session.adaptive_resupply_started_at
        record.adaptive_resupply_switched_at = browser_session.adaptive_resupply_switched_at
        record.adaptive_resupply_measured_at = browser_session.adaptive_resupply_measured_at
        record.adaptive_resupply_blockers = list(browser_session.adaptive_resupply_blockers)
        record.adaptive_resupply_abort_reason = browser_session.adaptive_resupply_abort_reason
        stabilization_payload = self._route2_resupply_stabilization_payload_locked(browser_session)
        record.adaptive_resupply_stabilization_active = bool(
            stabilization_payload["adaptive_resupply_stabilization_active"]
        )
        record.adaptive_resupply_stabilization_until = stabilization_payload[  # type: ignore[assignment]
            "adaptive_resupply_stabilization_until"
        ]
        record.adaptive_resupply_stabilization_seconds_remaining = stabilization_payload[  # type: ignore[assignment]
            "adaptive_resupply_stabilization_seconds_remaining"
        ]
        record.adaptive_resupply_stabilization_reason = stabilization_payload[  # type: ignore[assignment]
            "adaptive_resupply_stabilization_reason"
        ]
        record.last_resupply_completed_at = stabilization_payload["last_resupply_completed_at"]  # type: ignore[assignment]
        record.last_resupply_target_threads = stabilization_payload["last_resupply_target_threads"]  # type: ignore[assignment]
        record.resupplied_donor_protection_active = bool(
            stabilization_payload["resupplied_donor_protection_active"]
        )
        record.priority_reexpand_pending = bool(browser_session.priority_reexpand_pending)
        record.priority_reexpand_reason = browser_session.priority_reexpand_reason
        record.donor_protection_active = bool(browser_session.donor_protection_active)
        record.donor_health_after_resupply = dict(browser_session.donor_health_after_resupply)
        record.admission_blocked_by_resupply = bool(browser_session.admission_blocked_by_resupply)
        if epoch.replacement_reason == "adaptive_resupply_boost":
            record.adaptive_downshift_mode = "resupply_boost"
            record.adaptive_resupply_replacement_epoch_id = epoch.epoch_id
            record.adaptive_resupply_replacement_worker_id = epoch.active_worker_id
            record.adaptive_resupply_target_threads = epoch.adaptive_resupply_target_threads
            record.adaptive_resupply_request_id = epoch.adaptive_resupply_request_id
            record.adaptive_resupply_original_reclaim_request_id = (
                epoch.adaptive_resupply_original_reclaim_request_id
            )
            record.adaptive_resupply_started_at = epoch.adaptive_resupply_started_at
            record.adaptive_resupply_switched_at = epoch.adaptive_resupply_switched_at
            record.adaptive_resupply_abort_reason = epoch.adaptive_resupply_abort_reason
            if epoch.adaptive_resupply_abort_reason:
                record.adaptive_resupply_state = "aborted"
            elif epoch.adaptive_resupply_switched_at:
                record.adaptive_resupply_state = "switched"
            elif epoch.active_worker_id or (epoch.process and epoch.process.poll() is None):
                record.adaptive_resupply_state = "boost_replacement_warming"
            else:
                record.adaptive_resupply_state = "boost_replacement_starting"
        if epoch.replacement_reason == "maintenance_downshift":
            record.adaptive_downshift_enabled = bool(getattr(self.settings, "route2_adaptive_downshift_enabled", False))
            record.adaptive_downshift_mode = (
                "reclaim_donor" if epoch.adaptive_reclaim_request_id else "autonomous_maintenance"
            )
            record.reclaim_donor_downshift_active = bool(epoch.adaptive_reclaim_request_id)
            record.adaptive_downshift_replacement_epoch_id = epoch.epoch_id
            record.adaptive_downshift_replacement_worker_id = epoch.active_worker_id
            record.adaptive_downshift_target_threads = epoch.maintenance_downshift_target_threads
            record.maintenance_tier_target = epoch.maintenance_downshift_target_threads
            record.adaptive_downshift_transition_started_at = epoch.adaptive_downshift_transition_started_at
            record.adaptive_downshift_switched_at = epoch.adaptive_downshift_switched_at
            record.adaptive_downshift_aborted_reason = epoch.adaptive_downshift_aborted_reason
            record.adaptive_downshift_policy = (
                "reclaim_donor" if epoch.adaptive_reclaim_request_id else "maintenance"
            )
            if epoch.adaptive_downshift_aborted_reason:
                record.adaptive_downshift_state = "aborted"
            elif epoch.adaptive_downshift_switched_at:
                record.adaptive_downshift_state = "switched"
            elif epoch.active_worker_id or (epoch.process and epoch.process.poll() is None):
                record.adaptive_downshift_state = "replacement_warming"
            else:
                record.adaptive_downshift_state = "replacement_starting"
        if (
            browser_session.adaptive_reclaim_donor_worker_id
            and browser_session.adaptive_reclaim_donor_worker_id == record.worker_id
        ):
            record.adaptive_reclaim_candidate = browser_session.adaptive_reclaim_state in ROUTE2_RECLAIM_ACTIVE_STATES
            record.adaptive_reclaim_candidate_reason = "Selected as transactional reclaim donor."
            record.adaptive_reclaim_target_threads = (
                epoch.maintenance_downshift_target_threads or record.maintenance_tier_target
            )
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
            "adaptive_thread_control_enabled": self.settings.route2_adaptive_thread_control_enabled,
            "adaptive_thread_control_local_only": self.settings.route2_adaptive_thread_control_local_only,
            "adaptive_thread_control_cloud_enabled": self.settings.route2_adaptive_thread_control_cloud_enabled,
            "adaptive_thread_control_strict_12_enabled": self.settings.route2_adaptive_thread_control_strict_12_enabled,
            "adaptive_thread_control_real_9_prepare_enabled": (
                self.settings.route2_adaptive_thread_control_real_9_prepare_enabled
            ),
            "adaptive_downshift_enabled": self.settings.route2_adaptive_downshift_enabled,
            "adaptive_downshift_dry_run_enabled": self.settings.route2_adaptive_downshift_dry_run_enabled,
            "autonomous_maintenance_downshift_enabled": (
                self.settings.route2_adaptive_maintenance_downshift_enabled
            ),
            "autonomous_maintenance_downshift_dry_run_enabled": (
                self.settings.route2_adaptive_maintenance_downshift_dry_run_enabled
            ),
            "adaptive_reclaim_enabled": self.settings.route2_adaptive_reclaim_enabled,
            "adaptive_reclaim_dry_run_enabled": self.settings.route2_adaptive_reclaim_dry_run_enabled,
            "adaptive_resupply_enabled": self.settings.route2_adaptive_resupply_enabled,
            "adaptive_resupply_dry_run_enabled": self.settings.route2_adaptive_resupply_dry_run_enabled,
            "adaptive_resupply_stabilization_seconds": self._route2_resupply_stabilization_seconds(),
            "full_bad_condition_gate_enabled": bool(
                getattr(self.settings, "route2_full_bad_condition_30min_gate_enabled", False)
            ),
            "full_bad_condition_gate_dry_run_enabled": bool(
                getattr(self.settings, "route2_full_bad_condition_30min_gate_dry_run_enabled", True)
            ),
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

    def _route2_resupply_stabilization_seconds(self) -> int:
        return max(
            0,
            int(
                getattr(
                    self.settings,
                    "route2_adaptive_resupply_stabilization_seconds",
                    ROUTE2_ADAPTIVE_RESUPPLY_STABILIZATION_DEFAULT_SECONDS,
                )
                or 0
            ),
        )

    def _route2_resupply_stabilization_payload_locked(
        self,
        browser_session: BrowserPlaybackSession,
        *,
        now_ts: float | None = None,
    ) -> dict[str, object]:
        reference_ts = time.time() if now_ts is None else now_ts
        until_ts = float(browser_session.adaptive_resupply_stabilization_until_ts or 0.0)
        remaining = max(0.0, until_ts - reference_ts)
        active = remaining > 0.0
        return {
            "adaptive_resupply_stabilization_active": active,
            "adaptive_resupply_stabilization_until": browser_session.adaptive_resupply_stabilization_until,
            "adaptive_resupply_stabilization_seconds_remaining": round(remaining, 3) if active else None,
            "adaptive_resupply_stabilization_reason": (
                browser_session.adaptive_resupply_stabilization_reason if active else None
            ),
            "last_resupply_completed_at": browser_session.last_resupply_completed_at,
            "last_resupply_target_threads": browser_session.last_resupply_target_threads,
            "resupplied_donor_protection_active": active,
        }

    def _activate_route2_resupply_stabilization_locked(
        self,
        browser_session: BrowserPlaybackSession,
        *,
        target_threads: int | None,
        now_ts: float | None = None,
    ) -> None:
        reference_ts = time.time() if now_ts is None else now_ts
        completed_at = utcnow_iso()
        browser_session.last_resupply_completed_at = completed_at
        browser_session.last_resupply_target_threads = target_threads
        seconds = self._route2_resupply_stabilization_seconds()
        if seconds <= 0:
            browser_session.adaptive_resupply_stabilization_until_ts = 0.0
            browser_session.adaptive_resupply_stabilization_until = None
            browser_session.adaptive_resupply_stabilization_reason = None
            return
        until_ts = reference_ts + float(seconds)
        browser_session.adaptive_resupply_stabilization_until_ts = until_ts
        browser_session.adaptive_resupply_stabilization_until = datetime.fromtimestamp(until_ts).astimezone().isoformat()
        browser_session.adaptive_resupply_stabilization_reason = "post_resupply_donor_stabilization"

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
        requested_target_seconds = (
            session.pending_target_seconds
            if session.pending_target_seconds is not None
            else (epoch.attach_position_seconds or session.target_position_seconds or 0.0)
        )
        reserve_start_seconds = min(
            max(0.0, float(requested_target_seconds)),
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
        (
            runtime_runway_seconds,
            _runtime_supply_rate_x,
            _refill_in_progress,
            starvation_risk,
            stalled_recovery_needed,
        ) = self._route2_low_water_recovery_needed_locked(session, epoch)
        is_full_route2 = (
            session.browser_playback.engine_mode == "route2"
            and session.browser_playback.playback_mode == "full"
        )
        active_record = (
            self._route2_workers.get(epoch.active_worker_id)
            if epoch.active_worker_id is not None
            else None
        )
        provider_blocker = self._route2_provider_prepare_blocker(
            active_record.non_retryable_error
            if active_record is not None
            else (epoch.last_error or session.last_error)
        )
        cloud_source_feed_blocker: str | None = None
        if is_full_route2 and session.source_kind == "cloud" and active_record is not None:
            source_feed = self._route2_source_feed_rate_locked(session, active_record)
            if not source_feed.available:
                cloud_source_feed_blocker = "cloud_source_feed_unavailable"
            elif not source_feed.mature:
                cloud_source_feed_blocker = "cloud_source_feed_immature"
            elif source_feed.rate_x is None:
                cloud_source_feed_blocker = "cloud_source_feed_unavailable"
            elif float(source_feed.rate_x) < ROUTE2_ACTIVE_SUPPLY_HEALTHY_RATE_X:
                cloud_source_feed_blocker = "cloud_source_feed_low"
        coverage_starts_at_reserve = (
            epoch.init_published
            and epoch.contiguous_published_through_segment is not None
            and epoch.epoch_start_seconds <= reserve_start_seconds + 0.001
        )
        progress_source = "published_frontier" if coverage_starts_at_reserve else "manifest_contiguous_range_unavailable"
        actual_contiguous_seconds_after_target = (
            max(0.0, actual_ready_end_seconds - reserve_start_seconds)
            if coverage_starts_at_reserve
            else 0.0
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
        bad_condition_reasons: list[str] = []
        bad_condition_confidence = "none"
        bad_condition_mature = False
        if not is_full_route2:
            bad_condition_reason = "not_full_playback"
            bad_condition_reasons.append(bad_condition_reason)
        elif provider_blocker is not None:
            bad_condition_required = True
            bad_condition_reason = provider_blocker
            bad_condition_reasons.append(provider_blocker)
            bad_condition_confidence = "high"
            bad_condition_mature = True
        elif cloud_source_feed_blocker is not None:
            bad_condition_required = True
            bad_condition_reason = cloud_source_feed_blocker
            bad_condition_reasons.append(cloud_source_feed_blocker)
            bad_condition_confidence = "medium"
            bad_condition_mature = True
        elif not metrics_mature:
            bad_condition_reason = "metrics_immature"
            bad_condition_reasons.append(bad_condition_reason)
            bad_condition_confidence = "low"
        elif stalled_recovery_needed:
            bad_condition_required = True
            bad_condition_reason = "stalled_recovery_needed"
            bad_condition_reasons.append(bad_condition_reason)
            bad_condition_confidence = "high"
            bad_condition_mature = True
        elif starvation_risk:
            bad_condition_required = True
            bad_condition_reason = "starvation_risk"
            bad_condition_reasons.append(bad_condition_reason)
            bad_condition_confidence = "high"
            bad_condition_mature = True
        elif supply_rate_x < ROUTE2_BAD_CONDITION_SUPPLY_FLOOR_RATE_X:
            bad_condition_required = True
            bad_condition_reason = (
                "mature_supply_below_1_0"
                if supply_rate_x < ROUTE2_BAD_CONDITION_STRONG_SUPPLY_RATE_X
                else "mature_supply_below_1_05"
            )
            bad_condition_reasons.append(bad_condition_reason)
            bad_condition_confidence = (
                "high"
                if supply_rate_x < ROUTE2_BAD_CONDITION_STRONG_SUPPLY_RATE_X
                else "medium"
            )
            bad_condition_mature = True
        elif (
            runtime_runway_seconds + 0.001 < ROUTE2_FULL_FAST_START_RUNWAY_SECONDS
            and supply_rate_x < ROUTE2_ACTIVE_SUPPLY_HEALTHY_RATE_X
        ):
            bad_condition_required = True
            bad_condition_reason = "low_runway_with_poor_supply"
            bad_condition_reasons.append(bad_condition_reason)
            bad_condition_confidence = "medium"
            bad_condition_mature = True
        else:
            bad_condition_mature = True
            bad_condition_confidence = "high"
        reserve_eta_seconds = None
        if bad_condition_required and not reserve_satisfied and supply_rate_x > 0.001:
            reserve_eta_seconds = reserve_remaining_seconds / supply_rate_x
        gate_enabled = bool(getattr(self.settings, "route2_full_bad_condition_30min_gate_enabled", False))
        gate_dry_run_enabled = bool(
            getattr(self.settings, "route2_full_bad_condition_30min_gate_dry_run_enabled", True)
        )
        browser_session = session.browser_playback
        gate_applies_to_attach = bool(
            browser_session.attach_revision <= 0
            or browser_session.active_epoch_id != epoch.epoch_id
            or session.pending_target_seconds is not None
        )
        reserve_unsatisfied = bool(bad_condition_required and not reserve_satisfied)
        gate_blockers: list[str] = []
        if reserve_unsatisfied:
            gate_blockers.append("full_bad_condition_reserve_unsatisfied")
            if provider_blocker is not None:
                gate_blockers.append(provider_blocker)
            if cloud_source_feed_blocker is not None:
                gate_blockers.append(cloud_source_feed_blocker)
            if not coverage_starts_at_reserve:
                gate_blockers.append("published_frontier_not_contiguous_from_target")
            if not gate_applies_to_attach:
                gate_blockers.append("already_attached_no_detach")
        gate_would_block_ready = bool(
            gate_dry_run_enabled
            and reserve_unsatisfied
            and gate_applies_to_attach
        )
        gate_blocks_ready = bool(
            gate_enabled
            and reserve_unsatisfied
            and gate_applies_to_attach
        )
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
            "full_bad_condition_detected": bad_condition_required,
            "full_bad_condition_reason": bad_condition_reason if bad_condition_required else None,
            "full_bad_condition_reasons": bad_condition_reasons if bad_condition_required else [],
            "full_bad_condition_confidence": bad_condition_confidence,
            "full_bad_condition_mature": bad_condition_mature,
            "full_bad_condition_reserve_required_seconds": (
                reserve_required_seconds if is_full_route2 else 0.0
            ),
            "full_bad_condition_reserve_target_seconds": (
                reserve_target_ready_end_seconds if is_full_route2 else 0.0
            ),
            "full_bad_condition_actual_contiguous_end_seconds": (
                actual_ready_end_seconds if is_full_route2 else 0.0
            ),
            "full_bad_condition_actual_contiguous_seconds_after_target": (
                actual_contiguous_seconds_after_target if is_full_route2 else 0.0
            ),
            "full_bad_condition_reserve_remaining_seconds": (
                reserve_remaining_seconds if is_full_route2 else 0.0
            ),
            "full_bad_condition_reserve_satisfied": reserve_satisfied,
            "full_bad_condition_reserve_progress_source": progress_source if is_full_route2 else "not_full_playback",
            "full_bad_condition_reserve_eta_seconds": reserve_eta_seconds,
            "full_bad_condition_gate_enabled": gate_enabled,
            "full_bad_condition_gate_dry_run_enabled": gate_dry_run_enabled,
            "full_bad_condition_gate_would_block_ready": gate_would_block_ready,
            "full_bad_condition_gate_blocks_ready": gate_blocks_ready,
            "full_bad_condition_gate_blockers": list(dict.fromkeys(gate_blockers)),
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
            "full_bad_condition_detected": status["full_bad_condition_detected"],
            "full_bad_condition_reason": status["full_bad_condition_reason"],
            "full_bad_condition_reasons": list(status["full_bad_condition_reasons"]),
            "full_bad_condition_confidence": status["full_bad_condition_confidence"],
            "full_bad_condition_mature": status["full_bad_condition_mature"],
            "full_bad_condition_reserve_required_seconds": round(
                float(status["full_bad_condition_reserve_required_seconds"]),
                2,
            ),
            "full_bad_condition_reserve_target_seconds": round(
                float(status["full_bad_condition_reserve_target_seconds"]),
                2,
            ),
            "full_bad_condition_actual_contiguous_end_seconds": round(
                float(status["full_bad_condition_actual_contiguous_end_seconds"]),
                2,
            ),
            "full_bad_condition_actual_contiguous_seconds_after_target": round(
                float(status["full_bad_condition_actual_contiguous_seconds_after_target"]),
                2,
            ),
            "full_bad_condition_reserve_remaining_seconds": round(
                float(status["full_bad_condition_reserve_remaining_seconds"]),
                2,
            ),
            "full_bad_condition_reserve_satisfied": status["full_bad_condition_reserve_satisfied"],
            "full_bad_condition_reserve_progress_source": status["full_bad_condition_reserve_progress_source"],
            "full_bad_condition_reserve_eta_seconds": (
                round(float(status["full_bad_condition_reserve_eta_seconds"]), 2)
                if status["full_bad_condition_reserve_eta_seconds"] is not None
                else None
            ),
            "full_bad_condition_gate_enabled": status["full_bad_condition_gate_enabled"],
            "full_bad_condition_gate_dry_run_enabled": status["full_bad_condition_gate_dry_run_enabled"],
            "full_bad_condition_gate_would_block_ready": status["full_bad_condition_gate_would_block_ready"],
            "full_bad_condition_gate_blocks_ready": status["full_bad_condition_gate_blocks_ready"],
            "full_bad_condition_gate_blockers": list(status["full_bad_condition_gate_blockers"]),
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

    def _route2_maintenance_tier_target(
        self,
        *,
        assigned_threads: int,
        supply_rate_x: float,
        runway_seconds: float,
        comfortable_runway_seconds: float,
        manifest_complete: bool,
    ) -> int:
        current_threads = max(1, int(assigned_threads or 0))
        floor = max(4, int(self.settings.route2_min_worker_threads))
        if manifest_complete or (
            supply_rate_x >= ROUTE2_CLOSED_LOOP_DONOR_RATE_X
            and runway_seconds >= comfortable_runway_seconds * 1.25
        ):
            return min(current_threads, floor)
        return min(current_threads, max(floor, 6))

    def _route2_downshift_transition_headroom_locked(self, *, user_id: int) -> int:
        budget = self._route2_budget_summary_locked()
        total_available = int(budget["total_route2_budget_cores"]) - self._route2_running_threads_locked()
        per_user_available = int(budget["per_user_budget_cores"]) - self._route2_running_threads_locked(user_id=user_id)
        return max(0, min(total_available, per_user_available))

    def _route2_downshift_retry_cap_remaining(self, browser_session: BrowserPlaybackSession) -> int:
        return max(
            0,
            ROUTE2_ADAPTIVE_DOWNSHIFT_MAX_RETRIES_PER_SESSION
            - int(browser_session.adaptive_downshift_retry_count or 0),
        )

    def _route2_downshift_retry_seconds_remaining(
        self,
        browser_session: BrowserPlaybackSession,
        *,
        now_ts: float | None = None,
    ) -> float | None:
        reference_ts = time.time() if now_ts is None else now_ts
        retry_at = float(browser_session.adaptive_downshift_retry_not_before_ts or 0.0)
        if retry_at <= reference_ts:
            return None
        return max(0.0, retry_at - reference_ts)

    def _route2_reclaim_retry_cap_remaining(self, browser_session: BrowserPlaybackSession) -> int:
        return max(
            0,
            ROUTE2_ADAPTIVE_RECLAIM_MAX_ATTEMPTS_PER_DONOR
            - int(browser_session.adaptive_reclaim_retry_count or 0),
        )

    def _route2_reclaim_retry_seconds_remaining(
        self,
        browser_session: BrowserPlaybackSession,
        *,
        now_ts: float | None = None,
    ) -> float | None:
        reference_ts = time.time() if now_ts is None else now_ts
        retry_at = float(browser_session.adaptive_reclaim_retry_not_before_ts or 0.0)
        if retry_at <= reference_ts:
            return None
        return max(0.0, retry_at - reference_ts)

    def _route2_downshift_pressure_snapshot_payload(
        self,
        snapshot: _Route2ResourceSnapshot | None,
        *,
        moderate_sample_count: int = 0,
        moderate_elapsed_seconds: float | None = None,
    ) -> dict[str, object]:
        if snapshot is None:
            return {
                "sample_available": False,
                "sample_mature": False,
                "sample_stale": True,
                "external_pressure_level": "unknown",
                "external_pressure_reason": "resource_snapshot_missing",
                "moderate_sample_count": max(0, int(moderate_sample_count)),
                "moderate_elapsed_seconds": (
                    round(moderate_elapsed_seconds, 3) if moderate_elapsed_seconds is not None else None
                ),
            }
        return {
            "sample_available": True,
            "sample_mature": bool(snapshot.sample_mature),
            "sample_stale": bool(snapshot.sample_stale),
            "sample_age_seconds": max(0.0, round(time.time() - snapshot.sampled_at_ts, 3)),
            "host_cpu_total_cores": snapshot.host_cpu_total_cores,
            "host_cpu_used_cores": (
                round(snapshot.host_cpu_used_cores, 3) if snapshot.host_cpu_used_cores is not None else None
            ),
            "host_cpu_used_percent": (
                round(snapshot.host_cpu_used_percent, 4) if snapshot.host_cpu_used_percent is not None else None
            ),
            "external_cpu_cores_used_estimate": (
                round(snapshot.external_cpu_cores_used_estimate, 3)
                if snapshot.external_cpu_cores_used_estimate is not None
                else None
            ),
            "external_cpu_percent_estimate": (
                round(snapshot.external_cpu_percent_estimate, 4)
                if snapshot.external_cpu_percent_estimate is not None
                else None
            ),
            "external_ffmpeg_process_count": int(snapshot.external_ffmpeg_process_count),
            "external_ffmpeg_cpu_cores_estimate": (
                round(snapshot.external_ffmpeg_cpu_cores_estimate, 3)
                if snapshot.external_ffmpeg_cpu_cores_estimate is not None
                else None
            ),
            "route2_worker_ffmpeg_process_count": int(snapshot.route2_worker_ffmpeg_process_count),
            "elvern_owned_ffmpeg_process_count": int(snapshot.elvern_owned_ffmpeg_process_count),
            "elvern_owned_ffmpeg_cpu_cores_estimate": (
                round(snapshot.elvern_owned_ffmpeg_cpu_cores_estimate, 3)
                if snapshot.elvern_owned_ffmpeg_cpu_cores_estimate is not None
                else None
            ),
            "route2_cpu_cores_used_total": (
                round(snapshot.route2_cpu_cores_used_total, 3)
                if snapshot.route2_cpu_cores_used_total is not None
                else None
            ),
            "external_pressure_level": snapshot.external_pressure_level,
            "external_pressure_reason": snapshot.external_pressure_reason,
            "moderate_sample_count": max(0, int(moderate_sample_count)),
            "moderate_elapsed_seconds": (
                round(moderate_elapsed_seconds, 3) if moderate_elapsed_seconds is not None else None
            ),
        }

    def _route2_reset_downshift_pressure_tracker_locked(self, browser_session: BrowserPlaybackSession) -> None:
        browser_session.adaptive_downshift_pressure_moderate_started_at_ts = 0.0
        browser_session.adaptive_downshift_pressure_moderate_sample_count = 0

    def _route2_high_cpu_pressure_is_route2_dominated_locked(
        self,
        snapshot: _Route2ResourceSnapshot,
    ) -> bool:
        if snapshot.external_pressure_level != "high":
            return False
        if snapshot.external_pressure_reason != "external_cpu_high":
            return False
        if snapshot.external_ffmpeg_process_count > 0:
            return False
        if int(snapshot.route2_worker_ffmpeg_process_count or 0) <= 0:
            return False
        route2_cpu = snapshot.route2_cpu_cores_used_total
        host_cpu = snapshot.host_cpu_used_cores
        if route2_cpu is None or host_cpu is None or host_cpu <= 0:
            return False
        # When Route2 ffmpeg dominates a saturated host, short sampling skew can
        # make Route2-owned CPU look like "external" residual. Treat that case
        # like sustained-pressure evidence instead of a hard one-sample abort.
        # The host and per-worker CPU samples are collected from different
        # clocks, so a saturated Route2 ffmpeg can temporarily leave a large
        # residual even when no external ffmpeg or heavy process is present.
        # Do not ignore it; downgrade it to the sustained-pressure path so a
        # real non-Elvern load still aborts if it persists.
        return float(route2_cpu) >= 8.0 and (float(route2_cpu) / float(host_cpu)) >= 0.50

    def _route2_downshift_pressure_abort_reason_locked(
        self,
        session: MobilePlaybackSession,
        snapshot: _Route2ResourceSnapshot | None,
    ) -> str | None:
        browser_session = session.browser_playback
        now_ts = time.time()
        if snapshot is None or snapshot.sample_stale:
            self._route2_reset_downshift_pressure_tracker_locked(browser_session)
            browser_session.adaptive_downshift_pressure_abort_reason = None
            browser_session.adaptive_downshift_pressure_snapshot = self._route2_downshift_pressure_snapshot_payload(
                snapshot,
            )
            return None
        if snapshot.external_ffmpeg_process_count > 0:
            self._route2_reset_downshift_pressure_tracker_locked(browser_session)
            browser_session.adaptive_downshift_pressure_abort_reason = "external_ffmpeg_detected"
            browser_session.adaptive_downshift_pressure_snapshot = self._route2_downshift_pressure_snapshot_payload(
                snapshot,
            )
            return "external_ffmpeg_during_downshift"
        pressure_level = snapshot.external_pressure_level
        pressure_reason = snapshot.external_pressure_reason
        if self._route2_high_cpu_pressure_is_route2_dominated_locked(snapshot):
            pressure_level = "moderate"
            pressure_reason = f"{pressure_reason or 'external_cpu_high'}_route2_dominated_uncertain"
        if pressure_level == "high":
            self._route2_reset_downshift_pressure_tracker_locked(browser_session)
            browser_session.adaptive_downshift_pressure_abort_reason = (
                pressure_reason or "external_pressure_high"
            )
            browser_session.adaptive_downshift_pressure_snapshot = self._route2_downshift_pressure_snapshot_payload(
                snapshot,
            )
            return "external_pressure_during_downshift"
        if pressure_level == "moderate":
            started_at = float(browser_session.adaptive_downshift_pressure_moderate_started_at_ts or 0.0)
            if started_at <= 0.0:
                started_at = now_ts
                browser_session.adaptive_downshift_pressure_moderate_started_at_ts = started_at
                browser_session.adaptive_downshift_pressure_moderate_sample_count = 1
            else:
                browser_session.adaptive_downshift_pressure_moderate_sample_count += 1
            sample_count = int(browser_session.adaptive_downshift_pressure_moderate_sample_count)
            elapsed_seconds = max(0.0, now_ts - started_at)
            browser_session.adaptive_downshift_pressure_snapshot = self._route2_downshift_pressure_snapshot_payload(
                snapshot,
                moderate_sample_count=sample_count,
                moderate_elapsed_seconds=elapsed_seconds,
            )
            if (
                sample_count >= ROUTE2_ADAPTIVE_DOWNSHIFT_MODERATE_PRESSURE_MIN_SAMPLES
                and elapsed_seconds >= ROUTE2_ADAPTIVE_DOWNSHIFT_MODERATE_PRESSURE_MIN_SECONDS
            ):
                browser_session.adaptive_downshift_pressure_abort_reason = (
                    f"{pressure_reason or 'external_pressure_moderate'}_sustained"
                )
                return "external_pressure_during_downshift"
            browser_session.adaptive_downshift_pressure_abort_reason = None
            return None
        self._route2_reset_downshift_pressure_tracker_locked(browser_session)
        browser_session.adaptive_downshift_pressure_abort_reason = None
        browser_session.adaptive_downshift_pressure_snapshot = self._route2_downshift_pressure_snapshot_payload(
            snapshot,
        )
        return None

    def _adaptive_downshift_default_payload(self) -> dict[str, object]:
        return {
            "adaptive_downshift_enabled": bool(getattr(self.settings, "route2_adaptive_downshift_enabled", False)),
            "adaptive_downshift_candidate": False,
            "adaptive_downshift_mode": "none",
            "autonomous_maintenance_downshift_enabled": bool(
                getattr(self.settings, "route2_adaptive_maintenance_downshift_enabled", False)
            ),
            "autonomous_maintenance_downshift_candidate": False,
            "autonomous_maintenance_downshift_blockers": ["route2_session_or_epoch_missing"],
            "maintenance_downshift_suppressed_by_reclaim": False,
            "donor_reserved_for_reclaim": False,
            "reclaim_donor_downshift_active": False,
            "adaptive_downshift_target_threads": None,
            "adaptive_downshift_policy": "phase_3a_dry_run_only",
            "adaptive_downshift_reason": "No Route2 worker is available for downshift evaluation.",
            "adaptive_downshift_blockers": ["route2_session_or_epoch_missing"],
            "adaptive_downshift_replacement_epoch_id": None,
            "adaptive_downshift_replacement_worker_id": None,
            "adaptive_downshift_state": "none",
            "adaptive_downshift_action_deferred": False,
            "adaptive_downshift_action_defer_reason": None,
            "adaptive_downshift_transition_started_at": None,
            "adaptive_downshift_switched_at": None,
            "adaptive_downshift_aborted_reason": None,
            "adaptive_downshift_pressure_abort_reason": None,
            "adaptive_downshift_pressure_snapshot": {},
            "adaptive_downshift_retry_count": 0,
            "adaptive_downshift_retry_not_before_seconds": None,
            "adaptive_downshift_retry_blocker": None,
            "adaptive_downshift_last_abort_reason": None,
            "adaptive_downshift_replacement_epoch_cap_remaining": ROUTE2_ADAPTIVE_DOWNSHIFT_MAX_RETRIES_PER_SESSION,
            "adaptive_boost_exit_reason": None,
            "current_boost_tier": None,
            "maintenance_tier_target": None,
            "downshift_safe_to_apply": False,
            "downshift_transition_headroom_required": None,
            "downshift_transition_headroom_available": None,
        }

    def _apply_route2_downshift_payload_to_record(
        self,
        record: Route2WorkerRecord,
        payload: dict[str, object],
    ) -> None:
        record.adaptive_downshift_enabled = bool(payload["adaptive_downshift_enabled"])
        record.adaptive_downshift_candidate = bool(payload["adaptive_downshift_candidate"])
        record.adaptive_downshift_mode = str(payload["adaptive_downshift_mode"])
        record.autonomous_maintenance_downshift_enabled = bool(payload["autonomous_maintenance_downshift_enabled"])
        record.autonomous_maintenance_downshift_candidate = bool(payload["autonomous_maintenance_downshift_candidate"])
        record.autonomous_maintenance_downshift_blockers = list(
            payload["autonomous_maintenance_downshift_blockers"]  # type: ignore[arg-type]
        )
        record.maintenance_downshift_suppressed_by_reclaim = bool(
            payload["maintenance_downshift_suppressed_by_reclaim"]
        )
        record.donor_reserved_for_reclaim = bool(payload["donor_reserved_for_reclaim"])
        record.reclaim_donor_downshift_active = bool(payload["reclaim_donor_downshift_active"])
        record.adaptive_downshift_target_threads = payload["adaptive_downshift_target_threads"]  # type: ignore[assignment]
        record.adaptive_downshift_policy = payload["adaptive_downshift_policy"]  # type: ignore[assignment]
        record.adaptive_downshift_reason = payload["adaptive_downshift_reason"]  # type: ignore[assignment]
        record.adaptive_downshift_blockers = list(payload["adaptive_downshift_blockers"])  # type: ignore[arg-type]
        record.adaptive_downshift_replacement_epoch_id = payload["adaptive_downshift_replacement_epoch_id"]  # type: ignore[assignment]
        record.adaptive_downshift_replacement_worker_id = payload["adaptive_downshift_replacement_worker_id"]  # type: ignore[assignment]
        record.adaptive_downshift_state = str(payload["adaptive_downshift_state"])
        record.adaptive_downshift_action_deferred = bool(payload.get("adaptive_downshift_action_deferred", False))
        record.adaptive_downshift_action_defer_reason = payload.get(  # type: ignore[assignment]
            "adaptive_downshift_action_defer_reason"
        )
        record.adaptive_downshift_transition_started_at = payload["adaptive_downshift_transition_started_at"]  # type: ignore[assignment]
        record.adaptive_downshift_switched_at = payload["adaptive_downshift_switched_at"]  # type: ignore[assignment]
        record.adaptive_downshift_aborted_reason = payload["adaptive_downshift_aborted_reason"]  # type: ignore[assignment]
        record.adaptive_downshift_pressure_abort_reason = payload["adaptive_downshift_pressure_abort_reason"]  # type: ignore[assignment]
        record.adaptive_downshift_pressure_snapshot = dict(payload["adaptive_downshift_pressure_snapshot"])  # type: ignore[arg-type]
        record.adaptive_downshift_retry_count = int(payload["adaptive_downshift_retry_count"])
        record.adaptive_downshift_retry_not_before_seconds = payload["adaptive_downshift_retry_not_before_seconds"]  # type: ignore[assignment]
        record.adaptive_downshift_retry_blocker = payload["adaptive_downshift_retry_blocker"]  # type: ignore[assignment]
        record.adaptive_downshift_last_abort_reason = payload["adaptive_downshift_last_abort_reason"]  # type: ignore[assignment]
        record.adaptive_downshift_replacement_epoch_cap_remaining = payload["adaptive_downshift_replacement_epoch_cap_remaining"]  # type: ignore[assignment]
        record.adaptive_boost_exit_reason = payload["adaptive_boost_exit_reason"]  # type: ignore[assignment]
        record.current_boost_tier = payload["current_boost_tier"]  # type: ignore[assignment]
        record.maintenance_tier_target = payload["maintenance_tier_target"]  # type: ignore[assignment]
        record.downshift_safe_to_apply = bool(payload["downshift_safe_to_apply"])
        record.downshift_transition_headroom_required = payload["downshift_transition_headroom_required"]  # type: ignore[assignment]
        record.downshift_transition_headroom_available = payload["downshift_transition_headroom_available"]  # type: ignore[assignment]

    def _route2_adaptive_downshift_payload_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        record: Route2WorkerRecord,
        decision: _Route2ClosedLoopDryRunDecision,
    ) -> dict[str, object]:
        downshift_enabled = bool(getattr(self.settings, "route2_adaptive_downshift_enabled", False))
        dry_run_enabled = bool(getattr(self.settings, "route2_adaptive_downshift_dry_run_enabled", True))
        maintenance_enabled = bool(getattr(self.settings, "route2_adaptive_maintenance_downshift_enabled", False))
        maintenance_dry_run_enabled = bool(
            getattr(self.settings, "route2_adaptive_maintenance_downshift_dry_run_enabled", True)
        )
        assigned_threads = max(0, int(record.assigned_threads or 0))
        browser_session = session.browser_playback
        replacement_epoch_id = browser_session.replacement_epoch_id
        now_ts = time.time()
        pending_reclaim = (
            self._route2_pending_reclaim_request_locked(now_ts=now_ts)
            if bool(getattr(self.settings, "route2_adaptive_reclaim_enabled", False))
            else None
        )
        resource_snapshot = self._latest_route2_resource_snapshot_locked(now_ts=now_ts)
        retry_not_before_seconds = self._route2_downshift_retry_seconds_remaining(
            browser_session,
            now_ts=now_ts,
        )
        retry_blocker = browser_session.adaptive_downshift_retry_blocker
        retry_cap_remaining = self._route2_downshift_retry_cap_remaining(browser_session)
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
        comfortable_runway_seconds = self._route2_closed_loop_comfortable_runway_seconds(record.playback_mode)
        blockers: list[str] = []
        stabilization_payload = self._route2_resupply_stabilization_payload_locked(
            browser_session,
            now_ts=now_ts,
        )

        if not (dry_run_enabled or maintenance_dry_run_enabled or downshift_enabled):
            blockers.append("adaptive_downshift_dry_run_disabled")
        if bool(stabilization_payload["adaptive_resupply_stabilization_active"]):
            blockers.append("recently_resupplied_donor_stabilizing")
        if downshift_enabled and retry_not_before_seconds is not None:
            retry_blocker = "adaptive_downshift_retry_cooldown_active"
            blockers.append(retry_blocker)
        if downshift_enabled and retry_cap_remaining <= 0:
            retry_blocker = "adaptive_downshift_retry_cap_exceeded"
            blockers.append(retry_blocker)
        if downshift_enabled and (
            resource_snapshot is None
            or resource_snapshot.sample_stale
            or not resource_snapshot.sample_mature
        ):
            blockers.append("downshift_pressure_sample_unavailable")
        if assigned_threads < 9:
            blockers.append("not_boosted_prepare_tier")
        if record.state != "running" or not record.process_exists:
            blockers.append("worker_not_running")
        if record.stop_requested or epoch.stop_requested or epoch.state in {"draining", "failed", "ended"}:
            blockers.append("cleanup_or_drain_instability")
        if replacement_epoch_id:
            blockers.append("replacement_already_in_progress")
        if record.non_retryable_error or session.last_error or epoch.last_error:
            blockers.append("provider_source_or_session_error")
        if decision.role == "metrics_immature" or observation_seconds < ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS:
            blockers.append("telemetry_immature")
        if decision.prepare_boost_needed or decision.needs_resource:
            blockers.append("prepare_or_recovery_still_needs_boost")
        if decision.role in {"provider_error", "source_bound", "client_bound", "host_pressure_limited"}:
            blockers.append(f"{decision.role}_blocks_downshift")
        if self._stalled_recovery_needed(session):
            blockers.append("stalled_recovery_needed")
        if self._starvation_risk(session):
            blockers.append("starvation_risk")
        if bool(reserve_status.get("bad_condition_reserve_required")) and not bool(reserve_status.get("reserve_satisfied")):
            blockers.append("active_bad_condition_reserve_protection")
        if not manifest_complete and supply_rate_x < ROUTE2_CLOSED_LOOP_DOWNSHIFT_RATE_X:
            blockers.append("supply_below_downshift_threshold")
        if not manifest_complete and runway_seconds < comfortable_runway_seconds:
            blockers.append("runway_below_comfortable_target")
        if session.lifecycle_state not in {"attached", "background-suspended", "resuming"}:
            blockers.append("client_lifecycle_not_stable")
        if not manifest_complete and not refill_in_progress:
            blockers.append("no_active_refill_to_replace")

        maintenance_target = None
        if assigned_threads >= 9:
            maintenance_target = self._route2_maintenance_tier_target(
                assigned_threads=assigned_threads,
                supply_rate_x=supply_rate_x,
                runway_seconds=runway_seconds,
                comfortable_runway_seconds=comfortable_runway_seconds,
                manifest_complete=manifest_complete,
            )
            if maintenance_target >= assigned_threads:
                blockers.append("maintenance_target_not_below_current")
        headroom_available = (
            self._route2_downshift_transition_headroom_locked(user_id=record.user_id)
            if maintenance_target is not None
            else None
        )
        transition_headroom_blocked = bool(
            downshift_enabled
            and maintenance_target is not None
            and headroom_available is not None
            and headroom_available < maintenance_target
        )
        candidate = not blockers and maintenance_target is not None and maintenance_target < assigned_threads
        state = "candidate" if candidate else "none"
        replacement_worker_id = None
        transition_started_at = epoch.adaptive_downshift_transition_started_at
        switched_at = epoch.adaptive_downshift_switched_at
        aborted_reason = epoch.adaptive_downshift_aborted_reason
        reclaim_downshift_active = bool(epoch.adaptive_reclaim_request_id)
        if replacement_epoch_id:
            replacement = session.browser_playback.epochs.get(replacement_epoch_id)
            replacement_worker_id = replacement.active_worker_id if replacement is not None else None
            if replacement is not None and replacement.adaptive_reclaim_request_id:
                reclaim_downshift_active = True
            transition_started_at = (
                replacement.adaptive_downshift_transition_started_at if replacement is not None else None
            )
            switched_at = replacement.adaptive_downshift_switched_at if replacement is not None else None
            aborted_reason = replacement.adaptive_downshift_aborted_reason if replacement is not None else None
            if replacement is not None and replacement.replacement_reason == "maintenance_downshift":
                if replacement.state == "failed":
                    state = "failed"
                elif self._route2_downshift_replacement_ready_locked(session, replacement):
                    state = "ready_to_switch"
                elif replacement.active_worker_id or (replacement.process and replacement.process.poll() is None):
                    state = "replacement_warming"
                else:
                    state = "replacement_starting"
            else:
                state = "failed" if replacement is not None and replacement.state == "failed" else "replacement_warming"
        elif epoch.replacement_reason == "maintenance_downshift":
            replacement_epoch_id = epoch.epoch_id
            replacement_worker_id = epoch.active_worker_id
            transition_started_at = epoch.adaptive_downshift_transition_started_at
            switched_at = epoch.adaptive_downshift_switched_at
            aborted_reason = epoch.adaptive_downshift_aborted_reason
            maintenance_target = epoch.maintenance_downshift_target_threads or maintenance_target
            if epoch.adaptive_downshift_aborted_reason:
                state = "aborted"
            elif epoch.adaptive_downshift_switched_at:
                state = "switched"
            elif epoch.active_worker_id or (epoch.process and epoch.process.poll() is None):
                state = "replacement_warming"
            else:
                state = "replacement_starting"
        donor_reserved_for_reclaim = bool(candidate and pending_reclaim is not None and not reclaim_downshift_active)
        maintenance_downshift_suppressed_by_reclaim = bool(donor_reserved_for_reclaim)
        autonomous_candidate = bool(candidate and not reclaim_downshift_active and not donor_reserved_for_reclaim)
        autonomous_blockers = list(blockers)
        if donor_reserved_for_reclaim:
            autonomous_blockers.append("maintenance_downshift_suppressed_by_reclaim")
            autonomous_blockers.append("donor_reserved_for_reclaim")
        elif candidate and not maintenance_enabled:
            autonomous_blockers.append("autonomous_maintenance_downshift_disabled")
        reclaim_mode_allowed = bool(pending_reclaim is not None or reclaim_downshift_active)
        maintenance_mode_allowed = bool(maintenance_enabled and not maintenance_downshift_suppressed_by_reclaim)
        foreground_action_deferred = bool(
            candidate
            and downshift_enabled
            and (reclaim_mode_allowed or maintenance_mode_allowed)
            and self._route2_downshift_action_would_interrupt_active_client_locked(session)
        )
        if foreground_action_deferred:
            autonomous_blockers.append("foreground_active_playback_defer")
            if state == "candidate":
                state = "recommended_deferred"
        safe_to_apply = bool(
            candidate
            and downshift_enabled
            and not transition_headroom_blocked
            and (reclaim_mode_allowed or maintenance_mode_allowed)
            and not foreground_action_deferred
        )
        if transition_headroom_blocked:
            blockers.append("downshift_transition_headroom_unavailable")
            autonomous_blockers.append("downshift_transition_headroom_unavailable")
        reason = (
            "Boosted worker is oversupplied and can be planned for a lower maintenance replacement."
            if candidate
            else "Downshift replacement is blocked by conservative safety gates."
        )
        if candidate and not downshift_enabled:
            reason = "Dry-run candidate only; real replacement downshift flag is disabled."
        elif candidate and donor_reserved_for_reclaim:
            reason = "Maintenance downshift is suppressed; donor is reserved for an admission-triggered reclaim transaction."
        elif candidate and downshift_enabled and not maintenance_enabled and not reclaim_mode_allowed:
            reason = "Dry-run candidate only; autonomous maintenance downshift flag is disabled."
        if candidate and downshift_enabled and transition_headroom_blocked:
            reason = "Dry-run candidate only; transition headroom cannot safely hold old and replacement workers together."
        elif candidate and foreground_action_deferred:
            reason = (
                "Active ffmpeg thread count cannot be safely changed in-place; foreground visible downshift "
                "is deferred until playback is paused, backgrounded, or otherwise safe."
            )
        elif candidate and donor_reserved_for_reclaim:
            reason = "Maintenance downshift is suppressed; donor is reserved for an admission-triggered reclaim transaction."
        elif candidate and downshift_enabled and reclaim_mode_allowed:
            reason = "Safety gates passed; admission-triggered reclaim donor replacement downshift is allowed to start."
        elif candidate and downshift_enabled and maintenance_mode_allowed:
            reason = "Safety gates passed; real maintenance replacement downshift is allowed to start."
        target_for_status = (
            maintenance_target
            if candidate or state in {"replacement_starting", "replacement_warming", "ready_to_switch", "switched", "aborted"}
            else None
        )
        if epoch.replacement_reason == "adaptive_resupply_boost":
            downshift_mode = "resupply_boost"
        elif reclaim_downshift_active or donor_reserved_for_reclaim:
            downshift_mode = "reclaim_donor"
        elif candidate or state in {"replacement_starting", "replacement_warming", "ready_to_switch", "switched", "aborted"}:
            downshift_mode = "autonomous_maintenance"
        else:
            downshift_mode = "none"
        return {
            "adaptive_downshift_enabled": downshift_enabled,
            "adaptive_downshift_candidate": candidate,
            "adaptive_downshift_mode": downshift_mode,
            "autonomous_maintenance_downshift_enabled": maintenance_enabled,
            "autonomous_maintenance_downshift_candidate": autonomous_candidate,
            "autonomous_maintenance_downshift_blockers": list(dict.fromkeys(autonomous_blockers)),
            "maintenance_downshift_suppressed_by_reclaim": maintenance_downshift_suppressed_by_reclaim,
            "donor_reserved_for_reclaim": donor_reserved_for_reclaim,
            "reclaim_donor_downshift_active": bool(reclaim_downshift_active),
            "adaptive_downshift_target_threads": target_for_status,
            "adaptive_downshift_policy": (
                "reclaim_donor" if reclaim_downshift_active or donor_reserved_for_reclaim else "maintenance"
            ),
            "adaptive_downshift_reason": reason,
            "adaptive_downshift_blockers": list(dict.fromkeys(blockers)),
            "adaptive_downshift_replacement_epoch_id": replacement_epoch_id,
            "adaptive_downshift_replacement_worker_id": replacement_worker_id,
            "adaptive_downshift_state": state,
            "adaptive_downshift_action_deferred": foreground_action_deferred,
            "adaptive_downshift_action_defer_reason": (
                "foreground_active_playback" if foreground_action_deferred else None
            ),
            "adaptive_downshift_transition_started_at": transition_started_at,
            "adaptive_downshift_switched_at": switched_at,
            "adaptive_downshift_aborted_reason": aborted_reason,
            "adaptive_downshift_pressure_abort_reason": browser_session.adaptive_downshift_pressure_abort_reason,
            "adaptive_downshift_pressure_snapshot": dict(browser_session.adaptive_downshift_pressure_snapshot),
            "adaptive_downshift_retry_count": int(browser_session.adaptive_downshift_retry_count or 0),
            "adaptive_downshift_retry_not_before_seconds": (
                round(retry_not_before_seconds, 3) if retry_not_before_seconds is not None else None
            ),
            "adaptive_downshift_retry_blocker": retry_blocker,
            "adaptive_downshift_last_abort_reason": browser_session.adaptive_downshift_last_abort_reason,
            "adaptive_downshift_replacement_epoch_cap_remaining": retry_cap_remaining,
            "adaptive_boost_exit_reason": "oversupplied_comfortable_runway" if candidate else None,
            "current_boost_tier": assigned_threads if assigned_threads >= 9 else None,
            "maintenance_tier_target": target_for_status,
            "downshift_safe_to_apply": safe_to_apply,
            "downshift_transition_headroom_required": maintenance_target,
            "downshift_transition_headroom_available": headroom_available,
        }

    def _route2_admission_available_reserved_threads_locked(self) -> int:
        budget = self._route2_budget_summary_locked()
        active_records = [
            record
            for record in self._route2_workers.values()
            if record.state in {"queued", "running", "stopping"}
        ]
        reserved_total_threads = sum(
            self._route2_reserved_threads_for_admission_locked(record)
            for record in active_records
        )
        return int(budget["total_route2_budget_cores"]) - reserved_total_threads

    def _route2_reclaim_capacity_measurement_locked(
        self,
        *,
        user_id: int,
    ) -> dict[str, object]:
        snapshot = self._latest_route2_resource_snapshot_locked()
        headroom = self._route2_admission_available_reserved_threads_locked()
        host_total = snapshot.host_cpu_total_cores if snapshot is not None else None
        host_used = snapshot.host_cpu_used_cores if snapshot is not None else None
        host_spare = (
            max(0.0, float(host_total) - float(host_used))
            if host_total is not None and host_used is not None
            else None
        )
        return {
            "snapshot_fresh": bool(snapshot is not None and snapshot.sample_mature and not snapshot.sample_stale),
            "route2_cpu_cores_used": (
                round(float(snapshot.route2_cpu_cores_used_total), 3)
                if snapshot is not None and snapshot.route2_cpu_cores_used_total is not None
                else None
            ),
            "user_cpu_cores_used": (
                round(float(snapshot.per_user_cpu_cores_used_total.get(int(user_id), 0.0)), 3)
                if snapshot is not None
                else None
            ),
            "host_cpu_used_cores": round(float(host_used), 3) if host_used is not None else None,
            "host_cpu_spare_cores": round(float(host_spare), 3) if host_spare is not None else None,
            "route2_headroom": headroom,
            "memory_pressure": (
                round(float(snapshot.route2_memory_percent_of_total), 4)
                if snapshot is not None and snapshot.route2_memory_percent_of_total is not None
                else None
            ),
            "external_pressure": snapshot.external_pressure_level if snapshot is not None else None,
            "external_pressure_reason": snapshot.external_pressure_reason if snapshot is not None else None,
        }

    def _mark_route2_reclaim_consumer_admitted_if_matching_locked(
        self,
        *,
        incoming_user_id: int,
        incoming_media_item_id: int | None,
    ) -> None:
        for donor_session in self._sessions.values():
            browser_session = donor_session.browser_playback
            if browser_session.engine_mode != "route2":
                continue
            if browser_session.adaptive_reclaim_state != "capacity_available":
                continue
            if not browser_session.adaptive_reclaim_capacity_sufficient_for_consumer:
                continue
            if browser_session.adaptive_reclaim_consumer_user_id != int(incoming_user_id):
                continue
            if (
                incoming_media_item_id is not None
                and browser_session.adaptive_reclaim_consumer_media_item_id is not None
                and browser_session.adaptive_reclaim_consumer_media_item_id != int(incoming_media_item_id)
            ):
                continue
            browser_session.adaptive_reclaim_state = "consumer_admitted_after_reclaim"
            browser_session.adaptive_reclaim_completed_at = utcnow_iso()
            browser_session.adaptive_reclaim_failed_reason = None
            browser_session.adaptive_reclaim_abort_reason = None

    def _route2_resupplied_reclaim_capacity_blocker_locked(
        self,
        *,
        incoming_user_id: int,
        incoming_media_item_id: int | None,
    ) -> dict[str, object] | None:
        for donor_session in self._sessions.values():
            browser_session = donor_session.browser_playback
            if browser_session.engine_mode != "route2":
                continue
            if browser_session.adaptive_reclaim_state != "capacity_available":
                continue
            if browser_session.adaptive_reclaim_consumer_user_id != int(incoming_user_id):
                continue
            if (
                incoming_media_item_id is not None
                and browser_session.adaptive_reclaim_consumer_media_item_id is not None
                and browser_session.adaptive_reclaim_consumer_media_item_id != int(incoming_media_item_id)
            ):
                continue
            stabilization = self._route2_resupply_stabilization_payload_locked(browser_session)
            if not bool(stabilization["adaptive_resupply_stabilization_active"]):
                continue
            if browser_session.adaptive_reclaim_capacity_sufficient_for_consumer is not False:
                continue
            return {
                "admission_waiting_for_reclaim": False,
                "admission_reclaim_possible": False,
                "admission_reclaim_attempted": True,
                "admission_reclaim_succeeded": False,
                "admission_reclaim_failed_reason": "insufficient_measured_capacity_after_resupply",
                "admission_capacity_after_reclaim": browser_session.adaptive_reclaim_cpu_headroom_after,
                "admission_hard_block_reason": "insufficient_measured_capacity_after_resupply",
                "adaptive_reclaim_enabled": bool(getattr(self.settings, "route2_adaptive_reclaim_enabled", False)),
                "adaptive_reclaim_dry_run_enabled": bool(
                    getattr(self.settings, "route2_adaptive_reclaim_dry_run_enabled", True)
                ),
                "adaptive_reclaim_state": browser_session.adaptive_reclaim_state,
                "adaptive_reclaim_request_id": browser_session.adaptive_reclaim_request_id,
                "adaptive_reclaim_capacity_sufficient_for_consumer": False,
                "adaptive_reclaim_blockers": ["insufficient_measured_capacity_after_resupply"],
                **stabilization,
            }
        return None

    def _route2_pending_reclaim_request_locked(self, *, now_ts: float | None = None) -> dict[str, object] | None:
        request = self._route2_pending_reclaim_request
        if request is None:
            return None
        reference_ts = time.time() if now_ts is None else now_ts
        if float(request.get("expires_at_ts") or 0.0) <= reference_ts:
            self._route2_pending_reclaim_request = None
            return None
        return request

    def _route2_create_pending_reclaim_request_locked(
        self,
        *,
        incoming_user_id: int,
        incoming_media_item_id: int | None,
        incoming_consumer_session_id: str | None,
        incoming_consumer_reason: str,
    ) -> dict[str, object]:
        now_ts = time.time()
        request = self._route2_pending_reclaim_request_locked(now_ts=now_ts)
        if request is None:
            request = {
                "adaptive_reclaim_request_id": f"reclaim-{uuid.uuid4().hex}",
                "adaptive_reclaim_consumer_worker_id": f"admission-user-{int(incoming_user_id)}",
                "adaptive_reclaim_consumer_session_id": incoming_consumer_session_id,
                "adaptive_reclaim_consumer_user_id": int(incoming_user_id),
                "adaptive_reclaim_consumer_media_item_id": incoming_media_item_id,
                "adaptive_reclaim_consumer_reason": incoming_consumer_reason,
                "created_at": utcnow_iso(),
                "expires_at_ts": now_ts + ROUTE2_ADAPTIVE_RECLAIM_PENDING_TTL_SECONDS,
            }
            self._route2_pending_reclaim_request = request
        return request

    def _adaptive_reclaim_default_payload(self) -> dict[str, object]:
        return {
            "adaptive_reclaim_enabled": bool(getattr(self.settings, "route2_adaptive_reclaim_enabled", False)),
            "adaptive_reclaim_dry_run_enabled": bool(
                getattr(self.settings, "route2_adaptive_reclaim_dry_run_enabled", True)
            ),
            "adaptive_reclaim_candidate": False,
            "adaptive_reclaim_candidate_reason": None,
            "adaptive_reclaim_target_threads": None,
            "adaptive_reclaim_state": "none",
            "adaptive_reclaim_request_id": None,
            "adaptive_reclaim_consumer_worker_id": None,
            "adaptive_reclaim_consumer_session_id": None,
            "adaptive_reclaim_consumer_user_id": None,
            "adaptive_reclaim_consumer_media_item_id": None,
            "adaptive_reclaim_consumer_reason": None,
            "adaptive_reclaim_donor_worker_id": None,
            "adaptive_reclaim_donor_session_id": None,
            "adaptive_reclaim_downshift_replacement_epoch_id": None,
            "adaptive_reclaim_downshift_replacement_worker_id": None,
            "adaptive_reclaim_started_at": None,
            "adaptive_reclaim_switched_at": None,
            "adaptive_reclaim_measured_at": None,
            "adaptive_reclaim_completed_at": None,
            "adaptive_reclaim_failed_reason": None,
            "adaptive_reclaim_released_threads_expected": None,
            "adaptive_reclaim_released_threads_measured": None,
            "adaptive_reclaim_released_cpu_cores_measured": None,
            "adaptive_reclaim_cpu_headroom_before": None,
            "adaptive_reclaim_cpu_headroom_after": None,
            "adaptive_reclaim_route2_cpu_cores_used_before": None,
            "adaptive_reclaim_route2_cpu_cores_used_after": None,
            "adaptive_reclaim_user_cpu_cores_used_before": None,
            "adaptive_reclaim_user_cpu_cores_used_after": None,
            "adaptive_reclaim_host_cpu_used_cores_before": None,
            "adaptive_reclaim_host_cpu_used_cores_after": None,
            "adaptive_reclaim_host_cpu_spare_cores_before": None,
            "adaptive_reclaim_host_cpu_spare_cores_after": None,
            "adaptive_reclaim_route2_headroom_before": None,
            "adaptive_reclaim_route2_headroom_after": None,
            "adaptive_reclaim_memory_pressure_before": None,
            "adaptive_reclaim_memory_pressure_after": None,
            "adaptive_reclaim_external_pressure_before": None,
            "adaptive_reclaim_external_pressure_after": None,
            "adaptive_reclaim_capacity_sufficient_for_consumer": None,
            "adaptive_reclaim_retry_count": 0,
            "adaptive_reclaim_retry_not_before_seconds": None,
            "adaptive_reclaim_retry_blocker": None,
            "adaptive_reclaim_blockers": ["route2_session_or_epoch_missing"],
            "adaptive_reclaim_abort_reason": None,
            "admission_waiting_for_reclaim": False,
            "admission_reclaim_possible": False,
            "admission_reclaim_attempted": False,
            "admission_reclaim_succeeded": False,
            "admission_reclaim_failed_reason": None,
            "admission_capacity_after_reclaim": None,
            "admission_hard_block_reason": None,
        }

    def _adaptive_resupply_default_payload(self) -> dict[str, object]:
        return {
            "adaptive_resupply_enabled": bool(getattr(self.settings, "route2_adaptive_resupply_enabled", False)),
            "adaptive_resupply_dry_run_enabled": bool(
                getattr(self.settings, "route2_adaptive_resupply_dry_run_enabled", True)
            ),
            "adaptive_resupply_needed": False,
            "adaptive_resupply_reason": None,
            "adaptive_resupply_priority": 0,
            "adaptive_resupply_target_threads": None,
            "adaptive_resupply_state": "none",
            "adaptive_resupply_request_id": None,
            "adaptive_resupply_original_reclaim_request_id": None,
            "adaptive_resupply_donor_worker_id": None,
            "adaptive_resupply_replacement_epoch_id": None,
            "adaptive_resupply_replacement_worker_id": None,
            "adaptive_resupply_started_at": None,
            "adaptive_resupply_switched_at": None,
            "adaptive_resupply_measured_at": None,
            "adaptive_resupply_blockers": [],
            "adaptive_resupply_abort_reason": None,
            "adaptive_resupply_stabilization_active": False,
            "adaptive_resupply_stabilization_until": None,
            "adaptive_resupply_stabilization_seconds_remaining": None,
            "adaptive_resupply_stabilization_reason": None,
            "last_resupply_completed_at": None,
            "last_resupply_target_threads": None,
            "resupplied_donor_protection_active": False,
            "priority_reexpand_pending": False,
            "priority_reexpand_reason": None,
            "donor_protection_active": False,
            "donor_health_after_resupply": {},
            "admission_blocked_by_resupply": False,
        }

    def _route2_adaptive_reclaim_payload_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        record: Route2WorkerRecord,
        decision: _Route2ClosedLoopDryRunDecision,
        downshift_payload: dict[str, object],
    ) -> dict[str, object]:
        reclaim_enabled = bool(getattr(self.settings, "route2_adaptive_reclaim_enabled", False))
        dry_run_enabled = bool(getattr(self.settings, "route2_adaptive_reclaim_dry_run_enabled", True))
        browser_session = session.browser_playback
        blockers: list[str] = []
        stabilization_payload = self._route2_resupply_stabilization_payload_locked(browser_session)
        if not dry_run_enabled and not reclaim_enabled:
            blockers.append("adaptive_reclaim_dry_run_disabled")
        if bool(stabilization_payload["adaptive_resupply_stabilization_active"]):
            blockers.append("recently_resupplied_donor_stabilizing")
        if not bool(getattr(self.settings, "route2_adaptive_downshift_dry_run_enabled", True)) and not bool(
            getattr(self.settings, "route2_adaptive_downshift_enabled", False)
        ):
            blockers.append("adaptive_downshift_evaluation_disabled")
        if record.assigned_threads < 9:
            blockers.append("not_boosted_prepare_tier")
        if record.state != "running" or not record.process_exists:
            blockers.append("worker_not_running")
        if browser_session.replacement_epoch_id:
            blockers.append("replacement_already_in_progress")
        if record.non_retryable_error or session.last_error or epoch.last_error:
            blockers.append("provider_source_or_session_error")
        retry_seconds = self._route2_reclaim_retry_seconds_remaining(browser_session)
        if retry_seconds is not None:
            blockers.append("adaptive_reclaim_retry_cooldown_active")
        if self._route2_reclaim_retry_cap_remaining(browser_session) <= 0:
            blockers.append("adaptive_reclaim_retry_cap_exceeded")
        if (
            browser_session.adaptive_reclaim_donor_worker_id
            and browser_session.adaptive_reclaim_donor_worker_id != record.worker_id
            and browser_session.adaptive_reclaim_state in ROUTE2_RECLAIM_ACTIVE_STATES
        ):
            blockers.append("already_reclaim_donor_for_other_transaction")
        if decision.role in {"provider_error", "source_bound", "client_bound", "host_pressure_limited"}:
            blockers.append(f"{decision.role}_blocks_reclaim")
        if decision.prepare_boost_needed or decision.needs_resource:
            blockers.append("donor_still_needs_resource")
        if self._stalled_recovery_needed(session):
            blockers.append("stalled_recovery_needed")
        if self._starvation_risk(session):
            blockers.append("starvation_risk")
        reserve_status = self._route2_bad_condition_reserve_status_locked(session, epoch)
        if bool(reserve_status.get("bad_condition_reserve_required")) and not bool(reserve_status.get("reserve_satisfied")):
            blockers.append("active_bad_condition_reserve_protection")
        (
            _published_end_seconds,
            _effective_playhead_seconds,
            runway_seconds,
            supply_rate_x,
            observation_seconds,
            manifest_complete,
            _refill_in_progress,
        ) = self._route2_runtime_supply_metrics_locked(session, epoch)
        comfortable_runway_seconds = self._route2_closed_loop_comfortable_runway_seconds(record.playback_mode)
        if not manifest_complete and observation_seconds < ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS:
            blockers.append("telemetry_immature")
        if supply_rate_x < ROUTE2_CLOSED_LOOP_DOWNSHIFT_RATE_X:
            blockers.append("supply_below_reclaim_threshold")
        if runway_seconds < comfortable_runway_seconds:
            blockers.append("runway_below_comfortable_target")
        downshift_blockers = list(downshift_payload.get("adaptive_downshift_blockers") or [])
        blockers.extend(str(blocker) for blocker in downshift_blockers)
        target_threads = downshift_payload.get("adaptive_downshift_target_threads")
        if not isinstance(target_threads, int) or target_threads <= 0:
            blockers.append("reclaim_target_threads_unavailable")
        expected_release = (
            max(0, int(record.assigned_threads or 0) - int(target_threads))
            if isinstance(target_threads, int)
            else 0
        )
        if expected_release <= 0:
            blockers.append("no_reclaimable_threads")
        state = browser_session.adaptive_reclaim_state or "none"
        selected_donor = bool(
            browser_session.adaptive_reclaim_donor_worker_id
            and browser_session.adaptive_reclaim_donor_worker_id == record.worker_id
            and state
            in ROUTE2_RECLAIM_ACTIVE_STATES
        )
        candidate = not blockers or selected_donor
        if candidate and state == "none":
            state = "dry_run_candidate"
        return {
            "adaptive_reclaim_enabled": reclaim_enabled,
            "adaptive_reclaim_dry_run_enabled": dry_run_enabled,
            "adaptive_reclaim_candidate": candidate,
            "adaptive_reclaim_candidate_reason": (
                "Selected as transactional reclaim donor."
                if selected_donor
                else "Boosted worker is oversupplied and can be transactionally reclaimed."
                if candidate
                else None
            ),
            "adaptive_reclaim_target_threads": target_threads if isinstance(target_threads, int) else None,
            "adaptive_reclaim_state": state,
            "adaptive_reclaim_request_id": browser_session.adaptive_reclaim_request_id,
            "adaptive_reclaim_consumer_worker_id": browser_session.adaptive_reclaim_consumer_worker_id,
            "adaptive_reclaim_consumer_session_id": browser_session.adaptive_reclaim_consumer_session_id,
            "adaptive_reclaim_consumer_user_id": browser_session.adaptive_reclaim_consumer_user_id,
            "adaptive_reclaim_consumer_media_item_id": browser_session.adaptive_reclaim_consumer_media_item_id,
            "adaptive_reclaim_consumer_reason": browser_session.adaptive_reclaim_consumer_reason,
            "adaptive_reclaim_donor_worker_id": browser_session.adaptive_reclaim_donor_worker_id,
            "adaptive_reclaim_donor_session_id": browser_session.adaptive_reclaim_donor_session_id,
            "adaptive_reclaim_downshift_replacement_epoch_id": (
                browser_session.adaptive_reclaim_downshift_replacement_epoch_id
            ),
            "adaptive_reclaim_downshift_replacement_worker_id": (
                browser_session.adaptive_reclaim_downshift_replacement_worker_id
            ),
            "adaptive_reclaim_started_at": browser_session.adaptive_reclaim_started_at,
            "adaptive_reclaim_switched_at": browser_session.adaptive_reclaim_switched_at,
            "adaptive_reclaim_measured_at": browser_session.adaptive_reclaim_measured_at,
            "adaptive_reclaim_completed_at": browser_session.adaptive_reclaim_completed_at,
            "adaptive_reclaim_failed_reason": browser_session.adaptive_reclaim_failed_reason,
            "adaptive_reclaim_released_threads_expected": (
                browser_session.adaptive_reclaim_released_threads_expected or expected_release or None
            ),
            "adaptive_reclaim_released_threads_measured": browser_session.adaptive_reclaim_released_threads_measured,
            "adaptive_reclaim_released_cpu_cores_measured": (
                browser_session.adaptive_reclaim_released_cpu_cores_measured
            ),
            "adaptive_reclaim_cpu_headroom_before": browser_session.adaptive_reclaim_cpu_headroom_before,
            "adaptive_reclaim_cpu_headroom_after": browser_session.adaptive_reclaim_cpu_headroom_after,
            "adaptive_reclaim_route2_cpu_cores_used_before": (
                browser_session.adaptive_reclaim_route2_cpu_cores_used_before
            ),
            "adaptive_reclaim_route2_cpu_cores_used_after": (
                browser_session.adaptive_reclaim_route2_cpu_cores_used_after
            ),
            "adaptive_reclaim_user_cpu_cores_used_before": (
                browser_session.adaptive_reclaim_user_cpu_cores_used_before
            ),
            "adaptive_reclaim_user_cpu_cores_used_after": (
                browser_session.adaptive_reclaim_user_cpu_cores_used_after
            ),
            "adaptive_reclaim_host_cpu_used_cores_before": (
                browser_session.adaptive_reclaim_host_cpu_used_cores_before
            ),
            "adaptive_reclaim_host_cpu_used_cores_after": (
                browser_session.adaptive_reclaim_host_cpu_used_cores_after
            ),
            "adaptive_reclaim_host_cpu_spare_cores_before": (
                browser_session.adaptive_reclaim_host_cpu_spare_cores_before
            ),
            "adaptive_reclaim_host_cpu_spare_cores_after": (
                browser_session.adaptive_reclaim_host_cpu_spare_cores_after
            ),
            "adaptive_reclaim_route2_headroom_before": browser_session.adaptive_reclaim_route2_headroom_before,
            "adaptive_reclaim_route2_headroom_after": browser_session.adaptive_reclaim_route2_headroom_after,
            "adaptive_reclaim_memory_pressure_before": browser_session.adaptive_reclaim_memory_pressure_before,
            "adaptive_reclaim_memory_pressure_after": browser_session.adaptive_reclaim_memory_pressure_after,
            "adaptive_reclaim_external_pressure_before": browser_session.adaptive_reclaim_external_pressure_before,
            "adaptive_reclaim_external_pressure_after": browser_session.adaptive_reclaim_external_pressure_after,
            "adaptive_reclaim_capacity_sufficient_for_consumer": (
                browser_session.adaptive_reclaim_capacity_sufficient_for_consumer
            ),
            "adaptive_reclaim_retry_count": int(browser_session.adaptive_reclaim_retry_count or 0),
            "adaptive_reclaim_retry_not_before_seconds": (
                round(retry_seconds, 3)
                if retry_seconds is not None
                else None
            ),
            "adaptive_reclaim_retry_blocker": browser_session.adaptive_reclaim_retry_blocker,
            "adaptive_reclaim_blockers": [] if selected_donor else list(dict.fromkeys(blockers)),
            "adaptive_reclaim_abort_reason": browser_session.adaptive_reclaim_abort_reason,
            "admission_waiting_for_reclaim": state in ROUTE2_RECLAIM_ACTIVE_STATES,
            "admission_reclaim_possible": candidate,
            "admission_reclaim_attempted": state not in {"none", "dry_run_candidate"},
            "admission_reclaim_succeeded": state in {"capacity_available", "consumer_admitted_after_reclaim"},
            "admission_reclaim_failed_reason": (
                (browser_session.adaptive_reclaim_failed_reason or browser_session.adaptive_reclaim_abort_reason)
                if state in {"reclaim_aborted", "reclaim_failed", "capacity_insufficient"}
                else None
            ),
            "admission_capacity_after_reclaim": browser_session.adaptive_reclaim_cpu_headroom_after,
            "admission_hard_block_reason": None,
        }

    def _route2_adaptive_resupply_payload_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        record: Route2WorkerRecord,
    ) -> dict[str, object]:
        payload = self._adaptive_resupply_default_payload()
        browser_session = session.browser_playback
        payload.update(self._route2_resupply_stabilization_payload_locked(browser_session))
        if (
            browser_session.active_epoch_id != epoch.epoch_id
            and epoch.replacement_reason != "adaptive_resupply_boost"
        ):
            return payload
        reclaimed_or_downshifted = bool(
            browser_session.adaptive_reclaim_request_id
            and browser_session.adaptive_reclaim_state
            in {"capacity_available", "capacity_insufficient", "consumer_admitted_after_reclaim"}
        )
        if not reclaimed_or_downshifted:
            return payload
        (
            _published_end_seconds,
            _effective_playhead_seconds,
            runway_seconds,
            supply_rate_x,
            observation_seconds,
            manifest_complete,
            _refill_in_progress,
        ) = self._route2_runtime_supply_metrics_locked(session, epoch)
        reserve_status = self._route2_bad_condition_reserve_status_locked(session, epoch)
        low_runway = runway_seconds < self._route2_closed_loop_required_runway_seconds(record.playback_mode)
        stalled_recovery_needed = self._stalled_recovery_needed(session)
        starvation_risk = self._starvation_risk(session)
        reserve_deficit = bool(reserve_status.get("bad_condition_reserve_required")) and not bool(
            reserve_status.get("reserve_satisfied")
        )
        resupply_needed = bool(
            observation_seconds >= ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS
            and (
                supply_rate_x < ROUTE2_CLOSED_LOOP_HEALTH_FLOOR_RATE_X
                or low_runway
                or stalled_recovery_needed
                or starvation_risk
                or reserve_deficit
            )
        )
        replacement = (
            browser_session.epochs.get(browser_session.replacement_epoch_id)
            if browser_session.replacement_epoch_id
            else None
        )
        resupply_replacement_active = bool(
            replacement is not None and replacement.replacement_reason == "adaptive_resupply_boost"
        )
        if not resupply_needed:
            if resupply_replacement_active:
                payload.update(
                    {
                        "adaptive_resupply_needed": True,
                        "adaptive_resupply_reason": browser_session.adaptive_resupply_reason,
                        "adaptive_resupply_priority": 100,
                        "adaptive_resupply_target_threads": replacement.adaptive_resupply_target_threads,
                        "adaptive_resupply_state": (
                            "boost_replacement_ready"
                            if self._route2_downshift_replacement_ready_locked(session, replacement)
                            else "boost_replacement_warming"
                            if replacement.active_worker_id or (replacement.process and replacement.process.poll() is None)
                            else "boost_replacement_starting"
                        ),
                        "adaptive_resupply_request_id": replacement.adaptive_resupply_request_id,
                        "adaptive_resupply_original_reclaim_request_id": (
                            replacement.adaptive_resupply_original_reclaim_request_id
                        ),
                        "adaptive_resupply_donor_worker_id": browser_session.adaptive_resupply_donor_worker_id,
                        "adaptive_resupply_replacement_epoch_id": replacement.epoch_id,
                        "adaptive_resupply_replacement_worker_id": replacement.active_worker_id,
                        "adaptive_resupply_started_at": replacement.adaptive_resupply_started_at,
                        "adaptive_resupply_switched_at": replacement.adaptive_resupply_switched_at,
                        "adaptive_resupply_measured_at": browser_session.adaptive_resupply_measured_at,
                        "adaptive_resupply_blockers": list(browser_session.adaptive_resupply_blockers),
                        "adaptive_resupply_abort_reason": replacement.adaptive_resupply_abort_reason,
                        **self._route2_resupply_stabilization_payload_locked(browser_session),
                        "priority_reexpand_pending": True,
                        "priority_reexpand_reason": browser_session.priority_reexpand_reason,
                        "donor_protection_active": True,
                        "donor_health_after_resupply": dict(browser_session.donor_health_after_resupply),
                        "admission_blocked_by_resupply": True,
                    }
                )
                return payload
            browser_session.adaptive_resupply_needed = False
            browser_session.priority_reexpand_pending = False
            browser_session.donor_protection_active = False
            browser_session.priority_reexpand_reason = None
            browser_session.admission_blocked_by_resupply = False
            if browser_session.adaptive_resupply_request_id and browser_session.adaptive_resupply_state in {
                "switched",
                "measuring_health",
                "donor_safe",
            }:
                browser_session.adaptive_resupply_state = "donor_safe"
                payload.update(
                    {
                        "adaptive_resupply_state": "donor_safe",
                        "adaptive_resupply_request_id": browser_session.adaptive_resupply_request_id,
                        "adaptive_resupply_original_reclaim_request_id": (
                            browser_session.adaptive_resupply_original_reclaim_request_id
                        ),
                        "adaptive_resupply_donor_worker_id": browser_session.adaptive_resupply_donor_worker_id,
                        "adaptive_resupply_replacement_epoch_id": browser_session.adaptive_resupply_replacement_epoch_id,
                        "adaptive_resupply_replacement_worker_id": browser_session.adaptive_resupply_replacement_worker_id,
                        "adaptive_resupply_started_at": browser_session.adaptive_resupply_started_at,
                        "adaptive_resupply_switched_at": browser_session.adaptive_resupply_switched_at,
                        "adaptive_resupply_measured_at": browser_session.adaptive_resupply_measured_at,
                        "donor_health_after_resupply": dict(browser_session.donor_health_after_resupply),
                        **self._route2_resupply_stabilization_payload_locked(browser_session),
                    }
                )
            return payload
        target_threads = self._route2_next_runtime_rebalance_target_threads(int(record.assigned_threads or 0))
        if target_threads >= 12 and not bool(getattr(self.settings, "route2_adaptive_thread_control_strict_12_enabled", False)):
            target_threads = 9
        target_threads = min(
            int(getattr(self.settings, "route2_adaptive_max_worker_threads", target_threads) or target_threads),
            target_threads,
        )
        blockers: list[str] = []
        resource_snapshot = self._latest_route2_resource_snapshot_locked()
        if not manifest_complete and observation_seconds < ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS:
            blockers.append("telemetry_immature")
        if record.non_retryable_error or session.last_error or epoch.last_error:
            blockers.append("provider_source_or_session_error")
        if replacement is not None and not resupply_replacement_active:
            blockers.append("replacement_already_in_progress")
        if target_threads <= int(record.assigned_threads or 0):
            blockers.append("resupply_target_not_above_current")
        decision = self._evaluate_route2_closed_loop_dry_run_locked(session, epoch, record)
        if decision.role in {"provider_error", "source_bound", "client_bound", "host_pressure_limited"}:
            blockers.append(f"{decision.role}_blocks_resupply")
        if resource_snapshot is None or resource_snapshot.sample_stale or not resource_snapshot.sample_mature:
            blockers.append("resupply_resource_snapshot_unavailable")
        elif resource_snapshot.external_ffmpeg_process_count > 0:
            blockers.append("external_ffmpeg_blocks_resupply")
        elif resource_snapshot.external_pressure_level == "high":
            blockers.append("external_pressure_blocks_resupply")
        if (
            resource_snapshot is not None
            and resource_snapshot.total_memory_bytes
            and resource_snapshot.route2_memory_bytes_total is not None
            and (resource_snapshot.route2_memory_bytes_total / resource_snapshot.total_memory_bytes) >= 0.90
        ):
            blockers.append("ram_pressure_blocks_resupply")
        headroom_available = self._route2_downshift_transition_headroom_locked(user_id=record.user_id)
        if bool(getattr(self.settings, "route2_adaptive_resupply_enabled", False)) and headroom_available < target_threads:
            blockers.append("resupply_transition_headroom_unavailable")
        state = "priority_reexpand_pending"
        if resupply_replacement_active:
            state = (
                "boost_replacement_ready"
                if self._route2_downshift_replacement_ready_locked(session, replacement)  # type: ignore[arg-type]
                else "boost_replacement_warming"
                if replacement.active_worker_id or (replacement.process and replacement.process.poll() is None)  # type: ignore[union-attr]
                else "boost_replacement_starting"
            )
        elif bool(getattr(self.settings, "route2_adaptive_resupply_enabled", False)) and not blockers:
            state = "priority_reexpand_pending"
        elif not bool(getattr(self.settings, "route2_adaptive_resupply_enabled", False)):
            state = "dry_run_needed"
        browser_session.adaptive_resupply_needed = True
        browser_session.adaptive_resupply_reason = (
            "previously_reclaimed_donor_below_health_floor"
            if supply_rate_x < ROUTE2_CLOSED_LOOP_HEALTH_FLOOR_RATE_X
            else "previously_reclaimed_donor_stalled_recovery_needed"
            if stalled_recovery_needed
            else "previously_reclaimed_donor_starvation_risk"
            if starvation_risk
            else "previously_reclaimed_donor_full_reserve_deficit"
            if reserve_deficit
            else "previously_reclaimed_donor_needs_protected_runway"
        )
        browser_session.adaptive_resupply_target_threads = target_threads
        browser_session.adaptive_resupply_state = state
        browser_session.adaptive_resupply_original_reclaim_request_id = browser_session.adaptive_reclaim_request_id
        browser_session.adaptive_resupply_donor_worker_id = record.worker_id
        browser_session.adaptive_resupply_blockers = list(dict.fromkeys(blockers))
        browser_session.priority_reexpand_pending = True
        browser_session.priority_reexpand_reason = browser_session.adaptive_resupply_reason
        browser_session.donor_protection_active = True
        browser_session.admission_blocked_by_resupply = True
        payload.update(
            {
                "adaptive_resupply_needed": True,
                "adaptive_resupply_reason": browser_session.adaptive_resupply_reason,
                "adaptive_resupply_priority": 100,
                "adaptive_resupply_target_threads": target_threads,
                "adaptive_resupply_state": browser_session.adaptive_resupply_state,
                "adaptive_resupply_request_id": browser_session.adaptive_resupply_request_id,
                "adaptive_resupply_original_reclaim_request_id": (
                    browser_session.adaptive_resupply_original_reclaim_request_id
                ),
                "adaptive_resupply_donor_worker_id": browser_session.adaptive_resupply_donor_worker_id,
                "adaptive_resupply_replacement_epoch_id": (
                    replacement.epoch_id if resupply_replacement_active and replacement is not None else None
                ),
                "adaptive_resupply_replacement_worker_id": (
                    replacement.active_worker_id if resupply_replacement_active and replacement is not None else None
                ),
                "adaptive_resupply_started_at": (
                    replacement.adaptive_resupply_started_at
                    if resupply_replacement_active and replacement is not None
                    else browser_session.adaptive_resupply_started_at
                ),
                "adaptive_resupply_switched_at": browser_session.adaptive_resupply_switched_at,
                "adaptive_resupply_measured_at": browser_session.adaptive_resupply_measured_at,
                "adaptive_resupply_blockers": list(browser_session.adaptive_resupply_blockers),
                "adaptive_resupply_abort_reason": browser_session.adaptive_resupply_abort_reason,
                **self._route2_resupply_stabilization_payload_locked(browser_session),
                "priority_reexpand_pending": True,
                "priority_reexpand_reason": browser_session.priority_reexpand_reason,
                "donor_protection_active": True,
                "donor_health_after_resupply": dict(browser_session.donor_health_after_resupply),
                "admission_blocked_by_resupply": True,
            }
        )
        return payload

    def _apply_route2_reclaim_payload_to_record(
        self,
        record: Route2WorkerRecord,
        payload: dict[str, object],
    ) -> None:
        for key, value in payload.items():
            if hasattr(record, key):
                setattr(record, key, value)

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
                "audio": _ffmpeg_audio_map(session.browser_playback.selected_audio_stream_index),
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
            "stream_selection": contract["stream_selection"],
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

    def _route2_resupply_protections_locked(self) -> list[dict[str, object]]:
        protections: list[dict[str, object]] = []
        if not bool(getattr(self.settings, "route2_adaptive_resupply_dry_run_enabled", True)) and not bool(
            getattr(self.settings, "route2_adaptive_resupply_enabled", False)
        ):
            return protections
        for record in self._route2_workers.values():
            if record.state not in {"running", "stopping"}:
                continue
            session = self._sessions.get(record.session_id)
            if session is None or session.browser_playback.engine_mode != "route2":
                continue
            epoch = session.browser_playback.epochs.get(record.epoch_id)
            if epoch is None:
                continue
            payload = self._route2_adaptive_resupply_payload_locked(session, epoch, record)
            self._apply_route2_reclaim_payload_to_record(record, payload)
            if not bool(payload.get("priority_reexpand_pending")):
                continue
            protections.append(
                {
                    "worker_id": record.worker_id,
                    "session_id": session.session_id,
                    "reason": payload.get("adaptive_resupply_reason"),
                    "target_threads": payload.get("adaptive_resupply_target_threads"),
                    "state": payload.get("adaptive_resupply_state"),
                    "blockers": payload.get("adaptive_resupply_blockers") or [],
                }
            )
        return protections

    def _route2_reclaim_candidate_plans_locked(self) -> list[dict[str, object]]:
        plans: list[dict[str, object]] = []
        for record in self._route2_workers.values():
            if record.state != "running":
                continue
            session = self._sessions.get(record.session_id)
            if session is None or session.browser_playback.engine_mode != "route2":
                continue
            epoch = session.browser_playback.epochs.get(record.epoch_id)
            if epoch is None:
                continue
            decision = self._evaluate_route2_closed_loop_dry_run_locked(session, epoch, record)
            downshift_payload = self._route2_adaptive_downshift_payload_locked(
                session,
                epoch,
                record,
                decision,
            )
            reclaim_payload = self._route2_adaptive_reclaim_payload_locked(
                session,
                epoch,
                record,
                decision,
                downshift_payload,
            )
            self._apply_route2_downshift_payload_to_record(record, downshift_payload)
            self._apply_route2_reclaim_payload_to_record(record, reclaim_payload)
            if not bool(reclaim_payload.get("adaptive_reclaim_candidate")):
                continue
            target_threads = reclaim_payload.get("adaptive_reclaim_target_threads")
            expected_release = reclaim_payload.get("adaptive_reclaim_released_threads_expected")
            if not isinstance(target_threads, int) or target_threads <= 0:
                continue
            if int(expected_release or 0) <= 0:
                continue
            plans.append(
                {
                    "session": session,
                    "epoch": epoch,
                    "record": record,
                    "decision": decision,
                    "downshift_payload": downshift_payload,
                    "reclaim_payload": reclaim_payload,
                    "target_threads": target_threads,
                    "expected_release": int(expected_release or 0),
                }
            )
        return sorted(
            plans,
            key=lambda plan: (
                -int(plan["expected_release"]),
                str(plan["record"].worker_id),  # type: ignore[union-attr]
            ),
        )

    def _route2_start_reclaim_for_admission_locked(
        self,
        *,
        incoming_user_id: int,
        incoming_user_role: str,
        source_kind: str,
        incoming_media_item_id: int | None = None,
        incoming_consumer_session_id: str | None = None,
        incoming_consumer_reason: str = "admission_capacity_shortage",
    ) -> dict[str, object] | None:
        del incoming_user_role, source_kind
        reclaim_enabled = bool(getattr(self.settings, "route2_adaptive_reclaim_enabled", False))
        dry_run_enabled = bool(getattr(self.settings, "route2_adaptive_reclaim_dry_run_enabled", True))
        plans = self._route2_reclaim_candidate_plans_locked()
        if not plans:
            resupplied_capacity_blocker = self._route2_resupplied_reclaim_capacity_blocker_locked(
                incoming_user_id=incoming_user_id,
                incoming_media_item_id=incoming_media_item_id,
            )
            if resupplied_capacity_blocker is not None:
                return resupplied_capacity_blocker
            retry_blocked_session = next(
                (
                    candidate.browser_playback
                    for candidate in self._sessions.values()
                    if candidate.browser_playback.engine_mode == "route2"
                    and self._route2_reclaim_retry_seconds_remaining(candidate.browser_playback) is not None
                ),
                None,
            )
            if retry_blocked_session is not None:
                retry_seconds = self._route2_reclaim_retry_seconds_remaining(retry_blocked_session)
                return {
                    "admission_waiting_for_reclaim": True,
                    "admission_reclaim_possible": False,
                    "admission_reclaim_attempted": True,
                    "admission_reclaim_succeeded": False,
                    "admission_reclaim_failed_reason": "adaptive_reclaim_retry_cooldown_active",
                    "admission_capacity_after_reclaim": None,
                    "admission_hard_block_reason": None,
                    "adaptive_reclaim_enabled": reclaim_enabled,
                    "adaptive_reclaim_dry_run_enabled": dry_run_enabled,
                    "adaptive_reclaim_state": "consumer_waiting_for_reclaim",
                    "adaptive_reclaim_retry_count": int(retry_blocked_session.adaptive_reclaim_retry_count or 0),
                    "adaptive_reclaim_retry_not_before_seconds": (
                        round(retry_seconds, 3) if retry_seconds is not None else None
                    ),
                    "adaptive_reclaim_retry_blocker": "adaptive_reclaim_retry_cooldown_active",
                }
            if reclaim_enabled and bool(getattr(self.settings, "route2_adaptive_downshift_enabled", False)):
                pending = self._route2_create_pending_reclaim_request_locked(
                    incoming_user_id=incoming_user_id,
                    incoming_media_item_id=incoming_media_item_id,
                    incoming_consumer_session_id=incoming_consumer_session_id,
                    incoming_consumer_reason=incoming_consumer_reason,
                )
                return {
                    "admission_waiting_for_reclaim": True,
                    "admission_reclaim_possible": False,
                    "admission_reclaim_attempted": True,
                    "admission_reclaim_succeeded": False,
                    "admission_reclaim_failed_reason": "reclaim_candidate_not_ready",
                    "admission_capacity_after_reclaim": None,
                    "admission_hard_block_reason": None,
                    "adaptive_reclaim_enabled": reclaim_enabled,
                    "adaptive_reclaim_dry_run_enabled": dry_run_enabled,
                    "adaptive_reclaim_state": "consumer_waiting_for_reclaim",
                    "adaptive_reclaim_request_id": pending["adaptive_reclaim_request_id"],
                    "adaptive_reclaim_consumer_worker_id": pending["adaptive_reclaim_consumer_worker_id"],
                    "adaptive_reclaim_consumer_session_id": pending["adaptive_reclaim_consumer_session_id"],
                    "adaptive_reclaim_consumer_user_id": pending["adaptive_reclaim_consumer_user_id"],
                    "adaptive_reclaim_consumer_media_item_id": pending["adaptive_reclaim_consumer_media_item_id"],
                    "adaptive_reclaim_consumer_reason": pending["adaptive_reclaim_consumer_reason"],
                    "adaptive_reclaim_blockers": ["reclaim_candidate_not_ready"],
                }
            return {
                "admission_waiting_for_reclaim": False,
                "admission_reclaim_possible": False,
                "admission_reclaim_attempted": False,
                "admission_reclaim_succeeded": False,
                "admission_reclaim_failed_reason": "no_safe_reclaim_candidate",
                "admission_capacity_after_reclaim": None,
                "admission_hard_block_reason": None,
                "adaptive_reclaim_enabled": reclaim_enabled,
                "adaptive_reclaim_dry_run_enabled": dry_run_enabled,
            }
        plan = plans[0]
        session = plan["session"]
        epoch = plan["epoch"]
        record = plan["record"]
        target_threads = int(plan["target_threads"])
        expected_release = int(plan["expected_release"])
        before_measurement = self._route2_reclaim_capacity_measurement_locked(user_id=session.user_id)  # type: ignore[union-attr]
        before_headroom = int(before_measurement["route2_headroom"])
        detail: dict[str, object] = {
            "admission_waiting_for_reclaim": False,
            "admission_reclaim_possible": True,
            "admission_reclaim_attempted": False,
            "admission_reclaim_succeeded": False,
            "admission_reclaim_failed_reason": None,
            "admission_capacity_after_reclaim": None,
            "admission_hard_block_reason": None,
            "adaptive_reclaim_enabled": reclaim_enabled,
            "adaptive_reclaim_dry_run_enabled": dry_run_enabled,
            "adaptive_reclaim_candidate": True,
            "adaptive_reclaim_consumer_session_id": incoming_consumer_session_id,
            "adaptive_reclaim_consumer_user_id": int(incoming_user_id),
            "adaptive_reclaim_consumer_media_item_id": incoming_media_item_id,
            "adaptive_reclaim_consumer_reason": incoming_consumer_reason,
            "adaptive_reclaim_donor_worker_id": record.worker_id,  # type: ignore[union-attr]
            "adaptive_reclaim_donor_session_id": session.session_id,  # type: ignore[union-attr]
            "adaptive_reclaim_target_threads": target_threads,
            "adaptive_reclaim_released_threads_expected": expected_release,
            "adaptive_reclaim_cpu_headroom_before": before_headroom,
            "adaptive_reclaim_route2_cpu_cores_used_before": before_measurement["route2_cpu_cores_used"],
            "adaptive_reclaim_user_cpu_cores_used_before": before_measurement["user_cpu_cores_used"],
            "adaptive_reclaim_host_cpu_used_cores_before": before_measurement["host_cpu_used_cores"],
            "adaptive_reclaim_host_cpu_spare_cores_before": before_measurement["host_cpu_spare_cores"],
            "adaptive_reclaim_route2_headroom_before": before_measurement["route2_headroom"],
            "adaptive_reclaim_memory_pressure_before": before_measurement["memory_pressure"],
            "adaptive_reclaim_external_pressure_before": before_measurement["external_pressure"],
        }
        if not reclaim_enabled:
            detail["adaptive_reclaim_state"] = "dry_run_candidate"
            detail["admission_reclaim_failed_reason"] = "adaptive_reclaim_real_disabled"
            return detail
        if not bool(getattr(self.settings, "route2_adaptive_downshift_enabled", False)):
            detail["adaptive_reclaim_state"] = "reclaim_failed"
            detail["admission_reclaim_failed_reason"] = "adaptive_downshift_real_disabled"
            return detail
        browser_session = session.browser_playback  # type: ignore[union-attr]
        if browser_session.replacement_epoch_id:
            detail["adaptive_reclaim_state"] = "reclaim_failed"
            detail["admission_reclaim_failed_reason"] = "replacement_already_in_progress"
            return detail
        retry_seconds = self._route2_reclaim_retry_seconds_remaining(browser_session)
        if retry_seconds is not None:
            browser_session.adaptive_reclaim_retry_blocker = "adaptive_reclaim_retry_cooldown_active"
            detail["adaptive_reclaim_state"] = "consumer_waiting_for_reclaim"
            detail["admission_waiting_for_reclaim"] = True
            detail["admission_reclaim_attempted"] = True
            detail["admission_reclaim_failed_reason"] = browser_session.adaptive_reclaim_retry_blocker
            detail["adaptive_reclaim_retry_count"] = int(browser_session.adaptive_reclaim_retry_count or 0)
            detail["adaptive_reclaim_retry_not_before_seconds"] = round(retry_seconds, 3)
            detail["adaptive_reclaim_retry_blocker"] = browser_session.adaptive_reclaim_retry_blocker
            return detail
        if self._route2_reclaim_retry_cap_remaining(browser_session) <= 0:
            browser_session.adaptive_reclaim_retry_blocker = "adaptive_reclaim_retry_cap_exceeded"
            detail["adaptive_reclaim_state"] = "reclaim_failed"
            detail["admission_reclaim_failed_reason"] = browser_session.adaptive_reclaim_retry_blocker
            detail["adaptive_reclaim_retry_count"] = int(browser_session.adaptive_reclaim_retry_count or 0)
            detail["adaptive_reclaim_retry_blocker"] = browser_session.adaptive_reclaim_retry_blocker
            return detail
        pending = self._route2_pending_reclaim_request_locked()
        request_id = str(pending.get("adaptive_reclaim_request_id")) if pending else f"reclaim-{uuid.uuid4().hex}"
        consumer_worker_id = (
            str(pending.get("adaptive_reclaim_consumer_worker_id"))
            if pending
            else f"admission-user-{int(incoming_user_id)}"
        )
        consumer_session_id = (
            str(pending["adaptive_reclaim_consumer_session_id"])
            if pending and pending.get("adaptive_reclaim_consumer_session_id") is not None
            else incoming_consumer_session_id
        )
        consumer_user_id = (
            int(pending["adaptive_reclaim_consumer_user_id"])
            if pending and pending.get("adaptive_reclaim_consumer_user_id") is not None
            else int(incoming_user_id)
        )
        consumer_media_item_id = (
            int(pending["adaptive_reclaim_consumer_media_item_id"])
            if pending and pending.get("adaptive_reclaim_consumer_media_item_id") is not None
            else incoming_media_item_id
        )
        consumer_reason = (
            str(pending.get("adaptive_reclaim_consumer_reason") or incoming_consumer_reason)
            if pending
            else incoming_consumer_reason
        )
        now = utcnow_iso()
        browser_session.adaptive_reclaim_request_id = request_id
        browser_session.adaptive_reclaim_consumer_worker_id = consumer_worker_id
        browser_session.adaptive_reclaim_consumer_session_id = consumer_session_id
        browser_session.adaptive_reclaim_consumer_user_id = consumer_user_id
        browser_session.adaptive_reclaim_consumer_media_item_id = consumer_media_item_id
        browser_session.adaptive_reclaim_consumer_reason = consumer_reason
        browser_session.adaptive_reclaim_donor_worker_id = record.worker_id  # type: ignore[union-attr]
        browser_session.adaptive_reclaim_donor_session_id = session.session_id  # type: ignore[union-attr]
        browser_session.adaptive_reclaim_downshift_replacement_epoch_id = None
        browser_session.adaptive_reclaim_downshift_replacement_worker_id = None
        browser_session.adaptive_reclaim_started_at = now
        browser_session.adaptive_reclaim_switched_at = None
        browser_session.adaptive_reclaim_measured_at = None
        browser_session.adaptive_reclaim_completed_at = None
        browser_session.adaptive_reclaim_failed_reason = None
        browser_session.adaptive_reclaim_released_threads_expected = expected_release
        browser_session.adaptive_reclaim_released_threads_measured = None
        browser_session.adaptive_reclaim_released_cpu_cores_measured = None
        browser_session.adaptive_reclaim_cpu_headroom_before = before_headroom
        browser_session.adaptive_reclaim_cpu_headroom_after = None
        browser_session.adaptive_reclaim_route2_cpu_cores_used_before = before_measurement["route2_cpu_cores_used"]  # type: ignore[assignment]
        browser_session.adaptive_reclaim_route2_cpu_cores_used_after = None
        browser_session.adaptive_reclaim_user_cpu_cores_used_before = before_measurement["user_cpu_cores_used"]  # type: ignore[assignment]
        browser_session.adaptive_reclaim_user_cpu_cores_used_after = None
        browser_session.adaptive_reclaim_host_cpu_used_cores_before = before_measurement["host_cpu_used_cores"]  # type: ignore[assignment]
        browser_session.adaptive_reclaim_host_cpu_used_cores_after = None
        browser_session.adaptive_reclaim_host_cpu_spare_cores_before = before_measurement["host_cpu_spare_cores"]  # type: ignore[assignment]
        browser_session.adaptive_reclaim_host_cpu_spare_cores_after = None
        browser_session.adaptive_reclaim_route2_headroom_before = before_measurement["route2_headroom"]  # type: ignore[assignment]
        browser_session.adaptive_reclaim_route2_headroom_after = None
        browser_session.adaptive_reclaim_memory_pressure_before = before_measurement["memory_pressure"]  # type: ignore[assignment]
        browser_session.adaptive_reclaim_memory_pressure_after = None
        browser_session.adaptive_reclaim_external_pressure_before = before_measurement["external_pressure"]  # type: ignore[assignment]
        browser_session.adaptive_reclaim_external_pressure_after = None
        browser_session.adaptive_reclaim_capacity_sufficient_for_consumer = None
        browser_session.adaptive_reclaim_state = "donor_downshift_starting"
        browser_session.adaptive_reclaim_blockers = []
        browser_session.adaptive_reclaim_abort_reason = None
        browser_session.adaptive_reclaim_retry_blocker = None
        replacement = self._create_route2_downshift_replacement_epoch_locked(
            session,  # type: ignore[arg-type]
            epoch,  # type: ignore[arg-type]
            target_threads=target_threads,
            reclaim_request_id=request_id,
            reclaim_consumer_session_id=consumer_session_id,
            reclaim_consumer_user_id=consumer_user_id,
            reclaim_consumer_media_item_id=consumer_media_item_id,
            reclaim_consumer_reason=consumer_reason,
        )
        if replacement is None:
            browser_session.adaptive_reclaim_state = "reclaim_failed"
            browser_session.adaptive_reclaim_failed_reason = (
                browser_session.adaptive_downshift_retry_blocker or "reclaim_downshift_start_failed"
            )
            browser_session.adaptive_reclaim_abort_reason = browser_session.adaptive_reclaim_failed_reason
            browser_session.adaptive_reclaim_completed_at = utcnow_iso()
            browser_session.adaptive_reclaim_retry_count += 1
            browser_session.adaptive_reclaim_retry_not_before_ts = (
                time.time() + ROUTE2_ADAPTIVE_RECLAIM_RETRY_BACKOFF_SECONDS
            )
            browser_session.adaptive_reclaim_retry_blocker = "adaptive_reclaim_retry_cooldown_active"
            browser_session.adaptive_reclaim_blockers = [browser_session.adaptive_reclaim_failed_reason]
            detail["adaptive_reclaim_state"] = browser_session.adaptive_reclaim_state
            detail["admission_reclaim_failed_reason"] = browser_session.adaptive_reclaim_failed_reason
            return detail
        browser_session.adaptive_reclaim_downshift_replacement_epoch_id = replacement.epoch_id
        browser_session.adaptive_reclaim_downshift_replacement_worker_id = replacement.active_worker_id
        browser_session.adaptive_reclaim_state = "donor_downshift_warming"
        if pending is not None:
            self._route2_pending_reclaim_request = None
        detail.update(
            {
                "admission_waiting_for_reclaim": True,
                "admission_reclaim_attempted": True,
                "adaptive_reclaim_request_id": request_id,
                "adaptive_reclaim_consumer_worker_id": consumer_worker_id,
                "adaptive_reclaim_consumer_session_id": consumer_session_id,
                "adaptive_reclaim_consumer_user_id": consumer_user_id,
                "adaptive_reclaim_consumer_media_item_id": consumer_media_item_id,
                "adaptive_reclaim_consumer_reason": consumer_reason,
                "adaptive_reclaim_state": browser_session.adaptive_reclaim_state,
                "adaptive_reclaim_started_at": now,
                "adaptive_reclaim_downshift_replacement_epoch_id": replacement.epoch_id,
                "adaptive_reclaim_downshift_replacement_worker_id": replacement.active_worker_id,
            }
        )
        self._ensure_route2_epoch_workers_locked(session)  # type: ignore[arg-type]
        self._dispatch_waiting_route2_workers_locked()
        browser_session.adaptive_reclaim_downshift_replacement_worker_id = replacement.active_worker_id
        detail["adaptive_reclaim_downshift_replacement_worker_id"] = replacement.active_worker_id
        return detail

    def _raise_if_route2_admission_denied_locked(
        self,
        *,
        incoming_user_id: int,
        incoming_user_role: str,
        source_kind: str,
        incoming_media_item_id: int | None = None,
        incoming_consumer_session_id: str | None = None,
        incoming_consumer_reason: str = "admission_capacity_shortage",
    ) -> None:
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

        resupply_protections = self._route2_resupply_protections_locked()
        if resupply_protections:
            raise PlaybackAdmissionError(
                self._build_server_max_capacity_detail_locked(
                    reason_code="adaptive_resupply_priority_reexpand_pending",
                    message="An active playback needs resources back before new playback can start.",
                    active_user_count_after_admission=active_user_count_after_admission,
                    admission_min_threads=admission_min_threads,
                    reclaim_detail={
                        "admission_hard_block_reason": "adaptive_resupply_priority_reexpand_pending",
                        "adaptive_resupply_needed": True,
                        "priority_reexpand_pending": True,
                        "admission_blocked_by_resupply": True,
                        "adaptive_resupply_protections": resupply_protections,
                    },
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
            reclaim_detail = self._route2_start_reclaim_for_admission_locked(
                incoming_user_id=incoming_user_id,
                incoming_user_role=incoming_user_role,
                source_kind=source_kind,
                incoming_media_item_id=incoming_media_item_id,
                incoming_consumer_session_id=incoming_consumer_session_id,
                incoming_consumer_reason=incoming_consumer_reason,
            )
            reason_code = (
                "waiting_for_reclaim"
                if reclaim_detail and bool(reclaim_detail.get("admission_waiting_for_reclaim"))
                else "no_spare_protected_worker_capacity"
            )
            message = (
                "Capacity reclaim is in progress. Please retry after the active playback has safely downshifted."
                if reason_code == "waiting_for_reclaim"
                else None
            )
            raise PlaybackAdmissionError(
                self._build_server_max_capacity_detail_locked(
                    reason_code=reason_code,
                    message=message,
                    active_user_count_after_admission=active_user_count_after_admission,
                    available_reserved_threads=available_reserved_threads,
                    admission_min_threads=admission_min_threads,
                    reclaim_detail=reclaim_detail,
                )
            )

        user_remaining_reserved_threads = per_user_budget_after_admission - reserved_incoming_user_threads
        if user_remaining_reserved_threads < admission_min_threads:
            reclaim_detail = self._route2_start_reclaim_for_admission_locked(
                incoming_user_id=incoming_user_id,
                incoming_user_role=incoming_user_role,
                source_kind=source_kind,
                incoming_media_item_id=incoming_media_item_id,
                incoming_consumer_session_id=incoming_consumer_session_id,
                incoming_consumer_reason=incoming_consumer_reason,
            )
            reason_code = (
                "waiting_for_reclaim"
                if reclaim_detail and bool(reclaim_detail.get("admission_waiting_for_reclaim"))
                else "user_budget_protected_capacity_exhausted"
            )
            message = (
                "Capacity reclaim is in progress. Please retry after measured capacity is available."
                if reason_code == "waiting_for_reclaim"
                else None
            )
            raise PlaybackAdmissionError(
                self._build_server_max_capacity_detail_locked(
                    reason_code=reason_code,
                    message=message,
                    active_user_count_after_admission=active_user_count_after_admission,
                    available_reserved_threads=user_remaining_reserved_threads,
                    admission_min_threads=admission_min_threads,
                    reclaim_detail=reclaim_detail,
                )
            )

        self._mark_route2_reclaim_consumer_admitted_if_matching_locked(
            incoming_user_id=incoming_user_id,
            incoming_media_item_id=incoming_media_item_id,
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

        if record.source_kind == "cloud":
            cloud_prepare_enabled = (
                not bool(getattr(self.settings, "route2_adaptive_thread_control_local_only", True))
                and bool(getattr(self.settings, "route2_adaptive_thread_control_cloud_enabled", False))
            )
            if not cloud_prepare_enabled:
                blockers.append("cloud_adaptive_disabled")
                reason_parts.append("cloud real adaptive initial spawn is disabled")
        elif record.source_kind != "local":
            blockers.append("unsupported_source_kind")
            reason_parts.append("unsupported source kind for real adaptive initial spawn")
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
        real_9_prepare_enabled = bool(
            getattr(self.settings, "route2_adaptive_thread_control_real_9_prepare_enabled", False)
        )
        strict_12_prepare_enabled = bool(
            getattr(self.settings, "route2_adaptive_thread_control_strict_12_enabled", False)
        )
        prepare_boost_target = max(first_tier_target, 9)
        strict_prepare_target = max(prepare_boost_target, 12)
        adaptive_ceiling = min(
            max(0, int(self.settings.route2_adaptive_max_worker_threads)),
            max(0, int(available_total_threads)),
            max(0, int(user_remaining_threads)),
            max(0, int(route2_cpu_upbound_cores)),
        )
        if strict_12_prepare_enabled and real_9_prepare_enabled and adaptive_ceiling >= strict_prepare_target:
            dry_run_target = strict_prepare_target
        elif real_9_prepare_enabled and adaptive_ceiling >= prepare_boost_target:
            dry_run_target = prepare_boost_target
        else:
            dry_run_target = min(first_tier_target, adaptive_ceiling)
        if dry_run_target < int(self.settings.route2_min_worker_threads):
            blockers.append("below_min_worker_threads")
            reason_parts.append("adaptive dry-run ceiling is below route2_min_worker_threads")
        if dry_run_target < first_tier_target:
            reason_parts.append("adaptive max or CPU budget caps the first-tier target below 6")
        if real_9_prepare_enabled and dry_run_target < prepare_boost_target:
            reason_parts.append("adaptive max or CPU budget caps the real 9-thread prepare boost below 9")
        if strict_12_prepare_enabled and dry_run_target < strict_prepare_target:
            reason_parts.append("adaptive max or CPU budget caps the strict 12-thread prepare boost below 12")
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
        if real_9_prepare_enabled and dry_run_target < prepare_boost_target:
            capped_note += " Adaptive max or CPU budget caps the real 9-thread prepare boost below 9."
        if strict_12_prepare_enabled and dry_run_target < strict_prepare_target:
            capped_note += " Adaptive max or CPU budget caps the strict 12-thread prepare boost below 12."
        return _Route2AdaptiveSpawnDryRunDecision(
            recommended_threads=dry_run_target,
            reason=(
                f"Initial spawn dry-run would choose {dry_run_target} threads for a single active "
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
        real_9_prepare_enabled: bool | None = None,
        real_9_prepare_candidate: bool = False,
        real_9_prepare_blockers: list[str] | None = None,
        effective_ladder_target: int | None = None,
        lite_adaptive_prepare_candidate: bool = False,
        lite_adaptive_prepare_blockers: list[str] | None = None,
        cloud_adaptive_prepare_enabled: bool = False,
        cloud_adaptive_prepare_candidate: bool = False,
        cloud_adaptive_prepare_blockers: list[str] | None = None,
        strict_12_prepare_enabled: bool | None = None,
        strict_12_prepare_candidate: bool = False,
        strict_12_prepare_blockers: list[str] | None = None,
        strict_12_prepare_reason: str | None = None,
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
            real_9_prepare_enabled=bool(
                getattr(self.settings, "route2_adaptive_thread_control_real_9_prepare_enabled", False)
                if real_9_prepare_enabled is None
                else real_9_prepare_enabled
            ),
            real_9_prepare_candidate=real_9_prepare_candidate,
            real_9_prepare_applied=False,
            real_9_prepare_blockers=list(dict.fromkeys(real_9_prepare_blockers or [])),
            effective_ladder_target=effective_ladder_target,
            lite_adaptive_prepare_candidate=lite_adaptive_prepare_candidate,
            lite_adaptive_prepare_applied=False,
            lite_adaptive_prepare_blockers=list(dict.fromkeys(lite_adaptive_prepare_blockers or [])),
            cloud_adaptive_prepare_enabled=cloud_adaptive_prepare_enabled,
            cloud_adaptive_prepare_candidate=cloud_adaptive_prepare_candidate,
            cloud_adaptive_prepare_applied=False,
            cloud_adaptive_prepare_blockers=list(dict.fromkeys(cloud_adaptive_prepare_blockers or [])),
            strict_12_prepare_enabled=bool(
                getattr(self.settings, "route2_adaptive_thread_control_strict_12_enabled", False)
                if strict_12_prepare_enabled is None
                else strict_12_prepare_enabled
            ),
            strict_12_prepare_candidate=strict_12_prepare_candidate,
            strict_12_prepare_applied=False,
            strict_12_prepare_blockers=list(dict.fromkeys(strict_12_prepare_blockers or [])),
            strict_12_prepare_reason=strict_12_prepare_reason,
        )

    def _route2_cloud_adaptive_prepare_enabled(self) -> bool:
        return (
            not bool(getattr(self.settings, "route2_adaptive_thread_control_local_only", True))
            and bool(getattr(self.settings, "route2_adaptive_thread_control_cloud_enabled", False))
        )

    def _route2_provider_prepare_blocker(self, error: str | None) -> str | None:
        if not error:
            return None
        lowered = str(error).lower()
        if "quota" in lowered:
            return "provider_quota_exceeded"
        if "auth" in lowered or "token" in lowered or "reconnect" in lowered:
            return "provider_auth_required"
        if "provider" in lowered or "source" in lowered or "cloud" in lowered:
            return "provider_source_error"
        return "provider_source_error"

    def _route2_prepare_alias_blockers(self, blockers: list[str]) -> list[str]:
        aliases: list[str] = []
        for blocker in blockers:
            if blocker in {"external_host_cpu_pressure_high", "external_host_cpu_pressure_moderate"}:
                aliases.append("external_pressure")
            elif blocker in {"external_ffmpeg_detected", "external_ffmpeg_process_present"}:
                aliases.append("external_ffmpeg")
            elif blocker in {"route2_memory_pressure", "route2_memory_hard_pressure"}:
                aliases.append("ram_pressure")
            elif blocker == "telemetry_missing_or_stale":
                aliases.append("lite_telemetry_immature")
        return aliases

    def _route2_lite_adaptive_prepare_blockers_locked(
        self,
        record: Route2WorkerRecord,
        *,
        session: MobilePlaybackSession | None,
        epoch: PlaybackEpoch | None,
        target_threads: int,
        spawn_sample_mature: bool,
    ) -> list[str]:
        if record.playback_mode != "lite":
            return []
        blockers: list[str] = []
        if session is None or epoch is None:
            return ["lite_telemetry_immature"]

        gate = self._route2_epoch_startup_attach_gate_locked(session, epoch)
        required_runway = float(
            gate.get("required_startup_runway_seconds")
            or min(ROUTE2_LITE_SLOW_START_RUNWAY_SECONDS, max(0.0, float(session.duration_seconds or 0.0)))
        )
        actual_runway = float(gate.get("actual_startup_runway_seconds") or 0.0)
        supply_rate_x = float(gate.get("supply_rate_x") or 0.0)
        supply_observation_seconds = float(gate.get("supply_observation_seconds") or 0.0)
        initial_empty_startup = (
            record.state == "queued"
            and not bool(epoch.init_published)
            and epoch.contiguous_published_through_segment is None
            and actual_runway <= 0.001
        )
        if actual_runway + 0.001 >= required_runway:
            blockers.append("lite_runway_already_sufficient")
        if (
            actual_runway + 0.001 >= min(required_runway, ROUTE2_LITE_FAST_START_RUNWAY_SECONDS)
            and supply_observation_seconds + 0.001 >= ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS
            and supply_rate_x >= ROUTE2_STARTUP_MIN_SUPPLY_RATE_X
        ):
            blockers.append("lite_supply_healthy_no_boost_needed")
        if not spawn_sample_mature:
            blockers.append("lite_telemetry_immature")
        elif (
            not initial_empty_startup
            and 0.0 < supply_observation_seconds < ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS
        ):
            blockers.append("lite_telemetry_immature")

        provider_blocker = self._route2_provider_prepare_blocker(record.non_retryable_error or session.last_error)
        if provider_blocker:
            blockers.append(provider_blocker)
        if self._route2_client_limited_locked(session, epoch):
            blockers.append("client_bound")
        source_feed = self._route2_source_feed_rate_locked(session, record)
        if record.source_kind == "local" and source_feed.available and source_feed.mature and source_feed.rate_x is not None:
            if float(source_feed.rate_x) < ROUTE2_CLOSED_LOOP_HEALTH_FLOOR_RATE_X:
                blockers.append("source_bound")

        cpu_thread_proven = (
            target_threads >= 6
            and (
                initial_empty_startup
                or self._route2_record_cpu_thread_limited(record)
                or record.cpu_cores_used is None
            )
        )
        if not cpu_thread_proven:
            blockers.append("lite_cpu_thread_bottleneck_not_proven")
        return list(dict.fromkeys(blockers))

    def _route2_cloud_adaptive_prepare_blockers_locked(
        self,
        record: Route2WorkerRecord,
        *,
        session: MobilePlaybackSession | None,
        epoch: PlaybackEpoch | None,
    ) -> list[str]:
        if record.source_kind != "cloud":
            return []
        blockers: list[str] = []
        if not self._route2_cloud_adaptive_prepare_enabled():
            blockers.append("cloud_adaptive_disabled")
        if session is None or epoch is None:
            blockers.append("cloud_source_feed_unavailable")
            return list(dict.fromkeys(blockers))

        provider_blocker = self._route2_provider_prepare_blocker(record.non_retryable_error or session.last_error)
        if provider_blocker:
            blockers.append(provider_blocker)

        source_feed = self._route2_source_feed_rate_locked(session, record)
        if not source_feed.available:
            blockers.append("cloud_source_feed_unavailable")
        elif not source_feed.mature:
            blockers.append("cloud_source_feed_immature")
        elif source_feed.rate_x is None:
            blockers.append("cloud_source_feed_unavailable")
        elif float(source_feed.rate_x) < ROUTE2_CLOSED_LOOP_HEALTH_FLOOR_RATE_X:
            blockers.extend(["cloud_source_feed_low", "source_bound"])

        snapshot = self._latest_route2_resource_snapshot_locked()
        limiting_factor = self._evaluate_route2_limiting_factor_locked(
            session,
            epoch,
            record,
            progress=None,
            host_cpu_pressure=_host_cpu_pressure_from_resource_snapshot(snapshot),
            route2_cpu_cores_used_total=(
                snapshot.route2_cpu_cores_used_total
                if snapshot is not None and not snapshot.sample_stale
                else None
            ),
            route2_cpu_upbound_cores=self._route2_budget_summary_locked()["route2_cpu_upbound_cores"],
            total_memory_bytes=(
                snapshot.total_memory_bytes
                if snapshot is not None and not snapshot.sample_stale
                else None
            ),
            route2_memory_bytes_total=(
                snapshot.route2_memory_bytes_total
                if snapshot is not None and not snapshot.sample_stale
                else None
            ),
        )
        if limiting_factor.primary == "provider_error":
            blockers.append(provider_blocker or "provider_source_error")
        elif limiting_factor.primary == "client":
            blockers.append("client_bound")
        elif limiting_factor.primary in {"source", "cloud_source", "local_source"}:
            blockers.append("source_bound")
        elif limiting_factor.primary != "cpu_thread":
            blockers.append("cpu_thread_not_primary")
        elif limiting_factor.confidence < 0.65:
            blockers.append("limiting_factor_confidence_low")
        return list(dict.fromkeys(blockers))

    def _route2_strict_12_prepare_blockers_locked(
        self,
        record: Route2WorkerRecord,
        *,
        session: MobilePlaybackSession | None,
        epoch: PlaybackEpoch | None,
        target_threads: int,
        spawn_sample_mature: bool,
        lite_blockers: list[str],
        cloud_blockers: list[str],
    ) -> list[str]:
        blockers: list[str] = []
        if not bool(getattr(self.settings, "route2_adaptive_thread_control_strict_12_enabled", False)):
            blockers.append("strict_12_prepare_disabled")
        if int(getattr(self.settings, "route2_adaptive_max_worker_threads", 0) or 0) < 12:
            blockers.append("adaptive_cap_below_12")
        if target_threads < 12:
            blockers.append("effective_ladder_target_below_12")
        if not spawn_sample_mature:
            blockers.append("strict_12_telemetry_immature")
        if session is None or epoch is None:
            blockers.append("strict_12_session_or_epoch_missing")
            return list(dict.fromkeys(blockers))

        provider_blocker = self._route2_provider_prepare_blocker(record.non_retryable_error or session.last_error)
        if provider_blocker:
            blockers.append(provider_blocker)
        for blocker in lite_blockers:
            if blocker in {
                "lite_runway_already_sufficient",
                "lite_supply_healthy_no_boost_needed",
                "lite_telemetry_immature",
                "lite_cpu_thread_bottleneck_not_proven",
                "source_bound",
                "client_bound",
                "provider_auth_required",
                "provider_quota_exceeded",
                "provider_source_error",
                "external_pressure",
                "external_ffmpeg",
                "ram_pressure",
            }:
                blockers.append(blocker)
        for blocker in cloud_blockers:
            if blocker in {
                "cloud_adaptive_disabled",
                "cloud_source_feed_unavailable",
                "cloud_source_feed_immature",
                "cloud_source_feed_low",
                "provider_auth_required",
                "provider_quota_exceeded",
                "provider_source_error",
                "client_bound",
                "source_bound",
                "cpu_thread_not_primary",
                "limiting_factor_confidence_low",
            }:
                blockers.append(blocker)

        initial_empty_startup = (
            record.state == "queued"
            and not bool(epoch.init_published)
            and epoch.contiguous_published_through_segment is None
        )
        strong_worker_cpu = False
        if record.cpu_cores_used is not None:
            current_threads = max(1, int(record.assigned_threads or 0))
            cpu_cores_used = max(0.0, float(record.cpu_cores_used))
            strong_worker_cpu = (
                cpu_cores_used / float(current_threads) >= 0.95
                or cpu_cores_used >= max(2.0, current_threads * 0.95)
            )
        strict_local_initial_startup = bool(record.source_kind == "local" and initial_empty_startup)
        strict_cloud_cpu_thread = False
        if record.source_kind == "cloud" and not any(
            blocker
            in {
                "cloud_adaptive_disabled",
                "cloud_source_feed_unavailable",
                "cloud_source_feed_immature",
                "cloud_source_feed_low",
                "source_bound",
                "client_bound",
                "provider_auth_required",
                "provider_quota_exceeded",
                "provider_source_error",
                "cpu_thread_not_primary",
                "limiting_factor_confidence_low",
            }
            for blocker in cloud_blockers
        ):
            strict_cloud_cpu_thread = True
        if not (strong_worker_cpu or strict_local_initial_startup or strict_cloud_cpu_thread):
            blockers.append("strict_12_cpu_thread_bottleneck_not_proven")
        return list(dict.fromkeys(blockers))

    def _resolve_route2_real_assigned_threads_locked(
        self,
        record: Route2WorkerRecord,
        *,
        fixed_assigned_threads: int,
        spawn_dry_run: _Route2AdaptiveSpawnDryRunDecision,
        session: MobilePlaybackSession | None = None,
        epoch: PlaybackEpoch | None = None,
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
        real_9_prepare_enabled = bool(
            getattr(self.settings, "route2_adaptive_thread_control_real_9_prepare_enabled", False)
        )
        strict_12_prepare_enabled = bool(
            getattr(self.settings, "route2_adaptive_thread_control_strict_12_enabled", False)
        )
        cloud_adaptive_prepare_enabled = self._route2_cloud_adaptive_prepare_enabled()
        lite_adaptive_prepare_candidate = record.playback_mode == "lite"
        cloud_adaptive_prepare_candidate = record.source_kind == "cloud" and cloud_adaptive_prepare_enabled
        real_9_prepare_candidate = real_9_prepare_enabled and (
            record.source_kind == "local" or cloud_adaptive_prepare_candidate
        )
        real_9_prepare_blockers: list[str] = []
        lite_adaptive_prepare_blockers: list[str] = []
        cloud_adaptive_prepare_blockers: list[str] = []
        strict_12_prepare_blockers: list[str] = []
        if record.source_kind == "cloud":
            if not cloud_adaptive_prepare_enabled:
                blockers.append("cloud_adaptive_disabled")
                cloud_adaptive_prepare_blockers.append("cloud_adaptive_disabled")
                if bool(getattr(self.settings, "route2_adaptive_thread_control_local_only", True)):
                    blockers.append("cloud_adaptive_thread_control_local_only")
                    cloud_adaptive_prepare_blockers.append("cloud_adaptive_thread_control_local_only")
                    reason_parts.append("cloud real adaptive thread control is blocked by local-only rollout")
                else:
                    reason_parts.append("cloud real adaptive thread control is disabled/deferred")
        elif record.source_kind == "local":
            pass
        else:
            blockers.append("unsupported_source_kind")
            reason_parts.append("unsupported source kind for real adaptive thread control")

        target_threads = int(spawn_dry_run.recommended_threads or 0)
        original_target_threads = target_threads
        effective_ladder_target = target_threads if target_threads > 0 else int(fixed_assigned_threads)
        strict_12_prepare_candidate = strict_12_prepare_enabled and original_target_threads >= 12
        if not real_9_prepare_enabled:
            real_9_prepare_blockers.append("real_9_prepare_disabled")
        if record.source_kind != "local":
            if record.source_kind == "cloud":
                if not cloud_adaptive_prepare_enabled:
                    real_9_prepare_blockers.append("cloud_adaptive_disabled")
            else:
                real_9_prepare_blockers.append("real_9_prepare_local_only")
        if spawn_dry_run.blockers:
            blockers.extend(spawn_dry_run.blockers)
            real_9_prepare_blockers.extend(spawn_dry_run.blockers)
            lite_adaptive_prepare_blockers.extend(
                blocker
                for blocker in spawn_dry_run.blockers
                if blocker not in {"cloud_adaptive_disabled", "cloud_adaptive_thread_control_local_only"}
            )
            cloud_adaptive_prepare_blockers.extend(spawn_dry_run.blockers)
            alias_blockers = self._route2_prepare_alias_blockers(spawn_dry_run.blockers)
            real_9_prepare_blockers.extend(alias_blockers)
            lite_adaptive_prepare_blockers.extend(alias_blockers)
            cloud_adaptive_prepare_blockers.extend(alias_blockers)
            reason_parts.append("spawn dry-run safety blockers did not pass")
        if not bool(spawn_dry_run.sample_mature):
            blockers.append("telemetry_missing_or_stale")
            real_9_prepare_blockers.append("telemetry_missing_or_stale")
            lite_adaptive_prepare_blockers.append("lite_telemetry_immature")
            reason_parts.append("resource telemetry is missing, immature, or stale")
        if lite_adaptive_prepare_candidate:
            lite_adaptive_prepare_blockers.extend(
                self._route2_lite_adaptive_prepare_blockers_locked(
                    record,
                    session=session,
                    epoch=epoch,
                    target_threads=target_threads,
                    spawn_sample_mature=bool(spawn_dry_run.sample_mature),
                )
            )
        if record.source_kind == "cloud":
            cloud_adaptive_prepare_blockers.extend(
                self._route2_cloud_adaptive_prepare_blockers_locked(
                    record,
                    session=session,
                    epoch=epoch,
                )
            )
            if cloud_adaptive_prepare_blockers:
                blockers.extend(cloud_adaptive_prepare_blockers)
                real_9_prepare_blockers.extend(cloud_adaptive_prepare_blockers)
                reason_parts.append("cloud adaptive prepare gates did not pass")
        if lite_adaptive_prepare_candidate:
            hard_lite_blockers = {
                "lite_telemetry_immature",
                "lite_cpu_thread_bottleneck_not_proven",
                "provider_auth_required",
                "provider_quota_exceeded",
                "provider_source_error",
                "source_bound",
                "client_bound",
                "external_pressure",
                "external_ffmpeg",
                "ram_pressure",
            }
            if any(blocker in hard_lite_blockers for blocker in lite_adaptive_prepare_blockers):
                blockers.extend(lite_adaptive_prepare_blockers)
                real_9_prepare_blockers.extend(lite_adaptive_prepare_blockers)
                reason_parts.append("Lite adaptive prepare gates did not pass")
            elif target_threads >= 9 and any(
                blocker in {"lite_runway_already_sufficient", "lite_supply_healthy_no_boost_needed"}
                for blocker in lite_adaptive_prepare_blockers
            ):
                target_threads = 6
                effective_ladder_target = 6
                real_9_prepare_blockers.extend(lite_adaptive_prepare_blockers)
                real_9_prepare_blockers.append("effective_ladder_target_below_9")
        if original_target_threads >= 12 and not strict_12_prepare_enabled:
            strict_12_prepare_blockers.append("strict_12_prepare_disabled")
            if real_9_prepare_enabled and int(getattr(self.settings, "route2_adaptive_max_worker_threads", 0) or 0) >= 9:
                target_threads = 9
                effective_ladder_target = 9
            else:
                target_threads = 6
                effective_ladder_target = 6
        if strict_12_prepare_candidate:
            strict_12_prepare_blockers.extend(
                self._route2_strict_12_prepare_blockers_locked(
                    record,
                    session=session,
                    epoch=epoch,
                    target_threads=target_threads,
                    spawn_sample_mature=bool(spawn_dry_run.sample_mature),
                    lite_blockers=lite_adaptive_prepare_blockers,
                    cloud_blockers=cloud_adaptive_prepare_blockers,
                )
            )
            if strict_12_prepare_blockers:
                reason_parts.append("strict 12 prepare gates did not pass")
                if target_threads >= 12:
                    if real_9_prepare_enabled and int(getattr(self.settings, "route2_adaptive_max_worker_threads", 0) or 0) >= 9:
                        target_threads = 9
                        effective_ladder_target = 9
                    else:
                        target_threads = 6
                        effective_ladder_target = 6
        if int(getattr(self.settings, "route2_adaptive_max_worker_threads", 0) or 0) < 9:
            real_9_prepare_blockers.append("adaptive_max_below_9")
        if target_threads < 9:
            real_9_prepare_blockers.append("effective_ladder_target_below_9")
        elif target_threads == 12:
            real_9_prepare_blockers.append("strict_12_selected_instead")
        if target_threads in {9, 12}:
            if not real_9_prepare_enabled:
                blockers.append("real_9_prepare_disabled")
                reason_parts.append("real 9-thread prepare boost flag is disabled")
            if int(getattr(self.settings, "route2_adaptive_max_worker_threads", 0) or 0) < 9:
                blockers.append("adaptive_max_below_9")
                reason_parts.append("adaptive max worker threads is below the 9-thread prepare tier")
        elif target_threads != 6:
            blockers.append("unsupported_real_adaptive_target")
            reason_parts.append("this phase only permits initial 6-thread, flagged 9-thread, or strict 12-thread prepare assignment")
        if int(getattr(self.settings, "route2_adaptive_max_worker_threads", 0) or 0) < 6:
            blockers.append("adaptive_max_below_first_tier")
            reason_parts.append("adaptive max worker threads is below the 6-thread first tier")
        if int(fixed_assigned_threads) < int(self.settings.route2_min_worker_threads):
            blockers.append("fixed_assignment_below_min_worker_threads")
            real_9_prepare_blockers.append("fixed_assignment_below_min_worker_threads")
            reason_parts.append("fixed assignment is below route2_min_worker_threads")

        if blockers:
            source = (
                "cloud_disabled"
                if any(
                    blocker == "cloud_adaptive_disabled" or blocker.startswith("cloud_adaptive_thread_control")
                    for blocker in blockers
                )
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
                real_9_prepare_enabled=real_9_prepare_enabled,
                real_9_prepare_candidate=real_9_prepare_candidate,
                real_9_prepare_blockers=real_9_prepare_blockers,
                effective_ladder_target=effective_ladder_target,
                lite_adaptive_prepare_candidate=lite_adaptive_prepare_candidate,
                lite_adaptive_prepare_blockers=lite_adaptive_prepare_blockers,
                cloud_adaptive_prepare_enabled=cloud_adaptive_prepare_enabled,
                cloud_adaptive_prepare_candidate=cloud_adaptive_prepare_candidate,
                cloud_adaptive_prepare_blockers=cloud_adaptive_prepare_blockers,
                strict_12_prepare_enabled=strict_12_prepare_enabled,
                strict_12_prepare_candidate=strict_12_prepare_candidate,
                strict_12_prepare_blockers=strict_12_prepare_blockers,
                strict_12_prepare_reason=(
                    "Strict 12 prepare was blocked; fixed assignment fallback used."
                    if strict_12_prepare_candidate
                    else None
                ),
            )

        if target_threads == 12:
            source = "adaptive_cloud_prepare_12" if record.source_kind == "cloud" else "adaptive_local_prepare_12"
            lite_prepare_applied = bool(lite_adaptive_prepare_candidate and not lite_adaptive_prepare_blockers)
            cloud_prepare_applied = bool(record.source_kind == "cloud" and not cloud_adaptive_prepare_blockers)
            return _Route2RealThreadAssignmentDecision(
                assigned_threads=12,
                assignment_policy=source,
                assignment_reason=(
                    "Adaptive real thread control selected strict 12 threads for a Route2 prepare/startup boost "
                    "after strict single-workload, source, telemetry, CPU/thread, resource, and safety gates passed."
                ),
                assignment_blockers=[],
                adaptive_control_enabled=True,
                adaptive_control_applied=True,
                assigned_threads_source=source,
                fallback_used=False,
                real_9_prepare_enabled=real_9_prepare_enabled,
                real_9_prepare_candidate=real_9_prepare_candidate,
                real_9_prepare_applied=False,
                real_9_prepare_blockers=["strict_12_selected_instead"],
                effective_ladder_target=effective_ladder_target,
                lite_adaptive_prepare_candidate=lite_adaptive_prepare_candidate,
                lite_adaptive_prepare_applied=lite_prepare_applied,
                lite_adaptive_prepare_blockers=[],
                cloud_adaptive_prepare_enabled=cloud_adaptive_prepare_enabled,
                cloud_adaptive_prepare_candidate=cloud_adaptive_prepare_candidate,
                cloud_adaptive_prepare_applied=cloud_prepare_applied,
                cloud_adaptive_prepare_blockers=[],
                strict_12_prepare_enabled=strict_12_prepare_enabled,
                strict_12_prepare_candidate=True,
                strict_12_prepare_applied=True,
                strict_12_prepare_blockers=[],
                strict_12_prepare_reason="Strict 12 prepare gates passed.",
            )

        if target_threads == 9:
            source = "adaptive_cloud_prepare_9" if record.source_kind == "cloud" else "adaptive_local_prepare_9"
            lite_prepare_applied = bool(lite_adaptive_prepare_candidate and not lite_adaptive_prepare_blockers)
            cloud_prepare_applied = bool(record.source_kind == "cloud" and not cloud_adaptive_prepare_blockers)
            return _Route2RealThreadAssignmentDecision(
                assigned_threads=9,
                assignment_policy=source,
                assignment_reason=(
                    "Adaptive real thread control selected 9 threads for a Route2 prepare/startup boost "
                    "after the explicit real-9 flag, source, single-workload, telemetry, resource, and safety gates passed."
                ),
                assignment_blockers=[],
                adaptive_control_enabled=True,
                adaptive_control_applied=True,
                assigned_threads_source=source,
                fallback_used=False,
                real_9_prepare_enabled=real_9_prepare_enabled,
                real_9_prepare_candidate=True,
                real_9_prepare_applied=True,
                real_9_prepare_blockers=[],
                effective_ladder_target=effective_ladder_target,
                lite_adaptive_prepare_candidate=lite_adaptive_prepare_candidate,
                lite_adaptive_prepare_applied=lite_prepare_applied,
                lite_adaptive_prepare_blockers=[],
                cloud_adaptive_prepare_enabled=cloud_adaptive_prepare_enabled,
                cloud_adaptive_prepare_candidate=cloud_adaptive_prepare_candidate,
                cloud_adaptive_prepare_applied=cloud_prepare_applied,
                cloud_adaptive_prepare_blockers=[],
                strict_12_prepare_enabled=strict_12_prepare_enabled,
                strict_12_prepare_candidate=strict_12_prepare_candidate,
                strict_12_prepare_applied=False,
                strict_12_prepare_blockers=list(dict.fromkeys(strict_12_prepare_blockers)),
                strict_12_prepare_reason=(
                    "Strict 12 prepare was blocked; 9-thread fallback applied."
                    if strict_12_prepare_candidate and strict_12_prepare_blockers
                    else None
                ),
            )

        source = "adaptive_cloud_prepare_6" if record.source_kind == "cloud" else "adaptive_local_initial_6"
        lite_prepare_applied = bool(lite_adaptive_prepare_candidate and not lite_adaptive_prepare_blockers)
        cloud_prepare_applied = bool(record.source_kind == "cloud" and not cloud_adaptive_prepare_blockers)
        return _Route2RealThreadAssignmentDecision(
            assigned_threads=6,
            assignment_policy=source,
            assignment_reason=(
                "Adaptive real thread control selected 6 threads for an initial Route2 prepare spawn "
                "after strict source, telemetry, resource, and safety gates passed."
            ),
            assignment_blockers=[],
            adaptive_control_enabled=True,
            adaptive_control_applied=True,
            assigned_threads_source=source,
            fallback_used=False,
            real_9_prepare_enabled=real_9_prepare_enabled,
            real_9_prepare_candidate=real_9_prepare_candidate,
            real_9_prepare_applied=False,
            real_9_prepare_blockers=list(dict.fromkeys(real_9_prepare_blockers)),
            effective_ladder_target=effective_ladder_target,
            lite_adaptive_prepare_candidate=lite_adaptive_prepare_candidate,
            lite_adaptive_prepare_applied=lite_prepare_applied,
            lite_adaptive_prepare_blockers=list(dict.fromkeys(lite_adaptive_prepare_blockers)),
            cloud_adaptive_prepare_enabled=cloud_adaptive_prepare_enabled,
            cloud_adaptive_prepare_candidate=cloud_adaptive_prepare_candidate,
            cloud_adaptive_prepare_applied=cloud_prepare_applied,
            cloud_adaptive_prepare_blockers=list(dict.fromkeys(cloud_adaptive_prepare_blockers)),
            strict_12_prepare_enabled=strict_12_prepare_enabled,
            strict_12_prepare_candidate=strict_12_prepare_candidate,
            strict_12_prepare_applied=False,
            strict_12_prepare_blockers=list(dict.fromkeys(strict_12_prepare_blockers)),
            strict_12_prepare_reason=(
                "Strict 12 prepare was blocked; 6-thread fallback applied."
                if strict_12_prepare_candidate and strict_12_prepare_blockers
                else None
            ),
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

        required_startup_runway_seconds = 120.0
        if record.playback_mode == "lite":
            lite_gate = self._route2_epoch_startup_attach_gate_locked(session, epoch)
            required_startup_runway_seconds = float(
                lite_gate.get("required_startup_runway_seconds") or ROUTE2_LITE_SLOW_START_RUNWAY_SECONDS
            )

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
            required_startup_runway_seconds=required_startup_runway_seconds,
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
                audio_stream_index=epoch.audio_stream_index if epoch is not None else None,
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

    def _route2_worker_playback_metadata_locked(
        self,
        record: Route2WorkerRecord,
        session: MobilePlaybackSession | None,
        payload: dict[str, object],
    ) -> dict[str, object]:
        playback_mode = str(
            session.browser_playback.playback_mode if session is not None else record.playback_mode
        ).strip().lower()
        if playback_mode == "full":
            playback_surface = "route2_full"
            playback_surface_label = "Full"
        elif playback_mode == "lite":
            playback_surface = "route2_lite"
            playback_surface_label = "Lite"
        else:
            playback_surface = "unknown"
            playback_surface_label = "Unknown playback"

        device_class, device_label, evidence_source, confidence = _browser_device_display_from_evidence(
            client_device_class=session.client_device_class if session is not None else None,
            user_agent=session.client_user_agent if session is not None else None,
        )
        profile_label = str(payload.get("display_profile_label") or _route2_display_profile_label(record.profile))
        source_label = _source_kind_display_label(record.source_kind)
        playback_metadata_label = f"{playback_surface_label} · {device_label} {profile_label} · {source_label}"
        return {
            "playback_surface": playback_surface,
            "playback_surface_label": playback_surface_label,
            "device_class": device_class,
            "device_label": device_label,
            "device_evidence_source": evidence_source,
            "device_confidence": confidence,
            "source_label": source_label,
            "playback_metadata_label": playback_metadata_label,
        }

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
                    "real_9_prepare_enabled": record.real_9_prepare_enabled,
                    "real_9_prepare_candidate": record.real_9_prepare_candidate,
                    "real_9_prepare_applied": record.real_9_prepare_applied,
                    "real_9_prepare_blockers": list(record.real_9_prepare_blockers),
                    "effective_ladder_target": record.effective_ladder_target,
                    "lite_adaptive_prepare_candidate": record.lite_adaptive_prepare_candidate,
                    "lite_adaptive_prepare_applied": record.lite_adaptive_prepare_applied,
                    "lite_adaptive_prepare_blockers": list(record.lite_adaptive_prepare_blockers),
                    "cloud_adaptive_prepare_enabled": record.cloud_adaptive_prepare_enabled,
                    "cloud_adaptive_prepare_candidate": record.cloud_adaptive_prepare_candidate,
                    "cloud_adaptive_prepare_applied": record.cloud_adaptive_prepare_applied,
                    "cloud_adaptive_prepare_blockers": list(record.cloud_adaptive_prepare_blockers),
                    "strict_12_prepare_enabled": record.strict_12_prepare_enabled,
                    "strict_12_prepare_candidate": record.strict_12_prepare_candidate,
                    "strict_12_prepare_applied": record.strict_12_prepare_applied,
                    "strict_12_prepare_blockers": list(record.strict_12_prepare_blockers),
                    "strict_12_prepare_reason": record.strict_12_prepare_reason,
                    "adaptive_downshift_enabled": record.adaptive_downshift_enabled,
                    "adaptive_downshift_candidate": record.adaptive_downshift_candidate,
                    "adaptive_downshift_mode": record.adaptive_downshift_mode,
                    "autonomous_maintenance_downshift_enabled": (
                        record.autonomous_maintenance_downshift_enabled
                    ),
                    "autonomous_maintenance_downshift_candidate": (
                        record.autonomous_maintenance_downshift_candidate
                    ),
                    "autonomous_maintenance_downshift_blockers": list(
                        record.autonomous_maintenance_downshift_blockers
                    ),
                    "maintenance_downshift_suppressed_by_reclaim": (
                        record.maintenance_downshift_suppressed_by_reclaim
                    ),
                    "donor_reserved_for_reclaim": record.donor_reserved_for_reclaim,
                    "reclaim_donor_downshift_active": record.reclaim_donor_downshift_active,
                    "adaptive_downshift_target_threads": record.adaptive_downshift_target_threads,
                    "adaptive_downshift_policy": record.adaptive_downshift_policy,
                    "adaptive_downshift_reason": record.adaptive_downshift_reason,
                    "adaptive_downshift_blockers": list(record.adaptive_downshift_blockers),
                    "adaptive_downshift_replacement_epoch_id": record.adaptive_downshift_replacement_epoch_id,
                    "adaptive_downshift_replacement_worker_id": record.adaptive_downshift_replacement_worker_id,
                    "adaptive_downshift_state": record.adaptive_downshift_state,
                    "adaptive_downshift_action_deferred": record.adaptive_downshift_action_deferred,
                    "adaptive_downshift_action_defer_reason": record.adaptive_downshift_action_defer_reason,
                    "adaptive_downshift_transition_started_at": record.adaptive_downshift_transition_started_at,
                    "adaptive_downshift_switched_at": record.adaptive_downshift_switched_at,
                    "adaptive_downshift_aborted_reason": record.adaptive_downshift_aborted_reason,
                    "adaptive_downshift_pressure_abort_reason": record.adaptive_downshift_pressure_abort_reason,
                    "adaptive_downshift_pressure_snapshot": dict(record.adaptive_downshift_pressure_snapshot),
                    "adaptive_downshift_retry_count": record.adaptive_downshift_retry_count,
                    "adaptive_downshift_retry_not_before_seconds": (
                        round(record.adaptive_downshift_retry_not_before_seconds, 3)
                        if record.adaptive_downshift_retry_not_before_seconds is not None
                        else None
                    ),
                    "adaptive_downshift_retry_blocker": record.adaptive_downshift_retry_blocker,
                    "adaptive_downshift_last_abort_reason": record.adaptive_downshift_last_abort_reason,
                    "adaptive_downshift_replacement_epoch_cap_remaining": (
                        record.adaptive_downshift_replacement_epoch_cap_remaining
                    ),
                    "adaptive_boost_exit_reason": record.adaptive_boost_exit_reason,
                    "current_boost_tier": record.current_boost_tier,
                    "maintenance_tier_target": record.maintenance_tier_target,
                    "downshift_safe_to_apply": record.downshift_safe_to_apply,
                    "downshift_transition_headroom_required": record.downshift_transition_headroom_required,
                    "downshift_transition_headroom_available": record.downshift_transition_headroom_available,
                    "adaptive_reclaim_enabled": record.adaptive_reclaim_enabled,
                    "adaptive_reclaim_dry_run_enabled": record.adaptive_reclaim_dry_run_enabled,
                    "adaptive_reclaim_candidate": record.adaptive_reclaim_candidate,
                    "adaptive_reclaim_candidate_reason": record.adaptive_reclaim_candidate_reason,
                    "adaptive_reclaim_target_threads": record.adaptive_reclaim_target_threads,
                    "adaptive_reclaim_state": record.adaptive_reclaim_state,
                    "adaptive_reclaim_request_id": record.adaptive_reclaim_request_id,
                    "adaptive_reclaim_consumer_worker_id": record.adaptive_reclaim_consumer_worker_id,
                    "adaptive_reclaim_consumer_session_id": record.adaptive_reclaim_consumer_session_id,
                    "adaptive_reclaim_consumer_user_id": record.adaptive_reclaim_consumer_user_id,
                    "adaptive_reclaim_consumer_media_item_id": record.adaptive_reclaim_consumer_media_item_id,
                    "adaptive_reclaim_consumer_reason": record.adaptive_reclaim_consumer_reason,
                    "adaptive_reclaim_donor_worker_id": record.adaptive_reclaim_donor_worker_id,
                    "adaptive_reclaim_donor_session_id": record.adaptive_reclaim_donor_session_id,
                    "adaptive_reclaim_downshift_replacement_epoch_id": (
                        record.adaptive_reclaim_downshift_replacement_epoch_id
                    ),
                    "adaptive_reclaim_downshift_replacement_worker_id": (
                        record.adaptive_reclaim_downshift_replacement_worker_id
                    ),
                    "adaptive_reclaim_started_at": record.adaptive_reclaim_started_at,
                    "adaptive_reclaim_switched_at": record.adaptive_reclaim_switched_at,
                    "adaptive_reclaim_measured_at": record.adaptive_reclaim_measured_at,
                    "adaptive_reclaim_completed_at": record.adaptive_reclaim_completed_at,
                    "adaptive_reclaim_failed_reason": record.adaptive_reclaim_failed_reason,
                    "adaptive_reclaim_released_threads_expected": record.adaptive_reclaim_released_threads_expected,
                    "adaptive_reclaim_released_threads_measured": record.adaptive_reclaim_released_threads_measured,
                    "adaptive_reclaim_released_cpu_cores_measured": (
                        record.adaptive_reclaim_released_cpu_cores_measured
                    ),
                    "adaptive_reclaim_cpu_headroom_before": record.adaptive_reclaim_cpu_headroom_before,
                    "adaptive_reclaim_cpu_headroom_after": record.adaptive_reclaim_cpu_headroom_after,
                    "adaptive_reclaim_route2_cpu_cores_used_before": (
                        record.adaptive_reclaim_route2_cpu_cores_used_before
                    ),
                    "adaptive_reclaim_route2_cpu_cores_used_after": (
                        record.adaptive_reclaim_route2_cpu_cores_used_after
                    ),
                    "adaptive_reclaim_user_cpu_cores_used_before": (
                        record.adaptive_reclaim_user_cpu_cores_used_before
                    ),
                    "adaptive_reclaim_user_cpu_cores_used_after": (
                        record.adaptive_reclaim_user_cpu_cores_used_after
                    ),
                    "adaptive_reclaim_host_cpu_used_cores_before": (
                        record.adaptive_reclaim_host_cpu_used_cores_before
                    ),
                    "adaptive_reclaim_host_cpu_used_cores_after": (
                        record.adaptive_reclaim_host_cpu_used_cores_after
                    ),
                    "adaptive_reclaim_host_cpu_spare_cores_before": (
                        record.adaptive_reclaim_host_cpu_spare_cores_before
                    ),
                    "adaptive_reclaim_host_cpu_spare_cores_after": (
                        record.adaptive_reclaim_host_cpu_spare_cores_after
                    ),
                    "adaptive_reclaim_route2_headroom_before": record.adaptive_reclaim_route2_headroom_before,
                    "adaptive_reclaim_route2_headroom_after": record.adaptive_reclaim_route2_headroom_after,
                    "adaptive_reclaim_memory_pressure_before": record.adaptive_reclaim_memory_pressure_before,
                    "adaptive_reclaim_memory_pressure_after": record.adaptive_reclaim_memory_pressure_after,
                    "adaptive_reclaim_external_pressure_before": record.adaptive_reclaim_external_pressure_before,
                    "adaptive_reclaim_external_pressure_after": record.adaptive_reclaim_external_pressure_after,
                    "adaptive_reclaim_capacity_sufficient_for_consumer": (
                        record.adaptive_reclaim_capacity_sufficient_for_consumer
                    ),
                    "adaptive_reclaim_retry_count": record.adaptive_reclaim_retry_count,
                    "adaptive_reclaim_retry_not_before_seconds": (
                        round(record.adaptive_reclaim_retry_not_before_seconds, 3)
                        if record.adaptive_reclaim_retry_not_before_seconds is not None
                        else None
                    ),
                    "adaptive_reclaim_retry_blocker": record.adaptive_reclaim_retry_blocker,
                    "adaptive_reclaim_blockers": list(record.adaptive_reclaim_blockers),
                    "adaptive_reclaim_abort_reason": record.adaptive_reclaim_abort_reason,
                    "adaptive_resupply_enabled": record.adaptive_resupply_enabled,
                    "adaptive_resupply_dry_run_enabled": record.adaptive_resupply_dry_run_enabled,
                    "adaptive_resupply_needed": record.adaptive_resupply_needed,
                    "adaptive_resupply_reason": record.adaptive_resupply_reason,
                    "adaptive_resupply_priority": record.adaptive_resupply_priority,
                    "adaptive_resupply_target_threads": record.adaptive_resupply_target_threads,
                    "adaptive_resupply_state": record.adaptive_resupply_state,
                    "adaptive_resupply_request_id": record.adaptive_resupply_request_id,
                    "adaptive_resupply_original_reclaim_request_id": (
                        record.adaptive_resupply_original_reclaim_request_id
                    ),
                    "adaptive_resupply_donor_worker_id": record.adaptive_resupply_donor_worker_id,
                    "adaptive_resupply_replacement_epoch_id": record.adaptive_resupply_replacement_epoch_id,
                    "adaptive_resupply_replacement_worker_id": record.adaptive_resupply_replacement_worker_id,
                    "adaptive_resupply_started_at": record.adaptive_resupply_started_at,
                    "adaptive_resupply_switched_at": record.adaptive_resupply_switched_at,
                    "adaptive_resupply_measured_at": record.adaptive_resupply_measured_at,
                    "adaptive_resupply_blockers": list(record.adaptive_resupply_blockers),
                    "adaptive_resupply_abort_reason": record.adaptive_resupply_abort_reason,
                    "adaptive_resupply_stabilization_active": record.adaptive_resupply_stabilization_active,
                    "adaptive_resupply_stabilization_until": record.adaptive_resupply_stabilization_until,
                    "adaptive_resupply_stabilization_seconds_remaining": (
                        round(record.adaptive_resupply_stabilization_seconds_remaining, 3)
                        if record.adaptive_resupply_stabilization_seconds_remaining is not None
                        else None
                    ),
                    "adaptive_resupply_stabilization_reason": record.adaptive_resupply_stabilization_reason,
                    "last_resupply_completed_at": record.last_resupply_completed_at,
                    "last_resupply_target_threads": record.last_resupply_target_threads,
                    "resupplied_donor_protection_active": record.resupplied_donor_protection_active,
                    "priority_reexpand_pending": record.priority_reexpand_pending,
                    "priority_reexpand_reason": record.priority_reexpand_reason,
                    "donor_protection_active": record.donor_protection_active,
                    "donor_health_after_resupply": dict(record.donor_health_after_resupply),
                    "admission_blocked_by_resupply": record.admission_blocked_by_resupply,
                    "admission_waiting_for_reclaim": record.admission_waiting_for_reclaim,
                    "admission_reclaim_possible": record.admission_reclaim_possible,
                    "admission_reclaim_attempted": record.admission_reclaim_attempted,
                    "admission_reclaim_succeeded": record.admission_reclaim_succeeded,
                    "admission_reclaim_failed_reason": record.admission_reclaim_failed_reason,
                    "admission_capacity_after_reclaim": record.admission_capacity_after_reclaim,
                    "admission_hard_block_reason": record.admission_hard_block_reason,
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
                    payload["lite_undersupply_runway_seconds"] = None
                    payload["lite_undersupply_detected"] = False
                    payload["lite_undersupply_reason"] = None
                    payload["lite_required_runway_seconds"] = None
                    payload["lite_required_runway_source"] = None
                    payload.update({
                        "lite_threshold_decider_state": None,
                        "lite_threshold_previous_tier": None,
                        "lite_threshold_candidate_tier": None,
                        "lite_threshold_confirmed_tier": None,
                        "lite_threshold_decider_reason": None,
                        "lite_positive_evidence_seconds": None,
                        "lite_negative_evidence_seconds": None,
                        "lite_frontier_sample_count": None,
                        "lite_frontier_growth_rate_x": None,
                        "lite_effective_supply_rate_x": None,
                        "lite_supply_fast_ema_rate_x": None,
                        "lite_supply_slow_ema_rate_x": None,
                        "lite_supply_median_rate_x": None,
                        "lite_hysteresis_hold_reason": None,
                        "lite_cold_start_hold": False,
                        "lite_post_recovery_hold": False,
                        "lite_post_seek_hold": False,
                    })
                    payload.update(self._route2_bad_condition_reserve_payload_locked(session, epoch))
                    if record.playback_mode == "lite":
                        lite_gate = self._route2_epoch_startup_attach_gate_locked(session, epoch)
                        payload["lite_undersupply_runway_seconds"] = round(
                            float(
                                lite_gate.get("lite_undersupply_runway_seconds")
                                or ROUTE2_LITE_UNDERSUPPLY_START_RUNWAY_SECONDS
                            ),
                            2,
                        )
                        payload["lite_undersupply_detected"] = bool(
                            lite_gate.get("lite_undersupply_detected") or False
                        )
                        payload["lite_undersupply_reason"] = lite_gate.get("lite_undersupply_reason")
                        payload["lite_required_runway_seconds"] = (
                            round(float(lite_gate["lite_required_runway_seconds"]), 2)
                            if lite_gate.get("lite_required_runway_seconds") is not None
                            else None
                        )
                        payload["lite_required_runway_source"] = lite_gate.get("lite_required_runway_source")
                        for key in (
                            "lite_threshold_decider_state",
                            "lite_threshold_previous_tier",
                            "lite_threshold_candidate_tier",
                            "lite_threshold_confirmed_tier",
                            "lite_threshold_decider_reason",
                            "lite_positive_evidence_seconds",
                            "lite_negative_evidence_seconds",
                            "lite_frontier_sample_count",
                            "lite_frontier_growth_rate_x",
                            "lite_effective_supply_rate_x",
                            "lite_supply_fast_ema_rate_x",
                            "lite_supply_slow_ema_rate_x",
                            "lite_supply_median_rate_x",
                            "lite_hysteresis_hold_reason",
                            "lite_cold_start_hold",
                            "lite_post_recovery_hold",
                            "lite_post_seek_hold",
                        ):
                            if key in lite_gate:
                                payload[key] = lite_gate[key]
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
                    payload["lite_undersupply_runway_seconds"] = None
                    payload["lite_undersupply_detected"] = False
                    payload["lite_undersupply_reason"] = None
                    payload["lite_required_runway_seconds"] = None
                    payload["lite_required_runway_source"] = None
                    payload.update({
                        "lite_threshold_decider_state": None,
                        "lite_threshold_previous_tier": None,
                        "lite_threshold_candidate_tier": None,
                        "lite_threshold_confirmed_tier": None,
                        "lite_threshold_decider_reason": None,
                        "lite_positive_evidence_seconds": None,
                        "lite_negative_evidence_seconds": None,
                        "lite_frontier_sample_count": None,
                        "lite_frontier_growth_rate_x": None,
                        "lite_effective_supply_rate_x": None,
                        "lite_supply_fast_ema_rate_x": None,
                        "lite_supply_slow_ema_rate_x": None,
                        "lite_supply_median_rate_x": None,
                        "lite_hysteresis_hold_reason": None,
                        "lite_cold_start_hold": False,
                        "lite_post_recovery_hold": False,
                        "lite_post_seek_hold": False,
                    })
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
                    payload["full_bad_condition_detected"] = False
                    payload["full_bad_condition_reason"] = None
                    payload["full_bad_condition_reasons"] = []
                    payload["full_bad_condition_confidence"] = "none"
                    payload["full_bad_condition_mature"] = False
                    payload["full_bad_condition_reserve_required_seconds"] = None
                    payload["full_bad_condition_reserve_target_seconds"] = None
                    payload["full_bad_condition_actual_contiguous_end_seconds"] = None
                    payload["full_bad_condition_actual_contiguous_seconds_after_target"] = None
                    payload["full_bad_condition_reserve_remaining_seconds"] = None
                    payload["full_bad_condition_reserve_satisfied"] = False
                    payload["full_bad_condition_reserve_progress_source"] = None
                    payload["full_bad_condition_reserve_eta_seconds"] = None
                    payload["full_bad_condition_gate_enabled"] = bool(
                        getattr(self.settings, "route2_full_bad_condition_30min_gate_enabled", False)
                    )
                    payload["full_bad_condition_gate_dry_run_enabled"] = bool(
                        getattr(self.settings, "route2_full_bad_condition_30min_gate_dry_run_enabled", True)
                    )
                    payload["full_bad_condition_gate_would_block_ready"] = False
                    payload["full_bad_condition_gate_blocks_ready"] = False
                    payload["full_bad_condition_gate_blockers"] = []
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
                payload.update(self._route2_worker_playback_metadata_locked(record, session, payload))
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
                if session is not None and epoch is not None:
                    downshift_payload = self._route2_adaptive_downshift_payload_locked(
                        session,
                        epoch,
                        record,
                        closed_loop_decision,
                    )
                else:
                    downshift_payload = self._adaptive_downshift_default_payload()
                payload.update(downshift_payload)
                self._apply_route2_downshift_payload_to_record(record, downshift_payload)
                if session is not None and epoch is not None:
                    reclaim_payload = self._route2_adaptive_reclaim_payload_locked(
                        session,
                        epoch,
                        record,
                        closed_loop_decision,
                        downshift_payload,
                    )
                    resupply_payload = self._route2_adaptive_resupply_payload_locked(
                        session,
                        epoch,
                        record,
                    )
                else:
                    reclaim_payload = self._adaptive_reclaim_default_payload()
                    resupply_payload = self._adaptive_resupply_default_payload()
                payload.update(reclaim_payload)
                payload.update(resupply_payload)
                self._apply_route2_reclaim_payload_to_record(record, reclaim_payload)
                self._apply_route2_reclaim_payload_to_record(record, resupply_payload)
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

    def invalidate_sessions_for_media_items_and_users(
        self,
        *,
        media_item_ids: list[int],
        user_ids: list[int],
        reason: str,
    ) -> int:
        media_set = {int(item_id) for item_id in media_item_ids}
        user_set = {int(user_id) for user_id in user_ids}
        if not media_set or not user_set:
            return 0
        with self._lock:
            sessions = self._collect_sessions_to_invalidate_locked(
                lambda session: session.media_item_id in media_set and session.user_id in user_set,
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
        if reason == "age_requirement_changed":
            return "This movie's age requirement changed. Browser playback preparation was stopped."
        if reason == "user_age_credential_changed":
            return "This account's age credential changed. Browser playback preparation was stopped."
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

    def _build_browser_playback_session(
        self,
        *,
        engine_mode: str,
        playback_mode: str,
        selected_audio_stream_index: int | None = None,
    ) -> BrowserPlaybackSession:
        return BrowserPlaybackSession(
            engine_mode=engine_mode,
            playback_mode=playback_mode,
            state="legacy" if engine_mode == "legacy" else "starting",
            selected_audio_stream_index=selected_audio_stream_index,
            active_audio_stream_index=selected_audio_stream_index,
            audio_switch_state="active",
        )

    def _initialize_route2_session_locked(self, session: MobilePlaybackSession) -> None:
        _initialize_route2_session_locked_impl(
            session,
            build_route2_epoch_locked=self._build_route2_epoch_locked,
            ensure_route2_epoch_workspace_locked=self._ensure_route2_epoch_workspace_locked,
            ensure_route2_full_preflight_locked=self._ensure_route2_full_preflight_locked,
        )

    def _build_route2_epoch_locked(
        self,
        session: MobilePlaybackSession,
        *,
        target_position_seconds_override: float | None = None,
    ) -> PlaybackEpoch:
        return _build_route2_epoch_locked_impl(
            self._route2_root,
            session,
            clamp_time=self._clamp_time,
            target_position_seconds_override=target_position_seconds_override,
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
    ) -> dict[str, object]:
        gate = _route2_full_mode_gate_locked_impl(
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
        reserve_status = self._route2_bad_condition_reserve_payload_locked(session, epoch)
        gate.update(reserve_status)
        if bool(reserve_status["full_bad_condition_gate_blocks_ready"]):
            gate["mode_state"] = "preparing"
            gate["mode_ready"] = False
            gate["mode_estimate_seconds"] = reserve_status["full_bad_condition_reserve_eta_seconds"]
            gate["mode_estimate_source"] = "published_frontier"
            gate["gate_reason"] = "preparing_for_bad_condition_reserve"
        return gate

    def _route2_epoch_startup_attach_gate_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> dict[str, object]:
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
        mutate_session_target: bool = True,
    ) -> PlaybackEpoch | None:
        browser_session = session.browser_playback
        if browser_session.replacement_epoch_id:
            existing_replacement = browser_session.epochs.get(browser_session.replacement_epoch_id)
            if (
                existing_replacement is not None
                and existing_replacement.replacement_reason == "audio_track_switch"
                and reason != "audio_track_switch"
            ):
                self._fail_route2_audio_switch_locked(
                    session,
                    existing_replacement,
                    reason="Audio switch was superseded by another playback recovery.",
                )
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
        safe_target = self._clamp_time(target_position_seconds, session.duration_seconds)
        if mutate_session_target:
            session.target_position_seconds = safe_target
            session.pending_target_seconds = session.target_position_seconds
            replacement_epoch = self._build_route2_epoch_locked(session)
        else:
            replacement_epoch = self._build_route2_epoch_locked(
                session,
                target_position_seconds_override=safe_target,
            )
        replacement_epoch.replacement_reason = reason
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
            target_position_seconds=round(safe_target, 2),
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
        if replacement_epoch.replacement_reason == "audio_track_switch":
            browser_session.audio_switch_previous_epoch_id = (
                previous_active.epoch_id if previous_active is not None else browser_session.audio_switch_previous_epoch_id
            )
            browser_session.audio_switch_previous_audio_stream_index = browser_session.active_audio_stream_index
            browser_session.active_audio_stream_index = replacement_epoch.audio_stream_index
            browser_session.selected_audio_stream_index = replacement_epoch.audio_stream_index
            browser_session.pending_audio_stream_index = None
            browser_session.audio_switch_state = "committing"
            browser_session.audio_switch_error = None
            self._clear_route2_audio_switch_candidate_locked(browser_session)
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

    def _route2_downshift_replacement_ready_locked(
        self,
        session: MobilePlaybackSession,
        replacement_epoch: PlaybackEpoch,
    ) -> bool:
        if replacement_epoch.replacement_reason not in {"maintenance_downshift", "adaptive_resupply_boost"}:
            return False
        if replacement_epoch.state in {"failed", "ended", "draining"}:
            return False
        if replacement_epoch.last_error:
            return False
        if not replacement_epoch.init_published:
            return False
        if replacement_epoch.contiguous_published_through_segment is None:
            return False
        if self._stalled_recovery_needed(session) or self._starvation_risk(session):
            return False
        reserve_status = self._route2_bad_condition_reserve_status_locked(session, replacement_epoch)
        if bool(reserve_status.get("bad_condition_reserve_required")) and not bool(reserve_status.get("reserve_satisfied")):
            return False
        attach_ready = self._route2_epoch_startup_attach_ready_locked(session, replacement_epoch)
        return self._guard_route2_full_attach_boundary_locked(
            session,
            replacement_epoch,
            attach_eligible=attach_ready,
            guard_path=(
                "adaptive_resupply_ready_check"
                if replacement_epoch.replacement_reason == "adaptive_resupply_boost"
                else "maintenance_downshift_ready_check"
            ),
        )

    def _route2_audio_switch_replacement_ready_locked(
        self,
        session: MobilePlaybackSession,
        replacement_epoch: PlaybackEpoch,
    ) -> bool:
        if replacement_epoch.replacement_reason != "audio_track_switch":
            return False
        if replacement_epoch.state in {"failed", "ended", "draining"}:
            return False
        if replacement_epoch.last_error:
            return False
        if not replacement_epoch.init_published:
            return False
        if replacement_epoch.contiguous_published_through_segment is None:
            return False
        active_epoch = (
            session.browser_playback.epochs.get(session.browser_playback.active_epoch_id)
            if session.browser_playback.active_epoch_id
            else None
        )
        if active_epoch is None or active_epoch.state in {"failed", "ended"}:
            return False
        ready_end_seconds = self._route2_epoch_ready_end_seconds(session, replacement_epoch)
        remaining_presentation_seconds = max(
            0.0,
            float(session.duration_seconds or 0.0) - float(replacement_epoch.attach_position_seconds or 0.0),
        )
        required_runway = min(ROUTE2_AUDIO_SWITCH_READY_RUNWAY_SECONDS, remaining_presentation_seconds)
        if required_runway <= 0.0:
            return True
        return (
            ready_end_seconds - float(replacement_epoch.attach_position_seconds or 0.0)
        ) + 0.001 >= required_runway

    def _route2_downshift_abort_reason_locked(
        self,
        session: MobilePlaybackSession,
        replacement_epoch: PlaybackEpoch,
    ) -> str | None:
        if replacement_epoch.replacement_reason != "maintenance_downshift":
            return None
        if session.pending_target_seconds is not None:
            return "client_seek_during_downshift"
        if replacement_epoch.last_error or replacement_epoch.state == "failed":
            return "replacement_failed"
        snapshot = self._latest_route2_resource_snapshot_locked()
        pressure_abort_reason = self._route2_downshift_pressure_abort_reason_locked(session, snapshot)
        if pressure_abort_reason is not None:
            return pressure_abort_reason
        active_workloads = len(
            [
                record
                for record in self._route2_workers.values()
                if record.state in {"queued", "running"}
            ]
        )
        if active_workloads > 2:
            return "route2_workload_changed_during_downshift"
        return None

    def _abort_route2_downshift_replacement_locked(
        self,
        session: MobilePlaybackSession,
        replacement_epoch: PlaybackEpoch,
        *,
        reason: str,
    ) -> None:
        browser_session = session.browser_playback
        replacement_epoch.adaptive_downshift_aborted_reason = reason
        replacement_epoch.state = "failed" if replacement_epoch.state == "failed" else "ended"
        if replacement_epoch.adaptive_reclaim_request_id:
            browser_session.adaptive_reclaim_state = "reclaim_aborted"
            browser_session.adaptive_reclaim_abort_reason = reason
            browser_session.adaptive_reclaim_failed_reason = reason
            browser_session.adaptive_reclaim_completed_at = utcnow_iso()
            browser_session.adaptive_reclaim_retry_count += 1
            browser_session.adaptive_reclaim_retry_not_before_ts = (
                time.time() + ROUTE2_ADAPTIVE_RECLAIM_RETRY_BACKOFF_SECONDS
            )
            browser_session.adaptive_reclaim_retry_blocker = "adaptive_reclaim_retry_cooldown_active"
            browser_session.adaptive_reclaim_blockers = list(
                dict.fromkeys([*browser_session.adaptive_reclaim_blockers, reason])
            )
        browser_session.adaptive_downshift_last_abort_reason = reason
        browser_session.adaptive_downshift_retry_count += 1
        browser_session.adaptive_downshift_retry_not_before_ts = (
            time.time() + ROUTE2_ADAPTIVE_DOWNSHIFT_RETRY_BACKOFF_SECONDS
        )
        browser_session.adaptive_downshift_retry_blocker = "adaptive_downshift_retry_cooldown_active"
        active_epoch = (
            browser_session.epochs.get(browser_session.active_epoch_id)
            if browser_session.active_epoch_id
            else None
        )
        if active_epoch is not None:
            active_epoch.adaptive_downshift_aborted_reason = reason
            self._write_route2_epoch_metadata_locked(active_epoch)
        self._log_route2_event(
            "maintenance_downshift_replacement_aborted",
            session=session,
            epoch=replacement_epoch,
            level=logging.WARNING,
            reason=reason,
            pressure_abort_reason=browser_session.adaptive_downshift_pressure_abort_reason,
            pressure_snapshot=browser_session.adaptive_downshift_pressure_snapshot,
            adaptive_downshift_retry_count=browser_session.adaptive_downshift_retry_count,
            adaptive_downshift_retry_not_before_seconds=ROUTE2_ADAPTIVE_DOWNSHIFT_RETRY_BACKOFF_SECONDS,
        )
        self._discard_route2_epoch_locked(session, replacement_epoch.epoch_id)

    def _measure_route2_reclaim_capacity_after_downshift_locked(
        self,
        session: MobilePlaybackSession,
        replacement_epoch: PlaybackEpoch,
    ) -> None:
        browser_session = session.browser_playback
        if not replacement_epoch.adaptive_reclaim_request_id:
            return
        before_headroom = int(browser_session.adaptive_reclaim_cpu_headroom_before or 0)
        after_measurement = self._route2_reclaim_capacity_measurement_locked(user_id=session.user_id)
        after_headroom = int(after_measurement["route2_headroom"])
        expected_release = int(browser_session.adaptive_reclaim_released_threads_expected or 0)
        measured_release = max(0, after_headroom - before_headroom)
        before_route2_cpu = browser_session.adaptive_reclaim_route2_cpu_cores_used_before
        after_route2_cpu = after_measurement["route2_cpu_cores_used"]
        measured_released_cpu = (
            max(0.0, float(before_route2_cpu) - float(after_route2_cpu))
            if before_route2_cpu is not None and after_route2_cpu is not None
            else None
        )
        browser_session.adaptive_reclaim_switched_at = replacement_epoch.adaptive_downshift_switched_at
        browser_session.adaptive_reclaim_measured_at = utcnow_iso()
        browser_session.adaptive_reclaim_cpu_headroom_after = after_headroom
        browser_session.adaptive_reclaim_route2_headroom_after = after_headroom
        browser_session.adaptive_reclaim_released_threads_measured = measured_release
        browser_session.adaptive_reclaim_released_cpu_cores_measured = measured_released_cpu
        browser_session.adaptive_reclaim_route2_cpu_cores_used_after = after_route2_cpu  # type: ignore[assignment]
        browser_session.adaptive_reclaim_user_cpu_cores_used_after = after_measurement["user_cpu_cores_used"]  # type: ignore[assignment]
        browser_session.adaptive_reclaim_host_cpu_used_cores_after = after_measurement["host_cpu_used_cores"]  # type: ignore[assignment]
        browser_session.adaptive_reclaim_host_cpu_spare_cores_after = after_measurement["host_cpu_spare_cores"]  # type: ignore[assignment]
        browser_session.adaptive_reclaim_memory_pressure_after = after_measurement["memory_pressure"]  # type: ignore[assignment]
        browser_session.adaptive_reclaim_external_pressure_after = after_measurement["external_pressure"]  # type: ignore[assignment]
        required_capacity = self._route2_admission_min_worker_threads() + ROUTE2_ADAPTIVE_RECLAIM_MEASURED_HEADROOM_MARGIN_THREADS
        snapshot_fresh = bool(after_measurement["snapshot_fresh"])
        capacity_sufficient = bool(snapshot_fresh and after_headroom >= required_capacity)
        browser_session.adaptive_reclaim_capacity_sufficient_for_consumer = capacity_sufficient
        browser_session.adaptive_reclaim_completed_at = utcnow_iso()
        if capacity_sufficient:
            browser_session.adaptive_reclaim_state = "capacity_available"
            browser_session.adaptive_reclaim_abort_reason = None
            browser_session.adaptive_reclaim_failed_reason = None
            browser_session.adaptive_reclaim_blockers = []
        else:
            browser_session.adaptive_reclaim_state = "capacity_insufficient"
            browser_session.adaptive_reclaim_abort_reason = "insufficient_measured_capacity"
            browser_session.adaptive_reclaim_failed_reason = "insufficient_measured_capacity"
            browser_session.adaptive_reclaim_blockers = [
                "insufficient_measured_capacity",
                f"expected_release_{expected_release}",
                f"measured_release_{measured_release}",
            ]
            if not snapshot_fresh:
                browser_session.adaptive_reclaim_blockers.append("resource_snapshot_missing_or_stale")

    def _measure_route2_reclaim_capacity_after_resupply_locked(
        self,
        session: MobilePlaybackSession,
    ) -> None:
        browser_session = session.browser_playback
        if not browser_session.adaptive_reclaim_request_id:
            return
        after_measurement = self._route2_reclaim_capacity_measurement_locked(user_id=session.user_id)
        after_headroom = int(after_measurement["route2_headroom"])
        required_capacity = self._route2_admission_min_worker_threads() + ROUTE2_ADAPTIVE_RECLAIM_MEASURED_HEADROOM_MARGIN_THREADS
        snapshot_fresh = bool(after_measurement["snapshot_fresh"])
        browser_session.adaptive_reclaim_measured_at = utcnow_iso()
        browser_session.adaptive_reclaim_cpu_headroom_after = after_headroom
        browser_session.adaptive_reclaim_route2_headroom_after = after_headroom
        browser_session.adaptive_reclaim_route2_cpu_cores_used_after = after_measurement["route2_cpu_cores_used"]  # type: ignore[assignment]
        browser_session.adaptive_reclaim_user_cpu_cores_used_after = after_measurement["user_cpu_cores_used"]  # type: ignore[assignment]
        browser_session.adaptive_reclaim_host_cpu_used_cores_after = after_measurement["host_cpu_used_cores"]  # type: ignore[assignment]
        browser_session.adaptive_reclaim_host_cpu_spare_cores_after = after_measurement["host_cpu_spare_cores"]  # type: ignore[assignment]
        browser_session.adaptive_reclaim_memory_pressure_after = after_measurement["memory_pressure"]  # type: ignore[assignment]
        browser_session.adaptive_reclaim_external_pressure_after = after_measurement["external_pressure"]  # type: ignore[assignment]
        browser_session.adaptive_reclaim_capacity_sufficient_for_consumer = bool(
            snapshot_fresh and after_headroom >= required_capacity
        )
        if browser_session.adaptive_reclaim_capacity_sufficient_for_consumer:
            browser_session.adaptive_reclaim_failed_reason = None
            browser_session.adaptive_reclaim_abort_reason = None
        else:
            browser_session.adaptive_reclaim_failed_reason = "insufficient_measured_capacity_after_resupply"

    def _route2_downshift_action_would_interrupt_active_client_locked(
        self,
        session: MobilePlaybackSession,
    ) -> bool:
        browser_session = session.browser_playback
        if browser_session.engine_mode != "route2":
            return False
        if session.preparation_parked:
            return False
        if session.pending_target_seconds is not None:
            return False
        if session.lifecycle_state not in {"attached", "playing", "resuming"}:
            return False
        return bool(session.client_is_playing)

    def _create_route2_downshift_replacement_epoch_locked(
        self,
        session: MobilePlaybackSession,
        active_epoch: PlaybackEpoch,
        *,
        target_threads: int,
        reclaim_request_id: str | None = None,
        reclaim_consumer_session_id: str | None = None,
        reclaim_consumer_user_id: int | None = None,
        reclaim_consumer_media_item_id: int | None = None,
        reclaim_consumer_reason: str | None = None,
    ) -> PlaybackEpoch | None:
        browser_session = session.browser_playback
        if browser_session.replacement_epoch_id:
            return None
        if self._route2_downshift_retry_cap_remaining(browser_session) <= 0:
            browser_session.adaptive_downshift_retry_blocker = "adaptive_downshift_retry_cap_exceeded"
            active_epoch.adaptive_downshift_aborted_reason = "adaptive_downshift_retry_cap_exceeded"
            self._write_route2_epoch_metadata_locked(active_epoch)
            return None
        if self._route2_downshift_action_would_interrupt_active_client_locked(session):
            # Active ffmpeg thread count cannot be changed in-place safely. Starting
            # a replacement worker for a visible foreground session can steal CPU/IO
            # and cause jitter, so foreground downshift stays recommendation-only.
            active_epoch.adaptive_downshift_aborted_reason = None
            self._write_route2_epoch_metadata_locked(active_epoch)
            return None
        retry_seconds = self._route2_downshift_retry_seconds_remaining(browser_session)
        if retry_seconds is not None:
            browser_session.adaptive_downshift_retry_blocker = "adaptive_downshift_retry_cooldown_active"
            return None
        target_threads = max(int(self.settings.route2_min_worker_threads), int(target_threads))
        effective_playhead = self._route2_effective_playhead_seconds_locked(session, active_epoch)
        replacement_epoch = self._build_route2_epoch_locked(
            session,
            target_position_seconds_override=effective_playhead,
        )
        now = utcnow_iso()
        replacement_epoch.replacement_reason = "maintenance_downshift"
        replacement_epoch.maintenance_downshift_target_threads = target_threads
        replacement_epoch.maintenance_downshift_source_epoch_id = active_epoch.epoch_id
        replacement_epoch.adaptive_downshift_transition_started_at = now
        replacement_epoch.adaptive_reclaim_request_id = reclaim_request_id
        replacement_epoch.adaptive_reclaim_consumer_session_id = reclaim_consumer_session_id
        replacement_epoch.adaptive_reclaim_consumer_user_id = reclaim_consumer_user_id
        replacement_epoch.adaptive_reclaim_consumer_media_item_id = reclaim_consumer_media_item_id
        replacement_epoch.adaptive_reclaim_consumer_reason = reclaim_consumer_reason
        active_epoch.adaptive_downshift_aborted_reason = None
        browser_session.adaptive_downshift_retry_blocker = None
        browser_session.adaptive_downshift_pressure_abort_reason = None
        self._route2_reset_downshift_pressure_tracker_locked(browser_session)
        browser_session.replacement_epoch_id = replacement_epoch.epoch_id
        browser_session.epochs[replacement_epoch.epoch_id] = replacement_epoch
        self._ensure_route2_epoch_workspace_locked(replacement_epoch)
        self._log_route2_event(
            "maintenance_downshift_replacement_created",
            session=session,
            epoch=replacement_epoch,
            source_epoch_id=active_epoch.epoch_id,
            target_threads=target_threads,
            effective_playhead_seconds=round(effective_playhead, 2),
        )
        return replacement_epoch

    def _route2_downshift_promotion_would_interrupt_active_client_locked(
        self,
        session: MobilePlaybackSession,
        replacement_epoch: PlaybackEpoch,
    ) -> bool:
        if replacement_epoch.replacement_reason != "maintenance_downshift":
            return False
        if replacement_epoch.adaptive_reclaim_request_id:
            return False
        return self._route2_downshift_action_would_interrupt_active_client_locked(session)

    def _promote_route2_downshift_replacement_epoch_locked(
        self,
        session: MobilePlaybackSession,
        replacement_epoch: PlaybackEpoch,
    ) -> bool:
        browser_session = session.browser_playback
        previous_active = (
            browser_session.epochs.get(browser_session.active_epoch_id)
            if browser_session.active_epoch_id
            else None
        )
        if previous_active is replacement_epoch:
            return True
        if self._route2_downshift_promotion_would_interrupt_active_client_locked(session, replacement_epoch):
            replacement_epoch.state = "attach_ready"
            self._write_route2_epoch_metadata_locked(replacement_epoch)
            self._log_route2_event(
                "maintenance_downshift_promotion_deferred_for_active_playback",
                session=session,
                epoch=replacement_epoch,
                lifecycle_state=session.lifecycle_state,
                client_is_playing=session.client_is_playing,
            )
            return False
        next_revision = max(1, int(browser_session.attach_revision or 0) + 1)
        replacement_epoch.adaptive_downshift_switched_at = utcnow_iso()
        replacement_epoch.state = "attach_ready"
        browser_session.adaptive_downshift_retry_not_before_ts = 0.0
        browser_session.adaptive_downshift_retry_blocker = None
        browser_session.adaptive_downshift_pressure_abort_reason = None
        self._route2_reset_downshift_pressure_tracker_locked(browser_session)
        browser_session.active_epoch_id = replacement_epoch.epoch_id
        browser_session.replacement_epoch_id = None
        self._issue_route2_attach_revision_locked(
            session,
            next_revision=next_revision,
            reason="maintenance_downshift_ready",
            epoch=replacement_epoch,
        )
        if previous_active is not None:
            self._mark_route2_epoch_draining_locked(
                session,
                previous_active,
                reason="maintenance_downshift_switched",
                required_client_revision=browser_session.attach_revision,
            )
            self._write_route2_epoch_metadata_locked(previous_active)
            self._terminate_route2_epoch_locked(
                previous_active,
                session=session,
                final_state="stopped",
                remove_worker_record=False,
            )
        if replacement_epoch.adaptive_reclaim_request_id:
            browser_session.adaptive_reclaim_state = "donor_downshift_switched"
            browser_session.adaptive_reclaim_downshift_replacement_epoch_id = replacement_epoch.epoch_id
            browser_session.adaptive_reclaim_downshift_replacement_worker_id = replacement_epoch.active_worker_id
            browser_session.adaptive_reclaim_state = "measuring_capacity"
            self._measure_route2_reclaim_capacity_after_downshift_locked(session, replacement_epoch)
        self._write_route2_epoch_metadata_locked(replacement_epoch)
        self._log_route2_event(
            "maintenance_downshift_replacement_promoted",
            session=session,
            epoch=replacement_epoch,
            previous_epoch_id=previous_active.epoch_id if previous_active is not None else None,
            target_threads=replacement_epoch.maintenance_downshift_target_threads,
        )
        return True

    def _route2_resupply_health_after_switch_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        record: Route2WorkerRecord | None,
    ) -> dict[str, object]:
        (
            _published_end_seconds,
            _effective_playhead_seconds,
            runway_seconds,
            supply_rate_x,
            observation_seconds,
            manifest_complete,
            _refill_in_progress,
        ) = self._route2_runtime_supply_metrics_locked(session, epoch)
        reserve_status = self._route2_bad_condition_reserve_status_locked(session, epoch)
        blockers: list[str] = []
        if not manifest_complete and observation_seconds < ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS:
            blockers.append("telemetry_immature")
        if not manifest_complete and supply_rate_x < ROUTE2_CLOSED_LOOP_HEALTH_FLOOR_RATE_X:
            blockers.append("supply_below_health_floor")
        if not manifest_complete and runway_seconds < self._route2_closed_loop_required_runway_seconds(
            record.playback_mode if record is not None else session.browser_playback.playback_mode
        ):
            blockers.append("runway_below_protected_target")
        if self._stalled_recovery_needed(session):
            blockers.append("stalled_recovery_needed")
        if self._starvation_risk(session):
            blockers.append("starvation_risk")
        if bool(reserve_status.get("bad_condition_reserve_required")) and not bool(reserve_status.get("reserve_satisfied")):
            blockers.append("active_bad_condition_reserve_protection")
        safe = not blockers
        return {
            "safe": safe,
            "blockers": blockers,
            "supply_rate_x": round(float(supply_rate_x), 3),
            "runway_seconds": round(float(runway_seconds), 3),
            "observation_seconds": round(float(observation_seconds), 3),
            "manifest_complete": bool(manifest_complete),
            "reserve_satisfied": bool(reserve_status.get("reserve_satisfied")),
        }

    def _create_route2_resupply_replacement_epoch_locked(
        self,
        session: MobilePlaybackSession,
        active_epoch: PlaybackEpoch,
        *,
        target_threads: int,
    ) -> PlaybackEpoch | None:
        browser_session = session.browser_playback
        if browser_session.replacement_epoch_id:
            return None
        target_threads = max(int(self.settings.route2_min_worker_threads), int(target_threads))
        effective_playhead = self._route2_effective_playhead_seconds_locked(session, active_epoch)
        replacement_epoch = self._build_route2_epoch_locked(
            session,
            target_position_seconds_override=effective_playhead,
        )
        now = utcnow_iso()
        request_id = browser_session.adaptive_resupply_request_id or f"resupply-{uuid.uuid4().hex}"
        replacement_epoch.replacement_reason = "adaptive_resupply_boost"
        replacement_epoch.adaptive_resupply_request_id = request_id
        replacement_epoch.adaptive_resupply_original_reclaim_request_id = (
            browser_session.adaptive_reclaim_request_id
        )
        replacement_epoch.adaptive_resupply_target_threads = target_threads
        replacement_epoch.adaptive_resupply_source_epoch_id = active_epoch.epoch_id
        replacement_epoch.adaptive_resupply_started_at = now
        replacement_epoch.adaptive_resupply_switched_at = None
        replacement_epoch.adaptive_resupply_abort_reason = None
        browser_session.adaptive_resupply_request_id = request_id
        browser_session.adaptive_resupply_original_reclaim_request_id = (
            browser_session.adaptive_reclaim_request_id
        )
        browser_session.adaptive_resupply_replacement_epoch_id = replacement_epoch.epoch_id
        browser_session.adaptive_resupply_replacement_worker_id = replacement_epoch.active_worker_id
        browser_session.adaptive_resupply_started_at = now
        browser_session.adaptive_resupply_switched_at = None
        browser_session.adaptive_resupply_measured_at = None
        browser_session.adaptive_resupply_abort_reason = None
        browser_session.adaptive_resupply_state = "boost_replacement_starting"
        browser_session.adaptive_resupply_stabilization_until_ts = 0.0
        browser_session.adaptive_resupply_stabilization_until = None
        browser_session.adaptive_resupply_stabilization_reason = None
        browser_session.priority_reexpand_pending = True
        browser_session.donor_protection_active = True
        browser_session.replacement_epoch_id = replacement_epoch.epoch_id
        browser_session.epochs[replacement_epoch.epoch_id] = replacement_epoch
        self._ensure_route2_epoch_workspace_locked(replacement_epoch)
        self._log_route2_event(
            "adaptive_resupply_replacement_created",
            session=session,
            epoch=replacement_epoch,
            source_epoch_id=active_epoch.epoch_id,
            target_threads=target_threads,
            effective_playhead_seconds=round(effective_playhead, 2),
            adaptive_resupply_request_id=request_id,
            original_reclaim_request_id=browser_session.adaptive_reclaim_request_id,
        )
        return replacement_epoch

    def _route2_resupply_abort_reason_locked(
        self,
        session: MobilePlaybackSession,
        replacement_epoch: PlaybackEpoch,
    ) -> str | None:
        if replacement_epoch.replacement_reason != "adaptive_resupply_boost":
            return None
        if session.pending_target_seconds is not None:
            return "client_seek_during_resupply"
        if replacement_epoch.last_error or replacement_epoch.state == "failed":
            return "replacement_failed"
        snapshot = self._latest_route2_resource_snapshot_locked()
        pressure_abort_reason = self._route2_downshift_pressure_abort_reason_locked(session, snapshot)
        if pressure_abort_reason is not None:
            return pressure_abort_reason.replace("downshift", "resupply")
        active_workloads = len(
            [
                record
                for record in self._route2_workers.values()
                if record.state in {"queued", "running"}
            ]
        )
        if active_workloads > 2:
            return "route2_workload_changed_during_resupply"
        return None

    def _abort_route2_resupply_replacement_locked(
        self,
        session: MobilePlaybackSession,
        replacement_epoch: PlaybackEpoch,
        *,
        reason: str,
    ) -> None:
        browser_session = session.browser_playback
        replacement_epoch.adaptive_resupply_abort_reason = reason
        replacement_epoch.state = "failed" if replacement_epoch.state == "failed" else "ended"
        browser_session.adaptive_resupply_state = "aborted"
        browser_session.adaptive_resupply_abort_reason = reason
        browser_session.adaptive_resupply_blockers = list(
            dict.fromkeys([*browser_session.adaptive_resupply_blockers, reason])
        )
        browser_session.priority_reexpand_pending = True
        browser_session.donor_protection_active = True
        browser_session.admission_blocked_by_resupply = True
        self._log_route2_event(
            "adaptive_resupply_replacement_aborted",
            session=session,
            epoch=replacement_epoch,
            level=logging.WARNING,
            reason=reason,
            adaptive_resupply_request_id=replacement_epoch.adaptive_resupply_request_id,
            pressure_abort_reason=browser_session.adaptive_downshift_pressure_abort_reason,
            pressure_snapshot=browser_session.adaptive_downshift_pressure_snapshot,
        )
        self._discard_route2_epoch_locked(session, replacement_epoch.epoch_id)

    def _promote_route2_resupply_replacement_epoch_locked(
        self,
        session: MobilePlaybackSession,
        replacement_epoch: PlaybackEpoch,
    ) -> None:
        browser_session = session.browser_playback
        previous_active = (
            browser_session.epochs.get(browser_session.active_epoch_id)
            if browser_session.active_epoch_id
            else None
        )
        if previous_active is replacement_epoch:
            return
        next_revision = max(1, int(browser_session.attach_revision or 0) + 1)
        switched_at = utcnow_iso()
        replacement_epoch.adaptive_resupply_switched_at = switched_at
        replacement_epoch.state = "attach_ready"
        browser_session.active_epoch_id = replacement_epoch.epoch_id
        browser_session.replacement_epoch_id = None
        browser_session.adaptive_resupply_state = "switched"
        browser_session.adaptive_resupply_switched_at = switched_at
        browser_session.adaptive_resupply_replacement_epoch_id = replacement_epoch.epoch_id
        browser_session.adaptive_resupply_replacement_worker_id = replacement_epoch.active_worker_id
        self._route2_reset_downshift_pressure_tracker_locked(browser_session)
        self._issue_route2_attach_revision_locked(
            session,
            next_revision=next_revision,
            reason="adaptive_resupply_ready",
            epoch=replacement_epoch,
        )
        if previous_active is not None:
            self._mark_route2_epoch_draining_locked(
                session,
                previous_active,
                reason="adaptive_resupply_switched",
                required_client_revision=browser_session.attach_revision,
            )
            self._write_route2_epoch_metadata_locked(previous_active)
            self._terminate_route2_epoch_locked(
                previous_active,
                session=session,
                final_state="stopped",
                remove_worker_record=False,
            )
        replacement_record = (
            self._route2_workers.get(replacement_epoch.active_worker_id)
            if replacement_epoch.active_worker_id
            else None
        )
        health = self._route2_resupply_health_after_switch_locked(session, replacement_epoch, replacement_record)
        browser_session.adaptive_resupply_measured_at = utcnow_iso()
        browser_session.donor_health_after_resupply = health
        if bool(health.get("safe")):
            self._activate_route2_resupply_stabilization_locked(
                browser_session,
                target_threads=replacement_epoch.adaptive_resupply_target_threads,
            )
            browser_session.adaptive_resupply_state = "donor_safe"
            browser_session.adaptive_resupply_needed = False
            browser_session.adaptive_resupply_reason = None
            browser_session.adaptive_resupply_blockers = []
            browser_session.adaptive_resupply_abort_reason = None
            browser_session.priority_reexpand_pending = False
            browser_session.priority_reexpand_reason = None
            browser_session.donor_protection_active = False
            browser_session.admission_blocked_by_resupply = False
        else:
            browser_session.adaptive_resupply_stabilization_until_ts = 0.0
            browser_session.adaptive_resupply_stabilization_until = None
            browser_session.adaptive_resupply_stabilization_reason = None
            blockers = [str(item) for item in health.get("blockers") or []]
            browser_session.adaptive_resupply_state = "capacity_insufficient_after_resupply"
            browser_session.adaptive_resupply_blockers = blockers
            browser_session.priority_reexpand_pending = True
            browser_session.priority_reexpand_reason = browser_session.adaptive_resupply_reason
            browser_session.donor_protection_active = True
            browser_session.admission_blocked_by_resupply = True
        self._measure_route2_reclaim_capacity_after_resupply_locked(session)
        self._write_route2_epoch_metadata_locked(replacement_epoch)
        self._log_route2_event(
            "adaptive_resupply_replacement_promoted",
            session=session,
            epoch=replacement_epoch,
            previous_epoch_id=previous_active.epoch_id if previous_active is not None else None,
            target_threads=replacement_epoch.adaptive_resupply_target_threads,
            health=health,
        )

    def _maybe_start_route2_resupply_locked(
        self,
        session: MobilePlaybackSession,
        active_epoch: PlaybackEpoch,
    ) -> PlaybackEpoch | None:
        browser_session = session.browser_playback
        if browser_session.replacement_epoch_id:
            return None
        record = self._route2_workers.get(active_epoch.active_worker_id) if active_epoch.active_worker_id else None
        if record is None:
            return None
        payload = self._route2_adaptive_resupply_payload_locked(session, active_epoch, record)
        self._apply_route2_reclaim_payload_to_record(record, payload)
        if not bool(payload.get("adaptive_resupply_needed")):
            return None
        if not bool(getattr(self.settings, "route2_adaptive_resupply_enabled", False)):
            return None
        blockers = [str(blocker) for blocker in payload.get("adaptive_resupply_blockers") or []]
        if blockers:
            return None
        target_threads = payload.get("adaptive_resupply_target_threads")
        if not isinstance(target_threads, int) or target_threads <= int(record.assigned_threads or 0):
            return None
        replacement_epoch = self._create_route2_resupply_replacement_epoch_locked(
            session,
            active_epoch,
            target_threads=target_threads,
        )
        if replacement_epoch is not None:
            browser_session.adaptive_resupply_state = "boost_replacement_warming"
            self._ensure_route2_epoch_workers_locked(session)
            self._dispatch_waiting_route2_workers_locked()
            browser_session.adaptive_resupply_replacement_worker_id = replacement_epoch.active_worker_id
            if replacement_epoch.active_worker_id:
                replacement_record = self._route2_workers.get(replacement_epoch.active_worker_id)
                if replacement_record is not None:
                    self._sync_route2_worker_record_locked(replacement_record, session, replacement_epoch)
        return replacement_epoch

    def _maybe_start_route2_downshift_locked(
        self,
        session: MobilePlaybackSession,
        active_epoch: PlaybackEpoch,
    ) -> PlaybackEpoch | None:
        if not bool(getattr(self.settings, "route2_adaptive_downshift_enabled", False)):
            return None
        if session.browser_playback.priority_reexpand_pending:
            return None
        if session.browser_playback.replacement_epoch_id:
            return None
        record = self._route2_workers.get(active_epoch.active_worker_id) if active_epoch.active_worker_id else None
        if record is None:
            return None
        decision = self._evaluate_route2_closed_loop_dry_run_locked(session, active_epoch, record)
        payload = self._route2_adaptive_downshift_payload_locked(session, active_epoch, record, decision)
        self._apply_route2_downshift_payload_to_record(record, payload)
        if not bool(payload["downshift_safe_to_apply"]):
            return None
        target_threads = payload["adaptive_downshift_target_threads"]
        if not isinstance(target_threads, int) or target_threads <= 0:
            return None
        pending_reclaim = (
            self._route2_pending_reclaim_request_locked()
            if bool(getattr(self.settings, "route2_adaptive_reclaim_enabled", False))
            else None
        )
        reclaim_request_id = str(pending_reclaim.get("adaptive_reclaim_request_id")) if pending_reclaim else None
        reclaim_consumer_session_id = (
            str(pending_reclaim["adaptive_reclaim_consumer_session_id"])
            if pending_reclaim and pending_reclaim.get("adaptive_reclaim_consumer_session_id") is not None
            else None
        )
        reclaim_consumer_user_id = (
            int(pending_reclaim["adaptive_reclaim_consumer_user_id"])
            if pending_reclaim and pending_reclaim.get("adaptive_reclaim_consumer_user_id") is not None
            else None
        )
        reclaim_consumer_media_item_id = (
            int(pending_reclaim["adaptive_reclaim_consumer_media_item_id"])
            if pending_reclaim and pending_reclaim.get("adaptive_reclaim_consumer_media_item_id") is not None
            else None
        )
        reclaim_consumer_reason = (
            str(pending_reclaim.get("adaptive_reclaim_consumer_reason") or "admission_capacity_shortage")
            if pending_reclaim
            else None
        )
        if pending_reclaim is not None:
            before_measurement = self._route2_reclaim_capacity_measurement_locked(user_id=session.user_id)
            expected_release = max(0, int(record.assigned_threads or 0) - int(target_threads))
            session.browser_playback.adaptive_reclaim_request_id = reclaim_request_id
            session.browser_playback.adaptive_reclaim_consumer_worker_id = str(
                pending_reclaim.get("adaptive_reclaim_consumer_worker_id") or ""
            )
            session.browser_playback.adaptive_reclaim_consumer_session_id = reclaim_consumer_session_id
            session.browser_playback.adaptive_reclaim_consumer_user_id = reclaim_consumer_user_id
            session.browser_playback.adaptive_reclaim_consumer_media_item_id = reclaim_consumer_media_item_id
            session.browser_playback.adaptive_reclaim_consumer_reason = reclaim_consumer_reason
            session.browser_playback.adaptive_reclaim_donor_worker_id = record.worker_id
            session.browser_playback.adaptive_reclaim_donor_session_id = session.session_id
            session.browser_playback.adaptive_reclaim_started_at = utcnow_iso()
            session.browser_playback.adaptive_reclaim_switched_at = None
            session.browser_playback.adaptive_reclaim_measured_at = None
            session.browser_playback.adaptive_reclaim_completed_at = None
            session.browser_playback.adaptive_reclaim_failed_reason = None
            session.browser_playback.adaptive_reclaim_released_threads_expected = expected_release
            session.browser_playback.adaptive_reclaim_released_threads_measured = None
            session.browser_playback.adaptive_reclaim_released_cpu_cores_measured = None
            session.browser_playback.adaptive_reclaim_cpu_headroom_before = int(before_measurement["route2_headroom"])
            session.browser_playback.adaptive_reclaim_cpu_headroom_after = None
            session.browser_playback.adaptive_reclaim_route2_cpu_cores_used_before = before_measurement["route2_cpu_cores_used"]  # type: ignore[assignment]
            session.browser_playback.adaptive_reclaim_route2_cpu_cores_used_after = None
            session.browser_playback.adaptive_reclaim_user_cpu_cores_used_before = before_measurement["user_cpu_cores_used"]  # type: ignore[assignment]
            session.browser_playback.adaptive_reclaim_user_cpu_cores_used_after = None
            session.browser_playback.adaptive_reclaim_host_cpu_used_cores_before = before_measurement["host_cpu_used_cores"]  # type: ignore[assignment]
            session.browser_playback.adaptive_reclaim_host_cpu_used_cores_after = None
            session.browser_playback.adaptive_reclaim_host_cpu_spare_cores_before = before_measurement["host_cpu_spare_cores"]  # type: ignore[assignment]
            session.browser_playback.adaptive_reclaim_host_cpu_spare_cores_after = None
            session.browser_playback.adaptive_reclaim_route2_headroom_before = before_measurement["route2_headroom"]  # type: ignore[assignment]
            session.browser_playback.adaptive_reclaim_route2_headroom_after = None
            session.browser_playback.adaptive_reclaim_memory_pressure_before = before_measurement["memory_pressure"]  # type: ignore[assignment]
            session.browser_playback.adaptive_reclaim_memory_pressure_after = None
            session.browser_playback.adaptive_reclaim_external_pressure_before = before_measurement["external_pressure"]  # type: ignore[assignment]
            session.browser_playback.adaptive_reclaim_external_pressure_after = None
            session.browser_playback.adaptive_reclaim_capacity_sufficient_for_consumer = None
            session.browser_playback.adaptive_reclaim_state = "donor_downshift_starting"
            session.browser_playback.adaptive_reclaim_blockers = []
            session.browser_playback.adaptive_reclaim_abort_reason = None
        replacement_epoch = self._create_route2_downshift_replacement_epoch_locked(
            session,
            active_epoch,
            target_threads=target_threads,
            reclaim_request_id=reclaim_request_id,
            reclaim_consumer_session_id=reclaim_consumer_session_id,
            reclaim_consumer_user_id=reclaim_consumer_user_id,
            reclaim_consumer_media_item_id=reclaim_consumer_media_item_id,
            reclaim_consumer_reason=reclaim_consumer_reason,
        )
        if replacement_epoch is not None:
            if pending_reclaim is not None:
                session.browser_playback.adaptive_reclaim_downshift_replacement_epoch_id = replacement_epoch.epoch_id
                session.browser_playback.adaptive_reclaim_downshift_replacement_worker_id = replacement_epoch.active_worker_id
                session.browser_playback.adaptive_reclaim_state = "donor_downshift_warming"
                self._route2_pending_reclaim_request = None
            payload = self._route2_adaptive_downshift_payload_locked(session, active_epoch, record, decision)
            self._apply_route2_downshift_payload_to_record(record, payload)
        return replacement_epoch

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
        if session.preparation_parked:
            has_running = False
            for epoch_id in (session.browser_playback.active_epoch_id, session.browser_playback.replacement_epoch_id):
                if not epoch_id:
                    continue
                epoch = session.browser_playback.epochs.get(epoch_id)
                if epoch is not None and epoch.process and epoch.process.poll() is None:
                    has_running = True
                    break
            session.worker_state = "running" if has_running else "idle"
            session.queue_started_ts = None
            return
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
                _ffmpeg_audio_map(epoch.audio_stream_index),
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
                if epoch.replacement_reason == "audio_track_switch":
                    epoch.last_error = (
                        "FFmpeg command could not be built while preparing selected audio stream "
                        f"{epoch.audio_stream_index}: {_compact_error_text(exc)}"
                    )
                else:
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
                if epoch.replacement_reason == "audio_track_switch":
                    if source_input_error:
                        epoch.last_error = (
                            "Cloud/source input failed while preparing selected audio stream "
                            f"{epoch.audio_stream_index}: {_compact_error_text(source_input_error)}"
                        )
                    else:
                        stderr_hint = _compact_error_text(stderr_tail)
                        suffix = f": {stderr_hint}" if stderr_hint else f" (ffmpeg exited with code {return_code})"
                        epoch.last_error = (
                            f"FFmpeg failed while preparing selected audio stream {epoch.audio_stream_index}{suffix}"
                        )
                else:
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
                if replacement_epoch.replacement_reason == "audio_track_switch":
                    self._fail_route2_audio_switch_locked(
                        session,
                        replacement_epoch,
                        reason=failed_error or "Could not prepare the selected audio track",
                    )
                    self._discard_route2_epoch_locked(session, failed_epoch_id)
                    replacement_epoch = None
                elif replacement_epoch.replacement_reason == "maintenance_downshift":
                    self._abort_route2_downshift_replacement_locked(
                        session,
                        replacement_epoch,
                        reason="replacement_failed",
                    )
                    replacement_epoch = None
                elif replacement_epoch.replacement_reason == "adaptive_resupply_boost":
                    self._abort_route2_resupply_replacement_locked(
                        session,
                        replacement_epoch,
                        reason="replacement_failed",
                    )
                    replacement_epoch = None
                elif _is_non_retryable_cloud_source_error(failed_error):
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
                else:
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

        if replacement_epoch is None and active_epoch.state not in {"failed", "draining", "ended"}:
            replacement_epoch = self._maybe_start_route2_resupply_locked(session, active_epoch)
        if replacement_epoch is None and active_epoch.state not in {"failed", "draining", "ended"}:
            replacement_epoch = self._maybe_start_route2_downshift_locked(session, active_epoch)

        if replacement_epoch is not None and replacement_epoch.replacement_reason == "maintenance_downshift":
            abort_reason = self._route2_downshift_abort_reason_locked(session, replacement_epoch)
            if abort_reason is not None:
                self._abort_route2_downshift_replacement_locked(
                    session,
                    replacement_epoch,
                    reason=abort_reason,
                )
                replacement_epoch = None
            elif self._route2_downshift_replacement_ready_locked(session, replacement_epoch):
                promoted = self._promote_route2_downshift_replacement_epoch_locked(session, replacement_epoch)
                if promoted:
                    active_epoch = replacement_epoch
                    replacement_epoch = None
                    self._rebuild_route2_published_frontier_locked(active_epoch)
            else:
                if replacement_epoch.active_worker_id or (replacement_epoch.process and replacement_epoch.process.poll() is None):
                    replacement_epoch.state = "warming"
                elif replacement_epoch.state not in {"failed", "ended"}:
                    replacement_epoch.state = "starting"
                self._write_route2_epoch_metadata_locked(replacement_epoch)

        if replacement_epoch is not None and replacement_epoch.replacement_reason == "adaptive_resupply_boost":
            abort_reason = self._route2_resupply_abort_reason_locked(session, replacement_epoch)
            if abort_reason is not None:
                self._abort_route2_resupply_replacement_locked(
                    session,
                    replacement_epoch,
                    reason=abort_reason,
                )
                replacement_epoch = None
            elif self._route2_downshift_replacement_ready_locked(session, replacement_epoch):
                self._promote_route2_resupply_replacement_epoch_locked(session, replacement_epoch)
                active_epoch = replacement_epoch
                replacement_epoch = None
                self._rebuild_route2_published_frontier_locked(active_epoch)
            else:
                if replacement_epoch.active_worker_id or (replacement_epoch.process and replacement_epoch.process.poll() is None):
                    replacement_epoch.state = "warming"
                elif replacement_epoch.state not in {"failed", "ended"}:
                    replacement_epoch.state = "starting"
                self._write_route2_epoch_metadata_locked(replacement_epoch)

        if (
            replacement_epoch is not None
            and replacement_epoch.replacement_reason not in {"maintenance_downshift", "adaptive_resupply_boost"}
        ):
            if (
                replacement_epoch.replacement_reason == "audio_track_switch"
                and browser_session.audio_switch_candidate_expires_at_ts > 0
                and now_ts >= browser_session.audio_switch_candidate_expires_at_ts
                and browser_session.audio_switch_candidate_state != "committing"
            ):
                self._cancel_route2_audio_switch_candidate_locked(
                    session,
                    reason="Audio switch candidate expired before commit.",
                    failed=True,
                )
                replacement_epoch = None
            if (
                replacement_epoch is not None
                and replacement_epoch.replacement_reason == "audio_track_switch"
                and replacement_epoch.state == "ended"
            ):
                self._fail_route2_audio_switch_locked(
                    session,
                    replacement_epoch,
                    reason=replacement_epoch.last_error or "Selected audio track preparation ended before it was ready.",
                )
                self._discard_route2_epoch_locked(session, replacement_epoch.epoch_id)
                replacement_epoch = None
            if replacement_epoch is not None:
                if replacement_epoch.replacement_reason == "audio_track_switch":
                    replacement_attach_ready = self._route2_audio_switch_replacement_ready_locked(
                        session,
                        replacement_epoch,
                    )
                else:
                    replacement_attach_ready = self._route2_epoch_startup_attach_ready_locked(session, replacement_epoch)
                    replacement_attach_ready = self._guard_route2_full_attach_boundary_locked(
                        session,
                        replacement_epoch,
                        attach_eligible=replacement_attach_ready,
                        guard_path="replacement_promotion_check",
                    )
                if replacement_attach_ready:
                    if replacement_epoch.replacement_reason == "audio_track_switch":
                        if browser_session.audio_switch_candidate_state == "committing":
                            self._promote_route2_replacement_epoch_locked(session, replacement_epoch)
                            active_epoch = replacement_epoch
                            replacement_epoch = None
                            self._rebuild_route2_published_frontier_locked(active_epoch)
                            self._log_route2_event(
                                "audio_switch_commit_succeeded",
                                session=session,
                                epoch=active_epoch,
                                previous_epoch_id=browser_session.audio_switch_previous_epoch_id,
                                previous_audio_stream_index=browser_session.audio_switch_previous_audio_stream_index,
                                candidate_audio_stream_index=browser_session.active_audio_stream_index,
                            )
                        else:
                            replacement_epoch.state = "attach_ready"
                            browser_session.audio_switch_candidate_epoch_id = replacement_epoch.epoch_id
                            browser_session.audio_switch_candidate_stream_index = replacement_epoch.audio_stream_index
                            browser_session.audio_switch_candidate_state = "ready"
                            browser_session.audio_switch_candidate_error = None
                            if browser_session.audio_switch_candidate_ready_at_ts <= 0:
                                browser_session.audio_switch_candidate_ready_at_ts = now_ts
                            browser_session.audio_switch_state = "candidate_ready"
                            browser_session.audio_switch_error = None
                            browser_session.pending_audio_stream_index = replacement_epoch.audio_stream_index
                            self._write_route2_epoch_metadata_locked(replacement_epoch)
                            self._log_route2_event(
                                "audio_switch_candidate_ready",
                                session=session,
                                epoch=replacement_epoch,
                                active_epoch_id=browser_session.active_epoch_id,
                                previous_audio_stream_index=browser_session.active_audio_stream_index,
                                candidate_audio_stream_index=replacement_epoch.audio_stream_index,
                                ready_end_seconds=round(
                                    self._route2_epoch_ready_end_seconds(session, replacement_epoch),
                                    2,
                                ),
                            )
                    else:
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
                    if replacement_epoch.replacement_reason == "audio_track_switch":
                        browser_session.audio_switch_state = "candidate_preparing"
                        browser_session.audio_switch_candidate_state = "preparing"
                        browser_session.audio_switch_error = None
                        browser_session.audio_switch_candidate_error = None
                    else:
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
        if (
            browser_session.audio_switch_state == "committing"
            and browser_session.client_attach_revision >= browser_session.attach_revision
            and browser_session.replacement_epoch_id is None
        ):
            browser_session.audio_switch_state = "active"
            browser_session.audio_switch_error = None

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

    def _maybe_advance_native_hls_window_locked(self, session: MobilePlaybackSession) -> None:
        """Slide the active native-HLS manifest window when the playhead moves.

        Phase 2B: native_hls Safari sessions get a server-side sliding window so
        the manifest never exposes more than ``[anchor - 120, anchor + forward]``.
        This runs inside the heartbeat path. Normal forward window slides update
        the served manifest bytes and window diagnostics only; they must not bump
        attach_revision because that remounts native-HLS playback on iOS. Hls.js
        sessions are skipped because they prune client-side and a new manifest
        URL would just churn their MediaSource.
        """
        from .route2_native_hls_window import (  # local import: avoid eager dependency at module import time
            WINDOW_ANCHOR_DRIFT_REFRESH_SECONDS,
            WINDOW_EDGE_REFRESH_RUNWAY_SECONDS,
            compute_native_hls_window,
            is_native_hls_engine,
            resolve_window_anchor_seconds,
            should_refresh_native_hls_window,
        )

        if not is_native_hls_engine(session.selected_hls_engine):
            return
        if session.browser_playback.engine_mode != "route2":
            return
        active_epoch = (
            session.browser_playback.epochs.get(session.browser_playback.active_epoch_id)
            if session.browser_playback.active_epoch_id
            else None
        )
        if active_epoch is None:
            return
        # Only slide when the session is past initial preparation. While
        # preparing/recovering the orchestrator may issue its own attach
        # revisions; piling on would interleave reattach signals.
        if session.lifecycle_state not in {"attached", "playing"}:
            if not session.browser_playback.last_emitted_window_initialized:
                pass  # still allow first init below
            else:
                return
        # Compute the desired window from current session telemetry.
        strongest_anchor_position = max(
            session.client_current_time_seconds or 0.0,
            session.committed_playhead_seconds,
            session.actual_media_element_time_seconds,
            session.last_stable_position_seconds,
            session.target_position_seconds,
        )
        anchor = resolve_window_anchor_seconds(
            current_position_seconds=strongest_anchor_position,
            target_position_seconds=session.target_position_seconds,
            attach_position_seconds=active_epoch.attach_position_seconds,
        )
        window = compute_native_hls_window(
            anchor_seconds=anchor,
            duration_seconds=session.duration_seconds,
            buffer_tier=str(self._latest_buffer_tier_locked(session) or ""),
            playback_mode=session.browser_playback.playback_mode,
        )
        desired_start = float(window["active_window_start_seconds"])
        desired_end = float(window["active_window_end_seconds"])
        desired_anchor = float(window["active_window_anchor_seconds"])
        desired_back = float(window["active_window_back_seconds"])
        desired_forward = float(window["active_window_forward_seconds"])
        desired_tier = str(self._latest_buffer_tier_locked(session) or "")
        browser_session = session.browser_playback
        if not browser_session.last_emitted_window_initialized:
            browser_session.last_emitted_window_initialized = True
            browser_session.last_emitted_window_start_seconds = desired_start
            browser_session.last_emitted_window_end_seconds = desired_end
            browser_session.last_emitted_window_anchor_seconds = desired_anchor
            browser_session.last_emitted_window_back_seconds = desired_back
            browser_session.last_emitted_window_forward_seconds = desired_forward
            browser_session.last_emitted_window_buffer_tier = desired_tier
            browser_session.last_emitted_window_reason = "initial"
            browser_session.last_emitted_window_revision = max(
                browser_session.last_emitted_window_revision,
                browser_session.attach_revision,
            )
            return
        # Use a generous orchestrator-side anchor drift threshold so continuous
        # forward playback inside a healthy window does not churn revisions.
        forward_drift_threshold = max(
            WINDOW_ANCHOR_DRIFT_REFRESH_SECONDS,
            desired_forward * 0.5,
        )
        decision = should_refresh_native_hls_window(
            current_position_seconds=session.client_current_time_seconds,
            window_start_seconds=browser_session.last_emitted_window_start_seconds,
            window_end_seconds=browser_session.last_emitted_window_end_seconds,
            window_anchor_seconds=browser_session.last_emitted_window_anchor_seconds,
            buffer_tier_changed=(
                bool(desired_tier)
                and desired_tier != browser_session.last_emitted_window_buffer_tier
            ),
            active_forward_window_seconds=browser_session.last_emitted_window_forward_seconds,
            edge_runway_seconds=WINDOW_EDGE_REFRESH_RUNWAY_SECONDS,
            anchor_drift_seconds=forward_drift_threshold,
        )
        if not decision["should_refresh"]:
            return
        # Reason "anchor_drift" with a tiny forward delta is the chatty case;
        # apply a strict equality check so a noop slide never bumps revisions.
        start_changed = abs(desired_start - browser_session.last_emitted_window_start_seconds) > 0.5
        end_changed = abs(desired_end - browser_session.last_emitted_window_end_seconds) > 0.5
        tier_changed = desired_tier != browser_session.last_emitted_window_buffer_tier
        if not (start_changed or end_changed or tier_changed):
            browser_session.last_emitted_window_anchor_seconds = desired_anchor
            return
        browser_session.last_emitted_window_start_seconds = desired_start
        browser_session.last_emitted_window_end_seconds = desired_end
        browser_session.last_emitted_window_anchor_seconds = desired_anchor
        browser_session.last_emitted_window_back_seconds = desired_back
        browser_session.last_emitted_window_forward_seconds = desired_forward
        browser_session.last_emitted_window_buffer_tier = desired_tier
        browser_session.last_emitted_window_reason = str(decision.get("reason") or "slide")
        browser_session.last_emitted_window_revision += 1
        self._log_route2_event(
            "native_hls_window_slid",
            session=session,
            epoch=active_epoch,
            reason=browser_session.last_emitted_window_reason,
            active_window_revision=browser_session.last_emitted_window_revision,
            active_window_start_seconds=round(desired_start, 2),
            active_window_end_seconds=round(desired_end, 2),
            attach_revision=browser_session.attach_revision,
        )

    def _latest_buffer_tier_locked(self, session: MobilePlaybackSession) -> str | None:
        """Best-effort buffer-tier snapshot for native-HLS window sizing.

        The authoritative tier is recomputed inside ``_route2_snapshot_locked``;
        this lightweight read mirrors that derivation just for the window
        decision so we do not need a full snapshot before bumping revisions.
        """
        from .mobile_playback_buffer_contract import resolve_buffer_contract_fields

        try:
            fields = resolve_buffer_contract_fields(
                playback_mode=session.browser_playback.playback_mode,
                client_device_class=session.client_device_class,
            )
        except Exception:  # noqa: BLE001
            return None
        tier = fields.get("buffer_tier")
        return str(tier) if tier else None

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
        buffer_contract_fields = resolve_buffer_contract_fields(
            playback_mode=browser_session.playback_mode,
            client_device_class=session.client_device_class,
            required_startup_runway_seconds=(
                ROUTE2_FULL_FAST_START_RUNWAY_SECONDS
                if browser_session.playback_mode == "full"
                else ROUTE2_LITE_SLOW_START_RUNWAY_SECONDS
            ),
            lite_required_runway_seconds=ROUTE2_LITE_SLOW_START_RUNWAY_SECONDS,
            lite_required_runway_source="legacy_slow_path_45",
        )
        replacement_epoch = (
            browser_session.epochs.get(browser_session.replacement_epoch_id)
            if browser_session.replacement_epoch_id
            else None
        )
        audio_switch_replacement_epoch = (
            replacement_epoch
            if replacement_epoch is not None and replacement_epoch.replacement_reason == "audio_track_switch"
            else None
        )
        audio_switch_replacement_ready_end_seconds = (
            self._route2_epoch_ready_end_seconds(session, audio_switch_replacement_epoch)
            if audio_switch_replacement_epoch is not None
            else None
        )
        audio_switch_replacement_audio_stream_index = (
            audio_switch_replacement_epoch.audio_stream_index
            if audio_switch_replacement_epoch is not None
            else None
        )
        audio_switch_replacement_audio_map = (
            f"0:{int(audio_switch_replacement_audio_stream_index)}?"
            if audio_switch_replacement_audio_stream_index is not None
            else None
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
            "lite_undersupply_runway_seconds": None,
            "lite_undersupply_detected": False,
            "lite_undersupply_reason": None,
            "lite_required_runway_seconds": (
                ROUTE2_LITE_SLOW_START_RUNWAY_SECONDS if browser_session.playback_mode == "lite" else None
            ),
            "lite_required_runway_source": "legacy_slow_path_45" if browser_session.playback_mode == "lite" else None,
            **buffer_contract_fields,
            "selected_hls_engine": session.selected_hls_engine,
            "client_buffered_ahead_seconds": round(float(session.client_buffered_ahead_seconds), 2)
            if session.client_buffered_ahead_seconds is not None
            else None,
            "client_target_forward_buffer_seconds": round(float(session.client_target_forward_buffer_seconds), 2)
            if session.client_target_forward_buffer_seconds is not None
            else None,
            "client_ready_state": session.client_ready_state,
            "client_network_state": session.client_network_state,
            "client_current_time_seconds": round(float(session.client_current_time_seconds), 2)
            if session.client_current_time_seconds is not None
            else None,
            "client_time_advancing": session.client_time_advancing,
            "client_playback_stall_reason": session.client_playback_stall_reason,
            "hls_js_config": session.hls_js_config,
            "attach_revision": browser_session.attach_revision,
            "client_attach_revision": browser_session.client_attach_revision,
            "active_epoch_id": browser_session.active_epoch_id,
            "replacement_epoch_id": browser_session.replacement_epoch_id,
            "active_manifest_url": None,
            "attach_position_seconds": round(session.target_position_seconds, 2),
            "attach_ready": False,
            "browser_session_state": self._browser_session_state(session),
            "active_epoch_state": active_epoch.state if active_epoch is not None else None,
            "selected_audio_stream_index": browser_session.selected_audio_stream_index,
            "active_audio_stream_index": browser_session.active_audio_stream_index,
            "pending_audio_stream_index": browser_session.pending_audio_stream_index,
            "audio_switch_state": browser_session.audio_switch_state,
            "audio_switch_error": browser_session.audio_switch_error,
            "audio_switch_replacement_epoch_id": (
                audio_switch_replacement_epoch.epoch_id if audio_switch_replacement_epoch is not None else None
            ),
            "audio_switch_replacement_state": (
                audio_switch_replacement_epoch.state if audio_switch_replacement_epoch is not None else None
            ),
            "audio_switch_replacement_reason": (
                audio_switch_replacement_epoch.replacement_reason if audio_switch_replacement_epoch is not None else None
            ),
            "audio_switch_replacement_audio_stream_index": audio_switch_replacement_audio_stream_index,
            "audio_switch_replacement_audio_map": audio_switch_replacement_audio_map,
            "audio_switch_replacement_last_error": (
                audio_switch_replacement_epoch.last_error if audio_switch_replacement_epoch is not None else None
            ),
            "audio_switch_replacement_ready_end_seconds": (
                round(float(audio_switch_replacement_ready_end_seconds), 2)
                if audio_switch_replacement_ready_end_seconds is not None
                else None
            ),
            "audio_switch_replacement_attach_position_seconds": (
                round(float(audio_switch_replacement_epoch.attach_position_seconds), 2)
                if audio_switch_replacement_epoch is not None
                else None
            ),
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

    def _maybe_park_backgrounded_route2_session_locked(
        self,
        session: MobilePlaybackSession,
        *,
        now_ts: float,
    ) -> None:
        if session.browser_playback.engine_mode != "route2":
            return
        if session.lifecycle_state != "background-suspended":
            return
        if session.backgrounded_at_ts <= 0:
            session.backgrounded_at_ts = now_ts
            return
        if now_ts - session.backgrounded_at_ts < BACKGROUND_PREPARATION_PARK_SECONDS:
            return
        if session.preparation_parked:
            return
        session.preparation_parked = True
        session.preparation_parked_at_ts = now_ts
        session.lifecycle_state = "background-parked"
        running_worker_stop_requested = False
        session.worker_state = "idle"
        session.queue_started_ts = None
        for record in self._route2_workers.values():
            if record.session_id != session.session_id:
                continue
            if record.state == "queued":
                record.state = "paused"
            elif record.state == "running":
                record.stop_requested = True
                record.state = "stopping"
                running_worker_stop_requested = True
            epoch = session.browser_playback.epochs.get(record.epoch_id)
            if epoch is not None:
                epoch.stop_requested = True
        if running_worker_stop_requested:
            session.worker_state = "stopping"
        self._log_route2_event(
            "background_preparation_parked",
            session=session,
            backgrounded_seconds=round(now_ts - session.backgrounded_at_ts, 2),
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
                    """,  # nosec B608 - placeholders generated from managed session user_ids
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
                    """,  # nosec B608 - placeholders generated from managed auth_session_ids
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
                if session.preparation_parked:
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
                replacement_target_threads = (
                    int(epoch.maintenance_downshift_target_threads)
                    if epoch.replacement_reason == "maintenance_downshift"
                    and epoch.maintenance_downshift_target_threads is not None
                    else int(epoch.adaptive_resupply_target_threads)
                    if epoch.replacement_reason == "adaptive_resupply_boost"
                    and epoch.adaptive_resupply_target_threads is not None
                    else None
                )
                if replacement_target_threads is not None:
                    if (
                        available_total_threads < replacement_target_threads
                        or user_remaining_threads < replacement_target_threads
                    ):
                        if epoch.replacement_reason == "adaptive_resupply_boost":
                            self._abort_route2_resupply_replacement_locked(
                                session,
                                epoch,
                                reason="resupply_transition_headroom_unavailable",
                            )
                        else:
                            self._abort_route2_downshift_replacement_locked(
                                session,
                                epoch,
                                reason="downshift_transition_headroom_unavailable",
                            )
                        continue
                    assigned_threads = replacement_target_threads
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
                if replacement_target_threads is not None:
                    is_reclaim_replacement = bool(epoch.adaptive_reclaim_request_id)
                    is_resupply_replacement = epoch.replacement_reason == "adaptive_resupply_boost"
                    thread_assignment = _Route2RealThreadAssignmentDecision(
                        assigned_threads=assigned_threads,
                        assignment_policy=(
                            "adaptive_resupply_boost_replacement"
                            if is_resupply_replacement
                            else
                            "adaptive_reclaim_donor_maintenance_replacement"
                            if is_reclaim_replacement
                            else "adaptive_downshift_maintenance_replacement"
                        ),
                        assignment_reason=(
                            "Priority re-supply donor replacement uses the precomputed boost tier; "
                            "the maintenance epoch keeps serving until the boost replacement is ready."
                            if is_resupply_replacement
                            else
                            "Admission-triggered reclaim donor replacement uses the precomputed lower thread tier; "
                            "no theoretical capacity is counted before switch/drain measurement."
                            if is_reclaim_replacement
                            else "Maintenance downshift replacement uses the precomputed lower thread tier; "
                            "no in-place ffmpeg thread mutation is performed."
                        ),
                        assignment_blockers=[],
                        adaptive_control_enabled=(
                            bool(getattr(self.settings, "route2_adaptive_resupply_enabled", False))
                            if is_resupply_replacement
                            else bool(getattr(self.settings, "route2_adaptive_downshift_enabled", False))
                        ),
                        adaptive_control_applied=True,
                        assigned_threads_source=(
                            f"adaptive_resupply_boost_{assigned_threads}"
                            if is_resupply_replacement
                            else
                            f"adaptive_reclaim_maintenance_{assigned_threads}"
                            if is_reclaim_replacement
                            else f"adaptive_downshift_maintenance_{assigned_threads}"
                        ),
                        fallback_used=False,
                        effective_ladder_target=assigned_threads,
                    )
                else:
                    try:
                        thread_assignment = self._resolve_route2_real_assigned_threads_locked(
                            record,
                            fixed_assigned_threads=assigned_threads,
                            spawn_dry_run=spawn_dry_run,
                            session=session,
                            epoch=epoch,
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
                record.fixed_assigned_threads_at_dispatch = (
                    assigned_threads
                    if replacement_target_threads is not None
                    else min(
                        self.settings.route2_max_worker_threads,
                        available_total_threads,
                        user_remaining_threads,
                    )
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
                record.real_9_prepare_enabled = thread_assignment.real_9_prepare_enabled
                record.real_9_prepare_candidate = thread_assignment.real_9_prepare_candidate
                record.real_9_prepare_applied = thread_assignment.real_9_prepare_applied
                record.real_9_prepare_blockers = thread_assignment.real_9_prepare_blockers
                record.effective_ladder_target = thread_assignment.effective_ladder_target
                record.lite_adaptive_prepare_candidate = thread_assignment.lite_adaptive_prepare_candidate
                record.lite_adaptive_prepare_applied = thread_assignment.lite_adaptive_prepare_applied
                record.lite_adaptive_prepare_blockers = thread_assignment.lite_adaptive_prepare_blockers
                record.cloud_adaptive_prepare_enabled = thread_assignment.cloud_adaptive_prepare_enabled
                record.cloud_adaptive_prepare_candidate = thread_assignment.cloud_adaptive_prepare_candidate
                record.cloud_adaptive_prepare_applied = thread_assignment.cloud_adaptive_prepare_applied
                record.cloud_adaptive_prepare_blockers = thread_assignment.cloud_adaptive_prepare_blockers
                record.strict_12_prepare_enabled = thread_assignment.strict_12_prepare_enabled
                record.strict_12_prepare_candidate = thread_assignment.strict_12_prepare_candidate
                record.strict_12_prepare_applied = thread_assignment.strict_12_prepare_applied
                record.strict_12_prepare_blockers = thread_assignment.strict_12_prepare_blockers
                record.strict_12_prepare_reason = thread_assignment.strict_12_prepare_reason
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
                    self._maybe_park_backgrounded_route2_session_locked(session, now_ts=now_ts)
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
