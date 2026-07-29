#!/usr/bin/env python3
"""Inspect or safely migrate the Desktop Helper runtime release authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, NamedTuple


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from elvern_shared.desktop_helper_package_contract import (  # noqa: E402
    AUTHORITY_MUTATION_LOCK_SCHEMA,
    authority_mutation_lock_basename,
    authority_runtime_path_sha256,
)


VALIDATOR = REPO_ROOT / "clients/desktop-vlc-opener/scripts/validate-package.py"
MANIFEST_NAME = "release-manifest.json"
EXPECTED_TARGETS = ("windows-x64", "macos-dual-arch", "linux-universal")
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_STABLE_READ_ATTEMPTS = 3
IMMUTABLE_FILE_MODE = 0o444


class AuthorityError(RuntimeError):
    pass


class AuthorityOriginMismatch(AuthorityError):
    pass


class AuthorityInterrupted(AuthorityError):
    pass


class _ModeRepair(NamedTuple):
    path: Path
    original_mode: int
    fingerprint: tuple[int, int, int, int]


def _content_fingerprint(value: os.stat_result) -> tuple[int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _safe_stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _absolute_no_symlink(path: str, *, allow_missing_leaf: bool) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise AuthorityError("Release directory paths must be absolute.")
    if any(ord(character) < 32 or ord(character) == 127 for character in path):
        raise AuthorityError("Release directory paths cannot contain control characters.")
    normalized = Path(os.path.abspath(candidate))
    parts = normalized.parts[1:]
    cursor = Path(normalized.anchor)
    for index, component in enumerate(parts):
        cursor /= component
        try:
            metadata = os.lstat(cursor)
        except FileNotFoundError:
            if allow_missing_leaf and index == len(parts) - 1:
                return normalized
            raise AuthorityError(
                "Release directory parent does not exist."
                if allow_missing_leaf
                else "Release directory does not exist."
            ) from None
        if stat.S_ISLNK(metadata.st_mode):
            raise AuthorityError("Release directory paths cannot contain symlinks.")
        if not stat.S_ISDIR(metadata.st_mode):
            raise AuthorityError("Release directory paths must contain only directories.")
    return normalized


def _open_directory_fd(directory: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(directory.anchor, flags)
    try:
        for component in directory.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                raise AuthorityError("Release directory path is not a safe directory.")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_regular_file_at(
    directory: Path,
    filename: str,
    *,
    max_bytes: int | None = None,
) -> tuple[bytes, os.stat_result]:
    if not filename or "/" in filename or filename in {".", ".."}:
        raise AuthorityError("Release authority filename is unsafe.")
    directory_fd = _open_directory_fd(directory)
    try:
        for attempt in range(MAX_STABLE_READ_ATTEMPTS):
            try:
                descriptor = os.open(
                    filename,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                raise
            except OSError as exc:
                raise AuthorityError("Release authority file could not be opened safely.") from exc
            try:
                before = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode):
                    raise AuthorityError("Release authority file is not a safe regular file.")
                if max_bytes is not None and before.st_size > max_bytes:
                    raise AuthorityError("Release authority file is too large.")
                chunks: list[bytes] = []
                remaining = None if max_bytes is None else max_bytes + 1
                while remaining is None or remaining > 0:
                    chunk = os.read(
                        descriptor,
                        1024 * 1024 if remaining is None else min(1024 * 1024, remaining),
                    )
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if remaining is not None:
                        remaining -= len(chunk)
                content = b"".join(chunks)
                if max_bytes is not None and len(content) > max_bytes:
                    raise AuthorityError("Release authority file is too large.")
                after = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            try:
                current = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
            except OSError:
                current = None
            if (
                _safe_stat_identity(before) == _safe_stat_identity(after)
                and current is not None
                and current.st_dev == after.st_dev
                and current.st_ino == after.st_ino
            ):
                return content, after
            if attempt + 1 == MAX_STABLE_READ_ATTEMPTS:
                break
        raise AuthorityError("Release authority file changed while it was being read.")
    finally:
        os.close(directory_fd)


def _stat_regular_file_at(directory: Path, filename: str) -> os.stat_result:
    if not filename or "/" in filename or filename in {".", ".."}:
        raise AuthorityError("Release authority filename is unsafe.")
    directory_fd = _open_directory_fd(directory)
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                filename,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
        except OSError as exc:
            raise AuthorityError(
                "Release authority file could not be opened safely."
            ) from exc
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise AuthorityError("Release authority file is not a safe regular file.")
        current = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        if (
            current.st_dev != metadata.st_dev
            or current.st_ino != metadata.st_ino
            or not stat.S_ISREG(current.st_mode)
        ):
            raise AuthorityError(
                "Release authority file changed while it was being inspected."
            )
        return metadata
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


def _load_manifest(directory: Path) -> tuple[dict[str, object], bytes]:
    content, _metadata = _read_regular_file_at(
        directory,
        MANIFEST_NAME,
        max_bytes=MAX_MANIFEST_BYTES,
    )
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthorityError("Release manifest is invalid.") from exc
    if not isinstance(payload, dict):
        raise AuthorityError("Release manifest root is invalid.")
    return payload, content


def _validate(
    directory: Path,
    *,
    expected_origin: str | None,
) -> dict[str, object]:
    payload, manifest_bytes = _load_manifest(directory)
    (REPO_ROOT / "tmp").mkdir(mode=0o755, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".desktop-helper-authority-",
        dir=REPO_ROOT / "tmp",
    ) as temporary_directory:
        stable_manifest = Path(temporary_directory) / MANIFEST_NAME
        stable_manifest.write_bytes(manifest_bytes)
        stable_manifest.chmod(0o400)
        command = [
            sys.executable,
            str(VALIDATOR),
            "--manifest",
            str(stable_manifest),
            "--artifacts-dir",
            str(directory),
        ]
        for target in EXPECTED_TARGETS:
            command.extend(("--expected-package-target", target))
        result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AuthorityError("Strict package validation failed.")
    manifest_origin = payload.get("bound_origin_sha256")
    if expected_origin is not None and manifest_origin != expected_origin:
        raise AuthorityOriginMismatch(
            "Release manifest origin identity is incompatible with this server."
        )
    return payload


def _package_names(payload: dict[str, object]) -> list[str]:
    packages = payload.get("packages")
    if not isinstance(packages, list):
        raise AuthorityError("Release package list is invalid.")
    names: list[str] = []
    for package in packages:
        if not isinstance(package, dict):
            raise AuthorityError("Release package entry is invalid.")
        filename = package.get("filename")
        if (
            not isinstance(filename, str)
            or not filename
            or "/" in filename
            or filename in {".", ".."}
        ):
            raise AuthorityError("Release package filename is unsafe.")
        names.append(filename)
    return names


def _mutable_authority_files(
    directory: Path,
    payload: dict[str, object],
) -> list[str]:
    mutable: list[str] = []
    for filename in (*_package_names(payload), MANIFEST_NAME):
        metadata = _stat_regular_file_at(directory, filename)
        if stat.S_IMODE(metadata.st_mode) != IMMUTABLE_FILE_MODE:
            mutable.append(filename)
    return mutable


def _summary(
    directory: Path,
    payload: dict[str, object],
    *,
    expected_origin: str | None,
    mutable_files: list[str],
) -> dict[str, object]:
    packages = payload["packages"]
    return {
        "runtime_dir": str(directory),
        "manifest_state": "valid_but_mutable" if mutable_files else "valid",
        "helper_version": payload.get("helper_version"),
        "package_targets": [
            {
                "package_target": package.get("package_target"),
                "filename": package.get("filename"),
                "size_bytes": package.get("size_bytes"),
                "sha256": package.get("sha256"),
            }
            for package in packages
            if isinstance(package, dict)
        ],
        "origin_check": "compatible" if expected_origin is not None else "not_checked",
        "origin_compatible": True if expected_origin is not None else None,
        "mutable_file_count": len(mutable_files),
        "mutable_files": mutable_files,
    }


def inspect_authority(directory: Path, expected_origin: str | None) -> int:
    if not directory.exists():
        print(json.dumps({
            "runtime_dir": str(directory),
            "manifest_state": "absent",
            "origin_check": "not_checked",
            "origin_compatible": None,
        }, indent=2))
        return 2
    try:
        payload = _validate(directory, expected_origin=expected_origin)
        mutable_files = _mutable_authority_files(directory, payload)
    except FileNotFoundError:
        print(json.dumps({
            "runtime_dir": str(directory),
            "manifest_state": "absent",
            "origin_check": "not_checked",
            "origin_compatible": None,
        }, indent=2))
        return 2
    except AuthorityOriginMismatch as exc:
        print(json.dumps({
            "runtime_dir": str(directory),
            "manifest_state": "invalid",
            "origin_check": "incompatible",
            "origin_compatible": False,
            "error": str(exc),
        }, indent=2))
        return 5
    except AuthorityError as exc:
        print(json.dumps({
            "runtime_dir": str(directory),
            "manifest_state": "invalid",
            "origin_check": "unknown" if expected_origin is not None else "not_checked",
            "origin_compatible": None,
            "error": str(exc),
        }, indent=2))
        return 3
    print(json.dumps(_summary(
        directory,
        payload,
        expected_origin=expected_origin,
        mutable_files=mutable_files,
    ), indent=2))
    return 4 if mutable_files else 0


def _fsync_directory(path: Path) -> None:
    descriptor = _open_directory_fd(path)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _hash_handle(handle: BinaryIO) -> tuple[str, os.stat_result]:
    before = os.fstat(handle.fileno())
    if not stat.S_ISREG(before.st_mode):
        raise AuthorityError("Release authority file is not a safe regular file.")
    digest = hashlib.sha256()
    while chunk := handle.read(1024 * 1024):
        digest.update(chunk)
    after = os.fstat(handle.fileno())
    if _safe_stat_identity(before) != _safe_stat_identity(after):
        raise AuthorityError("Release authority file changed while it was being read.")
    return digest.hexdigest(), after


def _open_regular_path(path: Path) -> BinaryIO:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise AuthorityError("Release authority file could not be opened safely.") from exc
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise AuthorityError("Release authority file is not a safe regular file.")
    return os.fdopen(descriptor, "rb")


def _path_entry_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _same_content(path: Path, source: Path) -> bool:
    if not _path_entry_exists(path):
        return False
    with _open_regular_path(path) as destination_handle:
        destination_hash, destination_metadata = _hash_handle(destination_handle)
    with _open_regular_path(source) as source_handle:
        source_hash, source_metadata = _hash_handle(source_handle)
    return (
        destination_metadata.st_size == source_metadata.st_size
        and destination_hash == source_hash
    )


def _same_file(path: Path, source: Path) -> bool:
    if not _same_content(path, source):
        return False
    metadata = os.lstat(path)
    return stat.S_ISREG(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) == IMMUTABLE_FILE_MODE


def _copy_verified_file(source_file: Path, destination: Path, index: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{source_file.name}.new.",
        dir=destination,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output, _open_regular_path(source_file) as source_handle:
            shutil.copyfileobj(source_handle, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if not _same_content(temporary_path, source_file):
            raise AuthorityError("Copied release artifact failed verification.")
        os.chmod(temporary_path, IMMUTABLE_FILE_MODE)
        with _open_regular_path(temporary_path) as verified:
            os.fsync(verified.fileno())
        if os.environ.get("ELVERN_RUNTIME_MIGRATION_TEST_FAIL_AT") == str(index):
            raise AuthorityError("Injected migration failure.")
        os.replace(temporary_path, destination / source_file.name)
        _fsync_directory(destination)
    finally:
        temporary_path.unlink(missing_ok=True)


class _AuthorityMutationLock:
    def __init__(self, destination: Path) -> None:
        self.destination = destination
        self.parent = destination.parent
        self.runtime_path_sha256 = authority_runtime_path_sha256(str(destination))
        self.basename = authority_mutation_lock_basename(str(destination))
        self.path = self.parent / self.basename
        self.nonce = secrets.token_hex(24)
        self._identity: tuple[int, int] | None = None

    def acquire(self) -> None:
        try:
            os.mkdir(self.path, 0o700)
        except FileExistsError as exc:
            raise AuthorityError(
                "Another Desktop Helper authority mutation is active, or an "
                "unknown stale lock remains. Confirm no publisher or migration "
                "is running before removing the hashed lock manually."
            ) from exc
        metadata = os.lstat(self.path)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise AuthorityError("Desktop Helper authority mutation lock is unsafe.")
        self._identity = (metadata.st_dev, metadata.st_ino)
        owner = self.path / "owner"
        payload = (
            f"schema={AUTHORITY_MUTATION_LOCK_SCHEMA}\n"
            f"pid={os.getpid()}\n"
            f"started_at={datetime.now(timezone.utc).isoformat()}\n"
            f"transaction_nonce={self.nonce}\n"
            f"runtime_path_sha256={self.runtime_path_sha256}\n"
        ).encode("ascii")
        try:
            descriptor = os.open(
                owner,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                os.write(descriptor, payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            _fsync_directory(self.path)
            _fsync_directory(self.parent)
        except BaseException:
            try:
                owner.unlink(missing_ok=True)
                self.path.rmdir()
                _fsync_directory(self.parent)
            except OSError:
                pass
            self._identity = None
            raise

    def is_owned(self) -> bool:
        if self._identity is None:
            return False
        try:
            lock_metadata = os.lstat(self.path)
            if (
                stat.S_ISLNK(lock_metadata.st_mode)
                or not stat.S_ISDIR(lock_metadata.st_mode)
                or (lock_metadata.st_dev, lock_metadata.st_ino) != self._identity
            ):
                return False
            content, owner_metadata = _read_regular_file_at(
                self.path,
                "owner",
                max_bytes=4096,
            )
            if stat.S_IMODE(owner_metadata.st_mode) != 0o600:
                return False
            fields: dict[str, str] = {}
            for line in content.decode("ascii").splitlines():
                key, separator, value = line.partition("=")
                if not separator or key in fields:
                    return False
                fields[key] = value
            return fields == {
                "schema": AUTHORITY_MUTATION_LOCK_SCHEMA,
                "pid": str(os.getpid()),
                "started_at": fields.get("started_at", ""),
                "transaction_nonce": self.nonce,
                "runtime_path_sha256": self.runtime_path_sha256,
            } and bool(fields["started_at"])
        except (AuthorityError, OSError, UnicodeDecodeError, ValueError):
            return False

    def release(self) -> bool:
        if not self.is_owned():
            return False
        try:
            os.unlink(self.path / "owner")
            os.rmdir(self.path)
            _fsync_directory(self.parent)
        except OSError:
            return False
        self._identity = None
        return True


@contextmanager
def _authority_mutation_lock(destination: Path):
    lock = _AuthorityMutationLock(destination)
    lock.acquire()
    previous_handlers: dict[int, object] = {}

    def interrupt(signum, _frame):
        raise AuthorityInterrupted(
            f"Desktop Helper authority mutation interrupted by signal {signum}."
        )

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, interrupt)
    try:
        yield lock
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if not lock.release():
            print(
                "Warning: Desktop Helper authority lock was not removed because "
                "transaction ownership could not be verified.",
                file=sys.stderr,
            )


def _record_mode_repair(path: Path) -> _ModeRepair:
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise AuthorityError("Runtime release mode repair target is unsafe.")
    return _ModeRepair(
        path=path,
        original_mode=stat.S_IMODE(metadata.st_mode),
        fingerprint=_content_fingerprint(metadata),
    )


def _restore_modes(
    repairs: list[_ModeRepair],
    destination: Path,
    lock: _AuthorityMutationLock,
) -> list[str]:
    failures: list[str] = []
    if not lock.is_owned():
        return ["authority lock ownership changed before rollback"]
    for repair in reversed(repairs):
        try:
            current = os.lstat(repair.path)
            if (
                stat.S_ISLNK(current.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or _content_fingerprint(current) != repair.fingerprint
            ):
                failures.append(f"{repair.path.name}: fingerprint changed")
                continue
            os.chmod(repair.path, repair.original_mode, follow_symlinks=False)
            with _open_regular_path(repair.path) as handle:
                os.fsync(handle.fileno())
        except (AuthorityError, OSError) as exc:
            failures.append(f"{repair.path.name}: {type(exc).__name__}")
    try:
        _fsync_directory(destination)
    except (AuthorityError, OSError) as exc:
        failures.append(f"directory fsync: {type(exc).__name__}")
    return failures


def _repair_mode(path: Path, repairs: list[_ModeRepair]) -> None:
    repair = _record_mode_repair(path)
    repairs.append(repair)
    os.chmod(path, IMMUTABLE_FILE_MODE, follow_symlinks=False)
    with _open_regular_path(path) as handle:
        os.fsync(handle.fileno())


def _remove_created_destination_after_rollback(
    destination: Path,
    destination_identity: tuple[int, int],
    lock: _AuthorityMutationLock,
) -> list[str]:
    if not lock.is_owned():
        return ["authority lock ownership changed before destination cleanup"]
    try:
        metadata = os.lstat(destination)
    except OSError as exc:
        return [f"destination stat: {type(exc).__name__}"]
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != destination_identity
    ):
        return ["destination identity changed before cleanup"]
    if _path_entry_exists(destination / MANIFEST_NAME):
        return ["destination manifest remained during cleanup"]
    try:
        if any(destination.iterdir()):
            return ["destination is not empty after cleanup"]
        destination.rmdir()
        _fsync_directory(destination.parent)
    except (AuthorityError, OSError) as exc:
        return [f"destination cleanup: {type(exc).__name__}"]
    return []


def _migrate_authority_locked(
    source: Path,
    destination: Path,
    expected_origin: str,
    payload: dict[str, object],
    all_source_files: list[Path],
    source_files: list[Path],
    lock: _AuthorityMutationLock,
) -> int:
    package_names = _package_names(payload)
    repairs: list[Path] = []
    if destination.exists():
        destination_metadata = os.lstat(destination)
        if stat.S_ISLNK(destination_metadata.st_mode) or not stat.S_ISDIR(destination_metadata.st_mode):
            raise AuthorityError("Runtime release destination is unsafe.")
        entries = tuple(destination.iterdir())
        allowed = set(package_names) | {MANIFEST_NAME}
        if any(entry.name not in allowed for entry in entries):
            raise AuthorityError("Runtime release destination contains another authority.")
        for source_file in all_source_files:
            final_path = destination / source_file.name
            if _path_entry_exists(final_path):
                if not _same_content(final_path, source_file):
                    raise AuthorityError("Runtime release destination artifact conflicts.")
                if not _same_file(final_path, source_file):
                    repairs.append(final_path)
        all_files_present = all(
            _path_entry_exists(destination / item.name)
            for item in all_source_files
        )
        if _path_entry_exists(destination / MANIFEST_NAME) and not all_files_present:
            raise AuthorityError(
                "Runtime release destination has an incomplete active manifest."
            )
        if all_files_present:
            if not repairs:
                print("Runtime release authority is already identical.")
                return 0
            repaired_modes: list[_ModeRepair] = []
            try:
                for path in repairs:
                    _repair_mode(path, repaired_modes)
                _fsync_directory(destination)
                _validate(destination, expected_origin=expected_origin)
                if _mutable_authority_files(destination, payload):
                    raise AuthorityError("Runtime release immutable mode repair failed.")
            except BaseException as exc:
                rollback_failures = _restore_modes(repaired_modes, destination, lock)
                if rollback_failures:
                    raise AuthorityError(
                        f"{exc} Mode rollback could not be verified: "
                        + "; ".join(rollback_failures)
                    ) from exc
                raise
            print(f"Repaired immutable mode on {len(repairs)} runtime authority files.")
            return 0

    destination_created = False
    destination_identity: tuple[int, int] | None = None
    created: list[Path] = []
    repaired_modes: list[_ModeRepair] = []
    try:
        if not destination.exists():
            destination.mkdir(mode=0o755, parents=False, exist_ok=False)
            destination_created = True
            created_metadata = os.lstat(destination)
            if stat.S_ISLNK(created_metadata.st_mode) or not stat.S_ISDIR(created_metadata.st_mode):
                raise AuthorityError("Runtime release destination is unsafe.")
            destination_identity = (created_metadata.st_dev, created_metadata.st_ino)
            _fsync_directory(destination.parent)

        for index, source_file in enumerate(all_source_files):
            final_path = destination / source_file.name
            if _path_entry_exists(final_path):
                if _same_file(final_path, source_file):
                    continue
                if _same_content(final_path, source_file):
                    _repair_mode(final_path, repaired_modes)
                    _fsync_directory(destination)
                    continue
                raise AuthorityError("Runtime release destination artifact conflicts.")
            _copy_verified_file(source_file, destination, index)
            created.append(final_path)
        _validate(destination, expected_origin=expected_origin)
        if _mutable_authority_files(destination, payload):
            raise AuthorityError("Migrated runtime authority is mutable.")
    except BaseException as exc:
        cleanup_failures: list[str] = []
        if lock.is_owned():
            for path in reversed(created):
                try:
                    path.unlink(missing_ok=True)
                except OSError as cleanup_exc:
                    cleanup_failures.append(
                        f"{path.name}: {type(cleanup_exc).__name__}"
                    )
            cleanup_failures.extend(_restore_modes(repaired_modes, destination, lock))
            if destination_created:
                if destination_identity is None:
                    cleanup_failures.append(
                        "destination identity was not captured before cleanup"
                    )
                else:
                    cleanup_failures.extend(_remove_created_destination_after_rollback(
                        destination,
                        destination_identity,
                        lock,
                    ))
        else:
            cleanup_failures.append("authority lock ownership changed before cleanup")
        if cleanup_failures:
            raise AuthorityError(
                f"{exc} Migration rollback could not be verified: "
                + "; ".join(cleanup_failures)
            ) from exc
        raise
    print(f"Migrated {len(source_files)} immutable packages; manifest activated last.")
    return 0


def migrate_authority(
    source: Path,
    destination: Path,
    expected_origin: str,
    *,
    apply: bool,
) -> int:
    payload = _validate(source, expected_origin=expected_origin)
    package_names = _package_names(payload)
    source_files = [source / name for name in package_names]
    manifest_source = source / MANIFEST_NAME
    all_source_files = [*source_files, manifest_source]
    if not apply:
        repairs: list[Path] = []
        if destination.exists():
            destination_metadata = os.lstat(destination)
            if stat.S_ISLNK(destination_metadata.st_mode) or not stat.S_ISDIR(destination_metadata.st_mode):
                raise AuthorityError("Runtime release destination is unsafe.")
            entries = tuple(destination.iterdir())
            allowed = set(package_names) | {MANIFEST_NAME}
            if any(entry.name not in allowed for entry in entries):
                raise AuthorityError("Runtime release destination contains another authority.")
            for source_file in all_source_files:
                final_path = destination / source_file.name
                if _path_entry_exists(final_path):
                    if not _same_content(final_path, source_file):
                        raise AuthorityError(
                            "Runtime release destination artifact conflicts."
                        )
                    if not _same_file(final_path, source_file):
                        repairs.append(final_path)
            all_files_present = all(
                _path_entry_exists(destination / item.name)
                for item in all_source_files
            )
            if _path_entry_exists(destination / MANIFEST_NAME) and not all_files_present:
                raise AuthorityError(
                    "Runtime release destination has an incomplete active manifest."
                )
            if all_files_present:
                if repairs:
                    print(
                        f"Dry run: would repair immutable mode on {len(repairs)} "
                        f"runtime authority files in {destination}."
                    )
                else:
                    print("Runtime release authority is already identical.")
                return 0
        print(
            f"Dry run: validated {len(source_files)} packages for migration "
            f"into {destination}."
        )
        return 0
    with _authority_mutation_lock(destination) as lock:
        return _migrate_authority_locked(
            source,
            destination,
            expected_origin,
            payload,
            all_source_files,
            source_files,
            lock,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--runtime-dir", required=True)
    inspect_parser.add_argument("--expected-origin-sha256")
    migrate_parser = subparsers.add_parser("migrate")
    migrate_parser.add_argument("--source-dir", required=True)
    migrate_parser.add_argument("--runtime-dir", required=True)
    migrate_parser.add_argument("--expected-origin-sha256", required=True)
    migrate_parser.add_argument("--apply", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "inspect":
            runtime = _absolute_no_symlink(args.runtime_dir, allow_missing_leaf=True)
            return inspect_authority(runtime, args.expected_origin_sha256)
        source = _absolute_no_symlink(args.source_dir, allow_missing_leaf=False)
        runtime = _absolute_no_symlink(args.runtime_dir, allow_missing_leaf=True)
        return migrate_authority(
            source,
            runtime,
            args.expected_origin_sha256,
            apply=args.apply,
        )
    except AuthorityError as exc:
        print(f"Desktop Helper runtime authority error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
