from __future__ import annotations

from fastapi import APIRouter, Request

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
    )


@router.patch("", response_model=UserSettingsResponse)
def patch_user_settings(
    payload: UserSettingsUpdateRequest,
    request: Request,
    user=CurrentUser,
) -> UserSettingsResponse:
    updated = update_user_settings(
        request.app.state.settings,
        user_id=user.id,
        hide_duplicate_movies=payload.hide_duplicate_movies,
        hide_recently_added=payload.hide_recently_added,
        floating_controls_position=payload.floating_controls_position,
    )
    return UserSettingsResponse(
        hide_duplicate_movies=updated["hide_duplicate_movies"],
        hide_recently_added=updated["hide_recently_added"],
        floating_controls_position=str(updated["floating_controls_position"]),
    )
