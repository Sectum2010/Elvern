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
    under_supply_started_at_ts: float | None = None
    display_eta_seconds: float | None = None
    display_eta_updated_at_ts: float = 0.0
    display_eta_stable: bool = False


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
    created_at: str = field(default_factory=utcnow_iso)
    last_seen_at: str = field(default_factory=utcnow_iso)
    prepared_ranges: list[list[float]] = field(default_factory=list)
    stop_requested: bool = False
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
