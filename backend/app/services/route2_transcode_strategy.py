from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# Shadow-only selector.
# This does not alter the ffmpeg command path yet.
# The first active implementation should stay narrow:
# enable copy/remux only for high-confidence H.264/AAC or H.264 plus audio-transcode cases.
# HEVC, Dolby Vision, and HDR copy are future work, not v1.

Route2TranscodeStrategy = Literal[
    "stream_copy_video_audio",
    "copy_video_transcode_audio",
    "transcode_video_copy_audio",
    "full_transcode",
    "unsupported_fallback",
]
Route2TranscodeStrategyConfidence = Literal["low", "medium", "high"]


@dataclass(slots=True)
class Route2TranscodeStrategyInput:
    container: str | None = None
    video_codec: str | None = None
    video_profile: str | None = None
    video_level: str | None = None
    audio_codec: str | None = None
    audio_profile: str | None = None
    width: int | None = None
    height: int | None = None
    pixel_format: str | None = None
    bit_depth: int | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    color_space: str | None = None
    hdr_flag: bool | None = None
    dolby_vision_flag: bool | None = None
    audio_channels: int | None = None
    audio_channel_layout: str | None = None
    audio_sample_rate: int | None = None
    profile_key: str = "mobile_1080p"
    client_device_class: str | None = None
    source_kind: str = "local"
    original_filename: str | None = None


@dataclass(slots=True)
class Route2TranscodeStrategyDecision:
    strategy: Route2TranscodeStrategy
    confidence: Route2TranscodeStrategyConfidence
    reason: str
    video_copy_safe: bool
    audio_copy_safe: bool
    requires_video_transcode: bool
    requires_audio_transcode: bool
    missing_metadata: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)


SAFE_VIDEO_CODECS = {"h264", "avc", "avc1", "x264"}
SAFE_AUDIO_CODECS = {"aac", "aac_lc", "mp4a"}
SAFE_CONTAINERS = {"mp4", "m4v", "mov"}
HARD_VIDEO_TRANSCODE_CODECS = {"hevc", "h265", "x265", "hev1", "hvc1", "av1"}
SAFE_PIXEL_FORMATS = {"yuv420p"}
PROFILE_DIMENSION_LIMITS: dict[str, tuple[int, int]] = {
    "mobile_1080p": (1920, 1080),
    "mobile_2160p": (3840, 2160),
}


def _normalize_token(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).strip().lower().replace(" ", "").replace("-", "").replace(".", "")


def _filename_hints(value: str | None) -> str:
    return _normalize_token(value)


def _has_any_token(haystack: str, *tokens: str) -> bool:
    return any(token in haystack for token in tokens)


def _profile_requires_downscale(
    *,
    width: int | None,
    height: int | None,
    profile_key: str,
) -> bool:
    limits = PROFILE_DIMENSION_LIMITS.get(profile_key)
    if limits is None or width is None or height is None:
        return False
    max_width, max_height = limits
    return width > max_width or height > max_height


def _audio_codec_transcodable(audio_codec: str | None) -> bool:
    return bool(_normalize_token(audio_codec))


def select_route2_transcode_strategy(
    payload: Route2TranscodeStrategyInput,
) -> Route2TranscodeStrategyDecision:
    normalized_container = _normalize_token(payload.container)
    normalized_video_codec = _normalize_token(payload.video_codec)
    normalized_audio_codec = _normalize_token(payload.audio_codec)
    filename_hints = _filename_hints(payload.original_filename)
    missing_metadata: list[str] = []
    risk_flags: list[str] = []

    hdr_risk = bool(payload.hdr_flag) or _has_any_token(filename_hints, "hdr", "hdr10", "hdr10plus", "hdr10+")
    dv_risk = bool(payload.dolby_vision_flag) or _has_any_token(filename_hints, "dovi", "dolbyvision", "dv")
    remux_risk = _has_any_token(filename_hints, "remux", "bdremux", "brremux")
    bit_depth = int(payload.bit_depth) if payload.bit_depth not in {None, ""} else None
    high_bit_depth_risk = (bit_depth is not None and bit_depth > 8) or _has_any_token(filename_hints, "10bit")
    pixel_format = _normalize_token(payload.pixel_format)
    unsafe_pixel_format = bool(pixel_format) and pixel_format not in SAFE_PIXEL_FORMATS
    profile_requires_downscale = _profile_requires_downscale(
        width=payload.width,
        height=payload.height,
        profile_key=payload.profile_key,
    )

    if hdr_risk:
        risk_flags.append("hdr_risk")
    if dv_risk:
        risk_flags.append("dolby_vision_risk")
    if remux_risk:
        risk_flags.append("remux_risk")
    if high_bit_depth_risk:
        risk_flags.append("high_bit_depth_risk")
    if unsafe_pixel_format:
        risk_flags.append("unsafe_pixel_format")
    if profile_requires_downscale:
        risk_flags.append("profile_requires_downscale")

    if not normalized_video_codec:
        missing_metadata.append("video_codec")
    if not normalized_audio_codec:
        missing_metadata.append("audio_codec")
    if not normalized_container:
        missing_metadata.append("container")
    if payload.width is None:
        missing_metadata.append("width")
    if payload.height is None:
        missing_metadata.append("height")

    audio_copy_safe = normalized_audio_codec in SAFE_AUDIO_CODECS and payload.audio_channels in {1, 2}
    if normalized_audio_codec in SAFE_AUDIO_CODECS and payload.audio_channels is None:
        missing_metadata.append("audio_channels")

    if not normalized_video_codec and not normalized_audio_codec and not normalized_container and not filename_hints:
        return Route2TranscodeStrategyDecision(
            strategy="unsupported_fallback",
            confidence="low",
            reason="Core media metadata is absent, so Route2 cannot safely classify a conservative copy/remux path.",
            video_copy_safe=False,
            audio_copy_safe=False,
            requires_video_transcode=True,
            requires_audio_transcode=True,
            missing_metadata=missing_metadata,
            risk_flags=risk_flags,
        )

    if normalized_video_codec in HARD_VIDEO_TRANSCODE_CODECS or hdr_risk or dv_risk or high_bit_depth_risk:
        return Route2TranscodeStrategyDecision(
            strategy="full_transcode",
            confidence="high",
            reason="The source video is outside the conservative universal-browser copy/remux contract, so Route2 should stay on full transcode.",
            video_copy_safe=False,
            audio_copy_safe=audio_copy_safe,
            requires_video_transcode=True,
            requires_audio_transcode=not audio_copy_safe,
            missing_metadata=missing_metadata,
            risk_flags=risk_flags,
        )

    video_codec_safe = normalized_video_codec in SAFE_VIDEO_CODECS
    container_safe = normalized_container in SAFE_CONTAINERS
    pixel_metadata_complete = bool(pixel_format) and bit_depth is not None
    pixel_metadata_safe = pixel_format in SAFE_PIXEL_FORMATS and bit_depth == 8
    if video_codec_safe and not pixel_format:
        missing_metadata.append("pixel_format")
    if video_codec_safe and bit_depth is None:
        missing_metadata.append("bit_depth")
    if video_codec_safe and not container_safe:
        risk_flags.append("container_packaging_risk")

    if not video_codec_safe:
        return Route2TranscodeStrategyDecision(
            strategy="full_transcode",
            confidence="low" if normalized_video_codec else "medium",
            reason="The source video codec is not in the conservative Route2 copy-safe set.",
            video_copy_safe=False,
            audio_copy_safe=audio_copy_safe,
            requires_video_transcode=True,
            requires_audio_transcode=not audio_copy_safe,
            missing_metadata=missing_metadata,
            risk_flags=risk_flags,
        )

    if not pixel_metadata_complete or not pixel_metadata_safe:
        return Route2TranscodeStrategyDecision(
            strategy="full_transcode",
            confidence="medium",
            reason="Route2 lacks the explicit SDR 8-bit H.264 metadata needed for a safe copy/remux decision, so it should stay conservative.",
            video_copy_safe=False,
            audio_copy_safe=audio_copy_safe,
            requires_video_transcode=True,
            requires_audio_transcode=not audio_copy_safe,
            missing_metadata=missing_metadata,
            risk_flags=risk_flags,
        )

    if payload.width is None or payload.height is None:
        return Route2TranscodeStrategyDecision(
            strategy="full_transcode",
            confidence="medium",
            reason="Route2 needs explicit source dimensions before it can safely classify a copy/remux path against the active browser profile cap.",
            video_copy_safe=False,
            audio_copy_safe=audio_copy_safe,
            requires_video_transcode=True,
            requires_audio_transcode=not audio_copy_safe,
            missing_metadata=missing_metadata,
            risk_flags=risk_flags,
        )

    if profile_requires_downscale:
        return Route2TranscodeStrategyDecision(
            strategy="transcode_video_copy_audio" if audio_copy_safe else "full_transcode",
            confidence="high" if audio_copy_safe else "medium",
            reason="The source exceeds the current browser playback profile cap, so video still needs transcode even though the codec family is otherwise safe.",
            video_copy_safe=False,
            audio_copy_safe=audio_copy_safe,
            requires_video_transcode=True,
            requires_audio_transcode=not audio_copy_safe,
            missing_metadata=missing_metadata,
            risk_flags=risk_flags,
        )

    if not container_safe or remux_risk:
        return Route2TranscodeStrategyDecision(
            strategy="full_transcode",
            confidence="medium",
            reason="The source container or remux packaging is outside the first conservative copy/remux rollout.",
            video_copy_safe=False,
            audio_copy_safe=audio_copy_safe,
            requires_video_transcode=True,
            requires_audio_transcode=not audio_copy_safe,
            missing_metadata=missing_metadata,
            risk_flags=risk_flags,
        )

    if audio_copy_safe:
        return Route2TranscodeStrategyDecision(
            strategy="stream_copy_video_audio",
            confidence="high",
            reason="The source already matches the conservative H.264/AAC SDR contract for a future Route2 copy/remux rollout.",
            video_copy_safe=True,
            audio_copy_safe=True,
            requires_video_transcode=False,
            requires_audio_transcode=False,
            missing_metadata=missing_metadata,
            risk_flags=risk_flags,
        )

    if _audio_codec_transcodable(payload.audio_codec):
        return Route2TranscodeStrategyDecision(
            strategy="copy_video_transcode_audio",
            confidence="high",
            reason="The source video looks conservatively copy-safe, but the audio should still transcode to AAC for the current universal browser contract.",
            video_copy_safe=True,
            audio_copy_safe=False,
            requires_video_transcode=False,
            requires_audio_transcode=True,
            missing_metadata=missing_metadata,
            risk_flags=risk_flags,
        )

    return Route2TranscodeStrategyDecision(
        strategy="full_transcode",
        confidence="medium",
        reason="Audio metadata is too incomplete for a safe copy/remux decision, so Route2 should stay on full transcode.",
        video_copy_safe=False,
        audio_copy_safe=False,
        requires_video_transcode=True,
        requires_audio_transcode=True,
        missing_metadata=missing_metadata,
        risk_flags=risk_flags,
    )
