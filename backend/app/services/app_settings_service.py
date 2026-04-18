from __future__ import annotations

import sqlite3
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException, status

from ..config import Settings
from ..db import get_connection, utcnow_iso


POSTER_REFERENCE_LOCATION_KEY = "poster_reference_location"
GOOGLE_OAUTH_CLIENT_ID_KEY = "google_oauth_client_id"
GOOGLE_OAUTH_CLIENT_SECRET_KEY = "google_oauth_client_secret"
GOOGLE_DRIVE_HTTPS_ORIGIN_KEY = "google_drive_https_origin"


def _poster_reference_default_path(settings: Settings) -> Path:
    return (settings.media_root / "Posters").resolve()


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
