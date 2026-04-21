#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CORE_DEADCHECK = ROOT / "scripts" / "elvern-core-backend-deadcheck.py"


def _load_core_deadcheck():
    spec = importlib.util.spec_from_file_location("elvern_core_backend_deadcheck", CORE_DEADCHECK)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {CORE_DEADCHECK}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _header(headers: dict[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _candidate_item_ids(settings: Any, *, limit_per_class: int = 2) -> list[int]:
    from backend.app.db import get_connection

    candidates: list[int] = []
    with get_connection(settings) as connection:
        for source_kind in ("cloud", "local"):
            smallest = connection.execute(
                """
                SELECT id
                FROM media_items
                WHERE COALESCE(duration_seconds, 0) > 0
                  AND COALESCE(source_kind, 'local') = ?
                ORDER BY COALESCE(file_size, 999999999999), id
                LIMIT ?
                """,
                (source_kind, limit_per_class),
            ).fetchall()
            largest = connection.execute(
                """
                SELECT id
                FROM media_items
                WHERE COALESCE(duration_seconds, 0) > 0
                  AND COALESCE(source_kind, 'local') = ?
                ORDER BY COALESCE(file_size, 0) DESC, id
                LIMIT ?
                """,
                (source_kind, limit_per_class),
            ).fetchall()
            candidates.extend(int(row["id"]) for row in smallest)
            candidates.extend(int(row["id"]) for row in largest)
    seen: set[int] = set()
    return [item_id for item_id in candidates if not (item_id in seen or seen.add(item_id))]


def _probe_stream(
    client: Any,
    *,
    stream_url: str,
    file_size: int,
    app: str,
    first_bytes: int,
    include_tail: bool,
) -> dict[str, dict[str, object]]:
    user_agent = "Infuse" if app == "infuse" else "VLC"
    probes: list[tuple[str, str, str, bool]] = [
        ("head_0_0", "HEAD", "bytes=0-0", False),
        ("get_0_0", "GET", "bytes=0-0", True),
        ("get_first_window", "GET", f"bytes=0-{max(first_bytes - 1, 0)}", True),
    ]
    if include_tail and file_size > 1:
        tail_size = max(min(first_bytes, file_size), 1)
        probes.append(("get_tail_window", "GET", f"bytes={file_size - tail_size}-{file_size - 1}", True))

    results: dict[str, dict[str, object]] = {}
    for label, method, byte_range, read_body in probes:
        status, headers, body = client.request(
            method,
            stream_url,
            headers={"Range": byte_range, "User-Agent": user_agent},
            read_body=read_body,
        )
        results[label] = {
            "method": method,
            "range": byte_range,
            "status": status,
            "elapsed_ms": float(headers.get("x-deadcheck-elapsed-ms", "0")),
            "accept_ranges": _header(headers, "Accept-Ranges"),
            "content_range": _header(headers, "Content-Range"),
            "content_length": _header(headers, "Content-Length"),
            "content_type": _header(headers, "Content-Type"),
            "transfer_encoding": _header(headers, "Transfer-Encoding"),
            "body_bytes_read": len(body.encode("latin1")) if read_body else 0,
        }
    return results


def _diagnose_item(
    deadcheck: Any,
    client: Any,
    *,
    item_id: int,
    app: str,
    first_bytes: int,
    include_tail: bool,
) -> dict[str, object]:
    status, _, body = client.request("GET", f"/api/library/item/{item_id}")
    if status != 200:
        return {"item_id": item_id, "error": f"detail_status_{status}"}
    item = json.loads(body)
    client_name = "Elvern iOS Infuse Handoff" if app == "infuse" else "Elvern iOS VLC Handoff"
    session: dict[str, object] | None = None
    try:
        session = client.json(
            "POST",
            f"/api/native-playback/{item_id}/session",
            data={
                "client_name": client_name,
                "external_player": app,
                "requested_transport_mode": "single_best_path",
                "caller_surface": "web_browser",
                "current_path_class": "unknown",
                "trusted_network_context": False,
                "allow_browser_fallback": True,
            },
            headers={"User-Agent": "Mozilla/5.0 (iPhone) Mobile Safari"},
        )
        stream_url = str(session["stream_url"])
        parsed = urllib.parse.urlsplit(stream_url)
        return {
            "item_id": item_id,
            "title": item.get("title"),
            "filename": item.get("original_filename"),
            "source_kind": item.get("source_kind"),
            "file_size": item.get("file_size"),
            "duration_seconds": item.get("duration_seconds"),
            "container": item.get("container"),
            "video_codec": item.get("video_codec"),
            "audio_codec": item.get("audio_codec"),
            "app": app,
            "api_origin": session.get("api_origin"),
            "stream_url_scheme": parsed.scheme,
            "stream_url_host": parsed.netloc,
            "stream_url_path": parsed.path,
            "tokenized_stream": "token=" in stream_url,
            "transport_decision": session.get("transport_decision"),
            "probes": _probe_stream(
                client,
                stream_url=stream_url,
                file_size=int(item.get("file_size") or 0),
                app=app,
                first_bytes=first_bytes,
                include_tail=include_tail,
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "item_id": item_id,
            "title": item.get("title"),
            "source_kind": item.get("source_kind"),
            "file_size": item.get("file_size"),
            "container": item.get("container"),
            "video_codec": item.get("video_codec"),
            "audio_codec": item.get("audio_codec"),
            "app": app,
            "error": str(exc),
        }
    finally:
        if session and session.get("close_url"):
            try:
                client.request("POST", str(session["close_url"]))
            except Exception:  # noqa: BLE001
                pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Probe Elvern's tokenized native stream path for Infuse/VLC startup diagnostics. "
            "This does not launch the external app."
        )
    )
    parser.add_argument("--base-url", default=None, help="Frontend/API base URL. Defaults to local frontend.")
    parser.add_argument("--item-id", action="append", type=int, default=[], help="Media item id to probe. Repeatable.")
    parser.add_argument("--app", choices=("infuse", "vlc"), default="infuse", help="External player profile to simulate.")
    parser.add_argument("--first-bytes", type=int, default=1024 * 1024, help="Range window size for first/tail probes.")
    parser.add_argument("--skip-tail", action="store_true", help="Skip tail Range probe.")
    args = parser.parse_args()

    deadcheck = _load_core_deadcheck()
    settings, _, token = deadcheck._settings_and_auth()
    base_url = args.base_url or deadcheck._local_frontend_url(settings)
    client = deadcheck.ApiClient(base_url, settings.session_cookie_name, token)
    item_ids = args.item_id or _candidate_item_ids(settings)
    results = [
        _diagnose_item(
            deadcheck,
            client,
            item_id=item_id,
            app=args.app,
            first_bytes=args.first_bytes,
            include_tail=not args.skip_tail,
        )
        for item_id in item_ids
    ]
    print(
        json.dumps(
            {
                "ok": True,
                "base_url": base_url,
                "note": (
                    "Compare fast and slow real item ids by source_kind/container/codecs/file_size "
                    "and by per-Range elapsed_ms/transfer_encoding/content_range."
                ),
                "items": results,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
