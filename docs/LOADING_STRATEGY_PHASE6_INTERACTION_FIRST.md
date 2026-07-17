# Loading Strategy Phase 6: Interaction First

## Scope

Phase 6 prioritizes an already-running Elvern session, progressive Detail rendering, and bounded card-poster derivative work. It does not change playback protocols, search behavior, poster quality, the iPhone scheduler, or Library relocation algorithms.

## Runtime Connectivity

### Confirmed bug

The previous startup gate rendered its children only while `serviceReachable` was true. A transient runtime health failure could therefore unmount the authenticated application after Library, Detail, or a player was already active. Unmounting discarded React state and could make a connectivity incident look like an application reset.

### Permanent mount latch

The controller now has a one-way `runtimeReady` latch. Once an application response proves that Elvern has entered the normal auth/application flow, runtime connectivity changes do not unmount the application. The gate may place a notice or recovery overlay above the mounted tree, but the tree and its in-memory Query cache remain present.

Healthy runtime state does not poll continuously. Probes resume after an API network failure event, browser `offline`/`online`, a visibility return, Retry, or an active recovery interval.

### Classification heuristic

The probes are sequential and never overlap:

1. `/_elvern/frontend-health` checks the frontend process without the backend proxy.
2. `/health` checks the backend only after the frontend probe succeeds.

The runtime classifications are:

- `internet_offline`: the browser explicitly reports offline. The application stays mounted and shows `No Internet`; no full-screen Oops page is used.
- `frontend_or_vpn_unreachable`: the browser reports online but the frontend process cannot be reached. After the existing 60 second outage window, the VPN/reachability Oops copy is used.
- `backend_unreachable`: the frontend process responds but the backend health endpoint does not. After 60 seconds, the server Oops copy is used.
- `healthy`: both probes succeed. Recovery UI disappears automatically and the fixed 10 second recovery polling stops.

The two exact Oops messages are:

- Server: `Seems like the server has been bamboozled, please try again later.`
- Frontend/VPN: `Elvern could not be reached, check your VPN connection and try again.`

`No Internet` can be swiped upward by 24 px. It returns after 10 seconds while the browser remains offline. It does not block Library, Detail, or playback interaction.

### Offline shell and worker identity

The static bootstrap shell and `offline.html` use the same frontend-then-backend classification. The Service Worker excludes both health endpoints from navigation fallback handling. Registration cleanup recognizes an Elvern worker only when origin, dynamic base32 scope, exact `sw.js` path, and `elvern_worker=offline-shell-v1` marker all match. Unrelated same-origin Service Workers are not unregistered.

## Progressive Detail

When an exact protected Library Query cache contains the selected item, Detail can immediately preview only:

- `id`
- `title`
- `year`
- `source_kind`

Quality rank is intentionally absent from Detail. It remains a Library-card concept.

The Detail sequence is:

1. Commit the Detail route and render its stable structure.
2. Show the safe cached preview when available; otherwise show non-interactive skeleton structure.
3. Fetch authoritative item metadata with an AbortSignal.
4. After metadata arrives, request progress, browser playback capability, desktop capability, and active-session restore independently and in parallel.
5. Keep metadata visible if an auxiliary request fails.
6. Abort obsolete work when the item or route changes.

The progressive shell contains no fake interactive controls. Existing Library return payloads, card instance keys, desktop relocation, and phone/tablet orientation recovery are not rewritten.

Anonymous frontend marks are available only when `VITE_ELVERN_DETAIL_TIMING_ENABLED` is explicitly enabled. Mark names are fixed and contain no title, path, filename, poster URL, username, or query.

## Browser Poster Priority

Non-iPhone Library card images retain native lazy loading and async decode, with `fetchPriority="low"` added as a browser hint. The existing iPhone smart scheduler keeps its admission behavior and is not forced to low priority.

## Poster Derivative Manager

The process-scoped Poster Derivative Manager limits only expensive generation of an uncached card derivative. It does not limit browser downloads of cached images, original poster `FileResponse`, Library JSON, Detail metadata, progress, playback APIs, or media streaming.

Configuration:

- `ELVERN_POSTER_GENERATION_WORKERS=2` (default 2, positive integer, no product maximum or clamp)
- `ELVERN_POSTER_GENERATION_QUEUE_MAX=256` (positive integer)
- `ELVERN_POSTER_PREWARM_ENABLED=true`
- `ELVERN_POSTER_PREWARM_FIRST_ITEMS=12`
- `ELVERN_POSTER_PREWARM_RECENT_ITEMS=6`

Values above the detected CPU count produce one sanitized warning but are not changed automatically.

Manager guarantees:

- warm JPEG/PNG derivative lookup before source image decode;
- bounded queue;
- one generation future per source identity, source mtime/size, width, q97, and algorithm version;
- priority order of requested, normal, then prewarm;
- requested jobs can displace queued prewarm work when full;
- a requested job can promote the same queued derivative;
- normal work receives a fairness opportunity after a requested burst;
- an unstarted, non-prewarm job can be removed when it has no waiters;
- started Pillow jobs finish rather than being force-killed;
- existing atomic save and failure fallback remain authoritative.

The Detail item route enters a named 2 second interaction window before loading metadata. Already-running poster jobs finish, requested poster jobs can run, and queued normal/prewarm dispatch pauses until the window expires. Detail, progress, playback, and desktop APIs never enter this queue.

Prewarm is a background enqueue after a v2 response. It is limited to all Continue Watching items (maximum 6), the current main section's first configured items (default 12), and Recently Added's first configured items (default 6). It uses the current user's card display width and q97, is deduplicated by single-flight, and is first to be dropped under queue pressure.

## Poster Benchmark

The reproducible synthetic benchmark is `scripts/benchmark-poster-derivative-manager.py`. It uses JPEG and alpha PNG inputs at multiple dimensions, 1400 px/q97, cold and warm caches, duplicate concurrent requests, 25/100/500 submission sets, and worker counts 1/2/4/6/8/10. Outputs under `tmp/` are local diagnostics and are ignored by Git.

In the 500-request duplicate-heavy profile, observed throughput was approximately 631, 1005, 1403, 1967, 1654, and 1769 requests/second for workers 1, 2, 4, 6, 8, and 10. There were only six unique derivatives, so active workers correctly peaked at 1, 2, 4, 6, 6, and 6. Most requests collapsed into single-flight or became warm hits.

Across the same profiles, measured process CPU time ranged from 0.85 to 1.18 seconds and peak RSS growth ranged from about 101 to 241 MiB. Single-flight collapsed 454 to 494 of the 500 submissions; the variation at 8 and 10 workers reflects derivatives becoming warm before every duplicate joined the in-flight future. The spacious benchmark queue dropped no prewarm jobs. Separate bounded-queue tests prove that requested work displaces queued prewarm work when pressure is present. The product-default prewarm profile generated 24 unique derivatives in about 1.17 seconds and wrote about 712 KiB.

The latest isolated TestClient profile measured actual Detail/progress/playback endpoints during six concurrent poster generations. Detail p50 additional latency ranged from -0.73 ms to +1.76 ms across worker settings. Negative deltas are normal measurement noise. Progress and playback showed no material queue coupling. These timings are observations, not CI thresholds.

Default 2 remains the conservative product choice: it materially improves throughput over 1 while keeping active CPU and memory pressure below the more aggressive profiles. The benchmark never changes the configured value.

## Library View Plan Timing

`ELVERN_LIBRARY_PLAN_TIMING_ENABLED=false` remains the production default. When enabled, request-local `perf_counter_ns()` spans emit a random opaque correlation ID, fixed stage names, durations, and aggregate counts. They do not emit title, filename, path, poster URL, query, username, or Library payload.

See `LOADING_STRATEGY_PHASE6_VIEW_PLAN_TIMING_AUDIT.md` for measurements and the two parity-protected internal optimizations.

## Validation

- Backend: `1650 passed` with `.venv/bin/python -m pytest backend/tests -q` and again in the fresh CI environment.
- Frontend: `61` files and `784` tests passed with `npm test --prefix frontend` and again in fresh CI.
- Production build passed; the existing large-chunk warning remains informational.
- Chromium desktop passed Root/Local/Cloud v2 Detail return and canonical route checks (`6 passed`, one mobile-only skip).
- Chromium mobile connection recovery passed (`1 passed`, one desktop-only skip).
- Production Service Worker update and offline deep-link tests passed (`2 passed`).
- `npm audit`, both `pip-audit` requirements checks, Bandit, and the full fresh local CI mirror passed.
- Firefox/WebKit and real iPhone, iPad, Android, Windows, and macOS devices were not available in this environment and remain manual validation items.

## Rollback

- Disable prewarm: `ELVERN_POSTER_PREWARM_ENABLED=false`.
- Return poster generation to one worker if resource pressure is observed: `ELVERN_POSTER_GENERATION_WORKERS=1`.
- Keep Library timing disabled: `ELVERN_LIBRARY_PLAN_TIMING_ENABLED=false`.
- Keep frontend Detail marks disabled by omitting or disabling `VITE_ELVERN_DETAIL_TIMING_ENABLED`.
- A code rollback can independently revert the runtime gate, Progressive Detail, manager, or View Plan memoization because no database schema or persisted private cache was added.

## Not Implemented

Phase 6 does not implement a resource adapter, virtualization, a cross-platform smart-poster scheduler, search optimization, v2 search, cross-device revision, v1 retirement, or a pinch-zoom restriction.
