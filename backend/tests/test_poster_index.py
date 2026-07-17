from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading
import time

import pytest

from backend.app.services import poster_index_service
from backend.app.services import library_presentation_service
from backend.app.services.library_presentation_service import (
    _poster_url_for_row,
    _resolve_poster_path,
    _resolve_poster_path_legacy,
)
from backend.app.services.poster_index_service import (
    get_poster_index_metrics,
    get_poster_index_snapshot,
    invalidate_poster_indexes,
)


def _poster(directory: Path, name: str, content: bytes = b"poster") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(content)
    return path


@pytest.fixture(autouse=True)
def _clean_poster_index_cache() -> None:
    invalidate_poster_indexes()
    yield
    invalidate_poster_indexes()


@pytest.mark.parametrize(
    ("posters", "title", "year", "filename", "expected_name"),
    [
        (["Exact Film (2001).jpg"], "Exact Film", 2001, "Exact Film (2001).mkv", "Exact Film (2001).jpg"),
        (["Exact Film (2001).png"], "Exact Film", 2001, "Exact Film (2001).mkv", "Exact Film (2001).png"),
        (["Director’s Cut (2002).jpg"], "Director's Cut", 2002, "Director's Cut (2002).mkv", "Director’s Cut (2002).jpg"),
        (["Amélie (2001).jpg"], "Amélie", 2001, "Amélie.2001.mkv", "Amélie (2001).jpg"),
        (["Alpha Alpha: The Story (2011).jpg"], "Alpha Alpha: the Story", 2011, "Alpha.Alpha.the.Story.2011.mkv", "Alpha Alpha: The Story (2011).jpg"),
        (["Dragon (2010).jpg"], "Dragons", 2010, "Dragons.2010.mkv", "Dragon (2010).jpg"),
        (["Yearless Film.png"], "Yearless Film", 2020, "Yearless.Film.2020.mkv", "Yearless Film.png"),
        (["Other Film (2019).jpg"], "Exact Film", 2001, "Exact Film (2001).mkv", None),
        (["Exact Film (2001).webp"], "Exact Film", 2001, "Exact Film (2001).mkv", None),
        (["not-a-poster.jpg"], "Exact Film", 2001, "Exact Film (2001).mkv", None),
    ],
)
def test_index_resolver_matches_legacy_priority(
    initialized_settings,
    tmp_path: Path,
    posters: list[str],
    title: str,
    year: int,
    filename: str,
    expected_name: str | None,
) -> None:
    poster_dir = tmp_path / "posters"
    for name in posters:
        _poster(poster_dir, name)

    legacy = _resolve_poster_path_legacy(
        initialized_settings,
        poster_dir=poster_dir,
        title=title,
        year=year,
        original_filename=filename,
    )
    indexed = _resolve_poster_path(
        initialized_settings,
        poster_dir=poster_dir,
        title=title,
        year=year,
        original_filename=filename,
    )

    assert indexed == legacy
    assert indexed == (poster_dir / expected_name if expected_name else None)


def test_index_and_legacy_reject_ambiguous_yearless_matches(initialized_settings, tmp_path: Path) -> None:
    poster_dir = tmp_path / "posters"
    _poster(poster_dir, "Ambiguous Film.jpg")
    _poster(poster_dir, "Ambiguous.Film.png")

    arguments = {
        "poster_dir": poster_dir,
        "title": "Ambiguous Film",
        "year": 2020,
        "original_filename": "Ambiguous.Film.2020.mkv",
    }
    assert _resolve_poster_path_legacy(initialized_settings, **arguments) is None
    assert _resolve_poster_path(initialized_settings, **arguments) is None


def test_index_and_legacy_reject_symlink_outside_root(initialized_settings, tmp_path: Path) -> None:
    poster_dir = tmp_path / "posters"
    outside = _poster(tmp_path / "outside", "Escaped Film (2004).jpg")
    poster_dir.mkdir()
    link = poster_dir / "Escaped Film (2004).jpg"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    arguments = {
        "poster_dir": poster_dir,
        "title": "Escaped Film",
        "year": 2004,
        "original_filename": "Escaped.Film.2004.mkv",
    }
    assert _resolve_poster_path_legacy(initialized_settings, **arguments) is None
    assert _resolve_poster_path(initialized_settings, **arguments) is None


def test_missing_directory_returns_empty_snapshot_and_no_match(initialized_settings, tmp_path: Path) -> None:
    poster_dir = tmp_path / "missing"
    snapshot = get_poster_index_snapshot(poster_dir)
    assert snapshot is not None
    assert snapshot.entry_count == 0
    assert _resolve_poster_path(
        initialized_settings,
        poster_dir=poster_dir,
        title="Missing Film",
        year=2000,
        original_filename="Missing.Film.2000.mkv",
    ) is None


def test_warm_snapshot_does_not_iterate_directory_again(monkeypatch, tmp_path: Path) -> None:
    poster_dir = tmp_path / "posters"
    _poster(poster_dir, "Warm Film (2000).jpg")
    original_iterdir = Path.iterdir
    iterations = 0

    def counting_iterdir(path: Path):
        nonlocal iterations
        if path == poster_dir:
            iterations += 1
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", counting_iterdir)
    first = get_poster_index_snapshot(poster_dir)
    second = get_poster_index_snapshot(poster_dir)

    assert first is second
    assert iterations == 1
    assert get_poster_index_metrics()["build_count"] == 1


def test_directory_add_remove_and_rename_rebuild_once(tmp_path: Path) -> None:
    poster_dir = tmp_path / "posters"
    first_path = _poster(poster_dir, "First Film (2000).jpg")
    first = get_poster_index_snapshot(poster_dir)
    time.sleep(0.002)
    second_path = _poster(poster_dir, "Second Film (2001).jpg")
    second = get_poster_index_snapshot(poster_dir)
    assert second is not first
    assert second.entry_count == 2

    time.sleep(0.002)
    renamed_path = second_path.with_name("Renamed Film (2001).jpg")
    second_path.rename(renamed_path)
    third = get_poster_index_snapshot(poster_dir)
    assert third is not second
    assert "Renamed Film (2001).jpg" in third.exact_filename_map

    time.sleep(0.002)
    first_path.unlink()
    fourth = get_poster_index_snapshot(poster_dir)
    assert fourth.entry_count == 1
    assert get_poster_index_metrics()["build_count"] == 4


def test_concurrent_snapshot_requests_build_once(monkeypatch, tmp_path: Path) -> None:
    poster_dir = tmp_path / "posters"
    _poster(poster_dir, "Concurrent Film (2000).jpg")
    original_build = poster_index_service._build_poster_index_snapshot
    build_count = 0
    build_lock = threading.Lock()

    def slow_build(*args, **kwargs):
        nonlocal build_count
        with build_lock:
            build_count += 1
        time.sleep(0.02)
        return original_build(*args, **kwargs)

    monkeypatch.setattr(poster_index_service, "_build_poster_index_snapshot", slow_build)
    with ThreadPoolExecutor(max_workers=8) as executor:
        snapshots = list(executor.map(lambda _: get_poster_index_snapshot(poster_dir), range(16)))

    assert build_count == 1
    assert len({id(snapshot) for snapshot in snapshots}) == 1


def test_request_memo_reuses_poster_url_and_content_change_updates_token(
    initialized_settings,
    monkeypatch,
    tmp_path: Path,
) -> None:
    poster_dir = tmp_path / "posters"
    poster_path = _poster(poster_dir, "Memo Film (2000).jpg", b"first")
    row = {
        "id": 41,
        "title": "Memo Film",
        "year": 2000,
        "original_filename": "Memo.Film.2000.mkv",
        "source_kind": "local",
    }
    snapshot = get_poster_index_snapshot(poster_dir)
    memo: dict[int, str | None] = {}
    original_resolve = library_presentation_service._resolve_poster_path
    resolve_count = 0

    def counting_resolve(*args, **kwargs):
        nonlocal resolve_count
        resolve_count += 1
        return original_resolve(*args, **kwargs)

    monkeypatch.setattr(library_presentation_service, "_resolve_poster_path", counting_resolve)
    first_url = _poster_url_for_row(
        initialized_settings,
        row,
        poster_dir=poster_dir,
        poster_index=snapshot,
        poster_url_memo=memo,
    )
    assert _poster_url_for_row(
        initialized_settings,
        row,
        poster_dir=poster_dir,
        poster_index=snapshot,
        poster_url_memo=memo,
    ) == first_url
    assert len(memo) == 1
    assert resolve_count == 1

    time.sleep(0.002)
    poster_path.write_bytes(b"replacement-content")
    os.utime(poster_path, None)
    refreshed_url = _poster_url_for_row(
        initialized_settings,
        row,
        poster_dir=poster_dir,
        poster_index=snapshot,
        poster_url_memo={},
    )
    assert refreshed_url != first_url
    assert resolve_count == 2


def test_different_roots_have_isolated_snapshots(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _poster(first_root, "First Film (2000).jpg")
    _poster(second_root, "Second Film (2000).jpg")

    first = get_poster_index_snapshot(first_root)
    second = get_poster_index_snapshot(second_root)

    assert first.root != second.root
    assert set(first.exact_filename_map) == {"First Film (2000).jpg"}
    assert set(second.exact_filename_map) == {"Second Film (2000).jpg"}


def test_snapshot_cache_is_bounded(tmp_path: Path) -> None:
    for index in range(poster_index_service.POSTER_INDEX_MAX_ROOTS + 3):
        root = tmp_path / f"root-{index}"
        _poster(root, f"Film {index} (2000).jpg")
        assert get_poster_index_snapshot(root) is not None

    assert get_poster_index_metrics()["cached_root_count"] == poster_index_service.POSTER_INDEX_MAX_ROOTS


def test_build_failure_is_memoized_and_legacy_fallback_still_resolves(
    initialized_settings,
    monkeypatch,
    caplog,
    tmp_path: Path,
) -> None:
    poster_dir = tmp_path / "posters"
    expected = _poster(poster_dir, "Fallback Film (2003).jpg")
    build_count = 0

    def failed_build(*_args, **_kwargs):
        nonlocal build_count
        build_count += 1
        raise OSError("synthetic poster index failure")

    monkeypatch.setattr(poster_index_service, "_build_poster_index_snapshot", failed_build)
    arguments = {
        "poster_dir": poster_dir,
        "title": "Fallback Film",
        "year": 2003,
        "original_filename": "Fallback.Film.2003.mkv",
    }
    with caplog.at_level("WARNING"):
        assert _resolve_poster_path(initialized_settings, **arguments) == expected
        assert _resolve_poster_path(initialized_settings, **arguments) == expected

    assert build_count == 1
    assert caplog.text.count("Poster index build failed; using safe legacy lookup") == 1
    assert "synthetic poster index failure" not in caplog.text


def test_cloud_filename_derived_title_has_index_parity(initialized_settings, tmp_path: Path) -> None:
    poster_dir = tmp_path / "posters"
    expected = _poster(poster_dir, "Cloud Film (2018).jpg")
    arguments = {
        "poster_dir": poster_dir,
        "title": "Cloud.Film.2018.1080p",
        "year": None,
        "original_filename": "Cloud.Film.2018.1080p.WEB-DL.mkv",
        "source_kind": "cloud",
    }
    assert _resolve_poster_path_legacy(initialized_settings, **arguments) == expected
    assert _resolve_poster_path(initialized_settings, **arguments) == expected


def test_three_thousand_posters_and_one_thousand_lookups_iterate_directory_once(
    initialized_settings,
    tmp_path: Path,
) -> None:
    poster_dir = tmp_path / "posters"
    poster_dir.mkdir()
    for index in range(3000):
        year = 1950 + (index % 75)
        (poster_dir / f"Synthetic Movie {index}: The Story ({year}).jpg").write_bytes(b"poster")

    snapshot = get_poster_index_snapshot(poster_dir)
    assert snapshot is not None
    assert snapshot.entry_count == 3000
    for index in range(1000):
        year = 1950 + (index % 75)
        resolved = _resolve_poster_path(
            initialized_settings,
            poster_dir=poster_dir,
            poster_index=snapshot,
            title=f"Synthetic Movie {index}: the Story",
            year=year,
            original_filename=f"Synthetic.Movie.{index}.the.Story.{year}.mkv",
        )
        assert resolved is not None

    metrics = get_poster_index_metrics()
    assert metrics["directory_iteration_count"] == 1
    assert metrics["build_count"] == 1
