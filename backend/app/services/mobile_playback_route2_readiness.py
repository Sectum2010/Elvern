from __future__ import annotations

from .mobile_playback_models import (
    PLAYBACK_COMMIT_RUNWAY_SECONDS,
    READY_AFTER_TARGET_SECONDS,
    WATCH_LOW_WATERMARK_SECONDS,
    WATCH_STALLED_RECOVERY_RUNWAY_SECONDS,
    MobilePlaybackSession,
)


def _target_is_ready(session: MobilePlaybackSession) -> bool:
    if session.state == "failed":
        return False
    return (
        session.ready_start_seconds <= session.target_position_seconds <= session.ready_end_seconds
        and session.ready_end_seconds >= min(
            session.target_position_seconds + READY_AFTER_TARGET_SECONDS,
            session.duration_seconds,
        )
    )


def _playback_commit_is_ready(
    session: MobilePlaybackSession,
    *,
    target_is_ready,
) -> bool:
    if not target_is_ready(session):
        return False
    return session.ready_end_seconds >= min(
        session.target_position_seconds + PLAYBACK_COMMIT_RUNWAY_SECONDS,
        session.duration_seconds,
    )


def _watch_anchor_position(session: MobilePlaybackSession) -> float:
    if session.pending_target_seconds is not None:
        return session.target_position_seconds
    if session.committed_playhead_seconds > 0:
        return session.committed_playhead_seconds
    if session.last_stable_position_seconds > 0:
        return session.last_stable_position_seconds
    return session.target_position_seconds


def _ahead_runway_seconds(
    session: MobilePlaybackSession,
    *,
    watch_anchor_position,
) -> float:
    anchor = watch_anchor_position(session)
    return max(0.0, session.ready_end_seconds - anchor)


def _starvation_risk(
    session: MobilePlaybackSession,
    *,
    ahead_runway_seconds,
) -> bool:
    if session.pending_target_seconds is not None:
        return False
    return session.client_is_playing and ahead_runway_seconds(session) < WATCH_LOW_WATERMARK_SECONDS


def _stalled_recovery_needed(
    session: MobilePlaybackSession,
    *,
    ahead_runway_seconds,
) -> bool:
    if session.lifecycle_state in {"resuming", "recovering", "fatal"}:
        return True
    if session.stalled_recovery_requested:
        return True
    if session.pending_target_seconds is not None:
        return False
    return session.client_is_playing and ahead_runway_seconds(session) < WATCH_STALLED_RECOVERY_RUNWAY_SECONDS
