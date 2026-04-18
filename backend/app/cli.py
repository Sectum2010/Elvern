from __future__ import annotations

import argparse
import json
from pathlib import Path

from .auth import ensure_admin_user
from .config import refresh_settings
from .db import init_db
from .media_scan import scan_media_library
from .security import hash_password
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
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command == "hash-password":
        print(hash_password(args.password))
        return

    settings = refresh_settings()
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
