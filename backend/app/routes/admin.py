from __future__ import annotations

import asyncio
import json
from queue import Empty

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from ..auth import CurrentAdmin, CurrentHeartbeatAdmin, clear_session_cookie, resolve_client_ip
from ..schemas import (
    AdminTechnicalMetadataEnrichmentRequest,
    AdminTechnicalMetadataEnrichmentTriggerResponse,
    AdminTechnicalMetadataStatusResponse,
    AdminPlaybackWorkersStatusResponse,
    AdminPasswordUpdateRequest,
    AdminSessionListResponse,
    AdminSelfDeleteRequest,
    BackupCheckpointCreateResponse,
    BackupCheckpointInspectResponse,
    BackupCheckpointListResponse,
    BackupRestorePlanResponse,
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
from ..services.backup_service import (
    build_restore_dry_run_plan,
    create_backup_checkpoint,
    get_backups_dir_path,
    inspect_backup_checkpoint,
    list_backup_checkpoints,
    prune_backup_checkpoints,
    resolve_backup_checkpoint_path,
    summarize_backup_checkpoint,
)
from ..services.desktop_playback_service import resolve_same_host_request
from ..services.library_service import (
    hide_media_item_globally,
    list_globally_hidden_media_items,
    show_media_item_globally,
)
from ..services.media_technical_metadata_service import (
    get_local_technical_metadata_enrichment_status,
    trigger_local_technical_metadata_enrichment_batch,
)
from ..services.local_library_source_service import validate_shared_local_library_path


router = APIRouter(prefix="/api/admin", tags=["admin"])


def _resolve_admin_checkpoint_path(settings, checkpoint_id: str):
    try:
        return resolve_backup_checkpoint_path(settings, checkpoint_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


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
    if payload.enabled is False:
        request.app.state.mobile_playback_manager.invalidate_user_sessions(
            user_id,
            reason="user_disabled",
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
    request.app.state.mobile_playback_manager.invalidate_user_sessions(
        int(user.id),
        reason="self_deleted",
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
    request.app.state.mobile_playback_manager.invalidate_auth_session(
        session_id,
        reason="admin_revoked",
    )
    return MessageResponse(message="Session revoked")


@router.post(
    "/technical-metadata/enrich-local",
    response_model=AdminTechnicalMetadataEnrichmentTriggerResponse,
)
def admin_enrich_local_technical_metadata(
    payload: AdminTechnicalMetadataEnrichmentRequest,
    request: Request,
    user=CurrentAdmin,
) -> AdminTechnicalMetadataEnrichmentTriggerResponse:
    del user
    result = trigger_local_technical_metadata_enrichment_batch(
        request.app.state.settings,
        limit=payload.limit,
        retry_failed=payload.retry_failed,
    )
    return AdminTechnicalMetadataEnrichmentTriggerResponse(**result)


@router.get(
    "/technical-metadata/status",
    response_model=AdminTechnicalMetadataStatusResponse,
)
def admin_local_technical_metadata_status(
    request: Request,
    user=CurrentAdmin,
) -> AdminTechnicalMetadataStatusResponse:
    del user
    return AdminTechnicalMetadataStatusResponse(
        **get_local_technical_metadata_enrichment_status(request.app.state.settings)
    )


@router.get("/playback-workers", response_model=AdminPlaybackWorkersStatusResponse)
def admin_playback_workers(
    request: Request,
    user=CurrentAdmin,
) -> AdminPlaybackWorkersStatusResponse:
    del user
    return AdminPlaybackWorkersStatusResponse(
        **request.app.state.mobile_playback_manager.get_route2_worker_status()
    )


@router.post("/playback-workers/{worker_id}/terminate", response_model=MessageResponse)
def admin_terminate_playback_worker(
    worker_id: str,
    request: Request,
    user=CurrentAdmin,
) -> MessageResponse:
    del user
    terminated = request.app.state.mobile_playback_manager.terminate_route2_worker(
        worker_id,
        apply_admin_cooldown=True,
    )
    if not terminated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Playback worker not found",
        )
    return MessageResponse(message="Playback worker terminated")


@router.get("/audit", response_model=AuditLogListResponse)
def admin_audit_log(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    user=CurrentAdmin,
) -> AuditLogListResponse:
    del user
    return AuditLogListResponse(events=list_audit_log(request.app.state.settings, limit=limit))


@router.get("/backups", response_model=BackupCheckpointListResponse)
def admin_list_backups(request: Request, user=CurrentAdmin) -> BackupCheckpointListResponse:
    del user
    return BackupCheckpointListResponse(
        backups_dir=get_backups_dir_path(request.app.state.settings),
        checkpoints=list_backup_checkpoints(request.app.state.settings),
    )


@router.post("/backups", response_model=BackupCheckpointCreateResponse)
def admin_create_backup(request: Request, user=CurrentAdmin) -> BackupCheckpointCreateResponse:
    created = create_backup_checkpoint(
        request.app.state.settings,
        backup_trigger="manual_admin_ui",
        auto_checkpoint=False,
        reason="admin_ui",
        initiated_by_user_id=user.id,
        initiated_by_username=user.username,
        operation_context={
            "route": "/api/admin/backups",
            "action": "admin.backup.create",
        },
    )
    summary = summarize_backup_checkpoint(created["backup_path"])
    log_audit_event(
        request.app.state.settings,
        action="admin.backup.create",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        session_id=user.session_id,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={
            "checkpoint_id": summary["checkpoint_id"],
            "path": summary["path"],
            "created_at_utc": summary["created_at_utc"],
            "backup_trigger": summary["backup_trigger"],
        },
    )
    return BackupCheckpointCreateResponse(
        message="Backup checkpoint created.",
        warning=str(created.get("warning") or ""),
        checkpoint=summary,
    )


@router.get("/backups/{checkpoint_id}/inspect", response_model=BackupCheckpointInspectResponse)
def admin_inspect_backup(
    checkpoint_id: str,
    request: Request,
    user=CurrentAdmin,
) -> BackupCheckpointInspectResponse:
    del user
    checkpoint_path = _resolve_admin_checkpoint_path(request.app.state.settings, checkpoint_id)
    inspection = inspect_backup_checkpoint(checkpoint_path)
    summary = summarize_backup_checkpoint(checkpoint_path)
    manifest = inspection.get("manifest") or {}
    return BackupCheckpointInspectResponse(
        checkpoint_id=summary["checkpoint_id"],
        path=summary["path"],
        created_at_utc=summary["created_at_utc"],
        backup_trigger=summary["backup_trigger"],
        auto_checkpoint=summary["auto_checkpoint"],
        contains_secrets=summary["contains_secrets"],
        warning=str(inspection.get("warning") or "") or None,
        valid=bool(inspection.get("valid")),
        db_integrity_check_result=summary["db_integrity_check_result"],
        total_size_bytes=summary["total_size_bytes"],
        file_count=summary["file_count"],
        files_verified=int(inspection.get("files_verified") or 0),
        missing_files=list(inspection.get("missing_files") or []),
        hash_mismatches=list(inspection.get("hash_mismatches") or []),
        errors=list(inspection.get("errors") or []),
    )


@router.get("/backups/{checkpoint_id}/restore-plan", response_model=BackupRestorePlanResponse)
def admin_backup_restore_plan(
    checkpoint_id: str,
    request: Request,
    user=CurrentAdmin,
) -> BackupRestorePlanResponse:
    del user
    checkpoint_path = _resolve_admin_checkpoint_path(request.app.state.settings, checkpoint_id)
    return BackupRestorePlanResponse(
        **build_restore_dry_run_plan(request.app.state.settings, checkpoint_path)
    )


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
    validate_shared_local_library_path(request.app.state.settings, value=payload.value)
    existing_payload = get_media_library_reference_payload(request.app.state.settings)
    auto_backup_status = "created"
    auto_backup_error = None
    auto_checkpoint = None
    prune_summary = None
    try:
        auto_checkpoint = create_backup_checkpoint(
            request.app.state.settings,
            backup_trigger="auto_before_shared_local_path_update",
            auto_checkpoint=True,
            reason="shared_local_path_update",
            initiated_by_user_id=user.id,
            initiated_by_username=user.username,
            operation_context={
                "action": "admin.settings.media_library_reference",
                "existing_effective_path": existing_payload["effective_value"],
                "requested_value": payload.value,
            },
        )
    except Exception as exc:
        auto_backup_status = "failed"
        auto_backup_error = str(exc)
    else:
        try:
            prune_summary = prune_backup_checkpoints(request.app.state.settings, keep_auto=10)
        except Exception:
            prune_summary = None

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
            "auto_backup_status": auto_backup_status,
            "auto_backup_checkpoint_id": auto_checkpoint.get("checkpoint_id") if auto_checkpoint else None,
            "auto_backup_path": auto_checkpoint.get("backup_path") if auto_checkpoint else None,
            "auto_backup_created_at_utc": auto_checkpoint.get("created_at_utc") if auto_checkpoint else None,
            "auto_backup_error": auto_backup_error,
            "auto_backup_prune_summary": prune_summary,
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
