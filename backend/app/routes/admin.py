from __future__ import annotations

import asyncio
import json
from queue import Empty

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from ..auth import CurrentAdmin, CurrentHeartbeatAdmin, clear_session_cookie, resolve_client_ip, revoke_sessions_for_user
from ..schemas import (
    AdminTechnicalMetadataEnrichmentRequest,
    AdminTechnicalMetadataEnrichmentTriggerResponse,
    AdminTechnicalMetadataStatusResponse,
    AdminDownloadAccessResponse,
    AdminDownloadAccessUpdateRequest,
    AdminUrlPrefixResponse,
    AdminUrlPrefixRotateRequest,
    AdminUrlPrefixRotateResponse,
    AdminUserTotpDisableRequest,
    AdminUserTotpDisableResponse,
    AdminUserTotpSetupPromptUpdateRequest,
    AdminInviteCodeListResponse,
    AdminInviteCodeCreateRequest,
    AdminInviteCodeResponse,
    AdminPlaybackWorkersStatusResponse,
    AdminPasswordUpdateRequest,
    AdminSessionListResponse,
    AdminSelfDeleteRequest,
    AdminUserDeleteRequest,
    BackupCheckpointCreateResponse,
    BackupCheckpointCreateRequest,
    BackupCheckpointInspectResponse,
    BackupCheckpointListResponse,
    BackupCheckpointPassphraseRequest,
    BackupRestorePlanResponse,
    ExposureModeDraftRequest,
    ExposureModePlanResponse,
    AdminUserCreateRequest,
    AdminUserListResponse,
    AdminUserResponse,
    AdminUserUpdateRequest,
    AssistantUserAccessResponse,
    AssistantUserAccessUpdateRequest,
    AuditLogListResponse,
    PasswordHelpDismissRequest,
    PasswordHelpRequestListResponse,
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
from ..db import get_connection, utcnow_iso
from ..security import verify_password
from ..spa_static import mount_spa
from ..services.assistant_service import update_assistant_user_access
from ..services.audit_service import log_audit_event
from ..services.security_event_service import log_security_event
from ..services.admin_service import (
    create_user,
    delete_user,
    delete_self,
    list_active_sessions,
    list_audit_log,
    list_users,
    revoke_session,
    update_user_password,
    update_user,
)
from ..services.account_access_service import (
    dismiss_password_help_request,
    generate_invite_code,
    get_download_access_for_user,
    hide_invite_code_display,
    list_password_help_requests,
    list_visible_invite_codes,
    revoke_invite_code,
    update_download_access_for_user,
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
from ..services.exposure_mode_service import (
    DIRECT_PUBLIC_IP_WARNING,
    build_current_exposure_status,
    clear_pending_exposure_draft,
    save_pending_exposure_draft,
    validate_exposure_plan,
)
from ..services.library_service import (
    hide_media_item_globally,
    list_globally_hidden_media_items,
    show_media_item_globally,
)
from ..services.media_technical_metadata_service import (
    get_local_technical_metadata_enrichment_status,
    trigger_local_technical_metadata_enrichment_batch,
)
from ..services.native_playback_service import get_admin_native_playback_status
from ..services.local_library_source_service import validate_library_reference_locations
from ..url_prefix_service import get_url_prefix_status, rotate_url_prefix


router = APIRouter(prefix="/api/admin", tags=["admin"])


def _invalidate_user_sessions_if_available(request: Request, user_id: int, *, reason: str) -> None:
    manager = getattr(request.app.state, "mobile_playback_manager", None)
    invalidate = getattr(manager, "invalidate_user_sessions", None)
    if callable(invalidate):
        invalidate(user_id, reason=reason)


def _resolve_admin_checkpoint_path(settings, checkpoint_id: str):
    try:
        return resolve_backup_checkpoint_path(settings, checkpoint_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _verify_current_admin_password_for_route(settings, *, actor, current_admin_password: str) -> None:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT password_hash, enabled, role
            FROM users
            WHERE id = ?
            LIMIT 1
            """,
            (actor.id,),
        ).fetchone()
        if row is None or not bool(row["enabled"]) or (row["role"] or "standard_user") != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access is required",
            )
        ok, new_hash = verify_password(current_admin_password, row["password_hash"], settings)
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Current admin password is incorrect",
            )
        if new_hash is not None:
            connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (new_hash, utcnow_iso(), actor.id),
            )
            connection.commit()


@router.get("/users", response_model=AdminUserListResponse)
def admin_users(request: Request, user=CurrentAdmin) -> AdminUserListResponse:
    del user
    return AdminUserListResponse(users=list_users(request.app.state.settings))


@router.get("/url-prefix", response_model=AdminUrlPrefixResponse)
def admin_url_prefix(request: Request, user=CurrentAdmin) -> AdminUrlPrefixResponse:
    del user
    prefix = getattr(request.app.state, "url_prefix", "") or ""
    return AdminUrlPrefixResponse(**get_url_prefix_status(request.app.state.settings, prefix))


@router.post("/url-prefix/rotate", response_model=AdminUrlPrefixRotateResponse)
def admin_rotate_url_prefix(
    payload: AdminUrlPrefixRotateRequest,
    request: Request,
    user=CurrentAdmin,
) -> AdminUrlPrefixRotateResponse:
    settings = request.app.state.settings
    _verify_current_admin_password_for_route(
        settings,
        actor=user,
        current_admin_password=payload.current_admin_password,
    )
    with get_connection(settings) as connection:
        auth_session_ids = [
            int(row["id"])
            for row in connection.execute("SELECT id FROM sessions").fetchall()
        ]
        _old_prefix, new_prefix = rotate_url_prefix(
            settings,
            connection,
            actor_user_id=user.id,
            actor_username=user.username,
        )
        connection.commit()
    manager = getattr(request.app.state, "mobile_playback_manager", None)
    invalidate_auth_session = getattr(manager, "invalidate_auth_session", None)
    if callable(invalidate_auth_session):
        for auth_session_id in auth_session_ids:
            invalidate_auth_session(auth_session_id, reason="admin_revoked")
    request.app.state.url_prefix = new_prefix
    mount_spa(request.app, prefix=new_prefix)
    return AdminUrlPrefixRotateResponse(new_prefix=new_prefix, session_revoked=True)


@router.get("/exposure/status", response_model=ExposureModePlanResponse)
def admin_exposure_status(request: Request, user=CurrentAdmin) -> ExposureModePlanResponse:
    del user
    return ExposureModePlanResponse(
        **build_current_exposure_status(request.app.state.settings, request)
    )


@router.post("/exposure/validate", response_model=ExposureModePlanResponse)
def admin_validate_exposure_plan(
    payload: ExposureModeDraftRequest,
    request: Request,
    user=CurrentAdmin,
) -> ExposureModePlanResponse:
    del user
    return ExposureModePlanResponse(
        **validate_exposure_plan(request.app.state.settings, request, payload)
    )


@router.post("/exposure/drafts", response_model=ExposureModePlanResponse)
def admin_save_exposure_draft(
    payload: ExposureModeDraftRequest,
    request: Request,
    user=CurrentAdmin,
) -> ExposureModePlanResponse:
    settings = request.app.state.settings
    if not payload.current_admin_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current admin password is required to save an exposure draft.",
        )
    if not payload.acknowledgement:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Acknowledge that Phase 1 drafts do not change runtime behavior before saving.",
        )
    if payload.desired_mode == "public" and payload.public_entry_kind == "direct_ip":
        if not payload.direct_ip_not_recommended_acknowledgement:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=DIRECT_PUBLIC_IP_WARNING,
            )
    _verify_current_admin_password_for_route(
        settings,
        actor=user,
        current_admin_password=payload.current_admin_password,
    )
    validation_snapshot = validate_exposure_plan(settings, request, payload)
    blocking_errors = validation_snapshot.get("validation", {}).get("errors", [])
    if blocking_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Exposure draft has blocking validation errors.",
                "errors": blocking_errors,
            },
        )
    return ExposureModePlanResponse(
        **save_pending_exposure_draft(
            settings,
            user,
            payload,
            validation_snapshot=validation_snapshot,
        )
    )


@router.delete("/exposure/drafts", response_model=ExposureModePlanResponse)
def admin_clear_exposure_draft(request: Request, user=CurrentAdmin) -> ExposureModePlanResponse:
    cleared = clear_pending_exposure_draft(request.app.state.settings, user)
    status_payload = build_current_exposure_status(request.app.state.settings, request)
    status_payload.update(cleared)
    return ExposureModePlanResponse(**status_payload)


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
        age_credential=payload.age_credential,
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
        age_credential=payload.age_credential,
        current_admin_password=payload.current_admin_password,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    if payload.enabled is False:
        _invalidate_user_sessions_if_available(
            request,
            user_id,
            reason="user_disabled",
        )
    if payload.age_credential is not None:
        revoke_summary = updated.get("_age_revoke_summary") if isinstance(updated, dict) else None
        manager = getattr(request.app.state, "mobile_playback_manager", None)
        invalidate = getattr(manager, "invalidate_sessions_for_media_items_and_users", None)
        if callable(invalidate):
            invalidate(
                media_item_ids=[int(item_id) for item_id in (revoke_summary or {}).get("media_item_ids", [])],
                user_ids=[user_id],
                reason="user_age_credential_changed",
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


@router.delete("/users/{user_id}", response_model=MessageResponse)
def admin_delete_user(
    user_id: int,
    payload: AdminUserDeleteRequest,
    request: Request,
    user=CurrentAdmin,
) -> MessageResponse:
    deleted = delete_user(
        request.app.state.settings,
        user_id=user_id,
        confirm=payload.confirm,
        current_admin_password=payload.current_admin_password,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _invalidate_user_sessions_if_available(request, user_id, reason="user_deleted")
    return MessageResponse(message=f"Deleted user {deleted['username']}")


@router.post("/users/{user_id}/2fa/disable", response_model=AdminUserTotpDisableResponse)
def admin_disable_user_totp(
    user_id: int,
    payload: AdminUserTotpDisableRequest,
    request: Request,
    user=CurrentAdmin,
) -> AdminUserTotpDisableResponse:
    settings = request.app.state.settings
    _verify_current_admin_password_for_route(
        settings,
        actor=user,
        current_admin_password=payload.current_admin_password,
    )
    now = utcnow_iso()
    with get_connection(settings) as connection:
        row = connection.execute(
            "SELECT id, username FROM users WHERE id = ? LIMIT 1",
            (user_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        connection.execute(
            """
            UPDATE users
            SET totp_secret = NULL,
                totp_enabled_at = NULL,
                totp_last_used_window = NULL,
                totp_setup_skipped_at = ?,
                totp_setup_prompt_enabled = 0,
                updated_at = ?
            WHERE id = ?
            """,
            (now, now, user_id),
        )
        connection.execute("DELETE FROM user_recovery_codes WHERE user_id = ?", (user_id,))
        connection.commit()
    revoke_sessions_for_user(settings, user_id=user_id, reason="admin_disabled_totp")
    log_security_event(
        settings,
        event_kind="admin_disabled_user_totp",
        actor_user_id=user.id,
        actor_username=user.username,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={"target_user_id": user_id},
    )
    return AdminUserTotpDisableResponse(disabled=True, target_user=user_id)


@router.patch("/users/{user_id}/2fa/setup-prompt", response_model=AdminUserResponse)
def admin_update_user_totp_setup_prompt(
    user_id: int,
    payload: AdminUserTotpSetupPromptUpdateRequest,
    request: Request,
    user=CurrentAdmin,
) -> AdminUserResponse:
    settings = request.app.state.settings
    now = utcnow_iso()
    with get_connection(settings) as connection:
        row = connection.execute(
            "SELECT id, username, totp_secret FROM users WHERE id = ? LIMIT 1",
            (user_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        connection.execute(
            """
            UPDATE users
            SET totp_setup_prompt_enabled = ?,
                totp_setup_skipped_at = CASE WHEN ? THEN NULL ELSE totp_setup_skipped_at END,
                updated_at = ?
            WHERE id = ?
            """,
            (1 if payload.enabled else 0, 1 if payload.enabled else 0, now, user_id),
        )
        connection.commit()
    log_security_event(
        settings,
        event_kind="totp_setup_prompt_updated",
        actor_user_id=user.id,
        actor_username=user.username,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={
            "target_user_id": user_id,
            "target_username": row["username"],
            "enabled": payload.enabled,
            "target_totp_enabled": bool(row["totp_secret"]),
        },
    )
    updated = next(entry for entry in list_users(settings) if int(entry["id"]) == user_id)
    return AdminUserResponse(**updated)


@router.get("/invite-codes", response_model=AdminInviteCodeListResponse)
def admin_invite_codes(request: Request, user=CurrentAdmin) -> AdminInviteCodeListResponse:
    del user
    return AdminInviteCodeListResponse(invite_codes=list_visible_invite_codes(request.app.state.settings))


@router.post("/invite-codes", response_model=AdminInviteCodeResponse)
def admin_generate_invite_code(
    request: Request,
    payload: AdminInviteCodeCreateRequest | None = None,
    user=CurrentAdmin,
) -> AdminInviteCodeResponse:
    invite_code = generate_invite_code(
        request.app.state.settings,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        assigned_age=(payload.assigned_age if payload is not None else 18),
    )
    return AdminInviteCodeResponse(**invite_code)


@router.delete("/invite-codes/{invite_id}/display", response_model=MessageResponse)
def admin_hide_invite_code_display(
    invite_id: int,
    request: Request,
    user=CurrentAdmin,
) -> MessageResponse:
    hide_invite_code_display(
        request.app.state.settings,
        invite_id=invite_id,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return MessageResponse(message="Invite code hidden from admin display")


@router.post("/invite-codes/{invite_id}/revoke", response_model=MessageResponse)
def admin_revoke_invite_code(
    invite_id: int,
    request: Request,
    user=CurrentAdmin,
) -> MessageResponse:
    revoke_invite_code(
        request.app.state.settings,
        invite_id=invite_id,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return MessageResponse(message="Invite code revoked")


@router.get("/password-help-requests", response_model=PasswordHelpRequestListResponse)
def admin_password_help_requests(request: Request, user=CurrentAdmin) -> PasswordHelpRequestListResponse:
    del user
    return PasswordHelpRequestListResponse(requests=list_password_help_requests(request.app.state.settings))


@router.post("/password-help-requests/{request_id}/dismiss", response_model=MessageResponse)
def admin_dismiss_password_help_request(
    request_id: int,
    payload: PasswordHelpDismissRequest,
    request: Request,
    user=CurrentAdmin,
) -> MessageResponse:
    dismiss_password_help_request(
        request.app.state.settings,
        request_id=request_id,
        confirm=payload.confirm,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return MessageResponse(message="Password help request dismissed")


@router.get("/users/{user_id}/download-access", response_model=AdminDownloadAccessResponse)
def admin_get_user_download_access(
    user_id: int,
    request: Request,
    user=CurrentAdmin,
) -> AdminDownloadAccessResponse:
    del user
    return AdminDownloadAccessResponse(**get_download_access_for_user(request.app.state.settings, user_id=user_id))


@router.put("/users/{user_id}/download-access", response_model=AdminDownloadAccessResponse)
def admin_update_user_download_access(
    user_id: int,
    payload: AdminDownloadAccessUpdateRequest,
    request: Request,
    user=CurrentAdmin,
) -> AdminDownloadAccessResponse:
    updated = update_download_access_for_user(
        request.app.state.settings,
        user_id=user_id,
        access_mode=payload.access_mode,
        media_item_ids=payload.media_item_ids,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return AdminDownloadAccessResponse(**updated)


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
    _invalidate_user_sessions_if_available(request, int(user.id), reason="self_deleted")
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
    route2_status = request.app.state.mobile_playback_manager.get_route2_worker_status()
    native_status = get_admin_native_playback_status(request.app.state.settings)
    return AdminPlaybackWorkersStatusResponse(
        **route2_status,
        **native_status,
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
def admin_create_backup(
    request: Request,
    payload: BackupCheckpointCreateRequest | None = Body(default=None),
    user=CurrentAdmin,
) -> BackupCheckpointCreateResponse:
    passphrase = (payload.passphrase if payload else None) or None
    created = create_backup_checkpoint(
        request.app.state.settings,
        backup_trigger="manual_admin_ui",
        auto_checkpoint=False,
        trigger_kind="manual",
        passphrase=passphrase,
        reason="admin_ui",
        initiated_by_user_id=user.id,
        initiated_by_username=user.username,
        operation_context={
            "route": "/api/admin/backups",
            "action": "admin.backup.create",
        },
    )
    summary = summarize_backup_checkpoint(
        created["backup_path"],
        settings=request.app.state.settings,
        passphrase=passphrase,
    )
    log_security_event(
        request.app.state.settings,
        event_kind="backup_created_encrypted_manual",
        actor_user_id=user.id,
        actor_username=user.username,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={
            "checkpoint_id": summary["checkpoint_id"],
            "backup_key_source": summary.get("backup_key_source"),
        },
    )
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
    return _admin_inspect_backup_with_passphrase(
        checkpoint_id,
        request,
        passphrase=None,
    )


@router.post("/backups/{checkpoint_id}/inspect", response_model=BackupCheckpointInspectResponse)
def admin_inspect_backup_with_passphrase(
    checkpoint_id: str,
    payload: BackupCheckpointPassphraseRequest,
    request: Request,
    user=CurrentAdmin,
) -> BackupCheckpointInspectResponse:
    del user
    return _admin_inspect_backup_with_passphrase(
        checkpoint_id,
        request,
        passphrase=payload.passphrase,
    )


def _admin_inspect_backup_with_passphrase(
    checkpoint_id: str,
    request: Request,
    *,
    passphrase: str | None,
) -> BackupCheckpointInspectResponse:
    checkpoint_path = _resolve_admin_checkpoint_path(request.app.state.settings, checkpoint_id)
    inspection = inspect_backup_checkpoint(
        checkpoint_path,
        settings=request.app.state.settings,
        passphrase=passphrase,
    )
    summary = summarize_backup_checkpoint(
        checkpoint_path,
        settings=request.app.state.settings,
        passphrase=passphrase,
    )
    if inspection.get("encrypted") and not inspection.get("valid") and passphrase:
        log_security_event(
            request.app.state.settings,
            event_kind="backup_inspect_failed_wrong_passphrase",
            ip_address=resolve_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            details={"checkpoint_id": checkpoint_id},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Wrong backup passphrase.")
    manifest = inspection.get("manifest") or {}
    return BackupCheckpointInspectResponse(
        checkpoint_id=summary["checkpoint_id"],
        path=summary["path"],
        created_at_utc=summary["created_at_utc"],
        backup_trigger=summary["backup_trigger"],
        auto_checkpoint=summary["auto_checkpoint"],
        backup_storage=summary.get("backup_storage"),
        backup_encrypted=bool(summary.get("backup_encrypted")),
        backup_key_source=summary.get("backup_key_source"),
        contains_secrets=summary["contains_secrets"],
        warning=str(inspection.get("warning") or "") or None,
        valid=bool(inspection.get("valid")),
        db_integrity_check_result=summary["db_integrity_check_result"],
        total_size_bytes=int(inspection.get("total_size_bytes") or summary["total_size_bytes"]),
        file_count=int(inspection.get("file_count") or summary["file_count"]),
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
    return _admin_backup_restore_plan_with_passphrase(checkpoint_id, request, passphrase=None)


@router.post("/backups/{checkpoint_id}/restore-plan", response_model=BackupRestorePlanResponse)
def admin_backup_restore_plan_with_passphrase(
    checkpoint_id: str,
    payload: BackupCheckpointPassphraseRequest,
    request: Request,
    user=CurrentAdmin,
) -> BackupRestorePlanResponse:
    del user
    return _admin_backup_restore_plan_with_passphrase(
        checkpoint_id,
        request,
        passphrase=payload.passphrase,
    )


def _admin_backup_restore_plan_with_passphrase(
    checkpoint_id: str,
    request: Request,
    *,
    passphrase: str | None,
) -> BackupRestorePlanResponse:
    checkpoint_path = _resolve_admin_checkpoint_path(request.app.state.settings, checkpoint_id)
    try:
        return BackupRestorePlanResponse(
            **build_restore_dry_run_plan(
                request.app.state.settings,
                checkpoint_path,
                passphrase=passphrase,
            )
        )
    except ValueError:
        log_security_event(
            request.app.state.settings,
            event_kind="backup_inspect_failed_wrong_passphrase",
            ip_address=resolve_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            details={"checkpoint_id": checkpoint_id, "operation": "restore_plan"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Wrong backup passphrase.")


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
        explicit_same_host=False,
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
        explicit_same_host=False,
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
        purpose=payload.purpose,
    )
    return LocalDirectoryPickResponse(**result)


@router.put("/media-library-reference", response_model=MediaLibraryReferenceResponse)
def admin_update_media_library_reference(
    payload: MediaLibraryReferenceUpdateRequest,
    request: Request,
    user=CurrentAdmin,
) -> MediaLibraryReferenceResponse:
    validate_library_reference_locations(request.app.state.settings, value=payload.value)
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
            trigger_kind="auto",
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
        log_security_event(
            request.app.state.settings,
            event_kind="backup_created_encrypted_auto",
            actor_user_id=user.id,
            actor_username=user.username,
            ip_address=resolve_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            details={
                "checkpoint_id": auto_checkpoint.get("checkpoint_id") if auto_checkpoint else None,
                "backup_trigger": "auto_before_shared_local_path_update",
            },
        )
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
