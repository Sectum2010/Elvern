from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from ..auth import CurrentUser, resolve_client_ip
from ..schemas import (
    DesktopHelperVerificationRequest,
    DesktopHelperVerificationResponse,
    DesktopHelperReleaseListResponse,
    DesktopHelperStatusResponse,
    MessageResponse,
)
from ..services.audit_service import log_audit_event
from ..services.desktop_helper_service import (
    build_desktop_helper_release_payloads,
    create_desktop_helper_verification,
    get_desktop_helper_status,
    get_helper_release_download_path,
    normalize_desktop_helper_platform,
    resolve_desktop_helper_verification,
)


router = APIRouter(prefix="/api/desktop-helper", tags=["desktop-helper"])


@router.get("/status", response_model=DesktopHelperStatusResponse)
def desktop_helper_status(
    request: Request,
    platform: str = Query(...),
    device_id: str | None = Query(default=None),
    user=CurrentUser,
) -> DesktopHelperStatusResponse:
    payload = get_desktop_helper_status(
        request.app.state.settings,
        user_id=user.id,
        platform=platform,
        device_id=device_id,
        browser_user_agent=request.headers.get("user-agent"),
        source_ip=resolve_client_ip(request),
    )
    return DesktopHelperStatusResponse(**payload)


@router.post("/verify", response_model=DesktopHelperVerificationResponse)
def desktop_helper_verify_start(
    payload: DesktopHelperVerificationRequest,
    request: Request,
    user=CurrentUser,
) -> DesktopHelperVerificationResponse:
    verification = create_desktop_helper_verification(
        request.app.state.settings,
        user_id=user.id,
        platform=payload.platform,
        device_id=payload.device_id,
        browser_user_agent=request.headers.get("user-agent"),
        source_ip=resolve_client_ip(request),
    )
    return DesktopHelperVerificationResponse(**verification)


@router.get("/verify/{verification_id}", response_model=MessageResponse)
def desktop_helper_verify_resolve(
    verification_id: str,
    request: Request,
    token: str = Query(...),
) -> MessageResponse:
    payload = resolve_desktop_helper_verification(
        request.app.state.settings,
        verification_id=verification_id,
        access_token=token,
        helper_version=request.headers.get("x-elvern-helper-version"),
        helper_platform=request.headers.get("x-elvern-helper-platform"),
        helper_arch=request.headers.get("x-elvern-helper-arch"),
        helper_vlc_detection_state=request.headers.get("x-elvern-vlc-detection-state"),
        helper_vlc_detection_path=request.headers.get("x-elvern-vlc-detection-path"),
        source_ip=resolve_client_ip(request),
    )
    return MessageResponse(**payload)


@router.get("/releases", response_model=DesktopHelperReleaseListResponse)
def desktop_helper_releases(
    request: Request,
    platform: str = Query(...),
    user=CurrentUser,
) -> DesktopHelperReleaseListResponse:
    normalized_platform = normalize_desktop_helper_platform(platform)
    if normalized_platform not in {"windows", "mac"}:
        return DesktopHelperReleaseListResponse(platform=normalized_platform, releases=[])
    releases = build_desktop_helper_release_payloads(
        request.app.state.settings,
        platform=normalized_platform,
    )
    return DesktopHelperReleaseListResponse(platform=normalized_platform, releases=releases)


@router.get("/releases/{release_id}/download")
def desktop_helper_release_download(
    release_id: int,
    request: Request,
    user=CurrentUser,
):
    release = get_helper_release_download_path(request.app.state.settings, release_id)
    log_audit_event(
        request.app.state.settings,
        action="desktop_helper.download",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        session_id=user.session_id,
        target_type="desktop_helper_release",
        target_id=release_id,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={
            "platform": release["platform"],
            "runtime_id": release["runtime_id"],
            "version": release["version"],
            "channel": release["channel"],
        },
    )
    return FileResponse(
        path=release["file_path"],
        filename=release["filename"],
        media_type="application/zip",
    )
