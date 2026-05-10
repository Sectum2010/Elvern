from __future__ import annotations

import re


DOWNLOAD_SESSION_URL_PATTERN = re.compile(r"(/api/download/sessions/)[^/?#\s]+")


def redact_download_session_urls(value: object) -> str:
    return DOWNLOAD_SESSION_URL_PATTERN.sub(r"\1[redacted]", str(value or ""))
