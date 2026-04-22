# Elvern

Elvern is a private, self-hosted home media web app scaffold for family use over Tailscale. The current scaffold includes:

- FastAPI backend with multi-user cookie-based auth
- React + Vite frontend with PWA installability basics
- SQLite database
- Recursive local media scanning with `ffprobe`
- Automatic direct-play vs HLS fallback playback handling
- Desktop-first VLC handoff with one canonical private DGX server URL for all desktop clients
- Config-driven Linux / Windows / macOS path mapping for VLC targets
- Distributable `elvern-vlc://` desktop opener bundles for installed VLC on Windows/macOS
- Plain admin tools for user management, session revocation, and audit log review
- Short-lived playback tokens for VLC fallback URLs, tied to the authenticated Elvern session
- Native playback session API retained for current iPhone external playback handoff and future mobile-native reuse
- Playback progress save and resume
- `systemd` units for auto-start and restart
- Ubuntu launcher/control scripts so daily use does not require manual backend/frontend commands

Normal deployment model:

- one Elvern host
- one canonical private app URL for Linux, Windows, and macOS clients
- one matching private backend API origin for desktop helper handoff
- installed VLC as the primary desktop playback plane

## Repo Layout

```text
backend/      FastAPI app, SQLite schema, auth, scanning, streaming, VLC handoff
frontend/     React app, Vite config, service worker, production proxy server
clients/      installed-VLC opener helper and future platform-specific clients
deploy/       systemd unit files and env template
scripts/      bootstrap and rescan helpers
docs/         setup, architecture, and operations guides
```

## Quick Start

```bash
cd "$ELVERN_ROOT"
./scripts/setup-ubuntu.sh --install-packages
```

Then:

1. Edit `deploy/env/elvern.env`
2. For private-network testing, set `ELVERN_PUBLIC_APP_ORIGIN="https://example.tailnet.ts.net"` and `ELVERN_BACKEND_ORIGIN="http://example-private-host:8000"`, then replace those later with your real private hostname/origin pair
3. Generate an admin password hash with `.venv/bin/python -m backend.app.cli hash-password "your-password"`
4. Launch Elvern with `./scripts/elvern-start.sh --open-browser` or the installed desktop launcher

## Docker Quick Start

For a first-pass self-hosted container deployment, Elvern now includes a simple all-in-one Docker path that keeps the current backend plus production-frontend split intact.

1. Create `deploy/env/elvern.env` from `deploy/env/.env.example`
2. Set the usual `ELVERN_*` values there for admin credentials, session secret, and private origins. For a plain-HTTP first run, set `ELVERN_PUBLIC_APP_ORIGIN="http://<host>:4173"`, `ELVERN_BACKEND_ORIGIN="http://<host>:8000"`, and `ELVERN_COOKIE_SECURE="false"`
3. Edit the media bind mount in `docker-compose.yml` or export `ELVERN_DOCKER_MEDIA_PATH`
4. Launch with:

```bash
cd "$ELVERN_ROOT"
docker compose up --build
```

The default Docker ports are:

- frontend app: `http://<host>:4173`
- backend API: `http://<host>:8000`

Docker-specific notes:

- the container includes `ffmpeg` and `ffprobe`
- the desktop VLC helper remains client-side and is not part of the server container
- database and transcode/cache data persist under `./docker-data/data`
- your mounted media library is exposed inside the container at `/media`

See `docs/docker.md` for the full Docker setup notes.

Normal desktop playback flow:

1. Browse your library in Elvern.
2. Click `Open in VLC`.
3. On Linux, Windows, or macOS, use the same DGX Elvern URL in the browser.
4. After one-time helper registration, Elvern launches installed VLC on that client with the mapped direct source when configured.

Windows/macOS client helper packaging:

```bash
cd "$ELVERN_ROOT/clients/desktop-vlc-opener"
./scripts/publish-bundles.sh
```

That creates client-installable helper bundles under `clients/desktop-vlc-opener/artifacts/packages/`.

Admin workflow:

1. Sign in with the bootstrap admin account.
2. Open `Admin`.
3. Create family user accounts, disable or re-enable them, revoke sessions, and inspect recent audit events.

For the full flow and advanced options, follow:

- `docs/setup.md`
- `docs/docker.md`
- `docs/architecture.md`
- `docs/operations.md`
- `clients/desktop-vlc-opener/README.md`
