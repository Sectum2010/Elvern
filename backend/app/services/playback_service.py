from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlsplit

from ..config import Settings
from .native_playback_service import create_native_playback_session
from .transcode_service import TranscodeManager


logger = logging.getLogger(__name__)

SAFE_VIDEO_CODECS = {"h264"}
SAFE_AUDIO_CODECS = {"aac"}
DESKTOP_AUDIO_CODECS = {"aac", "mp3"}
DIRECT_PLAY_EXTENSIONS = {".mp4", ".m4v"}


def normalize_codec(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if "264" in normalized or normalized in {"avc", "avc1"}:
        return "h264"
    if "aac" in normalized or normalized.startswith("mp4a"):
        return "aac"
    if normalized in {"mp3", "mp2", "mpga"}:
        return "mp3"
    return normalized


def detect_client_profile(user_agent: str | None) -> str:
    if not user_agent:
        return "unknown"
    normalized = user_agent.lower()
    is_ios = any(token in normalized for token in ("iphone", "ipad", "ipod"))
    if "fxios" in normalized or "firefox" in normalized:
        return "firefox"
    if is_ios and "safari" in normalized and "crios" not in normalized and "edgios" not in normalized:
        return "iphone_safari"
    if "safari" in normalized and "chrome" not in normalized and "chromium" not in normalized and "edg" not in normalized:
        return "safari"
    if any(token in normalized for token in ("chrome", "chromium", "crios", "edg", "edge")):
        return "chromium"
    return "unknown"


def _select_container(item: dict[str, object]) -> str | None:
    suffix = Path(str(item["file_path"])).suffix.lower()
    if suffix in {".mp4", ".m4v"}:
        return "mp4"
    if suffix == ".mov":
        return "mov"
    if suffix == ".mkv":
        return "mkv"
    if suffix == ".webm":
        return "webm"
    if suffix == ".avi":
        return "avi"
    raw = item.get("container")
    return str(raw).lower() if raw else None


def _evaluate_direct_play(item: dict[str, object], client_profile: str) -> tuple[bool, str]:
    extension = Path(str(item["file_path"])).suffix.lower()
    container = _select_container(item)
    video_codec = normalize_codec(item.get("video_codec"))
    audio_codec = normalize_codec(item.get("audio_codec"))

    if extension not in DIRECT_PLAY_EXTENSIONS:
        return False, f"{extension or container or 'Unknown container'} is not in the direct-play safe allowlist"
    if video_codec not in SAFE_VIDEO_CODECS:
        return False, f"{video_codec or 'Unknown video codec'} is not considered browser-safe for direct play"
    if client_profile == "iphone_safari":
        if audio_codec is None:
            return False, "Missing audio metadata; choosing conservative HLS fallback for iPhone Safari"
        if audio_codec not in SAFE_AUDIO_CODECS and audio_codec is not None:
            return False, f"{audio_codec} is not direct-play safe for iPhone Safari"
        return True, "Safe direct-play profile for iPhone Safari"
    if client_profile in {"chromium", "firefox", "safari"}:
        if audio_codec not in DESKTOP_AUDIO_CODECS and audio_codec is not None:
            return False, f"{audio_codec} is not in the desktop direct-play allowlist"
        return True, "Safe direct-play profile for desktop browsers"
    if audio_codec not in SAFE_AUDIO_CODECS and audio_codec is not None:
        return False, "Unknown browser profile; choosing conservative HLS fallback"
    return True, "Conservative direct-play profile matched"


def build_playback_decision(
    settings: Settings,
    item: dict[str, object],
    *,
    user_agent: str | None,
    transcode_manager: TranscodeManager,
    force_hls: bool = False,
) -> dict[str, object]:
    client_profile = detect_client_profile(user_agent)
    source_kind = str(item.get("source_kind") or "local")
    direct_safe, reason = _evaluate_direct_play(item, client_profile)
    if force_hls:
        direct_safe = False
        reason = "Forced HLS fallback after direct playback could not be trusted"

    container = _select_container(item)
    video_codec = normalize_codec(item.get("video_codec"))
    audio_codec = normalize_codec(item.get("audio_codec"))
    if direct_safe:
        mode = "direct"
        payload = {
            "mode": mode,
            "direct_url": f"/api/stream/{int(item['id'])}",
            "hls_url": None,
            "reason": reason,
            "container": container,
            "video_codec": video_codec,
            "audio_codec": audio_codec,
            "client_profile": client_profile,
            "manifest_ready": False,
            "expected_duration_seconds": item.get("duration_seconds"),
            "generated_duration_seconds": item.get("duration_seconds"),
            "manifest_complete": True,
            "transcode_status": "not_needed",
            "transcode_enabled": settings.transcode_enabled,
            "last_error": None,
        }
    else:
        transcode_state = transcode_manager.get_job_snapshot(item)
        if source_kind != "local":
            reason = f"{reason} Cloud sources use HLS when direct browser play is unsafe."
        payload = {
            "mode": "hls",
            "direct_url": f"/api/stream/{int(item['id'])}",
            "hls_url": f"/api/hls/{int(item['id'])}/index.m3u8",
            "reason": reason,
            "container": container,
            "video_codec": video_codec,
            "audio_codec": audio_codec,
            "client_profile": client_profile,
            "manifest_ready": bool(transcode_state["manifest_ready"]),
            "expected_duration_seconds": transcode_state.get("expected_duration_seconds")
            or item.get("duration_seconds"),
            "generated_duration_seconds": transcode_state.get("generated_duration_seconds"),
            "manifest_complete": bool(transcode_state.get("manifest_complete")),
            "transcode_status": str(transcode_state["status"]),
            "transcode_enabled": bool(transcode_state["enabled"]),
            "last_error": transcode_state.get("last_error"),
        }
    logger.debug(
        "Playback decision item=%s client=%s mode=%s reason=%s",
        item["id"],
        client_profile,
        payload["mode"],
        payload["reason"],
    )
    return payload


def start_playback(
    settings: Settings,
    item: dict[str, object],
    *,
    user_id: int,
    user_agent: str | None,
    transcode_manager: TranscodeManager,
    force_hls: bool = False,
) -> dict[str, object]:
    decision = build_playback_decision(
        settings,
        item,
        user_agent=user_agent,
        transcode_manager=transcode_manager,
        force_hls=force_hls,
    )
    if decision["mode"] == "direct":
        logger.info(
            "Playback start item=%s mode=%s reason=%s",
            item["id"],
            decision["mode"],
            decision["reason"],
        )
        return decision

    source_input = None
    source_input_kind = "path"
    if str(item.get("source_kind") or "local") != "local":
        session_payload = create_native_playback_session(
            settings,
            user_id=user_id,
            item=item,
            auth_session_id=None,
            user_agent=user_agent,
            source_ip=None,
            client_name="Desktop Browser Cloud Transcode",
        )
        source_input = _rewrite_stream_url_for_server_localhost(
            settings,
            stream_url=str(session_payload["stream_url"]),
        )
        source_input_kind = "url"

    transcode_state = transcode_manager.ensure_started(
        item,
        reason="forced-hls" if force_hls else decision["reason"],
        owner_user_id=user_id,
        source_input=source_input,
        source_input_kind=source_input_kind,
    )
    decision["manifest_ready"] = bool(transcode_state["manifest_ready"])
    decision["expected_duration_seconds"] = transcode_state.get("expected_duration_seconds") or item.get(
        "duration_seconds"
    )
    decision["generated_duration_seconds"] = transcode_state.get("generated_duration_seconds")
    decision["manifest_complete"] = bool(transcode_state.get("manifest_complete"))
    decision["transcode_status"] = str(transcode_state["status"])
    decision["transcode_enabled"] = bool(transcode_state["enabled"])
    decision["last_error"] = transcode_state.get("last_error")
    logger.info(
        "Playback start item=%s mode=%s status=%s reason=%s",
        item["id"],
        decision["mode"],
        decision["transcode_status"],
        decision["reason"],
    )
    return decision


def stop_playback(
    item: dict[str, object],
    *,
    user_id: int,
    transcode_manager: TranscodeManager,
) -> bool:
    return transcode_manager.stop_item_for_owner(item, owner_user_id=user_id)


def _rewrite_stream_url_for_server_localhost(settings: Settings, *, stream_url: str) -> str:
    parsed = urlsplit(stream_url)
    if not parsed.scheme or not parsed.netloc:
        return stream_url
    host = settings.bind_host.strip()
    if host in {"", "0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return parsed._replace(netloc=f"{host}:{settings.port}").geturl()
