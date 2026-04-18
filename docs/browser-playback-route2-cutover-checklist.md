# Browser Playback Route 2 Cutover Checklist

This checklist is for explicit Route 2 validation only.
Legacy Browser Playback remains the default path until every item below is stable.

## Control Plane

- `attach_revision` only increases and never goes backward for a live session.
- `client_attach_revision` only reflects revisions the server has acknowledged.
- A stale Route 2 status response must not pull the client back to an older revision.
- Session status remains the only authority for active epoch identity and attach target.

## Truth Guarantees

- Route 2 manifests expose only the published contiguous frontier.
- Route 2 init and media segment requests never read staging files.
- No Route 2 endpoint blocks waiting for future segments.
- If an epoch is marked attach-ready or draining, the published media it exposes is immediately readable.
- Missing published init or segment files are logged as truth violations.

## Replacement Epoch

- An out-of-range seek creates a replacement epoch instead of mutating the active epoch namespace.
- Promotion switches `active_epoch_id`, bumps `attach_revision`, and updates `active_manifest_url` atomically.
- If replacement startup fails before promotion, the old authoritative epoch remains active if it is still readable.
- If replacement startup fails and no authoritative epoch remains, the Route 2 session fails explicitly.

## Draining / Stale Reconnect

- A promoted-out epoch enters `draining` and never returns to `active`.
- Draining media stays readable until either:
  - the client has acknowledged the new revision and the drain idle grace expires, or
  - the drain max lifetime expires.
- Requests to replacement or expired epochs are treated as stale reconnects and are rejected.

## Client Recovery

- Initial attach and replacement attach both retry until the server confirms `client_attach_revision`.
- If attach fails after a revision change, the client re-reads session authority and reattaches.
- Route 2 recovery keeps using `engine_mode=route2`; it must not silently recreate a legacy session.
- Media-element or HLS fatal errors on explicit Route 2 sessions trigger authority-driven recovery before surfacing a terminal error.

## Required Manual Scenarios

- Start Route 2 playback from `0:00` and confirm attach revision `1` is acknowledged.
- Play through a replacement-epoch promotion and confirm the client reattaches to the new epoch.
- Force a stale response or retry window and confirm the client does not move back to an older revision.
- Kill the active transcoder while published media still exists and confirm the session stays readable while recovery starts.
- Kill the replacement transcoder before promotion and confirm the old authoritative epoch remains active if still readable.
- Leave the detail page, return to the same item, and confirm Route 2 reconnects to the current authority epoch.
- Request media from an old drained epoch after promotion and confirm it eventually expires and is rejected.

## Must Never Happen

- No fake complete VOD manifest on Route 2.
- No session-scoped moving media alias.
- No serving from staging files.
- No old draining epoch becoming `active` again.
- No silent fallback from an explicit Route 2 session to legacy media semantics.

## Additional Required Manual Scenarios

- While staying inside the active epoch’s published ready range, perform `-10s` and confirm:
  - no replacement epoch is created
  - `attach_revision` does not change
  - no client reattach occurs
  - seek remains local-only

- Start Route 2 playback from `0:00` and play continuously through the old first seam/boundary area, confirming:
  - no stall at the former seam
  - no future-segment wait behavior
  - no false-ready behavior

- Before true end-of-media, confirm Route 2 manifest does not expose `#EXT-X-ENDLIST`.
- At true end-of-media, confirm Route 2 manifest does expose `#EXT-X-ENDLIST`.

- Run the Route 2 validation flow through both:
  - browser-playback route root
  - mobile-playback route root
  and confirm authority, revision, and epoch-scoped media behavior remain consistent.