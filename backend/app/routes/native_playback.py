from __future__ import annotations

import json
import logging
from time import monotonic
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse, Response

from ..auth import CurrentUser, resolve_client_ip
from ..schemas import (
    MessageResponse,
    NativePlaybackCloseRequest,
    NativePlaybackEventRequest,
    NativePlaybackHeartbeatResponse,
    NativePlaybackTransportProbeResponse,
    NativePlaybackProgressRequest,
    NativePlaybackSessionCreateRequest,
    NativePlaybackSessionResponse,
    ProgressResponse,
    TransportControllerDecisionResponse,
)
from ..services.library_service import get_media_item_detail
from ..services.audit_service import log_audit_event
from ..services.native_playback_service import (
    build_native_stream_response,
    close_native_playback_session,
    create_native_playback_session,
    get_native_playback_session_payload,
    heartbeat_native_playback_session,
    inspect_native_playback_access,
    record_native_playback_session_event,
    save_native_playback_session_progress,
)
from ..services.transport_controller_service import (
    attach_native_session_primary_target,
    build_ios_external_transport_request,
    resolve_transport_decision,
)


router = APIRouter(prefix="/api/native-playback", tags=["native-playback"])
logger = logging.getLogger(__name__)


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1", "[::1]"}


def _resolve_external_api_origin(request: Request) -> str:
    settings = request.app.state.settings
    configured_public_origin = settings.public_app_origin.strip().rstrip("/")
    if configured_public_origin:
        return configured_public_origin

    request_origin = str(request.base_url).rstrip("/")
    parsed_request_origin = urlsplit(request_origin)
    request_host = parsed_request_origin.hostname or ""
    if parsed_request_origin.scheme in {"http", "https"} and parsed_request_origin.netloc and not _is_loopback_host(request_host):
        return f"{parsed_request_origin.scheme}://{parsed_request_origin.netloc}"

    configured_backend_origin = settings.backend_origin.strip().rstrip("/")
    parsed_backend_origin = urlsplit(configured_backend_origin)
    backend_host = parsed_backend_origin.hostname or ""
    if (
        parsed_backend_origin.scheme in {"http", "https"}
        and parsed_backend_origin.netloc
        and not _is_loopback_host(backend_host)
    ):
        return configured_backend_origin

    if parsed_request_origin.scheme in {"http", "https"} and parsed_request_origin.netloc:
        return f"{parsed_request_origin.scheme}://{parsed_request_origin.netloc}"
    return configured_backend_origin or request_origin


def _rewrite_external_session_payload_urls(
    session_payload: dict[str, object],
    *,
    api_origin: str,
) -> dict[str, object]:
    payload = dict(session_payload)
    normalized_origin = api_origin.rstrip("/")
    parsed_origin = urlsplit(normalized_origin)
    if parsed_origin.scheme not in {"http", "https"} or not parsed_origin.netloc:
        return payload
    for key in ("details_url", "stream_url", "heartbeat_url", "progress_url", "event_url", "close_url"):
        value = str(payload.get(key) or "").strip()
        if not value:
            continue
        parsed_value = urlsplit(value)
        if not parsed_value.scheme or not parsed_value.netloc:
            continue
        payload[key] = urlunsplit(
            (
                parsed_origin.scheme,
                parsed_origin.netloc,
                parsed_value.path,
                parsed_value.query,
                parsed_value.fragment,
            )
        )
    payload["api_origin"] = normalized_origin
    return payload


def _resolve_ios_handoff_return_url(request: Request, *, item_id: int, return_path: str | None) -> str:
    candidate = (return_path or "").strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        candidate = f"/library/{item_id}"
    return f"{str(request.base_url).rstrip('/')}{candidate}"


def _build_ios_external_launch_url(
    *,
    app: str,
    stream_url: str,
    success_url: str,
    error_url: str,
) -> str:
    if app == "infuse":
        params = {"url": stream_url, "x-success": success_url, "x-error": error_url}
        return f"infuse://x-callback-url/play?{urlencode(params)}"
    # Keep VLC on the minimal handoff shape that previously worked on iPhone.
    # The recent callback parameters changed launch semantics and can cause VLC
    # to fail immediately instead of opening the stream.
    params = {"url": stream_url}
    return f"vlc-x-callback://x-callback-url/stream?{urlencode(params)}"


def _resolve_access_token(
    token: str | None,
    authorization: str | None,
) -> str:
    if token:
        return token
    if authorization and authorization.lower().startswith("bearer "):
        candidate = authorization.split(" ", 1)[1].strip()
        if candidate:
            return candidate
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Native playback access token is required",
    )


def _should_log_native_stream_debug(request: Request) -> bool:
    user_agent = str(request.headers.get("user-agent") or "").lower()
    client_host = (request.client.host if request.client else "") or ""
    return "vlc" in user_agent or not _is_loopback_host(client_host)


def _emit_native_stream_debug_log(
    request: Request,
    *,
    session_id: str,
    range_header: str | None,
    token_validation: str,
    status_code: int,
    response_headers: dict[str, str | None] | None = None,
    rejected_by: str | None = None,
    detail: str | None = None,
    validation_context: dict[str, object] | None = None,
    stream_context: dict[str, object] | None = None,
    phase: str = "open",
    request_id: str | None = None,
    elapsed_ms: float | None = None,
    bytes_sent: int | None = None,
) -> None:
    if not _should_log_native_stream_debug(request):
        return
    payload = {
        "event": "native_playback_stream_debug",
        "phase": phase,
        "request_id": request_id,
        "session_id": session_id,
        "item_id": (stream_context or {}).get("item_id"),
        "source_kind": (stream_context or {}).get("source_kind"),
        "stream_path_class": (stream_context or {}).get("stream_path_class"),
        "client_name": (stream_context or {}).get("client_name"),
        "container": (stream_context or {}).get("container"),
        "video_codec": (stream_context or {}).get("video_codec"),
        "audio_codec": (stream_context or {}).get("audio_codec"),
        "file_size": (stream_context or {}).get("file_size"),
        "duration_seconds": (stream_context or {}).get("duration_seconds"),
        "original_filename": (stream_context or {}).get("original_filename"),
        "method": request.method,
        "path": request.url.path,
        "query": request.url.query,
        "http_version": request.scope.get("http_version"),
        "client_host": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "range": range_header,
        "token_validation": token_validation,
        "status_code": status_code,
        "response_headers": response_headers or {},
        "rejected_by": rejected_by,
        "detail": detail,
        "validation_context": validation_context or {},
        "elapsed_ms": elapsed_ms,
        "bytes_sent": bytes_sent,
    }
    logger.info("Native playback stream debug %s", json.dumps(payload, ensure_ascii=True, sort_keys=True))


def _native_stream_debug_headers(stream_response) -> dict[str, str | None]:
    return {
        "accept-ranges": stream_response.headers.get("accept-ranges"),
        "content-length": stream_response.headers.get("content-length"),
        "content-range": stream_response.headers.get("content-range"),
        "content-type": stream_response.headers.get("content-type"),
        "transfer-encoding": stream_response.headers.get("transfer-encoding"),
    }


def _native_stream_context(stream_response) -> dict[str, object]:
    context = getattr(stream_response, "_elvern_native_stream_context", None)
    return dict(context) if isinstance(context, dict) else {}


def _wrap_native_stream_debug_iterator(
    body_iterator,
    *,
    request: Request,
    session_id: str,
    range_header: str | None,
    status_code: int,
    response_headers: dict[str, str | None],
    stream_context: dict[str, object],
    request_id: str,
    started_at: float,
):
    async def wrapped():
        bytes_sent = 0
        stream_error = None
        try:
            async for chunk in body_iterator:
                bytes_sent += len(chunk)
                yield chunk
        except Exception as exc:  # noqa: BLE001
            stream_error = str(exc)
            raise
        finally:
            _emit_native_stream_debug_log(
                request,
                session_id=session_id,
                range_header=range_header,
                token_validation="accepted",
                status_code=status_code,
                response_headers=response_headers,
                detail=stream_error,
                stream_context=stream_context,
                phase="complete",
                request_id=request_id,
                elapsed_ms=round((monotonic() - started_at) * 1000, 1),
                bytes_sent=bytes_sent,
            )

    return wrapped()


@router.post("/{item_id}/session", response_model=NativePlaybackSessionResponse)
def native_playback_session_create(
    item_id: int,
    request: Request,
    payload: NativePlaybackSessionCreateRequest | None = None,
    user=CurrentUser,
) -> NativePlaybackSessionResponse:
    logger.info(
        "native_playback_session_probe_entry %s",
        json.dumps(
            {
                "item_id": item_id,
                "client_name": payload.client_name if payload else None,
                "external_player": payload.external_player if payload else None,
                "requested_transport_mode": payload.requested_transport_mode if payload else None,
                "caller_surface": payload.caller_surface if payload else None,
                "current_path_class": payload.current_path_class if payload else None,
                "trusted_network_context": bool(payload.trusted_network_context) if payload else None,
                "allow_browser_fallback": bool(payload.allow_browser_fallback) if payload else None,
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
    )
    item = get_media_item_detail(
        request.app.state.settings,
        user_id=user.id,
        item_id=item_id,
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    transport_decision: TransportControllerDecisionResponse | None = None
    transport_request = build_ios_external_transport_request(
        request.app.state.settings,
        payload=payload,
        user_agent=request.headers.get("user-agent"),
    )
    if transport_request is not None:
        transport_decision = resolve_transport_decision(transport_request)
    session_payload = create_native_playback_session(
        request.app.state.settings,
        user_id=user.id,
        item=item,
        auth_session_id=user.session_id,
        user_agent=request.headers.get("user-agent"),
        source_ip=resolve_client_ip(request),
        client_name=(payload.client_name if payload else None),
    )
    session_payload = _rewrite_external_session_payload_urls(
        session_payload,
        api_origin=_resolve_external_api_origin(request),
    )
    if transport_decision is not None:
        transport_decision = attach_native_session_primary_target(
            transport_decision,
            stream_url=str(session_payload["stream_url"]),
            expires_at=str(session_payload["expires_at"]),
        )
        session_payload["transport_decision"] = transport_decision.model_dump(mode="json")
    session_payload["transport_probe"] = NativePlaybackTransportProbeResponse(
        item_id=item_id,
        client_name=payload.client_name if payload else None,
        external_player=payload.external_player if payload else None,
        requested_transport_mode=(
            transport_request.requested_transport_mode
            if transport_request is not None
            else (payload.requested_transport_mode if payload else None)
        ),
        caller_surface=(
            transport_request.caller_surface
            if transport_request is not None
            else (payload.caller_surface if payload else None)
        ),
        current_path_class=(
            transport_request.current_path_class
            if transport_request is not None
            else (payload.current_path_class if payload else None)
        ),
        trusted_network_context=(
            transport_request.trusted_network_context
            if transport_request is not None
            else (bool(payload.trusted_network_context) if payload else None)
        ),
        allow_browser_fallback=(
            transport_request.allow_browser_fallback
            if transport_request is not None
            else (bool(payload.allow_browser_fallback) if payload else None)
        ),
        transport_decision_exists=transport_decision is not None,
        selected_player=transport_decision.selected_player if transport_decision is not None else None,
        selected_mode=transport_decision.selected_mode if transport_decision is not None else None,
        primary_target_kind=(
            transport_decision.primary_target.target_kind
            if transport_decision is not None and transport_decision.primary_target is not None
            else None
        ),
        fallback_kind=(
            transport_decision.fallback.fallback_kind
            if transport_decision is not None and transport_decision.fallback is not None
            else None
        ),
        reason_code=(
            transport_decision.telemetry.reason_code
            if transport_decision is not None
            else None
        ),
    ).model_dump(mode="json")
    logger.info(
        "native_playback_session_probe_exit %s",
        json.dumps(
            {
                "item_id": item_id,
                "transport_decision_exists": transport_decision is not None,
                "selected_player": transport_decision.selected_player if transport_decision is not None else None,
                "selected_mode": transport_decision.selected_mode if transport_decision is not None else None,
                "primary_target_kind": (
                    transport_decision.primary_target.target_kind
                    if transport_decision is not None and transport_decision.primary_target is not None
                    else None
                ),
                "fallback_kind": (
                    transport_decision.fallback.fallback_kind
                    if transport_decision is not None and transport_decision.fallback is not None
                    else None
                ),
                "reason_code": (
                    transport_decision.telemetry.reason_code
                    if transport_decision is not None
                    else None
                ),
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
    )
    log_audit_event(
        request.app.state.settings,
        action="playback.handoff.create",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        session_id=user.session_id,
        target_type="media",
        target_id=item_id,
        media_item_id=item_id,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={
            "client_name": payload.client_name if payload else None,
            "mode": "native_playback",
            "transport_player": transport_decision.selected_player if transport_decision else None,
            "transport_selected_mode": transport_decision.selected_mode if transport_decision else None,
            "transport_reason_code": transport_decision.telemetry.reason_code if transport_decision else None,
        },
    )
    return NativePlaybackSessionResponse(**session_payload)


@router.get("/{item_id}/launch/{target_app}")
def native_playback_external_launch(
    item_id: int,
    target_app: str,
    request: Request,
    return_path: str | None = Query(default=None),
    user=CurrentUser,
):
    normalized_target_app = target_app.strip().lower()
    if normalized_target_app not in {"vlc", "infuse"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported external playback target")
    item = get_media_item_detail(
        request.app.state.settings,
        user_id=user.id,
        item_id=item_id,
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    session_payload = create_native_playback_session(
        request.app.state.settings,
        user_id=user.id,
        item=item,
        auth_session_id=user.session_id,
        user_agent=request.headers.get("user-agent"),
        source_ip=resolve_client_ip(request),
        client_name=(
            "Elvern iOS Infuse Handoff"
            if normalized_target_app == "infuse"
            else "Elvern iOS VLC Handoff"
        ),
    )
    session_payload = _rewrite_external_session_payload_urls(
        session_payload,
        api_origin=_resolve_external_api_origin(request),
    )
    if not session_payload.get("stream_url"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="External app handoff did not return a playback URL",
        )
    success_url = _resolve_ios_handoff_return_url(request, item_id=item_id, return_path=return_path)
    error_url = success_url
    launch_url = _build_ios_external_launch_url(
        app=normalized_target_app,
        stream_url=str(session_payload["stream_url"]),
        success_url=f"{success_url}{'&' if '?' in success_url else '?'}ios_app={normalized_target_app}&ios_result=success",
        error_url=f"{error_url}{'&' if '?' in error_url else '?'}ios_app={normalized_target_app}&ios_result=error",
    )
    log_audit_event(
        request.app.state.settings,
        action="playback.handoff.create",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        session_id=user.session_id,
        target_type="media",
        target_id=item_id,
        media_item_id=item_id,
        ip_address=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={
            "client_name": "Elvern iOS Infuse Handoff" if normalized_target_app == "infuse" else "Elvern iOS VLC Handoff",
            "mode": "native_playback_launch_redirect",
            "target_app": normalized_target_app,
        },
    )
    return RedirectResponse(url=launch_url, status_code=status.HTTP_302_FOUND)


@router.get("/session/{session_id}", response_model=NativePlaybackSessionResponse)
def native_playback_session_details(
    session_id: str,
    request: Request,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> NativePlaybackSessionResponse:
    access_token = _resolve_access_token(token, authorization)
    payload = get_native_playback_session_payload(
        request.app.state.settings,
        session_id=session_id,
        access_token=access_token,
        extend_ttl=True,
    )
    return NativePlaybackSessionResponse(**payload)


@router.post("/session/{session_id}/heartbeat", response_model=NativePlaybackHeartbeatResponse)
def native_playback_session_heartbeat(
    session_id: str,
    request: Request,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> NativePlaybackHeartbeatResponse:
    access_token = _resolve_access_token(token, authorization)
    payload = heartbeat_native_playback_session(
        request.app.state.settings,
        session_id=session_id,
        access_token=access_token,
    )
    return NativePlaybackHeartbeatResponse(**payload)


@router.post("/session/{session_id}/progress", response_model=ProgressResponse)
def native_playback_session_progress(
    session_id: str,
    payload: NativePlaybackProgressRequest,
    request: Request,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> ProgressResponse:
    access_token = _resolve_access_token(token, authorization)
    saved = save_native_playback_session_progress(
        request.app.state.settings,
        session_id=session_id,
        access_token=access_token,
        position_seconds=payload.position_seconds,
        duration_seconds=payload.duration_seconds,
        completed=payload.completed,
    )
    return ProgressResponse(**saved)


@router.post("/session/{session_id}/event", response_model=MessageResponse)
def native_playback_session_event(
    session_id: str,
    payload: NativePlaybackEventRequest,
    request: Request,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> MessageResponse:
    access_token = _resolve_access_token(token, authorization)
    record_native_playback_session_event(
        request.app.state.settings,
        session_id=session_id,
        access_token=access_token,
        event_type=payload.event_type,
        position_seconds=payload.position_seconds,
        duration_seconds=payload.duration_seconds,
        occurred_at=payload.occurred_at,
    )
    return MessageResponse(message="Native playback event recorded")


@router.post("/session/{session_id}/close", response_model=MessageResponse)
def native_playback_session_close(
    session_id: str,
    request: Request,
    payload: NativePlaybackCloseRequest | None = None,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> MessageResponse:
    access_token = _resolve_access_token(token, authorization)
    close_native_playback_session(
        request.app.state.settings,
        session_id=session_id,
        access_token=access_token,
        position_seconds=payload.position_seconds if payload else None,
        duration_seconds=payload.duration_seconds if payload else None,
        completed=bool(payload.completed) if payload else False,
    )
    return MessageResponse(message="Native playback session closed")


@router.api_route("/session/{session_id}/stream", methods=["GET", "HEAD"])
def native_playback_session_stream(
    session_id: str,
    request: Request,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
    range_header: str | None = Header(default=None, alias="Range"),
):
    request_id = uuid4().hex[:12]
    started_at = monotonic()
    try:
        access_token = _resolve_access_token(token, authorization)
    except HTTPException as exc:
        _emit_native_stream_debug_log(
            request,
            session_id=session_id,
            range_header=range_header,
            token_validation="missing",
            status_code=exc.status_code,
            rejected_by="access_token_resolver",
            detail=str(exc.detail),
            phase="reject",
            request_id=request_id,
            elapsed_ms=round((monotonic() - started_at) * 1000, 1),
        )
        raise

    try:
        stream_response = build_native_stream_response(
            request.app.state.settings,
            session_id=session_id,
            access_token=access_token,
            range_header=range_header,
            record_activity=request.method != "HEAD",
        )
    except HTTPException as exc:
        rejection_context = None
        rejected_by = "native_session_or_stream_contract"
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            rejection_context = inspect_native_playback_access(
                request.app.state.settings,
                session_id=session_id,
                access_token=access_token,
            )
            rejected_by = str(rejection_context.get("reason") or rejected_by)
        _emit_native_stream_debug_log(
            request,
            session_id=session_id,
            range_header=range_header,
            token_validation="rejected",
            status_code=exc.status_code,
            rejected_by=rejected_by,
            detail=str(exc.detail),
            validation_context=rejection_context,
            phase="reject",
            request_id=request_id,
            elapsed_ms=round((monotonic() - started_at) * 1000, 1),
        )
        raise
    except Exception as exc:  # noqa: BLE001
        _emit_native_stream_debug_log(
            request,
            session_id=session_id,
            range_header=range_header,
            token_validation="accepted",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            rejected_by="unexpected_stream_failure",
            detail=str(exc),
            phase="reject",
            request_id=request_id,
            elapsed_ms=round((monotonic() - started_at) * 1000, 1),
        )
        raise

    debug_headers = _native_stream_debug_headers(stream_response)
    stream_context = _native_stream_context(stream_response)
    _emit_native_stream_debug_log(
        request,
        session_id=session_id,
        range_header=range_header,
        token_validation="accepted",
        status_code=stream_response.status_code,
        response_headers=debug_headers,
        stream_context=stream_context,
        phase="open",
        request_id=request_id,
        elapsed_ms=round((monotonic() - started_at) * 1000, 1),
    )
    if request.method == "HEAD":
        return Response(
            status_code=stream_response.status_code,
            headers=dict(stream_response.headers),
            media_type=stream_response.media_type,
        )
    stream_response.body_iterator = _wrap_native_stream_debug_iterator(
        stream_response.body_iterator,
        request=request,
        session_id=session_id,
        range_header=range_header,
        status_code=stream_response.status_code,
        response_headers=debug_headers,
        stream_context=stream_context,
        request_id=request_id,
        started_at=started_at,
    )
    return stream_response
