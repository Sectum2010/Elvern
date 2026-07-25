#!/usr/bin/env python3
"""Inspect or safely migrate the Desktop Helper runtime release authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import BinaryIO


REPO_ROOT = Path(__file__).resolve().parents[1]
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
    manifest_origin = payload.get("bound_origin_sha256")
    if expected_origin is not None and manifest_origin != expected_origin:
        raise AuthorityOriginMismatch(
            "Release manifest origin identity is incompatible with this server."
        )
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
        if expected_origin:
            command.extend(("--expected-origin-sha256", expected_origin))
        result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AuthorityError("Strict package validation failed.")
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
        _content, metadata = _read_regular_file_at(directory, filename)
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
            "origin_check": "not_checked" if expected_origin is None else "incompatible",
            "origin_compatible": None if expected_origin is None else False,
        }, indent=2))
        return 2
    try:
        payload = _validate(directory, expected_origin=expected_origin)
        mutable_files = _mutable_authority_files(directory, payload)
    except FileNotFoundError:
        print(json.dumps({
            "runtime_dir": str(directory),
            "manifest_state": "absent",
            "origin_check": "not_checked" if expected_origin is None else "incompatible",
            "origin_compatible": None if expected_origin is None else False,
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
        return 3
    except AuthorityError as exc:
        print(json.dumps({
            "runtime_dir": str(directory),
            "manifest_state": "invalid",
            "origin_check": "not_checked" if expected_origin is None else "incompatible",
            "origin_compatible": None if expected_origin is None else False,
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
        if (
            _path_entry_exists(destination / MANIFEST_NAME)
            and not all_files_present
        ):
            raise AuthorityError(
                "Runtime release destination has an incomplete active manifest."
            )
        if all_files_present:
            if not apply:
                if repairs:
                    print(
                        f"Dry run: would repair immutable mode on {len(repairs)} "
                        f"runtime authority files in {destination}."
                    )
                else:
                    print("Runtime release authority is already identical.")
                return 0
            if not repairs:
                print("Runtime release authority is already identical.")
                return 0
            original_modes: dict[Path, int] = {}
            try:
                for path in repairs:
                    original_modes[path] = stat.S_IMODE(os.lstat(path).st_mode)
                    os.chmod(path, IMMUTABLE_FILE_MODE, follow_symlinks=False)
                    with _open_regular_path(path) as handle:
                        os.fsync(handle.fileno())
                _fsync_directory(destination)
                _validate(destination, expected_origin=expected_origin)
                if _mutable_authority_files(destination, payload):
                    raise AuthorityError("Runtime release immutable mode repair failed.")
            except BaseException:
                for path, mode in original_modes.items():
                    if _path_entry_exists(path) and not path.is_symlink():
                        os.chmod(path, mode, follow_symlinks=False)
                _fsync_directory(destination)
                raise
            print(f"Repaired immutable mode on {len(repairs)} runtime authority files.")
            return 0

    if not apply:
        print(
            f"Dry run: validated {len(source_files)} packages for migration "
            f"into {destination}."
        )
        return 0

    destination.mkdir(mode=0o755, parents=False, exist_ok=True)
    created: list[Path] = []
    try:
        for index, source_file in enumerate(all_source_files):
            final_path = destination / source_file.name
            if _path_entry_exists(final_path):
                if _same_file(final_path, source_file):
                    continue
                if _same_content(final_path, source_file):
                    os.chmod(final_path, IMMUTABLE_FILE_MODE, follow_symlinks=False)
                    with _open_regular_path(final_path) as handle:
                        os.fsync(handle.fileno())
                    _fsync_directory(destination)
                    continue
                raise AuthorityError("Runtime release destination artifact conflicts.")
            _copy_verified_file(source_file, destination, index)
            created.append(final_path)
        _validate(destination, expected_origin=expected_origin)
        if _mutable_authority_files(destination, payload):
            raise AuthorityError("Migrated runtime authority is mutable.")
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        _fsync_directory(destination)
        raise
    print(f"Migrated {len(source_files)} immutable packages; manifest activated last.")
    return 0


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
