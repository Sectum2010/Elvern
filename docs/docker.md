# Docker Deployment

This first Docker path keeps Elvern's current production shape:

- one container
- FastAPI backend on port `8000`
- the existing Node-based frontend production server on port `4173`
- `ffmpeg` and `ffprobe` installed inside the image
- the desktop VLC helper remains client-side and is not part of the container

## 1. Prepare the env file

Create the runtime env file if you do not have one already:

```bash
cd "$ELVERN_ROOT"
cp deploy/env/.env.example deploy/env/elvern.env
```

Edit `deploy/env/elvern.env` and set the usual Elvern values:

- `ELVERN_ADMIN_USERNAME`
- either `ELVERN_ADMIN_PASSWORD_HASH` or `ELVERN_ADMIN_BOOTSTRAP_PASSWORD`
- `ELVERN_SESSION_SECRET`
- `ELVERN_PUBLIC_APP_ORIGIN`
- `ELVERN_BACKEND_ORIGIN`
- `ELVERN_COOKIE_SECURE`
- playback and library mapping values that match your clients

For the simplest plain HTTP first run, use:

- `ELVERN_PUBLIC_APP_ORIGIN="http://<host>:4173"`
- `ELVERN_BACKEND_ORIGIN="http://<host>:8000"`
- `ELVERN_COOKIE_SECURE="false"`

Replace `<host>` with your Docker host's IP address or hostname.

If you publish Elvern over real HTTPS later, switch `ELVERN_COOKIE_SECURE` back to `"true"` and update the two origins to the real private URLs clients use.

Important container-path note:

- `docker-compose.yml` overrides `ELVERN_MEDIA_ROOT`, `ELVERN_DB_PATH`, `ELVERN_TRANSCODE_DIR`, `ELVERN_HELPER_RELEASES_DIR`, `ELVERN_FFMPEG_PATH`, and `ELVERN_FFPROBE_PATH` so they point at the correct in-container paths
- keep editing the rest of the normal `ELVERN_*` values in `deploy/env/elvern.env`

Important direct-path note:

- `ELVERN_MEDIA_ROOT` inside the container is `/media`
- `ELVERN_LIBRARY_ROOT_LINUX`, `ELVERN_LIBRARY_ROOT_WINDOWS`, and `ELVERN_LIBRARY_ROOT_MAC` are still client-visible target paths, not container paths
- for example, if the server container mounts the host media folder at `/media`, but your Linux desktop client sees the same files at `/srv/media/movies`, then keep `ELVERN_LIBRARY_ROOT_LINUX="/srv/media/movies"`

## 2. Edit the media bind mount

`docker-compose.yml` mounts a host media folder into the container read-only:

```yaml
- ${ELVERN_DOCKER_MEDIA_PATH:-/srv/media/movies}:/media:ro
```

Use either approach:

1. Edit `docker-compose.yml` and replace `/srv/media/movies` with your real host media path.
2. Or export `ELVERN_DOCKER_MEDIA_PATH` before launch.

Example:

```bash
export ELVERN_DOCKER_MEDIA_PATH="/srv/media/movies"
```

Elvern data persists under the repo-local bind mount:

```text
./docker-data/data
```

That directory stores:

- `elvern.db`
- transcode cache data
- helper release metadata

## 3. Launch

Primary happy path:

```bash
cd "$ELVERN_ROOT"
docker compose up --build
```

Detached mode:

```bash
docker compose up --build -d
```

The app is then available at:

- frontend app: `http://<host>:4173`
- backend API: `http://<host>:8000`

## 4. First-run checks

1. Open `http://<host>:4173`
2. Sign in with the bootstrap admin credentials
3. Open the Library page and rescan
4. Confirm the media library is visible
5. Confirm `Open in VLC` still targets the client-side helper flow you expect

## Notes and limits

- This is an all-in-one container on purpose. It does not split backend and frontend into separate services yet.
- This path does not package VLC or the desktop helper into the container.
- If you publish Elvern behind Tailscale Serve or another reverse proxy, keep `ELVERN_PUBLIC_APP_ORIGIN` and `ELVERN_BACKEND_ORIGIN` aligned with the real private URLs clients will use.
- The simplest persisted state path is `./docker-data/data`; you can replace it with another bind mount or a named volume later if you prefer.
