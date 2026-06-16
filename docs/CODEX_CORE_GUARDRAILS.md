# Codex Core Guardrails

This repo uses deadchecks for user-visible core paths. They are required before accepting changes that touch Detail rendering, mobile/browser playback lifecycle, native handoff, desktop VLC handoff, or the backend services/routes that feed those paths.

## Password Autofill / Non-login Secret Fields

- `LoginPage` is the only place allowed to use browser/password-manager login autofill.
- No admin modal, destructive confirmation, user-management action, signup page, update-password form, delete-user confirmation, self-delete flow, invite-code form, download setting, or admin setting may auto-fill saved passwords.
- `autocomplete="current-password"` is forbidden outside `LoginPage`.
- `name="password"` and `id="password"` are forbidden outside `LoginPage`.
- Admin re-auth and destructive action fields must use the hardened non-login secret input.
- Secret values must be cleared on cancel, close, submit, failed submit when safest, and unmount, and must never be stored in `localStorage` or `sessionStorage`.
- Every future PR touching password fields must run the autofill static regression test.
- Any future UI review must manually verify that non-login fields open empty and are not password-manager prefilled.
- This rule exists because accidental admin-password autofill in destructive actions is a major security risk.

## Download Security / Network Inspect Reality

- Elvern cannot hide a download request from the authorized user's own browser DevTools.
- Elvern is not DRM and does not apply per-file client-side encryption; authorized users who receive raw movie bytes can copy the downloaded file afterward.
- The download feature's security goal is to prevent unauthorized users, devices, accounts, and revoked sessions from reusing a captured URL or token to download movies they were not granted.
- Download protections must stay server-side: auth-session binding, user binding, media-item binding, download-grant checks, hidden and source visibility checks, Range re-checks, and revoke or terminate checks.
- Download bytes are encrypted in transit only when Elvern is served over HTTPS or another encrypted private transport.
- Inspect Network by the authorized user cannot be prevented; server-side authorization prevents unauthorized replay.
- Request logs and diagnostics must not expose raw download tokens or token hashes. If a path has to be logged, redact `/api/download/sessions/<anything>` as `/api/download/sessions/[redacted]`.

## Backup Plaintext Safety

- Default, Admin UI, and Admin API backup creation must stay encrypted-only.
- CLI plaintext checkpoint creation is unsafe and must require both `--output-dir`
  and `--allow-plaintext-backup`.
- Service-level plaintext checkpoint creation must require
  `allow_plaintext_backup=True`.
- Legacy plaintext backup inspect and restore-plan support may remain, but do not
  delete, move, or auto-migrate real backup files without explicit operator
  approval.

## Core Regression Matrix

- Detail page: the page must render, not blank, and expose the expected playback/handoff actions.
- Mobile Lite playback: a session must be created, attach must occur, a first-frame/playable state must be reached, and `currentTime` must advance.
- Mobile Full playback: same as Lite; a prepared or ready backend session is not enough.
- VLC handoff: native session creation and ranged stream access must remain valid.
- Infuse handoff: native session creation and ranged stream access must remain valid.
- Desktop VLC handoff: helper handoff creation and resolve must remain valid.

## Prepared Is Not Playable

Backend readiness signals are necessary but not sufficient. These are only prepared/contract signals:

- Session created.
- `attach_ready=true`, `mode_ready=true`, or `playback_commit_ready=true`.
- Manifest/init/segment files exist.
- Required URLs are present.

Playable proof requires browser/runtime evidence:

- A video element reaches `loadeddata`/`canplay` and `playing`, or the closest runtime equivalent.
- `currentTime` advances after attach.
- The source does not immediately reset/remount in a loop.
- Interaction does not cause immediate scroll/viewport jitter tied to playback lifecycle.

## Required Deadcheck Use

Run the backend deadcheck for changes touching:

- `backend/app/routes/library.py`
- `backend/app/routes/mobile_playback.py`
- `backend/app/routes/browser_playback.py`
- `backend/app/routes/native_playback.py`
- `backend/app/routes/desktop_playback.py`
- `backend/app/services/library_*`
- `backend/app/services/mobile_playback_*`
- `backend/app/services/native_playback_service.py`
- `backend/app/services/desktop_playback_service.py`

Run the browser deadcheck for changes touching:

- `frontend/src/pages/DetailPage.jsx`
- `frontend/src/features/playback/*`
- `frontend/src/lib/browserPlayback.js`
- `frontend/src/lib/api.js`
- mobile/browser playback backend routes/services

## Commands

Backend/API contracts:

```bash
cd /home/sectum/Projects/Elvern
.venv/bin/python scripts/elvern-core-backend-deadcheck.py
```

Browser/runtime playable checks:

```bash
cd /home/sectum/Projects/Elvern
.venv/bin/python scripts/elvern-core-browser-deadcheck.py
```

If frontend code changed, build and restart first:

```bash
cd /home/sectum/Projects/Elvern/frontend
npm run build

cd /home/sectum/Projects/Elvern
./scripts/elvern-restart.sh
```

## Reporting Format

For future Codex tasks on these paths, report:

- Commands run.
- Backend/API deadcheck result.
- Browser/runtime deadcheck result.
- Whether Lite and Full reached actual playable state, not just prepared.
- Whether VLC, Infuse, and desktop VLC handoff contracts passed.
- Any skipped check and the exact reason it was skipped.

Do not claim playback is fixed or protected unless the report distinguishes prepared checks from playable checks.
