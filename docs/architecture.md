# Elvern Desktop VLC Architecture

## Summary

Elvern now has two practical playback paths:

- installed VLC on desktop as the preferred playback plane
- browser playback for convenience and fallback only

The backend remains the control plane. The browser app still handles auth, library browsing, search, detail pages, progress summaries, and browser playback. Desktop playback now prefers handing off to the user’s installed VLC app instead of relying on the older embedded-player experiment.

Elvern is now also a real multi-user family system:

- separate user accounts
- simple roles: `admin` and `standard_user`
- per-user progress and continue-watching state
- audit logging for security and administration

## Why the architecture changed

Browser playback is still useful, but giant remux files expose the limits of the web stack:

- browser codec/container support is narrow
- HLS fallback adds latency and transcode cost
- browser timelines reflect streaming semantics instead of true file-native seek
- large random seeks never feel like local VLC

The result is a desktop-first VLC model:

- Elvern controls browsing, metadata, and resume/progress
- installed VLC handles the actual movie playback experience
- browser playback remains secondary
- Linux, Windows, and macOS all target the same private DGX Elvern server URL

## Core pieces

- `backend/`
  The Elvern API, SQLite database, auth, media scan/index, direct stream routes, HLS fallback, playback progress, VLC target resolution, and fallback session control plane.
- `frontend/`
  The private PWA shell for login, library/search/detail/settings, browser playback, and VLC handoff.
- `ELVERN_PUBLIC_APP_ORIGIN`
  The canonical private DGX app URL every desktop client should open in the browser.
- `ELVERN_BACKEND_ORIGIN`
  The matching private DGX backend API origin desktop helpers use to resolve short-lived VLC handoffs.
- `clients/desktop-vlc-opener/`
  Lightweight helper that resolves a short-lived Elvern handoff and launches the user’s installed VLC app.
- `scripts/`
  The local control layer for Ubuntu: one-click start, stop, restart, status, logs, launcher installation, and systemd automation.
- `deploy/linux/`
  Desktop-entry templates used to install `Elvern` and `Elvern Control` into the Linux app menu and Desktop.

## Desktop VLC flow

1. A signed-in browser opens a movie detail page.
2. That browser is using the same canonical DGX private app URL on Linux, Windows, or macOS.
3. The user chooses `Open in VLC`.
4. Elvern resolves the best desktop VLC target for the current platform.
5. Preferred order:
   - direct local path on Linux same-host
   - mapped Windows path or share
   - mapped macOS path or volume
   - short-lived backend URL only as fallback
6. In the normal cross-platform path, Elvern creates a short-lived `elvern-vlc://` handoff.
7. The lightweight desktop opener resolves that handoff against the backend API origin on the same DGX host and launches installed VLC with either:
   - the mapped direct source path
   - or the short-lived backend URL fallback
8. Linux same-host direct launch remains available as a fallback, but the standard daily-use flow is one shared DGX server URL plus local VLC on each client.
9. VLC playlists remain only as a secondary manual fallback, not the normal daily-use path.

The playback contract used by fallback URL mode remains platform-neutral so future non-desktop clients can still reuse it later.

## Security model

- the main Elvern web app still uses normal cookie-based login sessions
- sessions are per-user and can be revoked by an admin
- users can be disabled without deleting their history
- direct VLC path handoff never exposes filesystem browsing outside configured path mappings
- fallback URL mode uses short-lived, media-item-bound playback tokens tied to the requesting Elvern session
- desktop helper handoffs are opaque, short-lived, revocable, and do not expose mapped filesystem paths to the browser UI
- no anonymous raw media URLs are exposed
- no change to the Tailscale-only/private deployment model
- media paths are still real-path-validated under `ELVERN_MEDIA_ROOT`
- the fallback session contract remains reusable for future native clients

## Backend modules

- `backend/app/routes/desktop_playback.py`
  Desktop VLC target resolution, Linux same-host VLC launch, helper handoff creation/resolution, and VLC playlist generation.
- `backend/app/routes/admin.py`
  User management, session revocation, and audit-log review.
- `backend/app/services/desktop_playback_service.py`
  Platform mapping, file/share target construction, VLC process launch, opaque helper handoffs, and playlist generation.
- `backend/app/services/admin_service.py`
  Multi-user admin operations and active-session listing.
- `backend/app/services/audit_service.py`
  Durable audit records for auth, playback handoff, and admin actions.
- `backend/app/routes/playback.py`
  Browser direct-play vs HLS decision layer remains intact.
- `backend/app/routes/stream.py`
  Browser direct streaming remains intact.
- `backend/app/progress.py`
  Shared progress persistence used by browser playback today and available to future VLC sync integration later.

## Local control layer

Phase 3.1 keeps the backend/frontend split intact and adds a local automation layer instead of collapsing the app into a fake monolith.

On Ubuntu today:

- `scripts/elvern-start.sh` starts or reuses backend/frontend safely
- `scripts/elvern-control.sh` gives a simple control menu
- `scripts/install-systemd.sh` installs stable boot-time services
- `scripts/install-launchers.sh` creates app-menu and Desktop launchers

This means the user no longer needs to remember separate `uvicorn`, `node`, or `dotnet` commands for daily use.

## Future packaging direction

The control-layer idea is meant to extend cleanly to other client platforms without changing the Elvern server architecture:

- Windows
  Prefer installed VLC through the `elvern-vlc://` helper with mapped direct source paths or shares.
- macOS
  Prefer installed VLC through the `elvern-vlc://` helper with mapped volume paths.
- iPhone
  Keep the phone as a pure client. The future iPhone app may reuse the fallback playback-session contract, but it is not the focus of the current desktop-first VLC work.

## What remains later

- richer packaging for the VLC opener helper beyond the current register-once scripts
- standalone VLC progress sync if a practical VLC-side callback path is added
- future iPhone VLC-oriented handoff using its own client path
