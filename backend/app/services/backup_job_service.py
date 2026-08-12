from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException, status

from ..db import get_connection, utcnow_iso
from .admin_events_service import emit_admin_event
from .backup_encryption import (
    BackupEncryptionError,
    inspect_encrypted_backup_file_header,
)
from .backup_keyring_service import BackupKeyringService
from .backup_service import (
    create_backup_checkpoint,
    get_backups_dir_path,
    inspect_backup_checkpoint,
    resolve_backup_checkpoint_path,
)


ACTIVE_BACKUP_JOB_STATES = (
    "queued",
    "snapshotting_database",
    "collecting_components",
    "sealing_manifest",
    "archiving",
    "encrypting",
    "writing_checkpoint",
    "verifying_checkpoint",
)
BACKUP_LEASE_NAME = "global-backup-writer"
BACKUP_LEASE_HEARTBEAT_SECONDS = 10
BACKUP_LEASE_TTL_SECONDS = 45


class BackupLeaseLostError(RuntimeError):
    code = "backup_job_lease_lost"


def _utc_datetime() -> datetime:
    return datetime.now(timezone.utc)


def _lease_expiry(now: datetime) -> str:
    return (now + timedelta(seconds=BACKUP_LEASE_TTL_SECONDS)).isoformat()


def _parse_iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _serialize_job(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "state": row["state"],
        "progress_current": int(row["progress_current"] or 0),
        "progress_total": int(row["progress_total"] or 100),
        "progress_percent": int(row["progress_percent"] or 0),
        "stage_progress_current": (
            int(row["stage_progress_current"]) if row["stage_progress_current"] is not None else None
        ),
        "stage_progress_total": (
            int(row["stage_progress_total"]) if row["stage_progress_total"] is not None else None
        ),
        "stage_progress_unit": row["stage_progress_unit"],
        "message": row["message"] or "",
        "key_source": row["key_source"],
        "checkpoint_id": row["checkpoint_id"],
        "error_code": row["error_code"],
        "error_message": row["error_message"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


class BackupJobManager:
    def __init__(self, settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self.instance_id = f"{os.getpid()}-{uuid.uuid4().hex}"

    def start_job(
        self,
        *,
        actor,
        key_source: str,
        passphrase: str | None,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        normalized_source = "passphrase" if key_source == "passphrase" else "auto"
        normalized_idempotency = str(idempotency_key or "").strip()[:128] or None
        idempotency_scope = f"user:{int(actor.id)}:{str(actor.username).strip().casefold()}"
        now_dt = _utc_datetime()
        now = now_dt.isoformat()
        stale_job_id: str | None = None
        with self._lock, get_connection(self.settings) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if normalized_idempotency:
                existing = connection.execute(
                    """
                    SELECT * FROM backup_jobs
                    WHERE idempotency_scope = ? AND idempotency_key = ?
                    LIMIT 1
                    """,
                    (idempotency_scope, normalized_idempotency),
                ).fetchone()
                if existing is not None:
                    connection.commit()
                    return _serialize_job(existing)

            lease = connection.execute(
                "SELECT * FROM backup_job_leases WHERE lease_name = ? LIMIT 1",
                (BACKUP_LEASE_NAME,),
            ).fetchone()
            if lease is not None and (_parse_iso(lease["expires_at"]) or now_dt) > now_dt:
                connection.commit()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "backup_job_active",
                        "message": "A backup job is already active.",
                        "job_id": lease["job_id"],
                    },
                )
            if lease is not None:
                stale_job_id = str(lease["job_id"])
                self._interrupt_stale_job(connection, stale_job_id, now=now)
                connection.execute("DELETE FROM backup_job_leases WHERE lease_name = ?", (BACKUP_LEASE_NAME,))
            else:
                placeholders = ",".join("?" for _ in ACTIVE_BACKUP_JOB_STATES)
                stale = connection.execute(
                    f"SELECT id FROM backup_jobs WHERE state IN ({placeholders}) ORDER BY created_at DESC LIMIT 1",  # nosec B608
                    ACTIVE_BACKUP_JOB_STATES,
                ).fetchone()
                if stale is not None:
                    stale_job_id = str(stale["id"])
                    self._interrupt_stale_job(connection, stale_job_id, now=now)

            job_id = uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO backup_jobs (
                    id, state, progress_current, progress_total, progress_percent,
                    message, key_source, initiated_by_user_id, auth_session_id,
                    initiated_by_username, idempotency_scope, idempotency_key,
                    created_at, updated_at
                ) VALUES (?, 'queued', 0, 100, 0, 'Queued', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    normalized_source,
                    actor.id,
                    actor.session_id,
                    actor.username,
                    idempotency_scope,
                    normalized_idempotency,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO backup_job_leases (
                    lease_name, owner_instance_id, job_id, acquired_at, heartbeat_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (BACKUP_LEASE_NAME, self.instance_id, job_id, now, now, _lease_expiry(now_dt)),
            )
            connection.commit()
        if stale_job_id:
            self._cleanup_job_artifacts(stale_job_id, include_backup=True)
        thread = threading.Thread(
            target=self._run_job,
            kwargs={
                "job_id": job_id,
                "actor_id": actor.id,
                "actor_username": actor.username,
                "passphrase": passphrase if normalized_source == "passphrase" else None,
            },
            name=f"elvern-backup-{job_id[:8]}",
            daemon=True,
        )
        with self._lock:
            self._threads[job_id] = thread
        thread.start()
        return self.get_job(job_id)

    @staticmethod
    def _interrupt_stale_job(connection, job_id: str, *, now: str) -> None:
        placeholders = ",".join("?" for _ in ACTIVE_BACKUP_JOB_STATES)
        connection.execute(
            f"""
            UPDATE backup_jobs
            SET state = 'interrupted',
                error_code = 'backup_job_interrupted',
                error_message = 'Backup job ownership expired before completion.',
                message = 'Interrupted', updated_at = ?, completed_at = ?
            WHERE id = ? AND state IN ({placeholders})
            """,  # nosec B608 - placeholders are generated from a trusted constant tuple.
            (now, now, job_id, *ACTIVE_BACKUP_JOB_STATES),
        )

    def _heartbeat_loop(self, job_id: str, stop: threading.Event, lost: threading.Event) -> None:
        while not stop.wait(BACKUP_LEASE_HEARTBEAT_SECONDS):
            now_dt = _utc_datetime()
            with get_connection(self.settings) as connection:
                cursor = connection.execute(
                    """
                    UPDATE backup_job_leases
                    SET heartbeat_at = ?, expires_at = ?
                    WHERE lease_name = ? AND owner_instance_id = ? AND job_id = ?
                    """,
                    (
                        now_dt.isoformat(),
                        _lease_expiry(now_dt),
                        BACKUP_LEASE_NAME,
                        self.instance_id,
                        job_id,
                    ),
                )
                connection.commit()
            if cursor.rowcount != 1:
                lost.set()
                return

    def _assert_lease_owned(self, job_id: str, *, lost: threading.Event | None = None) -> None:
        if lost is not None and lost.is_set():
            raise BackupLeaseLostError("Backup job ownership was lost")
        now = _utc_datetime()
        with get_connection(self.settings) as connection:
            row = connection.execute(
                """
                SELECT expires_at
                FROM backup_job_leases
                WHERE lease_name = ? AND owner_instance_id = ? AND job_id = ?
                LIMIT 1
                """,
                (BACKUP_LEASE_NAME, self.instance_id, job_id),
            ).fetchone()
        if row is None or (_parse_iso(row["expires_at"]) or now) <= now:
            if lost is not None:
                lost.set()
            raise BackupLeaseLostError("Backup job ownership was lost")

    def _record_artifact_path(self, job_id: str, column: str, path: Path, *, lost: threading.Event) -> None:
        if column not in {"staging_path", "backup_path"}:
            raise ValueError("Unsupported backup artifact column")
        self._assert_lease_owned(job_id, lost=lost)
        with get_connection(self.settings) as connection:
            connection.execute(
                f"UPDATE backup_jobs SET {column} = ?, updated_at = ? WHERE id = ?",  # nosec B608 - column is allowlisted above.
                (str(path.resolve()), utcnow_iso(), job_id),
            )
            connection.commit()

    def _cleanup_job_artifacts(self, job_id: str, *, include_backup: bool) -> None:
        with get_connection(self.settings) as connection:
            row = connection.execute(
                "SELECT staging_path, backup_path, checkpoint_id FROM backup_jobs WHERE id = ? LIMIT 1",
                (job_id,),
            ).fetchone()
            cataloged = connection.execute(
                "SELECT 1 FROM backup_catalog WHERE job_id = ? LIMIT 1",
                (job_id,),
            ).fetchone()
        if row is None:
            return
        backups_root = Path(get_backups_dir_path(self.settings)).resolve()
        candidates: list[tuple[str, bool]] = []
        if row["staging_path"]:
            candidates.append((str(row["staging_path"]), True))
        if include_backup and row["backup_path"] and cataloged is None:
            candidates.append((str(row["backup_path"]), False))
        for raw_path, staging_only in candidates:
            candidate = Path(raw_path).resolve()
            try:
                candidate.relative_to(backups_root)
            except ValueError:
                continue
            if staging_only and not (
                candidate.name.startswith(".staging-") or candidate.name.endswith(".tmp")
            ):
                continue
            try:
                if candidate.is_dir():
                    shutil.rmtree(candidate)
                else:
                    candidate.unlink(missing_ok=True)
            except OSError:
                continue

    def _release_lease(self, job_id: str) -> None:
        with get_connection(self.settings) as connection:
            connection.execute(
                """
                DELETE FROM backup_job_leases
                WHERE lease_name = ? AND owner_instance_id = ? AND job_id = ?
                """,
                (BACKUP_LEASE_NAME, self.instance_id, job_id),
            )
            connection.commit()

    def _run_job(self, *, job_id: str, actor_id: int, actor_username: str, passphrase: str | None) -> None:
        heartbeat_stop = threading.Event()
        lease_lost = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(job_id, heartbeat_stop, lease_lost),
            name=f"elvern-backup-heartbeat-{job_id[:8]}",
            daemon=True,
        )
        heartbeat_started = False

        def progress(stage: str, current: int, total: int, message: str) -> None:
            self._assert_lease_owned(job_id, lost=lease_lost)
            percent = max(0, min(100, round((int(current) / max(int(total), 1)) * 100)))
            persisted_stage = "verifying_checkpoint" if stage == "completed" else stage
            persisted_percent = min(percent, 99)
            with get_connection(self.settings) as connection:
                row = connection.execute(
                    "SELECT progress_percent FROM backup_jobs WHERE id = ? LIMIT 1",
                    (job_id,),
                ).fetchone()
                persisted_percent = max(int(row["progress_percent"] or 0), persisted_percent)
                connection.execute(
                    """
                    UPDATE backup_jobs
                    SET state = ?, progress_current = ?, progress_total = ?,
                        progress_percent = ?, stage_progress_current = NULL,
                        stage_progress_total = NULL, stage_progress_unit = NULL,
                        message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        persisted_stage,
                        int(current),
                        int(total),
                        persisted_percent,
                        "Verifying checkpoint" if stage == "completed" else str(message)[:240],
                        utcnow_iso(),
                        job_id,
                    ),
                )
                connection.commit()
            emit_admin_event("backup_job_updated", dirty_sections=("backups",))

        def progress_metrics(
            stage: str,
            stage_current: int,
            stage_total: int,
            stage_unit: str,
            overall_current: int,
            overall_total: int,
            message: str,
        ) -> None:
            self._assert_lease_owned(job_id, lost=lease_lost)
            percent = max(0, min(99, round((int(overall_current) / max(int(overall_total), 1)) * 100)))
            with get_connection(self.settings) as connection:
                row = connection.execute(
                    "SELECT progress_percent FROM backup_jobs WHERE id = ? LIMIT 1",
                    (job_id,),
                ).fetchone()
                persisted_percent = max(int(row["progress_percent"] or 0), percent)
                connection.execute(
                    """
                    UPDATE backup_jobs
                    SET state = ?, progress_current = ?, progress_total = ?,
                        progress_percent = ?, stage_progress_current = ?,
                        stage_progress_total = ?, stage_progress_unit = ?,
                        message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        stage,
                        int(overall_current),
                        int(overall_total),
                        persisted_percent,
                        int(stage_current),
                        int(stage_total),
                        stage_unit,
                        str(message)[:240],
                        utcnow_iso(),
                        job_id,
                    ),
                )
                connection.commit()
            emit_admin_event("backup_job_updated", dirty_sections=("backups",))

        created: dict[str, object] | None = None
        try:
            self._assert_lease_owned(job_id, lost=lease_lost)
            heartbeat_thread.start()
            heartbeat_started = True
            now = utcnow_iso()
            with get_connection(self.settings) as connection:
                connection.execute(
                    "UPDATE backup_jobs SET started_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, job_id),
                )
                connection.commit()
            created = create_backup_checkpoint(
                self.settings,
                backup_trigger="manual_admin_ui",
                auto_checkpoint=False,
                trigger_kind="manual",
                passphrase=passphrase,
                reason="admin_ui_async",
                initiated_by_user_id=actor_id,
                initiated_by_username=actor_username,
                operation_context={"action": "admin.backup.job.create", "job_id": job_id},
                progress_callback=progress,
                progress_metrics_callback=progress_metrics,
                staging_path_callback=lambda path: self._record_artifact_path(
                    job_id,
                    "staging_path",
                    path,
                    lost=lease_lost,
                ),
            )
            self._record_artifact_path(
                job_id,
                "backup_path",
                Path(str(created["backup_path"])),
                lost=lease_lost,
            )
            self._assert_lease_owned(job_id, lost=lease_lost)
            inspection = inspect_backup_checkpoint(
                created["backup_path"],
                settings=self.settings,
                passphrase=passphrase,
            )
            if not inspection.get("valid"):
                raise BackupEncryptionError("New checkpoint did not pass verification")
            self._assert_lease_owned(job_id, lost=lease_lost)
            self._upsert_catalog(created, job_id=job_id)
            self._assert_lease_owned(job_id, lost=lease_lost)
            completed_at = utcnow_iso()
            with get_connection(self.settings) as connection:
                connection.execute("BEGIN IMMEDIATE")
                lease = connection.execute(
                    """
                    SELECT 1 FROM backup_job_leases
                    WHERE lease_name = ? AND owner_instance_id = ? AND job_id = ? AND expires_at > ?
                    LIMIT 1
                    """,
                    (BACKUP_LEASE_NAME, self.instance_id, job_id, completed_at),
                ).fetchone()
                if lease is None:
                    connection.rollback()
                    raise BackupLeaseLostError("Backup job ownership was lost")
                connection.execute(
                    """
                    UPDATE backup_jobs
                    SET state = 'completed', progress_current = 100, progress_total = 100,
                        progress_percent = 100, stage_progress_current = NULL,
                        stage_progress_total = NULL, stage_progress_unit = NULL,
                        message = 'Backup checkpoint created',
                        checkpoint_id = ?, updated_at = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (
                        created["checkpoint_id"],
                        completed_at,
                        completed_at,
                        job_id,
                    ),
                )
                connection.execute(
                    "DELETE FROM backup_job_leases WHERE lease_name = ? AND owner_instance_id = ? AND job_id = ?",
                    (BACKUP_LEASE_NAME, self.instance_id, job_id),
                )
                connection.commit()
            emit_admin_event("backup_job_completed", dirty_sections=("backups", "audit"))
        except Exception as exc:
            self._cleanup_job_artifacts(job_id, include_backup=True)
            code = getattr(exc, "code", None) or (
                "backup_disk_space_insufficient" if isinstance(exc, OSError) and exc.errno == errno.ENOSPC
                else "backup_write_failed"
            )
            completed_at = utcnow_iso()
            safe_message = self._safe_error_message(exc, code=code)
            if isinstance(exc, BackupLeaseLostError):
                with get_connection(self.settings) as connection:
                    connection.execute(
                        "DELETE FROM backup_catalog WHERE job_id = ?",
                        (job_id,),
                    )
                    connection.commit()
            if not isinstance(exc, BackupLeaseLostError):
                with get_connection(self.settings) as connection:
                    connection.execute(
                        """
                        UPDATE backup_jobs
                        SET state = 'failed', error_code = ?, error_message = ?,
                            message = 'Backup failed', updated_at = ?, completed_at = ?
                        WHERE id = ?
                        """,
                        (code, safe_message, completed_at, completed_at, job_id),
                    )
                    connection.commit()
                emit_admin_event("backup_job_failed", dirty_sections=("backups", "audit"))
        finally:
            heartbeat_stop.set()
            if heartbeat_started:
                heartbeat_thread.join(timeout=1)
            self._release_lease(job_id)
            with self._lock:
                self._threads.pop(job_id, None)

    def get_job(self, job_id: str) -> dict[str, object]:
        with get_connection(self.settings) as connection:
            row = connection.execute("SELECT * FROM backup_jobs WHERE id = ? LIMIT 1", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup job not found")
        return _serialize_job(row)

    def active_job(self) -> dict[str, object] | None:
        stale_job_id: str | None = None
        now_dt = _utc_datetime()
        with get_connection(self.settings) as connection:
            connection.execute("BEGIN IMMEDIATE")
            lease = connection.execute(
                "SELECT * FROM backup_job_leases WHERE lease_name = ? LIMIT 1",
                (BACKUP_LEASE_NAME,),
            ).fetchone()
            if lease is None:
                connection.commit()
                return None
            if (_parse_iso(lease["expires_at"]) or now_dt) <= now_dt:
                stale_job_id = str(lease["job_id"])
                self._interrupt_stale_job(connection, stale_job_id, now=now_dt.isoformat())
                connection.execute("DELETE FROM backup_job_leases WHERE lease_name = ?", (BACKUP_LEASE_NAME,))
                connection.commit()
                row = None
            else:
                row = connection.execute(
                    "SELECT * FROM backup_jobs WHERE id = ? LIMIT 1",
                    (lease["job_id"],),
                ).fetchone()
                connection.commit()
        if stale_job_id:
            self._cleanup_job_artifacts(stale_job_id, include_backup=True)
        return _serialize_job(row) if row is not None else None

    def _upsert_catalog(self, created: dict[str, object], *, job_id: str) -> None:
        now = utcnow_iso()
        backup_path = Path(str(created["backup_path"]))
        key_id = None
        if backup_path.is_file() and backup_path.name.endswith(".enc"):
            key_id = inspect_encrypted_backup_file_header(backup_path).get("key_id")
        fingerprint = _checkpoint_fingerprint(backup_path)
        with get_connection(self.settings) as connection:
            connection.execute("BEGIN IMMEDIATE")
            lease = connection.execute(
                """
                SELECT 1 FROM backup_job_leases
                WHERE lease_name = ? AND owner_instance_id = ? AND job_id = ? AND expires_at > ?
                LIMIT 1
                """,
                (BACKUP_LEASE_NAME, self.instance_id, job_id, now),
            ).fetchone()
            if lease is None:
                raise BackupLeaseLostError("Backup job ownership was lost")
            connection.execute(
                """
                INSERT INTO backup_catalog (
                    checkpoint_id, path, created_at_utc, backup_format_version,
                    backup_trigger, auto_checkpoint, backup_storage, backup_encrypted,
                    backup_key_source, contains_secrets, total_size_bytes, file_count,
                    catalog_status, key_id, last_verification_state,
                    last_verified_at, verification_fingerprint, job_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'valid', ?, 'valid', ?, ?, ?, ?)
                ON CONFLICT(checkpoint_id) DO UPDATE SET
                    path = excluded.path,
                    created_at_utc = excluded.created_at_utc,
                    backup_format_version = excluded.backup_format_version,
                    backup_trigger = excluded.backup_trigger,
                    auto_checkpoint = excluded.auto_checkpoint,
                    backup_storage = excluded.backup_storage,
                    backup_encrypted = excluded.backup_encrypted,
                    backup_key_source = excluded.backup_key_source,
                    contains_secrets = excluded.contains_secrets,
                    total_size_bytes = excluded.total_size_bytes,
                    file_count = excluded.file_count,
                    catalog_status = 'valid',
                    key_id = excluded.key_id,
                    last_verification_state = 'valid',
                    last_verified_at = excluded.last_verified_at,
                    verification_fingerprint = excluded.verification_fingerprint,
                    job_id = excluded.job_id,
                    updated_at = excluded.updated_at
                """,
                (
                    created["checkpoint_id"],
                    created["backup_path"],
                    created.get("created_at_utc"),
                    2,
                    created.get("backup_trigger"),
                    int(bool(created.get("auto_checkpoint"))),
                    created.get("backup_storage"),
                    int(bool(created.get("backup_encrypted"))),
                    created.get("backup_key_source"),
                    int(bool(created.get("contains_secrets"))),
                    int(created.get("total_size_bytes") or 0),
                    int(created.get("file_count") or 0),
                    key_id,
                    now,
                    fingerprint,
                    job_id,
                    now,
                ),
            )
            connection.commit()

    @staticmethod
    def _safe_error_message(exc: Exception, *, code: str) -> str:
        if code == "backup_disk_space_insufficient":
            return "There is not enough free disk space to create this checkpoint."
        if code == "backup_key_unavailable":
            return "The backup encryption key is unavailable."
        if code == "backup_job_lease_lost":
            return "Backup job ownership expired before completion."
        if isinstance(exc, BackupEncryptionError):
            return "The encrypted checkpoint could not be created or verified."
        return "The backup checkpoint could not be created."


_MANAGERS: dict[str, BackupJobManager] = {}
_MANAGERS_LOCK = threading.Lock()


def _checkpoint_fingerprint(path: Path) -> str:
    stat = path.stat()
    stable = f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
    return hashlib.sha256(stable).hexdigest()


_CHECKPOINT_FILENAME_TIMESTAMP = re.compile(
    r"^elvern-backup-(?P<timestamp>\d{8}-\d{6}Z)(?:-\d+)?(?:\.tar\.gz(?:\.enc)?)?$"
)


def _checkpoint_created_at(path: Path) -> str | None:
    if path.is_dir():
        manifest_path = path / "manifest.json"
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("created_at_utc"), str):
            try:
                parsed = datetime.fromisoformat(payload["created_at_utc"].replace("Z", "+00:00"))
            except ValueError:
                pass
            else:
                return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    match = _CHECKPOINT_FILENAME_TIMESTAMP.fullmatch(path.name)
    if match is None:
        return None
    try:
        parsed = datetime.strptime(match.group("timestamp"), "%Y%m%d-%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return parsed.isoformat().replace("+00:00", "Z")


def get_backup_job_manager(settings) -> BackupJobManager:
    identity = str(settings.db_path.resolve())
    with _MANAGERS_LOCK:
        manager = _MANAGERS.get(identity)
        if manager is None:
            manager = BackupJobManager(settings)
            _MANAGERS[identity] = manager
        return manager


def list_backup_catalog(settings) -> list[dict[str, object]]:
    backups_dir = Path(get_backups_dir_path(settings))
    backups_dir.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(backups_dir, 0o700)
    with get_connection(settings) as connection:
        known = {
            str(row["checkpoint_id"]): row
            for row in connection.execute("SELECT * FROM backup_catalog").fetchall()
        }
        disk_candidates = {
            item.name: item
            for item in backups_dir.iterdir()
            if item.name.endswith(".tar.gz.enc")
            or item.name.endswith(".tar.gz")
            or (item.is_dir() and (item / "manifest.json").is_file())
        }
        now = utcnow_iso()
        for checkpoint_id, path in disk_candidates.items():
            if checkpoint_id in known:
                known_row = known[checkpoint_id]
                current_fingerprint = _checkpoint_fingerprint(path)
                previous_fingerprint = str(known_row["verification_fingerprint"] or "")
                current_status = str(known_row["catalog_status"] or "unverified")
                if previous_fingerprint and current_fingerprint != previous_fingerprint:
                    current_status = "verification_stale"
                created_at = known_row["created_at_utc"] or _checkpoint_created_at(path)
                connection.execute(
                    """
                    UPDATE backup_catalog
                    SET path = ?, created_at_utc = ?, total_size_bytes = ?, catalog_status = ?,
                        last_verification_state = CASE
                            WHEN ? = 'verification_stale' THEN 'verification_stale'
                            ELSE last_verification_state
                        END,
                        updated_at = ?
                    WHERE checkpoint_id = ?
                    """,
                    (
                        str(path.resolve()),
                        created_at,
                        int(path.stat().st_size if path.is_file() else 0),
                        current_status,
                        current_status,
                        now,
                        checkpoint_id,
                    ),
                )
                continue
            stat = path.stat()
            backup_format_version = None
            backup_key_source = None
            key_id = None
            catalog_status = "legacy_unverified"
            if path.is_file() and path.name.endswith(".enc"):
                try:
                    header = inspect_encrypted_backup_file_header(path)
                except (OSError, BackupEncryptionError, ValueError):
                    catalog_status = "corrupt"
                else:
                    backup_format_version = header.get("format_version")
                    backup_key_source = header.get("key_source")
                    key_id = str(header.get("key_id") or "") or None
                    if backup_format_version == 2 and backup_key_source == "passphrase":
                        catalog_status = "needs_passphrase"
                    elif backup_format_version == 2 and backup_key_source == "auto":
                        key_id = str(header.get("key_id") or "")
                        try:
                            BackupKeyringService(settings).read_key(key_id)
                        except (OSError, ValueError):
                            catalog_status = "key_unavailable"
                        else:
                            catalog_status = "unverified"
            connection.execute(
                """
                INSERT OR IGNORE INTO backup_catalog (
                    checkpoint_id, path, created_at_utc, backup_format_version, backup_storage, backup_encrypted,
                    backup_key_source,
                    contains_secrets, total_size_bytes, file_count, catalog_status,
                    key_id, verification_fingerprint, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint_id,
                    str(path.resolve()),
                    _checkpoint_created_at(path),
                    backup_format_version,
                    "legacy_plaintext_directory" if path.is_dir() else "encrypted_archive",
                    int(path.name.endswith(".enc")),
                    backup_key_source,
                    int(stat.st_size if path.is_file() else 0),
                    1 if path.is_file() else 0,
                    catalog_status,
                    key_id,
                    _checkpoint_fingerprint(path),
                    now,
                ),
            )
        for checkpoint_id in set(known) - set(disk_candidates):
            connection.execute(
                """
                UPDATE backup_catalog
                SET catalog_status = 'missing', updated_at = ?
                WHERE checkpoint_id = ?
                """,
                (now, checkpoint_id),
            )
        connection.commit()
        rows = connection.execute(
            """
            SELECT * FROM backup_catalog
            ORDER BY CASE WHEN created_at_utc IS NULL THEN 1 ELSE 0 END,
                     created_at_utc DESC,
                     checkpoint_id DESC
            """
        ).fetchall()
    return [
        {
            "checkpoint_id": row["checkpoint_id"],
            "path": row["path"],
            "created_at_utc": row["created_at_utc"],
            "backup_format_version": row["backup_format_version"],
            "backup_trigger": row["backup_trigger"],
            "auto_checkpoint": bool(row["auto_checkpoint"]),
            "backup_storage": row["backup_storage"],
            "backup_encrypted": bool(row["backup_encrypted"]),
            "backup_key_source": row["backup_key_source"],
            "contains_secrets": bool(row["contains_secrets"]),
            "db_integrity_check_result": None,
            "total_size_bytes": int(row["total_size_bytes"] or 0),
            "file_count": int(row["file_count"] or 0),
            "git_commit": None,
            "git_dirty": None,
            "inspect_valid": False,
            "inspect_error": None,
            "catalog_status": row["catalog_status"],
            "key_id": row["key_id"],
            "last_verification_state": row["last_verification_state"],
            "last_verified_at": row["last_verified_at"],
            "verification_fingerprint": row["verification_fingerprint"],
            "job_id": row["job_id"],
        }
        for row in rows
    ]


def update_backup_catalog_verification(
    settings,
    *,
    checkpoint_id: str,
    valid: bool,
) -> None:
    path = resolve_backup_checkpoint_path(settings, checkpoint_id)
    state = "valid" if valid else "corrupt"
    now = utcnow_iso()
    with get_connection(settings) as connection:
        connection.execute(
            """
            UPDATE backup_catalog
            SET catalog_status = ?, last_verification_state = ?,
                last_verified_at = ?, verification_fingerprint = ?, updated_at = ?
            WHERE checkpoint_id = ?
            """,
            (state, state, now, _checkpoint_fingerprint(path), now, checkpoint_id),
        )
        connection.commit()


def delete_backup_checkpoint(settings, checkpoint_id: str) -> dict[str, object]:
    manager = get_backup_job_manager(settings)
    active = manager.active_job()
    if active and active.get("checkpoint_id") == checkpoint_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The checkpoint is being written by an active job")
    with get_connection(settings) as connection:
        catalog_row = connection.execute(
            "SELECT checkpoint_id FROM backup_catalog WHERE checkpoint_id = ? LIMIT 1",
            (checkpoint_id,),
        ).fetchone()
    if catalog_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup checkpoint not found")
    path = resolve_backup_checkpoint_path(settings, checkpoint_id)
    backups_root = Path(get_backups_dir_path(settings)).resolve()
    try:
        path.relative_to(backups_root)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Checkpoint path is outside the backup directory") from exc
    if path.name.startswith(".staging-") or path.name.endswith(".tmp"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Staging checkpoints cannot be deleted")
    trash_path = backups_root / f".trash-{uuid.uuid4().hex}"
    path.replace(trash_path)
    try:
        if trash_path.is_dir():
            shutil.rmtree(trash_path)
        else:
            trash_path.unlink()
    except OSError:
        if trash_path.exists() and not path.exists():
            trash_path.replace(path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "backup_delete_failed", "message": "The checkpoint could not be deleted."},
        )
    with get_connection(settings) as connection:
        connection.execute("DELETE FROM backup_catalog WHERE checkpoint_id = ?", (checkpoint_id,))
        connection.commit()
    emit_admin_event("backup_checkpoint_deleted", dirty_sections=("backups", "audit"))
    return {"checkpoint_id": checkpoint_id, "deleted": True}
