#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.services.library_presentation_service import (  # noqa: E402
    _resolve_poster_path,
    _resolve_poster_path_legacy,
)
from backend.app.services.poster_index_service import (  # noqa: E402
    get_poster_index_metrics,
    get_poster_index_snapshot,
    invalidate_poster_indexes,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark Poster Index v1 with synthetic filenames in a temporary directory.",
    )
    parser.add_argument("--items", type=int, default=1000, help="Number of synthetic media lookups.")
    parser.add_argument("--posters", type=int, default=3000, help="Number of synthetic poster files.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a text table.")
    return parser.parse_args()


def _create_fixture(root: Path, poster_count: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for index in range(poster_count):
        year = 1950 + (index % 75)
        # Lowercase "the" makes these normalized matches instead of direct exact-path hits.
        (root / f"Synthetic Movie {index}: The Story ({year}).jpg").write_bytes(b"poster")


def _lookup_arguments(index: int, poster_count: int) -> dict[str, object]:
    poster_index = index % poster_count
    year = 1950 + (poster_index % 75)
    return {
        "title": f"Synthetic Movie {poster_index}: the Story",
        "year": year,
        "original_filename": f"Synthetic.Movie.{poster_index}.the.Story.{year}.mkv",
    }


def run_benchmark(*, item_count: int, poster_count: int) -> dict[str, int | float]:
    if item_count <= 0 or poster_count <= 0:
        raise ValueError("--items and --posters must both be positive integers")

    with TemporaryDirectory(prefix="elvern-poster-index-benchmark-") as temporary_directory:
        poster_root = Path(temporary_directory) / "posters"
        _create_fixture(poster_root, poster_count)
        invalidate_poster_indexes()

        cold_started = perf_counter()
        snapshot = get_poster_index_snapshot(poster_root)
        cold_build_ms = (perf_counter() - cold_started) * 1000
        if snapshot is None or snapshot.entry_count != poster_count:
            raise RuntimeError("Poster Index fixture did not build completely")
        cold_metrics = get_poster_index_metrics()

        lookups = [_lookup_arguments(index, poster_count) for index in range(item_count)]
        warm_started = perf_counter()
        indexed_results = [
            _resolve_poster_path(
                None,  # settings is unused when poster_dir is explicit
                poster_dir=poster_root,
                poster_index=snapshot,
                **arguments,
            )
            for arguments in lookups
        ]
        warm_lookup_ms = (perf_counter() - warm_started) * 1000

        legacy_started = perf_counter()
        legacy_results = [
            _resolve_poster_path_legacy(
                None,
                poster_dir=poster_root,
                **arguments,
            )
            for arguments in lookups
        ]
        legacy_total_ms = (perf_counter() - legacy_started) * 1000

        if indexed_results != legacy_results or any(result is None for result in indexed_results):
            raise RuntimeError("Indexed and legacy resolver results diverged during benchmark")

        return {
            "item_count": item_count,
            "poster_count": poster_count,
            "cold_index_build_ms": round(cold_build_ms, 3),
            "warm_lookup_total_ms": round(warm_lookup_ms, 3),
            "warm_lookup_average_us": round((warm_lookup_ms * 1000) / item_count, 3),
            "legacy_resolver_total_ms": round(legacy_total_ms, 3),
            "legacy_lookup_average_us": round((legacy_total_ms * 1000) / item_count, 3),
            "index_directory_iteration_count": cold_metrics["directory_iteration_count"],
            "legacy_directory_iteration_count": item_count,
            "index_entry_stat_count": cold_metrics["entry_stat_count"],
            "speedup_ratio": round(legacy_total_ms / max(warm_lookup_ms, 0.000001), 2),
        }


def main() -> int:
    args = _arguments()
    try:
        result = run_benchmark(item_count=args.items, poster_count=args.posters)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"benchmark failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    print("Elvern Poster Index v1 benchmark (synthetic temporary data)")
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
