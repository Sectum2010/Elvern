# Loading Strategy Phase 7: Committed Search, Fast Oops, And Revision Sync

Status: implemented locally on 2026-07-19. Full validation and real-device
verification status are reported separately at the end of the implementation
round.

## Scope

Phase 7 contains five deliberately independent changes:

1. harden Phase 6E recovery against a browser-offline race;
2. replace network debounce with explicit committed Library search;
3. classify conclusive connection failures before the 60-second maximum wait;
4. add private, opaque cross-device Library revision synchronization; and
5. add local opt-in Firefox/WebKit production automation.

It does not add v2 search, pagination, virtualization, resource adaptation,
cross-platform poster scheduling, poster quality changes, or playback protocol
changes.

## Recovery And Fast Oops

Every recovery transaction captures both its lifecycle generation and the
monotonic browser-offline evidence generation. It rechecks them before App
Shell verification, after the recovery decision, and before arming or
reloading. An `offline` event invalidates older work immediately, and
`navigator.onLine === false` is always a hard recovery blocker.

The 60-second deadline remains the maximum wait for ambiguous evidence. It is
not imposed on evidence the product can classify conclusively. Structured
health results distinguish HTTP success, HTTP unhealthy, network error,
timeout, abort, and a missing App Shell marker. A candidate must survive two
matching rounds separated by `FAST_OOPS_CONFIRMATION_DELAY_MS = 750` before a
VPN, server, or trusted-public-failure Oops is latched. Browser-explicit offline
at cold start can latch the offline Oops immediately. A running App remains
mounted and continues using the existing No Internet notice.

The Oops state is one-way for the active outage. Later evidence may update the
copy but cannot replay the entry animation. Recovery is a separate verified
transition. The iOS paint-ready unverified path has exactly two bounded clean
rechecks at 500 ms and 1500 ms, plus the existing lifecycle/viewport event
rechecks; generations cancel stale timers.

## Committed Library Search

Search now has separate draft and committed values. Typing changes only the
active input draft. It does not change the URL, query key, displayed result
set, or network request. Enter commits unless IME composition is active.
Explicit Clear commits an empty query. Escape restores the committed value.

Root formal search remains v1 `/api/library/search`. Local and Cloud retain the
v2 base summary and filter locally using the committed value. Back/forward and
external URL changes abandon uncommitted drafts and synchronize both inputs to
the URL. Static and floating inputs do not mirror uncommitted text; one active
draft locks the other until commit, revert, or clear.

Phone and tablet render only the floating search and ignore the user setting
that could disable it. Settings therefore hides that desktop-only row on those
device classes. Desktop keeps both static and floating search and continues to
honor the setting. Floating expansion is controlled only by its own button and
stores one non-private boolean per canonical list path in `sessionStorage`;
search text is never persisted there.

This supersedes the Phase 1 300 ms debounce. Search remains URL-backed and
return-anchor compatible.

## Opaque Cross-Device Revision

The backend flag `ELVERN_LIBRARY_REVISION_ENABLED` defaults to true. The
frontend flag `VITE_ELVERN_LIBRARY_REVISION_MODE` defaults to off and enables
polling only for the exact value `on`.

Authenticated endpoints:

- `GET /api/library/v2/revision`
- `GET /api/library/v2/progress-state`

Both return `Cache-Control: private, no-store` and `Vary: Cookie`. The revision
payload contains HMAC-derived opaque values for `catalog`, `presentation`,
`permission`, `user_overlay`, `progress`, and `combined_library`.
`combined_library` deliberately excludes progress. The HMAC identity includes
user ID, role, and age credential; raw counters and mutation counts are never
sent to the browser.

SQLite counters are updated by triggers in the same authoritative transaction
as each mapped row mutation. A rollback therefore does not publish a new
revision. Multiple row changes can increment an internal counter more than
once, but clients observe only one changed layer token after commit and process
that layer once. The external contract does not expose count magnitude.

Layer mapping:

| Layer | Authoritative changes | Client action |
| --- | --- | --- |
| catalog | media/source/metadata/genre membership | silently invalidate active Library queries |
| presentation | summary-affecting user settings | invalidate settings and Library queries |
| permission | role/age/global visibility/source access | refresh auth and invalidate Library queries |
| user_overlay | per-user hidden item/movie/source | invalidate Library queries |
| progress | progress/completion | fetch minimal progress state and patch existing cached entities |

The progress snapshot applies source ownership/share, hidden source/item/movie
key, global hide, and age-access filters. It contains only item ID and progress
fields. It cannot add missing entities or reorder a list. Ordinary progress
patches existing entities; membership-changing transitions mark active Library
queries stale for silent refresh.

The client performs one baseline request without invalidation, checks at most
once per 60 seconds while visible, pauses while hidden, and checks immediately
on focus, pageshow, visibility return, online recovery, and scan completion.
Requests are single-flight and identity-scoped. Protected cache clearing on
logout, 401/403, or identity change includes revision queries. No revision or
Library payload is written to persistent browser storage.

## Firefox And WebKit Opt-In

The lockfile resolves Playwright 1.60.0. Extra browser installation is explicit:

```bash
npm run playwright:install:extra --prefix frontend
```

The installer invokes that exact local Playwright CLI for Firefox and WebKit,
and reports elapsed time and browser-cache disk growth. Ordinary `npm ci`,
`npm test`, fresh CI, and GitHub Actions do not run it.

Production-server projects use an isolated port, a base32-safe dynamic prefix,
fresh contexts, mocked public probes, retained failure traces/screenshots, and
ignored `tmp/` output:

```bash
npm run test:e2e:firefox --prefix frontend
npm run test:e2e:webkit --prefix frontend
npm run test:e2e:cross-browser --prefix frontend
```

An explicitly requested missing browser or host dependency fails visibly. It
is never silently skipped. Desktop WebKit remains desktop evidence only; it
does not prove iPhone/iPad PWA lifecycle, AutoFill, keyboard accessories,
safe-area changes, Split View, Stage Manager, or physical orientation behavior.

## Rollback

Disable only cross-device polling:

```bash
VITE_ELVERN_LIBRARY_REVISION_MODE=off
```

Emergency-disable the backend capability:

```bash
ELVERN_LIBRARY_REVISION_ENABLED=false
```

These switches do not disable the existing five-minute Library cache,
four-hour garbage-collection window, v1 fallback/search, mutation-local
invalidation, progress patching, scan polling, or relocation behavior.

The committed-search and Fast Oops code paths are source changes rather than
runtime feature flags. Their rollback is the corresponding source revert; do
not disable v1 or change Library v2 mode as a substitute.

## Preserved Contracts

- non-search Root/Local/Cloud remain v2 by default;
- formal Root search remains v1;
- v1 capability fallback remains;
- exact user/view query keys, 5-minute stale time, and 4-hour gc time remain;
- Poster Index, card 1400/q97, quality authority, and letter fallback remain;
- desktop return and iPhone/iPad orientation restore algorithms are unchanged;
- poster context menu, mobile selection guard, and Floating Island remain;
- playback, audio, subtitle, download, and desktop helper protocols are unchanged.

## Validation And Real-Device Limits

Validation covers focused unit/contract tests, full frontend/backend suites,
production build, Chromium production/Service Worker suites, local Firefox and
WebKit projects when host dependencies allow, security audits, diff checks, and
the fresh local CI mirror. Exact command results belong in the final task
report so this document does not become stale when rerun.

Real iPhone/iPad verification is still required for physical orientation,
keyboard/AutoFill, Home Screen PWA update/recovery, and lifecycle timing.
