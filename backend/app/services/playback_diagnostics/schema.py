from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .constants import EVENT_SOURCES, OBSERVATION_KINDS, SCHEMA_VERSION


DecimalTimestamp = str
EventPriority = Literal["low", "normal", "high", "critical"]
EventSeverity = Literal["debug", "info", "warning", "error", "critical"]


class DiagnosticsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlaybackDiagnosticEvent(DiagnosticsModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    event_id: str = Field(min_length=8, max_length=128)
    event_name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_.:-]+$")
    event_source: str = Field(min_length=1, max_length=32)
    severity: EventSeverity = "info"
    priority: EventPriority = "normal"

    playback_session_id: str = Field(min_length=8, max_length=128)
    playback_attempt_id: str | None = Field(default=None, max_length=128)
    attachment_id: str | None = Field(default=None, max_length=128)
    epoch_id: str | None = Field(default=None, max_length=128)
    worker_id: str | None = Field(default=None, max_length=128)
    incident_id: str | None = Field(default=None, max_length=128)
    decision_id: str | None = Field(default=None, max_length=128)

    trace_id: str | None = Field(default=None, max_length=128)
    span_id: str | None = Field(default=None, max_length=128)
    parent_span_id: str | None = Field(default=None, max_length=128)

    event_sequence: int = Field(ge=1)
    source_sequence: int = Field(ge=1)

    client_wall_time_ms: float | None = None
    client_monotonic_time_us: float | None = None
    client_time_origin_ms: float | None = None
    client_timer_resolution_us: float | None = None

    server_wall_time_ns: DecimalTimestamp | None = None
    server_monotonic_time_ns: DecimalTimestamp | None = None
    server_received_wall_time_ns: DecimalTimestamp | None = None
    server_received_monotonic_time_ns: DecimalTimestamp | None = None
    aligned_wall_time_ns: DecimalTimestamp | None = None
    clock_offset_ns: DecimalTimestamp | None = None
    clock_uncertainty_ns: DecimalTimestamp | None = None
    network_rtt_ns: DecimalTimestamp | None = None

    playhead_ms: float | None = Field(default=None, ge=0)
    media_element_time_ms: float | None = Field(default=None, ge=0)
    duration_ms: float | None = Field(default=None, ge=0)

    platform: str | None = Field(default=None, max_length=64)
    device_class: str | None = Field(default=None, max_length=32)
    browser_family: str | None = Field(default=None, max_length=64)
    browser_version: str | None = Field(default=None, max_length=64)
    os_family: str | None = Field(default=None, max_length=64)
    os_version: str | None = Field(default=None, max_length=64)
    hls_engine: str | None = Field(default=None, max_length=64)
    playback_mode: str | None = Field(default=None, max_length=32)
    stream_mode: str | None = Field(default=None, max_length=32)
    source_kind: str | None = Field(default=None, max_length=32)

    observation_kind: str
    measurement_method: str | None = Field(default=None, max_length=128)
    measurement_resolution: str | None = Field(default=None, max_length=128)
    measurement_uncertainty: str | None = Field(default=None, max_length=128)
    sample_window_ms: float | None = Field(default=None, ge=0)
    capability_available: bool | None = None
    unavailable_reason: str | None = Field(default=None, max_length=256)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_source")
    @classmethod
    def validate_event_source(cls, value: str) -> str:
        if value not in EVENT_SOURCES:
            raise ValueError("Unsupported diagnostics event source")
        return value

    @field_validator("observation_kind")
    @classmethod
    def validate_observation_kind(cls, value: str) -> str:
        if value not in OBSERVATION_KINDS:
            raise ValueError("Unsupported diagnostics observation kind")
        return value

    @field_validator(
        "server_wall_time_ns",
        "server_monotonic_time_ns",
        "server_received_wall_time_ns",
        "server_received_monotonic_time_ns",
        "aligned_wall_time_ns",
        "clock_offset_ns",
        "clock_uncertainty_ns",
        "network_rtt_ns",
    )
    @classmethod
    def validate_decimal_timestamp(cls, value: str | None) -> str | None:
        if value is not None and not value.lstrip("-").isdigit():
            raise ValueError("Nanosecond timestamps must be decimal strings")
        return value


class PlaybackDiagnosticsBootstrapRequest(DiagnosticsModel):
    playback_session_id: str = Field(min_length=8, max_length=128)
    client_instance_id: str = Field(min_length=8, max_length=128)
    platform: str = Field(default="unknown", max_length=64)
    device_class: str = Field(default="unknown", max_length=32)
    browser_family: str = Field(default="unknown", max_length=64)
    browser_version: str = Field(default="", max_length=64)
    os_family: str = Field(default="unknown", max_length=64)
    os_version: str = Field(default="", max_length=64)
    hls_engine: str = Field(default="unknown", max_length=64)
    capabilities: dict[str, bool | str | int | float | None] = Field(default_factory=dict)


class PlaybackDiagnosticsBootstrapResponse(DiagnosticsModel):
    enabled: bool
    diagnostics_session_id: str
    source_id: str
    schema_version: str
    client_spool_max_bytes: int
    batch_max_events: int
    batch_max_bytes: int
    clock_algorithm: str
    server_wall_time_ns: DecimalTimestamp
    server_monotonic_time_ns: DecimalTimestamp
    ack_watermark: int = Field(ge=0)


class PlaybackDiagnosticsBatchRequest(DiagnosticsModel):
    diagnostics_session_id: str = Field(min_length=8, max_length=128)
    source_id: str = Field(min_length=8, max_length=128)
    events: list[PlaybackDiagnosticEvent]


class PlaybackDiagnosticsBatchResponse(DiagnosticsModel):
    accepted: int
    duplicate: int
    rejected: int
    out_of_order: int
    ack_watermark: int
    capacity_state: str


class PlaybackDiagnosticsClockRequest(DiagnosticsModel):
    diagnostics_session_id: str = Field(min_length=8, max_length=128)
    source_id: str = Field(min_length=8, max_length=128)
    sample_id: str = Field(min_length=4, max_length=128)
    client_send_wall_time_ms: float
    client_send_monotonic_time_us: float


class PlaybackDiagnosticsClockResponse(DiagnosticsModel):
    sample_id: str
    client_send_wall_time_ms: float
    client_send_monotonic_time_us: float
    server_receive_wall_time_ns: DecimalTimestamp
    server_receive_monotonic_time_ns: DecimalTimestamp
    server_send_wall_time_ns: DecimalTimestamp
    server_send_monotonic_time_ns: DecimalTimestamp
    monotonic_raw_time_ns: DecimalTimestamp | None


class PlaybackDiagnosticsCloseRequest(DiagnosticsModel):
    diagnostics_session_id: str = Field(min_length=8, max_length=128)
    source_id: str = Field(min_length=8, max_length=128)
    reason: str = Field(default="client_closed", max_length=128)
    final_source_sequence: int | None = Field(default=None, ge=0)


class PlaybackDiagnosticsCloseResponse(DiagnosticsModel):
    accepted: bool
    ack_watermark: int
    finalized: bool


def build_server_event(
    *,
    event_name: str,
    playback_session_id: str,
    source_sequence: int,
    event_source: str = "server",
    observation_kind: str = "measured_server",
    priority: EventPriority = "normal",
    severity: EventSeverity = "info",
    payload: dict[str, Any] | None = None,
    **identities: Any,
) -> PlaybackDiagnosticEvent:
    wall_ns = time.time_ns()
    monotonic_ns = time.monotonic_ns()
    allowed_identities = {
        key: value
        for key, value in identities.items()
        if key
        in {
            "playback_attempt_id",
            "attachment_id",
            "epoch_id",
            "worker_id",
            "incident_id",
            "decision_id",
            "trace_id",
            "span_id",
            "parent_span_id",
            "playhead_ms",
            "media_element_time_ms",
            "duration_ms",
            "platform",
            "device_class",
            "browser_family",
            "browser_version",
            "os_family",
            "os_version",
            "hls_engine",
            "playback_mode",
            "stream_mode",
            "source_kind",
            "measurement_method",
            "measurement_resolution",
            "measurement_uncertainty",
            "sample_window_ms",
            "capability_available",
            "unavailable_reason",
        }
    }
    return PlaybackDiagnosticEvent(
        event_id=uuid.uuid4().hex,
        event_name=event_name,
        event_source=event_source,
        severity=severity,
        priority=priority,
        playback_session_id=playback_session_id,
        event_sequence=source_sequence,
        source_sequence=source_sequence,
        server_wall_time_ns=str(wall_ns),
        server_monotonic_time_ns=str(monotonic_ns),
        aligned_wall_time_ns=str(wall_ns),
        observation_kind=observation_kind,
        payload=payload or {},
        **allowed_identities,
    )
