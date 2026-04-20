from __future__ import annotations

import json
import math
import subprocess
import threading
from pathlib import Path

from ..config import Settings
from ..db import utcnow_iso
from .library_service import get_media_item_record
from .mobile_playback_models import (
    ROUTE2_FULL_PREFLIGHT_TIMEOUT_SECONDS,
    SEGMENT_DURATION_SECONDS,
    MobilePlaybackSession,
)
from .mobile_playback_source_service import (
    _rewrite_stream_url_for_server_localhost as _rewrite_stream_url_for_server_localhost_impl,
)
from .native_playback_service import close_native_playback_session, create_native_playback_session


def _route2_full_preflight_cache_path(route2_root: Path, session: MobilePlaybackSession) -> Path:
    return (
        route2_root
        / "preflight"
        / f"{session.source_fingerprint}-{session.profile}.json"
    )


def _route2_full_preflight_source_input(
    settings: Settings,
    session: MobilePlaybackSession,
) -> tuple[str, str | None, str | None]:
    if session.source_input_kind == "path":
        return session.source_locator, None, None
    item = get_media_item_record(settings, item_id=session.media_item_id)
    if item is None:
        raise ValueError("Experimental playback media item is no longer available")
    session_payload = create_native_playback_session(
        settings,
        user_id=session.user_id,
        item=item,
        auth_session_id=None,
        user_agent="Elvern Route2 Full Playback Preflight",
        source_ip=None,
        client_name="Route2 Full Playback Preflight",
    )
    return (
        _rewrite_stream_url_for_server_localhost_impl(
            settings,
            stream_url=str(session_payload["stream_url"]),
        ),
        str(session_payload["session_id"]),
        str(session_payload["access_token"]),
    )


def _route2_full_scan_packet_bins(
    settings: Settings,
    source_input: str,
    *,
    select_stream: str,
    total_segments: int,
) -> list[int]:
    command = [
        str(settings.ffprobe_path),
        "-v",
        "error",
        "-select_streams",
        select_stream,
        "-show_entries",
        "packet=pts_time,size",
        "-of",
        "csv=p=0",
        source_input,
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=ROUTE2_FULL_PREFLIGHT_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        return [0] * total_segments
    bins = [0] * total_segments
    for line in (completed.stdout or "").splitlines():
        parts = [token.strip() for token in line.split(",") if token.strip()]
        if len(parts) < 2:
            continue
        try:
            pts_seconds = float(parts[0])
            packet_bytes = int(float(parts[1]))
        except (TypeError, ValueError):
            continue
        if packet_bytes <= 0:
            continue
        segment_index = max(
            0,
            min(total_segments - 1, int(math.floor(max(pts_seconds, 0.0) / SEGMENT_DURATION_SECONDS))),
        )
        bins[segment_index] += packet_bytes
    return bins


def _build_route2_full_source_bin_bytes(
    settings: Settings,
    session: MobilePlaybackSession,
    *,
    route2_full_preflight_source_input,
    route2_full_scan_packet_bins,
    route2_profile_floor_segment_bytes,
) -> list[int]:
    total_segments = max(1, math.ceil(session.duration_seconds / SEGMENT_DURATION_SECONDS))
    source_input = ""
    native_session_id = None
    native_access_token = None
    try:
        source_input, native_session_id, native_access_token = route2_full_preflight_source_input(session)
        video_bins = route2_full_scan_packet_bins(
            source_input,
            select_stream="v:0",
            total_segments=total_segments,
        )
        audio_bins = route2_full_scan_packet_bins(
            source_input,
            select_stream="a:0",
            total_segments=total_segments,
        )
    finally:
        if native_session_id and native_access_token:
            try:
                close_native_playback_session(
                    settings,
                    session_id=native_session_id,
                    access_token=native_access_token,
                )
            except Exception:  # noqa: BLE001
                pass
    combined_bins = [video_bins[index] + audio_bins[index] for index in range(total_segments)]
    if any(combined_bins):
        return combined_bins
    average_segment_bytes = route2_profile_floor_segment_bytes(session.profile)
    return [average_segment_bytes] * total_segments


def _load_route2_full_preflight_cache_locked(
    session: MobilePlaybackSession,
    *,
    route2_full_preflight_cache_path,
) -> bool:
    cache_path = route2_full_preflight_cache_path(session)
    if not cache_path.exists():
        return False
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        source_bin_bytes = [max(0, int(value)) for value in payload.get("source_bin_bytes") or []]
    except (OSError, ValueError, TypeError):
        return False
    expected_segments = max(1, math.ceil(session.duration_seconds / SEGMENT_DURATION_SECONDS))
    if len(source_bin_bytes) != expected_segments:
        return False
    browser_session = session.browser_playback
    browser_session.full_source_bin_bytes = source_bin_bytes
    browser_session.full_preflight_state = "ready"
    browser_session.full_preflight_error = None
    browser_session.full_preflight_started_at_ts = 0.0
    return True


def _ensure_route2_full_preflight_locked(
    session: MobilePlaybackSession,
    *,
    load_route2_full_preflight_cache_locked,
    run_route2_full_preflight_worker,
) -> None:
    browser_session = session.browser_playback
    if browser_session.engine_mode != "route2" or browser_session.playback_mode != "full":
        browser_session.full_preflight_state = "idle"
        browser_session.full_preflight_error = None
        browser_session.full_source_bin_bytes.clear()
        return
    if browser_session.full_source_bin_bytes:
        browser_session.full_preflight_state = "ready"
        browser_session.full_preflight_error = None
        return
    if load_route2_full_preflight_cache_locked(session):
        return
    if browser_session.full_preflight_state == "running":
        return
    browser_session.full_preflight_state = "running"
    browser_session.full_preflight_started_at_ts = time.time()
    browser_session.full_preflight_error = None
    thread = threading.Thread(
        target=run_route2_full_preflight_worker,
        args=(session.session_id,),
        daemon=True,
        name=f"elvern-route2-full-preflight-{session.session_id[:8]}",
    )
    thread.start()


def _run_route2_full_preflight_worker(
    session_id: str,
    *,
    get_route2_session_locked,
    build_route2_full_source_bin_bytes,
    route2_full_preflight_cache_path,
    write_json_atomic,
) -> None:
    session = get_route2_session_locked(session_id)
    if session is None:
        return
    try:
        source_bin_bytes = build_route2_full_source_bin_bytes(session)
    except Exception as exc:  # noqa: BLE001
        active_session = get_route2_session_locked(session_id)
        if active_session is None:
            return
        browser_session = active_session.browser_playback
        browser_session.full_preflight_state = "failed"
        browser_session.full_preflight_error = str(exc) or "Full Playback preflight failed"
        return
    cache_path = route2_full_preflight_cache_path(session)
    write_json_atomic(
        cache_path,
        {
            "source_fingerprint": session.source_fingerprint,
            "profile": session.profile,
            "segment_duration_seconds": SEGMENT_DURATION_SECONDS,
            "source_bin_bytes": source_bin_bytes,
            "updated_at": utcnow_iso(),
        },
    )
    active_session = get_route2_session_locked(session_id)
    if active_session is None:
        return
    browser_session = active_session.browser_playback
    browser_session.full_source_bin_bytes = source_bin_bytes
    browser_session.full_preflight_state = "ready"
    browser_session.full_preflight_error = None
    browser_session.full_preflight_started_at_ts = 0.0
