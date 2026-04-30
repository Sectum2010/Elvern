#!/usr/bin/env python3
"""Developer-only Route2 cloud source benchmark/probe.

This script measures Google Drive source range-read behavior for cloud media
items without creating Route2 sessions, touching production cache, or logging
OAuth tokens. It intentionally probes the provider source path separately from
the local ffmpeg thread benchmark so cloud/source bottlenecks do not get hidden
by a full-file local download.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


GOOGLE_DRIVE_FILES_ENDPOINT = "https://www.googleapis.com/drive/v3/files"
DEFAULT_RANGE_MIB = (8, 32)
DEFAULT_POSITIONS = ("start", "middle", "near_end")
CSV_FIELDS = (
    "media_item_id",
    "title",
    "original_filename",
    "file_size",
    "probe_label",
    "position",
    "range_mib",
    "range_header",
    "status",
    "accept_ranges",
    "content_range",
    "content_length",
    "content_type",
    "first_byte_seconds",
    "elapsed_seconds",
    "bytes_read",
    "mib_per_second",
    "success",
    "error_class",
    "error_detail",
)


class CloudBenchmarkError(RuntimeError):
    pass


def _load_env_file() -> None:
    env_path = REPO_ROOT / "deploy" / "env" / "elvern.env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def _redact(value: object) -> str:
    text = str(value or "")
    for marker in ("access_token=", "token=", "refresh_token=", "resourceKey="):
        start = text.find(marker)
        while start >= 0:
            value_start = start + len(marker)
            value_end = value_start
            while value_end < len(text) and text[value_end] not in {"&", " ", "\n", "\r", "\t"}:
                value_end += 1
            text = f"{text[:value_start]}<redacted>{text[value_end:]}"
            start = text.find(marker, value_start + len("<redacted>"))
    return text


def _parse_positive_ints(raw_values: list[str], *, field_name: str) -> list[int]:
    parsed: list[int] = []
    for raw in raw_values:
        for chunk in str(raw).split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            value = int(chunk)
            if value <= 0:
                raise ValueError(f"{field_name} values must be positive")
            parsed.append(value)
    return sorted(dict.fromkeys(parsed))


def _load_cloud_items(settings, *, media_item_ids: list[int], titles: list[str]) -> list[dict[str, object]]:
    from backend.app.db import get_connection

    if not media_item_ids and not titles:
        raise CloudBenchmarkError("Provide --media-item-id or --title.")

    rows_by_id: dict[int, dict[str, object]] = {}
    with get_connection(settings) as connection:
        for item_id in media_item_ids:
            row = connection.execute(
                """
                SELECT
                    m.id,
                    m.title,
                    m.original_filename,
                    m.file_path,
                    m.file_size,
                    COALESCE(m.source_kind, 'local') AS source_kind,
                    m.external_media_id,
                    m.cloud_resource_key,
                    m.library_source_id,
                    s.google_drive_account_id
                FROM media_items m
                LEFT JOIN library_sources s
                  ON s.id = m.library_source_id
                WHERE m.id = ?
                LIMIT 1
                """,
                (item_id,),
            ).fetchone()
            if row is None:
                raise CloudBenchmarkError(f"Media item {item_id} was not found.")
            rows_by_id[int(row["id"])] = dict(row)

        for title in titles:
            needle = str(title or "").strip()
            if not needle:
                continue
            exact_rows = connection.execute(
                """
                SELECT
                    m.id,
                    m.title,
                    m.original_filename,
                    m.file_path,
                    m.file_size,
                    COALESCE(m.source_kind, 'local') AS source_kind,
                    m.external_media_id,
                    m.cloud_resource_key,
                    m.library_source_id,
                    s.google_drive_account_id
                FROM media_items m
                LEFT JOIN library_sources s
                  ON s.id = m.library_source_id
                WHERE lower(m.title) = lower(?)
                   OR lower(m.original_filename) = lower(?)
                ORDER BY m.id
                """,
                (needle, needle),
            ).fetchall()
            matched_rows = exact_rows
            if not matched_rows:
                like_value = f"%{needle.lower()}%"
                matched_rows = connection.execute(
                    """
                    SELECT
                        m.id,
                        m.title,
                        m.original_filename,
                        m.file_path,
                        m.file_size,
                        COALESCE(m.source_kind, 'local') AS source_kind,
                        m.external_media_id,
                        m.cloud_resource_key,
                        m.library_source_id,
                        s.google_drive_account_id
                    FROM media_items m
                    LEFT JOIN library_sources s
                      ON s.id = m.library_source_id
                    WHERE lower(m.title) LIKE ?
                       OR lower(m.original_filename) LIKE ?
                    ORDER BY m.id
                    """,
                    (like_value, like_value),
                ).fetchall()
            if not matched_rows:
                raise CloudBenchmarkError(f"No media item matched title {needle!r}.")
            for row in matched_rows:
                rows_by_id[int(row["id"])] = dict(row)

    items = [rows_by_id[item_id] for item_id in sorted(rows_by_id)]
    for item in items:
        if str(item.get("source_kind") or "local") != "cloud":
            raise CloudBenchmarkError(f"Media item {item['id']} is not a cloud item.")
        if not item.get("external_media_id"):
            raise CloudBenchmarkError(f"Media item {item['id']} is missing external_media_id.")
        if not int(item.get("google_drive_account_id") or 0):
            raise CloudBenchmarkError(f"Media item {item['id']} is missing a Google Drive account binding.")
    return items


def _resolve_drive_target(settings, item: dict[str, object], *, fetch_resource_key: bool) -> dict[str, object]:
    from fastapi import HTTPException

    from backend.app.services.cloud_library_service import get_google_drive_account_access_token_by_account_id
    from backend.app.services.google_drive_service import fetch_drive_file_resource_key

    try:
        access_token = get_google_drive_account_access_token_by_account_id(
            settings,
            google_account_id=int(item["google_drive_account_id"]),
        )
    except HTTPException as exc:
        raise CloudBenchmarkError(f"provider_auth_required_or_unavailable: {_redact(exc.detail)}") from exc

    resource_key = str(item.get("cloud_resource_key") or "").strip() or None
    if not resource_key and fetch_resource_key:
        try:
            resource_key = fetch_drive_file_resource_key(
                access_token,
                file_id=str(item["external_media_id"]),
            )
        except HTTPException as exc:
            raise CloudBenchmarkError(f"resource_key_lookup_failed: {_redact(exc.detail)}") from exc

    return {
        "file_id": str(item["external_media_id"]),
        "resource_key_present": bool(resource_key),
        "resource_key": resource_key,
        "access_token": access_token,
    }


def _range_start(*, position: str, file_size: int, byte_count: int) -> int:
    if position == "start":
        return 0
    if position == "middle":
        return max(0, (file_size - byte_count) // 2)
    if position == "near_end":
        return max(0, file_size - byte_count)
    raise ValueError(f"Unsupported probe position: {position}")


def _drive_media_url(*, file_id: str, resource_key: str | None) -> str:
    query = {
        "alt": "media",
        "supportsAllDrives": "true",
    }
    if resource_key:
        query["resourceKey"] = resource_key
    return f"{GOOGLE_DRIVE_FILES_ENDPOINT}/{file_id}?{urlencode(query)}"


def _small_error_body(exc: HTTPError) -> str:
    try:
        payload = exc.read(2048).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""
    return _redact(payload.strip())


def _probe_range(
    *,
    access_token: str,
    file_id: str,
    resource_key: str | None,
    item: dict[str, object],
    position: str,
    range_mib: int,
    timeout_seconds: float,
    chunk_size: int,
) -> dict[str, object]:
    file_size = int(item.get("file_size") or 0)
    byte_count = min(range_mib * 1024 * 1024, file_size if file_size > 0 else range_mib * 1024 * 1024)
    start = _range_start(position=position, file_size=file_size, byte_count=byte_count)
    end = max(start, start + byte_count - 1)
    range_header = f"bytes={start}-{end}"
    request = Request(
        _drive_media_url(file_id=file_id, resource_key=resource_key),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Range": range_header,
            "User-Agent": "Elvern Route2 Cloud Benchmark",
        },
    )
    started = time.monotonic()
    first_byte_seconds: float | None = None
    bytes_read = 0
    status_code: int | None = None
    response_headers: dict[str, str] = {}
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status_code = int(getattr(response, "status", 200))
            response_headers = {str(key): str(value) for key, value in response.headers.items()}
            while True:
                chunk = response.read(max(1, min(chunk_size, byte_count - bytes_read)))
                if not chunk:
                    break
                if first_byte_seconds is None:
                    first_byte_seconds = time.monotonic() - started
                bytes_read += len(chunk)
                if bytes_read >= byte_count:
                    break
    except HTTPError as exc:
        elapsed = time.monotonic() - started
        detail = _small_error_body(exc) or str(exc)
        return {
            "media_item_id": int(item["id"]),
            "title": str(item["title"]),
            "original_filename": str(item["original_filename"]),
            "file_size": file_size,
            "probe_label": f"{position}_{range_mib}mib",
            "position": position,
            "range_mib": range_mib,
            "range_header": range_header,
            "status": int(exc.code),
            "accept_ranges": None,
            "content_range": None,
            "content_length": None,
            "content_type": None,
            "first_byte_seconds": None,
            "elapsed_seconds": round(elapsed, 3),
            "bytes_read": 0,
            "mib_per_second": 0.0,
            "success": False,
            "error_class": "HTTPError",
            "error_detail": _redact(detail),
            "abort_item": exc.code in {401, 403, 429} or exc.code >= 500,
        }
    except URLError as exc:
        elapsed = time.monotonic() - started
        return {
            "media_item_id": int(item["id"]),
            "title": str(item["title"]),
            "original_filename": str(item["original_filename"]),
            "file_size": file_size,
            "probe_label": f"{position}_{range_mib}mib",
            "position": position,
            "range_mib": range_mib,
            "range_header": range_header,
            "status": None,
            "accept_ranges": None,
            "content_range": None,
            "content_length": None,
            "content_type": None,
            "first_byte_seconds": None,
            "elapsed_seconds": round(elapsed, 3),
            "bytes_read": 0,
            "mib_per_second": 0.0,
            "success": False,
            "error_class": "URLError",
            "error_detail": _redact(getattr(exc, "reason", exc)),
            "abort_item": True,
        }

    elapsed = max(0.001, time.monotonic() - started)
    return {
        "media_item_id": int(item["id"]),
        "title": str(item["title"]),
        "original_filename": str(item["original_filename"]),
        "file_size": file_size,
        "probe_label": f"{position}_{range_mib}mib",
        "position": position,
        "range_mib": range_mib,
        "range_header": range_header,
        "status": status_code,
        "accept_ranges": response_headers.get("Accept-Ranges"),
        "content_range": response_headers.get("Content-Range"),
        "content_length": response_headers.get("Content-Length"),
        "content_type": response_headers.get("Content-Type"),
        "first_byte_seconds": round(first_byte_seconds, 3) if first_byte_seconds is not None else None,
        "elapsed_seconds": round(elapsed, 3),
        "bytes_read": bytes_read,
        "mib_per_second": round((bytes_read / 1024 / 1024) / elapsed, 3),
        "success": bool(status_code in {200, 206} and bytes_read > 0),
        "error_class": None,
        "error_detail": None,
        "abort_item": False,
    }


def _write_outputs(run_root: Path, payload: dict[str, object], probes: list[dict[str, object]]) -> tuple[Path, Path]:
    run_root.mkdir(parents=True, exist_ok=True)
    json_path = run_root / "summary.json"
    csv_path = run_root / "source_probes.csv"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for probe in probes:
            writer.writerow({key: probe.get(key) for key in CSV_FIELDS})
    return json_path, csv_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe Google Drive source throughput for cloud Route2 benchmark calibration.",
    )
    parser.add_argument("--media-item-id", action="append", type=int, default=[], help="Cloud media item id. Repeatable.")
    parser.add_argument("--title", action="append", default=[], help="Cloud media title or filename search. Repeatable.")
    parser.add_argument("--range-mib", nargs="*", default=[",".join(str(value) for value in DEFAULT_RANGE_MIB)])
    parser.add_argument("--positions", nargs="*", default=list(DEFAULT_POSITIONS), choices=DEFAULT_POSITIONS)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--chunk-size", type=int, default=1024 * 1024)
    parser.add_argument(
        "--no-resource-key-fetch",
        action="store_true",
        help="Use only stored resource keys; do not fetch a missing key for the probe.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dev/artifacts/route2-cloud-benchmark"),
        help="Benchmark artifact root. Generated outputs are intentionally outside production cache.",
    )
    args = parser.parse_args()

    _load_env_file()
    from backend.app.config import refresh_settings

    settings = refresh_settings()
    range_mib_values = _parse_positive_ints(args.range_mib, field_name="range-mib")
    items = _load_cloud_items(
        settings,
        media_item_ids=args.media_item_id,
        titles=args.title,
    )
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = args.output_dir / run_id

    all_probes: list[dict[str, object]] = []
    item_summaries: list[dict[str, object]] = []
    fatal_error = False
    for item in items:
        item_summary = {
            "media_item_id": int(item["id"]),
            "title": str(item["title"]),
            "original_filename": str(item["original_filename"]),
            "source_kind": str(item["source_kind"]),
            "file_size": int(item.get("file_size") or 0),
            "file_path": str(item.get("file_path") or ""),
            "external_media_id": str(item.get("external_media_id") or ""),
            "library_source_id": int(item.get("library_source_id") or 0),
            "resource_key_present_before_probe": bool(str(item.get("cloud_resource_key") or "").strip()),
            "resource_key_fetched_for_probe": False,
            "probe_count": 0,
            "successful_probe_count": 0,
            "aborted": False,
            "error": None,
        }
        try:
            target = _resolve_drive_target(
                settings,
                item,
                fetch_resource_key=not args.no_resource_key_fetch,
            )
            item_summary["resource_key_available_for_probe"] = bool(target["resource_key_present"])
            item_summary["resource_key_fetched_for_probe"] = (
                bool(target["resource_key_present"])
                and not bool(item_summary["resource_key_present_before_probe"])
            )
        except CloudBenchmarkError as exc:
            item_summary["aborted"] = True
            item_summary["error"] = _redact(exc)
            item_summaries.append(item_summary)
            fatal_error = True
            continue

        for position in args.positions:
            for range_mib in range_mib_values:
                probe = _probe_range(
                    access_token=str(target["access_token"]),
                    file_id=str(target["file_id"]),
                    resource_key=target.get("resource_key"),
                    item=item,
                    position=position,
                    range_mib=range_mib,
                    timeout_seconds=max(1.0, float(args.timeout_seconds)),
                    chunk_size=max(64 * 1024, int(args.chunk_size)),
                )
                all_probes.append(probe)
                item_summary["probe_count"] = int(item_summary["probe_count"]) + 1
                if probe.get("success"):
                    item_summary["successful_probe_count"] = int(item_summary["successful_probe_count"]) + 1
                if probe.get("abort_item"):
                    item_summary["aborted"] = True
                    item_summary["error"] = probe.get("error_detail") or probe.get("error_class")
                    fatal_error = True
                    break
            if item_summary["aborted"]:
                break
        item_summaries.append(item_summary)

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "mode": "source_probe",
        "end_to_end_thread_benchmark": "deferred",
        "notes": (
            "This probe reuses existing Google Drive account token helpers and the same Drive media range endpoint "
            "used by Elvern's cloud proxy path. It does not create Route2/native sessions or write production cache."
        ),
        "range_mib": range_mib_values,
        "positions": list(args.positions),
        "items": item_summaries,
        "probes": all_probes,
    }
    json_path, csv_path = _write_outputs(run_root, payload, all_probes)
    print(json_path)
    print(csv_path)
    return 2 if fatal_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
