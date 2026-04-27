from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ..config import Settings
from ..db import get_connection, utcnow_iso
from .library_service import get_media_item_record


MEDIA_TECHNICAL_METADATA_VERSION = 1
HDR_TRANSFER_VALUES = {"smpte2084", "arib-std-b67"}
LOCAL_TECHNICAL_METADATA_BATCH_DEFAULT_LIMIT = 5
LOCAL_TECHNICAL_METADATA_BATCH_MAX_LIMIT = 25
KNOWN_8BIT_PIXEL_FORMATS = {
    "yuv420p",
    "yuvj420p",
    "nv12",
}
DV_CODEC_TAG_VALUES = {"dvhe", "dvh1"}
LOCAL_TECHNICAL_METADATA_FAILED_RETRY_AFTER_SECONDS = 6 * 60 * 60
TECHNICAL_METADATA_COLUMNS = (
    "metadata_version",
    "metadata_source",
    "probe_status",
    "probe_error",
    "probed_at",
    "updated_at",
    "source_fingerprint",
    "container",
    "duration_seconds",
    "bit_rate",
    "video_codec",
    "video_profile",
    "video_level",
    "pixel_format",
    "bit_depth",
    "width",
    "height",
    "color_transfer",
    "color_primaries",
    "color_space",
    "hdr_detected",
    "dolby_vision_detected",
    "audio_codec",
    "audio_profile",
    "audio_channels",
    "audio_channel_layout",
    "audio_sample_rate",
    "subtitle_count",
    "raw_probe_summary_json",
)


@dataclass(slots=True)
class _TechnicalMetadataBatchRuntime:
    run_lock: threading.Lock = field(default_factory=threading.Lock)
    state_lock: threading.Lock = field(default_factory=threading.Lock)
    running: bool = False
    current_item: dict[str, object] | None = None
    last_summary: dict[str, object] | None = None


_TECHNICAL_METADATA_BATCH_RUNTIMES: dict[str, _TechnicalMetadataBatchRuntime] = {}


def build_local_source_fingerprint(
    *,
    file_path: str | Path,
    file_size: int,
    file_mtime_ns: int,
) -> str:
    normalized_path = str(Path(file_path))
    payload = f"{normalized_path}|{int(file_size)}|{int(file_mtime_ns)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_local_source_fingerprint_from_path(file_path: str | Path) -> str:
    candidate = Path(file_path)
    stat = candidate.stat()
    return build_local_source_fingerprint(
        file_path=candidate,
        file_size=stat.st_size,
        file_mtime_ns=stat.st_mtime_ns,
    )


def _normalize_text(value: object) -> str | None:
    if value in {None, ""}:
        return None
    text = str(value).strip()
    return text or None


def _normalize_token(value: object) -> str | None:
    text = _normalize_text(value)
    if text is None:
        return None
    return text.lower()


def _coerce_int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: object) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_container(format_name: object) -> str | None:
    normalized = _normalize_token(format_name)
    if normalized is None:
        return None
    tokens = [token.strip() for token in normalized.split(",") if token.strip()]
    if not tokens:
        return None
    if any(token in {"mov", "mp4", "m4v", "m4a", "3gp", "3g2", "mj2"} for token in tokens):
        return "mp4"
    if "matroska" in tokens:
        return "mkv"
    if "webm" in tokens:
        return "webm"
    if "avi" in tokens:
        return "avi"
    if "mpegts" in tokens or "mpegtsraw" in tokens:
        return "ts"
    return tokens[0]


def _stream_sort_key(stream: dict[str, object]) -> tuple[int, int]:
    disposition = stream.get("disposition")
    default_flag = 0
    if isinstance(disposition, dict):
        default_flag = 1 if int(disposition.get("default", 0) or 0) == 1 else 0
    index = _coerce_int(stream.get("index"))
    return (0 if default_flag else 1, index if index is not None else 1_000_000)


def _pick_primary_stream(streams: list[dict[str, object]], codec_type: str) -> dict[str, object] | None:
    candidates = [
        stream
        for stream in streams
        if isinstance(stream, dict) and _normalize_token(stream.get("codec_type")) == codec_type
    ]
    if not candidates:
        return None
    return sorted(candidates, key=_stream_sort_key)[0]


def _derive_video_bit_depth(stream: dict[str, object]) -> int | None:
    bits_per_raw_sample = _coerce_int(stream.get("bits_per_raw_sample"))
    if bits_per_raw_sample is not None and bits_per_raw_sample > 0:
        return bits_per_raw_sample

    pixel_format = _normalize_token(stream.get("pix_fmt"))
    if pixel_format is None:
        return None
    if pixel_format in KNOWN_8BIT_PIXEL_FORMATS:
        return 8

    explicit_match = re.search(r"p(?P<bits>9|10|12|14|16)(?:le|be)?$", pixel_format)
    if explicit_match:
        return int(explicit_match.group("bits"))

    p0_match = re.match(r"p0(?P<bits>10|12|16)", pixel_format)
    if p0_match:
        return int(p0_match.group("bits"))

    return None


def _normalize_side_data_list(stream: dict[str, object]) -> list[dict[str, object]]:
    side_data_list = stream.get("side_data_list")
    if not isinstance(side_data_list, list):
        return []
    return [entry for entry in side_data_list if isinstance(entry, dict)]


def _detect_hdr(video_stream: dict[str, object] | None) -> bool | None:
    if video_stream is None:
        return None
    color_transfer = _normalize_token(video_stream.get("color_transfer"))
    if color_transfer in HDR_TRANSFER_VALUES:
        return True

    color_primaries = _normalize_token(video_stream.get("color_primaries"))
    if color_primaries is not None and color_primaries.startswith("bt2020"):
        for side_data in _normalize_side_data_list(video_stream):
            side_data_type = _normalize_token(side_data.get("side_data_type")) or ""
            if side_data_type in {"mastering display metadata", "content light level metadata"}:
                return True

    return False


def _detect_dolby_vision(video_stream: dict[str, object] | None) -> bool | None:
    if video_stream is None:
        return None

    codec_tag_string = _normalize_token(video_stream.get("codec_tag_string"))
    if codec_tag_string in DV_CODEC_TAG_VALUES:
        return True

    for side_data in _normalize_side_data_list(video_stream):
        side_data_type = _normalize_token(side_data.get("side_data_type")) or ""
        collapsed = side_data_type.replace(" ", "")
        if "dovi" in collapsed or "dolbyvision" in collapsed:
            return True
        if any(key.startswith("dv_") for key in side_data):
            return True

    return False


def _subtitle_count(streams: list[dict[str, object]]) -> int:
    return sum(
        1
        for stream in streams
        if isinstance(stream, dict) and _normalize_token(stream.get("codec_type")) == "subtitle"
    )


def _serialize_probe_summary(ffprobe_json: dict[str, object]) -> str | None:
    try:
        return json.dumps(
            ffprobe_json,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        return None


def parse_ffprobe_technical_metadata(ffprobe_json: dict) -> dict[str, object]:
    payload = ffprobe_json if isinstance(ffprobe_json, dict) else {}
    format_info = payload.get("format")
    format_info = format_info if isinstance(format_info, dict) else {}
    streams = payload.get("streams")
    streams = streams if isinstance(streams, list) else []

    video_stream = _pick_primary_stream(streams, "video")
    audio_stream = _pick_primary_stream(streams, "audio")

    result = {
        "metadata_version": MEDIA_TECHNICAL_METADATA_VERSION,
        "container": _normalize_container(format_info.get("format_name")),
        "duration_seconds": _coerce_float(format_info.get("duration")),
        "bit_rate": _coerce_int(format_info.get("bit_rate")),
        "video_codec": _normalize_token((video_stream or {}).get("codec_name")),
        "video_profile": _normalize_text((video_stream or {}).get("profile")),
        "video_level": _normalize_text((video_stream or {}).get("level")),
        "pixel_format": _normalize_token((video_stream or {}).get("pix_fmt")),
        "bit_depth": _derive_video_bit_depth(video_stream or {}),
        "width": _coerce_int((video_stream or {}).get("width")),
        "height": _coerce_int((video_stream or {}).get("height")),
        "color_transfer": _normalize_token((video_stream or {}).get("color_transfer")),
        "color_primaries": _normalize_token((video_stream or {}).get("color_primaries")),
        "color_space": _normalize_token((video_stream or {}).get("color_space")),
        "hdr_detected": _detect_hdr(video_stream),
        "dolby_vision_detected": _detect_dolby_vision(video_stream),
        "audio_codec": _normalize_token((audio_stream or {}).get("codec_name")),
        "audio_profile": _normalize_text((audio_stream or {}).get("profile")),
        "audio_channels": _coerce_int((audio_stream or {}).get("channels")),
        "audio_channel_layout": _normalize_text((audio_stream or {}).get("channel_layout")),
        "audio_sample_rate": _coerce_int((audio_stream or {}).get("sample_rate")),
        "subtitle_count": _subtitle_count(streams),
        "raw_probe_summary_json": _serialize_probe_summary(payload),
    }
    return result


def _parse_iso_to_epoch_seconds(value: object) -> float | None:
    text = _normalize_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.timestamp()


def get_technical_metadata(settings: Settings, media_item_id: int) -> dict[str, object] | None:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM media_item_technical_metadata
            WHERE media_item_id = ?
            LIMIT 1
            """,
            (int(media_item_id),),
        ).fetchone()
    return dict(row) if row is not None else None


def upsert_technical_metadata(
    settings: Settings,
    *,
    media_item_id: int,
    values: dict[str, object],
) -> dict[str, object]:
    existing = get_technical_metadata(settings, media_item_id)
    payload = {column: None for column in TECHNICAL_METADATA_COLUMNS}
    if existing is not None:
        for column in TECHNICAL_METADATA_COLUMNS:
            payload[column] = existing.get(column)

    for key, value in values.items():
        if key in TECHNICAL_METADATA_COLUMNS:
            payload[key] = value

    payload["metadata_version"] = int(
        payload.get("metadata_version") or MEDIA_TECHNICAL_METADATA_VERSION
    )
    payload["metadata_source"] = (
        str(payload.get("metadata_source") or "unknown").strip() or "unknown"
    )
    payload["probe_status"] = str(payload.get("probe_status") or "never").strip() or "never"
    payload["updated_at"] = str(payload.get("updated_at") or utcnow_iso())

    columns_sql = ", ".join(("media_item_id", *TECHNICAL_METADATA_COLUMNS))
    placeholders_sql = ", ".join("?" for _ in range(len(TECHNICAL_METADATA_COLUMNS) + 1))
    update_sql = ", ".join(f"{column} = excluded.{column}" for column in TECHNICAL_METADATA_COLUMNS)
    params = [int(media_item_id), *(payload[column] for column in TECHNICAL_METADATA_COLUMNS)]

    with get_connection(settings) as connection:
        connection.execute(
            f"""
            INSERT INTO media_item_technical_metadata ({columns_sql})
            VALUES ({placeholders_sql})
            ON CONFLICT(media_item_id) DO UPDATE SET
                {update_sql}
            """,
            params,
        )
        connection.commit()
    row = get_technical_metadata(settings, media_item_id)
    if row is None:
        raise ValueError("Technical metadata row could not be loaded after upsert")
    return row


def mark_technical_metadata_stale(
    settings: Settings,
    *,
    media_item_id: int,
    source_fingerprint: str | None = None,
    probe_error: str | None = None,
) -> dict[str, object]:
    return upsert_technical_metadata(
        settings,
        media_item_id=media_item_id,
        values={
            "metadata_version": MEDIA_TECHNICAL_METADATA_VERSION,
            "probe_status": "stale",
            "probe_error": probe_error,
            "source_fingerprint": source_fingerprint,
            "updated_at": utcnow_iso(),
        },
    )


def _current_local_source_fingerprint(item: dict[str, object]) -> str | None:
    file_path = Path(str(item.get("file_path") or ""))
    if not str(file_path).strip() or not file_path.exists() or not file_path.is_file():
        return None
    try:
        stat = file_path.stat()
    except OSError:
        return None
    return build_local_source_fingerprint(
        file_path=file_path,
        file_size=stat.st_size,
        file_mtime_ns=stat.st_mtime_ns,
    )


def _trusted_metadata_has_strategy_fields(
    item: dict[str, object],
    metadata_row: dict[str, object],
) -> bool:
    merged_container = _normalize_token(metadata_row.get("container") or item.get("container"))
    merged_video_codec = _normalize_token(metadata_row.get("video_codec") or item.get("video_codec"))
    merged_audio_codec = _normalize_token(metadata_row.get("audio_codec") or item.get("audio_codec"))
    merged_width = _coerce_int(metadata_row.get("width") if metadata_row.get("width") is not None else item.get("width"))
    merged_height = _coerce_int(metadata_row.get("height") if metadata_row.get("height") is not None else item.get("height"))
    merged_pixel_format = _normalize_token(
        metadata_row.get("pixel_format") if metadata_row.get("pixel_format") is not None else item.get("pixel_format")
    )
    merged_bit_depth = _coerce_int(
        metadata_row.get("bit_depth") if metadata_row.get("bit_depth") is not None else item.get("bit_depth")
    )
    merged_audio_channels = _coerce_int(
        metadata_row.get("audio_channels")
        if metadata_row.get("audio_channels") is not None
        else item.get("audio_channels")
    )

    if not merged_container or not merged_video_codec or not merged_audio_codec:
        return False
    if merged_width is None or merged_height is None:
        return False

    video_is_copy_candidate = merged_video_codec in {"h264", "avc", "avc1", "x264"}
    audio_is_copy_candidate = merged_audio_codec in {"aac", "aac_lc", "mp4a"}

    if video_is_copy_candidate and (not merged_pixel_format or merged_bit_depth is None):
        return False
    if audio_is_copy_candidate and merged_audio_channels is None:
        return False
    return True


def resolve_trusted_technical_metadata(
    settings: Settings,
    item: dict[str, object],
) -> dict[str, object] | None:
    if str(item.get("source_kind") or "local") != "local":
        return None

    media_item_id = item.get("id")
    if media_item_id in {None, ""}:
        return None

    metadata_row = get_technical_metadata(settings, int(media_item_id))
    if metadata_row is None:
        return None
    if str(metadata_row.get("probe_status") or "").strip() != "probed":
        return None
    if str(metadata_row.get("metadata_source") or "").strip() != "local_ffprobe":
        return None
    if int(metadata_row.get("metadata_version") or 0) != MEDIA_TECHNICAL_METADATA_VERSION:
        return None

    current_fingerprint = _current_local_source_fingerprint(item)
    stored_fingerprint = str(metadata_row.get("source_fingerprint") or "").strip()
    if current_fingerprint is None or not stored_fingerprint or current_fingerprint != stored_fingerprint:
        return None
    if not _trusted_metadata_has_strategy_fields(item, metadata_row):
        return None
    return metadata_row


def _technical_metadata_runtime(settings: Settings) -> _TechnicalMetadataBatchRuntime:
    key = str(settings.db_path.resolve())
    runtime = _TECHNICAL_METADATA_BATCH_RUNTIMES.get(key)
    if runtime is None:
        runtime = _TechnicalMetadataBatchRuntime()
        _TECHNICAL_METADATA_BATCH_RUNTIMES[key] = runtime
    return runtime


def _technical_metadata_batch_summary(*, limit: int, retry_failed: bool) -> dict[str, object]:
    return {
        "limit": int(limit),
        "retry_failed": bool(retry_failed),
        "scanned_candidates": 0,
        "probed": 0,
        "skipped": 0,
        "failed": 0,
        "stale": 0,
        "cloud_skipped": 0,
        "current_item": None,
        "errors": [],
    }


def _list_media_items_for_technical_metadata_batch(settings: Settings) -> list[dict[str, object]]:
    with get_connection(settings) as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                title,
                original_filename,
                file_path,
                COALESCE(source_kind, 'local') AS source_kind,
                library_source_id,
                external_media_id,
                cloud_mime_type,
                file_size,
                file_mtime,
                duration_seconds,
                width,
                height,
                video_codec,
                audio_codec,
                container,
                year,
                created_at,
                updated_at,
                last_scanned_at
            FROM media_items
            ORDER BY datetime(last_scanned_at) DESC, id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def run_local_technical_metadata_enrichment_batch(
    settings: Settings,
    *,
    limit: int = LOCAL_TECHNICAL_METADATA_BATCH_DEFAULT_LIMIT,
    retry_failed: bool = False,
    timeout_seconds: int = 30,
) -> dict[str, object]:
    effective_limit = int(limit)
    if effective_limit < 1 or effective_limit > LOCAL_TECHNICAL_METADATA_BATCH_MAX_LIMIT:
        raise ValueError(
            f"limit must be between 1 and {LOCAL_TECHNICAL_METADATA_BATCH_MAX_LIMIT}"
        )

    runtime = _technical_metadata_runtime(settings)
    summary = _technical_metadata_batch_summary(limit=effective_limit, retry_failed=retry_failed)
    processed = 0

    for item in _list_media_items_for_technical_metadata_batch(settings):
        summary["scanned_candidates"] += 1
        if str(item.get("source_kind") or "local") != "local":
            summary["cloud_skipped"] += 1
            continue

        eligible, reason = should_probe_local_item(
            settings,
            item,
            retry_failed=retry_failed,
        )
        if not eligible:
            summary["skipped"] += 1
            continue
        if processed >= effective_limit:
            break

        if reason in {"stale", "source_fingerprint_changed"}:
            summary["stale"] += 1
        current_item = {
            "id": int(item["id"]),
            "title": str(item.get("title") or ""),
        }
        summary["current_item"] = dict(current_item)
        with runtime.state_lock:
            runtime.current_item = dict(current_item)

        if reason == "source_fingerprint_changed":
            mark_technical_metadata_stale(
                settings,
                media_item_id=int(item["id"]),
                source_fingerprint=_current_local_source_fingerprint(item),
            )

        result = probe_local_item_technical_metadata(
            settings,
            item,
            timeout_seconds=timeout_seconds,
        )
        processed += 1
        if result["status"] == "probed":
            summary["probed"] += 1
        elif result["status"] == "failed":
            summary["failed"] += 1
            summary["errors"].append(
                f"{item.get('title') or item['id']}: {result.get('reason') or 'probe_failed'}"
            )
        else:
            summary["skipped"] += 1

    summary["current_item"] = None
    with runtime.state_lock:
        runtime.current_item = None
    return summary


def trigger_local_technical_metadata_enrichment_batch(
    settings: Settings,
    *,
    limit: int = LOCAL_TECHNICAL_METADATA_BATCH_DEFAULT_LIMIT,
    retry_failed: bool = False,
    timeout_seconds: int = 30,
) -> dict[str, object]:
    runtime = _technical_metadata_runtime(settings)
    if not runtime.run_lock.acquire(blocking=False):
        with runtime.state_lock:
            return {
                "started": False,
                "running": True,
                "summary": runtime.last_summary,
            }

    try:
        with runtime.state_lock:
            runtime.running = True
            runtime.current_item = None
        summary = run_local_technical_metadata_enrichment_batch(
            settings,
            limit=limit,
            retry_failed=retry_failed,
            timeout_seconds=timeout_seconds,
        )
        with runtime.state_lock:
            runtime.last_summary = dict(summary)
        return {
            "started": True,
            "running": False,
            "summary": summary,
        }
    finally:
        with runtime.state_lock:
            runtime.running = False
            runtime.current_item = None
        runtime.run_lock.release()


def get_local_technical_metadata_enrichment_status(settings: Settings) -> dict[str, object]:
    with get_connection(settings) as connection:
        local_counts = connection.execute(
            """
            SELECT
                COUNT(*) AS total_local_items,
                SUM(CASE WHEN t.probe_status = 'probed' THEN 1 ELSE 0 END) AS probed_local_items,
                SUM(CASE WHEN t.probe_status = 'stale' THEN 1 ELSE 0 END) AS stale_local_items,
                SUM(CASE WHEN t.probe_status = 'failed' THEN 1 ELSE 0 END) AS failed_local_items,
                SUM(CASE WHEN t.media_item_id IS NULL OR t.probe_status = 'never' THEN 1 ELSE 0 END) AS never_probed_local_items
            FROM media_items m
            LEFT JOIN media_item_technical_metadata t ON t.media_item_id = m.id
            WHERE COALESCE(m.source_kind, 'local') = 'local'
            """
        ).fetchone()
        cloud_count = connection.execute(
            """
            SELECT COUNT(*) AS cloud_items_not_supported
            FROM media_items
            WHERE COALESCE(source_kind, 'local') != 'local'
            """
        ).fetchone()

    runtime = _technical_metadata_runtime(settings)
    with runtime.state_lock:
        return {
            "total_local_items": int(local_counts["total_local_items"] or 0),
            "probed_local_items": int(local_counts["probed_local_items"] or 0),
            "stale_local_items": int(local_counts["stale_local_items"] or 0),
            "failed_local_items": int(local_counts["failed_local_items"] or 0),
            "never_probed_local_items": int(local_counts["never_probed_local_items"] or 0),
            "cloud_items_not_supported": int(cloud_count["cloud_items_not_supported"] or 0),
            "running": bool(runtime.running),
            "current_item": dict(runtime.current_item) if runtime.current_item else None,
            "last_summary": dict(runtime.last_summary) if runtime.last_summary else None,
        }


def should_probe_local_item(
    settings: Settings,
    item: dict[str, object],
    *,
    retry_failed: bool = False,
    failed_retry_after_seconds: int = LOCAL_TECHNICAL_METADATA_FAILED_RETRY_AFTER_SECONDS,
    now_epoch: float | None = None,
) -> tuple[bool, str]:
    if str(item.get("source_kind") or "local") != "local":
        return False, "cloud_source_not_supported"
    media_item_id = int(item["id"])
    existing = get_technical_metadata(settings, media_item_id)
    current_fingerprint = _current_local_source_fingerprint(item)

    if existing is None:
        if current_fingerprint is None:
            return True, "missing_local_file"
        return True, "no_metadata_row"

    if int(existing.get("metadata_version") or 0) != MEDIA_TECHNICAL_METADATA_VERSION:
        return True, "metadata_version_changed"

    probe_status = str(existing.get("probe_status") or "never").strip() or "never"
    if probe_status == "never":
        return True, "never_probed"
    if probe_status == "stale":
        return True, "stale"
    if probe_status == "probed":
        if current_fingerprint is None:
            return True, "missing_local_file"
        if str(existing.get("source_fingerprint") or "") != current_fingerprint:
            return True, "source_fingerprint_changed"
        return False, "fingerprint_unchanged"
    if probe_status == "failed":
        if retry_failed:
            return True, "retry_requested"
        failed_at_epoch = _parse_iso_to_epoch_seconds(existing.get("probed_at"))
        if failed_at_epoch is None:
            return False, "failed_backoff_active"
        effective_now_epoch = time.time() if now_epoch is None else now_epoch
        if (effective_now_epoch - failed_at_epoch) >= max(0, failed_retry_after_seconds):
            return True, "failed_backoff_elapsed"
        return False, "failed_backoff_active"

    return True, "unknown_probe_status"


def _probe_failure_result(
    settings: Settings,
    *,
    media_item_id: int,
    source_fingerprint: str | None,
    metadata_source: str,
    error_code: str,
) -> dict[str, object]:
    row = upsert_technical_metadata(
        settings,
        media_item_id=media_item_id,
        values={
            "metadata_version": MEDIA_TECHNICAL_METADATA_VERSION,
            "metadata_source": metadata_source,
            "probe_status": "failed",
            "probe_error": error_code,
            "probed_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
            "source_fingerprint": source_fingerprint,
        },
    )
    return {
        "status": "failed",
        "reason": error_code,
        "media_item_id": media_item_id,
        "technical_metadata": row,
    }


def probe_local_item_technical_metadata(
    settings: Settings,
    item: dict[str, object],
    *,
    timeout_seconds: int = 30,
) -> dict[str, object]:
    media_item_id = int(item["id"])
    if str(item.get("source_kind") or "local") != "local":
        return {
            "status": "skipped",
            "reason": "cloud_source_not_supported",
            "media_item_id": media_item_id,
        }

    file_path = Path(str(item.get("file_path") or ""))
    current_fingerprint = _current_local_source_fingerprint(item)
    if not settings.ffprobe_path:
        return _probe_failure_result(
            settings,
            media_item_id=media_item_id,
            source_fingerprint=current_fingerprint,
            metadata_source="local_ffprobe",
            error_code="ffprobe_unavailable",
        )
    if not str(file_path).strip() or not file_path.exists() or not file_path.is_file():
        return _probe_failure_result(
            settings,
            media_item_id=media_item_id,
            source_fingerprint=current_fingerprint,
            metadata_source="local_ffprobe",
            error_code="missing_file",
        )

    command = [
        str(settings.ffprobe_path),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(file_path),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_seconds)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _probe_failure_result(
            settings,
            media_item_id=media_item_id,
            source_fingerprint=current_fingerprint,
            metadata_source="local_ffprobe",
            error_code="timeout",
        )
    except (OSError, subprocess.SubprocessError):
        return _probe_failure_result(
            settings,
            media_item_id=media_item_id,
            source_fingerprint=current_fingerprint,
            metadata_source="local_ffprobe",
            error_code="ffprobe_exec_error",
        )

    if completed.returncode != 0:
        return _probe_failure_result(
            settings,
            media_item_id=media_item_id,
            source_fingerprint=current_fingerprint,
            metadata_source="local_ffprobe",
            error_code=f"ffprobe_exit_{completed.returncode}",
        )

    try:
        ffprobe_json = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return _probe_failure_result(
            settings,
            media_item_id=media_item_id,
            source_fingerprint=current_fingerprint,
            metadata_source="local_ffprobe",
            error_code="invalid_json",
        )

    parsed = parse_ffprobe_technical_metadata(ffprobe_json)
    row = upsert_technical_metadata(
        settings,
        media_item_id=media_item_id,
        values={
            **parsed,
            "metadata_version": MEDIA_TECHNICAL_METADATA_VERSION,
            "metadata_source": "local_ffprobe",
            "probe_status": "probed",
            "probe_error": None,
            "probed_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
            "source_fingerprint": current_fingerprint,
        },
    )
    return {
        "status": "probed",
        "reason": "probe_succeeded",
        "media_item_id": media_item_id,
        "technical_metadata": row,
    }


def run_one_local_technical_metadata_enrichment(
    settings: Settings,
    *,
    media_item_id: int | None = None,
    retry_failed: bool = False,
    timeout_seconds: int = 30,
) -> dict[str, object]:
    if media_item_id is not None:
        item = get_media_item_record(settings, item_id=int(media_item_id))
        if item is None:
            return {
                "status": "missing",
                "reason": "media_item_not_found",
                "media_item_id": int(media_item_id),
            }
        eligible, reason = should_probe_local_item(
            settings,
            item,
            retry_failed=retry_failed,
        )
        if not eligible:
            return {
                "status": "skipped",
                "reason": reason,
                "media_item_id": int(media_item_id),
            }
        if reason == "source_fingerprint_changed":
            mark_technical_metadata_stale(
                settings,
                media_item_id=int(media_item_id),
                source_fingerprint=_current_local_source_fingerprint(item),
            )
        return probe_local_item_technical_metadata(
            settings,
            item,
            timeout_seconds=timeout_seconds,
        )

    for item in _list_media_items_for_technical_metadata_batch(settings):
        if str(item.get("source_kind") or "local") != "local":
            continue
        eligible, reason = should_probe_local_item(
            settings,
            item,
            retry_failed=retry_failed,
        )
        if not eligible:
            continue
        if reason == "source_fingerprint_changed":
            mark_technical_metadata_stale(
                settings,
                media_item_id=int(item["id"]),
                source_fingerprint=_current_local_source_fingerprint(item),
            )
        return probe_local_item_technical_metadata(
            settings,
            item,
            timeout_seconds=timeout_seconds,
        )

    return {
        "status": "skipped",
        "reason": "no_eligible_local_items",
        "media_item_id": None,
    }
