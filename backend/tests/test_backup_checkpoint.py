from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import inspect
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app import cli as app_cli
from backend.app.db import BACKUP_JOB_PERSISTENCE_MIGRATION, get_connection, init_db, utcnow_iso
from backend.app.services import backup_service


def _cloud_sync_summary(
    *,
    status: str = "success",
    sources_total: int = 0,
    sources_synced: int = 0,
    sources_failed: int = 0,
    media_rows_written: int = 0,
    errors: list[str] | None = None,
    provider_auth_required: bool = False,
    reconnect_required: bool = False,
    message: str | None = None,
    stale_state_warning: str | None = None,
) -> dict[str, object]:
    error_values = list(errors or [])
    resolved_message = message
    if resolved_message is None:
        if provider_auth_required or reconnect_required:
            resolved_message = "Google Drive reconnect is required. Cloud library was not refreshed and may be stale."
        elif status == "partial_failure":
            resolved_message = (
                f"Cloud refresh completed with warnings: {sources_synced} source(s) synced, "
                f"{sources_failed} failed, {media_rows_written} media row(s) refreshed. "
                "Cloud items from failed sources may be stale."
            )
        elif status == "failed":
            resolved_message = (
                f"Cloud refresh failed: {sources_failed} source(s) failed, "
                f"{media_rows_written} media row(s) refreshed. Cloud library was not refreshed and may be stale."
            )
        elif status == "disabled":
            resolved_message = "Google Drive sync is disabled on this server."
        elif sources_total <= 0:
            resolved_message = "No cloud library sources are configured."
        else:
            resolved_message = (
                f"Cloud refresh completed: {sources_synced} source(s) synced, "
                f"{media_rows_written} media row(s) refreshed."
            )
    resolved_stale_warning = stale_state_warning
    if resolved_stale_warning is None:
        if provider_auth_required or reconnect_required:
            resolved_stale_warning = (
                "Cloud library was not refreshed and may be stale until Google Drive reconnects and the next sync succeeds."
            )
        elif status == "partial_failure":
            resolved_stale_warning = "Cloud items from failed sources may be stale until the next successful sync."
        elif status == "failed":
            resolved_stale_warning = "Cloud library was not refreshed and may be stale until the next successful sync."
    return {
        "status": status,
        "provider_auth_required": provider_auth_required,
        "reconnect_required": reconnect_required,
        "message": resolved_message,
        "sources_total": sources_total,
        "sources_synced": sources_synced,
        "sources_failed": sources_failed,
        "media_rows_written": media_rows_written,
        "errors": error_values,
        "stale_state_warning": resolved_stale_warning,
        "source_results": [],
    }


def _login(client, *, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _logout(client) -> None:
    response = client.post("/api/auth/logout")
    assert response.status_code == 200


def _create_standard_user_via_admin(client, *, username: str, password: str) -> None:
    response = client.post(
        "/api/admin/users",
        json={
            "username": username,
            "password": password,
            "role": "standard_user",
            "enabled": True,
        },
    )
    assert response.status_code == 200


class _SpyScanService:
    def __init__(self) -> None:
        self.enqueue_calls: list[str] = []

    def get_state(self) -> dict[str, object]:
        return {
            "running": bool(self.enqueue_calls),
            "job_id": 1 if self.enqueue_calls else None,
            "started_at": None,
            "finished_at": None,
            "reason": self.enqueue_calls[-1] if self.enqueue_calls else None,
            "files_seen": 0,
            "files_changed": 0,
            "files_removed": 0,
            "message": None,
        }

    def enqueue_scan(self, *, reason: str) -> dict[str, object]:
        self.enqueue_calls.append(reason)
        return self.get_state()

    def maybe_refresh_local_library(self, *, trigger: str) -> dict[str, object]:
        return {
            "checked": True,
            "scan_enqueued": False,
            "message": f"spy refresh skipped ({trigger})",
        }


def _latest_audit_details(settings, *, action: str) -> dict[str, object]:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT details_json
            FROM audit_logs
            WHERE action = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (action,),
        ).fetchone()
    assert row is not None
    return json.loads(row["details_json"])


def _prepare_fake_project_root(tmp_path, monkeypatch) -> Path:
    fake_root = tmp_path / "fake-project-root"
    env_dir = fake_root / "deploy" / "env"
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / "elvern.env").write_text(
        "ELVERN_SESSION_SECRET=test-backup-secret-value-1234567890\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(backup_service, "PROJECT_ROOT", fake_root)
    return fake_root


def _current_backup_actor(settings) -> SimpleNamespace:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT u.id AS user_id, u.username, s.id AS session_id
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.revoked_at IS NULL
            ORDER BY s.id DESC
            LIMIT 1
            """
        ).fetchone()
    assert row is not None
    return SimpleNamespace(
        id=int(row["user_id"]),
        username=str(row["username"]),
        session_id=int(row["session_id"]),
    )


def _insert_runtime_fixture_data(initialized_settings) -> None:
    media_file = Path(initialized_settings.media_root) / "Movie.2020.1080p.BluRay.mkv"
    media_file.write_bytes(b"movie-bytes")
    poster_dir = Path(initialized_settings.media_root) / "Posters"
    poster_dir.mkdir(parents=True, exist_ok=True)
    (poster_dir / "Movie (2020).png").write_bytes(b"poster-bytes")

    helper_dir = initialized_settings.helper_releases_dir
    helper_dir.mkdir(parents=True, exist_ok=True)
    (helper_dir / "stable" / "linux").mkdir(parents=True, exist_ok=True)
    (helper_dir / "stable" / "linux" / "helper.zip").write_bytes(b"helper-release")

    assistant_uploads_dir = initialized_settings.db_path.parent / "assistant_uploads"
    assistant_uploads_dir.mkdir(parents=True, exist_ok=True)
    (assistant_uploads_dir / "attachment.txt").write_text("assistant upload", encoding="utf-8")

    transcode_dir = initialized_settings.transcode_dir
    transcode_dir.mkdir(parents=True, exist_ok=True)
    (transcode_dir / "segment.ts").write_bytes(b"transcode-bytes")

    with get_connection(initialized_settings) as connection:
        connection.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES ('backup_fixture', 'present', '2026-04-24T00:00:00Z')
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """
        )
        connection.commit()


def _create_plaintext_checkpoint(initialized_settings, output_dir: Path, **kwargs):
    return backup_service.create_backup_checkpoint(
        initialized_settings,
        output_dir=output_dir,
        allow_plaintext_backup=True,
        **kwargs,
    )


def test_backup_creation_default_output_is_encrypted_archive(initialized_settings, tmp_path, monkeypatch) -> None:
    fake_root = _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    payload = backup_service.create_backup_checkpoint(initialized_settings)
    backup_path = Path(payload["backup_path"])

    assert backup_path.is_file()
    assert backup_path.parent == (fake_root / "backend" / "data" / "backups").resolve()
    assert backup_path.name.endswith(".tar.gz.enc")
    assert payload["backup_storage"] == "encrypted_archive"
    assert payload["backup_encrypted"] is True
    assert payload["backup_key_source"] == "auto"

    inspect_payload = backup_service.inspect_backup_checkpoint(backup_path, settings=initialized_settings)
    assert inspect_payload["valid"] is True
    assert inspect_payload["storage_kind"] == "encrypted_archive"


def test_backup_create_with_output_dir_requires_plaintext_allow_flag(
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    checkpoint_dir = tmp_path / "checkpoint"

    with pytest.raises(ValueError, match="Plaintext backup is unsafe"):
        backup_service.create_backup_checkpoint(initialized_settings, output_dir=checkpoint_dir)

    assert not checkpoint_dir.exists()


def test_backup_creation_produces_manifest_and_db_snapshot(initialized_settings, tmp_path, monkeypatch) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    checkpoint_dir = tmp_path / "checkpoint"
    payload = _create_plaintext_checkpoint(initialized_settings, checkpoint_dir)

    manifest_path = checkpoint_dir / "manifest.json"
    db_snapshot_path = checkpoint_dir / "elvern.db"
    assert Path(payload["backup_path"]) == checkpoint_dir
    assert manifest_path.is_file()
    assert db_snapshot_path.is_file()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["backup_format_version"] == backup_service.BACKUP_FORMAT_VERSION
    assert manifest["db_integrity_check_result"] == "ok"
    assert manifest["db_snapshot_filename"] == "elvern.db"
    assert manifest["env_included"] is True
    assert manifest["helper_releases_included"] is True
    assert manifest["assistant_uploads_included"] is True
    assert manifest["contains_secrets"] is True
    assert manifest["backup_trigger"] == "manual_cli"
    assert manifest["auto_checkpoint"] is False
    assert manifest["backup_storage"] == "legacy_plaintext_directory"
    assert manifest["backup_encrypted"] is False
    assert payload["backup_storage"] == "legacy_plaintext_directory"
    assert payload["backup_encrypted"] is False
    assert "plaintext backup directory" in str(payload["warning"]).lower()
    assert manifest["reason"] is None
    assert manifest["media_root_path"] == str(initialized_settings.media_root.resolve())
    assert manifest["transcode_dir"] == str(initialized_settings.transcode_dir.resolve())
    assert payload["manifest"] == manifest

    file_paths = {entry["relative_path"] for entry in manifest["files"]}
    assert "elvern.db" in file_paths
    assert "deploy/env/elvern.env" in file_paths
    assert "backend/data/helper_releases/stable/linux/helper.zip" in file_paths
    assert "backend/data/assistant_uploads/attachment.txt" in file_paths
    assert "backend/data/transcodes/segment.ts" not in file_paths
    assert "Movie.2020.1080p.BluRay.mkv" not in file_paths
    assert "Posters/Movie (2020).png" not in file_paths

    inspect_payload = backup_service.inspect_backup_checkpoint(checkpoint_dir)
    assert inspect_payload["valid"] is True
    assert inspect_payload["db_integrity_check_result"] == "ok"

    snapshot_connection = sqlite3.connect(db_snapshot_path)
    try:
        integrity_row = snapshot_connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        snapshot_connection.close()
    assert integrity_row == ("ok",)


def test_backup_list_returns_created_manual_checkpoint(initialized_settings, tmp_path, monkeypatch) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    backups_dir = tmp_path / "backups"
    checkpoint_dir = backups_dir / "manual-checkpoint"
    _create_plaintext_checkpoint(initialized_settings, checkpoint_dir)

    checkpoints = backup_service.list_backup_checkpoints(initialized_settings, backups_dir=backups_dir)
    assert len(checkpoints) == 1
    entry = checkpoints[0]
    assert entry["checkpoint_id"] == "manual-checkpoint"
    assert entry["path"] == str(checkpoint_dir)
    assert entry["backup_trigger"] == "manual_cli"
    assert entry["auto_checkpoint"] is False
    assert entry["contains_secrets"] is True
    assert entry["db_integrity_check_result"] == "ok"
    assert entry["inspect_valid"] is True
    assert entry["inspect_error"] is None
    assert entry["file_count"] >= 2
    assert entry["total_size_bytes"] > 0


def test_backup_creation_ignores_missing_optional_directories(initialized_settings, tmp_path, monkeypatch) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    shutil.rmtree(initialized_settings.helper_releases_dir, ignore_errors=True)
    shutil.rmtree(initialized_settings.db_path.parent / "assistant_uploads", ignore_errors=True)

    checkpoint_dir = tmp_path / "checkpoint"
    payload = _create_plaintext_checkpoint(initialized_settings, checkpoint_dir)

    manifest = payload["manifest"]
    assert manifest["helper_releases_included"] is False
    assert manifest["assistant_uploads_included"] is False
    assert backup_service.inspect_backup_checkpoint(checkpoint_dir)["valid"] is True


def test_backup_inspect_detects_tampered_file(initialized_settings, tmp_path, monkeypatch) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    checkpoint_dir = tmp_path / "checkpoint"
    _create_plaintext_checkpoint(initialized_settings, checkpoint_dir)

    env_copy = checkpoint_dir / "deploy" / "env" / "elvern.env"
    env_copy.write_text("tampered=true\n", encoding="utf-8")

    inspect_payload = backup_service.inspect_backup_checkpoint(checkpoint_dir)
    assert inspect_payload["valid"] is False
    assert inspect_payload["hash_mismatches"]
    assert inspect_payload["hash_mismatches"][0]["relative_path"] == "deploy/env/elvern.env"


def test_backup_create_without_env_excludes_env_file(initialized_settings, tmp_path, monkeypatch) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    checkpoint_dir = tmp_path / "checkpoint"
    payload = _create_plaintext_checkpoint(
        initialized_settings,
        checkpoint_dir,
        include_env=False,
    )

    manifest = payload["manifest"]
    assert manifest["env_included"] is False
    assert not (checkpoint_dir / "deploy" / "env" / "elvern.env").exists()


def test_backup_prune_never_deletes_manual_checkpoints(initialized_settings, tmp_path, monkeypatch) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    backups_dir = tmp_path / "backups"
    _create_plaintext_checkpoint(initialized_settings, backups_dir / "manual-a")
    _create_plaintext_checkpoint(initialized_settings, backups_dir / "manual-b")

    summary = backup_service.prune_backup_checkpoints(
        initialized_settings,
        keep_auto=0,
        backups_dir=backups_dir,
    )

    assert summary["deleted_count"] == 0
    assert summary["skipped_manual_count"] == 2
    assert (backups_dir / "manual-a").is_dir()
    assert (backups_dir / "manual-b").is_dir()


def test_backup_prune_deletes_only_older_auto_checkpoints_beyond_keep_auto(
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    timestamps = [
        datetime(2026, 4, 24, 1, 0, tzinfo=timezone.utc),
        datetime(2026, 4, 24, 2, 0, tzinfo=timezone.utc),
        datetime(2026, 4, 24, 3, 0, tzinfo=timezone.utc),
        datetime(2026, 4, 24, 4, 0, tzinfo=timezone.utc),
    ]
    monkeypatch.setattr(backup_service, "_utc_now", lambda: timestamps.pop(0))

    backups_dir = tmp_path / "backups"
    _create_plaintext_checkpoint(
        initialized_settings,
        backups_dir / "auto-1",
        backup_trigger="auto_before_admin_rescan",
        auto_checkpoint=True,
    )
    _create_plaintext_checkpoint(
        initialized_settings,
        backups_dir / "auto-2",
        backup_trigger="auto_before_admin_rescan",
        auto_checkpoint=True,
    )
    newest_auto = _create_plaintext_checkpoint(
        initialized_settings,
        backups_dir / "auto-3",
        backup_trigger="auto_before_admin_rescan",
        auto_checkpoint=True,
    )
    _create_plaintext_checkpoint(initialized_settings, backups_dir / "manual-1")

    summary = backup_service.prune_backup_checkpoints(
        initialized_settings,
        keep_auto=1,
        backups_dir=backups_dir,
    )

    assert summary["deleted_count"] == 2
    assert sorted(Path(path).name for path in summary["deleted_paths"]) == ["auto-1", "auto-2"]
    assert summary["skipped_manual_count"] == 1
    assert (backups_dir / Path(newest_auto["backup_path"]).name).is_dir()
    assert (backups_dir / "manual-1").is_dir()


def test_auto_backup_manifest_has_auto_checkpoint_true_and_trigger(
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    checkpoint_dir = tmp_path / "auto-checkpoint"
    payload = _create_plaintext_checkpoint(
        initialized_settings,
        checkpoint_dir,
        backup_trigger="auto_before_admin_rescan",
        auto_checkpoint=True,
        reason="manual",
        initiated_by_user_id=7,
        initiated_by_username="admin",
        operation_context={"route": "/api/library/rescan", "action": "admin.library.rescan"},
    )

    manifest = payload["manifest"]
    assert manifest["backup_trigger"] == "auto_before_admin_rescan"
    assert manifest["auto_checkpoint"] is True
    assert manifest["reason"] == "manual"
    assert manifest["initiated_by_user_id"] == 7
    assert manifest["initiated_by_username"] == "admin"
    assert manifest["operation_context"] == {
        "route": "/api/library/rescan",
        "action": "admin.library.rescan",
    }


def test_admin_library_rescan_creates_auto_checkpoint_before_enqueue_scan(
    client,
    admin_credentials,
    initialized_settings,
    monkeypatch,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    order: list[str] = []
    captured_backup_kwargs: dict[str, object] = {}
    scan_service = _SpyScanService()
    client.app.state.scan_service = scan_service

    def _fake_backup(settings, **kwargs):
        del settings
        order.append("backup")
        captured_backup_kwargs.update(kwargs)
        return {
            "checkpoint_id": "auto-backup-1",
            "backup_path": "/tmp/auto-backup-1",
            "created_at_utc": "2026-04-24T00:00:00Z",
        }

    def _fake_prune(settings, **kwargs):
        del settings, kwargs
        order.append("prune")
        return {"deleted_count": 0}

    def _fake_cloud_sync(settings):
        del settings
        order.append("cloud")
        return _cloud_sync_summary(
            status="success",
            sources_total=2,
            sources_synced=2,
            media_rows_written=8,
        )

    monkeypatch.setattr("backend.app.routes.library.create_backup_checkpoint", _fake_backup)
    monkeypatch.setattr("backend.app.routes.library.prune_backup_checkpoints", _fake_prune)
    monkeypatch.setattr("backend.app.routes.library.sync_all_google_drive_sources", _fake_cloud_sync)

    original_enqueue = scan_service.enqueue_scan

    def _enqueue_scan(*, reason: str):
        order.append("enqueue")
        return original_enqueue(reason=reason)

    scan_service.enqueue_scan = _enqueue_scan

    response = client.post("/api/library/rescan")
    assert response.status_code == 202
    assert order == ["backup", "prune", "cloud", "enqueue"]
    assert scan_service.enqueue_calls == ["manual"]
    assert "Cloud refresh completed: 2 source(s) synced, 8 media row(s) refreshed." in response.json()["message"]
    assert response.json()["cloud_sync"]["status"] == "success"
    assert response.json()["cloud_sync"]["message"] == "Cloud refresh completed: 2 source(s) synced, 8 media row(s) refreshed."
    assert captured_backup_kwargs["backup_trigger"] == "auto_before_admin_rescan"
    assert captured_backup_kwargs["auto_checkpoint"] is True
    assert captured_backup_kwargs["reason"] == "manual"
    assert captured_backup_kwargs["initiated_by_user_id"] == 1
    assert captured_backup_kwargs["initiated_by_username"] == admin_credentials["username"]
    assert captured_backup_kwargs["operation_context"] == {
        "route": "/api/library/rescan",
        "action": "admin.library.rescan",
        "reason": "manual",
    }

    details = _latest_audit_details(initialized_settings, action="admin.library.rescan")
    assert details["auto_backup_status"] == "created"
    assert details["auto_backup_checkpoint_id"] == "auto-backup-1"
    assert details["auto_backup_path"] == "/tmp/auto-backup-1"
    assert details["auto_backup_created_at_utc"] == "2026-04-24T00:00:00Z"
    assert details["auto_backup_error"] is None
    assert details["cloud_sync_status"] == "success"
    assert details["cloud_sync"]["sources_synced"] == 2
    assert details["cloud_sync"]["media_rows_written"] == 8
    assert details["cloud_sync_error"] is None


def test_standard_user_rescan_does_not_create_checkpoint(
    client,
    admin_credentials,
    initialized_settings,
    monkeypatch,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _create_standard_user_via_admin(client, username="backup-user", password="backup-user-password")
    _logout(client)
    _login(client, username="backup-user", password="backup-user-password")

    called = {"backup": False}
    cloud_sync_called = {"value": False}
    scan_service = _SpyScanService()
    client.app.state.scan_service = scan_service

    def _unexpected_backup(*args, **kwargs):
        called["backup"] = True
        raise AssertionError("backup checkpoint should not run for standard user refresh")

    def _unexpected_cloud_sync(*args, **kwargs):
        cloud_sync_called["value"] = True
        raise AssertionError("cloud sync should not run for standard user refresh")

    monkeypatch.setattr("backend.app.routes.library.create_backup_checkpoint", _unexpected_backup)
    monkeypatch.setattr("backend.app.routes.library.sync_all_google_drive_sources", _unexpected_cloud_sync)

    response = client.post("/api/library/rescan")
    assert response.status_code == 202
    assert called["backup"] is False
    assert cloud_sync_called["value"] is False
    assert scan_service.enqueue_calls == []


def test_admin_rescan_still_enqueues_when_auto_checkpoint_fails(
    client,
    admin_credentials,
    initialized_settings,
    monkeypatch,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    order: list[str] = []
    scan_service = _SpyScanService()
    client.app.state.scan_service = scan_service

    monkeypatch.setattr(
        "backend.app.routes.library.create_backup_checkpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        "backend.app.routes.library.sync_all_google_drive_sources",
        lambda *args, **kwargs: order.append("cloud") or _cloud_sync_summary(
            status="success",
            sources_total=1,
            sources_synced=1,
            media_rows_written=3,
        ),
    )

    response = client.post("/api/library/rescan")
    assert response.status_code == 202
    assert order == ["cloud"]
    assert "Cloud refresh completed: 1 source(s) synced, 3 media row(s) refreshed." in response.json()["message"]
    assert response.json()["cloud_sync"]["status"] == "success"
    assert response.json()["message"].endswith("Backup checkpoint failed; rescan started anyway.")
    assert scan_service.enqueue_calls == ["manual"]

    details = _latest_audit_details(initialized_settings, action="admin.library.rescan")
    assert details["auto_backup_status"] == "failed"
    assert details["auto_backup_checkpoint_id"] is None
    assert details["auto_backup_path"] is None
    assert details["auto_backup_created_at_utc"] is None
    assert details["auto_backup_error"] == "boom"
    assert details["cloud_sync_status"] == "success"
    assert details["cloud_sync"]["sources_synced"] == 1
    assert details["cloud_sync_error"] is None


def test_admin_rescan_surfaces_cloud_sync_partial_failure_but_still_enqueues_local_scan(
    client,
    admin_credentials,
    initialized_settings,
    monkeypatch,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    scan_service = _SpyScanService()
    client.app.state.scan_service = scan_service

    monkeypatch.setattr(
        "backend.app.routes.library.create_backup_checkpoint",
        lambda *args, **kwargs: {
            "checkpoint_id": "auto-backup-2",
            "backup_path": "/tmp/auto-backup-2",
            "created_at_utc": "2026-04-24T00:05:00Z",
        },
    )
    monkeypatch.setattr(
        "backend.app.routes.library.prune_backup_checkpoints",
        lambda *args, **kwargs: {"deleted_count": 0},
    )
    monkeypatch.setattr(
        "backend.app.routes.library.sync_all_google_drive_sources",
        lambda *args, **kwargs: _cloud_sync_summary(
            status="partial_failure",
            sources_total=2,
            sources_synced=1,
            sources_failed=1,
            media_rows_written=4,
            errors=["Google Drive token expired."],
        ),
    )

    response = client.post("/api/library/rescan")
    assert response.status_code == 202
    assert scan_service.enqueue_calls == ["manual"]
    assert response.json()["message"] == (
        "Local scan started. Cloud refresh completed with warnings: 1 source(s) synced, 1 failed, "
        "4 media row(s) refreshed. Cloud items from failed sources may be stale."
    )
    assert response.json()["cloud_sync"]["status"] == "partial_failure"
    assert response.json()["cloud_sync"]["stale_state_warning"] == "Cloud items from failed sources may be stale until the next successful sync."

    details = _latest_audit_details(initialized_settings, action="admin.library.rescan")
    assert details["cloud_sync_status"] == "partial_failure"
    assert details["cloud_sync"]["sources_total"] == 2
    assert details["cloud_sync"]["sources_synced"] == 1
    assert details["cloud_sync"]["sources_failed"] == 1
    assert details["cloud_sync"]["media_rows_written"] == 4
    assert details["cloud_sync_error"] == "Google Drive token expired."


def test_admin_rescan_surfaces_reconnect_required_cloud_stale_warning(
    client,
    admin_credentials,
    initialized_settings,
    monkeypatch,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    scan_service = _SpyScanService()
    client.app.state.scan_service = scan_service

    monkeypatch.setattr(
        "backend.app.routes.library.create_backup_checkpoint",
        lambda *args, **kwargs: {
            "checkpoint_id": "auto-backup-3",
            "backup_path": "/tmp/auto-backup-3",
            "created_at_utc": "2026-04-24T00:10:00Z",
        },
    )
    monkeypatch.setattr(
        "backend.app.routes.library.prune_backup_checkpoints",
        lambda *args, **kwargs: {"deleted_count": 0},
    )
    monkeypatch.setattr(
        "backend.app.routes.library.sync_all_google_drive_sources",
        lambda *args, **kwargs: _cloud_sync_summary(
            status="failed",
            sources_total=1,
            sources_failed=1,
            provider_auth_required=True,
            reconnect_required=True,
            errors=["Reconnect Google Drive to continue this action."],
            stale_state_warning=(
                "Cloud library was not refreshed and may be stale until Google Drive reconnects and the next sync succeeds."
            ),
        ),
    )

    response = client.post("/api/library/rescan")
    assert response.status_code == 202
    assert scan_service.enqueue_calls == ["manual"]
    assert response.json()["message"] == (
        "Local scan started. Google Drive reconnect is required. "
        "Cloud library was not refreshed and may be stale."
    )
    assert response.json()["cloud_sync"]["status"] == "failed"
    assert response.json()["cloud_sync"]["provider_auth_required"] is True
    assert response.json()["cloud_sync"]["reconnect_required"] is True
    assert response.json()["cloud_sync"]["stale_state_warning"] == (
        "Cloud library was not refreshed and may be stale until Google Drive reconnects and the next sync succeeds."
    )

    details = _latest_audit_details(initialized_settings, action="admin.library.rescan")
    assert details["cloud_sync_status"] == "failed"
    assert details["cloud_sync"]["provider_auth_required"] is True
    assert details["cloud_sync"]["reconnect_required"] is True
    assert details["cloud_sync_error"] == "Reconnect Google Drive to continue this action."


def test_shared_local_path_update_creates_auto_checkpoint_before_purge_and_scan(
    client,
    admin_credentials,
    initialized_settings,
    monkeypatch,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    current_path = client.get("/api/admin/media-library-reference").json()["effective_value"]
    replacement_path = Path(current_path) / "replacement-shared-library"
    replacement_path.mkdir()

    order: list[str] = []
    captured_backup_kwargs: dict[str, object] = {}

    def _fake_backup(settings, **kwargs):
        del settings
        order.append("backup")
        captured_backup_kwargs.update(kwargs)
        return {
            "checkpoint_id": "auto-path-backup-1",
            "backup_path": "/tmp/auto-path-backup-1",
            "created_at_utc": "2026-04-24T00:10:00Z",
        }

    def _fake_prune(settings, **kwargs):
        del settings, kwargs
        order.append("prune")
        return {"deleted_count": 0}

    def _fake_purge(connection, *, shared_source_id: int):
        del connection, shared_source_id
        order.append("purge")
        return 0

    def _fake_scan(settings, *, reason: str):
        del settings, reason
        order.append("scan")
        return {"ok": True}

    monkeypatch.setattr("backend.app.routes.admin.create_backup_checkpoint", _fake_backup)
    monkeypatch.setattr("backend.app.routes.admin.prune_backup_checkpoints", _fake_prune)
    monkeypatch.setattr("backend.app.services.app_settings_service.purge_shared_local_media_items", _fake_purge)
    monkeypatch.setattr("backend.app.services.app_settings_service.scan_media_library", _fake_scan)

    response = client.put(
        "/api/admin/media-library-reference",
        json={"value": str(replacement_path)},
    )
    assert response.status_code == 200
    assert order == ["backup", "prune", "purge", "scan"]
    assert captured_backup_kwargs["backup_trigger"] == "auto_before_shared_local_path_update"
    assert captured_backup_kwargs["auto_checkpoint"] is True
    assert captured_backup_kwargs["reason"] == "shared_local_path_update"
    assert captured_backup_kwargs["initiated_by_user_id"] == 1
    assert captured_backup_kwargs["initiated_by_username"] == admin_credentials["username"]
    assert captured_backup_kwargs["operation_context"] == {
        "action": "admin.settings.media_library_reference",
        "existing_effective_path": current_path,
        "requested_value": str(replacement_path),
    }

    details = _latest_audit_details(initialized_settings, action="admin.settings.media_library_reference")
    assert details["auto_backup_status"] == "created"
    assert details["auto_backup_checkpoint_id"] == "auto-path-backup-1"
    assert details["auto_backup_path"] == "/tmp/auto-path-backup-1"
    assert details["auto_backup_created_at_utc"] == "2026-04-24T00:10:00Z"
    assert details["auto_backup_error"] is None


def test_shared_local_path_update_still_proceeds_when_auto_checkpoint_fails(
    client,
    admin_credentials,
    initialized_settings,
    monkeypatch,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    initial_path = client.get("/api/admin/media-library-reference").json()["effective_value"]
    replacement_path = Path(initial_path) / "replacement-shared-library"
    replacement_path.mkdir()
    calls: list[str] = []

    monkeypatch.setattr(
        "backend.app.routes.admin.create_backup_checkpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        "backend.app.services.app_settings_service.purge_shared_local_media_items",
        lambda *args, **kwargs: calls.append("purge"),
    )
    monkeypatch.setattr(
        "backend.app.services.app_settings_service.scan_media_library",
        lambda *args, **kwargs: calls.append("scan"),
    )

    response = client.put(
        "/api/admin/media-library-reference",
        json={"value": str(replacement_path)},
    )
    assert response.status_code == 200
    assert calls == ["purge", "scan"]
    after_path = client.get("/api/admin/media-library-reference").json()["effective_value"]
    assert after_path == str(replacement_path)

    details = _latest_audit_details(initialized_settings, action="admin.settings.media_library_reference")
    assert details["auto_backup_status"] == "failed"
    assert details["auto_backup_checkpoint_id"] is None
    assert details["auto_backup_path"] is None
    assert details["auto_backup_created_at_utc"] is None
    assert details["auto_backup_error"] == "boom"


def test_admin_backup_endpoints_list_create_inspect_and_restore_plan(
    client,
    admin_credentials,
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    fake_root = _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    list_response = client.get("/api/admin/backups")
    assert list_response.status_code == 200
    assert list_response.json()["backups_dir"] == str((fake_root / "backend" / "data" / "backups").resolve())
    assert list_response.json()["checkpoints"] == []

    recent_auth_response = client.post(
        "/api/admin/backups/recent-auth",
        json={"current_admin_password": admin_credentials["password"]},
    )
    assert recent_auth_response.status_code == 200

    plaintext_probe_dir = tmp_path / "admin-plaintext-probe"
    create_response = client.post("/api/admin/backups", json={"output_dir": str(plaintext_probe_dir)})
    assert create_response.status_code == 200
    create_payload = create_response.json()
    checkpoint = create_payload["checkpoint"]
    checkpoint_id = checkpoint["checkpoint_id"]
    assert create_payload["message"] == "Backup checkpoint created."
    assert "secrets" in create_payload["warning"].lower()
    assert checkpoint["backup_trigger"] == "manual_admin_ui"
    assert checkpoint["auto_checkpoint"] is False
    assert checkpoint["contains_secrets"] is True
    assert checkpoint["backup_encrypted"] is True
    assert checkpoint["backup_key_source"] == "auto"
    assert checkpoint["inspect_valid"] is True
    assert Path(checkpoint["path"]).is_file()
    assert checkpoint["path"].endswith(".tar.gz.enc")
    assert not plaintext_probe_dir.exists()

    listed = client.get("/api/admin/backups").json()["checkpoints"]
    assert [entry["checkpoint_id"] for entry in listed] == [checkpoint_id]

    inspect_response = client.get(f"/api/admin/backups/{checkpoint_id}/inspect")
    assert inspect_response.status_code == 200
    inspect_payload = inspect_response.json()
    assert inspect_payload["checkpoint_id"] == checkpoint_id
    assert inspect_payload["valid"] is True
    assert inspect_payload["db_integrity_check_result"] == "ok"
    assert inspect_payload["file_count"] >= 2
    assert inspect_payload["errors"] == []
    assert "manifest" not in inspect_payload

    plan_response = client.get(f"/api/admin/backups/{checkpoint_id}/restore-plan")
    assert plan_response.status_code == 200
    plan_payload = plan_response.json()
    assert plan_payload["checkpoint_id"] == checkpoint_id
    assert plan_payload["checkpoint_valid"] is True
    assert plan_payload["restore_scope"]["media_files_included"] is False
    assert plan_payload["restore_scope"]["poster_files_included"] is False


def test_backup_job_requires_recent_auth_and_is_idempotent(
    client,
    admin_credentials,
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    missing_step_up = client.post(
        "/api/admin/backup-jobs",
        json={"key_source": "auto", "idempotency_key": "job-contract"},
    )
    assert missing_step_up.status_code == 428
    assert missing_step_up.json()["detail"]["code"] == "recent_auth_required"

    assert client.post(
        "/api/admin/backups/recent-auth",
        json={"current_admin_password": admin_credentials["password"]},
    ).status_code == 200
    created = client.post(
        "/api/admin/backup-jobs",
        json={"key_source": "auto", "idempotency_key": "job-contract"},
    )
    assert created.status_code == 202
    job_id = created.json()["id"]
    duplicate = client.post(
        "/api/admin/backup-jobs",
        json={"key_source": "auto", "idempotency_key": "job-contract"},
    )
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == job_id

    deadline = time.monotonic() + 15
    job = created.json()
    while job["state"] not in {"completed", "failed", "interrupted"} and time.monotonic() < deadline:
        time.sleep(0.05)
        response = client.get(f"/api/admin/backup-jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
    assert job["state"] == "completed", job
    assert job["progress_percent"] == 100
    assert job["checkpoint_id"]

    with get_connection(initialized_settings) as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(backup_jobs)").fetchall()}
        persisted = connection.execute(
            "SELECT idempotency_key, key_source, checkpoint_id FROM backup_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        catalog = connection.execute(
            "SELECT checkpoint_id, catalog_status FROM backup_catalog WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    assert "passphrase" not in columns
    assert "output_path" not in columns
    assert dict(persisted) == {
        "idempotency_key": "job-contract",
        "key_source": "auto",
        "checkpoint_id": job["checkpoint_id"],
    }
    assert dict(catalog) == {
        "checkpoint_id": job["checkpoint_id"],
        "catalog_status": "valid",
    }


def test_backup_job_rejects_a_competing_writer(
    client,
    admin_credentials,
    monkeypatch,
) -> None:
    from backend.app.services.backup_job_service import BackupJobManager

    entered = threading.Event()
    release = threading.Event()

    def hold_job(self, *, job_id, actor_id, actor_username, passphrase):
        del self, job_id, actor_id, actor_username, passphrase
        entered.set()
        release.wait(timeout=5)

    monkeypatch.setattr(BackupJobManager, "_run_job", hold_job)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    assert client.post(
        "/api/admin/backups/recent-auth",
        json={"current_admin_password": admin_credentials["password"]},
    ).status_code == 200
    first = client.post(
        "/api/admin/backup-jobs",
        json={"key_source": "auto", "idempotency_key": "writer-one"},
    )
    assert first.status_code == 202
    assert entered.wait(timeout=2)
    try:
        competing = client.post(
            "/api/admin/backup-jobs",
            json={"key_source": "auto", "idempotency_key": "writer-two"},
        )
        assert competing.status_code == 409
        assert competing.json()["detail"]["code"] == "backup_job_active"
        assert competing.json()["detail"]["job_id"] == first.json()["id"]
    finally:
        release.set()


def test_separate_backup_managers_preserve_a_live_lease_and_its_staging_artifact(
    client,
    admin_credentials,
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    from backend.app.services.backup_job_service import BackupJobManager

    _prepare_fake_project_root(tmp_path, monkeypatch)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    actor = _current_backup_actor(initialized_settings)
    monkeypatch.setattr(BackupJobManager, "_run_job", lambda *args, **kwargs: None)

    first_manager = BackupJobManager(initialized_settings)
    second_manager = BackupJobManager(initialized_settings)
    first = first_manager.start_job(
        actor=actor,
        key_source="auto",
        passphrase=None,
        idempotency_key="cross-process-live",
    )
    backups_dir = Path(backup_service.get_backups_dir_path(initialized_settings))
    backups_dir.mkdir(parents=True, exist_ok=True)
    staging_path = backups_dir / f".staging-{first['id']}"
    staging_path.mkdir()
    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE backup_jobs SET staging_path = ? WHERE id = ?",
            (str(staging_path.resolve()), first["id"]),
        )
        connection.commit()

    with pytest.raises(HTTPException) as exc_info:
        second_manager.start_job(
            actor=actor,
            key_source="auto",
            passphrase=None,
            idempotency_key="cross-process-competitor",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["job_id"] == first["id"]
    assert first_manager.get_job(str(first["id"]))["state"] == "queued"
    assert staging_path.is_dir()


def test_backup_lease_heartbeat_extends_expiry(
    client,
    admin_credentials,
    initialized_settings,
    monkeypatch,
) -> None:
    from backend.app.services import backup_job_service
    from backend.app.services.backup_job_service import BackupJobManager

    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    actor = _current_backup_actor(initialized_settings)
    monkeypatch.setattr(BackupJobManager, "_run_job", lambda *args, **kwargs: None)
    manager = BackupJobManager(initialized_settings)
    job = manager.start_job(
        actor=actor,
        key_source="auto",
        passphrase=None,
        idempotency_key="heartbeat-contract",
    )
    with get_connection(initialized_settings) as connection:
        before = connection.execute(
            "SELECT heartbeat_at, expires_at FROM backup_job_leases WHERE job_id = ?",
            (job["id"],),
        ).fetchone()
    assert before is not None

    monkeypatch.setattr(backup_job_service, "BACKUP_LEASE_HEARTBEAT_SECONDS", 0.01)
    stop = threading.Event()
    lost = threading.Event()
    heartbeat = threading.Thread(target=manager._heartbeat_loop, args=(str(job["id"]), stop, lost))
    heartbeat.start()
    time.sleep(0.04)
    stop.set()
    heartbeat.join(timeout=1)

    with get_connection(initialized_settings) as connection:
        after = connection.execute(
            "SELECT heartbeat_at, expires_at FROM backup_job_leases WHERE job_id = ?",
            (job["id"],),
        ).fetchone()
    assert after is not None
    assert after["heartbeat_at"] > before["heartbeat_at"]
    assert after["expires_at"] > before["expires_at"]
    assert lost.is_set() is False


def test_expired_backup_lease_reclaims_only_the_stale_job_and_artifact(
    client,
    admin_credentials,
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    from backend.app.services.backup_job_service import BackupJobManager

    _prepare_fake_project_root(tmp_path, monkeypatch)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    actor = _current_backup_actor(initialized_settings)
    monkeypatch.setattr(BackupJobManager, "_run_job", lambda *args, **kwargs: None)
    stale_manager = BackupJobManager(initialized_settings)
    replacement_manager = BackupJobManager(initialized_settings)
    stale = stale_manager.start_job(
        actor=actor,
        key_source="auto",
        passphrase=None,
        idempotency_key="stale-owner",
    )
    backups_dir = Path(backup_service.get_backups_dir_path(initialized_settings))
    backups_dir.mkdir(parents=True, exist_ok=True)
    stale_artifact = backups_dir / f".staging-{stale['id']}"
    stale_artifact.mkdir()
    unrelated_artifact = backups_dir / ".staging-unrelated"
    unrelated_artifact.mkdir()
    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE backup_jobs SET staging_path = ? WHERE id = ?",
            (str(stale_artifact.resolve()), stale["id"]),
        )
        connection.execute(
            "UPDATE backup_job_leases SET expires_at = '2000-01-01T00:00:00+00:00' WHERE job_id = ?",
            (stale["id"],),
        )
        connection.commit()

    replacement = replacement_manager.start_job(
        actor=actor,
        key_source="auto",
        passphrase=None,
        idempotency_key="replacement-owner",
    )

    assert replacement["id"] != stale["id"]
    assert stale_manager.get_job(str(stale["id"]))["state"] == "interrupted"
    assert replacement_manager.get_job(str(replacement["id"]))["state"] == "queued"
    assert not stale_artifact.exists()
    assert unrelated_artifact.is_dir()
    with get_connection(initialized_settings) as connection:
        lease = connection.execute("SELECT * FROM backup_job_leases").fetchone()
    assert lease is not None
    assert lease["job_id"] == replacement["id"]
    assert lease["owner_instance_id"] == replacement_manager.instance_id


def test_lost_backup_lease_cannot_commit_catalog_or_success(
    client,
    admin_credentials,
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    from backend.app.services.backup_job_service import BackupJobManager, BackupLeaseLostError

    _prepare_fake_project_root(tmp_path, monkeypatch)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    actor = _current_backup_actor(initialized_settings)
    monkeypatch.setattr(BackupJobManager, "_run_job", lambda *args, **kwargs: None)
    manager = BackupJobManager(initialized_settings)
    job = manager.start_job(
        actor=actor,
        key_source="auto",
        passphrase=None,
        idempotency_key="lease-lost-before-catalog",
    )
    backups_dir = Path(backup_service.get_backups_dir_path(initialized_settings))
    backups_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backups_dir / "elvern-backup-20260811-010203Z.tar.gz"
    backup_path.write_bytes(b"checkpoint")
    created = {
        "checkpoint_id": backup_path.name,
        "backup_path": str(backup_path),
        "created_at_utc": "2026-08-11T01:02:03Z",
        "backup_trigger": "manual_admin_ui",
        "auto_checkpoint": False,
        "backup_storage": "archive",
        "backup_encrypted": False,
        "backup_key_source": "auto",
        "contains_secrets": True,
        "total_size_bytes": backup_path.stat().st_size,
        "file_count": 1,
    }
    with get_connection(initialized_settings) as connection:
        connection.execute("DELETE FROM backup_job_leases WHERE job_id = ?", (job["id"],))
        connection.commit()

    with pytest.raises(BackupLeaseLostError):
        manager._upsert_catalog(created, job_id=str(job["id"]))

    with get_connection(initialized_settings) as connection:
        catalog_count = connection.execute(
            "SELECT COUNT(*) FROM backup_catalog WHERE job_id = ?",
            (job["id"],),
        ).fetchone()[0]
        state = connection.execute("SELECT state FROM backup_jobs WHERE id = ?", (job["id"],)).fetchone()[0]
    assert catalog_count == 0
    assert state != "completed"


def test_backup_catalog_upsert_commits_only_one_entry_for_owned_job(
    client,
    admin_credentials,
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    from backend.app.services.backup_job_service import BackupJobManager

    _prepare_fake_project_root(tmp_path, monkeypatch)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    actor = _current_backup_actor(initialized_settings)
    monkeypatch.setattr(BackupJobManager, "_run_job", lambda *args, **kwargs: None)
    manager = BackupJobManager(initialized_settings)
    job = manager.start_job(
        actor=actor,
        key_source="auto",
        passphrase=None,
        idempotency_key="single-catalog-entry",
    )
    backups_dir = Path(backup_service.get_backups_dir_path(initialized_settings))
    backups_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backups_dir / "elvern-backup-20260811-020304Z.tar.gz"
    backup_path.write_bytes(b"checkpoint")
    created = {
        "checkpoint_id": backup_path.name,
        "backup_path": str(backup_path),
        "created_at_utc": "2026-08-11T02:03:04Z",
        "backup_trigger": "manual_admin_ui",
        "auto_checkpoint": False,
        "backup_storage": "archive",
        "backup_encrypted": False,
        "backup_key_source": "auto",
        "contains_secrets": True,
        "total_size_bytes": backup_path.stat().st_size,
        "file_count": 1,
    }

    manager._upsert_catalog(created, job_id=str(job["id"]))
    manager._upsert_catalog(created, job_id=str(job["id"]))

    with get_connection(initialized_settings) as connection:
        rows = connection.execute(
            "SELECT checkpoint_id, job_id FROM backup_catalog WHERE job_id = ?",
            (job["id"],),
        ).fetchall()
    assert [dict(row) for row in rows] == [{"checkpoint_id": backup_path.name, "job_id": job["id"]}]


def test_remote_backup_routes_redact_host_paths(
    client,
    admin_credentials,
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    fake_root = _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)
    monkeypatch.setattr(
        "backend.app.routes.admin.resolve_same_host_request",
        lambda *args, **kwargs: {"same_host": False},
    )
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    assert client.post(
        "/api/admin/backups/recent-auth",
        json={"current_admin_password": admin_credentials["password"]},
    ).status_code == 200

    created = client.post("/api/admin/backups", json={})
    assert created.status_code == 200
    checkpoint = created.json()["checkpoint"]
    checkpoint_id = checkpoint["checkpoint_id"]
    assert checkpoint["path"] == f"backups/{checkpoint_id}"

    listed = client.get("/api/admin/backups")
    assert listed.status_code == 200
    assert listed.json()["backups_dir"] == "backups"
    assert listed.json()["checkpoints"][0]["path"] == f"backups/{checkpoint_id}"

    inspected = client.get(f"/api/admin/backups/{checkpoint_id}/inspect")
    assert inspected.status_code == 200
    assert inspected.json()["path"] == f"backups/{checkpoint_id}"

    restore_plan = client.get(f"/api/admin/backups/{checkpoint_id}/restore-plan")
    assert restore_plan.status_code == 200
    payload = restore_plan.json()
    assert payload["checkpoint_path"] == f"backups/{checkpoint_id}"
    assert all(value is None for value in payload["source_metadata"].values())
    serialized = json.dumps(payload)
    assert str(fake_root.resolve()) not in serialized
    assert str(initialized_settings.db_path.resolve()) not in serialized
    assert str(initialized_settings.media_root.resolve()) not in serialized


def test_backup_passphrase_failures_are_rate_limited_per_session_and_checkpoint(
    client,
    admin_credentials,
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    assert client.post(
        "/api/admin/backups/recent-auth",
        json={"current_admin_password": admin_credentials["password"]},
    ).status_code == 200
    created = client.post(
        "/api/admin/backups",
        json={"passphrase": "correct-test-passphrase"},
    )
    assert created.status_code == 200
    checkpoint_id = created.json()["checkpoint"]["checkpoint_id"]
    monkeypatch.setattr(
        "backend.app.services.recovery_security_service.BACKUP_PASSPHRASE_ATTEMPT_LIMIT",
        2,
    )

    for _ in range(2):
        response = client.post(
            f"/api/admin/backups/{checkpoint_id}/inspect",
            json={"passphrase": "incorrect-test-passphrase"},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "backup_passphrase_invalid"

    blocked = client.post(
        f"/api/admin/backups/{checkpoint_id}/inspect",
        json={"passphrase": "incorrect-test-passphrase"},
    )
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "backup_passphrase_rate_limited"


def test_backup_passphrase_rate_limit_is_shared_by_inspect_preview_and_restore_plan(
    client,
    admin_credentials,
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    assert client.post(
        "/api/admin/backups/recent-auth",
        json={"current_admin_password": admin_credentials["password"]},
    ).status_code == 200
    created = client.post(
        "/api/admin/backups",
        json={"passphrase": "correct-shared-passphrase"},
    )
    assert created.status_code == 200
    checkpoint_id = created.json()["checkpoint"]["checkpoint_id"]
    monkeypatch.setattr(
        "backend.app.services.recovery_security_service.BACKUP_PASSPHRASE_ATTEMPT_LIMIT",
        2,
    )

    inspect_response = client.post(
        f"/api/admin/backups/{checkpoint_id}/inspect",
        json={"passphrase": "wrong-shared-passphrase"},
    )
    preview_response = client.post(
        f"/api/admin/backups/{checkpoint_id}/preview",
        json={"passphrase": "wrong-shared-passphrase"},
    )
    restore_response = client.post(
        f"/api/admin/backups/{checkpoint_id}/restore-plan",
        json={"passphrase": "wrong-shared-passphrase"},
    )

    assert inspect_response.status_code == 422
    assert preview_response.status_code == 422
    assert restore_response.status_code == 429
    assert restore_response.json()["detail"]["code"] == "backup_passphrase_rate_limited"


def test_wrong_backup_passphrase_is_not_written_to_logs_or_security_events(
    client,
    admin_credentials,
    initialized_settings,
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    assert client.post(
        "/api/admin/backups/recent-auth",
        json={"current_admin_password": admin_credentials["password"]},
    ).status_code == 200
    created = client.post(
        "/api/admin/backups",
        json={"passphrase": "correct-log-test-passphrase"},
    )
    checkpoint_id = created.json()["checkpoint"]["checkpoint_id"]
    wrong_passphrase = "wrong-secret-must-never-be-logged"

    response = client.post(
        f"/api/admin/backups/{checkpoint_id}/inspect",
        json={"passphrase": wrong_passphrase},
    )

    assert response.status_code == 422
    assert wrong_passphrase not in response.text
    assert wrong_passphrase not in caplog.text
    with get_connection(initialized_settings) as connection:
        serialized_events = "\n".join(
            str(row["details_json"] or "")
            for row in connection.execute(
                "SELECT details_json FROM security_events ORDER BY id"
            ).fetchall()
        )
    assert wrong_passphrase not in serialized_events


def test_unexpected_checkpoint_value_error_is_redacted_from_api(
    client,
    admin_credentials,
    monkeypatch,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    secret_error = "internal /private/backup/path and raw configuration"
    monkeypatch.setattr(
        "backend.app.routes.admin.resolve_backup_checkpoint_path",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError(secret_error)),
    )

    response = client.get("/api/admin/backups/checkpoint/inspect")

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "backup_operation_failed",
        "message": "The backup operation could not be completed.",
    }
    assert secret_error not in response.text


def test_backup_catalog_keeps_missing_checkpoint_as_honest_status(
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)
    created = backup_service.create_backup_checkpoint(initialized_settings)
    from backend.app.services.backup_job_service import list_backup_catalog

    first = list_backup_catalog(initialized_settings)
    assert first[0]["checkpoint_id"] == created["checkpoint_id"]
    Path(created["backup_path"]).unlink()

    missing = list_backup_catalog(initialized_settings)
    assert missing[0]["checkpoint_id"] == created["checkpoint_id"]
    assert missing[0]["catalog_status"] == "missing"


def test_backup_job_survives_session_and_user_deletion_with_stable_idempotency_scope(
    client,
    admin_credentials,
    initialized_settings,
    monkeypatch,
) -> None:
    from backend.app.services.backup_job_service import BackupJobManager

    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _create_standard_user_via_admin(client, username="backup-owner", password="backup-owner-password")
    _logout(client)
    _login(client, username="backup-owner", password="backup-owner-password")
    actor = _current_backup_actor(initialized_settings)
    monkeypatch.setattr(BackupJobManager, "_run_job", lambda *args, **kwargs: None)
    manager = BackupJobManager(initialized_settings)
    job = manager.start_job(
        actor=actor,
        key_source="auto",
        passphrase=None,
        idempotency_key="survives-owner-deletion",
    )
    expected_scope = f"user:{actor.id}:{actor.username.casefold()}"

    with get_connection(initialized_settings) as connection:
        connection.execute("DELETE FROM sessions WHERE id = ?", (actor.session_id,))
        connection.execute("DELETE FROM users WHERE id = ?", (actor.id,))
        connection.commit()
        persisted = connection.execute("SELECT * FROM backup_jobs WHERE id = ?", (job["id"],)).fetchone()
    assert persisted is not None
    assert persisted["initiated_by_user_id"] is None
    assert persisted["auth_session_id"] is None
    assert persisted["initiated_by_username"] == actor.username
    assert persisted["idempotency_scope"] == expected_scope

    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE backup_jobs SET state = 'archiving', progress_percent = 55, updated_at = ? WHERE id = ?",
            (utcnow_iso(), job["id"]),
        )
        connection.commit()
    assert manager.get_job(str(job["id"]))["progress_percent"] == 55

    duplicate = manager.start_job(
        actor=actor,
        key_source="auto",
        passphrase=None,
        idempotency_key="survives-owner-deletion",
    )
    assert duplicate["id"] == job["id"]


def test_backup_job_schema_upgrade_preserves_active_and_completed_rows(
    initialized_settings,
) -> None:
    with get_connection(initialized_settings) as connection:
        user = connection.execute("SELECT id, username FROM users ORDER BY id LIMIT 1").fetchone()
        assert user is not None
        now = utcnow_iso()
        session_ids = [
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM sessions WHERE user_id = ? ORDER BY id LIMIT 2",
                (user["id"],),
            ).fetchall()
        ]
        while len(session_ids) < 2:
            cursor = connection.execute(
                """
                INSERT INTO sessions (
                    user_id, session_token_hash, created_at, expires_at,
                    last_seen_at, last_activity_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user["id"],
                    f"migration-session-{len(session_ids)}",
                    now,
                    "2099-01-01T00:00:00+00:00",
                    now,
                    now,
                ),
            )
            session_ids.append(int(cursor.lastrowid))
        connection.execute("DROP TABLE backup_job_leases")
        connection.execute("DELETE FROM backup_catalog")
        connection.execute("DROP TABLE backup_jobs")
        connection.execute(
            """
            CREATE TABLE backup_jobs (
                id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                progress_current INTEGER NOT NULL DEFAULT 0,
                progress_total INTEGER NOT NULL DEFAULT 100,
                progress_percent INTEGER NOT NULL DEFAULT 0,
                stage_progress_current INTEGER,
                stage_progress_total INTEGER,
                stage_progress_unit TEXT,
                message TEXT NOT NULL DEFAULT '',
                key_source TEXT NOT NULL,
                checkpoint_id TEXT,
                error_code TEXT,
                error_message TEXT,
                initiated_by_user_id INTEGER NOT NULL,
                initiated_by_username TEXT NOT NULL DEFAULT '',
                auth_session_id INTEGER NOT NULL,
                idempotency_key TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY (initiated_by_user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (auth_session_id) REFERENCES sessions (id) ON DELETE CASCADE,
                UNIQUE (auth_session_id, idempotency_key)
            )
            """
        )
        for job_id, state, session_id in (
            ("legacy-active", "archiving", session_ids[0]),
            ("legacy-completed", "completed", session_ids[1]),
        ):
            connection.execute(
                """
                INSERT INTO backup_jobs (
                    id, state, progress_current, progress_total, progress_percent,
                    message, key_source, checkpoint_id, initiated_by_user_id,
                    initiated_by_username, auth_session_id, idempotency_key,
                    created_at, started_at, updated_at, completed_at
                ) VALUES (?, ?, 50, 100, 50, 'legacy', 'auto', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    state,
                    "legacy-checkpoint" if state == "completed" else None,
                    user["id"],
                    user["username"],
                    session_id,
                    "same-key",
                    now,
                    now,
                    now,
                    now if state == "completed" else None,
                ),
            )
        connection.execute(
            "DELETE FROM schema_migrations WHERE name = ?",
            (BACKUP_JOB_PERSISTENCE_MIGRATION,),
        )
        connection.commit()

    init_db(initialized_settings)
    init_db(initialized_settings)

    with get_connection(initialized_settings) as connection:
        rows = connection.execute(
            "SELECT id, state, checkpoint_id, idempotency_scope FROM backup_jobs ORDER BY id"
        ).fetchall()
        lease_fks = connection.execute("PRAGMA foreign_key_list(backup_job_leases)").fetchall()
        indexes = {
            str(row["name"])
            for row in connection.execute("PRAGMA index_list(backup_jobs)").fetchall()
        }
        migration_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE name = ?",
            (BACKUP_JOB_PERSISTENCE_MIGRATION,),
        ).fetchone()[0]
    assert [row["id"] for row in rows] == ["legacy-active", "legacy-completed"]
    assert [row["state"] for row in rows] == ["archiving", "completed"]
    assert rows[1]["checkpoint_id"] == "legacy-checkpoint"
    assert {row["idempotency_scope"] for row in rows} == {
        f"legacy-session:{session_ids[0]}",
        f"legacy-session:{session_ids[1]}",
    }
    assert {row["table"] for row in lease_fks} == {"backup_jobs"}
    assert "idx_backup_jobs_scope_created" in indexes
    assert migration_count == 1


def test_backup_catalog_timestamp_order_staleness_and_private_directory(
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    from backend.app.services.backup_job_service import (
        _checkpoint_created_at,
        _checkpoint_fingerprint,
        list_backup_catalog,
    )

    _prepare_fake_project_root(tmp_path, monkeypatch)
    backups_dir = Path(backup_service.get_backups_dir_path(initialized_settings))
    backups_dir.mkdir(parents=True, exist_ok=True)
    known_new = backups_dir / "elvern-backup-20260811-120000Z.tar.gz"
    known_old = backups_dir / "elvern-backup-20260810-120000Z.tar.gz"
    unknown = backups_dir / "manual-checkpoint.tar.gz"
    malformed = backups_dir / "elvern-backup-20261399-990000Z.tar.gz"
    for path in (known_new, known_old, unknown, malformed):
        path.write_bytes(path.name.encode("utf-8"))

    assert _checkpoint_created_at(known_new) == "2026-08-11T12:00:00Z"
    assert _checkpoint_created_at(malformed) is None
    first = list_backup_catalog(initialized_settings)
    assert [row["checkpoint_id"] for row in first[:2]] == [known_new.name, known_old.name]
    assert first[0]["created_at_utc"] == "2026-08-11T12:00:00Z"
    assert {row["checkpoint_id"] for row in first[2:]} == {unknown.name, malformed.name}
    if os.name != "nt":
        assert (backups_dir.stat().st_mode & 0o777) == 0o700

    verified_at = "2026-08-11T12:30:00Z"
    with get_connection(initialized_settings) as connection:
        connection.execute(
            """
            UPDATE backup_catalog
            SET catalog_status = 'valid', last_verification_state = 'valid',
                last_verified_at = ?, verification_fingerprint = ?
            WHERE checkpoint_id = ?
            """,
            (verified_at, _checkpoint_fingerprint(known_new), known_new.name),
        )
        connection.commit()
    unchanged = list_backup_catalog(initialized_settings)
    unchanged_row = next(row for row in unchanged if row["checkpoint_id"] == known_new.name)
    assert unchanged_row["catalog_status"] == "valid"
    assert unchanged_row["last_verification_state"] == "valid"
    assert unchanged_row["last_verified_at"] == verified_at

    known_new.write_bytes(b"changed checkpoint contents")
    changed = list_backup_catalog(initialized_settings)
    changed_row = next(row for row in changed if row["checkpoint_id"] == known_new.name)
    assert changed_row["catalog_status"] == "verification_stale"
    assert changed_row["last_verification_state"] == "verification_stale"
    assert changed_row["last_verified_at"] == verified_at


def test_admin_backup_endpoint_rejects_invalid_checkpoint_ids(
    client,
    admin_credentials,
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    invalid_parent = client.get("/api/admin/backups/../inspect")
    assert invalid_parent.status_code in {400, 404}

    invalid_backslash = client.get("/api/admin/backups/bad\\name/inspect")
    assert invalid_backslash.status_code == 400
    assert "path separators" in invalid_backslash.json()["detail"]

    unknown = client.get("/api/admin/backups/unknown-checkpoint/inspect")
    assert unknown.status_code == 404


def test_non_admin_cannot_call_admin_backup_endpoints(
    client,
    admin_credentials,
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _create_standard_user_via_admin(client, username="backup-reader", password="backup-reader-password")
    checkpoint_dir = tmp_path / "manual-checkpoint"
    _create_plaintext_checkpoint(initialized_settings, checkpoint_dir)
    checkpoint_id = checkpoint_dir.name
    _logout(client)
    _login(client, username="backup-reader", password="backup-reader-password")

    for method, path in [
        ("get", "/api/admin/backups"),
        ("post", "/api/admin/backups"),
        ("get", f"/api/admin/backups/{checkpoint_id}/inspect"),
        ("get", f"/api/admin/backups/{checkpoint_id}/restore-plan"),
    ]:
        response = getattr(client, method)(path)
        assert response.status_code == 403


def test_backup_restore_plan_returns_valid_plan_for_valid_checkpoint(
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    fake_root = _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    checkpoint_dir = tmp_path / "checkpoint"
    _create_plaintext_checkpoint(initialized_settings, checkpoint_dir)

    plan = backup_service.build_restore_dry_run_plan(initialized_settings, checkpoint_dir)

    assert plan["restore_plan_format_version"] == backup_service.RESTORE_PLAN_FORMAT_VERSION
    assert plan["checkpoint_valid"] is True
    assert plan["blocking_errors"] == []
    assert plan["contains_secrets"] is True
    assert plan["backup_trigger"] == "manual_cli"
    assert plan["auto_checkpoint"] is False
    assert plan["source_metadata"]["source_project_root"] == str(fake_root.resolve())
    assert plan["current_metadata"]["current_project_root"] == str(fake_root.resolve())
    assert plan["comparison"]["same_project_root"] is True
    assert plan["comparison"]["same_db_path"] is True
    assert plan["restore_scope"]["db_snapshot_available"] is True
    assert plan["restore_scope"]["env_snapshot_available"] is True
    assert plan["restore_scope"]["helper_releases_available"] is True
    assert plan["restore_scope"]["assistant_uploads_available"] is True
    assert plan["restore_scope"]["media_files_included"] is False
    assert plan["restore_scope"]["poster_files_included"] is False
    assert plan["restore_scope"]["transcodes_included"] is False
    assert "media library files" in plan["not_included"]
    assert "Stop backend and frontend services before any manual recovery work." in plan["required_pre_restore_steps"]
    assert plan["manual_restore_outline"]


def test_backup_restore_plan_verifies_manifest_hashes(
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    checkpoint_dir = tmp_path / "checkpoint"
    _create_plaintext_checkpoint(initialized_settings, checkpoint_dir)
    env_copy = checkpoint_dir / "deploy" / "env" / "elvern.env"
    env_copy.write_text("tampered=true\n", encoding="utf-8")

    plan = backup_service.build_restore_dry_run_plan(initialized_settings, checkpoint_dir)

    assert plan["checkpoint_valid"] is False
    assert any("hash mismatches" in error.lower() for error in plan["blocking_errors"])
    assert plan["verification"]["hash_mismatches"]
    assert plan["verification"]["hash_mismatches"][0]["relative_path"] == "deploy/env/elvern.env"


def test_backup_restore_plan_blocks_when_db_snapshot_is_missing(
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    checkpoint_dir = tmp_path / "checkpoint"
    _create_plaintext_checkpoint(initialized_settings, checkpoint_dir)
    (checkpoint_dir / "elvern.db").unlink()

    plan = backup_service.build_restore_dry_run_plan(initialized_settings, checkpoint_dir)

    assert plan["checkpoint_valid"] is False
    assert plan["restore_scope"]["db_snapshot_available"] is False
    assert any("missing checkpoint files" in error.lower() for error in plan["blocking_errors"])


def test_backup_restore_plan_blocks_when_db_snapshot_is_corrupt(
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    checkpoint_dir = tmp_path / "checkpoint"
    _create_plaintext_checkpoint(initialized_settings, checkpoint_dir)
    (checkpoint_dir / "elvern.db").write_bytes(b"not-a-sqlite-database")

    plan = backup_service.build_restore_dry_run_plan(initialized_settings, checkpoint_dir)

    assert plan["checkpoint_valid"] is False
    assert str(plan["verification"]["db_integrity_check_result"]).startswith("error:")
    assert any("integrity_check" in error.lower() for error in plan["blocking_errors"])


def test_backup_restore_plan_warns_when_current_media_root_differs(
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    checkpoint_dir = tmp_path / "checkpoint"
    _create_plaintext_checkpoint(initialized_settings, checkpoint_dir)

    different_media_root = tmp_path / "different-media-root"
    different_media_root.mkdir()
    changed_settings = replace(initialized_settings, media_root=different_media_root.resolve())

    plan = backup_service.build_restore_dry_run_plan(changed_settings, checkpoint_dir)

    assert plan["checkpoint_valid"] is True
    assert plan["comparison"]["same_media_root_path"] is False
    assert any("media_root_path differs" in warning for warning in plan["warnings"])


def test_backup_restore_plan_states_media_files_are_not_included(
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    checkpoint_dir = tmp_path / "checkpoint"
    _create_plaintext_checkpoint(initialized_settings, checkpoint_dir)

    plan = backup_service.build_restore_dry_run_plan(initialized_settings, checkpoint_dir)

    assert plan["restore_scope"]["media_files_included"] is False
    assert plan["restore_scope"]["poster_files_included"] is False
    assert plan["restore_scope"]["transcodes_included"] is False
    assert "poster library files" in plan["not_included"]


def test_backup_restore_plan_does_not_overwrite_live_runtime_state(
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    fake_root = _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    checkpoint_dir = tmp_path / "checkpoint"
    _create_plaintext_checkpoint(initialized_settings, checkpoint_dir)

    live_env = fake_root / "deploy" / "env" / "elvern.env"
    live_helper = initialized_settings.helper_releases_dir / "stable" / "linux" / "helper.zip"
    live_upload = initialized_settings.db_path.parent / "assistant_uploads" / "attachment.txt"
    live_db = initialized_settings.db_path

    before = {
        "env_bytes": live_env.read_bytes(),
        "env_mtime_ns": live_env.stat().st_mtime_ns,
        "helper_bytes": live_helper.read_bytes(),
        "helper_mtime_ns": live_helper.stat().st_mtime_ns,
        "upload_text": live_upload.read_text(encoding="utf-8"),
        "upload_mtime_ns": live_upload.stat().st_mtime_ns,
        "db_bytes": live_db.read_bytes(),
        "db_mtime_ns": live_db.stat().st_mtime_ns,
    }

    plan = backup_service.build_restore_dry_run_plan(initialized_settings, checkpoint_dir)
    assert plan["checkpoint_valid"] is True

    assert live_env.read_bytes() == before["env_bytes"]
    assert live_env.stat().st_mtime_ns == before["env_mtime_ns"]
    assert live_helper.read_bytes() == before["helper_bytes"]
    assert live_helper.stat().st_mtime_ns == before["helper_mtime_ns"]
    assert live_upload.read_text(encoding="utf-8") == before["upload_text"]
    assert live_upload.stat().st_mtime_ns == before["upload_mtime_ns"]
    assert live_db.read_bytes() == before["db_bytes"]
    assert live_db.stat().st_mtime_ns == before["db_mtime_ns"]


def test_cli_backup_restore_plan_works_against_temp_checkpoint(
    initialized_settings,
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    checkpoint_dir = tmp_path / "checkpoint"
    _create_plaintext_checkpoint(initialized_settings, checkpoint_dir)

    monkeypatch.setattr(
        "sys.argv",
        ["backend.app.cli", "backup-restore-plan", str(checkpoint_dir)],
    )

    app_cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["checkpoint_id"] == "checkpoint"
    assert payload["checkpoint_valid"] is True
    assert payload["restore_scope"]["db_snapshot_available"] is True


def _backup_payload_with_sensitive_manifest() -> dict[str, object]:
    return {
        "checkpoint_id": "elvern-backup-20260606-010203Z.tar.gz.enc",
        "backup_path": "/safe/backups/elvern-backup-20260606-010203Z.tar.gz.enc",
        "manifest_path": "/safe/backups/elvern-backup-20260606-010203Z.tar.gz.enc:manifest.json",
        "created_at_utc": "2026-06-06T01:02:03+00:00",
        "backup_trigger": "manual_cli",
        "auto_checkpoint": False,
        "warning": "Manual backups may contain secrets. Do not share or commit them.",
        "contains_secrets": True,
        "backup_storage": "encrypted_archive",
        "backup_encrypted": True,
        "backup_key_source": "auto",
        "total_size_bytes": 123456,
        "file_count": 1,
        "manifest": {
            "source_db_path": "/private/elvern/backend/data/elvern.db",
            "project_root": "/private/elvern",
            "media_root_path": "/private/media",
            "transcode_dir": "/private/elvern/backend/data/transcodes",
            "public_app_origin": "https://private.example",
            "backend_origin": "https://backend.private.example",
            "operation_context": {"token": "secret"},
            "helper_releases_included": True,
            "assistant_uploads_included": True,
            "files": [
                {
                    "relative_path": "deploy/env/elvern.env",
                    "sha256": "abc",
                    "size_bytes": 42,
                }
            ],
        },
    }


def test_backup_create_cli_summary_excludes_manifest_and_sensitive_metadata() -> None:
    payload = _backup_payload_with_sensitive_manifest()

    summary = app_cli._backup_create_cli_summary(payload)
    serialized = json.dumps(summary)

    assert summary == {
        "checkpoint_id": "elvern-backup-20260606-010203Z.tar.gz.enc",
        "backup_path": "/safe/backups/elvern-backup-20260606-010203Z.tar.gz.enc",
        "created_at_utc": "2026-06-06T01:02:03+00:00",
        "backup_storage": "encrypted_archive",
        "backup_encrypted": True,
        "backup_key_source": "auto",
        "contains_secrets": True,
        "total_size_bytes": 123456,
        "file_count": 1,
        "warning": "Manual backups may contain secrets. Do not share or commit them.",
    }
    assert "manifest" not in summary
    for sensitive_value in (
        "source_db_path",
        "project_root",
        "media_root_path",
        "transcode_dir",
        "operation_context",
        "deploy/env/elvern.env",
        "private.example",
    ):
        assert sensitive_value not in serialized


def test_cli_backup_create_prints_safe_summary_only(monkeypatch, capsys) -> None:
    payload = _backup_payload_with_sensitive_manifest()
    calls: dict[str, object] = {}

    def _fake_create_backup_checkpoint(settings, **kwargs):
        calls["settings"] = settings
        calls["kwargs"] = kwargs
        return payload

    monkeypatch.setattr(app_cli, "refresh_settings", lambda: object())
    monkeypatch.setattr(app_cli, "create_backup_checkpoint", _fake_create_backup_checkpoint)
    monkeypatch.setattr(
        "sys.argv",
        ["backend.app.cli", "backup-create", "--no-env", "--no-helper-releases", "--no-assistant-uploads"],
    )

    app_cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output == app_cli._backup_create_cli_summary(payload)
    assert "manifest" not in output
    assert "manifest_path" not in output
    assert "backup_trigger" not in output
    assert "auto_checkpoint" not in output
    assert calls["kwargs"] == {
        "output_dir": None,
        "allow_plaintext_backup": False,
        "include_env": False,
        "include_helper_releases": False,
        "include_assistant_uploads": False,
    }


def test_cli_backup_create_rejects_output_dir_without_plaintext_allow(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    checkpoint_dir = tmp_path / "plaintext-checkpoint"

    def _unexpected_refresh_settings():
        raise AssertionError("settings should not load for rejected plaintext backup")

    monkeypatch.setattr(app_cli, "refresh_settings", _unexpected_refresh_settings)
    monkeypatch.setattr(
        "sys.argv",
        ["backend.app.cli", "backup-create", "--output-dir", str(checkpoint_dir)],
    )

    with pytest.raises(SystemExit) as excinfo:
        app_cli.main()

    captured = capsys.readouterr()
    assert excinfo.value.code == 2
    assert "Plaintext backup requires --allow-plaintext-backup." in captured.err
    assert "Plaintext backups may contain elvern.db, deploy/env/elvern.env" in captured.err
    assert "Plaintext backup is unsafe." in captured.err
    assert not checkpoint_dir.exists()


def test_cli_backup_create_output_dir_with_plaintext_allow_succeeds(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    checkpoint_dir = tmp_path / "plaintext-checkpoint"
    payload = {
        **_backup_payload_with_sensitive_manifest(),
        "checkpoint_id": checkpoint_dir.name,
        "backup_path": str(checkpoint_dir),
        "backup_storage": "legacy_plaintext_directory",
        "backup_encrypted": False,
        "backup_key_source": None,
        "warning": backup_service.PLAINTEXT_BACKUP_WARNING,
    }
    calls: dict[str, object] = {}

    def _fake_create_backup_checkpoint(settings, **kwargs):
        calls["settings"] = settings
        calls["kwargs"] = kwargs
        return payload

    monkeypatch.setattr(app_cli, "refresh_settings", lambda: object())
    monkeypatch.setattr(app_cli, "create_backup_checkpoint", _fake_create_backup_checkpoint)
    monkeypatch.setattr(
        "sys.argv",
        [
            "backend.app.cli",
            "backup-create",
            "--output-dir",
            str(checkpoint_dir),
            "--allow-plaintext-backup",
            "--no-env",
        ],
    )

    app_cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["backup_storage"] == "legacy_plaintext_directory"
    assert output["backup_encrypted"] is False
    assert output["warning"] == backup_service.PLAINTEXT_BACKUP_WARNING
    assert calls["kwargs"] == {
        "output_dir": str(checkpoint_dir),
        "allow_plaintext_backup": True,
        "include_env": False,
        "include_helper_releases": True,
        "include_assistant_uploads": True,
    }


def test_cli_backup_create_branch_uses_safe_summary() -> None:
    source = inspect.getsource(app_cli.main)
    backup_create_branch = source.split('if args.command == "backup-create":', maxsplit=1)[1].split(
        "init_db(settings)",
        maxsplit=1,
    )[0]

    assert "print(json.dumps(_backup_create_cli_summary(payload), indent=2))" in backup_create_branch
    assert "print(json.dumps(payload, indent=2))" not in backup_create_branch


def test_plan_backup_restore_script_exists_and_is_executable() -> None:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "plan-backup-restore.sh"
    assert script_path.is_file()
    assert script_path.stat().st_mode & 0o111


def test_no_destructive_restore_command_exists() -> None:
    parser = app_cli._build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["backup-restore"])
    assert excinfo.value.code == 2
