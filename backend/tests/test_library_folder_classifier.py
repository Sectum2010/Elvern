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
        ("Movies -M", ("M",), "Movies"),
        ("Movies-M", ("M",), "Movies"),
        ("Movies   -M", ("M",), "Movies"),
        ("Shows -TV", ("TV",), "Shows"),
        ("TV Shows-TV", ("TV",), "TV Shows"),
        ("Anime -AN", ("AN",), "Anime"),
        ("Anime-AN", ("AN",), "Anime"),
        ("Cartoons -C", ("C",), "Cartoons"),
        ("Cartoon-C", ("C",), "Cartoon"),
        ("Resident Evil -L", ("L",), "Resident Evil"),
        ("Resident Evil-L", ("L",), "Resident Evil"),
        ("One Shot -S", ("S",), "One Shot"),
        ("Single Movie-S", ("S",), "Single Movie"),
        ("Ignore Me -X", ("X",), "Ignore Me"),
        ("Ignore Me-X", ("X",), "Ignore Me"),
    ]

    for folder_name, suffixes, display_name in expectations:
        parsed = parse_folder_suffixes(folder_name)

        assert parsed.recognized_suffixes == suffixes
        assert parsed.display_name == display_name


def test_suffix_parser_allows_combined_suffixes_with_mixed_spacing() -> None:
    for folder_name in [
        "Resident Evil -M -L",
        "Resident Evil-M-L",
        "Resident Evil -M-L",
        "Resident Evil-M -L",
    ]:
        combined = parse_folder_suffixes(folder_name)

        assert combined.recognized_suffixes == ("M", "L")
        assert combined.display_name == "Resident Evil"
        assert combined.explicit_category == "movies"
        assert combined.explicit_role == "list"


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


def test_summary_keeps_compact_cartoon_category_folder(initialized_settings) -> None:
    media_root = Path(initialized_settings.media_root)
    cartoon_folder = media_root / "Cartoon-C"
    cartoon_folder.mkdir(parents=True)

    summary = get_library_reference_category_summary(initialized_settings)

    assert summary["cartoon"] == [
        {
            "path": str(cartoon_folder.resolve()),
            "name": "Cartoon",
        }
    ]


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
