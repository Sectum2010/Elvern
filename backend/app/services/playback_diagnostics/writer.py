from __future__ import annotations

import json
import hashlib
import logging
import queue
import threading
import time
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from .capacity import DiagnosticsCapacityError, DiagnosticsCapacityGuard
from .catalog import DiagnosticsCatalog
from .constants import (
    CRITICAL_EVENT_NAMES,
    DIAGNOSTICS_CATALOG_MUTATION_RESERVATION_BYTES,
)
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
    completion: Future["DiagnosticsWriteReceipt"] | None = None


@dataclass(frozen=True, slots=True)
class DiagnosticsEnqueueResult:
    accepted: int
    duplicate: int
    queued: bool

    def __bool__(self) -> bool:
        return self.queued


@dataclass(frozen=True, slots=True)
class DiagnosticsWriteReceipt:
    accepted: int
    duplicate: int
    out_of_order: int
    ack_watermark: int
    journal_chunk_sequence: int | None = None


class DiagnosticsWriterError(RuntimeError):
    """A retriable failure before a durable diagnostics ACK."""


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
        self._pending_event_ids: set[str] = set()
        self._pending_source_sequences: dict[tuple[str, int], str] = {}
        self._session_pending_condition = threading.Condition()
        self._pending_batches_by_session: dict[str, int] = {}
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
        self._last_status_write_monotonic = 0.0
        self._unindexed_sources: set[tuple[str, str]] = set()

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
        seen_ids: dict[str, int] = {}
        seen_sequences: dict[int, str] = {}
        with self._pending_lock:
            for event in batch.events:
                key = self._event_key(batch.source_id, event)
                event_id, source_id, sequence = key
                if event_id in seen_ids and seen_ids[event_id] != sequence:
                    raise ValueError("Diagnostics event id is reused with another sequence")
                if sequence in seen_sequences and seen_sequences[sequence] != event_id:
                    raise ValueError("Diagnostics batch contains conflicting source sequences")
                if event_id in seen_ids or sequence in seen_sequences:
                    duplicate += 1
                    continue
                seen_ids[event_id] = sequence
                seen_sequences[sequence] = event_id
                pending_sequence_key = (source_id, sequence)
                pending_event_id = self._pending_source_sequences.get(pending_sequence_key)
                if pending_event_id is not None and pending_event_id != event_id:
                    raise ValueError("Diagnostics source sequence is already pending with another event")
                if event_id in self._pending_event_ids or pending_event_id == event_id:
                    duplicate += 1
                    continue
                self._pending_event_ids.add(event_id)
                self._pending_source_sequences[pending_sequence_key] = event_id
                accepted_keys.append(key)
                accepted_events.append(event)
        if not accepted_events:
            self._increment("events_duplicate", duplicate)
            return DiagnosticsEnqueueResult(accepted=0, duplicate=duplicate, queued=False)
        queued_batch = replace(batch, events=tuple(accepted_events))
        self._increment_session_pending(batch.playback_session_id)
        try:
            self._queue.put_nowait(queued_batch)
        except queue.Full:
            self._decrement_session_pending(batch.playback_session_id)
            self._release_pending_keys(accepted_keys)
            self._increment("events_dropped", len(accepted_events))
            self._notify_failure(
                "writer_queue_full",
                {
                    "playback_session_id": batch.playback_session_id,
                    "events_dropped": len(accepted_events),
                },
            )
            if batch.completion is not None and not batch.completion.done():
                batch.completion.set_exception(DiagnosticsWriterError("Diagnostics writer queue is full"))
            return DiagnosticsEnqueueResult(accepted=0, duplicate=duplicate, queued=False)
        if duplicate:
            self._increment("events_duplicate", duplicate)
        self._increment("batches_enqueued", 1)
        return DiagnosticsEnqueueResult(
            accepted=len(accepted_events),
            duplicate=duplicate,
            queued=True,
        )

    def write_and_wait(
        self,
        batch: DiagnosticsWriteBatch,
        *,
        timeout: float = 10.0,
    ) -> DiagnosticsWriteReceipt:
        completion: Future[DiagnosticsWriteReceipt] = Future()
        submitted = replace(batch, completion=completion)
        enqueue_result = self.enqueue(submitted)
        if not enqueue_result.queued:
            if completion.done():
                return completion.result()
            return DiagnosticsWriteReceipt(
                accepted=0,
                duplicate=enqueue_result.duplicate,
                out_of_order=0,
                ack_watermark=self.catalog.ack_watermark(batch.source_id),
            )
        try:
            return completion.result(timeout=timeout)
        except FutureTimeoutError as exc:
            raise DiagnosticsWriterError("Timed out waiting for durable diagnostics write") from exc

    def flush(self, *, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0

    def flush_session(self, playback_session_id: str, *, timeout: float = 5.0) -> bool:
        """Wait for durable writer completions belonging to one session."""

        deadline = time.monotonic() + timeout
        with self._session_pending_condition:
            while self._pending_batches_by_session.get(playback_session_id, 0):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._session_pending_condition.wait(timeout=remaining)
            return True

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
                receipt = self._write_batch(batch)
                if batch.completion is not None and not batch.completion.done():
                    batch.completion.set_result(receipt)
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
                if batch is not None and batch.completion is not None and not batch.completion.done():
                    batch.completion.set_exception(exc)
            finally:
                if batch is not None:
                    self._release_pending_batch(batch)
                    self._decrement_session_pending(batch.playback_session_id)
                self._queue.task_done()

    def _write_batch(self, batch: DiagnosticsWriteBatch) -> DiagnosticsWriteReceipt:
        started_ns = time.monotonic_ns()
        queue_wait_ms = max(0.0, (started_ns - batch.enqueued_monotonic_ns) / 1_000_000)
        source_key = (batch.playback_session_id, batch.source_id)
        if source_key in self._unindexed_sources:
            self._recover_unindexed_source(batch)
        seen: set[tuple[str, str, int]] = set()
        unique_events: list[dict[str, Any]] = []
        for event in batch.events:
            key = self._event_key(batch.source_id, event)
            if key in seen:
                continue
            classification = self.catalog.classify_event(*key)
            if classification == "duplicate":
                continue
            if classification == "conflict":
                raise ValueError("Conflicting diagnostics event identity or source sequence")
            seen.add(key)
            unique_events.append(event)
        unique_events_tuple = tuple(unique_events)
        duplicate_before_write = len(batch.events) - len(unique_events_tuple)
        if duplicate_before_write:
            self._increment("events_duplicate", duplicate_before_write)
        if not unique_events_tuple:
            return DiagnosticsWriteReceipt(
                accepted=0,
                duplicate=duplicate_before_write,
                out_of_order=0,
                ack_watermark=self.catalog.ack_watermark(batch.source_id),
            )

        events_to_write = unique_events_tuple
        estimated_bytes = self._estimated_batch_bytes(events_to_write)
        critical = all(self._is_critical_event(event) for event in events_to_write)
        try:
            reservation = self.capacity.reserve(estimated_bytes, critical=critical)
        except DiagnosticsCapacityError as exc:
            critical_events = tuple(
                event for event in events_to_write if self._is_critical_event(event)
            )
            if critical_events and len(critical_events) < len(events_to_write):
                dropped = len(events_to_write) - len(critical_events)
                self._increment("events_dropped", dropped)
                self._notify_failure(
                    str(exc),
                    {
                        "playback_session_id": batch.playback_session_id,
                        "events_dropped": dropped,
                        "capacity_state": str(exc),
                    },
                )
                return self._write_batch(replace(batch, events=critical_events))
            self._increment("events_dropped", len(events_to_write))
            self._notify_failure(
                str(exc),
                {
                    "playback_session_id": batch.playback_session_id,
                    "events_dropped": len(events_to_write),
                    "capacity_state": str(exc),
                },
            )
            raise DiagnosticsWriterError(str(exc)) from exc

        catalog_size_before = self._catalog_storage_size()
        journal = None
        journal_size_before = 0
        chunk = None
        try:
            journal = self._journal_for(batch)
            journal_size_before = journal.path.stat().st_size if journal.path.exists() else 0
            chunk = journal.append(events_to_write)
            if chunk is None:
                reservation.release()
                return DiagnosticsWriteReceipt(
                    accepted=0,
                    duplicate=duplicate_before_write,
                    out_of_order=0,
                    ack_watermark=self.catalog.ack_watermark(batch.source_id),
                )
            relative_journal_path = str(journal.path.relative_to(self.root))
            inserted, catalog_duplicates, out_of_order = self.catalog.record_events(
                playback_session_id=batch.playback_session_id,
                source_id=batch.source_id,
                source_type=batch.source_type,
                journal_relative_path=relative_journal_path,
                journal_chunk_sequence=chunk.sequence,
                journal_chunk_hash=chunk.current_chunk_hash,
                events=events_to_write,
            )
        except Exception:
            journal_delta = (
                max(0, journal.path.stat().st_size - journal_size_before)
                if journal is not None and journal.path.exists()
                else 0
            )
            if chunk is not None or journal_delta > 0:
                # A failed fsync has indeterminate durability, but flushed bytes may
                # already form a valid record. Re-verify and reindex before any retry.
                self._unindexed_sources.add(source_key)
                self._journals.pop(source_key, None)
            catalog_delta = max(0, self._catalog_storage_size() - catalog_size_before)
            reservation.commit(journal_delta + catalog_delta)
            raise
        self._unindexed_sources.discard(source_key)
        journal_delta = max(0, journal.path.stat().st_size - journal_size_before)
        catalog_delta = max(0, self._catalog_storage_size() - catalog_size_before)
        reservation.commit(journal_delta + catalog_delta)
        elapsed_ms = (time.monotonic_ns() - started_ns) / 1_000_000
        self._increment("batches_written", 1)
        self._increment("events_written", inserted)
        self._increment("events_duplicate", catalog_duplicates)
        self._increment("events_out_of_order", out_of_order)
        self._set_metric("writer_latency_ms", elapsed_ms)
        self._set_metric("queue_wait_ms", queue_wait_ms)
        self._maybe_write_status(elapsed_ms)
        return DiagnosticsWriteReceipt(
            accepted=inserted,
            duplicate=duplicate_before_write + catalog_duplicates,
            out_of_order=out_of_order,
            ack_watermark=self.catalog.ack_watermark(batch.source_id),
            journal_chunk_sequence=chunk.sequence,
        )

    def _journal_for(self, batch: DiagnosticsWriteBatch) -> EncryptedJournal:
        key = (batch.playback_session_id, batch.source_id)
        existing = self._journals.get(key)
        if existing is not None and existing.path.stat().st_size < JOURNAL_ROTATE_BYTES:
            return existing
        session_path = resolve_beneath(self.root, batch.session_relative_path)
        raw_path = ensure_private_directory(resolve_beneath(session_path, "raw"))
        sequence = 1
        while True:
            source_token = hashlib.sha256(batch.source_id.encode("utf-8")).hexdigest()[:20]
            candidate = resolve_beneath(
                raw_path,
                f"{batch.source_type}-{source_token}-{sequence:06d}.elvd",
            )
            if not candidate.exists() or candidate.stat().st_size < JOURNAL_ROTATE_BYTES:
                break
            sequence += 1
        journal = EncryptedJournal(
            candidate,
            playback_session_id=batch.playback_session_id,
            source_type=batch.source_type,
            source_id=batch.source_id,
            key_store=self.key_store,
            active_key=self.active_key,
            quarantine_root=resolve_beneath(self.root, "quarantine"),
            capacity=self.capacity,
        )
        self._journals[key] = journal
        return journal

    def _recover_unindexed_source(self, batch: DiagnosticsWriteBatch) -> None:
        """Rebuild missing catalog rows after a durable journal/catalog split."""

        source_token = hashlib.sha256(batch.source_id.encode("utf-8")).hexdigest()[:20]
        raw_path = resolve_beneath(
            resolve_beneath(self.root, batch.session_relative_path),
            "raw",
        )
        if not raw_path.is_dir() or raw_path.is_symlink():
            self._unindexed_sources.discard((batch.playback_session_id, batch.source_id))
            return
        from .journal import verify_journal

        for journal_path in sorted(raw_path.glob(f"{batch.source_type}-{source_token}-*.elvd")):
            verification, events = verify_journal(
                journal_path,
                self.key_store,
                include_events=True,
                annotate_events=True,
                expected_playback_session_id=batch.playback_session_id,
                expected_source_id=batch.source_id,
                expected_source_type=batch.source_type,
                capacity=self.capacity,
            )
            if not verification.valid:
                raise DiagnosticsWriterError(
                    verification.error or "Unindexed diagnostics journal is invalid"
                )
            grouped: dict[int, list[dict[str, Any]]] = {}
            hashes: dict[int, str] = {}
            for event in events:
                chunk_sequence = int(event.pop("_journal_chunk_sequence", 0))
                hashes[chunk_sequence] = str(event.pop("_journal_chunk_hash", ""))
                grouped.setdefault(chunk_sequence, []).append(event)
            relative_path = str(journal_path.relative_to(self.root))
            for chunk_sequence in sorted(grouped):
                recovered_events = tuple(grouped[chunk_sequence])
                reservation = self.capacity.reserve(
                    DIAGNOSTICS_CATALOG_MUTATION_RESERVATION_BYTES
                    + len(recovered_events) * 16_384,
                    critical=True,
                )
                catalog_size_before = self._catalog_storage_size()
                try:
                    self.catalog.record_events(
                        playback_session_id=batch.playback_session_id,
                        source_id=batch.source_id,
                        source_type=batch.source_type,
                        journal_relative_path=relative_path,
                        journal_chunk_sequence=chunk_sequence,
                        journal_chunk_hash=hashes.get(chunk_sequence, ""),
                        events=recovered_events,
                    )
                except Exception:
                    reservation.commit(
                        max(0, self._catalog_storage_size() - catalog_size_before)
                    )
                    raise
                reservation.commit(
                    max(0, self._catalog_storage_size() - catalog_size_before)
                )
        source_key = (batch.playback_session_id, batch.source_id)
        self._unindexed_sources.discard(source_key)
        # Recovery is authoritative for the on-disk chain. Reconstruct the cached
        # journal so its sequence/hash state cannot lag behind recovered records.
        self._journals.pop(source_key, None)

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
        plaintext_bytes = sum(
            len(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            for event in events
        ) + len(events)
        # The reservation covers the encrypted journal record plus a deliberately
        # conservative SQLite/WAL page allowance. It must be an upper bound, not
        # an estimate of the likely compressed size.
        journal_upper_bound = plaintext_bytes * 2 + 16_384
        catalog_upper_bound = (
            DIAGNOSTICS_CATALOG_MUTATION_RESERVATION_BYTES
            + len(events) * 16_384
        )
        return journal_upper_bound + catalog_upper_bound

    def _release_pending_batch(self, batch: DiagnosticsWriteBatch) -> None:
        self._release_pending_keys(
            [self._event_key(batch.source_id, event) for event in batch.events]
        )

    def _release_pending_keys(self, keys: list[tuple[str, str, int]]) -> None:
        with self._pending_lock:
            for event_id, source_id, sequence in keys:
                self._pending_event_ids.discard(event_id)
                pending_key = (source_id, sequence)
                if self._pending_source_sequences.get(pending_key) == event_id:
                    self._pending_source_sequences.pop(pending_key, None)

    def _increment_session_pending(self, playback_session_id: str) -> None:
        with self._session_pending_condition:
            self._pending_batches_by_session[playback_session_id] = (
                self._pending_batches_by_session.get(playback_session_id, 0) + 1
            )

    def _decrement_session_pending(self, playback_session_id: str) -> None:
        with self._session_pending_condition:
            remaining = max(
                0,
                self._pending_batches_by_session.get(playback_session_id, 0) - 1,
            )
            if remaining:
                self._pending_batches_by_session[playback_session_id] = remaining
            else:
                self._pending_batches_by_session.pop(playback_session_id, None)
            self._session_pending_condition.notify_all()

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

    def _catalog_storage_size(self) -> int:
        return sum(
            candidate.stat().st_size
            for candidate in (
                self.catalog.path,
                Path(f"{self.catalog.path}-wal"),
                Path(f"{self.catalog.path}-shm"),
            )
            if candidate.exists() and candidate.is_file() and not candidate.is_symlink()
        )

    def _maybe_write_status(self, elapsed_ms: float) -> None:
        now = time.monotonic()
        if now - self._last_status_write_monotonic < 1.0:
            return
        self._last_status_write_monotonic = now
        try:
            self.capacity.write_current_status(
                enabled=True,
                writer_queue_depth=self._queue.qsize(),
                writer_latency_ms=round(elapsed_ms, 3),
                last_error_class=self.metrics().get("last_error_class"),
            )
        except Exception:  # noqa: BLE001 - status coalescing cannot invalidate a durable ACK.
            return
