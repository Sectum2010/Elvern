from __future__ import annotations

import importlib
from pathlib import Path

from fastapi.testclient import TestClient

import backend.app.main as main_module
from backend.app.auth import ensure_admin_user
from backend.app.config import get_settings, refresh_settings
from backend.app.db import get_connection, utcnow_iso
from backend.app.db import init_db
from backend.app.media_scan import scan_media_library
from backend.app.security import hash_password
from backend.app.services.desktop_playback_service import resolve_same_host_request
from backend.app.services.local_library_source_service import ensure_current_shared_local_source_binding
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


def test_test_settings_fixture_clears_live_auth_hash_and_linux_library_root(monkeypatch, request) -> None:
    monkeypatch.setenv("ELVERN_ADMIN_PASSWORD_HASH", hash_password("wrong-live-password"))
    monkeypatch.setenv("ELVERN_LIBRARY_ROOT_LINUX", "/home/sectum/Videos/Movies")

    initialized_settings = request.getfixturevalue("initialized_settings")
    client = request.getfixturevalue("client")
    admin_credentials = request.getfixturevalue("admin_credentials")

    assert initialized_settings.admin_password_hash is None
    assert initialized_settings.library_root_linux == str(initialized_settings.media_root)

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
    assert item["poster_url"] == f"/api/library/item/{item_id}/poster"

    poster_response = client.get(f"/api/library/item/{item_id}/poster")
    assert poster_response.status_code == 200
    assert poster_response.content == b"fake-jpg"


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
