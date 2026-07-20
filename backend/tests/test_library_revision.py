from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import time

from backend.app.db import (
    get_connection,
    init_db,
    LIBRARY_REVISION_TRIGGER_NAMES,
    utcnow_iso,
)
from backend.app.models import AuthenticatedUser
from backend.app.progress import save_progress
from backend.app.services.app_settings_service import (
    update_media_library_reference,
    update_poster_reference_location,
)
from backend.app.services.library_movie_identity_service import _row_hidden_movie_key
from backend.app.services.library_revision_mutation_service import bump_library_revision_layers
from backend.app.services.local_library_source_service import ensure_current_shared_local_source_binding
from backend.app.services.media_age_access_service import load_accessible_media_item_ids_by_age
from backend.app.services.media_age_access_service import resolve_age_restriction_movie_group
from backend.app.services.poster_index_service import (
    get_poster_index_snapshot,
    invalidate_poster_indexes,
)


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


def test_progress_state_includes_authoritative_zero_reset(client, initialized_settings, admin_credentials):
    _login(client, **admin_credentials)
    with get_connection(initialized_settings) as connection:
        item_id = _insert_item(connection, suffix="zero-reset")
        shared_source_id = ensure_current_shared_local_source_binding(
            initialized_settings,
            connection=connection,
        )
        connection.execute(
            "UPDATE media_items SET library_source_id = ? WHERE id = ?",
            (shared_source_id, item_id),
        )
        connection.execute(
            """
            INSERT INTO playback_progress (
                user_id, media_item_id, position_seconds, duration_seconds,
                watch_seconds_total, completed, updated_at
            ) VALUES (1, ?, 0, 7200, 120, 0, ?)
            """,
            (item_id, utcnow_iso()),
        )
        connection.commit()

    response = client.get("/api/library/v2/progress-state")

    assert response.status_code == 200
    assert response.json()["items"] == [{
        "id": item_id,
        "progress_seconds": 0.0,
        "progress_duration_seconds": 7200.0,
        "completed": False,
    }]


def test_revision_disabled_removes_triggers_and_ordinary_writes_are_zero_cost(initialized_settings):
    disabled_settings = replace(initialized_settings, library_revision_enabled=False)
    init_db(disabled_settings)
    with get_connection(disabled_settings) as connection:
        trigger_names = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_library_revision_%'"
        ).fetchall()
        before = connection.execute(
            "SELECT COALESCE(SUM(counter), 0) AS total FROM library_revision_counters"
        ).fetchone()["total"]
        item_id = _insert_item(connection, suffix="disabled")
        connection.execute(
            """
            INSERT INTO playback_progress (
                user_id, media_item_id, position_seconds, duration_seconds,
                watch_seconds_total, completed, updated_at
            ) VALUES (1, ?, 10, 100, 10, 0, ?)
            """,
            (item_id, utcnow_iso()),
        )
        connection.commit()
        after = connection.execute(
            "SELECT COALESCE(SUM(counter), 0) AS total FROM library_revision_counters"
        ).fetchone()["total"]

    assert trigger_names == []
    assert after == before


def test_revision_trigger_lifecycle_reinstalls_only_the_current_registry(initialized_settings):
    with get_connection(initialized_settings) as connection:
        enabled_names = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_library_revision_%'"
            ).fetchall()
        }
    assert enabled_names == set(LIBRARY_REVISION_TRIGGER_NAMES)

    disabled_settings = replace(initialized_settings, library_revision_enabled=False)
    init_db(disabled_settings)
    with get_connection(disabled_settings) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_library_revision_%'"
        ).fetchall() == []

    init_db(initialized_settings)
    with get_connection(initialized_settings) as connection:
        reenabled_names = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_library_revision_%'"
            ).fetchall()
        }
    assert reenabled_names == set(LIBRARY_REVISION_TRIGGER_NAMES)


def test_explicit_revision_bump_is_disabled_and_transaction_bound(initialized_settings):
    disabled_settings = replace(initialized_settings, library_revision_enabled=False)
    init_db(disabled_settings)
    with get_connection(disabled_settings) as connection:
        baseline_total = connection.execute(
            "SELECT COALESCE(SUM(counter), 0) AS total FROM library_revision_counters"
        ).fetchone()["total"]
        assert bump_library_revision_layers(
            disabled_settings,
            connection,
            global_layers=("catalog",),
        ) == 0
        assert connection.execute(
            "SELECT COALESCE(SUM(counter), 0) AS total FROM library_revision_counters"
        ).fetchone()["total"] == baseline_total

    init_db(initialized_settings)
    with get_connection(initialized_settings) as connection:
        before_rollback = connection.execute(
            "SELECT COALESCE(SUM(counter), 0) AS total FROM library_revision_counters"
        ).fetchone()["total"]
        assert bump_library_revision_layers(
            initialized_settings,
            connection,
            global_layers=("catalog", "catalog"),
        ) == 1
        connection.rollback()
    with get_connection(initialized_settings) as connection:
        assert connection.execute(
            "SELECT COALESCE(SUM(counter), 0) AS total FROM library_revision_counters"
        ).fetchone()["total"] == before_rollback


def test_revision_counter_writes_are_deduplicated_per_transaction(initialized_settings):
    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT counter
            FROM library_revision_counters
            WHERE scope_kind = 'global' AND scope_id = 0 AND layer = 'catalog'
            """
        ).fetchone()
        before_counter = int(row["counter"]) if row else 0
        now = utcnow_iso()
        connection.executemany(
            """
            INSERT INTO media_items (
                title, original_filename, file_path, file_size, file_mtime,
                created_at, updated_at, last_scanned_at
            ) VALUES (?, ?, ?, 1, 1, ?, ?, ?)
            """,
            [
                (f"Bulk {index}", f"bulk-{index}.mkv", f"/safe/bulk-{index}.mkv", now, now, now)
                for index in range(3000)
            ],
        )
        connection.commit()
        after_counter = int(connection.execute(
            """
            SELECT counter
            FROM library_revision_counters
            WHERE scope_kind = 'global' AND scope_id = 0 AND layer = 'catalog'
            """
        ).fetchone()["counter"])

    assert after_counter - before_counter == 1


def test_library_source_diagnostics_do_not_change_revision(client, initialized_settings, admin_credentials):
    _login(client, **admin_credentials)
    with get_connection(initialized_settings) as connection:
        source_id = ensure_current_shared_local_source_binding(initialized_settings, connection=connection)
        connection.commit()
    baseline, _ = _revision(client)

    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE library_sources SET last_synced_at = ?, last_error = ? WHERE id = ?",
            (utcnow_iso(), "temporary diagnostic", source_id),
        )
        connection.commit()

    current, _ = _revision(client)
    assert current == baseline


def test_library_source_truth_changes_only_the_intended_layer(
    client,
    initialized_settings,
    admin_credentials,
):
    _login(client, **admin_credentials)
    with get_connection(initialized_settings) as connection:
        source_id = ensure_current_shared_local_source_binding(initialized_settings, connection=connection)
        connection.commit()
    baseline, _ = _revision(client)

    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE library_sources SET is_shared = 0 WHERE id = ?",
            (source_id,),
        )
        connection.commit()
    after_permission, _ = _revision(client)
    assert after_permission["permission"] != baseline["permission"]
    assert after_permission["catalog"] == baseline["catalog"]

    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE library_sources SET display_name = 'Renamed local source' WHERE id = ?",
            (source_id,),
        )
        connection.commit()
    after_catalog, _ = _revision(client)
    assert after_catalog["catalog"] != after_permission["catalog"]
    assert after_catalog["permission"] == after_permission["permission"]


def test_presentation_trigger_ignores_unrelated_and_noop_settings(
    client,
    initialized_settings,
    admin_credentials,
):
    _login(client, **admin_credentials)
    baseline, _ = _revision(client)
    now = utcnow_iso()

    with get_connection(initialized_settings) as connection:
        connection.execute(
            "INSERT INTO user_settings (user_id, key, value, updated_at) VALUES (1, 'unrelated', 'x', ?)",
            (now,),
        )
        connection.commit()
    after_unrelated, _ = _revision(client)
    assert after_unrelated["presentation"] == baseline["presentation"]

    with get_connection(initialized_settings) as connection:
        connection.execute(
            """
            INSERT INTO user_settings (user_id, key, value, updated_at)
            VALUES (1, 'hide_recently_added', 'false', ?)
            """,
            (now,),
        )
        connection.commit()
    after_insert, _ = _revision(client)
    assert after_insert["presentation"] != after_unrelated["presentation"]

    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE user_settings SET updated_at = ? WHERE user_id = 1 AND key = 'hide_recently_added'",
            (utcnow_iso(),),
        )
        connection.commit()
    after_timestamp_only, _ = _revision(client)
    assert after_timestamp_only["presentation"] == after_insert["presentation"]

    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE user_settings SET value = 'true', updated_at = ? WHERE user_id = 1 AND key = 'hide_recently_added'",
            (utcnow_iso(),),
        )
        connection.commit()
    after_value, _ = _revision(client)
    assert after_value["presentation"] != after_timestamp_only["presentation"]


def test_user_scoped_row_move_invalidates_old_and_new_users(initialized_settings):
    now = utcnow_iso()
    with get_connection(initialized_settings) as connection:
        user_two_id = int(connection.execute(
            """
            INSERT INTO users (
                username, password_hash, role, enabled, age_credential, created_at, updated_at
            ) VALUES ('revision-scope-two', 'unused-test-hash', 'standard_user', 1, 18, ?, ?)
            """,
            (now, now),
        ).lastrowid)
        connection.execute(
            """
            INSERT INTO user_settings (user_id, key, value, updated_at)
            VALUES (1, 'hide_recently_added', 'false', ?)
            """,
            (now,),
        )
        connection.commit()

        def presentation_counter(user_id):
            row = connection.execute(
                """
                SELECT counter FROM library_revision_counters
                WHERE scope_kind = 'user' AND scope_id = ? AND layer = 'presentation'
                """,
                (user_id,),
            ).fetchone()
            return int(row["counter"]) if row else 0

        old_before = presentation_counter(1)
        new_before = presentation_counter(user_two_id)
        connection.execute(
            """
            UPDATE user_settings SET user_id = ?
            WHERE user_id = 1 AND key = 'hide_recently_added'
            """,
            (user_two_id,),
        )
        connection.commit()
        old_after = presentation_counter(1)
        new_after = presentation_counter(user_two_id)

    assert old_after - old_before == 1
    assert new_after - new_before == 1


def test_single_progress_save_writes_one_progress_revision(initialized_settings):
    with get_connection(initialized_settings) as connection:
        item_id = _insert_item(connection, suffix="single-progress")
        before_row = connection.execute(
            """
            SELECT counter FROM library_revision_counters
            WHERE scope_kind = 'user' AND scope_id = 1 AND layer = 'progress'
            """
        ).fetchone()
        before = int(before_row["counter"]) if before_row else 0
        connection.commit()

    save_progress(
        initialized_settings,
        user_id=1,
        media_item_id=item_id,
        position_seconds=30,
        duration_seconds=600,
        completed=False,
    )

    with get_connection(initialized_settings) as connection:
        after = int(connection.execute(
            """
            SELECT counter FROM library_revision_counters
            WHERE scope_kind = 'user' AND scope_id = 1 AND layer = 'progress'
            """
        ).fetchone()["counter"])
    assert after - before == 1


def test_progress_age_batch_query_count_is_constant_for_100_and_1000_items(initialized_settings):
    now = utcnow_iso()
    with get_connection(initialized_settings) as connection:
        connection.executemany(
            """
            INSERT INTO media_items (
                title, original_filename, file_path, file_size, file_mtime,
                year, created_at, updated_at, last_scanned_at
            ) VALUES (?, ?, ?, 1, 1, 2024, ?, ?, ?)
            """,
            [
                (f"Age Batch {index}", f"age-batch-{index}.mkv", f"/safe/age-batch-{index}.mkv", now, now, now)
                for index in range(1000)
            ],
        )
        connection.commit()
        item_ids = [
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM media_items WHERE title LIKE 'Age Batch %' ORDER BY id"
            ).fetchall()
        ]
        user = AuthenticatedUser(id=1, username="standard", role="standard_user", age_credential=18)

        def count_selects(ids):
            statements = []
            connection.set_trace_callback(statements.append)
            accessible = load_accessible_media_item_ids_by_age(connection, user=user, item_ids=ids)
            connection.set_trace_callback(None)
            return len([statement for statement in statements if statement.lstrip().upper().startswith(("SELECT", "WITH"))]), accessible

        count_100, accessible_100 = count_selects(item_ids[:100])
        count_1000, accessible_1000 = count_selects(item_ids)

    assert count_100 == 3
    assert count_1000 == 3
    assert accessible_100 == set(item_ids[:100])
    assert accessible_1000 == set(item_ids)


def test_progress_age_batch_preserves_manual_automatic_and_admin_semantics(initialized_settings):
    now = utcnow_iso()
    with get_connection(initialized_settings) as connection:
        automatic_id = _insert_item(connection, suffix="automatic-age")
        manual_id = _insert_item(connection, suffix="manual-age")
        unrestricted_id = _insert_item(connection, suffix="unrestricted-age")
        connection.execute("UPDATE media_items SET year = 2024 WHERE id IN (?, ?, ?)", (
            automatic_id, manual_id, unrestricted_id,
        ))
        automatic_row = connection.execute(
            "SELECT id, title, original_filename, year FROM media_items WHERE id = ?",
            (automatic_id,),
        ).fetchone()
        automatic_group = resolve_age_restriction_movie_group(automatic_row)
        manual_group_key = "age:manual:phase7b"
        connection.executemany(
            """
            INSERT INTO media_age_requirements (
                age_group_key, display_title, year, age_requirement, updated_at, updated_by_user_id
            ) VALUES (?, ?, 2024, 18, ?, 1)
            """,
            [
                (automatic_group.age_group_key, automatic_group.display_title, now),
                (manual_group_key, "Manual age group", now),
            ],
        )
        connection.execute(
            """
            INSERT INTO media_age_manual_group_links (
                age_group_key, media_item_id, created_by_user_id, created_at, note
            ) VALUES (?, ?, 1, ?, 'phase7b test')
            """,
            (manual_group_key, manual_id, now),
        )
        connection.commit()

        underage = AuthenticatedUser(
            id=1, username="standard", role="standard_user", age_credential=13,
        )
        admin = AuthenticatedUser(id=1, username="admin", role="admin", age_credential=1)
        item_ids = [automatic_id, manual_id, unrestricted_id]
        accessible_underage = load_accessible_media_item_ids_by_age(
            connection,
            user=underage,
            item_ids=item_ids,
        )
        accessible_admin = load_accessible_media_item_ids_by_age(
            connection,
            user=admin,
            item_ids=item_ids,
        )

    assert accessible_underage == {unrestricted_id}
    assert accessible_admin == set(item_ids)


def test_poster_reference_and_filesystem_fingerprint_bump_catalog_once(
    client,
    initialized_settings,
    admin_credentials,
    monkeypatch,
):
    _login(client, **admin_credentials)
    poster_root = Path(initialized_settings.media_root).parent / "revision-posters"
    poster_root.mkdir()

    baseline, _ = _revision(client)
    update_poster_reference_location(initialized_settings, value=str(poster_root))
    after_reference, _ = _revision(client)
    assert after_reference["catalog"] != baseline["catalog"]

    update_poster_reference_location(initialized_settings, value=str(poster_root))
    after_same_reference, _ = _revision(client)
    assert after_same_reference == after_reference

    invalidate_poster_indexes()
    first = get_poster_index_snapshot(poster_root, settings=initialized_settings)
    assert first is not None
    after_baseline_build, _ = _revision(client)
    assert after_baseline_build == after_same_reference

    poster_path = poster_root / "Revision Film (2024).jpg"
    poster_path.write_bytes(b"first-poster")
    second = get_poster_index_snapshot(poster_root, settings=initialized_settings)
    after_add, _ = _revision(client)
    assert second is not first
    assert after_add["catalog"] != after_baseline_build["catalog"]

    warm = get_poster_index_snapshot(poster_root, settings=initialized_settings)
    after_warm, _ = _revision(client)
    assert warm is second
    assert after_warm == after_add

    invalidate_poster_indexes(poster_root)
    rebuilt_after_restart = get_poster_index_snapshot(poster_root, settings=initialized_settings)
    after_restart_rebuild, _ = _revision(client)
    assert rebuilt_after_restart is not None
    assert after_restart_rebuild == after_add

    monkeypatch.setattr(
        "backend.app.services.poster_index_service.POSTER_INDEX_ENTRY_RECHECK_SECONDS",
        0.0,
    )
    original_stat = poster_path.stat()
    poster_path.write_bytes(b"other-poster")
    os.utime(poster_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    third = get_poster_index_snapshot(poster_root, settings=initialized_settings)
    after_replace, _ = _revision(client)
    assert third is not rebuilt_after_restart
    assert after_replace["catalog"] != after_add["catalog"]

    time.sleep(0.002)
    renamed_path = poster_root / "Revision Film Renamed (2024).jpg"
    poster_path.rename(renamed_path)
    fourth = get_poster_index_snapshot(poster_root, settings=initialized_settings)
    after_rename, _ = _revision(client)
    assert fourth is not third
    assert after_rename["catalog"] != after_replace["catalog"]

    time.sleep(0.002)
    renamed_path.unlink()
    fifth = get_poster_index_snapshot(poster_root, settings=initialized_settings)
    after_delete, _ = _revision(client)
    assert fifth is not fourth
    assert after_delete["catalog"] != after_rename["catalog"]

    serialized_revision = str(after_delete)
    assert str(poster_root) not in serialized_revision
    with get_connection(initialized_settings) as connection:
        stored = [dict(row) for row in connection.execute(
            "SELECT root_identity_hash, fingerprint_hash FROM poster_index_fingerprints"
        ).fetchall()]
    assert stored
    assert str(poster_root) not in str(stored)


def test_media_library_reference_change_bumps_catalog(
    client,
    initialized_settings,
    admin_credentials,
    monkeypatch,
):
    _login(client, **admin_credentials)
    replacement_root = Path(initialized_settings.media_root).parent / "revision-media"
    replacement_root.mkdir()
    monkeypatch.setattr(
        "backend.app.services.app_settings_service.scan_media_library",
        lambda *_args, **_kwargs: None,
    )
    baseline, _ = _revision(client)

    update_media_library_reference(initialized_settings, value=str(replacement_root))

    current, _ = _revision(client)
    assert current["catalog"] != baseline["catalog"]


def test_failed_poster_index_rebuild_does_not_publish_revision_or_raw_path(
    client,
    initialized_settings,
    admin_credentials,
    monkeypatch,
    caplog,
):
    _login(client, **admin_credentials)
    poster_root = Path(initialized_settings.media_root).parent / "failed-revision-posters"
    poster_root.mkdir()
    (poster_root / "Failure (2024).jpg").write_bytes(b"poster")
    invalidate_poster_indexes(poster_root)
    baseline, _ = _revision(client)

    def fail_build(*_args, **_kwargs):
        raise OSError("synthetic poster rebuild failure")

    monkeypatch.setattr(
        "backend.app.services.poster_index_service._build_poster_index_snapshot",
        fail_build,
    )
    with caplog.at_level("WARNING"):
        assert get_poster_index_snapshot(poster_root, settings=initialized_settings) is None

    current, _ = _revision(client)
    assert current == baseline
    assert str(poster_root) not in caplog.text


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
