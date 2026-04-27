from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse

from ..auth import CurrentUser
from ..schemas import (
    MobilePlaybackHeartbeatRequest,
    MobilePlaybackSeekRequest,
    MobilePlaybackSessionCreateRequest,
    MobilePlaybackSessionResponse,
    MobilePlaybackStopResponse,
)
from ..services.library_service import get_media_item_record


router = APIRouter(tags=["browser_playback"])


def _get_browser_manager(request: Request):
    return request.app.state.mobile_playback_manager


def _coerce_session_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError | PermissionError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Browser playback session not found")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Browser playback request failed")


def _rewrite_browser_session_payload(payload: dict[str, object]) -> dict[str, object]:
    normalized = dict(payload)
    for key in ("manifest_url", "active_manifest_url", "status_url", "seek_url", "heartbeat_url", "stop_url"):
        value = normalized.get(key)
        if isinstance(value, str):
            normalized[key] = value.replace("/api/mobile-playback/", "/api/browser-playback/")
    return normalized


@router.post("/api/browser-playback/sessions", response_model=MobilePlaybackSessionResponse)
def create_browser_playback_session(
    payload: MobilePlaybackSessionCreateRequest,
    request: Request,
    user=CurrentUser,
) -> MobilePlaybackSessionResponse:
    item = get_media_item_record(request.app.state.settings, item_id=payload.item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    try:
        response = _get_browser_manager(request).create_session(
            item,
            user_id=int(user.id),
            auth_session_id=user.session_id,
            username=user.username,
            profile=payload.profile,
            start_position_seconds=float(payload.start_position_seconds or 0.0),
            engine_mode=payload.engine_mode,
            playback_mode=payload.playback_mode,
        )
    except Exception as exc:  # noqa: BLE001
        raise _coerce_session_error(exc) from exc
    return MobilePlaybackSessionResponse(**_rewrite_browser_session_payload(response))


@router.get("/api/browser-playback/sessions/{session_id}", response_model=MobilePlaybackSessionResponse)
def get_browser_playback_session(
    session_id: str,
    request: Request,
    user=CurrentUser,
) -> MobilePlaybackSessionResponse:
    try:
        response = _get_browser_manager(request).get_session(session_id, user_id=int(user.id))
    except Exception as exc:  # noqa: BLE001
        raise _coerce_session_error(exc) from exc
    return MobilePlaybackSessionResponse(**_rewrite_browser_session_payload(response))


@router.get("/api/browser-playback/active", response_model=MobilePlaybackSessionResponse | None)
def get_active_browser_playback_session(
    request: Request,
    user=CurrentUser,
) -> MobilePlaybackSessionResponse | None:
    try:
        response = _get_browser_manager(request).get_active_session(user_id=int(user.id))
    except Exception as exc:  # noqa: BLE001
        raise _coerce_session_error(exc) from exc
    if response is None:
        return None
    return MobilePlaybackSessionResponse(**_rewrite_browser_session_payload(response))


@router.get("/api/browser-playback/items/{item_id}/active", response_model=MobilePlaybackSessionResponse | None)
def get_active_browser_playback_session_for_item(
    item_id: int,
    request: Request,
    user=CurrentUser,
) -> MobilePlaybackSessionResponse | None:
    try:
        response = _get_browser_manager(request).get_active_session_for_item(item_id, user_id=int(user.id))
    except Exception as exc:  # noqa: BLE001
        raise _coerce_session_error(exc) from exc
    if response is None:
        return None
    return MobilePlaybackSessionResponse(**_rewrite_browser_session_payload(response))


@router.post("/api/browser-playback/sessions/{session_id}/seek", response_model=MobilePlaybackSessionResponse)
def seek_browser_playback_session(
    session_id: str,
    payload: MobilePlaybackSeekRequest,
    request: Request,
    user=CurrentUser,
) -> MobilePlaybackSessionResponse:
    try:
        response = _get_browser_manager(request).seek_session(
            session_id,
            user_id=int(user.id),
            target_position_seconds=payload.target_position_seconds,
            last_stable_position_seconds=payload.last_stable_position_seconds,
            playing_before_seek=payload.playing_before_seek,
        )
    except Exception as exc:  # noqa: BLE001
        raise _coerce_session_error(exc) from exc
    return MobilePlaybackSessionResponse(**_rewrite_browser_session_payload(response))


@router.post("/api/browser-playback/sessions/{session_id}/heartbeat", response_model=MobilePlaybackSessionResponse)
def heartbeat_browser_playback_session(
    session_id: str,
    payload: MobilePlaybackHeartbeatRequest,
    request: Request,
    user=CurrentUser,
) -> MobilePlaybackSessionResponse:
    try:
        response = _get_browser_manager(request).update_runtime(
            session_id,
            user_id=int(user.id),
            committed_playhead_seconds=payload.committed_playhead_seconds,
            actual_media_element_time_seconds=payload.actual_media_element_time_seconds,
            client_attach_revision=payload.client_attach_revision,
            client_probe_bytes=payload.client_probe_bytes,
            client_probe_duration_ms=payload.client_probe_duration_ms,
            lifecycle_state=payload.lifecycle_state,
            stalled=payload.stalled,
            playing=payload.playing,
        )
    except Exception as exc:  # noqa: BLE001
        raise _coerce_session_error(exc) from exc
    return MobilePlaybackSessionResponse(**_rewrite_browser_session_payload(response))


@router.post("/api/browser-playback/sessions/{session_id}/stop", response_model=MobilePlaybackStopResponse)
def stop_browser_playback_session(
    session_id: str,
    request: Request,
    user=CurrentUser,
) -> MobilePlaybackStopResponse:
    try:
        stopped = _get_browser_manager(request).stop_session(session_id, user_id=int(user.id))
    except Exception as exc:  # noqa: BLE001
        raise _coerce_session_error(exc) from exc
    return MobilePlaybackStopResponse(
        stopped=stopped,
        message="Browser playback session released" if stopped else "No active browser playback session to release",
    )


@router.get("/api/browser-playback/sessions/{session_id}/index.m3u8")
def browser_playback_manifest(
    session_id: str,
    request: Request,
    user=CurrentUser,
):
    try:
        manifest_content = _get_browser_manager(request).get_manifest_content(
            session_id,
            user_id=int(user.id),
        )
    except Exception as exc:  # noqa: BLE001
        raise _coerce_session_error(exc) from exc
    return Response(
        content=manifest_content,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/api/browser-playback/sessions/{session_id}/init.mp4")
def browser_playback_init(
    session_id: str,
    request: Request,
    user=CurrentUser,
):
    try:
        init_path = _get_browser_manager(request).get_init_path(
            session_id,
            user_id=int(user.id),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise _coerce_session_error(exc) from exc
    return FileResponse(
        init_path,
        media_type="video/mp4",
        headers={"Cache-Control": "private, max-age=600, immutable"},
    )


@router.get("/api/browser-playback/sessions/{session_id}/segments/{segment_index}.m4s")
def browser_playback_segment(
    session_id: str,
    segment_index: int,
    request: Request,
    user=CurrentUser,
):
    try:
        segment_path = _get_browser_manager(request).get_segment_path(
            session_id,
            segment_index,
            user_id=int(user.id),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise _coerce_session_error(exc) from exc
    media_type = "video/iso.segment"
    if Path(segment_path).suffix.lower() == ".mp4":
        media_type = "video/mp4"
    return FileResponse(
        segment_path,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=600, immutable"},
    )


@router.get("/api/browser-playback/epochs/{epoch_id}/index.m3u8")
def browser_playback_epoch_manifest(
    epoch_id: str,
    request: Request,
    user=CurrentUser,
):
    try:
        manifest_content = _get_browser_manager(request).get_route2_epoch_manifest_content(
            epoch_id,
            user_id=int(user.id),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise _coerce_session_error(exc) from exc
    return Response(
        content=manifest_content,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/api/browser-playback/epochs/{epoch_id}/init.mp4")
def browser_playback_epoch_init(
    epoch_id: str,
    request: Request,
    user=CurrentUser,
):
    try:
        init_path = _get_browser_manager(request).get_route2_epoch_init_path(
            epoch_id,
            user_id=int(user.id),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise _coerce_session_error(exc) from exc
    return FileResponse(
        init_path,
        media_type="video/mp4",
        headers={"Cache-Control": "private, max-age=600, immutable"},
    )


@router.get("/api/browser-playback/epochs/{epoch_id}/segments/{segment_index}.m4s")
def browser_playback_epoch_segment(
    epoch_id: str,
    segment_index: int,
    request: Request,
    user=CurrentUser,
):
    try:
        segment_path = _get_browser_manager(request).get_route2_epoch_segment_path(
            epoch_id,
            segment_index,
            user_id=int(user.id),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise _coerce_session_error(exc) from exc
    media_type = "video/iso.segment"
    if Path(segment_path).suffix.lower() == ".mp4":
        media_type = "video/mp4"
    return FileResponse(
        segment_path,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=600, immutable"},
    )
