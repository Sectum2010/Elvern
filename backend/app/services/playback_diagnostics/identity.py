from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import os
from dataclasses import dataclass
from pathlib import Path

from .capacity import DiagnosticsCapacityGuard
from .crypto import DiagnosticsKey, DiagnosticsKeyStore, decrypt_blob, encrypt_blob
from .fileio import (
    FILE_MODE,
    atomic_write_bytes,
    atomic_write_json,
    encode_json_document,
    ensure_private_directory,
    resolve_beneath,
)


IDENTITY_CONTEXT = b"elvern-playback-diagnostics-identity-map-v1"
IDENTITY_KEY_BYTES = 32


@dataclass(frozen=True, slots=True)
class DiagnosticsIdentityKey:
    key_id: str
    material: bytes


def load_identity_key(root: Path) -> DiagnosticsIdentityKey:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise FileNotFoundError("Diagnostics identity key directory is unavailable")
    path = resolve_beneath(root, "identity-hmac-key.bin")
    metadata_path = resolve_beneath(root, "identity-hmac-key.json")
    if not path.exists() or not metadata_path.exists():
        raise FileNotFoundError("Diagnostics identity key files are incomplete")
    material = path.read_bytes()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if len(material) != IDENTITY_KEY_BYTES:
        raise ValueError("Invalid diagnostics identity key material")
    if metadata.get("schema_version") != "playback-diagnostics-identity-key-v1":
        raise ValueError("Unsupported diagnostics identity key metadata")
    key_id = str(metadata.get("key_id") or "")
    if not key_id:
        raise ValueError("Diagnostics identity key id is missing")
    return DiagnosticsIdentityKey(key_id=key_id, material=material)


def load_or_create_identity_key(
    root: Path,
    *,
    capacity: DiagnosticsCapacityGuard | None = None,
) -> DiagnosticsIdentityKey:
    root = ensure_private_directory(Path(root))
    path = resolve_beneath(root, "identity-hmac-key.bin")
    metadata_path = resolve_beneath(root, "identity-hmac-key.json")
    if path.exists() and metadata_path.exists():
        identity_key = load_identity_key(root)
        os.chmod(path, FILE_MODE)
        os.chmod(metadata_path, FILE_MODE)
        return identity_key
    if path.exists() or metadata_path.exists():
        raise ValueError("Diagnostics identity key files are incomplete")
    material = secrets.token_bytes(IDENTITY_KEY_BYTES)
    key_id = secrets.token_hex(12)
    metadata = {
        "schema_version": "playback-diagnostics-identity-key-v1",
        "key_id": key_id,
        "algorithm": "HMAC-SHA-256",
        "key_file": path.name,
    }
    encoded_metadata = encode_json_document(metadata)
    reservation = (
        capacity.reserve(len(material) + len(encoded_metadata))
        if capacity is not None
        else None
    )
    try:
        atomic_write_bytes(path, material)
        atomic_write_json(metadata_path, metadata)
        if reservation is not None:
            reservation.commit(len(material) + len(encoded_metadata))
    except Exception:
        if reservation is not None:
            actual = sum(
                candidate.stat().st_size
                for candidate in (path, metadata_path)
                if candidate.is_file() and not candidate.is_symlink()
            )
            reservation.commit(actual)
        raise
    return DiagnosticsIdentityKey(key_id=key_id, material=material)


class DiagnosticIdentityStore:
    """Encrypted user-id to random subject-id mapping kept outside raw journals."""

    def __init__(
        self,
        root: Path,
        key_store: DiagnosticsKeyStore,
        active_key: DiagnosticsKey,
        identity_key: DiagnosticsIdentityKey | None = None,
        capacity: DiagnosticsCapacityGuard | None = None,
    ) -> None:
        self.root = ensure_private_directory(Path(root))
        self.path = resolve_beneath(self.root, "identity-map.enc")
        self.key_store = key_store
        self.active_key = active_key
        self.capacity = capacity
        self.identity_key = identity_key or load_or_create_identity_key(
            self.root,
            capacity=capacity,
        )
        self._lock = threading.RLock()

    def get_or_create_subject(self, user_id: int) -> str:
        key = str(int(user_id))
        with self._lock:
            mapping = self._read()
            subject = str(mapping.get(key) or "")
            if subject:
                return subject
            subject = f"subject_{secrets.token_urlsafe(24)}"
            mapping[key] = subject
            self._write(mapping)
            return subject

    def owner_hash(self, user_id: int) -> str:
        digest = hmac.new(
            self.identity_key.material,
            f"owner:{int(user_id)}".encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        return f"owner_{digest}"

    def unlink_user(self, user_id: int) -> bool:
        key = str(int(user_id))
        with self._lock:
            mapping = self._read()
            removed = mapping.pop(key, None) is not None
            if removed:
                self._write(mapping, critical=True)
            return removed

    def resolve_subject_for_local_join(self, user_id: int) -> str | None:
        with self._lock:
            value = self._read().get(str(int(user_id)))
            return str(value) if value else None

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        plaintext = decrypt_blob(
            self.key_store,
            self.path.read_bytes(),
            context=IDENTITY_CONTEXT,
        )
        payload = json.loads(plaintext.decode("utf-8"))
        if payload.get("schema_version") != "playback-diagnostics-identities-v1":
            raise ValueError("Unsupported diagnostics identity map")
        identities = payload.get("identities")
        if not isinstance(identities, dict):
            raise ValueError("Invalid diagnostics identity map")
        return {
            str(key): str(value)
            for key, value in identities.items()
            if str(key).isdigit() and isinstance(value, str) and value.startswith("subject_")
        }

    def _write(self, mapping: dict[str, str], *, critical: bool = False) -> None:
        plaintext = json.dumps(
            {
                "schema_version": "playback-diagnostics-identities-v1",
                "identities": mapping,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        encrypted = encrypt_blob(self.active_key, plaintext, context=IDENTITY_CONTEXT)
        if self.capacity is None:
            atomic_write_bytes(self.path, encrypted)
            return
        old_size = self.path.stat().st_size if self.path.is_file() else 0
        reservation = self.capacity.reserve(len(encrypted), critical=critical)
        try:
            atomic_write_bytes(self.path, encrypted)
            reservation.commit(0)
            self.capacity.account_replacement(old_size=old_size, new_size=len(encrypted))
        except Exception:
            new_size = self.path.stat().st_size if self.path.is_file() else 0
            reservation.commit(0)
            self.capacity.account_replacement(old_size=old_size, new_size=new_size)
            raise
