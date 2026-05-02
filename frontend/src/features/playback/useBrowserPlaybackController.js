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
  getActivePlaybackWorkerConflict,
  getPlaybackAdmissionError,
  getPlaybackWorkerCooldown,
} from "../../lib/playbackWorkerOwnership";
import { getProviderAuthRequirement } from "../../lib/providerAuth";
import {
  isBrowserPlaybackAbsolutePositionReady,
  toBrowserPlaybackAbsoluteSeconds,
  toBrowserPlaybackMediaElementSeconds,
} from "../../lib/browserPlaybackTimeline";
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
  const keys = [
    "autoStartLoad",
    "backBufferLength",
    "enableWorker",
    "liveDurationInfinity",
    "liveMaxLatencyDurationCount",
    "liveSyncDurationCount",
    "lowLatencyMode",
    "maxBufferHole",
    "maxBufferLength",
    "maxBufferSize",
    "nudgeMaxRetry",
    "nudgeOffset",
  ];
  return keys.reduce((result, key) => {
    const value = config[key];
    if (["boolean", "number", "string"].includes(typeof value) || value == null) {
      result[key] = value ?? null;
    }
    return result;
  }, {});
}

export function useBrowserPlaybackController({
  itemId,
  item,
  progress,
  iosMobile,
  onProgressChange,
  onProviderAuthRequired,
}) {
  const videoRef = useRef(null);
  const hlsRef = useRef(null);
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
    resetMobilePlaybackState: resetOptimizedPlaybackSessionState,
    isHlsSessionPayload,
    resolveSessionAttachmentIdentity,
    resolveMobileCommittedPosition,
    syncMobilePlaybackState,
    postMobileRuntimeHeartbeat,
    maybeAcknowledgeHlsAttachment,
    recoverMobilePlaybackAfterResume,
    startMobileOptimizedPlayback,
    retargetMobileOptimizedPlayback,
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

  function resolveMediaElementPositionForAbsolute(session, absoluteSeconds) {
    return session
      ? toBrowserPlaybackMediaElementSeconds(session, absoluteSeconds)
      : Math.max(absoluteSeconds || 0, 0);
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
    });
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
      setPlaybackStatus("Playing while Elvern transcodes ahead");
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
        autoplay: playbackModeIntentRef.current !== "full",
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
      setPlaybackError(requestError.message || `Failed to stop ${browserPlaybackLabelTitle}`);
    }
  }

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
      setHlsEngineDiagnostics({
        selectedEngine: "native_hls",
        nativeHlsSelected: true,
        hlsJsSelected: false,
        hlsJsAttachedToVideo: false,
        hlsJsConfig: null,
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
        ...hlsSupportDiagnostics,
      });
      setPlaybackError("This browser cannot play HLS fallback streams");
      return () => {
        video.removeEventListener("error", handlePlaybackFailure);
      };
    }

    const hls = new Hls();
    hlsRef.current = hls;
    hls.loadSource(streamSource.url);
    hls.attachMedia(video);
    setHlsEngineDiagnostics({
      selectedEngine: "hls.js",
      nativeHlsSelected: false,
      hlsJsSelected: true,
      hlsJsAttachedToVideo: true,
      hlsJsConfig: compactHlsConfig(hls.config),
      ...hlsSupportDiagnostics,
    });
    hls.on(Hls.Events.MANIFEST_PARSED, maybeAutoplay);
    hls.on(Hls.Events.ERROR, (_event, data) => {
      if (data.fatal) {
        if (isHlsSessionPayload(mobileSessionRef.current)) {
          setPlaybackError("");
          setOptimizedPlaybackPending(true);
          setSeekNotice(`Reattaching the current ${browserPlaybackLabel} session.`);
          applyMobileLifecycleStatus("recovering");
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
      const currentTime = video.currentTime || 0;
      const ranges = video.buffered;
      for (let index = 0; index < ranges.length; index += 1) {
        const start = ranges.start(index);
        const end = ranges.end(index);
        if (currentTime >= start && currentTime <= end) {
          return Math.max(0, end - currentTime);
        }
      }
      return 0;
    }

    function handlePageHide() {
      beaconProgress(false);
      if (!iosMobile || !mobileSessionRef.current) {
        return;
      }
      mobileWasBackgroundedRef.current = true;
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
      stopProgressTimer();
      reportPlaybackEvent("playback_completed").catch((requestError) => {
        console.error("Failed to record playback completion", requestError);
        flushProgress(true);
      });
      clearMobileStallRecoveryTimer();
    }

    function handleVisibilityChange() {
      if (document.visibilityState === "hidden") {
        beaconProgress(false);
        if (iosMobile && mobileSessionRef.current) {
          mobileWasBackgroundedRef.current = true;
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
        recoverMobilePlaybackAfterResume("visibilitychange");
      }
    }

    function handlePageShow() {
      if (iosMobile && mobileSessionRef.current && mobileWasBackgroundedRef.current) {
        recoverMobilePlaybackAfterResume("pageshow");
      }
    }

    function handleWindowFocus() {
      if (iosMobile && mobileSessionRef.current && mobileWasBackgroundedRef.current) {
        recoverMobilePlaybackAfterResume("focus");
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
      const bufferedAhead = bufferedRunwaySeconds();
      const backendAhead = currentSession?.ahead_runway_seconds || 0;
      const refillInProgress = Boolean(currentSession?.refill_in_progress);
      const hardStarvation =
        currentSession?.stalled_recovery_needed
        || currentSession?.starvation_risk
        || (backendAhead <= 3 && bufferedAhead <= 0.75 && !refillInProgress);
      if (!hardStarvation) {
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
        const latestBackendAhead = latestSession?.ahead_runway_seconds || 0;
        if (latestBackendAhead > 6 && !latestSession?.stalled_recovery_needed) {
          return;
        }
        setOptimizedPlaybackPending(true);
        setSeekNotice(`Reconnecting the current ${browserPlaybackLabel} session.`);
        applyMobileLifecycleStatus("recovering");
        postMobileRuntimeHeartbeat({
          lifecycleState: "recovering",
          stalled: true,
          playing: true,
          force: true,
        }).catch(() => {
          // Recovery will still try to reattach locally.
        });
        recoverMobilePlaybackAfterResume("stalled");
      }, 2200);
    }

    function handleLoadedMetadata() {
      updatePlayerMetrics();
      maybeAcknowledgeHlsAttachment({ playing: !video.paused, force: true });
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

    function maybeFinalizeMobilePlayerReadiness() {
      if (!iosMobile || !mobileSessionRef.current || !streamSource) {
        return;
      }
      const currentSession = mobileSessionRef.current;
      const shouldAutoplay = mobileAutoplayPendingRef.current || mobileResumeAfterReadyRef.current;
      const isRetargetTransition = mobileRetargetTransitionRef.current;
      if (!mobileCanPlaySeenRef.current || !mobileLoadedDataSeenRef.current) {
        return;
      }
      if (mobileAwaitingTargetSeekRef.current) {
        return;
      }
      if (mobileSeekPendingRef.current) {
        return;
      }
      if (mobileAttachedEpochRef.current !== resolveSessionAttachmentIdentity(currentSession)) {
        return;
      }
      if (video.readyState < 3) {
        return;
      }
      const backendRunway = Math.max(
        0,
        (currentSession.ready_end_seconds || 0) - (currentSession.target_position_seconds || 0),
      );
      if (backendRunway < IOS_STABLE_READY_BACKEND_RUNWAY_SECONDS) {
        return;
      }
      if (shouldAutoplay && !isRetargetTransition) {
        if (!mobileWarmupProbeActiveRef.current) {
          mobileWarmupProbeActiveRef.current = true;
          mobileWarmupPlaybackObservedRef.current = false;
          mobileWarmupStartPositionRef.current =
            mobilePendingTargetRef.current != null
              ? resolveMediaElementPositionForAbsolute(mobileSessionRef.current, mobilePendingTargetRef.current)
              : (video.currentTime || 0);
          const readinessGeneration = mobileReadinessGenerationRef.current;
          if (iosMobile && getPlaybackMode(currentSession?.playback_mode || playbackModeIntentRef.current) === "lite") {
            video.controls = true;
          }
          video
            .play()
            .then(() => {
              window.setTimeout(() => {
                if (readinessGeneration !== mobileReadinessGenerationRef.current) {
                  return;
                }
                if (!mobileWarmupProbeActiveRef.current || mobilePlayerCanPlayRef.current) {
                  return;
                }
                if (!video.paused && video.readyState >= 3) {
                  mobileWarmupPlaybackObservedRef.current = true;
                  maybeFinalizeMobilePlayerReadiness();
                }
              }, 250);
            })
            .catch((requestError) => {
              mobileWarmupProbeActiveRef.current = false;
              const normalized = (requestError?.message || "").toLowerCase();
              if (
                normalized.includes("gesture")
                || normalized.includes("notallowed")
                || normalized.includes("denied")
                || normalized.includes("not allowed")
              ) {
                mobileAutoplayPendingRef.current = false;
                mobileResumeAfterReadyRef.current = false;
                mobilePlayerCanPlayRef.current = true;
                setMobilePlayerCanPlay(true);
                setMobileLifecycleStateValue("attached");
                clearOptimizedPlaybackPending();
                setPlaybackError("");
                setPlaybackStatus(browserReadyLabelTitle);
                setSeekNotice(`Tap play in the video controls to continue ${browserPlaybackLabel}.`);
                if (mobilePendingTargetRef.current != null) {
                  mobilePendingTargetRef.current = null;
                  mobileSeekPendingRef.current = false;
                  pendingSeekPhaseRef.current = "idle";
                  setPendingSeekPhase("idle");
                }
                return;
              }
              clearOptimizedPlaybackPending();
              setPlaybackError(requestError.message || `Failed to continue ${browserPlaybackLabel}`);
            });
        }
        if (!mobileWarmupPlaybackObservedRef.current) {
          return;
        }
      }
      mobilePlayerCanPlayRef.current = true;
      setMobilePlayerCanPlay(true);
      setMobileLifecycleStateValue("attached");
      setMobileFrozenFrameUrl("");
      clearOptimizedPlaybackPending();
      setPlaybackError("");
      setPlaybackStatus(browserReadyLabelTitle);
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
      if (mobileAutoplayPendingRef.current || mobileResumeAfterReadyRef.current) {
        const shouldResume = mobileAutoplayPendingRef.current || mobileResumeAfterReadyRef.current;
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
              setPlaybackStatus(browserReadyLabelTitle);
              setSeekNotice(`Tap play in the video controls to continue ${browserPlaybackLabel}.`);
              return;
            }
            setPlaybackError(requestError.message || `Failed to continue ${browserPlaybackLabel}`);
          });
        }
      }
    }

    function handleLoadedData() {
      updatePlayerMetrics();
      mobileLoadedDataSeenRef.current = true;
      maybeAcknowledgeHlsAttachment({ playing: !video.paused });
      maybeProbeMobileFirstFrame();
      maybeFinalizeMobilePlayerReadiness();
    }

    function handleProgress() {
      updatePlayerMetrics();
      clearMobileStallRecoveryTimer();
      maybeProbeMobileFirstFrame();
      maybeFinalizeMobilePlayerReadiness();
    }

    function handleCanPlay() {
      if (!mobileSessionRef.current) {
        return;
      }
      maybeAcknowledgeHlsAttachment({ playing: !video.paused });
      if (!iosMobile) {
        mobilePlayerCanPlayRef.current = true;
        setMobilePlayerCanPlay(true);
        clearOptimizedPlaybackPending();
        setPlaybackError("");
        setPlaybackStatus(browserReadyLabelTitle);
        return;
      }
      mobileCanPlaySeenRef.current = true;
      maybeProbeMobileFirstFrame();
      maybeFinalizeMobilePlayerReadiness();
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
        finalizeRetargetVisibility(video, {
          resumePlayback: mobileResumeAfterReadyRef.current,
          committedPosition: requestedTargetSecondsRef.current,
        });
        return;
      }
      if (mobileRetargetTransitionRef.current && !mobileSeekPendingRef.current) {
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
    }

    function handlePlaying() {
      clearMobileStallRecoveryTimer();
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
      if (mobileSessionRef.current && mobilePlayerCanPlayRef.current && !mobileSeekPendingRef.current) {
        const absoluteCurrentTime = resolveCurrentVideoAbsolutePosition(mobileSessionRef.current, video);
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
              ? "Playing while Elvern transcodes ahead"
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
          video.pause();
          mobileAutoplayPendingRef.current = true;
          setPlaybackStatus(`Preparing ${browserPlaybackLabel}`);
          setSeekNotice(`Elvern is still preparing enough video for stable ${browserPlaybackLabel}.`);
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
            ? "Playing while Elvern transcodes ahead"
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
    }

    return () => {
      stopProgressTimer();
      clearMobileStallRecoveryTimer();
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
    videoRef,
    mobilePendingTargetRef,
    mobileRetargetTransitionRef,
    mobileSeekPendingRef,
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
    stopCurrentBrowserPlaybackSession,
  };
}
