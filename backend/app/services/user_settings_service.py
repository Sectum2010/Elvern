from __future__ import annotations

from io import BytesIO
import re

from PIL import Image, ImageOps, UnidentifiedImageError

from .app_settings_service import get_media_library_reference_payload, validate_media_library_reference
from ..config import Settings
from ..db import get_connection, utcnow_iso


HIDE_DUPLICATE_MOVIES_KEY = "hide_duplicate_movies"
HIDE_RECENTLY_ADDED_KEY = "hide_recently_added"
FLOATING_LIBRARY_SEARCH_ENABLED_KEY = "floating_library_search_enabled"
POSTER_CARD_APPEARANCE_KEY = "poster_card_appearance"
POSTER_CARD_DISPLAY_MAX_WIDTH_KEY = "poster_card_display_max_width"
MEDIA_LIBRARY_REFERENCE_PRIVATE_KEY = "media_library_reference_private"
BACKGROUND_MODE_KEY = "background_mode"
BACKGROUND_PRESET_KEY = "background_preset"
BACKGROUND_GRADIENT_START_KEY = "background_gradient_start"
BACKGROUND_GRADIENT_END_KEY = "background_gradient_end"
BACKGROUND_GRADIENT_ACCENT_KEY = "background_gradient_accent"
BACKGROUND_SOLID_COLOR_KEY = "background_solid_color"
POSTER_CARD_APPEARANCES = {"classic", "modern", "clean"}
POSTER_CARD_DISPLAY_MAX_WIDTHS = {
    "800",
    "1000",
    "1200",
    "1400",
    "1600",
    "1800",
    "2000",
    "2200",
    "original",
}
BACKGROUND_MODES = {"preset", "gradient", "solid", "photo"}
BACKGROUND_PRESETS = {"neon", "basic", "midnight", "aurora", "rose", "ocean"}
BACKGROUND_UPLOAD_MAX_BYTES = 5 * 1024 * 1024
BACKGROUND_UPLOAD_MAX_DIMENSION = 2560
DEFAULT_BACKGROUND_GRADIENT_START = "#74114f"
DEFAULT_BACKGROUND_GRADIENT_END = "#1b41b5"
DEFAULT_BACKGROUND_GRADIENT_ACCENT = "#5c1867"
DEFAULT_BACKGROUND_SOLID_COLOR = "#151a21"
SAFE_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class UserSettingsValidationError(ValueError):
    pass


def _normalize_hex_color(value: str | None, *, field_name: str) -> str:
    candidate = str(value or "").strip()
    if not SAFE_HEX_COLOR_RE.fullmatch(candidate):
        raise UserSettingsValidationError(f"{field_name} must be a hex color like #7c3aed.")
    if len(candidate) == 4:
        candidate = "#" + "".join(character * 2 for character in candidate[1:])
    return candidate.lower()


def _try_normalize_hex_color(value: str | None, fallback: str) -> str:
    try:
        return _normalize_hex_color(value, field_name="Background color")
    except UserSettingsValidationError:
        return fallback


def get_user_background_photo_path(settings: Settings, *, user_id: int):
    return settings.data_dir / "user_backgrounds" / f"user-{int(user_id)}.jpg"


def _background_photo_url(settings: Settings, *, user_id: int) -> str | None:
    photo_path = get_user_background_photo_path(settings, user_id=user_id)
    if not photo_path.is_file():
        return None
    version = photo_path.stat().st_mtime_ns
    return f"/api/user-settings/background-photo?v={version}"


def get_user_settings(settings: Settings, *, user_id: int) -> dict[str, bool | str | None]:
    media_library_reference_payload: dict[str, object]
    values = {
        HIDE_DUPLICATE_MOVIES_KEY: True,
        HIDE_RECENTLY_ADDED_KEY: False,
        FLOATING_LIBRARY_SEARCH_ENABLED_KEY: True,
        POSTER_CARD_APPEARANCE_KEY: "classic",
        POSTER_CARD_DISPLAY_MAX_WIDTH_KEY: "1400",
        BACKGROUND_MODE_KEY: "preset",
        BACKGROUND_PRESET_KEY: "neon",
        BACKGROUND_GRADIENT_START_KEY: DEFAULT_BACKGROUND_GRADIENT_START,
        BACKGROUND_GRADIENT_END_KEY: DEFAULT_BACKGROUND_GRADIENT_END,
        BACKGROUND_GRADIENT_ACCENT_KEY: DEFAULT_BACKGROUND_GRADIENT_ACCENT,
        BACKGROUND_SOLID_COLOR_KEY: DEFAULT_BACKGROUND_SOLID_COLOR,
        "background_photo_url": None,
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
        if row["key"] == FLOATING_LIBRARY_SEARCH_ENABLED_KEY:
            values[FLOATING_LIBRARY_SEARCH_ENABLED_KEY] = row["value"] == "1"
        if row["key"] == POSTER_CARD_APPEARANCE_KEY and row["value"] in POSTER_CARD_APPEARANCES:
            values[POSTER_CARD_APPEARANCE_KEY] = row["value"]
        if row["key"] == POSTER_CARD_DISPLAY_MAX_WIDTH_KEY and row["value"] in POSTER_CARD_DISPLAY_MAX_WIDTHS:
            values[POSTER_CARD_DISPLAY_MAX_WIDTH_KEY] = row["value"]
        if row["key"] == BACKGROUND_MODE_KEY and row["value"] in BACKGROUND_MODES:
            values[BACKGROUND_MODE_KEY] = row["value"]
        if row["key"] == BACKGROUND_PRESET_KEY and row["value"] in BACKGROUND_PRESETS:
            values[BACKGROUND_PRESET_KEY] = row["value"]
        if row["key"] == BACKGROUND_GRADIENT_START_KEY:
            values[BACKGROUND_GRADIENT_START_KEY] = _try_normalize_hex_color(
                row["value"],
                DEFAULT_BACKGROUND_GRADIENT_START,
            )
        if row["key"] == BACKGROUND_GRADIENT_END_KEY:
            values[BACKGROUND_GRADIENT_END_KEY] = _try_normalize_hex_color(
                row["value"],
                DEFAULT_BACKGROUND_GRADIENT_END,
            )
        if row["key"] == BACKGROUND_GRADIENT_ACCENT_KEY:
            values[BACKGROUND_GRADIENT_ACCENT_KEY] = _try_normalize_hex_color(
                row["value"],
                DEFAULT_BACKGROUND_GRADIENT_ACCENT,
            )
        if row["key"] == BACKGROUND_SOLID_COLOR_KEY:
            values[BACKGROUND_SOLID_COLOR_KEY] = _try_normalize_hex_color(
                row["value"],
                DEFAULT_BACKGROUND_SOLID_COLOR,
            )
        if row["key"] == MEDIA_LIBRARY_REFERENCE_PRIVATE_KEY:
            private_value = validate_media_library_reference(value=row["value"])
            values["media_library_reference_private_value"] = private_value
            if private_value:
                values["media_library_reference_effective_value"] = private_value
    photo_url = _background_photo_url(settings, user_id=user_id)
    values["background_photo_url"] = photo_url
    if values[BACKGROUND_MODE_KEY] == "photo" and not photo_url:
        values[BACKGROUND_MODE_KEY] = "preset"
        values[BACKGROUND_PRESET_KEY] = "neon"
    return values


def update_user_settings(
    settings: Settings,
    *,
    user_id: int,
    hide_duplicate_movies: bool | None = None,
    hide_recently_added: bool | None = None,
    floating_library_search_enabled: bool | None = None,
    poster_card_appearance: str | None = None,
    poster_card_display_max_width: str | int | None = None,
    background_mode: str | None = None,
    background_preset: str | None = None,
    background_gradient_start: str | None = None,
    background_gradient_end: str | None = None,
    background_gradient_accent: str | None = None,
    background_solid_color: str | None = None,
    media_library_reference_private_value: str | None = None,
) -> dict[str, bool | str | None]:
    if (
        hide_duplicate_movies is None
        and hide_recently_added is None
        and floating_library_search_enabled is None
        and poster_card_appearance is None
        and poster_card_display_max_width is None
        and background_mode is None
        and background_preset is None
        and background_gradient_start is None
        and background_gradient_end is None
        and background_gradient_accent is None
        and background_solid_color is None
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
    if floating_library_search_enabled is not None:
        updates.append((FLOATING_LIBRARY_SEARCH_ENABLED_KEY, "1" if floating_library_search_enabled else "0"))
    if poster_card_appearance is not None:
        normalized_appearance = poster_card_appearance.strip().lower()
        if normalized_appearance not in POSTER_CARD_APPEARANCES:
            normalized_appearance = "classic"
        updates.append((POSTER_CARD_APPEARANCE_KEY, normalized_appearance))
    if poster_card_display_max_width is not None:
        normalized_max_width = str(poster_card_display_max_width).strip().lower()
        if normalized_max_width not in POSTER_CARD_DISPLAY_MAX_WIDTHS:
            normalized_max_width = "1400"
        updates.append((POSTER_CARD_DISPLAY_MAX_WIDTH_KEY, normalized_max_width))
    if background_mode is not None:
        normalized_background_mode = background_mode.strip().lower()
        if normalized_background_mode not in BACKGROUND_MODES:
            raise UserSettingsValidationError("Background mode is not supported.")
        if normalized_background_mode == "photo" and not get_user_background_photo_path(settings, user_id=user_id).is_file():
            raise UserSettingsValidationError("Upload a background photo before selecting photo mode.")
        updates.append((BACKGROUND_MODE_KEY, normalized_background_mode))
    if background_preset is not None:
        normalized_background_preset = background_preset.strip().lower()
        if normalized_background_preset not in BACKGROUND_PRESETS:
            raise UserSettingsValidationError("Background preset is not supported.")
        updates.append((BACKGROUND_PRESET_KEY, normalized_background_preset))
    if background_gradient_start is not None:
        updates.append((
            BACKGROUND_GRADIENT_START_KEY,
            _normalize_hex_color(background_gradient_start, field_name="Gradient start"),
        ))
    if background_gradient_end is not None:
        updates.append((
            BACKGROUND_GRADIENT_END_KEY,
            _normalize_hex_color(background_gradient_end, field_name="Gradient end"),
        ))
    if background_gradient_accent is not None:
        updates.append((
            BACKGROUND_GRADIENT_ACCENT_KEY,
            _normalize_hex_color(background_gradient_accent, field_name="Gradient accent"),
        ))
    if background_solid_color is not None:
        updates.append((
            BACKGROUND_SOLID_COLOR_KEY,
            _normalize_hex_color(background_solid_color, field_name="Solid background color"),
        ))
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


def get_poster_card_display_max_width(settings: Settings, *, user_id: int) -> int | None:
    value = get_user_settings(settings, user_id=user_id).get(POSTER_CARD_DISPLAY_MAX_WIDTH_KEY)
    if value == "original":
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return settings.poster_card_cache_max_width


def save_user_background_photo(
    settings: Settings,
    *,
    user_id: int,
    content: bytes,
    content_type: str | None = None,
) -> dict[str, bool | str | None]:
    if not content:
        raise UserSettingsValidationError("Choose a JPEG, PNG, or WebP image.")
    if len(content) > BACKGROUND_UPLOAD_MAX_BYTES:
        raise UserSettingsValidationError("Background photo must be 5 MB or smaller.")
    if str(content_type or "").lower() in {"image/svg+xml", "application/svg+xml"}:
        raise UserSettingsValidationError("SVG backgrounds are not supported.")

    try:
        with Image.open(BytesIO(content)) as opened_image:
            image_format = str(opened_image.format or "").upper()
        if image_format not in {"JPEG", "PNG", "WEBP"}:
            raise UserSettingsValidationError("Choose a JPEG, PNG, or WebP image.")
        with Image.open(BytesIO(content)) as opened_image:
            image = ImageOps.exif_transpose(opened_image)
            image.load()
            resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            image.thumbnail(
                (BACKGROUND_UPLOAD_MAX_DIMENSION, BACKGROUND_UPLOAD_MAX_DIMENSION),
                resampling,
            )
            if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
                alpha_source = image.convert("RGBA")
                canvas = Image.new("RGB", alpha_source.size, "#101820")
                canvas.paste(alpha_source, mask=alpha_source.getchannel("A"))
                output_image = canvas
            else:
                output_image = image.convert("RGB")
    except UserSettingsValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise UserSettingsValidationError("Choose a valid JPEG, PNG, or WebP image.") from error

    photo_path = get_user_background_photo_path(settings, user_id=user_id)
    photo_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = photo_path.with_suffix(".tmp")
    output_image.save(
        temporary_path,
        format="JPEG",
        quality=90,
        optimize=True,
        progressive=True,
    )
    temporary_path.replace(photo_path)
    return update_user_settings(
        settings,
        user_id=user_id,
        background_mode="photo",
    )


def delete_user_background_photo(settings: Settings, *, user_id: int) -> dict[str, bool | str | None]:
    photo_path = get_user_background_photo_path(settings, user_id=user_id)
    if photo_path.exists():
        photo_path.unlink()
    return update_user_settings(
        settings,
        user_id=user_id,
        background_mode="preset",
        background_preset="neon",
    )
