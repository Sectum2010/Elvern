from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..schemas import (
    NativePlaybackSessionCreateRequest,
    TransportControllerDecisionRequest,
    TransportControllerDecisionResponse,
    TransportControllerFallbackResponse,
    TransportControllerPrimaryTargetResponse,
    TransportControllerTelemetryResponse,
)


@dataclass(frozen=True)
class PlayerCapabilityProfile:
    profile_id: str
    player: str
    platform_family: str
    supports_raw_tokenized_url: bool
    supports_hls_url: bool
    supports_url_refresh: bool
    supports_return_callback: bool
    supports_clean_rehandoff: bool
    background_behavior: str


def build_ios_external_transport_request(
    settings: Settings,
    *,
    payload: NativePlaybackSessionCreateRequest | None,
    user_agent: str | None,
) -> TransportControllerDecisionRequest | None:
    requested_player = _resolve_requested_player(payload)
    if requested_player is None:
        return None

    return TransportControllerDecisionRequest(
        requested_player=requested_player,
        requested_transport_mode=(payload.requested_transport_mode if payload else None) or "single_best_path",
        platform_family=_detect_ios_platform_family(user_agent),
        caller_surface=(payload.caller_surface if payload else None) or "web_browser",
        environment_kind="tailnet_private" if settings.private_network_only else "remote_unknown",
        current_path_class=(payload.current_path_class if payload else None) or "unknown",
        trusted_network_context=bool(payload.trusted_network_context) if payload else False,
        allow_browser_fallback=True if payload is None else bool(payload.allow_browser_fallback),
    )


def resolve_transport_decision(
    request: TransportControllerDecisionRequest,
) -> TransportControllerDecisionResponse:
    capability = get_player_capability_profile(
        player=request.requested_player,
        platform_family=request.platform_family,
    )
    selected_mode = "single_best_path"
    downgraded = False
    reason_code = "phase2_explicit_context_baseline"

    if request.requested_transport_mode != "single_best_path":
        if request.caller_surface != "native_app":
            downgraded = True
            reason_code = "requested_direct_mode_requires_native_app_context"
        elif not request.trusted_network_context:
            downgraded = True
            reason_code = "requested_direct_mode_untrusted_network_context"
        elif request.current_path_class == "unknown":
            downgraded = True
            reason_code = "requested_direct_mode_missing_path_class"
        elif request.requested_transport_mode == "wifi_direct" and request.current_path_class != "wifi":
            downgraded = True
            reason_code = "requested_direct_mode_path_mismatch"
        elif request.requested_transport_mode == "cellular_direct" and request.current_path_class != "cellular":
            downgraded = True
            reason_code = "requested_direct_mode_path_mismatch"
        else:
            selected_mode = request.requested_transport_mode
            reason_code = "phase2_trusted_direct_mode_selected"

    fallback = None
    if request.allow_browser_fallback and request.caller_surface != "native_app":
        fallback = TransportControllerFallbackResponse(
            fallback_kind="browser_fallback",
            reason_code="browser_surface_available",
        )

    return TransportControllerDecisionResponse(
        selected_player=request.requested_player,
        selected_mode=selected_mode,
        primary_target=None,
        fallback=fallback,
        telemetry=TransportControllerTelemetryResponse(
            requested_player=request.requested_player,
            requested_transport_mode=request.requested_transport_mode,
            selected_mode=selected_mode,
            player_capability_profile=capability.profile_id,
            platform_family=request.platform_family,
            caller_surface=request.caller_surface,
            environment_kind=request.environment_kind,
            current_path_class=request.current_path_class,
            reason_code=reason_code,
            downgraded=downgraded,
        ),
    )


def attach_native_session_primary_target(
    decision: TransportControllerDecisionResponse,
    *,
    stream_url: str,
    expires_at: str,
) -> TransportControllerDecisionResponse:
    return decision.model_copy(
        update={
            "primary_target": TransportControllerPrimaryTargetResponse(
                target_kind="native_session_stream",
                url=stream_url,
                expires_at=expires_at,
            ),
        }
    )


def get_player_capability_profile(*, player: str, platform_family: str) -> PlayerCapabilityProfile:
    normalized_player = player.strip().lower()
    normalized_platform = platform_family.strip().lower()
    if normalized_player == "vlc":
        return PlayerCapabilityProfile(
            profile_id="vlc_ios_v1" if normalized_platform in {"ios", "ipad_os"} else "vlc_generic_v1",
            player="vlc",
            platform_family=normalized_platform,
            supports_raw_tokenized_url=True,
            supports_hls_url=True,
            supports_url_refresh=False,
            supports_return_callback=False,
            supports_clean_rehandoff=False,
            background_behavior="best_effort_external_app",
        )
    if normalized_player == "infuse":
        return PlayerCapabilityProfile(
            profile_id="infuse_ios_v1" if normalized_platform in {"ios", "ipad_os"} else "infuse_generic_v1",
            player="infuse",
            platform_family=normalized_platform,
            supports_raw_tokenized_url=True,
            supports_hls_url=True,
            supports_url_refresh=False,
            supports_return_callback=True,
            supports_clean_rehandoff=False,
            background_behavior="best_effort_external_app",
        )
    raise ValueError(f"Unsupported player capability profile: {player}")


def _resolve_requested_player(payload: NativePlaybackSessionCreateRequest | None) -> str | None:
    if payload is None:
        return None
    if payload.external_player in {"vlc", "infuse"}:
        return payload.external_player

    normalized_client_name = str(payload.client_name or "").strip().lower()
    if normalized_client_name.startswith("elvern ios vlc handoff"):
        return "vlc"
    if normalized_client_name.startswith("elvern ios infuse handoff"):
        return "infuse"
    return None


def _detect_ios_platform_family(user_agent: str | None) -> str:
    normalized = str(user_agent or "").strip().lower()
    if "ipad" in normalized:
        return "ipad_os"
    if "iphone" in normalized or "ipod" in normalized:
        return "ios"
    return "ios"
