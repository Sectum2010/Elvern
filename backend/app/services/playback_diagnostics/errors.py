from __future__ import annotations

from typing import Any


class PlaybackDiagnosticsError(RuntimeError):
    """Stable diagnostics-only failure exposed through the HTTP adapter."""

    status_code = 503
    code = "diagnostics_unavailable"
    public_message = "Playback diagnostics are unavailable."
    retryable = True

    def __init__(
        self,
        _internal_message: str | None = None,
        *,
        code: str | None = None,
        public_message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(_internal_message or code or self.code)
        self.error_code = code or self.code
        self.error_message = public_message or self.public_message
        self.error_details = dict(details or {})

    def response_detail(self) -> dict[str, Any]:
        return {
            "code": self.error_code,
            "message": self.error_message,
            "retryable": self.retryable,
            **self.error_details,
        }


class DiagnosticsNotFoundError(PlaybackDiagnosticsError):
    status_code = 404
    code = "diagnostics_not_found"
    public_message = "Playback diagnostics session or source was not found."
    retryable = False


class DiagnosticsConflictError(PlaybackDiagnosticsError):
    status_code = 409
    code = "diagnostics_conflict"
    public_message = "Playback diagnostics state conflicts with this request."
    retryable = False


class DiagnosticsCorruptError(DiagnosticsConflictError):
    code = "diagnostics_corrupt"
    public_message = "Playback diagnostics evidence is corrupt."


class DiagnosticsClosingError(DiagnosticsConflictError):
    code = "diagnostics_closing"
    public_message = "Playback diagnostics are closing."
    retryable = True


class DiagnosticsSealedError(PlaybackDiagnosticsError):
    status_code = 410
    code = "diagnostics_sealed"
    public_message = "Playback diagnostics are sealed."
    retryable = False


class DiagnosticsRequestTooLargeError(PlaybackDiagnosticsError):
    status_code = 413
    code = "diagnostics_request_too_large"
    public_message = "Playback diagnostics request is too large."
    retryable = False

    def __init__(self, _internal_message: str | None = None, *, code: str | None = None) -> None:
        super().__init__(
            _internal_message,
            code=code,
            details={
                "event_index": None,
                "event_id": None,
                "source_sequence": None,
                "reason": code or self.code,
                "permanent": False,
                "batch_split_allowed": True,
            },
        )


class DiagnosticsInvalidEventError(PlaybackDiagnosticsError):
    status_code = 422
    code = "diagnostics_invalid_event"
    public_message = "A playback diagnostics event is invalid."
    retryable = False

    def __init__(
        self,
        *,
        event_index: int,
        event_id: str | None,
        source_sequence: int | None,
        reason: str,
    ) -> None:
        super().__init__(
            details={
                "event_index": event_index,
                "event_id": event_id,
                "source_sequence": source_sequence,
                "reason": reason,
                "permanent": True,
                "batch_split_allowed": True,
            }
        )


class DiagnosticsRateLimitError(PlaybackDiagnosticsError):
    status_code = 429
    code = "diagnostics_budget_exceeded"
    public_message = "Playback diagnostics budget was exceeded."
    retryable = True


class DiagnosticsWorkerUnavailableError(PlaybackDiagnosticsError):
    status_code = 503
    code = "diagnostics_worker_unavailable"
    public_message = "Playback diagnostics worker is temporarily unavailable."
    retryable = True


class PlaybackDiagnosticsUnavailableError(DiagnosticsWorkerUnavailableError):
    code = "diagnostics_unavailable"


class PlaybackDiagnosticsOwnershipError(DiagnosticsNotFoundError, PermissionError):
    code = "diagnostics_not_found"


class DiagnosticsCapacityHttpError(PlaybackDiagnosticsError):
    status_code = 507
    code = "diagnostics_capacity_reached"
    public_message = "Playback diagnostics storage capacity was reached."
    retryable = False
