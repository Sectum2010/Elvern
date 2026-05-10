# Codex Engineering Handbook

This handbook governs future Codex work in Elvern. It is broader than refactor guidance: use it when adding code, choosing files, creating modules, deciding whether a slice is worth doing, deciding whether deadcheck is required, and deciding when to pause.

For core-path executable checks, also use `docs/CODEX_CORE_GUARDRAILS.md`.

For hard-won playback and platform regressions, also use `docs/PLAYBACK_REGRESSION_NOTES.md`. If a task disproves an early playback hypothesis, depends on live-device evidence, or fixes a high-regression-risk platform path, add or update a note there before calling the slice complete.

## Request flow in deployed Elvern

Production traffic flows through two Python/Node processes, not one:

```text
Public internet
    ↓
Tailscale Funnel edge
    ↓
frontend/server.mjs (Node, port 4173)
    ↓ (only /api/* and /health proxied)
backend uvicorn (Python, port 8000)
```

`server.mjs` is a reverse proxy and static file server. By default it only
forwards `/api/*` and `/health` to backend. Everything else is served directly
from `frontend/dist/`.

**Implication for any feature that wants dynamic backend response on a non-API
path:**

- backend FastAPI middleware/route alone is not enough.
- `server.mjs` must be updated to proxy that specific path to backend.
- Example: dynamic manifest serving (dvmnbcbw bug, May 2026) had correct
  backend middleware, but `server.mjs` intercepted `/<prefix>/manifest.webmanifest`
  and served the static dist file.

**Checklist before adding any new dynamic-content backend route:**

1. `cat frontend/server.mjs` and read the routing logic.
2. If the new path is not `/api/*` or `/health`, `server.mjs` will hijack it.
3. Add an explicit proxy rule in `server.mjs` for that path.
4. Test with `curl` against the public URL going through `server.mjs`, not just
   `localhost:8000` bypassing it.
5. The bug only shows up in the public path. Always test there.

## 1. Project Thesis And Boundaries

Elvern is a private media control plane. Its core job is to organize a private library and hand media off safely to the right playback path.

Core product paths include:

- Library browse/search/detail.
- Mobile/browser Lite and Full playback.
- Native VLC and Infuse handoff.
- Desktop VLC / external-player helper handoff.
- Cloud library connection, source management, sync, and streaming where it supports the private library.

Elvern is not:

- A Jellyfin/Plex-style server-transcode product.
- A broad media server rewrite project.
- A place to grow large feature frameworks without a clear product owner.
- A pile-up zone where every new behavior is added to the nearest large file.

Do not drift into product expansion while doing decomposition. A cleanup task should make an existing product path safer to own, not quietly change the product thesis.

## 2. Ownership Rules

A real owner/domain has a stable reason to exist. It owns one kind of product behavior or runtime policy.

Good owner examples from this repo:

- `library_movie_identity_service.py`: movie identity, edition labels, quality ranking, duplicate resolution.
- `library_presentation_service.py`: poster resolution, source labels, media-item serialization.
- `library_home_curation_service.py`: continue-watching and series-rail assembly.
- `library_hidden_service.py`: user/global hidden policy and mutations.
- `db_hidden_movie_keys.py`: hidden movie-key persistence and backfill.
- `desktop_playback_protocol_service.py`: deterministic desktop VLC target/protocol/playlist construction.
- `cloud_provider_auth_service.py`: Google Drive auth/connect/callback/token orchestration.
- `cloud_source_sync_service.py`: cloud sync, media upsert, metadata refresh.
- `cloud_stream_access_service.py`: cloud stream access and provider access checks.
- `mobile_playback_route2_snapshot.py`: Route2 state projection for frontend payloads.

Rules:

- One slice gets one owner. Do not move two domains because they are near each other.
- Name the owner before editing. If the owner cannot be named clearly, inspect more or stop.
- Preserve existing facades when routes and callers already depend on them.
- Do not move a function just because a file is large. Move it because it belongs to the new owner.
- If a slice requires changing routes, schemas, frontend, DB, and service logic together, it is not a narrow ownership slice.

## 3. Orchestrator Rules

Orchestrators are allowed to stay larger than domain modules when they coordinate runtime flow.

An orchestrator may own:

- Route-facing function names and payload boundaries.
- Call ordering between domain modules.
- Transaction or lifecycle sequencing.
- Cross-domain composition.
- Compatibility wrappers for existing imports.

An orchestrator should not own:

- Pure ranking/scoring policy.
- Serialization/presentation details.
- Deterministic URL/protocol/playlist construction.
- Provider-specific auth internals.
- Hidden/persistence policy that can be isolated cleanly.

Repo examples:

- `library_service.py` should remain the browse/search/detail orchestration shell after identity, presentation, home curation, and hidden policy moved out.
- `cloud_library_service.py` should remain the facade while provider auth, source CRUD, sync, and stream access live in domain modules.
- `mobile_playback_service.py` still owns dangerous runtime orchestration where authority, workers, sessions, and route-facing lifecycle are tightly coupled.
- `desktop_playback_service.py` should remain the desktop playback orchestrator while deterministic protocol construction lives elsewhere.

## 4. Rules For Adding New Code

Before adding code to an existing file, answer:

- What owner does this behavior belong to?
- Is that owner already represented by a domain module?
- Is the target file an orchestrator or a domain module?
- Will this make a route-facing/runtime core file harder to reason about?
- What deadcheck or targeted verification will prove the path still works?

It is acceptable to extend an existing large file only when:

- The behavior is orchestration glue that coordinates existing owners.
- The behavior is route-facing compatibility that must stay at the facade.
- The behavior is tightly coupled to lifecycle/process state and extracting it would be riskier than leaving it.
- The change is a small bug fix and creating a new module would hide the fix.

A new module is usually required when:

- The behavior has a reusable, nameable product domain.
- The behavior is deterministic/pure enough to verify with pre/post semantic comparisons.
- The behavior would otherwise add more policy to an already-large orchestrator.
- The behavior can be wired through an existing facade with no route/schema/frontend contract changes.

Do not add code to a file just because it is the file you are already editing.

## Password Hashing

Elvern stores new user passwords as Argon2id PHC strings. Legacy
`pbkdf2_sha256` hashes remain readable only for zero-downtime migration: on a
successful login, the auth flow persists a fresh Argon2id hash for that user.

Argon2id parameters are configured as an all-or-none set. If
`ELVERN_ARGON2_TIME_COST`, `ELVERN_ARGON2_MEMORY_COST`, and
`ELVERN_ARGON2_PARALLELISM` are all set, Elvern uses those exact values and
skips calibration. If all three are unset, Elvern calibrates parameters for the
current hardware and stores the result in `backend/data/argon2_calibration.json`.
Partial configuration is invalid.

`verify_password` must stay side-effect free: it may return a replacement hash
for callers to persist, but it must not write to the database itself. It must
also fail closed without raising on malformed input. Any change touching
`backend/app/security.py`, Argon2id calibration, or password-hash migration must
run the full password-security test file:

```bash
python -m pytest backend/tests/test_security.py -v
```

## Authentication And Rate Limiting

Elvern login throttling is SQLite-backed and survives backend restarts. The
login route owns two `SqliteRateLimiter` instances: IP buckets allow 10 failures
per 5 minutes before a 600 second lockout, and username buckets allow 50
failures per hour before a 600 second lockout. Checks must read SQLite directly;
do not add in-memory limiter decision caches.

`login_failures` is the short-lived limiter working table and is pruned after
7 days. `security_events` is the long-lived security audit stream and should not
be pruned by limiter cleanup. Keep it separate from the existing `audit_logs`
schema.

Browser auth sessions are idle-timeout based. `last_seen_at` is updated on every
valid request, `ELVERN_SESSION_IDLE_TIMEOUT_HOURS` defaults to 24 hours, and
`ELVERN_SESSION_TTL_HOURS` is only a 1 year safety cap. The
`session_idle_timeout_v1` migration intentionally clears existing sessions so
all users log in again after deployment.

Any change touching `backend/app/auth.py`, login throttling, or auth-session
lifetime must run:

```bash
python -m pytest backend/tests/test_rate_limiter.py -v
python -m pytest backend/tests/test_auth_sessions.py -v
```

## External HTTP Requests

Google API and Google Drive media calls must use
`backend/app/services/_safe_http.py`. Do not directly import or call
`urllib.request.urlopen` for Google API work; the safe wrapper validates the
initial host and every redirect target against the Google host allowlist.

Before adding a new allowed host suffix, stop and check the SSRF risk. The
allowlist should stay limited to Google-owned API, OAuth, static, and Drive CDN
hosts that Elvern actually needs.

## 5. Rules For Creating New Files

Create a new file when it gives a real owner a home.

A good new file:

- Has a domain name, not a vague utility name.
- Has one reason to change.
- Can be verified independently or through a thin facade.
- Does not require broad caller churn.
- Makes future additions less likely to pile into an orchestrator.

Do not create a new file when:

- The only reason is to reduce line count.
- The module would hold one tiny helper with no real owner.
- The new file needs a third shared utility file just to avoid making a decision.
- The extraction would hide lifecycle/process behavior behind a misleadingly “safe” name.

Tiny adapter rule:

- A thin facade import/wrapper is acceptable when preserving existing call sites.
- A new shared util file is not acceptable unless the shared owner is clear and the task explicitly allows it.
- If a helper belongs to two domains, default to keeping it in the facade until a cleaner boundary exists.

## 6. Runtime-Core Safety Rules

Prepared is not playable.

These are not enough by themselves:

- Session creation succeeded.
- A route returned `200`.
- Required URLs are present.
- Backend readiness flags are true.
- Manifest/init/segment files exist.
- Compile/build passed.

Runtime-core changes need evidence at the level users feel.

Extra caution is required for:

- Worker/process lifecycle.
- FFmpeg/VLC/helper process launch.
- Active playback authority and replacement state.
- Session registries and cleanup loops.
- Filesystem publication or deletion of active playback artifacts.
- Browser media attach/source lifecycle.
- Native stream token/range behavior.

For these areas, deadcheck is necessary but may not be sufficient. Add targeted verification for the exact lifecycle being touched.

## 7. Deadcheck Trigger Rules

Use `docs/CODEX_CORE_GUARDRAILS.md` for exact commands and core-path matrix.

Backend deadcheck is required for changes touching:

- Library detail payloads and browse/search data feeding Detail.
- Mobile/browser playback backend routes or services.
- Native VLC/Infuse handoff contracts.
- Desktop VLC handoff contracts.
- Services that feed those paths.

Browser/runtime deadcheck is required for changes touching:

- Detail page rendering.
- Frontend playback hooks/components.
- Browser/mobile playback source attach behavior.
- Backend changes that can affect mobile Lite/Full playable behavior.

Backend deadcheck proves:

- API contract sanity.
- Detail payload field sanity.
- Mobile session creation/payload sanity.
- Native VLC/Infuse stream contract sanity.
- Desktop VLC helper handoff create/resolve sanity.

Browser/runtime deadcheck proves more:

- Detail page renders rather than blanking.
- Lite/Full pass beyond preparation.
- A playable-ready state is reached.
- `currentTime` advances.
- Immediate source reset/remount thrash is not observed.
- Obvious playback-lifecycle jitter is not observed.

Deadcheck does not prove:

- Real desktop VLC GUI launch works.
- Real helper app launch works.
- Every OS path mapping opens in VLC.
- Long-running playback remains healthy.
- Infuse app startup behavior is optimal for every item/container/path class.
- Cloud provider APIs are healthy outside exercised smoke paths.

## 8. Refactor Stop Rules

Pause a mainline when:

- Remaining candidates are only micro-slices with low cleanup value.
- The next meaningful slice crosses lifecycle/process/state-machine red zones.
- Verification cannot prove the user-visible behavior at risk.
- The only argument left is “the file is still large.”
- A third file, route contract, schema, frontend, or DB change becomes necessary but was not in scope.
- The slice would combine multiple owners.

Micro-slice warning signs:

- Moving one logging helper without changing ownership clarity.
- Moving one wrapper that callers barely use.
- Extracting a name but not reducing conceptual load.
- Creating a file that future work would not naturally extend.

Red-zone warning signs:

- Authority/promotion/replacement state moves with worker lifecycle.
- Process launch moves with command construction and monitoring.
- Cleanup/deletion moves with active-session ownership.
- Route-facing methods move together with domain policy.

When a stop rule triggers, report it. Do not force momentum.

## 9. Reporting Rules For Future Codex Work

## URL prefix obfuscation

Elvern serves the browser SPA under a random base32 path prefix such as
`/h2k7m4p9/`. The goal is to keep generic scanners from finding known app
paths like `/admin` or `/login` by accident. This is only an added obscurity
layer; it does not replace authentication, session checks, rate limiting, or
server-side authorization.

The prefix can come from four places:

- First start with no `ELVERN_URL_PREFIX`: generate and persist
  `backend/data/url_prefix_state.json`.
- Manual env override: set `ELVERN_URL_PREFIX` to a valid 8-24 character
  safe base32 value.
- Admin Security panel rotation: generate a new prefix and revoke all sessions.
- CLI rotation: `python -m backend.app.cli rotate-url-prefix`, also revoking all
  sessions.

`ELVERN_FORCE_NEW_URL_PREFIX=1` is an emergency manual operation only. Never add
it to a default systemd unit, default docker compose file, or generated env
example as an enabled value. Prefix age reminders are soft 180-day prompts by
default. Every rotation revokes all auth sessions.

### URL prefix is server-only state

The prefix lives in `backend/data/url_prefix_state.json` and must NEVER be
committed to git. If you ever see it in `git status` as a tracked file, run:

```bash
git rm --cached backend/data/url_prefix_state.json
echo "backend/data/url_prefix_state.json" >> .gitignore
```

### Insider-only archives stay out of git

ZIPs or other archives made for private inspection, local handoff, unpublished
security notes, or insider-only analysis must be ignored. They often contain
clean source snapshots plus sensitive context that is not meant for GitHub. If
one appears in the repo, add a specific `.gitignore` entry for that archive and,
if it is already tracked, remove only the git index entry while keeping the
local file.

## Two-factor authentication

Admin accounts are softly required to enroll in TOTP. An admin without TOTP is
sent to `/setup/totp` after login unless they use the low-emphasis "Skip for
now" action; a skip lasts 30 days, then the prompt returns. Standard users may
enable 2FA, but are not required to.

TOTP secrets are stored in the users table in plaintext for now; backup
encryption is the future place to harden that storage. Recovery codes are shown
once, generated as 10 one-time `elvn-...` values, and stored only as SHA-256
hashes. Login challenges expire after 5 minutes. `totp_last_used_window`
prevents replay of an already accepted TOTP window.

Break-glass recovery is available from the server shell with
`python -m backend.app.cli admin-disable-totp <username>`. Admins can also reset
another user's 2FA from the Security panel with current admin password
confirmation; that reset revokes the target user's active sessions.

Before editing, state:

- Exact files proposed to touch.
- Exact symbols/functions proposed to move or change.
- Whether the slice is strict 2-file-safe, likely 2-file-safe, or not safe.
- What stays in the facade/orchestrator.
- What route/schema/frontend/DB/runtime areas are intentionally not touched.
- What verification will be required beyond compile/build.

After editing, report:

- Scoped diff stat for touched files only.
- Exact symbols moved or changed.
- Adapter/import/wrapper changes left behind.
- What was intentionally not changed.
- Commands run.
- Deadcheck results when required.
- Targeted semantic or runtime verification results.
- What was not verified.

Forbidden claims without evidence:

- “Zero behavior change” without pre/post semantic or runtime proof.
- “Playback fixed” without playable evidence.
- “Protected” without distinguishing prepared vs playable.
- “Safe” for lifecycle/process code based only on compile/build.
- “No route contract change” unless routes/schemas/callers were checked or unchanged by construction.

## Desktop helper

Helper bundles are bound to a single backend origin at build time. Build with
`ELVERN_BACKEND_ORIGIN` set:

```bash
export ELVERN_BACKEND_ORIGIN="https://your-server.tailnet.ts.net"
./clients/desktop-vlc-opener/scripts/publish-bundles.sh
```

If the backend origin changes, including domain or port, republish bundles and
redistribute them to helper users. The helper rejects `elvern-vlc://` URLs that
point to any other origin. URL prefix changes do not require republishing:
origin is scheme, host, and port, not path.

## 10. Practical Examples From Current Repo History

Accepted good slices:

- Library movie identity moved because duplicate selection, quality ranking, edition labels, and hidden movie keys formed one coherent policy owner.
- Library presentation moved because poster lookup, source labels, and serialization were a clear display-owner boundary.
- Library home curation moved because continue-watching and series rails were read-only home assembly.
- Library hidden moved because user/global hidden list/mutation behavior was a real product domain and could preserve DB semantics.
- DB hidden movie keys moved because persistence/backfill behavior had a clear database-domain owner and `db.py` could remain the facade.
- Cloud decomposition worked because auth, source CRUD, sync/upsert/refresh, and stream access each had separate owners while `cloud_library_service.py` stayed as facade.
- Mobile Route2 math/metrics/readiness/snapshot/lifecycle slices worked when they avoided authority/worker/route-facing red zones and had targeted verification.
- Desktop protocol extraction worked because path mapping, target inference, VLC command construction, helper URLs, and XSPF output are deterministic and semantically testable.

Paused mainlines:

- `mobile_playback_service.py` paused when remaining useful slices approached authority, worker, process, session-registry, and route-facing lifecycle red zones.
- `library_service.py` paused after identity, presentation, home curation, and hidden domains landed; the remaining file became mostly orchestration/fetching.
- `db.py` paused after hidden movie-key persistence moved; broader schema/bootstrap cleanup would be a different risk class.

How deadcheck changed decisions:

- Snapshot/state-projection became more reasonable after browser deadcheck could detect prepared-but-not-playable regressions.
- Deep playback lifecycle/process slices still require extra targeted verification because deadcheck does not fully cover teardown, replacement, or live desktop helper behavior.
- Backend-only checks are useful for contracts but cannot prove browser playback or actual external app behavior.

## 11. Default Decision Checklist

Use this checklist before adding or moving code:

- Can I name the owner in one sentence?
- Is this task one owner only?
- Can the existing facade keep route/caller contracts stable?
- Am I avoiding unrelated cleanup?
- Is this change product-core maintainability, not file-size vanity?
- Do I know the stop rule before editing?
- Do I know which deadcheck is required?
- Do I need targeted verification beyond deadcheck?
- Can I say exactly what was not verified?

If the answer is unclear, inspect first. If it stays unclear, pause rather than inventing a boundary.
