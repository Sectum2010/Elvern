from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from ..auth import CurrentUser
from ..schemas import SettingsHiddenTitlesResponse
from ..services.settings_hidden_titles_service import (
    get_settings_hidden_titles_payload,
    get_settings_hidden_titles_revision,
    settings_hidden_titles_etag,
)


router = APIRouter(prefix="/api/settings/hidden-titles", tags=["settings-hidden-titles"])


@router.get("", response_model=SettingsHiddenTitlesResponse)
def read_settings_hidden_titles(
    request: Request,
    response: Response,
    user=CurrentUser,
):
    revision = get_settings_hidden_titles_revision(request.app.state.settings, user=user)
    etag = settings_hidden_titles_etag(revision)
    headers = {"Cache-Control": "private, no-cache", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    payload = get_settings_hidden_titles_payload(
        request.app.state.settings,
        user=user,
        revision=revision,
    )
    response.headers.update(headers)
    return SettingsHiddenTitlesResponse(**payload)
