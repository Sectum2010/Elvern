from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import struct
import tempfile
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
    LEGACY_JOURNAL_MAGIC,
    LEGACY_JOURNAL_SCHEMA_VERSION,
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


class IncompleteJournalTailError(EOFError):
    """Raised only when the final physical journal record is incomplete."""


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
    schema_version: str | None = None
    playback_session_id: str | None = None
    source_id: str | None = None
    source_type: str | None = None
    incomplete_tail: bool = False


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
        raise IncompleteJournalTailError("truncated final journal record")
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
    digest_builder = hashlib.sha256()
    tail_size = 0
    with path.open("rb") as source:
        source.seek(offset)
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest_builder.update(chunk)
            tail_size += len(chunk)
    if tail_size == 0:
        return None
    digest = digest_builder.hexdigest()[:16]
    destination = resolve_beneath(
        quarantine_root,
        f"{path.stem}-{int(time.time())}-{digest}.corrupt",
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=quarantine_root,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, FILE_MODE)
        with path.open("rb") as source, os.fdopen(descriptor, "wb", closefd=True) as target:
            source.seek(offset)
            shutil.copyfileobj(source, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_path, destination)
        os.chmod(destination, FILE_MODE)
    finally:
        temporary_path.unlink(missing_ok=True)
    return destination


def verify_journal(
    path: Path,
    key_store: DiagnosticsKeyStore,
    *,
    recover: bool = False,
    quarantine_root: Path | None = None,
    include_events: bool = False,
    annotate_events: bool = False,
    expected_playback_session_id: str | None = None,
    expected_source_id: str | None = None,
    expected_source_type: str | None = None,
    capacity: Any | None = None,
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
    incomplete_tail = False
    corruption_offset: int | None = None
    journal_schema: str | None = None
    bound_session_id: str | None = None
    bound_source_id: str | None = None
    bound_source_type: str | None = None
    try:
        with path.open("rb") as handle:
            magic = handle.read(len(JOURNAL_MAGIC))
            if magic == JOURNAL_MAGIC:
                journal_schema = JOURNAL_SCHEMA_VERSION
            elif magic == LEGACY_JOURNAL_MAGIC:
                journal_schema = LEGACY_JOURNAL_SCHEMA_VERSION
            else:
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
                    raise IncompleteJournalTailError("truncated final diagnostics chunk header")
                header = json.loads(header_bytes.decode("utf-8"))
                ciphertext_length_raw = _read_exact(handle, JOURNAL_LENGTH_BYTES)
                if ciphertext_length_raw is None:
                    raise IncompleteJournalTailError("truncated final diagnostics ciphertext length")
                ciphertext_length = LENGTH_STRUCT.unpack(ciphertext_length_raw)[0]
                if ciphertext_length <= 0 or ciphertext_length > MAX_JOURNAL_CHUNK_BYTES:
                    raise DiagnosticsJournalError("Invalid diagnostics ciphertext length")
                ciphertext = _read_exact(handle, ciphertext_length)
                if ciphertext is None:
                    raise IncompleteJournalTailError("truncated final diagnostics ciphertext")

                current_hash = str(header.pop("current_chunk_hash", ""))
                if header.get("schema_version") != journal_schema:
                    raise DiagnosticsJournalError("Unsupported diagnostics journal schema")
                chunk_session_id = str(header.get("playback_session_id") or "")
                chunk_source_type = str(header.get("source_type") or "")
                chunk_source_id = str(header.get("source_id") or "")
                if journal_schema == JOURNAL_SCHEMA_VERSION and not chunk_source_id:
                    raise DiagnosticsJournalError("Diagnostics journal source id is missing")
                if bound_session_id is None:
                    bound_session_id = chunk_session_id
                    bound_source_id = chunk_source_id or None
                    bound_source_type = chunk_source_type
                elif (
                    chunk_session_id != bound_session_id
                    or chunk_source_type != bound_source_type
                    or (journal_schema == JOURNAL_SCHEMA_VERSION and chunk_source_id != bound_source_id)
                ):
                    raise DiagnosticsJournalError("Diagnostics journal source identity changed")
                if expected_playback_session_id and chunk_session_id != expected_playback_session_id:
                    raise DiagnosticsJournalError("Diagnostics journal session identity mismatch")
                if expected_source_type and chunk_source_type != expected_source_type:
                    raise DiagnosticsJournalError("Diagnostics journal source type mismatch")
                if (
                    expected_source_id
                    and journal_schema == JOURNAL_SCHEMA_VERSION
                    and chunk_source_id != expected_source_id
                ):
                    raise DiagnosticsJournalError("Diagnostics journal source identity mismatch")
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
                    if annotate_events:
                        events.extend(
                            {
                                **event,
                                "_journal_chunk_sequence": chunk_count + 1,
                                "_journal_chunk_hash": current_hash,
                            }
                            for event in decoded_events
                        )
                    else:
                        events.extend(decoded_events)
                chunk_count += 1
                event_count += len(decoded_events)
                previous_hash = current_hash
                valid_end = handle.tell()
    except IncompleteJournalTailError as exc:
        error = str(exc) or exc.__class__.__name__
        corruption_offset = valid_end
        incomplete_tail = True
    except (OSError, ValueError, InvalidTag, zlib.error, json.JSONDecodeError) as exc:
        error = str(exc) or exc.__class__.__name__
        corruption_offset = valid_end

    recovered_bytes = 0
    quarantined_path: Path | None = None
    if error is not None and recover and incomplete_tail:
        original_size = path.stat().st_size
        tail_size = max(0, original_size - valid_end)
        reservation = capacity.reserve(tail_size, critical=True) if capacity else None
        try:
            quarantined_path = _quarantine_tail(
                path,
                offset=corruption_offset or valid_end,
                quarantine_root=quarantine_root,
            )
            with path.open("r+b") as handle:
                handle.truncate(valid_end)
                handle.flush()
                os.fsync(handle.fileno())
            recovered_bytes = tail_size
            if reservation is not None:
                # The quarantine copy replaces the removed journal tail, so the
                # final root usage is unchanged after the temporary peak.
                reservation.commit(0)
            error = None
            incomplete_tail = False
        except Exception:
            if reservation is not None:
                if quarantined_path and quarantined_path.exists():
                    reservation.commit(quarantined_path.stat().st_size)
                else:
                    reservation.release()
            raise

    verification = JournalVerification(
        path=str(path),
        valid=error is None,
        chunk_count=chunk_count,
        event_count=event_count,
        last_chunk_hash=previous_hash if chunk_count else "",
        recovered_bytes=recovered_bytes,
        quarantined_path=str(quarantined_path) if quarantined_path else None,
        error=error,
        schema_version=journal_schema,
        playback_session_id=bound_session_id,
        source_id=bound_source_id,
        source_type=bound_source_type,
        incomplete_tail=incomplete_tail,
    )
    return verification, events


class EncryptedJournal:
    def __init__(
        self,
        path: Path,
        *,
        playback_session_id: str,
        source_type: str,
        source_id: str | None = None,
        key_store: DiagnosticsKeyStore,
        active_key: DiagnosticsKey,
        quarantine_root: Path,
        capacity: Any | None = None,
    ) -> None:
        self.path = Path(path)
        self.playback_session_id = playback_session_id
        self.source_type = source_type
        self.source_id = source_id or f"{source_type}_{playback_session_id}"
        self.key_store = key_store
        self.active_key = active_key
        self.quarantine_root = quarantine_root
        self.capacity = capacity
        ensure_private_directory(self.path.parent)
        if self.path.exists() and self.path.is_symlink():
            raise UnsafeDiagnosticsPathError("Refusing diagnostics journal symlink")
        if self.path.exists():
            os.chmod(self.path, FILE_MODE)
        verification, _ = verify_journal(
            self.path,
            self.key_store,
            recover=True,
            quarantine_root=self.quarantine_root,
            expected_playback_session_id=self.playback_session_id,
            expected_source_id=self.source_id,
            expected_source_type=self.source_type,
            capacity=self.capacity,
        )
        if not verification.valid:
            raise DiagnosticsJournalError(verification.error or "Invalid diagnostics journal")
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
            "source_id": self.source_id,
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
        existing_size = self.path.stat().st_size if self.path.exists() else 0
        offset = existing_size or len(JOURNAL_MAGIC)
        with open_private_append(self.path) as handle:
            if existing_size == 0:
                handle.write(JOURNAL_MAGIC)
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
            expected_playback_session_id=self.playback_session_id,
            expected_source_id=self.source_id,
            expected_source_type=self.source_type,
        )
        if not verification.valid:
            raise DiagnosticsJournalError(verification.error or "Invalid diagnostics journal")
        return events
