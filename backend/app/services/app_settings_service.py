from __future__ import annotations

from dataclasses import dataclass
import logging
import shutil
import sqlite3
import subprocess
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, status

from ..config import Settings
from ..db import get_connection, utcnow_iso
from ..media_scan import scan_media_library
from .local_library_source_service import (
    MEDIA_LIBRARY_REFERENCE_KEY,
    get_effective_shared_local_library_path,
    get_effective_library_reference_locations,
    get_library_reference_category_summary,
    ensure_shared_local_library_source,
    serialize_library_reference_locations,
    purge_shared_local_media_items,
    update_shared_local_library_path,
    validate_library_reference_locations,
)
from .local_path_security import (
    LIBRARY_REFERENCE_HELP_TEXT,
    is_restricted_settings_browse_path,
    validate_safe_directory_browse_path,
    validate_safe_existing_local_directory,
    validate_safe_library_reference_path,
    validate_safe_poster_reference_path,
)
from .poster_index_service import invalidate_poster_indexes
from .library_revision_mutation_service import bump_library_revision_layers


POSTER_REFERENCE_LOCATION_KEY = "poster_reference_location"
GOOGLE_OAUTH_CLIENT_ID_KEY = "google_oauth_client_id"
GOOGLE_OAUTH_CLIENT_SECRET_KEY = "google_oauth_client_secret"
GOOGLE_DRIVE_HTTPS_ORIGIN_KEY = "google_drive_https_origin"
DIRECTORY_PICKER_TITLES = {
    "library_reference": "Select library reference directory",
    "poster_reference": "Select poster reference directory",
    "generic": "Select directory",
}
DIRECTORY_PICKER_FAILURE_MESSAGE = "Failed to open the host directory picker."

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NativeDirectoryPickerCommand:
    backend: str
    argv: list[str]


def _poster_reference_default_path(settings: Settings) -> Path:
    try:
        shared_root = get_effective_shared_local_library_path(settings)
    except HTTPException:
        shared_root = Path(settings.media_root).expanduser().resolve(strict=False)
    default_path = (shared_root / "Posters").resolve(strict=False)
    if not is_restricted_settings_browse_path(settings, default_path):
        return default_path
    fallback = (Path.home() / "Videos" / "Elvern Posters").expanduser().resolve(strict=False)
    if not is_restricted_settings_browse_path(settings, fallback):
        return fallback
    return Path("/srv/media/Posters")


def get_global_app_setting(
    settings: Settings,
    *,
    key: str,
    connection: sqlite3.Connection | None = None,
) -> str | None:
    if connection is not None:
        row = connection.execute(
            """
            SELECT value
            FROM app_settings
            WHERE key = ?
            LIMIT 1
            """,
            (key,),
        ).fetchone()
        return str(row["value"]) if row and row["value"] is not None else None

    with get_connection(settings) as db:
        row = db.execute(
            """
            SELECT value
            FROM app_settings
            WHERE key = ?
            LIMIT 1
            """,
            (key,),
        ).fetchone()
        return str(row["value"]) if row and row["value"] is not None else None


def set_global_app_setting(
    settings: Settings,
    *,
    key: str,
    value: str | None,
    connection: sqlite3.Connection | None = None,
) -> bool:
    if connection is not None:
        current = get_global_app_setting(settings, key=key, connection=connection)
        if current == value:
            return False
        if value is None:
            connection.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        else:
            connection.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, utcnow_iso()),
            )
        return True
    with get_connection(settings) as owned_connection:
        changed = set_global_app_setting(
            settings,
            key=key,
            value=value,
            connection=owned_connection,
        )
        owned_connection.commit()
        return changed


def get_effective_google_oauth_client_id(
    settings: Settings,
    *,
    connection: sqlite3.Connection | None = None,
) -> str | None:
    configured = get_global_app_setting(
        settings,
        key=GOOGLE_OAUTH_CLIENT_ID_KEY,
        connection=connection,
    )
    if configured:
        return configured.strip() or None
    fallback = (settings.google_oauth_client_id or "").strip()
    return fallback or None


def get_effective_google_oauth_client_secret(
    settings: Settings,
    *,
    connection: sqlite3.Connection | None = None,
) -> str | None:
    configured = get_global_app_setting(
        settings,
        key=GOOGLE_OAUTH_CLIENT_SECRET_KEY,
        connection=connection,
    )
    if configured:
        return configured.strip() or None
    fallback = (settings.google_oauth_client_secret or "").strip()
    return fallback or None


def _normalized_secure_origin(candidate: str | None) -> str | None:
    raw = str(candidate or "").strip().rstrip("/")
    if not raw:
        return None
    parsed = urlsplit(raw)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return None
    try:
        ip_address(parsed.hostname)
        return None
    except ValueError:
        pass
    return f"https://{parsed.netloc}"


def get_effective_google_drive_https_origin(
    settings: Settings,
    *,
    connection: sqlite3.Connection | None = None,
) -> str | None:
    configured = get_global_app_setting(
        settings,
        key=GOOGLE_DRIVE_HTTPS_ORIGIN_KEY,
        connection=connection,
    )
    normalized_configured = _normalized_secure_origin(configured)
    if normalized_configured:
        return normalized_configured
    return _normalized_secure_origin(settings.public_app_origin)


def google_drive_callback_url(settings: Settings) -> str:
    https_origin = get_effective_google_drive_https_origin(settings)
    if not https_origin:
        return ""
    return f"{https_origin}/api/cloud-libraries/google/callback"


def google_drive_callback_source(settings: Settings) -> str:
    configured = get_global_app_setting(settings, key=GOOGLE_DRIVE_HTTPS_ORIGIN_KEY)
    if _normalized_secure_origin(configured):
        return "google_drive_https_origin"
    if _normalized_secure_origin(settings.public_app_origin):
        return "public_app_origin"
    return "unconfigured"


def google_drive_setup_instructions(settings: Settings) -> list[str]:
    callback_url = google_drive_callback_url(settings)
    origin = get_effective_google_drive_https_origin(settings) or "Set the HTTPS app origin below first."
    return [
        "Create an OAuth 2.0 Web application credential in Google Cloud.",
        f"Authorized JavaScript origin: {origin}",
        f"Authorized redirect URI: {callback_url or 'Available after you set a secure HTTPS app origin.'}",
        "Paste the HTTPS app origin, Google OAuth Client ID, and Client Secret here and save the setup.",
        "Then use Connect Google Drive below to link your account before adding libraries.",
    ]


def _normalize_google_drive_https_origin(value: str | None) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    normalized = _normalized_secure_origin(candidate)
    if normalized:
        return normalized
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Google Drive HTTPS app origin must be an absolute https:// hostname origin with no path, and it cannot be a raw IP address.",
    )


def _normalize_google_oauth_client_id(value: str | None) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    if any(char.isspace() for char in candidate):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google OAuth Client ID must not contain spaces.",
        )
    if not candidate.endswith(".apps.googleusercontent.com"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google OAuth Client ID should end with .apps.googleusercontent.com.",
        )
    return candidate


def _normalize_google_oauth_client_secret(value: str | None) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    if any(char.isspace() for char in candidate):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google OAuth Client Secret must not contain spaces.",
        )
    return candidate


def get_google_drive_setup_payload(
    settings: Settings,
    *,
    user_id: int,
    connection: sqlite3.Connection | None = None,
) -> dict[str, object]:
    def _build_payload(db: sqlite3.Connection) -> dict[str, object]:
        client_id = get_effective_google_oauth_client_id(settings, connection=db)
        client_secret = get_effective_google_oauth_client_secret(settings, connection=db)
        https_origin = get_effective_google_drive_https_origin(settings, connection=db)
        account_row = db.execute(
            """
            SELECT email, display_name
            FROM google_drive_accounts
            WHERE user_id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        missing_fields: list[str] = []
        if not https_origin:
            missing_fields.append("https_origin")
        if not client_id:
            missing_fields.append("client_id")
        if not client_secret:
            missing_fields.append("client_secret")
        if len(missing_fields) == 3:
            configuration_state = "not_configured"
            configuration_label = "Not configured"
            status_message = "Add a secure HTTPS app origin plus both Google OAuth credentials to enable Google Drive."
        elif missing_fields:
            configuration_state = "partially_configured"
            configuration_label = "Partially configured"
            status_message = "Google Drive setup is still missing one or more required fields."
        else:
            configuration_state = "ready"
            configuration_label = "Ready"
            status_message = "Google Drive OAuth is configured. Connect your Google account to begin."
        callback_source = google_drive_callback_source(settings)
        callback_warning = (
            "Set a stable HTTPS hostname for this Elvern instance before using Google Drive. Raw HTTP and IP-based origins are not accepted by Google web OAuth."
            if callback_source == "unconfigured"
            else None
        )
        return {
            "https_origin": https_origin or "",
            "client_id": client_id or "",
            "client_secret": client_secret or "",
            "javascript_origin": https_origin or "",
            "redirect_uri": google_drive_callback_url(settings),
            "callback_source": callback_source,
            "callback_warning": callback_warning,
            "configuration_state": configuration_state,
            "configuration_label": configuration_label,
            "status_message": status_message,
            "missing_fields": missing_fields,
            "connected": account_row is not None,
            "account_email": str(account_row["email"]) if account_row and account_row["email"] else None,
            "account_name": str(account_row["display_name"]) if account_row and account_row["display_name"] else None,
            "instructions": google_drive_setup_instructions(settings),
        }

    if connection is not None:
        return _build_payload(connection)
    with get_connection(settings) as db:
        return _build_payload(db)


def update_google_drive_setup(
    settings: Settings,
    *,
    user_id: int,
    https_origin: str | None,
    client_id: str | None,
    client_secret: str | None,
) -> dict[str, object]:
    normalized_https_origin = _normalize_google_drive_https_origin(https_origin)
    normalized_client_id = _normalize_google_oauth_client_id(client_id)
    normalized_client_secret = _normalize_google_oauth_client_secret(client_secret)
    with get_connection(settings) as connection:
        if normalized_https_origin is None:
            connection.execute("DELETE FROM app_settings WHERE key = ?", (GOOGLE_DRIVE_HTTPS_ORIGIN_KEY,))
        else:
            connection.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (GOOGLE_DRIVE_HTTPS_ORIGIN_KEY, normalized_https_origin, utcnow_iso()),
            )
        if normalized_client_id is None:
            connection.execute("DELETE FROM app_settings WHERE key = ?", (GOOGLE_OAUTH_CLIENT_ID_KEY,))
        else:
            connection.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (GOOGLE_OAUTH_CLIENT_ID_KEY, normalized_client_id, utcnow_iso()),
            )
        if normalized_client_secret is None:
            connection.execute("DELETE FROM app_settings WHERE key = ?", (GOOGLE_OAUTH_CLIENT_SECRET_KEY,))
        else:
            connection.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (GOOGLE_OAUTH_CLIENT_SECRET_KEY, normalized_client_secret, utcnow_iso()),
            )
        connection.commit()
        return get_google_drive_setup_payload(settings, user_id=user_id, connection=connection)


def media_library_reference_validation_rules(settings: Settings) -> list[str]:
    return [
        f"Leave blank to use the default library reference location: {normalize_media_library_reference_default_path(settings=settings)['default_value']}",
        "Choose one or more parent folders where Elvern should look for media folders.",
        "Use one absolute Linux directory path or local file:// URI per line.",
        f"System folders and Elvern data folders are not accepted. {LIBRARY_REFERENCE_HELP_TEXT}",
        "Elvern auto-discovers folders marked with -M, -TV, -AN, -C, -L, -S, and -X.",
        "Poster reference location stays manually configured below.",
    ]


def normalize_media_library_reference_default_path(*, settings: Settings | None = None) -> dict[str, str]:
    if settings is None:
        raise ValueError("settings is required")
    default_path = validate_safe_library_reference_path(settings, value=None)
    return {
        "default_value": default_path,
        "effective_value": default_path,
    }


def validate_media_library_reference(*, value: str | None) -> str | None:
    normalized = " ".join(str(value or "").split())
    if not normalized:
        return None
    return normalized


def get_media_library_reference_payload(
    settings: Settings,
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, object]:
    default_payload = normalize_media_library_reference_default_path(settings=settings)
    effective_locations = [
        str(path)
        for path in get_effective_library_reference_locations(settings, connection=connection)
    ]
    configured_value = get_global_app_setting(
        settings,
        key=MEDIA_LIBRARY_REFERENCE_KEY,
        connection=connection,
    )
    configured_locations: list[str] = []
    if configured_value:
        configured_locations = effective_locations
    effective_value = "\n".join(effective_locations)
    return {
        "configured_value": "\n".join(configured_locations) if configured_locations else None,
        "effective_value": effective_value,
        "default_value": default_payload["default_value"],
        "configured_locations": configured_locations,
        "effective_locations": effective_locations,
        "category_summary": get_library_reference_category_summary(settings, connection=connection),
        "validation_rules": media_library_reference_validation_rules(settings),
    }


def update_media_library_reference(settings: Settings, *, value: str | None) -> dict[str, object]:
    normalized_locations = validate_library_reference_locations(settings, value=value)
    default_location = normalize_media_library_reference_default_path(settings=settings)["default_value"]
    configured_value = (
        None
        if normalized_locations == [default_location]
        else serialize_library_reference_locations(normalized_locations)
    )
    with get_connection(settings) as connection:
        update_shared_local_library_path(
            settings,
            value=normalized_locations[0],
            connection=connection,
        )
        setting_changed = set_global_app_setting(
            settings,
            key=MEDIA_LIBRARY_REFERENCE_KEY,
            value=configured_value,
            connection=connection,
        )
        shared_source_id = ensure_shared_local_library_source(
            settings,
            connection=connection,
        )
        purge_shared_local_media_items(
            connection,
            shared_source_id=shared_source_id,
        )
        if setting_changed:
            bump_library_revision_layers(
                settings,
                connection,
                global_layers=("catalog",),
            )
        connection.commit()

    scan_media_library(settings, reason="library_reference_locations_update")
    return get_media_library_reference_payload(settings)


def browse_local_directories(
    settings: Settings,
    *,
    path: str | None,
) -> dict[str, object]:
    browse_dir = _resolve_browse_directory(settings, value=path)
    parent_path = None
    if browse_dir.parent != browse_dir and not is_restricted_settings_browse_path(settings, browse_dir.parent):
        parent_path = str(browse_dir.parent)
    directories: list[dict[str, str]] = []
    try:
        entries = sorted(browse_dir.iterdir(), key=lambda entry: entry.name.lower())
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not read that directory on the Elvern host.",
        ) from exc
    for entry in entries:
        try:
            if not entry.is_dir():
                continue
            resolved_entry = entry.resolve()
        except OSError:
            continue
        if is_restricted_settings_browse_path(settings, resolved_entry):
            continue
        directories.append(
            {
                "name": entry.name or str(entry),
                "path": str(resolved_entry),
            }
        )
    return {
        "current_path": str(browse_dir),
        "parent_path": parent_path,
        "directories": directories,
    }


def pick_local_directory(
    settings: Settings,
    *,
    path: str | None,
    purpose: str | None,
) -> str | None:
    browse_dir = _resolve_browse_directory(settings, value=path)
    selected_path = _run_native_directory_picker(
        start_directory=browse_dir,
        purpose=purpose,
    )
    if not selected_path:
        return None
    return _normalize_existing_local_directory(settings, selected_path)


def poster_reference_location_validation_rules(settings: Settings) -> list[str]:
    return [
        f"Leave blank to use the default Linux poster directory: {poster_reference_default(settings)['default_value']}",
        "Accepted: absolute Linux directory paths such as /srv/media/Posters",
        "Accepted: file:// URIs that resolve to an absolute local directory, such as file:///srv/media/Posters",
        "Rejected: relative paths, Windows paths, UNC/network authorities, and http/https URLs",
    ]


def poster_reference_default(settings: Settings) -> dict[str, str]:
    default_path = _poster_reference_default_path(settings)
    return {
        "default_value": str(default_path),
        "effective_value": str(default_path),
    }


def _normalize_local_poster_directory(value: str, *, settings: Settings) -> str:
    return validate_safe_poster_reference_path(settings, value=value)


def validate_poster_reference_location(
    settings: Settings,
    *,
    value: str | None,
) -> str | None:
    trimmed = (value or "").strip()
    if not trimmed:
        return None
    return _normalize_local_poster_directory(trimmed, settings=settings)


def get_poster_reference_location_payload(
    settings: Settings,
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, object]:
    default_payload = poster_reference_default(settings)
    configured_value = get_global_app_setting(
        settings,
        key=POSTER_REFERENCE_LOCATION_KEY,
        connection=connection,
    )
    effective_value = default_payload["effective_value"]
    if configured_value:
        try:
            effective_value = _normalize_local_poster_directory(configured_value, settings=settings)
        except HTTPException as exc:
            logger.warning(
                "Skipping unsafe stored poster reference location %s: %s",
                configured_value,
                exc.detail,
            )
            effective_value = default_payload["effective_value"]
    return {
        "configured_value": configured_value,
        "effective_value": effective_value,
        "default_value": default_payload["default_value"],
        "validation_rules": poster_reference_location_validation_rules(settings),
    }


def update_poster_reference_location(settings: Settings, *, value: str | None) -> dict[str, object]:
    normalized_value = validate_poster_reference_location(settings, value=value)
    with get_connection(settings) as connection:
        changed = set_global_app_setting(
            settings,
            key=POSTER_REFERENCE_LOCATION_KEY,
            value=normalized_value,
            connection=connection,
        )
        if changed:
            bump_library_revision_layers(
                settings,
                connection,
                global_layers=("catalog",),
            )
        connection.commit()
    if changed:
        invalidate_poster_indexes()
    return get_poster_reference_location_payload(settings)


def _parse_local_directory_candidate(settings: Settings, candidate: str) -> Path:
    return validate_safe_directory_browse_path(settings, candidate)


def _resolve_browse_directory(settings: Settings, *, value: str | None) -> Path:
    candidate = str(value or "").strip()
    if candidate:
        return _parse_local_directory_candidate(settings, candidate)
    else:
        return validate_safe_directory_browse_path(settings, None)


def _normalize_existing_local_directory(settings: Settings, value: str | Path) -> str:
    return validate_safe_existing_local_directory(settings, value)


def get_native_local_directory_picker_capability() -> dict[str, object]:
    from .desktop_playback_service import build_linux_gui_launch_environment

    launch_env, env_summary, env_diagnostics = build_linux_gui_launch_environment()
    display_available = bool(launch_env.get("DISPLAY"))
    wayland_available = bool(launch_env.get("WAYLAND_DISPLAY"))
    dbus_session_available = bool(launch_env.get("DBUS_SESSION_BUS_ADDRESS"))
    gui_session_available = display_available or wayland_available
    backend = _native_directory_picker_backend()

    if not gui_session_available:
        return {
            "native_picker_supported": False,
            "picker_backend": None,
            "gui_session_available": False,
            "display_available": display_available,
            "wayland_available": wayland_available,
            "dbus_session_available": dbus_session_available,
            "missing_dependency": "gui_session",
            "reason": "Elvern could not resolve an active Linux graphical session that the backend can use for the host directory picker.",
            "env_summary": env_summary,
            "env_diagnostics": env_diagnostics,
        }

    if backend:
        return {
            "native_picker_supported": True,
            "picker_backend": backend,
            "gui_session_available": gui_session_available,
            "display_available": display_available,
            "wayland_available": wayland_available,
            "dbus_session_available": dbus_session_available,
            "missing_dependency": None,
            "reason": None,
            "env_summary": env_summary,
            "env_diagnostics": env_diagnostics,
        }

    return {
        "native_picker_supported": False,
        "picker_backend": None,
        "gui_session_available": gui_session_available,
        "display_available": display_available,
        "wayland_available": wayland_available,
        "dbus_session_available": dbus_session_available,
        "missing_dependency": "native_picker_backend",
        "reason": "No supported host directory picker is installed. Install zenity, qarma, or kdialog on the Elvern host.",
        "env_summary": env_summary,
        "env_diagnostics": env_diagnostics,
    }


def _trusted_directory_picker_title(purpose: str | None) -> str:
    return DIRECTORY_PICKER_TITLES.get(str(purpose or "").strip(), DIRECTORY_PICKER_TITLES["generic"])


def _native_picker_executable(name: str) -> str | None:
    executable = shutil.which(name)
    if not executable:
        return None
    return str(Path(executable).resolve())


def _native_directory_picker_backend() -> str | None:
    if _native_picker_executable("zenity"):
        return "zenity"
    if _native_picker_executable("qarma"):
        return "qarma"
    if _native_picker_executable("kdialog"):
        return "kdialog"
    return None


def _native_directory_picker_command_candidates(start_directory: Path) -> list[NativeDirectoryPickerCommand]:
    normalized_start = str(start_directory.resolve())
    start_with_trailing_slash = normalized_start if normalized_start.endswith("/") else f"{normalized_start}/"
    command_candidates: list[NativeDirectoryPickerCommand] = []
    zenity_path = _native_picker_executable("zenity")
    if zenity_path:
        command_candidates.append(
            NativeDirectoryPickerCommand(
                backend="zenity",
                argv=[
                    zenity_path,
                    "--file-selection",
                    "--directory",
                    f"--filename={start_with_trailing_slash}",
                ],
            )
        )
    qarma_path = _native_picker_executable("qarma")
    if qarma_path:
        command_candidates.append(
            NativeDirectoryPickerCommand(
                backend="qarma",
                argv=[
                    qarma_path,
                    "--file-selection",
                    "--directory",
                    f"--filename={start_with_trailing_slash}",
                ],
            )
        )
    kdialog_path = _native_picker_executable("kdialog")
    if kdialog_path:
        command_candidates.append(
            NativeDirectoryPickerCommand(
                backend="kdialog",
                argv=[
                    kdialog_path,
                    "--getexistingdirectory",
                    normalized_start,
                ],
            )
        )
    return command_candidates


def _run_native_directory_picker(*, start_directory: Path, purpose: str | None) -> str | None:
    from .desktop_playback_service import build_linux_gui_launch_environment

    capability = get_native_local_directory_picker_capability()
    if not capability["native_picker_supported"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(capability["reason"] or DIRECTORY_PICKER_FAILURE_MESSAGE),
        )
    launch_env, _env_summary, _env_diagnostics = build_linux_gui_launch_environment()
    title = _trusted_directory_picker_title(purpose)

    command_candidates: list[NativeDirectoryPickerCommand] = []
    for command in _native_directory_picker_command_candidates(start_directory):
        backend_name = command.backend
        if backend_name in {"zenity", "qarma"}:
            command_candidates.append(
                NativeDirectoryPickerCommand(
                    backend=command.backend,
                    argv=[*command.argv, f"--title={title}"],
                )
            )
        elif backend_name == "kdialog":
            command_candidates.append(
                NativeDirectoryPickerCommand(
                    backend=command.backend,
                    argv=[*command.argv, "--title", title],
                )
            )
        else:
            command_candidates.append(command)

    last_error_detail = DIRECTORY_PICKER_FAILURE_MESSAGE
    for command in command_candidates:
        try:
            completed = subprocess.run(
                command.argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=300,
                env=launch_env,
            )
        except (OSError, subprocess.SubprocessError):
            continue

        if completed.returncode == 0:
            selected_path = str(completed.stdout or "").strip()
            return selected_path or None
        if completed.returncode in {1, 130}:
            return None
        stderr = str(completed.stderr or "").strip()
        if stderr:
            logger.debug(
                "Native directory picker backend %s exited with code %s and stderr length %s.",
                command.backend,
                completed.returncode,
                len(stderr),
            )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=last_error_detail,
    )


def try_pick_local_directory(
    settings: Settings,
    *,
    path: str | None,
    purpose: str | None,
) -> dict[str, object]:
    capability = get_native_local_directory_picker_capability()
    picker_backend = str(capability.get("picker_backend") or "") or None
    if not capability["native_picker_supported"]:
        return {
            "status": "unavailable",
            "selected_path": None,
            "reason": capability.get("reason"),
            "picker_backend": picker_backend,
        }
    try:
        selected_path = pick_local_directory(
            settings,
            path=path,
            purpose=purpose,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else DIRECTORY_PICKER_FAILURE_MESSAGE
        return {
            "status": "unavailable",
            "selected_path": None,
            "reason": detail,
            "picker_backend": picker_backend,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "selected_path": None,
            "reason": str(exc) or "Failed to open the host directory picker.",
            "picker_backend": picker_backend,
        }
    if not selected_path:
        return {
            "status": "cancelled",
            "selected_path": None,
            "reason": None,
            "picker_backend": picker_backend,
        }
    return {
        "status": "selected",
        "selected_path": selected_path,
        "reason": None,
        "picker_backend": picker_backend,
    }
