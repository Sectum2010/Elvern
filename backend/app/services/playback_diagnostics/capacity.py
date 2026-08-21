from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .constants import (
    DIAGNOSTICS_EMERGENCY_RESERVE_BYTES,
    DIAGNOSTICS_HARD_CAP_BYTES,
)
from .fileio import UnsafeDiagnosticsPathError, atomic_write_json, ensure_private_directory, resolve_beneath


@dataclass(frozen=True, slots=True)
class CapacitySnapshot:
    state: str
    usage_bytes: int
    normal_budget_bytes: int
    emergency_reserve_bytes: int
    hard_cap_bytes: int
    filesystem_free_bytes: int
    minimum_free_bytes: int
    checked_at_ns: int
    reason: str | None = None


def directory_size_bytes(root: Path) -> int:
    root = Path(root)
    if not root.exists():
        return 0
    if root.is_symlink():
        raise UnsafeDiagnosticsPathError("Diagnostics root must not be a symlink")
    total = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    raise UnsafeDiagnosticsPathError(
                        f"Refusing symlink inside diagnostics root: {entry.path}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
    return total


class DiagnosticsCapacityGuard:
    def __init__(
        self,
        root: Path,
        *,
        hard_cap_bytes: int = DIAGNOSTICS_HARD_CAP_BYTES,
        emergency_reserve_bytes: int = DIAGNOSTICS_EMERGENCY_RESERVE_BYTES,
        minimum_free_bytes: int = 1_000_000_000,
        disk_usage_reader: Callable[[Path], Any] = shutil.disk_usage,
    ) -> None:
        if hard_cap_bytes <= 0:
            raise ValueError("Diagnostics hard cap must be positive")
        if emergency_reserve_bytes <= 0 or emergency_reserve_bytes >= hard_cap_bytes:
            raise ValueError("Diagnostics emergency reserve must be below the hard cap")
        self.root = ensure_private_directory(Path(root))
        self.status_path = resolve_beneath(self.root, "recorder-status.json")
        self.hard_cap_bytes = int(hard_cap_bytes)
        self.emergency_reserve_bytes = int(emergency_reserve_bytes)
        self.normal_budget_bytes = self.hard_cap_bytes - self.emergency_reserve_bytes
        self.minimum_free_bytes = int(minimum_free_bytes)
        self.disk_usage_reader = disk_usage_reader
        self._lock = threading.RLock()
        self._last_snapshot: CapacitySnapshot | None = None

    def refresh(self, *, projected_write_bytes: int = 0, critical: bool = False) -> CapacitySnapshot:
        with self._lock:
            usage = directory_size_bytes(self.root)
            filesystem_free = int(self.disk_usage_reader(self.root).free)
            projected_usage = usage + max(0, int(projected_write_bytes))
            if filesystem_free - max(0, int(projected_write_bytes)) < self.minimum_free_bytes:
                state = "filesystem_low_space"
                reason = "filesystem free space is below the diagnostics safety floor"
            elif projected_usage > self.hard_cap_bytes:
                state = "capacity_exhausted"
                reason = "diagnostics hard cap reached"
            elif projected_usage > self.normal_budget_bytes:
                state = "reserve" if critical else "capacity_reached"
                reason = "normal diagnostics budget reached"
            else:
                state = "normal"
                reason = None
            snapshot = CapacitySnapshot(
                state=state,
                usage_bytes=usage,
                normal_budget_bytes=self.normal_budget_bytes,
                emergency_reserve_bytes=self.emergency_reserve_bytes,
                hard_cap_bytes=self.hard_cap_bytes,
                filesystem_free_bytes=filesystem_free,
                minimum_free_bytes=self.minimum_free_bytes,
                checked_at_ns=time.time_ns(),
                reason=reason,
            )
            self._last_snapshot = snapshot
            return snapshot

    def permit(self, write_bytes: int, *, critical: bool = False) -> tuple[bool, CapacitySnapshot]:
        snapshot = self.refresh(projected_write_bytes=write_bytes, critical=critical)
        allowed = snapshot.state == "normal" or (critical and snapshot.state == "reserve")
        return allowed, snapshot

    def write_current_status(self, **extra: object) -> CapacitySnapshot:
        snapshot = self.refresh()
        payload = {
            "schema_version": "playback-diagnostics-recorder-status-v1",
            **asdict(snapshot),
            **extra,
        }
        # This bounded replace remains available at the cap; it never creates an
        # unbounded append path and gives the operator the current recovery state.
        atomic_write_json(self.status_path, payload)
        return snapshot

    @property
    def last_snapshot(self) -> CapacitySnapshot | None:
        return self._last_snapshot
