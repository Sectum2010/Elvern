from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from backend.app.auth import create_session, destroy_session, get_user_by_session_token
from backend.app.db import get_connection, normalize_google_provider_identity_rows, utcnow_iso
from backend.app.models import AuthenticatedUser
from backend.app.services import cloud_provider_auth_service
from backend.app.services.app_settings_service import update_google_drive_setup
from backend.app.services.at_rest_encryption import encrypt_at_rest


def _configure(settings) -> None:
    update_google_drive_setup(
        settings,
        user_id=1,
        https_origin="https://example.com",
        client_id="example.apps.googleusercontent.com",
        client_secret="secret123",
    )


def _session(settings) -> tuple[int, str]:
    token = create_session(
        settings,
        AuthenticatedUser(id=1, username="admin", role="admin"),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    user = get_user_by_session_token(settings, token)
    assert user is not None and user.session_id is not None
    return int(user.session_id), token


def _begin(settings, *, session_id: int, operation_id: str) -> dict[str, str]:
    return cloud_provider_auth_service.build_google_drive_connect_response(
        settings,
        user_id=1,
        auth_session_id=session_id,
        operation_id=operation_id,
    )


def _insert_legacy_account_and_source(settings) -> None:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        account_id = int(connection.execute(
            """
            INSERT INTO google_drive_accounts (
                user_id, google_account_id, email, display_name, refresh_token,
                access_token, access_token_expires_at, created_at, updated_at
            ) VALUES (1, 'old-subject', 'old@example.test', 'Old account', ?, ?, ?, ?, ?)
            """,
            (
                encrypt_at_rest("old-refresh", settings),
                encrypt_at_rest("old-access", settings),
                (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                now,
                now,
            ),
        ).lastrowid)
        connection.execute(
            """
            INSERT INTO library_sources (
                owner_user_id, provider, google_drive_account_id, resource_type,
                resource_id, display_name, is_shared, created_at, updated_at
            ) VALUES (1, 'google_drive', ?, 'folder', 'source-1', 'Original source', 0, ?, ?)
            """,
            (account_id, now, now),
        )
        connection.commit()


def _create_mismatch(settings, monkeypatch, *, session_id: int, operation_id: str) -> None:
    response = _begin(settings, session_id=session_id, operation_id=operation_id)
    from urllib.parse import parse_qs, urlsplit

    state = parse_qs(urlsplit(response["authorization_url"]).query)["state"][0]
    monkeypatch.setattr(
        cloud_provider_auth_service,
        "exchange_google_oauth_code",
        lambda *_args, **_kwargs: {
            "access_token": "candidate-access",
            "refresh_token": "candidate-refresh",
            "expires_in": 3600,
        },
    )
    monkeypatch.setattr(
        cloud_provider_auth_service,
        "fetch_google_userinfo",
        lambda _access_token: {
            "sub": "new-subject",
            "email": "new@example.test",
            "name": "New account",
        },
    )
    result = cloud_provider_auth_service.complete_google_drive_connect(
        settings,
        state_token=state,
        code="oauth-code",
    )
    assert result["status"] == "account_mismatch"


def test_operation_id_is_hmac_stored_session_bound_and_single_use(initialized_settings) -> None:
    _configure(initialized_settings)
    session_id, _token = _session(initialized_settings)
    other_session_id, _other_token = _session(initialized_settings)
    operation_id = "single-use-operation-00000001"

    _begin(initialized_settings, session_id=session_id, operation_id=operation_id)
    pending = cloud_provider_auth_service.get_google_oauth_operation_payload(
        initialized_settings,
        user_id=1,
        auth_session_id=session_id,
        operation_id=operation_id,
    )
    assert pending["status"] == "pending"
    with get_connection(initialized_settings) as connection:
        stored = connection.execute(
            "SELECT operation_id_hash FROM google_oauth_operations WHERE auth_session_id = ?",
            (session_id,),
        ).fetchone()["operation_id_hash"]
    assert stored != operation_id
    assert operation_id not in str(stored)

    with pytest.raises(HTTPException) as other_session:
        cloud_provider_auth_service.get_google_oauth_operation_payload(
            initialized_settings,
            user_id=1,
            auth_session_id=other_session_id,
            operation_id=operation_id,
        )
    assert other_session.value.status_code == 409

    cancelled = cloud_provider_auth_service.cancel_google_oauth_operation(
        initialized_settings,
        user_id=1,
        auth_session_id=session_id,
        operation_id=operation_id,
    )
    assert cancelled["status"] == "cancelled"
    with pytest.raises(HTTPException) as replay:
        _begin(initialized_settings, session_id=session_id, operation_id=operation_id)
    assert replay.value.status_code == 409


def test_operation_expires_and_is_removed_when_session_is_revoked(initialized_settings) -> None:
    _configure(initialized_settings)
    session_id, token = _session(initialized_settings)
    operation_id = "expiring-operation-0000000001"
    _begin(initialized_settings, session_id=session_id, operation_id=operation_id)
    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE google_oauth_operations SET expires_at = ? WHERE auth_session_id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), session_id),
        )
        connection.commit()
    expired = cloud_provider_auth_service.get_google_oauth_operation_payload(
        initialized_settings,
        user_id=1,
        auth_session_id=session_id,
        operation_id=operation_id,
    )
    assert expired["status"] == "expired"

    destroy_session(initialized_settings, token)
    with get_connection(initialized_settings) as connection:
        assert connection.execute(
            "SELECT 1 FROM google_oauth_operations WHERE auth_session_id = ?",
            (session_id,),
        ).fetchone() is None


def test_account_replacement_rolls_back_every_database_change_on_mid_transaction_failure(
    initialized_settings,
    monkeypatch,
) -> None:
    _configure(initialized_settings)
    _insert_legacy_account_and_source(initialized_settings)
    session_id, _token = _session(initialized_settings)
    operation_id = "rollback-operation-0000000001"
    _create_mismatch(
        initialized_settings,
        monkeypatch,
        session_id=session_id,
        operation_id=operation_id,
    )
    monkeypatch.setattr(
        cloud_provider_auth_service,
        "fetch_drive_resource_metadata",
        lambda *_args, **_kwargs: {
            "resource_type": "folder",
            "resource_id": "source-1",
            "display_name": "Verified replacement",
        },
    )

    def fail_mid_transaction(connection, **_kwargs):
        connection.execute(
            "UPDATE library_sources SET display_name = 'MUST ROLL BACK' WHERE resource_id = 'source-1'"
        )
        raise RuntimeError("injected final transaction failure")

    monkeypatch.setattr(
        cloud_provider_auth_service,
        "_apply_google_source_validation_results_in_connection",
        fail_mid_transaction,
    )
    with pytest.raises(RuntimeError, match="injected final transaction failure"):
        cloud_provider_auth_service.confirm_google_account_candidate(
            initialized_settings,
            user_id=1,
            auth_session_id=session_id,
            operation_id=operation_id,
        )

    with get_connection(initialized_settings) as connection:
        account = connection.execute(
            """
            SELECT identity.subject_hash
            FROM google_drive_accounts account
            JOIN provider_identities identity ON identity.id = account.provider_identity_id
            WHERE account.user_id = 1
            """
        ).fetchone()
        source = connection.execute(
            "SELECT display_name FROM library_sources WHERE resource_id = 'source-1'"
        ).fetchone()
        candidate = connection.execute(
            "SELECT consumed_at, cancelled_at FROM google_oauth_account_candidates"
        ).fetchone()
        operation = connection.execute(
            "SELECT status FROM google_oauth_operations WHERE auth_session_id = ?",
            (session_id,),
        ).fetchone()
    assert account["subject_hash"] == cloud_provider_auth_service._hash_google_account_subject(
        initialized_settings,
        "old-subject",
    )
    assert source["display_name"] == "Original source"
    assert candidate["consumed_at"] is None
    assert candidate["cancelled_at"] is None
    assert operation["status"] == "account_mismatch"


def test_legacy_google_identity_normalization_is_idempotent_and_removes_plaintext(
    initialized_settings,
) -> None:
    _insert_legacy_account_and_source(initialized_settings)

    with get_connection(initialized_settings) as connection:
        normalize_google_provider_identity_rows(connection, settings=initialized_settings)
        normalize_google_provider_identity_rows(connection, settings=initialized_settings)
        connection.commit()
        account = connection.execute(
            """
            SELECT provider_identity_id, google_account_id, email, display_name
            FROM google_drive_accounts
            WHERE user_id = 1
            """
        ).fetchone()
        source = connection.execute(
            """
            SELECT provider_identity_id, expected_google_account_subject_hash,
                   expected_google_account_email, expected_google_account_name
            FROM library_sources
            WHERE resource_id = 'source-1'
            """
        ).fetchone()
        identities = connection.execute(
            "SELECT subject_hash, display_label_encrypted FROM provider_identities"
        ).fetchall()

    assert account["google_account_id"] == ""
    assert account["email"] is None
    assert account["display_name"] is None
    assert source["expected_google_account_subject_hash"] is None
    assert source["expected_google_account_email"] is None
    assert source["expected_google_account_name"] is None
    assert account["provider_identity_id"] == source["provider_identity_id"]
    assert len(identities) == 1
    assert identities[0]["subject_hash"] == cloud_provider_auth_service._hash_google_account_subject(
        initialized_settings,
        "old-subject",
    )
    assert "old-subject" not in str(identities[0]["subject_hash"])
    assert "Old account" not in str(identities[0]["display_label_encrypted"])
