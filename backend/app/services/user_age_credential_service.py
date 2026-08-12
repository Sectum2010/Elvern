from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..db import get_connection, utcnow_iso
from .admin_events_service import emit_admin_event
from .audit_service import log_audit_event


MAX_AGE_CREDENTIAL = 18


@dataclass(frozen=True, slots=True)
class AgeCredentialSchedule:
    age: int
    anchor_age: int
    anchor_date: date
    next_increment_on: date | None


def _anniversary(anchor: date, year: int) -> date:
    try:
        return anchor.replace(year=year)
    except ValueError:
        # A Feb 29 anchor advances on Feb 28 in non-leap years and returns to Feb 29 in leap years.
        return date(year, 2, 28)


def calculate_age_credential_schedule(
    *,
    anchor_age: int,
    anchor_date: date,
    today: date,
) -> AgeCredentialSchedule:
    normalized_anchor_age = max(1, min(MAX_AGE_CREDENTIAL, int(anchor_age)))
    if normalized_anchor_age >= MAX_AGE_CREDENTIAL:
        return AgeCredentialSchedule(MAX_AGE_CREDENTIAL, normalized_anchor_age, anchor_date, None)

    elapsed = 0
    candidate_year = anchor_date.year + 1
    while normalized_anchor_age + elapsed < MAX_AGE_CREDENTIAL:
        anniversary = _anniversary(anchor_date, candidate_year)
        if anniversary > today:
            break
        elapsed += 1
        candidate_year += 1
    age = min(MAX_AGE_CREDENTIAL, normalized_anchor_age + elapsed)
    next_increment = None if age >= MAX_AGE_CREDENTIAL else _anniversary(anchor_date, candidate_year)
    return AgeCredentialSchedule(age, normalized_anchor_age, anchor_date, next_increment)


def new_age_credential_schedule(*, age: int, today: date) -> AgeCredentialSchedule:
    normalized_age = max(1, min(MAX_AGE_CREDENTIAL, int(age)))
    next_increment = None if normalized_age >= MAX_AGE_CREDENTIAL else _anniversary(today, today.year + 1)
    return AgeCredentialSchedule(normalized_age, normalized_age, today, next_increment)


def current_local_date(settings, *, now: datetime | None = None) -> date:
    zone = ZoneInfo(settings.timezone_name)
    return (now.astimezone(zone) if now is not None else datetime.now(zone)).date()


def reconcile_due_age_credentials(
    settings,
    *,
    user_id: int | None = None,
    username: str | None = None,
    session_token_hash: str | None = None,
    today: date | None = None,
) -> dict[int, int]:
    local_today = today or current_local_date(settings)
    conditions = ["COALESCE(u.age_credential, 18) < 18", "u.age_credential_next_increment_on <= ?"]
    parameters: list[object] = [local_today.isoformat()]
    joins = ""
    if user_id is not None:
        conditions.append("u.id = ?")
        parameters.append(int(user_id))
    elif username is not None:
        conditions.append("u.username = ?")
        parameters.append(str(username))
    elif session_token_hash is not None:
        joins = "JOIN sessions s ON s.user_id = u.id"
        conditions.append("s.session_token_hash = ?")
        parameters.append(str(session_token_hash))

    changed: list[tuple[int, str, int, int]] = []
    results: dict[int, int] = {}
    with get_connection(settings) as connection:
        rows = connection.execute(
            f"""
            SELECT DISTINCT
                u.id, u.username, u.age_credential,
                u.age_credential_anchor_age, u.age_credential_anchor_date,
                u.age_credential_next_increment_on
            FROM users u
            {joins}
            WHERE {' AND '.join(conditions)}
            """,  # nosec B608 - clauses are selected from fixed strings above.
            parameters,
        ).fetchall()
        now = utcnow_iso()
        for row in rows:
            try:
                anchor_date = date.fromisoformat(str(row["age_credential_anchor_date"]))
                anchor_age = int(row["age_credential_anchor_age"])
            except (TypeError, ValueError):
                anchor_date = local_today
                anchor_age = int(row["age_credential"] or MAX_AGE_CREDENTIAL)
            schedule = calculate_age_credential_schedule(
                anchor_age=anchor_age,
                anchor_date=anchor_date,
                today=local_today,
            )
            previous_age = int(row["age_credential"] or MAX_AGE_CREDENTIAL)
            next_iso = schedule.next_increment_on.isoformat() if schedule.next_increment_on else None
            cursor = connection.execute(
                """
                UPDATE users
                SET age_credential = ?, age_credential_anchor_age = ?,
                    age_credential_anchor_date = ?, age_credential_next_increment_on = ?,
                    updated_at = CASE WHEN age_credential != ? THEN ? ELSE updated_at END
                WHERE id = ?
                  AND COALESCE(age_credential, 18) = ?
                  AND age_credential_next_increment_on <= ?
                """,
                (
                    schedule.age,
                    schedule.anchor_age,
                    schedule.anchor_date.isoformat(),
                    next_iso,
                    schedule.age,
                    now,
                    row["id"],
                    previous_age,
                    local_today.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                continue
            results[int(row["id"])] = schedule.age
            if schedule.age > previous_age:
                changed.append((int(row["id"]), str(row["username"]), previous_age, schedule.age))
        connection.commit()

    for changed_user_id, changed_username, previous_age, current_age in changed:
        emit_admin_event("user_age_credential_advanced", user_id=changed_user_id)
        log_audit_event(
            settings,
            action="user.age_credential.automatic_increment",
            outcome="success",
            user_id=changed_user_id,
            username=changed_username,
            role=None,
            target_type="user",
            target_id=changed_user_id,
            details={"previous_age": previous_age, "age_credential": current_age},
        )
    return results
