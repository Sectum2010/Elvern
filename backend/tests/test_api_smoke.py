from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.main as main_module
from backend.app.auth import ensure_admin_user
from backend.app.config import get_settings, refresh_settings
from backend.app.db import get_connection, utcnow_iso
from backend.app.db import init_db
from backend.app.media_scan import scan_media_library
from backend.app.security import hash_password
from backend.app.services import cloud_source_sync_service
from backend.app.services.desktop_playback_service import resolve_same_host_request
from backend.app.services.local_library_source_service import ensure_current_shared_local_source_binding
from backend.app.services.library_movie_identity_service import _row_hidden_movie_key
from backend.tests.conftest import (
    DummyAdminEventHub,
    DummyMobilePlaybackManager,
    DummyScanService,
    DummyTranscodeManager,
)


def _login(client, *, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _logout(client) -> None:
    response = client.post("/api/auth/logout")
    assert response.status_code == 200


def _create_standard_user_via_admin(client, *, username: str, password: str) -> None:
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


def _insert_google_drive_source(settings) -> int:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        account_cursor = connection.execute(
            """
            INSERT INTO google_drive_accounts (
                user_id,
                google_account_id,
                email,
                display_name,
                refresh_token,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (1, "google-account-1", "admin@example.com", "Admin", "refresh-token", now, now),
        )
        source_cursor = connection.execute(
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
            ) VALUES (?, 'google_drive', ?, 'folder', ?, ?, 1, ?, ?)
            """,
            (1, int(account_cursor.lastrowid), "folder-123", "Movies", now, now),
        )
        connection.commit()
        return int(source_cursor.lastrowid)


def _build_fake_google_drive_video_row(*, file_id: str, name: str) -> dict[str, object]:
    return {
        "id": file_id,
        "name": name,
        "mimeType": "video/mp4",
        "size": "1048576",
        "modifiedTime": "2024-01-01T00:00:00Z",
        "videoMediaMetadata": {
            "width": 1920,
            "height": 1080,
            "durationMillis": "1000",
        },
    }


def _sync_fake_google_drive_source(monkeypatch, settings, *, source_id: int, rows: list[dict[str, object]]) -> None:
    monkeypatch.setattr(cloud_source_sync_service, "google_drive_enabled", lambda _settings: True)
    monkeypatch.setattr(
        cloud_source_sync_service,
        "fetch_drive_resource_metadata",
        lambda *args, **kwargs: {"resource_id": "folder-123", "display_name": "Movies"},
    )
    monkeypatch.setattr(
        cloud_source_sync_service,
        "list_drive_media_files",
        lambda *args, **kwargs: rows,
    )
    cloud_source_sync_service._sync_google_drive_library_source(
        settings,
        source_id=source_id,
        raise_on_error=True,
        provider="google_drive",
        get_access_token_by_account_id=lambda *args, **kwargs: "test-access-token",
    )


def test_health_endpoint_smoke(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": "Elvern"}


def test_auth_login_me_logout_smoke(client, admin_credentials) -> None:
    unauthenticated = client.get("/api/auth/me")
    assert unauthenticated.status_code == 401

    login_response = client.post("/api/auth/login", json=admin_credentials)
    assert login_response.status_code == 200
    assert login_response.cookies
    assert login_response.json()["user"]["username"] == admin_credentials["username"]
    assert login_response.json()["user"]["session_id"] is None

    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["user"]["username"] == admin_credentials["username"]
    assert me_response.json()["user"]["role"] == "admin"
    assert me_response.json()["user"]["session_id"] is not None

    logout_response = client.post("/api/auth/logout")
    assert logout_response.status_code == 200
    assert logout_response.json() == {"message": "Logged out"}

    after_logout = client.get("/api/auth/me")
    assert after_logout.status_code == 401


def test_test_settings_fixture_clears_live_auth_hash_linux_root_and_origin_overrides(monkeypatch, request) -> None:
    monkeypatch.setenv("ELVERN_ADMIN_PASSWORD_HASH", hash_password("wrong-live-password"))
    monkeypatch.setenv("ELVERN_LIBRARY_ROOT_LINUX", "/home/sectum/Videos/Movies")
    monkeypatch.setenv("ELVERN_PUBLIC_APP_ORIGIN", "https://spark-e245.taila5aa7b.ts.net")
    monkeypatch.setenv("ELVERN_BACKEND_ORIGIN", "https://spark-e245.taila5aa7b.ts.net")

    initialized_settings = request.getfixturevalue("initialized_settings")
    client = request.getfixturevalue("client")
    admin_credentials = request.getfixturevalue("admin_credentials")

    assert initialized_settings.admin_password_hash is None
    assert initialized_settings.library_root_linux == str(initialized_settings.media_root)
    assert initialized_settings.public_app_origin == ""
    assert initialized_settings.backend_origin == ""

    response = client.post("/api/auth/login", json=admin_credentials)
    assert response.status_code == 200
    assert response.json()["user"]["username"] == admin_credentials["username"]


def test_app_startup_refreshes_settings_instead_of_using_stale_cached_settings(monkeypatch, tmp_path) -> None:
    media_root_a = tmp_path / "media-a"
    media_root_a.mkdir()
    db_path_a = tmp_path / "backend" / "data" / "a.db"
    helper_releases_dir = tmp_path / "backend" / "data" / "helper_releases"
    transcode_dir = tmp_path / "backend" / "data" / "transcodes"

    monkeypatch.setenv("ELVERN_MEDIA_ROOT", str(media_root_a))
    monkeypatch.setenv("ELVERN_DB_PATH", str(db_path_a))
    monkeypatch.setenv("ELVERN_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ELVERN_ADMIN_BOOTSTRAP_PASSWORD", "password-a-123456789012345678901234")
    monkeypatch.setenv("ELVERN_SESSION_SECRET", "session-secret-a-12345678901234567890")
    monkeypatch.setenv("ELVERN_COOKIE_SECURE", "false")
    monkeypatch.setenv("ELVERN_SCAN_ON_STARTUP", "false")
    monkeypatch.setenv("ELVERN_TRANSCODE_ENABLED", "false")
    monkeypatch.setenv("ELVERN_BROWSER_PLAYBACK_ROUTE2_ENABLED", "false")
    monkeypatch.setenv("ELVERN_HELPER_RELEASES_DIR", str(helper_releases_dir))
    monkeypatch.setenv("ELVERN_TRANSCODE_DIR", str(transcode_dir))
    settings_a = refresh_settings()
    init_db(settings_a)
    ensure_admin_user(settings_a)

    media_root_b = tmp_path / "media-b"
    media_root_b.mkdir()
    db_path_b = tmp_path / "backend" / "data" / "b.db"
    monkeypatch.setenv("ELVERN_MEDIA_ROOT", str(media_root_b))
    monkeypatch.setenv("ELVERN_DB_PATH", str(db_path_b))
    monkeypatch.setenv("ELVERN_ADMIN_BOOTSTRAP_PASSWORD", "password-b-123456789012345678901234")
    monkeypatch.setenv("ELVERN_SESSION_SECRET", "session-secret-b-12345678901234567890")
    settings_b = refresh_settings()
    init_db(settings_b)
    ensure_admin_user(settings_b)

    monkeypatch.setenv("ELVERN_MEDIA_ROOT", str(media_root_a))
    monkeypatch.setenv("ELVERN_DB_PATH", str(db_path_a))
    monkeypatch.setenv("ELVERN_ADMIN_BOOTSTRAP_PASSWORD", "password-a-123456789012345678901234")
    monkeypatch.setenv("ELVERN_SESSION_SECRET", "session-secret-a-12345678901234567890")
    stale_settings = refresh_settings()
    assert stale_settings.media_root == media_root_a.resolve()
    assert get_settings().media_root == media_root_a.resolve()

    monkeypatch.setenv("ELVERN_MEDIA_ROOT", str(media_root_b))
    monkeypatch.setenv("ELVERN_DB_PATH", str(db_path_b))
    monkeypatch.setenv("ELVERN_ADMIN_BOOTSTRAP_PASSWORD", "password-b-123456789012345678901234")
    monkeypatch.setenv("ELVERN_SESSION_SECRET", "session-secret-b-12345678901234567890")

    importlib.reload(main_module)
    monkeypatch.setattr(main_module, "ScanService", DummyScanService)
    monkeypatch.setattr(main_module, "TranscodeManager", DummyTranscodeManager)
    monkeypatch.setattr(main_module, "MobilePlaybackManager", DummyMobilePlaybackManager)
    monkeypatch.setattr(main_module, "admin_event_hub", DummyAdminEventHub())

    with TestClient(main_module.app) as client:
        assert client.app.state.settings.media_root == media_root_b.resolve()
        assert client.app.state.settings.db_path == db_path_b
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password-b-123456789012345678901234"},
        )
        assert response.status_code == 200
        assert response.json()["user"]["username"] == "admin"


def test_library_payload_uses_backend_parsed_title_for_garbage_filename(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    now = utcnow_iso()
    with get_connection(initialized_settings) as connection:
        shared_source_id = ensure_current_shared_local_source_binding(
            initialized_settings,
            connection=connection,
        )
        connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                library_source_id,
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
            ) VALUES (?, ?, ?, 'local', ?, ?, ?, NULL, NULL, NULL, NULL, NULL, 'mkv', NULL, ?, ?, ?)
            """,
            (
                "One Piece Stampede () [tmdbid-568012] - [Remux-1080p][TrueHD]",
                "One Piece Stampede () [tmdbid-568012] - [Remux-1080p][TrueHD].mkv",
                str(Path(initialized_settings.media_root) / "One Piece Stampede () [tmdbid-568012] - [Remux-1080p][TrueHD].mkv"),
                int(shared_source_id),
                1,
                1.0,
                now,
                now,
                now,
            ),
        )
        connection.commit()

    response = client.get("/api/library")
    assert response.status_code == 200
    assert response.json()["total_items"] == 1
    item = response.json()["items"][0]
    assert item["title"] == "One Piece Stampede"
    assert item["parsed_title"]["display_title"] == "One Piece Stampede"
    assert item["parsed_title"]["base_title"] == "One Piece Stampede"
    assert item["parsed_title"]["edition_identity"] == "standard"
    assert item["parsed_title"]["parsed_year"] is None


def test_library_payload_preserves_meaningful_title_numbers_and_parts(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    now = utcnow_iso()
    harry_part_1_filename = "Harry.Potter.and.the.Deathly.Hallows.Part.1.2010.4K.UHD.2160p.REMUX.DV.DTS-HD.MA.7.1.Dual.PTBR-BrRemux.mkv"
    harry_part_2_filename = "Harry.Potter.and.the.Deathly.Hallows.Part.2.2011.4K.UHD.2160p.REMUX.DV.DTS-HD.MA.7.1.Dual.PTBR-BrRemux.mkv"
    blade_filename = "Blade Runner 2049.2017.2160p.UHD.BluRay.REMUX.mkv"
    with get_connection(initialized_settings) as connection:
        shared_source_id = ensure_current_shared_local_source_binding(
            initialized_settings,
            connection=connection,
        )
        connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                library_source_id,
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
            ) VALUES (?, ?, ?, 'local', ?, ?, ?, NULL, NULL, NULL, NULL, NULL, 'mkv', ?, ?, ?, ?)
            """,
            (
                "Harry Potter and the Deathly Hallows Part",
                harry_part_1_filename,
                str(Path(initialized_settings.media_root) / harry_part_1_filename),
                int(shared_source_id),
                1,
                1.0,
                2010,
                now,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                library_source_id,
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
            ) VALUES (?, ?, ?, 'local', ?, ?, ?, NULL, NULL, NULL, NULL, NULL, 'mkv', ?, ?, ?, ?)
            """,
            (
                "Harry Potter and the Deathly Hallows Part",
                harry_part_2_filename,
                str(Path(initialized_settings.media_root) / harry_part_2_filename),
                int(shared_source_id),
                1,
                1.0,
                2011,
                now,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                library_source_id,
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
            ) VALUES (?, ?, ?, 'local', ?, ?, ?, NULL, NULL, NULL, NULL, NULL, 'mkv', NULL, ?, ?, ?)
            """,
            (
                "Blade Runner 2049.2017.2160p.UHD.BluRay.REMUX",
                blade_filename,
                str(Path(initialized_settings.media_root) / blade_filename),
                int(shared_source_id),
                1,
                1.0,
                now,
                now,
                now,
            ),
        )
        connection.commit()

    response = client.get("/api/library")
    assert response.status_code == 200
    items_by_filename = {
        item["original_filename"]: item
        for item in response.json()["items"]
    }
    assert items_by_filename[harry_part_1_filename]["title"] == "Harry Potter and the Deathly Hallows Part 1"
    assert items_by_filename[harry_part_1_filename]["parsed_title"]["display_title"] == "Harry Potter and the Deathly Hallows Part 1"
    assert items_by_filename[harry_part_1_filename]["parsed_title"]["title_source"] == "original_filename"
    assert items_by_filename[harry_part_2_filename]["title"] == "Harry Potter and the Deathly Hallows Part 2"
    assert items_by_filename[harry_part_2_filename]["parsed_title"]["display_title"] == "Harry Potter and the Deathly Hallows Part 2"
    assert items_by_filename[harry_part_2_filename]["parsed_title"]["title_source"] == "original_filename"
    assert items_by_filename[blade_filename]["title"] == "Blade Runner 2049"
    assert items_by_filename[blade_filename]["parsed_title"]["display_title"] == "Blade Runner 2049"
    assert items_by_filename[blade_filename]["parsed_title"]["parsed_year"] == 2017


def test_library_payload_smart_cases_display_title_without_touching_raw_base_title(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    now = utcnow_iso()
    filename = "the godfather 1972 4k-kc.mkv"
    with get_connection(initialized_settings) as connection:
        shared_source_id = ensure_current_shared_local_source_binding(
            initialized_settings,
            connection=connection,
        )
        connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                library_source_id,
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
            ) VALUES (?, ?, ?, 'local', ?, ?, ?, NULL, NULL, NULL, NULL, NULL, 'mkv', ?, ?, ?, ?)
            """,
            (
                "the godfather  4k-kc",
                filename,
                str(Path(initialized_settings.media_root) / filename),
                int(shared_source_id),
                1,
                1.0,
                1972,
                now,
                now,
                now,
            ),
        )
        connection.commit()

    response = client.get("/api/library")
    assert response.status_code == 200
    assert response.json()["total_items"] == 1
    item = response.json()["items"][0]
    assert item["title"] == "The Godfather"
    assert item["parsed_title"]["display_title"] == "The Godfather"
    assert item["parsed_title"]["base_title"] == "the godfather"
    assert item["parsed_title"]["parsed_year"] == 1972


def test_scan_preserves_raw_title_truth_while_library_uses_derived_display_title(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    filename = "Interstellar.2014.UHD.BluRay.2160p.DTS-HD.MA.5.1.HEVC.REMUX-FraMeSToR.mkv"
    media_file = Path(initialized_settings.media_root) / filename
    media_file.write_bytes(b"fake-video")

    scan_result = scan_media_library(initialized_settings, reason="test_scan_preserves_raw_title_truth")
    assert scan_result["files_seen"] == 1

    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT title, original_filename, year
            FROM media_items
            WHERE original_filename = ?
            LIMIT 1
            """,
            (filename,),
        ).fetchone()

    assert row is not None
    assert row["title"] == "Interstellar.2014.UHD.BluRay.2160p.DTS-HD.MA.5.1.HEVC.REMUX-FraMeSToR"
    assert row["original_filename"] == filename
    assert row["year"] == 2014

    response = client.get("/api/library")
    assert response.status_code == 200
    assert response.json()["total_items"] == 1
    item = response.json()["items"][0]
    assert item["title"] == "Interstellar"
    assert item["parsed_title"]["display_title"] == "Interstellar"
    assert item["parsed_title"]["title_source"] == "original_filename"
    assert item["parsed_title"]["parser_version"]
    assert item["parsed_title"]["suspicious_output"] is False


def test_poster_lookup_uses_raw_title_identity_not_cleaned_display_path(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    poster_dir = Path(initialized_settings.media_root) / "Posters"
    poster_dir.mkdir(parents=True, exist_ok=True)
    poster_path = poster_dir / "Harry Potter and the Deathly Hallows Part 1 (2010).jpg"
    poster_path.write_bytes(b"fake-jpg")

    now = utcnow_iso()
    original_filename = "Harry.Potter.and.the.Deathly.Hallows.Part.1.2010.4K.UHD.2160p.REMUX.DV.DTS-HD.MA.7.1.Dual.PTBR-BrRemux.mkv"
    with get_connection(initialized_settings) as connection:
        shared_source_id = ensure_current_shared_local_source_binding(
            initialized_settings,
            connection=connection,
        )
        cursor = connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                library_source_id,
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
            ) VALUES (?, ?, ?, 'local', ?, ?, ?, NULL, NULL, NULL, NULL, NULL, 'mkv', ?, ?, ?, ?)
            """,
            (
                "Harry Potter and the Deathly Hallows Part",
                original_filename,
                str(Path(initialized_settings.media_root) / original_filename),
                int(shared_source_id),
                1,
                1.0,
                2010,
                now,
                now,
                now,
            ),
        )
        item_id = int(cursor.lastrowid)
        connection.commit()

    library_response = client.get("/api/library")
    assert library_response.status_code == 200
    item = next(
        candidate
        for candidate in library_response.json()["items"]
        if candidate["id"] == item_id
    )
    assert item["title"] == "Harry Potter and the Deathly Hallows Part 1"
    assert item["parsed_title"]["title_source"] == "original_filename"
    assert item["poster_url"].startswith(f"/api/library/item/{item_id}/poster?v=")

    poster_response = client.get(item["poster_url"])
    assert poster_response.status_code == 200
    assert poster_response.content == b"fake-jpg"
    assert poster_response.headers["cache-control"] == "private, no-cache, max-age=0, must-revalidate"


def test_local_file_rename_updates_title_truth_after_rescan(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    media_root = Path(initialized_settings.media_root)
    original_filename = "Movie.Alpha.2014.1080p.BluRay.x264.mkv"
    original_path = media_root / original_filename
    original_path.write_bytes(b"movie-alpha")

    scan_media_library(initialized_settings, reason="manual")

    before = client.get("/api/library")
    assert before.status_code == 200
    assert before.json()["total_items"] == 1
    before_item = before.json()["items"][0]
    before_id = int(before_item["id"])
    assert before_item["title"] == "Movie Alpha"
    assert before_item["year"] == 2014
    now = utcnow_iso()
    with get_connection(initialized_settings) as connection:
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
            ) VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (1, before_id, 321.0, 1200.0, 321.0, now),
        )
        connection.commit()

    original_path.rename(media_root / "Movie.Bravo.1080p.BluRay.x264.mkv")
    scan_media_library(initialized_settings, reason="manual")

    after = client.get("/api/library")
    assert after.status_code == 200
    assert after.json()["total_items"] == 1
    after_item = after.json()["items"][0]
    assert after_item["id"] == before_id
    assert after_item["title"] == "Movie Bravo"
    assert after_item["original_filename"] == "Movie.Bravo.1080p.BluRay.x264.mkv"
    assert after_item["year"] == 2014
    assert after_item["progress_seconds"] == 321.0

    with get_connection(initialized_settings) as connection:
        rows = connection.execute(
            """
            SELECT id, title, original_filename, year
            FROM media_items
            ORDER BY id ASC
            """
        ).fetchall()

    assert [tuple(row) for row in rows] == [
        (
            before_id,
            "Movie.Bravo.1080p.BluRay.x264",
            "Movie.Bravo.1080p.BluRay.x264.mkv",
            2014,
        ),
    ]


def test_local_rename_collision_does_not_get_swallowed_by_duplicate_filter(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    media_root = Path(initialized_settings.media_root)
    stable_filename = "The.Godfather.1972.REMUX-FraMeSToR.mkv"
    renamed_source_filename = "The.Godfather.Coda.1972.4k-kc.mkv"
    renamed_collision_filename = "the godfather 1972 4k-kc.mkv"
    (media_root / stable_filename).write_bytes(b"stable-cut")
    source_path = media_root / renamed_source_filename
    source_path.write_bytes(b"coda-cut")

    scan_media_library(initialized_settings, reason="manual")
    before = client.get("/api/library")
    assert before.status_code == 200
    assert before.json()["total_items"] == 2

    source_path.rename(media_root / renamed_collision_filename)
    scan_media_library(initialized_settings, reason="manual")

    after = client.get("/api/library")
    assert after.status_code == 200
    assert after.json()["total_items"] == 2
    assert sum(1 for item in after.json()["items"] if item["title"] == "The Godfather") == 2
    assert sorted(item["original_filename"] for item in after.json()["items"]) == sorted(
        [stable_filename, renamed_collision_filename]
    )


def test_sequence_sensitive_rename_keeps_distinct_movies_visible_after_rescan(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    media_root = Path(initialized_settings.media_root)
    part_one_filename = "Harry.Potter.and.the.Deathly.Hallows.Part.1.2010.1080p.BluRay.x264.mkv"
    part_two_filename = "Harry.Potter.and.the.Deathly.Hallows.Part.2.2011.1080p.BluRay.x264.mkv"
    renamed_part_one_filename = "Harry.Potter.and.the.Deathly.Hallows.Part.One.2010.1080p.BluRay.x264.mkv"
    (media_root / part_one_filename).write_bytes(b"part-one")
    (media_root / part_two_filename).write_bytes(b"part-two")

    scan_media_library(initialized_settings, reason="manual")

    (media_root / part_one_filename).rename(media_root / renamed_part_one_filename)
    scan_media_library(initialized_settings, reason="manual")

    response = client.get("/api/library")
    assert response.status_code == 200
    assert response.json()["total_items"] == 2
    titles = sorted(item["title"] for item in response.json()["items"])
    assert titles == [
        "Harry Potter and the Deathly Hallows Part 2",
        "Harry Potter and the Deathly Hallows Part One",
    ]


def test_renamed_movie_and_renamed_poster_rematch_after_rescan(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    media_root = Path(initialized_settings.media_root)
    poster_dir = media_root / "Posters"
    poster_dir.mkdir(parents=True, exist_ok=True)

    original_movie_filename = "The.Godfather.1972.4k-kc.mkv"
    renamed_movie_filename = "The.Godfather.Coda.1972.4k-kc.mkv"
    original_movie_path = media_root / original_movie_filename
    original_movie_path.write_bytes(b"godfather-original")

    original_poster_path = poster_dir / "The Godfather (1972).jpg"
    original_poster_path.write_bytes(b"poster-original")

    scan_media_library(initialized_settings, reason="manual")
    before = client.get("/api/library")
    assert before.status_code == 200
    assert before.json()["total_items"] == 1
    before_item = before.json()["items"][0]
    before_item_id = int(before_item["id"])
    before_poster_url = before_item["poster_url"]
    assert before_item["title"] == "The Godfather"
    assert before_poster_url is not None
    assert client.get(before_poster_url).content == b"poster-original"

    original_movie_path.rename(media_root / renamed_movie_filename)
    renamed_poster_path = poster_dir / "The Godfather Coda (1972).jpg"
    original_poster_path.rename(renamed_poster_path)
    renamed_poster_path.write_bytes(b"poster-renamed")

    scan_media_library(initialized_settings, reason="manual")
    after = client.get("/api/library")
    assert after.status_code == 200
    assert after.json()["total_items"] == 1
    after_item = next(
        item for item in after.json()["items"] if item["original_filename"] == renamed_movie_filename
    )
    assert int(after_item["id"]) == before_item_id
    assert after_item["title"] == "The Godfather Coda"
    assert after_item["poster_url"] is not None
    assert after_item["poster_url"] != before_poster_url

    poster_response = client.get(after_item["poster_url"])
    assert poster_response.status_code == 200
    assert poster_response.content == b"poster-renamed"
    assert poster_response.headers["cache-control"] == "private, no-cache, max-age=0, must-revalidate"


def test_manual_rescan_heals_stale_hidden_movie_keys_for_recreated_local_rows(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    media_root = Path(initialized_settings.media_root)
    poster_dir = media_root / "Posters"
    poster_dir.mkdir(parents=True, exist_ok=True)
    movie_filename = "Harry.Potter.and.the.Philosopher's.Stone.2001.1080p.BluRay.x264.mkv"
    movie_path = media_root / movie_filename
    movie_path.write_bytes(b"philosophers-stone")
    (poster_dir / "Harry Potter and the Philosopher's Stone (2001).jpg").write_bytes(b"poster-bytes")

    scan_media_library(initialized_settings, reason="manual")

    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT id, title, year, original_filename
            FROM media_items
            WHERE original_filename = ?
            LIMIT 1
            """,
            (movie_filename,),
        ).fetchone()
        assert row is not None
        movie_key = _row_hidden_movie_key(row)
        assert movie_key is not None
        connection.execute(
            """
            INSERT OR REPLACE INTO global_hidden_movie_keys (
                movie_key,
                display_title,
                year,
                edition_identity,
                hidden_by_user_id,
                hidden_at
            ) VALUES (?, ?, ?, 'standard', 1, '2026-03-28 00:01:05')
            """,
            (movie_key, "Harry Potter and the Philosopher's Stone", 2001),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO user_hidden_movie_keys (
                user_id,
                movie_key,
                display_title,
                year,
                edition_identity,
                hidden_at
            ) VALUES (1, ?, ?, ?, 'standard', '2026-03-28 00:01:05')
            """,
            (movie_key, "Harry Potter and the Philosopher's Stone", 2001),
        )
        connection.commit()

    hidden_response = client.get("/api/library")
    assert hidden_response.status_code == 200
    assert hidden_response.json()["total_items"] == 0

    repair_result = scan_media_library(initialized_settings, reason="manual")
    assert repair_result["hidden_movie_keys_pruned"] == 2

    healed_response = client.get("/api/library")
    assert healed_response.status_code == 200
    assert healed_response.json()["total_items"] == 1
    item = healed_response.json()["items"][0]
    assert item["title"] == "Harry Potter and the Philosopher's Stone"
    assert item["poster_url"] is not None
    assert client.get(item["poster_url"]).content == b"poster-bytes"

    with get_connection(initialized_settings) as connection:
        hidden_rows = connection.execute(
            "SELECT COUNT(*) AS count FROM global_hidden_movie_keys WHERE movie_key = ?",
            (movie_key,),
        ).fetchone()
        assert int(hidden_rows["count"]) == 0
        user_hidden_rows = connection.execute(
            "SELECT COUNT(*) AS count FROM user_hidden_movie_keys WHERE movie_key = ? AND user_id = 1",
            (movie_key,),
        ).fetchone()
        assert int(user_hidden_rows["count"]) == 0


def test_poster_reference_location_switch_refreshes_poster_without_media_rescan(
    client,
    admin_credentials,
    initialized_settings,
    tmp_path,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    media_root = Path(initialized_settings.media_root)
    default_poster_dir = media_root / "Posters"
    default_poster_dir.mkdir(parents=True, exist_ok=True)
    alternate_poster_dir = tmp_path / "alternate-posters"
    alternate_poster_dir.mkdir()

    movie_filename = "Interstellar.2014.UHD.BluRay.2160p.DTS-HD.MA.5.1.HEVC.REMUX-FraMeSToR.mkv"
    (media_root / movie_filename).write_bytes(b"interstellar")
    (default_poster_dir / "Interstellar (2014).jpg").write_bytes(b"default-poster")
    (alternate_poster_dir / "Interstellar (2014).jpg").write_bytes(b"alternate-poster")

    scan_media_library(initialized_settings, reason="manual")
    before = client.get("/api/library")
    assert before.status_code == 200
    assert before.json()["total_items"] == 1
    before_item = before.json()["items"][0]
    before_poster_url = before_item["poster_url"]
    assert before_poster_url is not None
    assert client.get(before_poster_url).content == b"default-poster"

    update_response = client.put(
        "/api/admin/poster-reference-location",
        json={"value": str(alternate_poster_dir)},
    )
    assert update_response.status_code == 200
    assert update_response.json()["effective_value"] == str(alternate_poster_dir.resolve())

    after = client.get("/api/library")
    assert after.status_code == 200
    assert after.json()["total_items"] == 1
    after_item = after.json()["items"][0]
    assert after_item["poster_url"] is not None
    assert after_item["poster_url"] != before_poster_url

    poster_response = client.get(after_item["poster_url"])
    assert poster_response.status_code == 200
    assert poster_response.content == b"alternate-poster"
    assert poster_response.headers["cache-control"] == "private, no-cache, max-age=0, must-revalidate"


@pytest.mark.parametrize(
    ("movie_filename", "poster_filename", "expected_title"),
    [
        (
            "Harry.Potter.and.the.Deathly.Hallows.Part.1.2010.4K.UHD.2160p.REMUX.DV.DTS-HD.MA.7.1.Dual.PTBR-BrRemux.mkv",
            "Harry Potter and the Deathly Hallows_ Part I (2010).png",
            "Harry Potter and the Deathly Hallows Part 1",
        ),
        (
            "Harry.Potter.and.the.Deathly.Hallows.Part.2.2011.4K.UHD.2160p.REMUX.DV.DTS-HD.MA.7.1.Dual.PTBR-BrRemux.mkv",
            "Harry Potter and the Deathly Hallows_ Part II (2011).png",
            "Harry Potter and the Deathly Hallows Part 2",
        ),
    ],
)
def test_poster_lookup_matches_roman_numeral_part_variants_in_live_style_files(
    client,
    admin_credentials,
    initialized_settings,
    movie_filename: str,
    poster_filename: str,
    expected_title: str,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    media_root = Path(initialized_settings.media_root)
    poster_dir = media_root / "Posters"
    poster_dir.mkdir(parents=True, exist_ok=True)
    (media_root / movie_filename).write_bytes(b"movie-bytes")
    (poster_dir / poster_filename).write_bytes(b"poster-bytes")

    scan_media_library(initialized_settings, reason="manual")

    response = client.get("/api/library")
    assert response.status_code == 200
    assert response.json()["total_items"] == 1
    item = response.json()["items"][0]
    assert item["title"] == expected_title
    assert item["poster_url"] is not None

    poster_response = client.get(item["poster_url"])
    assert poster_response.status_code == 200
    assert poster_response.content == b"poster-bytes"


@pytest.mark.parametrize(
    ("movie_title", "original_filename", "poster_filename", "expected_title"),
    [
        (
            "Rocky II",
            "Rocky.II.1979.1080p.BluRay.x264.mkv",
            "Rocky 2 (1979).png",
            "Rocky II",
        ),
        (
            "Ocean's Eleven",
            "Oceans.Eleven.2001.1080p.BluRay.Remux.TrueHD.mkv",
            "Oceans Eleven (2001).png",
            "Ocean's Eleven",
        ),
        (
            "Pirates of the Caribbean: The Curse of the Black Pearl",
            "Pirates.of.the.Caribbean.The.Curse.of.the.Black.Pearl.2003.1080p.BluRay.x264.mkv",
            "Pirates of the Caribbean - The Curse of the Black Pearl (2003).png",
            "Pirates of the Caribbean: The Curse of the Black Pearl",
        ),
    ],
)
def test_poster_lookup_matches_safe_equivalence_family_patterns(
    client,
    admin_credentials,
    initialized_settings,
    movie_title: str,
    original_filename: str,
    poster_filename: str,
    expected_title: str,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    media_root = Path(initialized_settings.media_root)
    poster_dir = media_root / "Posters"
    poster_dir.mkdir(parents=True, exist_ok=True)
    movie_path = media_root / original_filename
    movie_path.write_bytes(b"movie-bytes")

    scan_media_library(initialized_settings, reason="manual")

    with get_connection(initialized_settings) as connection:
        connection.execute(
            """
            UPDATE media_items
            SET title = ?
            WHERE original_filename = ?
            """,
            (movie_title, original_filename),
        )
        connection.commit()

    (poster_dir / poster_filename).write_bytes(b"poster-bytes")

    response = client.get("/api/library")
    assert response.status_code == 200
    item = next(row for row in response.json()["items"] if row["original_filename"] == original_filename)
    assert item["title"] == expected_title
    assert item["poster_url"] is not None
    poster_response = client.get(item["poster_url"])
    assert poster_response.status_code == 200
    assert poster_response.content == b"poster-bytes"


def test_poster_lookup_matches_safe_singular_plural_variant_when_year_matches(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    media_root = Path(initialized_settings.media_root)
    poster_dir = media_root / "Posters"
    poster_dir.mkdir(parents=True, exist_ok=True)
    movie_filename = "Untouchables.2011.1080p.NF.WEB-DL.DD+5.1.x264-Telly.mkv"
    (media_root / movie_filename).write_bytes(b"movie-bytes")
    (poster_dir / "Untouchable (2011).png").write_bytes(b"poster-bytes")

    scan_media_library(initialized_settings, reason="manual")

    response = client.get("/api/library")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["title"] == "Untouchables"
    assert item["poster_url"] is not None

    poster_response = client.get(item["poster_url"])
    assert poster_response.status_code == 200
    assert poster_response.content == b"poster-bytes"


def test_poster_lookup_singular_plural_stage_requires_unique_yearful_candidate(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    media_root = Path(initialized_settings.media_root)
    poster_dir = media_root / "Posters"
    poster_dir.mkdir(parents=True, exist_ok=True)
    movie_filename = "Untouchables.2011.1080p.NF.WEB-DL.DD+5.1.x264-Telly.mkv"
    (media_root / movie_filename).write_bytes(b"movie-bytes")
    (poster_dir / "Untouchable (2011).jpg").write_bytes(b"poster-jpg")
    (poster_dir / "Untouchable (2011).png").write_bytes(b"poster-png")

    scan_media_library(initialized_settings, reason="manual")

    response = client.get("/api/library")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["title"] == "Untouchables"
    assert item["poster_url"] is None


def test_poster_lookup_singular_plural_stage_does_not_cross_part_identity(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    media_root = Path(initialized_settings.media_root)
    poster_dir = media_root / "Posters"
    poster_dir.mkdir(parents=True, exist_ok=True)
    movie_filename = "Untouchables.Part.2.2011.1080p.NF.WEB-DL.DD+5.1.x264-Telly.mkv"
    (media_root / movie_filename).write_bytes(b"movie-bytes")
    (poster_dir / "Untouchable Part 1 (2011).png").write_bytes(b"poster-bytes")

    scan_media_library(initialized_settings, reason="manual")

    response = client.get("/api/library")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["title"] == "Untouchables Part 2"
    assert item["poster_url"] is None


def test_poster_lookup_unique_yearless_fallback_is_safe_and_deterministic(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    media_root = Path(initialized_settings.media_root)
    poster_dir = media_root / "Posters"
    poster_dir.mkdir(parents=True, exist_ok=True)
    movie_filename = "Interstellar.2014.UHD.BluRay.2160p.DTS-HD.MA.5.1.HEVC.REMUX-FraMeSToR.mkv"
    (media_root / movie_filename).write_bytes(b"movie-bytes")
    (poster_dir / "Interstellar.png").write_bytes(b"poster-bytes")

    scan_media_library(initialized_settings, reason="manual")

    response = client.get("/api/library")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["title"] == "Interstellar"
    assert item["poster_url"] is not None
    poster_response = client.get(item["poster_url"])
    assert poster_response.status_code == 200
    assert poster_response.content == b"poster-bytes"


def test_poster_lookup_does_not_guess_across_title_typos(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    media_root = Path(initialized_settings.media_root)
    poster_dir = media_root / "Posters"
    poster_dir.mkdir(parents=True, exist_ok=True)
    movie_filename = "Forest.Gump.1994.1080p.BluRay.x264.DTS-ETRG.mkv"
    (media_root / movie_filename).write_bytes(b"movie-bytes")
    (poster_dir / "Forrest Gump (1994).png").write_bytes(b"poster-bytes")

    scan_media_library(initialized_settings, reason="manual")

    response = client.get("/api/library")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["title"] == "Forest Gump"
    assert item["poster_url"] is None


def test_cloud_rename_updates_one_row_in_place_without_duplicate_visibility(
    client,
    admin_credentials,
    initialized_settings,
    monkeypatch,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    poster_dir = Path(initialized_settings.media_root) / "Posters"
    poster_dir.mkdir(parents=True, exist_ok=True)
    source_id = _insert_google_drive_source(initialized_settings)

    _sync_fake_google_drive_source(
        monkeypatch,
        initialized_settings,
        source_id=source_id,
        rows=[_build_fake_google_drive_video_row(file_id="file-1", name="The Intouchables (2011).mp4")],
    )

    before_poster_path = poster_dir / "The Intouchables (2011).jpg"
    before_poster_path.write_bytes(b"cloud-poster-before")

    before = client.get("/api/library")
    assert before.status_code == 200
    assert before.json()["total_items"] == 1
    before_item = before.json()["items"][0]
    before_item_id = int(before_item["id"])
    assert before_item["title"] == "The Intouchables"
    assert before_item["poster_url"] is not None
    assert client.get(before_item["poster_url"]).content == b"cloud-poster-before"

    renamed_poster_path = poster_dir / "Untouchables (2011).jpg"
    before_poster_path.rename(renamed_poster_path)
    renamed_poster_path.write_bytes(b"cloud-poster-after")

    _sync_fake_google_drive_source(
        monkeypatch,
        initialized_settings,
        source_id=source_id,
        rows=[_build_fake_google_drive_video_row(file_id="file-1", name="Untouchables (2011).mp4")],
    )

    after = client.get("/api/library")
    assert after.status_code == 200
    assert after.json()["total_items"] == 1
    after_item = after.json()["items"][0]
    assert int(after_item["id"]) == before_item_id
    assert after_item["title"] == "Untouchables"
    assert after_item["original_filename"] == "Untouchables (2011).mp4"
    assert after_item["poster_url"] is not None
    assert after_item["poster_url"] != before_item["poster_url"]
    assert client.get(after_item["poster_url"]).content == b"cloud-poster-after"

    with get_connection(initialized_settings) as connection:
        rows = connection.execute(
            """
            SELECT id, title, original_filename, file_path, external_media_id, year
            FROM media_items
            WHERE COALESCE(source_kind, 'local') = 'cloud'
            ORDER BY id ASC
            """
        ).fetchall()

    assert [tuple(row) for row in rows] == [
        (
            before_item_id,
            "Untouchables (2011)",
            "Untouchables (2011).mp4",
            "gdrive://folder-123/file-1/Untouchables (2011).mp4",
            "file-1",
            2011,
        ),
    ]


def test_cloud_sync_collapses_existing_duplicate_rows_for_same_external_media_id(
    client,
    admin_credentials,
    initialized_settings,
    monkeypatch,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    source_id = _insert_google_drive_source(initialized_settings)
    now = utcnow_iso()
    with get_connection(initialized_settings) as connection:
        canonical_cursor = connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                library_source_id,
                external_media_id,
                cloud_mime_type,
                file_size,
                file_mtime,
                duration_seconds,
                width,
                height,
                container,
                year,
                created_at,
                updated_at,
                last_scanned_at
            ) VALUES (?, ?, ?, 'cloud', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "The Intouchables (2011)",
                "The Intouchables (2011).mp4",
                "gdrive://folder-123/file-1/The Intouchables (2011).mp4",
                source_id,
                "file-1",
                "video/mp4",
                1048576,
                1704067200.0,
                1000.0,
                1920,
                1080,
                "mp4",
                2011,
                now,
                now,
                now,
            ),
        )
        canonical_id = int(canonical_cursor.lastrowid)
        duplicate_cursor = connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                library_source_id,
                external_media_id,
                cloud_mime_type,
                file_size,
                file_mtime,
                duration_seconds,
                width,
                height,
                container,
                year,
                created_at,
                updated_at,
                last_scanned_at
            ) VALUES (?, ?, ?, 'cloud', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Untouchables (2011)",
                "Untouchables (2011).mp4",
                "gdrive://folder-123/file-1/Untouchables (2011).mp4",
                source_id,
                "file-1",
                "video/mp4",
                1048576,
                1704067200.0,
                1000.0,
                1920,
                1080,
                "mp4",
                2011,
                now,
                now,
                now,
            ),
        )
        duplicate_id = int(duplicate_cursor.lastrowid)
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (1, duplicate_id, 654.0, 1000.0, 654.0, 0, "2026-04-23T10:00:00+00:00"),
        )
        connection.execute(
            """
            INSERT INTO playback_watch_events (
                user_id,
                media_item_id,
                watched_seconds,
                recorded_at_epoch
            ) VALUES (?, ?, ?, ?)
            """,
            (1, duplicate_id, 321.0, 1713866400),
        )
        connection.commit()

    _sync_fake_google_drive_source(
        monkeypatch,
        initialized_settings,
        source_id=source_id,
        rows=[_build_fake_google_drive_video_row(file_id="file-1", name="Untouchables (2011).mp4")],
    )

    response = client.get("/api/library")
    assert response.status_code == 200
    assert response.json()["total_items"] == 1
    item = response.json()["items"][0]
    assert int(item["id"]) == canonical_id
    assert item["title"] == "Untouchables"
    assert item["progress_seconds"] == 654.0

    with get_connection(initialized_settings) as connection:
        rows = connection.execute(
            """
            SELECT id, title, original_filename, file_path, external_media_id
            FROM media_items
            WHERE COALESCE(source_kind, 'local') = 'cloud'
            ORDER BY id ASC
            """
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            (
                canonical_id,
                "Untouchables (2011)",
                "Untouchables (2011).mp4",
                "gdrive://folder-123/file-1/Untouchables (2011).mp4",
                "file-1",
            ),
        ]
        progress_row = connection.execute(
            """
            SELECT media_item_id, position_seconds, watch_seconds_total
            FROM playback_progress
            WHERE user_id = 1
            LIMIT 1
            """
        ).fetchone()
        assert tuple(progress_row) == (canonical_id, 654.0, 654.0)
        watch_event_row = connection.execute(
            """
            SELECT media_item_id, watched_seconds
            FROM playback_watch_events
            WHERE user_id = 1
            LIMIT 1
            """
        ).fetchone()
        assert tuple(watch_event_row) == (canonical_id, 321.0)


def test_library_route_handles_dirty_stored_titles_without_internal_server_error(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    now = utcnow_iso()
    with get_connection(initialized_settings) as connection:
        shared_source_id = ensure_current_shared_local_source_binding(
            initialized_settings,
            connection=connection,
        )
        connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                library_source_id,
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
            ) VALUES (?, ?, ?, 'local', ?, ?, ?, NULL, NULL, NULL, NULL, NULL, 'mkv', ?, ?, ?, ?)
            """,
            (
                "Avatar  UHD 4K BluRay 2160p ReMux HEVC HDR10 TrueHD 7 1 Atmos - MgB",
                "Avatar 2009 UHD 4K BluRay 2160p ReMux HEVC HDR10 TrueHD 7.1 Atmos - MgB.mkv",
                str(Path(initialized_settings.media_root) / "Avatar 2009 UHD 4K BluRay 2160p ReMux HEVC HDR10 TrueHD 7.1 Atmos - MgB.mkv"),
                int(shared_source_id),
                1,
                1.0,
                2009,
                now,
                now,
                now,
            ),
        )
        connection.commit()

    response = client.get("/api/library")
    assert response.status_code == 200
    assert response.json()["total_items"] == 1
    item = response.json()["items"][0]
    assert item["title"] == "Avatar"
    assert item["parsed_title"]["display_title"] == "Avatar"
    assert item["parsed_title"]["parsed_year"] == 2009


def test_admin_media_library_reference_smoke(client, admin_credentials) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    initial_response = client.get("/api/admin/media-library-reference")
    assert initial_response.status_code == 200
    initial_path = Path(initial_response.json()["effective_value"])
    assert initial_response.json() == {
        "configured_value": str(initial_path),
        "effective_value": str(initial_path),
        "default_value": str(initial_path),
        "validation_rules": [
            f"Leave blank to reset to the default shared local path: {initial_path}",
            "This is the real shared local library path currently used by Elvern for the shared local library.",
            "Use an absolute Linux directory path that already exists on this host.",
        ],
    }

    replacement_path = initial_path.parent / "shared-library-alt"
    replacement_path.mkdir()

    update_response = client.put(
        "/api/admin/media-library-reference",
        json={"value": str(replacement_path)},
    )
    assert update_response.status_code == 200
    assert update_response.json() == {
        "configured_value": str(replacement_path),
        "effective_value": str(replacement_path),
        "default_value": str(initial_path),
        "validation_rules": [
            f"Leave blank to reset to the default shared local path: {initial_path}",
            "This is the real shared local library path currently used by Elvern for the shared local library.",
            "Use an absolute Linux directory path that already exists on this host.",
        ],
    }


def test_admin_google_drive_setup_save_smoke(client, admin_credentials) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    update_response = client.put(
        "/api/admin/google-drive-setup",
        json={
            "https_origin": "https://example.com",
            "client_id": "example.apps.googleusercontent.com",
            "client_secret": "secret123",
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["https_origin"] == "https://example.com"
    assert update_response.json()["client_id"] == "example.apps.googleusercontent.com"
    assert update_response.json()["client_secret"] == "secret123"
    assert update_response.json()["configuration_state"] == "ready"
    assert update_response.json()["missing_fields"] == []

    cloud_response = client.get("/api/cloud-libraries")
    assert cloud_response.status_code == 200
    assert cloud_response.json()["google"]["enabled"] is True


def test_admin_google_drive_setup_validation_surfaces_specific_error(client, admin_credentials) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    invalid_response = client.put(
        "/api/admin/google-drive-setup",
        json={
            "https_origin": "https://example.com",
            "client_id": "example.apps.googleusercontent.com",
            "client_secret": "bad secret",
        },
    )
    assert invalid_response.status_code == 400
    assert invalid_response.json() == {
        "detail": "Google OAuth Client Secret must not contain spaces.",
    }


def test_admin_local_directory_browse_lists_host_directories_and_stays_admin_only(
    client,
    admin_credentials,
    tmp_path,
) -> None:
    browse_root = tmp_path / "browse-root"
    browse_root.mkdir()
    alpha_dir = browse_root / "alpha"
    beta_dir = browse_root / "beta"
    alpha_dir.mkdir()
    beta_dir.mkdir()
    (browse_root / "ignore-file.txt").write_text("not a directory", encoding="utf-8")

    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    response = client.get(
        "/api/admin/local-directories",
        params={"path": str(browse_root)},
    )

    assert response.status_code == 200
    assert response.json() == {
        "current_path": str(browse_root.resolve()),
        "parent_path": str(browse_root.resolve().parent),
        "directories": [
            {"name": "alpha", "path": str(alpha_dir.resolve())},
            {"name": "beta", "path": str(beta_dir.resolve())},
        ],
    }

    _create_standard_user_via_admin(client, username="browse-user", password="browse-user-password")
    _logout(client)
    _login(client, username="browse-user", password="browse-user-password")

    forbidden = client.get(
        "/api/admin/local-directories",
        params={"path": str(browse_root)},
    )
    assert forbidden.status_code == 403


def test_admin_local_directory_picker_returns_selected_host_directory_and_stays_admin_only(
    client,
    admin_credentials,
    monkeypatch,
    tmp_path,
) -> None:
    selected_dir = tmp_path / "picked-host-directory"
    selected_dir.mkdir()

    monkeypatch.setattr(
        "backend.app.routes.admin.resolve_same_host_request",
        lambda settings, *, platform, client_ip, request_host, explicit_same_host: {
            "same_host": True,
            "detection_source": "test",
            "reason": "test",
        },
    )
    monkeypatch.setattr(
        "backend.app.routes.admin.try_pick_local_directory",
        lambda settings, *, path, title: {
            "status": "selected",
            "selected_path": str(selected_dir.resolve()),
            "reason": None,
            "picker_backend": "zenity",
        },
    )

    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    response = client.post(
        "/api/admin/local-directory-picker",
        json={
            "path": str(tmp_path),
            "title": "Select poster directory",
            "platform": "linux",
            "same_host_hint": True,
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "selected",
        "selected_path": str(selected_dir.resolve()),
        "reason": None,
        "picker_backend": "zenity",
    }

    _create_standard_user_via_admin(client, username="picker-user", password="picker-password")
    _logout(client)
    _login(client, username="picker-user", password="picker-password")

    forbidden = client.post(
        "/api/admin/local-directory-picker",
        json={
            "path": str(tmp_path),
            "title": "Select poster directory",
            "platform": "linux",
            "same_host_hint": True,
        },
    )
    assert forbidden.status_code == 403


def test_admin_local_directory_picker_capability_is_admin_only_and_reports_same_host_linux_support(
    client,
    admin_credentials,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.routes.admin.resolve_same_host_request",
        lambda settings, *, platform, client_ip, request_host, explicit_same_host: {
            "same_host": platform == "linux",
            "detection_source": "local_server_ip_match",
            "reason": "Client IP matched a resolved local server address.",
        },
    )
    monkeypatch.setattr(
        "backend.app.routes.admin.get_native_local_directory_picker_capability",
        lambda: {
            "native_picker_supported": True,
            "picker_backend": "zenity",
            "gui_session_available": True,
            "display_available": True,
            "wayland_available": False,
            "dbus_session_available": True,
            "missing_dependency": None,
            "reason": None,
        },
    )

    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    response = client.get(
        "/api/admin/local-directory-picker/capability",
        params={"platform": "linux"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "native_picker_supported": True,
        "same_host_linux": True,
        "same_host_detection_source": "local_server_ip_match",
        "same_host_reason": "Client IP matched a resolved local server address.",
        "picker_backend": "zenity",
        "gui_session_available": True,
        "display_available": True,
        "wayland_available": False,
        "dbus_session_available": True,
        "missing_dependency": None,
        "reason": None,
    }

    _create_standard_user_via_admin(client, username="picker-cap-user", password="picker-cap-password")
    _logout(client)
    _login(client, username="picker-cap-user", password="picker-cap-password")

    forbidden = client.get(
        "/api/admin/local-directory-picker/capability",
        params={"platform": "linux"},
    )
    assert forbidden.status_code == 403


def test_resolve_same_host_request_uses_request_host_candidates(initialized_settings, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.services.desktop_playback_service._resolve_host_ips",
        lambda host: {"100.64.0.9"} if host == "spark.test.ts.net" else set(),
    )

    result = resolve_same_host_request(
        initialized_settings,
        platform="linux",
        client_ip="100.64.0.9",
        request_host="spark.test.ts.net",
    )

    assert result == {
        "same_host": True,
        "detection_source": "local_server_ip_match",
        "reason": "Client IP matched a resolved local server address.",
    }


def test_standard_user_private_media_library_reference_uses_shared_default_and_stays_private(
    client,
    admin_credentials,
) -> None:
    alice_password = "alice-family-password"
    bob_password = "bob-family-password"

    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )
    _create_standard_user_via_admin(client, username="alice-reference", password=alice_password)
    _create_standard_user_via_admin(client, username="bob-reference", password=bob_password)

    initial_path = Path(client.get("/api/admin/media-library-reference").json()["effective_value"])
    replacement_path = initial_path.parent / "shared-basement-library"
    replacement_path.mkdir()

    shared_update = client.put(
        "/api/admin/media-library-reference",
        json={"value": str(replacement_path)},
    )
    assert shared_update.status_code == 200
    assert shared_update.json()["effective_value"] == str(replacement_path)

    admin_private_attempt = client.patch(
        "/api/user-settings",
        json={"media_library_reference_private_value": "Admin Private Reference"},
    )
    assert admin_private_attempt.status_code == 403
    assert admin_private_attempt.json()["detail"] == "Only standard users can set a private media library reference"

    _logout(client)

    _login(client, username="alice-reference", password=alice_password)
    alice_initial = client.get("/api/user-settings")
    assert alice_initial.status_code == 200
    assert alice_initial.json()["media_library_reference_private_value"] is None
    assert alice_initial.json()["media_library_reference_shared_default_value"] == str(replacement_path)
    assert alice_initial.json()["media_library_reference_effective_value"] == str(replacement_path)

    alice_update = client.patch(
        "/api/user-settings",
        json={"media_library_reference_private_value": "Alice Shelf A"},
    )
    assert alice_update.status_code == 200
    assert alice_update.json()["media_library_reference_private_value"] == "Alice Shelf A"
    assert alice_update.json()["media_library_reference_shared_default_value"] == str(replacement_path)
    assert alice_update.json()["media_library_reference_effective_value"] == "Alice Shelf A"
    _logout(client)

    _login(client, username="bob-reference", password=bob_password)
    bob_initial = client.get("/api/user-settings")
    assert bob_initial.status_code == 200
    assert bob_initial.json()["media_library_reference_private_value"] is None
    assert bob_initial.json()["media_library_reference_shared_default_value"] == str(replacement_path)
    assert bob_initial.json()["media_library_reference_effective_value"] == str(replacement_path)
    _logout(client)

    _login(client, username="alice-reference", password=alice_password)
    alice_repeat = client.get("/api/user-settings")
    assert alice_repeat.status_code == 200
    assert alice_repeat.json()["media_library_reference_private_value"] == "Alice Shelf A"
    assert alice_repeat.json()["media_library_reference_effective_value"] == "Alice Shelf A"

    alice_clear = client.patch(
        "/api/user-settings",
        json={"media_library_reference_private_value": ""},
    )
    assert alice_clear.status_code == 200
    assert alice_clear.json()["media_library_reference_private_value"] is None
    assert alice_clear.json()["media_library_reference_shared_default_value"] == str(replacement_path)
    assert alice_clear.json()["media_library_reference_effective_value"] == str(replacement_path)
    _logout(client)

    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )
    admin_global = client.get("/api/admin/media-library-reference")
    assert admin_global.status_code == 200
    assert admin_global.json()["configured_value"] == str(replacement_path)
    assert admin_global.json()["effective_value"] == str(replacement_path)


def test_admin_shared_local_library_path_switch_rebuilds_visible_library(client, admin_credentials, tmp_path) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    original_root = Path(client.app.state.settings.media_root)
    original_movie = original_root / "Who.Am.I.2014.mp4"
    original_movie.write_bytes(b"original shared local movie")
    scan_media_library(client.app.state.settings, reason="manual")

    before_switch = client.get("/api/library")
    assert before_switch.status_code == 200
    assert before_switch.json()["total_items"] == 1
    original_item_id = int(before_switch.json()["items"][0]["id"])

    empty_root = tmp_path / "empty-shared-library"
    empty_root.mkdir()

    update_response = client.put(
        "/api/admin/media-library-reference",
        json={"value": str(empty_root)},
    )
    assert update_response.status_code == 200
    assert update_response.json()["effective_value"] == str(empty_root.resolve())

    after_switch = client.get("/api/library")
    assert after_switch.status_code == 200
    assert after_switch.json()["total_items"] == 0
    assert after_switch.json()["items"] == []
    assert after_switch.json()["recently_added"] == []
    assert after_switch.json()["continue_watching"] == []
    assert after_switch.json()["series_rails"] == []

    search_after_switch = client.get("/api/library/search", params={"q": "whoami"})
    assert search_after_switch.status_code == 200
    assert search_after_switch.json()["total_items"] == 0
    assert search_after_switch.json()["items"] == []

    detail_after_switch = client.get(f"/api/library/item/{original_item_id}")
    assert detail_after_switch.status_code == 404
    assert detail_after_switch.json()["detail"] == "Media item not found"

    stream_after_switch = client.get(f"/api/stream/{original_item_id}")
    assert stream_after_switch.status_code == 404
    assert stream_after_switch.json()["detail"] == "Media item not found"

    with get_connection(client.app.state.settings) as connection:
        local_row_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM media_items
            WHERE COALESCE(source_kind, 'local') = 'local'
            """
        ).fetchone()

    assert int(local_row_count["count"]) == 0


def test_legacy_unbound_local_rows_do_not_leak_through_shared_library_visibility(
    client,
    admin_credentials,
    tmp_path,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    empty_root = tmp_path / "empty-shared-library"
    empty_root.mkdir()
    update_response = client.put(
        "/api/admin/media-library-reference",
        json={"value": str(empty_root)},
    )
    assert update_response.status_code == 200

    legacy_file = tmp_path / "legacy-leak.mp4"
    legacy_file.write_bytes(b"legacy local row")
    now = utcnow_iso()

    with get_connection(client.app.state.settings) as connection:
        connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                library_source_id,
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
            ) VALUES (?, ?, ?, 'local', NULL, ?, ?, NULL, NULL, NULL, NULL, NULL, 'mp4', ?, ?, ?, ?)
            """,
            (
                "Legacy Leak",
                legacy_file.name,
                str(legacy_file.resolve()),
                int(legacy_file.stat().st_size),
                float(legacy_file.stat().st_mtime),
                2020,
                now,
                now,
                now,
            ),
        )
        legacy_item_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.commit()

    library_response = client.get("/api/library")
    assert library_response.status_code == 200
    assert library_response.json()["total_items"] == 0
    assert library_response.json()["items"] == []

    search_response = client.get("/api/library/search", params={"q": "legacyleak"})
    assert search_response.status_code == 200
    assert search_response.json()["total_items"] == 0
    assert search_response.json()["items"] == []

    detail_response = client.get(f"/api/library/item/{legacy_item_id}")
    assert detail_response.status_code == 404
    assert detail_response.json()["detail"] == "Media item not found"

    stream_response = client.get(f"/api/stream/{legacy_item_id}")
    assert stream_response.status_code == 404
    assert stream_response.json()["detail"] == "Media item not found"


def test_admin_shared_local_library_path_rejects_missing_directory(client, admin_credentials, tmp_path) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    missing_path = tmp_path / "missing-shared-library"
    response = client.put(
        "/api/admin/media-library-reference",
        json={"value": str(missing_path)},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Shared local library path does not exist on this host."
