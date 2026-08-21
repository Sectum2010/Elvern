from __future__ import annotations

import json
import logging
import queue
import secrets
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .capacity import DiagnosticsCapacityGuard
from .catalog import DiagnosticsCatalog
from .clock import monotonic_raw_time_ns
from .constants import (
    CLOCK_ALGORITHM_VERSION,
    DIAGNOSTICS_EMERGENCY_RESERVE_BYTES,
    MAX_EVENT_PAYLOAD_BYTES,
    ROOT_DIRECTORIES,
    SCHEMA_VERSION,
)
from .crypto import DiagnosticsKeyStore
from .exports import export_events
from .fileio import atomic_write_json, ensure_private_directory, resolve_beneath
from .host_sampler import HostDiagnosticsSampler
from .identity import DiagnosticIdentityStore
from .journal import verify_journal
from .privacy import normalize_user_agent, sanitize_event, sanitize_payload
from .schema import (
    PlaybackDiagnosticEvent,
    PlaybackDiagnosticsBatchResponse,
    PlaybackDiagnosticsBootstrapRequest,
    PlaybackDiagnosticsBootstrapResponse,
    PlaybackDiagnosticsClockRequest,
    PlaybackDiagnosticsClockResponse,
    build_server_event,
)
from .session_files import (
    create_session_metadata,
    read_session_events,
    write_manifest,
)
from .summaries import write_summary_files
from .writer import DiagnosticsWriteBatch, DiagnosticsWriter


logger = logging.getLogger(__name__)
MAX_EVENTS_PER_SOURCE_PER_MINUTE = 30_000


class PlaybackDiagnosticsUnavailableError(RuntimeError):
    """Raised only by diagnostics endpoints when the recorder is unavailable."""


class PlaybackDiagnosticsOwnershipError(PermissionError):
    """Raised when a diagnostics source does not belong to the current user."""


class PlaybackDiagnosticsService:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.enabled = bool(settings.playback_diagnostics_enabled)
        self.root = Path(settings.playback_diagnostics_root)
        self.catalog: DiagnosticsCatalog | None = None
        self.key_store: DiagnosticsKeyStore | None = None
        self.identity_store: DiagnosticIdentityStore | None = None
        self.capacity: DiagnosticsCapacityGuard | None = None
        self.writer: DiagnosticsWriter | None = None
        self.host_sampler: HostDiagnosticsSampler | None = None
        self._playback_manager: Any | None = None
        self._lock = threading.RLock()
        self._server_sequences: dict[tuple[str, str], int] = {}
        self._rate_windows: dict[str, tuple[float, int]] = {}
        self._background_threads: set[threading.Thread] = set()
        self._failure_counts: dict[str, int] = {}
        self._observation_queue: queue.Queue[dict[str, Any] | None] = queue.Queue(8_192)
        self._observation_stop = threading.Event()
        self._observation_thread: threading.Thread | None = None
        self._session_created_enqueued: set[str] = set()

    def bind_playback_manager(self, manager: Any) -> None:
        self._playback_manager = manager

    def start(self) -> None:
        if not self.enabled:
            return
        try:
            ensure_private_directory(self.root)
            for relative in ROOT_DIRECTORIES:
                ensure_private_directory(resolve_beneath(self.root, relative))
            self.key_store = DiagnosticsKeyStore(resolve_beneath(self.root, "keys"))
            active_key = self.key_store.load_or_create_active_key()
            self.identity_store = DiagnosticIdentityStore(
                resolve_beneath(self.root, "identities"),
                self.key_store,
                active_key,
            )
            self.capacity = DiagnosticsCapacityGuard(
                self.root,
                hard_cap_bytes=self.settings.playback_diagnostics_max_bytes,
                emergency_reserve_bytes=DIAGNOSTICS_EMERGENCY_RESERVE_BYTES,
                minimum_free_bytes=self.settings.playback_diagnostics_min_free_bytes,
            )
            self.catalog = DiagnosticsCatalog(self.root)
            self.writer = DiagnosticsWriter(
                self.root,
                catalog=self.catalog,
                capacity=self.capacity,
                key_store=self.key_store,
                active_key=active_key,
                failure_callback=self._on_writer_failure,
            )
            self.writer.start()
            self._start_observation_dispatcher()
            self.host_sampler = HostDiagnosticsSampler(
                active_session_ids=self._active_session_ids,
                active_processes=self._active_processes,
                observe=self.observe_event,
                diagnostics_root=self.root,
                transcode_root=Path(self.settings.transcode_dir),
            )
            self.host_sampler.start()
            self.capacity.write_current_status(enabled=True, startup_state="ready")
            self._start_background(self._recover_and_reconcile, "elvern-diagnostics-recovery")
        except Exception as exc:  # noqa: BLE001 - playback must start without diagnostics.
            self.enabled = False
            try:
                self.shutdown()
            except Exception:  # noqa: BLE001 - partial diagnostics cleanup is best effort.
                pass
            logger.warning(
                "Playback diagnostics unavailable at startup: %s",
                exc.__class__.__name__,
            )

    def shutdown(self) -> None:
        if self.host_sampler is not None:
            self.host_sampler.shutdown()
        self._stop_observation_dispatcher()
        writer = self.writer
        if writer is not None:
            writer.flush(timeout=5)
            writer.shutdown(timeout=5)
        with self._lock:
            threads = list(self._background_threads)
        for thread in threads:
            if thread.is_alive():
                thread.join(timeout=1)
        if self.capacity is not None:
            try:
                self.capacity.write_current_status(enabled=self.enabled, startup_state="stopped")
            except Exception:  # noqa: BLE001
                pass

    def observe_playback_session_created(
        self,
        context: dict[str, Any],
        *,
        user_id: int,
        client_user_agent: str | None = None,
    ) -> None:
        if not self._ready:
            return
        context_snapshot = dict(context)
        self._start_background(
            lambda: self._record_playback_session_created(
                context_snapshot,
                user_id=user_id,
                client_user_agent=client_user_agent,
            ),
            f"elvern-diagnostics-session-{str(context.get('playback_session_id') or '')[:12]}",
        )

    def _record_playback_session_created(
        self,
        context: dict[str, Any],
        *,
        user_id: int,
        client_user_agent: str | None,
    ) -> None:
        try:
            metadata = self._ensure_session_metadata(
                context,
                user_id=user_id,
                user_agent=client_user_agent,
            )
            self._ensure_server_source(metadata["playback_session_id"])
            self._enqueue_session_created_once(metadata)
        except Exception as exc:  # noqa: BLE001
            self._record_failure("session_create", exc)

    def bootstrap(
        self,
        payload: PlaybackDiagnosticsBootstrapRequest,
        *,
        user_id: int,
        user_agent: str | None,
    ) -> PlaybackDiagnosticsBootstrapResponse:
        self._require_ready()
        context = self._resolve_playback_context(payload.playback_session_id, user_id=user_id)
        metadata = self._ensure_session_metadata(
            context,
            user_id=user_id,
            user_agent=user_agent,
            platform=payload.platform,
            device_class=payload.device_class,
            browser_family=payload.browser_family,
            browser_version=payload.browser_version,
            os_family=payload.os_family,
            os_version=payload.os_version,
            hls_engine=payload.hls_engine,
            capabilities=payload.capabilities,
        )
        self._ensure_server_source(metadata["playback_session_id"])
        self._enqueue_session_created_once(metadata)
        existing_source = self.catalog.find_client_source(
            payload.playback_session_id,
            payload.client_instance_id,
        )
        source_id = (
            str(existing_source["source_id"])
            if existing_source is not None
            else f"client_{secrets.token_urlsafe(24)}"
        )
        if existing_source is None:
            self.catalog.register_source(
                playback_session_id=payload.playback_session_id,
                source_id=source_id,
                source_type="client",
                client_instance_id=payload.client_instance_id,
            )
        self.observe_event(
            "client_recorder_bootstrapped",
            playback_session_id=payload.playback_session_id,
            event_source="server",
            payload={
                "capabilities": payload.capabilities,
                "state": "active",
            },
            platform=metadata["platform"],
            device_class=metadata["device_class"],
            browser_family=metadata["browser_family"],
            browser_version=metadata["browser_version"],
            os_family=metadata["os_family"],
            os_version=metadata["os_version"],
            hls_engine=metadata["hls_engine"],
        )
        return PlaybackDiagnosticsBootstrapResponse(
            enabled=True,
            diagnostics_session_id=payload.playback_session_id,
            source_id=source_id,
            schema_version=SCHEMA_VERSION,
            client_spool_max_bytes=self.settings.playback_diagnostics_client_spool_max_bytes,
            batch_max_events=self.settings.playback_diagnostics_batch_max_events,
            batch_max_bytes=self.settings.playback_diagnostics_batch_max_bytes,
            clock_algorithm=CLOCK_ALGORITHM_VERSION,
            server_wall_time_ns=str(time.time_ns()),
            server_monotonic_time_ns=str(time.monotonic_ns()),
            ack_watermark=self.catalog.ack_watermark(source_id),
        )

    def ingest(
        self,
        *,
        diagnostics_session_id: str,
        source_id: str,
        events: list[PlaybackDiagnosticEvent],
        user_id: int,
    ) -> PlaybackDiagnosticsBatchResponse:
        self._require_ready()
        self._assert_source_owner(diagnostics_session_id, source_id, user_id=user_id)
        if len(events) > self.settings.playback_diagnostics_batch_max_events:
            raise ValueError("Diagnostics batch contains too many events")
        encoded_size = len(
            json.dumps(
                [event.model_dump(mode="json") for event in events],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if encoded_size > self.settings.playback_diagnostics_batch_max_bytes:
            raise ValueError("Diagnostics batch is too large")
        self._check_rate_limit(source_id, len(events))

        received_wall_ns = time.time_ns()
        received_monotonic_ns = time.monotonic_ns()
        sanitized_events: list[dict[str, Any]] = []
        duplicate = 0
        out_of_order = 0
        watermark = self.catalog.ack_watermark(source_id)
        for event in events:
            if event.playback_session_id != diagnostics_session_id:
                raise PlaybackDiagnosticsOwnershipError("Diagnostics event session mismatch")
            if event.event_source != "client":
                raise ValueError("Client diagnostics batches may only contain client events")
            if self.catalog.event_exists(event.event_id, source_id, event.source_sequence):
                duplicate += 1
                continue
            if event.source_sequence > watermark + 1:
                out_of_order += 1
            raw = event.model_dump(mode="json")
            event_size = len(
                json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            if event_size > MAX_EVENT_PAYLOAD_BYTES:
                raise ValueError("Diagnostics event is too large")
            raw["server_received_wall_time_ns"] = str(received_wall_ns)
            raw["server_received_monotonic_time_ns"] = str(received_monotonic_ns)
            if raw.get("aligned_wall_time_ns") is None:
                client_ms = raw.get("client_wall_time_ms")
                offset_ns = raw.get("clock_offset_ns")
                if client_ms is not None and offset_ns is not None:
                    raw["aligned_wall_time_ns"] = str(
                        int(float(client_ms) * 1_000_000) + int(str(offset_ns))
                    )
                else:
                    raw["aligned_wall_time_ns"] = str(received_wall_ns)
            sanitized_events.append(sanitize_event(raw))

        accepted = 0
        if sanitized_events:
            session = self.catalog.get_session(diagnostics_session_id)
            if session is None:
                raise KeyError("Diagnostics session is missing")
            enqueue_result = self.writer.enqueue(
                DiagnosticsWriteBatch(
                    playback_session_id=diagnostics_session_id,
                    source_id=source_id,
                    source_type="client",
                    session_relative_path=str(session["session_relative_path"]),
                    events=tuple(sanitized_events),
                    enqueued_monotonic_ns=time.monotonic_ns(),
                )
            )
            accepted = enqueue_result.accepted
            duplicate += enqueue_result.duplicate
            host_sampler = self.host_sampler
            if accepted and host_sampler is not None:
                for event in sanitized_events:
                    if event.get("event_name") != "stall_confirmed":
                        continue
                    incident_id = str(event.get("incident_id") or "")
                    if not incident_id:
                        continue
                    self._start_background(
                        lambda session_id=diagnostics_session_id, incident=incident_id: (
                            host_sampler.freeze_incident_ring(session_id, incident)
                        ),
                        f"elvern-diagnostics-incident-{incident_id[:12]}",
                    )
        capacity_state = self.capacity.refresh().state
        return PlaybackDiagnosticsBatchResponse(
            accepted=accepted,
            duplicate=duplicate,
            rejected=max(0, len(events) - accepted - duplicate),
            out_of_order=out_of_order,
            ack_watermark=self.catalog.ack_watermark(source_id),
            capacity_state=capacity_state,
        )

    def clock_exchange(
        self,
        payload: PlaybackDiagnosticsClockRequest,
        *,
        user_id: int,
    ) -> PlaybackDiagnosticsClockResponse:
        self._require_ready()
        self._assert_source_owner(
            payload.diagnostics_session_id,
            payload.source_id,
            user_id=user_id,
        )
        receive_wall = time.time_ns()
        receive_monotonic = time.monotonic_ns()
        raw_monotonic = monotonic_raw_time_ns()
        send_wall = time.time_ns()
        send_monotonic = time.monotonic_ns()
        return PlaybackDiagnosticsClockResponse(
            sample_id=payload.sample_id,
            client_send_wall_time_ms=payload.client_send_wall_time_ms,
            client_send_monotonic_time_us=payload.client_send_monotonic_time_us,
            server_receive_wall_time_ns=str(receive_wall),
            server_receive_monotonic_time_ns=str(receive_monotonic),
            server_send_wall_time_ns=str(send_wall),
            server_send_monotonic_time_ns=str(send_monotonic),
            monotonic_raw_time_ns=str(raw_monotonic) if raw_monotonic is not None else None,
        )

    def close(
        self,
        *,
        playback_session_id: str,
        source_id: str,
        user_id: int,
        reason: str,
        final_source_sequence: int | None,
    ) -> tuple[int, bool]:
        self._require_ready()
        self._assert_source_owner(playback_session_id, source_id, user_id=user_id)
        self.observe_event(
            "session_close",
            playback_session_id=playback_session_id,
            event_source="server",
            priority="critical",
            payload={
                "reason": reason,
                "source_sequence": final_source_sequence,
                "state": "closing",
            },
        )
        self._start_background(
            lambda: self.finalize_session(playback_session_id),
            f"elvern-diagnostics-finalize-{playback_session_id[:12]}",
        )
        return self.catalog.ack_watermark(source_id), True

    def observe_event(
        self,
        event_name: str,
        *,
        playback_session_id: str,
        event_source: str = "server",
        observation_kind: str = "measured_server",
        priority: str = "normal",
        severity: str = "info",
        payload: dict[str, Any] | None = None,
        **identities: Any,
    ) -> None:
        if not self._ready:
            return
        try:
            self._observation_queue.put_nowait(
                {
                    "event_name": event_name,
                    "playback_session_id": playback_session_id,
                    "event_source": event_source,
                    "observation_kind": observation_kind,
                    "priority": priority,
                    "severity": severity,
                    "payload": dict(payload or {}),
                    "identities": dict(identities),
                    "enqueued_monotonic_ns": time.monotonic_ns(),
                }
            )
        except queue.Full:
            self._on_writer_failure(
                "observer_queue_full",
                {"playback_session_id": playback_session_id, "events_dropped": 1},
            )
        except Exception as exc:  # noqa: BLE001 - observer failure cannot affect playback.
            self._record_failure("observe_enqueue", exc)

    def finalize_session(
        self,
        playback_session_id: str,
        *,
        final_state: str = "closed",
    ) -> bool:
        if not self._ready:
            return False
        try:
            self.writer.flush(timeout=10)
            session = self.catalog.get_session(playback_session_id)
            if session is None:
                return False
            metadata = session.get("metadata") or {}
            events, journal_reports = read_session_events(
                self.root,
                str(session["session_relative_path"]),
                self.key_store,
            )
            capacity_state = self.capacity.refresh().state
            write_summary_files(
                self.root,
                str(session["session_relative_path"]),
                metadata=metadata,
                events=events,
                source_stats=self.catalog.source_stats(playback_session_id),
                writer_metrics=self.writer.metrics(),
                capacity_state=capacity_state,
            )
            write_manifest(
                self.root,
                str(session["session_relative_path"]),
                journal_reports=journal_reports,
            )
            session_path = resolve_beneath(self.root, str(session["session_relative_path"]))
            session_json_path = resolve_beneath(session_path, "session.json")
            metadata["state"] = final_state
            metadata["updated_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            atomic_write_json(session_json_path, metadata)
            self.catalog.mark_finalized(playback_session_id, state=final_state)
            return True
        except Exception as exc:  # noqa: BLE001
            self._record_failure("finalize", exc)
            return False

    def finalize_session_async(self, playback_session_id: str) -> None:
        if not self._ready:
            return
        self._start_background(
            lambda: self.finalize_session(playback_session_id),
            f"elvern-diagnostics-finalize-{playback_session_id[:12]}",
        )

    def status(self) -> dict[str, Any]:
        if not self.enabled or not self._ready:
            return {
                "enabled": bool(self.settings.playback_diagnostics_enabled),
                "available": False,
                "root": str(self.root),
                "failure_counts": dict(self._failure_counts),
            }
        snapshot = self.capacity.refresh()
        return {
            "enabled": True,
            "available": True,
            "root": str(self.root),
            "capacity": asdict(snapshot),
            "writer": self.writer.metrics(),
            "observer_queue_depth": self._observation_queue.qsize(),
            "session_count": self.catalog.count_sessions(),
            "failure_counts": dict(self._failure_counts),
        }

    def list_sessions(self, **filters: Any) -> list[dict[str, Any]]:
        self._require_ready()
        return self.catalog.list_sessions(**filters)

    def inspect_session(self, playback_session_id: str) -> dict[str, Any]:
        self._require_ready()
        session = self.catalog.get_session(playback_session_id)
        if session is None:
            raise KeyError("Playback diagnostics session not found")
        events, journals = read_session_events(
            self.root,
            str(session["session_relative_path"]),
            self.key_store,
        )
        return {
            "session": session,
            "sources": self.catalog.source_stats(playback_session_id),
            "journals": journals,
            "event_count": len(events),
            "event_names": sorted({str(event.get("event_name")) for event in events}),
        }

    def verify_session(self, playback_session_id: str) -> dict[str, Any]:
        inspected = self.inspect_session(playback_session_id)
        journals = inspected["journals"]
        return {
            "playback_session_id": playback_session_id,
            "valid": all(journal["valid"] for journal in journals),
            "journals": journals,
            "event_count": inspected["event_count"],
        }

    def export_session(self, playback_session_id: str, *, format_name: str) -> Path:
        self._require_ready()
        session = self.catalog.get_session(playback_session_id)
        if session is None:
            raise KeyError("Playback diagnostics session not found")
        events, _ = read_session_events(
            self.root,
            str(session["session_relative_path"]),
            self.key_store,
        )
        return export_events(
            self.root,
            session_id=playback_session_id,
            events=events,
            format_name=format_name,
        )

    def reconcile(self) -> dict[str, int]:
        self._require_ready()
        return self.catalog.reconcile()

    def unlink_user_identity(self, user_id: int) -> bool:
        if not self._ready:
            return False
        try:
            return self.identity_store.unlink_user(user_id)
        except Exception as exc:  # noqa: BLE001
            self._record_failure("identity_unlink", exc)
            return False

    @property
    def _ready(self) -> bool:
        return bool(
            self.enabled
            and self.catalog is not None
            and self.key_store is not None
            and self.identity_store is not None
            and self.capacity is not None
            and self.writer is not None
        )

    def _require_ready(self) -> None:
        if not self._ready:
            raise PlaybackDiagnosticsUnavailableError("Playback diagnostics are unavailable")

    def _resolve_playback_context(self, playback_session_id: str, *, user_id: int) -> dict[str, Any]:
        manager = self._playback_manager
        if manager is None or not hasattr(manager, "get_diagnostic_session_context"):
            raise PlaybackDiagnosticsUnavailableError("Playback session resolver is unavailable")
        context = manager.get_diagnostic_session_context(playback_session_id, user_id=user_id)
        if not isinstance(context, dict):
            raise KeyError("Browser playback session not found")
        return context

    def _active_session_ids(self) -> tuple[str, ...]:
        manager = self._playback_manager
        if manager is None or not hasattr(manager, "list_active_diagnostic_session_ids"):
            return ()
        try:
            return tuple(manager.list_active_diagnostic_session_ids())
        except Exception:  # noqa: BLE001
            return ()

    def _active_processes(self) -> tuple[dict[str, object], ...]:
        manager = self._playback_manager
        if manager is None or not hasattr(manager, "list_active_diagnostic_processes"):
            return ()
        try:
            return tuple(manager.list_active_diagnostic_processes())
        except Exception:  # noqa: BLE001 - host diagnostics are observation only.
            return ()

    def _ensure_session_metadata(
        self,
        context: dict[str, Any],
        *,
        user_id: int,
        user_agent: str | None = None,
        platform: str | None = None,
        device_class: str | None = None,
        browser_family: str | None = None,
        browser_version: str | None = None,
        os_family: str | None = None,
        os_version: str | None = None,
        hls_engine: str | None = None,
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        subject_id = self.identity_store.get_or_create_subject(user_id)
        owner_hash = self.identity_store.owner_hash(user_id)
        existing = self.catalog.get_session(str(context["playback_session_id"]))
        if existing is not None and str(existing["owner_hash"]) != owner_hash:
            raise PlaybackDiagnosticsOwnershipError("Diagnostics session belongs to another user")
        normalized_ua = normalize_user_agent(user_agent)
        safe_capabilities = {
            str(key)[:128]: value
            for key, value in (capabilities or {}).items()
            if isinstance(value, bool | int | float | str) or value is None
        }
        metadata = create_session_metadata(
            root=self.root,
            project_root=Path(__file__).resolve().parents[4],
            ffmpeg_path=self.settings.ffmpeg_path,
            owner_hash=owner_hash,
            subject_id=subject_id,
            context=context,
            platform=platform or str(context.get("platform") or "unknown"),
            device_class=device_class or str(context.get("device_class") or "unknown"),
            browser_family=browser_family or normalized_ua["browser_family"],
            browser_version=browser_version or normalized_ua["browser_version"],
            os_family=os_family or normalized_ua["os_family"],
            os_version=os_version or normalized_ua["os_version"],
            hls_engine=hls_engine or str(context.get("hls_engine") or "unknown"),
            capabilities=safe_capabilities,
        )
        self.catalog.upsert_session(metadata)
        return metadata

    def _assert_source_owner(
        self,
        playback_session_id: str,
        source_id: str,
        *,
        user_id: int,
    ) -> None:
        owner_hash = self.identity_store.owner_hash(user_id)
        if not self.catalog.session_owned_by(playback_session_id, owner_hash):
            raise PlaybackDiagnosticsOwnershipError("Diagnostics session is not owned by this user")
        if not self.catalog.source_owned_by(source_id, playback_session_id):
            raise PlaybackDiagnosticsOwnershipError("Diagnostics source is not owned by this session")

    def _ensure_server_source(self, playback_session_id: str) -> str:
        return self._ensure_internal_source(playback_session_id, "server")

    def _enqueue_session_created_once(self, metadata: dict[str, Any]) -> None:
        session_id = str(metadata["playback_session_id"])
        with self._lock:
            if session_id in self._session_created_enqueued:
                return
            if self.catalog.session_has_event_name(session_id, "session_created"):
                self._session_created_enqueued.add(session_id)
                return
            self._session_created_enqueued.add(session_id)
        self.observe_event(
            "session_created",
            playback_session_id=session_id,
            event_source="server",
            priority="high",
            payload={
                "media_item_id": metadata["media_item_id"],
                "source_original_filename": metadata["source_original_filename"],
                "source_fingerprint": metadata["source_fingerprint"],
                "profile": metadata["profile"],
                "state": metadata["state"],
            },
            source_kind=metadata["source_kind"],
            playback_mode=metadata["playback_mode"],
            stream_mode=metadata["stream_mode"],
            duration_ms=metadata.get("duration_ms"),
        )

    def _ensure_internal_source(self, playback_session_id: str, source_type: str) -> str:
        source_id = f"{source_type}_{playback_session_id}"
        self.catalog.register_source(
            playback_session_id=playback_session_id,
            source_id=source_id,
            source_type=source_type,
        )
        return source_id

    def _next_internal_sequence(self, playback_session_id: str, source_type: str, source_id: str) -> int:
        key = (playback_session_id, source_type)
        with self._lock:
            if key not in self._server_sequences:
                source = next(
                    (
                        item
                        for item in self.catalog.source_stats(playback_session_id)
                        if item["source_id"] == source_id
                    ),
                    None,
                )
                self._server_sequences[key] = int(source.get("max_seen_sequence") or 0) if source else 0
            self._server_sequences[key] += 1
            return self._server_sequences[key]

    def _check_rate_limit(self, source_id: str, event_count: int) -> None:
        now = time.monotonic()
        with self._lock:
            started, count = self._rate_windows.get(source_id, (now, 0))
            if now - started >= 60:
                started, count = now, 0
            if count + event_count > MAX_EVENTS_PER_SOURCE_PER_MINUTE:
                raise ValueError("Diagnostics event rate limit exceeded")
            self._rate_windows[source_id] = (started, count + event_count)

    def _on_writer_failure(self, reason: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._failure_counts[reason] = self._failure_counts.get(reason, 0) + 1
        if self.capacity is not None:
            try:
                self.capacity.write_current_status(
                    enabled=self.enabled,
                    last_gap_reason=reason,
                    failure_counts=dict(self._failure_counts),
                    writer_queue_depth=self.writer.metrics().get("queue_depth") if self.writer else 0,
                )
            except Exception:  # noqa: BLE001
                return

    def _start_observation_dispatcher(self) -> None:
        if self._observation_thread and self._observation_thread.is_alive():
            return
        self._observation_stop.clear()
        self._observation_thread = threading.Thread(
            target=self._observation_loop,
            daemon=True,
            name="elvern-playback-diagnostics-observer",
        )
        self._observation_thread.start()

    def _stop_observation_dispatcher(self) -> None:
        self._observation_stop.set()
        try:
            self._observation_queue.put_nowait(None)
        except queue.Full:
            pass
        thread = self._observation_thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        self._observation_thread = None

    def _observation_loop(self) -> None:
        while not self._observation_stop.is_set() or not self._observation_queue.empty():
            try:
                observation = self._observation_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if observation is not None:
                    self._persist_observation(observation)
            except Exception as exc:  # noqa: BLE001 - observer failure cannot affect playback.
                self._record_failure("observe_persist", exc)
            finally:
                self._observation_queue.task_done()

    def _persist_observation(self, observation: dict[str, Any]) -> None:
        playback_session_id = str(observation["playback_session_id"])
        event_source = str(observation["event_source"])
        session = self.catalog.get_session(playback_session_id)
        if session is None:
            return
        source_id = self._ensure_internal_source(playback_session_id, event_source)
        sequence = self._next_internal_sequence(playback_session_id, event_source, source_id)
        event = build_server_event(
            event_name=str(observation["event_name"]),
            playback_session_id=playback_session_id,
            source_sequence=sequence,
            event_source=event_source,
            observation_kind=str(observation["observation_kind"]),
            priority=str(observation["priority"]),
            severity=str(observation["severity"]),
            payload=sanitize_payload(observation.get("payload") or {}),
            **dict(observation.get("identities") or {}),
        )
        sanitized = sanitize_event(event.model_dump(mode="json"))
        encoded_size = len(
            json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        if encoded_size > MAX_EVENT_PAYLOAD_BYTES:
            self._on_writer_failure(
                "event_payload_too_large",
                {
                    "playback_session_id": playback_session_id,
                    "events_dropped": 1,
                },
            )
            return
        self.writer.enqueue(
            DiagnosticsWriteBatch(
                playback_session_id=playback_session_id,
                source_id=source_id,
                source_type=event_source,
                session_relative_path=str(session["session_relative_path"]),
                events=(sanitized,),
                enqueued_monotonic_ns=int(observation["enqueued_monotonic_ns"]),
            )
        )

    def _record_failure(self, operation: str, exc: Exception) -> None:
        reason = f"{operation}:{exc.__class__.__name__}"
        with self._lock:
            self._failure_counts[reason] = self._failure_counts.get(reason, 0) + 1

    def _start_background(self, target, name: str) -> None:
        def runner() -> None:
            try:
                target()
            finally:
                with self._lock:
                    self._background_threads.discard(thread)

        thread = threading.Thread(target=runner, daemon=True, name=name)
        with self._lock:
            self._background_threads.add(thread)
        thread.start()

    def _recover_and_reconcile(self) -> None:
        if not self._ready:
            return
        try:
            sessions_root = resolve_beneath(self.root, "sessions")
            quarantine_root = resolve_beneath(self.root, "quarantine")
            for journal_path in sessions_root.glob("**/raw/*.elvd"):
                if journal_path.is_symlink():
                    continue
                verify_journal(
                    journal_path,
                    self.key_store,
                    recover=True,
                    quarantine_root=quarantine_root,
                )
            self.catalog.reconcile()
            for session in self.catalog.list_sessions_for_reconcile():
                if str(session.get("state") or "") in {"closed", "interrupted"}:
                    continue
                self.finalize_session(
                    str(session["playback_session_id"]),
                    final_state="interrupted",
                )
        except Exception as exc:  # noqa: BLE001
            self._record_failure("startup_recovery", exc)


def unlink_diagnostic_identity(settings, user_id: int) -> bool:
    """Best-effort identity unlink for account deletion without raw-data deletion."""

    if not settings.playback_diagnostics_enabled:
        return False
    try:
        root = Path(settings.playback_diagnostics_root)
        key_store = DiagnosticsKeyStore(resolve_beneath(root, "keys"))
        active_key = key_store.load_or_create_active_key()
        store = DiagnosticIdentityStore(
            resolve_beneath(root, "identities"),
            key_store,
            active_key,
        )
        return store.unlink_user(user_id)
    except Exception:  # noqa: BLE001 - account deletion must not fail on diagnostics cleanup.
        return False
