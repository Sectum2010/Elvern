from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

from backend.app.db import get_connection, utcnow_iso
from backend.app.services.local_library_source_service import ensure_current_shared_local_source_binding
from backend.app.services.library_quality_rank_service import build_library_quality_rank
from backend.app.services.library_service import _library_summary_revision


CONTRACT_PATH = Path(__file__).parent / "fixtures" / "library_summary_v2_contract.json"


def _login(client, *, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _logout(client) -> None:
    response = client.post("/api/auth/logout")
    assert response.status_code == 200


def _create_standard_user(client, *, username: str, password: str) -> None:
    response = client.post(
        "/api/admin/users",
        json={
            "username": username,
            "password": password,
            "role": "standard_user",
            "enabled": True,
        },
    )
    assert response.status_code == 200


def _insert_cloud_source(settings, *, resource_id: str, is_shared: bool) -> int:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        cursor = connection.execute(
            """
            INSERT INTO library_sources (
                owner_user_id,
                provider,
                resource_type,
                resource_id,
                display_name,
                is_shared,
                created_at,
                updated_at
            ) VALUES (1, 'google_drive', 'folder', ?, ?, ?, ?, ?)
            """,
            (resource_id, resource_id, int(is_shared), now, now),
        )
        connection.commit()
        return int(cursor.lastrowid)


def _move_item_to_cloud(settings, *, item_id: int, source_id: int) -> None:
    with get_connection(settings) as connection:
        connection.execute(
            """
            UPDATE media_items
            SET source_kind = 'cloud',
                library_source_id = ?,
                external_media_id = ?,
                file_path = ?
            WHERE id = ?
            """,
            (source_id, f"external-v2-{item_id}", f"gdrive://{source_id}/{item_id}", item_id),
        )
        connection.commit()


def _insert_media_item(
    settings,
    *,
    title: str,
    original_filename: str,
    media_relative_path: str | None = None,
    series_key: str | None = None,
    series_name: str | None = None,
    scanned_at: str,
) -> int:
    media_path = Path(settings.media_root) / "Movies" / (media_relative_path or original_filename)
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"synthetic-v2-contract-media")
    now = utcnow_iso()
    with get_connection(settings) as connection:
        source_id = ensure_current_shared_local_source_binding(settings, connection=connection)
        cursor = connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                library_source_id,
                series_folder_key,
                series_folder_name,
                library_category,
                library_category_path,
                library_category_name,
                library_folder_role,
                library_folder_path,
                library_folder_name,
                file_size,
                file_mtime,
                duration_seconds,
                width,
                height,
                video_codec,
                audio_codec,
                container,
                year,
                created_at,
                updated_at,
                last_scanned_at
            ) VALUES (?, ?, ?, 'local', ?, ?, ?, 'movies', ?, 'Movies', ?, ?, ?, ?, ?, 7200, 3840, 2160, 'hevc', 'eac3', 'mkv', 2024, ?, ?, ?)
            """,
            (
                title,
                original_filename,
                str(media_path),
                source_id,
                series_key,
                series_name,
                str(media_path.parent),
                "list" if series_key else "movie",
                str(media_path.parent),
                series_name or "Movies",
                media_path.stat().st_size,
                media_path.stat().st_mtime,
                now,
                now,
                scanned_at,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def _insert_progress(settings, *, item_id: int) -> None:
    with get_connection(settings) as connection:
        connection.execute(
            """
            INSERT INTO playback_progress (
                user_id,
                media_item_id,
                position_seconds,
                duration_seconds,
                watch_seconds_total,
                completed,
                updated_at
            ) VALUES (1, ?, 120, 7200, 120, 0, ?)
            """,
            (item_id, utcnow_iso()),
        )
        connection.commit()


def _flatten_v1_ids(payload: dict[str, object]) -> set[int]:
    ids = {int(item["id"]) for item in payload["items"]}
    for rail_name in ("series_rails", "cloud_series_rails"):
        for rail in payload[rail_name]:
            ids.update(int(item["id"]) for item in rail["items"])
    for section_name in ("continue_watching", "recently_added"):
        ids.update(int(item["id"]) for item in payload[section_name])
    return ids


def _assert_v1_v2_membership_and_order(v1: dict[str, object], v2: dict[str, object]) -> None:
    assert v2["sections"]["item_ids"] == [item["id"] for item in v1["items"]]
    assert v2["sections"]["continue_watching_item_ids"] == [
        item["id"] for item in v1["continue_watching"]
    ]
    assert v2["sections"]["recently_added_item_ids"] == [
        item["id"] for item in v1["recently_added"]
    ]
    for rail_name in ("series_rails", "cloud_series_rails"):
        assert [
            (rail["key"], rail["title"], rail["film_count"], rail["item_ids"])
            for rail in v2["sections"][rail_name]
        ] == [
            (
                rail["key"],
                rail["title"],
                rail["film_count"],
                [item["id"] for item in rail["items"]],
            )
            for rail in v1[rail_name]
        ]
    assert set(map(int, v2["items_by_id"])) == _flatten_v1_ids(v1)
    assert v2["total_items"] == v1["total_items"]
    assert v2["available_genres"] == v1["available_genres"]
    assert v2["scan_in_progress"] == v1["scan_in_progress"]


def test_v2_summary_is_atomic_normalized_and_semantically_matches_v1(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    first_id = _insert_media_item(
        initialized_settings,
        title="Synthetic Saga One",
        original_filename="Synthetic.Saga.One.2024.2160p.WEB-DL.mkv",
        series_key="synthetic-saga",
        series_name="Synthetic Saga",
        scanned_at="2026-01-01T00:00:00+00:00",
    )
    second_id = _insert_media_item(
        initialized_settings,
        title="Synthetic Saga Two",
        original_filename="Synthetic.Saga.Two.2024.2160p.WEB-DL.mkv",
        series_key="synthetic-saga",
        series_name="Synthetic Saga",
        scanned_at="2026-01-02T00:00:00+00:00",
    )
    _insert_progress(initialized_settings, item_id=second_id)

    v1_response = client.get("/api/library", params={"category": "movies"})
    v2_response = client.get("/api/library/v2/summary", params={"category": "movies"})

    assert v1_response.status_code == 200
    assert v2_response.status_code == 200
    assert v2_response.headers["cache-control"] == "private, no-store"
    assert v2_response.headers["vary"] == "Cookie"
    v1 = v1_response.json()
    v2 = v2_response.json()
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(v2) == set(contract["top_level_fields"])
    assert v2["schema_version"] == contract["schema_version"]
    assert set(v2["sections"]) == set(contract["section_fields"])
    assert set(map(int, v2["items_by_id"])) == {first_id, second_id}
    assert set(map(int, v2["items_by_id"])) == _flatten_v1_ids(v1)
    assert v2["sections"]["item_ids"] == [item["id"] for item in v1["items"]]
    assert v2["sections"]["continue_watching_item_ids"] == [
        item["id"] for item in v1["continue_watching"]
    ]
    assert v2["sections"]["recently_added_item_ids"] == [
        item["id"] for item in v1["recently_added"]
    ]
    assert [rail["item_ids"] for rail in v2["sections"]["series_rails"]] == [
        [item["id"] for item in rail["items"]] for rail in v1["series_rails"]
    ]
    for item in v2["items_by_id"].values():
        assert set(item) == set(contract["item_fields"])
        assert not set(item).intersection(contract["forbidden_item_fields"])
    referenced_ids = set(v2["sections"]["item_ids"])
    referenced_ids.update(v2["sections"]["continue_watching_item_ids"])
    referenced_ids.update(v2["sections"]["recently_added_item_ids"])
    for rail_name in ("series_rails", "cloud_series_rails"):
        for rail in v2["sections"][rail_name]:
            referenced_ids.update(rail["item_ids"])
    assert referenced_ids == set(map(int, v2["items_by_id"]))
    assert v2["total_items"] == v1["total_items"]
    assert v2["available_genres"] == v1["available_genres"]
    assert v2["scan_in_progress"] == v1["scan_in_progress"]


def test_v2_summary_rejects_search_query_explicitly(
    client,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    response = client.get("/api/library/v2/summary", params={"q": "synthetic"})

    assert response.status_code == 400
    assert "search" in response.json()["detail"].lower()


def test_v2_summary_omits_sensitive_fields_for_admin_too(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _insert_media_item(
        initialized_settings,
        title="Private Synthetic",
        original_filename="Private.Synthetic.2024.REMUX.mkv",
        scanned_at="2026-01-01T00:00:00+00:00",
    )

    response = client.get("/api/library/v2/summary")

    assert response.status_code == 200
    assert "Private.Synthetic.2024.REMUX.mkv" not in response.text
    assert str(initialized_settings.media_root) not in response.text


def test_v2_summary_accepts_empty_q_and_reports_scan_state(
    client,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    client.app.state.scan_service._state["running"] = True

    response = client.get("/api/library/v2/summary", params={"q": "   "})

    assert response.status_code == 200
    assert response.json()["scan_in_progress"] is True


def test_v2_summary_backend_kill_switch_is_an_explicit_capability_error(
    client,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    original_settings = client.app.state.settings
    client.app.state.settings = replace(original_settings, library_summary_v2_enabled=False)
    try:
        response = client.get("/api/library/v2/summary")
    finally:
        client.app.state.settings = original_settings

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "library_summary_v2_disabled"


def test_v2_summary_matches_v1_item_order_for_every_supported_sort(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    item_ids = [
        _insert_media_item(
            initialized_settings,
            title=title,
            original_filename=f"{title}.2024.1080p.WEB-DL.mkv",
            scanned_at=scanned_at,
        )
        for title, scanned_at in (
            ("Beta", "2026-06-02T00:00:00+00:00"),
            ("Alpha", "2026-06-01T00:00:00+00:00"),
            ("Gamma", "2026-06-03T00:00:00+00:00"),
        )
    ]
    with get_connection(initialized_settings) as connection:
        for index, item_id in enumerate(item_ids, start=1):
            connection.execute(
                "UPDATE media_items SET year = ?, file_size = ? WHERE id = ?",
                (2000 + index, index * 100, item_id),
            )
        connection.commit()

    for sort in (
        "smart",
        "az",
        "za",
        "recent_desc",
        "recent_asc",
        "year_desc",
        "year_asc",
        "size_desc",
        "size_asc",
    ):
        v1_response = client.get("/api/library", params={"category": "movies", "sort": sort})
        v2_response = client.get("/api/library/v2/summary", params={"category": "movies", "sort": sort})

        assert v1_response.status_code == 200
        assert v2_response.status_code == 200
        _assert_v1_v2_membership_and_order(v1_response.json(), v2_response.json())


def test_v2_quality_rank_matches_each_roles_current_v1_visible_inputs(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _create_standard_user(client, username="summary-viewer", password="viewer-password")
    item_id = _insert_media_item(
        initialized_settings,
        title="Role Quality",
        original_filename="Role.Quality.2024.2160p.REMUX.Atmos.HEVC.mkv",
        scanned_at="2026-01-01T00:00:00+00:00",
    )

    admin_v1 = client.get("/api/library").json()
    admin_v2 = client.get("/api/library/v2/summary").json()
    admin_item = next(item for item in admin_v1["items"] if item["id"] == item_id)
    assert admin_v2["items_by_id"][str(item_id)]["quality_rank"] == build_library_quality_rank(admin_item)

    _logout(client)
    _login(client, username="summary-viewer", password="viewer-password")
    standard_v1 = client.get("/api/library").json()
    standard_v2_response = client.get("/api/library/v2/summary")
    assert standard_v2_response.status_code == 200
    standard_v2 = standard_v2_response.json()
    standard_item = next(item for item in standard_v1["items"] if item["id"] == item_id)
    assert standard_item["original_filename"] is None
    assert standard_v2["items_by_id"][str(item_id)]["quality_rank"] == build_library_quality_rank(
        standard_item
    )
    assert "Role.Quality.2024.2160p.REMUX.Atmos.HEVC.mkv" not in standard_v2_response.text
    assert str(initialized_settings.media_root) not in standard_v2_response.text


def test_v2_revision_is_stable_opaque_and_tracks_card_summary_truth() -> None:
    payload = {
        "schema_version": "library-summary-v2",
        "view": {"category": "movies", "source": "all", "genre": None, "quality": "all", "sort": "smart"},
        "items_by_id": {
            "1": {
                "id": 1,
                "title": "Sensitive Synthetic Title",
                "year": 2024,
                "poster_url": "/api/library/item/1/poster?v=token-one",
                "source_kind": "local",
                "quality_rank": {"key": "gold", "score": 11},
                "progress_seconds": 5,
                "completed": False,
            },
            "2": {"id": 2, "title": "Second", "poster_url": None},
        },
        "sections": {
            "item_ids": [1, 2],
            "series_rails": [],
            "cloud_series_rails": [],
            "continue_watching_item_ids": [1],
            "recently_added_item_ids": [2, 1],
        },
        "available_genres": ["Action"],
        "total_items": 2,
        "scan_in_progress": False,
    }
    revision = _library_summary_revision(payload)

    assert revision == _library_summary_revision(deepcopy(payload))
    assert len(revision) == 64
    assert "Sensitive" not in revision
    for mutate in (
        lambda value: value["sections"].update(item_ids=[2, 1]),
        lambda value: value["items_by_id"]["1"].update(progress_seconds=6),
        lambda value: value["items_by_id"]["1"].update(poster_url="/api/library/item/1/poster?v=token-two"),
        lambda value: value["items_by_id"]["1"].update(completed=True),
        lambda value: value.update(scan_in_progress=True),
    ):
        changed = deepcopy(payload)
        mutate(changed)
        assert _library_summary_revision(changed) != revision


def test_v2_source_visibility_and_membership_match_v1_for_admin_and_standard_user(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _create_standard_user(client, username="source-viewer", password="viewer-password")
    local_id = _insert_media_item(
        initialized_settings,
        title="Local Visible",
        original_filename="Local.Visible.2024.mkv",
        scanned_at="2026-01-01T00:00:00+00:00",
    )
    shared_cloud_id = _insert_media_item(
        initialized_settings,
        title="Shared Cloud Visible",
        original_filename="Shared.Cloud.Visible.2024.mkv",
        scanned_at="2026-01-02T00:00:00+00:00",
    )
    private_cloud_id = _insert_media_item(
        initialized_settings,
        title="Private Cloud Visible",
        original_filename="Private.Cloud.Visible.2024.mkv",
        scanned_at="2026-01-03T00:00:00+00:00",
    )
    shared_source_id = _insert_cloud_source(
        initialized_settings,
        resource_id="summary-shared-source",
        is_shared=True,
    )
    private_source_id = _insert_cloud_source(
        initialized_settings,
        resource_id="summary-private-source",
        is_shared=False,
    )
    _move_item_to_cloud(initialized_settings, item_id=shared_cloud_id, source_id=shared_source_id)
    _move_item_to_cloud(initialized_settings, item_id=private_cloud_id, source_id=private_source_id)

    for source, expected_ids in (
        ("all", {local_id, shared_cloud_id, private_cloud_id}),
        ("local", {local_id}),
        ("cloud", {shared_cloud_id, private_cloud_id}),
    ):
        v1 = client.get("/api/library", params={"source": source}).json()
        v2 = client.get("/api/library/v2/summary", params={"source": source}).json()
        _assert_v1_v2_membership_and_order(v1, v2)
        assert set(v2["sections"]["item_ids"]) == expected_ids

    _logout(client)
    _login(client, username="source-viewer", password="viewer-password")
    for source, expected_ids in (
        ("all", {local_id, shared_cloud_id}),
        ("local", {local_id}),
        ("cloud", {shared_cloud_id}),
    ):
        v1 = client.get("/api/library", params={"source": source}).json()
        v2 = client.get("/api/library/v2/summary", params={"source": source}).json()
        _assert_v1_v2_membership_and_order(v1, v2)
        assert set(v2["sections"]["item_ids"]) == expected_ids
        assert private_cloud_id not in set(map(int, v2["items_by_id"]))


def test_v2_hidden_global_hidden_and_duplicate_visibility_match_v1(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    visible_id = _insert_media_item(
        initialized_settings,
        title="Always Visible",
        original_filename="Always.Visible.2024.mkv",
        scanned_at="2026-01-01T00:00:00+00:00",
    )
    user_hidden_id = _insert_media_item(
        initialized_settings,
        title="User Hidden",
        original_filename="User.Hidden.2024.mkv",
        scanned_at="2026-01-02T00:00:00+00:00",
    )
    global_hidden_id = _insert_media_item(
        initialized_settings,
        title="Global Hidden",
        original_filename="Global.Hidden.2024.mkv",
        scanned_at="2026-01-03T00:00:00+00:00",
    )
    first_duplicate_id = _insert_media_item(
        initialized_settings,
        title="Duplicate Film",
        original_filename="Duplicate.Film.2024.mkv",
        media_relative_path="duplicate-a/Duplicate.Film.2024.mkv",
        scanned_at="2026-01-04T00:00:00+00:00",
    )
    second_duplicate_id = _insert_media_item(
        initialized_settings,
        title="Duplicate Film",
        original_filename="Duplicate.Film.2024.mkv",
        media_relative_path="duplicate-b/Duplicate.Film.2024.mkv",
        scanned_at="2026-01-05T00:00:00+00:00",
    )
    with get_connection(initialized_settings) as connection:
        now = utcnow_iso()
        connection.execute(
            "INSERT INTO user_hidden_media_items (user_id, media_item_id, hidden_at) VALUES (1, ?, ?)",
            (user_hidden_id, now),
        )
        connection.execute(
            """
            INSERT INTO global_hidden_media_items (media_item_id, hidden_by_user_id, hidden_at)
            VALUES (?, 1, ?)
            """,
            (global_hidden_id, now),
        )
        connection.commit()

    v1 = client.get("/api/library").json()
    v2 = client.get("/api/library/v2/summary").json()
    _assert_v1_v2_membership_and_order(v1, v2)
    visible_ids = set(v2["sections"]["item_ids"])
    assert visible_id in visible_ids
    assert user_hidden_id not in visible_ids
    assert global_hidden_id not in visible_ids
    assert len({first_duplicate_id, second_duplicate_id}.intersection(visible_ids)) == 1

    settings_response = client.patch("/api/user-settings", json={"hide_duplicate_movies": False})
    assert settings_response.status_code == 200
    v1_with_duplicates = client.get("/api/library").json()
    v2_with_duplicates = client.get("/api/library/v2/summary").json()
    _assert_v1_v2_membership_and_order(v1_with_duplicates, v2_with_duplicates)
    duplicate_visible_ids = set(v2_with_duplicates["sections"]["item_ids"])
    assert {first_duplicate_id, second_duplicate_id}.issubset(duplicate_visible_ids)
