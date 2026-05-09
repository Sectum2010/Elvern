"""
Hostname-restricted HTTP client for Google API calls.
Blocks redirects to non-allowlisted hosts to prevent SSRF.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Google API, OAuth, and Drive media hosts. Drive file downloads may redirect to
# googleusercontent.com CDN hosts, so those are explicitly allowed.
GOOGLE_ALLOWED_HOST_SUFFIXES = (
    ".googleapis.com",
    ".google.com",
    ".googleusercontent.com",
    ".gstatic.com",
)

MAX_REDIRECTS = 5


class HostNotAllowedError(urllib.error.URLError):
    def __init__(self, host: str):
        super().__init__(f"Redirect to disallowed host: {host}")
        self.host = host


def _host_allowed(host: str) -> bool:
    if not isinstance(host, str):
        return False
    if not host:
        return False
    host = host.lower().strip()
    for suffix in GOOGLE_ALLOWED_HOST_SUFFIXES:
        bare = suffix.lstrip(".")
        if host == bare:
            return True
        if host.endswith(suffix):
            return True
    return False


class _RestrictedRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        host = (parsed.hostname or "").lower()
        if not _host_allowed(host):
            logger.warning(
                "Blocked redirect from %s to disallowed host %s (status %s)",
                req.full_url,
                host,
                code,
            )
            raise HostNotAllowedError(host or "<empty>")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_safe_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_RestrictedRedirectHandler())


def safe_urlopen(url, data=None, timeout=None):
    """
    Drop-in replacement for urllib.request.urlopen with host allowlist.

    The initial URL host and every redirect target host must match the Google
    allowlist.
    """
    if isinstance(url, urllib.request.Request):
        initial_url = url.full_url
    else:
        initial_url = url
    parsed = urlparse(initial_url)
    host = (parsed.hostname or "").lower()
    if not _host_allowed(host):
        raise HostNotAllowedError(host or "<empty>")
    opener = build_safe_opener()
    return opener.open(url, data=data, timeout=timeout)
