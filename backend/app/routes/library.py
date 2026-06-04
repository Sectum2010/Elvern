from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse

from ..auth import CurrentAdmin, CurrentUser, resolve_client_ip
from ..progress import refresh_recent_tracking
from ..schemas import (
    LibraryListResponse,
    MediaAgeManualGroupLinkRequest,
    MediaAgeRequirementUpdateRequest,
    MediaGenreUpdateRequest,
    MediaItemDetail,
    ScanResponse,
)
from ..services.backup_service import create_backup_checkpoint, prune_backup_checkpoints
from ..services.cloud_library_service import sync_all_google_drive_sources
from ..services.library_service import (
    get_media_item_detail,
    get_media_item_poster_path,
    list_library,
    normalize_library_category,
    search_library,
)
from ..services.media_technical_metadata_service import run_one_media_item_technical_metadata_enrichment
from ..services.account_access_service import is_item_download_allowed
from ..services.media_age_access_service import (
    assert_user_can_access_media_by_age,
    link_media_item_to_age_group,
    list_age_group_members,
    list_age_groups_for_admin,
    revoke_persistent_sessions_for_age_group,
    search_media_items_for_age_group_link,
    set_media_age_requirement,
    unlink_media_item_from_age_group,
)
from ..services.media_genre_service import set_media_genres
from ..services.poster_display_cache_service import get_or_create_card_poster_display_cache
from ..services.user_settings_service import get_poster_card_display_max_width
from ..services.audit_service import log_audit_event
from ..services.security_event_service import log_security_event


router = APIRouter(prefix="/api/library", tags=["library"])


def _validated_library_category(category: str | None) -> str:
    try:
        return normalize_library_category(category)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error


def _invalidate_mobile_sessions_from_revoke_summary(request: Request, revoke_summary: dict[str, object], *, reason: str) -> None:
    manager = getattr(request.app.state, "mobile_playback_manager", None)
    invalidate = getattr(manager, "invalidate_sessions_for_media_items_and_users", None)
    if callable(invalidate):
        invalidate(
            media_item_ids=[int(item_id) for item_id in revoke_summary.get("media_item_ids") or []],
            user_ids=[int(user_id) for user_id in revoke_summary.get("user_ids") or []],
            reason=reason,
        )


def _recent_refresh_message(refresh_summary: dict[str, object]) -> str:
    if refresh_summary["rebuilt_items"] or refresh_summary["inserted_items"]:
        return "Recent Watched refreshed."
    return "Recent Watched is already current."


def _cloud_sync_message(cloud_summary: dict[str, object]) -> str:
    message = str(cloud_summary.get("message") or "").strip()
    if message:
        return message
    return "Cloud refresh status updated."


def _local_scan_message(state: dict[str, object]) -> str:
    if state["running"]:
        return "Local scan started."
    return str(state.get("message") or "Local scan state updated.")


def _rescan_message(
    refresh_summary: dict[str, object],
    cloud_summary: dict[str, object],
    state: dict[str, object],
) -> str:
    cloud_status = str(cloud_summary.get("status") or "disabled")
    if cloud_status in {"failed", "partial_failure"}:
        parts = [
            _local_scan_message(state),
            _cloud_sync_message(cloud_summary),
        ]
        if refresh_summary["rebuilt_items"] or refresh_summary["inserted_items"]:
            parts.append(_recent_refresh_message(refresh_summary))
        return " ".join(part for part in parts if part)
    return " ".join(
        [
            _recent_refresh_message(refresh_summary),
            _cloud_sync_message(cloud_summary),
            _local_scan_message(state),
        ]
    )


@router.get("", response_model=LibraryListResponse)
def get_library(request: Request, category: str | None = None, user=CurrentUser) -> LibraryListResponse:
    normalized_category = _validated_library_category(category)
    request.app.state.scan_service.maybe_refresh_local_library(trigger="library")
    payload = list_library(request.app.state.settings, user_id=user.id, category=normalized_category)
    payload["scan_in_progress"] = request.app.state.scan_service.get_state()["running"]
    return LibraryListResponse(**payload)


@router.get("/search", response_model=LibraryListResponse)
def search(request: Request, q: str, category: str | None = None, user=CurrentUser) -> LibraryListResponse:
    normalized_category = _validated_library_category(category)
    payload = search_library(request.app.state.settings, user_id=user.id, query=q, category=normalized_category)
    payload["scan_in_progress"] = request.app.state.scan_service.get_state()["running"]
    return LibraryListResponse(**payload)


@router.get("/age-groups")
def list_age_groups(request: Request, user=CurrentAdmin) -> dict[str, object]:
    del user
    return list_age_groups_for_admin(request.app.state.settings)


@router.get("/age-groups/search")
def search_age_groups(request: Request, q: str = "", user=CurrentAdmin) -> dict[str, object]:
    del user
    return search_media_items_for_age_group_link(request.app.state.settings, q)


@router.get("/age-groups/{age_group_key:path}")
def get_age_group(age_group_key: str, request: Request, user=CurrentAdmin) -> dict[str, object]:
    del user
    return list_age_group_members(request.app.state.settings, age_group_key)


@router.post("/age-groups/link")
def link_age_group(
    payload: MediaAgeManualGroupLinkRequest,
    request: Request,
    user=CurrentAdmin,
) -> dict[str, object]:
    result = link_media_item_to_age_group(
        request.app.state.settings,
        target_media_item_id=payload.target_media_item_id,
        source_item_id=payload.source_item_id,
        age_group_key=payload.age_group_key,
        note=payload.note,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _invalidate_mobile_sessions_from_revoke_summary(
        request,
        result.get("revoked_sessions") or {},
        reason="age_group_manual_link_changed",
    )
    return result


@router.delete("/age-groups/links/{media_item_id}")
def unlink_age_group(media_item_id: int, request: Request, user=CurrentAdmin) -> dict[str, object]:
    result = unlink_media_item_from_age_group(
        request.app.state.settings,
        target_media_item_id=media_item_id,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _invalidate_mobile_sessions_from_revoke_summary(
        request,
        result.get("revoked_sessions") or {},
        reason="age_group_manual_link_removed",
    )
    return result


@router.get("/item/{item_id}", response_model=MediaItemDetail)
def get_item(item_id: int, request: Request, user=CurrentUser) -> MediaItemDetail:
    item = get_media_item_detail(
        request.app.state.settings,
        user_id=user.id,
        item_id=item_id,
        allow_globally_hidden=user.role == "admin",
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    try:
        assert_user_can_access_media_by_age(request.app.state.settings, user=user, item_id=item_id, purpose="download")
        age_allows_download = True
    except HTTPException:
        age_allows_download = False
    item["download_access_allowed"] = age_allows_download and is_item_download_allowed(
        request.app.state.settings,
        user_id=user.id,
        item_id=item_id,
    )
    return MediaItemDetail(**item)


@router.patch("/item/{item_id}/age-requirement", response_model=MediaItemDetail)
def update_item_age_requirement(
    item_id: int,
    payload: MediaAgeRequirementUpdateRequest,
    request: Request,
    user=CurrentAdmin,
) -> MediaItemDetail:
    updated_requirement = set_media_age_requirement(
        request.app.state.settings,
        item_id=item_id,
        age_requirement=payload.age_requirement,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    revoke_summary = revoke_persistent_sessions_for_age_group(
        request.app.state.settings,
        age_group_key=str(updated_requirement["age_group_key"]),
        age_requirement=updated_requirement["age_requirement"],
        reason="age_requirement_changed",
    )
    _invalidate_mobile_sessions_from_revoke_summary(request, revoke_summary, reason="age_requirement_changed")
    item = get_media_item_detail(
        request.app.state.settings,
        user_id=user.id,
        item_id=item_id,
        allow_globally_hidden=True,
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    item["download_access_allowed"] = is_item_download_allowed(
        request.app.state.settings,
        user_id=user.id,
        item_id=item_id,
    )
    return MediaItemDetail(**item)


@router.patch("/item/{item_id}/genres", response_model=MediaItemDetail)
def update_item_genres(
    item_id: int,
    payload: MediaGenreUpdateRequest,
    request: Request,
    user=CurrentAdmin,
) -> MediaItemDetail:
    set_media_genres(
        request.app.state.settings,
        item_id=item_id,
        genres=payload.genres,
        actor=user,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    item = get_media_item_detail(
        request.app.state.settings,
        user_id=user.id,
        item_id=item_id,
        allow_globally_hidden=True,
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    item["download_access_allowed"] = is_item_download_allowed(
        request.app.state.settings,
        user_id=user.id,
        item_id=item_id,
    )
    return MediaItemDetail(**item)


@router.post("/item/{item_id}/track-scan", status_code=status.HTTP_202_ACCEPTED)
def request_item_track_scan(item_id: int, request: Request, user=CurrentUser) -> dict[str, object]:
    item = get_media_item_detail(
        request.app.state.settings,
        user_id=user.id,
        item_id=item_id,
        allow_globally_hidden=user.role == "admin",
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    result = run_one_media_item_technical_metadata_enrichment(
        request.app.state.settings,
        media_item_id=item_id,
        user_id=user.id,
        timeout_seconds=30,
    )
    return result


@router.get("/item/{item_id}/poster")
def get_item_poster(item_id: int, request: Request, user=CurrentUser, variant: str = "original"):
    normalized_variant = (variant or "original").strip().lower() or "original"
    if normalized_variant not in {"original", "card"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported poster variant")

    poster_path = get_media_item_poster_path(
        request.app.state.settings,
        user_id=user.id,
        item_id=item_id,
        allow_globally_hidden=user.role == "admin",
    )
    if poster_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Poster not found")

    response_path = poster_path
    cache_control = "private, no-cache, max-age=0, must-revalidate"
    if normalized_variant == "card":
        target_width = get_poster_card_display_max_width(request.app.state.settings, user_id=user.id)
        if target_width is not None:
            response_path = get_or_create_card_poster_display_cache(
                request.app.state.settings,
                poster_path,
                target_width=target_width,
            )
            cache_control = "private, max-age=604800, immutable"

    return FileResponse(
        response_path,
        headers={"Cache-Control": cache_control},
    )


@router.post("/rescan", response_model=ScanResponse, status_code=status.HTTP_202_ACCEPTED)
def rescan(request: Request, user=CurrentUser) -> ScanResponse:
    refresh_summary = refresh_recent_tracking(
        request.app.state.settings,
        user_id=user.id,
    )

    if user.role != "admin":
        message = (
            "Recent Watched refreshed."
            if refresh_summary["rebuilt_items"] or refresh_summary["inserted_items"]
            else "Recent Watched is already current."
        )
        log_audit_event(
            request.app.state.settings,
            action="library.recent.refresh",
            outcome="success",
            user_id=user.id,
            username=user.username,
            role=user.role,
            session_id=user.session_id,
            ip_address=resolve_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            details=refresh_summary,
        )
        return ScanResponse(
            message=message,
            running=False,
            job_id=None,
            cloud_sync=None,
        )

    auto_backup_status = "created"
    auto_backup_error = None
    auto_checkpoint = None
    prune_summary = None
    try:
        auto_checkpoint = create_backup_checkpoint(
            request.app.state.settings,
            backup_trigger="auto_before_admin_rescan",
            auto_checkpoint=True,
            trigger_kind="auto",
            reason="manual",
            initiated_by_user_id=user.id,
            initiated_by_username=user.username,
            operation_context={
                "route": "/api/library/rescan",
                "action": "admin.library.rescan",
                "reason": "manual",
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
                "backup_trigger": "auto_before_admin_rescan",
            },
        )
        try:
            prune_summary = prune_backup_checkpoints(request.app.state.settings, keep_auto=10)
        except Exception:
            prune_summary = None

    cloud_sync_error = None
    try:
        cloud_sync = sync_all_google_drive_sources(request.app.state.settings)
    except Exception as exc:
        cloud_sync_error = str(exc)
        cloud_sync = {
            "status": "failed",
            "provider_auth_required": False,
            "reconnect_required": False,
            "message": "Cloud refresh failed. Cloud library was not refreshed and may be stale.",
            "sources_total": 0,
            "sources_synced": 0,
            "sources_failed": 0,
            "media_rows_written": 0,
            "errors": [cloud_sync_error],
            "stale_state_warning": "Cloud library was not refreshed and may be stale until the next successful sync.",
            "source_results": [],
        }

    state = request.app.state.scan_service.enqueue_scan(reason="manual")
    log_audit_event(
        request.app.state.settings,
        action="admin.library.rescan",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        session_id=user.session_id,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={
            "running": bool(state["running"]),
            "job_id": state.get("job_id"),
            "recent_refresh": refresh_summary,
            "auto_backup_status": auto_backup_status,
            "auto_backup_checkpoint_id": auto_checkpoint.get("checkpoint_id") if auto_checkpoint else None,
            "auto_backup_path": auto_checkpoint.get("backup_path") if auto_checkpoint else None,
            "auto_backup_created_at_utc": auto_checkpoint.get("created_at_utc") if auto_checkpoint else None,
            "auto_backup_error": auto_backup_error,
            "auto_backup_prune_summary": prune_summary,
            "cloud_sync": cloud_sync,
            "cloud_sync_status": cloud_sync.get("status"),
            "cloud_sync_error": cloud_sync_error or next(
                (str(value) for value in cloud_sync.get("errors") or [] if str(value).strip()),
                None,
            ),
        },
    )
    message = _rescan_message(refresh_summary, cloud_sync, state)
    if auto_backup_status == "failed":
        message = f"{message}. Backup checkpoint failed; rescan started anyway."
    return ScanResponse(
        message=message,
        running=bool(state["running"]),
        job_id=state.get("job_id"),
        cloud_sync=cloud_sync,
    )
