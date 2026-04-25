from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from backend.app.db import get_connection
from backend.app.services import backup_service


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


def test_backup_creation_produces_manifest_and_db_snapshot(initialized_settings, tmp_path, monkeypatch) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    checkpoint_dir = tmp_path / "checkpoint"
    payload = backup_service.create_backup_checkpoint(initialized_settings, output_dir=checkpoint_dir)

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
    assert manifest["reason"] is None
    assert manifest["media_root_path"] == str(initialized_settings.media_root.resolve())
    assert manifest["transcode_dir"] == str(initialized_settings.transcode_dir.resolve())

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
    backup_service.create_backup_checkpoint(initialized_settings, output_dir=checkpoint_dir)

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
    payload = backup_service.create_backup_checkpoint(initialized_settings, output_dir=checkpoint_dir)

    manifest = payload["manifest"]
    assert manifest["helper_releases_included"] is False
    assert manifest["assistant_uploads_included"] is False
    assert backup_service.inspect_backup_checkpoint(checkpoint_dir)["valid"] is True


def test_backup_inspect_detects_tampered_file(initialized_settings, tmp_path, monkeypatch) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    checkpoint_dir = tmp_path / "checkpoint"
    backup_service.create_backup_checkpoint(initialized_settings, output_dir=checkpoint_dir)

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
    payload = backup_service.create_backup_checkpoint(
        initialized_settings,
        output_dir=checkpoint_dir,
        include_env=False,
    )

    manifest = payload["manifest"]
    assert manifest["env_included"] is False
    assert not (checkpoint_dir / "deploy" / "env" / "elvern.env").exists()


def test_backup_prune_never_deletes_manual_checkpoints(initialized_settings, tmp_path, monkeypatch) -> None:
    _prepare_fake_project_root(tmp_path, monkeypatch)
    _insert_runtime_fixture_data(initialized_settings)

    backups_dir = tmp_path / "backups"
    backup_service.create_backup_checkpoint(initialized_settings, output_dir=backups_dir / "manual-a")
    backup_service.create_backup_checkpoint(initialized_settings, output_dir=backups_dir / "manual-b")

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
    backup_service.create_backup_checkpoint(
        initialized_settings,
        output_dir=backups_dir / "auto-1",
        backup_trigger="auto_before_admin_rescan",
        auto_checkpoint=True,
    )
    backup_service.create_backup_checkpoint(
        initialized_settings,
        output_dir=backups_dir / "auto-2",
        backup_trigger="auto_before_admin_rescan",
        auto_checkpoint=True,
    )
    newest_auto = backup_service.create_backup_checkpoint(
        initialized_settings,
        output_dir=backups_dir / "auto-3",
        backup_trigger="auto_before_admin_rescan",
        auto_checkpoint=True,
    )
    backup_service.create_backup_checkpoint(
        initialized_settings,
        output_dir=backups_dir / "manual-1",
    )

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
    payload = backup_service.create_backup_checkpoint(
        initialized_settings,
        output_dir=checkpoint_dir,
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

    monkeypatch.setattr("backend.app.routes.library.create_backup_checkpoint", _fake_backup)
    monkeypatch.setattr("backend.app.routes.library.prune_backup_checkpoints", _fake_prune)

    original_enqueue = scan_service.enqueue_scan

    def _enqueue_scan(*, reason: str):
        order.append("enqueue")
        return original_enqueue(reason=reason)

    scan_service.enqueue_scan = _enqueue_scan

    response = client.post("/api/library/rescan")
    assert response.status_code == 202
    assert order == ["backup", "prune", "enqueue"]
    assert scan_service.enqueue_calls == ["manual"]
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
    assert details["auto_backup_checkpoint_id"] == "auto-backup-1"
    assert details["auto_backup_path"] == "/tmp/auto-backup-1"
    assert details["auto_backup_created_at_utc"] == "2026-04-24T00:00:00Z"


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
    scan_service = _SpyScanService()
    client.app.state.scan_service = scan_service

    def _unexpected_backup(*args, **kwargs):
        called["backup"] = True
        raise AssertionError("backup checkpoint should not run for standard user refresh")

    monkeypatch.setattr("backend.app.routes.library.create_backup_checkpoint", _unexpected_backup)

    response = client.post("/api/library/rescan")
    assert response.status_code == 202
    assert called["backup"] is False
    assert scan_service.enqueue_calls == []


def test_admin_rescan_does_not_enqueue_when_auto_checkpoint_fails(
    client,
    admin_credentials,
    monkeypatch,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    scan_service = _SpyScanService()
    client.app.state.scan_service = scan_service

    monkeypatch.setattr(
        "backend.app.routes.library.create_backup_checkpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    response = client.post("/api/library/rescan")
    assert response.status_code == 500
    assert response.json()["detail"] == "Backup checkpoint failed; rescan was not started."
    assert scan_service.enqueue_calls == []


def test_shared_local_path_update_creates_auto_checkpoint_before_purge_and_scan(
    client,
    admin_credentials,
    initialized_settings,
    tmp_path,
    monkeypatch,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    current_path = client.get("/api/admin/media-library-reference").json()["effective_value"]
    replacement_path = tmp_path / "replacement-shared-library"
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
    assert details["auto_backup_checkpoint_id"] == "auto-path-backup-1"
    assert details["auto_backup_path"] == "/tmp/auto-path-backup-1"
    assert details["auto_backup_created_at_utc"] == "2026-04-24T00:10:00Z"


def test_shared_local_path_update_does_not_purge_or_scan_when_auto_checkpoint_fails(
    client,
    admin_credentials,
    tmp_path,
    monkeypatch,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    initial_path = client.get("/api/admin/media-library-reference").json()["effective_value"]
    replacement_path = tmp_path / "replacement-shared-library"
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
    assert response.status_code == 500
    assert response.json()["detail"] == "Backup checkpoint failed; shared local library path was not updated."
    assert calls == []
    after_path = client.get("/api/admin/media-library-reference").json()["effective_value"]
    assert after_path == initial_path
