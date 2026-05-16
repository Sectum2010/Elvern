from __future__ import annotations

import json
import subprocess

from fastapi import HTTPException

from backend.app.db import get_connection, utcnow_iso
from backend.app.services.library_service import _extract_playback_tracks_from_probe_summary, get_media_item_detail
from backend.app.services.local_library_source_service import ensure_current_shared_local_source_binding
from backend.app.services.media_technical_metadata_service import (
    get_technical_metadata,
    probe_cloud_item_technical_metadata,
    upsert_technical_metadata,
)


def test_extracts_audio_and_subtitle_tracks_from_probe_summary() -> None:
    payload = {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 6,
                "tags": {"language": "eng", "title": "English 5.1"},
                "disposition": {"default": 1},
            },
            {
                "index": 2,
                "codec_type": "audio",
                "codec_name": "ac3",
                "channels": 2,
                "tags": {"language": "jpn", "title": "Director Commentary"},
                "disposition": {"default": 0, "comment": 1},
            },
            {
                "index": 3,
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "tags": {"language": "eng", "title": "English"},
                "disposition": {"forced": 1},
            },
            {
                "index": 4,
                "codec_type": "subtitle",
                "codec_name": "hdmv_pgs_subtitle",
                "codec_long_name": "HDMV Presentation Graphic Stream subtitles",
                "tags": {"language": "eng", "title": "PGS"},
            },
        ],
    }

    audio_tracks, subtitle_tracks = _extract_playback_tracks_from_probe_summary(json.dumps(payload))

    assert [track["index"] for track in audio_tracks] == [1, 2]
    assert audio_tracks[0]["label"] == "English 5.1 (eng / aac / 6ch)"
    assert audio_tracks[0]["disposition_default"] is True
    assert audio_tracks[1]["disposition_commentary"] is True
    assert [track["index"] for track in subtitle_tracks] == [3, 4]
    assert subtitle_tracks[0]["text_based"] is True
    assert subtitle_tracks[0]["browser_supported"] is True
    assert subtitle_tracks[0]["disposition_forced"] is True
    assert subtitle_tracks[1]["image_based"] is True
    assert subtitle_tracks[1]["browser_supported"] is False
    assert subtitle_tracks[1]["codec_long_name"] == "HDMV Presentation Graphic Stream subtitles"
    assert subtitle_tracks[1]["track_source"] == "raw_probe_summary_json"


def test_extracts_multiple_audio_tracks_with_global_stream_indexes() -> None:
    payload = {
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264"},
            {
                "index": 2,
                "codec_type": "audio",
                "codec_name": "eac3",
                "channels": 8,
                "tags": {"language": "eng", "title": "Atmos"},
                "disposition": {"default": 1},
            },
            {
                "index": 5,
                "codec_type": "audio",
                "codec_name": "ac3",
                "channels": 6,
                "tags": {"language": "eng"},
                "disposition": {"default": 0},
            },
            {
                "index": 7,
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "tags": {"language": "eng", "title": "Commentary"},
                "disposition": {"commentary": 1},
            },
        ],
    }

    audio_tracks, _subtitle_tracks = _extract_playback_tracks_from_probe_summary(json.dumps(payload))

    assert [track["index"] for track in audio_tracks] == [2, 5, 7]
    assert audio_tracks[0]["codec"] == "eac3"
    assert audio_tracks[0]["channels"] == 8
    assert audio_tracks[1]["codec"] == "ac3"
    assert audio_tracks[2]["disposition_commentary"] is True


def test_media_row_fallback_audio_is_diagnostic_not_switchable(initialized_settings) -> None:
    settings = initialized_settings
    now = utcnow_iso()
    media_file = settings.media_root / "fallback-audio.mkv"
    media_file.write_bytes(b"fake media")
    with get_connection(settings) as connection:
        source_id = ensure_current_shared_local_source_binding(settings, connection=connection)
        connection.execute(
            """
            INSERT INTO media_items (
                id, title, original_filename, file_path, source_kind, library_source_id,
                file_size, file_mtime, duration_seconds, width, height,
                video_codec, audio_codec, container, year, created_at, updated_at, last_scanned_at
            ) VALUES (?, ?, ?, ?, 'local', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                701,
                "Fallback Audio",
                media_file.name,
                str(media_file),
                source_id,
                int(media_file.stat().st_size),
                float(media_file.stat().st_mtime),
                120.0,
                1920,
                1080,
                "h264",
                "aac",
                "mkv",
                now,
                now,
                now,
            ),
        )
        connection.commit()

    detail = get_media_item_detail(settings, user_id=1, item_id=701)

    assert detail is not None
    assert detail["audio_tracks"] == []
    diagnostics = detail["audio_track_diagnostics"]
    assert diagnostics["trusted_count"] == 0
    assert diagnostics["fallback_count"] == 1
    assert diagnostics["tracks"][0]["track_source"] == "media_row_fallback"
    assert diagnostics["tracks"][0]["title"] == "Default audio"


def test_audio_track_diagnostics_expose_trusted_probe_tracks(initialized_settings) -> None:
    settings = initialized_settings
    now = utcnow_iso()
    media_file = settings.media_root / "trusted-audio.mkv"
    media_file.write_bytes(b"fake media")
    with get_connection(settings) as connection:
        source_id = ensure_current_shared_local_source_binding(settings, connection=connection)
        connection.execute(
            """
            INSERT INTO media_items (
                id, title, original_filename, file_path, source_kind, library_source_id,
                file_size, file_mtime, duration_seconds, width, height,
                video_codec, audio_codec, container, year, created_at, updated_at, last_scanned_at
            ) VALUES (?, ?, ?, ?, 'local', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                702,
                "Trusted Audio",
                media_file.name,
                str(media_file),
                source_id,
                int(media_file.stat().st_size),
                float(media_file.stat().st_mtime),
                120.0,
                1920,
                1080,
                "h264",
                "aac",
                "mkv",
                now,
                now,
                now,
            ),
        )
        connection.commit()
    upsert_technical_metadata(
        settings,
        media_item_id=702,
        values={
            "metadata_version": 1,
            "metadata_source": "local_ffprobe",
            "probe_status": "probed",
            "probe_error": None,
            "probed_at": now,
            "updated_at": now,
            "source_fingerprint": "test-fingerprint",
            "raw_probe_summary_json": json.dumps(
                {
                    "streams": [
                        {"index": 0, "codec_type": "video", "codec_name": "h264"},
                        {"index": 2, "codec_type": "audio", "codec_name": "ac3", "channels": 6},
                        {"index": 5, "codec_type": "audio", "codec_name": "aac", "channels": 2},
                    ],
                }
            ),
        },
    )

    detail = get_media_item_detail(settings, user_id=1, item_id=702)

    assert detail is not None
    assert [track["index"] for track in detail["audio_tracks"]] == [2, 5]
    assert all(track["track_source"] == "raw_probe_summary_json" for track in detail["audio_tracks"])
    diagnostics = detail["audio_track_diagnostics"]
    assert diagnostics["trusted_count"] == 2
    assert diagnostics["fallback_count"] == 0
    assert diagnostics["tracks"][0]["browser_supported"] is True


def test_cloud_ffprobe_uses_provider_stream_url_and_stores_raw_tracks(initialized_settings, monkeypatch) -> None:
    settings = initialized_settings
    now = utcnow_iso()
    with get_connection(settings) as connection:
        connection.execute(
            """
            INSERT INTO media_items (
                id, title, original_filename, file_path, source_kind, library_source_id,
                external_media_id, cloud_resource_key, file_size, file_mtime,
                duration_seconds, width, height, video_codec, audio_codec, container,
                year, created_at, updated_at, last_scanned_at
            ) VALUES (?, ?, ?, ?, 'cloud', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                703,
                "Cloud Multi Audio",
                "cloud.mkv",
                "gdrive://cloud/file/cloud.mkv",
                "cloud-file-id",
                "resource-key",
                12345,
                111.0,
                120.0,
                1920,
                1080,
                "h264",
                "aac",
                "mkv",
                now,
                now,
                now,
            ),
        )
        connection.commit()
    session_payload = {
        "session_id": "cloud-probe-session",
        "access_token": "cloud-probe-token",
        "stream_url": "https://public.example/native/cloud-probe/stream",
    }
    closed = []

    monkeypatch.setattr(
        "backend.app.services.native_playback_service.create_native_playback_session",
        lambda *_args, **_kwargs: session_payload,
    )
    monkeypatch.setattr(
        "backend.app.services.native_playback_service.close_native_playback_session",
        lambda *_args, **kwargs: closed.append(kwargs["session_id"]),
    )
    monkeypatch.setattr(
        "backend.app.services.mobile_playback_source_service._rewrite_stream_url_for_server_localhost",
        lambda _settings, *, stream_url: "http://127.0.0.1:8000/native/cloud-probe/stream",
    )

    def fake_run(command, **_kwargs):
        assert "-reconnect" in command
        assert "-reconnect_streamed" in command
        assert command[-1] == "http://127.0.0.1:8000/native/cloud-probe/stream"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "streams": [
                        {"index": 0, "codec_type": "video", "codec_name": "h264"},
                        {"index": 1, "codec_type": "audio", "codec_name": "eac3", "channels": 8},
                        {"index": 3, "codec_type": "audio", "codec_name": "ac3", "channels": 6},
                    ],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = probe_cloud_item_technical_metadata(
        settings,
        {
            "id": 703,
            "source_kind": "cloud",
            "title": "Cloud Multi Audio",
            "original_filename": "cloud.mkv",
            "file_path": "gdrive://cloud/file/cloud.mkv",
            "library_source_id": 9,
            "external_media_id": "cloud-file-id",
            "cloud_resource_key": "resource-key",
            "file_size": 12345,
            "file_mtime": 111.0,
        },
        user_id=1,
    )

    assert result["status"] == "probed"
    assert closed == ["cloud-probe-session"]
    metadata = get_technical_metadata(settings, 703)
    assert metadata is not None
    assert metadata["metadata_source"] == "cloud_ffprobe"
    audio_tracks, _subtitle_tracks = _extract_playback_tracks_from_probe_summary(metadata["raw_probe_summary_json"])
    assert [track["index"] for track in audio_tracks] == [1, 3]


def test_cloud_auth_failure_does_not_create_fake_audio_track(initialized_settings, monkeypatch) -> None:
    settings = initialized_settings
    now = utcnow_iso()
    with get_connection(settings) as connection:
        connection.execute(
            """
            INSERT INTO media_items (
                id, title, original_filename, file_path, source_kind, library_source_id,
                external_media_id, cloud_resource_key, file_size, file_mtime,
                duration_seconds, width, height, video_codec, audio_codec, container,
                year, created_at, updated_at, last_scanned_at
            ) VALUES (?, ?, ?, ?, 'cloud', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                704,
                "Cloud Auth Failed",
                "cloud-auth.mkv",
                "gdrive://cloud/file/cloud-auth.mkv",
                "cloud-auth-file-id",
                "resource-key",
                12345,
                111.0,
                120.0,
                1920,
                1080,
                "h264",
                "aac",
                "mkv",
                now,
                now,
                now,
            ),
        )
        connection.commit()

    def fake_create(*_args, **_kwargs):
        raise HTTPException(status_code=401, detail={"code": "provider_auth_required", "provider_reason": "reauth_required"})

    monkeypatch.setattr("backend.app.services.native_playback_service.create_native_playback_session", fake_create)
    result = probe_cloud_item_technical_metadata(
        settings,
        {
            "id": 704,
            "source_kind": "cloud",
            "title": "Cloud Auth Failed",
            "original_filename": "cloud-auth.mkv",
            "file_path": "gdrive://cloud/file/cloud-auth.mkv",
            "library_source_id": 9,
            "external_media_id": "cloud-auth-file-id",
            "cloud_resource_key": "resource-key",
            "file_size": 12345,
            "file_mtime": 111.0,
        },
        user_id=1,
    )

    assert result["status"] == "failed"
    assert result["reason"] == "provider_reconnect_required"
    metadata = get_technical_metadata(settings, 704)
    assert metadata is not None
    assert metadata["probe_status"] == "failed"
    assert metadata["probe_error"] == "provider_reconnect_required"


def test_subtitle_codec_aliases_classify_text_and_image_tracks() -> None:
    payload = {
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264"},
            {"index": 3, "codec_type": "subtitle", "codec_name": "tx3g", "tags": {"language": "eng"}},
            {"index": 4, "codec_type": "subtitle", "codec_name": "dvb_subtitle", "tags": {"language": "eng"}},
            {"index": 5, "codec_type": "subtitle", "codec_name": "mystery_subtitle", "tags": {"language": "eng"}},
        ],
    }

    _audio_tracks, subtitle_tracks = _extract_playback_tracks_from_probe_summary(json.dumps(payload))

    assert subtitle_tracks[0]["text_based"] is True
    assert subtitle_tracks[0]["browser_supported"] is True
    assert subtitle_tracks[1]["image_based"] is True
    assert subtitle_tracks[1]["browser_supported"] is False
    assert subtitle_tracks[2]["text_based"] is False
    assert subtitle_tracks[2]["image_based"] is False
