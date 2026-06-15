# Exposure Mode Planner

Elvern's exposure mode planner is a planning surface for a future private/public exposure switch. Phase 1 added inert planning drafts. Phase 2 added Maintenance Mode as a reversible server safety mode. Phase 3 adds a prepared manual switch plan. None of these phases activates public/private switching.

## Product Rules

- Private Mode is for tailnet, Tailscale, LAN, and private DNS access.
- Public Mode supports a purchased custom domain such as `https://media.example.com`.
- Public Mode also supports direct public IP exposure such as `http://203.0.113.10:4173`, and the UI labels it `Not recommended`.
- Public Mode does not offer Tailscale Funnel.
- Public custom domains must use HTTPS to be considered ready.
- Saving a pending draft requires admin password re-authentication and acknowledgement.
- Pending drafts do not change runtime behavior.
- Maintenance Mode is a standalone security control, not only an exposure-mode sub-control.
- Maintenance Mode blocks enabled non-admin users, revokes/logs out active non-admin sessions when enabled, and does not change account enabled state.
- A prepared manual switch plan still has `takes_effect=false`.

## Phase 1 Limits

- Does not write deploy files or environment files.
- Does not change `ELVERN_PRIVATE_NETWORK_ONLY`, `ELVERN_PUBLIC_APP_ORIGIN`, or `ELVERN_BACKEND_ORIGIN`.
- Does not rotate the URL prefix automatically.
- Draft validation and pending-draft saving do not revoke sessions.
- Does not disable non-admin users.
- Does not change token TTLs.
- Does not probe a candidate origin from the backend.

Validation is limited to strict origin parsing, static safety checks, and comparing the proposed origin with the current admin request origin. If the origins do not match, Phase 3 keeps that as a warning; Phase 4 verifies through the target origin after manual env/proxy changes and restart.

## Phase 2 Maintenance Mode

Maintenance Mode is stored in `app_settings` as `exposure_mode_maintenance_lock_json` for compatibility with the earlier Phase 2 implementation. It is admin-controlled from the Security admin area and the Manage Exposure Mode UI, and requires current admin password re-authentication. Enabling it also requires acknowledgement that it logs out non-admin users and temporarily blocks non-admin logins without disabling accounts.

When Maintenance Mode is on:

- Admin users can still log in and manage the server.
- Enabled standard users cannot log in or use normal authenticated APIs.
- Active non-admin auth sessions are revoked with reason `maintenance_mode`.
- Auth-session-bound download, native playback, and desktop handoff records for non-admin users are invalidated.
- Actual disabled users remain disabled and keep the existing disabled-account behavior.
- Standard users see exactly:

> The server is currently under construction, please try again later

Maintenance Mode does not activate public/private mode, write environment files, rotate the URL prefix, change token TTLs, disable users, enable disabled users, or mutate pending exposure drafts. Disabling Maintenance Mode does not restore revoked sessions or create new sessions.

## Phase 3 Prepared Manual Switch Plan

The prepared manual switch plan is stored in `app_settings` as `exposure_mode_prepared_switch_json`. It freezes a revalidated pending draft into a copyable manual plan for an admin. It does not load into runtime `Settings` and does not change the running server.

Preparing a manual switch requires:

- Current admin password re-authentication.
- Admin acknowledgement that the action only prepares a plan.
- An existing pending exposure draft.
- Revalidation of the pending draft with no blocking errors.
- For direct public IP drafts, the direct-IP Not recommended acknowledgement must already be stored in the pending draft.

Preparing a manual switch automatically enables Maintenance Mode if it is not already on, and therefore revokes/logs out active non-admin sessions. Admin sessions remain allowed. The current request origin no longer blocks Phase 3 prepare; current-origin matching is a Phase 4 verification requirement after manual env/proxy changes and restart.

Prepared switch payloads explicitly include:

- `maintenance_mode_auto_enabled: true`
- `verification_required: true`
- `current_origin_match_required_in_phase: "phase_4_verification"`
- `takes_effect: false`

The prepared plan includes a copyable env suggestion block and manual restart / reverse proxy checklist. The block contains only high-level non-secret settings such as `ELVERN_PRIVATE_NETWORK_ONLY`, `ELVERN_PUBLIC_APP_ORIGIN`, `ELVERN_BACKEND_ORIGIN`, and `ELVERN_COOKIE_SECURE`.

Phase 3 does not write env files, edit deploy files, restart Elvern, activate exposure mode, rotate the URL prefix, revoke admin sessions, disable users, change `users.enabled`, or change token TTLs. It can revoke non-admin sessions only through Maintenance Mode. The prepared switch always reports `Prepared for manual apply`, `Activation not implemented`, and `takes_effect=false`.

## Phase 4 Prepared Switch Verification

Phase 4 is an admin-only verification step after an admin manually applies the prepared env/proxy/DNS changes, manually restarts Elvern, and opens the admin UI through the intended target address. It verifies the stored prepared switch against the current request origin and current runtime settings. It does not activate exposure mode.

Verification requires:

- Current admin password re-authentication.
- Admin acknowledgement that verification is non-activating.
- A stored prepared switch with status `prepared_for_manual_apply` or `verified_after_restart`.
- Maintenance Mode still enabled.

If verification passes with no blocking errors, the prepared switch is updated to `verified_after_restart` and stores `verified_at`, the verifying admin identity, and the structured verification result. Warnings are allowed. The stored prepared switch continues to include `takes_effect=false`, `activation_not_implemented=true`, and `maintenance_mode_release: "manual_only"`.

Phase 4 checks include:

- Current request origin matches the expected public origin for public custom domain and direct IP plans.
- Current request origin matches the private origin when a private origin was prepared; if no private origin was prepared, origin matching is skipped.
- Runtime `ELVERN_PRIVATE_NETWORK_ONLY` matches the prepared mode.
- Runtime `ELVERN_PUBLIC_APP_ORIGIN` matches public plans, while private-mode mismatches are warnings.
- Runtime `ELVERN_COOKIE_SECURE` is true for public HTTPS and false for plain HTTP direct IP.
- Broad trusted proxy CIDRs block public verification and warn in private verification.
- Missing URL prefix is a warning only.

Phase 4 does not write env files, edit deploy files, restart Elvern, rotate the URL prefix, release Maintenance Mode, revoke sessions, disable users, change token TTLs, fetch or probe user-provided origins from the backend, or change public/private runtime settings. Maintenance Mode remains under admin control.

## Phase 5 Finalize Verified Profile

Phase 5 records the verified exposure profile as the official current exposure profile. It requires a prepared switch with status `verified_after_restart`, current admin password re-authentication, and acknowledgement that finalization records the profile without changing runtime settings.

The finalized profile is stored in `app_settings` as `exposure_mode_finalized_profile_json`. It records the verified mode, origin, provider, verification snapshot, prepare/verify timestamps, and finalizing admin identity. The record keeps `takes_effect=false`, `maintenance_mode_release: "manual_only"`, `url_prefix_rotation: "manual_only"`, and `env_writing: "manual_only"`.

After finalization, the active pending draft and prepared switch working state are cleared so the UI returns to a clean current-profile view. The finalized profile itself is enough for this phase; detailed history, rollback, and richer profile timeline polish are left for later.

Phase 5 does not write env files, edit deploy files, restart Elvern, rotate the URL prefix, change token TTLs, change runtime `Settings`, revoke sessions, release users, disable users, change `users.enabled`, or turn Maintenance Mode off. Maintenance Mode remains under admin control.

## Phase 6 Library Maintenance Warning

Phase 6 adds a small Library page warning line while Maintenance Mode is on:

> Maintenance mode is still turned on

The warning is intentionally small and appears directly under the Library header. It has no button, modal, or blocking behavior, and disappears once Maintenance Mode is off. No Phase 7 history or rollback polish is implemented now.

## Public Custom Domain

Use this for a purchased DNS name with HTTPS. The planner checks that the input is origin-only, uses a DNS name rather than localhost/private IP/raw IP, and uses HTTPS.

Recommended checks before a future activation:

- DNS A/AAAA or provider hostname reaches the Elvern host or tunnel.
- HTTPS is working in the browser.
- `ELVERN_COOKIE_SECURE=true` for public HTTPS.
- `ELVERN_COOKIE_SECURE=false` for plain HTTP direct IP planning, because Secure cookies require HTTPS.
- `ELVERN_TRUSTED_PROXY_CIDRS` is restricted to known proxy addresses.
- Global low-regression security headers are still present.
- URL prefix rotation remains manual. Consider rotating it after completing public-mode setup if desired.

## Public Direct IP

Direct public IP mode is allowed for planning, and the UI labels it `Not recommended`. A purchased domain with HTTPS is safer and easier to maintain.

The planner rejects loopback, private, link-local, and reserved IP addresses for direct public mode. HTTP direct IP planning is allowed with warnings. HTTPS direct IP planning is also allowed, but certificate setup is usually harder than using a domain.

## Private Mode

Private Mode may use a private origin such as a tailnet hostname, LAN hostname, or private IP address. Network access is controlled by bind host, firewall, Tailscale/LAN configuration, and any reverse proxy.

## Reverse Proxy Notes

- Caddy: create DNS A/AAAA records to the server, reverse proxy to the frontend server, and use automatic HTTPS when ports 80/443 are reachable.
- Nginx: configure a TLS certificate and proxy browser traffic to the frontend server.
- Cloudflare Tunnel: configure a tunnel to the frontend server and public hostname in Cloudflare. This is not Tailscale Funnel.
- Manual/Other: ensure TLS and reverse proxy forwarding terminate at `frontend/server.mjs`; do not expose the backend directly unless a later phase explicitly designs that path.

## Future Activation Notes

Future activation should be split into later explicit phases. It should require admin re-authentication and should not automatically rotate the URL prefix or change token TTLs.

During a future stable switch, keep Maintenance Mode on until the server is ready. Standard users should see:

> The server is currently under construction, please try again later

Phase 4 should run after manual env/proxy apply and restart. It should verify the active config through the target origin and confirm the server is reachable at the intended address.

Phase 5 should design finalization and user release behavior. Admin should receive a success or next-step message before any later reauth/logout flow. Activation remains outside the planner, maintenance-lock, and prepared-plan phases.

## Security Checklist

- Keep Phase 1 validation free of backend requests to user-provided origins.
- Require HTTPS for public custom domains.
- Label direct public IP exposure as Not recommended.
- Keep trusted proxy CIDRs narrow.
- Keep secure cookies enabled for public HTTPS.
- Keep global security headers active.
- Keep URL prefix rotation manual.
- Keep prepared manual switch plans non-activating and `takes_effect=false`.
