from __future__ import annotations

import json
import logging
import threading
import time

from ..config import Settings
from ..db import utcnow_iso
from ..media_scan import build_local_library_freshness_snapshot, scan_media_library
from .app_settings_service import get_global_app_setting, set_global_app_setting


logger = logging.getLogger(__name__)
LOCAL_LIBRARY_FRESHNESS_SNAPSHOT_KEY = "local_library_freshness_snapshot"
LOCAL_LIBRARY_FRESHNESS_PROBE_STATE_KEY = "local_library_freshness_probe_state"
LOCAL_LIBRARY_FRESHNESS_PROBE_COOLDOWN_SECONDS = 300


class ScanService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._job_lock = threading.Lock()
        self._probe_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._state: dict[str, object] = {
            "running": False,
            "job_id": None,
            "started_at": None,
            "finished_at": None,
            "reason": None,
            "files_seen": 0,
            "files_changed": 0,
            "files_removed": 0,
            "message": None,
        }

    def get_state(self) -> dict[str, object]:
        with self._state_lock:
            return dict(self._state)

    def is_scan_running(self) -> bool:
        with self._state_lock:
            return bool(self._state["running"])

    def enqueue_scan(self, *, reason: str) -> dict[str, object]:
        if not self._job_lock.acquire(blocking=False):
            state = self.get_state()
            state["message"] = "A scan is already running"
            return state
        if reason == "startup":
            should_scan, decision_message = self._should_enqueue_startup_scan()
            if not should_scan:
                logger.info(decision_message)
                self._update_state(
                    running=False,
                    job_id=None,
                    started_at=None,
                    finished_at=utcnow_iso(),
                    reason=reason,
                    files_seen=0,
                    files_changed=0,
                    files_removed=0,
                    message=decision_message,
                )
                self._job_lock.release()
                return self.get_state()
        started_at = utcnow_iso()
        self._update_state(
            running=True,
            started_at=started_at,
            finished_at=None,
            reason=reason,
            files_seen=0,
            files_changed=0,
            files_removed=0,
            message="Scan started",
        )
        worker = threading.Thread(
            target=self._run_scan,
            args=(reason, started_at),
            daemon=True,
            name=f"elvern-scan-{reason}",
        )
        worker.start()
        return self.get_state()

    def _update_state(self, **values: object) -> None:
        with self._state_lock:
            self._state.update(values)

    def _load_local_library_freshness_snapshot(self) -> dict[str, object] | None:
        raw = get_global_app_setting(
            self.settings,
            key=LOCAL_LIBRARY_FRESHNESS_SNAPSHOT_KEY,
        )
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Ignoring invalid local library freshness snapshot")
            return None
        return payload if isinstance(payload, dict) else None

    def _load_local_library_freshness_probe_state(self) -> dict[str, object] | None:
        raw = get_global_app_setting(
            self.settings,
            key=LOCAL_LIBRARY_FRESHNESS_PROBE_STATE_KEY,
        )
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Ignoring invalid local library freshness probe state")
            return None
        return payload if isinstance(payload, dict) else None

    def _store_local_library_freshness_snapshot(self, snapshot: dict[str, object]) -> None:
        set_global_app_setting(
            self.settings,
            key=LOCAL_LIBRARY_FRESHNESS_SNAPSHOT_KEY,
            value=json.dumps(snapshot, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        )

    def _store_local_library_freshness_probe_state(self, state: dict[str, object]) -> None:
        set_global_app_setting(
            self.settings,
            key=LOCAL_LIBRARY_FRESHNESS_PROBE_STATE_KEY,
            value=json.dumps(state, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        )

    def _should_enqueue_local_library_scan(self) -> tuple[bool, str]:
        try:
            current_snapshot = build_local_library_freshness_snapshot(self.settings)
        except Exception:
            logger.exception("Local library freshness check failed; allowing startup scan")
            return True, "Startup scan allowed: local library freshness check failed."

        previous_snapshot = self._load_local_library_freshness_snapshot()
        if not previous_snapshot:
            return True, "Startup scan allowed: no prior local library freshness snapshot."
        if previous_snapshot.get("version") != current_snapshot.get("version"):
            return True, "Startup scan allowed: local library freshness snapshot format changed."
        if previous_snapshot.get("snapshot_state") != "ready":
            return True, "Startup scan allowed: prior local library freshness state is unknown."
        if current_snapshot.get("snapshot_state") != "ready":
            return True, "Startup scan allowed: current local library freshness state is unknown."
        if (
            previous_snapshot.get("media_root") != current_snapshot.get("media_root")
            or previous_snapshot.get("root_identity") != current_snapshot.get("root_identity")
        ):
            return True, "Startup scan allowed: media root identity changed."
        if (
            previous_snapshot.get("top_level_count") != current_snapshot.get("top_level_count")
            or previous_snapshot.get("top_level_fingerprint") != current_snapshot.get("top_level_fingerprint")
        ):
            return True, "Startup scan allowed: top-level library state changed."
        return False, "Startup scan skipped: local library top-level state matches the last successful scan."

    def _should_enqueue_startup_scan(self) -> tuple[bool, str]:
        return self._should_enqueue_local_library_scan()

    def maybe_refresh_local_library(self, *, trigger: str) -> dict[str, object]:
        if not self._probe_lock.acquire(blocking=False):
            return {
                "checked": False,
                "scan_enqueued": False,
                "message": "Local library freshness check already in progress.",
            }

        try:
            if self.is_scan_running():
                return {
                    "checked": False,
                    "scan_enqueued": False,
                    "message": "A scan is already running.",
                }

            now_epoch = int(time.time())
            probe_state = self._load_local_library_freshness_probe_state()
            if probe_state:
                checked_at_epoch = int(probe_state.get("checked_at_epoch") or 0)
                if checked_at_epoch > 0 and (now_epoch - checked_at_epoch) < LOCAL_LIBRARY_FRESHNESS_PROBE_COOLDOWN_SECONDS:
                    return {
                        "checked": False,
                        "scan_enqueued": False,
                        "message": "Local library freshness check skipped: cooldown active.",
                    }

            should_scan, message = self._should_enqueue_local_library_scan()
            self._store_local_library_freshness_probe_state(
                {
                    "checked_at_epoch": now_epoch,
                    "trigger": trigger,
                    "scan_enqueued": should_scan,
                    "message": message,
                }
            )
            if not should_scan:
                return {
                    "checked": True,
                    "scan_enqueued": False,
                    "message": message,
                }

            self.enqueue_scan(reason="opportunistic")
            return {
                "checked": True,
                "scan_enqueued": True,
                "message": message,
            }
        finally:
            self._probe_lock.release()

    def _run_scan(self, reason: str, started_at: str) -> None:
        try:
            logger.info("Starting media scan: %s", reason)
            result = scan_media_library(self.settings, reason=reason)
            try:
                self._store_local_library_freshness_snapshot(
                    build_local_library_freshness_snapshot(self.settings)
                )
            except Exception:
                logger.exception("Failed to store local library freshness snapshot after scan")
        except Exception as exc:
            logger.exception("Media scan failed")
            self._update_state(
                running=False,
                reason=reason,
                started_at=started_at,
                finished_at=utcnow_iso(),
                message=f"Scan failed: {exc}",
            )
        else:
            self._update_state(**result)
        finally:
            self._job_lock.release()
