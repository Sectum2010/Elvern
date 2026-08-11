from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
from pathlib import Path
from typing import BinaryIO

from cryptography.fernet import Fernet, InvalidToken
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .backup_keyring_service import BackupKeyringService


BACKUP_FORMAT_VERSION = 1
BACKUP_KEY_INFO = b"elvern-backup-v1"
PBKDF2_ITERATIONS_BACKUP = 600_000
SALT_BYTES = 16

HEADER_MAGIC = b"ELVERN_BACKUP_V1"
HEADER_MAGIC_V2 = b"ELVERN_BACKUP_V2\n"
KEY_SOURCE_AUTO = "auto"
KEY_SOURCE_PASSPHRASE = "passphrase"
BACKUP_FORMAT_VERSION_V2 = 2
BACKUP_ALGORITHM_V2 = "AES-256-GCM"
NONCE_BYTES_V2 = 12
TAG_BYTES_V2 = 16
FOOTER_MAGIC_V2 = b"ELV2LEN1"
FOOTER_BYTES_V2 = len(FOOTER_MAGIC_V2) + 8
KEY_VERIFIER_BYTES_V2 = 16
STREAM_CHUNK_BYTES = 1024 * 1024
PASSPHRASE_MIN_LENGTH = 12
PASSPHRASE_MAX_LENGTH = 1024

_KEY_SOURCE_AUTO_BYTE = 0
_KEY_SOURCE_PASSPHRASE_BYTE = 1
_HEADER_LENGTH = len(HEADER_MAGIC) + 1 + SALT_BYTES + 4


class BackupEncryptionError(ValueError):
    code = "backup_encryption_failed"


class BackupPassphraseRequiredError(BackupEncryptionError):
    code = "backup_passphrase_required"


class BackupWrongPassphraseError(BackupEncryptionError):
    code = "backup_passphrase_invalid"


class BackupIntegrityError(BackupEncryptionError):
    code = "backup_corrupt"


class BackupTruncatedError(BackupIntegrityError):
    code = "backup_truncated"


class BackupUnsupportedFormatError(BackupEncryptionError):
    code = "backup_format_unsupported"


class BackupKeyUnavailableError(BackupEncryptionError):
    code = "backup_key_unavailable"


def derive_backup_key_auto(settings) -> bytes:
    secret = settings.session_secret.encode("utf-8")
    material = hmac.new(secret, BACKUP_KEY_INFO, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(material)


def derive_backup_key_passphrase(passphrase: str, salt: bytes, iterations: int) -> bytes:
    material = hashlib.pbkdf2_hmac(
        "sha256",
        passphrase.encode("utf-8"),
        salt,
        iterations,
    )
    return base64.urlsafe_b64encode(material)


def encrypt_backup(tarball_bytes: bytes, *, settings, passphrase: str | None = None) -> bytes:
    if passphrase:
        salt = secrets.token_bytes(SALT_BYTES)
        iterations = PBKDF2_ITERATIONS_BACKUP
        key = derive_backup_key_passphrase(passphrase, salt, iterations)
        header = HEADER_MAGIC + bytes([_KEY_SOURCE_PASSPHRASE_BYTE]) + salt + struct.pack(">I", iterations)
    else:
        salt = b"\x00" * SALT_BYTES
        iterations = 0
        key = derive_backup_key_auto(settings)
        header = HEADER_MAGIC + bytes([_KEY_SOURCE_AUTO_BYTE]) + salt + struct.pack(">I", iterations)
    return header + Fernet(key).encrypt(tarball_bytes)


def decrypt_backup(blob: bytes, *, settings, passphrase: str | None = None) -> bytes:
    if blob.startswith(HEADER_MAGIC_V2):
        aad, header = _read_v2_header_bytes(blob)
        envelope_revision = int(header.get("envelope_revision") or 1)
        if envelope_revision == 2:
            minimum_size = len(aad) + TAG_BYTES_V2 + FOOTER_BYTES_V2
            if len(blob) < minimum_size:
                raise BackupTruncatedError("Encrypted backup is truncated")
            footer_start = len(blob) - FOOTER_BYTES_V2
            footer = blob[footer_start:]
            if not footer.startswith(FOOTER_MAGIC_V2):
                raise BackupTruncatedError("Encrypted backup length footer is missing")
            declared_ciphertext_bytes = struct.unpack(">Q", footer[len(FOOTER_MAGIC_V2):])[0]
            tag_start = footer_start - TAG_BYTES_V2
            actual_ciphertext_bytes = tag_start - len(aad)
            if declared_ciphertext_bytes != actual_ciphertext_bytes:
                raise BackupTruncatedError("Encrypted backup length does not match its footer")
            ciphertext = blob[len(aad):tag_start]
            tag = blob[tag_start:footer_start]
        elif envelope_revision == 1:
            if len(blob) < len(aad) + TAG_BYTES_V2:
                raise BackupTruncatedError("Encrypted backup is truncated")
            ciphertext = blob[len(aad):-TAG_BYTES_V2]
            tag = blob[-TAG_BYTES_V2:]
        else:
            raise BackupUnsupportedFormatError("Backup envelope revision is unsupported")
        key = _resolve_v2_key(settings=settings, header=header, passphrase=passphrase)
        nonce = base64.b64decode(str(header["nonce_b64"]), validate=True)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(aad)
        try:
            return decryptor.update(ciphertext) + decryptor.finalize()
        except InvalidTag as exc:
            raise BackupIntegrityError("Backup authentication failed") from exc
    if not blob.startswith(HEADER_MAGIC):
        raise ValueError("Not an Elvern encrypted backup")
    if len(blob) <= _HEADER_LENGTH:
        raise ValueError("Encrypted backup is truncated")

    offset = len(HEADER_MAGIC)
    key_source = blob[offset]
    offset += 1
    salt = blob[offset:offset + SALT_BYTES]
    offset += SALT_BYTES
    iterations = struct.unpack(">I", blob[offset:offset + 4])[0]
    offset += 4
    ciphertext = blob[offset:]

    if key_source == _KEY_SOURCE_AUTO_BYTE:
        key = derive_backup_key_auto(settings)
    elif key_source == _KEY_SOURCE_PASSPHRASE_BYTE:
        if not passphrase:
            raise ValueError("This backup requires a passphrase")
        key = derive_backup_key_passphrase(passphrase, salt, iterations)
    else:
        raise ValueError(f"Unknown key source {key_source}")

    try:
        return Fernet(key).decrypt(ciphertext)
    except InvalidToken as exc:
        raise ValueError("Backup decryption failed (wrong passphrase or corrupt)") from exc


def inspect_encrypted_backup_header(blob: bytes) -> dict[str, object]:
    if blob.startswith(HEADER_MAGIC_V2):
        try:
            _, header = _read_v2_header_bytes(blob)
        except BackupEncryptionError:
            return {"encrypted": True, "key_source": None, "format_version": 2}
        return {
            "encrypted": True,
            "key_source": header.get("key_source"),
            "format_version": 2,
            "algorithm": header.get("algorithm"),
            "key_id": header.get("key_id"),
        }
    if not blob.startswith(HEADER_MAGIC) or len(blob) <= _HEADER_LENGTH:
        return {
            "encrypted": False,
            "key_source": None,
        }
    key_source_byte = blob[len(HEADER_MAGIC)]
    key_source = {
        _KEY_SOURCE_AUTO_BYTE: KEY_SOURCE_AUTO,
        _KEY_SOURCE_PASSPHRASE_BYTE: KEY_SOURCE_PASSPHRASE,
    }.get(key_source_byte)
    return {
        "encrypted": key_source is not None,
        "key_source": key_source,
        "format_version": 1,
    }


def validate_backup_passphrase(passphrase: str) -> str:
    value = str(passphrase or "")
    if len(value) < PASSPHRASE_MIN_LENGTH:
        raise ValueError(f"Backup passphrase must be at least {PASSPHRASE_MIN_LENGTH} characters")
    if len(value) > PASSPHRASE_MAX_LENGTH:
        raise ValueError(f"Backup passphrase must be at most {PASSPHRASE_MAX_LENGTH} characters")
    return value


def _derive_v2_passphrase_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, dklen=32)


def _key_verifier(key: bytes, nonce: bytes) -> str:
    value = hmac.new(key, b"elvern-backup-v2-key-verifier\0" + nonce, hashlib.sha256).digest()
    return base64.b64encode(value[:KEY_VERIFIER_BYTES_V2]).decode("ascii")


def _encode_v2_header(header: dict[str, object]) -> tuple[bytes, bytes]:
    header_bytes = json.dumps(header, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(header_bytes) > 64 * 1024:
        raise BackupEncryptionError("Backup header is too large")
    prefix = HEADER_MAGIC_V2 + struct.pack(">I", len(header_bytes))
    return prefix + header_bytes, header_bytes


def _read_v2_header_bytes(blob: bytes) -> tuple[bytes, dict[str, object]]:
    minimum = len(HEADER_MAGIC_V2) + 4
    if len(blob) < minimum or not blob.startswith(HEADER_MAGIC_V2):
        raise BackupUnsupportedFormatError("Not an Elvern V2 backup")
    header_length = struct.unpack(">I", blob[len(HEADER_MAGIC_V2):minimum])[0]
    if header_length <= 0 or header_length > 64 * 1024:
        raise BackupIntegrityError("Backup header length is invalid")
    header_end = minimum + header_length
    if len(blob) < header_end:
        raise BackupTruncatedError("Encrypted backup is truncated")
    header_blob = blob[minimum:header_end]
    try:
        header = json.loads(header_blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupIntegrityError("Backup header is invalid") from exc
    if not isinstance(header, dict):
        raise BackupIntegrityError("Backup header is invalid")
    return blob[:header_end], header


def _read_v2_header(handle: BinaryIO) -> tuple[bytes, dict[str, object]]:
    prefix = handle.read(len(HEADER_MAGIC_V2) + 4)
    if len(prefix) < len(HEADER_MAGIC_V2) + 4 or not prefix.startswith(HEADER_MAGIC_V2):
        raise BackupUnsupportedFormatError("Not an Elvern V2 backup")
    header_length = struct.unpack(">I", prefix[-4:])[0]
    if header_length <= 0 or header_length > 64 * 1024:
        raise BackupIntegrityError("Backup header length is invalid")
    header_blob = handle.read(header_length)
    if len(header_blob) != header_length:
        raise BackupTruncatedError("Encrypted backup is truncated")
    try:
        header = json.loads(header_blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupIntegrityError("Backup header is invalid") from exc
    if not isinstance(header, dict):
        raise BackupIntegrityError("Backup header is invalid")
    return prefix + header_blob, header


def _resolve_v2_key(*, settings, header: dict[str, object], passphrase: str | None) -> bytes:
    if header.get("format_version") != BACKUP_FORMAT_VERSION_V2 or header.get("algorithm") != BACKUP_ALGORITHM_V2:
        raise BackupUnsupportedFormatError("Backup format or algorithm is unsupported")
    try:
        nonce = base64.b64decode(str(header["nonce_b64"]), validate=True)
    except (KeyError, ValueError) as exc:
        raise BackupIntegrityError("Backup nonce is invalid") from exc
    if len(nonce) != NONCE_BYTES_V2:
        raise BackupIntegrityError("Backup nonce is invalid")
    key_source = header.get("key_source")
    if key_source == KEY_SOURCE_AUTO:
        key_id = str(header.get("key_id") or "")
        try:
            key = BackupKeyringService(settings).read_key(key_id).key
        except (OSError, ValueError) as exc:
            raise BackupKeyUnavailableError("The backup encryption key is unavailable") from exc
    elif key_source == KEY_SOURCE_PASSPHRASE:
        if not passphrase:
            raise BackupPassphraseRequiredError("This backup requires a passphrase")
        try:
            salt = base64.b64decode(str(header["salt_b64"]), validate=True)
            iterations = int(header["pbkdf2_iterations"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BackupIntegrityError("Backup passphrase parameters are invalid") from exc
        if len(salt) != SALT_BYTES or iterations < PBKDF2_ITERATIONS_BACKUP:
            raise BackupIntegrityError("Backup passphrase parameters are invalid")
        key = _derive_v2_passphrase_key(validate_backup_passphrase(passphrase), salt, iterations)
    else:
        raise BackupUnsupportedFormatError("Backup key source is unsupported")
    if not hmac.compare_digest(str(header.get("key_verifier") or ""), _key_verifier(key, nonce)):
        if key_source == KEY_SOURCE_PASSPHRASE:
            raise BackupWrongPassphraseError("Wrong backup passphrase")
        raise BackupIntegrityError("Backup key verification failed")
    return key


class V2EncryptingWriter:
    def __init__(self, handle: BinaryIO, *, settings, passphrase: str | None = None) -> None:
        nonce = secrets.token_bytes(NONCE_BYTES_V2)
        if passphrase:
            passphrase = validate_backup_passphrase(passphrase)
            salt = secrets.token_bytes(SALT_BYTES)
            key = _derive_v2_passphrase_key(passphrase, salt, PBKDF2_ITERATIONS_BACKUP)
            key_source = KEY_SOURCE_PASSPHRASE
            key_id = None
        else:
            backup_key = BackupKeyringService(settings).active_write_key()
            salt = b""
            key = backup_key.key
            key_source = KEY_SOURCE_AUTO
            key_id = backup_key.key_id
        header = {
            "algorithm": BACKUP_ALGORITHM_V2,
            "envelope_revision": 2,
            "format_version": BACKUP_FORMAT_VERSION_V2,
            "key_id": key_id,
            "key_source": key_source,
            "key_verifier": _key_verifier(key, nonce),
            "nonce_b64": base64.b64encode(nonce).decode("ascii"),
            "pbkdf2_iterations": PBKDF2_ITERATIONS_BACKUP if passphrase else 0,
            "salt_b64": base64.b64encode(salt).decode("ascii") if salt else "",
        }
        aad, _ = _encode_v2_header(header)
        handle.write(aad)
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(aad)
        self.handle = handle
        self.encryptor = encryptor
        self.header = header
        self.closed = False
        self.bytes_in = 0

    def write(self, data: bytes) -> int:
        if self.closed:
            raise ValueError("Backup encryption writer is closed")
        if not data:
            return 0
        self.handle.write(self.encryptor.update(data))
        self.bytes_in += len(data)
        return len(data)

    def flush(self) -> None:
        self.handle.flush()

    def close(self) -> None:
        if self.closed:
            return
        self.handle.write(self.encryptor.finalize())
        self.handle.write(self.encryptor.tag)
        self.handle.write(FOOTER_MAGIC_V2)
        self.handle.write(struct.pack(">Q", self.bytes_in))
        self.handle.flush()
        self.closed = True


def decrypt_backup_file_v2(
    source_path: str | Path,
    destination_path: str | Path,
    *,
    settings,
    passphrase: str | None = None,
) -> dict[str, object]:
    source = Path(source_path)
    destination = Path(destination_path)
    file_size = source.stat().st_size
    with source.open("rb") as source_handle:
        aad, header = _read_v2_header(source_handle)
        ciphertext_start = source_handle.tell()
        envelope_revision = int(header.get("envelope_revision") or 1)
        if envelope_revision == 2:
            if file_size < ciphertext_start + TAG_BYTES_V2 + FOOTER_BYTES_V2:
                raise BackupTruncatedError("Encrypted backup is truncated")
            footer_start = file_size - FOOTER_BYTES_V2
            source_handle.seek(footer_start)
            footer = source_handle.read(FOOTER_BYTES_V2)
            if not footer.startswith(FOOTER_MAGIC_V2):
                raise BackupTruncatedError("Encrypted backup length footer is missing")
            declared_ciphertext_bytes = struct.unpack(">Q", footer[len(FOOTER_MAGIC_V2):])[0]
            tag_start = footer_start - TAG_BYTES_V2
            ciphertext_remaining = tag_start - ciphertext_start
            if declared_ciphertext_bytes != ciphertext_remaining:
                raise BackupTruncatedError("Encrypted backup length does not match its footer")
            source_handle.seek(tag_start)
            tag = source_handle.read(TAG_BYTES_V2)
        elif envelope_revision == 1:
            if file_size < ciphertext_start + TAG_BYTES_V2:
                raise BackupTruncatedError("Encrypted backup is truncated")
            source_handle.seek(file_size - TAG_BYTES_V2)
            tag = source_handle.read(TAG_BYTES_V2)
            ciphertext_remaining = file_size - ciphertext_start - TAG_BYTES_V2
        else:
            raise BackupUnsupportedFormatError("Backup envelope revision is unsupported")
        source_handle.seek(ciphertext_start)
        key = _resolve_v2_key(settings=settings, header=header, passphrase=passphrase)
        nonce = base64.b64decode(str(header["nonce_b64"]), validate=True)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(aad)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("wb") as destination_handle:
                while ciphertext_remaining > 0:
                    chunk = source_handle.read(min(STREAM_CHUNK_BYTES, ciphertext_remaining))
                    if not chunk:
                        raise BackupTruncatedError("Encrypted backup is truncated")
                    ciphertext_remaining -= len(chunk)
                    destination_handle.write(decryptor.update(chunk))
                destination_handle.write(decryptor.finalize())
                destination_handle.flush()
                os.fsync(destination_handle.fileno())
        except Exception as exc:
            destination.unlink(missing_ok=True)
            if isinstance(exc, BackupEncryptionError):
                raise
            if isinstance(exc, InvalidTag):
                raise BackupIntegrityError("Backup authentication failed") from exc
            raise
    return header


def inspect_encrypted_backup_file_header(path: str | Path) -> dict[str, object]:
    with Path(path).open("rb") as handle:
        prefix = handle.read(max(len(HEADER_MAGIC), len(HEADER_MAGIC_V2)) + 65540)
    return inspect_encrypted_backup_header(prefix)
