from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse

from ..auth import CurrentUser, resolve_client_ip
from ..schemas import (
    DesktopPlaybackHandoffCreateResponse,
    DesktopPlaybackHandoffRequest,
    DesktopPlaybackHandoffResolveResponse,
    DesktopPlaybackOpenRequest,
    DesktopPlaybackOpenResponse,
    DesktopPlaybackResolveResponse,
    MessageResponse,
)
from ..services.audit_service import log_audit_event
from ..services.desktop_playback_service import (
    build_desktop_playback_resolution,
    build_vlc_playlist_response,
    create_desktop_vlc_handoff,
    infer_desktop_platform,
    launch_vlc_for_item,
    record_desktop_vlc_handoff_started,
    resolve_same_host_request,
    resolve_desktop_vlc_handoff,
)
from ..services.library_service import get_media_item_detail


router = APIRouter(prefix="/api/desktop-playback", tags=["desktop-playback"])


@router.get("/{item_id}", response_model=DesktopPlaybackResolveResponse)
def desktop_playback_resolve(
    item_id: int,
    request: Request,
    platform: str | None = Query(default=None),
    same_host: bool = Query(default=False),
    user=CurrentUser,
) -> DesktopPlaybackResolveResponse:
    settings = request.app.state.settings
    item = get_media_item_detail(settings, user_id=user.id, item_id=item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    resolved_platform = infer_desktop_platform(request.headers.get("user-agent"), platform)
    client_ip = resolve_client_ip(request)
    same_host_context = resolve_same_host_request(
        settings,
        platform=resolved_platform,
        client_ip=client_ip,
        request_host=request.url.hostname,
        explicit_same_host=bool(same_host),
    )
    same_host_detected = bool(same_host_context["same_host"])
    payload = build_desktop_playback_resolution(
        settings,
        item=item,
        platform=resolved_platform,
        same_host=same_host_detected,
    )
    return DesktopPlaybackResolveResponse(**payload)


@router.post("/{item_id}/open", response_model=DesktopPlaybackOpenResponse)
def desktop_playback_open(
    item_id: int,
    request: Request,
    payload: DesktopPlaybackOpenRequest | None = None,
    user=CurrentUser,
) -> DesktopPlaybackOpenResponse:
    settings = request.app.state.settings
    item = get_media_item_detail(settings, user_id=user.id, item_id=item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    resolved_platform = infer_desktop_platform(
        request.headers.get("user-agent"),
        payload.platform if payload else None,
    )
    client_ip = resolve_client_ip(request)
    same_host_context = resolve_same_host_request(
        settings,
        platform=resolved_platform,
        client_ip=client_ip,
        request_host=request.url.hostname,
        explicit_same_host=bool(payload.same_host) if payload else False,
    )
    same_host_detected = bool(same_host_context["same_host"])
    if resolved_platform != "linux":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Direct VLC launch is only implemented for Linux same-host playback",
        )
    result = launch_vlc_for_item(
        settings,
        user_id=user.id,
        item=item,
        same_host=same_host_detected,
        auth_session_id=user.session_id,
        user_agent=request.headers.get("user-agent"),
        source_ip=client_ip,
    )
    log_audit_event(
        settings,
        action="playback.vlc.launch",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        session_id=user.session_id,
        target_type="media",
        target_id=item_id,
        media_item_id=item_id,
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent"),
        details={
            "platform": resolved_platform,
            "strategy": result["strategy"],
            "same_host": same_host_detected,
        },
    )
    return DesktopPlaybackOpenResponse(**result)


@router.post("/{item_id}/handoff", response_model=DesktopPlaybackHandoffCreateResponse)
def desktop_playback_handoff_create(
    item_id: int,
    request: Request,
    payload: DesktopPlaybackHandoffRequest | None = None,
    user=CurrentUser,
) -> DesktopPlaybackHandoffCreateResponse:
    item = get_media_item_detail(request.app.state.settings, user_id=user.id, item_id=item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    resolved_platform = infer_desktop_platform(
        request.headers.get("user-agent"),
        payload.platform if payload else None,
    )
    handoff = create_desktop_vlc_handoff(
        request.app.state.settings,
        user_id=user.id,
        item=item,
        platform=resolved_platform,
        device_id=payload.device_id if payload else None,
        auth_session_id=user.session_id,
        user_agent=request.headers.get("user-agent"),
        source_ip=resolve_client_ip(request),
    )
    log_audit_event(
        request.app.state.settings,
        action="playback.handoff.create",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        session_id=user.session_id,
        target_type="media",
        target_id=item_id,
        media_item_id=item_id,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={
            "platform": resolved_platform,
            "strategy": handoff["strategy"],
            "mode": "desktop_vlc_helper",
        },
    )
    return DesktopPlaybackHandoffCreateResponse(**handoff)


@router.get("/{item_id}/handoff/launch")
def desktop_playback_handoff_launch(
    item_id: int,
    request: Request,
    platform: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
    user=CurrentUser,
):
    item = get_media_item_detail(request.app.state.settings, user_id=user.id, item_id=item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    resolved_platform = infer_desktop_platform(request.headers.get("user-agent"), platform)
    handoff = create_desktop_vlc_handoff(
        request.app.state.settings,
        user_id=user.id,
        item=item,
        platform=resolved_platform,
        device_id=device_id,
        auth_session_id=user.session_id,
        user_agent=request.headers.get("user-agent"),
        source_ip=resolve_client_ip(request),
    )
    log_audit_event(
        request.app.state.settings,
        action="playback.handoff.create",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        session_id=user.session_id,
        target_type="media",
        target_id=item_id,
        media_item_id=item_id,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={
            "platform": resolved_platform,
            "strategy": handoff["strategy"],
            "mode": "desktop_vlc_helper_launch_redirect",
        },
    )
    return RedirectResponse(url=str(handoff["protocol_url"]), status_code=status.HTTP_302_FOUND)


@router.get("/handoff/{handoff_id}", response_model=DesktopPlaybackHandoffResolveResponse)
def desktop_playback_handoff_resolve(
    handoff_id: str,
    request: Request,
    token: str = Query(...),
) -> DesktopPlaybackHandoffResolveResponse:
    payload = resolve_desktop_vlc_handoff(
        request.app.state.settings,
        handoff_id=handoff_id,
        access_token=token,
        helper_version=request.headers.get("x-elvern-helper-version"),
        helper_platform=request.headers.get("x-elvern-helper-platform"),
        helper_arch=request.headers.get("x-elvern-helper-arch"),
        helper_vlc_detection_state=request.headers.get("x-elvern-vlc-detection-state"),
        helper_vlc_detection_path=request.headers.get("x-elvern-vlc-detection-path"),
        source_ip=resolve_client_ip(request),
    )
    log_audit_event(
        request.app.state.settings,
        action="playback.vlc.handoff.resolve",
        outcome="success",
        target_type="desktop_vlc_handoff",
        target_id=handoff_id,
        media_item_id=payload["media_id"],
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={
            "platform": payload["platform"],
            "strategy": payload["strategy"],
            "target_kind": payload["target_kind"],
        },
    )
    return DesktopPlaybackHandoffResolveResponse(**payload)


@router.post("/handoff/{handoff_id}/started", response_model=MessageResponse)
def desktop_playback_handoff_started(
    handoff_id: str,
    request: Request,
    token: str = Query(...),
) -> MessageResponse:
    payload = record_desktop_vlc_handoff_started(
        request.app.state.settings,
        handoff_id=handoff_id,
        access_token=token,
    )
    return MessageResponse(message=str(payload["message"]))


@router.get("/{item_id}/playlist")
def desktop_playback_playlist(
    item_id: int,
    request: Request,
    platform: str | None = Query(default=None),
    user=CurrentUser,
):
    item = get_media_item_detail(request.app.state.settings, user_id=user.id, item_id=item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    resolved_platform = infer_desktop_platform(request.headers.get("user-agent"), platform)
    resolution = build_desktop_playback_resolution(
        request.app.state.settings,
        item=item,
        platform=resolved_platform,
        same_host=False,
    )
    log_audit_event(
        request.app.state.settings,
        action="playback.handoff.create",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        session_id=user.session_id,
        target_type="media",
        target_id=item_id,
        media_item_id=item_id,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={
            "platform": resolved_platform,
            "strategy": resolution["strategy"],
            "mode": "vlc_playlist",
        },
    )
    return build_vlc_playlist_response(
        request.app.state.settings,
        user_id=user.id,
        item=item,
        platform=resolved_platform,
        auth_session_id=user.session_id,
        user_agent=request.headers.get("user-agent"),
        source_ip=resolve_client_ip(request),
    )
