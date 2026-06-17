import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { formatDuration } from "../../lib/format.js";

const KEYBOARD_STEP_SECONDS = 5;
const KEYBOARD_BIG_STEP_SECONDS = 30;

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function rangePercent(value, duration) {
  if (!Number.isFinite(value) || !Number.isFinite(duration) || duration <= 0) {
    return 0;
  }
  return clamp((value / duration) * 100, 0, 100);
}

function rangeStyle(start, end, duration) {
  const startPercent = rangePercent(start, duration);
  const endPercent = rangePercent(end, duration);
  const widthPercent = Math.max(0, endPercent - startPercent);
  return {
    left: `${startPercent}%`,
    width: `${widthPercent}%`,
  };
}

export default function ElvernTimeline({
  durationSeconds,
  currentTimeSeconds,
  bufferedAbsoluteRanges,
  playedNotCachedAbsoluteRanges,
  onSeekPreview,
  onSeekCommit,
  onDragStart,
  onDragEnd,
  disabled = false,
  ariaLabel = "Movie timeline",
  preparingTargetSeconds = null,
}) {
  const trackRef = useRef(null);
  const [dragSeconds, setDragSeconds] = useState(null);
  const [hoverSeconds, setHoverSeconds] = useState(null);
  const isDraggingRef = useRef(false);

  const safeDuration = Number.isFinite(durationSeconds) && durationSeconds > 0 ? durationSeconds : 0;
  const safeCurrent = Number.isFinite(currentTimeSeconds) ? clamp(currentTimeSeconds, 0, safeDuration) : 0;
  const playheadSeconds = dragSeconds != null ? dragSeconds : safeCurrent;
  const tooltipSeconds = dragSeconds != null
    ? dragSeconds
    : hoverSeconds != null
      ? hoverSeconds
      : null;

  const playheadPercent = rangePercent(playheadSeconds, safeDuration);

  const computeSecondsForClientX = useCallback((clientX) => {
    const track = trackRef.current;
    if (!track || safeDuration <= 0) {
      return 0;
    }
    const rect = track.getBoundingClientRect();
    if (rect.width <= 0) {
      return 0;
    }
    const ratio = clamp((clientX - rect.left) / rect.width, 0, 1);
    return ratio * safeDuration;
  }, [safeDuration]);

  const handlePointerDown = useCallback((event) => {
    if (disabled || safeDuration <= 0) {
      return;
    }
    if (event.button != null && event.button !== 0) {
      return;
    }
    event.preventDefault();
    const target = event.currentTarget;
    if (target?.setPointerCapture && event.pointerId != null) {
      try {
        target.setPointerCapture(event.pointerId);
      } catch (captureError) {
        // ignore: not all browsers expose pointer capture
      }
    }
    const next = computeSecondsForClientX(event.clientX);
    isDraggingRef.current = true;
    setDragSeconds(next);
    onDragStart?.();
    onSeekPreview?.(next);
  }, [computeSecondsForClientX, disabled, onDragStart, onSeekPreview, safeDuration]);

  const handlePointerMove = useCallback((event) => {
    if (disabled || safeDuration <= 0) {
      return;
    }
    if (isDraggingRef.current) {
      const next = computeSecondsForClientX(event.clientX);
      setDragSeconds(next);
      onSeekPreview?.(next);
      return;
    }
    if (event.pointerType === "touch") {
      return;
    }
    setHoverSeconds(computeSecondsForClientX(event.clientX));
  }, [computeSecondsForClientX, disabled, onSeekPreview, safeDuration]);

  const finalizeDrag = useCallback((event, { commit }) => {
    if (!isDraggingRef.current) {
      return;
    }
    const target = event?.currentTarget;
    if (target?.releasePointerCapture && event?.pointerId != null) {
      try {
        target.releasePointerCapture(event.pointerId);
      } catch (releaseError) {
        // ignore: not all browsers expose pointer capture
      }
    }
    const finalSeconds = event ? computeSecondsForClientX(event.clientX) : dragSeconds;
    isDraggingRef.current = false;
    setDragSeconds(null);
    onDragEnd?.();
    if (commit && finalSeconds != null) {
      onSeekCommit?.(finalSeconds);
    }
  }, [computeSecondsForClientX, dragSeconds, onDragEnd, onSeekCommit]);

  const handlePointerUp = useCallback((event) => {
    finalizeDrag(event, { commit: true });
  }, [finalizeDrag]);

  const handlePointerCancel = useCallback((event) => {
    finalizeDrag(event, { commit: false });
  }, [finalizeDrag]);

  const handlePointerLeave = useCallback(() => {
    if (!isDraggingRef.current) {
      setHoverSeconds(null);
    }
  }, []);

  const handleKeyDown = useCallback((event) => {
    if (disabled || safeDuration <= 0) {
      return;
    }
    let nextValue = safeCurrent;
    let handled = false;
    switch (event.key) {
      case "ArrowLeft":
        nextValue = clamp(safeCurrent - KEYBOARD_STEP_SECONDS, 0, safeDuration);
        handled = true;
        break;
      case "ArrowRight":
        nextValue = clamp(safeCurrent + KEYBOARD_STEP_SECONDS, 0, safeDuration);
        handled = true;
        break;
      case "PageDown":
        nextValue = clamp(safeCurrent - KEYBOARD_BIG_STEP_SECONDS, 0, safeDuration);
        handled = true;
        break;
      case "PageUp":
        nextValue = clamp(safeCurrent + KEYBOARD_BIG_STEP_SECONDS, 0, safeDuration);
        handled = true;
        break;
      case "Home":
        nextValue = 0;
        handled = true;
        break;
      case "End":
        nextValue = Math.max(0, safeDuration - 0.1);
        handled = true;
        break;
      default:
        break;
    }
    if (handled) {
      event.preventDefault();
      onSeekCommit?.(nextValue);
    }
  }, [disabled, onSeekCommit, safeCurrent, safeDuration]);

  useEffect(() => {
    return () => {
      isDraggingRef.current = false;
    };
  }, []);

  const playedLayer = useMemo(() => {
    if (!Array.isArray(playedNotCachedAbsoluteRanges)) {
      return [];
    }
    return playedNotCachedAbsoluteRanges
      .map(([start, end], index) => ({
        key: `played-${index}-${start.toFixed(2)}`,
        style: rangeStyle(start, end, safeDuration),
      }))
      .filter((entry) => entry.style.width !== "0%");
  }, [playedNotCachedAbsoluteRanges, safeDuration]);

  const bufferedLayer = useMemo(() => {
    if (!Array.isArray(bufferedAbsoluteRanges)) {
      return [];
    }
    return bufferedAbsoluteRanges
      .map(([start, end], index) => ({
        key: `buffered-${index}-${start.toFixed(2)}`,
        style: rangeStyle(start, end, safeDuration),
      }))
      .filter((entry) => entry.style.width !== "0%");
  }, [bufferedAbsoluteRanges, safeDuration]);

  const hasPreparingTarget = preparingTargetSeconds != null && preparingTargetSeconds !== "";
  const numericPreparingTarget = hasPreparingTarget ? Number(preparingTargetSeconds) : Number.NaN;
  const preparingPercent =
    safeDuration > 0 && Number.isFinite(numericPreparingTarget)
      ? rangePercent(numericPreparingTarget, safeDuration)
      : null;

  const tooltipPercent = tooltipSeconds != null ? rangePercent(tooltipSeconds, safeDuration) : null;

  return (
    <div className="elvern-timeline">
      <div
        ref={trackRef}
        aria-disabled={disabled || safeDuration <= 0 ? true : undefined}
        aria-label={ariaLabel}
        aria-valuemax={Math.round(safeDuration) || 0}
        aria-valuemin={0}
        aria-valuenow={Math.round(playheadSeconds)}
        aria-valuetext={`${formatDuration(playheadSeconds)} of ${formatDuration(safeDuration)}`}
        className={`elvern-timeline__track${disabled ? " elvern-timeline__track--disabled" : ""}`}
        onKeyDown={handleKeyDown}
        onPointerCancel={handlePointerCancel}
        onPointerDown={handlePointerDown}
        onPointerLeave={handlePointerLeave}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        role="slider"
        tabIndex={disabled ? -1 : 0}
      >
        <div className="elvern-timeline__layer elvern-timeline__layer--base" aria-hidden="true" />
        {playedLayer.map((entry) => (
          <div
            key={entry.key}
            aria-hidden="true"
            className="elvern-timeline__layer elvern-timeline__layer--played-uncached"
            style={entry.style}
          />
        ))}
        {bufferedLayer.map((entry) => (
          <div
            key={entry.key}
            aria-hidden="true"
            className="elvern-timeline__layer elvern-timeline__layer--buffered"
            style={entry.style}
          />
        ))}
        <div
          aria-hidden="true"
          className="elvern-timeline__layer elvern-timeline__layer--progress"
          style={{ width: `${playheadPercent}%` }}
        />
        {preparingPercent != null ? (
          <div
            aria-hidden="true"
            className="elvern-timeline__preparing-marker elvern-timeline__preparing-marker--target"
            style={{ left: `${preparingPercent}%` }}
          />
        ) : null}
        {tooltipPercent != null ? (
          <div
            aria-hidden="true"
            className="elvern-timeline__tooltip"
            style={{ left: `${tooltipPercent}%` }}
          >
            {formatDuration(tooltipSeconds)}
          </div>
        ) : null}
      </div>
    </div>
  );
}
