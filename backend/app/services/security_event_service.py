from __future__ import annotations

import json
from typing import Any

from ..config import Settings
from ..db import get_connection, utcnow_iso


def log_security_event(
    settings: Settings,
    *,
    event_kind: str,
    actor_user_id: int | None = None,
    actor_username: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    details_json = None
    if details:
        details_json = json.dumps(details, sort_keys=True, ensure_ascii=True)
    with get_connection(settings) as connection:
        connection.execute(
            """
            INSERT INTO security_events (
                event_kind,
                actor_user_id,
                actor_username,
                ip_address,
                user_agent,
                occurred_at,
                details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_kind,
                actor_user_id,
                actor_username,
                ip_address,
                user_agent,
                utcnow_iso(),
                details_json,
            ),
        )
        connection.commit()
