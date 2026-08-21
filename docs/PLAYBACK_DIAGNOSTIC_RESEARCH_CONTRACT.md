# Playback Diagnostic Research Contract

## Purpose

The Playback Diagnostic Research Plane is a local, observer-only flight recorder
for Elvern Browser Playback. It correlates client media evidence, Elvern HTTP
delivery, provider reads, FFmpeg and Route 2 activity, host resources, incidents,
and ETA observations without becoming a playback control input.

The recorder covers Lite and Full Browser Playback on desktop, phone, and tablet.
The initial research focus is Lite. It does not diagnose or change Full playback
policy, and it does not fix playback defects.

## Non-negotiable boundaries

- No visible UI, route, button, toast, modal, setting, or DOM change.
- No external upload or telemetry service.
- No changes to codecs, profiles, segment duration, HLS engine selection,
  buffering, recovery, readiness, provider retry, ATC, or worker allocation.
- No recorder result is returned to playback code as a decision.
- Recorder failures are contained and playback remains authoritative.
- No extra provider request, media read, probe, or transcode is made for
  diagnostics.
- No automatic retention, TTL, purge, oldest-first deletion, or disk-pressure
  deletion exists.

## Data flow

1. Browser Playback creates its existing playback session.
2. A best-effort backend observer creates a pseudonymous diagnostics session in
   a background thread. Failure does not change the playback response.
3. The browser recorder writes allowlisted events to IndexedDB before upload.
4. The browser sends bounded authenticated batches. A small `sendBeacon` batch
   on `pagehide` is best effort only.
5. The ingestion API validates ownership, schema, event and batch sizes,
   sequence, rate, and privacy. It then performs a non-blocking queue insert.
6. A dedicated writer performs compression, AES-GCM encryption, journal fsync,
   and catalog updates outside the ASGI request path.
7. Server, provider, FFmpeg, Route 2, ETA, and host observers copy existing
   state into the same asynchronous observation plane.
8. Session finalization decrypts local journals and writes direct-open summary
   files. Startup recovery finalizes sessions interrupted by a prior process.

The diagnostics API is separate from playback progress and has four endpoints:
`bootstrap`, `batch`, `clock`, and `close` under
`/api/playback-diagnostics/`.

## Identity model

`playback_session_id` is the stable identity for one Browser Playback session.
Recovery, seek, and reattachment remain correlated to that session.

`playback_attempt_id` changes when the observed attachment identity changes.
`attachment_id` identifies one concrete media attachment. `epoch_id` and
`worker_id` come from existing Route 2 state. `incident_id` identifies a stall
or recovery evidence window. `decision_id` identifies an observed ATC decision
or ETA chain when the source subsystem exposes one.

Raw diagnostics do not store a username. A random diagnostic `subject_id` is
mapped to the numeric user ID in an independently encrypted identity file. The
mapping is removed when the account is deleted; already recorded pseudonymous
sessions remain.

## Exact movie basename policy

The exact original basename, including Unicode, spaces, and extension, is an
explicitly approved sensitive field. It is stored as
`source_original_filename`, together with `media_item_id`, a SHA-256 basename
hash, and a source fingerprint. Parent directories and absolute media paths are
never stored. Basename extraction occurs before persistence and rejects null or
missing names.

## Clock model

Client durations use `performance.now()` and server durations use
`time.monotonic_ns()`. Wall clocks are reference points, not duration clocks.
Nanosecond values crossing JavaScript are decimal strings.

At bootstrap the client performs five clock exchanges. The estimator keeps the
lower-RTT half of samples, uses the median offset, the minimum adjusted RTT, and
the greater of half that RTT or half the selected offset spread as uncertainty.
The algorithm is versioned as `min-rtt-median-offset-v1`.

The client recalibrates every 60 seconds and after visibility resumes. Each
event may carry aligned wall time, offset, RTT, and uncertainty. A JavaScript
suspension is represented as lower/upper evidence bounds and an `inferred`
interval; the recorder never invents exact time while iOS or Safari JavaScript
is suspended.

## Observation certainty

Every event states one of:

- `measured_client`
- `measured_server`
- `measured_kernel`
- `measured_provider`
- `derived`
- `inferred`
- `unsupported`

Missing evidence is `null` or absent, never a fabricated zero. Unsupported
capabilities carry `capability_available=false` and an `unavailable_reason`.
Inferred evidence is not labeled measured.

## Sampling and incidents

- Client playhead/buffer ring: 250 ms.
- Permanent client aggregate: 1 second.
- Host/process ring: 500 ms.
- Permanent host aggregate: 1 second.
- GPU: 5 seconds.
- PSS via `smaps_rollup`: 10 seconds.
- Tailscale status: 30 seconds.
- Incident pre-window: 60 seconds.
- Incident post-window: 120 seconds.

Per-frame callbacks stay in memory during normal playback. Incident snapshots
are count- and byte-bounded, with large TimeRanges truncated explicitly. Host
pre-window capture is idempotent per incident and post-window samples are
tagged with the same incident identity.

## Capability matrix

This matrix describes code paths, not real-device certification.

| Evidence | hls.js desktop/mobile | Safari/native HLS | Host Linux |
| --- | --- | --- | --- |
| Media Element lifecycle and TimeRanges | Runtime-detected | Runtime-detected | N/A |
| hls.js loader/append events | Available when hls.js exposes the event | Unsupported | N/A |
| Client fragment-loader detail | `true` only for hls.js | `false` | N/A |
| Native HLS internal cache bytes | Unsupported | Unsupported | N/A |
| Server manifest/init/segment trace | Available | Available | N/A |
| `requestVideoFrameCallback` | Runtime-detected | Runtime-detected | N/A |
| `getVideoPlaybackQuality` | Runtime-detected | Runtime-detected | N/A |
| PerformanceObserver/resource timing | Runtime-detected | Runtime-detected | N/A |
| `performance.memory` | Runtime-detected, mainly Chromium | Usually unsupported | N/A |
| Compute Pressure | Runtime-detected | Usually unsupported | N/A |
| Exact browser/media-process CPU or RSS | Unsupported Web | Unsupported Web | Backend/FFmpeg `/proc` only |
| CPU, memory, PSI, cgroup, filesystem | N/A | N/A | Best effort, unprivileged |
| GPU | N/A | N/A | Best effort through local `nvidia-smi` |
| Tailscale path class | N/A | N/A | Best effort through local CLI |

No COOP, COEP, CSP, cross-origin isolation, exposure, auth, or session setting
is changed to unlock optional browser APIs.

## Native HLS restriction

Safari/native HLS does not expose its internal loader, retries, append queue, or
cache. Elvern records server manifest/init/segment requests, Media Element
events, buffered ranges, playhead, publication evidence, and first-frame
evidence. It sets `client_fragment_loader_detail=false`,
`native_hls_internal_cache=false`, and
`server_segment_request_trace=true`. Server body send is not represented as
client receive, browser buffer, or visible frame.

## Storage and integrity

The default root is `backend/data/playback_diagnostics/`. Directories are mode
`0700`; files are mode `0600`. Symlinks and paths escaping the trusted root are
rejected.

Raw `.elvd` journals are append-only chunks. JSON lines are compressed with
zlib before AES-256-GCM encryption under a dedicated random diagnostics key.
Each chunk has a unique nonce, key ID, schema, sequence, plaintext SHA-256,
previous hash, and current hash. Startup recovery keeps the last complete
chunk, quarantines a corrupt/truncated tail, and continues other sessions.

The key is not derived from the session secret, OAuth data, cookies, Google
tokens, or backup passphrases. Key files and raw diagnostics are excluded from
ordinary backup, Git, and Docker contexts.

## Capacity contract

- Exact hard cap: `80,000,000,000` bytes.
- Normal budget: `79,500,000,000` bytes.
- Emergency reserve: `500,000,000` bytes.
- Default filesystem safety floor: `1,000,000,000` free bytes.

At the normal budget, normal/high-volume data stops and reserve space is used
only for critical gap/capacity/close evidence. At the hard cap or filesystem
floor, new journal writes stop. A bounded current-status file may be replaced.
The recorder remains enabled and does not delete anything. It resumes after the
operator frees space; `reconcile` removes catalog rows for manually deleted
session directories.

## Performance evidence

The repository includes accelerated synthetic benchmarks:

```bash
node frontend/scripts/benchmark-playback-diagnostics.mjs \
  tmp/playback-diagnostics-benchmark/client.json
.venv/bin/python scripts/benchmark-playback-diagnostics.py \
  --output-root tmp/playback-diagnostics-benchmark \
  --client-report tmp/playback-diagnostics-benchmark/client.json
```

The 2026-08-20 local synthetic run measured 1,800 client events with event
creation p95 about 0.10 ms, IndexedDB enqueue p95 about 1.70 ms, and a 256-event
loopback upload p95 about 10.83 ms. The server no-incident 30-minute model wrote
4,020 events and grew the cold diagnostic root by about 4.74 MB; the incident
model wrote 4,895 events and grew it by about 5.12 MB. Conservative linear
2-hour projections were about 18.97 MB and 20.50 MB, or roughly 16,873 and
15,610 such sessions at the 80 GB hard cap.

These are accelerated Linux/headless-Chromium synthetic measurements. They are
not real Mac, iPhone, Google Drive, tailnet, or long-running playback results.
Raw reports are generated under ignored `tmp/playback-diagnostics-benchmark/`.

## Behavior-equivalence gate

Allowed behavioral differences are limited to diagnostic IDs, diagnostics API
requests, local diagnostics files, and internal recorder state. Regression
validation must preserve profile/engine selection, buffer targets, Lite/Full
gates, FFmpeg media commands, segment duration, manifest media content,
attachment/seek/recovery behavior, ATC actions, provider Range requests,
user-visible Browser Playback state, and DOM/UI output.

