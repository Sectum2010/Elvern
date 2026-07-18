from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import IntEnum
import logging
import os
from pathlib import Path
import threading
import time
from typing import Callable

from ..config import Settings
from .poster_display_cache_service import (
    POSTER_CARD_CACHE_ALGORITHM_VERSION,
    PosterDerivativeDisposition,
    PosterDerivativeResult,
    find_cached_card_poster_display_path,
    get_or_create_card_poster_display_result,
)


logger = logging.getLogger(__name__)
POSTER_INTERACTION_PRIORITY_WINDOW_SECONDS = 2.0
POSTER_REQUESTED_BURST_LIMIT = 8
POSTER_METRIC_SAMPLE_LIMIT = 2_048


class PosterDerivativePriority(IntEnum):
    REQUESTED = 0
    NORMAL = 1
    PREWARM = 2


class PosterDerivativeQueueFullError(RuntimeError):
    pass


@dataclass(slots=True)
class _DerivativeJob:
    key: tuple[object, ...]
    source_path: Path
    target_width: int
    priority: PosterDerivativePriority
    prewarm: bool
    sequence: int
    enqueued_at: float
    waiters: set[Future] = field(default_factory=set)
    started: bool = False


def _job_key(settings: Settings, source_path: Path, target_width: int) -> tuple[object, ...]:
    try:
        source_stat = source_path.stat()
        mtime_ns = int(source_stat.st_mtime_ns)
        size = int(source_stat.st_size)
    except OSError:
        mtime_ns = -1
        size = -1
    return (
        str(source_path.resolve()),
        mtime_ns,
        size,
        int(target_width),
        int(settings.poster_card_cache_jpeg_quality),
        POSTER_CARD_CACHE_ALGORITHM_VERSION,
    )


class PosterDerivativeManager:
    def __init__(
        self,
        settings: Settings,
        *,
        generation_function: Callable = get_or_create_card_poster_display_result,
        cache_lookup_function: Callable = find_cached_card_poster_display_path,
        interactive_window_seconds: float = POSTER_INTERACTION_PRIORITY_WINDOW_SECONDS,
    ) -> None:
        self.settings = settings
        self.worker_count = int(settings.poster_generation_workers)
        self.queue_max = int(settings.poster_generation_queue_max)
        self._generate = generation_function
        self._lookup_cache = cache_lookup_function
        self._interactive_window_seconds = max(0.0, float(interactive_window_seconds))
        self._condition = threading.Condition()
        self._executor = ThreadPoolExecutor(
            max_workers=self.worker_count,
            thread_name_prefix="elvern-poster",
        )
        self._dispatcher: threading.Thread | None = None
        self._started = False
        self._stopping = False
        self._active_workers = 0
        self._sequence = 0
        self._requested_burst = 0
        self._interactive_until = 0.0
        self._jobs: dict[tuple[object, ...], _DerivativeJob] = {}
        self._queued: list[_DerivativeJob] = []
        self._stats = {
            "cache_hits": 0,
            "generated": 0,
            "generation_failures": 0,
            "single_flight_collapsed": 0,
            "dropped_prewarm": 0,
            "queue_rejected": 0,
            "cancelled_before_start": 0,
            "queued_peak": 0,
            "active_worker_peak": 0,
            "queue_wait_seconds": deque(maxlen=POSTER_METRIC_SAMPLE_LIMIT),
            "generation_seconds": deque(maxlen=POSTER_METRIC_SAMPLE_LIMIT),
        }
        self._timing_totals = {
            "queue_wait_seconds": {"count": 0, "sum": 0.0, "max": 0.0},
            "generation_seconds": {"count": 0, "sum": 0.0, "max": 0.0},
        }
        self._disposition_counts = {value.value: 0 for value in PosterDerivativeDisposition}
        cpu_count = os.cpu_count() or 0
        if cpu_count and self.worker_count > cpu_count:
            logger.warning(
                "ELVERN_POSTER_GENERATION_WORKERS=%s exceeds detected CPU count=%s; the configured value is unchanged.",
                self.worker_count,
                cpu_count,
            )

    def start(self) -> None:
        with self._condition:
            if self._started:
                return
            self._started = True
            self._dispatcher = threading.Thread(
                target=self._dispatch_loop,
                name="elvern-poster-dispatch",
                daemon=True,
            )
            self._dispatcher.start()

    def shutdown(self) -> None:
        with self._condition:
            if not self._started:
                return
            self._stopping = True
            self._condition.notify_all()
        if self._dispatcher is not None:
            self._dispatcher.join(timeout=10)
        self._executor.shutdown(wait=True, cancel_futures=False)
        with self._condition:
            self._started = False
            self._dispatcher = None

    def enter_interactive_window(self) -> None:
        with self._condition:
            self._interactive_until = max(
                self._interactive_until,
                time.monotonic() + self._interactive_window_seconds,
            )
            self._condition.notify_all()

    def submit_result(
        self,
        source_path: Path | str,
        *,
        target_width: int,
        priority: PosterDerivativePriority = PosterDerivativePriority.REQUESTED,
        prewarm: bool = False,
    ) -> Future:
        path = Path(source_path)
        resolved_priority = PosterDerivativePriority(priority)
        waiter: Future = Future()
        try:
            cached = self._lookup_cache(
                self.settings,
                path,
                target_width=int(target_width),
            )
        except Exception:
            cached = None
        if cached is not None:
            with self._condition:
                self._stats["cache_hits"] += 1
                self._disposition_counts[
                    PosterDerivativeDisposition.DERIVATIVE_CACHE_HIT.value
                ] += 1
            waiter.set_result(PosterDerivativeResult(
                path=Path(cached),
                disposition=PosterDerivativeDisposition.DERIVATIVE_CACHE_HIT,
                immutable=True,
            ))
            return waiter

        if not self._started:
            self.start()
        key = _job_key(self.settings, path, int(target_width))
        with self._condition:
            existing = self._jobs.get(key)
            if existing is not None:
                if not existing.started and resolved_priority < existing.priority:
                    existing.priority = resolved_priority
                    existing.prewarm = existing.prewarm and prewarm
                existing.waiters.add(waiter)
                self._stats["single_flight_collapsed"] += 1
                waiter.add_done_callback(lambda future, job_key=key: self._handle_waiter_done(job_key, future))
                self._condition.notify_all()
                return waiter

            if len(self._queued) >= self.queue_max:
                dropped = self._drop_queued_prewarm_locked() if resolved_priority != PosterDerivativePriority.PREWARM else None
                if dropped is None:
                    self._stats["queue_rejected"] += 1
                    waiter.set_exception(PosterDerivativeQueueFullError("Poster derivative queue is full"))
                    return waiter

            self._sequence += 1
            job = _DerivativeJob(
                key=key,
                source_path=path,
                target_width=int(target_width),
                priority=resolved_priority,
                prewarm=bool(prewarm),
                sequence=self._sequence,
                enqueued_at=time.monotonic(),
                waiters={waiter},
            )
            self._jobs[key] = job
            self._queued.append(job)
            self._stats["queued_peak"] = max(self._stats["queued_peak"], len(self._queued))
            waiter.add_done_callback(lambda future, job_key=key: self._handle_waiter_done(job_key, future))
            self._condition.notify_all()
        return waiter

    def submit(
        self,
        source_path: Path | str,
        *,
        target_width: int,
        priority: PosterDerivativePriority = PosterDerivativePriority.REQUESTED,
        prewarm: bool = False,
    ) -> Future:
        result_waiter = self.submit_result(
            source_path,
            target_width=target_width,
            priority=priority,
            prewarm=prewarm,
        )
        path_waiter: Future = Future()

        def copy_result(completed: Future) -> None:
            if path_waiter.done():
                return
            try:
                path_waiter.set_result(completed.result().path)
            except Exception as exc:
                path_waiter.set_exception(exc)

        def propagate_cancel(completed: Future) -> None:
            if completed.cancelled() and not result_waiter.done():
                result_waiter.cancel()

        result_waiter.add_done_callback(copy_result)
        path_waiter.add_done_callback(propagate_cancel)
        return path_waiter

    async def get_or_create_result(
        self,
        source_path: Path | str,
        *,
        target_width: int,
        priority: PosterDerivativePriority = PosterDerivativePriority.REQUESTED,
    ) -> PosterDerivativeResult:
        waiter = self.submit_result(
            source_path,
            target_width=target_width,
            priority=priority,
        )
        try:
            return await asyncio.wrap_future(waiter)
        except asyncio.CancelledError:
            waiter.cancel()
            raise

    async def get_or_create(
        self,
        source_path: Path | str,
        *,
        target_width: int,
        priority: PosterDerivativePriority = PosterDerivativePriority.REQUESTED,
    ) -> Path:
        result = await self.get_or_create_result(
            source_path,
            target_width=target_width,
            priority=priority,
        )
        return result.path

    def prewarm(self, source_path: Path | str, *, target_width: int) -> Future:
        return self.submit(
            source_path,
            target_width=target_width,
            priority=PosterDerivativePriority.PREWARM,
            prewarm=True,
        )

    def snapshot_stats(self) -> dict[str, object]:
        with self._condition:
            snapshot = {
                key: list(value) if isinstance(value, deque) else value
                for key, value in self._stats.items()
            }
            for metric_name, totals in self._timing_totals.items():
                samples = sorted(self._stats[metric_name])
                count = int(totals["count"])
                snapshot[f"{metric_name}_summary"] = {
                    "count": count,
                    "mean": (float(totals["sum"]) / count) if count else 0.0,
                    "max": float(totals["max"]),
                    "p50": self._sample_percentile(samples, 0.50),
                    "p90": self._sample_percentile(samples, 0.90),
                }
            snapshot["disposition_counts"] = dict(self._disposition_counts)
            return snapshot

    @staticmethod
    def _sample_percentile(samples: list[float], percentile: float) -> float:
        if not samples:
            return 0.0
        index = min(len(samples) - 1, max(0, round((len(samples) - 1) * percentile)))
        return float(samples[index])

    def _record_timing_locked(self, metric_name: str, value: float) -> None:
        normalized = max(0.0, float(value))
        self._stats[metric_name].append(normalized)
        totals = self._timing_totals[metric_name]
        totals["count"] += 1
        totals["sum"] += normalized
        totals["max"] = max(float(totals["max"]), normalized)

    def _drop_queued_prewarm_locked(self) -> _DerivativeJob | None:
        candidates = [job for job in self._queued if job.priority == PosterDerivativePriority.PREWARM]
        if not candidates:
            return None
        dropped = max(candidates, key=lambda job: job.sequence)
        self._queued.remove(dropped)
        self._jobs.pop(dropped.key, None)
        self._stats["dropped_prewarm"] += 1
        error = PosterDerivativeQueueFullError("Poster prewarm was dropped for interactive work")
        for waiter in tuple(dropped.waiters):
            if not waiter.done():
                waiter.set_exception(error)
        return dropped

    def _handle_waiter_done(self, key: tuple[object, ...], waiter: Future) -> None:
        if not waiter.cancelled():
            return
        with self._condition:
            job = self._jobs.get(key)
            if job is None:
                return
            job.waiters.discard(waiter)
            if not job.started and not job.waiters and not job.prewarm:
                if job in self._queued:
                    self._queued.remove(job)
                self._jobs.pop(key, None)
                self._stats["cancelled_before_start"] += 1
                self._condition.notify_all()

    def _eligible_jobs_locked(self) -> list[_DerivativeJob]:
        interactive = not self._stopping and time.monotonic() < self._interactive_until
        if interactive:
            return [job for job in self._queued if job.priority == PosterDerivativePriority.REQUESTED]
        return list(self._queued)

    def _take_next_job_locked(self) -> _DerivativeJob | None:
        eligible = self._eligible_jobs_locked()
        if not eligible:
            return None
        normal_jobs = [job for job in eligible if job.priority == PosterDerivativePriority.NORMAL]
        if self._requested_burst >= POSTER_REQUESTED_BURST_LIMIT and normal_jobs:
            selected = min(normal_jobs, key=lambda job: job.sequence)
            self._requested_burst = 0
        else:
            selected = min(eligible, key=lambda job: (int(job.priority), job.sequence))
            if selected.priority == PosterDerivativePriority.REQUESTED:
                self._requested_burst += 1
            elif selected.priority == PosterDerivativePriority.NORMAL:
                self._requested_burst = 0
        self._queued.remove(selected)
        selected.started = True
        return selected

    def _dispatch_loop(self) -> None:
        while True:
            with self._condition:
                if self._stopping and not self._queued and self._active_workers == 0:
                    return
                if self._active_workers >= self.worker_count:
                    self._condition.wait(timeout=0.1)
                    continue
                job = self._take_next_job_locked()
                if job is None:
                    wait_for = 0.1
                    if self._interactive_until > time.monotonic():
                        wait_for = min(wait_for, max(0.001, self._interactive_until - time.monotonic()))
                    self._condition.wait(timeout=wait_for)
                    continue
                self._active_workers += 1
                self._stats["active_worker_peak"] = max(
                    self._stats["active_worker_peak"],
                    self._active_workers,
                )
                self._record_timing_locked("queue_wait_seconds", time.monotonic() - job.enqueued_at)
            worker_future = self._executor.submit(self._run_job, job)
            worker_future.add_done_callback(lambda completed, queued_job=job: self._finish_job(queued_job, completed))

    def _run_job(self, job: _DerivativeJob) -> PosterDerivativeResult:
        started_at = time.monotonic()
        try:
            generated = self._generate(
                self.settings,
                job.source_path,
                target_width=job.target_width,
            )
            if isinstance(generated, PosterDerivativeResult):
                return generated
            return PosterDerivativeResult(
                path=Path(generated),
                disposition=PosterDerivativeDisposition.DERIVATIVE_GENERATED,
                immutable=True,
            )
        finally:
            with self._condition:
                self._record_timing_locked("generation_seconds", time.monotonic() - started_at)

    def _finish_job(self, job: _DerivativeJob, completed: Future) -> None:
        try:
            result = completed.result()
            error = None
        except Exception as exc:
            result = None
            error = exc
        with self._condition:
            self._active_workers -= 1
            self._jobs.pop(job.key, None)
            if (
                error is None
                and result.disposition
                != PosterDerivativeDisposition.FALLBACK_GENERATION_ERROR
            ):
                self._stats["generated"] += 1
                self._disposition_counts[result.disposition.value] += 1
            else:
                self._stats["generation_failures"] += 1
                if result is not None:
                    self._disposition_counts[result.disposition.value] += 1
            waiters = tuple(job.waiters)
            self._condition.notify_all()
        for waiter in waiters:
            if waiter.done():
                continue
            if error is None:
                waiter.set_result(result)
            else:
                waiter.set_exception(error)
