from __future__ import annotations

import base64
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


KEYRING_SCHEMA_VERSION = 1
KEY_BYTES = 32


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _set_private_permissions(path: Path, *, directory: bool) -> None:
    if os.name != "nt":
        os.chmod(path, 0o700 if directory else 0o600)


@dataclass(frozen=True)
class BackupKey:
    key_id: str
    key: bytes


class BackupKeyringService:
    """Owns backup-only keys independently from auth/session secrets."""

    def __init__(self, settings, *, path: str | Path | None = None) -> None:
        self.path = (
            Path(path).expanduser().resolve()
            if path is not None
            else (settings.db_path.parent / "security" / "backup-keyring.json").resolve()
        )

    def active_write_key(self) -> BackupKey:
        payload = self._load_or_create()
        return self._decode_key(payload, str(payload["active_key_id"]))

    def read_key(self, key_id: str) -> BackupKey:
        payload = self._load_or_create()
        return self._decode_key(payload, key_id)

    def rotate(self) -> BackupKey:
        payload = self._load_or_create()
        key_id = self._new_key_id()
        payload["keys"][key_id] = {
            "created_at_utc": _utcnow_iso(),
            "key_b64": base64.b64encode(secrets.token_bytes(KEY_BYTES)).decode("ascii"),
        }
        payload["active_key_id"] = key_id
        payload["updated_at_utc"] = _utcnow_iso()
        self._write_atomic(payload)
        return self._decode_key(payload, key_id)

    def _load_or_create(self) -> dict[str, object]:
        if not self.path.exists():
            key_id = self._new_key_id()
            now = _utcnow_iso()
            payload: dict[str, object] = {
                "schema_version": KEYRING_SCHEMA_VERSION,
                "active_key_id": key_id,
                "created_at_utc": now,
                "updated_at_utc": now,
                "keys": {
                    key_id: {
                        "created_at_utc": now,
                        "key_b64": base64.b64encode(secrets.token_bytes(KEY_BYTES)).decode("ascii"),
                    }
                },
            }
            self._write_atomic(payload)
            return payload

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Backup keyring is unreadable") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != KEYRING_SCHEMA_VERSION:
            raise ValueError("Backup keyring schema is unsupported")
        if not isinstance(payload.get("keys"), dict) or not payload.get("active_key_id"):
            raise ValueError("Backup keyring is incomplete")
        _set_private_permissions(self.path.parent, directory=True)
        _set_private_permissions(self.path, directory=False)
        return payload

    def _write_atomic(self, payload: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _set_private_permissions(self.path.parent, directory=True)
        encoded = (json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                _set_private_permissions(temporary_path, directory=False)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            _set_private_permissions(self.path, directory=False)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _new_key_id() -> str:
        return f"bk-{secrets.token_hex(8)}"

    @staticmethod
    def _decode_key(payload: dict[str, object], key_id: str) -> BackupKey:
        entry = (payload.get("keys") or {}).get(key_id)
        if not isinstance(entry, dict):
            raise ValueError("Backup key is unavailable")
        try:
            key = base64.b64decode(str(entry["key_b64"]), validate=True)
        except (KeyError, ValueError) as exc:
            raise ValueError("Backup key is invalid") from exc
        if len(key) != KEY_BYTES:
            raise ValueError("Backup key has an invalid length")
        return BackupKey(key_id=key_id, key=key)
