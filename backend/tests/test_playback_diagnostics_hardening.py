from __future__ import annotations

import json
import stat
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app import cli as app_cli
from backend.app.config import ConfigError, validate_settings
from backend.app.services.playback_diagnostics.capacity import CapacitySnapshot
from backend.app.services.playback_diagnostics.crypto import DiagnosticsKeyStore
from backend.app.services.playback_diagnostics.exports import (
    OptionalParquetDependencyError,
    export_events,
)
from backend.app.services.playback_diagnostics.fileio import ensure_private_directory
from backend.app.services.playback_diagnostics.journal import EncryptedJournal, verify_journal
from backend.app.services.playback_diagnostics.host_sampler import HostDiagnosticsSampler
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

    def event_exists(self, *_args) -> bool:
        return False

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

    def permit(self, _bytes: int, *, critical: bool = False):
        self.calls.append(critical)
        if critical:
            return True, self._snapshot("reserve")
        return False, self._snapshot("capacity_reached")

    def write_current_status(self, **payload):
        self.status_updates.append(payload)
        return self._snapshot("capacity_reached")


def _writer(tmp_path: Path, *, capacity, failure_callback=None):
    root = ensure_private_directory(tmp_path / "diagnostics")
    key_store = DiagnosticsKeyStore(root / "keys")
    active_key = key_store.load_or_create_active_key()
    catalog = _RecordingCatalog()
    writer = DiagnosticsWriter(
        root,
        catalog=catalog,
        capacity=capacity,
        key_store=key_store,
        active_key=active_key,
        failure_callback=failure_callback,
    )
    return writer, catalog, key_store


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
    capacity.permit = lambda _bytes, critical=False: (True, capacity._snapshot("normal"))
    failures = []
    writer, _catalog, _key_store = _writer(
        tmp_path,
        capacity=capacity,
        failure_callback=lambda reason, payload: failures.append((reason, payload)),
    )

    class _FailingJournal:
        def append(self, _events):
            raise OSError("synthetic writer failure")

    writer._journal_for = lambda _batch: _FailingJournal()
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


def test_ciphertext_corruption_is_quarantined_without_losing_other_sessions(tmp_path):
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

    recovered, _ = verify_journal(
        corrupt.path,
        key_store,
        recover=True,
        quarantine_root=root / "quarantine",
    )
    good_verification, good_events = verify_journal(good.path, key_store, include_events=True)

    assert recovered.valid is True
    assert recovered.recovered_bytes > 0
    assert recovered.quarantined_path is not None
    assert stat.S_IMODE(Path(recovered.quarantined_path).stat().st_mode) == 0o600
    assert good_verification.valid is True
    assert len(good_events) == 1


def test_host_incident_ring_is_idempotent_and_records_pre_and_post_windows(tmp_path):
    observed = []
    sampler = HostDiagnosticsSampler(
        active_session_ids=lambda: (),
        observe=lambda event_name, **payload: observed.append((event_name, payload)),
        diagnostics_root=tmp_path / "diagnostics",
        transcode_root=tmp_path / "transcode",
    )
    sampler._ring.extend([{"sample": 1}, {"sample": 2}])

    sampler.freeze_incident_ring(SESSION_ID, "incident-synthetic-0001")
    sampler.freeze_incident_ring(SESSION_ID, "incident-synthetic-0001")
    sampler._emit_incident_post_samples({"sample": 3}, time.monotonic())

    assert [name for name, _payload in observed].count("host_incident_pre_sample") == 2
    assert [name for name, _payload in observed].count("host_incident_post_sample") == 1
    assert all(
        payload["incident_id"] == "incident-synthetic-0001"
        for _name, payload in observed
    )


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
    for subcommand in ("status", "list", "inspect", "verify", "export", "reconcile"):
        command = ["playback-diagnostics", subcommand]
        if subcommand in {"inspect", "verify"}:
            command.append(SESSION_ID)
        if subcommand == "export":
            command.extend([SESSION_ID, "--format", "ndjson"])
        assert parser.parse_args(command).diagnostics_command == subcommand

    class _FakeService:
        def __init__(self, _settings) -> None:
            pass

        def start(self) -> None:
            pass

        def shutdown(self) -> None:
            pass

        def status(self):
            return {"enabled": True, "available": True, "root": "/local/diagnostics"}

    monkeypatch.setattr(app_cli, "PlaybackDiagnosticsService", _FakeService)
    args = parser.parse_args(["playback-diagnostics", "status"])
    assert app_cli._run_playback_diagnostics_cli(args, SimpleNamespace()) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {"enabled": True, "available": True, "root": "/local/diagnostics"}


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
