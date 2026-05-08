from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Request, status

from ..auth import CurrentUser, resolve_client_ip
from ..media_stream import build_stream_response
from ..schemas import DownloadSessionResponse, DownloadSessionStatusRequest, MessageResponse
from ..services.account_access_service import (
    create_download_session,
    is_download_session_still_authorized,
    mark_download_session_completed,
    mark_download_session_failed,
    mark_download_session_terminated,
    safe_download_filename,
    validate_download_session,
)
from ..services.cloud_library_service import build_cloud_stream_response


router = APIRouter(prefix="/api/download", tags=["download"])


def _content_disposition(filename: str) -> str:
    safe_name = safe_download_filename(filename)
    return f"attachment; filename*=UTF-8''{quote(safe_name)}"


@router.post("/item/{item_id}/session", response_model=DownloadSessionResponse)
def create_item_download_session(
    item_id: int,
    request: Request,
    user=CurrentUser,
) -> DownloadSessionResponse:
    session_payload = create_download_session(
        request.app.state.settings,
        user=user,
        item_id=item_id,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return DownloadSessionResponse(**session_payload)


@router.get("/sessions/{token}")
def download_session(
    token: str,
    request: Request,
    range_header: str | None = Header(default=None, alias="Range"),
    user=CurrentUser,
):
    settings = request.app.state.settings
    item_id = validate_download_session(settings, token=token, user=user)

    def stream_validator() -> bool:
        return is_download_session_still_authorized(settings, token=token, user=user)

    target = build_cloud_stream_response(
        settings,
        user_id=user.id,
        item_id=item_id,
        range_header=range_header,
        stream_validator=stream_validator,
        validated_chunk_size=256 * 1024,
    )
    if target is None:
        mark_download_session_failed(
            settings,
            token=token,
            user=user,
            message="media_item_not_found",
            audit_action="download.failed",
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Movie not found")
    if isinstance(target, dict):
        response = build_stream_response(
            str(target["file_path"]),
            settings,
            range_header,
            validated_chunk_size=256 * 1024,
            stream_validator=stream_validator,
        )
        response.headers["Content-Disposition"] = _content_disposition(str(target.get("original_filename") or "movie"))
        return response
    target.headers["Content-Disposition"] = _content_disposition(str(target.headers.get("Content-Disposition") or "movie"))
    return target


@router.post("/sessions/{token}/complete", response_model=MessageResponse)
def complete_download_session(
    token: str,
    request: Request,
    user=CurrentUser,
) -> MessageResponse:
    mark_download_session_completed(request.app.state.settings, token=token, user=user)
    return MessageResponse(message="Download completed")


@router.post("/sessions/{token}/failed", response_model=MessageResponse)
def fail_download_session(
    token: str,
    payload: DownloadSessionStatusRequest,
    request: Request,
    user=CurrentUser,
) -> MessageResponse:
    mark_download_session_failed(
        request.app.state.settings,
        token=token,
        user=user,
        message=payload.message,
    )
    return MessageResponse(message="Download failure recorded")


@router.post("/sessions/{token}/terminate", response_model=MessageResponse)
def terminate_download_session(
    token: str,
    request: Request,
    user=CurrentUser,
) -> MessageResponse:
    mark_download_session_terminated(
        request.app.state.settings,
        token=token,
        user=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return MessageResponse(message="Download terminated")
