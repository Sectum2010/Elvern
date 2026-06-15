from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AuthLoginRequest(BaseModel):
    username: str
    password: str


class AuthSignupRequest(BaseModel):
    username: str
    password: str
    confirm_password: str
    invite_code: str


class PasswordHelpRequest(BaseModel):
    username: str


class UserResponse(BaseModel):
    id: int
    username: str
    role: Literal["admin", "standard_user"]
    enabled: bool = True
    assistant_beta_enabled: bool = False
    age_credential: int = Field(default=18, ge=1, le=18)
    session_id: int | None = None


class AuthUserEnvelope(BaseModel):
    user: UserResponse | None = None
    session: Literal["ok", "pending_totp"] = "ok"
    totp_setup_required: bool = False
    challenge_token: str | None = None
    expires_in_seconds: int | None = None


class TotpLoginRequest(BaseModel):
    challenge_token: str
    code: str


class TotpSetupStartResponse(BaseModel):
    secret: str
    qr_svg: str
    issuer: str
    account: str


class TotpSetupVerifyRequest(BaseModel):
    code: str


class TotpSetupVerifyResponse(BaseModel):
    enabled: bool
    recovery_codes: list[str] = Field(default_factory=list)


class TotpSkipResponse(BaseModel):
    skipped_until: str


class TotpStatusResponse(BaseModel):
    enabled: bool
    enabled_at: str | None = None
    recovery_codes_remaining: int = 0
    setup_required: bool = False
    setup_available: bool = False


class TotpDisableRequest(BaseModel):
    password: str
    totp_or_recovery: str


class TotpRegenerateRecoveryCodesRequest(BaseModel):
    password: str
    totp_code: str


class TotpRecoveryCodesResponse(BaseModel):
    recovery_codes: list[str] = Field(default_factory=list)


class AdminUserTotpDisableRequest(BaseModel):
    current_admin_password: str = Field(min_length=1)


class AdminUserTotpDisableResponse(BaseModel):
    disabled: bool
    target_user: int


class AdminUserTotpSetupPromptUpdateRequest(BaseModel):
    enabled: bool


class MessageResponse(BaseModel):
    message: str


class AdminUrlPrefixResponse(BaseModel):
    prefix: str
    generated_at: str | None = None
    rotated_count: int
    days_old: int
    rotation_reminder_due: bool


class AdminUrlPrefixRotateRequest(BaseModel):
    current_admin_password: str = Field(min_length=1)


class AdminUrlPrefixRotateResponse(BaseModel):
    new_prefix: str
    session_revoked: bool = True


ExposureMode = Literal["private", "public"]
ExposurePublicEntryKind = Literal["custom_domain", "direct_ip"]
ExposureReverseProxyProvider = Literal["caddy", "nginx", "cloudflare_tunnel", "manual_other"]


class ExposureModeDraftRequest(BaseModel):
    desired_mode: ExposureMode
    public_entry_kind: ExposurePublicEntryKind | None = None
    public_origin: str | None = None
    private_origin: str | None = None
    reverse_proxy_provider: ExposureReverseProxyProvider | None = None
    acknowledgement: bool = False
    direct_ip_not_recommended_acknowledgement: bool = False
    current_admin_password: str | None = None


class ExposureModePlanResponse(BaseModel):
    active: dict[str, object] = Field(default_factory=dict)
    desired: dict[str, object] = Field(default_factory=dict)
    validation: dict[str, object] = Field(default_factory=dict)
    plan: dict[str, object] = Field(default_factory=dict)
    pending_draft: dict[str, object] | None = None
    prepared_switch: dict[str, object] | None = None
    finalized_profile: dict[str, object] | None = None
    provider_choices: list[str] = Field(default_factory=list)
    public_entry_kinds: list[str] = Field(default_factory=list)
    takes_effect: bool = False


class ExposureMaintenanceLockRequest(BaseModel):
    current_admin_password: str = Field(min_length=1)
    acknowledgement: bool = False


class ExposureMaintenanceLockResponse(BaseModel):
    enabled: bool = False
    reason: str | None = None
    message: str
    created_by_user_id: int | None = None
    created_by_username: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    revoked_non_admin_sessions: int = 0
    affected_non_admin_users: int = 0


class ExposurePrepareSwitchRequest(BaseModel):
    current_admin_password: str = Field(min_length=1)
    acknowledgement: bool = False


class ExposurePreparedSwitchResponse(BaseModel):
    prepared_switch: dict[str, object] | None = None
    takes_effect: bool = False


class ExposureVerifyPreparedSwitchRequest(BaseModel):
    current_admin_password: str = Field(min_length=1)
    acknowledgement: bool = False


class ExposurePreparedSwitchVerificationResponse(BaseModel):
    prepared_switch: dict[str, object] | None = None
    verification: dict[str, object] = Field(default_factory=dict)
    takes_effect: bool = False


class ExposureFinalizeProfileRequest(BaseModel):
    current_admin_password: str = Field(min_length=1)
    acknowledgement: bool = False


class ExposureFinalizedProfileResponse(BaseModel):
    finalized_profile: dict[str, object] | None = None
    takes_effect: bool = False


class CloudSyncStatusResponse(BaseModel):
    status: Literal["success", "partial_failure", "failed", "disabled"] = "disabled"
    provider_auth_required: bool = False
    reconnect_required: bool = False
    message: str = ""
    sources_total: int = 0
    sources_synced: int = 0
    sources_failed: int = 0
    media_rows_written: int = 0
    errors: list[str] = Field(default_factory=list)
    stale_state_warning: str | None = None


class ScanResponse(BaseModel):
    message: str
    running: bool
    job_id: int | None = None
    cloud_sync: CloudSyncStatusResponse | None = None


class SubtitleTrackResponse(BaseModel):
    id: int
    language: str | None = None
    title: str | None = None
    codec: str | None = None
    disposition_default: bool = False


class MediaPlaybackTrackResponse(BaseModel):
    index: int
    codec: str | None = None
    codec_long_name: str | None = None
    language: str | None = None
    title: str | None = None
    channels: int | None = None
    disposition_default: bool = False
    disposition_forced: bool = False
    disposition_commentary: bool = False
    text_based: bool | None = None
    image_based: bool | None = None
    browser_supported: bool = False
    label: str | None = None
    track_source: str | None = None


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
    library_category: Literal["movies", "tv", "anime", "cartoon"] | None = None
    library_category_path: str | None = None
    library_category_name: str | None = None
    library_folder_role: str | None = None
    library_folder_path: str | None = None
    library_folder_name: str | None = None
    poster_url: str | None = None
    edition_label: str | None = None
    quality_tier: Literal["diamond", "gold", "silver", "iron", "bronze", "wood"] | None = None
    quality_label: str | None = None
    genres: list[str] = Field(default_factory=list)
    genre_display: str = "Unknown"
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
    download_access_allowed: bool = False


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
    arrange: dict[str, object] = Field(default_factory=dict)
    available_genres: list[str] = Field(default_factory=list)
    scan_in_progress: bool = False
    total_items: int = 0


class UserSettingsResponse(BaseModel):
    hide_duplicate_movies: bool = True
    hide_recently_added: bool = False
    floating_controls_position: Literal["bottom", "top"] = "bottom"
    floating_library_search_enabled: bool = True
    poster_card_appearance: Literal["classic", "modern", "clean"] = "classic"
    poster_card_display_max_width: Literal[
        "800",
        "1000",
        "1200",
        "1400",
        "1600",
        "1800",
        "2000",
        "2200",
        "original",
    ] = "1400"
    background_mode: Literal["preset", "gradient", "solid", "photo"] = "preset"
    background_preset: Literal["neon", "basic", "midnight", "aurora", "rose", "ocean"] = "neon"
    background_gradient_start: str = "#74114f"
    background_gradient_end: str = "#1b41b5"
    background_gradient_accent: str = "#5c1867"
    background_solid_color: str = "#151a21"
    background_photo_url: str | None = None
    media_library_reference_private_value: str | None = None
    media_library_reference_shared_default_value: str = ""
    media_library_reference_effective_value: str = ""


class UserSettingsUpdateRequest(BaseModel):
    hide_duplicate_movies: bool | None = None
    hide_recently_added: bool | None = None
    floating_controls_position: Literal["bottom", "top"] | None = None
    floating_library_search_enabled: bool | None = None
    poster_card_appearance: str | None = None
    poster_card_display_max_width: str | int | None = None
    background_mode: str | None = None
    background_preset: str | None = None
    background_gradient_start: str | None = None
    background_gradient_end: str | None = None
    background_gradient_accent: str | None = None
    background_solid_color: str | None = None
    media_library_reference_private_value: str | None = None


class MediaLibraryReferenceUpdateRequest(BaseModel):
    value: str = ""


class MediaLibraryReferenceResponse(BaseModel):
    configured_value: str | None = None
    effective_value: str
    default_value: str
    configured_locations: list[str] = Field(default_factory=list)
    effective_locations: list[str] = Field(default_factory=list)
    category_summary: dict[str, list[dict[str, str]]] = Field(default_factory=dict)
    validation_rules: list[str] = Field(default_factory=list)


class BackupCheckpointSummaryResponse(BaseModel):
    checkpoint_id: str
    path: str
    created_at_utc: str | None = None
    backup_format_version: int | None = None
    backup_trigger: str | None = None
    auto_checkpoint: bool = False
    backup_storage: str | None = None
    backup_encrypted: bool = False
    backup_key_source: str | None = None
    contains_secrets: bool = False
    db_integrity_check_result: str | None = None
    total_size_bytes: int = 0
    file_count: int = 0
    git_commit: str | None = None
    git_dirty: bool | None = None
    inspect_valid: bool = False
    inspect_error: str | None = None


class BackupCheckpointListResponse(BaseModel):
    backups_dir: str
    checkpoints: list[BackupCheckpointSummaryResponse] = Field(default_factory=list)


class BackupCheckpointCreateResponse(BaseModel):
    message: str
    warning: str | None = None
    checkpoint: BackupCheckpointSummaryResponse


class BackupCheckpointCreateRequest(BaseModel):
    passphrase: str | None = None


class BackupCheckpointHashMismatchResponse(BaseModel):
    relative_path: str
    expected_sha256: str
    actual_sha256: str


class BackupCheckpointInspectResponse(BaseModel):
    checkpoint_id: str
    path: str
    created_at_utc: str | None = None
    backup_trigger: str | None = None
    auto_checkpoint: bool = False
    backup_storage: str | None = None
    backup_encrypted: bool = False
    backup_key_source: str | None = None
    contains_secrets: bool = False
    warning: str | None = None
    valid: bool = False
    db_integrity_check_result: str | None = None
    total_size_bytes: int = 0
    file_count: int = 0
    files_verified: int = 0
    missing_files: list[str] = Field(default_factory=list)
    hash_mismatches: list[BackupCheckpointHashMismatchResponse] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class BackupCheckpointPassphraseRequest(BaseModel):
    passphrase: str | None = None


class BackupRestorePlanMetadataResponse(BaseModel):
    source_db_path: str | None = None
    source_project_root: str | None = None
    source_public_app_origin: str | None = None
    source_backend_origin: str | None = None
    source_media_root_path: str | None = None
    source_transcode_dir: str | None = None


class BackupRestorePlanCurrentMetadataResponse(BaseModel):
    current_db_path: str
    current_project_root: str
    current_public_app_origin: str
    current_backend_origin: str
    current_media_root_path: str
    current_transcode_dir: str


class BackupRestorePlanComparisonResponse(BaseModel):
    same_project_root: bool = False
    same_db_path: bool = False
    same_public_app_origin: bool = False
    same_backend_origin: bool = False
    same_media_root_path: bool = False


class BackupRestorePlanScopeResponse(BaseModel):
    db_snapshot_available: bool = False
    env_snapshot_available: bool = False
    helper_releases_available: bool = False
    assistant_uploads_available: bool = False
    media_files_included: bool = False
    poster_files_included: bool = False
    transcodes_included: bool = False


class BackupRestorePlanVerificationResponse(BaseModel):
    manifest_exists: bool = False
    db_snapshot_exists: bool = False
    db_integrity_check_result: str | None = None
    files_verified: int = 0
    missing_files: list[str] = Field(default_factory=list)
    hash_mismatches: list[BackupCheckpointHashMismatchResponse] = Field(default_factory=list)


class BackupRestorePlanResponse(BaseModel):
    restore_plan_format_version: int
    checkpoint_id: str
    checkpoint_path: str
    checkpoint_created_at_utc: str | None = None
    checkpoint_valid: bool = False
    blocking_errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    contains_secrets: bool = False
    warning: str | None = None
    backup_trigger: str | None = None
    auto_checkpoint: bool = False
    source_metadata: BackupRestorePlanMetadataResponse
    current_metadata: BackupRestorePlanCurrentMetadataResponse
    comparison: BackupRestorePlanComparisonResponse
    restore_scope: BackupRestorePlanScopeResponse
    not_included: list[str] = Field(default_factory=list)
    required_pre_restore_steps: list[str] = Field(default_factory=list)
    manual_restore_outline: list[str] = Field(default_factory=list)
    verification: BackupRestorePlanVerificationResponse


class LocalDirectoryBrowseEntryResponse(BaseModel):
    name: str
    path: str


class LocalDirectoryBrowseResponse(BaseModel):
    current_path: str
    parent_path: str | None = None
    directories: list[LocalDirectoryBrowseEntryResponse] = Field(default_factory=list)


DirectoryPickerPurpose = Literal["library_reference", "poster_reference", "generic"]


class LocalDirectoryPickRequest(BaseModel):
    path: str = ""
    purpose: DirectoryPickerPurpose = "generic"
    title: str | None = Field(
        default=None,
        description="Deprecated and ignored. Native picker titles are selected server-side by purpose.",
    )
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
    subtitle_tracks: list[MediaPlaybackTrackResponse] = Field(default_factory=list)
    audio_tracks: list[MediaPlaybackTrackResponse] = Field(default_factory=list)
    track_scan_status: str = "not_scanned"
    track_scan_error: str = ""
    track_scan_source: str = ""
    audio_track_diagnostics: dict[str, object] = Field(default_factory=dict)
    subtitle_track_diagnostics: dict[str, object] = Field(default_factory=dict)
    age_group_key: str = ""
    age_group_source: str = ""
    age_group_display_title: str = ""
    age_group_year: int | None = None
    automatic_age_group_key: str = ""
    automatic_age_group_display_title: str = ""
    automatic_age_group_year: int | None = None
    age_group_manual_link: dict[str, object] | None = None
    age_requirement: int | None = Field(default=None, ge=1, le=18)
    age_requirement_display: str = "None"
    age_requirement_updated_at: str | None = None
    age_requirement_updated_by_user_id: int | None = None
    genre_group_key: str = ""
    genre_group_display_title: str = ""
    genre_group_year: int | None = None
    genres: list[str] = Field(default_factory=list)
    genre_display: str = "Unknown"


class MediaAgeRequirementUpdateRequest(BaseModel):
    age_requirement: int | None = Field(default=None, ge=1, le=18)


class MediaGenreUpdateRequest(BaseModel):
    genres: list[str] = Field(default_factory=list)


class MediaAgeManualGroupLinkRequest(BaseModel):
    target_media_item_id: int
    source_item_id: int | None = None
    age_group_key: str | None = None
    note: str | None = None


class GoogleDriveConnectionResponse(BaseModel):
    enabled: bool = False
    connected: bool = False
    account_email: str | None = None
    account_name: str | None = None
    connection_status: Literal["not_configured", "not_connected", "connected", "reconnect_required", "error"] = "not_configured"
    reconnect_required: bool = False
    provider_auth_required: bool = False
    stale_state_warning: str | None = None
    status_message: str = ""


class ProviderAuthRequirementResponse(BaseModel):
    code: Literal["provider_auth_required"] = "provider_auth_required"
    provider: str = "google_drive"
    provider_reason: str = ""
    title: str = "Google Drive connection expired"
    message: str = "Reconnect Google Drive to continue cloud playback."
    reauth_required: bool = True
    allow_reconnect: bool = True
    requires_admin: bool = False


class ProviderAuthStatusResponse(BaseModel):
    provider_auth_required: bool = False
    reconnect_required: bool = False
    provider: str = "google_drive"
    requirement: ProviderAuthRequirementResponse | None = None
    sources_checked: int = 0


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
    sync_status: Literal["never_synced", "current", "stale", "reconnect_required", "error"] = "never_synced"
    provider_auth_required: bool = False
    reconnect_required: bool = False
    status_message: str | None = None
    stale_state_warning: str | None = None
    last_error_message: str | None = None


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
BrowserPlaybackClientDeviceClass = Literal["phone", "tablet", "desktop", "unknown"]
BrowserPlaybackSessionEngineState = Literal["legacy", "starting", "switching", "active", "recovering", "stopped", "failed"]
BrowserPlaybackEpochState = Literal["starting", "warming", "attach_ready", "active", "draining", "ended", "failed"]
BrowserPlaybackModeState = Literal["estimating", "preparing", "ready"]
BrowserPlaybackEstimateSource = str
MobilePlaybackState = Literal["queued", "preparing", "ready", "retargeting", "failed", "stopped", "expired"]
MobilePlaybackWorkerState = Literal["idle", "queued", "running"]
MobilePlaybackLifecycleState = Literal[
    "attached",
    "background-suspended",
    "background-parked",
    "resuming",
    "recovering",
    "fatal",
]


class MobilePlaybackSessionCreateRequest(BaseModel):
    item_id: int = Field(ge=1)
    profile: MobilePlaybackProfile = "mobile_1080p"
    start_position_seconds: float | None = Field(default=None, ge=0)
    engine_mode: BrowserPlaybackEngineMode | None = None
    playback_mode: BrowserPlaybackMode | None = None
    client_device_class: BrowserPlaybackClientDeviceClass | None = None
    selected_audio_stream_index: int | None = Field(default=None, ge=0)


class MobilePlaybackSeekRequest(BaseModel):
    target_position_seconds: float = Field(ge=0)
    last_stable_position_seconds: float | None = Field(default=None, ge=0)
    playing_before_seek: bool | None = None


class MobilePlaybackAudioTrackRequest(BaseModel):
    selected_audio_stream_index: int = Field(ge=0)
    current_position_seconds: float | None = Field(default=None, ge=0)
    playing_before_switch: bool | None = None


class MobilePlaybackSubtitlePrepareResponse(BaseModel):
    stream_index: int = Field(ge=0)
    label: str
    codec: str | None = None
    vtt_url: str
    prepared: bool = True


class MobilePlaybackHeartbeatRequest(BaseModel):
    committed_playhead_seconds: float | None = Field(default=None, ge=0)
    actual_media_element_time_seconds: float | None = Field(default=None, ge=0)
    client_attach_revision: int | None = Field(default=None, ge=0)
    client_probe_bytes: int | None = Field(default=None, ge=0)
    client_probe_duration_ms: int | None = Field(default=None, ge=1)
    lifecycle_state: MobilePlaybackLifecycleState | None = None
    stalled: bool | None = None
    playing: bool | None = None
    selected_hls_engine: str | None = None
    buffer_tier: str | None = None
    client_buffered_ahead_seconds: float | None = Field(default=None, ge=0)
    client_target_forward_buffer_seconds: float | None = Field(default=None, ge=0)
    client_back_buffer_seconds: float | None = Field(default=None, ge=0)
    client_max_buffer_size_bytes: int | None = Field(default=None, ge=0)
    client_ready_state: int | None = Field(default=None, ge=0)
    client_network_state: int | None = Field(default=None, ge=0)
    client_current_time_seconds: float | None = Field(default=None, ge=0)
    client_time_advancing: bool | None = None
    client_playback_stall_reason: str | None = None
    hls_js_config: dict[str, object] | None = None


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
    required_startup_runway_seconds: float | None = Field(default=None, ge=0)
    actual_startup_runway_seconds: float | None = Field(default=None, ge=0)
    effective_goodput_ratio: float | None = Field(default=None, ge=0)
    gate_reason: str | None = None
    lite_undersupply_runway_seconds: float | None = Field(default=None, ge=0)
    lite_undersupply_detected: bool = False
    lite_undersupply_reason: str | None = None
    lite_required_runway_seconds: float | None = Field(default=None, ge=0)
    lite_required_runway_source: str | None = None
    buffer_tier: str | None = None
    server_required_runway_seconds: float | None = Field(default=None, ge=0)
    server_reserve_seconds: float | None = Field(default=None, ge=0)
    client_recommended_forward_buffer_seconds: float | None = Field(default=None, ge=0)
    client_max_forward_buffer_seconds: float | None = Field(default=None, ge=0)
    client_back_buffer_seconds: float | None = Field(default=None, ge=0)
    client_max_buffer_size_bytes: int | None = Field(default=None, ge=0)
    client_buffer_policy_source: str | None = None
    client_buffer_limited_by_memory: bool = False
    selected_hls_engine: str | None = None
    client_buffered_ahead_seconds: float | None = Field(default=None, ge=0)
    client_target_forward_buffer_seconds: float | None = Field(default=None, ge=0)
    client_ready_state: int | None = Field(default=None, ge=0)
    client_network_state: int | None = Field(default=None, ge=0)
    client_current_time_seconds: float | None = Field(default=None, ge=0)
    client_time_advancing: bool | None = None
    client_playback_stall_reason: str | None = None
    hls_js_config: dict[str, object] | None = None
    active_window_start_seconds: float | None = Field(default=None, ge=0)
    active_window_end_seconds: float | None = Field(default=None, ge=0)
    active_window_back_seconds: float | None = Field(default=None, ge=0)
    active_window_forward_seconds: float | None = Field(default=None, ge=0)
    active_window_anchor_seconds: float | None = Field(default=None, ge=0)
    active_window_revision: int | str | None = None
    active_window_reason: str | None = None
    native_hls_window_policy: str | None = None
    client_back_buffer_prune_supported: bool = False
    full_duration_seconds: float | None = Field(default=None, ge=0)
    full_bad_condition_detected: bool = False
    full_bad_condition_reason: str | None = None
    full_bad_condition_reasons: list[str] = Field(default_factory=list)
    full_bad_condition_confidence: str | None = None
    full_bad_condition_mature: bool = False
    full_bad_condition_reserve_required_seconds: float | None = Field(default=None, ge=0)
    full_bad_condition_reserve_target_seconds: float | None = Field(default=None, ge=0)
    full_bad_condition_actual_contiguous_end_seconds: float | None = Field(default=None, ge=0)
    full_bad_condition_actual_contiguous_seconds_after_target: float | None = Field(default=None, ge=0)
    full_bad_condition_reserve_remaining_seconds: float | None = Field(default=None, ge=0)
    full_bad_condition_reserve_satisfied: bool = False
    full_bad_condition_reserve_progress_source: str | None = None
    full_bad_condition_reserve_eta_seconds: float | None = Field(default=None, ge=0)
    full_bad_condition_gate_enabled: bool = True
    full_bad_condition_gate_dry_run_enabled: bool = False
    full_bad_condition_gate_would_block_ready: bool = False
    full_bad_condition_gate_blocks_ready: bool = False
    full_bad_condition_gate_blockers: list[str] = Field(default_factory=list)
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
    selected_audio_stream_index: int | None = Field(default=None, ge=0)
    active_audio_stream_index: int | None = Field(default=None, ge=0)
    pending_audio_stream_index: int | None = Field(default=None, ge=0)
    audio_switch_state: str | None = None
    audio_switch_error: str | None = None
    audio_switch_replacement_epoch_id: str | None = None
    audio_switch_replacement_state: str | None = None
    audio_switch_replacement_reason: str | None = None
    audio_switch_replacement_audio_stream_index: int | None = Field(default=None, ge=0)
    audio_switch_replacement_audio_map: str | None = None
    audio_switch_replacement_last_error: str | None = None
    audio_switch_replacement_ready_end_seconds: float | None = Field(default=None, ge=0)
    audio_switch_replacement_attach_position_seconds: float | None = Field(default=None, ge=0)


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
    disposition_forced: bool = False
    text_based: bool | None = None
    image_based: bool | None = None
    browser_supported: bool = False
    label: str | None = None


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
    age_credential: int = Field(default=18, ge=1, le=18)
    age_credential_display: str = "18+"
    created_at: str
    updated_at: str
    last_login_at: str | None = None
    status_color: Literal["green", "yellow", "orange", "red", "grey"] = "grey"
    status_label: str = "Offline"
    active_sessions: int = 0
    last_seen_at: str | None = None
    last_activity_at: str | None = None
    totp_enabled: bool = False
    totp_enabled_at: str | None = None
    totp_setup_prompt_enabled: bool = False


class AdminUserListResponse(BaseModel):
    users: list[AdminUserResponse] = Field(default_factory=list)


class AdminUserCreateRequest(BaseModel):
    username: str
    password: str
    role: Literal["admin", "standard_user"] = "standard_user"
    enabled: bool = True
    age_credential: int = Field(default=18, ge=1, le=18)


class AdminUserUpdateRequest(BaseModel):
    enabled: bool | None = None
    role: Literal["admin", "standard_user"] | None = None
    age_credential: int | None = Field(default=None, ge=1, le=18)
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


class AdminUserDeleteRequest(BaseModel):
    current_admin_password: str
    confirm: bool = False


class AdminInviteCodeResponse(BaseModel):
    id: int
    code: str | None = None
    created_by_user_id: int
    created_at: str
    expires_at: str
    used_at: str | None = None
    used_by_user_id: int | None = None
    assigned_age: int = Field(default=18, ge=1, le=18)
    assigned_age_display: str = "18+"
    hidden_at: str | None = None
    revoked_at: str | None = None


class AdminInviteCodeListResponse(BaseModel):
    invite_codes: list[AdminInviteCodeResponse] = Field(default_factory=list)


class AdminInviteCodeCreateRequest(BaseModel):
    assigned_age: int = Field(default=18, ge=1, le=18)


class PasswordHelpRequestResponse(BaseModel):
    id: int
    username_snapshot: str
    user_id: int
    requester_ip_address: str | None = None
    requester_user_agent: str | None = None
    created_at: str
    expires_at: str
    status: Literal["pending", "dismissed"] = "pending"


class PasswordHelpRequestListResponse(BaseModel):
    requests: list[PasswordHelpRequestResponse] = Field(default_factory=list)


class PasswordHelpDismissRequest(BaseModel):
    confirm: bool = False


DownloadAccessMode = Literal["none", "all", "selected"]


class DownloadAccessMovieResponse(BaseModel):
    id: int
    title: str
    original_filename: str
    source_kind: Literal["local", "cloud"] = "local"
    file_size: int
    year: int | None = None


class AdminDownloadAccessResponse(BaseModel):
    user_id: int
    access_mode: DownloadAccessMode = "none"
    selected_items: list[DownloadAccessMovieResponse] = Field(default_factory=list)
    updated_at: str | None = None
    updated_by_user_id: int | None = None


class AdminDownloadAccessUpdateRequest(BaseModel):
    access_mode: DownloadAccessMode
    media_item_ids: list[int] = Field(default_factory=list)


class DownloadSessionResponse(BaseModel):
    session_id: int
    download_url: str
    controlled_download_url: str
    controlled_complete_url: str
    controlled_failed_url: str
    controlled_terminate_url: str
    title: str
    download_filename: str
    original_filename: str
    file_size: int
    expires_at: str
    session_token: str


class DownloadSessionStatusRequest(BaseModel):
    message: str | None = None


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


class AdminPlaybackWorkerItemResponse(BaseModel):
    worker_id: str
    session_id: str
    epoch_id: str
    media_item_id: int = Field(ge=1)
    title: str
    playback_mode: str
    profile: str
    transcode_profile_key: str | None = None
    display_profile_label: str | None = None
    playback_surface: str | None = None
    playback_surface_label: str | None = None
    device_class: str | None = None
    device_label: str | None = None
    device_evidence_source: str | None = None
    device_confidence: str | None = None
    source_kind: str
    source_label: str | None = None
    playback_metadata_label: str | None = None
    state: str
    display_status: str | None = None
    display_status_label: str | None = None
    display_status_tone: str | None = None
    display_status_reason: str | None = None
    display_status_priority: int | None = Field(default=None, ge=0)
    runtime_seconds: float | None = Field(default=None, ge=0)
    pid: int | None = Field(default=None, ge=1)
    target_position_seconds: float = Field(ge=0)
    prepared_ranges: list[list[float]] = Field(default_factory=list)
    stop_requested: bool = False
    cleanup_delayed: bool = False
    cleanup_delay_seconds: float | None = Field(default=None, ge=0)
    non_retryable_error: str | None = None
    failure_count: int = Field(default=0, ge=0)
    replacement_count: int = Field(default=0, ge=0)
    assigned_threads: int = Field(default=0, ge=0)
    fixed_assigned_threads_at_dispatch: int | None = Field(default=None, ge=0)
    adaptive_spawn_dry_run_enabled: bool = False
    adaptive_spawn_dry_run_threads: int | None = Field(default=None, ge=0)
    adaptive_spawn_dry_run_reason: str | None = None
    adaptive_spawn_dry_run_blockers: list[str] = Field(default_factory=list)
    adaptive_spawn_dry_run_policy: str | None = None
    adaptive_spawn_dry_run_source: str | None = None
    adaptive_spawn_dry_run_sample_age_seconds: float | None = Field(default=None, ge=0)
    adaptive_spawn_dry_run_sample_mature: bool | None = None
    adaptive_thread_control_enabled: bool = False
    adaptive_thread_control_applied: bool = False
    adaptive_thread_assignment_policy: str | None = None
    adaptive_thread_assignment_reason: str | None = None
    adaptive_thread_assignment_blockers: list[str] = Field(default_factory=list)
    adaptive_thread_assignment_fallback_used: bool = False
    assigned_threads_source: str = "fixed_disabled"
    real_9_prepare_enabled: bool = False
    real_9_prepare_candidate: bool = False
    real_9_prepare_applied: bool = False
    real_9_prepare_blockers: list[str] = Field(default_factory=list)
    effective_ladder_target: int | None = Field(default=None, ge=0)
    lite_adaptive_prepare_candidate: bool = False
    lite_adaptive_prepare_applied: bool = False
    lite_adaptive_prepare_blockers: list[str] = Field(default_factory=list)
    cloud_adaptive_prepare_enabled: bool = False
    cloud_adaptive_prepare_candidate: bool = False
    cloud_adaptive_prepare_applied: bool = False
    cloud_adaptive_prepare_blockers: list[str] = Field(default_factory=list)
    strict_12_prepare_enabled: bool = False
    strict_12_prepare_candidate: bool = False
    strict_12_prepare_applied: bool = False
    strict_12_prepare_blockers: list[str] = Field(default_factory=list)
    strict_12_prepare_reason: str | None = None
    adaptive_downshift_enabled: bool = False
    adaptive_downshift_candidate: bool = False
    adaptive_downshift_mode: str = "none"
    autonomous_maintenance_downshift_enabled: bool = False
    autonomous_maintenance_downshift_candidate: bool = False
    autonomous_maintenance_downshift_blockers: list[str] = Field(default_factory=list)
    maintenance_downshift_suppressed_by_reclaim: bool = False
    donor_reserved_for_reclaim: bool = False
    reclaim_donor_downshift_active: bool = False
    adaptive_downshift_target_threads: int | None = Field(default=None, ge=0)
    adaptive_downshift_policy: str | None = None
    adaptive_downshift_reason: str | None = None
    adaptive_downshift_blockers: list[str] = Field(default_factory=list)
    adaptive_downshift_replacement_epoch_id: str | None = None
    adaptive_downshift_replacement_worker_id: str | None = None
    adaptive_downshift_state: str = "none"
    adaptive_downshift_transition_started_at: str | None = None
    adaptive_downshift_switched_at: str | None = None
    adaptive_downshift_aborted_reason: str | None = None
    adaptive_downshift_pressure_abort_reason: str | None = None
    adaptive_downshift_pressure_snapshot: dict[str, object] = Field(default_factory=dict)
    adaptive_downshift_retry_count: int = Field(default=0, ge=0)
    adaptive_downshift_retry_not_before_seconds: float | None = Field(default=None, ge=0)
    adaptive_downshift_retry_blocker: str | None = None
    adaptive_downshift_last_abort_reason: str | None = None
    adaptive_downshift_replacement_epoch_cap_remaining: int | None = Field(default=None, ge=0)
    adaptive_boost_exit_reason: str | None = None
    current_boost_tier: int | None = Field(default=None, ge=0)
    maintenance_tier_target: int | None = Field(default=None, ge=0)
    downshift_safe_to_apply: bool = False
    downshift_transition_headroom_required: int | None = Field(default=None, ge=0)
    downshift_transition_headroom_available: int | None = Field(default=None, ge=0)
    adaptive_reclaim_enabled: bool = False
    adaptive_reclaim_dry_run_enabled: bool = True
    adaptive_reclaim_candidate: bool = False
    adaptive_reclaim_candidate_reason: str | None = None
    adaptive_reclaim_target_threads: int | None = Field(default=None, ge=0)
    adaptive_reclaim_state: str = "none"
    adaptive_reclaim_request_id: str | None = None
    adaptive_reclaim_consumer_worker_id: str | None = None
    adaptive_reclaim_consumer_session_id: str | None = None
    adaptive_reclaim_consumer_user_id: int | None = Field(default=None, ge=0)
    adaptive_reclaim_consumer_media_item_id: int | None = Field(default=None, ge=0)
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
    adaptive_reclaim_released_threads_expected: int | None = Field(default=None, ge=0)
    adaptive_reclaim_released_threads_measured: int | None = Field(default=None, ge=0)
    adaptive_reclaim_released_cpu_cores_measured: float | None = Field(default=None, ge=0)
    adaptive_reclaim_cpu_headroom_before: int | None = Field(default=None, ge=0)
    adaptive_reclaim_cpu_headroom_after: int | None = Field(default=None, ge=0)
    adaptive_reclaim_route2_cpu_cores_used_before: float | None = Field(default=None, ge=0)
    adaptive_reclaim_route2_cpu_cores_used_after: float | None = Field(default=None, ge=0)
    adaptive_reclaim_user_cpu_cores_used_before: float | None = Field(default=None, ge=0)
    adaptive_reclaim_user_cpu_cores_used_after: float | None = Field(default=None, ge=0)
    adaptive_reclaim_host_cpu_used_cores_before: float | None = Field(default=None, ge=0)
    adaptive_reclaim_host_cpu_used_cores_after: float | None = Field(default=None, ge=0)
    adaptive_reclaim_host_cpu_spare_cores_before: float | None = Field(default=None, ge=0)
    adaptive_reclaim_host_cpu_spare_cores_after: float | None = Field(default=None, ge=0)
    adaptive_reclaim_route2_headroom_before: int | None = Field(default=None, ge=0)
    adaptive_reclaim_route2_headroom_after: int | None = Field(default=None, ge=0)
    adaptive_reclaim_memory_pressure_before: float | None = Field(default=None, ge=0)
    adaptive_reclaim_memory_pressure_after: float | None = Field(default=None, ge=0)
    adaptive_reclaim_external_pressure_before: str | None = None
    adaptive_reclaim_external_pressure_after: str | None = None
    adaptive_reclaim_capacity_sufficient_for_consumer: bool | None = None
    adaptive_reclaim_retry_count: int = Field(default=0, ge=0)
    adaptive_reclaim_retry_not_before_seconds: float | None = Field(default=None, ge=0)
    adaptive_reclaim_retry_blocker: str | None = None
    adaptive_reclaim_blockers: list[str] = Field(default_factory=list)
    adaptive_reclaim_abort_reason: str | None = None
    adaptive_resupply_enabled: bool = False
    adaptive_resupply_dry_run_enabled: bool = True
    adaptive_resupply_needed: bool = False
    adaptive_resupply_reason: str | None = None
    adaptive_resupply_priority: int = Field(default=0, ge=0)
    adaptive_resupply_target_threads: int | None = Field(default=None, ge=0)
    adaptive_resupply_state: str = "none"
    adaptive_resupply_request_id: str | None = None
    adaptive_resupply_original_reclaim_request_id: str | None = None
    adaptive_resupply_donor_worker_id: str | None = None
    adaptive_resupply_replacement_epoch_id: str | None = None
    adaptive_resupply_replacement_worker_id: str | None = None
    adaptive_resupply_started_at: str | None = None
    adaptive_resupply_switched_at: str | None = None
    adaptive_resupply_measured_at: str | None = None
    adaptive_resupply_blockers: list[str] = Field(default_factory=list)
    adaptive_resupply_abort_reason: str | None = None
    adaptive_resupply_stabilization_active: bool = False
    adaptive_resupply_stabilization_until: str | None = None
    adaptive_resupply_stabilization_seconds_remaining: float | None = Field(default=None, ge=0)
    adaptive_resupply_stabilization_reason: str | None = None
    last_resupply_completed_at: str | None = None
    last_resupply_target_threads: int | None = Field(default=None, ge=0)
    resupplied_donor_protection_active: bool = False
    priority_reexpand_pending: bool = False
    priority_reexpand_reason: str | None = None
    donor_protection_active: bool = False
    donor_health_after_resupply: dict[str, object] = Field(default_factory=dict)
    admission_blocked_by_resupply: bool = False
    admission_waiting_for_reclaim: bool = False
    admission_reclaim_possible: bool = False
    admission_reclaim_attempted: bool = False
    admission_reclaim_succeeded: bool = False
    admission_reclaim_failed_reason: str | None = None
    admission_capacity_after_reclaim: int | None = Field(default=None, ge=0)
    admission_hard_block_reason: str | None = None
    process_exists: bool = False
    cpu_cores_used: float | None = Field(default=None, ge=0)
    cpu_percent_of_total: float | None = Field(default=None, ge=0)
    cpu_percent: float | None = Field(default=None, ge=0)
    memory_bytes: int | None = Field(default=None, ge=0)
    memory_percent_of_total: float | None = Field(default=None, ge=0)
    io_read_bytes: int | None = Field(default=None, ge=0)
    io_write_bytes: int | None = Field(default=None, ge=0)
    io_read_bytes_per_second: float | None = Field(default=None, ge=0)
    io_write_bytes_per_second: float | None = Field(default=None, ge=0)
    io_observation_seconds: float | None = Field(default=None, ge=0)
    io_sample_mature: bool = False
    io_sample_stale: bool = True
    io_missing_metrics: list[str] = Field(default_factory=list)
    route2_source_bytes_per_second: float | None = Field(default=None, ge=0)
    route2_source_observation_seconds: float | None = Field(default=None, ge=0)
    route2_source_status: str | None = None
    telemetry_sampled: bool = False
    last_sampled_at: str | None = None
    failure_reason: str | None = None
    adaptive_bottleneck_class: str | None = None
    adaptive_bottleneck_confidence: float | None = Field(default=None, ge=0, le=1)
    adaptive_recommended_threads: int | None = Field(default=None, ge=0)
    adaptive_current_threads: int | None = Field(default=None, ge=0)
    adaptive_safe_to_increase_threads: bool = False
    adaptive_safe_to_decrease_threads: bool = False
    adaptive_reason: str | None = None
    adaptive_missing_metrics: list[str] = Field(default_factory=list)
    runtime_playback_health: str | None = None
    runtime_playback_health_reason: str | None = None
    runtime_supply_rate_x: float | None = Field(default=None, ge=0)
    runtime_supply_observation_seconds: float | None = Field(default=None, ge=0)
    runtime_runway_seconds: float | None = Field(default=None, ge=0)
    runtime_rebalance_role: str | None = None
    runtime_rebalance_reason: str | None = None
    runtime_rebalance_target_threads: int | None = Field(default=None, ge=0)
    runtime_rebalance_can_donate_threads: int = Field(default=0, ge=0)
    runtime_rebalance_priority: int = Field(default=0, ge=0)
    bad_condition_reserve_required: bool = False
    bad_condition_reason: str | None = None
    bad_condition_supply_floor: float | None = Field(default=None, ge=0)
    bad_condition_strong: bool = False
    reserve_start_seconds: float | None = Field(default=None, ge=0)
    reserve_target_ready_end_seconds: float | None = Field(default=None, ge=0)
    reserve_actual_ready_end_seconds: float | None = Field(default=None, ge=0)
    reserve_required_seconds: float | None = Field(default=None, ge=0)
    reserve_remaining_seconds: float | None = Field(default=None, ge=0)
    reserve_satisfied: bool = False
    reserve_blocks_admission: bool = False
    reserve_eta_seconds: float | None = Field(default=None, ge=0)
    runway_delta_per_second: float | None = None
    runway_delta_observation_seconds: float | None = Field(default=None, ge=0)
    runway_delta_mature: bool = False
    full_bad_condition_detected: bool = False
    full_bad_condition_reason: str | None = None
    full_bad_condition_reasons: list[str] = Field(default_factory=list)
    full_bad_condition_confidence: str | None = None
    full_bad_condition_mature: bool = False
    full_bad_condition_reserve_required_seconds: float | None = Field(default=None, ge=0)
    full_bad_condition_reserve_target_seconds: float | None = Field(default=None, ge=0)
    full_bad_condition_actual_contiguous_end_seconds: float | None = Field(default=None, ge=0)
    full_bad_condition_actual_contiguous_seconds_after_target: float | None = Field(default=None, ge=0)
    full_bad_condition_reserve_remaining_seconds: float | None = Field(default=None, ge=0)
    full_bad_condition_reserve_satisfied: bool = False
    full_bad_condition_reserve_progress_source: str | None = None
    full_bad_condition_reserve_eta_seconds: float | None = Field(default=None, ge=0)
    full_bad_condition_gate_enabled: bool = False
    full_bad_condition_gate_dry_run_enabled: bool = True
    full_bad_condition_gate_would_block_ready: bool = False
    full_bad_condition_gate_blocks_ready: bool = False
    full_bad_condition_gate_blockers: list[str] = Field(default_factory=list)
    lite_undersupply_runway_seconds: float | None = Field(default=None, ge=0)
    lite_undersupply_detected: bool = False
    lite_undersupply_reason: str | None = None
    lite_required_runway_seconds: float | None = Field(default=None, ge=0)
    lite_required_runway_source: str | None = None
    ffmpeg_progress_out_time_seconds: float | None = Field(default=None, ge=0)
    ffmpeg_progress_speed_x: float | None = Field(default=None, ge=0)
    ffmpeg_progress_fps: float | None = Field(default=None, ge=0)
    ffmpeg_progress_frame: int | None = Field(default=None, ge=0)
    ffmpeg_progress_updated_at: str | None = None
    ffmpeg_progress_state: str = "unknown"
    ffmpeg_progress_stale: bool = True
    ffmpeg_progress_missing_metrics: list[str] = Field(default_factory=list)
    publish_segment_count: int = Field(default=0, ge=0)
    segment_publish_count: int = Field(default=0, ge=0)
    publish_init_latency_seconds: float | None = Field(default=None, ge=0)
    last_publish_latency_seconds: float | None = Field(default=None, ge=0)
    publish_latency_avg_seconds: float | None = Field(default=None, ge=0)
    publish_latency_max_seconds: float | None = Field(default=None, ge=0)
    last_publish_kind: str | None = None
    closed_loop_role: str | None = None
    closed_loop_reasons: list[str] = Field(default_factory=list)
    closed_loop_confidence: float | None = Field(default=None, ge=0, le=1)
    closed_loop_prepare_boost_needed: bool = False
    closed_loop_prepare_boost_target_threads: int | None = Field(default=None, ge=0)
    closed_loop_downshift_candidate: bool = False
    closed_loop_downshift_target_threads: int | None = Field(default=None, ge=0)
    closed_loop_needs_resource: bool = False
    closed_loop_needs_resource_reason: str | None = None
    closed_loop_donor_candidate: bool = False
    closed_loop_donor_rank: int | None = Field(default=None, ge=1)
    closed_loop_theoretical_donate_threads: int = Field(default=0, ge=0)
    closed_loop_protected_reason: str | None = None
    closed_loop_admission_should_block_new_users: bool = False
    closed_loop_admission_hard_block: bool = False
    closed_loop_admission_block_reason: str | None = None
    closed_loop_admission_block_reasons: list[str] = Field(default_factory=list)
    closed_loop_boost_blocked: bool = False
    closed_loop_boost_blockers: list[str] = Field(default_factory=list)
    closed_loop_boost_warning_reasons: list[str] = Field(default_factory=list)
    closed_loop_primary_bottleneck: str | None = None
    limiting_factor_primary: str | None = None
    limiting_factor_confidence: float | None = Field(default=None, ge=0, le=1)
    limiting_factor_scores: dict[str, float] = Field(default_factory=dict)
    limiting_factor_supporting_signals: list[str] = Field(default_factory=list)
    limiting_factor_blocking_signals: list[str] = Field(default_factory=list)
    limiting_factor_missing_metrics: list[str] = Field(default_factory=list)
    published_rate_x: float | None = Field(default=None, ge=0)
    encoder_rate_x: float | None = Field(default=None, ge=0)
    source_feed_rate_x: float | None = Field(default=None, ge=0)
    source_feed_rate_available: bool = False
    source_feed_rate_mature: bool = False
    source_feed_rate_reason: str | None = None
    source_feed_rate_missing_reason: str | None = None
    publish_efficiency_gap: float | None = Field(default=None, ge=0)
    client_delivery_rate_x: float | None = Field(default=None, ge=0)
    route2_transcode_strategy: str | None = None
    route2_transcode_strategy_confidence: str | None = None
    route2_transcode_strategy_reason: str | None = None
    route2_video_copy_safe: bool = False
    route2_audio_copy_safe: bool = False
    route2_strategy_risk_flags: list[str] = Field(default_factory=list)
    route2_strategy_missing_metadata: list[str] = Field(default_factory=list)
    route2_strategy_metadata_source: str | None = None
    route2_strategy_metadata_trusted: bool = False
    route2_command_adapter_preview_strategy: str | None = None
    route2_command_adapter_active: bool = False
    route2_command_adapter_summary: str | None = None
    route2_command_adapter_fallback_reason: str | None = None
    route2_output_contract_fingerprint: str | None = None
    route2_output_contract_version: str | None = None
    route2_output_contract_missing_fields: list[str] = Field(default_factory=list)
    route2_output_contract_summary: dict[str, object] = Field(default_factory=dict)
    shared_supply_candidate: bool = False
    shared_supply_group_key: str | None = None
    shared_output_key: str | None = None
    absolute_segment_index_start_candidate: int | None = Field(default=None, ge=0)
    absolute_segment_index_end_candidate: int | None = Field(default=None, ge=0)
    shared_output_metadata_written: bool = False
    shared_output_contract_status: str | None = None
    shared_output_ranges_status: str | None = None
    shared_output_range_count: int = Field(default=0, ge=0)
    shared_output_media_bytes_present: bool = False
    shared_output_byte_integrity_validated: bool = False
    shared_output_segment_bytes_stable: bool = False
    shared_output_mixed_writer_conflict: bool = False
    shared_output_conflict_count: int = Field(default=0, ge=0)
    shared_output_conflict_indexes: list[int] = Field(default_factory=list)
    shared_output_serving_allowed: bool = False
    shared_output_serving_blocked: bool = True
    shared_output_serving_blocked_reason: str | None = None
    shared_output_serving_blocked_reasons: list[str] = Field(default_factory=list)
    shared_output_canonical_generation_required: bool = True
    shared_output_canonical_generation_strategy: str | None = None
    shared_output_store_blockers: list[str] = Field(default_factory=list)
    shared_init_write_enabled: bool = False
    shared_init_write_attempted: bool = False
    shared_init_write_status: str | None = None
    shared_init_write_blockers: list[str] = Field(default_factory=list)
    shared_init_hash_sha256: str | None = None
    shared_init_size_bytes: int | None = Field(default=None, ge=0)
    shared_init_path_present: bool = False
    shared_segments_writer_enabled: bool = False
    shared_segment_write_attempted: bool = False
    shared_segment_write_status: str | None = None
    shared_segment_write_count: int = Field(default=0, ge=0)
    shared_segment_write_already_present_count: int = Field(default=0, ge=0)
    shared_segment_write_conflict_count: int = Field(default=0, ge=0)
    shared_segment_write_blockers: list[str] = Field(default_factory=list)
    shared_segment_write_last_index: int | None = Field(default=None, ge=0)
    shared_segment_write_last_hash: str | None = None
    shared_segment_write_range_start_index: int | None = Field(default=None, ge=0)
    shared_segment_write_range_end_index_exclusive: int | None = Field(default=None, ge=0)
    shared_segment_write_conflict_indexes: list[int] = Field(default_factory=list)
    shared_segment_write_serving_blocked_reason: str | None = None
    route2_init_available: bool = False
    route2_init_hash_sha256: str | None = None
    route2_init_hash_available: bool = False
    route2_init_hash_reason: str | None = None
    route2_init_size_bytes: int | None = Field(default=None, ge=0)
    route2_init_metadata_available: bool = False
    route2_init_compatibility_status: str | None = None
    route2_init_compatibility_blockers: list[str] = Field(default_factory=list)
    shared_store_write_plan_available: bool = False
    shared_store_candidate_range_start_index: int | None = Field(default=None, ge=0)
    shared_store_candidate_range_end_index_exclusive: int | None = Field(default=None, ge=0)
    shared_store_candidate_range_start_seconds: float | None = Field(default=None, ge=0)
    shared_store_candidate_range_end_seconds: float | None = Field(default=None, ge=0)
    shared_store_candidate_segment_count: int = Field(default=0, ge=0)
    shared_store_write_candidate_count: int = Field(default=0, ge=0)
    shared_store_write_blockers: list[str] = Field(default_factory=list)
    shared_store_mapping_confidence: str | None = None
    shared_store_mapping_notes: list[str] = Field(default_factory=list)
    shared_supply_group_size: int = Field(default=1, ge=0)
    shared_supply_level_candidate: str | None = None
    compatible_existing_workload_ids: list[str] = Field(default_factory=list)
    compatible_existing_worker_ids: list[str] = Field(default_factory=list)
    shared_supply_blockers: list[str] = Field(default_factory=list)
    shared_supply_permission_status: str | None = None
    estimated_duplicate_workers_avoided: int = Field(default=0, ge=0)
    shared_supply_notes: list[str] = Field(default_factory=list)
    started_at: str | None = None
    last_seen_at: str


class AdminPlaybackWorkersSharedSupplyGroupResponse(BaseModel):
    group_key: str
    workload_count: int = Field(default=0, ge=0)
    candidate_count: int = Field(default=0, ge=0)
    blockers: list[str] = Field(default_factory=list)
    estimated_duplicate_workers_avoided: int = Field(default=0, ge=0)
    shared_supply_group_init_compatibility_status: str | None = None
    shared_supply_group_init_hashes_match: bool = False
    shared_supply_group_init_blockers: list[str] = Field(default_factory=list)


class AdminPlaybackWorkersUserSummaryResponse(BaseModel):
    user_id: int
    username: str | None = None
    allocated_cpu_cores: int = Field(default=0, ge=0)
    allocated_budget_cores: int = Field(default=0, ge=0)
    cpu_cores_used: float | None = Field(default=None, ge=0)
    cpu_percent_of_user_limit: float | None = Field(default=None, ge=0)
    memory_bytes: int | None = Field(default=None, ge=0)
    memory_percent_of_total: float | None = Field(default=None, ge=0)
    running_workers: int = Field(default=0, ge=0)
    queued_workers: int = Field(default=0, ge=0)
    total_workers: int = Field(default=0, ge=0)
    items: list[AdminPlaybackWorkerItemResponse] = Field(default_factory=list)


class AdminNativePlaybackItemResponse(BaseModel):
    playback_kind: str = "native"
    session_id: str
    media_item_id: int = Field(ge=1)
    title: str = ""
    user_id: int = Field(ge=1)
    username: str | None = None
    client_name: str | None = None
    external_player: bool = False
    auth_session_coupled: bool = False
    playback_surface: str = "unknown"
    playback_surface_label: str = "Unknown playback"
    device_class: str = "unknown"
    device_label: str = "Unknown device"
    device_evidence_source: str = "unavailable"
    device_confidence: str = "unknown"
    display_profile_label: str = "unknown quality"
    source_kind: str = "local"
    source_label: str = "Unknown source"
    playback_metadata_label: str = ""
    created_at: str | None = None
    expires_at: str | None = None
    last_seen_at: str | None = None
    last_stream_activity_at: str | None = None
    closed_at: str | None = None
    revoked_at: str | None = None
    last_position_seconds: float | None = Field(default=None, ge=0)
    last_duration_seconds: float | None = Field(default=None, ge=0)
    display_status: str = "unknown"
    display_status_label: str = "Unknown"
    display_status_tone: str = "neutral"
    display_status_reason: str = ""
    display_status_priority: int = Field(default=99, ge=0)


class AdminNativePlaybacksUserSummaryResponse(BaseModel):
    user_id: int
    username: str | None = None
    total_native_playbacks: int = Field(default=0, ge=0)
    running_native_playbacks: int = Field(default=0, ge=0)
    idle_native_playbacks: int = Field(default=0, ge=0)
    items: list[AdminNativePlaybackItemResponse] = Field(default_factory=list)


class AdminPlaybackWorkersStatusResponse(BaseModel):
    shared_output_store_enabled: str | bool = "metadata_only"
    shared_output_root: str | None = None
    shared_output_metadata_version: str | None = None
    shared_output_store_ready_for_segments: bool = False
    shared_output_store_records_count: int = Field(default=0, ge=0)
    shared_output_metadata_write_errors: list[str] = Field(default_factory=list)
    shared_output_init_records_count: int = Field(default=0, ge=0)
    shared_output_init_write_errors: list[str] = Field(default_factory=list)
    shared_output_segments_records_count: int = Field(default=0, ge=0)
    shared_output_ranges_media_bytes_present_count: int = Field(default=0, ge=0)
    shared_output_segment_write_errors: list[str] = Field(default_factory=list)
    cpu_upbound_percent: int = Field(ge=0)
    cpu_budget_percent: int = Field(ge=0)
    total_cpu_cores: int = Field(ge=1)
    route2_cpu_upbound_cores: int = Field(ge=1)
    total_route2_budget_cores: int = Field(ge=1)
    route2_cpu_cores_used: float | None = Field(default=None, ge=0)
    route2_cpu_cores_used_total: float | None = Field(default=None, ge=0)
    route2_cpu_percent_of_total: float | None = Field(default=None, ge=0)
    route2_cpu_percent_of_upbound: float | None = Field(default=None, ge=0)
    route2_resource_sample_mature: bool = False
    route2_resource_sample_stale: bool = True
    route2_resource_sample_age_seconds: float | None = Field(default=None, ge=0)
    host_cpu_total_cores: int | None = Field(default=None, ge=1)
    host_cpu_used_cores: float | None = Field(default=None, ge=0)
    host_cpu_used_percent: float | None = Field(default=None, ge=0)
    external_cpu_cores_used_estimate: float | None = Field(default=None, ge=0)
    external_cpu_percent_estimate: float | None = Field(default=None, ge=0)
    external_ffmpeg_process_count: int = Field(default=0, ge=0)
    route2_worker_ffmpeg_process_count: int = Field(default=0, ge=0)
    elvern_owned_ffmpeg_process_count: int = Field(default=0, ge=0)
    elvern_owned_ffmpeg_cpu_cores_estimate: float | None = Field(default=None, ge=0)
    external_ffmpeg_cpu_cores_estimate: float | None = Field(default=None, ge=0)
    external_pressure_level: str = "unknown"
    external_pressure_reason: str | None = None
    route2_resource_missing_metrics: list[str] = Field(default_factory=list)
    psi_sample_available: bool = False
    psi_cpu_some_avg10: float | None = Field(default=None, ge=0)
    psi_cpu_full_avg10: float | None = Field(default=None, ge=0)
    psi_io_some_avg10: float | None = Field(default=None, ge=0)
    psi_io_full_avg10: float | None = Field(default=None, ge=0)
    psi_memory_some_avg10: float | None = Field(default=None, ge=0)
    psi_memory_full_avg10: float | None = Field(default=None, ge=0)
    psi_missing_metrics: list[str] = Field(default_factory=list)
    cgroup_pressure_available: bool = False
    cgroup_cpu_nr_periods: int | None = Field(default=None, ge=0)
    cgroup_cpu_nr_throttled: int | None = Field(default=None, ge=0)
    cgroup_cpu_throttled_usec: int | None = Field(default=None, ge=0)
    cgroup_cpu_throttled_delta: int | None = Field(default=None, ge=0)
    cgroup_cpu_throttled_usec_delta: int | None = Field(default=None, ge=0)
    cgroup_cpu_some_avg10: float | None = Field(default=None, ge=0)
    cgroup_cpu_full_avg10: float | None = Field(default=None, ge=0)
    cgroup_io_some_avg10: float | None = Field(default=None, ge=0)
    cgroup_io_full_avg10: float | None = Field(default=None, ge=0)
    cgroup_memory_some_avg10: float | None = Field(default=None, ge=0)
    cgroup_memory_full_avg10: float | None = Field(default=None, ge=0)
    cgroup_missing_metrics: list[str] = Field(default_factory=list)
    total_memory_bytes: int | None = Field(default=None, ge=0)
    route2_memory_bytes: int | None = Field(default=None, ge=0)
    route2_memory_bytes_total: int | None = Field(default=None, ge=0)
    route2_memory_percent_of_total: float | None = Field(default=None, ge=0)
    shared_supply_groups: list[AdminPlaybackWorkersSharedSupplyGroupResponse] = Field(default_factory=list)
    active_worker_count: int = Field(default=0, ge=0)
    queued_worker_count: int = Field(default=0, ge=0)
    active_decoding_user_count: int = Field(default=0, ge=0)
    active_route2_workload_count: int = Field(default=0, ge=0)
    per_user_budget_cores: int = Field(default=0, ge=0)
    max_worker_threads: int = Field(default=0, ge=0)
    adaptive_max_worker_threads: int = Field(default=0, ge=0)
    adaptive_thread_control_enabled: bool = True
    adaptive_thread_control_local_only: bool = False
    adaptive_thread_control_cloud_enabled: bool = True
    adaptive_thread_control_strict_12_enabled: bool = True
    adaptive_thread_control_real_9_prepare_enabled: bool = True
    adaptive_downshift_enabled: bool = True
    adaptive_downshift_dry_run_enabled: bool = False
    autonomous_maintenance_downshift_enabled: bool = True
    autonomous_maintenance_downshift_dry_run_enabled: bool = False
    adaptive_reclaim_enabled: bool = True
    adaptive_reclaim_dry_run_enabled: bool = False
    adaptive_resupply_enabled: bool = True
    adaptive_resupply_dry_run_enabled: bool = False
    adaptive_resupply_stabilization_seconds: int = Field(default=120, ge=0)
    full_bad_condition_gate_enabled: bool = True
    full_bad_condition_gate_dry_run_enabled: bool = False
    workers_by_user: list[AdminPlaybackWorkersUserSummaryResponse] = Field(default_factory=list)
    native_playback_count: int = Field(default=0, ge=0)
    native_playback_running_count: int = Field(default=0, ge=0)
    native_playback_idle_count: int = Field(default=0, ge=0)
    native_playbacks_by_user: list[AdminNativePlaybacksUserSummaryResponse] = Field(default_factory=list)


class AdminTechnicalMetadataCurrentItemResponse(BaseModel):
    id: int = Field(ge=1)
    title: str = ""


class AdminTechnicalMetadataBatchSummaryResponse(BaseModel):
    limit: int = Field(ge=1)
    retry_failed: bool = False
    scanned_candidates: int = Field(default=0, ge=0)
    probed: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    stale: int = Field(default=0, ge=0)
    cloud_skipped: int = Field(default=0, ge=0)
    current_item: AdminTechnicalMetadataCurrentItemResponse | None = None
    errors: list[str] = Field(default_factory=list)


class AdminTechnicalMetadataEnrichmentRequest(BaseModel):
    limit: int = Field(default=5, ge=1, le=25)
    retry_failed: bool = False


class AdminTechnicalMetadataEnrichmentTriggerResponse(BaseModel):
    started: bool
    running: bool
    summary: AdminTechnicalMetadataBatchSummaryResponse | None = None


class AdminTechnicalMetadataStatusResponse(BaseModel):
    total_local_items: int = Field(default=0, ge=0)
    probed_local_items: int = Field(default=0, ge=0)
    stale_local_items: int = Field(default=0, ge=0)
    failed_local_items: int = Field(default=0, ge=0)
    never_probed_local_items: int = Field(default=0, ge=0)
    cloud_items_not_supported: int = Field(default=0, ge=0)
    running: bool = False
    current_item: AdminTechnicalMetadataCurrentItemResponse | None = None
    last_summary: AdminTechnicalMetadataBatchSummaryResponse | None = None


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
    session_idle_timeout_hours: int
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
