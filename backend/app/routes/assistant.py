from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse

from ..auth import CurrentUser, resolve_client_ip
from ..schemas import (
    AssistantAttachmentExternalOpenResponse,
    AssistantRequestDetailEnvelope,
    AssistantRequestListResponse,
)
from ..services.assistant_service import (
    MAX_ATTACHMENT_BYTES,
    assistant_beta_enabled_for_user,
    create_assistant_request,
    get_assistant_attachment_file,
    issue_assistant_image_external_open_ticket,
    get_user_assistant_request_detail,
    list_user_assistant_requests,
    resolve_assistant_image_external_open_ticket,
)


router = APIRouter(prefix="/api/assistant", tags=["assistant"])
raw_router = APIRouter(tags=["assistant"])


def _require_assistant_access(request: Request, *, user) -> None:
    if assistant_beta_enabled_for_user(request.app.state.settings, user_id=user.id):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Assistant (Beta) is not enabled for this account",
    )


async def _read_attachment(upload: UploadFile | None) -> dict[str, object] | None:
    if upload is None or not upload.filename:
        return None
    content = await upload.read()
    if not content:
        return None
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Attachment must be 8 MB or smaller",
        )
    attachment_type = "other"
    mime_type = (upload.content_type or "").strip().lower()
    if mime_type.startswith("image/"):
        attachment_type = "image"
    elif mime_type.startswith("text/"):
        attachment_type = "text"
    return {
        "attachment_type": attachment_type,
        "original_filename": upload.filename,
        "mime_type": mime_type or None,
        "size_bytes": len(content),
        "content": content,
    }


async def _read_attachments(uploads: list[UploadFile]) -> list[dict[str, object]]:
    attachments: list[dict[str, object]] = []
    for upload in uploads:
        attachment_payload = await _read_attachment(upload)
        if attachment_payload is not None:
            attachments.append(attachment_payload)
    return attachments


@router.get("/requests", response_model=AssistantRequestListResponse)
def assistant_request_list(request: Request, user=CurrentUser) -> AssistantRequestListResponse:
    _require_assistant_access(request, user=user)
    return AssistantRequestListResponse(
        requests=list_user_assistant_requests(request.app.state.settings, user_id=user.id)
    )


@router.get("/requests/{request_id}", response_model=AssistantRequestDetailEnvelope)
def assistant_request_detail(request_id: int, request: Request, user=CurrentUser) -> AssistantRequestDetailEnvelope:
    _require_assistant_access(request, user=user)
    return AssistantRequestDetailEnvelope(
        request=get_user_assistant_request_detail(
            request.app.state.settings,
            user_id=user.id,
            request_id=request_id,
        )
    )


@router.get("/attachments/{attachment_id}")
def assistant_attachment_view(attachment_id: int, request: Request, user=CurrentUser):
    if user.role != "admin":
        _require_assistant_access(request, user=user)
    payload = get_assistant_attachment_file(
        request.app.state.settings,
        attachment_id=attachment_id,
        user=user,
    )
    return FileResponse(
        path=str(payload["path"]),
        media_type=str(payload["mime_type"]),
        filename=str(payload["filename"]),
        content_disposition_type="inline",
    )


@router.post(
    "/attachments/{attachment_id}/external-open",
    response_model=AssistantAttachmentExternalOpenResponse,
)
def assistant_attachment_external_open(
    attachment_id: int,
    request: Request,
    user=CurrentUser,
) -> AssistantAttachmentExternalOpenResponse:
    if user.role != "admin":
        _require_assistant_access(request, user=user)
    payload = issue_assistant_image_external_open_ticket(
        request.app.state.settings,
        attachment_id=attachment_id,
        user=user,
    )
    return AssistantAttachmentExternalOpenResponse(
        external_open_kind=str(payload["external_open_kind"]),
        external_open_url=str(payload["external_open_url"]),
        external_open_expires_at=str(payload["external_open_expires_at"]),
    )


@raw_router.get("/raw/assistant-images/{ticket_id}")
def assistant_attachment_raw_image(
    ticket_id: str,
    request: Request,
    token: str = Query(..., min_length=1),
):
    payload = resolve_assistant_image_external_open_ticket(
        request.app.state.settings,
        ticket_id=ticket_id,
        token=token,
    )
    return FileResponse(
        path=str(payload["path"]),
        media_type=str(payload["mime_type"]),
        filename=str(payload["filename"]),
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, no-store, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/requests",
    response_model=AssistantRequestDetailEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def assistant_create_request(
    request: Request,
    request_type: str = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    repro_steps: str | None = Form(default=None),
    expected_result: str | None = Form(default=None),
    actual_result: str | None = Form(default=None),
    urgency: str = Form(default="normal"),
    page_context: str | None = Form(default=None),
    platform: str | None = Form(default=None),
    app_version: str | None = Form(default=None),
    source_context: str | None = Form(default=None),
    related_entity_type: str | None = Form(default=None),
    related_entity_id: str | None = Form(default=None),
    attachments: list[UploadFile] | None = File(default=None),
    attachment: UploadFile | None = File(default=None),
    user=CurrentUser,
) -> AssistantRequestDetailEnvelope:
    _require_assistant_access(request, user=user)
    uploads = list(attachments or [])
    if attachment is not None:
        uploads.append(attachment)
    attachment_payloads = await _read_attachments(uploads)
    created = create_assistant_request(
        request.app.state.settings,
        user=user,
        request_type=request_type,
        title=title,
        description=description,
        repro_steps=repro_steps,
        expected_result=expected_result,
        actual_result=actual_result,
        urgency=urgency,
        page_context=page_context,
        platform=platform,
        app_version=app_version,
        source_context=source_context,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
        attachments=attachment_payloads,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return AssistantRequestDetailEnvelope(request=created)
