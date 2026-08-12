from __future__ import annotations

import threading
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from backend.app.auth import create_session
from backend.app.db import (
    AGE_CREDENTIAL_SCHEDULE_MIGRATION,
    _run_age_credential_schedule_migration,
    get_connection,
)
from backend.app.models import AuthenticatedUser
from backend.app.services.account_access_service import create_user_with_invite, generate_invite_code
from backend.app.services.admin_service import create_user, update_user
from backend.app.services.media_age_access_service import format_age_for_display
from backend.app.services.user_age_credential_service import (
    calculate_age_credential_schedule,
    current_local_date,
    reconcile_due_age_credentials,
)


def _admin(settings) -> AuthenticatedUser:
    with get_connection(settings) as connection:
        row = connection.execute("SELECT id, username FROM users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()
    return AuthenticatedUser(id=int(row["id"]), username=str(row["username"]), role="admin")


def _create_underage_user(settings, *, username: str, age: int = 12) -> dict[str, object]:
    return create_user(
        settings,
        username=username,
        password="family-password",
        role="standard_user",
        enabled=True,
        age_credential=age,
        actor=_admin(settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )


@pytest.mark.parametrize(
    ("age", "expected"),
    [(18, "18+"), (17, "17"), (16, "16"), (15, "15"), (13, "13"), (1, "1")],
)
def test_age_credential_display_only_appends_plus_to_18(age: int, expected: str) -> None:
    assert format_age_for_display(age) == expected


def test_age_schedule_handles_anniversary_catch_up_and_cap() -> None:
    normal = calculate_age_credential_schedule(
        anchor_age=12,
        anchor_date=date(2024, 6, 10),
        today=date(2025, 6, 10),
    )
    caught_up = calculate_age_credential_schedule(
        anchor_age=12,
        anchor_date=date(2020, 6, 10),
        today=date(2025, 8, 1),
    )
    capped = calculate_age_credential_schedule(
        anchor_age=17,
        anchor_date=date(2020, 6, 10),
        today=date(2025, 8, 1),
    )

    assert (normal.age, normal.next_increment_on) == (13, date(2026, 6, 10))
    assert (caught_up.age, caught_up.next_increment_on) == (17, date(2026, 6, 10))
    assert (capped.age, capped.next_increment_on) == (18, None)


def test_february_29_anchor_uses_february_28_then_returns_to_leap_day() -> None:
    non_leap = calculate_age_credential_schedule(
        anchor_age=12,
        anchor_date=date(2024, 2, 29),
        today=date(2025, 2, 28),
    )
    before_leap_anniversary = calculate_age_credential_schedule(
        anchor_age=12,
        anchor_date=date(2024, 2, 29),
        today=date(2028, 2, 28),
    )

    assert (non_leap.age, non_leap.next_increment_on) == (13, date(2026, 2, 28))
    assert (before_leap_anniversary.age, before_leap_anniversary.next_increment_on) == (15, date(2028, 2, 29))


def test_current_local_date_changes_at_configured_timezone_midnight(initialized_settings) -> None:
    settings = replace(initialized_settings, timezone_name="America/Los_Angeles")

    assert current_local_date(settings, now=datetime(2026, 8, 12, 6, 59, tzinfo=timezone.utc)) == date(2026, 8, 11)
    assert current_local_date(settings, now=datetime(2026, 8, 12, 7, 0, tzinfo=timezone.utc)) == date(2026, 8, 12)


def test_age_schedule_migration_anchors_underage_and_stops_18(initialized_settings) -> None:
    child = _create_underage_user(initialized_settings, username="migration-age-child", age=13)
    with get_connection(initialized_settings) as connection:
        connection.execute("DELETE FROM schema_migrations WHERE name = ?", (AGE_CREDENTIAL_SCHEDULE_MIGRATION,))
        connection.execute(
            """
            UPDATE users
            SET age_credential_anchor_age = NULL,
                age_credential_anchor_date = NULL,
                age_credential_next_increment_on = NULL
            """
        )
        connection.commit()
        _run_age_credential_schedule_migration(connection, settings=initialized_settings)
        child_row = connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (child["id"],),
        ).fetchone()
        admin_row = connection.execute(
            "SELECT * FROM users WHERE role = 'admin' ORDER BY id LIMIT 1",
        ).fetchone()
        migration_row_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE name = ?",
            (AGE_CREDENTIAL_SCHEDULE_MIGRATION,),
        ).fetchone()[0]

    expected_anchor = current_local_date(initialized_settings)
    assert child_row["age_credential_anchor_age"] == 13
    assert child_row["age_credential_anchor_date"] == expected_anchor.isoformat()
    assert child_row["age_credential_next_increment_on"] is not None
    assert admin_row["age_credential"] == 18
    assert admin_row["age_credential_next_increment_on"] is None
    assert migration_row_count == 1


def test_manual_same_and_different_age_saves_reset_anchor(initialized_settings, monkeypatch) -> None:
    child = _create_underage_user(initialized_settings, username="manual-age-child", age=12)
    actor = _admin(initialized_settings)
    dates = iter((date(2026, 1, 2), date(2026, 3, 4)))
    monkeypatch.setattr(
        "backend.app.services.admin_service.current_local_date",
        lambda settings: next(dates),
    )

    update_user(
        initialized_settings,
        user_id=int(child["id"]),
        enabled=None,
        role=None,
        age_credential=12,
        current_admin_password=None,
        actor=actor,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    with get_connection(initialized_settings) as connection:
        same = connection.execute("SELECT * FROM users WHERE id = ?", (child["id"],)).fetchone()
    update_user(
        initialized_settings,
        user_id=int(child["id"]),
        enabled=None,
        role=None,
        age_credential=14,
        current_admin_password=None,
        actor=actor,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    with get_connection(initialized_settings) as connection:
        changed = connection.execute("SELECT * FROM users WHERE id = ?", (child["id"],)).fetchone()

    assert (same["age_credential_anchor_age"], same["age_credential_anchor_date"]) == (12, "2026-01-02")
    assert (changed["age_credential_anchor_age"], changed["age_credential_anchor_date"]) == (14, "2026-03-04")


def test_invite_user_anchor_is_account_creation_date(initialized_settings) -> None:
    actor = _admin(initialized_settings)
    invite = generate_invite_code(
        initialized_settings,
        actor=actor,
        ip_address="127.0.0.1",
        user_agent="pytest",
        assigned_age=11,
    )
    user = create_user_with_invite(
        initialized_settings,
        username="invite-age-child",
        password="family-password",
        confirm_password="family-password",
        invite_code=str(invite["code"]),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    with get_connection(initialized_settings) as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user.id,)).fetchone()

    assert row["age_credential_anchor_age"] == 11
    assert row["age_credential_anchor_date"] == current_local_date(initialized_settings).isoformat()


def test_reconciliation_before_due_date_does_not_write(initialized_settings) -> None:
    child = _create_underage_user(initialized_settings, username="not-due-age-child", age=12)
    with get_connection(initialized_settings) as connection:
        before = connection.execute("SELECT * FROM users WHERE id = ?", (child["id"],)).fetchone()

    assert reconcile_due_age_credentials(
        initialized_settings,
        user_id=int(child["id"]),
        today=date.fromisoformat(str(before["age_credential_anchor_date"])),
    ) == {}
    with get_connection(initialized_settings) as connection:
        after = connection.execute("SELECT * FROM users WHERE id = ?", (child["id"],)).fetchone()

    assert dict(after) == dict(before)


def test_concurrent_reconciliation_is_idempotent_and_bumps_revision_once(initialized_settings) -> None:
    child = _create_underage_user(initialized_settings, username="concurrent-age-child", age=12)
    user_id = int(child["id"])
    with get_connection(initialized_settings) as connection:
        connection.execute(
            """
            UPDATE users
            SET age_credential_anchor_age = 12,
                age_credential_anchor_date = '2024-01-10',
                age_credential_next_increment_on = '2025-01-10'
            WHERE id = ?
            """,
            (user_id,),
        )
        connection.commit()
        before_revision = connection.execute(
            """
            SELECT COALESCE(counter, 0) FROM library_revision_counters
            WHERE scope_kind = 'user' AND scope_id = ? AND layer = 'permission'
            """,
            (user_id,),
        ).fetchone()
    baseline = int(before_revision[0]) if before_revision is not None else 0
    barrier = threading.Barrier(3)
    errors: list[Exception] = []

    def reconcile() -> None:
        try:
            barrier.wait(timeout=2)
            reconcile_due_age_credentials(initialized_settings, user_id=user_id, today=date(2025, 1, 10))
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=reconcile) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=5)

    with get_connection(initialized_settings) as connection:
        row = connection.execute("SELECT age_credential FROM users WHERE id = ?", (user_id,)).fetchone()
        after_revision = connection.execute(
            """
            SELECT counter FROM library_revision_counters
            WHERE scope_kind = 'user' AND scope_id = ? AND layer = 'permission'
            """,
            (user_id,),
        ).fetchone()
        audit_count = connection.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action = 'user.age_credential.automatic_increment' AND user_id = ?",
            (user_id,),
        ).fetchone()[0]

    assert errors == []
    assert row["age_credential"] == 13
    assert int(after_revision["counter"]) - baseline == 1
    assert audit_count == 1


def test_automatic_age_increase_does_not_revoke_session(initialized_settings) -> None:
    child = _create_underage_user(initialized_settings, username="automatic-age-session", age=12)
    user = AuthenticatedUser(
        id=int(child["id"]),
        username=str(child["username"]),
        role="standard_user",
        age_credential=12,
    )
    create_session(initialized_settings, user, ip_address="127.0.0.1", user_agent="pytest")
    with get_connection(initialized_settings) as connection:
        connection.execute(
            """
            UPDATE users
            SET age_credential_anchor_age = 12,
                age_credential_anchor_date = '2024-01-10',
                age_credential_next_increment_on = '2025-01-10'
            WHERE id = ?
            """,
            (user.id,),
        )
        connection.commit()

    reconcile_due_age_credentials(initialized_settings, user_id=user.id, today=date(2025, 1, 10))
    with get_connection(initialized_settings) as connection:
        session = connection.execute("SELECT revoked_at FROM sessions WHERE user_id = ?", (user.id,)).fetchone()

    assert session["revoked_at"] is None


def test_manual_age_decrease_invokes_strict_revocation(initialized_settings, monkeypatch) -> None:
    child = _create_underage_user(initialized_settings, username="manual-age-revoke", age=17)
    calls: list[dict[str, object]] = []

    def revoke(settings, **kwargs):
        del settings
        calls.append(kwargs)
        return {"revoked_native": 0, "revoked_downloads": 0, "revoked_desktop": 0}

    monkeypatch.setattr("backend.app.services.admin_service.revoke_persistent_sessions_for_user_age_change", revoke)
    update_user(
        initialized_settings,
        user_id=int(child["id"]),
        enabled=None,
        role=None,
        age_credential=13,
        current_admin_password=None,
        actor=_admin(initialized_settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    assert calls == [{"user_id": int(child["id"]), "reason": "user_age_credential_changed"}]
