# Loading Strategy Phase 7B: Revision Hardening

## Status

Implemented locally on 2026-07-19. Cross-device revision remains frontend
`off` while activation evidence is collected. The code is **ready for canary**,
not enabled. A missing or failed Gate A-F keeps it off; passing most gates is not
sufficient.

This phase does not change View Plan hidden/dedupe behavior, formal search
ranking, v1 availability, pagination, virtualization, resource adaptation,
poster quality, the smart-poster scheduler, pinch behavior, or playback/audio/
subtitle protocols.

## Progress Reset Contract

The former progress-state query omitted rows where position was zero and
`completed` was false. A second device could therefore observe a progress token
change but never receive the authoritative reset, leaving an old progress bar
until an unrelated full Library refresh.

`GET /api/library/v2/progress-state` now returns every accessible existing
`playback_progress` row, including:

```json
{
  "id": 42,
  "progress_seconds": 0.0,
  "progress_duration_seconds": 7200.0,
  "completed": false
}
```

It still omits items with no progress row and items hidden, inaccessible by
source, globally hidden, or denied by age policy. Reset paths retain an upserted
zero row; the only progress-row deletion found is cloud duplicate consolidation,
which first merges state into the surviving canonical item.

The frontend patches every existing v1 visual instance and the v2
`items_by_id` entity. A transition in either `has progress` or `completed`
membership triggers one active silent Library invalidation so Continue Watching
can be rebuilt. Numeric progress-only changes patch in place. No Loading or
Refreshing UI is introduced and the v2 summary revision is not forged.

## Revision Off Means Zero Writes

`ELVERN_LIBRARY_REVISION_ENABLED=false` now means all of the following:

- revision endpoints return the existing explicit 503 capability response;
- startup drops every trigger whose name starts with
  `trg_library_revision_`;
- no current trigger is installed;
- the centralized explicit bump helper returns before SQL;
- ordinary media, setting, hidden, permission, and progress writes cannot
  change revision counters.

The counter table is retained so emergency disable/enable does not destroy
history. `init_db()` always removes old and current definitions before
installing the current `v2` registry, avoiding SQLite's non-replacing
`CREATE TRIGGER IF NOT EXISTS` behavior. Repeated initialization is idempotent.

## Mutation API And Trigger Map

`bump_library_revision_layers(settings, connection, ...)` uses the caller's
transaction. Its connection-level claim registry allows one write per
scope/layer/transaction. A rollback rolls back the counter and clears claims;
commit starts a new de-duplication window.

| Truth change | Layer | Mechanism |
| --- | --- | --- |
| media membership/identity/card fields | global catalog | narrow v2 trigger; local scan claims once before its first real mutation |
| technical quality/card fields | global catalog | field-level trigger |
| genre group title/year/genres | global catalog | field-level trigger |
| source provider/resource/display/path | global catalog | field-level trigger |
| source owner/shared access | global permission | field-level trigger |
| age requirement/manual group | global permission | field-level trigger |
| global hidden item/movie key | global permission | narrow trigger |
| user role/enabled/age identity | user permission | field-level trigger for old/new user scope |
| allowed Library presentation settings only | user presentation | allowlisted field-level trigger |
| user hidden source/item/movie | user overlay | narrow trigger for old/new user scope |
| position/duration/completed/item identity | user progress | field-level trigger for old/new user scope |
| media/poster reference setting | global catalog | transaction-bound explicit bump |
| poster index content fingerprint | global catalog | successful-change explicit bump |

`updated_at`, scan timestamps, source `last_synced_at`, `last_error`, diagnostic
probe fields, and unrelated user settings do not bump revisions. Distributed
cloud/provider writes remain protected by narrow transaction-de-duplicated
triggers. They were not broadly refactored because that would exceed this phase.

### Write amplification evidence

The old broad row trigger could update the catalog counter once for every row in
a 3000-item transaction. The Phase 7B synthetic 3000-row test records a catalog
counter delta of exactly **1**. A normal progress save records a user progress
delta of exactly **1**. Source diagnostic timestamp/error updates record zero;
owner/share and source identity update only their intended layer. Rollback records
zero committed delta.

## Poster And Media References

Media-library and poster-reference settings now reuse the caller's database
connection. Setting truth and catalog revision commit atomically; saving the same
value does not bump.

Each successful Poster Index snapshot hashes sorted entry identity using filename,
size, mtime, ctime, and inode. Only hashes are persisted in
`poster_index_fingerprints`; raw poster roots and file paths are absent from the
revision response, fingerprint table, and warning log. The first successful
snapshot establishes a baseline without a false bump. Add, delete, rename, and
same-size replacement cause one catalog bump. A warm lookup is bounded by a
30-second revalidation interval, and revision polling itself never scans the
poster directory. Failed rebuilds publish neither a fingerprint nor a revision.

## Progress-State Age Batching

The old implementation reopened SQLite and recalculated age access once per
progress item. The new helper accepts the existing connection and uses three
batch queries for item identity, manual links, and requirements while reusing
the canonical automatic age-group resolver. Admin bypass and standard-user
manual/automatic grouping remain unchanged.

Temporary synthetic benchmark, 30 warm runs on this development host:

| Operation | SQL count | SELECT/WITH | p50 | p90 | worst |
| --- | ---: | ---: | ---: | ---: | ---: |
| revision endpoint service, one user | 1 | 1 | 0.794 ms | 0.838 ms | 1.062 ms |
| progress-state, 100 rows | 13 | 11 | 74.130 ms | 74.860 ms | 75.196 ms |
| progress-state, 1000 rows | 13 | 11 | 731.790 ms | 737.273 ms | 740.704 ms |

The fixed SQL count proves removal of N+1. Runtime still grows with returned rows
and title age-group parsing; these timings are diagnostic, not cross-machine
pass/fail thresholds. The revision endpoint only reads counters and does not
build a Library View Plan.

## Repeated Outages And Strict Health

The runtime controller now tracks `outageGeneration` and
`oopsLatchedGeneration`. Full verified recovery clears the current latch,
evidence reason, deadline, and candidate confirmation timers. A later independent
VPN/frontend or backend outage receives a new generation and can show Oops again.
Lifecycle and outage generations reject stale asynchronous probes/timers. The
standalone offline document retains its deliberate one-way per-document latch.
An already-running app remains mounted and continues to use the No Internet
notice.

Health success is now strict:

- frontend: 2xx plus `X-Elvern-Frontend-Health: 1`;
- backend: 2xx plus `X-Elvern-Backend-Health: 1`;
- both responses use `Cache-Control: no-store`;
- 401/403/404 are `unexpected_http_status`, 5xx is `http_unhealthy`, and a
  redirected HTML 200 without the endpoint-specific marker is `marker_missing`;
- frontend/backend markers cannot substitute for one another and no response
  body is read.

Health probes do not invoke auth logout behavior. Timeout and lifecycle abort
retain their separate evidence reasons.

## Search Control And DOM

Committed search now uses one pure reducer with `URL_SYNC`, `UPDATE_DRAFT`,
`COMMIT`, `REVERT`, `CLEAR`, and `RELEASE_LOCK`. Draft and owner change atomically;
no state updater invokes another setter. Every mutating controller API validates
the active owner, so a locked static/floating source cannot change, submit,
clear, or revert the other source through programmatic events.

Root renders one shared `StaticLibrarySearch` instance. Local and Cloud reuse the
same component. Desktop/laptop static and floating inputs do not create clear-X
buttons and use text inputs with search semantics, avoiding a native search
cancel control. Users clear by deleting text and submitting Enter. Phone/tablet
Floating search retains its explicit X. Escape restores the committed URL query,
IME submission remains guarded, drafts stay independent, and the existing
Dynamic search setting wording is unchanged.

CSS places the same Root form beneath the hero actions at narrower laptop widths
and in-row at wide desktop widths. No duplicated breakpoint-only input remains.

## Opt-In Browser Harness

`run-cross-browser-playwright.mjs` selects an OS-assigned localhost port,
coordinates concurrent runner processes with a temporary port lock, creates a
random base32-safe prefix, and writes to a unique ignored `tmp/` result folder.
Firefox, WebKit, Chromium-revision, and combined scripts use this runner. These
projects remain local opt-in and are not part of normal CI.

The browser installer reads installed `playwright` and `@playwright/test`
package metadata, verifies both and the local CLI have the same version, and
then installs Firefox/WebKit. There is no handwritten Playwright version.

## Activation Gates

| Gate | Required evidence | Final local status |
| --- | --- | --- |
| A correctness | progress/reset/layers/poster/outage/health/search/privacy | **PASS (automated)**: targeted, full frontend, and full backend suites pass |
| B write cost | off zero-write, 3000-row de-dupe, fixed SQL benchmarks | **PASS (automated)**: zero-write and de-duplication contracts pass; fixed SQL counts measured |
| C dual-client canary | same-account mutations plus different-account isolation | **PARTIAL / NOT PASSED**: two independent contexts prove 120 to 0 reset, Continue Watching removal, and catalog revision; the full mutation matrix and different-account canary remain |
| D browsers | Chromium, Firefox, WebKit production runs | **NOT PASSED**: Chromium 7/7 and Firefox 7/7 pass; all 7 WebKit cases are blocked before test execution because this host lacks `libavif.so.16` |
| E approved real devices | one approved real two-device combination | **NOT RUN**: no approved real-device pair was available in this coding environment |
| F rollback | frontend off and backend disabled rehearsal | **PASS (automated)**: frontend off-mode and backend disabled-mode contracts pass; final local bundle was rebuilt with revision off |

Because Gate E cannot be claimed from this host, the maximum honest outcome is
`ready-for-canary`; frontend revision remains `off` even if automated validation
passes.

## Validation Results

The final local activation decision is **ready-for-canary**. Revision polling is
not enabled by default and no live environment was changed.

- targeted backend revision/progress/poster/security checks: 82 passed;
- targeted frontend revision/search/health/Library checks: 16 files, 247 passed;
- complete frontend Vitest: 72 files, 914 passed;
- complete backend pytest: 1678 passed, one upstream deprecation warning;
- fresh CI mirror: passed, including the same 1678 backend tests and 914
  frontend tests, .NET Release build, production Vite build, `pip-audit`,
  Bandit, and `npm audit`;
- production Chromium revision suite: 7 passed;
- production Firefox revision suite: 7 passed;
- production WebKit revision suite: 7 blocked at browser launch by missing host
  library `libavif.so.16`; no application assertion ran;
- production service-worker/offline suites: 6 passed across Phase 6D and Phase
  5A/update coverage;
- visual screenshots inspected at 1440, 1024, and 900 CSS-pixel widths: one
  static search DOM, no desktop clear button, and no horizontal overflow;
- `npm run ui:qa`: not runnable through its development-server configuration on
  this host because Vite hit `ENOSPC` while watching `frontend/index.html`;
  production-server Playwright suites passed instead;
- `git diff --check`: passed;
- `npm audit --prefix frontend --audit-level=high`: zero vulnerabilities;
- `pip-audit` for runtime and test requirements: no known vulnerabilities;
- Bandit repository gate: no qualifying issues.

The opt-in browser commands use a dynamic port, random valid prefix, and unique
ignored output directory. The final default/off production build was restored
with:

```bash
VITE_ELVERN_LIBRARY_REVISION_MODE=off npm run build --prefix frontend
```

Remaining activation evidence is the complete Gate C mutation/isolation matrix,
a WebKit run on a host with its required runtime libraries, and Gate E on an
approved real-device pair. Until all three are recorded, revision mode stays
off.

## Rollback

Frontend one-step build rollback:

```bash
VITE_ELVERN_LIBRARY_REVISION_MODE=off npm run build --prefix frontend
```

Deploy that bundle with the repository's normal frontend lifecycle procedure.
It stops revision polling while preserving the five-minute Library cache, v2,
formal v1 search, and local progress patches.

Backend emergency stop:

```text
ELVERN_LIBRARY_REVISION_ENABLED=false
```

The backend setting requires normal backend initialization/restart to drop
triggers and return 503 from revision endpoints. No live environment is changed
or restarted by Phase 7B validation.
