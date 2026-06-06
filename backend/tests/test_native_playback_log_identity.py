from __future__ import annotations

import inspect
import json
import string

from backend.app import cli as app_cli
from backend.app.services import native_playback_service
from backend.app.services.log_identity_service import (
    native_session_log_fingerprint,
    stable_log_fingerprint,
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
