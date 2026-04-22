from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PureWindowsPath
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.app.auth import authenticate_user, destroy_session
from backend.app.db import get_connection, utcnow_iso
from backend.app.services.admin_service import create_user, update_user


IOS_SAFARI_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)
DESKTOP_CHROME_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)


class RouteTranscodeManagerStub:
    def __init__(self, *, status: str = "queued") -> None:
        self._snapshot = {
            "manifest_ready": False,
            "expected_duration_seconds": 120.0,
            "generated_duration_seconds": None,
            "manifest_complete": False,
            "status": status,
            "enabled": True,
            "last_error": None,
        }

    def start(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def get_job_snapshot(self, item: dict[str, object]) -> dict[str, object]:
        return dict(self._snapshot)


def _admin_user(settings):
    user, failure_reason = authenticate_user(
        settings,
        settings.admin_username,
        settings.admin_bootstrap_password or "",
    )
    assert failure_reason is None
    assert user is not None
    return user


def _login(client, *, username: str, password: str) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    session_token = response.cookies.get(client.app.state.settings.session_cookie_name)
    assert session_token
    return session_token


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


def _create_media_item_record(
    settings,
    *,
    relative_name: str,
    video_codec: str | None = "h264",
    audio_codec: str | None = "aac",
) -> dict[str, object]:
    media_file = Path(settings.media_root) / relative_name
    media_file.parent.mkdir(parents=True, exist_ok=True)
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
                f"Route Test {media_file.stem}",
                media_file.name,
                str(media_file),
                media_file.stat().st_size,
                media_file.stat().st_mtime,
                120.0,
                None,
                None,
                video_codec,
                audio_codec,
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
        "file_path": str(media_file),
        "relative_name": relative_name,
    }


@pytest.mark.parametrize(
    ("target_app", "expected_scheme", "expected_path", "expects_callbacks"),
    [
        ("infuse", "infuse", "/play", True),
        ("vlc", "vlc-x-callback", "/stream", False),
    ],
)
def test_native_external_launch_route_redirects_for_supported_targets(
    initialized_settings,
    client,
    admin_credentials,
    monkeypatch,
    target_app: str,
    expected_scheme: str,
    expected_path: str,
    expects_callbacks: bool,
) -> None:
    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings: ([], []),
    )
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    item = _create_media_item_record(
        initialized_settings,
        relative_name=f"native-launch/{target_app}.mp4",
    )

    response = client.get(
        f"/api/native-playback/{item['id']}/launch/{target_app}",
        headers={"user-agent": IOS_SAFARI_USER_AGENT},
        follow_redirects=False,
    )

    assert response.status_code == 302
    launch_url = response.headers["location"]
    parsed_launch = urlsplit(launch_url)
    params = parse_qs(parsed_launch.query)
    assert parsed_launch.scheme == expected_scheme
    assert parsed_launch.netloc == "x-callback-url"
    assert parsed_launch.path == expected_path

    stream_url = params["url"][0]
    parsed_stream = urlsplit(stream_url)
    assert parsed_stream.scheme == "http"
    assert parsed_stream.netloc == "testserver"
    assert parsed_stream.path.startswith("/api/native-playback/session/")
    assert parsed_stream.path.endswith("/stream")
    assert "token" in parse_qs(parsed_stream.query)

    if expects_callbacks:
        assert params["x-success"][0].endswith(
            f"/library/{item['id']}?ios_app={target_app}&ios_result=success"
        )
        assert params["x-error"][0].endswith(
            f"/library/{item['id']}?ios_app={target_app}&ios_result=error"
        )
    else:
        assert "x-success" not in params
        assert "x-error" not in params


def test_native_external_launch_route_rejects_unsupported_target(client, admin_credentials) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    response = client.get(
        "/api/native-playback/999/launch/mpv",
        headers={"user-agent": IOS_SAFARI_USER_AGENT},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported external playback target"


def test_desktop_playback_route_returns_linux_same_host_direct_path(initialized_settings, client, admin_credentials) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    item = _create_media_item_record(
        initialized_settings,
        relative_name="desktop/linux-direct.mp4",
    )
    client.app.state.settings = replace(initialized_settings, vlc_path_linux="/usr/bin/vlc")

    response = client.get(
        f"/api/desktop-playback/{item['id']}",
        params={"platform": "linux", "same_host": "true"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["platform"] == "linux"
    assert payload["strategy"] == "direct_path"
    assert payload["vlc_target"] == item["file_path"]
    assert payload["open_method"] == "spawn_vlc"
    assert payload["same_host_launch"] is True
    assert payload["used_backend_fallback"] is False


def test_desktop_playback_route_returns_mapped_windows_path_when_configured(
    initialized_settings,
    client,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    item = _create_media_item_record(
        initialized_settings,
        relative_name="desktop/windows-map.mp4",
    )
    client.app.state.settings = replace(initialized_settings, library_root_windows=r"Z:\Family Media")

    response = client.get(
        f"/api/desktop-playback/{item['id']}",
        params={"platform": "windows"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["platform"] == "windows"
    assert payload["strategy"] == "direct_path"
    assert payload["vlc_target"] == str(PureWindowsPath(r"Z:\Family Media").joinpath("desktop", "windows-map.mp4"))
    assert payload["same_host_launch"] is False
    assert payload["used_backend_fallback"] is False


def test_desktop_playback_route_returns_backend_fallback_when_mapping_is_missing(
    initialized_settings,
    client,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    item = _create_media_item_record(
        initialized_settings,
        relative_name="desktop/windows-fallback.mp4",
    )
    client.app.state.settings = replace(initialized_settings, library_root_windows=None)

    response = client.get(
        f"/api/desktop-playback/{item['id']}",
        params={"platform": "windows"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["platform"] == "windows"
    assert payload["strategy"] == "backend_url"
    assert payload["used_backend_fallback"] is True
    assert "short-lived backend URL" in payload["vlc_target"]
    assert any("Windows VLC mapping is not configured yet" in note for note in payload["notes"])


def test_playback_decision_route_returns_direct_for_safe_desktop_browser(
    initialized_settings,
    client,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    item = _create_media_item_record(
        initialized_settings,
        relative_name="browser/direct-safe.mp4",
    )
    client.app.state.transcode_manager = RouteTranscodeManagerStub()

    response = client.get(
        f"/api/playback/{item['id']}",
        headers={"user-agent": DESKTOP_CHROME_USER_AGENT},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "direct"
    assert payload["client_profile"] == "chromium"
    assert payload["direct_url"] == f"/api/stream/{item['id']}"
    assert payload["hls_url"] is None
    assert payload["reason"] == "Safe direct-play profile for desktop browsers"


def test_playback_decision_route_returns_hls_for_iphone_safari_without_audio_metadata(
    initialized_settings,
    client,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    item = _create_media_item_record(
        initialized_settings,
        relative_name="browser/iphone-fallback.mp4",
        audio_codec=None,
    )
    client.app.state.transcode_manager = RouteTranscodeManagerStub(status="queued")

    response = client.get(
        f"/api/playback/{item['id']}",
        headers={"user-agent": IOS_SAFARI_USER_AGENT},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "hls"
    assert payload["client_profile"] == "iphone_safari"
    assert payload["direct_url"] == f"/api/stream/{item['id']}"
    assert payload["hls_url"] == f"/api/hls/{item['id']}/index.m3u8"
    assert payload["transcode_status"] == "queued"
    assert payload["reason"] == "Missing audio metadata; choosing conservative HLS fallback for iPhone Safari"


@pytest.mark.parametrize("invalidation_mode", ["session_revoked", "user_disabled"])
def test_native_playback_details_route_fails_immediately_after_revoke_or_disable(
    initialized_settings,
    client,
    monkeypatch,
    invalidation_mode: str,
) -> None:
    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings: ([], []),
    )
    created_user = _create_standard_user(
        initialized_settings,
        username=f"playback-route-{invalidation_mode}",
    )
    session_token = _login(
        client,
        username=str(created_user["username"]),
        password="family-password",
    )
    item = _create_media_item_record(
        initialized_settings,
        relative_name=f"native-session/{invalidation_mode}.mp4",
    )

    create_response = client.post(
        f"/api/native-playback/{item['id']}/session",
        headers={"user-agent": IOS_SAFARI_USER_AGENT},
    )
    assert create_response.status_code == 200
    created_session = create_response.json()

    details_before_invalidation = client.get(
        f"/api/native-playback/session/{created_session['session_id']}",
        params={"token": created_session["access_token"]},
    )
    assert details_before_invalidation.status_code == 200
    assert details_before_invalidation.json()["session_id"] == created_session["session_id"]

    if invalidation_mode == "session_revoked":
        destroy_session(initialized_settings, session_token)
    else:
        update_user(
            initialized_settings,
            user_id=int(created_user["id"]),
            enabled=False,
            role=None,
            current_admin_password=None,
            actor=_admin_user(initialized_settings),
            ip_address="127.0.0.1",
            user_agent="pytest",
        )

    details_after_invalidation = client.get(
        f"/api/native-playback/session/{created_session['session_id']}",
        params={"token": created_session["access_token"]},
    )
    assert details_after_invalidation.status_code == 401
    assert details_after_invalidation.json()["detail"] == "Native playback session is invalid or has expired"
