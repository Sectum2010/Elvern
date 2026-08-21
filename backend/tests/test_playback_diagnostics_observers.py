from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

from starlette.requests import Request

from backend.app.routes import browser_playback
from backend.app.services import google_drive_service
from backend.app.services.mobile_playback_service import MobilePlaybackManager
from backend.app.services.playback_diagnostics import (
    eta_observer,
    ffmpeg_observer,
    manager_observer,
    provider_observer,
    runtime,
)
from backend.app.services.playback_diagnostics.host_sampler import (
    MAX_CAPTURED_INCIDENT_LINKS,
    MAX_UNSUPPORTED_CAPABILITY_LINKS,
    HostDiagnosticsSampler,
    cpu_percent_between,
    parse_meminfo,
    parse_pressure,
    parse_process_io,
    parse_process_stat,
    parse_proc_stat,
)
from backend.app.services.playback_diagnostics.catalog import DiagnosticsCatalog
from backend.app.services.playback_diagnostics.fileio import ensure_private_directory
from backend.app.services.playback_diagnostics.event_normalization import (
    normalize_deferred_observation,
)


def test_provider_observer_measures_existing_request_without_url_or_headers(monkeypatch):
    events = []
    monkeypatch.setattr(
        provider_observer,
        "observe_runtime_event",
        lambda name, **kwargs: events.append((name, kwargs)),
    )
    observer = provider_observer.ProviderRequestObserver(
        playback_session_id="session-synthetic-00000001",
        range_header="bytes=100-199",
        retry_count=1,
    )
    observer.headers_received(
        SimpleNamespace(status=206, headers={"Content-Length": "100", "Authorization": "forbidden"}),
        (100, 199, 1_000),
    )
    observer.chunk(40)
    observer.chunk(60)
    observer.finish(eof=True)

    assert [name for name, _ in events] == [
        "provider_request_started",
        "provider_headers_received",
        "provider_first_byte",
        "provider_request_completed",
    ]
    serialized = repr(events)
    assert "Authorization" not in serialized
    assert "forbidden" not in serialized
    assert events[-1][1]["payload"]["actual_bytes"] == 100


def test_route2_frontier_uses_the_authoritative_segment_duration():
    assert ffmpeg_observer.route2_frontier_ms(0, segment_duration_seconds=2.0) == 2_000
    assert ffmpeg_observer.route2_frontier_ms(3, segment_duration_seconds=2.0) == 8_000


def test_provider_observer_failure_preserves_existing_stream_bytes(monkeypatch):
    health = []
    monkeypatch.setattr(
        google_drive_service,
        "record_runtime_health",
        lambda component, reason: health.append((component, reason)),
    )

    class ExplodingObserver:
        def chunk(self, _size):
            raise RuntimeError("synthetic diagnostics failure")

        def finish(self, **_kwargs):
            raise RuntimeError("synthetic diagnostics failure")

    class SyntheticUpstream:
        def __init__(self) -> None:
            self.chunks = iter((b"first", b"second", b""))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            return next(self.chunks)

    streamed = b"".join(
        google_drive_service._iter_upstream_response(
            SyntheticUpstream(),
            observer=ExplodingObserver(),
        )
    )
    assert streamed == b"firstsecond"

    monkeypatch.setattr(
        google_drive_service,
        "ProviderRequestObserver",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic constructor failure")),
    )
    assert google_drive_service._create_provider_observer(
        playback_session_id="session-synthetic-00000001",
        range_header="bytes=0-9",
    ) is None
    assert health[-1] == ("provider_integration", "observer_construction_failed")
    assert len(health[:-1]) >= 3
    assert set(health[:-1]) == {
        ("provider_integration", "observer_callback_failed"),
    }


def test_provider_active_read_time_is_separate_from_downstream_backpressure(monkeypatch):
    events = []
    monkeypatch.setattr(
        provider_observer,
        "observe_runtime_event",
        lambda name, **kwargs: events.append((name, kwargs)),
    )
    observer = provider_observer.ProviderRequestObserver(
        playback_session_id="session-synthetic-00000001",
        range_header="bytes=0-99",
    )
    observer.started_ns = 0
    ticks = iter((1_000_000_000, 1_100_000_000, 1_200_000_000, 1_500_000_000, 2_000_000_000))
    monkeypatch.setattr(provider_observer.time, "monotonic_ns", lambda: next(ticks))

    observer.read_started()
    observer.chunk(100)
    observer.downstream_wait_started()
    observer.downstream_resumed()
    observer.finish(eof=True)

    completed = next(payload for name, payload in events if name == "provider_request_completed")
    metrics = completed["payload"]
    assert metrics["active_upstream_read_ms"] == 100
    assert metrics["upstream_wait_ms"] == 100
    assert metrics["consumer_backpressure_ms"] == 300
    assert metrics["request_duration_ms"] == 2_000
    assert metrics["upstream_active_read_throughput_bps"] == 8_000
    assert metrics["end_to_end_delivery_rate_bps"] == 400


def test_ffmpeg_fingerprint_redacts_paths_urls_and_secret_option_values():
    first = ffmpeg_observer.ffmpeg_command_fingerprint([
        "/usr/bin/ffmpeg",
        "-headers",
        "Authorization: Bearer synthetic-secret",
        "-i",
        "https://provider.invalid/file?token=one",
        "-c:v",
        "libx264",
        "/private/output/segment.m4s",
    ])
    second = ffmpeg_observer.ffmpeg_command_fingerprint([
        "/opt/ffmpeg",
        "-headers",
        "Authorization: Bearer another-secret",
        "-i",
        "https://other.invalid/file?token=two",
        "-c:v",
        "libx264",
        "/different/output/segment.m4s",
    ])
    assert first == second
    assert len(first) == 64


def test_ffmpeg_process_hook_defers_fingerprinting_and_exposes_no_command_secret(
    monkeypatch,
):
    events = []
    monkeypatch.setattr(
        ffmpeg_observer,
        "observe_runtime_event",
        lambda name, **kwargs: events.append((name, kwargs)),
    )
    ffmpeg_observer.observe_ffmpeg_process_spawned(
        playback_session_id="session-synthetic-00000001",
        command=[
            "/usr/bin/ffmpeg",
            "-headers",
            "Authorization: Bearer synthetic-secret",
            "-i",
            "https://provider.invalid/file?token=one",
            "/private/output/segment.m4s",
        ],
        pid=123,
        worker_id="worker-1",
        epoch_id="epoch-1",
        selected_threads=4,
    )

    captured = events[0][1]
    serialized = repr(captured)
    assert "synthetic-secret" not in serialized
    assert "provider.invalid" not in serialized
    assert "/private/output" not in serialized
    assert "command_fingerprint" not in captured["payload"]
    normalized = normalize_deferred_observation(
        {"payload": captured["payload"]}
    )
    assert len(normalized["payload"]["command_fingerprint"]) == 64
    assert "diagnostics_command_shape" not in normalized["payload"]


def test_playback_observers_use_monotonic_ids_and_defer_crypto_from_hot_hooks():
    for module in (
        provider_observer,
        manager_observer,
        ffmpeg_observer,
    ):
        source = inspect.getsource(module)
        assert "secrets." not in source
        assert "hashlib." not in source
    spawn_source = inspect.getsource(ffmpeg_observer.observe_ffmpeg_process_spawned)
    assert "ffmpeg_command_fingerprint(" not in spawn_source


def test_browser_playback_diagnostics_hook_failures_never_escape_routes(monkeypatch):
    health = []
    monkeypatch.setattr(
        browser_playback,
        "record_runtime_health",
        lambda component, reason: health.append((component, reason)),
    )
    monkeypatch.setattr(
        browser_playback,
        "try_capture_diagnostic_observation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic")),
    )
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/browser-playback/sessions",
        "headers": [(b"user-agent", b"synthetic-browser")],
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 8000),
        "scheme": "http",
    })

    browser_playback._observe_browser_session_created(
        request,
        object(),
        session_id="session-synthetic-00000001",
        user_id=1,
    )
    browser_playback._observe_browser_event(
        "diagnostics_session_finalize_requested",
        session_id="session-synthetic-00000001",
        priority="critical",
    )
    assert health == [
        ("browser_playback", "session_registration_capture_failed"),
        ("browser_playback", "event_capture_failed"),
    ]


def test_ffmpeg_observer_failure_never_escapes_process_hook(monkeypatch):
    health = []
    monkeypatch.setattr(
        ffmpeg_observer,
        "record_runtime_health",
        lambda component, reason: health.append((component, reason)),
    )
    monkeypatch.setattr(
        ffmpeg_observer,
        "observe_runtime_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic")),
    )
    ffmpeg_observer.observe_ffmpeg_process_spawned(
        playback_session_id="session-synthetic-00000001",
        command=["ffmpeg", "-version"],
        pid=123,
        worker_id="worker-1",
        epoch_id="epoch-1",
        selected_threads=4,
    )
    assert health == [("ffmpeg_observer", "process_spawn_capture_failed")]


def test_linux_host_parsers_preserve_counters_and_intervals():
    proc = parse_proc_stat(
        "cpu  10 1 4 20 2 0 1 0 0 0\n"
        "cpu0 5 1 2 10 1 0 1 0 0 0\n"
        "ctxt 99\nintr 101 0\nprocs_running 3\n"
    )
    assert proc["context_switches"] == 99
    assert proc["interrupts"] == 101
    assert proc["runnable_tasks"] == 3
    cpu = cpu_percent_between([1, 0, 1, 5, 0], [3, 0, 2, 7, 1])
    assert cpu["total"] == 6
    assert cpu["cpu_percent"] > 0
    memory = parse_meminfo("MemTotal: 100 kB\nMemAvailable: 80 kB\nCached: 10 kB\n")
    assert memory == {"total": 102_400, "available": 81_920, "cache": 10_240}
    pressure = parse_pressure("some avg10=0.10 avg60=0.20 avg300=0.30 total=42\n")
    assert pressure["some"]["total"] == 42
    process = parse_process_stat(
        "123 (synthetic worker) R 1 1 1 0 0 0 7 0 3 0 10 5 0 0 20 0 4 0 0 0 0",
        clock_ticks=100,
    )
    assert process["minor_faults"] == 7
    assert process["major_faults"] == 3
    assert process["cpu_seconds"] == 0.15
    assert process["worker_threads"] == 4
    assert parse_process_io("read_bytes: 12\nwrite_bytes: 34\n") == {
        "read_bytes": 12,
        "write_bytes": 34,
    }


def test_runtime_observer_is_noop_without_service():
    runtime.set_active_diagnostics_service(None)
    runtime.observe_runtime_event(
        "synthetic_event",
        playback_session_id="session-synthetic-00000001",
        payload={"state": "safe"},
    )


def test_native_stream_correlation_skips_a_contended_diagnostics_lock(monkeypatch):
    class _ContendedLock:
        @staticmethod
        def acquire(*, blocking):
            assert blocking is False
            return False

        @staticmethod
        def release():
            raise AssertionError("A lock that was not acquired must not be released")

    monkeypatch.setattr(runtime, "_lock", _ContendedLock())

    runtime.register_native_stream_context("native-session", "playback-session")

    assert runtime.resolve_native_stream_context("native-session") is None


def test_atc_evaluation_and_action_share_one_decision_without_changing_values(monkeypatch):
    events = []
    monkeypatch.setattr(
        manager_observer,
        "observe_runtime_event",
        lambda name, **kwargs: events.append((name, kwargs)),
    )
    decision_id = manager_observer.observe_atc_controller_evaluation(
        playback_session_id="session-synthetic-00000001",
        epoch_id="epoch-synthetic-00000001",
        worker_id="worker-synthetic-00000001",
        current_threads=10,
        target_threads=6,
        action="downshift",
        confidence=0.75,
        bottleneck_class="cpu_oversupply",
        reasons=["measured_oversupply"],
        blockers=[],
        input_snapshot={"assigned_threads": 10},
    )
    manager_observer.observe_atc_action(
        playback_session_id="session-synthetic-00000001",
        epoch_id="epoch-synthetic-00000001",
        worker_id="worker-synthetic-00000001",
        decision_id=decision_id,
        action="downshift",
        applied=True,
        reason="replacement_created",
        target_threads=6,
    )

    assert decision_id is not None
    assert {payload["decision_id"] for _name, payload in events} == {decision_id}
    produced = next(payload for name, payload in events if name == "atc_decision_produced")
    applied = next(payload for name, payload in events if name == "atc_action_applied")
    assert produced["payload"]["current_threads"] == 10
    assert produced["payload"]["target_threads"] == 6
    assert produced["payload"]["confidence"] == 0.75
    assert applied["payload"]["target_threads"] == 6


def test_status_polling_source_contains_no_atc_evaluation_hook():
    status_source = inspect.getsource(MobilePlaybackManager.get_route2_worker_status)
    assert "observe_atc_controller_evaluation" not in status_source
    assert "observe_atc_action" not in status_source


def test_eta_replacement_and_resolution_use_monotonic_duration_and_correct_units(monkeypatch):
    events = []
    monkeypatch.setattr(
        eta_observer,
        "observe_runtime_event",
        lambda name, **kwargs: events.append((name, kwargs)),
    )
    monotonic_ticks = iter((1_000_000_000, 2_000_000_000, 4_000_000_000))
    wall_ticks = iter((10_000_000_000, 11_000_000_000, 12_000_000_000))
    monkeypatch.setattr(eta_observer.time, "monotonic_ns", lambda: next(monotonic_ticks))
    monkeypatch.setattr(eta_observer.time, "time_ns", lambda: next(wall_ticks))
    with eta_observer._lock:
        eta_observer._pending.clear()
    base = {
        "session_id": "session-synthetic-00000001",
        "mode_ready": False,
        "mode_estimate_source": "published_frontier",
        "ahead_runway_seconds": 4,
        "supply_rate_x": 1.25,
    }

    eta_observer.observe_eta_snapshot({**base, "mode_estimate_seconds": 5})
    eta_observer.observe_eta_snapshot({**base, "mode_estimate_seconds": 3})
    eta_observer.observe_eta_snapshot({**base, "mode_estimate_seconds": None, "mode_ready": True})

    predictions = [payload for name, payload in events if name == "eta_prediction"]
    superseded = [payload for name, payload in events if name == "eta_prediction_superseded"]
    resolved = [payload for name, payload in events if name == "eta_resolved"]
    assert len(predictions) == 2
    assert len(superseded) == 1
    assert len(resolved) == 1
    assert superseded[0]["payload"]["prediction_id"] == predictions[0]["payload"]["prediction_id"]
    assert superseded[0]["payload"]["replacement_prediction_id"] == predictions[1]["payload"]["prediction_id"]
    assert resolved[0]["payload"]["prediction_id"] == predictions[1]["payload"]["prediction_id"]
    assert resolved[0]["payload"]["actual_duration_ms"] == 2_000
    assert predictions[1]["payload"]["predicted_duration_ms"] == 3_000
    assert predictions[1]["payload"]["input_snapshot"]["supply_rate_x"] == 1.25
    with eta_observer._lock:
        eta_observer._pending.clear()


def test_tailscale_paths_are_session_specific_and_persist_no_raw_peer_identity(
    tmp_path,
    monkeypatch,
):
    events = []
    sampler = HostDiagnosticsSampler(
        active_session_ids=lambda: ("session-direct", "session-derp", "session-unmapped"),
        active_session_clients=lambda: {
            "session-direct": "100.64.0.10",
            "session-derp": "100.64.0.20",
        },
        observe=lambda name, **kwargs: events.append((name, kwargs)),
        identity_key=b"i" * 32,
        diagnostics_root=tmp_path / "diagnostics",
        transcode_root=tmp_path / "transcode",
    )
    status = {
        "BackendState": "Running",
        "Health": [],
        "Peer": {
            "peer-a": {
                "Active": True,
                "HostName": "private-host-a",
                "TailscaleIPs": ["100.64.0.10"],
                "CurAddr": "198.51.100.10:41641",
            },
            "peer-b": {
                "Active": True,
                "HostName": "private-host-b",
                "TailscaleIPs": ["100.64.0.20"],
                "Relay": "sea",
            },
        },
    }
    monkeypatch.setattr("backend.app.services.playback_diagnostics.host_sampler.shutil.which", lambda _name: "/usr/bin/tailscale")
    monkeypatch.setattr(
        "backend.app.services.playback_diagnostics.host_sampler._run_bounded_command",
        lambda *_args, **_kwargs: (json.dumps(status).encode("utf-8"), b""),
    )

    sampler._sample_tailscale(("session-direct", "session-derp", "session-unmapped"))

    paths = {
        payload["playback_session_id"]: payload["payload"]["connection_path"]
        for name, payload in events
        if name == "tailscale_status"
    }
    assert paths == {
        "session-direct": "tailnet_direct",
        "session-derp": "derp",
        "session-unmapped": "unknown",
    }
    serialized = repr(events)
    for prohibited in (
        "100.64.0.10",
        "100.64.0.20",
        "198.51.100.10",
        "private-host-a",
        "private-host-b",
    ):
        assert prohibited not in serialized


def test_one_host_sample_is_stored_once_and_linked_to_multiple_sessions(tmp_path):
    root = ensure_private_directory(tmp_path / "diagnostics")
    catalog = DiagnosticsCatalog(root)
    for session_id in ("session-synthetic-one", "session-synthetic-two"):
        catalog.upsert_session(
            {
                "playback_session_id": session_id,
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
                "session_relative_path": f"sessions/{session_id}",
                "created_at_utc": "2026-08-20T00:00:00+00:00",
            }
        )
    links = (
        ("session-synthetic-one", None, None),
        ("session-synthetic-two", None, None),
    )

    assert catalog.record_host_observation(
        sample_id="host-synthetic-sample",
        event_name="host_aggregate",
        observed_wall_time_ns="100",
        observed_monotonic_time_ns="50",
        encrypted_payload=b"synthetic-encrypted-payload",
        links=links,
    ) is True
    assert catalog.record_host_observation(
        sample_id="host-synthetic-sample",
        event_name="host_aggregate",
        observed_wall_time_ns="100",
        observed_monotonic_time_ns="50",
        encrypted_payload=b"synthetic-encrypted-payload",
        links=links,
    ) is False
    with catalog._connect() as connection:
        observations = connection.execute(
            "SELECT COUNT(*) FROM diagnostic_host_observations"
        ).fetchone()[0]
        session_links = connection.execute(
            "SELECT COUNT(*) FROM diagnostic_session_host_links"
        ).fetchone()[0]
    assert observations == 1
    assert session_links == 2


def test_eta_capacity_eviction_is_explicit_and_session_cleanup_is_bounded(monkeypatch):
    events = []
    monkeypatch.setattr(
        eta_observer,
        "observe_runtime_event",
        lambda name, **kwargs: events.append((name, kwargs)),
    )
    monkeypatch.setattr(eta_observer, "_MAX_PENDING", 2)
    with eta_observer._lock:
        eta_observer._pending.clear()

    for session_number in range(3):
        eta_observer.observe_eta_snapshot(
            {
                "session_id": f"session-synthetic-{session_number:08d}",
                "mode_estimate_seconds": 2,
                "mode_estimate_source": "published_frontier",
                "mode_ready": False,
            }
        )

    superseded = [payload for name, payload in events if name == "eta_prediction_superseded"]
    assert len(eta_observer._pending) == 2
    assert superseded[-1]["payload"]["replacement_reason"] == "pending_ledger_capacity"
    eta_observer.forget_eta_session("session-synthetic-00000001")
    assert "session-synthetic-00000001" not in eta_observer._pending
    with eta_observer._lock:
        eta_observer._pending.clear()


def test_host_sampler_ledgers_are_bounded_and_clear_only_the_sealed_session(tmp_path):
    sampler = HostDiagnosticsSampler(
        active_session_ids=lambda: (),
        observe=lambda *_args, **_kwargs: None,
        diagnostics_root=tmp_path / "diagnostics",
        transcode_root=tmp_path / "transcode",
    )
    sampler._ring.append(sampler._new_sample_record({"sample": 1}))

    for index in range(MAX_UNSUPPORTED_CAPABILITY_LINKS + 1):
        sampler._emit_unsupported(
            (f"session-synthetic-{index:08d}",),
            "gpu",
            "unavailable",
        )
    for index in range(MAX_CAPTURED_INCIDENT_LINKS + 1):
        sampler.freeze_incident_ring(
            "session-to-forget",
            f"incident-synthetic-{index:08d}",
        )
    sampler._emit_unsupported(("session-to-forget",), "tailscale", "unavailable")
    sampler._emit_unsupported(("session-to-keep",), "tailscale", "unavailable")

    assert len(sampler._unsupported_emitted) == MAX_UNSUPPORTED_CAPABILITY_LINKS
    assert len(sampler._captured_incidents) == MAX_CAPTURED_INCIDENT_LINKS
    sampler.forget_session("session-to-forget")
    assert all(key[0] != "session-to-forget" for key in sampler._unsupported_emitted)
    assert all(key[0] != "session-to-forget" for key in sampler._captured_incidents)
    assert all(key[0] != "session-to-forget" for key in sampler._post_incidents)
    assert ("session-to-keep", "tailscale") in sampler._unsupported_emitted


def test_manager_decision_links_are_removed_for_only_the_sealed_session():
    with manager_observer._decision_lock:
        manager_observer._decisions_by_epoch.clear()
    manager_observer.bind_atc_decision("session-to-forget", "epoch-1", "decision-1")
    manager_observer.bind_atc_decision("session-to-keep", "epoch-2", "decision-2")

    manager_observer.forget_manager_session("session-to-forget")

    assert manager_observer._linked_atc_decision("session-to-forget", "epoch-1") is None
    assert manager_observer._linked_atc_decision("session-to-keep", "epoch-2") == "decision-2"
    with manager_observer._decision_lock:
        manager_observer._decisions_by_epoch.clear()
