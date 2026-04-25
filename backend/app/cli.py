from __future__ import annotations

import argparse
import json
from pathlib import Path

from .auth import ensure_admin_user
from .config import refresh_settings
from .db import init_db
from .media_scan import scan_media_library
from .security import hash_password
from .services.backup_service import (
    build_restore_dry_run_plan,
    create_backup_checkpoint,
    inspect_backup_checkpoint,
    list_backup_checkpoints,
    prune_backup_checkpoints,
)
from .services.desktop_helper_service import import_helper_release_artifacts
from .services.status_service import get_system_status


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Elvern backend helper commands")
    subparsers = parser.add_subparsers(dest="command", required=True)

    hash_parser = subparsers.add_parser("hash-password", help="Generate a password hash")
    hash_parser.add_argument("password", help="Password to hash")

    helper_parser = subparsers.add_parser(
        "import-helper-releases",
        help="Import built desktop helper packages into the backend-hosted release catalog",
    )
    helper_parser.add_argument(
        "--channel",
        default=None,
        help="Release channel to import into. Defaults to ELVERN_HELPER_DEFAULT_CHANNEL.",
    )
    helper_parser.add_argument(
        "sources",
        nargs="+",
        help="Package files or directories to import",
    )

    subparsers.add_parser("rescan", help="Run a synchronous media rescan")
    subparsers.add_parser("status", help="Print current system status")

    backup_create_parser = subparsers.add_parser(
        "backup-create",
        help="Create a local backup checkpoint of runtime state",
    )
    backup_create_parser.add_argument(
        "--output-dir",
        default=None,
        help="Destination checkpoint directory. Defaults to backend/data/backups/elvern-backup-YYYYMMDD-HHMMSSZ.",
    )
    backup_create_parser.add_argument(
        "--no-env",
        action="store_true",
        help="Exclude deploy/env/elvern.env from the checkpoint.",
    )
    backup_create_parser.add_argument(
        "--no-helper-releases",
        action="store_true",
        help="Exclude backend/data/helper_releases from the checkpoint.",
    )
    backup_create_parser.add_argument(
        "--no-assistant-uploads",
        action="store_true",
        help="Exclude backend/data/assistant_uploads from the checkpoint.",
    )

    backup_inspect_parser = subparsers.add_parser(
        "backup-inspect",
        help="Inspect and verify a backup checkpoint manifest and files",
    )
    backup_inspect_parser.add_argument("path", help="Checkpoint directory or manifest path to inspect")

    backup_list_parser = subparsers.add_parser(
        "backup-list",
        help="List backup checkpoints from the backup directory",
    )
    backup_list_parser.add_argument(
        "--output-dir",
        default=None,
        help="Backup directory to inspect. Defaults to backend/data/backups/.",
    )

    backup_prune_parser = subparsers.add_parser(
        "backup-prune",
        help="Prune older automatic backup checkpoints while keeping manual checkpoints",
    )
    backup_prune_parser.add_argument(
        "--output-dir",
        default=None,
        help="Backup directory to prune. Defaults to backend/data/backups/.",
    )
    backup_prune_parser.add_argument(
        "--keep-auto",
        type=int,
        default=10,
        help="Number of newest automatic checkpoints to retain.",
    )

    backup_restore_plan_parser = subparsers.add_parser(
        "backup-restore-plan",
        help="Build a dry-run recovery plan for a backup checkpoint",
    )
    backup_restore_plan_parser.add_argument(
        "path",
        help="Checkpoint directory or manifest path to inspect for recovery planning",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command == "hash-password":
        print(hash_password(args.password))
        return

    if args.command == "backup-inspect":
        payload = inspect_backup_checkpoint(args.path)
        print(json.dumps(payload, indent=2))
        return

    settings = refresh_settings()

    if args.command == "backup-list":
        payload = {"checkpoints": list_backup_checkpoints(settings, backups_dir=args.output_dir)}
        print(json.dumps(payload, indent=2))
        return

    if args.command == "backup-prune":
        payload = prune_backup_checkpoints(
            settings,
            keep_auto=args.keep_auto,
            backups_dir=args.output_dir,
        )
        print(json.dumps(payload, indent=2))
        return

    if args.command == "backup-restore-plan":
        payload = build_restore_dry_run_plan(settings, args.path)
        print(json.dumps(payload, indent=2))
        return

    if args.command == "backup-create":
        payload = create_backup_checkpoint(
            settings,
            output_dir=args.output_dir,
            include_env=not args.no_env,
            include_helper_releases=not args.no_helper_releases,
            include_assistant_uploads=not args.no_assistant_uploads,
        )
        print(json.dumps(payload, indent=2))
        return

    init_db(settings)
    ensure_admin_user(settings)

    if args.command == "rescan":
        result = scan_media_library(settings, reason="cli")
        print(json.dumps(result, indent=2))
        return

    if args.command == "status":
        payload = get_system_status(
            settings,
            scan_state={
                "running": False,
                "job_id": None,
                "started_at": None,
                "finished_at": None,
                "reason": None,
                "files_seen": 0,
                "files_changed": 0,
                "files_removed": 0,
                "message": "CLI status check",
            },
        )
        print(json.dumps(payload, indent=2))
        return

    if args.command == "import-helper-releases":
        payload = import_helper_release_artifacts(
            settings,
            (Path(source) for source in args.sources),
            channel=args.channel,
        )
        print(json.dumps(payload, indent=2))
        return

    parser.error("Unknown command")


if __name__ == "__main__":
    main()
