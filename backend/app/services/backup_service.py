from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
import subprocess

from ..config import PROJECT_ROOT, Settings


BACKUP_FORMAT_VERSION = 1
BACKUP_WARNING = "This backup may contain secrets. Do not commit or share it."


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp_for_directory(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%d-%H%M%SZ")


def _assistant_uploads_dir(settings: Settings) -> Path:
    return settings.db_path.parent / "assistant_uploads"


def _backup_root() -> Path:
    return PROJECT_ROOT / "backend" / "data" / "backups"


def _resolve_backups_dir(backups_dir: str | Path | None) -> Path:
    if backups_dir is None:
        return _backup_root().resolve()
    return Path(backups_dir).expanduser().resolve()


def _set_private_permissions(path: Path, *, is_dir: bool) -> None:
    if os.name == "nt":
        return
    os.chmod(path, 0o700 if is_dir else 0o600)


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _set_private_permissions(path, is_dir=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file(source: Path, destination: Path) -> None:
    _ensure_private_dir(destination.parent)
    shutil.copy2(source, destination)
    _set_private_permissions(destination, is_dir=False)


def _copy_tree(source_dir: Path, destination_dir: Path) -> None:
    _ensure_private_dir(destination_dir)
    for path in sorted(source_dir.rglob("*")):
        relative_path = path.relative_to(source_dir)
        target_path = destination_dir / relative_path
        if path.is_dir():
            _ensure_private_dir(target_path)
            continue
        if path.is_file():
            _copy_file(path, target_path)


def _create_sqlite_snapshot(*, source_db_path: Path, destination_path: Path) -> str:
    if not source_db_path.exists():
        raise FileNotFoundError(f"Source database does not exist: {source_db_path}")

    _ensure_private_dir(destination_path.parent)
    source_connection = sqlite3.connect(source_db_path, check_same_thread=False)
    destination_connection = sqlite3.connect(destination_path, check_same_thread=False)
    try:
        source_connection.execute("PRAGMA busy_timeout = 5000")
        source_connection.backup(destination_connection)
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()

    _set_private_permissions(destination_path, is_dir=False)
    return _sqlite_integrity_check(destination_path)


def _sqlite_integrity_check(db_path: Path) -> str:
    connection = sqlite3.connect(db_path, check_same_thread=False)
    try:
        rows = connection.execute("PRAGMA integrity_check").fetchall()
    finally:
        connection.close()
    if len(rows) == 1 and str(rows[0][0]).lower() == "ok":
        return "ok"
    return "; ".join(str(row[0]) for row in rows)


def _safe_git_metadata(project_root: Path) -> tuple[str | None, bool | None]:
    try:
        commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
        dirty_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None, None
    return commit_result.stdout.strip() or None, bool(dirty_result.stdout.strip())


def _build_file_manifest_entries(checkpoint_dir: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in sorted(checkpoint_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "manifest.json":
            continue
        entries.append(
            {
                "relative_path": path.relative_to(checkpoint_dir).as_posix(),
                "size_bytes": int(path.stat().st_size),
                "sha256": _sha256_file(path),
            }
        )
    return entries


def _directory_file_stats(directory: Path) -> tuple[int, int]:
    total_size = 0
    file_count = 0
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        stat = path.stat()
        total_size += int(stat.st_size)
        file_count += 1
    return total_size, file_count


def _parse_created_at_for_sort(value: object) -> tuple[int, str]:
    text = str(value or "").strip()
    if not text:
        return (0, "")
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return (0, text)
    return (int(parsed.timestamp()), text)


def _load_manifest_if_present(checkpoint_dir: Path) -> dict[str, object] | None:
    manifest_path = checkpoint_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _write_manifest(manifest_path: Path, payload: dict[str, object]) -> None:
    _ensure_private_dir(manifest_path.parent)
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _set_private_permissions(manifest_path, is_dir=False)


def create_backup_checkpoint(
    settings: Settings,
    output_dir: str | Path | None = None,
    *,
    include_env: bool = True,
    include_helper_releases: bool = True,
    include_assistant_uploads: bool = True,
    backup_trigger: str = "manual_cli",
    auto_checkpoint: bool = False,
    reason: str | None = None,
    initiated_by_user_id: int | None = None,
    initiated_by_username: str | None = None,
    operation_context: dict[str, object] | None = None,
) -> dict[str, object]:
    created_at = _utc_now()
    checkpoint_dir = (
        Path(output_dir).expanduser().resolve()
        if output_dir is not None
        else (_resolve_backups_dir(None) / f"elvern-backup-{_timestamp_for_directory(created_at)}").resolve()
    )
    if checkpoint_dir.exists():
        raise FileExistsError(f"Backup checkpoint already exists: {checkpoint_dir}")

    _ensure_private_dir(_resolve_backups_dir(None))
    _ensure_private_dir(checkpoint_dir)

    db_snapshot_filename = "elvern.db"
    db_snapshot_path = checkpoint_dir / db_snapshot_filename
    db_integrity_check_result = _create_sqlite_snapshot(
        source_db_path=settings.db_path,
        destination_path=db_snapshot_path,
    )

    env_source = (PROJECT_ROOT / "deploy" / "env" / "elvern.env").resolve()
    env_included = bool(include_env and env_source.exists())
    if env_included:
        _copy_file(env_source, checkpoint_dir / "deploy" / "env" / "elvern.env")

    helper_releases_source = settings.helper_releases_dir.resolve()
    helper_releases_included = bool(include_helper_releases and helper_releases_source.exists())
    if helper_releases_included:
        _copy_tree(
            helper_releases_source,
            checkpoint_dir / "backend" / "data" / "helper_releases",
        )

    assistant_uploads_source = _assistant_uploads_dir(settings).resolve()
    assistant_uploads_included = bool(
        include_assistant_uploads and assistant_uploads_source.exists()
    )
    if assistant_uploads_included:
        _copy_tree(
            assistant_uploads_source,
            checkpoint_dir / "backend" / "data" / "assistant_uploads",
        )

    git_commit, git_dirty = _safe_git_metadata(PROJECT_ROOT)
    manifest = {
        "backup_format_version": BACKUP_FORMAT_VERSION,
        "created_at_utc": _utc_iso(created_at),
        "app_name": settings.app_name,
        "db_snapshot_filename": db_snapshot_filename,
        "db_integrity_check_result": db_integrity_check_result,
        "source_db_path": str(settings.db_path.resolve()),
        "project_root": str(PROJECT_ROOT.resolve()),
        "public_app_origin": settings.public_app_origin,
        "backend_origin": settings.backend_origin,
        "media_root_path": str(settings.media_root.resolve()),
        "transcode_dir": str(settings.transcode_dir.resolve()),
        "helper_releases_included": helper_releases_included,
        "assistant_uploads_included": assistant_uploads_included,
        "env_included": env_included,
        "contains_secrets": True,
        "backup_trigger": backup_trigger,
        "auto_checkpoint": bool(auto_checkpoint),
        "reason": reason,
        "initiated_by_user_id": initiated_by_user_id,
        "initiated_by_username": initiated_by_username,
        "operation_context": dict(operation_context or {}),
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "files": _build_file_manifest_entries(checkpoint_dir),
    }
    _write_manifest(checkpoint_dir / "manifest.json", manifest)

    return {
        "checkpoint_id": checkpoint_dir.name,
        "backup_path": str(checkpoint_dir),
        "manifest_path": str((checkpoint_dir / "manifest.json").resolve()),
        "created_at_utc": manifest["created_at_utc"],
        "backup_trigger": backup_trigger,
        "auto_checkpoint": bool(auto_checkpoint),
        "warning": BACKUP_WARNING,
        "contains_secrets": True,
        "manifest": manifest,
    }


def inspect_backup_checkpoint(path: str | Path) -> dict[str, object]:
    requested_path = Path(path).expanduser().resolve()
    checkpoint_dir = requested_path if requested_path.is_dir() else requested_path.parent
    manifest_path = checkpoint_dir / "manifest.json"

    errors: list[str] = []
    missing_files: list[str] = []
    hash_mismatches: list[dict[str, object]] = []
    files_verified = 0
    manifest_exists = manifest_path.is_file()
    manifest_payload: dict[str, object] | None = None
    db_snapshot_exists = False
    db_integrity_check_result = "manifest_missing"

    if not manifest_exists:
        errors.append(f"Missing manifest.json at {manifest_path}")
    else:
        try:
            manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"Invalid manifest.json: {exc}")

    if manifest_payload is not None:
        db_snapshot_filename = str(manifest_payload.get("db_snapshot_filename") or "elvern.db")
        db_snapshot_path = checkpoint_dir / db_snapshot_filename
        db_snapshot_exists = db_snapshot_path.is_file()
        if not db_snapshot_exists:
            missing_files.append(db_snapshot_filename)
            db_integrity_check_result = "missing"
        else:
            db_integrity_check_result = _sqlite_integrity_check(db_snapshot_path)

        for entry in manifest_payload.get("files") or []:
            relative_path = str((entry or {}).get("relative_path") or "")
            expected_hash = str((entry or {}).get("sha256") or "")
            target_path = checkpoint_dir / relative_path
            if not target_path.is_file():
                missing_files.append(relative_path)
                continue
            actual_hash = _sha256_file(target_path)
            files_verified += 1
            if expected_hash != actual_hash:
                hash_mismatches.append(
                    {
                        "relative_path": relative_path,
                        "expected_sha256": expected_hash,
                        "actual_sha256": actual_hash,
                    }
                )

    valid = (
        manifest_exists
        and manifest_payload is not None
        and not errors
        and db_snapshot_exists
        and db_integrity_check_result == "ok"
        and not missing_files
        and not hash_mismatches
    )
    return {
        "checkpoint_id": checkpoint_dir.name,
        "backup_path": str(checkpoint_dir),
        "manifest_path": str(manifest_path),
        "manifest_exists": manifest_exists,
        "db_snapshot_exists": db_snapshot_exists,
        "db_integrity_check_result": db_integrity_check_result,
        "files_verified": files_verified,
        "missing_files": sorted(dict.fromkeys(missing_files)),
        "hash_mismatches": hash_mismatches,
        "errors": errors,
        "valid": valid,
        "contains_secrets": bool((manifest_payload or {}).get("contains_secrets")),
        "warning": BACKUP_WARNING if bool((manifest_payload or {}).get("contains_secrets")) else None,
        "manifest": manifest_payload,
    }


def list_backup_checkpoints(
    settings: Settings,
    backups_dir: str | Path | None = None,
) -> list[dict[str, object]]:
    del settings
    resolved_backups_dir = _resolve_backups_dir(backups_dir)
    if not resolved_backups_dir.exists():
        return []

    entries: list[dict[str, object]] = []
    for checkpoint_dir in sorted(
        (candidate for candidate in resolved_backups_dir.iterdir() if candidate.is_dir()),
        key=lambda candidate: candidate.name,
        reverse=True,
    ):
        if not (checkpoint_dir / "manifest.json").is_file():
            continue
        inspection = inspect_backup_checkpoint(checkpoint_dir)
        manifest = inspection.get("manifest") or {}
        total_size_bytes, file_count = _directory_file_stats(checkpoint_dir)
        errors = list(inspection.get("errors") or [])
        if inspection.get("missing_files"):
            errors.append(
                "Missing files: " + ", ".join(str(value) for value in inspection["missing_files"])
            )
        if inspection.get("hash_mismatches"):
            errors.append("Hash mismatches detected")
        entries.append(
            {
                "checkpoint_id": checkpoint_dir.name,
                "path": str(checkpoint_dir),
                "created_at_utc": manifest.get("created_at_utc"),
                "backup_format_version": manifest.get("backup_format_version"),
                "backup_trigger": manifest.get("backup_trigger"),
                "auto_checkpoint": bool(manifest.get("auto_checkpoint") is True),
                "contains_secrets": bool(manifest.get("contains_secrets")),
                "db_integrity_check_result": inspection.get("db_integrity_check_result"),
                "total_size_bytes": total_size_bytes,
                "file_count": file_count,
                "git_commit": manifest.get("git_commit"),
                "git_dirty": manifest.get("git_dirty"),
                "inspect_valid": bool(inspection.get("valid")),
                "inspect_error": "; ".join(errors) if errors else None,
            }
        )

    entries.sort(
        key=lambda entry: (
            _parse_created_at_for_sort(entry.get("created_at_utc")),
            str(entry.get("checkpoint_id") or ""),
        ),
        reverse=True,
    )
    return entries


def prune_backup_checkpoints(
    settings: Settings,
    keep_auto: int = 10,
    backups_dir: str | Path | None = None,
) -> dict[str, object]:
    entries = list_backup_checkpoints(settings, backups_dir=backups_dir)
    capped_keep_auto = max(int(keep_auto), 0)

    auto_entries: list[dict[str, object]] = []
    skipped_manual_count = 0
    skipped_unknown_count = 0
    for entry in entries:
        manifest = _load_manifest_if_present(Path(str(entry["path"])))
        auto_flag = None if manifest is None else manifest.get("auto_checkpoint")
        if auto_flag is True:
            auto_entries.append(entry)
        elif auto_flag is False:
            skipped_manual_count += 1
        else:
            skipped_unknown_count += 1

    auto_entries.sort(
        key=lambda entry: (
            _parse_created_at_for_sort(entry.get("created_at_utc")),
            str(entry.get("checkpoint_id") or ""),
        ),
        reverse=True,
    )
    to_delete = auto_entries[capped_keep_auto:]

    deleted_paths: list[str] = []
    errors: list[str] = []
    for entry in to_delete:
        checkpoint_path = Path(str(entry["path"]))
        try:
            shutil.rmtree(checkpoint_path)
        except OSError as exc:
            errors.append(f"Failed to delete {checkpoint_path}: {exc}")
            continue
        deleted_paths.append(str(checkpoint_path))

    return {
        "kept_count": len(entries) - len(deleted_paths),
        "deleted_count": len(deleted_paths),
        "deleted_paths": deleted_paths,
        "skipped_manual_count": skipped_manual_count,
        "skipped_unknown_count": skipped_unknown_count,
        "errors": errors,
    }
