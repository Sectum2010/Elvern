# Elvern Setup

These commands assume the project lives at `$ELVERN_ROOT` on your Linux host, for example `/opt/elvern`.

Use this guide for the server-side Linux host path. If you want the all-in-one container path instead, use `docs/docker.md`.

## 1. Recommended Linux host install

Use `./install.sh` as the primary friendly entrypoint for the current Ubuntu/Linux host flow:

```bash
cd "$ELVERN_ROOT"
./install.sh
```

Useful follow-ups:

- `./install.sh --help` shows unattended and advanced flags.
- `./install.sh --install-packages --enable-now` is a common Ubuntu/systemd first run.
- `./install.sh --unattended ...` is available when you want explicit flag-driven setup.

What `install.sh` does at a high level:

- bootstraps `deploy/env/elvern.env` from `deploy/env/.env.example` when needed
- prompts for or accepts the main runtime values
- reuses the existing `scripts/setup-ubuntu.sh` and `scripts/install-systemd.sh` flows
- supports both interactive and unattended usage
- stays Linux-host oriented; desktop helper installation on playback clients remains a separate step

### Lower-level manual Ubuntu/systemd path

If you want the older script-first path directly, or you are troubleshooting below `install.sh`, use:

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

If `deploy/env/elvern.env` does not exist yet, `./install.sh` creates it from `deploy/env/.env.example`. The lower-level setup script can also create it.

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

Desktop helper installation still happens separately on each playback client. The server-side Linux install flow does not register or install Windows/macOS/Linux client helpers for you.

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
- browser playback remains built in as the first-class path for weaker or less stable connections
- `Lite Playback` is the quick-start browser mode and is intended to begin once roughly the first 45 seconds are ready
- `Full Playback` is intended to wait for a larger browser-ready threshold aimed at smoother full-movie playback

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

1. On the Elvern server, validate the Windows package in staging:

```bash
cd "$ELVERN_ROOT/clients/desktop-vlc-opener"
export ELVERN_BACKEND_ORIGIN="https://the-effective-helper-backend-origin.example"
./scripts/publish-bundles.sh --platform windows
```

This does not publish or replace the active release manifest. Normal release
activation builds all three platform packages together:

```bash
./scripts/publish-bundles.sh \
  --activate \
  --active-dir "$ELVERN_ROOT/backend/data/helper_releases"
```

Use the exact directory configured as `ELVERN_HELPER_RELEASES_DIR`; for Docker this
is the host-mounted `"$ELVERN_ROOT/docker-data/data/helper_releases"`, corresponding
to `/data/helper_releases` inside the container. Staging output is never the runtime
release authority.

2. Download the active Windows package from Elvern's Install page.
3. Unzip it.
4. Double-click `Install-ElvernVlcOpener.cmd`. The verified package includes its
   runtime and installs per user.

macOS one-time setup:

1. On the Elvern server, validate the macOS package in staging:

```bash
cd "$ELVERN_ROOT/clients/desktop-vlc-opener"
export ELVERN_BACKEND_ORIGIN="https://the-effective-helper-backend-origin.example"
./scripts/publish-bundles.sh --platform macos
```

This does not publish or replace the active release manifest. Use a complete
`./scripts/publish-bundles.sh --activate --active-dir
"$ELVERN_ROOT/backend/data/helper_releases"` release, or the equivalent configured
runtime directory, before ordinary users download it.

2. Download the active macOS package from Elvern's Install page.
3. Unzip it.
4. Double-click `Install-ElvernVlcOpener.command`. It verifies the complete package
   tree, selects Apple Silicon or Intel locally, and installs to `~/Applications`
   without Python, a separate .NET Runtime, or `sudo`.

Remote Linux one-time setup:

```bash
cd "$ELVERN_ROOT/clients/desktop-vlc-opener"
export ELVERN_BACKEND_ORIGIN="https://the-effective-helper-backend-origin.example"
./scripts/publish-bundles.sh --platform linux
```

This validates a Linux universal ZIP in staging without publishing it. After a
complete `--activate` release, download the active Linux package from Elvern,
unzip it, and run `./Install-ElvernVlcOpener.sh`. It verifies the complete package
tree, selects x64/ARM64 and glibc/musl locally, installs under `~/.local`, and needs
neither Python, a separate .NET Runtime, nor `sudo`. Linux sessions on the Elvern
host itself continue to use host VLC directly and do not need the Helper. Flatpak
VLC is not supported in this release.

Helper uninstall is per-user and transactional on Windows, macOS, and Linux. Linux
restores a recorded safe previous `elvern-vlc://` handler only if Elvern remains the
current default and that previous desktop entry still exists. If the user selected
another handler later, uninstall leaves that choice untouched.

All standard packages are self-contained .NET 10 packages. Publishing fails rather than silently producing incomplete packages when an SDK or RID runtime pack is unavailable.
The release manifest also binds packages to the canonical effective backend origin;
the server fails closed instead of offering a package built for another origin.

Temporary manual testing path if you are not packaging yet:

These repository-checkout commands are development-only and require the .NET 10 SDK. They are not the normal user install path.

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

Linux repository-checkout registration is only for development. Normal remote Linux users should use the universal package; same-host Linux needs no Helper:

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
10. Open a direct-play-safe MP4 and confirm browser playback still works when you choose `Lite Playback` or `Full Playback`.
11. Open an incompatible file such as an MKV and confirm browser playback still reaches a usable browser session when you choose `Lite Playback` or `Full Playback`.
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
