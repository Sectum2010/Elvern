#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.auth import ensure_admin_user  # noqa: E402
from backend.app.config import get_settings, refresh_settings  # noqa: E402
from backend.app.db import get_connection, init_db  # noqa: E402
from backend.app.services import library_presentation_service  # noqa: E402
from backend.app.services import library_service  # noqa: E402
from backend.app.services.local_library_source_service import (  # noqa: E402
    ensure_current_shared_local_source_binding,
)
from backend.app.services.poster_index_service import (  # noqa: E402
    get_poster_index_metrics,
    invalidate_poster_indexes,
)


ITEM_COUNTS = (100, 500, 1000, 3000)
OVERLAP_RATIOS = (0.0, 0.25, 0.6)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def _timing_summary(values: list[float]) -> dict[str, float]:
    return {
        "p50_ms": round(statistics.median(values), 4),
        "p90_ms": round(_percentile(values, 0.9), 4),
        "worst_ms": round(max(values), 4),
    }


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip() or "unknown"


def _configure_isolated_settings(work_root: Path, media_root: Path):
    for key in [name for name in os.environ if name.startswith("ELVERN_")]:
        os.environ.pop(key, None)
    media_root.mkdir(parents=True, exist_ok=True)
    fake_ffprobe = work_root / "ffprobe"
    fake_ffprobe.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    fake_ffprobe.chmod(0o755)
    safe_environment = {
        "ELVERN_MEDIA_ROOT": str(media_root),
        "ELVERN_LIBRARY_ROOT_LINUX": str(media_root),
        "ELVERN_DB_PATH": str(work_root / "benchmark.db"),
        "ELVERN_ADMIN_USERNAME": "synthetic-admin",
        "ELVERN_ADMIN_BOOTSTRAP_PASSWORD": "synthetic-benchmark-password",
        "ELVERN_SESSION_SECRET": "synthetic-benchmark-session-secret-32chars",
        "ELVERN_COOKIE_SECURE": "false",
        "ELVERN_SCAN_ON_STARTUP": "false",
        "ELVERN_TRANSCODE_ENABLED": "false",
        "ELVERN_BROWSER_PLAYBACK_ROUTE2_ENABLED": "false",
        "ELVERN_PUBLIC_APP_ORIGIN": "",
        "ELVERN_BACKEND_ORIGIN": "",
        "ELVERN_HELPER_RELEASES_DIR": str(work_root / "helper-releases"),
        "ELVERN_TRANSCODE_DIR": str(work_root / "transcodes"),
        "ELVERN_FFPROBE_PATH": str(fake_ffprobe),
        "ELVERN_ARGON2_TIME_COST": "1",
        "ELVERN_ARGON2_MEMORY_COST": "8192",
        "ELVERN_ARGON2_PARALLELISM": "1",
    }
    os.environ.update(safe_environment)
    get_settings.cache_clear()
    settings = refresh_settings()
    init_db(settings)
    ensure_admin_user(settings)
    return settings


def _seed_synthetic_library(settings, *, item_count: int, overlap_ratio: float) -> None:
    overlap_count = int(item_count * overlap_ratio)
    with get_connection(settings) as connection:
        source_id = ensure_current_shared_local_source_binding(settings, connection=connection)
        rows = []
        for item_id in range(1, item_count + 1):
            in_rail = item_id <= overlap_count
            rows.append((
                f"Synthetic Title {item_id:05d}",
                f"Synthetic.Title.{item_id:05d}.2160p.WEB-DL.HEVC.EAC3.mkv",
                f"synthetic://media/{item_id}",
                source_id,
                "synthetic-series" if in_rail else None,
                "Synthetic Series" if in_rail else None,
                str(Path(settings.media_root) / "Movies"),
                "list" if in_rail else "movie",
                str(Path(settings.media_root) / "Movies"),
                20 * 1024**3,
                1704067200.0 + item_id,
                7200.0,
                3840,
                2160,
                "hevc",
                "eac3",
                "mkv",
                2020 + (item_id % 6),
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                f"2026-01-{1 + (item_id % 20):02d}T00:00:00+00:00",
            ))
        connection.executemany(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                library_source_id,
                series_folder_key,
                series_folder_name,
                library_category,
                library_category_path,
                library_category_name,
                library_folder_role,
                library_folder_path,
                library_folder_name,
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
            ) VALUES (?, ?, ?, 'local', ?, ?, ?, 'movies', ?, 'Movies', ?, ?, 'Movies', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        progress_count = min(overlap_count, 6)
        connection.executemany(
            """
            INSERT INTO playback_progress (
                user_id,
                media_item_id,
                position_seconds,
                duration_seconds,
                watch_seconds_total,
                completed,
                updated_at
            ) VALUES (1, ?, 120, 7200, 120, 0, '2026-01-21T00:00:00+00:00')
            """,
            [(item_id,) for item_id in range(1, progress_count + 1)],
        )
        connection.commit()


def _count_v1_item_objects(payload: dict[str, object]) -> int:
    count = len(payload["items"]) + len(payload["continue_watching"]) + len(payload["recently_added"])
    for rail_name in ("series_rails", "cloud_series_rails"):
        count += sum(len(rail["items"]) for rail in payload[rail_name])
    return count


def _run_cell(item_count: int, overlap_ratio: float, repetitions: int) -> dict[str, object]:
    cache_root = Path.home() / ".cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory(prefix="elvern-phase4-appdata-", dir=cache_root) as temp_dir,
        tempfile.TemporaryDirectory(prefix="elvern-phase4-media-", dir=cache_root) as media_dir,
    ):
        settings = _configure_isolated_settings(Path(temp_dir), Path(media_dir))
        _seed_synthetic_library(settings, item_count=item_count, overlap_ratio=overlap_ratio)
        invalidate_poster_indexes()

        timings: dict[str, list[float]] = {
            "view_plan_build": [],
            "v1_serialization": [],
            "v2_serialization": [],
            "v1_json_encode": [],
            "v2_json_encode": [],
        }
        poster_resolve_counts = {"v1": [], "v2": []}
        original_presentation_resolver = library_presentation_service._poster_url_for_row
        original_v2_resolver = library_service._poster_url_for_row
        active_counter = {"value": 0}

        def counted_presentation_resolver(*args, **kwargs):
            active_counter["value"] += 1
            return original_presentation_resolver(*args, **kwargs)

        def counted_v2_resolver(*args, **kwargs):
            active_counter["value"] += 1
            return original_v2_resolver(*args, **kwargs)

        library_presentation_service._poster_url_for_row = counted_presentation_resolver
        library_service._poster_url_for_row = counted_v2_resolver
        try:
            v1_payload = v2_payload = {}
            v1_json = v2_json = b""
            for _ in range(repetitions):
                started = time.perf_counter()
                plan = library_service.build_library_view_plan(settings, user_id=1)
                timings["view_plan_build"].append((time.perf_counter() - started) * 1000)

                active_counter["value"] = 0
                started = time.perf_counter()
                v1_payload = library_service.serialize_library_view_v1(settings, plan)
                timings["v1_serialization"].append((time.perf_counter() - started) * 1000)
                poster_resolve_counts["v1"].append(active_counter["value"])

                active_counter["value"] = 0
                started = time.perf_counter()
                v2_payload = library_service.serialize_library_view_v2(
                    settings,
                    plan,
                    scan_in_progress=False,
                )
                timings["v2_serialization"].append((time.perf_counter() - started) * 1000)
                poster_resolve_counts["v2"].append(active_counter["value"])

                started = time.perf_counter()
                v1_json = json.dumps(v1_payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                timings["v1_json_encode"].append((time.perf_counter() - started) * 1000)
                started = time.perf_counter()
                v2_json = json.dumps(v2_payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                timings["v2_json_encode"].append((time.perf_counter() - started) * 1000)
        finally:
            library_presentation_service._poster_url_for_row = original_presentation_resolver
            library_service._poster_url_for_row = original_v2_resolver

        poster_metrics = get_poster_index_metrics()
        v1_gzip_bytes = len(gzip.compress(v1_json))
        v2_gzip_bytes = len(gzip.compress(v2_json))
        return {
            "item_count": item_count,
            "section_overlap_percent": int(overlap_ratio * 100),
            "unique_item_count": len(v2_payload["items_by_id"]),
            "repeated_v1_item_object_count": _count_v1_item_objects(v1_payload),
            **{name: _timing_summary(values) for name, values in timings.items()},
            "v1_uncompressed_bytes": len(v1_json),
            "v2_uncompressed_bytes": len(v2_json),
            "v1_gzip_bytes": v1_gzip_bytes,
            "v2_gzip_bytes": v2_gzip_bytes,
            "uncompressed_reduction_ratio": round(1 - (len(v2_json) / len(v1_json)), 4),
            "gzip_reduction_ratio": round(1 - (v2_gzip_bytes / v1_gzip_bytes), 4),
            "poster_index_directory_iterations": poster_metrics["directory_iteration_count"],
            "v1_poster_resolve_count": int(statistics.median(poster_resolve_counts["v1"])),
            "v2_poster_resolve_count": int(statistics.median(poster_resolve_counts["v2"])),
        }


def run_benchmark(repetitions: int) -> dict[str, object]:
    return {
        "kind": "isolated_synthetic_production_library_view_plan",
        "repetitions_per_cell": repetitions,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "processor": platform.processor() or "unknown",
        "commit": _git_commit(),
        "private_data_used": False,
        "live_database_used": False,
        "cells": [
            _run_cell(item_count, overlap_ratio, repetitions)
            for item_count in ITEM_COUNTS
            for overlap_ratio in OVERLAP_RATIOS
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark v1/v2 serializers with an isolated synthetic Elvern database.")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    if args.repetitions < 5:
        parser.error("--repetitions must be at least 5")
    report = run_benchmark(args.repetitions)
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(f"{encoded}\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
