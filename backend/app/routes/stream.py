from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, status

from ..auth import CurrentUser
from ..media_stream import build_stream_response
from ..services.cloud_library_service import build_cloud_stream_response


router = APIRouter(prefix="/api/stream", tags=["stream"])


@router.get("/{item_id}")
def stream_item(
    item_id: int,
    request: Request,
    range_header: str | None = Header(default=None, alias="Range"),
    user=CurrentUser,
):
    target = build_cloud_stream_response(
        request.app.state.settings,
        user_id=user.id,
        item_id=item_id,
        range_header=range_header,
    )
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    if isinstance(target, dict):
        return build_stream_response(str(target["file_path"]), request.app.state.settings, range_header)
    return target
