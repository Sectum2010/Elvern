from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import inspect
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException, Response

import backend.app.auth as auth_module
import backend.app.db as db_module
from backend.app.services import account_access_service
from backend.app.auth import (
    authenticate_user,
    create_session,
    destroy_session,
    get_session_access_failure_reason,
    get_user_by_session_token,
)
from backend.app.db import (
    ACCOUNT_SHORT_TOKEN_HMAC_MIGRATION_NAME,
    BROWSER_SESSION_HMAC_MIGRATION_NAME,
    TOKEN_HASH_MIGRATION_REVOKE_REASON,
    get_connection,
    init_db,
    utcnow_iso,
)
from backend.app.schemas import NativePlaybackSessionResponse
from backend.app.security import TOKEN_HASH_PREFIX, hash_session_token, hash_token_hmac
from backend.app.services.account_access_service import (
    _legacy_requester_bucket_hash,
    _requester_bucket_hash,
    _secret_hash,
    create_password_help_request,
    create_user_with_invite,
    generate_invite_code,
    revoke_invite_code,
    create_download_session,
    get_download_access_for_user,
    is_item_download_allowed,
    is_download_session_still_authorized,
    mark_download_session_completed,
    mark_download_session_failed,
    mark_download_session_terminated,
    update_download_access_for_user,
    validate_download_session,
)
from backend.app.services.admin_service import create_user, delete_user, update_user
from backend.app.services.log_redaction import redact_download_session_urls
from backend.app.services.media_age_access_service import set_media_age_requirement
from backend.app.services.desktop_helper_service import (
    create_desktop_helper_verification,
    resolve_desktop_helper_verification,
)
from backend.app.services.desktop_playback_handoff_service import (
    create_desktop_vlc_handoff as create_desktop_vlc_handoff_record,
    resolve_desktop_vlc_handoff as resolve_desktop_vlc_handoff_record,
)
from backend.app.services.native_playback_service import (
    _build_native_playback_stream_policy,
    create_native_playback_session,
    build_native_stream_response,
    close_native_playback_session,
    get_admin_native_playback_status,
    get_native_playback_session_payload,
    inspect_native_playback_access,
    should_decouple_external_player_auth_session,
)
from backend.app.services.local_library_source_service import ensure_current_shared_local_source_binding
from backend.app.url_prefix_service import rotate_url_prefix


def _admin_user(settings):
    user, failure_reason = authenticate_user(
        settings,
        settings.admin_username,
        settings.admin_bootstrap_password or "",
    )
    assert failure_reason is None
    assert user is not None
    return user


def _create_standard_user(settings, *, username: str, password: str = "family-password") -> dict[str, object]:
    return create_user(
        settings,
        username=username,
        password=password,
        role="standard_user",
        enabled=True,
        actor=_admin_user(settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )


def _issue_user_session(settings, *, username: str, password: str):
    user, failure_reason = authenticate_user(settings, username, password)
    assert failure_reason is None
    assert user is not None
    token = create_session(
        settings,
        user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    session_user = get_user_by_session_token(settings, token)
    assert session_user is not None
    assert session_user.session_id is not None
    return session_user, token


def _auth_session_hash(settings, token: str) -> str:
    return hash_token_hmac(settings, purpose=auth_module.AUTH_SESSION_TOKEN_HASH_PURPOSE, token=token)


def _query_param(url: object, name: str) -> str:
    values = parse_qs(urlsplit(str(url)).query).get(name)
    assert values
    return values[0]


def _create_media_item(settings, *, relative_name: str = "movie.mp4") -> dict[str, object]:
    media_file = Path(settings.media_root) / relative_name
    media_file.write_bytes(b"not a real media file")
    now = utcnow_iso()
    with get_connection(settings) as connection:
        cursor = connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                file_size,
                file_mtime,
                duration_seconds,
                width,
                height,
                video_codec,
                audio_codec,
                container,
                year,
                created_at,
                updated_at,
                last_scanned_at
            ) VALUES (?, ?, ?, 'local', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Test Movie",
                media_file.name,
                str(media_file),
                media_file.stat().st_size,
                media_file.stat().st_mtime,
                120.0,
                None,
                None,
                "h264",
                "aac",
                "mp4",
                2024,
                now,
                now,
                now,
            ),
        )
        connection.commit()
        item_id = int(cursor.lastrowid)
    return {
        "id": item_id,
        "title": "Test Movie",
        "original_filename": media_file.name,
        "file_path": str(media_file),
        "source_kind": "local",
        "duration_seconds": 120.0,
        "container": "mp4",
        "video_codec": "h264",
        "audio_codec": "aac",
        "resume_position_seconds": 0,
        "subtitles": [],
    }


def _grant_download_for_item(settings, *, user_id: int, media_item_id: int) -> None:
    with get_connection(settings) as connection:
        shared_local_source_id = ensure_current_shared_local_source_binding(
            settings,
            connection=connection,
        )
        connection.execute(
            "UPDATE media_items SET library_source_id = ? WHERE id = ?",
            (shared_local_source_id, media_item_id),
        )
        connection.commit()
    update_download_access_for_user(
        settings,
        user_id=user_id,
        access_mode="selected",
        media_item_ids=[media_item_id],
        actor=_admin_user(settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )


def _create_authorized_download_session(settings, *, username: str):
    created = _create_standard_user(settings, username=username)
    media_item = _create_media_item(settings, relative_name=f"{username}.mp4")
    _grant_download_for_item(
        settings,
        user_id=int(created["id"]),
        media_item_id=int(media_item["id"]),
    )
    session_user, auth_token = _issue_user_session(
        settings,
        username=username,
        password="family-password",
    )
    session_payload = create_download_session(
        settings,
        user=session_user,
        item_id=int(media_item["id"]),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    return created, media_item, session_user, auth_token, session_payload


def _insert_legacy_download_session(
    settings,
    *,
    token: str,
    user,
    media_item_id: int,
    expires_at: str,
    auth_session_required: int = 1,
    completed_at: str | None = None,
    failed_at: str | None = None,
    revoked_at: str | None = None,
) -> tuple[int, str]:
    now = utcnow_iso()
    legacy_hash = _secret_hash(settings, "download-session", token)
    auth_session_id = user.session_id if auth_session_required else None
    with get_connection(settings) as connection:
        cursor = connection.execute(
            """
            INSERT INTO download_sessions (
                session_token_hash,
                user_id,
                media_item_id,
                auth_session_id,
                auth_session_required,
                created_at,
                expires_at,
                completed_at,
                failed_at,
                revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                legacy_hash,
                user.id,
                media_item_id,
                auth_session_id,
                auth_session_required,
                now,
                expires_at,
                completed_at,
                failed_at,
                revoked_at,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid), legacy_hash


def _set_media_admin_display_fields(
    settings,
    *,
    item_id: int,
    source_kind: str = "local",
    width: int | None = None,
    height: int | None = None,
) -> None:
    with get_connection(settings) as connection:
        connection.execute(
            """
            UPDATE media_items
            SET source_kind = ?, width = ?, height = ?
            WHERE id = ?
            """,
            (source_kind, width, height, item_id),
        )
        connection.commit()


def _mark_native_stream_activity(settings, *, session_id: str, at: datetime | None = None) -> None:
    activity_at = (at or datetime.now(timezone.utc)).isoformat()
    with get_connection(settings) as connection:
        connection.execute(
            """
            UPDATE native_playback_sessions
            SET last_progress_recorded_at = ?
            WHERE session_id = ?
            """,
            (activity_at, session_id),
        )
        connection.commit()


def _stub_mobile_auth_session_invalidation(client) -> None:
    client.app.state.mobile_playback_manager.invalidate_auth_session = (
        lambda auth_session_id, *, reason: 0
    )


def _login_headers(*, ip_address: str = "203.0.113.10", user_agent: str = "Pytest Browser 1.0") -> dict[str, str]:
    return {
        "x-forwarded-for": ip_address,
        "user-agent": user_agent,
    }


def test_native_playback_schema_includes_auth_session_provenance(initialized_settings) -> None:
    init_db(initialized_settings)
    with get_connection(initialized_settings) as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(native_playback_sessions)").fetchall()
        }
        indexes = {
            row["name"]
            for row in connection.execute("PRAGMA index_list(native_playback_sessions)").fetchall()
        }

    assert "created_from_auth_session_id" in columns
    assert "idx_native_playback_created_from_auth_session" in indexes


def test_native_playback_session_provenance_static_guards() -> None:
    assert "created_from_auth_session_id" not in inspect.getsource(auth_module.destroy_session)
    assert "created_from_auth_session_id" in inspect.getsource(
        auth_module.revoke_native_playback_sessions_created_from_auth_sessions
    )
    assert "created_from_auth_session_id" not in NativePlaybackSessionResponse.model_fields


def test_f10d_token_surfaces_use_scoped_hmac_with_legacy_fallbacks() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    phase_d_surfaces = {
        "backend/app/services/native_playback_service.py": {
            "purpose": 'NATIVE_PLAYBACK_ACCESS_HASH_PURPOSE = "native.playback.access"',
            "hmac_helper": "_native_playback_access_token_hash",
            "legacy_helper": "_legacy_native_playback_access_token_hash",
            "candidates_helper": "_native_playback_access_token_hash_candidates",
            "rehash_helper": "_maybe_rehash_native_playback_access_token",
        },
        "backend/app/services/desktop_playback_handoff_service.py": {
            "purpose": 'DESKTOP_VLC_HANDOFF_ACCESS_HASH_PURPOSE = "desktop.vlc.handoff.access"',
            "hmac_helper": "_desktop_vlc_handoff_access_token_hash",
            "legacy_helper": "_legacy_desktop_vlc_handoff_access_token_hash",
            "candidates_helper": "_desktop_vlc_handoff_access_token_hash_candidates",
            "rehash_helper": "_maybe_rehash_desktop_vlc_handoff_access_token",
        },
        "backend/app/services/desktop_helper_service.py": {
            "purpose": 'DESKTOP_HELPER_VERIFICATION_ACCESS_HASH_PURPOSE = "desktop.helper.verification.access"',
            "hmac_helper": "_desktop_helper_verification_access_token_hash",
            "legacy_helper": "_legacy_desktop_helper_verification_access_token_hash",
            "candidates_helper": "_desktop_helper_verification_access_token_hash_candidates",
        },
    }
    for relative_path, expected in phase_d_surfaces.items():
        source = (repo_root / relative_path).read_text(encoding="utf-8")
        assert expected["purpose"] in source
        assert expected["hmac_helper"] in source
        assert expected["legacy_helper"] in source
        assert expected["candidates_helper"] in source
        assert "hash_session_token" in source
        assert "hash_token_hmac" in source
        assert "verify_hmac_token_hash" not in source
        if "rehash_helper" in expected:
            assert expected["rehash_helper"] in source

    account_access_source = (repo_root / "backend/app/services/account_access_service.py").read_text(
        encoding="utf-8"
    )
    assert "download-session" in account_access_source
    assert "invite-code" in account_access_source
    assert 'DOWNLOAD_SESSION_HASH_PURPOSE = "download.session"' in account_access_source
    assert "_download_session_hash_candidates" in account_access_source
    assert "_maybe_rehash_download_session_token" in account_access_source
    assert '_secret_hash(settings, "download-session", token)' in account_access_source
    assert "hash_token_hmac(settings, purpose=DOWNLOAD_SESSION_HASH_PURPOSE, token=token)" in account_access_source
    assert "verify_hmac_token_hash" not in account_access_source
    download_source = account_access_source.split("def create_download_session", 1)[1]
    assert "_download_session_hash(settings, token)" in download_source
    assert "_download_session_hash_candidates(settings, token)" in download_source

    assistant_source = (repo_root / "backend/app/services/assistant_service.py").read_text(encoding="utf-8")
    assert 'ASSISTANT_EXTERNAL_OPEN_ACCESS_HASH_PURPOSE = "assistant.external_open.access"' in assistant_source
    assert "_assistant_external_open_access_token_hash" in assistant_source
    assert "_legacy_assistant_external_open_access_token_hash" in assistant_source
    assert "hash_token_hmac" in assistant_source
    assert "hash_session_token" in assistant_source

    assert inspect.getsource(account_access_service._secret_hash) == (
        "def _secret_hash(settings: Settings, namespace: str, value: str) -> str:\n"
        "    return hashlib.sha256(\n"
        '        f"{namespace}\\n{settings.session_secret}\\n{value}".encode("utf-8")\n'
        "    ).hexdigest()\n"
    )


def test_f10b_account_short_token_migration_scope_static_guard() -> None:
    source = inspect.getsource(db_module._run_account_short_token_hmac_migration)
    assert "DELETE FROM login_challenges" in source
    assert "DELETE FROM google_oauth_states" in source
    for forbidden_table in (
        "sessions",
        "invite_codes",
        "password_help_requests",
        "download_sessions",
        "native_playback_sessions",
        "desktop_vlc_handoffs",
        "desktop_helper_verifications",
        "assistant_attachment_external_open_tickets",
    ):
        assert forbidden_table not in source


def _recent_auth_login_details(settings, *, limit: int = 20) -> list[dict[str, object] | None]:
    with get_connection(settings) as connection:
        rows = connection.execute(
            """
            SELECT details_json
            FROM audit_logs
            WHERE action = 'auth.login'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    payload: list[dict[str, object] | None] = []
    for row in rows:
        details_json = row["details_json"]
        payload.append(json.loads(details_json) if details_json else None)
    return payload


def test_create_session_stores_only_the_hashed_token(initialized_settings) -> None:
    user = _admin_user(initialized_settings)

    token = create_session(
        initialized_settings,
        user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT session_token_hash, revoked_at
            FROM sessions
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row is not None
    assert row["session_token_hash"] == _auth_session_hash(initialized_settings, token)
    assert row["session_token_hash"].startswith(TOKEN_HASH_PREFIX)
    assert row["session_token_hash"] != token
    assert row["revoked_at"] is None

    resolved_user = get_user_by_session_token(initialized_settings, token)
    assert resolved_user is not None
    assert resolved_user.username == initialized_settings.admin_username
    assert resolved_user.session_id is not None


def test_destroy_session_revokes_access_without_storing_raw_token(initialized_settings) -> None:
    user = _admin_user(initialized_settings)
    token = create_session(
        initialized_settings,
        user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    destroy_session(initialized_settings, token)

    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT session_token_hash, revoked_at, revoked_reason
            FROM sessions
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row is not None
    assert row["session_token_hash"] == _auth_session_hash(initialized_settings, token)
    assert row["session_token_hash"].startswith(TOKEN_HASH_PREFIX)
    assert row["session_token_hash"] != token
    assert row["revoked_at"] is not None
    assert row["revoked_reason"] == "logout"
    assert get_user_by_session_token(initialized_settings, token) is None
    assert get_session_access_failure_reason(initialized_settings, token) == "revoked"


def test_legacy_browser_session_is_revoked_by_hmac_migration(initialized_settings, client) -> None:
    user = _admin_user(initialized_settings)
    legacy_token = "legacy-browser-session-token"
    legacy_hash = hash_session_token(legacy_token, initialized_settings.session_secret)
    now = datetime.now(timezone.utc)
    with get_connection(initialized_settings) as connection:
        connection.execute(
            "DELETE FROM schema_migrations WHERE name = ?",
            (BROWSER_SESSION_HMAC_MIGRATION_NAME,),
        )
        cursor = connection.execute(
            """
            INSERT INTO sessions (
                user_id,
                session_token_hash,
                created_at,
                expires_at,
                last_seen_at,
                last_activity_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user.id,
                legacy_hash,
                now.isoformat(),
                (now + timedelta(hours=1)).isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        legacy_session_id = int(cursor.lastrowid)
        connection.commit()

    init_db(initialized_settings)
    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT session_token_hash, revoked_at, revoked_reason
            FROM sessions
            WHERE id = ?
            """,
            (legacy_session_id,),
        ).fetchone()

    assert row is not None
    assert row["session_token_hash"] == legacy_hash
    assert row["revoked_at"] is not None
    assert row["revoked_reason"] == TOKEN_HASH_MIGRATION_REVOKE_REASON
    assert get_user_by_session_token(initialized_settings, legacy_token) is None

    client.cookies.set(initialized_settings.session_cookie_name, legacy_token)
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    destroy_session(initialized_settings, legacy_token)


def test_hmac_browser_session_survives_repeated_hmac_migration(initialized_settings) -> None:
    user = _admin_user(initialized_settings)
    token = create_session(
        initialized_settings,
        user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    token_hash = _auth_session_hash(initialized_settings, token)
    with get_connection(initialized_settings) as connection:
        session_id = connection.execute(
            "SELECT id FROM sessions WHERE session_token_hash = ?",
            (token_hash,),
        ).fetchone()["id"]
        connection.execute(
            "DELETE FROM schema_migrations WHERE name = ?",
            (BROWSER_SESSION_HMAC_MIGRATION_NAME,),
        )
        connection.commit()

    init_db(initialized_settings)
    init_db(initialized_settings)

    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            "SELECT revoked_at, revoked_reason FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        marker_count = connection.execute(
            "SELECT COUNT(*) AS count FROM schema_migrations WHERE name = ?",
            (BROWSER_SESSION_HMAC_MIGRATION_NAME,),
        ).fetchone()["count"]

    assert row is not None
    assert row["revoked_at"] is None
    assert row["revoked_reason"] is None
    assert marker_count == 1
    assert get_user_by_session_token(initialized_settings, token) is not None


def test_browser_session_hmac_migration_is_idempotent_for_legacy_rows(initialized_settings) -> None:
    user = _admin_user(initialized_settings)
    legacy_token = "legacy-browser-session-token-idempotent"
    legacy_hash = hash_session_token(legacy_token, initialized_settings.session_secret)
    now = datetime.now(timezone.utc)
    with get_connection(initialized_settings) as connection:
        connection.execute(
            "DELETE FROM schema_migrations WHERE name = ?",
            (BROWSER_SESSION_HMAC_MIGRATION_NAME,),
        )
        cursor = connection.execute(
            """
            INSERT INTO sessions (
                user_id,
                session_token_hash,
                created_at,
                expires_at,
                last_seen_at,
                last_activity_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user.id,
                legacy_hash,
                now.isoformat(),
                (now + timedelta(hours=1)).isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        legacy_session_id = int(cursor.lastrowid)
        connection.commit()

    init_db(initialized_settings)
    with get_connection(initialized_settings) as connection:
        first = connection.execute(
            "SELECT revoked_at, revoked_reason FROM sessions WHERE id = ?",
            (legacy_session_id,),
        ).fetchone()

    init_db(initialized_settings)
    with get_connection(initialized_settings) as connection:
        second = connection.execute(
            "SELECT revoked_at, revoked_reason FROM sessions WHERE id = ?",
            (legacy_session_id,),
        ).fetchone()

    assert first is not None
    assert second is not None
    assert second["revoked_at"] == first["revoked_at"]
    assert second["revoked_reason"] == TOKEN_HASH_MIGRATION_REVOKE_REASON


def test_account_short_token_hmac_migration_deletes_only_short_state_tables(initialized_settings) -> None:
    user = _admin_user(initialized_settings)
    session_token = create_session(
        initialized_settings,
        user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    session_hash = _auth_session_hash(initialized_settings, session_token)
    now = utcnow_iso()
    future = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    expires_at_unix = datetime.now(timezone.utc).timestamp() + 300
    with get_connection(initialized_settings) as connection:
        session_id = connection.execute(
            "SELECT id FROM sessions WHERE session_token_hash = ?",
            (session_hash,),
        ).fetchone()["id"]
        connection.execute(
            "DELETE FROM schema_migrations WHERE name = ?",
            (ACCOUNT_SHORT_TOKEN_HMAC_MIGRATION_NAME,),
        )
        connection.execute(
            """
            INSERT INTO login_challenges (
                challenge_token_hash, user_id, created_at, expires_at_unix, ip_address, user_agent
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("legacy-login-challenge", user.id, now, expires_at_unix, "127.0.0.1", "pytest"),
        )
        connection.execute(
            "INSERT INTO google_oauth_states (state_token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            ("legacy-oauth-state", user.id, now, future),
        )
        invite_cursor = connection.execute(
            """
            INSERT INTO invite_codes (code_hash, created_by_user_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            ("legacy-invite-hash-for-migration-test", user.id, now, future),
        )
        help_cursor = connection.execute(
            """
            INSERT INTO password_help_requests (
                username_snapshot,
                user_id,
                requester_bucket_hash,
                status,
                created_at,
                updated_at,
                expires_at
            ) VALUES (?, ?, ?, 'pending', ?, ?, ?)
            """,
            ("migration-help-user", user.id, "legacy-help-bucket-for-migration-test", now, now, future),
        )
        connection.commit()
        invite_id = int(invite_cursor.lastrowid)
        help_id = int(help_cursor.lastrowid)

    init_db(initialized_settings)
    init_db(initialized_settings)

    with get_connection(initialized_settings) as connection:
        assert connection.execute("SELECT COUNT(*) FROM login_challenges").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM google_oauth_states").fetchone()[0] == 0
        assert connection.execute("SELECT id FROM invite_codes WHERE id = ?", (invite_id,)).fetchone() is not None
        assert connection.execute("SELECT id FROM password_help_requests WHERE id = ?", (help_id,)).fetchone() is not None
        session_row = connection.execute(
            "SELECT revoked_at FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        marker_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE name = ?",
            (ACCOUNT_SHORT_TOKEN_HMAC_MIGRATION_NAME,),
        ).fetchone()[0]

    assert session_row is not None
    assert session_row["revoked_at"] is None
    assert marker_count == 1


def test_disabled_user_session_loses_access_immediately(initialized_settings, client) -> None:
    created = _create_standard_user(initialized_settings, username="family-user")
    session_user, token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )

    client.cookies.set(initialized_settings.session_cookie_name, token)
    me_before_disable = client.get("/api/auth/me")
    assert me_before_disable.status_code == 200
    assert me_before_disable.json()["user"]["username"] == "family-user"

    update_user(
        initialized_settings,
        user_id=int(created["id"]),
        enabled=False,
        role=None,
        current_admin_password=None,
        actor=_admin_user(initialized_settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    assert get_user_by_session_token(initialized_settings, token) is None
    assert get_session_access_failure_reason(initialized_settings, token) == "disabled"

    me_after_disable = client.get("/api/auth/me")
    assert me_after_disable.status_code == 403
    assert me_after_disable.json()["detail"] == "This account has been disabled"
    assert session_user.session_id is not None


@pytest.mark.parametrize(
    ("invalidation_mode", "expected_reason"),
    [
        ("session_revoked", "native_session_revoked"),
        ("user_disabled", "native_session_revoked"),
    ],
)
def test_native_playback_access_is_invalidated_after_parent_session_revoke_or_user_disable(
    initialized_settings,
    monkeypatch,
    invalidation_mode: str,
    expected_reason: str,
) -> None:
    created = _create_standard_user(initialized_settings, username=f"native-{invalidation_mode}")
    session_user, token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )
    item = _create_media_item(initialized_settings, relative_name=f"{invalidation_mode}.mp4")

    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )

    native_session = create_native_playback_session(
        initialized_settings,
        user_id=session_user.id,
        item=item,
        auth_session_id=session_user.session_id,
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name="Pytest Native Handoff",
    )

    payload_before_invalidation = get_native_playback_session_payload(
        initialized_settings,
        session_id=str(native_session["session_id"]),
        access_token=str(native_session["access_token"]),
    )
    assert payload_before_invalidation["session_id"] == native_session["session_id"]
    assert payload_before_invalidation["stream_url"].endswith(
        f"/api/native-playback/session/{native_session['session_id']}/stream?token={native_session['access_token']}"
    )
    assert payload_before_invalidation["details_url"].endswith(
        f"/api/native-playback/session/{native_session['session_id']}?token={native_session['access_token']}"
    )

    if invalidation_mode == "session_revoked":
        destroy_session(initialized_settings, token)
    else:
        update_user(
            initialized_settings,
            user_id=int(created["id"]),
            enabled=False,
            role=None,
            current_admin_password=None,
            actor=_admin_user(initialized_settings),
            ip_address="127.0.0.1",
            user_agent="pytest",
        )

    access_state = inspect_native_playback_access(
        initialized_settings,
        session_id=str(native_session["session_id"]),
        access_token=str(native_session["access_token"]),
    )
    assert access_state["allowed"] is False
    assert access_state["reason"] == expected_reason

    with pytest.raises(HTTPException) as exc_info:
        get_native_playback_session_payload(
            initialized_settings,
            session_id=str(native_session["session_id"]),
            access_token=str(native_session["access_token"]),
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Native playback session is invalid or has expired"


def test_external_player_native_playback_survives_parent_session_revoke(initialized_settings, monkeypatch) -> None:
    created = _create_standard_user(initialized_settings, username="native-ios-vlc")
    session_user, token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )
    item = _create_media_item(initialized_settings, relative_name="ios-vlc.mp4")

    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )

    native_session = create_native_playback_session(
        initialized_settings,
        user_id=session_user.id,
        item=item,
        auth_session_id=None,
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name="Elvern iOS VLC Handoff",
        created_from_auth_session_id=session_user.session_id,
    )

    destroy_session(initialized_settings, token)

    with get_connection(initialized_settings) as connection:
        auth_row = connection.execute(
            "SELECT revoked_at FROM sessions WHERE id = ?",
            (session_user.session_id,),
        ).fetchone()
        native_row = connection.execute(
            """
            SELECT auth_session_id, created_from_auth_session_id, revoked_at
            FROM native_playback_sessions
            WHERE session_id = ?
            LIMIT 1
            """,
            (str(native_session["session_id"]),),
        ).fetchone()

    assert auth_row["revoked_at"] is not None
    assert native_row["auth_session_id"] is None
    assert native_row["created_from_auth_session_id"] == session_user.session_id
    assert native_row["revoked_at"] is None

    access_state = inspect_native_playback_access(
        initialized_settings,
        session_id=str(native_session["session_id"]),
        access_token=str(native_session["access_token"]),
    )
    assert access_state["allowed"] is True
    assert access_state["reason"] == "allowed"

    payload = get_native_playback_session_payload(
        initialized_settings,
        session_id=str(native_session["session_id"]),
        access_token=str(native_session["access_token"]),
    )
    assert payload["session_id"] == native_session["session_id"]


def test_f10d_native_playback_uses_hmac_and_lazy_rehashes_legacy_access_token(
    initialized_settings,
    monkeypatch,
) -> None:
    created = _create_standard_user(initialized_settings, username="native-f10d-hmac")
    session_user, _token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )
    item = _create_media_item(initialized_settings, relative_name="native-f10d-hmac.mp4")
    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )

    native_session = create_native_playback_session(
        initialized_settings,
        user_id=session_user.id,
        item=item,
        auth_session_id=session_user.session_id,
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name="Pytest Native Handoff",
    )
    access_token = str(native_session["access_token"])
    session_id = str(native_session["session_id"])
    hmac_hash = hash_token_hmac(
        initialized_settings,
        purpose="native.playback.access",
        token=access_token,
    )
    legacy_hash = hash_session_token(access_token, initialized_settings.session_secret)

    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT access_token_hash, auth_session_id, created_at, expires_at
            FROM native_playback_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
    assert row is not None
    assert row["access_token_hash"] == hmac_hash
    assert str(row["access_token_hash"]).startswith(TOKEN_HASH_PREFIX)
    assert row["access_token_hash"] != legacy_hash
    assert access_token not in str(row["access_token_hash"])
    assert str(native_session["stream_url"]).endswith(
        f"/api/native-playback/session/{session_id}/stream?token={access_token}"
    )
    assert str(native_session["details_url"]).endswith(
        f"/api/native-playback/session/{session_id}?token={access_token}"
    )

    original_auth_session_id = row["auth_session_id"]
    original_created_at = row["created_at"]
    original_expires_at = row["expires_at"]
    with get_connection(initialized_settings) as connection:
        connection.execute(
            """
            UPDATE native_playback_sessions
            SET access_token_hash = ?
            WHERE session_id = ?
            """,
            (legacy_hash, session_id),
        )
        connection.commit()

    payload = get_native_playback_session_payload(
        initialized_settings,
        session_id=session_id,
        access_token=access_token,
    )
    assert payload["session_id"] == session_id

    with get_connection(initialized_settings) as connection:
        rehashed = connection.execute(
            """
            SELECT access_token_hash, auth_session_id, created_at, expires_at
            FROM native_playback_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
    assert rehashed is not None
    assert rehashed["access_token_hash"] == hmac_hash
    assert rehashed["auth_session_id"] == original_auth_session_id
    assert rehashed["created_at"] == original_created_at
    assert rehashed["expires_at"] == original_expires_at


@pytest.mark.parametrize(
    ("failure_mode", "token_suffix", "expected_present"),
    [
        ("wrong_token", "-wrong", True),
        ("expired", "", False),
        ("revoked", "", False),
        ("closed", "", False),
        ("disabled_user", "", True),
    ],
)
def test_f10d_native_playback_legacy_failure_paths_do_not_rehash(
    initialized_settings,
    monkeypatch,
    failure_mode: str,
    token_suffix: str,
    expected_present: bool,
) -> None:
    created = _create_standard_user(initialized_settings, username=f"native-f10d-{failure_mode}")
    session_user, _token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )
    item = _create_media_item(initialized_settings, relative_name=f"native-f10d-{failure_mode}.mp4")
    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )
    native_session = create_native_playback_session(
        initialized_settings,
        user_id=session_user.id,
        item=item,
        auth_session_id=None,
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name="Pytest Native Handoff",
    )
    session_id = str(native_session["session_id"])
    access_token = str(native_session["access_token"])
    legacy_hash = hash_session_token(access_token, initialized_settings.session_secret)
    now = datetime.now(timezone.utc)
    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE native_playback_sessions SET access_token_hash = ? WHERE session_id = ?",
            (legacy_hash, session_id),
        )
        if failure_mode == "expired":
            connection.execute(
                "UPDATE native_playback_sessions SET expires_at = ? WHERE session_id = ?",
                ((now - timedelta(seconds=5)).isoformat(), session_id),
            )
        elif failure_mode == "revoked":
            connection.execute(
                "UPDATE native_playback_sessions SET revoked_at = ? WHERE session_id = ?",
                (now.isoformat(), session_id),
            )
        elif failure_mode == "closed":
            connection.execute(
                "UPDATE native_playback_sessions SET closed_at = ? WHERE session_id = ?",
                (now.isoformat(), session_id),
            )
        elif failure_mode == "disabled_user":
            connection.execute("UPDATE users SET enabled = 0 WHERE id = ?", (session_user.id,))
        connection.commit()

    with pytest.raises(HTTPException) as exc_info:
        get_native_playback_session_payload(
            initialized_settings,
            session_id=session_id,
            access_token=access_token + token_suffix,
        )
    assert exc_info.value.status_code == 401

    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            "SELECT access_token_hash FROM native_playback_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if expected_present:
        assert row is not None
        assert row["access_token_hash"] == legacy_hash
    else:
        assert row is None


def test_f10d_desktop_vlc_handoff_uses_hmac_and_lazy_rehashes_legacy_access_token(
    initialized_settings,
) -> None:
    created = _create_standard_user(initialized_settings, username="desktop-handoff-f10d-hmac")
    session_user, _token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )
    item = _create_media_item(initialized_settings, relative_name="desktop-handoff-f10d-hmac.mp4")

    handoff = create_desktop_vlc_handoff_record(
        initialized_settings,
        user_id=session_user.id,
        item=item,
        platform="windows",
        device_id="desktop-handoff-f10d",
        auth_session_id=session_user.session_id,
        user_agent="pytest",
        source_ip="127.0.0.1",
        strategy="backend_url",
        resolved_target="http://testserver/api/native-playback/session/example/stream?token=existing",
        backend_origin="http://testserver",
    )
    handoff_id = str(handoff["handoff_id"])
    access_token = _query_param(handoff["protocol_url"], "token")
    assert _query_param(handoff["protocol_url"], "handoff") == handoff_id
    assert urlsplit(str(handoff["protocol_url"])).scheme == initialized_settings.vlc_helper_protocol
    hmac_hash = hash_token_hmac(
        initialized_settings,
        purpose="desktop.vlc.handoff.access",
        token=access_token,
    )
    legacy_hash = hash_session_token(access_token, initialized_settings.session_secret)

    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT access_token_hash, auth_session_id, strategy, expires_at
            FROM desktop_vlc_handoffs
            WHERE handoff_id = ?
            """,
            (handoff_id,),
        ).fetchone()
    assert row is not None
    assert row["access_token_hash"] == hmac_hash
    assert str(row["access_token_hash"]).startswith(TOKEN_HASH_PREFIX)
    assert row["access_token_hash"] != legacy_hash
    assert access_token not in str(row["access_token_hash"])

    original_auth_session_id = row["auth_session_id"]
    original_strategy = row["strategy"]
    original_expires_at = row["expires_at"]
    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE desktop_vlc_handoffs SET access_token_hash = ? WHERE handoff_id = ?",
            (legacy_hash, handoff_id),
        )
        connection.commit()

    resolved = resolve_desktop_vlc_handoff_record(
        initialized_settings,
        handoff_id=handoff_id,
        access_token=access_token,
        helper_version="1.0.0",
        helper_platform="windows",
        helper_arch="x64",
        helper_vlc_detection_state="installed",
        helper_vlc_detection_path="C:/Program Files/VideoLAN/VLC/vlc.exe",
        source_ip="127.0.0.1",
        backend_origin="http://testserver",
    )
    assert resolved["handoff_id"] == handoff_id

    with get_connection(initialized_settings) as connection:
        rehashed = connection.execute(
            """
            SELECT access_token_hash, auth_session_id, strategy, expires_at
            FROM desktop_vlc_handoffs
            WHERE handoff_id = ?
            """,
            (handoff_id,),
        ).fetchone()
    assert rehashed is not None
    assert rehashed["access_token_hash"] == hmac_hash
    assert rehashed["auth_session_id"] == original_auth_session_id
    assert rehashed["strategy"] == original_strategy
    assert rehashed["expires_at"] == original_expires_at


@pytest.mark.parametrize(
    ("failure_mode", "token_suffix", "expected_present"),
    [
        ("wrong_token", "-wrong", True),
        ("expired", "", False),
        ("revoked", "", False),
        ("disabled_user", "", True),
    ],
)
def test_f10d_desktop_vlc_handoff_legacy_failure_paths_do_not_rehash(
    initialized_settings,
    failure_mode: str,
    token_suffix: str,
    expected_present: bool,
) -> None:
    created = _create_standard_user(initialized_settings, username=f"desktop-handoff-f10d-{failure_mode}")
    session_user, _token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )
    item = _create_media_item(initialized_settings, relative_name=f"desktop-handoff-f10d-{failure_mode}.mp4")
    handoff = create_desktop_vlc_handoff_record(
        initialized_settings,
        user_id=session_user.id,
        item=item,
        platform="windows",
        device_id=f"desktop-handoff-f10d-{failure_mode}",
        auth_session_id=session_user.session_id,
        user_agent="pytest",
        source_ip="127.0.0.1",
        strategy="backend_url",
        resolved_target="http://testserver/api/native-playback/session/example/stream?token=existing",
        backend_origin="http://testserver",
    )
    handoff_id = str(handoff["handoff_id"])
    access_token = _query_param(handoff["protocol_url"], "token")
    legacy_hash = hash_session_token(access_token, initialized_settings.session_secret)
    now = datetime.now(timezone.utc)
    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE desktop_vlc_handoffs SET access_token_hash = ? WHERE handoff_id = ?",
            (legacy_hash, handoff_id),
        )
        if failure_mode == "expired":
            connection.execute(
                "UPDATE desktop_vlc_handoffs SET expires_at = ? WHERE handoff_id = ?",
                ((now - timedelta(seconds=5)).isoformat(), handoff_id),
            )
        elif failure_mode == "revoked":
            connection.execute(
                "UPDATE desktop_vlc_handoffs SET revoked_at = ? WHERE handoff_id = ?",
                (now.isoformat(), handoff_id),
            )
        elif failure_mode == "disabled_user":
            connection.execute("UPDATE users SET enabled = 0 WHERE id = ?", (session_user.id,))
        connection.commit()

    with pytest.raises(HTTPException) as exc_info:
        resolve_desktop_vlc_handoff_record(
            initialized_settings,
            handoff_id=handoff_id,
            access_token=access_token + token_suffix,
            backend_origin="http://testserver",
        )
    assert exc_info.value.status_code == 401

    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            "SELECT access_token_hash FROM desktop_vlc_handoffs WHERE handoff_id = ?",
            (handoff_id,),
        ).fetchone()
    if expected_present:
        assert row is not None
        assert row["access_token_hash"] == legacy_hash
    else:
        assert row is None


def test_f10d_desktop_helper_verification_uses_hmac_and_lazy_rehashes_legacy_access_token(
    initialized_settings,
) -> None:
    settings = replace(initialized_settings, backend_origin="http://testserver")
    created = _create_standard_user(settings, username="desktop-helper-f10d-hmac")
    session_user, _token = _issue_user_session(
        settings,
        username=str(created["username"]),
        password="family-password",
    )

    verification = create_desktop_helper_verification(
        settings,
        user_id=session_user.id,
        platform="windows",
        device_id="desktop-helper-f10d",
        browser_user_agent="pytest",
        source_ip="127.0.0.1",
    )
    verification_id = _query_param(verification["protocol_url"], "verification")
    access_token = _query_param(verification["protocol_url"], "token")
    assert urlsplit(str(verification["protocol_url"])).scheme == settings.vlc_helper_protocol
    hmac_hash = hash_token_hmac(
        settings,
        purpose="desktop.helper.verification.access",
        token=access_token,
    )
    legacy_hash = hash_session_token(access_token, settings.session_secret)

    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT access_token_hash, user_id, platform, device_id, expires_at, resolved_at
            FROM desktop_helper_verifications
            WHERE verification_id = ?
            """,
            (verification_id,),
        ).fetchone()
    assert row is not None
    assert row["access_token_hash"] == hmac_hash
    assert str(row["access_token_hash"]).startswith(TOKEN_HASH_PREFIX)
    assert row["access_token_hash"] != legacy_hash
    assert access_token not in str(row["access_token_hash"])

    original_user_id = row["user_id"]
    original_platform = row["platform"]
    original_device_id = row["device_id"]
    original_expires_at = row["expires_at"]
    with get_connection(settings) as connection:
        connection.execute(
            "UPDATE desktop_helper_verifications SET access_token_hash = ? WHERE verification_id = ?",
            (legacy_hash, verification_id),
        )
        connection.commit()

    result = resolve_desktop_helper_verification(
        settings,
        verification_id=verification_id,
        access_token=access_token,
        helper_version="1.0.0",
        helper_platform="windows",
        helper_arch="x64",
        helper_vlc_detection_state="installed",
        helper_vlc_detection_path="C:/Program Files/VideoLAN/VLC/vlc.exe",
        source_ip="127.0.0.1",
    )
    assert result["message"] == "Desktop helper verification recorded."

    with get_connection(settings) as connection:
        rehashed = connection.execute(
            """
            SELECT access_token_hash, user_id, platform, device_id, expires_at, resolved_at
            FROM desktop_helper_verifications
            WHERE verification_id = ?
            """,
            (verification_id,),
        ).fetchone()
    assert rehashed is not None
    assert rehashed["access_token_hash"] == hmac_hash
    assert rehashed["user_id"] == original_user_id
    assert rehashed["platform"] == original_platform
    assert rehashed["device_id"] == original_device_id
    assert rehashed["expires_at"] == original_expires_at
    assert rehashed["resolved_at"] is not None


@pytest.mark.parametrize(
    ("failure_mode", "token_suffix", "expected_present"),
    [
        ("wrong_token", "-wrong", True),
        ("expired", "", False),
        ("resolved", "", False),
        ("disabled_user", "", True),
    ],
)
def test_f10d_desktop_helper_verification_legacy_failure_paths_do_not_rehash(
    initialized_settings,
    failure_mode: str,
    token_suffix: str,
    expected_present: bool,
) -> None:
    settings = replace(initialized_settings, backend_origin="http://testserver")
    created = _create_standard_user(settings, username=f"desktop-helper-f10d-{failure_mode}")
    session_user, _token = _issue_user_session(
        settings,
        username=str(created["username"]),
        password="family-password",
    )
    verification = create_desktop_helper_verification(
        settings,
        user_id=session_user.id,
        platform="windows",
        device_id=f"desktop-helper-f10d-{failure_mode}",
        browser_user_agent="pytest",
        source_ip="127.0.0.1",
    )
    verification_id = _query_param(verification["protocol_url"], "verification")
    access_token = _query_param(verification["protocol_url"], "token")
    legacy_hash = hash_session_token(access_token, settings.session_secret)
    now = datetime.now(timezone.utc)
    with get_connection(settings) as connection:
        connection.execute(
            "UPDATE desktop_helper_verifications SET access_token_hash = ? WHERE verification_id = ?",
            (legacy_hash, verification_id),
        )
        if failure_mode == "expired":
            connection.execute(
                "UPDATE desktop_helper_verifications SET expires_at = ? WHERE verification_id = ?",
                ((now - timedelta(seconds=5)).isoformat(), verification_id),
            )
        elif failure_mode == "resolved":
            connection.execute(
                "UPDATE desktop_helper_verifications SET resolved_at = ? WHERE verification_id = ?",
                (now.isoformat(), verification_id),
            )
        elif failure_mode == "disabled_user":
            connection.execute("UPDATE users SET enabled = 0 WHERE id = ?", (session_user.id,))
        connection.commit()

    with pytest.raises(HTTPException) as exc_info:
        resolve_desktop_helper_verification(
            settings,
            verification_id=verification_id,
            access_token=access_token + token_suffix,
            helper_version="1.0.0",
            helper_platform="windows",
            helper_arch="x64",
            source_ip="127.0.0.1",
        )
    assert exc_info.value.status_code == 404

    with get_connection(settings) as connection:
        row = connection.execute(
            "SELECT access_token_hash FROM desktop_helper_verifications WHERE verification_id = ?",
            (verification_id,),
        ).fetchone()
    if expected_present:
        assert row is not None
        assert row["access_token_hash"] == legacy_hash
    else:
        assert row is None


def test_admin_revoke_auth_session_revokes_decoupled_external_native_playback_created_from_login(
    initialized_settings,
    client,
    admin_credentials,
    monkeypatch,
) -> None:
    created = _create_standard_user(initialized_settings, username="native-admin-revoke")
    session_user, _token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )
    item = _create_media_item(initialized_settings, relative_name="native-admin-revoke.mp4")

    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )

    native_session = create_native_playback_session(
        initialized_settings,
        user_id=session_user.id,
        item=item,
        auth_session_id=None,
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name="Elvern iOS Infuse Handoff",
        created_from_auth_session_id=session_user.session_id,
    )

    admin_login = client.post("/api/auth/login", json=admin_credentials)
    assert admin_login.status_code == 200
    _stub_mobile_auth_session_invalidation(client)
    revoke_response = client.post(f"/api/admin/sessions/{session_user.session_id}/revoke")

    assert revoke_response.status_code == 200
    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT auth_session_id, created_from_auth_session_id, revoked_at
            FROM native_playback_sessions
            WHERE session_id = ?
            LIMIT 1
            """,
            (str(native_session["session_id"]),),
        ).fetchone()

    assert row["auth_session_id"] is None
    assert row["created_from_auth_session_id"] == session_user.session_id
    assert row["revoked_at"] is not None

    access_state = inspect_native_playback_access(
        initialized_settings,
        session_id=str(native_session["session_id"]),
        access_token=str(native_session["access_token"]),
    )
    assert access_state["allowed"] is False
    assert access_state["reason"] == "native_session_revoked"

    with pytest.raises(HTTPException) as exc_info:
        get_native_playback_session_payload(
            initialized_settings,
            session_id=str(native_session["session_id"]),
            access_token=str(native_session["access_token"]),
        )
    assert exc_info.value.status_code == 401


def test_admin_revoke_auth_session_leaves_old_provenance_less_decoupled_native_playback(
    initialized_settings,
    client,
    admin_credentials,
    monkeypatch,
) -> None:
    created = _create_standard_user(initialized_settings, username="native-old-row")
    session_user, _token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )
    item = _create_media_item(initialized_settings, relative_name="native-old-row.mp4")

    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )

    native_session = create_native_playback_session(
        initialized_settings,
        user_id=session_user.id,
        item=item,
        auth_session_id=None,
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name="Elvern iOS VLC Handoff",
    )

    admin_login = client.post("/api/auth/login", json=admin_credentials)
    assert admin_login.status_code == 200
    _stub_mobile_auth_session_invalidation(client)
    revoke_response = client.post(f"/api/admin/sessions/{session_user.session_id}/revoke")

    assert revoke_response.status_code == 200
    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT auth_session_id, created_from_auth_session_id, revoked_at
            FROM native_playback_sessions
            WHERE session_id = ?
            LIMIT 1
            """,
            (str(native_session["session_id"]),),
        ).fetchone()

    assert row["auth_session_id"] is None
    assert row["created_from_auth_session_id"] is None
    assert row["revoked_at"] is None

    access_state = inspect_native_playback_access(
        initialized_settings,
        session_id=str(native_session["session_id"]),
        access_token=str(native_session["access_token"]),
    )
    assert access_state["allowed"] is True
    assert access_state["reason"] == "allowed"


def test_admin_revoke_auth_session_still_revokes_coupled_native_playback(
    initialized_settings,
    client,
    admin_credentials,
    monkeypatch,
) -> None:
    created = _create_standard_user(initialized_settings, username="native-coupled-revoke")
    session_user, _token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )
    item = _create_media_item(initialized_settings, relative_name="native-coupled-revoke.mp4")

    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )

    native_session = create_native_playback_session(
        initialized_settings,
        user_id=session_user.id,
        item=item,
        auth_session_id=session_user.session_id,
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name="Linux Same-Host VLC",
        created_from_auth_session_id=session_user.session_id,
    )

    admin_login = client.post("/api/auth/login", json=admin_credentials)
    assert admin_login.status_code == 200
    _stub_mobile_auth_session_invalidation(client)
    revoke_response = client.post(f"/api/admin/sessions/{session_user.session_id}/revoke")

    assert revoke_response.status_code == 200
    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT auth_session_id, created_from_auth_session_id, revoked_at
            FROM native_playback_sessions
            WHERE session_id = ?
            LIMIT 1
            """,
            (str(native_session["session_id"]),),
        ).fetchone()

    assert row["auth_session_id"] == session_user.session_id
    assert row["created_from_auth_session_id"] == session_user.session_id
    assert row["revoked_at"] is not None


@pytest.mark.parametrize(
    ("invalidation_mode", "expected_reason"),
    [
        ("user_disabled", "native_session_revoked"),
        ("native_session_revoked", "native_session_revoked"),
    ],
)
def test_external_player_native_playback_still_respects_user_disable_and_native_revoke(
    initialized_settings,
    monkeypatch,
    invalidation_mode: str,
    expected_reason: str,
) -> None:
    created = _create_standard_user(initialized_settings, username=f"native-external-{invalidation_mode}")
    session_user, _token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )
    item = _create_media_item(initialized_settings, relative_name=f"{invalidation_mode}-external.mp4")

    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )

    native_session = create_native_playback_session(
        initialized_settings,
        user_id=session_user.id,
        item=item,
        auth_session_id=None,
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name="Elvern iOS Infuse Handoff",
    )

    if invalidation_mode == "user_disabled":
        update_user(
            initialized_settings,
            user_id=int(created["id"]),
            enabled=False,
            role=None,
            current_admin_password=None,
            actor=_admin_user(initialized_settings),
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
    else:
        with get_connection(initialized_settings) as connection:
            connection.execute(
                """
                UPDATE native_playback_sessions
                SET revoked_at = ?
                WHERE session_id = ?
                """,
                (utcnow_iso(), str(native_session["session_id"])),
            )
            connection.commit()

    access_state = inspect_native_playback_access(
        initialized_settings,
        session_id=str(native_session["session_id"]),
        access_token=str(native_session["access_token"]),
    )
    assert access_state["allowed"] is False
    assert access_state["reason"] == expected_reason


@pytest.mark.parametrize(
    ("client_name", "username_slug"),
    [
        ("Elvern iOS VLC Handoff", "ios-vlc"),
        ("Elvern iOS Infuse Handoff", "ios-infuse"),
        ("VLC Helper Fallback (windows)", "desktop-vlc-helper"),
        ("VLC Playlist Fallback (mac)", "desktop-vlc-playlist"),
        ("Linux Same-Host VLC", "linux-same-host-vlc"),
    ],
)
def test_external_player_native_playback_uses_external_stream_ttl(
    initialized_settings,
    monkeypatch,
    client_name: str,
    username_slug: str,
) -> None:
    created = _create_standard_user(initialized_settings, username=f"native-{username_slug}")
    session_user, _token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )
    item = _create_media_item(initialized_settings, relative_name=f"{username_slug}.mp4")

    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )

    native_session = create_native_playback_session(
        initialized_settings,
        user_id=session_user.id,
        item=item,
        auth_session_id=None,
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name=client_name,
    )

    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT auth_session_id, created_at, expires_at
            FROM native_playback_sessions
            WHERE session_id = ?
            LIMIT 1
            """,
            (str(native_session["session_id"]),),
        ).fetchone()

    assert row is not None
    assert row["auth_session_id"] is None
    created_at = datetime.fromisoformat(str(row["created_at"]))
    expires_at = datetime.fromisoformat(str(row["expires_at"]))
    assert int((expires_at - created_at).total_seconds()) == initialized_settings.external_player_stream_ttl_seconds

    policy = _build_native_playback_stream_policy(
        initialized_settings,
        client_name=client_name,
        stream_path_class="local_file",
    )
    assert policy.external_player is True
    assert should_decouple_external_player_auth_session(client_name=client_name) is True


def test_ios_vlc_external_playback_survives_longer_than_native_session_minutes(
    initialized_settings,
    monkeypatch,
) -> None:
    created = _create_standard_user(initialized_settings, username="ios-vlc-airplay-pause")
    session_user, token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )
    item = _create_media_item(initialized_settings, relative_name="ios-vlc-airplay-pause.mp4")

    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )

    native_session = create_native_playback_session(
        initialized_settings,
        user_id=session_user.id,
        item=item,
        auth_session_id=None,
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name="Elvern iOS VLC Handoff",
    )

    now = datetime.now(timezone.utc)
    paused_since = now - timedelta(minutes=initialized_settings.native_playback_session_minutes + 2)
    external_expires_at = paused_since + timedelta(
        seconds=initialized_settings.external_player_stream_ttl_seconds,
    )
    assert external_expires_at > now
    with get_connection(initialized_settings) as connection:
        connection.execute(
            """
            UPDATE native_playback_sessions
            SET created_at = ?, last_seen_at = ?, expires_at = ?
            WHERE session_id = ?
            """,
            (
                paused_since.isoformat(),
                paused_since.isoformat(),
                external_expires_at.isoformat(),
                str(native_session["session_id"]),
            ),
        )
        connection.commit()

    destroy_session(initialized_settings, token)

    access_state = inspect_native_playback_access(
        initialized_settings,
        session_id=str(native_session["session_id"]),
        access_token=str(native_session["access_token"]),
    )
    assert access_state["allowed"] is True
    assert access_state["reason"] == "allowed"

    response = build_native_stream_response(
        initialized_settings,
        session_id=str(native_session["session_id"]),
        access_token=str(native_session["access_token"]),
        range_header="bytes=0-1",
        record_activity=False,
    )
    context = getattr(response, "_elvern_native_stream_context", None)
    assert response.status_code == 206
    assert context is not None
    assert context["external_player"] is True
    assert context["auth_session_coupled"] is False
    assert context["session_ttl_seconds"] == initialized_settings.external_player_stream_ttl_seconds


def test_desktop_vlc_external_playback_survives_browser_auth_revoke_and_normal_ttl_pause(
    initialized_settings,
    monkeypatch,
) -> None:
    created = _create_standard_user(initialized_settings, username="desktop-vlc-pause")
    session_user, token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )
    item = _create_media_item(initialized_settings, relative_name="desktop-vlc-pause.mp4")

    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )

    native_session = create_native_playback_session(
        initialized_settings,
        user_id=session_user.id,
        item=item,
        auth_session_id=None,
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name="VLC Playlist Fallback (windows)",
    )

    now = datetime.now(timezone.utc)
    paused_since = now - timedelta(seconds=initialized_settings.playback_token_ttl_seconds + 60)
    external_expires_at = paused_since + timedelta(
        seconds=initialized_settings.external_player_stream_ttl_seconds,
    )
    assert external_expires_at > now
    with get_connection(initialized_settings) as connection:
        connection.execute(
            """
            UPDATE native_playback_sessions
            SET created_at = ?, last_seen_at = ?, expires_at = ?
            WHERE session_id = ?
            """,
            (
                paused_since.isoformat(),
                paused_since.isoformat(),
                external_expires_at.isoformat(),
                str(native_session["session_id"]),
            ),
        )
        connection.commit()

    destroy_session(initialized_settings, token)

    access_state = inspect_native_playback_access(
        initialized_settings,
        session_id=str(native_session["session_id"]),
        access_token=str(native_session["access_token"]),
    )
    assert access_state["allowed"] is True
    assert access_state["reason"] == "allowed"

    response = build_native_stream_response(
        initialized_settings,
        session_id=str(native_session["session_id"]),
        access_token=str(native_session["access_token"]),
        range_header="bytes=0-1",
        record_activity=False,
    )
    context = getattr(response, "_elvern_native_stream_context", None)
    assert context is not None
    assert context["external_player"] is True
    assert context["auth_session_coupled"] is False
    assert context["session_ttl_seconds"] == initialized_settings.external_player_stream_ttl_seconds


@pytest.mark.parametrize(
    ("invalidation_mode", "expected_reason"),
    [
        ("user_disabled", "native_session_revoked"),
        ("native_session_revoked", "native_session_revoked"),
        ("native_session_closed", "native_session_closed"),
    ],
)
def test_desktop_vlc_external_playback_still_respects_disable_revoke_and_close(
    initialized_settings,
    monkeypatch,
    invalidation_mode: str,
    expected_reason: str,
) -> None:
    created = _create_standard_user(initialized_settings, username=f"desktop-vlc-{invalidation_mode}")
    session_user, _token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )
    item = _create_media_item(initialized_settings, relative_name=f"desktop-vlc-{invalidation_mode}.mp4")

    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )

    native_session = create_native_playback_session(
        initialized_settings,
        user_id=session_user.id,
        item=item,
        auth_session_id=None,
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name="VLC Playlist Fallback (windows)",
    )

    if invalidation_mode == "user_disabled":
        update_user(
            initialized_settings,
            user_id=int(created["id"]),
            enabled=False,
            role=None,
            current_admin_password=None,
            actor=_admin_user(initialized_settings),
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
    elif invalidation_mode == "native_session_revoked":
        with get_connection(initialized_settings) as connection:
            connection.execute(
                """
                UPDATE native_playback_sessions
                SET revoked_at = ?
                WHERE session_id = ?
                """,
                (utcnow_iso(), str(native_session["session_id"])),
            )
            connection.commit()
    else:
        close_native_playback_session(
            initialized_settings,
            session_id=str(native_session["session_id"]),
            access_token=str(native_session["access_token"]),
        )

    access_state = inspect_native_playback_access(
        initialized_settings,
        session_id=str(native_session["session_id"]),
        access_token=str(native_session["access_token"]),
    )
    assert access_state["allowed"] is False
    assert access_state["reason"] == expected_reason


def test_build_native_stream_response_exposes_external_player_debug_context(initialized_settings, monkeypatch) -> None:
    created = _create_standard_user(initialized_settings, username="native-stream-context")
    session_user, _token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )
    item = _create_media_item(initialized_settings, relative_name="native-stream-context.mp4")

    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )

    native_session = create_native_playback_session(
        initialized_settings,
        user_id=session_user.id,
        item=item,
        auth_session_id=None,
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name="Elvern iOS VLC Handoff",
    )

    response = build_native_stream_response(
        initialized_settings,
        session_id=str(native_session["session_id"]),
        access_token=str(native_session["access_token"]),
        range_header=None,
        record_activity=False,
    )
    context = getattr(response, "_elvern_native_stream_context", None)

    assert context is not None
    assert context["external_player"] is True
    assert context["validation_interval_seconds"] == 5.0
    assert context["ttl_refresh_interval_seconds"] == 60.0
    assert context["chunk_size_bytes"] == 2 * 1024 * 1024
    assert context["auth_session_coupled"] is False
    assert context["session_ttl_seconds"] == initialized_settings.external_player_stream_ttl_seconds


def test_admin_native_playback_status_exposes_vlc_and_infuse_without_sensitive_fields(
    initialized_settings,
    monkeypatch,
) -> None:
    created = _create_standard_user(initialized_settings, username="admin-native-visible")
    session_user, _token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )
    vlc_item = _create_media_item(initialized_settings, relative_name="admin-native-vlc.mp4")
    infuse_item = _create_media_item(initialized_settings, relative_name="admin-native-infuse.mp4")
    _set_media_admin_display_fields(
        initialized_settings,
        item_id=int(vlc_item["id"]),
        source_kind="cloud",
        width=1920,
        height=1080,
    )
    _set_media_admin_display_fields(
        initialized_settings,
        item_id=int(infuse_item["id"]),
        source_kind="local",
        width=1280,
        height=720,
    )

    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )

    vlc_session = create_native_playback_session(
        initialized_settings,
        user_id=session_user.id,
        item=vlc_item,
        auth_session_id=None,
        user_agent=(
            "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
        ),
        source_ip="127.0.0.1",
        client_name="Elvern iOS VLC Handoff",
    )
    infuse_session = create_native_playback_session(
        initialized_settings,
        user_id=session_user.id,
        item=infuse_item,
        auth_session_id=None,
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name="Elvern iOS Infuse Handoff",
    )
    _mark_native_stream_activity(
        initialized_settings,
        session_id=str(vlc_session["session_id"]),
    )
    _mark_native_stream_activity(
        initialized_settings,
        session_id=str(infuse_session["session_id"]),
    )

    payload = get_admin_native_playback_status(initialized_settings)

    assert payload["native_playback_count"] == 2
    group = payload["native_playbacks_by_user"][0]
    assert group["user_id"] == session_user.id
    assert group["total_native_playbacks"] == 2
    items = {item["session_id"]: item for item in group["items"]}
    vlc = items[str(vlc_session["session_id"])]
    assert vlc["playback_kind"] == "native"
    assert vlc["playback_surface"] == "vlc_backend_stream"
    assert vlc["playback_surface_label"] == "VLC"
    assert vlc["device_label"] == "iPad"
    assert vlc["device_evidence_source"] == "user_agent"
    assert vlc["display_profile_label"] == "1080p"
    assert vlc["source_label"] == "Cloud"
    assert vlc["playback_metadata_label"] == "VLC \u00b7 iPad 1080p \u00b7 Cloud"
    assert vlc["external_player"] is True
    assert vlc["auth_session_coupled"] is False
    assert vlc["display_status_label"] == "Running"
    assert vlc["last_stream_activity_at"] is not None

    infuse = items[str(infuse_session["session_id"])]
    assert infuse["playback_surface_label"] == "Infuse"
    assert infuse["device_label"] == "iOS device"
    assert infuse["display_profile_label"] == "720p"
    assert infuse["playback_metadata_label"] == "Infuse \u00b7 iOS device 720p \u00b7 Local"

    serialized = json.dumps(payload).lower()
    assert "access_token" not in serialized
    assert "access_token_hash" not in serialized
    assert "source_ip" not in serialized
    assert "file_path" not in serialized
    assert "mozilla" not in serialized


def test_admin_native_playback_status_maps_desktop_and_terminal_states(
    initialized_settings,
    monkeypatch,
) -> None:
    created = _create_standard_user(initialized_settings, username="admin-native-states")
    session_user, _token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )
    item = _create_media_item(initialized_settings, relative_name="admin-native-states.mp4")
    _set_media_admin_display_fields(
        initialized_settings,
        item_id=int(item["id"]),
        source_kind="local",
        width=3840,
        height=2160,
    )

    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )

    sessions = {
        "running": create_native_playback_session(
            initialized_settings,
            user_id=session_user.id,
            item=item,
            auth_session_id=None,
            user_agent="pytest",
            source_ip="127.0.0.1",
            client_name="VLC Helper Fallback (windows)",
        ),
        "idle": create_native_playback_session(
            initialized_settings,
            user_id=session_user.id,
            item=item,
            auth_session_id=None,
            user_agent="pytest",
            source_ip="127.0.0.1",
            client_name="VLC Playlist Fallback (linux)",
        ),
        "expired": create_native_playback_session(
            initialized_settings,
            user_id=session_user.id,
            item=item,
            auth_session_id=None,
            user_agent="pytest",
            source_ip="127.0.0.1",
            client_name="Linux Same-Host VLC",
        ),
        "revoked": create_native_playback_session(
            initialized_settings,
            user_id=session_user.id,
            item=item,
            auth_session_id=None,
            user_agent="pytest",
            source_ip="127.0.0.1",
            client_name="VLC Helper Fallback (mac)",
        ),
        "closed": create_native_playback_session(
            initialized_settings,
            user_id=session_user.id,
            item=item,
            auth_session_id=None,
            user_agent="pytest",
            source_ip="127.0.0.1",
            client_name="Linux Same-Host VLC Direct",
        ),
    }

    now = datetime.now(timezone.utc)
    for session in sessions.values():
        _mark_native_stream_activity(
            initialized_settings,
            session_id=str(session["session_id"]),
            at=now,
        )
    with get_connection(initialized_settings) as connection:
        connection.execute(
            """
            UPDATE native_playback_sessions
            SET last_seen_at = ?, last_progress_recorded_at = ?
            WHERE session_id = ?
            """,
            (
                (now - timedelta(minutes=10)).isoformat(),
                (now - timedelta(minutes=10)).isoformat(),
                str(sessions["idle"]["session_id"]),
            ),
        )
        connection.execute(
            """
            UPDATE native_playback_sessions
            SET expires_at = ?
            WHERE session_id = ?
            """,
            (
                (now - timedelta(seconds=5)).isoformat(),
                str(sessions["expired"]["session_id"]),
            ),
        )
        connection.execute(
            """
            UPDATE native_playback_sessions
            SET revoked_at = ?
            WHERE session_id = ?
            """,
            (now.isoformat(), str(sessions["revoked"]["session_id"])),
        )
        connection.execute(
            """
            UPDATE native_playback_sessions
            SET closed_at = ?
            WHERE session_id = ?
            """,
            (now.isoformat(), str(sessions["closed"]["session_id"])),
        )
        connection.commit()

    payload = get_admin_native_playback_status(initialized_settings)
    group = payload["native_playbacks_by_user"][0]
    items = {item["session_id"]: item for item in group["items"]}

    running = items[str(sessions["running"]["session_id"])]
    assert running["playback_metadata_label"] == "VLC \u00b7 Windows PC 2160p \u00b7 Local"
    assert running["display_status_label"] == "Running"
    assert running["display_status_tone"] == "success"
    assert running["auth_session_coupled"] is False

    assert str(sessions["idle"]["session_id"]) not in items
    assert str(sessions["expired"]["session_id"]) not in items
    assert str(sessions["revoked"]["session_id"]) not in items
    assert str(sessions["closed"]["session_id"]) not in items
    assert payload["native_playback_count"] == 1
    assert group["total_native_playbacks"] == 1
    assert group["running_native_playbacks"] == 1
    assert group["idle_native_playbacks"] == 0


def test_admin_native_playback_status_hides_long_ttl_idle_sessions_without_deleting_them(
    initialized_settings,
    monkeypatch,
) -> None:
    created = _create_standard_user(initialized_settings, username="admin-native-idle-hidden")
    session_user, _token = _issue_user_session(
        initialized_settings,
        username=str(created["username"]),
        password="family-password",
    )
    vlc_item = _create_media_item(initialized_settings, relative_name="admin-native-idle-vlc.mp4")
    infuse_item = _create_media_item(initialized_settings, relative_name="admin-native-idle-infuse.mp4")

    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )

    vlc_session = create_native_playback_session(
        initialized_settings,
        user_id=session_user.id,
        item=vlc_item,
        auth_session_id=None,
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name="Elvern iOS VLC Handoff",
    )
    infuse_session = create_native_playback_session(
        initialized_settings,
        user_id=session_user.id,
        item=infuse_item,
        auth_session_id=None,
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name="Elvern iOS Infuse Handoff",
    )

    payload = get_admin_native_playback_status(initialized_settings)

    assert payload["native_playback_count"] == 0
    assert payload["native_playbacks_by_user"] == []
    with get_connection(initialized_settings) as connection:
        rows = connection.execute(
            """
            SELECT session_id, expires_at, closed_at, revoked_at
            FROM native_playback_sessions
            WHERE session_id IN (?, ?)
            """,
            (str(vlc_session["session_id"]), str(infuse_session["session_id"])),
        ).fetchall()
    assert len(rows) == 2
    now = datetime.now(timezone.utc)
    for row in rows:
        assert row["closed_at"] is None
        assert row["revoked_at"] is None
        assert datetime.fromisoformat(str(row["expires_at"])) > now


def test_browser_internal_native_stream_policy_remains_short_lived(initialized_settings) -> None:
    policy = _build_native_playback_stream_policy(
        initialized_settings,
        client_name="Pytest Native Handoff",
        stream_path_class="local_file",
    )

    assert policy.external_player is False
    assert policy.session_ttl_seconds == initialized_settings.playback_token_ttl_seconds
    assert policy.validation_interval_seconds == 0.25
    assert policy.ttl_refresh_interval_seconds == 30.0
    assert policy.chunk_size_bytes == 64 * 1024


def test_disabled_user_login_returns_disabled_reason(initialized_settings, client) -> None:
    created = _create_standard_user(initialized_settings, username="disabled-login-user")

    update_user(
        initialized_settings,
        user_id=int(created["id"]),
        enabled=False,
        role=None,
        current_admin_password=None,
        actor=_admin_user(initialized_settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    user, failure_reason = authenticate_user(
        initialized_settings,
        "disabled-login-user",
        "family-password",
    )
    assert user is None
    assert failure_reason == "disabled"

    login_response = client.post(
        "/api/auth/login",
        json={"username": "disabled-login-user", "password": "family-password"},
    )
    assert login_response.status_code == 403
    assert login_response.json()["detail"] == "This account has been disabled"


def test_login_rate_limit_default_max_attempts_is_ten(initialized_settings) -> None:
    assert initialized_settings.login_max_attempts == 10
    assert initialized_settings.login_lockout_seconds == 600


def test_login_rate_limit_locks_same_client_bucket_after_tenth_failure_across_usernames(
    initialized_settings,
    client,
) -> None:
    _create_standard_user(initialized_settings, username="ethan")
    headers = _login_headers()

    for _ in range(5):
        response = client.post(
            "/api/auth/login",
            json={"username": initialized_settings.admin_username, "password": "wrong-password"},
            headers=headers,
        )
        assert response.status_code == 401

    for attempt in range(4):
        response = client.post(
            "/api/auth/login",
            json={"username": "ethan", "password": "wrong-password"},
            headers=headers,
        )
        assert response.status_code == 401, attempt

    tenth_response = client.post(
        "/api/auth/login",
        json={"username": "ethan", "password": "wrong-password"},
        headers=headers,
    )

    assert tenth_response.status_code == 429
    assert tenth_response.json()["detail"] == "Too many login attempts. Try again in 600 seconds."

    different_username_same_bucket = client.post(
        "/api/auth/login",
        json={"username": initialized_settings.admin_username, "password": "test-admin-password"},
        headers=headers,
    )
    assert different_username_same_bucket.status_code == 429
    assert different_username_same_bucket.json()["detail"] == "Too many login attempts from this IP. Try again in 600 seconds."


def test_login_rate_limit_private_browsing_simulation_with_same_ip_and_user_agent_is_still_locked(
    initialized_settings,
    client,
) -> None:
    headers = _login_headers(ip_address="203.0.113.20", user_agent="Pytest Private Browser 1.0")

    for attempt in range(10):
        response = client.post(
            "/api/auth/login",
            json={"username": initialized_settings.admin_username, "password": "wrong-password"},
            headers=headers,
        )
        expected_status = 429 if attempt == 9 else 401
        assert response.status_code == expected_status

    client.cookies.clear()
    retry_response = client.post(
        "/api/auth/login",
        json={"username": "someone-else", "password": "wrong-password"},
        headers=headers,
    )

    assert retry_response.status_code == 429
    assert retry_response.json()["detail"] == "Too many login attempts from this IP. Try again in 600 seconds."


def test_login_rate_limit_different_ip_is_not_blocked_but_same_ip_is_blocked(
    initialized_settings,
    client,
) -> None:
    blocked_headers = _login_headers(ip_address="203.0.113.30", user_agent="Pytest Device A")

    for attempt in range(10):
        response = client.post(
            "/api/auth/login",
            json={"username": initialized_settings.admin_username, "password": "wrong-password"},
            headers=blocked_headers,
        )
        expected_status = 429 if attempt == 9 else 401
        assert response.status_code == expected_status

    different_ip_response = client.post(
        "/api/auth/login",
        json={"username": initialized_settings.admin_username, "password": "wrong-password"},
        headers=_login_headers(ip_address="203.0.113.31", user_agent="Pytest Device A"),
    )
    assert different_ip_response.status_code == 401
    assert different_ip_response.json()["detail"] == "Invalid username or password"

    different_user_agent_response = client.post(
        "/api/auth/login",
        json={"username": initialized_settings.admin_username, "password": "wrong-password"},
        headers=_login_headers(ip_address="203.0.113.30", user_agent="Pytest Device B"),
    )
    assert different_user_agent_response.status_code == 429
    assert different_user_agent_response.json()["detail"] == "Too many login attempts from this IP. Try again in 600 seconds."


def test_successful_login_clears_client_bucket_failures(
    initialized_settings,
    client,
    admin_credentials,
) -> None:
    headers = _login_headers(ip_address="203.0.113.40", user_agent="Pytest Success Reset")

    for _ in range(9):
        response = client.post(
            "/api/auth/login",
            json={"username": initialized_settings.admin_username, "password": "wrong-password"},
            headers=headers,
        )
        assert response.status_code == 401

    success_response = client.post(
        "/api/auth/login",
        json=admin_credentials,
        headers=headers,
    )
    assert success_response.status_code == 200

    post_success_failure = client.post(
        "/api/auth/login",
        json={"username": initialized_settings.admin_username, "password": "wrong-password"},
        headers=headers,
    )
    assert post_success_failure.status_code == 401
    assert post_success_failure.json()["detail"] == "Invalid username or password"


def test_disabled_login_does_not_count_as_invalid_password_for_device_lockout(
    initialized_settings,
    client,
) -> None:
    created = _create_standard_user(initialized_settings, username="disabled-device-lockout")
    update_user(
        initialized_settings,
        user_id=int(created["id"]),
        enabled=False,
        role=None,
        current_admin_password=None,
        actor=_admin_user(initialized_settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    headers = _login_headers(ip_address="203.0.113.50", user_agent="Pytest Disabled Isolation")

    disabled_response = client.post(
        "/api/auth/login",
        json={"username": "disabled-device-lockout", "password": "family-password"},
        headers=headers,
    )
    assert disabled_response.status_code == 403
    assert disabled_response.json()["detail"] == "This account has been disabled"

    for _ in range(8):
        response = client.post(
            "/api/auth/login",
            json={"username": initialized_settings.admin_username, "password": "wrong-password"},
            headers=headers,
        )
        assert response.status_code == 401

    tenth_invalid = client.post(
        "/api/auth/login",
        json={"username": initialized_settings.admin_username, "password": "wrong-password"},
        headers=headers,
    )
    assert tenth_invalid.status_code == 429
    assert tenth_invalid.json()["detail"] == "Too many login attempts. Try again in 600 seconds."


def test_login_audit_log_distinguishes_invalid_credentials_and_device_rate_limited(
    initialized_settings,
    client,
) -> None:
    headers = _login_headers(ip_address="203.0.113.60", user_agent="Pytest Audit Device")

    first_failure = client.post(
        "/api/auth/login",
        json={"username": initialized_settings.admin_username, "password": "wrong-password"},
        headers=headers,
    )
    assert first_failure.status_code == 401

    for _ in range(8):
        response = client.post(
            "/api/auth/login",
            json={"username": "ethan", "password": "wrong-password"},
            headers=headers,
        )
        assert response.status_code == 401

    locked_response = client.post(
        "/api/auth/login",
        json={"username": "ethan", "password": "wrong-password"},
        headers=headers,
    )
    assert locked_response.status_code == 429

    latest_details = _recent_auth_login_details(initialized_settings, limit=12)
    reasons = [detail["reason"] for detail in latest_details if detail]

    assert "invalid_credentials" in reasons
    assert "ip_rate_limited" in reasons
    assert latest_details[0] == {
        "attempted_username": "ethan",
        "reason": "ip_rate_limited",
        "retry_after": 600,
    }


class TestSessionIdleTimeout:
    def test_session_accepts_request_within_idle_window(self, initialized_settings, client) -> None:
        user = _admin_user(initialized_settings)
        token = create_session(
            initialized_settings,
            user,
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
        seen_at = (datetime.now(timezone.utc) - timedelta(hours=23)).isoformat()
        with get_connection(initialized_settings) as connection:
            session_id = connection.execute(
                "SELECT id FROM sessions WHERE session_token_hash = ?",
                (_auth_session_hash(initialized_settings, token),),
            ).fetchone()["id"]
            connection.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
                (seen_at, session_id),
            )
            connection.commit()

        client.cookies.set(initialized_settings.session_cookie_name, token)
        response = client.get("/api/auth/me")

        assert response.status_code == 200
        with get_connection(initialized_settings) as connection:
            row = connection.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        assert row is not None

    def test_session_rejected_after_idle_timeout(self, initialized_settings, client) -> None:
        user = _admin_user(initialized_settings)
        token = create_session(
            initialized_settings,
            user,
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
        seen_at = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        with get_connection(initialized_settings) as connection:
            session_id = connection.execute(
                "SELECT id FROM sessions WHERE session_token_hash = ?",
                (_auth_session_hash(initialized_settings, token),),
            ).fetchone()["id"]
            connection.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
                (seen_at, session_id),
            )
            connection.commit()

        client.cookies.set(initialized_settings.session_cookie_name, token)
        response = client.get("/api/auth/me")

        assert response.status_code == 401
        with get_connection(initialized_settings) as connection:
            session_row = connection.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            event_row = connection.execute(
                """
                SELECT event_kind, details_json
                FROM security_events
                WHERE event_kind = 'session_revoked_idle'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        assert session_row is None
        assert event_row is not None
        assert json.loads(event_row["details_json"])["reason"] == "idle_timeout"

    def test_each_request_updates_last_seen_at(self, initialized_settings, client) -> None:
        user = _admin_user(initialized_settings)
        token = create_session(
            initialized_settings,
            user,
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
        previous_seen_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        with get_connection(initialized_settings) as connection:
            session_id = connection.execute(
                "SELECT id FROM sessions WHERE session_token_hash = ?",
                (_auth_session_hash(initialized_settings, token),),
            ).fetchone()["id"]
            connection.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
                (previous_seen_at, session_id),
            )
            connection.commit()

        client.cookies.set(initialized_settings.session_cookie_name, token)
        response = client.get("/api/auth/me")

        assert response.status_code == 200
        with get_connection(initialized_settings) as connection:
            row = connection.execute("SELECT last_seen_at FROM sessions WHERE id = ?", (session_id,)).fetchone()
        assert datetime.fromisoformat(row["last_seen_at"]) > datetime.fromisoformat(previous_seen_at)

    def test_session_with_null_last_seen_at_treated_as_expired(self, initialized_settings, client) -> None:
        user = _admin_user(initialized_settings)
        token = create_session(
            initialized_settings,
            user,
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
        with get_connection(initialized_settings) as connection:
            session_id = connection.execute(
                "SELECT id FROM sessions WHERE session_token_hash = ?",
                (_auth_session_hash(initialized_settings, token),),
            ).fetchone()["id"]
            connection.execute("UPDATE sessions SET last_seen_at = NULL WHERE id = ?", (session_id,))
            connection.commit()

        client.cookies.set(initialized_settings.session_cookie_name, token)
        response = client.get("/api/auth/me")

        assert response.status_code == 401
        with get_connection(initialized_settings) as connection:
            row = connection.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        assert row is None

    def test_absolute_ttl_safety_net_still_works(self, initialized_settings, client) -> None:
        user = _admin_user(initialized_settings)
        token = create_session(
            initialized_settings,
            user,
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
        now = datetime.now(timezone.utc)
        with get_connection(initialized_settings) as connection:
            session_id = connection.execute(
                "SELECT id FROM sessions WHERE session_token_hash = ?",
                (_auth_session_hash(initialized_settings, token),),
            ).fetchone()["id"]
            connection.execute(
                "UPDATE sessions SET expires_at = ?, last_seen_at = ? WHERE id = ?",
                ((now - timedelta(seconds=1)).isoformat(), now.isoformat(), session_id),
            )
            connection.commit()

        client.cookies.set(initialized_settings.session_cookie_name, token)
        response = client.get("/api/auth/me")

        assert response.status_code == 401


class TestAuthRateLimiting:
    def test_ip_lockout_after_10_failures(self, initialized_settings, client) -> None:
        headers = _login_headers(ip_address="203.0.113.70", user_agent="Pytest IP Lock")
        for attempt in range(10):
            response = client.post(
                "/api/auth/login",
                json={"username": initialized_settings.admin_username, "password": "wrong-password"},
                headers=headers,
            )
            assert response.status_code == (429 if attempt == 9 else 401)

        response = client.post(
            "/api/auth/login",
            json={"username": "somebody-else", "password": "wrong-password"},
            headers=headers,
        )

        assert response.status_code == 429
        with get_connection(initialized_settings) as connection:
            lockout = connection.execute(
                "SELECT 1 FROM login_lockouts WHERE bucket_kind = 'ip' AND bucket_key = '203.0.113.70'"
            ).fetchone()
            event = connection.execute(
                "SELECT 1 FROM security_events WHERE event_kind = 'login_lockout' LIMIT 1"
            ).fetchone()
        assert lockout is not None
        assert event is not None

    def test_username_lockout_after_50_failures_in_one_hour(self, initialized_settings, client) -> None:
        for attempt in range(50):
            response = client.post(
                "/api/auth/login",
                json={"username": initialized_settings.admin_username, "password": "wrong-password"},
                headers=_login_headers(ip_address=f"203.0.114.{attempt}", user_agent="Pytest Username Lock"),
            )
            assert response.status_code == (429 if attempt == 49 else 401)

        response = client.post(
            "/api/auth/login",
            json={"username": initialized_settings.admin_username, "password": "wrong-password"},
            headers=_login_headers(ip_address="203.0.115.1", user_agent="Pytest Username Lock"),
        )

        assert response.status_code == 429
        assert "username" in response.json()["detail"].lower()

    def test_successful_login_clears_both_limiters(self, initialized_settings, client, admin_credentials) -> None:
        headers = _login_headers(ip_address="203.0.113.80", user_agent="Pytest Clear Both")
        for _ in range(5):
            response = client.post(
                "/api/auth/login",
                json={"username": initialized_settings.admin_username, "password": "wrong-password"},
                headers=headers,
            )
            assert response.status_code == 401

        success = client.post("/api/auth/login", json=admin_credentials, headers=headers)
        assert success.status_code == 200

        for _ in range(5):
            response = client.post(
                "/api/auth/login",
                json={"username": initialized_settings.admin_username, "password": "wrong-password"},
                headers=headers,
            )
            assert response.status_code == 401

    def test_security_events_recorded_on_failure(self, initialized_settings, client) -> None:
        response = client.post(
            "/api/auth/login",
            json={"username": "missing-user", "password": "wrong-password"},
            headers=_login_headers(ip_address="203.0.113.90", user_agent="Pytest Security Failure"),
        )
        assert response.status_code == 401

        with get_connection(initialized_settings) as connection:
            row = connection.execute(
                """
                SELECT actor_username, ip_address, details_json
                FROM security_events
                WHERE event_kind = 'login_failure'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        assert row["actor_username"] == "missing-user"
        assert row["ip_address"] == "203.0.113.90"
        assert json.loads(row["details_json"])["reason"] == "user_not_found"

    def test_security_events_recorded_on_lockout(self, initialized_settings, client) -> None:
        headers = _login_headers(ip_address="203.0.113.91", user_agent="Pytest Security Lockout")
        for _ in range(10):
            client.post(
                "/api/auth/login",
                json={"username": initialized_settings.admin_username, "password": "wrong-password"},
                headers=headers,
            )

        with get_connection(initialized_settings) as connection:
            row = connection.execute(
                """
                SELECT details_json
                FROM security_events
                WHERE event_kind = 'login_lockout'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        assert row is not None
        assert json.loads(row["details_json"])["ip_lockout_seconds"] == 600

    def test_login_success_after_failures_event(self, initialized_settings, client, admin_credentials) -> None:
        headers = _login_headers(ip_address="203.0.113.92", user_agent="Pytest Success After Failure")
        for _ in range(3):
            response = client.post(
                "/api/auth/login",
                json={"username": initialized_settings.admin_username, "password": "wrong-password"},
                headers=headers,
            )
            assert response.status_code == 401

        success = client.post("/api/auth/login", json=admin_credentials, headers=headers)

        assert success.status_code == 200
        with get_connection(initialized_settings) as connection:
            row = connection.execute(
                """
                SELECT actor_username
                FROM security_events
                WHERE event_kind = 'login_success_after_failures'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        assert row is not None
        assert row["actor_username"] == initialized_settings.admin_username


def test_invite_code_is_hashed_one_time_and_required(initialized_settings) -> None:
    admin_user = _admin_user(initialized_settings)
    invite_payload = generate_invite_code(
        initialized_settings,
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
        assigned_age=13,
    )
    invite_code = str(invite_payload["code"])

    with get_connection(initialized_settings) as connection:
        row = connection.execute("SELECT code_hash FROM invite_codes WHERE id = ?", (invite_payload["id"],)).fetchone()
    assert row is not None
    assert row["code_hash"] != invite_code
    assert row["code_hash"].startswith(TOKEN_HASH_PREFIX)

    created_user = create_user_with_invite(
        initialized_settings,
        username="invited-user",
        password="family-password",
        confirm_password="family-password",
        invite_code=invite_code,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    assert created_user.username == "invited-user"
    assert created_user.age_credential == 13

    with pytest.raises(HTTPException) as reused_exc:
        create_user_with_invite(
            initialized_settings,
            username="second-user",
            password="family-password",
            confirm_password="family-password",
            invite_code=invite_code,
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
    assert reused_exc.value.status_code == 400

    with pytest.raises(HTTPException) as invalid_exc:
        create_user_with_invite(
            initialized_settings,
            username="third-user",
            password="family-password",
            confirm_password="family-password",
            invite_code="not-a-real-code",
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
    assert invalid_exc.value.status_code == 400


def _insert_legacy_invite_code(
    settings,
    *,
    code: str,
    assigned_age: int = 18,
    expires_at: str | None = None,
    used_at: str | None = None,
    revoked_at: str | None = None,
) -> int:
    now = utcnow_iso()
    resolved_expires_at = expires_at or (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    with get_connection(settings) as connection:
        cursor = connection.execute(
            """
            INSERT INTO invite_codes (
                code_hash,
                created_by_user_id,
                created_at,
                expires_at,
                assigned_age,
                used_at,
                used_by_user_id,
                revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _secret_hash(settings, "invite-code", code),
                1,
                now,
                resolved_expires_at,
                assigned_age,
                used_at,
                1 if used_at else None,
                revoked_at,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def test_legacy_invite_code_compatibility_respects_lifecycle(initialized_settings) -> None:
    valid_code = "LEGACY-INVITE!234"
    _insert_legacy_invite_code(initialized_settings, code=valid_code, assigned_age=13)

    created_user = create_user_with_invite(
        initialized_settings,
        username="legacy-invited-user",
        password="family-password",
        confirm_password="family-password",
        invite_code=valid_code,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    assert created_user.username == "legacy-invited-user"
    assert created_user.age_credential == 13
    with get_connection(initialized_settings) as connection:
        used_row = connection.execute(
            "SELECT used_at FROM invite_codes WHERE code_hash = ?",
            (_secret_hash(initialized_settings, "invite-code", valid_code),),
        ).fetchone()
    assert used_row is not None
    assert used_row["used_at"] is not None

    expired_code = "LEGACY-EXPIRED!234"
    _insert_legacy_invite_code(
        initialized_settings,
        code=expired_code,
        expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    )
    revoked_code = "LEGACY-REVOKED!234"
    _insert_legacy_invite_code(initialized_settings, code=revoked_code, revoked_at=utcnow_iso())
    used_code = "LEGACY-USED!234"
    _insert_legacy_invite_code(initialized_settings, code=used_code, used_at=utcnow_iso())

    for username, invite_code in (
        ("expired-legacy-user", expired_code),
        ("revoked-legacy-user", revoked_code),
        ("used-legacy-user", used_code),
    ):
        with pytest.raises(HTTPException) as exc_info:
            create_user_with_invite(
                initialized_settings,
                username=username,
                password="family-password",
                confirm_password="family-password",
                invite_code=invite_code,
                ip_address="127.0.0.1",
                user_agent="pytest",
            )
        assert exc_info.value.status_code == 400


def test_invite_code_defaults_assigned_age_to_adult(initialized_settings) -> None:
    invite_payload = generate_invite_code(
        initialized_settings,
        actor=_admin_user(initialized_settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    assert invite_payload["assigned_age"] == 18
    assert invite_payload["assigned_age_display"] == "18+"


def test_revoked_invite_code_cannot_create_user(initialized_settings) -> None:
    admin_user = _admin_user(initialized_settings)
    invite_payload = generate_invite_code(
        initialized_settings,
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    revoke_invite_code(
        initialized_settings,
        invite_id=int(invite_payload["id"]),
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            "SELECT revoked_at, hidden_at FROM invite_codes WHERE id = ?",
            (invite_payload["id"],),
        ).fetchone()

    assert row is not None
    assert row["revoked_at"] is not None
    assert row["hidden_at"] is not None

    with pytest.raises(HTTPException) as revoked_exc:
        create_user_with_invite(
            initialized_settings,
            username="revoked-invite-user",
            password="family-password",
            confirm_password="family-password",
            invite_code=str(invite_payload["code"]),
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
    assert revoked_exc.value.status_code == 400


def test_admin_invite_revoke_route_returns_clear_statuses(
    initialized_settings,
    client,
    admin_credentials,
) -> None:
    login_response = client.post("/api/auth/login", json=admin_credentials)
    assert login_response.status_code == 200
    admin_user = _admin_user(initialized_settings)

    invite_payload = generate_invite_code(
        initialized_settings,
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    revoke_response = client.post(f"/api/admin/invite-codes/{invite_payload['id']}/revoke")
    assert revoke_response.status_code == 200
    assert revoke_response.json()["message"] == "Invite code revoked"

    repeat_response = client.post(f"/api/admin/invite-codes/{invite_payload['id']}/revoke")
    assert repeat_response.status_code == 409
    assert "already revoked" in repeat_response.json()["detail"]

    missing_response = client.post("/api/admin/invite-codes/999999/revoke")
    assert missing_response.status_code == 404
    assert missing_response.json()["detail"] == "Invite code not found"

    expired_payload = generate_invite_code(
        initialized_settings,
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE invite_codes SET expires_at = ? WHERE id = ?",
            ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), expired_payload["id"]),
        )
        connection.commit()

    expired_response = client.post(f"/api/admin/invite-codes/{expired_payload['id']}/revoke")
    assert expired_response.status_code == 409
    assert "expired" in expired_response.json()["detail"]

    used_payload = generate_invite_code(
        initialized_settings,
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    create_user_with_invite(
        initialized_settings,
        username="used-invite-user",
        password="family-password",
        confirm_password="family-password",
        invite_code=str(used_payload["code"]),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    used_response = client.post(f"/api/admin/invite-codes/{used_payload['id']}/revoke")
    assert used_response.status_code == 409
    assert "already used" in used_response.json()["detail"]


def test_password_help_request_admin_details_include_request_metadata(
    initialized_settings,
    client,
    admin_credentials,
) -> None:
    _create_standard_user(initialized_settings, username="password-help-user")
    user_agent = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    )

    help_response = client.post(
        "/api/auth/password-help",
        json={"username": "password-help-user"},
        headers={"x-forwarded-for": "198.51.100.22", "user-agent": user_agent},
    )
    assert help_response.status_code == 200
    with get_connection(initialized_settings) as connection:
        stored_request = connection.execute(
            """
            SELECT requester_bucket_hash
            FROM password_help_requests
            WHERE username_snapshot = ?
            """,
            ("password-help-user",),
        ).fetchone()
    assert stored_request is not None
    assert stored_request["requester_bucket_hash"].startswith(TOKEN_HASH_PREFIX)
    assert "198.51.100.22" not in stored_request["requester_bucket_hash"]
    assert user_agent not in stored_request["requester_bucket_hash"]

    login_response = client.post("/api/auth/login", json=admin_credentials)
    assert login_response.status_code == 200

    list_response = client.get("/api/admin/password-help-requests")
    assert list_response.status_code == 200
    requests = list_response.json()["requests"]
    entry = next(row for row in requests if row["username_snapshot"] == "password-help-user")

    assert entry["requester_ip_address"] == "198.51.100.22"
    assert entry["requester_user_agent"] == user_agent

    dismiss_response = client.post(
        f"/api/admin/password-help-requests/{entry['id']}/dismiss",
        json={"confirm": True},
    )
    assert dismiss_response.status_code == 200
    assert dismiss_response.json()["message"] == "Password help request dismissed"


def test_legacy_password_help_request_admin_details_allow_unknown_metadata(
    initialized_settings,
    client,
    admin_credentials,
) -> None:
    created = _create_standard_user(initialized_settings, username="legacy-help-user")
    now = utcnow_iso()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with get_connection(initialized_settings) as connection:
        connection.execute(
            """
            INSERT INTO password_help_requests (
                username_snapshot,
                user_id,
                requester_bucket_hash,
                status,
                created_at,
                updated_at,
                expires_at
            ) VALUES (?, ?, ?, 'pending', ?, ?, ?)
            """,
            ("legacy-help-user", created["id"], "legacy-bucket", now, now, expires_at),
        )
        connection.commit()

    login_response = client.post("/api/auth/login", json=admin_credentials)
    assert login_response.status_code == 200

    list_response = client.get("/api/admin/password-help-requests")
    assert list_response.status_code == 200
    requests = list_response.json()["requests"]
    entry = next(row for row in requests if row["username_snapshot"] == "legacy-help-user")

    assert entry["requester_ip_address"] is None
    assert entry["requester_user_agent"] is None


def test_password_help_hmac_bucket_enforces_same_device_cooldown(initialized_settings) -> None:
    _create_standard_user(initialized_settings, username="help-device-user-a")
    _create_standard_user(initialized_settings, username="help-device-user-b")
    ip_address = "198.51.100.31"
    user_agent = "Pytest Password Help"

    create_password_help_request(
        initialized_settings,
        username="help-device-user-a",
        ip_address=ip_address,
        user_agent=user_agent,
    )
    create_password_help_request(
        initialized_settings,
        username="help-device-user-b",
        ip_address=ip_address,
        user_agent=user_agent,
    )

    with get_connection(initialized_settings) as connection:
        rows = connection.execute(
            """
            SELECT requester_bucket_hash
            FROM password_help_requests
            WHERE username_snapshot IN (?, ?)
            ORDER BY id
            """,
            ("help-device-user-a", "help-device-user-b"),
        ).fetchall()

    assert len(rows) == 1
    assert rows[0]["requester_bucket_hash"] == _requester_bucket_hash(
        initialized_settings,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    assert rows[0]["requester_bucket_hash"].startswith(TOKEN_HASH_PREFIX)


def test_legacy_password_help_bucket_enforces_cooldown_and_lazy_rehashes(initialized_settings) -> None:
    created = _create_standard_user(initialized_settings, username="legacy-help-device-user")
    _create_standard_user(initialized_settings, username="legacy-help-cooldown-user")
    ip_address = "198.51.100.44"
    user_agent = "Legacy Password Help"
    legacy_bucket_hash = _legacy_requester_bucket_hash(
        initialized_settings,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    now = utcnow_iso()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with get_connection(initialized_settings) as connection:
        connection.execute(
            """
            INSERT INTO password_help_requests (
                username_snapshot,
                user_id,
                requester_bucket_hash,
                requester_ip_address,
                requester_user_agent,
                status,
                created_at,
                updated_at,
                expires_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                "legacy-help-device-user",
                created["id"],
                legacy_bucket_hash,
                ip_address,
                user_agent,
                now,
                now,
                expires_at,
            ),
        )
        connection.commit()

    create_password_help_request(
        initialized_settings,
        username="legacy-help-cooldown-user",
        ip_address=ip_address,
        user_agent=user_agent,
    )

    with get_connection(initialized_settings) as connection:
        rows = connection.execute(
            """
            SELECT username_snapshot, requester_bucket_hash, requester_ip_address, requester_user_agent
            FROM password_help_requests
            WHERE username_snapshot IN (?, ?)
            ORDER BY id
            """,
            ("legacy-help-device-user", "legacy-help-cooldown-user"),
        ).fetchall()

    assert len(rows) == 1
    assert rows[0]["username_snapshot"] == "legacy-help-device-user"
    assert rows[0]["requester_bucket_hash"] == _requester_bucket_hash(
        initialized_settings,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    assert rows[0]["requester_ip_address"] == ip_address
    assert rows[0]["requester_user_agent"] == user_agent


def test_download_access_selected_movie_gate_and_revoke(initialized_settings) -> None:
    created = _create_standard_user(initialized_settings, username="download-user")
    media_item = _create_media_item(initialized_settings, relative_name="download-movie.mp4")
    admin_user = _admin_user(initialized_settings)
    with get_connection(initialized_settings) as connection:
        shared_local_source_id = ensure_current_shared_local_source_binding(
            initialized_settings,
            connection=connection,
        )
        connection.execute(
            """
            UPDATE media_items
            SET library_source_id = ?
            WHERE id = ?
            """,
            (shared_local_source_id, media_item["id"]),
        )
        connection.commit()

    assert is_item_download_allowed(
        initialized_settings,
        user_id=int(created["id"]),
        item_id=int(media_item["id"]),
    ) is False

    updated = update_download_access_for_user(
        initialized_settings,
        user_id=int(created["id"]),
        access_mode="selected",
        media_item_ids=[int(media_item["id"])],
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    assert updated["access_mode"] == "selected"
    assert get_download_access_for_user(initialized_settings, user_id=int(created["id"]))["selected_items"][0]["id"] == media_item["id"]
    assert is_item_download_allowed(
        initialized_settings,
        user_id=int(created["id"]),
        item_id=int(media_item["id"]),
    ) is True

    update_download_access_for_user(
        initialized_settings,
        user_id=int(created["id"]),
        access_mode="none",
        media_item_ids=[],
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    assert is_item_download_allowed(
        initialized_settings,
        user_id=int(created["id"]),
        item_id=int(media_item["id"]),
    ) is False


def test_admin_download_access_defaults_to_all_until_explicit_override(initialized_settings) -> None:
    admin_user = _admin_user(initialized_settings)
    media_item = _create_media_item(initialized_settings, relative_name="admin-download-default.mp4")
    with get_connection(initialized_settings) as connection:
        shared_local_source_id = ensure_current_shared_local_source_binding(
            initialized_settings,
            connection=connection,
        )
        connection.execute(
            "UPDATE media_items SET library_source_id = ? WHERE id = ?",
            (shared_local_source_id, media_item["id"]),
        )
        connection.commit()

    default_access = get_download_access_for_user(initialized_settings, user_id=admin_user.id)
    assert default_access["access_mode"] == "all"
    assert default_access["updated_at"] is None
    assert is_item_download_allowed(
        initialized_settings,
        user_id=admin_user.id,
        item_id=int(media_item["id"]),
    ) is True

    update_download_access_for_user(
        initialized_settings,
        user_id=admin_user.id,
        access_mode="none",
        media_item_ids=[],
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    explicit_access = get_download_access_for_user(initialized_settings, user_id=admin_user.id)
    assert explicit_access["access_mode"] == "none"
    assert explicit_access["updated_at"] is not None
    assert is_item_download_allowed(
        initialized_settings,
        user_id=admin_user.id,
        item_id=int(media_item["id"]),
    ) is False


def test_download_session_endpoint_authorizes_range_requests(initialized_settings, client) -> None:
    created = _create_standard_user(initialized_settings, username="range-download-user")
    media_item = _create_media_item(initialized_settings, relative_name="range-download.mp4")
    with get_connection(initialized_settings) as connection:
        shared_local_source_id = ensure_current_shared_local_source_binding(
            initialized_settings,
            connection=connection,
        )
        connection.execute(
            "UPDATE media_items SET library_source_id = ? WHERE id = ?",
            (shared_local_source_id, media_item["id"]),
        )
        connection.execute(
            "UPDATE media_items SET original_filename = ? WHERE id = ?",
            ("nested/family movie?.mp4", media_item["id"]),
        )
        connection.commit()
    update_download_access_for_user(
        initialized_settings,
        user_id=int(created["id"]),
        access_mode="selected",
        media_item_ids=[int(media_item["id"])],
        actor=_admin_user(initialized_settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    _session_user, token = _issue_user_session(
        initialized_settings,
        username="range-download-user",
        password="family-password",
    )
    client.cookies.set(initialized_settings.session_cookie_name, token)

    session_response = client.post(f"/api/download/item/{media_item['id']}/session")
    assert session_response.status_code == 200
    session_payload = session_response.json()
    download_url = session_payload["download_url"]
    assert session_payload["download_filename"] == "family movie?.mp4"
    assert session_payload["original_filename"] == "family movie?.mp4"
    assert session_payload["session_token"] not in session_payload["controlled_download_url"]

    range_response = client.get(download_url, headers={"Range": "bytes=0-3"})
    assert range_response.status_code == 206
    assert range_response.headers["accept-ranges"] == "bytes"
    assert range_response.headers["content-disposition"] == "attachment; filename*=UTF-8''family%20movie%3F.mp4"
    assert range_response.content == b"not "


def test_download_failure_audit_detail_redacts_token_bearing_urls(initialized_settings) -> None:
    _created, _media_item, session_user, _auth_token, session_payload = _create_authorized_download_session(
        initialized_settings,
        username="download-audit-redaction-user",
    )
    raw_token = str(session_payload["session_token"])
    raw_url = str(session_payload["download_url"])
    mark_download_session_failed(
        initialized_settings,
        token=raw_token,
        user=session_user,
        message=f"browser failed while fetching {raw_url}?token={raw_token}",
    )

    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT details_json
            FROM audit_logs
            WHERE action = 'download.failed'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row is not None
    details_json = str(row["details_json"])
    details = json.loads(details_json)
    assert details == {"message": "redacted_sensitive_download_detail"}
    assert raw_token not in details_json
    assert raw_url not in details_json
    assert "/api/download/sessions/" not in details_json


def test_download_session_new_token_hash_uses_hmac_and_validates(initialized_settings) -> None:
    _created, media_item, session_user, _auth_token, session_payload = _create_authorized_download_session(
        initialized_settings,
        username="download-hmac-new",
    )
    raw_token = str(session_payload["session_token"])

    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            "SELECT session_token_hash FROM download_sessions WHERE id = ?",
            (session_payload["session_id"],),
        ).fetchone()

    assert row is not None
    stored_hash = str(row["session_token_hash"])
    assert stored_hash.startswith(TOKEN_HASH_PREFIX)
    assert raw_token not in stored_hash
    assert stored_hash != _secret_hash(initialized_settings, "download-session", raw_token)
    assert (
        validate_download_session(initialized_settings, token=raw_token, user=session_user)
        == int(media_item["id"])
    )
    assert is_download_session_still_authorized(
        initialized_settings,
        token=raw_token,
        user=session_user,
        session_id=int(session_payload["session_id"]),
    )


def test_legacy_download_session_validates_and_lazy_rehashes(initialized_settings) -> None:
    created = _create_standard_user(initialized_settings, username="legacy-download-valid")
    media_item = _create_media_item(initialized_settings, relative_name="legacy-download-valid.mp4")
    _grant_download_for_item(
        initialized_settings,
        user_id=int(created["id"]),
        media_item_id=int(media_item["id"]),
    )
    session_user, _auth_token = _issue_user_session(
        initialized_settings,
        username="legacy-download-valid",
        password="family-password",
    )
    raw_token = "legacy-download-token-valid"
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    row_id, legacy_hash = _insert_legacy_download_session(
        initialized_settings,
        token=raw_token,
        user=session_user,
        media_item_id=int(media_item["id"]),
        expires_at=expires_at,
    )

    assert validate_download_session(initialized_settings, token=raw_token, user=session_user) == int(media_item["id"])

    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT session_token_hash, expires_at, auth_session_id, auth_session_required,
                   completed_at, failed_at, revoked_at
            FROM download_sessions
            WHERE id = ?
            """,
            (row_id,),
        ).fetchone()

    assert row is not None
    assert str(row["session_token_hash"]).startswith(TOKEN_HASH_PREFIX)
    assert row["session_token_hash"] != legacy_hash
    assert row["expires_at"] == expires_at
    assert int(row["auth_session_id"]) == int(session_user.session_id)
    assert int(row["auth_session_required"]) == 1
    assert row["completed_at"] is None
    assert row["failed_at"] is None
    assert row["revoked_at"] is None


def test_legacy_download_session_rehash_still_supports_completion_failure_and_termination(
    initialized_settings,
) -> None:
    created = _create_standard_user(initialized_settings, username="legacy-download-ops")
    media_item = _create_media_item(initialized_settings, relative_name="legacy-download-ops.mp4")
    _grant_download_for_item(
        initialized_settings,
        user_id=int(created["id"]),
        media_item_id=int(media_item["id"]),
    )
    session_user, _auth_token = _issue_user_session(
        initialized_settings,
        username="legacy-download-ops",
        password="family-password",
    )
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

    operations = (
        ("complete", mark_download_session_completed, "completed_at", None),
        ("failed", mark_download_session_failed, "failed_at", "client_failed"),
        ("terminated", mark_download_session_terminated, "revoked_at", None),
    )
    for suffix, operation, timestamp_column, message in operations:
        raw_token = f"legacy-download-token-{suffix}"
        row_id, _legacy_hash = _insert_legacy_download_session(
            initialized_settings,
            token=raw_token,
            user=session_user,
            media_item_id=int(media_item["id"]),
            expires_at=expires_at,
        )
        assert (
            validate_download_session(initialized_settings, token=raw_token, user=session_user)
            == int(media_item["id"])
        )
        if message is None:
            operation(initialized_settings, token=raw_token, user=session_user, session_id=row_id)
        else:
            operation(
                initialized_settings,
                token=raw_token,
                user=session_user,
                session_id=row_id,
                message=message,
            )
        with get_connection(initialized_settings) as connection:
            row = connection.execute(
                f"SELECT session_token_hash, {timestamp_column}, last_error FROM download_sessions WHERE id = ?",
                (row_id,),
            ).fetchone()
        assert row is not None
        assert str(row["session_token_hash"]).startswith(TOKEN_HASH_PREFIX)
        assert row[timestamp_column] is not None
        if suffix == "failed":
            assert row["last_error"] == "client_failed"
        if suffix == "terminated":
            assert row["last_error"] == "download_terminated"


def test_expired_legacy_download_session_is_rejected_without_rehash(initialized_settings) -> None:
    created = _create_standard_user(initialized_settings, username="legacy-download-expired")
    media_item = _create_media_item(initialized_settings, relative_name="legacy-download-expired.mp4")
    _grant_download_for_item(
        initialized_settings,
        user_id=int(created["id"]),
        media_item_id=int(media_item["id"]),
    )
    session_user, _auth_token = _issue_user_session(
        initialized_settings,
        username="legacy-download-expired",
        password="family-password",
    )
    raw_token = "legacy-download-token-expired"
    row_id, legacy_hash = _insert_legacy_download_session(
        initialized_settings,
        token=raw_token,
        user=session_user,
        media_item_id=int(media_item["id"]),
        expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    )

    with pytest.raises(HTTPException) as exc:
        validate_download_session(initialized_settings, token=raw_token, user=session_user)

    assert exc.value.status_code == 403
    with get_connection(initialized_settings) as connection:
        row = connection.execute("SELECT session_token_hash FROM download_sessions WHERE id = ?", (row_id,)).fetchone()
    assert row is not None
    assert row["session_token_hash"] == legacy_hash


@pytest.mark.parametrize(
    ("status_column", "status_value"),
    (
        ("completed_at", "2026-01-01T00:00:00+00:00"),
        ("failed_at", "2026-01-01T00:00:00+00:00"),
        ("revoked_at", "2026-01-01T00:00:00+00:00"),
    ),
)
def test_inactive_legacy_download_session_is_rejected_without_rehash(
    initialized_settings,
    status_column,
    status_value,
) -> None:
    created = _create_standard_user(initialized_settings, username=f"legacy-download-{status_column}")
    media_item = _create_media_item(initialized_settings, relative_name=f"legacy-download-{status_column}.mp4")
    _grant_download_for_item(
        initialized_settings,
        user_id=int(created["id"]),
        media_item_id=int(media_item["id"]),
    )
    session_user, _auth_token = _issue_user_session(
        initialized_settings,
        username=f"legacy-download-{status_column}",
        password="family-password",
    )
    raw_token = f"legacy-download-token-{status_column}"
    kwargs = {status_column: status_value}
    row_id, legacy_hash = _insert_legacy_download_session(
        initialized_settings,
        token=raw_token,
        user=session_user,
        media_item_id=int(media_item["id"]),
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        **kwargs,
    )

    with pytest.raises(HTTPException) as exc:
        validate_download_session(initialized_settings, token=raw_token, user=session_user)

    assert exc.value.status_code == 403
    with get_connection(initialized_settings) as connection:
        row = connection.execute("SELECT session_token_hash FROM download_sessions WHERE id = ?", (row_id,)).fetchone()
    assert row is not None
    assert row["session_token_hash"] == legacy_hash


def test_download_session_rejects_user_below_movie_age_requirement(initialized_settings, client) -> None:
    admin_user = _admin_user(initialized_settings)
    created = _create_standard_user(initialized_settings, username="underage-download-user")
    update_user(
        initialized_settings,
        user_id=int(created["id"]),
        enabled=None,
        role=None,
        age_credential=12,
        current_admin_password=None,
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    media_item = _create_media_item(initialized_settings, relative_name="age-gated-download.mp4")
    _grant_download_for_item(
        initialized_settings,
        user_id=int(created["id"]),
        media_item_id=int(media_item["id"]),
    )
    set_media_age_requirement(
        initialized_settings,
        item_id=int(media_item["id"]),
        age_requirement=16,
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    _session_user, token = _issue_user_session(
        initialized_settings,
        username="underage-download-user",
        password="family-password",
    )
    client.cookies.set(initialized_settings.session_cookie_name, token)

    response = client.post(f"/api/download/item/{media_item['id']}/session")

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "You must be 16 years old to view this film. "
        "Please contact an admin if your age credentials are incorrect."
    )


def test_download_session_validation_rechecks_age_after_requirement_change(initialized_settings) -> None:
    admin_user = _admin_user(initialized_settings)
    created = _create_standard_user(initialized_settings, username="download-age-recheck-user")
    media_item = _create_media_item(initialized_settings, relative_name="age-recheck-download.mp4")
    _grant_download_for_item(
        initialized_settings,
        user_id=int(created["id"]),
        media_item_id=int(media_item["id"]),
    )
    session_user, auth_token = _issue_user_session(
        initialized_settings,
        username="download-age-recheck-user",
        password="family-password",
    )
    session_payload = create_download_session(
        initialized_settings,
        user=session_user,
        item_id=int(media_item["id"]),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    update_user(
        initialized_settings,
        user_id=int(created["id"]),
        enabled=None,
        role=None,
        age_credential=12,
        current_admin_password=None,
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    set_media_age_requirement(
        initialized_settings,
        item_id=int(media_item["id"]),
        age_requirement=16,
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    refreshed_session_user = get_user_by_session_token(initialized_settings, auth_token)
    assert refreshed_session_user is not None

    with pytest.raises(HTTPException) as exc:
        validate_download_session(
            initialized_settings,
            token=str(session_payload["session_token"]),
            user=refreshed_session_user,
        )

    assert exc.value.status_code == 403
    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            "SELECT revoked_at, failed_at, last_error FROM download_sessions WHERE session_token_hash IS NOT NULL",
        ).fetchone()
    assert row["failed_at"] is not None
    assert row["last_error"] == "age_requirement_changed"


def test_download_session_rejects_captured_token_for_other_user_and_revoked_auth(initialized_settings, client) -> None:
    alice = _create_standard_user(initialized_settings, username="download-alice")
    _create_standard_user(initialized_settings, username="download-bob")
    media_item = _create_media_item(initialized_settings, relative_name="captured-token.mp4")
    with get_connection(initialized_settings) as connection:
        shared_local_source_id = ensure_current_shared_local_source_binding(
            initialized_settings,
            connection=connection,
        )
        connection.execute(
            "UPDATE media_items SET library_source_id = ? WHERE id = ?",
            (shared_local_source_id, media_item["id"]),
        )
        connection.commit()
    update_download_access_for_user(
        initialized_settings,
        user_id=int(alice["id"]),
        access_mode="selected",
        media_item_ids=[int(media_item["id"])],
        actor=_admin_user(initialized_settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    _alice_user, alice_token = _issue_user_session(
        initialized_settings,
        username="download-alice",
        password="family-password",
    )
    client.cookies.set(initialized_settings.session_cookie_name, alice_token)

    session_response = client.post(f"/api/download/item/{media_item['id']}/session")
    assert session_response.status_code == 200
    session_payload = session_response.json()

    _bob_user, bob_token = _issue_user_session(
        initialized_settings,
        username="download-bob",
        password="family-password",
    )
    client.cookies.set(initialized_settings.session_cookie_name, bob_token)
    replay_response = client.get(session_payload["download_url"], headers={"Range": "bytes=0-3"})
    assert replay_response.status_code == 403
    controlled_replay_response = client.get(
        session_payload["controlled_download_url"],
        headers={
            "Range": "bytes=0-3",
            "X-Elvern-Download-Token": session_payload["session_token"],
        },
    )
    assert controlled_replay_response.status_code == 403

    client.cookies.set(initialized_settings.session_cookie_name, alice_token)
    destroy_session(initialized_settings, alice_token)
    revoked_session_response = client.get(session_payload["download_url"], headers={"Range": "bytes=0-3"})
    assert revoked_session_response.status_code in {401, 403}


def test_download_session_cloud_response_uses_sanitized_original_filename(initialized_settings, client, monkeypatch) -> None:
    created = _create_standard_user(initialized_settings, username="cloud-download-name")
    media_item = _create_media_item(initialized_settings, relative_name="cloud-download-name.mp4")
    with get_connection(initialized_settings) as connection:
        shared_local_source_id = ensure_current_shared_local_source_binding(
            initialized_settings,
            connection=connection,
        )
        connection.execute(
            "UPDATE media_items SET library_source_id = ?, original_filename = ? WHERE id = ?",
            (shared_local_source_id, "cloud/family cloud movie.mkv", media_item["id"]),
        )
        connection.commit()
    update_download_access_for_user(
        initialized_settings,
        user_id=int(created["id"]),
        access_mode="selected",
        media_item_ids=[int(media_item["id"])],
        actor=_admin_user(initialized_settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    _session_user, token = _issue_user_session(
        initialized_settings,
        username="cloud-download-name",
        password="family-password",
    )
    client.cookies.set(initialized_settings.session_cookie_name, token)

    def fake_cloud_response(*args, **kwargs):
        return Response(
            content=b"cloud",
            headers={"Content-Disposition": "attachment; filename*=UTF-8''movie"},
            media_type="application/octet-stream",
        )

    monkeypatch.setattr("backend.app.routes.download.build_cloud_stream_response", fake_cloud_response)

    session_response = client.post(f"/api/download/item/{media_item['id']}/session")
    assert session_response.status_code == 200
    response = client.get(session_response.json()["download_url"])
    assert response.status_code == 200
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''family%20cloud%20movie.mkv"


def test_download_session_rejects_after_grant_revoked(initialized_settings, client) -> None:
    created = _create_standard_user(initialized_settings, username="revoked-download-grant")
    media_item = _create_media_item(initialized_settings, relative_name="grant-revoked.mp4")
    with get_connection(initialized_settings) as connection:
        shared_local_source_id = ensure_current_shared_local_source_binding(
            initialized_settings,
            connection=connection,
        )
        connection.execute(
            "UPDATE media_items SET library_source_id = ? WHERE id = ?",
            (shared_local_source_id, media_item["id"]),
        )
        connection.commit()
    update_download_access_for_user(
        initialized_settings,
        user_id=int(created["id"]),
        access_mode="selected",
        media_item_ids=[int(media_item["id"])],
        actor=_admin_user(initialized_settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    _session_user, token = _issue_user_session(
        initialized_settings,
        username="revoked-download-grant",
        password="family-password",
    )
    client.cookies.set(initialized_settings.session_cookie_name, token)
    session_response = client.post(f"/api/download/item/{media_item['id']}/session")
    assert session_response.status_code == 200

    update_download_access_for_user(
        initialized_settings,
        user_id=int(created["id"]),
        access_mode="none",
        media_item_ids=[],
        actor=_admin_user(initialized_settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    range_response = client.get(session_response.json()["download_url"], headers={"Range": "bytes=0-3"})
    assert range_response.status_code == 403


def test_download_session_terminate_revokes_stream_authorization(initialized_settings, client) -> None:
    created = _create_standard_user(initialized_settings, username="terminate-download-user")
    media_item = _create_media_item(initialized_settings, relative_name="terminate-download.mp4")
    with get_connection(initialized_settings) as connection:
        shared_local_source_id = ensure_current_shared_local_source_binding(
            initialized_settings,
            connection=connection,
        )
        connection.execute(
            "UPDATE media_items SET library_source_id = ? WHERE id = ?",
            (shared_local_source_id, media_item["id"]),
        )
        connection.commit()
    update_download_access_for_user(
        initialized_settings,
        user_id=int(created["id"]),
        access_mode="selected",
        media_item_ids=[int(media_item["id"])],
        actor=_admin_user(initialized_settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    _session_user, token = _issue_user_session(
        initialized_settings,
        username="terminate-download-user",
        password="family-password",
    )
    client.cookies.set(initialized_settings.session_cookie_name, token)

    session_response = client.post(f"/api/download/item/{media_item['id']}/session")
    assert session_response.status_code == 200
    session_payload = session_response.json()

    terminate_response = client.post(
        f"/api/download/sessions/{session_payload['session_token']}/terminate",
    )
    assert terminate_response.status_code == 200
    assert terminate_response.json()["message"] == "Download terminated"

    range_response = client.get(session_payload["download_url"], headers={"Range": "bytes=0-3"})
    assert range_response.status_code == 403
    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT revoked_at, last_error
            FROM download_sessions
            WHERE media_item_id = ? AND user_id = ?
            """,
            (media_item["id"], created["id"]),
        ).fetchone()
    assert row is not None
    assert row["revoked_at"] is not None
    assert row["last_error"] == "download_terminated"


def test_download_session_rejects_after_parent_auth_session_deleted(initialized_settings) -> None:
    _created, _media_item, session_user, _auth_token, session_payload = _create_authorized_download_session(
        initialized_settings,
        username="download-parent-deleted",
    )
    with get_connection(initialized_settings) as connection:
        connection.execute("DELETE FROM sessions WHERE id = ?", (session_user.session_id,))
        connection.commit()

    with pytest.raises(HTTPException) as exc:
        validate_download_session(
            initialized_settings,
            token=str(session_payload["session_token"]),
            user=session_user,
        )
    assert exc.value.status_code == 403


def test_download_session_rejects_after_logout_revokes_session(initialized_settings) -> None:
    _created, _media_item, session_user, auth_token, session_payload = _create_authorized_download_session(
        initialized_settings,
        username="download-parent-logout",
    )
    destroy_session(initialized_settings, auth_token)

    with pytest.raises(HTTPException) as exc:
        validate_download_session(
            initialized_settings,
            token=str(session_payload["session_token"]),
            user=session_user,
        )
    assert exc.value.status_code == 403
    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            "SELECT revoked_at, last_error FROM download_sessions WHERE id = ?",
            (session_payload["session_id"],),
        ).fetchone()
    assert row["revoked_at"] is not None
    assert row["last_error"] == "auth_session_logout"


def test_download_session_rejects_after_url_prefix_rotation(initialized_settings) -> None:
    _created, _media_item, session_user, _auth_token, session_payload = _create_authorized_download_session(
        initialized_settings,
        username="download-prefix-rotated",
    )
    with get_connection(initialized_settings) as connection:
        rotate_url_prefix(initialized_settings, connection, actor_user_id=1, actor_username="admin")
        connection.commit()

    with pytest.raises(HTTPException) as exc:
        validate_download_session(
            initialized_settings,
            token=str(session_payload["session_token"]),
            user=session_user,
        )
    assert exc.value.status_code == 403
    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            "SELECT revoked_at, last_error FROM download_sessions WHERE id = ?",
            (session_payload["session_id"],),
        ).fetchone()
    assert row["revoked_at"] is not None
    assert row["last_error"] == "url_prefix_rotated"


def test_download_session_url_redaction_hides_token() -> None:
    raw = "/api/download/sessions/super-secret-token-123?download=1"
    assert redact_download_session_urls(raw) == "/api/download/sessions/[redacted]?download=1"


def test_admin_delete_user_revokes_sessions_and_blocks_last_enabled_admin(initialized_settings) -> None:
    created = _create_standard_user(initialized_settings, username="delete-user")
    _session_user, token = _issue_user_session(
        initialized_settings,
        username="delete-user",
        password="family-password",
    )

    with pytest.raises(HTTPException) as password_exc:
        delete_user(
            initialized_settings,
            user_id=int(created["id"]),
            confirm=True,
            current_admin_password="wrong-password",
            actor=_admin_user(initialized_settings),
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
    assert password_exc.value.status_code == 401
    assert get_user_by_session_token(initialized_settings, token) is not None

    deleted = delete_user(
        initialized_settings,
        user_id=int(created["id"]),
        confirm=True,
        current_admin_password=initialized_settings.admin_bootstrap_password or "",
        actor=_admin_user(initialized_settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    assert deleted["username"] == "delete-user"
    assert get_user_by_session_token(initialized_settings, token) is None

    with pytest.raises(HTTPException) as self_exc:
        delete_user(
            initialized_settings,
            user_id=_admin_user(initialized_settings).id,
            confirm=True,
            current_admin_password=initialized_settings.admin_bootstrap_password or "",
            actor=_admin_user(initialized_settings),
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
    assert self_exc.value.status_code == 400
