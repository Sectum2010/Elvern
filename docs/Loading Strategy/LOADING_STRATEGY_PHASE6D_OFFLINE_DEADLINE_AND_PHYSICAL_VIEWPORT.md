# Loading Strategy Phase 6D: Offline Deadline And Physical Viewport

## Scope

Phase 6D hardens two narrow failure domains without changing playback, Library
queries, relocation, posters, search, or database behavior:

- the offline document could reload forever on same-host health evidence; and
- iOS could treat a contaminated first layout height as trusted geometry,
  leaving a short route with an unpainted area below the content.

This phase adds a deterministic offline deadline, verified recovery handshake,
public-probe circuit breaker, and a physical paint floor that is deliberately
separate from relocation geometry.

## Confirmed Offline Reload Loop

The old event chain was:

1. The Service Worker returned cached `offline.html` for a failed navigation.
2. The offline document started its 60-second connecting timer.
3. Public Internet remained unavailable, but same-host frontend and backend
   health endpoints still responded.
4. Two local health successes were treated as recovery.
5. `offline.html` reloaded the original deep link.
6. The Service Worker returned `offline.html` again.
7. A new document started a fresh 60-second timer and the cycle repeated.

This was not a timing-only bug. The evidence contract was wrong: same-host
frontend/backend health proves that Elvern processes are reachable; it does not
prove that public Internet has recovered.

## Immutable Document Deadline

Each `offline.html` document records one `documentStartedAt` and derives one
absolute `oopsDeadlineAt = documentStartedAt + 60_000`.

- Before the deadline the visible state is `connecting`.
- At or after the deadline, if recovery has not completed, `oopsLatched` becomes
  true.
- Once latched, the current document remains on Oops until verified recovery.
- Retry does not alter either timestamp, clear the latch, or restart familiar
  animation state.
- A failed automatic or manual recovery transaction returns to the same visible
  state: connecting before the deadline, Oops after the deadline.

A manual browser reload creates a new document and therefore a new deadline.
That is an explicit browser action. Pressing Elvern's Retry button is not a
document reload and cannot buy another 60 seconds.

The static `index.html` shell is presentation-only. It supplies the pre-React
paint, 400 ms reveal delay, 60-second fallback, and familiar motion. It no
longer contains a separate health/public-probe implementation. Once React
mounts, the shared startup controller owns connectivity decisions.

## Verified Four-Layer Recovery

The offline document may leave Oops/connecting only when one recovery
transaction proves all four layers:

1. Public Internet state is `online` from the ordered public probe chain.
2. `/_elvern/frontend-health` succeeds.
3. `/health` succeeds.
4. A network-only same-origin GET of the current deep link succeeds and returns
   `X-Elvern-App-Shell: 1` without `X-Elvern-Offline-Shell: 1`.

Every health request has its own 5-second AbortController timeout. The actual
App Shell verification has a separate 15-second timeout and uses `cache:
no-store`, same-origin credentials, and `Accept: text/html`. The current deep
link, query, and hash remain unchanged.

Frontend `server.mjs` and backend `SpaStaticFiles` add
`X-Elvern-App-Shell: 1` only to the actual SPA document. Service Worker offline
responses are cloned with `X-Elvern-Offline-Shell: 1`. These response markers
prevent cached `offline.html` from being mistaken for the application.

After the four checks pass, `offline.html` sends the controlling Service Worker
a minimal arm message containing only:

- message type and schema version;
- a random nonce; and
- an expiry no more than 15 seconds in the future.

The message contains no path, user, media, Library, auth, or private payload.
The Service Worker binds the arm to the sending client ID and acknowledges the
same nonce through a `MessageChannel` before navigation begins. The page does
not navigate on a missing, rejected, or mismatched acknowledgement. The worker
then consumes the arm once using the replacing offline client identity
(`replacesClientId`, with `clientId` as the non-replacement fallback) and gives
that recovery navigation a 15-second network window. Another tab
cannot consume it. Normal navigations retain the 8-second handoff window. A
failed or expired recovery request returns the offline shell and does not create
a reload loop.

After the acknowledgement, the page performs one real reload of the unchanged
current deep link. A same-URL `location.replace()` is not used because Chromium
may treat it as a no-op. This reload is reachable only after the four evidence
checks and the one-shot Service Worker arm, so local health alone cannot trigger
or repeat it.

## Public Probe Circuit Breaker

Each configured public endpoint has one in-memory state:

- `closed`: normal ordered attempts;
- `open`: skipped until its five-minute cooldown expires; or
- `half_open`: one trial after cooldown.

An endpoint failure is charged only when a later endpoint in that chain
succeeds. That later success proves the client had a usable public path and
makes the earlier endpoint-specific failure meaningful. Three such confirmed
failures open the endpoint for five minutes. A successful half-open trial closes
and resets it; a fallback-confirmed half-open failure reopens it.

When an entire chain fails, no individual endpoint is penalized. The client may
itself be offline, so assigning blame would poison the registry precisely when
recovery needs it. If every endpoint is cooling down, only the endpoint with the
earliest expiry is tried as the bounded half-open candidate.

The privacy contract remains unchanged: anonymous status-only GETs, omitted
credentials, no-store, no referrer, no response body reads, and no public IP or
user/media data stored by Elvern.

## Shared Connectivity Runtime

`frontend/src/lib/connectivityRuntimeCore.js` is the source of truth for:

- deadline and probe timings;
- exact Oops classifications and copy;
- public trust/circuit thresholds;
- verified recovery evidence;
- Service Worker handshake names and App/Offline headers; and
- familiar/status-word constants.

React imports it directly. The production build deterministically stamps the
same runtime into `offline.html` before computing the offline-shell revision.
The static `index.html` deliberately does not run a third probe algorithm.

## Confirmed iOS Initial-Geometry Failure

The prior coordinator could seed trusted layout state from the first observed
`documentElement.clientHeight`. On iOS, that first sample may already be short
because of stale WebKit geometry, keyboard restoration, browser chrome, or PWA
resume state. Once accepted as the baseline, later shrink detection had nothing
larger to compare against and the route could paint only the contaminated
height.

Phase 6D treats the first iOS sample as provisional. A first layout that is at
least 64 px or 8 percent shorter than matching screen/persisted evidence is
`initial_suspicious_shrink`; it is not promoted to trusted layout and the
relocation gate stays closed. A later stable full-height sample can become
trusted normally.

## Bounded Geometry Persistence

The optional local record uses key `elvern_ios_viewport_geometry_v1` and stores
at most 12 records for 24 hours. Restricted/private storage failures are
non-blocking. A record contains only this allowlisted schema:

```text
schema_version
platform                    iphone | ipad
display_mode                browser | standalone
orientation                 portrait | landscape
width_bucket                64 px bucket
screen_width
screen_height
trusted_layout_width
trusted_layout_height
physical_paint_floor_height
updated_at
```

Matching requires the same platform, display mode, orientation, width bucket,
and compatible screen geometry. Unknown fields are discarded during
normalization. No pathname, URL, route, user ID, media ID, Library state,
credential, or device fingerprint is stored.

## Physical Paint Floor Versus Relocation Geometry

Two heights now have separate jobs:

- The physical paint floor protects the visual canvas. It may use bounded
  screen, large-viewport, or matching persisted evidence and is exposed as
  `--app-physical-paint-floor-height`.
- The trusted stable viewport protects anchors and relocation. It is promoted
  only after the existing stable-sampling rules accept it.

The physical floor applies globally to `html`, `body`, `#root`, and the
route-external app backdrop, so short Login, Auth, Detail, and other routes are
painted as well as long Library pages. It is a minimum block size, not a fixed
content height; normal long-page scrolling is unchanged. Fullscreen playback's
existing black override remains intact.

The physical floor is never used as a relocation anchor. Existing
`libraryNavigation`, `viewportAnchor`, `cardInstanceKey`, rail offsets,
orientation restoration, correction counts, desktop tolerance, user-cancel
guards, and poster scheduler parameters were not rewritten. The existing
restore gate remains closed during provisional/suspicious/keyboard/orientation
states and opens only for trusted stable geometry.

## Service Worker Cache And Scope

The Service Worker still caches only the versioned `offline.html`. It does not
cache the SPA index, JavaScript/CSS bundles, API/auth/Library data, posters,
media, user settings, or health responses.

Activation removes old offline revisions only for the current dynamic prefix
scope and the legacy `elvern-shell*` family. It does not delete another active
prefix's current cache or unrelated origin caches. Late network promise
rejections are consumed and all handoff timers are cleared.

## Validation

Focused unit tests cover:

- exact 60-second deadline and permanent latch;
- Retry without deadline reset;
- same-host health without public Internet at both 60 and 120 seconds;
- four-layer recovery evidence;
- closed/open/half-open probe behavior and all-chain failure handling;
- Service Worker 8-second normal and 15-second armed recovery windows;
- one-shot, per-client and expiring recovery arms;
- App/Offline response headers and dynamic-prefix serving;
- first-sample iOS contamination, clean later promotion, persistence expiry and
  record bounds; and
- global physical paint-floor CSS without relocation rewrites.

Local validation completed on 2026-07-18:

- Phase 6D connectivity, Service Worker, iOS geometry, Auth/viewport, static
  shell, and server targets: 14 files, 154 tests passed.
- Dynamic-prefix/backend static route target: 45 tests passed.
- Full frontend Vitest: 68 files, 856 tests passed.
- Full backend pytest: 1,657 tests passed.
- New production Chromium desktop/mobile offline recovery: 2 tests passed.
- Existing Phase 5A production Service Worker/update suite: 2 tests passed.
- Frontend production build passed and stamped offline revision
  `7405c3af2c2e`; the existing large-chunk warning remains.
- Fresh local CI passed, including backend/frontend tests, .NET build, npm
  install/build/audit, both strict pip-audit runs, and Bandit.
- `git diff --check` passed.

The default Vite-dev Playwright suite could not start because this host had
already reached its OS file-watcher limit (`ENOSPC`). No test case ran in that
attempt. Production Service Worker coverage used the non-watching production
server instead. Firefox and WebKit were not run because only Chromium is
installed in the local Playwright browser cache.

All public endpoints in browser tests are mocked; local browser tests do not
send real requests to third-party probe services.

## Real-Device Work Still Required

Source and simulated-browser tests cannot prove all WebKit geometry behavior.
Real validation remains required for:

- iPhone Safari and installed PWA cold launch, resume, keyboard, AutoFill, and
  repeated portrait/landscape cycles;
- iPad Safari/PWA, Split View, Stage Manager, external keyboard, and safe-area
  changes;
- Android installed PWA offline recovery;
- Windows/macOS/Linux normal and same-host offline behavior;
- recovery before 60 seconds, recovery after Oops, and manual Retry; and
- more than five minutes of a failing public endpoint followed by half-open
  recovery.

The 64 px width bucket and screen-geometry tolerance are conservative safety
guards, not proof that every future iPad multitasking layout has been observed.

## Rollback

Phase 6D has no database migration and stores no private payload.

1. Revert the Phase 6D frontend/backend static-response changes and rebuild the
   frontend.
2. Restart Elvern with the repository lifecycle script.
3. If a clean local evidence reset is required, remove only
   `elvern_public_probe_trust_v1` and `elvern_ios_viewport_geometry_v1` from
   browser local storage. Do not clear Library/auth state as part of this phase.

Public probing can still be disabled operationally with
`VITE_ELVERN_PUBLIC_CONNECTIVITY_PROBE_URLS=none` followed by a frontend build.
That removes public evidence and therefore cannot provide verified offline
recovery by itself.

## Not Implemented

Phase 6D does not implement playback/audio/subtitle changes, Library query or
relocation rewrites, poster queue/prewarming or quality changes, search/v2
search, pagination, virtualization, resource detection/adaptation,
cross-platform smart-poster scheduling, cross-device revision, pinch policy,
database schema changes, or deployment-environment changes.

## Phase 6E Correction

Phase 6E found one remaining trust-boundary bug in the Phase 6D settle-timeout
fallback: with no matching persisted geometry, the physical paint floor could
still be passed through the old stable-promotion path. Phase 6E separates
provisional painting from trusted promotion. The paint floor never writes
geometry, dispatches the stable event, opens the restore gate, or enters anchor
math. Only validated persisted geometry and repeated clean layout/live samples
may become trusted.

Phase 6E also preserves strict public-Internet evidence for automatic recovery
while allowing a user-initiated Retry to use verified frontend, backend, and
real App Shell evidence when public probes are blocked or disabled. Explicit
browser offline remains non-bypassable. Recovery arms are now 15-second,
one-shot IndexedDB records instead of worker-process memory, so an ACK remains
valid across worker restart. See
`docs/LOADING_STRATEGY_PHASE6E_TRUST_AND_DURABLE_RECOVERY.md`.
