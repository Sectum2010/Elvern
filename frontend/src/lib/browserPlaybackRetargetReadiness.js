import {
  getContiguousClientBufferedAheadSeconds,
} from "./playbackTimelineRanges.js";
import { toBrowserPlaybackMediaElementSeconds } from "./browserPlaybackTimeline.js";

export const RETARGET_CLIENT_BUFFER_RELEASE_SECONDS = 15;

export function readClientBufferedAheadFromAbsoluteTarget(videoElement, sessionPayload, targetAbsoluteSeconds) {
  const target = Number(targetAbsoluteSeconds);
  if (!videoElement || !sessionPayload || !Number.isFinite(target) || target < 0) {
    return 0;
  }
  return getContiguousClientBufferedAheadSeconds(videoElement, target, sessionPayload);
}

export function shouldReleaseRetargetFrozenFrame({
  requiredClientBufferSeconds = RETARGET_CLIENT_BUFFER_RELEASE_SECONDS,
  sessionPayload,
  targetAbsoluteSeconds,
  videoElement,
} = {}) {
  const target = Number(targetAbsoluteSeconds);
  if (!videoElement || !sessionPayload || !Number.isFinite(target) || target < 0) {
    return false;
  }
  const targetMediaElementTime = toBrowserPlaybackMediaElementSeconds(sessionPayload, target);
  const seekSettled = Math.abs((Number(videoElement.currentTime) || 0) - targetMediaElementTime) <= 0.75;
  const bufferedAheadSeconds = readClientBufferedAheadFromAbsoluteTarget(
    videoElement,
    sessionPayload,
    target,
  );
  return Boolean(
    seekSettled
    && Number(videoElement.readyState || 0) >= 2
    && bufferedAheadSeconds + 0.001 >= Math.max(0, Number(requiredClientBufferSeconds) || 0),
  );
}
