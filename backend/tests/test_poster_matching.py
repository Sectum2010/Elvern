from __future__ import annotations

from pathlib import Path

from backend.app.services.library_presentation_service import _resolve_poster_path


def _write_poster(poster_dir: Path, filename: str) -> Path:
    poster_dir.mkdir(parents=True, exist_ok=True)
    poster_path = poster_dir / filename
    poster_path.write_bytes(b"poster-bytes")
    return poster_path


def test_poster_lookup_matches_internal_article_case_after_colon_from_source_title(
    initialized_settings,
) -> None:
    poster_dir = Path(initialized_settings.media_root) / "Posters"
    expected = _write_poster(
        poster_dir,
        "Alpha Alpha Alpha: The Beta Beta (2011).jpg",
    )

    resolved = _resolve_poster_path(
        initialized_settings,
        poster_dir=poster_dir,
        title="Alpha Alpha Alpha: the Beta Beta",
        year=2011,
        original_filename="Alpha Alpha Alpha: the Beta Beta (2011).mkv",
    )

    assert resolved == expected


def test_poster_lookup_matches_internal_article_case_after_colon_from_filename_truth(
    initialized_settings,
) -> None:
    poster_dir = Path(initialized_settings.media_root) / "Posters"
    expected = _write_poster(
        poster_dir,
        "Alpha Alpha Alpha: The Beta Beta (2011).png",
    )

    resolved = _resolve_poster_path(
        initialized_settings,
        poster_dir=poster_dir,
        title=None,
        year=2011,
        original_filename="Alpha.Alpha.Alpha.the.Beta.Beta.2011.mkv",
    )

    assert resolved == expected


def test_poster_lookup_matches_inverse_internal_article_case_after_colon(
    initialized_settings,
) -> None:
    poster_dir = Path(initialized_settings.media_root) / "Posters"
    expected = _write_poster(
        poster_dir,
        "Alpha Alpha Alpha: the Beta Beta (2011).jpg",
    )

    resolved = _resolve_poster_path(
        initialized_settings,
        poster_dir=poster_dir,
        title="Alpha Alpha Alpha: The Beta Beta",
        year=2011,
        original_filename="Alpha Alpha Alpha: The Beta Beta (2011).mkv",
    )

    assert resolved == expected


def test_poster_lookup_does_not_make_internal_articles_optional(
    initialized_settings,
) -> None:
    poster_dir = Path(initialized_settings.media_root) / "Posters"
    _write_poster(
        poster_dir,
        "Alpha Alpha Alpha: Beta Beta (2011).jpg",
    )

    resolved = _resolve_poster_path(
        initialized_settings,
        poster_dir=poster_dir,
        title="Alpha Alpha Alpha: The Beta Beta",
        year=2011,
        original_filename="Alpha Alpha Alpha: The Beta Beta (2011).mkv",
    )

    assert resolved is None


def test_poster_lookup_does_not_match_unrelated_subtitle(
    initialized_settings,
) -> None:
    poster_dir = Path(initialized_settings.media_root) / "Posters"
    _write_poster(
        poster_dir,
        "Alpha Alpha Alpha: The Gamma Gamma (2011).jpg",
    )

    resolved = _resolve_poster_path(
        initialized_settings,
        poster_dir=poster_dir,
        title="Alpha Alpha Alpha: The Beta Beta",
        year=2011,
        original_filename="Alpha Alpha Alpha: The Beta Beta (2011).mkv",
    )

    assert resolved is None
