from __future__ import annotations

from dataclasses import replace

from backend.app.db import get_connection, init_db, utcnow_iso
from backend.app.services.library_movie_identity_service import _row_hidden_movie_key
from backend.app.services.local_library_source_service import ensure_current_shared_local_source_binding


def _login(client, *, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _revision(client) -> tuple[dict[str, str], object]:
    response = client.get("/api/library/v2/revision")
    assert response.status_code == 200
    return response.json(), response


def _insert_item(connection, *, suffix: str = "one") -> int:
    now = utcnow_iso()
    cursor = connection.execute(
        """
        INSERT INTO media_items (
            title, original_filename, file_path, file_size, file_mtime,
            created_at, updated_at, last_scanned_at
        ) VALUES (?, ?, ?, 1, 1, ?, ?, ?)
        """,
        (f"Movie {suffix}", f"movie-{suffix}.mkv", f"/safe/movie-{suffix}.mkv", now, now, now),
    )
    return int(cursor.lastrowid)


def test_revision_migration_is_idempotent_and_endpoint_is_private(client, initialized_settings, admin_credentials):
    init_db(initialized_settings)
    init_db(initialized_settings)
    _login(client, **admin_credentials)

    payload, response = _revision(client)

    assert payload["schema_version"] == "library-revision-v1"
    assert set(payload) == {
        "schema_version", "catalog", "presentation", "permission",
        "user_overlay", "progress", "combined_library",
    }
    assert all(len(payload[field]) == 64 for field in payload if field != "schema_version")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"


def test_catalog_and_progress_change_only_their_layers(client, initialized_settings, admin_credentials):
    _login(client, **admin_credentials)
    baseline, _ = _revision(client)
    with get_connection(initialized_settings) as connection:
        item_id = _insert_item(connection)
        shared_source_id = ensure_current_shared_local_source_binding(
            initialized_settings,
            connection=connection,
        )
        connection.execute(
            "UPDATE media_items SET library_source_id = ? WHERE id = ?",
            (shared_source_id, item_id),
        )
        connection.commit()
    after_catalog, _ = _revision(client)

    assert after_catalog["catalog"] != baseline["catalog"]
    assert after_catalog["combined_library"] != baseline["combined_library"]
    assert after_catalog["progress"] == baseline["progress"]

    with get_connection(initialized_settings) as connection:
        connection.execute(
            """
            INSERT INTO playback_progress (
                user_id, media_item_id, position_seconds, duration_seconds,
                watch_seconds_total, completed, updated_at
            ) VALUES (1, ?, 120, 7200, 120, 0, ?)
            """,
            (item_id, utcnow_iso()),
        )
        connection.commit()
    after_progress, _ = _revision(client)

    assert after_progress["progress"] != after_catalog["progress"]
    assert after_progress["combined_library"] == after_catalog["combined_library"]
    progress_response = client.get("/api/library/v2/progress-state")
    assert progress_response.status_code == 200
    assert progress_response.json() == {
        "schema_version": "library-progress-state-v1",
        "progress_revision": after_progress["progress"],
        "items": [{
            "id": item_id,
            "progress_seconds": 120.0,
            "progress_duration_seconds": 7200.0,
            "completed": False,
        }],
    }


def test_rollback_does_not_change_revision(client, initialized_settings, admin_credentials):
    _login(client, **admin_credentials)
    baseline, _ = _revision(client)
    with get_connection(initialized_settings) as connection:
        _insert_item(connection, suffix="rolled-back")
        connection.rollback()

    current, _ = _revision(client)
    assert current == baseline


def test_revision_tokens_are_user_isolated(client, admin_credentials):
    _login(client, **admin_credentials)
    admin_revision, _ = _revision(client)
    created = client.post("/api/admin/users", json={
        "username": "revision-user",
        "password": "revision-user-password",
        "role": "standard_user",
        "enabled": True,
    })
    assert created.status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    _login(client, username="revision-user", password="revision-user-password")
    user_revision, _ = _revision(client)

    assert user_revision["catalog"] != admin_revision["catalog"]
    assert user_revision["combined_library"] != admin_revision["combined_library"]


def test_revision_capability_can_be_disabled(client, initialized_settings, admin_credentials):
    _login(client, **admin_credentials)
    client.app.state.settings = replace(initialized_settings, library_revision_enabled=False)

    response = client.get("/api/library/v2/revision")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "library_revision_disabled"


def test_progress_state_excludes_inaccessible_source_and_hidden_movie_key(
    client,
    initialized_settings,
    admin_credentials,
):
    _login(client, **admin_credentials)
    private_owner = client.post("/api/admin/users", json={
        "username": "revision-private-owner",
        "password": "revision-private-owner-password",
        "role": "standard_user",
        "enabled": True,
    })
    assert private_owner.status_code == 200
    private_owner_id = int(private_owner.json()["id"])
    now = utcnow_iso()
    with get_connection(initialized_settings) as connection:
        shared_source_id = ensure_current_shared_local_source_binding(
            initialized_settings,
            connection=connection,
        )
        visible_id = _insert_item(connection, suffix="visible-progress")
        connection.execute(
            "UPDATE media_items SET library_source_id = ?, year = 2024 WHERE id = ?",
            (shared_source_id, visible_id),
        )
        private_source_id = int(connection.execute(
            """
            INSERT INTO library_sources (
                owner_user_id, provider, resource_type, resource_id,
                display_name, is_shared, created_at, updated_at
            ) VALUES (?, 'google_drive', 'folder', 'private-source',
                      'Private source', 0, ?, ?)
            """,
            (private_owner_id, now, now),
        ).lastrowid)
        inaccessible_id = _insert_item(connection, suffix="inaccessible-progress")
        connection.execute(
            """
            UPDATE media_items
            SET source_kind = 'cloud', library_source_id = ?, year = 2024
            WHERE id = ?
            """,
            (private_source_id, inaccessible_id),
        )
        hidden_id = _insert_item(connection, suffix="hidden-progress")
        connection.execute(
            "UPDATE media_items SET library_source_id = ?, year = 2024 WHERE id = ?",
            (shared_source_id, hidden_id),
        )
        hidden_row = connection.execute(
            "SELECT title, year, original_filename FROM media_items WHERE id = ?",
            (hidden_id,),
        ).fetchone()
        hidden_movie_key = _row_hidden_movie_key(hidden_row)
        assert hidden_movie_key
        connection.execute(
            """
            INSERT INTO user_hidden_movie_keys (
                user_id, movie_key, display_title, year, edition_identity, hidden_at
            ) VALUES (1, ?, 'Movie hidden-progress', 2024, 'standard', ?)
            """,
            (hidden_movie_key, now),
        )
        for item_id in (visible_id, inaccessible_id, hidden_id):
            connection.execute(
                """
                INSERT INTO playback_progress (
                    user_id, media_item_id, position_seconds, duration_seconds,
                    watch_seconds_total, completed, updated_at
                ) VALUES (1, ?, 60, 600, 60, 0, ?)
                """,
                (item_id, now),
            )
        connection.commit()

    response = client.get("/api/library/v2/progress-state")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [visible_id]
