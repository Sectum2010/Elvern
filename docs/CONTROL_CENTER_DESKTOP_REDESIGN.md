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

New endpoints:

- `DELETE /api/admin/google-drive-setup`
- `DELETE /api/admin/google-drive-account`

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
- Recovery contains the complete existing encrypted-checkpoint workflow.

Revoking the current session triggers immediate auth reconciliation; revoking a
different session leaves the current administrator in Admin.

All Admin tabs continue to use the existing backend contracts. Users & Invites
keeps the complete User Actions popup, worker controls, invite confirmations,
and password-help workflow. Security keeps own/user 2FA, sessions, URL-prefix
rotation, and the four-step Exposure workflow. Logs keeps real sessions and
audit rows; Recovery keeps the full existing backup workflow. No destructive
mutation was replaced with demo state.

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
license files. They are scoped to `.control-center-desktop`. No Google Fonts or
other font network request is used.

## Visual and device review

The Chromium Settings navigation run can optionally save deterministic visual
review captures by setting `ELVERN_CONTROL_CENTER_SCREENSHOT_DIR`. This round's
ignored local captures are under `tmp/control-center-visual-review/` and cover:

- desktop Settings;
- desktop Settings with the System Status rail;
- desktop Admin Overview;
- phone Settings using the legacy surface;
- phone Admin using the legacy surface;
- tablet Settings using the legacy surface;
- tablet Admin using the legacy surface.

The phone/tablet captures and tests confirm that the Control Center shell is not
mounted for those device classes. Their legacy card order, navigation, and
Floating Island remain in place. Actual Windows, macOS, Linux, iPhone, iPad, and
Android device review is still required before release.

## Explicitly deferred

- Enable/disable-user reauthentication.
- Role-change and password-update session revocation.
- Fine-grained in-flight Download Access revocation.
- Invite idempotency/reveal recovery and Used/Expired hide behavior.
- Recovery-code response-loss recovery.
- Two-phase URL-prefix rotation.
- Exposure runtime activation, env writing, or restart.
- Real live-audit streaming, audit cursors, and audit detail expansion.
- Actual backup restore/download/delete/upload.
- A real computed Security score.
- Phone/tablet Control Center redesign.

## Rollback

Revert the phase commits in reverse order. The old Settings/Admin components and
legacy route contracts remain in the source, so removing the desktop route gate
and shared shell restores the old desktop surface without changing mobile. If
backend rollback occurs after migrations, the additional user-setting keys are
ignored by older code; encrypted OAuth secrets require the matching decrypting
service before DB overrides can be used.

## Validation

The completion report records the authoritative commands and final pass/fail
counts. Real-device review remains required for Windows, macOS, Linux, 1024 px
desktop overlay behavior, and phone/tablet visual parity.
