from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings, URL_PREFIX_PATTERN
from .db import get_connection, utcnow_iso
from .spa_static import clear_manifest_cache


URL_PREFIX_LENGTH = 8
URL_PREFIX_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


@dataclass(frozen=True, slots=True)
class UrlPrefixState:
    prefix: str
    generated_at: str
    rotated_count: int


def generate_url_prefix() -> str:
    return "".join(secrets.choice(URL_PREFIX_ALPHABET) for _ in range(URL_PREFIX_LENGTH))


def url_prefix_state_path(settings: Settings) -> Path:
    return settings.data_dir / "url_prefix_state.json"


def load_state(settings: Settings) -> UrlPrefixState | None:
    path = url_prefix_state_path(settings)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        prefix = str(payload["prefix"]).strip().lower()
        generated_at = str(payload["generated_at"])
        rotated_count = int(payload.get("rotated_count", 0))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if not URL_PREFIX_PATTERN.fullmatch(prefix):
        return None
    try:
        _parse_iso_utc(generated_at)
    except ValueError:
        return None
    if rotated_count < 0:
        return None
    return UrlPrefixState(
        prefix=prefix,
        generated_at=generated_at,
        rotated_count=rotated_count,
    )


def save_state(settings: Settings, state: UrlPrefixState) -> None:
    path = url_prefix_state_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(asdict(state), indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def resolve_url_prefix(settings: Settings, logger: logging.Logger) -> str:
    if settings.url_prefix:
        logger.info("URL prefix from env (manual override)")
        return settings.url_prefix

    state = load_state(settings)
    if os.getenv("ELVERN_FORCE_NEW_URL_PREFIX", "").strip() == "1":
        old_prefix = state.prefix if state else ""
        new_prefix = _generate_distinct_prefix(old_prefix)
        rotated_count = (state.rotated_count + 1) if state else 1
        save_state(
            settings,
            UrlPrefixState(
                prefix=new_prefix,
                generated_at=utcnow_iso(),
                rotated_count=rotated_count,
            ),
        )
        with get_connection(settings) as connection:
            connection.execute("DELETE FROM sessions")
            connection.commit()
        logger.warning("Forced URL prefix rotation via env var, all sessions revoked")
        logger.info("Generated new URL prefix: /%s/", new_prefix)
        return new_prefix

    if state is not None:
        logger.info("URL prefix loaded: /%s/", state.prefix)
        days_old = _days_old(state.generated_at)
        if (
            settings.url_prefix_rotation_reminder_days > 0
            and days_old > settings.url_prefix_rotation_reminder_days
        ):
            logger.warning("URL prefix is %s days old, consider rotation", days_old)
        return state.prefix

    prefix = generate_url_prefix()
    save_state(
        settings,
        UrlPrefixState(prefix=prefix, generated_at=utcnow_iso(), rotated_count=0),
    )
    logger.info("Generated new URL prefix: /%s/", prefix)
    logger.info("Bookmark this URL: https://<your-host>/%s/", prefix)
    return prefix


def rotate_url_prefix(
    settings: Settings,
    connection,
    *,
    actor_user_id: int | None = None,
    actor_username: str | None = None,
) -> tuple[str, str]:
    state = load_state(settings)
    old_prefix = settings.url_prefix or (state.prefix if state else "")
    new_prefix = _generate_distinct_prefix(old_prefix)
    clear_manifest_cache()
    save_state(
        settings,
        UrlPrefixState(
            prefix=new_prefix,
            generated_at=utcnow_iso(),
            rotated_count=(state.rotated_count + 1) if state else 1,
        ),
    )
    now = utcnow_iso()
    connection.execute("DELETE FROM sessions")
    details_json = json.dumps({"old": old_prefix, "new": new_prefix}, sort_keys=True)
    connection.execute(
        """
        INSERT INTO security_events (
            event_kind,
            actor_user_id,
            actor_username,
            occurred_at,
            details_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "url_prefix_rotated",
            actor_user_id,
            actor_username,
            now,
            details_json,
        ),
    )
    return old_prefix, new_prefix


def get_url_prefix_status(settings: Settings, prefix: str) -> dict[str, object]:
    state = load_state(settings)
    if state is None or state.prefix != prefix:
        generated_at = None
        rotated_count = 0
        days_old = 0
    else:
        generated_at = state.generated_at
        rotated_count = state.rotated_count
        days_old = _days_old(state.generated_at)
    reminder_due = (
        settings.url_prefix_rotation_reminder_days > 0
        and days_old > settings.url_prefix_rotation_reminder_days
    )
    return {
        "prefix": prefix,
        "generated_at": generated_at,
        "rotated_count": rotated_count,
        "days_old": days_old,
        "rotation_reminder_due": reminder_due,
    }


def _generate_distinct_prefix(old_prefix: str) -> str:
    for _ in range(20):
        candidate = generate_url_prefix()
        if candidate != old_prefix:
            return candidate
    raise RuntimeError("Could not generate a new URL prefix")


def _days_old(value: str) -> int:
    generated = _parse_iso_utc(value)
    now = datetime.now(timezone.utc)
    return max(0, (now - generated).days)


def _parse_iso_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
