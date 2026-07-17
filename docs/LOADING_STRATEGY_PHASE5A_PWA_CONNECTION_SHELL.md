# Loading Strategy Phase 5A: Canonical SPA Routes and Connection Recovery

## Status and scope

Phase 5A standardizes Elvern SPA URLs, fixes the duplicate Library header at trailing-slash routes, and adds a privacy-safe startup/offline recovery shell. It does not alter Library v2 data behavior, poster quality, relocation, playback, audio, subtitles, or media caching.

Non-search Root, Local, and Cloud Library views remain on v2 by default. Formal search remains on v1, and the existing v1 capability fallback remains available.

## Confirmed `/library/` root cause

React Router matched both `/library` and `/library/` to `LibraryPage`, but `ShellLayout` previously used strict pathname comparisons. `/library/` therefore missed the `app-shell--library-root` class and exposed the old Shell header while `LibraryPage` rendered its own desktop hero. The result was two Elvern headers and the wrong desktop layout.

The obsolete Shell-only Library header was removed. Library root/source/detail classification now uses the shared canonical helper, so `/library/` cannot enter a structurally different layout before its URL is replaced.

## Canonical SPA route contract

`frontend/src/lib/canonicalSpaPath.js` owns path normalization and Library route classification.

- `/` and a dynamic prefix root such as `/abc23456/` remain unchanged.
- Known SPA routes remove all trailing slashes: `/library/` becomes `/library`, `/library/local/` becomes `/library/local`, and `/library/42/` becomes `/library/42`.
- Query strings, hashes, `history.state`, and replace semantics are preserved.
- `/api/*`, `/health`, `sw.js`, manifests, icons, and asset paths are not canonicalized as SPA routes.
- `main.jsx` performs synchronous `history.replaceState()` before the first React render.
- `CanonicalSpaRouteGuard` returns no route content while replacing a later non-canonical client location, preventing a visible wrong-layout frame.
- `libraryNavigation.normalizeLibraryListPath()` preserves exact Local/Cloud list identity and existing return payload fields.

The BrowserRouter basename is detected from the existing 8-24 character base32-safe prefix. Canonicalization removes only the route suffix slash; it never removes the prefix.

## Dark-fantasy connection shell

`frontend/index.html` contains critical inline CSS and markup before the React root, so the first painted frame does not wait for JavaScript or bundled CSS. The initial `html`, `body`, and overlay backgrounds are `#080b12`.

The original local pixel familiars are:

- a raven;
- a cold-light wisp;
- a horned dungeon familiar.

They use inline SVG with crisp pixel edges and discrete familiar motion. They are intentionally unframed: the familiar stage has no border, background, or shadow. The waiting text moves smoothly left-to-right and back, following the latest product direction. Reduced-motion users receive a fixed raven, fixed text, and no sprite or text animation.

The rotating words are:

- `Flibbertigibbeting...`
- `Ruminating...`
- `Conjuring...`
- `Recombobulating...`

The familiar and word change every 7 seconds. The shell uses `role="status"`, `aria-live="polite"`, hides decorative SVG from assistive technology, and provides a visible keyboard focus state for Retry.

## Startup state machine

Named production constants are:

- `STARTUP_UNREACHABLE_DELAY_MS = 60_000`
- `STARTUP_HEALTH_PROBE_INTERVAL_MS = 10_000`
- `STARTUP_HEALTH_PROBE_TIMEOUT_MS = 5_000`

The states are `connecting`, `connected`, and `unreachable`.

Cold startup begins in `connecting` and never displays Oops before the original 60-second deadline. Offline events and failed probes do not shorten or reset that deadline. Only one health probe can be in flight. Online and visible-page events request an immediate probe without creating another interval.

Startup uses two readiness phases:

1. A successful root `/health` response proves the service is reachable and permits `AuthProvider` to mount.
2. Any actual HTTP response from the application, including 401, 403, or maintenance 503, proves the application responded and removes the shell.

A network exception is different: it returns the controller to connection recovery. This prevents a network failure from being mistaken for an expired session and prevents a successful health probe from hiding an unresolved application outage.

The exact unreachable copy is:

```text
Oops!
Elvern could not be reached. Check your connection and try again.
```

Retry is a semantic button rendered as muted text with no background, border, radius, card, or shadow. It probes immediately and leaves Oops visible on failure. A later successful probe/remounted Auth request enters Elvern automatically.

The static pre-bundle bootstrap has the same 60-second timer, 10-second probes, Retry, online/visibility handling, and cleanup. React cancels and aborts that bootstrap before starting the central controller, so the two implementations do not leave duplicate timers or probes.

## Auth and product-error boundaries

- HTTP 401 follows the existing Login flow.
- HTTP 403 follows the existing disabled-account or permission behavior.
- The exact maintenance 503 follows the existing maintenance notice.
- A v2 capability fallback, a failed poster, or another scoped product error does not open the global Oops shell.
- The startup gate does not clear users or protected Library caches.

## Minimal Service Worker

`frontend/public/sw.js` replaces the legacy self-unregistering worker. Registration uses `document.baseURI`, so a prefix such as `/abc23456/` registers `/abc23456/sw.js` with scope `/abc23456/`.

The versioned cache contains only:

- `/&lt;prefix&gt;/offline.html`

It explicitly does not cache:

- `index.html` or React/CSS bundles;
- `/api/*` or `/health`;
- auth or user-settings responses;
- Library or Detail payloads;
- posters, thumbnails, icons used by private content, or media;
- cookies, credentials, or browser storage.

The worker handles only same-origin GET navigation requests. It tries the network first and hands off to cached `offline.html` after 3.5 seconds or a network failure. API, health, manifest, worker, asset, and icon requests are outside this handler. The requested deep-link URL stays in the address bar.

Activation removes the old `elvern-shell*` family and obsolete `elvern-offline-shell-*` versions, while preserving unrelated origin caches. It then claims clients. Registration failure logs one controlled warning and never blocks online Elvern.

`offline.html` is standalone and contains inline CSS, familiars, timing, probes, Retry, and recovery. It reloads the same deep link after health recovers. It does not inspect cookies or private browser storage.

## Dynamic prefix and first-install limits

The existing injected `<base href="/&lt;prefix&gt;/">` remains the source of SPA asset and worker scope identity. The dynamic manifest endpoint continues to rewrite `start_url`, `scope`, icon paths, and shortcuts under the active prefix. Root `/health` remains an intentional unprefixed frontend proxy route.

A Service Worker can only help after the browser has loaded Elvern online and installed the worker/offline page at least once. A first-ever visit while fully offline can still show Safari's or the browser's native error page. Browser storage eviction, private browsing, OS PWA policy, and WebKit lifecycle rules can also remove or bypass the fallback.

## Validation

Completed before final CI:

- Phase 5A and affected Library/Auth/restore targeted Vitest: 22 files, 251 tests passed.
- Full frontend Vitest: 59 files, 761 tests passed.
- Backend prefix/static/smoke/security subset: 102 tests passed.
- Chromium Phase 5A desktop/mobile: 2 passed, 2 project-specific skips.
- Production Service Worker deep-link fallback: 1 passed.
- Existing desktop return restore: 2 passed.
- v2-on Root/Local/Cloud return restore: 3 passed.
- Production build passed with the existing large-chunk warning.
- Full backend pytest: 1,632 tests passed with one upstream Starlette deprecation warning.
- Fresh local CI passed, including the backend/frontend suites, .NET helper build, production frontend build, `pip-audit`, the repository Bandit gate, and `npm audit`.
- Rendered inspection at 375, 768, 1024, and 1440 pixels found no horizontal overflow; familiar frames were absent and the text animation was active.

## Real-device verification checklist

Verify iPhone Safari, iPhone Home Screen PWA, iPad PWA, Android Chrome/PWA, Windows, macOS Safari, and Linux Firefox/Chromium for:

- normal, slow, and restored startup;
- Wi-Fi present while Elvern or Tailscale is unreachable;
- frontend available with backend stopped;
- already-installed PWA launched fully offline;
- first-ever offline visit and its documented platform limitation;
- recovery before 60 seconds and after Oops;
- Retry, keyboard focus, reduced motion, portrait/landscape, and deep-link reload;
- `/library/` replacement and one desktop Library hero;
- Login, disabled-account, and maintenance behavior.

## Rollback

Restore the previous frontend/SW release, rebuild, and restart through the normal lifecycle script:

```bash
cd /home/sectum/Projects/Elvern
npm run build --prefix frontend
./scripts/elvern-restart.sh
```

The next old/new worker activation clears obsolete Elvern offline cache versions. Do not delete unrelated origin caches.

## Explicitly not implemented

- poster derivative queue or prewarming
- Library View Plan optimization
- cross-device revision coordination
- search optimization or v2 search
- pagination or virtualization
- resource detection/adaptation
- cross-platform scheduler expansion
- pinch-zoom restrictions
- poster width or JPEG quality changes
- playback, audio, or subtitle changes
