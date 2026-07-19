# Loading Strategy Phase 6E: Trust And Durable Recovery

Status: implemented locally; browser and real-device validation remain required.

## Scope

Phase 6E fixes three correctness gaps without changing Library data APIs,
playback, poster quality, or relocation algorithms:

1. physical paint geometry can no longer become trusted relocation geometry;
2. an explicit Retry can recover when public probes are blocked but Elvern is
   genuinely reachable; and
3. a Service Worker recovery arm survives a worker restart.

## Trusted Viewport Boundary

The confirmed failure chain was:

1. iOS started with a suspicious short `clientHeight`;
2. no matching persisted geometry was available;
3. the settle timeout constructed a fallback using the physical paint floor;
4. the old stable promotion path stored that fallback as trusted geometry;
5. local storage was written and the restore gate opened; and
6. relocation could therefore consume a height that existed only to cover the
   physical screen background.

The coordinator now separates these operations:

- `setProvisionalLayout(viewport, reason)` permits painting and normal route
  navigation, but cannot write trusted orientation state, write geometry,
  dispatch the stable event, or open the restore gate.
- `promoteTrustedLayout(viewport, evidenceSource)` is the only trusted
  promotion boundary and the only caller of `writeIOSViewportGeometry()`.

Allowed promotion evidence:

- `clean_stable_samples`
- `persisted_geometry` after the existing platform, display-mode, orientation,
  width-bucket, age, and screen-geometry checks

Explicitly untrusted evidence:

- `provisional_layout`
- `physical_paint_floor`
- `screen_geometry` by itself
- `large_viewport_probe` by itself

Invalid promotion evidence throws in development/test and is rejected in
production. Snapshot state now exposes `trustedLayoutVerified`,
`trustedEvidenceSource`, and `layoutVerificationState`.

If a suspicious initial layout has no validated geometry and no clean samples
arrive before the hard timeout, the state becomes
`paint_ready_layout_unverified`. The current layout remains provisional, the
physical floor still covers the device background, Auth settling returns
`false` at its existing hard bound so navigation can continue, and relocation
continues waiting with its gate closed. Later matching clean samples can make
the layout trusted, write one bounded geometry record, dispatch one stable
event, and open the existing gate.

No relocation module reads `physicalPaintFloorHeight` or
`--app-physical-paint-floor-height`; a static contract test covers
`viewportAnchor`, `libraryNavigation`, desktop return restore, and
`LibraryPage`.

## Public-Probe-Blocked Manual Recovery

Every public result now carries a reason:

- `endpoint_success`
- `browser_explicit_offline`
- `probe_failure_trusted`
- `probe_failure_unverified`
- `probes_disabled`
- `aborted`

Automatic, online-event, and visibility-return recovery remain strict. They
require public Internet success, frontend health, backend health, and a
network-only same-origin App Shell response marked with
`X-Elvern-App-Shell: 1` and not `X-Elvern-Offline-Shell: 1`.

Only a user click on the existing text Retry may use
`manual_service_only`. It requires `navigator.onLine !== false`, healthy
frontend and backend responses, and the same verified App Shell marker. It may
accept blocked/disabled public probes, but it cannot accept
`browser_explicit_offline`. The App Shell body is never read. A click received
while an automatic transaction is active is queued once rather than dropped.

Retry still does not reset the immutable 60-second deadline, clear the Oops
latch, restart animation, add URL state, or replace the existing text control.
The running application and its sticky No Internet behavior are unchanged.

## Durable Service Worker Recovery Arms

The in-memory `Map` was not durable: a browser could terminate the worker after
ACK and before reload, leaving the replacement worker without the arm. The
authority is now IndexedDB:

```text
database: elvern-service-worker-state-v1
object store: recovery_arms
key: scope identity + source client ID
```

Each record contains only:

```text
schema_version
scope_identity
source_client_id
nonce
expires_at
created_at
```

The store contains no route, query, account, user/media ID, Library data,
cookie, token, poster URL, probe response, or IP. Arms are 15-second,
one-shot, per-client, and per-scope, with a global bound of 32 records. Expired
records are removed during activation, before writes, and before consumption;
oldest records are removed when the bound is exceeded.

The worker sends `accepted: true` and `durability: durable` only after the
read-write transaction commits. Navigation atomically validates and deletes
the arm, preferring `event.replacesClientId` and falling back to
`event.clientId`. A valid arm selects the existing 15-second recovery path;
missing, expired, cross-client, cross-scope, or failed reads use the ordinary
8-second path.

IndexedDB failure does not fail installation or online navigation. A failed
write returns `accepted: false`; a failed read uses normal navigation. The
worker emits at most one generic warning and does not retry in a loop or fall
back to a second authority.

The Service Worker still caches only the stamped `offline.html`. It does not
cache SPA bundles, API/auth/Library data, settings, health responses, posters,
or media.

## Validation Contract

Focused tests cover:

- untrusted viewport evidence rejection;
- suspicious launch timeout without storage, stable event, or restore gate;
- later clean promotion and matching persisted geometry;
- Auth hard-bound completion while geometry is unverified;
- automatic versus manual recovery decisions and explicit-offline denial;
- App Shell marker failure;
- durable ACK only after commit;
- worker restart, replacement client, one-shot, expiry, scope/client isolation,
  32-record bound, cleanup, and IndexedDB failure; and
- the absence of paint-floor inputs from relocation modules.

Validation completed on 2026-07-18:

- focused frontend: 17 files, 198 tests passed;
- full frontend Vitest: 68 files, 872 tests passed;
- targeted backend viewport-adjacent Library/poster/prefix coverage: 116 passed;
- full backend pytest: 1657 passed;
- frontend production build: passed, with the existing large-chunk warning;
- production Service Worker E2E: 4 desktop/mobile Phase 6E tests passed;
- existing production offline-shell E2E: 2 tests passed;
- general Chromium desktop/mobile Playwright: 8 passed and 12 intentional
  project/platform skips; the first Vite launch hit the host file-watcher
  `ENOSPC` limit, then passed with polling enabled for that validation process;
- `npm audit`, production/test `pip-audit`, and Bandit: passed;
- fresh local CI: passed, including 1657 backend tests, 872 frontend tests,
  the .NET helper build, frontend build, dependency audits, and Bandit; and
- `git diff --check`: passed.

Runtime isolation tests prove restart durability. Production E2E proves the
durable ACK and recovery behavior across a real Service Worker navigation, but
Chromium does not expose a deterministic API to force the browser to terminate
the worker at the exact ACK-to-reload boundary. That browser-layer limitation
remains a real-device verification item rather than being reported as covered.

## Rollback

There is no backend or database migration.

1. Revert the Phase 6E frontend changes.
2. Rebuild the frontend.
3. Restart Elvern through the repository lifecycle script.

Old short-lived IndexedDB arm records expire on their own and contain no
private payload. Do not clear Library, auth, or user storage as part of this
rollback.

## Future Work

Phase 6E does not implement cross-device opaque revision, Firefox/WebKit
automation, View Plan hidden/dedupe changes, normalized/v2 search, SQLite FTS,
v1 retirement, resource adaptation, virtualization, scheduler expansion, or
pinch policy. See the two Phase 7 audit documents.
