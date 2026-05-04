#!/usr/bin/env python3
"""Isolated Route2 canonical shared-output generator experiment.

This developer-only script writes bounded HLS fMP4 artifacts under
``dev/artifacts`` and never touches live Route2 sessions or the production
``browser_playback_route2/shared_outputs`` store.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.services.mobile_playback_models import (  # noqa: E402
    MOBILE_PROFILES,
    SEGMENT_DURATION_SECONDS,
)


SCRIPT_VERSION = "route2-canonical-generator-experiment-v2"
DEFAULT_ARTIFACT_BASE = PROJECT_ROOT / "dev" / "artifacts" / "route2-canonical-generator"
DEFAULT_DB_PATH = PROJECT_ROOT / "backend" / "data" / "elvern.db"
DEFAULT_START_SECONDS = 30.0
DEFAULT_SEGMENT_COUNT = 4
DEFAULT_THREADS = 4
TIMESTAMP_TOLERANCE_SECONDS = 0.25
CONTAINER_BOXES = {
    b"moov",
    b"trak",
    b"mdia",
    b"minf",
    b"stbl",
    b"moof",
    b"traf",
}


@dataclass(frozen=True, slots=True)
class SourceSelection:
    path: Path
    basename: str
    duration_seconds: float | None
    source_kind: str = "local"
    selected_from_db: bool = False


@dataclass(slots=True)
class RunResult:
    label: str
    directory: Path
    command_redacted: list[str]
    return_code: int
    wall_seconds: float
    init_hash: str | None
    init_size_bytes: int | None
    segment_hashes: dict[int, str]
    segment_sizes: dict[int, int]
    segment_tfdt: dict[int, dict[str, Any]]
    manifest_segments: list[str]
    expected_indexes: list[int]
    filename_validation: dict[str, Any]
    timestamp_validation: dict[str, Any]
    keyframe_validation: dict[str, Any]
    stderr_path: str


@dataclass(frozen=True, slots=True)
class StrategySpec:
    name: str
    command_mode: str
    description: str
    expected_result: str
    threads_override: int | None = None
    start_index_override: int | None = None

    def threads_for(self, default_threads: int) -> int:
        return max(1, int(self.threads_override or default_threads))


def _utc_timestamp_for_path() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _json_default(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object is not JSON serializable: {type(value)!r}")


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size_bytes += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size_bytes


def _redact_path(value: str, source_path: Path) -> str:
    text = str(value)
    source_text = str(source_path)
    if source_text in text:
        text = text.replace(source_text, f"<source:{source_path.name}>")
    return text


def _redact_command(command: list[str], source_path: Path) -> list[str]:
    return [_redact_path(part, source_path) for part in command]


def _run_subprocess(command: list[str], *, source_path: Path, stderr_path: Path, timeout_seconds: float) -> int:
    started_at = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
        )
        stderr_payload = completed.stderr or ""
        return_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        stderr_payload = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        stderr_payload += f"\nTimed out after {timeout_seconds:.1f}s\n"
        return_code = 124
    stderr_payload = _redact_path(stderr_payload, source_path)
    stderr_payload += f"\n[wall_seconds] {time.monotonic() - started_at:.3f}\n"
    stderr_path.write_text(stderr_payload, encoding="utf-8", errors="replace")
    return return_code


def _probe_duration(ffprobe: str, source_path: Path) -> float | None:
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(source_path),
    ]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        return float(completed.stdout.strip())
    except ValueError:
        return None


def _select_source(args: argparse.Namespace, *, required_end_seconds: float) -> SourceSelection:
    if args.source_path is not None:
        source_path = args.source_path.expanduser().resolve()
        if not source_path.exists() or not source_path.is_file():
            raise SystemExit(f"Source path is not a file: {source_path}")
        return SourceSelection(
            path=source_path,
            basename=source_path.name,
            duration_seconds=_probe_duration(args.ffprobe, source_path),
            selected_from_db=False,
        )

    db_path = args.db_path.expanduser().resolve()
    if not db_path.exists():
        raise SystemExit(f"No --source-path provided and DB path does not exist: {db_path}")
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT file_path, original_filename, duration_seconds
            FROM media_items
            WHERE COALESCE(source_kind, 'local') = 'local'
              AND file_path IS NOT NULL
              AND file_path != ''
              AND COALESCE(duration_seconds, 0) >= ?
            ORDER BY duration_seconds ASC, file_size ASC
            """,
            (required_end_seconds + 2.0,),
        ).fetchall()
    finally:
        connection.close()

    for row in rows:
        candidate = Path(str(row["file_path"])).expanduser()
        if not candidate.exists() or not candidate.is_file():
            continue
        return SourceSelection(
            path=candidate.resolve(),
            basename=str(row["original_filename"] or candidate.name),
            duration_seconds=float(row["duration_seconds"]) if row["duration_seconds"] is not None else None,
            selected_from_db=True,
        )
    raise SystemExit("No existing local DB media item was long enough for the requested canonical range.")


def _read_u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big", signed=False)


def _read_u64(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 8], "big", signed=False)


def _iter_boxes(data: bytes, start: int = 0, end: int | None = None):
    limit = len(data) if end is None else min(len(data), end)
    offset = start
    while offset + 8 <= limit:
        size = _read_u32(data, offset)
        box_type = data[offset + 4 : offset + 8]
        header_size = 8
        if size == 1:
            if offset + 16 > limit:
                break
            size = _read_u64(data, offset + 8)
            header_size = 16
        elif size == 0:
            size = limit - offset
        if size < header_size or offset + size > limit:
            break
        yield box_type, offset + header_size, offset + size
        offset += size


def _find_child_boxes(data: bytes, parent_start: int, parent_end: int, box_type: bytes) -> list[tuple[int, int]]:
    return [(start, end) for current_type, start, end in _iter_boxes(data, parent_start, parent_end) if current_type == box_type]


def _find_first_child(data: bytes, parent_start: int, parent_end: int, box_type: bytes) -> tuple[int, int] | None:
    matches = _find_child_boxes(data, parent_start, parent_end, box_type)
    return matches[0] if matches else None


def _parse_tkhd_track_id(data: bytes, start: int, end: int) -> int | None:
    if start + 24 > end:
        return None
    version = data[start]
    track_id_offset = start + (20 if version == 1 else 12)
    if track_id_offset + 4 > end:
        return None
    return _read_u32(data, track_id_offset)


def _parse_mdhd_timescale(data: bytes, start: int, end: int) -> int | None:
    if start + 24 > end:
        return None
    version = data[start]
    timescale_offset = start + (20 if version == 1 else 12)
    if timescale_offset + 4 > end:
        return None
    value = _read_u32(data, timescale_offset)
    return value or None


def _parse_hdlr_type(data: bytes, start: int, end: int) -> str | None:
    handler_offset = start + 8
    if handler_offset + 4 > end:
        return None
    return data[handler_offset : handler_offset + 4].decode("ascii", errors="replace")


def _parse_init_tracks(init_path: Path) -> dict[int, dict[str, Any]]:
    try:
        data = init_path.read_bytes()
    except OSError:
        return {}
    tracks: dict[int, dict[str, Any]] = {}
    for moov_start, moov_end in _find_child_boxes(data, 0, len(data), b"moov"):
        for trak_start, trak_end in _find_child_boxes(data, moov_start, moov_end, b"trak"):
            tkhd = _find_first_child(data, trak_start, trak_end, b"tkhd")
            mdia = _find_first_child(data, trak_start, trak_end, b"mdia")
            if tkhd is None or mdia is None:
                continue
            track_id = _parse_tkhd_track_id(data, *tkhd)
            if track_id is None:
                continue
            mdhd = _find_first_child(data, mdia[0], mdia[1], b"mdhd")
            hdlr = _find_first_child(data, mdia[0], mdia[1], b"hdlr")
            tracks[track_id] = {
                "track_id": track_id,
                "timescale": _parse_mdhd_timescale(data, *mdhd) if mdhd else None,
                "handler_type": _parse_hdlr_type(data, *hdlr) if hdlr else None,
            }
    return tracks


def _parse_segment_tfdt(segment_path: Path, tracks: dict[int, dict[str, Any]]) -> dict[str, Any]:
    try:
        data = segment_path.read_bytes()
    except OSError as exc:
        return {"available": False, "reason": str(exc), "trafs": []}
    trafs: list[dict[str, Any]] = []
    for moof_start, moof_end in _find_child_boxes(data, 0, len(data), b"moof"):
        for traf_start, traf_end in _find_child_boxes(data, moof_start, moof_end, b"traf"):
            tfhd = _find_first_child(data, traf_start, traf_end, b"tfhd")
            tfdt = _find_first_child(data, traf_start, traf_end, b"tfdt")
            track_id = _read_u32(data, tfhd[0] + 4) if tfhd and tfhd[0] + 8 <= tfhd[1] else None
            base_decode_time = None
            if tfdt is not None and tfdt[0] + 8 <= tfdt[1]:
                version = data[tfdt[0]]
                base_decode_time = _read_u64(data, tfdt[0] + 4) if version == 1 else _read_u32(data, tfdt[0] + 4)
            track = tracks.get(int(track_id)) if track_id is not None else None
            timescale = track.get("timescale") if track else None
            trafs.append(
                {
                    "track_id": track_id,
                    "handler_type": track.get("handler_type") if track else None,
                    "timescale": timescale,
                    "base_media_decode_time": base_decode_time,
                    "base_media_decode_time_seconds": (
                        round(float(base_decode_time) / float(timescale), 6)
                        if base_decode_time is not None and timescale
                        else None
                    ),
                }
            )
    return {"available": bool(trafs), "trafs": trafs}


def _video_tfdt_seconds(tfdt_payload: dict[str, Any]) -> float | None:
    for traf in tfdt_payload.get("trafs") or []:
        if traf.get("handler_type") == "vide" and traf.get("base_media_decode_time_seconds") is not None:
            return float(traf["base_media_decode_time_seconds"])
    for traf in tfdt_payload.get("trafs") or []:
        if traf.get("base_media_decode_time_seconds") is not None:
            return float(traf["base_media_decode_time_seconds"])
    return None


def _parse_manifest_segments(manifest_path: Path) -> list[str]:
    try:
        payload = manifest_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    segments: list[str] = []
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith(".m4s") or ".m4s" in line:
            segments.append(Path(line).name)
    return segments


def _explicit_boundary_times(
    *,
    start_index: int,
    segment_count: int,
    segment_duration_seconds: float,
) -> list[str]:
    return [
        f"{(start_index + offset) * segment_duration_seconds:.6f}"
        for offset in range(segment_count)
    ]


def _build_canonical_ffmpeg_command(
    *,
    ffmpeg: str,
    source_path: Path,
    run_dir: Path,
    profile_key: str,
    threads: int,
    start_index: int,
    segment_count: int,
    segment_duration_seconds: float,
    strategy: StrategySpec,
) -> list[str]:
    profile = MOBILE_PROFILES[profile_key]
    canonical_start_seconds = start_index * segment_duration_seconds
    output_seconds = segment_count * segment_duration_seconds
    scale_filter = (
        f"scale=w='min({profile.max_width},iw)':h='min({profile.max_height},ih)':"
        "force_original_aspect_ratio=decrease"
    )
    keyframe_interval = max(1, int(segment_duration_seconds * 24))
    segment_pattern = run_dir / "abs_%012d.m4s"
    manifest_path = run_dir / "index.m3u8"
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-y",
        "-threads",
        str(max(1, int(threads))),
    ]
    if strategy.command_mode == "window_relative":
        command.extend(
            [
                "-ss",
                f"{canonical_start_seconds:.6f}",
                "-i",
                str(source_path),
                "-t",
                f"{output_seconds:.6f}",
                "-output_ts_offset",
                f"{canonical_start_seconds:.6f}",
            ]
        )
        force_key_frames = f"expr:gte(t,n_forced*{segment_duration_seconds})"
    elif strategy.command_mode == "copyts":
        command.extend(
            [
                "-copyts",
                "-seek_timestamp",
                "1",
                "-ss",
                f"{canonical_start_seconds:.6f}",
                "-i",
                str(source_path),
                "-t",
                f"{output_seconds:.6f}",
                "-avoid_negative_ts",
                "disabled",
            ]
        )
        force_key_frames = f"expr:gte(t,n_forced*{segment_duration_seconds})"
    elif strategy.command_mode == "copyts_explicit_keyframes":
        command.extend(
            [
                "-copyts",
                "-seek_timestamp",
                "1",
                "-ss",
                f"{canonical_start_seconds:.6f}",
                "-i",
                str(source_path),
                "-t",
                f"{output_seconds:.6f}",
                "-avoid_negative_ts",
                "disabled",
            ]
        )
        force_key_frames = ",".join(
            _explicit_boundary_times(
                start_index=start_index,
                segment_count=segment_count + 1,
                segment_duration_seconds=segment_duration_seconds,
            )
        )
    else:
        raise ValueError(f"Unknown canonical generator command mode: {strategy.command_mode}")
    command.extend(
        [
        "-muxpreload",
        "0",
        "-muxdelay",
        "0",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-sn",
        "-dn",
        "-vf",
        scale_filter,
        "-c:v",
        "libx264",
        "-preset",
        "superfast",
        "-profile:v",
        "high",
        "-level:v",
        profile.level,
        "-pix_fmt",
        "yuv420p",
        "-crf",
        str(profile.crf),
        "-maxrate",
        profile.maxrate,
        "-bufsize",
        profile.bufsize,
        "-g",
        str(keyframe_interval),
        "-keyint_min",
        str(keyframe_interval),
        "-sc_threshold",
        "0",
        "-force_key_frames",
        force_key_frames,
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-ar",
        "48000",
        "-b:a",
        "160k",
        "-max_muxing_queue_size",
        "2048",
        "-f",
        "hls",
        "-hls_time",
        f"{segment_duration_seconds:.6f}",
        "-hls_list_size",
        "0",
        "-hls_segment_type",
        "fmp4",
        "-hls_fmp4_init_filename",
        "init.mp4",
        "-hls_flags",
        "independent_segments+temp_file",
        "-start_number",
        str(start_index),
        "-hls_segment_filename",
        str(segment_pattern),
        str(manifest_path),
        ]
    )
    return command


def _validate_filenames(
    *,
    run_dir: Path,
    expected_indexes: list[int],
    manifest_segments: list[str],
) -> dict[str, Any]:
    expected_names = [f"abs_{index:012d}.m4s" for index in expected_indexes]
    actual_names = sorted(path.name for path in run_dir.glob("abs_*.m4s"))
    return {
        "expected_names": expected_names,
        "actual_names": actual_names,
        "manifest_segments": manifest_segments,
        "segment_count_valid": len(actual_names) == len(expected_names),
        "filenames_match_expected": actual_names == expected_names,
        "manifest_matches_expected": manifest_segments == expected_names,
        "gaps": [name for name in expected_names if name not in actual_names],
        "unexpected": [name for name in actual_names if name not in expected_names],
    }


def _validate_timestamps(
    *,
    segment_tfdt: dict[int, dict[str, Any]],
    expected_indexes: list[int],
    segment_duration_seconds: float,
) -> dict[str, Any]:
    missing: list[int] = []
    mismatches: list[dict[str, Any]] = []
    for index in expected_indexes:
        tfdt_payload = segment_tfdt.get(index)
        if not tfdt_payload or not tfdt_payload.get("available"):
            missing.append(index)
            continue
        video_seconds = _video_tfdt_seconds(tfdt_payload)
        expected_seconds = round(index * segment_duration_seconds, 6)
        if video_seconds is None:
            missing.append(index)
            continue
        if abs(video_seconds - expected_seconds) > TIMESTAMP_TOLERANCE_SECONDS:
            mismatches.append(
                {
                    "index": index,
                    "expected_seconds": expected_seconds,
                    "actual_video_tfdt_seconds": video_seconds,
                }
            )
    if missing:
        return {
            "status": "incomplete",
            "reason": "missing_tfdt",
            "missing_indexes": missing,
            "mismatches": mismatches,
        }
    if mismatches:
        return {
            "status": "failed",
            "reason": "tfdt_not_absolute_aligned",
            "missing_indexes": [],
            "mismatches": mismatches,
        }
    return {
        "status": "pass",
        "reason": "video_tfdt_matches_absolute_segment_start",
        "missing_indexes": [],
        "mismatches": [],
    }


def _write_probe_manifest(run_dir: Path, segment_name: str, segment_duration_seconds: float) -> Path:
    probe_dir = run_dir / "probe_manifests"
    probe_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = probe_dir / f"{Path(segment_name).stem}.m3u8"
    manifest_path.write_text(
        "\n".join(
            [
                "#EXTM3U",
                "#EXT-X-VERSION:7",
                f"#EXT-X-TARGETDURATION:{max(1, math.ceil(segment_duration_seconds))}",
                '#EXT-X-MAP:URI="../init.mp4"',
                f"#EXTINF:{segment_duration_seconds:.6f},",
                f"../{segment_name}",
                "#EXT-X-ENDLIST",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return manifest_path


def _first_video_packet_is_keyframe(ffprobe: str, manifest_path: Path, timeout_seconds: float) -> tuple[bool | None, str | None]:
    command = [
        ffprobe,
        "-v",
        "error",
        "-allowed_extensions",
        "ALL",
        "-select_streams",
        "v:0",
        "-show_packets",
        "-read_intervals",
        "%+#1",
        "-show_entries",
        "packet=flags,pts_time,dts_time",
        "-of",
        "json",
        str(manifest_path),
    ]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout_seconds)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if completed.returncode != 0:
        return None, completed.stderr.strip() or f"ffprobe exited {completed.returncode}"
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        return None, str(exc)
    packets = payload.get("packets")
    if not isinstance(packets, list) or not packets:
        return None, "no_video_packets"
    flags = str(packets[0].get("flags") or "")
    return ("K" in flags), None


def _validate_keyframes(
    *,
    ffprobe: str,
    run_dir: Path,
    expected_indexes: list[int],
    segment_duration_seconds: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    unavailable: list[dict[str, Any]] = []
    non_keyframes: list[int] = []
    checked: list[int] = []
    for index in expected_indexes:
        segment_name = f"abs_{index:012d}.m4s"
        if not (run_dir / segment_name).exists():
            unavailable.append({"index": index, "reason": "segment_missing"})
            continue
        probe_manifest = _write_probe_manifest(run_dir, segment_name, segment_duration_seconds)
        is_keyframe, reason = _first_video_packet_is_keyframe(ffprobe, probe_manifest, timeout_seconds)
        if is_keyframe is None:
            unavailable.append({"index": index, "reason": reason or "ffprobe_unavailable"})
            continue
        checked.append(index)
        if not is_keyframe:
            non_keyframes.append(index)
    if unavailable:
        return {
            "status": "incomplete",
            "reason": "ffprobe_packet_validation_incomplete",
            "checked_indexes": checked,
            "unavailable": unavailable,
            "non_keyframe_indexes": non_keyframes,
        }
    if non_keyframes:
        return {
            "status": "failed",
            "reason": "first_video_packet_not_keyframe",
            "checked_indexes": checked,
            "unavailable": [],
            "non_keyframe_indexes": non_keyframes,
        }
    return {
        "status": "pass",
        "reason": "first_video_packet_has_keyframe_flag",
        "checked_indexes": checked,
        "unavailable": [],
        "non_keyframe_indexes": [],
    }


def _collect_run_result(
    *,
    label: str,
    run_dir: Path,
    command_redacted: list[str],
    return_code: int,
    wall_seconds: float,
    expected_indexes: list[int],
    segment_duration_seconds: float,
    ffprobe: str,
    stderr_path: Path,
    timeout_seconds: float,
) -> RunResult:
    init_path = run_dir / "init.mp4"
    init_hash: str | None = None
    init_size: int | None = None
    if init_path.exists():
        init_hash, init_size = _hash_file(init_path)
    tracks = _parse_init_tracks(init_path) if init_path.exists() else {}
    segment_hashes: dict[int, str] = {}
    segment_sizes: dict[int, int] = {}
    segment_tfdt: dict[int, dict[str, Any]] = {}
    for path in sorted(run_dir.glob("abs_*.m4s")):
        stem = path.stem.removeprefix("abs_")
        try:
            index = int(stem)
        except ValueError:
            continue
        digest, size_bytes = _hash_file(path)
        segment_hashes[index] = digest
        segment_sizes[index] = size_bytes
        segment_tfdt[index] = _parse_segment_tfdt(path, tracks)
    manifest_segments = _parse_manifest_segments(run_dir / "index.m3u8")
    filename_validation = _validate_filenames(
        run_dir=run_dir,
        expected_indexes=expected_indexes,
        manifest_segments=manifest_segments,
    )
    timestamp_validation = _validate_timestamps(
        segment_tfdt=segment_tfdt,
        expected_indexes=expected_indexes,
        segment_duration_seconds=segment_duration_seconds,
    )
    keyframe_validation = _validate_keyframes(
        ffprobe=ffprobe,
        run_dir=run_dir,
        expected_indexes=expected_indexes,
        segment_duration_seconds=segment_duration_seconds,
        timeout_seconds=timeout_seconds,
    )
    return RunResult(
        label=label,
        directory=run_dir,
        command_redacted=command_redacted,
        return_code=return_code,
        wall_seconds=wall_seconds,
        init_hash=init_hash,
        init_size_bytes=init_size,
        segment_hashes=segment_hashes,
        segment_sizes=segment_sizes,
        segment_tfdt=segment_tfdt,
        manifest_segments=manifest_segments,
        expected_indexes=expected_indexes,
        filename_validation=filename_validation,
        timestamp_validation=timestamp_validation,
        keyframe_validation=keyframe_validation,
        stderr_path=str(stderr_path.relative_to(run_dir.parent)),
    )


def _run_generation(
    *,
    label: str,
    strategy_root: Path,
    source: SourceSelection,
    args: argparse.Namespace,
    start_index: int,
    segment_count: int,
    strategy: StrategySpec,
) -> RunResult:
    run_dir = strategy_root / label
    run_dir.mkdir(parents=True, exist_ok=True)
    thread_count = strategy.threads_for(args.threads)
    command = _build_canonical_ffmpeg_command(
        ffmpeg=args.ffmpeg,
        source_path=source.path,
        run_dir=run_dir,
        profile_key=args.profile,
        threads=thread_count,
        start_index=start_index,
        segment_count=segment_count,
        segment_duration_seconds=args.segment_duration_seconds,
        strategy=strategy,
    )
    command_redacted = _redact_command(command, source.path)
    (run_dir / "command.redacted.json").write_text(
        json.dumps(command_redacted, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    stderr_path = run_dir / "ffmpeg.stderr.log"
    started_at = time.monotonic()
    return_code = _run_subprocess(
        command,
        source_path=source.path,
        stderr_path=stderr_path,
        timeout_seconds=args.timeout_seconds,
    )
    wall_seconds = max(0.0, time.monotonic() - started_at)
    expected_indexes = list(range(start_index, start_index + segment_count))
    return _collect_run_result(
        label=label,
        run_dir=run_dir,
        command_redacted=command_redacted,
        return_code=return_code,
        wall_seconds=wall_seconds,
        expected_indexes=expected_indexes,
        segment_duration_seconds=args.segment_duration_seconds,
        ffprobe=args.ffprobe,
        stderr_path=stderr_path,
        timeout_seconds=args.timeout_seconds,
    )


def _compare_runs(
    reference: RunResult,
    candidate: RunResult,
    *,
    label: str,
    expected_indexes: list[int] | None = None,
) -> dict[str, Any]:
    indexes = expected_indexes if expected_indexes is not None else sorted(set(reference.segment_hashes) | set(candidate.segment_hashes))
    missing_in_reference = [index for index in indexes if index not in reference.segment_hashes]
    missing_in_candidate = [index for index in indexes if index not in candidate.segment_hashes]
    mismatches = [
        index
        for index in indexes
        if index in reference.segment_hashes
        and index in candidate.segment_hashes
        and reference.segment_hashes[index] != candidate.segment_hashes[index]
    ]
    timestamp_mismatches: list[dict[str, Any]] = []
    for index in indexes:
        ref_seconds = _video_tfdt_seconds(reference.segment_tfdt.get(index, {}))
        candidate_seconds = _video_tfdt_seconds(candidate.segment_tfdt.get(index, {}))
        if ref_seconds is None or candidate_seconds is None:
            timestamp_mismatches.append(
                {
                    "index": index,
                    "reference_tfdt_seconds": ref_seconds,
                    "candidate_tfdt_seconds": candidate_seconds,
                    "reason": "missing_tfdt",
                }
            )
        elif abs(ref_seconds - candidate_seconds) > TIMESTAMP_TOLERANCE_SECONDS:
            timestamp_mismatches.append(
                {
                    "index": index,
                    "reference_tfdt_seconds": ref_seconds,
                    "candidate_tfdt_seconds": candidate_seconds,
                    "reason": "tfdt_mismatch",
                }
            )
    init_hash_match = reference.init_hash is not None and reference.init_hash == candidate.init_hash
    passed = (
        init_hash_match
        and not missing_in_reference
        and not missing_in_candidate
        and not mismatches
        and not timestamp_mismatches
        and bool(indexes)
    )
    return {
        "label": label,
        "indexes_compared": indexes,
        "init_hash_match": init_hash_match,
        "segment_hashes_match": not mismatches and not missing_in_reference and not missing_in_candidate and bool(indexes),
        "timestamp_evidence_match": not timestamp_mismatches and bool(indexes),
        "missing_in_reference": missing_in_reference,
        "missing_in_candidate": missing_in_candidate,
        "segment_hash_mismatch_indexes": mismatches,
        "timestamp_mismatches": timestamp_mismatches,
        "pass": passed,
    }


def _run_to_summary(run: RunResult, artifact_root: Path) -> dict[str, Any]:
    return {
        "label": run.label,
        "directory": str(run.directory.relative_to(artifact_root)),
        "return_code": run.return_code,
        "wall_seconds": round(run.wall_seconds, 3),
        "command_redacted": run.command_redacted,
        "init_hash_sha256": run.init_hash,
        "init_size_bytes": run.init_size_bytes,
        "segment_hashes": {str(index): digest for index, digest in sorted(run.segment_hashes.items())},
        "segment_sizes": {str(index): size for index, size in sorted(run.segment_sizes.items())},
        "segment_tfdt": {str(index): payload for index, payload in sorted(run.segment_tfdt.items())},
        "manifest_segments": run.manifest_segments,
        "expected_indexes": run.expected_indexes,
        "filename_validation": run.filename_validation,
        "timestamp_validation": run.timestamp_validation,
        "keyframe_validation": run.keyframe_validation,
        "stderr_path": run.stderr_path,
    }


def _write_hash_table(artifact_root: Path, runs: list[RunResult]) -> None:
    all_indexes = sorted({index for run in runs for index in run.segment_hashes})
    with (artifact_root / "hash_table.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["absolute_index", "filename", *[f"{run.label}_sha256" for run in runs]])
        for index in all_indexes:
            writer.writerow(
                [
                    index,
                    f"abs_{index:012d}.m4s",
                    *[run.segment_hashes.get(index, "") for run in runs],
                ]
            )


def _build_summary(
    *,
    args: argparse.Namespace,
    artifact_root: Path,
    source: SourceSelection,
    strategy: StrategySpec,
    canonical_start_index: int,
    canonical_end_index_exclusive: int,
    runs: list[RunResult],
    comparisons: dict[str, Any],
    shifted: dict[str, Any] | None,
) -> dict[str, Any]:
    canonical_start_seconds = canonical_start_index * args.segment_duration_seconds
    canonical_end_seconds = canonical_end_index_exclusive * args.segment_duration_seconds
    blockers: list[str] = []
    for run in runs:
        if run.return_code != 0:
            blockers.append(f"{run.label}_ffmpeg_failed")
        if not run.filename_validation.get("filenames_match_expected"):
            blockers.append(f"{run.label}_filename_mismatch")
        if not run.filename_validation.get("segment_count_valid"):
            blockers.append(f"{run.label}_segment_count_invalid")
        if run.timestamp_validation.get("status") != "pass":
            blockers.append(f"{run.label}_timestamp_validation_{run.timestamp_validation.get('status')}")
        if run.keyframe_validation.get("status") != "pass":
            blockers.append(f"{run.label}_keyframe_validation_{run.keyframe_validation.get('status')}")
    for comparison_name, comparison in comparisons.items():
        if not comparison.get("pass"):
            blockers.append(f"{comparison_name}_failed")
    pass_fail = not blockers
    return {
        "script_version": SCRIPT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strategy": {
            "name": strategy.name,
            "command_mode": strategy.command_mode,
            "description": strategy.description,
            "expected_result": strategy.expected_result,
        },
        "source_kind": "local",
        "source": {
            "basename": source.basename,
            "selected_from_db": source.selected_from_db,
            "duration_seconds": source.duration_seconds,
        },
        "profile": args.profile,
        "threads": strategy.threads_for(args.threads),
        "segment_duration_seconds": args.segment_duration_seconds,
        "canonical_start_index": canonical_start_index,
        "canonical_end_index_exclusive": canonical_end_index_exclusive,
        "canonical_start_seconds": canonical_start_seconds,
        "canonical_end_seconds": canonical_end_seconds,
        "ffmpeg_command_family": strategy.name,
        "ffmpeg_command_preview_redacted": runs[0].command_redacted if runs else [],
        "run_directories": [str(run.directory.relative_to(artifact_root)) for run in runs],
        "init_hashes": {run.label: run.init_hash for run in runs},
        "segment_hash_table_by_absolute_index": {
            str(index): {run.label: run.segment_hashes.get(index) for run in runs}
            for index in sorted({idx for run in runs for idx in run.segment_hashes})
        },
        "run_a_vs_run_b": comparisons.get("run_a_vs_run_b"),
        "run_a_vs_shifted": comparisons.get("run_a_vs_shifted"),
        "timestamp_validation": {
            run.label: run.timestamp_validation for run in runs
        },
        "keyframe_validation": {
            run.label: run.keyframe_validation for run in runs
        },
        "shifted_target_check": shifted,
        "runs": [_run_to_summary(run, artifact_root) for run in runs],
        "blockers": sorted(set(blockers)),
        "pass": pass_fail,
        "production_shared_store_written": False,
        "serving_enabled": False,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate isolated canonical Route2-style HLS fMP4 artifacts and compare determinism."
    )
    parser.add_argument("--source-path", type=Path, default=None, help="Local source media path. Cloud URLs are not allowed.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH, help="DB used only for local auto-selection.")
    parser.add_argument("--start-seconds", type=float, default=None)
    parser.add_argument("--target-seconds", type=float, default=None)
    parser.add_argument("--segment-count", type=int, default=DEFAULT_SEGMENT_COUNT)
    parser.add_argument("--segment-duration-seconds", type=float, default=SEGMENT_DURATION_SECONDS)
    parser.add_argument("--profile", choices=sorted(MOBILE_PROFILES), default="mobile_1080p")
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--repeat-count", type=int, default=2)
    parser.add_argument("--shifted-target-check", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--strategies",
        nargs="*",
        default=[
            "current_window_relative_baseline",
            "copyts_absolute_candidate",
            "explicit_absolute_keyframes_candidate",
            "deterministic_isolation_candidate",
            "from_zero_reference_candidate",
        ],
        help="Strategy names to run. Default is the small Phase 1K-4B matrix.",
    )
    parser.add_argument(
        "--from-zero-reference",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include the from-zero diagnostic control when default strategies are used.",
    )
    parser.add_argument("--artifact-base", type=Path, default=DEFAULT_ARTIFACT_BASE)
    parser.add_argument("--ffmpeg", default=os.environ.get("ELVERN_FFMPEG_PATH", "ffmpeg"))
    parser.add_argument("--ffprobe", default=os.environ.get("ELVERN_FFPROBE_PATH", "ffprobe"))
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    args = parser.parse_args()
    if args.source_path is not None and str(args.source_path).startswith(("http://", "https://")):
        raise SystemExit("Cloud/URL sources are intentionally forbidden for this isolated local experiment.")
    if args.segment_count < 1:
        raise SystemExit("--segment-count must be positive")
    if args.segment_duration_seconds <= 0:
        raise SystemExit("--segment-duration-seconds must be positive")
    if args.repeat_count < 2:
        raise SystemExit("--repeat-count must be at least 2 for determinism comparison")
    if args.threads < 1:
        raise SystemExit("--threads must be positive")
    if args.start_seconds is not None and args.target_seconds is not None:
        raise SystemExit("Use either --start-seconds or --target-seconds, not both")
    if not args.from_zero_reference:
        args.strategies = [item for item in args.strategies if item != "from_zero_reference_candidate"]
    return args


def _strategy_specs(args: argparse.Namespace, canonical_start_index: int) -> list[StrategySpec]:
    strategies = {
        "current_window_relative_baseline": StrategySpec(
            name="current_window_relative_baseline",
            command_mode="window_relative",
            description=(
                "Baseline from Phase 1K-4A: middle seek, absolute filenames/start_number, "
                "but HLS fMP4 timestamps are expected to remain window-relative."
            ),
            expected_result="expected_fail",
        ),
        "copyts_absolute_candidate": StrategySpec(
            name="copyts_absolute_candidate",
            command_mode="copyts",
            description=(
                "Try to preserve input timestamps with -copyts, -seek_timestamp 1, and "
                "-avoid_negative_ts disabled instead of relying on output timestamp reset."
            ),
            expected_result="candidate",
        ),
        "explicit_absolute_keyframes_candidate": StrategySpec(
            name="explicit_absolute_keyframes_candidate",
            command_mode="copyts_explicit_keyframes",
            description=(
                "Try -copyts plus an explicit force_key_frames timestamp list at absolute "
                "canonical boundaries for this requested range."
            ),
            expected_result="candidate",
        ),
        "deterministic_isolation_candidate": StrategySpec(
            name="deterministic_isolation_candidate",
            command_mode="window_relative",
            description=(
                "Repeat the baseline command family with threads=1 to separate libx264 "
                "thread nondeterminism from timestamp/window-relative blockers."
            ),
            expected_result="diagnostic",
            threads_override=1,
        ),
        "from_zero_reference_candidate": StrategySpec(
            name="from_zero_reference_candidate",
            command_mode="window_relative",
            description=(
                "Diagnostic control from media time zero; this is not a production proposal "
                "for middle seeks, only a reference for timestamp/hash behavior near zero."
            ),
            expected_result="diagnostic",
            start_index_override=0,
        ),
    }
    selected: list[StrategySpec] = []
    for name in args.strategies:
        if name not in strategies:
            raise SystemExit(f"Unknown strategy: {name}")
        selected.append(strategies[name])
    return selected


def _run_strategy(
    *,
    strategy: StrategySpec,
    artifact_root: Path,
    source: SourceSelection,
    args: argparse.Namespace,
    default_start_index: int,
) -> dict[str, object]:
    strategy_root = artifact_root / strategy.name
    strategy_root.mkdir(parents=True, exist_ok=True)
    canonical_start_index = (
        int(strategy.start_index_override)
        if strategy.start_index_override is not None
        else int(default_start_index)
    )
    canonical_end_index_exclusive = canonical_start_index + args.segment_count
    shifted_start_index = canonical_start_index + 1 if args.shifted_target_check and args.segment_count > 1 else None
    runs: list[RunResult] = []
    labels = ["run_a", "run_b", *[f"run_{chr(ord('c') + index)}" for index in range(max(0, args.repeat_count - 2))]]
    for label in labels[: args.repeat_count]:
        runs.append(
            _run_generation(
                label=label,
                strategy_root=strategy_root,
                source=source,
                args=args,
                start_index=canonical_start_index,
                segment_count=args.segment_count,
                strategy=strategy,
            )
        )

    shifted: dict[str, Any] | None = None
    if shifted_start_index is not None:
        shifted_run = _run_generation(
            label="run_shifted_target",
            strategy_root=strategy_root,
            source=source,
            args=args,
            start_index=shifted_start_index,
            segment_count=args.segment_count,
            strategy=strategy,
        )
        runs.append(shifted_run)
        shifted_overlap = sorted(
            set(range(canonical_start_index, canonical_end_index_exclusive))
            & set(range(shifted_start_index, shifted_start_index + args.segment_count))
        )
        shifted = {
            "enabled": True,
            "shifted_requested_target_seconds": round(
                shifted_start_index * args.segment_duration_seconds + min(args.segment_duration_seconds / 4.0, 0.5),
                6,
            ),
            "shifted_start_index": shifted_start_index,
            "shifted_end_index_exclusive": shifted_start_index + args.segment_count,
            "overlap_indexes": shifted_overlap,
        }
    else:
        shifted = {"enabled": False, "reason": "segment_count_too_small"}

    comparisons: dict[str, Any] = {
        "run_a_vs_run_b": _compare_runs(
            runs[0],
            runs[1],
            label="run_a_vs_run_b",
            expected_indexes=list(range(canonical_start_index, canonical_end_index_exclusive)),
        )
    }
    shifted_run = next((run for run in runs if run.label == "run_shifted_target"), None)
    if shifted_run is not None and shifted is not None:
        comparisons["run_a_vs_shifted"] = _compare_runs(
            runs[0],
            shifted_run,
            label="run_a_vs_shifted",
            expected_indexes=list(shifted["overlap_indexes"]),
        )
    else:
        comparisons["run_a_vs_shifted"] = {
            "label": "run_a_vs_shifted",
            "pass": False,
            "reason": "shifted_target_check_disabled",
        }

    _write_hash_table(strategy_root, runs)
    summary = _build_summary(
        args=args,
        artifact_root=strategy_root,
        source=source,
        strategy=strategy,
        canonical_start_index=canonical_start_index,
        canonical_end_index_exclusive=canonical_end_index_exclusive,
        runs=runs,
        comparisons=comparisons,
        shifted=shifted,
    )
    (strategy_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    (strategy_root / "summary.csv").write_text(
        "metric,value\n"
        f"pass,{str(summary['pass']).lower()}\n"
        f"canonical_start_index,{canonical_start_index}\n"
        f"canonical_end_index_exclusive,{canonical_end_index_exclusive}\n"
        f"run_a_vs_run_b,{str(comparisons['run_a_vs_run_b']['pass']).lower()}\n"
        f"run_a_vs_shifted,{str(comparisons['run_a_vs_shifted']['pass']).lower()}\n",
        encoding="utf-8",
    )
    return summary


def _write_strategy_matrix_csv(artifact_root: Path, strategy_summaries: list[dict[str, object]]) -> None:
    with (artifact_root / "strategy_matrix.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "strategy",
                "threads",
                "pass",
                "run_a_vs_run_b",
                "run_a_vs_shifted",
                "run_a_timestamp",
                "run_shifted_timestamp",
                "run_a_keyframe",
                "blockers",
            ]
        )
        for summary in strategy_summaries:
            strategy = summary["strategy"]
            timestamp_validation = summary.get("timestamp_validation") or {}
            keyframe_validation = summary.get("keyframe_validation") or {}
            writer.writerow(
                [
                    strategy["name"],
                    summary["threads"],
                    summary["pass"],
                    (summary.get("run_a_vs_run_b") or {}).get("pass"),
                    (summary.get("run_a_vs_shifted") or {}).get("pass"),
                    ((timestamp_validation.get("run_a") or {}).get("status")),
                    ((timestamp_validation.get("run_shifted_target") or {}).get("status")),
                    ((keyframe_validation.get("run_a") or {}).get("status")),
                    ";".join(summary.get("blockers") or []),
                ]
            )


def main() -> int:
    args = _parse_args()
    requested_start_seconds = (
        float(args.start_seconds)
        if args.start_seconds is not None
        else float(args.target_seconds)
        if args.target_seconds is not None
        else DEFAULT_START_SECONDS
    )
    canonical_start_index = max(0, int(math.floor(max(0.0, requested_start_seconds) / args.segment_duration_seconds)))
    strategies = _strategy_specs(args, canonical_start_index)
    if not strategies:
        raise SystemExit("No strategies selected")
    max_required_end_index = 0
    for strategy in strategies:
        start_index = strategy.start_index_override if strategy.start_index_override is not None else canonical_start_index
        max_required_end_index = max(max_required_end_index, int(start_index) + args.segment_count)
        if args.shifted_target_check and args.segment_count > 1:
            max_required_end_index = max(max_required_end_index, int(start_index) + 1 + args.segment_count)
    source = _select_source(
        args,
        required_end_seconds=max_required_end_index * args.segment_duration_seconds,
    )

    artifact_root = args.artifact_base.expanduser().resolve() / f"{_utc_timestamp_for_path()}-strategy-matrix"
    artifact_root.mkdir(parents=True, exist_ok=False)

    strategy_summaries = [
        _run_strategy(
            strategy=strategy,
            artifact_root=artifact_root,
            source=source,
            args=args,
            default_start_index=canonical_start_index,
        )
        for strategy in strategies
    ]
    _write_strategy_matrix_csv(artifact_root, strategy_summaries)
    passed_strategy_names = [
        str(summary["strategy"]["name"])
        for summary in strategy_summaries
        if bool(summary.get("pass"))
    ]
    overall_summary = {
        "script_version": SCRIPT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_kind": "local",
        "source": {
            "basename": source.basename,
            "selected_from_db": source.selected_from_db,
            "duration_seconds": source.duration_seconds,
        },
        "profile": args.profile,
        "default_threads": args.threads,
        "segment_duration_seconds": args.segment_duration_seconds,
        "default_canonical_start_index": canonical_start_index,
        "strategy_names": [strategy.name for strategy in strategies],
        "strategy_summaries": strategy_summaries,
        "passed_strategy_names": passed_strategy_names,
        "any_strategy_passed": bool(passed_strategy_names),
        "overall_recommendation": (
            "At least one isolated command strategy passed the canonical proof checks."
            if passed_strategy_names
            else (
                "canonical_ffmpeg_command_proof_blocked; keep current shared-store bytes "
                "non-servable and evaluate single-writer contiguous canonical authority or "
                "another packaging approach."
            )
        ),
        "production_shared_store_written": False,
        "serving_enabled": False,
    }
    (artifact_root / "summary.json").write_text(
        json.dumps(overall_summary, ensure_ascii=True, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "artifact_root": str(artifact_root),
                "any_strategy_passed": bool(passed_strategy_names),
                "passed_strategy_names": passed_strategy_names,
                "blockers_by_strategy": {
                    str(summary["strategy"]["name"]): summary.get("blockers") or []
                    for summary in strategy_summaries
                },
            },
            indent=2,
        )
    )
    return 0 if passed_strategy_names else 2


if __name__ == "__main__":
    raise SystemExit(main())
