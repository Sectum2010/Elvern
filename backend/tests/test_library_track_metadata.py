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
