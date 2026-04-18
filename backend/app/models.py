from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AuthenticatedUser:
    id: int
    username: str
    role: str = "standard_user"
    enabled: bool = True
    assistant_beta_enabled: bool = False
    session_id: int | None = None


@dataclass(slots=True)
class MediaItemRecord:
    id: int
    title: str
    original_filename: str
    file_path: str
    file_size: int
    duration_seconds: float | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None
    container: str | None
    year: int | None
    created_at: str
    updated_at: str
    last_scanned_at: str


@dataclass(slots=True)
class PlaybackProgressRecord:
    media_item_id: int
    position_seconds: float
    duration_seconds: float | None
    completed: bool
    updated_at: str


@dataclass(slots=True)
class ScanStatus:
    running: bool
    job_id: int | None
    started_at: str | None
    finished_at: str | None
    reason: str | None
    files_seen: int
    files_changed: int
    files_removed: int
    message: str | None
