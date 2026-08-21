from __future__ import annotations

import itertools
import math
import os
import queue
import threading
import time
import weakref
from dataclasses import dataclass
from typing import Any, Mapping


MAX_CAPTURE_FIELDS = 48
MAX_CAPTURE_COLLECTION_ITEMS = 24
MAX_CAPTURE_STRING_BYTES = 512
MAX_CAPTURE_DEPTH = 2

_active_target_ref: weakref.ReferenceType[Any] | None = None
_process_prefix = f"diag-{os.getpid():x}-{time.monotonic_ns():x}"
_correlation_counter = itertools.count(1)


BoundedValue = str | int | float | bool | None | tuple[Any, ...]


class NonBlockingDiagnosticsIngressQueue(queue.Queue):
    """Bounded queue whose producer path rejects lock contention immediately."""

    def __init__(self, maxsize: int) -> None:
        super().__init__(maxsize=maxsize)
        self._pending_by_session: dict[str, int] = {}

    def put_nowait(self, item: Any) -> None:
        if not self.mutex.acquire(blocking=False):
            raise queue.Full
        try:
            if self.maxsize > 0 and self._qsize() >= self.maxsize:
                raise queue.Full
            self._put(item)
            self.unfinished_tasks += 1
            session_id = _observation_session_id(item)
            if session_id:
                self._pending_by_session[session_id] = (
                    self._pending_by_session.get(session_id, 0) + 1
                )
            self.not_empty.notify()
        finally:
            self.mutex.release()

    def mark_processed(self, item: Any) -> None:
        session_id = _observation_session_id(item)
        with self.all_tasks_done:
            if self.unfinished_tasks <= 0:
                raise ValueError("task_done() called too many times")
            self.unfinished_tasks -= 1
            if session_id:
                pending = max(0, self._pending_by_session.get(session_id, 0) - 1)
                if pending:
                    self._pending_by_session[session_id] = pending
                else:
                    self._pending_by_session.pop(session_id, None)
            if self.unfinished_tasks == 0:
                self.all_tasks_done.notify_all()
            else:
                self.all_tasks_done.notify_all()

    def wait_for_session(self, session_id: str, *, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self.all_tasks_done:
            while self._pending_by_session.get(session_id, 0):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.all_tasks_done.wait(timeout=remaining)
            return True


def _observation_session_id(item: Any) -> str:
    if isinstance(item, CapturedDiagnosticObservation):
        return item.playback_session_id
    if isinstance(item, Mapping):
        return str(item.get("playback_session_id") or "")
    return ""


@dataclass(frozen=True, slots=True)
class CapturedDiagnosticObservation:
    event_name: str
    playback_session_id: str
    event_source: str
    observation_kind: str
    priority: str
    severity: str
    payload: tuple[tuple[str, BoundedValue], ...]
    identities: tuple[tuple[str, BoundedValue], ...]
    captured_wall_time_ns: int
    captured_monotonic_ns: int

    def payload_dict(self) -> dict[str, Any]:
        return {key: _thaw(value) for key, value in self.payload}

    def identities_dict(self) -> dict[str, Any]:
        return {key: _thaw(value) for key, value in self.identities}


def set_diagnostic_ingress_target(target: Any | None) -> None:
    global _active_target_ref
    _active_target_ref = weakref.ref(target) if target is not None else None


def next_diagnostic_correlation_id(kind: str) -> str:
    safe_kind = "".join(character for character in str(kind) if character.isalnum())[:16]
    return f"{safe_kind or 'event'}-{_process_prefix}-{next(_correlation_counter):x}"


def try_capture_diagnostic_observation(
    event_name: str,
    *,
    playback_session_id: str | None,
    event_source: str = "server",
    observation_kind: str = "measured_server",
    priority: str = "normal",
    severity: str = "info",
    payload: Mapping[str, Any] | None = None,
    **identities: Any,
) -> str | None:
    """The only playback-facing diagnostics ingress boundary.

    It performs bounded in-memory snapshotting and an immediate queue offer. It
    never performs diagnostics storage work and never raises into playback.
    """

    session_id = _bounded_string(playback_session_id, 128)
    if not session_id:
        return None
    target_ref = _active_target_ref
    target = target_ref() if target_ref is not None else None
    if target is None:
        return None
    return capture_diagnostic_observation_for_target(
        target,
        event_name,
        playback_session_id=session_id,
        event_source=event_source,
        observation_kind=observation_kind,
        priority=priority,
        severity=severity,
        payload=payload,
        **identities,
    )


def capture_diagnostic_observation_for_target(
    target: Any,
    event_name: str,
    *,
    playback_session_id: str | None,
    event_source: str = "server",
    observation_kind: str = "measured_server",
    priority: str = "normal",
    severity: str = "info",
    payload: Mapping[str, Any] | None = None,
    **identities: Any,
) -> str | None:
    session_id = _bounded_string(playback_session_id, 128)
    if not session_id:
        return None
    captured_wall_ns = time.time_ns()
    captured_ns = time.monotonic_ns()
    try:
        observation = CapturedDiagnosticObservation(
            event_name=_bounded_string(event_name, 128) or "invalid_event",
            playback_session_id=session_id,
            event_source=_bounded_string(event_source, 32) or "server",
            observation_kind=_bounded_string(observation_kind, 32) or "measured_server",
            priority=_bounded_string(priority, 16) or "normal",
            severity=_bounded_string(severity, 16) or "info",
            payload=_freeze_mapping(payload or {}),
            identities=_freeze_mapping(identities),
            captured_wall_time_ns=captured_wall_ns,
            captured_monotonic_ns=captured_ns,
        )
        accepted = bool(target.try_capture_observation(observation))
        return next_diagnostic_correlation_id("capture") if accepted else None
    except BaseException:  # noqa: BLE001 - diagnostics cannot alter playback.
        try:
            target.health.record("ingress", "capture_target_failed")
        except BaseException:  # noqa: BLE001 - health is also observer-only.
            pass
        return None


def _freeze_mapping(values: Mapping[str, Any]) -> tuple[tuple[str, BoundedValue], ...]:
    frozen: list[tuple[str, BoundedValue]] = []
    for raw_key, raw_value in itertools.islice(values.items(), MAX_CAPTURE_FIELDS):
        key = _bounded_string(raw_key, 96)
        if not key:
            continue
        frozen.append((key, _freeze_value(raw_value, depth=0)))
    return tuple(frozen)


def _freeze_value(value: Any, *, depth: int) -> BoundedValue:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _bounded_string(value, MAX_CAPTURE_STRING_BYTES)
    if depth >= MAX_CAPTURE_DEPTH:
        return _bounded_string(type(value).__name__, 64)
    if isinstance(value, Mapping):
        return tuple(
            (key, _freeze_value(item, depth=depth + 1))
            for key, item in _freeze_mapping(value)[:MAX_CAPTURE_COLLECTION_ITEMS]
        )
    if isinstance(value, (tuple, list, set, frozenset)):
        return tuple(
            _freeze_value(item, depth=depth + 1)
            for item in itertools.islice(value, MAX_CAPTURE_COLLECTION_ITEMS)
        )
    return _bounded_string(type(value).__name__, 64)


def _thaw(value: BoundedValue) -> Any:
    if not isinstance(value, tuple):
        return value
    if value and all(
        isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str)
        for item in value
    ):
        return {item[0]: _thaw(item[1]) for item in value}
    return [_thaw(item) for item in value]


def _bounded_string(value: Any, max_bytes: int) -> str:
    text = str(value or "")
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")
