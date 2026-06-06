from __future__ import annotations

import inspect
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


def test_url_token_redaction_uses_linear_scanner() -> None:
    cases = {
        "/x.m3u8?token=secret": "/x.m3u8?token=[redacted]",
        "/x?access_token=secret&quality=hd": "/x?access_token=[redacted]&quality=hd",
        "/x?quality=hd&sig=secret": "/x?quality=hd&sig=[redacted]",
        "/x?quality=hd&api_key=secret": "/x?quality=hd&api_key=[redacted]",
        "/x?quality=hd&authorization=secret": "/x?quality=hd&authorization=[redacted]",
        "/x?quality=hd&token=abc&name=test&sig=xyz": (
            "/x?quality=hd&token=[redacted]&name=test&sig=[redacted]"
        ),
        "/x?monkey=banana": "/x?monkey=[redacted]",
        "/x?quality=hd&name=test": "/x?quality=hd&name=test",
        "/x?sig&key&token": "/x?sig&key&token",
        "plain string without query": "plain string without query",
    }

    for value, expected in cases.items():
        assert debug_routes._redact_url_tokens(value) == expected


def test_url_token_redaction_handles_large_malformed_strings() -> None:
    malicious = "/x?" + "&".join(
        f"sig{i}keytokenwithoutassignment" for i in range(10_000)
    )

    redacted = debug_routes._redact_url_tokens(malicious)

    assert redacted == malicious


def test_diagnostic_string_truncation_only_applies_above_generous_limit() -> None:
    exact_limit = "a" * debug_routes.MAX_DIAGNOSTIC_STRING_LENGTH
    over_limit = f"{exact_limit}b"

    assert debug_routes._redact_diagnostic_payload({"message": exact_limit})["message"] == exact_limit
    redacted = debug_routes._redact_diagnostic_payload({"message": over_limit})["message"]
    assert redacted == f"{exact_limit}[truncated]"


def test_diagnostic_depth_limit_caps_abnormal_nesting() -> None:
    nested: object = "leaf"
    for _ in range(debug_routes.MAX_DIAGNOSTIC_DEPTH + 2):
        nested = {"child": nested}

    redacted = debug_routes._redact_diagnostic_payload(nested)
    cursor = redacted
    for _ in range(debug_routes.MAX_DIAGNOSTIC_DEPTH + 1):
        assert isinstance(cursor, dict)
        cursor = cursor["child"]

    assert cursor == "[truncated: max depth exceeded]"


def test_large_collections_are_truncated_and_do_not_break_saving(
    client,
    admin_credentials,
    tmp_path,
    monkeypatch,
) -> None:
    diagnostics_dir = tmp_path / "playback-diagnostics"
    monkeypatch.setattr(debug_routes, "PLAYBACK_DIAGNOSTICS_DIR", diagnostics_dir)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    diagnostics = {
        **_diagnostics_payload(),
        "large_list": list(range(debug_routes.MAX_DIAGNOSTIC_LIST_ITEMS + 1)),
        "large_dict": {
            f"item_{index}": index
            for index in range(debug_routes.MAX_DIAGNOSTIC_DICT_ITEMS + 1)
        },
    }

    response = client.post(
        "/api/debug/playback-diagnostics",
        json={
            "diagnostic_source": "playback_debug_panel",
            "label": "large collections",
            "diagnostics": diagnostics,
        },
    )

    assert response.status_code == 200
    saved_payload = json.loads(Path(response.json()["saved_path"]).read_text(encoding="utf-8"))
    redacted = saved_payload["diagnostics"]

    assert len(redacted["large_list"]) == debug_routes.MAX_DIAGNOSTIC_LIST_ITEMS + 1
    assert redacted["large_list"][-1] == "[truncated: 1 additional items omitted]"
    assert len(redacted["large_dict"]) == debug_routes.MAX_DIAGNOSTIC_DICT_ITEMS + 1
    assert redacted["large_dict"]["[truncated]"] == "[truncated: 1 additional items omitted]"
    json.dumps(redacted)


def test_debug_route_no_longer_uses_resub_for_url_token_redaction() -> None:
    source = inspect.getsource(debug_routes._redact_url_tokens)

    assert "re.sub" not in source
    assert "[^=\\s" not in source


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
