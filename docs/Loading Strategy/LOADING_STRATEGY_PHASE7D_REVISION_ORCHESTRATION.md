# Loading Strategy Phase 7D: Revision Orchestration

## Status

Phase 7D keeps cross-device Library revision enabled by default and hardens the
frontend coordination boundary. One revision operation now owns one final
summary-refresh decision, progress patches are scoped to the captured protected
identity, progress-state capability failure no longer disables unrelated
revision layers, and revision snapshots use an exact contract.

Unset or empty `VITE_ELVERN_LIBRARY_REVISION_MODE` remains `on`. Explicit `on`
and `off` remain valid; every other non-empty value fails closed to `off`.
`ELVERN_LIBRARY_REVISION_ENABLED=false` remains the backend emergency stop.

## Confirmed Root Causes

The previous apply path could invalidate Library queries for a catalog,
presentation, permission, or overlay change and then call a progress patch
helper that performed another active invalidation when Continue Watching or
completion membership changed. A combined revision could therefore produce two
summary refresh decisions.

The progress helper also inspected every cached Library query family without
checking the query's user or role. Auth cleanup normally removes protected
caches, but that lifecycle behavior was the only isolation layer. A delayed
operation could also outlive the effect identity that created it.

Finally, one generic capability helper treated a 404 from either revision URL
as a reason to stop all polling, and the revision validator accepted unknown
top-level fields after validating the known tokens.

## Single-Refresh Orchestration

`patchLibraryProgressStateCaches()` is now a pure, identity-scoped entity patch.
It returns `patchedQueryCount` and `membershipMayHaveChanged`; it never marks a
query stale and never starts a refetch.

`applyLibraryRevisionChange()` collects only fixed, non-private reasons:

- `catalog`
- `presentation`
- `permission`
- `user_overlay`
- `progress_membership`
- `progress_capability_fallback`

After permission revalidation, user-settings invalidation, and any progress
entity patch, the operation calls the identity-scoped Library invalidation at
most once. Reasons never contain an item ID, title, query, path, or user name.

| Revision change | Entity patch | Full summary refresh |
| --- | --- | --- |
| catalog only | no | once |
| progress numeric only | yes | none |
| progress membership/completion | yes | once |
| catalog plus progress numeric | yes | once |
| catalog plus progress membership | yes | once |
| presentation plus permission plus progress membership | yes | once, plus separate settings/auth work |
| progress with progress-state unavailable | no | once per new progress token |

The existing `combined_library` value remains a derived comparison token. The
same four non-progress layers that previously required summary invalidation
continue to do so; Phase 7D does not change Library membership semantics.

## Protected Cache Identity

Revision progress patches and revision-owned invalidation require an explicit
`{ userId, role }`. The query must be a valid v1, v2, or shadow-v2 Library key,
and both normalized values must match exactly. Missing, empty, non-scalar, or
malformed identity values patch nothing. A query belonging to another user or
role keeps both its object reference and `dataUpdatedAt`.

The Library query-key schema is unchanged. It does not currently carry the
revision query's age/assistant permission identity, so the cache-level second
defense compares user ID and role. The synchronizer effect identity still
contains user ID, normalized role, age credential, and Assistant access state.
Auth identity changes clear protected queries, while a lifecycle generation
check rejects old work after every awaited boundary and before patch,
invalidation, or baseline commit.

## Capability Boundaries

Revision endpoint capability and progress-state sub-capability are separate:

- revision endpoint 404: stop the current identity's synchronizer lifecycle;
- revision endpoint 503 with `library_revision_disabled`: stop it;
- progress-state 404: disable only progress-state for the current lifecycle;
- future progress-state 503 with `library_progress_state_disabled`: disable
  only progress-state;
- progress-state 503 with `library_revision_disabled`: stop the entire
  synchronizer because the backend kill switch is global.

After a progress-only capability failure, revision polling, catalog,
presentation, permission, and overlay handling continue. Each new progress
token skips the missing endpoint, silently invalidates the current identity's
summary once, and advances that progress baseline. The authoritative v2 summary
supplies progress and membership. Repeating the same token does no work.
Reload or identity change starts a new lifecycle and probes progress-state again.

Progress-state 401, 403, ordinary 500, timeout, network failure, or malformed
payload does not disable either capability and does not advance the progress
baseline. A token race also retains the old progress baseline and receives at
most two immediate retries before returning to normal 60-second/focus cadence.

## Exact Revision Contract

`validateLibraryRevisionPayload()` accepts only an ordinary object containing
exactly these seven fields:

1. `schema_version`
2. `catalog`
3. `presentation`
4. `permission`
5. `user_overlay`
6. `progress`
7. `combined_library`

`schema_version` must equal `library-revision-v1`. Every token must be a string
of exactly 64 lowercase hexadecimal characters. Arrays, missing fields, extra
fields, uppercase tokens, wrong lengths, nulls, numbers, whitespace, titles,
paths, IDs, and raw counters fail with `LibraryRevisionContractError` before
baseline or Library cache mutation. A malformed response retains normal polling
cadence and a later valid response can establish or advance the baseline.

## Baseline Advancement

- a valid first snapshot establishes all layers;
- successful layers advance even when a transient progress fetch fails;
- successful or progress-capability-fallback progress tokens advance;
- progress 401/403/500/network/malformed/race retains the prior progress token;
- progress global-disable stops the lifecycle without applying the operation;
- a stale identity generation commits no baseline.

## Validation

Phase 7D local validation completed with:

- focused revision/query contract suite: 2 files, 48 passed;
- revision/query/Auth/LibraryPage/LibrarySourcePage suite: 5 files, 110 passed;
- complete frontend Vitest: 73 files, 952 passed;
- complete backend pytest: 1678 passed;
- production builds with default revision `on` and explicit `off`: passed; the
  repository's existing large-chunk warning remains;
- Chromium production Phase 7 suite: 14 passed, including one-summary refresh,
  progress 404 fallback, exact contract recovery, and two-identity coverage;
- Firefox production Phase 7 suite: 14 passed;
- WebKit production Phase 7 suite: no application assertion ran because the
  installed browser cannot launch on this host without `libavif.so.16`; all 14
  cases are environment-blocked, consistent with the retained Phase 7B record;
- production desktop/mobile Service Worker suite: 4 passed;
- production offline-shell install/update suite: 2 passed;
- fresh local CI: passed with 1678 backend tests, 952 frontend tests, .NET
  Release build, Vite production build, pip-audit, Bandit, and npm audit;
- standalone npm audit: zero vulnerabilities;
- standalone runtime and test pip-audit: no known vulnerabilities;
- standalone Bandit high-severity gate: no issues identified;
- `git diff --check`: passed.

No approved real-device pair was available, so real cross-device timing remains
unclaimed. No live environment was changed and no live service was restarted.

## Rollback

Frontend rollback:

```bash
VITE_ELVERN_LIBRARY_REVISION_MODE=off npm run build --prefix frontend
```

Backend emergency stop:

```text
ELVERN_LIBRARY_REVISION_ENABLED=false
```

Phase 7D does not implement View Plan hidden/dedupe work, formal v2 search,
SQLite FTS, v1 retirement, pagination, virtualization, a resource adapter,
cross-platform scheduler expansion, or pinch restrictions. It does not modify
search, playback, audio, subtitles, relocation, orientation restoration,
poster width, or q97.
