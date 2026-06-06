from __future__ import annotations

import argparse
import getpass
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .auth import ensure_admin_user
from .config import ConfigError, DEFAULT_DB_PATH, refresh_settings
from .db import get_connection, init_db
from .media_scan import scan_media_library
from .security import hash_password, verify_password
from .services.backup_service import (
    build_restore_dry_run_plan,
    create_backup_checkpoint,
    inspect_backup_checkpoint,
    list_backup_checkpoints,
    prune_backup_checkpoints,
)
from .services.desktop_helper_service import import_helper_release_artifacts
from .services.status_service import get_system_status


ARGON2_PARTIAL_CONFIG_ERROR = (
    "Argon2id parameters must be either all set "
    "(ELVERN_ARGON2_TIME_COST, ELVERN_ARGON2_MEMORY_COST, "
    "ELVERN_ARGON2_PARALLELISM) or all unset for adaptive calibration."
)


@dataclass(frozen=True)
class _Argon2CliSettings:
    db_path: Path
    argon2_time_cost: int | None
    argon2_memory_cost: int | None
    argon2_parallelism: int | None

    @property
    def argon2_params_manually_set(self) -> bool:
        return self.argon2_time_cost is not None


def _read_optional_argon2_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def _fallback_argon2_cli_settings() -> _Argon2CliSettings:
    time_cost = _read_optional_argon2_int("ELVERN_ARGON2_TIME_COST")
    memory_cost = _read_optional_argon2_int("ELVERN_ARGON2_MEMORY_COST")
    parallelism = _read_optional_argon2_int("ELVERN_ARGON2_PARALLELISM")
    provided = (time_cost is not None, memory_cost is not None, parallelism is not None)
    if any(provided) and not all(provided):
        raise ConfigError(ARGON2_PARTIAL_CONFIG_ERROR)
    if time_cost is not None and not (1 <= time_cost <= 10):
        raise ConfigError("ELVERN_ARGON2_TIME_COST must be between 1 and 10")
    if memory_cost is not None and not (8192 <= memory_cost <= 1048576):
        raise ConfigError("ELVERN_ARGON2_MEMORY_COST must be between 8192 and 1048576")
    if parallelism is not None and not (1 <= parallelism <= 16):
        raise ConfigError("ELVERN_ARGON2_PARALLELISM must be between 1 and 16")
    return _Argon2CliSettings(
        db_path=DEFAULT_DB_PATH,
        argon2_time_cost=time_cost,
        argon2_memory_cost=memory_cost,
        argon2_parallelism=parallelism,
    )


def _settings_for_argon2_cli():
    try:
        return refresh_settings()
    except ConfigError:
        return _fallback_argon2_cli_settings()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Elvern backend helper commands")
    subparsers = parser.add_subparsers(dest="command", required=True)

    hash_parser = subparsers.add_parser("hash-password", help="Generate a password hash")
    hash_parser.add_argument("password", help="Password to hash")

    subparsers.add_parser(
        "calibrate-argon2",
        help="Calibrate Argon2id password hashing parameters for this hardware",
    )

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
    subparsers.add_parser(
        "rotate-url-prefix",
        help="Rotate the random SPA URL prefix and revoke all sessions",
    )

    disable_totp_parser = subparsers.add_parser(
        "admin-disable-totp",
        help="Disable TOTP for an account from the server shell",
    )
    disable_totp_parser.add_argument("username", help="Username to reset")

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


def cmd_calibrate_argon2(args, settings) -> int:
    from .argon2_calibration import (
        CALIBRATION_VERSION,
        TARGET_VERIFY_MS,
        CalibrationRecord,
        calibrate_argon2,
        compute_host_fingerprint,
        write_calibration,
    )

    if settings.argon2_params_manually_set:
        print("Argon2id parameters are manually set in environment.")
        print(
            "Calibration would be ignored. Unset ELVERN_ARGON2_* "
            "to enable calibration, then re-run."
        )
        return 1

    print("Calibrating Argon2id parameters...")
    params, measured_ms = calibrate_argon2()
    record = CalibrationRecord(
        calibrated_at=datetime.now(timezone.utc).isoformat(),
        calibration_version=CALIBRATION_VERSION,
        host_fingerprint=compute_host_fingerprint(),
        target_verify_ms=TARGET_VERIFY_MS,
        measured_verify_ms=measured_ms,
        params=params,
    )
    write_calibration(record, settings)
    print(
        f"Calibrated: t={params.time_cost} "
        f"m={params.memory_cost} p={params.parallelism} "
        f"verify={measured_ms:.1f}ms"
    )
    return 0


def cmd_rotate_url_prefix(args, settings) -> int:
    del args
    from .url_prefix_service import rotate_url_prefix

    password = getpass.getpass("Admin password: ")
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT id, username, password_hash
            FROM users
            WHERE role = 'admin' AND enabled = 1
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            print("No enabled admin user found.")
            return 1
        ok, new_hash = verify_password(password, row["password_hash"], settings)
        if not ok:
            print("Current admin password is incorrect.")
            return 1
        if new_hash is not None:
            connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (new_hash, datetime.now(timezone.utc).isoformat(), row["id"]),
            )
        _old_prefix, new_prefix = rotate_url_prefix(
            settings,
            connection,
            actor_user_id=int(row["id"]),
            actor_username=str(row["username"]),
        )
        connection.commit()
    print(f"New URL prefix: /{new_prefix}/")
    print(f"All sessions revoked. Bookmark this URL: /{new_prefix}/")
    return 0


def cmd_admin_disable_totp(args, settings) -> int:
    from .services.security_event_service import log_security_event

    now = datetime.now(timezone.utc).isoformat()
    with get_connection(settings) as connection:
        row = connection.execute(
            "SELECT id, username FROM users WHERE username = ? LIMIT 1",
            (args.username,),
        ).fetchone()
        if row is None:
            print(f"User not found: {args.username}")
            return 1
        user_id = int(row["id"])
        connection.execute(
            """
            UPDATE users
            SET totp_secret = NULL,
                totp_enabled_at = NULL,
                totp_last_used_window = NULL,
                totp_setup_skipped_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (now, now, user_id),
        )
        connection.execute("DELETE FROM user_recovery_codes WHERE user_id = ?", (user_id,))
        connection.execute(
            "UPDATE sessions SET revoked_at = ?, revoked_reason = ? WHERE user_id = ? AND revoked_at IS NULL",
            (now, "cli_disabled_totp", user_id),
        )
        connection.commit()
    log_security_event(
        settings,
        event_kind="cli_disabled_user_totp",
        actor_user_id=user_id,
        actor_username=str(row["username"]),
    )
    print(f"TOTP disabled for user {row['username']}. They must re-enroll on next login.")
    return 0


def _backup_create_cli_summary(payload: dict[str, object]) -> dict[str, object]:
    safe_fields = (
        "checkpoint_id",
        "backup_path",
        "created_at_utc",
        "backup_storage",
        "backup_encrypted",
        "backup_key_source",
        "contains_secrets",
        "total_size_bytes",
        "file_count",
        "warning",
    )
    return {field: payload[field] for field in safe_fields if field in payload}


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command == "hash-password":
        settings = _settings_for_argon2_cli()
        print(hash_password(args.password, settings))
        return

    if args.command == "calibrate-argon2":
        settings = _settings_for_argon2_cli()
        raise SystemExit(cmd_calibrate_argon2(args, settings))

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
        print(json.dumps(_backup_create_cli_summary(payload), indent=2))
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

    if args.command == "rotate-url-prefix":
        raise SystemExit(cmd_rotate_url_prefix(args, settings))

    if args.command == "admin-disable-totp":
        raise SystemExit(cmd_admin_disable_totp(args, settings))

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
