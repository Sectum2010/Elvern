from __future__ import annotations

from typing import Any


RANK_DEFINITIONS = (
    {
        "key": "diamond",
        "label": "Diamond",
        "min_score": 15,
        "description": "Reference-grade library copy with minimal compromise.",
    },
    {
        "key": "gold",
        "label": "Gold",
        "min_score": 11,
        "description": "Excellent quality, just below reference tier.",
    },
    {
        "key": "silver",
        "label": "Silver",
        "min_score": 7,
        "description": "Good quality, highly watchable.",
    },
    {
        "key": "iron",
        "label": "Iron",
        "min_score": 5,
        "description": "Decent but clearly compromised.",
    },
    {
        "key": "bronze",
        "label": "Bronze",
        "min_score": 3,
        "description": "Lower-quality convenience copy.",
    },
    {
        "key": "wood",
        "label": "Wood",
        "min_score": float("-inf"),
        "description": "Basic fallback copy.",
    },
)


def _row_value(row: Any, key: str, default: object = None) -> object:
    if isinstance(row, dict):
        return row.get(key, default)
    if hasattr(row, "keys") and key in row.keys():
        return row[key]
    return default


def _number(value: object) -> float:
    if value in {None, ""}:
        return 0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def _has_token(haystack: str, *tokens: str) -> bool:
    return any(token in haystack for token in tokens)


def _quality_haystack(row: Any) -> str:
    return " ".join(
        str(value)
        for value in (
            _row_value(row, "title"),
            _row_value(row, "original_filename"),
            _row_value(row, "video_codec"),
            _row_value(row, "audio_codec"),
            _row_value(row, "container"),
        )
        if value
    ).lower()


def _detect_source(haystack: str) -> tuple[float, str | None]:
    if _has_token(haystack, "remux"):
        return 6, "REMUX"
    if _has_token(haystack, "bluray", "blu-ray", "bdrip", "bdrip"):
        return 5, "BluRay"
    if _has_token(haystack, "web-dl", "webdl"):
        return 4, "WEB-DL"
    if _has_token(haystack, "webrip", "web-rip"):
        return 3, "WEBRip"
    if _has_token(haystack, "hdtv", "hdrip", "dvdrip"):
        return 2, "Legacy source"
    return 0, None


def _detect_resolution(row: Any, haystack: str) -> tuple[float, str | None]:
    width = _number(_row_value(row, "width"))
    height = _number(_row_value(row, "height"))
    if width >= 3800 or height >= 2100 or _has_token(haystack, "2160p", "4k", "uhd"):
        return 4, "2160p"
    if width >= 1900 or height >= 1000 or _has_token(haystack, "1080p"):
        return 3, "1080p"
    if width >= 1200 or height >= 700 or _has_token(haystack, "720p"):
        return 2, "720p"
    if _has_token(haystack, "480p", "576p"):
        return 1, "SD"
    return 0, None


def _detect_audio(haystack: str) -> tuple[float, str | None]:
    if _has_token(haystack, "atmos"):
        return 3, "Atmos"
    if _has_token(haystack, "truehd", "dts-hd", "dtshd", "master audio", "ma "):
        return 3, "TrueHD / DTS-HD"
    if _has_token(haystack, "dts"):
        return 2, "DTS"
    if _has_token(haystack, "ddp", "eac3", "ac3", "dolby digital"):
        return 1, "Dolby Digital"
    if _has_token(haystack, "aac"):
        return 0, "AAC"
    return 0, None


def _detect_codec(haystack: str) -> tuple[float, str | None]:
    if _has_token(haystack, "hevc", "x265", "h265"):
        return 1, "HEVC"
    if _has_token(haystack, "av1"):
        return 1, "AV1"
    if _has_token(haystack, "x264", "h264", "avc"):
        return 0, "AVC"
    return 0, None


def _detect_size(file_size: object) -> tuple[float, str | None]:
    gib = _number(file_size) / (1024**3)
    if gib >= 80:
        return 3, f"{int(gib + 0.5)} GB"
    if gib >= 50:
        return 2, f"{int(gib + 0.5)} GB"
    if gib >= 20:
        return 1, f"{int(gib + 0.5)} GB"
    if gib >= 8:
        return 0.5, f"{int(gib + 0.5)} GB"
    if 0 < gib < 2:
        return -1, f"{gib:.1f} GB"
    return 0, f"{gib:.1f} GB" if gib > 0 else None


def build_library_quality_rank(row: Any) -> dict[str, object]:
    haystack = _quality_haystack(row)
    source = _detect_source(haystack)
    resolution = _detect_resolution(row, haystack)
    audio = _detect_audio(haystack)
    codec = _detect_codec(haystack)
    size = _detect_size(_row_value(row, "file_size"))
    detected = [label for _, label in (source, resolution, audio, codec, size) if label]
    score = sum(value for value, _ in (source, resolution, audio, codec, size))
    rank = next(
        definition
        for definition in RANK_DEFINITIONS
        if score >= float(definition["min_score"])
    )
    description = str(rank["description"])
    return {
        "key": rank["key"],
        "label": rank["label"],
        "score": score,
        "description": description,
        "detected": detected,
        "tooltip": (
            f"{description} Detected: {' · '.join(detected)}."
            if detected
            else description
        ),
    }
