import Hls from "hls.js";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  getPlaybackMode,
  resolveBrowserPlaybackSessionRoot,
} from "../../lib/browserPlayback";
import {
  capBrowserPlaybackProfileForDeviceClass,
  detectBrowserPlaybackDeviceClass,
} from "../../lib/browserPlaybackDevice";
import { resolveBrowserHlsEngine } from "../../lib/browserHlsEngine";
import {
  buildHlsConfig,
  classifyManifestWindowState,
  classifyPlaybackStall,
  compactHlsBufferConfig,
  deriveBufferTargetsFromSession,
  evaluateClientPlaybackReleaseGate,
  hasVideoFirstFrameForPlaybackRelease,
  muteVideoForClientPrewarm,
  readClientBufferedAheadSeconds,
  readClientPlaybackLiveness,
  restoreVideoAfterClientPrewarm,
  retuneHlsInstance,
  resolveAutomaticPlaybackRecoveryTarget,
  shouldStartClientBufferPrewarm,
  shouldRecoverNativeHlsStalePlaylist,
  shouldDisarmFirstFrameStallMonitor,
  shouldStartVisibleHlsSupplyRecovery,
} from "../../lib/browserPlaybackBufferPolicy";
import {
  buildBrowserPlaybackDiagnosticPayload,
  logBrowserPlaybackDiagnostic,
} from "../../lib/browserPlaybackDiagnostics";
import {
  getActivePlaybackWorkerConflict,
  getPlaybackAdmissionError,
  getPlaybackWorkerCooldown,
} from "../../lib/playbackWorkerOwnership";
import { getProviderAuthRequirement } from "../../lib/providerAuth";
import {
  getBrowserPlaybackAttachedManifestEndSeconds,
  isBrowserPlaybackAbsolutePositionReady,
  toBrowserPlaybackAbsoluteSeconds,
  toBrowserPlaybackMediaElementSeconds,
} from "../../lib/browserPlaybackTimeline";
import { getContiguousClientBufferedAheadSeconds } from "../../lib/playbackTimelineRanges";
import {
  RETARGET_CLIENT_BUFFER_RELEASE_SECONDS,
  shouldReleaseRetargetFrozenFrame,
} from "../../lib/browserPlaybackRetargetReadiness";
import { resolveBrowserPlaybackResumePosition } from "../../lib/browserPlaybackResume";
import { formatDuration } from "../../lib/format";
import {
  fetchPlaybackDecision,
  recordPlaybackEvent,
  savePlaybackProgress,
  startPlaybackPreparation,
  stopBrowserPlaybackSession,
} from "./browserSessionClient";
import { useOptimizedPlaybackSession } from "./useOptimizedPlaybackSession";

const SEEK_HEADROOM_SECONDS = 2;
const COMPLETION_GRACE_SECONDS = 15;
const IOS_OPTIMIZED_READY_SECONDS = 18;
const IOS_STABLE_READY_BACKEND_RUNWAY_SECONDS = 16;
const IOS_STABLE_READY_PLAYHEAD_ADVANCE_SECONDS = 0.5;

function readFiniteDuration(video) {
  if (!video) {
    return 0;
  }
  return Number.isFinite(video.duration) && video.duration > 0
    ? video.duration
    : 0;
}

function buildFreshManifestUrl(url) {
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}vod_attach=${Date.now()}`;
}

function readHlsSupportDiagnostics(video) {
  const canPlayAppleHls = video?.canPlayType?.("application/vnd.apple.mpegurl") || "";
  const canPlayXMpegUrl = video?.canPlayType?.("application/x-mpegURL") || "";
  return {
    canPlayTypeApplicationVndAppleMpegurl: canPlayAppleHls,
    canPlayTypeApplicationXMpegUrl: canPlayXMpegUrl,
    nativeHlsSupport: canPlayAppleHls || canPlayXMpegUrl,
    hlsJsSupported: Hls.isSupported(),
    hlsJsVersion: Hls.version || "",
  };
}

function compactHlsConfig(config = {}) {
  return compactHlsBufferConfig(config);
}

export function useBrowserPlaybackController({
  itemId,
  item,
  progress,
  iosMobile,
  onProgressChange,
  onProgressDirty,
  onProviderAuthRequired,
}) {
  const videoRef = useRef(null);
  const hlsRef = useRef(null);
  const nativeLivenessSampleRef = useRef(null);
  const firstFrameStallTimerRef = useRef(null);
  const firstFrameStallBaselineRef = useRef(null);
  const firstFrameAttachmentKeyRef = useRef("");
  const firstFrameAttachmentStartPositionRef = useRef(0);
  const firstFrameStallDisarmedRef = useRef(false);
  const firstFrameSuccessfulTimeupdateCountRef = useRef(0);
  const firstFramePlaybackAdvancingSinceRef = useRef(0);
  const firstFrameRecoveryAttemptsRef = useRef(new Map());
  const playbackStateRef = useRef(null);
  const browserPlaybackActiveRef = useRef(false);
  const playbackOpenedReportedRef = useRef(false);
  const progressTimerRef = useRef(null);
  const playbackPollRef = useRef(null);
  const browserPlayRequestedRef = useRef(false);
  const resumeAppliedRef = useRef(false);
  const pendingResumeRef = useRef(0);
  const fallbackAttemptedRef = useRef(false);
  const forceHlsRef = useRef(false);
  const optimizedVodRequiredRef = useRef(false);
  const playbackFlowRef = useRef(0);
  const currentItemIdRef = useRef(itemId);
  const attachedOptimizedManifestUrlRef = useRef("");
  const playbackPollGenerationRef = useRef(0);
  const browserStartPositionRef = useRef(0);
  const playbackModeIntentRef = useRef("lite");
  const mobilePrewarmAudioStateRef = useRef(null);
  const browserPlaybackDiagnosticLastLogRef = useRef(new Map());

  const [playback, setPlayback] = useState(null);
  const [streamSource, setStreamSource] = useState(null);
  const [playbackError, setPlaybackError] = useState("");
  const [seekNotice, setSeekNotice] = useState("");
  const [playbackStatus, setPlaybackStatus] = useState("Checking playback compatibility");
  const [playbackPosition, setPlaybackPosition] = useState(0);
  const [playerMeasuredDuration, setPlayerMeasuredDuration] = useState(0);
  const [optimizedPlaybackPending, setOptimizedPlaybackPending] = useState(false);
  const [playbackModeIntent, setPlaybackModeIntent] = useState("lite");
  const [hlsEngineDiagnostics, setHlsEngineDiagnostics] = useState({
    selectedEngine: "none",
    nativeHlsSelected: false,
    hlsJsSelected: false,
    hlsJsAttachedToVideo: false,
    hlsJsConfig: null,
  });

  const browserPlaybackSessionRoot = resolveBrowserPlaybackSessionRoot();
  const browserPlaybackDeviceClass = useMemo(() => {
    if (typeof navigator === "undefined") {
      return iosMobile ? "phone" : "unknown";
    }
    return detectBrowserPlaybackDeviceClass({
      userAgent: navigator.userAgent,
      maxTouchPoints: navigator.maxTouchPoints,
    });
  }, [iosMobile]);
  const browserPlaybackProfile = useMemo(
    () => capBrowserPlaybackProfileForDeviceClass({
      deviceClass: browserPlaybackDeviceClass,
      requestedProfile: "mobile_2160p",
    }),
    [browserPlaybackDeviceClass],
  );

  function clearOptimizedPlaybackPending() {
    setOptimizedPlaybackPending(false);
  }

  function setPlaybackModeIntentValue(nextPlaybackMode) {
    playbackModeIntentRef.current = getPlaybackMode(nextPlaybackMode);
    setPlaybackModeIntent(playbackModeIntentRef.current);
  }

  function clearPlaybackError() {
    setPlaybackError("");
  }

  function muteVideoForInternalPrewarm(video = videoRef.current) {
    if (!video) {
      return;
    }
    mobilePrewarmAudioStateRef.current = muteVideoForClientPrewarm(
      video,
      mobilePrewarmAudioStateRef.current,
    );
  }

  function restoreVideoAudioAfterPrewarm(video = videoRef.current) {
    const previous = mobilePrewarmAudioStateRef.current;
    if (!previous) {
      return;
    }
    mobilePrewarmAudioStateRef.current = restoreVideoAfterClientPrewarm(video, previous);
  }

  function stopPlaybackPolling() {
    playbackPollGenerationRef.current += 1;
    window.clearInterval(playbackPollRef.current);
    playbackPollRef.current = null;
  }

  function clearPlayerBinding() {
    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }
  }

  function clearPlaybackResources() {
    browserPlaybackActiveRef.current = false;
    playbackOpenedReportedRef.current = false;
    stopPlaybackPolling();
    clearPlayerBinding();
    restoreVideoAudioAfterPrewarm();
    attachedOptimizedManifestUrlRef.current = "";
  }

  const {
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
    resetMobilePlaybackState: resetOptimizedPlaybackSessionState,
    isHlsSessionPayload,
    resolveSessionAttachmentIdentity,
    resolveMobileCommittedPosition,
    syncMobilePlaybackState,
    postMobileRuntimeHeartbeat,
    maybeAcknowledgeHlsAttachment,
    recoverMobilePlaybackAfterResume,
    softResumeMobilePlaybackAfterBackground,
    startMobileOptimizedPlayback,
    retargetMobileOptimizedPlayback,
    selectBrowserPlaybackAudioTrack,
    prepareBrowserPlaybackSubtitleTrack,
    restoreActiveBrowserPlaybackSession: restoreOptimizedPlaybackSession,
    finalizeRetargetVisibility,
  } = useOptimizedPlaybackSession({
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
    hlsRef,
    hlsEngineDiagnostics,
  });

  const resumePosition = useMemo(() => {
    if (!progress || progress.completed) {
      return 0;
    }
    return progress.position_seconds || item?.resume_position_seconds || 0;
  }, [item?.resume_position_seconds, progress]);

  const fullDuration = useMemo(() => {
    if (mobileSession?.duration_seconds && mobileSession.duration_seconds > 0) {
      return mobileSession.duration_seconds;
    }
    if (item?.duration_seconds && item.duration_seconds > 0) {
      return item.duration_seconds;
    }
    if (playback?.expected_duration_seconds && playback.expected_duration_seconds > 0) {
      return playback.expected_duration_seconds;
    }
    if (progress?.duration_seconds && progress.duration_seconds > 0) {
      return progress.duration_seconds;
    }
    if (playback?.mode !== "hls" && playerMeasuredDuration > 0) {
      return playerMeasuredDuration;
    }
    return 0;
  }, [
    item?.duration_seconds,
    mobileSession?.duration_seconds,
    playback?.expected_duration_seconds,
    playback?.mode,
    progress?.duration_seconds,
    playerMeasuredDuration,
  ]);

  const resumableStartPosition = useMemo(() => {
    return resolveBrowserPlaybackResumePosition({
      progressPayload: progress,
      fallbackResumePositionSeconds: item?.resume_position_seconds || 0,
      durationSeconds: fullDuration,
      completionGraceSeconds: COMPLETION_GRACE_SECONDS,
    });
  }, [fullDuration, item?.resume_position_seconds, progress]);

  const availableDuration = useMemo(() => {
    if (mobileSession) {
      return Math.max(mobileSession.ready_end_seconds || 0, 0);
    }
    if (playback?.mode !== "hls") {
      return fullDuration || playerMeasuredDuration || 0;
    }
    const generatedDuration = Math.max(playback?.generated_duration_seconds || 0, 0);
    if (playback?.manifest_complete) {
      return fullDuration || generatedDuration;
    }
    return generatedDuration;
  }, [
    fullDuration,
    mobileSession,
    playback?.generated_duration_seconds,
    playback?.manifest_complete,
    playback?.mode,
    playerMeasuredDuration,
  ]);

  function resolveCurrentVideoAbsolutePosition(session = mobileSessionRef.current, video = videoRef.current) {
    if (!video) {
      return 0;
    }
    const mediaElementTime = Math.max(video.currentTime || 0, 0);
    return session
      ? toBrowserPlaybackAbsoluteSeconds(session, mediaElementTime)
      : mediaElementTime;
  }

  function buildAutomaticRecoveryTargetDiagnostic(
    session = mobileSessionRef.current,
    recoveryReason = "",
    video = videoRef.current,
  ) {
    return resolveAutomaticPlaybackRecoveryTarget({
      currentAbsolutePositionSeconds: session && video
        ? resolveCurrentVideoAbsolutePosition(session, video)
        : null,
      lastStablePositionSeconds: mobileLastStablePositionRef.current,
      committedPlayheadSeconds: committedPlayheadSecondsRef.current,
      actualMediaElementTimeSeconds: actualMediaElementTimeRef.current,
      targetPositionSeconds: resolveMobileReleaseTargetPosition(session, video),
      recoveryReason,
    });
  }

  function resolveMediaElementPositionForAbsolute(session, absoluteSeconds) {
    return session
      ? toBrowserPlaybackMediaElementSeconds(session, absoluteSeconds)
      : Math.max(absoluteSeconds || 0, 0);
  }

  function resolveMobileReleaseTargetPosition(session, video = videoRef.current) {
    if (mobilePendingTargetRef.current != null) {
      return mobilePendingTargetRef.current;
    }
    if (session?.pending_target_seconds != null) {
      return Math.max(0, Number(session.pending_target_seconds) || 0);
    }
    if (session?.target_position_seconds != null) {
      return Math.max(0, Number(session.target_position_seconds) || 0);
    }
    const seekOrRetargetActive =
      mobileSeekPendingRef.current
      || mobileRetargetTransitionRef.current
      || pendingSeekPhaseRef.current !== "idle";
    if (seekOrRetargetActive) {
      if (requestedTargetSecondsRef.current != null) {
        return requestedTargetSecondsRef.current;
      }
    }
    if (requestedTargetSecondsRef.current != null) {
      return requestedTargetSecondsRef.current;
    }
    return resolveCurrentVideoAbsolutePosition(session, video);
  }

  function isAudioSwitchAttachWaitingForClientRelease() {
    const pending = audioSwitchAttachRef.current;
    return Boolean(
      pending
      && pending.phase !== "acked"
      && pending.phase !== "failed"
    );
  }

  function evaluateMobileClientReleaseGate(session, video = videoRef.current) {
    if (!session || !video) {
      return {
        ready: false,
        clientReady: false,
        serverReady: false,
        requiredClientBufferSeconds: 0,
        clientBufferedAheadSeconds: 0,
        backendPreparedAheadSeconds: 0,
      };
    }
    const targetAbsoluteSeconds = resolveMobileReleaseTargetPosition(session, video);
    const clientBufferedAheadSeconds = getContiguousClientBufferedAheadSeconds(
      video,
      targetAbsoluteSeconds,
      session,
    );
    const manifestEndSeconds = isHlsSessionPayload(session)
      ? getBrowserPlaybackAttachedManifestEndSeconds(session)
      : Number(session.ready_end_seconds || 0);
    const backendPreparedAheadSeconds = Math.max(
      0,
      Number(manifestEndSeconds || 0) - targetAbsoluteSeconds,
    );
    const durationSeconds = [
      Number(session.duration_seconds || 0) || 0,
      Number(session.media_duration_seconds || 0) || 0,
      Number(session.full_duration_seconds || 0) || 0,
      Number(fullDuration || 0) || 0,
      Number.isFinite(video.duration) ? Number(video.duration) : 0,
    ].find((candidate) => candidate > 0) || 0;
    const remainingPlayableSeconds = durationSeconds > 0
      ? Math.max(0, durationSeconds - targetAbsoluteSeconds)
      : null;
    const gate = evaluateClientPlaybackReleaseGate({
      session,
      clientBufferedAheadSeconds,
      backendPreparedAheadSeconds,
      remainingPlayableSeconds,
      deviceClass: browserPlaybackDeviceClass,
      audioSwitch: isAudioSwitchAttachWaitingForClientRelease(),
    });
    return {
      ...gate,
      targetAbsoluteSeconds,
      remainingPlayableSeconds,
    };
  }

  function logBrowserPlaybackDiagnosticEvent(eventName, {
    clientPlaybackStallReason = "",
    eventReason = "",
    firstFrameReady = null,
    releaseGate = null,
    releaseGateReason = "",
    recoveryDecision = null,
    recoveryTarget = null,
    session = mobileSessionRef.current,
    staleNativePlaylistStall = null,
    video = videoRef.current,
    livenessSample = null,
  } = {}) {
    const activeVideo = video || videoRef.current;
    const activeSession = session || mobileSessionRef.current;
    const safeReleaseGate =
      releaseGate
      || (
        activeSession && activeVideo
          ? evaluateMobileClientReleaseGate(activeSession, activeVideo)
          : null
      );
    const safeLivenessSample =
      livenessSample
      || (
        activeVideo
          ? readClientPlaybackLiveness(activeVideo, nativeLivenessSampleRef.current)
          : null
      );
    const payload = buildBrowserPlaybackDiagnosticPayload({
      eventReason,
      session: activeSession,
      video: activeVideo,
      releaseGate: safeReleaseGate,
      livenessSample: safeLivenessSample,
      clientPlaybackStallReason,
      mobilePlayerCanPlay: mobilePlayerCanPlayRef.current,
      mobileLifecycleState: mobileLifecycleStateRef.current,
      firstFrameReady,
      loadedDataSeen: mobileLoadedDataSeenRef.current,
      canPlaySeen: mobileCanPlaySeenRef.current,
      frameReady: mobileFrameReadyRef.current,
      releaseGateReason,
      recoveryDecision,
      recoveryTarget,
      staleNativePlaylistStall,
    });
    logBrowserPlaybackDiagnostic({
      eventName,
      payload,
      lastLogMap: browserPlaybackDiagnosticLastLogRef.current,
    });
  }

  function logAutomaticRecoveryTarget(recoveryReason, {
    session = mobileSessionRef.current,
    video = videoRef.current,
  } = {}) {
    logBrowserPlaybackDiagnosticEvent("elvern:browser_playback_recovery_target", {
      clientPlaybackStallReason: recoveryReason,
      eventReason: recoveryReason,
      recoveryTarget: buildAutomaticRecoveryTargetDiagnostic(session, recoveryReason, video),
      session,
      video,
    });
  }

  function prepareControllerForLoad(nextItemId = itemId) {
    playbackFlowRef.current += 1;
    currentItemIdRef.current = nextItemId;
    setPlaybackError("");
    setSeekNotice("");
    setPlaybackStatus("Checking playback compatibility");
    setStreamSource(null);
    setPlayback(null);
    setPlaybackPosition(0);
    setPlayerMeasuredDuration(0);
    setHlsEngineDiagnostics({
      selectedEngine: "none",
      nativeHlsSelected: false,
      hlsJsSelected: false,
      hlsJsAttachedToVideo: false,
      hlsJsConfig: null,
      bufferTier: null,
      bufferPolicySource: null,
    });
    nativeLivenessSampleRef.current = null;
    window.clearTimeout(firstFrameStallTimerRef.current);
    firstFrameStallTimerRef.current = null;
    firstFrameStallBaselineRef.current = null;
    firstFrameAttachmentKeyRef.current = "";
    firstFrameAttachmentStartPositionRef.current = 0;
    firstFrameStallDisarmedRef.current = false;
    firstFrameSuccessfulTimeupdateCountRef.current = 0;
    firstFramePlaybackAdvancingSinceRef.current = 0;
    firstFrameRecoveryAttemptsRef.current = new Map();
    browserPlaybackDiagnosticLastLogRef.current = new Map();
    clearOptimizedPlaybackPending();
    fallbackAttemptedRef.current = false;
    forceHlsRef.current = false;
    optimizedVodRequiredRef.current = false;
    resumeAppliedRef.current = false;
    pendingResumeRef.current = 0;
    browserStartPositionRef.current = 0;
    clearPlaybackResources();
    resetOptimizedPlaybackSessionState();
    const video = videoRef.current;
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }
  }

  function resetMobilePlaybackState(options) {
    resetOptimizedPlaybackSessionState(options);
  }

  async function restoreActiveBrowserPlaybackSession() {
    return restoreOptimizedPlaybackSession();
  }

  function syncPlaybackState(payload) {
    playbackStateRef.current = payload;
    setPlayback(payload);
    if (payload.mode === "direct") {
      setPlaybackStatus("Direct Play");
      return;
    }
    if (browserPlaybackActiveRef.current) {
      if (payload.manifest_complete || payload.transcode_status === "completed") {
        setPlaybackStatus("Optimized stream");
        return;
      }
      setPlaybackStatus("Optimized stream");
      return;
    }
    if (payload.transcode_status === "busy") {
      setPlaybackStatus("Transcode queue busy");
      return;
    }
    if (payload.transcode_status === "failed") {
      setPlaybackStatus("Optimized stream failed");
      return;
    }
    if (payload.manifest_complete || payload.transcode_status === "completed") {
      setPlaybackStatus("Optimized stream");
      return;
    }
    if (payload.transcode_status === "idle") {
      setPlaybackStatus("Browser playback ready");
      return;
    }
    setPlaybackStatus("Optimizing for playback");
  }

  function resolveHlsAttachUrl(hlsUrl, waitForComplete) {
    if (!waitForComplete) {
      return hlsUrl;
    }
    if (!attachedOptimizedManifestUrlRef.current) {
      attachedOptimizedManifestUrlRef.current = buildFreshManifestUrl(hlsUrl);
    }
    return attachedOptimizedManifestUrlRef.current;
  }

  function startPlaybackPolling(forceHls = false, flowId = playbackFlowRef.current) {
    stopPlaybackPolling();
    const pollGeneration = playbackPollGenerationRef.current;
    let inFlight = false;
    playbackPollRef.current = window.setInterval(async () => {
      if (inFlight) {
        return;
      }
      inFlight = true;
      try {
        const current = await fetchPlaybackDecision({ itemId, forceHls });
        if (
          flowId !== playbackFlowRef.current
          || currentItemIdRef.current !== itemId
          || pollGeneration !== playbackPollGenerationRef.current
        ) {
          return;
        }
        const waitForComplete = iosMobile && optimizedVodRequiredRef.current;
        const readyForAttach =
          current.manifest_ready
          && current.hls_url
          && (!waitForComplete
            || current.manifest_complete
            || (current.generated_duration_seconds || 0) >= IOS_OPTIMIZED_READY_SECONDS);
        syncPlaybackState(current);
        if (current.last_error && !current.manifest_ready) {
          stopPlaybackPolling();
          setPlaybackError(current.last_error);
          return;
        }
        if (readyForAttach) {
          const resolvedUrl = resolveHlsAttachUrl(current.hls_url, waitForComplete);
          setStreamSource((existing) => {
            if (existing?.mode === "hls" && existing.url === resolvedUrl) {
              return existing;
            }
            return {
              mode: "hls",
              url: resolvedUrl,
            };
          });
        }
        if (
          current.manifest_complete
          || ["busy", "completed", "failed", "disabled"].includes(current.transcode_status)
        ) {
          stopPlaybackPolling();
        }
      } catch (requestError) {
        stopPlaybackPolling();
        setPlaybackError(requestError.message || "Failed to refresh playback status");
      } finally {
        inFlight = false;
      }
    }, 3000);
  }

  async function prepareHlsPlayback(forceHls = false, flowId = playbackFlowRef.current) {
    stopPlaybackPolling();
    clearPlayerBinding();
    if (videoRef.current) {
      videoRef.current.pause();
      videoRef.current.removeAttribute("src");
      videoRef.current.load();
    }
    setStreamSource(null);
    setPlaybackError("");
    setSeekNotice("");

    const shouldForceHls = forceHlsRef.current || forceHls;
    const waitForComplete = iosMobile && optimizedVodRequiredRef.current;
    const readyForAttach = (payload) =>
      payload.manifest_ready
      && payload.hls_url
      && (!waitForComplete
        || payload.manifest_complete
        || (payload.generated_duration_seconds || 0) >= IOS_OPTIMIZED_READY_SECONDS);
    forceHlsRef.current = shouldForceHls;

    const startPayload = await startPlaybackPreparation({
      itemId,
      forceHls: shouldForceHls,
    });
    if (flowId !== playbackFlowRef.current || currentItemIdRef.current !== itemId) {
      return;
    }
    syncPlaybackState(startPayload);

    if (startPayload.last_error && !startPayload.manifest_ready) {
      setPlaybackError(startPayload.last_error);
      return;
    }

    if (readyForAttach(startPayload)) {
      const resolvedUrl = resolveHlsAttachUrl(startPayload.hls_url, waitForComplete);
      setStreamSource({
        mode: "hls",
        url: resolvedUrl,
      });
    }

    if (
      !startPayload.manifest_complete
      && !["busy", "completed", "failed", "disabled"].includes(startPayload.transcode_status)
    ) {
      startPlaybackPolling(shouldForceHls, flowId);
    }
  }

  function cancelBrowserPlaybackRequest() {
    browserPlayRequestedRef.current = false;
  }

  function clearPlaybackStreamSource() {
    setStreamSource(null);
  }

  function setSeekNoticeValue(nextValue) {
    setSeekNotice(nextValue);
  }

  function setPlaybackStatusValue(nextValue) {
    setPlaybackStatus(nextValue);
  }

  function resetPendingPlaybackPreparation() {
    optimizedVodRequiredRef.current = false;
    playbackFlowRef.current += 1;
    attachedOptimizedManifestUrlRef.current = "";
  }

  async function startBrowserPlaybackFrom(
    startPositionSeconds,
    playbackMode = "lite",
    {
      onActivePlaybackConflict = null,
      suppressProviderAuthModal = false,
    } = {},
  ) {
    playbackFlowRef.current += 1;
    currentItemIdRef.current = itemId;
    attachedOptimizedManifestUrlRef.current = "";
    mobileAttachedEpochRef.current = null;
    browserPlayRequestedRef.current = true;
    playbackModeIntentRef.current = getPlaybackMode(playbackMode);
    setPlaybackModeIntent(playbackModeIntentRef.current);
    browserStartPositionRef.current = Math.max(0, startPositionSeconds || 0);
    resumeAppliedRef.current = false;
    pendingResumeRef.current = 0;
    requestedTargetSecondsRef.current = browserStartPositionRef.current;
    setRequestedTargetSeconds(browserStartPositionRef.current);
    mobilePlayerCanPlayRef.current = false;
    setMobilePlayerCanPlay(false);
    setPlaybackError("");
    setSeekNotice("");
    forceHlsRef.current = false;
    optimizedVodRequiredRef.current = false;
    setPlayerMeasuredDuration(0);
    setPlaybackPosition(browserStartPositionRef.current);
    setOptimizedPlaybackPending(true);
    setPlaybackStatus(`Preparing ${browserPlaybackLabel}`);
    if (
      playbackModeIntentRef.current === "full"
      && typeof Notification !== "undefined"
      && Notification.permission === "default"
    ) {
      Notification.requestPermission().catch(() => {
        // Browser notifications are optional; in-app notice remains the fallback.
      });
    }
    try {
      await startMobileOptimizedPlayback({
        autoplay: true,
        playbackMode: playbackModeIntentRef.current,
      });
    } catch (requestError) {
      clearOptimizedPlaybackPending();
      const providerAuthRequirement = getProviderAuthRequirement(requestError);
      if (providerAuthRequirement) {
        setSeekNotice("");
        setPlaybackStatus(`${browserPlaybackLabelTitle} blocked`);
        setPlaybackError("");
        if (suppressProviderAuthModal) {
          setPlaybackError(providerAuthRequirement.message || requestError.message || "Google Drive reconnect is required.");
          return false;
        }
        if (typeof onProviderAuthRequired === "function") {
          onProviderAuthRequired(providerAuthRequirement, {
            playbackMode: playbackModeIntentRef.current,
          });
        } else {
          setPlaybackError(providerAuthRequirement.message || requestError.message || "Google Drive reconnect is required.");
        }
        return false;
      }
      const playbackAdmission = getPlaybackAdmissionError(requestError);
      if (playbackAdmission) {
        setSeekNotice("");
        setPlaybackStatus(`${browserPlaybackLabelTitle} blocked`);
        setPlaybackError(playbackAdmission.message);
        return false;
      }
      const activePlaybackConflict = getActivePlaybackWorkerConflict(requestError);
      if (activePlaybackConflict && typeof onActivePlaybackConflict === "function") {
        setPlaybackError("");
        setSeekNotice("");
        setPlaybackStatus(`${browserPlaybackLabelTitle} blocked`);
        onActivePlaybackConflict(activePlaybackConflict);
        return false;
      }
      const playbackCooldown = getPlaybackWorkerCooldown(requestError);
      if (playbackCooldown) {
        setSeekNotice("");
        setPlaybackStatus(`${browserPlaybackLabelTitle} blocked`);
        setPlaybackError(playbackCooldown.message);
        return false;
      }
      setPlaybackError(requestError.message || `Failed to start ${browserPlaybackLabel}`);
      return false;
    }
    return true;
  }

  function playExistingBrowserSource() {
    const video = videoRef.current;
    if (!video) {
      return;
    }
    browserPlayRequestedRef.current = false;
    video.play().catch((requestError) => {
      setPlaybackError(requestError.message || `Failed to start ${browserPlaybackLabel}`);
    });
  }

  async function seekBrowserPlaybackTo(targetPositionSeconds, { resumeAfterReady = null } = {}) {
    const numericTarget = Number(targetPositionSeconds);
    if (!Number.isFinite(numericTarget) || numericTarget < 0) {
      return false;
    }
    const targetPosition = fullDuration > 0
      ? Math.min(fullDuration, numericTarget)
      : numericTarget;
    const video = videoRef.current;
    const activeSession = mobileSessionRef.current;
    const shouldResumeAfterReady =
      resumeAfterReady != null
        ? Boolean(resumeAfterReady)
        : Boolean(video && !video.paused);

    if (activeSession) {
      if (
        video
        && isBrowserPlaybackAbsolutePositionReady(
          activeSession,
          targetPosition,
          { headroomSeconds: SEEK_HEADROOM_SECONDS },
        )
      ) {
        let localSeekApplied = false;
        try {
          video.currentTime = resolveMediaElementPositionForAbsolute(activeSession, targetPosition);
          localSeekApplied = true;
        } catch {
          localSeekApplied = false;
        }
        if (localSeekApplied) {
          mobilePendingTargetRef.current = null;
          mobileSeekPendingRef.current = false;
          pendingSeekPhaseRef.current = "idle";
          mobileLastStablePositionRef.current = targetPosition;
          committedPlayheadSecondsRef.current = targetPosition;
          actualMediaElementTimeRef.current = targetPosition;
          requestedTargetSecondsRef.current = targetPosition;
          setCommittedPlayheadSeconds(targetPosition);
          setActualMediaElementTime(targetPosition);
          setRequestedTargetSeconds(targetPosition);
          setPlaybackPosition(targetPosition);
          clearOptimizedPlaybackPending();
          setPlaybackError("");
          setSeekNotice("");
          setPlaybackStatus(browserStreamLabelTitle);
          maybeAcknowledgeHlsAttachment({ playing: shouldResumeAfterReady, force: true });
          return true;
        }
      }

      try {
        await retargetMobileOptimizedPlayback(targetPosition, {
          resumeAfterReady: shouldResumeAfterReady,
        });
        return true;
      } catch (requestError) {
        clearOptimizedPlaybackPending();
        mobileSeekPendingRef.current = false;
        pendingSeekPhaseRef.current = "idle";
        setPendingSeekPhase("idle");
        setPlaybackError(requestError.message || "Failed to prepare the requested playback position");
        return false;
      }
    }

    if (!video) {
      return false;
    }
    try {
      video.currentTime = resolveMediaElementPositionForAbsolute(null, targetPosition);
      setPlaybackPosition(targetPosition);
      return true;
    } catch (requestError) {
      setPlaybackError(requestError.message || "Failed to seek playback");
      return false;
    }
  }

  async function stopCurrentBrowserPlaybackSession() {
    const activeSession = mobileSessionRef.current;
    playbackFlowRef.current += 1;
    currentItemIdRef.current = itemId;
    clearOptimizedPlaybackPending();
    setPlaybackError("");
    setSeekNotice("");
    setPlaybackStatus(`${browserPlaybackLabelTitle} stopped`);
    clearPlaybackResources();
    resetMobilePlaybackState({ clearPlayer: true });
    if (!activeSession?.session_id) {
      return;
    }
    try {
      await stopBrowserPlaybackSession({
        stopUrl: activeSession.stop_url,
        browserPlaybackSessionRoot,
        sessionId: activeSession.session_id,
      });
    } catch (requestError) {
      const normalizedMessage = String(requestError?.message || "").toLowerCase();
      const alreadyStopped = Boolean(
        requestError?.status === 404
        || normalizedMessage.includes("session not found")
        || normalizedMessage.includes("not found")
        || normalizedMessage.includes("already stopped")
      );
      if (alreadyStopped) {
        setPlaybackError("");
        setSeekNotice("");
        setPlaybackStatus(`${browserPlaybackLabelTitle} stopped`);
        return;
      }
      setPlaybackError(requestError.message || `Failed to stop ${browserPlaybackLabelTitle}`);
    }
  }

  useEffect(() => {
    if (!hlsRef.current || !mobileSession) {
      return;
    }
    const nextConfig = retuneHlsInstance(hlsRef.current, {
      session: mobileSession,
      deviceClass: browserPlaybackDeviceClass,
    });
    if (!nextConfig) {
      return;
    }
    setHlsEngineDiagnostics((current) => (
      current.selectedEngine === "hls.js"
        ? {
          ...current,
          hlsJsConfig: compactHlsConfig(hlsRef.current.config),
          bufferTier: nextConfig.bufferTier,
          targetForwardBufferSeconds: nextConfig.maxBufferLength,
          backBufferSeconds: nextConfig.backBufferLength,
          maxBufferSizeBytes: nextConfig.maxBufferSize,
          bufferPolicySource: nextConfig.policySource,
        }
        : current
    ));
  }, [
    browserPlaybackDeviceClass,
    mobileSession?.buffer_tier,
    mobileSession?.playback_mode,
    mobileSession?.server_required_runway_seconds,
    mobileSession?.server_reserve_seconds,
    mobileSession?.client_recommended_forward_buffer_seconds,
    mobileSession?.full_bad_condition_detected,
  ]);

  useEffect(() => {
    const session = mobileSession;
    const preparingSession = Boolean(
      session
      && (
        optimizedPlaybackPending
        || !session.attach_ready
        || !streamSource
        || !mobilePlayerCanPlay
      )
    );
    if (!preparingSession && !optimizedPlaybackPending) {
      return;
    }
    const eventReason =
      session?.gate_reason
      || session?.lite_undersupply_reason
      || (!session?.attach_ready ? "attach_not_ready" : "")
      || (!streamSource ? "stream_source_not_attached" : "")
      || "browser_playback_preparing";
    logBrowserPlaybackDiagnosticEvent("elvern:browser_playback_prepare_gate", {
      eventReason,
      releaseGateReason: eventReason,
      session,
    });
  }, [
    mobilePlayerCanPlay,
    mobileSession,
    optimizedPlaybackPending,
    streamSource,
  ]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !streamSource) {
      setHlsEngineDiagnostics((current) => (
        current.selectedEngine === "none"
          ? current
          : {
            selectedEngine: "none",
            nativeHlsSelected: false,
            hlsJsSelected: false,
            hlsJsAttachedToVideo: false,
            hlsJsConfig: null,
            bufferTier: null,
            bufferPolicySource: null,
          }
      ));
      return undefined;
    }

    clearPlayerBinding();
    video.pause();
    video.removeAttribute("src");
    video.load();
    browserPlaybackActiveRef.current = false;

    function handlePlaybackFailure() {
      if (mobileSessionRef.current) {
        if (mobileSeekPendingRef.current) {
          setPlaybackError("");
          return;
        }
        if (isHlsSessionPayload(mobileSessionRef.current)) {
          setPlaybackError("");
          setOptimizedPlaybackPending(true);
          setSeekNotice(`Reattaching the current ${browserPlaybackLabel} session.`);
          applyMobileLifecycleStatus("recovering");
          logAutomaticRecoveryTarget("media-error");
          recoverMobilePlaybackAfterResume("media-error").catch((requestError) => {
            clearOptimizedPlaybackPending();
            setPlaybackError(requestError.message || `${browserPlaybackLabelTitle} failed for this playback session`);
          });
          return;
        }
        clearOptimizedPlaybackPending();
        setPlaybackError(`${browserPlaybackLabelTitle} failed for this playback session`);
        return;
      }
      const currentPlayback = playbackStateRef.current;
      if (streamSource.mode !== "direct") {
        const optimizedStreamStillPreparing =
          currentPlayback?.mode === "hls"
          && !currentPlayback?.manifest_complete
          && currentPlayback?.transcode_status !== "failed"
          && currentPlayback?.transcode_status !== "disabled";
        if (optimizedStreamStillPreparing) {
          browserPlaybackActiveRef.current = false;
          browserPlayRequestedRef.current = true;
          attachedOptimizedManifestUrlRef.current = "";
          setOptimizedPlaybackPending(true);
          clearPlayerBinding();
          video.pause();
          video.removeAttribute("src");
          video.load();
          setStreamSource(null);
          setPlaybackError("");
          setPlaybackStatus(`Preparing ${browserPlaybackLabel}`);
          setSeekNotice(
            `${browserPlaybackLabelTitle} is still preparing. Elvern will retry automatically when more video is ready.`,
          );
          startPlaybackPolling(forceHlsRef.current || currentPlayback?.mode === "hls", playbackFlowRef.current);
          return;
        }
        browserPlayRequestedRef.current = false;
        setPlaybackError("Playback failed for the optimized stream");
        return;
      }
      browserPlayRequestedRef.current = false;
      if (fallbackAttemptedRef.current) {
        setPlaybackError("Direct playback failed and fallback could not recover");
        return;
      }
      fallbackAttemptedRef.current = true;
      prepareHlsPlayback(true).catch((requestError) => {
        setPlaybackError(requestError.message || "Failed to fall back to HLS playback");
      });
    }

    function maybeAutoplay() {
      if (!browserPlayRequestedRef.current) {
        return;
      }
      if (
        mobileSessionRef.current
        && !evaluateMobileClientReleaseGate(mobileSessionRef.current, video).ready
      ) {
        return;
      }
      browserPlayRequestedRef.current = false;
      video.play().catch((requestError) => {
        const message = requestError?.message || "";
        const normalized = message.toLowerCase();
        const looksLikeGestureLoss =
          iosMobile
          && optimizedVodRequiredRef.current
          && (
            normalized.includes("gesture")
            || normalized.includes("notallowed")
            || normalized.includes("denied")
            || normalized.includes("not allowed")
          );
        if (looksLikeGestureLoss) {
          clearOptimizedPlaybackPending();
          setPlaybackError("");
          setPlaybackStatus(browserReadyLabelTitle);
          setSeekNotice(`Tap play in the video controls to start ${browserPlaybackLabel}.`);
          return;
        }
        setPlaybackError(requestError.message || `Failed to start ${browserPlaybackLabel}`);
      });
    }

    video.addEventListener("error", handlePlaybackFailure);

    if (streamSource.mode === "direct") {
      setHlsEngineDiagnostics({
        selectedEngine: "direct",
        nativeHlsSelected: false,
        hlsJsSelected: false,
        hlsJsAttachedToVideo: false,
        hlsJsConfig: null,
        bufferTier: null,
        bufferPolicySource: null,
        ...readHlsSupportDiagnostics(video),
      });
      video.addEventListener("loadedmetadata", maybeAutoplay, { once: true });
      video.src = streamSource.url;
      video.load();
      return () => {
        video.removeEventListener("error", handlePlaybackFailure);
        video.removeEventListener("loadedmetadata", maybeAutoplay);
      };
    }

    const useManualMobileAutoplay = iosMobile && Boolean(mobileSessionRef.current);
    const hlsSupportDiagnostics = readHlsSupportDiagnostics(video);
    const selectedHlsEngine = resolveBrowserHlsEngine({
      deviceClass: browserPlaybackDeviceClass,
      hlsJsSupported: hlsSupportDiagnostics.hlsJsSupported,
      iosMobile,
      nativeHlsSupport: hlsSupportDiagnostics.nativeHlsSupport,
    });

    if (selectedHlsEngine === "native_hls") {
      const targets = deriveBufferTargetsFromSession(mobileSessionRef.current || {}, browserPlaybackDeviceClass);
      setHlsEngineDiagnostics({
        selectedEngine: "native_hls",
        nativeHlsSelected: true,
        hlsJsSelected: false,
        hlsJsAttachedToVideo: false,
        hlsJsConfig: null,
        bufferTier: targets.bufferTier,
        targetForwardBufferSeconds: targets.forwardBufferSeconds,
        backBufferSeconds: targets.backBufferSeconds,
        maxBufferSizeBytes: targets.maxBufferSizeBytes,
        bufferPolicySource: targets.policySource,
        ...hlsSupportDiagnostics,
      });
      if (!useManualMobileAutoplay) {
        video.addEventListener("loadedmetadata", maybeAutoplay, { once: true });
      }
      video.src = streamSource.url;
      video.load();
      return () => {
        video.removeEventListener("error", handlePlaybackFailure);
        if (!useManualMobileAutoplay) {
          video.removeEventListener("loadedmetadata", maybeAutoplay);
        }
      };
    }

    if (selectedHlsEngine === "unsupported_hls") {
      setHlsEngineDiagnostics({
        selectedEngine: "unsupported_hls",
        nativeHlsSelected: false,
        hlsJsSelected: false,
        hlsJsAttachedToVideo: false,
        hlsJsConfig: null,
        bufferTier: null,
        bufferPolicySource: null,
        ...hlsSupportDiagnostics,
      });
      setPlaybackError("This browser cannot play HLS fallback streams");
      return () => {
        video.removeEventListener("error", handlePlaybackFailure);
      };
    }

    const initialHlsConfig = buildHlsConfig({
      session: mobileSessionRef.current || {},
      deviceClass: browserPlaybackDeviceClass,
    });
    const {
      bufferTier: _bufferTier,
      policySource: _policySource,
      ...hlsConstructorConfig
    } = initialHlsConfig;
    const hls = new Hls(hlsConstructorConfig);
    hls.config.bufferTier = initialHlsConfig.bufferTier;
    hls.config.policySource = initialHlsConfig.policySource;
    hlsRef.current = hls;
    hls.loadSource(streamSource.url);
    hls.attachMedia(video);
    setHlsEngineDiagnostics({
      selectedEngine: "hls.js",
      nativeHlsSelected: false,
      hlsJsSelected: true,
      hlsJsAttachedToVideo: true,
      hlsJsConfig: compactHlsConfig(hls.config),
      bufferTier: initialHlsConfig.bufferTier,
      targetForwardBufferSeconds: initialHlsConfig.maxBufferLength,
      backBufferSeconds: initialHlsConfig.backBufferLength,
      maxBufferSizeBytes: initialHlsConfig.maxBufferSize,
      bufferPolicySource: initialHlsConfig.policySource,
      ...hlsSupportDiagnostics,
    });
    if (!useManualMobileAutoplay) {
      hls.on(Hls.Events.MANIFEST_PARSED, maybeAutoplay);
    }
    hls.on(Hls.Events.ERROR, (_event, data) => {
      if (data.fatal) {
        if (isHlsSessionPayload(mobileSessionRef.current)) {
          setPlaybackError("");
          setOptimizedPlaybackPending(true);
          setSeekNotice(`Reattaching the current ${browserPlaybackLabel} session.`);
          applyMobileLifecycleStatus("recovering");
          logAutomaticRecoveryTarget("hls-fatal");
          recoverMobilePlaybackAfterResume("hls-fatal").catch((requestError) => {
            clearOptimizedPlaybackPending();
            setPlaybackError(requestError.message || data.details || "HLS playback failed");
          });
          return;
        }
        setPlaybackError(data.details || "HLS playback failed");
      }
    });

    return () => {
      video.removeEventListener("error", handlePlaybackFailure);
      if (!useManualMobileAutoplay) {
        hls.off(Hls.Events.MANIFEST_PARSED, maybeAutoplay);
      }
      hls.destroy();
      if (hlsRef.current === hls) {
        hlsRef.current = null;
      }
    };
  }, [streamSource]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !item) {
      return undefined;
    }
    playbackOpenedReportedRef.current = false;

    function updatePlayerMetrics() {
      const absoluteTime = resolveCurrentVideoAbsolutePosition(mobileSessionRef.current, video);
      actualMediaElementTimeRef.current = absoluteTime;
      setActualMediaElementTime(absoluteTime);
      const displayTime =
        mobileSessionRef.current
        && pendingSeekPhaseRef.current !== "idle"
        && requestedTargetSecondsRef.current != null
          ? requestedTargetSecondsRef.current
          : absoluteTime;
      setPlaybackPosition(displayTime);
      const currentPlayback = playbackStateRef.current;
      const measuredDuration = readFiniteDuration(video);
      const shouldIgnoreMeasuredDuration =
        currentPlayback?.mode === "hls"
        && (!currentPlayback?.manifest_complete || !currentPlayback?.expected_duration_seconds);
      setPlayerMeasuredDuration(shouldIgnoreMeasuredDuration ? 0 : measuredDuration);
    }

    async function pushProgress(completed = false) {
      const persistedDuration = fullDuration > 0
        ? fullDuration
        : readFiniteDuration(video);
      const absolutePositionSeconds = resolveCurrentVideoAbsolutePosition(mobileSessionRef.current, video);
      if (!persistedDuration && absolutePositionSeconds <= 0) {
        return;
      }
      const playbackMode =
        iosMobile && mobileSessionRef.current
          ? "experimental_playback"
          : "browser_playback";
      const payload = await savePlaybackProgress({
        itemId: item.id,
        positionSeconds: absolutePositionSeconds,
        durationSeconds: persistedDuration || null,
        completed,
        playbackMode,
      });
      onProgressChange(payload);
    }

    function flushProgress(completed = false) {
      pushProgress(completed).catch((requestError) => {
        console.error("Failed to persist progress", requestError);
      });
    }

    function beaconProgress(completed = false) {
      const persistedDuration = fullDuration > 0
        ? fullDuration
        : readFiniteDuration(video);
      const absolutePositionSeconds = resolveCurrentVideoAbsolutePosition(mobileSessionRef.current, video);
      if (!navigator.sendBeacon || (!persistedDuration && absolutePositionSeconds <= 0)) {
        flushProgress(completed);
        return;
      }
      const playbackMode =
        iosMobile && mobileSessionRef.current
          ? "experimental_playback"
          : "browser_playback";
      const body = JSON.stringify({
        position_seconds: absolutePositionSeconds,
        duration_seconds: persistedDuration || null,
        completed,
        playback_mode: playbackMode,
      });
      onProgressDirty?.({
        media_item_id: item.id,
        position_seconds: absolutePositionSeconds,
        duration_seconds: persistedDuration || null,
        completed,
      });
      navigator.sendBeacon(
        `/api/progress/${item.id}`,
        new Blob([body], { type: "application/json" }),
      );
    }

    function resolvePlaybackTrackingMode() {
      return iosMobile && mobileSessionRef.current
        ? "experimental_playback"
        : "browser_playback";
    }

    async function reportPlaybackEvent(eventType) {
      const persistedDuration = fullDuration > 0
        ? fullDuration
        : readFiniteDuration(video);
      const absolutePositionSeconds = resolveCurrentVideoAbsolutePosition(mobileSessionRef.current, video);
      const payload = await recordPlaybackEvent({
        itemId: item.id,
        eventType,
        playbackMode: resolvePlaybackTrackingMode(),
        positionSeconds: absolutePositionSeconds,
        durationSeconds: persistedDuration || null,
      });
      onProgressChange(payload);
    }

    function startProgressTimer() {
      window.clearInterval(progressTimerRef.current);
      progressTimerRef.current = window.setInterval(() => {
        flushProgress(false);
      }, 5000);
    }

    function stopProgressTimer() {
      window.clearInterval(progressTimerRef.current);
      progressTimerRef.current = null;
    }

    function applyResumePosition() {
      if (resumeAppliedRef.current) {
        return;
      }
      if (!streamSource || video.readyState < 1) {
        return;
      }
      const safeResume = mobileSessionRef.current
        ? Math.max(
            0,
            requestedTargetSecondsRef.current != null
              ? requestedTargetSecondsRef.current
              : mobilePendingTargetRef.current != null
                ? mobilePendingTargetRef.current
                : mobileSessionRef.current.target_position_seconds || browserStartPositionRef.current || 0,
          )
        : resumableStartPosition;
      if (safeResume <= 0) {
        resumeAppliedRef.current = true;
        pendingResumeRef.current = 0;
        return;
      }
      if (
        playback?.mode === "hls"
        && !playback?.manifest_complete
        && safeResume > availableDuration - SEEK_HEADROOM_SECONDS
      ) {
        pendingResumeRef.current = safeResume;
        setSeekNotice(`Resuming at ${formatDuration(safeResume)} once that part is prepared.`);
        return;
      }
      video.currentTime = resolveMediaElementPositionForAbsolute(mobileSessionRef.current, safeResume);
      setPlaybackPosition(safeResume);
      pendingResumeRef.current = 0;
      resumeAppliedRef.current = true;
      setSeekNotice((current) => (
        current.startsWith("Resuming at ") ? "" : current
      ));
    }

    function clearMobileStallRecoveryTimer() {
      window.clearTimeout(mobileStallTimerRef.current);
      mobileStallTimerRef.current = null;
      mobileStallStartedAtRef.current = 0;
    }

    function bufferedRunwaySeconds() {
      return readClientBufferedAheadSeconds(video);
    }

    function shouldHoldRetargetFrozenFrameForClientBuffer(targetAbsoluteSeconds) {
      if (!iosMobile || !mobileRetargetTransitionRef.current) {
        return false;
      }
      const currentSession = mobileSessionRef.current;
      if (!currentSession) {
        return false;
      }
      const target = Number(targetAbsoluteSeconds);
      if (!Number.isFinite(target) || target < 0) {
        return true;
      }
      if (!shouldReleaseRetargetFrozenFrame({
        requiredClientBufferSeconds: RETARGET_CLIENT_BUFFER_RELEASE_SECONDS,
        sessionPayload: currentSession,
        targetAbsoluteSeconds: target,
        videoElement: video,
      })) {
        pendingSeekPhaseRef.current = "target_attached_waiting_client_buffer";
        setPendingSeekPhase("target_attached_waiting_client_buffer");
        setSeekNotice(`Preparing ${formatDuration(target)}...`);
        return true;
      }
      return false;
    }

    function clearFirstFrameStallMonitor() {
      window.clearTimeout(firstFrameStallTimerRef.current);
      firstFrameStallTimerRef.current = null;
      firstFrameStallBaselineRef.current = null;
    }

    function resolveFirstFrameAttachmentKey() {
      const currentSession = mobileSessionRef.current;
      if (!currentSession?.session_id) {
        return "";
      }
      return [
        currentSession.session_id,
        currentSession.attach_revision || currentSession.manifest_revision || 0,
        streamSource?.url || "",
      ].join(":");
    }

    function syncFirstFrameScopeForCurrentAttachment() {
      const nextKey = resolveFirstFrameAttachmentKey();
      if (!nextKey) {
        return false;
      }
      if (firstFrameAttachmentKeyRef.current !== nextKey) {
        clearFirstFrameStallMonitor();
        firstFrameAttachmentKeyRef.current = nextKey;
        firstFrameAttachmentStartPositionRef.current = resolveCurrentVideoAbsolutePosition(mobileSessionRef.current, video);
        firstFrameStallDisarmedRef.current = false;
        firstFrameSuccessfulTimeupdateCountRef.current = 0;
        firstFramePlaybackAdvancingSinceRef.current = 0;
      }
      return true;
    }

    function disarmFirstFrameStallMonitor() {
      firstFrameStallDisarmedRef.current = true;
      clearFirstFrameStallMonitor();
    }

    function noteFirstFramePlaybackProgress(absolutePositionSeconds) {
      if (!syncFirstFrameScopeForCurrentAttachment() || firstFrameStallDisarmedRef.current) {
        return;
      }
      const startPosition = firstFrameAttachmentStartPositionRef.current;
      if (absolutePositionSeconds > startPosition + 0.25) {
        firstFrameSuccessfulTimeupdateCountRef.current += 1;
        if (!firstFramePlaybackAdvancingSinceRef.current) {
          firstFramePlaybackAdvancingSinceRef.current = Date.now();
        }
      }
      const advancingDurationMs = firstFramePlaybackAdvancingSinceRef.current
        ? Date.now() - firstFramePlaybackAdvancingSinceRef.current
        : 0;
      if (shouldDisarmFirstFrameStallMonitor({
        attachmentStartSeconds: startPosition,
        currentAbsolutePositionSeconds: absolutePositionSeconds,
        successfulTimeupdateCount: firstFrameSuccessfulTimeupdateCountRef.current,
        advancingDurationMs,
      })) {
        disarmFirstFrameStallMonitor();
      }
    }

    function sampleNativeClientPlayback() {
      const previous = nativeLivenessSampleRef.current;
      const sample = readClientPlaybackLiveness(video, previous);
      nativeLivenessSampleRef.current = sample;
      const currentSession = mobileSessionRef.current;
      const targets = deriveBufferTargetsFromSession(currentSession || {}, browserPlaybackDeviceClass);
      const stall = classifyPlaybackStall({
        session: currentSession || {},
        livenessSample: sample,
        targetForwardBufferSeconds: targets.forwardBufferSeconds,
        firstFrameEligible: !firstFrameStallDisarmedRef.current,
      });
      setHlsEngineDiagnostics((current) => (
        current.selectedEngine === "native_hls"
          ? {
            ...current,
            bufferTier: targets.bufferTier,
            targetForwardBufferSeconds: targets.forwardBufferSeconds,
            backBufferSeconds: targets.backBufferSeconds,
            maxBufferSizeBytes: targets.maxBufferSizeBytes,
            bufferPolicySource: targets.policySource,
            clientBufferedAheadSeconds: sample.bufferedAheadSeconds,
            clientBufferSatisfiesTarget: stall.clientBufferSatisfiesTarget,
            clientTimeAdvancing: sample.timeAdvancing,
            clientReadyState: sample.readyState,
            clientNetworkState: sample.networkState,
            clientPlaybackStallReason: stall.stallReason || "",
            backendPreparedAheadSeconds: stall.backendPreparedAheadSeconds,
          }
          : current
      ));
      return { sample, stall, targets };
    }

    function beginClientStallRecovery(stallReason) {
      const currentSession = mobileSessionRef.current;
      if (!currentSession?.session_id || mobileRecoveryInFlightRef.current) {
        return;
      }
      const recoveryKey = `${currentSession.session_id}:${currentSession.manifest_revision || currentSession.attach_revision || 0}`;
      const attempts = firstFrameRecoveryAttemptsRef.current.get(recoveryKey) || 0;
      if (attempts >= 2) {
        setOptimizedPlaybackPending(false);
        setSeekNotice("");
        setPlaybackStatus(`${browserPlaybackLabelTitle} buffering`);
        setPlaybackError("Network is too weak to keep the browser buffer filled.");
        return;
      }
      firstFrameRecoveryAttemptsRef.current.set(recoveryKey, attempts + 1);
      clearFirstFrameStallMonitor();
      clearMobileStallRecoveryTimer();
      mobileWasPlayingBeforeSuspendRef.current = Boolean(!video.paused);
      try {
        video.pause();
      } catch {
        // Safari can refuse a programmatic pause during teardown.
      }
      setOptimizedPlaybackPending(true);
      setPlaybackError("");
      setSeekNotice(`Rebuffering ${browserPlaybackLabel} from the current position.`);
      applyMobileLifecycleStatus("recovering");
      postMobileRuntimeHeartbeat({
        lifecycleState: "recovering",
        stalled: true,
        playing: true,
        force: true,
        clientPlaybackStallReason: stallReason,
      }).catch(() => {
        // Recovery still reattaches locally if this diagnostic heartbeat misses.
      });
      logAutomaticRecoveryTarget("first-frame-stall");
      recoverMobilePlaybackAfterResume("first-frame-stall").catch((requestError) => {
        clearOptimizedPlaybackPending();
        setPlaybackError(requestError.message || "Network is too weak to keep the browser buffer filled.");
      });
    }

    function armFirstFrameStallMonitor() {
      if (
        !iosMobile
        || !mobileSessionRef.current
        || !streamSource
        || video.paused
        || mobileRecoveryInFlightRef.current
      ) {
        return;
      }
      if (!syncFirstFrameScopeForCurrentAttachment() || firstFrameStallDisarmedRef.current) {
        return;
      }
      if (firstFrameStallTimerRef.current) {
        return;
      }
      firstFrameStallBaselineRef.current = readClientPlaybackLiveness(video, null);
      firstFrameStallTimerRef.current = window.setTimeout(() => {
        firstFrameStallTimerRef.current = null;
        if (
          !mobileSessionRef.current
          || video.paused
          || mobileRecoveryInFlightRef.current
        ) {
          return;
        }
        const baseline = firstFrameStallBaselineRef.current;
        const latest = readClientPlaybackLiveness(video, baseline);
        nativeLivenessSampleRef.current = latest;
        const targets = deriveBufferTargetsFromSession(mobileSessionRef.current, browserPlaybackDeviceClass);
        const stall = classifyPlaybackStall({
          session: mobileSessionRef.current,
          livenessSample: latest,
          targetForwardBufferSeconds: targets.forwardBufferSeconds,
          firstFrameEligible: !firstFrameStallDisarmedRef.current,
        });
        if (!stall.firstFrameStall) {
          return;
        }
        beginClientStallRecovery(stall.stallReason || "first_frame_stall");
      }, 4000);
    }

    function handlePageHide() {
      beaconProgress(false);
      if (!iosMobile || !mobileSessionRef.current) {
        return;
      }
      mobileWasBackgroundedRef.current = true;
      mobileBackgroundHiddenAtRef.current = Date.now();
      mobileWasPlayingBeforeSuspendRef.current = Boolean(!video.paused && !video.ended);
      applyMobileLifecycleStatus("background-suspended");
      postMobileRuntimeHeartbeat({
        lifecycleState: "background-suspended",
        stalled: false,
        playing: false,
        useBeacon: true,
      });
    }

    function handlePause() {
      stopProgressTimer();
      const shouldRecordStop =
        resolveCurrentVideoAbsolutePosition(mobileSessionRef.current, video) > 0.5 &&
        (!iosMobile
          || !mobileSessionRef.current
          || (
            mobilePlayerCanPlayRef.current
            && !mobileSeekPendingRef.current
            && !mobileWarmupProbeActiveRef.current
          ));
      if (shouldRecordStop) {
        reportPlaybackEvent("playback_stopped").catch((requestError) => {
          console.error("Failed to record playback stop", requestError);
          flushProgress(false);
        });
      } else {
        flushProgress(false);
      }
      clearMobileStallRecoveryTimer();
      clearFirstFrameStallMonitor();
      if (mobileSessionRef.current) {
        postMobileRuntimeHeartbeat({
          lifecycleState:
            mobileLifecycleStateRef.current === "background-suspended"
              ? "background-suspended"
              : "attached",
          stalled: false,
          playing: false,
        }).catch(() => {
          // Ignore transient heartbeat failures during pause transitions.
        });
      }
    }

    function handleEnded() {
      if (mobileSessionRef.current) {
        const absoluteCurrentTime = resolveCurrentVideoAbsolutePosition(mobileSessionRef.current, video);
        const manifestState = classifyManifestWindowState({
          absolutePositionSeconds: absoluteCurrentTime,
          manifestEndSeconds: getBrowserPlaybackAttachedManifestEndSeconds(mobileSessionRef.current),
          fullDurationSeconds: fullDuration,
          completionGraceSeconds: COMPLETION_GRACE_SECONDS,
        });
        if (manifestState.manifestWindowExhausted) {
          mobileWasPlayingBeforeSuspendRef.current = true;
          setOptimizedPlaybackPending(true);
          setSeekNotice(`Reattaching ${browserPlaybackLabel} at ${formatDuration(absoluteCurrentTime)}.`);
          applyMobileLifecycleStatus("recovering");
          postMobileRuntimeHeartbeat({
            lifecycleState: "recovering",
            stalled: true,
            playing: true,
            force: true,
            clientPlaybackStallReason: "manifest_window_exhausted",
          }).catch(() => {
            // Recovery can still reattach locally if the diagnostic heartbeat misses.
          });
          logAutomaticRecoveryTarget("manifest-window-exhausted");
          recoverMobilePlaybackAfterResume("manifest-window-exhausted").catch((requestError) => {
            clearOptimizedPlaybackPending();
            setPlaybackError(requestError.message || `Failed to refresh ${browserPlaybackLabel}`);
          });
          return;
        }
      }
      stopProgressTimer();
      reportPlaybackEvent("playback_completed").catch((requestError) => {
        console.error("Failed to record playback completion", requestError);
        flushProgress(true);
      });
      clearMobileStallRecoveryTimer();
      clearFirstFrameStallMonitor();
    }

    function handleVisibilityChange() {
      if (document.visibilityState === "hidden") {
        beaconProgress(false);
        if (iosMobile && mobileSessionRef.current) {
          mobileWasBackgroundedRef.current = true;
          mobileBackgroundHiddenAtRef.current = Date.now();
          mobileWasPlayingBeforeSuspendRef.current = Boolean(!video.paused && !video.ended);
          applyMobileLifecycleStatus("background-suspended");
          postMobileRuntimeHeartbeat({
            lifecycleState: "background-suspended",
            stalled: false,
            playing: false,
            useBeacon: true,
          });
        }
        return;
      }
      if (iosMobile && mobileSessionRef.current && mobileWasBackgroundedRef.current) {
        softResumeMobilePlaybackAfterBackground("visibilitychange");
      }
    }

    function handlePageShow() {
      if (iosMobile && mobileSessionRef.current && mobileWasBackgroundedRef.current) {
        softResumeMobilePlaybackAfterBackground("pageshow");
      }
    }

    function handleWindowFocus() {
      if (iosMobile && mobileSessionRef.current && mobileWasBackgroundedRef.current) {
        softResumeMobilePlaybackAfterBackground("focus");
      }
    }

    function handlePlaybackStalled() {
      if (
        !iosMobile
        || !mobileSessionRef.current
        || mobileSeekPendingRef.current
        || !mobilePlayerCanPlayRef.current
        || video.paused
        || mobileRecoveryInFlightRef.current
      ) {
        return;
      }
      const currentSession = mobileSessionRef.current;
      const { sample, stall } = sampleNativeClientPlayback();
      const bufferedAhead = bufferedRunwaySeconds();
      const backendAhead = currentSession?.ahead_runway_seconds || 0;
      const refillInProgress = Boolean(currentSession?.refill_in_progress);
      const staleNativePlaylistStall = shouldRecoverNativeHlsStalePlaylist({
        hlsJsAttached: Boolean(hlsRef.current),
        backendPreparedAheadSeconds: backendAhead,
        stallReason: stall.stallReason,
      });
      const recoveryCandidate =
        currentSession?.stalled_recovery_needed
        || currentSession?.starvation_risk
        || stall.stallReason === "client_buffer_starved"
        || stall.firstFrameStall
        || stall.midPlaybackStall
        || staleNativePlaylistStall
        || (backendAhead <= 3 && bufferedAhead <= 0.75 && !refillInProgress);
      const recoveryDecision = shouldStartVisibleHlsSupplyRecovery({
        session: {
          ...currentSession,
          stalled_recovery_needed: Boolean(recoveryCandidate),
        },
        livenessSample: {
          ...sample,
          stallReason: stall.stallReason,
        },
        seekPending: mobileSeekPendingRef.current,
        recoveryInFlight: mobileRecoveryInFlightRef.current,
        lifecycleState: mobileLifecycleStateRef.current,
        mobilePlayerCanPlay: mobilePlayerCanPlayRef.current,
        videoPaused: video.paused,
        hlsJsAttached: Boolean(hlsRef.current),
        stalePlaylistStall: staleNativePlaylistStall,
      });
      if (recoveryCandidate) {
        const stallReason = staleNativePlaylistStall
          ? "native_hls_playlist_stale"
          : (stall.stallReason || "backend_or_client_runway_low");
        logBrowserPlaybackDiagnosticEvent("elvern:browser_playback_stall_snapshot", {
          clientPlaybackStallReason: stallReason,
          eventReason: stallReason,
          livenessSample: {
            ...sample,
            stallReason,
          },
          recoveryDecision,
          recoveryTarget: buildAutomaticRecoveryTargetDiagnostic(currentSession, stallReason, video),
          session: currentSession,
          staleNativePlaylistStall,
          video,
        });
      }
      if (!recoveryDecision.start) {
        clearMobileStallRecoveryTimer();
        return;
      }
      if (mobileStallTimerRef.current) {
        return;
      }
      mobileStallStartedAtRef.current = Date.now();
      mobileStallTimerRef.current = window.setTimeout(() => {
        mobileStallTimerRef.current = null;
        if (
          !mobileSessionRef.current
          || mobileSeekPendingRef.current
          || video.paused
          || mobileRecoveryInFlightRef.current
        ) {
          return;
        }
        const latestSession = mobileSessionRef.current;
        const { sample: latestSample, stall: latestStall } = sampleNativeClientPlayback();
        const latestBackendAhead = latestSession?.ahead_runway_seconds || 0;
        const latestBufferedAhead = bufferedRunwaySeconds();
        const latestRefillInProgress = Boolean(latestSession?.refill_in_progress);
        const staleNativePlaylistStall = shouldRecoverNativeHlsStalePlaylist({
          hlsJsAttached: Boolean(hlsRef.current),
          backendPreparedAheadSeconds: latestBackendAhead,
          stallReason: latestStall.stallReason,
        });
        const latestRecoveryCandidate =
          latestSession?.stalled_recovery_needed
          || latestSession?.starvation_risk
          || latestStall.stallReason === "client_buffer_starved"
          || latestStall.firstFrameStall
          || latestStall.midPlaybackStall
          || staleNativePlaylistStall
          || (latestBackendAhead <= 3 && latestBufferedAhead <= 0.75 && !latestRefillInProgress);
        const latestRecoveryDecision = shouldStartVisibleHlsSupplyRecovery({
          session: {
            ...latestSession,
            stalled_recovery_needed: Boolean(latestRecoveryCandidate),
          },
          livenessSample: {
            ...latestSample,
            stallReason: latestStall.stallReason,
          },
          seekPending: mobileSeekPendingRef.current,
          recoveryInFlight: mobileRecoveryInFlightRef.current,
          lifecycleState: mobileLifecycleStateRef.current,
          mobilePlayerCanPlay: mobilePlayerCanPlayRef.current,
          videoPaused: video.paused,
          hlsJsAttached: Boolean(hlsRef.current),
          stalePlaylistStall: staleNativePlaylistStall,
        });
        const latestStallReason = staleNativePlaylistStall
          ? "native_hls_playlist_stale"
          : (latestStall.stallReason || "backend_or_client_runway_low");
        const latestRecoveryTarget = buildAutomaticRecoveryTargetDiagnostic(
          latestSession,
          latestStallReason,
          video,
        );
        if (latestRecoveryCandidate) {
          logBrowserPlaybackDiagnosticEvent("elvern:browser_playback_stall_snapshot", {
            clientPlaybackStallReason: latestStallReason,
            eventReason: latestStallReason,
            livenessSample: {
              ...latestSample,
              stallReason: latestStallReason,
            },
            recoveryDecision: latestRecoveryDecision,
            recoveryTarget: latestRecoveryTarget,
            session: latestSession,
            staleNativePlaylistStall,
            video,
          });
        }
        if (!latestRecoveryDecision.start) {
          return;
        }
        setOptimizedPlaybackPending(true);
        setSeekNotice(`Reconnecting the current ${browserPlaybackLabel} session.`);
        applyMobileLifecycleStatus("recovering");
        mobileWasPlayingBeforeSuspendRef.current = Boolean(!video.paused);
        setHlsEngineDiagnostics((current) => (
          current.selectedEngine === "native_hls"
            ? {
              ...current,
              nativeHlsStallRecoveryReason: staleNativePlaylistStall
                ? "native_hls_playlist_stale"
                : (latestStall.stallReason || latestRecoveryDecision.reason || "client_stalled"),
              nativeHlsStallRecoveryPreservedPositionSeconds: resolveCurrentVideoAbsolutePosition(latestSession, video),
              nativeHlsStallRecoveryBackrollSeconds: latestRecoveryTarget.backrollSeconds,
              nativeHlsStallRecoveryTargetSeconds: latestRecoveryTarget.targetAfterBackrollSeconds,
              nativeHlsStallRecoveryAvoidedForwardSkip: latestRecoveryTarget.avoidedForwardSkip,
            }
            : current
        ));
        postMobileRuntimeHeartbeat({
          lifecycleState: "recovering",
          stalled: true,
          playing: true,
          force: true,
          clientPlaybackStallReason: staleNativePlaylistStall
            ? "native_hls_playlist_stale"
            : (latestStall.stallReason || latestRecoveryDecision.reason || "client_stalled"),
        }).catch(() => {
          // Recovery will still try to reattach locally.
        });
        recoverMobilePlaybackAfterResume("stalled");
      }, 2200);
    }

    function handleLoadedMetadata() {
      updatePlayerMetrics();
      maybeAcknowledgeHlsAttachment({ playing: !video.paused, force: true, loadedEventName: "loadedmetadata" });
      if (mobilePendingTargetRef.current != null && mobileSessionRef.current) {
        const pendingTarget = mobilePendingTargetRef.current;
        video.currentTime = resolveMediaElementPositionForAbsolute(mobileSessionRef.current, pendingTarget);
        setPlaybackPosition(pendingTarget);
        actualMediaElementTimeRef.current = pendingTarget;
        setActualMediaElementTime(pendingTarget);
        mobileAwaitingTargetSeekRef.current = resolveMediaElementPositionForAbsolute(
          mobileSessionRef.current,
          pendingTarget,
        ) > 0.5;
      }
      applyResumePosition();
      maybeProbeMobileFirstFrame();
      maybeFinalizeMobilePlayerReadiness();
      finalizeNonIosClientReadiness();
    }

    function maybeProbeMobileFirstFrame() {
      if (!iosMobile || !mobileSessionRef.current || !streamSource) {
        return;
      }
      if (!mobileCanPlaySeenRef.current || !mobileLoadedDataSeenRef.current) {
        return;
      }
      if (mobileAwaitingTargetSeekRef.current || mobileFrameReadyRef.current || mobileFrameProbePendingRef.current) {
        return;
      }
      if (video.readyState < 2) {
        return;
      }
      mobileFrameProbePendingRef.current = true;
      const readinessGeneration = mobileReadinessGenerationRef.current;

      const finalizeFrameReady = () => {
        if (readinessGeneration !== mobileReadinessGenerationRef.current) {
          return;
        }
        mobileFrameProbePendingRef.current = false;
        if (!mobileSessionRef.current || !streamSource) {
          return;
        }
        if (video.readyState < 2) {
          return;
        }
        if (video.videoWidth <= 0 || video.videoHeight <= 0) {
          return;
        }
        mobileFrameReadyRef.current = true;
        maybeFinalizeMobilePlayerReadiness();
      };

      if (typeof video.requestVideoFrameCallback === "function") {
        video.requestVideoFrameCallback(() => {
          finalizeFrameReady();
        });
        return;
      }

      window.setTimeout(() => {
        finalizeFrameReady();
      }, 120);
    }

    function refreshMobileReadinessFlagsFromReadyState() {
      if (video.readyState >= 2) {
        mobileLoadedDataSeenRef.current = true;
      }
      if (video.readyState >= 3) {
        mobileCanPlaySeenRef.current = true;
      }
    }

    function hasAttachedSourceForClientPrewarm(currentSession) {
      if (!currentSession || !streamSource) {
        return false;
      }
      const attachedEpoch = mobileAttachedEpochRef.current;
      const expectedEpoch = resolveSessionAttachmentIdentity(currentSession);
      if (attachedEpoch && expectedEpoch && attachedEpoch !== expectedEpoch) {
        return false;
      }
      return Boolean(video.currentSrc || video.getAttribute("src") || streamSource.url);
    }

    function handleIosPrewarmPlayFailure(requestError, readinessGeneration, { allowReadyFallback = false } = {}) {
      mobileWarmupProbeActiveRef.current = false;
      const normalized = (requestError?.message || "").toLowerCase();
      if (
        normalized.includes("abort")
        || normalized.includes("interrupted")
        || normalized.includes("fetching process")
      ) {
        window.setTimeout(() => {
          if (readinessGeneration !== mobileReadinessGenerationRef.current) {
            return;
          }
          maybeFinalizeMobilePlayerReadiness();
        }, 500);
        return;
      }
      if (
        normalized.includes("gesture")
        || normalized.includes("notallowed")
        || normalized.includes("denied")
        || normalized.includes("not allowed")
      ) {
        restoreVideoAudioAfterPrewarm(video);
        mobileAutoplayPendingRef.current = false;
        mobileResumeAfterReadyRef.current = false;
        browserPlayRequestedRef.current = false;
        if (allowReadyFallback) {
          mobilePlayerCanPlayRef.current = true;
          setMobilePlayerCanPlay(true);
          setMobileLifecycleStateValue("attached");
          clearOptimizedPlaybackPending();
          setPlaybackStatus(browserReadyLabelTitle);
          if (mobilePendingTargetRef.current != null) {
            mobilePendingTargetRef.current = null;
            mobileSeekPendingRef.current = false;
            pendingSeekPhaseRef.current = "idle";
            setPendingSeekPhase("idle");
          }
        } else {
          setPlaybackStatus(`Preparing ${browserPlaybackLabel}`);
        }
        setPlaybackError("");
        setSeekNotice(`Tap play in the video controls to continue ${browserPlaybackLabel}.`);
        return;
      }
      restoreVideoAudioAfterPrewarm(video);
      setPlaybackError(requestError.message || `Failed to warm up ${browserPlaybackLabel}`);
    }

    function startIosClientBufferPrewarm(currentSession, { fromUserGesture = false } = {}) {
      const releaseGate = evaluateMobileClientReleaseGate(currentSession, video);
      const playbackIntentActive =
        fromUserGesture
        || mobileAutoplayPendingRef.current
        || mobileResumeAfterReadyRef.current
        || browserPlayRequestedRef.current;
      if (!shouldStartClientBufferPrewarm({
        iosMobile,
        hasMobileSession: Boolean(currentSession),
        hasAttachedSource: hasAttachedSourceForClientPrewarm(currentSession),
        mobilePlayerCanPlay: mobilePlayerCanPlayRef.current,
        playbackIntentActive,
        releaseGateReady: releaseGate.ready,
        seekPending: Boolean(mobileSeekPendingRef.current),
        retargetTransition: Boolean(mobileRetargetTransitionRef.current),
        awaitingTargetSeek: Boolean(mobileAwaitingTargetSeekRef.current),
      })) {
        return false;
      }
      const warmupAlreadyActive = mobileWarmupProbeActiveRef.current;
      if (!warmupAlreadyActive) {
        mobileWarmupProbeActiveRef.current = true;
        mobileWarmupPlaybackObservedRef.current = false;
        mobileWarmupStartPositionRef.current =
          mobilePendingTargetRef.current != null
            ? resolveMediaElementPositionForAbsolute(mobileSessionRef.current, mobilePendingTargetRef.current)
            : (video.currentTime || 0);
      }
      setPlaybackStatus(`Preparing ${browserPlaybackLabel}`);
      setSeekNotice("");
      if (iosMobile && getPlaybackMode(currentSession?.playback_mode || playbackModeIntentRef.current) === "lite") {
        video.controls = false;
      }
      muteVideoForInternalPrewarm(video);
      if (fromUserGesture && !video.paused) {
        return true;
      }
      if (warmupAlreadyActive && !video.paused) {
        return true;
      }
      const readinessGeneration = mobileReadinessGenerationRef.current;
      video.play().catch((requestError) => {
        if (readinessGeneration !== mobileReadinessGenerationRef.current) {
          return;
        }
        handleIosPrewarmPlayFailure(requestError, readinessGeneration);
      });
      return true;
    }

    function debugPrewarmReleaseBlock(reason, clientReleaseGate = null) {
      if (!iosMobile || !mobileSessionRef.current || mobilePlayerCanPlayRef.current) {
        return;
      }
      if (!mobileWarmupProbeActiveRef.current && !browserPlayRequestedRef.current && !mobileAutoplayPendingRef.current) {
        return;
      }
      const session = mobileSessionRef.current;
      const gate = clientReleaseGate || evaluateMobileClientReleaseGate(session, video);
      const firstFrameReady = hasVideoFirstFrameForPlaybackRelease(video, {
        loadedDataSeen: mobileLoadedDataSeenRef.current,
        canPlaySeen: mobileCanPlaySeenRef.current,
        frameReady: mobileFrameReadyRef.current,
      });
      logBrowserPlaybackDiagnosticEvent("elvern:ios_playback_release_blocked", {
        eventReason: reason,
        firstFrameReady,
        releaseGate: gate,
        releaseGateReason: reason,
        session,
        video,
      });
    }

    function maybeFinalizeMobilePlayerReadiness() {
      if (!iosMobile || !mobileSessionRef.current || !streamSource) {
        return;
      }
      const currentSession = mobileSessionRef.current;
      const shouldAutoplay =
        mobileAutoplayPendingRef.current
        || mobileResumeAfterReadyRef.current
        || browserPlayRequestedRef.current;
      const isRetargetTransition = mobileRetargetTransitionRef.current;
      const clientReleaseGate = isRetargetTransition
        ? null
        : evaluateMobileClientReleaseGate(currentSession, video);
      if (
        clientReleaseGate
        && !clientReleaseGate.ready
        && shouldAutoplay
      ) {
        startIosClientBufferPrewarm(currentSession);
      }
      const firstFrameReady = hasVideoFirstFrameForPlaybackRelease(video, {
        loadedDataSeen: mobileLoadedDataSeenRef.current,
        canPlaySeen: mobileCanPlaySeenRef.current,
        frameReady: mobileFrameReadyRef.current,
      });
      if (!firstFrameReady) {
        debugPrewarmReleaseBlock("first_frame_not_ready", clientReleaseGate);
        return;
      }
      if (mobileAwaitingTargetSeekRef.current) {
        debugPrewarmReleaseBlock("awaiting_target_seek", clientReleaseGate);
        return;
      }
      if (mobileSeekPendingRef.current) {
        debugPrewarmReleaseBlock("seek_pending", clientReleaseGate);
        return;
      }
      if (mobileAttachedEpochRef.current !== resolveSessionAttachmentIdentity(currentSession)) {
        debugPrewarmReleaseBlock("attached_epoch_mismatch", clientReleaseGate);
        return;
      }
      if (isRetargetTransition) {
        const backendRunway = Math.max(
          0,
          (currentSession.ready_end_seconds || 0) - (currentSession.target_position_seconds || 0),
        );
        if (backendRunway < IOS_STABLE_READY_BACKEND_RUNWAY_SECONDS) {
          debugPrewarmReleaseBlock("retarget_backend_runway", clientReleaseGate);
          return;
        }
        const targetAbsoluteSeconds =
          mobilePendingTargetRef.current != null
            ? mobilePendingTargetRef.current
            : requestedTargetSecondsRef.current != null
              ? requestedTargetSecondsRef.current
              : currentSession.pending_target_seconds != null
                ? currentSession.pending_target_seconds
                : currentSession.target_position_seconds || 0;
        if (shouldHoldRetargetFrozenFrameForClientBuffer(targetAbsoluteSeconds)) {
          debugPrewarmReleaseBlock("retarget_client_buffer", clientReleaseGate);
          return;
        }
      } else {
        if (!clientReleaseGate.ready) {
          debugPrewarmReleaseBlock("client_release_gate", clientReleaseGate);
          return;
        }
      }
      mobilePlayerCanPlayRef.current = true;
      setMobilePlayerCanPlay(true);
      setMobileLifecycleStateValue("attached");
      setMobileFrozenFrameUrl("");
      clearOptimizedPlaybackPending();
      setPlaybackError("");
      setSeekNotice("");
      setPlaybackStatus(browserReadyLabelTitle);
      restoreVideoAudioAfterPrewarm(video);
      mobileWarmupProbeActiveRef.current = false;
      mobileWarmupPlaybackObservedRef.current = false;
      mobileWarmupStartPositionRef.current = 0;
      mobileRetargetTransitionRef.current = false;
      if (mobilePendingTargetRef.current != null) {
        mobilePendingTargetRef.current = null;
        mobileSeekPendingRef.current = false;
        pendingSeekPhaseRef.current = "idle";
        setPendingSeekPhase("idle");
      }
      if (mobileAutoplayPendingRef.current || mobileResumeAfterReadyRef.current || browserPlayRequestedRef.current) {
        const shouldResume =
          mobileAutoplayPendingRef.current
          || mobileResumeAfterReadyRef.current
          || browserPlayRequestedRef.current;
        mobileAutoplayPendingRef.current = false;
        mobileResumeAfterReadyRef.current = false;
        browserPlayRequestedRef.current = false;
        if (shouldResume && video.paused) {
          video.play().catch((requestError) => {
            const normalized = (requestError?.message || "").toLowerCase();
            if (
              normalized.includes("gesture")
              || normalized.includes("notallowed")
              || normalized.includes("denied")
              || normalized.includes("not allowed")
            ) {
              setPlaybackError("");
              setPlaybackStatus(browserReadyLabelTitle);
              setSeekNotice(`Tap play in the video controls to continue ${browserPlaybackLabel}.`);
              return;
            }
            setPlaybackError(requestError.message || `Failed to continue ${browserPlaybackLabel}`);
          });
        }
      }
    }

    function finalizeNonIosClientReadiness() {
      const activeSession = mobileSessionRef.current;
      if (!activeSession || iosMobile || mobilePlayerCanPlayRef.current) {
        return false;
      }
      if (!evaluateMobileClientReleaseGate(activeSession, video).ready) {
        return false;
      }
      maybeAcknowledgeHlsAttachment({ playing: !video.paused, loadedEventName: "canplay" });
      mobilePlayerCanPlayRef.current = true;
      setMobilePlayerCanPlay(true);
      clearOptimizedPlaybackPending();
      setPlaybackError("");
      setPlaybackStatus(browserReadyLabelTitle);
      const shouldResume =
        browserPlayRequestedRef.current
        || mobileAutoplayPendingRef.current
        || mobileResumeAfterReadyRef.current;
      browserPlayRequestedRef.current = false;
      mobileAutoplayPendingRef.current = false;
      mobileResumeAfterReadyRef.current = false;
      if (shouldResume && video.paused) {
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
      return true;
    }

    function maybeReleaseClientReadyPlayback() {
      const activeSession = mobileSessionRef.current;
      const hasPlayableSource = Boolean(
        streamSource
        || video.currentSrc
        || video.getAttribute("src")
      );
      const firstFrameReady = hasVideoFirstFrameForPlaybackRelease(video, {
        loadedDataSeen: mobileLoadedDataSeenRef.current,
        canPlaySeen: mobileCanPlaySeenRef.current,
        frameReady: mobileFrameReadyRef.current,
      });
      if (
        !activeSession
        || mobilePlayerCanPlayRef.current
        || !hasPlayableSource
        || !firstFrameReady
        || !video.paused
      ) {
        return false;
      }
      const clientReleaseGate = evaluateMobileClientReleaseGate(activeSession, video);
      if (!clientReleaseGate.ready) {
        return false;
      }
      const shouldResume =
        browserPlayRequestedRef.current
        || mobileAutoplayPendingRef.current
        || mobileResumeAfterReadyRef.current
        || activeSession.playback_mode === "full";
      if (!shouldResume) {
        return false;
      }
      browserPlayRequestedRef.current = false;
      mobileAutoplayPendingRef.current = false;
      mobileResumeAfterReadyRef.current = false;
      mobilePlayerCanPlayRef.current = true;
      setMobilePlayerCanPlay(true);
      setMobileLifecycleStateValue("attached");
      clearOptimizedPlaybackPending();
      setPlaybackError("");
      setSeekNotice("");
      setPlaybackStatus(browserReadyLabelTitle);
      video.play().then(() => {
        setPlaybackStatus(browserReadyLabelTitle);
      }).catch((requestError) => {
        const normalized = (requestError?.message || "").toLowerCase();
        if (
          normalized.includes("gesture")
          || normalized.includes("notallowed")
          || normalized.includes("denied")
          || normalized.includes("not allowed")
        ) {
          setPlaybackError("");
          setSeekNotice(`Tap play in the video controls to continue ${browserPlaybackLabel}.`);
          setPlaybackStatus(browserReadyLabelTitle);
          return;
        }
        setPlaybackError(requestError.message || `Failed to continue ${browserPlaybackLabel}`);
      });
      return true;
    }

    function handleLoadedData() {
      updatePlayerMetrics();
      mobileLoadedDataSeenRef.current = true;
      sampleNativeClientPlayback();
      maybeAcknowledgeHlsAttachment({ playing: !video.paused, loadedEventName: "loadeddata" });
      if (maybeReleaseClientReadyPlayback()) {
        return;
      }
      maybeProbeMobileFirstFrame();
      maybeFinalizeMobilePlayerReadiness();
      finalizeNonIosClientReadiness();
      armFirstFrameStallMonitor();
    }

    function handleProgress() {
      updatePlayerMetrics();
      sampleNativeClientPlayback();
      clearMobileStallRecoveryTimer();
      if (maybeReleaseClientReadyPlayback()) {
        return;
      }
      maybeProbeMobileFirstFrame();
      maybeFinalizeMobilePlayerReadiness();
      finalizeNonIosClientReadiness();
      armFirstFrameStallMonitor();
    }

    function handleCanPlay() {
      if (!mobileSessionRef.current) {
        return;
      }
      if (!iosMobile) {
        finalizeNonIosClientReadiness();
        return;
      }
      mobileCanPlaySeenRef.current = true;
      maybeAcknowledgeHlsAttachment({ playing: !video.paused, loadedEventName: "canplay" });
      sampleNativeClientPlayback();
      if (maybeReleaseClientReadyPlayback()) {
        return;
      }
      maybeProbeMobileFirstFrame();
      maybeFinalizeMobilePlayerReadiness();
      armFirstFrameStallMonitor();
    }

    function handleSeeked() {
      if (!mobileSessionRef.current) {
        return;
      }
      const absoluteCurrentTime = resolveCurrentVideoAbsolutePosition(mobileSessionRef.current, video);
      mobileAwaitingTargetSeekRef.current = false;
      actualMediaElementTimeRef.current = absoluteCurrentTime;
      setActualMediaElementTime(absoluteCurrentTime);
      if (
        pendingSeekPhaseRef.current === "committing"
        && requestedTargetSecondsRef.current != null
        && Math.abs(absoluteCurrentTime - requestedTargetSecondsRef.current) <= 0.75
      ) {
        if (shouldHoldRetargetFrozenFrameForClientBuffer(requestedTargetSecondsRef.current)) {
          maybeFinalizeMobilePlayerReadiness();
          return;
        }
        finalizeRetargetVisibility(video, {
          resumePlayback: mobileResumeAfterReadyRef.current,
          committedPosition: requestedTargetSecondsRef.current,
        });
        return;
      }
      if (mobileRetargetTransitionRef.current && !mobileSeekPendingRef.current) {
        if (shouldHoldRetargetFrozenFrameForClientBuffer(absoluteCurrentTime)) {
          maybeFinalizeMobilePlayerReadiness();
          return;
        }
        finalizeRetargetVisibility(video, {
          resumePlayback: mobileResumeAfterReadyRef.current,
          committedPosition: absoluteCurrentTime,
        });
        return;
      }
      if (!mobileSeekPendingRef.current && !mobileRetargetTransitionRef.current) {
        mobileLastStablePositionRef.current = absoluteCurrentTime;
        committedPlayheadSecondsRef.current = absoluteCurrentTime;
        setCommittedPlayheadSeconds(absoluteCurrentTime);
        requestedTargetSecondsRef.current = absoluteCurrentTime;
        setRequestedTargetSeconds(absoluteCurrentTime);
        clearOptimizedPlaybackPending();
        setPlaybackError("");
        setSeekNotice("");
        postMobileRuntimeHeartbeat({
          lifecycleState: "attached",
          stalled: false,
          playing: !video.paused,
          force: true,
        }).catch(() => {
          // Ignore transient heartbeat failures after an in-range seek.
        });
      }
      if (absoluteCurrentTime > 0.5) {
        reportPlaybackEvent("playback_seeked").catch((requestError) => {
          console.error("Failed to record playback seek", requestError);
        });
      }
      if (!iosMobile) {
        return;
      }
      maybeProbeMobileFirstFrame();
      maybeFinalizeMobilePlayerReadiness();
    }

    function handleDurationChange() {
      updatePlayerMetrics();
      applyResumePosition();
      if (maybeReleaseClientReadyPlayback()) {
        return;
      }
      maybeProbeMobileFirstFrame();
      maybeFinalizeMobilePlayerReadiness();
      finalizeNonIosClientReadiness();
    }

    function handlePlaying() {
      clearMobileStallRecoveryTimer();
      sampleNativeClientPlayback();
      armFirstFrameStallMonitor();
      if (mobileSessionRef.current) {
        maybeAcknowledgeHlsAttachment({ playing: true, force: true, loadedEventName: "playing" });
      }
      if (!mobileSessionRef.current || mobilePlayerCanPlayRef.current) {
        return;
      }
      if (mobileWarmupProbeActiveRef.current) {
        mobileWarmupPlaybackObservedRef.current = true;
        maybeFinalizeMobilePlayerReadiness();
      }
    }

    function handleTimeUpdate() {
      updatePlayerMetrics();
      clearMobileStallRecoveryTimer();
      sampleNativeClientPlayback();
      if (mobileSessionRef.current) {
        maybeAcknowledgeHlsAttachment({ playing: !video.paused, force: true, loadedEventName: "timeupdate" });
      }
      if (mobileSessionRef.current && mobilePlayerCanPlayRef.current && !mobileSeekPendingRef.current) {
        const absoluteCurrentTime = resolveCurrentVideoAbsolutePosition(mobileSessionRef.current, video);
        noteFirstFramePlaybackProgress(absoluteCurrentTime);
        mobileLastStablePositionRef.current = absoluteCurrentTime;
        committedPlayheadSecondsRef.current = absoluteCurrentTime;
        setCommittedPlayheadSeconds(committedPlayheadSecondsRef.current);
        postMobileRuntimeHeartbeat({
          lifecycleState: "attached",
          stalled: false,
          playing: !video.paused,
        }).catch(() => {
          // Ignore transient heartbeat failures during playback.
        });
      }
      if (iosMobile && mobileSessionRef.current && !mobilePlayerCanPlayRef.current && mobileWarmupProbeActiveRef.current) {
        const probeStart = mobileWarmupStartPositionRef.current || 0;
        if ((video.currentTime || 0) >= probeStart + IOS_STABLE_READY_PLAYHEAD_ADVANCE_SECONDS) {
          mobileWarmupPlaybackObservedRef.current = true;
          maybeFinalizeMobilePlayerReadiness();
        }
        return;
      }
      if (video.currentTime > 0) {
        browserPlaybackActiveRef.current = true;
        clearOptimizedPlaybackPending();
        setPlaybackError("");
        setSeekNotice((current) => (current.startsWith("Tap play") ? "" : current));
        if (mobileSessionRef.current) {
          setPlaybackStatus(browserStreamLabelTitle);
          return;
        }
        setPlaybackStatus(
          playbackStateRef.current?.mode === "direct"
            ? "Direct Play"
            : playbackStateRef.current?.mode === "hls"
                && !playbackStateRef.current?.manifest_complete
                && playbackStateRef.current?.transcode_status !== "completed"
              ? "Optimized stream"
              : "Optimized stream",
        );
      }
    }

    function handleSeeking() {
      if (!mobileSessionRef.current) {
        return;
      }
      const currentSession = mobileSessionRef.current;
      const targetPosition = resolveCurrentVideoAbsolutePosition(currentSession, video);
      if (isBrowserPlaybackAbsolutePositionReady(currentSession, targetPosition, { headroomSeconds: SEEK_HEADROOM_SECONDS })) {
        mobilePendingTargetRef.current = null;
        mobileSeekPendingRef.current = false;
        pendingSeekPhaseRef.current = "idle";
        mobileLastStablePositionRef.current = targetPosition;
        committedPlayheadSecondsRef.current = targetPosition;
        actualMediaElementTimeRef.current = targetPosition;
        requestedTargetSecondsRef.current = targetPosition;
        setCommittedPlayheadSeconds(targetPosition);
        setActualMediaElementTime(targetPosition);
        setRequestedTargetSeconds(targetPosition);
        setPlaybackPosition(targetPosition);
        clearOptimizedPlaybackPending();
        setPlaybackError("");
        setSeekNotice("");
        setPlaybackStatus(browserStreamLabelTitle);
        return;
      }
      if (mobileSeekPendingRef.current) {
        return;
      }
      retargetMobileOptimizedPlayback(targetPosition, {
        resumeAfterReady: !video.paused,
      }).catch((requestError) => {
        clearOptimizedPlaybackPending();
        mobileSeekPendingRef.current = false;
        setPlaybackError(requestError.message || "Failed to prepare the requested playback position");
      });
    }

    function handlePlayStarted() {
      if (iosMobile && mobileSessionRef.current && !mobilePlayerCanPlayRef.current) {
        if (!mobileWarmupProbeActiveRef.current) {
          muteVideoForInternalPrewarm(video);
          mobileAutoplayPendingRef.current = true;
          startIosClientBufferPrewarm(mobileSessionRef.current, { fromUserGesture: true });
          setPlaybackStatus(`Preparing ${browserPlaybackLabel}`);
          setSeekNotice("");
        }
        return;
      }
      browserPlaybackActiveRef.current = true;
      clearOptimizedPlaybackPending();
      setPlaybackError("");
      setSeekNotice((current) => (current.startsWith("Tap play") ? "" : current));
      if (!playbackOpenedReportedRef.current) {
        playbackOpenedReportedRef.current = true;
        reportPlaybackEvent("playback_opened").catch((requestError) => {
          playbackOpenedReportedRef.current = false;
          console.error("Failed to record playback open", requestError);
        });
      }
      if (mobileSessionRef.current) {
        setMobileLifecycleStateValue("attached");
        maybeAcknowledgeHlsAttachment({ playing: true, force: true });
        sampleNativeClientPlayback();
        postMobileRuntimeHeartbeat({
          lifecycleState: "attached",
          stalled: false,
          playing: true,
          force: true,
        }).catch(() => {
          // Ignore transient heartbeat failures when playback starts.
        });
        setPlaybackStatus(browserStreamLabelTitle);
        return;
      }
      setPlaybackStatus(
        playbackStateRef.current?.mode === "direct"
          ? "Direct Play"
          : playbackStateRef.current?.mode === "hls"
              && !playbackStateRef.current?.manifest_complete
              && playbackStateRef.current?.transcode_status !== "completed"
            ? "Optimized stream"
            : "Optimized stream",
      );
    }

    video.addEventListener("loadedmetadata", handleLoadedMetadata);
    video.addEventListener("loadeddata", handleLoadedData);
    video.addEventListener("canplay", handleCanPlay);
    video.addEventListener("durationchange", handleDurationChange);
    video.addEventListener("playing", handlePlaying);
    video.addEventListener("timeupdate", handleTimeUpdate);
    video.addEventListener("progress", handleProgress);
    video.addEventListener("play", handlePlayStarted);
    video.addEventListener("play", startProgressTimer);
    video.addEventListener("pause", handlePause);
    video.addEventListener("ended", handleEnded);
    video.addEventListener("seeking", handleSeeking);
    video.addEventListener("seeked", handleSeeked);
    video.addEventListener("waiting", handlePlaybackStalled);
    video.addEventListener("stalled", handlePlaybackStalled);
    video.addEventListener("emptied", handlePlaybackStalled);
    window.addEventListener("pagehide", handlePageHide);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("pageshow", handlePageShow);
    window.addEventListener("focus", handleWindowFocus);

    if (video.readyState >= 1) {
      updatePlayerMetrics();
      applyResumePosition();
      refreshMobileReadinessFlagsFromReadyState();
      maybeProbeMobileFirstFrame();
      maybeFinalizeMobilePlayerReadiness();
    }

    const mobileReadinessPollTimer = window.setInterval(() => {
      if (!mobileSessionRef.current || mobilePlayerCanPlayRef.current) {
        return;
      }
      refreshMobileReadinessFlagsFromReadyState();
      sampleNativeClientPlayback();
      if (maybeReleaseClientReadyPlayback()) {
        return;
      }
      if (iosMobile) {
        maybeProbeMobileFirstFrame();
        maybeFinalizeMobilePlayerReadiness();
      } else {
        finalizeNonIosClientReadiness();
      }
    }, 750);

    return () => {
      stopProgressTimer();
      clearMobileStallRecoveryTimer();
      clearFirstFrameStallMonitor();
      window.clearInterval(mobileReadinessPollTimer);
      window.removeEventListener("pagehide", handlePageHide);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("pageshow", handlePageShow);
      window.removeEventListener("focus", handleWindowFocus);
      video.removeEventListener("loadedmetadata", handleLoadedMetadata);
      video.removeEventListener("loadeddata", handleLoadedData);
      video.removeEventListener("canplay", handleCanPlay);
      video.removeEventListener("durationchange", handleDurationChange);
      video.removeEventListener("playing", handlePlaying);
      video.removeEventListener("timeupdate", handleTimeUpdate);
      video.removeEventListener("progress", handleProgress);
      video.removeEventListener("play", handlePlayStarted);
      video.removeEventListener("play", startProgressTimer);
      video.removeEventListener("pause", handlePause);
      video.removeEventListener("ended", handleEnded);
      video.removeEventListener("seeking", handleSeeking);
      video.removeEventListener("seeked", handleSeeked);
      video.removeEventListener("waiting", handlePlaybackStalled);
      video.removeEventListener("stalled", handlePlaybackStalled);
      video.removeEventListener("emptied", handlePlaybackStalled);
    };
  }, [
    availableDuration,
    fullDuration,
    item,
    playback?.manifest_complete,
    playback?.mode,
    resumableStartPosition,
    streamSource,
    iosMobile,
    onProgressChange,
    onProgressDirty,
    browserPlaybackLabel,
    browserPlaybackLabelTitle,
    browserReadyLabelTitle,
    browserStreamLabelTitle,
  ]);

  useEffect(() => {
    const video = videoRef.current;
    const pendingResume = pendingResumeRef.current;
    if (!video || !pendingResume || resumeAppliedRef.current) {
      return;
    }
    if (!streamSource || video.readyState < 1) {
      return;
    }
    if (
      playback?.mode === "hls"
      && !playback?.manifest_complete
      && pendingResume > availableDuration - SEEK_HEADROOM_SECONDS
    ) {
      return;
    }
    video.currentTime = resolveMediaElementPositionForAbsolute(mobileSessionRef.current, pendingResume);
    setPlaybackPosition(pendingResume);
    pendingResumeRef.current = 0;
    resumeAppliedRef.current = true;
    setSeekNotice((current) => (
      current.startsWith("Resuming at ") ? "" : current
    ));
  }, [availableDuration, playback?.manifest_complete, playback?.mode, streamSource]);

  return {
    hlsRef,
    videoRef,
    mobilePendingTargetRef,
    mobileRetargetTransitionRef,
    mobileSeekPendingRef,
    pendingSeekPhaseRef,
    mobileRecoveryInFlightRef,
    audioSwitchAttachRef,
    mobileSession,
    streamSource,
    mobilePlayerCanPlay,
    mobileFrozenFrameUrl,
    playback,
    playbackError,
    seekNotice,
    playbackPosition,
    playbackStatus,
    playbackModeIntent,
    browserPlaybackDeviceClass,
    browserPlaybackProfile,
    hlsEngineDiagnostics,
    prepareEstimateObservedAtMs,
    prepareEstimateNowMs,
    videoElementKey,
    activePlaybackMode,
    browserPlaybackLabel,
    browserPlaybackLabelTitle,
    browserStreamLabelTitle,
    browserReadyLabelTitle,
    resumePosition,
    fullDuration,
    resumableStartPosition,
    availableDuration,
    optimizedPlaybackPending,
    browserPlaybackSessionActive: Boolean(mobileSession) || optimizedPlaybackPending,
    hasAnyBrowserPlaybackArtifacts: Boolean(
      mobileSession || optimizedPlaybackPending || streamSource || attachedOptimizedManifestUrlRef.current
    ),
    setPlaybackModeIntentValue,
    clearPlaybackError,
    clearOptimizedPlaybackPending,
    prepareControllerForLoad,
    clearPlaybackResources,
    resetMobilePlaybackState,
    syncPlaybackState,
    restoreActiveBrowserPlaybackSession,
    cancelBrowserPlaybackRequest,
    clearPlaybackStreamSource,
    setSeekNoticeValue,
    setPlaybackStatusValue,
    resetPendingPlaybackPreparation,
    startBrowserPlaybackFrom,
    playExistingBrowserSource,
    seekBrowserPlaybackTo,
    selectBrowserPlaybackAudioTrack,
    prepareBrowserPlaybackSubtitleTrack,
    stopCurrentBrowserPlaybackSession,
  };
}
