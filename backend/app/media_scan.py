from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path

from .config import Settings
from .db import get_connection, preserve_hidden_movie_keys_for_media_item, utcnow_iso
from .db_hidden_movie_keys import (
    prune_recreated_local_hidden_movie_keys,
    resolve_hidden_copy_identity,
)
from .services.local_library_source_service import (
    ensure_current_shared_local_source_binding,
    get_effective_library_reference_locations,
    get_effective_shared_local_library_path,
)
from .services.library_folder_classifier import (
    DiscoveredMediaFile,
    discover_library_folders,
    path_is_same_or_inside,
)
from .services.library_revision_mutation_service import bump_library_revision_layers
from .services.local_path_security import is_restricted_library_reference_path
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


def _inside_any_media_root(candidate: Path, media_roots: list[Path]) -> bool:
    return any(_inside_media_root(candidate, media_root) for media_root in media_roots)


def _resolve_poster_reference_path_for_scan(settings: Settings, media_root: Path) -> Path:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT value
            FROM app_settings
            WHERE key = 'poster_reference_location'
            LIMIT 1
            """
        ).fetchone()
    configured_value = str(row["value"] or "").strip() if row else ""
    if configured_value:
        configured_path = Path(configured_value).expanduser().resolve()
        if configured_path.exists() and configured_path.is_dir():
            return configured_path
    return (media_root / "Posters").resolve()


def _scan_metadata_for_file(discovered_file: DiscoveredMediaFile) -> dict[str, object]:
    metadata = discovered_file.metadata
    return {
        "series_folder_key": metadata.series_folder_key,
        "series_folder_name": metadata.series_folder_name,
        "library_category": metadata.category,
        "library_category_path": str(metadata.category_path) if metadata.category_path else None,
        "library_category_name": metadata.category_display_name,
        "library_folder_role": metadata.role,
        "library_folder_path": str(metadata.folder_path),
        "library_folder_name": metadata.folder_display_name,
    }


def _scan_metadata_matches(row, scan_metadata: dict[str, object]) -> bool:
    return all((row[key] or None) == (value or None) for key, value in scan_metadata.items())


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
    media_roots = [
        path.resolve()
        for path in get_effective_library_reference_locations(settings)
    ]
    media_root = media_roots[0] if media_roots else get_effective_shared_local_library_path(settings).resolve()
    snapshot: dict[str, object] = {
        "version": LOCAL_LIBRARY_FRESHNESS_SNAPSHOT_VERSION,
        "media_root": str(media_root),
        "media_roots": [str(path) for path in media_roots],
        "snapshot_state": "unknown",
        "root_identity": None,
        "root_identities": [],
        "top_level_count": 0,
        "top_level_fingerprint": None,
        "top_level_fingerprints": [],
    }

    if not media_roots:
        snapshot["snapshot_state"] = "missing"
        return snapshot

    root_identities: list[dict[str, int]] = []
    all_top_level_entries: list[dict[str, object]] = []
    root_fingerprints: list[str] = []
    try:
        for root_index, root in enumerate(media_roots):
            if not root.exists() or not root.is_dir():
                snapshot["snapshot_state"] = "missing"
                return snapshot
            root_stat = root.stat()
            root_identities.append(
                {
                    "st_dev": int(root_stat.st_dev),
                    "st_ino": int(root_stat.st_ino),
                }
            )
            root_entries: list[dict[str, object]] = []
            for entry in sorted(root.iterdir(), key=lambda candidate: candidate.name.lower()):
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
                root_entries.append(
                    {
                        "root_index": root_index,
                        "root": str(root),
                        "name": entry.name,
                        "kind": entry_kind,
                        "mtime_ns": int(entry_stat.st_mtime_ns),
                        "size": entry_size,
                    }
                )
            encoded_root_entries = json.dumps(
                root_entries,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            root_fingerprints.append(hashlib.sha256(encoded_root_entries).hexdigest())
            all_top_level_entries.extend(root_entries)
    except OSError:
        snapshot["snapshot_state"] = "error"
        return snapshot

    encoded_entries = json.dumps(
        all_top_level_entries,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    snapshot["snapshot_state"] = "ready"
    snapshot["root_identity"] = root_identities[0] if root_identities else None
    snapshot["root_identities"] = root_identities
    snapshot["top_level_count"] = len(all_top_level_entries)
    snapshot["top_level_fingerprint"] = hashlib.sha256(encoded_entries).hexdigest()
    snapshot["top_level_fingerprints"] = root_fingerprints
    return snapshot


def scan_media_library(settings: Settings, *, reason: str) -> dict[str, object]:
    media_root = get_effective_shared_local_library_path(settings).resolve()
    started_at = utcnow_iso()
    files_seen = 0
    files_changed = 0
    files_removed = 0
    hidden_movie_keys_pruned = 0
    folder_warnings: list[dict[str, object]] = []

    with get_connection(settings) as connection:
        catalog_revision_claimed = False

        def claim_catalog_revision() -> None:
            nonlocal catalog_revision_claimed
            if catalog_revision_claimed:
                return
            bump_library_revision_layers(
                settings,
                connection,
                global_layers=("catalog",),
            )
            catalog_revision_claimed = True

        library_reference_locations = [
            path.resolve()
            for path in get_effective_library_reference_locations(settings, connection=connection)
        ]
        poster_reference_path = _resolve_poster_reference_path_for_scan(settings, media_root)
        discovery = discover_library_folders(
            library_reference_locations,
            allowed_video_extensions=settings.allowed_video_extensions,
            poster_reference_path=poster_reference_path,
            restricted_path_checker=lambda path: is_restricted_library_reference_path(settings, path),
        )
        folder_warnings = discovery.warnings
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
            current_files: list[tuple[Path, object, dict[str, object]]] = []
            current_paths: set[str] = set()
            for discovered_file in discovery.files:
                resolved = discovered_file.path.resolve()
                if not _inside_any_media_root(resolved, library_reference_locations):
                    logger.warning("Skipping out-of-root media path %s", resolved)
                    continue
                if poster_reference_path and path_is_same_or_inside(resolved, poster_reference_path):
                    continue
                stat = resolved.stat()
                current_files.append((resolved, stat, _scan_metadata_for_file(discovered_file)))
                current_paths.add(str(resolved))

            existing_rows = connection.execute(
                """
                SELECT
                    id,
                    file_path,
                    original_filename,
                    file_size,
                    file_mtime,
                    year,
                    series_folder_key,
                    series_folder_name,
                    library_category,
                    library_category_path,
                    library_category_name,
                    library_folder_role,
                    library_folder_path,
                    library_folder_name
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

            for resolved, stat, scan_metadata in current_files:
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
                    and _scan_metadata_matches(existing, scan_metadata)
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
                claim_catalog_revision()
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
                            series_folder_key = ?,
                            series_folder_name = ?,
                            library_category = ?,
                            library_category_path = ?,
                            library_category_name = ?,
                            library_folder_role = ?,
                            library_folder_path = ?,
                            library_folder_name = ?,
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
                            scan_metadata["series_folder_key"],
                            scan_metadata["series_folder_name"],
                            scan_metadata["library_category"],
                            scan_metadata["library_category_path"],
                            scan_metadata["library_category_name"],
                            scan_metadata["library_folder_role"],
                            scan_metadata["library_folder_path"],
                            scan_metadata["library_folder_name"],
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
                            series_folder_key,
                            series_folder_name,
                            library_category,
                            library_category_path,
                            library_category_name,
                            library_folder_role,
                            library_folder_path,
                            library_folder_name,
                            created_at,
                            updated_at,
                            last_scanned_at
                        ) VALUES (?, ?, ?, 'local', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            series_folder_key = excluded.series_folder_key,
                            series_folder_name = excluded.series_folder_name,
                            library_category = excluded.library_category,
                            library_category_path = excluded.library_category_path,
                            library_category_name = excluded.library_category_name,
                            library_folder_role = excluded.library_folder_role,
                            library_folder_path = excluded.library_folder_path,
                            library_folder_name = excluded.library_folder_name,
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
                            scan_metadata["series_folder_key"],
                            scan_metadata["series_folder_name"],
                            scan_metadata["library_category"],
                            scan_metadata["library_category_path"],
                            scan_metadata["library_category_name"],
                            scan_metadata["library_folder_role"],
                            scan_metadata["library_folder_path"],
                            scan_metadata["library_folder_name"],
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
                    resolve_hidden_copy_identity(
                        connection,
                        media_item_id=media_item_id,
                    )
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
                claim_catalog_revision()
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
        "folder_warnings": folder_warnings,
        "message": "Scan completed",
    }
