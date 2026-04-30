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
    adaptive_max_threads: int | None = None
    cpu_cores_used: float | None = None
    allocated_cpu_cores: int | None = None
    user_cpu_cores_used_total: float | None = None
    route2_cpu_upbound_cores: int | None = None
    route2_cpu_cores_used_total: float | None = None
    memory_bytes: int | None = None
    total_memory_bytes: int | None = None
    route2_memory_bytes_total: int | None = None
    route2_memory_percent_of_total: float | None = None
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
    return max(1, int(baseline))


def _adaptive_recommendation_ceiling(payload: Route2AdaptiveShadowInput) -> int:
    ceiling = (
        int(payload.adaptive_max_threads)
        if payload.adaptive_max_threads is not None and int(payload.adaptive_max_threads) > 0
        else int(payload.max_threads)
    )
    if payload.route2_cpu_upbound_cores is not None and payload.route2_cpu_upbound_cores > 0:
        ceiling = min(ceiling, max(1, int(math.floor(payload.route2_cpu_upbound_cores))))
    if payload.allocated_cpu_cores is not None and payload.allocated_cpu_cores > 0:
        ceiling = min(ceiling, max(1, int(math.floor(payload.allocated_cpu_cores))))
    return max(1, ceiling)


def _benchmark_informed_promotion_target(current_threads: int, adaptive_ceiling: int) -> int:
    if current_threads <= 4:
        target = 6
    elif current_threads < 10:
        target = 10
    else:
        target = min(current_threads + 2, adaptive_ceiling) if adaptive_ceiling > 10 else current_threads
    return max(current_threads, min(int(target), int(adaptive_ceiling)))


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


def _worker_thread_pressure_ratio(payload: Route2AdaptiveShadowInput, current_threads: int) -> float | None:
    if payload.cpu_cores_used is None:
        return None
    if current_threads > 0:
        return float(payload.cpu_cores_used) / float(max(current_threads, 1))
    return None


def _budget_pressure_ratio(used_cores: float | None, budget_cores: int | float | None) -> float | None:
    if used_cores is None or budget_cores is None:
        return None
    return float(used_cores) / max(float(budget_cores), 0.001)


def _route2_memory_pressure_ratio(payload: Route2AdaptiveShadowInput) -> float | None:
    if payload.total_memory_bytes and payload.route2_memory_bytes_total is not None:
        return float(payload.route2_memory_bytes_total) / max(float(payload.total_memory_bytes), 1.0)
    if payload.route2_memory_percent_of_total is not None:
        return max(0.0, float(payload.route2_memory_percent_of_total)) / 100.0
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
    if payload.user_cpu_cores_used_total is None:
        missing_metrics.append("user_cpu_cores_used_total")
    if payload.route2_cpu_upbound_cores is None:
        missing_metrics.append("route2_cpu_upbound_cores")
    if payload.route2_cpu_cores_used_total is None:
        missing_metrics.append("route2_cpu_cores_used_total")
    if payload.ahead_runway_seconds is None:
        missing_metrics.append("ahead_runway_seconds")
    if payload.supply_rate_x is None:
        missing_metrics.append("supply_rate_x")
    if payload.supply_observation_seconds is None:
        missing_metrics.append("supply_observation_seconds")
    if payload.total_memory_bytes is None:
        missing_metrics.append("total_memory_bytes")
    if payload.route2_memory_bytes_total is None and payload.route2_memory_percent_of_total is None:
        missing_metrics.append("route2_memory_bytes_total")

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

    adequate_sample = (
        payload.ahead_runway_seconds is not None
        and payload.supply_rate_x is not None
        and payload.supply_observation_seconds is not None
        and float(payload.supply_observation_seconds) >= 6.0
    )
    if not adequate_sample:
        return Route2AdaptiveShadowDecision(
            bottleneck_class="UNKNOWN",
            bottleneck_confidence=0.25,
            recommended_threads=current_threads,
            current_threads=current_threads,
            reason="early_bootstrap_insufficient_samples: Route2 supply metrics are not mature enough for a safe recommendation.",
            safe_to_increase_threads=False,
            safe_to_decrease_threads=False,
            missing_metrics=missing_metrics,
        )

    worker_thread_pressure_ratio = _worker_thread_pressure_ratio(payload, current_threads)
    user_budget_pressure_ratio = _budget_pressure_ratio(payload.user_cpu_cores_used_total, payload.allocated_cpu_cores)
    global_upbound_pressure_ratio = _budget_pressure_ratio(
        payload.route2_cpu_cores_used_total,
        payload.route2_cpu_upbound_cores,
    )
    cpu_cores_used = float(payload.cpu_cores_used) if payload.cpu_cores_used is not None else None
    worker_cpu_active = (
        cpu_cores_used is not None
        and (
            (worker_thread_pressure_ratio is not None and worker_thread_pressure_ratio >= 0.85)
            or cpu_cores_used >= max(1.0, current_threads * 0.85)
        )
    )
    low_or_moderate_cpu = cpu_cores_used is not None and not worker_cpu_active
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
    memory_pressure_ratio = _route2_memory_pressure_ratio(payload)
    memory_soft_pressure = memory_pressure_ratio is not None and memory_pressure_ratio >= 0.80
    memory_hard_pressure = memory_pressure_ratio is not None and memory_pressure_ratio >= 0.90

    if (
        supply_rate_x >= 1.5
        and runway_seconds + 0.001 >= comfortable_runway_seconds
        and not payload.starvation_risk
        and not payload.stalled_recovery_needed
    ):
        recommended_threads = _clamp_threads(current_threads - 2, max_threads=max(current_threads, payload.max_threads))
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

    adaptive_ceiling = _adaptive_recommendation_ceiling(payload)
    user_headroom_cores = (
        float(payload.allocated_cpu_cores) - float(payload.user_cpu_cores_used_total)
        if payload.allocated_cpu_cores is not None and payload.user_cpu_cores_used_total is not None
        else None
    )
    global_headroom_cores = (
        float(payload.route2_cpu_upbound_cores) - float(payload.route2_cpu_cores_used_total)
        if payload.route2_cpu_upbound_cores is not None and payload.route2_cpu_cores_used_total is not None
        else None
    )
    user_has_cpu_headroom = user_headroom_cores is not None and user_headroom_cores >= 1.0
    global_has_cpu_headroom = global_headroom_cores is not None and global_headroom_cores >= 1.0
    resource_headroom_ceiling = current_threads
    if user_headroom_cores is not None and global_headroom_cores is not None:
        resource_headroom_ceiling = current_threads + max(
            0,
            int(math.floor(min(user_headroom_cores, global_headroom_cores))),
        )
    benchmark_target = _benchmark_informed_promotion_target(current_threads, adaptive_ceiling)
    candidate_thread_ceiling = min(adaptive_ceiling, resource_headroom_ceiling, benchmark_target)
    target_increase_threads = candidate_thread_ceiling
    adaptive_ceiling_cap_active = adaptive_ceiling <= current_threads
    real_spawn_cap_is_below_shadow = payload.max_threads < adaptive_ceiling

    if low_supply and worker_cpu_active and not client_bound:
        if memory_pressure_ratio is None:
            return Route2AdaptiveShadowDecision(
                bottleneck_class="UNDER_SUPPLIED_BUT_CPU_LIMITED",
                bottleneck_confidence=0.72,
                recommended_threads=current_threads,
                current_threads=current_threads,
                reason="Supply is lagging and the worker is CPU-active, but memory metrics are missing so the shadow controller will not promote threads.",
                safe_to_increase_threads=False,
                safe_to_decrease_threads=False,
                missing_metrics=missing_metrics,
            )
        if memory_hard_pressure or memory_soft_pressure:
            return Route2AdaptiveShadowDecision(
                bottleneck_class="UNDER_SUPPLIED_BUT_CPU_LIMITED",
                bottleneck_confidence=0.84,
                recommended_threads=current_threads,
                current_threads=current_threads,
                reason="Supply is lagging and the worker is CPU-active, but the memory pressure guard blocks a shadow thread increase.",
                safe_to_increase_threads=False,
                safe_to_decrease_threads=False,
                missing_metrics=missing_metrics,
            )
        if current_threads < candidate_thread_ceiling and user_has_cpu_headroom and global_has_cpu_headroom:
            cpu_bound_confidence = (
                0.9
                if user_budget_pressure_ratio is not None and global_upbound_pressure_ratio is not None
                else 0.82
            )
            reason = (
                "Low supply with a CPU-active worker and available user/global CPU headroom; "
                "adaptive ceiling permits promotion, so the shadow controller recommends a benchmark-informed "
                f"thread target of {target_increase_threads}."
            )
            if real_spawn_cap_is_below_shadow:
                reason += (
                    f" Real worker spawn is still capped at {payload.max_threads}; shadow adaptive control "
                    f"would recommend {target_increase_threads} if enabled."
                )
            return Route2AdaptiveShadowDecision(
                bottleneck_class="CPU_BOUND",
                bottleneck_confidence=cpu_bound_confidence,
                recommended_threads=target_increase_threads,
                current_threads=current_threads,
                reason=reason,
                safe_to_increase_threads=target_increase_threads > current_threads,
                safe_to_decrease_threads=False,
                missing_metrics=missing_metrics,
            )
        if adaptive_ceiling_cap_active:
            reason = (
                "Supply is lagging and the worker is CPU-active, but the adaptive recommendation ceiling "
                "prevents a shadow thread increase."
            )
        elif not user_has_cpu_headroom or not global_has_cpu_headroom:
            reason = (
                "Supply is lagging and the worker is CPU-active, but user or global Route2 CPU headroom "
                "is unavailable or unmeasured."
            )
        else:
            reason = "Supply is lagging and the worker is CPU-active, but no safe shadow thread increase is available."
        return Route2AdaptiveShadowDecision(
            bottleneck_class="UNDER_SUPPLIED_BUT_CPU_LIMITED",
            bottleneck_confidence=(
                0.88
                if user_budget_pressure_ratio is not None or global_upbound_pressure_ratio is not None
                else 0.78
            ),
            recommended_threads=current_threads,
            current_threads=current_threads,
            reason=reason,
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
