# Elvern Setup

These commands assume the project lives at `$ELVERN_ROOT` on your Linux host, for example `/opt/elvern`.

## 1. Recommended one-time Ubuntu setup

Run the one-time setup script:

```bash
cd "$ELVERN_ROOT"
./scripts/setup-ubuntu.sh --install-packages
```

That script:

- verifies/install host dependencies
- creates `.venv` and installs backend requirements
- installs frontend dependencies and builds the production frontend
- builds the desktop VLC opener helper when `dotnet` is available
- creates launcher desktop entries for `Elvern` and `Elvern Control`

If you also want boot-time auto-start through systemd during setup, use:

```bash
cd "$ELVERN_ROOT"
./scripts/setup-ubuntu.sh --install-packages --install-systemd --enable-now
```

`ffmpeg` provides both `ffprobe` and the browser HLS fallback path. `vlc` is now the primary desktop playback target on Linux, and the setup flow should leave the host ready for `Open in VLC`.

## 2. Edit the runtime env file

If `deploy/env/elvern.env` does not exist yet, the setup script creates it from `deploy/env/.env.example`.

Edit `deploy/env/elvern.env` and set:

- `ELVERN_MEDIA_ROOT` to the real movie folder
- `ELVERN_PUBLIC_APP_ORIGIN` to the one private DGX app URL every desktop client should use
- `ELVERN_BACKEND_ORIGIN` to the matching private backend API origin every desktop helper should use
- `ELVERN_SESSION_SECRET` to a random secret
- keep `ELVERN_ENABLE_MULTIUSER="true"`
- keep `ELVERN_PRIVATE_NETWORK_ONLY="true"`
- either `ELVERN_ADMIN_PASSWORD_HASH` or `ELVERN_ADMIN_BOOTSTRAP_PASSWORD`
- confirm `ELVERN_FFMPEG_PATH` and `ELVERN_FFPROBE_PATH`
- confirm `ELVERN_TRANSCODE_DIR` points at an app-owned writable cache directory
- confirm `ELVERN_PLAYBACK_TOKEN_TTL_SECONDS` is short, such as `300`
- confirm `ELVERN_VLC_HELPER_PROTOCOL`, usually `elvern-vlc`
- confirm `ELVERN_VLC_PATH_LINUX`
- confirm `ELVERN_LIBRARY_ROOT_LINUX`
- set `ELVERN_LIBRARY_ROOT_WINDOWS` and `ELVERN_LIBRARY_ROOT_MAC` when you want mapped direct-source VLC playback on those platforms
- for real cross-platform use, do not leave the standard app/helper origins on loopback values

If you are testing only over explicit local-development loopback before setting up the real DGX private origin, temporarily set:

```bash
ELVERN_COOKIE_SECURE="false"
```

Generate a session secret:

```bash
openssl rand -hex 32
```

Generate a password hash:

```bash
cd "$ELVERN_ROOT"
. .venv/bin/activate
python -m backend.app.cli hash-password "replace-with-your-password"
```

## 3. Install or update systemd services

Recommended system-wide services:

```bash
cd "$ELVERN_ROOT"
./scripts/install-systemd.sh --scope system --enable-now
```

Alternative user-level services:

```bash
cd "$ELVERN_ROOT"
./scripts/install-systemd.sh --scope user --enable-now
sudo loginctl enable-linger your-user
```

Check the installed unit state at any time:

```bash
cd "$ELVERN_ROOT"
./scripts/check-systemd.sh
```

## 4. Normal daily use

After setup, the normal flow is:

1. Double-click `Elvern` from the app menu or Desktop.
2. Elvern starts backend/frontend if needed.
3. Your browser opens automatically.
4. Use `Elvern Control` if you want stop/restart/status/logs.

For family administration:

1. Sign in with the bootstrap admin account.
2. Open `Admin`.
3. Create user accounts for each family member.
4. Disable accounts or revoke sessions there if needed.

Command-line equivalents:

```bash
cd "$ELVERN_ROOT"
./scripts/elvern-start.sh --open-browser
./scripts/elvern-control.sh
./scripts/elvern-status.sh
```

## 5. Desktop playback defaults

Desktop playback is now VLC-first:

- `Open in VLC` is the recommended desktop button
- Linux, Windows, and macOS all open the same private Elvern server URL in the browser
- on Linux, Windows, and macOS, Elvern hands off to the lightweight `elvern-vlc://` helper after one-time registration
- the helper resolves the mapped direct source path when configured and launches installed VLC directly
- if no desktop path mapping exists yet, Elvern falls back to a short-lived backend URL, still through the helper
- VLC playlists remain as a secondary manual fallback, not the daily-use path
- browser playback remains built in as convenience mode only

Relevant env knobs:

```bash
ELVERN_ENABLE_MULTIUSER="true"
ELVERN_PRIVATE_NETWORK_ONLY="true"
ELVERN_PUBLIC_APP_ORIGIN="https://example.tailnet.ts.net"
ELVERN_BACKEND_ORIGIN="http://example-private-host:8000"
ELVERN_DESKTOP_PLAYBACK_MODE="vlc_direct"
ELVERN_PLAYBACK_TOKEN_TTL_SECONDS="300"
ELVERN_VLC_HELPER_PROTOCOL="elvern-vlc"
ELVERN_VLC_PATH_LINUX="/usr/bin/vlc"
ELVERN_LIBRARY_ROOT_LINUX="/srv/media/movies"
ELVERN_LIBRARY_ROOT_WINDOWS="Z:\\Movies"
ELVERN_LIBRARY_ROOT_MAC="/Volumes/Movies"
```

## 5A. Determine the real private server URL

On the Elvern host, determine the hostname you actually want every desktop client to use:

```bash
tailscale status
tailscale ip -4
hostname
```

Typical pattern:

- current private app URL: `https://example.tailnet.ts.net`
- current private backend API URL: `http://example-private-host:8000`
- later stable form: `https://example.tailnet.ts.net` and `http://example-private-host:8000`

After updating `deploy/env/elvern.env`, restart Elvern:

```bash
cd "$ELVERN_ROOT"
./scripts/elvern-restart.sh
./scripts/elvern-status.sh
```

Then verify:

- the browser opens the same `ELVERN_PUBLIC_APP_ORIGIN` on Linux, Windows, and macOS
- the Admin page shows the configured app URL and backend API URL

Windows one-time setup:

1. On the DGX server, build the Windows helper package:

```bash
cd "$ELVERN_ROOT/clients/desktop-vlc-opener"
./scripts/publish-bundles.sh --runtime win-x64
```

2. Copy `clients/desktop-vlc-opener/artifacts/packages/elvern-vlc-opener-<version>-win-x64.zip` to the Windows machine.
3. Unzip it.
4. Install the `.NET 8 Runtime` on that Windows machine if it is not already present.
5. Double-click `Install-ElvernVlcOpener.cmd`.

macOS one-time setup:

1. On the DGX server, build the macOS helper package:

```bash
cd "$ELVERN_ROOT/clients/desktop-vlc-opener"
./scripts/publish-bundles.sh --runtime osx-arm64
```

2. Copy `clients/desktop-vlc-opener/artifacts/packages/elvern-vlc-opener-<version>-osx-arm64.zip` to the Mac.
3. Unzip it.
4. Install the `.NET 8 Runtime` on that Mac if it is not already present.
5. Double-click `Install-ElvernVlcOpener.command`.

The default package path is now portable/framework-dependent because that is much more reliable to build from the DGX Linux host. If you intentionally want to try a self-contained same-RID package, use:

```bash
cd "$ELVERN_ROOT/clients/desktop-vlc-opener"
./scripts/publish-bundles.sh --runtime osx-arm64 --self-contained
```

That self-contained path may still fail when RID-specific runtime packs are unavailable on the DGX host.

Temporary manual testing path if you are not packaging yet:

Windows:

```powershell
cd C:\path\to\desktop-vlc-opener
dotnet build
.\scripts\register-protocol-windows.ps1
```

macOS:

```bash
cd /path/to/desktop-vlc-opener
dotnet build
./scripts/register-protocol-macos.sh
```

Linux helper registration is recommended too, because the standard daily-use model is one shared DGX Elvern URL plus local VLC handoff on every desktop client:

```bash
cd "$ELVERN_ROOT/clients/desktop-vlc-opener"
dotnet build
./scripts/register-protocol-linux.sh
```

Browser playback controls remain relevant too:

```bash
ELVERN_TRANSCODE_ENABLED="true"
ELVERN_TRANSCODE_DIR="/opt/elvern/backend/data/transcodes"
ELVERN_TRANSCODE_TTL_MINUTES="60"
ELVERN_MAX_CONCURRENT_TRANSCODES="1"
ELVERN_FFMPEG_PATH="/usr/bin/ffmpeg"
ELVERN_FFPROBE_PATH="/usr/bin/ffprobe"
```

## 6. First-run checks

After signing in:

1. Open the Library page.
2. Click `Rescan library`.
3. Wait for indexing to finish.
4. Open the same DGX Elvern URL from Linux, Windows, and/or macOS.
5. Open a movie detail page and click `Open in VLC`.
6. Confirm installed VLC launches on that client and opens the mapped direct source when configured.
7. Confirm VLC starts near the saved resume point when one exists.
8. On a Windows or macOS desktop that has the helper registered, confirm `Open in VLC` launches installed VLC without a manual copy/paste step.
9. If the platform mapping is intentionally left unset, confirm the helper still opens installed VLC using the short-lived backend URL fallback.
10. Open a direct-play-safe MP4 and confirm browser playback still works as fallback.
11. Open an incompatible file such as an MKV and confirm browser fallback still works when you choose it.
12. Create a second user from `Admin`, sign in as that user, and confirm progress/continue-watching are separate.
13. Pause, leave the page, reopen the movie, and confirm Elvern still shows the saved resume point for the signed-in user.

## 7. Advanced manual start commands

These are now troubleshooting commands, not the normal daily workflow.

Backend only:

```bash
cd "$ELVERN_ROOT"
set -a
. deploy/env/elvern.env
set +a
. .venv/bin/activate
uvicorn backend.app.main:app --host "$ELVERN_BIND_HOST" --port "$ELVERN_PORT"
```

Frontend only:

```bash
cd "$ELVERN_ROOT"
set -a
. deploy/env/elvern.env
set +a
cd frontend
npm run serve
```
