from __future__ import annotations

import logging
import time

from .mobile_playback_models import (
    ROUTE2_DRAIN_IDLE_GRACE_SECONDS,
    ROUTE2_DRAIN_MAX_SECONDS,
    MobilePlaybackSession,
    PlaybackEpoch,
)


def _route2_epoch_is_draining_expired_locked(
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
    session: MobilePlaybackSession,
    *,
    route2_epoch_is_draining_expired_locked,
    log_route2_event,
    discard_route2_epoch_locked,
    now_ts: float | None = None,
) -> None:
    browser_session = session.browser_playback
    now_ts = now_ts or time.time()
    for epoch_id, epoch in list(browser_session.epochs.items()):
        if epoch_id in {browser_session.active_epoch_id, browser_session.replacement_epoch_id}:
            continue
        if not route2_epoch_is_draining_expired_locked(session, epoch, now_ts=now_ts):
            continue
        log_route2_event(
            "epoch_drain_expired",
            session=session,
            epoch=epoch,
            drain_started_at_ts=epoch.drain_started_at_ts,
            last_media_access_at_ts=epoch.last_media_access_at_ts,
            drain_target_attach_revision=epoch.drain_target_attach_revision or None,
        )
        discard_route2_epoch_locked(session, epoch_id)


def _prepare_route2_epoch_access_locked(
    session: MobilePlaybackSession,
    epoch: PlaybackEpoch,
    *,
    media_kind: str,
    touch_session_locked,
    log_route2_event,
    discard_route2_epoch_locked,
) -> None:
    now_ts = time.time()
    touch_session_locked(session, media_access=True)
    _cleanup_route2_draining_epochs_locked(
        session,
        route2_epoch_is_draining_expired_locked=_route2_epoch_is_draining_expired_locked,
        log_route2_event=log_route2_event,
        discard_route2_epoch_locked=discard_route2_epoch_locked,
        now_ts=now_ts,
    )
    browser_session = session.browser_playback
    if epoch.epoch_id == browser_session.active_epoch_id:
        epoch.last_media_access_at_ts = now_ts
        return
    if epoch.state == "draining":
        if _route2_epoch_is_draining_expired_locked(session, epoch, now_ts=now_ts):
            log_route2_event(
                "stale_epoch_request",
                session=session,
                epoch=epoch,
                level=logging.WARNING,
                media_kind=media_kind,
                reason="draining_epoch_expired",
            )
            discard_route2_epoch_locked(session, epoch.epoch_id)
            raise FileNotFoundError("Route 2 epoch is no longer active")
        epoch.last_media_access_at_ts = now_ts
        return
    log_route2_event(
        "stale_epoch_request",
        session=session,
        epoch=epoch,
        level=logging.WARNING,
        media_kind=media_kind,
        reason="inactive_epoch_request",
    )
    raise FileNotFoundError("Route 2 epoch is no longer active")
