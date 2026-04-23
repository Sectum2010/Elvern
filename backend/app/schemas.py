from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AuthLoginRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    role: Literal["admin", "standard_user"]
    enabled: bool = True
    assistant_beta_enabled: bool = False
    session_id: int | None = None


class AuthUserEnvelope(BaseModel):
    user: UserResponse


class MessageResponse(BaseModel):
    message: str


class ScanResponse(BaseModel):
    message: str
    running: bool
    job_id: int | None = None


class SubtitleTrackResponse(BaseModel):
    id: int
    language: str | None = None
    title: str | None = None
    codec: str | None = None
    disposition_default: bool = False


class ParsedTitleResponse(BaseModel):
    display_title: str
    base_title: str
    edition_identity: str = "standard"
    parsed_year: int | None = None
    title_source: str = "fallback"
    parse_confidence: Literal["high", "medium", "low"] = "low"
    warnings: list[str] = Field(default_factory=list)
    parser_version: str = ""
    suspicious_output: bool = False


class LibraryItemSummary(BaseModel):
    id: int
    title: str
    parsed_title: ParsedTitleResponse
    original_filename: str
    source_kind: Literal["local", "cloud"] = "local"
    source_label: Literal["DGX", "Cloud"] = "DGX"
    library_source_id: int | None = None
    library_source_name: str | None = None
    library_source_shared: bool = False
    poster_url: str | None = None
    edition_label: str | None = None
    hidden_for_user: bool = False
    hidden_globally: bool = False
    file_size: int
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    container: str | None = None
    year: int | None = None
    created_at: str
    updated_at: str
    last_scanned_at: str
    progress_seconds: float | None = None
    progress_duration_seconds: float | None = None
    completed: bool = False


class SeriesRailResponse(BaseModel):
    key: str
    title: str
    film_count: int
    items: list[LibraryItemSummary] = Field(default_factory=list)


class LibraryListResponse(BaseModel):
    items: list[LibraryItemSummary] = Field(default_factory=list)
    series_rails: list[SeriesRailResponse] = Field(default_factory=list)
    cloud_series_rails: list[SeriesRailResponse] = Field(default_factory=list)
    continue_watching: list[LibraryItemSummary] = Field(default_factory=list)
    recently_added: list[LibraryItemSummary] = Field(default_factory=list)
    query: str | None = None
    scan_in_progress: bool = False
    total_items: int = 0


class UserSettingsResponse(BaseModel):
    hide_duplicate_movies: bool = True
    hide_recently_added: bool = False
    floating_controls_position: Literal["bottom", "top"] = "bottom"
    media_library_reference_private_value: str | None = None
    media_library_reference_shared_default_value: str = ""
    media_library_reference_effective_value: str = ""


class UserSettingsUpdateRequest(BaseModel):
    hide_duplicate_movies: bool | None = None
    hide_recently_added: bool | None = None
    floating_controls_position: Literal["bottom", "top"] | None = None
    media_library_reference_private_value: str | None = None


class MediaLibraryReferenceUpdateRequest(BaseModel):
    value: str = ""


class MediaLibraryReferenceResponse(BaseModel):
    configured_value: str | None = None
    effective_value: str
    default_value: str
    validation_rules: list[str] = Field(default_factory=list)


class LocalDirectoryBrowseEntryResponse(BaseModel):
    name: str
    path: str


class LocalDirectoryBrowseResponse(BaseModel):
    current_path: str
    parent_path: str | None = None
    directories: list[LocalDirectoryBrowseEntryResponse] = Field(default_factory=list)


class LocalDirectoryPickRequest(BaseModel):
    path: str = ""
    title: str = "Select directory"
    platform: str = ""
    same_host_hint: bool = False


class LocalDirectoryPickerCapabilityResponse(BaseModel):
    native_picker_supported: bool = False
    same_host_linux: bool = False
    same_host_detection_source: str | None = None
    same_host_reason: str | None = None
    picker_backend: str | None = None
    gui_session_available: bool = False
    display_available: bool = False
    wayland_available: bool = False
    dbus_session_available: bool = False
    missing_dependency: str | None = None
    reason: str | None = None


class LocalDirectoryPickResponse(BaseModel):
    status: Literal["selected", "cancelled", "unavailable", "error"] = "cancelled"
    selected_path: str | None = None
    reason: str | None = None
    picker_backend: str | None = None


class PosterReferenceLocationUpdateRequest(BaseModel):
    value: str = ""


class PosterReferenceLocationResponse(BaseModel):
    configured_value: str | None = None
    effective_value: str
    default_value: str
    validation_rules: list[str] = Field(default_factory=list)


class GoogleDriveSetupUpdateRequest(BaseModel):
    https_origin: str = ""
    client_id: str = ""
    client_secret: str = ""


class GoogleDriveSetupResponse(BaseModel):
    https_origin: str = ""
    client_id: str = ""
    client_secret: str = ""
    javascript_origin: str = ""
    redirect_uri: str = ""
    callback_source: Literal["google_drive_https_origin", "public_app_origin", "unconfigured"]
    callback_warning: str | None = None
    configuration_state: Literal["not_configured", "partially_configured", "ready"] = "not_configured"
    configuration_label: str
    status_message: str
    missing_fields: list[str] = Field(default_factory=list)
    connected: bool = False
    account_email: str | None = None
    account_name: str | None = None
    instructions: list[str] = Field(default_factory=list)


class HiddenMovieSummary(BaseModel):
    id: int
    title: str
    year: int | None = None
    edition_label: str | None = None
    poster_url: str | None = None
    hidden_at: str


class HiddenMovieListResponse(BaseModel):
    items: list[HiddenMovieSummary] = Field(default_factory=list)


class MediaItemDetail(LibraryItemSummary):
    file_path: str
    stream_url: str
    resume_position_seconds: float = 0
    subtitles: list[SubtitleTrackResponse] = Field(default_factory=list)


class GoogleDriveConnectionResponse(BaseModel):
    enabled: bool = False
    connected: bool = False
    account_email: str | None = None
    account_name: str | None = None


class CloudLibrarySourceSummary(BaseModel):
    id: int
    provider: Literal["google_drive"] = "google_drive"
    display_name: str
    resource_type: Literal["folder", "shared_drive"]
    resource_id: str
    source_label: Literal["Cloud"] = "Cloud"
    is_shared: bool = False
    hidden_for_user: bool = False
    owner_username: str | None = None
    owner_account_email: str | None = None
    item_count: int = 0
    created_at: str
    last_synced_at: str | None = None
    last_error: str | None = None


class CloudLibrariesResponse(BaseModel):
    google: GoogleDriveConnectionResponse = Field(default_factory=GoogleDriveConnectionResponse)
    my_libraries: list[CloudLibrarySourceSummary] = Field(default_factory=list)
    shared_libraries: list[CloudLibrarySourceSummary] = Field(default_factory=list)


class GoogleDriveConnectResponse(BaseModel):
    authorization_url: str


class GoogleDriveConnectRequest(BaseModel):
    return_path: str | None = None


class CloudLibrarySourceCreateRequest(BaseModel):
    resource_type: Literal["folder", "shared_drive"]
    resource_id: str = Field(min_length=2)
    shared: bool = False


class CloudLibrarySourceMoveRequest(BaseModel):
    shared: bool


class ProgressResponse(BaseModel):
    media_item_id: int
    position_seconds: float = 0
    duration_seconds: float | None = None
    completed: bool = False
    updated_at: str | None = None


class ProgressUpdateRequest(BaseModel):
    position_seconds: float = Field(ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    completed: bool = False
    playback_mode: str | None = Field(default=None, min_length=1)


PlaybackTrackingEventType = Literal[
    "playback_opened",
    "playback_progress",
    "playback_seeked",
    "playback_stopped",
    "playback_completed",
]


class PlaybackEventRequest(BaseModel):
    event_type: PlaybackTrackingEventType
    playback_mode: str = Field(min_length=1)
    position_seconds: float | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    occurred_at: str | None = None


class PlaybackStartRequest(BaseModel):
    force_hls: bool = False


class PlaybackDecisionResponse(BaseModel):
    mode: Literal["direct", "hls"]
    direct_url: str | None = None
    hls_url: str | None = None
    reason: str
    container: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    client_profile: str
    manifest_ready: bool = False
    expected_duration_seconds: float | None = None
    generated_duration_seconds: float | None = None
    manifest_complete: bool = False
    transcode_status: str = "not_needed"
    transcode_enabled: bool = True
    last_error: str | None = None


MobilePlaybackProfile = Literal["mobile_1080p", "mobile_2160p"]
BrowserPlaybackEngineMode = Literal["legacy", "route2"]
BrowserPlaybackMode = Literal["lite", "full"]
BrowserPlaybackSessionEngineState = Literal["legacy", "starting", "switching", "active", "recovering", "stopped", "failed"]
BrowserPlaybackEpochState = Literal["starting", "warming", "attach_ready", "active", "draining", "ended", "failed"]
BrowserPlaybackModeState = Literal["estimating", "preparing", "ready"]
BrowserPlaybackEstimateSource = Literal["none", "bootstrap", "true"]
MobilePlaybackState = Literal["queued", "preparing", "ready", "retargeting", "failed", "stopped", "expired"]
MobilePlaybackWorkerState = Literal["idle", "queued", "running"]
MobilePlaybackLifecycleState = Literal["attached", "background-suspended", "resuming", "recovering", "fatal"]


class MobilePlaybackSessionCreateRequest(BaseModel):
    item_id: int = Field(ge=1)
    profile: MobilePlaybackProfile = "mobile_1080p"
    start_position_seconds: float | None = Field(default=None, ge=0)
    engine_mode: BrowserPlaybackEngineMode | None = None
    playback_mode: BrowserPlaybackMode | None = None


class MobilePlaybackSeekRequest(BaseModel):
    target_position_seconds: float = Field(ge=0)
    last_stable_position_seconds: float | None = Field(default=None, ge=0)
    playing_before_seek: bool | None = None


class MobilePlaybackHeartbeatRequest(BaseModel):
    committed_playhead_seconds: float | None = Field(default=None, ge=0)
    actual_media_element_time_seconds: float | None = Field(default=None, ge=0)
    client_attach_revision: int | None = Field(default=None, ge=0)
    client_probe_bytes: int | None = Field(default=None, ge=0)
    client_probe_duration_ms: int | None = Field(default=None, ge=1)
    lifecycle_state: MobilePlaybackLifecycleState | None = None
    stalled: bool | None = None
    playing: bool | None = None


class MobilePlaybackSessionResponse(BaseModel):
    session_id: str
    media_item_id: int = Field(ge=1)
    epoch: int = Field(ge=1)
    manifest_revision: str
    state: MobilePlaybackState
    profile: MobilePlaybackProfile
    duration_seconds: float = Field(ge=0)
    target_position_seconds: float = Field(ge=0)
    ready_start_seconds: float = Field(ge=0)
    ready_end_seconds: float = Field(ge=0)
    can_play_from_target: bool = False
    manifest_url: str
    status_url: str
    seek_url: str
    heartbeat_url: str
    stop_url: str
    manifest_start_segment: int = Field(default=0, ge=0)
    manifest_end_segment: int = Field(default=0, ge=0)
    manifest_start_seconds: float = Field(default=0, ge=0)
    manifest_end_seconds: float = Field(default=0, ge=0)
    last_error: str | None = None
    worker_state: MobilePlaybackWorkerState = "idle"
    pending_target_seconds: float | None = Field(default=None, ge=0)
    last_stable_position_seconds: float = Field(default=0, ge=0)
    playing_before_seek: bool = False
    target_segment_index: int = Field(default=0, ge=0)
    target_cluster_ready: bool = False
    target_window_ready: bool = False
    playback_commit_ready: bool = False
    cache_ranges: list[list[float]] = Field(default_factory=list)
    committed_playhead_seconds: float = Field(default=0, ge=0)
    actual_media_element_time_seconds: float = Field(default=0, ge=0)
    ahead_runway_seconds: float = Field(default=0, ge=0)
    supply_rate_x: float = Field(default=0, ge=0)
    supply_observation_seconds: float = Field(default=0, ge=0)
    prepare_estimate_seconds: float | None = Field(default=None, ge=0)
    refill_in_progress: bool = False
    last_refill_start_seconds: float | None = Field(default=None, ge=0)
    last_refill_end_seconds: float | None = Field(default=None, ge=0)
    starvation_risk: bool = False
    stalled_recovery_needed: bool = False
    lifecycle_state: MobilePlaybackLifecycleState = "attached"
    status_poll_seconds: float = Field(default=1.0, ge=0)
    engine_mode: BrowserPlaybackEngineMode = "legacy"
    playback_mode: BrowserPlaybackMode = "lite"
    mode_state: BrowserPlaybackModeState = "preparing"
    mode_ready: bool = False
    mode_estimate_seconds: float | None = Field(default=None, ge=0)
    mode_estimate_source: BrowserPlaybackEstimateSource = "none"
    session_state: BrowserPlaybackSessionEngineState = "legacy"
    attach_revision: int = Field(default=0, ge=0)
    client_attach_revision: int = Field(default=0, ge=0)
    active_epoch_id: str | None = None
    replacement_epoch_id: str | None = None
    active_manifest_url: str | None = None
    attach_position_seconds: float = Field(default=0, ge=0)
    attach_ready: bool = False
    browser_session_state: BrowserPlaybackSessionEngineState = "legacy"
    active_epoch_state: BrowserPlaybackEpochState | None = None


class MobilePlaybackStopResponse(BaseModel):
    stopped: bool
    message: str


class ActiveTranscodeResponse(BaseModel):
    media_item_id: int
    title: str
    status: str
    started_at: str
    last_access_at: str
    manifest_ready: bool = False
    expected_duration_seconds: float | None = None
    generated_duration_seconds: float | None = None
    segment_count: int = 0
    manifest_complete: bool = False
    output_dir: str
    last_error: str | None = None


class TranscodeStatusResponse(BaseModel):
    enabled: bool
    ffmpeg_available: bool
    cache_dir: str
    ttl_minutes: int
    max_concurrent_transcodes: int
    active_jobs: list[ActiveTranscodeResponse] = Field(default_factory=list)
    last_error: str | None = None


class NativeTrackResponse(BaseModel):
    index: int
    codec: str | None = None
    language: str | None = None
    title: str | None = None
    channels: int | None = None
    disposition_default: bool = False


TransportSelectedMode = Literal[
    "local_direct_path",
    "wifi_direct",
    "cellular_direct",
    "single_best_path",
    "dual_channel_prewarm",
    "dual_channel_active",
]
TransportRequestedMode = Literal["single_best_path", "wifi_direct", "cellular_direct"]
TransportPlayer = Literal["vlc", "infuse"]
TransportPlatformFamily = Literal["ios", "ipad_os", "desktop", "unknown"]
TransportCallerSurface = Literal["web_browser", "web_pwa", "native_app", "unknown"]
TransportEnvironmentKind = Literal["same_host", "same_lan", "tailnet_private", "remote_unknown", "unknown"]
TransportPathClass = Literal["wifi", "cellular", "wired", "unknown"]


class TransportControllerDecisionRequest(BaseModel):
    requested_player: TransportPlayer
    requested_transport_mode: TransportRequestedMode = "single_best_path"
    platform_family: TransportPlatformFamily = "unknown"
    caller_surface: TransportCallerSurface = "unknown"
    environment_kind: TransportEnvironmentKind = "unknown"
    current_path_class: TransportPathClass = "unknown"
    trusted_network_context: bool = False
    allow_browser_fallback: bool = True


class TransportControllerPrimaryTargetResponse(BaseModel):
    target_kind: Literal["native_session_stream", "direct_path"]
    url: str
    expires_at: str | None = None


class TransportControllerFallbackResponse(BaseModel):
    fallback_kind: Literal["browser_fallback"]
    reason_code: str


class TransportControllerTelemetryResponse(BaseModel):
    requested_player: TransportPlayer
    requested_transport_mode: TransportRequestedMode
    selected_mode: TransportSelectedMode
    player_capability_profile: str
    platform_family: TransportPlatformFamily
    caller_surface: TransportCallerSurface
    environment_kind: TransportEnvironmentKind
    current_path_class: TransportPathClass
    reason_code: str
    downgraded: bool = False


class TransportControllerDecisionResponse(BaseModel):
    selected_player: TransportPlayer
    selected_mode: TransportSelectedMode
    primary_target: TransportControllerPrimaryTargetResponse | None = None
    fallback: TransportControllerFallbackResponse | None = None
    telemetry: TransportControllerTelemetryResponse


class NativePlaybackTransportProbeResponse(BaseModel):
    item_id: int
    client_name: str | None = None
    external_player: TransportPlayer | None = None
    requested_transport_mode: TransportRequestedMode | None = None
    caller_surface: TransportCallerSurface | None = None
    current_path_class: TransportPathClass | None = None
    trusted_network_context: bool | None = None
    allow_browser_fallback: bool | None = None
    transport_decision_exists: bool = False
    selected_player: TransportPlayer | None = None
    selected_mode: TransportSelectedMode | None = None
    primary_target_kind: Literal["native_session_stream", "direct_path"] | None = None
    fallback_kind: Literal["browser_fallback"] | None = None
    reason_code: str | None = None


class NativePlaybackSessionCreateRequest(BaseModel):
    client_name: str | None = None
    external_player: TransportPlayer | None = None
    requested_transport_mode: TransportRequestedMode | None = None
    caller_surface: TransportCallerSurface | None = None
    current_path_class: TransportPathClass | None = None
    trusted_network_context: bool = False
    allow_browser_fallback: bool = True


class NativePlaybackSessionResponse(BaseModel):
    session_id: str
    access_token: str | None = None
    api_origin: str
    details_url: str
    stream_url: str
    heartbeat_url: str
    progress_url: str
    event_url: str
    close_url: str
    expires_at: str
    title: str
    media_id: int
    duration_seconds: float | None = None
    resume_seconds: float = 0
    subtitle_tracks: list[NativeTrackResponse] = Field(default_factory=list)
    audio_tracks: list[NativeTrackResponse] = Field(default_factory=list)
    container: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    native_player_protocol: str
    session_api_version: int = 1
    transport_decision: TransportControllerDecisionResponse | None = None
    transport_probe: NativePlaybackTransportProbeResponse | None = None


class NativePlaybackHeartbeatResponse(BaseModel):
    message: str
    expires_at: str


class NativePlaybackProgressRequest(BaseModel):
    position_seconds: float = Field(ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    completed: bool = False


class NativePlaybackEventRequest(BaseModel):
    event_type: PlaybackTrackingEventType
    position_seconds: float | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    occurred_at: str | None = None


class NativePlaybackCloseRequest(BaseModel):
    position_seconds: float | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    completed: bool = False


class NativePlaybackStatusResponse(BaseModel):
    enabled: bool
    protocol: str
    session_ttl_minutes: int
    token_ttl_seconds: int
    active_sessions: int = 0


DesktopPlatform = Literal["linux", "windows", "mac"]


class DesktopPlaybackResolveResponse(BaseModel):
    platform: DesktopPlatform
    strategy: Literal["direct_path", "backend_url"]
    title: str
    resume_seconds: float = 0
    open_supported: bool = False
    handoff_supported: bool = False
    open_method: Literal["spawn_vlc", "protocol_helper", "download_playlist"]
    same_host_launch: bool = False
    used_backend_fallback: bool = False
    helper_protocol: str | None = None
    vlc_target: str
    playlist_url: str
    notes: list[str] = Field(default_factory=list)


class DesktopPlaybackOpenRequest(BaseModel):
    platform: DesktopPlatform | None = None
    same_host: bool = False


class DesktopPlaybackOpenResponse(BaseModel):
    launched: bool
    message: str
    target: str
    strategy: Literal["direct_path", "backend_url"]
    command: list[str] = Field(default_factory=list)


class DesktopPlaybackHandoffRequest(BaseModel):
    platform: DesktopPlatform | None = None
    device_id: str | None = Field(default=None, max_length=128)


class DesktopPlaybackHandoffCreateResponse(BaseModel):
    handoff_id: str
    helper_protocol: str
    protocol_url: str
    playlist_url: str
    expires_at: str
    strategy: Literal["direct_path", "backend_url"]
    message: str


class DesktopPlaybackHandoffResolveResponse(BaseModel):
    handoff_id: str
    title: str
    media_id: int
    platform: DesktopPlatform
    strategy: Literal["direct_path", "backend_url"]
    target_kind: Literal["path", "url"]
    target: str
    started_url: str | None = None
    resume_seconds: float = 0
    expires_at: str
    session_api_version: int = 1


class DesktopPlaybackStatusResponse(BaseModel):
    mode: str
    helper_protocol: str
    helper_requires_backend_origin: bool
    public_app_origin: str
    public_origin_configured: bool
    backend_origin: str
    backend_origin_configured: bool
    linux_vlc_available: bool
    linux_vlc_path: str | None = None
    windows_vlc_path: str | None = None
    mac_vlc_path: str | None = None
    linux_library_root: str
    windows_library_root: str | None = None
    mac_library_root: str | None = None


DesktopHelperStatusState = Literal[
    "helper_not_required",
    "unknown",
    "up_to_date",
    "update_available",
    "release_unavailable",
]
DesktopVlcDetectionState = Literal[
    "installed",
    "not_detected",
    "detection_unavailable",
]


class DesktopHelperReleaseResponse(BaseModel):
    id: int
    channel: str
    runtime_id: str
    platform: Literal["windows", "mac"]
    version: str
    filename: str
    size_bytes: int
    sha256: str
    published_at: str
    dotnet_runtime_required: str
    download_url: str
    recommended: bool = False


class DesktopHelperReleaseListResponse(BaseModel):
    platform: DesktopPlatform
    releases: list[DesktopHelperReleaseResponse] = Field(default_factory=list)


class DesktopHelperStatusResponse(BaseModel):
    device_id: str | None = None
    platform: DesktopPlatform
    helper_required: bool = True
    state: DesktopHelperStatusState
    vlc_detection_state: DesktopVlcDetectionState = "detection_unavailable"
    vlc_detection_path: str | None = None
    vlc_detection_checked_at: str | None = None
    recommended_runtime_id: str | None = None
    last_seen_helper_version: str | None = None
    last_seen_helper_platform: str | None = None
    last_seen_helper_arch: str | None = None
    last_seen_helper_at: str | None = None
    dotnet_runtime_required: str | None = None
    latest_releases: list[DesktopHelperReleaseResponse] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


DesktopHelperVerificationMode = Literal["host", "helper"]


class DesktopHelperVerificationRequest(BaseModel):
    platform: DesktopPlatform
    device_id: str | None = Field(default=None, max_length=128)


class DesktopHelperVerificationResponse(BaseModel):
    mode: DesktopHelperVerificationMode
    protocol_url: str | None = None
    expires_at: str | None = None
    status: DesktopHelperStatusResponse | None = None


class AdminUserResponse(BaseModel):
    id: int
    username: str
    role: Literal["admin", "standard_user"]
    enabled: bool = True
    assistant_beta_enabled: bool = False
    created_at: str
    updated_at: str
    last_login_at: str | None = None
    status_color: Literal["green", "yellow", "orange", "red", "grey"] = "grey"
    status_label: str = "Offline"
    active_sessions: int = 0
    last_seen_at: str | None = None
    last_activity_at: str | None = None


class AdminUserListResponse(BaseModel):
    users: list[AdminUserResponse] = Field(default_factory=list)


class AdminUserCreateRequest(BaseModel):
    username: str
    password: str
    role: Literal["admin", "standard_user"] = "standard_user"
    enabled: bool = True


class AdminUserUpdateRequest(BaseModel):
    enabled: bool | None = None
    role: Literal["admin", "standard_user"] | None = None
    current_admin_password: str | None = None


class AssistantUserAccessResponse(BaseModel):
    user_id: int
    assistant_beta_enabled: bool = False
    enabled_by_user_id: int | None = None
    enabled_at: str | None = None
    disabled_at: str | None = None
    note: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class AssistantUserAccessUpdateRequest(BaseModel):
    assistant_beta_enabled: bool
    note: str | None = None


AssistantRequestType = Literal[
    "bug_report",
    "improvement_suggestion",
    "library_issue",
    "playback_issue",
    "security_concern",
    "account_request",
    "other",
]

AssistantRequestUrgency = Literal["low", "normal", "high"]
AssistantRequestStatus = Literal["new", "triaged", "awaiting_admin", "approved", "rejected", "closed"]
AssistantAttachmentType = Literal["image", "text", "other"]
AssistantAttachmentStorageKind = Literal["local_upload"]
AssistantTriageCreatedBy = Literal["assistant", "admin_user"]
AssistantRiskLevel = Literal["low", "medium", "high", "critical"]
AssistantConfidenceLevel = Literal["low", "medium", "high"]
AssistantReversibilityImpact = Literal["none", "r0_possible", "r1_possible", "r2_or_higher", "unknown"]
AssistantActionCreatedByType = Literal["assistant", "admin_user"]
AssistantActionType = Literal[
    "create_backup_checkpoint",
    "library_rescan",
    "service_restart",
    "prepare_patch_in_sandbox",
    "save_change_record_draft",
    "send_admin_notification",
]
AssistantTargetScope = Literal[
    "library_local",
    "library_cloud",
    "library_all",
    "service_backend",
    "service_frontend",
    "sandbox_repo_copy",
    "other",
]
AssistantActionStatus = Literal["draft", "awaiting_admin", "approved", "rejected", "cancelled", "executed", "failed"]
AssistantApprovalDecision = Literal["approved", "rejected", "needs_more_info"]
AssistantChangeStatus = Literal["draft", "prepared", "executed", "reverted", "failed"]
AssistantReversibilityLevel = Literal["r0", "r1", "r2", "r3", "unknown"]


class AssistantRequestAttachmentResponse(BaseModel):
    id: int
    attachment_type: AssistantAttachmentType
    storage_kind: AssistantAttachmentStorageKind = "local_upload"
    storage_path_safe_ref: str
    view_url: str | None = None
    original_filename: str | None = None
    mime_type: str | None = None
    size_bytes: int = 0
    created_at: str


class AssistantAttachmentExternalOpenResponse(BaseModel):
    external_open_kind: Literal["raw_image_ticket"] = "raw_image_ticket"
    external_open_url: str
    external_open_expires_at: str


class AssistantRequestSummaryResponse(BaseModel):
    id: int
    request_number: str
    submitted_by_user_id: int
    submitted_by_display_name_snapshot: str
    request_type: AssistantRequestType
    title: str
    urgency: AssistantRequestUrgency = "normal"
    status: AssistantRequestStatus = "new"
    created_at: str
    updated_at: str
    status_updated_at: str | None = None
    page_context: str | None = None
    platform: str | None = None
    source_context: str | None = None
    is_archived: bool = False


class AssistantTriageDraftResponse(BaseModel):
    id: int
    request_id: int
    created_by: AssistantTriageCreatedBy
    model_provider: str | None = None
    model_name: str | None = None
    summary: str
    classification: str
    risk_level: AssistantRiskLevel = "medium"
    confidence_level: AssistantConfidenceLevel = "low"
    possible_duplicate_request_ids: list[int] = Field(default_factory=list)
    suggested_next_step: str | None = None
    suggested_owner: str | None = None
    needs_admin_approval: bool = False
    needs_external_access_approval: bool = False
    reversibility_impact_if_action_taken: AssistantReversibilityImpact = "unknown"
    notes_for_admin: str | None = None
    created_at: str
    updated_at: str


class AssistantActionRequestResponse(BaseModel):
    id: int
    request_id: int
    triage_draft_id: int | None = None
    created_by_type: AssistantActionCreatedByType
    created_by_user_id: int | None = None
    action_type: AssistantActionType
    target_scope: AssistantTargetScope
    reason: str
    proposed_plan: str | None = None
    risk_level: AssistantRiskLevel = "medium"
    requires_admin_approval: bool = True
    requires_external_access_approval: bool = False
    reversibility_level: AssistantReversibilityLevel = "unknown"
    warning_if_not_fully_reversible: str | None = None
    status: AssistantActionStatus = "draft"
    created_at: str
    updated_at: str


class AssistantApprovalRecordResponse(BaseModel):
    id: int
    action_request_id: int
    decision: AssistantApprovalDecision
    decided_by_user_id: int
    decision_note: str | None = None
    backup_required: bool = False
    rollback_plan_required: bool = False
    external_access_approved: bool = False
    decided_at: str


class AssistantChangeRecordResponse(BaseModel):
    id: int
    request_id: int
    linked_action_request_id: int | None = None
    created_at: str
    created_by_type: str
    change_summary: str | None = None
    reversibility_level: AssistantReversibilityLevel = "unknown"
    backup_reference: str | None = None
    revert_recipe_draft: str | None = None
    verification_plan_draft: str | None = None
    status: AssistantChangeStatus = "draft"


class AssistantRequestDetailResponse(AssistantRequestSummaryResponse):
    description: str
    repro_steps: str | None = None
    expected_result: str | None = None
    actual_result: str | None = None
    app_version: str | None = None
    related_entity_type: str | None = None
    related_entity_id: str | None = None
    admin_note: str | None = None
    duplicate_group_key: str | None = None
    status_updated_by_user_id: int | None = None
    attachments: list[AssistantRequestAttachmentResponse] = Field(default_factory=list)
    latest_triage_draft: AssistantTriageDraftResponse | None = None
    triage_drafts: list[AssistantTriageDraftResponse] = Field(default_factory=list)
    action_requests: list[AssistantActionRequestResponse] = Field(default_factory=list)
    approval_records: list[AssistantApprovalRecordResponse] = Field(default_factory=list)
    change_records: list[AssistantChangeRecordResponse] = Field(default_factory=list)


class AssistantRequestListResponse(BaseModel):
    requests: list[AssistantRequestSummaryResponse] = Field(default_factory=list)


class AssistantRequestDetailEnvelope(BaseModel):
    request: AssistantRequestDetailResponse


class AssistantRequestStatusUpdateRequest(BaseModel):
    status: AssistantRequestStatus
    admin_note: str | None = None


class AssistantTriageDraftCreateRequest(BaseModel):
    created_by: AssistantTriageCreatedBy = "assistant"
    model_provider: str | None = "local_stub"
    model_name: str | None = None
    summary: str
    classification: str
    risk_level: AssistantRiskLevel = "medium"
    confidence_level: AssistantConfidenceLevel = "low"
    possible_duplicate_request_ids: list[int] = Field(default_factory=list)
    suggested_next_step: str | None = None
    suggested_owner: str | None = None
    needs_admin_approval: bool = False
    needs_external_access_approval: bool = False
    reversibility_impact_if_action_taken: AssistantReversibilityImpact = "unknown"
    notes_for_admin: str | None = None


class AssistantActionRequestCreateRequest(BaseModel):
    triage_draft_id: int | None = None
    created_by_type: AssistantActionCreatedByType = "assistant"
    action_type: AssistantActionType
    target_scope: AssistantTargetScope
    reason: str
    proposed_plan: str | None = None
    risk_level: AssistantRiskLevel = "medium"
    requires_admin_approval: bool = True
    requires_external_access_approval: bool = False
    reversibility_level: AssistantReversibilityLevel = "unknown"
    warning_if_not_fully_reversible: str | None = None
    status: AssistantActionStatus = "awaiting_admin"


class AssistantApprovalRecordCreateRequest(BaseModel):
    decision: AssistantApprovalDecision
    decision_note: str | None = None
    backup_required: bool = False
    rollback_plan_required: bool = False
    external_access_approved: bool = False


class AssistantChangeRecordCreateRequest(BaseModel):
    linked_action_request_id: int | None = None
    created_by_type: str = "admin_user"
    change_summary: str | None = None
    reversibility_level: AssistantReversibilityLevel = "unknown"
    backup_reference: str | None = None
    revert_recipe_draft: str | None = None
    verification_plan_draft: str | None = None
    status: AssistantChangeStatus = "draft"


class AdminPasswordUpdateRequest(BaseModel):
    new_password: str
    current_admin_password: str


class AdminSelfDeleteRequest(BaseModel):
    current_admin_password: str
    confirm: bool = False


class AdminSessionResponse(BaseModel):
    id: int
    user_id: int
    username: str
    role: Literal["admin", "standard_user"]
    created_at: str
    expires_at: str
    last_seen_at: str
    last_activity_at: str | None = None
    user_agent: str | None = None
    ip_address: str | None = None


class AdminSessionListResponse(BaseModel):
    sessions: list[AdminSessionResponse] = Field(default_factory=list)


class AuditLogEventResponse(BaseModel):
    id: int
    created_at: str
    user_id: int | None = None
    username: str | None = None
    role: Literal["admin", "standard_user"] | None = None
    action: str
    outcome: str
    target_type: str | None = None
    target_id: str | None = None
    media_item_id: int | None = None
    session_id: int | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    details: dict | None = None


class AuditLogListResponse(BaseModel):
    events: list[AuditLogEventResponse] = Field(default_factory=list)


class SecurityStatusResponse(BaseModel):
    multiuser_enabled: bool
    private_network_only: bool
    session_ttl_hours: int
    playback_token_ttl_seconds: int


class ScanStatusPayload(BaseModel):
    running: bool
    job_id: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    reason: str | None = None
    files_seen: int = 0
    files_changed: int = 0
    files_removed: int = 0
    message: str | None = None


class SystemStatusResponse(BaseModel):
    app_name: str
    status: str
    public_app_origin: str
    backend_api_origin: str
    media_root: str
    media_root_exists: bool
    db_path: str
    ffprobe_available: bool
    total_media_items: int
    total_users: int
    startup_scan_enabled: bool
    backend_bind: str
    frontend_bind: str
    scan: ScanStatusPayload
    transcode: TranscodeStatusResponse
    native_playback: NativePlaybackStatusResponse
    desktop_playback: DesktopPlaybackStatusResponse
    security: SecurityStatusResponse
    last_scan: ScanStatusPayload | None = None
