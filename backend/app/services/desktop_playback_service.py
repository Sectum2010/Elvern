from __future__ import annotations

import ipaddress
import json
import logging
import os
import pwd
import re
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import quote, urlencode, urlsplit
from xml.sax.saxutils import escape

from fastapi import HTTPException, status
from fastapi.responses import Response

from ..config import Settings
from ..db import get_connection, utcnow_iso
from ..media_stream import ensure_media_path_within_root
from ..progress import save_progress
from ..security import generate_session_token, hash_session_token
from .cloud_library_service import refresh_cloud_media_item_metadata
from .cloud_library_service import ensure_cloud_media_item_provider_access
from .desktop_helper_service import (
    record_client_device_app_seen,
    record_helper_resolution,
)
from .library_service import get_media_item_record
from .native_playback_service import (
    close_native_playback_session,
    create_native_playback_session,
    record_native_playback_session_event,
    save_native_playback_session_progress,
)


logger = logging.getLogger(__name__)

SUPPORTED_DESKTOP_PLATFORMS = {"linux", "windows", "mac"}
DIRECT_EXTERNAL_PROGRESS_SECONDS = 1.0
DIRECT_VLC_PROGRESS_POLL_SECONDS = 5.0
DIRECT_VLC_PROGRESS_SOCKET_TIMEOUT_SECONDS = 10.0
DIRECT_VLC_RC_QUERY_TIMEOUT_SECONDS = 2.0
DIRECT_VLC_PROGRESS_MISSED_POLLS_BEFORE_CLOSE = 3
DIRECT_VLC_SEEK_FORWARD_SLACK_SECONDS = 15.0
DIRECT_VLC_SEEK_BACKWARD_THRESHOLD_SECONDS = 5.0


def _is_local_media_item(item: dict[str, object]) -> bool:
    return str(item.get("source_kind") or "local") == "local"


def _resolve_local_media_file(settings: Settings, item: dict[str, object]) -> Path | None:
    if not _is_local_media_item(item):
        return None
    return ensure_media_path_within_root(Path(str(item["file_path"])), settings)


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


def _probe_cloud_stream_duration_seconds_for_vlc(
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
        user_agent="Elvern Desktop VLC Duration Probe",
        source_ip=None,
        client_name="Desktop VLC Duration Probe",
    )
    stream_url = _rewrite_stream_url_for_linux_same_host(
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


def _ensure_cloud_item_duration_for_vlc(
    settings: Settings,
    *,
    user_id: int,
    item: dict[str, object],
) -> dict[str, object]:
    if str(item.get("source_kind") or "local") != "cloud":
        return item
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

    probed_duration = _probe_cloud_stream_duration_seconds_for_vlc(
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


def infer_desktop_platform(user_agent: str | None, requested_platform: str | None = None) -> str:
    if requested_platform:
        normalized = requested_platform.strip().lower()
        if normalized in SUPPORTED_DESKTOP_PLATFORMS:
            return normalized
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported desktop platform",
        )

    agent = (user_agent or "").lower()
    if "windows" in agent:
        return "windows"
    if "macintosh" in agent or ("mac os x" in agent and "iphone" not in agent and "ipad" not in agent):
        return "mac"
    return "linux"


def infer_same_host_request(
    settings: Settings,
    *,
    platform: str,
    client_ip: str | None,
) -> bool:
    if platform != "linux" or not client_ip:
        return False

    normalized_client_ip = _normalize_ip_literal(client_ip)
    if not normalized_client_ip:
        return False

    try:
        parsed_client_ip = ipaddress.ip_address(normalized_client_ip)
    except ValueError:
        return False

    if parsed_client_ip.is_loopback:
        return True

    return parsed_client_ip.compressed in _local_server_ip_candidates(settings)


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


def _local_server_ip_candidates(settings: Settings) -> set[str]:
    candidates: set[str] = set()

    for host in (settings.bind_host, settings.frontend_host):
        candidates.update(_resolve_host_ips(host))

    for origin in (settings.public_app_origin, settings.backend_origin):
        if not origin:
            continue
        hostname = urlsplit(origin).hostname or ""
        candidates.update(_resolve_host_ips(hostname))

    hostname = socket.gethostname().strip()
    if hostname:
        candidates.update(_resolve_host_ips(hostname))

    fqdn = socket.getfqdn().strip()
    if fqdn:
        candidates.update(_resolve_host_ips(fqdn))

    return candidates


def _normalize_ip_literal(value: str) -> str | None:
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    if "%" in candidate:
        candidate = candidate.split("%", 1)[0]
    return candidate or None


def _resolve_host_ips(host: str | None) -> set[str]:
    normalized_host = (host or "").strip()
    if not normalized_host or normalized_host in {"0.0.0.0", "::", "[::]"}:
        return set()

    normalized_host = _normalize_ip_literal(normalized_host) or ""
    if not normalized_host:
        return set()

    try:
        return {ipaddress.ip_address(normalized_host).compressed}
    except ValueError:
        pass

    resolved: set[str] = set()
    try:
        for _, _, _, _, sockaddr in socket.getaddrinfo(normalized_host, None):
            candidate = _normalize_ip_literal(sockaddr[0])
            if not candidate:
                continue
            try:
                resolved.add(ipaddress.ip_address(candidate).compressed)
            except ValueError:
                continue
    except OSError:
        return set()

    return resolved


def build_desktop_playback_resolution(
    settings: Settings,
    *,
    item: dict[str, object],
    platform: str,
    same_host: bool,
) -> dict[str, object]:
    resolved_file = _resolve_local_media_file(settings, item)
    mapped_target = map_media_path_for_platform(settings, resolved_file, platform) if resolved_file else None
    resume_seconds = float(item.get("resume_position_seconds") or 0)
    playlist_url = f"/api/desktop-playback/{int(item['id'])}/playlist?platform={platform}"
    handoff_supported = _desktop_helper_supported(settings)
    notes: list[str] = []
    vlc_available_on_linux_host = platform == "linux" and same_host and bool(settings.vlc_path_linux)

    if mapped_target is not None:
        strategy = "direct_path"
        vlc_target = mapped_target
        used_backend_fallback = False
        notes.append("Installed VLC will use a direct source path for this desktop platform.")
    else:
        strategy = "backend_url"
        vlc_target = "Elvern will fall back to a short-lived backend URL because no direct desktop mapping is configured."
        used_backend_fallback = True
        notes.append("No mapped direct source is configured for this platform, so VLC will use a short-lived backend URL fallback.")

    linux_same_host_launch = (
        vlc_available_on_linux_host
        and (
            (
                strategy == "direct_path"
                and mapped_target is not None
                and Path(mapped_target).exists()
            )
            or (not _is_local_media_item(item))
        )
    )
    if linux_same_host_launch:
        open_method = "spawn_vlc"
        if _is_local_media_item(item):
            notes.append("On the Elvern host, clicking Open in VLC launches the installed VLC app directly with the real local path.")
        else:
            notes.append("On the Elvern host, clicking Open in VLC launches the installed VLC app directly with a short-lived local Elvern stream URL for this cloud item.")
    elif handoff_supported:
        open_method = "protocol_helper"
        notes.append("Desktop helper handoff is ready for installed VLC on this platform.")
    else:
        open_method = "download_playlist"
        notes.append("Desktop helper handoff is unavailable until a real DGX app/backend origin is configured.")

    if platform == "linux" and strategy == "direct_path" and same_host and mapped_target and not Path(mapped_target).exists():
        notes.append("The configured Linux VLC source path does not currently exist on disk; check ELVERN_LIBRARY_ROOT_LINUX.")
    elif not resolved_file:
        notes.append("Cloud libraries use a secure backend stream fallback for desktop VLC in this phase.")
    elif platform == "windows" and not settings.library_root_windows:
        notes.append("Windows VLC mapping is not configured yet. Set ELVERN_LIBRARY_ROOT_WINDOWS to a drive root or UNC share.")
    elif platform == "mac" and not settings.library_root_mac:
        notes.append("macOS VLC mapping is not configured yet. Set ELVERN_LIBRARY_ROOT_MAC to the mounted media root.")

    return {
        "platform": platform,
        "strategy": strategy,
        "title": str(item["title"]),
        "resume_seconds": resume_seconds,
        "open_supported": linux_same_host_launch,
        "handoff_supported": handoff_supported,
        "open_method": open_method,
        "same_host_launch": linux_same_host_launch,
        "used_backend_fallback": used_backend_fallback,
        "helper_protocol": settings.vlc_helper_protocol if handoff_supported else None,
        "vlc_target": vlc_target,
        "playlist_url": playlist_url,
        "notes": notes,
    }


def launch_vlc_for_item(
    settings: Settings,
    *,
    user_id: int,
    item: dict[str, object],
    same_host: bool,
    auth_session_id: int | None,
    user_agent: str | None,
    source_ip: str | None,
) -> dict[str, object]:
    if not same_host:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Direct VLC launch is only available for same-host Linux playback",
        )

    item = _ensure_cloud_item_duration_for_vlc(
        settings,
        user_id=user_id,
        item=item,
    )
    resolved_file = _resolve_local_media_file(settings, item)
    if not settings.vlc_path_linux:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VLC was not found on this Linux host. Set ELVERN_VLC_PATH_LINUX or install VLC.",
        )
    resume_seconds = float(item.get("resume_position_seconds") or 0)
    strategy = "direct_path"
    tracked_progress_session: dict[str, str] | None = None
    rc_host_port = _reserve_linux_vlc_rc_port()
    if resolved_file is not None:
        target = map_media_path_for_platform(settings, resolved_file, "linux")
        if not target:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No Linux direct source path is configured for VLC launch",
            )
        target_path = Path(target)
        if not target_path.is_absolute():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The resolved Linux VLC target must be an absolute local path.",
            )
        if not target_path.exists():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The resolved Linux VLC target does not exist on disk. Check ELVERN_LIBRARY_ROOT_LINUX.",
            )
        launch_target = str(target_path)
        tracked_progress_session = create_native_playback_session(
            settings,
            user_id=user_id,
            item=item,
            auth_session_id=auth_session_id,
            user_agent=user_agent,
            source_ip=source_ip,
            client_name="Linux Same-Host VLC Direct",
        )
    else:
        tracked_progress_session = create_native_playback_session(
            settings,
            user_id=user_id,
            item=item,
            auth_session_id=auth_session_id,
            user_agent=user_agent,
            source_ip=source_ip,
            client_name="Linux Same-Host VLC",
        )
        launch_target = _rewrite_stream_url_for_linux_same_host(
            settings,
            stream_url=str(tracked_progress_session["stream_url"]),
        )
        strategy = "backend_url"
    command = build_vlc_launch_command(
        settings.vlc_path_linux,
        launch_target,
        resume_seconds,
        rc_host_port=rc_host_port,
    )
    launch_env, env_summary, env_diagnostics = build_linux_gui_launch_environment()

    if not launch_env.get("DISPLAY") and not launch_env.get("WAYLAND_DISPLAY"):
        if tracked_progress_session is not None:
            close_native_playback_session(
                settings,
                session_id=str(tracked_progress_session["session_id"]),
                access_token=str(tracked_progress_session["access_token"]),
            )
        logger.warning(
            "Refusing Linux VLC launch without GUI session context item=%s env=%s diagnostics=%s",
            item["id"],
            env_summary,
            env_diagnostics,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Elvern is running without an active Linux desktop display session for VLC. "
                "The backend service is missing DISPLAY/WAYLAND session context, so it cannot "
                "reliably open a GUI VLC window."
            ),
        )

    logger.info(
        "Launching VLC directly for item=%s target=%s env=%s diagnostics=%s",
        item["id"],
        launch_target,
        env_summary,
        env_diagnostics,
    )
    try:
        with open("/dev/null", "rb") as null_in, open("/dev/null", "wb") as null_out:
            process = subprocess.Popen(
                command,
                stdin=null_in,
                stdout=null_out,
                stderr=null_out,
                start_new_session=True,
                env=launch_env,
            )
    except OSError as exc:
        if tracked_progress_session is not None:
            close_native_playback_session(
                settings,
                session_id=str(tracked_progress_session["session_id"]),
                access_token=str(tracked_progress_session["access_token"]),
            )
        logger.exception("Failed to launch VLC for item=%s", item["id"])
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to launch VLC: {exc}",
        ) from exc

    time.sleep(0.75)
    return_code = process.poll()
    if return_code is not None:
        if tracked_progress_session is not None:
            close_native_playback_session(
                settings,
                session_id=str(tracked_progress_session["session_id"]),
                access_token=str(tracked_progress_session["access_token"]),
            )
        logger.warning(
            "VLC exited immediately after launch item=%s target=%s return_code=%s env=%s diagnostics=%s",
            item["id"],
            launch_target,
            return_code,
            env_summary,
            env_diagnostics,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "VLC exited immediately before reaching the Linux desktop session. "
                "Elvern did not keep a live GUI VLC process open, so this launch was not treated as a success."
            ),
        )

    if tracked_progress_session is not None:
        _start_linux_vlc_direct_progress_monitor(
            settings,
            process=process,
            rc_host_port=rc_host_port,
            session_id=str(tracked_progress_session["session_id"]),
            access_token=str(tracked_progress_session["access_token"]),
            duration_seconds=item.get("duration_seconds"),
        )

    message = "VLC launch request reached the active Linux desktop session."
    if resume_seconds > 0:
        message += f" Resume requested at {resume_seconds:.1f} seconds."
    return {
        "launched": True,
        "message": message,
        "target": launch_target,
        "strategy": strategy,
        "command": command,
    }


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
) -> dict[str, object]:
    backend_origin = _desktop_backend_origin(settings)
    if not _desktop_helper_supported(settings) or not backend_origin:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Configure ELVERN_PUBLIC_APP_ORIGIN to the real DGX private app URL "
                "before desktop helper handoff can be used. Set ELVERN_BACKEND_ORIGIN "
                "too when the backend API origin differs from the app origin host."
            ),
        )

    item = _ensure_cloud_item_duration_for_vlc(
        settings,
        user_id=user_id,
        item=item,
    )
    if str(item.get("source_kind") or "local") == "cloud":
        ensure_cloud_media_item_provider_access(
            settings,
            user_id=user_id,
            item_id=int(item["id"]),
        )
    cleanup_desktop_vlc_handoffs(settings)
    resolved_file = _resolve_local_media_file(settings, item)
    mapped_target = map_media_path_for_platform(settings, resolved_file, platform) if resolved_file else None

    if mapped_target is not None:
        strategy = "direct_path"
        resolved_target = mapped_target
    else:
        session_payload = create_native_playback_session(
            settings,
            user_id=user_id,
            item=item,
            # The helper fallback stream already carries its own short-lived
            # native playback token. Do not couple desktop VLC stream access to
            # the browser auth session, or external playback can be revoked by
            # unrelated web-session rotation/logout before VLC finishes opening.
            auth_session_id=None,
            user_agent=user_agent,
            source_ip=source_ip,
            client_name=f"VLC Helper Fallback ({platform})",
        )
        strategy = "backend_url"
        resolved_target = str(session_payload["stream_url"])

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


def build_linux_gui_launch_environment() -> tuple[dict[str, str], dict[str, str | None], list[str]]:
    runtime_env = os.environ.copy()
    current_user = pwd.getpwuid(os.getuid())
    runtime_dir = runtime_env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    launch_env = runtime_env.copy()
    diagnostics: list[str] = []
    session = _discover_active_linux_gui_session()

    launch_env["HOME"] = runtime_env.get("HOME") or current_user.pw_dir
    launch_env["USER"] = runtime_env.get("USER") or current_user.pw_name

    leader_env = _read_process_environment(session.get("leader", "")) if session.get("leader") else {}
    if leader_env:
        diagnostics.append(f"leader_env={session.get('leader')}")
        for key in (
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "XDG_RUNTIME_DIR",
            "DBUS_SESSION_BUS_ADDRESS",
            "XAUTHORITY",
        ):
            if leader_env.get(key):
                launch_env[key] = leader_env[key]

    effective_runtime_dir = launch_env.get("XDG_RUNTIME_DIR") or runtime_dir
    if effective_runtime_dir and Path(effective_runtime_dir).exists():
        launch_env["XDG_RUNTIME_DIR"] = effective_runtime_dir
        diagnostics.append(f"runtime_dir={effective_runtime_dir}")
        bus_path = Path(effective_runtime_dir) / "bus"
        if bus_path.exists():
            launch_env["DBUS_SESSION_BUS_ADDRESS"] = launch_env.get(
                "DBUS_SESSION_BUS_ADDRESS",
                runtime_env.get(
                "DBUS_SESSION_BUS_ADDRESS",
                f"unix:path={bus_path}",
                ),
            )
            diagnostics.append(f"dbus={launch_env['DBUS_SESSION_BUS_ADDRESS']}")
    else:
        diagnostics.append("runtime_dir_missing")

    if session.get("display") and not launch_env.get("DISPLAY"):
        launch_env["DISPLAY"] = session["display"]
    if launch_env.get("DISPLAY"):
        diagnostics.append(f"display={launch_env['DISPLAY']}")

    if session.get("type") == "wayland" and not launch_env.get("WAYLAND_DISPLAY"):
        detected_wayland = _detect_wayland_display(launch_env.get("XDG_RUNTIME_DIR"))
        if detected_wayland:
            launch_env["WAYLAND_DISPLAY"] = detected_wayland
    if launch_env.get("WAYLAND_DISPLAY"):
        diagnostics.append(f"wayland={launch_env['WAYLAND_DISPLAY']}")

    xauthority = launch_env.get("XAUTHORITY") or runtime_env.get("XAUTHORITY") or _detect_xauthority(
        launch_env.get("XDG_RUNTIME_DIR"),
        current_user.pw_dir,
    )
    if xauthority:
        launch_env["XAUTHORITY"] = xauthority
        diagnostics.append(f"xauthority={xauthority}")

    if session.get("id"):
        diagnostics.append(
            f"session={session['id']}:{session.get('type') or 'unknown'}:{session.get('state') or 'unknown'}",
        )
    else:
        diagnostics.append("session=unknown")

    env_summary = {
        "DISPLAY": launch_env.get("DISPLAY"),
        "WAYLAND_DISPLAY": launch_env.get("WAYLAND_DISPLAY"),
        "XDG_RUNTIME_DIR": launch_env.get("XDG_RUNTIME_DIR"),
        "DBUS_SESSION_BUS_ADDRESS": launch_env.get("DBUS_SESSION_BUS_ADDRESS"),
        "HOME": launch_env.get("HOME"),
        "USER": launch_env.get("USER"),
        "XAUTHORITY": launch_env.get("XAUTHORITY"),
    }
    return launch_env, env_summary, diagnostics


def _discover_active_linux_gui_session() -> dict[str, str]:
    current_uid = str(os.getuid())
    current_user = pwd.getpwuid(os.getuid()).pw_name

    preferred_session_ids = _preferred_session_ids_for_user(current_user)
    for session_id in preferred_session_ids:
        session = _describe_loginctl_session(session_id)
        if not session:
            continue
        if session.get("user") != current_uid:
            continue
        if session.get("active", "").lower() != "yes":
            continue
        if session.get("remote", "").lower() == "yes":
            continue
        return session

    sessions = _run_loginctl("list-sessions", "--no-legend")
    if not sessions:
        return {}

    for line in sessions.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        session_id = parts[0]
        uid = parts[1]
        state = parts[5]
        if uid != current_uid or state.lower() != "active":
            continue
        session = _describe_loginctl_session(session_id)
        if not session:
            continue
        if session.get("remote", "").lower() == "yes":
            continue
        return session
    return {}


def _preferred_session_ids_for_user(username: str) -> list[str]:
    user_properties = _parse_loginctl_properties(
        _run_loginctl(
            "show-user",
            username,
            "-p",
            "Display",
            "-p",
            "Sessions",
        ),
    )
    candidate_ids: list[str] = []
    display_session = user_properties.get("Display", "").strip()
    if display_session:
        candidate_ids.append(display_session)

    sessions_value = user_properties.get("Sessions", "").strip()
    if sessions_value:
        for session_id in sessions_value.split():
            if session_id and session_id not in candidate_ids:
                candidate_ids.append(session_id)
    return candidate_ids


def _describe_loginctl_session(session_id: str) -> dict[str, str]:
    details = _parse_loginctl_properties(
        _run_loginctl(
            "show-session",
            session_id,
            "-p",
            "Id",
            "-p",
            "Display",
            "-p",
            "Type",
            "-p",
            "State",
            "-p",
            "Active",
            "-p",
            "Remote",
            "-p",
            "Leader",
            "-p",
            "User",
            "-p",
            "Class",
        ),
    )
    if not details:
        return {}
    return {
        "id": details.get("Id", session_id),
        "display": details.get("Display", ""),
        "type": details.get("Type", ""),
        "state": details.get("State", ""),
        "active": details.get("Active", ""),
        "remote": details.get("Remote", ""),
        "leader": details.get("Leader", ""),
        "user": details.get("User", ""),
        "class": details.get("Class", ""),
    }


def _run_loginctl(*args: str) -> str:
    try:
        result = subprocess.run(
            ["loginctl", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _parse_loginctl_properties(raw_output: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in raw_output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        properties[key] = value
    return properties


def _detect_wayland_display(runtime_dir: str | None) -> str | None:
    if not runtime_dir:
        return None
    runtime_path = Path(runtime_dir)
    if not runtime_path.exists():
        return None
    for candidate in sorted(runtime_path.glob("wayland-*")):
        if candidate.is_socket() or candidate.exists():
            return candidate.name
    return None


def _detect_xauthority(runtime_dir: str | None, home_dir: str) -> str | None:
    home_candidate = Path(home_dir) / ".Xauthority"
    if home_candidate.exists():
        return str(home_candidate)

    if runtime_dir:
        runtime_path = Path(runtime_dir)
        for candidate in sorted(runtime_path.glob("*/Xauthority")):
            if candidate.exists():
                return str(candidate)
        for candidate in sorted(runtime_path.glob(".mutter-Xwaylandauth.*")):
            if candidate.exists():
                return str(candidate)
    return None


def _read_process_environment(pid: str) -> dict[str, str]:
    normalized_pid = (pid or "").strip()
    if not normalized_pid.isdigit():
        return {}
    environ_path = Path("/proc") / normalized_pid / "environ"
    try:
        payload = environ_path.read_bytes()
    except OSError:
        return {}

    environment: dict[str, str] = {}
    for chunk in payload.split(b"\0"):
        if not chunk or b"=" not in chunk:
            continue
        key, value = chunk.split(b"=", 1)
        try:
            environment[key.decode("utf-8")] = value.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return environment


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
    backend_origin = _desktop_backend_origin(settings)
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


def build_vlc_playlist_response(
    settings: Settings,
    *,
    user_id: int,
    item: dict[str, object],
    platform: str,
    auth_session_id: int | None,
    user_agent: str | None,
    source_ip: str | None,
) -> Response:
    item = _ensure_cloud_item_duration_for_vlc(
        settings,
        user_id=user_id,
        item=item,
    )
    resolved_file = _resolve_local_media_file(settings, item)
    mapped_target = map_media_path_for_platform(settings, resolved_file, platform) if resolved_file else None
    resume_seconds = float(item.get("resume_position_seconds") or 0)

    if mapped_target is not None:
        location = build_vlc_location_uri(platform, mapped_target)
        logger.info(
            "Building direct-source VLC playlist item=%s platform=%s target=%s",
            item["id"],
            platform,
            mapped_target,
        )
    else:
        session_payload = create_native_playback_session(
            settings,
            user_id=user_id,
            item=item,
            auth_session_id=auth_session_id,
            user_agent=user_agent,
            source_ip=source_ip,
            client_name=f"VLC Playlist Fallback ({platform})",
        )
        location = str(session_payload["stream_url"])
        logger.info(
            "Building backend-fallback VLC playlist item=%s platform=%s stream_url=%s",
            item["id"],
            platform,
            location,
        )

    playlist_body = build_xspf_playlist(
        title=str(item["title"]),
        location=location,
        resume_seconds=resume_seconds,
        duration_seconds=item.get("duration_seconds"),
    )
    filename = build_playlist_filename(str(item["title"]))
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "private, max-age=0, must-revalidate",
    }
    return Response(
        content=playlist_body,
        media_type="application/xspf+xml",
        headers=headers,
    )


def get_desktop_playback_status(settings: Settings) -> dict[str, object]:
    public_app_origin = _public_app_origin(settings)
    backend_origin = _desktop_backend_origin(settings)
    return {
        "mode": settings.desktop_playback_mode,
        "helper_protocol": settings.vlc_helper_protocol,
        "helper_requires_backend_origin": True,
        "public_app_origin": public_app_origin,
        "public_origin_configured": bool(settings.public_app_origin),
        "backend_origin": backend_origin,
        "backend_origin_configured": bool(settings.backend_origin),
        "linux_vlc_available": bool(settings.vlc_path_linux),
        "linux_vlc_path": settings.vlc_path_linux,
        "windows_vlc_path": settings.vlc_path_windows,
        "mac_vlc_path": settings.vlc_path_mac,
        "linux_library_root": settings.library_root_linux,
        "windows_library_root": settings.library_root_windows,
        "mac_library_root": settings.library_root_mac,
    }


def map_media_path_for_platform(settings: Settings, resolved_file: Path, platform: str) -> str | None:
    media_root = settings.media_root.resolve()
    try:
        relative_parts = resolved_file.relative_to(media_root).parts
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Media path escapes configured media root",
        ) from exc

    if platform == "linux":
        base_root = settings.library_root_linux.strip()
        if not base_root:
            return None
        return str(PurePosixPath(base_root).joinpath(*relative_parts))

    if platform == "mac":
        if not settings.library_root_mac:
            return None
        return str(PurePosixPath(settings.library_root_mac).joinpath(*relative_parts))

    if platform == "windows":
        if not settings.library_root_windows:
            return None
        return str(PureWindowsPath(settings.library_root_windows).joinpath(*relative_parts))

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unsupported desktop platform",
    )


def infer_target_kind(target: str) -> str:
    lowered = target.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return "url"
    return "path"


def build_vlc_launch_command(
    vlc_path: str | None,
    target: str,
    resume_seconds: float,
    *,
    rc_host_port: int | None = None,
) -> list[str]:
    if not vlc_path:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VLC was not found on this machine.",
        )
    command = [vlc_path]
    if rc_host_port is not None:
        command.extend(["--extraintf", "rc", "--rc-fake-tty", "--rc-host", f"127.0.0.1:{rc_host_port}"])
    if infer_target_kind(target) == "url":
        command.append("--network-caching=1000")
    if resume_seconds > 0:
        command.append(f"--start-time={resume_seconds:.3f}")
    command.append(target)
    return command


def _rewrite_stream_url_for_linux_same_host(settings: Settings, *, stream_url: str) -> str:
    parsed = urlsplit(stream_url)
    if not parsed.scheme or not parsed.netloc:
        return stream_url
    host = settings.bind_host.strip()
    if host in {"", "0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{settings.port}"
    return parsed._replace(netloc=netloc).geturl()


def build_vlc_location_uri(platform: str, target: str) -> str:
    if platform == "windows":
        if target.startswith("\\\\"):
            parts = [part for part in target.lstrip("\\").split("\\") if part]
            if len(parts) < 2:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Invalid Windows UNC path configured for VLC playback",
                )
            host, *segments = parts
            escaped_segments = "/".join(quote(segment) for segment in segments)
            return f"file://{host}/{escaped_segments}"

        normalized = target.replace("\\", "/")
        drive, _, remainder = normalized.partition(":")
        if len(drive) == 1 and remainder.startswith("/"):
            escaped_path = quote(remainder)
            return f"file:///{drive.upper()}:{escaped_path}"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Windows VLC playback target must be a drive path or UNC path",
        )

    escaped_path = quote(target, safe="/:")
    return f"file://{escaped_path if escaped_path.startswith('/') else f'/{escaped_path}'}"


def build_xspf_playlist(
    *,
    title: str,
    location: str,
    resume_seconds: float,
    duration_seconds: object,
) -> str:
    extension_lines = []
    if resume_seconds > 0:
        extension_lines.append(
            f"        <vlc:option>start-time={escape(f'{resume_seconds:.3f}')}</vlc:option>"
        )

    duration_line = ""
    if isinstance(duration_seconds, (int, float)) and duration_seconds > 0:
        duration_line = f"      <duration>{int(float(duration_seconds) * 1000)}</duration>\n"

    extension_block = ""
    if extension_lines:
        extension_body = "\n".join(extension_lines)
        extension_block = (
            "      <extension application=\"http://www.videolan.org/vlc/playlist/0\">\n"
            f"{extension_body}\n"
            "      </extension>\n"
        )

    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<playlist version=\"1\" xmlns=\"http://xspf.org/ns/0/\" "
        "xmlns:vlc=\"http://www.videolan.org/vlc/playlist/ns/0/\">\n"
        f"  <title>{escape(title)}</title>\n"
        "  <trackList>\n"
        "    <track>\n"
        f"      <location>{escape(location)}</location>\n"
        f"      <title>{escape(title)}</title>\n"
        f"{duration_line}"
        f"{extension_block}"
        "    </track>\n"
        "  </trackList>\n"
        "</playlist>\n"
    )


def build_playlist_filename(title: str) -> str:
    safe = "".join(character if character.isalnum() or character in {" ", "-", "_"} else " " for character in title)
    normalized = "-".join(part for part in safe.split() if part).strip("-") or "elvern-vlc"
    return f"{normalized}.xspf"


def build_vlc_helper_protocol_url(
    settings: Settings,
    *,
    backend_origin: str,
    handoff_id: str,
    access_token: str,
) -> str:
    params = urlencode(
        {
            "api": backend_origin,
            "handoff": handoff_id,
            "token": access_token,
        }
    )
    return f"{settings.vlc_helper_protocol}://play?{params}"


def build_vlc_helper_started_url(
    *,
    backend_origin: str,
    handoff_id: str,
    access_token: str,
) -> str:
    params = urlencode({"token": access_token})
    return (
        f"{backend_origin.rstrip('/')}/api/desktop-playback/handoff/"
        f"{quote(handoff_id, safe='')}/started?{params}"
    )


def _public_app_origin(settings: Settings) -> str:
    configured = settings.public_app_origin.strip().rstrip("/")
    if configured:
        return configured
    host = settings.frontend_host
    if host in {"", "0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    return f"http://{host}:{settings.frontend_port}"


def _desktop_backend_origin(settings: Settings) -> str:
    configured = settings.backend_origin.strip().rstrip("/")
    configured_host = (urlsplit(configured).hostname or "").strip().lower()
    if configured and configured_host not in {"127.0.0.1", "localhost", "::1"}:
        return configured
    public_origin = settings.public_app_origin.strip().rstrip("/")
    if public_origin:
        parsed = urlsplit(public_origin)
        host = (parsed.hostname or settings.bind_host).strip().lower()
        if host in {"", "0.0.0.0", "::", "[::]"}:
            host = "127.0.0.1"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{settings.port}"
    host = settings.bind_host
    if host in {"", "0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    return f"http://{host}:{settings.port}"


def _desktop_helper_supported(settings: Settings) -> bool:
    if not settings.vlc_helper_protocol:
        return False
    return bool(settings.backend_origin.strip() or settings.public_app_origin.strip())


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


def _reserve_linux_vlc_rc_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(candidate.getsockname()[1])


def _start_linux_vlc_direct_progress_monitor(
    settings: Settings,
    *,
    process: subprocess.Popen,
    rc_host_port: int,
    session_id: str,
    access_token: str,
    duration_seconds: object,
) -> None:
    watcher = threading.Thread(
        target=_monitor_linux_vlc_direct_progress,
        args=(settings, process, rc_host_port, session_id, access_token, duration_seconds),
        daemon=True,
        name=f"linux-vlc-progress-{session_id[:8]}",
    )
    watcher.start()


def _monitor_linux_vlc_direct_progress(
    settings: Settings,
    process: subprocess.Popen,
    rc_host_port: int,
    session_id: str,
    access_token: str,
    duration_seconds: object,
) -> None:
    connection: socket.socket | None = None
    last_position_seconds = 0.0
    should_close_session = False
    resolved_duration = None
    try:
        if duration_seconds is not None:
            resolved_duration = float(duration_seconds)
    except (TypeError, ValueError):
        resolved_duration = None

    try:
        connection = _connect_vlc_rc_socket(process, rc_host_port)
        if connection is None:
            return
        should_close_session = True
        last_position_seconds = _poll_vlc_position_until_exit(
            settings,
            process=process,
            connection=connection,
            session_id=session_id,
            access_token=access_token,
            duration_seconds=resolved_duration,
        )
        final_position = _query_vlc_rc_position(connection)
        if final_position is not None:
            last_position_seconds = max(last_position_seconds, final_position)
    except Exception:
        logger.exception("Linux VLC progress monitor failed for native session %s", session_id)
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        if should_close_session:
            try:
                close_native_playback_session(
                    settings,
                    session_id=session_id,
                    access_token=access_token,
                    position_seconds=last_position_seconds if last_position_seconds > 0 else None,
                    duration_seconds=resolved_duration,
                    completed=False,
                )
            except Exception:
                logger.exception("Unable to close Linux VLC direct native session %s", session_id)


def _connect_vlc_rc_socket(process: subprocess.Popen, rc_host_port: int) -> socket.socket | None:
    deadline = time.monotonic() + DIRECT_VLC_PROGRESS_SOCKET_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        connection: socket.socket | None = None
        try:
            connection = socket.create_connection(("127.0.0.1", rc_host_port), timeout=DIRECT_VLC_RC_QUERY_TIMEOUT_SECONDS)
            connection.settimeout(DIRECT_VLC_RC_QUERY_TIMEOUT_SECONDS)
            _drain_vlc_rc_output(connection)
            return connection
        except OSError:
            try:
                connection.close()
            except Exception:
                pass
            time.sleep(0.1)
    logger.warning("Linux VLC rc port was never ready for %s", rc_host_port)
    return None


def _poll_vlc_position_until_exit(
    settings: Settings,
    *,
    process: subprocess.Popen,
    connection: socket.socket,
    session_id: str,
    access_token: str,
    duration_seconds: float | None,
) -> float:
    last_position_seconds = 0.0
    previous_sample_position: float | None = None
    previous_sample_monotonic: float | None = None
    missed_polls = 0
    startup_deadline = time.monotonic() + DIRECT_VLC_PROGRESS_SOCKET_TIMEOUT_SECONDS
    opened_recorded = False
    while True:
        sampled_at = time.monotonic()
        position_seconds = _query_vlc_rc_position(connection)
        if not opened_recorded:
            record_native_playback_session_event(
                settings,
                session_id=session_id,
                access_token=access_token,
                event_type="playback_opened",
                position_seconds=position_seconds or 0.0,
                duration_seconds=duration_seconds,
            )
            opened_recorded = True
        if position_seconds is not None and position_seconds > last_position_seconds:
            save_native_playback_session_progress(
                settings,
                session_id=session_id,
                access_token=access_token,
                position_seconds=position_seconds,
                duration_seconds=duration_seconds,
                completed=False,
            )
            last_position_seconds = position_seconds
        if position_seconds is not None and _vlc_position_change_indicates_seek(
            previous_position_seconds=previous_sample_position,
            previous_sample_monotonic=previous_sample_monotonic,
            current_position_seconds=position_seconds,
            current_sample_monotonic=sampled_at,
        ):
            record_native_playback_session_event(
                settings,
                session_id=session_id,
                access_token=access_token,
                event_type="playback_seeked",
                position_seconds=position_seconds,
                duration_seconds=duration_seconds,
            )
        if position_seconds is not None:
            missed_polls = 0
            previous_sample_position = position_seconds
            previous_sample_monotonic = sampled_at
        else:
            missed_polls += 1
            if last_position_seconds <= 0 and process.poll() is not None and time.monotonic() < startup_deadline:
                missed_polls = 0
            elif missed_polls >= DIRECT_VLC_PROGRESS_MISSED_POLLS_BEFORE_CLOSE:
                break

        time.sleep(DIRECT_VLC_PROGRESS_POLL_SECONDS)
    return last_position_seconds


def _vlc_position_change_indicates_seek(
    *,
    previous_position_seconds: float | None,
    previous_sample_monotonic: float | None,
    current_position_seconds: float,
    current_sample_monotonic: float,
) -> bool:
    if previous_position_seconds is None or previous_sample_monotonic is None:
        return False
    delta_seconds = current_position_seconds - previous_position_seconds
    elapsed_seconds = max(current_sample_monotonic - previous_sample_monotonic, 0.0)
    if delta_seconds <= -DIRECT_VLC_SEEK_BACKWARD_THRESHOLD_SECONDS:
        return True
    return delta_seconds > elapsed_seconds + DIRECT_VLC_SEEK_FORWARD_SLACK_SECONDS


def _query_vlc_rc_position(connection: socket.socket) -> float | None:
    try:
        connection.sendall(b"get_time\n")
        response = _drain_vlc_rc_output(connection)
    except OSError:
        return None
    matches = [
        line.strip()
        for line in response.replace("\r", "\n").splitlines()
        if re.fullmatch(r"-?\d+", line.strip())
    ]
    if not matches:
        return None
    try:
        value = int(matches[-1])
    except ValueError:
        return None
    if value < 0:
        return None
    return float(value)


def _drain_vlc_rc_output(connection: socket.socket) -> str:
    chunks: list[str] = []
    deadline = time.monotonic() + DIRECT_VLC_RC_QUERY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            payload = connection.recv(4096)
        except socket.timeout:
            if chunks:
                break
            continue
        except OSError:
            break
        if not payload:
            break
        chunks.append(payload.decode("utf-8", errors="ignore"))
        if chunks[-1].rstrip().endswith(">"):
            break
    return "".join(chunks)
