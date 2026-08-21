from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import stat
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .constants import (
    DIAGNOSTICS_EMERGENCY_RESERVE_BYTES,
    DIAGNOSTICS_HARD_CAP_BYTES,
)
from .fileio import (
    UnsafeDiagnosticsPathError,
    atomic_write_json,
    encode_json_document,
    ensure_private_directory,
    private_file_size,
    read_private_bytes,
    resolve_beneath,
)


CAPACITY_LEDGER_SCHEMA_VERSION = "playback-diagnostics-capacity-ledger-v1"
CAPACITY_LEDGER_FILE_NAME = "capacity-ledger.json"
CAPACITY_MANAGED_FILE_CLASSES = (
    "catalog",
    "exports",
    "host_observations",
    "identity",
    "journals",
    "keys",
    "lease_metadata",
    "manifests",
    "quarantine",
    "recovery_scratch",
    "session_artifacts",
    "status",
    "temporary_files",
)


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


class DiagnosticsCapacityError(RuntimeError):
    """Raised when a diagnostics write cannot be reserved safely."""


class CapacityReservation:
    """One strictly single-use capacity transaction reservation."""

    def __init__(
        self,
        guard: "DiagnosticsCapacityGuard",
        reserved_bytes: int,
        snapshot: CapacitySnapshot,
    ) -> None:
        self.guard = guard
        self.reserved_bytes = int(reserved_bytes)
        self.snapshot = snapshot
        self._closed = False
        self._state_lock = threading.Lock()

    def _complete(
        self,
        operation: str,
        *,
        old_size: int = 0,
        new_size: int = 0,
        actual_peak_bytes: int | None = None,
    ) -> None:
        with self._state_lock:
            if self._closed:
                raise DiagnosticsCapacityError("capacity_reservation_already_closed")
            self.guard._commit_reservation(
                self.reserved_bytes,
                operation=operation,
                old_size=old_size,
                new_size=new_size,
                actual_peak_bytes=actual_peak_bytes,
            )
            self._closed = True

    def commit(self, actual_bytes: int | None = None) -> None:
        """Compatibility alias for append transactions."""

        self.commit_append(
            self.reserved_bytes if actual_bytes is None else actual_bytes
        )

    def commit_append(self, actual_bytes: int) -> None:
        self._complete("append", new_size=int(actual_bytes))

    def commit_replacement(
        self,
        *,
        old_size: int,
        new_size: int,
        actual_peak_bytes: int | None = None,
    ) -> None:
        self._complete(
            "replacement",
            old_size=int(old_size),
            new_size=int(new_size),
            actual_peak_bytes=actual_peak_bytes,
        )

    def commit_temporary_peak(self, *, final_growth_bytes: int = 0) -> None:
        self._complete("temporary_peak", new_size=int(final_growth_bytes))

    def release(self) -> None:
        with self._state_lock:
            if self._closed:
                raise DiagnosticsCapacityError("capacity_reservation_already_closed")
            self.guard._release_reservation(self.reserved_bytes)
            self._closed = True

    def __enter__(self) -> "CapacityReservation":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc, traceback
        if exc_type is None:
            self.commit_append(self.reserved_bytes)
        else:
            self.release()

    @property
    def closed(self) -> bool:
        with self._state_lock:
            return self._closed


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
                    metadata = entry.stat(follow_symlinks=False)
                    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                        raise UnsafeDiagnosticsPathError(
                            f"Refusing unsafe inode inside diagnostics root: {entry.path}"
                        )
                    total += metadata.st_size
                else:
                    raise UnsafeDiagnosticsPathError(
                        f"Refusing non-regular inode inside diagnostics root: {entry.path}"
                    )
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
        self.ledger_path = resolve_beneath(self.root, CAPACITY_LEDGER_FILE_NAME)
        self.hard_cap_bytes = int(hard_cap_bytes)
        self.emergency_reserve_bytes = int(emergency_reserve_bytes)
        self.normal_budget_bytes = self.hard_cap_bytes - self.emergency_reserve_bytes
        self.minimum_free_bytes = int(minimum_free_bytes)
        self.disk_usage_reader = disk_usage_reader
        self._lock = threading.RLock()
        self._reserved_bytes = 0
        self._last_snapshot: CapacitySnapshot | None = None
        self._ledger_generation = 0
        self._last_reconciliation_ns = 0
        self._ledger_fast_path = False
        self._ledger_write_failed = False

        trusted_usage = self._load_clean_ledger()
        if trusted_usage is None:
            self._usage_bytes = directory_size_bytes(self.root)
            self._last_reconciliation_ns = time.time_ns()
        else:
            self._usage_bytes = trusted_usage
            self._ledger_fast_path = True
        try:
            self._write_ledger_locked(shutdown_state="dirty")
        except DiagnosticsCapacityError:
            # An already-full store must remain inspectable. With no durable dirty
            # marker the next startup will reconcile rather than trust a fast path.
            self._ledger_write_failed = True

    @staticmethod
    def _ledger_integrity(payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _load_clean_ledger(self) -> int | None:
        try:
            ledger_size = private_file_size(
                self.ledger_path,
                trusted_root=self.root,
            )
        except FileNotFoundError:
            return None
        try:
            document = json.loads(
                read_private_bytes(
                    self.ledger_path,
                    max_bytes=256 * 1024,
                    trusted_root=self.root,
                ).decode("utf-8")
            )
            integrity = str(document.pop("integrity_sha256"))
            if document.get("schema_version") != CAPACITY_LEDGER_SCHEMA_VERSION:
                return None
            if document.get("shutdown_state") != "clean":
                return None
            if not bool(document.get("all_mutation_workers_stopped")):
                return None
            if int(document.get("hard_cap_bytes")) != self.hard_cap_bytes:
                return None
            if int(document.get("emergency_reserve_bytes")) != self.emergency_reserve_bytes:
                return None
            if tuple(document.get("managed_file_classes") or ()) != CAPACITY_MANAGED_FILE_CLASSES:
                return None
            if not hmac.compare_digest(integrity, self._ledger_integrity(document)):
                return None
            usage = int(document.get("committed_usage_bytes"))
            generation = int(document.get("ledger_generation"))
            reconciliation = int(document.get("last_reconciliation_ns"))
            if usage < ledger_size or usage > self.hard_cap_bytes or generation < 1:
                return None
            self._ledger_generation = generation
            self._last_reconciliation_ns = max(0, reconciliation)
            return usage
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
            return None

    def _ledger_document(
        self,
        *,
        shutdown_state: str,
        committed_usage_bytes: int,
        generation: int,
        generated_at_ns: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": CAPACITY_LEDGER_SCHEMA_VERSION,
            "ledger_generation": generation,
            "shutdown_state": shutdown_state,
            "all_mutation_workers_stopped": shutdown_state == "clean",
            "committed_usage_bytes": committed_usage_bytes,
            "reserved_bytes": 0,
            "hard_cap_bytes": self.hard_cap_bytes,
            "emergency_reserve_bytes": self.emergency_reserve_bytes,
            "last_reconciliation_ns": self._last_reconciliation_ns,
            "generated_at_ns": generated_at_ns,
            "managed_file_classes": list(CAPACITY_MANAGED_FILE_CLASSES),
        }
        payload["integrity_sha256"] = self._ledger_integrity(payload)
        return payload

    def _write_ledger_locked(self, *, shutdown_state: str) -> None:
        if shutdown_state not in {"clean", "dirty"}:
            raise ValueError("Invalid diagnostics capacity ledger shutdown state")
        old_size = private_file_size(
            self.ledger_path,
            trusted_root=self.root,
            missing_ok=True,
        )
        generation = self._ledger_generation + 1
        generated_at_ns = time.time_ns()
        base_usage = self._usage_bytes - old_size
        if base_usage < 0:
            raise DiagnosticsCapacityError("capacity_ledger_replacement_underflow")
        candidate_usage = self._usage_bytes
        document: dict[str, Any] = {}
        encoded = b""
        for _ in range(8):
            document = self._ledger_document(
                shutdown_state=shutdown_state,
                committed_usage_bytes=candidate_usage,
                generation=generation,
                generated_at_ns=generated_at_ns,
            )
            encoded = encode_json_document(document)
            next_usage = base_usage + len(encoded)
            if next_usage == candidate_usage:
                break
            candidate_usage = next_usage
        else:
            raise DiagnosticsCapacityError("capacity_ledger_size_did_not_converge")
        if candidate_usage + self._reserved_bytes > self.hard_cap_bytes:
            raise DiagnosticsCapacityError("capacity_hard_cap_invariant_broken")
        atomic_write_json(self.ledger_path, document, trusted_root=self.root)
        actual_size = private_file_size(self.ledger_path, trusted_root=self.root)
        if actual_size != len(encoded):
            raise DiagnosticsCapacityError("capacity_ledger_size_mismatch")
        self._usage_bytes = base_usage + actual_size
        self._ledger_generation = generation

    def refresh(self, *, projected_write_bytes: int = 0, critical: bool = False) -> CapacitySnapshot:
        with self._lock:
            usage = self._usage_bytes
            requested = max(0, int(projected_write_bytes))
            filesystem_free = int(self.disk_usage_reader(self.root).free)
            projected_usage = usage + self._reserved_bytes + requested
            if filesystem_free - requested < self.minimum_free_bytes:
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

    def reserve(self, write_bytes: int, *, critical: bool = False) -> CapacityReservation:
        requested = int(write_bytes)
        if requested < 0:
            raise ValueError("Diagnostics capacity reservation cannot be negative")
        with self._lock:
            snapshot = self.refresh(projected_write_bytes=requested, critical=critical)
            allowed = snapshot.state == "normal" or (critical and snapshot.state == "reserve")
            if not allowed:
                raise DiagnosticsCapacityError(snapshot.state)
            self._reserved_bytes += requested
            return CapacityReservation(self, requested, snapshot)

    def _commit_reservation(
        self,
        reserved_bytes: int,
        *,
        operation: str,
        old_size: int,
        new_size: int,
        actual_peak_bytes: int | None,
    ) -> None:
        with self._lock:
            reserved = int(reserved_bytes)
            old = int(old_size)
            new = int(new_size)
            if reserved < 0 or old < 0 or new < 0:
                raise DiagnosticsCapacityError("capacity_transaction_negative_size")
            if reserved > self._reserved_bytes:
                raise DiagnosticsCapacityError("capacity_reservation_invariant_broken")
            peak = new if actual_peak_bytes is None else int(actual_peak_bytes)
            if peak < 0:
                raise DiagnosticsCapacityError("capacity_transaction_negative_size")
            if peak > reserved:
                raise DiagnosticsCapacityError("capacity_reservation_underestimated")
            if operation in {"append", "temporary_peak"}:
                hypothetical_usage = self._usage_bytes + new
            elif operation == "replacement":
                if old > self._usage_bytes:
                    raise DiagnosticsCapacityError("capacity_replacement_underflow")
                hypothetical_usage = self._usage_bytes - old + new
            else:
                raise DiagnosticsCapacityError("capacity_transaction_unknown_operation")
            hypothetical_reserved = self._reserved_bytes - reserved
            if hypothetical_usage + hypothetical_reserved > self.hard_cap_bytes:
                raise DiagnosticsCapacityError("capacity_hard_cap_invariant_broken")
            self._usage_bytes = hypothetical_usage
            self._reserved_bytes = hypothetical_reserved

    def _release_reservation(self, reserved_bytes: int) -> None:
        with self._lock:
            reserved = int(reserved_bytes)
            if reserved < 0 or reserved > self._reserved_bytes:
                raise DiagnosticsCapacityError("capacity_reservation_invariant_broken")
            self._reserved_bytes -= reserved

    def account_deletion(self, *, old_size: int) -> None:
        old = int(old_size)
        with self._lock:
            if old < 0 or old > self._usage_bytes:
                raise DiagnosticsCapacityError("capacity_deletion_underflow")
            self._usage_bytes -= old

    def reconcile_usage(self) -> CapacitySnapshot:
        """Explicit maintenance reconciliation; never used in a write hot path."""

        measured = directory_size_bytes(self.root)
        with self._lock:
            if self._reserved_bytes:
                raise DiagnosticsCapacityError("capacity_reconcile_with_active_reservations")
            self._usage_bytes = measured
            self._last_reconciliation_ns = time.time_ns()
            self._write_ledger_locked(shutdown_state="dirty")
        return self.refresh()

    def mark_clean_shutdown(self) -> None:
        measured = directory_size_bytes(self.root)
        with self._lock:
            if self._reserved_bytes:
                raise DiagnosticsCapacityError("capacity_clean_shutdown_with_active_reservations")
            # SQLite can remove or shrink WAL/SHM files when the catalog closes.
            # Reconcile only at the clean shutdown boundary so the next startup
            # can trust the ledger without recursively scanning the store.
            self._usage_bytes = measured
            self._last_reconciliation_ns = time.time_ns()
            self._write_ledger_locked(shutdown_state="clean")

    def persist_dirty_checkpoint(self) -> None:
        """Durably checkpoint accounted usage while retaining crash-reconcile mode."""

        with self._lock:
            if self._reserved_bytes:
                raise DiagnosticsCapacityError(
                    "capacity_dirty_checkpoint_with_active_reservations"
                )
            self._write_ledger_locked(shutdown_state="dirty")

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
        old_size = private_file_size(
            self.status_path,
            trusted_root=self.root,
            missing_ok=True,
        )
        encoded_size = len(encode_json_document(payload))
        try:
            reservation = self.reserve(encoded_size, critical=True)
        except DiagnosticsCapacityError:
            return snapshot
        try:
            atomic_write_json(self.status_path, payload, trusted_root=self.root)
            new_size = private_file_size(self.status_path, trusted_root=self.root)
            reservation.commit_replacement(old_size=old_size, new_size=new_size)
        except Exception:
            new_size = private_file_size(
                self.status_path,
                trusted_root=self.root,
                missing_ok=True,
            )
            try:
                reservation.commit_replacement(old_size=old_size, new_size=new_size)
            except DiagnosticsCapacityError:
                if not reservation.closed:
                    reservation.release()
                self.reconcile_usage()
            raise
        return snapshot

    @property
    def last_snapshot(self) -> CapacitySnapshot | None:
        return self._last_snapshot

    @property
    def usage_bytes(self) -> int:
        with self._lock:
            return self._usage_bytes

    @property
    def reserved_bytes(self) -> int:
        with self._lock:
            return self._reserved_bytes

    @property
    def ledger_fast_path(self) -> bool:
        return self._ledger_fast_path

    @property
    def ledger_generation(self) -> int:
        return self._ledger_generation
