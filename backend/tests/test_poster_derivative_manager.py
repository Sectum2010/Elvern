from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import threading
import time

import pytest
from PIL import Image, ImageCms, JpegImagePlugin

from backend.app.config import ConfigError, refresh_settings
from backend.app.services.poster_derivative_manager import (
    PosterDerivativeManager,
    PosterDerivativePriority,
    PosterDerivativeQueueFullError,
)
from backend.app.services.poster_display_cache_service import get_or_create_card_poster_display_cache
from backend.app.routes import library as library_routes


def _manager_settings(initialized_settings, tmp_path: Path, *, workers: int = 2, queue_max: int = 8):
    return replace(
        initialized_settings,
        poster_display_cache_dir=(tmp_path / "poster-cache").resolve(),
        poster_generation_workers=workers,
        poster_generation_queue_max=queue_max,
        poster_prewarm_enabled=True,
        poster_prewarm_first_items=12,
        poster_prewarm_recent_items=6,
        library_plan_timing_enabled=False,
    )


@pytest.mark.parametrize(
    "name",
    [
        "ELVERN_POSTER_GENERATION_WORKERS",
        "ELVERN_POSTER_GENERATION_QUEUE_MAX",
        "ELVERN_POSTER_PREWARM_FIRST_ITEMS",
        "ELVERN_POSTER_PREWARM_RECENT_ITEMS",
    ],
)
def test_poster_configuration_requires_positive_integers(
    monkeypatch,
    initialized_settings,
    name: str,
) -> None:
    del initialized_settings
    for value in ("0", "-1", "not-an-integer"):
        monkeypatch.setenv(name, value)
        with pytest.raises(ConfigError, match=name):
            refresh_settings()
        monkeypatch.delenv(name)


def test_library_prewarm_uses_only_bounded_sections(
    initialized_settings,
    monkeypatch,
) -> None:
    settings = replace(
        initialized_settings,
        poster_prewarm_enabled=True,
        poster_prewarm_first_items=2,
        poster_prewarm_recent_items=1,
    )
    calls: list[tuple[str, int]] = []

    class StubManager:
        def prewarm(self, source_path, *, target_width):
            calls.append((Path(source_path).name, target_width))

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                poster_derivative_manager=StubManager(),
            ),
        ),
    )
    monkeypatch.setattr(library_routes, "get_poster_card_display_max_width", lambda *_args, **_kwargs: 1400)
    monkeypatch.setattr(
        library_routes,
        "get_media_item_poster_path",
        lambda *_args, item_id, **_kwargs: Path(f"poster-{item_id}.jpg"),
    )

    library_routes._prewarm_library_card_posters(
        request,
        user_id=1,
        allow_globally_hidden=False,
        payload={
            "sections": {
                "continue_watching_item_ids": [1, 2],
                "item_ids": [2, 3, 4],
                "recently_added_item_ids": [4, 5],
            },
        },
    )

    assert calls == [
        ("poster-1.jpg", 1400),
        ("poster-2.jpg", 1400),
        ("poster-3.jpg", 1400),
        ("poster-4.jpg", 1400),
    ]


def test_single_flight_collapses_duplicate_generation(initialized_settings, tmp_path) -> None:
    settings = _manager_settings(initialized_settings, tmp_path)
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source")
    calls = 0
    release = threading.Event()

    def generate(_settings, path, *, target_width):
        nonlocal calls
        calls += 1
        release.wait(timeout=2)
        return Path(path)

    manager = PosterDerivativeManager(
        settings,
        generation_function=generate,
        cache_lookup_function=lambda *_args, **_kwargs: None,
    )
    manager.start()
    try:
        first = manager.submit(source, target_width=1400)
        second = manager.submit(source, target_width=1400)
        release.set()

        assert first.result(timeout=2) == source
        assert second.result(timeout=2) == source
        assert calls == 1
        assert manager.snapshot_stats()["single_flight_collapsed"] == 1
    finally:
        manager.shutdown()


def test_queue_bound_drops_prewarm_before_requested_work(initialized_settings, tmp_path) -> None:
    settings = _manager_settings(initialized_settings, tmp_path, workers=1, queue_max=1)
    release = threading.Event()
    started = threading.Event()

    def generate(_settings, path, *, target_width):
        del target_width
        if Path(path).name == "active.jpg":
            started.set()
            release.wait(timeout=2)
        return Path(path)

    manager = PosterDerivativeManager(
        settings,
        generation_function=generate,
        cache_lookup_function=lambda *_args, **_kwargs: None,
    )
    manager.start()
    try:
        active = manager.submit(tmp_path / "active.jpg", target_width=1400)
        assert started.wait(timeout=1)
        prewarm = manager.submit(
            tmp_path / "prewarm.jpg",
            target_width=1400,
            priority=PosterDerivativePriority.PREWARM,
            prewarm=True,
        )
        requested = manager.submit(tmp_path / "requested.jpg", target_width=1400)
        release.set()

        assert active.result(timeout=2).name == "active.jpg"
        assert requested.result(timeout=2).name == "requested.jpg"
        with pytest.raises(PosterDerivativeQueueFullError):
            prewarm.result(timeout=2)
        stats = manager.snapshot_stats()
        assert stats["dropped_prewarm"] == 1
        assert stats["queued_peak"] <= 1
    finally:
        manager.shutdown()


def test_interactive_window_pauses_queued_normal_jobs(initialized_settings, tmp_path) -> None:
    settings = _manager_settings(initialized_settings, tmp_path, workers=1, queue_max=4)
    release = threading.Event()
    first_started = threading.Event()
    normal_started = threading.Event()

    def generate(_settings, path, *, target_width):
        del target_width
        if Path(path).name == "active.jpg":
            first_started.set()
            release.wait(timeout=2)
        else:
            normal_started.set()
        return Path(path)

    manager = PosterDerivativeManager(
        settings,
        generation_function=generate,
        cache_lookup_function=lambda *_args, **_kwargs: None,
        interactive_window_seconds=0.2,
    )
    manager.start()
    try:
        active = manager.submit(tmp_path / "active.jpg", target_width=1400)
        assert first_started.wait(timeout=1)
        normal = manager.submit(
            tmp_path / "normal.jpg",
            target_width=1400,
            priority=PosterDerivativePriority.NORMAL,
        )
        manager.enter_interactive_window()
        release.set()
        assert active.result(timeout=2).name == "active.jpg"
        assert not normal_started.wait(timeout=0.08)
        assert normal.result(timeout=2).name == "normal.jpg"
        assert normal_started.is_set()
    finally:
        manager.shutdown()


def test_cancelled_waiter_removes_unstarted_job(initialized_settings, tmp_path) -> None:
    settings = _manager_settings(initialized_settings, tmp_path, workers=1, queue_max=4)
    release = threading.Event()
    started = threading.Event()
    generated_names: list[str] = []

    def generate(_settings, path, *, target_width):
        del target_width
        generated_names.append(Path(path).name)
        if Path(path).name == "active.jpg":
            started.set()
            release.wait(timeout=2)
        return Path(path)

    manager = PosterDerivativeManager(
        settings,
        generation_function=generate,
        cache_lookup_function=lambda *_args, **_kwargs: None,
    )
    manager.start()
    try:
        active = manager.submit(tmp_path / "active.jpg", target_width=1400)
        assert started.wait(timeout=1)
        cancelled = manager.submit(tmp_path / "cancelled.jpg", target_width=1400)
        assert cancelled.cancel()
        release.set()
        assert active.result(timeout=2).name == "active.jpg"
        time.sleep(0.05)
        assert "cancelled.jpg" not in generated_names
    finally:
        manager.shutdown()


@pytest.mark.parametrize("source_format", ["JPEG", "PNG"])
def test_manager_preserves_existing_derivative_output_contract(
    initialized_settings,
    tmp_path,
    source_format: str,
) -> None:
    source = tmp_path / f"large.{source_format.lower()}"
    if source_format == "PNG":
        image = Image.new("RGBA", (2400, 3600), (20, 110, 220, 150))
        image.save(source, format="PNG", optimize=True)
    else:
        image = Image.new("RGB", (2400, 3600), (90, 40, 160))
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        exif = Image.Exif()
        exif[274] = 6
        image.save(
            source,
            format="JPEG",
            quality=100,
            subsampling=0,
            progressive=True,
            optimize=True,
            exif=exif,
            icc_profile=profile,
        )

    baseline_settings = replace(
        _manager_settings(initialized_settings, tmp_path),
        poster_display_cache_enabled=True,
        poster_display_cache_dir=(tmp_path / "baseline-cache").resolve(),
    )
    manager_settings = replace(
        baseline_settings,
        poster_display_cache_dir=(tmp_path / "manager-cache").resolve(),
    )
    baseline_path = get_or_create_card_poster_display_cache(
        baseline_settings,
        source,
        target_width=1400,
    )
    manager = PosterDerivativeManager(manager_settings)
    manager.start()
    try:
        managed_path = manager.submit(source, target_width=1400).result(timeout=10)
    finally:
        manager.shutdown()

    assert managed_path.suffix == baseline_path.suffix
    with Image.open(baseline_path) as baseline, Image.open(managed_path) as managed:
        assert managed.size == baseline.size
        assert managed.mode == baseline.mode
        assert managed.format == baseline.format
        assert managed.info.get("icc_profile") == baseline.info.get("icc_profile")
        if source_format == "JPEG":
            assert JpegImagePlugin.get_sampling(managed) == JpegImagePlugin.get_sampling(baseline) == 0
            assert bool(managed.info.get("progressive") or managed.info.get("progression"))
        else:
            assert "A" in managed.getbands()


def test_warm_cache_bypasses_worker_queue(initialized_settings, tmp_path) -> None:
    settings = replace(
        _manager_settings(initialized_settings, tmp_path),
        poster_display_cache_enabled=True,
    )
    source = tmp_path / "large.jpg"
    Image.new("RGB", (1800, 2700), (60, 120, 180)).save(source, format="JPEG", quality=98)
    expected = get_or_create_card_poster_display_cache(settings, source, target_width=1400)
    manager = PosterDerivativeManager(settings)
    manager.start()
    try:
        actual = manager.submit(source, target_width=1400).result(timeout=2)
        stats = manager.snapshot_stats()
    finally:
        manager.shutdown()

    assert actual == expected
    assert stats["cache_hits"] == 1
    assert stats["generated"] == 0
    assert stats["queued_peak"] == 0
