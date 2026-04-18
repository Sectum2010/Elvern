from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any

from ..config import Settings
from ..db import get_connection, utcnow_iso


AUDIT_RETENTION_DAYS = 90


def _cleanup_audit_logs_in_connection(connection, *, now_iso: str | None = None) -> None:
    current = now_iso or utcnow_iso()
    cutoff = (
        datetime.fromisoformat(current).astimezone(timezone.utc) - timedelta(days=AUDIT_RETENTION_DAYS)
    ).isoformat()
    connection.execute(
        """
        DELETE FROM audit_logs
        WHERE created_at <= ?
        """,
        (cutoff,),
    )


def log_audit_event(
    settings: Settings,
    *,
    action: str,
    outcome: str,
    user_id: int | None = None,
    username: str | None = None,
    role: str | None = None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    media_item_id: int | None = None,
    session_id: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    details_json = None
    if details:
        details_json = json.dumps(details, sort_keys=True, ensure_ascii=True)

    with get_connection(settings) as connection:
        _cleanup_audit_logs_in_connection(connection)
        connection.execute(
            """
            INSERT INTO audit_logs (
                created_at,
                user_id,
                username,
                role,
                action,
                outcome,
                target_type,
                target_id,
                media_item_id,
                session_id,
                ip_address,
                user_agent,
                details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utcnow_iso(),
                user_id,
                username,
                role,
                action,
                outcome,
                target_type,
                str(target_id) if target_id is not None else None,
                media_item_id,
                session_id,
                ip_address,
                user_agent,
                details_json,
            ),
        )
        connection.commit()


def list_recent_audit_events(settings: Settings, *, limit: int = 100) -> list[dict[str, object]]:
    capped_limit = min(max(limit, 1), 500)
    with get_connection(settings) as connection:
        _cleanup_audit_logs_in_connection(connection)
        connection.commit()
        rows = connection.execute(
            """
            SELECT
                id,
                created_at,
                user_id,
                username,
                role,
                action,
                outcome,
                target_type,
                target_id,
                media_item_id,
                session_id,
                ip_address,
                user_agent,
                details_json
            FROM audit_logs
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (capped_limit,),
        ).fetchall()
    payload: list[dict[str, object]] = []
    for row in rows:
        details = None
        if row["details_json"]:
            try:
                details = json.loads(row["details_json"])
            except json.JSONDecodeError:
                details = {"raw": row["details_json"]}
        payload.append(
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "user_id": row["user_id"],
                "username": row["username"],
                "role": row["role"],
                "action": row["action"],
                "outcome": row["outcome"],
                "target_type": row["target_type"],
                "target_id": row["target_id"],
                "media_item_id": row["media_item_id"],
                "session_id": row["session_id"],
                "ip_address": row["ip_address"],
                "user_agent": row["user_agent"],
                "details": details,
            }
        )
    return payload
