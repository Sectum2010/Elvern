# Contributing to Elvern

Thanks for being interested in Elvern. This is a small, actively-developed project maintained by one person ([@Sectum2010](https://github.com/Sectum2010)). Outside contributions are genuinely welcome and reviewed seriously — but the project has a clear shape, a clear threat model, and a few hard rules. Please skim this whole file before opening anything more than a typo fix.

If you have a question before you write code, the fastest path is opening a GitHub issue. Direct contact at `samuel.yang.yrx@gmail.com` is also fine for design questions you'd rather not discuss in public.

---

## What Elvern is

A self-hosted, private media library and adaptive playback engine. FastAPI + React + SQLite, designed to live inside a Tailscale (or equivalent WireGuard) tailnet, sized for a single household. See the [README](README.md) for the long version.

## What Elvern is not

These are non-negotiable, not just current-state:

- **Not a public streaming service.** No public sign-up, no anonymous sharing links, no federation.
- **Not a content-sharing or piracy platform.** Elvern streams media you already own to people you already trust.
- **Not a Plex/Jellyfin replacement.** It is intentionally smaller. The goal is fluent playback inside a private perimeter, not feature parity with anyone.
- **Not a framework.** Don't add abstractions in search of a use case.

PRs that drift toward any of the above will be declined no matter how clean the diff is.

---

## Before you open a PR

### Open an issue first if your change is non-trivial

Direct PRs are welcome for:

- Typos, broken links, doc clarifications.
- Obvious one-line bug fixes.
- Test additions for existing behavior.

Open an issue **first** for everything else, especially:

- New features or new endpoints.
- Refactors that move code between files or change file ownership.
- Anything that touches the security-sensitive paths listed below.
- UI/UX changes that affect existing flows.

The issue doesn't need to be a design doc. A few sentences on what you want to do and why is enough. The point is to surface "we already tried that" or "I'd rather solve it differently" before you've spent your evening on a 400-line diff.

### Run the local checks

CI runs the same things; running them locally first saves a round trip.

**Backend (Python 3.12):**
```bash
python -m pip install -r backend/requirements-test.txt
python -m pytest
```

**Frontend (Node 20):**
```bash
cd frontend
npm ci
npm test
npm run build
```

**Desktop helper (.NET 8) — only if you touched [clients/desktop-vlc-opener/](clients/desktop-vlc-opener/):**
```bash
dotnet build clients/desktop-vlc-opener/Elvern.VlcOpener.csproj --configuration Release
```

A red CI build will not be merged.

---

## Branch and PR workflow

1. Fork the repo.
2. Create a feature branch off `main`. Name it for the change (`fix-vlc-handoff-revoke`, not `patch-1`).
3. Open a PR against `Sectum2010/Elvern:main`.
4. Use a conventional-commit prefix in the PR title: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`. Match the existing `git log` style.
5. Fill out the [PR template](.github/PULL_REQUEST_TEMPLATE.md) — it's short.

Merge style is decided per-PR; you don't need to squash your branch yourself.

---

## Security-sensitive paths — issue first, always

Changes to these files or directories require a prior issue with the design discussed before any code review:

- [backend/app/auth.py](backend/app/auth.py) — sessions, login, revoke
- [backend/app/security.py](backend/app/security.py) — password hashing, token generation, rate limiting
- [backend/app/routes/auth.py](backend/app/routes/auth.py) — login/logout/heartbeat endpoints
- [backend/app/routes/admin.py](backend/app/routes/admin.py), [backend/app/routes/admin_assistant.py](backend/app/routes/admin_assistant.py) — admin surfaces
- [backend/app/routes/download.py](backend/app/routes/download.py), [backend/app/services/account_access_service.py](backend/app/services/account_access_service.py) — download sessions, invite codes
- [backend/app/routes/native_playback.py](backend/app/routes/native_playback.py), [backend/app/services/native_playback_service.py](backend/app/services/native_playback_service.py) — VLC/Infuse handoff
- [backend/app/routes/desktop_playback.py](backend/app/routes/desktop_playback.py), [backend/app/services/desktop_playback_service.py](backend/app/services/desktop_playback_service.py), [backend/app/routes/desktop_helper.py](backend/app/routes/desktop_helper.py)
- [backend/app/services/backup_service.py](backend/app/services/backup_service.py) — backup contents include secrets
- [backend/app/services/cloud_provider_auth_service.py](backend/app/services/cloud_provider_auth_service.py), [backend/app/services/google_drive_service.py](backend/app/services/google_drive_service.py) — OAuth flow
- [backend/app/services/audit_service.py](backend/app/services/audit_service.py) — audit trail
- [backend/app/media_stream.py](backend/app/media_stream.py) — path-traversal guard

The same applies to anything that adds, removes, or changes a cookie, header, token, or session lifetime, anywhere in the codebase.

If you found a vulnerability, **don't open an issue or PR for it**. Use GitHub Private Vulnerability Reporting at https://github.com/Sectum2010/Elvern/security/advisories/new, or email `samuel.yang.yrx@gmail.com` if you cannot use GitHub PVR. See [SECURITY.md](SECURITY.md) for the full private reporting flow.

---

## AI-assisted contributions

AI-assisted PRs (Claude, Codex, Copilot, ChatGPT, etc.) are allowed under three conditions:

1. **Disclose** in the PR description. Say which tool you used and for what (whole diff, scaffolding, comments only, etc.).
2. **You read and understood the diff yourself.** "The model said it should work" is not a review. If you can't explain a hunk to me, I can't merge it.
3. **You followed [docs/CODEX_CORE_GUARDRAILS.md](docs/CODEX_CORE_GUARDRAILS.md).** Especially the password-autofill rule (no autofill on any non-login secret field) and the download-security reality (the goal is server-side authorization, not hiding requests from a legitimate user's DevTools).

The PR template has a checkbox for this. The honor system is the system.

---

## Coding style

Defer to the patterns already in the codebase. Specifically:

- **Backend:** small functions, services-first organization, raw `sqlite3` with parameterized queries (no ORM), `from __future__ import annotations`, type hints where they help, dataclasses for plain data. Don't add new dependencies without an issue.
- **Frontend:** function components, hooks, plain CSS / CSS modules, no global state framework, `apiRequest()` from [frontend/src/lib/api.js](frontend/src/lib/api.js) for every fetch (so `credentials: "include"` stays universal). Don't introduce a UI library mid-feature.
- **Tests:** colocated with the code they cover. Backend tests in [backend/tests/](backend/tests/), frontend tests as `*.test.js` next to the source. New behavior needs a new test; bug fixes need a regression test.
- **Comments:** explain *why*, not *what*. The handbook in [docs/CODEX_ENGINEERING_HANDBOOK.md](docs/CODEX_ENGINEERING_HANDBOOK.md) is the longer version of this.

A small refactor to make your fix safer is fine. A large refactor in the same PR is not — split it.

---

## Code of conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md). Read it; it's short.

---

## Questions

GitHub issues are the default. For things you'd rather not discuss in public, `samuel.yang.yrx@gmail.com` is fine. Security reports should use GitHub Private Vulnerability Reporting at https://github.com/Sectum2010/Elvern/security/advisories/new, or email `samuel.yang.yrx@gmail.com` if you cannot use GitHub PVR.
