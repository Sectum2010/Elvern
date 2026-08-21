#!/usr/bin/env python3
"""Run accelerated synthetic benchmarks for the local playback recorder.

The benchmark writes only synthetic events to a temporary, ignored project
directory. It does not start Elvern, read media, or contact an external service.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import resource
import statistics
import sys
import time
import tracemalloc
import zlib
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.services.playback_diagnostics.capacity import (
    DiagnosticsCapacityGuard,
    directory_size_bytes,
)
from backend.app.services.playback_diagnostics.catalog import DiagnosticsCatalog
from backend.app.services.playback_diagnostics.constants import (
    DIAGNOSTICS_HARD_CAP_BYTES,
    DIAGNOSTICS_NORMAL_BUDGET_BYTES,
    SCHEMA_VERSION,
)
from backend.app.services.playback_diagnostics.crypto import DiagnosticsKeyStore
from backend.app.services.playback_diagnostics.fileio import (
    atomic_write_json,
    ensure_private_directory,
    resolve_beneath,
)
from backend.app.services.playback_diagnostics.journal import verify_journal
from backend.app.services.playback_diagnostics.privacy import sanitize_event
from backend.app.services.playback_diagnostics.schema import PlaybackDiagnosticEvent
from backend.app.services.playback_diagnostics.session_files import (
    read_session_events,
    write_manifest,
)
from backend.app.services.playback_diagnostics.summaries import write_summary_files
from backend.app.services.playback_diagnostics.writer import (
    DiagnosticsWriteBatch,
    DiagnosticsWriter,
)


DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "tmp" / "playback-diagnostics-benchmark"
SYNTHETIC_MINUTES = 30
BATCH_SIZE = 256


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "mean_ms": statistics.fmean(values) if values else None,
        "p50_ms": _percentile(values, 0.5),
        "p95_ms": _percentile(values, 0.95),
        "max_ms": max(values) if values else None,
    }


def _base_payload(sequence: int) -> dict[str, Any]:
    return {
        "buffered_ahead_ms": 45_000 - (sequence % 10) * 250,
        "buffered_behind_ms": 10_000,
        "total_buffered_ms": 55_000,
        "buffer_hole_count": 0,
        "buffer_slope_ms_per_s": 1_000,
        "playhead_slope_ms_per_s": 1_000,
        "ready_state": 4,
        "network_state": 1,
        "cpu_percent": 42.5,
        "memory_available_bytes": 16_000_000_000,
        "source_rate_bps": 24_000_000,
        "client_throughput_bps": 28_000_000,
        "output_bitrate_bps": 12_000_000,
        "state": "playing",
    }


def _event(
    sequence: int,
    *,
    source: str,
    source_sequence: int,
    name: str,
    payload: dict[str, Any],
    incident_id: str | None = None,
) -> dict[str, Any]:
    now_ns = 1_800_000_000_000_000_000 + sequence * 1_000_000
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": f"event-synthetic-{sequence:010d}",
        "event_name": name,
        "event_source": source,
        "severity": "info",
        "priority": "high" if incident_id else "normal",
        "playback_session_id": "session-synthetic-benchmark-0001",
        "playback_attempt_id": "attempt-synthetic-benchmark-0001",
        "attachment_id": "attachment-synthetic-benchmark-0001",
        "epoch_id": "epoch-synthetic-benchmark-0001",
        "worker_id": "worker-synthetic-benchmark-0001" if source in {"host", "ffmpeg"} else None,
        "incident_id": incident_id,
        "decision_id": None,
        "trace_id": None,
        "span_id": None,
        "parent_span_id": None,
        "event_sequence": sequence,
        "source_sequence": source_sequence,
        "client_wall_time_ms": now_ns / 1_000_000 if source == "client" else None,
        "client_monotonic_time_us": sequence * 1_000 if source == "client" else None,
        "client_time_origin_ms": 1_800_000_000_000 if source == "client" else None,
        "client_timer_resolution_us": 5 if source == "client" else None,
        "server_wall_time_ns": str(now_ns) if source != "client" else None,
        "server_monotonic_time_ns": str(sequence * 1_000_000),
        "server_received_wall_time_ns": str(now_ns),
        "server_received_monotonic_time_ns": str(sequence * 1_000_000),
        "aligned_wall_time_ns": str(now_ns),
        "clock_offset_ns": "1000000" if source == "client" else "0",
        "clock_uncertainty_ns": "2000000" if source == "client" else "0",
        "network_rtt_ns": "4000000" if source == "client" else None,
        "playhead_ms": sequence * 1_000,
        "media_element_time_ms": sequence * 1_000 if source == "client" else None,
        "duration_ms": 7_200_000,
        "platform": "synthetic",
        "device_class": "desktop",
        "browser_family": "chromium" if source == "client" else None,
        "browser_version": "synthetic",
        "os_family": "linux",
        "os_version": "synthetic",
        "hls_engine": "hls.js",
        "playback_mode": "lite",
        "stream_mode": "route2",
        "source_kind": "local",
        "observation_kind": "measured_client" if source == "client" else "measured_server",
        "measurement_method": "accelerated_synthetic_benchmark",
        "measurement_resolution": "modeled_1s_aggregate",
        "measurement_uncertainty": "synthetic_not_real_device",
        "sample_window_ms": 1_000,
        "capability_available": True,
        "unavailable_reason": None,
        "payload": payload,
    }


def _build_events(*, incident: bool) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    source_sequences: defaultdict[str, int] = defaultdict(int)

    def append(source: str, name: str, payload: dict[str, Any], incident_id=None) -> None:
        source_sequences[source] += 1
        events.append(
            _event(
                len(events) + 1,
                source=source,
                source_sequence=source_sequences[source],
                name=name,
                payload=payload,
                incident_id=incident_id,
            )
        )

    seconds = SYNTHETIC_MINUTES * 60
    for second in range(seconds):
        append("client", "media_aggregate", _base_payload(second + 1))
        append("host", "host_aggregate", _base_payload(second + 1))
        if second % 5 == 0:
            append(
                "host",
                "gpu_aggregate",
                {
                    "gpu_utilization_percent": 18.0,
                    "gpu_encoder_percent": 12.0,
                    "gpu_decoder_percent": 3.0,
                    "gpu_memory_bytes": 1_200_000_000,
                    "gpu_temperature_c": 54.0,
                },
            )
        if second % 30 == 0:
            append("server", "http_response_completed", {
                "route_template": "/api/browser-playback/:scope/segments/:segment",
                "segment_index": second // 30,
                "http_status": 200,
                "bytes": 4_000_000,
                "request_duration_ms": 18.0,
            })

    if incident:
        incident_id = "incident-synthetic-benchmark-0001"
        samples = [
            {
                "playhead_ms": index * 250,
                "buffered_ahead_ms": max(0, 15_000 - index * 65),
                "ready_state": 2,
                "network_state": 2,
            }
            for index in range(240)
        ]
        for start in range(0, len(samples), 64):
            append(
                "client",
                "client_incident_pre_samples",
                {"samples": samples[start : start + 64], "state": "pre_window"},
                incident_id,
            )
        frames = [
            {
                "media_time_ms": index * (1_000 / 60),
                "expected_display_time_ms": index * (1_000 / 60),
                "presented_frames": index,
                "processing_duration_ms": 1.2,
            }
            for index in range(3_600)
        ]
        for start in range(0, len(frames), 128):
            append(
                "client",
                "client_incident_pre_frames",
                {"frames": frames[start : start + 128], "state": "pre_window"},
                incident_id,
            )
        for index in range(120):
            append("host", "host_incident_pre_sample", _base_payload(index + 1), incident_id)
        for index in range(480):
            append("client", "client_incident_sample", _base_payload(index + 1), incident_id)
        for index in range(240):
            append("host", "host_incident_post_sample", _base_payload(index + 1), incident_id)
        append("client", "stall_confirmed", {"state": "confirmed"}, incident_id)
        append("client", "stall_ended", {"stall_duration_ms": 4_250}, incident_id)
    return events


def _metadata(relative_path: str) -> dict[str, Any]:
    return {
        "schema_version": "playback-diagnostics-session-v1",
        "diagnostics_event_schema": SCHEMA_VERSION,
        "playback_session_id": "session-synthetic-benchmark-0001",
        "owner_hash": "owner-synthetic-benchmark",
        "subject_id": "subject-synthetic-benchmark",
        "media_item_id": 1,
        "source_original_filename": "Synthetic Benchmark Movie.mkv",
        "source_filename_sha256": "a" * 64,
        "source_fingerprint": "b" * 64,
        "source_kind": "local",
        "source_size_bytes": 24_000_000_000,
        "duration_ms": 7_200_000,
        "container": "matroska",
        "video_codec": "hevc",
        "audio_codec": "eac3",
        "width": 3_840,
        "height": 2_160,
        "pixel_format": "yuv420p10le",
        "bit_depth": 10,
        "hdr": True,
        "dolby_vision": False,
        "audio_channels": 8,
        "selected_audio_stream_index": 1,
        "profile": "lite",
        "playback_mode": "lite",
        "stream_mode": "route2",
        "platform": "synthetic",
        "device_class": "desktop",
        "browser_family": "chromium",
        "browser_version": "synthetic",
        "os_family": "linux",
        "os_version": "synthetic",
        "hls_engine": "hls.js",
        "capabilities": {"synthetic": True},
        "elvern_commit": "synthetic",
        "ffmpeg_version": "synthetic",
        "config_fingerprint": "c" * 64,
        "state": "active",
        "created_at_utc": "2026-08-20T00:00:00+00:00",
        "updated_at_utc": "2026-08-20T00:00:00+00:00",
        "session_relative_path": relative_path,
    }


def _run_writer_scenario(output_root: Path, *, incident: bool) -> dict[str, Any]:
    name = "incident" if incident else "no_incident"
    root = ensure_private_directory(output_root / name / "diagnostics")
    key_store = DiagnosticsKeyStore(root / "keys")
    active_key = key_store.load_or_create_active_key()
    catalog = DiagnosticsCatalog(root)
    capacity = DiagnosticsCapacityGuard(root, minimum_free_bytes=1)
    baseline_bytes = directory_size_bytes(root)
    relative_path = "sessions/2026/08/20/session-synthetic-benchmark-0001"
    session_path = ensure_private_directory(resolve_beneath(root, relative_path))
    ensure_private_directory(resolve_beneath(session_path, "raw"))
    metadata = _metadata(relative_path)
    atomic_write_json(resolve_beneath(session_path, "session.json"), metadata)
    catalog.upsert_session(metadata)

    events = _build_events(incident=incident)
    by_source: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_source[str(event["event_source"])].append(event)
    source_ids: dict[str, str] = {}
    for source in by_source:
        source_id = f"{source}-synthetic-benchmark"
        source_ids[source] = source_id
        catalog.register_source(
            playback_session_id=metadata["playback_session_id"],
            source_id=source_id,
            source_type=source,
        )

    writer = DiagnosticsWriter(
        root,
        catalog=catalog,
        capacity=capacity,
        key_store=key_store,
        active_key=active_key,
    )
    validation_latencies: list[float] = []
    for event in events[: min(1_000, len(events))]:
        started = time.perf_counter_ns()
        validated = PlaybackDiagnosticEvent.model_validate(event).model_dump(mode="json")
        sanitize_event(validated)
        validation_latencies.append((time.perf_counter_ns() - started) / 1_000_000)

    tracemalloc.start()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    process_cpu_before = time.process_time()
    wall_before = time.perf_counter()
    enqueue_latencies: list[float] = []
    writer.start()
    try:
        for source, source_events in by_source.items():
            for start in range(0, len(source_events), BATCH_SIZE):
                batch_events = tuple(source_events[start : start + BATCH_SIZE])
                enqueued_ns = time.monotonic_ns()
                batch = DiagnosticsWriteBatch(
                    playback_session_id=metadata["playback_session_id"],
                    source_id=source_ids[source],
                    source_type=source,
                    session_relative_path=relative_path,
                    events=batch_events,
                    enqueued_monotonic_ns=enqueued_ns,
                )
                enqueue_started = time.perf_counter_ns()
                result = writer.enqueue(batch)
                enqueue_latencies.append((time.perf_counter_ns() - enqueue_started) / 1_000_000)
                if result.accepted != len(batch_events):
                    raise RuntimeError("Synthetic writer benchmark dropped a batch")
        if not writer.flush(timeout=120):
            raise RuntimeError("Synthetic diagnostics writer did not flush")
    finally:
        writer.shutdown(timeout=10)
    wall_seconds = time.perf_counter() - wall_before
    process_cpu_seconds = time.process_time() - process_cpu_before
    _current, peak_tracemalloc = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    stored_events, journal_reports = read_session_events(root, relative_path, key_store)
    source_stats = catalog.source_stats(metadata["playback_session_id"])
    write_summary_files(
        root,
        relative_path,
        metadata=metadata,
        events=stored_events,
        source_stats=source_stats,
        writer_metrics=writer.metrics(),
        capacity_state=capacity.refresh().state,
    )
    write_manifest(root, relative_path, journal_reports=journal_reports)
    final_bytes = directory_size_bytes(root)
    session_directory_bytes = directory_size_bytes(session_path)
    per_session_growth = max(1, final_bytes - baseline_bytes)

    return {
        "scenario": name,
        "benchmark_kind": "accelerated_synthetic_server",
        "modeled_session_minutes": SYNTHETIC_MINUTES,
        "event_count": len(events),
        "stored_event_count": len(stored_events),
        "batch_count": int(writer.metrics()["batches_written"]),
        "validation_and_sanitize": _summary(validation_latencies),
        "request_path_enqueue": _summary(enqueue_latencies),
        "writer": {
            **writer.metrics(),
            "wall_seconds": wall_seconds,
            "process_cpu_seconds": process_cpu_seconds,
            "cpu_to_wall_ratio": process_cpu_seconds / wall_seconds if wall_seconds else None,
            "throughput_events_per_second": len(stored_events) / wall_seconds if wall_seconds else None,
            "peak_tracemalloc_bytes": peak_tracemalloc,
            "max_rss_before_kib": rss_before,
            "max_rss_after_kib": rss_after,
        },
        "storage": {
            "cold_root_total_bytes": final_bytes,
            "session_directory_bytes": session_directory_bytes,
            "modeled_per_session_growth_bytes": per_session_growth,
            "average_disk_growth_bytes_per_hour": per_session_growth * 2,
            "projected_2h_bytes": per_session_growth * 4,
            "estimated_sessions_at_normal_budget": DIAGNOSTICS_NORMAL_BUDGET_BYTES
            // per_session_growth,
            "estimated_sessions_at_80gb_hard_cap": DIAGNOSTICS_HARD_CAP_BYTES
            // per_session_growth,
        },
        "journal_verification": journal_reports,
        "limitations": [
            "Accelerated synthetic event generation; not a wall-clock 30-minute playback.",
            "Local Linux process measurement; not a real Mac, iPhone, provider, or tailnet run.",
            "Per-session capacity is a linear projection from one synthetic session.",
        ],
    }


def _component_microbench(output_root: Path) -> dict[str, Any]:
    event = _event(
        1,
        source="client",
        source_sequence=1,
        name="media_aggregate",
        payload=_base_payload(1),
    )
    payload = b"\n".join(
        json.dumps({**event, "source_sequence": index + 1}, sort_keys=True).encode("utf-8")
        for index in range(BATCH_SIZE)
    )
    timings: dict[str, list[float]] = defaultdict(list)
    compressed = b""
    ciphertext = b""
    key = AESGCM.generate_key(bit_length=256)
    aes = AESGCM(key)
    for index in range(25):
        started = time.perf_counter_ns()
        json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        timings["serialization_ms"].append((time.perf_counter_ns() - started) / 1_000_000)

        started = time.perf_counter_ns()
        compressed = zlib.compress(payload, level=6)
        timings["compression_ms"].append((time.perf_counter_ns() - started) / 1_000_000)

        started = time.perf_counter_ns()
        ciphertext = aes.encrypt(index.to_bytes(12, "big"), compressed, b"synthetic")
        timings["encryption_ms"].append((time.perf_counter_ns() - started) / 1_000_000)

    fsync_path = output_root / "fsync-synthetic.bin"
    fsync_path.parent.mkdir(parents=True, exist_ok=True)
    for _index in range(10):
        started = time.perf_counter_ns()
        with fsync_path.open("wb") as handle:
            handle.write(ciphertext)
            handle.flush()
            os.fsync(handle.fileno())
        timings["fsync_ms"].append((time.perf_counter_ns() - started) / 1_000_000)
    fsync_path.unlink(missing_ok=True)
    return {
        "batch_events": BATCH_SIZE,
        "plaintext_bytes": len(payload),
        "compressed_bytes": len(compressed),
        "ciphertext_bytes": len(ciphertext),
        **{name: _summary(values) for name, values in timings.items()},
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    client = report.get("client") or {}
    scenarios = report["server_scenarios"]
    lines = [
        "# Playback Diagnostics Synthetic Benchmark",
        "",
        f"Measured at: `{report['measured_at_utc']}`",
        "",
        "> These are accelerated synthetic local measurements. They are not real Mac, iPhone, provider, or tailnet playback results.",
        "",
        "## Client",
        "",
    ]
    if client:
        lines.extend(
            [
                f"- Modeled events: `{client.get('synthetic_event_count')}` over `{client.get('modeled_session_minutes')}` minutes.",
                f"- Event creation p95: `{client.get('main_thread_event_creation', {}).get('p95_ms')}` ms.",
                f"- Serialization p95: `{client.get('serialization', {}).get('p95_ms')}` ms.",
                f"- IndexedDB enqueue p95: `{client.get('indexeddb', {}).get('enqueue', {}).get('p95_ms')}` ms.",
                f"- Loopback batch upload p95: `{client.get('loopback_batch_upload', {}).get('p95_ms')}` ms.",
                f"- Ring buffer modeled bytes: `{client.get('ring_buffers', {}).get('total_bytes')}`.",
            ]
        )
    else:
        lines.append("- Client report was not available; run the frontend benchmark first.")
    lines.extend(["", "## Server", ""])
    for scenario in scenarios:
        storage = scenario["storage"]
        writer = scenario["writer"]
        lines.extend(
            [
                f"### {scenario['scenario']}",
                "",
                f"- Events: `{scenario['event_count']}`; batches: `{scenario['batch_count']}`.",
                f"- Writer throughput: `{writer['throughput_events_per_second']:.2f}` events/s.",
                f"- Writer CPU/wall ratio: `{writer['cpu_to_wall_ratio']:.4f}`.",
                f"- Modeled session growth: `{storage['modeled_per_session_growth_bytes']}` bytes.",
                f"- Average hourly growth: `{storage['average_disk_growth_bytes_per_hour']}` bytes.",
                f"- Projected 2-hour growth: `{storage['projected_2h_bytes']}` bytes.",
                f"- Estimated sessions at 80GB: `{storage['estimated_sessions_at_80gb_hard_cap']}`.",
                "",
            ]
        )
    lines.extend(
        [
            "## Interpretation",
            "",
            "- Request-path work is validation, sanitization, and non-blocking queue insertion; journal compression, encryption, catalog writes, and fsync occur in the dedicated writer.",
            "- The capacity projections are linear estimates from synthetic payloads, not guarantees for real sessions.",
            "- The recorder never deletes old sessions when capacity is reached.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--client-report",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT / "client.json",
    )
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    for child in (output_root / "no_incident", output_root / "incident"):
        if child.exists():
            import shutil

            shutil.rmtree(child)

    client = None
    if args.client_report.is_file():
        client = json.loads(args.client_report.read_text(encoding="utf-8"))
    report = {
        "schema_version": "playback-diagnostics-benchmark-v1",
        "measured_at_utc": _utc_now(),
        "synthetic": True,
        "client": client,
        "server_components": _component_microbench(output_root),
        "server_scenarios": [
            _run_writer_scenario(output_root, incident=False),
            _run_writer_scenario(output_root, incident=True),
        ],
    }
    json_path = output_root / "benchmark.json"
    markdown_path = output_root / "benchmark.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    json_path.chmod(0o600)
    _write_markdown(markdown_path, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
