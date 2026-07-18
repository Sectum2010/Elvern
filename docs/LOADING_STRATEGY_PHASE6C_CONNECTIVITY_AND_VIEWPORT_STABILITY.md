# Loading Strategy Phase 6C: Connectivity And Viewport Stability

## Scope

Phase 6C fixes two stability failures without changing Library data, poster
generation, playback, or relocation algorithms:

- Desktop Internet evidence could be erased by successful same-host Elvern
  health checks.
- iOS/iPadOS could accept a keyboard-contaminated short layout height as the
  next trusted viewport, leaving Login or Detail with an unpainted bottom area.

## Desktop Same-Host Root Cause

The previous controller stored Internet and Elvern service reachability in one
classification. On Ubuntu, disabling Wi-Fi could emit `offline`, which showed
`No Internet`. Recovery polling then reached the frontend and backend through
the same local host, called the shared healthy path, and cleared the offline
classification. The notice therefore disappeared even though no public path
had recovered.

Phase 6C keeps independent state:

```text
internetState: online | offline | unknown
internetOutageLatched: boolean
publicProbeTrusted: boolean
frontendState: reachable | unreachable | unknown
backendState: reachable | unreachable | unknown
runtimeReady: boolean
serviceReachable: boolean
status: connected | connecting | unreachable
```

The legacy classification is derived for UI compatibility. It is not the
source of truth. A valid same-host snapshot is therefore `internetState =
offline` while both service states are `reachable`.

## Ordered Public Probe Registry

The built-in order is fixed:

1. `cloudflare-trace`: `https://www.cloudflare.com/cdn-cgi/trace`, HTTP 200.
2. `ipify-api64`: `https://api64.ipify.org`, HTTP 200.
3. `httpbin-204`: `https://httpbin.org/status/204`, HTTP 204.

One success stops the chain. One all-failed chain is not enough: Elvern waits
500 ms and runs the ordered chain again. Each endpoint has a 2-second timeout,
and controller cycles are single-flight. The visible desktop watchdog runs a
self-scheduled cycle every 8 seconds and always checks public Internet plus the
frontend and, when the frontend responds, the backend. Recovery checks use 10
seconds where the desktop watchdog is not active.

Operators can set `VITE_ELVERN_PUBLIC_CONNECTIVITY_PROBE_URLS` to a JSON array
or deterministic comma/newline list. The legacy singular
`VITE_ELVERN_PUBLIC_CONNECTIVITY_PROBE_URL` remains supported. Plural wins over
singular, missing values use the three defaults, and `none` disables public
evidence. Only HTTP/HTTPS URLs are accepted, duplicates are removed in order,
and the registry is capped at eight entries.

### Privacy Contract

Public requests are anonymous GETs with `credentials: omit`, `cache:
no-store`, CORS mode, no referrer, and no custom headers. Elvern reads only
response status/ok, elapsed time, and the local endpoint ID. It never calls
`response.text()`, `response.json()`, or `clone()` and never stores or logs a
response body, public IP, Cloudflare trace value, username, media identity,
Library query, cookie, or authorization value.

As with any HTTPS request, the external endpoint can observe ordinary network
metadata such as the source IP. Elvern does not receive that value from the
response body and does not persist it.

## Trust And Sticky Offline Evidence

After any endpoint succeeds, localStorage may contain
`elvern_public_probe_trust_v1` with only:

- `schema_version`
- `endpoint_list_hash`
- `last_successful_at`
- optional `last_successful_endpoint_id`

Trust expires after seven days. An ordered endpoint-list change invalidates
the record. Two failed rounds produce `offline` only for a trusted probe set.
An unverified set produces `unknown`, not a VPN claim. An explicit browser
offline signal can confirm offline immediately.

Confirmed offline sets `internetOutageLatched`. Only a successful public probe
can clear it. Frontend/backend health, localhost, a live VPN adapter,
`navigator.onLine = true`, route changes, cache hits, and background API
success cannot clear it.

At runtime, the application stays mounted and the latch shows `No Internet`.
The notice hides only after public success or an upward 24 px drag on the
notice. A user dismissal lasts 10 seconds and reappears if the latch remains.
`pointercancel` only resets the drag and does not dismiss.

## Oops Copy And Priority

- Offline: `It looks like you're offline. Please check your connection and try again.`
- Public online, frontend/VPN unavailable: `Elvern could not be reached, check your VPN connection and try again.`
- Frontend online, backend unavailable: `Seems like the server has been bamboozled, we will fix it as soon as possible.`
- Insufficient evidence: `Elvern could not be reached at the moment, please check your connection and try again.`

Confirmed runtime Internet loss uses only `No Internet`. VPN, backend, or
generic service failures retain the 60-second runtime overlay. Cold start,
refresh, and offline login retain the full 60-second offline Oops path.

## Static And Offline Shell Recovery

The old static `index.html` remembered any failure and reloaded after a later
local service success. Network-interface changes could repeat this cycle,
reset animations, and keep the tab loading indicator active.

Bootstrap now records `static_only`, `module_bootstrap_started`,
`react_started`, and `runtime_ready`. Normal `index.html` never reloads from a
health probe. Once module or React bootstrap starts, the static shell can only
be cleaned up by React.

Phase 6D supersedes the original Phase 6C recovery rule. Two consecutive local
frontend+backend successes were not sufficient evidence because a same-host
Elvern installation can remain reachable while public Internet is offline. That
rule could reload the offline document, reset its timer, and repeat forever.

The current offline document never reloads from local health alone. Recovery
requires public Internet evidence, frontend health, backend health, and a
network-only request that returns the online App Shell marker. Retry starts the
same recovery transaction but does not restart the 60-second deadline or clear
an Oops state. See
`docs/LOADING_STRATEGY_PHASE6D_OFFLINE_DEADLINE_AND_PHYSICAL_VIEWPORT.md` for the
authoritative recovery contract.

Normal motion retains running familiar idle frames, seven-second familiar/word
rotation, and per-letter wave animation. Reduced motion keeps one visible
static familiar and word. Optional `elvern_connection_shell_debug=1` logs only
  motion mode, bootstrap/shell state, and endpoint ID/status/timing; it logs no
  response body or user data.

## iOS/iPadOS Trusted Viewport

Before an Auth editable receives focus, the coordinator captures an in-memory
trusted viewport with orientation, width, height, scale, offset, timestamp,
and standalone state. Portrait and landscape trusted heights are separate.

After blur it enters a 700 ms post-keyboard quarantine. Stable acceptance is
blocked until no editable is active, scale is near 1, offsets are near zero,
orientation is stable, and consecutive layout/live measurements agree. During
Auth contamination, a same-orientation candidate is suspicious when it is at
least 64 px or 8 percent shorter than the pre-focus baseline. A standalone PWA
does not accept any same-orientation Auth shrink. The bounded settle fallback
uses the pre-focus trusted baseline rather than the current short layout and
allows navigation to continue within the approximately 1.5-second bound.

Outside Auth contamination, Safari can still accept legitimate browser-chrome
height changes after strict stable sampling. User pinch remains untouched;
normalization is still limited to observed Auth autozoom.

Auth exit is one coalesced promise per focus interaction. Duplicate explicit
and redirect-hook calls share that promise. The coordinator remains the sole
temporary viewport-meta owner, and reset generations prevent old timers from
restoring stale metadata.

## Paint Floor And Relocation Gate

`#elvern-app-paint-floor` is a fixed, pointer-free element before `#root`.
`--app-paint-viewport-height` follows the trusted stable height and uses
`100lvh` as an additional supported floor. It uses the existing app background
variable, so solid, gradient, preset, and photo modes are not duplicated.
Login, Detail, root canvas, and safe-area bottom therefore stay painted even
when a live viewport is temporarily short.

The restore gate now remains closed during editable focus, keyboard open,
focus autozoom, post-keyboard quarantine, suspicious shrink, settling, and
orientation change. Existing anchors, `cardInstanceKey`, rail offsets,
correction count, desktop tolerance, user-cancel behavior, and iPhone poster
scheduler values were not rewritten.

## Rollback

- Disable public evidence without removing service health checks:
  `VITE_ELVERN_PUBLIC_CONNECTIVITY_PROBE_URLS=none` and rebuild the frontend.
- Restore a private operator probe set by supplying the plural variable, or use
  the legacy singular variable during migration.
- A code rollback can revert the Phase 6C controller/static-shell and viewport
  changes independently. There is no database migration or private persisted
  payload. Remove only the two non-private trust/cooldown storage records if a
  clean evidence reset is required.

## Validation And Device Work

Unit coverage includes ordered fallback, status-only response handling,
double-round trust, same-host sticky outage, notification dismissal,
quarantine, suspicious shrink, per-orientation trust, standalone behavior,
Safari chrome changes, paint floor, and coalesced Auth exit. Browser coverage
mocks all public endpoints; CI never calls the real third parties.

Local validation completed on 2026-07-17:

- Phase 6C and surrounding frontend targets: 16 files, 192 tests passed.
- Full frontend Vitest: 65 files, 832 tests passed.
- Poster derivative/index and Library v2 backend regression: 69 tests passed.
- Full backend pytest: 1,657 tests passed.
- Chromium desktop/mobile connection-shell coverage: 4 applicable tests passed;
  4 project-inapplicable cases skipped by the existing project matrix.
- Production Service Worker/offline-shell coverage: 2 tests passed.
- Frontend production build and the fresh local CI mirror passed.
- `npm audit --audit-level=high`, both strict `pip-audit` runs, Bandit, and
  `git diff --check` passed. The existing Vite large-chunk warning remains.
- Firefox and WebKit Playwright projects were not run because only Chromium is
  installed in the local Playwright browser cache. This is reported, not
  treated as iOS proof.

Real iPhone/iPad Safari and installed-PWA validation remains required for
Apple Passwords/AutoFill, repeated portrait/landscape changes, split view,
safe-area painting, and stale WebKit viewport reports. Desktop validation must
also cover Ubuntu same-host Wi-Fi loss, VPN-only loss, backend-only loss, and
all public probes unavailable.

## Not Implemented

Phase 6C does not implement View Plan hidden/dedupe rewrites, cross-device
revision, normalized/v2 search, SQLite FTS, v1 retirement, a resource adapter,
virtualization, cross-platform scheduler expansion, or pinch restrictions.
