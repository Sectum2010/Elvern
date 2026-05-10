"""
Symmetric encryption for sensitive at-rest values such as OAuth tokens.

The key is derived from ELVERN_SESSION_SECRET. Rotating that secret invalidates
encrypted blobs that were written with the old secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Final

from cryptography.fernet import Fernet, InvalidToken

from ..config import Settings


logger = logging.getLogger(__name__)

AT_REST_KEY_INFO: Final[bytes] = b"elvern-at-rest-v1"
PLAINTEXT_PREFIX: Final[str] = ""
CIPHERTEXT_PREFIX: Final[str] = "fernet1$"

_fernet_cache: dict[int, Fernet] = {}


def _derive_fernet_key(settings: Settings) -> bytes:
    secret = settings.session_secret.encode("utf-8")
    material = hmac.new(secret, AT_REST_KEY_INFO, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(material)


def _get_fernet(settings: Settings) -> Fernet:
    cache_key = id(settings)
    if cache_key not in _fernet_cache:
        _fernet_cache[cache_key] = Fernet(_derive_fernet_key(settings))
    return _fernet_cache[cache_key]


def encrypt_at_rest(value: str, settings: Settings) -> str:
    """Encrypt a DB-bound string and add a format marker."""
    if not isinstance(value, str):
        raise TypeError("encrypt_at_rest expects str")
    if not value:
        return ""
    token = _get_fernet(settings).encrypt(value.encode("utf-8")).decode("ascii")
    return f"{CIPHERTEXT_PREFIX}{token}"


def decrypt_at_rest(stored: str, settings: Settings) -> tuple[str, bool]:
    """
    Return (plaintext, was_encrypted).

    was_encrypted=False means the stored value is a legacy plaintext value and
    the caller should re-encrypt it after successful use.
    """
    if not isinstance(stored, str):
        raise TypeError("decrypt_at_rest expects str")
    if not stored:
        return ("", False)
    if not stored.startswith(CIPHERTEXT_PREFIX):
        return (stored, False)

    ciphertext = stored[len(CIPHERTEXT_PREFIX):]
    try:
        plaintext = _get_fernet(settings).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        logger.warning("at-rest decrypt failed, key mismatch or corrupted")
        raise ValueError("Invalid encrypted at-rest value") from exc
    return (plaintext, True)
