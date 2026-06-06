from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..config import Settings
from ..db import utcnow_iso
from .app_settings_service import get_global_app_setting, set_global_app_setting


EXPOSURE_MODE_MAINTENANCE_LOCK_KEY = "exposure_mode_maintenance_lock_json"
EXPOSURE_MAINTENANCE_LOCK_MESSAGE = "The server is currently under construction, please try again later"
EXPOSURE_MAINTENANCE_LOCK_REASON = "exposure_mode_preparation"


def maintenance_lock_message() -> str:
    return EXPOSURE_MAINTENANCE_LOCK_MESSAGE


def get_exposure_maintenance_lock(settings: Settings) -> dict[str, Any]:
    try:
        raw_value = get_global_app_setting(settings, key=EXPOSURE_MODE_MAINTENANCE_LOCK_KEY)
    except sqlite3.OperationalError:
        return _disabled_lock()
    if not raw_value:
        return _disabled_lock()
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return _disabled_lock()
    if not isinstance(parsed, dict) or not bool(parsed.get("enabled")):
        return _disabled_lock()
    return {
        "enabled": True,
        "reason": str(parsed.get("reason") or EXPOSURE_MAINTENANCE_LOCK_REASON),
        "message": EXPOSURE_MAINTENANCE_LOCK_MESSAGE,
        "created_by_user_id": _optional_int(parsed.get("created_by_user_id")),
        "created_by_username": _optional_str(parsed.get("created_by_username")),
        "created_at": _optional_str(parsed.get("created_at")),
        "updated_at": _optional_str(parsed.get("updated_at")),
    }


def set_exposure_maintenance_lock(settings: Settings, actor: Any, *, enabled: bool) -> dict[str, Any]:
    if not enabled:
        set_global_app_setting(settings, key=EXPOSURE_MODE_MAINTENANCE_LOCK_KEY, value=None)
        return _disabled_lock()

    now = utcnow_iso()
    existing = get_exposure_maintenance_lock(settings)
    created_at = existing.get("created_at") if existing.get("enabled") else now
    created_by_user_id = existing.get("created_by_user_id") if existing.get("enabled") else getattr(actor, "id", None)
    created_by_username = existing.get("created_by_username") if existing.get("enabled") else getattr(actor, "username", None)
    lock = {
        "enabled": True,
        "reason": EXPOSURE_MAINTENANCE_LOCK_REASON,
        "message": EXPOSURE_MAINTENANCE_LOCK_MESSAGE,
        "created_by_user_id": created_by_user_id,
        "created_by_username": created_by_username,
        "created_at": created_at,
        "updated_at": now,
    }
    set_global_app_setting(
        settings,
        key=EXPOSURE_MODE_MAINTENANCE_LOCK_KEY,
        value=json.dumps(lock, sort_keys=True),
    )
    return lock


def is_exposure_maintenance_lock_enabled(settings: Settings) -> bool:
    return bool(get_exposure_maintenance_lock(settings).get("enabled"))


def _disabled_lock() -> dict[str, Any]:
    return {
        "enabled": False,
        "reason": None,
        "message": EXPOSURE_MAINTENANCE_LOCK_MESSAGE,
        "created_by_user_id": None,
        "created_by_username": None,
        "created_at": None,
        "updated_at": None,
    }


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
