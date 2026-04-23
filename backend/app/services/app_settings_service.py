from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException, status

from ..config import Settings
from ..db import get_connection, utcnow_iso
from ..media_scan import scan_media_library
from .local_library_source_service import (
    get_effective_shared_local_library_path,
    ensure_shared_local_library_source,
    purge_shared_local_media_items,
    shared_local_library_bootstrap_path,
    update_shared_local_library_path,
)


MEDIA_LIBRARY_REFERENCE_KEY = "media_library_reference"
POSTER_REFERENCE_LOCATION_KEY = "poster_reference_location"
GOOGLE_OAUTH_CLIENT_ID_KEY = "google_oauth_client_id"
GOOGLE_OAUTH_CLIENT_SECRET_KEY = "google_oauth_client_secret"
GOOGLE_DRIVE_HTTPS_ORIGIN_KEY = "google_drive_https_origin"


def _poster_reference_default_path(settings: Settings) -> Path:
    return (get_effective_shared_local_library_path(settings) / "Posters").resolve()


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


def set_global_app_setting(settings: Settings, *, key: str, value: str | None) -> None:
    with get_connection(settings) as connection:
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
        connection.commit()


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
        f"Leave blank to reset to the bootstrap shared local path: {normalize_media_library_reference_default_path(settings=settings)['default_value']}",
        "This is the real shared local library path currently used by Elvern for the shared local library.",
        "Use an absolute Linux directory path that already exists on this host.",
    ]


def normalize_media_library_reference_default_path(*, settings: Settings | None = None) -> dict[str, str]:
    if settings is None:
        raise ValueError("settings is required")
    default_path = str(shared_local_library_bootstrap_path(settings))
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
    effective_value = str(
        get_effective_shared_local_library_path(settings, connection=connection)
    )
    return {
        "configured_value": effective_value,
        "effective_value": effective_value,
        "default_value": default_payload["default_value"],
        "validation_rules": media_library_reference_validation_rules(settings),
    }


def update_media_library_reference(settings: Settings, *, value: str | None) -> dict[str, object]:
    with get_connection(settings) as connection:
        update_shared_local_library_path(
            settings,
            value=value,
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
        connection.commit()

    scan_media_library(settings, reason="shared_local_path_update")
    set_global_app_setting(settings, key=MEDIA_LIBRARY_REFERENCE_KEY, value=None)
    return get_media_library_reference_payload(settings)


def browse_local_directories(
    settings: Settings,
    *,
    path: str | None,
) -> dict[str, object]:
    browse_dir = _resolve_browse_directory(settings, value=path)
    parent_path = None if browse_dir.parent == browse_dir else str(browse_dir.parent)
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
        except OSError:
            continue
        directories.append(
            {
                "name": entry.name or str(entry),
                "path": str(entry.resolve()),
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
    title: str | None,
) -> str | None:
    browse_dir = _resolve_browse_directory(settings, value=path)
    selected_path = _run_native_directory_picker(
        start_directory=browse_dir,
        title=str(title or "").strip() or "Select directory",
    )
    if not selected_path:
        return None
    return _normalize_existing_local_directory(selected_path)


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
    candidate = value.strip()
    if not candidate:
        raise ValueError("empty")

    parsed = urlsplit(candidate)
    if parsed.scheme:
        if parsed.scheme.lower() != "file":
            raise ValueError("unsupported_uri_scheme")
        if parsed.netloc and parsed.netloc.lower() not in {"", "localhost"}:
            raise ValueError("unsupported_file_authority")
        candidate_path = Path(unquote(parsed.path or "")).expanduser()
    else:
        candidate_path = Path(candidate).expanduser()

    if not candidate_path.is_absolute():
        raise ValueError("relative_path")

    normalized_path = candidate_path.resolve()
    if not normalized_path.exists():
        raise ValueError("missing_path")
    if not normalized_path.is_dir():
        raise ValueError("not_directory")
    return str(normalized_path)


def validate_poster_reference_location(
    settings: Settings,
    *,
    value: str | None,
) -> str | None:
    trimmed = (value or "").strip()
    if not trimmed:
        return None
    try:
        return _normalize_local_poster_directory(trimmed, settings=settings)
    except ValueError as exc:
        code = str(exc)
        detail = {
            "unsupported_uri_scheme": "Poster reference location must be a local Linux path or file:// URI.",
            "unsupported_file_authority": "Poster reference location does not support remote file:// authorities. Mount the directory locally and use an absolute Linux path instead.",
            "relative_path": "Poster reference location must be an absolute Linux path or file:// URI.",
            "missing_path": "Poster reference location must point to an existing directory.",
            "not_directory": "Poster reference location must point to a directory.",
            "empty": "Poster reference location cannot be empty.",
        }.get(code, "Poster reference location is invalid.")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc


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
        except ValueError:
            effective_value = default_payload["effective_value"]
    return {
        "configured_value": configured_value,
        "effective_value": effective_value,
        "default_value": default_payload["default_value"],
        "validation_rules": poster_reference_location_validation_rules(settings),
    }


def update_poster_reference_location(settings: Settings, *, value: str | None) -> dict[str, object]:
    normalized_value = validate_poster_reference_location(settings, value=value)
    set_global_app_setting(
        settings,
        key=POSTER_REFERENCE_LOCATION_KEY,
        value=normalized_value,
    )
    return get_poster_reference_location_payload(settings)


def _parse_local_directory_candidate(candidate: str) -> Path:
    parsed = urlsplit(candidate)
    if parsed.scheme:
        if parsed.scheme.lower() != "file":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Directory browse only supports local Linux paths or file:// URIs.",
            )
        if parsed.netloc and parsed.netloc.lower() not in {"", "localhost"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Directory browse only supports local directories on the Elvern host.",
            )
        return Path(unquote(parsed.path or "")).expanduser()
    return Path(candidate).expanduser()


def _resolve_browse_directory(settings: Settings, *, value: str | None) -> Path:
    candidate = str(value or "").strip()
    if candidate:
        browse_path = _parse_local_directory_candidate(candidate)
        if not browse_path.is_absolute():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Directory browse needs an absolute Linux path or file:// URI.",
            )
        resolved = browse_path.resolve(strict=False)
    else:
        resolved = get_effective_shared_local_library_path(settings).resolve()

    current = resolved
    while True:
        if current.exists():
            browse_dir = current if current.is_dir() else current.parent
            break
        if current.parent == current:
            browse_dir = Path("/").resolve()
            break
        current = current.parent

    if not browse_dir.exists() or not browse_dir.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Directory browse could not find a readable local directory on this host.",
        )
    if not os.access(browse_dir, os.R_OK | os.X_OK):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Directory browse could not read that directory on the Elvern host.",
        )
    return browse_dir


def _normalize_existing_local_directory(value: str | Path) -> str:
    candidate_path = _parse_local_directory_candidate(str(value))
    if not candidate_path.is_absolute():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The selected host directory was not an absolute Linux path.",
        )
    normalized_path = candidate_path.resolve()
    if not normalized_path.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The selected host directory no longer exists.",
        )
    if not normalized_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The selected host path is not a directory.",
        )
    if not os.access(normalized_path, os.R_OK | os.X_OK):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The selected host directory is not readable.",
        )
    return str(normalized_path)


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


def _native_directory_picker_backend() -> str | None:
    if shutil.which("zenity"):
        return "zenity"
    if shutil.which("qarma"):
        return "qarma"
    if shutil.which("kdialog"):
        return "kdialog"
    return None


def _native_directory_picker_command_candidates(start_directory: Path) -> list[list[str]]:
    normalized_start = str(start_directory.resolve())
    start_with_trailing_slash = normalized_start if normalized_start.endswith("/") else f"{normalized_start}/"
    command_candidates: list[list[str]] = []
    if shutil.which("zenity"):
        command_candidates.append(
            [
                "zenity",
                "--file-selection",
                "--directory",
                f"--filename={start_with_trailing_slash}",
            ]
        )
    if shutil.which("qarma"):
        command_candidates.append(
            [
                "qarma",
                "--file-selection",
                "--directory",
                f"--filename={start_with_trailing_slash}",
            ]
        )
    if shutil.which("kdialog"):
        command_candidates.append(
            [
                "kdialog",
                "--getexistingdirectory",
                normalized_start,
            ]
        )
    return command_candidates


def _run_native_directory_picker(*, start_directory: Path, title: str) -> str | None:
    from .desktop_playback_service import build_linux_gui_launch_environment

    capability = get_native_local_directory_picker_capability()
    if not capability["native_picker_supported"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(capability["reason"] or "Failed to open the host directory picker."),
        )
    launch_env, _env_summary, _env_diagnostics = build_linux_gui_launch_environment()

    command_candidates: list[list[str]] = []
    for command in _native_directory_picker_command_candidates(start_directory):
        if command[0] in {"zenity", "qarma"}:
            command_candidates.append([*command, f"--title={title}"])
        elif command[0] == "kdialog":
            command_candidates.append([*command, "--title", title])
        else:
            command_candidates.append(command)

    last_error_detail = "Failed to open the host directory picker."
    for command in command_candidates:
        try:
            completed = subprocess.run(
                command,
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
            last_error_detail = stderr

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=last_error_detail,
    )


def try_pick_local_directory(
    settings: Settings,
    *,
    path: str | None,
    title: str | None,
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
            title=title,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "Failed to open the host directory picker."
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
