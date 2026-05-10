"""Pure helpers for the native-HLS server-side sliding manifest window.

Phase 2 contract:
  * Browser Playback sessions backed by Safari/WebKit native HLS cannot be
    pruned client-side via hls.js SourceBuffer APIs. The Elvern custom timeline
    still shows the full movie duration, but the active manifest window
    represents:

        [max(0, anchor - 120),  min(duration, anchor + forward_window_seconds)]

    where ``anchor`` is the current playback position (or attach/target
    position when playback hasn't started yet), the back window is locked at
    120 seconds universally, and the forward window is the locked buffer-tier
    target.

  * This module is pure: every function takes inputs and returns values.
    No session state is mutated and no I/O is performed. The runtime
    orchestrator calls these helpers when projecting a snapshot and (in
    follow-up work) when deciding to refresh the manifest URL.

The constants below intentionally mirror the values in
``mobile_playback_models`` and ``mobile_playback_buffer_contract`` so a single
import of this module is enough to compute the entire window contract; the
existing locked constants remain the source of truth and any future change to
them must update both modules together.
"""

from __future__ import annotations

from typing import Final, Mapping

from .mobile_playback_buffer_contract import (
    CLIENT_BACK_BUFFER_SECONDS,
    ROUTE2_FULL_BAD_CONDITION_BUFFER_SECONDS,
)
from .mobile_playback_models import (
    ROUTE2_FULL_FAST_START_RUNWAY_SECONDS,
    ROUTE2_LITE_FAST_START_RUNWAY_SECONDS,
    ROUTE2_LITE_SLOW_START_RUNWAY_SECONDS,
    ROUTE2_LITE_UNDERSUPPLY_START_RUNWAY_SECONDS,
)


NATIVE_HLS_BACK_WINDOW_SECONDS: Final[float] = float(CLIENT_BACK_BUFFER_SECONDS)
NATIVE_HLS_WINDOW_POLICY: Final[str] = "native_hls_sliding_window_v1"
NATIVE_HLS_ENGINE_LABEL: Final[str] = "native_hls"
HLS_JS_ENGINE_LABEL: Final[str] = "hls_js"
WINDOW_EDGE_REFRESH_RUNWAY_SECONDS: Final[float] = 20.0
WINDOW_ANCHOR_DRIFT_REFRESH_SECONDS: Final[float] = 10.0


BUFFER_TIER_FORWARD_SECONDS: Final[Mapping[str, float]] = {
    "lite_fast": float(ROUTE2_LITE_FAST_START_RUNWAY_SECONDS),
    "lite_uncertain": float(ROUTE2_LITE_SLOW_START_RUNWAY_SECONDS),
    "lite_undersupply": float(ROUTE2_LITE_UNDERSUPPLY_START_RUNWAY_SECONDS),
    "full_healthy": float(ROUTE2_FULL_FAST_START_RUNWAY_SECONDS),
    "full_bad_condition": float(ROUTE2_FULL_BAD_CONDITION_BUFFER_SECONDS),
}


def _coerce_finite_seconds(value: object, fallback: float = 0.0) -> float:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return fallback
    return numeric


def is_native_hls_engine(selected_hls_engine: str | None) -> bool:
    """Return True when the client-selected engine is Safari/WebKit native HLS."""
    if not isinstance(selected_hls_engine, str):
        return False
    return selected_hls_engine.strip().lower() == NATIVE_HLS_ENGINE_LABEL


def client_back_buffer_prune_supported(selected_hls_engine: str | None) -> bool:
    """Native-HLS sessions cannot be hard-pruned by hls.js; only hls.js can.

    Returns ``True`` only when we have positive evidence the engine is hls.js.
    Unknown / legacy / native engines all return ``False`` so the rest of the
    system never falsely claims it can prune the back buffer.
    """
    if not isinstance(selected_hls_engine, str):
        return False
    normalized = selected_hls_engine.strip().lower()
    return normalized == HLS_JS_ENGINE_LABEL


def compute_window_forward_seconds(
    *,
    buffer_tier: str | None,
    playback_mode: str | None = None,
) -> float:
    """Map a (buffer_tier, playback_mode) pair to the locked forward window.

    Falls back to a safe default per playback mode when the tier label is
    missing or unrecognised: ``lite`` falls back to ``lite_uncertain`` (45 s)
    and ``full`` falls back to ``full_healthy`` (120 s).
    """
    normalized_tier = (buffer_tier or "").strip().lower()
    if normalized_tier in BUFFER_TIER_FORWARD_SECONDS:
        return BUFFER_TIER_FORWARD_SECONDS[normalized_tier]
    mode = (playback_mode or "").strip().lower()
    if mode == "full":
        return BUFFER_TIER_FORWARD_SECONDS["full_healthy"]
    return BUFFER_TIER_FORWARD_SECONDS["lite_uncertain"]


def resolve_window_anchor_seconds(
    *,
    current_position_seconds: object = None,
    target_position_seconds: object = None,
    attach_position_seconds: object = None,
) -> float:
    """Pick the strongest non-zero anchor for the window.

    Preference order:
      1. ``current_position_seconds`` if positive (live playback).
      2. ``target_position_seconds`` if positive (post-seek pre-attach).
      3. ``attach_position_seconds`` if positive (initial attach).
      4. ``0.0`` when nothing else is known.
    """
    current = _coerce_finite_seconds(current_position_seconds)
    if current > 0.0:
        return current
    target = _coerce_finite_seconds(target_position_seconds)
    if target > 0.0:
        return target
    attach = _coerce_finite_seconds(attach_position_seconds)
    if attach > 0.0:
        return attach
    return 0.0


def compute_native_hls_window(
    *,
    anchor_seconds: object,
    duration_seconds: object,
    buffer_tier: str | None,
    playback_mode: str | None = None,
    back_window_seconds: float = NATIVE_HLS_BACK_WINDOW_SECONDS,
) -> dict[str, object]:
    """Project the active sliding window for native-HLS Browser Playback.

    Returns a dict with keys:

      * ``active_window_start_seconds``
      * ``active_window_end_seconds``
      * ``active_window_back_seconds``
      * ``active_window_forward_seconds``
      * ``active_window_anchor_seconds``
      * ``active_window_policy``

    The window is clamped into ``[0, duration_seconds]``. ``back`` and
    ``forward`` are the *contract* values (always 120 / tier target); the
    actual window ends may be shorter when clamped against duration / 0.
    """
    anchor = max(0.0, _coerce_finite_seconds(anchor_seconds))
    duration = max(0.0, _coerce_finite_seconds(duration_seconds))
    back = max(0.0, float(back_window_seconds))
    forward = compute_window_forward_seconds(
        buffer_tier=buffer_tier,
        playback_mode=playback_mode,
    )
    if duration > 0.0:
        clamped_anchor = min(anchor, duration)
    else:
        clamped_anchor = anchor
    raw_start = clamped_anchor - back
    raw_end = clamped_anchor + forward
    if duration > 0.0:
        end = max(0.0, min(duration, raw_end))
    else:
        end = max(0.0, raw_end)
    start = max(0.0, raw_start)
    if end < start:
        end = start
    return {
        "active_window_start_seconds": round(start, 2),
        "active_window_end_seconds": round(end, 2),
        "active_window_back_seconds": round(back, 2),
        "active_window_forward_seconds": round(forward, 2),
        "active_window_anchor_seconds": round(clamped_anchor, 2),
        "active_window_policy": NATIVE_HLS_WINDOW_POLICY,
    }


def is_position_in_active_window(
    position_seconds: object,
    *,
    window_start_seconds: object,
    window_end_seconds: object,
    headroom_seconds: float = 0.0,
) -> bool:
    """Inclusive containment check honouring an optional trailing headroom."""
    position = _coerce_finite_seconds(position_seconds, fallback=float("nan"))
    if position != position:
        return False
    start = _coerce_finite_seconds(window_start_seconds)
    end = _coerce_finite_seconds(window_end_seconds)
    if end <= start:
        return False
    safe_headroom = max(0.0, float(headroom_seconds))
    return start <= position <= max(start, end - safe_headroom)


def should_refresh_native_hls_window(
    *,
    current_position_seconds: object,
    window_start_seconds: object,
    window_end_seconds: object,
    window_anchor_seconds: object = None,
    seek_target_seconds: object = None,
    buffer_tier_changed: bool = False,
    edge_runway_seconds: float = WINDOW_EDGE_REFRESH_RUNWAY_SECONDS,
    anchor_drift_seconds: float = WINDOW_ANCHOR_DRIFT_REFRESH_SECONDS,
) -> dict[str, object]:
    """Decide whether the active window must be refreshed.

    Refresh triggers (any one of these is sufficient):

      * The current playhead is within ``edge_runway_seconds`` of the window
        end (default 20 s). Without refresh the manifest would run out.
      * A pending seek target is outside the current window.
      * The current playhead has drifted more than ``anchor_drift_seconds``
        away from the window anchor (default 10 s) — typically because the
        user seeked back to retained history.
      * The buffer tier (``lite_fast`` ↔ ``full_bad_condition`` etc.) changed
        materially, which changes the contracted forward window length.

    Returns ``{"should_refresh": bool, "reason": str | None}``.
    """
    current = _coerce_finite_seconds(current_position_seconds, fallback=float("nan"))
    end = _coerce_finite_seconds(window_end_seconds)
    start = _coerce_finite_seconds(window_start_seconds)
    if buffer_tier_changed:
        return {"should_refresh": True, "reason": "buffer_tier_changed"}
    if seek_target_seconds is not None:
        seek_target = _coerce_finite_seconds(seek_target_seconds, fallback=float("nan"))
        if seek_target == seek_target and not is_position_in_active_window(
            seek_target,
            window_start_seconds=start,
            window_end_seconds=end,
        ):
            return {"should_refresh": True, "reason": "seek_target_outside_window"}
    if current != current:
        return {"should_refresh": False, "reason": None}
    if end > start and (end - current) <= max(0.0, float(edge_runway_seconds)):
        return {"should_refresh": True, "reason": "approaching_window_end"}
    if window_anchor_seconds is not None:
        anchor = _coerce_finite_seconds(window_anchor_seconds, fallback=float("nan"))
        if anchor == anchor and abs(current - anchor) > max(0.0, float(anchor_drift_seconds)):
            return {"should_refresh": True, "reason": "anchor_drift"}
    return {"should_refresh": False, "reason": None}


def slice_manifest_segments_for_window(
    *,
    manifest_end_segment: int,
    segment_duration_seconds: float,
    epoch_start_seconds: float,
    window_start_seconds: object,
    window_end_seconds: object,
) -> dict[str, object]:
    """Compute the segment slice that satisfies a sliding active window.

    Inputs describe the published epoch (segment 0 starts at
    ``epoch_start_seconds``; ``manifest_end_segment`` is the last contiguous
    segment Route2 has published) and the target window in absolute movie
    seconds.

    Returns a dict with keys:

      * ``first_segment_index``     — first index to include in the playlist.
      * ``last_segment_index``      — last index to include (inclusive).
      * ``media_sequence_number``   — value for ``#EXT-X-MEDIA-SEQUENCE``;
                                      always equal to ``first_segment_index``.
      * ``first_segment_start_seconds`` — absolute start time of that segment;
                                      callers use this to recompute
                                      ``#EXT-X-START:TIME-OFFSET``.

    Slice rules:
      * The first segment kept is the one whose end time is strictly greater
        than ``window_start_seconds`` — so the playhead at ``window_start``
        is always inside a listed segment.
      * The last segment kept is the one whose start time is strictly less
        than ``window_end_seconds`` — so the listed playlist contains every
        segment the playhead might enter inside the window.
      * The slice is then clamped to ``[0, manifest_end_segment]``.

    If the inputs do not yield any included segment (e.g. window entirely
    behind segment 0), the slice degenerates to a single-segment playlist
    starting at ``max(0, manifest_end_segment)`` so HLS clients still have
    something to attach to. (The runtime refresh trigger should have moved
    the window before this happens; the degeneration is a safety net.)
    """
    safe_end = max(0, int(manifest_end_segment))
    seg_duration = max(0.001, float(segment_duration_seconds))
    epoch_start = max(0.0, _coerce_finite_seconds(epoch_start_seconds))
    win_start = _coerce_finite_seconds(window_start_seconds)
    win_end = _coerce_finite_seconds(window_end_seconds)
    if win_end <= win_start:
        first = safe_end
        last = safe_end
        seg_start = epoch_start + first * seg_duration
        return {
            "first_segment_index": first,
            "last_segment_index": last,
            "media_sequence_number": first,
            "first_segment_start_seconds": round(seg_start, 3),
        }
    relative_start = max(0.0, win_start - epoch_start)
    relative_end = max(relative_start, win_end - epoch_start)
    first_candidate = int(relative_start // seg_duration)
    last_candidate = int(relative_end // seg_duration)
    if (first_candidate + 1) * seg_duration <= relative_start + 1e-6:
        first_candidate += 1
    if last_candidate * seg_duration >= relative_end - 1e-6 and last_candidate > 0:
        last_candidate -= 1
    first = max(0, min(safe_end, first_candidate))
    last = max(first, min(safe_end, last_candidate))
    seg_start = epoch_start + first * seg_duration
    return {
        "first_segment_index": first,
        "last_segment_index": last,
        "media_sequence_number": first,
        "first_segment_start_seconds": round(seg_start, 3),
    }


def render_route2_epoch_manifest_text(
    *,
    epoch_start_seconds: float,
    attach_position_seconds: float,
    manifest_end_segment: int,
    duration_seconds: float,
    segment_duration_seconds: float,
    manifest_complete: bool,
    window_start_seconds: float | None = None,
    window_end_seconds: float | None = None,
    init_uri: str = "init.mp4",
    segment_uri_template: str = "segments/{index}.m4s",
) -> str:
    """Render the Route2 epoch ``.m3u8`` body.

    When ``window_start_seconds`` and ``window_end_seconds`` are provided the
    output is a sliced playlist starting at the segment whose end is past
    ``window_start_seconds`` (so the playhead at window_start is inside a
    listed segment). When they are ``None`` (or the window is degenerate) the
    full epoch playlist is emitted from segment 0 — that's the hls.js / legacy
    path and is preserved unchanged.

    The renderer is pure: no I/O, no session state. It is callable from
    `MobilePlaybackManager.get_route2_epoch_manifest_content` after the lock
    setup, and from focused unit tests that don't need a live manager.
    """
    import math

    safe_segment_duration = max(0.001, float(segment_duration_seconds))
    safe_end_segment = max(0, int(manifest_end_segment))
    epoch_start = max(0.0, _coerce_finite_seconds(epoch_start_seconds))
    duration = max(0.0, _coerce_finite_seconds(duration_seconds))
    attach_abs = max(0.0, _coerce_finite_seconds(attach_position_seconds))
    use_window = (
        window_start_seconds is not None
        and window_end_seconds is not None
        and float(window_end_seconds) > float(window_start_seconds)
    )
    if use_window:
        slice_result = slice_manifest_segments_for_window(
            manifest_end_segment=safe_end_segment,
            segment_duration_seconds=safe_segment_duration,
            epoch_start_seconds=epoch_start,
            window_start_seconds=window_start_seconds,
            window_end_seconds=window_end_seconds,
        )
        first_index = int(slice_result["first_segment_index"])
        media_sequence = int(slice_result["media_sequence_number"])
        first_segment_start = float(slice_result["first_segment_start_seconds"])
    else:
        first_index = 0
        media_sequence = 0
        first_segment_start = epoch_start
    relative_attach_offset = max(0.0, attach_abs - first_segment_start)
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:7",
        f"#EXT-X-TARGETDURATION:{math.ceil(safe_segment_duration)}",
        f"#EXT-X-MEDIA-SEQUENCE:{media_sequence}",
        "#EXT-X-PLAYLIST-TYPE:EVENT",
        "#EXT-X-INDEPENDENT-SEGMENTS",
        f'#EXT-X-MAP:URI="{init_uri}"',
        f"#EXT-X-START:TIME-OFFSET={relative_attach_offset:.3f},PRECISE=YES",
    ]
    for index in range(first_index, safe_end_segment + 1):
        segment_start = epoch_start + index * safe_segment_duration
        remaining = max(0.0, duration - segment_start) if duration > 0 else safe_segment_duration
        seg_extinf = min(safe_segment_duration, remaining) if duration > 0 else safe_segment_duration
        lines.append(f"#EXTINF:{seg_extinf:.3f},")
        lines.append(segment_uri_template.format(index=index))
    if manifest_complete:
        lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def build_active_window_snapshot_fields(
    *,
    selected_hls_engine: str | None,
    duration_seconds: object,
    buffer_tier: str | None,
    playback_mode: str | None,
    current_position_seconds: object = None,
    target_position_seconds: object = None,
    attach_position_seconds: object = None,
    active_window_revision: object = None,
    active_window_reason: str | None = None,
) -> dict[str, object]:
    """Assemble the additive snapshot fields the frontend consumes.

    Always present (so the frontend contract is stable across engines):

      * ``selected_hls_engine``
      * ``active_window_*``
      * ``active_window_revision``
      * ``active_window_reason``
      * ``native_hls_window_policy``
      * ``client_back_buffer_prune_supported``
      * ``full_duration_seconds``
      * ``attach_position_seconds`` (echoed for telemetry parity)
      * ``target_position_seconds`` (echoed for telemetry parity)
    """
    anchor = resolve_window_anchor_seconds(
        current_position_seconds=current_position_seconds,
        target_position_seconds=target_position_seconds,
        attach_position_seconds=attach_position_seconds,
    )
    window = compute_native_hls_window(
        anchor_seconds=anchor,
        duration_seconds=duration_seconds,
        buffer_tier=buffer_tier,
        playback_mode=playback_mode,
    )
    duration = max(0.0, _coerce_finite_seconds(duration_seconds))
    revision_value: object
    if active_window_revision is None:
        revision_value = None
    else:
        try:
            revision_value = int(active_window_revision)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            revision_value = str(active_window_revision)
    return {
        "selected_hls_engine": selected_hls_engine,
        **window,
        "active_window_revision": revision_value,
        "active_window_reason": active_window_reason,
        "native_hls_window_policy": NATIVE_HLS_WINDOW_POLICY,
        "client_back_buffer_prune_supported": client_back_buffer_prune_supported(
            selected_hls_engine,
        ),
        "full_duration_seconds": round(duration, 2),
        "attach_position_seconds": round(
            _coerce_finite_seconds(attach_position_seconds), 2,
        ),
        "target_position_seconds": round(
            _coerce_finite_seconds(target_position_seconds), 2,
        ),
    }
