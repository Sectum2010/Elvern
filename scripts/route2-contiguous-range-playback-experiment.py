#!/usr/bin/env python3
"""Isolated contiguous Route2-style per-range playback experiment.

This developer-only script writes one bounded HLS fMP4 range under
``dev/artifacts`` and validates that the range is internally readable as a
standalone artifact. It does not touch live Route2 playback, production shared
outputs, serving, reuse, attach, or cloud sources.
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


SCRIPT_VERSION = "route2-contiguous-range-playback-experiment-v1"
DEFAULT_ARTIFACT_BASE = PROJECT_ROOT / "dev" / "artifacts" / "route2-contiguous-range-playback"
DEFAULT_DB_PATH = PROJECT_ROOT / "backend" / "data" / "elvern.db"
DEFAULT_START_SECONDS = 30.0
DEFAULT_SEGMENT_COUNT = 6
DEFAULT_THREADS = 4
TIMESTAMP_TOLERANCE_SECONDS = 0.25
DURATION_TOLERANCE_SECONDS = 0.35


@dataclass(frozen=True, slots=True)
class SourceSelection:
    path: Path
    basename: str
    duration_seconds: float | None
    selected_from_db: bool


def _utc_timestamp_for_path() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size_bytes += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size_bytes


def _redact_path(value: str, source_path: Path) -> str:
    return str(value).replace(str(source_path), f"<source:{source_path.name}>")


def _redact_command(command: list[str], source_path: Path) -> list[str]:
    return [_redact_path(part, source_path) for part in command]


def _run_command(
    command: list[str],
    *,
    source_path: Path,
    stderr_path: Path,
    timeout_seconds: float,
) -> tuple[int, float]:
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
        return_code = completed.returncode
        stderr_payload = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        return_code = 124
        stderr_payload = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        stderr_payload += f"\nTimed out after {timeout_seconds:.1f}s\n"
    wall_seconds = max(0.0, time.monotonic() - started_at)
    stderr_payload = _redact_path(stderr_payload, source_path)
    stderr_payload += f"\n[wall_seconds] {wall_seconds:.3f}\n"
    stderr_path.write_text(stderr_payload, encoding="utf-8", errors="replace")
    return return_code, wall_seconds


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
        if str(args.source_path).startswith(("http://", "https://")):
            raise SystemExit("Cloud/URL sources are forbidden for this local-only experiment.")
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
    raise SystemExit("No existing local DB media item was long enough for the requested range.")


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
    offset = start + (20 if version == 1 else 12)
    return _read_u32(data, offset) if offset + 4 <= end else None


def _parse_mdhd_timescale(data: bytes, start: int, end: int) -> int | None:
    if start + 24 > end:
        return None
    version = data[start]
    offset = start + (20 if version == 1 else 12)
    value = _read_u32(data, offset) if offset + 4 <= end else 0
    return value or None


def _parse_hdlr_type(data: bytes, start: int, end: int) -> str | None:
    offset = start + 8
    return data[offset : offset + 4].decode("ascii", errors="replace") if offset + 4 <= end else None


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


def _tfdt_seconds_by_handler(tfdt_payload: dict[str, Any]) -> dict[str, float]:
    values: dict[str, float] = {}
    for traf in tfdt_payload.get("trafs") or []:
        handler = str(traf.get("handler_type") or f"track_{traf.get('track_id')}")
        seconds = traf.get("base_media_decode_time_seconds")
        if seconds is not None:
            values[handler] = float(seconds)
    return values


def _parse_manifest_extinf(manifest_path: Path) -> list[tuple[float, str]]:
    try:
        lines = manifest_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    entries: list[tuple[float, str]] = []
    pending_duration: float | None = None
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("#EXTINF:"):
            try:
                pending_duration = float(line.removeprefix("#EXTINF:").split(",", 1)[0])
            except ValueError:
                pending_duration = None
            continue
        if pending_duration is not None and line and not line.startswith("#"):
            entries.append((pending_duration, Path(line).name))
            pending_duration = None
    return entries


def _build_ffmpeg_command(
    *,
    args: argparse.Namespace,
    source_path: Path,
    output_dir: Path,
    start_index: int,
) -> list[str]:
    profile = MOBILE_PROFILES[args.profile]
    start_seconds = start_index * args.segment_duration_seconds
    output_seconds = args.segment_count * args.segment_duration_seconds
    keyframe_interval = max(1, int(args.segment_duration_seconds * 24))
    scale_filter = (
        f"scale=w='min({profile.max_width},iw)':h='min({profile.max_height},ih)':"
        "force_original_aspect_ratio=decrease"
    )
    return [
        args.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-y",
        "-threads",
        str(max(1, int(args.threads))),
        "-ss",
        f"{start_seconds:.6f}",
        "-i",
        str(source_path),
        "-t",
        f"{output_seconds:.6f}",
        "-output_ts_offset",
        f"{start_seconds:.6f}",
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
        f"expr:gte(t,n_forced*{args.segment_duration_seconds})",
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
        f"{args.segment_duration_seconds:.6f}",
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
        str(output_dir / "abs_%012d.m4s"),
        str(output_dir / "ffmpeg_index.m3u8"),
    ]


def _write_isolated_manifest(
    *,
    output_dir: Path,
    start_index: int,
    entries: list[tuple[float, str]],
    target_duration: int,
) -> Path:
    manifest_path = output_dir / "range.m3u8"
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:7",
        f"#EXT-X-TARGETDURATION:{target_duration}",
        f"#EXT-X-MEDIA-SEQUENCE:{start_index}",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        '#EXT-X-MAP:URI="init.mp4"',
        "#EXT-X-INDEPENDENT-SEGMENTS",
    ]
    for duration, segment_name in entries:
        lines.append(f"#EXTINF:{duration:.6f},")
        lines.append(segment_name)
    lines.append("#EXT-X-ENDLIST")
    lines.append("")
    manifest_path.write_text("\n".join(lines), encoding="utf-8")
    return manifest_path


def _write_single_segment_probe_manifest(output_dir: Path, segment_name: str, duration: float) -> Path:
    probe_dir = output_dir / "probe_manifests"
    probe_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = probe_dir / f"{Path(segment_name).stem}.m3u8"
    manifest_path.write_text(
        "\n".join(
            [
                "#EXTM3U",
                "#EXT-X-VERSION:7",
                f"#EXT-X-TARGETDURATION:{max(1, math.ceil(duration))}",
                '#EXT-X-MAP:URI="../init.mp4"',
                f"#EXTINF:{duration:.6f},",
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
    return "K" in str(packets[0].get("flags") or ""), None


def _run_manifest_read_validation(
    *,
    args: argparse.Namespace,
    source_path: Path,
    manifest_path: Path,
    output_dir: Path,
    read_seconds: float,
) -> dict[str, Any]:
    ffprobe_command = [
        args.ffprobe,
        "-v",
        "error",
        "-allowed_extensions",
        "ALL",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(manifest_path),
    ]
    ffmpeg_command = [
        args.ffmpeg,
        "-v",
        "error",
        "-allowed_extensions",
        "ALL",
        "-i",
        str(manifest_path),
        "-t",
        f"{read_seconds:.6f}",
        "-f",
        "null",
        "-",
    ]
    ffprobe_stderr = output_dir / "ffprobe_manifest.stderr.log"
    ffmpeg_stderr = output_dir / "ffmpeg_manifest_read.stderr.log"
    ffprobe_code, ffprobe_wall = _run_command(
        ffprobe_command,
        source_path=source_path,
        stderr_path=ffprobe_stderr,
        timeout_seconds=args.timeout_seconds,
    )
    ffmpeg_code, ffmpeg_wall = _run_command(
        ffmpeg_command,
        source_path=source_path,
        stderr_path=ffmpeg_stderr,
        timeout_seconds=args.timeout_seconds,
    )
    return {
        "ffprobe_return_code": ffprobe_code,
        "ffprobe_wall_seconds": round(ffprobe_wall, 3),
        "ffprobe_command": _redact_command(ffprobe_command, source_path),
        "ffmpeg_read_return_code": ffmpeg_code,
        "ffmpeg_read_wall_seconds": round(ffmpeg_wall, 3),
        "ffmpeg_read_command": _redact_command(ffmpeg_command, source_path),
        "pass": ffprobe_code == 0 and ffmpeg_code == 0,
    }


def _validate_artifacts(
    *,
    args: argparse.Namespace,
    source: SourceSelection,
    output_dir: Path,
    start_index: int,
) -> dict[str, Any]:
    expected_indexes = list(range(start_index, start_index + args.segment_count))
    expected_names = [f"abs_{index:012d}.m4s" for index in expected_indexes]
    generated_entries = _parse_manifest_extinf(output_dir / "ffmpeg_index.m3u8")
    generated_names = [name for _, name in generated_entries]
    target_duration = max(1, math.ceil(max([duration for duration, _ in generated_entries] or [args.segment_duration_seconds])))
    isolated_manifest_path = _write_isolated_manifest(
        output_dir=output_dir,
        start_index=start_index,
        entries=generated_entries,
        target_duration=target_duration,
    )

    init_path = output_dir / "init.mp4"
    init_hash, init_size = _hash_file(init_path) if init_path.exists() else (None, None)
    tracks = _parse_init_tracks(init_path) if init_path.exists() else {}
    segment_hashes: dict[int, str] = {}
    segment_sizes: dict[int, int] = {}
    segment_tfdt: dict[int, dict[str, Any]] = {}
    keyframe_results: dict[int, dict[str, Any]] = {}
    duration_results: dict[int, dict[str, Any]] = {}
    blockers: list[str] = []

    for offset, index in enumerate(expected_indexes):
        segment_name = f"abs_{index:012d}.m4s"
        segment_path = output_dir / segment_name
        if not segment_path.exists():
            blockers.append(f"missing_segment_{index}")
            continue
        digest, size_bytes = _hash_file(segment_path)
        segment_hashes[index] = digest
        segment_sizes[index] = size_bytes
        segment_tfdt[index] = _parse_segment_tfdt(segment_path, tracks)
        duration = generated_entries[offset][0] if offset < len(generated_entries) else None
        duration_results[index] = {
            "duration_seconds": duration,
            "within_tolerance": (
                duration is not None
                and abs(float(duration) - float(args.segment_duration_seconds)) <= DURATION_TOLERANCE_SECONDS
            ),
        }
        probe_manifest = _write_single_segment_probe_manifest(
            output_dir,
            segment_name,
            float(duration or args.segment_duration_seconds),
        )
        is_keyframe, reason = _first_video_packet_is_keyframe(args.ffprobe, probe_manifest, args.timeout_seconds)
        keyframe_results[index] = {
            "first_video_packet_keyframe": is_keyframe,
            "reason": reason,
        }

    if generated_names != expected_names:
        blockers.append("manifest_or_filename_gap")
    if len(generated_names) != args.segment_count:
        blockers.append("segment_count_mismatch")
    if not init_hash:
        blockers.append("missing_init")
    if any(not result.get("within_tolerance") for result in duration_results.values()):
        blockers.append("segment_duration_out_of_tolerance")
    if any(result.get("first_video_packet_keyframe") is not True for result in keyframe_results.values()):
        blockers.append("keyframe_validation_failed")

    timestamp_values_by_index = {
        index: _tfdt_seconds_by_handler(payload)
        for index, payload in sorted(segment_tfdt.items())
    }
    video_values = [
        values.get("vide")
        for _, values in sorted(timestamp_values_by_index.items())
        if values.get("vide") is not None
    ]
    audio_values = [
        values.get("soun")
        for _, values in sorted(timestamp_values_by_index.items())
        if values.get("soun") is not None
    ]
    video_monotonic = all(later > earlier for earlier, later in zip(video_values, video_values[1:]))
    audio_monotonic = all(later > earlier for earlier, later in zip(audio_values, audio_values[1:]))
    absolute_expected_first = start_index * args.segment_duration_seconds
    first_video = video_values[0] if video_values else None
    timestamp_classification = "unknown"
    if first_video is not None:
        if abs(first_video - absolute_expected_first) <= TIMESTAMP_TOLERANCE_SECONDS:
            timestamp_classification = "absolute_like"
        elif abs(first_video) <= TIMESTAMP_TOLERANCE_SECONDS:
            timestamp_classification = "range_relative"
        else:
            timestamp_classification = "offset_or_unexpected"
    if not video_monotonic:
        blockers.append("video_timestamps_not_monotonic")
    if audio_values and not audio_monotonic:
        blockers.append("audio_timestamps_not_monotonic")

    read_validation = _run_manifest_read_validation(
        args=args,
        source_path=source.path,
        manifest_path=isolated_manifest_path,
        output_dir=output_dir,
        read_seconds=max(1.0, min(args.segment_count * args.segment_duration_seconds, 12.0)),
    )
    if not read_validation["pass"]:
        blockers.append("manifest_read_validation_failed")

    hashes_csv = output_dir / "hashes.csv"
    with hashes_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["absolute_index", "filename", "sha256", "size_bytes"])
        for index in expected_indexes:
            writer.writerow([index, f"abs_{index:012d}.m4s", segment_hashes.get(index, ""), segment_sizes.get(index, "")])

    media_validation = {
        "expected_indexes": expected_indexes,
        "expected_names": expected_names,
        "generated_names": generated_names,
        "segment_count_valid": generated_names == expected_names,
        "no_gaps": generated_names == expected_names,
        "init_hash_sha256": init_hash,
        "init_size_bytes": init_size,
        "segment_hashes": {str(index): value for index, value in sorted(segment_hashes.items())},
        "segment_sizes": {str(index): value for index, value in sorted(segment_sizes.items())},
        "duration_validation": duration_results,
        "keyframe_validation": keyframe_results,
        "timestamp_tfdt": {str(index): values for index, values in timestamp_values_by_index.items()},
        "timestamp_classification": timestamp_classification,
        "video_tfdt_monotonic": video_monotonic,
        "audio_tfdt_monotonic": audio_monotonic,
        "isolated_manifest": isolated_manifest_path.name,
        "manifest_read_validation": read_validation,
        "blockers": sorted(set(blockers)),
        "pass": not blockers,
        "claim_scope": "per_range_standalone_only" if not blockers else "failed",
        "explicit_sparse_stitching_status": "blocked",
        "serving_allowed": False,
    }
    (output_dir / "media_validation.json").write_text(
        json.dumps(media_validation, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return media_validation


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and validate one isolated contiguous Route2-style range.")
    parser.add_argument("--source-path", type=Path, default=None)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--start-seconds", type=float, default=DEFAULT_START_SECONDS)
    parser.add_argument("--segment-count", type=int, default=DEFAULT_SEGMENT_COUNT)
    parser.add_argument("--segment-duration-seconds", type=float, default=SEGMENT_DURATION_SECONDS)
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--profile", choices=sorted(MOBILE_PROFILES), default="mobile_1080p")
    parser.add_argument("--artifact-base", type=Path, default=DEFAULT_ARTIFACT_BASE)
    parser.add_argument("--ffmpeg", default=os.environ.get("ELVERN_FFMPEG_PATH", "ffmpeg"))
    parser.add_argument("--ffprobe", default=os.environ.get("ELVERN_FFPROBE_PATH", "ffprobe"))
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    args = parser.parse_args()
    if args.segment_count < 1:
        raise SystemExit("--segment-count must be positive")
    if args.segment_duration_seconds <= 0:
        raise SystemExit("--segment-duration-seconds must be positive")
    if args.threads < 1:
        raise SystemExit("--threads must be positive")
    return args


def main() -> int:
    args = _parse_args()
    start_index = max(0, int(math.floor(max(0.0, args.start_seconds) / args.segment_duration_seconds)))
    end_index = start_index + args.segment_count
    source = _select_source(args, required_end_seconds=end_index * args.segment_duration_seconds)
    artifact_root = args.artifact_base.expanduser().resolve() / _utc_timestamp_for_path()
    artifact_root.mkdir(parents=True, exist_ok=False)

    command = _build_ffmpeg_command(
        args=args,
        source_path=source.path,
        output_dir=artifact_root,
        start_index=start_index,
    )
    command_redacted = _redact_command(command, source.path)
    (artifact_root / "command.redacted.json").write_text(
        json.dumps(command_redacted, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return_code, wall_seconds = _run_command(
        command,
        source_path=source.path,
        stderr_path=artifact_root / "ffmpeg_generate.stderr.log",
        timeout_seconds=args.timeout_seconds,
    )
    blockers: list[str] = []
    if return_code != 0:
        blockers.append("ffmpeg_generation_failed")
    media_validation = (
        _validate_artifacts(args=args, source=source, output_dir=artifact_root, start_index=start_index)
        if return_code == 0
        else {"pass": False, "blockers": blockers, "claim_scope": "failed", "explicit_sparse_stitching_status": "blocked"}
    )
    blockers.extend(media_validation.get("blockers") or [])
    pass_result = return_code == 0 and bool(media_validation.get("pass")) and not blockers
    summary = {
        "script_version": SCRIPT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_kind": "local",
        "source": {
            "basename": source.basename,
            "selected_from_db": source.selected_from_db,
            "duration_seconds": source.duration_seconds,
        },
        "profile": args.profile,
        "threads": args.threads,
        "preset": "superfast",
        "segment_duration_seconds": args.segment_duration_seconds,
        "range_start_index": start_index,
        "range_end_index_exclusive": end_index,
        "range_start_seconds": start_index * args.segment_duration_seconds,
        "range_end_seconds": end_index * args.segment_duration_seconds,
        "ffmpeg_generation_return_code": return_code,
        "ffmpeg_generation_wall_seconds": round(wall_seconds, 3),
        "ffmpeg_command_preview_redacted": command_redacted,
        "media_validation": media_validation,
        "blockers": sorted(set(blockers)),
        "pass": pass_result,
        "claim_scope": "per_range_standalone_only" if pass_result else "failed",
        "explicit_sparse_stitching_status": "blocked",
        "production_shared_store_written": False,
        "serving_allowed": False,
        "reuse_attach_follow_enabled": False,
    }
    (artifact_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "artifact_root": str(artifact_root),
                "pass": pass_result,
                "claim_scope": summary["claim_scope"],
                "timestamp_classification": media_validation.get("timestamp_classification"),
                "blockers": summary["blockers"],
            },
            indent=2,
        )
    )
    return 0 if pass_result else 2


if __name__ == "__main__":
    raise SystemExit(main())
