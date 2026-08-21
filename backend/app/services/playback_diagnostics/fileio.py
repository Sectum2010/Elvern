from __future__ import annotations

import errno
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any, Iterator


DIRECTORY_MODE = 0o700
FILE_MODE = 0o600


class UnsafeDiagnosticsPathError(ValueError):
    """Raised when a diagnostics path escapes its trusted root."""


def _absolute_lexical_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(Path(path))))


def _relative_parts_beneath(root: Path, path: Path) -> tuple[str, ...]:
    root_path = _absolute_lexical_path(root)
    candidate = _absolute_lexical_path(path)
    try:
        relative = candidate.relative_to(root_path)
    except ValueError as exc:
        raise UnsafeDiagnosticsPathError("Diagnostics path escapes its trusted root") from exc
    parts = tuple(relative.parts)
    if any(part in {"", ".", ".."} or os.sep in part for part in parts):
        raise UnsafeDiagnosticsPathError("Diagnostics path has an unsafe component")
    return parts


def _open_directory_descriptor(path: Path) -> int:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise UnsafeDiagnosticsPathError(
                "Diagnostics directory path is unsafe"
            ) from exc
        raise
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise UnsafeDiagnosticsPathError("Diagnostics path is not a directory")
    return descriptor


def _open_directory_beneath(
    root: Path,
    path: Path,
    *,
    create: bool,
) -> int:
    root_path = _absolute_lexical_path(root)
    if create:
        root_path.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
    root_descriptor = _open_directory_descriptor(root_path)
    try:
        if create:
            os.fchmod(root_descriptor, DIRECTORY_MODE)
        current_descriptor = root_descriptor
        for part in _relative_parts_beneath(root_path, path):
            if create:
                try:
                    os.mkdir(part, DIRECTORY_MODE, dir_fd=current_descriptor)
                except FileExistsError:
                    pass
            try:
                next_descriptor = os.open(
                    part,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=current_descriptor,
                )
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise UnsafeDiagnosticsPathError(
                        "Diagnostics directory component is unsafe"
                    ) from exc
                raise
            metadata = os.fstat(next_descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(next_descriptor)
                raise UnsafeDiagnosticsPathError(
                    "Diagnostics path component is not a directory"
                )
            if create:
                os.fchmod(next_descriptor, DIRECTORY_MODE)
            if current_descriptor != root_descriptor:
                os.close(current_descriptor)
            current_descriptor = next_descriptor
        if current_descriptor == root_descriptor:
            root_descriptor = -1
        return current_descriptor
    except Exception:
        if 'current_descriptor' in locals() and current_descriptor != root_descriptor:
            os.close(current_descriptor)
        raise
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)


def ensure_private_directory(path: Path, *, trusted_root: Path | None = None) -> Path:
    path = Path(path)
    if trusted_root is not None:
        descriptor = _open_directory_beneath(trusted_root, path, create=True)
        os.close(descriptor)
        return path
    if path.exists() and path.is_symlink():
        raise UnsafeDiagnosticsPathError(f"Refusing diagnostics symlink: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
    if path.is_symlink() or not path.is_dir():
        raise UnsafeDiagnosticsPathError(f"Diagnostics directory is not safe: {path}")
    os.chmod(path, DIRECTORY_MODE)
    return path


def resolve_beneath(root: Path, *parts: str | Path) -> Path:
    root_path = _absolute_lexical_path(root)
    candidate = _absolute_lexical_path(
        root_path.joinpath(*(Path(part) for part in parts))
    )
    _relative_parts_beneath(root_path, candidate)
    return candidate


def assert_regular_nonsymlink(
    path: Path,
    *,
    trusted_root: Path | None = None,
) -> None:
    if trusted_root is not None:
        parent_descriptor, name = _open_parent_directory(
            path,
            trusted_root=trusted_root,
            create_parent=False,
        )
        try:
            try:
                metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                return
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise UnsafeDiagnosticsPathError(
                    "Diagnostics path has an unsafe inode"
                )
        finally:
            os.close(parent_descriptor)
        return
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise UnsafeDiagnosticsPathError(f"Diagnostics path has an unsafe inode: {path}")


def _open_parent_directory(
    path: Path,
    *,
    trusted_root: Path | None = None,
    create_parent: bool,
) -> tuple[int, str]:
    path = Path(path)
    name = path.name
    if name in {"", ".", ".."} or os.sep in name:
        raise UnsafeDiagnosticsPathError("Diagnostics filename is unsafe")
    parent = path.parent
    if trusted_root is not None:
        return (
            _open_directory_beneath(
                trusted_root,
                parent,
                create=create_parent,
            ),
            name,
        )
    if create_parent:
        parent = ensure_private_directory(parent)
    descriptor = _open_directory_descriptor(parent)
    return descriptor, name


def _assert_private_regular_descriptor(descriptor: int) -> os.stat_result:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise UnsafeDiagnosticsPathError("Diagnostics file descriptor has an unsafe inode")
    return metadata


def read_private_bytes(
    path: Path,
    *,
    max_bytes: int,
    trusted_root: Path | None = None,
) -> bytes:
    """Read one bounded private file without following a replaced pathname."""

    parent_descriptor, name = _open_parent_directory(
        path,
        trusted_root=trusted_root,
        create_parent=False,
    )
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        metadata = _assert_private_regular_descriptor(descriptor)
        if metadata.st_size > max_bytes:
            raise ValueError("Diagnostics private file exceeds its size bound")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > max_bytes:
            raise ValueError("Diagnostics private file exceeds its size bound")
        return payload
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)


def private_file_stat(
    path: Path,
    *,
    trusted_root: Path | None = None,
    missing_ok: bool = False,
) -> os.stat_result | None:
    """Return metadata from a verified private regular file descriptor."""

    try:
        descriptor = open_private_descriptor(
            path,
            os.O_RDONLY,
            trusted_root=trusted_root,
        )
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    try:
        return _assert_private_regular_descriptor(descriptor)
    finally:
        os.close(descriptor)


def private_file_size(
    path: Path,
    *,
    trusted_root: Path | None = None,
    missing_ok: bool = False,
) -> int:
    """Return a private regular file size from its verified descriptor."""

    metadata = private_file_stat(
        path,
        trusted_root=trusted_root,
        missing_ok=missing_ok,
    )
    return int(metadata.st_size) if metadata is not None else 0


def list_private_directory(path: Path, *, trusted_root: Path) -> list[str]:
    """List one trusted private directory through its anchored descriptor."""

    descriptor = _open_directory_beneath(trusted_root, Path(path), create=False)
    try:
        return sorted(str(name) for name in os.listdir(descriptor))
    finally:
        os.close(descriptor)


def private_directory_stat(path: Path, *, trusted_root: Path) -> os.stat_result:
    descriptor = _open_directory_beneath(trusted_root, path, create=False)
    try:
        return os.fstat(descriptor)
    finally:
        os.close(descriptor)


def walk_private_tree(
    path: Path,
    *,
    trusted_root: Path,
) -> Iterator[tuple[str, os.stat_result]]:
    """Yield a descriptor-relative snapshot without following path replacements."""

    root_descriptor = _open_directory_beneath(trusted_root, path, create=False)
    pending: list[tuple[int, str]] = [(root_descriptor, "")]
    try:
        while pending:
            directory_descriptor, relative_parent = pending.pop()
            try:
                with os.scandir(directory_descriptor) as entries:
                    for entry in entries:
                        name = entry.name
                        relative = f"{relative_parent}/{name}" if relative_parent else name
                        metadata = os.stat(
                            name,
                            dir_fd=directory_descriptor,
                            follow_symlinks=False,
                        )
                        if stat.S_ISLNK(metadata.st_mode):
                            yield relative, metadata
                            continue
                        if stat.S_ISDIR(metadata.st_mode):
                            child_descriptor = os.open(
                                name,
                                os.O_RDONLY
                                | getattr(os, "O_DIRECTORY", 0)
                                | getattr(os, "O_NOFOLLOW", 0),
                                dir_fd=directory_descriptor,
                            )
                            child_metadata = os.fstat(child_descriptor)
                            if not stat.S_ISDIR(child_metadata.st_mode):
                                os.close(child_descriptor)
                                raise UnsafeDiagnosticsPathError(
                                    "Diagnostics tree entry changed type"
                                )
                            yield relative, child_metadata
                            pending.append((child_descriptor, relative))
                            continue
                        if stat.S_ISREG(metadata.st_mode):
                            descriptor = os.open(
                                name,
                                os.O_RDONLY
                                | getattr(os, "O_NOFOLLOW", 0)
                                | getattr(os, "O_NONBLOCK", 0),
                                dir_fd=directory_descriptor,
                            )
                            try:
                                metadata = os.fstat(descriptor)
                            finally:
                                os.close(descriptor)
                        yield relative, metadata
            finally:
                os.close(directory_descriptor)
    finally:
        for descriptor, _relative in pending:
            try:
                os.close(descriptor)
            except OSError:
                continue


def unlink_private_file(
    path: Path,
    *,
    trusted_root: Path,
    missing_ok: bool = False,
) -> bool:
    """Unlink one verified private regular file relative to its trusted root."""

    try:
        parent_descriptor, name = _open_parent_directory(
            path,
            trusted_root=trusted_root,
            create_parent=False,
        )
    except FileNotFoundError:
        if missing_ok:
            return False
        raise
    try:
        try:
            metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            if missing_ok:
                return False
            raise
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise UnsafeDiagnosticsPathError("Diagnostics unlink target has an unsafe inode")
        os.unlink(name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
        return True
    finally:
        os.close(parent_descriptor)


def open_private_descriptor(
    path: Path,
    flags: int,
    *,
    mode: int = FILE_MODE,
    trusted_root: Path | None = None,
) -> int:
    """Open one private regular file relative to its verified parent directory."""

    parent_descriptor, name = _open_parent_directory(
        path,
        trusted_root=trusted_root,
        create_parent=bool(int(flags) & os.O_CREAT),
    )
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            int(flags) | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=parent_descriptor,
        )
        _assert_private_regular_descriptor(descriptor)
        return descriptor
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    finally:
        os.close(parent_descriptor)


def rename_private_file(
    source: Path,
    destination: Path,
    *,
    trusted_root: Path,
) -> None:
    """Install one private regular file by descriptor-relative atomic rename."""

    source_parent, source_name = _open_parent_directory(
        source,
        trusted_root=trusted_root,
        create_parent=False,
    )
    destination_parent, destination_name = _open_parent_directory(
        destination,
        trusted_root=trusted_root,
        create_parent=True,
    )
    try:
        source_metadata = os.stat(
            source_name,
            dir_fd=source_parent,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(source_metadata.st_mode) or source_metadata.st_nlink != 1:
            raise UnsafeDiagnosticsPathError(
                "Diagnostics rename source has an unsafe inode"
            )
        try:
            os.stat(
                destination_name,
                dir_fd=destination_parent,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(f"Diagnostics rename destination exists: {destination}")
        os.rename(
            source_name,
            destination_name,
            src_dir_fd=source_parent,
            dst_dir_fd=destination_parent,
        )
        installed_descriptor = os.open(
            destination_name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=destination_parent,
        )
        try:
            _assert_private_regular_descriptor(installed_descriptor)
            os.fchmod(installed_descriptor, FILE_MODE)
        finally:
            os.close(installed_descriptor)
        os.fsync(destination_parent)
        source_parent_metadata = os.fstat(source_parent)
        destination_parent_metadata = os.fstat(destination_parent)
        if (
            source_parent_metadata.st_dev,
            source_parent_metadata.st_ino,
        ) != (
            destination_parent_metadata.st_dev,
            destination_parent_metadata.st_ino,
        ):
            os.fsync(source_parent)
    finally:
        os.close(destination_parent)
        os.close(source_parent)


def atomic_write_bytes(
    path: Path,
    payload: bytes,
    *,
    trusted_root: Path | None = None,
) -> None:
    path = Path(path)
    parent_descriptor, name = _open_parent_directory(
        path,
        trusted_root=trusted_root,
        create_parent=True,
    )
    temporary_name = f".{name}.{secrets.token_hex(12)}.tmp"
    descriptor = -1
    try:
        try:
            existing = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and (
            not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1
        ):
            raise UnsafeDiagnosticsPathError(f"Diagnostics path has an unsafe inode: {path}")
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            FILE_MODE,
            dir_fd=parent_descriptor,
        )
        os.fchmod(descriptor, FILE_MODE)
        _assert_private_regular_descriptor(descriptor)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(
            temporary_name,
            name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        installed_descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        try:
            _assert_private_regular_descriptor(installed_descriptor)
            os.fchmod(installed_descriptor, FILE_MODE)
        finally:
            os.close(installed_descriptor)
        os.fsync(parent_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass
        os.close(parent_descriptor)


def encode_json_document(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"


def atomic_write_json(
    path: Path,
    payload: Any,
    *,
    trusted_root: Path | None = None,
) -> None:
    atomic_write_bytes(path, encode_json_document(payload), trusted_root=trusted_root)


def open_private_append(path: Path, *, trusted_root: Path | None = None):
    path = Path(path)
    parent_descriptor, name = _open_parent_directory(
        path,
        trusted_root=trusted_root,
        create_parent=True,
    )
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
            FILE_MODE,
            dir_fd=parent_descriptor,
        )
        _assert_private_regular_descriptor(descriptor)
        os.fchmod(descriptor, FILE_MODE)
        return os.fdopen(descriptor, "ab", buffering=0)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    finally:
        os.close(parent_descriptor)


def fsync_directory(path: Path, *, trusted_root: Path | None = None) -> None:
    if trusted_root is not None:
        descriptor = _open_directory_beneath(trusted_root, Path(path), create=False)
    else:
        descriptor = _open_directory_descriptor(Path(path))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
