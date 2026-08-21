from __future__ import annotations

import hashlib
import secrets
from dataclasses import asdict, is_dataclass
from typing import Any

from .runtime import observe_runtime_event


_DETAIL_KEYS = {
    "target_threads",
    "current_threads",
    "selected_threads",
    "return_code",
    "segment_index",
    "attach_revision",
    "previous_epoch_id",
    "source_epoch_id",
    "active_epoch_id",
    "candidate_audio_stream_index",
    "previous_audio_stream_index",
    "ready_end_seconds",
    "actual_candidate_runway_seconds",
    "required_server_prepare_seconds",
    "lifecycle_state",
    "client_is_playing",
    "reason",
}


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        candidate = value.strip()
        if len(candidate) <= 256 and not candidate.startswith(("/", "~", "http://", "https://")):
            return candidate
    return None


def observe_route2_manager_event(
    event_name: str,
    *,
    session: Any,
    epoch: Any | None,
    details: dict[str, Any],
) -> None:
    try:
        browser = session.browser_playback
        source = "atc" if any(
            marker in event_name for marker in ("adaptive", "downshift", "resupply", "reclaim")
        ) else "ffmpeg" if "worker" in event_name else "server"
        payload: dict[str, Any] = {
            "state": str(getattr(session, "state", "unknown")),
            "session_state": str(getattr(browser, "state", "unknown")),
            "attachment_revision": int(getattr(browser, "attach_revision", 0) or 0),
            "playback_mode": str(getattr(browser, "playback_mode", "unknown")),
            "stream_mode": str(getattr(browser, "engine_mode", "unknown")),
        }
        if epoch is not None:
            payload.update(
                {
                    "state": str(getattr(epoch, "state", "unknown")),
                    "frontier_ms": round(
                        max(0, int(getattr(epoch, "contiguous_published_through_segment", -1)) + 1)
                        * 4_000,
                    ),
                }
            )
        for key in _DETAIL_KEYS:
            if key in details:
                value = _safe_scalar(details[key])
                if value is not None:
                    payload[key] = value
        error_text = details.get("error") or details.get("stderr_tail")
        if error_text:
            payload["error_detail_hash"] = hashlib.sha256(
                str(error_text).encode("utf-8", errors="replace")
            ).hexdigest()
        decision_id = None
        if source == "atc":
            request_id = details.get("adaptive_resupply_request_id") or details.get("reclaim_request_id")
            decision_id = (
                f"decision_{hashlib.sha256(str(request_id).encode()).hexdigest()[:24]}"
                if request_id
                else f"decision_{secrets.token_urlsafe(18)}"
            )
        observe_runtime_event(
            f"route2_{event_name}",
            playback_session_id=str(session.session_id),
            event_source=source,
            observation_kind="measured_server",
            priority="high" if any(word in event_name for word in ("failed", "blocked", "promoted")) else "normal",
            severity="error" if "failed" in event_name else "info",
            epoch_id=str(getattr(epoch, "epoch_id", "") or "") or None,
            worker_id=str(getattr(epoch, "active_worker_id", "") or "") or None,
            decision_id=decision_id,
            payload=payload,
        )
    except Exception:  # noqa: BLE001 - diagnostics cannot alter Route2.
        return


def observe_atc_evaluation(
    *,
    playback_session_id: str,
    epoch_id: str | None,
    worker_id: str | None,
    adaptive_input: Any,
    adaptive_decision: Any,
) -> None:
    try:
        decision_id = f"decision_{secrets.token_urlsafe(18)}"
        raw_input = asdict(adaptive_input) if is_dataclass(adaptive_input) else {}
        input_snapshot = {
            key: raw_input.get(key)
            for key in (
                "assigned_threads",
                "cpu_cores_used",
                "memory_bytes",
                "ahead_runway_seconds",
                "supply_rate_x",
                "supply_observation_seconds",
                "client_goodput_bytes_per_second",
                "server_goodput_bytes_per_second",
                "starvation_risk",
                "stalled_recovery_needed",
            )
        }
        common = {
            "playback_session_id": playback_session_id,
            "event_source": "atc",
            "observation_kind": "measured_server",
            "decision_id": decision_id,
            "epoch_id": epoch_id,
            "worker_id": worker_id,
        }
        observe_runtime_event(
            "atc_evaluation_started",
            payload={"algorithm_version": "route2-adaptive-shadow-current"},
            **common,
        )
        observe_runtime_event(
            "atc_inputs_captured",
            payload={"input_snapshot": input_snapshot},
            **common,
        )
        observe_runtime_event(
            "atc_decision_produced",
            priority="high",
            payload={
                "current_threads": int(adaptive_decision.current_threads),
                "target_threads": int(adaptive_decision.recommended_threads),
                "bottleneck_class": str(adaptive_decision.bottleneck_class),
                "confidence": float(adaptive_decision.bottleneck_confidence),
                "reason": str(adaptive_decision.reason),
                "missing_signals": list(adaptive_decision.missing_metrics),
                "blocked": not (
                    adaptive_decision.safe_to_increase_threads
                    or adaptive_decision.safe_to_decrease_threads
                ),
            },
            **common,
        )
    except Exception:  # noqa: BLE001 - classification result is untouched.
        return
