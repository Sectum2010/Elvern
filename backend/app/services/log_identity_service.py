from __future__ import annotations

import hashlib
from urllib.parse import urlsplit


def stable_log_fingerprint(value: object, *, namespace: str, length: int = 12) -> str:
    normalized_value = str(value).strip()
    if not normalized_value:
        return "unknown"
    digest = hashlib.sha256(
        f"{namespace}:{normalized_value}".encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()
    return digest[:length]


def native_session_log_fingerprint(session_id: object) -> str:
    return stable_log_fingerprint(session_id, namespace="native-session", length=12)


def local_media_path_log_fingerprint(path: object) -> str:
    return stable_log_fingerprint(path, namespace="local-media-path", length=12)


def token_url_log_fingerprint(value: object) -> str:
    return stable_log_fingerprint(value, namespace="token-url", length=12)


def safe_url_origin_label(value: object) -> str:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    if parsed.scheme:
        return parsed.scheme
    return "unknown"


def safe_url_path_label(value: object) -> str:
    parsed = urlsplit(str(value or "").strip())
    path = parsed.path.strip()
    if parsed.scheme and parsed.netloc and not path:
        return parsed.netloc
    if not path or not path.startswith("/"):
        return "unknown"
    parts = [part for part in path.split("/") if part]
    if parts[:3] == ["api", "native-playback", "session"]:
        suffix = f"/{'/'.join(parts[4:])}" if len(parts) > 4 else ""
        return f"/api/native-playback/session/{{session_id}}{suffix}"
    if parts[:3] == ["api", "download", "sessions"]:
        suffix = f"/{'/'.join(parts[4:])}" if len(parts) > 4 else ""
        return f"/api/download/sessions/{{token}}{suffix}"
    if parts[:3] == ["api", "download", "session-stream"]:
        suffix = f"/{'/'.join(parts[4:])}" if len(parts) > 4 else ""
        return f"/api/download/session-stream/{{session_id}}{suffix}"
    if parts[:3] == ["api", "desktop-playback", "handoff"]:
        suffix = f"/{'/'.join(parts[4:])}" if len(parts) > 4 else ""
        return f"/api/desktop-playback/handoff/{{handoff_id}}{suffix}"
    return path


def redacted_url_log_label(value: object) -> str:
    parsed = urlsplit(str(value or "").strip())
    if not parsed.scheme:
        return "url_fingerprint=unknown origin=unknown path=unknown has_query=false"
    return (
        f"url_fingerprint={token_url_log_fingerprint(value)} "
        f"origin={safe_url_origin_label(value)} "
        f"path={safe_url_path_label(value)} "
        f"has_query={bool(parsed.query)}"
    )
