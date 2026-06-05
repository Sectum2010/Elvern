from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.db import get_connection, utcnow_iso
from backend.app.media_scan import scan_media_library
from backend.app.services.local_library_source_service import ensure_current_shared_local_source_binding


def _login(client, *, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _insert_cloud_source(settings) -> int:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        cursor = connection.execute(
            """
            INSERT INTO library_sources (
                owner_user_id,
                provider,
                google_drive_account_id,
                resource_type,
                resource_id,
                display_name,
                is_shared,
                created_at,
                updated_at
            ) VALUES (1, 'google_drive', NULL, 'folder', ?, 'Cloud Movies', 1, ?, ?)
            """,
            ("cloud-category-source", now, now),
        )
        connection.commit()
        return int(cursor.lastrowid)


def _category_name(category: str | None) -> str | None:
    return {
        "movies": "Movies",
        "tv": "TV Shows",
        "anime": "Anime",
        "cartoon": "Cartoon",
    }.get(category or "")


def _insert_media_item(
    settings,
    *,
    title: str,
    original_filename: str,
    category: str | None = None,
    source_kind: str = "local",
    library_source_id: int | None = None,
    series_folder_key: str | None = None,
    series_folder_name: str | None = None,
    year: int | None = 2024,
    scanned_at: str | None = None,
) -> int:
    now = utcnow_iso()
    scanned_at = scanned_at or now
    category_path = str(Path(settings.media_root) / f"{_category_name(category) or 'Uncategorized'}") if category else None
    folder_path = str(Path(settings.media_root) / (series_folder_name or title))
    if source_kind == "local":
        media_path = Path(settings.media_root) / original_filename
        media_path.parent.mkdir(parents=True, exist_ok=True)
        media_path.write_bytes(b"test media")
        file_path = str(media_path)
        file_size = media_path.stat().st_size
        file_mtime = media_path.stat().st_mtime
    else:
        file_path = f"gdrive://{library_source_id}/{original_filename}"
        file_size = 1024
        file_mtime = 1704067200.0

    with get_connection(settings) as connection:
        if source_kind == "local" and library_source_id is None:
            library_source_id = ensure_current_shared_local_source_binding(settings, connection=connection)
        cursor = connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                library_source_id,
                external_media_id,
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1200.0, 1920, 1080, 'h264', 'aac', 'mkv', ?, ?, ?, ?)
            """,
            (
                title,
                original_filename,
                file_path,
                source_kind,
                library_source_id,
                f"external-{source_kind}-{original_filename}" if source_kind == "cloud" else None,
                series_folder_key,
                series_folder_name,
                category,
                category_path,
                _category_name(category),
                "list" if series_folder_key else ("category" if category else None),
                folder_path,
                series_folder_name or _category_name(category),
                file_size,
                file_mtime,
                year,
                now,
                now,
                scanned_at,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def _insert_progress(settings, *, media_item_id: int, updated_at: str = "2026-06-01T12:00:00+00:00") -> None:
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
            ) VALUES (1, ?, 300.0, 1200.0, 300.0, 0, ?)
            """,
            (media_item_id, updated_at),
        )
        connection.commit()


def test_library_category_default_movies_and_fallbacks(client, initialized_settings, admin_credentials) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    cloud_source_id = _insert_cloud_source(initialized_settings)
    movie_id = _insert_media_item(
        initialized_settings,
        title="Movie Alpha",
        original_filename="Movie.Alpha.2024.mkv",
        category="movies",
    )
    legacy_local_id = _insert_media_item(
        initialized_settings,
        title="Legacy Local",
        original_filename="Legacy.Local.2024.mkv",
    )
    legacy_cloud_id = _insert_media_item(
        initialized_settings,
        title="Legacy Cloud",
        original_filename="Legacy.Cloud.2024.mkv",
        source_kind="cloud",
        library_source_id=cloud_source_id,
    )
    anime_id = _insert_media_item(
        initialized_settings,
        title="One Piece Stampede",
        original_filename="One.Piece.Stampede.2019.mkv",
        category="anime",
    )

    default_response = client.get("/api/library")
    assert default_response.status_code == 200
    default_payload = default_response.json()
    assert {item["id"] for item in default_payload["items"]} == {movie_id, legacy_local_id, legacy_cloud_id}
    assert anime_id not in {item["id"] for item in default_payload["items"]}
    movie_item = next(item for item in default_payload["items"] if item["id"] == movie_id)
    assert movie_item["library_category"] == "movies"
    assert movie_item["library_category_name"] == "Movies"
    assert movie_item["library_folder_role"] == "category"

    explicit_movies = client.get("/api/library", params={"category": "movies"})
    assert explicit_movies.status_code == 200
    assert {item["id"] for item in explicit_movies.json()["items"]} == {movie_id, legacy_local_id, legacy_cloud_id}

    invalid_response = client.get("/api/library", params={"category": "genres"})
    assert invalid_response.status_code == 400
    assert "Invalid library category" in invalid_response.json()["detail"]

    invalid_search_response = client.get("/api/library/search", params={"q": "movie", "category": "genres"})
    assert invalid_search_response.status_code == 400
    assert "Invalid library category" in invalid_search_response.json()["detail"]


@pytest.mark.parametrize("category", ["tv", "anime", "cartoon"])
def test_non_movie_categories_exclude_uncategorized_fallbacks(
    client,
    initialized_settings,
    admin_credentials,
    category: str,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    cloud_source_id = _insert_cloud_source(initialized_settings)
    expected_id = _insert_media_item(
        initialized_settings,
        title=f"{category.title()} Alpha",
        original_filename=f"{category}.Alpha.2024.mkv",
        category=category,
    )
    _insert_media_item(
        initialized_settings,
        title="Legacy Local",
        original_filename=f"Legacy.Local.{category}.mkv",
    )
    _insert_media_item(
        initialized_settings,
        title="Legacy Cloud",
        original_filename=f"Legacy.Cloud.{category}.mkv",
        source_kind="cloud",
        library_source_id=cloud_source_id,
    )

    response = client.get("/api/library", params={"category": category})

    assert response.status_code == 200
    assert {item["id"] for item in response.json()["items"]} == {expected_id}


def test_library_search_is_category_scoped(client, initialized_settings, admin_credentials) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    movie_id = _insert_media_item(
        initialized_settings,
        title="One Piece Documentary",
        original_filename="One.Piece.Documentary.2024.mkv",
        category="movies",
    )
    anime_id = _insert_media_item(
        initialized_settings,
        title="One Piece Stampede",
        original_filename="One.Piece.Stampede.2019.mkv",
        category="anime",
    )

    movie_search = client.get("/api/library/search", params={"q": "one piece", "category": "movies"})
    assert movie_search.status_code == 200
    assert {item["id"] for item in movie_search.json()["items"]} == {movie_id}

    anime_search = client.get("/api/library/search", params={"q": "one piece", "category": "anime"})
    assert anime_search.status_code == 200
    assert {item["id"] for item in anime_search.json()["items"]} == {anime_id}


def test_category_scopes_series_rails_continue_and_recent(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    anime_a = _insert_media_item(
        initialized_settings,
        title="One Piece Film Red",
        original_filename="One.Piece.Film.Red.2022.mkv",
        category="anime",
        series_folder_key="series:one-piece",
        series_folder_name="One Piece",
        scanned_at="2026-06-01T10:00:00+00:00",
    )
    anime_b = _insert_media_item(
        initialized_settings,
        title="One Piece Stampede",
        original_filename="One.Piece.Stampede.2019.mkv",
        category="anime",
        series_folder_key="series:one-piece",
        series_folder_name="One Piece",
        scanned_at="2026-06-02T10:00:00+00:00",
    )
    movie_a = _insert_media_item(
        initialized_settings,
        title="Movie Saga One",
        original_filename="Movie.Saga.One.2020.mkv",
        category="movies",
        series_folder_key="series:movie-saga",
        series_folder_name="Movie Saga",
        scanned_at="2026-06-03T10:00:00+00:00",
    )
    movie_b = _insert_media_item(
        initialized_settings,
        title="Movie Saga Two",
        original_filename="Movie.Saga.Two.2021.mkv",
        category="movies",
        series_folder_key="series:movie-saga",
        series_folder_name="Movie Saga",
        scanned_at="2026-06-04T10:00:00+00:00",
    )
    _insert_progress(initialized_settings, media_item_id=anime_b, updated_at="2026-06-04T12:00:00+00:00")
    _insert_progress(initialized_settings, media_item_id=movie_b, updated_at="2026-06-05T12:00:00+00:00")

    anime_response = client.get("/api/library", params={"category": "anime"})
    assert anime_response.status_code == 200
    anime_payload = anime_response.json()
    assert {rail["title"] for rail in anime_payload["series_rails"]} == {"One Piece"}
    assert {item["id"] for item in anime_payload["series_rails"][0]["items"]} == {anime_a, anime_b}
    assert {item["id"] for item in anime_payload["continue_watching"]} == {anime_b}
    assert {item["id"] for item in anime_payload["recently_added"]} == {anime_a, anime_b}

    movies_response = client.get("/api/library", params={"category": "movies"})
    assert movies_response.status_code == 200
    movies_payload = movies_response.json()
    assert {rail["title"] for rail in movies_payload["series_rails"]} == {"Movie Saga"}
    assert {item["id"] for item in movies_payload["continue_watching"]} == {movie_b}
    assert all(item["id"] not in {anime_a, anime_b} for item in movies_payload["recently_added"])


def test_tv_category_scanned_nested_list_outputs_one_hannibal_rail(
    client,
    initialized_settings,
    admin_credentials,
    monkeypatch,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    media_root = Path(initialized_settings.media_root)
    list_folder = media_root / "TV Shows -TV" / "Hannibal (SE1~3) [1080p]-L"
    season_one = list_folder / "Hannibal Season 1"
    season_two = list_folder / "Hannibal Season 2"
    season_three = list_folder / "Hannibal Season 3"
    season_one.mkdir(parents=True)
    season_two.mkdir(parents=True)
    season_three.mkdir(parents=True)
    for path, content in (
        (season_one / "Hannibal S01E01.mkv", b"episode-one"),
        (season_two / "Hannibal S02E01.mkv", b"episode-two"),
        (season_three / "Hannibal S03E01.mkv", b"episode-three"),
    ):
        path.write_bytes(content)

    monkeypatch.setattr(
        "backend.app.media_scan.extract_media_metadata",
        lambda file_path, settings: {
            "duration_seconds": None,
            "width": 1920,
            "height": 1080,
            "video_codec": "h264",
            "audio_codec": "aac",
            "container": file_path.suffix.lower().lstrip(".") or None,
            "subtitles": [],
        },
    )
    scan_media_library(initialized_settings, reason="nested-list-api-test")

    response = client.get("/api/library", params={"category": "tv"})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["series_rails"]) == 1
    rail = payload["series_rails"][0]
    assert rail["key"] == "hannibal"
    assert rail["title"] == "Hannibal"
    assert rail["film_count"] == 3
    assert {item["original_filename"] for item in rail["items"]} == {
        "Hannibal S01E01.mkv",
        "Hannibal S02E01.mkv",
        "Hannibal S03E01.mkv",
    }


def test_series_rails_coalesce_duplicate_output_keys_within_category(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    ids = [
        _insert_media_item(
            initialized_settings,
            title="Hannibal S01E01",
            original_filename="Hannibal.S01E01.mkv",
            category="tv",
            series_folder_key="stale-season-one",
            series_folder_name="Hannibal Season 1",
            year=2013,
        ),
        _insert_media_item(
            initialized_settings,
            title="Hannibal S01E02",
            original_filename="Hannibal.S01E02.mkv",
            category="tv",
            series_folder_key="stale-season-one",
            series_folder_name="Hannibal Season 1",
            year=2013,
        ),
        _insert_media_item(
            initialized_settings,
            title="Hannibal S02E01",
            original_filename="Hannibal.S02E01.mkv",
            category="tv",
            series_folder_key="stale-season-two",
            series_folder_name="Hannibal Season 2",
            year=2014,
        ),
        _insert_media_item(
            initialized_settings,
            title="Hannibal S02E02",
            original_filename="Hannibal.S02E02.mkv",
            category="tv",
            series_folder_key="stale-season-two",
            series_folder_name="Hannibal Season 2",
            year=2014,
        ),
    ]

    response = client.get("/api/library", params={"category": "tv"})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["series_rails"]) == 1
    rail = payload["series_rails"][0]
    assert rail["key"] == "hannibal"
    assert rail["title"] == "Hannibal"
    assert rail["film_count"] == 4
    assert [item["id"] for item in rail["items"]] == ids


def test_category_filter_preserves_hidden_and_duplicate_visibility(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    cloud_source_id = _insert_cloud_source(initialized_settings)
    _insert_media_item(
        initialized_settings,
        title="Duplicate Toon",
        original_filename="Duplicate.Toon.2024.1080p.mkv",
        category="cartoon",
        source_kind="cloud",
        library_source_id=cloud_source_id,
        year=2024,
    )
    _insert_media_item(
        initialized_settings,
        title="Duplicate Toon",
        original_filename="Duplicate.Toon.2024.2160p.mkv",
        category="cartoon",
        source_kind="cloud",
        library_source_id=cloud_source_id,
        year=2024,
    )
    hidden_id = _insert_media_item(
        initialized_settings,
        title="Hidden Cartoon",
        original_filename="Hidden.Cartoon.2024.mkv",
        category="cartoon",
        year=2024,
    )
    with get_connection(initialized_settings) as connection:
        connection.execute(
            """
            INSERT INTO user_hidden_media_items (user_id, media_item_id, hidden_at)
            VALUES (1, ?, ?)
            """,
            (hidden_id, utcnow_iso()),
        )
        connection.commit()

    response = client.get("/api/library", params={"category": "cartoon"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_items"] == 1
    assert payload["items"][0]["title"] == "Duplicate Toon"
    assert payload["items"][0]["id"] != hidden_id
