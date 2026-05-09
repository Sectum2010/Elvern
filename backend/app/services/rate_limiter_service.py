from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone

from ..db import get_connection


LOGIN_FAILURE_RETENTION_SECONDS = 7 * 86400


class SqliteRateLimiter:
    """
    Thread-safe SQLite-backed rate limiter.

    Each check reads SQLite directly; there is no in-memory decision cache.
    """

    def __init__(
        self,
        settings,
        *,
        bucket_kind: str,
        window_seconds: int,
        max_attempts: int,
        lockout_seconds: int,
    ) -> None:
        if bucket_kind not in {"ip", "username"}:
            raise ValueError("bucket_kind must be 'ip' or 'username'")
        self.settings = settings
        self.bucket_kind = bucket_kind
        self.window_seconds = window_seconds
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._lock = threading.Lock()

    def check(self, key: str) -> int:
        normalized_key = _normalize_key(key)
        now = time.time()
        with self._lock:
            with get_connection(self.settings) as connection:
                row = connection.execute(
                    """
                    SELECT blocked_until_unix
                    FROM login_lockouts
                    WHERE bucket_kind = ? AND bucket_key = ?
                    LIMIT 1
                    """,
                    (self.bucket_kind, normalized_key),
                ).fetchone()
                if row is None:
                    return 0
                blocked_until = float(row["blocked_until_unix"])
                if blocked_until > now:
                    return max(1, math.ceil(blocked_until - now))
                connection.execute(
                    """
                    DELETE FROM login_lockouts
                    WHERE bucket_kind = ? AND bucket_key = ?
                    """,
                    (self.bucket_kind, normalized_key),
                )
                connection.commit()
        return 0

    def register_failure(self, key: str) -> int:
        normalized_key = _normalize_key(key)
        now = time.time()
        occurred_at = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
        window_cutoff = now - self.window_seconds
        retention_cutoff = now - LOGIN_FAILURE_RETENTION_SECONDS
        with self._lock:
            with get_connection(self.settings) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO login_failures (
                        bucket_kind,
                        bucket_key,
                        occurred_at,
                        occurred_at_unix
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (self.bucket_kind, normalized_key, occurred_at, now),
                )
                connection.execute(
                    """
                    DELETE FROM login_failures
                    WHERE bucket_kind = ?
                      AND bucket_key = ?
                      AND occurred_at_unix < ?
                    """,
                    (self.bucket_kind, normalized_key, window_cutoff),
                )
                connection.execute(
                    """
                    DELETE FROM login_failures
                    WHERE occurred_at_unix < ?
                    """,
                    (retention_cutoff,),
                )
                count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM login_failures
                        WHERE bucket_kind = ?
                          AND bucket_key = ?
                          AND occurred_at_unix >= ?
                        """,
                        (self.bucket_kind, normalized_key, window_cutoff),
                    ).fetchone()[0]
                )
                if count >= self.max_attempts:
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO login_lockouts (
                            bucket_kind,
                            bucket_key,
                            blocked_until_unix,
                            reason
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            self.bucket_kind,
                            normalized_key,
                            now + self.lockout_seconds,
                            f"{self.bucket_kind}_login_failure_threshold",
                        ),
                    )
                    connection.execute(
                        """
                        DELETE FROM login_failures
                        WHERE bucket_kind = ? AND bucket_key = ?
                        """,
                        (self.bucket_kind, normalized_key),
                    )
                    connection.commit()
                    return self.lockout_seconds
                connection.commit()
        return 0

    def clear(self, key: str) -> None:
        normalized_key = _normalize_key(key)
        with self._lock:
            with get_connection(self.settings) as connection:
                connection.execute(
                    """
                    DELETE FROM login_failures
                    WHERE bucket_kind = ? AND bucket_key = ?
                    """,
                    (self.bucket_kind, normalized_key),
                )
                connection.execute(
                    """
                    DELETE FROM login_lockouts
                    WHERE bucket_kind = ? AND bucket_key = ?
                    """,
                    (self.bucket_kind, normalized_key),
                )
                connection.commit()


def count_recent_failures(settings, *, bucket_kind: str, bucket_key: str, since_unix: float) -> int:
    with get_connection(settings) as connection:
        return int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM login_failures
                WHERE bucket_kind = ?
                  AND bucket_key = ?
                  AND occurred_at_unix >= ?
                """,
                (bucket_kind, _normalize_key(bucket_key), since_unix),
            ).fetchone()[0]
        )


def _normalize_key(key: str) -> str:
    normalized = str(key or "").strip()
    return normalized or "unknown"
