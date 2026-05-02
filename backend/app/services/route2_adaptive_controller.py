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
    active_route2_user_count: int | None = None
    host_cpu_total_cores: int | None = None
    host_cpu_used_cores: float | None = None
    host_cpu_used_percent: float | None = None
    external_cpu_cores_used_estimate: float | None = None
    external_cpu_percent_estimate: float | None = None
    external_ffmpeg_process_count: int | None = None
    external_ffmpeg_cpu_cores_estimate: float | None = None
    host_cpu_sample_mature: bool = False
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


def _benchmark_preferred_promotion_target(current_threads: int) -> int:
    if current_threads <= 5:
        target = 6
    elif current_threads <= 8:
        target = 9
    elif current_threads <= 11:
        target = 12
    else:
        target = current_threads
    return max(current_threads, int(target))


def _ladder_reason_for_target(target_threads: int) -> str:
    if target_threads == 6:
        return "Benchmark-informed ladder selected 6 as the first CPU-bound promotion target."
    if target_threads == 9:
        return "Benchmark data shows 6-8 often plateau; selecting 9 as the next useful tier."
    if target_threads == 12:
        return "Strict experimental 12-thread heavy tier conditions passed."
    return "Benchmark-informed ladder did not select a higher thread tier."


def _strict_twelve_tier_hold_reason(
    payload: Route2AdaptiveShadowInput,
    *,
    current_threads: int,
    worker_thread_pressure_ratio: float | None,
    cpu_cores_used: float | None,
    supply_rate_x: float,
    runway_seconds: float,
    required_startup_runway_seconds: float,
    user_headroom_cores: float | None,
    global_headroom_cores: float | None,
    memory_pressure_ratio: float | None,
) -> str | None:
    if current_threads < 9 or current_threads > 11:
        return None
    if payload.source_kind != "local":
        return "Cloud provider/source guard blocks 12-tier promotion; 12 remains strict/experimental for cloud."
    if payload.active_route2_user_count != 1:
        if payload.active_route2_user_count is None:
            return "12 is a strict experimental heavy tier and was not selected because active Route2 user accounting is unavailable."
        return "12 is a strict experimental heavy tier and was not selected because multiple Route2 users are active."
    if payload.supply_observation_seconds is None or float(payload.supply_observation_seconds) < 20.0:
        return "12 is a strict experimental heavy tier and was not selected because the supply sample is not long enough."
    strongly_cpu_active = (
        cpu_cores_used is not None
        and (
            (worker_thread_pressure_ratio is not None and worker_thread_pressure_ratio >= 0.95)
            or cpu_cores_used >= max(2.0, current_threads * 0.95)
        )
    )
    if not strongly_cpu_active:
        return "12 is a strict experimental heavy tier and was not selected because the worker is not strongly CPU-active."
    clearly_low_supply = (
        supply_rate_x < 0.90
        or runway_seconds + 0.001 < (required_startup_runway_seconds * 0.75)
    )
    if not clearly_low_supply:
        return "12 is a strict experimental heavy tier and was not selected because supply is not clearly low enough."
    if user_headroom_cores is None or global_headroom_cores is None:
        return "12 is a strict experimental heavy tier and was not selected because CPU headroom is unmeasured."
    if min(user_headroom_cores, global_headroom_cores) < 3.0:
        return "12 is a strict experimental heavy tier and was not selected because user/global CPU headroom is not large enough."
    if memory_pressure_ratio is None:
        return "12 is a strict experimental heavy tier and was not selected because memory pressure is unmeasured."
    if memory_pressure_ratio >= 0.80:
        return "12 is a strict experimental heavy tier and was not selected because the memory pressure guard blocks it."
    return None


def _external_host_pressure_hold_reason(
    payload: Route2AdaptiveShadowInput,
    *,
    current_threads: int,
    target_threads: int,
    worker_thread_pressure_ratio: float | None,
    cpu_cores_used: float | None,
    supply_rate_x: float,
    runway_seconds: float,
    required_startup_runway_seconds: float,
) -> str | None:
    if target_threads <= current_threads:
        return None

    host_metrics_mature = (
        payload.host_cpu_sample_mature
        and payload.host_cpu_total_cores is not None
        and payload.host_cpu_used_cores is not None
        and payload.host_cpu_used_percent is not None
    )
    strongly_cpu_active = (
        cpu_cores_used is not None
        and (
            (worker_thread_pressure_ratio is not None and worker_thread_pressure_ratio >= 0.95)
            or cpu_cores_used >= max(2.0, current_threads * 0.95)
        )
    )
    clearly_low_supply = (
        supply_rate_x < 0.85
        or runway_seconds + 0.001 < (required_startup_runway_seconds * 0.75)
    )
    if not host_metrics_mature:
        if target_threads <= 6 and strongly_cpu_active and clearly_low_supply:
            return None
        return (
            "Host CPU pressure metrics are missing or immature; non-Elvern workload has priority, "
            "so shadow controller blocks higher Route2 promotion."
        )

    host_total_cores = max(1.0, float(payload.host_cpu_total_cores or 1))
    host_used_cores = max(0.0, float(payload.host_cpu_used_cores or 0.0))
    host_used_ratio = max(0.0, float(payload.host_cpu_used_percent or 0.0))
    if host_used_ratio > 1.0:
        host_used_ratio = host_used_ratio / 100.0
    external_cpu_cores = max(0.0, float(payload.external_cpu_cores_used_estimate or 0.0))
    external_cpu_ratio = max(0.0, float(payload.external_cpu_percent_estimate or 0.0))
    if external_cpu_ratio > 1.0:
        external_cpu_ratio = external_cpu_ratio / 100.0
    external_ffmpeg_count = max(0, int(payload.external_ffmpeg_process_count or 0))
    host_spare_cores = max(0.0, host_total_cores - host_used_cores)
    high_external_pressure = (
        external_cpu_cores >= 4.0
        or external_cpu_ratio >= 0.20
    )
    if high_external_pressure:
        return (
            "External host CPU pressure is present; non-Elvern workload has priority, "
            "so shadow controller blocks Route2 promotion."
        )

    if external_ffmpeg_count > 0:
        if target_threads >= 9:
            return (
                "External ffmpeg process detected; non-Elvern ffmpeg has priority, "
                "so shadow controller blocks aggressive Route2 promotion."
            )
        if host_spare_cores < 2.0 or external_cpu_cores >= 1.0 or external_cpu_ratio >= 0.05:
            return (
                "External ffmpeg process detected and host spare capacity is limited; "
                "shadow controller blocks Route2 promotion."
            )

    moderate_external_pressure = (
        external_cpu_cores >= 3.0
        or external_cpu_ratio >= 0.15
    )
    if moderate_external_pressure and target_threads > 6:
        return (
            "Moderate external host CPU pressure limits Route2 to the first promotion tier; "
            "shadow controller blocks higher Route2 promotion."
        )

    return None


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
    if payload.active_route2_user_count is None:
        missing_metrics.append("active_route2_user_count")
    if not payload.host_cpu_sample_mature:
        missing_metrics.append("host_cpu_sample_mature")
    if payload.host_cpu_total_cores is None:
        missing_metrics.append("host_cpu_total_cores")
    if payload.host_cpu_used_cores is None:
        missing_metrics.append("host_cpu_used_cores")
    if payload.host_cpu_used_percent is None:
        missing_metrics.append("host_cpu_used_percent")
    if payload.external_cpu_cores_used_estimate is None:
        missing_metrics.append("external_cpu_cores_used_estimate")
    if payload.external_cpu_percent_estimate is None:
        missing_metrics.append("external_cpu_percent_estimate")
    if payload.external_ffmpeg_process_count is None:
        missing_metrics.append("external_ffmpeg_process_count")
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
    preferred_ladder_target = _benchmark_preferred_promotion_target(current_threads)
    strict_twelve_hold_reason = _strict_twelve_tier_hold_reason(
        payload,
        current_threads=current_threads,
        worker_thread_pressure_ratio=worker_thread_pressure_ratio,
        cpu_cores_used=cpu_cores_used,
        supply_rate_x=supply_rate_x,
        runway_seconds=runway_seconds,
        required_startup_runway_seconds=required_startup_runway_seconds,
        user_headroom_cores=user_headroom_cores,
        global_headroom_cores=global_headroom_cores,
        memory_pressure_ratio=memory_pressure_ratio,
    )
    benchmark_target = preferred_ladder_target
    if preferred_ladder_target == 12 and strict_twelve_hold_reason is not None:
        benchmark_target = current_threads
    target_increase_threads = benchmark_target
    external_pressure_hold_reason = _external_host_pressure_hold_reason(
        payload,
        current_threads=current_threads,
        target_threads=target_increase_threads,
        worker_thread_pressure_ratio=worker_thread_pressure_ratio,
        cpu_cores_used=cpu_cores_used,
        supply_rate_x=supply_rate_x,
        runway_seconds=runway_seconds,
        required_startup_runway_seconds=required_startup_runway_seconds,
    )
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
        if (
            target_increase_threads > current_threads
            and external_pressure_hold_reason is None
            and target_increase_threads <= adaptive_ceiling
            and target_increase_threads <= resource_headroom_ceiling
            and user_has_cpu_headroom
            and global_has_cpu_headroom
        ):
            cpu_bound_confidence = (
                0.9
                if user_budget_pressure_ratio is not None and global_upbound_pressure_ratio is not None
                else 0.82
            )
            reason = (
                "Low supply with a CPU-active worker and available user/global CPU headroom; "
                "adaptive ceiling permits promotion. "
                f"{_ladder_reason_for_target(target_increase_threads)}"
            )
            if real_spawn_cap_is_below_shadow:
                reason += (
                    f" Real worker spawn is still capped at {payload.max_threads}; shadow adaptive control "
                    f"would recommend {target_increase_threads} if enabled."
                )
            if payload.active_route2_user_count == 1:
                reason += (
                    " Single-user Route2 accounting is present; future real adaptive spawn could consider "
                    "starting at 6 only after continuous telemetry is mature."
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
        if external_pressure_hold_reason is not None:
            reason = external_pressure_hold_reason
        elif strict_twelve_hold_reason is not None:
            reason = strict_twelve_hold_reason
        elif adaptive_ceiling_cap_active or preferred_ladder_target > adaptive_ceiling:
            reason = (
                "Supply is lagging and the worker is CPU-active, but the adaptive recommendation ceiling "
                "prevents a shadow thread increase."
            )
        elif preferred_ladder_target > resource_headroom_ceiling:
            reason = (
                "Supply is lagging and the worker is CPU-active, but user/global CPU headroom is not large "
                "enough for the next benchmark-informed thread tier."
            )
        elif not user_has_cpu_headroom or not global_has_cpu_headroom:
            reason = (
                "Supply is lagging and the worker is CPU-active, but user or global Route2 CPU headroom "
                "is unavailable or unmeasured."
            )
        elif preferred_ladder_target <= current_threads:
            reason = "Benchmark-informed ladder has no default increase beyond the current thread tier."
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
