from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PureWindowsPath
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError
from urllib.request import Request

from backend.app.services.native_playback_service import (
    _build_native_playback_stream_policy,
    resolve_native_playback_session_client_name,
)
from backend.app.routes.native_playback import _build_ios_external_launch_url
from backend.app.db import utcnow_iso
from backend.app.services.desktop_playback_service import build_desktop_playback_resolution
from backend.app.services.google_drive_service import proxy_google_drive_file_response
from backend.app.services.mobile_playback_models import (
    BrowserPlaybackSession,
    MobilePlaybackSession,
    PlaybackEpoch,
)
from backend.app.services.mobile_playback_source_service import _probe_worker_source_input_error
from backend.app.services.mobile_playback_service import MobilePlaybackManager
from backend.app.services.mobile_playback_route2_full_gate import _route2_full_mode_gate_locked
from backend.app.services.mobile_playback_route2_preflight_service import _ensure_route2_full_preflight_locked
from backend.app.services.mobile_playback_route2_gates import (
    _route2_epoch_startup_attach_gate_locked,
    _route2_epoch_startup_attach_ready_locked,
)
from backend.app.services.playback_service import build_playback_decision
from fastapi import HTTPException


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


def _make_route2_session(*, playback_mode: str = "lite", client_attach_revision: int = 0) -> MobilePlaybackSession:
    return MobilePlaybackSession(
        session_id="route2-session",
        user_id=1,
        media_item_id=104,
        profile="mobile_2160p",
        duration_seconds=6302.0,
        cache_key="route2-cache",
        source_locator="gdrive://coco",
        source_input_kind="cloud",
        source_fingerprint="route2-fingerprint",
        created_at=utcnow_iso(),
        last_client_seen_at=utcnow_iso(),
        last_media_access_at=utcnow_iso(),
        state="preparing",
        target_position_seconds=5.0,
        browser_playback=BrowserPlaybackSession(
            engine_mode="route2",
            playback_mode=playback_mode,
            client_attach_revision=client_attach_revision,
        ),
    )


def _make_route2_epoch() -> PlaybackEpoch:
    epoch_root = Path("/tmp/elvern-route2-gate-test")
    return PlaybackEpoch(
        epoch_id="route2-epoch",
        session_id="route2-session",
        created_at=utcnow_iso(),
        target_position_seconds=5.0,
        epoch_start_seconds=0.0,
        attach_position_seconds=5.0,
        epoch_dir=epoch_root,
        staging_dir=epoch_root / "staging",
        published_dir=epoch_root / "published",
        staging_manifest_path=epoch_root / "staging" / "ffmpeg.m3u8",
        metadata_path=epoch_root / "epoch.json",
        frontier_path=epoch_root / "published" / "frontier.json",
        published_init_path=epoch_root / "published" / "init.mp4",
        state="warming",
        init_published=True,
        contiguous_published_through_segment=12,
    )


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
    assert "resume" not in vlc_params
    assert "start" not in vlc_params
    assert "position" not in vlc_params
    assert "time" not in vlc_params


def test_native_playback_session_client_name_preserves_player_hint_for_ios_external_routes() -> None:
    assert resolve_native_playback_session_client_name(client_name=None, external_player="vlc") == "Elvern iOS VLC Handoff"
    assert (
        resolve_native_playback_session_client_name(client_name="Custom Surface", external_player="infuse")
        == "Elvern iOS Infuse Handoff - Custom Surface"
    )
    assert (
        resolve_native_playback_session_client_name(client_name="Elvern iOS VLC Handoff", external_player="vlc")
        == "Elvern iOS VLC Handoff"
    )


def test_external_player_stream_policy_uses_long_ttl_and_large_chunk_profiles(initialized_settings) -> None:
    local_policy = _build_native_playback_stream_policy(
        initialized_settings,
        client_name="Elvern iOS VLC Handoff",
        stream_path_class="local_file",
    )
    cloud_policy = _build_native_playback_stream_policy(
        initialized_settings,
        client_name="Elvern iOS Infuse Handoff",
        stream_path_class="cloud_proxy",
    )
    browser_policy = _build_native_playback_stream_policy(
        initialized_settings,
        client_name="Pytest Native Handoff",
        stream_path_class="local_file",
    )

    assert local_policy.external_player is True
    assert local_policy.session_ttl_seconds == initialized_settings.external_player_stream_ttl_seconds
    assert local_policy.validation_interval_seconds == 5.0
    assert local_policy.ttl_refresh_interval_seconds == 60.0
    assert local_policy.chunk_size_bytes == 2 * 1024 * 1024

    assert cloud_policy.external_player is True
    assert cloud_policy.session_ttl_seconds == initialized_settings.external_player_stream_ttl_seconds
    assert cloud_policy.validation_interval_seconds == 5.0
    assert cloud_policy.ttl_refresh_interval_seconds == 60.0
    assert cloud_policy.chunk_size_bytes == 1024 * 1024

    assert browser_policy.external_player is False
    assert browser_policy.session_ttl_seconds == initialized_settings.playback_token_ttl_seconds
    assert browser_policy.validation_interval_seconds == 0.25
    assert browser_policy.ttl_refresh_interval_seconds == 30.0
    assert browser_policy.chunk_size_bytes == 64 * 1024


def test_route2_lite_initial_attach_ready_uses_target_window_instead_of_projected_runway() -> None:
    session = _make_route2_session(playback_mode="lite", client_attach_revision=0)
    epoch = _make_route2_epoch()

    gate = _route2_epoch_startup_attach_gate_locked(
        session,
        epoch,
        route2_full_mode_requires_initial_attach_gate_locked=lambda _session: False,
        route2_full_mode_gate_locked=lambda _session, _epoch: {"mode_ready": False},
        route2_attach_gate_state_locked=lambda *_args, **_kwargs: (False, 1.0, 1.08, 6.0, 16.8, True),
        route2_epoch_ready_end_seconds_locked=lambda _session, _epoch: 20.0,
    )

    assert gate["ready"] is True
    assert gate["required_startup_runway_seconds"] == 15.0
    assert gate["actual_startup_runway_seconds"] == 15.0
    assert gate["gate_reason"] == "lite_fast_supply_surplus"


def test_route2_lite_initial_attach_still_waits_for_minimum_target_window() -> None:
    session = _make_route2_session(playback_mode="lite", client_attach_revision=0)
    epoch = _make_route2_epoch()

    gate = _route2_epoch_startup_attach_gate_locked(
        session,
        epoch,
        route2_full_mode_requires_initial_attach_gate_locked=lambda _session: False,
        route2_full_mode_gate_locked=lambda _session, _epoch: {"mode_ready": False},
        route2_attach_gate_state_locked=lambda *_args, **_kwargs: (False, 54.0, 0.72, 16.8, 6.0, True),
        route2_epoch_ready_end_seconds_locked=lambda _session, _epoch: 24.0,
    )

    assert gate["ready"] is False
    assert gate["required_startup_runway_seconds"] == 45.0
    assert gate["actual_startup_runway_seconds"] == 19.0
    assert gate["gate_reason"] == "lite_slow_supply_unknown_or_deficit"


def test_route2_lite_initial_attach_with_insufficient_observation_requires_slow_runway() -> None:
    session = _make_route2_session(playback_mode="lite", client_attach_revision=0)
    epoch = _make_route2_epoch()

    gate = _route2_epoch_startup_attach_gate_locked(
        session,
        epoch,
        route2_full_mode_requires_initial_attach_gate_locked=lambda _session: False,
        route2_full_mode_gate_locked=lambda _session, _epoch: {"mode_ready": False},
        route2_attach_gate_state_locked=lambda *_args, **_kwargs: (False, 54.0, 1.4, 5.5, 16.8, True),
        route2_epoch_ready_end_seconds_locked=lambda _session, _epoch: 50.0,
    )

    assert gate["ready"] is True
    assert gate["required_startup_runway_seconds"] == 45.0
    assert gate["gate_reason"] == "lite_slow_supply_unknown_or_deficit"


def test_route2_lite_reattach_keeps_existing_projected_runway_gate() -> None:
    session = _make_route2_session(playback_mode="lite", client_attach_revision=1)
    epoch = _make_route2_epoch()

    ready = _route2_epoch_startup_attach_ready_locked(
        session,
        epoch,
        route2_full_mode_requires_initial_attach_gate_locked=lambda _session: False,
        route2_full_mode_gate_locked=lambda _session, _epoch: {"mode_ready": False},
        route2_attach_gate_state_locked=lambda *_args, **_kwargs: (False, 54.0, 0.72, 16.8, 6.0, True),
        route2_epoch_ready_end_seconds_locked=lambda _session, _epoch: 26.0,
    )

    assert ready is False


def _make_full_gate_session() -> MobilePlaybackSession:
    session = _make_route2_session(playback_mode="full", client_attach_revision=0)
    session.browser_playback.full_preflight_state = "ready"
    session.browser_playback.full_source_bin_bytes = [500_000] * 3600
    return session


def _make_full_budget_metrics(
    *,
    prepared_bytes: float,
    cumulative_budget_bytes: list[float] | None = None,
    reserve_bytes: float = 0.0,
    reference_bytes_per_second: float = 100.0,
) -> dict[str, float | list[float] | int]:
    return {
        "prepared_bytes": prepared_bytes,
        "cumulative_budget_bytes": cumulative_budget_bytes or [50_000.0, 120_000.0, 250_000.0],
        "deadline_seconds": [60.0, 120.0, 180.0],
        "reserve_bytes": reserve_bytes,
        "estimated_fraction_remaining": 0.0,
        "future_segment_cv": 0.0,
        "reference_bytes_per_second": reference_bytes_per_second,
    }


def test_route2_full_initial_attach_supply_surplus_waits_until_120_seconds() -> None:
    session = _make_full_gate_session()
    epoch = _make_route2_epoch()

    gate = _route2_full_mode_gate_locked(
        session,
        epoch,
        route2_full_mode_requires_initial_attach_gate_locked=lambda _session: True,
        route2_full_prepare_elapsed_seconds_locked=lambda _session, now_ts=None: 25.0,
        ensure_route2_full_preflight_locked=lambda _session: None,
        route2_full_bootstrap_eta_locked=lambda _session, _epoch, now_ts=None: 34.0,
        route2_full_budget_metrics_locked=lambda _session, _epoch: _make_full_budget_metrics(prepared_bytes=10_000.0),
        route2_server_byte_goodput_locked=lambda _epoch: {"safe_rate": 0.0, "observation_seconds": 0.0, "confident": False},
        route2_client_goodput_locked=lambda _session: {"safe_rate": 0.0, "observation_seconds": 0.0, "confident": False},
        route2_epoch_ready_end_seconds=lambda _session, _epoch: 124.0,
        route2_supply_model_locked=lambda _epoch: {"effective_rate_x": 1.2, "observation_seconds": 6.0},
    )

    assert gate["mode_ready"] is False
    assert gate["required_startup_runway_seconds"] == 120.0
    assert gate["actual_startup_runway_seconds"] == 119.0
    assert gate["mode_estimate_source"] == "fast_start_supply_surplus"
    assert gate["gate_reason"] == "full_fast_start_waiting_for_runway"


def test_route2_full_initial_attach_supply_surplus_starts_at_120_seconds() -> None:
    session = _make_full_gate_session()
    epoch = _make_route2_epoch()

    gate = _route2_full_mode_gate_locked(
        session,
        epoch,
        route2_full_mode_requires_initial_attach_gate_locked=lambda _session: True,
        route2_full_prepare_elapsed_seconds_locked=lambda _session, now_ts=None: 25.0,
        ensure_route2_full_preflight_locked=lambda _session: None,
        route2_full_bootstrap_eta_locked=lambda _session, _epoch, now_ts=None: 34.0,
        route2_full_budget_metrics_locked=lambda _session, _epoch: _make_full_budget_metrics(prepared_bytes=10_000.0),
        route2_server_byte_goodput_locked=lambda _epoch: {"safe_rate": 0.0, "observation_seconds": 0.0, "confident": False},
        route2_client_goodput_locked=lambda _session: {"safe_rate": 0.0, "observation_seconds": 0.0, "confident": False},
        route2_epoch_ready_end_seconds=lambda _session, _epoch: 125.0,
        route2_supply_model_locked=lambda _epoch: {"effective_rate_x": 1.2, "observation_seconds": 6.0},
    )

    assert gate["mode_ready"] is True
    assert gate["mode_estimate_source"] == "fast_start_supply_surplus"
    assert gate["gate_reason"] == "full_fast_start_supply_surplus"


def test_route2_full_initial_attach_supply_deficit_stays_conservative() -> None:
    session = _make_full_gate_session()
    epoch = _make_route2_epoch()

    gate = _route2_full_mode_gate_locked(
        session,
        epoch,
        route2_full_mode_requires_initial_attach_gate_locked=lambda _session: True,
        route2_full_prepare_elapsed_seconds_locked=lambda _session, now_ts=None: 25.0,
        ensure_route2_full_preflight_locked=lambda _session: None,
        route2_full_bootstrap_eta_locked=lambda _session, _epoch, now_ts=None: 34.0,
        route2_full_budget_metrics_locked=lambda _session, _epoch: _make_full_budget_metrics(prepared_bytes=10_000.0),
        route2_server_byte_goodput_locked=lambda _epoch: {"safe_rate": 0.0, "observation_seconds": 0.0, "confident": False},
        route2_client_goodput_locked=lambda _session: {"safe_rate": 0.0, "observation_seconds": 0.0, "confident": False},
        route2_epoch_ready_end_seconds=lambda _session, _epoch: 125.0,
        route2_supply_model_locked=lambda _epoch: {"effective_rate_x": 0.9, "observation_seconds": 6.0},
    )

    assert gate["mode_ready"] is False
    assert gate["gate_reason"] == "full_bootstrap_server_unknown"


def test_route2_full_existing_budget_complete_condition_still_returns_ready() -> None:
    session = _make_full_gate_session()
    epoch = _make_route2_epoch()

    gate = _route2_full_mode_gate_locked(
        session,
        epoch,
        route2_full_mode_requires_initial_attach_gate_locked=lambda _session: True,
        route2_full_prepare_elapsed_seconds_locked=lambda _session, now_ts=None: 25.0,
        ensure_route2_full_preflight_locked=lambda _session: None,
        route2_full_bootstrap_eta_locked=lambda _session, _epoch, now_ts=None: 34.0,
        route2_full_budget_metrics_locked=lambda _session, _epoch: _make_full_budget_metrics(
            prepared_bytes=260_000.0,
            cumulative_budget_bytes=[50_000.0, 120_000.0, 250_000.0],
        ),
        route2_server_byte_goodput_locked=lambda _epoch: {"safe_rate": 0.0, "observation_seconds": 0.0, "confident": False},
        route2_client_goodput_locked=lambda _session: {"safe_rate": 0.0, "observation_seconds": 0.0, "confident": False},
        route2_epoch_ready_end_seconds=lambda _session, _epoch: 40.0,
        route2_supply_model_locked=lambda _epoch: {"effective_rate_x": 0.8, "observation_seconds": 6.0},
    )

    assert gate["mode_ready"] is True
    assert gate["gate_reason"] == "full_budget_complete"


def test_route2_full_gate_not_required_stays_ready_for_noninitial_attach() -> None:
    session = _make_full_gate_session()
    epoch = _make_route2_epoch()

    gate = _route2_full_mode_gate_locked(
        session,
        epoch,
        route2_full_mode_requires_initial_attach_gate_locked=lambda _session: False,
        route2_full_prepare_elapsed_seconds_locked=lambda _session, now_ts=None: 25.0,
        ensure_route2_full_preflight_locked=lambda _session: None,
        route2_full_bootstrap_eta_locked=lambda _session, _epoch, now_ts=None: 34.0,
        route2_full_budget_metrics_locked=lambda _session, _epoch: _make_full_budget_metrics(prepared_bytes=10_000.0),
        route2_server_byte_goodput_locked=lambda _epoch: {"safe_rate": 0.0, "observation_seconds": 0.0, "confident": False},
        route2_client_goodput_locked=lambda _session: {"safe_rate": 0.0, "observation_seconds": 0.0, "confident": False},
        route2_epoch_ready_end_seconds=lambda _session, _epoch: 40.0,
        route2_supply_model_locked=lambda _epoch: {"effective_rate_x": 0.8, "observation_seconds": 6.0},
    )

    assert gate["mode_ready"] is True
    assert gate["gate_reason"] == "full_gate_not_required"


def test_route2_epoch_ffmpeg_command_keeps_resumed_media_timeline_local(initialized_settings, monkeypatch) -> None:
    manager = MobilePlaybackManager(initialized_settings)
    session = _make_route2_session(playback_mode="lite", client_attach_revision=0)
    session.profile = "mobile_2160p"
    epoch = _make_route2_epoch()
    epoch.epoch_start_seconds = 3307.2
    epoch.attach_position_seconds = 3327.2

    monkeypatch.setattr(
        "backend.app.services.mobile_playback_service._resolve_worker_source_input_impl",
        lambda _settings, _session: ("https://example.test/route2-source.mkv", "url"),
    )

    command = manager._build_route2_epoch_ffmpeg_command(session=session, epoch=epoch)

    offset_index = command.index("-output_ts_offset")
    assert command[offset_index + 1] == "0.000"
    assert command[command.index("-ss") + 1] == "3307.200"


def test_google_drive_stream_proxy_preserves_provider_error_detail(monkeypatch) -> None:
    request = Request("https://www.googleapis.com/drive/v3/files/demo?alt=media")
    payload = (
        b'{'
        b'"error":{"code":403,"message":"The download quota for this file has been exceeded.",'
        b'"errors":[{"reason":"downloadQuotaExceeded"}]}}'
    )

    def _raise_http_error(_request, timeout=30):
        raise HTTPError(
            request.full_url,
            403,
            "Forbidden",
            hdrs=None,
            fp=None,
        )

    error = HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=None)
    monkeypatch.setattr(error, "read", lambda: payload)
    monkeypatch.setattr("backend.app.services.google_drive_service.urlopen", lambda _request, timeout=30: (_ for _ in ()).throw(error))

    try:
        proxy_google_drive_file_response(
            "token",
            file_id="demo",
            filename="inside-out.mkv",
            resource_key=None,
            range_header=None,
        )
    except HTTPException as exc:
        assert exc.status_code == 403
        assert exc.detail == "The download quota for this file has been exceeded."
        assert exc.headers["X-Elvern-Stream-Error-Detail"] == "The download quota for this file has been exceeded."
    else:
        raise AssertionError("Expected proxy_google_drive_file_response to raise HTTPException")


def test_probe_worker_source_input_error_uses_head_and_stream_error_headers(monkeypatch) -> None:
    request = Request("http://127.0.0.1:8000/api/native-playback/session/demo/stream?token=secret")
    seen: dict[str, str] = {}

    def _raise_http_error(http_request, timeout=15):
        seen["method"] = http_request.get_method()
        error = HTTPError(
            request.full_url,
            403,
            "Forbidden",
            hdrs={"X-Elvern-Stream-Error-Detail": "The download quota for this file has been exceeded."},
            fp=None,
        )
        raise error

    monkeypatch.setattr(
        "backend.app.services.mobile_playback_source_service.urlopen",
        _raise_http_error,
    )

    detail = _probe_worker_source_input_error(request.full_url)

    assert seen["method"] == "HEAD"
    assert detail == "The download quota for this file has been exceeded."


def test_google_drive_stream_proxy_forwards_range_header(monkeypatch) -> None:
    seen: dict[str, str | None] = {}

    class _FakeResponse:
        status = 206
        headers = {
            "Content-Length": "1",
            "Content-Range": "bytes 0-0/1",
            "Content-Type": "video/mp4",
            "Accept-Ranges": "bytes",
        }

        def read(self, _size=-1):
            return b""

    def _open(request, timeout=30):
        seen["range"] = request.headers.get("Range")
        return _FakeResponse()

    monkeypatch.setattr("backend.app.services.google_drive_service.urlopen", _open)

    response = proxy_google_drive_file_response(
        "token",
        file_id="demo",
        filename="demo.mp4",
        resource_key=None,
        range_header="bytes=0-0",
    )

    assert seen["range"] == "bytes=0-0"
    assert response.status_code == 206


def test_lite_route2_does_not_start_full_preflight_worker(monkeypatch) -> None:
    session = _make_route2_session(playback_mode="lite", client_attach_revision=0)
    called: list[str] = []

    _ensure_route2_full_preflight_locked(
        session,
        load_route2_full_preflight_cache_locked=lambda _session: called.append("load") or False,
        run_route2_full_preflight_worker=lambda _session_id: called.append("run"),
    )

    assert called == []
    assert session.browser_playback.full_preflight_state == "idle"
    assert session.browser_playback.full_preflight_error is None


def test_route2_non_retryable_quota_error_does_not_create_replacement_epoch(initialized_settings, monkeypatch) -> None:
    manager = MobilePlaybackManager(initialized_settings)
    session = _make_route2_session(playback_mode="lite", client_attach_revision=0)
    active_epoch = _make_route2_epoch()
    active_epoch.state = "failed"
    active_epoch.last_error = "The download quota for this file has been exceeded."
    session.browser_playback.active_epoch_id = active_epoch.epoch_id
    session.browser_playback.state = "starting"
    session.browser_playback.epochs[active_epoch.epoch_id] = active_epoch

    replacement_creations: list[str] = []

    monkeypatch.setattr(manager, "_ensure_route2_full_preflight_locked", lambda _session: None)
    monkeypatch.setattr(manager, "_cleanup_route2_draining_epochs_locked", lambda _session, now_ts: None)
    monkeypatch.setattr(manager, "_rebuild_route2_published_frontier_locked", lambda _epoch: None)
    monkeypatch.setattr(manager, "_record_route2_frontier_sample_locked", lambda _session, _epoch, now_ts=None: None)
    monkeypatch.setattr(manager, "_mark_route2_epoch_draining_locked", lambda _session, epoch, reason: setattr(epoch, "state", "draining"))
    monkeypatch.setattr(manager, "_route2_epoch_startup_attach_ready_locked", lambda _session, _epoch: False)
    monkeypatch.setattr(manager, "_write_route2_epoch_metadata_locked", lambda _epoch: None)
    monkeypatch.setattr(manager, "_log_route2_event", lambda *args, **kwargs: None)

    def _record_replacement(*args, **kwargs):
        replacement_creations.append("replacement")
        raise AssertionError("non-retryable quota error should not create replacement epochs")

    monkeypatch.setattr(manager, "_create_route2_replacement_epoch_locked", _record_replacement)

    manager._refresh_route2_session_authority_locked(session)

    assert replacement_creations == []
    assert session.state == "failed"
    assert session.browser_playback.state == "failed"
    assert session.last_error == "The download quota for this file has been exceeded."
    assert session.browser_playback.replacement_epoch_id is None

    manager._refresh_route2_session_authority_locked(session)

    assert replacement_creations == []
    assert session.browser_playback.replacement_epoch_id is None


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
