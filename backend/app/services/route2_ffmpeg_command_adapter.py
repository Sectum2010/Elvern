from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .mobile_playback_models import MOBILE_PROFILES, SEGMENT_DURATION_SECONDS
from .route2_transcode_strategy import Route2TranscodeStrategy, Route2TranscodeStrategyConfidence


COPY_PREVIEW_BLOCKING_RISK_FLAGS = {
    "hdr_risk",
    "dolby_vision_risk",
    "high_bit_depth_risk",
    "unsafe_pixel_format",
    "remux_risk",
    "container_packaging_risk",
}
SENSITIVE_QUERY_KEYS = {
    "token",
    "sig",
    "signature",
    "auth",
    "key",
    "api_key",
    "access_token",
    "refresh_token",
    "x-goog-signature",
    "x-goog-credential",
}


@dataclass(slots=True)
class Route2FFmpegCommandAdapterInput:
    ffmpeg_path: str = "/usr/bin/ffmpeg"
    profile_key: str = "mobile_1080p"
    thread_budget: int = 4
    source_input: str | None = None
    source_input_kind: str = "path"
    epoch_start_seconds: float = 0.0
    segment_pattern: str = "segment_%06d.m4s"
    staging_manifest_path: str = "ffmpeg.m3u8"
    strategy: Route2TranscodeStrategy = "full_transcode"
    strategy_confidence: Route2TranscodeStrategyConfidence = "low"
    strategy_reason: str = ""
    video_copy_safe: bool = False
    audio_copy_safe: bool = False
    risk_flags: list[str] = field(default_factory=list)
    missing_metadata: list[str] = field(default_factory=list)
    metadata_source: str = "coarse"
    metadata_trusted: bool = False


@dataclass(slots=True)
class Route2FFmpegCommandAdapterPreview:
    adapter_strategy: Route2TranscodeStrategy
    command_preview: list[str]
    command_preview_summary: str
    fallback_reason: str | None
    active_enabled: bool = False


def _redact_source_input(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return "<source>"
    if text.startswith(("http://", "https://")):
        parts = urlsplit(text)
        safe_query = []
        for key, query_value in parse_qsl(parts.query, keep_blank_values=True):
            normalized_key = key.strip().lower()
            safe_query.append((key, "REDACTED" if normalized_key in SENSITIVE_QUERY_KEYS else query_value))
        redacted_query = "&".join(f"{key}={query_value}" for key, query_value in safe_query)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, redacted_query, parts.fragment))
    return Path(text).name or "<source>"


def _common_command_prefix(payload: Route2FFmpegCommandAdapterInput) -> list[str]:
    command = [
        str(payload.ffmpeg_path),
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-y",
        "-threads",
        str(max(1, int(payload.thread_budget or 1))),
    ]
    if payload.source_input_kind == "url":
        command.extend(
            [
                "-reconnect",
                "1",
                "-reconnect_streamed",
                "1",
                "-reconnect_on_network_error",
                "1",
                "-rw_timeout",
                "15000000",
            ]
        )
    command.extend(
        [
            "-ss",
            f"{float(payload.epoch_start_seconds or 0.0):.3f}",
            "-i",
            _redact_source_input(payload.source_input),
            "-output_ts_offset",
            "0.000",
            "-muxpreload",
            "0",
            "-muxdelay",
            "0",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-sn",
            "-dn",
        ]
    )
    return command


def _common_hls_tail(payload: Route2FFmpegCommandAdapterInput) -> list[str]:
    return [
        "-max_muxing_queue_size",
        "2048",
        "-f",
        "hls",
        "-hls_time",
        f"{SEGMENT_DURATION_SECONDS:.0f}",
        "-hls_list_size",
        "0",
        "-hls_segment_type",
        "fmp4",
        "-hls_fmp4_init_filename",
        "init.mp4",
        "-hls_flags",
        "independent_segments+temp_file",
        "-start_number",
        "0",
        "-hls_segment_filename",
        str(payload.segment_pattern),
        str(payload.staging_manifest_path),
    ]


def _video_transcode_segment(payload: Route2FFmpegCommandAdapterInput) -> list[str]:
    profile = MOBILE_PROFILES[payload.profile_key]
    scale_filter = (
        f"scale=w='min({profile.max_width},iw)':h='min({profile.max_height},ih)':"
        "force_original_aspect_ratio=decrease"
    )
    keyframe_interval = int(SEGMENT_DURATION_SECONDS * 24)
    return [
        "-vf",
        scale_filter,
        "-c:v",
        "libx264",
        "-preset",
        "superfast",
        "-profile:v",
        "high",
        "-level:v",
        profile.level,
        "-pix_fmt",
        "yuv420p",
        "-crf",
        str(profile.crf),
        "-maxrate",
        profile.maxrate,
        "-bufsize",
        profile.bufsize,
        "-g",
        str(keyframe_interval),
        "-keyint_min",
        str(keyframe_interval),
        "-sc_threshold",
        "0",
        "-force_key_frames",
        f"expr:gte(t,n_forced*{SEGMENT_DURATION_SECONDS})",
    ]


def _audio_transcode_segment() -> list[str]:
    return [
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-ar",
        "48000",
        "-b:a",
        "160k",
    ]


def _build_full_transcode_preview(payload: Route2FFmpegCommandAdapterInput) -> list[str]:
    return [
        *_common_command_prefix(payload),
        *_video_transcode_segment(payload),
        *_audio_transcode_segment(),
        *_common_hls_tail(payload),
    ]


def _copy_preview_fallback_reason(
    payload: Route2FFmpegCommandAdapterInput,
    *,
    require_audio_copy: bool,
    require_video_copy: bool,
) -> str | None:
    if payload.strategy_confidence != "high":
        return "Copy preview requires a high-confidence selector decision."
    if payload.metadata_source != "local_ffprobe" or not payload.metadata_trusted:
        return "Copy preview requires trusted local_ffprobe metadata."
    if payload.missing_metadata:
        return "Copy preview requires complete trusted metadata without missing fields."
    blocking_flags = [flag for flag in payload.risk_flags if flag in COPY_PREVIEW_BLOCKING_RISK_FLAGS]
    if blocking_flags:
        return f"Copy preview is blocked by strategy risk flags: {', '.join(sorted(blocking_flags))}."
    if require_video_copy and not payload.video_copy_safe:
        return "Copy preview requires selector-confirmed video copy safety."
    if require_audio_copy and not payload.audio_copy_safe:
        return "Copy preview requires selector-confirmed audio copy safety."
    return None


def build_route2_ffmpeg_command_preview(
    payload: Route2FFmpegCommandAdapterInput,
) -> Route2FFmpegCommandAdapterPreview:
    summary = "Preview only: libx264 video + AAC audio to Route2 HLS fMP4."
    preview = _build_full_transcode_preview(payload)
    adapter_strategy: Route2TranscodeStrategy = "full_transcode"
    fallback_reason: str | None = None

    if payload.strategy == "stream_copy_video_audio":
        fallback_reason = _copy_preview_fallback_reason(
            payload,
            require_audio_copy=True,
            require_video_copy=True,
        )
        if fallback_reason is None:
            adapter_strategy = "stream_copy_video_audio"
            summary = "Preview only: copy video + copy audio to Route2 HLS fMP4."
            preview = [
                *_common_command_prefix(payload),
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                *_common_hls_tail(payload),
            ]
    elif payload.strategy == "copy_video_transcode_audio":
        fallback_reason = _copy_preview_fallback_reason(
            payload,
            require_audio_copy=False,
            require_video_copy=True,
        )
        if fallback_reason is None:
            adapter_strategy = "copy_video_transcode_audio"
            summary = "Preview only: copy video + AAC audio transcode to Route2 HLS fMP4."
            preview = [
                *_common_command_prefix(payload),
                "-c:v",
                "copy",
                *_audio_transcode_segment(),
                *_common_hls_tail(payload),
            ]
    elif payload.strategy == "transcode_video_copy_audio":
        fallback_reason = _copy_preview_fallback_reason(
            payload,
            require_audio_copy=True,
            require_video_copy=False,
        )
        if fallback_reason is None:
            adapter_strategy = "transcode_video_copy_audio"
            summary = "Preview only: libx264 video transcode + copy audio to Route2 HLS fMP4."
            preview = [
                *_common_command_prefix(payload),
                *_video_transcode_segment(payload),
                "-c:a",
                "copy",
                *_common_hls_tail(payload),
            ]
    elif payload.strategy == "unsupported_fallback":
        fallback_reason = payload.strategy_reason or "Selector requested unsupported fallback."

    return Route2FFmpegCommandAdapterPreview(
        adapter_strategy=adapter_strategy,
        command_preview=preview,
        command_preview_summary=summary,
        fallback_reason=fallback_reason,
        active_enabled=False,
    )
