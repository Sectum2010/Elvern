# Loading Strategy Phase 6B: Stability And Roadmap

## Scope

Phase 6B hardens three existing Phase 6 systems: runtime connectivity evidence,
iOS/iPadOS viewport ownership around authentication, and poster derivative
queue correctness. It does not change playback, Library quality authority,
search behavior, card dimensions, JPEG quality, or existing relocation
algorithms.

## Connectivity Evidence

### Confirmed desktop failure mode

The Phase 6 controller reacted to browser online/offline events, API failures,
visibility return, and recovery polling, but a healthy running desktop did not
probe continuously. Windows, macOS, or Linux can retain an online virtual
adapter after Wi-Fi is disabled. In that state `navigator.onLine` stays true,
no `offline` event is guaranteed, and an idle cached Library produces no API
failure. The controller therefore received no evidence that could show the
runtime `No Internet` notice. A later refresh saw only an unreachable Elvern
origin and conservatively used the VPN message.

### Evidence and order

The shared classifier uses four independent signals:

1. Browser evidence: `navigator.onLine` and online/offline events.
2. Frontend evidence: `/_elvern/frontend-health`.
3. Backend evidence: `/health`, only after frontend success.
4. Public Internet evidence: an operator-configured URL outside Elvern and its
   VPN, supplied at build time as
   `VITE_ELVERN_PUBLIC_CONNECTIVITY_PROBE_URL`.

Classification is deterministic:

- Browser offline, or frontend failure plus two public-probe failures separated
  by a short bounded confirmation window: `internet_offline`.
- Frontend failure plus public-probe success: `frontend_or_vpn_unreachable`.
- Frontend success plus backend failure: `backend_unreachable`.
- Frontend and backend success: `healthy`.

If the public probe is not configured, one privacy-safe warning is emitted and
a frontend failure remains `frontend_or_vpn_unreachable`. Elvern does not claim
to know that the Internet is offline without that evidence.

The exact Oops copy is:

- Offline: `It looks like you're offline. Please check your connection and try again.`
- VPN/origin: `Elvern could not be reached, check your VPN connection and try again.`
- Server: `Seems like the server has been bamboozled, we will fix it as soon as possible.`

Cold start, refresh, and a login request made while truly offline retain the
60-second startup window. Once `runtimeReady` is latched, Internet loss keeps
the React application mounted and uses only the existing swipeable `No
Internet` notice. VPN/origin and backend outages may place the existing Oops
overlay above the mounted application after 60 seconds; neither path clears
Auth, Query cache, Library state, or playback state.

### Desktop watchdog

`DESKTOP_CONNECTIVITY_WATCHDOG_INTERVAL_MS` is 8,000 ms. It runs only on
Windows, macOS, and Linux while the document is visible, runtime is ready, and
classification is healthy. It stops when hidden or in outage, and visibility
return triggers one immediate probe. Outages continue using the existing
10-second recovery interval. All controller probes are single-flight.

### Public probe privacy contract

The public endpoint is chosen and controlled by the operator. It must be
outside the Elvern/VPN path, return an empty 204 or small 200, and allow
anonymous CORS. Elvern does not hardcode a third-party service. Requests use:

- `credentials: "omit"`
- `cache: "no-store"`
- `referrerPolicy: "no-referrer"`
- `mode: "cors"`
- an independent timeout and `AbortController`

No Cookie, Authorization header, username, item ID, media path, Library query,
or Library payload is sent. The same build-time value is stamped into
`index.html` and `offline.html`, so React, bootstrap, and Service Worker
fallback shells classify failures consistently.

## iOS And iPadOS Viewport Coordinator

### Confirmed root cause

Auth inputs inherited a computed size below 16 px. Safari and Home Screen PWA
could apply focus autozoom for manual focus or Apple Passwords/AutoFill. The old
global listener then copied every `visualViewport.height` directly into the App
root, including keyboard-shrunken or stale WebKit values. Login, main startup,
and orientation restore also had separate viewport-meta reset transactions.
That let an Auth viewport leak into Library/Detail as a short root, a bottom
black gap, or competing reset/rotation corrections.

### Single owner and two viewport models

`iosViewportCoordinator.js` is now the only owner of temporary viewport-meta
normalization. It recognizes iPhone/iPod, iPad, and iPad desktop-style identity
(`MacIntel`/Mac plus multiple touch points).

It publishes two models:

- Stable layout viewport: root height, page background, app shell, backdrop,
  and existing restore measurements.
- Live visual viewport: keyboard/overlay height, offsets, and scale.

Stable height is retained separately for portrait and landscape. It is updated
only after consecutive close samples while no editable is focused, no keyboard
is inferred, scale is close to 1, no reset is active, and orientation is not
settling. A bounded fallback uses the layout viewport; it never opens the
restore gate while an editable is still focused.

The coordinator exposes `stable`, `editable_focused`, `keyboard_open`,
`focus_autozoom`, `settling`, and `orientation_changing` state through a single
snapshot and root data attributes. The App root is no longer resized to every
live keyboard viewport.

### Authentication behavior

All iPhone/iPad Auth form inputs under `.login-form`, plus TOTP Setup inputs,
use an actual 16 px font. Labels retain their existing size. Autocomplete,
Apple AutoFill, password managers, and user pinch zoom remain enabled.

When the keyboard is open, Login, New User, Forgot Password, TOTP Challenge,
and TOTP Setup use a top-aligned, vertically scrollable layout with safe-area
padding. Long forms remain reachable instead of staying vertically centered
behind the keyboard.

Focus autozoom requires a short evidence chain: an Auth editable was just
focused, scale rose from near 1, and live height fell materially. An unfocused
user pinch is not normalized. Debug mode is opt-in with
`elvern_ios_viewport_debug=1`; it records only viewport dimensions, offsets,
scale, orientation, state flags, reset generation, and gate state.

### Auth exit transaction and relocation gate

Every successful Auth-to-Library path uses the shared transaction:

1. Blur the active editable.
2. Enter bounded `auth_exit` settling.
3. Wait at least two animation frames.
4. If focus autozoom was observed, perform one scoped normalization after blur.
5. Wait for stable sampling, with a 1.2-second hard bound and safe fallback.
6. Continue navigation even if WebKit times out.

Reset generations prevent an older restore timer from overwriting a newer
viewport transaction. Login's previous mount-time reset was removed, and the
legacy viewport API delegates to the coordinator.

Library's existing anchor, `cardInstanceKey`, correction, rail, and orientation
algorithms remain unchanged. The coordinator only closes their capture/restore
gate while the viewport is unstable. Existing stable anchors and pending return
targets remain intact; one existing restore is scheduled when the stable event
opens the gate.

Real Safari/PWA behavior still requires iPhone and iPad validation. Desktop
WebKit cannot prove iOS keyboard, AutoFill assistant, Home Screen viewport, or
rotation behavior.

## Poster Derivative Correctness

### Priority

Ordinary HTTP `variant=card` requests now enter the manager as `NORMAL`.
`PREWARM` remains internal background work, and `REQUESTED` remains reserved
for an explicit future interactive derivative call. No client query parameter
can promote priority. During the two-second Detail interaction window, already
running work completes, queued NORMAL/PREWARM pauses, and REQUESTED may run.

### Structured results and cache headers

Internal poster results carry a path, disposition, and immutable flag. The
important dispositions are:

- `derivative_cache_hit`
- `derivative_generated`
- `original_already_small`
- `fallback_queue_full`
- `fallback_generation_error`

Generated derivatives, warm derivatives, and tokenized originals already at or
below target width retain `private, max-age=604800, immutable`. Queue-full and
generation-error original fallback uses
`private, no-cache, max-age=0, must-revalidate` plus `Vary: Cookie`, so a failed
card attempt cannot poison that card URL for a week. The next request may retry.

### Bounded metrics

Queue-wait and generation timing samples use `deque(maxlen=2048)`. Lifetime
count/sum/max stays constant-size, while p50/p90 are estimated from the bounded
recent sample. A 100,000-observation regression test confirms both sample
containers remain at 2,048 entries. Snapshots contain aggregate numbers and
dispositions only, never source paths.

### Prewarm lookup count

Before Phase 6B, background prewarm could call poster-path resolution once for
each selected item: up to six Continue Watching, twelve main items, and six
Recently Added items before ID dedupe. That was up to 24 independent item/path
lookups after the v2 response.

The v2 serializer now builds a private request-local poster-path memo while it
creates the existing public poster URLs. It returns the public schema unchanged
and separately supplies a bounded internal candidate list to the background
task. The background task performs zero per-item database/poster-path lookups,
deduplicates by resolved source path, and submits the existing PREWARM jobs.
No private path enters JSON or logs.

## Future Loading Roadmap Audit

The following sections are source-backed audits only. Nothing here is
implemented in Phase 6B.

### A. View Plan hidden/dedupe

Confirmed call path:

`build_library_view_plan` loads all accessible and Continue Watching rows,
decorates and category-filters them, then calls
`_build_visible_representative_context`. If duplicate hiding is enabled, that
function first groups rows by `_dedupe_group_key`, selects the maximum
`_quality_sort_key` representative, preserves the first group position, then
applies global ID/key hiding and per-user ID/key hiding. It finally builds a
visible ID set and representative map used to transfer authoritative Continue
Watching progress to the visible duplicate.

The synthetic 3,000-item/60-percent-overlap benchmark reports 25 SELECT/WITH
statements after Phase 6's safe memoization. The combined hidden/representative
stage is recorded under both timing names and is 3,340.52 ms p50; those two
numbers are aliases and must not be added. Its Python work is nominally linear
in rows plus group members, but each row can also compute normalized movie and
edition identity, making constants significant.

Current hidden tables already have unique/composite keys for `(user_id,
media_item_id)`, `(user_id, movie_key)`, global media ID, and global movie key.
No ordinary SQL index removes the measured Python identity/grouping work.
Future persisted normalized identity or database candidate filtering might
help, but would require a schema/version contract and parity migration.

Semantic hazards are global-before-user hide order, ID versus movie-key hide,
quality-authoritative representative choice, first-position preservation,
edition identity, permission filtering, and Continue Watching progress
transfer. The safe next phase is a dedicated parity corpus and sub-stage
profiler, followed by one isolated algorithm change. It is not a Phase 6B
rewrite.

### B. Cross-device opaque revision

A future endpoint can return only an authenticated, user/role-scoped opaque
Library revision. On focus or visibility return, the frontend would compare
that small value and invalidate exact v2 queries only when it changes. It must
not return titles, IDs, paths, posters, filters, or Library content. Revision
scope must include permission and presentation authorities, and mutation
coverage must be proven before relying on it. This is not implemented.

### C. Normalized and database search

Current formal search remains v1. It loads accessible rows, builds the existing
normalized search index per row, applies `match_search_query`, then uses the
current scorer for final ordering. The safe sequence is first to normalize the
search response contract without changing matching, then add indexed/FTS
candidate retrieval, while retaining the existing scorer as the final ranker
and parity oracle. v2 search and SQLite FTS are not implemented.

### D. Firefox and WebKit automation

Current automated browser coverage is strongest in Chromium. Future Firefox
coverage should exercise canonical routes, cached return, connection notices,
public-probe failure classification, and Service Worker fallback. Desktop
WebKit can cover route, shell, network, and basic viewport contracts, but it is
not evidence for iOS. Real iPhone/iPad Safari and installed PWA remain required
for AutoFill assistant, keyboard dismissal, focus zoom, safe-area, repeated
portrait/landscape rotation, and stale visual viewport behavior.

### E. v1 retirement

Do not remove v1 until v2 search exists, v2 has a sustained stable period,
fallback reasons are measured, rollback is rehearsed, and Firefox/WebKit
coverage is established. Formal search and capability fallback continue to
depend on v1 today.

Also deferred: resource detector/adapter, virtualization, cross-platform smart
poster scheduling, and any pinch-zoom restriction.

## Rollback

- Omit `VITE_ELVERN_PUBLIC_CONNECTIVITY_PROBE_URL` to return frontend failures
  to the conservative VPN/origin classification.
- Revert the Phase 6B frontend coordinator/watchdog changes as one code change;
  there is no schema or private persisted payload.
- Poster prewarm remains independently disableable with
  `ELVERN_POSTER_PREWARM_ENABLED=false`.
- Poster workers and queue retain their existing defaults of 2 and 256.

## Validation Status

This section is completed with the final local validation results for the
Phase 6B change. Timing results are observational, not release thresholds.

- Targeted frontend: 17 files, 192 tests passed.
- Targeted backend poster/library: 50 tests passed.
- Full frontend: 64 files, 806 tests passed.
- Full backend: 1,657 tests passed.
- Production frontend build passed with the existing large-chunk warning.
- Chromium desktop: 4 passed and 4 platform/mode skips; mobile: 2 passed and
  6 desktop/mode skips; explicit v2 Root/Local/Cloud: 3 passed.
- Production Service Worker/offline E2E: 2 passed.
- `npm audit --audit-level=high`, runtime and test `pip-audit`, and Bandit all
  passed with no known vulnerabilities or medium/high Bandit findings.
- Poster and Library Summary v2 benchmarks completed. Results are retained as
  ignored local artifacts under `tmp/`; they are observational and are not
  promoted to release thresholds.
- WebKit automation was not run because the browser is not installed locally.
  Real iPhone/iPad keyboard, AutoFill, Home Screen, rotation, and recovery
  behavior remains a required device-validation item.
- No public connectivity probe is configured in the current build environment.
  The implementation therefore uses the documented conservative VPN/origin
  fallback until an operator supplies a privacy-safe external 204 endpoint.
- The fresh local CI mirror passed, including 1,657 backend tests, 806 frontend
  tests, the .NET helper build, production frontend build, dependency audits,
  Bandit, and repository policy checks.
