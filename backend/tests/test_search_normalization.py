from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.db import get_connection, utcnow_iso
from backend.app.services.title_normalization import build_search_index, match_search_query


def _login(client, *, username: str, password: str) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200


def _insert_media_item(
    settings,
    *,
    title: str,
    original_filename: str,
) -> int:
    media_file = Path(settings.media_root) / original_filename
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(b"not a real media file")
    now = utcnow_iso()
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
            ) VALUES (?, ?, ?, 'local', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                media_file.name,
                str(media_file),
                media_file.stat().st_size,
                media_file.stat().st_mtime,
                120.0,
                None,
                None,
                "h264",
                "aac",
                "mp4",
                2014,
                now,
                now,
                now,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


@pytest.mark.parametrize(
    ("title", "original_filename", "query"),
    [
        ("Who Am I", "Who.Am.I.2014.mkv", "who am i"),
        ("Who Am I", "Who.Am.I.2014.mkv", "whoami"),
        ("Spider-Man: Homecoming", "Spider-Man.Homecoming.2017.mkv", "spidermanhomecoming"),
        ("Ocean's Eleven", "Ocean's.Eleven.2001.mkv", "oceans eleven"),
        ("Ocean's Eleven", "Ocean's.Eleven.2001.mkv", "oceanseleven"),
    ],
)
def test_match_search_query_supports_compact_form_variants(
    title: str,
    original_filename: str,
    query: str,
) -> None:
    matched, score = match_search_query(
        query=query,
        search_index=build_search_index(
            title=title,
            year=2014,
            original_filename=original_filename,
        ),
    )

    assert matched is True
    assert score > 0


@pytest.mark.parametrize(
    ("title", "original_filename", "query"),
    [
        ("Who Am I", "Who.Am.I.2014.mkv", "whoareyou"),
        ("Ocean's Eleven", "Ocean's.Eleven.2001.mkv", "octopus"),
    ],
)
def test_match_search_query_keeps_unrelated_compact_strings_out(
    title: str,
    original_filename: str,
    query: str,
) -> None:
    matched, score = match_search_query(
        query=query,
        search_index=build_search_index(
            title=title,
            year=2014,
            original_filename=original_filename,
        ),
    )

    assert matched is False
    assert score == 0


def test_library_search_route_matches_compact_query(client, initialized_settings, admin_credentials) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )
    who_am_i_id = _insert_media_item(
        initialized_settings,
        title="Who Am I",
        original_filename="Who.Am.I.2014.mkv",
    )
    _insert_media_item(
        initialized_settings,
        title="Ocean's Eleven",
        original_filename="Ocean's.Eleven.2001.mkv",
    )

    response = client.get("/api/library/search", params={"q": "whoami"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_items"] == 1
    assert [item["id"] for item in payload["items"]] == [who_am_i_id]
    assert [item["title"] for item in payload["items"]] == ["Who Am I"]
