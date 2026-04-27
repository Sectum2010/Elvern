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
  createOptimizedPlaybackSession,
  fetchActiveOptimizedPlaybackSession,
  fetchOptimizedPlaybackSessionStatus,
  postOptimizedPlaybackHeartbeat,
  seekOptimizedPlaybackSession,
} from "./browserSessionClient";

const SESSION_MANIFEST_REFRESH_RUNWAY_SECONDS = 12;

function buildSessionManifestUrl(url, manifestRevision) {
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}manifest_revision=${encodeURIComponent(manifestRevision)}`;
}

function buildAttachRevisionManifestUrl(url, attachRevision) {
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}attach_revision=${encodeURIComponent(String(attachRevision || 0))}`;
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
  const mobileWasPlayingBeforeSuspendRef = useRef(false);
  const mobileStallTimerRef = useRef(null);
  const mobileStallStartedAtRef = useRef(0);
  const mobileClientAttachRevisionRef = useRef(0);
  const mobilePendingAttachRevisionRef = useRef(0);
  const route2LastAttachAttemptAtRef = useRef(0);
  const route2LastAttachAttemptRevisionRef = useRef(0);
  const committedPlayheadSecondsRef = useRef(0);
  const actualMediaElementTimeRef = useRef(0);
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
    }
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
    return Math.max(payload.ready_end_seconds || 0, 0);
  }

  function resolveCurrentManifestPosition(payload = mobileSessionRef.current) {
    if (!payload) {
      return 0;
    }
    if (isRoute2SessionPayload(payload)) {
      return resolveRoute2AttachPosition(payload);
    }
    return Math.max(payload.target_position_seconds || 0, 0);
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
    if (currentPosition < currentManifestEnd - SESSION_MANIFEST_REFRESH_RUNWAY_SECONDS) {
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
        mobileLifecycleStateRef.current === "recovering"
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

  function maybeAttachHlsAuthority(payload, { autoplay = false } = {}) {
    if (!isHlsAttachReady(payload) || !hlsAttachmentNeedsReattach(payload)) {
      return false;
    }
    armMobileManifestAttachment(payload, {
      autoplay,
      targetPosition: resolveHlsAttachPosition(payload),
      preserveAuthority: true,
      resetSeekPreparation: true,
      forceReattach: Boolean(attachedOptimizedManifestUrlRef.current),
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

  function resolveMobileAuthorityPosition(payload = mobileSessionRef.current) {
    if (typeof payload?.pending_target_seconds === "number") {
      return Math.max(payload.pending_target_seconds, 0);
    }
    if (typeof payload?.target_position_seconds === "number") {
      return Math.max(payload.target_position_seconds, 0);
    }
    return Math.max(resolveMobileCommittedPosition(payload), 0);
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
  } = {}) {
    const activeSession = mobileSessionRef.current;
    if (!activeSession?.session_id) {
      return null;
    }
    const payload = {
      committed_playhead_seconds: resolveMobileCommittedPosition(activeSession),
      actual_media_element_time_seconds: actualMediaElementTimeRef.current || 0,
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

  function maybeAcknowledgeHlsAttachment({ playing = null, force = false } = {}) {
    const activeSession = mobileSessionRef.current;
    if (!isHlsAttachReady(activeSession)) {
      return;
    }
    const serverAttachRevision = Number(activeSession.attach_revision || 0);
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
  }

  function maybeAcknowledgeRoute2Attachment({ playing = null, force = false } = {}) {
    maybeAcknowledgeHlsAttachment({ playing, force });
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
    mobileRecoveryInFlightRef.current = true;
    applyMobileLifecycleStatus("resuming");
    setOptimizedPlaybackPending(true);
    setSeekNotice(`Reconnecting the current ${browserPlaybackLabel} session.`);
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
        const recoveryTarget = resolveMobileCommittedPosition(activeSession);
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
        const recoveryTarget = resolveMobileCommittedPosition(payload);
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
      const recoveryTarget = resolveMobileAuthorityPosition(payload);
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
    mobileWasPlayingBeforeSuspendRef,
    mobileStallTimerRef,
    mobileStallStartedAtRef,
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
    recoverMobilePlaybackAfterResume,
    startMobileOptimizedPlayback,
    retargetMobileOptimizedPlayback,
    restoreActiveBrowserPlaybackSession,
    finalizeRetargetVisibility,
  };
}
