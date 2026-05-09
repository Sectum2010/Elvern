from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from backend.app.db import get_connection, init_db
from backend.app.services.rate_limiter_service import SqliteRateLimiter


def _limiter(settings, *, bucket_kind: str = "ip", max_attempts: int = 3) -> SqliteRateLimiter:
    return SqliteRateLimiter(
        settings,
        bucket_kind=bucket_kind,
        window_seconds=60,
        max_attempts=max_attempts,
        lockout_seconds=30,
    )


def _count(settings, table: str, *, bucket_kind: str | None = None, bucket_key: str | None = None) -> int:
    query = f"SELECT COUNT(*) FROM {table}"
    params: tuple[object, ...] = ()
    if bucket_kind is not None and bucket_key is not None:
        query += " WHERE bucket_kind = ? AND bucket_key = ?"
        params = (bucket_kind, bucket_key)
    with get_connection(settings) as connection:
        return int(connection.execute(query, params).fetchone()[0])


class TestSqliteRateLimiter:
    def test_failure_count_under_threshold_returns_zero(self, test_settings) -> None:
        init_db(test_settings)
        limiter = _limiter(test_settings, max_attempts=3)

        assert limiter.register_failure("203.0.113.10") == 0
        assert limiter.register_failure("203.0.113.10") == 0

        assert _count(test_settings, "login_failures", bucket_kind="ip", bucket_key="203.0.113.10") == 2

    def test_lockout_triggers_at_max_attempts(self, test_settings) -> None:
        init_db(test_settings)
        limiter = _limiter(test_settings, max_attempts=2)

        assert limiter.register_failure("203.0.113.10") == 0
        assert limiter.register_failure("203.0.113.10") == 30

        assert limiter.check("203.0.113.10") > 0

    def test_check_during_lockout_returns_remaining_seconds(self, test_settings) -> None:
        init_db(test_settings)
        limiter = _limiter(test_settings, max_attempts=1)

        assert limiter.register_failure("203.0.113.10") == 30

        assert 1 <= limiter.check("203.0.113.10") <= 30

    def test_check_after_lockout_expires_returns_zero(self, test_settings) -> None:
        init_db(test_settings)
        limiter = _limiter(test_settings, max_attempts=1)
        limiter.register_failure("203.0.113.10")
        with get_connection(test_settings) as connection:
            connection.execute(
                """
                UPDATE login_lockouts
                SET blocked_until_unix = ?
                WHERE bucket_kind = 'ip' AND bucket_key = '203.0.113.10'
                """,
                (time.time() - 1,),
            )
            connection.commit()

        assert limiter.check("203.0.113.10") == 0
        assert _count(test_settings, "login_lockouts", bucket_kind="ip", bucket_key="203.0.113.10") == 0

    def test_failures_outside_window_pruned(self, test_settings) -> None:
        init_db(test_settings)
        limiter = _limiter(test_settings, max_attempts=10)
        now = time.time()
        with get_connection(test_settings) as connection:
            connection.execute(
                """
                INSERT INTO login_failures (bucket_kind, bucket_key, occurred_at, occurred_at_unix)
                VALUES ('ip', '203.0.113.10', ?, ?)
                """,
                (datetime.fromtimestamp(now - 200, tz=timezone.utc).isoformat(), now - 200),
            )
            connection.commit()

        limiter.register_failure("203.0.113.10")

        assert _count(test_settings, "login_failures", bucket_kind="ip", bucket_key="203.0.113.10") == 1

    def test_independent_keys_do_not_interfere(self, test_settings) -> None:
        init_db(test_settings)
        limiter = _limiter(test_settings, max_attempts=2)

        limiter.register_failure("203.0.113.10")
        limiter.register_failure("203.0.113.10")

        assert limiter.check("203.0.113.10") > 0
        assert limiter.check("203.0.113.11") == 0

    def test_clear_resets_specific_key(self, test_settings) -> None:
        init_db(test_settings)
        limiter = _limiter(test_settings, max_attempts=2)
        limiter.register_failure("203.0.113.10")
        limiter.register_failure("203.0.113.10")

        limiter.clear("203.0.113.10")

        assert limiter.check("203.0.113.10") == 0
        assert _count(test_settings, "login_failures", bucket_kind="ip", bucket_key="203.0.113.10") == 0
        assert _count(test_settings, "login_lockouts", bucket_kind="ip", bucket_key="203.0.113.10") == 0

    def test_concurrent_register_failure_thread_safe(self, test_settings) -> None:
        init_db(test_settings)
        limiter = _limiter(test_settings, max_attempts=200)
        threads = [
            threading.Thread(target=limiter.register_failure, args=("203.0.113.10",))
            for _index in range(100)
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert _count(test_settings, "login_failures", bucket_kind="ip", bucket_key="203.0.113.10") == 100

    def test_ip_and_username_limiters_are_independent(self, test_settings) -> None:
        init_db(test_settings)
        ip_limiter = _limiter(test_settings, bucket_kind="ip", max_attempts=1)
        username_limiter = _limiter(test_settings, bucket_kind="username", max_attempts=2)

        assert ip_limiter.register_failure("203.0.113.10") == 30

        assert username_limiter.check("203.0.113.10") == 0
        assert username_limiter.register_failure("203.0.113.10") == 0

    def test_lockout_record_replaced_not_duplicated(self, test_settings) -> None:
        init_db(test_settings)
        limiter = _limiter(test_settings, max_attempts=1)

        limiter.register_failure("203.0.113.10")
        limiter.register_failure("203.0.113.10")

        assert _count(test_settings, "login_lockouts", bucket_kind="ip", bucket_key="203.0.113.10") == 1

    def test_seven_day_global_cleanup(self, test_settings) -> None:
        init_db(test_settings)
        limiter = _limiter(test_settings, max_attempts=10)
        now = time.time()
        with get_connection(test_settings) as connection:
            connection.execute(
                """
                INSERT INTO login_failures (bucket_kind, bucket_key, occurred_at, occurred_at_unix)
                VALUES ('username', 'old-user', ?, ?)
                """,
                (datetime.fromtimestamp(now - 8 * 86400, tz=timezone.utc).isoformat(), now - 8 * 86400),
            )
            connection.commit()

        limiter.register_failure("203.0.113.10")

        assert _count(test_settings, "login_failures", bucket_kind="username", bucket_key="old-user") == 0

    def test_check_does_not_modify_state_when_no_lockout(self, test_settings) -> None:
        init_db(test_settings)
        limiter = _limiter(test_settings)
        before_failures = _count(test_settings, "login_failures")
        before_lockouts = _count(test_settings, "login_lockouts")

        assert limiter.check("203.0.113.10") == 0

        assert _count(test_settings, "login_failures") == before_failures
        assert _count(test_settings, "login_lockouts") == before_lockouts
