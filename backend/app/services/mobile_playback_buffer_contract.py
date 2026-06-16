from __future__ import annotations

from .mobile_playback_models import (
    ROUTE2_FULL_FAST_START_RUNWAY_SECONDS,
    ROUTE2_LITE_FAST_START_RUNWAY_SECONDS,
    ROUTE2_LITE_SLOW_START_RUNWAY_SECONDS,
    ROUTE2_LITE_UNDERSUPPLY_START_RUNWAY_SECONDS,
)


ROUTE2_FULL_BAD_CONDITION_BUFFER_SECONDS = 900.0
CLIENT_LITE_REAL_CACHE_SECONDS = 15.0
CLIENT_FULL_REAL_CACHE_SECONDS = 30.0
CLIENT_BACK_BUFFER_SECONDS = 120.0
CLIENT_PHONE_MAX_BUFFER_SIZE_BYTES = 250 * 1024 * 1024
CLIENT_TABLET_MAX_BUFFER_SIZE_BYTES = 300 * 1024 * 1024
CLIENT_DESKTOP_MAX_BUFFER_SIZE_BYTES = 3 * 1024 * 1024 * 1024


def client_max_buffer_size_bytes(device_class: str | None) -> int:
    normalized = (device_class or "").strip().lower()
    if normalized == "phone":
        return CLIENT_PHONE_MAX_BUFFER_SIZE_BYTES
    if normalized == "tablet":
        return CLIENT_TABLET_MAX_BUFFER_SIZE_BYTES
    return CLIENT_DESKTOP_MAX_BUFFER_SIZE_BYTES


def _coerce_positive_seconds(value: object, fallback: float) -> float:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return numeric if numeric > 0.0 else fallback


def resolve_buffer_contract_fields(
    *,
    playback_mode: str | None,
    client_device_class: str | None,
    required_startup_runway_seconds: object = None,
    full_bad_condition_detected: bool = False,
    full_bad_condition_reserve_required_seconds: object = None,
    lite_required_runway_seconds: object = None,
    lite_required_runway_source: str | None = None,
    lite_undersupply_detected: bool = False,
) -> dict[str, object]:
    mode = "full" if playback_mode == "full" else "lite"
    if mode == "full":
        if full_bad_condition_detected:
            tier = "full_bad_condition"
            target = ROUTE2_FULL_BAD_CONDITION_BUFFER_SECONDS
            policy_source = "route2_full_bad_condition"
        else:
            tier = "full_healthy"
            target = ROUTE2_FULL_FAST_START_RUNWAY_SECONDS
            policy_source = "route2_full_healthy"
    else:
        source = (lite_required_runway_source or "").strip().lower()
        required = _coerce_positive_seconds(
            lite_required_runway_seconds,
            ROUTE2_LITE_SLOW_START_RUNWAY_SECONDS,
        )
        if lite_undersupply_detected or "undersupply" in source:
            tier = "lite_undersupply"
            target = ROUTE2_LITE_UNDERSUPPLY_START_RUNWAY_SECONDS
        elif "healthy_fast" in source or required <= ROUTE2_LITE_FAST_START_RUNWAY_SECONDS + 0.001:
            tier = "lite_fast"
            target = ROUTE2_LITE_FAST_START_RUNWAY_SECONDS
        else:
            tier = "lite_uncertain"
            target = ROUTE2_LITE_SLOW_START_RUNWAY_SECONDS
        policy_source = f"route2_lite_{source or tier}"

    client_target = CLIENT_FULL_REAL_CACHE_SECONDS if mode == "full" else CLIENT_LITE_REAL_CACHE_SECONDS
    server_required = _coerce_positive_seconds(
        required_startup_runway_seconds,
        target,
    )
    server_reserve = (
        _coerce_positive_seconds(full_bad_condition_reserve_required_seconds, target)
        if mode == "full" and full_bad_condition_detected
        else server_required
    )
    return {
        "buffer_tier": tier,
        "server_required_runway_seconds": round(server_required, 2),
        "server_reserve_seconds": round(server_reserve, 2),
        "client_recommended_forward_buffer_seconds": round(client_target, 2),
        "client_max_forward_buffer_seconds": round(client_target, 2),
        "client_back_buffer_seconds": round(CLIENT_BACK_BUFFER_SECONDS, 2),
        "client_max_buffer_size_bytes": client_max_buffer_size_bytes(client_device_class),
        "client_buffer_policy_source": policy_source,
        "client_buffer_limited_by_memory": False,
    }
