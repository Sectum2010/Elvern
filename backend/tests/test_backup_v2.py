from __future__ import annotations

import io
import json
import os
import struct
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.services import backup_keyring_service, backup_service
from backend.app.services.backup_encryption import (
    BACKUP_ALGORITHM_V2,
    HEADER_MAGIC_V2,
    BackupIntegrityError,
    BackupKeyUnavailableError,
    BackupTruncatedError,
    BackupUnsupportedFormatError,
    BackupWrongPassphraseError,
    V2EncryptingWriter,
    decrypt_backup,
    inspect_encrypted_backup_header,
    validate_backup_passphrase,
)
from backend.app.services.backup_keyring_service import BackupKeyringService


def _encrypt_v2(settings, payload: bytes, *, passphrase: str | None = None) -> bytes:
    output = io.BytesIO()
    writer = V2EncryptingWriter(output, settings=settings, passphrase=passphrase)
    writer.write(payload)
    writer.close()
    return output.getvalue()


def _rewrite_header(blob: bytes, transform) -> bytes:
    header_start = len(HEADER_MAGIC_V2) + 4
    header_length = struct.unpack(">I", blob[len(HEADER_MAGIC_V2):header_start])[0]
    header_end = header_start + header_length
    header = json.loads(blob[header_start:header_end].decode("utf-8"))
    transform(header)
    encoded = json.dumps(header, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return HEADER_MAGIC_V2 + struct.pack(">I", len(encoded)) + encoded + blob[header_end:]


def _decode_header(blob: bytes) -> dict[str, object]:
    header_start = len(HEADER_MAGIC_V2) + 4
    header_length = struct.unpack(">I", blob[len(HEADER_MAGIC_V2):header_start])[0]
    return json.loads(blob[header_start:header_start + header_length].decode("utf-8"))


def test_v2_auto_key_round_trip_is_independent_from_session_secret(initialized_settings) -> None:
    blob = _encrypt_v2(initialized_settings, b"checkpoint-data")
    changed_settings = replace(initialized_settings, session_secret="another-session-secret-long-enough")

    assert decrypt_backup(blob, settings=changed_settings) == b"checkpoint-data"
    header = inspect_encrypted_backup_header(blob)
    assert header["format_version"] == 2
    assert header["algorithm"] == BACKUP_ALGORITHM_V2
    assert header["key_source"] == "auto"


def test_v2_passphrase_round_trip_and_wrong_passphrase(initialized_settings) -> None:
    blob = _encrypt_v2(initialized_settings, b"checkpoint-data", passphrase="correct-passphrase")

    assert decrypt_backup(blob, settings=initialized_settings, passphrase="correct-passphrase") == b"checkpoint-data"
    with pytest.raises(BackupWrongPassphraseError):
        decrypt_backup(blob, settings=initialized_settings, passphrase="incorrect-passphrase")


def test_v2_uses_unique_nonce(initialized_settings) -> None:
    first = _decode_header(_encrypt_v2(initialized_settings, b"same"))
    second = _decode_header(_encrypt_v2(initialized_settings, b"same"))

    assert first["key_id"] == second["key_id"]
    assert first != second


@pytest.mark.parametrize("offset_from_end", [1, 20])
def test_v2_rejects_tag_and_ciphertext_tampering(initialized_settings, offset_from_end: int) -> None:
    blob = bytearray(_encrypt_v2(initialized_settings, b"checkpoint-data" * 20))
    blob[-offset_from_end] ^= 0x01

    with pytest.raises(BackupIntegrityError):
        decrypt_backup(bytes(blob), settings=initialized_settings)


def test_v2_rejects_truncation(initialized_settings) -> None:
    blob = _encrypt_v2(initialized_settings, b"checkpoint-data")

    with pytest.raises(BackupTruncatedError) as exc_info:
        decrypt_backup(blob[:-8], settings=initialized_settings)
    assert exc_info.value.code == "backup_truncated"


def test_v2_passphrase_ciphertext_tampering_is_corrupt_not_wrong_passphrase(initialized_settings) -> None:
    blob = bytearray(_encrypt_v2(initialized_settings, b"checkpoint-data", passphrase="correct-passphrase"))
    blob[-20] ^= 0x01

    with pytest.raises(BackupIntegrityError) as exc_info:
        decrypt_backup(bytes(blob), settings=initialized_settings, passphrase="correct-passphrase")
    assert not isinstance(exc_info.value, BackupWrongPassphraseError)
    assert exc_info.value.code == "backup_corrupt"


def test_v2_rejects_unsupported_algorithm_before_decrypt(initialized_settings) -> None:
    blob = _encrypt_v2(initialized_settings, b"checkpoint-data")
    changed = _rewrite_header(blob, lambda header: header.__setitem__("algorithm", "unsupported"))

    with pytest.raises(BackupUnsupportedFormatError):
        decrypt_backup(changed, settings=initialized_settings)


def test_v2_reports_missing_auto_key(initialized_settings) -> None:
    blob = _encrypt_v2(initialized_settings, b"checkpoint-data")
    changed = _rewrite_header(blob, lambda header: header.__setitem__("key_id", "bk-missing"))

    with pytest.raises(BackupKeyUnavailableError):
        decrypt_backup(changed, settings=initialized_settings)


def test_v2_header_tampering_is_authenticated(initialized_settings) -> None:
    blob = _encrypt_v2(initialized_settings, b"checkpoint-data")
    changed = _rewrite_header(blob, lambda header: header.__setitem__("key_verifier", "invalid"))

    with pytest.raises(BackupIntegrityError):
        decrypt_backup(changed, settings=initialized_settings)


def test_backup_passphrase_bounds() -> None:
    with pytest.raises(ValueError, match="at least"):
        validate_backup_passphrase("short")
    assert validate_backup_passphrase("a" * 1024) == "a" * 1024
    with pytest.raises(ValueError, match="at most"):
        validate_backup_passphrase("a" * 1025)


def test_keyring_permissions_and_rotation_keep_previous_key(initialized_settings) -> None:
    service = BackupKeyringService(initialized_settings)
    first = service.active_write_key()
    second = service.rotate()

    assert first.key_id != second.key_id
    assert service.read_key(first.key_id).key == first.key
    assert service.read_key(second.key_id).key == second.key
    if os.name != "nt":
        assert service.path.parent.stat().st_mode & 0o777 == 0o700
        assert service.path.stat().st_mode & 0o777 == 0o600


def test_missing_keyring_read_does_not_create_file(initialized_settings) -> None:
    service = BackupKeyringService(initialized_settings)
    assert not service.path.exists()

    with pytest.raises(ValueError, match="unavailable"):
        service.read_key("bk-missing")

    assert not service.path.exists()
    assert not service.path.with_name(f".{service.path.name}.lock").exists()


def test_corrupt_keyring_read_returns_stable_safe_error(initialized_settings) -> None:
    service = BackupKeyringService(initialized_settings)
    service.path.parent.mkdir(parents=True, exist_ok=True)
    service.path.write_text('{"private_path": "/secret/location"', encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        service.read_key("bk-secret")

    assert str(exc_info.value) == "Backup keyring is unreadable"
    assert str(service.path) not in str(exc_info.value)
    assert "secret" not in str(exc_info.value).casefold()


def test_first_auto_write_creates_one_keyring_with_one_key(initialized_settings) -> None:
    service = BackupKeyringService(initialized_settings)

    active = service.active_write_key()
    payload = json.loads(service.path.read_text(encoding="utf-8"))

    assert payload["active_key_id"] == active.key_id
    assert list(payload["keys"]) == [active.key_id]


def test_manual_passphrase_encryption_does_not_create_auto_keyring(initialized_settings) -> None:
    service = BackupKeyringService(initialized_settings)

    _encrypt_v2(initialized_settings, b"manual", passphrase="manual-test-passphrase")

    assert not service.path.exists()


def test_concurrent_first_keyring_creators_converge(initialized_settings) -> None:
    barrier = threading.Barrier(3)
    created = []
    errors: list[Exception] = []

    def create() -> None:
        try:
            barrier.wait(timeout=2)
            created.append(BackupKeyringService(initialized_settings).active_write_key())
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=5)

    payload = json.loads(BackupKeyringService(initialized_settings).path.read_text(encoding="utf-8"))
    assert errors == []
    assert len(created) == 2
    assert created[0].key_id == created[1].key_id
    assert len(payload["keys"]) == 1


def test_keyring_directory_fsync_unsupported_is_safe(initialized_settings, monkeypatch) -> None:
    service = BackupKeyringService(initialized_settings)
    monkeypatch.setattr(
        backup_keyring_service,
        "_fsync_parent_directory",
        lambda _path: None,
    )

    active = service.active_write_key()

    assert service.read_key(active.key_id).key == active.key


def test_keyring_parent_fsync_closes_descriptor_when_fsync_is_unsupported(monkeypatch, tmp_path) -> None:
    opened = []
    closed = []
    monkeypatch.setattr(
        backup_keyring_service,
        "_open_directory",
        lambda path, flags: opened.append((path, flags)) or 71,
    )
    monkeypatch.setattr(
        backup_keyring_service,
        "_fsync_descriptor",
        lambda descriptor: (_ for _ in ()).throw(OSError("directory fsync unsupported")),
    )
    monkeypatch.setattr(backup_keyring_service, "_close_descriptor", closed.append)

    backup_keyring_service._fsync_parent_directory(tmp_path)

    assert opened == [(tmp_path, os.O_RDONLY)]
    assert closed == [71]


def test_sqlite_snapshot_reports_real_page_progress(initialized_settings, tmp_path) -> None:
    progress: list[tuple[int, int]] = []
    destination = tmp_path / "snapshot.db"

    result = backup_service._create_sqlite_snapshot(
        source_db_path=initialized_settings.db_path,
        destination_path=destination,
        progress_callback=lambda completed, total: progress.append((completed, total)),
    )

    assert result == "ok"
    assert destination.is_file()
    assert progress
    assert progress[-1][1] > 0
    assert progress[-1][0] == progress[-1][1]
    assert progress == sorted(progress)


def test_failed_encrypted_backup_removes_staging(initialized_settings, tmp_path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.setattr(backup_service, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(
        backup_service,
        "_create_sqlite_snapshot",
        lambda **kwargs: (_ for _ in ()).throw(OSError("injected failure")),
    )

    with pytest.raises(OSError, match="injected failure"):
        backup_service.create_backup_checkpoint(initialized_settings)

    backups_dir = project_root / "backend" / "data" / "backups"
    assert not list(backups_dir.glob(".staging-*"))
    assert not list(backups_dir.glob("*.tmp"))


def test_keyring_is_not_in_new_checkpoint(initialized_settings, tmp_path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    env_dir = project_root / "deploy" / "env"
    env_dir.mkdir(parents=True)
    (env_dir / "elvern.env").write_text("ELVERN_EXAMPLE=value\n", encoding="utf-8")
    monkeypatch.setattr(backup_service, "PROJECT_ROOT", project_root)

    created = backup_service.create_backup_checkpoint(initialized_settings)
    decrypted = decrypt_backup(Path(created["backup_path"]).read_bytes(), settings=initialized_settings)

    assert b"backup-keyring" not in decrypted
    assert str(BackupKeyringService(initialized_settings).path).encode("utf-8") not in decrypted
