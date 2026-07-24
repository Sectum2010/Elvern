#!/usr/bin/env python3
"""Normalize a desktop Helper origin for the release build host."""

from __future__ import annotations

import hashlib
import sys
from urllib.parse import urlsplit


def normalize_origin(value: str) -> str:
    candidate = value.strip()
    if (
        not candidate
        or candidate != value
        or "%" in candidate
        or any(
            ord(character) > 127
            or ord(character) < 32
            or ord(character) == 127
            for character in candidate
        )
    ):
        raise ValueError("origin is invalid")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("origin is invalid") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("origin must be an exact absolute HTTP(S) origin")
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    if any(label.startswith("xn--") for label in host.split(".")):
        raise ValueError("IDN hostnames are unsupported")
    if ":" in host:
        host = f"[{host}]"
    default_port = 80 if scheme == "http" else 443
    return f"{scheme}://{host}" + (
        "" if port in {None, default_port} else f":{port}"
    )


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: normalize-origin.py <origin>", file=sys.stderr)
        return 2
    try:
        origin = normalize_origin(sys.argv[1])
    except ValueError as exc:
        print(f"ELVERN_BACKEND_ORIGIN {exc}", file=sys.stderr)
        return 1
    print(origin)
    print(hashlib.sha256(origin.encode("utf-8")).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
