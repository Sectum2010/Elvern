from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path

from .config import Settings
from .db import get_connection, preserve_hidden_movie_keys_for_media_item, utcnow_iso
from .db_hidden_movie_keys import (
    find_local_copy_identity_candidates,
    LocalCopyEvidenceMemo,
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


def _write_scanned_local_media_item(
    connection,
    *,
    media_item_id: int | None,
    resolved: Path,
    file_stat,
    scan_metadata: dict[str, object],
    media_metadata: dict[str, object],
    shared_local_source_id: int,
    preserved_year: int | None,
    now: str,
) -> int:
    values = (
        resolved.stem,
        resolved.name,
        str(resolved),
        shared_local_source_id,
        file_stat.st_size,
        file_stat.st_mtime,
        media_metadata["duration_seconds"],
        media_metadata["width"],
        media_metadata["height"],
        media_metadata["video_codec"],
        media_metadata["audio_codec"],
        media_metadata["container"],
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
    )
    if media_item_id is not None:
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
            (*values, media_item_id),
        )
        return media_item_id
    cursor = connection.execute(
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
        """,
        (
            *values[:-2],
            now,
            now,
            now,
        ),
    )
    return int(cursor.lastrowid)


def _replace_scanned_subtitles(
    connection,
    *,
    media_item_id: int,
    subtitles: list[dict[str, object]],
) -> None:
    connection.execute(
        "DELETE FROM subtitle_tracks WHERE media_item_id = ?",
        (media_item_id,),
    )
    for subtitle in subtitles:
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
        connection.execute("SAVEPOINT media_scan_mutations")
        try:
            current_files: list[tuple[Path, object, dict[str, object]]] = []
            for discovered_file in discovery.files:
                resolved = discovered_file.path.resolve()
                if not _inside_any_media_root(resolved, library_reference_locations):
                    logger.warning("Skipping out-of-root media path %s", resolved)
                    continue
                if poster_reference_path and path_is_same_or_inside(resolved, poster_reference_path):
                    continue
                stat = resolved.stat()
                current_files.append((resolved, stat, _scan_metadata_for_file(discovered_file)))

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
                    library_folder_name,
                    COALESCE(source_kind, 'local') AS source_kind,
                    library_source_id,
                    hidden_copy_identity
                FROM media_items
                WHERE COALESCE(source_kind, 'local') = 'local'
                """
            ).fetchall()
            existing_by_path = {row["file_path"]: row for row in existing_rows}
            evidence_memo = LocalCopyEvidenceMemo()
            current_records: list[dict[str, object]] = []
            stable_existing_ids: set[int] = set()

            # Classify continuity before writing aliases or mutating media rows.
            for resolved, file_stat, scan_metadata in current_files:
                file_path = str(resolved)
                files_seen += 1
                existing = existing_by_path.get(file_path)
                record: dict[str, object] = {
                    "resolved": resolved,
                    "file_stat": file_stat,
                    "scan_metadata": scan_metadata,
                    "file_path": file_path,
                    "existing": existing,
                    "stable": False,
                    "candidate_identities": (),
                    "candidate_row_ids": set(),
                    "rename_target": None,
                    "legacy_same_path_target": None,
                }
                stat_unchanged = bool(
                    existing
                    and existing["file_size"] == file_stat.st_size
                    and existing["file_mtime"] == file_stat.st_mtime
                )
                metadata_unchanged = bool(
                    stat_unchanged and _scan_metadata_matches(existing, scan_metadata)
                )
                if metadata_unchanged:
                    existing_identity = str(existing["hidden_copy_identity"] or "").strip()
                    if existing_identity:
                        locator_candidates = find_local_copy_identity_candidates(
                            connection,
                            media_row=existing,
                            file_path=resolved,
                            file_stat=file_stat,
                            require_locator=True,
                            evidence_memo=evidence_memo,
                            include_content_sample=False,
                        )
                        if existing_identity in locator_candidates:
                            record["stable"] = True
                        else:
                            strong_locator_candidates = find_local_copy_identity_candidates(
                                connection,
                                media_row=existing,
                                file_path=resolved,
                                file_stat=file_stat,
                                require_locator=True,
                                evidence_memo=evidence_memo,
                                require_sample=True,
                            )
                            record["stable"] = existing_identity in strong_locator_candidates
                            if record["stable"]:
                                resolve_hidden_copy_identity(
                                    connection,
                                    media_row=existing,
                                    local_file_path=resolved,
                                    local_file_stat=file_stat,
                                    evidence_memo=evidence_memo,
                                )
                    else:
                        resolve_hidden_copy_identity(
                            connection,
                            media_row=existing,
                            local_file_path=resolved,
                            local_file_stat=file_stat,
                            evidence_memo=evidence_memo,
                            include_content_sample=False,
                        )
                        record["stable"] = True
                elif (
                    stat_unchanged
                    and not str(existing["hidden_copy_identity"] or "").strip()
                ):
                    # Legacy rows without identity evidence cannot be matched by alias.
                    # Keep the same row while scanner-owned folder metadata is upgraded.
                    record["legacy_same_path_target"] = existing
                if record["stable"]:
                    stable_existing_ids.add(int(existing["id"]))
                elif record["legacy_same_path_target"] is None:
                    record["candidate_identities"] = find_local_copy_identity_candidates(
                        connection,
                        media_row={
                            "source_kind": "local",
                            "library_source_id": shared_local_source_id,
                            "file_path": file_path,
                        },
                        file_path=resolved,
                        file_stat=file_stat,
                        require_locator=False,
                        evidence_memo=evidence_memo,
                        require_sample=True,
                    )
                current_records.append(record)

            displaced_rows = [
                row
                for row in existing_rows
                if int(row["id"]) not in stable_existing_ids
            ]
            displaced_rows_by_identity: dict[str, list] = {}
            for row in displaced_rows:
                identity = str(row["hidden_copy_identity"] or "").strip()
                if identity:
                    displaced_rows_by_identity.setdefault(identity, []).append(row)

            candidate_paths_by_row_id: dict[int, set[str]] = {}
            for record in current_records:
                if record["stable"]:
                    continue
                legacy_same_path_target = record["legacy_same_path_target"]
                candidate_row_ids = (
                    {int(legacy_same_path_target["id"])}
                    if legacy_same_path_target is not None
                    else {
                        int(row["id"])
                        for identity in record["candidate_identities"]
                        for row in displaced_rows_by_identity.get(str(identity), [])
                    }
                )
                record["candidate_row_ids"] = candidate_row_ids
                for row_id in candidate_row_ids:
                    candidate_paths_by_row_id.setdefault(row_id, set()).add(
                        str(record["file_path"])
                    )

            matched_existing_ids: set[int] = set()
            for record in current_records:
                candidate_row_ids = record["candidate_row_ids"]
                if record["stable"] or len(candidate_row_ids) != 1:
                    continue
                candidate_row_id = next(iter(candidate_row_ids))
                if len(candidate_paths_by_row_id.get(candidate_row_id, ())) != 1:
                    continue
                rename_target = next(
                    row for row in displaced_rows if int(row["id"]) == candidate_row_id
                )
                record["rename_target"] = rename_target
                matched_existing_ids.add(candidate_row_id)

            changed_records = [record for record in current_records if not record["stable"]]
            removable_rows = [
                row for row in displaced_rows if int(row["id"]) not in matched_existing_ids
            ]
            if changed_records or removable_rows:
                claim_catalog_revision()

            # Stage matched rows first so rename cycles and path replacements cannot
            # violate the unique file_path constraint during the final apply.
            for record in changed_records:
                rename_target = record["rename_target"]
                if (
                    rename_target is None
                    or str(rename_target["file_path"]) == str(record["file_path"])
                ):
                    continue
                connection.execute(
                    "UPDATE media_items SET file_path = ? WHERE id = ?",
                    (
                        f"__elvern_scan_staging__:{job_id}:{int(rename_target['id'])}",
                        int(rename_target["id"]),
                    ),
                )

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

            for record in changed_records:
                resolved = record["resolved"]
                file_stat = record["file_stat"]
                scan_metadata = record["scan_metadata"]
                rename_target = record["rename_target"]
                media_metadata = extract_media_metadata(resolved, settings)
                _, inferred_year = infer_title_and_year(resolved.stem)
                existing_year = rename_target["year"] if rename_target is not None else None
                preserved_year = _preserve_known_year(
                    inferred_year=inferred_year,
                    existing_year=existing_year,
                )
                now = utcnow_iso()
                media_item_id = _write_scanned_local_media_item(
                    connection,
                    media_item_id=(
                        int(rename_target["id"]) if rename_target is not None else None
                    ),
                    resolved=resolved,
                    file_stat=file_stat,
                    scan_metadata=scan_metadata,
                    media_metadata=media_metadata,
                    shared_local_source_id=shared_local_source_id,
                    preserved_year=preserved_year,
                    now=now,
                )
                blocked_identities = {
                    str(identity)
                    for identity in record["candidate_identities"]
                    if displaced_rows_by_identity.get(str(identity))
                }
                resolve_hidden_copy_identity(
                    connection,
                    media_item_id=media_item_id,
                    local_file_path=resolved,
                    local_file_stat=file_stat,
                    evidence_memo=evidence_memo,
                    disallowed_alias_identities=(
                        blocked_identities if rename_target is None else None
                    ),
                )
                _replace_scanned_subtitles(
                    connection,
                    media_item_id=media_item_id,
                    subtitles=media_metadata["subtitles"],
                )
                files_changed += 1

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
            connection.execute("RELEASE SAVEPOINT media_scan_mutations")
            connection.commit()
        except Exception as exc:
            connection.execute("ROLLBACK TO SAVEPOINT media_scan_mutations")
            connection.execute("RELEASE SAVEPOINT media_scan_mutations")
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
