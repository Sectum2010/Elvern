# Backup And Recovery

Git commits protect Elvern code. They do not protect your live runtime state.

Elvern backup checkpoints are the runtime-state safety layer. They are meant to help an admin understand what can be recovered after a bad rescan, a broken shared local path update, or another damaging operational mistake.

Recovery is admin-only in the current product design. Standard-user scoped recovery is not implemented.

## What A Backup Checkpoint Includes

- a consistent SQLite snapshot created with the SQLite backup API
- `deploy/env/elvern.env` when included
- `backend/data/helper_releases/` when included
- `backend/data/assistant_uploads/` when included
- a `manifest.json` with hashes, metadata, and inspection details

SQLite uses WAL mode in Elvern, so the system uses the SQLite backup API instead of raw-copying only the `.db` file. That produces a consistent checkpoint snapshot without depending on ad hoc file copies.

## What A Backup Checkpoint Does Not Include

- movie files from the media library
- poster library files
- transcode or browser playback cache files
- runtime logs
- `.venv`
- `frontend/node_modules`
- `frontend/dist`

That means backup checkpoints protect Elvern runtime state, not your media collection itself.

## Secrets Warning

Backup checkpoints may contain secrets.

That can include:
- values from `deploy/env/elvern.env`
- Google Drive refresh tokens and other sensitive runtime data stored in SQLite

Do not commit or casually share backup checkpoint directories.

## Automatic Safety Checkpoints

Elvern creates automatic checkpoints before these dangerous actions:

- admin manual rescan
- shared local library path update

Those checkpoints are meant to give an admin a known recovery point before Elvern mutates live runtime state.

Automatic checkpoints are best-effort by default.

If automatic checkpoint creation fails, Elvern reports or audits the warning but still continues the requested admin action. Backup failure does not block the rescan or the shared local library path update by default.

## Current Recovery Limitation

Restore is not implemented yet.

This stage adds a dry-run recovery plan only. It can inspect a checkpoint, verify its files and database snapshot, compare checkpoint metadata against the current live environment, and explain what recovery would involve. It does not overwrite the live database, env file, helper releases, or assistant uploads.

## Current Workflow

Use these commands to work with checkpoints:

```bash
./scripts/create-backup-checkpoint.sh
./scripts/inspect-backup-checkpoint.sh /path/to/checkpoint
./scripts/list-backup-checkpoints.sh
./scripts/prune-backup-checkpoints.sh --keep-auto 10
./scripts/plan-backup-restore.sh /path/to/checkpoint
```

The restore-plan command is intentionally non-destructive. It is there to answer:

- is this checkpoint valid
- what runtime state does it contain
- what does it not contain
- how does it differ from the current live environment
- what manual recovery steps would be required

## Admin Recovery Panel

Elvern now exposes backup visibility inside the existing Admin page at:

- Admin -> Recovery

That panel is admin-only and can:

- create a manual server-local backup checkpoint
- list recent checkpoints
- inspect a checkpoint
- generate a dry-run restore plan

It does not:

- restore anything
- download or export backups through the browser
- encrypt checkpoints
- expose standard-user recovery workflows

Backups stay server-local in this stage. For off-host protection, copy checkpoint folders from `backend/data/backups/` to an external drive, NAS, or other secure storage.

## Future Direction

If this backup foundation proves reliable in practice, a future stage can add an explicit restore flow with strong confirmation gates and a clear separation between dry-run planning and destructive recovery.
