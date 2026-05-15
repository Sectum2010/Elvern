from __future__ import annotations

import json

from backend.app.services.library_service import _extract_playback_tracks_from_probe_summary


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
                "tags": {"language": "jpn"},
                "disposition": {"default": 0},
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
                "tags": {"language": "eng"},
            },
        ],
    }

    audio_tracks, subtitle_tracks = _extract_playback_tracks_from_probe_summary(json.dumps(payload))

    assert [track["index"] for track in audio_tracks] == [1, 2]
    assert audio_tracks[0]["label"] == "English 5.1 (eng / aac / 6ch)"
    assert audio_tracks[0]["disposition_default"] is True
    assert [track["index"] for track in subtitle_tracks] == [3, 4]
    assert subtitle_tracks[0]["text_based"] is True
    assert subtitle_tracks[0]["disposition_forced"] is True
    assert subtitle_tracks[1]["image_based"] is True
