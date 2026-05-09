from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.config import refresh_settings
from backend.app.db import get_connection, init_db, utcnow_iso
from backend.app.security import hash_password
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
    def test_root_path_404(self, client) -> None:
        assert client.get("/").status_code == 404

    def test_unknown_prefix_404(self, client) -> None:
        assert client.get("/wrongprefix/library").status_code == 404

    def test_correct_prefix_serves_spa(self, client) -> None:
        prefix = client.app.state.url_prefix
        response = client.get(f"/{prefix}/library")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_api_path_unaffected(self, client) -> None:
        assert client.get("/api/auth/me").status_code in {200, 401}


class TestSpaServing:
    def test_html_route_falls_back_to_index(self, client) -> None:
        prefix = client.app.state.url_prefix
        response = client.get(f"/{prefix}/library")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert f'<base href="/{prefix}/">' in response.text

    def test_nested_html_route_uses_prefix_base_href(self, client) -> None:
        prefix = client.app.state.url_prefix
        response = client.get(f"/{prefix}/setup/totp")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert f'<base href="/{prefix}/">' in response.text

    def test_missing_asset_returns_404_not_index(self, client) -> None:
        prefix = client.app.state.url_prefix
        response = client.get(f"/{prefix}/assets/nonexistent.js")
        assert response.status_code == 404
        assert b"<html" not in response.content.lower()

    def test_nested_missing_asset_returns_404_not_index(self, client) -> None:
        prefix = client.app.state.url_prefix
        response = client.get(f"/{prefix}/setup/assets/nonexistent.js")
        assert response.status_code == 404
        assert b"<html" not in response.content.lower()

    def test_existing_asset_returns_correct_mime(self, client) -> None:
        from backend.app.spa_static import FRONTEND_DIST

        asset_path = Path(FRONTEND_DIST) / "assets" / "test.js"
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_text("export const elvernSpaAssetMimeCheck = true;\n", encoding="utf-8")
        try:
            prefix = client.app.state.url_prefix
            response = client.get(f"/{prefix}/assets/test.js")
        finally:
            asset_path.unlink(missing_ok=True)
        assert response.status_code == 200
        assert response.headers["content-type"].split(";")[0] in {
            "text/javascript",
            "application/javascript",
        }
        assert b"elvernSpaAssetMimeCheck" in response.content

    def test_root_prefix_serves_index_html(self, client) -> None:
        prefix = client.app.state.url_prefix
        response = client.get(f"/{prefix}/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert b"<html" in response.content.lower()
        assert f'<base href="/{prefix}/">' in response.text


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
