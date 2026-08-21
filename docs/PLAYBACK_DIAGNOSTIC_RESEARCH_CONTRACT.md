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

## Opt-in boundary

Diagnostics are disabled by default for new installations. Local research use
requires `ELVERN_PLAYBACK_DIAGNOSTICS_ENABLED=true`. Disabled backend startup
creates no diagnostics root, lease, key, catalog, status, journal, or derived
file; the disabled frontend creates no diagnostics IndexedDB and installs no
diagnostic observer, timer, or listener. There is no diagnostics data UI, data
API, or upload endpoint. The four authenticated HTTP endpoints below exist only
to ingest/control the local recorder when it is explicitly enabled.

## Data flow

1. Browser Playback creates its existing playback session. Its production hook
   performs only a non-blocking bounded immutable snapshot into the diagnostics
   ingress queue. Diagnostic registration, identity work, filesystem setup, and
   catalog persistence happen on diagnostics workers and never gate playback.
2. Backend observations that race registration enter a bounded provisional
   buffer and are flushed only after validated session metadata is durable.
3. A browser module Worker owns event construction, sanitization, IndexedDB,
   batching, upload, clock work, and persistent close recovery. The UI thread
   posts bounded scalar snapshots only. Browser source sequence and IndexedDB
   insertion are one transaction.
4. The browser sends bounded authenticated batches. A small `sendBeacon` batch
   on `pagehide` is best effort only and never authorizes local deletion.
5. The ingestion API validates ownership, schema, event and batch sizes,
   sequence, rate, and privacy, then submits bounded work to a dedicated writer.
6. The writer performs compression, AES-GCM encryption, journal append/fsync,
   catalog commit, durable gap accounting, and contiguous watermark
   recomputation outside playback/ASGI execution. The diagnostics HTTP request
   waits for that completion receipt; queue admission alone is never an ACK.
7. Only the durable ACK watermark returned after both journal fsync and catalog
   commit permits the browser to delete IndexedDB rows. Retriable failure leaves
   them intact.
8. Server, provider, FFmpeg, Route 2, ETA, and host observers copy only existing
   bounded state into the same non-blocking ingress. Internal source sequences
   are assigned at the durable writer boundary, not by hot-path observers.
9. Close records a client final-source barrier, drains accepted observations and
   writer work, seals internal maxima, freezes host links, writes optional
   derived output when normal capacity permits, then writes and verifies the
   critical seal capsule/manifest last. No event may append after seal.
10. Backend startup recovery inspects only unsealed sessions. Browser startup or
   authenticated-login recovery independently scans the owner-isolated
   IndexedDB registry in bounded pages. A crashed active source remains
   `interrupted_recoverable`; neither side fabricates a clean completion.

The diagnostics API is separate from playback progress and has five endpoint
families: `bootstrap`, `batch`, `gap`, `clock`, and `close` under
`/api/playback-diagnostics/`.

## Playback-isolation and overhead contract

`try_capture_diagnostic_observation(...)` is the sole production ingress from
playback modules. It never raises, waits, serializes a full event, performs I/O,
starts work, or acquires a blocking diagnostics lock. It immediately drops when
disabled, unavailable, contended, full, degraded beyond the event's priority,
or circuit-open. Diagnostic return values are optional correlation only and
cannot select a playback path.

Diagnostics workers/executors and all ingress/spool/recovery structures are
bounded. Self-health measures main-thread capture, Worker delay/depth, IDB,
backend ingress/writer depth and latency, host sampler latency, repeated errors,
spool pressure, CPU pressure, I/O pressure, and memory pressure. The exact
ordered modes are `normal`, `reduced_sampling`, `optional_disabled`,
`reduced_aggregates`, `critical_only`, and `circuit_open`. High-frequency data
is removed first, optional resource/performance data second, aggregate
frequency third, and non-critical data fourth. The open circuit accepts only
terminal and gap evidence. These health counters never re-enter the event
stream.

An IndexedDB open that is blocked or rejected for quota immediately uses the
bounded memory fallback instead of delaying recorder startup. Runtime storage
failures are reported only through bounded diagnostics health and cannot escape
into the player/controller call chain.

## Identity model

`playback_session_id` is the stable identity for one Browser Playback session.
Recovery, seek, and reattachment remain correlated to that session.

`playback_attempt_id` changes when the observed attachment identity changes.
`attachment_id` identifies one concrete media attachment. `epoch_id` and
`worker_id` come from existing Route 2 state. `incident_id` identifies a stall
or recovery evidence window. `decision_id` identifies an observed ATC decision
or ETA chain when the source subsystem exposes one.

Raw diagnostics do not store a username. A random diagnostic `subject_id` is
mapped to the numeric user ID in an identity file encrypted with the diagnostics
journal key store. A separate stable HMAC key derives `owner_hash` and network
pseudonyms without making user ownership depend on the rotating active
encryption key. The mapping is removed when the account is deleted; already
recorded pseudonymous sessions remain.

## Exact movie basename policy

The exact original basename, including Unicode, spaces, and extension, is an
explicitly approved sensitive field. It is stored as
`source_original_filename`, together with `media_item_id`, a SHA-256 basename
hash, and a source fingerprint. Parent directories and absolute media paths are
never stored. Basename extraction occurs before persistence and rejects null or
missing names.

Structured private evidence preserves that exact basename. Markdown, terminal,
and CSV views escape control/line-injection bytes, choose a safe Markdown code
delimiter, and prefix spreadsheet-formula values. Browser route/URL identity
uses SHA-256; raw URLs, query strings, provider keys, and tokens are prohibited.

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

Nanosecond fields are decimal transport/storage units, not a nanosecond-accuracy
claim. Browser timer resolution, event-loop scheduling, network asymmetry,
background throttling, and suspension bound the usable precision.

## Durable lifecycle

The durable states are `provisional`, `registering`, `active`,
`interrupted_recoverable`, `closing`, `sealed`, and `corrupt`. A client final
source sequence is complete only when the contiguous durable ACK reaches it.
Internal source maxima are frozen after their observation/writer queues drain.
Missing ranges keep the session open/closing. Duplicate and concurrent
finalizers collapse onto one result, `manifest.json` is generated and verified
last, and only then may the state become `sealed`.

The client state machine is `open -> closing -> sealed`, with explicit
`paused_authentication`, `paused_capacity`, `interrupted_recoverable`,
`orphaned_local`, and `terminal_rejected` states. Once closing starts, ordinary
media/HLS/performance/clock/ACK self-events stop. Exactly one terminal event (or
an explicit durable gap if local persistence cannot accept it) determines the
frozen final client sequence; no N+1 event is allowed.

The persistent recovery registry records client/source identity, close reason
and state, final sequence, last durable ACK, timestamps, response code,
generation, and a short renewable active-worker lease. Recovery skips a live
lease, retries expired/pending rows on startup, login, reconnect, visibility,
and periodic wakeup with bounded jittered backoff, and cleans up only after all
ACK obligations and a stable sealed response.

Allocated sequence loss is never hidden. Durable gap rows retain the exact
inclusive range, reason, declaration origin, timestamp, and optional rejected
event identity. Missing ranges keep a source closing unless covered by such an
explicit declaration; completeness and seal evidence continue to disclose the
loss.

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
Each v2 chunk has authenticated session/source/type identity, a unique nonce,
key ID, schema, sequence, plaintext SHA-256, previous hash, and current hash.

Automatic recovery may truncate only an incomplete final physical record at EOF
while the exclusive lease is held and every prior complete record passes schema,
identity, sequence, chain, nonce, AEAD, decompression, plaintext-hash, and count
verification. The suffix is streamed to quarantine first. Missing/unreadable
keys, bad key metadata, InvalidTag, complete-record corruption, middle-chain or
sequence failure, invalid magic, permission/I/O/path/symlink failure, identity
mismatch, and concurrent-writer suspicion preserve the original bytes and mark
the session corrupt. Sealed historical journals are not decrypted on normal
startup; complete verification is an explicit read-only CLI operation.

The key is not derived from the session secret, OAuth data, cookies, Google
tokens, or backup passphrases. Key files and raw diagnostics are excluded from
ordinary backup, Git, and Docker contexts.

One kernel-backed non-blocking lease permits one mutating owner per diagnostics
root. The live writer and offline `export`, `reconcile`, and `finalize` operations
cannot overlap. Read-only `status`, `list`, `inspect`, and `verify` do not start a
recorder, recovery, writer, or host sampler and do not create files; inspect,
verify, and export accept sealed sessions only.

Finalization atomically freezes host-evidence links before deriving output. The
critical `seal.json` canonicalizes source/gap state, journal verification,
frozen host link count/time bounds/digest, close reason, and derived-artifact
status into one evidence digest. Verification rejects unknown or malformed host
payload fields, duplicate or post-cutoff links, altered source/gap values, and
manifest/hash mismatches. A sealed session is immutable.

## Capacity contract

- Exact hard cap: `80,000,000,000` bytes.
- Normal budget: `79,500,000,000` bytes.
- Emergency reserve: `500,000,000` bytes.
- Default filesystem safety floor: `1,000,000,000` free bytes.

At the normal budget, normal/high-volume data stops and reserve space is used
only for critical gap/capacity/close evidence. At the hard cap or filesystem
floor, new journal writes stop. A bounded current-status file may be replaced.
The recorder remains enabled and does not delete anything. It resumes after the
operator frees space; offline `reconcile` removes catalog rows for manually
deleted session directories.

Startup/reconcile establish physical usage. Thereafter a process-shared locked
ledger atomically reserves final growth, temporary peak, replacement growth,
and conservative catalog/WAL overhead. This removes recursive tree scans from
the batch path and prevents concurrent oversubscription. The conservative ledger
may temporarily exceed physical bytes after SQLite sidecars shrink; this can
reject early but cannot permit the physical tree to exceed the exact hard cap.

All catalog, journal, session artifact, seal/manifest, host, identity, key,
status, lease metadata, export, quarantine, recovery scratch, temporary file,
and SQLite sidecar bytes use the same atomic reserve/commit/release model. A
clean startup fast path is allowed only after every mutation worker stopped and
the integrity-protected clean ledger was durably written; otherwise startup
reconciles physical bytes and marks the ledger dirty. Derived summaries never
consume critical reserve. If normal capacity is unavailable, the small critical
seal capsule can still close honestly and records derived output as deferred.

## Typed failure and corruption policy

Diagnostics HTTP errors have stable safe codes: authentication pauses on
`401/403`; exact `422` invalid-event identity permits one gap; `413` permits
bounded split or a single-event gap; `429` uses bounded backoff; `507` pauses for
operator capacity recovery; missing, closing, sealed, corrupt, and identity
conflict remain distinct. No raw exception, path, or secret is returned.

Automatic journal repair is limited to a physically incomplete final record.
The tail is written/fsynced and atomically renamed/fsynced in quarantine before
the source journal is truncated/fsynced. Every complete-record, crypto, chain,
identity, path, inode, permission, or generic I/O defect preserves original
bytes and marks the session corrupt. Descriptor-relative operations reject
traversal, symlinks, non-regular files, hardlinks, and destination replacement.

There is no TTL or automatic deletion. The operator must stop the mutating
owner, copy evidence if needed, delete selected whole session directories, and
run the local `playback-diagnostics reconcile` command. There is no diagnostics
data-reading web API or visible diagnostics UI.

## Performance evidence

The repository includes accelerated synthetic benchmarks:

```bash
node frontend/scripts/benchmark-playback-diagnostics.mjs \
  tmp/playback-diagnostics-benchmark/client.json
.venv/bin/python scripts/benchmark-playback-diagnostics.py \
  --output-root tmp/playback-diagnostics-benchmark \
  --client-report tmp/playback-diagnostics-benchmark/client.json
```

The 2026-08-21 local synthetic run measured 1,800 client events. The dedicated
Worker capture boundary accepted 4,096 of 4,096 compact observations with p95
and p99 of 0.10 ms, an ordinary maximum of 0.20 ms, a maximum 16-observation
capture task of 0.50 ms, and no attributable long task. IndexedDB enqueue p95
was 1.90 ms; occupied-ring push p95 was 0.00 ms/max 0.10 ms; incremental
serialization p95 was 0.10 ms; and a 256-event loopback-only upload p95 was
12.75 ms. The bounded 60-second rings held 240 sample entries and 7,200 frame
entries (modeled 120 fps), about 1.25 MB in the serialized benchmark
representation.

The no-incident and incident server models durably wrote 4,020 and 4,895 events
with zero writer errors and no capacity-reservation underestimate. A scale case
created 2,000 sealed synthetic sessions plus one open journal; normal startup
reconciled capacity in 75.64 ms, recovered open sessions in 61.19 ms, and
decrypted only the one open journal. The 40,000-iteration ingress benchmark
measured disabled p95/p99 at 0.0038/0.0039 ms and ready p95/p99 at
0.0048/0.0050 ms; a contended capture dropped in 0.0220 ms without waiting.
Concurrent capacity contenders admitted one and rejected one without
oversubscription, and an unindexed two-event journal rebuilt to ACK 2 without
duplicate raw IDs.

These are accelerated local Linux/headless-Chromium synthetic measurements.
Upload latency is localhost only; storage projections come from synthetic event
shape. They are not wall-clock playback, real Mac/Windows/iPhone/iPad/Android,
Safari, provider, Google Drive, tailnet, network, or long-running validation.
Playwright WebKit is also not real Safari/iOS. Raw reports are generated under
ignored `tmp/playback-diagnostics-benchmark/`.

## Behavior-equivalence gate

Allowed behavioral differences are limited to diagnostic IDs, diagnostics API
requests, local diagnostics files, and internal recorder state. Regression
validation must preserve profile/engine selection, buffer targets, Lite/Full
gates, FFmpeg media commands, segment duration, manifest media content,
attachment/seek/recovery behavior, ATC actions, provider Range requests,
user-visible Browser Playback state, and DOM/UI output.
