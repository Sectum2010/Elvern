# Playback Diagnostics Schema

## Versioned envelope

The current event schema is `playback-diagnostics-event-v2`. Every raw event is
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

`event_sequence` is monotonic within the client recorder. For browser sources,
`source_sequence` is allocated atomically with the IndexedDB insert. For
server, provider, FFmpeg, ATC, host, and recorder-internal sources it is
allocated only by the diagnostics writer under the serialized catalog mutation
boundary. A capture rejected before allocation consumes no sequence. Once a
sequence is allocated, it is either durably persisted or covered by an explicit
durable gap declaration.

The server-created `source_id` binds a journal to one playback session, source
type, and (for browser sources) stable `client_instance_id`. `event_id` and
`(source_id, source_sequence)` are both unique catalog keys. Duplicate and
out-of-order events are counted, and a contiguous durable ACK advances only
after journal fsync, catalog event commit, and durable gap accounting. A batch
is never silently partially filtered: an invalid client event is returned with
its exact index, ID, sequence, and stable reason so that the client can isolate
that one event and declare the corresponding gap.

## Version registry

| Object | Current schema | Compatibility behavior |
| --- | --- | --- |
| Event | `playback-diagnostics-event-v2` | v1 is accepted only as a declared legacy event schema |
| Journal | `elvern-diagnostics-journal-v2` | v2 binds session/source/type in authenticated chunk headers; v1 is read as legacy |
| Catalog | `playback-diagnostics-catalog-v2` | records source ACK/final watermarks and lifecycle state |
| Session metadata | `playback-diagnostics-session-v2` | validated before catalog registration or derived output |
| Summary | `playback-diagnostics-summary-v2` | derived from sealed raw evidence |
| Completeness | `playback-diagnostics-completeness-v2` | reports lifecycle/source/drop/capability evidence separately |
| Seal capsule | `playback-diagnostics-seal-v1` | immutable critical evidence and derived-artifact status |
| Manifest | `playback-diagnostics-session-manifest-v2` | generated last from visible files and journal verification reports |
| Capacity ledger | `playback-diagnostics-capacity-ledger-v1` | trusted only after a verified clean shutdown |

New records are never silently written under a legacy version.

## Session lifecycle

Durable states are `provisional`, `registering`, `active`,
`interrupted_recoverable`, `closing`, `sealed`, and `corrupt`.

- `provisional`/`registering`: metadata creation is in progress; a bounded set of
  early backend observations may wait for registration.
- `active`: registered sources may append.
- `interrupted_recoverable`: a prior writer stopped before close; browser
  IndexedDB replay may resume the same client source.
- `closing`: a final source sequence is declared, but final source barriers or
  backend observation/writer drains may still be pending.
- `sealed`: all accepted source barriers are durable, derived files and the
  critical seal/manifest are durable, and all later appends are rejected.
  Optional derived files may be complete, deferred for capacity, or failed as
  recorded by the immutable seal.
- `corrupt`: strict recovery found a defect that must not be truncated or
  reinterpreted automatically.

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

## Typed diagnostics HTTP errors

Diagnostics errors use a closed JSON `detail` with stable `code`, safe
`message`, and `retryable`. `413` declares request-size handling and whether a
batch may be split. `422` identifies exactly one rejected event by index,
event ID, source sequence, and bounded reason. `401`/`403` pause authenticated
recovery; `429` is bounded rate/concurrency pressure; `507` is local capacity
pressure; `404`, `409 closing/corrupt`, and `410 sealed` retain distinct source
lifecycle meaning. Raw exception strings, paths, secrets, and storage internals
are not returned.

Gap declarations are first-class durable catalog rows with source, inclusive
start/end sequence, bounded reason code, declaration origin, timestamp, and
optional rejected-event identity hash. Gaps advance contiguous source coverage
without pretending the missing event exists, and remain visible in source,
completeness, and seal evidence.

## Clock fields and uncertainty

Client/server clock exchange uses five samples at session bootstrap. The
versioned estimator records offset, selected minimum RTT, uncertainty, sample
count, and observed drift. RTT is measured with a monotonic client clock; wall
time is only an alignment anchor. Event durations use monotonic clocks. Decimal
nanosecond fields are a storage representation, not the measurement precision:
browser timer resolution, scheduling delay, network asymmetry, background
throttling, and suspension remain explicit uncertainty. `aligned_wall_time_ns`
is a correlation estimate, not a claim of nanosecond cross-device accuracy.

## Direct-open derived schema

Every durably sealed session produces the critical capsule:

- `session.json`: pseudonymous identity, exact basename, media/build/platform
  metadata and capabilities.
- `seal.json`: canonical source/gap watermarks, journal verification, frozen
  host-evidence cutoff/digest, close reason, and derived-artifact status.
- `manifest.json`: generated and directory-fsynced last, with SHA-256 and size
  of every visible critical/derived file plus journal verification reports.

When normal capacity and artifact generation permit, the session also produces:

- `summary.json`: identity, movie complexity, QoE, client/server distributions,
  and diagnostics quality.
- `summary.md`: human-readable local summary.
- `timeline.csv`: aligned semantic/aggregate timeline.
- `completeness.json`: source, sequence, drop, clock, capability, and writer
  quality evidence.

Derived output never consumes the emergency seal reserve. If it cannot be
created, the seal states `derived_artifacts_deferred_capacity` or
`derived_artifacts_failed`; the raw journals and critical capsule remain the
authoritative immutable evidence.

Manifest verification re-hashes every declared visible file and compares raw
journals with the catalog. An `interrupted_recoverable` session is not presented
as finalized and does not receive a final manifest until an explicit close or
offline finalize succeeds.

The derived files never replace raw evidence. Completeness reports expected and
present sources, final and ACK watermarks, explicit missing ranges/counts,
client/server/writer drops, clock coverage, capability availability, lifecycle
state, and incident-window coverage separately. A high score is not inferred
from one event per source; the legacy single telemetry-completeness score is
`null`. Missing or unsupported evidence must not be interpreted as proof that no
issue occurred.

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

Capability flags state only what the current runtime actually exposed. hls.js
attempt detail is reported only when hls.js emitted it; native HLS does not
fabricate loader/cache detail. `freeze`/`resume`, Long Animation Frames,
Compute Pressure, memory, storage, Network Information, frame callback, and
playback-quality evidence remain unavailable/unsupported when their APIs are not
present. Headless browser coverage is not real-device Safari/iOS certification.
