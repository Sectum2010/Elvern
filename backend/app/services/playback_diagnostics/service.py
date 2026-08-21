from __future__ import annotations

import json
import logging
import queue
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .capacity import DiagnosticsCapacityGuard
from .catalog import DiagnosticsCatalog
from .clock import monotonic_raw_time_ns
from .constants import (
    CLOCK_ALGORITHM_VERSION,
    DIAGNOSTICS_CATALOG_MUTATION_RESERVATION_BYTES,
    DIAGNOSTICS_EMERGENCY_RESERVE_BYTES,
    MAX_EVENT_PAYLOAD_BYTES,
    ROOT_DIRECTORIES,
    SCHEMA_VERSION,
    SESSION_VISIBLE_FILES,
)
from .crypto import DiagnosticsKey, DiagnosticsKeyStore, decrypt_blob, encrypt_blob
from .exports import export_events
from .fileio import (
    atomic_write_bytes,
    atomic_write_json,
    encode_json_document,
    ensure_private_directory,
    fsync_directory,
    resolve_beneath,
)
from .host_sampler import HostDiagnosticsSampler
from .eta_observer import forget_eta_session
from .identity import (
    DiagnosticIdentityStore,
    DiagnosticsIdentityKey,
    load_identity_key,
    load_or_create_identity_key,
)
from .journal import verify_journal
from .lease import DiagnosticsRootLease
from .manager_observer import forget_manager_session
from .privacy import normalize_user_agent, sanitize_event, sanitize_payload
from .schema import (
    PlaybackDiagnosticEvent,
    PlaybackDiagnosticsBatchResponse,
    PlaybackDiagnosticsBootstrapRequest,
    PlaybackDiagnosticsBootstrapResponse,
    PlaybackDiagnosticsClockRequest,
    PlaybackDiagnosticsClockResponse,
    SessionMetadataV2,
    build_server_event,
)
from .session_files import (
    build_manifest,
    create_session_metadata,
    read_session_events,
)
from .summaries import build_summary_artifacts
from .writer import DiagnosticsWriteBatch, DiagnosticsWriter, DiagnosticsWriterError


logger = logging.getLogger(__name__)
MAX_EVENTS_PER_SOURCE_PER_MINUTE = 30_000
OBSERVATION_BATCH_MAX_EVENTS = 64
OBSERVATION_BATCH_WAIT_SECONDS = 0.05
CLOSE_REQUEST_WAIT_SECONDS = 5.0
PROVISIONAL_OBSERVATION_TTL_SECONDS = 5.0
PROVISIONAL_OBSERVATION_MAX_SESSIONS = 256
PROVISIONAL_OBSERVATION_MAX_EVENTS_PER_SESSION = 256


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
        self._active_key: DiagnosticsKey | None = None
        self._identity_key: DiagnosticsIdentityKey | None = None
        self.identity_store: DiagnosticIdentityStore | None = None
        self.capacity: DiagnosticsCapacityGuard | None = None
        self.writer: DiagnosticsWriter | None = None
        self.host_sampler: HostDiagnosticsSampler | None = None
        self._root_lease: DiagnosticsRootLease | None = None
        self._playback_manager: Any | None = None
        self._lock = threading.RLock()
        self._server_sequences: OrderedDict[tuple[str, str], int] = OrderedDict()
        self._rate_windows: OrderedDict[str, tuple[float, int]] = OrderedDict()
        self._background_threads: set[threading.Thread] = set()
        self._failure_counts: dict[str, int] = {}
        self._observation_queue: queue.Queue[dict[str, Any] | None] = queue.Queue(8_192)
        self._observation_stop = threading.Event()
        self._observation_thread: threading.Thread | None = None
        self._session_created_enqueued: OrderedDict[str, None] = OrderedDict()
        self._finalization_locks: OrderedDict[str, threading.Lock] = OrderedDict()
        self._closing_sessions: OrderedDict[str, None] = OrderedDict()
        self._closing_barriers: OrderedDict[str, int] = OrderedDict()
        self._sealed_sessions: OrderedDict[str, None] = OrderedDict()
        self._observation_pending_by_session: OrderedDict[str, int] = OrderedDict()
        self._provisional_observations: OrderedDict[
            str,
            tuple[float, list[dict[str, Any]]],
        ] = OrderedDict()
        self._ingest_condition = threading.Condition(self._lock)
        self._ingest_pending_by_session: OrderedDict[str, int] = OrderedDict()
        self._sealing_sessions: OrderedDict[str, None] = OrderedDict()
        self._finalization_tasks: OrderedDict[
            str,
            tuple[threading.Event, dict[str, bool]],
        ] = OrderedDict()
        self._maintenance_mode = False
        self._session_client_addresses: OrderedDict[str, str] = OrderedDict()
        self._last_failure_status_write_monotonic = 0.0

    def bind_playback_manager(self, manager: Any) -> None:
        self._playback_manager = manager

    def start(self) -> None:
        if not self.enabled:
            return
        try:
            self._validate_diagnostics_root()
            ensure_private_directory(self.root)
            self._root_lease = DiagnosticsRootLease(
                self.root,
                mode="writer",
                metadata={"elvern_commit": "runtime"},
            ).acquire()
            for relative in ROOT_DIRECTORIES:
                ensure_private_directory(resolve_beneath(self.root, relative))
            self.capacity = DiagnosticsCapacityGuard(
                self.root,
                hard_cap_bytes=self.settings.playback_diagnostics_max_bytes,
                emergency_reserve_bytes=DIAGNOSTICS_EMERGENCY_RESERVE_BYTES,
                minimum_free_bytes=self.settings.playback_diagnostics_min_free_bytes,
            )
            self.key_store = DiagnosticsKeyStore(
                resolve_beneath(self.root, "keys"),
                capacity=self.capacity,
            )
            active_key = self.key_store.load_or_create_active_key()
            identity_key = load_or_create_identity_key(
                resolve_beneath(self.root, "identities"),
                capacity=self.capacity,
            )
            self._active_key = active_key
            self._identity_key = identity_key
            self.identity_store = DiagnosticIdentityStore(
                resolve_beneath(self.root, "identities"),
                self.key_store,
                active_key,
                identity_key,
                capacity=self.capacity,
            )
            self.catalog = DiagnosticsCatalog(self.root, capacity=self.capacity)
            self.writer = DiagnosticsWriter(
                self.root,
                catalog=self.catalog,
                capacity=self.capacity,
                key_store=self.key_store,
                active_key=active_key,
                failure_callback=self._on_writer_failure,
            )
            self._recover_open_sessions()
            self.writer.start()
            self._start_observation_dispatcher()
            self.host_sampler = HostDiagnosticsSampler(
                active_session_ids=self._active_session_ids,
                active_session_clients=self._active_session_clients,
                active_processes=self._active_processes,
                observe=self.observe_event,
                record_host_observation=self._record_host_observation,
                identity_key=identity_key.material,
                diagnostics_root=self.root,
                transcode_root=Path(self.settings.transcode_dir),
            )
            self.host_sampler.start()
            self.capacity.write_current_status(enabled=True, startup_state="ready")
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

    def start_maintenance(self, *, writer_required: bool) -> None:
        """Open an existing store under the exclusive offline maintenance lease."""

        if not self.enabled:
            raise PlaybackDiagnosticsUnavailableError("Playback diagnostics are disabled")
        if not self.root.is_dir() or self.root.is_symlink():
            raise PlaybackDiagnosticsUnavailableError("Playback diagnostics store is unavailable")
        self._validate_diagnostics_root()
        self._root_lease = DiagnosticsRootLease(
            self.root,
            mode="maintenance",
            metadata={"elvern_commit": "operator"},
        ).acquire()
        try:
            self._maintenance_mode = True
            self.capacity = DiagnosticsCapacityGuard(
                self.root,
                hard_cap_bytes=self.settings.playback_diagnostics_max_bytes,
                emergency_reserve_bytes=DIAGNOSTICS_EMERGENCY_RESERVE_BYTES,
                minimum_free_bytes=self.settings.playback_diagnostics_min_free_bytes,
            )
            self.key_store = DiagnosticsKeyStore(
                resolve_beneath(self.root, "keys"),
                read_only=True,
            )
            active_key = self.key_store.load_or_create_active_key()
            self._active_key = active_key
            self.catalog = DiagnosticsCatalog(self.root, capacity=self.capacity)
            if writer_required:
                self.writer = DiagnosticsWriter(
                    self.root,
                    catalog=self.catalog,
                    capacity=self.capacity,
                    key_store=self.key_store,
                    active_key=active_key,
                    failure_callback=self._on_writer_failure,
                )
                self.writer.start()
        except Exception:
            self.shutdown()
            raise

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
                thread.join(timeout=10)
        if self.capacity is not None and not self._maintenance_mode:
            try:
                self.capacity.write_current_status(enabled=self.enabled, startup_state="stopped")
            except Exception:  # noqa: BLE001
                pass
        if self._root_lease is not None:
            self._root_lease.release()
            self._root_lease = None
        self._maintenance_mode = False
        self._active_key = None
        self._identity_key = None

    def observe_playback_session_created(
        self,
        context: dict[str, Any],
        *,
        user_id: int,
        client_user_agent: str | None = None,
        client_ip: str | None = None,
    ) -> None:
        if not self._ready:
            return
        session_id = str(context.get("playback_session_id") or "")
        if session_id and client_ip:
            with self._lock:
                self._remember(self._session_client_addresses, session_id, str(client_ip))
        context_snapshot = dict(context)
        self._record_playback_session_created(
            context_snapshot,
            user_id=user_id,
            client_user_agent=client_user_agent,
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
            self._flush_provisional_observations(metadata["playback_session_id"])
            self._activate_session(metadata)
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
        existing_session = self.catalog.get_session(payload.playback_session_id)
        existing_source = None
        if existing_session is not None:
            owner_hash = self.identity_store.owner_hash(user_id)
            if str(existing_session.get("owner_hash") or "") != owner_hash:
                raise PlaybackDiagnosticsOwnershipError("Diagnostics session belongs to another user")
            existing_source = self.catalog.find_client_source(
                payload.playback_session_id,
                payload.client_instance_id,
            )
        if existing_session is not None and existing_source is not None:
            return self._bootstrap_response(
                payload.playback_session_id,
                str(existing_source["source_id"]),
            )
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
        self._flush_provisional_observations(metadata["playback_session_id"])
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
            self._catalog_write(
                lambda: self.catalog.register_source(
                    playback_session_id=payload.playback_session_id,
                    source_id=source_id,
                    source_type="client",
                    client_instance_id=payload.client_instance_id,
                )
            )
        self._activate_session(metadata)
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
        return self._bootstrap_response(payload.playback_session_id, source_id)

    def _bootstrap_response(
        self,
        playback_session_id: str,
        source_id: str,
    ) -> PlaybackDiagnosticsBootstrapResponse:
        return PlaybackDiagnosticsBootstrapResponse(
            enabled=True,
            diagnostics_session_id=playback_session_id,
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
        self._enter_ingest(diagnostics_session_id)
        try:
            return self._ingest_events(
                diagnostics_session_id=diagnostics_session_id,
                source_id=source_id,
                events=events,
            )
        finally:
            self._exit_ingest(diagnostics_session_id)

    def _ingest_events(
        self,
        *,
        diagnostics_session_id: str,
        source_id: str,
        events: list[PlaybackDiagnosticEvent],
    ) -> PlaybackDiagnosticsBatchResponse:
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
        session = self.catalog.get_session(diagnostics_session_id)
        if session is None:
            raise KeyError("Diagnostics session is missing")
        session_state = str(session.get("state") or "")
        if session_state in {"sealed", "corrupt"}:
            raise DiagnosticsWriterError(f"Diagnostics session is {session_state}")
        source_row = self.catalog.get_source(source_id)
        final_source_sequence = (
            int(source_row["final_source_sequence"])
            if source_row is not None and source_row.get("final_source_sequence") is not None
            else None
        )
        for event in events:
            if event.playback_session_id != diagnostics_session_id:
                raise PlaybackDiagnosticsOwnershipError("Diagnostics event session mismatch")
            if event.event_source != "client":
                raise ValueError("Client diagnostics batches may only contain client events")
            if final_source_sequence is not None and event.source_sequence > final_source_sequence:
                raise ValueError("Diagnostics event exceeds the declared final source sequence")
            classification = self.catalog.classify_event(
                event.event_id,
                source_id,
                event.source_sequence,
            )
            if classification == "duplicate":
                duplicate += 1
                continue
            if classification == "conflict":
                raise ValueError("Conflicting diagnostics event identity or source sequence")
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
            receipt = self.writer.write_and_wait(
                DiagnosticsWriteBatch(
                    playback_session_id=diagnostics_session_id,
                    source_id=source_id,
                    source_type="client",
                    session_relative_path=str(session["session_relative_path"]),
                    events=tuple(sanitized_events),
                    enqueued_monotonic_ns=time.monotonic_ns(),
                )
            )
            accepted = receipt.accepted
            duplicate += receipt.duplicate
            out_of_order = receipt.out_of_order
            watermark = receipt.ack_watermark
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
            ack_watermark=watermark,
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
    ) -> tuple[int, bool, str]:
        self._require_ready()
        self._assert_source_owner(playback_session_id, source_id, user_id=user_id)
        session = self.catalog.get_session(playback_session_id)
        if session is None:
            raise KeyError("Diagnostics session is missing")
        if str(session.get("state") or "") == "sealed":
            return self.catalog.ack_watermark(source_id), True, "sealed"
        watermark = self.catalog.ack_watermark(source_id)
        source = self.catalog.get_source(source_id)
        if source is None:
            raise KeyError("Diagnostics source is missing")
        final_sequence = (
            max(watermark, int(source.get("max_seen_sequence") or 0))
            if final_source_sequence is None
            else int(final_source_sequence)
        )
        self._catalog_write(
            lambda: self.catalog.set_final_source_sequence(source_id, final_sequence),
            critical=True,
        )
        self._begin_closing(playback_session_id)
        observations_drained = self._drain_observations(
            playback_session_id=playback_session_id,
            timeout=CLOSE_REQUEST_WAIT_SECONDS,
        )
        writer_drained = self.writer.flush_session(
            playback_session_id,
            timeout=CLOSE_REQUEST_WAIT_SECONDS,
        )
        watermark = self.catalog.ack_watermark(source_id)
        if (
            not observations_drained
            or not writer_drained
            or watermark < final_sequence
            or self.catalog.missing_source_ranges(source_id)
        ):
            return watermark, False, "closing"
        finalized = self._finalize_with_bounded_wait(
            playback_session_id,
            close_reason=reason,
            timeout=CLOSE_REQUEST_WAIT_SECONDS,
        )
        return self.catalog.ack_watermark(source_id), finalized, "sealed" if finalized else "closing"

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
        enqueued_monotonic_ns = time.monotonic_ns()
        try:
            with self._lock:
                if (
                    playback_session_id in self._closing_sessions
                    or playback_session_id in self._sealed_sessions
                ):
                    return
                observation = {
                    "event_name": event_name,
                    "playback_session_id": playback_session_id,
                    "event_source": event_source,
                    "observation_kind": observation_kind,
                    "priority": priority,
                    "severity": severity,
                    "payload": dict(payload or {}),
                    "identities": dict(identities),
                    "enqueued_monotonic_ns": enqueued_monotonic_ns,
                }
                self._observation_queue.put_nowait(observation)
                pending = self._observation_pending_by_session.get(playback_session_id, 0) + 1
                self._remember(
                    self._observation_pending_by_session,
                    playback_session_id,
                    pending,
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
        final_state: str = "sealed",
        close_reason: str = "server_finalized",
        require_client_close_barrier: bool = True,
    ) -> bool:
        if not self._ready:
            return False
        lock = self._finalization_lock(playback_session_id)
        with lock:
            sealed = False
            try:
                session = self.catalog.get_session(playback_session_id)
                if session is None:
                    return False
                if str(session.get("state") or "") == "sealed":
                    self._remember(self._sealed_sessions, playback_session_id, None)
                    return True
                if str(session.get("state") or "") == "corrupt":
                    return False
                self._begin_closing(playback_session_id)
                if not self._drain_observations(
                    playback_session_id=playback_session_id,
                    timeout=10.0,
                ):
                    return False
                if not self.writer.flush_session(playback_session_id, timeout=10.0):
                    return False
                if not self._source_barriers_complete(
                    playback_session_id,
                    require_client_final=require_client_close_barrier,
                ):
                    return False
                if not self._begin_sealing(playback_session_id, timeout=10.0):
                    return False
                if not self.writer.flush_session(playback_session_id, timeout=10.0):
                    return False
                if not self._source_barriers_complete(
                    playback_session_id,
                    require_client_final=require_client_close_barrier,
                ):
                    return False
                if not self.catalog.session_has_event_name(playback_session_id, "session_close"):
                    self._write_internal_event(
                        playback_session_id,
                        "session_close",
                        payload={"reason": close_reason, "state": "closing"},
                    )
                if not self.catalog.session_has_event_name(playback_session_id, "session_finalized"):
                    self._write_internal_event(
                        playback_session_id,
                        "session_finalized",
                        payload={"reason": close_reason, "state": "sealed"},
                    )
                if not self.writer.flush_session(playback_session_id, timeout=10.0):
                    return False
                self._catalog_write(
                    lambda: self.catalog.seal_open_source_sequences(
                        playback_session_id,
                        include_client=not require_client_close_barrier,
                    ),
                    critical=True,
                )
                if not self._source_barriers_complete(
                    playback_session_id,
                    require_client_final=require_client_close_barrier,
                    require_all_final=True,
                ):
                    return False
                session = self.catalog.get_session(playback_session_id)
                metadata = dict(session.get("metadata") or {})
                metadata["state"] = final_state
                metadata["updated_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                metadata = SessionMetadataV2.model_validate(metadata).model_dump(mode="json")
                session_path = resolve_beneath(self.root, str(session["session_relative_path"]))
                events, journal_reports = read_session_events(
                    self.root,
                    str(session["session_relative_path"]),
                    self.key_store,
                )
                events.extend(self._linked_host_events(playback_session_id))
                events.sort(
                    key=lambda event: (
                        int(str(event.get("aligned_wall_time_ns") or "0")),
                        str(event.get("event_source") or ""),
                        int(event.get("source_sequence") or 0),
                    )
                )
                capacity_state = self.capacity.refresh().state
                _, _, summary_artifacts = build_summary_artifacts(
                    metadata,
                    events,
                    source_stats=self.catalog.source_stats(playback_session_id),
                    writer_metrics=self.writer.metrics(),
                    capacity_state=capacity_state,
                )
                artifacts = {
                    "session.json": encode_json_document(metadata),
                    **summary_artifacts,
                }
                manifest = build_manifest(
                    self.root,
                    str(session["session_relative_path"]),
                    journal_reports=journal_reports,
                    content_overrides=artifacts,
                )
                artifacts["manifest.json"] = encode_json_document(manifest)
                temporary_peak, visible_before, visible_after = self._artifact_replacement_sizes(
                    session_path,
                    artifacts,
                )
                reservation = self.capacity.reserve(temporary_peak, critical=True)
                try:
                    for name in SESSION_VISIBLE_FILES:
                        payload = artifacts.get(name)
                        if payload is not None:
                            atomic_write_bytes(resolve_beneath(session_path, name), payload)
                    fsync_directory(session_path)
                    reservation.commit(0)
                    self.capacity.account_replacement(
                        old_size=visible_before,
                        new_size=visible_after,
                    )
                except Exception:
                    current_size = self._visible_session_size(session_path)
                    reservation.commit(0)
                    self.capacity.account_replacement(
                        old_size=visible_before,
                        new_size=current_size,
                    )
                    raise
                self._catalog_write(
                    lambda: self.catalog.mark_finalized(playback_session_id, state="sealed"),
                    critical=True,
                )
                sealed = True
                source_ids = {
                    str(source["source_id"])
                    for source in self.catalog.source_stats(playback_session_id)
                }
                with self._lock:
                    self._closing_sessions.pop(playback_session_id, None)
                    self._closing_barriers.pop(playback_session_id, None)
                    self._observation_pending_by_session.pop(playback_session_id, None)
                    self._ingest_pending_by_session.pop(playback_session_id, None)
                    self._sealing_sessions.pop(playback_session_id, None)
                    self._session_client_addresses.pop(playback_session_id, None)
                    for sequence_key in tuple(self._server_sequences):
                        if sequence_key[0] == playback_session_id:
                            self._server_sequences.pop(sequence_key, None)
                    self._session_created_enqueued.pop(playback_session_id, None)
                    self._provisional_observations.pop(playback_session_id, None)
                    self._finalization_locks.pop(playback_session_id, None)
                    for source_id in source_ids:
                        self._rate_windows.pop(source_id, None)
                    self._remember(self._sealed_sessions, playback_session_id, None)
                if self.host_sampler is not None:
                    self.host_sampler.forget_session(playback_session_id)
                forget_eta_session(playback_session_id)
                forget_manager_session(playback_session_id)
                return True
            except Exception as exc:  # noqa: BLE001
                self._record_failure("finalize", exc)
                return False
            finally:
                if not sealed:
                    with self._lock:
                        self._sealing_sessions.pop(playback_session_id, None)
                        self._ingest_condition.notify_all()

    def finalize_session_async(self, playback_session_id: str) -> None:
        if not self._ready:
            return
        session = self.catalog.get_session(playback_session_id)
        if session is not None and str(session.get("state") or "") == "active":
            self._catalog_write(
                lambda: self.catalog.set_session_state(
                    playback_session_id,
                    "interrupted_recoverable",
                ),
                critical=True,
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
        events.extend(self._linked_host_events(playback_session_id))
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

    def export_session(
        self,
        playback_session_id: str,
        *,
        format_name: str,
        events: list[dict[str, Any]] | None = None,
    ) -> Path:
        self._require_ready()
        session = self.catalog.get_session(playback_session_id)
        if session is None:
            raise KeyError("Playback diagnostics session not found")
        if str(session.get("state") or "") != "sealed":
            raise ValueError("Only sealed playback diagnostics sessions may be exported")
        if events is None:
            events, _ = read_session_events(
                self.root,
                str(session["session_relative_path"]),
                self.key_store,
            )
            events.extend(self._linked_host_events(playback_session_id))
        return export_events(
            self.root,
            session_id=playback_session_id,
            events=events,
            format_name=format_name,
            capacity=self.capacity,
        )

    def reconcile(self) -> dict[str, int]:
        if self.catalog is None or self.capacity is None:
            raise PlaybackDiagnosticsUnavailableError("Playback diagnostics are unavailable")
        payload = self._catalog_write(
            self.catalog.reconcile,
            estimated_bytes=64 * 1024 * 1024,
        )
        snapshot = self.capacity.reconcile_usage()
        return {**payload, "usage_bytes": snapshot.usage_bytes}

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
        if self._maintenance_mode:
            return bool(
                self.enabled
                and self.catalog is not None
                and self.key_store is not None
                and self.capacity is not None
            )
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

    def _active_session_clients(self) -> dict[str, str]:
        active = set(self._active_session_ids())
        with self._lock:
            return {
                session_id: address
                for session_id, address in self._session_client_addresses.items()
                if session_id in active
            }

    def _catalog_storage_size(self) -> int:
        if self.catalog is None:
            return 0
        return sum(
            candidate.stat().st_size
            for candidate in (
                self.catalog.path,
                Path(f"{self.catalog.path}-wal"),
                Path(f"{self.catalog.path}-shm"),
            )
            if candidate.exists() and candidate.is_file() and not candidate.is_symlink()
        )

    def _catalog_write(
        self,
        operation,
        *,
        estimated_bytes: int = DIAGNOSTICS_CATALOG_MUTATION_RESERVATION_BYTES,
        critical: bool = False,
    ):
        """Run one catalog mutation under the authoritative capacity ledger."""

        if self.capacity is None:
            raise PlaybackDiagnosticsUnavailableError("Playback diagnostics capacity is unavailable")
        before = self._catalog_storage_size()
        reservation = self.capacity.reserve(estimated_bytes, critical=critical)
        try:
            result = operation()
        except Exception:
            after = self._catalog_storage_size()
            reservation.commit(0)
            self.capacity.account_replacement(old_size=before, new_size=after)
            raise
        after = self._catalog_storage_size()
        reservation.commit(0)
        self.capacity.account_replacement(old_size=before, new_size=after)
        return result

    def _record_host_observation(
        self,
        event_name: str,
        *,
        sample_id: str,
        payload: dict[str, Any],
        session_links: tuple[tuple[str, str | None, str | None], ...],
        observed_wall_time_ns: int,
        observed_monotonic_time_ns: int,
    ) -> None:
        if not self._ready or self._active_key is None:
            return
        try:
            links = tuple(
                link
                for link in session_links
                if self.catalog.get_session(link[0]) is not None
            )
            if not links:
                return
            sanitized_payload = sanitize_payload(payload)
            context = f"playback-diagnostics-host:{sample_id}".encode("utf-8")
            encrypted = encrypt_blob(
                self._active_key,
                json.dumps(
                    sanitized_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                context=context,
            )
            estimated_growth = len(encrypted) + 1_048_576
            reservation = self.capacity.reserve(estimated_growth)
            catalog_before = self._catalog_storage_size()
            try:
                inserted = self.catalog.record_host_observation(
                    sample_id=sample_id,
                    event_name=event_name,
                    observed_wall_time_ns=str(observed_wall_time_ns),
                    observed_monotonic_time_ns=str(observed_monotonic_time_ns),
                    encrypted_payload=encrypted,
                    links=links,
                )
                catalog_after = self._catalog_storage_size()
                reservation.commit(0)
                self.capacity.account_replacement(
                    old_size=catalog_before,
                    new_size=catalog_after,
                )
            except Exception:
                catalog_after = self._catalog_storage_size()
                reservation.commit(0)
                self.capacity.account_replacement(
                    old_size=catalog_before,
                    new_size=catalog_after,
                )
                raise
        except Exception as exc:  # noqa: BLE001 - host diagnostics are observer-only.
            self._record_failure("host_observation", exc)

    def _linked_host_events(self, playback_session_id: str) -> list[dict[str, Any]]:
        if self.catalog is None or self.key_store is None:
            return []
        events: list[dict[str, Any]] = []
        for row in self.catalog.linked_host_observations(playback_session_id):
            sample_id = str(row.get("sample_id") or "")
            try:
                decoded = decrypt_blob(
                    self.key_store,
                    bytes(row["encrypted_payload"]),
                    context=f"playback-diagnostics-host:{sample_id}".encode("utf-8"),
                )
                payload = json.loads(decoded.decode("utf-8"))
            except (KeyError, OSError, UnicodeDecodeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            incident_id = str(row.get("incident_id") or "") or None
            incident_phase = str(row.get("incident_phase") or "") or None
            if incident_phase:
                payload["incident_phase"] = incident_phase
            payload["host_sample_id"] = sample_id
            events.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "event_id": sample_id,
                    "event_name": str(row.get("event_name") or "host_observation"),
                    "event_source": "host",
                    "playback_session_id": playback_session_id,
                    "source_sequence": 0,
                    "event_sequence": 0,
                    "aligned_wall_time_ns": str(row.get("observed_wall_time_ns") or "0"),
                    "server_monotonic_time_ns": str(
                        row.get("observed_monotonic_time_ns") or "0"
                    ),
                    "observation_kind": "measured_kernel",
                    "incident_id": incident_id,
                    "payload": payload,
                }
            )
        return events

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
        session_id = str(context["playback_session_id"])
        with self._finalization_lock(session_id):
            subject_id = self.identity_store.get_or_create_subject(user_id)
            owner_hash = self.identity_store.owner_hash(user_id)
            existing = self.catalog.get_session(session_id)
            if existing is not None and str(existing["owner_hash"]) != owner_hash:
                raise PlaybackDiagnosticsOwnershipError("Diagnostics session belongs to another user")
            if existing is not None and existing.get("metadata"):
                return SessionMetadataV2.model_validate(existing["metadata"]).model_dump(mode="json")
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
                write=False,
            )
            encoded = encode_json_document(metadata)
            session_path = ensure_private_directory(
                resolve_beneath(self.root, metadata["session_relative_path"])
            )
            ensure_private_directory(resolve_beneath(session_path, "raw"))
            session_json = resolve_beneath(session_path, "session.json")
            old_session_size = session_json.stat().st_size if session_json.is_file() else 0
            catalog_before = self._catalog_storage_size()
            reservation = self.capacity.reserve(
                len(encoded) + DIAGNOSTICS_CATALOG_MUTATION_RESERVATION_BYTES,
                critical=True,
            )
            try:
                self.catalog.upsert_session(metadata)
                atomic_write_bytes(session_json, encoded)
                catalog_after = self._catalog_storage_size()
                reservation.commit(0)
                self.capacity.account_replacement(
                    old_size=catalog_before + old_session_size,
                    new_size=catalog_after + len(encoded),
                )
            except Exception:
                catalog_after = self._catalog_storage_size()
                session_after = session_json.stat().st_size if session_json.is_file() else 0
                reservation.commit(0)
                self.capacity.account_replacement(
                    old_size=catalog_before + old_session_size,
                    new_size=catalog_after + session_after,
                )
                raise
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
                self._remember(self._session_created_enqueued, session_id, None)
                return
            self._remember(self._session_created_enqueued, session_id, None)
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
        self._catalog_write(
            lambda: self.catalog.register_source(
                playback_session_id=playback_session_id,
                source_id=source_id,
                source_type=source_type,
            )
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
                self._remember(
                    self._server_sequences,
                    key,
                    int(source.get("max_seen_sequence") or 0) if source else 0,
                )
            self._server_sequences[key] += 1
            self._server_sequences.move_to_end(key)
            return self._server_sequences[key]

    def _check_rate_limit(self, source_id: str, event_count: int) -> None:
        now = time.monotonic()
        with self._lock:
            started, count = self._rate_windows.get(source_id, (now, 0))
            if now - started >= 60:
                started, count = now, 0
            if count + event_count > MAX_EVENTS_PER_SOURCE_PER_MINUTE:
                raise ValueError("Diagnostics event rate limit exceeded")
            self._remember(self._rate_windows, source_id, (started, count + event_count))

    def _on_writer_failure(self, reason: str, payload: dict[str, Any]) -> None:
        del payload
        with self._lock:
            self._failure_counts[reason] = self._failure_counts.get(reason, 0) + 1
            now = time.monotonic()
            if now - self._last_failure_status_write_monotonic < 1.0:
                return
            self._last_failure_status_write_monotonic = now
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
                self._prune_provisional_observations()
                continue
            observations: list[dict[str, Any]] = []
            queue_items = 1
            if observation is not None:
                observations.append(observation)
            deadline = time.monotonic() + OBSERVATION_BATCH_WAIT_SECONDS
            while len(observations) < OBSERVATION_BATCH_MAX_EVENTS:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    candidate = self._observation_queue.get(timeout=remaining)
                except queue.Empty:
                    break
                queue_items += 1
                if candidate is not None:
                    observations.append(candidate)
            try:
                if observations:
                    self._persist_observations(observations)
            except Exception as exc:  # noqa: BLE001 - observer failure cannot affect playback.
                self._record_failure("observe_persist", exc)
            finally:
                with self._lock:
                    for queued in observations:
                        session_id = str(queued.get("playback_session_id") or "")
                        pending = max(
                            0,
                            self._observation_pending_by_session.get(session_id, 0) - 1,
                        )
                        if pending:
                            self._remember(
                                self._observation_pending_by_session,
                                session_id,
                                pending,
                            )
                        else:
                            self._observation_pending_by_session.pop(session_id, None)
                    self._ingest_condition.notify_all()
                for _index in range(queue_items):
                    self._observation_queue.task_done()

    def _persist_observations(self, observations: list[dict[str, Any]]) -> None:
        grouped: OrderedDict[
            tuple[str, str, str, str],
            list[dict[str, Any]],
        ] = OrderedDict()
        for observation in observations:
            playback_session_id = str(observation["playback_session_id"])
            event_source = str(observation["event_source"])
            session = self.catalog.get_session(playback_session_id)
            if session is None:
                self._buffer_provisional_observation(observation)
                continue
            state = str(session.get("state") or "")
            if state in {"sealed", "corrupt"}:
                continue
            if state == "closing":
                with self._lock:
                    barrier = self._closing_barriers.get(playback_session_id)
                if barrier is None or int(observation["enqueued_monotonic_ns"]) > barrier:
                    continue
            source_id = self._ensure_internal_source(playback_session_id, event_source)
            sequence = self._next_internal_sequence(
                playback_session_id,
                event_source,
                source_id,
            )
            try:
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
            except Exception as exc:  # noqa: BLE001 - one bad observer event is isolated.
                self._record_failure("observe_sanitize", exc)
                continue
            encoded_size = len(
                json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            if encoded_size > MAX_EVENT_PAYLOAD_BYTES:
                self._on_writer_failure(
                    "event_payload_too_large",
                    {
                        "playback_session_id": playback_session_id,
                        "events_dropped": 1,
                    },
                )
                continue
            key = (
                playback_session_id,
                source_id,
                event_source,
                str(session["session_relative_path"]),
            )
            grouped.setdefault(key, []).append(sanitized)

        for (
            playback_session_id,
            source_id,
            event_source,
            session_relative_path,
        ), events in grouped.items():
            self.writer.enqueue(
                DiagnosticsWriteBatch(
                    playback_session_id=playback_session_id,
                    source_id=source_id,
                    source_type=event_source,
                    session_relative_path=session_relative_path,
                    events=tuple(events),
                    enqueued_monotonic_ns=min(
                        int(observation["enqueued_monotonic_ns"])
                        for observation in observations
                        if str(observation["playback_session_id"]) == playback_session_id
                        and str(observation["event_source"]) == event_source
                    ),
                )
            )

    def _buffer_provisional_observation(self, observation: dict[str, Any]) -> None:
        session_id = str(observation.get("playback_session_id") or "")
        if not session_id:
            return
        now = time.monotonic()
        self._prune_provisional_observations(now=now)
        with self._lock:
            row = self._provisional_observations.get(session_id)
            created_at, buffered = row if row is not None else (now, [])
            if len(buffered) >= PROVISIONAL_OBSERVATION_MAX_EVENTS_PER_SESSION:
                self._on_writer_failure(
                    "provisional_observation_overflow",
                    {"playback_session_id": session_id, "events_dropped": 1},
                )
                return
            buffered.append(observation)
            self._provisional_observations[session_id] = (created_at, buffered)
            self._provisional_observations.move_to_end(session_id)
            while len(self._provisional_observations) > PROVISIONAL_OBSERVATION_MAX_SESSIONS:
                dropped_session, (_created, dropped) = self._provisional_observations.popitem(
                    last=False
                )
                self._on_writer_failure(
                    "provisional_observation_overflow",
                    {
                        "playback_session_id": dropped_session,
                        "events_dropped": len(dropped),
                    },
                )

    def _flush_provisional_observations(self, playback_session_id: str) -> None:
        with self._lock:
            row = self._provisional_observations.pop(playback_session_id, None)
        if row is None:
            return
        _created_at, buffered = row
        for observation in buffered:
            try:
                with self._lock:
                    self._observation_queue.put_nowait(observation)
                    pending = self._observation_pending_by_session.get(
                        playback_session_id,
                        0,
                    ) + 1
                    self._remember(
                        self._observation_pending_by_session,
                        playback_session_id,
                        pending,
                    )
            except queue.Full:
                self._on_writer_failure(
                    "observer_queue_full",
                    {"playback_session_id": playback_session_id, "events_dropped": 1},
                )

    def _prune_provisional_observations(self, *, now: float | None = None) -> None:
        cutoff = (time.monotonic() if now is None else now) - PROVISIONAL_OBSERVATION_TTL_SECONDS
        expired: list[tuple[str, int]] = []
        with self._lock:
            for session_id, (created_at, buffered) in tuple(
                self._provisional_observations.items()
            ):
                if created_at > cutoff:
                    continue
                self._provisional_observations.pop(session_id, None)
                expired.append((session_id, len(buffered)))
        for session_id, count in expired:
            self._on_writer_failure(
                "provisional_observation_expired",
                {"playback_session_id": session_id, "events_dropped": count},
            )

    def _write_internal_event(
        self,
        playback_session_id: str,
        event_name: str,
        *,
        payload: dict[str, Any],
    ) -> None:
        session = self.catalog.get_session(playback_session_id)
        if session is None:
            raise KeyError("Diagnostics session is missing")
        source_id = self._ensure_internal_source(playback_session_id, "server")
        sequence = self._next_internal_sequence(playback_session_id, "server", source_id)
        event = build_server_event(
            event_name=event_name,
            playback_session_id=playback_session_id,
            source_sequence=sequence,
            event_source="server",
            priority="critical",
            payload=sanitize_payload(payload),
        )
        self.writer.write_and_wait(
            DiagnosticsWriteBatch(
                playback_session_id=playback_session_id,
                source_id=source_id,
                source_type="server",
                session_relative_path=str(session["session_relative_path"]),
                events=(sanitize_event(event.model_dump(mode="json")),),
                enqueued_monotonic_ns=time.monotonic_ns(),
            )
        )

    def _activate_session(self, metadata: dict[str, Any]) -> None:
        session_id = str(metadata["playback_session_id"])
        current = self.catalog.get_session(session_id)
        if current is None or str(current.get("state") or "") not in {"registering", "provisional"}:
            return
        active_metadata = {**metadata, "state": "active"}
        active_metadata = SessionMetadataV2.model_validate(active_metadata).model_dump(mode="json")
        session_path = resolve_beneath(self.root, active_metadata["session_relative_path"])
        session_json = resolve_beneath(session_path, "session.json")
        old_size = session_json.stat().st_size if session_json.exists() else 0
        encoded_size = len(json.dumps(active_metadata, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")) + 1
        reservation = self.capacity.reserve(encoded_size, critical=True)
        try:
            atomic_write_json(session_json, active_metadata)
            reservation.commit(0)
            self.capacity.account_replacement(old_size=old_size, new_size=session_json.stat().st_size)
            self._catalog_write(
                lambda: self.catalog.set_session_state(session_id, "active"),
                critical=True,
            )
        except Exception:
            reservation.release()
            raise

    def _begin_closing(self, playback_session_id: str) -> None:
        with self._lock:
            if playback_session_id in self._sealed_sessions:
                return
            if playback_session_id not in self._closing_sessions:
                self._remember(self._closing_sessions, playback_session_id, None)
                self._remember(
                    self._closing_barriers,
                    playback_session_id,
                    time.monotonic_ns(),
                )
        session = self.catalog.get_session(playback_session_id)
        if session is not None and str(session.get("state") or "") not in {"sealed", "corrupt"}:
            self._catalog_write(
                lambda: self.catalog.set_session_state(playback_session_id, "closing"),
                critical=True,
            )

    def _enter_ingest(self, playback_session_id: str) -> None:
        with self._ingest_condition:
            if (
                playback_session_id in self._sealing_sessions
                or playback_session_id in self._sealed_sessions
            ):
                raise DiagnosticsWriterError("Diagnostics session is sealing or sealed")
            pending = self._ingest_pending_by_session.get(playback_session_id, 0) + 1
            self._remember(self._ingest_pending_by_session, playback_session_id, pending)

    def _exit_ingest(self, playback_session_id: str) -> None:
        with self._ingest_condition:
            remaining = max(
                0,
                self._ingest_pending_by_session.get(playback_session_id, 0) - 1,
            )
            if remaining:
                self._remember(
                    self._ingest_pending_by_session,
                    playback_session_id,
                    remaining,
                )
            else:
                self._ingest_pending_by_session.pop(playback_session_id, None)
            self._ingest_condition.notify_all()

    def _begin_sealing(self, playback_session_id: str, *, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._ingest_condition:
            self._remember(self._sealing_sessions, playback_session_id, None)
            while self._ingest_pending_by_session.get(playback_session_id, 0):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._sealing_sessions.pop(playback_session_id, None)
                    self._ingest_condition.notify_all()
                    return False
                self._ingest_condition.wait(timeout=remaining)
            return True

    def _source_barriers_complete(
        self,
        playback_session_id: str,
        *,
        require_client_final: bool,
        require_all_final: bool = False,
    ) -> bool:
        for source in self.catalog.source_stats(playback_session_id):
            source_id = str(source["source_id"])
            source_type = str(source.get("source_type") or "")
            final = source.get("final_source_sequence")
            if final is None:
                if require_all_final or (require_client_final and source_type == "client"):
                    return False
                continue
            final_sequence = int(final)
            if int(source.get("ack_watermark") or 0) < final_sequence:
                return False
            if self.catalog.missing_source_ranges(source_id):
                return False
        return True

    def _finalize_with_bounded_wait(
        self,
        playback_session_id: str,
        *,
        close_reason: str,
        timeout: float,
    ) -> bool:
        with self._lock:
            task = self._finalization_tasks.get(playback_session_id)
            if task is None or task[0].is_set():
                completed = threading.Event()
                result = {"finalized": False}
                task = (completed, result)
                self._remember(self._finalization_tasks, playback_session_id, task)

                def finalize() -> None:
                    try:
                        result["finalized"] = self.finalize_session(
                            playback_session_id,
                            close_reason=close_reason,
                            require_client_close_barrier=True,
                        )
                    finally:
                        completed.set()

                self._start_background(
                    finalize,
                    f"elvern-diagnostics-finalize-{playback_session_id[:12]}",
                )
        completed, result = task
        completed.wait(timeout=max(0.0, timeout))
        if not completed.is_set():
            return False
        with self._lock:
            if self._finalization_tasks.get(playback_session_id) is task:
                self._finalization_tasks.pop(playback_session_id, None)
        return bool(result.get("finalized"))

    def _drain_observations(self, *, playback_session_id: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._ingest_condition:
            while self._observation_pending_by_session.get(playback_session_id, 0):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._ingest_condition.wait(timeout=remaining)
            return True

    def _finalization_lock(self, playback_session_id: str) -> threading.Lock:
        with self._lock:
            lock = self._finalization_locks.get(playback_session_id)
            if lock is None:
                lock = threading.Lock()
                self._remember(self._finalization_locks, playback_session_id, lock)
            else:
                self._finalization_locks.move_to_end(playback_session_id)
            return lock

    @staticmethod
    def _remember(mapping: OrderedDict, key: Any, value: Any, *, limit: int = 4_096) -> None:
        mapping[key] = value
        mapping.move_to_end(key)
        while len(mapping) > limit:
            mapping.popitem(last=False)

    @staticmethod
    def _visible_session_size(session_path: Path) -> int:
        names = (
            "session.json",
            "summary.json",
            "completeness.json",
            "timeline.csv",
            "summary.md",
            "manifest.json",
        )
        return sum(
            path.stat().st_size
            for name in names
            if (path := session_path / name).is_file() and not path.is_symlink()
        )

    @staticmethod
    def _artifact_replacement_sizes(
        session_path: Path,
        artifacts: dict[str, bytes],
    ) -> tuple[int, int, int]:
        """Return exact atomic-write peak, old size, and final size."""

        old_sizes: dict[str, int] = {}
        for name in SESSION_VISIBLE_FILES:
            path = resolve_beneath(session_path, name)
            old_sizes[name] = (
                path.stat().st_size
                if path.is_file() and not path.is_symlink()
                else 0
            )
        current_delta = 0
        peak_delta = 0
        for name in SESSION_VISIBLE_FILES:
            payload = artifacts.get(name)
            if payload is None:
                continue
            new_size = len(payload)
            # Atomic replacement temporarily retains the old file while the
            # complete new file exists beside it.
            peak_delta = max(peak_delta, current_delta + new_size)
            current_delta += new_size - old_sizes[name]
        old_total = sum(old_sizes.values())
        final_total = old_total + current_delta
        return max(0, peak_delta), old_total, final_total

    def _validate_diagnostics_root(self) -> None:
        diagnostics_root = self.root.resolve(strict=False)
        project_root = Path(__file__).resolve().parents[4]
        candidates = {
            Path(self.settings.media_root),
            Path(self.settings.transcode_dir),
            Path(self.settings.poster_display_cache_dir),
            Path(self.settings.helper_releases_dir),
            Path(self.settings.db_path).parent / "backups",
            project_root / "backend" / "app",
            project_root / "backend" / "data" / "backups",
            project_root / "frontend" / "dist",
            project_root / "frontend" / "public",
            project_root / "frontend" / "src",
        }
        for configured in (
            getattr(self.settings, "library_root_linux", None),
            getattr(self.settings, "library_root_windows", None),
            getattr(self.settings, "library_root_mac", None),
        ):
            if configured:
                candidates.add(Path(configured))
        for candidate in candidates:
            protected = candidate.expanduser().resolve(strict=False)
            if diagnostics_root == protected:
                raise ValueError("Playback diagnostics root overlaps protected application data")
            try:
                diagnostics_root.relative_to(protected)
            except ValueError:
                pass
            else:
                raise ValueError("Playback diagnostics root is inside protected application data")
            try:
                protected.relative_to(diagnostics_root)
            except ValueError:
                pass
            else:
                raise ValueError("Playback diagnostics root contains protected application data")

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

    def _recover_open_sessions(self) -> None:
        """Repair/reindex only unsealed sessions while the root lease is held."""

        quarantine_root = resolve_beneath(self.root, "quarantine")
        for session in self.catalog.list_unsealed_sessions():
            session_id = str(session["playback_session_id"])
            session_path = resolve_beneath(self.root, str(session["session_relative_path"]))
            raw_path = resolve_beneath(session_path, "raw")
            corrupt = False
            if raw_path.is_dir():
                for journal_path in sorted(raw_path.glob("*.elvd")):
                    if journal_path.is_symlink():
                        corrupt = True
                        break
                    verification, events = verify_journal(
                        journal_path,
                        self.key_store,
                        recover=True,
                        quarantine_root=quarantine_root,
                        include_events=True,
                        annotate_events=True,
                        capacity=self.capacity,
                    )
                    if not verification.valid:
                        corrupt = True
                        break
                    source_type = verification.source_type or str(
                        (events[0].get("event_source") if events else None) or "recorder"
                    )
                    source_id = verification.source_id or f"{source_type}_{session_id}"
                    self._catalog_write(
                        lambda: self.catalog.register_source(
                            playback_session_id=session_id,
                            source_id=source_id,
                            source_type=source_type,
                        ),
                        critical=True,
                    )
                    if events:
                        grouped: dict[int, list[dict[str, Any]]] = {}
                        hashes: dict[int, str] = {}
                        for event in events:
                            chunk_sequence = int(event.pop("_journal_chunk_sequence", 0))
                            hashes[chunk_sequence] = str(event.pop("_journal_chunk_hash", ""))
                            grouped.setdefault(chunk_sequence, []).append(event)
                        relative_path = str(journal_path.relative_to(self.root))
                        for chunk_sequence in sorted(grouped):
                            recovered_events = tuple(grouped[chunk_sequence])
                            self._catalog_write(
                                lambda: self.catalog.record_events(
                                    playback_session_id=session_id,
                                    source_id=source_id,
                                    source_type=source_type,
                                    journal_relative_path=relative_path,
                                    journal_chunk_sequence=chunk_sequence,
                                    journal_chunk_hash=hashes.get(chunk_sequence, ""),
                                    events=recovered_events,
                                ),
                                estimated_bytes=(
                                    DIAGNOSTICS_CATALOG_MUTATION_RESERVATION_BYTES
                                    + len(recovered_events) * 16_384
                                ),
                                critical=True,
                            )
            target_state = "corrupt" if corrupt else "interrupted_recoverable"
            self._catalog_write(
                lambda: self.catalog.set_session_state(session_id, target_state),
                critical=True,
            )
            catalog_session = self.catalog.get_session(session_id)
            metadata = dict((catalog_session or {}).get("metadata") or {})
            if metadata:
                if metadata.get("schema_version") == "playback-diagnostics-session-v1":
                    metadata["schema_version"] = "playback-diagnostics-session-v2"
                metadata["state"] = target_state
                metadata["updated_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                try:
                    validated = SessionMetadataV2.model_validate(metadata).model_dump(mode="json")
                    ensure_private_directory(session_path)
                    ensure_private_directory(raw_path)
                    metadata_path = resolve_beneath(session_path, "session.json")
                    old_size = metadata_path.stat().st_size if metadata_path.is_file() else 0
                    encoded = encode_json_document(validated)
                    reservation = self.capacity.reserve(len(encoded), critical=True)
                    try:
                        atomic_write_bytes(metadata_path, encoded)
                        reservation.commit(0)
                        self.capacity.account_replacement(
                            old_size=old_size,
                            new_size=len(encoded),
                        )
                    except Exception:
                        new_size = metadata_path.stat().st_size if metadata_path.is_file() else 0
                        reservation.commit(0)
                        self.capacity.account_replacement(
                            old_size=old_size,
                            new_size=new_size,
                        )
                        raise
                except Exception:
                    self._catalog_write(
                        lambda: self.catalog.set_session_state(session_id, "corrupt"),
                        critical=True,
                    )
            if corrupt:
                self._remember(self._sealed_sessions, session_id, None)
        self.capacity.reconcile_usage()


def unlink_diagnostic_identity(settings, user_id: int) -> bool:
    """Best-effort identity unlink for account deletion without raw-data deletion."""

    if not settings.playback_diagnostics_enabled:
        return False
    try:
        from .runtime import get_active_diagnostics_service

        root = Path(settings.playback_diagnostics_root)
        active_service = get_active_diagnostics_service()
        if active_service is not None and Path(active_service.root) == root:
            return bool(active_service.unlink_user_identity(user_id))
        if root.is_symlink() or not root.is_dir():
            return False
        with DiagnosticsRootLease(root, mode="identity-unlink"):
            key_store = DiagnosticsKeyStore(resolve_beneath(root, "keys"), read_only=True)
            active_key = key_store.load_or_create_active_key()
            identity_root = resolve_beneath(root, "identities")
            identity_key = load_identity_key(identity_root)
            store = DiagnosticIdentityStore(
                identity_root,
                key_store,
                active_key,
                identity_key,
                capacity=DiagnosticsCapacityGuard(
                    root,
                    hard_cap_bytes=settings.playback_diagnostics_max_bytes,
                    emergency_reserve_bytes=DIAGNOSTICS_EMERGENCY_RESERVE_BYTES,
                    minimum_free_bytes=settings.playback_diagnostics_min_free_bytes,
                ),
            )
            return store.unlink_user(user_id)
    except Exception:  # noqa: BLE001 - account deletion must not fail on diagnostics cleanup.
        return False
