from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse

from ..auth import CurrentUser
from ..schemas import (
    PlaybackDecisionResponse,
    PlaybackStartRequest,
)
from ..services.library_service import get_media_item_detail
from ..services.playback_service import build_playback_decision, start_playback, stop_playback


router = APIRouter(tags=["playback"])


def _get_item_or_404(request: Request, item_id: int, *, user_id: int) -> dict[str, object]:
    item = get_media_item_detail(
        request.app.state.settings,
        user_id=user_id,
        item_id=item_id,
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    return item


@router.get("/api/playback/{item_id}", response_model=PlaybackDecisionResponse)
def playback_decision(
    item_id: int,
    request: Request,
    force_hls: bool = False,
    user=CurrentUser,
) -> PlaybackDecisionResponse:
    item = _get_item_or_404(request, item_id, user_id=int(user.id))
    payload = build_playback_decision(
        request.app.state.settings,
        item,
        user_agent=request.headers.get("user-agent"),
        transcode_manager=request.app.state.transcode_manager,
        force_hls=force_hls,
    )
    return PlaybackDecisionResponse(**payload)


@router.post("/api/playback/{item_id}/start", response_model=PlaybackDecisionResponse)
def playback_start(
    item_id: int,
    request: Request,
    payload: PlaybackStartRequest | None = None,
    user=CurrentUser,
) -> PlaybackDecisionResponse:
    item = _get_item_or_404(request, item_id, user_id=int(user.id))
    request_payload = payload or PlaybackStartRequest()
    decision = start_playback(
        request.app.state.settings,
        item,
        user_id=int(user.id),
        user_agent=request.headers.get("user-agent"),
        transcode_manager=request.app.state.transcode_manager,
        force_hls=request_payload.force_hls,
    )
    return PlaybackDecisionResponse(**decision)


@router.post("/api/playback/{item_id}/stop")
def playback_stop(item_id: int, request: Request, user=CurrentUser):
    item = _get_item_or_404(request, item_id, user_id=int(user.id))
    stopped = stop_playback(
        item,
        user_id=int(user.id),
        transcode_manager=request.app.state.transcode_manager,
    )
    return {
        "stopped": stopped,
        "message": "Optimized playback session released" if stopped else "No active optimized playback session to release",
    }


@router.get("/api/hls/{item_id}/index.m3u8")
def hls_manifest(item_id: int, request: Request, user=CurrentUser):
    item = _get_item_or_404(request, item_id, user_id=int(user.id))
    manifest_content = request.app.state.transcode_manager.get_manifest_content(item)
    if manifest_content is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="HLS manifest is not ready yet")
    return Response(
        content=manifest_content,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/api/hls/{item_id}/{segment_name}")
def hls_segment(item_id: int, segment_name: str, request: Request, user=CurrentUser):
    item = _get_item_or_404(request, item_id, user_id=int(user.id))
    segment_path = request.app.state.transcode_manager.get_segment_path(item, segment_name)
    if segment_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="HLS segment not found")
    media_type = "video/mp2t"
    if Path(segment_name).suffix.lower() in {".m4s", ".mp4"}:
        media_type = "video/iso.segment"
    return FileResponse(
        segment_path,
        media_type=media_type,
        headers={"Cache-Control": "private, no-store"},
    )
