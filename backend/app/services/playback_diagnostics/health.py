from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any


MAX_HEALTH_REASONS = 256
ERROR_WINDOW_SECONDS = 30.0
ERRORS_BEFORE_REDUCED = 8
ERRORS_BEFORE_OPTIONAL_DISABLED = 16
ERRORS_BEFORE_REDUCED_AGGREGATES = 24
ERRORS_BEFORE_CRITICAL_ONLY = 40
ERRORS_BEFORE_CIRCUIT_OPEN = 64
CAPTURE_MODES = (
    "normal",
    "reduced_sampling",
    "optional_disabled",
    "reduced_aggregates",
    "critical_only",
    "circuit_open",
)
QUEUE_PRESSURE_THRESHOLDS = (0.50, 0.70, 0.82, 0.94, 1.0)
WRITER_LATENCY_THRESHOLDS_MS = (50.0, 125.0, 300.0, 1_000.0, 5_000.0)
HOST_SAMPLER_LATENCY_THRESHOLDS_MS = (50.0, 125.0, 300.0, 1_000.0, 5_000.0)
SYSTEM_PRESSURE_THRESHOLDS = (0.75, 0.85, 0.92, 0.97, 1.0)


@dataclass(slots=True)
class DiagnosticsHealthCounter:
    component: str
    reason_code: str
    count: int
    first_monotonic_ns: int
    last_monotonic_ns: int
    safe_context: str | None = None


class DiagnosticsHealth:
    """Bounded diagnostics self-health that never enters the event stream."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: OrderedDict[tuple[str, str, str], DiagnosticsHealthCounter] = (
            OrderedDict()
        )
        self._recent_errors: list[float] = []
        self._error_mode = "normal"
        self._pressure_mode = "normal"
        self._capture_mode = "normal"
        self._state = "disabled"
        self._queue_state: dict[str, int | float | str | None] = {}

    @property
    def state(self) -> str:
        return self._state

    @property
    def capture_mode(self) -> str:
        return self._capture_mode

    def set_state(self, state: str) -> None:
        self._state = str(state)

    def record(
        self,
        component: str,
        reason_code: str,
        *,
        safe_context: str | None = None,
        error: bool = True,
    ) -> None:
        """Record one bounded reason without ever waiting for a contended lock."""

        if not self._lock.acquire(blocking=False):
            return
        try:
            now_ns = time.monotonic_ns()
            safe_component = str(component or "unknown")[:64]
            safe_reason = str(reason_code or "unknown")[:96]
            safe_value = str(safe_context)[:64] if safe_context else ""
            key = (safe_component, safe_reason, safe_value)
            current = self._counters.get(key)
            if current is None:
                current = DiagnosticsHealthCounter(
                    component=safe_component,
                    reason_code=safe_reason,
                    count=0,
                    first_monotonic_ns=now_ns,
                    last_monotonic_ns=now_ns,
                    safe_context=safe_value or None,
                )
                self._counters[key] = current
            current.count += 1
            current.last_monotonic_ns = now_ns
            self._counters.move_to_end(key)
            while len(self._counters) > MAX_HEALTH_REASONS:
                self._counters.popitem(last=False)
            if error:
                now = now_ns / 1_000_000_000
                cutoff = now - ERROR_WINDOW_SECONDS
                self._recent_errors = [value for value in self._recent_errors if value >= cutoff]
                self._recent_errors.append(now)
                self._error_mode = self._mode_for_error_count(len(self._recent_errors))
                self._refresh_capture_mode()
        finally:
            self._lock.release()

    def update_queues(self, **values: int | float | str | None) -> None:
        """Update diagnostics-owned pressure signals outside playback hot paths."""

        if not self._lock.acquire(blocking=False):
            return
        try:
            self._queue_state = {
                str(key)[:64]: value
                for key, value in values.items()
            }
            now = time.monotonic()
            cutoff = now - ERROR_WINDOW_SECONDS
            self._recent_errors = [value for value in self._recent_errors if value >= cutoff]
            self._error_mode = self._mode_for_error_count(len(self._recent_errors))
            self._pressure_mode = self._mode_for_pressure(self._queue_state)
            self._refresh_capture_mode()
        finally:
            self._lock.release()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "capture_mode": self._capture_mode,
                "counters": [asdict(counter) for counter in self._counters.values()],
                "queues": dict(self._queue_state),
            }

    def reset_error_window(self) -> None:
        with self._lock:
            self._recent_errors.clear()
            self._error_mode = "normal"
            self._pressure_mode = "normal"
            self._capture_mode = "normal"

    def _refresh_capture_mode(self) -> None:
        rank = max(
            self._mode_rank(self._error_mode),
            self._mode_rank(self._pressure_mode),
        )
        self._capture_mode = CAPTURE_MODES[rank]

    @staticmethod
    def _mode_rank(mode: str) -> int:
        try:
            return CAPTURE_MODES.index(mode)
        except ValueError:
            return 0

    @classmethod
    def _mode_for_pressure(cls, values: dict[str, int | float | str | None]) -> str:
        ranks = [0]
        for depth_name, capacity_name in (
            ("ingress_depth", "ingress_capacity"),
            ("writer_depth", "writer_capacity"),
        ):
            depth = cls._finite_nonnegative(values.get(depth_name))
            capacity = cls._finite_nonnegative(values.get(capacity_name))
            if depth is not None and capacity is not None and capacity > 0:
                ranks.append(cls._rank_for_thresholds(depth / capacity, QUEUE_PRESSURE_THRESHOLDS))
        for name, thresholds in (
            ("writer_latency_ms", WRITER_LATENCY_THRESHOLDS_MS),
            ("host_sampler_latency_ms", HOST_SAMPLER_LATENCY_THRESHOLDS_MS),
            ("cpu_pressure_ratio", SYSTEM_PRESSURE_THRESHOLDS),
            ("io_pressure_ratio", SYSTEM_PRESSURE_THRESHOLDS),
            ("memory_pressure_ratio", SYSTEM_PRESSURE_THRESHOLDS),
        ):
            value = cls._finite_nonnegative(values.get(name))
            if value is not None:
                ranks.append(cls._rank_for_thresholds(value, thresholds))
        return CAPTURE_MODES[min(max(ranks), len(CAPTURE_MODES) - 1)]

    @staticmethod
    def _finite_nonnegative(value: int | float | str | None) -> float | None:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if isfinite(result) and result >= 0 else None

    @staticmethod
    def _rank_for_thresholds(value: float, thresholds: tuple[float, ...]) -> int:
        rank = 0
        for index, threshold in enumerate(thresholds, start=1):
            if value < threshold:
                break
            rank = index
        return rank

    @staticmethod
    def _mode_for_error_count(count: int) -> str:
        if count >= ERRORS_BEFORE_CIRCUIT_OPEN:
            return "circuit_open"
        if count >= ERRORS_BEFORE_CRITICAL_ONLY:
            return "critical_only"
        if count >= ERRORS_BEFORE_REDUCED_AGGREGATES:
            return "reduced_aggregates"
        if count >= ERRORS_BEFORE_OPTIONAL_DISABLED:
            return "optional_disabled"
        if count >= ERRORS_BEFORE_REDUCED:
            return "reduced_sampling"
        return "normal"
