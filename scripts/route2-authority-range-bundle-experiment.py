#!/usr/bin/env python3
"""Authority-bound contiguous Route2 range validation bundle experiment.

This developer-only script combines a simulated single-writer authority record,
lease lifecycle, one generated contiguous HLS fMP4 range, validation metadata,
and a conflict scenario. It writes only under ``dev/artifacts`` and never
touches production shared outputs, live Route2 playback, serving, reuse, attach,
or cloud sources.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = PROJECT_ROOT / "scripts" / "route2-contiguous-range-playback-experiment.py"
DEFAULT_ARTIFACT_BASE = PROJECT_ROOT / "dev" / "artifacts" / "route2-authority-range-bundle"
DEFAULT_DB_PATH = PROJECT_ROOT / "backend" / "data" / "elvern.db"
SCRIPT_VERSION = "route2-authority-range-bundle-experiment-v1"
GENERATION_STRATEGY_VERSION = "single-writer-contiguous-range-v1"
SERVING_BLOCKED_REASONS = [
    "serving_disabled",
    "sparse_stitching_blocked",
    "production_writer_disabled",
    "permission_gate_not_implemented",
]


def _load_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("route2_contiguous_range_playback_experiment", HELPER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load helper script: {HELPER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _timestamp_for_path() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _stable_id(*parts: object, length: int = 16) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:length]


def _hash_placeholder(index: int, suffix: str) -> str:
    return hashlib.sha256(f"simulated-conflict:{index}:{suffix}".encode("utf-8")).hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a dev-only authority-bound contiguous range validation bundle.")
    parser.add_argument("--source-path", type=Path, default=None)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--start-seconds", type=float, default=30.0)
    parser.add_argument("--segment-count", type=int, default=6)
    parser.add_argument("--segment-duration-seconds", type=float, default=2.0)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--profile", choices=["mobile_1080p", "mobile_2160p"], default="mobile_1080p")
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
    if args.source_path is not None and str(args.source_path).startswith(("http://", "https://")):
        raise SystemExit("Cloud/URL sources are forbidden for this local-only experiment.")
    return args


def _source_fingerprint(source: Any) -> str:
    stat = source.path.stat()
    digest = hashlib.sha256(
        f"{source.basename}|{stat.st_size}|{int(stat.st_mtime)}".encode("utf-8")
    ).hexdigest()
    return f"source:redacted-local:{digest[:16]}"


def _output_contract_fingerprint(args: argparse.Namespace) -> str:
    payload = {
        "profile": args.profile,
        "playback_mode": "full",
        "segment_duration_seconds": args.segment_duration_seconds,
        "video_codec": "libx264",
        "audio_codec": "aac",
        "container": "hls_fmp4",
        "preset": "superfast",
        "threads": args.threads,
        "generation_strategy_version": GENERATION_STRATEGY_VERSION,
        "timestamp_policy": "range_relative_per_range",
    }
    return "contract:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:24]


def _make_authority_metadata(
    *,
    args: argparse.Namespace,
    source: Any,
    range_start_index: int,
    range_end_index_exclusive: int,
    artifact_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    created_at = _utcnow()
    shared_output_key = "r2ss:dev:authority-range-bundle:" + _stable_id(
        source.basename,
        args.profile,
        args.segment_duration_seconds,
        "full",
    )
    generation_id = "gen:" + _stable_id(shared_output_key, range_start_index, range_end_index_exclusive, artifact_root.name)
    contract_fingerprint = _output_contract_fingerprint(args)
    authority_id = "auth:" + _stable_id(
        shared_output_key,
        contract_fingerprint,
        GENERATION_STRATEGY_VERSION,
        range_start_index,
        range_end_index_exclusive,
        generation_id,
    )
    authority = {
        "authority_id": authority_id,
        "shared_output_key": shared_output_key,
        "generation_id": generation_id,
        "output_contract_fingerprint": contract_fingerprint,
        "source_fingerprint": _source_fingerprint(source),
        "profile": args.profile,
        "playback_mode": "full",
        "segment_duration_seconds": args.segment_duration_seconds,
        "generation_strategy_version": GENERATION_STRATEGY_VERSION,
        "preset": "superfast",
        "thread_policy": "fixed_threads",
        "threads": args.threads,
        "ffmpeg_version": "not_recorded_dev_artifact",
        "timestamp_policy": "range_relative_per_range",
        "range_start_index": range_start_index,
        "range_end_index_exclusive": range_end_index_exclusive,
        "state": "pending",
        "state_history": [
            {"state": "pending", "at": _iso(created_at)},
        ],
        "created_at": _iso(created_at),
        "updated_at": _iso(created_at),
        "validated_bytes": False,
        "media_bytes_present": False,
        "byte_integrity_validated": False,
        "segment_bytes_stable": False,
        "segment_bytes_stable_reason": "not_proven_cross_window",
        "timestamp_validation_status": "not_run",
        "keyframe_validation_status": "not_run",
        "contiguous_range_validated": False,
        "serving_allowed": False,
        "serving_blocked": True,
        "serving_blocked_reasons": list(SERVING_BLOCKED_REASONS),
        "sparse_stitching_status": "blocked",
        "attach_from_anywhere_status": "blocked",
        "reuse_status": "blocked",
        "production_write_status": "not_performed",
        "claim_scope": "per_range_standalone_only_if_validation_passes",
    }
    lease = {
        "authority_id": authority_id,
        "generation_id": generation_id,
        "writer_id": "dev-bundle-writer:" + _stable_id(authority_id, artifact_root.name, length=12),
        "status": "active",
        "heartbeat_at": _iso(created_at),
        "expires_at": _iso(created_at + timedelta(seconds=30)),
        "range_start_index": range_start_index,
        "range_end_index_exclusive": range_end_index_exclusive,
        "stale": False,
        "expired": False,
    }
    return authority, lease


def _transition(authority: dict[str, Any], state: str) -> None:
    now = _utcnow()
    authority["state"] = state
    authority["updated_at"] = _iso(now)
    authority.setdefault("state_history", []).append({"state": state, "at": _iso(now)})


def _heartbeat(lease: dict[str, Any]) -> None:
    now = _utcnow()
    lease["heartbeat_at"] = _iso(now)
    lease["expires_at"] = _iso(now + timedelta(seconds=30))
    lease["status"] = "active"
    lease["stale"] = False
    lease["expired"] = False


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _copy_hashes_to_range_manifest(output_dir: Path, validation: dict[str, Any], range_start_index: int, range_end_index: int) -> dict[str, Any]:
    expected_indexes = list(range(range_start_index, range_end_index))
    return {
        "manifest_filename": "range.m3u8",
        "range_start_index": range_start_index,
        "range_end_index_exclusive": range_end_index,
        "segment_filenames": [f"abs_{index:012d}.m4s" for index in expected_indexes],
        "init_filename": "init.mp4",
        "ext_x_map": 'URI="init.mp4"',
        "ext_x_media_sequence": range_start_index,
        "no_gaps": bool(validation.get("no_gaps")),
        "sparse_stitching": False,
        "claim_scope": "per_range_standalone_only",
        "serving_allowed": False,
    }


def _run_full_range_validation(
    *,
    helper: ModuleType,
    args: argparse.Namespace,
    source: Any,
    artifact_root: Path,
    read_seconds: float,
) -> dict[str, Any]:
    manifest_path = artifact_root / "range.m3u8"
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
        f"{read_seconds + 0.5:.6f}",
        "-f",
        "null",
        "-",
    ]
    ffprobe_code, ffprobe_wall = helper._run_command(
        ffprobe_command,
        source_path=source.path,
        stderr_path=artifact_root / "ffprobe_full_range.stderr.log",
        timeout_seconds=args.timeout_seconds,
    )
    ffmpeg_code, ffmpeg_wall = helper._run_command(
        ffmpeg_command,
        source_path=source.path,
        stderr_path=artifact_root / "ffmpeg_full_range_read.stderr.log",
        timeout_seconds=args.timeout_seconds,
    )
    duration = None
    try:
        completed = subprocess.run(ffprobe_command, check=False, capture_output=True, text=True, timeout=args.timeout_seconds)
        if completed.returncode == 0:
            payload = json.loads(completed.stdout or "{}")
            duration = float(payload.get("format", {}).get("duration"))
    except (OSError, subprocess.TimeoutExpired, TypeError, ValueError, json.JSONDecodeError):
        duration = None
    return {
        "ffprobe_return_code": ffprobe_code,
        "ffprobe_wall_seconds": round(ffprobe_wall, 3),
        "ffprobe_duration_seconds": duration,
        "ffprobe_command": helper._redact_command(ffprobe_command, source.path),
        "ffmpeg_full_range_return_code": ffmpeg_code,
        "ffmpeg_full_range_wall_seconds": round(ffmpeg_wall, 3),
        "ffmpeg_full_range_read_seconds": round(read_seconds + 0.5, 3),
        "ffmpeg_full_range_command": helper._redact_command(ffmpeg_command, source.path),
        "pass": ffprobe_code == 0 and ffmpeg_code == 0,
    }


def _conflict_scenario(authority: dict[str, Any], conflict_index: int) -> dict[str, Any]:
    now = _iso(_utcnow())
    existing_hash = _hash_placeholder(conflict_index, "existing")
    candidate_hash = _hash_placeholder(conflict_index, "candidate")
    return {
        "scenario": "simulated_segment_hash_conflict",
        "authority_id": authority["authority_id"],
        "range_start_index": authority["range_start_index"],
        "range_end_index_exclusive": authority["range_end_index_exclusive"],
        "state": "conflict",
        "conflict_indexes": [conflict_index],
        "conflict_count": 1,
        "first_conflict_at": now,
        "last_conflict_at": now,
        "segment_hash_conflicts": [
            {
                "index": conflict_index,
                "existing_sha256": existing_hash,
                "candidate_sha256": candidate_hash,
                "detected_at": now,
            }
        ],
        "mixed_writer_conflict": True,
        "segment_bytes_stable": False,
        "serving_allowed": False,
        "serving_blocked": True,
        "serving_blocked_reasons": sorted(set(SERVING_BLOCKED_REASONS + ["segment_hash_conflict"])),
        "overwrite_performed": False,
    }


def main() -> int:
    args = _parse_args()
    helper = _load_helper()
    range_start_index = max(0, int(math.floor(max(0.0, args.start_seconds) / args.segment_duration_seconds)))
    range_end_index = range_start_index + args.segment_count
    source = helper._select_source(args, required_end_seconds=range_end_index * args.segment_duration_seconds)
    artifact_root = args.artifact_base.expanduser().resolve() / _timestamp_for_path()
    artifact_root.mkdir(parents=True, exist_ok=False)

    authority, lease = _make_authority_metadata(
        args=args,
        source=source,
        range_start_index=range_start_index,
        range_end_index_exclusive=range_end_index,
        artifact_root=artifact_root,
    )
    _transition(authority, "generating")
    _heartbeat(lease)
    command = helper._build_ffmpeg_command(
        args=args,
        source_path=source.path,
        output_dir=artifact_root,
        start_index=range_start_index,
    )
    command_redacted = helper._redact_command(command, source.path)
    (artifact_root / "command.redacted.json").write_text(
        json.dumps(command_redacted, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    generation_code, generation_wall = helper._run_command(
        command,
        source_path=source.path,
        stderr_path=artifact_root / "ffmpeg_generate.stderr.log",
        timeout_seconds=args.timeout_seconds,
    )

    _transition(authority, "validating")
    validation: dict[str, Any]
    if generation_code == 0:
        validation = helper._validate_artifacts(
            args=args,
            source=source,
            output_dir=artifact_root,
            start_index=range_start_index,
        )
    else:
        validation = {
            "pass": False,
            "blockers": ["ffmpeg_generation_failed"],
            "claim_scope": "failed",
            "explicit_sparse_stitching_status": "blocked",
        }
    generated_duration = args.segment_count * args.segment_duration_seconds
    full_range_validation = (
        _run_full_range_validation(
            helper=helper,
            args=args,
            source=source,
            artifact_root=artifact_root,
            read_seconds=generated_duration,
        )
        if generation_code == 0 and (artifact_root / "range.m3u8").exists()
        else {"pass": False, "reason": "range_manifest_missing_or_generation_failed"}
    )
    validation["full_range_manifest_read_validation"] = full_range_validation
    if not full_range_validation.get("pass"):
        validation.setdefault("blockers", []).append("full_range_manifest_read_failed")

    pass_result = generation_code == 0 and bool(validation.get("pass")) and bool(full_range_validation.get("pass"))
    if pass_result:
        _transition(authority, "validated_bytes")
    else:
        _transition(authority, "failed")

    authority.update(
        {
            "validated_bytes": pass_result,
            "media_bytes_present": generation_code == 0,
            "byte_integrity_validated": pass_result,
            "segment_bytes_stable": False,
            "segment_bytes_stable_reason": "not_proven_cross_window",
            "timestamp_validation_status": validation.get("timestamp_classification", "unknown"),
            "keyframe_validation_status": "pass" if all(
                item.get("first_video_packet_keyframe") is True
                for item in (validation.get("keyframe_validation") or {}).values()
            ) else "failed_or_incomplete",
            "contiguous_range_validated": bool(validation.get("no_gaps")) and pass_result,
            "serving_allowed": False,
            "serving_blocked": True,
            "serving_blocked_reasons": sorted(set(SERVING_BLOCKED_REASONS)),
            "sparse_stitching_status": "blocked",
            "attach_from_anywhere_status": "blocked",
            "reuse_status": "blocked",
            "production_write_status": "not_performed",
            "claim_scope": "per_range_standalone_only" if pass_result else "failed",
            "ffmpeg_generation_return_code": generation_code,
            "ffmpeg_generation_wall_seconds": round(generation_wall, 3),
        }
    )

    range_manifest = _copy_hashes_to_range_manifest(
        artifact_root,
        validation,
        range_start_index,
        range_end_index,
    )
    conflict = _conflict_scenario(authority, range_start_index + 1)

    _write_json(artifact_root / "authority.json", authority)
    _write_json(artifact_root / "lease.json", lease)
    _write_json(artifact_root / "validation.json", validation)
    _write_json(artifact_root / "range_manifest.json", range_manifest)
    _write_json(artifact_root / "conflict_scenario.json", conflict)

    summary = {
        "script_version": SCRIPT_VERSION,
        "created_at": _iso(_utcnow()),
        "source_kind": "local",
        "source": {
            "basename": source.basename,
            "selected_from_db": source.selected_from_db,
            "duration_seconds": source.duration_seconds,
        },
        "artifact_root": str(artifact_root),
        "authority_id": authority["authority_id"],
        "generation_id": authority["generation_id"],
        "authority_state": authority["state"],
        "lease_status": lease["status"],
        "range_start_index": range_start_index,
        "range_end_index_exclusive": range_end_index,
        "range_start_seconds": range_start_index * args.segment_duration_seconds,
        "range_end_seconds": range_end_index * args.segment_duration_seconds,
        "profile": args.profile,
        "threads": args.threads,
        "preset": "superfast",
        "timestamp_policy": "range_relative_per_range",
        "validation_pass": bool(validation.get("pass")),
        "full_range_read_pass": bool(full_range_validation.get("pass")),
        "pass": pass_result,
        "claim_scope": "per_range_standalone_only" if pass_result else "failed",
        "sparse_stitching_status": "blocked",
        "explicit_sparse_stitching_status": "blocked",
        "serving_allowed": False,
        "serving_blocked": True,
        "serving_blocked_reasons": sorted(set(SERVING_BLOCKED_REASONS)),
        "attach_from_anywhere_status": "blocked",
        "reuse_status": "blocked",
        "production_write_status": "not_performed",
        "production_shared_store_written": False,
        "cloud_used": False,
        "ffmpeg_command_preview_redacted": command_redacted,
        "metadata_files": [
            "authority.json",
            "lease.json",
            "validation.json",
            "range_manifest.json",
            "conflict_scenario.json",
            "summary.json",
            "hashes.csv",
        ],
        "conflict_scenario": conflict,
    }
    _write_json(artifact_root / "summary.json", summary)

    print(
        json.dumps(
            {
                "artifact_root": str(artifact_root),
                "pass": pass_result,
                "authority_state": authority["state"],
                "claim_scope": summary["claim_scope"],
                "timestamp_classification": validation.get("timestamp_classification"),
                "serving_allowed": False,
            },
            indent=2,
        )
    )
    return 0 if pass_result else 2


if __name__ == "__main__":
    raise SystemExit(main())
