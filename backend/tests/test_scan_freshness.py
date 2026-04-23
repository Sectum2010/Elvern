from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from backend.app.media_scan import build_local_library_freshness_snapshot
from backend.app.services.scan_service import ScanService


class _RecordingThread:
    created: list["_RecordingThread"] = []

    def __init__(self, *, target, args, daemon, name) -> None:
        self.target = target
        self.args = args
        self.daemon = daemon
        self.name = name
        self.started = False
        self.__class__.created.append(self)

    def start(self) -> None:
        self.started = True


def test_startup_scan_allows_full_scan_without_prior_snapshot(initialized_settings) -> None:
    service = ScanService(initialized_settings)

    should_scan, reason = service._should_enqueue_startup_scan()

    assert should_scan is True
    assert reason == "Startup scan allowed: no prior local library freshness snapshot."


def test_startup_scan_skips_when_root_identity_and_top_level_state_match(initialized_settings) -> None:
    service = ScanService(initialized_settings)
    snapshot = build_local_library_freshness_snapshot(initialized_settings)
    service._store_local_library_freshness_snapshot(snapshot)

    should_scan, reason = service._should_enqueue_startup_scan()

    assert should_scan is False
    assert reason == "Startup scan skipped: local library top-level state matches the last successful scan."


def test_startup_scan_allows_when_media_root_identity_changes(initialized_settings, tmp_path) -> None:
    original_service = ScanService(initialized_settings)
    original_service._store_local_library_freshness_snapshot(
        build_local_library_freshness_snapshot(initialized_settings)
    )

    replacement_root = tmp_path / "other-media"
    replacement_root.mkdir()
    changed_settings = replace(initialized_settings, media_root=replacement_root.resolve())
    changed_service = ScanService(changed_settings)

    should_scan, reason = changed_service._should_enqueue_startup_scan()

    assert should_scan is True
    assert reason == "Startup scan allowed: media root identity changed."


def test_startup_scan_allows_when_top_level_state_changes(initialized_settings) -> None:
    service = ScanService(initialized_settings)
    service._store_local_library_freshness_snapshot(
        build_local_library_freshness_snapshot(initialized_settings)
    )

    (Path(initialized_settings.media_root) / "New Movie Folder").mkdir()

    should_scan, reason = service._should_enqueue_startup_scan()

    assert should_scan is True
    assert reason == "Startup scan allowed: top-level library state changed."


def test_opportunistic_refresh_enqueues_scan_without_prior_snapshot(initialized_settings, monkeypatch) -> None:
    service = ScanService(initialized_settings)
    enqueued_reasons: list[str] = []

    monkeypatch.setattr(
        service,
        "enqueue_scan",
        lambda *, reason: enqueued_reasons.append(reason) or {"running": True, "reason": reason, "message": "Scan started"},
    )

    result = service.maybe_refresh_local_library(trigger="library")

    assert result == {
        "checked": True,
        "scan_enqueued": True,
        "message": "Startup scan allowed: no prior local library freshness snapshot.",
    }
    assert enqueued_reasons == ["opportunistic"]


def test_opportunistic_refresh_respects_backend_cooldown(initialized_settings, monkeypatch) -> None:
    service = ScanService(initialized_settings)
    enqueued_reasons: list[str] = []

    monkeypatch.setattr(
        service,
        "enqueue_scan",
        lambda *, reason: enqueued_reasons.append(reason) or {"running": True, "reason": reason, "message": "Scan started"},
    )

    first = service.maybe_refresh_local_library(trigger="session")
    second = service.maybe_refresh_local_library(trigger="library")

    assert first["checked"] is True
    assert first["scan_enqueued"] is True
    assert second == {
        "checked": False,
        "scan_enqueued": False,
        "message": "Local library freshness check skipped: cooldown active.",
    }
    assert enqueued_reasons == ["opportunistic"]


def test_opportunistic_refresh_skips_scan_when_snapshot_is_unchanged(initialized_settings) -> None:
    service = ScanService(initialized_settings)
    service._store_local_library_freshness_snapshot(
        build_local_library_freshness_snapshot(initialized_settings)
    )

    result = service.maybe_refresh_local_library(trigger="library")

    assert result == {
        "checked": True,
        "scan_enqueued": False,
        "message": "Startup scan skipped: local library top-level state matches the last successful scan.",
    }


def test_opportunistic_refresh_enqueues_scan_when_snapshot_is_dirty(initialized_settings, monkeypatch) -> None:
    service = ScanService(initialized_settings)
    service._store_local_library_freshness_snapshot(
        build_local_library_freshness_snapshot(initialized_settings)
    )
    enqueued_reasons: list[str] = []

    monkeypatch.setattr(
        service,
        "enqueue_scan",
        lambda *, reason: enqueued_reasons.append(reason) or {"running": True, "reason": reason, "message": "Scan started"},
    )
    (Path(initialized_settings.media_root) / "Freshness Dirty Folder").mkdir()

    result = service.maybe_refresh_local_library(trigger="library")

    assert result == {
        "checked": True,
        "scan_enqueued": True,
        "message": "Startup scan allowed: top-level library state changed.",
    }
    assert enqueued_reasons == ["opportunistic"]


def test_opportunistic_refresh_noops_while_scan_is_running(initialized_settings) -> None:
    service = ScanService(initialized_settings)
    service._update_state(running=True)

    result = service.maybe_refresh_local_library(trigger="library")

    assert result == {
        "checked": False,
        "scan_enqueued": False,
        "message": "A scan is already running.",
    }


def test_manual_rescan_still_forces_full_scan(initialized_settings, monkeypatch) -> None:
    service = ScanService(initialized_settings)
    service._store_local_library_freshness_snapshot(
        build_local_library_freshness_snapshot(initialized_settings)
    )

    _RecordingThread.created = []
    monkeypatch.setattr(
        "backend.app.services.scan_service.threading.Thread",
        _RecordingThread,
    )

    startup_state = service.enqueue_scan(reason="startup")
    assert startup_state["running"] is False
    assert startup_state["message"] == "Startup scan skipped: local library top-level state matches the last successful scan."
    assert _RecordingThread.created == []

    manual_state = service.enqueue_scan(reason="manual")

    assert manual_state["running"] is True
    assert manual_state["reason"] == "manual"
    assert manual_state["message"] == "Scan started"
    assert len(_RecordingThread.created) == 1
    assert _RecordingThread.created[0].started is True


def test_successful_scan_persists_freshness_snapshot(initialized_settings, monkeypatch) -> None:
    service = ScanService(initialized_settings)
    snapshot = build_local_library_freshness_snapshot(initialized_settings)

    monkeypatch.setattr(
        "backend.app.services.scan_service.scan_media_library",
        lambda settings, reason: {
            "job_id": 1,
            "started_at": "2026-04-22T00:00:00+00:00",
            "finished_at": "2026-04-22T00:00:01+00:00",
            "reason": reason,
            "running": False,
            "files_seen": 0,
            "files_changed": 0,
            "files_removed": 0,
            "message": "Scan completed",
        },
    )
    monkeypatch.setattr(
        "backend.app.services.scan_service.build_local_library_freshness_snapshot",
        lambda settings: snapshot,
    )

    assert service._job_lock.acquire(blocking=False) is True
    service._run_scan("startup", "2026-04-22T00:00:00+00:00")

    assert service._load_local_library_freshness_snapshot() == snapshot


def test_login_me_and_library_routes_trigger_opportunistic_refresh(client, admin_credentials, monkeypatch) -> None:
    triggers: list[str] = []

    monkeypatch.setattr(
        client.app.state.scan_service,
        "maybe_refresh_local_library",
        lambda *, trigger: triggers.append(trigger) or {"checked": True, "scan_enqueued": False, "message": "ok"},
        raising=False,
    )

    login_response = client.post("/api/auth/login", json=admin_credentials)

    assert login_response.status_code == 200
    assert triggers == ["login"]

    triggers.clear()
    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert triggers == ["session"]

    triggers.clear()
    library_response = client.get("/api/library")
    assert library_response.status_code == 200
    assert triggers == ["library"]
