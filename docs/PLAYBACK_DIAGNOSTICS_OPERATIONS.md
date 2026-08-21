# Playback Diagnostics Operations

## Scope

This is a local operator interface for the observer-only Playback Diagnostic
Research Plane. There is no Elvern UI and no external upload.

## Configuration

Defaults:

```dotenv
ELVERN_PLAYBACK_DIAGNOSTICS_ENABLED=true
ELVERN_PLAYBACK_DIAGNOSTICS_ROOT=<backend data>/playback_diagnostics
ELVERN_PLAYBACK_DIAGNOSTICS_MAX_BYTES=80000000000
ELVERN_PLAYBACK_DIAGNOSTICS_CLIENT_SPOOL_MAX_BYTES=64000000
ELVERN_PLAYBACK_DIAGNOSTICS_BATCH_MAX_EVENTS=256
ELVERN_PLAYBACK_DIAGNOSTICS_BATCH_MAX_BYTES=524288
ELVERN_PLAYBACK_DIAGNOSTICS_MIN_FREE_BYTES=1000000000
```

The 80 GB value is intentionally exact and configuration validation rejects a
different value. Diagnostics are enabled by default and are not date-limited.
Changing an environment value requires the normal Elvern process restart; the
recorder never changes its own enabled flag.

## Files and permissions

Default layout:

```text
backend/data/playback_diagnostics/
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
```

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

Capacity is calculated from the real diagnostics tree before writes. The
recorder does not trust only an in-memory counter.

## No cleanup policy

There is no retention job, TTL, startup purge, oldest-session eviction, or
automatic capacity cleanup. To free space:

1. Stop or otherwise avoid writing to the specific session directory being
   managed.
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

At startup, the recorder scans local journals. A partial tail is copied to
`quarantine/`, the source journal is truncated to its last valid complete chunk,
and other sessions continue. Sessions left active by a prior process are marked
`interrupted` and receive summary/manifest output.

Use `verify <session-id>` to validate AEAD tags, sequence, and hash chains.
Verification does not contact a network service.

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

