from __future__ import annotations

import hashlib
import io
import json
import os
import errno
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from contextlib import contextmanager
from typing import Callable, Iterator, Literal

from ..config import PROJECT_ROOT, Settings
from ..db import get_connection
from .backup_encryption import (
    HEADER_MAGIC,
    HEADER_MAGIC_V2,
    KEY_SOURCE_AUTO,
    KEY_SOURCE_PASSPHRASE,
    BackupEncryptionError,
    V2EncryptingWriter,
    decrypt_backup_file_v2,
    decrypt_backup,
    encrypt_backup,
    inspect_encrypted_backup_file_header,
    inspect_encrypted_backup_header,
    validate_backup_passphrase,
)


BACKUP_FORMAT_VERSION = 2
RESTORE_PLAN_FORMAT_VERSION = 1
BACKUP_WARNING = (
    "Manual backups may contain secrets such as env values, OAuth tokens, "
    "session-related secrets, and database contents. Do not commit or share them."
)
PLAINTEXT_BACKUP_DENIED_ERROR = (
    "Plaintext backup is unsafe. Use explicit allow_plaintext_backup=True only for "
    "dev/test/manual recovery scenarios."
)
BACKUP_EXCLUDED_RUNTIME_PATHS = ("backend/data/playback_diagnostics",)
PLAINTEXT_BACKUP_WARNING = (
    BACKUP_WARNING
    + " This checkpoint is a plaintext backup directory; keep it on trusted storage "
    "only and encrypt or remove it when finished."
)


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


def get_backups_dir_path(
    settings: Settings,
    backups_dir: str | Path | None = None,
) -> str:
    del settings
    return str(_resolve_backups_dir(backups_dir))


def _allocate_default_checkpoint_dir(created_at: datetime) -> Path:
    backups_dir = _resolve_backups_dir(None)
    base_name = f"elvern-backup-{_timestamp_for_directory(created_at)}"
    candidate = (backups_dir / base_name).resolve()
    if not candidate.exists():
        return candidate

    suffix = 2
    while True:
        candidate = (backups_dir / f"{base_name}-{suffix}").resolve()
        if not candidate.exists():
            return candidate
        suffix += 1


def _allocate_default_encrypted_backup_path(created_at: datetime) -> Path:
    backups_dir = _resolve_backups_dir(None)
    base_name = f"elvern-backup-{_timestamp_for_directory(created_at)}"
    candidate = (backups_dir / f"{base_name}.tar.gz.enc").resolve()
    if not candidate.exists():
        return candidate

    suffix = 2
    while True:
        candidate = (backups_dir / f"{base_name}-{suffix}.tar.gz.enc").resolve()
        if not candidate.exists():
            return candidate
        suffix += 1


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


def _create_tar_gz_bytes(source_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(source_dir.rglob("*")):
            archive.add(path, arcname=path.relative_to(source_dir).as_posix())
    return buffer.getvalue()


def _extract_tar_gz_bytes(tarball_bytes: bytes, destination_dir: Path) -> None:
    _ensure_private_dir(destination_dir)
    with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as archive:
        destination_root = destination_dir.resolve()
        for member in archive.getmembers():
            target = (destination_root / member.name).resolve()
            try:
                target.relative_to(destination_root)
            except ValueError as exc:
                raise ValueError("Backup archive contains unsafe paths") from exc
            if member.isdir():
                _ensure_private_dir(target)
                continue
            if not member.isfile():
                raise ValueError("Backup archive contains unsupported member type")
            _ensure_private_dir(target.parent)
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError("Backup archive contains unreadable file")
            with extracted, target.open("wb") as output:
                shutil.copyfileobj(extracted, output)
            _set_private_permissions(target, is_dir=False)


def _extract_tar_gz_file(tarball_path: Path, destination_dir: Path) -> None:
    _ensure_private_dir(destination_dir)
    destination_root = destination_dir.resolve()
    with tarfile.open(tarball_path, mode="r:gz") as archive:
        for member in archive:
            target = (destination_root / member.name).resolve()
            try:
                target.relative_to(destination_root)
            except ValueError as exc:
                raise ValueError("Backup archive contains unsafe paths") from exc
            if member.isdir():
                _ensure_private_dir(target)
                continue
            if not member.isfile():
                raise ValueError("Backup archive contains unsupported member type")
            _ensure_private_dir(target.parent)
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError("Backup archive contains unreadable file")
            with extracted, target.open("wb") as output:
                shutil.copyfileobj(extracted, output, length=1024 * 1024)
            _set_private_permissions(target, is_dir=False)


def _stream_encrypted_archive(
    source_dir: Path,
    destination_path: Path,
    *,
    settings: Settings,
    passphrase: str | None,
) -> dict[str, object]:
    _ensure_private_dir(destination_path.parent)
    temporary_path = destination_path.with_name(f".{destination_path.name}.{os.getpid()}.tmp")
    temporary_path.unlink(missing_ok=True)
    try:
        with temporary_path.open("wb") as output:
            _set_private_permissions(temporary_path, is_dir=False)
            writer = V2EncryptingWriter(output, settings=settings, passphrase=passphrase)
            try:
                with tarfile.open(fileobj=writer, mode="w|gz") as archive:
                    for path in sorted(source_dir.rglob("*")):
                        archive.add(path, arcname=path.relative_to(source_dir).as_posix())
            finally:
                writer.close()
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, destination_path)
        _set_private_permissions(destination_path, is_dir=False)
        directory_fd = os.open(destination_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return dict(writer.header)
    finally:
        temporary_path.unlink(missing_ok=True)


def _create_sqlite_snapshot(
    *,
    source_db_path: Path,
    destination_path: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> str:
    if not source_db_path.exists():
        raise FileNotFoundError(f"Source database does not exist: {source_db_path}")

    _ensure_private_dir(destination_path.parent)
    source_connection = sqlite3.connect(source_db_path, check_same_thread=False)
    destination_connection = sqlite3.connect(destination_path, check_same_thread=False)
    try:
        source_connection.execute("PRAGMA busy_timeout = 5000")
        latest_page_progress: tuple[int, int] | None = None

        def report_pages(_status: int, remaining: int, total: int) -> None:
            nonlocal latest_page_progress
            latest_page_progress = (
                max(int(total) - int(remaining), 0),
                max(int(total), 0),
            )

        source_connection.backup(
            destination_connection,
            pages=64,
            progress=report_pages if progress_callback is not None else None,
        )
        destination_connection.commit()
        if progress_callback is not None and latest_page_progress is not None:
            progress_callback(*latest_page_progress)
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


def _resolve_checkpoint_dir(path: str | Path) -> Path:
    requested_path = Path(path).expanduser().resolve()
    return requested_path if requested_path.is_dir() else requested_path.parent


@contextmanager
def _materialized_checkpoint(
    path: str | Path,
    *,
    settings: Settings | None = None,
    passphrase: str | None = None,
) -> Iterator[tuple[Path, dict[str, object]]]:
    requested_path = Path(path).expanduser().resolve()
    if requested_path.is_dir():
        yield requested_path, {
            "storage_kind": "legacy_plaintext_directory",
            "encrypted": False,
            "key_source": None,
            "archive_path": None,
        }
        return

    if not requested_path.is_file():
        yield requested_path.parent, {
            "storage_kind": "missing",
            "encrypted": False,
            "key_source": None,
            "archive_path": str(requested_path),
        }
        return

    with requested_path.open("rb") as source:
        magic = source.read(max(len(HEADER_MAGIC), len(HEADER_MAGIC_V2)))
    if magic.startswith(HEADER_MAGIC_V2):
        if settings is None:
            raise ValueError("Encrypted backup inspection requires settings")
        with tempfile.TemporaryDirectory(prefix="elvern-backup-inspect-v2-") as tmp_dir:
            temporary_root = Path(tmp_dir)
            tarball_path = temporary_root / "checkpoint.tar.gz"
            header = decrypt_backup_file_v2(
                requested_path,
                tarball_path,
                settings=settings,
                passphrase=passphrase,
            )
            checkpoint_dir = temporary_root / "checkpoint"
            _extract_tar_gz_file(tarball_path, checkpoint_dir)
            yield checkpoint_dir, {
                "storage_kind": "encrypted_archive",
                "encrypted": True,
                "key_source": header.get("key_source"),
                "format_version": 2,
                "archive_path": str(requested_path),
            }
        return

    blob = requested_path.read_bytes()
    if blob.startswith(HEADER_MAGIC):
        if settings is None:
            raise ValueError("Encrypted backup inspection requires settings")
        header = inspect_encrypted_backup_header(blob)
        tarball_bytes = decrypt_backup(blob, settings=settings, passphrase=passphrase)
        with tempfile.TemporaryDirectory(prefix="elvern-backup-inspect-") as tmp_dir:
            checkpoint_dir = Path(tmp_dir) / requested_path.stem.removesuffix(".tar.gz")
            _extract_tar_gz_bytes(tarball_bytes, checkpoint_dir)
            yield checkpoint_dir, {
                "storage_kind": "encrypted_archive",
                "encrypted": True,
                "key_source": header.get("key_source"),
                "archive_path": str(requested_path),
            }
        return

    if requested_path.name.endswith(".tar.gz"):
        with tempfile.TemporaryDirectory(prefix="elvern-backup-legacy-") as tmp_dir:
            checkpoint_dir = Path(tmp_dir) / requested_path.stem.removesuffix(".tar")
            _extract_tar_gz_bytes(blob, checkpoint_dir)
            yield checkpoint_dir, {
                "storage_kind": "legacy_plaintext_archive",
                "encrypted": False,
                "key_source": None,
                "archive_path": str(requested_path),
            }
        return

    yield requested_path.parent, {
        "storage_kind": "unknown_file",
        "encrypted": False,
        "key_source": None,
        "archive_path": str(requested_path),
    }


def _safe_resolved_path(path: Path) -> str:
    return str(path.expanduser().resolve())


def _collect_inspection_errors(inspection: dict[str, object]) -> list[str]:
    errors = list(str(value) for value in inspection.get("errors") or [])
    if inspection.get("missing_files"):
        errors.append(
            "Missing files: " + ", ".join(str(value) for value in inspection["missing_files"])
        )
    if inspection.get("hash_mismatches"):
        errors.append("Hash mismatches detected")
    return errors


def _write_manifest(manifest_path: Path, payload: dict[str, object]) -> None:
    _ensure_private_dir(manifest_path.parent)
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _set_private_permissions(manifest_path, is_dir=False)


def _create_backup_checkpoint_impl(
    settings: Settings,
    output_dir: str | Path | None = None,
    *,
    allow_plaintext_backup: bool = False,
    include_env: bool | None = None,
    include_helper_releases: bool = True,
    include_assistant_uploads: bool = True,
    backup_trigger: str = "manual_cli",
    auto_checkpoint: bool = False,
    trigger_kind: Literal["auto", "manual"] | None = None,
    passphrase: str | None = None,
    reason: str | None = None,
    initiated_by_user_id: int | None = None,
    initiated_by_username: str | None = None,
    operation_context: dict[str, object] | None = None,
    progress_callback: Callable[[str, int, int, str], None] | None = None,
    progress_metrics_callback: Callable[[str, int, int, str, int, int, str], None] | None = None,
    staging_path_callback: Callable[[Path], None] | None = None,
) -> dict[str, object]:
    def report(stage: str, current: int, total: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback(stage, current, total, message)

    created_at = _utc_now()
    resolved_trigger_kind = trigger_kind or ("auto" if auto_checkpoint else "manual")
    resolved_include_env = (resolved_trigger_kind == "manual") if include_env is None else bool(include_env)
    encrypted_output = output_dir is None
    if encrypted_output and resolved_trigger_kind == "manual" and passphrase:
        passphrase = validate_backup_passphrase(passphrase)
    if not encrypted_output and not allow_plaintext_backup:
        raise ValueError(PLAINTEXT_BACKUP_DENIED_ERROR)
    encrypted_backup_path = _allocate_default_encrypted_backup_path(created_at) if encrypted_output else None
    checkpoint_dir = Path(output_dir).expanduser().resolve() if output_dir is not None else (
        _resolve_backups_dir(None) / f".staging-{encrypted_backup_path.name.removesuffix('.tar.gz.enc')}"
    ).resolve()
    if checkpoint_dir.exists():
        raise FileExistsError(f"Backup checkpoint already exists: {checkpoint_dir}")
    if encrypted_backup_path is not None and encrypted_backup_path.exists():
        raise FileExistsError(f"Backup checkpoint already exists: {encrypted_backup_path}")

    _ensure_private_dir(_resolve_backups_dir(None))
    _ensure_private_dir(checkpoint_dir)
    if staging_path_callback is not None:
        staging_path_callback(checkpoint_dir)

    report("snapshotting_database", 5, 100, "Creating a consistent database snapshot")
    db_snapshot_filename = "elvern.db"
    db_snapshot_path = checkpoint_dir / db_snapshot_filename
    def report_snapshot_pages(completed_pages: int, total_pages: int) -> None:
        overall_current = 5
        if total_pages > 0:
            overall_current += round((completed_pages / total_pages) * 15)
        message = f"Creating a consistent database snapshot ({completed_pages}/{total_pages} pages)"
        report("snapshotting_database", overall_current, 100, message)
        if progress_metrics_callback is not None:
            progress_metrics_callback(
                "snapshotting_database",
                completed_pages,
                total_pages,
                "pages",
                overall_current,
                100,
                message,
            )

    db_integrity_check_result = _create_sqlite_snapshot(
        source_db_path=settings.db_path,
        destination_path=db_snapshot_path,
        progress_callback=report_snapshot_pages,
    )

    report("collecting_components", 22, 100, "Collecting protected runtime components")
    env_source = (PROJECT_ROOT / "deploy" / "env" / "elvern.env").resolve()
    env_included = bool(resolved_include_env and env_source.exists())
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

    report("sealing_manifest", 50, 100, "Sealing the checkpoint manifest")
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
        "excluded_runtime_paths": list(BACKUP_EXCLUDED_RUNTIME_PATHS),
        "env_included": env_included,
        "contains_secrets": True,
        "backup_trigger": backup_trigger,
        "auto_checkpoint": bool(auto_checkpoint),
        "trigger_kind": resolved_trigger_kind,
        "backup_storage": "encrypted_archive" if encrypted_output else "legacy_plaintext_directory",
        "backup_encrypted": bool(encrypted_output),
        "backup_key_source": (
            KEY_SOURCE_PASSPHRASE
            if encrypted_output and resolved_trigger_kind == "manual" and passphrase
            else KEY_SOURCE_AUTO
            if encrypted_output
            else None
        ),
        "reason": reason,
        "initiated_by_user_id": initiated_by_user_id,
        "initiated_by_username": initiated_by_username,
        "operation_context": dict(operation_context or {}),
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "files": _build_file_manifest_entries(checkpoint_dir),
    }
    _write_manifest(checkpoint_dir / "manifest.json", manifest)

    backup_path = checkpoint_dir
    manifest_path = checkpoint_dir / "manifest.json"
    total_size_bytes, file_count = _directory_file_stats(checkpoint_dir)
    if encrypted_output and encrypted_backup_path is not None:
        free_bytes = shutil.disk_usage(encrypted_backup_path.parent).free
        estimated_required = max(total_size_bytes * 2, 8 * 1024 * 1024)
        if free_bytes < estimated_required:
            raise OSError(
                errno.ENOSPC,
                f"Insufficient disk space for backup: need about {estimated_required} bytes",
            )
        report("archiving", 62, 100, "Archiving checkpoint components")
        report("encrypting", 72, 100, "Encrypting checkpoint with AES-256-GCM")
        encryption_header = _stream_encrypted_archive(
            checkpoint_dir,
            encrypted_backup_path,
            settings=settings,
            passphrase=passphrase if resolved_trigger_kind == "manual" else None,
        )
        manifest["backup_key_id"] = encryption_header.get("key_id")
        shutil.rmtree(checkpoint_dir)
        backup_path = encrypted_backup_path
        manifest_path = encrypted_backup_path
        total_size_bytes = int(encrypted_backup_path.stat().st_size)
        file_count = 1
        report("writing_checkpoint", 88, 100, "Committing the encrypted checkpoint")
        report("verifying_checkpoint", 96, 100, "Verifying checkpoint metadata")

    report("completed", 100, 100, "Backup checkpoint created")

    return {
        "checkpoint_id": backup_path.name,
        "backup_path": str(backup_path),
        "manifest_path": str(manifest_path.resolve()),
        "created_at_utc": manifest["created_at_utc"],
        "backup_trigger": backup_trigger,
        "auto_checkpoint": bool(auto_checkpoint),
        "warning": BACKUP_WARNING if encrypted_output else PLAINTEXT_BACKUP_WARNING,
        "contains_secrets": True,
        "backup_storage": manifest["backup_storage"],
        "backup_encrypted": bool(encrypted_output),
        "backup_key_source": manifest["backup_key_source"],
        "total_size_bytes": total_size_bytes,
        "file_count": file_count,
        "manifest": manifest,
    }


def create_backup_checkpoint(
    settings: Settings,
    output_dir: str | Path | None = None,
    *,
    allow_plaintext_backup: bool = False,
    include_env: bool | None = None,
    include_helper_releases: bool = True,
    include_assistant_uploads: bool = True,
    backup_trigger: str = "manual_cli",
    auto_checkpoint: bool = False,
    trigger_kind: Literal["auto", "manual"] | None = None,
    passphrase: str | None = None,
    reason: str | None = None,
    initiated_by_user_id: int | None = None,
    initiated_by_username: str | None = None,
    operation_context: dict[str, object] | None = None,
    progress_callback: Callable[[str, int, int, str], None] | None = None,
    progress_metrics_callback: Callable[[str, int, int, str, int, int, str], None] | None = None,
    staging_path_callback: Callable[[Path], None] | None = None,
) -> dict[str, object]:
    staging_path: Path | None = None

    def remember_staging_path(path: Path) -> None:
        nonlocal staging_path
        staging_path = path
        if staging_path_callback is not None:
            staging_path_callback(path)

    try:
        return _create_backup_checkpoint_impl(
            settings,
            output_dir,
            allow_plaintext_backup=allow_plaintext_backup,
            include_env=include_env,
            include_helper_releases=include_helper_releases,
            include_assistant_uploads=include_assistant_uploads,
            backup_trigger=backup_trigger,
            auto_checkpoint=auto_checkpoint,
            trigger_kind=trigger_kind,
            passphrase=passphrase,
            reason=reason,
            initiated_by_user_id=initiated_by_user_id,
            initiated_by_username=initiated_by_username,
            operation_context=operation_context,
            progress_callback=progress_callback,
            progress_metrics_callback=progress_metrics_callback,
            staging_path_callback=remember_staging_path,
        )
    finally:
        if output_dir is None and staging_path is not None and staging_path.exists():
            shutil.rmtree(staging_path, ignore_errors=True)


def _inspect_plaintext_checkpoint_dir(checkpoint_dir: Path) -> dict[str, object]:
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
            try:
                db_integrity_check_result = _sqlite_integrity_check(db_snapshot_path)
            except sqlite3.Error as exc:
                errors.append(f"SQLite integrity_check failed for {db_snapshot_filename}: {exc}")
                db_integrity_check_result = f"error: {exc}"

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
    total_size_bytes, file_count = _directory_file_stats(checkpoint_dir)
    return {
        "checkpoint_id": checkpoint_dir.name,
        "backup_path": str(checkpoint_dir),
        "manifest_path": str(manifest_path),
        "manifest_exists": manifest_exists,
        "db_snapshot_exists": db_snapshot_exists,
        "db_integrity_check_result": db_integrity_check_result,
        "files_verified": files_verified,
        "total_size_bytes": total_size_bytes,
        "file_count": file_count,
        "missing_files": sorted(dict.fromkeys(missing_files)),
        "hash_mismatches": hash_mismatches,
        "errors": errors,
        "valid": valid,
        "contains_secrets": bool((manifest_payload or {}).get("contains_secrets")),
        "warning": BACKUP_WARNING if bool((manifest_payload or {}).get("contains_secrets")) else None,
        "manifest": manifest_payload,
    }


def inspect_backup_checkpoint(
    path: str | Path,
    *,
    settings: Settings | None = None,
    passphrase: str | None = None,
) -> dict[str, object]:
    try:
        with _materialized_checkpoint(path, settings=settings, passphrase=passphrase) as (
            checkpoint_dir,
            storage_metadata,
        ):
            inspection = _inspect_plaintext_checkpoint_dir(checkpoint_dir)
            archive_path = storage_metadata.get("archive_path")
            if archive_path:
                inspection["checkpoint_id"] = Path(str(archive_path)).name
                inspection["backup_path"] = str(archive_path)
                inspection["manifest_path"] = f"{archive_path}:manifest.json"
            inspection.update(storage_metadata)
            return inspection
    except (ValueError, BackupEncryptionError) as exc:
        requested_path = Path(path).expanduser().resolve()
        header = (
            inspect_encrypted_backup_file_header(requested_path)
            if requested_path.is_file() and requested_path.suffix == ".enc"
            else {"encrypted": False, "key_source": None}
        )
        return {
            "checkpoint_id": requested_path.name,
            "backup_path": str(requested_path),
            "manifest_path": str(requested_path),
            "manifest_exists": False,
            "db_snapshot_exists": False,
            "db_integrity_check_result": "unavailable",
            "files_verified": 0,
            "missing_files": [],
            "hash_mismatches": [],
            "errors": [str(exc)],
            "error_code": getattr(exc, "code", "backup_corrupt"),
            "valid": False,
            "contains_secrets": True,
            "warning": BACKUP_WARNING,
            "manifest": None,
            "storage_kind": "encrypted_archive" if requested_path.suffix == ".enc" else "unknown_file",
            "encrypted": bool(header.get("encrypted")),
            "key_source": header.get("key_source"),
            "archive_path": str(requested_path),
        }


def resolve_backup_checkpoint_path(
    settings: Settings,
    checkpoint_id: str,
    *,
    backups_dir: str | Path | None = None,
) -> Path:
    del settings
    normalized_id = str(checkpoint_id or "").strip()
    if not normalized_id:
        raise ValueError("Checkpoint id is required.")
    if normalized_id in {".", ".."}:
        raise ValueError("Checkpoint id must be a checkpoint directory name.")
    if "/" in normalized_id or "\\" in normalized_id:
        raise ValueError("Checkpoint id must not contain path separators.")
    if Path(normalized_id).name != normalized_id:
        raise ValueError("Checkpoint id must be a directory basename only.")

    resolved_backups_dir = _resolve_backups_dir(backups_dir)
    candidate = (resolved_backups_dir / normalized_id).resolve()
    try:
        candidate.relative_to(resolved_backups_dir)
    except ValueError as exc:
        raise ValueError("Checkpoint id must resolve under the backup directory.") from exc

    if candidate.is_file() and (
        candidate.name.endswith(".tar.gz.enc") or candidate.name.endswith(".tar.gz")
    ):
        return candidate
    if not candidate.is_dir() or not (candidate / "manifest.json").is_file():
        raise FileNotFoundError(f"Unknown checkpoint: {normalized_id}")
    return candidate


def summarize_backup_checkpoint(
    path: str | Path,
    *,
    settings: Settings | None = None,
    passphrase: str | None = None,
) -> dict[str, object]:
    requested_path = Path(path).expanduser().resolve()
    inspection = inspect_backup_checkpoint(requested_path, settings=settings, passphrase=passphrase)
    manifest = inspection.get("manifest") or {}
    if requested_path.is_dir():
        total_size_bytes, file_count = _directory_file_stats(requested_path)
    elif requested_path.is_file():
        total_size_bytes, file_count = int(requested_path.stat().st_size), 1
    else:
        total_size_bytes, file_count = 0, 0
    errors = _collect_inspection_errors(inspection)
    return {
        "checkpoint_id": requested_path.name,
        "path": str(requested_path),
        "created_at_utc": manifest.get("created_at_utc"),
        "backup_format_version": manifest.get("backup_format_version"),
        "backup_trigger": manifest.get("backup_trigger"),
        "auto_checkpoint": bool(manifest.get("auto_checkpoint") is True),
        "contains_secrets": bool(manifest.get("contains_secrets")),
        "backup_storage": inspection.get("storage_kind") or manifest.get("backup_storage"),
        "backup_encrypted": bool(inspection.get("encrypted") or manifest.get("backup_encrypted")),
        "backup_key_source": inspection.get("key_source") or manifest.get("backup_key_source"),
        "db_integrity_check_result": inspection.get("db_integrity_check_result"),
        "total_size_bytes": total_size_bytes,
        "file_count": file_count,
        "git_commit": manifest.get("git_commit"),
        "git_dirty": manifest.get("git_dirty"),
        "inspect_valid": bool(inspection.get("valid")),
        "inspect_error": "; ".join(errors) if errors else None,
    }


def build_restore_dry_run_plan(
    settings: Settings,
    checkpoint_path: str | Path,
    *,
    passphrase: str | None = None,
) -> dict[str, object]:
    with _materialized_checkpoint(checkpoint_path, settings=settings, passphrase=passphrase) as (
        checkpoint_dir,
        storage_metadata,
    ):
        inspection = _inspect_plaintext_checkpoint_dir(checkpoint_dir)
        manifest = inspection.get("manifest") or {}
        checkpoint_display_path = str(storage_metadata.get("archive_path") or checkpoint_dir)
        checkpoint_id = Path(checkpoint_display_path).name

        source_metadata = {
            "source_db_path": manifest.get("source_db_path"),
            "source_project_root": manifest.get("project_root"),
            "source_public_app_origin": manifest.get("public_app_origin") or "",
            "source_backend_origin": manifest.get("backend_origin") or "",
            "source_media_root_path": manifest.get("media_root_path"),
            "source_transcode_dir": manifest.get("transcode_dir"),
        }
        current_metadata = {
            "current_db_path": _safe_resolved_path(settings.db_path),
            "current_project_root": _safe_resolved_path(PROJECT_ROOT),
            "current_public_app_origin": settings.public_app_origin,
            "current_backend_origin": settings.backend_origin,
            "current_media_root_path": _safe_resolved_path(settings.media_root),
            "current_transcode_dir": _safe_resolved_path(settings.transcode_dir),
        }
        comparison = {
            "same_project_root": source_metadata["source_project_root"] == current_metadata["current_project_root"],
            "same_db_path": source_metadata["source_db_path"] == current_metadata["current_db_path"],
            "same_public_app_origin": source_metadata["source_public_app_origin"] == current_metadata["current_public_app_origin"],
            "same_backend_origin": source_metadata["source_backend_origin"] == current_metadata["current_backend_origin"],
            "same_media_root_path": source_metadata["source_media_root_path"] == current_metadata["current_media_root_path"],
        }

        restore_scope = {
            "db_snapshot_available": bool(inspection.get("db_snapshot_exists")),
            "env_snapshot_available": (checkpoint_dir / "deploy" / "env" / "elvern.env").is_file(),
            "helper_releases_available": (checkpoint_dir / "backend" / "data" / "helper_releases").exists(),
            "assistant_uploads_available": (checkpoint_dir / "backend" / "data" / "assistant_uploads").exists(),
            "media_files_included": False,
            "poster_files_included": False,
            "transcodes_included": False,
            "playback_diagnostics_included": False,
        }

    blocking_errors: list[str] = []
    blocking_errors.extend(str(value) for value in inspection.get("errors") or [])
    if inspection.get("missing_files"):
        blocking_errors.append(
            "Missing checkpoint files: " + ", ".join(str(value) for value in inspection["missing_files"])
        )
    if inspection.get("hash_mismatches"):
        mismatched_paths = [
            str(entry.get("relative_path") or "")
            for entry in inspection.get("hash_mismatches") or []
            if str(entry.get("relative_path") or "")
        ]
        blocking_errors.append(
            "Checkpoint file hash mismatches: " + ", ".join(mismatched_paths)
        )
    if inspection.get("db_integrity_check_result") != "ok":
        blocking_errors.append(
            f"Backup database integrity_check result is {inspection.get('db_integrity_check_result')!r}"
        )

    warnings: list[str] = []
    if not comparison["same_project_root"]:
        warnings.append("Checkpoint project_root differs from the current project root.")
    if not comparison["same_db_path"]:
        warnings.append("Checkpoint source_db_path differs from the current live db_path.")
    if not comparison["same_public_app_origin"]:
        warnings.append("Checkpoint public_app_origin differs from the current live public_app_origin.")
    if not comparison["same_backend_origin"]:
        warnings.append("Checkpoint backend_origin differs from the current live backend_origin.")
    if not comparison["same_media_root_path"]:
        warnings.append("Checkpoint media_root_path differs from the current live media_root_path.")
    if not restore_scope["env_snapshot_available"]:
        warnings.append("Checkpoint does not include deploy/env/elvern.env.")
    if not restore_scope["helper_releases_available"]:
        warnings.append("Checkpoint does not include backend/data/helper_releases.")
    if not restore_scope["assistant_uploads_available"]:
        warnings.append("Checkpoint does not include backend/data/assistant_uploads.")

    checkpoint_valid = bool(inspection.get("valid")) and not blocking_errors

    return {
        "restore_plan_format_version": RESTORE_PLAN_FORMAT_VERSION,
        "checkpoint_id": checkpoint_id,
        "checkpoint_path": checkpoint_display_path,
        "checkpoint_created_at_utc": manifest.get("created_at_utc"),
        "checkpoint_valid": checkpoint_valid,
        "blocking_errors": blocking_errors,
        "warnings": warnings,
        "contains_secrets": bool(manifest.get("contains_secrets")),
        "warning": BACKUP_WARNING if bool(manifest.get("contains_secrets")) else None,
        "backup_trigger": manifest.get("backup_trigger"),
        "auto_checkpoint": bool(manifest.get("auto_checkpoint") is True),
        "source_metadata": source_metadata,
        "current_metadata": current_metadata,
        "comparison": comparison,
        "restore_scope": restore_scope,
        "not_included": [
            "media library files",
            "poster library files",
            "transcodes/cache",
            "virtualenv",
            "frontend node_modules/dist",
            "logs",
        ],
        "required_pre_restore_steps": [
            "Stop backend and frontend services before any manual recovery work.",
            "Create a fresh safety backup checkpoint of the current live state.",
            "Verify the target runtime paths before touching db/env/helper/upload files.",
            "Confirm secrets handling before moving any checkpoint files.",
        ],
        "manual_restore_outline": [
            "Review this plan and resolve any blocking_errors first.",
            "Stop Elvern services and make a fresh safety backup of the current live state.",
            "Decide which checkpoint components you intend to recover: db snapshot, env snapshot, helper releases, assistant uploads.",
            "Verify the target live paths and secret-handling requirements before replacing any runtime files.",
            "Perform any recovery manually using the verified checkpoint files only after explicit operator confirmation.",
            "Start Elvern again and verify login, library state, and settings after the manual recovery.",
        ],
        "verification": {
            "manifest_exists": bool(inspection.get("manifest_exists")),
            "db_snapshot_exists": bool(inspection.get("db_snapshot_exists")),
            "db_integrity_check_result": inspection.get("db_integrity_check_result"),
            "files_verified": inspection.get("files_verified"),
            "missing_files": list(inspection.get("missing_files") or []),
            "hash_mismatches": list(inspection.get("hash_mismatches") or []),
        },
    }


def build_safe_backup_preview(
    settings: Settings,
    checkpoint_path: str | Path,
    *,
    passphrase: str | None = None,
) -> dict[str, object]:
    with _materialized_checkpoint(checkpoint_path, settings=settings, passphrase=passphrase) as (
        checkpoint_dir,
        storage_metadata,
    ):
        inspection = _inspect_plaintext_checkpoint_dir(checkpoint_dir)
        manifest = inspection.get("manifest") or {}
        db_snapshot = checkpoint_dir / str(manifest.get("db_snapshot_filename") or "elvern.db")
        backup_counts = {"users": 0, "enabled_admins": 0, "media_items": 0}
        backup_schema_version = None
        if db_snapshot.is_file():
            connection = sqlite3.connect(db_snapshot)
            try:
                backup_counts = {
                    "users": int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]),
                    "enabled_admins": int(
                        connection.execute(
                            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND enabled = 1"
                        ).fetchone()[0]
                    ),
                    "media_items": int(connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0]),
                }
                backup_schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            finally:
                connection.close()
        helper_releases_available = (checkpoint_dir / "backend" / "data" / "helper_releases").exists()
        assistant_uploads_available = (checkpoint_dir / "backend" / "data" / "assistant_uploads").exists()

    with get_connection(settings) as connection:
        current_counts = {
            "users": int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]),
            "enabled_admins": int(
                connection.execute(
                    "SELECT COUNT(*) FROM users WHERE role = 'admin' AND enabled = 1"
                ).fetchone()[0]
            ),
            "media_items": int(connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0]),
        }
        current_schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])

    blocking_errors = _collect_inspection_errors(inspection)
    warnings: list[str] = []
    if manifest.get("media_root_path") != str(settings.media_root.resolve()):
        warnings.append("The media library reference differs from the current server.")
    if not helper_releases_available:
        warnings.append("Desktop helper releases are not included in this checkpoint.")
    if not assistant_uploads_available:
        warnings.append("Assistant uploads are not included in this checkpoint.")
    return {
        "preview_only": True,
        "checkpoint_id": Path(str(storage_metadata.get("archive_path") or checkpoint_path)).name,
        "checkpoint_valid": bool(inspection.get("valid")) and not blocking_errors,
        "database_integrity": inspection.get("db_integrity_check_result"),
        "schema_compatible": backup_schema_version == current_schema_version,
        "backup_counts": backup_counts,
        "current_counts": current_counts,
        "settings_matches": {
            "project_root": manifest.get("project_root") == str(PROJECT_ROOT.resolve()),
            "media_library_reference": manifest.get("media_root_path") == str(settings.media_root.resolve()),
            "public_origin": manifest.get("public_app_origin") == settings.public_app_origin,
        },
        "helper_releases_available": helper_releases_available,
        "assistant_uploads_available": assistant_uploads_available,
        "blocking_errors": blocking_errors,
        "warnings": warnings,
    }


def list_backup_checkpoints(
    settings: Settings,
    backups_dir: str | Path | None = None,
) -> list[dict[str, object]]:
    resolved_backups_dir = _resolve_backups_dir(backups_dir)
    if not resolved_backups_dir.exists():
        return []

    entries: list[dict[str, object]] = []
    for candidate in sorted(
        (
            item
            for item in resolved_backups_dir.iterdir()
            if (item.is_dir() and (item / "manifest.json").is_file())
            or item.name.endswith(".tar.gz.enc")
            or item.name.endswith(".tar.gz")
        ),
        key=lambda candidate: candidate.name,
        reverse=True,
    ):
        entries.append(summarize_backup_checkpoint(candidate, settings=settings))

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
        auto_flag = entry.get("auto_checkpoint") if entry.get("inspect_valid") else None
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
            if checkpoint_path.is_dir():
                shutil.rmtree(checkpoint_path)
            else:
                checkpoint_path.unlink()
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
