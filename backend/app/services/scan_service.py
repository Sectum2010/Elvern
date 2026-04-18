from __future__ import annotations

import logging
import threading

from ..config import Settings
from ..db import utcnow_iso
from ..media_scan import scan_media_library
from .cloud_library_service import sync_all_google_drive_sources


logger = logging.getLogger(__name__)


class ScanService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._job_lock = threading.Lock()
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

    def enqueue_scan(self, *, reason: str) -> dict[str, object]:
        if not self._job_lock.acquire(blocking=False):
            state = self.get_state()
            state["message"] = "A scan is already running"
            return state
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

    def _run_scan(self, reason: str, started_at: str) -> None:
        try:
            logger.info("Starting media scan: %s", reason)
            result = scan_media_library(self.settings, reason=reason)
            if reason == "manual":
                cloud_summary = sync_all_google_drive_sources(self.settings)
                sources_synced = int(cloud_summary.get("sources_synced", 0))
                media_rows_written = int(cloud_summary.get("media_rows_written", 0))
                result["message"] = (
                    f"Scan completed. Cloud sources synced: {sources_synced}. "
                    f"Cloud media rows refreshed: {media_rows_written}."
                )
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
