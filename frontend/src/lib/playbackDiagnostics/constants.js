export const PLAYBACK_DIAGNOSTICS_SCHEMA_VERSION = "playback-diagnostics-event-v1";
export const PLAYBACK_DIAGNOSTICS_DB_NAME = "elvern-playback-diagnostics-v1";
export const PLAYBACK_DIAGNOSTICS_DB_VERSION = 1;
export const PLAYBACK_DIAGNOSTICS_DEFAULT_SPOOL_MAX_BYTES = 64_000_000;
export const PLAYBACK_DIAGNOSTICS_CRITICAL_RESERVE_RATIO = 0.1;
export const PLAYBACK_DIAGNOSTICS_DEFAULT_BATCH_MAX_EVENTS = 256;
export const PLAYBACK_DIAGNOSTICS_DEFAULT_BATCH_MAX_BYTES = 524_288;
export const PLAYBACK_DIAGNOSTICS_CLIENT_SAMPLE_MS = 250;
export const PLAYBACK_DIAGNOSTICS_AGGREGATE_MS = 1_000;
export const PLAYBACK_DIAGNOSTICS_FLUSH_MS = 5_000;
export const PLAYBACK_DIAGNOSTICS_FLUSH_SOON_MS = 1_000;
export const PLAYBACK_DIAGNOSTICS_CLOCK_RECALIBRATION_MS = 60_000;
export const PLAYBACK_DIAGNOSTICS_CLOCK_EXCHANGE_SAMPLES = 5;
export const PLAYBACK_DIAGNOSTICS_INCIDENT_PRE_SECONDS = 60;
export const PLAYBACK_DIAGNOSTICS_INCIDENT_POST_SECONDS = 120;
export const PLAYBACK_DIAGNOSTICS_INCIDENT_SAMPLE_CHUNK = 64;
export const PLAYBACK_DIAGNOSTICS_INCIDENT_FRAME_CHUNK = 128;
export const PLAYBACK_DIAGNOSTICS_INCIDENT_CHUNK_TARGET_BYTES = 48_000;
export const PLAYBACK_DIAGNOSTICS_INCIDENT_MAX_RANGES = 64;

export const PLAYBACK_DIAGNOSTICS_CRITICAL_EVENTS = new Set([
  "telemetry_gap",
  "session_close",
  "capacity_reached",
  "capacity_exhausted",
  "recorder_failure",
]);

export const PLAYBACK_DIAGNOSTICS_MEDIA_EVENTS = Object.freeze([
  "loadstart",
  "durationchange",
  "loadedmetadata",
  "loadeddata",
  "progress",
  "canplay",
  "canplaythrough",
  "play",
  "playing",
  "pause",
  "waiting",
  "stalled",
  "suspend",
  "abort",
  "emptied",
  "seeking",
  "seeked",
  "ratechange",
  "volumechange",
  "resize",
  "ended",
  "error",
]);
