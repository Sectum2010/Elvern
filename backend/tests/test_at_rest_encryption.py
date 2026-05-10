from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi import HTTPException

from backend.app.db import get_connection, utcnow_iso
from backend.app.services import cloud_provider_auth_service
from backend.app.services.at_rest_encryption import (
    CIPHERTEXT_PREFIX,
    _derive_fernet_key,
    decrypt_at_rest,
    encrypt_at_rest,
)


class TestEncryption:
    def test_round_trip_returns_plaintext(self, initialized_settings) -> None:
        stored = encrypt_at_rest("refresh-token", initialized_settings)

        plaintext, was_encrypted = decrypt_at_rest(stored, initialized_settings)

        assert plaintext == "refresh-token"
        assert was_encrypted is True

    def test_distinct_calls_produce_distinct_ciphertext(self, initialized_settings) -> None:
        first = encrypt_at_rest("refresh-token", initialized_settings)
        second = encrypt_at_rest("refresh-token", initialized_settings)

        assert first != second
        assert first.startswith(CIPHERTEXT_PREFIX)
        assert second.startswith(CIPHERTEXT_PREFIX)

    def test_decrypt_with_different_secret_fails(self, initialized_settings) -> None:
        settings_b = replace(initialized_settings, session_secret="different-test-secret-value-32-chars")
        stored = encrypt_at_rest("refresh-token", initialized_settings)

        with pytest.raises(ValueError):
            decrypt_at_rest(stored, settings_b)

    def test_empty_string_round_trip(self, initialized_settings) -> None:
        assert encrypt_at_rest("", initialized_settings) == ""
        assert decrypt_at_rest("", initialized_settings) == ("", False)

    def test_legacy_plaintext_passthrough(self, initialized_settings) -> None:
        assert decrypt_at_rest("not_encrypted_at_all", initialized_settings) == (
            "not_encrypted_at_all",
            False,
        )

    def test_invalid_ciphertext_raises(self, initialized_settings) -> None:
        with pytest.raises(ValueError):
            decrypt_at_rest("fernet1$garbage", initialized_settings)

    def test_unicode_round_trip(self, initialized_settings) -> None:
        value = "令牌-🔒-token"
        stored = encrypt_at_rest(value, initialized_settings)

        plaintext, was_encrypted = decrypt_at_rest(stored, initialized_settings)

        assert plaintext == value
        assert was_encrypted is True


class TestKeyDerivation:
    def test_same_settings_produces_same_key(self, initialized_settings) -> None:
        assert _derive_fernet_key(initialized_settings) == _derive_fernet_key(initialized_settings)

    def test_different_session_secret_different_key(self, initialized_settings) -> None:
        settings_b = replace(initialized_settings, session_secret="different-test-secret-value-32-chars")

        assert _derive_fernet_key(initialized_settings) != _derive_fernet_key(settings_b)

    def test_session_secret_with_special_chars(self, initialized_settings) -> None:
        settings_b = replace(initialized_settings, session_secret="secret-!@#$%^&*()-_+=-with-symbols")
        stored = encrypt_at_rest("refresh-token", settings_b)

        assert decrypt_at_rest(stored, settings_b) == ("refresh-token", True)


class TestOauthTokenLazyMigration:
    def _insert_account(self, settings, *, refresh_token: str, access_token: str = "") -> int:
        now = utcnow_iso()
        with get_connection(settings) as connection:
            cursor = connection.execute(
                """
                INSERT INTO google_drive_accounts (
                    user_id,
                    google_account_id,
                    email,
                    display_name,
                    refresh_token,
                    access_token,
                    access_token_expires_at,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    "google-account-1",
                    "admin@example.com",
                    "Admin",
                    refresh_token,
                    access_token,
                    "2000-01-01T00:00:00+00:00",
                    now,
                    now,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def test_legacy_plaintext_token_works_and_gets_upgraded(self, initialized_settings, monkeypatch) -> None:
        account_id = self._insert_account(initialized_settings, refresh_token="legacy-refresh-token")
        seen_refresh_tokens: list[str] = []

        def _fake_refresh(settings, *, refresh_token):
            del settings
            seen_refresh_tokens.append(refresh_token)
            return {"access_token": "new-access-token", "expires_in": 3600}

        monkeypatch.setattr(cloud_provider_auth_service, "refresh_google_access_token", _fake_refresh)

        token = cloud_provider_auth_service.get_google_drive_account_access_token_by_account_id(
            initialized_settings,
            google_account_id=account_id,
        )

        assert token == "new-access-token"
        assert seen_refresh_tokens == ["legacy-refresh-token"]
        with get_connection(initialized_settings) as connection:
            row = connection.execute(
                "SELECT refresh_token, access_token FROM google_drive_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            event = connection.execute(
                """
                SELECT event_kind
                FROM security_events
                WHERE event_kind = 'oauth_token_encryption_upgraded'
                LIMIT 1
                """
            ).fetchone()
        assert str(row["refresh_token"]).startswith(CIPHERTEXT_PREFIX)
        assert str(row["access_token"]).startswith(CIPHERTEXT_PREFIX)
        assert decrypt_at_rest(str(row["refresh_token"]), initialized_settings) == ("legacy-refresh-token", True)
        assert decrypt_at_rest(str(row["access_token"]), initialized_settings) == ("new-access-token", True)
        assert event is not None

    def test_already_encrypted_token_not_double_encrypted(self, initialized_settings, monkeypatch) -> None:
        encrypted_refresh = encrypt_at_rest("refresh-token", initialized_settings)
        account_id = self._insert_account(initialized_settings, refresh_token=encrypted_refresh)

        def _fake_refresh(settings, *, refresh_token):
            del settings
            assert refresh_token == "refresh-token"
            return {"access_token": "new-access-token", "expires_in": 3600}

        monkeypatch.setattr(cloud_provider_auth_service, "refresh_google_access_token", _fake_refresh)

        cloud_provider_auth_service.get_google_drive_account_access_token_by_account_id(
            initialized_settings,
            google_account_id=account_id,
        )

        with get_connection(initialized_settings) as connection:
            row = connection.execute(
                "SELECT refresh_token FROM google_drive_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            event_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM security_events
                WHERE event_kind = 'oauth_token_encryption_upgraded'
                """
            ).fetchone()[0]
        assert decrypt_at_rest(str(row["refresh_token"]), initialized_settings) == ("refresh-token", True)
        assert event_count == 0

    def test_corrupted_token_returns_empty(self, initialized_settings) -> None:
        plain, was_encrypted, corrupted = cloud_provider_auth_service._decrypt_stored_oauth_value(
            "fernet1$garbage",
            initialized_settings,
        )

        assert plain == ""
        assert was_encrypted is False
        assert corrupted is True

    def test_corrupted_token_forces_reauth(self, initialized_settings) -> None:
        account_id = self._insert_account(initialized_settings, refresh_token="fernet1$garbage")

        with pytest.raises(HTTPException) as exc_info:
            cloud_provider_auth_service.get_google_drive_account_access_token_by_account_id(
                initialized_settings,
                google_account_id=account_id,
            )

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "provider_auth_required"
