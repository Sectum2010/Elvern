from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.auth import (
    authenticate_user,
    create_session,
    destroy_session,
    get_session_access_failure_reason,
    get_user_by_session_token,
)
from backend.app.db import get_connection, utcnow_iso
from backend.app.security import hash_session_token
from backend.app.services.admin_service import create_user, update_user
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


def _login_headers(*, ip_address: str = "203.0.113.10", user_agent: str = "Pytest Browser 1.0") -> dict[str, str]:
    return {
        "x-forwarded-for": ip_address,
        "user-agent": user_agent,
    }


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
    assert row["session_token_hash"] == hash_session_token(token, initialized_settings.session_secret)
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
    assert row["session_token_hash"] == hash_session_token(token, initialized_settings.session_secret)
    assert row["session_token_hash"] != token
    assert row["revoked_at"] is not None
    assert row["revoked_reason"] == "logout"
    assert get_user_by_session_token(initialized_settings, token) is None
    assert get_session_access_failure_reason(initialized_settings, token) == "revoked"


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
        lambda file_path, settings: ([], []),
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
        lambda file_path, settings: ([], []),
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

    destroy_session(initialized_settings, token)

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
        lambda file_path, settings: ([], []),
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
        lambda file_path, settings: ([], []),
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
        lambda file_path, settings: ([], []),
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
        lambda file_path, settings: ([], []),
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
        lambda file_path, settings: ([], []),
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
        lambda file_path, settings: ([], []),
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
        lambda file_path, settings: ([], []),
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
        lambda file_path, settings: ([], []),
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
        lambda file_path, settings: ([], []),
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
    assert tenth_response.json()["detail"] == "Too many login attempts from this device. Try again in 600 seconds."

    different_username_same_bucket = client.post(
        "/api/auth/login",
        json={"username": initialized_settings.admin_username, "password": "test-admin-password"},
        headers=headers,
    )
    assert different_username_same_bucket.status_code == 429
    assert different_username_same_bucket.json()["detail"] == "Too many login attempts from this device. Try again in 600 seconds."


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
    assert retry_response.json()["detail"] == "Too many login attempts from this device. Try again in 600 seconds."


def test_login_rate_limit_different_client_bucket_is_not_blocked(
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
    assert different_user_agent_response.status_code == 401
    assert different_user_agent_response.json()["detail"] == "Invalid username or password"


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

    for _ in range(9):
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
    assert tenth_invalid.json()["detail"] == "Too many login attempts from this device. Try again in 600 seconds."


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
    assert "device_rate_limited" in reasons
    assert latest_details[0] == {
        "attempted_username": "ethan",
        "reason": "device_rate_limited",
        "retry_after": 600,
    }
