from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

from .config import Settings
from .db import get_connection, preserve_hidden_movie_keys_for_media_item, utcnow_iso


logger = logging.getLogger(__name__)
YEAR_PATTERN = re.compile(r"(19|20)\d{2}")


def infer_title_and_year(filename_stem: str) -> tuple[str, int | None]:
    normalized = re.sub(r"[._]+", " ", filename_stem)
    normalized = re.sub(r"\s+", " ", normalized).strip(" -_")
    year_match = None
    for match in YEAR_PATTERN.finditer(normalized):
        year_match = match
    year = int(year_match.group(0)) if year_match else None
    if year_match:
        title = (normalized[: year_match.start()] + normalized[year_match.end() :]).strip(
            " -_()[]"
        )
    else:
        title = normalized
    return title or filename_stem, year


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


def scan_media_library(settings: Settings, *, reason: str) -> dict[str, object]:
    media_root = settings.media_root.resolve()
    started_at = utcnow_iso()
    files_seen = 0
    files_changed = 0
    files_removed = 0

    with get_connection(settings) as connection:
        cursor = connection.execute(
            """
            INSERT INTO scan_jobs (started_at, status, reason, message)
            VALUES (?, 'running', ?, ?)
            """,
            (started_at, reason, "Scan started"),
        )
        job_id = cursor.lastrowid
        try:
            existing_rows = connection.execute(
                """
                SELECT id, file_path, file_size, file_mtime
                FROM media_items
                WHERE COALESCE(source_kind, 'local') = 'local'
                """
            ).fetchall()
            existing_by_path = {row["file_path"]: row for row in existing_rows}
            seen_paths: set[str] = set()

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
                file_path = str(resolved)
                seen_paths.add(file_path)
                files_seen += 1
                existing = existing_by_path.get(file_path)
                if (
                    existing
                    and existing["file_size"] == stat.st_size
                    and existing["file_mtime"] == stat.st_mtime
                ):
                    continue

                metadata = extract_media_metadata(resolved, settings)
                title, year = infer_title_and_year(resolved.stem)
                now = utcnow_iso()
                connection.execute(
                    """
                    INSERT INTO media_items (
                        title,
                        original_filename,
                        file_path,
                        source_kind,
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
                    ) VALUES (?, ?, ?, 'local', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(file_path) DO UPDATE SET
                        title = excluded.title,
                        original_filename = excluded.original_filename,
                        source_kind = 'local',
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
                        stat.st_size,
                        stat.st_mtime,
                        metadata["duration_seconds"],
                        metadata["width"],
                        metadata["height"],
                        metadata["video_codec"],
                        metadata["audio_codec"],
                        metadata["container"],
                        year,
                        now,
                        now,
                        now,
                    ),
                )
                media_item = connection.execute(
                    "SELECT id FROM media_items WHERE file_path = ?",
                    (file_path,),
                ).fetchone()
                if media_item:
                    connection.execute(
                        "DELETE FROM subtitle_tracks WHERE media_item_id = ?",
                        (media_item["id"],),
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
                                media_item["id"],
                                subtitle["language"],
                                subtitle["title"],
                                subtitle["codec"],
                                subtitle["disposition_default"],
                            ),
                        )
                files_changed += 1

            removable_rows = [
                row for row in existing_rows if row["file_path"] not in seen_paths
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
        "Media scan complete: seen=%s changed=%s removed=%s",
        files_seen,
        files_changed,
        files_removed,
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
        "message": "Scan completed",
    }
