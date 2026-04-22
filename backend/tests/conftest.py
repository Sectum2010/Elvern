from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from backend.app.auth import ensure_admin_user
from backend.app.config import refresh_settings
from backend.app.db import init_db


class DummyScanService:
    def __init__(self, settings) -> None:
        self.settings = settings
        self._state = {
            "running": False,
            "job_id": None,
            "started_at": None,
            "finished_at": None,
            "reason": None,
            "files_seen": 0,
            "files_changed": 0,
            "files_removed": 0,
            "message": None,
        }

    def get_state(self) -> dict[str, object]:
        return dict(self._state)

    def enqueue_scan(self, *, reason: str) -> dict[str, object]:
        state = self.get_state()
        state["reason"] = reason
        state["message"] = "scan skipped in tests"
        return state


class DummyTranscodeManager:
    def __init__(self, settings) -> None:
        self.settings = settings

    def start(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def get_debug_status(self) -> dict[str, object]:
        return {
            "enabled": False,
            "ffmpeg_available": False,
            "cache_dir": str(self.settings.transcode_dir),
            "ttl_minutes": self.settings.transcode_ttl_minutes,
            "max_concurrent_transcodes": self.settings.max_concurrent_transcodes,
            "active_jobs": [],
            "last_error": None,
        }


class DummyMobilePlaybackManager:
    def __init__(self, settings) -> None:
        self.settings = settings

    def start(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


class DummyAdminEventHub:
    def start(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


@pytest.fixture()
def test_settings(tmp_path, monkeypatch):
    media_root = tmp_path / "media"
    media_root.mkdir()
    db_path = tmp_path / "backend" / "data" / "test.db"
    helper_releases_dir = tmp_path / "backend" / "data" / "helper_releases"
    transcode_dir = tmp_path / "backend" / "data" / "transcodes"

    monkeypatch.setenv("ELVERN_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("ELVERN_DB_PATH", str(db_path))
    monkeypatch.setenv("ELVERN_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ELVERN_ADMIN_BOOTSTRAP_PASSWORD", "test-admin-password")
    monkeypatch.setenv("ELVERN_SESSION_SECRET", "test-session-secret-value-with-32-chars")
    monkeypatch.setenv("ELVERN_COOKIE_SECURE", "false")
    monkeypatch.setenv("ELVERN_SCAN_ON_STARTUP", "false")
    monkeypatch.setenv("ELVERN_TRANSCODE_ENABLED", "false")
    monkeypatch.setenv("ELVERN_BROWSER_PLAYBACK_ROUTE2_ENABLED", "false")
    monkeypatch.setenv("ELVERN_HELPER_RELEASES_DIR", str(helper_releases_dir))
    monkeypatch.setenv("ELVERN_TRANSCODE_DIR", str(transcode_dir))

    settings = refresh_settings()
    yield settings
    refresh_settings()


@pytest.fixture()
def initialized_settings(test_settings):
    init_db(test_settings)
    ensure_admin_user(test_settings)
    return test_settings


@pytest.fixture()
def client(initialized_settings, monkeypatch):
    import backend.app.main as main_module

    importlib.reload(main_module)
    monkeypatch.setattr(main_module, "ScanService", DummyScanService)
    monkeypatch.setattr(main_module, "TranscodeManager", DummyTranscodeManager)
    monkeypatch.setattr(main_module, "MobilePlaybackManager", DummyMobilePlaybackManager)
    monkeypatch.setattr(main_module, "admin_event_hub", DummyAdminEventHub())

    with TestClient(main_module.app) as test_client:
        yield test_client


@pytest.fixture()
def admin_credentials(initialized_settings) -> dict[str, str]:
    return {
        "username": initialized_settings.admin_username,
        "password": initialized_settings.admin_bootstrap_password or "",
    }
