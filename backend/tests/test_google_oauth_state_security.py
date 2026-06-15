from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from backend.app.db import ACCOUNT_SHORT_TOKEN_HMAC_MIGRATION_NAME, get_connection, init_db, utcnow_iso
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


def test_google_oauth_state_is_hmac_stored_and_callback_uses_hmac_lookup(
    initialized_settings,
    monkeypatch,
) -> None:
    _configure_google_drive(initialized_settings)

    response = cloud_provider_auth_service.build_google_drive_connect_response(
        initialized_settings,
        user_id=1,
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
    assert result["return_path"] == "/settings?tab=cloud"
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
