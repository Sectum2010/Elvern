from __future__ import annotations

from fastapi import APIRouter, Request

from ..auth import CurrentAdmin
from ..schemas import SystemStatusResponse
from ..services.desktop_playback_service import get_desktop_playback_status
from ..services.native_playback_service import get_native_playback_status
from ..services.status_service import get_system_status


router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/status", response_model=SystemStatusResponse)
def system_status(request: Request, user=CurrentAdmin) -> SystemStatusResponse:
    del user
    payload = get_system_status(
        request.app.state.settings,
        scan_state=request.app.state.scan_service.get_state(),
        transcode_state=request.app.state.transcode_manager.get_debug_status(),
        native_playback_state=get_native_playback_status(request.app.state.settings),
        desktop_playback_state=get_desktop_playback_status(request.app.state.settings),
    )
    return SystemStatusResponse(**payload)
