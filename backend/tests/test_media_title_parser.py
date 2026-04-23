from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from backend.app.db import get_connection, utcnow_iso
from backend.app.services.media_title_parser import TITLE_PARSER_VERSION, parse_media_title
from backend.app.services.title_normalization import (
    clean_title_for_matching,
    resolve_poster_match_identity,
    resolve_title_metadata,
)


FIXTURE_PATH = Path(__file__).with_name("fixtures") / "media_title_parser_cases.json"
FIXTURE_CASES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
REPO_ROOT = Path(__file__).resolve().parents[2]
EDITION_PHRASE_MAP = {
    "director's cut": ["director", "cut"],
    "final cut": ["final", "cut"],
    "extended": ["extended"],
    "unrated": ["unrated"],
}


@pytest.mark.parametrize("case", FIXTURE_CASES, ids=[case["name"] for case in FIXTURE_CASES])
def test_parse_media_title_regressions(case) -> None:
    parsed = parse_media_title(
        title=case["title"],
        original_filename=case["original_filename"],
        year=case["year"],
    )

    assert parsed["display_title"] == case["expected_display_title"]
    assert parsed["base_title"] == case["expected_display_title"]
    assert parsed["edition_identity"] == case["expected_edition_identity"]
    assert parsed["parsed_year"] == case["expected_parsed_year"]
    assert parsed["poster_match_title"] == case.get("expected_poster_match_title", case["expected_display_title"])
    assert parsed["poster_match_year"] == case.get("expected_poster_match_year", case["expected_parsed_year"])
    assert parsed["poster_match_identity"]["title"] == case.get("expected_poster_match_title", case["expected_display_title"])
    assert parsed["poster_match_identity"]["year"] == case.get("expected_poster_match_year", case["expected_parsed_year"])
    assert parsed["poster_match_source"] in {"title", "original_filename", "stored_title", "fallback", None}
    assert parsed["poster_match_identity"]["source"] in {"title", "original_filename", "stored_title", "fallback", None}
    assert parsed["title_source"] in {"title", "original_filename", "stored_title", "fallback"}
    assert parsed["parse_confidence"] in {"high", "medium", "low"}
    assert isinstance(parsed["warnings"], list)
    assert parsed["parser_version"] == TITLE_PARSER_VERSION
    assert parsed["suspicious_output"] is False
    if case["expected_parsed_year"] is not None:
        assert str(case["expected_parsed_year"]) not in parsed["display_title"]
    for phrase in EDITION_PHRASE_MAP.get(case["expected_edition_identity"], []):
        assert phrase not in parsed["display_title"].lower()
    assert "1080p" not in parsed["display_title"].lower()
    assert "bluray" not in parsed["display_title"].lower()
    assert "tmdbid" not in parsed["display_title"].lower()
    assert "imdb" not in parsed["display_title"].lower()


def test_dirty_stored_title_does_not_beat_filename_source() -> None:
    parsed = parse_media_title(
        title="One Piece Film Strong World 1080p BluRay DDP 5 1 10bit H 265-iVy",
        original_filename="One Piece Film Strong World 1080p BluRay DDP 5 1 10bit H 265-iVy.mkv",
        year=None,
    )

    assert parsed["display_title"] == "One Piece Film Strong World"
    assert parsed["title_source"] == "original_filename"
    assert parsed["suspicious_output"] is False


def test_trusted_clean_title_beats_dirty_filename_when_available() -> None:
    parsed = parse_media_title(
        title="Ocean's Eleven",
        original_filename="Oceans.Eleven.2001.1080p.BluRay.Remux.mkv",
        year=2001,
    )

    assert parsed["display_title"] == "Ocean's Eleven"
    assert parsed["title_source"] == "title"
    assert parsed["poster_match_title"] == "Ocean's Eleven"
    assert parsed["poster_match_year"] == 2001
    assert parsed["poster_match_source"] == "title"
    assert parsed["poster_match_identity"] == {
        "title": "Ocean's Eleven",
        "year": 2001,
        "source": "title",
    }


@pytest.mark.parametrize(
    ("stored_title", "original_filename", "year", "expected_title", "expected_year"),
    [
        (
            "Harry Potter and the Deathly Hallows Part",
            "Harry.Potter.and.the.Deathly.Hallows.Part.1.2010.4K.UHD.2160p.REMUX.DV.DTS-HD.MA.7.1.Dual.PTBR-BrRemux.mkv",
            2010,
            "Harry Potter and the Deathly Hallows Part 1",
            2010,
        ),
        (
            "Harry Potter and the Deathly Hallows Part",
            "Harry.Potter.and.the.Deathly.Hallows.Part.2.2011.4K.UHD.2160p.REMUX.DV.DTS-HD.MA.7.1.Dual.PTBR-BrRemux.mkv",
            2011,
            "Harry Potter and the Deathly Hallows Part 2",
            2011,
        ),
        (
            "The Menu  iTA-ENG WEBDL 2160p HEVC HDR x265-CYBER",
            "The.Menu.2022.iTA-ENG.WEBDL.2160p.HEVC.HDR.x265-CYBER.mkv",
            2022,
            "The Menu",
            2022,
        ),
    ],
)
def test_live_row_like_dirty_titles_do_not_beat_cleaner_raw_sources(
    stored_title: str,
    original_filename: str,
    year: int,
    expected_title: str,
    expected_year: int,
) -> None:
    parsed = parse_media_title(
        title=stored_title,
        original_filename=original_filename,
        year=year,
    )
    poster_identity = resolve_poster_match_identity(
        title=stored_title,
        original_filename=original_filename,
        year=year,
    )

    assert parsed["display_title"] == expected_title
    assert parsed["title_source"] == "original_filename"
    assert parsed["poster_match_title"] == expected_title
    assert parsed["poster_match_year"] == expected_year
    assert parsed["poster_match_source"] == "original_filename"
    assert parsed["poster_match_identity"] == {
        "title": expected_title,
        "year": expected_year,
        "source": "original_filename",
    }
    assert poster_identity["title"] == expected_title
    assert poster_identity["year"] == expected_year
    assert poster_identity["source"] == "original_filename"


def test_title_normalization_wrappers_use_backend_parser() -> None:
    raw_value = "One Piece Stampede () [tmdbid-568012] - [Remux-1080p][TrueHD].mkv"

    cleaned_title = clean_title_for_matching(raw_value, None)
    metadata = resolve_title_metadata(
        title=None,
        year=None,
        original_filename=raw_value,
    )
    poster_identity = resolve_poster_match_identity(
        title=None,
        year=None,
        original_filename=raw_value,
    )

    assert cleaned_title == "One Piece Stampede"
    assert metadata["display_title"] == "One Piece Stampede"
    assert metadata["base_title"] == "One Piece Stampede"
    assert metadata["poster_match_title"] == "One Piece Stampede"
    assert metadata["poster_match_year"] is None
    assert metadata["edition_identity"] == "standard"
    assert metadata["title_source"] == "original_filename"
    assert metadata["parsed_year"] is None
    assert poster_identity["title"] == "One Piece Stampede"
    assert poster_identity["year"] is None
    assert poster_identity["source"] == "original_filename"


def test_suspicious_output_is_flagged_for_hopeless_metadata_only_input() -> None:
    parsed = parse_media_title(
        title=None,
        original_filename="2160p.BluRay.REMUX.TrueHD.Atmos-FraMeSToR.mkv",
        year=None,
    )

    assert parsed["suspicious_output"] is True
    assert parsed["parser_version"] == TITLE_PARSER_VERSION
    assert any(
        warning.startswith("display_title_contains_") or warning == "display_title_implausibly_short"
        for warning in parsed["warnings"]
    )


def test_title_diagnostics_script_snapshot_output_is_stable(
    initialized_settings,
) -> None:
    now = utcnow_iso()
    with get_connection(initialized_settings) as connection:
        connection.execute(
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
            ) VALUES (?, ?, ?, 'local', ?, ?, NULL, NULL, NULL, NULL, NULL, 'mkv', ?, ?, ?, ?)
            """,
            (
                "Harry Potter and the Deathly Hallows Part",
                "Harry.Potter.and.the.Deathly.Hallows.Part.1.2010.4K.UHD.2160p.REMUX.DV.DTS-HD.MA.7.1.Dual.PTBR-BrRemux.mkv",
                str(initialized_settings.media_root / "Harry.Potter.and.the.Deathly.Hallows.Part.1.2010.4K.UHD.2160p.REMUX.DV.DTS-HD.MA.7.1.Dual.PTBR-BrRemux.mkv"),
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
            ) VALUES (?, ?, ?, 'local', ?, ?, NULL, NULL, NULL, NULL, NULL, 'mkv', ?, ?, ?, ?)
            """,
            (
                "Blade Runner 2049.2017.2160p.UHD.BluRay.REMUX",
                "Blade Runner 2049.2017.2160p.UHD.BluRay.REMUX.mkv",
                str(initialized_settings.media_root / "Blade Runner 2049.2017.2160p.UHD.BluRay.REMUX.mkv"),
                1,
                1.0,
                None,
                now,
                now,
                now,
            ),
        )
        connection.commit()

    env = os.environ.copy()
    command = [
        sys.executable,
        "scripts/elvern-title-diagnostics.py",
        "--source-kind",
        "all",
        "--limit",
        "10",
        "--snapshot",
    ]

    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["parser_version"] == TITLE_PARSER_VERSION
    assert payload["filters"] == {
        "source_kind": "all",
        "only_suspicious": False,
        "limit": 10,
    }
    assert payload["summary"]["rows_checked"] >= 2
    assert payload["summary"]["rows_reported"] >= 2
    assert payload["summary"]["suspicious_rows"] <= payload["summary"]["rows_reported"]
    assert isinstance(payload["rows"], list)
    row_ids = [row["id"] for row in payload["rows"]]
    assert row_ids == sorted(row_ids)
    for row in payload["rows"]:
        assert sorted(row.keys()) == [
            "display_title",
            "display_title_changed",
            "id",
            "original_filename",
            "parse_confidence",
            "parser_version",
            "poster_match_identity",
            "source_kind",
            "stored_title",
            "stored_year",
            "suspicious_output",
            "title_source",
            "warnings",
        ]
        assert row["parser_version"] == TITLE_PARSER_VERSION
        assert sorted(row["poster_match_identity"].keys()) == ["title", "year"]

    rows_by_filename = {row["original_filename"]: row for row in payload["rows"]}
    assert rows_by_filename[
        "Harry.Potter.and.the.Deathly.Hallows.Part.1.2010.4K.UHD.2160p.REMUX.DV.DTS-HD.MA.7.1.Dual.PTBR-BrRemux.mkv"
    ]["display_title"] == "Harry Potter and the Deathly Hallows Part 1"
    assert rows_by_filename[
        "Harry.Potter.and.the.Deathly.Hallows.Part.1.2010.4K.UHD.2160p.REMUX.DV.DTS-HD.MA.7.1.Dual.PTBR-BrRemux.mkv"
    ]["poster_match_identity"] == {
        "title": "Harry Potter and the Deathly Hallows Part 1",
        "year": 2010,
    }
    assert rows_by_filename[
        "Blade Runner 2049.2017.2160p.UHD.BluRay.REMUX.mkv"
    ]["display_title"] == "Blade Runner 2049"
    assert rows_by_filename[
        "Blade Runner 2049.2017.2160p.UHD.BluRay.REMUX.mkv"
    ]["poster_match_identity"] == {
        "title": "Blade Runner 2049",
        "year": 2017,
    }
