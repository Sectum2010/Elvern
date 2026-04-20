from __future__ import annotations

import hashlib
import json
import logging
import math
import shutil
import statistics
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from ..config import Settings
from ..db import get_connection, utcnow_iso
from ..media_stream import ensure_media_path_within_root
from .mobile_playback_models import (
    BACKGROUND_EXPANSION_FORWARD_SECONDS,
    FRONTIER_WAIT_SECONDS,
    MANIFEST_ADVANCE_MIN_GROWTH_SECONDS,
    MANIFEST_ADVANCE_TRIGGER_SECONDS,
    MOBILE_PROFILES,
    PLAYBACK_COMMIT_RUNWAY_SECONDS,
    READY_AFTER_TARGET_SECONDS,
    ROUTE2_ATTACH_ACK_WARN_SECONDS,
    ROUTE2_ATTACH_READY_SECONDS,
    ROUTE2_DRAIN_IDLE_GRACE_SECONDS,
    ROUTE2_DRAIN_MAX_SECONDS,
    ROUTE2_ETA_DISPLAY_MAX_VOLATILITY_RATIO,
    ROUTE2_ETA_DISPLAY_MIN_GROWTH_EVENTS,
    ROUTE2_ETA_DISPLAY_MIN_OBSERVATION_SECONDS,
    ROUTE2_ETA_DISPLAY_STICKY_OBSERVATION_SECONDS,
    ROUTE2_FULL_GOODPUT_MIN_SAMPLE_COUNT,
    ROUTE2_FULL_RESERVE_BASE_SECONDS,
    ROUTE2_FULL_RESERVE_MAX_UNCERTAINTY_SECONDS,
    ROUTE2_FULL_RESERVE_MAX_VOLATILITY_SECONDS,
    ROUTE2_FULL_VOLATILITY_HORIZON_SECONDS,
    ROUTE2_RECOVERY_MIN_RUNWAY_SECONDS,
    ROUTE2_RECOVERY_MIN_SUPPLY_RATE_X,
    ROUTE2_RECOVERY_PROJECTION_HORIZON_SECONDS,
    ROUTE2_RECOVERY_RESUME_RUNWAY_SECONDS,
    ROUTE2_REPLACEMENT_RETRY_BACKOFF_SECONDS,
    ROUTE2_STARTUP_MIN_RUNWAY_SECONDS,
    ROUTE2_STARTUP_MIN_SUPPLY_RATE_X,
    ROUTE2_STARTUP_PROJECTION_HORIZON_SECONDS,
    ROUTE2_SUPPLY_RATE_FAST_EMA_ALPHA,
    ROUTE2_SUPPLY_RATE_SLOW_EMA_ALPHA,
    SEEK_PREROLL_SECONDS,
    SEGMENT_DURATION_SECONDS,
    STATUS_POLL_PREPARE_SECONDS,
    TARGET_WINDOW_FORWARD_SECONDS,
    TARGET_WINDOW_PREROLL_SECONDS,
    WATCH_LOW_WATERMARK_SECONDS,
    WATCH_REFILL_TARGET_SECONDS,
    WATCH_STALLED_RECOVERY_RUNWAY_SECONDS,
    BrowserPlaybackSession,
    CacheState,
    MobileClusterJob,
    MobilePlaybackSession,
    PlaybackEpoch,
)
from .mobile_playback_route2_metrics import (
    _route2_client_goodput_locked as _route2_client_goodput_locked_impl,
    _route2_effective_playhead_seconds_locked as _route2_effective_playhead_seconds_locked_impl,
    _route2_position_in_epoch_locked as _route2_position_in_epoch_locked_impl,
    _route2_recovery_target_locked as _route2_recovery_target_locked_impl,
    _route2_runtime_supply_metrics_locked as _route2_runtime_supply_metrics_locked_impl,
    _route2_server_byte_goodput_locked as _route2_server_byte_goodput_locked_impl,
    _route2_supply_model_locked as _route2_supply_model_locked_impl,
    _route2_supply_rate_x_locked as _route2_supply_rate_x_locked_impl,
)
from .mobile_playback_route2_samples import (
    _record_route2_byte_sample_locked as _record_route2_byte_sample_locked_impl,
    _record_route2_client_probe_sample_locked as _record_route2_client_probe_sample_locked_impl,
    _record_route2_frontier_sample_locked as _record_route2_frontier_sample_locked_impl,
    _route2_epoch_ready_end_seconds as _route2_epoch_ready_end_seconds_impl,
)
from .mobile_playback_route2_readiness import (
    _ahead_runway_seconds as _ahead_runway_seconds_impl,
    _playback_commit_is_ready as _playback_commit_is_ready_impl,
    _stalled_recovery_needed as _stalled_recovery_needed_impl,
    _starvation_risk as _starvation_risk_impl,
    _target_is_ready as _target_is_ready_impl,
    _watch_anchor_position as _watch_anchor_position_impl,
)
from .mobile_playback_route2_full_helpers import (
    _parse_bitrate_bps as _parse_bitrate_bps_impl,
    _route2_full_bootstrap_eta_locked as _route2_full_bootstrap_eta_locked_impl,
    _route2_full_budget_metrics_locked as _route2_full_budget_metrics_locked_impl,
    _route2_full_mode_requires_initial_attach_gate_locked as _route2_full_mode_requires_initial_attach_gate_locked_impl,
    _route2_full_prepare_elapsed_seconds_locked as _route2_full_prepare_elapsed_seconds_locked_impl,
    _route2_full_safe_calibration_ratio_locked as _route2_full_safe_calibration_ratio_locked_impl,
    _route2_profile_floor_bytes_per_second as _route2_profile_floor_bytes_per_second_impl,
    _route2_profile_floor_segment_bytes as _route2_profile_floor_segment_bytes_impl,
)
from .mobile_playback_route2_full_gate import (
    _route2_display_prepare_eta_locked as _route2_display_prepare_eta_locked_impl,
    _route2_full_mode_gate_locked as _route2_full_mode_gate_locked_impl,
)
from .mobile_playback_route2_gates import (
    _route2_attach_gate_state_locked as _route2_attach_gate_state_locked_impl,
    _route2_epoch_recovery_ready_locked as _route2_epoch_recovery_ready_locked_impl,
    _route2_epoch_startup_attach_ready_locked as _route2_epoch_startup_attach_ready_locked_impl,
)
from .mobile_playback_route2_recovery import (
    _route2_low_water_recovery_needed_locked as _route2_low_water_recovery_needed_locked_impl,
)
from .mobile_playback_route2_math import (
    _conservative_goodput_locked as _conservative_goodput_locked_impl,
    _ema_locked as _ema_locked_impl,
    _harmonic_mean_locked as _harmonic_mean_locked_impl,
    _percentile_locked as _percentile_locked_impl,
    _route2_projected_runway_seconds_locked as _route2_projected_runway_seconds_locked_impl,
    _route2_required_runway_seconds_locked as _route2_required_runway_seconds_locked_impl,
)
from .mobile_playback_source_service import (
    _resolve_duration_seconds as _resolve_duration_seconds_impl,
    _resolve_worker_source_input as _resolve_worker_source_input_impl,
)


logger = logging.getLogger(__name__)


class MobilePlaybackManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._sessions: dict[str, MobilePlaybackSession] = {}
        self._active_session_by_user: dict[int, str] = {}
        self._cache_states: dict[str, CacheState] = {}
        self._workers: dict[str, str] = {}
        self._manager_stop = threading.Event()
        self._manager_thread: threading.Thread | None = None
        self._session_root = self.settings.transcode_dir / "mobile_sessions"
        self._cache_root = self.settings.transcode_dir / "mobile_cache"
        self._route2_root = self.settings.transcode_dir / "browser_playback_route2"

    def start(self) -> None:
        self._session_root.mkdir(parents=True, exist_ok=True)
        self._cache_root.mkdir(parents=True, exist_ok=True)
        self._route2_root.mkdir(parents=True, exist_ok=True)
        (self._route2_root / "preflight").mkdir(parents=True, exist_ok=True)
        self._cleanup_orphaned_cache_dirs()
        if self._manager_thread is None:
            self._manager_thread = threading.Thread(
                target=self._manager_loop,
                daemon=True,
                name="elvern-mobile-playback-manager",
            )
            self._manager_thread.start()
        logger.info(
            "Mobile playback manager ready: root=%s cache=%s workers=%s",
            self._session_root,
            self._cache_root,
            self.settings.max_concurrent_mobile_workers,
        )

    def shutdown(self) -> None:
        self._manager_stop.set()
        if self._manager_thread and self._manager_thread.is_alive():
            self._manager_thread.join(timeout=2)
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._active_session_by_user.clear()
            self._workers.clear()
        for session in sessions:
            self._terminate_session(session, remove_session_dir=False)

    def create_session(
        self,
        item: dict[str, object],
        *,
        user_id: int,
        profile: str = "mobile_1080p",
        start_position_seconds: float = 0.0,
        engine_mode: str | None = None,
        playback_mode: str | None = None,
    ) -> dict[str, object]:
        self._validate_transcoding()
        profile_key = self._normalize_profile(profile)
        selected_engine_mode = self._select_engine_mode(engine_mode)
        selected_playback_mode = self._select_playback_mode(playback_mode)
        if selected_engine_mode != "route2" and selected_playback_mode != "lite":
            raise ValueError("Full Playback requires Browser Playback Route 2")
        source_kind = str(item.get("source_kind") or "local")
        if source_kind == "local":
            source_locator = str(
                ensure_media_path_within_root(Path(str(item["file_path"])), self.settings)
            )
            source_input_kind = "path"
        else:
            source_locator = str(item.get("file_path") or "").strip()
            if not source_locator:
                raise ValueError("Experimental playback requires a valid cloud media source")
            source_input_kind = "url"
        duration_seconds, item = _resolve_duration_seconds_impl(
            self.settings,
            item,
            user_id=user_id,
        )
        if not duration_seconds or duration_seconds <= 0:
            raise ValueError("Experimental playback requires a known duration")
        source_fingerprint = self._source_fingerprint(item, source_locator)
        cache_key = self._build_cache_key(source_fingerprint, profile_key)

        now = utcnow_iso()
        now_ts = time.time()
        target_position_seconds = self._clamp_time(start_position_seconds, duration_seconds)

        with self._lock:
            existing_session_id = self._active_session_by_user.get(user_id)
            existing_session = self._sessions.get(existing_session_id) if existing_session_id else None

        if (
            existing_session
            and existing_session.state not in {"failed", "stopped", "expired"}
            and existing_session.media_item_id == int(item["id"])
            and existing_session.profile == profile_key
            and existing_session.browser_playback.engine_mode == selected_engine_mode
            and existing_session.browser_playback.playback_mode == selected_playback_mode
        ):
            self.touch_session(existing_session.session_id, user_id=user_id, media_access=True)
            if (
                selected_engine_mode == "legacy"
                and abs(existing_session.target_position_seconds - target_position_seconds) > SEGMENT_DURATION_SECONDS
            ):
                return self.seek_session(
                    existing_session.session_id,
                    user_id=user_id,
                    target_position_seconds=target_position_seconds,
                    last_stable_position_seconds=existing_session.last_stable_position_seconds,
                    playing_before_seek=False,
                )
            if (
                selected_engine_mode == "route2"
                and abs(existing_session.target_position_seconds - target_position_seconds) > SEGMENT_DURATION_SECONDS
            ):
                self.stop_session(existing_session.session_id, user_id=user_id)
                existing_session = None
            else:
                return self.get_session(existing_session.session_id, user_id=user_id)

        if existing_session:
            self.stop_session(existing_session.session_id, user_id=user_id)

        session_id = uuid.uuid4().hex
        session = MobilePlaybackSession(
            session_id=session_id,
            user_id=user_id,
            media_item_id=int(item["id"]),
            profile=profile_key,
            duration_seconds=duration_seconds,
            cache_key=cache_key,
            source_locator=source_locator,
            source_input_kind=source_input_kind,
            source_fingerprint=source_fingerprint,
            created_at=now,
            last_client_seen_at=now,
            last_media_access_at=now,
            target_position_seconds=target_position_seconds,
            last_stable_position_seconds=target_position_seconds,
            committed_playhead_seconds=target_position_seconds,
            actual_media_element_time_seconds=target_position_seconds,
            expires_at_ts=now_ts + (self.settings.mobile_session_ttl_minutes * 60),
            browser_playback=self._build_browser_playback_session(
                engine_mode=selected_engine_mode,
                playback_mode=selected_playback_mode,
            ),
        )
        if selected_engine_mode == "route2":
            with self._lock:
                self._initialize_route2_session_locked(session)
                self._sessions[session.session_id] = session
                self._active_session_by_user[user_id] = session.session_id
            return self.get_session(session.session_id, user_id=user_id)

        cache_state = self._load_cache_state(
            cache_key=cache_key,
            profile=profile_key,
            duration_seconds=duration_seconds,
            source_fingerprint=source_fingerprint,
        )
        with self._lock:
            self._refresh_ready_window_locked(session, cache_state)
            if not self._target_is_ready(session):
                session.pending_target_seconds = target_position_seconds
                session.active_job = self._build_target_cluster_job(session)
            self._transition_session_state_locked(session)
            self._sessions[session.session_id] = session
            self._active_session_by_user[user_id] = session.session_id
        self._ensure_worker_for_session(session.session_id)
        return self.get_session(session.session_id, user_id=user_id)

    def get_session(self, session_id: str, *, user_id: int) -> dict[str, object]:
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id)
            if session.browser_playback.engine_mode == "route2":
                self._touch_session_locked(session, media_access=False)
                self._refresh_route2_session_authority_locked(session)
                return self._route2_snapshot_locked(session)
            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            self._touch_session_locked(session, media_access=False)
            self._refresh_ready_window_locked(session, cache_state)
            self._transition_session_state_locked(session)
            return self._snapshot_locked(session, cache_state)

    def get_active_session(self, *, user_id: int) -> dict[str, object] | None:
        with self._lock:
            session_id = self._active_session_by_user.get(user_id)
            session = self._sessions.get(session_id) if session_id else None
            if session is None or session.state in {"failed", "stopped", "expired"}:
                return None
            if session.browser_playback.engine_mode == "route2":
                self._touch_session_locked(session, media_access=False)
                self._refresh_route2_session_authority_locked(session)
                return self._route2_snapshot_locked(session)
            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            self._touch_session_locked(session, media_access=False)
            self._refresh_ready_window_locked(session, cache_state)
            self._transition_session_state_locked(session)
            return self._snapshot_locked(session, cache_state)

    def get_active_session_for_item(self, item_id: int, *, user_id: int) -> dict[str, object] | None:
        with self._lock:
            session_id = self._active_session_by_user.get(user_id)
            session = self._sessions.get(session_id) if session_id else None
            if (
                session is None
                or session.media_item_id != item_id
                or session.state in {"failed", "stopped", "expired"}
            ):
                return None
            if session.browser_playback.engine_mode == "route2":
                self._touch_session_locked(session, media_access=False)
                self._refresh_route2_session_authority_locked(session)
                return self._route2_snapshot_locked(session)
            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            self._touch_session_locked(session, media_access=False)
            self._refresh_ready_window_locked(session, cache_state)
            self._transition_session_state_locked(session)
            return self._snapshot_locked(session, cache_state)

    def seek_session(
        self,
        session_id: str,
        *,
        user_id: int,
        target_position_seconds: float,
        last_stable_position_seconds: float | None = None,
        playing_before_seek: bool | None = None,
    ) -> dict[str, object]:
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id)
            if session.browser_playback.engine_mode == "route2":
                target = self._clamp_time(target_position_seconds, session.duration_seconds)
                stable_position = self._clamp_time(
                    last_stable_position_seconds
                    if last_stable_position_seconds is not None
                    else session.last_stable_position_seconds,
                    session.duration_seconds,
                )
                browser_session = session.browser_playback
                active_epoch = (
                    browser_session.epochs.get(browser_session.active_epoch_id)
                    if browser_session.active_epoch_id
                    else None
                )
                if active_epoch is None:
                    raise ValueError("Browser Playback Route 2 active epoch is missing")
                self._rebuild_route2_published_frontier_locked(active_epoch)
                session.last_stable_position_seconds = stable_position
                session.committed_playhead_seconds = stable_position
                session.actual_media_element_time_seconds = stable_position
                if playing_before_seek is not None:
                    session.playing_before_seek = bool(playing_before_seek)
                    session.client_is_playing = bool(playing_before_seek)
                session.lifecycle_state = "attached"
                session.stalled_recovery_requested = False
                session.last_error = None
                if self._route2_position_in_epoch_locked(session, active_epoch, target):
                    if browser_session.replacement_epoch_id:
                        self._discard_route2_epoch_locked(session, browser_session.replacement_epoch_id)
                    session.target_position_seconds = target
                    session.pending_target_seconds = None
                    active_epoch.attach_position_seconds = target
                    self._write_route2_epoch_metadata_locked(active_epoch)
                    self._refresh_route2_session_authority_locked(session)
                    return self._route2_snapshot_locked(session)
                self._create_route2_replacement_epoch_locked(
                    session,
                    target_position_seconds=target,
                    reason="out_of_range_seek",
                )
                self._refresh_route2_session_authority_locked(session)
                return self._route2_snapshot_locked(session)
            target = self._clamp_time(target_position_seconds, session.duration_seconds)
            stable_position = self._clamp_time(
                last_stable_position_seconds
                if last_stable_position_seconds is not None
                else session.last_stable_position_seconds,
                session.duration_seconds,
            )
            old_job = session.active_job
            if old_job is not None:
                old_job.superseded = True
                self._terminate_job_locked(session, old_job, remove_output=True)
                session.active_job = None

            session.epoch += 1
            session.target_position_seconds = target
            session.pending_target_seconds = target
            session.manifest_start_segment = None
            session.manifest_end_segment = None
            session.last_stable_position_seconds = stable_position
            session.committed_playhead_seconds = stable_position
            session.actual_media_element_time_seconds = stable_position
            session.last_refill_start_seconds = None
            session.last_refill_end_seconds = None
            if playing_before_seek is not None:
                session.playing_before_seek = bool(playing_before_seek)
                session.client_is_playing = bool(playing_before_seek)
            session.lifecycle_state = "attached"
            session.stalled_recovery_requested = False
            session.last_error = None
            session.worker_state = "idle"
            session.queue_started_ts = None

            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            self._refresh_ready_window_locked(session, cache_state)
            if not self._target_is_ready(session):
                session.active_job = self._build_target_cluster_job(session)
            self._transition_session_state_locked(session)
        self._ensure_worker_for_session(session_id)
        return self.get_session(session_id, user_id=user_id)

    def update_runtime(
        self,
        session_id: str,
        *,
        user_id: int,
        committed_playhead_seconds: float | None = None,
        actual_media_element_time_seconds: float | None = None,
        client_attach_revision: int | None = None,
        client_probe_bytes: int | None = None,
        client_probe_duration_ms: int | None = None,
        lifecycle_state: str | None = None,
        stalled: bool | None = None,
        playing: bool | None = None,
    ) -> dict[str, object]:
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id)
            if session.browser_playback.engine_mode == "route2":
                if committed_playhead_seconds is not None:
                    session.committed_playhead_seconds = self._clamp_time(
                        committed_playhead_seconds,
                        session.duration_seconds,
                    )
                    session.last_stable_position_seconds = session.committed_playhead_seconds
                if actual_media_element_time_seconds is not None:
                    session.actual_media_element_time_seconds = self._clamp_time(
                        actual_media_element_time_seconds,
                        session.duration_seconds,
                    )
                if lifecycle_state:
                    session.lifecycle_state = lifecycle_state
                if playing is not None:
                    session.client_is_playing = bool(playing)
                browser_session = session.browser_playback
                if client_attach_revision is not None:
                    coerced_revision = max(0, int(client_attach_revision))
                    browser_session.client_attach_revision = min(
                        browser_session.attach_revision,
                        max(browser_session.client_attach_revision, coerced_revision),
                    )
                self._record_route2_client_probe_sample_locked(
                    session,
                    probe_bytes=client_probe_bytes,
                    probe_duration_ms=client_probe_duration_ms,
                )
                self._touch_session_locked(session, media_access=bool(playing))
                self._refresh_route2_session_authority_locked(session)
                return self._route2_snapshot_locked(session)
            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            if committed_playhead_seconds is not None:
                committed = self._clamp_time(committed_playhead_seconds, session.duration_seconds)
                session.committed_playhead_seconds = committed
                if session.pending_target_seconds is None:
                    session.last_stable_position_seconds = committed
            if actual_media_element_time_seconds is not None:
                session.actual_media_element_time_seconds = self._clamp_time(
                    actual_media_element_time_seconds,
                    session.duration_seconds,
                )
            if lifecycle_state:
                session.lifecycle_state = lifecycle_state
            if playing is not None:
                session.client_is_playing = bool(playing)
            if stalled is True:
                session.stalled_recovery_requested = True
            elif stalled is False and session.lifecycle_state == "attached":
                session.stalled_recovery_requested = False
            self._touch_session_locked(session, media_access=bool(playing))
            self._refresh_ready_window_locked(session, cache_state)
            self._maybe_advance_manifest_window_locked(session)
            self._transition_session_state_locked(session)
        self._ensure_worker_for_session(session_id)
        return self.get_session(session_id, user_id=user_id)

    def stop_session(self, session_id: str, *, user_id: int) -> bool:
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id, allow_missing=True)
            if session is None:
                return False
            self._sessions.pop(session.session_id, None)
            if self._active_session_by_user.get(user_id) == session.session_id:
                self._active_session_by_user.pop(user_id, None)
        self._terminate_session(session)
        return True

    def touch_session(self, session_id: str, *, user_id: int, media_access: bool) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.user_id != user_id:
                return
            self._touch_session_locked(session, media_access=media_access)

    def get_manifest_content(self, session_id: str, *, user_id: int) -> str:
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id)
            if session.browser_playback.engine_mode == "route2":
                raise ValueError("Browser Playback Route 2 manifest serving is not active yet")
            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            self._touch_session_locked(session, media_access=True)
            self._refresh_ready_window_locked(session, cache_state)
            self._transition_session_state_locked(session)
            manifest_start_segment, manifest_end_segment, total_segments = self._resolve_manifest_window_locked(
                session,
                cache_state,
            )
            target_position_seconds = session.target_position_seconds
            duration_seconds = session.duration_seconds
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:7",
            f"#EXT-X-TARGETDURATION:{math.ceil(SEGMENT_DURATION_SECONDS)}",
            f"#EXT-X-MEDIA-SEQUENCE:{manifest_start_segment}",
            "#EXT-X-PLAYLIST-TYPE:VOD",
            "#EXT-X-INDEPENDENT-SEGMENTS",
            '#EXT-X-MAP:URI="init.mp4"',
        ]
        manifest_start_seconds = manifest_start_segment * SEGMENT_DURATION_SECONDS
        start_offset_seconds = max(0.0, target_position_seconds - manifest_start_seconds)
        if start_offset_seconds > 0.05:
            lines.append(f"#EXT-X-START:TIME-OFFSET={start_offset_seconds:.3f},PRECISE=YES")
        for index in range(manifest_start_segment, manifest_end_segment + 1):
            duration = min(
                SEGMENT_DURATION_SECONDS,
                max(duration_seconds - (index * SEGMENT_DURATION_SECONDS), 0.0),
            )
            lines.append(f"#EXTINF:{duration:.3f},")
            lines.append(f"segments/{index}.m4s")
        lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines) + "\n"

    def get_route2_epoch_manifest_content(self, epoch_id: str, *, user_id: int) -> str:
        with self._lock:
            session, epoch = self._get_owned_route2_epoch_locked(epoch_id, user_id)
            self._prepare_route2_epoch_access_locked(session, epoch, media_kind="manifest")
            self._rebuild_route2_published_frontier_locked(epoch)
            if not epoch.init_published or epoch.contiguous_published_through_segment is None:
                if epoch.state in {"attach_ready", "active", "draining"}:
                    self._log_route2_truth_violation(
                        "manifest_without_published_frontier",
                        session=session,
                        epoch=epoch,
                    )
                raise FileNotFoundError("Route 2 epoch manifest is not published yet")
            ready_start_seconds = epoch.epoch_start_seconds
            attach_offset_seconds = max(0.0, epoch.attach_position_seconds - ready_start_seconds)
            total_epoch_segments = max(
                1,
                math.ceil(max(session.duration_seconds - epoch.epoch_start_seconds, 0.0) / SEGMENT_DURATION_SECONDS),
            )
            manifest_end_segment = min(epoch.contiguous_published_through_segment, total_epoch_segments - 1)
            manifest_complete = epoch.transcoder_completed and manifest_end_segment >= (total_epoch_segments - 1)
            lines = [
                "#EXTM3U",
                "#EXT-X-VERSION:7",
                f"#EXT-X-TARGETDURATION:{math.ceil(SEGMENT_DURATION_SECONDS)}",
                "#EXT-X-MEDIA-SEQUENCE:0",
                "#EXT-X-PLAYLIST-TYPE:EVENT",
                "#EXT-X-INDEPENDENT-SEGMENTS",
                '#EXT-X-MAP:URI="init.mp4"',
            ]
            # Route 2 manifests stay open while the epoch transcoder publishes ahead.
            # Always emit the authoritative attach point so clients do not drift to the
            # published frontier and start behaving like a live-edge stream.
            lines.append(f"#EXT-X-START:TIME-OFFSET={attach_offset_seconds:.3f},PRECISE=YES")
            for index in range(0, manifest_end_segment + 1):
                segment_start_seconds = epoch.epoch_start_seconds + (index * SEGMENT_DURATION_SECONDS)
                duration = min(
                    SEGMENT_DURATION_SECONDS,
                    max(session.duration_seconds - segment_start_seconds, 0.0),
                )
                lines.append(f"#EXTINF:{duration:.3f},")
                lines.append(f"segments/{index}.m4s")
            if manifest_complete:
                lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines) + "\n"

    def get_init_path(self, session_id: str, *, user_id: int) -> Path:
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id)
            if session.browser_playback.engine_mode == "route2":
                raise ValueError("Browser Playback Route 2 init serving is not active yet")
            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            self._touch_session_locked(session, media_access=True)
            active_output_dir = session.active_job.output_dir if session.active_job else None
        if cache_state.init_path.exists():
            return cache_state.init_path
        if active_output_dir:
            candidate = active_output_dir / "init.mp4"
            if candidate.exists():
                self._publish_init_to_cache(cache_state, candidate)
                return cache_state.init_path if cache_state.init_path.exists() else candidate
        deadline = time.time() + FRONTIER_WAIT_SECONDS
        while time.time() < deadline:
            if cache_state.init_path.exists():
                return cache_state.init_path
            if active_output_dir:
                candidate = active_output_dir / "init.mp4"
                if candidate.exists():
                    self._publish_init_to_cache(cache_state, candidate)
                    return cache_state.init_path if cache_state.init_path.exists() else candidate
            time.sleep(0.1)
        raise FileNotFoundError("Experimental playback init segment is not ready yet")

    def get_route2_epoch_init_path(self, epoch_id: str, *, user_id: int) -> Path:
        with self._lock:
            session, epoch = self._get_owned_route2_epoch_locked(epoch_id, user_id)
            self._prepare_route2_epoch_access_locked(session, epoch, media_kind="init")
            self._rebuild_route2_published_frontier_locked(epoch)
            if not epoch.init_published or not epoch.published_init_path.exists():
                if epoch.state in {"attach_ready", "active", "draining"}:
                    self._log_route2_truth_violation(
                        "published_init_missing",
                        session=session,
                        epoch=epoch,
                        published_init_path=str(epoch.published_init_path),
                    )
                raise FileNotFoundError("Route 2 epoch init segment is not published yet")
            return epoch.published_init_path

    def get_segment_path(
        self,
        session_id: str,
        segment_index: int,
        *,
        user_id: int,
    ) -> Path:
        if segment_index < 0:
            raise FileNotFoundError("Experimental playback segment not found")
        with self._lock:
            session = self._get_owned_session_locked(session_id, user_id)
            if session.browser_playback.engine_mode == "route2":
                raise ValueError("Browser Playback Route 2 segment serving is not active yet")
        deadline = time.time() + FRONTIER_WAIT_SECONDS
        while time.time() < deadline:
            should_wait_for_frontier = False
            with self._lock:
                session = self._get_owned_session_locked(session_id, user_id)
                cache_state = self._load_cache_state_locked(
                    cache_key=session.cache_key,
                    profile=session.profile,
                    duration_seconds=session.duration_seconds,
                    source_fingerprint=session.source_fingerprint,
                )
                self._touch_session_locked(session, media_access=True)
                self._refresh_ready_window_locked(session, cache_state)
                self._transition_session_state_locked(session)
                active_job = session.active_job
                active_output_dir = active_job.output_dir if active_job else None
                cached = cache_state.cache_dir / f"segment_{segment_index:06d}.m4s"
                if cached.exists():
                    return cached
                if active_output_dir:
                    candidate = active_output_dir / f"segment_{segment_index:06d}.m4s"
                    if candidate.exists():
                        self._publish_segment_to_cache_locked(cache_state, segment_index, candidate)
                        self._write_cache_metadata_locked(cache_state)
                        return cached if cached.exists() else candidate
                available = self._combined_available_segments_locked(session, cache_state)
                frontier_segment = max(available) if available else -1
                wait_for_segment = (
                    active_job is not None
                    and active_job.prepare_start_segment <= segment_index <= active_job.prepare_end_segment
                )
                should_wait_for_frontier = (
                    session.state in {"queued", "preparing", "retargeting", "ready"}
                    and segment_index > frontier_segment
                    and segment_index <= frontier_segment + math.ceil(WATCH_REFILL_TARGET_SECONDS / SEGMENT_DURATION_SECONDS)
                )
            if not wait_for_segment and not should_wait_for_frontier:
                raise FileNotFoundError("Experimental playback segment is not cached yet")
            if should_wait_for_frontier:
                self._ensure_worker_for_session(session_id)
            time.sleep(0.1)
        raise FileNotFoundError("Experimental playback segment is not ready yet")

    def get_route2_epoch_segment_path(
        self,
        epoch_id: str,
        segment_index: int,
        *,
        user_id: int,
    ) -> Path:
        if segment_index < 0:
            raise FileNotFoundError("Route 2 epoch segment not found")
        with self._lock:
            session, epoch = self._get_owned_route2_epoch_locked(epoch_id, user_id)
            self._prepare_route2_epoch_access_locked(session, epoch, media_kind="segment")
            self._rebuild_route2_published_frontier_locked(epoch)
            if not epoch.init_published or epoch.contiguous_published_through_segment is None:
                if epoch.state in {"attach_ready", "active", "draining"}:
                    self._log_route2_truth_violation(
                        "segment_requested_without_frontier",
                        session=session,
                        epoch=epoch,
                        segment_index=segment_index,
                    )
                raise FileNotFoundError("Route 2 epoch segment is not published yet")
            total_epoch_segments = max(
                1,
                math.ceil(max(session.duration_seconds - epoch.epoch_start_seconds, 0.0) / SEGMENT_DURATION_SECONDS),
            )
            if segment_index >= total_epoch_segments:
                raise FileNotFoundError("Route 2 epoch segment is not published yet")
            if segment_index > epoch.contiguous_published_through_segment:
                raise FileNotFoundError("Route 2 epoch segment is not published yet")
            segment_path = self._route2_segment_destination(epoch, segment_index)
            if not segment_path.exists():
                self._log_route2_truth_violation(
                    "published_segment_missing",
                    session=session,
                    epoch=epoch,
                    segment_index=segment_index,
                    expected_path=str(segment_path),
                )
                raise FileNotFoundError("Route 2 epoch segment is not published yet")
            return segment_path

    def _validate_transcoding(self) -> None:
        if not self.settings.transcode_enabled:
            raise ValueError("Experimental playback is disabled on this server")
        if not self.settings.ffmpeg_path:
            raise ValueError("ffmpeg was not found on the server")

    def _select_engine_mode(self, value: str | None) -> str:
        candidate = (value or "").strip().lower()
        if not candidate:
            candidate = "route2" if self.settings.browser_playback_route2_enabled else "legacy"
        if candidate not in {"legacy", "route2"}:
            raise ValueError("Unsupported browser playback engine mode")
        if candidate == "route2":
            if not self.settings.browser_playback_route2_enabled:
                raise ValueError("Browser Playback Route 2 is disabled on this server")
        return candidate

    def _select_playback_mode(self, value: str | None) -> str:
        candidate = (value or "").strip().lower()
        if not candidate:
            return "lite"
        if candidate not in {"lite", "full"}:
            raise ValueError("Unsupported browser playback mode")
        return candidate

    def _build_browser_playback_session(self, *, engine_mode: str, playback_mode: str) -> BrowserPlaybackSession:
        return BrowserPlaybackSession(
            engine_mode=engine_mode,
            playback_mode=playback_mode,
            state="legacy" if engine_mode == "legacy" else "starting",
        )

    def _initialize_route2_session_locked(self, session: MobilePlaybackSession) -> None:
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
        initial_epoch = self._build_route2_epoch_locked(session)
        browser_session.active_epoch_id = initial_epoch.epoch_id
        browser_session.epochs[initial_epoch.epoch_id] = initial_epoch
        self._ensure_route2_epoch_workspace_locked(initial_epoch)
        self._ensure_route2_full_preflight_locked(session)
        session.worker_state = "idle"
        session.pending_target_seconds = None
        session.ready_start_seconds = 0.0
        session.ready_end_seconds = 0.0
        session.state = "preparing"

    def _build_route2_epoch_locked(self, session: MobilePlaybackSession) -> PlaybackEpoch:
        epoch_id = uuid.uuid4().hex
        epoch_dir = self._route2_root / "sessions" / session.session_id / "epochs" / epoch_id
        target_position_seconds = self._clamp_time(session.target_position_seconds, session.duration_seconds)
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

    def _log_route2_event(
        self,
        event: str,
        *,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch | None = None,
        level: int = logging.INFO,
        **details: object,
    ) -> None:
        payload: dict[str, object] = {
            "event": event,
            "session_id": session.session_id,
            "media_item_id": session.media_item_id,
            "engine_mode": session.browser_playback.engine_mode,
            "playback_mode": session.browser_playback.playback_mode,
            "session_state": session.state,
            "browser_session_state": session.browser_playback.state,
            "attach_revision": session.browser_playback.attach_revision,
            "client_attach_revision": session.browser_playback.client_attach_revision,
            "active_epoch_id": session.browser_playback.active_epoch_id,
            "replacement_epoch_id": session.browser_playback.replacement_epoch_id,
        }
        if epoch is not None:
            payload.update(
                {
                    "epoch_id": epoch.epoch_id,
                    "epoch_state": epoch.state,
                    "epoch_start_seconds": round(epoch.epoch_start_seconds, 2),
                    "attach_position_seconds": round(epoch.attach_position_seconds, 2),
                    "published_frontier_segment": epoch.contiguous_published_through_segment,
                }
            )
        payload.update(details)
        logger.log(level, "Route2 %s", json.dumps(payload, sort_keys=True, default=str))

    def _log_route2_truth_violation(
        self,
        violation: str,
        *,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        **details: object,
    ) -> None:
        self._log_route2_event(
            "truth_violation",
            session=session,
            epoch=epoch,
            level=logging.WARNING,
            violation=violation,
            **details,
        )

    def _guard_route2_full_attach_boundary_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch | None,
        *,
        attach_eligible: bool,
        guard_path: str,
    ) -> bool:
        browser_session = session.browser_playback
        if not attach_eligible:
            return False
        if (
            browser_session.engine_mode != "route2"
            or browser_session.playback_mode != "full"
            or epoch is None
        ):
            return True
        full_mode_gate = self._route2_full_mode_gate_locked(session, epoch)
        if bool(full_mode_gate["mode_ready"]):
            browser_session.last_full_contract_violation_signature = ""
            return True
        signature = (
            f"{guard_path}:{epoch.epoch_id}:{browser_session.attach_revision}:"
            f"{full_mode_gate.get('mode_state')}"
        )
        if browser_session.last_full_contract_violation_signature != signature:
            browser_session.last_full_contract_violation_signature = signature
            self._log_route2_event(
                "full_contract_violation_blocked",
                session=session,
                epoch=epoch,
                level=logging.ERROR,
                violation="full_attach_without_mode_ready",
                guard_path=guard_path,
                attempted_attach_eligible=attach_eligible,
                mode_ready=False,
                mode_state=str(full_mode_gate.get("mode_state") or "unknown"),
                mode_estimate_seconds=full_mode_gate.get("mode_estimate_seconds"),
                mode_estimate_source=str(full_mode_gate.get("mode_estimate_source") or "none"),
            )
        return False

    def _route2_full_preflight_cache_path(self, session: MobilePlaybackSession) -> Path:
        return _route2_full_preflight_cache_path_impl(
            self._route2_root,
            session,
        )

    def _parse_bitrate_bps(self, value: str) -> int:
        return _parse_bitrate_bps_impl(value)

    def _route2_profile_floor_bytes_per_second(self, profile_key: str) -> float:
        return _route2_profile_floor_bytes_per_second_impl(profile_key)

    def _route2_profile_floor_segment_bytes(self, profile_key: str) -> int:
        return _route2_profile_floor_segment_bytes_impl(profile_key)

    def _route2_full_preflight_source_input(self, session: MobilePlaybackSession) -> tuple[str, str | None, str | None]:
        return _route2_full_preflight_source_input_impl(
            self.settings,
            session,
        )

    def _route2_full_scan_packet_bins(
        self,
        source_input: str,
        *,
        select_stream: str,
        total_segments: int,
    ) -> list[int]:
        return _route2_full_scan_packet_bins_impl(
            self.settings,
            source_input,
            select_stream=select_stream,
            total_segments=total_segments,
        )

    def _build_route2_full_source_bin_bytes(self, session: MobilePlaybackSession) -> list[int]:
        return _build_route2_full_source_bin_bytes_impl(
            self.settings,
            session,
            route2_full_preflight_source_input=self._route2_full_preflight_source_input,
            route2_full_scan_packet_bins=self._route2_full_scan_packet_bins,
            route2_profile_floor_segment_bytes=self._route2_profile_floor_segment_bytes,
        )

    def _load_route2_full_preflight_cache_locked(self, session: MobilePlaybackSession) -> bool:
        return _load_route2_full_preflight_cache_locked_impl(
            session,
            route2_full_preflight_cache_path=self._route2_full_preflight_cache_path,
        )

    def _ensure_route2_full_preflight_locked(self, session: MobilePlaybackSession) -> None:
        _ensure_route2_full_preflight_locked_impl(
            session,
            load_route2_full_preflight_cache_locked=self._load_route2_full_preflight_cache_locked,
            run_route2_full_preflight_worker=self._run_route2_full_preflight_worker,
        )

    def _run_route2_full_preflight_worker(self, session_id: str) -> None:
        def get_route2_session_locked(active_session_id: str) -> MobilePlaybackSession | None:
            with self._lock:
                session = self._sessions.get(active_session_id)
                if session is None or session.browser_playback.engine_mode != "route2":
                    return None
                return session

        _run_route2_full_preflight_worker_impl(
            session_id,
            get_route2_session_locked=get_route2_session_locked,
            build_route2_full_source_bin_bytes=self._build_route2_full_source_bin_bytes,
            route2_full_preflight_cache_path=self._route2_full_preflight_cache_path,
            write_json_atomic=self._write_json_atomic,
        )

    def _issue_route2_attach_revision_locked(
        self,
        session: MobilePlaybackSession,
        *,
        next_revision: int,
        reason: str,
        epoch: PlaybackEpoch | None = None,
    ) -> None:
        browser_session = session.browser_playback
        next_value = max(0, int(next_revision))
        if browser_session.attach_revision == next_value:
            return
        if next_value > 0 and not self._guard_route2_full_attach_boundary_locked(
            session,
            epoch,
            attach_eligible=True,
            guard_path=f"issue_attach_revision:{reason}",
        ):
            return
        browser_session.attach_revision = next_value
        browser_session.attach_revision_issued_at_ts = time.time() if next_value > 0 else 0.0
        browser_session.last_attach_warning_revision = 0
        self._log_route2_event(
            "attach_revision_issued",
            session=session,
            epoch=epoch,
            reason=reason,
            next_revision=next_value,
        )

    def _mark_route2_epoch_draining_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        *,
        reason: str,
        required_client_revision: int | None = None,
    ) -> None:
        previous_state = epoch.state
        now_ts = time.time()
        epoch.state = "draining"
        if epoch.drain_started_at_ts is None:
            epoch.drain_started_at_ts = now_ts
        epoch.last_media_access_at_ts = max(epoch.last_media_access_at_ts, now_ts)
        if required_client_revision is not None:
            epoch.drain_target_attach_revision = max(
                epoch.drain_target_attach_revision,
                int(required_client_revision),
            )
        if previous_state != "draining":
            self._log_route2_event(
                "epoch_draining",
                session=session,
                epoch=epoch,
                reason=reason,
                required_client_revision=epoch.drain_target_attach_revision or None,
            )

    def _route2_epoch_is_draining_expired_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        *,
        now_ts: float | None = None,
    ) -> bool:
        if epoch.state != "draining":
            return False
        if session.browser_playback.active_epoch_id == epoch.epoch_id:
            return False
        now_ts = now_ts or time.time()
        drain_started_at_ts = epoch.drain_started_at_ts or now_ts
        last_media_access_at_ts = epoch.last_media_access_at_ts or drain_started_at_ts
        client_caught_up = (
            epoch.drain_target_attach_revision > 0
            and session.browser_playback.client_attach_revision >= epoch.drain_target_attach_revision
        )
        if client_caught_up and now_ts - last_media_access_at_ts >= ROUTE2_DRAIN_IDLE_GRACE_SECONDS:
            return True
        return now_ts - drain_started_at_ts >= ROUTE2_DRAIN_MAX_SECONDS

    def _cleanup_route2_draining_epochs_locked(
        self,
        session: MobilePlaybackSession,
        *,
        now_ts: float | None = None,
    ) -> None:
        browser_session = session.browser_playback
        now_ts = now_ts or time.time()
        for epoch_id, epoch in list(browser_session.epochs.items()):
            if epoch_id in {browser_session.active_epoch_id, browser_session.replacement_epoch_id}:
                continue
            if not self._route2_epoch_is_draining_expired_locked(session, epoch, now_ts=now_ts):
                continue
            self._log_route2_event(
                "epoch_drain_expired",
                session=session,
                epoch=epoch,
                drain_started_at_ts=epoch.drain_started_at_ts,
                last_media_access_at_ts=epoch.last_media_access_at_ts,
                drain_target_attach_revision=epoch.drain_target_attach_revision or None,
            )
            self._discard_route2_epoch_locked(session, epoch_id)

    def _prepare_route2_epoch_access_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        *,
        media_kind: str,
    ) -> None:
        now_ts = time.time()
        self._touch_session_locked(session, media_access=True)
        self._cleanup_route2_draining_epochs_locked(session, now_ts=now_ts)
        browser_session = session.browser_playback
        if epoch.epoch_id == browser_session.active_epoch_id:
            epoch.last_media_access_at_ts = now_ts
            return
        if epoch.state == "draining":
            if self._route2_epoch_is_draining_expired_locked(session, epoch, now_ts=now_ts):
                self._log_route2_event(
                    "stale_epoch_request",
                    session=session,
                    epoch=epoch,
                    level=logging.WARNING,
                    media_kind=media_kind,
                    reason="draining_epoch_expired",
                )
                self._discard_route2_epoch_locked(session, epoch.epoch_id)
                raise FileNotFoundError("Route 2 epoch is no longer active")
            epoch.last_media_access_at_ts = now_ts
            return
        self._log_route2_event(
            "stale_epoch_request",
            session=session,
            epoch=epoch,
            level=logging.WARNING,
            media_kind=media_kind,
            reason="inactive_epoch_request",
        )
        raise FileNotFoundError("Route 2 epoch is no longer active")

    def _route2_epoch_ready_end_seconds(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> float:
        return _route2_epoch_ready_end_seconds_impl(
            session,
            epoch,
        )

    def _record_route2_frontier_sample_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        *,
        now_ts: float | None = None,
    ) -> None:
        return _record_route2_frontier_sample_locked_impl(
            session,
            epoch,
            route2_epoch_ready_end_seconds_locked=self._route2_epoch_ready_end_seconds,
            now_ts=now_ts,
        )

    def _record_route2_byte_sample_locked(
        self,
        epoch: PlaybackEpoch,
        *,
        now_ts: float | None = None,
    ) -> None:
        return _record_route2_byte_sample_locked_impl(
            epoch,
            now_ts=now_ts,
        )

    def _record_route2_client_probe_sample_locked(
        self,
        session: MobilePlaybackSession,
        *,
        probe_bytes: int | None,
        probe_duration_ms: int | None,
        now_ts: float | None = None,
    ) -> None:
        return _record_route2_client_probe_sample_locked_impl(
            session,
            probe_bytes=probe_bytes,
            probe_duration_ms=probe_duration_ms,
            now_ts=now_ts,
        )

    def _harmonic_mean_locked(self, values: list[float]) -> float:
        return _harmonic_mean_locked_impl(values)

    def _percentile_locked(self, values: list[float], percentile: float) -> float:
        return _percentile_locked_impl(values, percentile)

    def _conservative_goodput_locked(
        self,
        rates: list[float],
        *,
        observation_seconds: float,
    ) -> dict[str, float | int | bool]:
        return _conservative_goodput_locked_impl(
            rates,
            observation_seconds=observation_seconds,
        )

    def _route2_server_byte_goodput_locked(
        self,
        epoch: PlaybackEpoch,
    ) -> dict[str, float | int | bool]:
        return _route2_server_byte_goodput_locked_impl(
            epoch,
            conservative_goodput_locked=self._conservative_goodput_locked,
        )

    def _route2_client_goodput_locked(
        self,
        session: MobilePlaybackSession,
    ) -> dict[str, float | int | bool]:
        return _route2_client_goodput_locked_impl(
            session,
            conservative_goodput_locked=self._conservative_goodput_locked,
        )

    def _route2_supply_rate_x_locked(self, epoch: PlaybackEpoch) -> tuple[float, float]:
        return _route2_supply_rate_x_locked_impl(epoch)

    def _ema_locked(self, values: list[float], *, alpha: float) -> float:
        return _ema_locked_impl(values, alpha=alpha)

    def _route2_supply_model_locked(self, epoch: PlaybackEpoch) -> dict[str, float | int | bool]:
        return _route2_supply_model_locked_impl(epoch)

    def _route2_effective_playhead_seconds_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> float:
        return _route2_effective_playhead_seconds_locked_impl(
            session,
            epoch,
            clamp_time=self._clamp_time,
        )

    def _route2_runtime_supply_metrics_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> tuple[float, float, float, float, float, bool, bool]:
        return _route2_runtime_supply_metrics_locked_impl(
            session,
            epoch,
            route2_epoch_ready_end_seconds_locked=self._route2_epoch_ready_end_seconds,
            route2_effective_playhead_seconds_locked=self._route2_effective_playhead_seconds_locked,
            route2_supply_model_locked=self._route2_supply_model_locked,
        )

    def _route2_projected_runway_seconds_locked(
        self,
        runway_seconds: float,
        supply_rate_x: float,
        *,
        projection_horizon_seconds: float,
        demand_rate_x: float = 1.0,
    ) -> float:
        return _route2_projected_runway_seconds_locked_impl(
            runway_seconds,
            supply_rate_x,
            projection_horizon_seconds=projection_horizon_seconds,
            demand_rate_x=demand_rate_x,
        )

    def _route2_required_runway_seconds_locked(
        self,
        *,
        minimum_runway_seconds: float,
        projected_runway_target_seconds: float,
        projection_horizon_seconds: float,
        supply_rate_x: float,
    ) -> float:
        return _route2_required_runway_seconds_locked_impl(
            minimum_runway_seconds=minimum_runway_seconds,
            projected_runway_target_seconds=projected_runway_target_seconds,
            projection_horizon_seconds=projection_horizon_seconds,
            supply_rate_x=supply_rate_x,
        )

    def _route2_attach_gate_state_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        *,
        minimum_runway_seconds: float,
        projected_runway_target_seconds: float,
        projection_horizon_seconds: float,
        minimum_supply_rate_x: float,
        reference_position_seconds: float | None = None,
    ) -> tuple[bool, float | None, float, float, float, bool]:
        return _route2_attach_gate_state_locked_impl(
            session,
            epoch,
            minimum_runway_seconds=minimum_runway_seconds,
            projected_runway_target_seconds=projected_runway_target_seconds,
            projection_horizon_seconds=projection_horizon_seconds,
            minimum_supply_rate_x=minimum_supply_rate_x,
            reference_position_seconds=reference_position_seconds,
            clamp_time=self._clamp_time,
            route2_epoch_ready_end_seconds_locked=self._route2_epoch_ready_end_seconds,
            route2_supply_model_locked=self._route2_supply_model_locked,
            route2_runtime_supply_metrics_locked=self._route2_runtime_supply_metrics_locked,
            route2_projected_runway_seconds_locked=self._route2_projected_runway_seconds_locked,
            route2_required_runway_seconds_locked=self._route2_required_runway_seconds_locked,
        )

    def _route2_display_prepare_eta_locked(
        self,
        epoch: PlaybackEpoch,
        raw_eta_seconds: float | None,
        *,
        now_ts: float | None = None,
        display_confident: bool = False,
    ) -> float | None:
        return _route2_display_prepare_eta_locked_impl(
            epoch,
            raw_eta_seconds,
            now_ts=now_ts,
            display_confident=display_confident,
        )

    def _route2_full_mode_requires_initial_attach_gate_locked(
        self,
        session: MobilePlaybackSession,
    ) -> bool:
        return _route2_full_mode_requires_initial_attach_gate_locked_impl(session)

    def _route2_full_safe_calibration_ratio_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        source_bin_bytes: list[int],
    ) -> float:
        return _route2_full_safe_calibration_ratio_locked_impl(
            session,
            epoch,
            source_bin_bytes,
            segment_index_for_time=self._segment_index_for_time,
            percentile_locked=self._percentile_locked,
            ema_locked=self._ema_locked,
        )

    def _route2_full_budget_metrics_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> dict[str, float | list[float] | int] | None:
        return _route2_full_budget_metrics_locked_impl(
            session,
            epoch,
            segment_index_for_time=self._segment_index_for_time,
            route2_full_safe_calibration_ratio_locked=self._route2_full_safe_calibration_ratio_locked,
        )

    def _route2_full_prepare_elapsed_seconds_locked(
        self,
        session: MobilePlaybackSession,
        *,
        now_ts: float | None = None,
    ) -> float:
        return _route2_full_prepare_elapsed_seconds_locked_impl(
            session,
            now_ts=now_ts,
        )

    def _route2_full_bootstrap_eta_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        *,
        now_ts: float | None = None,
    ) -> float | None:
        return _route2_full_bootstrap_eta_locked_impl(
            session,
            epoch,
            now_ts=now_ts,
            route2_full_prepare_elapsed_seconds_locked=self._route2_full_prepare_elapsed_seconds_locked,
            route2_epoch_ready_end_seconds=self._route2_epoch_ready_end_seconds,
            route2_supply_model_locked=self._route2_supply_model_locked,
        )

    def _route2_full_mode_gate_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> dict[str, float | str | bool | None]:
        return _route2_full_mode_gate_locked_impl(
            session,
            epoch,
            route2_full_mode_requires_initial_attach_gate_locked=self._route2_full_mode_requires_initial_attach_gate_locked,
            route2_full_prepare_elapsed_seconds_locked=self._route2_full_prepare_elapsed_seconds_locked,
            ensure_route2_full_preflight_locked=self._ensure_route2_full_preflight_locked,
            route2_full_bootstrap_eta_locked=self._route2_full_bootstrap_eta_locked,
            route2_full_budget_metrics_locked=self._route2_full_budget_metrics_locked,
            route2_server_byte_goodput_locked=self._route2_server_byte_goodput_locked,
            route2_client_goodput_locked=self._route2_client_goodput_locked,
        )

    def _route2_epoch_startup_attach_ready_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> bool:
        return _route2_epoch_startup_attach_ready_locked_impl(
            session,
            epoch,
            route2_full_mode_requires_initial_attach_gate_locked=self._route2_full_mode_requires_initial_attach_gate_locked,
            route2_full_mode_gate_locked=self._route2_full_mode_gate_locked,
            route2_attach_gate_state_locked=self._route2_attach_gate_state_locked,
            route2_epoch_ready_end_seconds_locked=self._route2_epoch_ready_end_seconds,
        )

    def _route2_epoch_recovery_ready_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> bool:
        return _route2_epoch_recovery_ready_locked_impl(
            session,
            epoch,
            route2_attach_gate_state_locked=self._route2_attach_gate_state_locked,
        )

    def _route2_low_water_recovery_needed_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        *,
        now_ts: float | None = None,
    ) -> tuple[float, float, bool, bool, bool]:
        return _route2_low_water_recovery_needed_locked_impl(
            session,
            epoch,
            route2_runtime_supply_metrics_locked=self._route2_runtime_supply_metrics_locked,
            route2_projected_runway_seconds_locked=self._route2_projected_runway_seconds_locked,
            now_ts=now_ts,
        )

    def _route2_position_in_epoch_locked(
        self,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
        position_seconds: float,
    ) -> bool:
        return _route2_position_in_epoch_locked_impl(
            session,
            epoch,
            position_seconds,
            route2_epoch_ready_end_seconds_locked=self._route2_epoch_ready_end_seconds,
        )

    def _route2_recovery_target_locked(
        self,
        session: MobilePlaybackSession,
        active_epoch: PlaybackEpoch | None = None,
    ) -> float:
        return _route2_recovery_target_locked_impl(
            session,
            active_epoch,
            clamp_time=self._clamp_time,
        )

    def _terminate_route2_epoch_locked(self, epoch: PlaybackEpoch) -> None:
        epoch.stop_requested = True
        if epoch.active_worker_id:
            self._workers.pop(epoch.active_worker_id, None)
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
        self,
        session: MobilePlaybackSession,
        epoch_id: str,
    ) -> None:
        browser_session = session.browser_playback
        epoch = browser_session.epochs.get(epoch_id)
        if epoch is None:
            if browser_session.replacement_epoch_id == epoch_id:
                browser_session.replacement_epoch_id = None
            return
        self._terminate_route2_epoch_locked(epoch)
        if browser_session.replacement_epoch_id == epoch_id:
            browser_session.replacement_epoch_id = None
        browser_session.epochs.pop(epoch_id, None)
        shutil.rmtree(epoch.epoch_dir, ignore_errors=True)

    def _create_route2_replacement_epoch_locked(
        self,
        session: MobilePlaybackSession,
        *,
        target_position_seconds: float,
        reason: str,
    ) -> PlaybackEpoch:
        browser_session = session.browser_playback
        if browser_session.replacement_epoch_id:
            self._discard_route2_epoch_locked(session, browser_session.replacement_epoch_id)
        session.epoch += 1
        session.target_position_seconds = self._clamp_time(target_position_seconds, session.duration_seconds)
        session.pending_target_seconds = session.target_position_seconds
        replacement_epoch = self._build_route2_epoch_locked(session)
        browser_session.replacement_epoch_id = replacement_epoch.epoch_id
        browser_session.epochs[replacement_epoch.epoch_id] = replacement_epoch
        self._ensure_route2_epoch_workspace_locked(replacement_epoch)
        browser_session.replacement_retry_not_before_ts = 0.0
        self._log_route2_event(
            "replacement_epoch_created",
            session=session,
            epoch=replacement_epoch,
            reason=reason,
            target_position_seconds=round(session.target_position_seconds, 2),
        )
        return replacement_epoch

    def _promote_route2_replacement_epoch_locked(
        self,
        session: MobilePlaybackSession,
        replacement_epoch: PlaybackEpoch,
    ) -> None:
        browser_session = session.browser_playback
        next_attach_revision = max(1, browser_session.attach_revision + 1)
        previous_active = (
            browser_session.epochs.get(browser_session.active_epoch_id)
            if browser_session.active_epoch_id
            else None
        )
        if previous_active is not None and previous_active.epoch_id != replacement_epoch.epoch_id:
            self._mark_route2_epoch_draining_locked(
                session,
                previous_active,
                reason="replacement_promotion",
                required_client_revision=next_attach_revision,
            )
            self._terminate_route2_epoch_locked(previous_active)
            previous_active.stop_requested = False
            self._write_route2_epoch_metadata_locked(previous_active)
        browser_session.active_epoch_id = replacement_epoch.epoch_id
        browser_session.replacement_epoch_id = None
        self._issue_route2_attach_revision_locked(
            session,
            next_revision=next_attach_revision,
            reason="replacement_promotion",
            epoch=replacement_epoch,
        )
        session.target_position_seconds = replacement_epoch.attach_position_seconds
        session.pending_target_seconds = replacement_epoch.attach_position_seconds
        session.last_error = None
        self._log_route2_event(
            "replacement_epoch_promoted",
            session=session,
            epoch=replacement_epoch,
            previous_epoch_id=previous_active.epoch_id if previous_active is not None else None,
        )

    def _route2_epoch_needs_worker_locked(self, epoch: PlaybackEpoch) -> bool:
        if epoch.state in {"failed", "draining", "ended"}:
            return False
        if epoch.transcoder_completed:
            return False
        if epoch.active_worker_id or (epoch.process and epoch.process.poll() is None):
            return False
        return True

    def _ensure_route2_epoch_workers_locked(self, session: MobilePlaybackSession) -> None:
        browser_session = session.browser_playback
        waiting_epoch_exists = False
        running_epoch_exists = False
        for epoch_id in (browser_session.active_epoch_id, browser_session.replacement_epoch_id):
            if not epoch_id:
                continue
            epoch = browser_session.epochs.get(epoch_id)
            if epoch is None:
                continue
            if epoch.active_worker_id or (epoch.process and epoch.process.poll() is None):
                running_epoch_exists = True
                if epoch.state == "starting":
                    epoch.state = "warming"
                    self._write_route2_epoch_metadata_locked(epoch)
                continue
            if not self._route2_epoch_needs_worker_locked(epoch):
                continue
            if len(self._workers) >= self.settings.max_concurrent_mobile_workers:
                waiting_epoch_exists = True
                continue
            worker_id = uuid.uuid4().hex
            epoch.active_worker_id = worker_id
            epoch.stop_requested = False
            epoch.state = "warming"
            self._workers[worker_id] = session.session_id
            running_epoch_exists = True
            self._write_route2_epoch_metadata_locked(epoch)
            thread = threading.Thread(
                target=self._run_route2_epoch_worker,
                args=(session.session_id, epoch.epoch_id, worker_id),
                daemon=True,
                name=f"elvern-route2-worker-{worker_id[:8]}",
            )
            thread.start()
        if running_epoch_exists:
            session.worker_state = "running"
            session.queue_started_ts = None
        elif waiting_epoch_exists:
            session.worker_state = "queued"
            if session.queue_started_ts is None:
                session.queue_started_ts = time.time()
        else:
            session.worker_state = "idle"
            session.queue_started_ts = None

    def _ensure_route2_epoch_workspace_locked(self, epoch: PlaybackEpoch) -> None:
        epoch.epoch_dir.mkdir(parents=True, exist_ok=True)
        epoch.staging_dir.mkdir(parents=True, exist_ok=True)
        epoch.published_dir.mkdir(parents=True, exist_ok=True)
        self._rebuild_route2_published_frontier_locked(epoch)
        self._write_route2_epoch_metadata_locked(epoch)

    def _write_route2_epoch_metadata_locked(self, epoch: PlaybackEpoch) -> None:
        self._write_json_atomic(
            epoch.metadata_path,
            {
                "epoch_id": epoch.epoch_id,
                "session_id": epoch.session_id,
                "created_at": epoch.created_at,
                "state": epoch.state,
                "target_position_seconds": round(epoch.target_position_seconds, 2),
                "epoch_start_seconds": round(epoch.epoch_start_seconds, 2),
                "attach_position_seconds": round(epoch.attach_position_seconds, 2),
                "segment_duration_seconds": SEGMENT_DURATION_SECONDS,
                "staging_dir": str(epoch.staging_dir),
                "published_dir": str(epoch.published_dir),
                "published_total_bytes": epoch.published_total_bytes,
                "transcoder_completed": epoch.transcoder_completed,
                "active_worker_id": epoch.active_worker_id,
                "drain_started_at_ts": epoch.drain_started_at_ts,
                "drain_target_attach_revision": epoch.drain_target_attach_revision,
                "last_media_access_at_ts": epoch.last_media_access_at_ts,
                "last_error": epoch.last_error,
                "updated_at": utcnow_iso(),
            },
        )

    def _write_route2_frontier_locked(self, epoch: PlaybackEpoch) -> None:
        published_end_seconds = 0.0
        if epoch.init_published and epoch.contiguous_published_through_segment is not None:
            published_end_seconds = epoch.epoch_start_seconds + (
                (epoch.contiguous_published_through_segment + 1) * SEGMENT_DURATION_SECONDS
            )
        self._write_json_atomic(
            epoch.frontier_path,
            {
                "epoch_id": epoch.epoch_id,
                "state": epoch.state,
                "segment_duration_seconds": SEGMENT_DURATION_SECONDS,
                "init_published": epoch.init_published,
                "published_ranges": self._compress_ranges(epoch.published_segments),
                "contiguous_published_through_segment": epoch.contiguous_published_through_segment,
                "published_total_bytes": epoch.published_total_bytes,
                "published_ready_start_seconds": round(epoch.epoch_start_seconds, 2)
                if epoch.init_published and epoch.contiguous_published_through_segment is not None
                else 0.0,
                "published_ready_end_seconds": round(published_end_seconds, 2),
                "transcoder_completed": epoch.transcoder_completed,
                "last_published_at": epoch.last_published_at,
                "last_error": epoch.last_error,
                "updated_at": utcnow_iso(),
            },
        )

    def _write_json_atomic(self, destination: Path, payload: dict[str, object]) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(destination)

    def _rebuild_route2_published_frontier_locked(self, epoch: PlaybackEpoch) -> None:
        epoch.published_dir.mkdir(parents=True, exist_ok=True)
        epoch.init_published = epoch.published_init_path.exists()
        epoch.published_init_bytes = epoch.published_init_path.stat().st_size if epoch.init_published else 0
        published_segments: set[int] = set()
        published_segment_bytes: dict[int, int] = {}
        for child in epoch.published_dir.glob("segment_*.m4s"):
            token = child.stem.removeprefix("segment_")
            try:
                segment_index = int(token)
            except ValueError:
                continue
            published_segments.add(segment_index)
            published_segment_bytes[segment_index] = child.stat().st_size
        epoch.published_segments = published_segments
        epoch.published_segment_bytes = published_segment_bytes
        epoch.published_total_bytes = epoch.published_init_bytes + sum(published_segment_bytes.values())
        epoch.contiguous_published_through_segment = self._contiguous_segment_frontier(published_segments) if epoch.init_published else None
        if epoch.init_published or epoch.published_segments:
            epoch.last_published_at = utcnow_iso()
        self._record_route2_byte_sample_locked(epoch)
        self._write_route2_frontier_locked(epoch)

    def _contiguous_segment_frontier(self, published_segments: set[int]) -> int | None:
        if 0 not in published_segments:
            return None
        frontier = 0
        while frontier + 1 in published_segments:
            frontier += 1
        return frontier

    def _route2_segment_destination(self, epoch: PlaybackEpoch, segment_index: int) -> Path:
        return epoch.published_dir / f"segment_{segment_index:06d}.m4s"

    def _route2_publish_init_locked(self, epoch: PlaybackEpoch, staged_init_path: Path) -> Path:
        epoch.published_dir.mkdir(parents=True, exist_ok=True)
        if not staged_init_path.exists():
            raise FileNotFoundError("Route 2 staged init segment is missing")
        if not epoch.published_init_path.exists():
            staged_init_path.replace(epoch.published_init_path)
        self._rebuild_route2_published_frontier_locked(epoch)
        self._write_route2_epoch_metadata_locked(epoch)
        return epoch.published_init_path

    def _route2_publish_segment_locked(
        self,
        epoch: PlaybackEpoch,
        segment_index: int,
        staged_segment_path: Path,
    ) -> Path:
        if segment_index < 0:
            raise ValueError("Route 2 segment index must be non-negative")
        if not staged_segment_path.exists():
            raise FileNotFoundError("Route 2 staged segment is missing")
        destination = self._route2_segment_destination(epoch, segment_index)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            staged_segment_path.replace(destination)
        self._rebuild_route2_published_frontier_locked(epoch)
        self._write_route2_epoch_metadata_locked(epoch)
        return destination

    def _publish_route2_epoch_outputs_locked(self, epoch: PlaybackEpoch) -> None:
        init_candidate = epoch.staging_dir / "init.mp4"
        if init_candidate.exists() and not epoch.init_published:
            self._route2_publish_init_locked(epoch, init_candidate)
        for child in sorted(epoch.staging_dir.glob("segment_*.m4s")):
            token = child.stem.removeprefix("segment_")
            try:
                segment_index = int(token)
            except ValueError:
                continue
            if segment_index in epoch.published_segments:
                continue
            self._route2_publish_segment_locked(epoch, segment_index, child)

    def _build_route2_epoch_ffmpeg_command(
        self,
        *,
        session: MobilePlaybackSession,
        epoch: PlaybackEpoch,
    ) -> list[str]:
        profile = MOBILE_PROFILES[session.profile]
        segment_pattern = epoch.staging_dir / "segment_%06d.m4s"
        scale_filter = (
            f"scale=w='min({profile.max_width},iw)':h='min({profile.max_height},ih)':"
            "force_original_aspect_ratio=decrease"
        )
        keyframe_interval = int(SEGMENT_DURATION_SECONDS * 24)
        source_input, source_input_kind = _resolve_worker_source_input_impl(
            self.settings,
            session,
        )
        command = [
            str(self.settings.ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-y",
        ]
        if source_input_kind == "url":
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
                f"{epoch.epoch_start_seconds:.3f}",
                "-i",
                source_input,
                "-output_ts_offset",
                f"{epoch.epoch_start_seconds:.3f}",
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
                "-c:a",
                "aac",
                "-ac",
                "2",
                "-ar",
                "48000",
                "-b:a",
                "160k",
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
                str(segment_pattern),
                str(epoch.staging_manifest_path),
            ]
        )
        return command

    def _publish_route2_epoch_outputs(self, session_id: str, epoch_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.browser_playback.engine_mode != "route2":
                return
            epoch = session.browser_playback.epochs.get(epoch_id)
            if epoch is None:
                return
            self._publish_route2_epoch_outputs_locked(epoch)
            self._refresh_route2_session_authority_locked(session)

    def _run_route2_epoch_worker(self, session_id: str, epoch_id: str, worker_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.browser_playback.engine_mode != "route2":
                self._workers.pop(worker_id, None)
                return
            epoch = session.browser_playback.epochs.get(epoch_id)
            if epoch is None or epoch.active_worker_id != worker_id:
                self._workers.pop(worker_id, None)
                return
            shutil.rmtree(epoch.staging_dir, ignore_errors=True)
            epoch.staging_dir.mkdir(parents=True, exist_ok=True)
            try:
                command = self._build_route2_epoch_ffmpeg_command(session=session, epoch=epoch)
            except Exception as exc:  # noqa: BLE001
                self._workers.pop(worker_id, None)
                epoch.active_worker_id = None
                epoch.state = "failed"
                epoch.last_error = str(exc) or "Browser Playback Route 2 could not prepare the source"
                self._log_route2_event(
                    "epoch_worker_prepare_failed",
                    session=session,
                    epoch=epoch,
                    level=logging.ERROR,
                    error=epoch.last_error,
                )
                self._write_route2_epoch_metadata_locked(epoch)
                self._refresh_route2_session_authority_locked(session)
                return
        logger.info(
            "Starting Browser Playback Route 2 epoch session=%s epoch=%s target=%.2f command=%s",
            session_id,
            epoch_id,
            epoch.attach_position_seconds,
            " ".join(command),
        )
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError as exc:
            with self._lock:
                self._workers.pop(worker_id, None)
                session = self._sessions.get(session_id)
                if not session or session.browser_playback.engine_mode != "route2":
                    return
                epoch = session.browser_playback.epochs.get(epoch_id)
                if epoch is None:
                    return
                epoch.active_worker_id = None
                epoch.state = "failed"
                epoch.last_error = str(exc)
                self._log_route2_event(
                    "epoch_worker_spawn_failed",
                    session=session,
                    epoch=epoch,
                    level=logging.ERROR,
                    error=epoch.last_error,
                )
                self._write_route2_epoch_metadata_locked(epoch)
                self._refresh_route2_session_authority_locked(session)
            return

        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.browser_playback.engine_mode != "route2":
                process.terminate()
                self._workers.pop(worker_id, None)
                return
            epoch = session.browser_playback.epochs.get(epoch_id)
            if epoch is None or epoch.active_worker_id != worker_id:
                process.terminate()
                self._workers.pop(worker_id, None)
                return
            epoch.process = process
            if epoch.state == "starting":
                epoch.state = "warming"
            self._write_route2_epoch_metadata_locked(epoch)

        while process.poll() is None and not self._manager_stop.is_set():
            self._publish_route2_epoch_outputs(session_id, epoch_id)
            time.sleep(0.35)

        self._publish_route2_epoch_outputs(session_id, epoch_id)
        return_code = process.wait()
        with self._lock:
            self._workers.pop(worker_id, None)
            session = self._sessions.get(session_id)
            if not session or session.browser_playback.engine_mode != "route2":
                return
            epoch = session.browser_playback.epochs.get(epoch_id)
            if epoch is None:
                return
            epoch.process = None
            epoch.active_worker_id = None
            if epoch.stop_requested or epoch.state in {"draining", "ended"}:
                epoch.stop_requested = False
                self._write_route2_epoch_metadata_locked(epoch)
                self._refresh_route2_session_authority_locked(session)
                return
            if return_code != 0:
                epoch.state = "failed"
                epoch.last_error = (
                    "Browser Playback Route 2 epoch transcoder failed "
                    f"(ffmpeg exited with code {return_code})"
                )
                self._log_route2_event(
                    "epoch_worker_failed",
                    session=session,
                    epoch=epoch,
                    level=logging.ERROR,
                    return_code=return_code,
                    error=epoch.last_error,
                )
                self._write_route2_epoch_metadata_locked(epoch)
                self._refresh_route2_session_authority_locked(session)
                return
            epoch.transcoder_completed = True
            epoch.last_error = None
            self._log_route2_event(
                "epoch_worker_completed",
                session=session,
                epoch=epoch,
            )
            self._write_route2_epoch_metadata_locked(epoch)
            self._refresh_route2_session_authority_locked(session)

    def _route2_snapshot_locked(self, session: MobilePlaybackSession) -> dict[str, object]:
        now_ts = time.time()
        browser_session = session.browser_playback
        active_epoch = (
            browser_session.epochs.get(browser_session.active_epoch_id)
            if browser_session.active_epoch_id
            else None
        )
        replacement_epoch = (
            browser_session.epochs.get(browser_session.replacement_epoch_id)
            if browser_session.replacement_epoch_id
            else None
        )
        active_manifest_url = None
        attach_position_seconds = round(session.target_position_seconds, 2)
        attach_ready = False
        ahead_runway_seconds = 0.0
        supply_rate_x = 0.0
        supply_observation_seconds = 0.0
        prepare_estimate_seconds = None
        mode_estimate_seconds = None
        mode_estimate_source = "none"
        mode_state = "preparing"
        mode_ready = False
        refill_in_progress = False
        starvation_risk = False
        stalled_recovery_needed = False
        if active_epoch is not None:
            active_manifest_url = f"/api/mobile-playback/epochs/{active_epoch.epoch_id}/index.m3u8"
            attach_position_seconds = round(active_epoch.attach_position_seconds, 2)
        ready_start_seconds = 0.0
        ready_end_seconds = 0.0
        cache_ranges: list[list[float]] = []
        manifest_end_segment = 0
        controller_epoch = replacement_epoch if replacement_epoch is not None else active_epoch
        recovery_gate = replacement_epoch is None and session.lifecycle_state in {"resuming", "recovering"}
        if controller_epoch and controller_epoch.init_published and controller_epoch.contiguous_published_through_segment is not None:
            (
                _controller_attach_ready,
                raw_prepare_estimate_seconds,
                supply_rate_x,
                supply_observation_seconds,
                _projected_runway_seconds,
                display_confident,
            ) = self._route2_attach_gate_state_locked(
                session,
                controller_epoch,
                minimum_runway_seconds=(
                    ROUTE2_RECOVERY_MIN_RUNWAY_SECONDS if recovery_gate else ROUTE2_STARTUP_MIN_RUNWAY_SECONDS
                ),
                projected_runway_target_seconds=(
                    ROUTE2_RECOVERY_RESUME_RUNWAY_SECONDS if recovery_gate else ROUTE2_ATTACH_READY_SECONDS
                ),
                projection_horizon_seconds=(
                    ROUTE2_RECOVERY_PROJECTION_HORIZON_SECONDS if recovery_gate else ROUTE2_STARTUP_PROJECTION_HORIZON_SECONDS
                ),
                minimum_supply_rate_x=(
                    ROUTE2_RECOVERY_MIN_SUPPLY_RATE_X if recovery_gate else ROUTE2_STARTUP_MIN_SUPPLY_RATE_X
                ),
                reference_position_seconds=None if recovery_gate else controller_epoch.attach_position_seconds,
            )
            prepare_estimate_seconds = self._route2_display_prepare_eta_locked(
                controller_epoch,
                raw_prepare_estimate_seconds,
                now_ts=now_ts,
                display_confident=display_confident,
            )
        if active_epoch and active_epoch.init_published and active_epoch.contiguous_published_through_segment is not None:
            recovery_attach_ready = self._route2_epoch_recovery_ready_locked(session, active_epoch)
            startup_attach_ready = self._route2_epoch_startup_attach_ready_locked(session, active_epoch)
            attach_ready = (
                recovery_attach_ready if session.lifecycle_state in {"resuming", "recovering"} else startup_attach_ready
            ) and browser_session.attach_revision > 0
            attach_ready = self._guard_route2_full_attach_boundary_locked(
                session,
                active_epoch,
                attach_eligible=attach_ready,
                guard_path="route2_snapshot_attach_ready",
            )
            ready_start_seconds = round(active_epoch.epoch_start_seconds, 2)
            ready_end_seconds = round(self._route2_epoch_ready_end_seconds(session, active_epoch), 2)
            manifest_end_segment = active_epoch.contiguous_published_through_segment
            cache_ranges = [[ready_start_seconds, ready_end_seconds]]
            (
                ahead_runway_seconds,
                _supply_rate_x,
                refill_in_progress,
                starvation_risk,
                stalled_recovery_needed,
            ) = self._route2_low_water_recovery_needed_locked(session, active_epoch)
            if replacement_epoch is None:
                supply_rate_x = _supply_rate_x
        if browser_session.playback_mode == "full" and controller_epoch is not None:
            full_mode_gate = self._route2_full_mode_gate_locked(session, controller_epoch)
            mode_state = str(full_mode_gate["mode_state"])
            mode_ready = bool(full_mode_gate["mode_ready"])
            mode_estimate_source = str(full_mode_gate.get("mode_estimate_source") or "none")
            mode_estimate_seconds = (
                round(float(full_mode_gate["mode_estimate_seconds"]), 2)
                if full_mode_gate["mode_estimate_seconds"] is not None
                else None
            )
            prepare_estimate_seconds = mode_estimate_seconds
        else:
            mode_ready = attach_ready
            mode_estimate_seconds = round(prepare_estimate_seconds, 2) if prepare_estimate_seconds is not None else None
            mode_estimate_source = "true" if mode_estimate_seconds is not None else "none"
            if mode_ready:
                mode_state = "ready"
            elif mode_estimate_seconds is None:
                mode_state = "estimating"
            else:
                mode_state = "preparing"
        can_play_from_target = (
            active_epoch is not None
            and self._route2_position_in_epoch_locked(session, active_epoch, session.target_position_seconds)
            and session.pending_target_seconds is None
        )
        return {
            "session_id": session.session_id,
            "media_item_id": session.media_item_id,
            "epoch": session.epoch,
            "manifest_revision": (
                f"route2:{browser_session.attach_revision}:{active_epoch.epoch_id}"
                if active_epoch is not None
                else f"route2:{browser_session.attach_revision}:none"
            ),
            "state": session.state,
            "profile": session.profile,
            "duration_seconds": round(session.duration_seconds, 2),
            "target_position_seconds": round(session.target_position_seconds, 2),
            "ready_start_seconds": ready_start_seconds,
            "ready_end_seconds": ready_end_seconds,
            "can_play_from_target": can_play_from_target,
            "manifest_url": (
                active_manifest_url
                if active_manifest_url
                else f"/api/mobile-playback/sessions/{session.session_id}/index.m3u8"
            ),
            "status_url": f"/api/mobile-playback/sessions/{session.session_id}",
            "seek_url": f"/api/mobile-playback/sessions/{session.session_id}/seek",
            "heartbeat_url": f"/api/mobile-playback/sessions/{session.session_id}/heartbeat",
            "stop_url": f"/api/mobile-playback/sessions/{session.session_id}/stop",
            "manifest_start_segment": 0,
            "manifest_end_segment": manifest_end_segment,
            "manifest_start_seconds": ready_start_seconds,
            "manifest_end_seconds": ready_end_seconds,
            "last_error": session.last_error,
            "worker_state": session.worker_state,
            "pending_target_seconds": round(session.pending_target_seconds, 2)
            if session.pending_target_seconds is not None
            else None,
            "last_stable_position_seconds": round(session.last_stable_position_seconds, 2),
            "playing_before_seek": session.playing_before_seek,
            "target_segment_index": self._segment_index_for_time(session.target_position_seconds),
            "target_cluster_ready": False,
            "target_window_ready": False,
            "playback_commit_ready": False,
            "cache_ranges": cache_ranges,
            "committed_playhead_seconds": round(session.committed_playhead_seconds, 2),
            "actual_media_element_time_seconds": round(session.actual_media_element_time_seconds, 2),
            "ahead_runway_seconds": round(ahead_runway_seconds, 2),
            "supply_rate_x": round(supply_rate_x, 3),
            "supply_observation_seconds": round(supply_observation_seconds, 2),
            "prepare_estimate_seconds": round(prepare_estimate_seconds, 2)
            if prepare_estimate_seconds is not None
            else None,
            "refill_in_progress": refill_in_progress,
            "last_refill_start_seconds": None,
            "last_refill_end_seconds": None,
            "starvation_risk": starvation_risk,
            "stalled_recovery_needed": stalled_recovery_needed,
            "lifecycle_state": session.lifecycle_state,
            "status_poll_seconds": (
                STATUS_POLL_PREPARE_SECONDS
                if browser_session.replacement_epoch_id or not attach_ready or browser_session.client_attach_revision < browser_session.attach_revision
                else 3.0
            ),
            "engine_mode": browser_session.engine_mode,
            "playback_mode": browser_session.playback_mode,
            "mode_state": mode_state,
            "mode_ready": mode_ready,
            "mode_estimate_seconds": mode_estimate_seconds,
            "mode_estimate_source": mode_estimate_source,
            "session_state": browser_session.state,
            "attach_revision": browser_session.attach_revision,
            "client_attach_revision": browser_session.client_attach_revision,
            "active_epoch_id": browser_session.active_epoch_id,
            "replacement_epoch_id": browser_session.replacement_epoch_id,
            "active_manifest_url": active_manifest_url,
            "attach_position_seconds": attach_position_seconds,
            "attach_ready": attach_ready,
            "browser_session_state": browser_session.state,
            "active_epoch_state": active_epoch.state if active_epoch is not None else None,
        }

    def _refresh_route2_session_authority_locked(self, session: MobilePlaybackSession) -> None:
        now_ts = time.time()
        browser_session = session.browser_playback
        self._ensure_route2_full_preflight_locked(session)
        self._cleanup_route2_draining_epochs_locked(session, now_ts=now_ts)
        active_epoch = (
            browser_session.epochs.get(browser_session.active_epoch_id)
            if browser_session.active_epoch_id
            else None
        )
        if active_epoch is None:
            browser_session.state = "failed"
            session.state = "failed"
            session.last_error = "Route 2 active epoch is missing"
            self._log_route2_event(
                "authority_missing_active_epoch",
                session=session,
                level=logging.ERROR,
                error=session.last_error,
            )
            return
        self._rebuild_route2_published_frontier_locked(active_epoch)
        self._record_route2_frontier_sample_locked(session, active_epoch, now_ts=now_ts)
        replacement_epoch = (
            browser_session.epochs.get(browser_session.replacement_epoch_id)
            if browser_session.replacement_epoch_id
            else None
        )
        if replacement_epoch is not None:
            self._rebuild_route2_published_frontier_locked(replacement_epoch)
            self._record_route2_frontier_sample_locked(session, replacement_epoch, now_ts=now_ts)
            if replacement_epoch.state == "failed":
                failed_error = replacement_epoch.last_error
                failed_epoch_id = replacement_epoch.epoch_id
                self._log_route2_event(
                    "replacement_epoch_failed_before_promotion",
                    session=session,
                    epoch=replacement_epoch,
                    level=logging.WARNING,
                    error=failed_error,
                )
                browser_session.replacement_retry_not_before_ts = now_ts + ROUTE2_REPLACEMENT_RETRY_BACKOFF_SECONDS
                self._discard_route2_epoch_locked(session, failed_epoch_id)
                replacement_epoch = None
                if active_epoch.state == "draining" and not self._route2_epoch_startup_attach_ready_locked(session, active_epoch):
                    browser_session.state = "failed"
                    session.state = "failed"
                    session.last_error = failed_error or "Browser Playback Route 2 replacement epoch failed"
                    self._log_route2_event(
                        "recovery_failed_without_authoritative_epoch",
                        session=session,
                        epoch=active_epoch,
                        level=logging.ERROR,
                        error=session.last_error,
                    )
                    self._write_route2_epoch_metadata_locked(active_epoch)
                    return

        if active_epoch.state == "failed":
            self._mark_route2_epoch_draining_locked(
                session,
                active_epoch,
                reason="active_epoch_failure",
            )
            self._write_route2_epoch_metadata_locked(active_epoch)
            if replacement_epoch is None and now_ts >= browser_session.replacement_retry_not_before_ts:
                replacement_epoch = self._create_route2_replacement_epoch_locked(
                    session,
                    target_position_seconds=self._route2_recovery_target_locked(session, active_epoch),
                    reason="active_epoch_failure",
                )
            browser_session.state = "recovering"

        if (
            replacement_epoch is None
            and active_epoch.state == "draining"
            and now_ts >= browser_session.replacement_retry_not_before_ts
        ):
            replacement_epoch = self._create_route2_replacement_epoch_locked(
                session,
                target_position_seconds=self._route2_recovery_target_locked(session, active_epoch),
                reason="draining_epoch_retry",
            )

        if replacement_epoch is not None:
            replacement_attach_ready = self._route2_epoch_startup_attach_ready_locked(session, replacement_epoch)
            replacement_attach_ready = self._guard_route2_full_attach_boundary_locked(
                session,
                replacement_epoch,
                attach_eligible=replacement_attach_ready,
                guard_path="replacement_promotion_check",
            )
            if replacement_attach_ready:
                self._promote_route2_replacement_epoch_locked(session, replacement_epoch)
                active_epoch = replacement_epoch
                replacement_epoch = None
                self._rebuild_route2_published_frontier_locked(active_epoch)
            else:
                if replacement_epoch.active_worker_id or (replacement_epoch.process and replacement_epoch.process.poll() is None):
                    replacement_epoch.state = "warming"
                elif replacement_epoch.state not in {"failed", "ended"}:
                    replacement_epoch.state = "starting"
                self._write_route2_epoch_metadata_locked(replacement_epoch)
                browser_session.state = "recovering" if active_epoch.state == "draining" else "switching"
                session.state = "retargeting" if session.pending_target_seconds is not None else "preparing"
        elif active_epoch.state == "draining":
            browser_session.state = "recovering"

        if browser_session.replacement_epoch_id is None and active_epoch.state == "draining":
            if not self._route2_epoch_startup_attach_ready_locked(session, active_epoch):
                browser_session.state = "recovering"
                session.state = "preparing"
                session.last_error = active_epoch.last_error or "Recovering Browser Playback Route 2 epoch"
                self._write_route2_epoch_metadata_locked(active_epoch)
                self._ensure_route2_epoch_workers_locked(session)
                return

        attach_ready = self._route2_epoch_startup_attach_ready_locked(session, active_epoch)
        attach_ready = self._guard_route2_full_attach_boundary_locked(
            session,
            active_epoch,
            attach_eligible=attach_ready,
            guard_path="refresh_active_attach_ready",
        )
        if attach_ready and browser_session.attach_revision == 0:
            self._issue_route2_attach_revision_locked(
                session,
                next_revision=1,
                reason="initial_attach_ready",
                epoch=active_epoch,
            )

        if session.pending_target_seconds is not None and browser_session.client_attach_revision >= browser_session.attach_revision:
            if abs(session.pending_target_seconds - active_epoch.attach_position_seconds) <= 0.5:
                session.pending_target_seconds = None

        if attach_ready:
            if browser_session.replacement_epoch_id is None:
                if active_epoch.state == "draining":
                    browser_session.state = "recovering"
                elif browser_session.client_attach_revision >= browser_session.attach_revision:
                    browser_session.state = "active"
                    active_epoch.state = "active"
                else:
                    browser_session.state = "starting"
                    active_epoch.state = "attach_ready"
                session.state = "ready" if session.pending_target_seconds is None else "retargeting"
            session.ready_start_seconds = round(active_epoch.epoch_start_seconds, 2)
            session.ready_end_seconds = round(self._route2_epoch_ready_end_seconds(session, active_epoch), 2)
            session.last_error = None
            if (
                browser_session.attach_revision > 0
                and browser_session.client_attach_revision < browser_session.attach_revision
                and browser_session.attach_revision_issued_at_ts > 0
                and now_ts - browser_session.attach_revision_issued_at_ts >= ROUTE2_ATTACH_ACK_WARN_SECONDS
                and browser_session.last_attach_warning_revision < browser_session.attach_revision
            ):
                browser_session.last_attach_warning_revision = browser_session.attach_revision
                self._log_route2_event(
                    "attach_ack_overdue",
                    session=session,
                    epoch=active_epoch,
                    level=logging.WARNING,
                    attach_revision_issued_at_ts=browser_session.attach_revision_issued_at_ts,
                )
            self._write_route2_epoch_metadata_locked(active_epoch)
            self._ensure_route2_epoch_workers_locked(session)
            return

        if active_epoch.active_worker_id or (active_epoch.process and active_epoch.process.poll() is None):
            active_epoch.state = "warming"
        elif active_epoch.state not in {"draining", "failed", "ended"}:
            active_epoch.state = "starting"
        if browser_session.replacement_epoch_id is None:
            browser_session.state = "recovering" if active_epoch.state == "draining" else "starting"
            session.state = "preparing"
        session.ready_start_seconds = 0.0
        session.ready_end_seconds = 0.0
        self._write_route2_epoch_metadata_locked(active_epoch)
        self._ensure_route2_epoch_workers_locked(session)

    def _manifest_window_locked(
        self,
        session: MobilePlaybackSession,
        cache_state: CacheState,
    ) -> tuple[int, int, int]:
        total_segments = max(1, math.ceil(session.duration_seconds / SEGMENT_DURATION_SECONDS))
        available = self._combined_available_segments_locked(session, cache_state)
        if not available:
            anchor_index = self._segment_index_for_time(session.target_position_seconds)
            return anchor_index, anchor_index, total_segments

        # Keep the exposed manifest anchored to the requested target slice.
        # Playback/session state can continue advancing independently, but the
        # playlist itself must stay VOD-like and seekable instead of sliding
        # forward like a live window.
        anchor_position = session.target_position_seconds
        anchor_index = self._segment_index_for_time(anchor_position)

        if anchor_index not in available:
            lower_candidates = [index for index in available if index <= anchor_index]
            upper_candidates = [index for index in available if index >= anchor_index]
            if upper_candidates:
                anchor_index = min(upper_candidates)
            elif lower_candidates:
                anchor_index = max(lower_candidates)
            else:
                anchor_index = min(available)

        manifest_start_segment = anchor_index
        while manifest_start_segment > 0 and (manifest_start_segment - 1) in available:
            manifest_start_segment -= 1

        manifest_end_segment = anchor_index
        max_index = total_segments - 1
        while manifest_end_segment < max_index and (manifest_end_segment + 1) in available:
            manifest_end_segment += 1

        return manifest_start_segment, manifest_end_segment, total_segments

    def _resolve_manifest_window_locked(
        self,
        session: MobilePlaybackSession,
        cache_state: CacheState,
    ) -> tuple[int, int, int]:
        total_segments = max(1, math.ceil(session.duration_seconds / SEGMENT_DURATION_SECONDS))
        if self._target_is_ready(session) and session.pending_target_seconds is None:
            if session.manifest_start_segment is None or session.manifest_end_segment is None:
                (
                    session.manifest_start_segment,
                    _initial_end_segment,
                    _computed_total_segments,
                ) = self._manifest_window_locked(session, cache_state)
                # Keep a single stable VOD manifest for the current epoch so the
                # browser can continue requesting future segments without
                # playlist swaps at each cache-fill boundary.
                session.manifest_end_segment = total_segments - 1
            return session.manifest_start_segment, session.manifest_end_segment, total_segments
        return self._manifest_window_locked(session, cache_state)

    def _maybe_advance_manifest_window_locked(self, session: MobilePlaybackSession) -> None:
        if (
            session.pending_target_seconds is not None
            or session.manifest_start_segment is None
            or session.manifest_end_segment is None
        ):
            return
        current_position = max(
            session.actual_media_element_time_seconds,
            session.committed_playhead_seconds,
            session.last_stable_position_seconds,
            session.target_position_seconds,
        )
        attached_end_seconds = min(
            session.duration_seconds,
            (session.manifest_end_segment + 1) * SEGMENT_DURATION_SECONDS,
        )
        remaining_attached_seconds = max(0.0, attached_end_seconds - current_position)
        additional_ready_seconds = max(0.0, session.ready_end_seconds - attached_end_seconds)
        if remaining_attached_seconds > MANIFEST_ADVANCE_TRIGGER_SECONDS:
            return
        if additional_ready_seconds < MANIFEST_ADVANCE_MIN_GROWTH_SECONDS:
            return
        total_segments = max(1, math.ceil(session.duration_seconds / SEGMENT_DURATION_SECONDS))
        new_end_segment = min(
            total_segments - 1,
            max(self._segment_index_for_time(max(session.ready_end_seconds - 0.001, 0.0)), session.manifest_end_segment),
        )
        if new_end_segment <= session.manifest_end_segment:
            return
        session.target_position_seconds = self._clamp_time(current_position, session.duration_seconds)
        session.manifest_end_segment = new_end_segment

    def _normalize_profile(self, value: str | None) -> str:
        candidate = (value or "mobile_1080p").strip().lower()
        if candidate not in MOBILE_PROFILES:
            raise ValueError("Unsupported mobile playback profile")
        return candidate

    def _source_fingerprint(self, item: dict[str, object], source_locator: str) -> str:
        size_token = int(item.get("file_size") or 0)
        mtime_token = int(float(item.get("file_mtime") or 0))
        raw = f"{source_locator}|{size_token}|{mtime_token}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _build_cache_key(self, source_fingerprint: str, profile: str) -> str:
        return hashlib.sha256(f"{source_fingerprint}:{profile}".encode("utf-8")).hexdigest()[:20]

    def _load_cache_state(
        self,
        *,
        cache_key: str,
        profile: str,
        duration_seconds: float,
        source_fingerprint: str,
    ) -> CacheState:
        with self._lock:
            return self._load_cache_state_locked(
                cache_key=cache_key,
                profile=profile,
                duration_seconds=duration_seconds,
                source_fingerprint=source_fingerprint,
            )

    def _load_cache_state_locked(
        self,
        *,
        cache_key: str,
        profile: str,
        duration_seconds: float,
        source_fingerprint: str,
    ) -> CacheState:
        cache_state = self._cache_states.get(cache_key)
        if cache_state is None:
            cache_dir = self._cache_root / cache_key
            cache_state = CacheState(
                cache_key=cache_key,
                cache_dir=cache_dir,
                metadata_path=cache_dir / "coverage.json",
                init_path=cache_dir / "init.mp4",
                duration_seconds=duration_seconds,
                profile=profile,
                total_segments=max(1, math.ceil(duration_seconds / SEGMENT_DURATION_SECONDS)),
                source_fingerprint=source_fingerprint,
            )
            self._cache_states[cache_key] = cache_state
        if not cache_state.loaded:
            self._hydrate_cache_state_locked(cache_state)
        return cache_state

    def _hydrate_cache_state_locked(self, cache_state: CacheState) -> None:
        cache_state.cache_dir.mkdir(parents=True, exist_ok=True)
        cached_segments: set[int] = set()
        if cache_state.metadata_path.exists():
            try:
                payload = json.loads(cache_state.metadata_path.read_text(encoding="utf-8"))
                for start, end in payload.get("cached_ranges", []):
                    cached_segments.update(range(int(start), int(end) + 1))
            except (OSError, ValueError, TypeError):
                cached_segments.clear()
        if not cached_segments:
            for child in cache_state.cache_dir.glob("segment_*.m4s"):
                token = child.stem.removeprefix("segment_")
                try:
                    cached_segments.add(int(token))
                except ValueError:
                    continue
        cache_state.cached_segments = cached_segments
        cache_state.loaded = True
        self._write_cache_metadata_locked(cache_state)

    def _build_target_cluster_job(
        self,
        session: MobilePlaybackSession,
        *,
        target_segment_index: int | None = None,
    ) -> MobileClusterJob:
        target_segment = (
            target_segment_index
            if target_segment_index is not None
            else self._segment_index_for_time(session.target_position_seconds)
        )
        preroll_segments = math.ceil(TARGET_WINDOW_PREROLL_SECONDS / SEGMENT_DURATION_SECONDS)
        forward_segments = math.ceil(TARGET_WINDOW_FORWARD_SECONDS / SEGMENT_DURATION_SECONDS)
        total_segments = max(1, math.ceil(session.duration_seconds / SEGMENT_DURATION_SECONDS))
        prepare_start_segment = max(0, target_segment - preroll_segments)
        prepare_end_segment = min(total_segments - 1, target_segment + forward_segments)
        prepare_start_seconds = prepare_start_segment * SEGMENT_DURATION_SECONDS
        prepare_end_seconds = min(
            session.duration_seconds,
            (prepare_end_segment + 1) * SEGMENT_DURATION_SECONDS,
        )
        output_dir = self._session_root / session.session_id / f"cluster-{session.epoch}-target"
        return MobileClusterJob(
            generation=session.epoch,
            phase="target",
            target_position_seconds=session.target_position_seconds,
            target_segment_index=target_segment,
            prepare_start_segment=prepare_start_segment,
            prepare_end_segment=prepare_end_segment,
            prepare_start_seconds=prepare_start_seconds,
            prepare_end_seconds=prepare_end_seconds,
            output_dir=output_dir,
            manifest_path=output_dir / "ffmpeg.m3u8",
        )

    def _build_expansion_cluster_job(
        self,
        session: MobilePlaybackSession,
        cache_state: CacheState,
    ) -> MobileClusterJob | None:
        if not self._target_is_ready(session):
            return None
        target_segment = self._segment_index_for_time(session.target_position_seconds)
        total_segments = max(1, math.ceil(session.duration_seconds / SEGMENT_DURATION_SECONDS))
        available = self._combined_available_segments_locked(session, cache_state)
        if target_segment not in available:
            return None
        right = target_segment
        max_index = total_segments - 1
        while right < max_index and (right + 1) in available:
            right += 1
        prepare_start_segment = right + 1
        if prepare_start_segment > max_index:
            return None
        anchor_position = self._watch_anchor_position(session)
        desired_ready_end = min(
            session.duration_seconds,
            anchor_position + WATCH_REFILL_TARGET_SECONDS,
        )
        desired_end_segment = min(
            max_index,
            self._segment_index_for_time(max(desired_ready_end - 0.001, 0.0)),
        )
        if desired_end_segment < prepare_start_segment:
            return None
        prepare_end_segment = desired_end_segment
        prepare_start_seconds = prepare_start_segment * SEGMENT_DURATION_SECONDS
        prepare_end_seconds = min(
            session.duration_seconds,
            (prepare_end_segment + 1) * SEGMENT_DURATION_SECONDS,
        )
        output_dir = self._session_root / session.session_id / f"cluster-{session.epoch}-expand"
        return MobileClusterJob(
            generation=session.epoch,
            phase="expand",
            target_position_seconds=session.target_position_seconds,
            target_segment_index=target_segment,
            prepare_start_segment=prepare_start_segment,
            prepare_end_segment=prepare_end_segment,
            prepare_start_seconds=prepare_start_seconds,
            prepare_end_seconds=prepare_end_seconds,
            output_dir=output_dir,
            manifest_path=output_dir / "ffmpeg.m3u8",
        )

    def _segment_index_for_time(self, position_seconds: float) -> int:
        return max(0, int(math.floor(position_seconds / SEGMENT_DURATION_SECONDS)))

    def _clamp_time(self, position_seconds: float, duration_seconds: float) -> float:
        clamped = max(0.0, float(position_seconds or 0.0))
        if duration_seconds <= 0:
            return clamped
        return min(clamped, max(duration_seconds - 1.0, 0.0))

    def _touch_session_locked(self, session: MobilePlaybackSession, *, media_access: bool) -> None:
        now = utcnow_iso()
        session.last_client_seen_at = now
        if media_access:
            session.last_media_access_at = now
        session.expires_at_ts = time.time() + (self.settings.mobile_session_ttl_minutes * 60)

    def _browser_session_state(self, session: MobilePlaybackSession) -> str:
        browser_session = session.browser_playback
        if browser_session.engine_mode != "legacy":
            return browser_session.state
        if session.state == "failed":
            return "failed"
        if session.state in {"stopped", "expired"}:
            return "stopped"
        return browser_session.state

    def _snapshot_locked(self, session: MobilePlaybackSession, cache_state: CacheState) -> dict[str, object]:
        target_window_ready = self._target_is_ready(session)
        playback_commit_ready = self._playback_commit_is_ready(session)
        ahead_runway_seconds = self._ahead_runway_seconds(session)
        starvation_risk = self._starvation_risk(session)
        stalled_recovery_needed = self._stalled_recovery_needed(session)
        browser_session = session.browser_playback
        active_epoch = (
            browser_session.epochs.get(browser_session.active_epoch_id)
            if browser_session.active_epoch_id
            else None
        )
        manifest_start_segment, manifest_end_segment, _total_segments = self._resolve_manifest_window_locked(
            session,
            cache_state,
        )
        refill_in_progress = (
            session.active_job is not None
            and session.active_job.phase == "expand"
            and session.worker_state == "running"
            and not session.active_job.superseded
        )
        manifest_start_seconds = round(manifest_start_segment * SEGMENT_DURATION_SECONDS, 2)
        manifest_end_seconds = round(
            min(session.duration_seconds, (manifest_end_segment + 1) * SEGMENT_DURATION_SECONDS),
            2,
        )
        return {
            "session_id": session.session_id,
            "media_item_id": session.media_item_id,
            "epoch": session.epoch,
            "manifest_revision": f"{session.epoch}:{manifest_start_segment}:{manifest_end_segment}",
            "state": session.state,
            "profile": session.profile,
            "duration_seconds": round(session.duration_seconds, 2),
            "target_position_seconds": round(session.target_position_seconds, 2),
            "ready_start_seconds": round(session.ready_start_seconds, 2),
            "ready_end_seconds": round(session.ready_end_seconds, 2),
            "can_play_from_target": target_window_ready,
            "manifest_url": f"/api/mobile-playback/sessions/{session.session_id}/index.m3u8",
            "status_url": f"/api/mobile-playback/sessions/{session.session_id}",
            "seek_url": f"/api/mobile-playback/sessions/{session.session_id}/seek",
            "heartbeat_url": f"/api/mobile-playback/sessions/{session.session_id}/heartbeat",
            "stop_url": f"/api/mobile-playback/sessions/{session.session_id}/stop",
            "manifest_start_segment": manifest_start_segment,
            "manifest_end_segment": manifest_end_segment,
            "manifest_start_seconds": manifest_start_seconds,
            "manifest_end_seconds": manifest_end_seconds,
            "last_error": session.last_error,
            "worker_state": session.worker_state,
            "pending_target_seconds": round(session.pending_target_seconds, 2) if session.pending_target_seconds is not None else None,
            "last_stable_position_seconds": round(session.last_stable_position_seconds, 2),
            "playing_before_seek": session.playing_before_seek,
            "target_segment_index": self._segment_index_for_time(session.target_position_seconds),
            "target_cluster_ready": target_window_ready,
            "target_window_ready": target_window_ready,
            "playback_commit_ready": playback_commit_ready,
            "cache_ranges": self._cache_ranges_to_seconds(cache_state),
            "committed_playhead_seconds": round(session.committed_playhead_seconds, 2),
            "actual_media_element_time_seconds": round(session.actual_media_element_time_seconds, 2),
            "ahead_runway_seconds": round(ahead_runway_seconds, 2),
            "supply_rate_x": 0.0,
            "supply_observation_seconds": 0.0,
            "prepare_estimate_seconds": None,
            "refill_in_progress": refill_in_progress,
            "last_refill_start_seconds": round(session.last_refill_start_seconds, 2)
            if session.last_refill_start_seconds is not None
            else None,
            "last_refill_end_seconds": round(session.last_refill_end_seconds, 2)
            if session.last_refill_end_seconds is not None
            else None,
            "starvation_risk": starvation_risk,
            "stalled_recovery_needed": stalled_recovery_needed,
            "lifecycle_state": session.lifecycle_state,
            "status_poll_seconds": (
                STATUS_POLL_PREPARE_SECONDS
                if session.state in {"queued", "preparing", "retargeting"} or starvation_risk or stalled_recovery_needed
                else 5.0
            ),
            "engine_mode": browser_session.engine_mode,
            "playback_mode": browser_session.playback_mode,
            "mode_state": "ready" if playback_commit_ready else ("preparing" if session.state in {"queued", "preparing", "retargeting"} else "estimating"),
            "mode_ready": playback_commit_ready,
            "mode_estimate_seconds": None,
            "mode_estimate_source": "none",
            "attach_revision": browser_session.attach_revision,
            "client_attach_revision": browser_session.client_attach_revision,
            "active_epoch_id": browser_session.active_epoch_id,
            "replacement_epoch_id": browser_session.replacement_epoch_id,
            "active_manifest_url": None,
            "attach_position_seconds": round(session.target_position_seconds, 2),
            "attach_ready": False,
            "browser_session_state": self._browser_session_state(session),
            "active_epoch_state": active_epoch.state if active_epoch is not None else None,
        }

    def _target_is_ready(self, session: MobilePlaybackSession) -> bool:
        return _target_is_ready_impl(session)

    def _playback_commit_is_ready(self, session: MobilePlaybackSession) -> bool:
        return _playback_commit_is_ready_impl(
            session,
            target_is_ready=self._target_is_ready,
        )

    def _watch_anchor_position(self, session: MobilePlaybackSession) -> float:
        return _watch_anchor_position_impl(session)

    def _ahead_runway_seconds(self, session: MobilePlaybackSession) -> float:
        return _ahead_runway_seconds_impl(
            session,
            watch_anchor_position=self._watch_anchor_position,
        )

    def _starvation_risk(self, session: MobilePlaybackSession) -> bool:
        return _starvation_risk_impl(
            session,
            ahead_runway_seconds=self._ahead_runway_seconds,
        )

    def _stalled_recovery_needed(self, session: MobilePlaybackSession) -> bool:
        return _stalled_recovery_needed_impl(
            session,
            ahead_runway_seconds=self._ahead_runway_seconds,
        )

    def _combined_available_segments_locked(self, session: MobilePlaybackSession, cache_state: CacheState) -> set[int]:
        available = set(cache_state.cached_segments)
        active_job = session.active_job
        if active_job and active_job.output_dir.exists():
            for child in active_job.output_dir.glob("segment_*.m4s"):
                token = child.stem.removeprefix("segment_")
                try:
                    available.add(int(token))
                except ValueError:
                    continue
        return available

    def _refresh_ready_window_locked(self, session: MobilePlaybackSession, cache_state: CacheState) -> None:
        target_index = self._segment_index_for_time(session.target_position_seconds)
        available = self._combined_available_segments_locked(session, cache_state)
        if target_index not in available:
            anchor = target_index * SEGMENT_DURATION_SECONDS
            session.ready_start_seconds = anchor
            session.ready_end_seconds = anchor
            return
        left = target_index
        while left > 0 and (left - 1) in available:
            left -= 1
        right = target_index
        max_index = cache_state.total_segments - 1
        while right < max_index and (right + 1) in available:
            right += 1
        session.ready_start_seconds = left * SEGMENT_DURATION_SECONDS
        session.ready_end_seconds = min(session.duration_seconds, (right + 1) * SEGMENT_DURATION_SECONDS)

    def _transition_session_state_locked(self, session: MobilePlaybackSession) -> None:
        if session.last_error:
            session.state = "failed"
            session.worker_state = "idle"
            session.pending_target_seconds = None
            return
        if self._playback_commit_is_ready(session):
            session.state = "ready"
            if session.worker_state != "running":
                session.worker_state = "idle"
            session.queue_started_ts = None
            session.pending_target_seconds = None
            return
        if session.worker_state == "running":
            session.state = "retargeting" if session.epoch > 1 else "preparing"
            return
        if session.queue_started_ts is not None:
            session.state = "queued"
            session.worker_state = "queued"
            return
        session.state = "retargeting" if session.epoch > 1 else "preparing"

    def _ensure_worker_for_session(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.state in {"failed", "stopped", "expired"}:
                return
            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            self._refresh_ready_window_locked(session, cache_state)
            if self._target_is_ready(session):
                active_job = session.active_job
                if active_job is None or active_job.state in {"ready", "failed", "superseded"}:
                    expansion_job = self._build_expansion_cluster_job(session, cache_state)
                    if expansion_job is not None:
                        session.last_refill_start_seconds = expansion_job.prepare_start_seconds
                        session.last_refill_end_seconds = expansion_job.prepare_end_seconds
                        session.active_job = expansion_job
                        active_job = session.active_job
                    else:
                        session.worker_state = "idle"
                        session.queue_started_ts = None
                        self._transition_session_state_locked(session)
                        return
                elif active_job.phase != "expand":
                    self._transition_session_state_locked(session)
                    return
            active_job = session.active_job
            if active_job is None or active_job.generation != session.epoch or active_job.state in {"ready", "failed", "superseded"}:
                session.active_job = self._build_target_cluster_job(session)
                active_job = session.active_job
            if active_job.active_worker_id:
                return
            if len(self._workers) >= self.settings.max_concurrent_mobile_workers:
                if session.queue_started_ts is None:
                    session.queue_started_ts = time.time()
                session.worker_state = "queued"
                self._transition_session_state_locked(session)
                return
            worker_id = uuid.uuid4().hex
            active_job.active_worker_id = worker_id
            session.worker_state = "running"
            session.queue_started_ts = None
            self._workers[worker_id] = session.session_id
            thread = threading.Thread(
                target=self._run_worker,
                args=(session.session_id, session.epoch, worker_id),
                daemon=True,
                name=f"elvern-mobile-worker-{worker_id[:8]}",
            )
            thread.start()

    def _run_worker(self, session_id: str, generation: int, worker_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                self._workers.pop(worker_id, None)
                return
            job = session.active_job
            if job is None or job.generation != generation:
                self._workers.pop(worker_id, None)
                return
            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            shutil.rmtree(job.output_dir, ignore_errors=True)
            job.output_dir.mkdir(parents=True, exist_ok=True)
            try:
                command = self._build_mobile_ffmpeg_command(session=session, job=job)
            except Exception as exc:  # noqa: BLE001
                self._workers.pop(worker_id, None)
                session = self._sessions.get(session_id)
                if session and session.epoch == generation:
                    session.last_error = str(exc) or "Experimental playback could not prepare the cloud source"
                    session.worker_state = "idle"
                    self._transition_session_state_locked(session)
                return
        logger.info(
            "Starting experimental mobile cache fill session=%s generation=%s target=%.2f command=%s",
            session_id,
            generation,
            job.target_position_seconds,
            " ".join(command),
        )
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError as exc:
            with self._lock:
                self._workers.pop(worker_id, None)
                session = self._sessions.get(session_id)
                if session and session.epoch == generation:
                    session.last_error = str(exc)
                    session.worker_state = "idle"
                    self._transition_session_state_locked(session)
            return

        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.active_job is None or session.active_job.generation != generation:
                process.terminate()
                self._workers.pop(worker_id, None)
                return
            session.active_job.process = process
            session.active_job.state = "preparing"
            session.worker_state = "running"

        while process.poll() is None and not self._manager_stop.is_set():
            self._publish_job_outputs(session_id, generation)
            time.sleep(0.35)

        self._publish_job_outputs(session_id, generation)
        return_code = process.wait()
        with self._lock:
            self._workers.pop(worker_id, None)
            session = self._sessions.get(session_id)
            if not session:
                return
            job = session.active_job
            if job is None or job.generation != generation:
                return
            job.process = None
            job.active_worker_id = None
            if job.superseded:
                job.state = "superseded"
                if session.worker_state == "running":
                    session.worker_state = "idle"
                return
            if return_code != 0:
                job.state = "failed"
                session.last_error = f"Experimental playback failed to prepare cache segments (ffmpeg exited with code {return_code})"
                session.worker_state = "idle"
                self._transition_session_state_locked(session)
                return
            job.state = "ready"
            self._refresh_ready_window_locked(session, cache_state)
            if not self._target_is_ready(session):
                session.last_error = "Experimental playback could not prepare the requested seek target"
            session.worker_state = "idle"
            self._transition_session_state_locked(session)
        self._ensure_worker_for_session(session_id)

    def _build_mobile_ffmpeg_command(
        self,
        *,
        session: MobilePlaybackSession,
        job: MobileClusterJob,
    ) -> list[str]:
        profile = MOBILE_PROFILES[session.profile]
        segment_pattern = job.output_dir / "segment_%06d.m4s"
        scale_filter = (
            f"scale=w='min({profile.max_width},iw)':h='min({profile.max_height},ih)':"
            "force_original_aspect_ratio=decrease"
        )
        cluster_duration = max(
            SEGMENT_DURATION_SECONDS,
            job.prepare_end_seconds - job.prepare_start_seconds,
        )
        keyframe_interval = int(SEGMENT_DURATION_SECONDS * 24)
        source_input, source_input_kind = _resolve_worker_source_input_impl(
            self.settings,
            session,
        )
        command = [
            str(self.settings.ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-y",
        ]
        if source_input_kind == "url":
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
            f"{job.prepare_start_seconds:.3f}",
            "-i",
            source_input,
            "-t",
            f"{cluster_duration:.3f}",
            # Shift each sparse cluster onto the movie's absolute timeline so
            # stable full-VOD manifests keep monotonically increasing PTS/DTS
            # after far seek instead of resetting segments to local zero.
            "-output_ts_offset",
            f"{job.prepare_start_seconds:.3f}",
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
            "-vf",
            scale_filter,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
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
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-b:a",
            "160k",
            "-max_muxing_queue_size",
            "2048",
            "-f",
            "hls",
            "-hls_time",
            f"{SEGMENT_DURATION_SECONDS:.0f}",
            "-hls_list_size",
            "0",
            "-hls_playlist_type",
            "vod",
            "-hls_segment_type",
            "fmp4",
            "-hls_fmp4_init_filename",
            "init.mp4",
            "-hls_flags",
            "independent_segments+temp_file",
            "-start_number",
            str(job.prepare_start_segment),
            "-hls_segment_filename",
            str(segment_pattern),
            str(job.manifest_path),
            ]
        )
        return command

    def _publish_job_outputs(self, session_id: str, generation: int) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return
            job = session.active_job
            if job is None or job.generation != generation:
                return
            cache_state = self._load_cache_state_locked(
                cache_key=session.cache_key,
                profile=session.profile,
                duration_seconds=session.duration_seconds,
                source_fingerprint=session.source_fingerprint,
            )
            self._publish_outputs_locked(cache_state, job.output_dir)
            self._refresh_ready_window_locked(session, cache_state)
            self._transition_session_state_locked(session)

    def _publish_outputs_locked(self, cache_state: CacheState, output_dir: Path) -> None:
        init_candidate = output_dir / "init.mp4"
        if init_candidate.exists():
            self._publish_init_to_cache_locked(cache_state, init_candidate)
        changed = False
        for child in output_dir.glob("segment_*.m4s"):
            token = child.stem.removeprefix("segment_")
            try:
                segment_index = int(token)
            except ValueError:
                continue
            if segment_index in cache_state.cached_segments:
                continue
            self._publish_segment_to_cache_locked(cache_state, segment_index, child)
            changed = True
        if changed:
            self._write_cache_metadata_locked(cache_state)

    def _publish_init_to_cache(self, cache_state: CacheState, candidate: Path) -> None:
        with self._lock:
            self._publish_init_to_cache_locked(cache_state, candidate)

    def _publish_segment_to_cache(self, cache_state: CacheState, segment_index: int, candidate: Path) -> None:
        with self._lock:
            self._publish_segment_to_cache_locked(cache_state, segment_index, candidate)
            self._write_cache_metadata_locked(cache_state)

    def _publish_init_to_cache_locked(self, cache_state: CacheState, candidate: Path) -> None:
        cache_state.cache_dir.mkdir(parents=True, exist_ok=True)
        if cache_state.init_path.exists():
            return
        self._copy_or_link(candidate, cache_state.init_path)

    def _publish_segment_to_cache_locked(self, cache_state: CacheState, segment_index: int, candidate: Path) -> None:
        cache_state.cache_dir.mkdir(parents=True, exist_ok=True)
        destination = cache_state.cache_dir / f"segment_{segment_index:06d}.m4s"
        if destination.exists():
            cache_state.cached_segments.add(segment_index)
            return
        self._copy_or_link(candidate, destination)
        cache_state.cached_segments.add(segment_index)

    def _copy_or_link(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.hardlink_to(source)
        except OSError:
            shutil.copy2(source, destination)

    def _active_cache_jobs_locked(self, cache_key: str) -> list[dict[str, object]]:
        jobs: list[dict[str, object]] = []
        for session in self._sessions.values():
            if session.cache_key != cache_key:
                continue
            job = session.active_job
            if job is None or job.superseded:
                continue
            jobs.append(
                {
                    "session_id": session.session_id,
                    "generation": job.generation,
                    "target_segment_index": job.target_segment_index,
                    "segment_range": [job.prepare_start_segment, job.prepare_end_segment],
                    "state": job.state,
                }
            )
        return jobs

    def _write_cache_metadata_locked(self, cache_state: CacheState) -> None:
        ranges = self._compress_ranges(cache_state.cached_segments)
        payload = {
            "cache_key": cache_state.cache_key,
            "profile": cache_state.profile,
            "duration_seconds": round(cache_state.duration_seconds, 2),
            "segment_duration_seconds": SEGMENT_DURATION_SECONDS,
            "total_segments": cache_state.total_segments,
            "source_fingerprint": cache_state.source_fingerprint,
            "cached_ranges": ranges,
            "active_jobs": self._active_cache_jobs_locked(cache_state.cache_key),
            "updated_at": utcnow_iso(),
        }
        cache_state.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_state.metadata_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

    def _compress_ranges(self, values: set[int]) -> list[list[int]]:
        if not values:
            return []
        ordered = sorted(values)
        ranges: list[list[int]] = []
        start = ordered[0]
        end = ordered[0]
        for value in ordered[1:]:
            if value == end + 1:
                end = value
                continue
            ranges.append([start, end])
            start = end = value
        ranges.append([start, end])
        return ranges

    def _cache_ranges_to_seconds(self, cache_state: CacheState) -> list[list[float]]:
        ranges = self._compress_ranges(cache_state.cached_segments)
        second_ranges: list[list[float]] = []
        for start, end in ranges:
            start_seconds = round(start * SEGMENT_DURATION_SECONDS, 2)
            end_seconds = round(
                min(cache_state.duration_seconds, (end + 1) * SEGMENT_DURATION_SECONDS),
                2,
            )
            second_ranges.append([start_seconds, end_seconds])
        return second_ranges

    def _terminate_session(self, session: MobilePlaybackSession, *, remove_session_dir: bool = True) -> None:
        if session.active_job:
            self._terminate_job(session.active_job)
        if session.browser_playback.engine_mode == "route2":
            for epoch in session.browser_playback.epochs.values():
                self._terminate_route2_epoch_locked(epoch)
        if remove_session_dir:
            shutil.rmtree(self._session_root / session.session_id, ignore_errors=True)
            shutil.rmtree(self._route2_root / "sessions" / session.session_id, ignore_errors=True)

    def _terminate_job_locked(
        self,
        session: MobilePlaybackSession,
        job: MobileClusterJob,
        *,
        remove_output: bool = False,
    ) -> None:
        self._terminate_job(job)
        if job.active_worker_id:
            self._workers.pop(job.active_worker_id, None)
            job.active_worker_id = None
        job.process = None
        if remove_output:
            shutil.rmtree(job.output_dir, ignore_errors=True)
        session.worker_state = "idle"

    def _terminate_job(self, job: MobileClusterJob) -> None:
        process = job.process
        if not process or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _get_owned_route2_epoch_locked(
        self,
        epoch_id: str,
        user_id: int,
    ) -> tuple[MobilePlaybackSession, PlaybackEpoch]:
        for session in self._sessions.values():
            if session.user_id != user_id:
                continue
            if session.browser_playback.engine_mode != "route2":
                continue
            epoch = session.browser_playback.epochs.get(epoch_id)
            if epoch is None:
                continue
            return session, epoch
        raise KeyError("Browser playback epoch not found")

    def _get_owned_session_locked(
        self,
        session_id: str,
        user_id: int,
        *,
        allow_missing: bool = False,
    ) -> MobilePlaybackSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            if allow_missing:
                return None
            raise KeyError("Mobile playback session not found")
        if session.user_id != user_id:
            if allow_missing:
                return None
            raise PermissionError("Mobile playback session not found")
        return session

    def _manager_loop(self) -> None:
        while not self._manager_stop.wait(1):
            self._cleanup_sessions_and_cache()
            self._dispatch_waiting_sessions()

    def _cleanup_sessions_and_cache(self) -> None:
        now_ts = time.time()
        stale_sessions: list[MobilePlaybackSession] = []
        with self._lock:
            for session_id, session in list(self._sessions.items()):
                if session.browser_playback.engine_mode == "route2":
                    self._cleanup_route2_draining_epochs_locked(session, now_ts=now_ts)
                idle_for = now_ts - max(
                    self._parse_iso_ts(session.last_client_seen_at),
                    self._parse_iso_ts(session.last_media_access_at),
                )
                if session.expires_at_ts <= now_ts or idle_for > self.settings.mobile_session_idle_seconds:
                    session.state = "expired"
                    stale_sessions.append(session)
                    self._sessions.pop(session_id, None)
                    if self._active_session_by_user.get(session.user_id) == session_id:
                        self._active_session_by_user.pop(session.user_id, None)
                elif session.worker_state == "queued" and session.queue_started_ts:
                    if now_ts - session.queue_started_ts > self.settings.mobile_queue_timeout_seconds:
                        session.last_error = "Maximum concurrent mobile workers reached; try again when another experimental playback job finishes."
                        session.state = "failed"
                        session.worker_state = "idle"
                        session.queue_started_ts = None
            self._cleanup_orphaned_cache_dirs_locked(now_ts)
        for session in stale_sessions:
            logger.info("Cleaning up expired mobile playback session=%s", session.session_id)
            self._terminate_session(session)

    def _dispatch_waiting_sessions(self) -> None:
        with self._lock:
            available_slots = self.settings.max_concurrent_mobile_workers - len(self._workers)
            if available_slots <= 0:
                return
            session_ids = [
                session.session_id
                for session in self._sessions.values()
                if (
                    session.browser_playback.engine_mode == "route2"
                    or (session.worker_state == "queued" and session.state in {"queued", "ready", "preparing", "retargeting"})
                )
            ]
        for session_id in session_ids:
            with self._lock:
                session = self._sessions.get(session_id)
                if session is None:
                    continue
                engine_mode = session.browser_playback.engine_mode
            if engine_mode == "route2":
                with self._lock:
                    session = self._sessions.get(session_id)
                    if session is None or session.browser_playback.engine_mode != "route2":
                        continue
                    self._refresh_route2_session_authority_locked(session)
            else:
                self._ensure_worker_for_session(session_id)

    def _cleanup_orphaned_cache_dirs(self) -> None:
        self._cleanup_orphaned_cache_dirs_locked(time.time())

    def _cleanup_orphaned_cache_dirs_locked(self, now_ts: float) -> None:
        if not self._cache_root.exists():
            return
        cutoff = now_ts - (self.settings.mobile_cache_ttl_hours * 3600)
        for child in self._cache_root.iterdir():
            if not child.is_dir():
                continue
            if child.stat().st_mtime >= cutoff:
                continue
            logger.info("Removing stale mobile cache directory %s", child)
            shutil.rmtree(child, ignore_errors=True)
        route2_sessions_root = self._route2_root / "sessions"
        if not route2_sessions_root.exists():
            return
        route2_cutoff = now_ts - (self.settings.mobile_session_ttl_minutes * 60)
        for child in route2_sessions_root.iterdir():
            if not child.is_dir():
                continue
            if child.stat().st_mtime >= route2_cutoff:
                continue
            logger.info("Removing stale Route 2 session directory %s", child)
            shutil.rmtree(child, ignore_errors=True)

    def _parse_iso_ts(self, value: str) -> float:
        try:
            if not value:
                return time.time()
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return time.time()
