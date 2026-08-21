from __future__ import annotations

import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from .errors import DiagnosticsRateLimitError


EVENTS_PER_SOURCE_PER_MINUTE = 30_000
BYTES_PER_SOURCE_PER_MINUTE = 64 * 1024 * 1024
BYTES_PER_SESSION_PER_MINUTE = 128 * 1024 * 1024
BYTES_PER_USER_PER_MINUTE = 256 * 1024 * 1024
GLOBAL_INGRESS_BYTES_PER_SECOND = 64 * 1024 * 1024
SESSION_SOURCE_CREATIONS_PER_USER_PER_MINUTE = 120
HOST_OBSERVATIONS_PER_SECOND = 64
MAX_CONCURRENT_DIAGNOSTICS_WRITES = 8
MAX_CONCURRENT_EXPORTS = 2
MAX_BUDGET_IDENTITIES = 4_096


@dataclass
class _Window:
    started: float
    value: int


class DiagnosticsBudgets:
    """Bounded, diagnostics-only admission accounting.

    Every operation is non-blocking. A saturated budget rejects diagnostics work;
    it never waits and therefore cannot backpressure playback.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: OrderedDict[tuple[str, str], _Window] = OrderedDict()
        self._writes = threading.BoundedSemaphore(MAX_CONCURRENT_DIAGNOSTICS_WRITES)
        self._exports = threading.BoundedSemaphore(MAX_CONCURRENT_EXPORTS)

    def admit_ingest(
        self,
        *,
        source_id: str,
        session_id: str,
        user_id: int,
        event_count: int,
        byte_count: int,
    ) -> None:
        now = time.monotonic()
        with self._lock:
            checks = (
                ("source_events", source_id, 60.0, event_count, EVENTS_PER_SOURCE_PER_MINUTE),
                ("source_bytes", source_id, 60.0, byte_count, BYTES_PER_SOURCE_PER_MINUTE),
                ("session_bytes", session_id, 60.0, byte_count, BYTES_PER_SESSION_PER_MINUTE),
                ("user_bytes", str(user_id), 60.0, byte_count, BYTES_PER_USER_PER_MINUTE),
                ("global_bytes", "global", 1.0, byte_count, GLOBAL_INGRESS_BYTES_PER_SECOND),
            )
            self._admit_all_locked(checks, now=now)

    def admit_creation(self, *, user_id: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._admit_all_locked(
                ((
                    "user_creations",
                    str(user_id),
                    60.0,
                    1,
                    SESSION_SOURCE_CREATIONS_PER_USER_PER_MINUTE,
                ),),
                now=now,
            )

    def admit_global_ingress(self, *, byte_count: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._admit_all_locked(
                ((
                    "global_bytes",
                    "global",
                    1.0,
                    int(byte_count),
                    GLOBAL_INGRESS_BYTES_PER_SECOND,
                ),),
                now=now,
            )

    def admit_host_observation(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._admit_all_locked(
                (("host_observations", "global", 1.0, 1, HOST_OBSERVATIONS_PER_SECOND),),
                now=now,
            )

    @contextmanager
    def write_slot(self) -> Iterator[None]:
        if not self._writes.acquire(blocking=False):
            raise DiagnosticsRateLimitError(
                code="diagnostics_write_concurrency_exceeded",
            )
        try:
            yield
        finally:
            self._writes.release()

    @contextmanager
    def export_slot(self) -> Iterator[None]:
        if not self._exports.acquire(blocking=False):
            raise DiagnosticsRateLimitError(
                code="diagnostics_export_concurrency_exceeded",
            )
        try:
            yield
        finally:
            self._exports.release()

    def _admit_all_locked(self, checks, *, now: float) -> None:
        pending: list[tuple[tuple[str, str], _Window]] = []
        for namespace, identity, duration, amount, limit in checks:
            key = (namespace, identity)
            current = self._windows.get(key)
            if current is None or now - current.started >= duration:
                candidate = _Window(started=now, value=amount)
            else:
                candidate = _Window(started=current.started, value=current.value + amount)
            if candidate.value > limit:
                raise DiagnosticsRateLimitError(
                    code=f"diagnostics_{namespace}_budget_exceeded",
                )
            pending.append((key, candidate))
        for key, candidate in pending:
            self._windows[key] = candidate
            self._windows.move_to_end(key)
        while len(self._windows) > MAX_BUDGET_IDENTITIES:
            self._windows.popitem(last=False)
