from __future__ import annotations

from .app_settings_service import get_media_library_reference_payload, validate_media_library_reference
from ..config import Settings
from ..db import get_connection, utcnow_iso


HIDE_DUPLICATE_MOVIES_KEY = "hide_duplicate_movies"
HIDE_RECENTLY_ADDED_KEY = "hide_recently_added"
FLOATING_CONTROLS_POSITION_KEY = "floating_controls_position"
POSTER_CARD_APPEARANCE_KEY = "poster_card_appearance"
MEDIA_LIBRARY_REFERENCE_PRIVATE_KEY = "media_library_reference_private"
FLOATING_CONTROLS_POSITIONS = {"bottom", "top"}
POSTER_CARD_APPEARANCES = {"classic", "modern"}


def get_user_settings(settings: Settings, *, user_id: int) -> dict[str, bool | str | None]:
    media_library_reference_payload: dict[str, object]
    values = {
        HIDE_DUPLICATE_MOVIES_KEY: True,
        HIDE_RECENTLY_ADDED_KEY: False,
        FLOATING_CONTROLS_POSITION_KEY: "bottom",
        POSTER_CARD_APPEARANCE_KEY: "classic",
        "media_library_reference_private_value": None,
        "media_library_reference_shared_default_value": "",
        "media_library_reference_effective_value": "",
    }
    with get_connection(settings) as connection:
        media_library_reference_payload = get_media_library_reference_payload(settings, connection=connection)
        values["media_library_reference_shared_default_value"] = str(
            media_library_reference_payload["effective_value"]
        )
        values["media_library_reference_effective_value"] = str(
            media_library_reference_payload["effective_value"]
        )
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
        if row["key"] == POSTER_CARD_APPEARANCE_KEY and row["value"] in POSTER_CARD_APPEARANCES:
            values[POSTER_CARD_APPEARANCE_KEY] = row["value"]
        if row["key"] == MEDIA_LIBRARY_REFERENCE_PRIVATE_KEY:
            private_value = validate_media_library_reference(value=row["value"])
            values["media_library_reference_private_value"] = private_value
            if private_value:
                values["media_library_reference_effective_value"] = private_value
    return values


def update_user_settings(
    settings: Settings,
    *,
    user_id: int,
    hide_duplicate_movies: bool | None = None,
    hide_recently_added: bool | None = None,
    floating_controls_position: str | None = None,
    poster_card_appearance: str | None = None,
    media_library_reference_private_value: str | None = None,
) -> dict[str, bool | str | None]:
    if (
        hide_duplicate_movies is None
        and hide_recently_added is None
        and floating_controls_position is None
        and poster_card_appearance is None
        and media_library_reference_private_value is None
    ):
        return get_user_settings(settings, user_id=user_id)

    now = utcnow_iso()
    updates: list[tuple[str, str]] = []
    deletes: list[str] = []
    if hide_duplicate_movies is not None:
        updates.append((HIDE_DUPLICATE_MOVIES_KEY, "1" if hide_duplicate_movies else "0"))
    if hide_recently_added is not None:
        updates.append((HIDE_RECENTLY_ADDED_KEY, "1" if hide_recently_added else "0"))
    if floating_controls_position is not None:
        normalized_position = floating_controls_position.strip().lower()
        if normalized_position not in FLOATING_CONTROLS_POSITIONS:
            normalized_position = "bottom"
        updates.append((FLOATING_CONTROLS_POSITION_KEY, normalized_position))
    if poster_card_appearance is not None:
        normalized_appearance = poster_card_appearance.strip().lower()
        if normalized_appearance not in POSTER_CARD_APPEARANCES:
            normalized_appearance = "classic"
        updates.append((POSTER_CARD_APPEARANCE_KEY, normalized_appearance))
    if media_library_reference_private_value is not None:
        normalized_media_library_reference = validate_media_library_reference(
            value=media_library_reference_private_value,
        )
        if normalized_media_library_reference is None:
            deletes.append(MEDIA_LIBRARY_REFERENCE_PRIVATE_KEY)
        else:
            updates.append((MEDIA_LIBRARY_REFERENCE_PRIVATE_KEY, normalized_media_library_reference))
    with get_connection(settings) as connection:
        for key in deletes:
            connection.execute(
                """
                DELETE FROM user_settings
                WHERE user_id = ? AND key = ?
                """,
                (user_id, key),
            )
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
