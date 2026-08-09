from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from backend.app.auth import create_session, get_user_by_session_token
from backend.app.db import ACCOUNT_SHORT_TOKEN_HMAC_MIGRATION_NAME, get_connection, init_db, utcnow_iso
from backend.app.models import AuthenticatedUser
from backend.app.security import TOKEN_HASH_PREFIX
from backend.app.services import cloud_provider_auth_service
from backend.app.services.app_settings_service import update_google_drive_setup


def _configure_google_drive(settings) -> None:
    update_google_drive_setup(
        settings,
        user_id=1,
        https_origin="https://example.com",
        client_id="example.apps.googleusercontent.com",
        client_secret="secret123",
    )


def _state_from_authorization_url(authorization_url: str) -> str:
    parsed = urlsplit(authorization_url)
    return parse_qs(parsed.query)["state"][0]


def _oauth_session_id(settings, *, user_id: int = 1) -> int:
    token = create_session(
        settings,
        AuthenticatedUser(id=user_id, username=f"user-{user_id}", role="admin"),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    session_user = get_user_by_session_token(settings, token)
    assert session_user is not None
    assert session_user.session_id is not None
    return session_user.session_id


def test_google_oauth_state_is_hmac_stored_and_callback_uses_hmac_lookup(
    initialized_settings,
    monkeypatch,
) -> None:
    _configure_google_drive(initialized_settings)

    response = cloud_provider_auth_service.build_google_drive_connect_response(
        initialized_settings,
        user_id=1,
        auth_session_id=_oauth_session_id(initialized_settings),
        operation_id="oauth-operation-0000000000000001",
        return_path="/settings?tab=cloud",
    )
    state_payload = _state_from_authorization_url(response["authorization_url"])
    state_context = cloud_provider_auth_service.resolve_google_connect_state(state_payload)
    raw_state_token = str(state_context["state_token"])

    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            "SELECT state_token, user_id FROM google_oauth_states",
        ).fetchone()
    assert row is not None
    assert row["user_id"] == 1
    assert row["state_token"].startswith(TOKEN_HASH_PREFIX)
    assert row["state_token"] != raw_state_token
    assert raw_state_token not in row["state_token"]
    stored_state_token = row["state_token"]

    monkeypatch.setattr(
        cloud_provider_auth_service,
        "exchange_google_oauth_code",
        lambda *args, **kwargs: {
            "access_token": "google-access-token",
            "refresh_token": "google-refresh-token",
            "expires_in": 3600,
        },
    )
    monkeypatch.setattr(
        cloud_provider_auth_service,
        "fetch_google_userinfo",
        lambda _access_token: {
            "sub": "google-account-1",
            "email": "admin@example.com",
            "name": "Admin",
        },
    )

    result = cloud_provider_auth_service.complete_google_drive_connect(
        initialized_settings,
        state_token=state_payload,
        code="oauth-code",
    )

    assert result["user_id"] == 1
    assert result["return_path"] == "/settings/cloud-sharing"
    with get_connection(initialized_settings) as connection:
        assert (
            connection.execute(
                "SELECT state_token FROM google_oauth_states WHERE state_token = ?",
                (stored_state_token,),
            ).fetchone()
            is None
        )
        account_row = connection.execute(
            "SELECT refresh_token, access_token FROM google_drive_accounts WHERE user_id = 1",
        ).fetchone()
    assert account_row is not None
    assert account_row["refresh_token"] != "google-refresh-token"
    assert account_row["access_token"] != "google-access-token"


def test_account_short_token_hmac_migration_deletes_legacy_plaintext_google_oauth_state(
    initialized_settings,
) -> None:
    now = utcnow_iso()
    future = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    with get_connection(initialized_settings) as connection:
        connection.execute(
            "DELETE FROM schema_migrations WHERE name = ?",
            (ACCOUNT_SHORT_TOKEN_HMAC_MIGRATION_NAME,),
        )
        connection.execute(
            "INSERT INTO google_oauth_states (state_token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            ("legacy-plaintext-state-token", 1, now, future),
        )
        connection.commit()

    init_db(initialized_settings)
    init_db(initialized_settings)

    with get_connection(initialized_settings) as connection:
        state_count = connection.execute("SELECT COUNT(*) FROM google_oauth_states").fetchone()[0]
        marker_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE name = ?",
            (ACCOUNT_SHORT_TOKEN_HMAC_MIGRATION_NAME,),
        ).fetchone()[0]
    assert state_count == 0
    assert marker_count == 1


def test_google_first_connect_does_not_claim_identity_unknown_orphaned_sources(
    initialized_settings,
    monkeypatch,
) -> None:
    _configure_google_drive(initialized_settings)
    now = utcnow_iso()
    with get_connection(initialized_settings) as connection:
        other_user_id = int(connection.execute(
            """
            INSERT INTO users (username, password_hash, role, enabled, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            ("other-user", "test-hash", "standard_user", now, now),
        ).lastrowid)
        source_rows = [
            (1, "google_drive", "folder", "owner-google", "Owner Google", "stale owner error"),
            (other_user_id, "google_drive", "folder", "other-google", "Other Google", "stale other error"),
            (1, "local", "folder", "owner-local", "Owner Local", "stale local error"),
        ]
        connection.executemany(
            """
            INSERT INTO library_sources (
                owner_user_id, provider, google_drive_account_id, resource_type,
                resource_id, display_name, is_shared, created_at, updated_at, last_error
            ) VALUES (?, ?, NULL, ?, ?, ?, 0, ?, ?, ?)
            """,
            [(*row[:5], now, now, row[5]) for row in source_rows],
        )
        connection.commit()

    response = cloud_provider_auth_service.build_google_drive_connect_response(
        initialized_settings,
        user_id=1,
        auth_session_id=_oauth_session_id(initialized_settings),
        operation_id="oauth-operation-0000000000000002",
    )
    state_payload = _state_from_authorization_url(response["authorization_url"])
    monkeypatch.setattr(
        cloud_provider_auth_service,
        "exchange_google_oauth_code",
        lambda *args, **kwargs: {
            "access_token": "reconnected-access-token",
            "refresh_token": "reconnected-refresh-token",
            "expires_in": 3600,
        },
    )
    monkeypatch.setattr(
        cloud_provider_auth_service,
        "fetch_google_userinfo",
        lambda _access_token: {
            "sub": "reconnected-google-account",
            "email": "admin@example.com",
            "name": "Admin",
        },
    )

    cloud_provider_auth_service.complete_google_drive_connect(
        initialized_settings,
        state_token=state_payload,
        code="oauth-code",
    )

    with get_connection(initialized_settings) as connection:
        account_id = int(connection.execute(
            "SELECT id FROM google_drive_accounts WHERE user_id = 1",
        ).fetchone()["id"])
        sources = {
            row["resource_id"]: dict(row)
            for row in connection.execute(
                """
                SELECT resource_id, google_drive_account_id, last_error
                FROM library_sources
                WHERE resource_id IN ('owner-google', 'other-google', 'owner-local')
                """
            )
        }

    assert sources["owner-google"]["google_drive_account_id"] is None
    assert sources["owner-google"]["last_error"] == "stale owner error"
    assert sources["other-google"]["google_drive_account_id"] is None
    assert sources["other-google"]["last_error"] == "stale other error"
    assert sources["owner-local"]["google_drive_account_id"] is None
    assert sources["owner-local"]["last_error"] == "stale local error"
    assert cloud_provider_auth_service.get_google_drive_account_access_token_by_account_id(
        initialized_settings,
        google_account_id=account_id,
    ) == "reconnected-access-token"
