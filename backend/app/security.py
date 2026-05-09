from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError

from .argon2_calibration import resolve_argon2_params


PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 600_000
ARGON2_PREFIX = "$argon2id$"
DUMMY_PASSWORD_FOR_TIMING = "elvern_dummy_verify_password_v1"

logger = logging.getLogger(__name__)
_hasher_cache: dict[int, PasswordHasher] = {}
_dummy_hash_cache: dict[int, str] = {}
_cache_lock = threading.Lock()


def _get_hasher(settings) -> PasswordHasher:
    with _cache_lock:
        cache_key = id(settings)
        if cache_key not in _hasher_cache:
            params = resolve_argon2_params(settings, logger)
            _hasher_cache[cache_key] = PasswordHasher(
                time_cost=params.time_cost,
                memory_cost=params.memory_cost,
                parallelism=params.parallelism,
            )
        return _hasher_cache[cache_key]


def _get_dummy_hash(settings) -> str:
    cache_key = id(settings)
    with _cache_lock:
        cached_hash = _dummy_hash_cache.get(cache_key)
    if cached_hash is not None:
        return cached_hash
    hasher = _get_hasher(settings)
    dummy_hash = hasher.hash(DUMMY_PASSWORD_FOR_TIMING)
    with _cache_lock:
        return _dummy_hash_cache.setdefault(cache_key, dummy_hash)


def hash_password(password: str, settings) -> str:
    hasher = _get_hasher(settings)
    return hasher.hash(password)


def _hash_password_pbkdf2(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    salt_b64 = base64.urlsafe_b64encode(salt).decode("utf-8")
    digest_b64 = base64.urlsafe_b64encode(digest).decode("utf-8")
    return f"{PBKDF2_ALGORITHM}${PBKDF2_ITERATIONS}${salt_b64}${digest_b64}"


def looks_like_password_hash(value: str) -> bool:
    if not isinstance(value, str):
        return False
    if value.startswith(ARGON2_PREFIX):
        return True
    parts = value.split("$")
    return len(parts) == 4 and parts[0] == PBKDF2_ALGORITHM


def _verify_pbkdf2(password: str, password_hash: str) -> bool:
    parts = password_hash.split("$")
    if len(parts) != 4 or parts[0] != PBKDF2_ALGORITHM:
        return False
    _, raw_iterations, salt_b64, digest_b64 = parts
    try:
        iterations = int(raw_iterations)
        salt = base64.urlsafe_b64decode(salt_b64.encode("utf-8"))
        expected_digest = base64.urlsafe_b64decode(digest_b64.encode("utf-8"))
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        )
    except Exception:
        return False
    return hmac.compare_digest(candidate, expected_digest)


def _perform_dummy_verify(settings) -> None:
    try:
        hasher = _get_hasher(settings)
        dummy = _get_dummy_hash(settings)
        try:
            hasher.verify(dummy, "definitely_wrong_password")
        except VerifyMismatchError:
            pass
        except Exception:
            pass
    except Exception:
        pass


def verify_password(
    password: str,
    password_hash: str,
    settings,
) -> tuple[bool, str | None]:
    if not isinstance(password, str) or not isinstance(password_hash, str):
        _perform_dummy_verify(settings)
        return False, None
    if not password_hash:
        _perform_dummy_verify(settings)
        return False, None

    if password_hash.startswith(ARGON2_PREFIX):
        try:
            _get_hasher(settings).verify(password_hash, password)
            return True, None
        except VerifyMismatchError:
            return False, None
        except (InvalidHash, VerificationError, Exception):
            _perform_dummy_verify(settings)
            return False, None

    if password_hash.startswith(PBKDF2_ALGORITHM + "$"):
        if _verify_pbkdf2(password, password_hash):
            try:
                new_hash = hash_password(password, settings)
                return True, new_hash
            except Exception:
                return True, None
        return False, None

    _perform_dummy_verify(settings)
    return False, None


def perform_dummy_verify(settings) -> None:
    _perform_dummy_verify(settings)


def generate_session_token() -> str:
    return secrets.token_urlsafe(48)


def hash_session_token(token: str, session_secret: str) -> str:
    return hashlib.sha256(f"{session_secret}:{token}".encode("utf-8")).hexdigest()
