from __future__ import annotations

from contextlib import contextmanager
import logging
import time
import uuid


logger = logging.getLogger(__name__)
LIBRARY_VIEW_PLAN_STAGE_NAMES = (
    "accessible_media_sql",
    "continue_watching_sql",
    "watch_event_aggregates",
    "tracking_event_aggregates",
    "hidden_access_loading",
    "user_settings",
    "genre_loading",
    "category_filter",
    "source_filter",
    "genre_filter",
    "quality_filter",
    "hidden_filtering",
    "duplicate_representative",
    "row_decoration",
    "sorting",
    "local_series_rail_build",
    "cloud_series_rail_build",
    "continue_watching_selection",
    "recently_added_selection",
    "poster_url_resolution",
    "v1_serialization",
    "v2_serialization",
    "revision_hash",
    "json_encoding",
    "route_total",
)


class LibraryViewPlanTiming:
    """Request-local, anonymous timing spans for Library view-plan diagnostics."""

    def __init__(self, *, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.correlation_id = uuid.uuid4().hex[:12] if self.enabled else ""
        self._stage_ns: dict[str, int] = (
            {name: 0 for name in LIBRARY_VIEW_PLAN_STAGE_NAMES}
            if self.enabled
            else {}
        )
        self._counts: dict[str, int] = {}

    @contextmanager
    def stage(self, name: str):
        if not self.enabled:
            yield
            return
        started_ns = time.perf_counter_ns()
        try:
            yield
        finally:
            self.add_ns(name, time.perf_counter_ns() - started_ns)

    def add_ns(self, name: str, duration_ns: int) -> None:
        if not self.enabled:
            return
        normalized_name = str(name).strip()
        if not normalized_name:
            return
        self._stage_ns[normalized_name] = self._stage_ns.get(normalized_name, 0) + max(0, int(duration_ns))

    def count(self, name: str, value: int) -> None:
        if self.enabled:
            self._counts[str(name)] = max(0, int(value))

    def snapshot(self) -> dict[str, object]:
        return {
            "correlation_id": self.correlation_id,
            "stages_ms": {
                name: round(duration_ns / 1_000_000, 3)
                for name, duration_ns in sorted(self._stage_ns.items())
            },
            "counts": dict(sorted(self._counts.items())),
        }

    def log(self) -> None:
        if self.enabled:
            logger.info("Library view-plan timing: %s", self.snapshot())
