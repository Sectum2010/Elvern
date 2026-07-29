from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .config import Settings
from .db_hidden_movie_keys import (
    _backfill_hidden_movie_keys,
    _build_hidden_movie_key,
    preserve_hidden_movie_keys_for_media_item,
)
from .services.at_rest_encryption import CIPHERTEXT_PREFIX, encrypt_at_rest


logger = logging.getLogger(__name__)
BROWSER_SESSION_HMAC_MIGRATION_NAME = "browser_session_hmac1_v1"
ACCOUNT_SHORT_TOKEN_HMAC_MIGRATION_NAME = "account_short_token_hmac1_v1"
FLOATING_POSITION_RETIREMENT_MIGRATION = "floating_controls_position_retired_v1"
TOKEN_HASH_MIGRATION_REVOKE_REASON = "token_hash_migration"
TOKEN_HASH_PREFIX = "hmac1$"
TOTP_PENDING_SECRET_TTL_SECONDS = 10 * 60


TABLE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'standard_user',
        enabled INTEGER NOT NULL DEFAULT 1,
        age_credential INTEGER NOT NULL DEFAULT 18 CHECK (age_credential BETWEEN 1 AND 18),
        last_login_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        session_token_hash TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        last_seen_at TEXT,
        last_activity_at TEXT,
        user_agent TEXT,
        ip_address TEXT,
        revoked_at TEXT,
        revoked_reason TEXT,
        cleanup_confirmed_at TEXT,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_recovery_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        code_hash TEXT NOT NULL,
        used_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS login_challenges (
        challenge_token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        expires_at_unix REAL NOT NULL,
        ip_address TEXT,
        user_agent TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS totp_pending_secrets (
        user_id INTEGER PRIMARY KEY,
        secret TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS media_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        original_filename TEXT NOT NULL,
        file_path TEXT NOT NULL UNIQUE,
        source_kind TEXT NOT NULL DEFAULT 'local',
        library_source_id INTEGER,
        external_media_id TEXT,
        cloud_mime_type TEXT,
        cloud_resource_key TEXT,
        hidden_copy_identity TEXT,
        series_folder_key TEXT,
        series_folder_name TEXT,
        library_category TEXT,
        library_category_path TEXT,
        library_category_name TEXT,
        library_folder_role TEXT,
        library_folder_path TEXT,
        library_folder_name TEXT,
        file_size INTEGER NOT NULL,
        file_mtime REAL NOT NULL,
        duration_seconds REAL,
        width INTEGER,
        height INTEGER,
        video_codec TEXT,
        audio_codec TEXT,
        container TEXT,
        year INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_scanned_at TEXT NOT NULL,
        FOREIGN KEY (library_source_id) REFERENCES library_sources (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS media_item_technical_metadata (
        media_item_id INTEGER PRIMARY KEY,
        metadata_version INTEGER NOT NULL,
        metadata_source TEXT NOT NULL DEFAULT 'unknown',
        probe_status TEXT NOT NULL DEFAULT 'never',
        probe_error TEXT,
        probed_at TEXT,
        updated_at TEXT NOT NULL,
        source_fingerprint TEXT,
        container TEXT,
        duration_seconds REAL,
        bit_rate INTEGER,
        video_codec TEXT,
        video_profile TEXT,
        video_level TEXT,
        pixel_format TEXT,
        bit_depth INTEGER,
        width INTEGER,
        height INTEGER,
        color_transfer TEXT,
        color_primaries TEXT,
        color_space TEXT,
        hdr_detected INTEGER,
        dolby_vision_detected INTEGER,
        audio_codec TEXT,
        audio_profile TEXT,
        audio_channels INTEGER,
        audio_channel_layout TEXT,
        audio_sample_rate INTEGER,
        subtitle_count INTEGER,
        raw_probe_summary_json TEXT,
        FOREIGN KEY (media_item_id) REFERENCES media_items (id) ON DELETE CASCADE,
        CHECK (probe_status IN ('never', 'probed', 'failed', 'stale'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hidden_copy_identity_aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hidden_copy_identity TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        library_source_id INTEGER NOT NULL DEFAULT 0,
        locator_hash TEXT NOT NULL,
        evidence_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        UNIQUE (
            hidden_copy_identity,
            source_kind,
            library_source_id,
            locator_hash,
            evidence_hash
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS subtitle_tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        media_item_id INTEGER NOT NULL,
        language TEXT,
        title TEXT,
        codec TEXT,
        disposition_default INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (media_item_id) REFERENCES media_items (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS playback_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        media_item_id INTEGER NOT NULL,
        position_seconds REAL NOT NULL DEFAULT 0,
        duration_seconds REAL,
        watch_seconds_total REAL NOT NULL DEFAULT 0,
        completed INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (media_item_id) REFERENCES media_items (id) ON DELETE CASCADE,
        UNIQUE (user_id, media_item_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS playback_watch_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        media_item_id INTEGER NOT NULL,
        watched_seconds REAL NOT NULL,
        recorded_at_epoch INTEGER NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (media_item_id) REFERENCES media_items (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS playback_tracking_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        media_item_id INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        playback_mode TEXT NOT NULL,
        tracking_source TEXT NOT NULL DEFAULT 'direct',
        native_session_id TEXT,
        position_seconds REAL,
        duration_seconds REAL,
        completed INTEGER NOT NULL DEFAULT 0,
        occurred_at TEXT NOT NULL,
        recorded_at_epoch INTEGER NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (media_item_id) REFERENCES media_items (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scan_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL,
        reason TEXT NOT NULL,
        files_seen INTEGER NOT NULL DEFAULT 0,
        files_changed INTEGER NOT NULL DEFAULT 0,
        files_removed INTEGER NOT NULL DEFAULT 0,
        message TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        name TEXT PRIMARY KEY,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        UNIQUE (user_id, key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS invite_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code_hash TEXT NOT NULL UNIQUE,
        created_by_user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        assigned_age INTEGER NOT NULL DEFAULT 18 CHECK (assigned_age BETWEEN 1 AND 18),
        used_at TEXT,
        used_by_user_id INTEGER,
        hidden_at TEXT,
        revoked_at TEXT,
        FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (used_by_user_id) REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS media_age_requirements (
        age_group_key TEXT PRIMARY KEY,
        display_title TEXT NOT NULL,
        year INTEGER,
        age_requirement INTEGER CHECK (age_requirement IS NULL OR (age_requirement BETWEEN 1 AND 18)),
        updated_at TEXT NOT NULL,
        updated_by_user_id INTEGER,
        FOREIGN KEY (updated_by_user_id) REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS media_age_manual_group_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        age_group_key TEXT NOT NULL,
        media_item_id INTEGER NOT NULL UNIQUE,
        created_by_user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        note TEXT,
        FOREIGN KEY (media_item_id) REFERENCES media_items(id) ON DELETE CASCADE,
        FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS media_genre_groups (
        genre_group_key TEXT PRIMARY KEY,
        display_title TEXT NOT NULL,
        year INTEGER,
        genres_json TEXT NOT NULL DEFAULT '[]',
        updated_at TEXT NOT NULL,
        updated_by_user_id INTEGER,
        FOREIGN KEY (updated_by_user_id) REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS password_help_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username_snapshot TEXT NOT NULL,
        user_id INTEGER,
        requester_bucket_hash TEXT,
        requester_ip_address TEXT,
        requester_user_agent TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        dismissed_at TEXT,
        dismissed_by_user_id INTEGER,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL,
        FOREIGN KEY (dismissed_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
        CHECK (status IN ('pending', 'dismissed'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS download_access_grants (
        user_id INTEGER PRIMARY KEY,
        access_mode TEXT NOT NULL DEFAULT 'none',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by_user_id INTEGER,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (updated_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
        CHECK (access_mode IN ('none', 'all', 'selected'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS download_access_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        media_item_id INTEGER NOT NULL,
        granted_by_user_id INTEGER,
        granted_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (media_item_id) REFERENCES media_items (id) ON DELETE CASCADE,
        FOREIGN KEY (granted_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
        UNIQUE (user_id, media_item_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS download_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_token_hash TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL,
        media_item_id INTEGER NOT NULL,
        auth_session_id INTEGER,
        auth_session_required INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        completed_at TEXT,
        failed_at TEXT,
        revoked_at TEXT,
        last_error TEXT,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (media_item_id) REFERENCES media_items (id) ON DELETE CASCADE,
        FOREIGN KEY (auth_session_id) REFERENCES sessions (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS google_drive_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE,
        google_account_id TEXT NOT NULL,
        email TEXT,
        display_name TEXT,
        refresh_token TEXT NOT NULL,
        access_token TEXT,
        access_token_expires_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS google_oauth_states (
        state_token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS library_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_user_id INTEGER NOT NULL,
        provider TEXT NOT NULL,
        google_drive_account_id INTEGER,
        resource_type TEXT NOT NULL,
        resource_id TEXT NOT NULL,
        display_name TEXT NOT NULL,
        local_path TEXT,
        is_shared INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_synced_at TEXT,
        last_error TEXT,
        FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (google_drive_account_id) REFERENCES google_drive_accounts (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_hidden_library_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        library_source_id INTEGER NOT NULL,
        hidden_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (library_source_id) REFERENCES library_sources (id) ON DELETE CASCADE,
        UNIQUE (user_id, library_source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_hidden_media_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        media_item_id INTEGER NOT NULL,
        hidden_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (media_item_id) REFERENCES media_items (id) ON DELETE CASCADE,
        UNIQUE (user_id, media_item_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_hidden_movie_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        movie_key TEXT NOT NULL,
        display_title TEXT NOT NULL,
        year INTEGER NOT NULL,
        edition_identity TEXT NOT NULL DEFAULT 'standard',
        hidden_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        UNIQUE (user_id, movie_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS global_hidden_media_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        media_item_id INTEGER NOT NULL UNIQUE,
        hidden_by_user_id INTEGER NOT NULL,
        hidden_at TEXT NOT NULL,
        FOREIGN KEY (media_item_id) REFERENCES media_items (id) ON DELETE CASCADE,
        FOREIGN KEY (hidden_by_user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS global_hidden_movie_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        movie_key TEXT NOT NULL UNIQUE,
        display_title TEXT NOT NULL,
        year INTEGER NOT NULL,
        edition_identity TEXT NOT NULL DEFAULT 'standard',
        hidden_by_user_id INTEGER NOT NULL,
        hidden_at TEXT NOT NULL,
        FOREIGN KEY (hidden_by_user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS native_playback_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL UNIQUE,
        access_token_hash TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        media_item_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        closed_at TEXT,
        revoked_at TEXT,
        auth_session_id INTEGER,
        created_from_auth_session_id INTEGER,
        client_name TEXT,
        user_agent TEXT,
        source_ip TEXT,
        last_progress_recorded_at TEXT,
        last_position_seconds REAL NOT NULL DEFAULT 0,
        last_duration_seconds REAL,
        last_error TEXT,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (media_item_id) REFERENCES media_items (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        user_id INTEGER,
        username TEXT,
        role TEXT,
        action TEXT NOT NULL,
        outcome TEXT NOT NULL,
        target_type TEXT,
        target_id TEXT,
        media_item_id INTEGER,
        session_id INTEGER,
        ip_address TEXT,
        user_agent TEXT,
        details_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS login_failures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bucket_kind TEXT NOT NULL,
        bucket_key TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        occurred_at_unix REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS login_lockouts (
        bucket_kind TEXT NOT NULL,
        bucket_key TEXT NOT NULL,
        blocked_until_unix REAL NOT NULL,
        reason TEXT,
        PRIMARY KEY (bucket_kind, bucket_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS security_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_kind TEXT NOT NULL,
        actor_user_id INTEGER,
        actor_username TEXT,
        ip_address TEXT,
        user_agent TEXT,
        occurred_at TEXT NOT NULL,
        details_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS client_devices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT NOT NULL UNIQUE,
        last_user_id INTEGER,
        browser_platform TEXT,
        browser_user_agent TEXT,
        helper_platform TEXT,
        helper_arch TEXT,
        helper_version TEXT,
        helper_channel TEXT NOT NULL DEFAULT 'stable',
        helper_last_seen_at TEXT,
        helper_vlc_detection_state TEXT,
        helper_vlc_detection_path TEXT,
        helper_vlc_detection_checked_at TEXT,
        app_last_seen_at TEXT,
        last_ip_address TEXT,
        friendly_name TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (last_user_id) REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS helper_releases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel TEXT NOT NULL DEFAULT 'stable',
        runtime_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        version TEXT NOT NULL,
        filename TEXT NOT NULL,
        relative_path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        dotnet_runtime_required TEXT NOT NULL DEFAULT '8.x',
        published_at TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS desktop_vlc_handoffs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        handoff_id TEXT NOT NULL UNIQUE,
        access_token_hash TEXT NOT NULL,
        auth_session_id INTEGER,
        user_id INTEGER NOT NULL,
        media_item_id INTEGER NOT NULL,
        platform TEXT NOT NULL,
        strategy TEXT NOT NULL,
        resolved_target TEXT NOT NULL,
        resume_seconds REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        revoked_at TEXT,
        device_id TEXT,
        helper_version TEXT,
        helper_platform TEXT,
        helper_arch TEXT,
        helper_vlc_detection_state TEXT,
        helper_vlc_detection_path TEXT,
        helper_vlc_detection_checked_at TEXT,
        resolved_at TEXT,
        user_agent TEXT,
        source_ip TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS desktop_helper_verifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verification_id TEXT NOT NULL UNIQUE,
        access_token_hash TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        platform TEXT NOT NULL,
        device_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        resolved_at TEXT,
        helper_version TEXT,
        helper_platform TEXT,
        helper_arch TEXT,
        helper_vlc_detection_state TEXT,
        helper_vlc_detection_path TEXT,
        helper_vlc_detection_checked_at TEXT,
        source_ip TEXT,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assistant_user_access (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE,
        assistant_beta_enabled INTEGER NOT NULL DEFAULT 0,
        enabled_by_user_id INTEGER,
        enabled_at TEXT,
        disabled_at TEXT,
        note TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (enabled_by_user_id) REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assistant_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_number TEXT NOT NULL UNIQUE,
        submitted_by_user_id INTEGER NOT NULL,
        submitted_by_display_name_snapshot TEXT NOT NULL,
        request_type TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        repro_steps TEXT,
        expected_result TEXT,
        actual_result TEXT,
        urgency TEXT NOT NULL DEFAULT 'normal',
        page_context TEXT,
        platform TEXT,
        app_version TEXT,
        source_context TEXT,
        related_entity_type TEXT,
        related_entity_id TEXT,
        status TEXT NOT NULL DEFAULT 'new',
        status_updated_at TEXT,
        status_updated_by_user_id INTEGER,
        admin_note TEXT,
        duplicate_group_key TEXT,
        is_archived INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (submitted_by_user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (status_updated_by_user_id) REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assistant_request_attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        attachment_type TEXT NOT NULL,
        storage_kind TEXT NOT NULL,
        storage_path_safe_ref TEXT NOT NULL,
        original_filename TEXT,
        mime_type TEXT,
        size_bytes INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY (request_id) REFERENCES assistant_requests (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assistant_attachment_external_open_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id TEXT NOT NULL UNIQUE,
        access_token_hash TEXT NOT NULL,
        attachment_id INTEGER NOT NULL,
        issued_by_user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        last_opened_at TEXT,
        FOREIGN KEY (attachment_id) REFERENCES assistant_request_attachments (id) ON DELETE CASCADE,
        FOREIGN KEY (issued_by_user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assistant_triage_drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        created_by TEXT NOT NULL,
        model_provider TEXT,
        model_name TEXT,
        summary TEXT NOT NULL,
        classification TEXT NOT NULL,
        risk_level TEXT NOT NULL,
        confidence_level TEXT NOT NULL,
        possible_duplicate_request_ids_json TEXT,
        suggested_next_step TEXT,
        suggested_owner TEXT,
        needs_admin_approval INTEGER NOT NULL DEFAULT 0,
        needs_external_access_approval INTEGER NOT NULL DEFAULT 0,
        reversibility_impact_if_action_taken TEXT NOT NULL DEFAULT 'unknown',
        notes_for_admin TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (request_id) REFERENCES assistant_requests (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assistant_action_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        triage_draft_id INTEGER,
        created_by_type TEXT NOT NULL,
        created_by_user_id INTEGER,
        action_type TEXT NOT NULL,
        target_scope TEXT NOT NULL,
        reason TEXT NOT NULL,
        proposed_plan TEXT,
        risk_level TEXT NOT NULL,
        requires_admin_approval INTEGER NOT NULL DEFAULT 1,
        requires_external_access_approval INTEGER NOT NULL DEFAULT 0,
        reversibility_level TEXT NOT NULL DEFAULT 'unknown',
        warning_if_not_fully_reversible TEXT,
        status TEXT NOT NULL DEFAULT 'draft',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (request_id) REFERENCES assistant_requests (id) ON DELETE CASCADE,
        FOREIGN KEY (triage_draft_id) REFERENCES assistant_triage_drafts (id) ON DELETE SET NULL,
        FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assistant_approval_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action_request_id INTEGER NOT NULL,
        decision TEXT NOT NULL,
        decided_by_user_id INTEGER NOT NULL,
        decision_note TEXT,
        backup_required INTEGER NOT NULL DEFAULT 0,
        rollback_plan_required INTEGER NOT NULL DEFAULT 0,
        external_access_approved INTEGER NOT NULL DEFAULT 0,
        decided_at TEXT NOT NULL,
        FOREIGN KEY (action_request_id) REFERENCES assistant_action_requests (id) ON DELETE CASCADE,
        FOREIGN KEY (decided_by_user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assistant_change_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        linked_action_request_id INTEGER,
        created_at TEXT NOT NULL,
        created_by_type TEXT NOT NULL,
        change_summary TEXT,
        reversibility_level TEXT NOT NULL DEFAULT 'unknown',
        backup_reference TEXT,
        revert_recipe_draft TEXT,
        verification_plan_draft TEXT,
        status TEXT NOT NULL DEFAULT 'draft',
        FOREIGN KEY (request_id) REFERENCES assistant_requests (id) ON DELETE CASCADE,
        FOREIGN KEY (linked_action_request_id) REFERENCES assistant_action_requests (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS library_revision_counters (
        scope_kind TEXT NOT NULL CHECK (scope_kind IN ('global', 'user')),
        scope_id INTEGER NOT NULL,
        layer TEXT NOT NULL CHECK (layer IN ('catalog', 'presentation', 'permission', 'user_overlay', 'progress')),
        counter INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (scope_kind, scope_id, layer)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS poster_index_fingerprints (
        root_identity_hash TEXT PRIMARY KEY,
        fingerprint_hash TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
)


LIBRARY_REVISION_TRIGGER_VERSION = "v2"
LIBRARY_REVISION_TRIGGER_PREFIX = "trg_library_revision_"


def _changed_columns(*columns: str) -> str:
    return " OR ".join(f"OLD.{column} IS NOT NEW.{column}" for column in columns)


def _library_revision_trigger_statements() -> tuple[str, ...]:
    trigger_specs: list[tuple[str, str, str, str, str | None]] = [
        (
            "media_items", "catalog", "global", "0",
            _changed_columns(
                "title", "original_filename", "file_path", "source_kind", "library_source_id",
                "external_media_id", "cloud_mime_type", "cloud_resource_key", "series_folder_key",
                "series_folder_name", "library_category", "library_category_path",
                "library_category_name", "library_folder_role", "library_folder_path",
                "library_folder_name", "file_size", "file_mtime", "duration_seconds", "width",
                "height", "video_codec", "audio_codec", "container", "year",
            ),
        ),
        (
            "media_item_technical_metadata", "catalog", "global", "0",
            _changed_columns(
                "metadata_version", "source_fingerprint", "container", "duration_seconds", "bit_rate",
                "video_codec", "video_profile", "video_level", "pixel_format", "bit_depth", "width",
                "height", "color_transfer", "color_primaries", "color_space", "hdr_detected",
                "dolby_vision_detected", "audio_codec", "audio_profile", "audio_channels",
                "audio_channel_layout", "audio_sample_rate", "subtitle_count",
            ),
        ),
        ("media_genre_groups", "catalog", "global", "0", _changed_columns("display_title", "year", "genres_json")),
        (
            "library_sources", "catalog", "global", "0",
            _changed_columns(
                "provider", "google_drive_account_id", "resource_type", "resource_id", "display_name", "local_path",
            ),
        ),
        ("library_sources", "permission", "global", "0", _changed_columns("owner_user_id", "is_shared")),
        (
            "media_age_requirements", "permission", "global", "0",
            _changed_columns("display_title", "year", "age_requirement"),
        ),
        (
            "media_age_manual_group_links", "permission", "global", "0",
            _changed_columns("age_group_key", "media_item_id"),
        ),
        ("global_hidden_media_items", "permission", "global", "0", _changed_columns("media_item_id")),
        (
            "global_hidden_movie_keys", "permission", "global", "0",
            _changed_columns("movie_key", "display_title", "year", "edition_identity"),
        ),
        ("users", "permission", "user", "{alias}.id", _changed_columns("id", "role", "enabled", "age_credential")),
        (
            "user_settings",
            "presentation",
            "user",
            "{alias}.user_id",
            "(OLD.key IN ('hide_duplicate_movies', 'hide_recently_added', 'poster_card_appearance', 'poster_card_display_max_width')"
            " OR NEW.key IN ('hide_duplicate_movies', 'hide_recently_added', 'poster_card_appearance', 'poster_card_display_max_width'))"
            " AND (OLD.value IS NOT NEW.value OR OLD.key IS NOT NEW.key OR OLD.user_id IS NOT NEW.user_id)",
        ),
        (
            "user_hidden_library_sources", "user_overlay", "user", "{alias}.user_id",
            _changed_columns("user_id", "library_source_id"),
        ),
        (
            "user_hidden_media_items", "user_overlay", "user", "{alias}.user_id",
            _changed_columns("user_id", "media_item_id"),
        ),
        (
            "user_hidden_movie_keys", "user_overlay", "user", "{alias}.user_id",
            _changed_columns("user_id", "movie_key", "display_title", "year", "edition_identity"),
        ),
        (
            "playback_progress", "progress", "user", "{alias}.user_id",
            _changed_columns("user_id", "media_item_id", "position_seconds", "duration_seconds", "completed"),
        ),
    ]
    statements: list[str] = []
    for table, layer, scope_kind, scope_template, update_condition in trigger_specs:
        for operation, alias in (("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD")):
            scope_id = scope_template.format(alias=alias)
            condition = update_condition.format(alias=alias) if operation == "UPDATE" and update_condition else None
            if table == "user_settings" and operation != "UPDATE":
                condition = (
                    f"{alias}.key IN ('hide_duplicate_movies', 'hide_recently_added', "
                    "'poster_card_appearance', 'poster_card_display_max_width')"
                )
            claim = f"elvern_revision_should_bump('{scope_kind}', {scope_id}, '{layer}') = 1"
            when_clause = f"WHEN ({condition}) AND {claim}" if condition else f"WHEN {claim}"
            trigger_name = (
                f"{LIBRARY_REVISION_TRIGGER_PREFIX}{LIBRARY_REVISION_TRIGGER_VERSION}_"
                f"{table}_{layer}_{operation.lower()}"
            )
            statements.append(
                f"""
                CREATE TRIGGER {trigger_name}
                AFTER {operation} ON {table}
                {when_clause}
                BEGIN
                    INSERT INTO library_revision_counters (
                        scope_kind, scope_id, layer, counter, updated_at
                    ) VALUES ('{scope_kind}', {scope_id}, '{layer}', 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(scope_kind, scope_id, layer) DO UPDATE SET
                        counter = counter + 1,
                        updated_at = CURRENT_TIMESTAMP;
                END
                """
            )
        old_scope_id = scope_template.format(alias="OLD")
        new_scope_id = scope_template.format(alias="NEW")
        if scope_kind == "user" and old_scope_id != new_scope_id:
            scope_change_condition = f"{old_scope_id} IS NOT {new_scope_id}"
            if table == "user_settings":
                scope_change_condition += (
                    " AND (OLD.key IN ('hide_duplicate_movies', 'hide_recently_added', "
                    "'poster_card_appearance', 'poster_card_display_max_width')"
                    " OR NEW.key IN ('hide_duplicate_movies', 'hide_recently_added', "
                    "'poster_card_appearance', 'poster_card_display_max_width'))"
                )
            trigger_name = (
                f"{LIBRARY_REVISION_TRIGGER_PREFIX}{LIBRARY_REVISION_TRIGGER_VERSION}_"
                f"{table}_{layer}_update_old_scope"
            )
            statements.append(
                f"""
                CREATE TRIGGER {trigger_name}
                AFTER UPDATE ON {table}
                WHEN ({scope_change_condition})
                  AND elvern_revision_should_bump('{scope_kind}', {old_scope_id}, '{layer}') = 1
                BEGIN
                    INSERT INTO library_revision_counters (
                        scope_kind, scope_id, layer, counter, updated_at
                    ) VALUES ('{scope_kind}', {old_scope_id}, '{layer}', 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(scope_kind, scope_id, layer) DO UPDATE SET
                        counter = counter + 1,
                        updated_at = CURRENT_TIMESTAMP;
                END
                """
            )
    return tuple(statements)


LIBRARY_REVISION_TRIGGER_STATEMENTS = _library_revision_trigger_statements()
LIBRARY_REVISION_TRIGGER_NAMES = tuple(
    statement.split("CREATE TRIGGER ", 1)[1].split(None, 1)[0]
    for statement in LIBRARY_REVISION_TRIGGER_STATEMENTS
)


INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions (expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_last_seen_at ON sessions (last_seen_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_revoked_at ON sessions (revoked_at)",
    "CREATE INDEX IF NOT EXISTS idx_media_items_title ON media_items (title)",
    "CREATE INDEX IF NOT EXISTS idx_media_items_filename ON media_items (original_filename)",
    "CREATE INDEX IF NOT EXISTS idx_media_items_scanned ON media_items (last_scanned_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_media_items_source_kind ON media_items (source_kind)",
    "CREATE INDEX IF NOT EXISTS idx_media_items_library_source_id ON media_items (library_source_id)",
    "CREATE INDEX IF NOT EXISTS idx_media_items_external_media_id ON media_items (external_media_id)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_media_items_active_hidden_copy_identity
    ON media_items (hidden_copy_identity)
    WHERE hidden_copy_identity LIKE 'copy-v2:%'
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_hidden_copy_alias_lookup
    ON hidden_copy_identity_aliases (
        source_kind,
        library_source_id,
        locator_hash,
        evidence_hash
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_hidden_copy_alias_evidence
    ON hidden_copy_identity_aliases (
        source_kind,
        library_source_id,
        evidence_hash
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_media_item_technical_metadata_probe_status ON media_item_technical_metadata (probe_status)",
    "CREATE INDEX IF NOT EXISTS idx_media_item_technical_metadata_probed_at ON media_item_technical_metadata (probed_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_media_item_technical_metadata_source_fingerprint ON media_item_technical_metadata (source_fingerprint)",
    "CREATE INDEX IF NOT EXISTS idx_media_item_technical_metadata_source ON media_item_technical_metadata (metadata_source)",
    "CREATE INDEX IF NOT EXISTS idx_progress_updated ON playback_progress (updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_playback_watch_events_user_recorded ON playback_watch_events (user_id, recorded_at_epoch DESC)",
    "CREATE INDEX IF NOT EXISTS idx_playback_watch_events_user_media ON playback_watch_events (user_id, media_item_id)",
    "CREATE INDEX IF NOT EXISTS idx_playback_tracking_events_user_recorded ON playback_tracking_events (user_id, recorded_at_epoch DESC)",
    "CREATE INDEX IF NOT EXISTS idx_playback_tracking_events_user_media ON playback_tracking_events (user_id, media_item_id)",
    "CREATE INDEX IF NOT EXISTS idx_playback_tracking_events_native_session ON playback_tracking_events (native_session_id)",
    "CREATE INDEX IF NOT EXISTS idx_user_settings_user_id ON user_settings (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_invite_codes_expires_at ON invite_codes (expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_invite_codes_created_by ON invite_codes (created_by_user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_media_age_requirements_updated_at ON media_age_requirements (updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_media_age_manual_group_links_group ON media_age_manual_group_links (age_group_key)",
    "CREATE INDEX IF NOT EXISTS idx_media_age_manual_group_links_created_by ON media_age_manual_group_links (created_by_user_id)",
    "CREATE INDEX IF NOT EXISTS idx_media_genre_groups_updated_at ON media_genre_groups (updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_password_help_requests_status ON password_help_requests (status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_password_help_requests_user ON password_help_requests (user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_password_help_requests_bucket ON password_help_requests (requester_bucket_hash, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_download_access_items_user_id ON download_access_items (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_download_access_items_media_item_id ON download_access_items (media_item_id)",
    "CREATE INDEX IF NOT EXISTS idx_download_sessions_user_media ON download_sessions (user_id, media_item_id)",
    "CREATE INDEX IF NOT EXISTS idx_download_sessions_expires_at ON download_sessions (expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_google_drive_accounts_user_id ON google_drive_accounts (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_google_oauth_states_expires_at ON google_oauth_states (expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_library_sources_owner_user_id ON library_sources (owner_user_id)",
    "CREATE INDEX IF NOT EXISTS idx_library_sources_provider_resource ON library_sources (provider, resource_type, resource_id)",
    "CREATE INDEX IF NOT EXISTS idx_library_sources_shared ON library_sources (is_shared, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_user_hidden_library_sources_user_id ON user_hidden_library_sources (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_user_hidden_library_sources_source_id ON user_hidden_library_sources (library_source_id)",
    "CREATE INDEX IF NOT EXISTS idx_user_hidden_media_items_user_id ON user_hidden_media_items (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_user_hidden_media_items_media_item_id ON user_hidden_media_items (media_item_id)",
    "CREATE INDEX IF NOT EXISTS idx_user_hidden_movie_keys_user_id ON user_hidden_movie_keys (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_user_hidden_movie_keys_movie_key ON user_hidden_movie_keys (movie_key)",
    "CREATE INDEX IF NOT EXISTS idx_global_hidden_media_items_hidden_by_user_id ON global_hidden_media_items (hidden_by_user_id)",
    "CREATE INDEX IF NOT EXISTS idx_global_hidden_media_items_hidden_at ON global_hidden_media_items (hidden_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_global_hidden_movie_keys_hidden_by_user_id ON global_hidden_movie_keys (hidden_by_user_id)",
    "CREATE INDEX IF NOT EXISTS idx_global_hidden_movie_keys_hidden_at ON global_hidden_movie_keys (hidden_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_native_playback_expires_at ON native_playback_sessions (expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_native_playback_user_item ON native_playback_sessions (user_id, media_item_id)",
    "CREATE INDEX IF NOT EXISTS idx_native_playback_auth_session ON native_playback_sessions (auth_session_id)",
    "CREATE INDEX IF NOT EXISTS idx_native_playback_created_from_auth_session ON native_playback_sessions (created_from_auth_session_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON audit_logs (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_login_failures_bucket_time ON login_failures (bucket_kind, bucket_key, occurred_at_unix DESC)",
    "CREATE INDEX IF NOT EXISTS idx_login_failures_cleanup ON login_failures (occurred_at_unix)",
    "CREATE INDEX IF NOT EXISTS idx_security_events_time ON security_events (occurred_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_security_events_user ON security_events (actor_user_id, occurred_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_security_events_kind ON security_events (event_kind, occurred_at DESC)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_recovery_codes_hash ON user_recovery_codes (code_hash)",
    "CREATE INDEX IF NOT EXISTS idx_recovery_codes_user ON user_recovery_codes (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_login_challenges_expiry ON login_challenges (expires_at_unix)",
    "CREATE INDEX IF NOT EXISTS idx_client_devices_last_user_id ON client_devices (last_user_id)",
    "CREATE INDEX IF NOT EXISTS idx_client_devices_helper_last_seen_at ON client_devices (helper_last_seen_at DESC)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_helper_releases_channel_runtime_version ON helper_releases (channel, runtime_id, version)",
    "CREATE INDEX IF NOT EXISTS idx_helper_releases_platform_channel ON helper_releases (platform, channel, published_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_desktop_vlc_handoffs_expires_at ON desktop_vlc_handoffs (expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_desktop_vlc_handoffs_auth_session ON desktop_vlc_handoffs (auth_session_id)",
    "CREATE INDEX IF NOT EXISTS idx_desktop_vlc_handoffs_device_id ON desktop_vlc_handoffs (device_id)",
    "CREATE INDEX IF NOT EXISTS idx_desktop_helper_verifications_expires_at ON desktop_helper_verifications (expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_desktop_helper_verifications_device_id ON desktop_helper_verifications (device_id)",
    "CREATE INDEX IF NOT EXISTS idx_assistant_user_access_enabled ON assistant_user_access (assistant_beta_enabled, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_assistant_requests_submitter ON assistant_requests (submitted_by_user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_assistant_requests_status ON assistant_requests (status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_assistant_requests_archived ON assistant_requests (is_archived, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_assistant_request_attachments_request_id ON assistant_request_attachments (request_id)",
    "CREATE INDEX IF NOT EXISTS idx_assistant_attachment_external_open_tickets_expires_at ON assistant_attachment_external_open_tickets (expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_assistant_attachment_external_open_tickets_attachment_id ON assistant_attachment_external_open_tickets (attachment_id)",
    "CREATE INDEX IF NOT EXISTS idx_assistant_triage_drafts_request_id ON assistant_triage_drafts (request_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_assistant_action_requests_request_id ON assistant_action_requests (request_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_assistant_action_requests_status ON assistant_action_requests (status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_assistant_approval_records_action_id ON assistant_approval_records (action_request_id, decided_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_assistant_change_records_request_id ON assistant_change_records (request_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_assistant_change_records_action_id ON assistant_change_records (linked_action_request_id, created_at DESC)",
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ElvernConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._library_revision_claims: set[tuple[str, int, str]] = set()

    def claim_library_revision(self, scope_kind: object, scope_id: object, layer: object) -> int:
        try:
            claim = (str(scope_kind), int(scope_id), str(layer))
        except (TypeError, ValueError):
            return 0
        if claim in self._library_revision_claims:
            return 0
        self._library_revision_claims.add(claim)
        return 1

    def commit(self) -> None:
        super().commit()
        self._library_revision_claims.clear()

    def rollback(self) -> None:
        super().rollback()
        self._library_revision_claims.clear()


def connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        db_path,
        check_same_thread=False,
        factory=ElvernConnection,
    )
    connection.row_factory = sqlite3.Row
    connection.create_function(
        "elvern_revision_should_bump",
        3,
        connection.claim_library_revision,
    )
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


@contextmanager
def get_connection(settings: Settings) -> Iterator[sqlite3.Connection]:
    connection = connect(settings.db_path)
    try:
        yield connection
    finally:
        connection.close()


def init_db(settings: Settings) -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(settings) as connection:
        for statement in TABLE_STATEMENTS:
            connection.execute(statement)
        _drop_library_revision_triggers(connection)
        _run_schema_migrations(connection, settings=settings)
        if settings.library_revision_enabled:
            for statement in LIBRARY_REVISION_TRIGGER_STATEMENTS:
                connection.execute(statement)
        for statement in INDEX_STATEMENTS:
            connection.execute(statement)
        connection.commit()


def _drop_library_revision_triggers(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE ?",
        (f"{LIBRARY_REVISION_TRIGGER_PREFIX}%",),
    ).fetchall()
    for row in rows:
        name = str(row["name"]).replace('"', '""')
        connection.execute(f'DROP TRIGGER IF EXISTS "{name}"')


def _run_schema_migrations(connection: sqlite3.Connection, *, settings: Settings) -> None:
    _ensure_column(connection, "playback_progress", "watch_seconds_total", "REAL NOT NULL DEFAULT 0")
    _ensure_column(connection, "media_items", "source_kind", "TEXT NOT NULL DEFAULT 'local'")
    _ensure_column(connection, "media_items", "library_source_id", "INTEGER")
    _ensure_column(connection, "media_items", "external_media_id", "TEXT")
    _ensure_column(connection, "media_items", "cloud_mime_type", "TEXT")
    _ensure_column(connection, "media_items", "cloud_resource_key", "TEXT")
    _ensure_column(connection, "media_items", "hidden_copy_identity", "TEXT")
    _ensure_column(connection, "media_items", "series_folder_key", "TEXT")
    _ensure_column(connection, "media_items", "series_folder_name", "TEXT")
    _ensure_column(connection, "media_items", "library_category", "TEXT")
    _ensure_column(connection, "media_items", "library_category_path", "TEXT")
    _ensure_column(connection, "media_items", "library_category_name", "TEXT")
    _ensure_column(connection, "media_items", "library_folder_role", "TEXT")
    _ensure_column(connection, "media_items", "library_folder_path", "TEXT")
    _ensure_column(connection, "media_items", "library_folder_name", "TEXT")

    _ensure_column(connection, "users", "role", "TEXT NOT NULL DEFAULT 'standard_user'")
    _ensure_column(connection, "users", "enabled", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(connection, "users", "last_login_at", "TEXT")
    _ensure_column(connection, "users", "age_credential", "INTEGER NOT NULL DEFAULT 18")
    _ensure_column(connection, "users", "totp_secret", "TEXT")
    _ensure_column(connection, "users", "totp_enabled_at", "TEXT")
    _ensure_column(connection, "users", "totp_last_used_window", "INTEGER")
    _ensure_column(connection, "users", "totp_setup_skipped_at", "TEXT")
    _ensure_column(connection, "users", "totp_setup_prompt_enabled", "INTEGER NOT NULL DEFAULT 0")
    _run_totp_prompt_default_migration(connection)
    _mark_totp_migration(connection)
    _harden_totp_at_rest_values(connection, settings=settings)

    _ensure_column(connection, "sessions", "revoked_at", "TEXT")
    _ensure_column(connection, "sessions", "revoked_reason", "TEXT")
    _ensure_column(connection, "sessions", "last_seen_at", "TEXT")
    _ensure_column(connection, "sessions", "last_activity_at", "TEXT")
    _ensure_column(connection, "sessions", "cleanup_confirmed_at", "TEXT")
    _run_session_idle_timeout_migration(connection)
    _run_browser_session_hmac_migration(connection)

    _ensure_column(connection, "native_playback_sessions", "revoked_at", "TEXT")
    _ensure_column(connection, "native_playback_sessions", "auth_session_id", "INTEGER")
    _ensure_column(connection, "native_playback_sessions", "created_from_auth_session_id", "INTEGER")
    _ensure_column(connection, "native_playback_sessions", "source_ip", "TEXT")
    _ensure_column(connection, "native_playback_sessions", "last_progress_recorded_at", "TEXT")

    _ensure_column(connection, "desktop_vlc_handoffs", "device_id", "TEXT")
    _ensure_column(connection, "desktop_vlc_handoffs", "helper_version", "TEXT")
    _ensure_column(connection, "desktop_vlc_handoffs", "helper_platform", "TEXT")
    _ensure_column(connection, "desktop_vlc_handoffs", "helper_arch", "TEXT")
    _ensure_column(connection, "desktop_vlc_handoffs", "helper_vlc_detection_state", "TEXT")
    _ensure_column(connection, "desktop_vlc_handoffs", "helper_vlc_detection_path", "TEXT")
    _ensure_column(connection, "desktop_vlc_handoffs", "helper_vlc_detection_checked_at", "TEXT")
    _ensure_column(connection, "desktop_vlc_handoffs", "resolved_at", "TEXT")
    _ensure_column(connection, "download_sessions", "auth_session_required", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(connection, "client_devices", "helper_vlc_detection_state", "TEXT")
    _ensure_column(connection, "client_devices", "helper_vlc_detection_path", "TEXT")
    _ensure_column(connection, "client_devices", "helper_vlc_detection_checked_at", "TEXT")
    _ensure_column(connection, "library_sources", "local_path", "TEXT")
    _ensure_column(connection, "assistant_change_records", "request_id", "INTEGER")
    _ensure_column(connection, "password_help_requests", "requester_ip_address", "TEXT")
    _ensure_column(connection, "password_help_requests", "requester_user_agent", "TEXT")
    _ensure_column(connection, "invite_codes", "assigned_age", "INTEGER NOT NULL DEFAULT 18")
    _run_account_short_token_hmac_migration(connection)
    _run_floating_position_retirement_migration(connection)

    _backfill_playback_watch_history(connection)
    _backfill_session_activity_columns(connection)
    _backfill_hidden_movie_keys(connection)


def _run_floating_position_retirement_migration(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """
        SELECT name
        FROM schema_migrations
        WHERE name = ?
        LIMIT 1
        """,
        (FLOATING_POSITION_RETIREMENT_MIGRATION,),
    ).fetchone()
    if row is not None:
        return
    cursor = connection.execute(
        "DELETE FROM user_settings WHERE key = 'floating_controls_position'"
    )
    connection.execute(
        """
        INSERT INTO schema_migrations (name, applied_at)
        VALUES (?, ?)
        """,
        (FLOATING_POSITION_RETIREMENT_MIGRATION, utcnow_iso()),
    )
    if cursor.rowcount:
        logger.info("Removed %s retired floating position setting(s)", cursor.rowcount)


def _harden_totp_at_rest_values(connection: sqlite3.Connection, *, settings: Settings) -> None:
    encrypted_user_secrets = 0
    rows = connection.execute(
        """
        SELECT id, totp_secret
        FROM users
        WHERE totp_secret IS NOT NULL
          AND totp_secret != ''
          AND totp_secret NOT LIKE ?
        """,
        (CIPHERTEXT_PREFIX + "%",),
    ).fetchall()
    for row in rows:
        connection.execute(
            "UPDATE users SET totp_secret = ? WHERE id = ?",
            (encrypt_at_rest(str(row["totp_secret"]), settings), int(row["id"])),
        )
        encrypted_user_secrets += 1

    now_epoch = int(datetime.now(timezone.utc).timestamp())
    cutoff_epoch = now_epoch - TOTP_PENDING_SECRET_TTL_SECONDS
    encrypted_pending_secrets = 0
    deleted_pending_secrets = 0
    pending_rows = connection.execute(
        """
        SELECT user_id, secret, created_at
        FROM totp_pending_secrets
        """
    ).fetchall()
    for row in pending_rows:
        created_epoch = _epoch_seconds_from_iso(row["created_at"])
        if created_epoch is None or created_epoch < cutoff_epoch:
            connection.execute(
                "DELETE FROM totp_pending_secrets WHERE user_id = ?",
                (int(row["user_id"]),),
            )
            deleted_pending_secrets += 1
            continue
        secret = str(row["secret"] or "")
        if secret and not secret.startswith(CIPHERTEXT_PREFIX):
            connection.execute(
                "UPDATE totp_pending_secrets SET secret = ? WHERE user_id = ?",
                (encrypt_at_rest(secret, settings), int(row["user_id"])),
            )
            encrypted_pending_secrets += 1

    if encrypted_user_secrets:
        logger.info("Encrypted %s legacy TOTP secret(s)", encrypted_user_secrets)
    if encrypted_pending_secrets:
        logger.info("Encrypted %s legacy pending TOTP secret(s)", encrypted_pending_secrets)
    if deleted_pending_secrets:
        logger.info("Deleted %s expired pending TOTP secret(s)", deleted_pending_secrets)


def _run_session_idle_timeout_migration(connection: sqlite3.Connection) -> None:
    migration_name = "session_idle_timeout_v1"
    row = connection.execute(
        """
        SELECT name
        FROM schema_migrations
        WHERE name = ?
        LIMIT 1
        """,
        (migration_name,),
    ).fetchone()
    if row is not None:
        return
    connection.execute("DELETE FROM sessions")
    connection.execute(
        """
        INSERT INTO schema_migrations (name, applied_at)
        VALUES (?, ?)
        """,
        (migration_name, utcnow_iso()),
    )
    logger.info("Cleared all sessions for idle-timeout migration. All users must log in again.")


def _run_browser_session_hmac_migration(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """
        SELECT name
        FROM schema_migrations
        WHERE name = ?
        LIMIT 1
        """,
        (BROWSER_SESSION_HMAC_MIGRATION_NAME,),
    ).fetchone()
    if row is not None:
        return

    now = utcnow_iso()
    cursor = connection.execute(
        """
        UPDATE sessions
        SET revoked_at = ?,
            revoked_reason = ?,
            cleanup_confirmed_at = NULL
        WHERE revoked_at IS NULL
          AND session_token_hash NOT LIKE ?
        """,
        (now, TOKEN_HASH_MIGRATION_REVOKE_REASON, TOKEN_HASH_PREFIX + "%"),
    )
    connection.execute(
        """
        INSERT INTO schema_migrations (name, applied_at)
        VALUES (?, ?)
        """,
        (BROWSER_SESSION_HMAC_MIGRATION_NAME, now),
    )
    if cursor.rowcount:
        logger.info("Revoked %s legacy browser sessions for HMAC token hash migration.", cursor.rowcount)


def _run_account_short_token_hmac_migration(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """
        SELECT name
        FROM schema_migrations
        WHERE name = ?
        LIMIT 1
        """,
        (ACCOUNT_SHORT_TOKEN_HMAC_MIGRATION_NAME,),
    ).fetchone()
    if row is not None:
        return

    challenge_cursor = connection.execute("DELETE FROM login_challenges")
    oauth_cursor = connection.execute("DELETE FROM google_oauth_states")
    connection.execute(
        """
        INSERT INTO schema_migrations (name, applied_at)
        VALUES (?, ?)
        """,
        (ACCOUNT_SHORT_TOKEN_HMAC_MIGRATION_NAME, utcnow_iso()),
    )
    if challenge_cursor.rowcount or oauth_cursor.rowcount:
        logger.info(
            "Cleared %s login challenges and %s Google OAuth states for HMAC token hash migration.",
            challenge_cursor.rowcount,
            oauth_cursor.rowcount,
        )


def _mark_totp_migration(connection: sqlite3.Connection) -> None:
    migration_name = "totp_2fa_v1"
    row = connection.execute(
        """
        SELECT name
        FROM schema_migrations
        WHERE name = ?
        LIMIT 1
        """,
        (migration_name,),
    ).fetchone()
    if row is not None:
        return
    connection.execute(
        """
        INSERT INTO schema_migrations (name, applied_at)
        VALUES (?, ?)
        """,
        (migration_name, utcnow_iso()),
    )


def _run_totp_prompt_default_migration(connection: sqlite3.Connection) -> None:
    migration_name = "totp_prompt_defaults_v1"
    row = connection.execute(
        """
        SELECT name
        FROM schema_migrations
        WHERE name = ?
        LIMIT 1
        """,
        (migration_name,),
    ).fetchone()
    if row is not None:
        return
    connection.execute(
        """
        UPDATE users
        SET totp_setup_prompt_enabled = 1
        WHERE role = 'admin'
          AND totp_secret IS NULL
        """
    )
    connection.execute(
        """
        INSERT INTO schema_migrations (name, applied_at)
        VALUES (?, ?)
        """,
        (migration_name, utcnow_iso()),
    )


def _ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name in columns:
        return
    connection.execute(
        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
    )


def _epoch_seconds_from_iso(value: object) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return int(parsed.timestamp())


def _backfill_playback_watch_history(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT
            user_id,
            media_item_id,
            position_seconds,
            watch_seconds_total,
            updated_at
        FROM playback_progress
        """
    ).fetchall()
    if not rows:
        return

    fallback_epoch = int(datetime.now(timezone.utc).timestamp())
    for row in rows:
        user_id = int(row["user_id"])
        media_item_id = int(row["media_item_id"])
        position_seconds = round(max(float(row["position_seconds"] or 0), 0.0), 2)
        watch_seconds_total = round(max(float(row["watch_seconds_total"] or 0), 0.0), 2)
        if watch_seconds_total <= 0 and position_seconds > 0:
            watch_seconds_total = position_seconds
            connection.execute(
                """
                UPDATE playback_progress
                SET watch_seconds_total = ?
                WHERE user_id = ? AND media_item_id = ? AND COALESCE(watch_seconds_total, 0) <= 0
                """,
                (watch_seconds_total, user_id, media_item_id),
            )

        if watch_seconds_total <= 0:
            continue

        existing_event = connection.execute(
            """
            SELECT 1
            FROM playback_watch_events
            WHERE user_id = ? AND media_item_id = ?
            LIMIT 1
            """,
            (user_id, media_item_id),
        ).fetchone()
        if existing_event is not None:
            continue

        recorded_at_epoch = _epoch_seconds_from_iso(row["updated_at"]) or fallback_epoch
        connection.execute(
            """
            INSERT INTO playback_watch_events (
                user_id,
                media_item_id,
                watched_seconds,
                recorded_at_epoch
            ) VALUES (?, ?, ?, ?)
            """,
            (user_id, media_item_id, watch_seconds_total, recorded_at_epoch),
        )


def _backfill_session_activity_columns(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        UPDATE sessions
        SET last_activity_at = COALESCE(last_activity_at, last_seen_at, created_at)
        WHERE last_activity_at IS NULL
        """
    )
    connection.execute(
        """
        UPDATE sessions
        SET cleanup_confirmed_at = COALESCE(cleanup_confirmed_at, revoked_at)
        WHERE cleanup_confirmed_at IS NULL
          AND revoked_reason = 'logout'
          AND revoked_at IS NOT NULL
        """
    )
