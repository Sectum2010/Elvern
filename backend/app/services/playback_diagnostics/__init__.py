"""Local, fail-closed playback diagnostics research plane."""

from .runtime import observe_runtime_event, set_active_diagnostics_service
from .service import PlaybackDiagnosticsService

__all__ = (
    "PlaybackDiagnosticsService",
    "observe_runtime_event",
    "set_active_diagnostics_service",
)
