from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class _ExecutorTask:
    callback: Callable[[], Any]
    future: Future[Any]


class BoundedDiagnosticsExecutor:
    """Small fixed-worker executor with immediate rejection on saturation."""

    def __init__(self, name: str, *, max_workers: int, max_queue: int) -> None:
        if max_workers < 1 or max_queue < 1:
            raise ValueError("Diagnostics executor limits must be positive")
        self.name = str(name)
        self._queue: queue.Queue[_ExecutorTask | None] = queue.Queue(max_queue)
        self._stop = threading.Event()
        self._workers = [
            threading.Thread(
                target=self._run,
                name=f"{self.name}-{index + 1}",
                daemon=False,
            )
            for index in range(max_workers)
        ]
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        for worker in self._workers:
            worker.start()

    def submit_nowait(self, callback: Callable[[], Any]) -> Future[Any] | None:
        if not self._started or self._stop.is_set():
            return None
        future: Future[Any] = Future()
        try:
            self._queue.put_nowait(_ExecutorTask(callback=callback, future=future))
        except queue.Full:
            return None
        return future

    def shutdown(self, *, timeout: float) -> bool:
        self._stop.set()
        for _worker in self._workers:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                break
        deadline = time.monotonic() + max(0.0, timeout)
        for worker in self._workers:
            remaining = max(0.0, deadline - time.monotonic())
            if worker.is_alive():
                worker.join(timeout=remaining)
        return not self.workers_alive

    @property
    def workers_alive(self) -> bool:
        return any(worker.is_alive() for worker in self._workers)

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    def _run(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                task = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if task is None:
                    continue
                if not task.future.set_running_or_notify_cancel():
                    continue
                try:
                    task.future.set_result(task.callback())
                except BaseException as exc:  # noqa: BLE001 - stored on the task future.
                    task.future.set_exception(exc)
            finally:
                self._queue.task_done()
