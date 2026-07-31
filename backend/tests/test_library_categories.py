from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.db import get_connection, utcnow_iso
from backend.app.media_scan import scan_media_library
from backend.app.services.local_library_source_service import ensure_current_shared_local_source_binding
from backend.app.services.media_genre_service import COMMON_MOVIE_GENRES, resolve_genre_movie_group


LIBRARY_TEST_CATEGORIES = ("movies", "tv", "anime", "cartoon")


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
    file_size: int | None = None,
    width: int | None = 1920,
    height: int | None = 1080,
    video_codec: str | None = "h264",
    audio_codec: str | None = "aac",
    container: str | None = "mkv",
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
        file_size = media_path.stat().st_size if file_size is None else file_size
        file_mtime = media_path.stat().st_mtime
    else:
        file_path = f"gdrive://{library_source_id}/{original_filename}"
        file_size = 1024 if file_size is None else file_size
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1200.0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                width,
                height,
                video_codec,
                audio_codec,
                container,
                year,
                now,
                now,
                scanned_at,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def _set_genres(settings, *, media_item_id: int, genres: list[str]) -> None:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT id, title, original_filename, year
            FROM media_items
            WHERE id = ?
            LIMIT 1
            """,
            (media_item_id,),
        ).fetchone()
        assert row is not None
        group = resolve_genre_movie_group(row)
        connection.execute(
            """
            INSERT INTO media_genre_groups (
                genre_group_key,
                display_title,
                year,
                genres_json,
                updated_at,
                updated_by_user_id
            ) VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(genre_group_key) DO UPDATE SET
                display_title = excluded.display_title,
                year = excluded.year,
                genres_json = excluded.genres_json,
                updated_at = excluded.updated_at,
                updated_by_user_id = excluded.updated_by_user_id
            """,
            (
                group.genre_group_key,
                group.display_title,
                group.year,
                json.dumps(genres, ensure_ascii=True),
                now,
            ),
        )
        connection.commit()


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


@pytest.mark.parametrize("category", LIBRARY_TEST_CATEGORIES)
def test_library_source_filter_all_local_and_cloud_by_category(
    client,
    initialized_settings,
    admin_credentials,
    category: str,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    cloud_source_id = _insert_cloud_source(initialized_settings)
    other_category = next(value for value in LIBRARY_TEST_CATEGORIES if value != category)
    local_id = _insert_media_item(
        initialized_settings,
        title=f"{category.title()} Local Source",
        original_filename=f"{category}.Local.Source.2024.mkv",
        category=category,
        source_kind="local",
    )
    cloud_id = _insert_media_item(
        initialized_settings,
        title=f"{category.title()} Cloud Source",
        original_filename=f"{category}.Cloud.Source.2024.mkv",
        category=category,
        source_kind="cloud",
        library_source_id=cloud_source_id,
    )
    other_category_id = _insert_media_item(
        initialized_settings,
        title=f"{other_category.title()} Local Source",
        original_filename=f"{other_category}.Local.Source.2024.mkv",
        category=other_category,
        source_kind="local",
    )

    all_response = client.get("/api/library", params={"category": category, "source": "all"})
    local_response = client.get("/api/library", params={"category": category, "source": "local"})
    cloud_response = client.get("/api/library", params={"category": category, "source": "cloud"})

    assert all_response.status_code == 200
    assert {item["id"] for item in all_response.json()["items"]} == {local_id, cloud_id}
    assert other_category_id not in {item["id"] for item in all_response.json()["items"]}
    assert all_response.json()["arrange"]["source"] == "all"
    assert local_response.status_code == 200
    assert {item["id"] for item in local_response.json()["items"]} == {local_id}
    assert local_response.json()["arrange"]["source"] == "local"
    assert cloud_response.status_code == 200
    assert {item["id"] for item in cloud_response.json()["items"]} == {cloud_id}
    assert cloud_response.json()["arrange"]["source"] == "cloud"


@pytest.mark.parametrize("category", LIBRARY_TEST_CATEGORIES)
def test_library_search_respects_category_and_source_filter(
    client,
    initialized_settings,
    admin_credentials,
    category: str,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    cloud_source_id = _insert_cloud_source(initialized_settings)
    other_category = next(value for value in LIBRARY_TEST_CATEGORIES if value != category)
    local_id = _insert_media_item(
        initialized_settings,
        title=f"Needle {category.title()} Local",
        original_filename=f"Needle.{category}.Local.2024.mkv",
        category=category,
        source_kind="local",
    )
    cloud_id = _insert_media_item(
        initialized_settings,
        title=f"Needle {category.title()} Cloud",
        original_filename=f"Needle.{category}.Cloud.2024.mkv",
        category=category,
        source_kind="cloud",
        library_source_id=cloud_source_id,
    )
    other_category_id = _insert_media_item(
        initialized_settings,
        title=f"Needle {other_category.title()} Local",
        original_filename=f"Needle.{other_category}.Local.2024.mkv",
        category=other_category,
        source_kind="local",
    )

    all_response = client.get(
        "/api/library/search",
        params={"q": "needle", "category": category, "source": "all"},
    )
    local_response = client.get(
        "/api/library/search",
        params={"q": "needle", "category": category, "source": "local"},
    )
    cloud_response = client.get(
        "/api/library/search",
        params={"q": "needle", "category": category, "source": "cloud"},
    )

    assert all_response.status_code == 200
    assert {item["id"] for item in all_response.json()["items"]} == {local_id, cloud_id}
    assert other_category_id not in {item["id"] for item in all_response.json()["items"]}
    assert local_response.status_code == 200
    assert {item["id"] for item in local_response.json()["items"]} == {local_id}
    assert local_response.json()["arrange"]["source"] == "local"
    assert cloud_response.status_code == 200
    assert {item["id"] for item in cloud_response.json()["items"]} == {cloud_id}
    assert cloud_response.json()["arrange"]["source"] == "cloud"


def test_library_genre_filter_matches_membership_without_folder_inference(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    adventure_id = _insert_media_item(
        initialized_settings,
        title="River Quest",
        original_filename="River.Quest.2024.mkv",
        category="movies",
        series_folder_key="series:river",
        series_folder_name="River Quest",
    )
    drama_id = _insert_media_item(
        initialized_settings,
        title="Quiet Room",
        original_filename="Quiet.Room.2024.mkv",
        category="movies",
    )
    folder_named_id = _insert_media_item(
        initialized_settings,
        title="Folder Named Adventure",
        original_filename="Folder.Named.Adventure.2024.mkv",
        category="movies",
        series_folder_key="series:folder-adventure",
        series_folder_name="Adventure Shelf",
    )
    _set_genres(initialized_settings, media_item_id=adventure_id, genres=["Adventure", "Family"])
    _set_genres(initialized_settings, media_item_id=drama_id, genres=["Drama"])

    adventure_response = client.get("/api/library", params={"category": "movies", "genre": "Adventure"})
    family_response = client.get("/api/library", params={"category": "movies", "genre": "Family"})

    assert adventure_response.status_code == 200
    adventure_payload = adventure_response.json()
    assert {item["id"] for item in adventure_payload["items"]} == {adventure_id}
    assert folder_named_id not in {item["id"] for item in adventure_payload["items"]}
    assert adventure_payload["available_genres"] == COMMON_MOVIE_GENRES
    assert family_response.status_code == 200
    assert {item["id"] for item in family_response.json()["items"]} == {adventure_id}


def test_library_available_genres_include_presets_and_append_custom_genres(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    custom_id = _insert_media_item(
        initialized_settings,
        title="City Lights",
        original_filename="City.Lights.2024.mkv",
        category="movies",
    )

    no_metadata_response = client.get("/api/library", params={"category": "movies"})
    preset_zero_match_response = client.get("/api/library", params={"category": "movies", "genre": "Action"})
    _set_genres(initialized_settings, media_item_id=custom_id, genres=["Neo Noir"])
    custom_response = client.get("/api/library", params={"category": "movies"})

    assert no_metadata_response.status_code == 200
    assert no_metadata_response.json()["available_genres"] == COMMON_MOVIE_GENRES
    assert preset_zero_match_response.status_code == 200
    preset_zero_match_payload = preset_zero_match_response.json()
    assert preset_zero_match_payload["arrange"]["genre"] == "Action"
    assert preset_zero_match_payload["items"] == []
    assert preset_zero_match_payload["available_genres"] == COMMON_MOVIE_GENRES
    assert custom_response.status_code == 200
    assert custom_response.json()["available_genres"] == [*COMMON_MOVIE_GENRES, "Neo Noir"]


def test_library_quality_filter_uses_exact_existing_tier_inputs(client, initialized_settings, admin_credentials) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    diamond_id = _insert_media_item(
        initialized_settings,
        title="Reference Copy",
        original_filename="Reference.Copy.2024.2160p.UHD.REMUX.x265.TrueHD.Atmos.mkv",
        category="movies",
        file_size=90 * 1024**3,
        width=3840,
        height=2160,
        video_codec="hevc",
        audio_codec="truehd atmos",
    )
    gold_id = _insert_media_item(
        initialized_settings,
        title="Gold Copy",
        original_filename="Gold.Copy.2024.1080p.WEB-DL.x265.DTS.mkv",
        category="movies",
        file_size=25 * 1024**3,
        width=1920,
        height=1080,
        video_codec="hevc",
        audio_codec="dts",
    )
    wood_id = _insert_media_item(
        initialized_settings,
        title="Tiny Copy",
        original_filename="Tiny.Copy.2024.mkv",
        category="movies",
        file_size=256 * 1024**2,
        width=640,
        height=360,
        video_codec="h264",
        audio_codec="aac",
    )

    response = client.get("/api/library", params={"category": "movies", "quality": "diamond"})
    gold_response = client.get("/api/library", params={"category": "movies", "quality": "gold"})
    wood_response = client.get("/api/library", params={"category": "movies", "quality": "wood"})
    all_quality_response = client.get("/api/library", params={"category": "movies", "quality": "all"})

    assert response.status_code == 200
    payload = response.json()
    assert {item["id"] for item in payload["items"]} == {diamond_id}
    assert payload["items"][0]["quality_tier"] == "diamond"
    assert gold_id not in {item["id"] for item in payload["items"]}
    assert gold_response.status_code == 200
    assert {item["id"] for item in gold_response.json()["items"]} == {gold_id}
    assert gold_response.json()["items"][0]["quality_tier"] == "gold"
    assert wood_response.status_code == 200
    assert {item["id"] for item in wood_response.json()["items"]} == {wood_id}
    assert wood_response.json()["items"][0]["quality_tier"] == "wood"
    assert all_quality_response.status_code == 200
    assert {item["id"] for item in all_quality_response.json()["items"]} == {diamond_id, gold_id, wood_id}


def test_library_sort_modes(client, initialized_settings, admin_credentials) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    beta_id = _insert_media_item(
        initialized_settings,
        title="Beta",
        original_filename="Beta.2002.mkv",
        category="movies",
        year=2002,
        scanned_at="2026-06-02T00:00:00+00:00",
        file_size=20,
    )
    alpha_id = _insert_media_item(
        initialized_settings,
        title="Alpha",
        original_filename="Alpha.2001.mkv",
        category="movies",
        year=2001,
        scanned_at="2026-06-01T00:00:00+00:00",
        file_size=10,
    )
    gamma_id = _insert_media_item(
        initialized_settings,
        title="Gamma",
        original_filename="Gamma.2003.mkv",
        category="movies",
        year=2003,
        scanned_at="2026-06-03T00:00:00+00:00",
        file_size=30,
    )

    expectations = {
        "az": [alpha_id, beta_id, gamma_id],
        "za": [gamma_id, beta_id, alpha_id],
        "recent_desc": [gamma_id, beta_id, alpha_id],
        "recent_asc": [alpha_id, beta_id, gamma_id],
        "year_desc": [gamma_id, beta_id, alpha_id],
        "year_asc": [alpha_id, beta_id, gamma_id],
        "size_desc": [gamma_id, beta_id, alpha_id],
        "size_asc": [alpha_id, beta_id, gamma_id],
    }
    for sort, expected_ids in expectations.items():
        response = client.get("/api/library", params={"category": "movies", "sort": sort})

        assert response.status_code == 200
        assert [item["id"] for item in response.json()["items"]] == expected_ids


@pytest.mark.parametrize("category", LIBRARY_TEST_CATEGORIES)
def test_library_arrange_filters_compose_before_sorting(
    client,
    initialized_settings,
    admin_credentials,
    category: str,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    cloud_source_id = _insert_cloud_source(initialized_settings)
    beta_id = _insert_media_item(
        initialized_settings,
        title=f"Beta {category.title()} Match",
        original_filename=f"Beta.{category}.Match.2024.1080p.WEB-DL.x265.DTS.mkv",
        category=category,
        source_kind="cloud",
        library_source_id=cloud_source_id,
        file_size=25 * 1024**3,
        width=1920,
        height=1080,
        video_codec="hevc",
        audio_codec="dts",
    )
    alpha_id = _insert_media_item(
        initialized_settings,
        title=f"Alpha {category.title()} Match",
        original_filename=f"Alpha.{category}.Match.2024.1080p.WEB-DL.x265.DTS.mkv",
        category=category,
        source_kind="cloud",
        library_source_id=cloud_source_id,
        file_size=25 * 1024**3,
        width=1920,
        height=1080,
        video_codec="hevc",
        audio_codec="dts",
    )
    local_id = _insert_media_item(
        initialized_settings,
        title=f"Local {category.title()} Match",
        original_filename=f"Local.{category}.Match.2024.1080p.WEB-DL.x265.DTS.mkv",
        category=category,
        source_kind="local",
        file_size=25 * 1024**3,
        width=1920,
        height=1080,
        video_codec="hevc",
        audio_codec="dts",
    )
    wood_id = _insert_media_item(
        initialized_settings,
        title=f"Wood {category.title()} Match",
        original_filename=f"Wood.{category}.Match.2024.mkv",
        category=category,
        source_kind="cloud",
        library_source_id=cloud_source_id,
        file_size=256 * 1024**2,
        width=640,
        height=360,
        video_codec="h264",
        audio_codec="aac",
    )
    drama_id = _insert_media_item(
        initialized_settings,
        title=f"Drama {category.title()} Match",
        original_filename=f"Drama.{category}.Match.2024.1080p.WEB-DL.x265.DTS.mkv",
        category=category,
        source_kind="cloud",
        library_source_id=cloud_source_id,
        file_size=25 * 1024**3,
        width=1920,
        height=1080,
        video_codec="hevc",
        audio_codec="dts",
    )
    _set_genres(initialized_settings, media_item_id=beta_id, genres=["Action"])
    _set_genres(initialized_settings, media_item_id=alpha_id, genres=["Action"])
    _set_genres(initialized_settings, media_item_id=local_id, genres=["Action"])
    _set_genres(initialized_settings, media_item_id=wood_id, genres=["Action"])
    _set_genres(initialized_settings, media_item_id=drama_id, genres=["Drama"])

    response = client.get(
        "/api/library",
        params={
            "category": category,
            "source": "cloud",
            "genre": "Action",
            "quality": "gold",
            "sort": "az",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload["items"]] == [alpha_id, beta_id]
    assert payload["arrange"] == {
        "source": "cloud",
        "genres": ["Action"],
        "qualities": ["gold"],
        "genre": "Action",
        "quality": "gold",
        "sort": "az",
    }


def test_library_multi_filters_use_or_within_groups_and_and_across_groups(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    cloud_source_id = _insert_cloud_source(initialized_settings)
    action_diamond_id = _insert_media_item(
        initialized_settings,
        title="Multi Probe Action Diamond",
        original_filename="Multi.Probe.Action.Diamond.2026.2160p.UHD.REMUX.x265.TrueHD.Atmos.mkv",
        category="movies",
        source_kind="cloud",
        library_source_id=cloud_source_id,
        file_size=90 * 1024**3,
        width=3840,
        height=2160,
        video_codec="hevc",
        audio_codec="truehd atmos",
    )
    drama_gold_id = _insert_media_item(
        initialized_settings,
        title="Multi Probe Drama Gold",
        original_filename="Multi.Probe.Drama.Gold.2026.1080p.WEB-DL.x265.DTS.mkv",
        category="movies",
        source_kind="cloud",
        library_source_id=cloud_source_id,
        file_size=25 * 1024**3,
        width=1920,
        height=1080,
        video_codec="hevc",
        audio_codec="dts",
    )
    action_wood_id = _insert_media_item(
        initialized_settings,
        title="Multi Probe Action Wood",
        original_filename="Multi.Probe.Action.Wood.2026.mkv",
        category="movies",
        source_kind="cloud",
        library_source_id=cloud_source_id,
        file_size=256 * 1024**2,
        width=640,
        height=360,
        video_codec="h264",
        audio_codec="aac",
    )
    comedy_diamond_id = _insert_media_item(
        initialized_settings,
        title="Multi Probe Comedy Diamond",
        original_filename="Multi.Probe.Comedy.Diamond.2026.2160p.UHD.REMUX.x265.TrueHD.Atmos.mkv",
        category="movies",
        source_kind="cloud",
        library_source_id=cloud_source_id,
        file_size=90 * 1024**3,
        width=3840,
        height=2160,
        video_codec="hevc",
        audio_codec="truehd atmos",
    )
    local_action_diamond_id = _insert_media_item(
        initialized_settings,
        title="Multi Probe Local Action Diamond",
        original_filename="Multi.Probe.Local.Action.Diamond.2026.2160p.UHD.REMUX.x265.TrueHD.Atmos.mkv",
        category="movies",
        file_size=90 * 1024**3,
        width=3840,
        height=2160,
        video_codec="hevc",
        audio_codec="truehd atmos",
    )
    _set_genres(
        initialized_settings,
        media_item_id=action_diamond_id,
        genres=["Action"],
    )
    _set_genres(
        initialized_settings,
        media_item_id=drama_gold_id,
        genres=["Drama"],
    )
    _set_genres(
        initialized_settings,
        media_item_id=action_wood_id,
        genres=["Action"],
    )
    _set_genres(
        initialized_settings,
        media_item_id=comedy_diamond_id,
        genres=["Comedy"],
    )
    _set_genres(
        initialized_settings,
        media_item_id=local_action_diamond_id,
        genres=["Action"],
    )
    common_params = [
        ("category", "movies"),
        ("source", "cloud"),
        ("genre", "Drama"),
        ("genre", "action"),
        ("quality", "gold"),
        ("quality", "diamond"),
        ("sort", "az"),
    ]

    v1_response = client.get("/api/library", params=common_params)
    search_response = client.get(
        "/api/library/search",
        params=[("q", "Multi Probe"), *common_params],
    )
    v2_response = client.get("/api/library/v2/summary", params=common_params)

    assert v1_response.status_code == 200
    assert search_response.status_code == 200
    assert v2_response.status_code == 200
    expected_ids = [action_diamond_id, drama_gold_id]
    assert [item["id"] for item in v1_response.json()["items"]] == expected_ids
    assert [item["id"] for item in search_response.json()["items"]] == expected_ids
    assert v2_response.json()["sections"]["item_ids"] == expected_ids
    expected_arrange = {
        "source": "cloud",
        "genres": ["action", "Drama"],
        "qualities": ["diamond", "gold"],
        "genre": None,
        "quality": None,
        "sort": "az",
    }
    assert v1_response.json()["arrange"] == expected_arrange
    assert search_response.json()["arrange"] == expected_arrange
    assert v2_response.json()["view"] == {
        "category": "movies",
        **expected_arrange,
    }
    returned_ids = set(expected_ids)
    assert action_wood_id not in returned_ids
    assert comedy_diamond_id not in returned_ids
    assert local_action_diamond_id not in returned_ids

    invalid_response = client.get(
        "/api/library",
        params=[("quality", "diamond"), ("quality", "platinum")],
    )
    assert invalid_response.status_code == 400
    assert "Invalid library quality" in invalid_response.json()["detail"]


@pytest.mark.parametrize(
    ("param_name", "param_value", "message"),
    [
        ("source", "tape", "Invalid library source"),
        ("quality", "platinum", "Invalid library quality"),
        ("sort", "runtime_desc", "Invalid library sort"),
    ],
)
def test_library_arrange_invalid_params_return_400(
    client,
    initialized_settings,
    admin_credentials,
    param_name: str,
    param_value: str,
    message: str,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    response = client.get("/api/library", params={param_name: param_value})

    assert response.status_code == 400
    assert message in response.json()["detail"]


@pytest.mark.parametrize(
    ("path", "extra_params"),
    [
        ("/api/library", []),
        ("/api/library/search", [("q", "probe")]),
        ("/api/library/v2/summary", []),
    ],
)
def test_library_genre_filter_limits_are_consistent_across_endpoints(
    client,
    initialized_settings,
    admin_credentials,
    path: str,
    extra_params: list[tuple[str, str]],
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    allowed = [("genre", f"Custom Genre {index}") for index in range(64)]

    assert client.get(path, params=[*extra_params, *allowed]).status_code == 200

    duplicate_case = [
        *allowed,
        ("genre", "custom genre 0"),
        ("genre", "CUSTOM GENRE 0"),
    ]
    assert client.get(path, params=[*extra_params, *duplicate_case]).status_code == 200

    too_many = [*allowed, ("genre", "One Genre Too Many")]
    too_many_response = client.get(path, params=[*extra_params, *too_many])
    assert too_many_response.status_code == 400
    assert too_many_response.json()["detail"] == (
        "A maximum of 64 unique library genre filters is allowed."
    )

    long_value = "SensitiveGenreValue" + ("x" * 120)
    long_response = client.get(
        path,
        params=[*extra_params, ("genre", long_value)],
    )
    assert long_response.status_code == 400
    assert long_response.json()["detail"] == (
        "Library genre filters must be 128 characters or shorter."
    )
    assert long_value not in long_response.text
