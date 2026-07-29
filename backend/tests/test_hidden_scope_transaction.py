from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.app import db_hidden_movie_keys
from backend.app.db import get_connection, utcnow_iso
from backend.app.db_hidden_movie_keys import (
    _legacy_hidden_keys_for_row,
    materialize_legacy_hidden_coverage_for_item,
    migrate_legacy_hidden_movie_keys,
    preserve_hidden_movie_keys_for_media_item,
    repair_hidden_copy_identity_collisions,
    resolve_hidden_copy_identity,
)
from backend.app.services import library_hidden_service
from backend.app.services.library_hidden_service import (
    hide_media_item_for_user,
    set_hidden_media_item_scope,
    show_media_item_for_user,
)
from backend.app.services.local_library_source_service import (
    ensure_current_shared_local_source_binding,
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
        resolve_hidden_copy_identity(
            connection,
            media_item_id=int(cursor.lastrowid),
        )
        connection.commit()
        return int(cursor.lastrowid)


def _insert_copy_pair(settings) -> tuple[int, int]:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        source_id = ensure_current_shared_local_source_binding(
            settings,
            connection=connection,
        )
        item_ids = []
        for resolution, width, height in (("1080p", 1920, 1080), ("720p", 1280, 720)):
            cursor = connection.execute(
                """
                INSERT INTO media_items (
                    title, original_filename, file_path, source_kind,
                    library_source_id, file_size, file_mtime, width, height,
                    year, created_at, updated_at, last_scanned_at
                ) VALUES (
                    'Movie A', ?, ?, 'local', ?, 1, 1, ?, ?, 2026, ?, ?, ?
                )
                """,
                (
                    f"Movie.A.2026.{resolution}.BluRay.mkv",
                    f"/safe/movie-a-{resolution}.mkv",
                    source_id,
                    width,
                    height,
                    now,
                    now,
                    now,
                ),
            )
            item_ids.append(int(cursor.lastrowid))
        connection.commit()
    return item_ids[0], item_ids[1]


def _insert_local_source(settings, *, resource_id: str, local_path: str) -> int:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        cursor = connection.execute(
            """
            INSERT INTO library_sources (
                owner_user_id, provider, resource_type, resource_id,
                display_name, local_path, is_shared, created_at, updated_at
            ) VALUES (1, 'local', 'directory', ?, ?, ?, 1, ?, ?)
            """,
            (resource_id, resource_id, local_path, now, now),
        )
        connection.commit()
        return int(cursor.lastrowid)


def _insert_identity_item(
    settings,
    *,
    file_path: str,
    source_id: int,
    filename: str = "Identity.Movie.2026.1080p.mkv",
    source_kind: str = "local",
    external_media_id: str | None = None,
    cloud_resource_key: str | None = None,
) -> int:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        cursor = connection.execute(
            """
            INSERT INTO media_items (
                title, original_filename, file_path, source_kind,
                library_source_id, external_media_id, cloud_resource_key,
                file_size, file_mtime, width, height, year,
                created_at, updated_at, last_scanned_at
            ) VALUES (
                'Identity Movie', ?, ?, ?, ?, ?, ?,
                4096, 1700000000, 1920, 1080, 2026, ?, ?, ?
            )
            """,
            (
                filename,
                file_path,
                source_kind,
                source_id,
                external_media_id,
                cloud_resource_key,
                now,
                now,
                now,
            ),
        )
        item_id = int(cursor.lastrowid)
        resolve_hidden_copy_identity(connection, media_item_id=item_id)
        connection.commit()
        return item_id


def _copy_identity(settings, *, item_id: int) -> str:
    with get_connection(settings) as connection:
        identity = resolve_hidden_copy_identity(connection, media_item_id=item_id)
        connection.commit()
    assert identity is not None
    return identity


def _scope_truth(settings, *, item_id: int, user_id: int = 1) -> dict[str, object]:
    with get_connection(settings) as connection:
        media_row = connection.execute(
            """
            SELECT
                id, title, year, original_filename, file_path,
                COALESCE(source_kind, 'local') AS source_kind,
                library_source_id, external_media_id, cloud_resource_key,
                hidden_copy_identity
            FROM media_items
            WHERE id = ?
            """,
            (item_id,),
        ).fetchone()
        movie_key = str(resolve_hidden_copy_identity(connection, media_row=media_row))
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


def test_hidden_scope_service_rejects_invalid_scope_before_database_access(
    initialized_settings,
    monkeypatch,
) -> None:
    def fail_if_opened(*_args, **_kwargs):
        raise AssertionError("database must not be opened for an invalid scope")

    monkeypatch.setattr(library_hidden_service, "get_connection", fail_if_opened)

    with pytest.raises(ValueError, match="invalid_scope"):
        _set_scope(initialized_settings, item_id=1, target_scope="shared")


def test_hidden_copy_scope_is_independent_for_matching_1080p_and_720p_copies(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, **admin_credentials)
    copy_1080p, copy_720p = _insert_copy_pair(initialized_settings)
    settings_response = client.patch(
        "/api/user-settings",
        json={"hide_duplicate_movies": False},
    )
    assert settings_response.status_code == 200

    hide_media_item_for_user(initialized_settings, user_id=1, item_id=copy_1080p)
    library_after_1080p_hide = client.get("/api/library")
    assert library_after_1080p_hide.status_code == 200
    visible_ids = {int(item["id"]) for item in library_after_1080p_hide.json()["items"]}
    assert copy_1080p not in visible_ids
    assert copy_720p in visible_ids

    hide_media_item_for_user(initialized_settings, user_id=1, item_id=copy_720p)
    personal_list = client.get("/api/user-hidden-items")
    assert personal_list.status_code == 200
    assert {int(item["id"]) for item in personal_list.json()["items"]}.issuperset(
        {copy_1080p, copy_720p}
    )

    _set_scope(initialized_settings, item_id=copy_1080p, target_scope="global")
    assert _scope_truth(initialized_settings, item_id=copy_1080p)["global_item_at"] is not None
    assert _scope_truth(initialized_settings, item_id=copy_1080p)["personal_item_at"] is None
    sibling_truth = _scope_truth(initialized_settings, item_id=copy_720p)
    assert sibling_truth["personal_item_at"] is not None
    assert sibling_truth["global_item_at"] is None

    _set_scope(initialized_settings, item_id=copy_1080p, target_scope="personal")
    sibling_after_reverse = _scope_truth(initialized_settings, item_id=copy_720p)
    assert sibling_after_reverse["personal_item_at"] == sibling_truth["personal_item_at"]
    assert sibling_after_reverse["global_item_at"] is None

    show_media_item_for_user(initialized_settings, user_id=1, item_id=copy_1080p)
    assert _scope_truth(initialized_settings, item_id=copy_1080p)["personal_item_at"] is None
    assert _scope_truth(initialized_settings, item_id=copy_720p)["personal_item_at"] is not None


def test_hidden_copy_identity_separates_same_basename_by_directory_and_source(
    initialized_settings,
) -> None:
    first_source = _insert_local_source(
        initialized_settings,
        resource_id="identity-source-a",
        local_path="/safe/source-a",
    )
    second_source = _insert_local_source(
        initialized_settings,
        resource_id="identity-source-b",
        local_path="/safe/source-b",
    )
    first = _insert_identity_item(
        initialized_settings,
        file_path="/safe/source-a/one/Same.Name.2026.mkv",
        source_id=first_source,
        filename="Same.Name.2026.mkv",
    )
    second_directory = _insert_identity_item(
        initialized_settings,
        file_path="/safe/source-a/two/Same.Name.2026.mkv",
        source_id=first_source,
        filename="Same.Name.2026.mkv",
    )
    second_source_copy = _insert_identity_item(
        initialized_settings,
        file_path="/safe/source-b/one/Same.Name.2026.mkv",
        source_id=second_source,
        filename="Same.Name.2026.mkv",
    )

    identities = {
        _copy_identity(initialized_settings, item_id=item_id)
        for item_id in (first, second_directory, second_source_copy)
    }
    assert len(identities) == 3
    assert all(identity.startswith("copy-v2:") for identity in identities)
    serialized = "\n".join(identities)
    assert "/safe/" not in serialized
    assert "same.name" not in serialized.lower()


def test_hidden_copy_identity_survives_recreation_and_strong_row_rename(
    initialized_settings,
) -> None:
    source_root = Path(initialized_settings.media_root) / "identity-recreation"
    source_root.mkdir()
    source_id = _insert_local_source(
        initialized_settings,
        resource_id="identity-recreation",
        local_path=str(source_root),
    )
    original_path = source_root / "Original.2026.mkv"
    renamed_path = source_root / "Renamed.2026.mkv"
    original_path.write_bytes(b"identity-recreation-content")
    item_id = _insert_identity_item(
        initialized_settings,
        file_path=str(original_path),
        source_id=source_id,
    )
    original_identity = _copy_identity(initialized_settings, item_id=item_id)
    with get_connection(initialized_settings) as connection:
        aliases = connection.execute(
            """
            SELECT locator_hash, evidence_hash
            FROM hidden_copy_identity_aliases
            WHERE hidden_copy_identity = ?
            """,
            (original_identity,),
        ).fetchall()
    assert aliases
    serialized_aliases = "\n".join(
        f"{row['locator_hash']}:{row['evidence_hash']}" for row in aliases
    ).lower()
    assert str(source_root).lower() not in serialized_aliases
    assert "original.2026.mkv" not in serialized_aliases

    original_path.rename(renamed_path)
    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE media_items SET file_path = ?, original_filename = ? WHERE id = ?",
            (str(renamed_path), "Renamed.2026.mkv", item_id),
        )
        renamed_identity = resolve_hidden_copy_identity(connection, media_item_id=item_id)
        assert renamed_identity == original_identity
        preserve_hidden_movie_keys_for_media_item(connection, media_item_id=item_id)
        connection.execute("DELETE FROM media_items WHERE id = ?", (item_id,))
        connection.commit()

    renamed_path.unlink()
    original_path.write_bytes(b"identity-recreation-content")
    recreated = _insert_identity_item(
        initialized_settings,
        file_path=str(original_path),
        source_id=source_id,
    )
    assert _copy_identity(initialized_settings, item_id=recreated) == original_identity


def test_cloud_hidden_copy_identity_uses_source_scoped_external_id_not_filename(
    initialized_settings,
) -> None:
    first_source = _insert_local_source(
        initialized_settings,
        resource_id="cloud-identity-a",
        local_path="/unused/cloud-a",
    )
    second_source = _insert_local_source(
        initialized_settings,
        resource_id="cloud-identity-b",
        local_path="/unused/cloud-b",
    )
    first = _insert_identity_item(
        initialized_settings,
        file_path="gdrive://source-a/folder/Before.mkv",
        source_id=first_source,
        source_kind="cloud",
        external_media_id="provider-file-1",
    )
    same_provider_copy_renamed = _copy_identity(initialized_settings, item_id=first)
    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE media_items SET file_path = ?, original_filename = ? WHERE id = ?",
            ("gdrive://source-a/renamed/After.mkv", "After.mkv", first),
        )
        assert resolve_hidden_copy_identity(connection, media_item_id=first) == same_provider_copy_renamed
        connection.commit()
    other_source = _insert_identity_item(
        initialized_settings,
        file_path="gdrive://source-b/folder/Before.mkv",
        source_id=second_source,
        source_kind="cloud",
        external_media_id="provider-file-1",
    )
    assert _copy_identity(initialized_settings, item_id=other_source) != same_provider_copy_renamed


def test_non_local_path_fallback_identity_remains_deterministic(
    initialized_settings,
) -> None:
    source_id = _insert_local_source(
        initialized_settings,
        resource_id="provider-path-fallback",
        local_path="/unused/provider-path-fallback",
    )
    item_id = _insert_identity_item(
        initialized_settings,
        file_path="provider://source/item-one",
        source_id=source_id,
        source_kind="provider",
    )
    original_identity = _copy_identity(initialized_settings, item_id=item_id)

    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE media_items SET hidden_copy_identity = NULL WHERE id = ?",
            (item_id,),
        )
        rebuilt_identity = resolve_hidden_copy_identity(
            connection,
            media_item_id=item_id,
        )
        connection.commit()

    assert rebuilt_identity == original_identity


def test_legacy_group_materializes_all_copies_before_selected_show_again(
    initialized_settings,
) -> None:
    first, second = _insert_copy_pair(initialized_settings)
    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT
                id, title, year, original_filename, file_path,
                COALESCE(source_kind, 'local') AS source_kind,
                library_source_id, external_media_id, cloud_resource_key,
                hidden_copy_identity
            FROM media_items
            WHERE id = ?
            """,
            (first,),
        ).fetchone()
        legacy_group_key = _legacy_hidden_keys_for_row(row)[0]
        connection.execute(
            """
            INSERT INTO user_hidden_movie_keys (
                user_id, movie_key, display_title, year, edition_identity, hidden_at
            ) VALUES (1, ?, 'Movie A', 2026, 'standard', '2026-01-02 03:04:05')
            """,
            (legacy_group_key,),
        )
        connection.commit()

    show_media_item_for_user(initialized_settings, user_id=1, item_id=first)

    assert _scope_truth(initialized_settings, item_id=first)["personal_key_at"] is None
    second_truth = _scope_truth(initialized_settings, item_id=second)
    assert second_truth["personal_key_at"] == "2026-01-02 03:04:05"
    with get_connection(initialized_settings) as connection:
        legacy = connection.execute(
            """
            SELECT 1
            FROM user_hidden_movie_keys
            WHERE user_id = 1 AND movie_key = ?
            """,
            (legacy_group_key,),
        ).fetchone()
    assert legacy is None


def test_legacy_transitional_key_migrates_once_and_orphan_is_retained(
    initialized_settings,
) -> None:
    item_id = _insert_media_item(initialized_settings, suffix="legacy-migration")
    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT
                id, title, year, original_filename, file_path,
                COALESCE(source_kind, 'local') AS source_kind,
                library_source_id, external_media_id, cloud_resource_key,
                hidden_copy_identity
            FROM media_items
            WHERE id = ?
            """,
            (item_id,),
        ).fetchone()
        legacy_keys = _legacy_hidden_keys_for_row(row)
        transitional_key = legacy_keys[-1]
        connection.execute(
            """
            INSERT INTO user_hidden_movie_keys (
                user_id, movie_key, display_title, year, edition_identity, hidden_at
            ) VALUES (1, ?, 'Scope Movie', 2026, 'standard', '2026-02-03 04:05:06')
            """,
            (transitional_key,),
        )
        orphan_key = "orphan|1999|standard"
        connection.execute(
            """
            INSERT INTO user_hidden_movie_keys (
                user_id, movie_key, display_title, year, edition_identity, hidden_at
            ) VALUES (1, ?, 'Orphan', 1999, 'standard', '2026-02-03 04:05:07')
            """,
            (orphan_key,),
        )
        first = migrate_legacy_hidden_movie_keys(connection)
        second = migrate_legacy_hidden_movie_keys(connection)
        copy_identity = resolve_hidden_copy_identity(connection, media_item_id=item_id)
        migrated = connection.execute(
            """
            SELECT hidden_at
            FROM user_hidden_movie_keys
            WHERE user_id = 1 AND movie_key = ?
            """,
            (copy_identity,),
        ).fetchone()
        orphan = connection.execute(
            """
            SELECT hidden_at
            FROM user_hidden_movie_keys
            WHERE user_id = 1 AND movie_key = ?
            """,
            (orphan_key,),
        ).fetchone()
        connection.commit()

    assert first["user_legacy_keys_migrated"] == 1
    assert first["user_legacy_keys_retained"] == 1
    assert second["user_legacy_keys_migrated"] == 0
    assert second["user_legacy_keys_retained"] == 1
    assert migrated["hidden_at"] == "2026-02-03 04:05:06"
    assert orphan["hidden_at"] == "2026-02-03 04:05:07"


@pytest.mark.parametrize("unrelated_legacy_count", [0, 1, 1000, 3000])
def test_normal_hidden_mutations_skip_full_identity_scan_without_relevant_legacy_key(
    initialized_settings,
    monkeypatch,
    unrelated_legacy_count: int,
) -> None:
    item_id = _insert_media_item(
        initialized_settings,
        suffix=f"fast-path-{unrelated_legacy_count}",
    )
    if unrelated_legacy_count:
        now = utcnow_iso()
        with get_connection(initialized_settings) as connection:
            for index in range(unrelated_legacy_count):
                connection.execute(
                    """
                    INSERT INTO user_hidden_movie_keys (
                        user_id, movie_key, display_title, year,
                        edition_identity, hidden_at
                    ) VALUES (1, ?, 'Unrelated', 1999, 'standard', ?)
                    """,
                    (f"unrelated-{unrelated_legacy_count}-{index}", now),
                )
            connection.commit()

    def fail_full_scan(_connection):
        raise AssertionError("ordinary Hidden mutation must not scan all media_items")

    monkeypatch.setattr(db_hidden_movie_keys, "_load_identity_rows", fail_full_scan)
    hide_media_item_for_user(initialized_settings, user_id=1, item_id=item_id)
    show_media_item_for_user(initialized_settings, user_id=1, item_id=item_id)
    _set_scope(initialized_settings, item_id=item_id, target_scope="personal")
    _set_scope(initialized_settings, item_id=item_id, target_scope="global")


def test_personal_legacy_fast_path_does_not_query_global_records(
    initialized_settings,
) -> None:
    item_id = _insert_media_item(initialized_settings, suffix="personal-fast-path")
    statements: list[str] = []
    with get_connection(initialized_settings) as connection:
        connection.set_trace_callback(statements.append)
        materialize_legacy_hidden_coverage_for_item(
            connection,
            media_item_id=item_id,
            user_id=1,
            include_global=False,
        )
        connection.set_trace_callback(None)
    assert not any("global_hidden_movie_keys" in statement for statement in statements)
    assert not any(
        "FROM media_items" in statement and "WHERE id =" not in statement
        for statement in statements
    )


def test_historical_identity_collision_repair_preserves_scope_and_enforces_uniqueness(
    initialized_settings,
) -> None:
    first, second = _insert_copy_pair(initialized_settings)
    now = utcnow_iso()
    shared_identity = "copy-v2:" + ("a" * 64)
    with get_connection(initialized_settings) as connection:
        connection.execute(
            "DROP INDEX idx_media_items_active_hidden_copy_identity"
        )
        connection.execute(
            "UPDATE media_items SET hidden_copy_identity = ? WHERE id IN (?, ?)",
            (shared_identity, first, second),
        )
        connection.execute(
            """
            INSERT INTO users (
                username, password_hash, role, enabled, age_credential,
                created_at, updated_at
            ) VALUES ('collision-user', 'unused', 'standard_user', 1, 18, ?, ?)
            """,
            (now, now),
        )
        second_user_id = int(connection.execute(
            "SELECT id FROM users WHERE username = 'collision-user'"
        ).fetchone()["id"])
        for user_id, hidden_at in (
            (1, "2026-01-01T00:00:00+00:00"),
            (second_user_id, "2026-01-02T00:00:00+00:00"),
        ):
            connection.execute(
                """
                INSERT INTO user_hidden_movie_keys (
                    user_id, movie_key, display_title, year,
                    edition_identity, hidden_at
                ) VALUES (?, ?, 'Movie A', 2026, 'standard', ?)
                """,
                (user_id, shared_identity, hidden_at),
            )
        connection.execute(
            """
            INSERT INTO global_hidden_movie_keys (
                movie_key, display_title, year, edition_identity,
                hidden_by_user_id, hidden_at
            ) VALUES (?, 'Movie A', 2026, 'standard', 1, ?)
            """,
            (shared_identity, "2026-01-03T00:00:00+00:00"),
        )
        connection.execute(
            """
            INSERT INTO user_hidden_media_items (
                user_id, media_item_id, hidden_at
            ) VALUES (?, ?, ?)
            """,
            (second_user_id, second, "2026-01-02T00:00:00+00:00"),
        )
        first_result = repair_hidden_copy_identity_collisions(connection)
        second_result = repair_hidden_copy_identity_collisions(connection)
        identities = [
            str(row["hidden_copy_identity"])
            for row in connection.execute(
                "SELECT hidden_copy_identity FROM media_items WHERE id IN (?, ?) ORDER BY id",
                (first, second),
            ).fetchall()
        ]
        assert len(set(identities)) == 2
        assert shared_identity in identities
        assert first_result["identity_collisions_repaired"] == 1
        assert second_result["identity_collisions_repaired"] == 0
        for user_id, hidden_at in (
            (1, "2026-01-01T00:00:00+00:00"),
            (second_user_id, "2026-01-02T00:00:00+00:00"),
        ):
            records = connection.execute(
                """
                SELECT movie_key, hidden_at
                FROM user_hidden_movie_keys
                WHERE user_id = ? AND movie_key IN (?, ?)
                ORDER BY movie_key
                """,
                (user_id, *identities),
            ).fetchall()
            assert len(records) == 2
            assert {str(row["hidden_at"]) for row in records} == {hidden_at}
        global_records = connection.execute(
            """
            SELECT movie_key, hidden_at
            FROM global_hidden_movie_keys
            WHERE movie_key IN (?, ?)
            """,
            identities,
        ).fetchall()
        assert len(global_records) == 2
        assert {
            str(row["hidden_at"]) for row in global_records
        } == {"2026-01-03T00:00:00+00:00"}
        direct = connection.execute(
            """
            SELECT hidden_at
            FROM user_hidden_media_items
            WHERE user_id = ? AND media_item_id = ?
            """,
            (second_user_id, second),
        ).fetchone()
        assert direct is not None
        assert direct["hidden_at"] == "2026-01-02T00:00:00+00:00"
        connection.execute(
            """
            CREATE UNIQUE INDEX idx_media_items_active_hidden_copy_identity
            ON media_items (hidden_copy_identity)
            WHERE hidden_copy_identity LIKE 'copy-v2:%'
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE media_items SET hidden_copy_identity = ? WHERE id = ?",
                (identities[0], second),
            )
        connection.rollback()


def test_historical_identity_collision_repair_rolls_back_atomically(
    initialized_settings,
    monkeypatch,
) -> None:
    first, second = _insert_copy_pair(initialized_settings)
    shared_identity = "copy-v2:" + ("b" * 64)
    now = utcnow_iso()
    with get_connection(initialized_settings) as connection:
        connection.execute(
            "DROP INDEX idx_media_items_active_hidden_copy_identity"
        )
        connection.execute(
            "UPDATE media_items SET hidden_copy_identity = ? WHERE id IN (?, ?)",
            (shared_identity, first, second),
        )
        connection.execute(
            """
            INSERT INTO user_hidden_movie_keys (
                user_id, movie_key, display_title, year,
                edition_identity, hidden_at
            ) VALUES (1, ?, 'Movie A', 2026, 'standard', ?)
            """,
            (shared_identity, now),
        )

        def fail_materialization(*_args, **_kwargs):
            raise RuntimeError("injected collision repair failure")

        monkeypatch.setattr(
            db_hidden_movie_keys,
            "_insert_user_copy_key",
            fail_materialization,
        )
        with pytest.raises(RuntimeError, match="injected collision repair failure"):
            repair_hidden_copy_identity_collisions(connection)

        identities = {
            str(row["hidden_copy_identity"])
            for row in connection.execute(
                "SELECT hidden_copy_identity FROM media_items WHERE id IN (?, ?)",
                (first, second),
            ).fetchall()
        }
        assert identities == {shared_identity}
        records = connection.execute(
            """
            SELECT movie_key, hidden_at
            FROM user_hidden_movie_keys
            WHERE user_id = 1
            """
        ).fetchall()
        assert [(row["movie_key"], row["hidden_at"]) for row in records] == [
            (shared_identity, now),
        ]
        connection.rollback()


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
