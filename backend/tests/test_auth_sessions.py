from __future__ import annotations

from backend.app.auth import (
    authenticate_user,
    create_session,
    destroy_session,
    get_session_access_failure_reason,
    get_user_by_session_token,
)
from backend.app.db import get_connection
from backend.app.security import hash_session_token


def _admin_user(settings):
    user, failure_reason = authenticate_user(
        settings,
        settings.admin_username,
        settings.admin_bootstrap_password or "",
    )
    assert failure_reason is None
    assert user is not None
    return user


def test_create_session_stores_only_the_hashed_token(initialized_settings) -> None:
    user = _admin_user(initialized_settings)

    token = create_session(
        initialized_settings,
        user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT session_token_hash, revoked_at
            FROM sessions
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row is not None
    assert row["session_token_hash"] == hash_session_token(token, initialized_settings.session_secret)
    assert row["session_token_hash"] != token
    assert row["revoked_at"] is None

    resolved_user = get_user_by_session_token(initialized_settings, token)
    assert resolved_user is not None
    assert resolved_user.username == initialized_settings.admin_username
    assert resolved_user.session_id is not None


def test_destroy_session_revokes_access_without_storing_raw_token(initialized_settings) -> None:
    user = _admin_user(initialized_settings)
    token = create_session(
        initialized_settings,
        user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    destroy_session(initialized_settings, token)

    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT session_token_hash, revoked_at, revoked_reason
            FROM sessions
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row is not None
    assert row["session_token_hash"] == hash_session_token(token, initialized_settings.session_secret)
    assert row["session_token_hash"] != token
    assert row["revoked_at"] is not None
    assert row["revoked_reason"] == "logout"
    assert get_user_by_session_token(initialized_settings, token) is None
    assert get_session_access_failure_reason(initialized_settings, token) == "revoked"
