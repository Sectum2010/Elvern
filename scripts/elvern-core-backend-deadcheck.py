#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class DeadcheckError(RuntimeError):
    pass


def _load_env_file() -> None:
    env_path = ROOT / "deploy" / "env" / "elvern.env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def _settings_and_auth():
    _load_env_file()
    from backend.app.auth import create_session
    from backend.app.config import get_settings
    from backend.app.db import get_connection
    from backend.app.models import AuthenticatedUser

    settings = get_settings()
    with get_connection(settings) as conn:
        row = conn.execute(
            "SELECT id, username, role, enabled FROM users WHERE enabled = 1 ORDER BY id LIMIT 1"
        ).fetchone()
    if row is None:
        raise DeadcheckError("No enabled user exists for deadcheck authentication.")
    user = AuthenticatedUser(
        id=int(row["id"]),
        username=str(row["username"]),
        role=str(row["role"] or "standard_user"),
        enabled=bool(row["enabled"]),
    )
    token = create_session(
        settings,
        user,
        ip_address="127.0.0.1",
        user_agent="elvern-core-backend-deadcheck",
    )
    return settings, user, token


def _local_frontend_url(settings: Any) -> str:
    port = os.environ.get("ELVERN_FRONTEND_PORT", "4173")
    return f"http://127.0.0.1:{port}"


class ApiClient:
    def __init__(self, base_url: str, cookie_name: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.cookie_name = cookie_name
        self.token = token

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        read_body: bool = True,
    ) -> tuple[int, dict[str, str], str]:
        url = path_or_url if path_or_url.startswith(("http://", "https://")) else f"{self.base_url}{path_or_url}"
        body = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("Cookie", f"{self.cookie_name}={self.token}")
        request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content = response.read().decode("latin1", "replace") if read_body else ""
                elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
                response_headers = dict(response.headers)
                response_headers["x-deadcheck-elapsed-ms"] = str(elapsed_ms)
                return response.status, response_headers, content
        except urllib.error.HTTPError as exc:
            content = exc.read().decode("latin1", "replace")
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            response_headers = dict(exc.headers)
            response_headers["x-deadcheck-elapsed-ms"] = str(elapsed_ms)
            return exc.code, response_headers, content

    def json(self, method: str, path: str, *, data: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        status, _, body = self.request(method, path, data=data, headers=headers)
        if status < 200 or status >= 300:
            raise DeadcheckError(f"{method} {path} returned {status}: {body[:300]}")
        return json.loads(body) if body else None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DeadcheckError(message)


def _choose_visible_item(
    settings: Any,
    client: ApiClient,
    *,
    preferred_id: int | None,
    source_kind: str | None = None,
) -> dict[str, Any]:
    from backend.app.db import get_connection

    candidates: list[int] = []
    if preferred_id:
        candidates.append(preferred_id)
    with get_connection(settings) as conn:
        query = """
            SELECT id
            FROM media_items
            WHERE COALESCE(duration_seconds, 0) > 0
        """
        params: list[Any] = []
        if source_kind:
            query += " AND source_kind = ?"
            params.append(source_kind)
        query += " ORDER BY COALESCE(duration_seconds, 999999), COALESCE(file_size, 999999999999), id LIMIT 40"
        candidates.extend(int(row["id"]) for row in conn.execute(query, params).fetchall())
    seen: set[int] = set()
    for item_id in candidates:
        if item_id in seen:
            continue
        seen.add(item_id)
        status, _, body = client.request("GET", f"/api/library/item/{item_id}")
        if status == 200:
            return json.loads(body)
    label = f" {source_kind}" if source_kind else ""
    raise DeadcheckError(f"No visible{label} media item could be selected for deadcheck.")


def _check_detail_payload(client: ApiClient, item: dict[str, Any]) -> dict[str, Any]:
    detail = client.json("GET", f"/api/library/item/{item['id']}")
    for field in ("id", "title", "original_filename", "source_kind", "source_label", "file_size", "duration_seconds", "stream_url"):
        _require(field in detail, f"Detail payload is missing {field!r}.")
    _require(detail["id"] == item["id"], "Detail payload item id drifted.")
    _require(str(detail["title"]).strip(), "Detail payload title is empty.")
    _require(detail["source_kind"] in {"local", "cloud"}, "Detail source_kind is invalid.")
    _require(float(detail["duration_seconds"] or 0) > 0, "Detail duration_seconds must be positive.")
    return {
        "item_id": detail["id"],
        "title": detail["title"],
        "source_kind": detail["source_kind"],
        "source_label": detail["source_label"],
        "duration_seconds": detail["duration_seconds"],
        "has_poster_url": bool(detail.get("poster_url")),
    }


def _stop_mobile_session(client: ApiClient, payload: dict[str, Any] | None) -> None:
    if payload and payload.get("stop_url"):
        client.request("POST", str(payload["stop_url"]))


def _check_mobile_session(client: ApiClient, item_id: int, playback_mode: str) -> dict[str, Any]:
    payload = client.json(
        "POST",
        "/api/mobile-playback/sessions",
        data={
            "item_id": item_id,
            "profile": "mobile_1080p",
            "start_position_seconds": 0,
            "engine_mode": "route2",
            "playback_mode": playback_mode,
        },
        headers={"User-Agent": "Mozilla/5.0 (iPhone) Mobile Safari"},
    )
    try:
        for field in (
            "session_id",
            "media_item_id",
            "manifest_url",
            "status_url",
            "heartbeat_url",
            "stop_url",
            "engine_mode",
            "playback_mode",
            "mode_state",
            "attach_revision",
        ):
            _require(field in payload, f"Mobile {playback_mode} payload is missing {field!r}.")
        _require(payload["media_item_id"] == item_id, f"Mobile {playback_mode} item id drifted.")
        _require(payload["engine_mode"] == "route2", f"Mobile {playback_mode} did not use route2 engine.")
        _require(payload["playback_mode"] == playback_mode, f"Mobile {playback_mode} playback_mode drifted.")
        _require(str(payload["manifest_url"]).startswith("/api/mobile-playback/"), "Mobile manifest_url path drifted.")
        _require(str(payload["status_url"]).startswith("/api/mobile-playback/"), "Mobile status_url path drifted.")
        return {
            "session_id": payload["session_id"],
            "state": payload.get("state"),
            "engine_mode": payload.get("engine_mode"),
            "playback_mode": payload.get("playback_mode"),
            "mode_state": payload.get("mode_state"),
            "attach_ready": payload.get("attach_ready"),
            "mode_ready": payload.get("mode_ready"),
            "prepared_not_playable_note": "API payload sanity only; browser deadcheck proves playable.",
        }
    finally:
        _stop_mobile_session(client, payload)


def _check_native_handoff(client: ApiClient, item: dict[str, Any], app: str) -> dict[str, Any]:
    client_name = "Elvern iOS Infuse Handoff" if app == "infuse" else "Elvern iOS VLC Handoff"
    session = client.json(
        "POST",
        f"/api/native-playback/{item['id']}/session",
        data={
            "client_name": client_name,
            "external_player": app,
            "caller_surface": "web_browser",
            "trusted_network_context": True,
            "allow_browser_fallback": True,
        },
        headers={"User-Agent": "Mozilla/5.0 (iPhone) Mobile Safari"},
    )
    for field in ("session_id", "stream_url", "details_url", "access_token", "expires_at"):
        _require(field in session, f"{app} native payload is missing {field!r}.")
    stream_url = str(session["stream_url"])
    _require("/api/native-playback/session/" in stream_url, f"{app} stream_url path drifted.")
    _require("token=" in stream_url, f"{app} stream_url is not tokenized.")
    status, headers, _ = client.request(
        "HEAD",
        stream_url,
        headers={
            "Range": "bytes=0-0",
            "User-Agent": "Infuse" if app == "infuse" else "VLC",
        },
        read_body=False,
    )
    _require(status in {200, 206}, f"{app} stream HEAD returned {status}.")
    if status == 206:
        _require(bool(headers.get("Content-Range") or headers.get("content-range")), f"{app} stream 206 lacks Content-Range.")
    decision = session.get("transport_decision") or {}
    return {
        "item_id": item["id"],
        "item_source_kind": item.get("source_kind"),
        "item_container": item.get("container"),
        "item_file_size": item.get("file_size"),
        "stream_url_path": urllib.parse.urlsplit(stream_url).path,
        "tokenized_stream": "token=" in stream_url,
        "head_status": status,
        "content_range": headers.get("Content-Range") or headers.get("content-range"),
        "content_type": headers.get("Content-Type") or headers.get("content-type"),
        "accept_ranges": headers.get("Accept-Ranges") or headers.get("accept-ranges"),
        "elapsed_ms": float(headers.get("x-deadcheck-elapsed-ms", "0")),
        "transport_player": decision.get("selected_player"),
        "transport_mode": decision.get("selected_mode"),
    }


def _check_desktop_vlc_handoff(client: ApiClient, item_id: int) -> dict[str, Any]:
    handoff = client.json(
        "POST",
        f"/api/desktop-playback/{item_id}/handoff",
        data={"platform": "linux", "device_id": "core-deadcheck"},
        headers={"User-Agent": "Elvern Core Deadcheck"},
    )
    for field in ("handoff_id", "protocol_url", "playlist_url", "strategy", "expires_at"):
        _require(field in handoff, f"Desktop VLC handoff payload is missing {field!r}.")
    parsed = urllib.parse.urlsplit(str(handoff["protocol_url"]))
    query = urllib.parse.parse_qs(parsed.query)
    token = (query.get("token") or [""])[0]
    _require(token, "Desktop VLC protocol_url is missing token.")
    resolved = client.json(
        "GET",
        f"/api/desktop-playback/handoff/{handoff['handoff_id']}?token={urllib.parse.quote(token)}",
        headers={
            "x-elvern-helper-version": "core-deadcheck",
            "x-elvern-helper-platform": "linux",
            "x-elvern-helper-arch": "x64",
            "x-elvern-vlc-detection-state": "not_verified",
        },
    )
    for field in ("handoff_id", "media_id", "platform", "strategy", "target_kind", "target"):
        _require(field in resolved, f"Desktop VLC resolve payload is missing {field!r}.")
    _require(resolved["handoff_id"] == handoff["handoff_id"], "Desktop VLC handoff id drifted.")
    _require(resolved["media_id"] == item_id, "Desktop VLC media id drifted.")
    _require(resolved["target_kind"] in {"path", "url"}, "Desktop VLC target_kind is invalid.")
    return {
        "handoff_id": resolved["handoff_id"],
        "platform": resolved["platform"],
        "strategy": resolved["strategy"],
        "target_kind": resolved["target_kind"],
        "has_started_url": bool(resolved.get("started_url")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Elvern core backend/API deadchecks.")
    parser.add_argument("--base-url", default=None, help="Frontend/API base URL. Defaults to local frontend.")
    parser.add_argument("--item-id", type=int, default=None, help="Preferred visible item id for Detail and mobile checks.")
    parser.add_argument("--native-item-id", type=int, default=None, help="Preferred visible item id for VLC/Infuse stream checks.")
    args = parser.parse_args()

    settings, _, token = _settings_and_auth()
    base_url = args.base_url or _local_frontend_url(settings)
    client = ApiClient(base_url, settings.session_cookie_name, token)

    active = client.json("GET", "/api/mobile-playback/active")
    _stop_mobile_session(client, active)

    item = _choose_visible_item(settings, client, preferred_id=args.item_id)
    native_item = _choose_visible_item(settings, client, preferred_id=args.native_item_id, source_kind="local")

    result = {
        "base_url": base_url,
        "detail": _check_detail_payload(client, item),
        "mobile_lite": _check_mobile_session(client, int(item["id"]), "lite"),
        "mobile_full": _check_mobile_session(client, int(item["id"]), "full"),
        "native_vlc": _check_native_handoff(client, native_item, "vlc"),
        "native_infuse": _check_native_handoff(client, native_item, "infuse"),
        "desktop_vlc_handoff": _check_desktop_vlc_handoff(client, int(native_item["id"])),
        "infuse_diagnostics_note": (
            "Use native_infuse.elapsed_ms/content_range/source_kind/container/file_size to compare slow vs fast handoff cases."
        ),
    }
    print(json.dumps({"ok": True, "checks": result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeadcheckError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        raise SystemExit(1) from exc
