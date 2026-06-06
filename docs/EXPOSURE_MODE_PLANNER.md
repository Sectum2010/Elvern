# Exposure Mode Planner

Elvern's exposure mode planner is a Phase 1 planning surface for a future private/public exposure switch. Phase 1 is intentionally non-activating and non-destructive.

## Product Rules

- Private Mode is for tailnet, Tailscale, LAN, and private DNS access.
- Public Mode supports a purchased custom domain such as `https://media.example.com`.
- Public Mode also supports direct public IP exposure such as `http://203.0.113.10:4173`, but this is explicitly NOT RECOMMENDED.
- Public Mode does not offer Tailscale Funnel.
- Public custom domains must use HTTPS to be considered ready.
- Saving a pending draft requires admin password re-authentication and acknowledgement.
- Pending drafts do not change runtime behavior.

## Phase 1 Limits

- Does not write deploy files or environment files.
- Does not change `ELVERN_PRIVATE_NETWORK_ONLY`, `ELVERN_PUBLIC_APP_ORIGIN`, or `ELVERN_BACKEND_ORIGIN`.
- Does not rotate the URL prefix automatically.
- Does not revoke sessions.
- Does not disable non-admin users.
- Does not change token TTLs.
- Does not probe a candidate origin from the backend.

Validation is limited to strict origin parsing, static safety checks, and comparing the proposed origin with the current admin request origin. If the origins do not match, the admin should open the admin page through the proposed address and validate again.

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

Future activation should be a separate, explicit phase. It should require admin re-authentication and should not automatically rotate the URL prefix or change token TTLs.

During a future stable switch, non-admin accounts should be temporarily blocked until the server is ready. Standard users should see:

> The server is currently under construction, please try again later

Admin should receive a success or next-step message before any later reauth/logout flow. Existing session handling belongs to that future activation phase.

## Security Checklist

- Keep Phase 1 validation free of backend requests to user-provided origins.
- Require HTTPS for public custom domains.
- Label direct public IP exposure as NOT RECOMMENDED.
- Keep trusted proxy CIDRs narrow.
- Keep secure cookies enabled for public HTTPS.
- Keep global security headers active.
- Keep URL prefix rotation manual.
