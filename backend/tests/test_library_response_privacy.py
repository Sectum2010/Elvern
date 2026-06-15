from __future__ import annotations

from pathlib import Path

from backend.app.db import get_connection, utcnow_iso
from backend.app.services.local_library_source_service import ensure_current_shared_local_source_binding


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


def _insert_media_item(settings, *, original_filename: str = "Private.Source.File.2026.mkv") -> tuple[int, Path]:
    media_file = Path(settings.media_root) / "Movies" / original_filename
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(b"private-source")
    now = utcnow_iso()
    with get_connection(settings) as connection:
        shared_source_id = ensure_current_shared_local_source_binding(
            settings,
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
            ) VALUES (?, ?, ?, 'local', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Private Source File",
                original_filename,
                str(media_file.resolve()),
                shared_source_id,
                "movies",
                str(media_file.parent.resolve()),
                "Movies",
                "movie",
                str(media_file.parent.resolve()),
                "Movies",
                media_file.stat().st_size,
                media_file.stat().st_mtime,
                1200,
                1920,
                1080,
                "h264",
                "aac",
                "mkv",
                2026,
                now,
                now,
                now,
            ),
        )
        media_item_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO media_item_technical_metadata (
                media_item_id,
                metadata_version,
                metadata_source,
                probe_status,
                probe_error,
                updated_at,
                source_fingerprint
            ) VALUES (?, 1, 'ffprobe', 'failed', ?, ?, ?)
            """,
            (
                media_item_id,
                f"ffprobe failed for {media_file.resolve()}",
                now,
                "source-fingerprint",
            ),
        )
        connection.commit()
    return media_item_id, media_file.resolve()


def test_standard_user_library_detail_redacts_path_and_original_filename(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    item_id, media_file = _insert_media_item(initialized_settings)
    standard_password = "standard-user-password"

    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _create_standard_user(client, username="privacy-standard", password=standard_password)
    _logout(client)
    _login(client, username="privacy-standard", password=standard_password)

    response = client.get(f"/api/library/item/{item_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "Private Source File"
    assert payload["stream_url"] == f"/api/stream/{item_id}"
    assert payload["source_kind"] == "local"
    assert payload["file_path"] is None
    assert payload["original_filename"] is None
    assert payload["library_category_path"] is None
    assert payload["library_folder_path"] is None
    assert payload["track_scan_error"] == ""
    assert payload["audio_track_diagnostics"]["track_scan_error"] == ""
    assert payload["subtitle_track_diagnostics"]["track_scan_error"] == ""
    assert str(media_file) not in response.text
    assert "Private.Source.File.2026.mkv" not in response.text


def test_admin_library_detail_keeps_source_file_metadata(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    item_id, media_file = _insert_media_item(initialized_settings)

    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    response = client.get(f"/api/library/item/{item_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["stream_url"] == f"/api/stream/{item_id}"
    assert payload["file_path"] == str(media_file)
    assert payload["original_filename"] == "Private.Source.File.2026.mkv"
    assert payload["library_category_path"] == str(media_file.parent)
    assert payload["library_folder_path"] == str(media_file.parent)
    assert str(media_file) in payload["track_scan_error"]


def test_standard_user_library_lists_redact_original_filename_and_folder_paths(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    _insert_media_item(initialized_settings)
    standard_password = "standard-user-password"

    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _create_standard_user(client, username="privacy-list-standard", password=standard_password)
    _logout(client)
    _login(client, username="privacy-list-standard", password=standard_password)

    response = client.get("/api/library")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["title"] == "Private Source File"
    assert payload["items"][0]["original_filename"] is None
    assert payload["items"][0]["library_category_path"] is None
    assert payload["items"][0]["library_folder_path"] is None
    assert "Private.Source.File.2026.mkv" not in response.text


def test_admin_library_lists_keep_original_filename_for_debugging(
    client,
    admin_credentials,
    initialized_settings,
) -> None:
    _insert_media_item(initialized_settings)

    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    response = client.get("/api/library")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["original_filename"] == "Private.Source.File.2026.mkv"
