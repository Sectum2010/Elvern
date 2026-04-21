from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path

from ..db import utcnow_iso
from .mobile_playback_models import MobilePlaybackSession, PlaybackEpoch


def _initialize_route2_session_locked(
    session: MobilePlaybackSession,
    *,
    build_route2_epoch_locked,
    ensure_route2_epoch_workspace_locked,
    ensure_route2_full_preflight_locked,
) -> None:
    browser_session = session.browser_playback
    browser_session.state = "starting"
    browser_session.attach_revision = 0
    browser_session.client_attach_revision = 0
    browser_session.attach_revision_issued_at_ts = 0.0
    browser_session.last_attach_warning_revision = 0
    browser_session.replacement_epoch_id = None
    browser_session.replacement_retry_not_before_ts = 0.0
    browser_session.full_preflight_error = None
    browser_session.full_prepare_started_at_ts = (
        time.time() if browser_session.playback_mode == "full" else 0.0
    )
    browser_session.client_probe_samples.clear()
    initial_epoch = build_route2_epoch_locked(session)
    browser_session.active_epoch_id = initial_epoch.epoch_id
    browser_session.epochs[initial_epoch.epoch_id] = initial_epoch
    ensure_route2_epoch_workspace_locked(initial_epoch)
    ensure_route2_full_preflight_locked(session)
    session.worker_state = "idle"
    session.pending_target_seconds = None
    session.ready_start_seconds = 0.0
    session.ready_end_seconds = 0.0
    session.state = "preparing"


def _build_route2_epoch_locked(
    route2_root: Path,
    session: MobilePlaybackSession,
    *,
    clamp_time,
) -> PlaybackEpoch:
    epoch_id = uuid.uuid4().hex
    epoch_dir = route2_root / "sessions" / session.session_id / "epochs" / epoch_id
    target_position_seconds = clamp_time(session.target_position_seconds, session.duration_seconds)
    epoch_start_seconds = max(0.0, round(target_position_seconds - 20.0, 2))
    return PlaybackEpoch(
        epoch_id=epoch_id,
        session_id=session.session_id,
        created_at=utcnow_iso(),
        target_position_seconds=target_position_seconds,
        epoch_start_seconds=epoch_start_seconds,
        attach_position_seconds=target_position_seconds,
        epoch_dir=epoch_dir,
        staging_dir=epoch_dir / "staging",
        published_dir=epoch_dir / "published",
        staging_manifest_path=(epoch_dir / "staging" / "ffmpeg.m3u8"),
        metadata_path=epoch_dir / "epoch.json",
        frontier_path=(epoch_dir / "published" / "frontier.json"),
        published_init_path=(epoch_dir / "published" / "init.mp4"),
    )


def _ensure_route2_epoch_workspace_locked(
    epoch: PlaybackEpoch,
    *,
    rebuild_route2_published_frontier_locked,
    write_route2_epoch_metadata_locked,
) -> None:
    epoch.epoch_dir.mkdir(parents=True, exist_ok=True)
    epoch.staging_dir.mkdir(parents=True, exist_ok=True)
    epoch.published_dir.mkdir(parents=True, exist_ok=True)
    rebuild_route2_published_frontier_locked(epoch)
    write_route2_epoch_metadata_locked(epoch)


def _terminate_route2_epoch_locked(
    epoch: PlaybackEpoch,
    *,
    workers: dict[str, str],
) -> None:
    epoch.stop_requested = True
    if epoch.active_worker_id:
        workers.pop(epoch.active_worker_id, None)
        epoch.active_worker_id = None
    process = epoch.process
    epoch.process = None
    if not process or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _discard_route2_epoch_locked(
    session: MobilePlaybackSession,
    epoch_id: str,
    *,
    terminate_route2_epoch_locked,
) -> None:
    browser_session = session.browser_playback
    epoch = browser_session.epochs.get(epoch_id)
    if epoch is None:
        if browser_session.replacement_epoch_id == epoch_id:
            browser_session.replacement_epoch_id = None
        return
    terminate_route2_epoch_locked(epoch)
    if browser_session.replacement_epoch_id == epoch_id:
        browser_session.replacement_epoch_id = None
    browser_session.epochs.pop(epoch_id, None)
    shutil.rmtree(epoch.epoch_dir, ignore_errors=True)
