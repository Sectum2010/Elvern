from __future__ import annotations

import csv
import io
import json
import hashlib
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


class _RecordingCatalog:
    def __init__(self) -> None:
        self.recorded: list[dict[str, object]] = []
        self.path = Path("/nonexistent/catalog.sqlite3")

    def classify_event(self, *_args) -> str:
        return "new"

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


def _writer(tmp_path: Path, *, capacity, failure_callback=None):
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


def test_mixed_batch_preserves_critical_close_in_emergency_reserve(tmp_path):
    capacity = _ReserveCapacity()
    failures = []
    writer, catalog, key_store = _writer(
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

    writer._write_batch(batch)

    assert capacity.calls == [False, True]
    assert [event["event_name"] for event in catalog.recorded] == ["session_close"]
    assert writer.metrics()["events_dropped"] == 1
    assert failures[0][0] == "capacity_reached"
    journal_path = next((writer.root / batch.session_relative_path / "raw").glob("*.elvd"))
    verification, events = verify_journal(journal_path, key_store, include_events=True)
    assert verification.valid is True
    assert [event["event_name"] for event in events] == ["session_close"]


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

    real_open = Path.open

    def deny_journal_read(path, mode="r", *args, **kwargs):
        if path == journal.path and mode == "rb":
            raise PermissionError("synthetic permission failure")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_journal_read)
    permission_failure, _events = verify_journal(
        journal.path,
        key_store,
        recover=True,
        quarantine_root=root / "quarantine",
    )
    monkeypatch.setattr(Path, "open", real_open)
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
        hard_cap_bytes=1_000,
        emergency_reserve_bytes=100,
        minimum_free_bytes=1,
        disk_usage_reader=lambda _path: SimpleNamespace(free=10_000),
    )
    barrier = threading.Barrier(3)
    reservations = []
    failures = []

    def reserve() -> None:
        barrier.wait()
        try:
            reservations.append(guard.reserve(600))
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
    assert guard.reserved_bytes == 600
    reservations[0].release()
    assert guard.reserved_bytes == 0


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
    original_finish = guard._finish_reservation

    def capture_finish(reserved_bytes: int, actual_bytes: int) -> None:
        accounting.append((reserved_bytes, actual_bytes))
        original_finish(reserved_bytes, actual_bytes)

    monkeypatch.setattr(guard, "_finish_reservation", capture_finish)
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


def test_fsync_failure_reindexes_flushed_record_before_retry_without_duplicate(
    tmp_path,
    monkeypatch,
):
    writer, catalog, key_store, batch = _durable_writer(tmp_path)
    real_fsync = journal_module.os.fsync
    failed = False

    def fail_first_fsync(descriptor):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("synthetic fsync failure")
        return real_fsync(descriptor)

    monkeypatch.setattr(journal_module.os, "fsync", fail_first_fsync)
    with pytest.raises(OSError, match="synthetic fsync failure"):
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


def test_catalog_failure_after_journal_fsync_rebuilds_before_retry_without_duplicate(
    tmp_path,
    monkeypatch,
):
    writer, catalog, key_store, batch = _durable_writer(tmp_path)
    real_record_events = catalog.record_events
    failed = False

    def fail_first_catalog_commit(**kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("synthetic catalog commit failure")
        return real_record_events(**kwargs)

    monkeypatch.setattr(catalog, "record_events", fail_first_catalog_commit)
    with pytest.raises(OSError, match="synthetic catalog commit failure"):
        writer._write_batch(batch)

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

    def write_then_fail(path, payload):
        original_atomic_write(path, payload)
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
