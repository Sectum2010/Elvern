from __future__ import annotations

import math
import statistics

from .mobile_playback_models import (
    ROUTE2_FULL_GOODPUT_MIN_OBSERVATION_SECONDS,
    ROUTE2_FULL_GOODPUT_MIN_SAMPLE_COUNT,
    ROUTE2_SUPPLY_RATE_SLOW_EMA_ALPHA,
)


def _harmonic_mean_locked(values: list[float]) -> float:
    positive_values = [value for value in values if value > 0.0]
    if not positive_values:
        return 0.0
    inverse_sum = sum(1.0 / value for value in positive_values)
    if inverse_sum <= 0.0:
        return 0.0
    return len(positive_values) / inverse_sum


def _percentile_locked(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(value for value in values if value > 0.0)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    rank = max(0, min(len(ordered) - 1, int(math.floor((len(ordered) - 1) * percentile))))
    return ordered[rank]


def _ema_locked(values: list[float], *, alpha: float) -> float:
    if not values:
        return 0.0
    ema_value = values[0]
    for value in values[1:]:
        ema_value = (alpha * value) + ((1.0 - alpha) * ema_value)
    return max(0.0, ema_value)


def _conservative_goodput_locked(
    rates: list[float],
    *,
    observation_seconds: float,
) -> dict[str, float | int | bool]:
    positive_rates = [rate for rate in rates if rate > 0.0]
    if not positive_rates:
        return {
            "safe_rate": 0.0,
            "harmonic_mean": 0.0,
            "slow_ema": 0.0,
            "p20": 0.0,
            "median_rate": 0.0,
            "sample_count": 0,
            "observation_seconds": observation_seconds,
            "confident": False,
        }
    harmonic_mean = _harmonic_mean_locked(positive_rates)
    slow_ema = _ema_locked(positive_rates, alpha=ROUTE2_SUPPLY_RATE_SLOW_EMA_ALPHA)
    p20 = _percentile_locked(positive_rates, 0.20)
    median_rate = max(0.0, float(statistics.median(positive_rates)))
    safe_rate = min(rate for rate in (harmonic_mean, slow_ema, p20) if rate > 0.0)
    sample_count = len(positive_rates)
    confident = (
        sample_count >= ROUTE2_FULL_GOODPUT_MIN_SAMPLE_COUNT
        and observation_seconds >= ROUTE2_FULL_GOODPUT_MIN_OBSERVATION_SECONDS
        and safe_rate > 0.0
    )
    return {
        "safe_rate": safe_rate,
        "harmonic_mean": harmonic_mean,
        "slow_ema": slow_ema,
        "p20": p20,
        "median_rate": median_rate,
        "sample_count": sample_count,
        "observation_seconds": observation_seconds,
        "confident": confident,
    }


def _route2_projected_runway_seconds_locked(
    runway_seconds: float,
    supply_rate_x: float,
    *,
    projection_horizon_seconds: float,
    demand_rate_x: float = 1.0,
) -> float:
    return max(0.0, runway_seconds + ((supply_rate_x - demand_rate_x) * projection_horizon_seconds))


def _route2_required_runway_seconds_locked(
    *,
    minimum_runway_seconds: float,
    projected_runway_target_seconds: float,
    projection_horizon_seconds: float,
    supply_rate_x: float,
) -> float:
    projected_requirement_seconds = max(
        0.0,
        projected_runway_target_seconds - ((supply_rate_x - 1.0) * projection_horizon_seconds),
    )
    return max(minimum_runway_seconds, projected_requirement_seconds)
