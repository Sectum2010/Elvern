from __future__ import annotations

import hashlib


def stable_log_fingerprint(value: object, *, namespace: str, length: int = 12) -> str:
    normalized_value = str(value).strip()
    if not normalized_value:
        return "unknown"
    digest = hashlib.sha256(f"{namespace}:{normalized_value}".encode("utf-8")).hexdigest()
    return digest[:length]


def native_session_log_fingerprint(session_id: object) -> str:
    return stable_log_fingerprint(session_id, namespace="native-session", length=12)
