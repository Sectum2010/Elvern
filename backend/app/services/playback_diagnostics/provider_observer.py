from __future__ import annotations

import re
import secrets
import time
from typing import Any

from .runtime import observe_runtime_event


_BYTE_RANGE = re.compile(r"^bytes=(\d+)-(\d*)$")


def parse_byte_range(value: object) -> tuple[int | None, int | None]:
    match = _BYTE_RANGE.fullmatch(str(value or "").strip())
    if match is None:
        return None, None
    return int(match.group(1)), int(match.group(2)) if match.group(2) else None


class ProviderRequestObserver:
    """Best-effort measurements around one existing provider request."""

    def __init__(
        self,
        *,
        playback_session_id: str | None,
        range_header: str | None,
        retry_count: int = 0,
    ) -> None:
        self.playback_session_id = str(playback_session_id or "") or None
        self.provider_request_id = f"provider_{secrets.token_urlsafe(18)}"
        self.range_start, self.range_end = parse_byte_range(range_header)
        self.retry_count = max(0, int(retry_count))
        self.started_ns = time.monotonic_ns()
        self.first_byte_ns: int | None = None
        self.actual_bytes = 0
        self.expected_bytes: int | None = None
        self.status: int | None = None
        self.content_range_start: int | None = None
        self.content_range_end: int | None = None
        self.finished = False
        self._emit(
            "provider_request_started",
            {
                "provider_request_id": self.provider_request_id,
                "range_start": self.range_start,
                "range_end": self.range_end,
                "retries": self.retry_count,
            },
        )

    def headers_received(self, upstream: Any, content_range: tuple[int, int, int] | None) -> None:
        try:
            self.status = int(getattr(upstream, "status", 0) or 0)
            headers = getattr(upstream, "headers", {}) or {}
            raw_length = headers.get("Content-Length")
            self.expected_bytes = int(raw_length) if raw_length not in {None, ""} else None
            if content_range is not None:
                self.content_range_start, self.content_range_end, _total = content_range
                self.expected_bytes = max(0, self.content_range_end - self.content_range_start + 1)
            self._emit(
                "provider_headers_received",
                {
                    "provider_request_id": self.provider_request_id,
                    "http_status": self.status,
                    "expected_bytes": self.expected_bytes,
                    "content_range_start": self.content_range_start,
                    "content_range_end": self.content_range_end,
                    "response_headers_ready_ms": self._elapsed_ms(),
                },
            )
        except Exception:  # noqa: BLE001 - observer cannot alter provider behavior.
            return

    def chunk(self, size: int) -> None:
        try:
            now = time.monotonic_ns()
            if self.first_byte_ns is None:
                self.first_byte_ns = now
                self._emit(
                    "provider_first_byte",
                    {
                        "provider_request_id": self.provider_request_id,
                        "time_to_first_byte_ms": self._elapsed_ms(now),
                    },
                )
            self.actual_bytes += max(0, int(size))
        except Exception:  # noqa: BLE001
            return

    def finish(self, *, eof: bool, cancelled: bool = False) -> None:
        if self.finished:
            return
        self.finished = True
        elapsed_ms = self._elapsed_ms()
        throughput = (
            (self.actual_bytes * 8 * 1_000) / elapsed_ms
            if elapsed_ms > 0 and self.actual_bytes > 0
            else None
        )
        self._emit(
            "provider_request_completed",
            {
                "provider_request_id": self.provider_request_id,
                "http_status": self.status,
                "expected_bytes": self.expected_bytes,
                "actual_bytes": self.actual_bytes,
                "provider_throughput_bps": throughput,
                "request_duration_ms": elapsed_ms,
                "cancelled": bool(cancelled),
                "complete": bool(eof),
                "retries": self.retry_count,
            },
            priority="high" if cancelled else "normal",
        )

    def error(self, exc: BaseException, *, status_code: int | None = None) -> None:
        if self.finished:
            return
        self.finished = True
        error_class = exc.__class__.__name__
        self._emit(
            "provider_request_failed",
            {
                "provider_request_id": self.provider_request_id,
                "http_status": status_code,
                "actual_bytes": self.actual_bytes,
                "request_duration_ms": self._elapsed_ms(),
                "error_class": error_class,
                "retries": self.retry_count,
            },
            priority="high",
            severity="error",
        )

    def _elapsed_ms(self, now_ns: int | None = None) -> float:
        return max(0.0, ((now_ns or time.monotonic_ns()) - self.started_ns) / 1_000_000)

    def _emit(
        self,
        event_name: str,
        payload: dict[str, Any],
        *,
        priority: str = "normal",
        severity: str = "info",
    ) -> None:
        observe_runtime_event(
            event_name,
            playback_session_id=self.playback_session_id,
            event_source="provider",
            observation_kind="measured_provider",
            priority=priority,
            severity=severity,
            payload=payload,
        )
