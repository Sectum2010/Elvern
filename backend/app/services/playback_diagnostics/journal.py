from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import struct
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .constants import (
    JOURNAL_LENGTH_BYTES,
    JOURNAL_MAGIC,
    JOURNAL_SCHEMA_VERSION,
    MAX_JOURNAL_CHUNK_BYTES,
)
from .crypto import DiagnosticsKey, DiagnosticsKeyStore, NONCE_BYTES
from .fileio import (
    FILE_MODE,
    UnsafeDiagnosticsPathError,
    atomic_write_bytes,
    ensure_private_directory,
    open_private_append,
    resolve_beneath,
)


LENGTH_STRUCT = struct.Struct(">Q")


class DiagnosticsJournalError(ValueError):
    """Raised when an encrypted diagnostics journal is invalid."""


@dataclass(frozen=True, slots=True)
class JournalChunkRecord:
    sequence: int
    offset: int
    end_offset: int
    event_count: int
    plaintext_length: int
    ciphertext_length: int
    previous_chunk_hash: str
    current_chunk_hash: str
    nonce: str


@dataclass(frozen=True, slots=True)
class JournalVerification:
    path: str
    valid: bool
    chunk_count: int
    event_count: int
    last_chunk_hash: str
    recovered_bytes: int = 0
    quarantined_path: str | None = None
    error: str | None = None


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_exact(handle, size: int) -> bytes | None:
    payload = handle.read(size)
    if not payload:
        return None
    if len(payload) != size:
        raise EOFError("truncated journal record")
    return payload


def _quarantine_tail(
    path: Path,
    *,
    offset: int,
    quarantine_root: Path | None,
) -> Path | None:
    if quarantine_root is None:
        return None
    quarantine_root = ensure_private_directory(quarantine_root)
    tail = path.read_bytes()[offset:]
    if not tail:
        return None
    digest = hashlib.sha256(tail).hexdigest()[:16]
    destination = resolve_beneath(
        quarantine_root,
        f"{path.stem}-{int(time.time())}-{digest}.corrupt",
    )
    atomic_write_bytes(destination, tail)
    return destination


def verify_journal(
    path: Path,
    key_store: DiagnosticsKeyStore,
    *,
    recover: bool = False,
    quarantine_root: Path | None = None,
    include_events: bool = False,
) -> tuple[JournalVerification, list[dict[str, Any]]]:
    path = Path(path)
    if path.is_symlink():
        raise UnsafeDiagnosticsPathError(f"Refusing diagnostics journal symlink: {path}")
    events: list[dict[str, Any]] = []
    if not path.exists():
        return JournalVerification(str(path), True, 0, 0, ""), events

    valid_end = 0
    chunk_count = 0
    event_count = 0
    previous_hash = "0" * 64
    error: str | None = None
    corruption_offset: int | None = None
    try:
        with path.open("rb") as handle:
            magic = handle.read(len(JOURNAL_MAGIC))
            if magic != JOURNAL_MAGIC:
                raise DiagnosticsJournalError("Invalid diagnostics journal magic")
            valid_end = len(JOURNAL_MAGIC)
            while True:
                record_offset = handle.tell()
                raw_header_length = _read_exact(handle, JOURNAL_LENGTH_BYTES)
                if raw_header_length is None:
                    break
                header_length = LENGTH_STRUCT.unpack(raw_header_length)[0]
                if header_length <= 0 or header_length > 64_000:
                    raise DiagnosticsJournalError("Invalid diagnostics chunk header length")
                header_bytes = _read_exact(handle, header_length)
                if header_bytes is None:
                    raise EOFError("truncated diagnostics chunk header")
                header = json.loads(header_bytes.decode("utf-8"))
                ciphertext_length_raw = _read_exact(handle, JOURNAL_LENGTH_BYTES)
                if ciphertext_length_raw is None:
                    raise EOFError("truncated diagnostics ciphertext length")
                ciphertext_length = LENGTH_STRUCT.unpack(ciphertext_length_raw)[0]
                if ciphertext_length <= 0 or ciphertext_length > MAX_JOURNAL_CHUNK_BYTES:
                    raise DiagnosticsJournalError("Invalid diagnostics ciphertext length")
                ciphertext = _read_exact(handle, ciphertext_length)
                if ciphertext is None:
                    raise EOFError("truncated diagnostics ciphertext")

                current_hash = str(header.pop("current_chunk_hash", ""))
                if header.get("schema_version") != JOURNAL_SCHEMA_VERSION:
                    raise DiagnosticsJournalError("Unsupported diagnostics journal schema")
                if int(header.get("chunk_sequence") or 0) != chunk_count + 1:
                    raise DiagnosticsJournalError("Diagnostics chunk sequence is not contiguous")
                if str(header.get("previous_chunk_hash") or "") != previous_hash:
                    raise DiagnosticsJournalError("Diagnostics journal hash chain is broken")
                expected_hash = hashlib.sha256(
                    bytes.fromhex(previous_hash) + _canonical_json(header) + ciphertext
                ).hexdigest()
                if not secrets.compare_digest(current_hash, expected_hash):
                    raise DiagnosticsJournalError("Diagnostics chunk hash mismatch")
                nonce = base64.b64decode(str(header.get("nonce") or ""), validate=True)
                if len(nonce) != NONCE_BYTES:
                    raise DiagnosticsJournalError("Invalid diagnostics chunk nonce")
                key = key_store.load_key(str(header.get("key_id") or ""))
                compressed = AESGCM(key.material).decrypt(
                    nonce,
                    ciphertext,
                    _canonical_json(header),
                )
                plaintext = zlib.decompress(compressed)
                if len(plaintext) != int(header.get("plaintext_length") or -1):
                    raise DiagnosticsJournalError("Diagnostics plaintext length mismatch")
                if hashlib.sha256(plaintext).hexdigest() != header.get("plaintext_sha256"):
                    raise DiagnosticsJournalError("Diagnostics plaintext hash mismatch")
                decoded_events = [
                    json.loads(line)
                    for line in plaintext.decode("utf-8").splitlines()
                    if line.strip()
                ]
                if len(decoded_events) != int(header.get("event_count") or -1):
                    raise DiagnosticsJournalError("Diagnostics event count mismatch")
                if include_events:
                    events.extend(decoded_events)
                chunk_count += 1
                event_count += len(decoded_events)
                previous_hash = current_hash
                valid_end = handle.tell()
    except (OSError, EOFError, ValueError, InvalidTag, zlib.error, json.JSONDecodeError) as exc:
        error = str(exc) or exc.__class__.__name__
        corruption_offset = valid_end

    recovered_bytes = 0
    quarantined_path: Path | None = None
    if error is not None and recover:
        quarantined_path = _quarantine_tail(
            path,
            offset=corruption_offset or valid_end,
            quarantine_root=quarantine_root,
        )
        original_size = path.stat().st_size
        with path.open("r+b") as handle:
            handle.truncate(valid_end)
            handle.flush()
            os.fsync(handle.fileno())
        recovered_bytes = max(0, original_size - valid_end)
        error = None

    verification = JournalVerification(
        path=str(path),
        valid=error is None,
        chunk_count=chunk_count,
        event_count=event_count,
        last_chunk_hash=previous_hash if chunk_count else "",
        recovered_bytes=recovered_bytes,
        quarantined_path=str(quarantined_path) if quarantined_path else None,
        error=error,
    )
    return verification, events


class EncryptedJournal:
    def __init__(
        self,
        path: Path,
        *,
        playback_session_id: str,
        source_type: str,
        key_store: DiagnosticsKeyStore,
        active_key: DiagnosticsKey,
        quarantine_root: Path,
    ) -> None:
        self.path = Path(path)
        self.playback_session_id = playback_session_id
        self.source_type = source_type
        self.key_store = key_store
        self.active_key = active_key
        self.quarantine_root = quarantine_root
        ensure_private_directory(self.path.parent)
        if self.path.exists() and self.path.is_symlink():
            raise UnsafeDiagnosticsPathError("Refusing diagnostics journal symlink")
        if not self.path.exists():
            with open_private_append(self.path) as handle:
                handle.write(JOURNAL_MAGIC)
                handle.flush()
                os.fsync(handle.fileno())
        os.chmod(self.path, FILE_MODE)
        verification, _ = verify_journal(
            self.path,
            self.key_store,
            recover=True,
            quarantine_root=self.quarantine_root,
        )
        if not verification.valid:
            raise DiagnosticsJournalError(verification.error or "Invalid diagnostics journal")
        if self.path.stat().st_size == 0:
            with open_private_append(self.path) as handle:
                handle.write(JOURNAL_MAGIC)
                handle.flush()
                os.fsync(handle.fileno())
        self.chunk_sequence = verification.chunk_count
        self.previous_chunk_hash = verification.last_chunk_hash or "0" * 64

    def append(self, events: Iterable[dict[str, Any]]) -> JournalChunkRecord | None:
        event_list = list(events)
        if not event_list:
            return None
        plaintext = b"\n".join(_canonical_json(event) for event in event_list) + b"\n"
        compressed = zlib.compress(plaintext, level=6)
        nonce = secrets.token_bytes(NONCE_BYTES)
        sequence = self.chunk_sequence + 1
        header = {
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "key_id": self.active_key.key_id,
            "playback_session_id": self.playback_session_id,
            "source_type": self.source_type,
            "chunk_sequence": sequence,
            "event_count": len(event_list),
            "plaintext_length": len(plaintext),
            "compressed_length": len(compressed),
            "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
            "previous_chunk_hash": self.previous_chunk_hash,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "created_at_utc": _utc_now(),
        }
        associated_data = _canonical_json(header)
        ciphertext = AESGCM(self.active_key.material).encrypt(
            nonce,
            compressed,
            associated_data,
        )
        if len(ciphertext) > MAX_JOURNAL_CHUNK_BYTES:
            raise DiagnosticsJournalError("Diagnostics chunk is too large")
        current_hash = hashlib.sha256(
            bytes.fromhex(self.previous_chunk_hash) + associated_data + ciphertext
        ).hexdigest()
        stored_header = {**header, "current_chunk_hash": current_hash}
        stored_header_bytes = _canonical_json(stored_header)
        record = (
            LENGTH_STRUCT.pack(len(stored_header_bytes))
            + stored_header_bytes
            + LENGTH_STRUCT.pack(len(ciphertext))
            + ciphertext
        )
        offset = self.path.stat().st_size
        with open_private_append(self.path) as handle:
            handle.write(record)
            handle.flush()
            os.fsync(handle.fileno())
        self.chunk_sequence = sequence
        self.previous_chunk_hash = current_hash
        return JournalChunkRecord(
            sequence=sequence,
            offset=offset,
            end_offset=offset + len(record),
            event_count=len(event_list),
            plaintext_length=len(plaintext),
            ciphertext_length=len(ciphertext),
            previous_chunk_hash=header["previous_chunk_hash"],
            current_chunk_hash=current_hash,
            nonce=header["nonce"],
        )

    def read_events(self) -> list[dict[str, Any]]:
        verification, events = verify_journal(
            self.path,
            self.key_store,
            include_events=True,
        )
        if not verification.valid:
            raise DiagnosticsJournalError(verification.error or "Invalid diagnostics journal")
        return events
