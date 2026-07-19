# Loading Strategy Phase 7: Firefox And WebKit Automation Audit

Status: audit only. Phase 6E does not install browsers or change CI.

## Confirmed Current State

- `@playwright/test` is pinned through the frontend lockfile at 1.60.0.
- The normal Playwright config defines Chromium desktop and Chromium mobile
  projects only and uses the Vite development server.
- Production configs use `server.mjs`, dynamic URL prefixes, and Chromium for
  Service Worker/offline coverage.
- The local Playwright cache contains Chromium/headless-shell and ffmpeg, but no
  Firefox or WebKit browser bundle.
- GitHub CI and `scripts/elvern-ci-local.sh --fresh` run unit/backend/build and
  security checks; neither currently installs browsers nor runs Playwright.
- Prior Vite-dev Playwright attempts encountered the host's `ENOSPC` file
  watcher limit. Production-server suites avoid that watcher dependency.

## Proposed Projects

### Firefox desktop

Cover Root/Local/Cloud v2, Detail return, canonical trailing slashes, offline
shell, sticky No Internet, Retry/manual service-only recovery, Service Worker
update/recovery arm, desktop poster context menu, and a basic playback route
that does not assert engine-specific media behavior.

### WebKit desktop

Cover static connection shell, fixed offline deadline, automatic/manual
recovery, canonical routes, viewport CSS contracts, basic Login-to-Library,
Detail return, and global background paint-floor coverage.

Desktop WebKit cannot prove iOS AutoFill, Home Screen PWA lifecycle, keyboard
accessory behavior, real safe-area changes, iPad Split View/Stage Manager, or
physical orientation geometry. Those remain real-device requirements.

## Installation And CI Strategy

1. Add a local opt-in repository script that checks the lockfile Playwright
   version and runs `playwright install firefox webkit`; do not auto-download
   during ordinary `npm ci`.
2. Add Firefox production-server tests first because they avoid the known Vite
   watcher pressure and have lower PWA emulation ambiguity.
3. Add WebKit desktop contracts next, explicitly excluding claims about iOS PWA
   and keyboard behavior.
4. Keep Chromium Service Worker tests as the authority for Chromium PWA
   lifecycle; treat cross-engine SW timing as separate evidence.
5. Add CI as an opt-in/required matrix only after local stability. Cache the
   Playwright browser directory using the lockfile hash and pinned Playwright
   version.
6. Record trace, screenshot, and server logs on failure. Never silently skip a
   configured required project.

Browser downloads are large and OS dependencies differ. CI should use the
Playwright-version-matched install command (and documented system-dependency
installation) rather than an unpinned browser package. A local command may
skip with a clear message when an optional browser is absent; a required CI job
must fail with the exact missing browser/install instruction.

## Dynamic Prefix And Service Worker Requirements

All projects must run against a collision-free local production port and an
isolated dynamic prefix. Assertions must cover registration scope, `sw.js`,
`offline.html`, original deep link/query/hash retention, App/Offline marker
headers, one-shot recovery arms, and cache-family isolation. Public probes must
always be route-mocked; CI must never contact Cloudflare, ipify, or httpbin.

Service Worker/PWA tests need a fresh browser context and explicit cleanup so a
previous run cannot provide the controller/cache. Test output belongs under
ignored `tmp/`. Production-server startup should be preferred for these suites;
Vite-dev tests need a documented watcher preflight or a lower-watch strategy
before being made required.

## Recommended Coverage Order

1. Firefox canonical routes and v2 Root/Local/Cloud.
2. Firefox Detail return and context menu.
3. Firefox offline shell, sticky notice, Retry, update, and recovery arm.
4. WebKit static shell/canonical/Login/Library/Detail contracts.
5. WebKit offline deadline and recovery using production server.
6. Cross-engine regression grouping and CI cache.
7. Continue real iPhone/iPad PWA, keyboard, safe-area, Split View, and
   orientation testing outside desktop emulation.

## Risks And Exit Criteria

Firefox and WebKit can differ in Service Worker activation, navigation preload,
IndexedDB transaction scheduling, media support, focus behavior, and timing.
Avoid browser-specific sleeps; assert observable state and use bounded polling.

The future phase is ready for required CI only after repeated clean local runs,
deterministic dynamic-prefix isolation, no external network calls, documented
browser download size/time, trace-backed failure diagnosis, and an explicit
real-device gap statement. None of those automation changes are implemented in
Phase 6E.
