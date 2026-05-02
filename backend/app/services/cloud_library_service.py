from __future__ import annotations

from typing import Literal

from ..config import Settings
from ..models import AuthenticatedUser
from .cloud_library_source_service import (
    add_google_drive_library_source as _add_google_drive_library_source_impl,
    get_cloud_libraries_payload as _get_cloud_libraries_payload_impl,
    get_google_drive_provider_auth_status_payload as _get_google_drive_provider_auth_status_payload_impl,
    hide_shared_library_source_for_user as _hide_shared_library_source_for_user_impl,
    move_google_drive_library_source as _move_google_drive_library_source_impl,
    show_shared_library_source_for_user as _show_shared_library_source_for_user_impl,
)
from .cloud_provider_auth_service import (
    build_google_connect_callback_redirect as _build_google_connect_callback_redirect_impl,
    build_google_drive_connect_response as _build_google_drive_connect_response_impl,
    complete_google_drive_connect as _complete_google_drive_connect_impl,
    get_google_drive_account_access_token as _get_google_drive_account_access_token_impl,
    get_google_drive_account_access_token_by_account_id as _get_google_drive_account_access_token_by_account_id_impl,
    resolve_google_connect_state as _resolve_google_connect_state_impl,
)
from .cloud_source_sync_service import (
    refresh_google_drive_media_item_metadata as _refresh_google_drive_media_item_metadata_impl,
    sync_all_google_drive_sources as _sync_all_google_drive_sources_impl,
    sync_google_drive_library_source as _sync_google_drive_library_source_impl,
    sync_visible_google_drive_sources as _sync_visible_google_drive_sources_impl,
)
from .cloud_stream_access_service import (
    build_cloud_stream_response as _build_cloud_stream_response_impl,
    ensure_cloud_media_item_provider_access as _ensure_cloud_media_item_provider_access_impl,
    resolve_media_stream_target as _resolve_media_stream_target_impl,
)


GOOGLE_DRIVE_PROVIDER = "google_drive"


def resolve_google_connect_state(state_token: str) -> dict[str, str | None]:
    return _resolve_google_connect_state_impl(state_token)


def get_cloud_libraries_payload(settings: Settings, *, user: AuthenticatedUser) -> dict[str, object]:
    return _get_cloud_libraries_payload_impl(
        settings,
        user=user,
        provider=GOOGLE_DRIVE_PROVIDER,
    )


def get_google_drive_provider_auth_status_payload(
    settings: Settings,
    *,
    user: AuthenticatedUser,
) -> dict[str, object]:
    return _get_google_drive_provider_auth_status_payload_impl(
        settings,
        user=user,
        provider=GOOGLE_DRIVE_PROVIDER,
        get_access_token_by_account_id=get_google_drive_account_access_token_by_account_id,
    )


def build_google_drive_connect_response(
    settings: Settings,
    *,
    user_id: int,
    return_path: str | None = None,
) -> dict[str, str]:
    return _build_google_drive_connect_response_impl(
        settings,
        user_id=user_id,
        return_path=return_path,
    )


def complete_google_drive_connect(
    settings: Settings,
    *,
    state_token: str,
    code: str,
) -> dict[str, object]:
    return _complete_google_drive_connect_impl(
        settings,
        state_token=state_token,
        code=code,
    )


def add_google_drive_library_source(
    settings: Settings,
    *,
    user: AuthenticatedUser,
    resource_type: Literal["folder", "shared_drive"],
    resource_id: str,
    shared: bool,
) -> dict[str, object]:
    return _add_google_drive_library_source_impl(
        settings,
        user=user,
        resource_type=resource_type,
        resource_id=resource_id,
        shared=shared,
        provider=GOOGLE_DRIVE_PROVIDER,
        get_access_token=get_google_drive_account_access_token,
        sync_source=_sync_google_drive_library_source,
    )


def sync_visible_google_drive_sources(settings: Settings, *, user_id: int) -> None:
    _sync_visible_google_drive_sources(settings, user_id=user_id)


def _sync_visible_google_drive_sources(settings: Settings, *, user_id: int) -> None:
    _sync_visible_google_drive_sources_impl(
        settings,
        user_id=user_id,
        provider=GOOGLE_DRIVE_PROVIDER,
        get_access_token_by_account_id=get_google_drive_account_access_token_by_account_id,
    )


def sync_all_google_drive_sources(settings: Settings) -> dict[str, object]:
    return _sync_all_google_drive_sources_impl(
        settings,
        provider=GOOGLE_DRIVE_PROVIDER,
        get_access_token_by_account_id=get_google_drive_account_access_token_by_account_id,
    )


def _sync_google_drive_library_source(
    settings: Settings,
    *,
    source_id: int,
    raise_on_error: bool,
) -> int:
    return _sync_google_drive_library_source_impl(
        settings,
        source_id=source_id,
        raise_on_error=raise_on_error,
        provider=GOOGLE_DRIVE_PROVIDER,
        get_access_token_by_account_id=get_google_drive_account_access_token_by_account_id,
    )


def hide_shared_library_source_for_user(settings: Settings, *, user: AuthenticatedUser, source_id: int) -> None:
    return _hide_shared_library_source_for_user_impl(
        settings,
        user=user,
        source_id=source_id,
        provider=GOOGLE_DRIVE_PROVIDER,
    )


def show_shared_library_source_for_user(settings: Settings, *, user: AuthenticatedUser, source_id: int) -> None:
    return _show_shared_library_source_for_user_impl(
        settings,
        user=user,
        source_id=source_id,
        provider=GOOGLE_DRIVE_PROVIDER,
    )


def move_google_drive_library_source(
    settings: Settings,
    *,
    user: AuthenticatedUser,
    source_id: int,
    shared: bool,
) -> dict[str, object]:
    return _move_google_drive_library_source_impl(
        settings,
        user=user,
        source_id=source_id,
        shared=shared,
        provider=GOOGLE_DRIVE_PROVIDER,
    )


def resolve_media_stream_target(
    settings: Settings,
    *,
    user_id: int,
    item_id: int,
) -> dict[str, object] | None:
    return _resolve_media_stream_target_impl(
        settings,
        user_id=user_id,
        item_id=item_id,
        get_access_token_by_account_id=get_google_drive_account_access_token_by_account_id,
    )


def build_cloud_stream_response(
    settings: Settings,
    *,
    user_id: int,
    item_id: int,
    range_header: str | None,
    stream_validator=None,
    validated_chunk_size: int | None = None,
):
    return _build_cloud_stream_response_impl(
        settings,
        user_id=user_id,
        item_id=item_id,
        range_header=range_header,
        stream_validator=stream_validator,
        validated_chunk_size=validated_chunk_size,
        get_access_token_by_account_id=get_google_drive_account_access_token_by_account_id,
    )


def refresh_cloud_media_item_metadata(
    settings: Settings,
    *,
    item_id: int,
) -> dict[str, object] | None:
    return _refresh_google_drive_media_item_metadata_impl(
        settings,
        item_id=item_id,
        get_access_token_by_account_id=get_google_drive_account_access_token_by_account_id,
    )


def get_google_drive_account_access_token(settings: Settings, *, user_id: int) -> tuple[str, dict[str, object]]:
    return _get_google_drive_account_access_token_impl(settings, user_id=user_id)


def get_google_drive_account_access_token_by_account_id(
    settings: Settings,
    *,
    google_account_id: int,
) -> str:
    return _get_google_drive_account_access_token_by_account_id_impl(
        settings,
        google_account_id=google_account_id,
    )


def ensure_cloud_media_item_provider_access(
    settings: Settings,
    *,
    user_id: int,
    item_id: int,
) -> None:
    return _ensure_cloud_media_item_provider_access_impl(
        settings,
        user_id=user_id,
        item_id=item_id,
        get_access_token_by_account_id=get_google_drive_account_access_token_by_account_id,
    )


def build_google_connect_callback_redirect(
    settings: Settings,
    *,
    success: bool,
    message: str,
    return_path: str | None = None,
) -> str:
    return _build_google_connect_callback_redirect_impl(
        settings,
        success=success,
        message=message,
        return_path=return_path,
    )
