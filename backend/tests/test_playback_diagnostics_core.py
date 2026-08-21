from __future__ import annotations

import json
import os
import stat
from collections import namedtuple
from pathlib import Path

import pytest

from backend.app.services.playback_diagnostics.capacity import DiagnosticsCapacityGuard
from backend.app.services.playback_diagnostics.catalog import DiagnosticsCatalog
from backend.app.services.playback_diagnostics.constants import (
    DIAGNOSTICS_EMERGENCY_RESERVE_BYTES,
    DIAGNOSTICS_HARD_CAP_BYTES,
    DIAGNOSTICS_NORMAL_BUDGET_BYTES,
    JOURNAL_MAGIC,
    SCHEMA_VERSION,
)
from backend.app.services.playback_diagnostics.crypto import DiagnosticsKeyStore
from backend.app.services.playback_diagnostics.fileio import (
    UnsafeDiagnosticsPathError,
    atomic_write_bytes,
    ensure_private_directory,
    resolve_beneath,
)
from backend.app.services.playback_diagnostics.identity import DiagnosticIdentityStore
from backend.app.services.playback_diagnostics.journal import EncryptedJournal, verify_journal
from backend.app.services.playback_diagnostics.privacy import (
    DiagnosticsPrivacyError,
    basename_sha256,
    safe_source_basename,
    sanitize_event,
    sanitize_payload,
)
from backend.app.services.playback_diagnostics.schema import PlaybackDiagnosticEvent


DiskUsage = namedtuple("DiskUsage", "total used free")


def _event(sequence: int, *, event_id: str | None = None) -> dict[str, object]:
    return PlaybackDiagnosticEvent(
        event_id=event_id or f"event-{sequence:08d}",
        event_name="client_aggregate",
        event_source="client",
        playback_session_id="session-00000001",
        event_sequence=sequence,
        source_sequence=sequence,
        client_wall_time_ms=1_000 + sequence,
        client_monotonic_time_us=2_000 + sequence,
        aligned_wall_time_ns=str(1_000_000_000 + sequence),
        observation_kind="measured_client",
        payload={"buffered_ahead_ms": 5_000},
    ).model_dump(mode="json")


def _key_store(tmp_path: Path):
    root = ensure_private_directory(tmp_path / "keys")
    store = DiagnosticsKeyStore(root)
    return store, store.load_or_create_active_key()


def test_diagnostics_config_defaults_are_enabled_without_retention(test_settings):
    assert test_settings.playback_diagnostics_enabled is True
    assert test_settings.playback_diagnostics_max_bytes == DIAGNOSTICS_HARD_CAP_BYTES
    assert test_settings.playback_diagnostics_root == test_settings.data_dir / "playback_diagnostics"
    assert not hasattr(test_settings, "playback_diagnostics_retention_days")
    assert not hasattr(test_settings, "playback_diagnostics_ttl")
    assert DIAGNOSTICS_NORMAL_BUDGET_BYTES == 79_500_000_000
    assert DIAGNOSTICS_EMERGENCY_RESERVE_BYTES == 500_000_000


def test_capacity_guard_uses_reserve_only_for_critical_and_never_deletes(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    old_record = root / "old.elvd"
    old_record.write_bytes(b"x" * 901)
    guard = DiagnosticsCapacityGuard(
        root,
        hard_cap_bytes=1_000,
        emergency_reserve_bytes=100,
        minimum_free_bytes=1,
        disk_usage_reader=lambda _path: DiskUsage(10_000, 0, 10_000),
    )

    allowed, snapshot = guard.permit(1, critical=False)
    assert allowed is False
    assert snapshot.state == "capacity_reached"
    assert old_record.exists()

    allowed, snapshot = guard.permit(1, critical=True)
    assert allowed is True
    assert snapshot.state == "reserve"
    assert old_record.exists()

    allowed, snapshot = guard.permit(100, critical=True)
    assert allowed is False
    assert snapshot.state == "capacity_exhausted"
    assert old_record.exists()


def test_capacity_guard_resumes_after_manual_deletion(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    record = root / "manual-delete.elvd"
    record.write_bytes(b"x" * 950)
    guard = DiagnosticsCapacityGuard(
        root,
        hard_cap_bytes=1_000,
        emergency_reserve_bytes=100,
        minimum_free_bytes=1,
        disk_usage_reader=lambda _path: DiskUsage(10_000, 0, 10_000),
    )
    assert guard.permit(1)[0] is False
    record.unlink()
    allowed, snapshot = guard.permit(100)
    assert allowed is True
    assert snapshot.state == "normal"


def test_capacity_guard_honors_filesystem_floor_without_deleting(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    record = root / "keep.elvd"
    record.write_bytes(b"record")
    guard = DiagnosticsCapacityGuard(
        root,
        hard_cap_bytes=1_000,
        emergency_reserve_bytes=100,
        minimum_free_bytes=200,
        disk_usage_reader=lambda _path: DiskUsage(10_000, 9_850, 150),
    )
    allowed, snapshot = guard.permit(1, critical=True)
    assert allowed is False
    assert snapshot.state == "filesystem_low_space"
    assert record.read_bytes() == b"record"


def test_key_store_creates_private_independent_256_bit_key(tmp_path):
    store, key = _key_store(tmp_path)
    assert len(key.material) == 32
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE((store.root / f"key-{key.key_id}.bin").stat().st_mode) == 0o600
    assert stat.S_IMODE(store.active_key_path.stat().st_mode) == 0o600


def test_identity_mapping_is_encrypted_and_can_be_unlinked(tmp_path):
    store, key = _key_store(tmp_path)
    identities = DiagnosticIdentityStore(tmp_path / "identities", store, key)
    subject = identities.get_or_create_subject(42)
    assert subject.startswith("subject_")
    encrypted = identities.path.read_bytes()
    assert b'"42":' not in encrypted
    assert subject.encode() not in encrypted
    assert identities.resolve_subject_for_local_join(42) == subject
    assert identities.unlink_user(42) is True
    assert identities.resolve_subject_for_local_join(42) is None


def test_encrypted_journal_round_trip_hash_chain_and_unique_nonce(tmp_path):
    store, key = _key_store(tmp_path)
    journal = EncryptedJournal(
        tmp_path / "sessions" / "raw" / "client-000001.elvd",
        playback_session_id="session-00000001",
        source_type="client",
        key_store=store,
        active_key=key,
        quarantine_root=tmp_path / "quarantine",
    )
    first = journal.append([_event(1)])
    second = journal.append([_event(2)])
    assert first is not None and second is not None
    assert first.nonce != second.nonce
    assert second.previous_chunk_hash == first.current_chunk_hash
    assert journal.path.read_bytes().startswith(JOURNAL_MAGIC)
    verification, events = verify_journal(journal.path, store, include_events=True)
    assert verification.valid is True
    assert verification.chunk_count == 2
    assert [event["source_sequence"] for event in events] == [1, 2]


def test_journal_recovers_truncated_tail_and_quarantines_it(tmp_path):
    store, key = _key_store(tmp_path)
    journal = EncryptedJournal(
        tmp_path / "raw" / "server-000001.elvd",
        playback_session_id="session-00000001",
        source_type="server",
        key_store=store,
        active_key=key,
        quarantine_root=tmp_path / "quarantine",
    )
    journal.append([_event(1)])
    original_size = journal.path.stat().st_size
    with journal.path.open("ab") as handle:
        handle.write(b"partial-record")
    verification, _ = verify_journal(
        journal.path,
        store,
        recover=True,
        quarantine_root=tmp_path / "quarantine",
    )
    assert verification.valid is True
    assert verification.recovered_bytes == len(b"partial-record")
    assert verification.quarantined_path is not None
    assert journal.path.stat().st_size == original_size


def test_private_paths_reject_symlinks_and_traversal(tmp_path):
    root = ensure_private_directory(tmp_path / "root")
    with pytest.raises(UnsafeDiagnosticsPathError):
        resolve_beneath(root, "..", "outside")
    target = tmp_path / "target"
    target.write_text("target", encoding="utf-8")
    link = root / "link"
    link.symlink_to(target)
    with pytest.raises(UnsafeDiagnosticsPathError):
        atomic_write_bytes(link, b"replacement")


def test_privacy_preserves_exact_basename_but_rejects_paths_urls_and_secrets():
    basename = "Winnetou - Apache Gold (1963) 复刻版.mkv"
    assert safe_source_basename(f"/private/media/{basename}") == basename
    assert len(basename_sha256(basename)) == 64
    safe = sanitize_payload({"source_original_filename": basename, "buffered_ahead_ms": 1000})
    assert safe["source_original_filename"] == basename

    for payload in (
        {"reason": "Authorization: Bearer secret-value-123456"},
        {"reason": "https://example.test/movie?token=secret"},
        {"reason": "/srv/private/media/movie.mkv"},
    ):
        with pytest.raises(DiagnosticsPrivacyError):
            sanitize_payload(payload)


def test_privacy_allows_only_normalized_browser_playback_routes():
    safe = sanitize_payload(
        {
            "normalized_route": "/api/browser-playback/epochs/:id/segments/:segment",
            "route_template": "/api/browser-playback/:scope/index.m3u8",
        }
    )
    assert safe["normalized_route"].endswith("/:id/segments/:segment")
    assert safe["route_template"].endswith("/:scope/index.m3u8")

    for route in (
        "/srv/media/private.mkv",
        "/api/browser-playback/../../private",
        "/api/browser-playback/session?token=secret",
        "https://example.test/api/browser-playback/session",
    ):
        with pytest.raises(DiagnosticsPrivacyError):
            sanitize_payload({"normalized_route": route})


def test_event_allowlist_drops_unknown_fields_and_requires_decimal_ns():
    raw = _event(1)
    raw["unknown_top_level"] = "discard me"
    raw["payload"]["unknown_payload"] = "discard me"
    sanitized = sanitize_event(raw)
    assert "unknown_top_level" not in sanitized
    assert "unknown_payload" not in sanitized["payload"]
    raw["aligned_wall_time_ns"] = 1.5
    with pytest.raises(DiagnosticsPrivacyError):
        sanitize_event(raw)


def test_event_envelope_rejects_paths_urls_secrets_and_unsafe_identifiers():
    cases = (
        ("epoch_id", "/srv/private/media/movie.mkv"),
        ("worker_id", "https://provider.invalid/worker"),
        ("incident_id", "Authorization: Bearer secret-value-123456"),
        ("trace_id", "trace id with spaces"),
    )
    for field, value in cases:
        raw = _event(1)
        raw[field] = value
        with pytest.raises(DiagnosticsPrivacyError):
            sanitize_event(raw)


def test_catalog_accounts_for_duplicate_out_of_order_and_reconcile(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    catalog = DiagnosticsCatalog(root)
    relative = Path("sessions/2026/08/20/session-00000001")
    session_path = ensure_private_directory(resolve_beneath(root, relative))
    (session_path / "session.json").write_text("{}", encoding="utf-8")
    metadata = {
        "playback_session_id": "session-00000001",
        "owner_hash": "owner-hash",
        "subject_id": "subject-random",
        "media_item_id": 7,
        "source_original_filename": "Movie.mkv",
        "source_filename_sha256": "a" * 64,
        "source_fingerprint": "b" * 64,
        "source_kind": "local",
        "platform": "linux",
        "device_class": "desktop",
        "playback_mode": "lite",
        "stream_mode": "route2",
        "hls_engine": "hls.js",
        "state": "active",
        "session_relative_path": str(relative),
        "created_at_utc": "2026-08-20T00:00:00+00:00",
    }
    catalog.upsert_session(metadata)
    catalog.register_source(
        playback_session_id="session-00000001",
        source_id="client-source",
        source_type="client",
    )
    inserted, duplicate, out_of_order = catalog.record_events(
        playback_session_id="session-00000001",
        source_id="client-source",
        journal_relative_path="sessions/raw/client.elvd",
        journal_chunk_sequence=1,
        events=[_event(2)],
    )
    assert (inserted, duplicate, out_of_order) == (1, 0, 1)
    assert catalog.ack_watermark("client-source") == 0
    inserted, duplicate, out_of_order = catalog.record_events(
        playback_session_id="session-00000001",
        source_id="client-source",
        journal_relative_path="sessions/raw/client.elvd",
        journal_chunk_sequence=2,
        events=[_event(1), _event(2)],
    )
    assert (inserted, duplicate, out_of_order) == (1, 1, 0)
    assert catalog.ack_watermark("client-source") == 2
    assert catalog.reconcile()["catalog_sessions_removed"] == 0
    for path in sorted(session_path.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
    session_path.rmdir()
    assert catalog.reconcile()["catalog_sessions_removed"] == 1


def test_event_schema_uses_decimal_strings_for_nanoseconds():
    event = PlaybackDiagnosticEvent.model_validate(_event(1))
    assert event.schema_version == SCHEMA_VERSION
    assert isinstance(event.aligned_wall_time_ns, str)
    invalid = _event(1)
    invalid["aligned_wall_time_ns"] = "1.5"
    with pytest.raises(ValueError):
        PlaybackDiagnosticEvent.model_validate(invalid)
