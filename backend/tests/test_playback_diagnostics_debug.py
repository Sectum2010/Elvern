from __future__ import annotations

import json
from pathlib import Path

from backend.app.routes import debug as debug_routes


def _login(client, *, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _logout(client) -> None:
    response = client.post("/api/auth/logout")
    assert response.status_code == 200


def _create_standard_user(client, *, username: str, password: str) -> None:
    response = client.post(
        "/api/admin/users",
        json={
            "username": username,
            "password": password,
            "role": "standard_user",
            "enabled": True,
        },
    )
    assert response.status_code == 200


def _diagnostics_payload() -> dict[str, object]:
    return {
        "item": {
            "id": 1264,
            "title": "Pacific Rim Uprising",
        },
        "platform": {
            "detectedClientPlatform": "desktop",
            "detectedDesktopPlatform": "windows",
        },
        "hls_engine": {
            "selectedEngine": "hls.js",
            "nativeHlsSupport": "",
            "hlsJsSupported": True,
            "hlsJsVersion": "1.6.15",
        },
        "video": {
            "duration": 16.099,
            "currentSrc": "http://testserver/api/mobile-playback/session/abc/manifest.m3u8?token=secret-token",
        },
        "time_ranges": {
            "seekable": [{"start": 0, "end": 16.099}],
            "buffered": [{"start": 0.1, "end": 15.9}],
        },
        "manifest": {
            "playlist_type": "EVENT",
            "classification": "event_open",
            "contains_endlist": False,
        },
        "headers": {
            "cookie": "session-cookie",
            "authorization": "Bearer secret",
        },
    }


def test_admin_can_save_gated_playback_diagnostics(
    client,
    admin_credentials,
    tmp_path,
    monkeypatch,
) -> None:
    diagnostics_dir = tmp_path / "playback-diagnostics"
    monkeypatch.setattr(debug_routes, "PLAYBACK_DIAGNOSTICS_DIR", diagnostics_dir)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    response = client.post(
        "/api/debug/playback-diagnostics",
        json={
            "diagnostic_source": "playback_debug_panel",
            "label": "Windows Capture",
            "diagnostics": _diagnostics_payload(),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["saved"] is True
    assert payload["label"] == "Windows Capture"
    assert payload["detected_desktop_platform"] == "windows"
    assert payload["selected_engine"] == "hls.js"
    assert payload["video_duration"] == 16.099
    assert payload["manifest_classification"] == "event_open"
    saved_path = Path(payload["saved_path"])
    assert saved_path.parent == diagnostics_dir
    assert saved_path.name.startswith("windows-capture-windows-")
    assert saved_path.is_file()

    saved_payload = json.loads(saved_path.read_text(encoding="utf-8"))
    assert saved_payload["diagnostic_source"] == "playback_debug_panel"
    assert saved_payload["label"] == "Windows Capture"
    assert "token=[redacted]" in saved_payload["diagnostics"]["video"]["currentSrc"]
    assert saved_payload["diagnostics"]["headers"]["cookie"] == "[redacted]"
    assert saved_payload["diagnostics"]["headers"]["authorization"] == "[redacted]"


def test_playback_diagnostics_requires_debug_panel_gate_and_label(
    client,
    admin_credentials,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(debug_routes, "PLAYBACK_DIAGNOSTICS_DIR", tmp_path / "playback-diagnostics")
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    missing_gate = client.post(
        "/api/debug/playback-diagnostics",
        json={
            "label": "mac-safari",
            "diagnostics": _diagnostics_payload(),
        },
    )
    assert missing_gate.status_code == 400

    missing_label = client.post(
        "/api/debug/playback-diagnostics",
        json={
            "diagnostic_source": "playback_debug_panel",
            "label": "",
            "diagnostics": _diagnostics_payload(),
        },
    )
    assert missing_label.status_code == 400

    missing_diagnostics = client.post(
        "/api/debug/playback-diagnostics",
        json={
            "diagnostic_source": "playback_debug_panel",
            "label": "mac-safari",
            "diagnostics": "not-an-object",
        },
    )
    assert missing_diagnostics.status_code == 400


def test_standard_user_cannot_save_playback_diagnostics(
    client,
    admin_credentials,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(debug_routes, "PLAYBACK_DIAGNOSTICS_DIR", tmp_path / "playback-diagnostics")
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _create_standard_user(client, username="viewer", password="viewer-password")
    _logout(client)
    _login(client, username="viewer", password="viewer-password")

    response = client.post(
        "/api/debug/playback-diagnostics",
        json={
            "diagnostic_source": "playback_debug_panel",
            "label": "windows",
            "diagnostics": _diagnostics_payload(),
        },
    )

    assert response.status_code == 403
