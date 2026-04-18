from __future__ import annotations

from datetime import datetime, timezone
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import Iterable
import uuid


ADMIN_DIRTY_SECTIONS = ("admin_users", "active_sessions")


class AdminEventHub:
    def __init__(self, *, tick_interval_seconds: float = 30.0) -> None:
        self._tick_interval_seconds = max(float(tick_interval_seconds), 5.0)
        self._lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._subscribers: dict[str, Queue[dict[str, object]]] = {}

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = Thread(target=self._run_tick_loop, name="admin-events-tick", daemon=True)
            self._thread.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        with self._lock:
            subscribers = list(self._subscribers.items())
            self._subscribers.clear()
        for _, queue in subscribers:
            self._offer(queue, {"event_type": "stream_shutdown", "occurred_at": _utcnow_iso(), "dirty_sections": []})
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread = None

    def subscribe(self) -> tuple[str, Queue[dict[str, object]]]:
        subscriber_id = uuid.uuid4().hex
        queue: Queue[dict[str, object]] = Queue(maxsize=32)
        with self._lock:
            self._subscribers[subscriber_id] = queue
        self._offer(
            queue,
            {
                "event_type": "stream_connected",
                "occurred_at": _utcnow_iso(),
                "dirty_sections": list(ADMIN_DIRTY_SECTIONS),
            },
        )
        return subscriber_id, queue

    def unsubscribe(self, subscriber_id: str) -> None:
        with self._lock:
            self._subscribers.pop(subscriber_id, None)

    def emit(
        self,
        event_type: str,
        *,
        user_id: int | None = None,
        session_id: int | None = None,
        dirty_sections: Iterable[str] | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "event_type": event_type,
            "occurred_at": _utcnow_iso(),
            "dirty_sections": list(dirty_sections or ADMIN_DIRTY_SECTIONS),
        }
        if user_id is not None:
            payload["user_id"] = int(user_id)
        if session_id is not None:
            payload["session_id"] = int(session_id)
        with self._lock:
            queues = list(self._subscribers.values())
        for queue in queues:
            self._offer(queue, payload)

    def _run_tick_loop(self) -> None:
        while not self._stop_event.wait(self._tick_interval_seconds):
            with self._lock:
                has_subscribers = bool(self._subscribers)
            if not has_subscribers:
                continue
            self.emit("session_status_changed", dirty_sections=ADMIN_DIRTY_SECTIONS)

    def _offer(self, queue: Queue[dict[str, object]], payload: dict[str, object]) -> None:
        try:
            queue.put_nowait(payload)
        except Full:
            try:
                queue.get_nowait()
            except Empty:
                pass
            try:
                queue.put_nowait(payload)
            except Full:
                pass


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


admin_event_hub = AdminEventHub()


def emit_admin_event(
    event_type: str,
    *,
    user_id: int | None = None,
    session_id: int | None = None,
    dirty_sections: Iterable[str] | None = None,
) -> None:
    admin_event_hub.emit(
        event_type,
        user_id=user_id,
        session_id=session_id,
        dirty_sections=dirty_sections,
    )
