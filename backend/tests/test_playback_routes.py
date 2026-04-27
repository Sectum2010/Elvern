from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PureWindowsPath
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.app.auth import authenticate_user, destroy_session
from backend.app.db import get_connection, utcnow_iso
from backend.app.services.admin_service import create_user, update_user
from backend.app.services.mobile_playback_service import (
    ActivePlaybackWorkerConflictError,
    PlaybackWorkerCooldownError,
)


IOS_SAFARI_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)
DESKTOP_CHROME_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)


class BrowserPlaybackRouteManagerStub:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = dict(payload)
        self.create_exception: Exception | None = None
        self.browser_cooldown_exception: Exception | None = None
        self.create_calls = 0

    def start(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def raise_if_browser_playback_cooldown_active(self, **kwargs) -> None:
        del kwargs
        if self.browser_cooldown_exception is not None:
            raise self.browser_cooldown_exception

    def create_session(self, *args, **kwargs) -> dict[str, object]:
        self.create_calls += 1
        if self.create_exception is not None:
            raise self.create_exception
        return dict(self.payload)

    def get_session(self, session_id: str, *, user_id: int, **kwargs) -> dict[str, object]:
        payload = dict(self.payload)
        payload["session_id"] = session_id
        return payload

    def get_active_session(self, *, user_id: int, **kwargs) -> dict[str, object] | None:
        return dict(self.payload)

    def get_active_session_for_item(self, item_id: int, *, user_id: int, **kwargs) -> dict[str, object] | None:
        payload = dict(self.payload)
        payload["media_item_id"] = item_id
        return payload

    def update_runtime(self, session_id: str, *, user_id: int, **kwargs) -> dict[str, object]:
        payload = dict(self.payload)
        payload["session_id"] = session_id
        return payload


class AdminPlaybackWorkerManagerStub:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = dict(payload)
        self.invalidated_user_calls: list[tuple[int, str]] = []
        self.invalidated_auth_session_calls: list[tuple[int, str]] = []
        self.terminated_worker_ids: list[str] = []
        self.terminated_worker_cooldown_flags: list[bool] = []
        self.terminate_result = True

    def start(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def get_route2_worker_status(self) -> dict[str, object]:
        return dict(self.payload)

    def invalidate_user_sessions(self, user_id: int, *, reason: str) -> int:
        self.invalidated_user_calls.append((user_id, reason))
        return 1

    def invalidate_auth_session(self, auth_session_id: int, *, reason: str) -> int:
        self.invalidated_auth_session_calls.append((auth_session_id, reason))
        return 1

    def terminate_route2_worker(self, worker_id: str, *, apply_admin_cooldown: bool = False) -> bool:
        self.terminated_worker_ids.append(worker_id)
        self.terminated_worker_cooldown_flags.append(bool(apply_admin_cooldown))
        return self.terminate_result


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


def _set_shared_progress(
    settings,
    *,
    user_id: int,
    item_id: int,
    position_seconds: float,
    duration_seconds: float = 120.0,
    completed: bool = False,
) -> None:
    with get_connection(settings) as connection:
        connection.execute(
            """
            INSERT INTO playback_progress (
                user_id,
                media_item_id,
                position_seconds,
                duration_seconds,
                watch_seconds_total,
                completed,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, media_item_id) DO UPDATE SET
                position_seconds = excluded.position_seconds,
                duration_seconds = excluded.duration_seconds,
                watch_seconds_total = excluded.watch_seconds_total,
                completed = excluded.completed,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                item_id,
                position_seconds,
                duration_seconds,
                round(position_seconds, 2),
                int(completed),
                utcnow_iso(),
            ),
        )
        connection.commit()


def _make_browser_playback_route2_payload(
    *,
    item_id: int,
    session_id: str = "route2-session",
    mode_estimate_source: str = "fast_start_supply_surplus",
    gate_reason: str = "full_fast_start_supply_surplus",
) -> dict[str, object]:
    return {
        "session_id": session_id,
        "media_item_id": item_id,
        "epoch": 1,
        "manifest_revision": "route2:1:epoch-1",
        "state": "ready",
        "profile": "mobile_2160p",
        "duration_seconds": 14125.17,
        "target_position_seconds": 837.01,
        "ready_start_seconds": 817.01,
        "ready_end_seconds": 957.01,
        "can_play_from_target": True,
        "manifest_url": f"/api/mobile-playback/sessions/{session_id}/index.m3u8",
        "status_url": f"/api/mobile-playback/sessions/{session_id}",
        "seek_url": f"/api/mobile-playback/sessions/{session_id}/seek",
        "heartbeat_url": f"/api/mobile-playback/sessions/{session_id}/heartbeat",
        "stop_url": f"/api/mobile-playback/sessions/{session_id}/stop",
        "worker_state": "running",
        "engine_mode": "route2",
        "playback_mode": "full",
        "mode_state": "ready",
        "mode_ready": True,
        "mode_estimate_seconds": 0.0,
        "mode_estimate_source": mode_estimate_source,
        "active_epoch_id": "epoch-1",
        "active_manifest_url": f"/api/mobile-playback/epochs/epoch-1/index.m3u8",
        "attach_position_seconds": 837.01,
        "attach_ready": True,
        "browser_session_state": "active",
        "session_state": "active",
        "active_epoch_state": "attach_ready",
        "gate_reason": gate_reason,
        "required_startup_runway_seconds": 120.0,
        "actual_startup_runway_seconds": 140.0,
        "effective_goodput_ratio": 2.847,
        "supply_rate_x": 2.847,
        "supply_observation_seconds": 16.92,
    }


def _make_admin_playback_workers_payload() -> dict[str, object]:
    return {
        "cpu_upbound_percent": 90,
        "cpu_budget_percent": 90,
        "total_cpu_cores": 20,
        "route2_cpu_upbound_cores": 18,
        "total_route2_budget_cores": 18,
        "route2_cpu_cores_used": 7.2,
        "route2_cpu_percent_of_total": 36.0,
        "route2_cpu_percent_of_upbound": 40.0,
        "total_memory_bytes": 32 * 1024 * 1024 * 1024,
        "route2_memory_bytes": 2 * 1024 * 1024 * 1024,
        "route2_memory_percent_of_total": 6.25,
        "active_worker_count": 1,
        "queued_worker_count": 2,
        "active_decoding_user_count": 2,
        "per_user_budget_cores": 9,
        "workers_by_user": [
            {
                "user_id": 1,
                "username": "alice",
                "allocated_cpu_cores": 9,
                "allocated_budget_cores": 9,
                "cpu_cores_used": 7.2,
                "cpu_percent_of_user_limit": 80.0,
                "memory_bytes": int(1.2 * 1024 * 1024 * 1024),
                "memory_percent_of_total": 3.75,
                "running_workers": 1,
                "queued_workers": 1,
                "total_workers": 2,
                "items": [
                    {
                        "worker_id": "worker-1",
                        "session_id": "session-1",
                        "epoch_id": "epoch-1",
                        "media_item_id": 501,
                        "title": "Two Towers",
                        "playback_mode": "full",
                        "profile": "mobile_2160p",
                        "source_kind": "local",
                        "state": "running",
                        "runtime_seconds": 12.5,
                        "pid": 4321,
                        "target_position_seconds": 837.01,
                        "prepared_ranges": [[817.01, 957.01]],
                        "stop_requested": False,
                        "non_retryable_error": None,
                        "failure_count": 0,
                        "replacement_count": 1,
                        "assigned_threads": 6,
                        "process_exists": True,
                        "cpu_cores_used": 7.2,
                        "cpu_percent_of_total": 36.0,
                        "cpu_percent": 36.0,
                        "memory_bytes": int(1.2 * 1024 * 1024 * 1024),
                        "memory_percent_of_total": 3.75,
                        "telemetry_sampled": True,
                        "last_sampled_at": "2026-04-26T12:00:06+00:00",
                        "failure_reason": None,
                        "started_at": "2026-04-26T12:00:00+00:00",
                        "last_seen_at": "2026-04-26T12:00:05+00:00",
                    },
                    {
                        "worker_id": "worker-2",
                        "session_id": "session-2",
                        "epoch_id": "epoch-2",
                        "media_item_id": 502,
                        "title": "Queued Prep",
                        "playback_mode": "lite",
                        "profile": "mobile_1080p",
                        "source_kind": "cloud",
                        "state": "queued",
                        "runtime_seconds": None,
                        "pid": None,
                        "target_position_seconds": 120.0,
                        "prepared_ranges": [],
                        "stop_requested": False,
                        "non_retryable_error": None,
                        "failure_count": 0,
                        "replacement_count": 0,
                        "assigned_threads": 0,
                        "process_exists": False,
                        "cpu_cores_used": None,
                        "cpu_percent_of_total": None,
                        "cpu_percent": None,
                        "memory_bytes": None,
                        "memory_percent_of_total": None,
                        "telemetry_sampled": False,
                        "last_sampled_at": None,
                        "failure_reason": None,
                        "started_at": None,
                        "last_seen_at": "2026-04-26T12:00:05+00:00",
                    },
                ],
            },
            {
                "user_id": 2,
                "username": "bob",
                "allocated_cpu_cores": 9,
                "allocated_budget_cores": 9,
                "cpu_cores_used": None,
                "cpu_percent_of_user_limit": None,
                "memory_bytes": None,
                "memory_percent_of_total": None,
                "running_workers": 0,
                "queued_workers": 1,
                "total_workers": 1,
                "items": [
                    {
                        "worker_id": "worker-3",
                        "session_id": "session-3",
                        "epoch_id": "epoch-3",
                        "media_item_id": 503,
                        "title": "Cloud Wait",
                        "playback_mode": "full",
                        "profile": "mobile_2160p",
                        "source_kind": "cloud",
                        "state": "queued",
                        "runtime_seconds": None,
                        "pid": None,
                        "target_position_seconds": 42.0,
                        "prepared_ranges": [],
                        "stop_requested": False,
                        "non_retryable_error": "The download quota for this file has been exceeded.",
                        "failure_count": 1,
                        "replacement_count": 0,
                        "assigned_threads": 0,
                        "process_exists": False,
                        "cpu_cores_used": None,
                        "cpu_percent_of_total": None,
                        "cpu_percent": None,
                        "memory_bytes": None,
                        "memory_percent_of_total": None,
                        "telemetry_sampled": False,
                        "last_sampled_at": None,
                        "failure_reason": "The download quota for this file has been exceeded.",
                        "started_at": None,
                        "last_seen_at": "2026-04-26T12:00:05+00:00",
                    },
                ],
            },
        ],
    }


def _make_active_playback_worker_conflict_detail(
    *,
    title: str = "Coco",
    media_item_id: int = 70,
    playback_mode: str = "full",
    worker_id: str = "worker-1",
    session_id: str = "session-1",
) -> dict[str, object]:
    return {
        "code": "active_playback_worker_exists",
        "active_movie_title": title,
        "active_media_item_id": media_item_id,
        "active_playback_mode": playback_mode,
        "active_worker_id": worker_id,
        "active_session_id": session_id,
        "message": f"{title} is still preparing.",
    }


def _make_playback_worker_cooldown_detail(
    *,
    media_item_id: int = 70,
    remaining_seconds: int = 30,
) -> dict[str, object]:
    return {
        "code": "playback_worker_cooldown",
        "media_item_id": media_item_id,
        "remaining_seconds": remaining_seconds,
        "message": (
            "Your current quota for this movie has been reached. "
            f"Please try again in {remaining_seconds} seconds."
        ),
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

    with get_connection(initialized_settings) as connection:
        session_row = connection.execute(
            """
            SELECT auth_session_id, client_name
            FROM native_playback_sessions
            ORDER BY rowid DESC
            LIMIT 1
            """
        ).fetchone()

    assert session_row is not None
    assert session_row["auth_session_id"] is None
    assert str(session_row["client_name"]).lower().startswith(f"elvern ios {target_app} handoff")


def test_native_external_launch_route_rejects_unsupported_target(client, admin_credentials) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    response = client.get(
        "/api/native-playback/999/launch/mpv",
        headers={"user-agent": IOS_SAFARI_USER_AGENT},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported external playback target"


@pytest.mark.parametrize(
    ("external_player", "expected_prefix"),
    [
        ("vlc", "elvern ios vlc handoff"),
        ("infuse", "elvern ios infuse handoff"),
    ],
)
def test_native_playback_session_route_decouples_ios_external_player_auth_session(
    initialized_settings,
    client,
    admin_credentials,
    monkeypatch,
    external_player: str,
    expected_prefix: str,
) -> None:
    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings: ([], []),
    )
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    item = _create_media_item_record(
        initialized_settings,
        relative_name=f"native-session/{external_player}-external.mp4",
    )

    create_response = client.post(
        f"/api/native-playback/{item['id']}/session",
        headers={"user-agent": IOS_SAFARI_USER_AGENT},
        json={
            "client_name": "Custom Handoff Surface",
            "external_player": external_player,
        },
    )

    assert create_response.status_code == 200
    created_session = create_response.json()

    with get_connection(initialized_settings) as connection:
        session_row = connection.execute(
            """
            SELECT auth_session_id, client_name
            FROM native_playback_sessions
            WHERE session_id = ?
            LIMIT 1
            """,
            (created_session["session_id"],),
        ).fetchone()

    assert session_row is not None
    assert session_row["auth_session_id"] is None
    assert str(session_row["client_name"]).lower().startswith(expected_prefix)


def test_native_playback_session_route_uses_shared_progress_for_ios_vlc_resume_seconds(
    initialized_settings,
    client,
    admin_credentials,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings: ([], []),
    )
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    item = _create_media_item_record(
        initialized_settings,
        relative_name="native-session/ios-vlc-shared-progress.mp4",
    )
    _set_shared_progress(
        initialized_settings,
        user_id=1,
        item_id=int(item["id"]),
        position_seconds=2640.0,
    )

    create_response = client.post(
        f"/api/native-playback/{item['id']}/session",
        headers={"user-agent": IOS_SAFARI_USER_AGENT},
        json={
            "client_name": "Elvern iOS VLC Handoff",
            "external_player": "vlc",
        },
    )

    assert create_response.status_code == 200
    created_session = create_response.json()
    assert created_session["resume_seconds"] == 2640.0

    with get_connection(initialized_settings) as connection:
        session_row = connection.execute(
            """
            SELECT last_position_seconds, last_duration_seconds
            FROM native_playback_sessions
            WHERE session_id = ?
            LIMIT 1
            """,
            (created_session["session_id"],),
        ).fetchone()
        progress_row = connection.execute(
            """
            SELECT position_seconds, duration_seconds, completed
            FROM playback_progress
            WHERE user_id = ? AND media_item_id = ?
            LIMIT 1
            """,
            (1, int(item["id"])),
        ).fetchone()

    assert session_row is not None
    assert float(session_row["last_position_seconds"] or 0.0) == 2640.0
    assert progress_row is not None
    assert float(progress_row["position_seconds"] or 0.0) == 2640.0
    assert bool(progress_row["completed"]) is False


def test_native_playback_session_route_ignores_other_users_progress_for_ios_vlc_resume(
    initialized_settings,
    client,
    admin_credentials,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings: ([], []),
    )
    created_user = _create_standard_user(initialized_settings, username="ios-vlc-other-user")
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    item = _create_media_item_record(
        initialized_settings,
        relative_name="native-session/ios-vlc-other-user.mp4",
    )
    _set_shared_progress(
        initialized_settings,
        user_id=1,
        item_id=int(item["id"]),
        position_seconds=600.0,
    )
    _set_shared_progress(
        initialized_settings,
        user_id=int(created_user["id"]),
        item_id=int(item["id"]),
        position_seconds=1800.0,
    )

    create_response = client.post(
        f"/api/native-playback/{item['id']}/session",
        headers={"user-agent": IOS_SAFARI_USER_AGENT},
        json={
            "client_name": "Elvern iOS VLC Handoff",
            "external_player": "vlc",
        },
    )

    assert create_response.status_code == 200
    created_session = create_response.json()
    assert created_session["resume_seconds"] == 600.0


def test_browser_playback_routes_accept_full_fast_start_estimate_source(
    initialized_settings,
    client,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    item = _create_media_item_record(
        initialized_settings,
        relative_name="browser/full-fast-start-route-shape.mp4",
    )
    payload = _make_browser_playback_route2_payload(item_id=int(item["id"]))
    client.app.state.mobile_playback_manager = BrowserPlaybackRouteManagerStub(payload)

    create_response = client.post(
        "/api/browser-playback/sessions",
        json={
            "item_id": int(item["id"]),
            "profile": "mobile_2160p",
            "playback_mode": "full",
            "start_position_seconds": 837.01,
        },
    )
    assert create_response.status_code == 200
    assert create_response.json()["mode_estimate_source"] == "fast_start_supply_surplus"
    assert create_response.json()["gate_reason"] == "full_fast_start_supply_surplus"

    session_response = client.get(f"/api/browser-playback/sessions/{payload['session_id']}")
    assert session_response.status_code == 200
    assert session_response.json()["mode_estimate_source"] == "fast_start_supply_surplus"
    assert session_response.json()["gate_reason"] == "full_fast_start_supply_surplus"

    active_response = client.get("/api/browser-playback/active")
    assert active_response.status_code == 200
    assert active_response.json()["mode_estimate_source"] == "fast_start_supply_surplus"
    assert active_response.json()["gate_reason"] == "full_fast_start_supply_surplus"

    item_active_response = client.get(f"/api/browser-playback/items/{item['id']}/active")
    assert item_active_response.status_code == 200
    assert item_active_response.json()["mode_estimate_source"] == "fast_start_supply_surplus"
    assert item_active_response.json()["gate_reason"] == "full_fast_start_supply_surplus"

    heartbeat_response = client.post(
        f"/api/browser-playback/sessions/{payload['session_id']}/heartbeat",
        json={
            "committed_playhead_seconds": 840.0,
            "actual_media_element_time_seconds": 3.0,
            "client_attach_revision": 1,
            "lifecycle_state": "attached",
            "playing": True,
        },
    )
    assert heartbeat_response.status_code == 200
    assert heartbeat_response.json()["mode_estimate_source"] == "fast_start_supply_surplus"
    assert heartbeat_response.json()["gate_reason"] == "full_fast_start_supply_surplus"


@pytest.mark.parametrize(
    ("mode_estimate_source", "gate_reason"),
    [
        ("none", "not_full_mode"),
        ("none", "full_gate_not_required"),
        ("none", "lite_slow_supply_unknown_or_deficit"),
        ("none", "startup_projected_runway"),
        ("bootstrap", "full_preflight_bootstrap"),
        ("bootstrap", "full_budget_unavailable_bootstrap"),
        ("bootstrap", "full_bootstrap_server_unknown"),
        ("bootstrap", "full_bootstrap_effective_goodput_unknown"),
        ("bootstrap", "full_budget_waiting_for_client_probe"),
        ("none", "full_budget_waiting_for_client_probe"),
        ("none", "full_budget_waiting_for_stable_eta"),
        ("true", "full_budget_waiting"),
        ("true", "full_budget_complete"),
        ("true", "full_budget_projected_ready"),
        ("fast_start_supply_surplus", "full_fast_start_supply_surplus"),
        ("fast_start_supply_surplus", "full_fast_start_waiting_for_runway"),
        ("true", "lite_fast_supply_surplus"),
    ],
)
def test_browser_playback_create_route_accepts_route2_diagnostic_strings(
    initialized_settings,
    client,
    admin_credentials,
    mode_estimate_source: str,
    gate_reason: str,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    item = _create_media_item_record(
        initialized_settings,
        relative_name=f"browser/route2-diagnostics-{mode_estimate_source}-{gate_reason}.mp4",
    )
    payload = _make_browser_playback_route2_payload(
        item_id=int(item["id"]),
        mode_estimate_source=mode_estimate_source,
        gate_reason=gate_reason,
    )
    client.app.state.mobile_playback_manager = BrowserPlaybackRouteManagerStub(payload)

    create_response = client.post(
        "/api/browser-playback/sessions",
        json={
            "item_id": int(item["id"]),
            "profile": "mobile_2160p",
            "playback_mode": "full",
            "start_position_seconds": 837.01,
        },
    )

    assert create_response.status_code == 200
    body = create_response.json()
    assert body["mode_estimate_source"] == mode_estimate_source
    assert body["gate_reason"] == gate_reason


def test_browser_playback_create_route_returns_structured_active_worker_conflict(
    initialized_settings,
    client,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    item = _create_media_item_record(
        initialized_settings,
        relative_name="browser/route2-active-worker-conflict.mp4",
    )
    stub = BrowserPlaybackRouteManagerStub(_make_browser_playback_route2_payload(item_id=int(item["id"])))
    stub.create_exception = ActivePlaybackWorkerConflictError(
        _make_active_playback_worker_conflict_detail(
            title="Coco",
            media_item_id=70,
            playback_mode="full",
            worker_id="worker-1",
            session_id="session-1",
        )
    )
    client.app.state.mobile_playback_manager = stub

    create_response = client.post(
        "/api/browser-playback/sessions",
        json={
            "item_id": int(item["id"]),
            "profile": "mobile_2160p",
            "playback_mode": "full",
            "start_position_seconds": 12.0,
        },
    )

    assert create_response.status_code == 409
    assert create_response.json()["detail"] == _make_active_playback_worker_conflict_detail(
        title="Coco",
        media_item_id=70,
        playback_mode="full",
        worker_id="worker-1",
        session_id="session-1",
    )


@pytest.mark.parametrize("playback_mode", ["lite", "full"])
def test_browser_playback_create_route_returns_structured_worker_cooldown(
    initialized_settings,
    client,
    admin_credentials,
    playback_mode: str,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    item = _create_media_item_record(
        initialized_settings,
        relative_name=f"browser/route2-worker-cooldown-{playback_mode}.mp4",
    )
    stub = BrowserPlaybackRouteManagerStub(_make_browser_playback_route2_payload(item_id=int(item["id"])))
    stub.browser_cooldown_exception = PlaybackWorkerCooldownError(
        _make_playback_worker_cooldown_detail(
            media_item_id=int(item["id"]),
            remaining_seconds=27,
        )
    )
    client.app.state.mobile_playback_manager = stub

    create_response = client.post(
        "/api/browser-playback/sessions",
        json={
            "item_id": int(item["id"]),
            "profile": "mobile_2160p",
            "playback_mode": playback_mode,
            "start_position_seconds": 12.0,
        },
    )

    assert create_response.status_code == 409
    assert create_response.json()["detail"] == _make_playback_worker_cooldown_detail(
        media_item_id=int(item["id"]),
        remaining_seconds=27,
    )
    assert stub.create_calls == 0


def test_admin_playback_workers_route_returns_route2_worker_registry(
    client,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    stub = AdminPlaybackWorkerManagerStub(_make_admin_playback_workers_payload())
    client.app.state.mobile_playback_manager = stub

    response = client.get("/api/admin/playback-workers")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cpu_upbound_percent"] == 90
    assert payload["cpu_budget_percent"] == 90
    assert payload["total_cpu_cores"] == 20
    assert payload["route2_cpu_upbound_cores"] == 18
    assert payload["total_route2_budget_cores"] == 18
    assert payload["route2_cpu_percent_of_total"] == 36.0
    assert payload["route2_memory_bytes"] == 2147483648
    assert payload["active_worker_count"] == 1
    assert payload["queued_worker_count"] == 2
    assert payload["active_decoding_user_count"] == 2
    assert payload["per_user_budget_cores"] == 9
    assert len(payload["workers_by_user"]) == 2
    assert payload["workers_by_user"][0]["allocated_cpu_cores"] == 9
    assert payload["workers_by_user"][0]["items"][0]["assigned_threads"] == 6
    assert payload["workers_by_user"][0]["items"][0]["cpu_cores_used"] == 7.2


def test_admin_terminate_playback_worker_route_stops_owned_worker(
    client,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    stub = AdminPlaybackWorkerManagerStub(_make_admin_playback_workers_payload())
    client.app.state.mobile_playback_manager = stub

    response = client.post("/api/admin/playback-workers/worker-1/terminate")

    assert response.status_code == 200
    assert response.json()["message"] == "Playback worker terminated"
    assert stub.terminated_worker_ids == ["worker-1"]
    assert stub.terminated_worker_cooldown_flags == [True]


def test_admin_terminate_playback_worker_route_returns_404_for_unknown_worker(
    client,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    stub = AdminPlaybackWorkerManagerStub(_make_admin_playback_workers_payload())
    stub.terminate_result = False
    client.app.state.mobile_playback_manager = stub

    response = client.post("/api/admin/playback-workers/missing-worker/terminate")

    assert response.status_code == 404
    assert response.json()["detail"] == "Playback worker not found"
    assert stub.terminated_worker_ids == ["missing-worker"]
    assert stub.terminated_worker_cooldown_flags == [True]


def test_non_admin_cannot_terminate_playback_worker(
    initialized_settings,
    client,
) -> None:
    created_user = _create_standard_user(initialized_settings, username="route2-non-admin")
    _login(client, username=created_user["username"], password="family-password")
    stub = AdminPlaybackWorkerManagerStub(_make_admin_playback_workers_payload())
    client.app.state.mobile_playback_manager = stub

    response = client.post("/api/admin/playback-workers/worker-1/terminate")

    assert response.status_code == 403
    assert stub.terminated_worker_ids == []


def test_admin_disable_user_route_invalidates_route2_workers(
    initialized_settings,
    client,
    admin_credentials,
) -> None:
    created_user = _create_standard_user(initialized_settings, username="route2-disable-user")
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    stub = AdminPlaybackWorkerManagerStub(_make_admin_playback_workers_payload())
    client.app.state.mobile_playback_manager = stub

    response = client.patch(
        f"/api/admin/users/{created_user['id']}",
        json={"enabled": False},
    )

    assert response.status_code == 200
    assert stub.invalidated_user_calls == [(int(created_user["id"]), "user_disabled")]


def test_admin_revoke_session_route_invalidates_route2_workers(
    initialized_settings,
    client,
    admin_credentials,
) -> None:
    created_user = _create_standard_user(initialized_settings, username="route2-revoke-user")
    _login(client, username=str(created_user["username"]), password="family-password")
    with get_connection(initialized_settings) as connection:
        session_row = connection.execute(
            """
            SELECT id
            FROM sessions
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(created_user["id"]),),
        ).fetchone()
    assert session_row is not None

    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    stub = AdminPlaybackWorkerManagerStub(_make_admin_playback_workers_payload())
    client.app.state.mobile_playback_manager = stub

    response = client.post(f"/api/admin/sessions/{int(session_row['id'])}/revoke")

    assert response.status_code == 200
    assert stub.invalidated_auth_session_calls == [(int(session_row["id"]), "admin_revoked")]


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

    with get_connection(initialized_settings) as connection:
        session_row = connection.execute(
            """
            SELECT auth_session_id
            FROM native_playback_sessions
            WHERE session_id = ?
            LIMIT 1
            """,
            (created_session["session_id"],),
        ).fetchone()

    assert session_row is not None
    assert session_row["auth_session_id"] is not None

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
