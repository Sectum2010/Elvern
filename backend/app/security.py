from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time


PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 600_000


def hash_password(password: str) -> str:
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
    parts = value.split("$")
    return len(parts) == 4 and parts[0] == PBKDF2_ALGORITHM


def verify_password(password: str, password_hash: str) -> bool:
    parts = password_hash.split("$")
    if len(parts) != 4 or parts[0] != PBKDF2_ALGORITHM:
        return False
    _, raw_iterations, salt_b64, digest_b64 = parts
    try:
        iterations = int(raw_iterations)
        salt = base64.urlsafe_b64decode(salt_b64.encode("utf-8"))
        expected_digest = base64.urlsafe_b64decode(digest_b64.encode("utf-8"))
    except (ValueError, TypeError):
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(candidate, expected_digest)


def generate_session_token() -> str:
    return secrets.token_urlsafe(48)


def hash_session_token(token: str, session_secret: str) -> str:
    return hashlib.sha256(f"{session_secret}:{token}".encode("utf-8")).hexdigest()


class LoginRateLimiter:
    def __init__(
        self,
        window_seconds: int,
        max_attempts: int,
        lockout_seconds: int,
    ) -> None:
        self.window_seconds = window_seconds
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._failures: dict[str, list[float]] = {}
        self._blocked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> int:
        now = time.time()
        with self._lock:
            blocked_until = self._blocked_until.get(key)
            if blocked_until and blocked_until > now:
                return int(blocked_until - now)
            self._blocked_until.pop(key, None)
            recent = [
                timestamp
                for timestamp in self._failures.get(key, [])
                if now - timestamp <= self.window_seconds
            ]
            self._failures[key] = recent
            return 0

    def register_failure(self, key: str) -> int:
        now = time.time()
        with self._lock:
            recent = [
                timestamp
                for timestamp in self._failures.get(key, [])
                if now - timestamp <= self.window_seconds
            ]
            recent.append(now)
            self._failures[key] = recent
            if len(recent) >= self.max_attempts:
                blocked_until = now + self.lockout_seconds
                self._blocked_until[key] = blocked_until
                self._failures[key] = []
                return self.lockout_seconds
        return 0

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._blocked_until.pop(key, None)
