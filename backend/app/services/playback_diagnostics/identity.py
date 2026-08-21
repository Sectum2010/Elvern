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
    open_private_descriptor,
    private_file_size,
    private_file_stat,
    read_private_bytes,
    resolve_beneath,
    unlink_private_file,
)


IDENTITY_CONTEXT = b"elvern-playback-diagnostics-identity-map-v1"
IDENTITY_KEY_BYTES = 32


@dataclass(frozen=True, slots=True)
class DiagnosticsIdentityKey:
    key_id: str
    material: bytes


def load_identity_key(
    root: Path,
    *,
    trusted_root: Path | None = None,
) -> DiagnosticsIdentityKey:
    root = Path(root)
    authority_root = Path(trusted_root) if trusted_root is not None else root.parent
    path = resolve_beneath(root, "identity-hmac-key.bin")
    metadata_path = resolve_beneath(root, "identity-hmac-key.json")
    try:
        private_file_size(path, trusted_root=authority_root)
        private_file_size(metadata_path, trusted_root=authority_root)
    except FileNotFoundError:
        raise FileNotFoundError("Diagnostics identity key files are incomplete")
    material = read_private_bytes(
        path,
        max_bytes=IDENTITY_KEY_BYTES,
        trusted_root=authority_root,
    )
    metadata = json.loads(
        read_private_bytes(
            metadata_path,
            max_bytes=8_192,
            trusted_root=authority_root,
        ).decode("utf-8")
    )
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
    trusted_root: Path | None = None,
) -> DiagnosticsIdentityKey:
    root = Path(root)
    authority_root = Path(trusted_root) if trusted_root is not None else root.parent
    root = ensure_private_directory(root, trusted_root=authority_root)
    path = resolve_beneath(root, "identity-hmac-key.bin")
    metadata_path = resolve_beneath(root, "identity-hmac-key.json")
    path_exists = private_file_stat(
        path,
        trusted_root=authority_root,
        missing_ok=True,
    ) is not None
    metadata_exists = private_file_stat(
        metadata_path,
        trusted_root=authority_root,
        missing_ok=True,
    ) is not None
    if path_exists and metadata_exists:
        identity_key = load_identity_key(root, trusted_root=authority_root)
        for candidate in (path, metadata_path):
            descriptor = open_private_descriptor(
                candidate,
                os.O_RDONLY,
                trusted_root=authority_root,
            )
            try:
                os.fchmod(descriptor, FILE_MODE)
            finally:
                os.close(descriptor)
        return identity_key
    if path_exists and not metadata_exists:
        identity_map_path = resolve_beneath(root, "identity-map.enc")
        if private_file_stat(
            identity_map_path,
            trusted_root=authority_root,
            missing_ok=True,
        ) is not None:
            raise ValueError("Diagnostics identity key files are incomplete")
        orphan_size = private_file_size(path, trusted_root=authority_root)
        unlink_private_file(path, trusted_root=authority_root)
        if capacity is not None:
            capacity.account_deletion(old_size=orphan_size)
        path_exists = False
    if path_exists or metadata_exists:
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
        atomic_write_bytes(path, material, trusted_root=authority_root)
        atomic_write_json(metadata_path, metadata, trusted_root=authority_root)
        if reservation is not None:
            reservation.commit_append(len(material) + len(encoded_metadata))
    except Exception:
        if reservation is not None:
            actual = sum(
                private_file_size(
                    candidate,
                    trusted_root=authority_root,
                    missing_ok=True,
                )
                for candidate in (path, metadata_path)
            )
            reservation.commit_append(actual)
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
        trusted_root: Path | None = None,
    ) -> None:
        requested_root = Path(root)
        self.trusted_root = (
            Path(trusted_root) if trusted_root is not None else requested_root.parent
        )
        self.root = ensure_private_directory(
            requested_root,
            trusted_root=self.trusted_root,
        )
        self.path = resolve_beneath(self.root, "identity-map.enc")
        self.key_store = key_store
        self.active_key = active_key
        self.capacity = capacity
        self.identity_key = identity_key or load_or_create_identity_key(
            self.root,
            capacity=capacity,
            trusted_root=self.trusted_root,
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
        if private_file_stat(
            self.path,
            trusted_root=self.trusted_root,
            missing_ok=True,
        ) is None:
            return {}
        plaintext = decrypt_blob(
            self.key_store,
            read_private_bytes(
                self.path,
                max_bytes=16 * 1024 * 1024,
                trusted_root=self.trusted_root,
            ),
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
            atomic_write_bytes(self.path, encrypted, trusted_root=self.trusted_root)
            return
        old_size = private_file_size(
            self.path,
            trusted_root=self.trusted_root,
            missing_ok=True,
        )
        reservation = self.capacity.reserve(len(encrypted), critical=critical)
        try:
            atomic_write_bytes(self.path, encrypted, trusted_root=self.trusted_root)
            reservation.commit_replacement(old_size=old_size, new_size=len(encrypted))
        except Exception:
            new_size = private_file_size(
                self.path,
                trusted_root=self.trusted_root,
                missing_ok=True,
            )
            if not reservation.closed:
                reservation.commit_replacement(old_size=old_size, new_size=new_size)
            raise
