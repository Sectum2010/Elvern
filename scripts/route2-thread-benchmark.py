#!/usr/bin/env python3
"""Developer-only Route2-style ffmpeg thread scaling benchmark.

This script runs isolated local ffmpeg preparations for one input path with a
range of thread counts. It does not touch live Route2 sessions or production
cache directories.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_THREADS = (2, 4, 6, 8, 10, 12)


def _clock_ticks_per_second() -> int:
    try:
        return max(1, int(os.sysconf("SC_CLK_TCK")))
    except (AttributeError, ValueError, OSError):
        return 100


def _read_process_cpu_seconds(pid: int) -> float | None:
    try:
        payload = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    close_index = payload.rfind(")")
    if close_index < 0 or close_index + 2 >= len(payload):
        return None
    tail = payload[close_index + 2 :].split()
    if len(tail) <= 12:
        return None
    try:
        return (int(tail[11]) + int(tail[12])) / _clock_ticks_per_second()
    except ValueError:
        return None


def _read_process_rss_bytes(pid: int) -> int | None:
    try:
        payload = (Path("/proc") / str(pid) / "status").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line.startswith("VmRSS:"):
            continue
        parts = line.split()
        if len(parts) < 2:
            return None
        try:
            return int(parts[1]) * 1024
        except ValueError:
            return None
    return None


def _manifest_generated_seconds(manifest_path: Path) -> float:
    try:
        payload = manifest_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0.0
    total = 0.0
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line.startswith("#EXTINF:"):
            continue
        value = line.removeprefix("#EXTINF:").split(",", 1)[0]
        try:
            total += max(0.0, float(value))
        except ValueError:
            continue
    return total


def _parse_thread_counts(raw_values: list[str]) -> list[int]:
    values: list[int] = []
    for raw in raw_values:
        for chunk in str(raw).split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            value = int(chunk)
            if value < 1:
                raise ValueError("thread counts must be positive")
            values.append(value)
    return sorted(dict.fromkeys(values))


def _run_one(args: argparse.Namespace, *, thread_count: int, run_root: Path) -> dict[str, object]:
    output_dir = run_root / f"threads-{thread_count}"
    output_dir.mkdir(parents=True, exist_ok=True)
    stderr_path = output_dir / "ffmpeg.stderr.log"
    manifest_path = output_dir / "index.m3u8"
    segment_pattern = output_dir / "segment_%06d.m4s"
    command = [
        args.ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        str(args.input),
        "-t",
        str(args.sample_seconds),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        args.preset,
        "-threads",
        str(thread_count),
        "-c:a",
        "aac",
        "-f",
        "hls",
        "-hls_time",
        str(args.hls_time),
        "-hls_segment_type",
        "fmp4",
        "-hls_flags",
        "independent_segments",
        "-hls_playlist_type",
        "event",
        "-hls_segment_filename",
        str(segment_pattern),
        str(manifest_path),
    ]
    if args.scale:
        command[command.index("-c:v") : command.index("-c:v")] = ["-vf", f"scale={args.scale}"]

    start_wall = time.monotonic()
    first_segment_wall: float | None = None
    runway_45_wall: float | None = None
    runway_120_wall: float | None = None
    peak_cpu_cores = 0.0
    peak_rss_bytes = 0
    cpu_start: float | None = None
    cpu_end: float | None = None
    last_cpu_sample: tuple[float, float] | None = None
    generated_seconds = 0.0
    success = False

    with stderr_path.open("w", encoding="utf-8", errors="replace") as stderr_stream:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=stderr_stream, text=True)
        while process.poll() is None:
            now = time.monotonic()
            elapsed = now - start_wall
            cpu_seconds = _read_process_cpu_seconds(process.pid)
            rss_bytes = _read_process_rss_bytes(process.pid)
            generated_seconds = _manifest_generated_seconds(manifest_path)
            if cpu_seconds is not None:
                cpu_start = cpu_seconds if cpu_start is None else cpu_start
                cpu_end = cpu_seconds
                if last_cpu_sample is not None:
                    last_wall, last_cpu = last_cpu_sample
                    delta_wall = max(0.001, now - last_wall)
                    peak_cpu_cores = max(peak_cpu_cores, max(0.0, cpu_seconds - last_cpu) / delta_wall)
                last_cpu_sample = (now, cpu_seconds)
            if rss_bytes is not None:
                peak_rss_bytes = max(peak_rss_bytes, rss_bytes)
            if first_segment_wall is None and any(output_dir.glob("segment_*.m4s")):
                first_segment_wall = elapsed
            if runway_45_wall is None and generated_seconds >= 45.0:
                runway_45_wall = elapsed
            if runway_120_wall is None and generated_seconds >= 120.0:
                runway_120_wall = elapsed
            time.sleep(args.sample_interval)
        return_code = process.wait()
        success = return_code == 0

    wall_seconds = max(0.001, time.monotonic() - start_wall)
    generated_seconds = _manifest_generated_seconds(manifest_path)
    avg_cpu_cores = (
        max(0.0, float(cpu_end) - float(cpu_start)) / wall_seconds
        if cpu_start is not None and cpu_end is not None
        else None
    )
    return {
        "input": str(args.input),
        "thread_count": thread_count,
        "wall_seconds": round(wall_seconds, 3),
        "time_to_first_segment_seconds": round(first_segment_wall, 3) if first_segment_wall is not None else None,
        "time_to_45s_runway_seconds": round(runway_45_wall, 3) if runway_45_wall is not None else None,
        "time_to_120s_runway_seconds": round(runway_120_wall, 3) if runway_120_wall is not None else None,
        "generated_seconds": round(generated_seconds, 3),
        "supply_rate_x": round(generated_seconds / wall_seconds, 3),
        "avg_cpu_cores_used": round(avg_cpu_cores, 3) if avg_cpu_cores is not None else None,
        "peak_cpu_cores_used": round(peak_cpu_cores, 3),
        "peak_rss_bytes": peak_rss_bytes or None,
        "success": success,
        "ffmpeg_stderr_path": str(stderr_path),
        "manifest_path": str(manifest_path),
        "command": command,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated Route2-style ffmpeg thread scaling benchmarks.")
    parser.add_argument("--input", required=True, type=Path, help="Local media file to benchmark.")
    parser.add_argument("--ffmpeg", default=os.environ.get("ELVERN_FFMPEG_PATH", "ffmpeg"))
    parser.add_argument("--threads", nargs="*", default=[",".join(str(value) for value in DEFAULT_THREADS)])
    parser.add_argument("--sample-seconds", type=float, default=150.0)
    parser.add_argument("--sample-interval", type=float, default=0.5)
    parser.add_argument("--hls-time", type=float, default=2.0)
    parser.add_argument("--preset", default="veryfast")
    parser.add_argument("--scale", default="", help="Optional ffmpeg scale expression, e.g. 1920:-2.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dev/artifacts/route2-thread-benchmark"),
        help="Benchmark artifact root. Generated outputs are intentionally outside production cache.",
    )
    args = parser.parse_args()
    if not args.input.exists():
        parser.error(f"input does not exist: {args.input}")
    thread_counts = _parse_thread_counts(args.threads)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = args.output_dir / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    results = [_run_one(args, thread_count=thread_count, run_root=run_root) for thread_count in thread_counts]
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input),
        "sample_seconds": args.sample_seconds,
        "threads": thread_counts,
        "results": results,
    }
    json_path = run_root / "summary.json"
    csv_path = run_root / "summary.csv"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "input",
            "thread_count",
            "wall_seconds",
            "time_to_first_segment_seconds",
            "time_to_45s_runway_seconds",
            "time_to_120s_runway_seconds",
            "generated_seconds",
            "supply_rate_x",
            "avg_cpu_cores_used",
            "peak_cpu_cores_used",
            "peak_rss_bytes",
            "success",
            "ffmpeg_stderr_path",
            "manifest_path",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow({key: result.get(key) for key in fieldnames})
    print(json_path)
    print(csv_path)
    return 0 if all(result["success"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
