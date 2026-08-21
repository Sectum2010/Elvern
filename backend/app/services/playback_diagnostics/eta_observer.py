from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from .ingress import next_diagnostic_correlation_id
from .runtime import observe_runtime_event, record_runtime_health


_lock = threading.RLock()


@dataclass(frozen=True)
class _PendingPrediction:
    prediction_id: str
    created_monotonic_ns: int
    predicted_duration_ms: float
    algorithm_version: str


_pending: OrderedDict[str, _PendingPrediction] = OrderedDict()
_MAX_PENDING = 4_096


def _confidence_for_source(source: str) -> tuple[float | None, str, str, bool]:
    if source in {"published_frontier", "fast_start_supply_surplus"}:
        return 0.75, "derived_from_measured_frontier_and_supply", "heuristic", False
    if source in {"none", "unknown", ""}:
        return None, "unknown", "unavailable", False
    return None, "source_does_not_expose_confidence", "unavailable", False


def _emit_superseded(
    session_id: str,
    previous: _PendingPrediction,
    *,
    replacement_prediction_id: str | None,
    reason: str,
) -> None:
    observe_runtime_event(
        "eta_prediction_superseded",
        playback_session_id=session_id,
        event_source="server",
        observation_kind="derived",
        priority="high",
        payload={
            "prediction_id": previous.prediction_id,
            "prediction_kind": "client_buffer_ready_eta",
            "replacement_prediction_id": replacement_prediction_id,
            "replacement_reason": reason,
        },
    )


def observe_eta_snapshot(payload: dict[str, Any]) -> None:
    """Record estimates already present in a playback API response."""

    try:
        session_id = str(payload.get("session_id") or "")
        if not session_id:
            return
        now_monotonic_ns = time.monotonic_ns()
        now_wall_ns = time.time_ns()
        estimate = payload.get("mode_estimate_seconds")
        ready = bool(payload.get("mode_ready"))
        if not _lock.acquire(blocking=False):
            record_runtime_health("eta_observer", "eta_ledger_busy")
            return
        try:
            previous = _pending.get(session_id)
            if estimate is not None and not ready:
                estimate_seconds = max(0.0, float(estimate))
                algorithm_version = str(payload.get("mode_estimate_source") or "unknown")
                predicted_ms = estimate_seconds * 1_000
                if (
                    previous is not None
                    and previous.predicted_duration_ms == predicted_ms
                    and previous.algorithm_version == algorithm_version
                ):
                    _pending.move_to_end(session_id)
                    return
                prediction_id = next_diagnostic_correlation_id("prediction")
                if previous is not None:
                    _emit_superseded(
                        session_id,
                        previous,
                        replacement_prediction_id=prediction_id,
                        reason="estimate_recalculated",
                    )
                confidence, confidence_basis, confidence_kind, calibrated = (
                    _confidence_for_source(algorithm_version)
                )
                _pending[session_id] = _PendingPrediction(
                    prediction_id=prediction_id,
                    created_monotonic_ns=now_monotonic_ns,
                    predicted_duration_ms=predicted_ms,
                    algorithm_version=algorithm_version,
                )
                _pending.move_to_end(session_id)
                while len(_pending) > _MAX_PENDING:
                    evicted_session_id, evicted = _pending.popitem(last=False)
                    _emit_superseded(
                        evicted_session_id,
                        evicted,
                        replacement_prediction_id=None,
                        reason="pending_ledger_capacity",
                    )
                observe_runtime_event(
                    "eta_prediction",
                    playback_session_id=session_id,
                    event_source="server",
                    observation_kind="derived",
                    payload={
                        "prediction_id": prediction_id,
                        "prediction_kind": "client_buffer_ready_eta",
                        "prediction_monotonic_origin_ns": str(now_monotonic_ns),
                        "predicted_duration_ms": predicted_ms,
                        "predicted_ready_monotonic_ns": str(
                            now_monotonic_ns + int(estimate_seconds * 1_000_000_000)
                        ),
                        "estimated_ready_wall_time_ns": str(
                            now_wall_ns + int(estimate_seconds * 1_000_000_000)
                        ),
                        "algorithm_version": algorithm_version,
                        "input_snapshot": {
                            "runway_ms": float(payload.get("ahead_runway_seconds") or 0) * 1_000,
                            "supply_rate_x": payload.get("supply_rate_x"),
                        },
                        "confidence": confidence,
                        "confidence_basis": confidence_basis,
                        "confidence_kind": confidence_kind,
                        "calibrated": calibrated,
                    },
                )
            if ready and previous is not None:
                actual_ms = max(
                    0.0,
                    (now_monotonic_ns - previous.created_monotonic_ns) / 1_000_000,
                )
                predicted_ms = previous.predicted_duration_ms
                signed_bias = actual_ms - predicted_ms
                observe_runtime_event(
                    "eta_resolved",
                    playback_session_id=session_id,
                    event_source="server",
                    observation_kind="derived",
                    priority="high",
                    payload={
                        "prediction_id": previous.prediction_id,
                        "prediction_kind": "client_buffer_ready_eta",
                        "actual_duration_ms": actual_ms,
                        "absolute_error_ms": abs(signed_bias),
                        "relative_error": abs(signed_bias) / predicted_ms if predicted_ms > 0 else None,
                        "signed_bias_ms": signed_bias,
                    },
                )
                _pending.pop(session_id, None)
            elif estimate is None and not ready and previous is not None:
                _emit_superseded(
                    session_id,
                    previous,
                    replacement_prediction_id=None,
                    reason="estimate_became_unavailable",
                )
                _pending.pop(session_id, None)
        finally:
            _lock.release()
    except Exception:  # noqa: BLE001 - response payload is never changed.
        record_runtime_health("eta_observer", "eta_snapshot_failed")
        return


def forget_eta_session(playback_session_id: str) -> None:
    """Release completed-session state without changing playback responses."""

    with _lock:
        previous = _pending.pop(playback_session_id, None)
    if previous is not None:
        _emit_superseded(
            playback_session_id,
            previous,
            replacement_prediction_id=None,
            reason="session_forgotten",
        )
