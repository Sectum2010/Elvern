# Playback Diagnostics Schema

## Versioned envelope

The current event schema is `playback-diagnostics-event-v1`. Every raw event is
validated against the following closed envelope; extra top-level fields are not
persisted.

| Group | Fields |
| --- | --- |
| Event | `schema_version`, `event_id`, `event_name`, `event_source`, `severity`, `priority` |
| Correlation | `playback_session_id`, `playback_attempt_id`, `attachment_id`, `epoch_id`, `worker_id`, `incident_id`, `decision_id` |
| Trace | `trace_id`, `span_id`, `parent_span_id` |
| Ordering | `event_sequence`, `source_sequence` |
| Client clock | `client_wall_time_ms`, `client_monotonic_time_us`, `client_time_origin_ms`, `client_timer_resolution_us` |
| Server clock | `server_wall_time_ns`, `server_monotonic_time_ns`, `server_received_wall_time_ns`, `server_received_monotonic_time_ns` |
| Alignment | `aligned_wall_time_ns`, `clock_offset_ns`, `clock_uncertainty_ns`, `network_rtt_ns` |
| Media | `playhead_ms`, `media_element_time_ms`, `duration_ms` |
| Platform | `platform`, `device_class`, `browser_family`, `browser_version`, `os_family`, `os_version`, `hls_engine` |
| Playback | `playback_mode`, `stream_mode`, `source_kind` |
| Evidence | `observation_kind`, `measurement_method`, `measurement_resolution`, `measurement_uncertainty`, `sample_window_ms`, `capability_available`, `unavailable_reason` |
| Data | `payload` |

Nanosecond values are decimal strings because JavaScript `Number` cannot safely
represent nanosecond integers. A missing field is `null`/absent, not zero.

`event_sequence` is monotonic within the client recorder. `source_sequence` is
monotonic within one registered source and drives idempotency and continuous ACK
watermarks. `event_id` and `(source_id, source_sequence)` are both unique catalog
keys. Duplicate and out-of-order events are counted.

## Sources and certainty

Allowed `event_source` values:

- `client`
- `server`
- `host`
- `provider`
- `ffmpeg`
- `atc`
- `recorder`

Allowed `observation_kind` values:

- `measured_client`
- `measured_server`
- `measured_kernel`
- `measured_provider`
- `derived`
- `inferred`
- `unsupported`

Priorities are `low`, `normal`, `high`, and `critical`. Critical events include
capacity/filesystem state, telemetry gaps, recorder failure, and session close
or finalization. The server reserves capacity for critical closure evidence.

## Event families

The schema is extensible through bounded `event_name` values and an allowlisted
payload. Current families include:

### Session and recorder

`session_created`, `client_recorder_started`,
`client_recorder_bootstrapped`, `attachment_changed`, `recorder_aggregate`,
`recorder_batch_acked`, `recorder_upload_retry`, `telemetry_gap`,
`session_close`, and `session_finalized`.

### User/controller and Media Element

`play_intent`, `play_requested`, `play_started`, `pause_intent`,
`pause_started`, `resume_intent`, `resume_started`, `seek_intent`,
`seek_started`, `seek_completed`, `stop_intent`, `quit`, `completed`,
`playback_failed`, `volume_changed`, `muted`, `unmuted`,
`playback_rate_changed`, fullscreen and picture-in-picture transitions, plus
raw Media Element lifecycle names and one-second `media_aggregate` snapshots.

Actions carry `action_origin` from `user`, `elvern_controller`, `browser`,
`operating_system`, `recovery`, `inferred_user`, or `unknown`. The recorder does
not monkeypatch `HTMLMediaElement`.

### Page lifecycle and performance

Visibility/focus, `pagehide`, `pageshow`, online/offline, freeze/resume,
orientation, `document.wasDiscarded`, inferred suspension bounds, Long Tasks,
Long Animation Frames, event-loop aggregates, resource timing, storage/memory
capabilities, and client resource aggregates.

### Frames and HLS

One-second frame aggregates and incident frame chunks contain frame cadence,
presented/dropped/corrupted frame evidence, callback lateness, and processing
duration. hls.js event names are prefixed `hls_` and include manifest, level,
fragment, buffer, audio/subtitle switch, FPS-drop, emergency-abort, and error
events. URLs become a normalized route and one-way hash.

Native HLS never emits fabricated hls.js loader events. Its capability record
states no client fragment detail or internal cache, while server segment trace
remains available.

### HTTP/provider/FFmpeg/host

Server distribution events include request acceptance, response timing, status,
bytes scheduled, cancellation, route template, cache class, epoch, and segment.
They do not assert client receipt or visible frame.

Provider events describe only existing Range requests: start/end, headers,
first/last byte, expected/actual bytes, status, retry/cancel/EOF, throughput, and
sanitized error class. No diagnostic provider request is made.

FFmpeg/Route 2 events copy existing worker lifecycle, progress, segment staging,
generation/publication, frontier, and controller data. Command identity is a
sanitized fingerprint, not an argv dump. Host aggregates use unprivileged
`/proc`, PSI, cgroup, filesystem, process, optional `nvidia-smi`, and optional
local Tailscale CLI evidence.

### Incident and ETA

Incident events include candidate/confirmed stall, recovery start, resumed
playhead, stall end, pre/post client and host windows, and post-recovery
observation. Raw evidence remains primary; offline classification uses the
versioned `playback-incident-classifier-v1` model and must retain uncertainty,
supporting/contradicting evidence, alternatives, and missing evidence when
available.

ETA observation retains prediction identity/kind, predicted duration or ready
time, model/input/reason/confidence when exposed, and later actual duration,
absolute/relative error, and signed bias. A newer prediction does not overwrite
an older one.

## Payload rules

Payload keys come from a centralized allowlist. Strings are bounded, numeric
values must be finite, lists are capped, and nesting is bounded. Each encoded
event is limited to 64,000 bytes. Exact movie basename is the only path-like
sensitive value allowed. Normalized routes have their own strict
`/api/browser-playback/` validator. Full URL, absolute path, token, cookie,
Authorization, arbitrary header/body, media bytes, subtitle content, full user
agent, and full FFmpeg command values are prohibited.

## Clock fields and uncertainty

Client/server clock exchange uses five samples at session bootstrap. The
versioned estimator records offset, minimum selected RTT, uncertainty, and
sample count. Event durations use monotonic clocks. `aligned_wall_time_ns` is a
correlation estimate, not a claim of nanosecond cross-device accuracy.

## Direct-open derived schema

Each finalized/interrupted session produces:

- `session.json`: pseudonymous identity, exact basename, media/build/platform
  metadata and capabilities.
- `summary.json`: identity, movie complexity, QoE, client/server distributions,
  and diagnostics quality.
- `summary.md`: human-readable local summary.
- `timeline.csv`: aligned semantic/aggregate timeline.
- `completeness.json`: source, sequence, drop, clock, capability, and writer
  quality evidence.
- `manifest.json`: SHA-256 and size of direct-open files plus journal
  verification reports.

The derived files never replace raw evidence. A missing field or unsupported
capability is listed and must not be interpreted as proof that no issue occurred.

## Current unsupported or platform-limited fields

Ordinary Web APIs cannot reliably provide Safari process RSS, iPhone free RAM,
browser media-process RAM, native HLS cache bytes, total browser HTTP cache,
exact browser CPU, numeric iOS memory pressure, other-app memory, or the OS audio
clock. Availability of frame callbacks, playback quality, memory APIs, Compute
Pressure, Long Animation Frames, storage estimates, and network information is
runtime-detected.

Host GPU is limited to the fields exposed by a successful local `nvidia-smi`
query. Tailscale capture is limited to local backend state, bounded health count,
and coarse active path class. DCGM-only data, detailed peer counters, privileged
packet evidence, and real native-app process evidence are not fabricated.

