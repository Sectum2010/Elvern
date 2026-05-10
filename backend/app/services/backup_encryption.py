from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct

from cryptography.fernet import Fernet, InvalidToken


BACKUP_FORMAT_VERSION = 1
BACKUP_KEY_INFO = b"elvern-backup-v1"
PBKDF2_ITERATIONS_BACKUP = 600_000
SALT_BYTES = 16

HEADER_MAGIC = b"ELVERN_BACKUP_V1"
KEY_SOURCE_AUTO = "auto"
KEY_SOURCE_PASSPHRASE = "passphrase"

_KEY_SOURCE_AUTO_BYTE = 0
_KEY_SOURCE_PASSPHRASE_BYTE = 1
_HEADER_LENGTH = len(HEADER_MAGIC) + 1 + SALT_BYTES + 4


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
    }
