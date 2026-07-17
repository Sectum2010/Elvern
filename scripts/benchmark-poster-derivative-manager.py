#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import sys
import tempfile
import threading
import time

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import get_settings, refresh_settings  # noqa: E402
from backend.app.auth import ensure_admin_user  # noqa: E402
from backend.app.db import get_connection, init_db, utcnow_iso  # noqa: E402
from backend.app.services.local_library_source_service import ensure_current_shared_local_source_binding  # noqa: E402
from backend.app.services.poster_derivative_manager import PosterDerivativeManager  # noqa: E402


WORKER_COUNTS = (1, 2, 4, 6, 8, 10)
QUEUE_SIZES = (25, 100, 500)


def _configure_isolated_settings(root: Path):
    for key in [name for name in os.environ if name.startswith("ELVERN_")]:
        os.environ.pop(key, None)
    media_root = root / "media"
    media_root.mkdir(parents=True, exist_ok=True)
    fake_ffprobe = root / "ffprobe"
    fake_ffprobe.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    fake_ffprobe.chmod(0o755)
    os.environ.update({
        "ELVERN_MEDIA_ROOT": str(media_root),
        "ELVERN_LIBRARY_ROOT_LINUX": str(media_root),
        "ELVERN_DB_PATH": str(root / "appdata" / "benchmark.db"),
        "ELVERN_ADMIN_USERNAME": "synthetic-admin",
        "ELVERN_ADMIN_BOOTSTRAP_PASSWORD": "synthetic-benchmark-password",
        "ELVERN_SESSION_SECRET": "synthetic-benchmark-session-secret-32chars",
        "ELVERN_COOKIE_SECURE": "false",
        "ELVERN_SCAN_ON_STARTUP": "false",
        "ELVERN_TRANSCODE_ENABLED": "false",
        "ELVERN_PUBLIC_APP_ORIGIN": "",
        "ELVERN_BACKEND_ORIGIN": "",
        "ELVERN_HELPER_RELEASES_DIR": str(root / "helper-releases"),
        "ELVERN_TRANSCODE_DIR": str(root / "transcodes"),
        "ELVERN_FFPROBE_PATH": str(fake_ffprobe),
    })
    get_settings.cache_clear()
    return refresh_settings()


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "p50_ms": round(statistics.median(values), 3) if values else 0.0,
        "p90_ms": round(_percentile(values, 0.9), 3),
        "worst_ms": round(max(values), 3) if values else 0.0,
    }


def _rss_bytes() -> int:
    try:
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _directory_bytes(path: Path) -> int:
    return sum(candidate.stat().st_size for candidate in path.rglob("*") if candidate.is_file())


def _create_sources(root: Path) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    sources: list[Path] = []
    fixtures = (
        ("poster-a.jpg", "JPEG", (1600, 2400), (50, 90, 170, 255)),
        ("poster-b.jpg", "JPEG", (1900, 2850), (130, 55, 145, 255)),
        ("poster-c.jpg", "JPEG", (2200, 3300), (40, 135, 110, 255)),
        ("poster-d.png", "PNG", (1600, 2400), (40, 120, 220, 155)),
        ("poster-e.png", "PNG", (1900, 2850), (175, 85, 95, 180)),
        ("poster-f.png", "PNG", (2200, 3300), (80, 145, 75, 165)),
    )
    for filename, image_format, size, color in fixtures:
        path = root / filename
        mode = "RGBA" if image_format == "PNG" else "RGB"
        image = Image.new(mode, size, color if mode == "RGBA" else color[:3])
        save_options = {"optimize": True}
        if image_format == "JPEG":
            save_options.update({"quality": 98, "subsampling": 0, "progressive": True})
        image.save(path, format=image_format, **save_options)
        sources.append(path)
    return sources


def _create_prewarm_sources(root: Path) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    sources: list[Path] = []
    for index in range(24):
        image_format = "PNG" if index % 4 == 0 else "JPEG"
        extension = "png" if image_format == "PNG" else "jpg"
        path = root / f"prewarm-{index:02d}.{extension}"
        width = 1500 + ((index % 3) * 120)
        size = (width, round(width * 1.5))
        if image_format == "PNG":
            image = Image.new("RGBA", size, (35 + index, 90, 180, 170))
            image.save(path, format="PNG", optimize=True)
        else:
            image = Image.new("RGB", size, (45 + index, 80, 155))
            image.save(path, format="JPEG", quality=98, subsampling=0, progressive=True, optimize=True)
        sources.append(path)
    return sources


def _run_storm(base_settings, root: Path, sources: list[Path], *, workers: int, request_count: int) -> dict[str, object]:
    cache_dir = root / f"cache-{workers}-{request_count}"
    shutil.rmtree(cache_dir, ignore_errors=True)
    settings = replace(
        base_settings,
        poster_display_cache_enabled=True,
        poster_display_cache_dir=cache_dir,
        poster_card_cache_max_width=1400,
        poster_card_cache_jpeg_quality=97,
        poster_generation_workers=workers,
        poster_generation_queue_max=max(512, request_count),
    )
    manager = PosterDerivativeManager(settings)
    manager.start()
    completed_latencies: list[float] = []
    control_latencies: list[float] = []
    lock = threading.Lock()
    monitor_stop = threading.Event()
    rss_baseline = _rss_bytes()
    thread_baseline = threading.active_count()
    peak_rss = rss_baseline
    peak_threads = thread_baseline

    def monitor() -> None:
        nonlocal peak_rss, peak_threads
        while not monitor_stop.wait(0.01):
            peak_rss = max(peak_rss, _rss_bytes())
            peak_threads = max(peak_threads, threading.active_count())

    monitor_thread = threading.Thread(target=monitor, name="poster-benchmark-monitor", daemon=True)
    monitor_thread.start()
    control_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="poster-benchmark-control")
    started = time.perf_counter()
    cpu_started = time.process_time()
    futures: list[Future] = []
    try:
        for index in range(request_count):
            submitted_at = time.perf_counter()
            future = manager.submit(sources[index % len(sources)], target_width=1400)

            def record_completion(_future, request_started=submitted_at):
                with lock:
                    completed_latencies.append((time.perf_counter() - request_started) * 1000)

            future.add_done_callback(record_completion)
            futures.append(future)
            if index % max(1, request_count // 10) == 0:
                control_started = time.perf_counter()

                def control_request(started_at=control_started):
                    time.sleep(0.001)
                    with lock:
                        control_latencies.append((time.perf_counter() - started_at) * 1000)

                control_executor.submit(control_request)
        for future in futures:
            future.result(timeout=120)
        control_executor.shutdown(wait=True)
        elapsed_seconds = time.perf_counter() - started
        cpu_seconds = time.process_time() - cpu_started
        warm_samples: list[float] = []
        for _ in range(25):
            warm_started = time.perf_counter()
            manager.submit(sources[0], target_width=1400).result(timeout=5)
            warm_samples.append((time.perf_counter() - warm_started) * 1000)
        stats = manager.snapshot_stats()
    finally:
        monitor_stop.set()
        monitor_thread.join(timeout=1)
        control_executor.shutdown(wait=False, cancel_futures=True)
        manager.shutdown()

    return {
        "workers": workers,
        "submitted_requests": request_count,
        "unique_source_derivatives": len(sources),
        "total_seconds": round(elapsed_seconds, 4),
        "throughput_requests_per_second": round(request_count / elapsed_seconds, 3),
        "request_completion_latency": _summary(completed_latencies),
        "cold_generation_latency": _summary([value * 1000 for value in stats["generation_seconds"]]),
        "queue_wait_latency": _summary([value * 1000 for value in stats["queue_wait_seconds"]]),
        "warm_cache_latency": _summary(warm_samples),
        "independent_control_latency": _summary(control_latencies),
        "active_worker_peak": stats["active_worker_peak"],
        "thread_count_peak": peak_threads,
        "cpu_seconds": round(cpu_seconds, 4),
        "peak_rss_bytes": peak_rss,
        "peak_rss_delta_bytes": max(0, peak_rss - rss_baseline),
        "thread_count_delta_peak": max(0, peak_threads - thread_baseline),
        "disk_bytes_written": _directory_bytes(cache_dir),
        "single_flight_collapsed": stats["single_flight_collapsed"],
        "dropped_prewarm": stats["dropped_prewarm"],
        "generated": stats["generated"],
        "cache_hits": stats["cache_hits"],
    }


def _run_prewarm_profile(base_settings, root: Path, sources: list[Path], *, label: str, submissions: int) -> dict[str, object]:
    cache_dir = root / f"prewarm-{label}"
    settings = replace(
        base_settings,
        poster_display_cache_enabled=True,
        poster_display_cache_dir=cache_dir,
        poster_generation_workers=2,
        poster_generation_queue_max=256,
    )
    manager = PosterDerivativeManager(settings)
    manager.start()
    started = time.perf_counter()
    try:
        futures = [
            manager.prewarm(sources[index % len(sources)], target_width=1400)
            for index in range(submissions)
        ]
        for future in futures:
            future.result(timeout=60)
        elapsed_seconds = time.perf_counter() - started
        stats = manager.snapshot_stats()
    finally:
        manager.shutdown()
    return {
        "profile": label,
        "submitted": submissions,
        "seconds": round(elapsed_seconds, 4),
        "generated": stats["generated"],
        "single_flight_collapsed": stats["single_flight_collapsed"],
        "dropped_prewarm": stats["dropped_prewarm"],
        "disk_bytes_written": _directory_bytes(cache_dir),
    }


def _seed_api_benchmark_item(settings: object, root: Path) -> int:
    init_db(settings)
    ensure_admin_user(settings)
    media_path = root / "media" / "synthetic-benchmark.mp4"
    media_path.write_bytes(b"synthetic benchmark media")
    now = utcnow_iso()
    with get_connection(settings) as connection:
        source_id = ensure_current_shared_local_source_binding(settings, connection=connection)
        cursor = connection.execute(
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
            ) VALUES (?, ?, ?, 'local', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Synthetic Benchmark Item",
                media_path.name,
                str(media_path),
                source_id,
                media_path.stat().st_size,
                media_path.stat().st_mtime,
                120.0,
                1920,
                1080,
                "h264",
                "aac",
                "mp4",
                2026,
                now,
                now,
                now,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def _measure_api_requests(client, item_id: int, *, repetitions: int = 8) -> dict[str, dict[str, float]]:
    endpoints = {
        "detail_item": f"/api/library/item/{item_id}",
        "progress": f"/api/progress/{item_id}",
        "playback": f"/api/playback/{item_id}",
    }
    samples: dict[str, list[float]] = {name: [] for name in endpoints}
    for _ in range(repetitions):
        for name, endpoint in endpoints.items():
            started = time.perf_counter()
            response = client.get(endpoint)
            elapsed_ms = (time.perf_counter() - started) * 1000
            if response.status_code != 200:
                raise RuntimeError(f"Synthetic API benchmark failed for {name}: HTTP {response.status_code}")
            samples[name].append(elapsed_ms)
    return {name: _summary(values) for name, values in samples.items()}


def _run_api_latency_profiles(base_settings, root: Path, sources: list[Path]) -> list[dict[str, object]]:
    from fastapi.testclient import TestClient
    from backend.app import main as main_module

    item_id = _seed_api_benchmark_item(base_settings, root)
    profiles: list[dict[str, object]] = []
    with TestClient(main_module.app, client=("127.0.0.1", 50100)) as client:
        login_response = client.post(
            "/api/auth/login",
            json={
                "username": base_settings.admin_username,
                "password": base_settings.admin_bootstrap_password,
            },
        )
        if login_response.status_code != 200:
            raise RuntimeError(f"Synthetic API benchmark login failed: HTTP {login_response.status_code}")
        client.app.state.poster_derivative_manager.shutdown()
        for workers in WORKER_COUNTS:
            settings = replace(
                base_settings,
                poster_display_cache_enabled=True,
                poster_display_cache_dir=root / f"api-cache-{workers}",
                poster_generation_workers=workers,
                poster_generation_queue_max=256,
            )
            manager = PosterDerivativeManager(settings)
            manager.start()
            client.app.state.poster_derivative_manager = manager
            try:
                baseline = _measure_api_requests(client, item_id)
                storm_futures = [
                    manager.submit(source, target_width=1400)
                    for source in sources
                ]
                storm = _measure_api_requests(client, item_id)
                for future in storm_futures:
                    future.result(timeout=120)
                stats = manager.snapshot_stats()
            finally:
                manager.shutdown()
            profiles.append({
                "workers": workers,
                "poster_jobs": len(sources),
                "active_worker_peak": stats["active_worker_peak"],
                "no_poster_load": baseline,
                "poster_storm": storm,
                "additional_p50_ms": {
                    name: round(storm[name]["p50_ms"] - baseline[name]["p50_ms"], 3)
                    for name in baseline
                },
            })
    return profiles


def _write_markdown(report: dict[str, object], path: Path) -> None:
    lines = [
        "# Poster Derivative Manager Benchmark",
        "",
        "Synthetic data only. The queue sizes are concurrent request counts over six repeated source identities, so the run also measures single-flight collapse.",
        "",
        "| Workers | Requests | Seconds | Req/s | Active peak | CPU s | RSS delta MiB | Collapsed |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["runs"]:
        lines.append(
            f"| {row['workers']} | {row['submitted_requests']} | {row['total_seconds']} | "
            f"{row['throughput_requests_per_second']} | {row['active_worker_peak']} | {row['cpu_seconds']} | "
            f"{row['peak_rss_delta_bytes'] / 1024 / 1024:.1f} | {row['single_flight_collapsed']} |"
        )
    lines.extend((
        "",
        "## Prewarm profiles",
        "",
        "| Profile | Submitted | Seconds | Generated | Collapsed | Dropped |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ))
    for row in report["prewarm_profiles"]:
        lines.append(
            f"| {row['profile']} | {row['submitted']} | {row['seconds']} | "
            f"{row['generated']} | {row['single_flight_collapsed']} | {row['dropped_prewarm']} |"
        )
    lines.extend((
        "",
        "## API latency during poster generation",
        "",
        "| Workers | Detail base p50 | Detail storm p50 | Detail delta | Progress delta | Playback delta |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ))
    for row in report["api_latency_profiles"]:
        lines.append(
            f"| {row['workers']} | {row['no_poster_load']['detail_item']['p50_ms']} | "
            f"{row['poster_storm']['detail_item']['p50_ms']} | {row['additional_p50_ms']['detail_item']} | "
            f"{row['additional_p50_ms']['progress']} | {row['additional_p50_ms']['playback']} |"
        )
    lines.extend((
        "",
        "Timing values are observational and never act as CI pass/fail thresholds.",
        "The independent control measurement is an in-process latency sentinel, not a claim about a deployed Detail endpoint.",
    ))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark the isolated Poster Derivative Manager with synthetic images.")
    parser.add_argument("--json-output", type=Path, default=PROJECT_ROOT / "tmp/poster-derivative-manager-benchmark.json")
    parser.add_argument("--markdown-output", type=Path, default=PROJECT_ROOT / "tmp/poster-derivative-manager-benchmark.md")
    args = parser.parse_args()
    cache_root = Path.home() / ".cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="elvern-poster-manager-benchmark-",
        dir=cache_root,
    ) as temporary_directory:
        root = Path(temporary_directory)
        sources = _create_sources(root / "sources")
        prewarm_sources = _create_prewarm_sources(root / "prewarm-sources")
        settings = _configure_isolated_settings(root)
        runs = [
            _run_storm(settings, root, sources, workers=workers, request_count=request_count)
            for workers in WORKER_COUNTS
            for request_count in QUEUE_SIZES
        ]
        prewarm_profiles = [
            _run_prewarm_profile(settings, root, prewarm_sources, label=label, submissions=count)
            for label, count in (
                ("disabled", 0),
                ("continue_only", 6),
                ("main_6", 6),
                ("main_12", 12),
                ("main_18", 18),
                ("product_default_continue_6_main_12_recent_6", 24),
            )
        ]
        api_latency_profiles = _run_api_latency_profiles(settings, root, sources)
    report = {
        "kind": "isolated_synthetic_poster_derivative_manager",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "default_workers_remains": 2,
        "target_width": 1400,
        "jpeg_quality": 97,
        "worker_counts": list(WORKER_COUNTS),
        "queue_request_counts": list(QUEUE_SIZES),
        "private_data_used": False,
        "live_database_used": False,
        "timings_are_ci_thresholds": False,
        "runs": runs,
        "prewarm_profiles": prewarm_profiles,
        "api_latency_profiles": api_latency_profiles,
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _write_markdown(report, args.markdown_output)
    print(json.dumps({
        "json_output": str(args.json_output),
        "markdown_output": str(args.markdown_output),
        "runs": len(runs),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
