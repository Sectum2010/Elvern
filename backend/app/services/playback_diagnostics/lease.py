from __future__ import annotations

import errno
import json
import os
import time
from pathlib import Path
from typing import Any

from .fileio import FILE_MODE, assert_regular_nonsymlink, ensure_private_directory, resolve_beneath

try:
    import fcntl
except ImportError:  # pragma: no cover - Elvern's diagnostics writer currently runs on Linux.
    fcntl = None  # type: ignore[assignment]


LEASE_FILENAME = ".writer.lock"


class DiagnosticsLeaseError(RuntimeError):
    """Raised when the diagnostics root is already owned by another writer."""


class DiagnosticsRootLease:
    """Kernel-backed exclusive ownership for one diagnostics root."""

    def __init__(self, root: Path, *, mode: str, metadata: dict[str, Any] | None = None) -> None:
        self.root = Path(root)
        self.mode = str(mode)
        self.metadata = dict(metadata or {})
        self.path = resolve_beneath(self.root, LEASE_FILENAME)
        self._handle = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> "DiagnosticsRootLease":
        if self.held:
            return self
        if fcntl is None:
            raise DiagnosticsLeaseError("Playback diagnostics require kernel file locking")
        ensure_private_directory(self.root)
        assert_regular_nonsymlink(self.path)
        descriptor = os.open(
            self.path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            FILE_MODE,
        )
        os.fchmod(descriptor, FILE_MODE)
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise DiagnosticsLeaseError(
                    "Playback diagnostics root is owned by another writer or maintenance process"
                ) from exc
            raise
        payload = {
            "schema_version": "playback-diagnostics-root-lease-v1",
            "pid": os.getpid(),
            "mode": self.mode,
            "acquired_at_unix_ns": time.time_ns(),
            **self.metadata,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        handle.seek(0)
        handle.truncate(0)
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle
        return self

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "DiagnosticsRootLease":
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        self.release()


def read_lease_status(root: Path) -> dict[str, Any]:
    """Read bounded lock metadata and test the kernel lock without creating files."""

    root = Path(root)
    path = root / LEASE_FILENAME
    if not path.exists() or path.is_symlink() or not path.is_file():
        return {"held": False, "metadata": None}
    metadata: dict[str, Any] | None = None
    try:
        with path.open("rb") as handle:
            raw = handle.read(8_193)
        if len(raw) > 8_192:
            raise ValueError("Diagnostics lease metadata is unexpectedly large")
        parsed = json.loads(raw.decode("utf-8"))
        if isinstance(parsed, dict):
            metadata = {
                key: parsed.get(key)
                for key in ("schema_version", "pid", "mode", "acquired_at_unix_ns", "elvern_commit")
                if key in parsed
            }
    except (OSError, UnicodeDecodeError, ValueError):
        metadata = None
    if fcntl is None:
        return {"held": None, "metadata": metadata}
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return {"held": None, "metadata": metadata}
    handle = os.fdopen(descriptor, "rb", buffering=0)
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return {"held": True, "metadata": metadata}
            return {"held": None, "metadata": metadata}
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return {"held": False, "metadata": metadata}
    finally:
        handle.close()
