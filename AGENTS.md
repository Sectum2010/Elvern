# AGENTS.md

Guidance for AI coding agents working in this repository. Keep changes scoped, public-safe, and consistent with the project.

## Project Identity

Elvern is a self-hosted media library and playback control-plane app. It combines a FastAPI backend, a React/Vite PWA frontend, SQLite state, ffmpeg-based browser playback, and a desktop helper for installed VLC handoff.

Elvern is not meant to replace VLC as the desktop playback engine. The desktop premium path is `Open in VLC`: Elvern keeps library, auth, resume, and handoff context while VLC performs desktop playback. Browser playback is the universal web fallback path with Lite and Full modes; treat existing playback flows as sensitive and regression-prone.

## Architecture Overview

- `backend/`: FastAPI app, route modules, services, SQLite-backed state, playback/transcode control, auth, library scanning, cloud/library integrations, and tests under `backend/tests/`.
- `frontend/`: React 18 + Vite PWA with `react-router-dom`, `hls.js`, Vitest tests, and production serving through `server.mjs`.
- `clients/desktop-vlc-opener/`: .NET 8 desktop helper that resolves short-lived Elvern handoffs and launches the user's installed VLC.
- `scripts/`: local operations, diagnostics, lifecycle helpers, backup helpers, and CI mirror scripts.
- `deploy/` and `docker-compose.yml`: deployment/environment scaffolding and container launch configuration.
- `docs/`: project documentation and regression notes.

## Verified Commands

Use the narrowest relevant checks for the task. Prefer the repo's local CI mirror for broad validation.

Backend:

```bash
python -m pip install -r backend/requirements-test.txt
python -m pytest
```

Frontend:

```bash
cd frontend
npm ci
npm test
npm run build
npm run dev
```

Desktop VLC helper, only when `clients/desktop-vlc-opener/` is touched:

```bash
dotnet build clients/desktop-vlc-opener/Elvern.VlcOpener.csproj --configuration Release
```

Docker:

```bash
docker compose up --build -d
```

Local lifecycle and diagnostics:

```bash
./scripts/elvern-start.sh --open-browser
./scripts/elvern-restart.sh --open-browser
./scripts/elvern-status.sh
./scripts/elvern-logs.sh
./scripts/rescan.sh
./scripts/elvern-ci-local.sh --fresh
.venv/bin/python scripts/elvern-core-backend-deadcheck.py
.venv/bin/python scripts/elvern-core-browser-deadcheck.py
```

If a command is not listed here or in `README.md`, `CONTRIBUTING.md`, package files, compose files, or scripts, verify before use instead of guessing.

## UI/UX Rules

- Keep UI professional, clean, consistent, and calm.
- Prefer existing components, CSS patterns, and design tokens over one-off styles.
- Do not introduce random hard-coded colors, spacing, shadows, or ad hoc visual systems unless the task truly requires it.
- Every UI change should consider loading, empty, error, disabled, hover, and focus states when relevant.
- Check mobile, tablet, and desktop layouts before calling UI work done.
- Preserve established workflows and labels unless the user explicitly asks for a change.

## Motion Rules

- Use subtle motion only when it improves clarity, continuity, or feedback.
- Keep motion fluid, calm, and professional.
- Respect `prefers-reduced-motion`.
- Avoid flashy, slow, bouncy, jittery, or distracting animation.
- Do not animate in ways that block interaction, hide state changes, or create layout instability.

## Safety And Regression Rules

- Do not break existing playback flows.
- Do not break Linux same-host VLC behavior or desktop VLC helper handoff behavior.
- Do not change authentication, streaming, database schema, deployment behavior, cookie/session behavior, or token handling unless the task explicitly asks for it.
- Keep changes scoped to the user's request.
- Avoid broad refactors in the same change as a bug fix.
- For security-sensitive or playback-sensitive areas, add focused regression tests when behavior changes.
- Never include secrets, credentials, auth tokens, private network details, personal contact information, real media paths, or local machine-specific data in committed files.
- `.agents/` is local-only and ignored; do not copy local agent skill content into committed repo files.

## Done Criteria

When finishing a task:

- Explain what changed and why.
- List files changed.
- Run relevant checks when available.
- Report checks that were not run and why.
- State whether dependencies were installed, whether app behavior changed, and whether anything was committed or pushed.
