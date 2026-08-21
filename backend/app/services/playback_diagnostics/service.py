from __future__ import annotations

import json
import logging
import queue
import secrets
import sqlite3
import threading
import time
from collections import OrderedDict
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .budgets import DiagnosticsBudgets
from .capacity import DiagnosticsCapacityError, DiagnosticsCapacityGuard
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
from .executor import BoundedDiagnosticsExecutor
from .fileio import (
    atomic_write_bytes,
    atomic_write_json,
    encode_json_document,
    ensure_private_directory,
    private_file_size,
    resolve_beneath,
)
from .host_sampler import HostDiagnosticsSampler
from .health import DiagnosticsHealth
from .ingress import (
    CapturedDiagnosticObservation,
    NonBlockingDiagnosticsIngressQueue,
    capture_diagnostic_observation_for_target,
)
from .eta_observer import forget_eta_session
from .errors import (
    DiagnosticsCapacityHttpError,
    DiagnosticsClosingError,
    DiagnosticsConflictError,
    DiagnosticsCorruptError,
    DiagnosticsInvalidEventError,
    DiagnosticsNotFoundError,
    DiagnosticsRequestTooLargeError,
    DiagnosticsSealedError,
    DiagnosticsWorkerUnavailableError,
    PlaybackDiagnosticsOwnershipError,
    PlaybackDiagnosticsUnavailableError,
)
from .event_normalization import normalize_deferred_observation
from .identity import (
    DiagnosticIdentityStore,
    DiagnosticsIdentityKey,
    load_identity_key,
    load_or_create_identity_key,
)
from .journal import verify_journal
from .lease import DiagnosticsRootLease
from .manager_observer import forget_manager_session
from .privacy import (
    normalize_user_agent,
    sanitize_event,
    sanitize_payload,
    validate_canonical_payload,
)
from .schema import (
    PlaybackDiagnosticEvent,
    PlaybackDiagnosticsBatchResponse,
    PlaybackDiagnosticsBootstrapRequest,
    PlaybackDiagnosticsBootstrapResponse,
    PlaybackDiagnosticsClockRequest,
    PlaybackDiagnosticsClockResponse,
    PlaybackDiagnosticsGapRequest,
    SessionMetadataV2,
)
from .sealing import build_seal_capsule, write_critical_seal, write_derived_artifacts
from .session_files import (
    create_session_metadata,
    read_session_events,
)
from .writer import DiagnosticsWriteBatch, DiagnosticsWriter, DiagnosticsWriterError


logger = logging.getLogger(__name__)
OBSERVATION_BATCH_MAX_EVENTS = 64
OBSERVATION_BATCH_WAIT_SECONDS = 0.05
CLOSE_REQUEST_WAIT_SECONDS = 5.0
PROVISIONAL_OBSERVATION_TTL_SECONDS = 5.0
PROVISIONAL_OBSERVATION_MAX_SESSIONS = 256
PROVISIONAL_OBSERVATION_MAX_EVENTS_PER_SESSION = 256
HIGH_FREQUENCY_DIAGNOSTIC_EVENTS = frozenset(
    {
        "client_incident_pre_frames",
        "client_incident_pre_samples",
        "ffmpeg_progress_sample",
        "media_aggregate",
        "performance_aggregate",
        "progress",
    }
)
OPTIONAL_DIAGNOSTIC_EVENTS = frozenset(
    {
        "client_capability_unavailable",
        "client_resource_timing",
        "performance_aggregate",
        "resource_sample",
    }
)
AGGREGATE_DIAGNOSTIC_EVENTS = frozenset(
    {
        "client_incident_post_aggregate",
        "host_aggregate",
        "media_aggregate",
        "performance_aggregate",
    }
)
TERMINAL_OR_GAP_DIAGNOSTIC_EVENTS = frozenset(
    {
        "completed",
        "playback_failed",
        "quit",
        "session_close",
        "session_finalized",
        "telemetry_gap",
    }
)


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
        self.health = DiagnosticsHealth()
        self.budgets = DiagnosticsBudgets()
        self.state = "disabled" if not self.enabled else "initializing"
        self.health.set_state(self.state)
        self._startup_thread: threading.Thread | None = None
        self._shutdown_requested = threading.Event()
        self._shutdown_failed = False
        self._background_executor: BoundedDiagnosticsExecutor | None = None
        self._lock = threading.RLock()
        self._failure_counts: dict[str, int] = {}
        self._observation_queue: NonBlockingDiagnosticsIngressQueue = NonBlockingDiagnosticsIngressQueue(
            8_192
        )
        self._observation_stop = threading.Event()
        self._observation_thread: threading.Thread | None = None
        self._session_created_enqueued: OrderedDict[str, None] = OrderedDict()
        self._finalization_locks: OrderedDict[str, threading.Lock] = OrderedDict()
        self._closing_sessions: OrderedDict[str, None] = OrderedDict()
        self._closing_barriers: OrderedDict[str, int] = OrderedDict()
        self._sealed_sessions: OrderedDict[str, None] = OrderedDict()
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
        self._aggregate_degradation_counter = 0
        self._host_pressure: dict[str, float] = {}

    def bind_playback_manager(self, manager: Any) -> None:
        self._playback_manager = manager

    def start(self) -> None:
        if not self.enabled:
            self._set_state("disabled")
            return
        self._set_state("initializing")
        self._initialize_runtime()

    def start_async(self) -> None:
        """Initialize the diagnostics store without delaying Elvern readiness."""

        if not self.enabled:
            self._set_state("disabled")
            return
        thread = self._startup_thread
        if thread is not None and thread.is_alive():
            return
        self._shutdown_requested.clear()
        self._set_state("initializing")
        self._startup_thread = threading.Thread(
            target=self._initialize_runtime,
            name="elvern-playback-diagnostics-startup",
            daemon=False,
        )
        self._startup_thread.start()

    def _initialize_runtime(self) -> None:
        try:
            self._validate_diagnostics_root()
            ensure_private_directory(self.root)
            self._root_lease = DiagnosticsRootLease(
                self.root,
                mode="writer",
                metadata={"elvern_commit": "runtime"},
            ).acquire()
            for relative in ROOT_DIRECTORIES:
                ensure_private_directory(
                    resolve_beneath(self.root, relative),
                    trusted_root=self.root,
                )
            self.capacity = DiagnosticsCapacityGuard(
                self.root,
                hard_cap_bytes=self.settings.playback_diagnostics_max_bytes,
                emergency_reserve_bytes=DIAGNOSTICS_EMERGENCY_RESERVE_BYTES,
                minimum_free_bytes=self.settings.playback_diagnostics_min_free_bytes,
            )
            self.key_store = DiagnosticsKeyStore(
                resolve_beneath(self.root, "keys"),
                capacity=self.capacity,
                trusted_root=self.root,
            )
            active_key = self.key_store.load_or_create_active_key()
            identity_key = load_or_create_identity_key(
                resolve_beneath(self.root, "identities"),
                capacity=self.capacity,
                trusted_root=self.root,
            )
            self._active_key = active_key
            self._identity_key = identity_key
            self.identity_store = DiagnosticIdentityStore(
                resolve_beneath(self.root, "identities"),
                self.key_store,
                active_key,
                identity_key,
                capacity=self.capacity,
                trusted_root=self.root,
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
            self._background_executor = BoundedDiagnosticsExecutor(
                "elvern-playback-diagnostics-work",
                max_workers=2,
                max_queue=256,
            )
            self._background_executor.start()
            self._recover_open_sessions()
            if self._shutdown_requested.is_set():
                return
            self.writer.start()
            self._start_observation_dispatcher()
            self.host_sampler = HostDiagnosticsSampler(
                active_session_ids=self._active_session_ids,
                active_session_clients=self._active_session_clients,
                active_processes=self._active_processes,
                observe=self.observe_event,
                record_host_observation=self._record_host_observation,
                should_defer_optional=self._host_sampling_under_pressure,
                health_callback=lambda reason: self.health.record(
                    "host_sampler",
                    reason,
                    error=False,
                ),
                pressure_callback=self._on_host_pressure,
                identity_key=identity_key.material,
                diagnostics_root=self.root,
                transcode_root=Path(self.settings.transcode_dir),
            )
            self.host_sampler.start()
            self._set_state("ready")
            self.capacity.write_current_status(enabled=True, startup_state="ready")
        except Exception as exc:  # noqa: BLE001 - playback must start without diagnostics.
            self._set_state("degraded")
            self.health.record("startup", "initialization_failed")
            self._shutdown_partial_runtime()
            logger.warning(
                "Playback diagnostics unavailable at startup: %s",
                exc.__class__.__name__,
            )

    def _shutdown_partial_runtime(self) -> None:
        host_stopped = True
        if self.host_sampler is not None:
            host_stopped = self.host_sampler.shutdown(timeout=5)
        observer_stopped = self._stop_observation_dispatcher()
        executor_stopped = True
        if self._background_executor is not None:
            executor_stopped = self._background_executor.shutdown(timeout=5)
        writer_stopped = True
        if self.writer is not None:
            writer_stopped = self.writer.shutdown(timeout=5)
        catalog_stopped = True
        if self.catalog is not None and host_stopped and observer_stopped and executor_stopped and writer_stopped:
            try:
                self.catalog.close()
            except Exception:  # noqa: BLE001
                catalog_stopped = False
        if (
            self._root_lease is not None
            and host_stopped
            and observer_stopped
            and executor_stopped
            and writer_stopped
            and catalog_stopped
        ):
            self._root_lease.release()
            self._root_lease = None

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
                trusted_root=self.root,
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
        self._shutdown_requested.set()
        if self.enabled:
            self._set_state("shutting_down")
        startup_thread = self._startup_thread
        if (
            startup_thread is not None
            and startup_thread is not threading.current_thread()
            and startup_thread.is_alive()
        ):
            startup_thread.join(timeout=15)
        host_stopped = True
        if self.host_sampler is not None:
            host_stopped = self.host_sampler.shutdown(timeout=5)
        observer_stopped = self._stop_observation_dispatcher()
        executor_stopped = True
        if self._background_executor is not None:
            executor_stopped = self._background_executor.shutdown(timeout=10)
        writer = self.writer
        writer_stopped = True
        if writer is not None:
            writer.flush(timeout=5)
            writer_stopped = writer.shutdown(timeout=5)
        startup_stopped = startup_thread is None or not startup_thread.is_alive()
        all_mutation_workers_stopped = bool(
            host_stopped
            and observer_stopped
            and executor_stopped
            and writer_stopped
            and startup_stopped
        )
        self._shutdown_failed = not all_mutation_workers_stopped
        if self.capacity is not None and not self._maintenance_mode:
            try:
                self.capacity.write_current_status(
                    enabled=self.enabled,
                    startup_state=(
                        "stopped" if all_mutation_workers_stopped else "shutdown_failed"
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - shutdown must retain the lease.
                self.health.record("shutdown", "status_write_failed")
                self._record_failure("shutdown_status_write", exc)
        if all_mutation_workers_stopped and self.catalog is not None:
            try:
                self.catalog.close()
                if self.capacity is not None and not self._maintenance_mode:
                    self.capacity.mark_clean_shutdown()
            except Exception as exc:  # noqa: BLE001
                all_mutation_workers_stopped = False
                self._shutdown_failed = True
                self.health.record("shutdown", "clean_ledger_failed")
                self._record_failure("shutdown", exc)
        if self._root_lease is not None and all_mutation_workers_stopped:
            self._root_lease.release()
            self._root_lease = None
        elif self._root_lease is not None:
            self.health.record("shutdown", "mutation_worker_still_alive")
        self._maintenance_mode = False
        self._active_key = None
        self._identity_key = None
        if all_mutation_workers_stopped:
            self._set_state("disabled")

    def observe_playback_session_created(
        self,
        context: dict[str, Any],
        *,
        user_id: int,
        client_user_agent: str | None = None,
        client_ip: str | None = None,
    ) -> None:
        session_id = str(context.get("playback_session_id") or "")
        self.observe_event(
            "diagnostics_session_registration_with_context",
            playback_session_id=session_id,
            event_source="server",
            observation_kind="inferred",
            priority="critical",
            payload={
                "owner_user_id": int(user_id),
                "client_user_agent": client_user_agent or "",
                "client_address": client_ip or "",
                "context": context,
            },
        )

    def _record_playback_session_created(
        self,
        context: dict[str, Any],
        *,
        user_id: int,
        client_user_agent: str | None,
    ) -> None:
        try:
            self.budgets.admit_creation(user_id=user_id)
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
            existing_state = str(existing_session.get("state") or "")
            if existing_state == "sealed":
                raise DiagnosticsSealedError()
            if existing_state == "corrupt":
                raise DiagnosticsCorruptError()
            if existing_state == "closing":
                raise DiagnosticsClosingError()
            existing_source = self.catalog.find_client_source(
                payload.playback_session_id,
                payload.client_instance_id,
            )
        if existing_session is not None and existing_source is not None:
            return self._bootstrap_response(
                payload.playback_session_id,
                str(existing_source["source_id"]),
            )
        self.budgets.admit_creation(user_id=user_id)
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
            state=(
                "interrupted_recoverable"
                if str(
                    (self.catalog.get_session(playback_session_id) or {}).get("state") or ""
                )
                == "interrupted_recoverable"
                else "active"
            ),
        )

    def ingest(
        self,
        *,
        diagnostics_session_id: str,
        source_id: str,
        events: list[PlaybackDiagnosticEvent | dict[str, Any]],
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
                user_id=user_id,
            )
        finally:
            self._exit_ingest(diagnostics_session_id)

    def _ingest_events(
        self,
        *,
        diagnostics_session_id: str,
        source_id: str,
        events: list[PlaybackDiagnosticEvent | dict[str, Any]],
        user_id: int,
    ) -> PlaybackDiagnosticsBatchResponse:
        if len(events) > self.settings.playback_diagnostics_batch_max_events:
            raise DiagnosticsRequestTooLargeError(code="diagnostics_batch_event_limit")

        validated_events: list[PlaybackDiagnosticEvent] = []
        encoded_size = 2
        for event_index, candidate in enumerate(events):
            raw_candidate = (
                candidate.model_dump(mode="json")
                if isinstance(candidate, PlaybackDiagnosticEvent)
                else candidate
            )
            try:
                encoded = json.dumps(
                    raw_candidate,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise self._invalid_event_error(
                    event_index,
                    raw_candidate,
                    reason="event_not_json_serializable",
                ) from exc
            encoded_size += len(encoded) + (1 if event_index else 0)
            if encoded_size > self.settings.playback_diagnostics_batch_max_bytes:
                raise DiagnosticsRequestTooLargeError(code="diagnostics_batch_byte_limit")
            try:
                validated_events.append(PlaybackDiagnosticEvent.model_validate(raw_candidate))
            except ValidationError as exc:
                raise self._invalid_event_error(
                    event_index,
                    raw_candidate,
                    reason="event_schema_invalid",
                ) from exc

        self.budgets.admit_ingest(
            source_id=source_id,
            session_id=diagnostics_session_id,
            user_id=user_id,
            event_count=len(validated_events),
            byte_count=encoded_size,
        )

        received_wall_ns = time.time_ns()
        received_monotonic_ns = time.monotonic_ns()
        sanitized_events: list[dict[str, Any]] = []
        duplicate = 0
        out_of_order = 0
        watermark = self.catalog.ack_watermark(source_id)
        session = self.catalog.get_session(diagnostics_session_id)
        if session is None:
            raise DiagnosticsNotFoundError()
        session_state = str(session.get("state") or "")
        if session_state == "sealed":
            raise DiagnosticsSealedError()
        if session_state == "corrupt":
            raise DiagnosticsCorruptError()
        source_row = self.catalog.get_source(source_id)
        final_source_sequence = (
            int(source_row["final_source_sequence"])
            if source_row is not None and source_row.get("final_source_sequence") is not None
            else None
        )
        for event_index, event in enumerate(validated_events):
            if event.playback_session_id != diagnostics_session_id:
                raise self._invalid_event_error(
                    event_index,
                    event,
                    reason="event_session_mismatch",
                )
            if event.event_source != "client":
                raise self._invalid_event_error(
                    event_index,
                    event,
                    reason="event_source_not_client",
                )
            if final_source_sequence is not None and event.source_sequence > final_source_sequence:
                raise self._invalid_event_error(
                    event_index,
                    event,
                    reason="event_exceeds_final_source_sequence",
                )
            if event.source_sequence > watermark + 1:
                out_of_order += 1
            raw = event.model_dump(mode="json")
            event_size = len(
                json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            if event_size > MAX_EVENT_PAYLOAD_BYTES:
                raise self._invalid_event_error(
                    event_index,
                    event,
                    reason="event_payload_too_large",
                )
            raw["server_received_wall_time_ns"] = str(received_wall_ns)
            raw["server_received_monotonic_time_ns"] = str(received_monotonic_ns)
            if raw.get("aligned_wall_time_ns") is None:
                client_ms = raw.get("client_wall_time_ms")
                offset_ns = raw.get("clock_offset_ns")
                if client_ms is not None and offset_ns is not None:
                    raw["aligned_wall_time_ns"] = str(
                        int(float(client_ms) * 1_000_000) + int(str(offset_ns))
                    )
            sanitized_events.append(sanitize_event(raw))

        accepted = 0
        if sanitized_events:
            try:
                with self.budgets.write_slot():
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
            except DiagnosticsCapacityError as exc:
                raise DiagnosticsCapacityHttpError() from exc
            except DiagnosticsWriterError as exc:
                if "timed out" in str(exc).lower():
                    raise DiagnosticsWorkerUnavailableError() from exc
                raise DiagnosticsConflictError(
                    code="diagnostics_event_identity_conflict",
                ) from exc
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

    @staticmethod
    def _invalid_event_error(
        event_index: int,
        event: PlaybackDiagnosticEvent | dict[str, Any] | Any,
        *,
        reason: str,
    ) -> DiagnosticsInvalidEventError:
        raw = event.model_dump(mode="json") if isinstance(event, PlaybackDiagnosticEvent) else event
        event_id = None
        source_sequence = None
        if isinstance(raw, dict):
            raw_event_id = raw.get("event_id")
            if isinstance(raw_event_id, str) and 0 < len(raw_event_id) <= 128:
                event_id = raw_event_id
            raw_sequence = raw.get("source_sequence")
            if isinstance(raw_sequence, int) and not isinstance(raw_sequence, bool):
                source_sequence = raw_sequence
        return DiagnosticsInvalidEventError(
            event_index=event_index,
            event_id=event_id,
            source_sequence=source_sequence,
            reason=reason,
        )

    def declare_client_gap(
        self,
        payload: PlaybackDiagnosticsGapRequest,
        *,
        user_id: int,
    ) -> int:
        """Durably account for an authenticated client sequence that cannot be uploaded."""

        self._require_ready()
        self._assert_source_owner(
            payload.diagnostics_session_id,
            payload.source_id,
            user_id=user_id,
        )
        session = self.catalog.get_session(payload.diagnostics_session_id)
        source = self.catalog.get_source(payload.source_id)
        if session is None or source is None:
            raise DiagnosticsNotFoundError()
        session_state = str(session.get("state") or "")
        if session_state == "sealed":
            raise DiagnosticsSealedError()
        if session_state == "corrupt":
            raise DiagnosticsCorruptError()
        if str(source.get("source_type") or "") != "client":
            raise DiagnosticsConflictError(code="diagnostics_gap_source_not_client")
        if payload.end_sequence < payload.start_sequence:
            raise DiagnosticsConflictError(code="diagnostics_gap_range_invalid")
        if payload.end_sequence - payload.start_sequence + 1 > 100_000:
            raise DiagnosticsRequestTooLargeError(code="diagnostics_gap_range_limit")
        final_sequence = source.get("final_source_sequence")
        if final_sequence is not None and payload.end_sequence > int(final_sequence):
            raise DiagnosticsConflictError(code="diagnostics_gap_exceeds_final_sequence")
        encoded_size = len(
            json.dumps(payload.model_dump(mode="json"), separators=(",", ":")).encode("utf-8")
        )
        self.budgets.admit_ingest(
            source_id=payload.source_id,
            session_id=payload.diagnostics_session_id,
            user_id=user_id,
            event_count=0,
            byte_count=encoded_size,
        )
        try:
            with self.budgets.write_slot():
                return self._catalog_write(
                    lambda: self.catalog.declare_source_gap(
                        source_id=payload.source_id,
                        start_sequence=payload.start_sequence,
                        end_sequence=payload.end_sequence,
                        reason_code=payload.reason_code,
                        declaration_origin="authenticated_client",
                        rejected_event_name=payload.rejected_event_name,
                        rejected_event_hash=payload.rejected_event_hash,
                    ),
                    critical=True,
                )
        except DiagnosticsCapacityError as exc:
            raise DiagnosticsCapacityHttpError() from exc
        except KeyError as exc:
            raise DiagnosticsNotFoundError() from exc
        except ValueError as exc:
            raise DiagnosticsConflictError(code="diagnostics_gap_conflict") from exc

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
            raise DiagnosticsNotFoundError()
        session_state = str(session.get("state") or "")
        if session_state == "sealed":
            return self.catalog.ack_watermark(source_id), True, "sealed"
        if session_state == "corrupt":
            raise DiagnosticsCorruptError()
        watermark = self.catalog.ack_watermark(source_id)
        source = self.catalog.get_source(source_id)
        if source is None:
            raise KeyError("Diagnostics source is missing")
        final_sequence = (
            max(watermark, int(source.get("max_seen_sequence") or 0))
            if final_source_sequence is None
            else int(final_source_sequence)
        )
        try:
            self._catalog_write(
                lambda: self.catalog.set_final_source_sequence(source_id, final_sequence),
                critical=True,
            )
        except DiagnosticsCapacityError as exc:
            raise DiagnosticsCapacityHttpError() from exc
        except ValueError as exc:
            raise DiagnosticsConflictError(
                code="diagnostics_final_sequence_conflict",
            ) from exc
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
        capture_diagnostic_observation_for_target(
            self,
            event_name,
            playback_session_id=playback_session_id,
            event_source=event_source,
            observation_kind=observation_kind,
            priority=priority,
            severity=severity,
            payload=payload or {},
            **identities,
        )

    def try_capture_observation(
        self,
        observation: CapturedDiagnosticObservation,
    ) -> bool:
        """Offer one bounded immutable capture without storage work or waiting."""

        if self.state not in {"initializing", "ready"}:
            return False
        capture_mode = self.health.capture_mode
        critical = observation.priority == "critical" or observation.event_name in {
            "diagnostics_session_registration_requested",
            "diagnostics_session_registration_with_context",
            "diagnostics_session_finalize_requested",
            "session_close",
            "session_finalized",
            "telemetry_gap",
            "recorder_failure",
        }
        if capture_mode == "circuit_open" and observation.event_name not in TERMINAL_OR_GAP_DIAGNOSTIC_EVENTS:
            self.health.record("ingress", "circuit_drop", error=False)
            return False
        if capture_mode == "critical_only" and not critical:
            self.health.record("ingress", "critical_only_drop", error=False)
            return False
        if capture_mode in {
            "reduced_sampling",
            "optional_disabled",
            "reduced_aggregates",
        } and observation.event_name in HIGH_FREQUENCY_DIAGNOSTIC_EVENTS:
            self.health.record("ingress", "reduced_sampling_drop", error=False)
            return False
        if capture_mode in {"optional_disabled", "reduced_aggregates"} and observation.event_name in OPTIONAL_DIAGNOSTIC_EVENTS:
            self.health.record("ingress", "optional_observation_drop", error=False)
            return False
        if capture_mode == "reduced_aggregates" and observation.event_name in AGGREGATE_DIAGNOSTIC_EVENTS:
            self._aggregate_degradation_counter = (self._aggregate_degradation_counter + 1) % 4
            if self._aggregate_degradation_counter:
                self.health.record("ingress", "aggregate_frequency_drop", error=False)
                return False
        try:
            self._observation_queue.put_nowait(observation)
        except queue.Full:
            self.health.record("ingress", "queue_full")
            return False
        return True

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
                invalid_journals = [
                    report for report in journal_reports if not bool(report.get("valid"))
                ]
                if invalid_journals:
                    self._catalog_write(
                        lambda: self.catalog.set_session_state(
                            playback_session_id,
                            "corrupt",
                        ),
                        critical=True,
                    )
                    raise DiagnosticsCorruptError()
                host_link_cutoff = self._catalog_write(
                    lambda: self.catalog.freeze_host_links(playback_session_id),
                    critical=True,
                )
                try:
                    events.extend(self._linked_host_events(playback_session_id, strict=True))
                except DiagnosticsCorruptError:
                    self._catalog_write(
                        lambda: self.catalog.set_session_state(
                            playback_session_id,
                            "corrupt",
                        ),
                        critical=True,
                    )
                    raise
                events.sort(
                    key=lambda event: (
                        int(str(event.get("aligned_wall_time_ns") or "0")),
                        str(event.get("event_source") or ""),
                        int(event.get("source_sequence") or 0),
                    )
                )
                capacity_state = self.capacity.refresh().state
                source_stats = self.catalog.source_stats(playback_session_id)
                derived_status = write_derived_artifacts(
                    session_path=session_path,
                    metadata=metadata,
                    events=events,
                    source_stats=source_stats,
                    writer_metrics=self.writer.metrics(),
                    capacity_state=capacity_state,
                    capacity=self.capacity,
                )
                seal_capsule = build_seal_capsule(
                    playback_session_id=playback_session_id,
                    close_reason=close_reason,
                    source_stats=source_stats,
                    journal_reports=journal_reports,
                    host_link_cutoff=host_link_cutoff,
                    derived_artifact_status=derived_status,
                )
                write_critical_seal(
                    root=self.root,
                    session_relative_path=str(session["session_relative_path"]),
                    metadata=metadata,
                    seal_capsule=seal_capsule,
                    journal_reports=journal_reports,
                    capacity=self.capacity,
                )
                self._catalog_write(
                    lambda: self.catalog.mark_finalized(playback_session_id, state="sealed"),
                    critical=True,
                )
                self.capacity.persist_dirty_checkpoint()
                sealed = True
                with self._lock:
                    self._closing_sessions.pop(playback_session_id, None)
                    self._closing_barriers.pop(playback_session_id, None)
                    self._ingest_pending_by_session.pop(playback_session_id, None)
                    self._sealing_sessions.pop(playback_session_id, None)
                    self._session_client_addresses.pop(playback_session_id, None)
                    self._session_created_enqueued.pop(playback_session_id, None)
                    self._provisional_observations.pop(playback_session_id, None)
                    self._finalization_locks.pop(playback_session_id, None)
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
        self.observe_event(
            "diagnostics_session_finalize_requested",
            playback_session_id=playback_session_id,
            event_source="server",
            observation_kind="inferred",
            priority="critical",
            payload={"reason": "server_finalize_requested"},
        )

    def _mark_session_interrupted(
        self,
        playback_session_id: str,
        *,
        reason: str,
    ) -> None:
        del reason
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
                "state": self.state,
                "root": str(self.root),
                "failure_counts": dict(self._failure_counts),
                "health": self.health.snapshot(),
            }
        snapshot = self.capacity.refresh()
        return {
            "enabled": True,
            "available": True,
            "state": self.state,
            "root": str(self.root),
            "capacity": asdict(snapshot),
            "writer": self.writer.metrics(),
            "observer_queue_depth": self._observation_queue.qsize(),
            "session_count": self.catalog.count_sessions(),
            "failure_counts": dict(self._failure_counts),
            "health": self.health.snapshot(),
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
        with self.budgets.export_slot():
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
            and self.state == "ready"
            and self.catalog is not None
            and self.key_store is not None
            and self.identity_store is not None
            and self.capacity is not None
            and self.writer is not None
        )

    def _set_state(self, state: str) -> None:
        self.state = str(state)
        self.health.set_state(self.state)

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
            self.health.record("host_sampler", "active_session_list_failed")
            return ()

    def _active_processes(self) -> tuple[dict[str, object], ...]:
        manager = self._playback_manager
        if manager is None or not hasattr(manager, "list_active_diagnostic_processes"):
            return ()
        try:
            return tuple(manager.list_active_diagnostic_processes())
        except Exception:  # noqa: BLE001 - host diagnostics are observation only.
            self.health.record("host_sampler", "active_process_list_failed")
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
        return self.catalog.storage_size()

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
        reservation = self.capacity.reserve(estimated_bytes, critical=critical)
        with self.catalog.mutation_guard():
            before = self._catalog_storage_size()
            try:
                result = operation()
            except Exception:
                after = self._catalog_storage_size()
                reservation.commit_replacement(
                    old_size=before,
                    new_size=after,
                    actual_peak_bytes=max(0, after - before),
                )
                raise
            after = self._catalog_storage_size()
            reservation.commit_replacement(
                old_size=before,
                new_size=after,
                actual_peak_bytes=max(0, after - before),
            )
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
            self.budgets.admit_host_observation()
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
            inserted = self._catalog_write(
                lambda: self.catalog.record_host_observation(
                    sample_id=sample_id,
                    event_name=event_name,
                    observed_wall_time_ns=str(observed_wall_time_ns),
                    observed_monotonic_time_ns=str(observed_monotonic_time_ns),
                    encrypted_payload=encrypted,
                    links=links,
                ),
                estimated_bytes=len(encrypted) + 1_048_576,
            )
        except Exception as exc:  # noqa: BLE001 - host diagnostics are observer-only.
            self._record_failure("host_observation", exc)

    def _host_sampling_under_pressure(self) -> bool:
        if self.state != "ready" or self.health.capture_mode != "normal":
            return True
        if self._observation_queue.qsize() >= max(1, self._observation_queue.maxsize // 2):
            return True
        writer = self.writer
        if writer is None:
            return True
        metrics = writer.metrics()
        return int(metrics.get("queue_depth") or 0) >= 1_024

    def _on_host_pressure(self, **values: float) -> None:
        self._host_pressure = {
            str(key): max(0.0, float(value))
            for key, value in values.items()
        }
        self._update_diagnostics_pressure()

    def _update_diagnostics_pressure(self) -> None:
        writer_metrics = self.writer.metrics() if self.writer is not None else {}
        self.health.update_queues(
            ingress_depth=self._observation_queue.qsize(),
            ingress_capacity=self._observation_queue.maxsize,
            writer_depth=int(writer_metrics.get("queue_depth") or 0),
            writer_capacity=int(writer_metrics.get("queue_capacity") or 0),
            writer_latency_ms=float(writer_metrics.get("writer_latency_ms") or 0),
            **self._host_pressure,
        )

    def _linked_host_events(
        self,
        playback_session_id: str,
        *,
        strict: bool = False,
    ) -> list[dict[str, Any]]:
        if self.catalog is None or self.key_store is None:
            return []
        events: list[dict[str, Any]] = []
        seen_links: set[tuple[str, str, str]] = set()
        cutoff = self.catalog.host_link_cutoff(playback_session_id)
        for row in self.catalog.linked_host_observations(playback_session_id):
            sample_id = str(row.get("sample_id") or "")
            try:
                if (
                    not sample_id.startswith("host_")
                    or not sample_id.removeprefix("host_").isalnum()
                    or not str(row.get("event_name") or "").replace("_", "").isalnum()
                    or not str(row.get("observed_wall_time_ns") or "").isdigit()
                    or not str(row.get("observed_monotonic_time_ns") or "").isdigit()
                ):
                    raise ValueError("invalid_host_identity")
                incident_phase = str(row.get("incident_phase") or "")
                if incident_phase not in {"", "pre", "post", "trigger"}:
                    raise ValueError("invalid_host_incident_phase")
                link_identity = (
                    sample_id,
                    str(row.get("incident_id") or ""),
                    incident_phase,
                )
                if link_identity in seen_links:
                    raise ValueError("duplicate_host_link")
                seen_links.add(link_identity)
                if cutoff is not None and str(row.get("linked_at_utc") or "") > str(
                    cutoff.get("cutoff_at_utc") or ""
                ):
                    raise ValueError("host_link_beyond_cutoff")
                decoded = decrypt_blob(
                    self.key_store,
                    bytes(row["encrypted_payload"]),
                    context=f"playback-diagnostics-host:{sample_id}".encode("utf-8"),
                )
                payload = json.loads(decoded.decode("utf-8"))
                payload = validate_canonical_payload(payload)
            except (KeyError, OSError, UnicodeDecodeError, ValueError) as exc:
                if strict:
                    raise DiagnosticsCorruptError(
                        code="diagnostics_host_evidence_corrupt",
                    ) from exc
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
            context = self._with_trusted_technical_metadata(context)
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
                resolve_beneath(self.root, metadata["session_relative_path"]),
                trusted_root=self.root,
            )
            ensure_private_directory(
                resolve_beneath(session_path, "raw"),
                trusted_root=self.root,
            )
            session_json = resolve_beneath(session_path, "session.json")
            old_session_size = private_file_size(
                session_json,
                trusted_root=self.root,
                missing_ok=True,
            )
            reservation = self.capacity.reserve(
                len(encoded) + DIAGNOSTICS_CATALOG_MUTATION_RESERVATION_BYTES,
                critical=True,
            )
            with self.catalog.mutation_guard():
                catalog_before = self._catalog_storage_size()
                try:
                    self.catalog.upsert_session(metadata)
                    atomic_write_bytes(
                        session_json,
                        encoded,
                        trusted_root=self.root,
                    )
                    catalog_after = self._catalog_storage_size()
                    reservation.commit_replacement(
                        old_size=catalog_before + old_session_size,
                        new_size=catalog_after + len(encoded),
                        actual_peak_bytes=(
                            max(0, catalog_after - catalog_before) + len(encoded)
                        ),
                    )
                except Exception:
                    catalog_after = self._catalog_storage_size()
                    session_after = private_file_size(
                        session_json,
                        trusted_root=self.root,
                        missing_ok=True,
                    )
                    if not reservation.closed:
                        reservation.commit_replacement(
                            old_size=catalog_before + old_session_size,
                            new_size=catalog_after + session_after,
                            actual_peak_bytes=(
                                max(0, catalog_after - catalog_before) + session_after
                            ),
                        )
                    raise
            return metadata

    def _with_trusted_technical_metadata(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge already-probed metadata without synchronously probing media."""

        media_item_id = context.get("media_item_id")
        if media_item_id in {None, ""}:
            return context
        try:
            from ..media_technical_metadata_service import get_technical_metadata

            trusted = get_technical_metadata(self.settings, int(media_item_id))
        except (OSError, sqlite3.Error, TypeError, ValueError):
            self.health.record(
                "session_metadata",
                "trusted_metadata_lookup_failed",
                error=False,
            )
            return context
        if not trusted or str(trusted.get("probe_status") or "") != "probed":
            return context
        merged = dict(context)
        mapping = {
            "bit_rate": "nominal_bitrate",
            "video_profile": "video_profile",
            "video_level": "video_level",
            "color_primaries": "color_primaries",
            "color_transfer": "color_transfer",
            "color_space": "color_space",
            "audio_sample_rate": "audio_sample_rate",
            "subtitle_count": "subtitle_count",
        }
        for source_key, target_key in mapping.items():
            if merged.get(target_key) is None and trusted.get(source_key) is not None:
                merged[target_key] = trusted[source_key]
        return merged

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

    def _on_writer_failure(self, reason: str, payload: dict[str, Any]) -> None:
        del payload
        self.health.record("writer", str(reason)[:96])
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
            except Exception:  # noqa: BLE001 - health writes must stay isolated.
                self.health.record("capacity", "status_write_failed")

    def _start_observation_dispatcher(self) -> None:
        if self._observation_thread and self._observation_thread.is_alive():
            return
        self._observation_stop.clear()
        self._observation_thread = threading.Thread(
            target=self._observation_loop,
            daemon=False,
            name="elvern-playback-diagnostics-observer",
        )
        self._observation_thread.start()

    def _stop_observation_dispatcher(self) -> bool:
        self._observation_stop.set()
        try:
            self._observation_queue.put_nowait(None)
        except queue.Full:
            self.health.record("ingress", "shutdown_signal_queue_full")
        thread = self._observation_thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        stopped = thread is None or not thread.is_alive()
        if stopped:
            self._observation_thread = None
        return stopped

    def _observation_loop(self) -> None:
        while not self._observation_stop.is_set() or not self._observation_queue.empty():
            try:
                observation = self._observation_queue.get(timeout=0.25)
            except queue.Empty:
                self._update_diagnostics_pressure()
                self._prune_provisional_observations()
                continue
            observations: list[dict[str, Any]] = []
            consumed = [observation]
            if observation is not None:
                observations.append(self._observation_dict(observation))
            deadline = time.monotonic() + OBSERVATION_BATCH_WAIT_SECONDS
            while len(observations) < OBSERVATION_BATCH_MAX_EVENTS:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    candidate = self._observation_queue.get(timeout=remaining)
                except queue.Empty:
                    break
                consumed.append(candidate)
                if candidate is not None:
                    observations.append(self._observation_dict(candidate))
            try:
                if observations:
                    self._persist_observations(observations)
            except Exception as exc:  # noqa: BLE001 - observer failure cannot affect playback.
                self._record_failure("observe_persist", exc)
            finally:
                for queued in consumed:
                    self._observation_queue.mark_processed(queued)
                self._update_diagnostics_pressure()

    @staticmethod
    def _observation_dict(
        observation: CapturedDiagnosticObservation | dict[str, Any],
    ) -> dict[str, Any]:
        if isinstance(observation, CapturedDiagnosticObservation):
            return {
                "event_name": observation.event_name,
                "playback_session_id": observation.playback_session_id,
                "event_source": observation.event_source,
                "observation_kind": observation.observation_kind,
                "priority": observation.priority,
                "severity": observation.severity,
                "payload": observation.payload_dict(),
                "identities": observation.identities_dict(),
                "captured_wall_time_ns": observation.captured_wall_time_ns,
                "enqueued_monotonic_ns": observation.captured_monotonic_ns,
            }
        return observation

    def _persist_observations(self, observations: list[dict[str, Any]]) -> None:
        grouped: OrderedDict[
            tuple[str, str, str, str],
            list[dict[str, Any]],
        ] = OrderedDict()
        for observation in observations:
            observation = normalize_deferred_observation(observation)
            if self._handle_control_observation(observation):
                continue
            try:
                self.budgets.admit_global_ingress(
                    byte_count=len(
                        json.dumps(
                            observation,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    )
                )
            except Exception as exc:  # noqa: BLE001 - drop observer work, never playback.
                self.health.record("ingress", "global_byte_budget_drop")
                self._record_failure("ingress_budget", exc)
                continue
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
            key = (
                playback_session_id,
                source_id,
                event_source,
                str(session["session_relative_path"]),
            )
            grouped.setdefault(key, []).append(observation)

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
                    allocate_source_sequences=True,
                )
            )

    def _handle_control_observation(self, observation: dict[str, Any]) -> bool:
        event_name = str(observation.get("event_name") or "")
        if event_name not in {
            "diagnostics_session_registration_requested",
            "diagnostics_session_registration_with_context",
            "diagnostics_session_finalize_requested",
        }:
            return False
        session_id = str(observation.get("playback_session_id") or "")
        payload = dict(observation.get("payload") or {})
        if event_name.startswith("diagnostics_session_registration"):
            try:
                user_id = int(payload.get("owner_user_id"))
                supplied_context = payload.get("context")
                context = (
                    dict(supplied_context)
                    if isinstance(supplied_context, dict)
                    else self._resolve_playback_context(session_id, user_id=user_id)
                )
                client_address = str(payload.get("client_address") or "")
                if client_address:
                    with self._lock:
                        self._remember(
                            self._session_client_addresses,
                            session_id,
                            client_address,
                        )
                self._record_playback_session_created(
                    context,
                    user_id=user_id,
                    client_user_agent=str(payload.get("client_user_agent") or "") or None,
                )
            except Exception as exc:  # noqa: BLE001 - worker-owned diagnostics failure.
                self._record_failure("session_registration", exc)
            return True
        try:
            self._mark_session_interrupted(
                session_id,
                reason=str(payload.get("reason") or "server_finalize_requested"),
            )
        except Exception as exc:  # noqa: BLE001 - worker-owned diagnostics failure.
            self._record_failure("session_interrupt", exc)
        return True

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
                self._observation_queue.put_nowait(observation)
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
        self.writer.write_and_wait(
            DiagnosticsWriteBatch(
                playback_session_id=playback_session_id,
                source_id=source_id,
                source_type="server",
                session_relative_path=str(session["session_relative_path"]),
                events=(
                    {
                        "event_name": event_name,
                        "observation_kind": "measured_server",
                        "priority": "critical",
                        "severity": "info",
                        "payload": payload,
                        "identities": {},
                        "captured_wall_time_ns": time.time_ns(),
                        "enqueued_monotonic_ns": time.monotonic_ns(),
                    },
                ),
                enqueued_monotonic_ns=time.monotonic_ns(),
                allocate_source_sequences=True,
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
        old_size = private_file_size(
            session_json,
            trusted_root=self.root,
            missing_ok=True,
        )
        encoded_size = len(json.dumps(active_metadata, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")) + 1
        reservation = self.capacity.reserve(encoded_size, critical=True)
        try:
            atomic_write_json(
                session_json,
                active_metadata,
                trusted_root=self.root,
            )
            reservation.commit_replacement(
                old_size=old_size,
                new_size=private_file_size(session_json, trusted_root=self.root),
            )
            self._catalog_write(
                lambda: self.catalog.set_session_state(session_id, "active"),
                critical=True,
            )
        except Exception:
            if not reservation.closed:
                current_size = private_file_size(
                    session_json,
                    trusted_root=self.root,
                    missing_ok=True,
                )
                reservation.commit_replacement(old_size=old_size, new_size=current_size)
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
            if playback_session_id in self._sealed_sessions:
                raise DiagnosticsSealedError()
            if playback_session_id in self._sealing_sessions:
                raise DiagnosticsClosingError()
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

                submitted = self._start_background(
                    finalize,
                    f"elvern-diagnostics-finalize-{playback_session_id[:12]}",
                )
                if not submitted:
                    completed.set()
        completed, result = task
        completed.wait(timeout=max(0.0, timeout))
        if not completed.is_set():
            return False
        with self._lock:
            if self._finalization_tasks.get(playback_session_id) is task:
                self._finalization_tasks.pop(playback_session_id, None)
        return bool(result.get("finalized"))

    def _drain_observations(self, *, playback_session_id: str, timeout: float) -> bool:
        return self._observation_queue.wait_for_session(
            playback_session_id,
            timeout=timeout,
        )

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
        reason = str(operation)[:96]
        with self._lock:
            self._failure_counts[reason] = self._failure_counts.get(reason, 0) + 1
        self.health.record("service", reason)

    def _start_background(self, target, name: str) -> bool:
        del name
        executor = self._background_executor
        future = executor.submit_nowait(target) if executor is not None else None
        if future is None:
            self.health.record("executor", "background_queue_full")
            return False
        return True

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
                    ensure_private_directory(session_path, trusted_root=self.root)
                    ensure_private_directory(raw_path, trusted_root=self.root)
                    metadata_path = resolve_beneath(session_path, "session.json")
                    old_size = private_file_size(
                        metadata_path,
                        trusted_root=self.root,
                        missing_ok=True,
                    )
                    encoded = encode_json_document(validated)
                    reservation = self.capacity.reserve(len(encoded), critical=True)
                    try:
                        atomic_write_bytes(
                            metadata_path,
                            encoded,
                            trusted_root=self.root,
                        )
                        reservation.commit_replacement(
                            old_size=old_size,
                            new_size=len(encoded),
                        )
                    except Exception:
                        new_size = private_file_size(
                            metadata_path,
                            trusted_root=self.root,
                            missing_ok=True,
                        )
                        if not reservation.closed:
                            reservation.commit_replacement(
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
            key_store = DiagnosticsKeyStore(
                resolve_beneath(root, "keys"),
                read_only=True,
                trusted_root=root,
            )
            active_key = key_store.load_or_create_active_key()
            identity_root = resolve_beneath(root, "identities")
            identity_key = load_identity_key(identity_root, trusted_root=root)
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
                trusted_root=root,
            )
            return store.unlink_user(user_id)
    except Exception:  # noqa: BLE001 - account deletion must not fail on diagnostics cleanup.
        return False
