from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PureWindowsPath
from urllib.parse import parse_qs, urlsplit

from backend.app.routes.native_playback import _build_ios_external_launch_url
from backend.app.services.desktop_playback_service import build_desktop_playback_resolution
from backend.app.services.playback_service import build_playback_decision


class StubTranscodeManager:
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

    def get_job_snapshot(self, item: dict[str, object]) -> dict[str, object]:
        return dict(self._snapshot)


def _make_local_item(
    settings,
    *,
    item_id: int,
    relative_name: str,
    video_codec: str | None = "h264",
    audio_codec: str | None = "aac",
) -> dict[str, object]:
    media_file = Path(settings.media_root) / relative_name
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(b"not a real media file")
    return {
        "id": item_id,
        "title": f"Playback Contract {item_id}",
        "file_path": str(media_file),
        "source_kind": "local",
        "duration_seconds": 120.0,
        "container": "mp4",
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "resume_position_seconds": 18.5,
        "subtitles": [],
    }


def test_ios_external_launch_url_contract_for_infuse_and_vlc() -> None:
    stream_url = "https://media.example/api/native-playback/session/demo/stream?token=secret"
    success_url = "https://app.example/library/42?ios_app=infuse&ios_result=success"
    error_url = "https://app.example/library/42?ios_app=infuse&ios_result=error"

    infuse_url = _build_ios_external_launch_url(
        app="infuse",
        stream_url=stream_url,
        success_url=success_url,
        error_url=error_url,
    )
    parsed_infuse = urlsplit(infuse_url)
    infuse_params = parse_qs(parsed_infuse.query)
    assert parsed_infuse.scheme == "infuse"
    assert parsed_infuse.netloc == "x-callback-url"
    assert parsed_infuse.path == "/play"
    assert infuse_params["url"] == [stream_url]
    assert infuse_params["x-success"] == [success_url]
    assert infuse_params["x-error"] == [error_url]

    vlc_url = _build_ios_external_launch_url(
        app="vlc",
        stream_url=stream_url,
        success_url=success_url,
        error_url=error_url,
    )
    parsed_vlc = urlsplit(vlc_url)
    vlc_params = parse_qs(parsed_vlc.query)
    assert parsed_vlc.scheme == "vlc-x-callback"
    assert parsed_vlc.netloc == "x-callback-url"
    assert parsed_vlc.path == "/stream"
    assert vlc_params["url"] == [stream_url]
    assert "x-success" not in vlc_params
    assert "x-error" not in vlc_params


def test_native_external_launch_rejects_unsupported_target(client, admin_credentials) -> None:
    login_response = client.post("/api/auth/login", json=admin_credentials)
    assert login_response.status_code == 200

    response = client.get("/api/native-playback/999/launch/mpv")

    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported external playback target"


def test_desktop_resolution_prefers_linux_same_host_direct_path_when_available(initialized_settings) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=101,
        relative_name="movies/linux-direct.mp4",
    )
    settings = replace(initialized_settings, vlc_path_linux="/usr/bin/vlc")

    resolution = build_desktop_playback_resolution(
        settings,
        item=item,
        platform="linux",
        same_host=True,
    )

    assert resolution["strategy"] == "direct_path"
    assert resolution["vlc_target"] == item["file_path"]
    assert resolution["open_method"] == "spawn_vlc"
    assert resolution["same_host_launch"] is True
    assert resolution["used_backend_fallback"] is False


def test_desktop_resolution_same_host_linux_uses_actual_local_file_not_configured_library_root(initialized_settings) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=104,
        relative_name="movies/linux-direct-real-path.mp4",
    )
    settings = replace(
        initialized_settings,
        vlc_path_linux="/usr/bin/vlc",
        library_root_linux="/home/sectum/Videos/Movies",
    )

    resolution = build_desktop_playback_resolution(
        settings,
        item=item,
        platform="linux",
        same_host=True,
    )

    assert resolution["strategy"] == "direct_path"
    assert resolution["vlc_target"] == item["file_path"]
    assert resolution["same_host_launch"] is True
    assert resolution["used_backend_fallback"] is False


def test_desktop_resolution_prefers_mapped_windows_path_before_backend_fallback(initialized_settings) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=102,
        relative_name="movies/windows-map.mp4",
    )
    settings = replace(initialized_settings, library_root_windows=r"Z:\Family Media")

    resolution = build_desktop_playback_resolution(
        settings,
        item=item,
        platform="windows",
        same_host=False,
    )

    assert resolution["strategy"] == "direct_path"
    assert resolution["vlc_target"] == str(PureWindowsPath(r"Z:\Family Media").joinpath("movies", "windows-map.mp4"))
    assert resolution["same_host_launch"] is False
    assert resolution["used_backend_fallback"] is False


def test_desktop_resolution_falls_back_to_backend_url_when_no_desktop_mapping_exists(initialized_settings) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=103,
        relative_name="movies/windows-fallback.mp4",
    )
    settings = replace(initialized_settings, library_root_windows=None)

    resolution = build_desktop_playback_resolution(
        settings,
        item=item,
        platform="windows",
        same_host=False,
    )

    assert resolution["strategy"] == "backend_url"
    assert resolution["used_backend_fallback"] is True
    assert resolution["same_host_launch"] is False
    assert "short-lived backend URL" in resolution["vlc_target"]
    assert any("Windows VLC mapping is not configured yet" in note for note in resolution["notes"])


def test_playback_decision_keeps_browser_direct_play_for_safe_desktop_mp4(initialized_settings) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=201,
        relative_name="browser/direct-safe.mp4",
    )

    decision = build_playback_decision(
        initialized_settings,
        item,
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/123.0 Safari/537.36",
        transcode_manager=StubTranscodeManager(),
    )

    assert decision["mode"] == "direct"
    assert decision["client_profile"] == "chromium"
    assert decision["direct_url"] == f"/api/stream/{item['id']}"
    assert decision["hls_url"] is None
    assert decision["reason"] == "Safe direct-play profile for desktop browsers"


def test_playback_decision_uses_browser_fallback_for_iphone_safari_without_audio_metadata(initialized_settings) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=202,
        relative_name="browser/iphone-fallback.mp4",
        audio_codec=None,
    )

    decision = build_playback_decision(
        initialized_settings,
        item,
        user_agent=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
        ),
        transcode_manager=StubTranscodeManager(status="queued"),
    )

    assert decision["mode"] == "hls"
    assert decision["client_profile"] == "iphone_safari"
    assert decision["direct_url"] == f"/api/stream/{item['id']}"
    assert decision["hls_url"] == f"/api/hls/{item['id']}/index.m3u8"
    assert decision["transcode_status"] == "queued"
    assert decision["reason"] == "Missing audio metadata; choosing conservative HLS fallback for iPhone Safari"
