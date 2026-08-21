from __future__ import annotations

import json
import os
import stat
import struct
from collections import namedtuple
from pathlib import Path

import pytest

from backend.app import config as config_module
from backend.app.services.playback_diagnostics.capacity import DiagnosticsCapacityGuard
from backend.app.services.playback_diagnostics.catalog import DiagnosticsCatalog
from backend.app.services.playback_diagnostics.constants import (
    DIAGNOSTICS_EMERGENCY_RESERVE_BYTES,
    DIAGNOSTICS_HARD_CAP_BYTES,
    DIAGNOSTICS_NORMAL_BUDGET_BYTES,
    JOURNAL_MAGIC,
    SCHEMA_VERSION,
)
from backend.app.services.playback_diagnostics.crypto import DiagnosticsKey, DiagnosticsKeyStore
from backend.app.services.playback_diagnostics.fileio import (
    UnsafeDiagnosticsPathError,
    atomic_write_bytes,
    ensure_private_directory,
    resolve_beneath,
)
from backend.app.services.playback_diagnostics.identity import (
    DiagnosticIdentityStore,
    load_or_create_identity_key,
)
from backend.app.services.playback_diagnostics.journal import EncryptedJournal, verify_journal
from backend.app.services.playback_diagnostics.privacy import (
    DiagnosticsPrivacyError,
    basename_sha256,
    markdown_inline_code,
    safe_source_basename,
    safe_human_text,
    sanitize_event,
    sanitize_payload,
    spreadsheet_safe_cell,
)
from backend.app.services.playback_diagnostics.schema import (
    PlaybackDiagnosticEvent,
    SessionMetadataV2,
)
from backend.app.services.playback_diagnostics.summaries import build_summary


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


def test_diagnostics_config_defaults_are_disabled_without_retention(test_settings):
    assert test_settings.playback_diagnostics_enabled is False
    assert test_settings.playback_diagnostics_max_bytes == DIAGNOSTICS_HARD_CAP_BYTES
    assert test_settings.playback_diagnostics_root == test_settings.data_dir / "playback_diagnostics"
    assert not hasattr(test_settings, "playback_diagnostics_retention_days")
    assert not hasattr(test_settings, "playback_diagnostics_ttl")
    assert DIAGNOSTICS_NORMAL_BUDGET_BYTES == 79_500_000_000
    assert DIAGNOSTICS_EMERGENCY_RESERVE_BYTES == 500_000_000


def test_diagnostics_installation_switch_parses_explicit_values_and_example_is_off(
    monkeypatch,
):
    monkeypatch.setenv("ELVERN_PLAYBACK_DIAGNOSTICS_ENABLED", "true")
    assert config_module._get_bool("ELVERN_PLAYBACK_DIAGNOSTICS_ENABLED", False) is True

    monkeypatch.setenv("ELVERN_PLAYBACK_DIAGNOSTICS_ENABLED", "false")
    assert config_module._get_bool("ELVERN_PLAYBACK_DIAGNOSTICS_ENABLED", True) is False

    project_root = Path(__file__).resolve().parents[2]
    example = (project_root / "deploy" / "env" / ".env.example").read_text(
        encoding="utf-8"
    )
    setting_line = next(
        line
        for line in example.splitlines()
        if line.startswith("ELVERN_PLAYBACK_DIAGNOSTICS_ENABLED=")
    )
    assert setting_line.partition("=")[2].strip().strip('"') == "false"


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
    guard.reconcile_usage()
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


def test_owner_hash_is_stable_when_the_active_journal_key_changes(tmp_path):
    store, first_key = _key_store(tmp_path)
    identity_key = load_or_create_identity_key(tmp_path / "identities")
    first = DiagnosticIdentityStore(
        tmp_path / "identities",
        store,
        first_key,
        identity_key,
    )
    replacement_key = DiagnosticsKey(key_id="replacement-key", material=b"r" * 32)
    replacement = DiagnosticIdentityStore(
        tmp_path / "identities",
        store,
        replacement_key,
        identity_key,
    )

    assert first.owner_hash(42) == replacement.owner_hash(42)


def test_session_metadata_is_closed_and_rejects_non_finite_values():
    metadata = {
        "schema_version": "playback-diagnostics-session-v2",
        "diagnostics_event_schema": "playback-diagnostics-event-v2",
        "playback_session_id": "session-synthetic-00000001",
        "owner_hash": f"owner_{'a' * 64}",
        "subject_id": "subject_synthetic00000001",
        "media_item_id": 7,
        "source_original_filename": "Synthetic.mkv",
        "source_filename_sha256": "b" * 64,
        "source_fingerprint": "c" * 64,
        "source_kind": "local",
        "profile": "balanced",
        "playback_mode": "lite",
        "stream_mode": "route2",
        "platform": "linux",
        "device_class": "desktop",
        "browser_family": "firefox",
        "browser_version": "152",
        "os_family": "linux",
        "os_version": "synthetic",
        "hls_engine": "hls.js",
        "capabilities": {},
        "elvern_commit": "d" * 40,
        "ffmpeg_version": "synthetic",
        "config_fingerprint": "e" * 64,
        "state": "active",
        "created_at_utc": "2026-08-20T00:00:00+00:00",
        "updated_at_utc": "2026-08-20T00:00:00+00:00",
        "session_relative_path": "sessions/2026/08/20/session-synthetic-00000001",
    }
    assert SessionMetadataV2.model_validate(metadata).media_item_id == 7

    with pytest.raises(ValueError):
        SessionMetadataV2.model_validate({**metadata, "unexpected": "rejected"})
    with pytest.raises(ValueError):
        SessionMetadataV2.model_validate({**metadata, "duration_ms": float("nan")})


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
    truncated_tail = struct.pack(">Q", 100) + b"partial"
    with journal.path.open("ab") as handle:
        handle.write(truncated_tail)
    verification, _ = verify_journal(
        journal.path,
        store,
        recover=True,
        quarantine_root=tmp_path / "quarantine",
    )
    assert verification.valid is True
    assert verification.recovered_bytes == len(truncated_tail)
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
        {"reason": r"C:\private\media\movie.mkv"},
        {"reason": r"\\server\share\movie.mkv"},
        {"reason": "resource_key=private-drive-key"},
        {"reason": "Cookie: session=private-cookie"},
    ):
        with pytest.raises(DiagnosticsPrivacyError):
            sanitize_payload(payload)

    with pytest.raises(DiagnosticsPrivacyError):
        safe_source_basename("bad\x00name.mkv")


def test_exact_basename_remains_private_while_human_rendering_is_injection_safe():
    basename = "=Title`\nnext\rline\t\x1b[31m.mkv"
    assert safe_source_basename(basename) == basename
    rendered = safe_human_text(basename)
    assert "\n" not in rendered
    assert "\r" not in rendered
    assert "\t" not in rendered
    assert "\x1b" not in rendered
    assert "\\n" in rendered
    assert "\\u001b" in rendered
    assert markdown_inline_code(basename).count("`") >= 4
    for prefix in ("=", "+", "-", "@"):
        assert spreadsheet_safe_cell(f"{prefix}formula") == f"'{prefix}formula"


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


def test_required_frontend_emitted_field_families_survive_server_sanitization():
    intended = {
        "requested": "lite",
        "minimum_buffer_ms": 750,
        "type": "request_video_frame_callback",
        "media_element_time_ms": 1_250,
        "duplicate_count": 2,
        "out_of_order_count": 1,
        "buffered_range_count": 3,
        "seekable_range_count": 2,
        "played_range_count": 1,
        "chunk_sequence": 4,
        "final": True,
        "serialized_bytes": 512,
        "samples": [{"media_element_time_ms": 1_200, "buffered_range_count": 3}],
    }

    assert sanitize_payload(intended) == intended


def test_summary_uses_explicit_gaps_and_never_invents_a_completeness_score():
    metadata = {
        "state": "sealed",
        "capabilities": {},
        "source_original_filename": "Synthetic.mkv",
    }
    events = [
        {"event_name": "stall_candidate", "event_source": "client", "payload": {}},
        {"event_name": "stall_confirmed", "event_source": "client", "payload": {}},
        {"event_name": "recovery_waiting", "event_source": "client", "payload": {}},
        {"event_name": "recovery_action_applied", "event_source": "server", "payload": {}},
        {"event_name": "first_video_frame_inferred", "event_source": "client", "payload": {}},
    ]
    source_stats = [
        {
            "source_id": "client-source",
            "ack_watermark": 1,
            "max_seen_sequence": 100,
            "final_source_sequence": 100,
            "missing_ranges": [[2, 3], [7, 7]],
            "missing_sequence_count": 3,
        },
        {
            "source_id": "server-source",
            "ack_watermark": 1,
            "max_seen_sequence": 1,
            "final_source_sequence": 1,
            "missing_ranges": [],
            "missing_sequence_count": 0,
        },
    ]

    summary, completeness = build_summary(
        metadata,
        events,
        source_stats=source_stats,
        writer_metrics={"events_dropped": 0},
        capacity_state="normal",
    )

    quality = summary["diagnostics_quality"]
    assert quality["telemetry_completeness_score"] is None
    assert quality["completeness_assessment"] == "incomplete"
    assert quality["sequence_gap_count"] == 3
    assert quality["missing_sequence_ranges"] == {"client-source": [[2, 3], [7, 7]]}
    assert summary["qoe"]["stall_count"] == 1
    assert summary["qoe"]["recovery_action_count"] == 1
    assert summary["qoe"]["first_presented_frame_observed"] is False
    assert summary["qoe"]["inferred_first_frame_observed"] is True
    assert completeness["sequence_gap_count"] == 3


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
