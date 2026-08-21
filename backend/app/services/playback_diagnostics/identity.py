from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from pathlib import Path

from .crypto import DiagnosticsKey, DiagnosticsKeyStore, decrypt_blob, encrypt_blob
from .fileio import atomic_write_bytes, ensure_private_directory, resolve_beneath


IDENTITY_CONTEXT = b"elvern-playback-diagnostics-identity-map-v1"


class DiagnosticIdentityStore:
    """Encrypted user-id to random subject-id mapping kept outside raw journals."""

    def __init__(self, root: Path, key_store: DiagnosticsKeyStore, active_key: DiagnosticsKey) -> None:
        self.root = ensure_private_directory(Path(root))
        self.path = resolve_beneath(self.root, "identity-map.enc")
        self.key_store = key_store
        self.active_key = active_key
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
            self.active_key.material,
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
                self._write(mapping)
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

    def _write(self, mapping: dict[str, str]) -> None:
        plaintext = json.dumps(
            {
                "schema_version": "playback-diagnostics-identities-v1",
                "identities": mapping,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        encrypted = encrypt_blob(self.active_key, plaintext, context=IDENTITY_CONTEXT)
        atomic_write_bytes(self.path, encrypted)
