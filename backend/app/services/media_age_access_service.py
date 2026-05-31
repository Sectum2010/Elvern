from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status

from ..config import Settings
from ..db import get_connection, utcnow_iso
from ..models import AuthenticatedUser
from .audit_service import log_audit_event
from .title_normalization import normalize_title_key, resolve_title_metadata


AGE_ACCESS_DENIED_TEMPLATE = (
    "You must be {age} years old to view this film. Please contact an admin if your age credentials are incorrect."
)
AGE_ACCESS_DENIED_18 = (
    "You must be 18+ years old to view this film. Please contact an admin if your age credentials are incorrect."
)


@dataclass(frozen=True, slots=True)
class AgeGroupResolution:
    age_group_key: str
    display_title: str
    year: int | None
    source: str


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    keys = getattr(row, "keys", None)
    if callable(keys):
        try:
            if key in row.keys():
                return row[key]
        except Exception:  # noqa: BLE001
            return default
    return getattr(row, key, default)


def format_age_for_display(value: int | None) -> str:
    if value is None:
        return "None"
    age = validate_age_credential(value)
    return "18+" if age == 18 else str(age)


def validate_age_credential(value: object) -> int:
    try:
        age = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Age credential must be a whole number from 1 to 18") from exc
    if age < 1 or age > 18:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Age credential must be a whole number from 1 to 18")
    return age


def validate_age_requirement(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        age = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Age requirement must be None or a whole number from 1 to 18") from exc
    if age < 1 or age > 18:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Age requirement must be None or a whole number from 1 to 18")
    return age


def _fallback_age_group(row: Any) -> AgeGroupResolution:
    item_id = _row_value(row, "id")
    title = str(_row_value(row, "title", "") or "").strip() or f"Media Item {item_id}"
    return AgeGroupResolution(
        age_group_key=f"age:item:{int(item_id)}",
        display_title=title,
        year=None,
        source="item_fallback",
    )


def resolve_age_restriction_movie_group(row_or_item: Any) -> AgeGroupResolution:
    item_id = _row_value(row_or_item, "id")
    if item_id in {None, ""}:
        return _fallback_age_group({"id": 0, "title": "Unknown media item"})
    metadata = resolve_title_metadata(
        title=_row_value(row_or_item, "title"),
        year=_row_value(row_or_item, "year"),
        original_filename=_row_value(row_or_item, "original_filename"),
    )
    if bool(metadata.get("suspicious_output")):
        return _fallback_age_group(row_or_item)
    raw_year = metadata.get("parsed_year") or _row_value(row_or_item, "year")
    try:
        year = int(raw_year)
    except (TypeError, ValueError):
        return _fallback_age_group(row_or_item)
    identity_title = str(
        (metadata.get("poster_match_identity") or {}).get("title")
        or metadata.get("poster_match_title")
        or metadata.get("base_title")
        or ""
    ).strip()
    normalized_title = normalize_title_key(identity_title)
    if not normalized_title:
        return _fallback_age_group(row_or_item)
    return AgeGroupResolution(
        age_group_key=f"age:title:{normalized_title}|{year}",
        display_title=identity_title,
        year=year,
        source="title_year",
    )


def build_age_restriction_movie_key(row_or_item: Any) -> str:
    return resolve_age_restriction_movie_group(row_or_item).age_group_key


def _get_media_item_row(settings: Settings, item_id: int):
    with get_connection(settings) as connection:
        return connection.execute(
            """
            SELECT id, title, original_filename, file_path, COALESCE(source_kind, 'local') AS source_kind, year
            FROM media_items
            WHERE id = ?
            LIMIT 1
            """,
            (item_id,),
        ).fetchone()


def resolve_media_age_group(settings: Settings, item_id: int) -> dict[str, object]:
    row = _get_media_item_row(settings, item_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    group = resolve_age_restriction_movie_group(row)
    return {
        "age_group_key": group.age_group_key,
        "display_title": group.display_title,
        "year": group.year,
        "source": group.source,
    }


def resolve_media_age_requirement(settings: Settings, item_id: int) -> dict[str, object]:
    row = _get_media_item_row(settings, item_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    group = resolve_age_restriction_movie_group(row)
    with get_connection(settings) as connection:
        requirement_row = connection.execute(
            """
            SELECT age_requirement, updated_at, updated_by_user_id
            FROM media_age_requirements
            WHERE age_group_key = ?
            LIMIT 1
            """,
            (group.age_group_key,),
        ).fetchone()
    age_requirement = (
        int(requirement_row["age_requirement"])
        if requirement_row is not None and requirement_row["age_requirement"] is not None
        else None
    )
    return {
        "age_group_key": group.age_group_key,
        "age_group_source": group.source,
        "age_group_display_title": group.display_title,
        "age_group_year": group.year,
        "age_requirement": age_requirement,
        "age_requirement_display": format_age_for_display(age_requirement),
        "age_requirement_updated_at": requirement_row["updated_at"] if requirement_row else None,
        "age_requirement_updated_by_user_id": requirement_row["updated_by_user_id"] if requirement_row else None,
    }


def set_media_age_requirement(
    settings: Settings,
    *,
    item_id: int,
    age_requirement: object,
    actor: AuthenticatedUser,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, object]:
    normalized_requirement = validate_age_requirement(age_requirement)
    group_payload = resolve_media_age_group(settings, item_id)
    now = utcnow_iso()
    with get_connection(settings) as connection:
        connection.execute(
            """
            INSERT INTO media_age_requirements (
                age_group_key,
                display_title,
                year,
                age_requirement,
                updated_at,
                updated_by_user_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(age_group_key) DO UPDATE SET
                display_title = excluded.display_title,
                year = excluded.year,
                age_requirement = excluded.age_requirement,
                updated_at = excluded.updated_at,
                updated_by_user_id = excluded.updated_by_user_id
            """,
            (
                group_payload["age_group_key"],
                group_payload["display_title"],
                group_payload["year"],
                normalized_requirement,
                now,
                actor.id,
            ),
        )
        connection.commit()
    log_audit_event(
        settings,
        action="admin.media_age_requirement.update",
        outcome="success",
        user_id=actor.id,
        username=actor.username,
        role=actor.role,
        session_id=actor.session_id,
        target_type="media_age_group",
        target_id=str(group_payload["age_group_key"]),
        media_item_id=item_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details={
            "age_requirement": normalized_requirement,
            "age_requirement_display": format_age_for_display(normalized_requirement),
            "display_title": group_payload["display_title"],
            "year": group_payload["year"],
        },
    )
    return resolve_media_age_requirement(settings, item_id)


def _age_denied_message(requirement: int) -> str:
    if requirement >= 18:
        return AGE_ACCESS_DENIED_18
    return AGE_ACCESS_DENIED_TEMPLATE.format(age=requirement)


def assert_user_can_access_media_by_age(
    settings: Settings,
    *,
    user: AuthenticatedUser,
    item_id: int,
    purpose: str = "playback",
) -> None:
    del purpose
    if (user.role or "standard_user") == "admin":
        return
    requirement = resolve_media_age_requirement(settings, item_id)["age_requirement"]
    if requirement is None:
        return
    user_age = validate_age_credential(getattr(user, "age_credential", 18))
    if user_age < int(requirement):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_age_denied_message(int(requirement)))


def _load_all_media_age_groups(connection) -> dict[int, AgeGroupResolution]:
    rows = connection.execute(
        """
        SELECT id, title, original_filename, year
        FROM media_items
        """
    ).fetchall()
    return {int(row["id"]): resolve_age_restriction_movie_group(row) for row in rows}


def media_item_ids_for_age_group(settings: Settings, age_group_key: str) -> list[int]:
    with get_connection(settings) as connection:
        groups = _load_all_media_age_groups(connection)
    return sorted(item_id for item_id, group in groups.items() if group.age_group_key == age_group_key)


def revoke_persistent_sessions_for_age_group(
    settings: Settings,
    *,
    age_group_key: str,
    age_requirement: int | None,
    reason: str,
) -> dict[str, object]:
    if age_requirement is None:
        return {"media_item_ids": [], "user_ids": [], "revoked_native": 0, "revoked_downloads": 0, "revoked_desktop": 0}
    item_ids = media_item_ids_for_age_group(settings, age_group_key)
    if not item_ids:
        return {"media_item_ids": [], "user_ids": [], "revoked_native": 0, "revoked_downloads": 0, "revoked_desktop": 0}
    now = utcnow_iso()
    placeholders = ",".join("?" for _ in item_ids)
    with get_connection(settings) as connection:
        user_rows = connection.execute(
            f"""
            SELECT DISTINCT u.id
            FROM users u
            WHERE COALESCE(u.role, 'standard_user') != 'admin'
              AND COALESCE(u.age_credential, 18) < ?
            """,  # nosec B608 - age is parameterized; no dynamic values except placeholders.
            (int(age_requirement),),
        ).fetchall()
        user_ids = [int(row["id"]) for row in user_rows]
        if not user_ids:
            return {"media_item_ids": item_ids, "user_ids": [], "revoked_native": 0, "revoked_downloads": 0, "revoked_desktop": 0}
        user_placeholders = ",".join("?" for _ in user_ids)
        native_cursor = connection.execute(
            f"""
            UPDATE native_playback_sessions
            SET revoked_at = COALESCE(revoked_at, ?),
                last_error = COALESCE(last_error, ?)
            WHERE media_item_id IN ({placeholders})
              AND user_id IN ({user_placeholders})
              AND revoked_at IS NULL
              AND closed_at IS NULL
            """,  # nosec B608 - placeholders generated from trusted list lengths.
            (now, reason, *item_ids, *user_ids),
        )
        download_cursor = connection.execute(
            f"""
            UPDATE download_sessions
            SET revoked_at = COALESCE(revoked_at, ?),
                last_error = COALESCE(last_error, ?)
            WHERE media_item_id IN ({placeholders})
              AND user_id IN ({user_placeholders})
              AND revoked_at IS NULL
              AND completed_at IS NULL
            """,  # nosec B608
            (now, reason, *item_ids, *user_ids),
        )
        desktop_cursor = connection.execute(
            f"""
            UPDATE desktop_vlc_handoffs
            SET revoked_at = COALESCE(revoked_at, ?)
            WHERE media_item_id IN ({placeholders})
              AND user_id IN ({user_placeholders})
              AND revoked_at IS NULL
            """,  # nosec B608
            (now, *item_ids, *user_ids),
        )
        connection.commit()
    return {
        "media_item_ids": item_ids,
        "user_ids": user_ids,
        "revoked_native": int(native_cursor.rowcount or 0),
        "revoked_downloads": int(download_cursor.rowcount or 0),
        "revoked_desktop": int(desktop_cursor.rowcount or 0),
    }


def revoke_persistent_sessions_for_user_age_change(
    settings: Settings,
    *,
    user_id: int,
    reason: str,
) -> dict[str, object]:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        user_row = connection.execute(
            """
            SELECT id, role, age_credential
            FROM users
            WHERE id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if user_row is None or (user_row["role"] or "standard_user") == "admin":
            return {"media_item_ids": [], "user_ids": [], "revoked_native": 0, "revoked_downloads": 0, "revoked_desktop": 0}
        user_age = validate_age_credential(user_row["age_credential"])
        groups = _load_all_media_age_groups(connection)
        requirement_rows = connection.execute(
            """
            SELECT age_group_key, age_requirement
            FROM media_age_requirements
            WHERE age_requirement IS NOT NULL
              AND age_requirement > ?
            """,
            (user_age,),
        ).fetchall()
        restricted_group_keys = {str(row["age_group_key"]) for row in requirement_rows}
        item_ids = sorted(
            item_id
            for item_id, group in groups.items()
            if group.age_group_key in restricted_group_keys
        )
        if not item_ids:
            return {"media_item_ids": [], "user_ids": [int(user_id)], "revoked_native": 0, "revoked_downloads": 0, "revoked_desktop": 0}
        placeholders = ",".join("?" for _ in item_ids)
        native_cursor = connection.execute(
            f"""
            UPDATE native_playback_sessions
            SET revoked_at = COALESCE(revoked_at, ?),
                last_error = COALESCE(last_error, ?)
            WHERE user_id = ?
              AND media_item_id IN ({placeholders})
              AND revoked_at IS NULL
              AND closed_at IS NULL
            """,  # nosec B608
            (now, reason, user_id, *item_ids),
        )
        download_cursor = connection.execute(
            f"""
            UPDATE download_sessions
            SET revoked_at = COALESCE(revoked_at, ?),
                last_error = COALESCE(last_error, ?)
            WHERE user_id = ?
              AND media_item_id IN ({placeholders})
              AND revoked_at IS NULL
              AND completed_at IS NULL
            """,  # nosec B608
            (now, reason, user_id, *item_ids),
        )
        desktop_cursor = connection.execute(
            f"""
            UPDATE desktop_vlc_handoffs
            SET revoked_at = COALESCE(revoked_at, ?)
            WHERE user_id = ?
              AND media_item_id IN ({placeholders})
              AND revoked_at IS NULL
            """,  # nosec B608
            (now, user_id, *item_ids),
        )
        connection.commit()
    return {
        "media_item_ids": item_ids,
        "user_ids": [int(user_id)],
        "revoked_native": int(native_cursor.rowcount or 0),
        "revoked_downloads": int(download_cursor.rowcount or 0),
        "revoked_desktop": int(desktop_cursor.rowcount or 0),
    }
