from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status

from ..auth import CurrentAdmin


router = APIRouter(prefix="/api/debug", tags=["debug"])

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PLAYBACK_DIAGNOSTICS_DIR = PROJECT_ROOT / "tmp" / "playback-diagnostics"
SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")
SECRET_KEY_RE = re.compile(r"(token|access_token|auth|authorization|signature|sig|key|secret|cookie)", re.IGNORECASE)


def _sanitize_filename_part(value: object, *, fallback: str) -> str:
    normalized = SAFE_FILENAME_RE.sub("-", str(value or "").strip()).strip("-._").lower()
    return normalized[:80] or fallback


def _redact_url_tokens(value: str) -> str:
    return re.sub(
        r"([?&][^=\s\"']*(?:token|access_token|auth|authorization|signature|sig|key|secret)[^=\s\"']*=)[^&\s\"']+",
        r"\1[redacted]",
        value,
        flags=re.IGNORECASE,
    )


def _redact_diagnostic_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if SECRET_KEY_RE.search(str(key)):
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = _redact_diagnostic_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_diagnostic_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_url_tokens(value)
    return value


def _extract_platform(diagnostics: dict[str, Any]) -> str:
    platform = diagnostics.get("platform")
    if isinstance(platform, dict):
        for key in ("detectedDesktopPlatform", "detectedClientPlatform"):
            value = platform.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return "unknown"


def _compact_summary(diagnostics: dict[str, Any], *, label: str, saved_path: Path) -> dict[str, Any]:
    hls_engine = diagnostics.get("hls_engine") if isinstance(diagnostics.get("hls_engine"), dict) else {}
    video = diagnostics.get("video") if isinstance(diagnostics.get("video"), dict) else {}
    manifest = diagnostics.get("manifest") if isinstance(diagnostics.get("manifest"), dict) else {}
    item = diagnostics.get("item") if isinstance(diagnostics.get("item"), dict) else {}
    platform = diagnostics.get("platform") if isinstance(diagnostics.get("platform"), dict) else {}
    time_ranges = diagnostics.get("time_ranges") if isinstance(diagnostics.get("time_ranges"), dict) else {}
    return {
        "saved_path": str(saved_path),
        "filename": saved_path.name,
        "label": label,
        "item_id": item.get("id"),
        "item_title": item.get("title"),
        "detected_client_platform": platform.get("detectedClientPlatform"),
        "detected_desktop_platform": platform.get("detectedDesktopPlatform"),
        "selected_engine": hls_engine.get("selectedEngine"),
        "native_hls_support": hls_engine.get("nativeHlsSupport"),
        "hls_js_supported": hls_engine.get("hlsJsSupported"),
        "hls_js_version": hls_engine.get("hlsJsVersion"),
        "video_duration": video.get("duration"),
        "seekable": time_ranges.get("seekable"),
        "buffered": time_ranges.get("buffered"),
        "manifest_playlist_type": manifest.get("playlist_type"),
        "manifest_classification": manifest.get("classification"),
        "manifest_contains_endlist": manifest.get("contains_endlist"),
    }


@router.post("/playback-diagnostics")
def save_playback_diagnostics(payload: dict[str, Any], request: Request, user=CurrentAdmin) -> dict[str, Any]:
    del request, user
    if payload.get("diagnostic_source") != "playback_debug_panel":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Playback diagnostics must come from the gated debug panel.",
        )
    raw_label = str(payload.get("label") or "").strip()
    if not raw_label:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A diagnostics label is required.")
    diagnostics = payload.get("diagnostics")
    if not isinstance(diagnostics, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Diagnostics payload must be an object.")

    label = _sanitize_filename_part(raw_label, fallback="unlabeled")
    platform = _sanitize_filename_part(_extract_platform(diagnostics), fallback="unknown")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    filename = f"{label}-{platform}-{timestamp}-{uuid4().hex[:8]}.json"
    PLAYBACK_DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        PLAYBACK_DIAGNOSTICS_DIR.chmod(0o700)
    except OSError:
        pass
    saved_path = PLAYBACK_DIAGNOSTICS_DIR / filename
    if saved_path.exists():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Diagnostics filename collision.")

    redacted_payload = {
        "diagnostic_source": "playback_debug_panel",
        "label": raw_label,
        "received_at_utc": datetime.now(timezone.utc).isoformat(),
        "diagnostics": _redact_diagnostic_payload(diagnostics),
    }
    saved_path.write_text(json.dumps(redacted_payload, indent=2, sort_keys=True) + "\n")
    try:
        saved_path.chmod(0o600)
    except OSError:
        pass
    summary = _compact_summary(redacted_payload["diagnostics"], label=raw_label, saved_path=saved_path)
    return {
        "saved": True,
        **summary,
    }
