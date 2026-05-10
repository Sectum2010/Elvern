import {
  getBrowserPlaybackTimelineStartSeconds,
  getBrowserPlaybackTimelineEndSeconds,
  isBrowserPlaybackAbsolutePositionReady,
  toBrowserPlaybackAbsoluteSeconds,
  toBrowserPlaybackMediaElementSeconds,
} from "./browserPlaybackTimeline.js";

const RANGE_MERGE_EPSILON_SECONDS = 0.05;
const PLAYED_ACCUMULATE_MAX_GAP_SECONDS = 1.5;

function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function coerceRange(start, end) {
  const safeStart = isFiniteNumber(start) ? start : Number(start);
  const safeEnd = isFiniteNumber(end) ? end : Number(end);
  if (!Number.isFinite(safeStart) || !Number.isFinite(safeEnd)) {
    return null;
  }
  if (safeEnd <= safeStart) {
    return null;
  }
  return [Math.max(0, safeStart), Math.max(safeStart, safeEnd)];
}

export function normalizeTimeRanges(timeRanges) {
  if (!timeRanges) {
    return [];
  }
  if (Array.isArray(timeRanges)) {
    const result = [];
    for (const entry of timeRanges) {
      if (!Array.isArray(entry) || entry.length < 2) {
        continue;
      }
      const range = coerceRange(entry[0], entry[1]);
      if (range) {
        result.push(range);
      }
    }
    return mergeRanges(result);
  }
  const length = Number(timeRanges.length);
  if (!Number.isFinite(length) || length <= 0) {
    return [];
  }
  const result = [];
  for (let index = 0; index < length; index += 1) {
    let start;
    let end;
    try {
      start = timeRanges.start(index);
      end = timeRanges.end(index);
    } catch (rangeError) {
      continue;
    }
    const range = coerceRange(start, end);
    if (range) {
      result.push(range);
    }
  }
  return mergeRanges(result);
}

export function mergeRanges(ranges) {
  if (!Array.isArray(ranges) || ranges.length === 0) {
    return [];
  }
  const sorted = ranges
    .map((entry) => coerceRange(entry?.[0], entry?.[1]))
    .filter(Boolean)
    .sort((a, b) => a[0] - b[0]);
  if (sorted.length === 0) {
    return [];
  }
  const merged = [sorted[0].slice()];
  for (let index = 1; index < sorted.length; index += 1) {
    const previous = merged[merged.length - 1];
    const current = sorted[index];
    if (current[0] <= previous[1] + RANGE_MERGE_EPSILON_SECONDS) {
      previous[1] = Math.max(previous[1], current[1]);
    } else {
      merged.push(current.slice());
    }
  }
  return merged;
}

export function clampRangesToDuration(ranges, durationSeconds) {
  const duration = isFiniteNumber(durationSeconds) ? durationSeconds : Number(durationSeconds);
  if (!Number.isFinite(duration) || duration <= 0) {
    return [];
  }
  const result = [];
  for (const entry of ranges || []) {
    const range = coerceRange(entry?.[0], entry?.[1]);
    if (!range) {
      continue;
    }
    const clampedStart = Math.max(0, Math.min(duration, range[0]));
    const clampedEnd = Math.max(clampedStart, Math.min(duration, range[1]));
    if (clampedEnd > clampedStart + RANGE_MERGE_EPSILON_SECONDS) {
      result.push([clampedStart, clampedEnd]);
    }
  }
  return mergeRanges(result);
}

export function subtractRanges(minuendRanges, subtrahendRanges) {
  const minuend = mergeRanges(minuendRanges);
  const subtrahend = mergeRanges(subtrahendRanges);
  if (minuend.length === 0) {
    return [];
  }
  if (subtrahend.length === 0) {
    return minuend.map((range) => range.slice());
  }
  const result = [];
  for (const [start, end] of minuend) {
    let cursor = start;
    for (const [subStart, subEnd] of subtrahend) {
      if (subEnd <= cursor) {
        continue;
      }
      if (subStart >= end) {
        break;
      }
      if (subStart > cursor) {
        result.push([cursor, Math.min(end, subStart)]);
      }
      cursor = Math.max(cursor, subEnd);
      if (cursor >= end) {
        break;
      }
    }
    if (cursor < end) {
      result.push([cursor, end]);
    }
  }
  return mergeRanges(result);
}

export function accumulatePlayedRange(existingRanges, newRange) {
  const incoming = coerceRange(newRange?.[0], newRange?.[1]);
  if (!incoming) {
    return mergeRanges(existingRanges);
  }
  return mergeRanges([...(existingRanges || []), incoming]);
}

export function appendPlayedSample(existingRanges, previousAbsoluteSeconds, currentAbsoluteSeconds) {
  if (!isFiniteNumber(currentAbsoluteSeconds) || currentAbsoluteSeconds < 0) {
    return mergeRanges(existingRanges);
  }
  const previous = isFiniteNumber(previousAbsoluteSeconds) && previousAbsoluteSeconds >= 0
    ? previousAbsoluteSeconds
    : currentAbsoluteSeconds;
  const delta = currentAbsoluteSeconds - previous;
  if (delta <= 0) {
    return mergeRanges(existingRanges);
  }
  if (delta > PLAYED_ACCUMULATE_MAX_GAP_SECONDS) {
    return mergeRanges([
      ...(existingRanges || []),
      [currentAbsoluteSeconds - 0.25, currentAbsoluteSeconds],
    ]);
  }
  return mergeRanges([
    ...(existingRanges || []),
    [previous, currentAbsoluteSeconds],
  ]);
}

export function rangesToAbsolute(sessionPayload, mediaElementRanges) {
  const offset = getBrowserPlaybackTimelineStartSeconds(sessionPayload);
  if (!offset) {
    return mergeRanges(mediaElementRanges);
  }
  const shifted = (mediaElementRanges || [])
    .map((entry) => coerceRange(entry?.[0], entry?.[1]))
    .filter(Boolean)
    .map(([start, end]) => [start + offset, end + offset]);
  return mergeRanges(shifted);
}

export function readBufferedAbsoluteRanges(videoElement, sessionPayload) {
  if (!videoElement) {
    return [];
  }
  const local = normalizeTimeRanges(videoElement.buffered);
  return rangesToAbsolute(sessionPayload, local);
}

export function readPlayedAbsoluteRanges(videoElement, sessionPayload) {
  if (!videoElement) {
    return [];
  }
  const local = normalizeTimeRanges(videoElement.played);
  return rangesToAbsolute(sessionPayload, local);
}

export function computePlayedNotCachedRanges(playedRanges, bufferedRanges, durationSeconds) {
  const clampedPlayed = clampRangesToDuration(playedRanges, durationSeconds);
  const clampedBuffered = clampRangesToDuration(bufferedRanges, durationSeconds);
  return subtractRanges(clampedPlayed, clampedBuffered);
}

export function isAbsolutePositionInRanges(absoluteSeconds, ranges) {
  if (!isFiniteNumber(absoluteSeconds)) {
    return false;
  }
  for (const [start, end] of ranges || []) {
    if (absoluteSeconds >= start && absoluteSeconds <= end) {
      return true;
    }
  }
  return false;
}

export const SEEK_TARGET_KIND = Object.freeze({
  BUFFERED: "buffered",
  WINDOW: "window",
  UNCACHED: "uncached",
});

export function classifySeekTarget({
  absoluteTargetSeconds,
  bufferedAbsoluteRanges,
  sessionPayload,
  bufferedHeadroomSeconds = 0.5,
  windowHeadroomSeconds = 0,
}) {
  const target = isFiniteNumber(absoluteTargetSeconds) ? absoluteTargetSeconds : Number(absoluteTargetSeconds);
  if (!Number.isFinite(target) || target < 0) {
    return SEEK_TARGET_KIND.UNCACHED;
  }
  if (isAbsolutePositionInBufferedWithHeadroom(target, bufferedAbsoluteRanges, bufferedHeadroomSeconds)) {
    return SEEK_TARGET_KIND.BUFFERED;
  }
  if (isBrowserPlaybackAbsolutePositionReady(sessionPayload, target, { headroomSeconds: windowHeadroomSeconds })) {
    return SEEK_TARGET_KIND.WINDOW;
  }
  return SEEK_TARGET_KIND.UNCACHED;
}

function isAbsolutePositionInBufferedWithHeadroom(absoluteSeconds, bufferedRanges, headroomSeconds) {
  if (!isFiniteNumber(absoluteSeconds)) {
    return false;
  }
  const safeHeadroom = Math.max(0, Number(headroomSeconds) || 0);
  for (const [start, end] of bufferedRanges || []) {
    if (absoluteSeconds >= start && absoluteSeconds <= end - safeHeadroom) {
      return true;
    }
  }
  return false;
}

export function mapAbsoluteToMediaElementSeconds(sessionPayload, absoluteSeconds) {
  return toBrowserPlaybackMediaElementSeconds(sessionPayload, absoluteSeconds);
}

export function mapMediaElementSecondsToAbsolute(sessionPayload, mediaElementSeconds) {
  return toBrowserPlaybackAbsoluteSeconds(sessionPayload, mediaElementSeconds);
}

export function getActiveWindowAbsoluteRange(sessionPayload) {
  const start = getBrowserPlaybackTimelineStartSeconds(sessionPayload);
  const end = getBrowserPlaybackTimelineEndSeconds(sessionPayload);
  if (end <= start) {
    return null;
  }
  return [start, end];
}
