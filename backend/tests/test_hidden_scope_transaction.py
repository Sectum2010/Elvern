from __future__ import annotations

import pytest

from backend.app.db import get_connection, utcnow_iso
from backend.app.services import library_hidden_service
from backend.app.services.library_hidden_service import (
    hide_media_item_for_user,
    set_hidden_media_item_scope,
)


def _login(client, *, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _logout(client) -> None:
    response = client.post("/api/auth/logout")
    assert response.status_code == 200


def _insert_media_item(settings, *, suffix: str) -> int:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        cursor = connection.execute(
            """
            INSERT INTO media_items (
                title, original_filename, file_path, file_size, file_mtime,
                year, created_at, updated_at, last_scanned_at
            ) VALUES (?, ?, ?, 1, 1, 2026, ?, ?, ?)
            """,
            (
                f"Scope Movie {suffix}",
                f"scope-movie-{suffix}-1080p.mkv",
                f"/safe/scope-movie-{suffix}.mkv",
                now,
                now,
                now,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def _scope_truth(settings, *, item_id: int, user_id: int = 1) -> dict[str, object]:
    with get_connection(settings) as connection:
        media_row = connection.execute(
            "SELECT title, year, original_filename FROM media_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        identity = library_hidden_service._movie_identity_payload(
            title=media_row["title"],
            year=media_row["year"],
            original_filename=media_row["original_filename"],
        )
        movie_key = str(identity["movie_key"])
        personal_item = connection.execute(
            "SELECT hidden_at FROM user_hidden_media_items WHERE user_id = ? AND media_item_id = ?",
            (user_id, item_id),
        ).fetchone()
        personal_key = connection.execute(
            "SELECT hidden_at FROM user_hidden_movie_keys WHERE user_id = ? AND movie_key = ?",
            (user_id, movie_key),
        ).fetchone()
        global_item = connection.execute(
            "SELECT hidden_at FROM global_hidden_media_items WHERE media_item_id = ?",
            (item_id,),
        ).fetchone()
        global_key = connection.execute(
            "SELECT hidden_at FROM global_hidden_movie_keys WHERE movie_key = ?",
            (movie_key,),
        ).fetchone()
        audit_count = int(connection.execute(
            "SELECT COUNT(*) AS count FROM audit_logs WHERE action = 'admin.library.set_hidden_scope'"
        ).fetchone()["count"])
        counters = {
            (str(row["scope_kind"]), int(row["scope_id"]), str(row["layer"])): int(row["counter"])
            for row in connection.execute(
                """
                SELECT scope_kind, scope_id, layer, counter
                FROM library_revision_counters
                WHERE (scope_kind = 'global' AND layer = 'permission')
                   OR (scope_kind = 'user' AND scope_id = ? AND layer = 'user_overlay')
                """,
                (user_id,),
            ).fetchall()
        }
    return {
        "movie_key": movie_key,
        "personal_item_at": personal_item["hidden_at"] if personal_item else None,
        "personal_key_at": personal_key["hidden_at"] if personal_key else None,
        "global_item_at": global_item["hidden_at"] if global_item else None,
        "global_key_at": global_key["hidden_at"] if global_key else None,
        "audit_count": audit_count,
        "counters": counters,
    }


def _set_scope(settings, *, item_id: int, target_scope: str) -> dict[str, object]:
    return set_hidden_media_item_scope(
        settings,
        actor_user_id=1,
        actor_username="admin",
        actor_role="admin",
        actor_session_id=1,
        item_id=item_id,
        target_scope=target_scope,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )


def _counter_value(
    truth: dict[str, object],
    *,
    scope_kind: str,
    scope_id: int,
    layer: str,
) -> int:
    counters = truth["counters"]
    assert isinstance(counters, dict)
    return int(counters.get((scope_kind, scope_id, layer), 0))


def test_hidden_scope_endpoint_requires_admin_and_validates_scope(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, **admin_credentials)
    item_id = _insert_media_item(initialized_settings, suffix="auth")
    created = client.post(
        "/api/admin/users",
        json={
            "username": "scope-user",
            "password": "scope-user-password",
            "role": "standard_user",
            "enabled": True,
        },
    )
    assert created.status_code == 200
    _logout(client)
    _login(client, username="scope-user", password="scope-user-password")

    assert client.put(
        f"/api/admin/hidden-items/{item_id}/scope",
        json={"target_scope": "global"},
    ).status_code == 403

    _logout(client)
    _login(client, **admin_credentials)
    invalid = client.put(
        f"/api/admin/hidden-items/{item_id}/scope",
        json={"target_scope": "shared"},
    )
    assert invalid.status_code == 422
    missing = client.put(
        "/api/admin/hidden-items/999999/scope",
        json={"target_scope": "global"},
    )
    assert missing.status_code == 404


@pytest.mark.parametrize(
    ("initial_scope", "target_scope"),
    [
        ("personal", "global"),
        ("global", "personal"),
        ("both", "global"),
        ("both", "personal"),
        ("neither", "global"),
        ("neither", "personal"),
    ],
)
def test_hidden_scope_self_heals_to_one_authoritative_scope(
    initialized_settings,
    initial_scope,
    target_scope,
) -> None:
    item_id = _insert_media_item(initialized_settings, suffix=f"{initial_scope}-{target_scope}")
    if initial_scope in {"personal", "both"}:
        hide_media_item_for_user(initialized_settings, user_id=1, item_id=item_id)
    if initial_scope in {"global", "both"}:
        _set_scope(initialized_settings, item_id=item_id, target_scope="global")
        if initial_scope == "both":
            hide_media_item_for_user(initialized_settings, user_id=1, item_id=item_id)

    result = _set_scope(initialized_settings, item_id=item_id, target_scope=target_scope)
    truth = _scope_truth(initialized_settings, item_id=item_id)

    assert result["target_scope"] == target_scope
    assert result["changed"] is True or initial_scope == target_scope
    if target_scope == "global":
        assert truth["global_item_at"] == truth["global_key_at"] == result["hidden_at"]
        assert truth["personal_item_at"] is None
        assert truth["personal_key_at"] is None
    else:
        assert truth["personal_item_at"] == truth["personal_key_at"] == result["hidden_at"]
        assert truth["global_item_at"] is None
        assert truth["global_key_at"] is None


def test_repeated_scope_put_is_idempotent_without_timestamp_revision_or_audit_churn(
    initialized_settings,
) -> None:
    item_id = _insert_media_item(initialized_settings, suffix="idempotent")
    first = _set_scope(initialized_settings, item_id=item_id, target_scope="global")
    before = _scope_truth(initialized_settings, item_id=item_id)

    second = _set_scope(initialized_settings, item_id=item_id, target_scope="global")
    after = _scope_truth(initialized_settings, item_id=item_id)

    assert first["changed"] is True
    assert second["changed"] is False
    assert second["hidden_at"] == first["hidden_at"]
    assert after["global_item_at"] == before["global_item_at"]
    assert after["global_key_at"] == before["global_key_at"]
    assert after["audit_count"] == before["audit_count"]
    assert after["counters"] == before["counters"]


def test_scope_transfer_preserves_other_users_personal_hidden_records(initialized_settings) -> None:
    item_id = _insert_media_item(initialized_settings, suffix="other-user")
    now = utcnow_iso()
    with get_connection(initialized_settings) as connection:
        cursor = connection.execute(
            """
            INSERT INTO users (
                username, password_hash, role, enabled, age_credential, created_at, updated_at
            ) VALUES ('other-scope-user', 'unused', 'standard_user', 1, 18, ?, ?)
            """,
            (now, now),
        )
        other_user_id = int(cursor.lastrowid)
        connection.commit()
    hide_media_item_for_user(initialized_settings, user_id=other_user_id, item_id=item_id)
    hide_media_item_for_user(initialized_settings, user_id=1, item_id=item_id)

    _set_scope(initialized_settings, item_id=item_id, target_scope="global")

    other_truth = _scope_truth(initialized_settings, item_id=item_id, user_id=other_user_id)
    assert other_truth["personal_item_at"] is not None
    assert other_truth["personal_key_at"] is not None


def test_scope_response_excludes_private_identity_fields(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, **admin_credentials)
    item_id = _insert_media_item(initialized_settings, suffix="response")

    response = client.put(
        f"/api/admin/hidden-items/{item_id}/scope",
        json={"target_scope": "global"},
    )

    assert response.status_code == 200
    assert set(response.json()) == {
        "item_id",
        "target_scope",
        "changed",
        "hidden_at",
        "message",
    }
    serialized = response.text.lower()
    assert "filename" not in serialized
    assert "file_path" not in serialized
    assert "movie_key" not in serialized


def test_audit_failure_rolls_back_scope_and_revision(
    initialized_settings,
    monkeypatch,
) -> None:
    item_id = _insert_media_item(initialized_settings, suffix="audit-rollback")
    hide_media_item_for_user(initialized_settings, user_id=1, item_id=item_id)
    before = _scope_truth(initialized_settings, item_id=item_id)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(library_hidden_service, "write_audit_event_in_connection", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        _set_scope(initialized_settings, item_id=item_id, target_scope="global")

    after = _scope_truth(initialized_settings, item_id=item_id)
    assert after == before


def test_target_insertion_failure_rolls_back_scope_audit_and_revision(
    initialized_settings,
    monkeypatch,
) -> None:
    item_id = _insert_media_item(initialized_settings, suffix="insert-rollback")
    hide_media_item_for_user(initialized_settings, user_id=1, item_id=item_id)
    before = _scope_truth(initialized_settings, item_id=item_id)
    original_hide_globally = library_hidden_service._hide_globally_in_connection

    def insert_then_fail(*args, **kwargs):
        original_hide_globally(*args, **kwargs)
        raise RuntimeError("injected target insertion failure")

    monkeypatch.setattr(
        library_hidden_service,
        "_hide_globally_in_connection",
        insert_then_fail,
    )
    with pytest.raises(RuntimeError, match="injected target insertion failure"):
        _set_scope(initialized_settings, item_id=item_id, target_scope="global")

    assert _scope_truth(initialized_settings, item_id=item_id) == before


@pytest.mark.parametrize("failure_timing", ["before", "after"])
def test_source_deletion_failure_rolls_back_scope_audit_and_revision(
    initialized_settings,
    monkeypatch,
    failure_timing,
) -> None:
    item_id = _insert_media_item(initialized_settings, suffix=f"delete-{failure_timing}")
    hide_media_item_for_user(initialized_settings, user_id=1, item_id=item_id)
    before = _scope_truth(initialized_settings, item_id=item_id)
    original_show_for_user = library_hidden_service._show_for_user_in_connection

    def fail_around_delete(*args, **kwargs):
        if failure_timing == "before":
            raise RuntimeError("injected source deletion failure")
        original_show_for_user(*args, **kwargs)
        raise RuntimeError("injected source deletion failure")

    monkeypatch.setattr(
        library_hidden_service,
        "_show_for_user_in_connection",
        fail_around_delete,
    )
    with pytest.raises(RuntimeError, match="injected source deletion failure"):
        _set_scope(initialized_settings, item_id=item_id, target_scope="global")

    assert _scope_truth(initialized_settings, item_id=item_id) == before


def test_successful_personal_to_global_transfer_bumps_each_revision_at_most_once(
    initialized_settings,
) -> None:
    item_id = _insert_media_item(initialized_settings, suffix="revision-dedupe")
    hide_media_item_for_user(initialized_settings, user_id=1, item_id=item_id)
    before = _scope_truth(initialized_settings, item_id=item_id)

    result = _set_scope(initialized_settings, item_id=item_id, target_scope="global")
    after = _scope_truth(initialized_settings, item_id=item_id)

    assert result["changed"] is True
    assert (
        _counter_value(after, scope_kind="global", scope_id=0, layer="permission")
        - _counter_value(before, scope_kind="global", scope_id=0, layer="permission")
    ) == 1
    assert (
        _counter_value(after, scope_kind="user", scope_id=1, layer="user_overlay")
        - _counter_value(before, scope_kind="user", scope_id=1, layer="user_overlay")
    ) == 1
    assert int(after["audit_count"]) - int(before["audit_count"]) == 1


def test_existing_global_hide_and_show_contracts_and_audit_actions_remain_unchanged(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, **admin_credentials)
    item_id = _insert_media_item(initialized_settings, suffix="legacy-global-contract")

    hidden_response = client.post(f"/api/admin/global-hidden-items/{item_id}")
    assert hidden_response.status_code == 200
    assert hidden_response.json() == {"message": "This movie is hidden for everyone"}
    hidden_truth = _scope_truth(initialized_settings, item_id=item_id)
    assert hidden_truth["global_item_at"] is not None
    assert hidden_truth["global_key_at"] is not None

    shown_response = client.delete(f"/api/admin/global-hidden-items/{item_id}")
    assert shown_response.status_code == 200
    assert shown_response.json() == {"message": "This movie is visible again for everyone"}
    shown_truth = _scope_truth(initialized_settings, item_id=item_id)
    assert shown_truth["global_item_at"] is None
    assert shown_truth["global_key_at"] is None

    with get_connection(initialized_settings) as connection:
        actions = [
            str(row["action"])
            for row in connection.execute(
                """
                SELECT action
                FROM audit_logs
                WHERE media_item_id = ?
                  AND action IN (
                      'admin.library.hide_global',
                      'admin.library.show_global'
                  )
                ORDER BY id
                """,
                (item_id,),
            ).fetchall()
        ]
    assert actions == [
        "admin.library.hide_global",
        "admin.library.show_global",
    ]


def test_existing_global_hide_audit_failure_rolls_back_state(
    client,
    initialized_settings,
    admin_credentials,
    monkeypatch,
) -> None:
    _login(client, **admin_credentials)
    item_id = _insert_media_item(initialized_settings, suffix="legacy-global-rollback")
    before = _scope_truth(initialized_settings, item_id=item_id)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(library_hidden_service, "write_audit_event_in_connection", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        client.post(f"/api/admin/global-hidden-items/{item_id}")

    after = _scope_truth(initialized_settings, item_id=item_id)
    assert after == before


def test_existing_global_show_audit_failure_rolls_back_state(
    client,
    initialized_settings,
    admin_credentials,
    monkeypatch,
) -> None:
    _login(client, **admin_credentials)
    item_id = _insert_media_item(initialized_settings, suffix="legacy-global-show-rollback")
    hidden_response = client.post(f"/api/admin/global-hidden-items/{item_id}")
    assert hidden_response.status_code == 200
    before = _scope_truth(initialized_settings, item_id=item_id)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(library_hidden_service, "write_audit_event_in_connection", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        client.delete(f"/api/admin/global-hidden-items/{item_id}")

    after = _scope_truth(initialized_settings, item_id=item_id)
    assert after == before
