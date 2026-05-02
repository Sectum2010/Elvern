from __future__ import annotations

import json
import mimetypes
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from fastapi import HTTPException, status
from fastapi.responses import StreamingResponse

from ..config import Settings
from .app_settings_service import (
    get_effective_google_oauth_client_id,
    get_effective_google_oauth_client_secret,
    get_effective_google_drive_https_origin,
    google_drive_callback_url,
)


GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_DRIVE_FILES_ENDPOINT = "https://www.googleapis.com/drive/v3/files"
GOOGLE_DRIVE_DRIVES_ENDPOINT = "https://www.googleapis.com/drive/v3/drives"
GOOGLE_DRIVE_SCOPES = (
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/drive.readonly",
)
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
PROVIDER_AUTH_REQUIRED_CODE = "provider_auth_required"
PROVIDER_QUOTA_EXCEEDED_CODE = "provider_quota_exceeded"
PROVIDER_SOURCE_ERROR_CODE = "provider_source_error"
VALIDATED_UPSTREAM_STREAM_DEFAULT_CHUNK_SIZE = 64 * 1024


def google_drive_enabled(settings: Settings) -> bool:
    return bool(
        get_effective_google_drive_https_origin(settings)
        and
        get_effective_google_oauth_client_id(settings)
        and get_effective_google_oauth_client_secret(settings)
    )


def require_google_drive_enabled(settings: Settings) -> None:
    if google_drive_enabled(settings):
        return
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Google Drive setup is incomplete. Add a secure HTTPS app origin, Client ID, and Client Secret in Settings.",
    )


def google_drive_redirect_uri(settings: Settings) -> str:
    return google_drive_callback_url(settings)


def build_google_drive_authorization_url(settings: Settings, *, state_token: str) -> str:
    require_google_drive_enabled(settings)
    client_id = get_effective_google_oauth_client_id(settings) or ""
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": google_drive_redirect_uri(settings),
            "response_type": "code",
            "scope": " ".join(GOOGLE_DRIVE_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state_token,
            "include_granted_scopes": "true",
        }
    )
    return f"{GOOGLE_AUTH_ENDPOINT}?{query}"


def exchange_google_oauth_code(settings: Settings, *, code: str) -> dict[str, object]:
    require_google_drive_enabled(settings)
    client_id = get_effective_google_oauth_client_id(settings) or ""
    client_secret = get_effective_google_oauth_client_secret(settings) or ""
    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": google_drive_redirect_uri(settings),
        "grant_type": "authorization_code",
    }
    return _post_form_json(GOOGLE_TOKEN_ENDPOINT, payload)


def refresh_google_access_token(settings: Settings, *, refresh_token: str) -> dict[str, object]:
    require_google_drive_enabled(settings)
    client_id = get_effective_google_oauth_client_id(settings) or ""
    client_secret = get_effective_google_oauth_client_secret(settings) or ""
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    return _post_form_json(GOOGLE_TOKEN_ENDPOINT, payload)


def build_google_drive_provider_auth_required_detail(
    *,
    reason: str = "reauth_required",
    title: str | None = None,
    message: str | None = None,
    allow_reconnect: bool = True,
    requires_admin: bool = False,
) -> dict[str, object]:
    return {
        "code": PROVIDER_AUTH_REQUIRED_CODE,
        "provider": "google_drive",
        "provider_reason": reason,
        "title": title or "Google Drive connection expired",
        "message": message or "Reconnect Google Drive to continue this action.",
        "reauth_required": True,
        "allow_reconnect": bool(allow_reconnect),
        "requires_admin": bool(requires_admin),
    }


def _raise_google_drive_provider_auth_required(
    *,
    reason: str = "reauth_required",
    title: str | None = None,
    message: str | None = None,
    allow_reconnect: bool = True,
    requires_admin: bool = False,
) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=build_google_drive_provider_auth_required_detail(
            reason=reason,
            title=title,
            message=message,
            allow_reconnect=allow_reconnect,
            requires_admin=requires_admin,
        ),
    )


def _google_provider_auth_reason_from_error(
    *,
    error_code: object | None = None,
    error_description: object | None = None,
) -> str | None:
    normalized_code = str(error_code or "").strip().lower()
    normalized_description = str(error_description or "").strip().lower()
    combined = " ".join(part for part in (normalized_code, normalized_description) if part).strip()
    if not combined:
        return None
    if "invalid_grant" in combined or "invalid_token" in combined:
        return "token_expired_or_revoked"
    if "unauthenticated" in combined or "invalid credentials" in combined or "autherror" in combined:
        return "token_expired_or_revoked"
    if "expired" in combined:
        return "token_expired_or_revoked"
    if "revoked" in combined:
        return "token_expired_or_revoked"
    if "reauth" in combined or "re-auth" in combined:
        return "reauth_required"
    return None


def _parse_google_drive_http_error(
    exc: HTTPError,
    *,
    default_detail: str,
) -> tuple[str, str | None, str | None]:
    detail = default_detail
    provider_auth_reason = None
    provider_source_reason = None
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            detail = str(error.get("message") or detail)
            provider_auth_reason = _google_provider_auth_reason_from_error(
                error_code=error.get("status") or error.get("code"),
                error_description=error.get("message"),
            )
            errors = error.get("errors")
            if isinstance(errors, list):
                for entry in errors:
                    if not isinstance(entry, dict):
                        continue
                    reason = str(entry.get("reason") or "").strip()
                    if reason:
                        if not provider_auth_reason:
                            provider_auth_reason = _google_provider_auth_reason_from_error(
                                error_code=reason,
                                error_description=detail,
                            )
                        if provider_auth_reason:
                            break
                        provider_source_reason = reason
                        break
        elif isinstance(error, str):
            detail = error
            provider_auth_reason = _google_provider_auth_reason_from_error(
                error_code=error,
                error_description=payload.get("error_description") if isinstance(payload, dict) else None,
            )
    except Exception:
        pass
    return detail, provider_auth_reason, provider_source_reason


def _google_drive_provider_source_code(provider_source_reason: str | None) -> str:
    normalized = str(provider_source_reason or "").strip().lower()
    if normalized == "downloadquotaexceeded":
        return PROVIDER_QUOTA_EXCEEDED_CODE
    return PROVIDER_SOURCE_ERROR_CODE


def build_google_drive_provider_source_error_detail(
    *,
    message: str,
    reason_code: str | None = None,
) -> dict[str, object]:
    return {
        "code": _google_drive_provider_source_code(reason_code),
        "provider": "google_drive",
        "reason_code": reason_code or "provider_source_error",
        "message": message,
    }


def _stream_error_headers(
    *,
    detail: str,
    provider_auth_reason: str | None = None,
    provider_source_reason: str | None = None,
) -> dict[str, str]:
    headers = {
        "X-Elvern-Stream-Error-Detail": detail,
    }
    if provider_auth_reason:
        headers["X-Elvern-Provider-Reason"] = provider_auth_reason
    if provider_source_reason:
        headers["X-Elvern-Provider-Source-Reason"] = provider_source_reason
        headers["X-Elvern-Provider-Error-Code"] = _google_drive_provider_source_code(provider_source_reason)
    return headers


def get_google_token_expiry_iso(expires_in: object) -> str | None:
    try:
        seconds = max(int(float(expires_in or 0)), 0)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    expiry = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return expiry.isoformat()


def fetch_google_userinfo(access_token: str) -> dict[str, object]:
    return _get_json(
        GOOGLE_USERINFO_ENDPOINT,
        headers={"Authorization": f"Bearer {access_token}"},
    )


def fetch_drive_resource_metadata(
    access_token: str,
    *,
    resource_type: str,
    resource_id: str,
) -> dict[str, object]:
    if resource_type == "folder":
        payload = _get_json(
            f"{GOOGLE_DRIVE_FILES_ENDPOINT}/{resource_id}",
            query={
                "fields": "id,name,mimeType",
                "supportsAllDrives": "true",
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if str(payload.get("mimeType")) != FOLDER_MIME_TYPE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This Google Drive resource is not a folder.",
            )
        return {
            "resource_type": "folder",
            "resource_id": str(payload["id"]),
            "display_name": str(payload.get("name") or payload["id"]),
        }

    if resource_type == "shared_drive":
        payload = _get_json(
            f"{GOOGLE_DRIVE_DRIVES_ENDPOINT}/{resource_id}",
            query={"fields": "id,name"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        return {
            "resource_type": "shared_drive",
            "resource_id": str(payload["id"]),
            "display_name": str(payload.get("name") or payload["id"]),
        }

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported Google Drive source type.")


def fetch_drive_file_resource_key(access_token: str, *, file_id: str) -> str | None:
    payload = _get_json(
        f"{GOOGLE_DRIVE_FILES_ENDPOINT}/{file_id}",
        query={
            "fields": "id,resourceKey",
            "supportsAllDrives": "true",
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return str(payload.get("resourceKey") or "").strip() or None


def fetch_drive_file_metadata(
    access_token: str,
    *,
    file_id: str,
    resource_key: str | None = None,
) -> dict[str, object]:
    query = {
        "fields": (
            "id,name,mimeType,size,modifiedTime,resourceKey,"
            "videoMediaMetadata(durationMillis,width,height)"
        ),
        "supportsAllDrives": "true",
    }
    if resource_key:
        query["resourceKey"] = resource_key
    return _get_json(
        f"{GOOGLE_DRIVE_FILES_ENDPOINT}/{file_id}",
        query=query,
        headers={"Authorization": f"Bearer {access_token}"},
    )


def list_drive_media_files(
    access_token: str,
    *,
    resource_type: str,
    resource_id: str,
    allowed_video_extensions: tuple[str, ...],
) -> list[dict[str, object]]:
    if resource_type == "folder":
        return _list_folder_media_files(
            access_token,
            folder_id=resource_id,
            allowed_video_extensions=allowed_video_extensions,
        )
    if resource_type == "shared_drive":
        return _list_shared_drive_media_files(
            access_token,
            drive_id=resource_id,
            allowed_video_extensions=allowed_video_extensions,
        )
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported Google Drive source type.")


def proxy_google_drive_file_response(
    access_token: str,
    *,
    file_id: str,
    filename: str,
    resource_key: str | None,
    range_header: str | None,
    chunk_size: int = 1024 * 1024,
    validated_chunk_size: int | None = None,
    stream_validator: Callable[[], bool] | None = None,
) -> StreamingResponse:
    headers = {"Authorization": f"Bearer {access_token}"}
    requested_range_header = str(range_header or "").strip()
    fallback_range_header = None
    if requested_range_header:
        headers["Range"] = requested_range_header
    else:
        # Google Drive often rejects unbounded alt=media opens for large videos
        # as download-quota failures. Keep cloud playback probes bounded and let
        # media clients continue with explicit Range requests.
        fallback_size = max(1, int(validated_chunk_size or chunk_size or VALIDATED_UPSTREAM_STREAM_DEFAULT_CHUNK_SIZE))
        fallback_range_header = f"bytes=0-{fallback_size - 1}"
        headers["Range"] = fallback_range_header
    query = {
        "alt": "media",
        "supportsAllDrives": "true",
    }
    if resource_key:
        query["resourceKey"] = resource_key
    request = Request(
        f"{GOOGLE_DRIVE_FILES_ENDPOINT}/{file_id}?{urlencode(query)}",
        headers=headers,
    )
    try:
        upstream = urlopen(request, timeout=30)
    except HTTPError as exc:
        detail, provider_auth_reason, provider_source_reason = _parse_google_drive_http_error(
            exc,
            default_detail="Cloud media file could not be streamed.",
        )
        if exc.code == 401 or provider_auth_reason:
            _raise_google_drive_provider_auth_required(
                reason=provider_auth_reason or "reauth_required",
                message="Reconnect Google Drive to continue this action.",
            )
        if exc.code == 404 and detail == "Cloud media file could not be streamed.":
            detail = "Cloud media file not found."
        raise HTTPException(
            status_code=exc.code,
            detail=(
                build_google_drive_provider_source_error_detail(
                    message=detail,
                    reason_code=provider_source_reason,
                )
                if provider_source_reason
                else detail
            ),
            headers=_stream_error_headers(
                detail=detail,
                provider_auth_reason=provider_auth_reason,
                provider_source_reason=provider_source_reason,
            ),
        ) from exc
    except URLError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Google Drive could not be reached.",
        ) from exc

    upstream_headers = getattr(upstream, "headers", {})
    response_headers = {
        "Accept-Ranges": upstream_headers.get("Accept-Ranges", "bytes"),
        "Cache-Control": "private, max-age=0, must-revalidate",
    }
    if fallback_range_header:
        response_headers["X-Elvern-Cloud-Range-Fallback"] = fallback_range_header
    for header_name in ("Content-Length", "Content-Range"):
        header_value = upstream_headers.get(header_name)
        if header_value:
            response_headers[header_name] = header_value
    media_type = upstream_headers.get("Content-Type") or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    status_code = getattr(upstream, "status", status.HTTP_200_OK)
    return StreamingResponse(
        _iter_upstream_response(
            upstream,
            chunk_size=chunk_size,
            validated_chunk_size=validated_chunk_size,
            stream_validator=stream_validator,
        ),
        media_type=media_type,
        headers=response_headers,
        status_code=status_code,
    )


def build_cloud_virtual_path(*, resource_id: str, file_id: str, filename: str) -> str:
    safe_name = PurePosixPath(filename or file_id).name or file_id
    return f"gdrive://{resource_id}/{file_id}/{safe_name}"


def _iter_upstream_response(
    upstream,
    *,
    chunk_size: int = 1024 * 1024,
    validated_chunk_size: int | None = None,
    stream_validator: Callable[[], bool] | None = None,
) -> Iterator[bytes]:
    effective_chunk_size = resolve_effective_upstream_chunk_size(
        chunk_size=chunk_size,
        stream_validator=stream_validator,
        validated_chunk_size=validated_chunk_size,
    )
    with upstream:
        while True:
            if stream_validator and not stream_validator():
                break
            chunk = upstream.read(effective_chunk_size)
            if not chunk:
                break
            if stream_validator and not stream_validator():
                break
            yield chunk


def resolve_effective_upstream_chunk_size(
    *,
    chunk_size: int,
    stream_validator: Callable[[], bool] | None = None,
    validated_chunk_size: int | None = None,
) -> int:
    if stream_validator is None:
        return chunk_size
    if validated_chunk_size is not None and validated_chunk_size > 0:
        return validated_chunk_size
    return VALIDATED_UPSTREAM_STREAM_DEFAULT_CHUNK_SIZE


def _list_folder_media_files(
    access_token: str,
    *,
    folder_id: str,
    allowed_video_extensions: tuple[str, ...],
) -> list[dict[str, object]]:
    discovered: list[dict[str, object]] = []
    queue = [{
        "id": folder_id,
        "name": "",
        "is_root": True,
    }]
    while queue:
        current_folder = queue.pop(0)
        current_folder_id = str(current_folder["id"])
        current_folder_name = str(current_folder.get("name") or "").strip()
        current_is_root = bool(current_folder.get("is_root"))
        page_token: str | None = None
        while True:
            payload = _get_json(
                GOOGLE_DRIVE_FILES_ENDPOINT,
                query={
                    "q": f"'{current_folder_id}' in parents and trashed = false",
                    "fields": (
                        "nextPageToken,"
                        "files(id,name,mimeType,size,modifiedTime,resourceKey,"
                        "videoMediaMetadata(durationMillis,width,height))"
                    ),
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                    "pageSize": "1000",
                    **({"pageToken": page_token} if page_token else {}),
                },
                headers={"Authorization": f"Bearer {access_token}"},
            )
            for row in payload.get("files", []):
                mime_type = str(row.get("mimeType") or "")
                if mime_type == FOLDER_MIME_TYPE:
                    queue.append(
                        {
                            "id": str(row["id"]),
                            "name": str(row.get("name") or row["id"]),
                            "is_root": False,
                        }
                    )
                    continue
                if _is_video_candidate(row, allowed_video_extensions=allowed_video_extensions):
                    payload_row = dict(row)
                    if not current_is_root and current_folder_name:
                        payload_row["seriesFolderKey"] = current_folder_id
                        payload_row["seriesFolderName"] = current_folder_name
                    discovered.append(payload_row)
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
    return discovered


def _list_shared_drive_media_files(
    access_token: str,
    *,
    drive_id: str,
    allowed_video_extensions: tuple[str, ...],
) -> list[dict[str, object]]:
    discovered: list[dict[str, object]] = []
    page_token: str | None = None
    while True:
        payload = _get_json(
            GOOGLE_DRIVE_FILES_ENDPOINT,
            query={
                "corpora": "drive",
                "driveId": drive_id,
                "includeItemsFromAllDrives": "true",
                "supportsAllDrives": "true",
                "q": "trashed = false",
                "fields": (
                    "nextPageToken,"
                    "files(id,name,mimeType,size,modifiedTime,resourceKey,"
                    "videoMediaMetadata(durationMillis,width,height))"
                ),
                "pageSize": "1000",
                **({"pageToken": page_token} if page_token else {}),
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        for row in payload.get("files", []):
            if _is_video_candidate(row, allowed_video_extensions=allowed_video_extensions):
                discovered.append(dict(row))
        page_token = payload.get("nextPageToken")
        if not page_token:
            break
    return discovered


def _is_video_candidate(row: dict[str, object], *, allowed_video_extensions: tuple[str, ...]) -> bool:
    name = str(row.get("name") or "")
    mime_type = str(row.get("mimeType") or "")
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in allowed_video_extensions:
        return True
    return mime_type.startswith("video/")


def _post_form_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    body = urlencode({key: str(value) for key, value in payload.items() if value is not None}).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = "Google authentication request failed."
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            error_code = payload.get("error")
            error_description = payload.get("error_description") or error_code
            provider_auth_reason = _google_provider_auth_reason_from_error(
                error_code=error_code,
                error_description=error_description,
            )
            if provider_auth_reason:
                _raise_google_drive_provider_auth_required(
                    reason=provider_auth_reason,
                    message="Reconnect Google Drive to continue this action.",
                )
            if error_description:
                detail = f"Google authentication failed: {error_description}"
        except HTTPException:
            raise
        except Exception:
            pass
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from exc
    except URLError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Google authentication service could not be reached.",
        ) from exc


def _get_json(
    url: str,
    *,
    query: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    resolved_url = url
    if query:
        resolved_url = f"{url}?{urlencode(query)}"
    request = Request(resolved_url, headers=headers or {})
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail, provider_auth_reason, _provider_source_reason = _parse_google_drive_http_error(
            exc,
            default_detail="Google Drive request failed.",
        )
        if exc.code == 404:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from exc
        if exc.code == 401 or provider_auth_reason:
            _raise_google_drive_provider_auth_required(
                reason=provider_auth_reason or "reauth_required",
                message="Reconnect Google Drive to continue this action.",
            )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from exc
    except URLError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Google Drive could not be reached.",
        ) from exc
