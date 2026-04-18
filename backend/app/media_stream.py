from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Callable, Iterator

from fastapi import HTTPException, status
from fastapi.responses import StreamingResponse

from .config import Settings


RANGE_PATTERN = re.compile(r"bytes=(\d*)-(\d*)")


def ensure_media_path_within_root(file_path: Path, settings: Settings) -> Path:
    resolved = file_path.resolve()
    media_root = settings.media_root.resolve()
    try:
        resolved.relative_to(media_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Media path escapes configured media root",
        ) from exc
    return resolved


def _parse_range_header(range_header: str | None, file_size: int) -> tuple[int, int, bool]:
    if not range_header:
        return 0, file_size - 1, False
    match = RANGE_PATTERN.fullmatch(range_header.strip())
    if not match:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Invalid range header",
        )
    start_raw, end_raw = match.groups()
    if start_raw == "" and end_raw == "":
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Empty range header",
        )
    if start_raw == "":
        length = int(end_raw)
        start = max(file_size - length, 0)
        end = file_size - 1
    else:
        start = int(start_raw)
        end = int(end_raw) if end_raw else file_size - 1
    if start > end or start >= file_size:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Requested range is outside the file",
        )
    end = min(end, file_size - 1)
    return start, end, True


def _iter_file(
    file_path: Path,
    start: int,
    end: int,
    *,
    chunk_size: int = 1024 * 1024,
    stream_validator: Callable[[], bool] | None = None,
) -> Iterator[bytes]:
    effective_chunk_size = 64 * 1024 if stream_validator else chunk_size
    with file_path.open("rb") as handle:
        handle.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            if stream_validator and not stream_validator():
                break
            chunk = handle.read(min(effective_chunk_size, remaining))
            if not chunk:
                break
            if stream_validator and not stream_validator():
                break
            remaining -= len(chunk)
            yield chunk


def build_stream_response(
    file_path: str,
    settings: Settings,
    range_header: str | None,
    *,
    stream_validator: Callable[[], bool] | None = None,
) -> StreamingResponse:
    resolved = ensure_media_path_within_root(Path(file_path), settings)
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media file not found")

    file_size = resolved.stat().st_size
    start, end, partial = _parse_range_header(range_header, file_size)
    media_type, _ = mimetypes.guess_type(resolved.name)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Cache-Control": "private, max-age=0, must-revalidate",
    }
    status_code = status.HTTP_200_OK
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        status_code = status.HTTP_206_PARTIAL_CONTENT
    return StreamingResponse(
        _iter_file(resolved, start, end, stream_validator=stream_validator),
        media_type=media_type or "application/octet-stream",
        headers=headers,
        status_code=status_code,
    )
