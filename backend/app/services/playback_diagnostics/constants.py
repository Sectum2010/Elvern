from __future__ import annotations

from pathlib import Path


SCHEMA_VERSION = "playback-diagnostics-event-v2"
LEGACY_EVENT_SCHEMA_VERSIONS = frozenset({"playback-diagnostics-event-v1"})
SUPPORTED_EVENT_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION, *LEGACY_EVENT_SCHEMA_VERSIONS})
JOURNAL_SCHEMA_VERSION = "elvern-diagnostics-journal-v2"
LEGACY_JOURNAL_SCHEMA_VERSION = "elvern-diagnostics-journal-v1"
CATALOG_SCHEMA_VERSION = "playback-diagnostics-catalog-v3"
CLOCK_ALGORITHM_VERSION = "min-rtt-median-offset-v1"
CLASSIFIER_VERSION = "playback-incident-classifier-v1"

DIAGNOSTICS_HARD_CAP_BYTES = 80_000_000_000
DIAGNOSTICS_EMERGENCY_RESERVE_BYTES = 500_000_000
DIAGNOSTICS_NORMAL_BUDGET_BYTES = (
    DIAGNOSTICS_HARD_CAP_BYTES - DIAGNOSTICS_EMERGENCY_RESERVE_BYTES
)
DIAGNOSTICS_CATALOG_MUTATION_RESERVATION_BYTES = 8 * 1024 * 1024

CLIENT_RING_SAMPLE_INTERVAL_MS = 250
CLIENT_AGGREGATE_INTERVAL_MS = 1_000
HOST_RING_SAMPLE_INTERVAL_MS = 500
HOST_AGGREGATE_INTERVAL_MS = 1_000
GPU_SAMPLE_INTERVAL_MS = 5_000
PSS_SAMPLE_INTERVAL_MS = 10_000
TAILSCALE_SAMPLE_INTERVAL_MS = 30_000
CLOCK_RECALIBRATION_INTERVAL_MS = 60_000
INCIDENT_PRE_WINDOW_SECONDS = 60
INCIDENT_POST_WINDOW_SECONDS = 120

JOURNAL_MAGIC = b"ELVD2\n"
LEGACY_JOURNAL_MAGIC = b"ELVD1\n"
JOURNAL_LENGTH_BYTES = 8
MAX_JOURNAL_CHUNK_BYTES = 4_000_000
MAX_EVENT_PAYLOAD_BYTES = 64_000

CRITICAL_EVENT_NAMES = frozenset(
    {
        "capacity_reached",
        "capacity_exhausted",
        "filesystem_low_space",
        "telemetry_gap",
        "session_close",
        "session_finalized",
        "recorder_failure",
    }
)

SESSION_VISIBLE_FILES = (
    "session.json",
    "seal.json",
    "summary.md",
    "summary.json",
    "timeline.csv",
    "completeness.json",
    "manifest.json",
)

ROOT_DIRECTORIES = (
    Path("keys"),
    Path("identities"),
    Path("sessions"),
    Path("derived"),
    Path("exports"),
    Path("quarantine"),
)

OBSERVATION_KINDS = frozenset(
    {
        "measured_client",
        "measured_server",
        "measured_kernel",
        "measured_provider",
        "derived",
        "inferred",
        "unsupported",
    }
)

EVENT_SOURCES = frozenset(
    {
        "client",
        "server",
        "host",
        "provider",
        "ffmpeg",
        "atc",
        "recorder",
    }
)

PLAYBACK_DIAGNOSTICS_BACKUP_EXCLUSION = Path("backend/data/playback_diagnostics")

SESSION_STATES = frozenset(
    {
        "provisional",
        "registering",
        "active",
        "interrupted_recoverable",
        "closing",
        "sealed",
        "corrupt",
    }
)
