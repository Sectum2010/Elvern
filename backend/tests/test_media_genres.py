from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.db import get_connection, utcnow_iso
from backend.app.models import AuthenticatedUser
from backend.app.services.media_genre_service import (
    get_media_genre_metadata,
    media_item_ids_for_genre_group,
    normalize_genre_labels,
    set_media_genres,
)


def _insert_media_item(settings, *, title: str, original_filename: str, year: int) -> int:
    now = utcnow_iso()
    file_path = str((Path(settings.media_root) / original_filename).resolve())
    with get_connection(settings) as connection:
        cursor = connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                file_size,
                file_mtime,
                container,
                year,
                created_at,
                updated_at,
                last_scanned_at
            ) VALUES (?, ?, ?, 'local', 1, 1.0, 'mkv', ?, ?, ?, ?)
            """,
            (title, original_filename, file_path, year, now, now, now),
        )
        connection.commit()
        return int(cursor.lastrowid)


def _admin_actor(initialized_settings) -> AuthenticatedUser:
    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT id, username
            FROM users
            WHERE role = 'admin'
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()
    assert row is not None
    return AuthenticatedUser(
        id=int(row["id"]),
        username=str(row["username"]),
        role="admin",
        session_id=1,
    )


def test_genre_normalization_deduplicates_and_enforces_max_three() -> None:
    assert normalize_genre_labels([" Adventure ", "Family", "adventure", "Sci   Fi", ""]) == [
        "Adventure",
        "Family",
        "Sci Fi",
    ]

    with pytest.raises(HTTPException) as exc_info:
        normalize_genre_labels(["Action", "Adventure", "Comedy", "Drama"])

    assert exc_info.value.status_code == 400
    assert "up to 3 genres" in str(exc_info.value.detail)


def test_genre_applies_by_movie_group_identity_across_editions(initialized_settings) -> None:
    standard_id = _insert_media_item(
        initialized_settings,
        title="Blade Runner",
        original_filename="Blade.Runner.1982.mkv",
        year=1982,
    )
    directors_cut_id = _insert_media_item(
        initialized_settings,
        title="Blade Runner Director's Cut",
        original_filename="Blade.Runner.1982.Directors.Cut.mkv",
        year=1982,
    )
    actor = _admin_actor(initialized_settings)

    updated = set_media_genres(
        initialized_settings,
        item_id=standard_id,
        genres=["Sci-Fi", "Drama", "Sci-Fi"],
        actor=actor,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    directors_cut_metadata = get_media_genre_metadata(
        initialized_settings,
        directors_cut_id,
    )

    assert updated["genres"] == ["Sci-Fi", "Drama"]
    assert directors_cut_metadata["genres"] == ["Sci-Fi", "Drama"]
    assert directors_cut_metadata["genre_display"] == "Sci-Fi, Drama"
    assert media_item_ids_for_genre_group(
        initialized_settings,
        str(updated["genre_group_key"]),
    ) == [standard_id, directors_cut_id]
