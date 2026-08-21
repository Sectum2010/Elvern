# Playback Diagnostics Operations

## Scope

This is a local operator interface for the observer-only Playback Diagnostic
Research Plane. There is no Elvern UI and no external upload.

## Configuration

Defaults:

```dotenv
ELVERN_PLAYBACK_DIAGNOSTICS_ENABLED=false
ELVERN_PLAYBACK_DIAGNOSTICS_ROOT=<backend data>/playback_diagnostics
ELVERN_PLAYBACK_DIAGNOSTICS_MAX_BYTES=80000000000
ELVERN_PLAYBACK_DIAGNOSTICS_CLIENT_SPOOL_MAX_BYTES=64000000
ELVERN_PLAYBACK_DIAGNOSTICS_BATCH_MAX_EVENTS=256
ELVERN_PLAYBACK_DIAGNOSTICS_BATCH_MAX_BYTES=524288
ELVERN_PLAYBACK_DIAGNOSTICS_MIN_FREE_BYTES=1000000000
```

The 80 GB value is intentionally exact and configuration validation rejects a
different value. New installations are opted out. A local operator must set
`ELVERN_PLAYBACK_DIAGNOSTICS_ENABLED=true` explicitly to start the recorder.
When disabled, backend startup does not create the diagnostics root, key,
catalog, status file, or writer lease, and the frontend does not open the
diagnostics IndexedDB or install diagnostic observers, timers, or listeners.

Changing an environment value requires the normal Elvern process restart; the
recorder never changes its own enabled flag. There is no Elvern UI for enabling,
viewing, or exporting this local research data.

## Files and permissions

Default layout:

```text
backend/data/playback_diagnostics/
  .writer.lock
  catalog.sqlite3
  recorder-status.json
  keys/
  identities/
  sessions/YYYY/MM/DD/<playback_session_id>/
    session.json
    summary.md
    summary.json
    timeline.csv
    completeness.json
    manifest.json
    raw/*.elvd
  derived/
  exports/
  quarantine/
```

Directories are `0700` and files are `0600`. The recorder refuses a symlinked
root, key, catalog, journal, or output path and rejects directory traversal.

Files designed for direct local opening include:

```text
backend/data/playback_diagnostics/sessions/2026/08/20/<session-id>/summary.md
backend/data/playback_diagnostics/sessions/2026/08/20/<session-id>/timeline.csv
backend/data/playback_diagnostics/sessions/2026/08/20/<session-id>/session.json
```

Raw `.elvd` files are encrypted containers and should be read through the CLI.

## CLI

Run from the repository root with the same environment used by Elvern:

```bash
.venv/bin/python -m backend.app.cli playback-diagnostics status
.venv/bin/python -m backend.app.cli playback-diagnostics list
.venv/bin/python -m backend.app.cli playback-diagnostics list --date 2026-08-20
.venv/bin/python -m backend.app.cli playback-diagnostics list --basename 'Movie Name.mkv'
.venv/bin/python -m backend.app.cli playback-diagnostics list --source cloud --platform ios --mode lite
.venv/bin/python -m backend.app.cli playback-diagnostics inspect <session-id>
.venv/bin/python -m backend.app.cli playback-diagnostics verify <session-id>
.venv/bin/python -m backend.app.cli playback-diagnostics export <session-id> --format ndjson
.venv/bin/python -m backend.app.cli playback-diagnostics export <session-id> --format csv
.venv/bin/python -m backend.app.cli playback-diagnostics export <session-id> --format perfetto
.venv/bin/python -m backend.app.cli playback-diagnostics export <session-id> --format parquet
.venv/bin/python -m backend.app.cli playback-diagnostics reconcile
.venv/bin/python -m backend.app.cli playback-diagnostics finalize <session-id>
```

`status`, `list`, `inspect`, and `verify` use a dedicated read-only store. They
do not instantiate the live recorder, run recovery, start the writer or host
sampler, create missing files, or mutate the catalog. `inspect` and `verify`
accept only durably `sealed` sessions.

`export`, `reconcile`, and `finalize` are offline maintenance operations because
they create or change local files. They acquire the same non-blocking kernel
lease as the live writer and fail if Elvern or another maintenance process owns
the diagnostics root. Stop Elvern before running them. `finalize` accepts only
an `interrupted_recoverable` session and deliberately freezes its currently
durable source maxima; use it only after the browser has had no further chance
to replay its local spool.

There is exactly one mutating owner per diagnostics root. The live recorder,
offline maintenance, and identity unlink all use `.writer.lock`; lock metadata
is informational and the kernel `flock` is authoritative. Do not delete the lock
file to bypass ownership.

Exports are local files under `playback_diagnostics/exports/`, mode `0600`.
NDJSON, CSV, and Perfetto/Chrome Trace JSON use standard runtime dependencies.
Parquet is optional and lazy-loaded:

```bash
.venv/bin/python -m pip install -r backend/requirements-diagnostics-export.txt
```

Missing Parquet dependencies do not affect Elvern startup or recording.

## Capacity states

`recorder-status.json` and `playback-diagnostics status` expose:

- `normal`: writes fit under the 79.5 GB normal budget.
- `capacity_reached`: normal writes stop; critical events may use the 500 MB
  reserve.
- `reserve`: a critical write is admitted within that reserve.
- `capacity_exhausted`: the 80 GB hard cap prevents new persistence.
- `filesystem_low_space`: the filesystem safety floor prevents new persistence.

Startup and explicit reconciliation establish physical usage. Steady-state
writes use one process-shared, lock-protected ledger with atomic reservations;
the writer does not recursively walk the diagnostics tree for every batch. A
reservation includes the final bytes, temporary peak, replacement behavior,
and a conservative catalog/WAL allowance. Concurrent reservations cannot
oversubscribe either the normal budget or the hard cap.

The runtime ledger can conservatively exceed current physical bytes while
SQLite sidecars shrink or disappear; that causes earlier rejection, not a hard
cap bypass. Offline `reconcile` re-measures physical usage. The hard contract is
that diagnostics-owned physical storage must not exceed 80,000,000,000 bytes.

## No cleanup policy

There is no retention job, TTL, startup purge, oldest-session eviction, or
automatic capacity cleanup. To free space:

1. Stop Elvern so the diagnostics writer lease is released.
2. Copy any session needed for research to appropriately protected storage.
3. Manually delete only selected session directories under `sessions/`.
4. Run:

   ```bash
   .venv/bin/python -m backend.app.cli playback-diagnostics reconcile
   ```

5. Re-run `playback-diagnostics status` and confirm the state returns to
   `normal` when enough space exists.

Do not delete individual journal chunks from a retained session. Doing so makes
that session incomplete. Deleting key material makes encrypted raw journals
unreadable and is not a supported cleanup method.

## Crash recovery and corruption

At startup, the recorder considers only sessions that are not durably `sealed`.
It does not decrypt all historical sealed journals. Missing catalog rows are
rebuilt from valid source-bound journal chunks, and an unfinished session is
left `interrupted_recoverable` so an offline browser can resume the same client
source and replay its IndexedDB queue. Startup does not automatically seal that
session or write final summary/manifest output.

Automatic repair is intentionally narrow. It is allowed only while the process
holds the exclusive writer/maintenance lease, every prior complete record has
passed schema, identity, sequence, hash-chain, nonce, AEAD, decompression,
plaintext-hash, and event-count checks, and the sole defect is an incomplete
final physical record at EOF. That suffix is copied to `quarantine/` by
streaming from the failure offset and only then truncated.

Missing or unreadable keys, invalid key metadata, InvalidTag, hash/sequence
corruption, invalid magic, complete-record decompression/JSON corruption,
source/session mismatch, permission or generic I/O failure, symlink/path
failure, and suspected concurrent writing are never auto-truncated. Original
bytes remain in place and the session is marked `corrupt` for local operator
attention.

Use `verify <session-id>` to validate journal authentication and chaining,
catalog-to-journal identity, source watermarks, private permissions, and every
file size/hash declared by the final manifest. Verification does not contact a
network service.

## Session lifecycle and ACK

Durable states are `provisional`, `registering`, `active`,
`interrupted_recoverable`, `closing`, `sealed`, and `corrupt`. Backend observers
may place a bounded provisional observation before registration finishes; it is
flushed only after session metadata exists.

The batch HTTP response advances `ack_watermark` only after the writer thread
has appended the encrypted journal record, flushed and fsynced it, committed the
catalog transaction, and recomputed the contiguous source watermark. Queue
admission is not an ACK. Timeouts and write/catalog failures return a retriable
failure and leave the browser spool intact.

Closing records the client's declared final source sequence. A gap keeps the
session in `closing`; a later replay may fill it. Finalization drains backend
observations and writer work, seals internal source maxima, writes all derived
files, writes `manifest.json` last, verifies the visible-file manifest, and then
marks the catalog row `sealed`. Concurrent or duplicate close/finalize attempts
share one finalization result. Sealing rejects every later append.

## Failure behavior

- Recorder startup failure disables only the in-process recorder instance.
- Browser Playback session creation still succeeds.
- Queue, validation, encryption, catalog, host sampler, provider observer, and
  writer failures are caught at observer boundaries.
- Queue/capacity loss is counted and represented as `telemetry_gap` or recorder
  status when possible.
- A missing measurement is not proof that no playback fault occurred.

## Backup, Git, and Docker

`backend/data/playback_diagnostics/` is explicitly excluded by `.gitignore` and
`.dockerignore`. Ordinary backups set `playback_diagnostics_included=false` and
exclude the runtime path. Ordinary restore does not restore it. Keys, raw
journals, direct-open summaries, exports, and benchmark output must not be
committed.

## Synthetic benchmark

```bash
node frontend/scripts/benchmark-playback-diagnostics.mjs \
  tmp/playback-diagnostics-benchmark/client.json
.venv/bin/python scripts/benchmark-playback-diagnostics.py \
  --output-root tmp/playback-diagnostics-benchmark \
  --client-report tmp/playback-diagnostics-benchmark/client.json
```

Reports are `benchmark.json` and `benchmark.md` under the ignored output root.
The benchmark uses synthetic basenames, IDs, metrics, and a temporary localhost
endpoint. It does not start Elvern, read media, or contact a provider.

The current benchmark includes occupied 60-second sample/frame rings, sustained
O(1) pushes, incremental incident serialization, IndexedDB enqueue/read/ACK and
reload recovery, loopback upload, steady-state writer batching, concurrent
capacity reservations, unindexed-journal catalog rebuild, and 2,000 sealed
synthetic sessions plus one open session. It is an accelerated Linux/headless
Chromium measurement, not real playback and not certification for Safari,
iPhone/iPad, macOS, Windows, a provider, or a tailnet.
