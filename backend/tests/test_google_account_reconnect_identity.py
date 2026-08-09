from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException

from backend.app.auth import create_session, destroy_session, get_user_by_session_token
from backend.app.db import get_connection, utcnow_iso
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


def _session(settings, *, user_id: int = 1) -> tuple[int, str]:
    token = create_session(
        settings,
        AuthenticatedUser(id=user_id, username=f"user-{user_id}", role="admin"),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    user = get_user_by_session_token(settings, token)
    assert user is not None and user.session_id is not None
    return user.session_id, token


def _state(authorization_url: str) -> str:
    return parse_qs(urlsplit(authorization_url).query)["state"][0]


def _insert_account_and_sources(settings, *, source_count: int = 1) -> int:
    now = utcnow_iso()
    old_subject = "google-account-old"
    old_hash = cloud_provider_auth_service._hash_google_account_subject(settings, old_subject)
    with get_connection(settings) as connection:
        account_id = int(connection.execute(
            """
            INSERT INTO google_drive_accounts (
                user_id, google_account_id, email, display_name, refresh_token,
                access_token, access_token_expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                old_subject,
                "old@example.test",
                "Old account",
                encrypt_at_rest("old-refresh", settings),
                encrypt_at_rest("old-access", settings),
                (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                now,
                now,
            ),
        ).lastrowid)
        for index in range(source_count):
            connection.execute(
                """
                INSERT INTO library_sources (
                    owner_user_id, provider, google_drive_account_id,
                    expected_google_account_subject_hash, expected_google_account_email,
                    expected_google_account_name, resource_type, resource_id,
                    display_name, is_shared, created_at, updated_at, last_error
                ) VALUES (1, 'google_drive', ?, ?, ?, ?, 'folder', ?, ?, 0, ?, ?, ?)
                """,
                (
                    account_id,
                    old_hash,
                    "old@example.test",
                    "Old account",
                    f"source-{index + 1}",
                    f"Source {index + 1}",
                    now,
                    now,
                    "Reconnect required.",
                ),
            )
        connection.commit()
    return account_id


def _begin(settings, *, session_id: int, operation_id: str) -> str:
    response = cloud_provider_auth_service.build_google_drive_connect_response(
        settings,
        user_id=1,
        auth_session_id=session_id,
        operation_id=operation_id,
    )
    return _state(response["authorization_url"])


def _mock_oauth(monkeypatch, *, subject: str, access: str = "new-access") -> None:
    monkeypatch.setattr(
        cloud_provider_auth_service,
        "exchange_google_oauth_code",
        lambda *args, **kwargs: {
            "access_token": access,
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        },
    )
    monkeypatch.setattr(
        cloud_provider_auth_service,
        "fetch_google_userinfo",
        lambda _access_token: {
            "sub": subject,
            "email": "new@example.test",
            "name": "New account",
        },
    )


def test_same_account_reconnect_rebinds_only_matching_sources_after_validation(
    initialized_settings,
    monkeypatch,
) -> None:
    _configure(initialized_settings)
    account_id = _insert_account_and_sources(initialized_settings)
    session_id, _ = _session(initialized_settings)
    operation_id = "same-account-operation-00000001"
    state = _begin(initialized_settings, session_id=session_id, operation_id=operation_id)
    _mock_oauth(monkeypatch, subject="google-account-old")
    monkeypatch.setattr(
        cloud_provider_auth_service,
        "fetch_drive_resource_metadata",
        lambda *args, **kwargs: {
            "resource_type": "folder",
            "resource_id": kwargs["resource_id"],
            "display_name": "Verified source",
        },
    )

    result = cloud_provider_auth_service.complete_google_drive_connect(
        initialized_settings,
        state_token=state,
        code="oauth-code",
    )

    assert result["status"] == "connected"
    with get_connection(initialized_settings) as connection:
        source = connection.execute(
            "SELECT google_drive_account_id, last_error, display_name FROM library_sources WHERE resource_id = 'source-1'",
        ).fetchone()
    assert int(source["google_drive_account_id"]) == account_id
    assert source["last_error"] is None
    assert source["display_name"] == "Verified source"


def test_different_account_creates_encrypted_session_bound_candidate_without_rebinding(
    initialized_settings,
    monkeypatch,
) -> None:
    _configure(initialized_settings)
    old_account_id = _insert_account_and_sources(initialized_settings)
    session_id, _ = _session(initialized_settings)
    operation_id = "different-account-operation-0001"
    state = _begin(initialized_settings, session_id=session_id, operation_id=operation_id)
    _mock_oauth(monkeypatch, subject="google-account-new")

    result = cloud_provider_auth_service.complete_google_drive_connect(
        initialized_settings,
        state_token=state,
        code="oauth-code",
    )

    assert result["status"] == "account_mismatch"
    candidate = cloud_provider_auth_service.get_google_account_candidate_payload(
        initialized_settings,
        user_id=1,
        auth_session_id=session_id,
        operation_id=operation_id,
    )
    assert candidate["current_account_label"] == "Old account"
    assert candidate["candidate_account_label"] == "New account"
    assert "google-account" not in repr(candidate)
    with get_connection(initialized_settings) as connection:
        account = connection.execute(
            "SELECT id, google_account_id FROM google_drive_accounts WHERE user_id = 1",
        ).fetchone()
        source = connection.execute(
            "SELECT google_drive_account_id, last_error FROM library_sources WHERE resource_id = 'source-1'",
        ).fetchone()
        stored_candidate = connection.execute(
            "SELECT access_token, refresh_token FROM google_oauth_account_candidates",
        ).fetchone()
    assert int(account["id"]) == old_account_id
    assert account["google_account_id"] == "google-account-old"
    assert int(source["google_drive_account_id"]) == old_account_id
    assert source["last_error"] == "Reconnect required."
    assert stored_candidate["access_token"] != "new-access"
    assert stored_candidate["refresh_token"] != "new-refresh"


def test_different_account_cancel_is_one_time_and_preserves_current_account(
    initialized_settings,
    monkeypatch,
) -> None:
    _configure(initialized_settings)
    old_account_id = _insert_account_and_sources(initialized_settings)
    session_id, _ = _session(initialized_settings)
    operation_id = "cancel-account-operation-00000001"
    state = _begin(initialized_settings, session_id=session_id, operation_id=operation_id)
    _mock_oauth(monkeypatch, subject="google-account-new")
    cloud_provider_auth_service.complete_google_drive_connect(
        initialized_settings,
        state_token=state,
        code="oauth-code",
    )

    cloud_provider_auth_service.cancel_google_account_candidate(
        initialized_settings,
        user_id=1,
        auth_session_id=session_id,
        operation_id=operation_id,
    )

    with pytest.raises(HTTPException) as replay:
        cloud_provider_auth_service.cancel_google_account_candidate(
            initialized_settings,
            user_id=1,
            auth_session_id=session_id,
            operation_id=operation_id,
        )
    assert replay.value.status_code == 409
    with get_connection(initialized_settings) as connection:
        account = connection.execute("SELECT id FROM google_drive_accounts WHERE user_id = 1").fetchone()
    assert int(account["id"]) == old_account_id


def test_different_account_replace_migrates_only_sources_with_verified_access(
    initialized_settings,
    monkeypatch,
) -> None:
    _configure(initialized_settings)
    _insert_account_and_sources(initialized_settings, source_count=2)
    session_id, _ = _session(initialized_settings)
    operation_id = "replace-account-operation-0000001"
    state = _begin(initialized_settings, session_id=session_id, operation_id=operation_id)
    _mock_oauth(monkeypatch, subject="google-account-new")
    cloud_provider_auth_service.complete_google_drive_connect(
        initialized_settings,
        state_token=state,
        code="oauth-code",
    )

    def metadata(_token, *, resource_type, resource_id):
        if resource_id == "source-2":
            raise HTTPException(status_code=403, detail="provider-private-detail")
        return {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "display_name": "Verified source 1",
        }

    monkeypatch.setattr(cloud_provider_auth_service, "fetch_drive_resource_metadata", metadata)
    result = cloud_provider_auth_service.confirm_google_account_candidate(
        initialized_settings,
        user_id=1,
        auth_session_id=session_id,
        operation_id=operation_id,
    )

    assert result["migrated_source_count"] == 1
    assert result["failed_source_count"] == 1
    with get_connection(initialized_settings) as connection:
        account = connection.execute(
            "SELECT id, google_account_id FROM google_drive_accounts WHERE user_id = 1",
        ).fetchone()
        sources = {
            row["resource_id"]: dict(row)
            for row in connection.execute(
                """
                SELECT resource_id, google_drive_account_id, last_error,
                       expected_google_account_subject_hash
                FROM library_sources
                ORDER BY resource_id
                """
            )
        }
    assert account["google_account_id"] == "google-account-new"
    assert int(sources["source-1"]["google_drive_account_id"]) == int(account["id"])
    assert sources["source-1"]["last_error"] is None
    assert sources["source-2"]["google_drive_account_id"] is None
    assert sources["source-2"]["last_error"] == "Google Drive access could not be verified for this source."
    with pytest.raises(HTTPException) as replay:
        cloud_provider_auth_service.confirm_google_account_candidate(
            initialized_settings,
            user_id=1,
            auth_session_id=session_id,
            operation_id=operation_id,
        )
    assert replay.value.status_code == 409


def test_candidate_is_rejected_for_another_session_and_removed_on_logout(
    initialized_settings,
    monkeypatch,
) -> None:
    _configure(initialized_settings)
    _insert_account_and_sources(initialized_settings)
    session_id, token = _session(initialized_settings)
    other_session_id, _ = _session(initialized_settings)
    operation_id = "session-bound-operation-00000001"
    state = _begin(initialized_settings, session_id=session_id, operation_id=operation_id)
    _mock_oauth(monkeypatch, subject="google-account-new")
    cloud_provider_auth_service.complete_google_drive_connect(
        initialized_settings,
        state_token=state,
        code="oauth-code",
    )

    with pytest.raises(HTTPException) as mismatch:
        cloud_provider_auth_service.get_google_account_candidate_payload(
            initialized_settings,
            user_id=1,
            auth_session_id=other_session_id,
            operation_id=operation_id,
        )
    assert mismatch.value.status_code == 409
    destroy_session(initialized_settings, token)
    with get_connection(initialized_settings) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM google_oauth_account_candidates WHERE auth_session_id = ?",
            (session_id,),
        ).fetchone()[0]
    assert count == 0


def test_expired_candidate_cannot_be_read_or_confirmed(
    initialized_settings,
    monkeypatch,
) -> None:
    _configure(initialized_settings)
    _insert_account_and_sources(initialized_settings)
    session_id, _ = _session(initialized_settings)
    operation_id = "expired-account-operation-000001"
    state = _begin(initialized_settings, session_id=session_id, operation_id=operation_id)
    _mock_oauth(monkeypatch, subject="google-account-new")
    cloud_provider_auth_service.complete_google_drive_connect(
        initialized_settings,
        state_token=state,
        code="oauth-code",
    )
    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE google_oauth_account_candidates SET expires_at = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),),
        )
        connection.commit()

    with pytest.raises(HTTPException) as read_error:
        cloud_provider_auth_service.get_google_account_candidate_payload(
            initialized_settings,
            user_id=1,
            auth_session_id=session_id,
            operation_id=operation_id,
        )
    assert read_error.value.status_code == 409
    with pytest.raises(HTTPException) as confirm_error:
        cloud_provider_auth_service.confirm_google_account_candidate(
            initialized_settings,
            user_id=1,
            auth_session_id=session_id,
            operation_id=operation_id,
        )
    assert confirm_error.value.status_code == 409
    with get_connection(initialized_settings) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM google_oauth_account_candidates",
        ).fetchone()[0]
    assert count == 0


def test_candidate_tokens_and_subject_do_not_escape_responses_urls_or_logs(
    initialized_settings,
    monkeypatch,
    caplog,
) -> None:
    _configure(initialized_settings)
    _insert_account_and_sources(initialized_settings)
    session_id, _ = _session(initialized_settings)
    operation_id = "nonleak-account-operation-000001"
    subject = "sensitive-google-subject-99117"
    access_token = "sensitive-access-token-99117"
    refresh_token = "sensitive-refresh-token-99117"
    connect_response = cloud_provider_auth_service.build_google_drive_connect_response(
        initialized_settings,
        user_id=1,
        auth_session_id=session_id,
        operation_id=operation_id,
    )
    state = _state(connect_response["authorization_url"])
    monkeypatch.setattr(
        cloud_provider_auth_service,
        "exchange_google_oauth_code",
        lambda *args, **kwargs: {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": 3600,
        },
    )
    monkeypatch.setattr(
        cloud_provider_auth_service,
        "fetch_google_userinfo",
        lambda _access_token: {
            "sub": subject,
            "email": "candidate@example.test",
            "name": "Candidate account",
        },
    )

    completion = cloud_provider_auth_service.complete_google_drive_connect(
        initialized_settings,
        state_token=state,
        code="oauth-code",
    )
    candidate = cloud_provider_auth_service.get_google_account_candidate_payload(
        initialized_settings,
        user_id=1,
        auth_session_id=session_id,
        operation_id=operation_id,
    )
    exposed_text = "\n".join(
        (
            repr(connect_response),
            repr(completion),
            repr(candidate),
            caplog.text,
        )
    )
    assert subject not in exposed_text
    assert access_token not in exposed_text
    assert refresh_token not in exposed_text
