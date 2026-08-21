from __future__ import annotations

import csv
import io
import json
import hashlib
import os
import random
import stat
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app import cli as app_cli
from backend.app.config import ConfigError, validate_settings
from backend.app.services.playback_diagnostics.capacity import (
    CapacitySnapshot,
    DiagnosticsCapacityGuard,
    DiagnosticsCapacityError,
    directory_size_bytes,
)
from backend.app.services.playback_diagnostics import capacity as capacity_module
from backend.app.services.playback_diagnostics.catalog import DiagnosticsCatalog
from backend.app.services.playback_diagnostics import crypto as crypto_module
from backend.app.services.playback_diagnostics.crypto import DiagnosticsKeyStore
from backend.app.services.playback_diagnostics.exports import (
    OptionalParquetDependencyError,
    export_events,
)
from backend.app.services.playback_diagnostics.fileio import ensure_private_directory
from backend.app.services.playback_diagnostics.identity import (
    DiagnosticIdentityStore,
    load_or_create_identity_key,
)
from backend.app.services.playback_diagnostics import journal as journal_module
from backend.app.services.playback_diagnostics.journal import EncryptedJournal, verify_journal
from backend.app.services.playback_diagnostics.host_sampler import HostDiagnosticsSampler
from backend.app.services.playback_diagnostics.health import DiagnosticsHealth
from backend.app.services.playback_diagnostics.ingress import CapturedDiagnosticObservation
from backend.app.services.playback_diagnostics.lease import (
    DiagnosticsLeaseError,
    DiagnosticsRootLease,
)
from backend.app.services.playback_diagnostics.operator_store import (
    DiagnosticsOperatorError,
    PlaybackDiagnosticsReadOnlyStore,
)
from backend.app.services.playback_diagnostics.service import PlaybackDiagnosticsService
from backend.app.services.playback_diagnostics.summaries import build_summary_artifacts
from backend.app.services.playback_diagnostics.writer import (
    DiagnosticsWriteBatch,
    DiagnosticsWriter,
)


SESSION_ID = "session-synthetic-hardening-0001"
SOURCE_ID = "client-synthetic-hardening-0001"


def _event(sequence: int, *, critical: bool = False) -> dict[str, object]:
    return {
        "event_id": f"event-synthetic-hardening-{sequence:04d}",
        "event_name": "session_close" if critical else "media_aggregate",
        "event_source": "client",
        "priority": "critical" if critical else "normal",
        "playback_session_id": SESSION_ID,
        "event_sequence": sequence,
        "source_sequence": sequence,
        "observation_kind": "measured_client",
        "payload": {"state": "closing" if critical else "active"},
    }


def _captured_observation(
    event_name: str,
    *,
    priority: str = "normal",
) -> CapturedDiagnosticObservation:
    return CapturedDiagnosticObservation(
        event_name=event_name,
        playback_session_id=SESSION_ID,
        event_source="server",
        observation_kind="measured_server",
        priority=priority,
        severity="info",
        payload=(),
        identities=(),
        captured_wall_time_ns=time.time_ns(),
        captured_monotonic_ns=time.monotonic_ns(),
    )


class _RecordingCatalog:
    def __init__(self) -> None:
        self.recorded: list[dict[str, object]] = []
        self.path = Path("/nonexistent/catalog.sqlite3")

    def classify_event(self, *_args) -> str:
        return "new"

    def classify_event_batch(self, _source_id, events):
        return tuple("new" for _event in events)

    def ack_watermark(self, *_args) -> int:
        return 0

    def record_events(self, *, events, **_kwargs):
        self.recorded.extend(events)
        return len(events), 0, 0


class _ReserveCapacity:
    def __init__(self) -> None:
        self.calls: list[bool] = []
        self.status_updates: list[dict[str, object]] = []

    @staticmethod
    def _snapshot(state: str) -> CapacitySnapshot:
        return CapacitySnapshot(
            state=state,
            usage_bytes=79_500_000_000,
            normal_budget_bytes=79_500_000_000,
            emergency_reserve_bytes=500_000_000,
            hard_cap_bytes=80_000_000_000,
            filesystem_free_bytes=10_000_000_000,
            minimum_free_bytes=1,
            checked_at_ns=time.time_ns(),
        )

    class _Reservation:
        def commit(self, _actual_bytes=None) -> None:
            return None

        def release(self) -> None:
            return None

    def reserve(self, _bytes: int, *, critical: bool = False):
        self.calls.append(critical)
        if critical:
            return self._Reservation()
        raise DiagnosticsCapacityError("capacity_reached")

    def write_current_status(self, **payload):
        self.status_updates.append(payload)
        return self._snapshot("capacity_reached")


def _writer(
    tmp_path: Path,
    *,
    capacity,
    failure_callback=None,
    max_queue_batches: int = 2_048,
):
    root = ensure_private_directory(tmp_path / "diagnostics")
    key_store = DiagnosticsKeyStore(root / "keys")
    active_key = key_store.load_or_create_active_key()
    catalog = _RecordingCatalog()
    catalog.path = root / "catalog.sqlite3"
    writer = DiagnosticsWriter(
        root,
        catalog=catalog,
        capacity=capacity,
        key_store=key_store,
        active_key=active_key,
        max_queue_batches=max_queue_batches,
        failure_callback=failure_callback,
    )
    return writer, catalog, key_store


def _durable_writer(tmp_path: Path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    key_store = DiagnosticsKeyStore(root / "keys")
    active_key = key_store.load_or_create_active_key()
    catalog = DiagnosticsCatalog(root)
    catalog.upsert_session(
        {
            "playback_session_id": SESSION_ID,
            "owner_hash": "owner-synthetic",
            "subject_id": "subject-synthetic",
            "media_item_id": 7,
            "source_original_filename": "Synthetic.mkv",
            "source_filename_sha256": "a" * 64,
            "source_fingerprint": "b" * 64,
            "source_kind": "local",
            "platform": "linux",
            "device_class": "desktop",
            "playback_mode": "lite",
            "stream_mode": "route2",
            "hls_engine": "hls.js",
            "state": "active",
            "session_relative_path": "sessions/synthetic",
            "created_at_utc": "2026-08-20T00:00:00+00:00",
        }
    )
    catalog.register_source(
        playback_session_id=SESSION_ID,
        source_id=SOURCE_ID,
        source_type="client",
    )
    guard = DiagnosticsCapacityGuard(
        root,
        hard_cap_bytes=100_000_000,
        emergency_reserve_bytes=10_000_000,
        minimum_free_bytes=1,
        disk_usage_reader=lambda _path: SimpleNamespace(free=1_000_000_000),
    )
    writer = DiagnosticsWriter(
        root,
        catalog=catalog,
        capacity=guard,
        key_store=key_store,
        active_key=active_key,
    )
    batch = DiagnosticsWriteBatch(
        playback_session_id=SESSION_ID,
        source_id=SOURCE_ID,
        source_type="client",
        session_relative_path="sessions/synthetic",
        events=(_event(1),),
        enqueued_monotonic_ns=time.monotonic_ns(),
    )
    return writer, catalog, key_store, batch


def test_config_rejects_any_diagnostics_cap_other_than_exact_80gb(test_settings):
    with pytest.raises(ConfigError, match="exactly 80000000000"):
        validate_settings(
            replace(test_settings, playback_diagnostics_max_bytes=80_000_000_001)
        )


def test_shutdown_status_failure_is_recorded_in_bounded_health(test_settings, tmp_path):
    class _FailingStatusCapacity:
        def write_current_status(self, **_payload):
            raise OSError("synthetic status failure")

    service = PlaybackDiagnosticsService(
        replace(
            test_settings,
            playback_diagnostics_enabled=True,
            playback_diagnostics_root=tmp_path / "diagnostics",
        )
    )
    service.capacity = _FailingStatusCapacity()

    service.shutdown()

    counters = service.health.snapshot()["counters"]
    assert any(
        counter["component"] == "shutdown"
        and counter["reason_code"] == "status_write_failed"
        for counter in counters
    )


def test_diagnostics_health_degrades_in_the_required_order():
    health = DiagnosticsHealth()
    expected = {
        8: "reduced_sampling",
        16: "optional_disabled",
        24: "reduced_aggregates",
        40: "critical_only",
        64: "circuit_open",
    }

    for count in range(1, 65):
        health.record("synthetic", "repeated_failure")
        if count in expected:
            assert health.capture_mode == expected[count]

    health.reset_error_window()
    assert health.capture_mode == "normal"
    health.update_queues(
        ingress_depth=50,
        ingress_capacity=100,
        writer_depth=0,
        writer_capacity=100,
        writer_latency_ms=0,
    )
    assert health.capture_mode == "reduced_sampling"
    health.update_queues(
        ingress_depth=0,
        ingress_capacity=100,
        writer_depth=0,
        writer_capacity=100,
        writer_latency_ms=150,
    )
    assert health.capture_mode == "optional_disabled"
    health.update_queues(
        ingress_depth=0,
        ingress_capacity=100,
        writer_depth=100,
        writer_capacity=100,
        writer_latency_ms=0,
    )
    assert health.capture_mode == "circuit_open"


def test_backend_overhead_circuit_preserves_only_terminal_and_gap_events(
    test_settings,
    tmp_path,
):
    service = PlaybackDiagnosticsService(
        replace(
            test_settings,
            playback_diagnostics_enabled=True,
            playback_diagnostics_root=tmp_path / "diagnostics",
        )
    )
    service.state = "ready"
    service.health.update_queues(
        ingress_depth=service._observation_queue.maxsize,
        ingress_capacity=service._observation_queue.maxsize,
        writer_depth=0,
        writer_capacity=1,
        writer_latency_ms=0,
    )

    assert service.health.capture_mode == "circuit_open"
    assert service.try_capture_observation(_captured_observation("media_aggregate")) is False
    assert service.try_capture_observation(
        _captured_observation("recorder_failure", priority="critical")
    ) is False
    assert service.try_capture_observation(
        _captured_observation("telemetry_gap", priority="critical")
    ) is True
    assert service.try_capture_observation(
        _captured_observation("session_close", priority="critical")
    ) is True


def test_full_ingress_queue_rejects_immediately_without_raising(
    test_settings,
    tmp_path,
):
    service = PlaybackDiagnosticsService(
        replace(
            test_settings,
            playback_diagnostics_enabled=True,
            playback_diagnostics_root=tmp_path / "diagnostics",
        )
    )
    service.state = "ready"
    service._observation_queue = service._observation_queue.__class__(maxsize=1)
    service._observation_queue.put_nowait(_captured_observation("media_aggregate"))

    assert service.try_capture_observation(
        _captured_observation("media_aggregate")
    ) is False
    assert any(
        counter["component"] == "ingress"
        and counter["reason_code"] == "queue_full"
        for counter in service.health.snapshot()["counters"]
    )


def test_full_writer_queue_rejects_immediately_and_reports_bounded_health(tmp_path):
    failures: list[tuple[str, dict[str, object]]] = []
    writer, _catalog, _key_store = _writer(
        tmp_path,
        capacity=_ReserveCapacity(),
        failure_callback=lambda reason, context: failures.append((reason, context)),
        max_queue_batches=1,
    )
    first = DiagnosticsWriteBatch(
        playback_session_id=SESSION_ID,
        source_id=SOURCE_ID,
        source_type="client",
        session_relative_path="sessions/synthetic",
        events=(_event(1),),
        enqueued_monotonic_ns=time.monotonic_ns(),
    )
    second = replace(first, events=(_event(2),))

    assert writer.enqueue(first).queued is True
    assert writer.enqueue(second).queued is False
    assert failures == [
        (
            "writer_queue_full",
            {
                "playback_session_id": SESSION_ID,
                "events_dropped": 1,
            },
        )
    ]
    assert writer.metrics()["events_dropped"] == 1


def test_host_sampler_reports_pressure_without_emitting_a_health_event(tmp_path):
    reported: list[dict[str, float]] = []
    health_reasons: list[str] = []
    sampler = HostDiagnosticsSampler(
        active_session_ids=lambda: (),
        observe=lambda *_args, **_kwargs: None,
        health_callback=health_reasons.append,
        pressure_callback=lambda **values: reported.append(values),
        diagnostics_root=tmp_path / "diagnostics",
        transcode_root=tmp_path / "transcode",
    )

    sampler._report_pressure(
        {
            "cpu": {"cpu_percent": 95.0},
            "memory": {"total": 1_000, "available": 100},
            "psi": {"io": {"some": {"avg10": 92.0}}},
        },
        latency_ms=75.0,
    )

    assert reported == [{
        "host_sampler_latency_ms": 75.0,
        "cpu_pressure_ratio": 0.95,
        "io_pressure_ratio": 0.92,
        "memory_pressure_ratio": 0.9,
    }]
    assert health_reasons == []


def test_mixed_source_batch_is_rejected_atomically_at_normal_capacity_limit(tmp_path):
    capacity = _ReserveCapacity()
    failures = []
    writer, catalog, _key_store = _writer(
        tmp_path,
        capacity=capacity,
        failure_callback=lambda reason, payload: failures.append((reason, payload)),
    )
    batch = DiagnosticsWriteBatch(
        playback_session_id=SESSION_ID,
        source_id=SOURCE_ID,
        source_type="client",
        session_relative_path="sessions/2026/08/20/session-synthetic-hardening-0001",
        events=(_event(1), _event(2, critical=True)),
        enqueued_monotonic_ns=time.monotonic_ns(),
    )

    with pytest.raises(RuntimeError, match="capacity_reached"):
        writer._write_batch(batch)

    assert capacity.calls == [False]
    assert catalog.recorded == []
    assert writer.metrics()["events_dropped"] == 2
    assert failures[0][0] == "capacity_reached"
    assert not list((writer.root / batch.session_relative_path).rglob("*.elvd"))


def test_writer_thread_failure_is_contained_and_releases_pending_keys(tmp_path):
    capacity = _ReserveCapacity()
    capacity.reserve = lambda _bytes, critical=False: capacity._Reservation()
    failures = []
    writer, _catalog, _key_store = _writer(
        tmp_path,
        capacity=capacity,
        failure_callback=lambda reason, payload: failures.append((reason, payload)),
    )

    class _FailingJournal:
        def __init__(self, path: Path) -> None:
            self.path = path
            self.path.write_bytes(b"")

        def append(self, _events):
            raise OSError("synthetic writer failure")

    writer._journal_for = lambda _batch: _FailingJournal(tmp_path / "failing.elvd")
    batch = DiagnosticsWriteBatch(
        playback_session_id=SESSION_ID,
        source_id=SOURCE_ID,
        source_type="client",
        session_relative_path="sessions/synthetic",
        events=(_event(1),),
        enqueued_monotonic_ns=time.monotonic_ns(),
    )
    writer.start()
    try:
        assert writer.enqueue(batch).accepted == 1
        assert writer.flush(timeout=2) is True
        assert writer.metrics()["writer_errors"] == 1
        assert failures[0][0] == "writer_failure"
        assert writer.enqueue(batch).accepted == 1
    finally:
        writer.shutdown(timeout=2)


def test_ciphertext_corruption_is_preserved_without_losing_other_sessions(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    key_store = DiagnosticsKeyStore(root / "keys")
    active_key = key_store.load_or_create_active_key()
    good = EncryptedJournal(
        root / "sessions" / "good.elvd",
        playback_session_id="session-synthetic-good-0001",
        source_type="server",
        key_store=key_store,
        active_key=active_key,
        quarantine_root=root / "quarantine",
    )
    corrupt = EncryptedJournal(
        root / "sessions" / "corrupt.elvd",
        playback_session_id="session-synthetic-corrupt-0001",
        source_type="server",
        key_store=key_store,
        active_key=active_key,
        quarantine_root=root / "quarantine",
    )
    good.append([_event(1)])
    corrupt.append([_event(2)])
    payload = bytearray(corrupt.path.read_bytes())
    payload[-1] ^= 0x01
    corrupt.path.write_bytes(payload)
    corrupted_bytes = corrupt.path.read_bytes()

    recovered, _ = verify_journal(
        corrupt.path,
        key_store,
        recover=True,
        quarantine_root=root / "quarantine",
    )
    good_verification, good_events = verify_journal(good.path, key_store, include_events=True)

    assert recovered.valid is False
    assert recovered.recovered_bytes == 0
    assert recovered.quarantined_path is None
    assert corrupt.path.read_bytes() == corrupted_bytes
    assert good_verification.valid is True
    assert len(good_events) == 1


def test_missing_key_and_middle_ciphertext_corruption_never_trigger_tail_repair(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    key_store = DiagnosticsKeyStore(root / "keys")
    active_key = key_store.load_or_create_active_key()
    missing_key_journal = EncryptedJournal(
        root / "sessions" / "missing-key.elvd",
        playback_session_id="session-synthetic-missing-key",
        source_type="server",
        key_store=key_store,
        active_key=active_key,
        quarantine_root=root / "quarantine",
    )
    missing_key_journal.append([_event(1)])
    missing_key_bytes = missing_key_journal.path.read_bytes()
    key_path = key_store.root / f"key-{active_key.key_id}.bin"
    hidden_key_path = key_path.with_suffix(".unavailable")
    key_path.rename(hidden_key_path)
    verification, _events = verify_journal(
        missing_key_journal.path,
        key_store,
        recover=True,
        quarantine_root=root / "quarantine",
    )
    assert verification.valid is False
    assert verification.recovered_bytes == 0
    assert missing_key_journal.path.read_bytes() == missing_key_bytes
    hidden_key_path.rename(key_path)

    middle_journal = EncryptedJournal(
        root / "sessions" / "middle-corrupt.elvd",
        playback_session_id="session-synthetic-middle-corrupt",
        source_type="server",
        key_store=key_store,
        active_key=active_key,
        quarantine_root=root / "quarantine",
    )
    middle_journal.append([_event(1)])
    middle_journal.append([_event(2)])
    payload = bytearray(middle_journal.path.read_bytes())
    payload[len(payload) // 3] ^= 0x01
    middle_journal.path.write_bytes(payload)
    corrupted_bytes = bytes(payload)
    verification, _events = verify_journal(
        middle_journal.path,
        key_store,
        recover=True,
        quarantine_root=root / "quarantine",
    )
    assert verification.valid is False
    assert verification.recovered_bytes == 0
    assert middle_journal.path.read_bytes() == corrupted_bytes


def test_invalid_tag_and_permission_failure_preserve_original_journal_bytes(
    tmp_path,
    monkeypatch,
):
    root = ensure_private_directory(tmp_path / "diagnostics")
    key_store = DiagnosticsKeyStore(root / "keys")
    active_key = key_store.load_or_create_active_key()
    journal = EncryptedJournal(
        root / "sessions" / "invalid-tag.elvd",
        playback_session_id="session-synthetic-invalid-tag",
        source_type="server",
        key_store=key_store,
        active_key=active_key,
        quarantine_root=root / "quarantine",
    )
    journal.append([_event(1)])

    payload = journal.path.read_bytes()
    offset = len(journal_module.JOURNAL_MAGIC)
    header_length = journal_module.LENGTH_STRUCT.unpack(
        payload[offset : offset + journal_module.JOURNAL_LENGTH_BYTES]
    )[0]
    header_start = offset + journal_module.JOURNAL_LENGTH_BYTES
    header_end = header_start + header_length
    header = json.loads(payload[header_start:header_end].decode("utf-8"))
    ciphertext_length_start = header_end
    ciphertext_start = ciphertext_length_start + journal_module.JOURNAL_LENGTH_BYTES
    ciphertext = bytearray(payload[ciphertext_start:])
    ciphertext[-1] ^= 0x01
    header_without_hash = dict(header)
    header_without_hash.pop("current_chunk_hash")
    header["current_chunk_hash"] = hashlib.sha256(
        bytes.fromhex(str(header_without_hash["previous_chunk_hash"]))
        + journal_module._canonical_json(header_without_hash)
        + bytes(ciphertext)
    ).hexdigest()
    encoded_header = journal_module._canonical_json(header)
    assert len(encoded_header) == header_length
    invalid_tag_bytes = (
        payload[:offset]
        + journal_module.LENGTH_STRUCT.pack(len(encoded_header))
        + encoded_header
        + payload[ciphertext_length_start:ciphertext_start]
        + bytes(ciphertext)
    )
    journal.path.write_bytes(invalid_tag_bytes)

    invalid_tag, _events = verify_journal(
        journal.path,
        key_store,
        recover=True,
        quarantine_root=root / "quarantine",
    )
    assert invalid_tag.valid is False
    assert invalid_tag.error == "InvalidTag"
    assert invalid_tag.recovered_bytes == 0
    assert invalid_tag.quarantined_path is None
    assert journal.path.read_bytes() == invalid_tag_bytes

    real_open_private_descriptor = journal_module.open_private_descriptor

    def deny_journal_read(path, flags, *args, **kwargs):
        if path == journal.path and flags == os.O_RDONLY:
            raise PermissionError("synthetic permission failure")
        return real_open_private_descriptor(path, flags, *args, **kwargs)

    monkeypatch.setattr(journal_module, "open_private_descriptor", deny_journal_read)
    permission_failure, _events = verify_journal(
        journal.path,
        key_store,
        recover=True,
        quarantine_root=root / "quarantine",
    )
    monkeypatch.setattr(
        journal_module,
        "open_private_descriptor",
        real_open_private_descriptor,
    )
    assert permission_failure.valid is False
    assert permission_failure.error == "synthetic permission failure"
    assert permission_failure.recovered_bytes == 0
    assert permission_failure.quarantined_path is None
    assert journal.path.read_bytes() == invalid_tag_bytes


def test_host_incident_ring_is_idempotent_and_records_pre_and_post_windows(tmp_path):
    observed = []
    sampler = HostDiagnosticsSampler(
        active_session_ids=lambda: (),
        observe=lambda *_args, **_kwargs: None,
        record_host_observation=lambda event_name, **payload: observed.append(
            (event_name, payload)
        ),
        diagnostics_root=tmp_path / "diagnostics",
        transcode_root=tmp_path / "transcode",
    )
    sampler._ring.extend(
        [
            sampler._new_sample_record({"sample": 1}),
            sampler._new_sample_record({"sample": 2}),
        ]
    )

    sampler.freeze_incident_ring(SESSION_ID, "incident-synthetic-0001")
    sampler.freeze_incident_ring(SESSION_ID, "incident-synthetic-0001")
    sampler._emit_incident_post_samples(
        sampler._new_sample_record({"sample": 3}),
        time.monotonic(),
    )

    assert [name for name, _payload in observed].count("host_incident_sample") == 3
    assert all(
        payload["session_links"][0][1] == "incident-synthetic-0001"
        for _name, payload in observed
    )
    assert [payload["session_links"][0][2] for _name, payload in observed] == [
        "pre",
        "pre",
        "post",
    ]


def test_parquet_dependency_is_lazy_and_has_local_install_hint(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", None)
    with pytest.raises(OptionalParquetDependencyError, match="requirements-diagnostics-export"):
        export_events(
            tmp_path / "diagnostics",
            session_id=SESSION_ID,
            events=[_event(1)],
            format_name="parquet",
        )


def test_cli_exposes_all_local_playback_diagnostics_subcommands(capsys, monkeypatch):
    parser = app_cli._build_parser()
    for subcommand in (
        "status",
        "list",
        "inspect",
        "verify",
        "export",
        "reconcile",
        "finalize",
    ):
        command = ["playback-diagnostics", subcommand]
        if subcommand in {"inspect", "verify", "finalize"}:
            command.append(SESSION_ID)
        if subcommand == "export":
            command.extend([SESSION_ID, "--format", "ndjson"])
        assert parser.parse_args(command).diagnostics_command == subcommand

    class _FakeReadOnlyStore:
        def __init__(self, _root) -> None:
            pass

        def status(self):
            return {"enabled": True, "available": True, "root": "/local/diagnostics"}

    monkeypatch.setattr(app_cli, "PlaybackDiagnosticsReadOnlyStore", _FakeReadOnlyStore)
    args = parser.parse_args(["playback-diagnostics", "status"])
    settings = SimpleNamespace(playback_diagnostics_root=Path("/local/diagnostics"))
    assert app_cli._run_playback_diagnostics_cli(args, settings) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {"enabled": True, "available": True, "root": "/local/diagnostics"}


def test_read_only_cli_commands_never_construct_a_diagnostics_service(capsys, monkeypatch):
    class _FakeReadOnlyStore:
        def __init__(self, _root) -> None:
            pass

        def status(self):
            return {"available": True}

        def list_sessions(self, **_kwargs):
            return []

        def inspect_session(self, session_id):
            return {"playback_session_id": session_id}

        def verify_session(self, session_id):
            return {"playback_session_id": session_id, "valid": True}

    monkeypatch.setattr(app_cli, "PlaybackDiagnosticsReadOnlyStore", _FakeReadOnlyStore)
    monkeypatch.setattr(
        app_cli,
        "PlaybackDiagnosticsService",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("read-only command constructed the diagnostics service")
        ),
    )
    parser = app_cli._build_parser()
    settings = SimpleNamespace(playback_diagnostics_root=Path("/local/diagnostics"))
    commands = (
        ["playback-diagnostics", "status"],
        ["playback-diagnostics", "list"],
        ["playback-diagnostics", "inspect", SESSION_ID],
        ["playback-diagnostics", "verify", SESSION_ID],
    )

    for command in commands:
        assert app_cli._run_playback_diagnostics_cli(parser.parse_args(command), settings) == 0
        capsys.readouterr()


def test_read_only_store_rejects_active_inspect_verify_and_export(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    catalog = DiagnosticsCatalog(root)
    catalog.upsert_session(
        {
            "playback_session_id": SESSION_ID,
            "owner_hash": "owner-synthetic",
            "subject_id": "subject-synthetic",
            "media_item_id": 7,
            "source_original_filename": "Synthetic.mkv",
            "source_filename_sha256": "a" * 64,
            "source_fingerprint": "b" * 64,
            "source_kind": "local",
            "platform": "linux",
            "device_class": "desktop",
            "playback_mode": "lite",
            "stream_mode": "route2",
            "hls_engine": "hls.js",
            "state": "active",
            "session_relative_path": "sessions/synthetic",
            "created_at_utc": "2026-08-20T00:00:00+00:00",
        }
    )
    store = PlaybackDiagnosticsReadOnlyStore(root)

    for operation in (
        store.inspect_session,
        store.verify_session,
        store.read_events_for_export,
    ):
        with pytest.raises(DiagnosticsOperatorError, match="only sealed sessions"):
            operation(SESSION_ID)


def test_git_docker_and_backup_contract_explicitly_exclude_diagnostics():
    project_root = Path(__file__).resolve().parents[2]
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "backend/data/playback_diagnostics/synthetic.elvd"],
        cwd=project_root,
        check=False,
    )
    assert ignored.returncode == 0
    dockerignore = (project_root / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "backend/data/playback_diagnostics/" in dockerignore
    from backend.app.services.backup_service import BACKUP_EXCLUDED_RUNTIME_PATHS

    assert BACKUP_EXCLUDED_RUNTIME_PATHS == ("backend/data/playback_diagnostics",)


def test_capacity_reservations_are_atomic_across_concurrent_writers(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    guard = DiagnosticsCapacityGuard(
        root,
        hard_cap_bytes=10_000,
        emergency_reserve_bytes=1_000,
        minimum_free_bytes=1,
        disk_usage_reader=lambda _path: SimpleNamespace(free=10_000),
    )
    barrier = threading.Barrier(3)
    reservations = []
    failures = []

    def reserve() -> None:
        barrier.wait()
        try:
            reservations.append(guard.reserve(6_000))
        except DiagnosticsCapacityError as exc:
            failures.append(str(exc))

    threads = [threading.Thread(target=reserve) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert len(reservations) == 1
    assert failures == ["capacity_exhausted"]
    assert guard.reserved_bytes == 6_000
    reservations[0].release()
    assert guard.reserved_bytes == 0


def test_capacity_randomized_concurrent_transactions_reconcile_every_root_byte(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    hard_cap = 100_000
    guard = DiagnosticsCapacityGuard(
        root,
        hard_cap_bytes=hard_cap,
        emergency_reserve_bytes=20_000,
        minimum_free_bytes=1,
        disk_usage_reader=lambda _path: SimpleNamespace(free=10_000_000),
    )
    managed_roots = tuple(
        ensure_private_directory(root / name)
        for name in ("journals", "exports", "quarantine", "recovery_scratch")
    )
    physical_lock = threading.Lock()
    start = threading.Barrier(7)
    failures: list[BaseException] = []
    admitted_states: list[tuple[str, bool]] = []
    tracked_paths: set[Path] = set()

    def record_physical_size() -> None:
        assert directory_size_bytes(root) <= hard_cap

    def exercise(worker_index: int) -> None:
        rng = random.Random(20260821 + worker_index)
        target = managed_roots[worker_index % len(managed_roots)] / f"worker-{worker_index}.bin"
        try:
            start.wait()
            for operation_index in range(48):
                operation = rng.choice(("append", "replace", "temporary"))
                size = rng.randint(96, 1_024)
                critical = operation_index % 13 == 0
                try:
                    reservation = guard.reserve(size, critical=critical)
                except DiagnosticsCapacityError as exc:
                    assert str(exc) in {
                        "capacity_reached",
                        "capacity_exhausted",
                    }
                    continue
                admitted_states.append((reservation.snapshot.state, critical))
                try:
                    with physical_lock:
                        if operation == "append":
                            with target.open("ab") as stream:
                                stream.write(bytes([worker_index + 1]) * size)
                                stream.flush()
                                os.fsync(stream.fileno())
                            tracked_paths.add(target)
                            record_physical_size()
                            reservation.commit_append(size)
                        elif operation == "replace":
                            old_size = target.stat().st_size if target.exists() else 0
                            temporary = target.with_name(f".{target.name}.{operation_index}.tmp")
                            with temporary.open("xb") as stream:
                                stream.write(bytes([worker_index + 1]) * size)
                                stream.flush()
                                os.fsync(stream.fileno())
                            record_physical_size()
                            os.replace(temporary, target)
                            tracked_paths.add(target)
                            reservation.commit_replacement(
                                old_size=old_size,
                                new_size=size,
                                actual_peak_bytes=size,
                            )
                        else:
                            temporary = managed_roots[3] / (
                                f"worker-{worker_index}-{operation_index}.scratch"
                            )
                            with temporary.open("xb") as stream:
                                stream.write(bytes([worker_index + 1]) * size)
                                stream.flush()
                                os.fsync(stream.fileno())
                            record_physical_size()
                            temporary.unlink()
                            reservation.commit_temporary_peak(final_growth_bytes=0)
                        assert guard.usage_bytes == directory_size_bytes(root)
                        assert guard.reserved_bytes >= 0
                except BaseException:
                    if not reservation.closed:
                        reservation.release()
                    raise
        except BaseException as exc:  # pragma: no cover - re-raised in main thread
            failures.append(exc)

    threads = [threading.Thread(target=exercise, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    assert admitted_states
    assert all(state == "normal" or critical for state, critical in admitted_states)
    assert guard.reserved_bytes == 0
    assert guard.usage_bytes == directory_size_bytes(root)

    with physical_lock:
        for path in tracked_paths:
            if path.exists():
                old_size = path.stat().st_size
                path.unlink()
                guard.account_deletion(old_size=old_size)
        assert guard.usage_bytes == directory_size_bytes(root)

        reserve_growth = guard.normal_budget_bytes - guard.usage_bytes + 1
        capsule = root / "critical-seal-capsule.bin"
        reservation = guard.reserve(reserve_growth, critical=True)
        assert reservation.snapshot.state == "reserve"
        capsule.write_bytes(b"c" * reserve_growth)
        record_physical_size()
        reservation.commit_append(reserve_growth)
        assert guard.usage_bytes == directory_size_bytes(root)
        with pytest.raises(DiagnosticsCapacityError, match="capacity_reached"):
            guard.reserve(1)
        capsule.unlink()
        guard.account_deletion(old_size=reserve_growth)

    assert guard.reserved_bytes == 0
    guard.mark_clean_shutdown()
    assert guard.usage_bytes == directory_size_bytes(root)

    restarted = DiagnosticsCapacityGuard(
        root,
        hard_cap_bytes=hard_cap,
        emergency_reserve_bytes=20_000,
        minimum_free_bytes=1,
        disk_usage_reader=lambda _path: SimpleNamespace(free=10_000_000),
    )
    assert restarted.ledger_fast_path is True
    assert restarted.reserved_bytes == 0
    assert restarted.usage_bytes == directory_size_bytes(root)


def test_steady_state_writer_batch_never_recursively_scans_the_root(tmp_path, monkeypatch):
    root = ensure_private_directory(tmp_path / "diagnostics")
    guard = DiagnosticsCapacityGuard(
        root,
        hard_cap_bytes=10_000_000,
        emergency_reserve_bytes=1_000_000,
        minimum_free_bytes=1,
        disk_usage_reader=lambda _path: SimpleNamespace(free=100_000_000),
    )
    writer, catalog, _key_store = _writer(tmp_path, capacity=guard)
    monkeypatch.setattr(
        capacity_module,
        "directory_size_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("steady-state recursive scan")
        ),
    )
    batch = DiagnosticsWriteBatch(
        playback_session_id=SESSION_ID,
        source_id=SOURCE_ID,
        source_type="client",
        session_relative_path="sessions/synthetic",
        events=(_event(1),),
        enqueued_monotonic_ns=time.monotonic_ns(),
    )

    receipt = writer._write_batch(batch)

    assert receipt.accepted == 1
    assert len(catalog.recorded) == 1
    assert guard.reserved_bytes == 0


def test_writer_catalog_reservation_covers_large_incident_history_batch(tmp_path, monkeypatch):
    root = ensure_private_directory(tmp_path / "diagnostics")
    key_store = DiagnosticsKeyStore(root / "keys")
    active_key = key_store.load_or_create_active_key()
    catalog = DiagnosticsCatalog(root)
    catalog.upsert_session(
        {
            "playback_session_id": SESSION_ID,
            "owner_hash": "owner-synthetic",
            "subject_id": "subject-synthetic",
            "media_item_id": 7,
            "source_original_filename": "Synthetic.mkv",
            "source_filename_sha256": "a" * 64,
            "source_fingerprint": "b" * 64,
            "source_kind": "local",
            "platform": "linux",
            "device_class": "desktop",
            "playback_mode": "lite",
            "stream_mode": "route2",
            "hls_engine": "hls.js",
            "state": "active",
            "session_relative_path": "sessions/synthetic",
            "created_at_utc": "2026-08-20T00:00:00+00:00",
        }
    )
    catalog.register_source(
        playback_session_id=SESSION_ID,
        source_id=SOURCE_ID,
        source_type="client",
    )
    guard = DiagnosticsCapacityGuard(
        root,
        hard_cap_bytes=100_000_000,
        emergency_reserve_bytes=10_000_000,
        minimum_free_bytes=1,
        disk_usage_reader=lambda _path: SimpleNamespace(free=1_000_000_000),
    )
    accounting = []
    original_commit = guard._commit_reservation

    def capture_commit(reserved_bytes: int, **transaction) -> None:
        actual_peak = transaction.get("actual_peak_bytes")
        accounting.append(
            (
                reserved_bytes,
                int(transaction["new_size"] if actual_peak is None else actual_peak),
            )
        )
        original_commit(reserved_bytes, **transaction)

    monkeypatch.setattr(guard, "_commit_reservation", capture_commit)
    writer = DiagnosticsWriter(
        root,
        catalog=catalog,
        capacity=guard,
        key_store=key_store,
        active_key=active_key,
    )
    events = tuple(
        {
            **_event(sequence),
            "payload": {
                "state": "incident_history",
                "samples": [
                    {"playhead_ms": sample * 250, "buffered_ahead_ms": 15_000 - sample}
                    for sample in range(64)
                ],
            },
        }
        for sequence in range(1, 61)
    )

    receipt = writer._write_batch(
        DiagnosticsWriteBatch(
            playback_session_id=SESSION_ID,
            source_id=SOURCE_ID,
            source_type="client",
            session_relative_path="sessions/synthetic",
            events=events,
            enqueued_monotonic_ns=time.monotonic_ns(),
        )
    )

    assert receipt.accepted == 60
    assert receipt.ack_watermark == 60
    assert guard.reserved_bytes == 0
    assert accounting
    assert all(actual <= reserved for reserved, actual in accounting)


def test_journal_construction_failure_releases_capacity_without_creating_raw_file(
    tmp_path,
    monkeypatch,
):
    root = ensure_private_directory(tmp_path / "diagnostics")
    guard = DiagnosticsCapacityGuard(
        root,
        hard_cap_bytes=10_000_000,
        emergency_reserve_bytes=1_000_000,
        minimum_free_bytes=1,
        disk_usage_reader=lambda _path: SimpleNamespace(free=100_000_000),
    )
    writer, _catalog, _key_store = _writer(tmp_path, capacity=guard)
    monkeypatch.setattr(
        writer,
        "_journal_for",
        lambda _batch: (_ for _ in ()).throw(OSError("synthetic journal construction failure")),
    )
    batch = DiagnosticsWriteBatch(
        playback_session_id=SESSION_ID,
        source_id=SOURCE_ID,
        source_type="client",
        session_relative_path="sessions/synthetic",
        events=(_event(1),),
        enqueued_monotonic_ns=time.monotonic_ns(),
    )

    with pytest.raises(OSError, match="journal construction"):
        writer._write_batch(batch)

    assert guard.reserved_bytes == 0
    assert not list(root.rglob("*.elvd"))


@pytest.mark.parametrize("failure_ordinal", [1, 2])
def test_journal_file_or_parent_fsync_failure_reindexes_before_retry_without_duplicate(
    tmp_path,
    monkeypatch,
    failure_ordinal,
):
    writer, catalog, key_store, batch = _durable_writer(tmp_path)
    real_fsync = journal_module.os.fsync
    calls = 0

    def fail_selected_fsync(descriptor):
        nonlocal calls
        calls += 1
        if calls == failure_ordinal:
            raise OSError(f"synthetic fsync failure {failure_ordinal}")
        return real_fsync(descriptor)

    monkeypatch.setattr(journal_module.os, "fsync", fail_selected_fsync)
    with pytest.raises(OSError, match=f"synthetic fsync failure {failure_ordinal}"):
        writer._write_batch(batch)
    monkeypatch.setattr(journal_module.os, "fsync", real_fsync)

    journal_path = next((writer.root / batch.session_relative_path / "raw").glob("*.elvd"))
    verification, raw_events = verify_journal(journal_path, key_store, include_events=True)
    assert verification.valid is True
    assert len(raw_events) == 1
    assert catalog.ack_watermark(SOURCE_ID) == 0

    retry = writer._write_batch(batch)
    verification, raw_events = verify_journal(journal_path, key_store, include_events=True)
    assert retry.accepted == 0
    assert retry.duplicate == 1
    assert retry.ack_watermark == 1
    assert verification.valid is True
    assert len(raw_events) == 1


@pytest.mark.parametrize(
    "failure_stage",
    (
        "quarantine_file_fsync",
        "quarantine_rename_before",
        "quarantine_rename_after",
        "source_open",
        "source_fsync",
    ),
)
def test_quarantine_crash_boundaries_never_lose_both_tail_copies(
    tmp_path,
    monkeypatch,
    failure_stage,
):
    root = ensure_private_directory(tmp_path / "diagnostics")
    quarantine_root = ensure_private_directory(root / "quarantine", trusted_root=root)
    key_store = DiagnosticsKeyStore(root / "keys")
    active_key = key_store.load_or_create_active_key()
    journal = EncryptedJournal(
        root / "sessions" / "crash-boundary.elvd",
        playback_session_id="session-synthetic-crash-boundary",
        source_type="server",
        key_store=key_store,
        active_key=active_key,
        quarantine_root=quarantine_root,
        trusted_root=root,
    )
    journal.append([_event(1)])
    valid_bytes = journal.path.read_bytes()
    tail = journal_module.LENGTH_STRUCT.pack(512) + b"incomplete-crash-tail"
    with journal.path.open("ab") as stream:
        stream.write(tail)
        stream.flush()
        os.fsync(stream.fileno())

    real_fsync = journal_module.os.fsync
    real_rename = journal_module.rename_private_file
    real_open = journal_module.open_private_descriptor

    if failure_stage == "quarantine_file_fsync":
        def fail_quarantine_file_fsync(descriptor):
            target = os.readlink(f"/proc/self/fd/{descriptor}")
            if "quarantine" in target and target.endswith(".tmp"):
                raise OSError("synthetic quarantine file fsync failure")
            return real_fsync(descriptor)

        monkeypatch.setattr(journal_module.os, "fsync", fail_quarantine_file_fsync)
    elif failure_stage == "quarantine_rename_before":
        monkeypatch.setattr(
            journal_module,
            "rename_private_file",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("synthetic quarantine rename failure")
            ),
        )
    elif failure_stage == "quarantine_rename_after":
        def fail_after_quarantine_rename(*args, **kwargs):
            real_rename(*args, **kwargs)
            raise OSError("synthetic post-rename directory durability failure")

        monkeypatch.setattr(
            journal_module,
            "rename_private_file",
            fail_after_quarantine_rename,
        )
    elif failure_stage == "source_open":
        def fail_source_open(path, flags, *args, **kwargs):
            if Path(path) == journal.path and int(flags) & os.O_RDWR:
                raise OSError("synthetic source truncate open failure")
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(journal_module, "open_private_descriptor", fail_source_open)
    else:
        source_metadata = journal.path.stat()

        def fail_source_fsync(descriptor):
            metadata = os.fstat(descriptor)
            if (
                metadata.st_dev == source_metadata.st_dev
                and metadata.st_ino == source_metadata.st_ino
            ):
                raise OSError("synthetic source fsync failure")
            return real_fsync(descriptor)

        monkeypatch.setattr(journal_module.os, "fsync", fail_source_fsync)

    with pytest.raises(OSError, match="synthetic"):
        verify_journal(
            journal.path,
            key_store,
            recover=True,
            quarantine_root=quarantine_root,
            trusted_root=root,
        )

    source_bytes = journal.path.read_bytes()
    quarantine_payloads = [
        path.read_bytes()
        for path in quarantine_root.glob("*.corrupt")
    ]
    assert source_bytes.endswith(tail) or tail in quarantine_payloads
    assert not list(quarantine_root.glob("*.tmp"))
    if failure_stage == "source_fsync":
        assert source_bytes == valid_bytes
        assert tail in quarantine_payloads


def test_capacity_commit_validation_is_atomic_and_reservations_are_single_use(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    guard = DiagnosticsCapacityGuard(
        root,
        hard_cap_bytes=10_000,
        emergency_reserve_bytes=1_000,
        minimum_free_bytes=1,
        disk_usage_reader=lambda _path: SimpleNamespace(free=100_000),
    )
    initial_usage = guard.usage_bytes
    underestimated = guard.reserve(100)

    with pytest.raises(DiagnosticsCapacityError, match="underestimated"):
        underestimated.commit_append(101)
    assert guard.usage_bytes == initial_usage
    assert guard.reserved_bytes == 100
    underestimated.release()

    committed = guard.reserve(100)
    committed.commit_append(100)
    assert guard.usage_bytes == initial_usage + 100
    assert guard.reserved_bytes == 0
    with pytest.raises(DiagnosticsCapacityError, match="already_closed"):
        committed.commit_append(100)
    with pytest.raises(DiagnosticsCapacityError, match="already_closed"):
        committed.release()
    assert guard.usage_bytes == initial_usage + 100
    assert guard.reserved_bytes == 0


@pytest.mark.parametrize("failure_timing", ["before_write", "after_write"])
def test_clean_shutdown_ledger_crash_boundary_selects_safe_startup_path(
    tmp_path,
    monkeypatch,
    failure_timing,
):
    root = ensure_private_directory(tmp_path / "diagnostics")
    parameters = {
        "hard_cap_bytes": 100_000,
        "emergency_reserve_bytes": 10_000,
        "minimum_free_bytes": 1,
        "disk_usage_reader": lambda _path: SimpleNamespace(free=1_000_000),
    }
    guard = DiagnosticsCapacityGuard(root, **parameters)
    real_atomic_write_json = capacity_module.atomic_write_json

    def fail_clean_marker(*args, **kwargs):
        if failure_timing == "after_write":
            real_atomic_write_json(*args, **kwargs)
        raise OSError(f"synthetic clean marker {failure_timing}")

    monkeypatch.setattr(capacity_module, "atomic_write_json", fail_clean_marker)
    with pytest.raises(OSError, match="synthetic clean marker"):
        guard.mark_clean_shutdown()
    monkeypatch.setattr(capacity_module, "atomic_write_json", real_atomic_write_json)

    restarted = DiagnosticsCapacityGuard(root, **parameters)
    assert restarted.ledger_fast_path is (failure_timing == "after_write")
    assert restarted.usage_bytes == directory_size_bytes(root)
    assert restarted.reserved_bytes == 0


def test_clean_shutdown_ledger_reconciles_catalog_close_file_shrink(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    parameters = {
        "hard_cap_bytes": 100_000,
        "emergency_reserve_bytes": 10_000,
        "minimum_free_bytes": 1,
        "disk_usage_reader": lambda _path: SimpleNamespace(free=1_000_000),
    }
    guard = DiagnosticsCapacityGuard(root, **parameters)
    simulated_wal = root / "catalog.sqlite3-wal"
    reservation = guard.reserve(4_096)
    simulated_wal.write_bytes(b"w" * 4_096)
    reservation.commit_append(4_096)
    assert guard.usage_bytes == directory_size_bytes(root)

    simulated_wal.unlink()
    assert guard.usage_bytes > directory_size_bytes(root)
    guard.mark_clean_shutdown()

    assert guard.usage_bytes == directory_size_bytes(root)
    restarted = DiagnosticsCapacityGuard(root, **parameters)
    assert restarted.ledger_fast_path is True
    assert restarted.usage_bytes == directory_size_bytes(root)


def test_host_link_cutoff_is_atomic_with_concurrent_observation_and_stays_frozen(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    catalog = DiagnosticsCatalog(root)
    catalog.upsert_session(
        {
            "playback_session_id": SESSION_ID,
            "owner_hash": "owner-synthetic",
            "subject_id": "subject-synthetic",
            "media_item_id": 7,
            "source_original_filename": "Synthetic.mkv",
            "source_filename_sha256": "a" * 64,
            "source_fingerprint": "b" * 64,
            "source_kind": "local",
            "platform": "linux",
            "device_class": "desktop",
            "playback_mode": "lite",
            "stream_mode": "route2",
            "hls_engine": "hls.js",
            "state": "active",
            "session_relative_path": "sessions/synthetic",
            "created_at_utc": "2026-08-20T00:00:00+00:00",
        }
    )
    catalog.record_host_observation(
        sample_id="sample-before-cutoff",
        event_name="host_aggregate",
        observed_wall_time_ns="100",
        observed_monotonic_time_ns="10",
        encrypted_payload=b"ciphertext-before",
        links=((SESSION_ID, None, None),),
    )
    barrier = threading.Barrier(3)
    results: list[dict[str, object]] = []

    def freeze() -> None:
        barrier.wait()
        results.append(catalog.freeze_host_links(SESSION_ID))

    def record_concurrently() -> None:
        barrier.wait()
        catalog.record_host_observation(
            sample_id="sample-at-cutoff",
            event_name="host_aggregate",
            observed_wall_time_ns="200",
            observed_monotonic_time_ns="20",
            encrypted_payload=b"ciphertext-at-cutoff",
            links=((SESSION_ID, "incident-1", "post"),),
        )

    threads = [threading.Thread(target=freeze), threading.Thread(target=record_concurrently)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    cutoff = catalog.host_link_cutoff(SESSION_ID)
    linked = catalog.linked_host_observations(SESSION_ID)
    assert cutoff is not None
    assert results == [cutoff]
    assert int(cutoff["link_count"]) == len(linked)
    assert catalog.freeze_host_links(SESSION_ID) == cutoff

    catalog.record_host_observation(
        sample_id="sample-after-cutoff",
        event_name="host_aggregate",
        observed_wall_time_ns="300",
        observed_monotonic_time_ns="30",
        encrypted_payload=b"ciphertext-after",
        links=((SESSION_ID, "incident-2", "post"),),
    )
    assert catalog.linked_host_observations(SESSION_ID) == linked
    assert catalog.host_link_cutoff(SESSION_ID) == cutoff


def test_concurrent_catalog_mutations_use_one_accounting_coordinator_and_reconcile(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    guard = DiagnosticsCapacityGuard(
        root,
        hard_cap_bytes=1_000_000_000,
        emergency_reserve_bytes=100_000_000,
        minimum_free_bytes=1,
        disk_usage_reader=lambda _path: SimpleNamespace(free=2_000_000_000),
    )
    catalog = DiagnosticsCatalog(root, capacity=guard)
    barrier = threading.Barrier(9)
    failures: list[BaseException] = []

    def mutate(operation) -> None:
        reservation = guard.reserve(8 * 1024 * 1024, critical=True)
        with catalog.mutation_guard():
            before = catalog.storage_size()
            try:
                operation()
            except BaseException:
                after = catalog.storage_size()
                reservation.commit_replacement(
                    old_size=before,
                    new_size=after,
                    actual_peak_bytes=max(0, after - before),
                )
                raise
            after = catalog.storage_size()
            reservation.commit_replacement(
                old_size=before,
                new_size=after,
                actual_peak_bytes=max(0, after - before),
            )

    def exercise(index: int) -> None:
        session_id = f"session-concurrent-{index:04d}"
        source_id = f"source-concurrent-{index:04d}"
        try:
            barrier.wait()
            mutate(lambda: catalog.upsert_session({
                "playback_session_id": session_id,
                "owner_hash": f"owner-{index}",
                "subject_id": f"subject-{index}",
                "media_item_id": index,
                "source_original_filename": f"Synthetic-{index}.mkv",
                "source_filename_sha256": hashlib.sha256(
                    f"Synthetic-{index}.mkv".encode("utf-8")
                ).hexdigest(),
                "source_fingerprint": hashlib.sha256(
                    f"fingerprint-{index}".encode("utf-8")
                ).hexdigest(),
                "source_kind": "local",
                "platform": "linux",
                "device_class": "desktop",
                "playback_mode": "lite",
                "stream_mode": "route2",
                "hls_engine": "hls.js",
                "state": "active",
                "session_relative_path": f"sessions/concurrent/{index}",
                "created_at_utc": "2026-08-21T00:00:00+00:00",
            }))
            mutate(lambda: catalog.register_source(
                playback_session_id=session_id,
                source_id=source_id,
                source_type="client",
                client_instance_id=f"client-{index}",
            ))
            event = {
                "event_id": f"event-concurrent-{index:04d}",
                "event_name": "media_aggregate",
                "event_source": "client",
                "source_sequence": 1,
                "aligned_wall_time_ns": str(1_000 + index),
            }
            mutate(lambda: catalog.record_events(
                playback_session_id=session_id,
                source_id=source_id,
                source_type="client",
                journal_relative_path=f"sessions/concurrent/{index}/raw/client.elvd",
                journal_chunk_sequence=1,
                journal_chunk_hash=hashlib.sha256(session_id.encode("utf-8")).hexdigest(),
                events=(event,),
                preclassified=True,
            ))
            mutate(lambda: catalog.record_host_observation(
                sample_id=f"sample-concurrent-{index:04d}",
                event_name="host_aggregate",
                observed_wall_time_ns=str(2_000 + index),
                observed_monotonic_time_ns=str(3_000 + index),
                encrypted_payload=f"ciphertext-{index}".encode("ascii"),
                links=((session_id, None, None),),
            ))
            mutate(lambda: catalog.set_final_source_sequence(source_id, 1))
            mutate(lambda: catalog.set_session_state(session_id, "closing"))
        except BaseException as exc:  # pragma: no cover - re-raised in main thread
            failures.append(exc)

    threads = [threading.Thread(target=exercise, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    assert guard.reserved_bytes == 0
    assert guard.usage_bytes == directory_size_bytes(root)
    assert all(
        catalog.ack_watermark(f"source-concurrent-{index:04d}") == 1
        for index in range(8)
    )

    catalog.close()
    guard.reconcile_usage()
    assert guard.reserved_bytes == 0
    assert guard.usage_bytes == directory_size_bytes(root)


@pytest.mark.parametrize("failure_stage", ["before_commit", "after_commit"])
def test_catalog_failure_around_commit_rebuilds_before_retry_without_duplicate(
    tmp_path,
    monkeypatch,
    failure_stage,
):
    writer, catalog, key_store, batch = _durable_writer(tmp_path)
    real_record_events = catalog.record_events
    failed = False

    def fail_first_catalog_commit(**kwargs):
        nonlocal failed
        if not failed:
            failed = True
            if failure_stage == "after_commit":
                real_record_events(**kwargs)
            raise OSError("synthetic catalog commit failure")
        return real_record_events(**kwargs)

    monkeypatch.setattr(catalog, "record_events", fail_first_catalog_commit)
    with pytest.raises(OSError, match="synthetic catalog commit failure"):
        writer._write_batch(batch)

    journal_path = next((writer.root / batch.session_relative_path / "raw").glob("*.elvd"))
    verification, raw_events = verify_journal(journal_path, key_store, include_events=True)
    assert verification.valid is True
    assert len(raw_events) == 1
    assert catalog.ack_watermark(SOURCE_ID) == (
        1 if failure_stage == "after_commit" else 0
    )

    retry = writer._write_batch(batch)
    verification, raw_events = verify_journal(journal_path, key_store, include_events=True)
    assert retry.accepted == 0
    assert retry.duplicate == 1
    assert retry.ack_watermark == 1
    assert verification.valid is True
    assert len(raw_events) == 1


def test_lost_response_after_catalog_commit_retries_as_one_durable_raw_event(tmp_path):
    writer, catalog, key_store, batch = _durable_writer(tmp_path)

    committed = writer._write_batch(batch)
    retry_writer = DiagnosticsWriter(
        writer.root,
        catalog=catalog,
        capacity=writer.capacity,
        key_store=key_store,
        active_key=writer.active_key,
    )
    retried = retry_writer._write_batch(batch)

    journal_path = next((writer.root / batch.session_relative_path / "raw").glob("*.elvd"))
    verification, raw_events = verify_journal(journal_path, key_store, include_events=True)
    assert committed.accepted == 1
    assert committed.ack_watermark == 1
    assert retried.accepted == 0
    assert retried.duplicate == 1
    assert retried.ack_watermark == 1
    assert verification.valid is True
    assert len(raw_events) == 1


def test_final_artifact_reservation_uses_exact_atomic_replacement_peak(tmp_path):
    session_path = ensure_private_directory(tmp_path / "diagnostics" / "session")
    (session_path / "session.json").write_bytes(b"s" * 100)
    (session_path / "summary.md").write_bytes(b"m" * 200)
    artifacts = {
        "session.json": b"s" * 150,
        "summary.md": b"m" * 50,
        "summary.json": b"j" * 300,
        "manifest.json": b"x" * 20,
    }

    peak, old_total, final_total = PlaybackDiagnosticsService._artifact_replacement_sizes(
        session_path,
        artifacts,
    )

    assert peak == 220
    assert old_total == 300
    assert final_total == 520


def test_key_and_identity_files_are_included_in_incremental_capacity_ledger(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    guard = DiagnosticsCapacityGuard(
        root,
        hard_cap_bytes=1_000_000,
        emergency_reserve_bytes=100_000,
        minimum_free_bytes=1,
        disk_usage_reader=lambda _path: SimpleNamespace(free=10_000_000),
    )
    key_store = DiagnosticsKeyStore(root / "keys", capacity=guard)
    active_key = key_store.load_or_create_active_key()
    identity_key = load_or_create_identity_key(root / "identities", capacity=guard)
    identities = DiagnosticIdentityStore(
        root / "identities",
        key_store,
        active_key,
        identity_key,
        capacity=guard,
    )
    identities.get_or_create_subject(42)

    assert guard.reserved_bytes == 0
    assert guard.usage_bytes == directory_size_bytes(root)


def test_active_key_creation_recovers_after_metadata_write_interruption(
    tmp_path,
    monkeypatch,
):
    root = ensure_private_directory(tmp_path / "diagnostics")
    store = DiagnosticsKeyStore(root / "keys", trusted_root=root)
    real_atomic_write_json = crypto_module.atomic_write_json
    write_attempts = 0

    def interrupt_metadata_write(*args, **kwargs):
        nonlocal write_attempts
        write_attempts += 1
        if write_attempts == 1:
            raise OSError("synthetic interrupted metadata write")
        return real_atomic_write_json(*args, **kwargs)

    monkeypatch.setattr(crypto_module, "atomic_write_json", interrupt_metadata_write)
    with pytest.raises(OSError, match="synthetic interrupted metadata write"):
        store.load_or_create_active_key()

    assert not (root / "keys" / "active-key.json").exists()
    orphaned_keys = list((root / "keys").glob("key-*.bin"))
    assert len(orphaned_keys) == 1
    assert orphaned_keys[0].stat().st_size == 32

    active = store.load_or_create_active_key()
    assert store.load_or_create_active_key() == active
    assert (root / "keys" / "active-key.json").is_file()
    assert len(list((root / "keys").glob("key-*.bin"))) == 2


def test_identity_key_creation_recovers_material_only_partial_file(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    identities = ensure_private_directory(root / "identities", trusted_root=root)
    partial_path = identities / "identity-hmac-key.bin"
    partial_material = b"p" * 32
    partial_path.write_bytes(partial_material)
    partial_path.chmod(0o600)
    guard = DiagnosticsCapacityGuard(
        root,
        hard_cap_bytes=1_000_000,
        emergency_reserve_bytes=100_000,
        minimum_free_bytes=1,
        disk_usage_reader=lambda _path: SimpleNamespace(free=10_000_000),
    )

    identity_key = load_or_create_identity_key(
        identities,
        capacity=guard,
        trusted_root=root,
    )

    assert identity_key.material != partial_material
    assert (identities / "identity-hmac-key.json").is_file()
    assert stat.S_IMODE(partial_path.stat().st_mode) == 0o600
    assert guard.reserved_bytes == 0
    assert guard.usage_bytes == directory_size_bytes(root)


def test_identity_key_partial_file_fails_closed_when_identity_map_exists(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    identities = ensure_private_directory(root / "identities", trusted_root=root)
    partial_path = identities / "identity-hmac-key.bin"
    partial_path.write_bytes(b"p" * 32)
    partial_path.chmod(0o600)
    identity_map = identities / "identity-map.enc"
    identity_map.write_bytes(b"existing encrypted identity evidence")
    identity_map.chmod(0o600)

    with pytest.raises(ValueError, match="identity key files are incomplete"):
        load_or_create_identity_key(identities, trusted_root=root)

    assert partial_path.read_bytes() == b"p" * 32
    assert identity_map.is_file()


@pytest.mark.parametrize("corruption", ["active_key", "catalog"])
def test_corrupt_startup_state_degrades_without_retaining_root_lease(
    test_settings,
    tmp_path,
    corruption,
):
    root = ensure_private_directory(tmp_path / f"diagnostics-{corruption}")
    if corruption == "active_key":
        keys = ensure_private_directory(root / "keys", trusted_root=root)
        active_key = keys / "active-key.json"
        active_key.write_text("{not-json", encoding="utf-8")
        active_key.chmod(0o600)
    else:
        catalog = root / "catalog.sqlite3"
        catalog.write_bytes(b"not a sqlite catalog")
        catalog.chmod(0o600)
    settings = replace(
        test_settings,
        playback_diagnostics_enabled=True,
        playback_diagnostics_root=root,
        playback_diagnostics_min_free_bytes=1,
    )
    service = PlaybackDiagnosticsService(settings)

    service.start()

    assert service.state == "degraded"
    assert service._root_lease is None
    assert any(
        counter["component"] == "startup"
        and counter["reason_code"] == "initialization_failed"
        and counter["count"] == 1
        for counter in service.health.snapshot()["counters"]
    )
    with DiagnosticsRootLease(root, mode="post-failure-check"):
        pass


def test_writer_failure_status_updates_are_coalesced(tmp_path):
    settings = SimpleNamespace(
        playback_diagnostics_enabled=True,
        playback_diagnostics_root=tmp_path / "diagnostics",
    )
    service = PlaybackDiagnosticsService(settings)

    class _StatusCapacity:
        def __init__(self) -> None:
            self.calls = []

        def write_current_status(self, **payload):
            self.calls.append(payload)

    service.capacity = _StatusCapacity()
    service._on_writer_failure("synthetic_failure", {})
    service._on_writer_failure("synthetic_failure", {})

    assert service._failure_counts == {"synthetic_failure": 2}
    assert len(service.capacity.calls) == 1
    assert service.capacity.calls[0]["failure_counts"] == {"synthetic_failure": 1}


def test_cross_process_root_lease_blocks_key_identity_races_and_releases_after_exit(tmp_path):
    root = tmp_path / "diagnostics"
    project_root = Path(__file__).resolve().parents[2]
    script = """
import sys
from pathlib import Path
from backend.app.services.playback_diagnostics.capacity import DiagnosticsCapacityGuard
from backend.app.services.playback_diagnostics.crypto import DiagnosticsKeyStore
from backend.app.services.playback_diagnostics.identity import DiagnosticIdentityStore, load_or_create_identity_key
from backend.app.services.playback_diagnostics.lease import DiagnosticsRootLease

root = Path(sys.argv[1])
lease = DiagnosticsRootLease(root, mode="test-writer").acquire()
capacity = DiagnosticsCapacityGuard(root, hard_cap_bytes=1000000, emergency_reserve_bytes=100000, minimum_free_bytes=1)
keys = DiagnosticsKeyStore(root / "keys", capacity=capacity)
active = keys.load_or_create_active_key()
identity_key = load_or_create_identity_key(root / "identities", capacity=capacity)
DiagnosticIdentityStore(root / "identities", keys, active, identity_key, capacity=capacity).get_or_create_subject(7)
print("READY", flush=True)
sys.stdin.readline()
lease.release()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(root)],
        cwd=project_root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "READY"
        with pytest.raises(DiagnosticsLeaseError):
            DiagnosticsRootLease(root, mode="second-writer").acquire()
    finally:
        process.stdin.write("release\n")
        process.stdin.flush()
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, f"{stdout}\n{stderr}"

    with DiagnosticsRootLease(root, mode="offline-maintenance"):
        assert DiagnosticsKeyStore(root / "keys", read_only=True).load_or_create_active_key()
    assert len(list((root / "keys").glob("key-*.bin"))) == 1
    assert (root / "identities" / "identity-hmac-key.bin").is_file()


def test_offline_maintenance_rejects_live_writer_lease_then_opens_after_release(
    test_settings,
    tmp_path,
):
    root = ensure_private_directory(tmp_path / "diagnostics")
    capacity = DiagnosticsCapacityGuard(
        root,
        hard_cap_bytes=test_settings.playback_diagnostics_max_bytes,
        emergency_reserve_bytes=500_000_000,
        minimum_free_bytes=1,
    )
    DiagnosticsKeyStore(root / "keys", capacity=capacity).load_or_create_active_key()
    DiagnosticsCatalog(root, capacity=capacity)
    settings = replace(
        test_settings,
        playback_diagnostics_enabled=True,
        playback_diagnostics_root=root,
        playback_diagnostics_min_free_bytes=1,
    )

    with DiagnosticsRootLease(root, mode="live-writer"):
        blocked = PlaybackDiagnosticsService(settings)
        with pytest.raises(DiagnosticsLeaseError):
            blocked.start_maintenance(writer_required=False)

    service = PlaybackDiagnosticsService(settings)
    try:
        service.start_maintenance(writer_required=False)
        assert service._maintenance_mode is True
        assert service.host_sampler is None
        assert service._observation_thread is None
    finally:
        service.shutdown()


def test_read_only_status_and_list_leave_store_files_and_mtimes_unchanged(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    catalog = DiagnosticsCatalog(root)
    catalog.upsert_session(
        {
            "playback_session_id": SESSION_ID,
            "owner_hash": "owner-synthetic",
            "subject_id": "subject-synthetic",
            "media_item_id": 7,
            "source_original_filename": "Synthetic.mkv",
            "source_filename_sha256": "a" * 64,
            "source_fingerprint": "b" * 64,
            "source_kind": "local",
            "platform": "linux",
            "device_class": "desktop",
            "playback_mode": "lite",
            "stream_mode": "route2",
            "hls_engine": "hls.js",
            "state": "active",
            "session_relative_path": "sessions/synthetic",
            "created_at_utc": "2026-08-20T00:00:00+00:00",
        }
    )
    status_path = root / "recorder-status.json"
    status_path.write_text('{"enabled": true}\n', encoding="utf-8")

    def snapshot() -> dict[str, tuple[int, int, str]]:
        return {
            str(path.relative_to(root)): (
                path.stat().st_size,
                path.stat().st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
        }

    before = snapshot()
    store = PlaybackDiagnosticsReadOnlyStore(root)
    assert store.status()["recorder_status"] == {"enabled": True}
    assert store.list_sessions(limit=10)[0]["playback_session_id"] == SESSION_ID
    after = snapshot()

    assert after == before


def test_human_reports_neutralize_markdown_control_and_csv_formula_injection():
    malicious_basename = "Synthetic`Title\n## injected heading.mkv"
    metadata = {
        "playback_session_id": SESSION_ID,
        "source_original_filename": malicious_basename,
        "media_item_id": 7,
        "subject_id": "subject-synthetic",
        "source_kind": "local",
        "playback_mode": "lite",
        "platform": "linux",
        "browser_family": "firefox",
        "hls_engine": "hls.js",
        "elvern_commit": "a" * 40,
        "ffmpeg_version": "synthetic",
        "diagnostics_event_schema": "playback-diagnostics-event-v2",
        "state": "sealed",
        "capabilities": {},
    }
    events = [
        {
            "event_name": "media_aggregate",
            "event_source": "client",
            "observation_kind": "measured_client",
            "source_sequence": 1,
            "aligned_wall_time_ns": "1000000000",
            "payload": {"state": "=1+1"},
        }
    ]
    _summary, _completeness, artifacts = build_summary_artifacts(
        metadata,
        events,
        source_stats=[],
        writer_metrics={},
        capacity_state="normal",
    )
    markdown = artifacts["summary.md"].decode("utf-8")
    assert "\n## injected heading" not in markdown
    assert "\\n## injected heading" in markdown
    assert malicious_basename not in markdown

    rows = list(csv.DictReader(io.StringIO(artifacts["timeline.csv"].decode("utf-8"))))
    assert rows[0]["state"] == "'=1+1"


def test_operator_permission_walk_rejects_symlink_hardlink_and_fifo(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    session = ensure_private_directory(root / "sessions" / "synthetic")
    regular = session / "regular.bin"
    regular.write_bytes(b"synthetic")
    regular.chmod(0o600)
    os.link(regular, session / "hardlink.bin")
    os.mkfifo(session / "pipe")
    (session / "link").symlink_to(regular)

    errors = PlaybackDiagnosticsReadOnlyStore(root)._verify_private_permissions(session)

    assert any(error.startswith("symlink_rejected:link") for error in errors)
    assert any(error.startswith("hardlink_rejected:") for error in errors)
    assert any(error.startswith("non_regular_file_rejected:pipe") for error in errors)


def test_export_obeys_capacity_and_accounts_a_replacement_that_raises_after_write(
    tmp_path,
    monkeypatch,
):
    root = ensure_private_directory(tmp_path / "diagnostics")
    guard = DiagnosticsCapacityGuard(
        root,
        hard_cap_bytes=1_000,
        emergency_reserve_bytes=100,
        minimum_free_bytes=1,
        disk_usage_reader=lambda _path: SimpleNamespace(free=10_000),
    )
    oversized_event = {**_event(1), "payload": {"state": "x" * 2_000}}
    with pytest.raises(DiagnosticsCapacityError):
        export_events(
            root,
            session_id=SESSION_ID,
            events=[oversized_event],
            format_name="ndjson",
            capacity=guard,
        )
    assert not (root / "exports" / f"{SESSION_ID}.ndjson").exists()

    guard = DiagnosticsCapacityGuard(
        root,
        hard_cap_bytes=100_000,
        emergency_reserve_bytes=10_000,
        minimum_free_bytes=1,
        disk_usage_reader=lambda _path: SimpleNamespace(free=1_000_000),
    )
    import backend.app.services.playback_diagnostics.exports as exports_module

    original_atomic_write = exports_module.atomic_write_bytes

    def write_then_fail(path, payload, **kwargs):
        original_atomic_write(path, payload, **kwargs)
        raise OSError("synthetic post-replace failure")

    monkeypatch.setattr(exports_module, "atomic_write_bytes", write_then_fail)
    with pytest.raises(OSError, match="post-replace"):
        export_events(
            root,
            session_id=SESSION_ID,
            events=[_event(1)],
            format_name="ndjson",
            capacity=guard,
        )
    assert guard.reserved_bytes == 0
    assert guard.usage_bytes == directory_size_bytes(root)
