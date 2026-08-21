from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from .capacity import DiagnosticsCapacityGuard
from .catalog import DiagnosticsCatalog
from .constants import CRITICAL_EVENT_NAMES
from .crypto import DiagnosticsKey, DiagnosticsKeyStore
from .fileio import ensure_private_directory, resolve_beneath
from .journal import EncryptedJournal


logger = logging.getLogger(__name__)
JOURNAL_ROTATE_BYTES = 64_000_000


@dataclass(frozen=True, slots=True)
class DiagnosticsWriteBatch:
    playback_session_id: str
    source_id: str
    source_type: str
    session_relative_path: str
    events: tuple[dict[str, Any], ...]
    enqueued_monotonic_ns: int


@dataclass(frozen=True, slots=True)
class DiagnosticsEnqueueResult:
    accepted: int
    duplicate: int
    queued: bool

    def __bool__(self) -> bool:
        return self.queued


class DiagnosticsWriter:
    def __init__(
        self,
        root: Path,
        *,
        catalog: DiagnosticsCatalog,
        capacity: DiagnosticsCapacityGuard,
        key_store: DiagnosticsKeyStore,
        active_key: DiagnosticsKey,
        max_queue_batches: int = 2_048,
        failure_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.root = ensure_private_directory(Path(root))
        self.catalog = catalog
        self.capacity = capacity
        self.key_store = key_store
        self.active_key = active_key
        self.failure_callback = failure_callback
        self._queue: queue.Queue[DiagnosticsWriteBatch | None] = queue.Queue(max_queue_batches)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._journals: dict[tuple[str, str], EncryptedJournal] = {}
        self._pending_lock = threading.Lock()
        self._pending_event_keys: set[tuple[str, str, int]] = set()
        self._metrics_lock = threading.Lock()
        self._metrics: dict[str, int | float | str | None] = {
            "batches_enqueued": 0,
            "batches_written": 0,
            "events_written": 0,
            "events_duplicate": 0,
            "events_out_of_order": 0,
            "events_dropped": 0,
            "writer_errors": 0,
            "writer_latency_ms": 0.0,
            "queue_wait_ms": 0.0,
            "last_error_class": None,
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="elvern-playback-diagnostics-writer",
        )
        self._thread.start()

    def shutdown(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None

    def enqueue(self, batch: DiagnosticsWriteBatch) -> DiagnosticsEnqueueResult:
        accepted_events: list[dict[str, Any]] = []
        accepted_keys: list[tuple[str, str, int]] = []
        duplicate = 0
        with self._pending_lock:
            for event in batch.events:
                key = self._event_key(batch.source_id, event)
                if key in self._pending_event_keys:
                    duplicate += 1
                    continue
                self._pending_event_keys.add(key)
                accepted_keys.append(key)
                accepted_events.append(event)
        if not accepted_events:
            self._increment("events_duplicate", duplicate)
            return DiagnosticsEnqueueResult(accepted=0, duplicate=duplicate, queued=False)
        queued_batch = replace(batch, events=tuple(accepted_events))
        try:
            self._queue.put_nowait(queued_batch)
        except queue.Full:
            self._release_pending_keys(accepted_keys)
            self._increment("events_dropped", len(accepted_events))
            self._notify_failure(
                "writer_queue_full",
                {
                    "playback_session_id": batch.playback_session_id,
                    "events_dropped": len(accepted_events),
                },
            )
            return DiagnosticsEnqueueResult(accepted=0, duplicate=duplicate, queued=False)
        if duplicate:
            self._increment("events_duplicate", duplicate)
        self._increment("batches_enqueued", 1)
        return DiagnosticsEnqueueResult(
            accepted=len(accepted_events),
            duplicate=duplicate,
            queued=True,
        )

    def flush(self, *, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0

    def metrics(self) -> dict[str, Any]:
        with self._metrics_lock:
            return {
                **self._metrics,
                "queue_depth": self._queue.qsize(),
            }

    def _run(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                batch = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if batch is None:
                    continue
                self._write_batch(batch)
            except Exception as exc:  # noqa: BLE001 - diagnostics must fail closed.
                logger.warning("Playback diagnostics writer failed: %s", exc.__class__.__name__)
                self._increment("writer_errors", 1)
                self._set_metric("last_error_class", exc.__class__.__name__)
                self._notify_failure(
                    "writer_failure",
                    {
                        "playback_session_id": getattr(batch, "playback_session_id", ""),
                        "error_class": exc.__class__.__name__,
                    },
                )
            finally:
                if batch is not None:
                    self._release_pending_batch(batch)
                self._queue.task_done()

    def _write_batch(self, batch: DiagnosticsWriteBatch) -> None:
        started_ns = time.monotonic_ns()
        queue_wait_ms = max(0.0, (started_ns - batch.enqueued_monotonic_ns) / 1_000_000)
        seen: set[tuple[str, str, int]] = set()
        unique_events: list[dict[str, Any]] = []
        for event in batch.events:
            key = self._event_key(batch.source_id, event)
            if key in seen or self.catalog.event_exists(*key):
                continue
            seen.add(key)
            unique_events.append(event)
        unique_events_tuple = tuple(unique_events)
        duplicate_before_write = len(batch.events) - len(unique_events_tuple)
        if duplicate_before_write:
            self._increment("events_duplicate", duplicate_before_write)
        if not unique_events_tuple:
            return

        events_to_write = unique_events_tuple
        estimated_bytes = self._estimated_batch_bytes(events_to_write)
        critical = all(self._is_critical_event(event) for event in events_to_write)
        permitted, snapshot = self.capacity.permit(estimated_bytes, critical=critical)
        capacity_failure_state = snapshot.state
        dropped_for_capacity = 0
        if not permitted and snapshot.state == "capacity_reached" and not critical:
            critical_events = tuple(
                event for event in events_to_write if self._is_critical_event(event)
            )
            if critical_events:
                critical_bytes = self._estimated_batch_bytes(critical_events)
                permitted, snapshot = self.capacity.permit(critical_bytes, critical=True)
                if permitted:
                    dropped_for_capacity = len(events_to_write) - len(critical_events)
                    events_to_write = critical_events
        if not permitted:
            dropped_for_capacity = len(events_to_write)
        if dropped_for_capacity:
            self._increment("events_dropped", dropped_for_capacity)
            self.capacity.write_current_status(
                enabled=True,
                writer_queue_depth=self._queue.qsize(),
                last_gap_reason=capacity_failure_state,
            )
            self._notify_failure(
                capacity_failure_state,
                {
                    "playback_session_id": batch.playback_session_id,
                    "events_dropped": dropped_for_capacity,
                    "capacity_state": capacity_failure_state,
                },
            )
        if not permitted:
            return

        journal = self._journal_for(batch)
        chunk = journal.append(events_to_write)
        if chunk is None:
            return
        relative_journal_path = str(journal.path.relative_to(self.root))
        inserted, catalog_duplicates, out_of_order = self.catalog.record_events(
            playback_session_id=batch.playback_session_id,
            source_id=batch.source_id,
            journal_relative_path=relative_journal_path,
            journal_chunk_sequence=chunk.sequence,
            events=events_to_write,
        )
        elapsed_ms = (time.monotonic_ns() - started_ns) / 1_000_000
        self._increment("batches_written", 1)
        self._increment("events_written", inserted)
        self._increment("events_duplicate", catalog_duplicates)
        self._increment("events_out_of_order", out_of_order)
        self._set_metric("writer_latency_ms", elapsed_ms)
        self._set_metric("queue_wait_ms", queue_wait_ms)
        self.capacity.write_current_status(
            enabled=True,
            writer_queue_depth=self._queue.qsize(),
            writer_latency_ms=round(elapsed_ms, 3),
            last_error_class=self.metrics().get("last_error_class"),
        )

    def _journal_for(self, batch: DiagnosticsWriteBatch) -> EncryptedJournal:
        key = (batch.playback_session_id, batch.source_type)
        existing = self._journals.get(key)
        if existing is not None and existing.path.stat().st_size < JOURNAL_ROTATE_BYTES:
            return existing
        session_path = resolve_beneath(self.root, batch.session_relative_path)
        raw_path = ensure_private_directory(resolve_beneath(session_path, "raw"))
        sequence = 1
        while True:
            candidate = resolve_beneath(raw_path, f"{batch.source_type}-{sequence:06d}.elvd")
            if not candidate.exists() or candidate.stat().st_size < JOURNAL_ROTATE_BYTES:
                break
            sequence += 1
        journal = EncryptedJournal(
            candidate,
            playback_session_id=batch.playback_session_id,
            source_type=batch.source_type,
            key_store=self.key_store,
            active_key=self.active_key,
            quarantine_root=resolve_beneath(self.root, "quarantine"),
        )
        self._journals[key] = journal
        return journal

    def _increment(self, key: str, amount: int) -> None:
        with self._metrics_lock:
            self._metrics[key] = int(self._metrics.get(key) or 0) + int(amount)

    @staticmethod
    def _event_key(source_id: str, event: dict[str, Any]) -> tuple[str, str, int]:
        return (
            str(event["event_id"]),
            str(source_id),
            int(event["source_sequence"]),
        )

    @staticmethod
    def _is_critical_event(event: dict[str, Any]) -> bool:
        return bool(
            event.get("priority") == "critical"
            or event.get("event_name") in CRITICAL_EVENT_NAMES
        )

    @staticmethod
    def _estimated_batch_bytes(events: tuple[dict[str, Any], ...]) -> int:
        return sum(
            len(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            for event in events
        ) + 8_192

    def _release_pending_batch(self, batch: DiagnosticsWriteBatch) -> None:
        self._release_pending_keys(
            [self._event_key(batch.source_id, event) for event in batch.events]
        )

    def _release_pending_keys(self, keys: list[tuple[str, str, int]]) -> None:
        with self._pending_lock:
            for key in keys:
                self._pending_event_keys.discard(key)

    def _set_metric(self, key: str, value: int | float | str | None) -> None:
        with self._metrics_lock:
            self._metrics[key] = value

    def _notify_failure(self, reason: str, payload: dict[str, Any]) -> None:
        if self.failure_callback is None:
            return
        try:
            self.failure_callback(reason, payload)
        except Exception:  # noqa: BLE001 - observer callbacks are never control inputs.
            return
