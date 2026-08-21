from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from typing import Any

from .runtime import observe_runtime_event


_lock = threading.RLock()
_pending: OrderedDict[str, tuple[str, int, float]] = OrderedDict()
_MAX_PENDING = 4_096


def observe_eta_snapshot(payload: dict[str, Any]) -> None:
    """Record estimates already present in a playback API response."""

    try:
        session_id = str(payload.get("session_id") or "")
        if not session_id:
            return
        now_ns = time.time_ns()
        estimate = payload.get("mode_estimate_seconds")
        ready = bool(payload.get("mode_ready"))
        with _lock:
            previous = _pending.get(session_id)
            if estimate is not None and not ready:
                prediction_id = f"prediction_{secrets.token_urlsafe(18)}"
                estimate_seconds = max(0.0, float(estimate))
                _pending[session_id] = (prediction_id, now_ns, estimate_seconds)
                _pending.move_to_end(session_id)
                while len(_pending) > _MAX_PENDING:
                    _pending.popitem(last=False)
                observe_runtime_event(
                    "eta_prediction",
                    playback_session_id=session_id,
                    event_source="server",
                    observation_kind="derived",
                    payload={
                        "prediction_id": prediction_id,
                        "prediction_kind": "client_buffer_ready_eta",
                        "predicted_duration_ms": estimate_seconds * 1_000,
                        "predicted_ready_time_ns": str(now_ns + int(estimate_seconds * 1_000_000_000)),
                        "algorithm_version": str(payload.get("mode_estimate_source") or "unknown"),
                        "input_snapshot": {
                            "runway_ms": float(payload.get("ahead_runway_seconds") or 0) * 1_000,
                            "source_rate_bps": payload.get("supply_rate_x"),
                        },
                        "confidence": 1.0 if payload.get("mode_estimate_source") == "true" else 0.5,
                    },
                )
            if ready and previous is not None:
                prediction_id, predicted_at_ns, estimate_seconds = previous
                actual_ms = max(0.0, (now_ns - predicted_at_ns) / 1_000_000)
                predicted_ms = estimate_seconds * 1_000
                signed_bias = actual_ms - predicted_ms
                observe_runtime_event(
                    "eta_resolved",
                    playback_session_id=session_id,
                    event_source="server",
                    observation_kind="derived",
                    priority="high",
                    payload={
                        "prediction_id": prediction_id,
                        "prediction_kind": "client_buffer_ready_eta",
                        "actual_duration_ms": actual_ms,
                        "absolute_error_ms": abs(signed_bias),
                        "relative_error": abs(signed_bias) / predicted_ms if predicted_ms > 0 else None,
                        "signed_bias_ms": signed_bias,
                    },
                )
                _pending.pop(session_id, None)
    except Exception:  # noqa: BLE001 - response payload is never changed.
        return
