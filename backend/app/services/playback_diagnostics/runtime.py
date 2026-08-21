from __future__ import annotations

import threading
import time
import weakref
from collections import OrderedDict
from typing import Any

from .ingress import (
    set_diagnostic_ingress_target,
    try_capture_diagnostic_observation,
)


_lock = threading.RLock()
_active_service_ref: weakref.ReferenceType[Any] | None = None
_native_stream_contexts: OrderedDict[str, tuple[str, float]] = OrderedDict()
_NATIVE_CONTEXT_TTL_SECONDS = 24 * 60 * 60
_NATIVE_CONTEXT_MAX_ENTRIES = 4_096


def set_active_diagnostics_service(service: Any | None) -> None:
    global _active_service_ref
    with _lock:
        _active_service_ref = weakref.ref(service) if service is not None else None
    set_diagnostic_ingress_target(service)


def get_active_diagnostics_service() -> Any | None:
    active_ref = _active_service_ref
    return active_ref() if active_ref is not None else None


def register_native_stream_context(native_session_id: str, playback_session_id: str) -> None:
    """Correlate an existing internal stream without retaining its secret URL."""

    native_id = str(native_session_id or "").strip()
    playback_id = str(playback_session_id or "").strip()
    if not native_id or not playback_id:
        return
    if not _lock.acquire(blocking=False):
        return
    try:
        _prune_native_stream_contexts_locked()
        _native_stream_contexts[native_id] = (playback_id, time.monotonic())
        _native_stream_contexts.move_to_end(native_id)
        while len(_native_stream_contexts) > _NATIVE_CONTEXT_MAX_ENTRIES:
            _native_stream_contexts.popitem(last=False)
    finally:
        _lock.release()


def resolve_native_stream_context(native_session_id: str) -> str | None:
    native_id = str(native_session_id or "").strip()
    if not native_id:
        return None
    if not _lock.acquire(blocking=False):
        return None
    try:
        _prune_native_stream_contexts_locked()
        row = _native_stream_contexts.get(native_id)
        if row is None:
            return None
        _native_stream_contexts.move_to_end(native_id)
        return row[0]
    finally:
        _lock.release()


def _prune_native_stream_contexts_locked() -> None:
    cutoff = time.monotonic() - _NATIVE_CONTEXT_TTL_SECONDS
    expired = [key for key, (_session_id, seen_at) in _native_stream_contexts.items() if seen_at < cutoff]
    for key in expired:
        _native_stream_contexts.pop(key, None)


def observe_runtime_event(
    event_name: str,
    *,
    playback_session_id: str | None,
    event_source: str = "server",
    observation_kind: str = "measured_server",
    priority: str = "normal",
    severity: str = "info",
    payload: dict[str, Any] | None = None,
    **identities: Any,
) -> None:
    """Compatibility alias for the one non-blocking production ingress API."""

    try_capture_diagnostic_observation(
        event_name,
        playback_session_id=playback_session_id,
        event_source=event_source,
        observation_kind=observation_kind,
        priority=priority,
        severity=severity,
        payload=payload or {},
        **identities,
    )


def record_runtime_health(component: str, reason_code: str) -> None:
    """Record bounded diagnostics self-health without entering the source stream."""

    service = get_active_diagnostics_service()
    if service is None:
        return
    try:
        service.health.record(component, reason_code)
    except BaseException:  # noqa: BLE001 - health cannot affect playback.
        return
