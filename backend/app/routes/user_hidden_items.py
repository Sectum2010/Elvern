from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from ..auth import CurrentUser
from ..schemas import HiddenMovieListResponse, MessageResponse
from ..services.library_service import (
    hide_media_item_for_user,
    list_hidden_media_items,
    show_media_item_for_user,
)


router = APIRouter(prefix="/api/user-hidden-items", tags=["user-hidden-items"])


@router.get("", response_model=HiddenMovieListResponse)
def read_hidden_movies(request: Request, user=CurrentUser) -> HiddenMovieListResponse:
    return HiddenMovieListResponse(
        items=list_hidden_media_items(request.app.state.settings, user_id=user.id)
    )


@router.post("/{item_id}", response_model=MessageResponse)
def hide_movie(item_id: int, request: Request, user=CurrentUser) -> MessageResponse:
    try:
        hide_media_item_for_user(request.app.state.settings, user_id=user.id, item_id=item_id)
    except ValueError as exc:
        if str(exc) == "not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found") from exc
        raise
    return MessageResponse(message="This movie is hidden for your account")


@router.delete("/{item_id}", response_model=MessageResponse)
def show_movie_again(item_id: int, request: Request, user=CurrentUser) -> MessageResponse:
    show_media_item_for_user(request.app.state.settings, user_id=user.id, item_id=item_id)
    return MessageResponse(message="This movie is visible again")
