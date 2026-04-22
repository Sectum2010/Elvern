from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.media_stream import ensure_media_path_within_root


def test_media_root_allows_real_files_inside_root(initialized_settings) -> None:
    media_file = initialized_settings.media_root / "clip.mp4"
    media_file.write_bytes(b"test payload")

    resolved = ensure_media_path_within_root(media_file, initialized_settings)

    assert resolved == media_file.resolve()


def test_media_root_blocks_parent_path_escape(initialized_settings) -> None:
    outside_file = initialized_settings.media_root.parent / "outside.mp4"
    outside_file.write_bytes(b"not inside media root")

    escaped_path = initialized_settings.media_root / ".." / outside_file.name

    with pytest.raises(HTTPException) as exc_info:
        ensure_media_path_within_root(escaped_path, initialized_settings)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Media path escapes configured media root"


def test_media_root_blocks_symlink_escape(initialized_settings, tmp_path) -> None:
    outside_file = tmp_path / "outside.mp4"
    outside_file.write_bytes(b"outside root")

    symlink_path = initialized_settings.media_root / "linked.mp4"
    symlink_path.symlink_to(outside_file)

    with pytest.raises(HTTPException) as exc_info:
        ensure_media_path_within_root(Path(symlink_path), initialized_settings)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Media path escapes configured media root"
