from __future__ import annotations

from types import SimpleNamespace

from backend.app.services import google_drive_service
from backend.app.services.playback_diagnostics import ffmpeg_observer, provider_observer, runtime
from backend.app.services.playback_diagnostics.host_sampler import (
    cpu_percent_between,
    parse_meminfo,
    parse_pressure,
    parse_process_io,
    parse_process_stat,
    parse_proc_stat,
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


def test_provider_observer_failure_preserves_existing_stream_bytes(monkeypatch):
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


def test_ffmpeg_observer_failure_never_escapes_process_hook(monkeypatch):
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
