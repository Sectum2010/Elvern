from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path

from .config import Settings
from .db import get_connection, preserve_hidden_movie_keys_for_media_item, utcnow_iso
from .db_hidden_movie_keys import prune_recreated_local_hidden_movie_keys
from .services.local_library_source_service import (
    ensure_current_shared_local_source_binding,
    get_effective_shared_local_library_path,
)
from .services.media_title_parser import parse_media_title


logger = logging.getLogger(__name__)
LOCAL_LIBRARY_FRESHNESS_SNAPSHOT_VERSION = 1


def _coerce_scan_year(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _preserve_known_year(*, inferred_year: int | None, existing_year: object) -> int | None:
    if inferred_year is not None:
        return inferred_year
    return _coerce_scan_year(existing_year)


def _local_file_signature(*, file_size: object, file_mtime: object, filename: object) -> tuple[int, float, str]:
    try:
        normalized_size = int(file_size or 0)
    except (TypeError, ValueError):
        normalized_size = 0
    try:
        normalized_mtime = round(float(file_mtime or 0.0), 6)
    except (TypeError, ValueError):
        normalized_mtime = 0.0
    suffix = Path(str(filename or "")).suffix.lower()
    return normalized_size, normalized_mtime, suffix


def infer_title_and_year(filename_stem: str) -> tuple[str, int | None]:
    parsed = parse_media_title(
        title=None,
        year=None,
        original_filename=filename_stem,
    )
    resolved_title = str(parsed["display_title"] or "").strip() or filename_stem
    return resolved_title, parsed["parsed_year"]


def _inside_media_root(candidate: Path, media_root: Path) -> bool:
    try:
        candidate.relative_to(media_root)
        return True
    except ValueError:
        return False


def extract_media_metadata(file_path: Path, settings: Settings) -> dict[str, object]:
    if not settings.ffprobe_path:
        return {
            "duration_seconds": None,
            "width": None,
            "height": None,
            "video_codec": None,
            "audio_codec": None,
            "container": file_path.suffix.lower().lstrip(".") or None,
            "subtitles": [],
        }
    command = [
        settings.ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        (
            "format=duration,format_name:"
            "stream=index,codec_type,codec_name,width,height,"
            "disposition:stream_tags=language,title"
        ),
        "-of",
        "json",
        str(file_path),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ffprobe failed for %s: %s", file_path, exc)
        return {
            "duration_seconds": None,
            "width": None,
            "height": None,
            "video_codec": None,
            "audio_codec": None,
            "container": file_path.suffix.lower().lstrip(".") or None,
            "subtitles": [],
        }
    if completed.returncode != 0:
        logger.warning("ffprobe exited with %s for %s", completed.returncode, file_path)
        return {
            "duration_seconds": None,
            "width": None,
            "height": None,
            "video_codec": None,
            "audio_codec": None,
            "container": file_path.suffix.lower().lstrip(".") or None,
            "subtitles": [],
        }
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        logger.warning("ffprobe returned invalid JSON for %s", file_path)
        return {
            "duration_seconds": None,
            "width": None,
            "height": None,
            "video_codec": None,
            "audio_codec": None,
            "container": file_path.suffix.lower().lstrip(".") or None,
            "subtitles": [],
        }
    streams = payload.get("streams", [])
    format_info = payload.get("format", {})
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        {},
    )
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        {},
    )
    subtitle_streams = [
        stream for stream in streams if stream.get("codec_type") == "subtitle"
    ]
    subtitles = []
    for stream in subtitle_streams:
        tags = stream.get("tags", {})
        disposition = stream.get("disposition", {}) or {}
        subtitles.append(
            {
                "language": tags.get("language"),
                "title": tags.get("title"),
                "codec": stream.get("codec_name"),
                "disposition_default": int(disposition.get("default", 0)),
            }
        )
    duration_raw = format_info.get("duration")
    duration_seconds = round(float(duration_raw), 2) if duration_raw else None
    format_name = format_info.get("format_name")
    suffix = file_path.suffix.lower()
    if suffix in {".mp4", ".m4v"}:
        container = "mp4"
    elif suffix == ".mov":
        container = "mov"
    elif suffix == ".mkv":
        container = "mkv"
    elif suffix == ".webm":
        container = "webm"
    elif suffix == ".avi":
        container = "avi"
    else:
        container = format_name.split(",")[0] if format_name else None
    return {
        "duration_seconds": duration_seconds,
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "video_codec": video_stream.get("codec_name"),
        "audio_codec": audio_stream.get("codec_name"),
        "container": container,
        "subtitles": subtitles,
    }


def build_local_library_freshness_snapshot(settings: Settings) -> dict[str, object]:
    media_root = get_effective_shared_local_library_path(settings).resolve()
    snapshot: dict[str, object] = {
        "version": LOCAL_LIBRARY_FRESHNESS_SNAPSHOT_VERSION,
        "media_root": str(media_root),
        "snapshot_state": "unknown",
        "root_identity": None,
        "top_level_count": 0,
        "top_level_fingerprint": None,
    }

    if not media_root.exists():
        snapshot["snapshot_state"] = "missing"
        return snapshot

    try:
        root_stat = media_root.stat()
        top_level_entries: list[dict[str, object]] = []
        for entry in sorted(media_root.iterdir(), key=lambda candidate: candidate.name.lower()):
            try:
                entry_stat = entry.stat()
            except OSError:
                snapshot["snapshot_state"] = "error"
                return snapshot
            if entry.is_dir():
                entry_kind = "dir"
                entry_size = 0
            elif entry.is_file():
                entry_kind = "file"
                entry_size = int(entry_stat.st_size)
            else:
                entry_kind = "other"
                entry_size = 0
            top_level_entries.append(
                {
                    "name": entry.name,
                    "kind": entry_kind,
                    "mtime_ns": int(entry_stat.st_mtime_ns),
                    "size": entry_size,
                }
            )
    except OSError:
        snapshot["snapshot_state"] = "error"
        return snapshot

    encoded_entries = json.dumps(
        top_level_entries,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    snapshot["snapshot_state"] = "ready"
    snapshot["root_identity"] = {
        "st_dev": int(root_stat.st_dev),
        "st_ino": int(root_stat.st_ino),
    }
    snapshot["top_level_count"] = len(top_level_entries)
    snapshot["top_level_fingerprint"] = hashlib.sha256(encoded_entries).hexdigest()
    return snapshot


def scan_media_library(settings: Settings, *, reason: str) -> dict[str, object]:
    media_root = get_effective_shared_local_library_path(settings).resolve()
    started_at = utcnow_iso()
    files_seen = 0
    files_changed = 0
    files_removed = 0
    hidden_movie_keys_pruned = 0

    with get_connection(settings) as connection:
        shared_local_source_id = ensure_current_shared_local_source_binding(
            settings,
            connection=connection,
        )
        cursor = connection.execute(
            """
            INSERT INTO scan_jobs (started_at, status, reason, message)
            VALUES (?, 'running', ?, ?)
            """,
            (started_at, reason, "Scan started"),
        )
        job_id = cursor.lastrowid
        try:
            current_files: list[tuple[Path, object]] = []
            current_paths: set[str] = set()
            for candidate in media_root.rglob("*"):
                if not candidate.is_file():
                    continue
                if candidate.suffix.lower() not in settings.allowed_video_extensions:
                    continue
                resolved = candidate.resolve()
                if not _inside_media_root(resolved, media_root):
                    logger.warning("Skipping out-of-root media path %s", resolved)
                    continue
                stat = resolved.stat()
                current_files.append((resolved, stat))
                current_paths.add(str(resolved))

            existing_rows = connection.execute(
                """
                SELECT id, file_path, original_filename, file_size, file_mtime, year
                FROM media_items
                WHERE COALESCE(source_kind, 'local') = 'local'
                """
            ).fetchall()
            existing_by_path = {row["file_path"]: row for row in existing_rows}
            missing_existing_by_signature: dict[tuple[int, float, str], list] = {}
            for row in existing_rows:
                if row["file_path"] in current_paths:
                    continue
                signature = _local_file_signature(
                    file_size=row["file_size"],
                    file_mtime=row["file_mtime"],
                    filename=row["original_filename"],
                )
                missing_existing_by_signature.setdefault(signature, []).append(row)
            seen_paths: set[str] = set()
            rename_matched_existing_ids: set[int] = set()

            for resolved, stat in current_files:
                file_path = str(resolved)
                seen_paths.add(file_path)
                files_seen += 1
                existing = existing_by_path.get(file_path)
                rename_target = None
                if existing is None:
                    signature = _local_file_signature(
                        file_size=stat.st_size,
                        file_mtime=stat.st_mtime,
                        filename=resolved.name,
                    )
                    candidates = [
                        row
                        for row in missing_existing_by_signature.get(signature, [])
                        if int(row["id"]) not in rename_matched_existing_ids
                    ]
                    if len(candidates) == 1:
                        rename_target = candidates[0]
                        rename_matched_existing_ids.add(int(rename_target["id"]))
                if (
                    existing
                    and existing["file_size"] == stat.st_size
                    and existing["file_mtime"] == stat.st_mtime
                ):
                    continue

                metadata = extract_media_metadata(resolved, settings)
                # Preserve the source-provided title stem in storage. Clean display titles
                # stay derived at read time so parser changes do not destructively rewrite
                # the raw library title truth.
                title = resolved.stem
                _, inferred_year = infer_title_and_year(resolved.stem)
                existing_year = None
                if existing is not None:
                    existing_year = existing["year"]
                elif rename_target is not None:
                    existing_year = rename_target["year"]
                preserved_year = _preserve_known_year(
                    inferred_year=inferred_year,
                    existing_year=existing_year,
                )
                now = utcnow_iso()
                media_item_id: int | None = None
                if rename_target is not None:
                    # Keep the same media row when a local rename is strongly detectable.
                    # This preserves progress/history/poster/year continuity instead of
                    # turning a rename into delete+insert.
                    media_item_id = int(rename_target["id"])
                    connection.execute(
                        """
                        UPDATE media_items
                        SET title = ?,
                            original_filename = ?,
                            file_path = ?,
                            source_kind = 'local',
                            library_source_id = ?,
                            file_size = ?,
                            file_mtime = ?,
                            duration_seconds = ?,
                            width = ?,
                            height = ?,
                            video_codec = ?,
                            audio_codec = ?,
                            container = ?,
                            year = ?,
                            updated_at = ?,
                            last_scanned_at = ?
                        WHERE id = ?
                        """,
                        (
                            title,
                            resolved.name,
                            file_path,
                            shared_local_source_id,
                            stat.st_size,
                            stat.st_mtime,
                            metadata["duration_seconds"],
                            metadata["width"],
                            metadata["height"],
                            metadata["video_codec"],
                            metadata["audio_codec"],
                            metadata["container"],
                            preserved_year,
                            now,
                            now,
                            media_item_id,
                        ),
                    )
                else:
                    connection.execute(
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
                        ON CONFLICT(file_path) DO UPDATE SET
                            title = excluded.title,
                            original_filename = excluded.original_filename,
                            source_kind = 'local',
                            library_source_id = excluded.library_source_id,
                            file_size = excluded.file_size,
                            file_mtime = excluded.file_mtime,
                            duration_seconds = excluded.duration_seconds,
                            width = excluded.width,
                            height = excluded.height,
                            video_codec = excluded.video_codec,
                            audio_codec = excluded.audio_codec,
                            container = excluded.container,
                            year = excluded.year,
                            updated_at = excluded.updated_at,
                            last_scanned_at = excluded.last_scanned_at
                        """,
                        (
                            title,
                            resolved.name,
                            file_path,
                            shared_local_source_id,
                            stat.st_size,
                            stat.st_mtime,
                            metadata["duration_seconds"],
                            metadata["width"],
                            metadata["height"],
                            metadata["video_codec"],
                            metadata["audio_codec"],
                            metadata["container"],
                            preserved_year,
                            now,
                            now,
                            now,
                        ),
                    )
                    media_item = connection.execute(
                        "SELECT id FROM media_items WHERE file_path = ?",
                        (file_path,),
                    ).fetchone()
                    media_item_id = int(media_item["id"]) if media_item else None
                if media_item_id is not None:
                    connection.execute(
                        "DELETE FROM subtitle_tracks WHERE media_item_id = ?",
                        (media_item_id,),
                    )
                    for subtitle in metadata["subtitles"]:
                        connection.execute(
                            """
                            INSERT INTO subtitle_tracks (
                                media_item_id,
                                language,
                                title,
                                codec,
                                disposition_default
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                media_item_id,
                                subtitle["language"],
                                subtitle["title"],
                                subtitle["codec"],
                                subtitle["disposition_default"],
                            ),
                        )
                files_changed += 1

            removable_rows = [
                row
                for row in existing_rows
                if row["file_path"] not in seen_paths and int(row["id"]) not in rename_matched_existing_ids
            ]
            for row in removable_rows:
                preserve_hidden_movie_keys_for_media_item(
                    connection,
                    media_item_id=int(row["id"]),
                )
                connection.execute(
                    "DELETE FROM media_items WHERE id = ?",
                    (row["id"],),
                )
                files_removed += 1

            prune_summary = prune_recreated_local_hidden_movie_keys(
                connection,
                shared_local_source_id=shared_local_source_id,
            )
            hidden_movie_keys_pruned = int(
                prune_summary.get("global_hidden_movie_keys_pruned", 0)
            ) + int(
                prune_summary.get("user_hidden_movie_keys_pruned", 0)
            )

            finished_at = utcnow_iso()
            connection.execute(
                """
                UPDATE scan_jobs
                SET finished_at = ?, status = 'completed', files_seen = ?, files_changed = ?, files_removed = ?, message = ?
                WHERE id = ?
                """,
                (
                    finished_at,
                    files_seen,
                    files_changed,
                    files_removed,
                    "Scan completed",
                    job_id,
                ),
            )
            connection.commit()
        except Exception as exc:
            finished_at = utcnow_iso()
            connection.execute(
                """
                UPDATE scan_jobs
                SET finished_at = ?, status = 'failed', files_seen = ?, files_changed = ?, files_removed = ?, message = ?
                WHERE id = ?
                """,
                (
                    finished_at,
                    files_seen,
                    files_changed,
                    files_removed,
                    f"Scan failed: {exc}",
                    job_id,
                ),
            )
            connection.commit()
            raise

    logger.info(
        "Media scan complete: seen=%s changed=%s removed=%s hidden_movie_keys_pruned=%s",
        files_seen,
        files_changed,
        files_removed,
        hidden_movie_keys_pruned,
    )
    return {
        "job_id": job_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "reason": reason,
        "running": False,
        "files_seen": files_seen,
        "files_changed": files_changed,
        "files_removed": files_removed,
        "hidden_movie_keys_pruned": hidden_movie_keys_pruned,
        "message": "Scan completed",
    }
