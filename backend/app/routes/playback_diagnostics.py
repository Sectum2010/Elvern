from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from ..auth import CurrentUser
from ..services.playback_diagnostics.schema import (
    PlaybackDiagnosticsBatchRequest,
    PlaybackDiagnosticsBatchResponse,
    PlaybackDiagnosticsBootstrapRequest,
    PlaybackDiagnosticsBootstrapResponse,
    PlaybackDiagnosticsClockRequest,
    PlaybackDiagnosticsClockResponse,
    PlaybackDiagnosticsCloseRequest,
    PlaybackDiagnosticsCloseResponse,
)
from ..services.playback_diagnostics.service import (
    PlaybackDiagnosticsOwnershipError,
    PlaybackDiagnosticsUnavailableError,
)


router = APIRouter(prefix="/api/playback-diagnostics", tags=["playback_diagnostics"])


def _service(request: Request):
    return request.app.state.playback_diagnostics_service


def _raise_diagnostics_error(exc: Exception) -> None:
    if isinstance(exc, PlaybackDiagnosticsOwnershipError | PermissionError | KeyError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Playback diagnostics session not found",
        ) from exc
    if isinstance(exc, PlaybackDiagnosticsUnavailableError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Playback diagnostics are unavailable",
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Playback diagnostics are unavailable",
    ) from exc


@router.post("/bootstrap", response_model=PlaybackDiagnosticsBootstrapResponse)
def bootstrap_playback_diagnostics(
    payload: PlaybackDiagnosticsBootstrapRequest,
    request: Request,
    user=CurrentUser,
) -> PlaybackDiagnosticsBootstrapResponse:
    try:
        return _service(request).bootstrap(
            payload,
            user_id=int(user.id),
            user_agent=request.headers.get("user-agent"),
        )
    except Exception as exc:  # noqa: BLE001
        _raise_diagnostics_error(exc)


@router.post("/batch", response_model=PlaybackDiagnosticsBatchResponse)
def ingest_playback_diagnostics(
    payload: PlaybackDiagnosticsBatchRequest,
    request: Request,
    user=CurrentUser,
) -> PlaybackDiagnosticsBatchResponse:
    try:
        return _service(request).ingest(
            diagnostics_session_id=payload.diagnostics_session_id,
            source_id=payload.source_id,
            events=payload.events,
            user_id=int(user.id),
        )
    except Exception as exc:  # noqa: BLE001
        _raise_diagnostics_error(exc)


@router.post("/clock", response_model=PlaybackDiagnosticsClockResponse)
def exchange_playback_diagnostics_clock(
    payload: PlaybackDiagnosticsClockRequest,
    request: Request,
    user=CurrentUser,
) -> PlaybackDiagnosticsClockResponse:
    try:
        return _service(request).clock_exchange(payload, user_id=int(user.id))
    except Exception as exc:  # noqa: BLE001
        _raise_diagnostics_error(exc)


@router.post("/close", response_model=PlaybackDiagnosticsCloseResponse)
def close_playback_diagnostics(
    payload: PlaybackDiagnosticsCloseRequest,
    request: Request,
    user=CurrentUser,
) -> PlaybackDiagnosticsCloseResponse:
    try:
        watermark, finalized, close_state = _service(request).close(
            playback_session_id=payload.diagnostics_session_id,
            source_id=payload.source_id,
            user_id=int(user.id),
            reason=payload.reason,
            final_source_sequence=payload.final_source_sequence,
        )
        return PlaybackDiagnosticsCloseResponse(
            accepted=True,
            ack_watermark=watermark,
            finalized=finalized,
            state=close_state,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_diagnostics_error(exc)
