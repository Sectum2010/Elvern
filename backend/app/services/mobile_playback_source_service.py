from __future__ import annotations

import subprocess
from urllib.parse import urlsplit

from ..config import Settings
from ..db import get_connection, utcnow_iso
from .cloud_library_service import refresh_cloud_media_item_metadata
from .library_service import get_media_item_record
from .mobile_playback_models import MobilePlaybackSession
from .native_playback_service import close_native_playback_session, create_native_playback_session


def _coerce_duration(value: object) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _resolve_duration_seconds(
    settings: Settings,
    item: dict[str, object],
    *,
    user_id: int,
) -> tuple[float | None, dict[str, object]]:
    duration_seconds = _coerce_duration(item.get("duration_seconds"))
    if duration_seconds and duration_seconds > 0:
        return duration_seconds, item
    if str(item.get("source_kind") or "local") != "cloud":
        return duration_seconds, item
    try:
        refreshed_item = refresh_cloud_media_item_metadata(
            settings,
            item_id=int(item["id"]),
        )
    except Exception:  # noqa: BLE001
        refreshed_item = None
    if refreshed_item is not None:
        item = refreshed_item
        duration_seconds = _coerce_duration(item.get("duration_seconds"))
        if duration_seconds and duration_seconds > 0:
            return duration_seconds, item
    probed_duration = _probe_cloud_stream_duration_seconds(
        settings,
        item,
        user_id=user_id,
    )
    if probed_duration and probed_duration > 0:
        item = _persist_cloud_duration_seconds(
            settings,
            item_id=int(item["id"]),
            duration_seconds=probed_duration,
        )
        return probed_duration, item
    return duration_seconds, item


def _probe_cloud_stream_duration_seconds(
    settings: Settings,
    item: dict[str, object],
    *,
    user_id: int,
) -> float | None:
    if not settings.ffprobe_path:
        return None
    session_payload = create_native_playback_session(
        settings,
        user_id=user_id,
        item=item,
        auth_session_id=None,
        user_agent="Elvern Mobile Experimental Duration Probe",
        source_ip=None,
        client_name="Mobile Experimental Duration Probe",
    )
    stream_url = _rewrite_stream_url_for_server_localhost(
        settings,
        stream_url=str(session_payload["stream_url"]),
    )
    try:
        completed = subprocess.run(
            [
                str(settings.ffprobe_path),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                stream_url,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            close_native_playback_session(
                settings,
                session_id=str(session_payload["session_id"]),
                access_token=str(session_payload["access_token"]),
            )
        except Exception:  # noqa: BLE001
            pass
    if completed.returncode != 0:
        return None
    return _coerce_duration((completed.stdout or "").strip())


def _persist_cloud_duration_seconds(
    settings: Settings,
    *,
    item_id: int,
    duration_seconds: float,
) -> dict[str, object]:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        connection.execute(
            """
            UPDATE media_items
            SET duration_seconds = ?,
                updated_at = ?,
                last_scanned_at = ?
            WHERE id = ?
            """,
            (duration_seconds, now, now, item_id),
        )
        connection.commit()
    item = get_media_item_record(settings, item_id=item_id)
    if item is None:
        raise ValueError("Experimental playback media item is no longer available")
    return item


def _resolve_worker_source_input(
    settings: Settings,
    session: MobilePlaybackSession,
) -> tuple[str, str]:
    if session.source_input_kind == "path":
        return session.source_locator, "path"
    item = get_media_item_record(settings, item_id=session.media_item_id)
    if item is None:
        raise ValueError("Experimental playback media item is no longer available")
    session_payload = create_native_playback_session(
        settings,
        user_id=session.user_id,
        item=item,
        auth_session_id=None,
        user_agent="Elvern Mobile Experimental Playback",
        source_ip=None,
        client_name="Mobile Experimental Cloud Transcode",
    )
    return _rewrite_stream_url_for_server_localhost(
        settings,
        stream_url=str(session_payload["stream_url"]),
    ), "url"


def _rewrite_stream_url_for_server_localhost(
    settings: Settings,
    *,
    stream_url: str,
) -> str:
    parsed = urlsplit(stream_url)
    if not parsed.scheme or not parsed.netloc:
        return stream_url
    host = settings.bind_host.strip()
    if host in {"", "0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return parsed._replace(netloc=f"{host}:{settings.port}").geturl()
