from __future__ import annotations

from datetime import datetime, timezone

from .config import Settings
from .db import get_connection, utcnow_iso

FIRST_PROGRESS_SAMPLE_CAP_SECONDS = 10.0
PROGRESS_SAMPLE_SLACK_SECONDS = 2.0
PROGRESS_SAMPLE_MAX_INCREMENT_SECONDS = 30.0
TRACKING_EVENT_PROGRESS_TYPES = {
    "playback_progress",
    "playback_seeked",
    "playback_stopped",
    "playback_completed",
}


def _is_bogus_inferred_cloud_vlc_completion(tracking_row) -> bool:
    if tracking_row is None:
        return False
    if str(tracking_row["tracking_source"] or "") != "inferred":
        return False
    if str(tracking_row["playback_mode"] or "") != "vlc_external":
        return False
    if str(tracking_row["source_kind"] or "local") != "cloud":
        return False
    if not bool(tracking_row["completed"]):
        return False
    return float(tracking_row["position_seconds"] or 0.0) <= 0.0


def _select_rebuild_tracking_row(tracking_rows: list) -> object | None:
    if not tracking_rows:
        return None
    latest_row = tracking_rows[0]
    if not _is_bogus_inferred_cloud_vlc_completion(latest_row):
        return latest_row
    for row in tracking_rows:
        if bool(row["completed"]):
            continue
        if float(row["position_seconds"] or 0.0) <= 0.0:
            continue
        return row
    return latest_row


def _rebuild_ignored_bogus_completion(tracking_rows: list, selected_row) -> bool:
    if not tracking_rows or selected_row is None:
        return False
    latest_row = tracking_rows[0]
    if latest_row == selected_row:
        return False
    return _is_bogus_inferred_cloud_vlc_completion(latest_row)


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


def _compute_watch_seconds_increment(
    existing_row,
    *,
    position_seconds: float,
    now_dt: datetime,
    count_watch_increment: bool = True,
) -> float:
    if not count_watch_increment:
        return 0.0
    raw_delta = position_seconds
    previous_updated_at = None
    if existing_row is not None:
        raw_delta = position_seconds - float(existing_row["position_seconds"] or 0)
        previous_updated_at = _parse_utc_iso(existing_row["updated_at"])

    if raw_delta <= 0:
        return 0.0

    if previous_updated_at is None:
        return round(min(raw_delta, FIRST_PROGRESS_SAMPLE_CAP_SECONDS), 2)

    elapsed_seconds = max((now_dt - previous_updated_at).total_seconds(), 0.0)
    capped_increment = min(
        raw_delta,
        elapsed_seconds + PROGRESS_SAMPLE_SLACK_SECONDS,
        PROGRESS_SAMPLE_MAX_INCREMENT_SECONDS,
    )
    return round(max(capped_increment, 0.0), 2)


def _normalize_event_timestamp(
    occurred_at: str | None = None,
    *,
    fallback_dt: datetime | None = None,
) -> tuple[str, int]:
    fallback = fallback_dt or datetime.now(timezone.utc)
    parsed = _parse_utc_iso(occurred_at)
    resolved = parsed or fallback
    return resolved.isoformat(), int(resolved.timestamp())


def _insert_tracking_event(
    connection,
    *,
    user_id: int,
    media_item_id: int,
    event_type: str,
    playback_mode: str,
    tracking_source: str,
    position_seconds: float | None,
    duration_seconds: float | None,
    completed: bool,
    occurred_at: str,
    recorded_at_epoch: int,
    native_session_id: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO playback_tracking_events (
            user_id,
            media_item_id,
            event_type,
            playback_mode,
            tracking_source,
            native_session_id,
            position_seconds,
            duration_seconds,
            completed,
            occurred_at,
            recorded_at_epoch
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            media_item_id,
            event_type,
            playback_mode,
            tracking_source,
            native_session_id,
            position_seconds,
            duration_seconds,
            int(completed),
            occurred_at,
            recorded_at_epoch,
        ),
    )


def get_progress(
    settings: Settings,
    *,
    user_id: int,
    media_item_id: int,
) -> dict[str, object]:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT media_item_id, position_seconds, duration_seconds, completed, updated_at
            FROM playback_progress
            WHERE user_id = ? AND media_item_id = ?
            """,
            (user_id, media_item_id),
        ).fetchone()
        if row is None:
            return {
                "media_item_id": media_item_id,
                "position_seconds": 0.0,
                "duration_seconds": None,
                "completed": False,
                "updated_at": None,
            }
        return {
            "media_item_id": row["media_item_id"],
            "position_seconds": float(row["position_seconds"] or 0),
            "duration_seconds": row["duration_seconds"],
            "completed": bool(row["completed"]),
            "updated_at": row["updated_at"],
        }


def save_progress(
    settings: Settings,
    *,
    user_id: int,
    media_item_id: int,
    position_seconds: float,
    duration_seconds: float | None,
    completed: bool,
    playback_mode: str | None = None,
    event_type: str | None = None,
    occurred_at: str | None = None,
    native_session_id: str | None = None,
    tracking_source: str = "direct",
    count_watch_increment: bool = True,
) -> dict[str, object]:
    clamped_position = max(position_seconds, 0.0)
    if duration_seconds is not None:
        clamped_position = min(clamped_position, duration_seconds)
    auto_completed = False
    if duration_seconds and duration_seconds > 0:
        remaining = max(duration_seconds - clamped_position, 0.0)
        auto_completed = remaining <= max(duration_seconds * 0.03, 30.0)
    is_completed = completed or auto_completed
    stored_position = 0.0 if is_completed else round(clamped_position, 2)
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    event_occurred_at, recorded_at_epoch = _normalize_event_timestamp(occurred_at, fallback_dt=now_dt)
    with get_connection(settings) as connection:
        existing_row = connection.execute(
            """
            SELECT position_seconds, updated_at, watch_seconds_total, duration_seconds
            FROM playback_progress
            WHERE user_id = ? AND media_item_id = ?
            """,
            (user_id, media_item_id),
        ).fetchone()
        watch_seconds_increment = _compute_watch_seconds_increment(
            existing_row,
            position_seconds=clamped_position,
            now_dt=now_dt,
            count_watch_increment=count_watch_increment,
        )
        previous_watch_seconds_total = float(existing_row["watch_seconds_total"] or 0) if existing_row else 0.0
        watch_seconds_total = round(previous_watch_seconds_total + watch_seconds_increment, 2)
        stored_duration_seconds = duration_seconds
        if stored_duration_seconds is None and existing_row is not None and existing_row["duration_seconds"] is not None:
            stored_duration_seconds = float(existing_row["duration_seconds"])
        connection.execute(
            """
            INSERT INTO playback_progress (
                user_id,
                media_item_id,
                position_seconds,
                duration_seconds,
                watch_seconds_total,
                completed,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, media_item_id) DO UPDATE SET
                position_seconds = excluded.position_seconds,
                duration_seconds = COALESCE(excluded.duration_seconds, playback_progress.duration_seconds),
                watch_seconds_total = excluded.watch_seconds_total,
                completed = excluded.completed,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                media_item_id,
                stored_position,
                stored_duration_seconds,
                watch_seconds_total,
                int(is_completed),
                now,
            ),
        )
        if watch_seconds_increment > 0:
            connection.execute(
                """
                INSERT INTO playback_watch_events (
                    user_id,
                    media_item_id,
                    watched_seconds,
                    recorded_at_epoch
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    user_id,
                    media_item_id,
                    watch_seconds_increment,
                    recorded_at_epoch,
                ),
            )
        if event_type and playback_mode:
            _insert_tracking_event(
                connection,
                user_id=user_id,
                media_item_id=media_item_id,
                event_type=event_type,
                playback_mode=playback_mode,
                tracking_source=tracking_source,
                position_seconds=stored_position if is_completed else clamped_position,
                duration_seconds=stored_duration_seconds,
                completed=is_completed,
                occurred_at=event_occurred_at,
                recorded_at_epoch=recorded_at_epoch,
                native_session_id=native_session_id,
            )
        connection.commit()
    return {
        "media_item_id": media_item_id,
        "position_seconds": stored_position,
        "duration_seconds": stored_duration_seconds,
        "completed": is_completed,
        "updated_at": now,
    }


def record_playback_event(
    settings: Settings,
    *,
    user_id: int,
    media_item_id: int,
    event_type: str,
    playback_mode: str,
    position_seconds: float | None = None,
    duration_seconds: float | None = None,
    occurred_at: str | None = None,
    native_session_id: str | None = None,
    tracking_source: str = "direct",
) -> None:
    now_dt = datetime.now(timezone.utc)
    event_occurred_at, recorded_at_epoch = _normalize_event_timestamp(occurred_at, fallback_dt=now_dt)
    clamped_position = None if position_seconds is None else round(max(position_seconds, 0.0), 2)
    with get_connection(settings) as connection:
        _insert_tracking_event(
            connection,
            user_id=user_id,
            media_item_id=media_item_id,
            event_type=event_type,
            playback_mode=playback_mode,
            tracking_source=tracking_source,
            position_seconds=clamped_position,
            duration_seconds=duration_seconds,
            completed=event_type == "playback_completed",
            occurred_at=event_occurred_at,
            recorded_at_epoch=recorded_at_epoch,
            native_session_id=native_session_id,
        )
        connection.commit()


def refresh_recent_tracking(
    settings: Settings,
    *,
    user_id: int,
) -> dict[str, int]:
    rebuilt_items = 0
    inserted_items = 0

    with get_connection(settings) as connection:
        watch_rows = connection.execute(
            """
            SELECT
                media_item_id,
                ROUND(SUM(watched_seconds), 2) AS watch_seconds_total,
                MAX(recorded_at_epoch) AS last_watch_event_epoch
            FROM playback_watch_events
            WHERE user_id = ?
            GROUP BY media_item_id
            """,
            (user_id,),
        ).fetchall()
        tracking_rows = connection.execute(
            """
            SELECT
                t.media_item_id,
                t.event_type,
                t.playback_mode,
                t.tracking_source,
                t.position_seconds,
                t.duration_seconds,
                t.completed,
                t.recorded_at_epoch,
                COALESCE(m.source_kind, 'local') AS source_kind
            FROM playback_tracking_events t
            JOIN media_items m
              ON m.id = t.media_item_id
            WHERE t.user_id = ?
              AND t.event_type IN ('playback_progress', 'playback_seeked', 'playback_stopped', 'playback_completed')
            ORDER BY t.media_item_id ASC, t.recorded_at_epoch DESC, t.id DESC
            """,
            (user_id,),
        ).fetchall()

        watch_rows_by_media_item_id = {
            int(row["media_item_id"]): row
            for row in watch_rows
        }
        tracking_rows_by_media_item_id: dict[int, list] = {}
        for row in tracking_rows:
            tracking_rows_by_media_item_id.setdefault(int(row["media_item_id"]), []).append(row)

        candidate_media_item_ids = sorted(
            set(watch_rows_by_media_item_id) | set(tracking_rows_by_media_item_id)
        )

        for media_item_id in candidate_media_item_ids:
            watch_row = watch_rows_by_media_item_id.get(media_item_id)
            aggregated_watch_seconds = round(
                max(float(watch_row["watch_seconds_total"] or 0), 0.0) if watch_row is not None else 0.0,
                2,
            )
            last_watch_event_epoch = int(watch_row["last_watch_event_epoch"] or 0) if watch_row is not None else 0
            latest_watch_iso = (
                datetime.fromtimestamp(last_watch_event_epoch, tz=timezone.utc).isoformat()
                if last_watch_event_epoch > 0
                else ""
            )
            tracking_rows_for_media_item = tracking_rows_by_media_item_id.get(media_item_id, [])
            tracking_row = _select_rebuild_tracking_row(tracking_rows_for_media_item)
            ignored_bogus_completion = _rebuild_ignored_bogus_completion(
                tracking_rows_for_media_item,
                tracking_row,
            )
            latest_tracking_epoch = int(tracking_row["recorded_at_epoch"] or 0) if tracking_row is not None else 0
            latest_tracking_iso = (
                datetime.fromtimestamp(latest_tracking_epoch, tz=timezone.utc).isoformat()
                if latest_tracking_epoch > 0
                else ""
            )
            existing_row = connection.execute(
                """
                SELECT
                    position_seconds,
                    duration_seconds,
                    watch_seconds_total,
                    completed,
                    updated_at
                FROM playback_progress
                WHERE user_id = ? AND media_item_id = ?
                LIMIT 1
                """,
                (user_id, media_item_id),
            ).fetchone()

            if existing_row is None:
                if aggregated_watch_seconds <= 0 and tracking_row is None:
                    continue
                tracking_completed = bool(tracking_row["completed"]) if tracking_row is not None else False
                tracking_position_seconds = round(
                    max(float(tracking_row["position_seconds"] or 0), 0.0) if tracking_row is not None else 0.0,
                    2,
                )
                tracking_duration_seconds = (
                    float(tracking_row["duration_seconds"])
                    if tracking_row is not None and tracking_row["duration_seconds"] is not None
                    else None
                )
                inserted_updated_at = max(latest_watch_iso, latest_tracking_iso) or utcnow_iso()
                connection.execute(
                    """
                    INSERT INTO playback_progress (
                        user_id,
                        media_item_id,
                        position_seconds,
                        duration_seconds,
                        watch_seconds_total,
                        completed,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        media_item_id,
                        0.0 if tracking_completed else tracking_position_seconds,
                        tracking_duration_seconds,
                        aggregated_watch_seconds,
                        int(tracking_completed),
                        inserted_updated_at,
                    ),
                )
                inserted_items += 1
                continue

            existing_position_seconds = round(max(float(existing_row["position_seconds"] or 0), 0.0), 2)
            existing_duration_seconds = (
                float(existing_row["duration_seconds"])
                if existing_row["duration_seconds"] is not None
                else None
            )
            existing_watch_seconds = round(max(float(existing_row["watch_seconds_total"] or 0), 0.0), 2)
            existing_updated_at = str(existing_row["updated_at"] or "")
            existing_completed = bool(existing_row["completed"])
            next_watch_seconds = max(existing_watch_seconds, aggregated_watch_seconds)
            next_updated_at = max(existing_updated_at, latest_watch_iso, latest_tracking_iso)
            next_position_seconds = existing_position_seconds
            next_duration_seconds = existing_duration_seconds
            next_completed = existing_completed
            if ignored_bogus_completion:
                next_completed = False

            if tracking_row is not None:
                tracking_completed = bool(tracking_row["completed"])
                next_completed = next_completed or tracking_completed
                if tracking_row["duration_seconds"] is not None:
                    next_duration_seconds = float(tracking_row["duration_seconds"])
                if tracking_completed:
                    next_position_seconds = 0.0
                elif tracking_row["position_seconds"] is not None:
                    next_position_seconds = round(max(float(tracking_row["position_seconds"] or 0), 0.0), 2)

            if (
                next_watch_seconds == existing_watch_seconds
                and next_updated_at == existing_updated_at
                and next_position_seconds == existing_position_seconds
                and next_duration_seconds == existing_duration_seconds
                and next_completed == existing_completed
            ):
                continue

            connection.execute(
                """
                UPDATE playback_progress
                SET
                    position_seconds = ?,
                    duration_seconds = ?,
                    watch_seconds_total = ?,
                    completed = ?,
                    updated_at = ?
                WHERE user_id = ? AND media_item_id = ?
                """,
                (
                    next_position_seconds,
                    next_duration_seconds,
                    next_watch_seconds,
                    int(next_completed),
                    next_updated_at,
                    user_id,
                    media_item_id,
                ),
            )
            rebuilt_items += 1

        connection.commit()

    return {
        "rebuilt_items": rebuilt_items,
        "inserted_items": inserted_items,
    }
