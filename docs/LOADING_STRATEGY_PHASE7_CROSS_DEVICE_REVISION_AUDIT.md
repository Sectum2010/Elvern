# Loading Strategy Phase 7: Cross-Device Opaque Revision Audit

Status: historical Phase 6E audit. The recommended endpoint, layered opaque
revision counters, client polling, and progress-state patch were implemented
by Phase 7. Phase 7B implements zero-state progress, revision-off zero-write
semantics, versioned narrow triggers, transaction de-duplication, poster
fingerprints, and age-query batching. See
`LOADING_STRATEGY_PHASE7B_REVISION_HARDENING.md`. That historical round's
activation remained gated.

**Current status (Phase 7C):** the audited revision endpoint, lightweight
progress patch, and layered client synchronizer are implemented and now default
to frontend `on` by product decision. Progress payload validation, token-race
handling, and disabled/404 capability pause are also implemented. Explicit
frontend `off` and backend capability false remain rollback switches. The
historical recommendations below are retained as design provenance.

## Confirmed Current Behavior

Library queries are user/role/view isolated in TanStack Query with a five-minute
`staleTime` and four-hour `gcTime`. Successful mutations in `LibraryPage`,
`DetailPage`, and `SettingsPage` commonly call the centralized
`invalidateLibraryQueries()`. Progress writes patch all matching in-memory v1
and v2 item instances and mark Library queries stale.

Those mechanisms are local to one browser runtime. Device B does not receive
Device A's invalidation or entity patch. It generally learns about changes when
a query is stale and mounts/refocuses, while scan-in-progress payloads use the
existing 2.5-second polling. A tab that remains active with a fresh cache may
therefore remain stale for roughly five minutes, and longer gaps are possible
when no refetch trigger occurs.

The existing v2 summary `revision` is a SHA-256-style hash of the fully built,
user-visible summary payload. It is useful for payload identity and shadow
comparison, but it is not a cheap revision endpoint: obtaining it already pays
the full summary query, filtering, serialization, and hash cost. There is no
current cross-device revision query or mutation counter.

## Current Cross-Device Gaps

| Change on Device A | Current local behavior | Device B risk |
| --- | --- | --- |
| Scan/cloud sync completes | local invalidate and scan polling | catalog/source changes wait for another refetch |
| Genre/metadata/poster changes | local invalidate | stale cards, filters, tokens, or detail summary |
| User hide/restore or source hide | local invalidate | private visibility differs between devices |
| Global hide/restore | local invalidate for actor | every other signed-in client can show stale access |
| Age/access/permission changes | local invalidate for actor plus session revocation paths | stale visibility is security-relevant |
| Duplicate/Recently Added settings | local settings update and Library invalidate | another device keeps old presentation |
| Progress/completion | local entity patch plus stale mark | Continue Watching/order/completion can lag |
| Cloud reconnect/source ownership | local refresh and invalidate | source membership can lag |

## Recommended Revision Model

Use layered opaque revisions plus a combined value, not one counter for every
kind of activity:

- `catalog_revision`: scanned items, canonical metadata, source membership,
  technical summary fields, genres, poster token/reference.
- `presentation_revision`: per-user settings that alter sections, dedupe,
  Recently Added, or poster URL identity.
- `permission_revision`: role, age/access rules, global visibility, ownership,
  and source access.
- `user_overlay_revision`: per-user hidden items and hidden shared sources.
- `progress_revision`: per-user progress/completion changes.
- `combined_library_revision`: opaque composition of the non-progress layers
  for a simple full-summary invalidation decision.

Layering avoids turning frequent progress heartbeats into full-Library
refetches. Permission changes need the fastest conservative response. Catalog,
presentation, and overlay changes normally invalidate v1/v2 summaries while
allowing exact cached content to remain visible during silent refetch.

## Endpoint Evaluation

### `/api/library/revision`

Pros: version-neutral and usable by v1 and v2. Cons: its ownership becomes
ambiguous while v1 is retained and future schemas diverge.

### `/api/library/v2/revision`

Pros: clear contract ownership, easy capability/rollback boundary, and aligned
with the default non-search path. Cons: v1 search still needs an explicit rule
for which layers invalidate it.

### Revision fields only in summary

Pros: no request. Cons: cannot detect change without downloading the full
summary, so it does not solve the cross-device freshness problem.

Recommendation: add an authenticated, lightweight
`/api/library/v2/revision` in a future phase, while retaining the full summary's
content revision. A possible private response is:

```json
{
  "schema_version": "library-revision-v1",
  "catalog": "opaque",
  "presentation": "opaque",
  "permission": "opaque",
  "user_overlay": "opaque",
  "progress": "opaque",
  "combined_library": "opaque"
}
```

It should use the existing authentication identity, user/role isolation,
`Cache-Control: private, no-store`, `Vary: Cookie`, no media identifiers, and no
globally comparable counts. Logout/401/403 must cancel and remove the revision
query alongside protected caches. Maintenance handling should follow existing
API policy.

## Storage And Atomicity

Recommended source: a small database revision table with scoped integer
counters updated by `INSERT ... ON CONFLICT DO UPDATE` in the same transaction
as each authoritative mutation. API values should be opaque encodings/HMACs of
the relevant scoped counters so clients cannot infer another user's activity.

Timestamps are vulnerable to collisions, clock issues, and same-tick writes.
Re-hashing full tables or summaries is too expensive for a lightweight poll.
An append-only event table adds retention and privacy complexity. Transactional
counters are the simplest reliable source, but they require a schema migration
and service transaction-boundary work; those risks are why this is not a Phase
6E change.

Mutation mapping for a future implementation:

- scan/cloud sync completion: catalog, and permission when source ownership or
  sharing changes;
- metadata/genre/poster token/reference: catalog;
- user hide/restore and hidden source: user overlay;
- global hide/restore and source visibility/ownership: permission plus catalog
  when section membership changes;
- age/access/role/download visibility: permission;
- duplicate and Recently Added settings: presentation;
- poster width/appearance settings: presentation when URL/presentation identity
  changes;
- progress/completed: progress only;
- private/shared reference changes: catalog/presentation only if the resulting
  Library summary actually changes.

## Client Integration

Create a small identity-scoped TanStack revision query in a future phase. On a
layer change:

- keep exact cached Library content visible;
- invalidate affected v1/v2 keys with silent stale-while-revalidate;
- never show Refreshing or clear the cache;
- do not replay a consumed return anchor merely because background data changed;
- use the existing progress entity patch for the local writer;
- for cross-device progress, fetch/patch a small progress delta or item summary
  rather than refetching the full Library every few seconds.

The revision key must include user ID and permission identity. Search remains
v1 until its own design phase, but catalog/permission/overlay changes should
mark exact v1 search keys stale.

## Detection Cadence Trade-Offs

- Focus, `pageshow`, visibility return, and network recovery should trigger an
  immediate revision check.
- Visible 30-second polling gives faster cross-device response but doubles the
  request rate of a 60-second interval.
- Visible 60-second polling is a safer initial baseline for a cheap indexed
  lookup; benchmark concurrent users and SQLite contention before choosing.
- Hidden tabs should pause or use a much slower cadence.
- Active scans can check faster because a known state transition is pending.
- During playback, avoid aggressive progress polling and full summary refresh;
  use the progress layer/delta and reduce background work.

The final cadence must be selected from measured endpoint latency, database
lock time, concurrent-client load, and mobile battery/network cost rather than
from a platform name alone.

## Privacy, Rollback, And Tests

Revision values must reveal neither item identity nor mutation count and must
not be stored as Library content. Test multi-user isolation, logout, role
changes, maintenance, focus/visibility, offline recovery, stale cached display,
progress deltas, scan acceleration, no duplicate restore, and rollback with the
revision feature disabled.

Rollback should disable revision polling without disabling the existing
five-minute cache, mutation-local invalidation, scan polling, v1 fallback, or
progress patch behavior.
