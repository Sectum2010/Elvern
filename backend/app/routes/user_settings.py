from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from ..auth import CurrentUser
from ..schemas import UserSettingsResponse, UserSettingsUpdateRequest
from ..services.user_settings_service import get_user_settings, update_user_settings


router = APIRouter(prefix="/api/user-settings", tags=["user-settings"])


@router.get("", response_model=UserSettingsResponse)
def read_user_settings(request: Request, user=CurrentUser) -> UserSettingsResponse:
    payload = get_user_settings(request.app.state.settings, user_id=user.id)
    return UserSettingsResponse(
        hide_duplicate_movies=payload["hide_duplicate_movies"],
        hide_recently_added=payload["hide_recently_added"],
        floating_controls_position=str(payload["floating_controls_position"]),
        poster_card_appearance=str(payload["poster_card_appearance"]),
        media_library_reference_private_value=payload["media_library_reference_private_value"],
        media_library_reference_shared_default_value=str(payload["media_library_reference_shared_default_value"]),
        media_library_reference_effective_value=str(payload["media_library_reference_effective_value"]),
    )


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
    updated = update_user_settings(
        request.app.state.settings,
        user_id=user.id,
        hide_duplicate_movies=payload.hide_duplicate_movies,
        hide_recently_added=payload.hide_recently_added,
        floating_controls_position=payload.floating_controls_position,
        poster_card_appearance=payload.poster_card_appearance,
        media_library_reference_private_value=payload.media_library_reference_private_value,
    )
    return UserSettingsResponse(
        hide_duplicate_movies=updated["hide_duplicate_movies"],
        hide_recently_added=updated["hide_recently_added"],
        floating_controls_position=str(updated["floating_controls_position"]),
        poster_card_appearance=str(updated["poster_card_appearance"]),
        media_library_reference_private_value=updated["media_library_reference_private_value"],
        media_library_reference_shared_default_value=str(updated["media_library_reference_shared_default_value"]),
        media_library_reference_effective_value=str(updated["media_library_reference_effective_value"]),
    )
