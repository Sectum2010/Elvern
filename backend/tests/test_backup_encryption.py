from __future__ import annotations

import io
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.services import backup_service
from backend.app.services.backup_encryption import (
    HEADER_MAGIC,
    decrypt_backup,
    encrypt_backup,
)


@pytest.fixture(autouse=True)
def isolated_backup_root(tmp_path, monkeypatch) -> Path:
    fake_root = tmp_path / "fake-project-root"
    env_dir = fake_root / "deploy" / "env"
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / "elvern.env").write_text("ELVERN_SESSION_SECRET=secret\n", encoding="utf-8")
    monkeypatch.setattr(backup_service, "PROJECT_ROOT", fake_root)
    return fake_root


def _tar_contains(tarball_bytes: bytes, member_name: str) -> bool:
    with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as archive:
        return member_name in archive.getnames()


def _tar_with_member(member: tarfile.TarInfo, content: bytes = b"payload") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        if member.isfile():
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        else:
            archive.addfile(member)
    return buffer.getvalue()


class TestBackupEncryptionFormat:
    def test_round_trip_auto(self, initialized_settings) -> None:
        blob = encrypt_backup(b"tarball-bytes", settings=initialized_settings)

        assert blob.startswith(HEADER_MAGIC)
        assert decrypt_backup(blob, settings=initialized_settings) == b"tarball-bytes"

    def test_round_trip_passphrase(self, initialized_settings) -> None:
        blob = encrypt_backup(b"tarball-bytes", settings=initialized_settings, passphrase="testpass1234")

        assert decrypt_backup(blob, settings=initialized_settings, passphrase="testpass1234") == b"tarball-bytes"

    def test_auto_backup_decryptable_without_passphrase(self, initialized_settings) -> None:
        blob = encrypt_backup(b"tarball-bytes", settings=initialized_settings)

        assert decrypt_backup(blob, settings=initialized_settings) == b"tarball-bytes"

    def test_passphrase_backup_requires_correct_passphrase(self, initialized_settings) -> None:
        blob = encrypt_backup(b"tarball-bytes", settings=initialized_settings, passphrase="testpass1234")

        with pytest.raises(ValueError, match="requires a passphrase"):
            decrypt_backup(blob, settings=initialized_settings)

    def test_passphrase_backup_wrong_passphrase_fails(self, initialized_settings) -> None:
        blob = encrypt_backup(b"tarball-bytes", settings=initialized_settings, passphrase="testpass1234")

        with pytest.raises(ValueError, match="Backup decryption failed"):
            decrypt_backup(blob, settings=initialized_settings, passphrase="wrongpass1234")

    def test_corrupted_blob_fails_gracefully(self, initialized_settings) -> None:
        blob = encrypt_backup(b"tarball-bytes", settings=initialized_settings)

        with pytest.raises(ValueError):
            decrypt_backup(blob[:-8] + b"corrupt!", settings=initialized_settings)

    def test_header_invalid_fails_gracefully(self, initialized_settings) -> None:
        with pytest.raises(ValueError, match="Not an Elvern encrypted backup"):
            decrypt_backup(b"not-a-backup", settings=initialized_settings)

    def test_passphrase_backup_with_different_session_secret_still_works(self, initialized_settings) -> None:
        settings_b = replace(initialized_settings, session_secret="different-secret-value-32-chars")
        blob = encrypt_backup(b"tarball-bytes", settings=initialized_settings, passphrase="testpass1234")

        assert decrypt_backup(blob, settings=settings_b, passphrase="testpass1234") == b"tarball-bytes"

    def test_auto_backup_with_different_session_secret_fails(self, initialized_settings) -> None:
        settings_b = replace(initialized_settings, session_secret="different-secret-value-32-chars")
        blob = encrypt_backup(b"tarball-bytes", settings=initialized_settings)

        with pytest.raises(ValueError):
            decrypt_backup(blob, settings=settings_b)


class TestBackupServiceFlow:
    def test_auto_backup_excludes_env_by_default(self, initialized_settings) -> None:
        created = backup_service.create_backup_checkpoint(
            initialized_settings,
            trigger_kind="auto",
            auto_checkpoint=True,
            backup_trigger="auto_test",
        )

        tarball = decrypt_backup(Path(created["backup_path"]).read_bytes(), settings=initialized_settings)
        assert created["backup_path"].endswith(".tar.gz.enc")
        assert _tar_contains(tarball, "elvern.db")
        assert not _tar_contains(tarball, "deploy/env/elvern.env")

    def test_manual_backup_includes_env_by_default(self, initialized_settings) -> None:
        created = backup_service.create_backup_checkpoint(
            initialized_settings,
            trigger_kind="manual",
            backup_trigger="manual_test",
            passphrase="testpass1234",
        )

        tarball = decrypt_backup(
            Path(created["backup_path"]).read_bytes(),
            settings=initialized_settings,
            passphrase="testpass1234",
        )
        assert _tar_contains(tarball, "deploy/env/elvern.env")

    def test_auto_backup_no_passphrase_uses_auto_key(self, initialized_settings) -> None:
        created = backup_service.create_backup_checkpoint(
            initialized_settings,
            trigger_kind="auto",
            auto_checkpoint=True,
            backup_trigger="auto_test",
        )

        assert backup_service.inspect_backup_checkpoint(
            created["backup_path"],
            settings=initialized_settings,
        )["key_source"] == "auto"

    def test_manual_backup_with_passphrase_uses_passphrase_key(self, initialized_settings) -> None:
        created = backup_service.create_backup_checkpoint(
            initialized_settings,
            trigger_kind="manual",
            backup_trigger="manual_test",
            passphrase="testpass1234",
        )

        assert backup_service.inspect_backup_checkpoint(
            created["backup_path"],
            settings=initialized_settings,
            passphrase="testpass1234",
        )["key_source"] == "passphrase"

    def test_inspect_auto_backup_no_passphrase(self, initialized_settings) -> None:
        created = backup_service.create_backup_checkpoint(
            initialized_settings,
            trigger_kind="auto",
            auto_checkpoint=True,
            backup_trigger="auto_test",
        )

        inspection = backup_service.inspect_backup_checkpoint(created["backup_path"], settings=initialized_settings)

        assert inspection["valid"] is True

    def test_inspect_manual_backup_requires_passphrase(self, initialized_settings) -> None:
        created = backup_service.create_backup_checkpoint(
            initialized_settings,
            trigger_kind="manual",
            backup_trigger="manual_test",
            passphrase="testpass1234",
        )

        inspection = backup_service.inspect_backup_checkpoint(created["backup_path"], settings=initialized_settings)

        assert inspection["valid"] is False
        assert inspection["key_source"] == "passphrase"


class TestBackupTarExtraction:
    def test_manual_extraction_writes_regular_files(self, tmp_path) -> None:
        member = tarfile.TarInfo("nested/file.txt")
        tarball = _tar_with_member(member, b"hello")

        backup_service._extract_tar_gz_bytes(tarball, tmp_path / "restore")

        assert (tmp_path / "restore" / "nested" / "file.txt").read_text(encoding="utf-8") == "hello"

    def test_manual_extraction_rejects_path_traversal(self, tmp_path) -> None:
        member = tarfile.TarInfo("../evil.txt")
        tarball = _tar_with_member(member, b"bad")

        with pytest.raises(ValueError, match="unsafe paths"):
            backup_service._extract_tar_gz_bytes(tarball, tmp_path / "restore")
        assert not (tmp_path / "evil.txt").exists()

    def test_manual_extraction_rejects_links(self, tmp_path) -> None:
        member = tarfile.TarInfo("link")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        tarball = _tar_with_member(member)

        with pytest.raises(ValueError, match="unsupported member type"):
            backup_service._extract_tar_gz_bytes(tarball, tmp_path / "restore")


class TestLegacyPlaintextBackups:
    def test_old_plaintext_backup_still_inspectable(self, initialized_settings, tmp_path) -> None:
        checkpoint_dir = tmp_path / "legacy-checkpoint"
        backup_service.create_backup_checkpoint(
            initialized_settings,
            output_dir=checkpoint_dir,
            allow_plaintext_backup=True,
        )

        inspection = backup_service.inspect_backup_checkpoint(checkpoint_dir)

        assert inspection["valid"] is True
        assert inspection["storage_kind"] == "legacy_plaintext_directory"
