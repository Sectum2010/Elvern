from __future__ import annotations

from dataclasses import replace
import json
import subprocess
import time
from pathlib import Path, PureWindowsPath
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from backend.app.auth import authenticate_user, create_session as create_auth_session, destroy_session, get_user_by_session_token
from backend.app.services.native_playback_service import (
    _build_native_playback_stream_policy,
    resolve_native_playback_session_client_name,
)
from backend.app.routes.native_playback import _build_ios_external_launch_url
from backend.app.config import ConfigError, refresh_settings
from backend.app.db import get_connection, utcnow_iso
from backend.app.services.desktop_playback_service import build_desktop_playback_resolution
from backend.app.services.google_drive_service import proxy_google_drive_file_response
from backend.app.services.library_service import get_media_item_record
from backend.app.services.media_technical_metadata_service import (
    build_local_source_fingerprint,
    build_local_source_fingerprint_from_path,
    get_technical_metadata,
    get_local_technical_metadata_enrichment_status,
    mark_technical_metadata_stale,
    parse_ffprobe_technical_metadata,
    resolve_trusted_technical_metadata,
    run_local_technical_metadata_enrichment_batch,
    run_one_local_technical_metadata_enrichment,
    should_probe_local_item,
    trigger_local_technical_metadata_enrichment_batch,
    upsert_technical_metadata,
)
from backend.app.services.mobile_playback_models import (
    BrowserPlaybackSession,
    MobilePlaybackSession,
    PlaybackEpoch,
    Route2WorkerRecord,
)
from backend.app.services.mobile_playback_source_service import _probe_worker_source_input_error
from backend.app.services.mobile_playback_service import (
    ActivePlaybackWorkerConflictError,
    _HostCpuJiffySample,
    _HostCpuPressureSnapshot,
    _Route2ResourceSnapshot,
    MobilePlaybackManager,
    PlaybackAdmissionError,
    PlaybackWorkerCooldownError,
    _build_host_cpu_pressure_snapshot,
    _count_external_ffmpeg_processes,
    _host_cpu_pressure_from_resource_snapshot,
    _parse_proc_stat_host_cpu_jiffies,
    _parse_proc_stat_cpu_seconds,
    _parse_proc_status_rss_bytes,
)
from backend.app.services.mobile_playback_route2_full_gate import _route2_full_mode_gate_locked
from backend.app.services.mobile_playback_route2_preflight_service import _ensure_route2_full_preflight_locked
from backend.app.services.mobile_playback_route2_gates import (
    _route2_epoch_startup_attach_gate_locked,
    _route2_epoch_startup_attach_ready_locked,
)
from backend.app.services.route2_adaptive_controller import (
    Route2AdaptiveShadowInput,
    classify_route2_adaptive_shadow,
)
from backend.app.services.route2_transcode_strategy import (
    Route2TranscodeStrategyInput,
    select_route2_transcode_strategy,
)
from backend.app.services.route2_ffmpeg_command_adapter import (
    Route2FFmpegCommandAdapterInput,
    build_route2_ffmpeg_command_preview,
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
        auth_session_id=101,
        username="alice",
        media_item_id=104,
        media_title="Route2 Session Movie",
        profile="mobile_2160p",
        source_kind="cloud",
        duration_seconds=6302.0,
        cache_key="route2-cache",
        source_locator="gdrive://coco",
        source_input_kind="url",
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
    container: str = "mp4",
    width: int | None = 1920,
    height: int | None = 1080,
    pixel_format: str | None = None,
    bit_depth: int | None = None,
    audio_channels: int | None = None,
    hdr_flag: bool | None = None,
    dolby_vision_flag: bool | None = None,
) -> dict[str, object]:
    media_file = Path(settings.media_root) / relative_name
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(b"not a real media file")
    return {
        "id": item_id,
        "title": f"Playback Contract {item_id}",
        "original_filename": media_file.name,
        "file_path": str(media_file),
        "source_kind": "local",
        "duration_seconds": 120.0,
        "container": container,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "width": width,
        "height": height,
        "pixel_format": pixel_format,
        "bit_depth": bit_depth,
        "audio_channels": audio_channels,
        "hdr_flag": hdr_flag,
        "dolby_vision_flag": dolby_vision_flag,
        "resume_position_seconds": 18.5,
        "subtitles": [],
    }


def _make_route2_manager(initialized_settings, **overrides) -> tuple[MobilePlaybackManager, object]:
    settings = replace(
        initialized_settings,
        transcode_enabled=True,
        browser_playback_route2_enabled=True,
        ffmpeg_path=overrides.pop("ffmpeg_path", "/usr/bin/ffmpeg"),
        ffprobe_path=overrides.pop("ffprobe_path", "/usr/bin/ffprobe"),
        **overrides,
    )
    return MobilePlaybackManager(settings), settings


def _set_route2_resource_snapshot(
    manager: MobilePlaybackManager,
    *,
    sampled_at_ts: float | None = None,
    sample_mature: bool = True,
    host_cpu_total_cores: int = 20,
    host_cpu_used_cores: float = 1.0,
    host_cpu_used_percent: float = 0.05,
    route2_cpu_cores_used_total: float | None = 0.0,
    per_user_cpu_cores_used_total: dict[int, float] | None = None,
    total_memory_bytes: int | None = 16 * 1024 * 1024 * 1024,
    route2_memory_bytes_total: int | None = 0,
    external_cpu_cores_used_estimate: float | None = 0.0,
    external_cpu_percent_estimate: float | None = 0.0,
    external_ffmpeg_process_count: int = 0,
    external_ffmpeg_cpu_cores_estimate: float | None = None,
    external_pressure_level: str = "none",
) -> None:
    if sampled_at_ts is None:
        sampled_at_ts = time.time()
    manager._route2_resource_snapshot = _Route2ResourceSnapshot(
        sampled_at_ts=sampled_at_ts,
        sampled_at="2026-01-01T00:00:00+00:00",
        sample_mature=sample_mature,
        sample_stale=False,
        host_cpu_total_cores=host_cpu_total_cores,
        host_cpu_used_cores=host_cpu_used_cores,
        host_cpu_used_percent=host_cpu_used_percent,
        route2_cpu_cores_used_total=route2_cpu_cores_used_total,
        route2_cpu_percent_of_host=(
            (route2_cpu_cores_used_total / host_cpu_total_cores) * 100
            if route2_cpu_cores_used_total is not None and host_cpu_total_cores
            else None
        ),
        per_user_cpu_cores_used_total=per_user_cpu_cores_used_total or {},
        total_memory_bytes=total_memory_bytes,
        route2_memory_bytes_total=route2_memory_bytes_total,
        route2_memory_percent_of_total=(
            (route2_memory_bytes_total / total_memory_bytes) * 100
            if route2_memory_bytes_total is not None and total_memory_bytes
            else None
        ),
        external_cpu_cores_used_estimate=external_cpu_cores_used_estimate,
        external_cpu_percent_estimate=external_cpu_percent_estimate,
        external_ffmpeg_process_count=external_ffmpeg_process_count,
        external_ffmpeg_cpu_cores_estimate=external_ffmpeg_cpu_cores_estimate,
        external_pressure_level=external_pressure_level,
        missing_metrics=[],
    )


def _capture_route2_worker_threads(monkeypatch) -> list[tuple[str, str, str]]:
    started_workers: list[tuple[str, str, str]] = []

    class _FakeThread:
        def __init__(self, *, target, args, daemon, name):
            self.target = target
            self.args = args
            self.daemon = daemon
            self.name = name

        def start(self) -> None:
            started_workers.append(self.args)

    monkeypatch.setattr("backend.app.services.mobile_playback_service.threading.Thread", _FakeThread)
    return started_workers


def _active_route2_record_for_session(
    manager: MobilePlaybackManager,
    session_payload: dict[str, object],
) -> tuple[MobilePlaybackSession, PlaybackEpoch, Route2WorkerRecord]:
    session = manager._sessions[str(session_payload["session_id"])]
    active_epoch_id = session.browser_playback.active_epoch_id
    assert active_epoch_id is not None
    epoch = session.browser_playback.epochs[active_epoch_id]
    assert epoch.active_worker_id is not None
    record = manager._route2_workers[epoch.active_worker_id]
    return session, epoch, record


def _mark_route2_runtime_supply(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
    record: Route2WorkerRecord,
    *,
    supply_rate_x: float,
    observation_seconds: float = 12.0,
    runway_seconds: float = 60.0,
    effective_playhead_seconds: float = 40.0,
    cpu_cores_used: float | None = None,
    client_is_playing: bool = True,
    manifest_complete: bool = False,
    refill_in_progress: bool = True,
) -> None:
    ready_end_seconds = effective_playhead_seconds + runway_seconds
    epoch.epoch_start_seconds = 0.0
    epoch.attach_position_seconds = min(epoch.attach_position_seconds, effective_playhead_seconds)
    epoch.init_published = True
    epoch.contiguous_published_through_segment = max(0, int((ready_end_seconds / 2.0) - 1))
    epoch.transcoder_completed = manifest_complete
    if refill_in_progress:
        epoch.active_worker_id = record.worker_id
    else:
        epoch.active_worker_id = None
    epoch.frontier_samples = [
        (0.0, 20.0),
        (float(observation_seconds), 20.0 + (float(supply_rate_x) * float(observation_seconds))),
    ]
    session.ready_start_seconds = 0.0
    session.ready_end_seconds = ready_end_seconds
    session.target_position_seconds = 0.0
    session.last_stable_position_seconds = 0.0
    session.committed_playhead_seconds = effective_playhead_seconds
    session.actual_media_element_time_seconds = 0.0
    session.pending_target_seconds = None
    session.lifecycle_state = "attached"
    session.client_is_playing = client_is_playing
    record.cpu_cores_used = cpu_cores_used
    record.telemetry_sampled = cpu_cores_used is not None


def _make_route2_worker_record_for_spawn_dry_run(*, source_kind: str = "local", user_id: int = 1) -> Route2WorkerRecord:
    return Route2WorkerRecord(
        worker_id="spawn-dry-run-worker",
        session_id="spawn-dry-run-session",
        epoch_id="spawn-dry-run-epoch",
        user_id=user_id,
        username="alice",
        auth_session_id=11,
        media_item_id=990,
        title="Spawn Dry Run",
        playback_mode="full",
        profile="mobile_1080p",
        source_kind=source_kind,
        target_position_seconds=0.0,
        state="queued",
    )


class _TelemetryProcess:
    def __init__(self, *, pid: int = 4321, running: bool = True) -> None:
        self.pid = pid
        self.running = running

    def poll(self):
        return None if self.running else 0


def _make_route2_adaptive_input(**overrides) -> Route2AdaptiveShadowInput:
    payload = Route2AdaptiveShadowInput(
        worker_state="running",
        playback_mode="full",
        profile="mobile_2160p",
        source_kind="local",
        assigned_threads=4,
        default_threads=4,
        max_threads=8,
        adaptive_max_threads=10,
        cpu_cores_used=4.0,
        allocated_cpu_cores=8,
        user_cpu_cores_used_total=4.0,
        route2_cpu_upbound_cores=18,
        route2_cpu_cores_used_total=8.0,
        active_route2_user_count=1,
        host_cpu_total_cores=20,
        host_cpu_used_cores=8.2,
        host_cpu_used_percent=0.41,
        external_cpu_cores_used_estimate=0.2,
        external_cpu_percent_estimate=0.01,
        external_ffmpeg_process_count=0,
        external_ffmpeg_cpu_cores_estimate=None,
        host_cpu_sample_mature=True,
        memory_bytes=512 * 1024 * 1024,
        total_memory_bytes=16 * 1024 * 1024 * 1024,
        route2_memory_bytes_total=512 * 1024 * 1024,
        ready_end_seconds=180.0,
        effective_playhead_seconds=40.0,
        ahead_runway_seconds=140.0,
        required_startup_runway_seconds=120.0,
        supply_rate_x=1.0,
        supply_observation_seconds=20.0,
        client_goodput_bytes_per_second=4_000_000.0,
        client_goodput_confident=True,
        server_goodput_bytes_per_second=6_000_000.0,
        server_goodput_confident=True,
        non_retryable_error=None,
        starvation_risk=False,
        stalled_recovery_needed=False,
        mode_ready=True,
    )
    for key, value in overrides.items():
        setattr(payload, key, value)
    return payload


def _insert_media_item_record(settings, item: dict[str, object]) -> dict[str, object]:
    file_path = str(item["file_path"])
    file_size = int(item.get("file_size") or 0)
    file_mtime = float(item.get("file_mtime") or 0.0)
    candidate = Path(file_path)
    if candidate.exists():
        stat = candidate.stat()
        file_size = int(item.get("file_size") or stat.st_size)
        file_mtime = float(item.get("file_mtime") or stat.st_mtime)
    elif file_size <= 0:
        file_size = 1
        file_mtime = time.time()

    now = utcnow_iso()
    with get_connection(settings) as connection:
        connection.execute(
            """
            INSERT INTO media_items (
                id,
                title,
                original_filename,
                file_path,
                source_kind,
                library_source_id,
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
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                int(item["id"]),
                str(item.get("title") or f"Media Item {item['id']}"),
                str(item.get("original_filename") or Path(file_path).name or f"{item['id']}.bin"),
                file_path,
                str(item.get("source_kind") or "local"),
                file_size,
                file_mtime,
                item.get("duration_seconds"),
                item.get("width"),
                item.get("height"),
                item.get("video_codec"),
                item.get("audio_codec"),
                item.get("container"),
                now,
                now,
                now,
            ),
        )
        connection.commit()
    return item


def _make_route2_strategy_input(**overrides) -> Route2TranscodeStrategyInput:
    payload = Route2TranscodeStrategyInput(
        container="mp4",
        video_codec="h264",
        audio_codec="aac",
        width=1920,
        height=1080,
        pixel_format="yuv420p",
        bit_depth=8,
        hdr_flag=False,
        dolby_vision_flag=False,
        audio_channels=2,
        profile_key="mobile_2160p",
        source_kind="local",
        original_filename="movie.1080p.bluray.x264.aac.mkv",
    )
    for key, value in overrides.items():
        setattr(payload, key, value)
    return payload


def _make_route2_ffmpeg_command_adapter_input(**overrides) -> Route2FFmpegCommandAdapterInput:
    payload = Route2FFmpegCommandAdapterInput(
        ffmpeg_path="/usr/bin/ffmpeg",
        profile_key="mobile_2160p",
        thread_budget=4,
        source_input="https://example.test/library/movie.mp4",
        source_input_kind="url",
        epoch_start_seconds=0.0,
        segment_pattern="/tmp/route2-preview/segment_%06d.m4s",
        staging_manifest_path="/tmp/route2-preview/ffmpeg.m3u8",
        strategy="full_transcode",
        strategy_confidence="high",
        strategy_reason="preview",
        video_copy_safe=False,
        audio_copy_safe=False,
        risk_flags=[],
        missing_metadata=[],
        metadata_source="local_ffprobe",
        metadata_trusted=True,
    )
    for key, value in overrides.items():
        setattr(payload, key, value)
    return payload


def _upsert_trusted_local_technical_metadata(
    settings,
    item: dict[str, object],
    **overrides,
) -> dict[str, object]:
    return upsert_technical_metadata(
        settings,
        media_item_id=int(item["id"]),
        values={
            "metadata_version": 1,
            "metadata_source": "local_ffprobe",
            "probe_status": "probed",
            "probe_error": None,
            "probed_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
            "source_fingerprint": build_local_source_fingerprint_from_path(item["file_path"]),
            "container": item.get("container"),
            "video_codec": item.get("video_codec"),
            "audio_codec": item.get("audio_codec"),
            "width": item.get("width"),
            "height": item.get("height"),
            **overrides,
        },
    )


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
    assert gate["mode_estimate_seconds"] is not None
    assert gate["mode_estimate_seconds"] < 5.0


def test_route2_full_initial_attach_supply_surplus_starts_before_preflight_ready() -> None:
    session = _make_route2_session(playback_mode="full", client_attach_revision=0)
    epoch = _make_route2_epoch()

    gate = _route2_full_mode_gate_locked(
        session,
        epoch,
        route2_full_mode_requires_initial_attach_gate_locked=lambda _session: True,
        route2_full_prepare_elapsed_seconds_locked=lambda _session, now_ts=None: 25.0,
        ensure_route2_full_preflight_locked=lambda _session: None,
        route2_full_bootstrap_eta_locked=lambda _session, _epoch, now_ts=None: 3600.0,
        route2_full_budget_metrics_locked=lambda _session, _epoch: None,
        route2_server_byte_goodput_locked=lambda _epoch: {"safe_rate": 0.0, "observation_seconds": 0.0, "confident": False},
        route2_client_goodput_locked=lambda _session: {"safe_rate": 0.0, "observation_seconds": 0.0, "confident": False},
        route2_epoch_ready_end_seconds=lambda _session, _epoch: 125.0,
        route2_supply_model_locked=lambda _epoch: {"effective_rate_x": 1.2, "observation_seconds": 6.0},
    )

    assert gate["mode_ready"] is True
    assert gate["mode_estimate_source"] == "fast_start_supply_surplus"
    assert gate["gate_reason"] == "full_fast_start_supply_surplus"


def test_route2_full_initial_attach_supply_surplus_starts_before_budget_metrics_exist() -> None:
    session = _make_full_gate_session()
    epoch = _make_route2_epoch()

    gate = _route2_full_mode_gate_locked(
        session,
        epoch,
        route2_full_mode_requires_initial_attach_gate_locked=lambda _session: True,
        route2_full_prepare_elapsed_seconds_locked=lambda _session, now_ts=None: 25.0,
        ensure_route2_full_preflight_locked=lambda _session: None,
        route2_full_bootstrap_eta_locked=lambda _session, _epoch, now_ts=None: 3600.0,
        route2_full_budget_metrics_locked=lambda _session, _epoch: None,
        route2_server_byte_goodput_locked=lambda _epoch: {"safe_rate": 0.0, "observation_seconds": 0.0, "confident": False},
        route2_client_goodput_locked=lambda _session: {"safe_rate": 0.0, "observation_seconds": 0.0, "confident": False},
        route2_epoch_ready_end_seconds=lambda _session, _epoch: 125.0,
        route2_supply_model_locked=lambda _epoch: {"effective_rate_x": 1.2, "observation_seconds": 6.0},
    )

    assert gate["mode_ready"] is True
    assert gate["mode_estimate_source"] == "fast_start_supply_surplus"
    assert gate["gate_reason"] == "full_fast_start_supply_surplus"


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


def test_route2_full_existing_budget_projected_ready_condition_still_returns_ready() -> None:
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
            prepared_bytes=10_000.0,
            cumulative_budget_bytes=[50_000.0, 120_000.0, 250_000.0],
            reserve_bytes=0.0,
            reference_bytes_per_second=100.0,
        ),
        route2_server_byte_goodput_locked=lambda _epoch: {"safe_rate": 2000.0, "observation_seconds": 10.0, "confident": True},
        route2_client_goodput_locked=lambda _session: {"safe_rate": 2000.0, "observation_seconds": 10.0, "confident": True},
        route2_epoch_ready_end_seconds=lambda _session, _epoch: 40.0,
        route2_supply_model_locked=lambda _epoch: {"effective_rate_x": 0.8, "observation_seconds": 6.0},
    )

    assert gate["mode_ready"] is True
    assert gate["gate_reason"] == "full_budget_projected_ready"


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

    command = manager._build_route2_epoch_ffmpeg_command(session=session, epoch=epoch, thread_budget=4)

    offset_index = command.index("-output_ts_offset")
    assert command[offset_index + 1] == "0.000"
    assert command[command.index("-ss") + 1] == "3307.200"
    assert command[command.index("-threads") + 1] == "4"


def test_route2_transcode_strategy_shadow_classifies_safe_h264_aac_as_stream_copy() -> None:
    decision = select_route2_transcode_strategy(_make_route2_strategy_input())

    assert decision.strategy == "stream_copy_video_audio"
    assert decision.confidence == "high"
    assert decision.video_copy_safe is True
    assert decision.audio_copy_safe is True


@pytest.mark.parametrize("audio_codec", ["truehd", "dts", "ac3"])
def test_route2_transcode_strategy_shadow_classifies_safe_h264_with_unsafe_audio_as_copy_video_transcode_audio(
    audio_codec: str,
) -> None:
    decision = select_route2_transcode_strategy(
        _make_route2_strategy_input(
            audio_codec=audio_codec,
            audio_channels=6,
            original_filename=f"movie.1080p.bluray.x264.{audio_codec}.mkv",
        )
    )

    assert decision.strategy == "copy_video_transcode_audio"
    assert decision.video_copy_safe is True
    assert decision.audio_copy_safe is False


def test_route2_transcode_strategy_shadow_classifies_hevc_main10_with_aac_as_full_transcode() -> None:
    decision = select_route2_transcode_strategy(
        _make_route2_strategy_input(
            video_codec="hevc",
            bit_depth=10,
            original_filename="movie.2160p.hevc.10bit.aac.mkv",
        )
    )

    assert decision.strategy == "full_transcode"
    assert decision.confidence == "high"


def test_route2_transcode_strategy_shadow_classifies_hevc_truehd_remux_as_full_transcode() -> None:
    decision = select_route2_transcode_strategy(
        _make_route2_strategy_input(
            container="mkv",
            video_codec="hevc",
            audio_codec="truehd",
            width=3840,
            height=2160,
            bit_depth=10,
            audio_channels=8,
            original_filename="movie.2160p.truehd.atmos.dv.hevc.remux.mkv",
        )
    )

    assert decision.strategy == "full_transcode"
    assert "remux_risk" in decision.risk_flags


def test_route2_transcode_strategy_shadow_classifies_unknown_video_codec_conservatively() -> None:
    decision = select_route2_transcode_strategy(
        _make_route2_strategy_input(
            video_codec="vp9",
            original_filename="movie.vp9.aac.webm",
        )
    )

    assert decision.strategy == "full_transcode"
    assert decision.confidence in {"low", "medium"}


def test_route2_transcode_strategy_shadow_requires_explicit_pixel_and_bit_depth_for_h264_copy() -> None:
    decision = select_route2_transcode_strategy(
        _make_route2_strategy_input(
            pixel_format=None,
            bit_depth=None,
        )
    )

    assert decision.strategy == "full_transcode"
    assert "pixel_format" in decision.missing_metadata
    assert "bit_depth" in decision.missing_metadata


def test_route2_transcode_strategy_shadow_keeps_hdr_or_dolby_vision_on_full_transcode() -> None:
    hdr_decision = select_route2_transcode_strategy(
        _make_route2_strategy_input(
            hdr_flag=True,
            original_filename="movie.hdr10.h264.aac.mp4",
        )
    )
    dv_decision = select_route2_transcode_strategy(
        _make_route2_strategy_input(
            dolby_vision_flag=True,
            original_filename="movie.dv.h264.aac.mp4",
        )
    )

    assert hdr_decision.strategy == "full_transcode"
    assert dv_decision.strategy == "full_transcode"
    assert "hdr_risk" in hdr_decision.risk_flags
    assert "dolby_vision_risk" in dv_decision.risk_flags


def test_route2_transcode_strategy_shadow_reports_missing_metadata_conservatively() -> None:
    decision = select_route2_transcode_strategy(
        _make_route2_strategy_input(
            width=None,
            height=None,
            audio_channels=None,
        )
    )

    assert decision.strategy == "full_transcode"
    assert "width" in decision.missing_metadata
    assert "height" in decision.missing_metadata


def test_route2_transcode_strategy_shadow_is_pure_value_helper(monkeypatch) -> None:
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected io")))

    decision = select_route2_transcode_strategy(_make_route2_strategy_input())

    assert decision.strategy == "stream_copy_video_audio"


def test_route2_transcode_strategy_shadow_does_not_change_current_ffmpeg_command_path(initialized_settings, monkeypatch) -> None:
    manager = MobilePlaybackManager(initialized_settings)
    session = _make_route2_session(playback_mode="lite", client_attach_revision=0)
    session.profile = "mobile_2160p"
    session.source_container = "mp4"
    session.source_video_codec = "h264"
    session.source_audio_codec = "aac"
    session.source_width = 1920
    session.source_height = 1080
    session.source_pixel_format = "yuv420p"
    session.source_bit_depth = 8
    session.source_audio_channels = 2
    epoch = _make_route2_epoch()

    monkeypatch.setattr(
        "backend.app.services.mobile_playback_service._resolve_worker_source_input_impl",
        lambda _settings, _session: ("https://example.test/route2-source.mp4", "url"),
    )

    command = manager._build_route2_epoch_ffmpeg_command(session=session, epoch=epoch, thread_budget=4)

    assert "-c:v" in command
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-c:a") + 1] == "aac"


def test_route2_ffmpeg_command_adapter_full_transcode_preview_keeps_libx264_aac_shape() -> None:
    preview = build_route2_ffmpeg_command_preview(_make_route2_ffmpeg_command_adapter_input())

    assert preview.adapter_strategy == "full_transcode"
    assert "-c:v" in preview.command_preview
    assert preview.command_preview[preview.command_preview.index("-c:v") + 1] == "libx264"
    assert preview.command_preview[preview.command_preview.index("-c:a") + 1] == "aac"
    assert preview.active_enabled is False


def test_route2_ffmpeg_command_adapter_stream_copy_preview_uses_copy_copy() -> None:
    preview = build_route2_ffmpeg_command_preview(
        _make_route2_ffmpeg_command_adapter_input(
            strategy="stream_copy_video_audio",
            video_copy_safe=True,
            audio_copy_safe=True,
        )
    )

    assert preview.adapter_strategy == "stream_copy_video_audio"
    assert preview.command_preview[preview.command_preview.index("-c:v") + 1] == "copy"
    assert preview.command_preview[preview.command_preview.index("-c:a") + 1] == "copy"


def test_route2_ffmpeg_command_adapter_copy_video_transcode_audio_preview_uses_copy_and_aac() -> None:
    preview = build_route2_ffmpeg_command_preview(
        _make_route2_ffmpeg_command_adapter_input(
            strategy="copy_video_transcode_audio",
            video_copy_safe=True,
            audio_copy_safe=False,
        )
    )

    assert preview.adapter_strategy == "copy_video_transcode_audio"
    assert preview.command_preview[preview.command_preview.index("-c:v") + 1] == "copy"
    assert preview.command_preview[preview.command_preview.index("-c:a") + 1] == "aac"


def test_route2_ffmpeg_command_adapter_hevc_like_strategy_falls_back_to_full_transcode_preview() -> None:
    preview = build_route2_ffmpeg_command_preview(
        _make_route2_ffmpeg_command_adapter_input(
            strategy="full_transcode",
            video_copy_safe=False,
            audio_copy_safe=False,
            risk_flags=["hdr_risk", "dolby_vision_risk", "high_bit_depth_risk", "remux_risk"],
        )
    )

    assert preview.adapter_strategy == "full_transcode"
    assert preview.fallback_reason is None
    assert preview.command_preview[preview.command_preview.index("-c:v") + 1] == "libx264"


def test_route2_ffmpeg_command_adapter_refuses_copy_preview_when_metadata_is_untrusted_or_unsafe() -> None:
    preview = build_route2_ffmpeg_command_preview(
        _make_route2_ffmpeg_command_adapter_input(
            strategy="stream_copy_video_audio",
            video_copy_safe=True,
            audio_copy_safe=True,
            metadata_source="coarse",
            metadata_trusted=False,
            risk_flags=["remux_risk"],
        )
    )

    assert preview.adapter_strategy == "full_transcode"
    assert preview.fallback_reason is not None
    assert preview.command_preview[preview.command_preview.index("-c:v") + 1] == "libx264"


def test_route2_ffmpeg_command_adapter_preview_redacts_source_urls_and_tokens() -> None:
    preview = build_route2_ffmpeg_command_preview(
        _make_route2_ffmpeg_command_adapter_input(
            source_input="https://example.test/media/movie.mp4?token=secret&sig=abc&keep=ok",
            strategy="stream_copy_video_audio",
            video_copy_safe=True,
            audio_copy_safe=True,
        )
    )

    input_value = preview.command_preview[preview.command_preview.index("-i") + 1]
    assert "secret" not in input_value
    assert "abc" not in input_value
    assert "REDACTED" in input_value
    assert "keep=ok" in input_value


def test_route2_ffmpeg_command_adapter_1259_like_fixture_remains_full_transcode_preview() -> None:
    preview = build_route2_ffmpeg_command_preview(
        _make_route2_ffmpeg_command_adapter_input(
            strategy="full_transcode",
            strategy_reason="hevc main10 remux",
            risk_flags=["hdr_risk", "dolby_vision_risk", "remux_risk", "high_bit_depth_risk", "unsafe_pixel_format"],
            metadata_source="local_ffprobe",
            metadata_trusted=True,
        )
    )

    assert preview.adapter_strategy == "full_transcode"
    assert "libx264" in preview.command_preview


def test_resolve_trusted_technical_metadata_accepts_matching_local_ffprobe_row(initialized_settings) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=904,
        relative_name="route2/trusted-safe.mp4",
        pixel_format=None,
        bit_depth=None,
        audio_channels=None,
    )
    _insert_media_item_record(initialized_settings, item)
    _upsert_trusted_local_technical_metadata(
        initialized_settings,
        item,
        pixel_format="yuv420p",
        bit_depth=8,
        audio_channels=2,
        color_transfer="bt709",
        color_primaries="bt709",
        color_space="bt709",
        video_profile="High",
        audio_profile="LC",
    )

    resolved = resolve_trusted_technical_metadata(
        initialized_settings,
        get_media_item_record(initialized_settings, item_id=int(item["id"])),
    )

    assert resolved is not None
    assert resolved["metadata_source"] == "local_ffprobe"
    assert resolved["pixel_format"] == "yuv420p"
    assert resolved["bit_depth"] == 8
    assert resolved["audio_channels"] == 2


def test_resolve_trusted_technical_metadata_ignores_stale_fingerprint(initialized_settings) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=905,
        relative_name="route2/stale-fingerprint.mp4",
        pixel_format=None,
        bit_depth=None,
        audio_channels=None,
    )
    _insert_media_item_record(initialized_settings, item)
    upsert_technical_metadata(
        initialized_settings,
        media_item_id=int(item["id"]),
        values={
            "metadata_version": 1,
            "metadata_source": "local_ffprobe",
            "probe_status": "probed",
            "probe_error": None,
            "probed_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
            "source_fingerprint": "mismatched-fingerprint",
            "container": "mp4",
            "video_codec": "h264",
            "audio_codec": "aac",
            "width": 1920,
            "height": 1080,
            "pixel_format": "yuv420p",
            "bit_depth": 8,
            "audio_channels": 2,
        },
    )

    resolved = resolve_trusted_technical_metadata(
        initialized_settings,
        get_media_item_record(initialized_settings, item_id=int(item["id"])),
    )

    assert resolved is None


@pytest.mark.parametrize("probe_status", ["failed", "stale", "never"])
def test_resolve_trusted_technical_metadata_ignores_untrusted_probe_states(initialized_settings, probe_status: str) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=906 + ["failed", "stale", "never"].index(probe_status),
        relative_name=f"route2/untrusted-{probe_status}.mp4",
        pixel_format=None,
        bit_depth=None,
        audio_channels=None,
    )
    _insert_media_item_record(initialized_settings, item)
    upsert_technical_metadata(
        initialized_settings,
        media_item_id=int(item["id"]),
        values={
            "metadata_version": 1,
            "metadata_source": "local_ffprobe",
            "probe_status": probe_status,
            "probe_error": None,
            "probed_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
            "source_fingerprint": build_local_source_fingerprint_from_path(item["file_path"]),
            "container": "mp4",
            "video_codec": "h264",
            "audio_codec": "aac",
            "width": 1920,
            "height": 1080,
            "pixel_format": "yuv420p",
            "bit_depth": 8,
            "audio_channels": 2,
        },
    )

    resolved = resolve_trusted_technical_metadata(
        initialized_settings,
        get_media_item_record(initialized_settings, item_id=int(item["id"])),
    )

    assert resolved is None


def test_resolve_trusted_technical_metadata_ignores_wrong_version_or_source(initialized_settings) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=909,
        relative_name="route2/untrusted-version.mp4",
        pixel_format=None,
        bit_depth=None,
        audio_channels=None,
    )
    _insert_media_item_record(initialized_settings, item)
    upsert_technical_metadata(
        initialized_settings,
        media_item_id=int(item["id"]),
        values={
            "metadata_version": 999,
            "metadata_source": "manual_import",
            "probe_status": "probed",
            "probe_error": None,
            "probed_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
            "source_fingerprint": build_local_source_fingerprint_from_path(item["file_path"]),
            "container": "mp4",
            "video_codec": "h264",
            "audio_codec": "aac",
            "width": 1920,
            "height": 1080,
            "pixel_format": "yuv420p",
            "bit_depth": 8,
            "audio_channels": 2,
        },
    )

    resolved = resolve_trusted_technical_metadata(
        initialized_settings,
        get_media_item_record(initialized_settings, item_id=int(item["id"])),
    )

    assert resolved is None


def test_resolve_trusted_technical_metadata_ignores_cloud_items_without_reads(initialized_settings, monkeypatch) -> None:
    item = {
        "id": 910,
        "title": "Cloud Item",
        "source_kind": "cloud",
        "file_path": "/does/not/matter.mp4",
    }
    monkeypatch.setattr(
        "backend.app.services.media_technical_metadata_service.get_technical_metadata",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected metadata lookup")),
    )

    resolved = resolve_trusted_technical_metadata(initialized_settings, item)

    assert resolved is None


def test_route2_worker_status_uses_trusted_local_metadata_for_stream_copy_shadow(initialized_settings) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    item = _make_local_item(
        settings,
        item_id=911,
        relative_name="route2/trusted-stream-copy.mp4",
        pixel_format=None,
        bit_depth=None,
        audio_channels=None,
    )
    _insert_media_item_record(settings, item)
    _upsert_trusted_local_technical_metadata(
        settings,
        item,
        pixel_format="yuv420p",
        bit_depth=8,
        audio_channels=2,
    )
    manager.create_session(
        item,
        user_id=1,
        auth_session_id=101,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
    )

    summary = manager.get_route2_worker_status()
    worker = next(group for group in summary["workers_by_user"] if group["user_id"] == 1)["items"][0]

    assert worker["route2_transcode_strategy"] == "stream_copy_video_audio"
    assert worker["route2_strategy_metadata_source"] == "local_ffprobe"
    assert worker["route2_strategy_metadata_trusted"] is True
    assert worker["route2_command_adapter_preview_strategy"] == "stream_copy_video_audio"
    assert worker["route2_command_adapter_active"] is False
    assert "copy video + copy audio" in worker["route2_command_adapter_summary"]
    assert worker["route2_command_adapter_fallback_reason"] is None


@pytest.mark.parametrize("audio_codec", ["truehd", "dts"])
def test_route2_worker_status_uses_trusted_local_metadata_for_copy_video_transcode_audio_shadow(
    initialized_settings,
    audio_codec: str,
) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    item = _make_local_item(
        settings,
        item_id=912 if audio_codec == "truehd" else 913,
        relative_name=f"route2/trusted-copy-video-{audio_codec}.mp4",
        audio_codec=audio_codec,
        pixel_format=None,
        bit_depth=None,
        audio_channels=None,
    )
    _insert_media_item_record(settings, item)
    _upsert_trusted_local_technical_metadata(
        settings,
        item,
        pixel_format="yuv420p",
        bit_depth=8,
        audio_channels=8,
    )
    manager.create_session(
        item,
        user_id=1,
        auth_session_id=101,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
    )

    summary = manager.get_route2_worker_status()
    worker = next(group for group in summary["workers_by_user"] if group["user_id"] == 1)["items"][0]

    assert worker["route2_transcode_strategy"] == "copy_video_transcode_audio"
    assert worker["route2_strategy_metadata_source"] == "local_ffprobe"
    assert worker["route2_strategy_metadata_trusted"] is True
    assert worker["route2_command_adapter_preview_strategy"] == "copy_video_transcode_audio"
    assert worker["route2_command_adapter_active"] is False
    assert "copy video + AAC audio transcode" in worker["route2_command_adapter_summary"]
    assert worker["route2_command_adapter_fallback_reason"] is None


def test_route2_worker_status_keeps_hevc_main10_truehd_on_full_transcode_with_trusted_metadata(initialized_settings) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    item = _make_local_item(
        settings,
        item_id=914,
        relative_name="route2/trusted-hevc-full.mkv",
        container="mkv",
        video_codec="hevc",
        audio_codec="truehd",
        width=3840,
        height=2160,
        pixel_format=None,
        bit_depth=None,
        audio_channels=None,
    )
    _insert_media_item_record(settings, item)
    _upsert_trusted_local_technical_metadata(
        settings,
        item,
        pixel_format="yuv420p10le",
        bit_depth=10,
        audio_channels=8,
        dolby_vision_detected=True,
    )
    manager.create_session(
        item,
        user_id=1,
        auth_session_id=101,
        username="alice",
        engine_mode="route2",
        playback_mode="full",
    )

    summary = manager.get_route2_worker_status()
    worker = next(group for group in summary["workers_by_user"] if group["user_id"] == 1)["items"][0]

    assert worker["route2_transcode_strategy"] == "full_transcode"
    assert worker["route2_strategy_metadata_source"] == "local_ffprobe"
    assert worker["route2_strategy_metadata_trusted"] is True
    assert worker["route2_command_adapter_preview_strategy"] == "full_transcode"
    assert worker["route2_command_adapter_active"] is False
    assert "libx264 video + AAC audio" in worker["route2_command_adapter_summary"]


def test_route2_worker_status_ignores_stale_or_missing_trusted_metadata_and_keeps_conservative_behavior(initialized_settings) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    item = _make_local_item(
        settings,
        item_id=915,
        relative_name="route2/stale-fallback.mp4",
        pixel_format=None,
        bit_depth=None,
        audio_channels=None,
    )
    _insert_media_item_record(settings, item)
    upsert_technical_metadata(
        settings,
        media_item_id=int(item["id"]),
        values={
            "metadata_version": 1,
            "metadata_source": "local_ffprobe",
            "probe_status": "probed",
            "probe_error": None,
            "probed_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
            "source_fingerprint": "stale-fingerprint",
            "container": "mp4",
            "video_codec": "h264",
            "audio_codec": "aac",
            "width": 1920,
            "height": 1080,
            "pixel_format": "yuv420p",
            "bit_depth": 8,
            "audio_channels": 2,
        },
    )
    manager.create_session(
        item,
        user_id=1,
        auth_session_id=101,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
    )

    summary = manager.get_route2_worker_status()
    worker = next(group for group in summary["workers_by_user"] if group["user_id"] == 1)["items"][0]

    assert worker["route2_transcode_strategy"] == "full_transcode"
    assert worker["route2_strategy_metadata_source"] == "coarse"
    assert worker["route2_strategy_metadata_trusted"] is False
    assert worker["route2_command_adapter_preview_strategy"] == "full_transcode"
    assert worker["route2_command_adapter_active"] is False


def test_route2_trusted_metadata_shadow_does_not_change_current_ffmpeg_command_path(initialized_settings, monkeypatch) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    item = _make_local_item(
        settings,
        item_id=916,
        relative_name="route2/trusted-shadow-command.mp4",
        pixel_format=None,
        bit_depth=None,
        audio_channels=None,
    )
    _insert_media_item_record(settings, item)
    _upsert_trusted_local_technical_metadata(
        settings,
        item,
        pixel_format="yuv420p",
        bit_depth=8,
        audio_channels=2,
    )
    payload = manager.create_session(
        item,
        user_id=1,
        auth_session_id=111,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
    )
    summary = manager.get_route2_worker_status()
    worker = next(group for group in summary["workers_by_user"] if group["user_id"] == 1)["items"][0]
    assert worker["route2_transcode_strategy"] == "stream_copy_video_audio"
    assert worker["route2_command_adapter_preview_strategy"] == "stream_copy_video_audio"
    assert worker["route2_command_adapter_active"] is False

    with manager._lock:
        session = manager._sessions[payload["session_id"]]
        active_epoch = session.browser_playback.epochs[session.browser_playback.active_epoch_id]

    monkeypatch.setattr(
        "backend.app.services.mobile_playback_service._resolve_worker_source_input_impl",
        lambda _settings, _session: ("https://example.test/route2-source.mp4", "url"),
    )

    command = manager._build_route2_epoch_ffmpeg_command(session=session, epoch=active_epoch, thread_budget=4)

    assert "-c:v" in command
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-c:a") + 1] == "aac"


def test_parse_ffprobe_technical_metadata_extracts_h264_yuv420p_aac_fields() -> None:
    payload = {
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "120.125",
            "bit_rate": "4000000",
        },
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "profile": "High",
                "level": 41,
                "pix_fmt": "yuv420p",
                "width": 1920,
                "height": 1080,
                "color_transfer": "bt709",
                "color_primaries": "bt709",
                "color_space": "bt709",
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "profile": "LC",
                "channels": 2,
                "channel_layout": "stereo",
                "sample_rate": "48000",
            },
        ],
    }

    metadata = parse_ffprobe_technical_metadata(payload)

    assert metadata["container"] == "mp4"
    assert metadata["duration_seconds"] == pytest.approx(120.125)
    assert metadata["bit_rate"] == 4_000_000
    assert metadata["video_codec"] == "h264"
    assert metadata["video_profile"] == "High"
    assert metadata["video_level"] == "41"
    assert metadata["pixel_format"] == "yuv420p"
    assert metadata["bit_depth"] == 8
    assert metadata["width"] == 1920
    assert metadata["height"] == 1080
    assert metadata["hdr_detected"] is False
    assert metadata["dolby_vision_detected"] is False
    assert metadata["audio_codec"] == "aac"
    assert metadata["audio_profile"] == "LC"
    assert metadata["audio_channels"] == 2
    assert metadata["audio_channel_layout"] == "stereo"
    assert metadata["audio_sample_rate"] == 48_000
    assert metadata["subtitle_count"] == 0


def test_parse_ffprobe_technical_metadata_extracts_hevc_main10_truehd_fields() -> None:
    payload = {
        "format": {
            "format_name": "matroska,webm",
            "duration": "6302.04",
            "bit_rate": "81234567",
        },
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "profile": "Main 10",
                "level": 153,
                "pix_fmt": "yuv420p10le",
                "width": 3840,
                "height": 2160,
                "color_transfer": "bt2020-10",
                "color_primaries": "bt2020",
                "color_space": "bt2020nc",
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "truehd",
                "profile": "Dolby TrueHD",
                "channels": 8,
                "channel_layout": "7.1",
                "sample_rate": "48000",
            },
        ],
    }

    metadata = parse_ffprobe_technical_metadata(payload)

    assert metadata["container"] == "mkv"
    assert metadata["video_codec"] == "hevc"
    assert metadata["video_profile"] == "Main 10"
    assert metadata["pixel_format"] == "yuv420p10le"
    assert metadata["bit_depth"] == 10
    assert metadata["audio_codec"] == "truehd"
    assert metadata["audio_channels"] == 8
    assert metadata["audio_channel_layout"] == "7.1"


@pytest.mark.parametrize("color_transfer", ["smpte2084", "arib-std-b67"])
def test_parse_ffprobe_technical_metadata_detects_hdr_from_transfer(color_transfer: str) -> None:
    payload = {
        "format": {"format_name": "matroska,webm"},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "pix_fmt": "yuv420p10le",
                "width": 3840,
                "height": 2160,
                "color_transfer": color_transfer,
                "color_primaries": "bt2020",
                "color_space": "bt2020nc",
            }
        ],
    }

    metadata = parse_ffprobe_technical_metadata(payload)

    assert metadata["hdr_detected"] is True


def test_parse_ffprobe_technical_metadata_detects_dolby_vision_from_side_data() -> None:
    payload = {
        "format": {"format_name": "matroska,webm"},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "pix_fmt": "yuv420p10le",
                "width": 3840,
                "height": 2160,
                "side_data_list": [
                    {
                        "side_data_type": "DOVI configuration record",
                        "dv_profile": 8,
                    }
                ],
            }
        ],
    }

    metadata = parse_ffprobe_technical_metadata(payload)

    assert metadata["dolby_vision_detected"] is True


def test_parse_ffprobe_technical_metadata_counts_subtitle_streams() -> None:
    payload = {
        "format": {"format_name": "matroska,webm"},
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"},
            {"index": 1, "codec_type": "subtitle", "codec_name": "subrip"},
            {"index": 2, "codec_type": "subtitle", "codec_name": "ass"},
        ],
    }

    metadata = parse_ffprobe_technical_metadata(payload)

    assert metadata["subtitle_count"] == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"}, "streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"}]},
        {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"}, "streams": [{"codec_type": "audio", "codec_name": "aac", "channels": 2}]},
    ],
)
def test_parse_ffprobe_technical_metadata_tolerates_missing_audio_or_video(payload: dict[str, object]) -> None:
    metadata = parse_ffprobe_technical_metadata(payload)

    assert metadata["raw_probe_summary_json"] is not None


@pytest.mark.parametrize("payload", [{}, {"format": "not-a-dict", "streams": "not-a-list"}])
def test_parse_ffprobe_technical_metadata_tolerates_malformed_or_empty_payloads(payload: dict[str, object]) -> None:
    metadata = parse_ffprobe_technical_metadata(payload)

    assert metadata["container"] is None
    assert metadata["video_codec"] is None
    assert metadata["audio_codec"] is None
    assert metadata["subtitle_count"] == 0


def test_init_db_creates_media_item_technical_metadata_table_and_indexes(initialized_settings) -> None:
    with get_connection(initialized_settings) as connection:
        table = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'media_item_technical_metadata'
            """
        ).fetchone()
        indexes = {
            row["name"]
            for row in connection.execute("PRAGMA index_list(media_item_technical_metadata)").fetchall()
        }

    assert table is not None
    assert "idx_media_item_technical_metadata_probe_status" in indexes
    assert "idx_media_item_technical_metadata_probed_at" in indexes
    assert "idx_media_item_technical_metadata_source_fingerprint" in indexes
    assert "idx_media_item_technical_metadata_source" in indexes


def test_local_source_fingerprint_changes_when_size_or_mtime_changes() -> None:
    original = build_local_source_fingerprint(
        file_path="/tmp/movie.mkv",
        file_size=100,
        file_mtime_ns=1_000,
    )
    size_changed = build_local_source_fingerprint(
        file_path="/tmp/movie.mkv",
        file_size=101,
        file_mtime_ns=1_000,
    )
    mtime_changed = build_local_source_fingerprint(
        file_path="/tmp/movie.mkv",
        file_size=100,
        file_mtime_ns=1_001,
    )

    assert original != size_changed
    assert original != mtime_changed


def test_local_source_fingerprint_from_path_matches_explicit_components(tmp_path) -> None:
    media_file = tmp_path / "fingerprint.mkv"
    media_file.write_bytes(b"technical-metadata")
    stat_before = media_file.stat()

    expected = build_local_source_fingerprint(
        file_path=media_file,
        file_size=stat_before.st_size,
        file_mtime_ns=stat_before.st_mtime_ns,
    )
    actual = build_local_source_fingerprint_from_path(media_file)

    assert actual == expected


def test_parse_ffprobe_technical_metadata_is_pure_and_does_not_touch_source_files(tmp_path, monkeypatch) -> None:
    media_file = tmp_path / "movie.mkv"
    media_file.write_bytes(b"read-only media placeholder")
    stat_before = media_file.stat()

    def _unexpected_open(*args, **kwargs):
        raise AssertionError("parser should not open media files")

    monkeypatch.setattr("builtins.open", _unexpected_open)

    metadata = parse_ffprobe_technical_metadata(
        {
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"}],
        }
    )
    stat_after = media_file.stat()

    assert metadata["video_codec"] == "h264"
    assert stat_after.st_size == stat_before.st_size
    assert stat_after.st_mtime_ns == stat_before.st_mtime_ns


def test_local_technical_metadata_no_row_is_eligible(initialized_settings) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=8101,
        relative_name="technical/eligible.mp4",
    )
    _insert_media_item_record(initialized_settings, item)

    stored_item = get_media_item_record(initialized_settings, item_id=8101)
    assert stored_item is not None

    eligible, reason = should_probe_local_item(initialized_settings, stored_item)

    assert eligible is True
    assert reason == "no_metadata_row"


def test_local_technical_metadata_matching_probed_fingerprint_is_skipped(initialized_settings) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=8102,
        relative_name="technical/up_to_date.mp4",
    )
    _insert_media_item_record(initialized_settings, item)
    fingerprint = build_local_source_fingerprint_from_path(item["file_path"])
    upsert_technical_metadata(
        initialized_settings,
        media_item_id=8102,
        values={
            "metadata_version": 1,
            "metadata_source": "local_ffprobe",
            "probe_status": "probed",
            "probed_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
            "source_fingerprint": fingerprint,
        },
    )

    stored_item = get_media_item_record(initialized_settings, item_id=8102)
    assert stored_item is not None

    eligible, reason = should_probe_local_item(initialized_settings, stored_item)

    assert eligible is False
    assert reason == "fingerprint_unchanged"


def test_local_technical_metadata_changed_source_marks_stale_and_eligible(initialized_settings) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=8103,
        relative_name="technical/stale.mp4",
    )
    _insert_media_item_record(initialized_settings, item)
    fingerprint = build_local_source_fingerprint_from_path(item["file_path"])
    upsert_technical_metadata(
        initialized_settings,
        media_item_id=8103,
        values={
            "metadata_version": 1,
            "metadata_source": "local_ffprobe",
            "probe_status": "probed",
            "probed_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
            "source_fingerprint": fingerprint,
            "video_codec": "h264",
        },
    )

    media_file = Path(item["file_path"])
    media_file.write_bytes(b"changed-content")
    item["file_size"] = media_file.stat().st_size
    item["file_mtime"] = media_file.stat().st_mtime
    stored_item = get_media_item_record(initialized_settings, item_id=8103)
    assert stored_item is not None

    eligible, reason = should_probe_local_item(initialized_settings, stored_item)

    assert eligible is True
    assert reason == "source_fingerprint_changed"

    stale_row = mark_technical_metadata_stale(
        initialized_settings,
        media_item_id=8103,
        source_fingerprint=build_local_source_fingerprint_from_path(media_file),
    )
    assert stale_row["probe_status"] == "stale"


def test_local_technical_metadata_cloud_item_is_not_eligible_and_does_not_run_ffprobe(
    initialized_settings,
    monkeypatch,
) -> None:
    item = {
        "id": 8104,
        "title": "Cloud Technical Metadata",
        "original_filename": "cloud.mkv",
        "file_path": "gdrive://cloud-item",
        "source_kind": "cloud",
        "duration_seconds": 90.0,
        "container": "mkv",
        "video_codec": None,
        "audio_codec": None,
        "width": None,
        "height": None,
    }
    _insert_media_item_record(initialized_settings, item)

    def _unexpected_run(*args, **kwargs):
        raise AssertionError("cloud item should not invoke ffprobe")

    monkeypatch.setattr("backend.app.services.media_technical_metadata_service.subprocess.run", _unexpected_run)

    result = run_one_local_technical_metadata_enrichment(
        initialized_settings,
        media_item_id=8104,
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "cloud_source_not_supported"


def test_local_technical_metadata_successful_probe_writes_probed_row(initialized_settings, monkeypatch) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=8105,
        relative_name="technical/probed.mp4",
        video_codec=None,
        audio_codec=None,
        width=None,
        height=None,
    )
    _insert_media_item_record(initialized_settings, item)
    ffprobe_payload = {
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "91.25",
            "bit_rate": "3500000",
        },
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "profile": "High",
                "pix_fmt": "yuv420p",
                "width": 1920,
                "height": 1080,
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "channel_layout": "stereo",
                "sample_rate": "48000",
            },
        ],
    }

    class _Completed:
        returncode = 0
        stdout = json.dumps(ffprobe_payload)
        stderr = ""

    monkeypatch.setattr(
        "backend.app.services.media_technical_metadata_service.subprocess.run",
        lambda *args, **kwargs: _Completed(),
    )

    result = run_one_local_technical_metadata_enrichment(
        initialized_settings,
        media_item_id=8105,
    )
    row = get_technical_metadata(initialized_settings, 8105)

    assert result["status"] == "probed"
    assert row is not None
    assert row["probe_status"] == "probed"
    assert row["metadata_source"] == "local_ffprobe"
    assert row["video_codec"] == "h264"
    assert row["pixel_format"] == "yuv420p"
    assert row["bit_depth"] == 8
    assert row["audio_codec"] == "aac"
    assert row["audio_channels"] == 2


def test_local_technical_metadata_timeout_stores_failed_status(initialized_settings, monkeypatch) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=8106,
        relative_name="technical/timeout.mp4",
    )
    _insert_media_item_record(initialized_settings, item)

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=30)

    monkeypatch.setattr(
        "backend.app.services.media_technical_metadata_service.subprocess.run",
        _raise_timeout,
    )

    result = run_one_local_technical_metadata_enrichment(
        initialized_settings,
        media_item_id=8106,
    )
    row = get_technical_metadata(initialized_settings, 8106)

    assert result["status"] == "failed"
    assert result["reason"] == "timeout"
    assert row is not None
    assert row["probe_status"] == "failed"
    assert row["probe_error"] == "timeout"


def test_local_technical_metadata_invalid_json_stores_failed_status(initialized_settings, monkeypatch) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=8107,
        relative_name="technical/invalid-json.mp4",
    )
    _insert_media_item_record(initialized_settings, item)

    class _Completed:
        returncode = 0
        stdout = "{bad-json"
        stderr = ""

    monkeypatch.setattr(
        "backend.app.services.media_technical_metadata_service.subprocess.run",
        lambda *args, **kwargs: _Completed(),
    )

    result = run_one_local_technical_metadata_enrichment(
        initialized_settings,
        media_item_id=8107,
    )
    row = get_technical_metadata(initialized_settings, 8107)

    assert result["status"] == "failed"
    assert result["reason"] == "invalid_json"
    assert row is not None
    assert row["probe_status"] == "failed"
    assert row["probe_error"] == "invalid_json"


def test_local_technical_metadata_missing_file_stores_failed_status(initialized_settings) -> None:
    item = {
        "id": 8108,
        "title": "Missing Local File",
        "original_filename": "missing.mkv",
        "file_path": str(Path(initialized_settings.media_root) / "technical" / "missing.mkv"),
        "source_kind": "local",
        "duration_seconds": 100.0,
        "container": "mkv",
        "video_codec": "h264",
        "audio_codec": "aac",
        "width": 1920,
        "height": 1080,
    }
    _insert_media_item_record(initialized_settings, item)

    result = run_one_local_technical_metadata_enrichment(
        initialized_settings,
        media_item_id=8108,
    )
    row = get_technical_metadata(initialized_settings, 8108)

    assert result["status"] == "failed"
    assert result["reason"] == "missing_file"
    assert row is not None
    assert row["probe_status"] == "failed"
    assert row["probe_error"] == "missing_file"


def test_local_technical_metadata_probe_does_not_modify_local_file(initialized_settings, monkeypatch) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=8109,
        relative_name="technical/readonly.mp4",
    )
    _insert_media_item_record(initialized_settings, item)
    media_file = Path(item["file_path"])
    stat_before = media_file.stat()
    content_before = media_file.read_bytes()

    class _Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
                "streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"}],
            }
        )
        stderr = ""

    monkeypatch.setattr(
        "backend.app.services.media_technical_metadata_service.subprocess.run",
        lambda *args, **kwargs: _Completed(),
    )

    result = run_one_local_technical_metadata_enrichment(
        initialized_settings,
        media_item_id=8109,
    )
    stat_after = media_file.stat()
    content_after = media_file.read_bytes()

    assert result["status"] == "probed"
    assert stat_after.st_size == stat_before.st_size
    assert stat_after.st_mtime_ns == stat_before.st_mtime_ns
    assert content_after == content_before


def test_local_technical_metadata_failed_probe_does_not_break_library_or_playback_functions(
    initialized_settings,
    monkeypatch,
) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=8110,
        relative_name="technical/library-safe.mp4",
    )
    _insert_media_item_record(initialized_settings, item)

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=30)

    monkeypatch.setattr(
        "backend.app.services.media_technical_metadata_service.subprocess.run",
        _raise_timeout,
    )

    result = run_one_local_technical_metadata_enrichment(
        initialized_settings,
        media_item_id=8110,
    )
    stored_item = get_media_item_record(initialized_settings, item_id=8110)

    assert result["status"] == "failed"
    assert stored_item is not None

    decision = build_playback_decision(
        initialized_settings,
        stored_item,
        user_agent="Mozilla/5.0",
        transcode_manager=StubTranscodeManager(),
    )

    assert isinstance(decision, dict)
    assert decision["mode"] in {"direct", "hls"}


def test_local_technical_metadata_failed_probe_respects_backoff_unless_retry_requested(
    initialized_settings,
) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=8111,
        relative_name="technical/backoff.mp4",
    )
    _insert_media_item_record(initialized_settings, item)
    upsert_technical_metadata(
        initialized_settings,
        media_item_id=8111,
        values={
            "metadata_version": 1,
            "metadata_source": "local_ffprobe",
            "probe_status": "failed",
            "probe_error": "timeout",
            "probed_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
        },
    )

    stored_item = get_media_item_record(initialized_settings, item_id=8111)
    assert stored_item is not None

    skipped, skipped_reason = should_probe_local_item(initialized_settings, stored_item)
    retried, retried_reason = should_probe_local_item(
        initialized_settings,
        stored_item,
        retry_failed=True,
    )

    assert skipped is False
    assert skipped_reason == "failed_backoff_active"
    assert retried is True
    assert retried_reason == "retry_requested"


def test_local_technical_metadata_batch_probes_only_local_eligible_items_and_skips_cloud(
    initialized_settings,
    monkeypatch,
) -> None:
    eligible = _make_local_item(
        initialized_settings,
        item_id=8112,
        relative_name="technical/batch-eligible.mp4",
    )
    up_to_date = _make_local_item(
        initialized_settings,
        item_id=8113,
        relative_name="technical/batch-up-to-date.mp4",
    )
    cloud_item = {
        "id": 8114,
        "title": "Cloud Batch Item",
        "original_filename": "cloud-batch.mkv",
        "file_path": "gdrive://cloud-batch",
        "source_kind": "cloud",
        "duration_seconds": 90.0,
        "container": "mkv",
        "video_codec": None,
        "audio_codec": None,
        "width": None,
        "height": None,
    }
    _insert_media_item_record(initialized_settings, eligible)
    _insert_media_item_record(initialized_settings, up_to_date)
    _insert_media_item_record(initialized_settings, cloud_item)
    upsert_technical_metadata(
        initialized_settings,
        media_item_id=8113,
        values={
            "metadata_version": 1,
            "metadata_source": "local_ffprobe",
            "probe_status": "probed",
            "probed_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
            "source_fingerprint": build_local_source_fingerprint_from_path(up_to_date["file_path"]),
        },
    )

    ffprobe_payload = {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        "streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"}],
    }
    calls: list[list[str]] = []

    class _Completed:
        returncode = 0
        stdout = json.dumps(ffprobe_payload)
        stderr = ""

    def _fake_run(command, *args, **kwargs):
        calls.append(list(command))
        return _Completed()

    monkeypatch.setattr(
        "backend.app.services.media_technical_metadata_service.subprocess.run",
        _fake_run,
    )

    summary = run_local_technical_metadata_enrichment_batch(
        initialized_settings,
        limit=5,
    )

    assert summary["probed"] == 1
    assert summary["cloud_skipped"] == 1
    assert summary["skipped"] >= 1
    assert len(calls) == 1
    assert get_technical_metadata(initialized_settings, 8112)["probe_status"] == "probed"
    assert get_technical_metadata(initialized_settings, 8113)["probe_status"] == "probed"
    assert get_technical_metadata(initialized_settings, 8114) is None


def test_local_technical_metadata_batch_respects_limit(initialized_settings, monkeypatch) -> None:
    first = _make_local_item(
        initialized_settings,
        item_id=8115,
        relative_name="technical/batch-limit-a.mp4",
    )
    second = _make_local_item(
        initialized_settings,
        item_id=8116,
        relative_name="technical/batch-limit-b.mp4",
    )
    _insert_media_item_record(initialized_settings, first)
    _insert_media_item_record(initialized_settings, second)

    class _Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
                "streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"}],
            }
        )
        stderr = ""

    monkeypatch.setattr(
        "backend.app.services.media_technical_metadata_service.subprocess.run",
        lambda *args, **kwargs: _Completed(),
    )

    summary = run_local_technical_metadata_enrichment_batch(
        initialized_settings,
        limit=1,
    )

    assert summary["probed"] == 1
    rows = [get_technical_metadata(initialized_settings, 8115), get_technical_metadata(initialized_settings, 8116)]
    assert sum(1 for row in rows if row is not None and row["probe_status"] == "probed") == 1


def test_local_technical_metadata_batch_does_not_retry_recent_failed_rows_unless_requested(
    initialized_settings,
    monkeypatch,
) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=8117,
        relative_name="technical/batch-backoff.mp4",
    )
    _insert_media_item_record(initialized_settings, item)
    upsert_technical_metadata(
        initialized_settings,
        media_item_id=8117,
        values={
            "metadata_version": 1,
            "metadata_source": "local_ffprobe",
            "probe_status": "failed",
            "probe_error": "timeout",
            "probed_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
        },
    )

    def _unexpected_run(*args, **kwargs):
        raise AssertionError("recent failed rows should not be probed without retry_failed")

    monkeypatch.setattr(
        "backend.app.services.media_technical_metadata_service.subprocess.run",
        _unexpected_run,
    )

    skipped_summary = run_local_technical_metadata_enrichment_batch(
        initialized_settings,
        limit=5,
        retry_failed=False,
    )

    class _Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
                "streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"}],
            }
        )
        stderr = ""

    monkeypatch.setattr(
        "backend.app.services.media_technical_metadata_service.subprocess.run",
        lambda *args, **kwargs: _Completed(),
    )
    retried_summary = run_local_technical_metadata_enrichment_batch(
        initialized_settings,
        limit=5,
        retry_failed=True,
    )

    assert skipped_summary["probed"] == 0
    assert skipped_summary["skipped"] >= 1
    assert retried_summary["probed"] == 1


def test_local_technical_metadata_batch_changed_fingerprint_becomes_stale_and_eligible(
    initialized_settings,
    monkeypatch,
) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=8118,
        relative_name="technical/batch-stale.mp4",
    )
    _insert_media_item_record(initialized_settings, item)
    original_fingerprint = build_local_source_fingerprint_from_path(item["file_path"])
    upsert_technical_metadata(
        initialized_settings,
        media_item_id=8118,
        values={
            "metadata_version": 1,
            "metadata_source": "local_ffprobe",
            "probe_status": "probed",
            "probed_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
            "source_fingerprint": original_fingerprint,
            "video_codec": "h264",
        },
    )
    media_file = Path(item["file_path"])
    media_file.write_bytes(b"changed batch stale payload")

    class _Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
                "streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"}],
            }
        )
        stderr = ""

    monkeypatch.setattr(
        "backend.app.services.media_technical_metadata_service.subprocess.run",
        lambda *args, **kwargs: _Completed(),
    )

    summary = run_local_technical_metadata_enrichment_batch(
        initialized_settings,
        limit=5,
    )
    row = get_technical_metadata(initialized_settings, 8118)

    assert summary["stale"] >= 1
    assert summary["probed"] == 1
    assert row is not None
    assert row["probe_status"] == "probed"
    assert row["source_fingerprint"] != original_fingerprint


def test_local_technical_metadata_enrichment_status_counts_local_rows_correctly(initialized_settings) -> None:
    never_item = _make_local_item(
        initialized_settings,
        item_id=8119,
        relative_name="technical/status-never.mp4",
    )
    probed_item = _make_local_item(
        initialized_settings,
        item_id=8120,
        relative_name="technical/status-probed.mp4",
    )
    failed_item = _make_local_item(
        initialized_settings,
        item_id=8121,
        relative_name="technical/status-failed.mp4",
    )
    stale_item = _make_local_item(
        initialized_settings,
        item_id=8122,
        relative_name="technical/status-stale.mp4",
    )
    cloud_item = {
        "id": 8123,
        "title": "Cloud Status",
        "original_filename": "cloud-status.mkv",
        "file_path": "gdrive://cloud-status",
        "source_kind": "cloud",
        "duration_seconds": 60.0,
        "container": "mkv",
        "video_codec": None,
        "audio_codec": None,
        "width": None,
        "height": None,
    }
    for item in [never_item, probed_item, failed_item, stale_item, cloud_item]:
        _insert_media_item_record(initialized_settings, item)

    upsert_technical_metadata(
        initialized_settings,
        media_item_id=8120,
        values={
            "metadata_version": 1,
            "metadata_source": "local_ffprobe",
            "probe_status": "probed",
            "probed_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
            "source_fingerprint": build_local_source_fingerprint_from_path(probed_item["file_path"]),
        },
    )
    upsert_technical_metadata(
        initialized_settings,
        media_item_id=8121,
        values={
            "metadata_version": 1,
            "metadata_source": "local_ffprobe",
            "probe_status": "failed",
            "probed_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
        },
    )
    upsert_technical_metadata(
        initialized_settings,
        media_item_id=8122,
        values={
            "metadata_version": 1,
            "metadata_source": "local_ffprobe",
            "probe_status": "stale",
            "updated_at": utcnow_iso(),
        },
    )

    status_payload = get_local_technical_metadata_enrichment_status(initialized_settings)

    assert status_payload["total_local_items"] >= 4
    assert status_payload["probed_local_items"] >= 1
    assert status_payload["failed_local_items"] >= 1
    assert status_payload["stale_local_items"] >= 1
    assert status_payload["never_probed_local_items"] >= 1
    assert status_payload["cloud_items_not_supported"] >= 1
    assert status_payload["running"] is False


def test_local_technical_metadata_batch_probe_does_not_modify_local_file(initialized_settings, monkeypatch) -> None:
    item = _make_local_item(
        initialized_settings,
        item_id=8124,
        relative_name="technical/batch-readonly.mp4",
    )
    _insert_media_item_record(initialized_settings, item)
    media_file = Path(item["file_path"])
    stat_before = media_file.stat()
    content_before = media_file.read_bytes()

    class _Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
                "streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"}],
            }
        )
        stderr = ""

    monkeypatch.setattr(
        "backend.app.services.media_technical_metadata_service.subprocess.run",
        lambda *args, **kwargs: _Completed(),
    )

    summary = run_local_technical_metadata_enrichment_batch(
        initialized_settings,
        limit=1,
    )
    stat_after = media_file.stat()
    content_after = media_file.read_bytes()

    assert summary["probed"] == 1
    assert stat_after.st_size == stat_before.st_size
    assert stat_after.st_mtime_ns == stat_before.st_mtime_ns
    assert content_after == content_before


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


def test_route2_cpu_budget_summary_uses_global_budget_and_fair_user_share(initialized_settings, monkeypatch) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 20)

    item_a = _make_local_item(settings, item_id=301, relative_name="route2/cpu-a.mp4")
    item_b = _make_local_item(settings, item_id=302, relative_name="route2/cpu-b.mp4")
    item_c = _make_local_item(settings, item_id=303, relative_name="route2/cpu-c.mp4")

    manager.create_session(item_a, user_id=1, auth_session_id=101, username="alice", engine_mode="route2", playback_mode="lite")
    manager.create_session(item_b, user_id=2, auth_session_id=201, username="bob", engine_mode="route2", playback_mode="lite")

    summary = manager.get_route2_worker_status()
    assert summary["cpu_upbound_percent"] == 90
    assert summary["cpu_budget_percent"] == 90
    assert summary["total_cpu_cores"] == 20
    assert summary["route2_cpu_upbound_cores"] == 18
    assert summary["total_route2_budget_cores"] == 18
    assert summary["active_decoding_user_count"] == 2
    assert summary["per_user_budget_cores"] == 9

    manager.create_session(item_c, user_id=3, auth_session_id=301, username="carol", engine_mode="route2", playback_mode="lite")
    summary = manager.get_route2_worker_status()
    assert summary["active_decoding_user_count"] == 3
    assert summary["per_user_budget_cores"] == 6


def test_route2_cpu_upbound_env_alias_is_supported(monkeypatch, test_settings) -> None:
    monkeypatch.setenv("ELVERN_ROUTE2_CPU_UPBOUND_PERCENT", "91")
    monkeypatch.setenv("ELVERN_ROUTE2_CPU_BUDGET_PERCENT", "88")

    settings = refresh_settings()

    assert settings.route2_cpu_budget_percent == 91


@pytest.mark.parametrize(
    ("detected_cores", "expected_default"),
    [
        (20, 4),
        (2, 2),
    ],
)
def test_route2_max_worker_threads_default_is_min_four_or_detected_cores(
    monkeypatch,
    test_settings,
    detected_cores: int,
    expected_default: int,
) -> None:
    monkeypatch.delenv("ELVERN_ROUTE2_MAX_WORKER_THREADS", raising=False)
    monkeypatch.setattr("backend.app.config.os.cpu_count", lambda: detected_cores)

    settings = refresh_settings()

    assert settings.route2_max_worker_threads == expected_default


def test_route2_max_worker_threads_env_override_still_works(monkeypatch, test_settings) -> None:
    monkeypatch.setenv("ELVERN_ROUTE2_MAX_WORKER_THREADS", "6")
    monkeypatch.setattr("backend.app.config.os.cpu_count", lambda: 20)

    settings = refresh_settings()

    assert settings.route2_max_worker_threads == 6


def test_route2_adaptive_max_worker_threads_defaults_to_min_ten_or_detected_cores(
    monkeypatch,
    test_settings,
) -> None:
    monkeypatch.delenv("ELVERN_ROUTE2_ADAPTIVE_MAX_WORKER_THREADS", raising=False)
    monkeypatch.setattr("backend.app.config.os.cpu_count", lambda: 20)

    settings = refresh_settings()

    assert settings.route2_max_worker_threads == 4
    assert settings.route2_adaptive_max_worker_threads == 10
    assert settings.route2_protected_min_threads_per_active_user == 2


def test_route2_protected_min_threads_env_override_still_works(monkeypatch, test_settings) -> None:
    monkeypatch.setenv("ELVERN_ROUTE2_PROTECTED_MIN_THREADS_PER_ACTIVE_USER", "3")

    settings = refresh_settings()

    assert settings.route2_protected_min_threads_per_active_user == 3


def test_route2_adaptive_max_worker_threads_env_override_is_shadow_only_config(monkeypatch, test_settings) -> None:
    monkeypatch.setenv("ELVERN_ROUTE2_MAX_WORKER_THREADS", "4")
    monkeypatch.setenv("ELVERN_ROUTE2_ADAPTIVE_MAX_WORKER_THREADS", "10")
    monkeypatch.setattr("backend.app.config.os.cpu_count", lambda: 20)

    settings = refresh_settings()

    assert settings.route2_max_worker_threads == 4
    assert settings.route2_adaptive_max_worker_threads == 10


def test_route2_max_worker_threads_validation_still_rejects_invalid_values(monkeypatch, test_settings) -> None:
    monkeypatch.setenv("ELVERN_ROUTE2_MAX_WORKER_THREADS", "5")
    monkeypatch.setattr("backend.app.config.os.cpu_count", lambda: 4)

    with pytest.raises(ConfigError, match="ELVERN_ROUTE2_MAX_WORKER_THREADS"):
        refresh_settings()


def test_route2_protected_min_threads_validation_rejects_invalid_values(monkeypatch, test_settings) -> None:
    monkeypatch.setenv("ELVERN_ROUTE2_PROTECTED_MIN_THREADS_PER_ACTIVE_USER", "0")

    with pytest.raises(ConfigError, match="ELVERN_ROUTE2_PROTECTED_MIN_THREADS_PER_ACTIVE_USER"):
        refresh_settings()


def test_route2_protected_min_threads_validation_rejects_floor_above_real_max(monkeypatch, test_settings) -> None:
    monkeypatch.setenv("ELVERN_ROUTE2_MAX_WORKER_THREADS", "2")
    monkeypatch.setenv("ELVERN_ROUTE2_PROTECTED_MIN_THREADS_PER_ACTIVE_USER", "3")
    monkeypatch.setattr("backend.app.config.os.cpu_count", lambda: 8)

    with pytest.raises(ConfigError, match="ELVERN_ROUTE2_PROTECTED_MIN_THREADS_PER_ACTIVE_USER"):
        refresh_settings()


def test_route2_adaptive_max_worker_threads_validation_rejects_invalid_values(monkeypatch, test_settings) -> None:
    monkeypatch.setenv("ELVERN_ROUTE2_ADAPTIVE_MAX_WORKER_THREADS", "9")
    monkeypatch.setattr("backend.app.config.os.cpu_count", lambda: 8)

    with pytest.raises(ConfigError, match="ELVERN_ROUTE2_ADAPTIVE_MAX_WORKER_THREADS"):
        refresh_settings()


def test_route2_adaptive_shadow_provider_error_classifies_without_thread_increase() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            non_retryable_error="The download quota for this file has been exceeded.",
        )
    )

    assert decision.bottleneck_class == "PROVIDER_ERROR"
    assert decision.safe_to_increase_threads is False
    assert decision.recommended_threads == 4


def test_route2_adaptive_shadow_queued_worker_is_waiting_for_capacity() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            worker_state="queued",
            assigned_threads=0,
            cpu_cores_used=None,
        )
    )

    assert decision.bottleneck_class == "WAITING_FOR_CAPACITY"
    assert decision.bottleneck_confidence == pytest.approx(0.98)


def test_route2_adaptive_shadow_over_supplied_worker_recommends_same_or_lower_threads() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            supply_rate_x=1.8,
            ahead_runway_seconds=220.0,
            cpu_cores_used=2.0,
        )
    )

    assert decision.bottleneck_class == "OVER_SUPPLIED"
    assert decision.recommended_threads <= decision.current_threads
    assert decision.safe_to_decrease_threads is True


def test_route2_adaptive_shadow_early_bootstrap_is_unknown_not_storage_bound() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            source_kind="local",
            assigned_threads=4,
            cpu_cores_used=2.0,
            allocated_cpu_cores=18,
            user_cpu_cores_used_total=2.0,
            route2_cpu_upbound_cores=18,
            route2_cpu_cores_used_total=2.0,
            ahead_runway_seconds=0.0,
            supply_rate_x=0.0,
            supply_observation_seconds=0.0,
        )
    )

    assert decision.bottleneck_class == "UNKNOWN"
    assert "early_bootstrap_insufficient_samples" in decision.reason
    assert decision.recommended_threads == 4
    assert decision.safe_to_increase_threads is False


def test_route2_adaptive_shadow_current_four_promotes_to_benchmark_target_six() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            supply_rate_x=0.78,
            ahead_runway_seconds=70.0,
            supply_observation_seconds=20.0,
            cpu_cores_used=7.5,
            allocated_cpu_cores=18,
            user_cpu_cores_used_total=7.5,
            route2_cpu_upbound_cores=18,
            route2_cpu_cores_used_total=7.5,
            max_threads=4,
            adaptive_max_threads=12,
        )
    )

    assert decision.bottleneck_class == "CPU_BOUND"
    assert decision.safe_to_increase_threads is True
    assert decision.recommended_threads == 6
    assert "selected 6 as the first CPU-bound promotion target" in decision.reason
    assert "Real worker spawn is still capped at 4" in decision.reason


def test_route2_adaptive_shadow_current_five_promotes_to_benchmark_target_six() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            assigned_threads=5,
            supply_rate_x=0.78,
            ahead_runway_seconds=70.0,
            supply_observation_seconds=20.0,
            cpu_cores_used=7.5,
            allocated_cpu_cores=18,
            user_cpu_cores_used_total=7.5,
            route2_cpu_upbound_cores=18,
            route2_cpu_cores_used_total=7.5,
            max_threads=4,
            adaptive_max_threads=12,
        )
    )

    assert decision.bottleneck_class == "CPU_BOUND"
    assert decision.safe_to_increase_threads is True
    assert decision.recommended_threads == 6
    assert "selected 6 as the first CPU-bound promotion target" in decision.reason


def test_route2_adaptive_shadow_current_six_promotes_to_benchmark_target_nine() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            assigned_threads=6,
            supply_rate_x=0.78,
            ahead_runway_seconds=70.0,
            supply_observation_seconds=20.0,
            cpu_cores_used=7.5,
            allocated_cpu_cores=18,
            user_cpu_cores_used_total=7.5,
            route2_cpu_upbound_cores=18,
            route2_cpu_cores_used_total=7.5,
            max_threads=4,
            adaptive_max_threads=10,
        )
    )

    assert decision.bottleneck_class == "CPU_BOUND"
    assert decision.safe_to_increase_threads is True
    assert decision.recommended_threads == 9
    assert "6-8 often plateau" in decision.reason
    assert "Real worker spawn is still capped at 4" in decision.reason


def test_route2_adaptive_shadow_current_eight_promotes_to_benchmark_target_nine() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            assigned_threads=8,
            supply_rate_x=0.78,
            ahead_runway_seconds=70.0,
            supply_observation_seconds=20.0,
            cpu_cores_used=8.8,
            allocated_cpu_cores=18,
            user_cpu_cores_used_total=8.8,
            route2_cpu_upbound_cores=18,
            route2_cpu_cores_used_total=8.8,
            max_threads=4,
            adaptive_max_threads=10,
        )
    )

    assert decision.bottleneck_class == "CPU_BOUND"
    assert decision.safe_to_increase_threads is True
    assert decision.recommended_threads == 9
    assert "6-8 often plateau" in decision.reason


def test_route2_adaptive_shadow_high_external_cpu_blocks_promotion() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            supply_rate_x=0.78,
            ahead_runway_seconds=70.0,
            supply_observation_seconds=20.0,
            cpu_cores_used=7.5,
            allocated_cpu_cores=18,
            user_cpu_cores_used_total=7.5,
            route2_cpu_upbound_cores=18,
            route2_cpu_cores_used_total=7.5,
            max_threads=4,
            adaptive_max_threads=12,
            host_cpu_total_cores=20,
            host_cpu_used_cores=14.0,
            host_cpu_used_percent=0.70,
            external_cpu_cores_used_estimate=6.5,
            external_cpu_percent_estimate=0.325,
        )
    )

    assert decision.bottleneck_class == "UNDER_SUPPLIED_BUT_CPU_LIMITED"
    assert decision.safe_to_increase_threads is False
    assert decision.recommended_threads == 4
    assert "External host CPU pressure" in decision.reason
    assert "non-Elvern workload has priority" in decision.reason


def test_route2_adaptive_shadow_external_ffmpeg_blocks_nine_tier_promotion() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            assigned_threads=6,
            supply_rate_x=0.78,
            ahead_runway_seconds=70.0,
            supply_observation_seconds=20.0,
            cpu_cores_used=7.5,
            allocated_cpu_cores=18,
            user_cpu_cores_used_total=7.5,
            route2_cpu_upbound_cores=18,
            route2_cpu_cores_used_total=7.5,
            max_threads=4,
            adaptive_max_threads=12,
            external_cpu_cores_used_estimate=0.2,
            external_cpu_percent_estimate=0.01,
            external_ffmpeg_process_count=1,
        )
    )

    assert decision.bottleneck_class == "UNDER_SUPPLIED_BUT_CPU_LIMITED"
    assert decision.safe_to_increase_threads is False
    assert decision.recommended_threads == 6
    assert "External ffmpeg process detected" in decision.reason


def test_route2_adaptive_shadow_immature_host_sample_blocks_nine_tier_promotion() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            assigned_threads=6,
            supply_rate_x=0.78,
            ahead_runway_seconds=70.0,
            supply_observation_seconds=20.0,
            cpu_cores_used=7.5,
            allocated_cpu_cores=18,
            user_cpu_cores_used_total=7.5,
            route2_cpu_upbound_cores=18,
            route2_cpu_cores_used_total=7.5,
            max_threads=4,
            adaptive_max_threads=12,
            host_cpu_total_cores=None,
            host_cpu_used_cores=None,
            host_cpu_used_percent=None,
            external_cpu_cores_used_estimate=None,
            external_cpu_percent_estimate=None,
            host_cpu_sample_mature=False,
        )
    )

    assert decision.bottleneck_class == "UNDER_SUPPLIED_BUT_CPU_LIMITED"
    assert decision.safe_to_increase_threads is False
    assert decision.recommended_threads == 6
    assert "Host CPU pressure metrics are missing or immature" in decision.reason


def test_route2_adaptive_shadow_current_nine_holds_twelve_when_strict_conditions_do_not_pass() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            assigned_threads=9,
            supply_rate_x=0.92,
            ahead_runway_seconds=100.0,
            supply_observation_seconds=30.0,
            cpu_cores_used=9.2,
            allocated_cpu_cores=18,
            user_cpu_cores_used_total=9.2,
            route2_cpu_upbound_cores=18,
            route2_cpu_cores_used_total=9.2,
            max_threads=4,
            adaptive_max_threads=12,
            active_route2_user_count=1,
        )
    )

    assert decision.bottleneck_class == "UNDER_SUPPLIED_BUT_CPU_LIMITED"
    assert decision.safe_to_increase_threads is False
    assert decision.recommended_threads == 9
    assert "12 is a strict experimental heavy tier" in decision.reason


def test_route2_adaptive_shadow_external_ffmpeg_blocks_strict_twelve_tier() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            assigned_threads=9,
            supply_rate_x=0.78,
            ahead_runway_seconds=70.0,
            supply_observation_seconds=30.0,
            cpu_cores_used=9.2,
            allocated_cpu_cores=18,
            user_cpu_cores_used_total=9.2,
            route2_cpu_upbound_cores=18,
            route2_cpu_cores_used_total=9.2,
            max_threads=4,
            adaptive_max_threads=12,
            active_route2_user_count=1,
            external_cpu_cores_used_estimate=0.2,
            external_cpu_percent_estimate=0.01,
            external_ffmpeg_process_count=1,
        )
    )

    assert decision.bottleneck_class == "UNDER_SUPPLIED_BUT_CPU_LIMITED"
    assert decision.safe_to_increase_threads is False
    assert decision.recommended_threads == 9
    assert "External ffmpeg process detected" in decision.reason


def test_route2_adaptive_shadow_current_nine_promotes_to_twelve_when_strict_conditions_pass() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            assigned_threads=9,
            supply_rate_x=0.78,
            ahead_runway_seconds=70.0,
            supply_observation_seconds=30.0,
            cpu_cores_used=9.2,
            allocated_cpu_cores=18,
            user_cpu_cores_used_total=9.2,
            route2_cpu_upbound_cores=18,
            route2_cpu_cores_used_total=9.2,
            max_threads=4,
            adaptive_max_threads=12,
            active_route2_user_count=1,
        )
    )

    assert decision.bottleneck_class == "CPU_BOUND"
    assert decision.safe_to_increase_threads is True
    assert decision.recommended_threads == 12
    assert "Strict experimental 12-thread heavy tier conditions passed" in decision.reason


def test_route2_adaptive_shadow_cloud_current_nine_does_not_promote_to_twelve_by_default() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            source_kind="cloud",
            assigned_threads=9,
            supply_rate_x=0.78,
            ahead_runway_seconds=70.0,
            supply_observation_seconds=30.0,
            cpu_cores_used=9.2,
            allocated_cpu_cores=18,
            user_cpu_cores_used_total=9.2,
            route2_cpu_upbound_cores=18,
            route2_cpu_cores_used_total=9.2,
            max_threads=4,
            adaptive_max_threads=12,
            active_route2_user_count=1,
        )
    )

    assert decision.bottleneck_class == "UNDER_SUPPLIED_BUT_CPU_LIMITED"
    assert decision.safe_to_increase_threads is False
    assert decision.recommended_threads == 9
    assert "Cloud provider/source guard blocks 12-tier promotion" in decision.reason


def test_route2_adaptive_shadow_cpu_bound_at_adaptive_ceiling_does_not_increase() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            assigned_threads=10,
            supply_rate_x=0.78,
            ahead_runway_seconds=70.0,
            supply_observation_seconds=20.0,
            cpu_cores_used=10.2,
            allocated_cpu_cores=18,
            user_cpu_cores_used_total=10.2,
            route2_cpu_upbound_cores=18,
            route2_cpu_cores_used_total=10.2,
            max_threads=4,
            adaptive_max_threads=10,
        )
    )

    assert decision.bottleneck_class == "UNDER_SUPPLIED_BUT_CPU_LIMITED"
    assert decision.safe_to_increase_threads is False
    assert decision.recommended_threads == 10
    assert "adaptive recommendation ceiling" in decision.reason


def test_route2_adaptive_shadow_cpu_bound_without_spare_budget_stays_under_supplied_cpu_limited() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            supply_rate_x=0.74,
            ahead_runway_seconds=60.0,
            cpu_cores_used=6.9,
            allocated_cpu_cores=4,
            user_cpu_cores_used_total=4.0,
            route2_cpu_cores_used_total=18.0,
            adaptive_max_threads=8,
        )
    )

    assert decision.bottleneck_class == "UNDER_SUPPLIED_BUT_CPU_LIMITED"
    assert decision.safe_to_increase_threads is False
    assert decision.recommended_threads == 4


def test_route2_adaptive_shadow_cloud_low_supply_low_cpu_is_source_bound() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            source_kind="cloud",
            supply_rate_x=0.72,
            ahead_runway_seconds=50.0,
            cpu_cores_used=2.2,
            allocated_cpu_cores=8,
            user_cpu_cores_used_total=2.2,
            server_goodput_bytes_per_second=2_000_000.0,
            client_goodput_bytes_per_second=4_000_000.0,
        )
    )

    assert decision.bottleneck_class == "SOURCE_BOUND"
    assert decision.safe_to_increase_threads is False


def test_route2_adaptive_shadow_local_low_supply_low_cpu_is_storage_bound() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            source_kind="local",
            supply_rate_x=0.72,
            ahead_runway_seconds=50.0,
            cpu_cores_used=2.2,
            allocated_cpu_cores=8,
            user_cpu_cores_used_total=2.2,
        )
    )

    assert decision.bottleneck_class == "STORAGE_BOUND"
    assert decision.safe_to_increase_threads is False


def test_route2_adaptive_shadow_client_weak_while_backend_runway_is_healthy() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            supply_rate_x=1.35,
            ahead_runway_seconds=150.0,
            cpu_cores_used=3.5,
            stalled_recovery_needed=True,
        )
    )

    assert decision.bottleneck_class == "CLIENT_BOUND"
    assert decision.safe_to_increase_threads is False


def test_route2_adaptive_shadow_memory_guard_blocks_thread_increase() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            supply_rate_x=0.78,
            ahead_runway_seconds=70.0,
            supply_observation_seconds=20.0,
            cpu_cores_used=7.5,
            allocated_cpu_cores=18,
            user_cpu_cores_used_total=7.5,
            route2_cpu_upbound_cores=18,
            route2_cpu_cores_used_total=7.5,
            max_threads=8,
            adaptive_max_threads=8,
            total_memory_bytes=10 * 1024 * 1024 * 1024,
            route2_memory_bytes_total=8 * 1024 * 1024 * 1024,
        )
    )

    assert decision.bottleneck_class == "UNDER_SUPPLIED_BUT_CPU_LIMITED"
    assert decision.safe_to_increase_threads is False
    assert decision.recommended_threads == 4
    assert "memory pressure guard" in decision.reason


def test_route2_adaptive_shadow_insufficient_metrics_falls_back_to_unknown() -> None:
    decision = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            supply_rate_x=None,
            ahead_runway_seconds=None,
            cpu_cores_used=None,
        )
    )

    assert decision.bottleneck_class == "UNKNOWN"
    assert "supply_rate_x" in decision.missing_metrics
    assert "ahead_runway_seconds" in decision.missing_metrics


def test_route2_adaptive_shadow_decrease_clamps_to_min_and_real_max_no_longer_hides_current_threads() -> None:
    low = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            assigned_threads=2,
            supply_rate_x=2.0,
            ahead_runway_seconds=260.0,
            cpu_cores_used=1.0,
        )
    )
    high = classify_route2_adaptive_shadow(
        _make_route2_adaptive_input(
            assigned_threads=7,
            max_threads=6,
            supply_rate_x=0.7,
            ahead_runway_seconds=40.0,
            cpu_cores_used=6.6,
            allocated_cpu_cores=8,
            user_cpu_cores_used_total=8.0,
            route2_cpu_cores_used_total=8.0,
            adaptive_max_threads=8,
        )
    )

    assert low.recommended_threads == 2
    assert high.current_threads == 7
    assert high.recommended_threads == 7


def test_route2_adaptive_shadow_classifier_is_pure_value_helper(monkeypatch) -> None:
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected io")))

    decision = classify_route2_adaptive_shadow(_make_route2_adaptive_input())

    assert decision.current_threads == 4


def test_route2_worker_status_first_sample_is_unsampled_then_reports_live_cpu_and_memory(initialized_settings, monkeypatch) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_max_worker_threads=18,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 20)
    monkeypatch.setattr(
        "backend.app.services.mobile_playback_service._read_total_memory_bytes",
        lambda: 8 * 1024 * 1024 * 1024,
    )

    monotonic_values = iter([100.0, 102.0])
    monkeypatch.setattr(
        "backend.app.services.mobile_playback_service.time.monotonic",
        lambda: next(monotonic_values),
    )

    cpu_seconds_by_call = iter([10.0, 14.0])
    inspected_pids: list[int] = []

    def _fake_read_cpu_seconds(pid: int) -> float:
        inspected_pids.append(pid)
        return next(cpu_seconds_by_call)

    monkeypatch.setattr(
        "backend.app.services.mobile_playback_service._read_process_cpu_seconds",
        _fake_read_cpu_seconds,
    )
    monkeypatch.setattr(
        "backend.app.services.mobile_playback_service._read_process_rss_bytes",
        lambda pid: 512 * 1024 * 1024,
    )
    host_cpu_samples = iter(
        [
            _HostCpuJiffySample(total_jiffies=1_000, idle_jiffies=800, total_cpu_cores=20, sample_monotonic=100.0),
            _HostCpuJiffySample(total_jiffies=3_000, idle_jiffies=1_600, total_cpu_cores=20, sample_monotonic=102.0),
        ]
    )
    monkeypatch.setattr(
        "backend.app.services.mobile_playback_service._read_host_cpu_jiffy_sample",
        lambda *, sample_monotonic: next(host_cpu_samples),
    )
    monkeypatch.setattr(
        "backend.app.services.mobile_playback_service._count_external_ffmpeg_processes",
        lambda *, owned_route2_pids: 1,
    )

    item_a = _make_local_item(settings, item_id=304, relative_name="route2/live-a.mp4")
    item_b = _make_local_item(settings, item_id=305, relative_name="route2/live-b.mp4")
    running_payload = manager.create_session(
        item_a,
        user_id=1,
        auth_session_id=111,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
    )
    manager.create_session(
        item_b,
        user_id=2,
        auth_session_id=222,
        username="bob",
        engine_mode="route2",
        playback_mode="lite",
    )

    with manager._lock:
        session = manager._sessions[running_payload["session_id"]]
        active_epoch = session.browser_playback.epochs[session.browser_playback.active_epoch_id]
        active_epoch.process = _TelemetryProcess(pid=4321, running=True)
        record = manager._ensure_route2_worker_record_locked(session, active_epoch)
        record.state = "running"
        record.assigned_threads = 4

    first = manager.get_route2_worker_status()
    first_user = next(group for group in first["workers_by_user"] if group["user_id"] == 1)
    first_item = first_user["items"][0]
    assert first["route2_cpu_cores_used"] is None
    assert first["route2_cpu_percent_of_total"] is None
    assert first["host_cpu_sample_mature"] is False
    assert first["external_ffmpeg_process_count"] == 1
    assert first_item["telemetry_sampled"] is False
    assert first_item["cpu_cores_used"] is None
    assert first_item["memory_bytes"] == 512 * 1024 * 1024
    assert first_item["memory_percent_of_total"] == pytest.approx(6.25)

    second = manager.get_route2_worker_status()
    second_user = next(group for group in second["workers_by_user"] if group["user_id"] == 1)
    second_item = second_user["items"][0]
    assert second["route2_cpu_cores_used"] == pytest.approx(2.0)
    assert second["route2_cpu_percent_of_total"] == pytest.approx(10.0)
    assert second["route2_cpu_percent_of_upbound"] == pytest.approx(11.111, rel=1e-3)
    assert second["host_cpu_sample_mature"] is True
    assert second["host_cpu_used_cores"] == pytest.approx(6.0)
    assert second["host_cpu_used_percent"] == pytest.approx(0.3)
    assert second["external_cpu_cores_used_estimate"] == pytest.approx(4.0)
    assert second["external_cpu_percent_estimate"] == pytest.approx(0.2)
    assert second["external_ffmpeg_process_count"] == 1
    assert second["route2_memory_bytes"] == 512 * 1024 * 1024
    assert second["route2_memory_percent_of_total"] == pytest.approx(6.25)
    assert second_user["allocated_cpu_cores"] == 9
    assert second_user["allocated_budget_cores"] == 9
    assert second_user["cpu_cores_used"] == pytest.approx(2.0)
    assert second_user["cpu_percent_of_user_limit"] == pytest.approx(22.222, rel=1e-3)
    assert second_user["memory_bytes"] == 512 * 1024 * 1024
    assert second_user["memory_percent_of_total"] == pytest.approx(6.25)
    assert second_item["process_exists"] is True
    assert second_item["telemetry_sampled"] is True
    assert second_item["cpu_cores_used"] == pytest.approx(2.0)
    assert second_item["cpu_percent_of_total"] == pytest.approx(10.0)
    assert second_item["memory_bytes"] == 512 * 1024 * 1024
    assert isinstance(second_item["adaptive_bottleneck_class"], str)
    assert second_item["adaptive_recommended_threads"] is not None
    assert isinstance(second_item["adaptive_missing_metrics"], list)
    assert isinstance(second_item["route2_transcode_strategy"], str)
    assert isinstance(second_item["route2_strategy_risk_flags"], list)
    assert isinstance(second_item["route2_strategy_missing_metadata"], list)
    assert inspected_pids == [4321, 4321]
    with manager._lock:
        assert manager._route2_workers[record.worker_id].assigned_threads == 4


def test_route2_worker_status_handles_exited_owned_worker_without_crashing(initialized_settings, monkeypatch) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 20)
    monkeypatch.setattr(
        "backend.app.services.mobile_playback_service._read_total_memory_bytes",
        lambda: 8 * 1024 * 1024 * 1024,
    )

    item = _make_local_item(settings, item_id=306, relative_name="route2/exited.mp4")
    payload = manager.create_session(
        item,
        user_id=1,
        auth_session_id=333,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
    )

    with manager._lock:
        session = manager._sessions[payload["session_id"]]
        active_epoch = session.browser_playback.epochs[session.browser_playback.active_epoch_id]
        active_epoch.process = _TelemetryProcess(pid=9876, running=False)
        record = manager._ensure_route2_worker_record_locked(session, active_epoch)
        record.state = "running"
        record.assigned_threads = 2
        record.started_at = "2026-01-01T00:00:00+00:00"

    summary = manager.get_route2_worker_status()
    user_group = next(group for group in summary["workers_by_user"] if group["user_id"] == 1)
    item_payload = user_group["items"][0]

    assert item_payload["process_exists"] is False
    assert item_payload["telemetry_sampled"] is False
    assert item_payload["cpu_cores_used"] is None
    assert item_payload["memory_bytes"] is None
    assert item_payload["state"] == "interrupted"


def test_route2_rss_parser_reads_vmrss_kib() -> None:
    payload = "Name:\tffmpeg\nVmRSS:\t  524288 kB\nThreads:\t8\n"

    assert _parse_proc_status_rss_bytes(payload) == 524288 * 1024


def test_route2_cpu_stat_parser_reads_user_and_system_ticks(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.services.mobile_playback_service._clock_ticks_per_second",
        lambda: 100,
    )
    payload = "4321 (ffmpeg worker) S 1 2 3 4 5 6 7 8 9 10 250 75 0 0 20 0 1 0 12345"

    assert _parse_proc_stat_cpu_seconds(payload) == pytest.approx(3.25)


def test_route2_host_cpu_jiffy_parser_reads_aggregate_cpu_line() -> None:
    payload = "cpu  100 20 30 850 10 0 0 0 0 0\ncpu0 50 0 10 400 0 0 0 0 0 0\n"

    assert _parse_proc_stat_host_cpu_jiffies(payload) == (1010, 860)


def test_route2_host_cpu_pressure_first_sample_is_immature() -> None:
    snapshot = _build_host_cpu_pressure_snapshot(
        previous_sample=None,
        current_sample=_HostCpuJiffySample(
            total_jiffies=1_000,
            idle_jiffies=800,
            total_cpu_cores=20,
            sample_monotonic=100.0,
        ),
        route2_cpu_cores_used_total=0.0,
        external_ffmpeg_process_count=0,
    )

    assert snapshot.host_cpu_sample_mature is False
    assert snapshot.host_cpu_total_cores == 20
    assert snapshot.host_cpu_used_cores is None
    assert snapshot.external_cpu_cores_used_estimate is None


def test_route2_host_cpu_pressure_computes_mature_usage_and_external_estimate(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.services.mobile_playback_service._clock_ticks_per_second",
        lambda: 100,
    )

    snapshot = _build_host_cpu_pressure_snapshot(
        previous_sample=_HostCpuJiffySample(
            total_jiffies=1_000,
            idle_jiffies=800,
            total_cpu_cores=20,
            sample_monotonic=100.0,
        ),
        current_sample=_HostCpuJiffySample(
            total_jiffies=3_000,
            idle_jiffies=1_600,
            total_cpu_cores=20,
            sample_monotonic=102.0,
        ),
        route2_cpu_cores_used_total=2.0,
        external_ffmpeg_process_count=0,
    )

    assert snapshot.host_cpu_sample_mature is True
    assert snapshot.host_cpu_used_cores == pytest.approx(6.0)
    assert snapshot.host_cpu_used_percent == pytest.approx(0.3)
    assert snapshot.external_cpu_cores_used_estimate == pytest.approx(4.0)
    assert snapshot.external_cpu_percent_estimate == pytest.approx(0.2)


def test_route2_external_cpu_estimate_never_goes_negative(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.services.mobile_playback_service._clock_ticks_per_second",
        lambda: 100,
    )

    snapshot = _build_host_cpu_pressure_snapshot(
        previous_sample=_HostCpuJiffySample(
            total_jiffies=1_000,
            idle_jiffies=800,
            total_cpu_cores=20,
            sample_monotonic=100.0,
        ),
        current_sample=_HostCpuJiffySample(
            total_jiffies=3_000,
            idle_jiffies=1_600,
            total_cpu_cores=20,
            sample_monotonic=102.0,
        ),
        route2_cpu_cores_used_total=9.0,
        external_ffmpeg_process_count=0,
    )

    assert snapshot.host_cpu_used_cores == pytest.approx(6.0)
    assert snapshot.external_cpu_cores_used_estimate == pytest.approx(0.0)
    assert snapshot.external_cpu_percent_estimate == pytest.approx(0.0)


def test_route2_external_ffmpeg_detector_excludes_owned_route2_pids(tmp_path: Path) -> None:
    for pid, comm in {
        100: "ffmpeg\n",
        101: "ffprobe\n",
        102: "python\n",
        103: "ffmpeg\n",
    }.items():
        proc_dir = tmp_path / str(pid)
        proc_dir.mkdir()
        (proc_dir / "comm").write_text(comm, encoding="utf-8")
    (tmp_path / "self").mkdir()

    assert _count_external_ffmpeg_processes(proc_root=tmp_path, owned_route2_pids={100}) == 2


def test_route2_external_ffmpeg_detector_uses_comm_without_cmdline(tmp_path: Path) -> None:
    proc_dir = tmp_path / "200"
    proc_dir.mkdir()
    (proc_dir / "comm").write_text("ffmpeg\n", encoding="utf-8")

    assert _count_external_ffmpeg_processes(proc_root=tmp_path, owned_route2_pids=set()) == 1


def test_route2_stale_resource_snapshot_marks_adaptive_input_immature(initialized_settings, monkeypatch) -> None:
    manager, _settings = _make_route2_manager(initialized_settings)
    record = Route2WorkerRecord(
        worker_id="worker-a",
        session_id="missing-session",
        epoch_id="epoch-a",
        user_id=1,
        username="alice",
        auth_session_id=11,
        media_item_id=501,
        title="Telemetry Test",
        playback_mode="full",
        profile="mobile_1080p",
        source_kind="local",
        target_position_seconds=0.0,
        state="running",
        assigned_threads=6,
    )
    manager._route2_resource_snapshot = _Route2ResourceSnapshot(
        sampled_at_ts=100.0,
        sampled_at="2026-01-01T00:00:00+00:00",
        sample_mature=True,
        sample_stale=False,
        host_cpu_total_cores=20,
        host_cpu_used_cores=8.0,
        host_cpu_used_percent=0.4,
        route2_cpu_cores_used_total=4.0,
        route2_cpu_percent_of_host=20.0,
        per_user_cpu_cores_used_total={1: 4.0},
        total_memory_bytes=8 * 1024 * 1024 * 1024,
        route2_memory_bytes_total=512 * 1024 * 1024,
        route2_memory_percent_of_total=6.25,
        external_cpu_cores_used_estimate=0.5,
        external_cpu_percent_estimate=0.025,
        external_ffmpeg_process_count=0,
        external_ffmpeg_cpu_cores_estimate=None,
        external_pressure_level="none",
        missing_metrics=[],
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.time.time", lambda: 106.0)

    adaptive_input = manager._build_route2_adaptive_shadow_input_locked(
        record,
        allocated_cpu_cores=18,
        user_cpu_cores_used_total=None,
        route2_cpu_cores_used_total=None,
        route2_cpu_upbound_cores=18,
        active_route2_user_count=1,
        host_cpu_pressure=_HostCpuPressureSnapshot(
            host_cpu_total_cores=20,
            host_cpu_used_cores=1.0,
            host_cpu_used_percent=0.05,
            external_cpu_cores_used_estimate=0.0,
            external_cpu_percent_estimate=0.0,
            external_ffmpeg_process_count=0,
            external_ffmpeg_cpu_cores_estimate=None,
            host_cpu_sample_mature=True,
        ),
        total_memory_bytes=None,
        route2_memory_bytes_total=None,
    )

    assert manager._route2_resource_snapshot.sample_stale is True
    assert adaptive_input.host_cpu_sample_mature is False


def test_route2_fresh_resource_snapshot_feeds_adaptive_input(initialized_settings, monkeypatch) -> None:
    manager, _settings = _make_route2_manager(initialized_settings)
    record = Route2WorkerRecord(
        worker_id="worker-b",
        session_id="missing-session",
        epoch_id="epoch-b",
        user_id=7,
        username="bob",
        auth_session_id=77,
        media_item_id=502,
        title="Telemetry Test",
        playback_mode="full",
        profile="mobile_1080p",
        source_kind="local",
        target_position_seconds=0.0,
        state="running",
        assigned_threads=6,
    )
    manager._route2_resource_snapshot = _Route2ResourceSnapshot(
        sampled_at_ts=100.0,
        sampled_at="2026-01-01T00:00:00+00:00",
        sample_mature=True,
        sample_stale=False,
        host_cpu_total_cores=20,
        host_cpu_used_cores=7.5,
        host_cpu_used_percent=0.375,
        route2_cpu_cores_used_total=5.5,
        route2_cpu_percent_of_host=27.5,
        per_user_cpu_cores_used_total={7: 5.5},
        total_memory_bytes=16 * 1024 * 1024 * 1024,
        route2_memory_bytes_total=3 * 1024 * 1024 * 1024,
        route2_memory_percent_of_total=18.75,
        external_cpu_cores_used_estimate=2.0,
        external_cpu_percent_estimate=0.1,
        external_ffmpeg_process_count=1,
        external_ffmpeg_cpu_cores_estimate=None,
        external_pressure_level="moderate",
        missing_metrics=[],
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.time.time", lambda: 101.0)

    adaptive_input = manager._build_route2_adaptive_shadow_input_locked(
        record,
        allocated_cpu_cores=18,
        user_cpu_cores_used_total=None,
        route2_cpu_cores_used_total=None,
        route2_cpu_upbound_cores=18,
        active_route2_user_count=1,
        host_cpu_pressure=_HostCpuPressureSnapshot(
            host_cpu_total_cores=None,
            host_cpu_used_cores=None,
            host_cpu_used_percent=None,
            external_cpu_cores_used_estimate=None,
            external_cpu_percent_estimate=None,
            external_ffmpeg_process_count=0,
            external_ffmpeg_cpu_cores_estimate=None,
            host_cpu_sample_mature=False,
        ),
        total_memory_bytes=None,
        route2_memory_bytes_total=None,
    )

    assert adaptive_input.host_cpu_sample_mature is True
    assert adaptive_input.host_cpu_used_cores == pytest.approx(7.5)
    assert adaptive_input.external_cpu_cores_used_estimate == pytest.approx(2.0)
    assert adaptive_input.external_ffmpeg_process_count == 1
    assert adaptive_input.user_cpu_cores_used_total == pytest.approx(5.5)
    assert adaptive_input.route2_cpu_cores_used_total == pytest.approx(5.5)
    assert adaptive_input.total_memory_bytes == 16 * 1024 * 1024 * 1024
    assert adaptive_input.route2_memory_bytes_total == 3 * 1024 * 1024 * 1024


def test_route2_adaptive_spawn_dry_run_local_single_user_recommends_six(initialized_settings) -> None:
    manager, _settings = _make_route2_manager(
        initialized_settings,
        route2_max_worker_threads=4,
        route2_adaptive_max_worker_threads=12,
    )
    _set_route2_resource_snapshot(manager, per_user_cpu_cores_used_total={1: 0.0})
    record = _make_route2_worker_record_for_spawn_dry_run(source_kind="local", user_id=1)

    decision = manager._build_route2_adaptive_spawn_dry_run_locked(
        record,
        fixed_assigned_threads=4,
        available_total_threads=18,
        user_remaining_threads=18,
        allocated_cpu_cores=18,
        route2_cpu_upbound_cores=18,
        active_route2_user_count=1,
    )

    assert decision.recommended_threads == 6
    assert decision.blockers == []
    assert "would choose 6" in decision.reason
    assert decision.sample_mature is True


def test_route2_adaptive_spawn_dry_run_multi_user_remains_conservative(initialized_settings) -> None:
    manager, _settings = _make_route2_manager(initialized_settings, route2_adaptive_max_worker_threads=12)
    _set_route2_resource_snapshot(manager, per_user_cpu_cores_used_total={1: 0.0})
    record = _make_route2_worker_record_for_spawn_dry_run(source_kind="local", user_id=1)

    decision = manager._build_route2_adaptive_spawn_dry_run_locked(
        record,
        fixed_assigned_threads=4,
        available_total_threads=9,
        user_remaining_threads=9,
        allocated_cpu_cores=9,
        route2_cpu_upbound_cores=18,
        active_route2_user_count=2,
    )

    assert decision.recommended_threads == 4
    assert "not_single_user_route2_workload" in decision.blockers
    assert "not a single-user workload" in decision.reason


def test_route2_adaptive_spawn_dry_run_cloud_remains_deferred(initialized_settings) -> None:
    manager, _settings = _make_route2_manager(initialized_settings, route2_adaptive_max_worker_threads=12)
    _set_route2_resource_snapshot(manager, per_user_cpu_cores_used_total={1: 0.0})
    record = _make_route2_worker_record_for_spawn_dry_run(source_kind="cloud", user_id=1)

    decision = manager._build_route2_adaptive_spawn_dry_run_locked(
        record,
        fixed_assigned_threads=4,
        available_total_threads=18,
        user_remaining_threads=18,
        allocated_cpu_cores=18,
        route2_cpu_upbound_cores=18,
        active_route2_user_count=1,
    )

    assert decision.recommended_threads == 4
    assert "cloud_adaptive_spawn_deferred" in decision.blockers
    assert "cloud real adaptive initial spawn is deferred" in decision.reason


def test_route2_adaptive_spawn_dry_run_external_cpu_remains_conservative(initialized_settings) -> None:
    manager, _settings = _make_route2_manager(initialized_settings, route2_adaptive_max_worker_threads=12)
    _set_route2_resource_snapshot(
        manager,
        host_cpu_used_cores=14.0,
        host_cpu_used_percent=0.70,
        external_cpu_cores_used_estimate=5.0,
        external_cpu_percent_estimate=0.25,
        external_pressure_level="high",
        per_user_cpu_cores_used_total={1: 0.0},
    )
    record = _make_route2_worker_record_for_spawn_dry_run(source_kind="local", user_id=1)

    decision = manager._build_route2_adaptive_spawn_dry_run_locked(
        record,
        fixed_assigned_threads=4,
        available_total_threads=18,
        user_remaining_threads=18,
        allocated_cpu_cores=18,
        route2_cpu_upbound_cores=18,
        active_route2_user_count=1,
    )

    assert decision.recommended_threads == 4
    assert "external_host_cpu_pressure_high" in decision.blockers
    assert "external host CPU pressure is high" in decision.reason


def test_route2_adaptive_spawn_dry_run_external_ffmpeg_remains_conservative(initialized_settings) -> None:
    manager, _settings = _make_route2_manager(initialized_settings, route2_adaptive_max_worker_threads=12)
    _set_route2_resource_snapshot(
        manager,
        external_ffmpeg_process_count=1,
        external_pressure_level="moderate",
        per_user_cpu_cores_used_total={1: 0.0},
    )
    record = _make_route2_worker_record_for_spawn_dry_run(source_kind="local", user_id=1)

    decision = manager._build_route2_adaptive_spawn_dry_run_locked(
        record,
        fixed_assigned_threads=4,
        available_total_threads=18,
        user_remaining_threads=18,
        allocated_cpu_cores=18,
        route2_cpu_upbound_cores=18,
        active_route2_user_count=1,
    )

    assert decision.recommended_threads == 4
    assert "external_ffmpeg_detected" in decision.blockers
    assert "external ffmpeg/ffprobe is present" in decision.reason


def test_route2_adaptive_spawn_dry_run_stale_or_missing_telemetry_remains_conservative(initialized_settings) -> None:
    manager, _settings = _make_route2_manager(initialized_settings, route2_adaptive_max_worker_threads=12)
    _set_route2_resource_snapshot(
        manager,
        sampled_at_ts=time.time() - 10.0,
        per_user_cpu_cores_used_total={1: 0.0},
    )
    record = _make_route2_worker_record_for_spawn_dry_run(source_kind="local", user_id=1)

    stale_decision = manager._build_route2_adaptive_spawn_dry_run_locked(
        record,
        fixed_assigned_threads=4,
        available_total_threads=18,
        user_remaining_threads=18,
        allocated_cpu_cores=18,
        route2_cpu_upbound_cores=18,
        active_route2_user_count=1,
    )
    manager._route2_resource_snapshot = None
    missing_decision = manager._build_route2_adaptive_spawn_dry_run_locked(
        record,
        fixed_assigned_threads=4,
        available_total_threads=18,
        user_remaining_threads=18,
        allocated_cpu_cores=18,
        route2_cpu_upbound_cores=18,
        active_route2_user_count=1,
    )

    assert stale_decision.recommended_threads == 4
    assert "telemetry_missing_or_stale" in stale_decision.blockers
    assert "resource telemetry is missing, immature, or stale" in stale_decision.reason
    assert missing_decision.recommended_threads == 4
    assert "telemetry_missing_or_stale" in missing_decision.blockers


def test_route2_adaptive_spawn_dry_run_ram_pressure_remains_conservative(initialized_settings) -> None:
    manager, _settings = _make_route2_manager(initialized_settings, route2_adaptive_max_worker_threads=12)
    _set_route2_resource_snapshot(
        manager,
        total_memory_bytes=10 * 1024 * 1024 * 1024,
        route2_memory_bytes_total=9 * 1024 * 1024 * 1024,
        per_user_cpu_cores_used_total={1: 0.0},
    )
    record = _make_route2_worker_record_for_spawn_dry_run(source_kind="local", user_id=1)

    decision = manager._build_route2_adaptive_spawn_dry_run_locked(
        record,
        fixed_assigned_threads=4,
        available_total_threads=18,
        user_remaining_threads=18,
        allocated_cpu_cores=18,
        route2_cpu_upbound_cores=18,
        active_route2_user_count=1,
    )

    assert decision.recommended_threads == 4
    assert "route2_memory_pressure" in decision.blockers
    assert "memory pressure" in decision.reason


def test_route2_adaptive_spawn_dry_run_respects_adaptive_max_below_six(initialized_settings) -> None:
    manager, _settings = _make_route2_manager(initialized_settings, route2_adaptive_max_worker_threads=5)
    _set_route2_resource_snapshot(manager, per_user_cpu_cores_used_total={1: 0.0})
    record = _make_route2_worker_record_for_spawn_dry_run(source_kind="local", user_id=1)

    decision = manager._build_route2_adaptive_spawn_dry_run_locked(
        record,
        fixed_assigned_threads=4,
        available_total_threads=18,
        user_remaining_threads=18,
        allocated_cpu_cores=18,
        route2_cpu_upbound_cores=18,
        active_route2_user_count=1,
    )

    assert decision.recommended_threads == 5
    assert "caps the first-tier target below 6" in decision.reason


def test_route2_adaptive_spawn_dry_run_respects_route2_min_worker_threads(initialized_settings) -> None:
    manager, _settings = _make_route2_manager(
        initialized_settings,
        route2_min_worker_threads=7,
        route2_max_worker_threads=7,
        route2_adaptive_max_worker_threads=12,
    )
    _set_route2_resource_snapshot(manager, per_user_cpu_cores_used_total={1: 0.0})
    record = _make_route2_worker_record_for_spawn_dry_run(source_kind="local", user_id=1)

    decision = manager._build_route2_adaptive_spawn_dry_run_locked(
        record,
        fixed_assigned_threads=7,
        available_total_threads=18,
        user_remaining_threads=18,
        allocated_cpu_cores=18,
        route2_cpu_upbound_cores=18,
        active_route2_user_count=1,
    )

    assert decision.recommended_threads == 7
    assert decision.recommended_threads >= manager.settings.route2_min_worker_threads


def test_route2_dispatch_stores_spawn_dry_run_without_changing_assigned_threads(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=4,
        route2_adaptive_max_worker_threads=12,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 20)
    _set_route2_resource_snapshot(manager, per_user_cpu_cores_used_total={1: 0.0})
    started_workers: list[tuple[str, str, str]] = []

    class _FakeThread:
        def __init__(self, *, target, args, daemon, name):
            self.args = args

        def start(self) -> None:
            started_workers.append(self.args)

    monkeypatch.setattr("backend.app.services.mobile_playback_service.threading.Thread", _FakeThread)

    item = _make_local_item(settings, item_id=313, relative_name="route2/spawn-dry-run.mp4")
    payload = manager.create_session(
        item,
        user_id=1,
        auth_session_id=801,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
    )

    manager._dispatch_waiting_sessions()

    with manager._lock:
        session = manager._sessions[payload["session_id"]]
        active_epoch = session.browser_playback.epochs[session.browser_playback.active_epoch_id]
        record = manager._route2_workers[active_epoch.active_worker_id]
        assert record.assigned_threads == 4
        assert record.fixed_assigned_threads_at_dispatch == 4
        assert record.adaptive_spawn_dry_run_enabled is True
        assert record.adaptive_spawn_dry_run_threads == 6
        assert record.adaptive_spawn_dry_run_source == "initial_spawn"
        assert record.adaptive_spawn_dry_run_sample_mature is True
    status = manager.get_route2_worker_status()
    user_group = next(group for group in status["workers_by_user"] if group["user_id"] == 1)
    item_payload = user_group["items"][0]
    assert item_payload["assigned_threads"] == 4
    assert item_payload["fixed_assigned_threads_at_dispatch"] == 4
    assert item_payload["adaptive_spawn_dry_run_enabled"] is True
    assert item_payload["adaptive_spawn_dry_run_threads"] == 6
    assert item_payload["adaptive_spawn_dry_run_source"] == "initial_spawn"
    assert len(started_workers) == 1


def test_route2_user_with_many_jobs_queues_instead_of_rejecting(initialized_settings, monkeypatch) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=50,
        route2_max_worker_threads=2,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 4)

    started_workers: list[tuple[str, str, str]] = []

    class _FakeThread:
        def __init__(self, *, target, args, daemon, name):
            self.target = target
            self.args = args
            self.daemon = daemon
            self.name = name

        def start(self) -> None:
            started_workers.append(self.args)

    monkeypatch.setattr("backend.app.services.mobile_playback_service.threading.Thread", _FakeThread)

    item_a = _make_local_item(settings, item_id=311, relative_name="route2/active-a.mp4")
    item_b = _make_local_item(settings, item_id=312, relative_name="route2/active-b.mp4")

    first = manager.create_session(
        item_a,
        user_id=1,
        auth_session_id=401,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
    )

    manager._dispatch_waiting_sessions()

    with pytest.raises(ActivePlaybackWorkerConflictError) as conflict:
        manager.create_session(
            item_b,
            user_id=1,
            auth_session_id=401,
            username="alice",
            engine_mode="route2",
            playback_mode="lite",
        )

    detail = conflict.value.detail
    assert detail["code"] == "same_user_active_playback_limit"
    assert detail["legacy_code"] == "active_playback_worker_exists"
    assert detail["message"] == "You already have an active playback. Stop it or switch before starting another."
    assert detail["active_media_item_id"] == int(item_a["id"])
    assert detail["active_session_id"] == first["session_id"]
    assert len(manager._route2_session_ids_by_user[1]) == 1
    assert len(started_workers) == 1
    assert len([record for record in manager._route2_workers.values() if record.state == "running"]) == 1
    assert len([record for record in manager._route2_workers.values() if record.state == "queued"]) == 0


def test_route2_worker_scheduler_does_not_exceed_budget_derived_capacity(initialized_settings, monkeypatch) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=50,
        route2_min_worker_threads=1,
        route2_max_worker_threads=1,
        route2_protected_min_threads_per_active_user=1,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 4)

    started_workers: list[tuple[str, str, str]] = []

    class _FakeThread:
        def __init__(self, *, target, args, daemon, name):
            self.args = args

        def start(self) -> None:
            started_workers.append(self.args)

    monkeypatch.setattr("backend.app.services.mobile_playback_service.threading.Thread", _FakeThread)

    item_a = _make_local_item(settings, item_id=321, relative_name="route2/fair-a.mp4")
    item_b = _make_local_item(settings, item_id=322, relative_name="route2/fair-b.mp4")

    manager.create_session(item_a, user_id=1, auth_session_id=501, username="alice", engine_mode="route2", playback_mode="lite")
    manager.create_session(item_b, user_id=2, auth_session_id=601, username="bob", engine_mode="route2", playback_mode="lite")

    manager._dispatch_waiting_sessions()

    running_workers = [record for record in manager._route2_workers.values() if record.state == "running"]
    queued_workers = [record for record in manager._route2_workers.values() if record.state == "queued"]

    assert len(started_workers) == 2
    assert len(running_workers) == 2
    assert len(queued_workers) == 0
    assert sum(record.assigned_threads for record in running_workers) <= 2


def test_route2_duplicate_same_user_movie_mode_reuses_existing_preparation(initialized_settings) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    item = _make_local_item(settings, item_id=331, relative_name="route2/reuse.mp4")

    first = manager.create_session(
        item,
        user_id=1,
        auth_session_id=801,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
        start_position_seconds=0.0,
    )

    with manager._lock:
        session = manager._sessions[first["session_id"]]
        active_epoch = session.browser_playback.epochs[session.browser_playback.active_epoch_id]
        active_epoch.published_dir.mkdir(parents=True, exist_ok=True)
        active_epoch.published_init_path.write_bytes(b"init")
        for segment_index in range(25):
            (active_epoch.published_dir / f"segment_{segment_index:06d}.m4s").write_bytes(b"segment")
        manager._refresh_route2_session_authority_locked(session)

    second = manager.create_session(
        item,
        user_id=1,
        auth_session_id=801,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
        start_position_seconds=10.0,
    )

    assert second["session_id"] == first["session_id"]
    assert second["target_position_seconds"] == 10.0
    assert second["ready_end_seconds"] >= 40.0
    assert len(manager._route2_session_ids_by_user[1]) == 1
    assert len({record.worker_id for record in manager._route2_workers.values() if record.session_id == first["session_id"]}) == 1


def test_route2_logout_keep_preparing_reconnects_same_movie_without_duplicate_worker(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    item = _make_local_item(settings, item_id=335, relative_name="route2/reconnect-after-logout.mp4")
    started_workers: list[tuple[str, str, str]] = []
    auth_user, failure_reason = authenticate_user(
        initialized_settings,
        initialized_settings.admin_username,
        initialized_settings.admin_bootstrap_password or "",
    )
    assert failure_reason is None
    assert auth_user is not None
    first_token = create_auth_session(
        initialized_settings,
        auth_user,
        ip_address="127.0.0.1",
        user_agent="pytest-route2-keep-preparing",
    )
    first_session_user = get_user_by_session_token(initialized_settings, first_token)
    assert first_session_user is not None
    assert first_session_user.session_id is not None

    class _FakeThread:
        def __init__(self, *, target, args, daemon, name):
            self.args = args

        def start(self) -> None:
            started_workers.append(self.args)

    monkeypatch.setattr("backend.app.services.mobile_playback_service.threading.Thread", _FakeThread)

    first = manager.create_session(
        item,
        user_id=first_session_user.id,
        auth_session_id=first_session_user.session_id,
        username=first_session_user.username,
        engine_mode="route2",
        playback_mode="full",
        start_position_seconds=0.0,
    )
    manager._dispatch_waiting_sessions()

    with manager._lock:
        session = manager._sessions[first["session_id"]]
        session.last_client_seen_at = "2026-04-26T12:00:00+00:00"
        session.last_media_access_at = "2026-04-26T12:00:00+00:00"
        session.expires_at_ts = time.time() - 60
        active_epoch = session.browser_playback.epochs[session.browser_playback.active_epoch_id]
        active_epoch.published_dir.mkdir(parents=True, exist_ok=True)
        active_epoch.published_init_path.write_bytes(b"init")
        for segment_index in range(70):
            (active_epoch.published_dir / f"segment_{segment_index:06d}.m4s").write_bytes(b"segment")
        manager._refresh_route2_session_authority_locked(session)
        record = manager._route2_workers[active_epoch.active_worker_id]
        record.state = "running"
        prepared_ranges_before = [list(entry) for entry in record.prepared_ranges]

    destroy_session(initialized_settings, first_token)
    manager._reconcile_managed_session_auth_state()
    manager._cleanup_sessions_and_cache()

    with manager._lock:
        assert first["session_id"] in manager._sessions

    second_token = create_auth_session(
        initialized_settings,
        auth_user,
        ip_address="127.0.0.1",
        user_agent="pytest-route2-reconnect",
    )
    second_session_user = get_user_by_session_token(initialized_settings, second_token)
    assert second_session_user is not None
    assert second_session_user.session_id is not None
    restored = manager.get_active_session_for_item(
        int(item["id"]),
        user_id=second_session_user.id,
        auth_session_id=second_session_user.session_id,
        username=second_session_user.username,
    )
    second = manager.create_session(
        item,
        user_id=second_session_user.id,
        auth_session_id=second_session_user.session_id,
        username=second_session_user.username,
        engine_mode="route2",
        playback_mode="full",
        start_position_seconds=30.0,
    )

    assert restored is not None
    assert restored["session_id"] == first["session_id"]
    assert second["session_id"] == first["session_id"]
    assert second["ready_end_seconds"] >= 120.0
    started_route2_workers = [args for args in started_workers if len(args) == 3]
    assert len(started_route2_workers) == 1

    with manager._lock:
        session = manager._sessions[first["session_id"]]
        active_epoch = session.browser_playback.epochs[session.browser_playback.active_epoch_id]
        record = manager._route2_workers[active_epoch.active_worker_id]
        assert session.auth_session_id == second_session_user.session_id
        assert prepared_ranges_before == record.prepared_ranges
        assert len({worker.worker_id for worker in manager._route2_workers.values() if worker.session_id == first["session_id"]}) == 1


def test_route2_another_user_is_not_blocked_by_active_preparation(initialized_settings) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    item_a = _make_local_item(settings, item_id=336, relative_name="route2/user-a.mp4")
    item_b = _make_local_item(settings, item_id=337, relative_name="route2/user-b.mp4")

    first = manager.create_session(
        item_a,
        user_id=1,
        auth_session_id=930,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
    )
    second = manager.create_session(
        item_b,
        user_id=2,
        auth_session_id=940,
        username="bob",
        engine_mode="route2",
        playback_mode="lite",
    )

    assert first["session_id"] != second["session_id"]
    assert len(manager._route2_session_ids_by_user[1]) == 1
    assert len(manager._route2_session_ids_by_user[2]) == 1


def test_route2_admin_can_start_multiple_playbacks_when_capacity_exists(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=2,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 8)
    item_a = _make_local_item(settings, item_id=346, relative_name="route2/admin-a.mp4")
    item_b = _make_local_item(settings, item_id=347, relative_name="route2/admin-b.mp4")

    first = manager.create_session(
        item_a,
        user_id=1,
        auth_session_id=960,
        username="admin",
        engine_mode="route2",
        playback_mode="lite",
        user_role="admin",
    )
    second = manager.create_session(
        item_b,
        user_id=1,
        auth_session_id=960,
        username="admin",
        engine_mode="route2",
        playback_mode="lite",
        user_role="admin",
    )

    assert first["session_id"] != second["session_id"]
    assert len(manager._route2_session_ids_by_user[1]) == 2


def test_route2_new_user_is_admitted_when_protected_floor_capacity_exists(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=2,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 8)
    item_a = _make_local_item(settings, item_id=348, relative_name="route2/floor-user-a.mp4")
    item_b = _make_local_item(settings, item_id=349, relative_name="route2/floor-user-b.mp4")

    first = manager.create_session(item_a, user_id=1, auth_session_id=961, username="alice", engine_mode="route2", playback_mode="lite")
    second = manager.create_session(item_b, user_id=2, auth_session_id=962, username="bob", engine_mode="route2", playback_mode="lite")

    assert first["session_id"] != second["session_id"]
    assert len(manager._route2_session_ids_by_user[1]) == 1
    assert len(manager._route2_session_ids_by_user[2]) == 1


def test_route2_capacity_exactly_equal_to_existing_protected_floors_blocks_new_user(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=2,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 5)
    item_a = _make_local_item(settings, item_id=355, relative_name="route2/exact-floor-a.mp4")
    item_b = _make_local_item(settings, item_id=356, relative_name="route2/exact-floor-b.mp4")
    item_c = _make_local_item(settings, item_id=357, relative_name="route2/exact-floor-c.mp4")

    manager.create_session(item_a, user_id=1, auth_session_id=971, username="alice", engine_mode="route2", playback_mode="lite")
    manager.create_session(item_b, user_id=2, auth_session_id=972, username="bob", engine_mode="route2", playback_mode="lite")

    with pytest.raises(PlaybackAdmissionError) as exc:
        manager.create_session(item_c, user_id=3, auth_session_id=973, username="carol", engine_mode="route2", playback_mode="lite")

    assert exc.value.detail["code"] == "server_max_capacity"


def test_route2_capacity_just_enough_two_spare_threads_admits_new_user(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=2,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 5)
    item_a = _make_local_item(settings, item_id=358, relative_name="route2/just-enough-a.mp4")
    item_b = _make_local_item(settings, item_id=359, relative_name="route2/just-enough-b.mp4")

    first = manager.create_session(item_a, user_id=1, auth_session_id=974, username="alice", engine_mode="route2", playback_mode="lite")
    second = manager.create_session(item_b, user_id=2, auth_session_id=975, username="bob", engine_mode="route2", playback_mode="lite")

    assert first["session_id"] != second["session_id"]
    assert len(manager._route2_session_ids_by_user[1]) == 1
    assert len(manager._route2_session_ids_by_user[2]) == 1


def test_route2_capacity_one_thread_short_blocks_new_user(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=2,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 4)
    item_a = _make_local_item(settings, item_id=360, relative_name="route2/one-short-a.mp4")
    item_b = _make_local_item(settings, item_id=363, relative_name="route2/one-short-b.mp4")

    manager.create_session(item_a, user_id=1, auth_session_id=976, username="alice", engine_mode="route2", playback_mode="lite")

    with pytest.raises(PlaybackAdmissionError) as exc:
        manager.create_session(item_b, user_id=2, auth_session_id=977, username="bob", engine_mode="route2", playback_mode="lite")

    assert exc.value.detail["code"] == "server_max_capacity"


def test_route2_reclaimable_threads_are_not_counted_as_available_for_admission(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=4,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 6)
    _capture_route2_worker_threads(monkeypatch)
    item_a = _make_local_item(settings, item_id=364, relative_name="route2/reclaimable-a.mp4")
    item_b = _make_local_item(settings, item_id=365, relative_name="route2/reclaimable-b.mp4")

    manager.create_session(item_a, user_id=1, auth_session_id=978, username="alice", engine_mode="route2", playback_mode="lite")
    manager._dispatch_waiting_sessions()
    running_record = next(record for record in manager._route2_workers.values() if record.user_id == 1)
    assert running_record.state == "running"
    assert running_record.assigned_threads == 4

    with pytest.raises(PlaybackAdmissionError) as exc:
        manager.create_session(item_b, user_id=2, auth_session_id=979, username="bob", engine_mode="route2", playback_mode="lite")

    detail = exc.value.detail
    assert detail["code"] == "server_max_capacity"
    assert detail["reason_code"] == "no_spare_protected_worker_capacity"
    assert detail["available_reserved_threads"] == 1


def test_route2_server_max_capacity_when_protected_floor_cannot_be_preserved(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=2,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 4)
    item_a = _make_local_item(settings, item_id=350, relative_name="route2/capacity-a.mp4")
    item_b = _make_local_item(settings, item_id=351, relative_name="route2/capacity-b.mp4")

    manager.create_session(item_a, user_id=1, auth_session_id=963, username="alice", engine_mode="route2", playback_mode="lite")

    with pytest.raises(PlaybackAdmissionError) as exc:
        manager.create_session(item_b, user_id=2, auth_session_id=964, username="bob", engine_mode="route2", playback_mode="lite")

    detail = exc.value.detail
    assert detail["code"] == "server_max_capacity"
    assert detail["reason_code"] == "per_user_budget_below_protected_floor"
    assert detail["message"] == "Server is busy. Please try again later."


def test_route2_external_cpu_pressure_blocks_new_admission(initialized_settings, monkeypatch) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=2,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 8)
    _set_route2_resource_snapshot(
        manager,
        external_cpu_cores_used_estimate=5.0,
        external_cpu_percent_estimate=0.625,
        external_pressure_level="high",
    )
    item = _make_local_item(settings, item_id=352, relative_name="route2/external-pressure.mp4")

    with pytest.raises(PlaybackAdmissionError) as exc:
        manager.create_session(item, user_id=1, auth_session_id=965, username="alice", engine_mode="route2", playback_mode="lite")

    assert exc.value.detail["code"] == "server_max_capacity"
    assert exc.value.detail["reason_code"] == "external_host_cpu_pressure_high"
    assert exc.value.detail["message"] == "Server is busy with another task. Please try again later."


def test_route2_external_ffmpeg_pressure_blocks_new_admission(initialized_settings, monkeypatch) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=2,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 8)
    _set_route2_resource_snapshot(
        manager,
        external_ffmpeg_process_count=1,
        external_ffmpeg_cpu_cores_estimate=1.25,
        external_pressure_level="moderate",
    )
    item = _make_local_item(settings, item_id=353, relative_name="route2/external-ffmpeg.mp4")

    with pytest.raises(PlaybackAdmissionError) as exc:
        manager.create_session(item, user_id=1, auth_session_id=966, username="alice", engine_mode="route2", playback_mode="lite")

    assert exc.value.detail["code"] == "server_max_capacity"
    assert exc.value.detail["reason_code"] == "external_ffmpeg_pressure"


def test_route2_hard_ram_pressure_blocks_new_admission(initialized_settings, monkeypatch) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=2,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 8)
    _set_route2_resource_snapshot(
        manager,
        total_memory_bytes=100,
        route2_memory_bytes_total=91,
    )
    item = _make_local_item(settings, item_id=354, relative_name="route2/ram-pressure.mp4")

    with pytest.raises(PlaybackAdmissionError) as exc:
        manager.create_session(item, user_id=1, auth_session_id=967, username="alice", engine_mode="route2", playback_mode="lite")

    assert exc.value.detail["code"] == "server_max_capacity"
    assert exc.value.detail["reason_code"] == "route2_memory_hard_pressure"


def test_route2_active_playback_healthy_with_spare_capacity_admits_new_user(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=2,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 5)
    _capture_route2_worker_threads(monkeypatch)
    item_a = _make_local_item(settings, item_id=366, relative_name="route2/healthy-runtime-a.mp4")
    item_b = _make_local_item(settings, item_id=367, relative_name="route2/healthy-runtime-b.mp4")

    first = manager.create_session(item_a, user_id=1, auth_session_id=980, username="alice", engine_mode="route2", playback_mode="lite")
    manager._dispatch_waiting_sessions()
    session, epoch, record = _active_route2_record_for_session(manager, first)
    assert record.assigned_threads == 2
    _mark_route2_runtime_supply(
        session,
        epoch,
        record,
        supply_rate_x=1.2,
        runway_seconds=70.0,
        cpu_cores_used=1.0,
    )

    second = manager.create_session(item_b, user_id=2, auth_session_id=981, username="bob", engine_mode="route2", playback_mode="lite")

    assert second["session_id"] != first["session_id"]
    assert len(manager._route2_session_ids_by_user[2]) == 1


def test_route2_active_playback_at_floor_low_cpu_supply_blocks_new_admission(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=2,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 5)
    _capture_route2_worker_threads(monkeypatch)
    item_a = _make_local_item(settings, item_id=368, relative_name="route2/starved-runtime-a.mp4")
    item_b = _make_local_item(settings, item_id=369, relative_name="route2/starved-runtime-b.mp4")

    first = manager.create_session(item_a, user_id=1, auth_session_id=982, username="alice", engine_mode="route2", playback_mode="lite")
    manager._dispatch_waiting_sessions()
    session, epoch, record = _active_route2_record_for_session(manager, first)
    assert record.assigned_threads == 2
    _mark_route2_runtime_supply(
        session,
        epoch,
        record,
        supply_rate_x=1.0,
        runway_seconds=10.0,
        cpu_cores_used=2.0,
    )

    with pytest.raises(PlaybackAdmissionError) as exc:
        manager.create_session(item_b, user_id=2, auth_session_id=983, username="bob", engine_mode="route2", playback_mode="lite")

    detail = exc.value.detail
    assert detail["code"] == "server_max_capacity"
    assert detail["reason_code"] == "active_stream_protection"


def test_route2_active_playback_manifest_complete_does_not_block_on_zero_supply(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=2,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 5)
    _capture_route2_worker_threads(monkeypatch)
    item_a = _make_local_item(settings, item_id=370, relative_name="route2/complete-runtime-a.mp4")
    item_b = _make_local_item(settings, item_id=371, relative_name="route2/complete-runtime-b.mp4")

    first = manager.create_session(item_a, user_id=1, auth_session_id=984, username="alice", engine_mode="route2", playback_mode="lite")
    manager._dispatch_waiting_sessions()
    session, epoch, record = _active_route2_record_for_session(manager, first)
    _mark_route2_runtime_supply(
        session,
        epoch,
        record,
        supply_rate_x=0.0,
        runway_seconds=0.0,
        cpu_cores_used=0.0,
        manifest_complete=True,
        refill_in_progress=False,
    )

    second = manager.create_session(item_b, user_id=2, auth_session_id=985, username="bob", engine_mode="route2", playback_mode="lite")

    assert second["session_id"] != first["session_id"]


def test_route2_active_playback_immature_metrics_block_when_capacity_is_tight(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=2,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 5)
    _capture_route2_worker_threads(monkeypatch)
    item_a = _make_local_item(settings, item_id=372, relative_name="route2/immature-runtime-a.mp4")
    item_b = _make_local_item(settings, item_id=373, relative_name="route2/immature-runtime-b.mp4")

    first = manager.create_session(item_a, user_id=1, auth_session_id=986, username="alice", engine_mode="route2", playback_mode="lite")
    manager._dispatch_waiting_sessions()
    session, epoch, record = _active_route2_record_for_session(manager, first)
    _mark_route2_runtime_supply(
        session,
        epoch,
        record,
        supply_rate_x=1.4,
        observation_seconds=2.0,
        runway_seconds=60.0,
        cpu_cores_used=1.0,
    )

    with pytest.raises(PlaybackAdmissionError) as exc:
        manager.create_session(item_b, user_id=2, auth_session_id=987, username="bob", engine_mode="route2", playback_mode="lite")

    detail = exc.value.detail
    assert detail["code"] == "server_max_capacity"
    assert detail["reason_code"] == "active_stream_metrics_immature"


def test_route2_source_bound_low_supply_is_not_classified_as_cpu_thread_starved(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=2,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 5)
    _capture_route2_worker_threads(monkeypatch)
    item = _make_local_item(settings, item_id=374, relative_name="route2/source-bound-runtime.mp4")

    first = manager.create_session(item, user_id=1, auth_session_id=988, username="alice", engine_mode="route2", playback_mode="lite")
    manager._dispatch_waiting_sessions()
    session, epoch, record = _active_route2_record_for_session(manager, first)
    session.source_kind = "cloud"
    record.source_kind = "cloud"
    _mark_route2_runtime_supply(
        session,
        epoch,
        record,
        supply_rate_x=0.8,
        runway_seconds=12.0,
        cpu_cores_used=0.4,
    )

    health = manager._evaluate_route2_active_playback_health_locked(session, epoch, record)

    assert health.status == "source_bound"
    assert health.cpu_thread_limited is False
    assert health.admission_blocking is False


def test_route2_client_bound_low_supply_is_not_treated_as_thread_donor_problem(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=2,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 5)
    _capture_route2_worker_threads(monkeypatch)
    item = _make_local_item(settings, item_id=377, relative_name="route2/client-bound-runtime.mp4")

    first = manager.create_session(item, user_id=1, auth_session_id=991, username="alice", engine_mode="route2", playback_mode="lite")
    manager._dispatch_waiting_sessions()
    session, epoch, record = _active_route2_record_for_session(manager, first)
    _mark_route2_runtime_supply(
        session,
        epoch,
        record,
        supply_rate_x=0.8,
        runway_seconds=12.0,
        cpu_cores_used=2.0,
    )
    epoch.byte_samples = [
        (0.0, 0),
        (4.0, 16_000_000),
        (8.0, 32_000_000),
        (12.0, 48_000_000),
    ]
    session.browser_playback.client_probe_samples = [
        (0.0, 1_000_000, 0.5),
        (4.0, 1_000_000, 0.5),
        (8.0, 1_000_000, 0.5),
        (12.0, 1_000_000, 0.5),
    ]

    health = manager._evaluate_route2_active_playback_health_locked(session, epoch, record)

    assert health.status == "client_bound"
    assert health.admission_blocking is False
    assert health.runtime_rebalance_role == "neutral"


def test_route2_provider_error_health_is_not_mapped_to_active_stream_cpu_busy(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=2,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 5)
    _capture_route2_worker_threads(monkeypatch)
    item = _make_local_item(settings, item_id=378, relative_name="route2/provider-error-runtime.mp4")

    first = manager.create_session(item, user_id=1, auth_session_id=992, username="alice", engine_mode="route2", playback_mode="lite")
    manager._dispatch_waiting_sessions()
    session, epoch, record = _active_route2_record_for_session(manager, first)
    record.non_retryable_error = "Google Drive provider quota exceeded"
    _mark_route2_runtime_supply(
        session,
        epoch,
        record,
        supply_rate_x=0.4,
        runway_seconds=4.0,
        cpu_cores_used=2.0,
    )

    health = manager._evaluate_route2_active_playback_health_locked(session, epoch, record)

    assert health.status == "provider_error"
    assert health.cpu_thread_limited is True
    assert health.admission_blocking is False


def test_route2_runtime_rebalance_dry_run_marks_donor_and_recipient_without_changing_threads(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, settings = _make_route2_manager(
        initialized_settings,
        route2_cpu_budget_percent=90,
        route2_max_worker_threads=4,
    )
    monkeypatch.setattr("backend.app.services.mobile_playback_service.os.cpu_count", lambda: 10)
    _capture_route2_worker_threads(monkeypatch)
    item_a = _make_local_item(settings, item_id=375, relative_name="route2/donor-runtime.mp4")
    item_b = _make_local_item(settings, item_id=376, relative_name="route2/recipient-runtime.mp4")

    first = manager.create_session(item_a, user_id=1, auth_session_id=989, username="alice", engine_mode="route2", playback_mode="lite")
    second = manager.create_session(item_b, user_id=2, auth_session_id=990, username="bob", engine_mode="route2", playback_mode="lite")
    manager._dispatch_waiting_sessions()
    donor_session, donor_epoch, donor_record = _active_route2_record_for_session(manager, first)
    recipient_session, recipient_epoch, recipient_record = _active_route2_record_for_session(manager, second)
    assert donor_record.assigned_threads == 4
    assert recipient_record.assigned_threads == 4
    _mark_route2_runtime_supply(
        donor_session,
        donor_epoch,
        donor_record,
        supply_rate_x=1.5,
        runway_seconds=95.0,
        effective_playhead_seconds=10.0,
        cpu_cores_used=1.0,
    )
    _mark_route2_runtime_supply(
        recipient_session,
        recipient_epoch,
        recipient_record,
        supply_rate_x=0.9,
        runway_seconds=8.0,
        cpu_cores_used=4.0,
    )

    donor_health = manager._evaluate_route2_active_playback_health_locked(donor_session, donor_epoch, donor_record)
    recipient_health = manager._evaluate_route2_active_playback_health_locked(
        recipient_session,
        recipient_epoch,
        recipient_record,
    )

    assert donor_health.runtime_rebalance_role == "donor_candidate"
    assert donor_health.runtime_rebalance_can_donate_threads == 2
    assert donor_health.runtime_rebalance_target_threads == 2
    assert recipient_health.runtime_rebalance_role == "needs_resource"
    assert recipient_health.runtime_rebalance_target_threads == 6
    assert donor_record.assigned_threads == 4
    assert recipient_record.assigned_threads == 4


def test_route2_stop_then_start_new_movie_allows_replacement_movie(initialized_settings) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    item_a = _make_local_item(settings, item_id=338, relative_name="route2/stop-then-start-a.mp4")
    item_b = _make_local_item(settings, item_id=339, relative_name="route2/stop-then-start-b.mp4")

    first = manager.create_session(
        item_a,
        user_id=1,
        auth_session_id=950,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
    )

    assert manager.stop_session(first["session_id"], user_id=1) is True

    second = manager.create_session(
        item_b,
        user_id=1,
        auth_session_id=950,
        username="alice",
        engine_mode="route2",
        playback_mode="full",
    )

    assert second["session_id"] != first["session_id"]
    assert len(manager._route2_session_ids_by_user[1]) == 1
    assert second["media_item_id"] == int(item_b["id"])


def test_route2_replacement_epoch_cap_fails_session_clearly(initialized_settings) -> None:
    manager, _settings = _make_route2_manager(
        initialized_settings,
        route2_max_replacement_epochs_per_session=2,
    )
    session = _make_route2_session(playback_mode="lite", client_attach_revision=0)

    with manager._lock:
        manager._initialize_route2_session_locked(session)
        manager._sessions[session.session_id] = session
        manager._register_route2_session_locked(session)
        first = manager._create_route2_replacement_epoch_locked(session, target_position_seconds=30.0, reason="seek-1")
        second = manager._create_route2_replacement_epoch_locked(session, target_position_seconds=60.0, reason="seek-2")
        third = manager._create_route2_replacement_epoch_locked(session, target_position_seconds=90.0, reason="seek-3")

    assert first is not None
    assert second is not None
    assert third is None
    assert session.state == "failed"
    assert "maximum number of replacement epochs" in (session.last_error or "").lower()


def test_route2_stop_session_terminates_owned_worker(initialized_settings) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    item = _make_local_item(settings, item_id=341, relative_name="route2/stop.mp4")
    payload = manager.create_session(
        item,
        user_id=1,
        auth_session_id=901,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
    )

    class _FakeProcess:
        def __init__(self) -> None:
            self.pid = 4321
            self.terminated = False
            self.killed = False
            self._return_code = None

        def poll(self):
            return self._return_code

        def terminate(self) -> None:
            self.terminated = True
            self._return_code = 0

        def wait(self, timeout=None):
            self._return_code = 0 if self._return_code is None else self._return_code
            return self._return_code

        def kill(self) -> None:
            self.killed = True
            self._return_code = -9

    fake_process = _FakeProcess()
    with manager._lock:
        session = manager._sessions[payload["session_id"]]
        active_epoch = session.browser_playback.epochs[session.browser_playback.active_epoch_id]
        active_epoch.process = fake_process
        record = manager._ensure_route2_worker_record_locked(session, active_epoch)
        record.state = "running"
        record.assigned_threads = 2

    assert manager.stop_session(payload["session_id"], user_id=1) is True
    assert fake_process.terminated is True
    assert payload["session_id"] not in manager._sessions
    assert 1 not in manager._route2_session_ids_by_user
    with manager._lock:
        assert manager._browser_playback_cooldowns == {}


def test_route2_user_stop_does_not_create_browser_cooldown_and_allows_immediate_restart(
    initialized_settings,
) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    item = _make_local_item(settings, item_id=342, relative_name="route2/stop-restart.mp4")
    first = manager.create_session(
        item,
        user_id=1,
        auth_session_id=902,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
    )

    assert manager.stop_session(first["session_id"], user_id=1) is True

    with manager._lock:
        assert manager._browser_playback_cooldowns == {}

    second = manager.create_session(
        item,
        user_id=1,
        auth_session_id=903,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
    )

    assert second["media_item_id"] == int(item["id"])
    assert second["session_id"] != first["session_id"]


def test_route2_admin_terminate_worker_creates_browser_cooldown_for_same_user_movie(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    item = _make_local_item(settings, item_id=344, relative_name="route2/admin-cooldown.mp4")
    monkeypatch.setattr("backend.app.services.mobile_playback_service.time.time", lambda: 100.0)
    payload = manager.create_session(
        item,
        user_id=1,
        auth_session_id=910,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
    )

    class _FakeProcess:
        def __init__(self) -> None:
            self.pid = 6542
            self.terminated = False
            self.killed = False
            self._return_code = None

        def poll(self):
            return self._return_code

        def terminate(self) -> None:
            self.terminated = True
            self._return_code = 0

        def wait(self, timeout=None):
            self._return_code = 0 if self._return_code is None else self._return_code
            return self._return_code

        def kill(self) -> None:
            self.killed = True
            self._return_code = -9

    fake_process = _FakeProcess()
    with manager._lock:
        session = manager._sessions[payload["session_id"]]
        active_epoch = session.browser_playback.epochs[session.browser_playback.active_epoch_id]
        active_epoch.process = fake_process
        record = manager._ensure_route2_worker_record_locked(session, active_epoch)
        record.state = "running"
        worker_id = record.worker_id

    assert manager.terminate_route2_worker(worker_id, apply_admin_cooldown=True) is True
    assert fake_process.terminated is True

    with manager._lock:
        entry = manager._browser_playback_cooldowns[(1, int(item["id"]))]

    assert entry["reason"] == "admin_terminated_worker"
    assert entry["expires_at_ts"] == 130.0

    with pytest.raises(PlaybackWorkerCooldownError) as cooldown:
        manager.raise_if_browser_playback_cooldown_active(
            user_id=1,
            media_item_id=int(item["id"]),
            playback_mode="lite",
        )

    assert cooldown.value.detail == {
        "code": "playback_worker_cooldown",
        "media_item_id": int(item["id"]),
        "remaining_seconds": 30,
        "message": "Your current quota for this movie has been reached. Please try again in 30 seconds.",
    }


@pytest.mark.parametrize("playback_mode", ["lite", "full"])
def test_route2_browser_cooldown_blocks_same_user_same_movie_for_lite_and_full(
    initialized_settings,
    monkeypatch,
    playback_mode: str,
) -> None:
    manager, _settings = _make_route2_manager(initialized_settings)
    monkeypatch.setattr("backend.app.services.mobile_playback_service.time.time", lambda: 200.0)

    with manager._lock:
        manager._record_admin_terminated_browser_playback_cooldown_locked(
            user_id=1,
            media_item_id=345,
        )

    with pytest.raises(PlaybackWorkerCooldownError) as cooldown:
        manager.raise_if_browser_playback_cooldown_active(
            user_id=1,
            media_item_id=345,
            playback_mode=playback_mode,
        )

    assert cooldown.value.detail == {
        "code": "playback_worker_cooldown",
        "media_item_id": 345,
        "remaining_seconds": 30,
        "message": "Your current quota for this movie has been reached. Please try again in 30 seconds.",
    }


def test_route2_browser_cooldown_does_not_block_other_user_or_other_movie_and_expires(
    initialized_settings,
    monkeypatch,
) -> None:
    manager, _settings = _make_route2_manager(initialized_settings)
    now_holder = {"value": 300.0}
    monkeypatch.setattr(
        "backend.app.services.mobile_playback_service.time.time",
        lambda: now_holder["value"],
    )

    with manager._lock:
        manager._record_admin_terminated_browser_playback_cooldown_locked(
            user_id=1,
            media_item_id=346,
        )

    manager.raise_if_browser_playback_cooldown_active(user_id=2, media_item_id=346, playback_mode="lite")
    manager.raise_if_browser_playback_cooldown_active(user_id=1, media_item_id=347, playback_mode="full")

    now_holder["value"] = 331.0
    manager.raise_if_browser_playback_cooldown_active(user_id=1, media_item_id=346, playback_mode="lite")

    with manager._lock:
        assert (1, 346) not in manager._browser_playback_cooldowns


def test_route2_browser_cooldown_is_not_enforced_by_generic_session_create(initialized_settings, monkeypatch) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    item = _make_local_item(settings, item_id=347, relative_name="route2/browser-only-cooldown.mp4")
    monkeypatch.setattr("backend.app.services.mobile_playback_service.time.time", lambda: 400.0)

    with manager._lock:
        manager._record_admin_terminated_browser_playback_cooldown_locked(
            user_id=1,
            media_item_id=int(item["id"]),
        )

    payload = manager.create_session(
        item,
        user_id=1,
        auth_session_id=920,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
    )

    assert payload["media_item_id"] == int(item["id"])


def test_route2_admin_terminate_worker_stops_matching_owned_session(initialized_settings) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    item = _make_local_item(settings, item_id=345, relative_name="route2/admin-terminate.mp4")
    payload = manager.create_session(
        item,
        user_id=1,
        auth_session_id=911,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
    )

    class _FakeProcess:
        def __init__(self) -> None:
            self.pid = 6543
            self.terminated = False
            self.killed = False
            self._return_code = None

        def poll(self):
            return self._return_code

        def terminate(self) -> None:
            self.terminated = True
            self._return_code = 0

        def wait(self, timeout=None):
            self._return_code = 0 if self._return_code is None else self._return_code
            return self._return_code

        def kill(self) -> None:
            self.killed = True
            self._return_code = -9

    fake_process = _FakeProcess()
    with manager._lock:
        session = manager._sessions[payload["session_id"]]
        active_epoch = session.browser_playback.epochs[session.browser_playback.active_epoch_id]
        active_epoch.process = fake_process
        record = manager._ensure_route2_worker_record_locked(session, active_epoch)
        record.state = "running"
        record.assigned_threads = 2
        worker_id = record.worker_id

    assert manager.terminate_route2_worker(worker_id) is True
    assert fake_process.terminated is True
    assert payload["session_id"] not in manager._sessions
    with manager._lock:
        assert manager._browser_playback_cooldowns == {}
    summary = manager.get_route2_worker_status()
    assert summary["active_worker_count"] == 0


def test_route2_admin_terminate_worker_does_not_kill_unrelated_pid(initialized_settings) -> None:
    manager, _settings = _make_route2_manager(initialized_settings)

    class _FakeProcess:
        def __init__(self) -> None:
            self.pid = 7777
            self.terminated = False

        def poll(self):
            return None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self) -> None:
            self.terminated = True

    fake_process = _FakeProcess()
    with manager._lock:
        manager._route2_workers["orphan-worker"] = Route2WorkerRecord(
            worker_id="orphan-worker",
            session_id="missing-session",
            epoch_id="missing-epoch",
            user_id=999,
            username="ghost",
            auth_session_id=None,
            media_item_id=999,
            title="Orphan",
            playback_mode="lite",
            profile="mobile_1080p",
            source_kind="local",
            target_position_seconds=0.0,
            state="running",
            pid=fake_process.pid,
            process=fake_process,
        )

    assert manager.terminate_route2_worker("orphan-worker") is False
    assert fake_process.terminated is False


def test_route2_disable_user_invalidates_owned_workers(initialized_settings) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    item = _make_local_item(settings, item_id=351, relative_name="route2/disable.mp4")
    payload = manager.create_session(
        item,
        user_id=1,
        auth_session_id=1001,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
    )

    class _FakeProcess:
        def __init__(self) -> None:
            self.pid = 9876
            self.terminated = False
            self._return_code = None

        def poll(self):
            return self._return_code

        def terminate(self) -> None:
            self.terminated = True
            self._return_code = 0

        def wait(self, timeout=None):
            self._return_code = 0 if self._return_code is None else self._return_code
            return self._return_code

        def kill(self) -> None:
            self._return_code = -9

    fake_process = _FakeProcess()
    with manager._lock:
        session = manager._sessions[payload["session_id"]]
        active_epoch = session.browser_playback.epochs[session.browser_playback.active_epoch_id]
        active_epoch.process = fake_process
        record = manager._ensure_route2_worker_record_locked(session, active_epoch)
        record.state = "running"

    assert manager.invalidate_user_sessions(1, reason="user_disabled") == 1
    assert fake_process.terminated is True
    assert payload["session_id"] not in manager._sessions


def test_route2_revoke_auth_session_invalidates_matching_workers(initialized_settings) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    item_a = _make_local_item(settings, item_id=361, relative_name="route2/revoke-a.mp4")
    item_b = _make_local_item(settings, item_id=362, relative_name="route2/revoke-b.mp4")

    payload_a = manager.create_session(
        item_a,
        user_id=1,
        auth_session_id=1101,
        username="alice",
        engine_mode="route2",
        playback_mode="lite",
    )
    payload_b = manager.create_session(
        item_b,
        user_id=2,
        auth_session_id=2202,
        username="bob",
        engine_mode="route2",
        playback_mode="lite",
    )

    assert manager.invalidate_auth_session(1101, reason="admin_revoked") == 1
    assert payload_a["session_id"] not in manager._sessions
    assert payload_b["session_id"] in manager._sessions


def test_route2_startup_cleanup_marks_stale_running_metadata_as_interrupted(initialized_settings) -> None:
    manager, settings = _make_route2_manager(initialized_settings)
    metadata_path = (
        settings.transcode_dir
        / "browser_playback_route2"
        / "sessions"
        / "stale-session"
        / "epochs"
        / "stale-epoch"
        / "epoch.json"
    )
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        """
{
  "epoch_id": "stale-epoch",
  "session_id": "stale-session",
  "state": "warming",
  "active_worker_id": "dead-worker",
  "transcoder_completed": false,
  "last_error": null
}
""".strip(),
        encoding="utf-8",
    )

    manager.start()
    manager.shutdown()

    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert payload["active_worker_id"] is None
    assert payload["state"] == "failed"
    assert "backend restart" in str(payload["last_error"]).lower()


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
