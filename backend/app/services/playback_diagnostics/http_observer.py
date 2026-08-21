from __future__ import annotations

import re
import time
import json
from typing import Any

from .ingress import next_diagnostic_correlation_id
from .privacy import normalized_route_identity
from .runtime import observe_runtime_event, record_runtime_health


SESSION_PATH = re.compile(r"^/api/browser-playback/sessions/([^/]+)(?:/|$)")
EPOCH_PATH = re.compile(r"^/api/browser-playback/epochs/([^/]+)(?:/|$)")
DIAGNOSTICS_API_PREFIX = "/api/playback-diagnostics/"
DEFAULT_DIAGNOSTICS_BODY_LIMIT_BYTES = 2_000_000


class DiagnosticsRequestBodyTooLarge(ValueError):
    pass


class PlaybackDiagnosticsBodyLimitMiddleware:
    """Bound diagnostics request bodies before FastAPI materializes JSON."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        path = str(scope.get("path") or "")
        if scope.get("type") != "http" or not path.startswith(DIAGNOSTICS_API_PREFIX):
            await self.app(scope, receive, send)
            return
        app = scope.get("app")
        settings = getattr(getattr(app, "state", None), "settings", None)
        configured = int(
            getattr(settings, "playback_diagnostics_batch_max_bytes", 0)
            or DEFAULT_DIAGNOSTICS_BODY_LIMIT_BYTES
        )
        limit = max(64_000, min(DEFAULT_DIAGNOSTICS_BODY_LIMIT_BYTES, configured + 64_000))
        headers = {bytes(name).lower(): bytes(value) for name, value in scope.get("headers") or []}
        try:
            content_length = int(headers.get(b"content-length", b"0") or b"0")
        except ValueError:
            content_length = 0
        if content_length > limit:
            await self._reject(send)
            return
        received = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    raise DiagnosticsRequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except DiagnosticsRequestBodyTooLarge:
            await self._reject(send)

    @staticmethod
    async def _reject(send) -> None:
        body = json.dumps(
            {"detail": "Playback diagnostics request body is too large"},
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def classify_browser_playback_route(path: str) -> tuple[str, str | None, str | None, int | None]:
    route, _ = normalized_route_identity(path)
    session_id: str | None = None
    epoch_id: str | None = None
    segment_index: int | None = None
    session_match = SESSION_PATH.match(path)
    epoch_match = EPOCH_PATH.match(path)
    if session_match:
        session_id = session_match.group(1)
    elif epoch_match:
        epoch_id = epoch_match.group(1)
    segment_match = re.search(r"/segments/(\d+)\.(?:m4s|mp4)$", path)
    if segment_match:
        segment_index = int(segment_match.group(1))
    if path.endswith("/index.m3u8"):
        route = "/api/browser-playback/:scope/index.m3u8"
    elif path.endswith("/init.mp4"):
        route = "/api/browser-playback/:scope/init.mp4"
    elif segment_index is not None:
        route = "/api/browser-playback/:scope/segments/:segment"
    elif session_id:
        suffix = path.split(session_id, 1)[1]
        route = f"/api/browser-playback/sessions/:session{suffix}"
    return route, session_id, epoch_id, segment_index


class PlaybackDiagnosticsHttpMiddleware:
    """Observer-only ASGI timing around Browser Playback distribution routes."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "")
        if not path.startswith("/api/browser-playback/"):
            await self.app(scope, receive, send)
            return

        app = scope.get("app")
        diagnostics = getattr(getattr(app, "state", None), "playback_diagnostics_service", None)
        if not bool(getattr(diagnostics, "enabled", False)):
            await self.app(scope, receive, send)
            return

        route, session_id, epoch_id, segment_index = classify_browser_playback_route(path)
        if session_id is None and epoch_id and app is not None:
            try:
                manager = app.state.mobile_playback_manager
                session_id = manager.resolve_diagnostic_session_for_epoch(epoch_id)
            except Exception:  # noqa: BLE001
                record_runtime_health("http_observer", "epoch_resolution_failed")
                session_id = None
        accepted_wall_ns = time.time_ns()
        accepted_monotonic_ns = time.monotonic_ns()
        trace_id = next_diagnostic_correlation_id("trace")
        status_code: int | None = None
        headers_ready_ns: int | None = None
        first_body_ns: int | None = None
        last_body_ns: int | None = None
        bytes_scheduled = 0
        disconnected = False
        exception_class: str | None = None
        cache_control_class = "unknown"

        observe_runtime_event(
            "http_request_accepted",
            playback_session_id=session_id,
            event_source="server",
            trace_id=trace_id,
            epoch_id=epoch_id,
            payload={
                "route_template": route,
                "segment_index": segment_index,
                "request_start_ms": accepted_wall_ns / 1_000_000,
            },
        )

        async def observed_receive() -> dict[str, Any]:
            nonlocal disconnected
            message = await receive()
            if message.get("type") == "http.disconnect":
                disconnected = True
            return message

        async def observed_send(message: dict[str, Any]) -> None:
            nonlocal status_code, headers_ready_ns, first_body_ns, last_body_ns
            nonlocal bytes_scheduled, cache_control_class
            message_type = message.get("type")
            if message_type == "http.response.start":
                headers_ready_ns = time.monotonic_ns()
                status_code = int(message.get("status") or 0)
                for name, value in message.get("headers") or []:
                    if bytes(name).lower() == b"cache-control":
                        decoded = bytes(value).decode("latin-1", errors="replace").lower()
                        if "no-store" in decoded:
                            cache_control_class = "no_store"
                        elif "immutable" in decoded:
                            cache_control_class = "private_immutable"
                        elif "private" in decoded:
                            cache_control_class = "private"
                        else:
                            cache_control_class = "other"
            elif message_type == "http.response.body":
                now = time.monotonic_ns()
                if first_body_ns is None:
                    first_body_ns = now
                body = message.get("body") or b""
                bytes_scheduled += len(body)
                if not message.get("more_body", False):
                    last_body_ns = now
            await send(message)

        try:
            await self.app(scope, observed_receive, observed_send)
        except Exception as exc:
            exception_class = exc.__class__.__name__
            raise
        finally:
            finished_ns = time.monotonic_ns()
            observe_runtime_event(
                "http_response_completed",
                playback_session_id=session_id,
                event_source="server",
                trace_id=trace_id,
                epoch_id=epoch_id,
                severity="error" if exception_class else "info",
                payload={
                    "route_template": route,
                    "segment_index": segment_index,
                    "http_status": status_code,
                    "response_headers_ready_ms": (
                        (headers_ready_ns - accepted_monotonic_ns) / 1_000_000
                        if headers_ready_ns is not None
                        else None
                    ),
                    "first_byte_ms": (
                        (first_body_ns - accepted_monotonic_ns) / 1_000_000
                        if first_body_ns is not None
                        else None
                    ),
                    "last_byte_ms": (
                        (last_body_ns - accepted_monotonic_ns) / 1_000_000
                        if last_body_ns is not None
                        else None
                    ),
                    "bytes": bytes_scheduled,
                    "request_duration_ms": (finished_ns - accepted_monotonic_ns) / 1_000_000,
                    "cancelled": disconnected,
                    "exception_class": exception_class,
                    "cache_control_class": cache_control_class,
                },
            )
