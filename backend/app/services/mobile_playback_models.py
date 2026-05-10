from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..db import utcnow_iso


SEGMENT_DURATION_SECONDS = 2.0
SEEK_PREROLL_SECONDS = 12.0
TARGET_WINDOW_PREROLL_SECONDS = 6.0
TARGET_WINDOW_FORWARD_SECONDS = 60.0
BACKGROUND_EXPANSION_FORWARD_SECONDS = 600.0
READY_AFTER_TARGET_SECONDS = 20.0
PLAYBACK_COMMIT_RUNWAY_SECONDS = 20.0
WATCH_LOW_WATERMARK_SECONDS = 18.0
WATCH_REFILL_TARGET_SECONDS = 90.0
WATCH_STALLED_RECOVERY_RUNWAY_SECONDS = 8.0
FRONTIER_WAIT_SECONDS = 12.0
STATUS_POLL_PREPARE_SECONDS = 1.0
MANIFEST_ADVANCE_TRIGGER_SECONDS = 16.0
MANIFEST_ADVANCE_MIN_GROWTH_SECONDS = 8.0
ROUTE2_ATTACH_READY_SECONDS = 45.0
ROUTE2_STARTUP_MIN_RUNWAY_SECONDS = 24.0
ROUTE2_STARTUP_PROJECTION_HORIZON_SECONDS = 90.0
ROUTE2_RECOVERY_RESUME_RUNWAY_SECONDS = 18.0
ROUTE2_RECOVERY_MIN_RUNWAY_SECONDS = 10.0
ROUTE2_RECOVERY_PROJECTION_HORIZON_SECONDS = 24.0
#
# Initial startup gates use explicit mode-specific runway thresholds. Recovery
# and reattach continue to use the existing conservative projected-runway rules.
ROUTE2_LITE_FAST_START_RUNWAY_SECONDS = 15.0
ROUTE2_LITE_SLOW_START_RUNWAY_SECONDS = 45.0
ROUTE2_LITE_UNDERSUPPLY_START_RUNWAY_SECONDS = 180.0
ROUTE2_FULL_FAST_START_RUNWAY_SECONDS = 120.0
ROUTE2_LOW_WATER_RUNWAY_SECONDS = 12.0
ROUTE2_LOW_WATER_PROJECTION_HORIZON_SECONDS = 90.0
ROUTE2_SUPPLY_RATE_WINDOW_SECONDS = 18.0
ROUTE2_SUPPLY_RATE_MIN_SAMPLE_SECONDS = 6.0
ROUTE2_SUPPLY_RATE_FAST_EMA_ALPHA = 0.55
ROUTE2_SUPPLY_RATE_SLOW_EMA_ALPHA = 0.25
ROUTE2_SUPPLY_SURPLUS_MIN_OBSERVATION_SECONDS = 6.0
ROUTE2_SUPPLY_SURPLUS_MIN_RATE_X = 1.05
ROUTE2_STARTUP_MIN_SUPPLY_RATE_X = 1.05
ROUTE2_RECOVERY_MIN_SUPPLY_RATE_X = 1.02
ROUTE2_LOW_WATER_SUSTAIN_SECONDS = 6.0
ROUTE2_DRAIN_IDLE_GRACE_SECONDS = 12.0
ROUTE2_DRAIN_MAX_SECONDS = 90.0
ROUTE2_REPLACEMENT_RETRY_BACKOFF_SECONDS = 3.0
ROUTE2_ATTACH_ACK_WARN_SECONDS = 6.0
ROUTE2_ETA_DISPLAY_MIN_OBSERVATION_SECONDS = 10.0
ROUTE2_ETA_DISPLAY_MIN_GROWTH_EVENTS = 3
ROUTE2_ETA_DISPLAY_STICKY_OBSERVATION_SECONDS = 14.0
ROUTE2_ETA_DISPLAY_MAX_VOLATILITY_RATIO = 0.95
ROUTE2_ETA_DISPLAY_GRACE_SECONDS = 12.0
ROUTE2_ETA_DISPLAY_MAX_UPWARD_STEP_SECONDS = 3.0
ROUTE2_ETA_DISPLAY_MAX_UPWARD_RATIO = 0.18
ROUTE2_ETA_DISPLAY_UPWARD_BLEND = 0.35
ROUTE2_FULL_GOODPUT_WINDOW_SECONDS = 60.0
ROUTE2_FULL_GOODPUT_MIN_SAMPLE_COUNT = 3
ROUTE2_FULL_GOODPUT_MIN_OBSERVATION_SECONDS = 8.0
ROUTE2_FULL_PREFLIGHT_TIMEOUT_SECONDS = 180.0
ROUTE2_FULL_PROBE_MIN_DURATION_SECONDS = 0.15
ROUTE2_FULL_PROBE_MAX_DURATION_SECONDS = 30.0
ROUTE2_FULL_BOOTSTRAP_ESTIMATE_DELAY_SECONDS = 20.0
ROUTE2_FULL_RESERVE_BASE_SECONDS = 20.0
ROUTE2_FULL_RESERVE_MAX_VOLATILITY_SECONDS = 12.0
ROUTE2_FULL_RESERVE_MAX_UNCERTAINTY_SECONDS = 10.0
ROUTE2_FULL_VOLATILITY_HORIZON_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class MobileProfile:
    key: str
    max_width: int
    max_height: int
    level: str
    crf: int
    maxrate: str
    bufsize: str


MOBILE_PROFILES: dict[str, MobileProfile] = {
    "mobile_1080p": MobileProfile(
        key="mobile_1080p",
        max_width=1920,
        max_height=1080,
        level="4.1",
        crf=21,
        maxrate="5500k",
        bufsize="11000k",
    ),
    "mobile_2160p": MobileProfile(
        key="mobile_2160p",
        max_width=3840,
        max_height=2160,
        level="5.1",
        crf=22,
        maxrate="16000k",
        bufsize="32000k",
    ),
}


@dataclass(slots=True)
class CacheState:
    cache_key: str
    cache_dir: Path
    metadata_path: Path
    init_path: Path
    duration_seconds: float
    profile: str
    total_segments: int
    source_fingerprint: str
    cached_segments: set[int] = field(default_factory=set)
    loaded: bool = False


@dataclass(slots=True)
class MobileClusterJob:
    generation: int
    phase: str
    target_position_seconds: float
    target_segment_index: int
    prepare_start_segment: int
    prepare_end_segment: int
    prepare_start_seconds: float
    prepare_end_seconds: float
    output_dir: Path
    manifest_path: Path
    state: str = "preparing"
    active_worker_id: str | None = None
    created_at: str = field(default_factory=utcnow_iso)
    superseded: bool = False
    process: subprocess.Popen[str] | None = field(default=None, repr=False)


@dataclass(slots=True)
class PlaybackEpoch:
    epoch_id: str
    session_id: str
    created_at: str
    target_position_seconds: float
    epoch_start_seconds: float
    attach_position_seconds: float
    epoch_dir: Path = field(repr=False)
    staging_dir: Path = field(repr=False)
    published_dir: Path = field(repr=False)
    staging_manifest_path: Path = field(repr=False)
    metadata_path: Path = field(repr=False)
    frontier_path: Path = field(repr=False)
    published_init_path: Path = field(repr=False)
    state: str = "starting"
    init_published: bool = False
    published_segments: set[int] = field(default_factory=set, repr=False)
    published_segment_bytes: dict[int, int] = field(default_factory=dict, repr=False)
    published_init_bytes: int = 0
    published_total_bytes: int = 0
    contiguous_published_through_segment: int | None = None
    transcoder_completed: bool = False
    active_worker_id: str | None = None
    last_published_at: str | None = None
    last_error: str | None = None
    stop_requested: bool = False
    process: subprocess.Popen[str] | None = field(default=None, repr=False)
    drain_started_at_ts: float | None = None
    drain_target_attach_revision: int = 0
    last_media_access_at_ts: float = field(default_factory=time.time)
    frontier_samples: list[tuple[float, float]] = field(default_factory=list, repr=False)
    byte_samples: list[tuple[float, int]] = field(default_factory=list, repr=False)
    publish_segment_count: int = 0
    publish_init_latency_seconds: float | None = None
    last_publish_latency_seconds: float | None = None
    publish_latency_total_seconds: float = 0.0
    publish_latency_max_seconds: float | None = None
    last_publish_kind: str | None = None
    under_supply_started_at_ts: float | None = None
    display_eta_seconds: float | None = None
    display_eta_updated_at_ts: float = 0.0
    display_eta_stable: bool = False
    replacement_reason: str | None = None
    maintenance_downshift_target_threads: int | None = None
    maintenance_downshift_source_epoch_id: str | None = None
    adaptive_downshift_transition_started_at: str | None = None
    adaptive_downshift_switched_at: str | None = None
    adaptive_downshift_aborted_reason: str | None = None
    adaptive_reclaim_request_id: str | None = None
    adaptive_reclaim_consumer_session_id: str | None = None
    adaptive_reclaim_consumer_user_id: int | None = None
    adaptive_reclaim_consumer_media_item_id: int | None = None
    adaptive_reclaim_consumer_reason: str | None = None
    adaptive_resupply_request_id: str | None = None
    adaptive_resupply_original_reclaim_request_id: str | None = None
    adaptive_resupply_target_threads: int | None = None
    adaptive_resupply_source_epoch_id: str | None = None
    adaptive_resupply_started_at: str | None = None
    adaptive_resupply_switched_at: str | None = None
    adaptive_resupply_abort_reason: str | None = None


@dataclass(slots=True)
class Route2WorkerRecord:
    worker_id: str
    session_id: str
    epoch_id: str
    user_id: int
    username: str | None
    auth_session_id: int | None
    media_item_id: int
    title: str
    playback_mode: str
    profile: str
    source_kind: str
    target_position_seconds: float
    state: str = "queued"
    pid: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str = field(default_factory=utcnow_iso)
    last_seen_at: str = field(default_factory=utcnow_iso)
    prepared_ranges: list[list[float]] = field(default_factory=list)
    stop_requested: bool = False
    cleanup_delayed: bool = False
    cleanup_delay_seconds: float | None = None
    non_retryable_error: str | None = None
    failure_count: int = 0
    replacement_count: int = 0
    assigned_threads: int = 0
    fixed_assigned_threads_at_dispatch: int | None = None
    adaptive_spawn_dry_run_enabled: bool = False
    adaptive_spawn_dry_run_threads: int | None = None
    adaptive_spawn_dry_run_reason: str | None = None
    adaptive_spawn_dry_run_blockers: list[str] = field(default_factory=list)
    adaptive_spawn_dry_run_policy: str | None = None
    adaptive_spawn_dry_run_source: str | None = None
    adaptive_spawn_dry_run_sample_age_seconds: float | None = None
    adaptive_spawn_dry_run_sample_mature: bool | None = None
    adaptive_thread_control_enabled: bool = False
    adaptive_thread_control_applied: bool = False
    adaptive_thread_assignment_policy: str | None = None
    adaptive_thread_assignment_reason: str | None = None
    adaptive_thread_assignment_blockers: list[str] = field(default_factory=list)
    adaptive_thread_assignment_fallback_used: bool = False
    assigned_threads_source: str = "fixed_disabled"
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
    adaptive_downshift_enabled: bool = False
    adaptive_downshift_candidate: bool = False
    adaptive_downshift_mode: str = "none"
    autonomous_maintenance_downshift_enabled: bool = False
    autonomous_maintenance_downshift_candidate: bool = False
    autonomous_maintenance_downshift_blockers: list[str] = field(default_factory=list)
    maintenance_downshift_suppressed_by_reclaim: bool = False
    donor_reserved_for_reclaim: bool = False
    reclaim_donor_downshift_active: bool = False
    adaptive_downshift_target_threads: int | None = None
    adaptive_downshift_policy: str | None = None
    adaptive_downshift_reason: str | None = None
    adaptive_downshift_blockers: list[str] = field(default_factory=list)
    adaptive_downshift_replacement_epoch_id: str | None = None
    adaptive_downshift_replacement_worker_id: str | None = None
    adaptive_downshift_state: str = "none"
    adaptive_downshift_transition_started_at: str | None = None
    adaptive_downshift_switched_at: str | None = None
    adaptive_downshift_aborted_reason: str | None = None
    adaptive_downshift_pressure_abort_reason: str | None = None
    adaptive_downshift_pressure_snapshot: dict[str, object] = field(default_factory=dict)
    adaptive_downshift_retry_count: int = 0
    adaptive_downshift_retry_not_before_seconds: float | None = None
    adaptive_downshift_retry_blocker: str | None = None
    adaptive_downshift_last_abort_reason: str | None = None
    adaptive_downshift_replacement_epoch_cap_remaining: int | None = None
    adaptive_boost_exit_reason: str | None = None
    current_boost_tier: int | None = None
    maintenance_tier_target: int | None = None
    downshift_safe_to_apply: bool = False
    downshift_transition_headroom_required: int | None = None
    downshift_transition_headroom_available: int | None = None
    adaptive_reclaim_enabled: bool = False
    adaptive_reclaim_dry_run_enabled: bool = True
    adaptive_reclaim_candidate: bool = False
    adaptive_reclaim_candidate_reason: str | None = None
    adaptive_reclaim_target_threads: int | None = None
    adaptive_reclaim_state: str = "none"
    adaptive_reclaim_request_id: str | None = None
    adaptive_reclaim_consumer_worker_id: str | None = None
    adaptive_reclaim_consumer_session_id: str | None = None
    adaptive_reclaim_consumer_user_id: int | None = None
    adaptive_reclaim_consumer_media_item_id: int | None = None
    adaptive_reclaim_consumer_reason: str | None = None
    adaptive_reclaim_donor_worker_id: str | None = None
    adaptive_reclaim_donor_session_id: str | None = None
    adaptive_reclaim_downshift_replacement_epoch_id: str | None = None
    adaptive_reclaim_downshift_replacement_worker_id: str | None = None
    adaptive_reclaim_started_at: str | None = None
    adaptive_reclaim_switched_at: str | None = None
    adaptive_reclaim_measured_at: str | None = None
    adaptive_reclaim_completed_at: str | None = None
    adaptive_reclaim_failed_reason: str | None = None
    adaptive_reclaim_released_threads_expected: int | None = None
    adaptive_reclaim_released_threads_measured: int | None = None
    adaptive_reclaim_released_cpu_cores_measured: float | None = None
    adaptive_reclaim_cpu_headroom_before: int | None = None
    adaptive_reclaim_cpu_headroom_after: int | None = None
    adaptive_reclaim_route2_cpu_cores_used_before: float | None = None
    adaptive_reclaim_route2_cpu_cores_used_after: float | None = None
    adaptive_reclaim_user_cpu_cores_used_before: float | None = None
    adaptive_reclaim_user_cpu_cores_used_after: float | None = None
    adaptive_reclaim_host_cpu_used_cores_before: float | None = None
    adaptive_reclaim_host_cpu_used_cores_after: float | None = None
    adaptive_reclaim_host_cpu_spare_cores_before: float | None = None
    adaptive_reclaim_host_cpu_spare_cores_after: float | None = None
    adaptive_reclaim_route2_headroom_before: int | None = None
    adaptive_reclaim_route2_headroom_after: int | None = None
    adaptive_reclaim_memory_pressure_before: float | None = None
    adaptive_reclaim_memory_pressure_after: float | None = None
    adaptive_reclaim_external_pressure_before: str | None = None
    adaptive_reclaim_external_pressure_after: str | None = None
    adaptive_reclaim_capacity_sufficient_for_consumer: bool | None = None
    adaptive_reclaim_retry_count: int = 0
    adaptive_reclaim_retry_not_before_seconds: float | None = None
    adaptive_reclaim_retry_blocker: str | None = None
    adaptive_reclaim_blockers: list[str] = field(default_factory=list)
    adaptive_reclaim_abort_reason: str | None = None
    adaptive_resupply_enabled: bool = False
    adaptive_resupply_dry_run_enabled: bool = True
    adaptive_resupply_needed: bool = False
    adaptive_resupply_reason: str | None = None
    adaptive_resupply_priority: int = 0
    adaptive_resupply_target_threads: int | None = None
    adaptive_resupply_state: str = "none"
    adaptive_resupply_request_id: str | None = None
    adaptive_resupply_original_reclaim_request_id: str | None = None
    adaptive_resupply_donor_worker_id: str | None = None
    adaptive_resupply_replacement_epoch_id: str | None = None
    adaptive_resupply_replacement_worker_id: str | None = None
    adaptive_resupply_started_at: str | None = None
    adaptive_resupply_switched_at: str | None = None
    adaptive_resupply_measured_at: str | None = None
    adaptive_resupply_blockers: list[str] = field(default_factory=list)
    adaptive_resupply_abort_reason: str | None = None
    adaptive_resupply_stabilization_active: bool = False
    adaptive_resupply_stabilization_until: str | None = None
    adaptive_resupply_stabilization_seconds_remaining: float | None = None
    adaptive_resupply_stabilization_reason: str | None = None
    last_resupply_completed_at: str | None = None
    last_resupply_target_threads: int | None = None
    resupplied_donor_protection_active: bool = False
    priority_reexpand_pending: bool = False
    priority_reexpand_reason: str | None = None
    donor_protection_active: bool = False
    donor_health_after_resupply: dict[str, object] = field(default_factory=dict)
    admission_blocked_by_resupply: bool = False
    admission_waiting_for_reclaim: bool = False
    admission_reclaim_possible: bool = False
    admission_reclaim_attempted: bool = False
    admission_reclaim_succeeded: bool = False
    admission_reclaim_failed_reason: str | None = None
    admission_capacity_after_reclaim: int | None = None
    admission_hard_block_reason: str | None = None
    process_exists: bool = False
    cpu_cores_used: float | None = None
    cpu_percent_of_total: float | None = None
    memory_bytes: int | None = None
    memory_percent_of_total: float | None = None
    telemetry_sampled: bool = False
    last_sampled_at: str | None = None
    last_cpu_sample_monotonic: float | None = field(default=None, repr=False)
    last_process_cpu_seconds: float | None = field(default=None, repr=False)
    last_cpu_sample_pid: int | None = field(default=None, repr=False)
    io_read_bytes: int | None = None
    io_write_bytes: int | None = None
    io_read_bytes_per_second: float | None = None
    io_write_bytes_per_second: float | None = None
    io_observation_seconds: float | None = None
    io_sample_mature: bool = False
    io_sample_stale: bool = True
    io_missing_metrics: list[str] = field(default_factory=list)
    last_io_sample_pid: int | None = field(default=None, repr=False)
    last_io_sample_monotonic: float | None = field(default=None, repr=False)
    last_io_read_bytes: int | None = field(default=None, repr=False)
    last_io_write_bytes: int | None = field(default=None, repr=False)
    process: subprocess.Popen[str] | None = field(default=None, repr=False)


@dataclass(slots=True)
class BrowserPlaybackSession:
    engine_mode: str = "legacy"
    playback_mode: str = "lite"
    state: str = "legacy"
    attach_revision: int = 0
    client_attach_revision: int = 0
    attach_revision_issued_at_ts: float = 0.0
    last_attach_warning_revision: int = 0
    last_full_contract_violation_signature: str = ""
    active_epoch_id: str | None = None
    replacement_epoch_id: str | None = None
    replacement_retry_not_before_ts: float = 0.0
    replacement_epoch_count: int = 0
    adaptive_downshift_retry_count: int = 0
    adaptive_downshift_retry_not_before_ts: float = 0.0
    adaptive_downshift_retry_blocker: str | None = None
    adaptive_downshift_last_abort_reason: str | None = None
    adaptive_downshift_pressure_moderate_started_at_ts: float = 0.0
    adaptive_downshift_pressure_moderate_sample_count: int = 0
    adaptive_downshift_pressure_abort_reason: str | None = None
    adaptive_downshift_pressure_snapshot: dict[str, object] = field(default_factory=dict)
    adaptive_reclaim_request_id: str | None = None
    adaptive_reclaim_consumer_worker_id: str | None = None
    adaptive_reclaim_consumer_session_id: str | None = None
    adaptive_reclaim_consumer_user_id: int | None = None
    adaptive_reclaim_consumer_media_item_id: int | None = None
    adaptive_reclaim_consumer_reason: str | None = None
    adaptive_reclaim_donor_worker_id: str | None = None
    adaptive_reclaim_donor_session_id: str | None = None
    adaptive_reclaim_downshift_replacement_epoch_id: str | None = None
    adaptive_reclaim_downshift_replacement_worker_id: str | None = None
    adaptive_reclaim_started_at: str | None = None
    adaptive_reclaim_switched_at: str | None = None
    adaptive_reclaim_measured_at: str | None = None
    adaptive_reclaim_completed_at: str | None = None
    adaptive_reclaim_failed_reason: str | None = None
    adaptive_reclaim_released_threads_expected: int | None = None
    adaptive_reclaim_released_threads_measured: int | None = None
    adaptive_reclaim_released_cpu_cores_measured: float | None = None
    adaptive_reclaim_cpu_headroom_before: int | None = None
    adaptive_reclaim_cpu_headroom_after: int | None = None
    adaptive_reclaim_route2_cpu_cores_used_before: float | None = None
    adaptive_reclaim_route2_cpu_cores_used_after: float | None = None
    adaptive_reclaim_user_cpu_cores_used_before: float | None = None
    adaptive_reclaim_user_cpu_cores_used_after: float | None = None
    adaptive_reclaim_host_cpu_used_cores_before: float | None = None
    adaptive_reclaim_host_cpu_used_cores_after: float | None = None
    adaptive_reclaim_host_cpu_spare_cores_before: float | None = None
    adaptive_reclaim_host_cpu_spare_cores_after: float | None = None
    adaptive_reclaim_route2_headroom_before: int | None = None
    adaptive_reclaim_route2_headroom_after: int | None = None
    adaptive_reclaim_memory_pressure_before: float | None = None
    adaptive_reclaim_memory_pressure_after: float | None = None
    adaptive_reclaim_external_pressure_before: str | None = None
    adaptive_reclaim_external_pressure_after: str | None = None
    adaptive_reclaim_capacity_sufficient_for_consumer: bool | None = None
    adaptive_reclaim_retry_count: int = 0
    adaptive_reclaim_retry_not_before_ts: float = 0.0
    adaptive_reclaim_retry_blocker: str | None = None
    adaptive_reclaim_state: str = "none"
    adaptive_reclaim_blockers: list[str] = field(default_factory=list)
    adaptive_reclaim_abort_reason: str | None = None
    adaptive_resupply_needed: bool = False
    adaptive_resupply_reason: str | None = None
    adaptive_resupply_target_threads: int | None = None
    adaptive_resupply_state: str = "none"
    adaptive_resupply_request_id: str | None = None
    adaptive_resupply_original_reclaim_request_id: str | None = None
    adaptive_resupply_donor_worker_id: str | None = None
    adaptive_resupply_replacement_epoch_id: str | None = None
    adaptive_resupply_replacement_worker_id: str | None = None
    adaptive_resupply_started_at: str | None = None
    adaptive_resupply_switched_at: str | None = None
    adaptive_resupply_measured_at: str | None = None
    adaptive_resupply_blockers: list[str] = field(default_factory=list)
    adaptive_resupply_abort_reason: str | None = None
    adaptive_resupply_stabilization_until_ts: float = 0.0
    adaptive_resupply_stabilization_until: str | None = None
    adaptive_resupply_stabilization_reason: str | None = None
    last_resupply_completed_at: str | None = None
    last_resupply_target_threads: int | None = None
    priority_reexpand_pending: bool = False
    priority_reexpand_reason: str | None = None
    donor_protection_active: bool = False
    donor_health_after_resupply: dict[str, object] = field(default_factory=dict)
    admission_blocked_by_resupply: bool = False
    full_preflight_state: str = "idle"
    full_preflight_error: str | None = None
    full_preflight_started_at_ts: float = 0.0
    full_prepare_started_at_ts: float = 0.0
    full_source_bin_bytes: list[int] = field(default_factory=list, repr=False)
    client_probe_samples: list[tuple[float, int, float]] = field(default_factory=list, repr=False)
    epochs: dict[str, PlaybackEpoch] = field(default_factory=dict)


@dataclass(slots=True)
class MobilePlaybackSession:
    session_id: str
    user_id: int
    auth_session_id: int | None
    username: str | None
    media_item_id: int
    media_title: str
    profile: str
    source_kind: str
    duration_seconds: float
    cache_key: str
    source_locator: str
    source_input_kind: str
    source_fingerprint: str
    created_at: str
    last_client_seen_at: str
    last_media_access_at: str
    state: str = "queued"
    epoch: int = 1
    target_position_seconds: float = 0.0
    pending_target_seconds: float | None = None
    manifest_start_segment: int | None = None
    manifest_end_segment: int | None = None
    ready_start_seconds: float = 0.0
    ready_end_seconds: float = 0.0
    last_stable_position_seconds: float = 0.0
    committed_playhead_seconds: float = 0.0
    actual_media_element_time_seconds: float = 0.0
    playing_before_seek: bool = False
    client_is_playing: bool = False
    client_device_class: str | None = None
    client_user_agent: str | None = None
    selected_hls_engine: str | None = None
    client_buffered_ahead_seconds: float | None = None
    client_target_forward_buffer_seconds: float | None = None
    client_back_buffer_seconds: float | None = None
    client_max_buffer_size_bytes: int | None = None
    client_ready_state: int | None = None
    client_network_state: int | None = None
    client_current_time_seconds: float | None = None
    client_time_advancing: bool | None = None
    client_playback_stall_reason: str | None = None
    hls_js_config: dict[str, object] | None = None
    lifecycle_state: str = "attached"
    stalled_recovery_requested: bool = False
    last_refill_start_seconds: float | None = None
    last_refill_end_seconds: float | None = None
    last_error: str | None = None
    worker_state: str = "idle"
    queue_started_ts: float | None = None
    expires_at_ts: float = 0.0
    active_job: MobileClusterJob | None = None
    browser_playback: BrowserPlaybackSession = field(default_factory=BrowserPlaybackSession)
    source_original_filename: str | None = None
    source_container: str | None = None
    source_video_codec: str | None = None
    source_audio_codec: str | None = None
    source_width: int | None = None
    source_height: int | None = None
    source_pixel_format: str | None = None
    source_bit_depth: int | None = None
    source_hdr_flag: bool | None = None
    source_dolby_vision_flag: bool | None = None
    source_audio_channels: int | None = None
