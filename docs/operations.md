# Elvern Operations

## Daily Use

Normal family workflow on the Elvern host:

1. Double-click `Elvern`.
2. The launcher starts backend/frontend if needed.
3. Your browser opens automatically.

For management, use one of these:

```bash
cd "$ELVERN_ROOT"
./scripts/elvern-control.sh
./scripts/elvern-status.sh
./scripts/elvern-restart.sh --open-browser
./scripts/elvern-stop.sh
```

The launcher scripts prefer systemd services when they are installed and fall back
to safe local background processes when they are not.

## Launcher Checks

The launcher workflow checks:

- `deploy/env/elvern.env` exists
- the media root, session secret, and admin password config look sane
- `.venv` and the built frontend exist
- `ffmpeg` / `ffprobe` availability
- whether Elvern systemd units already exist
- whether the backend/frontend ports are already healthy before starting anything

Install or refresh the desktop launchers:

```bash
cd "$ELVERN_ROOT"
./scripts/install-launchers.sh
```

## Service Management

Check service state:

```bash
cd "$ELVERN_ROOT"
./scripts/check-systemd.sh
```

Install or update systemd services:

```bash
cd "$ELVERN_ROOT"
./scripts/install-systemd.sh --scope system --enable-now
```

Remove systemd services if needed:

```bash
cd "$ELVERN_ROOT"
./scripts/uninstall-systemd.sh --scope system
```

Restart after config or code changes:

```bash
cd "$ELVERN_ROOT/frontend"
npm run build
cd "$ELVERN_ROOT"
./scripts/elvern-restart.sh --open-browser
```

## Logs

Quick recent logs:

```bash
cd "$ELVERN_ROOT"
./scripts/elvern-logs.sh
```

Deep service logs:

```bash
sudo journalctl -u elvern-backend.service -f
sudo journalctl -u elvern-frontend.service -f
```

## Manual Rescan

Run a synchronous rescan from the server shell:

```bash
cd "$ELVERN_ROOT"
./scripts/rescan.sh
```

Or use the `Rescan library` button inside the app.

## Health Checks

Backend health:

```bash
curl "${ELVERN_BACKEND_ORIGIN}/health"
```

Frontend through the canonical private Elvern app origin:

```bash
curl "${ELVERN_PUBLIC_APP_ORIGIN}/health"
```

For explicit local-development fallback only, you can still probe loopback directly:

```bash
curl "http://127.0.0.1:8000/health"
curl "http://127.0.0.1:4173/health"
```

## Playback Diagnostics

Watch playback and transcoding logs:

```bash
sudo journalctl -u elvern-backend.service -f | rg "Playback|ffmpeg|transcode|HLS"
```

Backend browser-playback endpoints for a signed-in browser session:

- `GET /api/playback/:id` decides direct vs HLS
- `POST /api/playback/:id/start` starts or reuses an HLS transcode when needed
- `GET /api/hls/:id/index.m3u8` serves the manifest
- `GET /api/hls/:id/:segment` serves HLS segments

Desktop VLC playback endpoints:

- `GET /api/desktop-playback/:id` resolves the preferred VLC target for Linux, Windows, or macOS
- `POST /api/desktop-playback/:id/open` launches installed VLC directly on the Elvern Linux host for same-host playback
- `POST /api/desktop-playback/:id/handoff` creates a short-lived opaque desktop opener handoff
- `GET /api/desktop-playback/handoff/:handoff_id?token=...` resolves that handoff for the registered desktop helper
- `GET /api/desktop-playback/:id/playlist` downloads a VLC `.xspf` playlist using a mapped direct source path or a short-lived backend URL fallback

Admin endpoints:

- `GET /api/admin/users`
- `POST /api/admin/users`
- `PATCH /api/admin/users/:id`
- `GET /api/admin/sessions`
- `POST /api/admin/sessions/:id/revoke`
- `GET /api/admin/audit`

Native playback session endpoints remain in use for iPhone external-player handoff and future mobile-native reuse:

- `POST /api/native-playback/:id/session`
- `GET /api/native-playback/session/:session_id`
- `GET /api/native-playback/session/:session_id/stream`
- `POST /api/native-playback/session/:session_id/heartbeat`
- `POST /api/native-playback/session/:session_id/progress`
- `POST /api/native-playback/session/:session_id/close`

## Tailscale Notes

Recommended exposure model:

- set one canonical private app URL in `ELVERN_PUBLIC_APP_ORIGIN`
- set one matching private backend API origin in `ELVERN_BACKEND_ORIGIN`
- Linux, Windows, and macOS clients all browse to the same app URL
- desktop helpers resolve against the matching backend API origin
- keep `ELVERN_PRIVATE_NETWORK_ONLY="true"` as the normal operating mode

This keeps Elvern private and avoids exposing a raw public listener. MagicDNS can then give family devices a stable machine name inside Tailscale.

Before using Tailscale Serve, set:

- `ELVERN_PUBLIC_APP_ORIGIN` to the private HTTPS origin you want clients to use
- `ELVERN_BACKEND_ORIGIN` to a private backend API origin the desktop helper can reach
- `ELVERN_COOKIE_SECURE="true"`

Then serve the frontend over tailnet HTTPS:

```bash
sudo tailscale serve --bg 4173
sudo tailscale serve status
```

With current Tailscale Serve syntax, `tailscale serve --bg 4173` publishes the local frontend on tailnet HTTPS and keeps it private to the tailnet. The app is designed so the frontend can sit behind that HTTPS entry point and continue proxying `/api/*` to the backend locally.

## Playback Behavior

Elvern now has two main playback paths, with one private Elvern server URL for every desktop client:

- desktop Linux: `Open in VLC` launches installed VLC on that Linux client through the same DGX private server URL
- desktop Windows/macOS: `Open in VLC` uses the lightweight `elvern-vlc://` helper to launch installed VLC with the mapped direct source when configured
- desktop Windows/macOS fallback: if no direct mapping exists, the helper can still open installed VLC with a short-lived backend URL
- browser playback remains a first-class path for weaker or less stable connections

In current product language:

- `Open in VLC` is preferred on strong home, local, or stable Wi-Fi conditions because it best preserves original quality, subtitle handling, audio-track selection, and local-player behavior
- `Lite Playback` is the quick-start browser mode and is intended to begin once roughly the first 45 seconds are ready
- `Full Playback` is intended to wait for a larger browser-ready threshold aimed at smoother full-movie playback

Typical direct-play case:

- `mp4`
- `h264`
- `aac`

Typical HLS fallback case:

- `mkv`
- doubtful remuxes
- unsupported audio for Safari
- anything the decision layer does not trust for the current browser profile

For Windows/macOS mapping, configure the platform roots so Elvern can translate:

- Linux path under `ELVERN_MEDIA_ROOT`
- Windows mapped path under `ELVERN_LIBRARY_ROOT_WINDOWS`
- macOS mapped path under `ELVERN_LIBRARY_ROOT_MAC`

Elvern computes the path relative to `ELVERN_MEDIA_ROOT` and joins that relative path under the platform-specific root.

For VLC fallback URLs, Elvern now uses short-lived playback tokens tied to the requesting authenticated web session. Revoking the Elvern session or disabling the user invalidates those fallback playback URLs.

For desktop helper handoffs, Elvern now uses a separate short-lived opaque handoff token. The browser only sees the handoff token and helper URL, not the mapped filesystem path itself.

## Multi-user And Audit

- every family member should have their own Elvern account
- `Continue Watching` and saved progress are per-user
- admin can create users, disable or re-enable them, and revoke active sessions
- audit records capture login success/failure, logout, playback handoff creation, direct VLC launch, user changes, session revocation, and manual rescans
- audit records include timestamp, user, action, target, IP when available, and outcome

## Troubleshooting

If the double-click launcher does not start Elvern:

1. Run `./scripts/elvern-status.sh` and confirm the env file and media root are valid.
2. Run `./scripts/elvern-logs.sh` and inspect the launcher/backend/frontend logs.
3. Re-run `./scripts/setup-ubuntu.sh` to rebuild missing runtime pieces.
4. Confirm `ffmpeg`, `ffprobe`, `node`, and `.venv/bin/uvicorn` exist.
5. If you are relying on boot-time services, run `./scripts/check-systemd.sh`.

If direct play fails for a file that looked safe, the frontend will try to force an HLS fallback automatically. If playback still fails:

1. Confirm `ffmpeg` and `ffprobe` are installed and match the paths in `deploy/env/elvern.env`.
2. Check the Settings page for `Playback compatibility` status and the last playback error.
3. Watch backend logs while opening the movie and confirm whether Elvern chose `direct` or `hls`.
4. On iPhone Safari, prefer the Tailscale Serve HTTPS URL instead of plain local HTTP.
5. Make sure the transcode cache directory exists and is writable by the service user.

If `Open in VLC` fails on the DGX Linux host:

1. Confirm `vlc` is installed and `ELVERN_VLC_PATH_LINUX` points at the real binary.
2. Confirm `ELVERN_LIBRARY_ROOT_LINUX` matches the real local media root that VLC can open.
3. Open a movie detail page from a browser running on the DGX host itself and click `Open in VLC`.
4. Confirm browser playback stops immediately after the click so only VLC keeps playing.
5. If needed, download the VLC playlist from the detail page and open it manually in VLC.
6. Check backend logs for `Launching VLC directly` or `Building direct-source VLC playlist`.

If Windows/macOS `Open in VLC` is not launching installed VLC yet:

1. Make sure `ELVERN_PUBLIC_APP_ORIGIN` and `ELVERN_BACKEND_ORIGIN` both point at the real DGX private hostname or MagicDNS name.
2. Build the helper in `clients/desktop-vlc-opener`.
3. Register the helper protocol once:
   - Windows: `.\scripts\register-protocol-windows.ps1`
   - macOS: `./scripts/register-protocol-macos.sh`
4. Set `ELVERN_LIBRARY_ROOT_WINDOWS` or `ELVERN_LIBRARY_ROOT_MAC`.
5. Reopen the detail page so Elvern resolves the mapped target again.
6. If mapping is still not ready, use the playlist fallback until the mapping is corrected.

If you need to revoke access:

1. Sign in as an admin user.
2. Open `Admin`.
3. Disable the user account or revoke one or more active sessions.
4. Any short-lived VLC fallback URLs tied to the revoked Elvern session will stop working automatically.
