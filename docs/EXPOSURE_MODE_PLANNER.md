# Exposure Mode Planner

Elvern's exposure mode planner is a planning surface for a future private/public exposure switch. Phase 1 added inert planning drafts. Phase 2 adds a reversible temporary maintenance lock. Phase 3 adds a prepared manual switch plan. None of these phases activates public/private switching.

## Product Rules

- Private Mode is for tailnet, Tailscale, LAN, and private DNS access.
- Public Mode supports a purchased custom domain such as `https://media.example.com`.
- Public Mode also supports direct public IP exposure such as `http://203.0.113.10:4173`, but this is explicitly NOT RECOMMENDED.
- Public Mode does not offer Tailscale Funnel.
- Public custom domains must use HTTPS to be considered ready.
- Saving a pending draft requires admin password re-authentication and acknowledgement.
- Pending drafts do not change runtime behavior.
- The temporary maintenance lock blocks enabled non-admin users without changing account enabled state.
- A prepared manual switch plan still has `takes_effect=false`.

## Phase 1 Limits

- Does not write deploy files or environment files.
- Does not change `ELVERN_PRIVATE_NETWORK_ONLY`, `ELVERN_PUBLIC_APP_ORIGIN`, or `ELVERN_BACKEND_ORIGIN`.
- Does not rotate the URL prefix automatically.
- Does not revoke sessions.
- Does not disable non-admin users.
- Does not change token TTLs.
- Does not probe a candidate origin from the backend.

Validation is limited to strict origin parsing, static safety checks, and comparing the proposed origin with the current admin request origin. If the origins do not match, the admin should open the admin page through the proposed address and validate again.

## Phase 2 Maintenance Lock

The temporary maintenance lock is stored in `app_settings` as `exposure_mode_maintenance_lock_json`. It is admin-controlled from the Manage Exposure Mode UI and requires current admin password re-authentication. Enabling it also requires acknowledgement that it temporarily blocks non-admin users but does not disable their accounts.

When the lock is on:

- Admin users can still log in and manage the server.
- Enabled standard users cannot log in or use normal authenticated APIs.
- Existing standard-user sessions are not revoked by the lock.
- Actual disabled users remain disabled and keep the existing disabled-account behavior.
- Standard users see exactly:

> The server is currently under construction, please try again later

The lock does not activate public/private mode, write environment files, rotate the URL prefix, change token TTLs, revoke sessions, disable users, or mutate pending exposure drafts. Future activation work can use this lock before manual environment and restart verification.

## Phase 3 Prepared Manual Switch Plan

The prepared manual switch plan is stored in `app_settings` as `exposure_mode_prepared_switch_json`. It freezes a revalidated pending draft into a copyable manual plan for an admin. It does not load into runtime `Settings` and does not change the running server.

Preparing a manual switch requires:

- Current admin password re-authentication.
- Admin acknowledgement that the action only prepares a plan.
- An existing pending exposure draft.
- The temporary maintenance lock to be on.
- Revalidation of the pending draft with no blocking errors.
- For public custom domain and direct public IP drafts, the current admin request origin must match the proposed public origin.
- For direct public IP drafts, the direct-IP NOT RECOMMENDED acknowledgement must already be stored in the pending draft.

The prepared plan includes a copyable env suggestion block and manual restart / reverse proxy checklist. The block contains only high-level non-secret settings such as `ELVERN_PRIVATE_NETWORK_ONLY`, `ELVERN_PUBLIC_APP_ORIGIN`, `ELVERN_BACKEND_ORIGIN`, and `ELVERN_COOKIE_SECURE`.

Phase 3 does not write env files, edit deploy files, restart Elvern, activate exposure mode, rotate the URL prefix, revoke sessions, disable users, change `users.enabled`, or change token TTLs. The prepared switch always reports `Prepared for manual apply`, `Activation not implemented`, and `takes_effect=false`.

## Public Custom Domain

Use this for a purchased DNS name with HTTPS. The planner checks that the input is origin-only, uses a DNS name rather than localhost/private IP/raw IP, and uses HTTPS.

Recommended checks before a future activation:

- DNS A/AAAA or provider hostname reaches the Elvern host or tunnel.
- HTTPS is working in the browser.
- `ELVERN_COOKIE_SECURE=true` for public HTTPS.
- `ELVERN_TRUSTED_PROXY_CIDRS` is restricted to known proxy addresses.
- Global low-regression security headers are still present.
- URL prefix rotation remains manual. Consider rotating it after completing public-mode setup if desired.

## Public Direct IP

Direct public IP mode is allowed for planning, but it is NOT RECOMMENDED. A purchased domain with HTTPS is safer and easier to maintain.

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

During a future stable switch, enable the temporary maintenance lock before switch preparation and keep non-admin users blocked until the server is ready. Standard users should see:

> The server is currently under construction, please try again later

Phase 4 should run after manual env/proxy apply and restart. It should verify the active config through the target origin and confirm the server is reachable at the intended address.

Phase 5 should design finalization and user release behavior. Admin should receive a success or next-step message before any later reauth/logout flow. Activation remains outside the planner, maintenance-lock, and prepared-plan phases.

## Security Checklist

- Keep Phase 1 validation free of backend requests to user-provided origins.
- Require HTTPS for public custom domains.
- Label direct public IP exposure as NOT RECOMMENDED.
- Keep trusted proxy CIDRs narrow.
- Keep secure cookies enabled for public HTTPS.
- Keep global security headers active.
- Keep URL prefix rotation manual.
- Keep prepared manual switch plans non-activating and `takes_effect=false`.
