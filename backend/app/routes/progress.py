from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from ..auth import CurrentUser
from ..progress import get_progress, record_playback_event, save_progress
from ..schemas import PlaybackEventRequest, ProgressResponse, ProgressUpdateRequest
from ..services.library_service import get_media_item_detail


router = APIRouter(prefix="/api/progress", tags=["progress"])


@router.get("/{item_id}", response_model=ProgressResponse)
def fetch_progress(item_id: int, request: Request, user=CurrentUser) -> ProgressResponse:
    item = get_media_item_detail(
        request.app.state.settings,
        user_id=user.id,
        item_id=item_id,
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    payload = get_progress(
        request.app.state.settings,
        user_id=user.id,
        media_item_id=item_id,
    )
    return ProgressResponse(**payload)


@router.post("/{item_id}", response_model=ProgressResponse)
def update_progress(
    item_id: int,
    payload: ProgressUpdateRequest,
    request: Request,
    user=CurrentUser,
) -> ProgressResponse:
    item = get_media_item_detail(
        request.app.state.settings,
        user_id=user.id,
        item_id=item_id,
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    saved = save_progress(
        request.app.state.settings,
        user_id=user.id,
        media_item_id=item_id,
        position_seconds=payload.position_seconds,
        duration_seconds=payload.duration_seconds,
        completed=payload.completed,
        playback_mode=payload.playback_mode,
        event_type="playback_progress" if payload.playback_mode else None,
    )
    return ProgressResponse(**saved)


@router.post("/{item_id}/event", response_model=ProgressResponse)
def record_progress_event(
    item_id: int,
    payload: PlaybackEventRequest,
    request: Request,
    user=CurrentUser,
) -> ProgressResponse:
    item = get_media_item_detail(
        request.app.state.settings,
        user_id=user.id,
        item_id=item_id,
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")

    if payload.event_type == "playback_opened":
        record_playback_event(
            request.app.state.settings,
            user_id=user.id,
            media_item_id=item_id,
            event_type=payload.event_type,
            playback_mode=payload.playback_mode,
            position_seconds=payload.position_seconds,
            duration_seconds=payload.duration_seconds,
            occurred_at=payload.occurred_at,
        )
        current = get_progress(
            request.app.state.settings,
            user_id=user.id,
            media_item_id=item_id,
        )
        return ProgressResponse(**current)

    saved = save_progress(
        request.app.state.settings,
        user_id=user.id,
        media_item_id=item_id,
        position_seconds=float(payload.position_seconds or 0),
        duration_seconds=payload.duration_seconds,
        completed=payload.event_type == "playback_completed",
        playback_mode=payload.playback_mode,
        event_type=payload.event_type,
        occurred_at=payload.occurred_at,
        count_watch_increment=payload.event_type != "playback_seeked",
    )
    return ProgressResponse(**saved)
