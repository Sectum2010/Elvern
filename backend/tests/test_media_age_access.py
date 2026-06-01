from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.auth import authenticate_user
from backend.app.db import get_connection, utcnow_iso
from backend.app.services.admin_service import create_user
from backend.app.services.media_age_access_service import (
    AGE_ACCESS_DENIED_18,
    assert_user_can_access_media_by_age,
    link_media_item_to_age_group,
    list_age_group_members,
    list_age_groups_for_admin,
    normalize_age_group_identity_title,
    revoke_persistent_sessions_for_age_group,
    revoke_persistent_sessions_for_user_age_change,
    resolve_age_restriction_movie_group,
    resolve_effective_age_group,
    resolve_media_age_requirement,
    set_media_age_requirement,
    unlink_media_item_from_age_group,
)
from backend.app.services.library_movie_identity_service import _dedupe_group_key


def _age_group_key(
    title: str,
    *,
    year: int | None,
    original_filename: str | None = None,
    item_id: int = 1,
) -> str:
    return resolve_age_restriction_movie_group({
        "id": item_id,
        "title": title,
        "year": year,
        "original_filename": original_filename or f"{title}.mkv",
        "source_kind": "local",
    }).age_group_key


def _admin_user(settings):
    user, failure_reason = authenticate_user(
        settings,
        settings.admin_username,
        settings.admin_bootstrap_password or "",
    )
    assert failure_reason is None
    assert user is not None
    return user


def _login(client, *, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


def _create_media_item(
    settings,
    *,
    title: str,
    original_filename: str,
    year: int | None,
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
                video_codec,
                audio_codec,
                container,
                year,
                created_at,
                updated_at,
                last_scanned_at
            ) VALUES (?, ?, ?, 'local', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                media_file.name,
                str(media_file),
                media_file.stat().st_size,
                media_file.stat().st_mtime,
                120.0,
                "h264",
                "aac",
                "mp4",
                year,
                now,
                now,
                now,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def test_age_group_uses_conservative_title_year_without_aliasing() -> None:
    standard = resolve_age_restriction_movie_group({
        "id": 1,
        "title": "The Super Mario Galaxy Movie",
        "year": 2026,
        "original_filename": "The.Super.Mario.Galaxy.Movie.2026.1080p.mkv",
    })
    director_cut = resolve_age_restriction_movie_group({
        "id": 2,
        "title": "The Super Mario Galaxy Movie - Director's Cut",
        "year": 2026,
        "original_filename": "The.Super.Mario.Galaxy.Movie.2026.Directors.Cut.2160p.mkv",
    })
    sorcerer = resolve_age_restriction_movie_group({
        "id": 3,
        "title": "Harry Potter and the Sorcerer's Stone",
        "year": 2001,
        "original_filename": "Harry.Potter.and.the.Sorcerers.Stone.2001.mkv",
    })
    philosopher = resolve_age_restriction_movie_group({
        "id": 4,
        "title": "Harry Potter and the Philosopher's Stone",
        "year": 2001,
        "original_filename": "Harry.Potter.and.the.Philosophers.Stone.2001.mkv",
    })
    no_year = resolve_age_restriction_movie_group({
        "id": 5,
        "title": "Camera Dump",
        "year": None,
        "original_filename": "MVI_0123.mkv",
    })

    assert standard.age_group_key == director_cut.age_group_key
    assert standard.source == "title_year"
    assert sorcerer.age_group_key != philosopher.age_group_key
    assert no_year.age_group_key == "age:item:5"
    assert no_year.source == "item_fallback"


def test_age_group_ignores_safe_quality_and_release_noise() -> None:
    baseline = _age_group_key(
        "The Super Mario Galaxy Movie",
        year=2026,
        original_filename="The.Super.Mario.Galaxy.Movie.2026.1080p.WEB-DL.mkv",
    )
    noisy = _age_group_key(
        "The Super Mario Galaxy Movie",
        year=2026,
        original_filename="The.Super.Mario.Galaxy.Movie.2026.2160p.UHD.BluRay.HDR10.Atmos-GALAXY.mkv",
        item_id=2,
    )

    assert baseline == noisy


def test_age_group_ignores_safe_movie_editions_for_same_title_year() -> None:
    baseline = _age_group_key("Blade Runner", year=1982)
    cases = [
        "Blade Runner Theatrical Cut",
        "Blade Runner Extended Edition",
        "Blade Runner Director Cut",
        "Blade Runner Final Cut",
        "Blade Runner Unrated",
    ]

    for index, title in enumerate(cases, start=2):
        assert _age_group_key(title, year=1982, item_id=index) == baseline


def test_age_group_normalizes_safe_modifier_examples() -> None:
    examples = [
        (
            "Mad Max: Fury Road (2015) Black & Chrome Edition",
            "Mad.Max.Fury.Road.2015.Theatrical.1080p.WEBRip.x264-WITNESS",
            2015,
        ),
        (
            "The Godfather (1972) Coppola Restoration",
            "Godfather.1972.REMASTERED.4K.UHD.BluRay.TrueHD-GFATHER",
            1972,
        ),
        (
            "Jurassic Park (1993) 25th Anniversary Edition",
            "Jurassic.Park.1993.3D.1080p.BluRay.x264-DINO",
            1993,
        ),
        (
            "Avengers: Endgame (2019) Bonus Edition",
            "Avengers.Endgame.2019.2160p.UHD.BluRay.HDR10-MARVEL",
            2019,
        ),
        (
            "Star Wars: Episode IV - A New Hope (1977) Despecialized Edition",
            "Star.Wars.Episode.4.A.New.Hope.1977.1997.Special.Edition.1080p.BluRay",
            1977,
        ),
    ]

    for index, (left, right, year) in enumerate(examples, start=10):
        assert _age_group_key(left, year=year, item_id=index) == _age_group_key(
            right,
            year=year,
            item_id=index + 100,
        )


def test_age_group_normalizes_safe_numbered_contexts() -> None:
    assert _age_group_key("Star Wars Episode IV A New Hope", year=1977) == _age_group_key(
        "Star Wars Episode 4 A New Hope",
        year=1977,
        item_id=2,
    )
    assert _age_group_key("Dune: Part Two", year=2024, item_id=3) == _age_group_key(
        "Dune.Part.2.2024.2160p.UHD.BluRay.x265-ARRAKIS",
        year=2024,
        item_id=4,
    )
    assert _age_group_key("Guardians of the Galaxy Vol. 2", year=2017, item_id=5) == _age_group_key(
        "Guardians.Of.The.Galaxy.Volume.Two.2017.1080p.BluRay.DTS-MIXTAPE",
        year=2017,
        item_id=6,
    )


def test_age_group_does_not_global_convert_final_roman_numerals() -> None:
    assert _age_group_key("Project V", year=2024) != _age_group_key("Project 5", year=2024, item_id=2)
    assert _age_group_key("Malcolm X", year=1992, item_id=3) != _age_group_key("Malcolm 10", year=1992, item_id=4)
    assert _age_group_key("Episode IV", year=2024, item_id=5) == _age_group_key(
        "Episode 4",
        year=2024,
        item_id=6,
    )


def test_age_group_strips_3d_only_with_metadata_context() -> None:
    assert normalize_age_group_identity_title("Project 3D", year=2024) == "project 3d"
    assert normalize_age_group_identity_title(
        "Jurassic Park 3D",
        year=1993,
        metadata={"warnings": ["metadata_suffix_removed"]},
    ) == "jurassic park"


def test_age_group_keeps_out_of_scope_aliases_separate() -> None:
    assert _age_group_key("LOTR Return Of The King", year=2003) != _age_group_key(
        "The Lord of the Rings: The Return of the King",
        year=2003,
        item_id=2,
    )
    assert _age_group_key("Harry Potter and the Sorcerer's Stone", year=2001, item_id=3) != _age_group_key(
        "Harry Potter and the Philosopher's Stone",
        year=2001,
        item_id=4,
    )
    assert _age_group_key("Raiders of the Lost Ark", year=1981, item_id=5) != _age_group_key(
        "Indiana Jones: Raiders of the Lost Ark",
        year=1981,
        item_id=6,
    )
    assert _age_group_key("Fast.and.Furious.3.Tokyo.Drift.2006.720p.BluRay", year=2006, item_id=7) != _age_group_key(
        "The Fast and the Furious: Tokyo Drift",
        year=2006,
        item_id=8,
    )


def test_age_group_uses_item_fallback_for_missing_year_or_unsafe_parse() -> None:
    missing_year = resolve_age_restriction_movie_group({
        "id": 11,
        "title": "Camera Dump",
        "year": None,
        "original_filename": "MVI_0123.mkv",
    })
    suspicious = resolve_age_restriction_movie_group({
        "id": 12,
        "title": "2026",
        "year": None,
        "original_filename": "2026.mkv",
    })

    assert missing_year.age_group_key == "age:item:11"
    assert suspicious.age_group_key == "age:item:12"


def test_age_group_ignores_edition_identity_without_changing_local_dedupe() -> None:
    standard_row = {
        "title": "The Super Mario Galaxy Movie",
        "year": 2026,
        "original_filename": "The.Super.Mario.Galaxy.Movie.2026.1080p.mkv",
        "source_kind": "local",
    }
    extended_row = {
        "title": "The Super Mario Galaxy Movie Extended Cut",
        "year": 2026,
        "original_filename": "The.Super.Mario.Galaxy.Movie.2026.Extended.Cut.1080p.mkv",
        "source_kind": "local",
    }

    assert _age_group_key(standard_row["title"], year=2026, original_filename=standard_row["original_filename"]) == _age_group_key(
        extended_row["title"],
        year=2026,
        original_filename=extended_row["original_filename"],
        item_id=2,
    )
    assert _dedupe_group_key(standard_row) != _dedupe_group_key(extended_row)


def test_manual_age_group_link_overrides_age_group_without_touching_dedupe(initialized_settings) -> None:
    admin_user = _admin_user(initialized_settings)
    source_item_id = _create_media_item(
        initialized_settings,
        title="LOTR Return Of The King",
        original_filename="LOTR.Return.Of.The.King.2003.mkv",
        year=2003,
    )
    target_item_id = _create_media_item(
        initialized_settings,
        title="The Lord of the Rings: The Return of the King",
        original_filename="The.Lord.of.the.Rings.The.Return.of.the.King.2003.mkv",
        year=2003,
    )

    source_auto = resolve_effective_age_group(initialized_settings, source_item_id)
    target_auto = resolve_effective_age_group(initialized_settings, target_item_id)
    assert source_auto["age_group_key"] != target_auto["age_group_key"]

    linked = link_media_item_to_age_group(
        initialized_settings,
        target_media_item_id=target_item_id,
        source_item_id=source_item_id,
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    target_effective = resolve_effective_age_group(initialized_settings, target_item_id)
    assert target_effective["age_group_key"] == source_auto["age_group_key"]
    assert target_effective["manual_link"]["media_item_id"] == target_item_id
    assert linked["age_group"]["manual_links_count"] == 1
    members = list_age_group_members(initialized_settings, str(source_auto["age_group_key"]))
    assert {item["id"] for item in members["manual_linked_copies"]} == {target_item_id}
    assert _dedupe_group_key({
        "title": "LOTR Return Of The King",
        "year": 2003,
        "original_filename": "LOTR.Return.Of.The.King.2003.mkv",
        "source_kind": "local",
    }) != _dedupe_group_key({
        "title": "The Lord of the Rings: The Return of the King",
        "year": 2003,
        "original_filename": "The.Lord.of.the.Rings.The.Return.of.the.King.2003.mkv",
        "source_kind": "local",
    })

    unlinked = unlink_media_item_from_age_group(
        initialized_settings,
        target_media_item_id=target_item_id,
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    assert unlinked["linked"] is False
    assert resolve_effective_age_group(initialized_settings, target_item_id)["age_group_key"] == target_auto["age_group_key"]
    with get_connection(initialized_settings) as connection:
        actions = [
            row["action"]
            for row in connection.execute(
                "SELECT action FROM audit_logs WHERE action LIKE 'admin.media_age_group.%' ORDER BY id"
            ).fetchall()
        ]
    assert actions == ["admin.media_age_group.link", "admin.media_age_group.unlink"]


def test_admin_age_group_routes_are_admin_only(client, initialized_settings, admin_credentials) -> None:
    source_item_id = _create_media_item(
        initialized_settings,
        title="Admin Age Group Movie",
        original_filename="Admin.Age.Group.Movie.2026.mkv",
        year=2026,
    )
    target_item_id = _create_media_item(
        initialized_settings,
        title="Admin Age Group Movie Extended Cut",
        original_filename="Admin.Age.Group.Movie.2026.Extended.Cut.mkv",
        year=2026,
    )

    unauthenticated = client.get("/api/library/age-groups")
    assert unauthenticated.status_code == 401

    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    groups_response = client.get("/api/library/age-groups")
    assert groups_response.status_code == 200
    groups = groups_response.json()["items"]
    assert any(group["display_title"] == "Admin Age Group Movie" for group in groups)

    source_group = resolve_effective_age_group(initialized_settings, source_item_id)["age_group_key"]
    link_response = client.post(
        "/api/library/age-groups/link",
        json={
            "age_group_key": source_group,
            "target_media_item_id": target_item_id,
        },
    )
    assert link_response.status_code == 200
    assert link_response.json()["linked"] is True

    group_response = client.get(f"/api/library/age-groups/{source_group}")
    assert group_response.status_code == 200
    assert group_response.json()["manual_links_count"] == 1

    unlink_response = client.delete(f"/api/library/age-groups/links/{target_item_id}")
    assert unlink_response.status_code == 200
    assert unlink_response.json()["linked"] is False


def test_age_requirement_denies_standard_user_and_admin_bypasses(initialized_settings) -> None:
    admin_user = _admin_user(initialized_settings)
    item_id = _create_media_item(
        initialized_settings,
        title="Age Gate Movie",
        original_filename="Age.Gate.Movie.2026.mkv",
        year=2026,
    )
    create_user(
        initialized_settings,
        username="age-gated-user",
        password="family-password",
        role="standard_user",
        enabled=True,
        age_credential=12,
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    standard_user, failure_reason = authenticate_user(
        initialized_settings,
        "age-gated-user",
        "family-password",
    )
    assert failure_reason is None
    assert standard_user is not None

    updated = set_media_age_requirement(
        initialized_settings,
        item_id=item_id,
        age_requirement=18,
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    assert updated["age_requirement"] == 18
    assert updated["age_requirement_display"] == "18+"
    assert resolve_media_age_requirement(initialized_settings, item_id)["age_requirement"] == 18

    with pytest.raises(HTTPException) as exc:
        assert_user_can_access_media_by_age(
            initialized_settings,
            user=standard_user,
            item_id=item_id,
            purpose="playback",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == AGE_ACCESS_DENIED_18

    assert_user_can_access_media_by_age(
        initialized_settings,
        user=admin_user,
        item_id=item_id,
        purpose="playback",
    )


def test_users_default_to_adult_age_credential(initialized_settings) -> None:
    admin_user = _admin_user(initialized_settings)
    created = create_user(
        initialized_settings,
        username="default-age-user",
        password="family-password",
        role="standard_user",
        enabled=True,
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    assert admin_user.age_credential == 18
    assert created["age_credential"] == 18
    assert created["age_credential_display"] == "18+"


def test_age_requirement_change_revokes_disallowed_persistent_sessions(initialized_settings) -> None:
    admin_user = _admin_user(initialized_settings)
    user_payload = create_user(
        initialized_settings,
        username="age-revoke-user",
        password="family-password",
        role="standard_user",
        enabled=True,
        age_credential=12,
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    item_id = _create_media_item(
        initialized_settings,
        title="Revoked Age Movie",
        original_filename="Revoked.Age.Movie.2026.mkv",
        year=2026,
    )
    updated = set_media_age_requirement(
        initialized_settings,
        item_id=item_id,
        age_requirement=16,
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    now = utcnow_iso()
    with get_connection(initialized_settings) as connection:
        connection.execute(
            """
            INSERT INTO native_playback_sessions (
                session_id, access_token_hash, user_id, media_item_id,
                created_at, expires_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("native-age-session", "native-hash", user_payload["id"], item_id, now, "2999-01-01T00:00:00+00:00", now),
        )
        connection.execute(
            """
            INSERT INTO download_sessions (
                session_token_hash, user_id, media_item_id, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("download-age-hash", user_payload["id"], item_id, now, "2999-01-01T00:00:00+00:00"),
        )
        connection.execute(
            """
            INSERT INTO desktop_vlc_handoffs (
                handoff_id, access_token_hash, user_id, media_item_id,
                platform, strategy, resolved_target, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "desktop-age-handoff",
                "desktop-hash",
                user_payload["id"],
                item_id,
                "linux",
                "stream_url",
                "http://example.invalid/movie",
                now,
                "2999-01-01T00:00:00+00:00",
            ),
        )
        connection.commit()

    summary = revoke_persistent_sessions_for_age_group(
        initialized_settings,
        age_group_key=str(updated["age_group_key"]),
        age_requirement=16,
        reason="age_requirement_changed",
    )

    assert summary["media_item_ids"] == [item_id]
    assert summary["user_ids"] == [user_payload["id"]]
    assert summary["revoked_native"] == 1
    assert summary["revoked_downloads"] == 1
    assert summary["revoked_desktop"] == 1
    with get_connection(initialized_settings) as connection:
        native_row = connection.execute("SELECT revoked_at, last_error FROM native_playback_sessions").fetchone()
        download_row = connection.execute("SELECT revoked_at, last_error FROM download_sessions").fetchone()
        desktop_row = connection.execute("SELECT revoked_at FROM desktop_vlc_handoffs").fetchone()
    assert native_row["revoked_at"] is not None
    assert native_row["last_error"] == "age_requirement_changed"
    assert download_row["revoked_at"] is not None
    assert download_row["last_error"] == "age_requirement_changed"
    assert desktop_row["revoked_at"] is not None


def test_user_age_credential_change_revokes_newly_disallowed_sessions(initialized_settings) -> None:
    admin_user = _admin_user(initialized_settings)
    user_payload = create_user(
        initialized_settings,
        username="age-credential-revoke-user",
        password="family-password",
        role="standard_user",
        enabled=True,
        age_credential=18,
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    item_id = _create_media_item(
        initialized_settings,
        title="Credential Revoke Movie",
        original_filename="Credential.Revoke.Movie.2026.mkv",
        year=2026,
    )
    set_media_age_requirement(
        initialized_settings,
        item_id=item_id,
        age_requirement=18,
        actor=admin_user,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    now = utcnow_iso()
    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE users SET age_credential = 12 WHERE id = ?",
            (user_payload["id"],),
        )
        connection.execute(
            """
            INSERT INTO download_sessions (
                session_token_hash, user_id, media_item_id, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("download-age-change-hash", user_payload["id"], item_id, now, "2999-01-01T00:00:00+00:00"),
        )
        connection.commit()

    summary = revoke_persistent_sessions_for_user_age_change(
        initialized_settings,
        user_id=int(user_payload["id"]),
        reason="user_age_credential_changed",
    )

    assert summary["media_item_ids"] == [item_id]
    assert summary["user_ids"] == [user_payload["id"]]
    assert summary["revoked_downloads"] == 1
    with get_connection(initialized_settings) as connection:
        row = connection.execute("SELECT revoked_at, last_error FROM download_sessions").fetchone()
    assert row["revoked_at"] is not None
    assert row["last_error"] == "user_age_credential_changed"
