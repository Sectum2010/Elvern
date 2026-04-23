from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from backend.app.db import get_connection, utcnow_iso
from backend.app.media_scan import scan_media_library
from backend.app.services.local_library_source_service import (
    LOCAL_FILESYSTEM_PROVIDER,
    LOCAL_LIBRARY_RESOURCE_TYPE,
    SHARED_LOCAL_LIBRARY_DISPLAY_NAME,
    SHARED_LOCAL_LIBRARY_RESOURCE_ID,
    bind_unassigned_local_media_items_to_shared_source,
    build_private_local_library_resource_id,
    ensure_shared_local_library_source,
    get_effective_shared_local_library_path,
    normalize_local_library_path,
    update_shared_local_library_path,
)


def test_shared_local_library_source_is_created_with_first_class_local_shape(initialized_settings) -> None:
    source_id = ensure_shared_local_library_source(initialized_settings)

    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT
                s.id,
                s.owner_user_id,
                s.provider,
                s.resource_type,
                s.resource_id,
                s.display_name,
                s.local_path,
                s.is_shared,
                u.username AS owner_username
            FROM library_sources s
            JOIN users u ON u.id = s.owner_user_id
            WHERE s.id = ?
            """,
            (source_id,),
        ).fetchone()

    assert row is not None
    assert int(row["id"]) == source_id
    assert str(row["owner_username"]) == initialized_settings.admin_username
    assert str(row["provider"]) == LOCAL_FILESYSTEM_PROVIDER
    assert str(row["resource_type"]) == LOCAL_LIBRARY_RESOURCE_TYPE
    assert str(row["resource_id"]) == SHARED_LOCAL_LIBRARY_RESOURCE_ID
    assert str(row["display_name"]) == SHARED_LOCAL_LIBRARY_DISPLAY_NAME
    assert str(row["local_path"]) == normalize_local_library_path(initialized_settings.media_root)
    assert bool(row["is_shared"]) is True


def test_shared_local_library_source_preserves_existing_live_path(initialized_settings, tmp_path) -> None:
    initial_source_id = ensure_shared_local_library_source(initialized_settings)

    replacement_root = tmp_path / "replacement-media-root"
    replacement_root.mkdir()
    updated_path = update_shared_local_library_path(
        initialized_settings,
        value=str(replacement_root),
    )
    replacement_settings = replace(initialized_settings, media_root=(tmp_path / "ignored-bootstrap-root").resolve())

    updated_source_id = ensure_shared_local_library_source(replacement_settings)

    assert updated_source_id == initial_source_id
    assert updated_path == normalize_local_library_path(replacement_root)
    assert get_effective_shared_local_library_path(replacement_settings) == replacement_root.resolve()

    with get_connection(replacement_settings) as connection:
        row = connection.execute(
            """
            SELECT local_path
            FROM library_sources
            WHERE id = ?
            """,
            (updated_source_id,),
        ).fetchone()
        source_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM library_sources
            WHERE provider = ?
              AND resource_type = ?
              AND resource_id = ?
            """,
            (
                LOCAL_FILESYSTEM_PROVIDER,
                LOCAL_LIBRARY_RESOURCE_TYPE,
                SHARED_LOCAL_LIBRARY_RESOURCE_ID,
            ),
        ).fetchone()

    assert row is not None
    assert str(row["local_path"]) == normalize_local_library_path(replacement_root)
    assert int(source_count["count"]) == 1


def test_local_scan_backfills_existing_rows_and_binds_new_rows_to_shared_source(
    initialized_settings,
    monkeypatch,
) -> None:
    media_root = Path(initialized_settings.media_root)
    media_root.mkdir(parents=True, exist_ok=True)
    existing_file = media_root / "Who.Am.I.2014.mp4"
    existing_file.write_bytes(b"fake-movie-data")
    existing_stat = existing_file.stat()
    now = utcnow_iso()

    with get_connection(initialized_settings) as connection:
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
                "Who Am I",
                existing_file.name,
                str(existing_file.resolve()),
                int(existing_stat.st_size),
                float(existing_stat.st_mtime),
                2014,
                now,
                now,
                now,
            ),
        )
        existing_media_item_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        shared_source_id = ensure_shared_local_library_source(initialized_settings, connection=connection)
        updated_count = bind_unassigned_local_media_items_to_shared_source(
            connection,
            shared_source_id=shared_source_id,
            shared_local_path=initialized_settings.media_root,
        )
        connection.commit()

    assert updated_count == 1

    new_file = media_root / "Spider-Man.Homecoming.2017.mp4"
    new_file.write_bytes(b"new-movie-data")

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

    result = scan_media_library(initialized_settings, reason="manual")

    assert result["files_seen"] == 2
    assert result["files_removed"] == 0

    with get_connection(initialized_settings) as connection:
        shared_source_row = connection.execute(
            """
            SELECT id, local_path
            FROM library_sources
            WHERE provider = ?
              AND resource_type = ?
              AND resource_id = ?
            LIMIT 1
            """,
            (
                LOCAL_FILESYSTEM_PROVIDER,
                LOCAL_LIBRARY_RESOURCE_TYPE,
                SHARED_LOCAL_LIBRARY_RESOURCE_ID,
            ),
        ).fetchone()
        rows = connection.execute(
            """
            SELECT id, title, file_path, source_kind, library_source_id
            FROM media_items
            WHERE COALESCE(source_kind, 'local') = 'local'
            ORDER BY id ASC
            """
        ).fetchall()

    assert shared_source_row is not None
    assert str(shared_source_row["local_path"]) == normalize_local_library_path(initialized_settings.media_root)
    assert len(rows) == 2
    assert int(rows[0]["id"]) == existing_media_item_id
    assert int(rows[0]["library_source_id"]) == int(shared_source_row["id"])
    assert str(rows[1]["title"]) == "Spider-Man Homecoming"
    assert int(rows[1]["library_source_id"]) == int(shared_source_row["id"])


def test_private_local_library_resource_ids_are_owner_scoped() -> None:
    assert build_private_local_library_resource_id(user_id=7) == "user_private:7"
    assert build_private_local_library_resource_id(user_id=42) == "user_private:42"
