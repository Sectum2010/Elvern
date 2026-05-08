from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Request, status

from ..auth import CurrentUser, resolve_client_ip
from ..media_stream import build_stream_response
from ..schemas import DownloadSessionResponse, DownloadSessionStatusRequest, MessageResponse
from ..services.account_access_service import (
    create_download_session,
    get_download_filename_for_item,
    is_download_session_still_authorized,
    mark_download_session_completed,
    mark_download_session_failed,
    mark_download_session_terminated,
    safe_download_filename,
    validate_download_session,
)
from ..services.cloud_library_service import build_cloud_stream_response


router = APIRouter(prefix="/api/download", tags=["download"])
DOWNLOAD_TOKEN_HEADER = "X-Elvern-Download-Token"


def _content_disposition(filename: str) -> str:
    safe_name = safe_download_filename(filename)
    return f"attachment; filename*=UTF-8''{quote(safe_name)}"


def _require_download_header_token(token: str | None) -> str:
    if not token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Download token is required")
    return token


def _build_authorized_download_response(
    *,
    settings,
    user,
    token: str,
    item_id: int,
    range_header: str | None,
    session_id: int | None = None,
):
    filename = get_download_filename_for_item(settings, user_id=user.id, item_id=item_id)

    def stream_validator() -> bool:
        return is_download_session_still_authorized(settings, token=token, user=user, session_id=session_id)

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
            session_id=session_id,
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
        response.headers["Content-Disposition"] = _content_disposition(filename)
        return response
    target.headers["Content-Disposition"] = _content_disposition(filename)
    return target


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
    return _build_authorized_download_response(
        settings=settings,
        user=user,
        token=token,
        item_id=item_id,
        range_header=range_header,
    )


@router.get("/session-stream/{session_id}")
def controlled_download_session(
    session_id: int,
    request: Request,
    range_header: str | None = Header(default=None, alias="Range"),
    download_token: str | None = Header(default=None, alias=DOWNLOAD_TOKEN_HEADER),
    user=CurrentUser,
):
    settings = request.app.state.settings
    token = _require_download_header_token(download_token)
    item_id = validate_download_session(settings, token=token, user=user, session_id=session_id)
    return _build_authorized_download_response(
        settings=settings,
        user=user,
        token=token,
        item_id=item_id,
        range_header=range_header,
        session_id=session_id,
    )


@router.post("/sessions/{token}/complete", response_model=MessageResponse)
def complete_download_session(
    token: str,
    request: Request,
    user=CurrentUser,
) -> MessageResponse:
    mark_download_session_completed(request.app.state.settings, token=token, user=user)
    return MessageResponse(message="Download completed")


@router.post("/session-stream/{session_id}/complete", response_model=MessageResponse)
def complete_controlled_download_session(
    session_id: int,
    request: Request,
    download_token: str | None = Header(default=None, alias=DOWNLOAD_TOKEN_HEADER),
    user=CurrentUser,
) -> MessageResponse:
    token = _require_download_header_token(download_token)
    mark_download_session_completed(request.app.state.settings, token=token, user=user, session_id=session_id)
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


@router.post("/session-stream/{session_id}/failed", response_model=MessageResponse)
def fail_controlled_download_session(
    session_id: int,
    payload: DownloadSessionStatusRequest,
    request: Request,
    download_token: str | None = Header(default=None, alias=DOWNLOAD_TOKEN_HEADER),
    user=CurrentUser,
) -> MessageResponse:
    token = _require_download_header_token(download_token)
    mark_download_session_failed(
        request.app.state.settings,
        token=token,
        user=user,
        message=payload.message,
        session_id=session_id,
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


@router.post("/session-stream/{session_id}/terminate", response_model=MessageResponse)
def terminate_controlled_download_session(
    session_id: int,
    request: Request,
    download_token: str | None = Header(default=None, alias=DOWNLOAD_TOKEN_HEADER),
    user=CurrentUser,
) -> MessageResponse:
    token = _require_download_header_token(download_token)
    mark_download_session_terminated(
        request.app.state.settings,
        token=token,
        user=user,
        session_id=session_id,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return MessageResponse(message="Download terminated")
