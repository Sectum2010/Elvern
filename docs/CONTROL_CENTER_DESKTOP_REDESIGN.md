# Desktop Control Center redesign

## Scope

This change gives Settings and Admin a shared Control Center shell on recognized
Windows, macOS, and Linux desktop browsers. Phone, tablet, and unknown-device
routes retain the existing Settings and Admin surfaces. The only cross-device
settings contract change is the poster-width list: `800`, `1000`, `1400`, and
`original`.

The implementation follows a "new outfit, old behavior" rule. Existing API
endpoints, destructive confirmations, auth/session behavior, playback controls,
Library cache and relocation, poster loading, offline recovery, and mobile
navigation remain authoritative.

## Device gate and routes

The new shell requires both:

- `deviceClass === "desktop"`
- platform is `windows`, `mac`, or `linux`

Canonical Settings routes:

- `/settings/appearance`
- `/settings/library`
- `/settings/cloud-sharing`
- `/settings/hidden-titles`
- `/settings/playback-apps`
- `/settings/server-storage` (admin only)

Canonical Admin routes:

- `/admin/overview`
- `/admin/users-invites`
- `/admin/security`
- `/admin/logs`
- `/admin/recovery`

`/admin/assistant` and `/admin/assistant/:requestId` remain standalone routes.
The route gate maps old `?section=` URLs to the appropriate nested route while
preserving other query parameters, the hash, navigation state, and replace
semantics. Invalid desktop routes fall back to Appearance or Overview. The
legacy phone/tablet router behavior is not rewritten.

Legacy mappings:

| Legacy location | Desktop destination |
| --- | --- |
| `/settings` | remembered Settings tab, otherwise `/settings/appearance` |
| `section=preferences`, `section=display` | `/settings/appearance` |
| `section=libraries` | `/settings/cloud-sharing` |
| `section=hidden` or `section=libraries#hidden-list` | `/settings/hidden-titles` |
| `/install`, `/desktop`, or `section=install` | `/settings/playback-apps` |
| Google OAuth setup hash/status | `/settings/server-storage` |
| `section=advanced` | admin: `/settings/server-storage`; standard: `/settings/appearance` |
| `/admin` | remembered Admin tab, otherwise `/admin/overview` |
| `section=security` | `/admin/security` |
| `section=panel` | `/admin/users-invites` |
| `section=logs` | `/admin/logs` |
| `section=recovery` | `/admin/recovery` |

## Session-owned presentation state

Theme and the last Settings/Admin tabs use `sessionStorage`. They survive a
refresh and a Library round trip in the same authenticated browser tab. Auth
logout, identity loss, role change, and a different signed-in user clear that
session presentation state before the next user can consume it.

The admin-only System Status rail open state is a device-local visual preference
in `localStorage`. It does not contain protected application data. Protected rail
payloads remain in the user/role-isolated TanStack Query memory cache and are
cleared with the other protected caches. The rail component itself is not mounted
for a standard user.

## Settings parity and contracts

- Appearance: existing poster appearance, floating-island position, maximum
  poster width, background presets, photo upload/reset, and the approved hue
  editor.
- Library: existing duplicate/recent controls and the complete admin-only age
  restriction management chain.
- Cloud & Sharing: existing Google account state and personal/shared cloud source
  actions.
- Hidden titles: existing personal/global lists, restore, and scope transfer with
  the authoritative reconciliation flow.
- Playback & Apps: the existing platform-specific install/helper surface.
- Server & Storage: existing admin-only media/poster reference paths, Google
  OAuth setup, directory picker behavior, and server-owned controls.

The background contract adds `legacy_v1` and `hue_v2` models, three bounded hue
values, and a sanitized original photo filename. Existing stored colors and
presets stay readable. The poster-width migration is idempotent and maps prior
widths to `1400`.

## OAuth secret boundary

The Google OAuth client secret is encrypted at rest with the existing Elvern
at-rest key. API responses expose only configured/source state, never plaintext.
A blank save preserves the existing secret; an explicit setup-clear removes DB
overrides; disconnect removes the account connection without clearing setup or
source rows. Environment fallback remains supported.

Migrations:

- `poster_width_control_center_1400_v1`
- `google_oauth_secret_fernet1_v1`
- `google_provider_identity_v1`

Google account identity is normalized through `provider_identities`. Provider
subjects are compared by HMAC, display labels are encrypted at rest, and source
rows reference the normalized identity instead of retaining duplicate plaintext
account labels. Legacy subject/label columns remain schema-compatible for safe
rollback but are cleared and are no longer the authoritative read/write path.

Reconnect is a durable, server-authoritative operation bound to the Elvern user,
auth session, HMAC of the opaque operation ID, and expiry. The browser transitions
through starting, external navigation, and one bounded reconciliation into
connected, incomplete/cancelled, account mismatch, error, or expiry. Status and
cancel are authenticated POST requests with `no-store`; raw operation IDs, OAuth
state, authorization codes, tokens, provider subjects, labels, and full provider
URLs are not logged. Callback completion and confirmed account replacement use a
single final database transaction so candidate consumption, normalized identity,
account/source binding, operation completion, and audit state cannot partially
commit.

New endpoints:

- `DELETE /api/admin/google-drive-setup`
- `DELETE /api/admin/google-drive-account`
- `POST /api/cloud-libraries/google/operation/status`
- `POST /api/cloud-libraries/google/operation/cancel`

## Settings resource ownership

Age restrictions use one resource controller for initial load, forced refresh,
cached refresh, error/retry, cancellation, and identity changes. A manual refresh
issues one authoritative request, keeps cached rows visible, disables duplicate
clicks, and displays `Refreshing…` with the Meridian scan icon until the real
request settles.

Hidden titles use `GET /api/settings/hidden-titles`, a versioned, private,
no-cache response that reads only hidden-key/direct-hidden rows and never invokes
the full Library presentation path, poster generation, or unrelated title
parsing. The response contains personal rows and, for admins only, global rows.
Its ETag is derived from permission/user-overlay revision plus role; a matching
`If-None-Match` returns 304 before list materialization. The frontend keeps one
user/role-isolated in-memory Control Center query (`30s` stale time, `4h` garbage
collection), shows cached rows while validating, and never persists hidden-title
contents in browser storage.

## Admin parity

- Overview uses the approved presentation-only `92` / `PRIVATE` score and real
  system, exposure, maintenance, user, and session data.
- Users & Invites retains user actions, create user, age credential, invites,
  password help, worker controls, and existing mutation endpoints.
- Security retains own/user 2FA, URL-prefix rotation, sessions, and exposure
  workflow contracts. Opening the Exposure planner always refreshes its
  authoritative server state before presenting the current draft/prepared result.
  The new own-2FA dialog traps focus, supports Escape, and restores focus.
- Logs retains real sessions and the real 100-event audit response. The approved
  demo ticker line is presentation-only and does not replace audit data.
- Recovery uses the Meridian single-card, three-stage encrypted-checkpoint
  workflow with real catalog, verification, preview, deletion, recent-auth, and
  asynchronous job state. It still provides no Restore, browser Export, or
  backup-keyring export action.

Revoking the current session triggers immediate auth reconciliation; revoking a
different session leaves the current administrator in Admin.

All Admin tabs continue to use the existing backend contracts. Users & Invites
keeps the complete User Actions popup, worker controls, invite confirmations,
and password-help workflow. Security keeps own/user 2FA, sessions, URL-prefix
rotation, and the four-step Exposure workflow. Logs keeps real sessions and
audit rows; Recovery keeps the full existing backup workflow. No destructive
mutation was replaced with demo state.

The private local Meridian demo under `ui-redesign/tmp` is the visual source of
truth for the desktop account menu, User Actions, and Recovery surfaces. Demo
behavior and synthetic values are never backend authority; production access,
security, and mutation contracts remain authoritative.

Users & Invites uses one Meridian surface hierarchy rather than stacked legacy
and Meridian card shadows. Avatars select one of eight restrained palettes from
a stable hash of the internal user ID; color never communicates role, age,
presence, or risk. Active/background/pending status indicators have distinct,
subtle motion while offline/disabled indicators remain static; reduced-motion
mode disables all status animation. User Actions uses dedicated Meridian
Account, Assistant, and Downloads panels while retaining the secure production
handlers. Enable and Disable both require the current administrator password;
disable revokes active access while enable does not restore old sessions. Age
credentials silently advance on the configured local-calendar anniversary until
18, and only 18 is displayed as `18+`; under-18 values never receive a plus.
Invite age selection is an inline Meridian panel on desktop, and Password Help
has its own stable card with real loading, refresh, cached-error, empty, and
request states.

Overview keeps the presentation-only `92 / PRIVATE` gauge. Posture rows reserve
an explicit label column and constrain long values to their value column. Long
origins remain right-aligned and ellipsized, with the full value available to
tooltip, keyboard focus, and the restrained copy action.

## Viewport paint policy

Desktop Settings/Admin applies a synchronous route-scoped Control Center paint
state to `html`, `body`, `#root`, the application shell, and the page shell before
the first visible React paint. The production root fills the usable viewport and
has no demo-frame radius or shadow; browser/OS/PWA chrome owns physical window
rounding. The paint follows light/mixed/dark theme changes and safe-area insets,
then is removed when leaving Control Center so the chosen Library background is
restored. This prevents Neon or another Library preset from leaking through the
corners during direct load, route navigation, refresh, or status-rail motion.

## Request ownership

Desktop Admin resource requests are route-owned and isolated by user, role, and
resource in a protected in-memory Query cache. Query functions use TanStack's
AbortSignal. Route generations reject stale application, and auth cache cleanup
cancels/removes late protected requests.

Expected baseline request owners:

| Surface | Initial route resources |
| --- | --- |
| Overview | system, users, sessions, exposure (4) |
| Users & Invites | system, users, invites, password help (4), plus one worker owner |
| Security | system, users, sessions, invites, URL prefix, own TOTP (6) |
| Logs | system, sessions, audit (3) |
| Recovery | system, audit, backups (3) |
| Closed status rail | none |
| First rail open | seven real resources; fresh page data is deduplicated |

While a scan runs, only system status polls every 2.5 seconds. Workers poll every
4 seconds only on visible Users & Invites, with one owner. The rail polls its
shared resources every 30 seconds only while open and visible. SSE refreshes only
users/sessions used by the current route and does not perform a duplicate
`stream_connected` baseline. Failed rail resources retain their most recent real
value and are marked stale instead of becoming zero.

The route-resource table is asserted directly in `adminControlCenter.test.js`.
Concurrent requests for the same protected resource are asserted to collapse to
one request in `controlCenterQueries.test.js`; the rail's first-open budget is
seven requests in `SystemStatusRail.test.jsx`.

## Motion and accessibility

The Settings/Admin card switches the real route at the midpoint of an
approximately 550 ms 3D transition. Reduced-motion mode uses a short fade and no
3D rotation. Buttons, links, dialogs, focus-visible states, disabled pending
states, Escape handling, and live status feedback use native semantics.

At wide desktop widths the status rail consumes its own right-side column. At
approximately 1024 px it becomes an overlay so the main Control Center does not
collapse.

## Local fonts

Archivo, Sora, and Space Grotesk are bundled as local WOFF2 assets with their OFL
license files. They are scoped to `.meridian-control-center`. No Google Fonts or
other font network request is used.

## Visual and device review

Visual verification has two explicit modes. Local source generation requires the
private demo path and an explicit update flag:

```bash
ELVERN_MERIDIAN_DEMO_PATH=/absolute/private/Settings-Meridian.dc.html \
  npm run visual:update:control-center --prefix frontend
```

It renders the real local demo and production in the same Chromium runtime,
normalizes all dynamic values to synthetic fixtures, and writes demo, production,
diff, geometry, computed-style, and source-hash evidence. Baseline updates are a
review action: generated files remain unstaged and must be visually inspected
before an owner accepts them. The private HTML/support script and its absolute
path are never copied into production, served by Elvern, or shipped in reviewed
artifacts.

The source-generation suite also has a direct 21-state gate for the bottom-left
account menu, all three User Actions tabs, and all three Recovery phases across
light, mixed, and dark themes:

```bash
ELVERN_MERIDIAN_DEMO_PATH=/absolute/private/Settings-Meridian.dc.html \
  node frontend/scripts/run-cross-browser-playwright.mjs \
    --project chromium-control-center-source-generation \
    --grep "account, User Actions, and Recovery states"
```

The standalone demo runtime is normalized to the production global `border-box`
model and bundled fonts; the private demo source itself is never edited. Each
state writes full demo/production crops plus the strict parity crop, pixel diff,
full-region diagnostics, and named geometry/computed-style evidence under the
ignored `tmp/meridian-parity/direct-states/` directory. Account-menu comparison
covers the full region and is effectively pixel exact. User Actions compares the
full modal region with a 6% changed-pixel and mean-delta 9 ceiling while retaining
exact shell geometry and styles. Recovery retains full phase screenshots and
diagnostics, while its strict pixel gate covers the shared top row, scope, and
three-stage navigation so intentional security copy and real controls do not
exempt the shell; card width, radius, padding, stage bars/labels, and phase-content
geometry remain independently asserted within 1px. The verified worst direct
results were 0 changed pixels for account-menu geometry, `5.8462% / 8.5847` for
User Actions, and `3.9032% / 3.4167` for the Recovery shell.

Normal CI does not read the private demo. It compares stubbed production against
the sanitized reviewed files under
`frontend/tests-phase7/baselines/control-center`:

```bash
node frontend/scripts/run-cross-browser-playwright.mjs \
  --use-existing-build --project chromium-control-center-baseline
```

The CI gate requires exact dimensions and landmark presence, 0-1px named
geometry tolerance, exact audited computed styles, changed-pixel ratio at most
`0.5%`, and mean channel delta at most `1.5`. Animations are frozen only for
pixel capture; separate tests retain the real motion/reduced-motion contract.
The baseline manifest records the private source SHA-256 and only these approved
differences:

- user-approved warm light palette;
- browser-owned root frame instead of the demo's 18px preview radius/shadow;
- deterministic avatar palettes;
- state-specific user status motion;
- approved connected Google copy;
- long-origin ellipsis/tooltip/copy treatment;
- existing Create User `More ages` selector.

Reviewed deterministic captures cover:

- desktop Settings;
- desktop Settings with the System Status rail;
- desktop Admin Overview;
- Settings Age pending/success/error;
- Cloud connected/reconnect/incomplete Back recovery;
- Hidden personal/global empty, non-empty, skeleton, and cached refresh;
- Users light/mixed/dark and active/background/pending/offline/disabled states;
- Create User, inline Invite Age, synthetic invite code, and Password Help states;
- long and IPv6-like origins at 1360, 1180, and 1024 widths;
- Neon corner paint at 1360 x 880, 1440 x 900, 1180 x 760, and 1024 x 768;
- phone Settings using the legacy surface;
- phone Admin using the legacy surface;
- tablet Settings using the legacy surface;
- tablet Admin using the legacy surface.

The phone/tablet captures and tests confirm that the Control Center shell is not
mounted for those device classes. Their legacy card order, navigation, and
Floating Island remain in place. Actual Windows, macOS, Linux, iPhone, iPad, and
Android device review is still required before release.

## Explicitly deferred

- Role-change and password-update session revocation.
- Invite idempotency/reveal recovery and Used/Expired hide behavior.
- Recovery-code response-loss recovery.
- Two-phase URL-prefix rotation.
- Exposure runtime activation, env writing, or restart.
- Real live-audit streaming, audit cursors, and audit detail expansion.
- Actual backup restore/download/upload.
- A real computed Security score.
- Phone/tablet Control Center redesign.

## Rollback

This implementation is intentionally left as visible, unstaged working-tree
changes until the owner reviews and commits it. Before a commit, rollback means
reviewing and removing only this documented patch; no agent-created stash,
temporary branch, hidden commit, or alternate worktree is involved. After an
owner-created commit, use the repository's normal reviewed revert process for
that commit. The old Settings/Admin components and legacy route contracts remain
in the source, so removing the desktop route gate and shared shell restores the
old desktop surface without changing mobile. If backend rollback occurs after
migrations, the additional user-setting keys are ignored by older code;
encrypted OAuth secrets require the matching decrypting service before DB
overrides can be used.

## Validation

The completion report records the authoritative commands and final pass/fail
counts. Real-device review remains required for Windows, macOS, Linux, 1024 px
desktop overlay behavior, and phone/tablet visual parity.
