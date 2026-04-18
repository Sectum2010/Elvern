from __future__ import annotations

from ..config import Settings
from ..db import get_connection, utcnow_iso


HIDE_DUPLICATE_MOVIES_KEY = "hide_duplicate_movies"
HIDE_RECENTLY_ADDED_KEY = "hide_recently_added"
FLOATING_CONTROLS_POSITION_KEY = "floating_controls_position"
FLOATING_CONTROLS_POSITIONS = {"bottom", "top"}


def get_user_settings(settings: Settings, *, user_id: int) -> dict[str, bool | str]:
    values = {
        HIDE_DUPLICATE_MOVIES_KEY: True,
        HIDE_RECENTLY_ADDED_KEY: False,
        FLOATING_CONTROLS_POSITION_KEY: "bottom",
    }
    with get_connection(settings) as connection:
        rows = connection.execute(
            """
            SELECT key, value
            FROM user_settings
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchall()
    for row in rows:
        if row["key"] == HIDE_DUPLICATE_MOVIES_KEY:
            values[HIDE_DUPLICATE_MOVIES_KEY] = row["value"] == "1"
        if row["key"] == HIDE_RECENTLY_ADDED_KEY:
            values[HIDE_RECENTLY_ADDED_KEY] = row["value"] == "1"
        if row["key"] == FLOATING_CONTROLS_POSITION_KEY and row["value"] in FLOATING_CONTROLS_POSITIONS:
            values[FLOATING_CONTROLS_POSITION_KEY] = row["value"]
    return values


def update_user_settings(
    settings: Settings,
    *,
    user_id: int,
    hide_duplicate_movies: bool | None = None,
    hide_recently_added: bool | None = None,
    floating_controls_position: str | None = None,
) -> dict[str, bool | str]:
    if (
        hide_duplicate_movies is None
        and hide_recently_added is None
        and floating_controls_position is None
    ):
        return get_user_settings(settings, user_id=user_id)

    now = utcnow_iso()
    updates: list[tuple[str, str]] = []
    if hide_duplicate_movies is not None:
        updates.append((HIDE_DUPLICATE_MOVIES_KEY, "1" if hide_duplicate_movies else "0"))
    if hide_recently_added is not None:
        updates.append((HIDE_RECENTLY_ADDED_KEY, "1" if hide_recently_added else "0"))
    if floating_controls_position is not None:
        normalized_position = floating_controls_position.strip().lower()
        if normalized_position not in FLOATING_CONTROLS_POSITIONS:
            normalized_position = "bottom"
        updates.append((FLOATING_CONTROLS_POSITION_KEY, normalized_position))
    with get_connection(settings) as connection:
        for key, value in updates:
            connection.execute(
                """
                INSERT INTO user_settings (user_id, key, value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    key,
                    value,
                    now,
                ),
            )
        connection.commit()
    return get_user_settings(settings, user_id=user_id)
