from __future__ import annotations

from pathlib import Path

from backend.app.db import get_connection
from backend.app.media_scan import scan_media_library
from backend.app.services.library_folder_classifier import (
    discover_library_folders,
    parse_folder_suffixes,
)
from backend.app.services.local_library_source_service import (
    get_library_reference_category_summary,
)


VIDEO_EXTENSIONS = {".mkv", ".mp4"}


def test_suffix_parser_recognizes_supported_suffixes_and_cleans_display_names() -> None:
    expectations = [
        ("Movies -M", ("M",), "Movies", "movies", None),
        ("Movies-M", ("M",), "Movies", "movies", None),
        ("Movies   -M", ("M",), "Movies", "movies", None),
        ("TV Shows -TV", ("TV",), "TV Shows", "tv", None),
        ("TV Shows-TV", ("TV",), "TV Shows", "tv", None),
        ("Anime -AN", ("AN",), "Anime", "anime", None),
        ("Anime-AN", ("AN",), "Anime", "anime", None),
        ("Cartoon -C", ("C",), "Cartoon", "cartoon", None),
        ("Cartoon-C", ("C",), "Cartoon", "cartoon", None),
        ("Resident Evil -L", ("L",), "Resident Evil", None, "list"),
        ("Resident Evil-L", ("L",), "Resident Evil", None, "list"),
        ("Single Movie -S", ("S",), "Single Movie", None, "single"),
        ("Single Movie-S", ("S",), "Single Movie", None, "single"),
        ("Ignore Me -X", ("X",), "Ignore Me", None, None),
        ("Ignore Me-X", ("X",), "Ignore Me", None, None),
    ]

    for folder_name, suffixes, display_name, category, role in expectations:
        parsed = parse_folder_suffixes(folder_name)

        assert parsed.recognized_suffixes == suffixes
        assert parsed.display_name == display_name
        assert parsed.explicit_category == category
        assert parsed.explicit_role == role


def test_suffix_parser_only_recognizes_one_final_suffix() -> None:
    expectations = [
        ("Resident Evil -M -L", ("L",), "Resident Evil -M", None, "list"),
        ("Resident Evil-M-L", ("L",), "Resident Evil-M", None, "list"),
        ("Resident Evil -M-L", ("L",), "Resident Evil -M", None, "list"),
        ("Resident Evil-M -L", ("L",), "Resident Evil-M", None, "list"),
        ("Title -L -M", ("M",), "Title -L", "movies", None),
        ("Title-L-M", ("M",), "Title-L", "movies", None),
    ]

    for folder_name, suffixes, display_name, category, role in expectations:
        parsed = parse_folder_suffixes(folder_name)

        assert parsed.recognized_suffixes == suffixes
        assert parsed.display_name == display_name
        assert parsed.explicit_category == category
        assert parsed.explicit_role == role


def test_suffix_parser_preserves_unsupported_g_suffix() -> None:
    unsupported_spaced = parse_folder_suffixes("Genres -G")
    unsupported_compact = parse_folder_suffixes("Genres-G")

    assert unsupported_spaced.recognized_suffixes == ()
    assert unsupported_spaced.unknown_suffixes == ("G",)
    assert unsupported_spaced.display_name == "Genres -G"
    assert unsupported_compact.recognized_suffixes == ()
    assert unsupported_compact.unknown_suffixes == ("G",)
    assert unsupported_compact.display_name == "Genres-G"


def test_summary_uses_empty_category_folders_without_media_rows(initialized_settings) -> None:
    media_root = Path(initialized_settings.media_root)
    folders = {
        "movies": media_root / "Movies -M",
        "tv": media_root / "TV Shows-TV",
        "cartoon": media_root / "Cartoon -C",
        "anime": media_root / "Anime-AN",
    }
    for folder in folders.values():
        folder.mkdir(parents=True)

    with get_connection(initialized_settings) as connection:
        media_item_count = connection.execute(
            "SELECT COUNT(*) FROM media_items"
        ).fetchone()[0]
        summary = get_library_reference_category_summary(
            initialized_settings,
            connection=connection,
        )

    assert media_item_count == 0
    assert summary == {
        "movies": [{"path": str(folders["movies"].resolve()), "name": "Movies"}],
        "tv": [{"path": str(folders["tv"].resolve()), "name": "TV Shows"}],
        "cartoon": [{"path": str(folders["cartoon"].resolve()), "name": "Cartoon"}],
        "anime": [{"path": str(folders["anime"].resolve()), "name": "Anime"}],
    }


def test_summary_keeps_compact_category_folders(initialized_settings) -> None:
    media_root = Path(initialized_settings.media_root)
    folders = {
        "movies": media_root / "Movies-M",
        "tv": media_root / "TV Shows-TV",
        "cartoon": media_root / "Cartoon-C",
        "anime": media_root / "Anime-AN",
    }
    for folder in folders.values():
        folder.mkdir(parents=True)

    summary = get_library_reference_category_summary(initialized_settings)

    assert summary == {
        "movies": [{"path": str(folders["movies"].resolve()), "name": "Movies"}],
        "tv": [{"path": str(folders["tv"].resolve()), "name": "TV Shows"}],
        "cartoon": [{"path": str(folders["cartoon"].resolve()), "name": "Cartoon"}],
        "anime": [{"path": str(folders["anime"].resolve()), "name": "Anime"}],
    }


def test_summary_excludes_poster_reference_and_x_category_folders(initialized_settings) -> None:
    media_root = Path(initialized_settings.media_root)
    poster_root = media_root / "Posters"
    poster_root.mkdir(parents=True)
    (poster_root / "Poster Category-C").mkdir()
    excluded_folder = media_root / "Ignore Me-X" / "Hidden Cartoon-C"
    excluded_folder.mkdir(parents=True)

    summary = get_library_reference_category_summary(initialized_settings)

    assert summary == {
        "movies": [],
        "tv": [],
        "cartoon": [],
        "anime": [],
    }


def test_discovery_inherits_category_and_excludes_x_subtrees(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    franchise = root / "Movies -M" / "Resident Evil -L"
    excluded = root / "Movies -M" / "Do Not Scan -X"
    franchise.mkdir(parents=True)
    excluded.mkdir(parents=True)
    (franchise / "Resident.Evil.2002.mkv").write_bytes(b"movie")
    (excluded / "Hidden.Movie.2020.mkv").write_bytes(b"hidden")

    discovery = discover_library_folders(
        [root],
        allowed_video_extensions=VIDEO_EXTENSIONS,
    )

    assert [file.path.name for file in discovery.files] == ["Resident.Evil.2002.mkv"]
    metadata = discovery.files[0].metadata
    assert metadata.category == "movies"
    assert metadata.category_display_name == "Movies"
    assert metadata.series_folder_name == "Resident Evil"
    assert metadata.role == "list"
    assert str(excluded.resolve()) in discovery.excluded_paths


def test_discovery_inherits_nested_explicit_list_identity(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    list_folder = root / "TV Shows -TV" / "Hannibal (SE1~3) [1080p]-L"
    season_one = list_folder / "Hannibal Season 1"
    season_two = list_folder / "Hannibal Season 2"
    season_three = list_folder / "Hannibal Season 3"
    season_one.mkdir(parents=True)
    season_two.mkdir(parents=True)
    season_three.mkdir(parents=True)
    (season_one / "Hannibal S01E01.mkv").write_bytes(b"episode-one")
    (season_two / "Hannibal S02E01.mkv").write_bytes(b"episode-two")
    (season_three / "Hannibal S03E01.mkv").write_bytes(b"episode-three")

    discovery = discover_library_folders(
        [root],
        allowed_video_extensions=VIDEO_EXTENSIONS,
    )

    assert sorted(file.path.name for file in discovery.files) == [
        "Hannibal S01E01.mkv",
        "Hannibal S02E01.mkv",
        "Hannibal S03E01.mkv",
    ]
    series_keys = {file.metadata.series_folder_key for file in discovery.files}
    assert len(series_keys) == 1
    assert next(iter(series_keys), "").startswith("local-folder:")
    for file in discovery.files:
        assert file.metadata.category == "tv"
        assert file.metadata.category_display_name == "TV Shows"
        assert file.metadata.series_folder_name == "Hannibal (SE1~3) [1080p]"
        assert file.metadata.folder_display_name.startswith("Hannibal Season")


def test_discovery_excludes_configured_poster_reference_path(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    media_folder = root / "Movies -M" / "Movie One -S"
    poster_folder = root / "Movies -M" / "Posters"
    media_folder.mkdir(parents=True)
    poster_folder.mkdir(parents=True)
    (media_folder / "Movie.One.2020.mp4").write_bytes(b"movie")
    (poster_folder / "Poster.Reference.Clip.mp4").write_bytes(b"poster-video")

    discovery = discover_library_folders(
        [root],
        allowed_video_extensions=VIDEO_EXTENSIONS,
        poster_reference_path=poster_folder,
    )

    assert [file.path.name for file in discovery.files] == ["Movie.One.2020.mp4"]
    assert str(poster_folder.resolve()) in discovery.excluded_paths


def test_discovery_marks_smart_single_movie_folder_and_warns_for_explicit_s_multi_video(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    smart = root / "Smart Single"
    explicit = root / "Crowded -S"
    smart.mkdir(parents=True)
    explicit.mkdir(parents=True)
    (smart / "Smart.Movie.2020.mkv").write_bytes(b"movie")
    (smart / "Smart.Movie.2020.srt").write_bytes(b"subtitle")
    (explicit / "Part.One.2020.mkv").write_bytes(b"one")
    (explicit / "Part.Two.2020.mkv").write_bytes(b"two")

    discovery = discover_library_folders(
        [root],
        allowed_video_extensions=VIDEO_EXTENSIONS,
    )

    roles_by_name = {file.path.name: file.metadata.role for file in discovery.files}
    assert roles_by_name["Smart.Movie.2020.mkv"] == "smart_single"
    assert roles_by_name["Part.One.2020.mkv"] == "single"
    assert roles_by_name["Part.Two.2020.mkv"] == "single"
    assert discovery.warnings == [
        {
            "code": "explicit_single_folder_has_multiple_videos",
            "path": str(explicit.resolve()),
            "video_count": 2,
        }
    ]


def test_discovery_skips_restricted_symlink_targets(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    media_folder = root / "Movies -M"
    restricted_target = tmp_path / "Restricted"
    media_folder.mkdir(parents=True)
    restricted_target.mkdir()
    (restricted_target / "Hidden.Movie.2020.mkv").write_bytes(b"hidden")
    (media_folder / "Linked Restricted").symlink_to(restricted_target, target_is_directory=True)

    discovery = discover_library_folders(
        [root],
        allowed_video_extensions=VIDEO_EXTENSIONS,
        restricted_path_checker=lambda path: path == restricted_target.resolve(),
    )

    assert discovery.files == []
    assert discovery.warnings == [
        {
            "code": "library_reference_restricted_path_skipped",
            "path": str(restricted_target.resolve()),
        }
    ]
    assert str(restricted_target.resolve()) in discovery.excluded_paths


def test_discovery_skips_already_visited_symlink_loop(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    media_folder = root / "Movies -M"
    media_folder.mkdir(parents=True)
    (media_folder / "Movie.One.2020.mkv").write_bytes(b"movie")
    (media_folder / "Loop").symlink_to(media_folder, target_is_directory=True)

    discovery = discover_library_folders(
        [root],
        allowed_video_extensions=VIDEO_EXTENSIONS,
    )

    assert [file.path.name for file in discovery.files] == ["Movie.One.2020.mkv"]
    assert {
        "code": "library_reference_directory_loop_skipped",
        "path": str(media_folder.resolve()),
    } in discovery.warnings


def test_discovery_follows_safe_symlink_once(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    safe_target = tmp_path / "Safe Target"
    root.mkdir()
    safe_target.mkdir()
    (safe_target / "Linked.Movie.2020.mkv").write_bytes(b"movie")
    (root / "Linked Media").symlink_to(safe_target, target_is_directory=True)

    discovery = discover_library_folders(
        [root],
        allowed_video_extensions=VIDEO_EXTENSIONS,
    )

    assert [file.path.name for file in discovery.files] == ["Linked.Movie.2020.mkv"]
    assert discovery.warnings == []


def test_scan_persists_folder_metadata_and_category_summary(initialized_settings, monkeypatch) -> None:
    media_root = Path(initialized_settings.media_root)
    franchise = media_root / "Movies -M" / "Resident Evil -L"
    poster_folder = media_root / "Posters"
    excluded = media_root / "Movies -M" / "Skip -X"
    franchise.mkdir(parents=True)
    poster_folder.mkdir(parents=True)
    excluded.mkdir(parents=True)
    movie_path = franchise / "Resident.Evil.2002.mkv"
    poster_video_path = poster_folder / "Poster.Reference.Clip.mp4"
    excluded_path = excluded / "Ignored.Movie.2020.mp4"
    movie_path.write_bytes(b"movie")
    poster_video_path.write_bytes(b"poster-video")
    excluded_path.write_bytes(b"ignored")

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

    result = scan_media_library(initialized_settings, reason="folder-test")

    assert result["files_seen"] == 1
    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            """
            SELECT
                title,
                series_folder_name,
                library_category,
                library_category_name,
                library_folder_role,
                library_folder_name
            FROM media_items
            WHERE file_path = ?
            LIMIT 1
            """,
            (str(movie_path.resolve()),),
        ).fetchone()
        excluded_row = connection.execute(
            "SELECT 1 FROM media_items WHERE file_path IN (?, ?)",
            (str(poster_video_path.resolve()), str(excluded_path.resolve())),
        ).fetchone()
        summary = get_library_reference_category_summary(
            initialized_settings,
            connection=connection,
        )

    assert row is not None
    assert row["title"] == "Resident.Evil.2002"
    assert row["series_folder_name"] == "Resident Evil"
    assert row["library_category"] == "movies"
    assert row["library_category_name"] == "Movies"
    assert row["library_folder_role"] == "list"
    assert row["library_folder_name"] == "Resident Evil"
    assert excluded_row is None
    assert summary["movies"] == [
        {
            "path": str((media_root / "Movies -M").resolve()),
            "name": "Movies",
        }
    ]


def test_scan_persists_nested_explicit_list_identity(initialized_settings, monkeypatch) -> None:
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

    result = scan_media_library(initialized_settings, reason="nested-list-test")

    assert result["files_seen"] == 3
    with get_connection(initialized_settings) as connection:
        rows = connection.execute(
            """
            SELECT
                original_filename,
                library_category,
                library_folder_role,
                library_folder_name,
                series_folder_key,
                series_folder_name
            FROM media_items
            WHERE original_filename LIKE 'Hannibal S0%E01.mkv'
            ORDER BY original_filename ASC
            """
        ).fetchall()

    assert len(rows) == 3
    assert {row["series_folder_key"] for row in rows} == {rows[0]["series_folder_key"]}
    assert rows[0]["series_folder_key"].startswith("local-folder:")
    for row in rows:
        assert row["library_category"] == "tv"
        assert row["library_folder_role"] in {"legacy", "smart_single"}
        assert row["library_folder_name"].startswith("Hannibal Season")
        assert row["series_folder_name"] == "Hannibal (SE1~3) [1080p]"


def test_scan_updates_unchanged_files_after_parent_list_suffix_added(initialized_settings, monkeypatch) -> None:
    media_root = Path(initialized_settings.media_root)
    tv_root = media_root / "TV Shows -TV"
    season_one = tv_root / "Hannibal Season 1"
    season_two = tv_root / "Hannibal Season 2"
    season_three = tv_root / "Hannibal Season 3"
    season_one.mkdir(parents=True)
    season_two.mkdir(parents=True)
    season_three.mkdir(parents=True)
    for path, content in (
        (season_one / "Hannibal S01E01.mkv", b"episode-one"),
        (season_two / "Hannibal S02E01.mkv", b"episode-two-has-unique-size"),
        (season_three / "Hannibal S03E01.mkv", b"episode-three-has-a-unique-size"),
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

    first_result = scan_media_library(initialized_settings, reason="before-list-suffix")
    assert first_result["files_seen"] == 3
    with get_connection(initialized_settings) as connection:
        before_rows = connection.execute(
            """
            SELECT id, original_filename, series_folder_key, series_folder_name
            FROM media_items
            WHERE original_filename LIKE 'Hannibal S0%E01.mkv'
            ORDER BY original_filename ASC
            """
        ).fetchall()
    assert len(before_rows) == 3
    assert {row["series_folder_key"] for row in before_rows} == {None}
    ids_by_name = {row["original_filename"]: int(row["id"]) for row in before_rows}

    list_folder = tv_root / "Hannibal (SE1~3) [1080p]-L"
    list_folder.mkdir()
    for season_folder in (season_one, season_two, season_three):
        season_folder.rename(list_folder / season_folder.name)

    second_result = scan_media_library(initialized_settings, reason="after-list-suffix")
    assert second_result["files_seen"] == 3
    with get_connection(initialized_settings) as connection:
        after_rows = connection.execute(
            """
            SELECT id, original_filename, file_path, series_folder_key, series_folder_name
            FROM media_items
            WHERE original_filename LIKE 'Hannibal S0%E01.mkv'
            ORDER BY original_filename ASC
            """
        ).fetchall()

    assert len(after_rows) == 3
    assert {row["series_folder_key"] for row in after_rows} == {after_rows[0]["series_folder_key"]}
    assert after_rows[0]["series_folder_key"].startswith("local-folder:")
    for row in after_rows:
        assert int(row["id"]) == ids_by_name[row["original_filename"]]
        assert "Hannibal (SE1~3) [1080p]-L" in row["file_path"]
        assert row["series_folder_name"] == "Hannibal (SE1~3) [1080p]"
