#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


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
        raise DeadcheckError("No enabled user exists for browser deadcheck authentication.")
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
        user_agent="elvern-core-browser-deadcheck",
    )
    return settings, user, token


def _local_frontend_url() -> str:
    port = os.environ.get("ELVERN_FRONTEND_PORT", "4173")
    return f"http://127.0.0.1:{port}"


def _api_request(
    base_url: str,
    cookie_name: str,
    token: str,
    method: str,
    path: str,
    data: dict[str, Any] | None = None,
) -> Any:
    body = None if data is None else json.dumps(data).encode()
    request = urllib.request.Request(f"{base_url.rstrip('/')}{path}", data=body, method=method)
    request.add_header("Cookie", f"{cookie_name}={token}")
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = response.read().decode()
        return json.loads(payload) if payload else None


def _stop_active_mobile_session(base_url: str, cookie_name: str, token: str) -> None:
    try:
        active = _api_request(base_url, cookie_name, token, "GET", "/api/mobile-playback/active")
    except Exception:
        return
    if active and active.get("stop_url"):
        try:
            _api_request(base_url, cookie_name, token, "POST", str(active["stop_url"]))
        except Exception:
            pass


def _choose_visible_item(settings: Any, base_url: str, cookie_name: str, token: str, preferred_id: int | None) -> dict[str, Any]:
    from backend.app.db import get_connection

    candidates: list[int] = []
    if preferred_id:
        candidates.append(preferred_id)
    with get_connection(settings) as conn:
        candidates.extend(
            int(row["id"])
            for row in conn.execute(
                """
                SELECT id
                FROM media_items
                WHERE COALESCE(duration_seconds, 0) > 0
                ORDER BY COALESCE(duration_seconds, 999999), COALESCE(file_size, 999999999999), id
                LIMIT 60
                """
            ).fetchall()
        )
    seen: set[int] = set()
    for item_id in candidates:
        if item_id in seen:
            continue
        seen.add(item_id)
        try:
            return _api_request(base_url, cookie_name, token, "GET", f"/api/library/item/{item_id}")
        except urllib.error.HTTPError:
            continue
    raise DeadcheckError("No visible media item could be selected for browser deadcheck.")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class WebDriver:
    def __init__(self, geckodriver: str, *, width: int, height: int) -> None:
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.proc = subprocess.Popen(
            [geckodriver, "--host", "127.0.0.1", "--port", str(self.port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.session_id: str | None = None
        self.width = width
        self.height = height

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None, *, timeout: int = 60) -> Any:
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode()
            return json.loads(body) if body else None

    def start(self) -> None:
        deadline = time.time() + 15
        while True:
            try:
                status = self._request("GET", "/status", timeout=1)
                if status.get("value", {}).get("ready"):
                    break
            except Exception:
                if self.proc.poll() is not None:
                    raise DeadcheckError("geckodriver exited before becoming ready.")
                if time.time() > deadline:
                    raise
                time.sleep(0.25)
        session = self._request(
            "POST",
            "/session",
            {
                "capabilities": {
                    "alwaysMatch": {
                        "browserName": "firefox",
                        "acceptInsecureCerts": True,
                        "moz:firefoxOptions": {
                            "args": ["-headless", f"--width={self.width}", f"--height={self.height}"],
                            "prefs": {
                                "general.useragent.override": IPHONE_UA,
                                "media.autoplay.default": 0,
                                "media.autoplay.blocking_policy": 0,
                            },
                        },
                    }
                }
            },
        )
        self.session_id = str(session["value"]["sessionId"])

    def wd(self, method: str, path: str, payload: dict[str, Any] | None = None, *, timeout: int = 60) -> Any:
        if not self.session_id:
            raise DeadcheckError("WebDriver session has not started.")
        return self._request(method, f"/session/{self.session_id}{path}", payload, timeout=timeout)

    def script(self, source: str, *args: Any, timeout: int = 60) -> Any:
        return self.wd("POST", "/execute/sync", {"script": source, "args": list(args)}, timeout=timeout)["value"]

    def close(self) -> None:
        if self.session_id:
            try:
                self.wd("DELETE", "", timeout=10)
            except Exception:
                pass
            self.session_id = None
        if self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                pass


PROBE_SCRIPT = r"""
(() => {
  window.__elvernTrace = [];
  window.__elvernPush = (type, data = {}) => {
    const entry = {t: Math.round(performance.now()), type, ...data};
    window.__elvernTrace.push(entry);
    if (window.__elvernTrace.length > 3000) window.__elvernTrace.shift();
  };
  const push = window.__elvernPush;
  window.addEventListener("error", (event) => push("window:error", {message: event.message, source: event.filename, line: event.lineno}));
  window.addEventListener("unhandledrejection", (event) => push("window:unhandledrejection", {reason: String(event.reason && (event.reason.message || event.reason))}));
  const originalFetch = window.fetch.bind(window);
  window.fetch = function(input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || String(input);
    const interesting = /mobile-playback|browser-playback|native-playback|\.m3u8|\/init|\/segments\//.test(url);
    if (interesting) push("fetch:start", {url, method: (init && init.method) || "GET"});
    return originalFetch(input, init).then((response) => {
      if (interesting) push("fetch:done", {url, status: response.status, ok: response.ok, type: response.headers.get("content-type") || ""});
      return response;
    }, (error) => {
      if (interesting) push("fetch:error", {url, message: String(error && (error.message || error))});
      throw error;
    });
  };
  const originalLoad = HTMLMediaElement.prototype.load;
  HTMLMediaElement.prototype.load = function() {
    push("media:load:call", {src: this.currentSrc || this.getAttribute("src") || "", readyState: this.readyState, currentTime: this.currentTime, paused: this.paused});
    return originalLoad.apply(this, arguments);
  };
  const originalPlay = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function() {
    push("media:play:call", {src: this.currentSrc || this.getAttribute("src") || "", readyState: this.readyState, currentTime: this.currentTime, paused: this.paused});
    const result = originalPlay.apply(this, arguments);
    if (result && typeof result.then === "function") {
      result.then(
        () => push("media:play:resolved", {readyState: this.readyState, currentTime: this.currentTime, paused: this.paused}),
        (error) => push("media:play:rejected", {message: String(error && (error.message || error)), readyState: this.readyState, currentTime: this.currentTime, paused: this.paused}),
      );
    }
    return result;
  };
  const events = ["loadstart", "loadedmetadata", "loadeddata", "canplay", "play", "playing", "pause", "waiting", "stalled", "emptied", "error", "durationchange", "timeupdate", "seeking", "seeked"];
  function attach(video) {
    if (!video || video.__elvernProbeAttached) return;
    video.__elvernProbeAttached = true;
    push("video:attached-listeners", {cls: String(video.className || "")});
    for (const eventName of events) {
      video.addEventListener(eventName, () => push(`video:${eventName}`, {
        readyState: video.readyState,
        networkState: video.networkState,
        currentTime: Number((video.currentTime || 0).toFixed(3)),
        duration: Number.isFinite(video.duration) ? Number(video.duration.toFixed(3)) : String(video.duration),
        paused: video.paused,
        currentSrc: video.currentSrc || video.getAttribute("src") || "",
        width: video.videoWidth || 0,
        height: video.videoHeight || 0,
        controls: video.controls,
        cls: String(video.className || ""),
        error: video.error ? {code: video.error.code, message: video.error.message} : null,
      }));
    }
  }
  function attachAll() { document.querySelectorAll("video").forEach(attach); }
  attachAll();
  new MutationObserver(attachAll).observe(document.documentElement, {subtree: true, childList: true});
  window.__elvernSnapshot = () => {
    const video = document.querySelector("video");
    return {
      now: Math.round(performance.now()),
      scrollY: window.scrollY,
      visualViewport: window.visualViewport ? {
        height: window.visualViewport.height,
        offsetTop: window.visualViewport.offsetTop,
        pageTop: window.visualViewport.pageTop,
      } : null,
      bodyText: document.body ? document.body.innerText.slice(0, 1600) : "",
      video: video ? {
        currentSrc: video.currentSrc || video.getAttribute("src") || "",
        readyState: video.readyState,
        networkState: video.networkState,
        currentTime: Number((video.currentTime || 0).toFixed(3)),
        duration: Number.isFinite(video.duration) ? Number(video.duration.toFixed(3)) : String(video.duration),
        paused: video.paused,
        controls: video.controls,
        cls: String(video.className || ""),
        width: video.videoWidth || 0,
        height: video.videoHeight || 0,
        error: video.error ? {code: video.error.code, message: video.error.message} : null,
      } : null,
    };
  };
  return true;
})();
"""


def _click_button(driver: WebDriver, pattern: str) -> dict[str, Any]:
    return driver.script(
        """
        const regex = new RegExp(arguments[0], "i");
        const button = Array.from(document.querySelectorAll("button")).find((candidate) => regex.test(candidate.innerText || ""));
        if (!button) {
          return {clicked: false, buttons: Array.from(document.querySelectorAll("button")).map((candidate) => candidate.innerText)};
        }
        button.click();
        return {clicked: true, text: button.innerText};
        """,
        pattern,
    )


def _trace_summary(trace: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    blob_sources: list[str] = []
    for entry in trace:
        entry_type = str(entry.get("type"))
        counts[entry_type] = counts.get(entry_type, 0) + 1
        current_src = str(entry.get("currentSrc") or "")
        if current_src.startswith("blob:") and (not blob_sources or blob_sources[-1] != current_src):
            blob_sources.append(current_src)
    return {
        "event_counts": counts,
        "distinct_blob_sources": len(blob_sources),
        "media_load_calls": counts.get("media:load:call", 0),
        "emptied_events": counts.get("video:emptied", 0),
        "playing_events": counts.get("video:playing", 0),
        "timeupdate_events": counts.get("video:timeupdate", 0),
        "errors": [entry for entry in trace if str(entry.get("type", "")).endswith("error") or entry.get("error")],
    }


def _run_mode_check(
    *,
    base_url: str,
    cookie_name: str,
    token: str,
    geckodriver: str,
    item: dict[str, Any],
    mode: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    driver = WebDriver(geckodriver, width=390, height=844)
    driver.start()
    try:
        driver.wd("POST", "/url", {"url": base_url})
        time.sleep(0.5)
        driver.wd("POST", "/cookie", {"cookie": {"name": cookie_name, "value": token, "path": "/", "sameSite": "Lax"}})
        driver.wd("POST", "/url", {"url": f"{base_url.rstrip()}/library/{item['id']}"})
        time.sleep(3)
        driver.script(PROBE_SCRIPT)
        initial = driver.script("return window.__elvernSnapshot();")
        body_text = str(initial.get("bodyText") or "")
        for expected in ("Lite Playback", "Full Playback", "Open in VLC", "Open in Infuse"):
            if expected not in body_text:
                raise DeadcheckError(f"Detail page missing action {expected!r} for {mode} check.")

        click = _click_button(driver, "Lite Playback" if mode == "lite" else "Full Playback")
        if not click.get("clicked"):
            raise DeadcheckError(f"Could not click {mode} playback button: {click}")
        time.sleep(0.5)
        _click_button(driver, "Start from beginning")

        playable = False
        samples: list[dict[str, Any]] = []
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            time.sleep(1)
            snapshot = driver.script("return window.__elvernSnapshot();")
            samples.append(snapshot)
            video = snapshot.get("video") or {}
            if (
                float(video.get("currentTime") or 0) >= 2
                and int(video.get("readyState") or 0) >= 3
                and not bool(video.get("paused"))
            ):
                playable = True
                break
        before_interaction = driver.script("return window.__elvernSnapshot();")
        driver.script(
            """
            const video = document.querySelector("video");
            if (video) {
              video.dispatchEvent(new MouseEvent("click", {bubbles: true, cancelable: true, clientX: 20, clientY: 20}));
            }
            return true;
            """
        )
        time.sleep(1)
        after_interaction = driver.script("return window.__elvernSnapshot();")
        trace = driver.script("return window.__elvernTrace || [];")
        summary = _trace_summary(trace)

        if not playable:
            raise DeadcheckError(
                f"{mode} playback did not become playable within {timeout_seconds}s. "
                f"Last snapshot: {json.dumps(samples[-1] if samples else initial, sort_keys=True)}"
            )
        if summary["distinct_blob_sources"] > 2:
            raise DeadcheckError(f"{mode} playback source reset/remount thrash detected: {summary}")
        if summary["media_load_calls"] > 3 or summary["emptied_events"] > 3:
            raise DeadcheckError(f"{mode} playback load/reset thrash detected: {summary}")

        before_viewport = before_interaction.get("visualViewport") or {}
        after_viewport = after_interaction.get("visualViewport") or {}
        scroll_delta = abs(float(after_interaction.get("scrollY") or 0) - float(before_interaction.get("scrollY") or 0))
        viewport_delta = abs(float(after_viewport.get("pageTop") or 0) - float(before_viewport.get("pageTop") or 0))
        if scroll_delta > 2 or viewport_delta > 2:
            raise DeadcheckError(f"{mode} playback interaction jitter detected: scroll_delta={scroll_delta}, viewport_delta={viewport_delta}")

        final_video = after_interaction.get("video") or {}
        return {
            "item_id": item["id"],
            "title": item["title"],
            "source_kind": item.get("source_kind"),
            "playable": playable,
            "final_current_time": final_video.get("currentTime"),
            "final_ready_state": final_video.get("readyState"),
            "paused": final_video.get("paused"),
            "distinct_blob_sources": summary["distinct_blob_sources"],
            "media_load_calls": summary["media_load_calls"],
            "emptied_events": summary["emptied_events"],
            "playing_events": summary["playing_events"],
            "scroll_delta_after_interaction": scroll_delta,
            "viewport_delta_after_interaction": viewport_delta,
        }
    finally:
        driver.close()
        _stop_active_mobile_session(base_url, cookie_name, token)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Elvern core browser/runtime deadchecks.")
    parser.add_argument("--base-url", default=None, help="Frontend base URL. Defaults to local frontend.")
    parser.add_argument("--item-id", type=int, default=None, help="Use this visible item id for both Lite and Full.")
    parser.add_argument("--lite-item-id", type=int, default=None, help="Use this visible item id for Lite.")
    parser.add_argument("--full-item-id", type=int, default=None, help="Use this visible item id for Full.")
    parser.add_argument("--timeout", type=int, default=150, help="Seconds to wait for each mode to become playable.")
    parser.add_argument("--geckodriver", default=None, help="Path to geckodriver.")
    args = parser.parse_args()

    geckodriver = args.geckodriver or shutil.which("geckodriver")
    if not geckodriver:
        raise DeadcheckError("geckodriver is required for browser/runtime deadcheck.")

    settings, _, token = _settings_and_auth()
    base_url = (args.base_url or _local_frontend_url()).rstrip("/")
    _stop_active_mobile_session(base_url, settings.session_cookie_name, token)
    lite_item = _choose_visible_item(settings, base_url, settings.session_cookie_name, token, args.lite_item_id or args.item_id)
    full_item = _choose_visible_item(settings, base_url, settings.session_cookie_name, token, args.full_item_id or args.item_id)

    result = {
        "base_url": base_url,
        "detail_render": {
            "checked_via": "browser detail page before each playback mode",
            "required_actions": ["Lite Playback", "Full Playback", "Open in VLC", "Open in Infuse"],
        },
        "lite": _run_mode_check(
            base_url=base_url,
            cookie_name=settings.session_cookie_name,
            token=token,
            geckodriver=geckodriver,
            item=lite_item,
            mode="lite",
            timeout_seconds=args.timeout,
        ),
        "full": _run_mode_check(
            base_url=base_url,
            cookie_name=settings.session_cookie_name,
            token=token,
            geckodriver=geckodriver,
            item=full_item,
            mode="full",
            timeout_seconds=args.timeout,
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
