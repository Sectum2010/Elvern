from __future__ import annotations

import hashlib
import json
import os
from pathlib import PurePath
from typing import Any, Iterable
from urllib.parse import urlsplit


MAX_COMMAND_TOKENS = 96
MAX_COMMAND_TOKEN_BYTES = 128


def ffmpeg_command_shape(command: Iterable[object]) -> tuple[str, ...]:
    """Return a bounded, secret-free command shape without hashing on playback paths."""

    normalized: list[str] = []
    redact_next = False
    for index, raw in enumerate(command):
        if index >= MAX_COMMAND_TOKENS:
            normalized.append("<truncated>")
            break
        token = str(raw)
        if redact_next:
            normalized.append("<redacted-value>")
            redact_next = False
            continue
        if token in {"-i", "-headers", "-http_proxy", "-cookies"}:
            normalized.append(token)
            redact_next = True
            continue
        split = urlsplit(token)
        if split.scheme in {"http", "https"}:
            normalized.append("<url>")
        elif os.path.isabs(token) or token.startswith(("~/", "./", "../")):
            normalized.append(f"<path:{PurePath(token).suffix.lower() or 'none'}>")
        elif index == 0:
            normalized.append(PurePath(token).name[:MAX_COMMAND_TOKEN_BYTES])
        else:
            normalized.append(token[:MAX_COMMAND_TOKEN_BYTES])
    return tuple(normalized)


def ffmpeg_command_fingerprint(command: Iterable[object]) -> str:
    return fingerprint_command_shape(ffmpeg_command_shape(command))


def fingerprint_command_shape(command_shape: Iterable[object]) -> str:
    bounded = tuple(str(token)[:MAX_COMMAND_TOKEN_BYTES] for token in command_shape)[
        :MAX_COMMAND_TOKENS
    ]
    encoded = json.dumps(bounded, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def normalize_deferred_observation(observation: dict[str, Any]) -> dict[str, Any]:
    """Perform diagnostics-only derived work after the ingress queue boundary."""

    payload = dict(observation.get("payload") or {})
    command_shape = payload.pop("diagnostics_command_shape", None)
    if isinstance(command_shape, (tuple, list)):
        payload["command_fingerprint"] = fingerprint_command_shape(command_shape)
    return {**observation, "payload": payload}
