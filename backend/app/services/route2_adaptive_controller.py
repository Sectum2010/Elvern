from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal


Route2AdaptiveBottleneckClass = Literal[
    "CPU_BOUND",
    "SOURCE_BOUND",
    "CLIENT_BOUND",
    "OVER_SUPPLIED",
    "UNDER_SUPPLIED_BUT_CPU_LIMITED",
    "WAITING_FOR_CAPACITY",
    "STORAGE_BOUND",
    "PROVIDER_ERROR",
    "UNKNOWN",
]


@dataclass(slots=True)
class Route2AdaptiveShadowInput:
    worker_state: str
    playback_mode: str
    profile: str
    source_kind: str
    assigned_threads: int = 0
    default_threads: int = 4
    max_threads: int = 4
    cpu_cores_used: float | None = None
    allocated_cpu_cores: int | None = None
    route2_cpu_upbound_cores: int | None = None
    route2_cpu_cores_used_total: float | None = None
    memory_bytes: int | None = None
    ready_end_seconds: float | None = None
    effective_playhead_seconds: float | None = None
    ahead_runway_seconds: float | None = None
    required_startup_runway_seconds: float | None = None
    supply_rate_x: float | None = None
    supply_observation_seconds: float | None = None
    client_goodput_bytes_per_second: float | None = None
    client_goodput_confident: bool = False
    server_goodput_bytes_per_second: float | None = None
    server_goodput_confident: bool = False
    non_retryable_error: str | None = None
    starvation_risk: bool = False
    stalled_recovery_needed: bool = False
    mode_ready: bool | None = None


@dataclass(slots=True)
class Route2AdaptiveShadowDecision:
    bottleneck_class: Route2AdaptiveBottleneckClass
    bottleneck_confidence: float
    recommended_threads: int
    current_threads: int
    reason: str
    safe_to_increase_threads: bool
    safe_to_decrease_threads: bool
    missing_metrics: list[str] = field(default_factory=list)


def _default_required_runway_seconds(playback_mode: str) -> float:
    return 120.0 if playback_mode == "full" else 45.0


def _comfortable_runway_seconds(playback_mode: str, required_startup_runway_seconds: float) -> float:
    baseline = max(required_startup_runway_seconds, _default_required_runway_seconds(playback_mode))
    extra = 60.0 if playback_mode == "full" else 20.0
    return max(baseline * 1.5, baseline + extra)


def _clamp_threads(value: int, *, max_threads: int) -> int:
    upper_bound = max(1, max_threads)
    lower_bound = min(2, upper_bound)
    return max(lower_bound, min(int(value), upper_bound))


def _effective_current_threads(payload: Route2AdaptiveShadowInput) -> int:
    baseline = payload.assigned_threads if payload.assigned_threads > 0 else payload.default_threads
    return _clamp_threads(baseline, max_threads=payload.max_threads)


def _looks_like_provider_error(message: str | None) -> bool:
    normalized = (message or "").strip().lower()
    if not normalized:
        return False
    provider_markers = (
        "quota",
        "provider",
        "google drive",
        "gdrive",
        "forbidden",
        "unauthorized",
        "permission",
        "source",
        "auth",
        "rate limit",
    )
    return any(marker in normalized for marker in provider_markers)


def _cpu_pressure_ratio(payload: Route2AdaptiveShadowInput, current_threads: int) -> float | None:
    if payload.cpu_cores_used is None:
        return None
    if payload.allocated_cpu_cores and payload.allocated_cpu_cores > 0:
        return payload.cpu_cores_used / max(float(payload.allocated_cpu_cores), 0.001)
    if current_threads > 0:
        return payload.cpu_cores_used / float(current_threads)
    return None


def _has_weak_client(payload: Route2AdaptiveShadowInput) -> bool:
    if payload.stalled_recovery_needed:
        return True
    if not payload.client_goodput_confident:
        return False
    if (
        payload.server_goodput_confident
        and payload.server_goodput_bytes_per_second is not None
        and payload.server_goodput_bytes_per_second > 0
        and payload.client_goodput_bytes_per_second is not None
    ):
        return payload.client_goodput_bytes_per_second < (payload.server_goodput_bytes_per_second * 0.65)
    return False


def classify_route2_adaptive_shadow(
    payload: Route2AdaptiveShadowInput,
) -> Route2AdaptiveShadowDecision:
    current_threads = _effective_current_threads(payload)
    required_startup_runway_seconds = max(
        0.0,
        float(payload.required_startup_runway_seconds or _default_required_runway_seconds(payload.playback_mode)),
    )
    missing_metrics: list[str] = []
    if payload.cpu_cores_used is None:
        missing_metrics.append("cpu_cores_used")
    if payload.allocated_cpu_cores is None:
        missing_metrics.append("allocated_cpu_cores")
    if payload.ahead_runway_seconds is None:
        missing_metrics.append("ahead_runway_seconds")
    if payload.supply_rate_x is None:
        missing_metrics.append("supply_rate_x")
    if payload.supply_observation_seconds is None:
        missing_metrics.append("supply_observation_seconds")

    if _looks_like_provider_error(payload.non_retryable_error):
        return Route2AdaptiveShadowDecision(
            bottleneck_class="PROVIDER_ERROR",
            bottleneck_confidence=0.98,
            recommended_threads=current_threads,
            current_threads=current_threads,
            reason="Provider or source failure is already explicit; changing threads would not help.",
            safe_to_increase_threads=False,
            safe_to_decrease_threads=False,
            missing_metrics=missing_metrics,
        )

    if payload.worker_state in {"queued", "waiting_for_capacity"}:
        return Route2AdaptiveShadowDecision(
            bottleneck_class="WAITING_FOR_CAPACITY",
            bottleneck_confidence=0.98,
            recommended_threads=current_threads,
            current_threads=current_threads,
            reason="Worker is waiting for scheduler capacity; shadow controller should not recommend more threads.",
            safe_to_increase_threads=False,
            safe_to_decrease_threads=False,
            missing_metrics=missing_metrics,
        )

    if payload.ahead_runway_seconds is None or payload.supply_rate_x is None:
        return Route2AdaptiveShadowDecision(
            bottleneck_class="UNKNOWN",
            bottleneck_confidence=0.25,
            recommended_threads=current_threads,
            current_threads=current_threads,
            reason="Insufficient Route2 supply metrics are available for a safe recommendation.",
            safe_to_increase_threads=False,
            safe_to_decrease_threads=False,
            missing_metrics=missing_metrics,
        )

    cpu_ratio = _cpu_pressure_ratio(payload, current_threads)
    cpu_pressure = cpu_ratio is not None and cpu_ratio >= 0.80
    low_or_moderate_cpu = cpu_ratio is not None and cpu_ratio < 0.60
    runway_seconds = max(0.0, float(payload.ahead_runway_seconds))
    supply_rate_x = max(0.0, float(payload.supply_rate_x))
    supply_healthy = supply_rate_x >= 1.0 and runway_seconds + 0.001 >= required_startup_runway_seconds
    low_supply = (
        supply_rate_x < 1.0
        or runway_seconds + 0.001 < required_startup_runway_seconds
    )
    comfortable_runway_seconds = _comfortable_runway_seconds(
        payload.playback_mode,
        required_startup_runway_seconds,
    )
    client_bound = supply_healthy and _has_weak_client(payload)

    if (
        supply_rate_x >= 1.5
        and runway_seconds + 0.001 >= comfortable_runway_seconds
        and not payload.starvation_risk
        and not payload.stalled_recovery_needed
    ):
        recommended_threads = _clamp_threads(current_threads - 2, max_threads=payload.max_threads)
        return Route2AdaptiveShadowDecision(
            bottleneck_class="OVER_SUPPLIED",
            bottleneck_confidence=0.88 if bool(payload.mode_ready) else 0.78,
            recommended_threads=recommended_threads,
            current_threads=current_threads,
            reason="Supply is comfortably ahead of demand; shadow controller would trend toward fewer threads.",
            safe_to_increase_threads=False,
            safe_to_decrease_threads=recommended_threads < current_threads,
            missing_metrics=missing_metrics,
        )

    if client_bound:
        return Route2AdaptiveShadowDecision(
            bottleneck_class="CLIENT_BOUND",
            bottleneck_confidence=0.82 if payload.client_goodput_confident else 0.68,
            recommended_threads=current_threads,
            current_threads=current_threads,
            reason="Backend supply looks healthy, but client-side goodput or stalled recovery suggests the client is the limiter.",
            safe_to_increase_threads=False,
            safe_to_decrease_threads=False,
            missing_metrics=missing_metrics,
        )

    fair_share_ceiling = (
        min(payload.max_threads, max(1, int(math.floor(payload.allocated_cpu_cores))))
        if payload.allocated_cpu_cores is not None and payload.allocated_cpu_cores > 0
        else payload.max_threads
    )
    target_increase_threads = min(current_threads + 2, fair_share_ceiling)
    increase_step = max(0, target_increase_threads - current_threads)
    global_headroom_cores = None
    if (
        payload.route2_cpu_upbound_cores is not None
        and payload.route2_cpu_cores_used_total is not None
    ):
        global_headroom_cores = max(
            0.0,
            float(payload.route2_cpu_upbound_cores) - float(payload.route2_cpu_cores_used_total),
        )
    spare_budget_available = (
        increase_step > 0
        and (
            global_headroom_cores is None
            or global_headroom_cores + 0.001 >= float(increase_step)
        )
    )

    if low_supply and cpu_pressure:
        if current_threads < fair_share_ceiling and spare_budget_available:
            return Route2AdaptiveShadowDecision(
                bottleneck_class="CPU_BOUND",
                bottleneck_confidence=0.86 if payload.allocated_cpu_cores is not None else 0.74,
                recommended_threads=target_increase_threads,
                current_threads=current_threads,
                reason="Supply is lagging while CPU usage is already near the worker budget; shadow controller sees room to add threads.",
                safe_to_increase_threads=target_increase_threads > current_threads,
                safe_to_decrease_threads=False,
                missing_metrics=missing_metrics,
            )
        return Route2AdaptiveShadowDecision(
            bottleneck_class="UNDER_SUPPLIED_BUT_CPU_LIMITED",
            bottleneck_confidence=0.9 if payload.allocated_cpu_cores is not None else 0.78,
            recommended_threads=current_threads,
            current_threads=current_threads,
            reason="Supply is lagging and CPU is already near budget, but fair-share or global Route2 upbound leaves no safe room to add threads.",
            safe_to_increase_threads=False,
            safe_to_decrease_threads=False,
            missing_metrics=missing_metrics,
        )

    weak_server_goodput = (
        payload.server_goodput_confident
        and payload.client_goodput_confident
        and payload.server_goodput_bytes_per_second is not None
        and payload.client_goodput_bytes_per_second is not None
        and payload.server_goodput_bytes_per_second <= payload.client_goodput_bytes_per_second
    )

    if low_supply and (payload.source_kind == "cloud" or weak_server_goodput) and not client_bound and low_or_moderate_cpu:
        return Route2AdaptiveShadowDecision(
            bottleneck_class="SOURCE_BOUND",
            bottleneck_confidence=0.72 if payload.server_goodput_confident else 0.58,
            recommended_threads=current_threads,
            current_threads=current_threads,
            reason="Supply is lagging without corresponding CPU pressure, which points to the source side rather than transcode threads.",
            safe_to_increase_threads=False,
            safe_to_decrease_threads=False,
            missing_metrics=missing_metrics,
        )

    if low_supply and payload.source_kind == "local" and not client_bound and low_or_moderate_cpu:
        return Route2AdaptiveShadowDecision(
            bottleneck_class="STORAGE_BOUND",
            bottleneck_confidence=0.35,
            recommended_threads=current_threads,
            current_threads=current_threads,
            reason="Local supply is lagging without clear CPU or client pressure; storage remains the best low-confidence explanation until disk metrics exist.",
            safe_to_increase_threads=False,
            safe_to_decrease_threads=False,
            missing_metrics=missing_metrics,
        )

    return Route2AdaptiveShadowDecision(
        bottleneck_class="UNKNOWN",
        bottleneck_confidence=0.3,
        recommended_threads=current_threads,
        current_threads=current_threads,
        reason="No strong bottleneck signal stands out from the current Route2 samples.",
        safe_to_increase_threads=False,
        safe_to_decrease_threads=False,
        missing_metrics=missing_metrics,
    )
