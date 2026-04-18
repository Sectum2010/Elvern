from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from ..auth import CurrentUser
from ..schemas import (
    CloudLibrariesResponse,
    CloudLibrarySourceCreateRequest,
    CloudLibrarySourceMoveRequest,
    CloudLibrarySourceSummary,
    GoogleDriveConnectRequest,
    GoogleDriveConnectResponse,
    MessageResponse,
)
from ..services.cloud_library_service import (
    add_google_drive_library_source,
    build_google_connect_callback_redirect,
    build_google_drive_connect_response,
    complete_google_drive_connect,
    get_cloud_libraries_payload,
    hide_shared_library_source_for_user,
    move_google_drive_library_source,
    resolve_google_connect_state,
    show_shared_library_source_for_user,
)


router = APIRouter(prefix="/api/cloud-libraries", tags=["cloud-libraries"])


@router.get("", response_model=CloudLibrariesResponse)
def read_cloud_libraries(request: Request, user=CurrentUser) -> CloudLibrariesResponse:
    payload = get_cloud_libraries_payload(request.app.state.settings, user=user)
    return CloudLibrariesResponse(**payload)


@router.post("/google/connect", response_model=GoogleDriveConnectResponse)
def cloud_libraries_google_connect(
    request: Request,
    payload: GoogleDriveConnectRequest | None = None,
    user=CurrentUser,
) -> GoogleDriveConnectResponse:
    response_payload = build_google_drive_connect_response(
        request.app.state.settings,
        user_id=user.id,
        return_path=payload.return_path if payload else None,
    )
    return GoogleDriveConnectResponse(**response_payload)


@router.get("/google/callback")
def cloud_libraries_google_callback(
    request: Request,
    state: str = Query(...),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    state_context = resolve_google_connect_state(state)
    if error:
        redirect_target = build_google_connect_callback_redirect(
            request.app.state.settings,
            success=False,
            message="Google Drive sign-in was cancelled or denied.",
            return_path=state_context.get("return_path"),
        )
        return RedirectResponse(redirect_target, status_code=303)
    if not code:
        raise HTTPException(status_code=400, detail="Google Drive callback code is missing.")
    try:
        result = complete_google_drive_connect(request.app.state.settings, state_token=state, code=code)
        redirect_target = build_google_connect_callback_redirect(
            request.app.state.settings,
            success=True,
            message="Google Drive connected.",
            return_path=str(result.get("return_path") or "") or None,
        )
    except HTTPException as exc:
        error_message = exc.detail
        if isinstance(error_message, dict):
            error_message = (
                error_message.get("message")
                or error_message.get("title")
                or "Google Drive reconnect failed."
            )
        redirect_target = build_google_connect_callback_redirect(
            request.app.state.settings,
            success=False,
            message=str(error_message),
            return_path=state_context.get("return_path"),
        )
    return RedirectResponse(redirect_target, status_code=303)


@router.post("/sources", response_model=CloudLibrarySourceSummary)
def create_cloud_library_source(
    payload: CloudLibrarySourceCreateRequest,
    request: Request,
    user=CurrentUser,
) -> CloudLibrarySourceSummary:
    created = add_google_drive_library_source(
        request.app.state.settings,
        user=user,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        shared=bool(payload.shared),
    )
    return CloudLibrarySourceSummary(**created)


@router.patch("/sources/{source_id}", response_model=CloudLibrarySourceSummary)
def move_cloud_library_source(
    source_id: int,
    payload: CloudLibrarySourceMoveRequest,
    request: Request,
    user=CurrentUser,
) -> CloudLibrarySourceSummary:
    updated = move_google_drive_library_source(
        request.app.state.settings,
        user=user,
        source_id=source_id,
        shared=bool(payload.shared),
    )
    return CloudLibrarySourceSummary(**updated)


@router.post("/sources/{source_id}/hide", response_model=MessageResponse)
def hide_shared_cloud_library_source(
    source_id: int,
    request: Request,
    user=CurrentUser,
) -> MessageResponse:
    hide_shared_library_source_for_user(request.app.state.settings, user=user, source_id=source_id)
    return MessageResponse(message="This shared library is hidden for your account.")


@router.delete("/sources/{source_id}/hide", response_model=MessageResponse)
def show_shared_cloud_library_source(
    source_id: int,
    request: Request,
    user=CurrentUser,
) -> MessageResponse:
    show_shared_library_source_for_user(request.app.state.settings, user=user, source_id=source_id)
    return MessageResponse(message="This shared library is visible again.")
