from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fileio import atomic_write_bytes, ensure_private_directory, resolve_beneath


class OptionalParquetDependencyError(RuntimeError):
    """Raised when local Parquet export dependencies are unavailable."""


def _flatten_event(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    row = {key: value for key, value in event.items() if key != "payload"}
    for key, value in payload.items():
        if isinstance(value, dict | list):
            row[f"payload.{key}"] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            row[f"payload.{key}"] = value
    return row


def _safe_export_name(session_id: str, format_name: str) -> str:
    safe_id = str(session_id)
    if not safe_id or not safe_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid playback diagnostics session id")
    suffix = {
        "ndjson": "ndjson",
        "csv": "csv",
        "parquet": "parquet",
        "perfetto": "trace.json",
    }[format_name]
    return f"{safe_id}.{suffix}"


def export_events(
    root: Path,
    *,
    session_id: str,
    events: list[dict[str, Any]],
    format_name: str,
) -> Path:
    normalized = format_name.strip().lower()
    if normalized not in {"ndjson", "csv", "parquet", "perfetto"}:
        raise ValueError("Export format must be ndjson, csv, parquet, or perfetto")
    export_root = ensure_private_directory(resolve_beneath(root, "exports"))
    destination = resolve_beneath(export_root, _safe_export_name(session_id, normalized))
    rows = [_flatten_event(event) for event in events]

    if normalized == "ndjson":
        payload = b"\n".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True).encode("utf-8")
            for event in events
        ) + (b"\n" if events else b"")
        atomic_write_bytes(destination, payload)
        return destination

    if normalized == "csv":
        import io

        fields = sorted({field for row in rows for field in row})
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        atomic_write_bytes(destination, buffer.getvalue().encode("utf-8"))
        return destination

    if normalized == "perfetto":
        trace_events: list[dict[str, Any]] = []
        for event in events:
            aligned_ns = int(str(event.get("aligned_wall_time_ns") or "0"))
            trace_events.append(
                {
                    "name": event.get("event_name"),
                    "cat": event.get("event_source"),
                    "ph": "i",
                    "s": "t",
                    "ts": aligned_ns / 1_000,
                    "pid": "elvern",
                    "tid": event.get("event_source"),
                    "args": _flatten_event(event),
                }
            )
        atomic_write_bytes(
            destination,
            json.dumps(
                {
                    "traceEvents": trace_events,
                    "displayTimeUnit": "ms",
                    "metadata": {
                        "schema_version": "playback-diagnostics-perfetto-v1",
                        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
        return destination

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise OptionalParquetDependencyError(
            "Parquet export is optional. Install the pinned local diagnostics export "
            "requirements from backend/requirements-diagnostics-export.txt."
        ) from exc
    table = pa.Table.from_pylist(rows)
    temporary = destination.with_name(f".{destination.name}.tmp")
    pq.write_table(table, temporary, compression="zstd")
    temporary.chmod(0o600)
    temporary.replace(destination)
    destination.chmod(0o600)
    return destination
