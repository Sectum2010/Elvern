from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


DIRECTORY_MODE = 0o700
FILE_MODE = 0o600


class UnsafeDiagnosticsPathError(ValueError):
    """Raised when a diagnostics path escapes its trusted root."""


def ensure_private_directory(path: Path) -> Path:
    path = Path(path)
    if path.exists() and path.is_symlink():
        raise UnsafeDiagnosticsPathError(f"Refusing diagnostics symlink: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
    if path.is_symlink() or not path.is_dir():
        raise UnsafeDiagnosticsPathError(f"Diagnostics directory is not safe: {path}")
    os.chmod(path, DIRECTORY_MODE)
    return path


def resolve_beneath(root: Path, *parts: str | Path) -> Path:
    root = Path(root).resolve(strict=False)
    candidate = root.joinpath(*(Path(part) for part in parts))
    if candidate.exists() and candidate.is_symlink():
        raise UnsafeDiagnosticsPathError(f"Refusing diagnostics symlink: {candidate}")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise UnsafeDiagnosticsPathError("Diagnostics path escapes its root") from exc
    return resolved


def assert_regular_nonsymlink(path: Path) -> None:
    if path.is_symlink():
        raise UnsafeDiagnosticsPathError(f"Refusing diagnostics symlink: {path}")
    if path.exists() and not path.is_file():
        raise UnsafeDiagnosticsPathError(f"Diagnostics path is not a regular file: {path}")


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path = Path(path)
    ensure_private_directory(path.parent)
    assert_regular_nonsymlink(path)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, FILE_MODE)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, FILE_MODE)
        fsync_directory(path.parent)
    finally:
        temporary_path.unlink(missing_ok=True)


def encode_json_document(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_bytes(path, encode_json_document(payload))


def open_private_append(path: Path):
    path = Path(path)
    ensure_private_directory(path.parent)
    assert_regular_nonsymlink(path)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
        FILE_MODE,
    )
    os.fchmod(descriptor, FILE_MODE)
    return os.fdopen(descriptor, "ab", buffering=0)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(Path(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
