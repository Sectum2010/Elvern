# Loading Strategy Phase 1: Future-Phase Audit

**Current status / superseded by:** Phase 1 caching and card-poster work is
implemented. Its 300 ms search-debounce design was superseded by Phase 7's
Enter-only committed search. Phase 7B hardens revision, search ownership, and
browser automation; this document remains historical benchmark/design evidence.

This audit is based on the repository state reviewed during Phase 1. It records future work only. Phase 1 does not implement a cross-platform poster scheduler, virtualization, adaptive device/network profiles, or a lightweight backend library API.

## Evidence Labels

- **Confirmed:** directly visible in the current source.
- **Inference:** a likely consequence of the confirmed implementation, but not measured here.
- **Benchmark needed:** cannot be selected safely from source inspection alone.

## A. Cross-Platform Smart Poster Scheduler

### Current platform paths

**Confirmed:** `frontend/src/lib/smartPosterLoading.js` enables the scheduler only when `document.documentElement.dataset.deviceShell === "iphone"` and `IntersectionObserver` exists. `frontend/src/main.jsx` sets that shell marker only for iPhone/iPod user agents.

**Confirmed:** Android phone, Android tablet, iPad, Windows, macOS, and Linux cards do not enter the smart scheduler. They now request the same `variant=card` URL as iPhone, but retain native browser `loading="lazy"` and `decoding="async"`. iPhone scheduler-admitted images use eager loading after admission.

**Confirmed:** `detectClientPlatform()` distinguishes iPhone, iPad, Android, Windows, macOS, and Linux. Android phone/tablet is separated by the `mobile` user-agent marker. This platform detection currently does not enable the scheduler outside iPhone.

### Current iPhone policy

| Policy | Portrait | Landscape |
| --- | ---: | ---: |
| Intersection root margin | `150% 20% 150% 20%` | `60% 8% 60% 8%` |
| Admission tick | 90 ms | 160 ms |
| Orientation settle | 200 ms | 540 ms |
| Scroll idle settle | 140 ms | 140 ms |
| Maximum mounted images | 96 | 36 |
| Idle max in-flight | 8 | 4 |
| Idle new admissions/tick | 4 | 2 |
| Medium max in-flight | 5 | 2 |
| Fast max in-flight | 3 | 1 |
| Very-fast max in-flight | 1 | 1 |

**Confirmed:** both orientations use speed boundaries of 0.45, 1.1, and 1.8 px/ms. The scheduler samples scroll in `requestAnimationFrame` and applies an exponential moving average with alpha 0.58.

**Confirmed:** candidates are ranked visible first, then ahead of the scroll direction, then behind. Mounted images outside the candidate/visible area are detached. Overflow eviction excludes visible cards, prioritizes distant cards, then older `lastSeenAt` values. Landscape additionally prefers evicting behind-scroll candidates.

**Confirmed:** portrait admits visible/near-ahead/near-behind candidates progressively as motion slows. Landscape uses lower concurrency and lower admission counts, a smaller prefetch margin, a longer settle delay, and a lower mounted-image ceiling.

### Actual responsive grid shape

**Confirmed from `frontend/src/styles.css`:**

- Base grid: 1 column.
- 520-859 px: 2 columns.
- 860 px and above: 3 columns.
- Library root/source at 1280 px and above: 6 columns.
- Coarse pointer, 740-1400 px, non-phone: 4 columns.
- Phone class portrait: 1 column.
- Phone class landscape: 3 columns.
- Desktop rails normally expose five slots, phone portrait one, and phone landscape two; rail packing can alter the exact visible group.

**Inference:** a phone viewport commonly exposes about 1-3 poster cards at once in portrait and 3-9 in landscape; a tablet can expose roughly 4-12; desktop can expose roughly 6-18. Card style, title height, rail composition, viewport height, zoom, and browser chrome make these ranges approximate.

### Why iPhone parameters cannot be copied

**Confirmed:** desktop and iPad can have four to six columns, while iPhone portrait has one. The same root margin therefore admits very different card counts. Desktop also has pointer/hover behavior and wide rails; iPad and Android can combine touch, high pixel density, split-screen, and rotation. Browser lazy-loading behavior also differs.

**Inference:** copying iPhone's 96-image portrait ceiling to desktop may retain too many decoded bitmaps, while copying landscape's four-request idle limit may underuse a desktop connection. A single user-agent-derived preset would hide large differences within each platform.

### Required benchmark evidence

Measure per platform and orientation: visible-card count, request concurrency, transfer bytes, decode duration, peak decoded-image memory, admission-to-paint latency, blank-card rate during fast scroll, long frames, detach/reload churn, and restoration accuracy. Include low-memory/high-DPR devices and Safari, Firefox, and Chromium.

### Future interface, not implemented

Introduce a policy provider such as `getPosterLoadingProfile(runtimeMetrics)` returning observer margins, admission cadence, in-flight ceiling, mounted ceiling, and eviction bias. Keep scheduler mechanics platform-neutral and put calibrated profiles behind explicit capability/benchmark gates. Preserve `variant=card`; first adaptation should change concurrency and mounting, not image quality.

## B. Virtual List and Grid Audit

### Mechanisms affected

**Confirmed:** the root library can render search results, sorted grids, Other Movies, Continue Watching, Recently Added, local/cloud Series Rails, and duplicate media IDs in multiple sections. `cardInstanceKey` distinguishes those instances.

**Confirmed:** `libraryNavigation` persists `listPath`, `anchorItemId`, `anchorInstanceKey`, viewport X/Y ratios, `scrollY`, viewport dimensions, `railKey`, and `railScrollLeft`. It stores only relocation metadata in session storage, not the library payload.

**Confirmed:** `viewportAnchor` selects the exact instance, computes viewport-relative correction, restores horizontal rail position, and supports later refinement. LibraryPage has separate orientation/zoom recovery, correction limits, user-interaction cancellation, and stable center-anchor sampling.

**Confirmed:** Series Rails have independent horizontal scrolling, touch release/momentum behavior, desktop dragging, viewport-specific slots, packed rows, and phone-landscape static layouts.

### Future virtualization contract

1. Every rendered collection must expose a stable data index keyed by a composite identity: section/rail key plus media item ID. Item ID alone is insufficient.
2. The relocation target must first map `anchorInstanceKey` to the owning virtual collection and logical index.
3. If the target is not mounted, call the virtualizer's deterministic `scrollToIndex` without smooth behavior.
4. Wait for a committed target DOM node through a virtualizer callback or bounded layout observation, not arbitrary timeouts.
5. Reuse the existing viewport-ratio and rail-left refinement after the node exists.
6. Reordering after background refresh must either preserve a stable anchor transaction or defer correction once the user interacts. It must never replay the old anchor unconditionally.
7. Main-grid and horizontal-rail virtualization need separate coordinate systems and restoration adapters.
8. Dynamic card body height and responsive column changes require measured rows or a proven fixed-height contract. Index arithmetic based only on column count is unsafe.

### Safe rollout order

Do not virtualize Series Rails, Continue Watching, Recently Added, duplicate multi-section items, or phone/tablet orientation-sensitive sections in the first virtualization experiment. Start with a flat desktop sorted/search grid behind a test flag. Expand only after exact-instance return, resize, zoom, and reorder tests pass.

### Required regression coverage

Cover target initially outside the DOM, duplicate IDs in multiple sections, back from detail, stale background reorder, browser back/forward with `q`, horizontal rail restoration, portrait/landscape rotation, zoomed iPad recovery, dynamic title heights, responsive column changes, focus/keyboard navigation, reduced motion, and user scroll cancelling correction. Real iPhone/iPad and Android validation remains mandatory.

## C. Device and Network Monitoring Audit

### Existing signals

**Confirmed:** the repository uses `performance.now()` for scroll/anchor and playback timing, `requestAnimationFrame` for viewport/scroll work, visibility/focus events for auth/playback/viewport recovery, user-agent/platform detection, and local debug keys for smart-poster/viewport and playback diagnostics.

**Confirmed absent from the library/poster path:** no `PerformanceObserver`, image resource timing collector, explicit `img.decode()` timing, long-task/long-animation-frame observer, request-latency profile, `hardwareConcurrency`, `deviceMemory`, Network Information API, `effectiveType`, or `saveData` profile selector.

### Future monitoring design

- Treat platform, DPR, `hardwareConcurrency`, `deviceMemory`, and network API values only as weak hints.
- Prefer runtime measurements: library TTFB, JSON parse/commit time, poster resource duration/bytes, admission-to-load time, decode/paint proxy, rAF frame gaps, blank-card duration, and detach/reload churn.
- Begin in `normal`; downgrade to `conservative` only after a minimum observation window with repeated pressure signals. Reserve `text-first` for sustained severe pressure or explicit user choice.
- Use hysteresis: stricter downgrade threshold, slower upgrade threshold, a cooldown after each change, and minimum sample counts. Never switch profiles on a single slow request.
- Initially adapt only request concurrency, prefetch distance, and mounted-image count. Do not lower card dimensions or JPEG quality.
- Persist only a small local profile record: schema version, profile name, coarse capability bucket, sample counters, and expiry. Never store item IDs, titles, query strings, poster URLs, or library payloads.
- Provide a user-visible diagnostics export containing only aggregate timing/count data and active thresholds.

### Browser caveats

**Confirmed/standards constraint:** Network Information and `deviceMemory` are absent or restricted in Safari and Firefox. Resource Timing may hide transfer size across origins without timing headers. Long Animation Frame support is not universal. Browser cache hits, private mode, background tabs, low-power mode, and iOS memory pressure can distort samples. All probes need feature detection and graceful absence.

## D. Backend Lightweight Library Audit

### Current request work

**Confirmed:** `/api/library` can trigger `maybe_refresh_local_library`, then `list_library` loads user settings and performs a full accessible-media query plus a second continue-watching query. It also queries watch-event aggregates, tracking-event aggregates, genre groups, global hidden IDs/keys, and user hidden IDs/keys. Shared-local-source and app-setting helpers perform additional conditional reads/writes. Therefore there is no fixed single SQL count; static inspection shows at least nine explicit read result sets in the main service path, plus helper work.

**Confirmed:** filtering category/source/genre/quality, duplicate/hidden representative selection, recent sorting, smart sorting, rail creation, and much of dedupe happen in Python after fetching full rows.

**Confirmed:** `/api/library/search` fetches all accessible rows, builds a search index and score for each row in Python, then filters/dedupes/hides/sorts matches. It opens another connection for hidden-ID/key queries.

**Confirmed:** `/library/local` and `/library/cloud` currently request `/api/library` and filter payload items/rails in the frontend. They are not lightweight server-filtered endpoints.

### Poster resolution and repeated serialization

**Confirmed:** each serialized item calls `_poster_url_for_row`, which resolves candidate names and can iterate the poster directory in multiple fallback passes. A found poster is `stat`ed to build a cache token. The same media row can be serialized again in `items`, Series Rails, Continue Watching, and Recently Added.

**Inference:** with many unmatched posters, repeated sorted directory traversal can dominate list latency. The exact cost depends on poster count, filesystem cache, match quality, and repeated appearances.

**Confirmed:** first request to an uncached `variant=card` opens the original with Pillow, applies EXIF transpose, resizes with LANCZOS, and writes an optimized progressive JPEG (`quality=97`, `subsampling=0`) or optimized PNG synchronously in the request. Concurrent first-view card requests can therefore compete for CPU and I/O. Existing cached files avoid that resize path.

### Future direction

Create a metadata-first endpoint returning only fields required to lay out cards and rails, with pagination/cursors or bounded sections. Move source/category/genre/quality filtering and indexed search into database-backed queries. Build a persistent poster index keyed by normalized title/year plus file identity, and store the resolved poster identity/token so list serialization does not scan directories. Serialize a media item once per response and reference it from section membership where practical. Keep original detail responses and card-generation semantics backward compatible during migration.

## E. Cache Invalidation Coverage

| Mutation or event | Phase 1 status | Residual risk |
| --- | --- | --- |
| Library rescan start and scan polling completion | Invalidates centralized library prefix; current scan payload keeps polling | Low |
| User hide/restore | Connected in Detail and Settings | Low |
| Global hide/restore | Connected in Detail and Settings | Low |
| Genre edit | Connected after success | Low |
| Age requirement/link/unlink | Connected after success | Low |
| Detail track metadata refresh | Connected after refreshed detail succeeds | Low |
| Cloud source add, visibility, ownership move, reconnect callback | Connected after successful mutation/callback | Low-medium; asynchronous provider sync may finish later |
| Poster reference and media-library reference save | Connected after success | Low-medium; later scan completion still governs new content |
| Duplicate and Recently Added user settings | Settings event plus library invalidation | Low |
| Poster appearance/width and floating controls | Settings event updates presentation state; library payload not discarded | Medium: immutable card URLs do not encode the per-user width choice |
| Playback progress/completion | Not connected in Phase 1 to avoid playback refactor | Medium: Continue Watching may remain stale for up to stale time or another invalidation |
| Mutations from another browser/session | Relies on five-minute stale time | Medium |
| Server-side/cloud sync completion without a matching client event | Polling covers local scan state; other completion paths are not unified | Medium |
| Future metadata/poster edits added elsewhere | No central mutation event bus yet | Medium |

Future work should add a small library-revision/event contract rather than importing QueryClient invalidation throughout playback and cloud internals. A server revision in list payloads or a scoped frontend event bus would allow safe invalidation without coupling subsystems.

## F. Benchmark Plan

### Matrix

Run 100, 500, 1,000, and 3,000 visible-item libraries on Windows, macOS, Linux, iPhone, iPad, Android phone, and Android tablet. Test portrait/landscape where supported. For posters compare original URLs with card 1400/q97, each with warm cache and fully uncached card generation.

### Measurements

1. `/api/library` and `/api/library/search` TTFB, server CPU, SQL time/query count, poster-resolution time, and response bytes.
2. Browser download, JSON parse, React render, and commit duration.
3. Poster request count, transfer bytes, cache-hit ratio, generation duration, decode/admission-to-paint duration, and peak memory.
4. Scroll rAF frame gaps, long-frame percentage, blank-card duration, and scheduler detach/reload count.
5. Detail-return time to cached content and time to accurate anchor restoration.
6. Orientation restore error in pixels and viewport-ratio delta, including zoomed iPad cases.
7. DOM node count, mounted image count, JS heap where exposed, and process memory from platform tooling.
8. Background stale refresh behavior: content continuity, reorder impact, duplicate restore attempts, and request cancellation.

### Procedure and acceptance

- Use identical datasets and poster files per run; record device, browser/version, viewport, DPR, zoom, network shaping, cold/warm state, and power mode.
- Repeat each cell at least five times; report median, p90, and worst case.
- Capture server and browser timelines together using a correlation ID that contains no private title/path data.
- Define acceptance thresholds before tuning profiles: no full-page loading on exact cache return, no cross-user data exposure, zero wrong-instance restores, bounded blank-card duration, and no material scroll-frame regression.

**Benchmark needed:** concrete scheduler ceilings, virtualization overscan, adaptive-profile thresholds, and backend pagination size must not be chosen until this matrix has representative measurements.
