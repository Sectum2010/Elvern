from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .fileio import (
    FILE_MODE,
    UnsafeDiagnosticsPathError,
    assert_regular_nonsymlink,
    atomic_write_bytes,
    atomic_write_json,
    ensure_private_directory,
    resolve_beneath,
)


KEY_BYTES = 32
NONCE_BYTES = 12


@dataclass(frozen=True, slots=True)
class DiagnosticsKey:
    key_id: str
    material: bytes

    def __post_init__(self) -> None:
        if len(self.material) != KEY_BYTES:
            raise ValueError("Diagnostics encryption keys must be 256-bit")


class DiagnosticsKeyStore:
    def __init__(self, root: Path) -> None:
        self.root = ensure_private_directory(Path(root))
        self.active_key_path = resolve_beneath(self.root, "active-key.json")

    def load_or_create_active_key(self) -> DiagnosticsKey:
        if self.active_key_path.exists():
            return self._load_active_key()

        key_id = secrets.token_hex(12)
        key_path = resolve_beneath(self.root, f"key-{key_id}.bin")
        material = secrets.token_bytes(KEY_BYTES)
        if key_path.exists():
            raise UnsafeDiagnosticsPathError("Refusing to replace an existing diagnostics key")
        atomic_write_bytes(key_path, material)
        atomic_write_json(
            self.active_key_path,
            {
                "schema_version": "playback-diagnostics-key-v1",
                "key_id": key_id,
                "algorithm": "AES-256-GCM",
                "key_file": key_path.name,
            },
        )
        return DiagnosticsKey(key_id=key_id, material=material)

    def load_key(self, key_id: str) -> DiagnosticsKey:
        normalized = str(key_id or "").strip()
        if not normalized or not normalized.replace("-", "").isalnum():
            raise ValueError("Invalid diagnostics key id")
        key_path = resolve_beneath(self.root, f"key-{normalized}.bin")
        assert_regular_nonsymlink(key_path)
        material = key_path.read_bytes()
        if len(material) != KEY_BYTES:
            raise ValueError("Invalid diagnostics key material")
        os.chmod(key_path, FILE_MODE)
        return DiagnosticsKey(key_id=normalized, material=material)

    def _load_active_key(self) -> DiagnosticsKey:
        assert_regular_nonsymlink(self.active_key_path)
        payload = json.loads(self.active_key_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "playback-diagnostics-key-v1":
            raise ValueError("Unsupported diagnostics key metadata")
        key_id = str(payload.get("key_id") or "")
        key_file = str(payload.get("key_file") or "")
        if key_file != f"key-{key_id}.bin":
            raise ValueError("Diagnostics active key metadata is inconsistent")
        return self.load_key(key_id)


def encrypt_blob(key: DiagnosticsKey, plaintext: bytes, *, context: bytes) -> bytes:
    nonce = secrets.token_bytes(NONCE_BYTES)
    ciphertext = AESGCM(key.material).encrypt(nonce, plaintext, context)
    envelope = {
        "schema_version": "playback-diagnostics-encrypted-blob-v1",
        "key_id": key.key_id,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "context_sha256": hashlib.sha256(context).hexdigest(),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")


def decrypt_blob(key_store: DiagnosticsKeyStore, payload: bytes, *, context: bytes) -> bytes:
    envelope = json.loads(payload.decode("utf-8"))
    if envelope.get("schema_version") != "playback-diagnostics-encrypted-blob-v1":
        raise ValueError("Unsupported diagnostics encrypted blob")
    if envelope.get("context_sha256") != hashlib.sha256(context).hexdigest():
        raise ValueError("Diagnostics encrypted blob context mismatch")
    key = key_store.load_key(str(envelope.get("key_id") or ""))
    nonce = base64.b64decode(str(envelope.get("nonce") or ""), validate=True)
    ciphertext = base64.b64decode(str(envelope.get("ciphertext") or ""), validate=True)
    if len(nonce) != NONCE_BYTES:
        raise ValueError("Invalid diagnostics encrypted blob nonce")
    return AESGCM(key.material).decrypt(nonce, ciphertext, context)
