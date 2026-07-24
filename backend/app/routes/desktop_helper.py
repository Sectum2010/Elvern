from __future__ import annotations

import logging
import re
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse

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
    normalize_desktop_helper_platform,
    open_helper_release_download,
    resolve_desktop_helper_verification,
)


_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
_SAFE_DOWNLOAD_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*\.zip$")
logger = logging.getLogger(__name__)


def _stream_and_close(
    handle,
    *,
    remaining: int | None = None,
    audit_completion=lambda _outcome: None,
    chunk_size: int = _DOWNLOAD_CHUNK_SIZE,
):
    """Stream the verified handle and always close it — on success, error, or
    client disconnect (Starlette closes the generator, running this finally)."""
    completed = False
    try:
        while remaining is None or remaining > 0:
            chunk = handle.read(
                chunk_size if remaining is None else min(chunk_size, remaining)
            )
            if not chunk:
                completed = remaining is None
                break
            if remaining is not None:
                remaining -= len(chunk)
            yield chunk
        if remaining == 0:
            completed = True
    finally:
        handle.close()
        try:
            audit_completion("success" if completed else "interrupted")
        except Exception:
            logger.warning("Desktop helper download completion audit could not be recorded")


def _download_content_disposition(filename: str) -> str:
    if not _SAFE_DOWNLOAD_FILENAME.fullmatch(filename):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Desktop helper release filename is invalid",
        )
    return (
        f'attachment; filename="{filename}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


def _resolve_download_range(range_header: str | None, size_bytes: int) -> tuple[int, int] | None:
    if not range_header:
        return None
    if not range_header.startswith("bytes=") or "," in range_header:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail="Requested download range is not satisfiable",
            headers={"Content-Range": f"bytes */{size_bytes}"},
        )
    raw_start, separator, raw_end = range_header[6:].partition("-")
    try:
        if not separator:
            raise ValueError
        if raw_start:
            start = int(raw_start)
            end = int(raw_end) if raw_end else size_bytes - 1
        else:
            suffix_length = int(raw_end)
            if suffix_length <= 0:
                raise ValueError
            start = max(0, size_bytes - suffix_length)
            end = size_bytes - 1
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail="Requested download range is not satisfiable",
            headers={"Content-Range": f"bytes */{size_bytes}"},
        ) from exc
    if start < 0 or start >= size_bytes or end < start:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail="Requested download range is not satisfiable",
            headers={"Content-Range": f"bytes */{size_bytes}"},
        )
    return start, min(end, size_bytes - 1)
from ..services.desktop_playback_service import resolve_same_host_request


router = APIRouter(prefix="/api/desktop-helper", tags=["desktop-helper"])


@router.get("/status", response_model=DesktopHelperStatusResponse)
def desktop_helper_status(
    request: Request,
    platform: str = Query(...),
    device_id: str | None = Query(default=None),
    user=CurrentUser,
) -> DesktopHelperStatusResponse:
    client_ip = resolve_client_ip(request)
    same_host_context = resolve_same_host_request(
        request.app.state.settings,
        platform=normalize_desktop_helper_platform(platform),
        client_ip=client_ip,
        request_host=request.url.hostname,
        explicit_same_host=False,
    )
    payload = get_desktop_helper_status(
        request.app.state.settings,
        user_id=user.id,
        platform=platform,
        device_id=device_id,
        browser_user_agent=request.headers.get("user-agent"),
        source_ip=client_ip,
        same_host=bool(same_host_context["same_host"]),
        same_host_detection_source=str(same_host_context["detection_source"]),
    )
    return DesktopHelperStatusResponse(**payload)


@router.post("/verify", response_model=DesktopHelperVerificationResponse)
def desktop_helper_verify_start(
    payload: DesktopHelperVerificationRequest,
    request: Request,
    user=CurrentUser,
) -> DesktopHelperVerificationResponse:
    client_ip = resolve_client_ip(request)
    same_host_context = resolve_same_host_request(
        request.app.state.settings,
        platform=payload.platform,
        client_ip=client_ip,
        request_host=request.url.hostname,
        explicit_same_host=False,
    )
    verification = create_desktop_helper_verification(
        request.app.state.settings,
        user_id=user.id,
        platform=payload.platform,
        device_id=payload.device_id,
        browser_user_agent=request.headers.get("user-agent"),
        source_ip=client_ip,
        same_host=bool(same_host_context["same_host"]),
        same_host_detection_source=str(same_host_context["detection_source"]),
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
    releases = build_desktop_helper_release_payloads(
        request.app.state.settings,
        platform=normalized_platform,
    )
    return DesktopHelperReleaseListResponse(platform=normalized_platform, releases=releases)


@router.api_route("/releases/{release_id}/download", methods=["GET", "HEAD"])
def desktop_helper_release_download(
    release_id: int,
    request: Request,
    user=CurrentUser,
):
    release = open_helper_release_download(request.app.state.settings, release_id)
    handle = release["handle"]
    audit_details = {
        "platform": release["platform"],
        "runtime_id": release["runtime_id"],
        "package_target": release.get("package_target"),
        "version": release["version"],
        "channel": release["channel"],
    }

    def audit(outcome: str) -> None:
        log_audit_event(
            request.app.state.settings,
            action="desktop_helper.download",
            outcome=outcome,
            user_id=user.id,
            username=user.username,
            role=user.role,
            session_id=user.session_id,
            target_type="desktop_helper_release",
            target_id=release_id,
            ip_address=resolve_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            details=audit_details,
        )

    try:
        filename = str(release["filename"])
        content_disposition = _download_content_disposition(filename)
        size_bytes = int(release["size_bytes"])
        byte_range = _resolve_download_range(request.headers.get("range"), size_bytes)
        audit("started")
    except BaseException:
        handle.close()
        raise

    start, end = byte_range or (0, size_bytes - 1)
    content_length = end - start + 1
    handle.seek(start)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": content_disposition,
        "Content-Length": str(content_length),
    }
    response_status = status.HTTP_206_PARTIAL_CONTENT if byte_range else status.HTTP_200_OK
    if byte_range:
        headers["Content-Range"] = f"bytes {start}-{end}/{size_bytes}"
    if request.method == "HEAD":
        handle.close()
        audit("success")
        return Response(
            status_code=response_status,
            media_type="application/zip",
            headers=headers,
        )
    return StreamingResponse(
        _stream_and_close(
            handle,
            remaining=content_length,
            audit_completion=audit,
        ),
        status_code=response_status,
        media_type="application/zip",
        headers=headers,
    )
