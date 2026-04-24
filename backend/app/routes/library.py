from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse

from ..auth import CurrentUser, resolve_client_ip
from ..progress import refresh_recent_tracking
from ..schemas import LibraryListResponse, MediaItemDetail, ScanResponse
from ..services.library_service import (
    get_media_item_detail,
    get_media_item_poster_path,
    list_library,
    search_library,
)
from ..services.audit_service import log_audit_event


router = APIRouter(prefix="/api/library", tags=["library"])


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
        )

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
        },
    )
    message = (
        "Recent Watched refreshed. Scan started"
        if state["running"]
        else str(state.get("message") or "Scan state updated")
    )
    return ScanResponse(
        message=message,
        running=bool(state["running"]),
        job_id=state.get("job_id"),
    )
