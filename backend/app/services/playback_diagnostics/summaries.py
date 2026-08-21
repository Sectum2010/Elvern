from __future__ import annotations

import csv
import io
import json
import math
import os
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .fileio import FILE_MODE, atomic_write_bytes, encode_json_document, resolve_beneath
from .privacy import markdown_inline_code, spreadsheet_safe_cell


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite_numbers(events: Iterable[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for event in events:
        candidate = event.get("payload", {}).get(key)
        if candidate is None:
            candidate = event.get(key)
        try:
            number = float(candidate)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            values.append(number)
    return values


def _percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p5": None, "p50": None, "p95": None}
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return {
        "p5": round(percentile(0.05), 3),
        "p50": round(percentile(0.5), 3),
        "p95": round(percentile(0.95), 3),
    }


def _first_event_ns(events: list[dict[str, Any]], name: str) -> int | None:
    for event in events:
        if event.get("event_name") == name:
            try:
                return int(str(event.get("aligned_wall_time_ns") or "0"))
            except ValueError:
                return None
    return None


def build_summary(
    metadata: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    source_stats: list[dict[str, Any]],
    writer_metrics: dict[str, Any],
    capacity_state: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    names = Counter(str(event.get("event_name") or "unknown") for event in events)
    sources = Counter(str(event.get("event_source") or "unknown") for event in events)
    session_created_ns = _first_event_ns(events, "session_created")
    play_intent_ns = _first_event_ns(events, "play_intent")
    first_frame_ns = _first_event_ns(events, "first_video_frame_presented")
    inferred_first_frame_ns = _first_event_ns(events, "first_video_frame_inferred")
    startup_delay_ms = None
    if first_frame_ns is not None and (play_intent_ns or session_created_ns) is not None:
        startup_delay_ms = max(
            0.0,
            (first_frame_ns - int(play_intent_ns or session_created_ns or 0)) / 1_000_000,
        )

    stall_durations = _finite_numbers(
        [event for event in events if event.get("event_name") == "stall_ended"],
        "stall_duration_ms",
    )
    recovery_durations = _finite_numbers(
        [event for event in events if event.get("event_name") in {"playhead_progress_resumed", "recovery_completed"}],
        "actual_duration_ms",
    )
    sequence_gaps = sum(int(source.get("missing_sequence_count") or 0) for source in source_stats)
    missing_ranges = {
        str(source.get("source_id") or "unknown"): list(source.get("missing_ranges") or [])
        for source in source_stats
        if source.get("missing_ranges")
    }
    unsupported = sorted(
        {
            str(event.get("unavailable_reason"))
            for event in events
            if event.get("observation_kind") == "unsupported" and event.get("unavailable_reason")
        }
    )
    clock_uncertainties = [
        int(str(event["clock_uncertainty_ns"]))
        for event in events
        if event.get("clock_uncertainty_ns") is not None
        and str(event["clock_uncertainty_ns"]).lstrip("-").isdigit()
    ]
    expected_sources = {"client", "server"}
    present_sources = set(sources)
    required_sources_present = expected_sources.issubset(present_sources)
    all_sources_final = bool(source_stats) and all(
        source.get("final_source_sequence") is not None for source in source_stats
    )
    all_sources_acked = all(
        int(source.get("ack_watermark") or 0)
        >= int(source.get("final_source_sequence") or source.get("max_seen_sequence") or 0)
        for source in source_stats
    )
    dropped_event_count = int(writer_metrics.get("events_dropped") or 0)
    lifecycle_state = str(metadata.get("state") or "unknown")
    completeness_assessment = (
        "complete"
        if lifecycle_state == "sealed"
        and required_sources_present
        and all_sources_final
        and all_sources_acked
        and sequence_gaps == 0
        and dropped_event_count == 0
        else "incomplete"
    )
    clock_quality_score = 0.0
    if clock_uncertainties:
        median_uncertainty_ms = statistics.median(clock_uncertainties) / 1_000_000
        clock_quality_score = round(max(0.0, 100.0 - min(100.0, median_uncertainty_ms)), 1)
    capabilities = metadata.get("capabilities") if isinstance(metadata.get("capabilities"), dict) else {}
    supported_capability_states = {"api_detected", "server_collected", "detected_not_collected"}
    known_capability_states = supported_capability_states | {"api_absent", "not_applicable"}
    supported_count = sum(
        value is True or value in supported_capability_states
        for value in capabilities.values()
    )
    known_count = sum(
        isinstance(value, bool) or value in known_capability_states
        for value in capabilities.values()
    )
    platform_capability_score = round((supported_count / known_count) * 100, 1) if known_count else 0.0

    summary = {
        "schema_version": "playback-diagnostics-summary-v2",
        "generated_at_utc": _utc_now(),
        "identity": {
            "source_original_filename": metadata.get("source_original_filename"),
            "media_item_id": metadata.get("media_item_id"),
            "playback_session_id": metadata.get("playback_session_id"),
            "subject_id": metadata.get("subject_id"),
            "source_kind": metadata.get("source_kind"),
            "playback_mode": metadata.get("playback_mode"),
            "platform": metadata.get("platform"),
            "browser_family": metadata.get("browser_family"),
            "hls_engine": metadata.get("hls_engine"),
            "elvern_commit": metadata.get("elvern_commit"),
            "ffmpeg_version": metadata.get("ffmpeg_version"),
            "diagnostics_schema": metadata.get("diagnostics_event_schema"),
        },
        "movie_complexity": {
            "size_bytes": metadata.get("source_size_bytes"),
            "duration_ms": metadata.get("duration_ms"),
            "container": metadata.get("container"),
            "video_codec": metadata.get("video_codec"),
            "audio_codec": metadata.get("audio_codec"),
            "width": metadata.get("width"),
            "height": metadata.get("height"),
            "pixel_format": metadata.get("pixel_format"),
            "bit_depth": metadata.get("bit_depth"),
            "hdr": metadata.get("hdr"),
            "dolby_vision": metadata.get("dolby_vision"),
            "source_throughput_bps": _percentiles(_finite_numbers(events, "source_rate_bps")),
            "output_bitrate_bps": _percentiles(_finite_numbers(events, "output_bitrate_bps")),
            "ffmpeg_speed_x": _percentiles(_finite_numbers(events, "speed_x")),
            "peak_worker_rss_bytes": max(_finite_numbers(events, "memory_rss_bytes"), default=None),
            "segment_size_bytes": _percentiles(_finite_numbers(events, "segment_bytes")),
            "publish_latency_ms": _percentiles(_finite_numbers(events, "publish_latency_ms")),
        },
        "qoe": {
            "startup_delay_ms": round(startup_delay_ms, 3) if startup_delay_ms is not None else None,
            "first_presented_frame_observed": first_frame_ns is not None,
            "inferred_first_frame_observed": inferred_first_frame_ns is not None,
            "stall_count": names["stall_confirmed"],
            "total_stalled_ms": round(sum(stall_durations), 3),
            "longest_stall_ms": round(max(stall_durations), 3) if stall_durations else None,
            "recovery_action_count": names["recovery_action_applied"],
            "passive_progress_resume_count": names["playhead_progress_resumed"],
            "recovery_duration_ms": _percentiles(recovery_durations),
            "seek_count": names["seek_intent"],
            "pause_count": names["pause_started"],
            "completed": bool(names["completed"]),
            "quit": bool(names["quit"]),
        },
        "client": {
            "buffered_ahead_ms": _percentiles(_finite_numbers(events, "buffered_ahead_ms")),
            "minimum_buffer_ms": min(_finite_numbers(events, "buffered_ahead_ms"), default=None),
            "client_throughput_bps": _percentiles(_finite_numbers(events, "client_throughput_bps")),
            "window_dropped_frame_ratio": _percentiles(
                _finite_numbers(events, "window_dropped_frame_ratio")
            ),
            "dropped_frame_delta": {
                "dropped": int(sum(_finite_numbers(events, "delta_dropped_frames"))),
                "total": int(sum(_finite_numbers(events, "delta_total_frames"))),
            },
            "long_task_ms": _percentiles(_finite_numbers(events, "long_task_ms")),
            "capabilities": capabilities,
        },
        "server": {
            "upstream_active_read_throughput_bps": _percentiles(
                _finite_numbers(events, "upstream_active_read_throughput_bps")
            ),
            "end_to_end_delivery_rate_bps": _percentiles(
                _finite_numbers(events, "end_to_end_delivery_rate_bps")
            ),
            "consumer_backpressure_ms": _percentiles(
                _finite_numbers(events, "consumer_backpressure_ms")
            ),
            "host_cpu_percent": _percentiles(_finite_numbers(events, "cpu_percent")),
            "host_memory_available_bytes": _percentiles(_finite_numbers(events, "memory_available_bytes")),
            "atc_evaluations": names["atc_evaluation_started"],
            "atc_actions": names["atc_action_applied"],
            "eta_predictions": names["eta_prediction"],
            "eta_resolutions": names["eta_resolved"],
            "eta_superseded": names["eta_prediction_superseded"],
        },
        "diagnostics_quality": {
            "telemetry_completeness_score": None,
            "completeness_assessment": completeness_assessment,
            "lifecycle_state": lifecycle_state,
            "expected_event_sources": sorted(expected_sources),
            "required_sources_present": required_sources_present,
            "all_sources_final_declared": all_sources_final,
            "all_source_watermarks_complete": all_sources_acked,
            "missing_sequence_ranges": missing_ranges,
            "clock_calibration_sample_count": names["clock_synchronized"],
            "host_evidence_state": "observed" if "host" in present_sources else "not_observed",
            "incident_window_complete_count": names["post_recovery_observation"],
            "clock_quality_score": clock_quality_score,
            "platform_capability_score": platform_capability_score,
            "unsupported_fields": unsupported,
            "dropped_event_count": dropped_event_count,
            "sequence_gap_count": sequence_gaps,
            "duplicate_event_count": sum(int(source.get("duplicate_count") or 0) for source in source_stats),
            "out_of_order_event_count": sum(int(source.get("out_of_order_count") or 0) for source in source_stats),
            "capacity_state": capacity_state,
            "event_count": len(events),
            "event_sources": dict(sources),
        },
    }
    completeness = {
        "schema_version": "playback-diagnostics-completeness-v2",
        "generated_at_utc": summary["generated_at_utc"],
        **summary["diagnostics_quality"],
        "source_stats": source_stats,
        "writer_overhead": writer_metrics,
    }
    return summary, completeness


def build_timeline_csv(events: list[dict[str, Any]]) -> bytes:
    rows: list[list[Any]] = []
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        rows.append(
            [
                spreadsheet_safe_cell(value)
                for value in (
                event.get("aligned_wall_time_ns"),
                event.get("event_name"),
                event.get("event_source"),
                event.get("observation_kind"),
                event.get("source_sequence"),
                event.get("playback_attempt_id"),
                event.get("attachment_id"),
                event.get("epoch_id"),
                event.get("incident_id"),
                event.get("decision_id"),
                event.get("playhead_ms"),
                payload.get("buffered_ahead_ms"),
                payload.get("source_rate_bps"),
                payload.get("client_throughput_bps"),
                payload.get("cpu_percent"),
                payload.get("memory_available_bytes"),
                payload.get("state"),
                )
            ]
        )
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "aligned_wall_time_ns",
            "event_name",
            "event_source",
            "observation_kind",
            "source_sequence",
            "playback_attempt_id",
            "attachment_id",
            "epoch_id",
            "incident_id",
            "decision_id",
            "playhead_ms",
            "buffered_ahead_ms",
            "source_rate_bps",
            "client_throughput_bps",
            "host_cpu_percent",
            "host_memory_available_bytes",
            "state",
        ]
    )
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def write_timeline_csv(path: Path, events: list[dict[str, Any]]) -> None:
    atomic_write_bytes(path, build_timeline_csv(events))


def build_summary_artifacts(
    metadata: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    source_stats: list[dict[str, Any]],
    writer_metrics: dict[str, Any],
    capacity_state: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, bytes]]:
    summary, completeness = build_summary(
        metadata,
        events,
        source_stats=source_stats,
        writer_metrics=writer_metrics,
        capacity_state=capacity_state,
    )


    identity = summary["identity"]
    qoe = summary["qoe"]
    quality = summary["diagnostics_quality"]
    unsupported = quality["unsupported_fields"] or ["None reported"]
    markdown = "\n".join(
        [
            "# Playback Diagnostic Session",
            "",
            "## Identity",
            f"- Movie file: {markdown_inline_code(identity['source_original_filename'])}",
            f"- Media item: {markdown_inline_code(identity['media_item_id'])}",
            f"- Session: {markdown_inline_code(identity['playback_session_id'])}",
            f"- Source: {markdown_inline_code(identity['source_kind'])}",
            f"- Mode: {markdown_inline_code(identity['playback_mode'])}",
            "- Platform: "
            f"{markdown_inline_code(identity['platform'])} / "
            f"{markdown_inline_code(identity['browser_family'])} / "
            f"{markdown_inline_code(identity['hls_engine'])}",
            "",
            "## QoE",
            f"- Startup delay: `{qoe['startup_delay_ms']}` ms",
            f"- Stalls: `{qoe['stall_count']}`",
            f"- Total stalled: `{qoe['total_stalled_ms']}` ms",
            f"- Longest stall: `{qoe['longest_stall_ms']}` ms",
            f"- Recovery actions: `{qoe['recovery_action_count']}`",
            f"- Passive progress resumes: `{qoe['passive_progress_resume_count']}`",
            f"- Completed: `{qoe['completed']}`; quit: `{qoe['quit']}`",
            "",
            "## Host, Source, FFmpeg, and ATC",
            "See `summary.json` and `timeline.csv` for measured distributions and correlated events.",
            "",
            "## Diagnostics Quality",
            f"- Completeness assessment: `{quality['completeness_assessment']}`",
            f"- Clock quality: `{quality['clock_quality_score']}`",
            f"- Platform capability: `{quality['platform_capability_score']}`",
            f"- Dropped events: `{quality['dropped_event_count']}`",
            f"- Sequence gaps: `{quality['sequence_gap_count']}`",
            f"- Capacity state: `{quality['capacity_state']}`",
            "",
            "## Unsupported or unavailable evidence",
            *(f"- {item}" for item in unsupported),
            "",
            "A missing measurement is not evidence that no fault occurred.",
            "",
        ]
    )
    artifacts = {
        "summary.json": encode_json_document(summary),
        "completeness.json": encode_json_document(completeness),
        "timeline.csv": build_timeline_csv(events),
        "summary.md": markdown.encode("utf-8"),
    }
    return summary, completeness, artifacts


def write_summary_files(
    root: Path,
    session_relative_path: str,
    *,
    metadata: dict[str, Any],
    events: list[dict[str, Any]],
    source_stats: list[dict[str, Any]],
    writer_metrics: dict[str, Any],
    capacity_state: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    session_path = resolve_beneath(root, session_relative_path)
    summary, completeness, artifacts = build_summary_artifacts(
        metadata,
        events,
        source_stats=source_stats,
        writer_metrics=writer_metrics,
        capacity_state=capacity_state,
    )
    for name, payload in artifacts.items():
        output_path = resolve_beneath(session_path, name)
        atomic_write_bytes(output_path, payload)
        os.chmod(output_path, FILE_MODE)
    return summary, completeness
