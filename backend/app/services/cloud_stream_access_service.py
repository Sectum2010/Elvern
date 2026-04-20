from __future__ import annotations

from typing import Callable

from fastapi import HTTPException, status

from ..config import Settings
from ..db import get_connection, utcnow_iso
from .google_drive_service import (
    build_google_drive_provider_auth_required_detail,
    fetch_drive_file_resource_key,
    proxy_google_drive_file_response,
)


def resolve_media_stream_target(
    settings: Settings,
    *,
    user_id: int,
    item_id: int,
    get_access_token_by_account_id: Callable[..., str],
) -> dict[str, object] | None:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT
                m.id,
                m.file_path,
                m.original_filename,
                m.source_kind,
                m.external_media_id,
                m.cloud_resource_key,
                s.id AS library_source_id,
                s.owner_user_id,
                s.is_shared,
                account.id AS google_account_id
            FROM media_items m
            LEFT JOIN library_sources s
              ON s.id = m.library_source_id
            LEFT JOIN google_drive_accounts account
              ON account.id = s.google_drive_account_id
            LEFT JOIN user_hidden_library_sources h
              ON h.library_source_id = s.id
             AND h.user_id = ?
            WHERE m.id = ?
              AND (
                COALESCE(m.source_kind, 'local') = 'local'
                OR (
                    s.id IS NOT NULL
                AND h.id IS NULL
                AND (
                        s.owner_user_id = ?
                     OR s.is_shared = 1
                    )
                )
              )
            LIMIT 1
            """,
            (user_id, item_id, user_id),
        ).fetchone()
        if row is None:
            return None
        if str(row["source_kind"] or "local") == "local":
            return {
                "source_kind": "local",
                "file_path": str(row["file_path"]),
                "original_filename": str(row["original_filename"]),
            }
        google_account_id = int(row["google_account_id"] or 0)
        row_payload = dict(row)
    if not google_account_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cloud source is missing its Google Drive connection.",
        )
    access_token = get_access_token_by_account_id(
        settings,
        google_account_id=google_account_id,
    )
    resource_key = str(row_payload["cloud_resource_key"] or "").strip() or None
    if not resource_key:
        resource_key = fetch_drive_file_resource_key(
            access_token,
            file_id=str(row_payload["external_media_id"]),
        )
        if resource_key:
            with get_connection(settings) as connection:
                connection.execute(
                    """
                    UPDATE media_items
                    SET cloud_resource_key = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (resource_key, utcnow_iso(), item_id),
                )
                connection.commit()
    return {
        "source_kind": "cloud",
        "file_id": str(row_payload["external_media_id"]),
        "original_filename": str(row_payload["original_filename"]),
        "resource_key": resource_key,
        "access_token": access_token,
    }


def build_cloud_stream_response(
    settings: Settings,
    *,
    user_id: int,
    item_id: int,
    range_header: str | None,
    stream_validator,
    get_access_token_by_account_id: Callable[..., str],
):
    target = resolve_media_stream_target(
        settings,
        user_id=user_id,
        item_id=item_id,
        get_access_token_by_account_id=get_access_token_by_account_id,
    )
    if target is None:
        return None
    if target["source_kind"] == "local":
        return target
    return proxy_google_drive_file_response(
        target["access_token"],
        file_id=str(target["file_id"]),
        filename=str(target["original_filename"]),
        resource_key=target.get("resource_key"),
        range_header=range_header,
        stream_validator=stream_validator,
    )


def ensure_cloud_media_item_provider_access(
    settings: Settings,
    *,
    user_id: int,
    item_id: int,
    get_access_token_by_account_id: Callable[..., str],
) -> None:
    provider_context = _load_cloud_media_item_provider_context(
        settings,
        user_id=user_id,
        item_id=item_id,
    )
    if provider_context is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found.")
    if str(provider_context.get("source_kind") or "local") != "cloud":
        return
    try:
        target = resolve_media_stream_target(
            settings,
            user_id=user_id,
            item_id=item_id,
            get_access_token_by_account_id=get_access_token_by_account_id,
        )
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict) and detail.get("code") == "provider_auth_required":
            allow_reconnect = _current_user_can_manage_cloud_provider_connection(
                provider_context,
                user_id=user_id,
            )
            if allow_reconnect:
                raise
            raise HTTPException(
                status_code=exc.status_code,
                detail=build_google_drive_provider_auth_required_detail(
                    reason=str(detail.get("provider_reason") or "reauth_required"),
                    title="Google Drive connection needs administrator attention",
                    message="Ask an administrator to reconnect Google Drive to continue this action.",
                    allow_reconnect=False,
                    requires_admin=True,
                ),
            ) from exc
        raise
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found.")


def _load_cloud_media_item_provider_context(
    settings: Settings,
    *,
    user_id: int,
    item_id: int,
) -> dict[str, object] | None:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT
                m.id,
                COALESCE(m.source_kind, 'local') AS source_kind,
                s.owner_user_id,
                s.is_shared,
                account.id AS google_account_id
            FROM media_items m
            LEFT JOIN library_sources s
              ON s.id = m.library_source_id
            LEFT JOIN google_drive_accounts account
              ON account.id = s.google_drive_account_id
            LEFT JOIN user_hidden_library_sources h
              ON h.library_source_id = s.id
             AND h.user_id = ?
            WHERE m.id = ?
              AND (
                COALESCE(m.source_kind, 'local') = 'local'
                OR (
                    s.id IS NOT NULL
                AND h.id IS NULL
                AND (
                        s.owner_user_id = ?
                     OR s.is_shared = 1
                    )
                )
              )
            LIMIT 1
            """,
            (user_id, item_id, user_id),
        ).fetchone()
    return dict(row) if row is not None else None


def _current_user_can_manage_cloud_provider_connection(
    provider_context: dict[str, object],
    *,
    user_id: int,
) -> bool:
    if str(provider_context.get("source_kind") or "local") != "cloud":
        return False
    owner_user_id = int(provider_context.get("owner_user_id") or 0)
    google_account_id = int(provider_context.get("google_account_id") or 0)
    return owner_user_id > 0 and owner_user_id == user_id and google_account_id > 0
