from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status

from ..config import Settings
from ..db import get_connection, utcnow_iso
from ..progress import save_progress
from ..security import generate_session_token, hash_session_token
from .desktop_helper_service import (
    record_client_device_app_seen,
    record_helper_resolution,
)
from .desktop_playback_protocol_service import (
    build_vlc_helper_protocol_url,
    build_vlc_helper_started_url,
    infer_target_kind,
)


logger = logging.getLogger(__name__)

DIRECT_EXTERNAL_PROGRESS_SECONDS = 1.0


def cleanup_desktop_vlc_handoffs(settings: Settings) -> None:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        connection.execute(
            """
            DELETE FROM desktop_vlc_handoffs
            WHERE expires_at <= ?
               OR revoked_at IS NOT NULL
            """,
            (now,),
        )
        connection.commit()


def create_desktop_vlc_handoff(
    settings: Settings,
    *,
    user_id: int,
    item: dict[str, object],
    platform: str,
    device_id: str | None,
    auth_session_id: int | None,
    user_agent: str | None,
    source_ip: str | None,
    strategy: str,
    resolved_target: str,
    backend_origin: str,
) -> dict[str, object]:
    handoff_id = generate_session_token()
    access_token = generate_session_token()
    access_token_hash = hash_session_token(access_token, settings.session_secret)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=settings.playback_token_ttl_seconds)
    now_iso = now.isoformat()
    expires_at_iso = expires_at.isoformat()
    resume_seconds = float(item.get("resume_position_seconds") or 0)

    if device_id:
        record_client_device_app_seen(
            settings,
            device_id=device_id,
            user_id=user_id,
            browser_platform=platform,
            browser_user_agent=user_agent,
            ip_address=source_ip,
        )

    with get_connection(settings) as connection:
        connection.execute(
            """
            INSERT INTO desktop_vlc_handoffs (
                handoff_id,
                access_token_hash,
                auth_session_id,
                user_id,
                media_item_id,
                platform,
                strategy,
                resolved_target,
                resume_seconds,
                created_at,
                expires_at,
                device_id,
                user_agent,
                source_ip
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                handoff_id,
                access_token_hash,
                auth_session_id,
                user_id,
                int(item["id"]),
                platform,
                strategy,
                resolved_target,
                resume_seconds,
                now_iso,
                expires_at_iso,
                device_id,
                user_agent,
                source_ip,
            ),
        )
        connection.commit()

    logger.info(
        "Created VLC helper handoff item=%s user=%s platform=%s strategy=%s expires_at=%s",
        item["id"],
        user_id,
        platform,
        strategy,
        expires_at_iso,
    )
    logger.info(
        "Desktop VLC handoff debug %s",
        json.dumps(
            {
                "event": "desktop_vlc_handoff_created",
                "handoff_id": handoff_id,
                "platform": platform,
                "strategy": strategy,
                "helper_protocol": settings.vlc_helper_protocol,
                "resolved_target": resolved_target,
                "backend_origin": backend_origin,
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
    )
    return {
        "handoff_id": handoff_id,
        "helper_protocol": settings.vlc_helper_protocol,
        "protocol_url": build_vlc_helper_protocol_url(
            settings,
            backend_origin=backend_origin,
            handoff_id=handoff_id,
            access_token=access_token,
        ),
        "playlist_url": f"/api/desktop-playback/{int(item['id'])}/playlist?platform={platform}",
        "expires_at": expires_at_iso,
        "strategy": strategy,
        "message": "Launching installed VLC through the Elvern desktop opener helper.",
    }


def resolve_desktop_vlc_handoff(
    settings: Settings,
    *,
    handoff_id: str,
    access_token: str,
    helper_version: str | None = None,
    helper_platform: str | None = None,
    helper_arch: str | None = None,
    helper_vlc_detection_state: str | None = None,
    helper_vlc_detection_path: str | None = None,
    source_ip: str | None = None,
    backend_origin: str,
) -> dict[str, object]:
    row = _require_desktop_vlc_handoff(
        settings,
        handoff_id=handoff_id,
        access_token=access_token,
    )
    record_helper_resolution(
        settings,
        handoff_id=handoff_id,
        device_id=str(row["device_id"]) if row.get("device_id") else None,
        user_id=int(row["user_id"]),
        helper_version=helper_version,
        helper_platform=helper_platform,
        helper_arch=helper_arch,
        helper_vlc_detection_state=helper_vlc_detection_state,
        helper_vlc_detection_path=helper_vlc_detection_path,
        source_ip=source_ip,
    )
    target = str(row["resolved_target"])
    logger.info(
        "Desktop VLC handoff debug %s",
        json.dumps(
            {
                "event": "desktop_vlc_handoff_resolved",
                "handoff_id": row["handoff_id"],
                "platform": row["platform"],
                "strategy": row["strategy"],
                "target_kind": infer_target_kind(target),
                "target": target,
                "helper_version": helper_version,
                "helper_platform": helper_platform,
                "helper_arch": helper_arch,
                "helper_vlc_detection_state": helper_vlc_detection_state,
                "helper_vlc_detection_path": helper_vlc_detection_path,
                "source_ip": source_ip,
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
    )
    return {
        "handoff_id": row["handoff_id"],
        "title": row["title"],
        "media_id": int(row["media_item_id"]),
        "platform": row["platform"],
        "strategy": row["strategy"],
        "target_kind": infer_target_kind(target),
        "target": target,
        "started_url": (
            build_vlc_helper_started_url(
                backend_origin=backend_origin,
                handoff_id=handoff_id,
                access_token=access_token,
            )
            if row["strategy"] == "direct_path" and backend_origin
            else None
        ),
        "resume_seconds": float(row["resume_seconds"] or 0),
        "expires_at": row["expires_at"],
        "session_api_version": 1,
    }


def record_desktop_vlc_handoff_started(
    settings: Settings,
    *,
    handoff_id: str,
    access_token: str,
) -> dict[str, object]:
    row = _require_desktop_vlc_handoff(
        settings,
        handoff_id=handoff_id,
        access_token=access_token,
    )
    if row["strategy"] != "direct_path":
        return {
            "recorded": False,
            "message": "This handoff uses a backend stream URL and records watch progress from actual stream activity.",
        }
    _record_verified_external_launch_progress(
        settings,
        user_id=int(row["user_id"]),
        media_item_id=int(row["media_item_id"]),
        resume_seconds=float(row["resume_seconds"] or 0),
        duration_seconds=row.get("duration_seconds"),
    )
    return {
        "recorded": True,
        "message": "Confirmed VLC launch recorded for Continue Watching.",
    }


def _require_desktop_vlc_handoff(
    settings: Settings,
    *,
    handoff_id: str,
    access_token: str,
) -> dict[str, object]:
    cleanup_desktop_vlc_handoffs(settings)
    token_hash = hash_session_token(access_token, settings.session_secret)
    now = utcnow_iso()
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT
                h.handoff_id,
                h.media_item_id,
                h.platform,
                h.strategy,
                h.resolved_target,
                h.resume_seconds,
                h.expires_at,
                h.device_id,
                h.user_id,
                m.title,
                m.duration_seconds,
                u.enabled
            FROM desktop_vlc_handoffs h
            JOIN media_items m ON m.id = h.media_item_id
            JOIN users u ON u.id = h.user_id
            WHERE h.handoff_id = ?
              AND h.access_token_hash = ?
              AND h.expires_at > ?
              AND h.revoked_at IS NULL
              AND u.enabled = 1
            LIMIT 1
            """,
            (handoff_id, token_hash, now),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Desktop VLC handoff is invalid or has expired",
        )
    return dict(row)


def _record_verified_external_launch_progress(
    settings: Settings,
    *,
    user_id: int,
    media_item_id: int,
    resume_seconds: float,
    duration_seconds: object,
) -> None:
    target_position = max(float(resume_seconds or 0.0), 0.0) + DIRECT_EXTERNAL_PROGRESS_SECONDS
    resolved_duration = None
    try:
        if duration_seconds is not None:
            resolved_duration = float(duration_seconds)
    except (TypeError, ValueError):
        resolved_duration = None
    if resolved_duration is not None:
        target_position = min(target_position, resolved_duration)
    save_progress(
        settings,
        user_id=user_id,
        media_item_id=media_item_id,
        position_seconds=target_position,
        duration_seconds=resolved_duration,
        completed=False,
    )
