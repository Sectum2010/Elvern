from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError

import pytest

from backend.app.services import _safe_http
from backend.app.services._safe_http import HostNotAllowedError, safe_urlopen


class _RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        base = f"http://127.0.0.1:{self.server.server_port}"
        if self.path == "/ok":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if self.path == "/redirect-allowed":
            self.send_response(302)
            self.send_header("Location", f"{base}/ok")
            self.end_headers()
            return
        if self.path == "/chain-1":
            self.send_response(302)
            self.send_header("Location", f"{base}/chain-2")
            self.end_headers()
            return
        if self.path == "/chain-2":
            self.send_response(302)
            self.send_header("Location", f"{base}/ok")
            self.end_headers()
            return
        if self.path == "/redirect-blocked":
            self.send_response(302)
            self.send_header("Location", "http://evil.example.com/steal")
            self.end_headers()
            return
        if self.path == "/middle-1":
            self.send_response(302)
            self.send_header("Location", f"{base}/middle-2")
            self.end_headers()
            return
        if self.path == "/middle-2":
            self.send_response(302)
            self.send_header("Location", "http://evil.example.com/second-hop")
            self.end_headers()
            return
        if self.path.startswith("/loop/"):
            try:
                current = int(self.path.rsplit("/", 1)[-1])
            except ValueError:
                current = 0
            self.send_response(302)
            self.send_header("Location", f"{base}/loop/{current + 1}")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):  # noqa: A002
        return None


@pytest.fixture()
def redirect_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class TestHostAllowlist:
    def test_googleapis_subdomain_allowed(self):
        assert _safe_http._host_allowed("www.googleapis.com")

    def test_googleusercontent_subdomain_allowed(self):
        assert _safe_http._host_allowed("drive.usercontent.googleusercontent.com")

    def test_random_domain_blocked(self):
        assert not _safe_http._host_allowed("example.com")

    def test_attacker_subdomain_of_legit_blocked(self):
        assert not _safe_http._host_allowed("googleapis.com.evil.com")

    def test_empty_host_blocked(self):
        assert not _safe_http._host_allowed("")

    def test_case_insensitive_match(self):
        assert _safe_http._host_allowed("WWW.GOOGLEAPIS.COM")

    def test_non_string_blocked_safely(self):
        assert not _safe_http._host_allowed(None)


class TestRestrictedRedirectHandler:
    @pytest.fixture(autouse=True)
    def allow_local_mock_server(self, monkeypatch):
        def host_allowed(host):
            return host == "127.0.0.1"

        monkeypatch.setattr(_safe_http, "_host_allowed", host_allowed)

    def test_initial_url_to_blocked_host_raises(self):
        with pytest.raises(HostNotAllowedError):
            safe_urlopen("http://evil.example.com/")

    def test_redirect_to_allowed_host_succeeds(self, redirect_server):
        with safe_urlopen(f"{redirect_server}/redirect-allowed", timeout=5) as response:
            assert response.status == 200
            assert response.read() == b"ok"

    def test_redirect_to_blocked_host_raises(self, redirect_server):
        with pytest.raises(HostNotAllowedError):
            safe_urlopen(f"{redirect_server}/redirect-blocked", timeout=5)

    def test_redirect_chain_within_allowlist_succeeds(self, redirect_server):
        with safe_urlopen(f"{redirect_server}/chain-1", timeout=5) as response:
            assert response.status == 200
            assert response.read() == b"ok"

    def test_excessive_redirects_blocked(self, redirect_server):
        with pytest.raises(HTTPError):
            safe_urlopen(f"{redirect_server}/loop/0", timeout=5)

    def test_redirect_then_blocked_in_middle(self, redirect_server):
        with pytest.raises(HostNotAllowedError):
            safe_urlopen(f"{redirect_server}/middle-1", timeout=5)
