from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse

from ..auth import CurrentUser, resolve_client_ip
from ..progress import refresh_recent_tracking
from ..schemas import LibraryListResponse, MediaItemDetail, ScanResponse
from ..services.backup_service import create_backup_checkpoint, prune_backup_checkpoints
from ..services.cloud_library_service import sync_all_google_drive_sources
from ..services.library_service import (
    get_media_item_detail,
    get_media_item_poster_path,
    list_library,
    search_library,
)
from ..services.audit_service import log_audit_event


router = APIRouter(prefix="/api/library", tags=["library"])


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
def get_library(request: Request, user=CurrentUser) -> LibraryListResponse:
    request.app.state.scan_service.maybe_refresh_local_library(trigger="library")
    payload = list_library(request.app.state.settings, user_id=user.id)
    payload["scan_in_progress"] = request.app.state.scan_service.get_state()["running"]
    return LibraryListResponse(**payload)


@router.get("/search", response_model=LibraryListResponse)
def search(request: Request, q: str, user=CurrentUser) -> LibraryListResponse:
    payload = search_library(request.app.state.settings, user_id=user.id, query=q)
    payload["scan_in_progress"] = request.app.state.scan_service.get_state()["running"]
    return LibraryListResponse(**payload)


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
    return MediaItemDetail(**item)


@router.get("/item/{item_id}/poster")
def get_item_poster(item_id: int, request: Request, user=CurrentUser):
    poster_path = get_media_item_poster_path(
        request.app.state.settings,
        user_id=user.id,
        item_id=item_id,
        allow_globally_hidden=user.role == "admin",
    )
    if poster_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Poster not found")
    return FileResponse(
        poster_path,
        headers={"Cache-Control": "private, no-cache, max-age=0, must-revalidate"},
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
