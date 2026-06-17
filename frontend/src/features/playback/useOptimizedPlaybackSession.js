import { useEffect, useRef, useState } from "react";
import {
  buildHlsProbeSegmentUrl,
  getPlaybackMode,
  getPlaybackModeLabel,
  getPlaybackModeTitle,
  getSessionModeEstimateSeconds,
  isHlsSessionPayload as isSharedHlsSessionPayload,
} from "../../lib/browserPlayback";
import {
  compactHlsBufferConfig,
  classifyManifestWindowState,
  deriveBufferTargetsFromSession,
  readClientPlaybackLiveness,
  resolveAutomaticPlaybackRecoveryTarget,
  resolvePlaybackRecoveryTargetSeconds,
  shouldStartVisibleHlsSupplyRecovery,
} from "../../lib/browserPlaybackBufferPolicy";
import {
  getBrowserPlaybackAttachedManifestEndSeconds,
  shouldForceReattachForManifestWindowRefresh,
  toBrowserPlaybackAbsoluteSeconds,
  toBrowserPlaybackMediaElementSeconds,
} from "../../lib/browserPlaybackTimeline";
import {
  createBrowserPlaybackAttempt,
  resolveBrowserPlaybackSessionNotFound,
  SESSION_SOURCE_EXPLICIT_CREATE,
  SESSION_SOURCE_RECOVERY_CREATE,
  SESSION_SOURCE_RESTORE_ACTIVE,
  SESSION_SOURCE_SEEK,
  SESSION_SOURCE_STATUS,
  shouldAcceptBrowserPlaybackSessionPayload,
} from "../../lib/browserPlaybackSessionLifecycle";
import { formatDuration } from "../../lib/format";
import {
  cancelOptimizedPlaybackAudioTrackCandidate,
  commitOptimizedPlaybackAudioTrackCandidate,
  createOptimizedPlaybackSession,
  fetchActiveOptimizedPlaybackSession,
  fetchOptimizedPlaybackSessionStatus,
  postOptimizedPlaybackHeartbeat,
  prepareOptimizedPlaybackSubtitleTrack,
  selectOptimizedPlaybackAudioTrack,
  seekOptimizedPlaybackSession,
} from "./browserSessionClient";

const SESSION_MANIFEST_REFRESH_RUNWAY_SECONDS = 12;
const BACKGROUND_PREPARATION_PARK_MS = 5 * 60 * 1000;
const AUDIO_SWITCH_ATTACH_LOAD_EVENTS = new Set(["loadedmetadata", "loadeddata", "canplay"]);
export const AUDIO_SWITCH_ATTACH_LOAD_TIMEOUT_MS = 15000;
export const AUDIO_SWITCH_ATTACH_RETRY_LIMIT = 1;
export const AUDIO_SWITCH_CANDIDATE_VALIDATION_TIMEOUT_MS = 8000;
export const AUDIO_SWITCH_ATTACH_RESTORED_MESSAGE = "Audio switch failed. Restored previous audio.";
export const AUDIO_SWITCH_ATTACH_RESTART_REQUIRED_MESSAGE = "Audio switch failed. Restart playback to continue.";
export const AUDIO_SWITCH_ATTACH_FAILURE_MESSAGE = AUDIO_SWITCH_ATTACH_RESTART_REQUIRED_MESSAGE;

function isAudioSwitchAttachWaiting(pending) {
  return Boolean(
    pending
    && (pending.phase === "pending_attach" || pending.phase === "source_set")
  );
}

export function buildAudioSwitchAttachDiagnostic(eventName, details = {}) {
  const diagnostic = {
    event: eventName,
  };
  const allowedFields = [
    "expectedAttachRevision",
    "expectedActiveEpochId",
    "targetAudioStreamIndex",
    "phase",
    "elapsedMs",
    "retryCount",
    "clientAttachRevision",
    "currentAttachRevision",
    "loadedEventName",
    "successReason",
    "previousAttachRevision",
    "previousActiveEpochId",
    "previousAudioStreamIndex",
    "candidateEpochId",
    "candidateAudioStreamIndex",
    "candidateAttachPositionSeconds",
    "candidateReadyEndSeconds",
    "requiredServerPrepareSeconds",
    "actualCandidateRunwaySeconds",
    "checkedSegmentCount",
    "activeAudioStreamIndex",
    "validationMethod",
    "timeoutMs",
    "reason",
    "oldStreamRetained",
    "failureReason",
    "sourceGeneration",
    "paused",
    "readyState",
  ];
  allowedFields.forEach((field) => {
    if (details[field] !== undefined) {
      diagnostic[field] = details[field];
    }
  });
  if (details.sourceSetAtMs !== undefined) {
    diagnostic.sourceSetAtMs = Boolean(details.sourceSetAtMs);
  }
  if (details.loadedAtMs !== undefined) {
    diagnostic.loadedAtMs = Boolean(details.loadedAtMs);
  }
  if (details.evidenceAfterSourceSet !== undefined) {
    diagnostic.evidenceAfterSourceSet = Boolean(details.evidenceAfterSourceSet);
  }
  if (details.restoredPrevious !== undefined) {
    diagnostic.restoredPrevious = Boolean(details.restoredPrevious);
  }
  return diagnostic;
}

function stripManifestCacheBusters(url) {
  if (typeof url !== "string" || !url.trim()) {
    return "";
  }
  try {
    const parsed = new URL(url, "https://elvern.local");
    parsed.searchParams.delete("attach_revision");
    parsed.searchParams.delete("manifest_revision");
    return `${parsed.pathname}?${parsed.searchParams.toString()}`.replace(/\?$/, "");
  } catch {
    return url.split("#")[0].split("?")[0];
  }
}

export function softResumeRequiresHardReattach({
  payload,
  attachedManifestUrl = "",
  attachedIdentity = null,
  streamSourceUrl = "",
} = {}) {
  if (!payload) {
    return false;
  }
  const nextIdentity = payload.active_epoch_id || payload.epoch || null;
  if (attachedIdentity && nextIdentity && attachedIdentity !== nextIdentity) {
    return true;
  }
  const activeManifestUrl = payload.active_manifest_url || payload.manifest_url || "";
  if (!activeManifestUrl) {
    return false;
  }
  const activeManifest = stripManifestCacheBusters(activeManifestUrl);
  const attachedManifest = stripManifestCacheBusters(attachedManifestUrl);
  const streamManifest = stripManifestCacheBusters(streamSourceUrl);
  if (!attachedManifest && !streamManifest) {
    return true;
  }
  if (!attachedManifest && streamManifest) {
    return activeManifest !== streamManifest;
  }
  return Boolean(
    activeManifest
    && attachedManifest
    && activeManifest !== attachedManifest
    && (!streamManifest || activeManifest !== streamManifest)
  );
}

function buildSessionManifestUrl(url, manifestRevision) {
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}manifest_revision=${encodeURIComponent(manifestRevision)}`;
}

function buildAttachRevisionManifestUrl(url, attachRevision) {
  if (typeof url !== "string" || !url.trim()) {
    return "";
  }
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}attach_revision=${encodeURIComponent(String(attachRevision || 0))}`;
}

function isAudioSwitchPreparingState(payload) {
  const state = String(payload?.audio_switch_state || "").trim().toLowerCase();
  return Boolean(
    ["preparing", "candidate_preparing", "candidate_ready", "committing"].includes(state)
    && Number.isInteger(payload?.pending_audio_stream_index)
  );
}

function isAudioSwitchPromotionPayload(previousPayload, payload) {
  if (!isSharedHlsSessionPayload(previousPayload) || !isSharedHlsSessionPayload(payload)) {
    return false;
  }
  const switchState = String(payload?.audio_switch_state || "").trim().toLowerCase();
  if (!["active", "committing"].includes(switchState) || !Number.isInteger(payload?.active_audio_stream_index)) {
    return false;
  }
  const previousPendingStream = Number.isInteger(previousPayload?.pending_audio_stream_index)
    ? previousPayload.pending_audio_stream_index
    : Number.isInteger(previousPayload?.audio_switch_candidate_stream_index)
      ? previousPayload.audio_switch_candidate_stream_index
    : null;
  const previousActiveStream = Number.isInteger(previousPayload?.active_audio_stream_index)
    ? previousPayload.active_audio_stream_index
    : null;
  const currentActiveStream = payload.active_audio_stream_index;
  const promotedPendingStream = previousPendingStream != null && previousPendingStream === currentActiveStream;
  const activeStreamChanged = previousActiveStream != null && previousActiveStream !== currentActiveStream;
  const previousWasAudioSwitch = isAudioSwitchPreparingState(previousPayload);
  const epochChanged = Boolean(
    previousPayload?.active_epoch_id
    && payload?.active_epoch_id
    && previousPayload.active_epoch_id !== payload.active_epoch_id,
  );
  const attachRevisionIncreased = Number(payload?.attach_revision || 0) > Number(previousPayload?.attach_revision || 0);
  return Boolean(
    previousWasAudioSwitch
    && (promotedPendingStream || activeStreamChanged)
    && (epochChanged || attachRevisionIncreased)
    && payload?.active_manifest_url
  );
}

function isAudioSwitchCandidateReadyPayload(payload) {
  const switchState = String(payload?.audio_switch_state || "").trim().toLowerCase();
  const candidateState = String(payload?.audio_switch_candidate_state || "").trim().toLowerCase();
  const requiredRunway = Number(payload?.audio_switch_candidate_required_runway_seconds);
  const actualRunway = Number(payload?.audio_switch_candidate_actual_runway_seconds);
  const hasRunwayNumbers = Number.isFinite(requiredRunway) && Number.isFinite(actualRunway);
  if (hasRunwayNumbers && actualRunway + 0.001 < requiredRunway) {
    return false;
  }
  if (hasRunwayNumbers && payload?.audio_switch_candidate_runway_satisfied === false) {
    return false;
  }
  return Boolean(
    isSharedHlsSessionPayload(payload)
    && (switchState === "candidate_ready" || candidateState === "ready" || payload?.audio_switch_requires_commit)
    && payload?.audio_switch_candidate_manifest_url
    && Number.isInteger(payload?.audio_switch_candidate_stream_index)
    && payload?.session_id
  );
}

function resolveAudioSwitchCandidateIdentity(payload) {
  if (!isAudioSwitchCandidateReadyPayload(payload)) {
    return "";
  }
  return [
    payload.session_id,
    payload.audio_switch_candidate_epoch_id || "",
    payload.audio_switch_candidate_stream_index,
  ].join(":");
}

function buildCandidateProbeUrls(manifestUrl) {
  const baseHref = typeof window !== "undefined" && window.location?.origin
    ? window.location.origin
    : "https://elvern.local";
  const manifest = new URL(manifestUrl, baseHref);
  const epochBase = new URL(".", manifest);
  return {
    manifestUrl: manifest.pathname + manifest.search,
    initUrl: new URL("init.mp4", epochBase).pathname,
    segmentUrlFromLine(segmentLine) {
      const resolved = new URL(segmentLine, epochBase);
      return resolved.pathname + resolved.search;
    },
  };
}

function parseCandidateManifestSegments(manifestText) {
  const lines = String(manifestText || "").split(/\r?\n/u);
  let mediaSequence = 0;
  let targetDuration = 2;
  let pendingDuration = null;
  const segments = [];
  lines.forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line) {
      return;
    }
    if (line.startsWith("#EXT-X-MEDIA-SEQUENCE:")) {
      const parsed = Number(line.slice("#EXT-X-MEDIA-SEQUENCE:".length));
      if (Number.isFinite(parsed) && parsed >= 0) {
        mediaSequence = parsed;
      }
      return;
    }
    if (line.startsWith("#EXT-X-TARGETDURATION:")) {
      const parsed = Number(line.slice("#EXT-X-TARGETDURATION:".length));
      if (Number.isFinite(parsed) && parsed > 0) {
        targetDuration = parsed;
      }
      return;
    }
    if (line.startsWith("#EXTINF:")) {
      const parsed = Number(line.slice("#EXTINF:".length).split(",")[0]);
      pendingDuration = Number.isFinite(parsed) && parsed > 0 ? parsed : targetDuration;
      return;
    }
    if (line.startsWith("#") || !/\.m4s(?:$|[?#])/u.test(line)) {
      return;
    }
    const parsedIndex = line.match(/segments\/(\d+)\.m4s(?:$|[?#])/u);
    const segmentIndex = parsedIndex
      ? Number(parsedIndex[1])
      : mediaSequence + segments.length;
    const durationSeconds = pendingDuration != null ? pendingDuration : targetDuration;
    segments.push({
      line,
      segmentIndex: Number.isFinite(segmentIndex) ? segmentIndex : mediaSequence + segments.length,
      durationSeconds,
      startSeconds: (Number.isFinite(segmentIndex) ? segmentIndex : mediaSequence + segments.length) * targetDuration,
    });
    pendingDuration = null;
  });
  return {
    mediaSequence,
    targetDuration,
    segments,
  };
}

function selectCandidateValidationSegments(payload, manifestText) {
  const { segments, targetDuration } = parseCandidateManifestSegments(manifestText);
  if (!segments.length) {
    return [];
  }
  const attach = Number(payload?.audio_switch_candidate_attach_position_seconds);
  const readyEnd = Number(payload?.audio_switch_candidate_ready_end_seconds);
  const hasAttach = Number.isFinite(attach) && attach >= 0;
  const targetSegmentIndex = hasAttach
    ? Math.max(0, Math.floor(attach / Math.max(0.001, targetDuration)))
    : null;
  const attachSegment = targetSegmentIndex != null
    ? segments.find((segment) => segment.segmentIndex >= targetSegmentIndex)
      || segments[segments.length - 1]
    : segments[Math.max(0, Math.floor(segments.length * 0.66))];
  const selected = [attachSegment];
  if (Number.isFinite(readyEnd) && readyEnd > (hasAttach ? attach : 0)) {
    const readySegmentIndex = Math.max(0, Math.floor((readyEnd - Math.max(0.001, targetDuration)) / Math.max(0.001, targetDuration)));
    const readySegment = segments.find((segment) => segment.segmentIndex >= readySegmentIndex)
      || segments[segments.length - 1];
    if (readySegment && readySegment.line !== attachSegment.line) {
      selected.push(readySegment);
    }
  }
  return selected.filter(Boolean);
}

async function fetchWithTimeout(url, { signal: parentSignal = null, timeoutMs = 0, ...options } = {}) {
  const controller = new AbortController();
  let timerId = null;
  const timerHost = typeof window !== "undefined" ? window : globalThis;
  function abortFromParent() {
    controller.abort();
  }
  if (parentSignal) {
    if (parentSignal.aborted) {
      controller.abort();
    } else {
      parentSignal.addEventListener("abort", abortFromParent, { once: true });
    }
  }
  if (timeoutMs > 0) {
    timerId = timerHost.setTimeout(() => {
      controller.abort();
    }, timeoutMs);
  }
  try {
    return await fetch(url, {
      ...options,
      signal: controller.signal,
    });
  } finally {
    if (timerId != null) {
      timerHost.clearTimeout(timerId);
    }
    if (parentSignal) {
      parentSignal.removeEventListener("abort", abortFromParent);
    }
  }
}

export async function validateAudioSwitchCandidatePayload(payload, { timeoutMs = AUDIO_SWITCH_CANDIDATE_VALIDATION_TIMEOUT_MS } = {}) {
  const manifestUrl = payload?.audio_switch_candidate_manifest_url;
  if (!manifestUrl) {
    throw new Error("candidate_manifest_missing");
  }
  const probeUrls = buildCandidateProbeUrls(manifestUrl);
  const manifestResponse = await fetchWithTimeout(probeUrls.manifestUrl, {
    credentials: "include",
    cache: "no-store",
    timeoutMs,
  });
  if (!manifestResponse.ok) {
    throw new Error("candidate_manifest_unavailable");
  }
  const manifestText = await manifestResponse.text();
  if (!manifestText.includes("#EXTM3U")) {
    throw new Error("candidate_manifest_invalid");
  }
  const segmentLines = selectCandidateValidationSegments(payload, manifestText);
  if (!segmentLines.length) {
    throw new Error("candidate_segment_missing");
  }
  const initResponse = await fetchWithTimeout(probeUrls.initUrl, {
    credentials: "include",
    cache: "no-store",
    timeoutMs,
  });
  if (!initResponse.ok) {
    throw new Error("candidate_init_unavailable");
  }
  const initBytes = await initResponse.arrayBuffer();
  if (!initBytes.byteLength) {
    throw new Error("candidate_init_empty");
  }
  let totalSegmentBytes = 0;
  for (const segment of segmentLines) {
    const segmentResponse = await fetchWithTimeout(probeUrls.segmentUrlFromLine(segment.line), {
      credentials: "include",
      cache: "no-store",
      timeoutMs,
    });
    if (!segmentResponse.ok) {
      throw new Error("candidate_segment_unavailable");
    }
    const segmentBytes = await segmentResponse.arrayBuffer();
    if (!segmentBytes.byteLength) {
      throw new Error("candidate_segment_empty");
    }
    totalSegmentBytes += segmentBytes.byteLength;
  }
  return {
    validationMethod: "manifest_init_attach_window_segment_fetch",
    initBytes: initBytes.byteLength,
    segmentBytes: totalSegmentBytes,
    checkedSegmentCount: segmentLines.length,
  };
}

function captureVideoFrameSnapshot(video) {
  if (!video || video.videoWidth <= 0 || video.videoHeight <= 0) {
    return "";
  }
  try {
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const context = canvas.getContext("2d");
    if (!context) {
      return "";
    }
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.82);
  } catch {
    return "";
  }
}

function releasePlaybackSession(stopUrl, fallbackUrl = "") {
  const url = stopUrl || fallbackUrl;
  if (!url || typeof window === "undefined") {
    return;
  }
  if (navigator.sendBeacon) {
    navigator.sendBeacon(url, new Blob([], { type: "text/plain" }));
    return;
  }
  fetch(url, {
    method: "POST",
    credentials: "include",
    keepalive: true,
  }).catch(() => {
    // Ignore unload-time cleanup failures.
  });
}

export function useOptimizedPlaybackSession({
  itemId,
  iosMobile,
  streamSource,
  optimizedPlaybackPending,
  browserPlaybackSessionRoot,
  browserPlaybackProfile,
  browserPlaybackDeviceClass,
  videoRef,
  clearPlayerBinding,
  clearOptimizedPlaybackPending,
  playbackFlowRef,
  currentItemIdRef,
  attachedOptimizedManifestUrlRef,
  browserStartPositionRef,
  playbackModeIntentRef,
  setPlaybackModeIntent,
  setStreamSource,
  setPlaybackError,
  setSeekNotice,
  setPlaybackStatus,
  setPlaybackPosition,
  setOptimizedPlaybackPending,
  hlsRef = null,
  hlsEngineDiagnostics = null,
}) {
  const mobileSessionRef = useRef(null);
  const mobilePollRef = useRef(null);
  const mobilePollTokenRef = useRef(0);
  const mobilePendingTargetRef = useRef(null);
  const requestedTargetSecondsRef = useRef(null);
  const mobileAutoplayPendingRef = useRef(false);
  const mobileResumeAfterReadyRef = useRef(false);
  const mobileSeekPendingRef = useRef(false);
  const pendingSeekPhaseRef = useRef("idle");
  const mobileAttachedEpochRef = useRef(null);
  const mobileAttachedManifestRevisionRef = useRef("");
  const mobileAttachedManifestEndRef = useRef(0);
  const mobileCanPlaySeenRef = useRef(false);
  const mobileLoadedDataSeenRef = useRef(false);
  const mobileAwaitingTargetSeekRef = useRef(false);
  const mobileFrameReadyRef = useRef(false);
  const mobileFrameProbePendingRef = useRef(false);
  const mobileReadinessGenerationRef = useRef(0);
  const mobilePlayerCanPlayRef = useRef(false);
  const mobileWarmupProbeActiveRef = useRef(false);
  const mobileWarmupPlaybackObservedRef = useRef(false);
  const mobileWarmupStartPositionRef = useRef(0);
  const mobileRetargetTransitionRef = useRef(false);
  const mobileLastStablePositionRef = useRef(0);
  const mobileLifecycleStateRef = useRef("attached");
  const mobileRecoveryInFlightRef = useRef(false);
  const mobileLastHeartbeatAtRef = useRef(0);
  const mobileHeartbeatInFlightRef = useRef(false);
  const mobileWasBackgroundedRef = useRef(false);
  const mobileBackgroundHiddenAtRef = useRef(0);
  const mobileWasPlayingBeforeSuspendRef = useRef(false);
  const mobileStallTimerRef = useRef(null);
  const mobileStallStartedAtRef = useRef(0);
  const mobileClientAttachRevisionRef = useRef(0);
  const mobilePendingAttachRevisionRef = useRef(0);
  const audioSwitchAttachRef = useRef(null);
  const audioSwitchCandidateValidationRef = useRef(null);
  const route2LastAttachAttemptAtRef = useRef(0);
  const route2LastAttachAttemptRevisionRef = useRef(0);
  const committedPlayheadSecondsRef = useRef(0);
  const actualMediaElementTimeRef = useRef(0);
  const clientPlaybackLivenessSampleRef = useRef(null);
  const fullProbeInFlightRef = useRef(false);
  const browserPlaybackAttemptCounterRef = useRef(0);
  const browserPlaybackLatestAttemptRef = useRef(null);
  const browserPlaybackCurrentSessionRef = useRef(null);
  const browserPlaybackDeadSessionIdsRef = useRef(new Set());

  const [mobileSession, setMobileSession] = useState(null);
  const [mobilePlayerCanPlay, setMobilePlayerCanPlay] = useState(false);
  const [mobileFrozenFrameUrl, setMobileFrozenFrameUrl] = useState("");
  const [requestedTargetSeconds, setRequestedTargetSeconds] = useState(null);
  const [committedPlayheadSeconds, setCommittedPlayheadSeconds] = useState(0);
  const [actualMediaElementTime, setActualMediaElementTime] = useState(0);
  const [pendingSeekPhase, setPendingSeekPhase] = useState("idle");
  const [mobileLifecycleState, setMobileLifecycleState] = useState("attached");
  const [prepareEstimateObservedAtMs, setPrepareEstimateObservedAtMs] = useState(0);
  const [prepareEstimateNowMs, setPrepareEstimateNowMs] = useState(() => Date.now());
  const [videoElementKey, setVideoElementKey] = useState(0);

  const activePlaybackMode = getPlaybackMode(mobileSession?.playback_mode || playbackModeIntentRef.current);
  const browserPlaybackLabel = getPlaybackModeLabel(activePlaybackMode);
  const browserPlaybackLabelTitle = getPlaybackModeTitle(activePlaybackMode);
  const browserStreamLabelTitle = browserPlaybackLabelTitle;
  const browserReadyLabelTitle = `${browserPlaybackLabelTitle} ready`;

  function setMobileLifecycleStateValue(nextState) {
    mobileLifecycleStateRef.current = nextState;
    setMobileLifecycleState(nextState);
  }

  function clearBrowserPlaybackLifecycleState() {
    browserPlaybackLatestAttemptRef.current = null;
    browserPlaybackCurrentSessionRef.current = null;
    browserPlaybackDeadSessionIdsRef.current = new Set();
  }

  function markBrowserPlaybackSessionDead(sessionId) {
    const normalizedSessionId = typeof sessionId === "string" ? sessionId.trim() : "";
    if (!normalizedSessionId) {
      return;
    }
    const nextDeadSessionIds = new Set(browserPlaybackDeadSessionIdsRef.current);
    nextDeadSessionIds.add(normalizedSessionId);
    browserPlaybackDeadSessionIdsRef.current = nextDeadSessionIds;
  }

  function buildSyntheticBrowserPlaybackAttempt(identity, payload) {
    const nextAttemptId = browserPlaybackAttemptCounterRef.current + 1;
    browserPlaybackAttemptCounterRef.current = nextAttemptId;
    return createBrowserPlaybackAttempt({
      attemptId: nextAttemptId,
      itemId: identity?.itemId || payload?.media_item_id || itemId,
      playbackMode: identity?.playbackMode || payload?.playback_mode || playbackModeIntentRef.current,
      startPositionSeconds: Math.max(
        0,
        Number(
          identity?.startPositionSeconds
          ?? payload?.pending_target_seconds
          ?? payload?.target_position_seconds
          ?? payload?.committed_playhead_seconds
          ?? 0,
        ),
      ),
      profile:
        identity?.profile
        || (typeof payload?.profile === "string" ? payload.profile : "")
        || browserPlaybackProfile,
      engineMode:
        identity?.engineMode
        || (typeof payload?.engine_mode === "string" ? payload.engine_mode : ""),
    });
  }

  function acceptBrowserPlaybackSessionPayload(payload, source, { responseAttempt = null } = {}) {
    const latestAttempt = browserPlaybackLatestAttemptRef.current;
    const currentSession = browserPlaybackCurrentSessionRef.current;
    const decision = shouldAcceptBrowserPlaybackSessionPayload({
      payload,
      source,
      itemId,
      responseAttempt,
      latestAttempt,
      currentSession,
      deadSessionIds: browserPlaybackDeadSessionIdsRef.current,
    });
    if (!decision.accept) {
      return { accepted: false, decision, identity: decision.identity };
    }

    let nextIdentity = decision.identity;
    if (responseAttempt) {
      browserPlaybackAttemptCounterRef.current = Math.max(
        browserPlaybackAttemptCounterRef.current,
        responseAttempt.attemptId || 0,
      );
      browserPlaybackLatestAttemptRef.current = responseAttempt;
      nextIdentity = {
        ...nextIdentity,
        attemptId: responseAttempt.attemptId,
        startPositionSeconds: responseAttempt.startPositionSeconds,
        profile: responseAttempt.profile || nextIdentity.profile,
        engineMode: responseAttempt.engineMode || nextIdentity.engineMode,
      };
    } else if (source === SESSION_SOURCE_RESTORE_ACTIVE && !currentSession && !latestAttempt) {
      const syntheticAttempt = buildSyntheticBrowserPlaybackAttempt(nextIdentity, payload);
      browserPlaybackLatestAttemptRef.current = syntheticAttempt;
      nextIdentity = {
        ...nextIdentity,
        attemptId: syntheticAttempt.attemptId,
        startPositionSeconds: syntheticAttempt.startPositionSeconds,
        profile: syntheticAttempt.profile || nextIdentity.profile,
        engineMode: syntheticAttempt.engineMode || nextIdentity.engineMode,
      };
    } else if (currentSession) {
      nextIdentity = {
        ...nextIdentity,
        attemptId: currentSession.attemptId,
        startPositionSeconds: currentSession.startPositionSeconds,
        profile: nextIdentity.profile || currentSession.profile,
        engineMode: nextIdentity.engineMode || currentSession.engineMode,
      };
    }

    browserPlaybackCurrentSessionRef.current = nextIdentity;
    syncMobilePlaybackState(payload);
    return { accepted: true, decision, identity: nextIdentity };
  }

  function clearCurrentBrowserPlaybackSession({ preserveIntent = true } = {}) {
    stopMobilePlaybackPolling();
    attachedOptimizedManifestUrlRef.current = "";
    mobileSessionRef.current = null;
    browserPlaybackCurrentSessionRef.current = null;
    mobilePendingTargetRef.current = null;
    requestedTargetSecondsRef.current = null;
    mobileAutoplayPendingRef.current = false;
    mobileResumeAfterReadyRef.current = false;
    mobileSeekPendingRef.current = false;
    pendingSeekPhaseRef.current = "idle";
    mobileAttachedEpochRef.current = null;
    mobileAttachedManifestRevisionRef.current = "";
    mobileAttachedManifestEndRef.current = 0;
    audioSwitchAttachRef.current = null;
    audioSwitchCandidateValidationRef.current = null;
    mobileCanPlaySeenRef.current = false;
    mobileLoadedDataSeenRef.current = false;
    mobileAwaitingTargetSeekRef.current = false;
    mobileFrameReadyRef.current = false;
    mobileFrameProbePendingRef.current = false;
    mobileReadinessGenerationRef.current += 1;
    mobilePlayerCanPlayRef.current = false;
    mobileWarmupProbeActiveRef.current = false;
    mobileWarmupPlaybackObservedRef.current = false;
    mobileWarmupStartPositionRef.current = 0;
    mobileRetargetTransitionRef.current = false;
    mobileLifecycleStateRef.current = "attached";
    mobileRecoveryInFlightRef.current = false;
    mobileHeartbeatInFlightRef.current = false;
    mobilePendingAttachRevisionRef.current = 0;
    mobileClientAttachRevisionRef.current = 0;
    route2LastAttachAttemptAtRef.current = 0;
    route2LastAttachAttemptRevisionRef.current = 0;
    setMobileSession(null);
    setMobilePlayerCanPlay(false);
    setMobileFrozenFrameUrl("");
    setRequestedTargetSeconds(null);
    setPendingSeekPhase("idle");
    setMobileLifecycleState("attached");
    clearOptimizedPlaybackPending();
    clearPlayerBinding();
    const video = videoRef.current;
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }
    setStreamSource(null);
    if (!preserveIntent) {
      browserStartPositionRef.current = 0;
      playbackModeIntentRef.current = "lite";
      setPlaybackModeIntent("lite");
    }
  }

  function handleMissingBrowserPlaybackSession(sessionId) {
    const outcome = resolveBrowserPlaybackSessionNotFound({
      failedSessionId: sessionId,
      currentSession: browserPlaybackCurrentSessionRef.current,
    });
    if (outcome.markDead) {
      markBrowserPlaybackSessionDead(sessionId);
    }
    if (outcome.clearCurrentSession) {
      clearCurrentBrowserPlaybackSession({ preserveIntent: true });
      setPlaybackStatus(`${browserPlaybackLabelTitle} unavailable`);
      setSeekNotice("");
      setPlaybackError(
        `This ${browserPlaybackLabel} session expired before it could attach. Start it again.`,
      );
    }
    return outcome;
  }

  function applyMobileLifecycleStatus(nextState) {
    setMobileLifecycleStateValue(nextState);
    if (nextState === "background-suspended") {
      setPlaybackStatus(`${browserPlaybackLabelTitle} suspended`);
      return;
    }
    if (nextState === "resuming") {
      setPlaybackStatus(`Resuming ${browserPlaybackLabel}`);
      return;
    }
    if (nextState === "recovering") {
      setPlaybackStatus(`Recovering ${browserPlaybackLabel}`);
      return;
    }
    if (nextState === "fatal") {
      setPlaybackStatus(`${browserStreamLabelTitle} failed`);
    }
  }

  function stopMobilePlaybackPolling() {
    mobilePollTokenRef.current += 1;
    window.clearTimeout(mobilePollRef.current);
    mobilePollRef.current = null;
  }

  function resetMobilePlaybackState({ clearPlayer = false } = {}) {
    stopMobilePlaybackPolling();
    attachedOptimizedManifestUrlRef.current = "";
    mobileSessionRef.current = null;
    clearBrowserPlaybackLifecycleState();
    mobilePendingTargetRef.current = null;
    requestedTargetSecondsRef.current = null;
    mobileAutoplayPendingRef.current = false;
    mobileResumeAfterReadyRef.current = false;
    mobileSeekPendingRef.current = false;
    pendingSeekPhaseRef.current = "idle";
    mobileAttachedEpochRef.current = null;
    mobileAttachedManifestRevisionRef.current = "";
    mobileAttachedManifestEndRef.current = 0;
    audioSwitchAttachRef.current = null;
    audioSwitchCandidateValidationRef.current = null;
    mobileCanPlaySeenRef.current = false;
    mobileLoadedDataSeenRef.current = false;
    mobileAwaitingTargetSeekRef.current = false;
    mobileFrameReadyRef.current = false;
    mobileFrameProbePendingRef.current = false;
    mobileReadinessGenerationRef.current += 1;
    setMobileSession(null);
    mobilePlayerCanPlayRef.current = false;
    mobileWarmupProbeActiveRef.current = false;
    mobileWarmupPlaybackObservedRef.current = false;
    mobileWarmupStartPositionRef.current = 0;
    mobileRetargetTransitionRef.current = false;
    mobileLastStablePositionRef.current = 0;
    mobileLifecycleStateRef.current = "attached";
    mobileRecoveryInFlightRef.current = false;
    mobileLastHeartbeatAtRef.current = 0;
    mobileHeartbeatInFlightRef.current = false;
    mobileWasBackgroundedRef.current = false;
    mobileBackgroundHiddenAtRef.current = 0;
    mobileWasPlayingBeforeSuspendRef.current = false;
    window.clearTimeout(mobileStallTimerRef.current);
    mobileStallTimerRef.current = null;
    mobileStallStartedAtRef.current = 0;
    mobileClientAttachRevisionRef.current = 0;
    mobilePendingAttachRevisionRef.current = 0;
    route2LastAttachAttemptAtRef.current = 0;
    route2LastAttachAttemptRevisionRef.current = 0;
    committedPlayheadSecondsRef.current = 0;
    actualMediaElementTimeRef.current = 0;
    browserStartPositionRef.current = 0;
    playbackModeIntentRef.current = "lite";
    setPrepareEstimateObservedAtMs(0);
    setPlaybackModeIntent("lite");
    setMobilePlayerCanPlay(false);
    setMobileFrozenFrameUrl("");
    setRequestedTargetSeconds(null);
    setCommittedPlayheadSeconds(0);
    setActualMediaElementTime(0);
    setPendingSeekPhase("idle");
    setMobileLifecycleState("attached");
    if (clearPlayer) {
      clearPlayerBinding();
      const video = videoRef.current;
      if (video) {
        video.pause();
        video.removeAttribute("src");
        video.load();
      }
      setStreamSource(null);
    }
  }

  function isHlsSessionPayload(payload = mobileSessionRef.current) {
    return isSharedHlsSessionPayload(payload);
  }

  function isRoute2SessionPayload(payload = mobileSessionRef.current) {
    return isHlsSessionPayload(payload);
  }

  function resolveHlsAttachPosition(payload = mobileSessionRef.current) {
    if (typeof payload?.attach_position_seconds === "number") {
      return Math.max(payload.attach_position_seconds, 0);
    }
    return Math.max(payload?.target_position_seconds || 0, 0);
  }

  function resolveRoute2AttachPosition(payload = mobileSessionRef.current) {
    return resolveHlsAttachPosition(payload);
  }

  function resolveSessionMediaElementTime(payload, absoluteSeconds) {
    return toBrowserPlaybackMediaElementSeconds(payload, absoluteSeconds);
  }

  function resolveSessionAbsoluteTime(payload, mediaElementSeconds) {
    return toBrowserPlaybackAbsoluteSeconds(payload, mediaElementSeconds);
  }

  function resolveSessionAttachmentIdentity(payload = mobileSessionRef.current) {
    if (!payload) {
      return null;
    }
    if (isHlsSessionPayload(payload)) {
      return payload.active_epoch_id || null;
    }
    return payload.epoch;
  }

  function resolveRoute2AttachManifestUrl(payload) {
    if (!payload) {
      return "";
    }
    return buildAttachRevisionManifestUrl(
      payload.active_manifest_url || payload.manifest_url,
      payload.attach_revision,
    );
  }

  function debugAudioSwitchAttach(eventName, details = {}) {
    if (typeof process !== "undefined" && process.env?.NODE_ENV === "test") {
      return;
    }
    if (typeof console === "undefined" || typeof console.debug !== "function") {
      return;
    }
    console.debug("[elvern] audio_switch_attach", buildAudioSwitchAttachDiagnostic(eventName, details));
  }

  function audioSwitchStatusHasTargetActive(payload, targetAudioStreamIndex) {
    const switchState = String(payload?.audio_switch_state || "").trim().toLowerCase();
    return Boolean(
      isRoute2SessionPayload(payload)
      && ["active", "committing"].includes(switchState)
      && Number(payload?.active_audio_stream_index) === Number(targetAudioStreamIndex)
    );
  }

  function audioSwitchStatusConfirmsPreviousActive(payload, targetAudioStreamIndex) {
    const switchState = String(payload?.audio_switch_state || "").trim().toLowerCase();
    const candidateState = String(payload?.audio_switch_candidate_state || "").trim().toLowerCase();
    return Boolean(
      isRoute2SessionPayload(payload)
      && Number.isInteger(payload?.active_audio_stream_index)
      && Number(payload.active_audio_stream_index) !== Number(targetAudioStreamIndex)
      && (
        ["failed", "cancelled", "canceled", "active"].includes(switchState)
        || ["none", "failed", "cancelled", "canceled"].includes(candidateState)
        || !payload?.audio_switch_candidate_epoch_id
      )
    );
  }

  function waitForAudioSwitchVerificationPoll(delayMs) {
    const timerHost = typeof window !== "undefined" ? window : globalThis;
    return new Promise((resolve) => {
      timerHost.setTimeout(resolve, delayMs);
    });
  }

  async function pollAudioSwitchCandidateFailure(payload, candidateIdentity, error) {
    const targetAudioStreamIndex = payload.audio_switch_candidate_stream_index;
    if (audioSwitchCandidateValidationRef.current?.identity === candidateIdentity) {
      audioSwitchCandidateValidationRef.current = {
        ...audioSwitchCandidateValidationRef.current,
        state: "checking",
        checkingAtMs: Date.now(),
      };
    }
    debugAudioSwitchAttach("audio_switch_verification_ambiguous_polling", {
      candidateEpochId: payload.audio_switch_candidate_epoch_id,
      candidateAudioStreamIndex: targetAudioStreamIndex,
      activeAudioStreamIndex: payload.active_audio_stream_index,
      validationMethod: "manifest_init_attach_window_segment_fetch",
      reason: error?.message || "validation_failed",
      oldStreamRetained: true,
    });
    setPlaybackError("");
    setSeekNotice("Audio switch verification timed out. Checking current audio...");
    let latestPayload = payload;
    for (let attemptIndex = 0; attemptIndex < 2; attemptIndex += 1) {
      if (attemptIndex > 0) {
        await waitForAudioSwitchVerificationPoll(500);
      }
      const statusPayload = await fetchOptimizedPlaybackSessionStatus({
        browserPlaybackSessionRoot,
        sessionId: payload.session_id,
        statusUrl: payload.status_url,
      });
      const acceptedPayload = acceptBrowserPlaybackSessionPayload(statusPayload, SESSION_SOURCE_STATUS);
      if (acceptedPayload.accepted) {
        latestPayload = statusPayload;
        syncMobilePlaybackState(statusPayload);
      }
      if (audioSwitchStatusHasTargetActive(statusPayload, targetAudioStreamIndex)) {
        const video = videoRef.current;
        debugAudioSwitchAttach("audio_switch_false_failure_suppressed", {
          candidateEpochId: payload.audio_switch_candidate_epoch_id,
          candidateAudioStreamIndex: targetAudioStreamIndex,
          activeAudioStreamIndex: statusPayload.active_audio_stream_index,
          paused: video ? Boolean(video.paused) : true,
          readyState: video ? Number(video.readyState || 0) : 0,
          reason: "target_active_after_validation_failure",
        });
        audioSwitchCandidateValidationRef.current = {
          identity: candidateIdentity,
          state: "committed",
          startedAtMs: audioSwitchCandidateValidationRef.current?.startedAtMs || Date.now(),
          committedAtMs: Date.now(),
        };
        clearOptimizedPlaybackPending();
        setPlaybackError("");
        setSeekNotice(video?.paused ? "Audio switched. Tap play to continue." : "");
        return;
      }
      if (audioSwitchStatusConfirmsPreviousActive(statusPayload, targetAudioStreamIndex)) {
        latestPayload = statusPayload;
        break;
      }
    }
    if (!audioSwitchStatusConfirmsPreviousActive(latestPayload, targetAudioStreamIndex)) {
      try {
        const cancelled = await cancelOptimizedPlaybackAudioTrackCandidate({
          browserPlaybackSessionRoot,
          sessionId: payload.session_id,
          cancelUrl: payload.audio_switch_cancel_url,
        });
        const acceptedPayload = acceptBrowserPlaybackSessionPayload(cancelled, SESSION_SOURCE_STATUS);
        if (acceptedPayload.accepted) {
          latestPayload = cancelled;
          syncMobilePlaybackState(cancelled);
        }
      } catch {
        // Keep the existing playable source if the cancel acknowledgement misses.
      }
    }
    if (audioSwitchStatusConfirmsPreviousActive(latestPayload, targetAudioStreamIndex)) {
      debugAudioSwitchAttach("audio_switch_failure_confirmed_previous_active", {
        candidateEpochId: payload.audio_switch_candidate_epoch_id,
        candidateAudioStreamIndex: targetAudioStreamIndex,
        activeAudioStreamIndex: latestPayload.active_audio_stream_index,
        reason: error?.message || "validation_failed",
        oldStreamRetained: true,
      });
      clearOptimizedPlaybackPending();
      setPlaybackError("");
      setSeekNotice("Audio switch failed. Previous audio is still playing.");
      setPlaybackStatus(browserStreamLabelTitle);
    }
  }

  function maybeStartAudioSwitchCandidateValidation(payload) {
    if (!isAudioSwitchCandidateReadyPayload(payload)) {
      if (isAudioSwitchPreparingState(payload)) {
        debugAudioSwitchAttach("audio_switch_candidate_validation_waiting_for_ready", {
          candidateEpochId: payload?.audio_switch_candidate_epoch_id,
          candidateAudioStreamIndex: payload?.audio_switch_candidate_stream_index,
          activeAudioStreamIndex: payload?.active_audio_stream_index,
          candidateAttachPositionSeconds: payload?.audio_switch_candidate_attach_position_seconds,
          candidateReadyEndSeconds: payload?.audio_switch_candidate_ready_end_seconds,
          requiredServerPrepareSeconds: payload?.audio_switch_candidate_required_runway_seconds,
          actualCandidateRunwaySeconds: payload?.audio_switch_candidate_actual_runway_seconds,
          reason: "backend_candidate_not_ready",
          oldStreamRetained: true,
        });
      }
      return false;
    }
    const candidateIdentity = resolveAudioSwitchCandidateIdentity(payload);
    const current = audioSwitchCandidateValidationRef.current;
    if (
      current?.identity === candidateIdentity
      && ["running", "checking", "committed"].includes(current.state)
    ) {
      return true;
    }
    audioSwitchCandidateValidationRef.current = {
      identity: candidateIdentity,
      state: "running",
      startedAtMs: Date.now(),
    };
    debugAudioSwitchAttach("audio_switch_candidate_validation_started", {
      candidateEpochId: payload.audio_switch_candidate_epoch_id,
      candidateAudioStreamIndex: payload.audio_switch_candidate_stream_index,
      activeAudioStreamIndex: payload.active_audio_stream_index,
      candidateAttachPositionSeconds: payload.audio_switch_candidate_attach_position_seconds,
      candidateReadyEndSeconds: payload.audio_switch_candidate_ready_end_seconds,
      requiredServerPrepareSeconds: payload.audio_switch_candidate_required_runway_seconds,
      actualCandidateRunwaySeconds: payload.audio_switch_candidate_actual_runway_seconds,
      validationMethod: "manifest_init_attach_window_segment_fetch",
      timeoutMs: AUDIO_SWITCH_CANDIDATE_VALIDATION_TIMEOUT_MS,
      oldStreamRetained: true,
    });
    validateAudioSwitchCandidatePayload(payload)
      .then(async (result) => {
        if (audioSwitchCandidateValidationRef.current?.identity !== candidateIdentity) {
          return;
        }
        audioSwitchCandidateValidationRef.current = {
          identity: candidateIdentity,
          state: "validated",
          startedAtMs: audioSwitchCandidateValidationRef.current.startedAtMs,
          validatedAtMs: Date.now(),
        };
        debugAudioSwitchAttach("audio_switch_candidate_validation_succeeded", {
          candidateEpochId: payload.audio_switch_candidate_epoch_id,
          candidateAudioStreamIndex: payload.audio_switch_candidate_stream_index,
          activeAudioStreamIndex: payload.active_audio_stream_index,
          validationMethod: result.validationMethod,
          checkedSegmentCount: result.checkedSegmentCount,
          oldStreamRetained: true,
        });
        const committed = await commitOptimizedPlaybackAudioTrackCandidate({
          browserPlaybackSessionRoot,
          sessionId: payload.session_id,
          commitUrl: payload.audio_switch_commit_url,
        });
        if (audioSwitchCandidateValidationRef.current?.identity !== candidateIdentity) {
          return;
        }
        audioSwitchCandidateValidationRef.current = {
          identity: candidateIdentity,
          state: "committed",
          startedAtMs: audioSwitchCandidateValidationRef.current.startedAtMs,
          committedAtMs: Date.now(),
        };
        debugAudioSwitchAttach("audio_switch_commit_succeeded", {
          candidateEpochId: payload.audio_switch_candidate_epoch_id,
          candidateAudioStreamIndex: payload.audio_switch_candidate_stream_index,
          activeAudioStreamIndex: committed?.active_audio_stream_index,
          oldStreamRetained: Boolean(committed?.old_epoch_retained),
        });
        const acceptedPayload = acceptBrowserPlaybackSessionPayload(committed, SESSION_SOURCE_STATUS);
        if (!acceptedPayload.accepted) {
          return;
        }
        syncMobilePlaybackState(committed);
        setOptimizedPlaybackPending(true);
        setPlaybackError("");
        setSeekNotice(`Preparing ${browserPlaybackLabel}`);
        if (isRoute2AttachReady(committed)) {
          maybeAttachRoute2Authority(committed, {
            autoplay:
              mobileAutoplayPendingRef.current
              || mobileResumeAfterReadyRef.current
              || !videoRef.current?.paused,
          });
        }
        scheduleMobilePlaybackPoll(
          committed.session_id,
          Math.max(1000, Math.round((committed.status_poll_seconds || 1) * 1000)),
        );
      })
      .catch(async (error) => {
        if (audioSwitchCandidateValidationRef.current?.identity !== candidateIdentity) {
          return;
        }
        audioSwitchCandidateValidationRef.current = {
          identity: candidateIdentity,
          state: "failed",
          startedAtMs: audioSwitchCandidateValidationRef.current.startedAtMs,
          failedAtMs: Date.now(),
        };
        debugAudioSwitchAttach("audio_switch_candidate_validation_failed", {
          candidateEpochId: payload.audio_switch_candidate_epoch_id,
          candidateAudioStreamIndex: payload.audio_switch_candidate_stream_index,
          activeAudioStreamIndex: payload.active_audio_stream_index,
          validationMethod: "manifest_init_attach_window_segment_fetch",
          reason: error?.message || "validation_failed",
          oldStreamRetained: true,
        });
        await pollAudioSwitchCandidateFailure(payload, candidateIdentity, error);
      });
    return true;
  }

  function captureAudioSwitchPreviousSnapshot(previousPayload) {
    const attachedManifestUrl = attachedOptimizedManifestUrlRef.current || "";
    const streamSourceUrl = streamSource?.mode === "hls" ? streamSource.url : "";
    const restorableUrl = attachedManifestUrl || streamSourceUrl || resolveRoute2AttachManifestUrl(previousPayload);
    const video = videoRef.current;
    const playbackPositionSeconds = resolvePlaybackRecoveryTargetSeconds({
      currentAbsolutePositionSeconds: previousPayload && video
        ? resolveSessionAbsoluteTime(previousPayload, Math.max(video.currentTime || 0, 0))
        : null,
      committedPlayheadSeconds: committedPlayheadSecondsRef.current,
      actualMediaElementTimeSeconds: actualMediaElementTimeRef.current,
      targetPositionSeconds: previousPayload
        ? resolveMobileAuthorityPosition(previousPayload)
        : null,
    });
    return {
      streamSource: restorableUrl ? { mode: "hls", url: restorableUrl } : null,
      attachedOptimizedManifestUrl: attachedManifestUrl || restorableUrl,
      activeEpochId: previousPayload?.active_epoch_id || mobileAttachedEpochRef.current || null,
      attachRevision: Number(previousPayload?.attach_revision || mobileAttachedManifestRevisionRef.current || 0),
      activeAudioStreamIndex: Number.isInteger(previousPayload?.active_audio_stream_index)
        ? previousPayload.active_audio_stream_index
        : null,
      clientAttachRevision: Number(previousPayload?.client_attach_revision || mobileClientAttachRevisionRef.current || 0),
      mobileAttachedEpoch: mobileAttachedEpochRef.current,
      mobileAttachedManifestRevision: mobileAttachedManifestRevisionRef.current,
      mobileAttachedManifestEnd: mobileAttachedManifestEndRef.current,
      pendingAttachRevision: mobilePendingAttachRevisionRef.current,
      committedPlayheadSeconds: committedPlayheadSecondsRef.current,
      actualMediaElementTimeSeconds: actualMediaElementTimeRef.current,
      requestedTargetSeconds: requestedTargetSecondsRef.current,
      pendingTargetSeconds: mobilePendingTargetRef.current,
      playbackPositionSeconds,
      paused: video ? Boolean(video.paused) : true,
      lifecycleState: mobileLifecycleStateRef.current || "attached",
      mobileSession: previousPayload || null,
      restorable: Boolean(restorableUrl),
    };
  }

  function beginAudioSwitchVerifiedAttach(previousPayload, payload) {
    const expectedAttachRevision = Number(payload?.attach_revision || 0);
    const expectedActiveEpochId = payload?.active_epoch_id || null;
    const expectedManifestUrl = resolveRoute2AttachManifestUrl(payload);
    const targetAudioStreamIndex = Number.isInteger(payload?.active_audio_stream_index)
      ? payload.active_audio_stream_index
      : null;
    if (
      expectedAttachRevision <= 0
      || !expectedActiveEpochId
      || !expectedManifestUrl
      || targetAudioStreamIndex == null
    ) {
      return;
    }
    const current = audioSwitchAttachRef.current;
    if (
      current
      && current.expectedAttachRevision === expectedAttachRevision
      && current.expectedActiveEpochId === expectedActiveEpochId
      && current.targetAudioStreamIndex === targetAudioStreamIndex
      && current.phase !== "failed"
    ) {
      return;
    }
    audioSwitchAttachRef.current = {
      targetAudioStreamIndex,
      expectedAttachRevision,
      expectedActiveEpochId,
      expectedManifestUrl,
      previousActiveEpochId: previousPayload?.active_epoch_id || null,
      previousAttachRevision: Number(previousPayload?.attach_revision || 0),
      previousAudioStreamIndex: Number.isInteger(previousPayload?.active_audio_stream_index)
        ? previousPayload.active_audio_stream_index
        : null,
      previousSnapshot: captureAudioSwitchPreviousSnapshot(previousPayload),
      phase: "pending_attach",
      requestedAtMs: Date.now(),
      sourceSetAtMs: 0,
      loadedAtMs: 0,
      loadedEventName: "",
      sourceGeneration: 0,
      evidenceAfterSourceSet: false,
      retryCount: 0,
    };
    debugAudioSwitchAttach("audio_switch_attach_started", {
      targetAudioStreamIndex,
      expectedAttachRevision,
      expectedActiveEpochId,
      phase: "pending_attach",
      retryCount: 0,
    });
  }

  function isPendingAudioSwitchAttachForRevision(revision) {
    const pending = audioSwitchAttachRef.current;
    return Boolean(
      pending
      && pending.phase !== "acked"
      && pending.phase !== "failed"
      && Number(pending.expectedAttachRevision || 0) === Number(revision || 0)
    );
  }

  function isAudioSwitchAttachLoadedForRevision(revision) {
    const pending = audioSwitchAttachRef.current;
    return Boolean(
      pending
      && Number(pending.expectedAttachRevision || 0) === Number(revision || 0)
      && pending.phase === "loaded"
      && Number.isFinite(pending.loadedAtMs)
    );
  }

  function isFailedAudioSwitchAttachForRevision(revision) {
    const pending = audioSwitchAttachRef.current;
    return Boolean(
      pending
      && Number(pending.expectedAttachRevision || 0) === Number(revision || 0)
      && pending.phase === "failed"
    );
  }

  function audioSwitchPayloadHasError(payload) {
    const switchState = String(payload?.audio_switch_state || "").trim().toLowerCase();
    return Boolean(
      switchState === "failed"
      || String(payload?.audio_switch_error || "").trim()
      || String(payload?.last_error || "").trim()
      || payload?.state === "failed"
      || payload?.state === "expired"
      || payload?.state === "stopped"
    );
  }

  function isAudioSwitchTargetActive(pending, payload = mobileSessionRef.current) {
    if (!pending || !isRoute2SessionPayload(payload) || audioSwitchPayloadHasError(payload)) {
      return false;
    }
    const switchState = String(payload?.audio_switch_state || "").trim().toLowerCase();
    return Boolean(
      switchState === "active"
      && Number(payload?.attach_revision || 0) === Number(pending.expectedAttachRevision || 0)
      && (payload?.active_epoch_id || null) === pending.expectedActiveEpochId
      && Number(payload?.active_audio_stream_index) === Number(pending.targetAudioStreamIndex)
    );
  }

  function isAudioSwitchExpectedSourceAttached(pending) {
    return Boolean(
      pending
      && attachedOptimizedManifestUrlRef.current === pending.expectedManifestUrl
      && mobileAttachedEpochRef.current === pending.expectedActiveEpochId
      && mobileAttachedManifestRevisionRef.current === String(pending.expectedAttachRevision || 0)
    );
  }

  function resolveAudioSwitchAttachSuccessReason(pending, payload = mobileSessionRef.current, { playing = null } = {}) {
    if (!isAudioSwitchTargetActive(pending, payload)) {
      return "";
    }
    const expectedRevision = Number(pending.expectedAttachRevision || 0);
    if (Number(payload?.client_attach_revision || 0) >= expectedRevision) {
      return "client_attach_revision";
    }
    if (pending.phase === "failed") {
      return "";
    }
    if (!isAudioSwitchExpectedSourceAttached(pending)) {
      return "";
    }
    const sourceSetAtMs = Number(pending.sourceSetAtMs || 0);
    const loadedAtMs = Number(pending.loadedAtMs || 0);
    const loadedAfterSourceSet = Boolean(
      sourceSetAtMs > 0
      && loadedAtMs >= sourceSetAtMs
      && pending.evidenceAfterSourceSet
    );
    const playingAfterSourceSet = Boolean(
      sourceSetAtMs > 0
      && Number(pending.postSourcePlayingAtMs || 0) >= sourceSetAtMs
    );
    const advancingAfterSourceSet = Boolean(
      sourceSetAtMs > 0
      && Number(pending.postSourceTimeAdvancingAtMs || 0) >= sourceSetAtMs
    );
    if (loadedAfterSourceSet && pending.loadedEventName) {
      return "post_source_loaded_event";
    }
    if (playingAfterSourceSet) {
      return "post_source_playing_event";
    }
    if (advancingAfterSourceSet) {
      return "post_source_time_advancing";
    }
    if (loadedAfterSourceSet && payload?.client_time_advancing === true) {
      return "backend_active_plus_client_progress_after_source_set";
    }
    return "";
  }

  function markAudioSwitchAttachSucceeded(
    pending,
    {
      payload = mobileSessionRef.current,
      successReason = "inferred",
      playing = null,
      sendHeartbeat = true,
    } = {},
  ) {
    if (!pending || !isAudioSwitchTargetActive(pending, payload)) {
      return false;
    }
    const expectedRevision = Number(pending.expectedAttachRevision || 0);
    audioSwitchAttachRef.current = {
      ...pending,
      phase: "acked",
      succeededAtMs: Date.now(),
      successReason,
    };
    mobilePendingAttachRevisionRef.current = Math.max(
      mobilePendingAttachRevisionRef.current,
      expectedRevision,
    );
    const video = videoRef.current;
    const pausedReady = video ? Boolean(video.paused) : playing === false;
    setSeekNotice(pausedReady ? "Audio switched. Tap play to continue." : "");
    setPlaybackError("");
    setMobileLifecycleStateValue("attached");
    if (pausedReady) {
      debugAudioSwitchAttach("audio_switch_success_ready_paused", {
        expectedAttachRevision: pending.expectedAttachRevision,
        expectedActiveEpochId: pending.expectedActiveEpochId,
        targetAudioStreamIndex: pending.targetAudioStreamIndex,
        paused: true,
        readyState: video ? Number(video.readyState || 0) : 0,
        successReason,
      });
    }
    debugAudioSwitchAttach("audio_switch_success_inferred_post_source", {
      expectedAttachRevision: pending.expectedAttachRevision,
      expectedActiveEpochId: pending.expectedActiveEpochId,
      targetAudioStreamIndex: pending.targetAudioStreamIndex,
      phase: "acked",
      retryCount: pending.retryCount || 0,
      clientAttachRevision: Number(payload?.client_attach_revision || 0),
      currentAttachRevision: Number(payload?.attach_revision || 0),
      sourceSetAtMs: pending.sourceSetAtMs,
      loadedAtMs: pending.loadedAtMs,
      evidenceAfterSourceSet: pending.evidenceAfterSourceSet,
      successReason,
    });
    if (sendHeartbeat && Number(payload?.client_attach_revision || 0) < expectedRevision) {
      postMobileRuntimeHeartbeat({
        lifecycleState: "attached",
        stalled: false,
        playing: playing != null ? playing : video ? !video.paused : null,
        clientAttachRevision: expectedRevision,
        force: true,
      }).catch(() => {
        // Subsequent heartbeats will keep carrying the inferred attach revision.
      });
    }
    return true;
  }

  function maybeInferAudioSwitchAttachSuccess({
    payload = mobileSessionRef.current,
    playing = null,
    sendHeartbeat = true,
  } = {}) {
    const pending = audioSwitchAttachRef.current;
    if (!pending || pending.phase === "acked") {
      return false;
    }
    const successReason = resolveAudioSwitchAttachSuccessReason(pending, payload, { playing });
    if (!successReason) {
      return false;
    }
    return markAudioSwitchAttachSucceeded(pending, {
      payload,
      successReason,
      playing,
      sendHeartbeat,
    });
  }

  function markAudioSwitchAttachSourceSet(payload, sessionManifestUrl) {
    const pending = audioSwitchAttachRef.current;
    if (
      !pending
      || pending.phase === "failed"
      || pending.phase === "acked"
      || Number(pending.expectedAttachRevision || 0) !== Number(payload?.attach_revision || 0)
      || pending.expectedActiveEpochId !== (payload?.active_epoch_id || null)
    ) {
      return;
    }
    audioSwitchAttachRef.current = {
      ...pending,
      expectedManifestUrl: sessionManifestUrl || pending.expectedManifestUrl,
      phase: "source_set",
      sourceSetAtMs: Date.now(),
      loadedAtMs: 0,
      loadedEventName: "",
      sourceGeneration: (pending.sourceGeneration || 0) + 1,
      evidenceAfterSourceSet: false,
      postSourcePlayingAtMs: 0,
      postSourceTimeAdvancingAtMs: 0,
    };
    debugAudioSwitchAttach("audio_switch_source_set", {
      expectedAttachRevision: pending.expectedAttachRevision,
      expectedActiveEpochId: pending.expectedActiveEpochId,
      targetAudioStreamIndex: pending.targetAudioStreamIndex,
      phase: "source_set",
      retryCount: pending.retryCount || 0,
      sourceSetAtMs: audioSwitchAttachRef.current.sourceSetAtMs,
      loadedAtMs: 0,
      sourceGeneration: audioSwitchAttachRef.current.sourceGeneration,
    });
  }

  function markAudioSwitchAttachLoaded({ eventName = "" } = {}) {
    const pending = audioSwitchAttachRef.current;
    if (!pending || pending.phase !== "source_set") {
      return false;
    }
    if (attachedOptimizedManifestUrlRef.current !== pending.expectedManifestUrl) {
      return false;
    }
    if (mobileAttachedEpochRef.current !== pending.expectedActiveEpochId) {
      return false;
    }
    if (mobileAttachedManifestRevisionRef.current !== String(pending.expectedAttachRevision || 0)) {
      return false;
    }
    audioSwitchAttachRef.current = {
      ...pending,
      phase: "loaded",
      loadedAtMs: Date.now(),
      loadedEventName: eventName,
      evidenceAfterSourceSet: true,
    };
    debugAudioSwitchAttach("audio_switch_loaded", {
      loadedEventName: eventName,
      expectedAttachRevision: pending.expectedAttachRevision,
      expectedActiveEpochId: pending.expectedActiveEpochId,
      targetAudioStreamIndex: pending.targetAudioStreamIndex,
      phase: "loaded",
      retryCount: pending.retryCount || 0,
      sourceSetAtMs: pending.sourceSetAtMs,
      loadedAtMs: audioSwitchAttachRef.current.loadedAtMs,
    });
    return true;
  }

  function markAudioSwitchPostSourceEvidence({ eventName = "", playing = null } = {}) {
    const pending = audioSwitchAttachRef.current;
    if (!pending || pending.phase === "failed" || pending.phase === "acked") {
      return false;
    }
    if (!isAudioSwitchExpectedSourceAttached(pending) || Number(pending.sourceSetAtMs || 0) <= 0) {
      return false;
    }
    const normalizedEventName = String(eventName || "").trim().toLowerCase();
    const nextEvidence = {
      ...pending,
      evidenceAfterSourceSet: true,
    };
    let changed = false;
    if (normalizedEventName === "playing" && playing === true) {
      nextEvidence.postSourcePlayingAtMs = Date.now();
      changed = true;
    }
    if (normalizedEventName === "timeupdate") {
      nextEvidence.postSourceTimeAdvancingAtMs = Date.now();
      changed = true;
    }
    if (!changed) {
      return false;
    }
    audioSwitchAttachRef.current = nextEvidence;
    return true;
  }

  function restorePreviousAudioSwitchSource(pending, elapsedMs = 0) {
    const snapshot = pending?.previousSnapshot || null;
    if (!snapshot?.restorable || !snapshot?.streamSource?.url) {
      return false;
    }
    const restoredAtMs = Date.now();
    const video = videoRef.current;
    audioSwitchAttachRef.current = {
      ...pending,
      phase: "failed",
      failedAtMs: restoredAtMs,
      restoredPrevious: true,
      failureReason: "attach_timeout",
    };
    attachedOptimizedManifestUrlRef.current = snapshot.attachedOptimizedManifestUrl || snapshot.streamSource.url;
    mobileAttachedEpochRef.current = snapshot.mobileAttachedEpoch || snapshot.activeEpochId || null;
    mobileAttachedManifestRevisionRef.current = snapshot.mobileAttachedManifestRevision || String(snapshot.attachRevision || 0);
    mobileAttachedManifestEndRef.current = snapshot.mobileAttachedManifestEnd || 0;
    mobilePendingAttachRevisionRef.current = Math.max(
      Number(snapshot.pendingAttachRevision || 0),
      Number(snapshot.clientAttachRevision || 0),
    );
    mobileClientAttachRevisionRef.current = Math.max(
      Number(snapshot.clientAttachRevision || 0),
      Number(snapshot.attachRevision || 0),
    );
    if (snapshot.mobileSession) {
      mobileSessionRef.current = snapshot.mobileSession;
      setMobileSession(snapshot.mobileSession);
    }
    mobilePendingTargetRef.current = snapshot.pendingTargetSeconds ?? null;
    requestedTargetSecondsRef.current = snapshot.requestedTargetSeconds ?? null;
    committedPlayheadSecondsRef.current = Math.max(Number(snapshot.committedPlayheadSeconds || 0), 0);
    actualMediaElementTimeRef.current = Math.max(Number(snapshot.actualMediaElementTimeSeconds || 0), 0);
    setCommittedPlayheadSeconds(committedPlayheadSecondsRef.current);
    setActualMediaElementTime(actualMediaElementTimeRef.current);
    setRequestedTargetSeconds(requestedTargetSecondsRef.current);
    setPlaybackPosition(Math.max(Number(snapshot.playbackPositionSeconds || 0), 0));
    mobileWarmupProbeActiveRef.current = false;
    mobileAutoplayPendingRef.current = !snapshot.paused;
    mobileResumeAfterReadyRef.current = !snapshot.paused;
    mobilePlayerCanPlayRef.current = false;
    setMobilePlayerCanPlay(false);
    setOptimizedPlaybackPending(true);
    setSeekNotice("Restoring previous audio...");
    setPlaybackStatus(`Preparing ${browserPlaybackLabel}`);
    setMobileLifecycleStateValue(snapshot.lifecycleState || "attached");
    setPlaybackError(AUDIO_SWITCH_ATTACH_RESTORED_MESSAGE);
    clearPlayerBinding();
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }
    setVideoElementKey((current) => current + 1);
    setStreamSource((existing) => (
      existing?.mode === "hls" && existing.url === snapshot.streamSource.url
        ? existing
        : { ...snapshot.streamSource }
    ));
    debugAudioSwitchAttach("audio_switch_attach_failed_restored_previous", {
      expectedAttachRevision: pending.expectedAttachRevision,
      expectedActiveEpochId: pending.expectedActiveEpochId,
      targetAudioStreamIndex: pending.targetAudioStreamIndex,
      previousAttachRevision: pending.previousAttachRevision,
      previousActiveEpochId: pending.previousActiveEpochId,
      previousAudioStreamIndex: pending.previousAudioStreamIndex,
      phase: "failed",
      elapsedMs,
      retryCount: pending.retryCount || 0,
      clientAttachRevision: mobileClientAttachRevisionRef.current || 0,
      currentAttachRevision: Number(mobileSessionRef.current?.attach_revision || 0),
      sourceSetAtMs: pending.sourceSetAtMs,
      loadedAtMs: pending.loadedAtMs,
      restoredPrevious: true,
    });
    return true;
  }

  function failAudioSwitchAttach(pending, elapsedMs = 0) {
    if (!pending || pending.phase === "failed" || pending.phase === "acked") {
      return;
    }
    if (restorePreviousAudioSwitchSource(pending, elapsedMs)) {
      return;
    }
    audioSwitchAttachRef.current = {
      ...pending,
      phase: "failed",
      failedAtMs: Date.now(),
      restoredPrevious: false,
      failureReason: "attach_timeout",
    };
    mobilePendingAttachRevisionRef.current = 0;
    mobileWarmupProbeActiveRef.current = false;
    mobileAutoplayPendingRef.current = false;
    mobileResumeAfterReadyRef.current = false;
    mobilePlayerCanPlayRef.current = false;
    setMobilePlayerCanPlay(false);
    clearOptimizedPlaybackPending();
    setSeekNotice("");
    setPlaybackStatus(`${browserStreamLabelTitle} failed`);
    setMobileLifecycleStateValue("fatal");
    setPlaybackError(AUDIO_SWITCH_ATTACH_RESTART_REQUIRED_MESSAGE);
    debugAudioSwitchAttach("audio_switch_attach_failed_restart_required", {
      expectedAttachRevision: pending.expectedAttachRevision,
      expectedActiveEpochId: pending.expectedActiveEpochId,
      targetAudioStreamIndex: pending.targetAudioStreamIndex,
      previousAttachRevision: pending.previousAttachRevision,
      previousActiveEpochId: pending.previousActiveEpochId,
      previousAudioStreamIndex: pending.previousAudioStreamIndex,
      phase: "failed",
      elapsedMs,
      retryCount: pending.retryCount || 0,
      clientAttachRevision: mobileClientAttachRevisionRef.current || 0,
      currentAttachRevision: Number(mobileSessionRef.current?.attach_revision || 0),
      sourceSetAtMs: pending.sourceSetAtMs,
      loadedAtMs: pending.loadedAtMs,
      restoredPrevious: false,
    });
  }

  function handleAudioSwitchAttachLoadTimeout() {
    const pending = audioSwitchAttachRef.current;
    if (!isAudioSwitchAttachWaiting(pending)) {
      return;
    }
    const nowMs = Date.now();
    const startedAtMs = Number.isFinite(pending.sourceSetAtMs)
      ? pending.sourceSetAtMs
      : Number.isFinite(pending.requestedAtMs)
        ? pending.requestedAtMs
        : nowMs;
    const elapsedMs = Math.max(0, nowMs - startedAtMs);
    if (elapsedMs < AUDIO_SWITCH_ATTACH_LOAD_TIMEOUT_MS) {
      return;
    }
    const video = videoRef.current;
    if (maybeInferAudioSwitchAttachSuccess({
      payload: mobileSessionRef.current,
      playing: video ? !video.paused : null,
      sendHeartbeat: true,
    })) {
      return;
    }
    debugAudioSwitchAttach("audio_switch_attach_timeout", {
      expectedAttachRevision: pending.expectedAttachRevision,
      expectedActiveEpochId: pending.expectedActiveEpochId,
      targetAudioStreamIndex: pending.targetAudioStreamIndex,
      phase: pending.phase,
      elapsedMs,
      retryCount: pending.retryCount || 0,
      clientAttachRevision: mobileClientAttachRevisionRef.current || 0,
      currentAttachRevision: Number(mobileSessionRef.current?.attach_revision || 0),
      sourceSetAtMs: pending.sourceSetAtMs,
      loadedAtMs: pending.loadedAtMs,
    });
    const activeSession = mobileSessionRef.current;
    const sessionMatchesPending = Boolean(
      isRoute2AttachReady(activeSession)
      && Number(activeSession?.attach_revision || 0) === Number(pending.expectedAttachRevision || 0)
      && (activeSession?.active_epoch_id || null) === pending.expectedActiveEpochId
      && Number(activeSession?.active_audio_stream_index) === Number(pending.targetAudioStreamIndex)
    );
    if ((pending.retryCount || 0) < AUDIO_SWITCH_ATTACH_RETRY_LIMIT && sessionMatchesPending) {
      const retryCount = (pending.retryCount || 0) + 1;
      audioSwitchAttachRef.current = {
        ...pending,
        phase: "pending_attach",
        retryCount,
        sourceSetAtMs: 0,
        loadedAtMs: 0,
        loadedEventName: "",
        evidenceAfterSourceSet: false,
        retryStartedAtMs: nowMs,
      };
      debugAudioSwitchAttach("audio_switch_attach_retry_started", {
        expectedAttachRevision: pending.expectedAttachRevision,
        expectedActiveEpochId: pending.expectedActiveEpochId,
        targetAudioStreamIndex: pending.targetAudioStreamIndex,
        phase: "pending_attach",
        elapsedMs,
        retryCount,
        clientAttachRevision: mobileClientAttachRevisionRef.current || 0,
        currentAttachRevision: Number(activeSession?.attach_revision || 0),
        sourceSetAtMs: 0,
        loadedAtMs: 0,
      });
      armMobileManifestAttachment(activeSession, {
        autoplay:
          mobileAutoplayPendingRef.current
          || mobileResumeAfterReadyRef.current
          || !videoRef.current?.paused,
        targetPosition: resolveLivePlaybackRecoveryTarget(activeSession),
        preserveAuthority: true,
        resetSeekPreparation: true,
        forceReattach: true,
        forceVideoRemount: true,
      });
      return;
    }
    if (maybeInferAudioSwitchAttachSuccess({
      payload: activeSession,
      playing: video ? !video.paused : null,
      sendHeartbeat: true,
    })) {
      return;
    }
    if (!sessionMatchesPending) {
      debugAudioSwitchAttach("audio_switch_attach_retry_failed", {
        expectedAttachRevision: pending.expectedAttachRevision,
        expectedActiveEpochId: pending.expectedActiveEpochId,
        targetAudioStreamIndex: pending.targetAudioStreamIndex,
        phase: pending.phase,
        elapsedMs,
        retryCount: pending.retryCount || 0,
        clientAttachRevision: mobileClientAttachRevisionRef.current || 0,
        currentAttachRevision: Number(activeSession?.attach_revision || 0),
        sourceSetAtMs: pending.sourceSetAtMs,
        loadedAtMs: pending.loadedAtMs,
      });
    }
    failAudioSwitchAttach(pending, elapsedMs);
  }

  function isHlsAttachReady(payload = mobileSessionRef.current) {
    const requiresFullModeReady = getPlaybackMode(payload?.playback_mode) === "full";
    return Boolean(
      isHlsSessionPayload(payload)
      && payload?.attach_ready
      && (!requiresFullModeReady || payload?.mode_ready)
      && payload?.active_manifest_url
      && (payload?.attach_revision || 0) > 0
    );
  }

  function isRoute2AttachReady(payload = mobileSessionRef.current) {
    return isHlsAttachReady(payload);
  }

  function resolveHlsHeartbeatAttachRevision(payload = mobileSessionRef.current) {
    if (!isHlsSessionPayload(payload)) {
      return 0;
    }
    const authorityRevision = Number(payload?.attach_revision || 0);
    if (
      isPendingAudioSwitchAttachForRevision(authorityRevision)
      && !isAudioSwitchAttachLoadedForRevision(authorityRevision)
    ) {
      return Math.min(
        authorityRevision,
        Math.max(
          mobileClientAttachRevisionRef.current || 0,
          Number(payload?.client_attach_revision || 0),
        ),
      );
    }
    const pendingRevision = Number(mobilePendingAttachRevisionRef.current || 0);
    const confirmedRevision = Number(mobileClientAttachRevisionRef.current || 0);
    return Math.min(authorityRevision, Math.max(confirmedRevision, pendingRevision));
  }

  function resolveRoute2HeartbeatAttachRevision(payload = mobileSessionRef.current) {
    return resolveHlsHeartbeatAttachRevision(payload);
  }

  function syncMobilePlaybackState(payload) {
    const previousPayload = mobileSessionRef.current;
    if (isRoute2SessionPayload(previousPayload) && isRoute2SessionPayload(payload)) {
      const previousRevision = Number(previousPayload?.attach_revision || 0);
      const incomingRevision = Number(payload?.attach_revision || 0);
      if (incomingRevision < previousRevision) {
        return;
      }
      if (isAudioSwitchPromotionPayload(previousPayload, payload)) {
        beginAudioSwitchVerifiedAttach(previousPayload, payload);
      }
    }
    mobileSessionRef.current = payload;
    setMobileSession(payload);
    const resolvedPlaybackMode = getPlaybackMode(payload?.playback_mode || playbackModeIntentRef.current);
    playbackModeIntentRef.current = resolvedPlaybackMode;
    setPlaybackModeIntent(resolvedPlaybackMode);
    const nowMs = Date.now();
    setPrepareEstimateNowMs(nowMs);
    setPrepareEstimateObservedAtMs(
      isRoute2SessionPayload(payload) && getSessionModeEstimateSeconds(payload) != null ? nowMs : 0,
    );
    if (isRoute2SessionPayload(payload) && typeof payload.client_attach_revision === "number") {
      mobileClientAttachRevisionRef.current = Math.max(
        mobileClientAttachRevisionRef.current,
        payload.client_attach_revision,
      );
      const latestSeenAttachRevision = Math.min(
        payload.client_attach_revision,
        Number(payload.attach_revision || 0),
        Number(previousPayload?.attach_revision || 0),
      );
      if ((payload.client_attach_revision || 0) >= latestSeenAttachRevision) {
        mobilePendingAttachRevisionRef.current = 0;
      }
      const pendingAudioAttach = audioSwitchAttachRef.current;
      if (
        pendingAudioAttach
        && pendingAudioAttach.phase !== "failed"
        && pendingAudioAttach.phase !== "acked"
        && Number(payload.client_attach_revision || 0) >= pendingAudioAttach.expectedAttachRevision
      ) {
        markAudioSwitchAttachSucceeded(pendingAudioAttach, {
          payload,
          successReason: "client_attach_revision",
          playing: payload?.client_is_playing === true ? true : null,
          sendHeartbeat: false,
        });
        debugAudioSwitchAttach("audio_switch_ack_confirmed", {
          expectedAttachRevision: pendingAudioAttach.expectedAttachRevision,
          clientAttachRevision: payload.client_attach_revision,
        });
      }
    }
    maybeInferAudioSwitchAttachSuccess({
      payload,
      playing: payload?.client_is_playing === true ? true : null,
      sendHeartbeat: false,
    });
    maybeStartAudioSwitchCandidateValidation(payload);
    if (typeof payload.committed_playhead_seconds === "number") {
      committedPlayheadSecondsRef.current = Math.max(payload.committed_playhead_seconds, 0);
      setCommittedPlayheadSeconds(payload.committed_playhead_seconds);
    }
    if (typeof payload.actual_media_element_time_seconds === "number") {
      actualMediaElementTimeRef.current = Math.max(payload.actual_media_element_time_seconds, 0);
      setActualMediaElementTime(payload.actual_media_element_time_seconds);
    }
    if (typeof payload.pending_target_seconds === "number") {
      requestedTargetSecondsRef.current = payload.pending_target_seconds;
      setRequestedTargetSeconds(payload.pending_target_seconds);
      if (mobileSeekPendingRef.current) {
        pendingSeekPhaseRef.current = "preparing";
        setPendingSeekPhase("preparing");
      }
    } else if (!mobileSeekPendingRef.current) {
      requestedTargetSecondsRef.current = null;
      setRequestedTargetSeconds(null);
    }
    if (payload.last_error) {
      setPlaybackError(payload.last_error);
      setPlaybackStatus(`${browserStreamLabelTitle} failed`);
      if (payload.state === "failed") {
        clearOptimizedPlaybackPending();
      }
      return;
    }
    if (payload.state === "failed") {
      clearOptimizedPlaybackPending();
      setPlaybackStatus(`${browserStreamLabelTitle} failed`);
      if (!payload.attach_ready) {
        setPlaybackError(`${browserPlaybackLabelTitle} failed for this playback session`);
      }
      return;
    }
    if (payload.state === "ready") {
      if (!payload.attach_ready) {
        setPlaybackStatus(`Preparing ${browserPlaybackLabel}`);
        return;
      }
      if ((payload.client_attach_revision || 0) >= (payload.attach_revision || 0)) {
        setMobileLifecycleStateValue("attached");
        setPlaybackStatus(browserStreamLabelTitle);
        return;
      }
      setPlaybackStatus(browserReadyLabelTitle);
      return;
    }
    if (payload.state === "queued") {
      setPlaybackStatus(`${browserPlaybackLabelTitle} queued`);
      return;
    }
    if (payload.state === "seeking") {
      setPlaybackStatus("Preparing target playback");
      return;
    }
    if (payload.state === "attached") {
      if (payload.pending_target_seconds != null) {
        setPlaybackStatus(`Preparing ${browserPlaybackLabel}`);
        return;
      }
      setPlaybackStatus(browserStreamLabelTitle);
      return;
    }
    setPlaybackStatus(browserPlaybackLabelTitle);
  }

  function resolveAttachedManifestEndSeconds(payload = mobileSessionRef.current) {
    if (!payload) {
      return 0;
    }
    return getBrowserPlaybackAttachedManifestEndSeconds(payload);
  }

  function resolveCurrentManifestPosition(payload = mobileSessionRef.current) {
    if (!payload) {
      return 0;
    }
    const video = videoRef.current;
    if (video) {
      return resolvePlaybackRecoveryTargetSeconds({
        currentAbsolutePositionSeconds: resolveSessionAbsoluteTime(payload, Math.max(video.currentTime || 0, 0)),
        committedPlayheadSeconds: committedPlayheadSecondsRef.current,
        actualMediaElementTimeSeconds: actualMediaElementTimeRef.current,
        targetPositionSeconds: isRoute2SessionPayload(payload)
          ? resolveRoute2AttachPosition(payload)
          : payload.target_position_seconds,
      });
    }
    return resolvePlaybackRecoveryTargetSeconds({
      committedPlayheadSeconds: committedPlayheadSecondsRef.current,
      actualMediaElementTimeSeconds: actualMediaElementTimeRef.current,
      targetPositionSeconds: isRoute2SessionPayload(payload)
        ? resolveRoute2AttachPosition(payload)
        : payload.target_position_seconds,
    });
  }

  function maybeRefreshAttachedMobileManifest(payload = mobileSessionRef.current) {
    if (
      !payload?.playback_commit_ready
      || !attachedOptimizedManifestUrlRef.current
      || !streamSource
      || streamSource.mode !== "hls"
    ) {
      return false;
    }
    const currentPosition = resolveCurrentManifestPosition(payload);
    const currentManifestEnd = resolveAttachedManifestEndSeconds(payload);
    const manifestState = classifyManifestWindowState({
      absolutePositionSeconds: currentPosition,
      manifestEndSeconds: currentManifestEnd,
      fullDurationSeconds: payload.duration_seconds || 0,
      refreshRunwaySeconds: SESSION_MANIFEST_REFRESH_RUNWAY_SECONDS,
    });
    if (!manifestState.shouldRefreshManifest) {
      return false;
    }
    if (!shouldForceReattachForManifestWindowRefresh(payload)) {
      mobileAttachedManifestEndRef.current = currentManifestEnd;
      mobileAttachedManifestRevisionRef.current = String(payload.attach_revision || 0);
      mobileAttachedEpochRef.current = resolveSessionAttachmentIdentity(payload);
      return false;
    }
    armMobileManifestAttachment(payload, {
      autoplay: !videoRef.current?.paused,
      targetPosition: currentPosition,
      preserveAuthority: true,
      resetSeekPreparation: false,
      forceReattach: true,
    });
    return true;
  }

  function armMobileManifestAttachment(
    payload,
    {
      autoplay = false,
      targetPosition = null,
      preserveAuthority = false,
      resetSeekPreparation = false,
      forceReattach = false,
      forceVideoRemount = false,
    } = {},
  ) {
    syncMobilePlaybackState(payload);
    const route2Session = isRoute2SessionPayload(payload);
    const nextAttachmentIdentity = resolveSessionAttachmentIdentity(payload);
    const manifestRevision = route2Session
      ? String(payload.attach_revision || 0)
      : (payload.manifest_revision || String(payload.epoch));
    const sessionManifestUrl = route2Session
      ? buildAttachRevisionManifestUrl(
          payload.active_manifest_url || payload.manifest_url,
          payload.attach_revision,
        )
      : buildSessionManifestUrl(payload.manifest_url, manifestRevision);
    mobileAutoplayPendingRef.current = autoplay;
    if (targetPosition != null) {
      mobilePendingTargetRef.current = targetPosition;
      requestedTargetSecondsRef.current = targetPosition;
      setRequestedTargetSeconds(targetPosition);
    } else if (mobilePendingTargetRef.current == null) {
      const authorityTarget = route2Session
        ? resolveRoute2AttachPosition(payload)
        : payload.target_position_seconds;
      mobilePendingTargetRef.current = authorityTarget;
      requestedTargetSecondsRef.current = authorityTarget;
      setRequestedTargetSeconds(authorityTarget);
    }
    const shouldRemountVideoElement = Boolean(
      iosMobile
      && route2Session
      && forceReattach
      && attachedOptimizedManifestUrlRef.current
      && (
        forceVideoRemount
        || mobileLifecycleStateRef.current === "recovering"
        || mobileRecoveryInFlightRef.current
        || attachedOptimizedManifestUrlRef.current !== sessionManifestUrl
        || mobileAttachedEpochRef.current !== nextAttachmentIdentity
      )
    );
    if (shouldRemountVideoElement) {
      clearPlayerBinding();
      const currentVideo = videoRef.current;
      if (currentVideo) {
        currentVideo.pause();
        currentVideo.removeAttribute("src");
        currentVideo.load();
      }
      setVideoElementKey((current) => current + 1);
    }
    mobileAttachedEpochRef.current = nextAttachmentIdentity;
    mobileAttachedManifestRevisionRef.current = manifestRevision;
    mobileAttachedManifestEndRef.current = resolveAttachedManifestEndSeconds(payload);
    attachedOptimizedManifestUrlRef.current = sessionManifestUrl;
    if (route2Session) {
      markAudioSwitchAttachSourceSet(payload, sessionManifestUrl);
    }
    mobileCanPlaySeenRef.current = false;
    mobileLoadedDataSeenRef.current = false;
    mobileAwaitingTargetSeekRef.current = resolveSessionMediaElementTime(
      payload,
      targetPosition != null ? targetPosition : payload.target_position_seconds,
    ) > 0.5;
    mobileFrameReadyRef.current = false;
    mobileFrameProbePendingRef.current = false;
    mobileReadinessGenerationRef.current += 1;
    mobilePlayerCanPlayRef.current = false;
    mobileWarmupProbeActiveRef.current = false;
    mobileWarmupPlaybackObservedRef.current = false;
    mobileWarmupStartPositionRef.current = 0;
    setMobilePlayerCanPlay(false);
    if (!preserveAuthority) {
      committedPlayheadSecondsRef.current = 0;
      actualMediaElementTimeRef.current = 0;
      setCommittedPlayheadSeconds(0);
      setActualMediaElementTime(0);
    }
    setStreamSource((existing) => {
      if (!forceReattach && existing?.mode === "hls" && existing.url === sessionManifestUrl) {
        return existing;
      }
      return {
        mode: "hls",
        url: sessionManifestUrl,
      };
    });
    if (resetSeekPreparation) {
      mobileSeekPendingRef.current = false;
      pendingSeekPhaseRef.current = "idle";
      setPendingSeekPhase("idle");
    }
    if (!preserveAuthority) {
      setPlaybackPosition(targetPosition != null ? targetPosition : payload.target_position_seconds || 0);
    }
    if (!forceReattach) {
      mobileRetargetTransitionRef.current = false;
      setMobileFrozenFrameUrl("");
    }
    setPlaybackError("");
    setSeekNotice("");
    applyMobileLifecycleStatus(forceReattach ? "recovering" : "attached");
    if (!forceReattach) {
      clearOptimizedPlaybackPending();
    }
    const currentVideo = videoRef.current;
    if (!forceReattach && currentVideo && currentVideo.readyState >= 1) {
      maybeAcknowledgeRoute2Attachment({ playing: !currentVideo.paused, force: true });
    }
    if (route2Session) {
      const attachRevision = Number(payload.attach_revision || 0);
      route2LastAttachAttemptAtRef.current = Date.now();
      route2LastAttachAttemptRevisionRef.current = attachRevision;
      mobilePendingAttachRevisionRef.current = Math.max(
        mobilePendingAttachRevisionRef.current,
        attachRevision,
      );
      if (mobilePendingAttachRevisionRef.current > attachRevision) {
        mobilePendingAttachRevisionRef.current = attachRevision;
      }
      if (payload.client_attach_revision != null) {
        mobileClientAttachRevisionRef.current = Math.max(
          mobileClientAttachRevisionRef.current,
          Number(payload.client_attach_revision || 0),
        );
      }
    }
  }

  function hlsAttachmentNeedsReattach(payload = mobileSessionRef.current) {
    if (!isHlsAttachReady(payload)) {
      return false;
    }
    const authorityRevision = String(payload.attach_revision || 0);
    const authorityRevisionNumber = Number(payload.attach_revision || 0);
    const nextIdentity = resolveSessionAttachmentIdentity(payload);
    return Boolean(
      !attachedOptimizedManifestUrlRef.current
      || mobileAttachedManifestRevisionRef.current !== authorityRevision
      || mobileAttachedEpochRef.current !== nextIdentity
      || route2LastAttachAttemptRevisionRef.current !== authorityRevisionNumber
      || mobilePendingAttachRevisionRef.current > authorityRevisionNumber
    );
  }

  function route2AttachmentNeedsReattach(payload = mobileSessionRef.current) {
    return hlsAttachmentNeedsReattach(payload);
  }

  function isFailedAudioSwitchAttachPayload(payload) {
    const pending = audioSwitchAttachRef.current;
    return Boolean(
      pending
      && pending.phase === "failed"
      && isRoute2SessionPayload(payload)
      && Number(payload?.attach_revision || 0) === Number(pending.expectedAttachRevision || 0)
      && (payload?.active_epoch_id || null) === pending.expectedActiveEpochId
      && Number(payload?.active_audio_stream_index) === Number(pending.targetAudioStreamIndex)
    );
  }

  async function softResumeMobilePlaybackAfterBackground(trigger) {
    void trigger;
    const activeSession = mobileSessionRef.current;
    if (!activeSession?.session_id || mobileRecoveryInFlightRef.current) {
      return;
    }
    const hiddenAt = mobileBackgroundHiddenAtRef.current || 0;
    const backgroundDurationMs = hiddenAt > 0 ? Math.max(Date.now() - hiddenAt, 0) : 0;
    const video = videoRef.current;
    const shouldResume =
      mobileWasPlayingBeforeSuspendRef.current || (!video?.paused && mobilePlayerCanPlayRef.current);
    mobileRecoveryInFlightRef.current = true;
    applyMobileLifecycleStatus("resuming");
    try {
      let payload = null;
      try {
        payload = await fetchOptimizedPlaybackSessionStatus({
          statusUrl: activeSession.status_url,
          browserPlaybackSessionRoot,
          sessionId: activeSession.session_id,
        });
        const acceptedStatus = acceptBrowserPlaybackSessionPayload(payload, SESSION_SOURCE_STATUS);
        if (!acceptedStatus.accepted) {
          return;
        }
      } catch (requestError) {
        if (requestError?.status === 404) {
          handleMissingBrowserPlaybackSession(activeSession.session_id);
          return;
        }
        setSeekNotice("Rechecking playback session...");
        scheduleMobilePlaybackPoll(
          activeSession.session_id,
          Math.max(1000, Math.round((activeSession.status_poll_seconds || 1) * 1000)),
        );
        return;
      }
      if (payload.state === "failed" || payload.state === "expired" || payload.state === "stopped") {
        setPlaybackError(`This ${browserPlaybackLabel} session is no longer active.`);
        return;
      }
      const heartbeatLifecycle = backgroundDurationMs > BACKGROUND_PREPARATION_PARK_MS ? "resuming" : "attached";
      await postMobileRuntimeHeartbeat({
        lifecycleState: heartbeatLifecycle,
        stalled: false,
        playing: shouldResume,
        force: backgroundDurationMs > BACKGROUND_PREPARATION_PARK_MS,
      }).catch(() => {
        // Foreground return should keep the current element/source even if this diagnostic misses.
      });
      const hardReattachRequired = softResumeRequiresHardReattach({
        payload,
        attachedManifestUrl: attachedOptimizedManifestUrlRef.current,
        attachedIdentity: mobileAttachedEpochRef.current,
        streamSourceUrl: streamSource?.mode === "hls" ? streamSource.url : "",
      });
      if (hardReattachRequired) {
        const recoveryTarget = resolveLivePlaybackRecoveryTarget(payload);
        preservePlaybackRecoveryTarget(recoveryTarget);
        setOptimizedPlaybackPending(true);
        setSeekNotice(`Reattaching the current ${browserPlaybackLabel} session.`);
        if (isHlsAttachReady(payload)) {
          armMobileManifestAttachment(payload, {
            autoplay: shouldResume,
            targetPosition: recoveryTarget,
            forceReattach: true,
            preserveAuthority: true,
            resetSeekPreparation: true,
          });
        } else {
          scheduleMobilePlaybackPoll(
            payload.session_id,
            Math.max(1000, Math.round((payload.status_poll_seconds || 1) * 1000)),
          );
        }
        return;
      }
      syncMobilePlaybackState(payload);
      if (isRoute2SessionPayload(payload)) {
        mobileAttachedManifestEndRef.current = resolveAttachedManifestEndSeconds(payload);
        mobileAttachedManifestRevisionRef.current = String(payload.attach_revision || 0);
        mobileAttachedEpochRef.current = resolveSessionAttachmentIdentity(payload);
        scheduleMobilePlaybackPoll(
          payload.session_id,
          Math.max(1000, Math.round((payload.status_poll_seconds || 1) * 1000)),
        );
      }
      applyMobileLifecycleStatus("attached");
      clearOptimizedPlaybackPending();
      setPlaybackError("");
      setSeekNotice("");
      if (shouldResume && video?.paused && mobilePlayerCanPlayRef.current) {
        try {
          await video.play();
        } catch {
          setSeekNotice("Tap play to resume.");
        }
      }
    } catch (requestError) {
      setSeekNotice("Rechecking playback session...");
      scheduleMobilePlaybackPoll(
        activeSession.session_id,
        Math.max(1000, Math.round((activeSession.status_poll_seconds || 1) * 1000)),
      );
    } finally {
      mobileWasBackgroundedRef.current = false;
      mobileBackgroundHiddenAtRef.current = 0;
      mobileRecoveryInFlightRef.current = false;
    }
  }

  function maybeAttachHlsAuthority(payload, { autoplay = false } = {}) {
    if (!isHlsAttachReady(payload) || !hlsAttachmentNeedsReattach(payload)) {
      return false;
    }
    if (isFailedAudioSwitchAttachPayload(payload)) {
      debugAudioSwitchAttach("audio_switch_failed_revision_reattach_blocked", {
        expectedAttachRevision: audioSwitchAttachRef.current?.expectedAttachRevision,
        expectedActiveEpochId: audioSwitchAttachRef.current?.expectedActiveEpochId,
        targetAudioStreamIndex: audioSwitchAttachRef.current?.targetAudioStreamIndex,
        phase: "failed",
        restoredPrevious: audioSwitchAttachRef.current?.restoredPrevious,
      });
      return false;
    }
    const reattachingExistingManifest = Boolean(attachedOptimizedManifestUrlRef.current);
    const explicitRetargetPending = Boolean(
      mobileSeekPendingRef.current
      || payload.pending_target_seconds != null
    );
    armMobileManifestAttachment(payload, {
      autoplay,
      targetPosition: reattachingExistingManifest && !explicitRetargetPending
        ? resolveLivePlaybackRecoveryTarget(payload)
        : resolveHlsAttachPosition(payload),
      preserveAuthority: true,
      resetSeekPreparation: true,
      forceReattach: reattachingExistingManifest,
    });
    return true;
  }

  function maybeAttachRoute2Authority(payload, { autoplay = false } = {}) {
    return maybeAttachHlsAuthority(payload, { autoplay });
  }

  function completeHlsLocalTargetTransition(payload, targetPosition) {
    const video = videoRef.current;
    const nextCommittedPosition = Math.max(
      targetPosition != null ? targetPosition : resolveHlsAttachPosition(payload),
      0,
    );
    committedPlayheadSecondsRef.current = nextCommittedPosition;
    actualMediaElementTimeRef.current = nextCommittedPosition;
    mobileLastStablePositionRef.current = nextCommittedPosition;
    pendingSeekPhaseRef.current = "idle";
    mobileSeekPendingRef.current = false;
    mobilePendingTargetRef.current = null;
    requestedTargetSecondsRef.current = nextCommittedPosition;
    mobileAwaitingTargetSeekRef.current = false;
    if (video) {
      try {
        video.currentTime = resolveSessionMediaElementTime(payload, nextCommittedPosition);
      } catch {
        // Keep the current element time if Safari refuses the target reposition.
      }
    }
    setActualMediaElementTime(nextCommittedPosition);
    setPlaybackPosition(nextCommittedPosition);
    if (iosMobile && mobileRetargetTransitionRef.current) {
      pendingSeekPhaseRef.current = "target_attached_waiting_client_buffer";
      setPendingSeekPhase("target_attached_waiting_client_buffer");
      mobileSeekPendingRef.current = false;
      mobilePendingTargetRef.current = nextCommittedPosition;
      requestedTargetSecondsRef.current = nextCommittedPosition;
      mobilePlayerCanPlayRef.current = false;
      setMobilePlayerCanPlay(false);
      setPlaybackError("");
      setSeekNotice(`Preparing ${formatDuration(nextCommittedPosition)}...`);
      setPlaybackStatus("Preparing target playback");
      setMobileLifecycleStateValue("attached");
      maybeAcknowledgeHlsAttachment({
        playing: false,
        force: true,
      });
      return;
    }
    clearOptimizedPlaybackPending();
    setMobileFrozenFrameUrl("");
    mobileRetargetTransitionRef.current = false;
    setPlaybackError("");
    setSeekNotice("");
    setPlaybackStatus(browserStreamLabelTitle);
    setMobileLifecycleStateValue("attached");
    maybeAcknowledgeHlsAttachment({
      playing: video ? !video.paused : false,
      force: true,
    });
  }

  function completeRoute2LocalTargetTransition(payload, targetPosition) {
    completeHlsLocalTargetTransition(payload, targetPosition);
  }

  function finalizeRetargetVisibility(video, { resumePlayback, committedPosition }) {
    const nextCommittedPosition = Math.max(
      committedPosition ?? resolveSessionAbsoluteTime(mobileSessionRef.current, video.currentTime || 0),
      0,
    );
    committedPlayheadSecondsRef.current = nextCommittedPosition;
    mobileLastStablePositionRef.current = nextCommittedPosition;
    setCommittedPlayheadSeconds(nextCommittedPosition);
    pendingSeekPhaseRef.current = "idle";
    setPendingSeekPhase("idle");
    requestedTargetSecondsRef.current = nextCommittedPosition;
    setRequestedTargetSeconds(nextCommittedPosition);
    mobilePlayerCanPlayRef.current = true;
    setMobilePlayerCanPlay(true);
    mobileRetargetTransitionRef.current = false;
    mobileSeekPendingRef.current = false;
    mobilePendingTargetRef.current = null;
    setPlaybackError("");
    setSeekNotice("");
    setPlaybackStatus(browserReadyLabelTitle);
    setMobileLifecycleStateValue("attached");
    setMobileFrozenFrameUrl("");
    clearOptimizedPlaybackPending();
    mobileAutoplayPendingRef.current = false;
    mobileResumeAfterReadyRef.current = false;
    if (resumePlayback) {
      video.play().catch((requestError) => {
        const normalized = (requestError?.message || "").toLowerCase();
        if (
          normalized.includes("gesture")
          || normalized.includes("notallowed")
          || normalized.includes("denied")
          || normalized.includes("not allowed")
        ) {
          setPlaybackError("");
          setSeekNotice(`Tap play in the video controls to continue ${browserPlaybackLabel}.`);
          return;
        }
        setPlaybackError(requestError.message || `Failed to continue ${browserPlaybackLabel}`);
      });
    }
  }

  function completeMobileTargetTransition(payload) {
    syncMobilePlaybackState(payload);
    const targetPosition = Math.max(
      mobilePendingTargetRef.current != null
        ? mobilePendingTargetRef.current
        : payload.pending_target_seconds != null
          ? payload.pending_target_seconds
          : payload.target_position_seconds || 0,
      0,
    );
    pendingSeekPhaseRef.current = "committing";
    setPendingSeekPhase("committing");
    if (mobilePlayerCanPlayRef.current && videoRef.current) {
      const frozenFrameUrl = captureVideoFrameSnapshot(videoRef.current);
      if (frozenFrameUrl) {
        setMobileFrozenFrameUrl(frozenFrameUrl);
        mobileRetargetTransitionRef.current = true;
      } else {
        mobileRetargetTransitionRef.current = false;
      }
    } else {
      mobileRetargetTransitionRef.current = false;
    }
    mobileCanPlaySeenRef.current = false;
    mobileLoadedDataSeenRef.current = false;
    mobileAwaitingTargetSeekRef.current = resolveSessionMediaElementTime(payload, targetPosition) > 0.5;
    mobileFrameReadyRef.current = false;
    mobileFrameProbePendingRef.current = false;
    mobileReadinessGenerationRef.current += 1;
    mobilePlayerCanPlayRef.current = false;
    mobileWarmupProbeActiveRef.current = false;
    mobileWarmupPlaybackObservedRef.current = false;
    mobileWarmupStartPositionRef.current = 0;
    setMobilePlayerCanPlay(false);
    setPlaybackError("");
    setPlaybackStatus("Preparing target playback");
    setSeekNotice(`Preparing ${formatDuration(targetPosition)}...`);
    setOptimizedPlaybackPending(true);
    setPendingSeekPhase("committing");
    const video = videoRef.current;
    if (!video) {
      return;
    }
    if (
      isRoute2SessionPayload(payload)
      && !route2AttachmentNeedsReattach(payload)
      && payload.pending_target_seconds == null
    ) {
      completeRoute2LocalTargetTransition(payload, targetPosition);
      return;
    }
    if (attachedOptimizedManifestUrlRef.current !== (streamSource?.url || "")) {
      armMobileManifestAttachment(payload, {
        autoplay: mobileResumeAfterReadyRef.current,
        targetPosition,
        preserveAuthority: true,
        resetSeekPreparation: true,
        forceReattach: true,
      });
      return;
    }
    mobileAwaitingTargetSeekRef.current = resolveSessionMediaElementTime(payload, targetPosition) > 0.5;
    actualMediaElementTimeRef.current = targetPosition;
    setActualMediaElementTime(targetPosition);
    setPlaybackPosition(targetPosition);
    try {
      video.currentTime = resolveSessionMediaElementTime(payload, targetPosition);
    } catch {
      // Safari can reject currentTime jumps until the media element settles.
    }
  }

  function resolveMobileCommittedPosition(payload = mobileSessionRef.current) {
    if (typeof payload?.committed_playhead_seconds === "number") {
      return Math.max(payload.committed_playhead_seconds, 0);
    }
    return Math.max(committedPlayheadSecondsRef.current || 0, 0);
  }

  function buildClientPlaybackTelemetry({ stallReason = null } = {}) {
    const activeSession = mobileSessionRef.current;
    if (!activeSession?.session_id) {
      return {};
    }
    const video = videoRef.current;
    const targets = deriveBufferTargetsFromSession(activeSession, browserPlaybackDeviceClass);
    const previousSample = clientPlaybackLivenessSampleRef.current;
    const livenessSample = readClientPlaybackLiveness(video, previousSample);
    clientPlaybackLivenessSampleRef.current = livenessSample;
    const selectedEngine =
      hlsEngineDiagnostics?.selectedEngine
      || (hlsRef?.current ? "hls.js" : "native_hls");
    const payload = {
      selected_hls_engine: selectedEngine,
      buffer_tier: targets.bufferTier,
      client_buffered_ahead_seconds: livenessSample.bufferedAheadSeconds,
      client_target_forward_buffer_seconds: targets.forwardBufferSeconds,
      client_back_buffer_seconds: targets.backBufferSeconds,
      client_max_buffer_size_bytes: targets.maxBufferSizeBytes,
      client_ready_state: livenessSample.readyState,
      client_network_state: livenessSample.networkState,
      client_current_time_seconds: livenessSample.currentTimeSeconds,
      client_time_advancing: livenessSample.timeAdvancing,
      client_playback_stall_reason: stallReason || "",
    };
    if (hlsRef?.current?.config) {
      payload.hls_js_config = compactHlsBufferConfig(hlsRef.current.config);
    }
    return payload;
  }

  function resolveMobileAuthorityPosition(payload = mobileSessionRef.current) {
    if (typeof payload?.pending_target_seconds === "number") {
      return Math.max(payload.pending_target_seconds, 0);
    }
    if (typeof payload?.target_position_seconds === "number") {
      return Math.max(payload.target_position_seconds, 0);
    }
    return Math.max(resolveMobileCommittedPosition(payload), 0);
  }

  function resolveLivePlaybackRecoveryTarget(payload = mobileSessionRef.current) {
    const video = videoRef.current;
    const currentAbsolutePositionSeconds = video && payload
      ? resolveSessionAbsoluteTime(payload, Math.max(video.currentTime || 0, 0))
      : null;
    return resolvePlaybackRecoveryTargetSeconds({
      currentAbsolutePositionSeconds,
      committedPlayheadSeconds: committedPlayheadSecondsRef.current,
      actualMediaElementTimeSeconds: actualMediaElementTimeRef.current,
      targetPositionSeconds: resolveMobileAuthorityPosition(payload),
    });
  }

  function resolveAutomaticPlaybackRecoveryTargetDecision(
    payload = mobileSessionRef.current,
    recoveryReason = "",
  ) {
    const video = videoRef.current;
    const currentAbsolutePositionSeconds = video && payload
      ? resolveSessionAbsoluteTime(payload, Math.max(video.currentTime || 0, 0))
      : null;
    return resolveAutomaticPlaybackRecoveryTarget({
      currentAbsolutePositionSeconds,
      lastStablePositionSeconds: mobileLastStablePositionRef.current,
      committedPlayheadSeconds: committedPlayheadSecondsRef.current,
      actualMediaElementTimeSeconds: actualMediaElementTimeRef.current,
      targetPositionSeconds: resolveMobileAuthorityPosition(payload),
      recoveryReason,
    });
  }

  function preservePlaybackRecoveryTarget(targetPosition) {
    const safeTarget = Math.max(Number(targetPosition || 0), 0);
    committedPlayheadSecondsRef.current = safeTarget;
    actualMediaElementTimeRef.current = safeTarget;
    mobileLastStablePositionRef.current = safeTarget;
    requestedTargetSecondsRef.current = safeTarget;
    setCommittedPlayheadSeconds(safeTarget);
    setActualMediaElementTime(safeTarget);
    setRequestedTargetSeconds(safeTarget);
    setPlaybackPosition(safeTarget);
  }

  async function postMobileRuntimeHeartbeat({
    lifecycleState = null,
    stalled = null,
    playing = null,
    clientAttachRevision = null,
    clientProbeBytes = null,
    clientProbeDurationMs = null,
    force = false,
    useBeacon = false,
    clientPlaybackStallReason = null,
  } = {}) {
    const activeSession = mobileSessionRef.current;
    if (!activeSession?.session_id) {
      return null;
    }
    const payload = {
      committed_playhead_seconds: resolveMobileCommittedPosition(activeSession),
      actual_media_element_time_seconds: actualMediaElementTimeRef.current || 0,
      ...buildClientPlaybackTelemetry({
        stallReason: clientPlaybackStallReason || (stalled ? "client_reported_stall" : null),
      }),
    };
    if (isRoute2SessionPayload(activeSession)) {
      const nextAttachRevision =
        clientAttachRevision != null
          ? Math.max(0, Number(clientAttachRevision || 0))
          : resolveRoute2HeartbeatAttachRevision(activeSession);
      if (nextAttachRevision > 0) {
        payload.client_attach_revision = nextAttachRevision;
      }
      if (
        clientProbeBytes != null
        && clientProbeDurationMs != null
        && clientProbeBytes > 0
        && clientProbeDurationMs > 0
      ) {
        payload.client_probe_bytes = Math.round(clientProbeBytes);
        payload.client_probe_duration_ms = Math.round(clientProbeDurationMs);
      }
    }
    if (lifecycleState) {
      payload.lifecycle_state = lifecycleState;
    }
    if (stalled != null) {
      payload.stalled = stalled;
    }
    if (playing != null) {
      payload.playing = playing;
    }
    if (useBeacon && navigator.sendBeacon) {
      const heartbeatUrl =
        activeSession.heartbeat_url || `${browserPlaybackSessionRoot}/sessions/${activeSession.session_id}/heartbeat`;
      navigator.sendBeacon(
        heartbeatUrl,
        new Blob([JSON.stringify(payload)], { type: "application/json" }),
      );
      return null;
    }
    const now = Date.now();
    if (!force && mobileHeartbeatInFlightRef.current) {
      return null;
    }
    if (!force && now - mobileLastHeartbeatAtRef.current < 2500) {
      return null;
    }
    mobileHeartbeatInFlightRef.current = true;
    mobileLastHeartbeatAtRef.current = now;
    try {
      const response = await postOptimizedPlaybackHeartbeat({
        heartbeatUrl: activeSession.heartbeat_url,
        browserPlaybackSessionRoot,
        sessionId: activeSession.session_id,
        data: payload,
      });
      const acceptedResponse = acceptBrowserPlaybackSessionPayload(response, SESSION_SOURCE_STATUS);
      if (!acceptedResponse.accepted) {
        return null;
      }
      if (isRoute2SessionPayload(response)) {
        if (maybeStartRoute2SupplyRecovery(response)) {
          return response;
        }
        maybeAttachRoute2Authority(response, {
          autoplay:
            mobileAutoplayPendingRef.current
            || mobileResumeAfterReadyRef.current
            || !videoRef.current?.paused,
        });
      }
      maybeRefreshAttachedMobileManifest(response);
      return response;
    } finally {
      mobileHeartbeatInFlightRef.current = false;
    }
  }

  function maybeAcknowledgeHlsAttachment({ playing = null, force = false, loadedEventName = "" } = {}) {
    const activeSession = mobileSessionRef.current;
    if (!isHlsAttachReady(activeSession)) {
      return;
    }
    const serverAttachRevision = Number(activeSession.attach_revision || 0);
    if (isFailedAudioSwitchAttachForRevision(serverAttachRevision)) {
      if (maybeInferAudioSwitchAttachSuccess({ payload: activeSession, playing, sendHeartbeat: true })) {
        return;
      }
      if (AUDIO_SWITCH_ATTACH_LOAD_EVENTS.has(String(loadedEventName || ""))) {
        const pendingAudioAttach = audioSwitchAttachRef.current;
        debugAudioSwitchAttach("audio_switch_loaded_late_ignored", {
          loadedEventName,
          expectedAttachRevision: serverAttachRevision,
          expectedActiveEpochId: pendingAudioAttach?.expectedActiveEpochId || activeSession?.active_epoch_id || null,
          targetAudioStreamIndex: pendingAudioAttach?.targetAudioStreamIndex ?? activeSession?.active_audio_stream_index ?? null,
          phase: "failed",
          retryCount: pendingAudioAttach?.retryCount || 0,
          clientAttachRevision: mobileClientAttachRevisionRef.current || 0,
          currentAttachRevision: serverAttachRevision,
          sourceSetAtMs: pendingAudioAttach?.sourceSetAtMs || 0,
          loadedAtMs: pendingAudioAttach?.loadedAtMs || 0,
        });
      }
      return;
    }
    if (isPendingAudioSwitchAttachForRevision(serverAttachRevision)) {
      if (AUDIO_SWITCH_ATTACH_LOAD_EVENTS.has(String(loadedEventName || ""))) {
        markAudioSwitchAttachLoaded({ eventName: loadedEventName });
      } else {
        markAudioSwitchPostSourceEvidence({ eventName: loadedEventName, playing });
      }
      if (maybeInferAudioSwitchAttachSuccess({ payload: activeSession, playing, sendHeartbeat: true })) {
        return;
      }
      if (!isAudioSwitchAttachLoadedForRevision(serverAttachRevision)) {
        mobilePendingAttachRevisionRef.current = Math.max(
          mobilePendingAttachRevisionRef.current,
          serverAttachRevision,
        );
        return;
      }
    }
    const confirmedAttachRevision = Math.max(
      mobileClientAttachRevisionRef.current || 0,
      Number(activeSession.client_attach_revision || 0),
    );
    if (serverAttachRevision <= 0 || confirmedAttachRevision >= serverAttachRevision) {
      mobilePendingAttachRevisionRef.current = 0;
      return;
    }
    mobilePendingAttachRevisionRef.current = Math.max(
      mobilePendingAttachRevisionRef.current,
      serverAttachRevision,
    );
    postMobileRuntimeHeartbeat({
      lifecycleState: "attached",
      stalled: false,
      playing: playing != null ? playing : !videoRef.current?.paused,
      clientAttachRevision: serverAttachRevision,
      force,
    }).catch(() => {
      // Subsequent heartbeats will continue carrying the pending attach revision.
    });
    const pendingAudioAttach = audioSwitchAttachRef.current;
    if (
      pendingAudioAttach
      && pendingAudioAttach.expectedAttachRevision === serverAttachRevision
      && pendingAudioAttach.phase === "loaded"
    ) {
      debugAudioSwitchAttach("audio_switch_ack_sent", {
        expectedAttachRevision: serverAttachRevision,
        expectedActiveEpochId: pendingAudioAttach.expectedActiveEpochId,
      });
    }
  }

  function maybeAcknowledgeRoute2Attachment({ playing = null, force = false, loadedEventName = "" } = {}) {
    maybeAcknowledgeHlsAttachment({ playing, force, loadedEventName });
  }

  function maybeStartHlsSupplyRecovery(payload) {
    if (!isHlsSessionPayload(payload) || !payload?.stalled_recovery_needed) {
      return false;
    }
    const video = videoRef.current;
    if (
      !video
      || mobileSeekPendingRef.current
      || mobileRecoveryInFlightRef.current
      || mobileLifecycleStateRef.current !== "attached"
      || !mobilePlayerCanPlayRef.current
      || video.paused
    ) {
      return false;
    }
    const livenessSample = readClientPlaybackLiveness(video, clientPlaybackLivenessSampleRef.current);
    clientPlaybackLivenessSampleRef.current = livenessSample;
    const recoveryDecision = shouldStartVisibleHlsSupplyRecovery({
      session: payload,
      livenessSample,
      seekPending: mobileSeekPendingRef.current,
      recoveryInFlight: mobileRecoveryInFlightRef.current,
      lifecycleState: mobileLifecycleStateRef.current,
      mobilePlayerCanPlay: mobilePlayerCanPlayRef.current,
      videoPaused: video.paused,
      hlsJsAttached: Boolean(hlsRef?.current),
    });
    if (!recoveryDecision.start) {
      return false;
    }
    setOptimizedPlaybackPending(true);
    setPlaybackError("");
    setSeekNotice(`Rebuffering ${browserPlaybackLabel} while Elvern rebuilds safe runway.`);
    applyMobileLifecycleStatus("recovering");
    postMobileRuntimeHeartbeat({
      lifecycleState: "recovering",
      stalled: true,
      playing: true,
      force: true,
    }).catch(() => {
      // Route 2 recovery can continue locally if this control heartbeat misses.
    });
    recoverMobilePlaybackAfterResume("route2-low-water").catch((requestError) => {
      clearOptimizedPlaybackPending();
      setPlaybackError(requestError.message || `Failed to stabilize ${browserPlaybackLabel}`);
    });
    return true;
  }

  function maybeStartRoute2SupplyRecovery(payload) {
    return maybeStartHlsSupplyRecovery(payload);
  }

  async function recoverMobilePlaybackAfterResume(trigger) {
    const activeSession = mobileSessionRef.current;
    const explicitRoute2Session = isRoute2SessionPayload(activeSession);
    if ((!iosMobile && !explicitRoute2Session) || !activeSession?.session_id || mobileRecoveryInFlightRef.current) {
      return;
    }
    const backgroundResumeTrigger = Boolean(
      mobileWasBackgroundedRef.current
      && (trigger === "visibilitychange" || trigger === "pageshow" || trigger === "focus"),
    );
    const hiddenAt = mobileBackgroundHiddenAtRef.current || 0;
    const backgroundDurationMs = backgroundResumeTrigger && hiddenAt > 0
      ? Math.max(Date.now() - hiddenAt, 0)
      : 0;
    const capturedRecoveryTarget = backgroundResumeTrigger
      ? resolveLivePlaybackRecoveryTarget(activeSession)
      : resolveAutomaticPlaybackRecoveryTargetDecision(activeSession, trigger).targetAfterBackrollSeconds;
    mobileRecoveryInFlightRef.current = true;
    applyMobileLifecycleStatus("resuming");
    if (!backgroundResumeTrigger) {
      setOptimizedPlaybackPending(true);
      setSeekNotice(`Reconnecting the current ${browserPlaybackLabel} session.`);
    }
    const video = videoRef.current;
    const shouldResume =
      mobileWasPlayingBeforeSuspendRef.current || (!video?.paused && mobilePlayerCanPlayRef.current);
    try {
      let payload = null;
      try {
        payload = await fetchOptimizedPlaybackSessionStatus({
          statusUrl: activeSession.status_url,
          browserPlaybackSessionRoot,
          sessionId: activeSession.session_id,
        });
        const acceptedStatus = acceptBrowserPlaybackSessionPayload(payload, SESSION_SOURCE_STATUS);
        if (!acceptedStatus.accepted) {
          return;
        }
      } catch (requestError) {
        if (requestError?.status === 404) {
          handleMissingBrowserPlaybackSession(activeSession.session_id);
          return;
        }
        const recoveryTarget = capturedRecoveryTarget;
        const recoveryAttempt =
          browserPlaybackLatestAttemptRef.current
          || buildSyntheticBrowserPlaybackAttempt(browserPlaybackCurrentSessionRef.current, activeSession);
        payload = await createOptimizedPlaybackSession({
          browserPlaybackSessionRoot,
          itemId,
          profile: activeSession.profile || "mobile_1080p",
          startPositionSeconds: recoveryTarget,
          playbackMode: getPlaybackMode(activeSession.playback_mode || playbackModeIntentRef.current),
          engineMode: explicitRoute2Session ? "route2" : undefined,
          clientDeviceClass: browserPlaybackDeviceClass,
        });
        const acceptedRecoveryPayload = acceptBrowserPlaybackSessionPayload(
          payload,
          SESSION_SOURCE_RECOVERY_CREATE,
          { responseAttempt: recoveryAttempt },
        );
        if (!acceptedRecoveryPayload.accepted) {
          releasePlaybackSession(
            payload.stop_url,
            `${browserPlaybackSessionRoot}/sessions/${payload.session_id}/stop`,
          );
          return;
        }
      }
      if (payload.state === "failed" || payload.state === "expired" || payload.state === "stopped") {
        const recoveryTarget = backgroundResumeTrigger
          ? resolvePlaybackRecoveryTargetSeconds({
            currentAbsolutePositionSeconds: capturedRecoveryTarget,
            committedPlayheadSeconds: committedPlayheadSecondsRef.current,
            actualMediaElementTimeSeconds: actualMediaElementTimeRef.current,
            targetPositionSeconds: resolveMobileAuthorityPosition(payload),
          })
          : resolveAutomaticPlaybackRecoveryTargetDecision(payload, trigger).targetAfterBackrollSeconds;
        const recoveryAttempt =
          browserPlaybackLatestAttemptRef.current
          || buildSyntheticBrowserPlaybackAttempt(browserPlaybackCurrentSessionRef.current, payload);
        payload = await createOptimizedPlaybackSession({
          browserPlaybackSessionRoot,
          itemId,
          profile: payload.profile || activeSession.profile || "mobile_1080p",
          startPositionSeconds: recoveryTarget,
          playbackMode: getPlaybackMode(
            payload.playback_mode
            || activeSession.playback_mode
            || playbackModeIntentRef.current,
          ),
          engineMode: (isRoute2SessionPayload(payload) || explicitRoute2Session) ? "route2" : undefined,
          clientDeviceClass: browserPlaybackDeviceClass,
        });
        const acceptedRecoveryPayload = acceptBrowserPlaybackSessionPayload(
          payload,
          SESSION_SOURCE_RECOVERY_CREATE,
          { responseAttempt: recoveryAttempt },
        );
        if (!acceptedRecoveryPayload.accepted) {
          releasePlaybackSession(
            payload.stop_url,
            `${browserPlaybackSessionRoot}/sessions/${payload.session_id}/stop`,
          );
          return;
        }
      }
      if (backgroundResumeTrigger) {
        const hardReattachRequired = Boolean(
          isHlsAttachReady(payload)
          && hlsAttachmentNeedsReattach(payload)
        );
        await postMobileRuntimeHeartbeat({
          lifecycleState: "resuming",
          stalled: false,
          playing: shouldResume,
          force: backgroundDurationMs > BACKGROUND_PREPARATION_PARK_MS,
        }).catch(() => {
          // A missed foreground heartbeat must not turn a soft resume into a destructive reattach.
        });
        if (!hardReattachRequired) {
          syncMobilePlaybackState(payload);
          if (isRoute2SessionPayload(payload)) {
            mobileAttachedManifestEndRef.current = resolveAttachedManifestEndSeconds(payload);
            mobileAttachedManifestRevisionRef.current = String(payload.attach_revision || 0);
            mobileAttachedEpochRef.current = resolveSessionAttachmentIdentity(payload);
            scheduleMobilePlaybackPoll(
              payload.session_id,
              Math.max(1000, Math.round((payload.status_poll_seconds || 1) * 1000)),
            );
          }
          applyMobileLifecycleStatus("attached");
          clearOptimizedPlaybackPending();
          setPlaybackError("");
          setSeekNotice("");
          if (shouldResume && video?.paused && mobilePlayerCanPlayRef.current) {
            try {
              await video.play();
            } catch {
              setSeekNotice("Tap play to resume.");
            }
          }
          return;
        }
        setOptimizedPlaybackPending(true);
      }
      const recoveryTarget = backgroundResumeTrigger
        ? resolvePlaybackRecoveryTargetSeconds({
          currentAbsolutePositionSeconds: capturedRecoveryTarget,
          committedPlayheadSeconds: committedPlayheadSecondsRef.current,
          actualMediaElementTimeSeconds: actualMediaElementTimeRef.current,
          targetPositionSeconds: resolveMobileAuthorityPosition(payload),
        })
        : resolveAutomaticPlaybackRecoveryTargetDecision(payload, trigger).targetAfterBackrollSeconds;
      preservePlaybackRecoveryTarget(recoveryTarget);
      if (video && mobilePlayerCanPlayRef.current) {
        const frozenFrameUrl = captureVideoFrameSnapshot(video);
        if (frozenFrameUrl) {
          setMobileFrozenFrameUrl(frozenFrameUrl);
        }
      }
      mobilePlayerCanPlayRef.current = false;
      setMobilePlayerCanPlay(false);
      applyMobileLifecycleStatus("recovering");
      if (isRoute2SessionPayload(payload)) {
        if (isRoute2AttachReady(payload)) {
          armMobileManifestAttachment(payload, {
            autoplay: shouldResume,
            targetPosition: recoveryTarget,
            forceReattach: true,
            preserveAuthority: true,
            resetSeekPreparation: true,
          });
        } else {
          scheduleMobilePlaybackPoll(
            payload.session_id,
            Math.max(1000, Math.round((payload.status_poll_seconds || 1) * 1000)),
          );
        }
      } else {
        armMobileManifestAttachment(payload, {
          autoplay: shouldResume,
          targetPosition: recoveryTarget,
          forceReattach: true,
          preserveAuthority: true,
        });
        scheduleMobilePlaybackPoll(
          payload.session_id,
          Math.max(1000, Math.round((payload.status_poll_seconds || 1) * 1000)),
        );
      }
      setPlaybackError("");
      setSeekNotice(`Reattaching the current ${browserPlaybackLabel} session.`);
      if (trigger === "stalled") {
        await postMobileRuntimeHeartbeat({
          lifecycleState: "recovering",
          stalled: true,
          playing: shouldResume,
          force: true,
        }).catch(() => {
          // Recovery continues locally even if the runtime heartbeat misses.
        });
      }
    } catch (requestError) {
      applyMobileLifecycleStatus("fatal");
      clearOptimizedPlaybackPending();
      setPlaybackError(requestError.message || `Failed to recover ${browserPlaybackLabelTitle}`);
    } finally {
      mobileWasBackgroundedRef.current = false;
      mobileBackgroundHiddenAtRef.current = 0;
      mobileRecoveryInFlightRef.current = false;
    }
  }

  function scheduleMobilePlaybackPoll(sessionId, delayMs = 1000, pollToken = mobilePollTokenRef.current) {
    window.clearTimeout(mobilePollRef.current);
    mobilePollRef.current = window.setTimeout(async () => {
      try {
        const payload = await fetchOptimizedPlaybackSessionStatus({
          statusUrl: mobileSessionRef.current?.status_url,
          browserPlaybackSessionRoot,
          sessionId,
        });
        if (
          pollToken !== mobilePollTokenRef.current
          || currentItemIdRef.current !== itemId
        ) {
          return;
        }
        const acceptedPayload = acceptBrowserPlaybackSessionPayload(payload, SESSION_SOURCE_STATUS);
        if (!acceptedPayload.accepted) {
          return;
        }
        if (isRoute2SessionPayload(payload)) {
          if (maybeStartRoute2SupplyRecovery(payload)) {
            scheduleMobilePlaybackPoll(
              sessionId,
              Math.max(1000, Math.round((payload.status_poll_seconds || 1) * 1000)),
              pollToken,
            );
            return;
          }
          maybeAttachRoute2Authority(payload, {
            autoplay: mobileAutoplayPendingRef.current || mobileResumeAfterReadyRef.current,
          });
        } else if (!attachedOptimizedManifestUrlRef.current && payload.playback_commit_ready) {
          armMobileManifestAttachment(payload, {
            autoplay: mobileAutoplayPendingRef.current || mobileResumeAfterReadyRef.current,
            targetPosition:
              mobilePendingTargetRef.current != null
                ? mobilePendingTargetRef.current
                : payload.target_position_seconds,
            resetSeekPreparation: true,
          });
        } else if (maybeRefreshAttachedMobileManifest(payload)) {
          // The attached VOD slice changed; reattach before the current one runs dry.
        } else if (mobileSeekPendingRef.current && payload.playback_commit_ready) {
          completeMobileTargetTransition(payload);
        }
        if (payload.state === "failed" || payload.state === "stopped" || payload.state === "expired") {
          stopMobilePlaybackPolling();
          return;
        }
        scheduleMobilePlaybackPoll(
          sessionId,
          Math.max(1000, Math.round((payload.status_poll_seconds || 1) * 1000)),
          pollToken,
        );
      } catch (requestError) {
        if (pollToken !== mobilePollTokenRef.current) {
          return;
        }
        if (requestError?.status === 404) {
          handleMissingBrowserPlaybackSession(sessionId);
          return;
        }
        if (isRoute2SessionPayload(mobileSessionRef.current)) {
          recoverMobilePlaybackAfterResume("poll-error").catch((recoveryError) => {
            stopMobilePlaybackPolling();
            clearOptimizedPlaybackPending();
            setPlaybackError(recoveryError.message || requestError.message || `Failed to refresh ${browserPlaybackLabel}`);
          });
          return;
        }
        stopMobilePlaybackPolling();
        clearOptimizedPlaybackPending();
        setPlaybackError(requestError.message || `Failed to refresh ${browserPlaybackLabel}`);
      }
    }, delayMs);
  }

  async function ensureMobileSessionReady(
    payload,
    { autoplay = false, targetPosition = null } = {},
    { source = SESSION_SOURCE_STATUS, responseAttempt = null } = {},
  ) {
    const acceptedPayload = acceptBrowserPlaybackSessionPayload(payload, source, { responseAttempt });
    if (!acceptedPayload.accepted) {
      return false;
    }
    if (payload.last_error && payload.state === "failed") {
      return true;
    }
    if (isRoute2SessionPayload(payload)) {
      if (isRoute2AttachReady(payload)) {
        armMobileManifestAttachment(payload, {
          autoplay,
          targetPosition: targetPosition != null ? targetPosition : resolveRoute2AttachPosition(payload),
          resetSeekPreparation: true,
        });
        return true;
      }
      mobileAutoplayPendingRef.current = autoplay;
      mobilePendingTargetRef.current =
        targetPosition != null ? targetPosition : resolveRoute2AttachPosition(payload);
      scheduleMobilePlaybackPoll(
        payload.session_id,
        Math.max(1000, Math.round((payload.status_poll_seconds || 1) * 1000)),
      );
      return true;
    }
    if (payload.playback_commit_ready) {
      armMobileManifestAttachment(payload, {
        autoplay,
        targetPosition,
        resetSeekPreparation: true,
      });
      return true;
    }
    mobileAutoplayPendingRef.current = autoplay;
    if (targetPosition != null) {
      mobilePendingTargetRef.current = targetPosition;
    } else if (mobilePendingTargetRef.current == null) {
      mobilePendingTargetRef.current = payload.target_position_seconds;
    }
    scheduleMobilePlaybackPoll(
      payload.session_id,
      Math.max(1000, Math.round((payload.status_poll_seconds || 1) * 1000)),
    );
    return true;
  }

  async function startMobileOptimizedPlayback({ autoplay = true, playbackMode = "lite" } = {}) {
    const flowGeneration = playbackFlowRef.current;
    stopMobilePlaybackPolling();
    browserPlaybackCurrentSessionRef.current = null;
    const targetPosition = Math.max(
      0,
      requestedTargetSecondsRef.current != null
        ? requestedTargetSecondsRef.current
        : browserStartPositionRef.current || 0,
    );
    const explicitAttempt = createBrowserPlaybackAttempt({
      attemptId: browserPlaybackAttemptCounterRef.current + 1,
      itemId,
      playbackMode,
      startPositionSeconds: targetPosition,
      profile: browserPlaybackProfile,
      engineMode: "route2",
    });
    browserPlaybackAttemptCounterRef.current = explicitAttempt.attemptId;
    browserPlaybackLatestAttemptRef.current = explicitAttempt;
    const payload = await createOptimizedPlaybackSession({
      browserPlaybackSessionRoot,
      itemId,
      profile: browserPlaybackProfile,
      startPositionSeconds: targetPosition,
      playbackMode: explicitAttempt.playbackMode,
      clientDeviceClass: browserPlaybackDeviceClass,
    });
    if (flowGeneration !== playbackFlowRef.current || currentItemIdRef.current !== itemId) {
      releasePlaybackSession(
        payload.stop_url,
        `${browserPlaybackSessionRoot}/sessions/${payload.session_id}/stop`,
      );
      return null;
    }
    const accepted = await ensureMobileSessionReady(payload, {
      autoplay,
      targetPosition,
    }, {
      source: SESSION_SOURCE_EXPLICIT_CREATE,
      responseAttempt: explicitAttempt,
    });
    if (!accepted) {
      releasePlaybackSession(
        payload.stop_url,
        `${browserPlaybackSessionRoot}/sessions/${payload.session_id}/stop`,
      );
      return null;
    }
    return true;
  }

  async function retargetMobileOptimizedPlayback(targetPosition, { resumeAfterReady = true } = {}) {
    const activeSession = mobileSessionRef.current;
    if (!activeSession?.session_id) {
      return;
    }
    stopMobilePlaybackPolling();
    mobileResumeAfterReadyRef.current = resumeAfterReady;
    mobileSeekPendingRef.current = true;
    mobilePendingTargetRef.current = targetPosition;
    requestedTargetSecondsRef.current = targetPosition;
    setRequestedTargetSeconds(targetPosition);
    pendingSeekPhaseRef.current = "preparing";
    setPendingSeekPhase("preparing");
    setSeekNotice(`Preparing ${formatDuration(targetPosition)}...`);
    setPlaybackStatus("Preparing target playback");
    setOptimizedPlaybackPending(true);
    setPlaybackPosition(targetPosition);
    const video = videoRef.current;
    const stablePosition =
      resolveMobileCommittedPosition(activeSession)
      || mobileLastStablePositionRef.current
      || actualMediaElementTimeRef.current
      || resolveSessionAbsoluteTime(activeSession, video?.currentTime || 0);
    if (video && mobilePlayerCanPlayRef.current) {
      const frozenFrameUrl = captureVideoFrameSnapshot(video);
      setMobileFrozenFrameUrl(frozenFrameUrl);
      mobileRetargetTransitionRef.current = Boolean(frozenFrameUrl);
    } else {
      setMobileFrozenFrameUrl("");
    }
    mobilePlayerCanPlayRef.current = false;
    setMobilePlayerCanPlay(false);
    if (video) {
      video.pause();
      mobileAwaitingTargetSeekRef.current = false;
      const stableMediaElementTime = resolveSessionMediaElementTime(activeSession, stablePosition);
      if (Math.abs((video.currentTime || 0) - stableMediaElementTime) > 0.25) {
        try {
          video.currentTime = stableMediaElementTime;
        } catch {
          // Keep the current element time if Safari refuses this stabilizing rewind.
        }
      }
      actualMediaElementTimeRef.current = stablePosition;
      setActualMediaElementTime(stablePosition);
    }
    const payload = await seekOptimizedPlaybackSession({
      seekUrl: activeSession.seek_url,
      targetPositionSeconds: targetPosition,
      lastStablePositionSeconds: stablePosition,
      playingBeforeSeek: resumeAfterReady,
    });
    const acceptedPayload = acceptBrowserPlaybackSessionPayload(payload, SESSION_SOURCE_SEEK);
    if (!acceptedPayload.accepted) {
      return;
    }
    if (isRoute2SessionPayload(payload)) {
      if (maybeAttachRoute2Authority(payload, { autoplay: resumeAfterReady })) {
        return;
      }
      if (
        payload.pending_target_seconds == null
        && isRoute2AttachReady(payload)
        && !route2AttachmentNeedsReattach(payload)
      ) {
        completeRoute2LocalTargetTransition(payload, targetPosition);
        return;
      }
      scheduleMobilePlaybackPoll(
        payload.session_id,
        Math.max(1000, Math.round((payload.status_poll_seconds || 1) * 1000)),
      );
      return;
    }
    if (payload.playback_commit_ready) {
      completeMobileTargetTransition(payload);
      return;
    }
    scheduleMobilePlaybackPoll(
      payload.session_id,
      Math.max(1000, Math.round((payload.status_poll_seconds || 1) * 1000)),
    );
  }

  async function selectBrowserPlaybackAudioTrack(track) {
    const activeSession = mobileSessionRef.current;
    const streamIndex = Number(track?.index);
    if (!activeSession?.session_id || !Number.isInteger(streamIndex) || streamIndex < 0) {
      return;
    }
    const video = videoRef.current;
    const currentPosition = resolvePlaybackRecoveryTargetSeconds({
      currentAbsolutePositionSeconds: video
        ? resolveSessionAbsoluteTime(activeSession, Math.max(video.currentTime || 0, 0))
        : null,
      committedPlayheadSeconds: committedPlayheadSecondsRef.current,
      actualMediaElementTimeSeconds: actualMediaElementTimeRef.current,
      targetPositionSeconds: resolveMobileAuthorityPosition(activeSession),
    });
    const payload = await selectOptimizedPlaybackAudioTrack({
      browserPlaybackSessionRoot,
      sessionId: activeSession.session_id,
      selectedAudioStreamIndex: streamIndex,
      currentPositionSeconds: currentPosition,
      playingBeforeSwitch: video ? !video.paused : activeSession.client_is_playing,
    });
    const acceptedPayload = acceptBrowserPlaybackSessionPayload(payload, SESSION_SOURCE_STATUS);
    if (!acceptedPayload.accepted) {
      return;
    }
    syncMobilePlaybackState(payload);
    scheduleMobilePlaybackPoll(
      payload.session_id,
      Math.max(1000, Math.round((payload.status_poll_seconds || 1) * 1000)),
    );
    return payload;
  }

  async function prepareBrowserPlaybackSubtitleTrack(track) {
    const activeSession = mobileSessionRef.current;
    const streamIndex = Number(track?.index);
    if (!activeSession?.session_id || !Number.isInteger(streamIndex) || streamIndex < 0) {
      return null;
    }
    setSeekNotice(`Preparing subtitle track: ${track.label || `Subtitle ${streamIndex}`}`);
    const payload = await prepareOptimizedPlaybackSubtitleTrack({
      browserPlaybackSessionRoot,
      sessionId: activeSession.session_id,
      streamIndex,
    });
    setSeekNotice("");
    return payload;
  }

  async function restoreActiveBrowserPlaybackSession() {
    // Route 2's reusable preparation cache lives on the backend session/epoch
    // workspace. Browser buffers are transient and should not be treated as
    // long-term storage for multi-GB media, especially on iPhone Safari.
    const payload = await fetchActiveOptimizedPlaybackSession({
      browserPlaybackSessionRoot,
      itemId,
    });
    if (!payload) {
      return false;
    }
    setPlaybackError("");
    setSeekNotice("");
    return ensureMobileSessionReady(payload, {
      autoplay: false,
      targetPosition: resolveMobileAuthorityPosition(payload),
    }, {
      source: SESSION_SOURCE_RESTORE_ACTIVE,
    });
  }

  useEffect(() => {
    mobilePlayerCanPlayRef.current = mobilePlayerCanPlay;
  }, [mobilePlayerCanPlay]);

  useEffect(() => {
    if (optimizedPlaybackPending || (mobileSession && !mobilePlayerCanPlay)) {
      setPrepareEstimateNowMs(Date.now());
      const timerId = window.setInterval(() => {
        setPrepareEstimateNowMs(Date.now());
      }, 1000);
      return () => {
        window.clearInterval(timerId);
      };
    }
    return undefined;
  }, [mobilePlayerCanPlay, mobileSession, optimizedPlaybackPending]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return undefined;
    }
    const pending = audioSwitchAttachRef.current;
    if (!isAudioSwitchAttachWaiting(pending)) {
      return undefined;
    }
    const nowMs = Date.now();
    const startedAtMs = Number.isFinite(pending.sourceSetAtMs)
      ? pending.sourceSetAtMs
      : Number.isFinite(pending.requestedAtMs)
        ? pending.requestedAtMs
        : nowMs;
    const elapsedMs = Math.max(0, nowMs - startedAtMs);
    const remainingMs = Math.max(0, AUDIO_SWITCH_ATTACH_LOAD_TIMEOUT_MS - elapsedMs);
    const timerId = window.setTimeout(() => {
      handleAudioSwitchAttachLoadTimeout();
    }, remainingMs);
    return () => {
      window.clearTimeout(timerId);
    };
  }, [
    mobilePlayerCanPlay,
    mobileSession?.active_epoch_id,
    mobileSession?.active_audio_stream_index,
    mobileSession?.attach_revision,
    mobileSession?.client_attach_revision,
    streamSource,
    videoElementKey,
  ]);

  useEffect(() => {
    if (
      typeof window === "undefined"
      || !isRoute2SessionPayload(mobileSession)
      || getPlaybackMode(mobileSession?.playback_mode) !== "full"
      || mobileSession?.mode_ready
      || fullProbeInFlightRef.current
    ) {
      return undefined;
    }

    async function runFullPlaybackProbe() {
      const activeSession = mobileSessionRef.current;
      if (
        !isRoute2SessionPayload(activeSession)
        || getPlaybackMode(activeSession?.playback_mode) !== "full"
        || activeSession?.mode_ready
      ) {
        return;
      }
      const probeUrl = buildHlsProbeSegmentUrl(activeSession);
      if (!probeUrl || fullProbeInFlightRef.current) {
        return;
      }
      fullProbeInFlightRef.current = true;
      const startedAt = typeof performance !== "undefined" ? performance.now() : Date.now();
      try {
        const response = await fetch(probeUrl, {
          credentials: "include",
          cache: "no-store",
        });
        if (!response.ok) {
          return;
        }
        const buffer = await response.arrayBuffer();
        const finishedAt = typeof performance !== "undefined" ? performance.now() : Date.now();
        const durationMs = Math.max(1, Math.round(finishedAt - startedAt));
        if (buffer.byteLength <= 0) {
          return;
        }
        await postMobileRuntimeHeartbeat({
          clientProbeBytes: buffer.byteLength,
          clientProbeDurationMs: durationMs,
          playing: false,
        }).catch(() => {
          // The next scheduled probe/heartbeat will retry naturally.
        });
      } finally {
        fullProbeInFlightRef.current = false;
      }
    }

    runFullPlaybackProbe().catch(() => {
      // Ignore probe failures; Full mode will stay estimating until enough clean samples arrive.
    });
    const timerId = window.setInterval(() => {
      runFullPlaybackProbe().catch(() => {
        // Ignore probe failures; future samples can still recover confidence.
      });
    }, 5000);
    return () => {
      window.clearInterval(timerId);
    };
  }, [mobileSession?.manifest_end_segment, mobileSession?.mode_ready, mobileSession?.playback_mode, mobileSession?.session_id]);

  return {
    mobileSessionRef,
    mobilePendingTargetRef,
    requestedTargetSecondsRef,
    mobileAutoplayPendingRef,
    mobileResumeAfterReadyRef,
    mobileSeekPendingRef,
    pendingSeekPhaseRef,
    mobileAttachedEpochRef,
    mobileCanPlaySeenRef,
    mobileLoadedDataSeenRef,
    mobileAwaitingTargetSeekRef,
    mobileFrameReadyRef,
    mobileFrameProbePendingRef,
    mobileReadinessGenerationRef,
    mobilePlayerCanPlayRef,
    mobileWarmupProbeActiveRef,
    mobileWarmupPlaybackObservedRef,
    mobileWarmupStartPositionRef,
    mobileRetargetTransitionRef,
    mobileLastStablePositionRef,
    mobileLifecycleStateRef,
    mobileRecoveryInFlightRef,
    mobileLastHeartbeatAtRef,
    mobileHeartbeatInFlightRef,
    mobileWasBackgroundedRef,
    mobileBackgroundHiddenAtRef,
    mobileWasPlayingBeforeSuspendRef,
    mobileStallTimerRef,
    mobileStallStartedAtRef,
    audioSwitchAttachRef,
    committedPlayheadSecondsRef,
    actualMediaElementTimeRef,
    mobileSession,
    activePlaybackMode,
    browserPlaybackLabel,
    browserPlaybackLabelTitle,
    browserStreamLabelTitle,
    browserReadyLabelTitle,
    mobilePlayerCanPlay,
    mobileFrozenFrameUrl,
    prepareEstimateObservedAtMs,
    prepareEstimateNowMs,
    videoElementKey,
    setRequestedTargetSeconds,
    setCommittedPlayheadSeconds,
    setActualMediaElementTime,
    setPendingSeekPhase,
    setMobilePlayerCanPlay,
    setMobileFrozenFrameUrl,
    setMobileLifecycleStateValue,
    applyMobileLifecycleStatus,
    resetMobilePlaybackState,
    isHlsSessionPayload,
    isRoute2SessionPayload,
    resolveSessionAttachmentIdentity,
    resolveMobileCommittedPosition,
    syncMobilePlaybackState,
    postMobileRuntimeHeartbeat,
    maybeAcknowledgeHlsAttachment,
    maybeAcknowledgeRoute2Attachment,
    maybeAttachRoute2Authority,
    recoverMobilePlaybackAfterResume,
    softResumeMobilePlaybackAfterBackground,
    startMobileOptimizedPlayback,
    retargetMobileOptimizedPlayback,
    selectBrowserPlaybackAudioTrack,
    prepareBrowserPlaybackSubtitleTrack,
    restoreActiveBrowserPlaybackSession,
    finalizeRetargetVisibility,
  };
}
