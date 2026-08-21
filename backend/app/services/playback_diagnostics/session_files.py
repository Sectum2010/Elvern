from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .constants import SCHEMA_VERSION, SESSION_VISIBLE_FILES
from .crypto import DiagnosticsKeyStore
from .fileio import (
    atomic_write_json,
    ensure_private_directory,
    list_private_directory,
    open_private_descriptor,
    private_file_size,
    resolve_beneath,
)
from .journal import verify_journal
from .privacy import basename_sha256, safe_source_basename, source_fingerprint
from .schema import SessionMetadataV2


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path, *, trusted_root: Path) -> str:
    digest = hashlib.sha256()
    descriptor = open_private_descriptor(
        path,
        os.O_RDONLY,
        trusted_root=trusted_root,
    )
    with os.fdopen(descriptor, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_datetime(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@lru_cache(maxsize=1)
def resolve_elvern_commit(project_root: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    candidate = completed.stdout.strip()
    return candidate if len(candidate) == 40 else "unknown"


@lru_cache(maxsize=8)
def resolve_ffmpeg_version(ffmpeg_path: str | None) -> str:
    if not ffmpeg_path:
        return "unavailable"
    try:
        completed = subprocess.run(
            [ffmpeg_path, "-version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    first_line = completed.stdout.splitlines()[0] if completed.stdout else ""
    return first_line[:256] or "unknown"


def build_session_relative_path(playback_session_id: str, created_at: object) -> Path:
    moment = _safe_datetime(created_at)
    safe_id = str(playback_session_id)
    if not safe_id or not safe_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid playback diagnostics session id")
    return Path("sessions") / f"{moment:%Y}" / f"{moment:%m}" / f"{moment:%d}" / safe_id


def create_session_metadata(
    *,
    root: Path,
    project_root: Path,
    ffmpeg_path: str | None,
    owner_hash: str,
    subject_id: str,
    context: dict[str, Any],
    platform: str = "unknown",
    device_class: str = "unknown",
    browser_family: str = "unknown",
    browser_version: str = "",
    os_family: str = "unknown",
    os_version: str = "",
    hls_engine: str = "unknown",
    capabilities: dict[str, Any] | None = None,
    write: bool = True,
) -> dict[str, Any]:
    session_id = str(context["playback_session_id"])
    created_at = context.get("created_at_utc") or _utc_now()
    relative_path = build_session_relative_path(session_id, created_at)
    basename = safe_source_basename(context.get("source_original_filename") or "unknown-media")
    raw_fingerprint = str(context.get("source_fingerprint") or basename)
    stable_fingerprint = (
        raw_fingerprint.lower()
        if len(raw_fingerprint) == 64
        and all(character in "0123456789abcdefABCDEF" for character in raw_fingerprint)
        else source_fingerprint(raw_fingerprint)
    )
    metadata = {
        "schema_version": "playback-diagnostics-session-v2",
        "diagnostics_event_schema": SCHEMA_VERSION,
        "playback_session_id": session_id,
        "owner_hash": owner_hash,
        "subject_id": subject_id,
        "media_item_id": int(context.get("media_item_id") or 0),
        "source_original_filename": basename,
        "source_filename_sha256": basename_sha256(basename),
        "source_fingerprint": stable_fingerprint,
        "source_kind": str(context.get("source_kind") or "unknown"),
        "source_size_bytes": context.get("source_size_bytes"),
        "duration_ms": context.get("duration_ms"),
        "container": context.get("container"),
        "video_codec": context.get("video_codec"),
        "audio_codec": context.get("audio_codec"),
        "width": context.get("width"),
        "height": context.get("height"),
        "pixel_format": context.get("pixel_format"),
        "bit_depth": context.get("bit_depth"),
        "hdr": context.get("hdr"),
        "dolby_vision": context.get("dolby_vision"),
        "audio_channels": context.get("audio_channels"),
        "selected_audio_stream_index": context.get("selected_audio_stream_index"),
        "nominal_bitrate": context.get("nominal_bitrate"),
        "frame_rate": context.get("frame_rate"),
        "video_profile": context.get("video_profile"),
        "video_level": context.get("video_level"),
        "color_primaries": context.get("color_primaries"),
        "color_transfer": context.get("color_transfer"),
        "color_space": context.get("color_space"),
        "audio_sample_rate": context.get("audio_sample_rate"),
        "audio_track_count": context.get("audio_track_count"),
        "subtitle_count": context.get("subtitle_count"),
        "container_profile": context.get("container_profile"),
        "profile": str(context.get("profile") or "unknown"),
        "playback_mode": str(context.get("playback_mode") or "unknown"),
        "stream_mode": str(context.get("stream_mode") or "unknown"),
        "platform": platform,
        "device_class": device_class,
        "browser_family": browser_family,
        "browser_version": browser_version,
        "os_family": os_family,
        "os_version": os_version,
        "hls_engine": hls_engine,
        "capabilities": capabilities or {},
        "elvern_commit": resolve_elvern_commit(str(project_root)),
        "ffmpeg_version": resolve_ffmpeg_version(ffmpeg_path),
        "config_fingerprint": hashlib.sha256(
            json.dumps(
                {
                    "profile": context.get("profile"),
                    "playback_mode": context.get("playback_mode"),
                    "stream_mode": context.get("stream_mode"),
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
        "state": "registering",
        "created_at_utc": str(created_at),
        "updated_at_utc": _utc_now(),
        "session_relative_path": str(relative_path),
    }
    validated = SessionMetadataV2.model_validate(metadata).model_dump(mode="json")
    if write:
        session_path = ensure_private_directory(
            resolve_beneath(root, relative_path),
            trusted_root=root,
        )
        ensure_private_directory(
            resolve_beneath(session_path, "raw"),
            trusted_root=root,
        )
        atomic_write_json(
            resolve_beneath(session_path, "session.json"),
            validated,
            trusted_root=root,
        )
    return validated


def read_session_events(
    root: Path,
    session_relative_path: str,
    key_store: DiagnosticsKeyStore,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    session_path = resolve_beneath(root, session_relative_path)
    raw_path = resolve_beneath(session_path, "raw")
    events: list[dict[str, Any]] = []
    journal_reports: list[dict[str, Any]] = []
    try:
        journal_names = list_private_directory(raw_path, trusted_root=root)
    except FileNotFoundError:
        return events, journal_reports
    for name in journal_names:
        if not name.endswith(".elvd") or Path(name).name != name:
            continue
        journal_path = resolve_beneath(raw_path, name)
        verification, journal_events = verify_journal(
            journal_path,
            key_store,
            include_events=True,
            trusted_root=root,
        )
        journal_reports.append(
            {
                "path": str(journal_path.relative_to(root)),
                "valid": verification.valid,
                "chunk_count": verification.chunk_count,
                "event_count": verification.event_count,
                "last_chunk_hash": verification.last_chunk_hash,
                "error": verification.error,
            }
        )
        if verification.valid:
            events.extend(journal_events)
    events.sort(
        key=lambda event: (
            int(str(event.get("aligned_wall_time_ns") or "0")),
            str(event.get("event_source") or ""),
            int(event.get("source_sequence") or 0),
        )
    )
    return events, journal_reports


def build_manifest(
    root: Path,
    session_relative_path: str,
    *,
    journal_reports: Iterable[dict[str, Any]],
    content_overrides: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    session_path = resolve_beneath(root, session_relative_path)
    overrides = dict(content_overrides or {})
    files: list[dict[str, Any]] = []
    for name in SESSION_VISIBLE_FILES:
        if name == "manifest.json":
            continue
        if name in overrides:
            payload = overrides[name]
            files.append(
                {
                    "relative_path": name,
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
            continue
        path = resolve_beneath(session_path, name)
        try:
            size_bytes = private_file_size(path, trusted_root=root)
        except FileNotFoundError:
            continue
        files.append(
            {
                "relative_path": name,
                "size_bytes": size_bytes,
                "sha256": _sha256_file(path, trusted_root=root),
            }
        )
    manifest = {
        "schema_version": "playback-diagnostics-session-manifest-v2",
        "generated_at_utc": _utc_now(),
        "files": files,
        "journals": list(journal_reports),
    }
    return manifest


def write_manifest(
    root: Path,
    session_relative_path: str,
    *,
    journal_reports: Iterable[dict[str, Any]],
    content_overrides: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    manifest = build_manifest(
        root,
        session_relative_path,
        journal_reports=journal_reports,
        content_overrides=content_overrides,
    )
    session_path = resolve_beneath(root, session_relative_path)
    manifest_path = resolve_beneath(session_path, "manifest.json")
    atomic_write_json(manifest_path, manifest, trusted_root=root)
    return manifest
