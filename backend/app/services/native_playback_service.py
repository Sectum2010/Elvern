from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from urllib.parse import quote, urlsplit

from fastapi import HTTPException, status

from ..config import Settings
from ..db import get_connection, utcnow_iso
from ..media_stream import build_stream_response, ensure_media_path_within_root
from ..progress import record_playback_event, save_progress
from ..security import generate_session_token, hash_session_token
from .cloud_library_service import build_cloud_stream_response, refresh_cloud_media_item_metadata
from .library_service import get_media_item_record


logger = logging.getLogger(__name__)

EXTERNAL_STREAM_INITIAL_PROGRESS_SECONDS = 5.0
EXTERNAL_STREAM_MIN_INCREMENT_SECONDS = 15.0
EXTERNAL_STREAM_MAX_INCREMENT_SECONDS = 30.0
EXTERNAL_STREAM_SIGNIFICANT_POSITION_DELTA_SECONDS = 15.0
EXTERNAL_STREAM_RANGE_PATTERN = re.compile(r"bytes=(\d+)-(\d*)")
EXTERNAL_STREAM_NEAR_END_RANGE_GUARD_SECONDS = 120.0
LINUX_SAME_HOST_VLC_DIRECT_PROGRESS_FRESH_SECONDS = 9.0
IOS_VLC_CLOUD_CONSERVATIVE_RANGE_BACKOFF_SECONDS = 12.0
DEFAULT_STREAM_VALIDATION_INTERVAL_SECONDS = 0.25
ACTIVE_STREAM_TTL_REFRESH_SECONDS = 30.0
EXTERNAL_PLAYER_STREAM_VALIDATION_INTERVAL_SECONDS = 5.0
EXTERNAL_PLAYER_ACTIVE_STREAM_TTL_REFRESH_SECONDS = 60.0
EXTERNAL_PLAYER_LOCAL_FILE_CHUNK_SIZE_BYTES = 2 * 1024 * 1024
EXTERNAL_PLAYER_CLOUD_PROXY_CHUNK_SIZE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class NativePlaybackStreamPolicy:
    external_player: bool
    session_ttl_seconds: int
    validation_interval_seconds: float
    ttl_refresh_interval_seconds: float
    chunk_size_bytes: int


def inspect_native_playback_access(
    settings: Settings,
    *,
    session_id: str,
    access_token: str | None,
) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    payload: dict[str, object] = {
        "session_id": session_id,
        "reason": "unknown",
        "allowed": False,
    }
    if not session_id:
        payload["reason"] = "session_lookup_miss"
        return payload
    if not access_token:
        payload["reason"] = "missing_access_token"
        return payload

    token_hash = hash_session_token(access_token, settings.session_secret)
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT
                n.session_id,
                n.access_token_hash,
                n.user_id,
                n.media_item_id,
                n.auth_session_id,
                n.client_name,
                n.created_at,
                n.expires_at,
                n.last_seen_at,
                n.closed_at,
                n.revoked_at,
                u.enabled AS user_enabled,
                s.id AS auth_session_found,
                s.expires_at AS auth_session_expires_at,
                s.revoked_at AS auth_session_revoked_at
            FROM native_playback_sessions n
            JOIN users u ON u.id = n.user_id
            LEFT JOIN sessions s ON s.id = n.auth_session_id
            WHERE n.session_id = ?
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()

    if row is None:
        payload["reason"] = "session_lookup_miss"
        return payload

    payload.update(
        {
            "user_id": int(row["user_id"]),
            "media_item_id": int(row["media_item_id"]),
            "auth_session_id": row["auth_session_id"],
            "client_name": row["client_name"],
            "native_created_at": row["created_at"],
            "native_expires_at": row["expires_at"],
            "native_last_seen_at": row["last_seen_at"],
            "native_closed_at": row["closed_at"],
            "native_revoked_at": row["revoked_at"],
            "auth_session_expires_at": row["auth_session_expires_at"],
            "auth_session_revoked_at": row["auth_session_revoked_at"],
        }
    )

    if str(row["access_token_hash"]) != token_hash:
        payload["reason"] = "token_mismatch"
        return payload
    if row["closed_at"] is not None:
        payload["reason"] = "native_session_closed"
        return payload
    if row["revoked_at"] is not None:
        payload["reason"] = "native_session_revoked"
        return payload
    if str(row["expires_at"]) <= now_iso:
        payload["reason"] = "native_session_expired"
        return payload
    if not bool(row["user_enabled"]):
        payload["reason"] = "user_disabled"
        return payload
    if row["auth_session_id"] is not None:
        if row["auth_session_found"] is None:
            payload["reason"] = "auth_session_lookup_miss"
            return payload
        if row["auth_session_revoked_at"] is not None:
            payload["reason"] = "auth_session_revoked"
            return payload
        if str(row["auth_session_expires_at"] or "") <= now_iso:
            payload["reason"] = "auth_session_expired"
            return payload

    payload["reason"] = "allowed"
    payload["allowed"] = True
    return payload


def _coerce_duration_seconds(value: object) -> float | None:
    if value in {None, ""}:
        return None
    try:
        resolved = round(float(value), 2)
    except (TypeError, ValueError):
        return None
    return resolved if resolved > 0 else None


def _merge_item_payload(
    original_item: dict[str, object],
    updated_item: dict[str, object] | None,
) -> dict[str, object]:
    if updated_item is None:
        return dict(original_item)
    merged_item = dict(original_item)
    merged_item.update(updated_item)
    return merged_item


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
    return get_media_item_record(settings, item_id=item_id) or {"id": item_id, "duration_seconds": duration_seconds}


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


def _probe_cloud_stream_duration_seconds_for_native_playback(
    settings: Settings,
    *,
    user_id: int,
    item: dict[str, object],
) -> float | None:
    if not settings.ffprobe_path:
        return None
    session_payload = create_native_playback_session(
        settings,
        user_id=user_id,
        item=item,
        auth_session_id=None,
        user_agent="Elvern iOS VLC Duration Probe",
        source_ip=None,
        client_name="iOS VLC Duration Probe",
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
    return _coerce_duration_seconds((completed.stdout or "").strip())


def _should_ensure_ios_vlc_cloud_duration(client_name: object, item: dict[str, object]) -> bool:
    normalized_client_name = str(client_name or "").strip().lower()
    if not normalized_client_name.startswith("elvern ios vlc handoff"):
        return False
    if str(item.get("source_kind") or "local") != "cloud":
        return False
    return _coerce_duration_seconds(item.get("duration_seconds")) is None


def _content_disposition_inline_filename(value: object) -> str | None:
    filename = str(value or "").strip().replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not filename:
        return None
    filename = filename.replace("\r", "").replace("\n", "")
    filename = filename.encode("latin-1", "replace").decode("latin-1")
    filename = filename.replace("\\", "\\\\").replace('"', '\\"')
    return f'inline; filename="{filename}"'


def _ensure_ios_vlc_cloud_duration(
    settings: Settings,
    *,
    user_id: int,
    item: dict[str, object],
) -> dict[str, object]:
    duration_seconds = _coerce_duration_seconds(item.get("duration_seconds"))
    if duration_seconds is not None:
        return item

    refreshed_item = None
    try:
        refreshed_item = refresh_cloud_media_item_metadata(
            settings,
            item_id=int(item["id"]),
        )
    except Exception:  # noqa: BLE001
        refreshed_item = None

    merged_item = _merge_item_payload(item, refreshed_item)
    duration_seconds = _coerce_duration_seconds(merged_item.get("duration_seconds"))
    if duration_seconds is not None:
        return merged_item

    probed_duration = _probe_cloud_stream_duration_seconds_for_native_playback(
        settings,
        user_id=user_id,
        item=merged_item,
    )
    if probed_duration is None:
        return merged_item

    persisted_item = _persist_cloud_duration_seconds(
        settings,
        item_id=int(merged_item["id"]),
        duration_seconds=probed_duration,
    )
    return _merge_item_payload(merged_item, persisted_item)


def cleanup_native_playback_sessions(settings: Settings) -> None:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        connection.execute(
            """
            DELETE FROM native_playback_sessions
            WHERE expires_at <= ?
               OR closed_at IS NOT NULL
               OR revoked_at IS NOT NULL
            """,
            (now,),
        )
        connection.commit()


def resolve_native_playback_session_client_name(
    *,
    client_name: str | None,
    external_player: object | None = None,
) -> str | None:
    normalized_external_player = str(external_player or "").strip().lower()
    normalized_client_name = str(client_name or "").strip()
    if normalized_external_player not in {"vlc", "infuse"}:
        return normalized_client_name or None
    canonical = "Elvern iOS VLC Handoff" if normalized_external_player == "vlc" else "Elvern iOS Infuse Handoff"
    if not normalized_client_name:
        return canonical
    lowered = normalized_client_name.lower()
    if lowered.startswith(canonical.lower()):
        return normalized_client_name
    return f"{canonical} - {normalized_client_name}"


def should_decouple_external_player_auth_session(
    *,
    client_name: object | None = None,
    external_player: object | None = None,
) -> bool:
    normalized_external_player = str(external_player or "").strip().lower()
    if normalized_external_player in {"vlc", "infuse"}:
        return True
    return _is_ios_external_player_backend_stream_client(client_name)


def _is_ios_external_player_backend_stream_client(client_name: object | None) -> bool:
    normalized = str(client_name or "").strip().lower()
    return normalized.startswith("elvern ios vlc handoff") or normalized.startswith("elvern ios infuse handoff")


def _uses_external_player_backend_stream(client_name: object | None) -> bool:
    normalized = str(client_name or "").strip().lower()
    if _is_ios_external_player_backend_stream_client(normalized):
        return True
    return normalized.startswith("vlc helper fallback") or normalized.startswith("vlc playlist fallback")


def _build_native_playback_stream_policy(
    settings: Settings,
    *,
    client_name: object | None,
    stream_path_class: str,
) -> NativePlaybackStreamPolicy:
    external_player = _uses_external_player_backend_stream(client_name)
    if external_player:
        chunk_size_bytes = (
            EXTERNAL_PLAYER_CLOUD_PROXY_CHUNK_SIZE_BYTES
            if stream_path_class == "cloud_proxy"
            else EXTERNAL_PLAYER_LOCAL_FILE_CHUNK_SIZE_BYTES
        )
        return NativePlaybackStreamPolicy(
            external_player=True,
            session_ttl_seconds=settings.external_player_stream_ttl_seconds,
            validation_interval_seconds=EXTERNAL_PLAYER_STREAM_VALIDATION_INTERVAL_SECONDS,
            ttl_refresh_interval_seconds=EXTERNAL_PLAYER_ACTIVE_STREAM_TTL_REFRESH_SECONDS,
            chunk_size_bytes=chunk_size_bytes,
        )
    return NativePlaybackStreamPolicy(
        external_player=False,
        session_ttl_seconds=settings.playback_token_ttl_seconds,
        validation_interval_seconds=DEFAULT_STREAM_VALIDATION_INTERVAL_SECONDS,
        ttl_refresh_interval_seconds=ACTIVE_STREAM_TTL_REFRESH_SECONDS,
        chunk_size_bytes=64 * 1024,
    )


def create_native_playback_session(
    settings: Settings,
    *,
    user_id: int,
    item: dict[str, object],
    auth_session_id: int | None,
    user_agent: str | None,
    source_ip: str | None,
    client_name: str | None,
) -> dict[str, object]:
    if not settings.native_playback_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Native playback sessions are disabled",
        )

    item_payload = dict(item)
    if _should_ensure_ios_vlc_cloud_duration(client_name, item_payload):
        item_payload = _ensure_ios_vlc_cloud_duration(
            settings,
            user_id=user_id,
            item=item_payload,
        )

    cleanup_native_playback_sessions(settings)

    session_id = generate_session_token()
    access_token = generate_session_token()
    access_token_hash = hash_session_token(access_token, settings.session_secret)
    now = datetime.now(timezone.utc)
    expires_at = _session_expiry(settings, now, client_name=client_name)
    now_iso = now.isoformat()
    expires_at_iso = expires_at.isoformat()
    resume_seconds = float(item_payload.get("resume_position_seconds") or 0)

    with get_connection(settings) as connection:
        connection.execute(
            """
            INSERT INTO native_playback_sessions (
                session_id,
                access_token_hash,
                user_id,
                media_item_id,
                auth_session_id,
                created_at,
                expires_at,
                last_seen_at,
                client_name,
                user_agent,
                source_ip,
                last_position_seconds,
                last_duration_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                access_token_hash,
                user_id,
                int(item_payload["id"]),
                auth_session_id,
                now_iso,
                expires_at_iso,
                now_iso,
                client_name,
                user_agent,
                source_ip,
                resume_seconds,
                item_payload.get("duration_seconds"),
            ),
        )
        connection.commit()

    logger.info(
        "Created native playback session item=%s user=%s client=%s expires_at=%s",
        item_payload["id"],
        user_id,
        client_name or "unknown",
        expires_at_iso,
    )
    return _build_session_payload(
        settings,
        session_id=session_id,
        access_token=access_token,
        row={
            "session_id": session_id,
            "media_item_id": int(item_payload["id"]),
            "title": item_payload.get("title"),
            "file_path": item_payload.get("file_path"),
            "source_kind": item_payload.get("source_kind"),
            "duration_seconds": item_payload.get("duration_seconds"),
            "container": item_payload.get("container"),
            "video_codec": item_payload.get("video_codec"),
            "audio_codec": item_payload.get("audio_codec"),
            "expires_at": expires_at_iso,
            "resume_seconds": resume_seconds,
            "subtitles": item_payload.get("subtitles") or [],
        },
        include_access_token=True,
    )


def get_native_playback_session_payload(
    settings: Settings,
    *,
    session_id: str,
    access_token: str,
    extend_ttl: bool = False,
) -> dict[str, object]:
    row = _require_native_session(
        settings,
        session_id=session_id,
        access_token=access_token,
        extend_ttl=extend_ttl,
    )
    return _build_session_payload(
        settings,
        session_id=session_id,
        access_token=access_token,
        row=row,
        include_access_token=False,
    )


def heartbeat_native_playback_session(
    settings: Settings,
    *,
    session_id: str,
    access_token: str,
) -> dict[str, str]:
    row = _require_native_session(
        settings,
        session_id=session_id,
        access_token=access_token,
        extend_ttl=True,
    )
    logger.debug("Native playback heartbeat session=%s item=%s", session_id, row["media_item_id"])
    return {
        "message": "Native playback session renewed",
        "expires_at": str(row["expires_at"]),
    }


def save_native_playback_session_progress(
    settings: Settings,
    *,
    session_id: str,
    access_token: str,
    position_seconds: float,
    duration_seconds: float | None,
    completed: bool,
) -> dict[str, object]:
    row = _require_native_session(
        settings,
        session_id=session_id,
        access_token=access_token,
        extend_ttl=True,
    )
    playback_mode = _normalize_native_playback_mode(row.get("client_name"))
    saved = save_progress(
        settings,
        user_id=int(row["user_id"]),
        media_item_id=int(row["media_item_id"]),
        position_seconds=position_seconds,
        duration_seconds=duration_seconds,
        completed=completed,
        playback_mode=playback_mode,
        event_type="playback_progress",
        native_session_id=session_id,
    )
    _update_native_session_snapshot(
        settings,
        session_id=session_id,
        position_seconds=saved["position_seconds"],
        duration_seconds=saved["duration_seconds"],
        extend_ttl=True,
        client_name=row.get("client_name"),
    )
    return saved


def close_native_playback_session(
    settings: Settings,
    *,
    session_id: str,
    access_token: str,
    position_seconds: float | None = None,
    duration_seconds: float | None = None,
    completed: bool = False,
) -> None:
    row = _require_native_session(
        settings,
        session_id=session_id,
        access_token=access_token,
        extend_ttl=False,
    )
    playback_mode = _normalize_native_playback_mode(row.get("client_name"))
    if position_seconds is not None:
        save_progress(
            settings,
            user_id=int(row["user_id"]),
            media_item_id=int(row["media_item_id"]),
            position_seconds=position_seconds,
            duration_seconds=duration_seconds,
            completed=completed,
            playback_mode=playback_mode,
            event_type="playback_completed" if completed else "playback_stopped",
            native_session_id=session_id,
        )
    closed_at = utcnow_iso()
    with get_connection(settings) as connection:
        connection.execute(
            """
            UPDATE native_playback_sessions
            SET closed_at = ?, last_position_seconds = COALESCE(?, last_position_seconds), last_duration_seconds = COALESCE(?, last_duration_seconds), last_progress_recorded_at = ?
            WHERE session_id = ?
            """,
            (closed_at, position_seconds, duration_seconds, closed_at, session_id),
        )
        connection.commit()
    logger.info(
        "Closed native playback session=%s item=%s completed=%s",
        session_id,
        row["media_item_id"],
        completed,
    )


def record_native_playback_session_event(
    settings: Settings,
    *,
    session_id: str,
    access_token: str,
    event_type: str,
    position_seconds: float | None = None,
    duration_seconds: float | None = None,
    occurred_at: str | None = None,
) -> None:
    row = _require_native_session(
        settings,
        session_id=session_id,
        access_token=access_token,
        extend_ttl=True,
    )
    playback_mode = _normalize_native_playback_mode(row.get("client_name"))
    if event_type == "playback_opened":
        record_playback_event(
            settings,
            user_id=int(row["user_id"]),
            media_item_id=int(row["media_item_id"]),
            event_type=event_type,
            playback_mode=playback_mode,
            position_seconds=position_seconds,
            duration_seconds=duration_seconds,
            occurred_at=occurred_at,
            native_session_id=session_id,
        )
        return

    saved = save_progress(
        settings,
        user_id=int(row["user_id"]),
        media_item_id=int(row["media_item_id"]),
        position_seconds=float(position_seconds or 0),
        duration_seconds=duration_seconds,
        completed=event_type == "playback_completed",
        playback_mode=playback_mode,
        event_type=event_type,
        occurred_at=occurred_at,
        native_session_id=session_id,
        count_watch_increment=event_type != "playback_seeked",
    )
    _update_native_session_snapshot(
        settings,
        session_id=session_id,
        position_seconds=saved["position_seconds"],
        duration_seconds=saved["duration_seconds"],
        extend_ttl=True,
        client_name=row.get("client_name"),
    )


def build_native_stream_response(
    settings: Settings,
    *,
    session_id: str,
    access_token: str,
    range_header: str | None,
    record_activity: bool = True,
):
    row = _require_native_session(
        settings,
        session_id=session_id,
        access_token=access_token,
        extend_ttl=True,
    )
    stream_path_class = "cloud_proxy" if str(row.get("source_kind") or "local") == "cloud" else "local_file"
    stream_policy = _build_native_playback_stream_policy(
        settings,
        client_name=row.get("client_name"),
        stream_path_class=stream_path_class,
    )
    stream_validator = _build_native_stream_validator(
        settings,
        session_id=session_id,
        access_token=access_token,
        client_name=row.get("client_name"),
        validation_interval_seconds=stream_policy.validation_interval_seconds,
        ttl_refresh_interval_seconds=stream_policy.ttl_refresh_interval_seconds,
    )
    target = build_cloud_stream_response(
        settings,
        user_id=int(row["user_id"]),
        item_id=int(row["media_item_id"]),
        range_header=range_header,
        stream_validator=stream_validator,
        validated_chunk_size=(
            stream_policy.chunk_size_bytes
            if stream_path_class == "cloud_proxy"
            else None
        ),
    )
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media item not found")
    if isinstance(target, dict):
        file_path = ensure_media_path_within_root(Path(str(target["file_path"])), settings)
        response = build_stream_response(
            str(file_path),
            settings,
            range_header,
            validated_chunk_size=stream_policy.chunk_size_bytes,
            stream_validator=stream_validator,
        )
    else:
        response = target
    if _normalize_native_playback_mode(row.get("client_name")) == "infuse_external":
        content_disposition = _content_disposition_inline_filename(row.get("original_filename"))
        if content_disposition:
            response.headers["Content-Disposition"] = content_disposition
    setattr(
        response,
        "_elvern_native_stream_context",
        {
            "session_id": session_id,
            "item_id": int(row["media_item_id"]),
            "client_name": row.get("client_name"),
            "source_kind": str(row.get("source_kind") or "local"),
            "stream_path_class": stream_path_class,
            "external_player": stream_policy.external_player,
            "validation_interval_seconds": stream_policy.validation_interval_seconds,
            "ttl_refresh_interval_seconds": stream_policy.ttl_refresh_interval_seconds,
            "chunk_size_bytes": stream_policy.chunk_size_bytes,
            "auth_session_coupled": row.get("auth_session_id") is not None,
            "session_ttl_seconds": stream_policy.session_ttl_seconds,
            "file_size": row.get("file_size"),
            "duration_seconds": row.get("duration_seconds"),
            "container": row.get("container"),
            "video_codec": row.get("video_codec"),
            "audio_codec": row.get("audio_codec"),
            "original_filename": row.get("original_filename"),
        },
    )
    if record_activity:
        _record_external_stream_activity(
            settings,
            row=row,
            requested_range_header=range_header,
        )
    return response


def get_native_playback_status(settings: Settings) -> dict[str, object]:
    cleanup_native_playback_sessions(settings)
    now = utcnow_iso()
    with get_connection(settings) as connection:
        active_sessions = connection.execute(
            """
            SELECT COUNT(*)
            FROM native_playback_sessions
            WHERE closed_at IS NULL AND expires_at > ?
            """,
            (now,),
        ).fetchone()[0]
    return {
        "enabled": settings.native_playback_enabled,
        "protocol": settings.native_player_protocol,
        "session_ttl_minutes": settings.native_playback_session_minutes,
        "token_ttl_seconds": settings.playback_token_ttl_seconds,
        "active_sessions": int(active_sessions),
    }


def _require_native_session(
    settings: Settings,
    *,
    session_id: str,
    access_token: str,
    extend_ttl: bool,
):
    if not session_id or not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Native playback session token is required",
        )

    cleanup_native_playback_sessions(settings)

    now = datetime.now(timezone.utc)
    token_hash = hash_session_token(access_token, settings.session_secret)
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT
                n.session_id,
                n.user_id,
                n.media_item_id,
                n.created_at,
                n.expires_at,
                n.last_seen_at,
                n.last_progress_recorded_at,
                n.last_position_seconds,
                n.last_duration_seconds,
                n.auth_session_id,
                n.client_name,
                m.title,
                m.original_filename,
                m.file_path,
                m.file_size,
                COALESCE(m.source_kind, 'local') AS source_kind,
                m.external_media_id,
                m.cloud_mime_type,
                m.duration_seconds,
                m.container,
                m.video_codec,
                m.audio_codec
            FROM native_playback_sessions n
            JOIN media_items m ON m.id = n.media_item_id
            LEFT JOIN sessions s ON s.id = n.auth_session_id
            JOIN users u ON u.id = n.user_id
            WHERE n.session_id = ?
              AND n.access_token_hash = ?
              AND n.closed_at IS NULL
              AND n.revoked_at IS NULL
              AND n.expires_at > ?
              AND u.enabled = 1
              AND (n.auth_session_id IS NULL OR (
                    s.revoked_at IS NULL
                AND s.expires_at > ?
              ))
            LIMIT 1
            """,
            (session_id, token_hash, now.isoformat(), now.isoformat()),
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Native playback session is invalid or has expired",
            )

        current_expires = row["expires_at"]
        if extend_ttl:
            current_expires = _session_expiry(settings, now, client_name=row["client_name"]).isoformat()
            connection.execute(
                """
                UPDATE native_playback_sessions
                SET last_seen_at = ?, expires_at = ?
                WHERE session_id = ?
                """,
                (now.isoformat(), current_expires, session_id),
            )
            connection.commit()

    payload = dict(row)
    payload["expires_at"] = current_expires
    payload["resume_seconds"] = float(row["last_position_seconds"] or 0)
    return payload


def _build_native_stream_validator(
    settings: Settings,
    *,
    session_id: str,
    access_token: str,
    client_name: object | None,
    validation_interval_seconds: float,
    ttl_refresh_interval_seconds: float,
):
    next_check_at = 0.0
    next_ttl_refresh_at = 0.0
    stream_open = True

    def validator() -> bool:
        nonlocal next_check_at, next_ttl_refresh_at, stream_open
        if not stream_open:
            return False
        now_monotonic = monotonic()
        if now_monotonic < next_check_at:
            return True
        next_check_at = now_monotonic + validation_interval_seconds
        stream_open = _native_stream_access_still_valid(
            settings,
            session_id=session_id,
            access_token=access_token,
            extend_ttl=now_monotonic >= next_ttl_refresh_at,
        )
        if stream_open and now_monotonic >= next_ttl_refresh_at:
            next_ttl_refresh_at = now_monotonic + ttl_refresh_interval_seconds
        if not stream_open:
            logger.info(
                "Stopping native playback stream session=%s client=%s because access is no longer valid",
                session_id,
                client_name or "unknown",
            )
        return stream_open

    return validator


def _native_stream_access_still_valid(
    settings: Settings,
    *,
    session_id: str,
    access_token: str,
    extend_ttl: bool = False,
) -> bool:
    token_hash = hash_session_token(access_token, settings.session_secret)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT n.client_name
            FROM native_playback_sessions n
            LEFT JOIN sessions s ON s.id = n.auth_session_id
            JOIN users u ON u.id = n.user_id
            WHERE n.session_id = ?
              AND n.access_token_hash = ?
              AND n.closed_at IS NULL
              AND n.revoked_at IS NULL
              AND n.expires_at > ?
              AND u.enabled = 1
              AND (n.auth_session_id IS NULL OR (
                    s.revoked_at IS NULL
                AND s.expires_at > ?
              ))
            LIMIT 1
            """,
            (session_id, token_hash, now_iso, now_iso),
        ).fetchone()
        if row is not None and extend_ttl:
            connection.execute(
                """
                UPDATE native_playback_sessions
                SET last_seen_at = ?, expires_at = ?
                WHERE session_id = ?
                  AND access_token_hash = ?
                  AND closed_at IS NULL
                  AND revoked_at IS NULL
                """,
                (
                    now_iso,
                    _session_expiry(settings, now, client_name=row["client_name"]).isoformat(),
                    session_id,
                    token_hash,
                ),
            )
            connection.commit()
    return row is not None


def _session_expiry(
    settings: Settings,
    now: datetime | None = None,
    *,
    client_name: object | None = None,
) -> datetime:
    current = now or datetime.now(timezone.utc)
    ttl_seconds = (
        settings.external_player_stream_ttl_seconds
        if _uses_external_player_backend_stream(client_name)
        else settings.playback_token_ttl_seconds
    )
    return current + timedelta(seconds=ttl_seconds)


def _normalize_native_playback_mode(client_name: object) -> str:
    normalized = str(client_name or "").strip().lower()
    if not normalized:
        return "native_playback"
    if "desktop web handoff" in normalized or normalized == "desktop_player":
        return "desktop_player"
    if normalized.startswith("linux same-host vlc") or normalized.startswith("vlc helper fallback") or normalized.startswith("vlc playlist fallback"):
        return "vlc_external"
    if "infuse handoff" in normalized:
        return "infuse_external"
    if "ios vlc handoff" in normalized:
        return "vlc_external"
    return normalized.replace(" ", "_").replace("-", "_")


def _update_native_session_snapshot(
    settings: Settings,
    *,
    session_id: str,
    position_seconds: float | None,
    duration_seconds: float | None,
    extend_ttl: bool,
    client_name: object | None = None,
    last_progress_recorded_at: str | None = None,
) -> None:
    now_iso = utcnow_iso()
    expires_at = _session_expiry(settings, client_name=client_name).isoformat() if extend_ttl else None
    with get_connection(settings) as connection:
        if extend_ttl:
            connection.execute(
                """
                UPDATE native_playback_sessions
                SET
                    last_position_seconds = COALESCE(?, last_position_seconds),
                    last_duration_seconds = COALESCE(?, last_duration_seconds),
                    last_seen_at = ?,
                    expires_at = ?,
                    last_progress_recorded_at = COALESCE(?, last_progress_recorded_at)
                WHERE session_id = ?
                """,
                (
                    position_seconds,
                    duration_seconds,
                    now_iso,
                    expires_at,
                    last_progress_recorded_at or now_iso,
                    session_id,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE native_playback_sessions
                SET
                    last_position_seconds = COALESCE(?, last_position_seconds),
                    last_duration_seconds = COALESCE(?, last_duration_seconds),
                    last_progress_recorded_at = COALESCE(?, last_progress_recorded_at)
                WHERE session_id = ?
                """,
                (
                    position_seconds,
                    duration_seconds,
                    last_progress_recorded_at,
                    session_id,
                ),
            )
        connection.commit()


def _build_session_payload(
    settings: Settings,
    *,
    session_id: str,
    access_token: str,
    row: dict[str, object],
    include_access_token: bool,
) -> dict[str, object]:
    api_origin = _native_api_origin(settings)
    stream_url, details_url, heartbeat_url, progress_url, event_url, close_url = _build_session_urls(
        api_origin=api_origin,
        session_id=session_id,
        access_token=access_token,
    )
    audio_tracks: list[dict[str, object]] = []
    subtitle_tracks: list[dict[str, object]] = []
    if str(row.get("source_kind") or "local") == "local":
        audio_tracks, subtitle_tracks = _probe_tracks(
            Path(str(row["file_path"])),
            settings,
        )
    if not subtitle_tracks and row.get("subtitles"):
        subtitle_tracks = [
            {
                "index": int(index),
                "codec": subtitle.get("codec"),
                "language": subtitle.get("language"),
                "title": subtitle.get("title"),
                "channels": None,
                "disposition_default": bool(subtitle.get("disposition_default")),
            }
            for index, subtitle in enumerate(row.get("subtitles") or [], start=1)
        ]
    payload = {
        "session_id": session_id,
        "access_token": access_token if include_access_token else None,
        "api_origin": api_origin,
        "details_url": details_url,
        "stream_url": stream_url,
        "heartbeat_url": heartbeat_url,
        "progress_url": progress_url,
        "event_url": event_url,
        "close_url": close_url,
        "expires_at": str(row["expires_at"]),
        "title": str(row["title"]),
        "media_id": int(row["media_item_id"]),
        "duration_seconds": row.get("duration_seconds"),
        "resume_seconds": float(row.get("resume_seconds") or 0),
        "subtitle_tracks": subtitle_tracks,
        "audio_tracks": audio_tracks,
        "container": row.get("container"),
        "video_codec": row.get("video_codec"),
        "audio_codec": row.get("audio_codec"),
        "native_player_protocol": settings.native_player_protocol,
        "session_api_version": 1,
    }
    logger.info(
        "Built native playback session payload session=%s media=%s api_origin=%s details_url=%s stream_url=%s",
        session_id,
        row["media_item_id"],
        api_origin,
        details_url,
        stream_url,
    )
    return payload


def _build_session_urls(
    *,
    api_origin: str,
    session_id: str,
    access_token: str,
) -> tuple[str, str, str, str, str, str]:
    escaped_token = quote(access_token, safe="")
    base_path = f"/api/native-playback/session/{session_id}"
    query = f"?token={escaped_token}"
    base_url = f"{api_origin}{base_path}"
    return (
        f"{base_url}/stream{query}",
        f"{base_url}{query}",
        f"{base_url}/heartbeat{query}",
        f"{base_url}/progress{query}",
        f"{base_url}/event{query}",
        f"{base_url}/close{query}",
    )


def _parse_utc_iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _tracks_external_stream_activity(client_name: object) -> bool:
    normalized = str(client_name or "").strip().lower()
    if not normalized:
        return False
    return (
        normalized == "linux same-host vlc"
        or normalized.startswith("vlc helper fallback")
        or normalized.startswith("vlc playlist fallback")
        or normalized.startswith("elvern ios vlc handoff")
        or normalized.startswith("elvern ios infuse handoff")
    )


def _is_ios_vlc_cloud_fallback(row: dict[str, object]) -> bool:
    normalized = str(row.get("client_name") or "").strip().lower()
    if not normalized.startswith("elvern ios vlc handoff"):
        return False
    return str(row.get("source_kind") or "local") == "cloud"


def _apply_conservative_ios_vlc_cloud_range_estimate(
    *,
    estimated_position: float,
    current_position: float,
    duration_seconds: float | None,
) -> float:
    backoff_seconds = min(
        IOS_VLC_CLOUD_CONSERVATIVE_RANGE_BACKOFF_SECONDS,
        max(estimated_position * 0.01, 5.0),
    )
    conservative_position = max(estimated_position - backoff_seconds, 0.0)
    conservative_position = max(current_position, conservative_position)
    if duration_seconds is not None:
        conservative_position = min(conservative_position, duration_seconds)
    return round(conservative_position, 2)


def _ios_vlc_cloud_completion_guard_seconds(duration_seconds: float) -> float:
    return max(duration_seconds * 0.03, 30.0) + 1.0


def _should_ignore_external_range_seek_estimate(
    *,
    row: dict[str, object],
    now: datetime,
    estimated_position: float,
    duration_seconds: float | None,
) -> bool:
    if duration_seconds is None or duration_seconds <= 0:
        return False
    remaining_seconds = max(duration_seconds - estimated_position, 0.0)
    near_end_guard_seconds = _ios_vlc_cloud_completion_guard_seconds(duration_seconds)
    if remaining_seconds > near_end_guard_seconds:
        return False
    created_at = _parse_utc_iso(row.get("created_at"))
    if created_at is None:
        return True
    return (now - created_at).total_seconds() <= EXTERNAL_STREAM_NEAR_END_RANGE_GUARD_SECONDS


def _record_external_stream_activity(
    settings: Settings,
    *,
    row: dict[str, object],
    requested_range_header: str | None,
) -> None:
    if not _tracks_external_stream_activity(row.get("client_name")):
        return

    now = datetime.now(timezone.utc)
    last_progress_recorded_at = _parse_utc_iso(row.get("last_progress_recorded_at"))
    normalized_client_name = str(row.get("client_name") or "").strip().lower()
    if (
        normalized_client_name == "linux same-host vlc"
        and last_progress_recorded_at is not None
        and (now - last_progress_recorded_at).total_seconds() <= LINUX_SAME_HOST_VLC_DIRECT_PROGRESS_FRESH_SECONDS
    ):
        return
    current_position = max(float(row.get("last_position_seconds") or 0.0), 0.0)
    duration_seconds = row.get("last_duration_seconds")
    if duration_seconds is None:
        duration_seconds = row.get("duration_seconds")

    resolved_duration = None
    try:
        if duration_seconds is not None:
            resolved_duration = float(duration_seconds)
    except (TypeError, ValueError):
        resolved_duration = None

    ios_vlc_cloud_fallback = _is_ios_vlc_cloud_fallback(row)

    increment_seconds = 0.0
    if last_progress_recorded_at is None:
        increment_seconds = EXTERNAL_STREAM_INITIAL_PROGRESS_SECONDS
    else:
        elapsed_seconds = max((now - last_progress_recorded_at).total_seconds(), 0.0)
        if elapsed_seconds >= EXTERNAL_STREAM_MIN_INCREMENT_SECONDS:
            increment_seconds = min(elapsed_seconds, EXTERNAL_STREAM_MAX_INCREMENT_SECONDS)

    estimated_range_position = _estimate_external_stream_position_from_range(
        requested_range_header=requested_range_header,
        file_size=row.get("file_size"),
        duration_seconds=resolved_duration,
    )
    if estimated_range_position is not None and ios_vlc_cloud_fallback:
        estimated_range_position = _apply_conservative_ios_vlc_cloud_range_estimate(
            estimated_position=estimated_range_position,
            current_position=current_position,
            duration_seconds=resolved_duration,
        )
    if (
        estimated_range_position is not None
        and _should_ignore_external_range_seek_estimate(
            row=row,
            now=now,
            estimated_position=estimated_range_position,
            duration_seconds=resolved_duration,
        )
    ):
        estimated_range_position = None
    significant_position_change = (
        estimated_range_position is not None
        and estimated_range_position - current_position >= EXTERNAL_STREAM_SIGNIFICANT_POSITION_DELTA_SECONDS
    )
    if (
        increment_seconds <= 0
        and estimated_range_position is None
        and not significant_position_change
    ):
        return

    next_position = current_position
    event_type = "playback_progress"
    count_watch_increment = True
    if significant_position_change and estimated_range_position is not None:
        next_position = max(next_position, estimated_range_position)
        event_type = "playback_seeked"
        count_watch_increment = False
    if increment_seconds > 0:
        next_position = max(next_position, current_position + increment_seconds)
    if ios_vlc_cloud_fallback and resolved_duration is not None:
        max_fallback_position = max(
            resolved_duration - _ios_vlc_cloud_completion_guard_seconds(resolved_duration),
            0.0,
        )
        next_position = min(next_position, max_fallback_position)
    if resolved_duration is not None:
        next_position = min(next_position, resolved_duration)
    next_position = round(max(next_position, 0.0), 2)
    if next_position == current_position and not significant_position_change and increment_seconds <= 0:
        return

    saved = save_progress(
        settings,
        user_id=int(row["user_id"]),
        media_item_id=int(row["media_item_id"]),
        position_seconds=next_position,
        duration_seconds=resolved_duration,
        completed=False,
        playback_mode=_normalize_native_playback_mode(row.get("client_name")),
        event_type=event_type,
        native_session_id=str(row["session_id"]),
        tracking_source="inferred",
        count_watch_increment=count_watch_increment,
    )
    _update_native_session_snapshot(
        settings,
        session_id=str(row["session_id"]),
        position_seconds=saved["position_seconds"],
        duration_seconds=saved["duration_seconds"],
        extend_ttl=False,
        last_progress_recorded_at=now.isoformat(),
    )


def _estimate_external_stream_position_from_range(
    *,
    requested_range_header: str | None,
    file_size: object,
    duration_seconds: float | None,
) -> float | None:
    if not requested_range_header or duration_seconds is None or duration_seconds <= 0:
        return None
    try:
        resolved_file_size = int(file_size or 0)
    except (TypeError, ValueError):
        return None
    if resolved_file_size <= 0:
        return None

    match = EXTERNAL_STREAM_RANGE_PATTERN.fullmatch(requested_range_header.strip())
    if match is None:
        return None
    try:
        start_byte = int(match.group(1))
    except (TypeError, ValueError):
        return None
    if start_byte < 0:
        return None
    clamped_start_byte = min(start_byte, max(resolved_file_size - 1, 0))
    estimated_position = (clamped_start_byte / resolved_file_size) * duration_seconds
    return round(max(estimated_position, 0.0), 2)


def _native_api_origin(settings: Settings) -> str:
    configured = settings.backend_origin.strip().rstrip("/")
    configured_host = (urlsplit(configured).hostname or "").strip().lower()
    if configured and configured_host not in {"127.0.0.1", "localhost", "::1"}:
        return configured
    public_origin = settings.public_app_origin.strip().rstrip("/")
    if public_origin:
        parsed_public_origin = urlsplit(public_origin)
        host = (parsed_public_origin.hostname or "").strip().lower()
        if host and host not in {"127.0.0.1", "localhost", "::1"}:
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            return f"http://{host}:{settings.port}"
    if settings.bind_host in {"0.0.0.0", "::"}:
        return f"http://127.0.0.1:{settings.port}"
    return f"http://{settings.bind_host}:{settings.port}"


def _probe_tracks(file_path: Path, settings: Settings) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if not settings.ffprobe_path:
        return [], []

    command = [
        settings.ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        (
            "stream=index,codec_type,codec_name,channels,"
            "disposition:stream_tags=language,title"
        ),
        "-of",
        "json",
        str(file_path),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ffprobe track probe failed for %s: %s", file_path, exc)
        return [], []

    if completed.returncode != 0:
        logger.warning(
            "ffprobe track probe exited with %s for %s",
            completed.returncode,
            file_path,
        )
        return [], []

    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        logger.warning("ffprobe track probe returned invalid JSON for %s", file_path)
        return [], []

    audio_tracks: list[dict[str, object]] = []
    subtitle_tracks: list[dict[str, object]] = []
    for stream in payload.get("streams", []):
        codec_type = stream.get("codec_type")
        if codec_type not in {"audio", "subtitle"}:
            continue
        tags = stream.get("tags", {}) or {}
        disposition = stream.get("disposition", {}) or {}
        track = {
            "index": int(stream.get("index") or 0),
            "codec": stream.get("codec_name"),
            "language": tags.get("language"),
            "title": tags.get("title"),
            "channels": int(stream["channels"]) if stream.get("channels") else None,
            "disposition_default": bool(disposition.get("default", 0)),
        }
        if codec_type == "audio":
            audio_tracks.append(track)
        else:
            subtitle_tracks.append(track)
    return audio_tracks, subtitle_tracks
