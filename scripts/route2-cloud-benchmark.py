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
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
DEFAULT_E2E_THREADS = (4, 6, 10, 12)
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
E2E_CSV_FIELDS = (
    "media_item_id",
    "title",
    "source_kind",
    "original_filename",
    "file_size",
    "thread_count",
    "repeat_index",
    "wall_seconds",
    "time_to_first_segment_seconds",
    "time_to_45s_runway_seconds",
    "time_to_120s_runway_seconds",
    "generated_seconds",
    "supply_rate_x",
    "avg_cpu_cores_used",
    "peak_cpu_cores_used",
    "peak_rss_bytes",
    "source_request_count",
    "source_bytes_proxied",
    "source_mib_per_second",
    "source_status_counts",
    "success",
    "error_class",
    "error_detail",
    "ffmpeg_stderr_path",
    "manifest_path",
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


def _clock_ticks_per_second() -> int:
    try:
        return max(1, int(os.sysconf("SC_CLK_TCK")))
    except (AttributeError, ValueError, OSError):
        return 100


def _read_process_cpu_seconds(pid: int) -> float | None:
    try:
        payload = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    close_index = payload.rfind(")")
    if close_index < 0 or close_index + 2 >= len(payload):
        return None
    tail = payload[close_index + 2 :].split()
    if len(tail) <= 12:
        return None
    try:
        return (int(tail[11]) + int(tail[12])) / _clock_ticks_per_second()
    except ValueError:
        return None


def _read_process_rss_bytes(pid: int) -> int | None:
    try:
        payload = (Path("/proc") / str(pid) / "status").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line.startswith("VmRSS:"):
            continue
        parts = line.split()
        if len(parts) < 2:
            return None
        try:
            return int(parts[1]) * 1024
        except ValueError:
            return None
    return None


def _manifest_generated_seconds(manifest_path: Path) -> float:
    try:
        payload = manifest_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0.0
    total = 0.0
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line.startswith("#EXTINF:"):
            continue
        value = line.removeprefix("#EXTINF:").split(",", 1)[0]
        try:
            total += max(0.0, float(value))
        except ValueError:
            continue
    return total


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


class _BenchmarkProxyState:
    def __init__(self, targets: dict[int, dict[str, object]]) -> None:
        self.targets = targets
        self._lock = threading.Lock()
        self._active_item_id: int | None = None
        self._metrics: dict[str, object] = {}

    def reset(self, *, item_id: int) -> None:
        with self._lock:
            self._active_item_id = int(item_id)
            self._metrics = {
                "started_at": time.monotonic(),
                "request_count": 0,
                "bytes_proxied": 0,
                "status_counts": {},
                "errors": [],
                "abort": False,
            }

    def record(self, *, status_code: int | None, bytes_proxied: int = 0, error: str | None = None) -> None:
        with self._lock:
            self._metrics["request_count"] = int(self._metrics.get("request_count") or 0) + 1
            self._metrics["bytes_proxied"] = int(self._metrics.get("bytes_proxied") or 0) + max(0, int(bytes_proxied))
            if status_code is not None:
                status_key = str(status_code)
                status_counts = dict(self._metrics.get("status_counts") or {})
                status_counts[status_key] = int(status_counts.get(status_key) or 0) + 1
                self._metrics["status_counts"] = status_counts
                if status_code in {401, 403, 429} or status_code >= 500:
                    self._metrics["abort"] = True
            if error:
                errors = list(self._metrics.get("errors") or [])
                errors.append(_redact(error))
                self._metrics["errors"] = errors[-10:]

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            elapsed = max(0.001, time.monotonic() - float(self._metrics.get("started_at") or time.monotonic()))
            bytes_proxied = int(self._metrics.get("bytes_proxied") or 0)
            return {
                "request_count": int(self._metrics.get("request_count") or 0),
                "bytes_proxied": bytes_proxied,
                "mib_per_second": round((bytes_proxied / 1024 / 1024) / elapsed, 3),
                "status_counts": dict(self._metrics.get("status_counts") or {}),
                "errors": list(self._metrics.get("errors") or []),
                "abort": bool(self._metrics.get("abort")),
            }


def _make_proxy_handler(state: _BenchmarkProxyState):
    class BenchmarkProxyHandler(BaseHTTPRequestHandler):
        server_version = "ElvernRoute2CloudBenchmark/1.0"

        def log_message(self, format, *args):  # noqa: A002, ANN001
            return

        def do_HEAD(self) -> None:  # noqa: N802
            self._handle(send_body=False)

        def do_GET(self) -> None:  # noqa: N802
            self._handle(send_body=True)

        def _handle(self, *, send_body: bool) -> None:
            path = self.path.split("?", 1)[0].strip("/")
            parts = path.split("/")
            if len(parts) != 2 or parts[0] != "media":
                self.send_error(404)
                return
            try:
                item_id = int(parts[1])
            except ValueError:
                self.send_error(404)
                return
            target = state.targets.get(item_id)
            if target is None:
                self.send_error(404)
                return
            request_headers = {
                "Authorization": f"Bearer {target['access_token']}",
                "User-Agent": "Elvern Route2 Cloud E2E Benchmark Proxy",
            }
            range_header = self.headers.get("Range")
            if range_header:
                request_headers["Range"] = range_header
            request = Request(
                _drive_media_url(
                    file_id=str(target["file_id"]),
                    resource_key=target.get("resource_key"),
                ),
                headers=request_headers,
                method="GET" if send_body else "HEAD",
            )
            bytes_proxied = 0
            status_code: int | None = None
            try:
                with urlopen(request, timeout=60) as upstream:
                    status_code = int(getattr(upstream, "status", 200))
                    self.send_response(status_code)
                    upstream_headers = getattr(upstream, "headers", {})
                    for header_name in (
                        "Accept-Ranges",
                        "Content-Length",
                        "Content-Range",
                        "Content-Type",
                        "Last-Modified",
                    ):
                        header_value = upstream_headers.get(header_name)
                        if header_value:
                            self.send_header(header_name, header_value)
                    self.send_header("Cache-Control", "private, max-age=0, must-revalidate")
                    self.end_headers()
                    if send_body:
                        while True:
                            chunk = upstream.read(1024 * 1024)
                            if not chunk:
                                break
                            try:
                                self.wfile.write(chunk)
                            except (BrokenPipeError, ConnectionResetError):
                                break
                            bytes_proxied += len(chunk)
            except HTTPError as exc:
                status_code = int(exc.code)
                detail = _small_error_body(exc) or str(exc)
                state.record(status_code=status_code, error=detail)
                self.send_response(status_code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                if send_body:
                    self.wfile.write(_redact(detail).encode("utf-8", errors="replace")[:2048])
                return
            except URLError as exc:
                detail = str(getattr(exc, "reason", exc) or "Google Drive proxy read failed")
                state.record(status_code=None, error=detail)
                self.send_response(502)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                if send_body:
                    self.wfile.write(_redact(detail).encode("utf-8", errors="replace")[:2048])
                return
            finally:
                if status_code is not None:
                    state.record(status_code=status_code, bytes_proxied=bytes_proxied)

    return BenchmarkProxyHandler


def _start_benchmark_proxy(targets: dict[int, dict[str, object]]) -> tuple[ThreadingHTTPServer, _BenchmarkProxyState, str]:
    state = _BenchmarkProxyState(targets)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_proxy_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="route2-cloud-benchmark-proxy")
    thread.start()
    host, port = server.server_address
    return server, state, f"http://{host}:{port}"


def _build_cloud_e2e_ffmpeg_command(
    *,
    ffmpeg_path: str,
    source_url: str,
    output_dir: Path,
    thread_count: int,
    sample_seconds: float,
    hls_time: float,
    profile_key: str,
) -> tuple[list[str], Path]:
    from backend.app.services.mobile_playback_models import MOBILE_PROFILES, SEGMENT_DURATION_SECONDS

    profile = MOBILE_PROFILES.get(profile_key)
    if profile is None:
        raise CloudBenchmarkError(f"Unknown Route2 profile: {profile_key}")
    manifest_path = output_dir / "index.m3u8"
    segment_pattern = output_dir / "segment_%06d.m4s"
    keyframe_interval = int(SEGMENT_DURATION_SECONDS * 24)
    scale_filter = (
        f"scale=w='min({profile.max_width},iw)':h='min({profile.max_height},ih)':"
        "force_original_aspect_ratio=decrease"
    )
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-y",
        "-threads",
        str(max(1, int(thread_count))),
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_on_network_error",
        "1",
        "-rw_timeout",
        "15000000",
        "-ss",
        "0.000",
        "-i",
        source_url,
        "-t",
        str(float(sample_seconds)),
        "-output_ts_offset",
        "0.000",
        "-muxpreload",
        "0",
        "-muxdelay",
        "0",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-sn",
        "-dn",
        "-vf",
        scale_filter,
        "-c:v",
        "libx264",
        "-preset",
        "superfast",
        "-profile:v",
        "high",
        "-level:v",
        profile.level,
        "-pix_fmt",
        "yuv420p",
        "-crf",
        str(profile.crf),
        "-maxrate",
        profile.maxrate,
        "-bufsize",
        profile.bufsize,
        "-g",
        str(keyframe_interval),
        "-keyint_min",
        str(keyframe_interval),
        "-sc_threshold",
        "0",
        "-force_key_frames",
        f"expr:gte(t,n_forced*{SEGMENT_DURATION_SECONDS})",
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-ar",
        "48000",
        "-b:a",
        "160k",
        "-max_muxing_queue_size",
        "2048",
        "-f",
        "hls",
        "-hls_time",
        f"{float(hls_time):.0f}",
        "-hls_list_size",
        "0",
        "-hls_segment_type",
        "fmp4",
        "-hls_fmp4_init_filename",
        "init.mp4",
        "-hls_flags",
        "independent_segments+temp_file",
        "-start_number",
        "0",
        "-hls_segment_filename",
        str(segment_pattern),
        str(manifest_path),
    ]
    return command, manifest_path


def _run_cloud_e2e_one(
    *,
    settings,
    item: dict[str, object],
    proxy_base_url: str,
    proxy_state: _BenchmarkProxyState,
    thread_count: int,
    repeat_index: int,
    run_root: Path,
    sample_seconds: float,
    sample_interval: float,
    hls_time: float,
    profile_key: str,
) -> dict[str, object]:
    output_dir = run_root / f"item-{item['id']}" / f"threads-{thread_count}-repeat-{repeat_index}"
    output_dir.mkdir(parents=True, exist_ok=True)
    stderr_path = output_dir / "ffmpeg.stderr.log"
    source_url = f"{proxy_base_url}/media/{int(item['id'])}"
    command, manifest_path = _build_cloud_e2e_ffmpeg_command(
        ffmpeg_path=str(settings.ffmpeg_path or "ffmpeg"),
        source_url=source_url,
        output_dir=output_dir,
        thread_count=thread_count,
        sample_seconds=sample_seconds,
        hls_time=hls_time,
        profile_key=profile_key,
    )
    proxy_state.reset(item_id=int(item["id"]))
    start_wall = time.monotonic()
    first_segment_wall: float | None = None
    runway_45_wall: float | None = None
    runway_120_wall: float | None = None
    peak_cpu_cores = 0.0
    peak_rss_bytes = 0
    cpu_start: float | None = None
    cpu_end: float | None = None
    last_cpu_sample: tuple[float, float] | None = None
    generated_seconds = 0.0
    success = False
    error_class: str | None = None
    error_detail: str | None = None

    with stderr_path.open("w", encoding="utf-8", errors="replace") as stderr_stream:
        try:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=stderr_stream, text=True)
        except OSError as exc:
            return {
                "media_item_id": int(item["id"]),
                "title": str(item["title"]),
                "source_kind": str(item["source_kind"]),
                "original_filename": str(item["original_filename"]),
                "file_size": int(item.get("file_size") or 0),
                "thread_count": thread_count,
                "repeat_index": repeat_index,
                "wall_seconds": 0.0,
                "success": False,
                "error_class": "OSError",
                "error_detail": _redact(exc),
                "ffmpeg_stderr_path": str(stderr_path),
                "manifest_path": str(manifest_path),
            }
        while process.poll() is None:
            now = time.monotonic()
            elapsed = now - start_wall
            cpu_seconds = _read_process_cpu_seconds(process.pid)
            rss_bytes = _read_process_rss_bytes(process.pid)
            generated_seconds = _manifest_generated_seconds(manifest_path)
            if cpu_seconds is not None:
                cpu_start = cpu_seconds if cpu_start is None else cpu_start
                cpu_end = cpu_seconds
                if last_cpu_sample is not None:
                    last_wall, last_cpu = last_cpu_sample
                    delta_wall = max(0.001, now - last_wall)
                    peak_cpu_cores = max(peak_cpu_cores, max(0.0, cpu_seconds - last_cpu) / delta_wall)
                last_cpu_sample = (now, cpu_seconds)
            if rss_bytes is not None:
                peak_rss_bytes = max(peak_rss_bytes, rss_bytes)
            if first_segment_wall is None and any(output_dir.glob("segment_*.m4s")):
                first_segment_wall = elapsed
            if runway_45_wall is None and generated_seconds >= 45.0:
                runway_45_wall = elapsed
            if runway_120_wall is None and generated_seconds >= 120.0:
                runway_120_wall = elapsed
            if proxy_state.snapshot().get("abort"):
                process.terminate()
                error_class = "CloudSourceAbort"
                error_detail = "Cloud source proxy reported provider/auth/quota/server errors."
                break
            time.sleep(max(0.1, float(sample_interval)))
        return_code = process.wait()
        success = return_code == 0
        if not success and error_class is None:
            error_class = "FFmpegError"
            error_detail = f"ffmpeg exited with code {return_code}"

    wall_seconds = max(0.001, time.monotonic() - start_wall)
    generated_seconds = _manifest_generated_seconds(manifest_path)
    avg_cpu_cores = (
        max(0.0, float(cpu_end) - float(cpu_start)) / wall_seconds
        if cpu_start is not None and cpu_end is not None
        else None
    )
    source_metrics = proxy_state.snapshot()
    return {
        "media_item_id": int(item["id"]),
        "title": str(item["title"]),
        "source_kind": str(item["source_kind"]),
        "original_filename": str(item["original_filename"]),
        "file_size": int(item.get("file_size") or 0),
        "thread_count": thread_count,
        "repeat_index": repeat_index,
        "wall_seconds": round(wall_seconds, 3),
        "time_to_first_segment_seconds": round(first_segment_wall, 3) if first_segment_wall is not None else None,
        "time_to_45s_runway_seconds": round(runway_45_wall, 3) if runway_45_wall is not None else None,
        "time_to_120s_runway_seconds": round(runway_120_wall, 3) if runway_120_wall is not None else None,
        "generated_seconds": round(generated_seconds, 3),
        "supply_rate_x": round(generated_seconds / wall_seconds, 3),
        "avg_cpu_cores_used": round(avg_cpu_cores, 3) if avg_cpu_cores is not None else None,
        "peak_cpu_cores_used": round(peak_cpu_cores, 3),
        "peak_rss_bytes": peak_rss_bytes or None,
        "source_request_count": source_metrics.get("request_count"),
        "source_bytes_proxied": source_metrics.get("bytes_proxied"),
        "source_mib_per_second": source_metrics.get("mib_per_second"),
        "source_status_counts": source_metrics.get("status_counts"),
        "source_errors": source_metrics.get("errors"),
        "success": success,
        "error_class": error_class,
        "error_detail": _redact(error_detail),
        "ffmpeg_stderr_path": str(stderr_path),
        "manifest_path": str(manifest_path),
        "command": command,
    }


def _write_e2e_outputs(run_root: Path, payload: dict[str, object], runs: list[dict[str, object]]) -> tuple[Path, Path]:
    run_root.mkdir(parents=True, exist_ok=True)
    json_path = run_root / "summary.json"
    csv_path = run_root / "summary.csv"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=E2E_CSV_FIELDS)
        writer.writeheader()
        for run in runs:
            row = {key: run.get(key) for key in E2E_CSV_FIELDS}
            row["source_status_counts"] = json.dumps(row["source_status_counts"], sort_keys=True)
            writer.writerow(row)
    return json_path, csv_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe Google Drive source throughput for cloud Route2 benchmark calibration.",
    )
    parser.add_argument("--mode", choices=("source-probe", "ffmpeg-e2e"), default="source-probe")
    parser.add_argument("--media-item-id", action="append", type=int, default=[], help="Cloud media item id. Repeatable.")
    parser.add_argument("--title", action="append", default=[], help="Cloud media title or filename search. Repeatable.")
    parser.add_argument("--range-mib", nargs="*", default=[",".join(str(value) for value in DEFAULT_RANGE_MIB)])
    parser.add_argument("--positions", nargs="*", default=list(DEFAULT_POSITIONS), choices=DEFAULT_POSITIONS)
    parser.add_argument("--threads", nargs="*", default=[",".join(str(value) for value in DEFAULT_E2E_THREADS)])
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--sample-seconds", type=float, default=150.0)
    parser.add_argument("--sample-interval", type=float, default=0.5)
    parser.add_argument("--hls-time", type=float, default=2.0)
    parser.add_argument("--profile", default="mobile_2160p")
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
    thread_counts = _parse_positive_ints(args.threads, field_name="threads")
    items = _load_cloud_items(
        settings,
        media_item_ids=args.media_item_id,
        titles=args.title,
    )
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = args.output_dir / run_id

    if args.mode == "ffmpeg-e2e":
        targets: dict[int, dict[str, object]] = {}
        item_summaries: list[dict[str, object]] = []
        for item in items:
            target = _resolve_drive_target(
                settings,
                item,
                fetch_resource_key=not args.no_resource_key_fetch,
            )
            targets[int(item["id"])] = target
            item_summaries.append(
                {
                    "media_item_id": int(item["id"]),
                    "title": str(item["title"]),
                    "original_filename": str(item["original_filename"]),
                    "source_kind": str(item["source_kind"]),
                    "file_size": int(item.get("file_size") or 0),
                    "file_path_label": str(item.get("file_path") or "").split("?", 1)[0],
                    "resource_key_available_for_benchmark": bool(target.get("resource_key_present")),
                }
            )
        server, proxy_state, proxy_base_url = _start_benchmark_proxy(targets)
        runs: list[dict[str, object]] = []
        fatal_error = False
        try:
            for item in items:
                for thread_count in thread_counts:
                    for repeat_index in range(1, max(1, int(args.repeats)) + 1):
                        run = _run_cloud_e2e_one(
                            settings=settings,
                            item=item,
                            proxy_base_url=proxy_base_url,
                            proxy_state=proxy_state,
                            thread_count=thread_count,
                            repeat_index=repeat_index,
                            run_root=run_root,
                            sample_seconds=float(args.sample_seconds),
                            sample_interval=float(args.sample_interval),
                            hls_time=float(args.hls_time),
                            profile_key=str(args.profile),
                        )
                        runs.append(run)
                        if run.get("error_class") == "CloudSourceAbort":
                            fatal_error = True
                            break
                    if fatal_error:
                        break
                if fatal_error:
                    break
        finally:
            server.shutdown()
            server.server_close()
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "mode": "ffmpeg_e2e",
            "notes": (
                "This benchmark feeds ffmpeg through a temporary tokenless localhost proxy that forwards range "
                "requests to Google Drive using existing app token helpers. It does not create Route2/native "
                "playback sessions and writes only isolated benchmark artifacts."
            ),
            "threads": thread_counts,
            "repeats": max(1, int(args.repeats)),
            "sample_seconds": float(args.sample_seconds),
            "profile": str(args.profile),
            "items": item_summaries,
            "results": runs,
        }
        json_path, csv_path = _write_e2e_outputs(run_root, payload, runs)
        print(json_path)
        print(csv_path)
        return 2 if fatal_error or not all(run.get("success") for run in runs) else 0

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
