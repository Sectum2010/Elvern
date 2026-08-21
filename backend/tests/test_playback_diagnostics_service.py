from __future__ import annotations

import asyncio
import ast
import hashlib
import json
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.routes import playback_diagnostics as playback_diagnostics_routes
from backend.app.services.playback_diagnostics import sealing as diagnostics_sealing_module
from backend.app.services.playback_diagnostics.capacity import DiagnosticsCapacityError
from backend.app.services.playback_diagnostics.crypto import encrypt_blob
from backend.app.services.playback_diagnostics.errors import (
    DiagnosticsClosingError,
    DiagnosticsConflictError,
    DiagnosticsCorruptError,
    DiagnosticsInvalidEventError,
    DiagnosticsNotFoundError,
    DiagnosticsRateLimitError,
    DiagnosticsRequestTooLargeError,
    DiagnosticsSealedError,
    DiagnosticsWorkerUnavailableError,
)
from backend.app.services.playback_diagnostics.http_observer import (
    PlaybackDiagnosticsBodyLimitMiddleware,
    PlaybackDiagnosticsHttpMiddleware,
    classify_browser_playback_route,
)
from backend.app.services.playback_diagnostics.runtime import (
    observe_runtime_event,
    set_active_diagnostics_service,
)
from backend.app.services.playback_diagnostics.operator_store import (
    PlaybackDiagnosticsReadOnlyStore,
)
from backend.app.services.playback_diagnostics.schema import (
    PlaybackDiagnosticEvent,
    PlaybackDiagnosticsBootstrapRequest,
    PlaybackDiagnosticsGapRequest,
)
from backend.app.services.playback_diagnostics.session_files import read_session_events
from backend.app.services.playback_diagnostics.service import (
    PlaybackDiagnosticsOwnershipError,
    PlaybackDiagnosticsService,
)


SESSION_ID = "session-synthetic-00000001"


class DiagnosticPlaybackManager:
    def __init__(self) -> None:
        self.context = {
            "playback_session_id": SESSION_ID,
            "media_item_id": 101,
            "source_original_filename": "Synthetic_Test_Clip_001.mkv",
            "source_fingerprint": "a" * 64,
            "source_kind": "local",
            "source_size_bytes": 1_000_000,
            "duration_ms": 60_000,
            "container": "matroska",
            "video_codec": "h264",
            "audio_codec": "aac",
            "width": 1_920,
            "height": 1_080,
            "profile": "balanced",
            "playback_mode": "lite",
            "stream_mode": "route2",
            "hls_engine": "hls.js",
            "device_class": "desktop",
            "created_at_utc": "2026-08-20T00:00:00+00:00",
            "state": "active",
        }

    def get_diagnostic_session_context(self, session_id: str, *, user_id: int):
        if session_id != SESSION_ID or user_id != 7:
            raise KeyError("not found")
        return dict(self.context)

    def list_active_diagnostic_session_ids(self):
        return ()

    def list_active_diagnostic_processes(self):
        return ()


def _event(sequence: int) -> PlaybackDiagnosticEvent:
    return PlaybackDiagnosticEvent(
        event_id=f"event-synthetic-{sequence:08d}",
        event_name="media_aggregate",
        event_source="client",
        playback_session_id=SESSION_ID,
        event_sequence=sequence,
        source_sequence=sequence,
        client_wall_time_ms=1_000 + sequence,
        client_monotonic_time_us=2_000 + sequence,
        aligned_wall_time_ns=str(1_000_000_000 + sequence),
        observation_kind="measured_client",
        payload={"buffered_ahead_ms": 4_000, "unknown_private_state": "dropped"},
    )


def _service(test_settings, tmp_path: Path) -> PlaybackDiagnosticsService:
    settings = replace(
        test_settings,
        playback_diagnostics_enabled=True,
        playback_diagnostics_root=tmp_path / "playback_diagnostics",
        playback_diagnostics_min_free_bytes=1,
    )
    service = PlaybackDiagnosticsService(settings)
    service.bind_playback_manager(DiagnosticPlaybackManager())
    service.start()
    return service


def _bootstrap(service: PlaybackDiagnosticsService):
    return service.bootstrap(
        PlaybackDiagnosticsBootstrapRequest(
            playback_session_id=SESSION_ID,
            client_instance_id="client-synthetic-00000001",
            platform="linux",
            device_class="desktop",
            browser_family="firefox",
            browser_version="152.0",
            os_family="linux",
            hls_engine="hls.js",
            capabilities={"request_video_frame_callback": True},
        ),
        user_id=7,
        user_agent="SyntheticBrowser/1.0",
    )


def test_diagnostics_root_rejects_both_directions_of_protected_path_overlap(
    test_settings,
    tmp_path,
):
    media_root = tmp_path / "protected-media"
    inside = PlaybackDiagnosticsService(
        replace(
            test_settings,
            playback_diagnostics_enabled=True,
            media_root=media_root,
            playback_diagnostics_root=media_root / "diagnostics",
        )
    )
    with pytest.raises(ValueError, match="inside protected"):
        inside._validate_diagnostics_root()

    diagnostics_root = tmp_path / "diagnostics-parent"
    contains = PlaybackDiagnosticsService(
        replace(
            test_settings,
            playback_diagnostics_enabled=True,
            media_root=diagnostics_root / "media",
            playback_diagnostics_root=diagnostics_root,
        )
    )
    with pytest.raises(ValueError, match="contains protected"):
        contains._validate_diagnostics_root()


def test_diagnostics_root_rejects_application_source_and_fixed_backup_roots(
    test_settings,
):
    project_root = Path(__file__).resolve().parents[2]
    protected_roots = (
        project_root / "backend" / "app" / "diagnostics",
        project_root / "frontend" / "src" / "diagnostics",
        project_root / "backend" / "data" / "backups",
    )

    for diagnostics_root in protected_roots:
        service = PlaybackDiagnosticsService(
            replace(
                test_settings,
                playback_diagnostics_enabled=True,
                playback_diagnostics_root=diagnostics_root,
            )
        )
        with pytest.raises(ValueError, match="protected application data"):
            service._validate_diagnostics_root()


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code", "expected_retryable"),
    (
        (DiagnosticsNotFoundError(), 404, "diagnostics_not_found", False),
        (DiagnosticsConflictError(), 409, "diagnostics_conflict", False),
        (DiagnosticsClosingError(), 409, "diagnostics_closing", True),
        (DiagnosticsCorruptError(), 409, "diagnostics_corrupt", False),
        (DiagnosticsSealedError(), 410, "diagnostics_sealed", False),
        (
            DiagnosticsRequestTooLargeError(),
            413,
            "diagnostics_request_too_large",
            False,
        ),
        (
            DiagnosticsInvalidEventError(
                event_index=2,
                event_id="event-synthetic-00000003",
                source_sequence=3,
                reason="event_schema_invalid",
            ),
            422,
            "diagnostics_invalid_event",
            False,
        ),
        (DiagnosticsRateLimitError(), 429, "diagnostics_budget_exceeded", True),
        (
            DiagnosticsWorkerUnavailableError(),
            503,
            "diagnostics_worker_unavailable",
            True,
        ),
        (DiagnosticsCapacityError("synthetic capacity"), 507, "diagnostics_capacity_reached", False),
    ),
)
def test_diagnostics_http_error_contract_is_typed_and_public_safe(
    error,
    expected_status,
    expected_code,
    expected_retryable,
):
    with pytest.raises(HTTPException) as raised:
        playback_diagnostics_routes._raise_diagnostics_error(error)

    assert raised.value.status_code == expected_status
    assert raised.value.detail["code"] == expected_code
    assert raised.value.detail["retryable"] is expected_retryable
    assert "synthetic capacity" not in json.dumps(raised.value.detail)


def _drain(service: PlaybackDiagnosticsService) -> None:
    service._observation_queue.join()
    assert service.writer.flush(timeout=5)


def _seal_test_session(
    service: PlaybackDiagnosticsService,
    *,
    include_host_observation: bool = False,
):
    bootstrap = _bootstrap(service)
    service.ingest(
        diagnostics_session_id=SESSION_ID,
        source_id=bootstrap.source_id,
        events=[_event(1)],
        user_id=7,
    )
    _drain(service)
    if include_host_observation:
        service._record_host_observation(
            "host_resource_sample",
            sample_id="host_synthetic00000001",
            payload={"cpu_percent": 12.5, "memory_rss_bytes": 4096},
            session_links=((SESSION_ID, None, None),),
            observed_wall_time_ns=1_000_000_100,
            observed_monotonic_time_ns=2_000_000_100,
        )
    watermark, finalized, state = service.close(
        playback_session_id=SESSION_ID,
        source_id=bootstrap.source_id,
        user_id=7,
        reason="synthetic_test_complete",
        final_source_sequence=1,
    )
    assert (watermark, finalized, state) == (1, True, "sealed")
    session = service.catalog.get_session(SESSION_ID)
    assert session is not None
    return bootstrap, service.root / str(session["session_relative_path"])


def test_disabled_service_start_creates_no_diagnostics_artifacts(test_settings, tmp_path):
    root = tmp_path / "disabled-diagnostics"
    service = PlaybackDiagnosticsService(
        replace(
            test_settings,
            playback_diagnostics_enabled=False,
            playback_diagnostics_root=root,
        )
    )

    service.start()
    try:
        assert root.exists() is False
        assert service.catalog is None
        assert service.key_store is None
        assert service.writer is None
        assert service.host_sampler is None
        assert service._observation_thread is None
    finally:
        service.shutdown()


def test_async_start_returns_before_diagnostics_initialization_finishes(
    test_settings,
    tmp_path,
    monkeypatch,
):
    service = PlaybackDiagnosticsService(
        replace(
            test_settings,
            playback_diagnostics_enabled=True,
            playback_diagnostics_root=tmp_path / "diagnostics",
        )
    )
    entered = threading.Event()
    release = threading.Event()

    def blocked_initialize() -> None:
        entered.set()
        release.wait(timeout=5)

    monkeypatch.setattr(service, "_initialize_runtime", blocked_initialize)
    started = time.perf_counter()
    service.start_async()
    elapsed = time.perf_counter() - started
    try:
        assert entered.wait(timeout=1)
        assert elapsed < 0.25
        assert service.health.state == "initializing"
    finally:
        release.set()
        service._startup_thread.join(timeout=1)
        service.shutdown()


def test_playback_modules_use_only_the_nonblocking_diagnostics_ingress_boundary():
    project_root = Path(__file__).resolve().parents[2]
    direct_ingress_path = project_root / "backend" / "app" / "routes" / "browser_playback.py"
    observer_paths = [
        project_root / "backend" / "app" / "services" / "playback_diagnostics" / name
        for name in (
            "eta_observer.py",
            "ffmpeg_observer.py",
            "http_observer.py",
            "manager_observer.py",
            "provider_observer.py",
        )
    ]
    playback_path = project_root / "backend" / "app" / "services" / "mobile_playback_service.py"

    direct_tree = ast.parse(direct_ingress_path.read_text(encoding="utf-8"))
    imported_names = {
        alias.name
        for node in ast.walk(direct_tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "try_capture_diagnostic_observation" in imported_names
    assert "PlaybackDiagnosticsService" not in imported_names

    forbidden_calls = {
        "bootstrap",
        "ingest",
        "observe_event",
        "observe_playback_session_created",
        "finalize_session",
    }
    for path in [direct_ingress_path, playback_path, *observer_paths]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called_attributes = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert called_attributes.isdisjoint(forbidden_calls), path


def test_shutdown_retains_root_lease_while_a_mutation_worker_is_alive(
    test_settings,
    tmp_path,
):
    release_worker = threading.Event()
    worker = threading.Thread(
        target=lambda: release_worker.wait(timeout=5),
        name="synthetic-blocked-diagnostics-writer",
        daemon=False,
    )
    worker.start()

    class _BlockingWriter:
        @staticmethod
        def flush(*, timeout):
            del timeout
            return False

        @staticmethod
        def shutdown(*, timeout):
            del timeout
            worker.join(timeout=0.01)
            return not worker.is_alive()

    class _RecordingLease:
        def __init__(self):
            self.release_count = 0

        def release(self):
            self.release_count += 1

    service = PlaybackDiagnosticsService(
        replace(
            test_settings,
            playback_diagnostics_enabled=True,
            playback_diagnostics_root=tmp_path / "diagnostics",
        )
    )
    lease = _RecordingLease()
    service.writer = _BlockingWriter()
    service._root_lease = lease
    try:
        service.shutdown()
        assert worker.is_alive()
        assert service._shutdown_failed is True
        assert lease.release_count == 0
        assert service._root_lease is lease
    finally:
        release_worker.set()
        worker.join(timeout=1)
        service.shutdown()
    assert lease.release_count == 1
    assert service._root_lease is None


def test_provisional_observation_is_flushed_after_session_registration(
    test_settings,
    tmp_path,
):
    service = _service(test_settings, tmp_path)
    try:
        service.observe_event(
            "provider_request_started",
            playback_session_id=SESSION_ID,
            event_source="provider",
            payload={"range_count": 1},
        )
        service._observation_queue.join()
        assert SESSION_ID in service._provisional_observations

        service.observe_playback_session_created(
            DiagnosticPlaybackManager().context,
            user_id=7,
            client_user_agent="SyntheticBrowser/1.0",
        )
        _drain(service)

        session = service.catalog.get_session(SESSION_ID)
        events, _reports = read_session_events(
            service.root,
            str(session["session_relative_path"]),
            service.key_store,
        )
        assert any(event["event_name"] == "provider_request_started" for event in events)
        assert SESSION_ID not in service._provisional_observations
    finally:
        service.shutdown()


def test_service_bootstrap_ingest_idempotency_ownership_and_direct_open_files(
    test_settings,
    tmp_path,
):
    service = _service(test_settings, tmp_path)
    try:
        bootstrap = _bootstrap(service)
        assert bootstrap.enabled is True
        assert bootstrap.diagnostics_session_id == SESSION_ID
        assert bootstrap.server_wall_time_ns.isdigit()

        with pytest.raises(PlaybackDiagnosticsOwnershipError):
            service.ingest(
                diagnostics_session_id=SESSION_ID,
                source_id=bootstrap.source_id,
                events=[_event(1)],
                user_id=8,
            )

        first = service.ingest(
            diagnostics_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            events=[_event(2)],
            user_id=7,
        )
        duplicate = service.ingest(
            diagnostics_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            events=[_event(2)],
            user_id=7,
        )
        assert first.accepted == 1
        assert first.out_of_order == 1
        assert duplicate.accepted == 0
        assert duplicate.duplicate == 1

        service.ingest(
            diagnostics_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            events=[_event(1)],
            user_id=7,
        )
        _drain(service)
        assert service.catalog.ack_watermark(bootstrap.source_id) == 2
        session = service.catalog.get_session(SESSION_ID)
        session_root = service.root / str(session["session_relative_path"])
        raw_events, _reports = read_session_events(
            service.root,
            str(session["session_relative_path"]),
            service.key_store,
        )
        client_sequence_twos = [
            event
            for event in raw_events
            if event.get("event_source") == "client" and event.get("source_sequence") == 2
        ]
        assert len(client_sequence_twos) == 1
        watermark, finalized, close_state = service.close(
            playback_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            user_id=7,
            reason="synthetic_test_complete",
            final_source_sequence=2,
        )
        assert watermark == 2
        assert finalized is True
        assert close_state == "sealed"

        session = service.catalog.get_session(SESSION_ID)
        for name in (
            "session.json",
            "summary.md",
            "summary.json",
            "timeline.csv",
            "completeness.json",
            "manifest.json",
        ):
            path = session_root / name
            assert path.is_file()
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(session_root.stat().st_mode) == 0o700
        summary = json.loads((session_root / "summary.json").read_text(encoding="utf-8"))
        assert summary["identity"]["source_original_filename"] == "Synthetic_Test_Clip_001.mkv"
        manifest = json.loads((session_root / "manifest.json").read_text(encoding="utf-8"))
        manifest_files = {
            row["relative_path"]: row
            for row in manifest["files"]
        }
        final_session_bytes = (session_root / "session.json").read_bytes()
        assert manifest_files["session.json"]["sha256"] == hashlib.sha256(
            final_session_bytes
        ).hexdigest()
        assert manifest_files["session.json"]["size_bytes"] == len(final_session_bytes)
        operator = PlaybackDiagnosticsReadOnlyStore(service.root)
        assert operator.verify_session(SESSION_ID)["valid"] is True
        exported = service.export_session(SESSION_ID, format_name="ndjson")
        assert exported.is_file()
        assert stat.S_IMODE(exported.stat().st_mode) == 0o600
        assert "unknown_private_state" not in exported.read_text(encoding="utf-8")
        (session_root / "summary.md").write_text("tampered\n", encoding="utf-8")
        verification = operator.verify_session(SESSION_ID)
        assert verification["valid"] is False
        assert any("summary.md" in error for error in verification["errors"])
    finally:
        service.shutdown()


def test_startup_recovery_marks_session_interrupted_recoverable_without_sealing(
    test_settings,
    tmp_path,
):
    first = _service(test_settings, tmp_path)
    bootstrap = _bootstrap(first)
    first.ingest(
        diagnostics_session_id=SESSION_ID,
        source_id=bootstrap.source_id,
        events=[_event(1)],
        user_id=7,
    )
    _drain(first)
    first.shutdown()

    second = _service(test_settings, tmp_path)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            session = second.catalog.get_session(SESSION_ID)
            if session and session["state"] == "interrupted_recoverable":
                break
            time.sleep(0.02)
        assert session["state"] == "interrupted_recoverable"
        session_root = second.root / str(session["session_relative_path"])
        metadata = json.loads((session_root / "session.json").read_text(encoding="utf-8"))
        assert metadata["state"] == "interrupted_recoverable"
        assert not (session_root / "summary.md").exists()
        assert not (session_root / "manifest.json").exists()
    finally:
        second.shutdown()


def test_operator_rejects_decryptable_host_payload_outside_safe_schema(
    test_settings,
    tmp_path,
):
    service = _service(test_settings, tmp_path)
    try:
        _bootstrap_result, _session_root = _seal_test_session(
            service,
            include_host_observation=True,
        )
        sample_id = "host_synthetic00000001"
        invalid_payload = json.dumps(
            {"cpu_percent": 12.5, "unexpected_host_field": "not-canonical"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        encrypted = encrypt_blob(
            service._active_key,
            invalid_payload,
            context=f"playback-diagnostics-host:{sample_id}".encode("utf-8"),
        )
        with service.catalog._lock, service.catalog._connect() as connection:
            connection.execute(
                """
                UPDATE diagnostic_host_observations
                SET encrypted_payload = ? WHERE sample_id = ?
                """,
                (encrypted, sample_id),
            )
            connection.commit()

        verification = PlaybackDiagnosticsReadOnlyStore(service.root).verify_session(
            SESSION_ID
        )

        assert verification["valid"] is False
        assert f"host_payload_invalid:{sample_id}" in verification["errors"]
        assert verification["host_evidence"]["valid"] is False
    finally:
        service.shutdown()


def test_operator_recomputes_seal_evidence_digest(test_settings, tmp_path):
    service = _service(test_settings, tmp_path)
    try:
        _bootstrap_result, session_root = _seal_test_session(service)
        seal_path = session_root / "seal.json"
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
        seal["close_reason"] = "tampered_after_seal"
        seal_path.write_text(json.dumps(seal, sort_keys=True), encoding="utf-8")

        verification = PlaybackDiagnosticsReadOnlyStore(service.root).verify_session(
            SESSION_ID
        )

        assert verification["valid"] is False
        assert "seal_evidence_digest_mismatch" in verification["errors"]
    finally:
        service.shutdown()


def test_operator_compares_sealed_source_evidence_field_by_field(
    test_settings,
    tmp_path,
):
    service = _service(test_settings, tmp_path)
    try:
        bootstrap, _session_root = _seal_test_session(service)
        with service.catalog._lock, service.catalog._connect() as connection:
            connection.execute(
                """
                UPDATE diagnostic_sources
                SET duplicate_count = duplicate_count + 1
                WHERE source_id = ?
                """,
                (bootstrap.source_id,),
            )
            connection.commit()

        verification = PlaybackDiagnosticsReadOnlyStore(service.root).verify_session(
            SESSION_ID
        )

        assert verification["valid"] is False
        assert "seal_source_evidence_mismatch" in verification["errors"]
    finally:
        service.shutdown()


def test_close_waits_for_missing_sequence_then_concurrent_retry_seals_once(
    test_settings,
    tmp_path,
):
    service = _service(test_settings, tmp_path)
    try:
        bootstrap = _bootstrap(service)
        service.observe_event(
            "synthetic_pre_close_observation",
            playback_session_id=SESSION_ID,
            payload={"state": "active"},
        )
        first = service.ingest(
            diagnostics_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            events=[_event(2)],
            user_id=7,
        )
        assert first.accepted == 1
        assert first.ack_watermark == 0

        watermark, finalized, state = service.close(
            playback_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            user_id=7,
            reason="synthetic_gap",
            final_source_sequence=2,
        )
        assert (watermark, finalized, state) == (0, False, "closing")
        assert service.catalog.get_session(SESSION_ID)["state"] == "closing"

        replay = service.ingest(
            diagnostics_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            events=[_event(1)],
            user_id=7,
        )
        assert replay.accepted == 1
        assert replay.ack_watermark == 2

        barrier = threading.Barrier(3)

        def close_again():
            barrier.wait()
            return service.close(
                playback_session_id=SESSION_ID,
                source_id=bootstrap.source_id,
                user_id=7,
                reason="synthetic_retry",
                final_source_sequence=2,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(close_again) for _ in range(2)]
            barrier.wait()
            results = [future.result(timeout=10) for future in futures]

        assert results == [(2, True, "sealed"), (2, True, "sealed")]
        assert service.catalog.get_session(SESSION_ID)["state"] == "sealed"
        sealed_session = service.catalog.get_session(SESSION_ID)
        events, _reports = read_session_events(
            service.root,
            str(sealed_session["session_relative_path"]),
            service.key_store,
        )
        names = [event["event_name"] for event in events]
        assert names.count("synthetic_pre_close_observation") == 1
        assert names.count("session_close") == 1
        assert names.count("session_finalized") == 1

        with pytest.raises(DiagnosticsSealedError):
            service.ingest(
                diagnostics_session_id=SESSION_ID,
                source_id=bootstrap.source_id,
                events=[_event(3)],
                user_id=7,
            )
        assert service.close(
            playback_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            user_id=7,
            reason="duplicate_close",
            final_source_sequence=2,
        ) == (2, True, "sealed")
    finally:
        service.shutdown()


def test_authenticated_client_gap_advances_watermark_and_is_idempotent(
    test_settings,
    tmp_path,
):
    service = _service(test_settings, tmp_path)
    try:
        bootstrap = _bootstrap(service)
        receipt = service.ingest(
            diagnostics_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            events=[_event(2)],
            user_id=7,
        )
        assert receipt.ack_watermark == 0
        assert service.close(
            playback_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            user_id=7,
            reason="client_invalid_event",
            final_source_sequence=2,
        ) == (0, False, "closing")

        gap = PlaybackDiagnosticsGapRequest(
            diagnostics_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            start_sequence=1,
            end_sequence=1,
            reason_code="client_invalid_event",
            rejected_event_name="media_aggregate",
            rejected_event_hash="a" * 64,
        )
        assert service.declare_client_gap(gap, user_id=7) == 2
        assert service.declare_client_gap(gap, user_id=7) == 2
        gaps = service.catalog.source_gaps(bootstrap.source_id)
        assert len(gaps) == 1
        assert gaps[0] | {"declared_at_utc": "bounded"} == {
            "source_id": bootstrap.source_id,
            "start_sequence": 1,
            "end_sequence": 1,
            "reason_code": "client_invalid_event",
            "declaration_origin": "authenticated_client",
            "declared_at_utc": "bounded",
            "rejected_event_name": "media_aggregate",
            "rejected_event_hash": "a" * 64,
        }

        with pytest.raises(DiagnosticsConflictError):
            service.declare_client_gap(
                gap.model_copy(update={"reason_code": "client_capacity_drop"}),
                user_id=7,
            )

        assert service.close(
            playback_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            user_id=7,
            reason="client_invalid_event_recovered",
            final_source_sequence=2,
        ) == (2, True, "sealed")
        with pytest.raises(DiagnosticsSealedError):
            service.declare_client_gap(gap, user_id=7)
    finally:
        service.shutdown()


@pytest.mark.parametrize("failure_name", ["session.json", "manifest.json"])
@pytest.mark.parametrize("failure_timing", ["before_write", "after_write"])
def test_final_artifact_failure_never_seals_and_close_retry_finishes_valid_manifest(
    test_settings,
    tmp_path,
    monkeypatch,
    failure_name,
    failure_timing,
):
    service = _service(test_settings, tmp_path)
    try:
        bootstrap = _bootstrap(service)
        receipt = service.ingest(
            diagnostics_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            events=[_event(1)],
            user_id=7,
        )
        assert receipt.ack_watermark == 1
        session = service.catalog.get_session(SESSION_ID)
        session_root = service.root / str(session["session_relative_path"])
        real_atomic_write = diagnostics_sealing_module.atomic_write_bytes
        failed = False

        def fail_once(path, payload, **kwargs):
            nonlocal failed
            if not failed and Path(path).name == failure_name:
                failed = True
                if failure_timing == "after_write":
                    real_atomic_write(path, payload, **kwargs)
                raise OSError(f"synthetic {failure_name} failure")
            return real_atomic_write(path, payload, **kwargs)

        monkeypatch.setattr(diagnostics_sealing_module, "atomic_write_bytes", fail_once)
        first_close = service.close(
            playback_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            user_id=7,
            reason="synthetic_final_artifact_failure",
            final_source_sequence=1,
        )
        assert first_close == (1, False, "closing")
        assert service.catalog.get_session(SESSION_ID)["state"] == "closing"
        assert (session_root / "manifest.json").exists() is (
            failure_name == "manifest.json" and failure_timing == "after_write"
        )

        monkeypatch.setattr(
            diagnostics_sealing_module,
            "atomic_write_bytes",
            real_atomic_write,
        )
        retry_close = service.close(
            playback_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            user_id=7,
            reason="synthetic_final_artifact_retry",
            final_source_sequence=1,
        )
        assert retry_close == (1, True, "sealed")
        assert service.catalog.get_session(SESSION_ID)["state"] == "sealed"
        assert PlaybackDiagnosticsReadOnlyStore(service.root).verify_session(SESSION_ID)[
            "valid"
        ] is True
    finally:
        service.shutdown()


@pytest.mark.parametrize("failure_ordinal", [1, 2])
def test_seal_directory_fsync_failure_retries_to_one_valid_manifest(
    test_settings,
    tmp_path,
    monkeypatch,
    failure_ordinal,
):
    service = _service(test_settings, tmp_path)
    try:
        bootstrap = _bootstrap(service)
        assert service.ingest(
            diagnostics_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            events=[_event(1)],
            user_id=7,
        ).ack_watermark == 1
        real_fsync_directory = diagnostics_sealing_module.fsync_directory
        calls = 0

        def fail_selected_directory_fsync(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == failure_ordinal + 1:
                raise OSError(f"synthetic seal directory fsync {failure_ordinal}")
            return real_fsync_directory(*args, **kwargs)

        monkeypatch.setattr(
            diagnostics_sealing_module,
            "fsync_directory",
            fail_selected_directory_fsync,
        )
        assert service.close(
            playback_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            user_id=7,
            reason="synthetic_seal_fsync_failure",
            final_source_sequence=1,
        ) == (1, False, "closing")
        assert service.catalog.get_session(SESSION_ID)["state"] == "closing"

        monkeypatch.setattr(
            diagnostics_sealing_module,
            "fsync_directory",
            real_fsync_directory,
        )
        assert service.close(
            playback_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            user_id=7,
            reason="synthetic_seal_fsync_retry",
            final_source_sequence=1,
        ) == (1, True, "sealed")
        verification = PlaybackDiagnosticsReadOnlyStore(service.root).verify_session(
            SESSION_ID
        )
        assert verification["valid"] is True
    finally:
        service.shutdown()


def test_catalog_sealed_commit_response_loss_is_idempotent_on_close_retry(
    test_settings,
    tmp_path,
    monkeypatch,
):
    service = _service(test_settings, tmp_path)
    try:
        bootstrap = _bootstrap(service)
        assert service.ingest(
            diagnostics_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            events=[_event(1)],
            user_id=7,
        ).ack_watermark == 1
        real_mark_finalized = service.catalog.mark_finalized
        failed = False

        def lose_response_after_commit(*args, **kwargs):
            nonlocal failed
            result = real_mark_finalized(*args, **kwargs)
            if not failed:
                failed = True
                raise OSError("synthetic sealed catalog response loss")
            return result

        monkeypatch.setattr(service.catalog, "mark_finalized", lose_response_after_commit)
        assert service.close(
            playback_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            user_id=7,
            reason="synthetic_catalog_response_loss",
            final_source_sequence=1,
        ) == (1, False, "closing")
        assert service.catalog.get_session(SESSION_ID)["state"] == "sealed"
        assert service.close(
            playback_session_id=SESSION_ID,
            source_id=bootstrap.source_id,
            user_id=7,
            reason="synthetic_catalog_response_retry",
            final_source_sequence=1,
        ) == (1, True, "sealed")
        assert PlaybackDiagnosticsReadOnlyStore(service.root).verify_session(SESSION_ID)[
            "valid"
        ] is True
    finally:
        service.shutdown()


def test_ingest_rejects_a_single_event_above_the_event_contract_limit(test_settings, tmp_path):
    service = _service(test_settings, tmp_path)
    try:
        bootstrap = _bootstrap(service)
        oversized = _event(1).model_copy(
            update={"payload": {"samples": ["x" * 4_096 for _ in range(20)]}}
        )

        with pytest.raises(DiagnosticsInvalidEventError) as raised:
            service.ingest(
                diagnostics_session_id=SESSION_ID,
                source_id=bootstrap.source_id,
                events=[oversized],
                user_id=7,
            )
        assert raised.value.response_detail() == {
            "code": "diagnostics_invalid_event",
            "message": "A playback diagnostics event is invalid.",
            "retryable": False,
            "event_index": 0,
            "event_id": "event-synthetic-00000001",
            "source_sequence": 1,
            "reason": "event_payload_too_large",
            "permanent": True,
            "batch_split_allowed": True,
        }
    finally:
        service.shutdown()


def test_internal_oversized_observation_is_bounded_before_worker_persistence(
    test_settings,
    tmp_path,
):
    service = _service(test_settings, tmp_path)
    try:
        _bootstrap(service)
        service.observe_event(
            "synthetic_internal_aggregate",
            playback_session_id=SESSION_ID,
            payload={"samples": ["x" * 4_096 for _ in range(20)]},
        )
        service._observation_queue.join()
        assert service.writer.flush_session(SESSION_ID, timeout=5) is True

        session = service.catalog.get_session(SESSION_ID)
        events, _reports = read_session_events(
            service.root,
            str(session["session_relative_path"]),
            service.key_store,
        )
        persisted = next(
            event for event in events if event["event_name"] == "synthetic_internal_aggregate"
        )
        samples = persisted["payload"]["samples"]
        assert len(samples) == 20
        assert len(samples) <= 24
        assert all(len(sample.encode("utf-8")) <= 512 for sample in samples)
    finally:
        service.shutdown()


def test_reconcile_is_not_limited_to_interactive_list_cap(tmp_path):
    from backend.app.services.playback_diagnostics.catalog import DiagnosticsCatalog

    catalog = DiagnosticsCatalog(tmp_path / "diagnostics")
    sessions = [
        {
            "playback_session_id": f"session-{index:05d}",
            "session_relative_path": f"sessions/{index:05d}",
        }
        for index in range(5_001)
    ]
    catalog.list_sessions_for_reconcile = lambda: sessions
    removed = []
    catalog.remove_missing_session = removed.append
    report = catalog.reconcile()
    assert report == {"catalog_sessions_checked": 5_001, "catalog_sessions_removed": 5_001}
    assert len(removed) == 5_001


def test_body_limit_rejects_before_inner_app_reads_payload():
    called = False

    async def app(_scope, _receive, _send):
        nonlocal called
        called = True

    middleware = PlaybackDiagnosticsBodyLimitMiddleware(app)
    messages = []
    scope = {
        "type": "http",
        "path": "/api/playback-diagnostics/batch",
        "headers": [(b"content-length", b"3000000")],
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(middleware(scope, receive, send))
    assert called is False
    assert messages[0]["status"] == 413
    assert b"too large" in messages[1]["body"]


def test_http_observer_preserves_asgi_response_and_runtime_failure_is_swallowed():
    class FailingService:
        def observe_event(self, *_args, **_kwargs):
            raise RuntimeError("synthetic observer failure")

    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 206, "headers": []})
        await send({"type": "http.response.body", "body": b"media-bytes"})

    scope = {
        "type": "http",
        "path": f"/api/browser-playback/sessions/{SESSION_ID}/status",
        "headers": [],
    }
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    failing = FailingService()
    set_active_diagnostics_service(failing)
    try:
        observe_runtime_event("synthetic_event", playback_session_id=SESSION_ID)
        asyncio.run(PlaybackDiagnosticsHttpMiddleware(app)(scope, receive, send))
    finally:
        set_active_diagnostics_service(None)
    assert sent == [
        {"type": "http.response.start", "status": 206, "headers": []},
        {"type": "http.response.body", "body": b"media-bytes"},
    ]


def test_browser_playback_route_classification_discards_query_secrets():
    route, session_id, epoch_id, segment = classify_browser_playback_route(
        "/api/browser-playback/epochs/epoch-synthetic-01/segments/9.m4s"
    )
    assert route == "/api/browser-playback/:scope/segments/:segment"
    assert session_id is None
    assert epoch_id == "epoch-synthetic-01"
    assert segment == 9
