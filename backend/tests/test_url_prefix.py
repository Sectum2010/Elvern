from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from backend.app.config import refresh_settings
from backend.app.db import get_connection, init_db, utcnow_iso
from backend.app.security import hash_password
from backend.app.spa_static import clear_manifest_cache, install_manifest_middleware, mount_spa, render_manifest_for_prefix
from backend.app.url_prefix_service import (
    URL_PREFIX_ALPHABET,
    URL_PREFIX_LENGTH,
    UrlPrefixState,
    generate_url_prefix,
    load_state,
    resolve_url_prefix,
    rotate_url_prefix,
    save_state,
    url_prefix_state_path,
)


def _login(client, *, username: str = "admin", password: str = "test-admin-password") -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


@pytest.fixture()
def spa_frontend_dist(tmp_path):
    dist = tmp_path / "frontend-dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "icons").mkdir()
    (dist / "index.html").write_text(
        "<!doctype html><html><head><title>Elvern</title></head><body><div id=\"root\"></div></body></html>",
        encoding="utf-8",
    )
    (dist / "manifest.webmanifest").write_text(
        json.dumps(
            {
                "name": "Elvern",
                "start_url": "/library",
                "scope": "/",
                "icons": [
                    {
                        "src": "/icons/icon-192.png",
                        "sizes": "192x192",
                        "type": "image/png",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (dist / "assets" / "test.js").write_text(
        "export const elvernSpaAssetMimeCheck = true;\n",
        encoding="utf-8",
    )
    (dist / "sw.js").write_text("self.addEventListener('fetch', () => {});\n", encoding="utf-8")
    (dist / "offline.html").write_text("<!doctype html><title>Elvern Offline</title>", encoding="utf-8")
    return dist


@pytest.fixture()
def spa_client(spa_frontend_dist):
    clear_manifest_cache()
    app = FastAPI()
    app.state.url_prefix = "testprefix"

    @app.get("/api/auth/me")
    def auth_me():
        return Response(status_code=401)

    install_manifest_middleware(app, frontend_dist=spa_frontend_dist)
    mount_spa(app, prefix="testprefix", frontend_dist=spa_frontend_dist)
    with TestClient(app) as test_client:
        yield test_client
    clear_manifest_cache()


class TestUrlPrefixGeneration:
    def test_generates_correct_length(self) -> None:
        assert len(generate_url_prefix()) == URL_PREFIX_LENGTH

    def test_uses_safe_alphabet(self) -> None:
        prefix = generate_url_prefix()
        assert set(prefix) <= set(URL_PREFIX_ALPHABET)
        assert not (set(prefix) & set("01ilo"))

    def test_each_call_unique(self) -> None:
        assert len({generate_url_prefix() for _ in range(100)}) == 100


class TestUrlPrefixState:
    def test_round_trip_save_load(self, test_settings) -> None:
        state = UrlPrefixState(prefix="abcdefgh", generated_at=utcnow_iso(), rotated_count=2)
        save_state(test_settings, state)
        assert load_state(test_settings) == state

    def test_corrupted_file_returns_none(self, test_settings) -> None:
        path = url_prefix_state_path(test_settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert load_state(test_settings) is None

    def test_atomic_write_no_partial(self, test_settings) -> None:
        state = UrlPrefixState(prefix="abcdefgh", generated_at=utcnow_iso(), rotated_count=0)
        save_state(test_settings, state)
        path = url_prefix_state_path(test_settings)
        assert path.exists()
        assert not path.with_name(f"{path.name}.tmp").exists()
        assert json.loads(path.read_text(encoding="utf-8"))["prefix"] == "abcdefgh"


class TestResolveUrlPrefix:
    def test_env_override_skips_state(self, test_settings, monkeypatch) -> None:
        monkeypatch.setenv("ELVERN_URL_PREFIX", "h2k7m4p9")
        settings = refresh_settings()
        save_state(
            settings,
            UrlPrefixState(prefix="abcdefgh", generated_at=utcnow_iso(), rotated_count=0),
        )
        assert resolve_url_prefix(settings, logging.getLogger("test")) == "h2k7m4p9"
        assert load_state(settings).prefix == "abcdefgh"

    def test_first_start_generates_and_persists(self, test_settings) -> None:
        prefix = resolve_url_prefix(test_settings, logging.getLogger("test"))
        state = load_state(test_settings)
        assert state is not None
        assert state.prefix == prefix

    def test_subsequent_start_loads_persisted(self, test_settings) -> None:
        save_state(
            test_settings,
            UrlPrefixState(prefix="abcdefgh", generated_at=utcnow_iso(), rotated_count=0),
        )
        assert resolve_url_prefix(test_settings, logging.getLogger("test")) == "abcdefgh"

    def test_force_rotation_env_var(self, initialized_settings, monkeypatch) -> None:
        test_settings = initialized_settings
        save_state(
            test_settings,
            UrlPrefixState(prefix="abcdefgh", generated_at=utcnow_iso(), rotated_count=0),
        )
        with get_connection(test_settings) as connection:
            connection.execute(
                """
                INSERT INTO sessions (user_id, session_token_hash, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (1, "hash", utcnow_iso(), utcnow_iso()),
            )
            connection.commit()
        monkeypatch.setenv("ELVERN_FORCE_NEW_URL_PREFIX", "1")
        prefix = resolve_url_prefix(test_settings, logging.getLogger("test"))
        assert prefix != "abcdefgh"
        assert load_state(test_settings).rotated_count == 1
        with get_connection(test_settings) as connection:
            assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0

    def test_age_warning_logged_when_old(self, test_settings, caplog) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=181)).isoformat()
        save_state(test_settings, UrlPrefixState(prefix="abcdefgh", generated_at=old, rotated_count=0))
        with caplog.at_level(logging.WARNING):
            assert resolve_url_prefix(test_settings, logging.getLogger("test")) == "abcdefgh"
        assert "URL prefix is" in caplog.text


class TestRotateUrlPrefix:
    def test_rotation_creates_new_prefix(self, test_settings) -> None:
        init_db(test_settings)
        save_state(test_settings, UrlPrefixState(prefix="abcdefgh", generated_at=utcnow_iso(), rotated_count=0))
        with get_connection(test_settings) as connection:
            old_prefix, new_prefix = rotate_url_prefix(test_settings, connection)
            connection.commit()
        assert old_prefix == "abcdefgh"
        assert new_prefix != old_prefix
        assert load_state(test_settings).prefix == new_prefix

    def test_rotation_revokes_all_sessions(self, initialized_settings) -> None:
        save_state(initialized_settings, UrlPrefixState(prefix="abcdefgh", generated_at=utcnow_iso(), rotated_count=0))
        with get_connection(initialized_settings) as connection:
            connection.execute(
                """
                INSERT INTO sessions (user_id, session_token_hash, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (1, "hash", utcnow_iso(), utcnow_iso()),
            )
            rotate_url_prefix(initialized_settings, connection)
            connection.commit()
            assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0

    def test_rotation_revokes_transient_access_surfaces(self, initialized_settings) -> None:
        save_state(initialized_settings, UrlPrefixState(prefix="abcdefgh", generated_at=utcnow_iso(), rotated_count=0))
        now = utcnow_iso()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with get_connection(initialized_settings) as connection:
            session_cursor = connection.execute(
                """
                INSERT INTO sessions (user_id, session_token_hash, created_at, expires_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (1, "rotate-session-hash", now, future, now),
            )
            session_id = int(session_cursor.lastrowid)
            media_cursor = connection.execute(
                """
                INSERT INTO media_items (
                    title,
                    original_filename,
                    file_path,
                    source_kind,
                    file_size,
                    file_mtime,
                    created_at,
                    updated_at,
                    last_scanned_at
                ) VALUES (?, ?, ?, 'local', ?, ?, ?, ?, ?)
                """,
                (
                    "Rotate Movie",
                    "rotate.mp4",
                    str(Path(initialized_settings.media_root) / "rotate.mp4"),
                    1,
                    1.0,
                    now,
                    now,
                    now,
                ),
            )
            media_id = int(media_cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO download_sessions (
                    session_token_hash, user_id, media_item_id, auth_session_id, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("download-hash", 1, media_id, session_id, now, future),
            )
            connection.execute(
                """
                INSERT INTO native_playback_sessions (
                    session_id, access_token_hash, user_id, media_item_id,
                    created_at, expires_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("native-session", "native-hash", 1, media_id, now, future, now),
            )
            connection.execute(
                """
                INSERT INTO desktop_vlc_handoffs (
                    handoff_id, access_token_hash, auth_session_id, user_id, media_item_id,
                    platform, strategy, resolved_target, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("handoff-id", "handoff-hash", session_id, 1, media_id, "linux", "backend_url", "vlc", now, future),
            )
            connection.execute(
                """
                INSERT INTO login_challenges (
                    challenge_token_hash, user_id, created_at, expires_at_unix, ip_address, user_agent
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("challenge-hash", 1, now, datetime.now(timezone.utc).timestamp() + 300, "127.0.0.1", "pytest"),
            )
            connection.execute(
                """
                INSERT INTO google_oauth_states (state_token, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                ("oauth-state", 1, now, future),
            )
            connection.execute(
                """
                INSERT INTO invite_codes (code_hash, created_by_user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                ("invite-hash", 1, now, future),
            )

            rotate_url_prefix(initialized_settings, connection)
            connection.commit()

            assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM login_challenges").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM google_oauth_states").fetchone()[0] == 0
            assert connection.execute("SELECT revoked_at FROM download_sessions").fetchone()[0] is not None
            assert connection.execute("SELECT revoked_at FROM native_playback_sessions").fetchone()[0] is not None
            assert connection.execute("SELECT revoked_at FROM desktop_vlc_handoffs").fetchone()[0] is not None
            assert connection.execute("SELECT revoked_at FROM invite_codes").fetchone()[0] is not None

    def test_rotation_writes_security_event(self, initialized_settings) -> None:
        save_state(initialized_settings, UrlPrefixState(prefix="abcdefgh", generated_at=utcnow_iso(), rotated_count=0))
        with get_connection(initialized_settings) as connection:
            rotate_url_prefix(initialized_settings, connection, actor_user_id=1, actor_username="admin")
            connection.commit()
            row = connection.execute(
                "SELECT event_kind, actor_user_id, actor_username FROM security_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row["event_kind"] == "url_prefix_rotated"
        assert row["actor_user_id"] == 1
        assert row["actor_username"] == "admin"

    def test_rotation_count_increments(self, test_settings) -> None:
        init_db(test_settings)
        save_state(test_settings, UrlPrefixState(prefix="abcdefgh", generated_at=utcnow_iso(), rotated_count=3))
        with get_connection(test_settings) as connection:
            rotate_url_prefix(test_settings, connection)
            connection.commit()
        assert load_state(test_settings).rotated_count == 4


class TestUrlPrefixRouting:
    def test_root_path_404(self, spa_client) -> None:
        assert spa_client.get("/").status_code == 404

    def test_unknown_prefix_404(self, spa_client) -> None:
        assert spa_client.get("/wrongprefix/library").status_code == 404

    def test_correct_prefix_serves_spa(self, spa_client) -> None:
        prefix = spa_client.app.state.url_prefix
        response = spa_client.get(f"/{prefix}/library")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_api_path_unaffected(self, spa_client) -> None:
        assert spa_client.get("/api/auth/me").status_code in {200, 401}


class TestSpaServing:
    def test_html_route_falls_back_to_index(self, spa_client) -> None:
        prefix = spa_client.app.state.url_prefix
        response = spa_client.get(f"/{prefix}/library")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert f'<base href="/{prefix}/">' in response.text
        assert response.headers["x-elvern-app-shell"] == "1"

    def test_trailing_slash_library_route_still_serves_spa_document(self, spa_client) -> None:
        prefix = spa_client.app.state.url_prefix
        response = spa_client.get(f"/{prefix}/library/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert f'<base href="/{prefix}/">' in response.text
        assert response.headers["x-elvern-app-shell"] == "1"

    @pytest.mark.parametrize("asset_name", ["sw.js", "offline.html"])
    def test_offline_shell_assets_are_never_http_cached(self, spa_client, asset_name) -> None:
        prefix = spa_client.app.state.url_prefix
        response = spa_client.get(f"/{prefix}/{asset_name}")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"

    def test_nested_html_route_uses_prefix_base_href(self, spa_client) -> None:
        prefix = spa_client.app.state.url_prefix
        response = spa_client.get(f"/{prefix}/setup/totp")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert f'<base href="/{prefix}/">' in response.text

    def test_missing_asset_returns_404_not_index(self, spa_client) -> None:
        prefix = spa_client.app.state.url_prefix
        response = spa_client.get(f"/{prefix}/assets/nonexistent.js")
        assert response.status_code == 404
        assert b"<html" not in response.content.lower()

    def test_nested_missing_asset_returns_404_not_index(self, spa_client) -> None:
        prefix = spa_client.app.state.url_prefix
        response = spa_client.get(f"/{prefix}/setup/assets/nonexistent.js")
        assert response.status_code == 404
        assert b"<html" not in response.content.lower()

    def test_existing_asset_returns_correct_mime(self, spa_client) -> None:
        prefix = spa_client.app.state.url_prefix
        response = spa_client.get(f"/{prefix}/assets/test.js")
        assert response.status_code == 200
        assert response.headers["content-type"].split(";")[0] in {
            "text/javascript",
            "application/javascript",
        }
        assert b"elvernSpaAssetMimeCheck" in response.content

    def test_root_prefix_serves_index_html(self, spa_client) -> None:
        prefix = spa_client.app.state.url_prefix
        response = spa_client.get(f"/{prefix}/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert b"<html" in response.content.lower()
        assert f'<base href="/{prefix}/">' in response.text


class TestManifestRendering:
    def test_manifest_start_url_prefixed(self, tmp_path) -> None:
        clear_manifest_cache()
        (tmp_path / "manifest.webmanifest").write_text(
            json.dumps({"start_url": "/library", "scope": "/", "icons": []}),
            encoding="utf-8",
        )
        payload = json.loads(render_manifest_for_prefix("xyzabc23", tmp_path))
        assert payload["start_url"] == "/xyzabc23/library"

    def test_manifest_scope_prefixed(self, tmp_path) -> None:
        clear_manifest_cache()
        (tmp_path / "manifest.webmanifest").write_text(
            json.dumps({"start_url": "/library", "scope": "/", "icons": []}),
            encoding="utf-8",
        )
        payload = json.loads(render_manifest_for_prefix("xyzabc23", tmp_path))
        assert payload["scope"] == "/xyzabc23/"

    def test_manifest_icon_src_prefixed(self, tmp_path) -> None:
        clear_manifest_cache()
        (tmp_path / "manifest.webmanifest").write_text(
            json.dumps(
                {
                    "start_url": "/library",
                    "scope": "/",
                    "icons": [{"src": "/icons/icon-192.png?v=2"}],
                }
            ),
            encoding="utf-8",
        )
        payload = json.loads(render_manifest_for_prefix("xyzabc23", tmp_path))
        assert payload["icons"][0]["src"] == "/xyzabc23/icons/icon-192.png?v=2"

    def test_manifest_already_prefixed_paths_not_double_prefixed(self, tmp_path) -> None:
        clear_manifest_cache()
        (tmp_path / "manifest.webmanifest").write_text(
            json.dumps(
                {
                    "start_url": "/xyzabc23/library",
                    "scope": "/xyzabc23/",
                    "icons": [{"src": "/xyzabc23/icons/icon-192.png?v=2"}],
                }
            ),
            encoding="utf-8",
        )
        payload = json.loads(render_manifest_for_prefix("xyzabc23", tmp_path))
        assert payload["start_url"] == "/xyzabc23/library"
        assert payload["scope"] == "/xyzabc23/"
        assert payload["icons"][0]["src"] == "/xyzabc23/icons/icon-192.png?v=2"

    def test_manifest_cache_returns_same_bytes(self, tmp_path) -> None:
        clear_manifest_cache()
        (tmp_path / "manifest.webmanifest").write_text(
            json.dumps({"start_url": "/library", "scope": "/", "icons": []}),
            encoding="utf-8",
        )
        first = render_manifest_for_prefix("xyzabc23", tmp_path)
        second = render_manifest_for_prefix("xyzabc23", tmp_path)
        assert first == second

    def test_clear_cache_invalidates(self, tmp_path) -> None:
        clear_manifest_cache()
        manifest_path = tmp_path / "manifest.webmanifest"
        manifest_path.write_text(
            json.dumps({"start_url": "/library", "scope": "/", "icons": []}),
            encoding="utf-8",
        )
        first = json.loads(render_manifest_for_prefix("xyzabc23", tmp_path))
        manifest_path.write_text(
            json.dumps({"start_url": "/login", "scope": "/", "icons": []}),
            encoding="utf-8",
        )
        clear_manifest_cache()
        second = json.loads(render_manifest_for_prefix("xyzabc23", tmp_path))
        assert first["start_url"] == "/xyzabc23/library"
        assert second["start_url"] == "/xyzabc23/login"


class TestManifestEndpoint:
    def test_manifest_served_at_prefixed_path(self, spa_client) -> None:
        prefix = spa_client.app.state.url_prefix
        response = spa_client.get(f"/{prefix}/manifest.webmanifest")
        assert response.status_code == 200
        assert response.headers["content-type"].split(";")[0] == "application/manifest+json"
        payload = response.json()
        assert payload["start_url"] == f"/{prefix}/library"
        assert payload["scope"] == f"/{prefix}/"
        assert all(icon["src"].startswith(f"/{prefix}/icons/") for icon in payload["icons"])

    def test_manifest_at_root_path_404(self, spa_client) -> None:
        assert spa_client.get("/manifest.webmanifest").status_code == 404

    def test_manifest_at_wrong_prefix_404(self, spa_client) -> None:
        assert spa_client.get("/wrongprefix/manifest.webmanifest").status_code == 404


class TestManifestMiddleware:
    @pytest.fixture()
    def app_with_full_mount(self, tmp_path):
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html></html>", encoding="utf-8")
        (dist / "manifest.webmanifest").write_text(
            json.dumps(
                {
                    "name": "Elvern",
                    "start_url": "/library",
                    "scope": "/",
                    "icons": [
                        {
                            "src": "/icons/icon-192.png",
                            "sizes": "192x192",
                            "type": "image/png",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        app = FastAPI()
        app.state.url_prefix = "testprefix"
        install_manifest_middleware(app, frontend_dist=dist)
        mount_spa(app, prefix="testprefix", frontend_dist=dist)
        return app

    def test_manifest_intercepted_before_mount(self, app_with_full_mount) -> None:
        client = TestClient(app_with_full_mount)
        response = client.get("/testprefix/manifest.webmanifest")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/manifest+json"
        data = response.json()
        assert data["start_url"] == "/testprefix/library"
        assert data["scope"] == "/testprefix/"
        assert data["icons"][0]["src"] == "/testprefix/icons/icon-192.png"

    def test_other_static_assets_not_intercepted(self, app_with_full_mount) -> None:
        client = TestClient(app_with_full_mount)
        response = client.get("/testprefix/")
        assert response.status_code == 200
        assert "html" in response.headers.get("content-type", "")

    def test_root_manifest_path_not_intercepted(self, app_with_full_mount) -> None:
        client = TestClient(app_with_full_mount)
        response = client.get("/manifest.webmanifest")
        assert response.status_code == 404


class TestAdminRotateEndpoint:
    def test_requires_admin_role(self, client, initialized_settings) -> None:
        with get_connection(initialized_settings) as connection:
            now = utcnow_iso()
            connection.execute(
                """
                INSERT INTO users (username, password_hash, role, enabled, created_at, updated_at)
                VALUES (?, ?, 'standard_user', 1, ?, ?)
                """,
                ("standard-user", hash_password("standard-password", initialized_settings), now, now),
            )
            connection.commit()
        response = client.post("/api/auth/login", json={"username": "standard-user", "password": "standard-password"})
        assert response.status_code == 200
        rotate_response = client.post(
            "/api/admin/url-prefix/rotate",
            json={"current_admin_password": "standard-password"},
        )
        assert rotate_response.status_code == 403

    def test_requires_correct_password(self, client, admin_credentials) -> None:
        _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
        response = client.post(
            "/api/admin/url-prefix/rotate",
            json={"current_admin_password": "wrong-password"},
        )
        assert response.status_code == 401

    def test_writes_audit_event_on_success(self, client, initialized_settings, admin_credentials) -> None:
        _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
        response = client.post(
            "/api/admin/url-prefix/rotate",
            json={"current_admin_password": admin_credentials["password"]},
        )
        assert response.status_code == 200
        with get_connection(initialized_settings) as connection:
            row = connection.execute(
                "SELECT event_kind FROM security_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row["event_kind"] == "url_prefix_rotated"

    def test_returns_new_prefix(self, client, admin_credentials) -> None:
        _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
        response = client.post(
            "/api/admin/url-prefix/rotate",
            json={"current_admin_password": admin_credentials["password"]},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["new_prefix"]
        assert payload["session_revoked"] is True
