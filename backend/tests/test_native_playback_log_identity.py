from __future__ import annotations

from dataclasses import replace
import inspect
import json
import logging
from pathlib import Path
import string
import subprocess

from backend.app import cli as app_cli
from backend.app.services import native_playback_service
from backend.app.services.log_identity_service import (
    local_media_path_log_fingerprint,
    native_session_log_fingerprint,
    redacted_url_log_label,
    safe_url_origin_label,
    safe_url_path_label,
    stable_log_fingerprint,
    token_url_log_fingerprint,
)


def test_native_playback_session_log_fingerprint_is_stable_and_non_reversible() -> None:
    session_id = "raw-native-session-id-12345"

    first = native_session_log_fingerprint(session_id)
    second = native_session_log_fingerprint(session_id)
    different = native_session_log_fingerprint("different-native-session-id")

    assert first == second
    assert first != different
    assert len(first) == 12
    assert all(character in string.hexdigits for character in first)
    assert session_id not in first
    assert native_session_log_fingerprint("  ") == "unknown"
    assert stable_log_fingerprint(session_id, namespace="other-namespace") != first


def test_local_media_path_log_fingerprint_is_stable_and_non_reversible() -> None:
    raw_path = "/srv/private-library/Secret Family Movie.mkv"

    first = local_media_path_log_fingerprint(raw_path)
    second = local_media_path_log_fingerprint(raw_path)
    different = local_media_path_log_fingerprint("/srv/private-library/Other Movie.mkv")

    assert first == second
    assert first != different
    assert len(first) == 12
    assert all(character in string.hexdigits for character in first)
    assert raw_path not in first
    assert Path(raw_path).name not in first
    assert local_media_path_log_fingerprint("  ") == "unknown"


def test_safe_origin_log_label_keeps_only_scheme_and_host_port() -> None:
    assert native_playback_service._safe_origin_log_label(
        "https://example.com/path?token=secret#fragment"
    ) == "https://example.com"
    assert native_playback_service._safe_origin_log_label(
        "http://127.0.0.1:8000"
    ) == "http://127.0.0.1:8000"
    assert native_playback_service._safe_origin_log_label(
        "http://[::1]:8000/foo?token=secret"
    ) == "http://[::1]:8000"
    assert native_playback_service._safe_origin_log_label("not-a-url") == "unknown"
    assert native_playback_service._safe_origin_log_label("") == "unknown"
    assert native_playback_service._safe_origin_log_label(None) == "unknown"


def test_safe_token_url_log_helpers_omit_query_tokens_and_dynamic_ids() -> None:
    raw_url = "https://elvern.example/api/native-playback/session/raw-session-id/stream?token=secret-token"

    assert safe_url_origin_label(raw_url) == "https://elvern.example"
    assert safe_url_path_label(raw_url) == "/api/native-playback/session/{session_id}/stream"
    assert token_url_log_fingerprint(raw_url) == token_url_log_fingerprint(raw_url)
    assert token_url_log_fingerprint(raw_url) != token_url_log_fingerprint(raw_url.replace("secret-token", "other"))

    label = redacted_url_log_label(raw_url)
    assert "origin=https://elvern.example" in label
    assert "path=/api/native-playback/session/{session_id}/stream" in label
    assert "has_query=True" in label
    assert "secret-token" not in label
    assert "raw-session-id" not in label
    assert "token=" not in label


def test_safe_custom_protocol_log_helpers_omit_query_tokens() -> None:
    raw_url = "elvern-vlc://play?api=https%3A%2F%2Felvern.example&handoff=raw-handoff&token=secret-token"

    assert safe_url_origin_label(raw_url) == "elvern-vlc://play"
    assert safe_url_path_label(raw_url) == "play"

    label = redacted_url_log_label(raw_url)
    assert "origin=elvern-vlc://play" in label
    assert "path=play" in label
    assert "has_query=True" in label
    assert "secret-token" not in label
    assert "raw-handoff" not in label
    assert "api=" not in label


def test_safe_download_url_path_labels_omit_tokens() -> None:
    assert (
        safe_url_path_label("https://elvern.example/api/download/sessions/raw-token/complete?token=other")
        == "/api/download/sessions/{token}/complete"
    )
    assert (
        safe_url_path_label("https://elvern.example/api/download/session-stream/42/failed")
        == "/api/download/session-stream/{session_id}/failed"
    )


def test_cli_native_session_fingerprint_prints_json_without_raw_session_or_settings(
    monkeypatch,
    capsys,
) -> None:
    raw_session_id = "raw-native-session-id-for-cli"

    def _unexpected_settings_call():
        raise AssertionError("native-session-fingerprint must not load settings")

    def _unexpected_init_db_call(settings):
        del settings
        raise AssertionError("native-session-fingerprint must not initialize the database")

    monkeypatch.setattr("sys.argv", ["backend.app.cli", "native-session-fingerprint", raw_session_id])
    monkeypatch.setattr(app_cli, "refresh_settings", _unexpected_settings_call)
    monkeypatch.setattr(app_cli, "init_db", _unexpected_init_db_call)

    app_cli.main()

    stdout = capsys.readouterr().out
    assert raw_session_id not in stdout
    assert json.loads(stdout) == {
        "session_fingerprint": native_session_log_fingerprint(raw_session_id),
    }


def test_cli_native_session_fingerprint_prompts_without_echo_when_argument_omitted(
    monkeypatch,
    capsys,
) -> None:
    raw_session_id = "raw-native-session-id-from-prompt"
    prompts: list[str] = []

    def _fake_getpass(prompt: str) -> str:
        prompts.append(prompt)
        return raw_session_id

    def _unexpected_settings_call():
        raise AssertionError("native-session-fingerprint must not load settings")

    monkeypatch.setattr("sys.argv", ["backend.app.cli", "native-session-fingerprint"])
    monkeypatch.setattr(app_cli.getpass, "getpass", _fake_getpass)
    monkeypatch.setattr(app_cli, "refresh_settings", _unexpected_settings_call)

    app_cli.main()

    stdout = capsys.readouterr().out
    assert prompts == ["Native session id: "]
    assert raw_session_id not in stdout
    assert json.loads(stdout) == {
        "session_fingerprint": native_session_log_fingerprint(raw_session_id),
    }


def test_cli_media_path_fingerprint_prints_json_without_raw_path_or_settings(
    monkeypatch,
    capsys,
) -> None:
    raw_path = "/srv/private-library/Secret Family Movie.mkv"

    def _unexpected_settings_call():
        raise AssertionError("media-path-fingerprint must not load settings")

    def _unexpected_init_db_call(settings):
        del settings
        raise AssertionError("media-path-fingerprint must not initialize the database")

    monkeypatch.setattr("sys.argv", ["backend.app.cli", "media-path-fingerprint", raw_path])
    monkeypatch.setattr(app_cli, "refresh_settings", _unexpected_settings_call)
    monkeypatch.setattr(app_cli, "init_db", _unexpected_init_db_call)

    app_cli.main()

    stdout = capsys.readouterr().out
    assert raw_path not in stdout
    assert Path(raw_path).name not in stdout
    assert json.loads(stdout) == {
        "path_fingerprint": local_media_path_log_fingerprint(raw_path),
    }


def test_cli_media_path_fingerprint_prompts_without_echo_when_argument_omitted(
    monkeypatch,
    capsys,
) -> None:
    raw_path = "/srv/private-library/Prompt Secret Movie.mkv"
    prompts: list[str] = []

    def _fake_getpass(prompt: str) -> str:
        prompts.append(prompt)
        return raw_path

    def _unexpected_settings_call():
        raise AssertionError("media-path-fingerprint must not load settings")

    monkeypatch.setattr("sys.argv", ["backend.app.cli", "media-path-fingerprint"])
    monkeypatch.setattr(app_cli.getpass, "getpass", _fake_getpass)
    monkeypatch.setattr(app_cli, "refresh_settings", _unexpected_settings_call)

    app_cli.main()

    stdout = capsys.readouterr().out
    assert prompts == ["Media path: "]
    assert raw_path not in stdout
    assert Path(raw_path).name not in stdout
    assert json.loads(stdout) == {
        "path_fingerprint": local_media_path_log_fingerprint(raw_path),
    }


def test_probe_tracks_oserror_log_uses_path_fingerprint_without_raw_path(
    initialized_settings,
    monkeypatch,
    caplog,
) -> None:
    settings = replace(initialized_settings, ffprobe_path="/usr/bin/ffprobe")
    raw_path = settings.media_root / "private-folder" / "Secret Family Movie.mkv"
    expected_fingerprint = local_media_path_log_fingerprint(raw_path)

    def _raise_oserror(*args, **kwargs):
        del args, kwargs
        raise OSError(f"cannot open {raw_path}")

    monkeypatch.setattr(native_playback_service.subprocess, "run", _raise_oserror)

    with caplog.at_level(logging.WARNING, logger="backend.app.services.native_playback_service"):
        audio_tracks, subtitle_tracks = native_playback_service._probe_tracks(
            raw_path,
            settings,
            media_item_id=701,
        )

    assert audio_tracks == []
    assert subtitle_tracks == []
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "ffprobe track probe failed item=701" in log_text
    assert f"path_fingerprint={expected_fingerprint}" in log_text
    assert "error_type=OSError" in log_text
    assert str(raw_path) not in log_text
    assert raw_path.name not in log_text
    assert "cannot open" not in log_text


def test_probe_tracks_nonzero_log_uses_path_fingerprint_without_raw_path(
    initialized_settings,
    monkeypatch,
    caplog,
) -> None:
    settings = replace(initialized_settings, ffprobe_path="/usr/bin/ffprobe")
    raw_path = settings.media_root / "private-folder" / "Secret Nonzero Movie.mkv"
    expected_fingerprint = local_media_path_log_fingerprint(raw_path)

    def _completed_nonzero(command, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(
            command,
            7,
            stdout="",
            stderr=f"failed to inspect {raw_path}",
        )

    monkeypatch.setattr(native_playback_service.subprocess, "run", _completed_nonzero)

    with caplog.at_level(logging.WARNING, logger="backend.app.services.native_playback_service"):
        audio_tracks, subtitle_tracks = native_playback_service._probe_tracks(
            raw_path,
            settings,
            media_item_id=702,
        )

    assert audio_tracks == []
    assert subtitle_tracks == []
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "ffprobe track probe exited returncode=7 item=702" in log_text
    assert f"path_fingerprint={expected_fingerprint}" in log_text
    assert str(raw_path) not in log_text
    assert raw_path.name not in log_text
    assert "failed to inspect" not in log_text


def test_probe_tracks_invalid_json_log_uses_path_fingerprint_without_raw_path(
    initialized_settings,
    monkeypatch,
    caplog,
) -> None:
    settings = replace(initialized_settings, ffprobe_path="/usr/bin/ffprobe")
    raw_path = settings.media_root / "private-folder" / "Secret Invalid Json Movie.mkv"
    expected_fingerprint = local_media_path_log_fingerprint(raw_path)

    def _completed_invalid_json(command, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(command, 0, stdout="{not-json", stderr=str(raw_path))

    monkeypatch.setattr(native_playback_service.subprocess, "run", _completed_invalid_json)

    with caplog.at_level(logging.WARNING, logger="backend.app.services.native_playback_service"):
        audio_tracks, subtitle_tracks = native_playback_service._probe_tracks(
            raw_path,
            settings,
            media_item_id=703,
        )

    assert audio_tracks == []
    assert subtitle_tracks == []
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "ffprobe track probe returned invalid JSON item=703" in log_text
    assert f"path_fingerprint={expected_fingerprint}" in log_text
    assert str(raw_path) not in log_text
    assert raw_path.name not in log_text


def test_probe_tracks_success_still_returns_audio_and_subtitle_tracks(
    initialized_settings,
    monkeypatch,
) -> None:
    settings = replace(initialized_settings, ffprobe_path="/usr/bin/ffprobe")
    raw_path = settings.media_root / "successful-probe.mkv"

    def _completed_success(command, **kwargs):
        del kwargs
        assert command[-1] == str(raw_path)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "streams": [
                        {
                            "index": 2,
                            "codec_type": "audio",
                            "codec_name": "aac",
                            "channels": 6,
                            "tags": {"language": "eng", "title": "English 5.1"},
                            "disposition": {"default": 1},
                        },
                        {
                            "index": 3,
                            "codec_type": "subtitle",
                            "codec_name": "subrip",
                            "tags": {"language": "eng", "title": "English"},
                            "disposition": {"default": 0},
                        },
                    ],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(native_playback_service.subprocess, "run", _completed_success)

    audio_tracks, subtitle_tracks = native_playback_service._probe_tracks(
        raw_path,
        settings,
        media_item_id=704,
    )

    assert audio_tracks == [
        {
            "index": 2,
            "codec": "aac",
            "language": "eng",
            "title": "English 5.1",
            "channels": 6,
            "disposition_default": True,
        }
    ]
    assert subtitle_tracks == [
        {
            "index": 3,
            "codec": "subrip",
            "language": "eng",
            "title": "English",
            "channels": None,
            "disposition_default": False,
        }
    ]


def test_build_session_payload_still_includes_probe_tracks_for_local_items(
    initialized_settings,
    monkeypatch,
) -> None:
    settings = replace(initialized_settings, ffprobe_path="/usr/bin/ffprobe")
    raw_path = settings.media_root / "payload-probe.mkv"

    def _completed_success(command, **kwargs):
        del kwargs
        assert command[-1] == str(raw_path)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "streams": [
                        {
                            "index": 1,
                            "codec_type": "audio",
                            "codec_name": "eac3",
                            "channels": 8,
                            "tags": {"language": "eng", "title": "Atmos"},
                            "disposition": {"default": 1},
                        },
                        {
                            "index": 4,
                            "codec_type": "subtitle",
                            "codec_name": "ass",
                            "tags": {"language": "jpn", "title": "Signs"},
                            "disposition": {"default": 0},
                        },
                    ],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(native_playback_service.subprocess, "run", _completed_success)

    payload = native_playback_service._build_session_payload(
        settings,
        session_id="native-session-for-payload-probe",
        access_token="access-token-for-payload-probe",
        include_access_token=True,
        row={
            "media_item_id": 705,
            "file_path": str(raw_path),
            "source_kind": "local",
            "subtitles": [],
            "expires_at": "2026-06-06T00:00:00+00:00",
            "title": "Payload Probe",
            "duration_seconds": 120.0,
            "resume_seconds": 0.0,
            "container": "mkv",
            "video_codec": "h264",
            "audio_codec": "eac3",
        },
    )

    assert payload["audio_tracks"][0]["index"] == 1
    assert payload["audio_tracks"][0]["codec"] == "eac3"
    assert payload["subtitle_tracks"][0]["index"] == 4
    assert payload["subtitle_tracks"][0]["codec"] == "ass"
    assert payload["stream_url"].endswith(
        "/api/native-playback/session/native-session-for-payload-probe/stream?token=access-token-for-payload-probe"
    )


def test_close_native_playback_session_log_uses_fingerprint_static_guard() -> None:
    source = inspect.getsource(native_playback_service.close_native_playback_session)
    logger_block = source.split("logger.info(", maxsplit=1)[1]

    assert "session_fingerprint=%s" in logger_block
    assert "session=%s item=%s" not in logger_block
    assert "native_session_log_fingerprint(session_id)" in logger_block
    assert "logger.info(" in source


def test_build_session_payload_log_uses_safe_fields_static_guard() -> None:
    source = inspect.getsource(native_playback_service._build_session_payload)
    logger_block = source.split("logger.info(", maxsplit=1)[1]

    assert "session_fingerprint=%s" in logger_block
    assert "include_access_token:%s" in logger_block
    assert "_safe_origin_log_label(api_origin)" in logger_block
    assert "native_session_log_fingerprint(session_id)" in logger_block
    assert "details_url=%s stream_url=%s" not in logger_block
    assert "details_url," not in logger_block
    assert "stream_url," not in logger_block


def test_probe_tracks_warning_logs_do_not_pass_raw_file_path_static_guard() -> None:
    source = inspect.getsource(native_playback_service._probe_tracks)

    assert "ffprobe track probe failed for %s: %s" not in source
    assert "ffprobe track probe exited with %s for %s" not in source
    assert "ffprobe track probe returned invalid JSON for %s" not in source
    assert "str(exc)" not in source
    assert "completed.stderr" not in source
    assert "completed.stdout" not in source.split("payload = json.loads", maxsplit=1)[0]
    assert "file_path.name" not in source

    warning_blocks = source.split("logger.warning(")[1:]
    assert warning_blocks
    for block in warning_blocks:
        logger_args = block.split("return [], []", maxsplit=1)[0]
        assert "file_path," not in logger_args
        assert "str(file_path)" not in logger_args
        assert "local_media_path_log_fingerprint(file_path)" in logger_args
