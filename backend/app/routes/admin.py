from __future__ import annotations

import asyncio
import json
from queue import Empty

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from ..auth import CurrentAdmin, CurrentHeartbeatAdmin, clear_session_cookie, resolve_client_ip
from ..schemas import (
    AdminPasswordUpdateRequest,
    AdminSessionListResponse,
    AdminSelfDeleteRequest,
    AdminUserCreateRequest,
    AdminUserListResponse,
    AdminUserResponse,
    AdminUserUpdateRequest,
    AssistantUserAccessResponse,
    AssistantUserAccessUpdateRequest,
    AuditLogListResponse,
    GoogleDriveSetupResponse,
    GoogleDriveSetupUpdateRequest,
    HiddenMovieListResponse,
    LocalDirectoryBrowseResponse,
    LocalDirectoryPickerCapabilityResponse,
    LocalDirectoryPickRequest,
    LocalDirectoryPickResponse,
    MediaLibraryReferenceResponse,
    MediaLibraryReferenceUpdateRequest,
    MessageResponse,
    PosterReferenceLocationResponse,
    PosterReferenceLocationUpdateRequest,
)
from ..services.assistant_service import update_assistant_user_access
from ..services.audit_service import log_audit_event
from ..services.admin_service import (
    create_user,
    delete_self,
    list_active_sessions,
    list_audit_log,
    list_users,
    revoke_session,
    update_user_password,
    update_user,
)
from ..services.app_settings_service import (
    get_google_drive_setup_payload,
    get_media_library_reference_payload,
    get_poster_reference_location_payload,
    browse_local_directories,
    get_native_local_directory_picker_capability,
    try_pick_local_directory,
    update_google_drive_setup,
    update_media_library_reference,
    update_poster_reference_location,
)
from ..services.desktop_playback_service import resolve_same_host_request
from ..services.library_service import (
    hide_media_item_globally,
    list_globally_hidden_media_items,
    show_media_item_globally,
)


router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users", response_model=AdminUserListResponse)
def admin_users(request: Request, user=CurrentAdmin) -> AdminUserListResponse:
    del user
    return AdminUserListResponse(users=list_users(request.app.state.settings))


@router.post("/users", response_model=AdminUserResponse)
def admin_create_user(
    payload: AdminUserCreateRequest,
    request: Request,
    user=CurrentAdmin,
) -> AdminUserResponse:
    created = create_user(
        request.app.state.settings,
        username=payload.username,
        password=payload.password,
        role=payload.role,
        enabled=payload.enabled,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return AdminUserResponse(**created)


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
def admin_update_user(
    user_id: int,
    payload: AdminUserUpdateRequest,
    request: Request,
    user=CurrentAdmin,
) -> AdminUserResponse:
    updated = update_user(
        request.app.state.settings,
        user_id=user_id,
        enabled=payload.enabled,
        role=payload.role,
        current_admin_password=payload.current_admin_password,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return AdminUserResponse(**updated)


@router.patch("/users/{user_id}/assistant-access", response_model=AssistantUserAccessResponse)
def admin_update_user_assistant_access(
    user_id: int,
    payload: AssistantUserAccessUpdateRequest,
    request: Request,
    user=CurrentAdmin,
) -> AssistantUserAccessResponse:
    updated = update_assistant_user_access(
        request.app.state.settings,
        target_user_id=user_id,
        assistant_beta_enabled=payload.assistant_beta_enabled,
        note=payload.note,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return AssistantUserAccessResponse(**updated)


@router.post("/users/{user_id}/password", response_model=MessageResponse)
def admin_update_user_password(
    user_id: int,
    payload: AdminPasswordUpdateRequest,
    request: Request,
    user=CurrentAdmin,
) -> MessageResponse:
    updated = update_user_password(
        request.app.state.settings,
        user_id=user_id,
        new_password=payload.new_password,
        current_admin_password=payload.current_admin_password,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return MessageResponse(message=f"Password updated for {updated['username']}")


@router.post("/self-delete", response_model=MessageResponse)
def admin_self_delete(
    payload: AdminSelfDeleteRequest,
    request: Request,
    response: Response,
    user=CurrentAdmin,
) -> MessageResponse:
    delete_self(
        request.app.state.settings,
        actor=user,
        current_admin_password=payload.current_admin_password,
        confirm=payload.confirm,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    clear_session_cookie(response, request.app.state.settings)
    return MessageResponse(message="Your admin account was deleted")


@router.get("/sessions", response_model=AdminSessionListResponse)
def admin_sessions(request: Request, user=CurrentAdmin) -> AdminSessionListResponse:
    del user
    return AdminSessionListResponse(sessions=list_active_sessions(request.app.state.settings))


@router.get("/events/stream")
async def admin_events_stream(request: Request, user=CurrentHeartbeatAdmin) -> StreamingResponse:
    del user
    subscriber_id, queue = request.app.state.admin_event_hub.subscribe()

    async def event_iterator():
        try:
            yield "retry: 5000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.to_thread(queue.get, True, 15.0)
                except Empty:
                    yield ": keepalive\n\n"
                    continue
                if event.get("event_type") == "stream_shutdown":
                    break
                event_name = str(event.get("event_type") or "message")
                payload = json.dumps(event, ensure_ascii=True, sort_keys=True)
                yield f"event: {event_name}\ndata: {payload}\n\n"
        finally:
            request.app.state.admin_event_hub.unsubscribe(subscriber_id)

    return StreamingResponse(
        event_iterator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sessions/{session_id}/revoke", response_model=MessageResponse)
def admin_revoke_session(
    session_id: int,
    request: Request,
    user=CurrentAdmin,
) -> MessageResponse:
    revoke_session(
        request.app.state.settings,
        session_id=session_id,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return MessageResponse(message="Session revoked")


@router.get("/audit", response_model=AuditLogListResponse)
def admin_audit_log(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    user=CurrentAdmin,
) -> AuditLogListResponse:
    del user
    return AuditLogListResponse(events=list_audit_log(request.app.state.settings, limit=limit))


@router.get("/global-hidden-items", response_model=HiddenMovieListResponse)
def admin_global_hidden_items(request: Request, user=CurrentAdmin) -> HiddenMovieListResponse:
    del user
    return HiddenMovieListResponse(items=list_globally_hidden_media_items(request.app.state.settings))


@router.get("/media-library-reference", response_model=MediaLibraryReferenceResponse)
def admin_get_media_library_reference(
    request: Request,
    user=CurrentAdmin,
) -> MediaLibraryReferenceResponse:
    del user
    return MediaLibraryReferenceResponse(
        **get_media_library_reference_payload(request.app.state.settings)
    )


@router.get("/local-directories", response_model=LocalDirectoryBrowseResponse)
def admin_browse_local_directories(
    request: Request,
    path: str = Query(default=""),
    user=CurrentAdmin,
) -> LocalDirectoryBrowseResponse:
    del user
    return LocalDirectoryBrowseResponse(
        **browse_local_directories(request.app.state.settings, path=path)
    )


@router.get("/local-directory-picker/capability", response_model=LocalDirectoryPickerCapabilityResponse)
def admin_local_directory_picker_capability(
    request: Request,
    platform: str = Query(default=""),
    same_host_hint: bool = Query(default=False),
    user=CurrentAdmin,
) -> LocalDirectoryPickerCapabilityResponse:
    del user
    same_host_context = resolve_same_host_request(
        request.app.state.settings,
        platform=str(platform or "").strip().lower(),
        client_ip=resolve_client_ip(request),
        request_host=request.url.hostname,
        explicit_same_host=bool(same_host_hint),
    )
    same_host_linux = bool(same_host_context["same_host"])
    if not same_host_linux:
        return LocalDirectoryPickerCapabilityResponse(
            native_picker_supported=False,
            same_host_linux=False,
            same_host_detection_source=str(same_host_context["detection_source"]),
            same_host_reason=str(same_host_context["reason"]),
            reason="Native host picker is only used for same-host Linux admin sessions.",
        )
    capability = get_native_local_directory_picker_capability()
    return LocalDirectoryPickerCapabilityResponse(
        native_picker_supported=bool(capability["native_picker_supported"]),
        same_host_linux=True,
        same_host_detection_source=str(same_host_context["detection_source"]),
        same_host_reason=str(same_host_context["reason"]),
        picker_backend=str(capability["picker_backend"]) if capability.get("picker_backend") else None,
        gui_session_available=bool(capability["gui_session_available"]),
        display_available=bool(capability["display_available"]),
        wayland_available=bool(capability["wayland_available"]),
        dbus_session_available=bool(capability["dbus_session_available"]),
        missing_dependency=str(capability["missing_dependency"]) if capability.get("missing_dependency") else None,
        reason=str(capability["reason"]) if capability["reason"] else None,
    )


@router.post("/local-directory-picker", response_model=LocalDirectoryPickResponse)
def admin_pick_local_directory(
    payload: LocalDirectoryPickRequest,
    request: Request,
    user=CurrentAdmin,
) -> LocalDirectoryPickResponse:
    del user
    same_host_context = resolve_same_host_request(
        request.app.state.settings,
        platform=str(payload.platform or "").strip().lower(),
        client_ip=resolve_client_ip(request),
        request_host=request.url.hostname,
        explicit_same_host=bool(payload.same_host_hint),
    )
    if not same_host_context["same_host"]:
        return LocalDirectoryPickResponse(
            status="unavailable",
            selected_path=None,
            reason="Native host picker is only used for same-host Linux admin sessions.",
            picker_backend=None,
        )
    result = try_pick_local_directory(
        request.app.state.settings,
        path=payload.path,
        title=payload.title,
    )
    return LocalDirectoryPickResponse(**result)


@router.put("/media-library-reference", response_model=MediaLibraryReferenceResponse)
def admin_update_media_library_reference(
    payload: MediaLibraryReferenceUpdateRequest,
    request: Request,
    user=CurrentAdmin,
) -> MediaLibraryReferenceResponse:
    updated = update_media_library_reference(
        request.app.state.settings,
        value=payload.value,
    )
    log_audit_event(
        request.app.state.settings,
        action="admin.settings.media_library_reference",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        session_id=user.session_id,
        target_type="app_setting",
        target_id="media_library_reference",
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={
            "configured_value": updated["configured_value"],
            "effective_value": updated["effective_value"],
        },
    )
    return MediaLibraryReferenceResponse(**updated)


@router.get("/poster-reference-location", response_model=PosterReferenceLocationResponse)
def admin_get_poster_reference_location(
    request: Request,
    user=CurrentAdmin,
) -> PosterReferenceLocationResponse:
    del user
    return PosterReferenceLocationResponse(
        **get_poster_reference_location_payload(request.app.state.settings)
    )


@router.put("/poster-reference-location", response_model=PosterReferenceLocationResponse)
def admin_update_poster_reference_location(
    payload: PosterReferenceLocationUpdateRequest,
    request: Request,
    user=CurrentAdmin,
) -> PosterReferenceLocationResponse:
    updated = update_poster_reference_location(
        request.app.state.settings,
        value=payload.value,
    )
    log_audit_event(
        request.app.state.settings,
        action="admin.settings.poster_reference_location",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        session_id=user.session_id,
        target_type="app_setting",
        target_id="poster_reference_location",
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={
            "configured_value": updated["configured_value"],
            "effective_value": updated["effective_value"],
        },
    )
    return PosterReferenceLocationResponse(**updated)


@router.get("/google-drive-setup", response_model=GoogleDriveSetupResponse)
def admin_get_google_drive_setup(
    request: Request,
    user=CurrentAdmin,
) -> GoogleDriveSetupResponse:
    payload = get_google_drive_setup_payload(
        request.app.state.settings,
        user_id=user.id,
    )
    return GoogleDriveSetupResponse(**payload)


@router.put("/google-drive-setup", response_model=GoogleDriveSetupResponse)
def admin_update_google_drive_setup(
    payload: GoogleDriveSetupUpdateRequest,
    request: Request,
    user=CurrentAdmin,
) -> GoogleDriveSetupResponse:
    updated = update_google_drive_setup(
        request.app.state.settings,
        user_id=user.id,
        https_origin=payload.https_origin,
        client_id=payload.client_id,
        client_secret=payload.client_secret,
    )
    log_audit_event(
        request.app.state.settings,
        action="admin.settings.google_drive_setup",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        session_id=user.session_id,
        target_type="app_setting",
        target_id="google_drive_setup",
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={
            "configuration_state": updated["configuration_state"],
            "connected": updated["connected"],
            "missing_fields": updated["missing_fields"],
            "https_origin": updated["https_origin"],
            "redirect_uri": updated["redirect_uri"],
        },
    )
    return GoogleDriveSetupResponse(**updated)


@router.post("/global-hidden-items/{item_id}", response_model=MessageResponse)
def admin_hide_movie_for_everyone(
    item_id: int,
    request: Request,
    user=CurrentAdmin,
) -> MessageResponse:
    try:
        hide_media_item_globally(
            request.app.state.settings,
            actor_user_id=user.id,
            item_id=item_id,
        )
    except ValueError as exc:
        if str(exc) == "not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found") from exc
        raise
    log_audit_event(
        request.app.state.settings,
        action="admin.library.hide_global",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        session_id=user.session_id,
        target_type="media_item",
        target_id=item_id,
        media_item_id=item_id,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return MessageResponse(message="This movie is hidden for everyone")


@router.delete("/global-hidden-items/{item_id}", response_model=MessageResponse)
def admin_show_movie_for_everyone(
    item_id: int,
    request: Request,
    user=CurrentAdmin,
) -> MessageResponse:
    show_media_item_globally(request.app.state.settings, item_id=item_id)
    log_audit_event(
        request.app.state.settings,
        action="admin.library.show_global",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        session_id=user.session_id,
        target_type="media_item",
        target_id=item_id,
        media_item_id=item_id,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return MessageResponse(message="This movie is visible again for everyone")
