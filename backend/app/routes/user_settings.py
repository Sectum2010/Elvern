from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from ..auth import CurrentUser
from ..schemas import UserSettingsResponse, UserSettingsUpdateRequest
from ..services.user_settings_service import (
    UserSettingsValidationError,
    delete_user_background_photo,
    get_user_background_photo_path,
    get_user_settings,
    save_user_background_photo,
    update_user_settings,
)


router = APIRouter(prefix="/api/user-settings", tags=["user-settings"])


def _media_library_reference_source(payload: dict[str, object]) -> str:
    if str(payload.get("media_library_reference_private_value") or "").strip():
        return "private_reference"
    return "shared_default"


def _media_library_reference_label(source: str) -> str:
    if source == "private_reference":
        return "My private reference"
    return "Shared default"


def _to_user_settings_response(payload: dict[str, object], *, user_role: str) -> UserSettingsResponse:
    media_reference_source = _media_library_reference_source(payload)
    media_reference_private_value = payload["media_library_reference_private_value"]
    if user_role == "standard_user":
        media_reference_shared_default_value = ""
        media_reference_effective_value = (
            str(media_reference_private_value)
            if media_reference_source == "private_reference" and media_reference_private_value is not None
            else ""
        )
    else:
        media_reference_shared_default_value = str(payload["media_library_reference_shared_default_value"])
        media_reference_effective_value = str(payload["media_library_reference_effective_value"])
    return UserSettingsResponse(
        hide_duplicate_movies=bool(payload["hide_duplicate_movies"]),
        hide_recently_added=bool(payload["hide_recently_added"]),
        floating_library_search_enabled=bool(payload["floating_library_search_enabled"]),
        poster_card_appearance=str(payload["poster_card_appearance"]),
        poster_card_display_max_width=str(payload["poster_card_display_max_width"]),
        background_mode=str(payload["background_mode"]),
        background_preset=str(payload["background_preset"]),
        background_gradient_start=str(payload["background_gradient_start"]),
        background_gradient_end=str(payload["background_gradient_end"]),
        background_gradient_accent=str(payload["background_gradient_accent"]),
        background_solid_color=str(payload["background_solid_color"]),
        background_photo_url=payload["background_photo_url"],
        media_library_reference_private_value=media_reference_private_value,
        media_library_reference_shared_default_value=media_reference_shared_default_value,
        media_library_reference_effective_value=media_reference_effective_value,
        media_library_reference_effective_source=media_reference_source,
        media_library_reference_effective_label=_media_library_reference_label(media_reference_source),
    )


@router.get("", response_model=UserSettingsResponse)
def read_user_settings(request: Request, user=CurrentUser) -> UserSettingsResponse:
    payload = get_user_settings(request.app.state.settings, user_id=user.id)
    return _to_user_settings_response(payload, user_role=user.role)


@router.patch("", response_model=UserSettingsResponse)
def patch_user_settings(
    payload: UserSettingsUpdateRequest,
    request: Request,
    user=CurrentUser,
) -> UserSettingsResponse:
    if payload.media_library_reference_private_value is not None and user.role != "standard_user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only standard users can set a private media library reference",
        )
    try:
        updated = update_user_settings(
            request.app.state.settings,
            user_id=user.id,
            hide_duplicate_movies=payload.hide_duplicate_movies,
            hide_recently_added=payload.hide_recently_added,
            floating_library_search_enabled=payload.floating_library_search_enabled,
            poster_card_appearance=payload.poster_card_appearance,
            poster_card_display_max_width=payload.poster_card_display_max_width,
            background_mode=payload.background_mode,
            background_preset=payload.background_preset,
            background_gradient_start=payload.background_gradient_start,
            background_gradient_end=payload.background_gradient_end,
            background_gradient_accent=payload.background_gradient_accent,
            background_solid_color=payload.background_solid_color,
            media_library_reference_private_value=payload.media_library_reference_private_value,
        )
    except UserSettingsValidationError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return _to_user_settings_response(updated, user_role=user.role)


@router.get("/background-photo")
def read_background_photo(request: Request, user=CurrentUser) -> FileResponse:
    photo_path = get_user_background_photo_path(request.app.state.settings, user_id=user.id)
    if not photo_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Background photo not found")
    return FileResponse(
        photo_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-cache, max-age=0, must-revalidate"},
    )


@router.post("/background-photo", response_model=UserSettingsResponse)
async def upload_background_photo(
    request: Request,
    file: UploadFile = File(...),
    user=CurrentUser,
) -> UserSettingsResponse:
    content = await file.read()
    try:
        updated = save_user_background_photo(
            request.app.state.settings,
            user_id=user.id,
            content=content,
            content_type=file.content_type,
        )
    except UserSettingsValidationError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return _to_user_settings_response(updated, user_role=user.role)


@router.delete("/background-photo", response_model=UserSettingsResponse)
def remove_background_photo(request: Request, user=CurrentUser) -> UserSettingsResponse:
    updated = delete_user_background_photo(request.app.state.settings, user_id=user.id)
    return _to_user_settings_response(updated, user_role=user.role)
