from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import HTTPException, status

from ..config import Settings
from ..db import get_connection, utcnow_iso
from ..security import generate_session_token, hash_token_hmac
from .at_rest_encryption import decrypt_at_rest, encrypt_at_rest
from .app_settings_service import get_effective_google_drive_https_origin
from .google_drive_service import (
    build_google_drive_provider_auth_required_detail,
    build_google_drive_authorization_url,
    exchange_google_oauth_code,
    fetch_google_userinfo,
    get_google_token_expiry_iso,
    refresh_google_access_token,
    require_google_drive_enabled,
)
from .security_event_service import log_security_event


GOOGLE_STATE_TTL_MINUTES = 15
GOOGLE_OAUTH_STATE_TOKEN_HASH_PURPOSE = "google.oauth_state"


def _hash_google_oauth_state_token(settings: Settings, state_token: str) -> str:
    return hash_token_hmac(settings, purpose=GOOGLE_OAUTH_STATE_TOKEN_HASH_PURPOSE, token=state_token)


def _provider_auth_required_for_corrupted_token() -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=build_google_drive_provider_auth_required_detail(
            reason="token_corrupted",
            message="Reconnect Google Drive to continue this action.",
        ),
    )


def _decrypt_stored_oauth_value(
    stored: object,
    settings: Settings,
) -> tuple[str, bool, bool]:
    """
    Return (plaintext, was_encrypted, corrupted).

    Legacy plaintext values are accepted so callers can lazily migrate them.
    Corrupted encrypted values are treated as missing provider auth.
    """
    try:
        plaintext, was_encrypted = decrypt_at_rest(str(stored or ""), settings)
    except ValueError:
        return "", False, True
    return plaintext, was_encrypted, False


def _log_oauth_token_encryption_upgrade(
    settings: Settings,
    *,
    user_id: int | None,
    account_id: int,
    columns: list[str],
) -> None:
    if not columns:
        return
    log_security_event(
        settings,
        event_kind="oauth_token_encryption_upgraded",
        actor_user_id=user_id,
        details={
            "google_drive_account_id": account_id,
            "columns": sorted(columns),
        },
    )


def _normalize_google_connect_return_path(return_path: str | None) -> str | None:
    candidate = str(return_path or "").strip()
    if not candidate:
        return None
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return None
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return None
    return urlunsplit(("", "", parsed.path, parsed.query, ""))


def _encode_google_connect_state_payload(*, state_token: str, return_path: str | None) -> str:
    payload = {"token": state_token}
    normalized_return_path = _normalize_google_connect_return_path(return_path)
    if normalized_return_path:
        payload["return_path"] = normalized_return_path
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"elvern:{encoded}"


def resolve_google_connect_state(state_token: str) -> dict[str, str | None]:
    candidate = str(state_token or "").strip()
    if not candidate.startswith("elvern:"):
        return {
            "state_token": candidate,
            "return_path": None,
        }
    encoded = candidate.split(":", 1)[1]
    padding = "=" * ((4 - (len(encoded) % 4)) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{encoded}{padding}".encode("ascii")).decode("utf-8")
        payload = json.loads(decoded)
    except Exception:  # noqa: BLE001
        return {
            "state_token": candidate,
            "return_path": None,
        }
    token = str(payload.get("token") or "").strip() or candidate
    return_path = _normalize_google_connect_return_path(payload.get("return_path"))
    return {
        "state_token": token,
        "return_path": return_path,
    }


def build_google_drive_connect_response(
    settings: Settings,
    *,
    user_id: int,
    return_path: str | None = None,
) -> dict[str, str]:
    require_google_drive_enabled(settings)
    state_token = generate_session_token()
    state_token_hash = _hash_google_oauth_state_token(settings, state_token)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=GOOGLE_STATE_TTL_MINUTES)
    with get_connection(settings) as connection:
        connection.execute(
            """
            INSERT INTO google_oauth_states (state_token, user_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (state_token_hash, user_id, now.isoformat(), expires_at.isoformat()),
        )
        connection.commit()
    return {
        "authorization_url": build_google_drive_authorization_url(
            settings,
            state_token=_encode_google_connect_state_payload(
                state_token=state_token,
                return_path=return_path,
            ),
        ),
    }


def complete_google_drive_connect(
    settings: Settings,
    *,
    state_token: str,
    code: str,
) -> dict[str, object]:
    require_google_drive_enabled(settings)
    now_iso = utcnow_iso()
    state_context = resolve_google_connect_state(state_token)
    resolved_state_token = str(state_context["state_token"] or "").strip()
    resolved_state_token_hash = _hash_google_oauth_state_token(settings, resolved_state_token)
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT state_token, user_id
            FROM google_oauth_states
            WHERE state_token = ? AND expires_at > ?
            LIMIT 1
            """,
            (resolved_state_token_hash, now_iso),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google Drive sign-in state expired.")
        user_id = int(row["user_id"])

    token_payload = exchange_google_oauth_code(settings, code=code)
    access_token = str(token_payload.get("access_token") or "")
    refresh_token = token_payload.get("refresh_token")
    if not access_token:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Google Drive did not return an access token.")
    userinfo = fetch_google_userinfo(access_token)
    google_account_id = str(userinfo.get("sub") or "")
    if not google_account_id:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Google Drive account details were incomplete.")

    access_token_expires_at = get_google_token_expiry_iso(token_payload.get("expires_in"))
    now = utcnow_iso()
    with get_connection(settings) as connection:
        existing = connection.execute(
            """
            SELECT id, refresh_token
            FROM google_drive_accounts
            WHERE user_id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        upgraded_columns: list[str] = []
        existing_account_id = int(existing["id"]) if existing else None
        if refresh_token:
            stored_refresh_token = str(refresh_token)
        elif existing and existing["refresh_token"]:
            stored_refresh_token, was_encrypted, corrupted = _decrypt_stored_oauth_value(
                existing["refresh_token"],
                settings,
            )
            if corrupted:
                stored_refresh_token = ""
            elif not was_encrypted:
                upgraded_columns.append("refresh_token")
        else:
            stored_refresh_token = ""
        if not stored_refresh_token:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Google Drive did not provide a refresh token. Please try connecting again.",
            )
        encrypted_refresh_token = encrypt_at_rest(stored_refresh_token, settings)
        encrypted_access_token = encrypt_at_rest(access_token, settings)
        connection.execute(
            """
            INSERT INTO google_drive_accounts (
                user_id,
                google_account_id,
                email,
                display_name,
                refresh_token,
                access_token,
                access_token_expires_at,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                google_account_id = excluded.google_account_id,
                email = excluded.email,
                display_name = excluded.display_name,
                refresh_token = excluded.refresh_token,
                access_token = excluded.access_token,
                access_token_expires_at = excluded.access_token_expires_at,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                google_account_id,
                userinfo.get("email"),
                userinfo.get("name") or userinfo.get("email"),
                encrypted_refresh_token,
                encrypted_access_token,
                access_token_expires_at,
                now,
                now,
            ),
        )
        account_row = connection.execute(
            "SELECT id FROM google_drive_accounts WHERE user_id = ? LIMIT 1",
            (user_id,),
        ).fetchone()
        if account_row is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Google Drive account could not be saved.",
            )
        connection.execute(
            """
            UPDATE library_sources
            SET google_drive_account_id = ?, last_error = NULL, updated_at = ?
            WHERE owner_user_id = ?
              AND provider = 'google_drive'
              AND google_drive_account_id IS NULL
            """,
            (int(account_row["id"]), now, user_id),
        )
        connection.execute("DELETE FROM google_oauth_states WHERE state_token = ?", (resolved_state_token_hash,))
        connection.commit()
        if upgraded_columns and existing_account_id is not None:
            _log_oauth_token_encryption_upgrade(
                settings,
                user_id=user_id,
                account_id=existing_account_id,
                columns=upgraded_columns,
            )
    return {
        "user_id": user_id,
        "account_email": userinfo.get("email"),
        "account_name": userinfo.get("name") or userinfo.get("email"),
        "return_path": state_context["return_path"],
    }


def get_google_drive_account_access_token(settings: Settings, *, user_id: int) -> tuple[str, dict[str, object]]:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT id, user_id, refresh_token, access_token, access_token_expires_at
            FROM google_drive_accounts
            WHERE user_id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connect Google Drive before adding a cloud library.",
        )
    row_payload = dict(row)
    access_token, updated_row = _ensure_access_token(settings, row=row_payload)
    return access_token, updated_row


def get_google_drive_account_access_token_by_account_id(
    settings: Settings,
    *,
    google_account_id: int,
) -> str:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT id, user_id, refresh_token, access_token, access_token_expires_at
            FROM google_drive_accounts
            WHERE id = ?
            LIMIT 1
            """,
            (google_account_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Drive account is no longer available.",
        )
    access_token, _ = _ensure_access_token(settings, row=dict(row))
    return access_token


def _ensure_access_token(settings: Settings, *, row: dict[str, object]) -> tuple[str, dict[str, object]]:
    account_id = int(row["id"])
    user_id = int(row["user_id"]) if row.get("user_id") is not None else None
    upgraded_columns: list[str] = []
    access_token, access_was_encrypted, access_corrupted = _decrypt_stored_oauth_value(
        row.get("access_token"),
        settings,
    )
    if access_corrupted:
        access_token = ""
    elif access_token and not access_was_encrypted:
        upgraded_columns.append("access_token")
    access_token_expires_at = str(row.get("access_token_expires_at") or "")
    if access_token and access_token_expires_at:
        try:
            expires_at = datetime.fromisoformat(access_token_expires_at)
        except ValueError:
            expires_at = None
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at > datetime.now(timezone.utc) + timedelta(seconds=30):
                if upgraded_columns:
                    with get_connection(settings) as connection:
                        connection.execute(
                            """
                            UPDATE google_drive_accounts
                            SET access_token = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (encrypt_at_rest(access_token, settings), utcnow_iso(), account_id),
                        )
                        connection.commit()
                    _log_oauth_token_encryption_upgrade(
                        settings,
                        user_id=user_id,
                        account_id=account_id,
                        columns=upgraded_columns,
                    )
                    row["access_token"] = access_token
                return access_token, row

    refresh_token, refresh_was_encrypted, refresh_corrupted = _decrypt_stored_oauth_value(
        row.get("refresh_token"),
        settings,
    )
    if refresh_corrupted or not refresh_token:
        _provider_auth_required_for_corrupted_token()
    if not refresh_was_encrypted:
        upgraded_columns.append("refresh_token")

    refreshed = refresh_google_access_token(settings, refresh_token=refresh_token)
    next_access_token = str(refreshed.get("access_token") or "")
    if not next_access_token:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Google Drive did not return a refreshed access token.",
        )
    access_token_expires_at = get_google_token_expiry_iso(refreshed.get("expires_in"))
    with get_connection(settings) as connection:
        connection.execute(
            """
            UPDATE google_drive_accounts
            SET refresh_token = ?, access_token = ?, access_token_expires_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                encrypt_at_rest(refresh_token, settings),
                encrypt_at_rest(next_access_token, settings),
                access_token_expires_at,
                utcnow_iso(),
                account_id,
            ),
        )
        connection.commit()
    if upgraded_columns:
        _log_oauth_token_encryption_upgrade(
            settings,
            user_id=user_id,
            account_id=account_id,
            columns=upgraded_columns,
        )
    row["access_token"] = next_access_token
    row["access_token_expires_at"] = access_token_expires_at
    return next_access_token, row


def build_google_connect_callback_redirect(
    settings: Settings,
    *,
    success: bool,
    message: str,
    return_path: str | None = None,
) -> str:
    base_origin = (get_effective_google_drive_https_origin(settings) or "").strip().rstrip("/")
    if not base_origin:
        base_origin = (settings.public_app_origin or "").strip().rstrip("/")
    if not base_origin:
        host = settings.frontend_host
        if host in {"0.0.0.0", "::"}:  # nosec B104 - intentional bind for Tailscale/LAN access
            host = "127.0.0.1"
        base_origin = f"http://{host}:{settings.frontend_port}"
    normalized_return_path = _normalize_google_connect_return_path(return_path) or "/settings"
    parsed_return_path = urlsplit(normalized_return_path)
    query_items = [
        item
        for item in parsed_return_path.query.split("&")
        if item and not item.startswith("googleDriveStatus=") and not item.startswith("googleDriveMessage=")
    ]
    query_items.append(
        urlencode(
            {
                "googleDriveStatus": "connected" if success else "error",
                "googleDriveMessage": message,
            }
        )
    )
    merged_query = "&".join(query_items)
    return f"{base_origin}{urlunsplit(('', '', parsed_return_path.path, merged_query, ''))}"
