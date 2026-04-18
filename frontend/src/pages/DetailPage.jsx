import Hls from "hls.js";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { LoadingView } from "../components/LoadingView";
import { apiRequest } from "../lib/api";
import {
  buildRoute2ProbeSegmentUrl,
  getPlaybackMode,
  getPlaybackModeLabel,
  getPlaybackModeTitle,
  getSessionModeEstimateSeconds,
  isIOSMobileBrowser,
  isRoute2SessionPayload as isSharedRoute2SessionPayload,
  resolveBrowserPlaybackSessionRoot,
} from "../lib/browserPlayback";
import { getOrCreateDeviceId } from "../lib/device";
import { formatBytes, formatDuration } from "../lib/format";
import {
  extractLibraryReturnState,
  readLibraryReturnTarget,
  rememberLibraryReturnTarget,
} from "../lib/libraryNavigation";
import { getMovieCardTitle } from "../lib/movieTitles";
import {
  clearProviderAuthIntent,
  getProviderAuthRequirement,
  readProviderAuthIntent,
  saveProviderAuthIntent,
} from "../lib/providerAuth";


const SEEK_HEADROOM_SECONDS = 2;
const COMPLETION_GRACE_SECONDS = 15;
const IOS_OPTIMIZED_READY_SECONDS = 18;
const IOS_STABLE_READY_BACKEND_RUNWAY_SECONDS = 16;
const IOS_STABLE_READY_PLAYHEAD_ADVANCE_SECONDS = 0.5;
const SESSION_MANIFEST_REFRESH_RUNWAY_SECONDS = 12;
const ROUTE2_ATTACH_RETRY_MS = 2500;
const IMPORTANT_PLAYBACK_STATUS_KEYWORDS = [
  "failed",
  "error",
  "recovering",
  "resuming",
  "suspended",
  "stopped",
  "queued",
];
const IMPORTANT_PLAYBACK_REASON_KEYWORDS = [
  "failed",
  "error",
  "stopped",
  "action required",
  "tap play",
  "sign in",
  "disabled",
];


function readFiniteDuration(video) {
  if (!video) {
    return 0;
  }
  return Number.isFinite(video.duration) && video.duration > 0
    ? video.duration
    : 0;
}

function detectDesktopPlatform() {
  if (typeof navigator === "undefined") {
    return null;
  }
  const agent = (navigator.userAgent || "").toLowerCase();
  if (agent.includes("windows")) {
    return "windows";
  }
  if ((agent.includes("macintosh") || agent.includes("mac os x")) && !agent.includes("iphone") && !agent.includes("ipad")) {
    return "mac";
  }
  if (agent.includes("linux") && !agent.includes("android")) {
    return "linux";
  }
  return null;
}

function isLocalDevelopmentLoopback(platform) {
  if (typeof window === "undefined" || platform !== "linux") {
    return false;
  }
  const host = (window.location.hostname || "").toLowerCase();
  return host === "localhost" || host === "127.0.0.1";
}

function buildInfuseLaunchUrl(streamUrl, { successUrl, errorUrl } = {}) {
  const params = new URLSearchParams({ url: streamUrl });
  if (successUrl) {
    params.set("x-success", successUrl);
  }
  if (errorUrl) {
    params.set("x-error", errorUrl);
  }
  return `infuse://x-callback-url/play?${params.toString()}`;
}

function buildIosVlcLaunchUrl(streamUrl) {
  const params = new URLSearchParams({ url: streamUrl });
  return `vlc-x-callback://x-callback-url/stream?${params.toString()}`;
}

function buildFreshManifestUrl(url) {
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}vod_attach=${Date.now()}`;
}

function buildSessionManifestUrl(url, manifestRevision) {
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}manifest_revision=${encodeURIComponent(manifestRevision)}`;
}

function buildAttachRevisionManifestUrl(url, attachRevision) {
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}attach_revision=${encodeURIComponent(String(attachRevision || 0))}`;
}

function buildIosExternalAppCallbackUrl({ app, result }) {
  if (typeof window === "undefined") {
    return "";
  }
  const callbackUrl = new URL(window.location.href);
  callbackUrl.searchParams.set("ios_app", app);
  callbackUrl.searchParams.set("ios_result", result);
  callbackUrl.searchParams.delete("errorCode");
  callbackUrl.searchParams.delete("errorMessage");
  return callbackUrl.toString();
}

function detectIosExternalCallerSurface() {
  if (typeof window === "undefined" || typeof navigator === "undefined") {
    return "web_browser";
  }
  const standaloneMedia = typeof window.matchMedia === "function"
    ? window.matchMedia("(display-mode: standalone)").matches
    : false;
  const navigatorStandalone = Boolean(navigator.standalone);
  return standaloneMedia || navigatorStandalone ? "web_pwa" : "web_browser";
}

function normalizeIosTransportDebug(payload) {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const probe = payload.transport_probe && typeof payload.transport_probe === "object"
    ? payload.transport_probe
    : null;
  const decision = payload.transport_decision && typeof payload.transport_decision === "object"
    ? payload.transport_decision
    : null;
  const selectedPlayer = probe?.selected_player || decision?.selected_player || "";
  const selectedMode = probe?.selected_mode || decision?.selected_mode || "";
  const primaryTargetKind =
    probe?.primary_target_kind || decision?.primary_target?.target_kind || "";
  const reasonCode = probe?.reason_code || decision?.telemetry?.reason_code || "";
  if (!selectedPlayer && !selectedMode && !primaryTargetKind && !reasonCode) {
    return null;
  }
  return {
    selectedPlayer: selectedPlayer || "unknown",
    selectedMode: selectedMode || "unknown",
    primaryTargetKind: primaryTargetKind || "unknown",
    reasonCode: reasonCode || "unknown",
  };
}

function formatTimeRange(startSeconds, endSeconds) {
  if (!Number.isFinite(startSeconds) || !Number.isFinite(endSeconds)) {
    return "";
  }
  const safeStart = Math.max(startSeconds || 0, 0);
  const safeEnd = Math.max(endSeconds || 0, 0);
  if (safeEnd <= safeStart + 0.25) {
    return formatDuration(safeStart);
  }
  return `${formatDuration(safeStart)}-${formatDuration(safeEnd)}`;
}

function formatEstimateDuration(seconds) {
  const numericSeconds = Number(seconds) || 0;
  const totalSeconds = Math.max(0, numericSeconds > 0 ? Math.ceil(numericSeconds) : 0);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const remainingSeconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(remainingSeconds).padStart(2, "0")}`;
}

function isImportantPlaybackStatus(status) {
  if (typeof status !== "string" || !status.trim()) {
    return false;
  }
  const normalized = status.trim().toLowerCase();
  return IMPORTANT_PLAYBACK_STATUS_KEYWORDS.some((keyword) => normalized.includes(keyword));
}

function isImportantPlaybackReason(reason) {
  if (typeof reason !== "string" || !reason.trim()) {
    return false;
  }
  const normalized = reason.trim().toLowerCase();
  return IMPORTANT_PLAYBACK_REASON_KEYWORDS.some((keyword) => normalized.includes(keyword));
}

function resolvePrepareEstimateTone(seconds) {
  if (!Number.isFinite(seconds)) {
    return "estimating";
  }
  if (seconds >= 20 * 60) {
    return "purple";
  }
  if (seconds >= 15 * 60) {
    return "dark-red";
  }
  if (seconds >= 10 * 60) {
    return "light-red";
  }
  if (seconds >= 6 * 60) {
    return "orange";
  }
  if (seconds >= 4 * 60) {
    return "yellow";
  }
  return "green";
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

function saveIosExternalAppLaunchState({ itemId, app, launchUrl, playbackUrl }) {
  if (typeof window === "undefined" || !itemId || !app) {
    return;
  }
  try {
    window.sessionStorage.setItem(
      `elvern-ios-handoff:${itemId}:${app}`,
      JSON.stringify({
        itemId,
        app,
        launchUrl,
        playbackUrl,
        savedAt: Date.now(),
      }),
    );
  } catch {
    // Ignore sessionStorage failures; the live handoff is more important.
  }
}

function readIosExternalAppLaunchState({ itemId, app, maxAgeMs = 15 * 60 * 1000 }) {
  if (typeof window === "undefined" || !itemId || !app) {
    return null;
  }
  try {
    const raw = window.sessionStorage.getItem(`elvern-ios-handoff:${itemId}:${app}`);
    if (!raw) {
      return null;
    }
    const payload = JSON.parse(raw);
    if (!payload || payload.itemId !== itemId || payload.app !== app) {
      return null;
    }
    if (Date.now() - Number(payload.savedAt || 0) > maxAgeMs) {
      return null;
    }
    return payload;
  } catch {
    return null;
  }
}

function clearIosExternalAppLaunchState({ itemId, app }) {
  if (typeof window === "undefined" || !itemId || !app) {
    return;
  }
  try {
    window.sessionStorage.removeItem(`elvern-ios-handoff:${itemId}:${app}`);
  } catch {
    // Ignore sessionStorage cleanup failures.
  }
}

function releaseOptimizedPlaybackSession(itemId) {
  if (!itemId || typeof window === "undefined") {
    return;
  }
  const url = `/api/playback/${itemId}/stop`;
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

function releaseMobilePlaybackSession(sessionId) {
  if (!sessionId || typeof window === "undefined") {
    return;
  }
  const url = `/api/mobile-playback/sessions/${sessionId}/stop`;
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


export function DetailPage() {
  const { itemId } = useParams();
  const location = useLocation();
  const { user } = useAuth();
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
  const browserStartPositionRef = useRef(0);
  const playbackModeIntentRef = useRef("lite");
  const fullProbeInFlightRef = useRef(false);

  const [item, setItem] = useState(null);
  const [progress, setProgress] = useState(null);
  const [playback, setPlayback] = useState(null);
  const [mobileSession, setMobileSession] = useState(null);
  const [streamSource, setStreamSource] = useState(null);
  const [mobilePlayerCanPlay, setMobilePlayerCanPlay] = useState(false);
  const [mobileFrozenFrameUrl, setMobileFrozenFrameUrl] = useState("");
  const [error, setError] = useState("");
  const [playbackError, setPlaybackError] = useState("");
  const [seekNotice, setSeekNotice] = useState("");
  const [playbackStatus, setPlaybackStatus] = useState("Checking playback compatibility");
  const [playbackPosition, setPlaybackPosition] = useState(0);
  const [requestedTargetSeconds, setRequestedTargetSeconds] = useState(null);
  const [committedPlayheadSeconds, setCommittedPlayheadSeconds] = useState(0);
  const [actualMediaElementTime, setActualMediaElementTime] = useState(0);
  const [pendingSeekPhase, setPendingSeekPhase] = useState("idle");
  const [mobileLifecycleState, setMobileLifecycleState] = useState("attached");
  const [playerMeasuredDuration, setPlayerMeasuredDuration] = useState(0);
  const [desktopPlayback, setDesktopPlayback] = useState(null);
  const [vlcLaunchPending, setVlcLaunchPending] = useState(false);
  const [vlcLaunchMessage, setVlcLaunchMessage] = useState("");
  const [vlcLaunchError, setVlcLaunchError] = useState("");
  const [providerReconnectPending, setProviderReconnectPending] = useState(false);
  const [providerReconnectResult, setProviderReconnectResult] = useState(null);
  const [providerReconnectModal, setProviderReconnectModal] = useState({
    open: false,
    provider: "",
    title: "",
    message: "",
    actionType: "",
    allowReconnect: true,
    requiresAdmin: false,
    errorMessage: "",
  });
  const [iosAppLaunchPending, setIosAppLaunchPending] = useState(false);
  const [iosAppLaunchMessage, setIosAppLaunchMessage] = useState("");
  const [iosAppLaunchError, setIosAppLaunchError] = useState("");
  const [iosAppLaunchUrl, setIosAppLaunchUrl] = useState("");
  const [iosAppPlaybackUrl, setIosAppPlaybackUrl] = useState("");
  const [iosAppTarget, setIosAppTarget] = useState("");
  const [iosTransportDebug, setIosTransportDebug] = useState(null);
  const [optimizedPlaybackPending, setOptimizedPlaybackPending] = useState(false);
  const [playbackModeIntent, setPlaybackModeIntent] = useState("lite");
  const [prepareEstimateObservedAtMs, setPrepareEstimateObservedAtMs] = useState(0);
  const [prepareEstimateNowMs, setPrepareEstimateNowMs] = useState(() => Date.now());
  const [videoElementKey, setVideoElementKey] = useState(0);
  const [browserResumeModalOpen, setBrowserResumeModalOpen] = useState(false);
  const [browserStopModalOpen, setBrowserStopModalOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [hiddenActionPending, setHiddenActionPending] = useState(false);
  const [hiddenActionMessage, setHiddenActionMessage] = useState("");
  const [hiddenActionError, setHiddenActionError] = useState("");
  const [globalHiddenActionPending, setGlobalHiddenActionPending] = useState(false);
  const [globalHiddenActionMessage, setGlobalHiddenActionMessage] = useState("");
  const [globalHiddenActionError, setGlobalHiddenActionError] = useState("");
  const [detailRefreshKey, setDetailRefreshKey] = useState(0);
  const desktopPlatform = detectDesktopPlatform();
  const iosMobile = isIOSMobileBrowser();
  const localDevLoopback = isLocalDevelopmentLoopback(desktopPlatform);
  const desktopDeviceId = useMemo(() => getOrCreateDeviceId(), []);
  const isAdmin = user?.role === "admin";
  const browserPlaybackSessionRoot = resolveBrowserPlaybackSessionRoot();
  const browserPlaybackProfile = iosMobile ? "mobile_1080p" : "mobile_2160p";
  const activePlaybackMode = getPlaybackMode(mobileSession?.playback_mode || playbackModeIntent);
  const browserPlaybackLabel = getPlaybackModeLabel(activePlaybackMode);
  const browserPlaybackLabelTitle = getPlaybackModeTitle(activePlaybackMode);
  const browserStreamLabelTitle = browserPlaybackLabelTitle;
  const browserReadyLabelTitle = `${browserPlaybackLabelTitle} ready`;
  const activeLibraryReturn = useMemo(() => {
    const fromLocation = extractLibraryReturnState(location.state);
    if (fromLocation) {
      return fromLocation;
    }
    return readLibraryReturnTarget() || {
      listPath: "/library",
      anchorItemId: null,
      scrollY: 0,
      pendingRestore: false,
    };
  }, [location.state]);
  const libraryReturnPath = activeLibraryReturn?.listPath || "/library";
  const libraryReturnLinkState = useMemo(
    () => ({ restoreLibraryReturn: true }),
    [],
  );

  function prepareLibraryReturnNavigation() {
    rememberLibraryReturnTarget({
      listPath: libraryReturnPath,
      anchorItemId: activeLibraryReturn?.anchorItemId ?? null,
      scrollY: activeLibraryReturn?.scrollY ?? 0,
      pendingRestore: true,
    });
  }

  function closeProviderReconnectModal() {
    clearProviderAuthIntent();
    setProviderReconnectModal({
      open: false,
      provider: "",
      title: "",
      message: "",
      actionType: "",
      allowReconnect: true,
      requiresAdmin: false,
      errorMessage: "",
    });
    setProviderReconnectPending(false);
  }

  function openProviderReconnectModal(requirement, actionType, errorMessage = "") {
    setProviderReconnectModal({
      open: true,
      provider: requirement.provider,
      title: requirement.title,
      message: requirement.message,
      actionType,
      allowReconnect: requirement.allowReconnect !== false,
      requiresAdmin: requirement.requiresAdmin === true,
      errorMessage,
    });
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

  function clearOptimizedPlaybackPending() {
    setOptimizedPlaybackPending(false);
  }

  function setMobileLifecycleStateValue(nextState) {
    mobileLifecycleStateRef.current = nextState;
    setMobileLifecycleState(nextState);
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

  function stopBrowserPlaybackForDesktopHandoff(statusMessage = "Handing off to VLC") {
    browserPlayRequestedRef.current = false;
    clearPlaybackResources();
    const video = videoRef.current;
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }
    setStreamSource(null);
    setPlaybackError("");
    setSeekNotice("");
    setPlaybackStatus(statusMessage);
    clearOptimizedPlaybackPending();
  }

  function resetIosExternalAppState() {
    setIosAppLaunchMessage("");
    setIosAppLaunchError("");
    setIosAppLaunchUrl("");
    setIosAppPlaybackUrl("");
    setIosAppTarget("");
  }

  useEffect(() => {
    setHiddenActionPending(false);
    setHiddenActionMessage("");
    setHiddenActionError("");
    setGlobalHiddenActionPending(false);
    setGlobalHiddenActionMessage("");
    setGlobalHiddenActionError("");
  }, [itemId]);

  useEffect(() => {
    if (!activeLibraryReturn) {
      return;
    }
    rememberLibraryReturnTarget({
      listPath: activeLibraryReturn.listPath,
      anchorItemId: activeLibraryReturn.anchorItemId,
      scrollY: activeLibraryReturn.scrollY,
      pendingRestore: false,
    });
  }, [activeLibraryReturn]);

  useEffect(() => {
    mobilePlayerCanPlayRef.current = mobilePlayerCanPlay;
  }, [mobilePlayerCanPlay]);

  useEffect(() => {
    if (typeof window === "undefined" || !iosMobile) {
      return;
    }
    const currentUrl = new URL(window.location.href);
    const app = currentUrl.searchParams.get("ios_app");
    const result = currentUrl.searchParams.get("ios_result");
    if (!result || (app !== "infuse" && app !== "vlc")) {
      return;
    }
    const savedLaunch = app === "infuse"
      ? readIosExternalAppLaunchState({ itemId, app: "infuse" })
      : null;
    if (savedLaunch?.launchUrl) {
      setIosAppLaunchUrl(savedLaunch.launchUrl);
    }
    if (savedLaunch?.playbackUrl) {
      setIosAppPlaybackUrl(savedLaunch.playbackUrl);
    }
    const appLabel = app === "infuse" ? "Infuse" : "VLC";
    setIosAppTarget(appLabel);
    if (result === "error") {
      const returnedMessage = currentUrl.searchParams.get("errorMessage");
      setIosAppLaunchError(
        returnedMessage
          ? `${appLabel} handoff failed: ${returnedMessage}`
          : app === "infuse"
            ? "Infuse could not continue this handoff. Use the short-lived playback URL below inside Infuse."
            : "VLC could not continue this handoff. Try the VLC button again.",
      );
      setIosAppLaunchMessage("");
    } else {
      setIosAppLaunchError("");
      setIosAppLaunchMessage(
        app === "infuse"
          ? "Returned from Infuse. If playback did not continue there, use the short-lived playback URL below inside Infuse."
          : "Returned from VLC.",
      );
    }
    currentUrl.searchParams.delete("ios_app");
    currentUrl.searchParams.delete("ios_result");
    currentUrl.searchParams.delete("errorCode");
    currentUrl.searchParams.delete("errorMessage");
    window.history.replaceState({}, "", `${currentUrl.pathname}${currentUrl.search}${currentUrl.hash}`);
  }, [iosMobile, itemId]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const currentUrl = new URL(window.location.href);
    const statusValue = currentUrl.searchParams.get("googleDriveStatus");
    if (!statusValue) {
      return;
    }
    const statusMessage = currentUrl.searchParams.get("googleDriveMessage") || "";
    setProviderReconnectResult({
      provider: "google_drive",
      status: statusValue,
      message: statusMessage,
    });
    currentUrl.searchParams.delete("googleDriveStatus");
    currentUrl.searchParams.delete("googleDriveMessage");
    window.history.replaceState({}, "", `${currentUrl.pathname}${currentUrl.search}${currentUrl.hash}`);
  }, [itemId]);

  useEffect(() => {
    if (!providerReconnectResult) {
      return;
    }
    const pendingIntent = readProviderAuthIntent();
    if (
      !pendingIntent
      || pendingIntent.provider !== "google_drive"
      || Number(pendingIntent.mediaItemId ?? pendingIntent.itemId) !== Number(itemId)
    ) {
      setProviderReconnectPending(false);
      setProviderReconnectResult(null);
      return;
    }
    if (providerReconnectResult.status !== "connected") {
      setProviderReconnectPending(false);
      openProviderReconnectModal(
        {
          provider: "google_drive",
          title: "Google Drive connection expired",
          message: "Reconnect Google Drive to continue this action.",
        },
        String(pendingIntent.actionType || ""),
        providerReconnectResult.message || "Google Drive reconnect was cancelled or failed.",
      );
      setProviderReconnectResult(null);
      return;
    }
    if (!item || !desktopPlayback) {
      return;
    }
    clearProviderAuthIntent();
    closeProviderReconnectModal();
    setProviderReconnectResult(null);
    if (pendingIntent.actionType === "desktop_vlc_handoff") {
      setVlcLaunchError("");
      setVlcLaunchMessage("Google Drive reconnected. Retrying VLC handoff.");
      void handleOpenInVlc({ isProviderRetry: true });
    }
  }, [desktopPlayback, item, itemId, providerReconnectResult]);

  useEffect(() => {
    if (error || playbackError) {
      clearOptimizedPlaybackPending();
    }
  }, [error, playbackError]);

  useEffect(() => {
    if (typeof window === "undefined" || !(optimizedPlaybackPending || (mobileSession && !mobilePlayerCanPlay))) {
      return undefined;
    }
    setPrepareEstimateNowMs(Date.now());
    const timerId = window.setInterval(() => {
      setPrepareEstimateNowMs(Date.now());
    }, 1000);
    return () => {
      window.clearInterval(timerId);
    };
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
      const probeUrl = buildRoute2ProbeSegmentUrl(activeSession);
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

  function stopMobilePlaybackPolling() {
    mobilePollTokenRef.current += 1;
    window.clearTimeout(mobilePollRef.current);
    mobilePollRef.current = null;
  }

  function resetMobilePlaybackState({ clearPlayer = false } = {}) {
    stopMobilePlaybackPolling();
    attachedOptimizedManifestUrlRef.current = "";
    mobileSessionRef.current = null;
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
    setBrowserResumeModalOpen(false);
    setBrowserStopModalOpen(false);
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

  function isRoute2SessionPayload(payload = mobileSessionRef.current) {
    return isSharedRoute2SessionPayload(payload);
  }

  function resolveRoute2AttachPosition(payload = mobileSessionRef.current) {
    if (typeof payload?.attach_position_seconds === "number") {
      return Math.max(payload.attach_position_seconds, 0);
    }
    return Math.max(payload?.target_position_seconds || 0, 0);
  }

  function resolveSessionAttachmentIdentity(payload = mobileSessionRef.current) {
    if (!payload) {
      return null;
    }
    if (isRoute2SessionPayload(payload)) {
      return payload.active_epoch_id || null;
    }
    return payload.epoch;
  }

  function isRoute2AttachReady(payload = mobileSessionRef.current) {
    const requiresFullModeReady = getPlaybackMode(payload?.playback_mode) === "full";
    return Boolean(
      isRoute2SessionPayload(payload) &&
      payload?.attach_ready &&
      (!requiresFullModeReady || payload?.mode_ready) &&
      payload?.active_manifest_url &&
      (payload?.attach_revision || 0) > 0,
    );
  }

  function resolveRoute2HeartbeatAttachRevision(payload = mobileSessionRef.current) {
    if (!isRoute2SessionPayload(payload)) {
      return 0;
    }
    const authorityRevision = Number(payload?.attach_revision || 0);
    const pendingRevision = Number(mobilePendingAttachRevisionRef.current || 0);
    const confirmedRevision = Number(mobileClientAttachRevisionRef.current || 0);
    return Math.min(authorityRevision, Math.max(confirmedRevision, pendingRevision));
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
      const latestSeenAttachRevision = Math.max(
        Number(payload.attach_revision || 0),
        Number(previousPayload?.attach_revision || 0),
        Number(route2LastAttachAttemptRevisionRef.current || 0),
      );
      if ((payload.client_attach_revision || 0) >= latestSeenAttachRevision) {
        mobilePendingAttachRevisionRef.current = 0;
      }
    }
    if (typeof payload.committed_playhead_seconds === "number") {
      committedPlayheadSecondsRef.current = payload.committed_playhead_seconds;
      setCommittedPlayheadSeconds(payload.committed_playhead_seconds);
    }
    if (typeof payload.actual_media_element_time_seconds === "number") {
      actualMediaElementTimeRef.current = payload.actual_media_element_time_seconds;
      setActualMediaElementTime(payload.actual_media_element_time_seconds);
    }
    if (payload.pending_target_seconds != null) {
      mobileSeekPendingRef.current = true;
      requestedTargetSecondsRef.current = payload.pending_target_seconds;
      setRequestedTargetSeconds(payload.pending_target_seconds);
      if (pendingSeekPhaseRef.current === "idle") {
        pendingSeekPhaseRef.current = "preparing";
        setPendingSeekPhase("preparing");
      }
    }
    if (payload.last_error && payload.state === "failed") {
      applyMobileLifecycleStatus("fatal");
      clearOptimizedPlaybackPending();
      setPlaybackError(payload.last_error);
      setPlaybackStatus(`${browserStreamLabelTitle} failed`);
      return;
    }
    if (payload.state === "failed") {
      applyMobileLifecycleStatus("fatal");
      clearOptimizedPlaybackPending();
      setPlaybackStatus(`${browserStreamLabelTitle} failed`);
      return;
    }
    if (isRoute2SessionPayload(payload)) {
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
    if (mobileLifecycleStateRef.current === "background-suspended") {
      return;
    }
    if (mobileLifecycleStateRef.current === "resuming" || mobileLifecycleStateRef.current === "recovering") {
      return;
    }
    if (payload.state === "queued") {
      setPlaybackStatus(`${browserPlaybackLabelTitle} queued`);
      return;
    }
    if (payload.state === "retargeting") {
      setPlaybackStatus("Preparing target playback");
      return;
    }
    if (payload.state === "preparing") {
      setPlaybackStatus(`Preparing ${browserPlaybackLabel}`);
      return;
    }
    if (payload.state === "ready") {
      setMobileLifecycleStateValue("attached");
      setPlaybackStatus(browserStreamLabelTitle);
      return;
    }
    setPlaybackStatus(browserPlaybackLabelTitle);
  }

  function resolveAttachedManifestEndSeconds(payload = mobileSessionRef.current) {
    if (isRoute2SessionPayload(payload)) {
      return Math.max(payload?.ready_end_seconds || 0, 0);
    }
    // `ready_end_seconds` can extend beyond the currently attached VOD slice,
    // so track the actual manifest tail when deciding whether we must reattach.
    if (typeof payload?.manifest_end_seconds === "number") {
      return Math.max(payload.manifest_end_seconds, 0);
    }
    return Math.max(payload?.ready_end_seconds || 0, 0);
  }

  function resolveCurrentManifestPosition(payload = mobileSessionRef.current) {
    const video = videoRef.current;
    return Math.max(
      video?.currentTime || 0,
      actualMediaElementTimeRef.current || 0,
      committedPlayheadSecondsRef.current || 0,
      payload?.target_position_seconds || 0,
    );
  }

  function maybeRefreshAttachedMobileManifest(payload) {
    if (isRoute2SessionPayload(payload)) {
      return false;
    }
    if (
      !payload?.playback_commit_ready
      || !attachedOptimizedManifestUrlRef.current
      || mobileSeekPendingRef.current
    ) {
      return false;
    }
    const manifestRevision = payload.manifest_revision || String(payload.epoch);
    if (manifestRevision === mobileAttachedManifestRevisionRef.current) {
      return false;
    }
    const video = videoRef.current;
    const currentPosition = resolveCurrentManifestPosition(payload);
    const remainingAttachedRunway = Math.max(0, mobileAttachedManifestEndRef.current - currentPosition);
    const shouldRefreshSlice =
      !video
      || video.paused
      || !mobilePlayerCanPlayRef.current
      || payload.stalled_recovery_needed
      || payload.starvation_risk
      || remainingAttachedRunway <= SESSION_MANIFEST_REFRESH_RUNWAY_SECONDS;
    if (!shouldRefreshSlice) {
      return false;
    }
    armMobileManifestAttachment(payload, {
      autoplay: Boolean(video && !video.paused),
      targetPosition: currentPosition,
      forceReattach: true,
      preserveAuthority: true,
      resetSeekPreparation: true,
    });
    return true;
  }

  function armMobileManifestAttachment(
    payload,
    {
      autoplay = false,
      targetPosition = null,
      forceReattach = false,
      preserveAuthority = false,
      resetSeekPreparation = false,
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
    mobileAwaitingTargetSeekRef.current =
      (targetPosition != null ? targetPosition : payload.target_position_seconds) > 0.5;
    mobileFrameReadyRef.current = false;
    mobileFrameProbePendingRef.current = false;
    mobileReadinessGenerationRef.current += 1;
    mobilePlayerCanPlayRef.current = false;
    mobileWarmupProbeActiveRef.current = false;
    mobileWarmupPlaybackObservedRef.current = false;
    mobileWarmupStartPositionRef.current = 0;
    mobileRetargetTransitionRef.current = false;
    if (route2Session) {
      const attachRevision = Number(payload.attach_revision || 0);
      route2LastAttachAttemptAtRef.current = Date.now();
      route2LastAttachAttemptRevisionRef.current = attachRevision;
      mobileClientAttachRevisionRef.current = Math.min(
        mobileClientAttachRevisionRef.current,
        attachRevision,
      );
      if (mobilePendingAttachRevisionRef.current > attachRevision) {
        mobilePendingAttachRevisionRef.current = attachRevision;
      }
    }
    if (resetSeekPreparation) {
      mobileSeekPendingRef.current = false;
      pendingSeekPhaseRef.current = "idle";
      setPendingSeekPhase("idle");
    }
    if (!preserveAuthority) {
      mobileLastStablePositionRef.current = 0;
      committedPlayheadSecondsRef.current = 0;
      actualMediaElementTimeRef.current = 0;
      setCommittedPlayheadSeconds(0);
      setActualMediaElementTime(0);
    }
    setMobilePlayerCanPlay(false);
    applyMobileLifecycleStatus(forceReattach ? "recovering" : "attached");
    if (!forceReattach) {
      setMobileFrozenFrameUrl("");
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
  }

  function route2AttachmentNeedsReattach(payload = mobileSessionRef.current) {
    if (!isRoute2AttachReady(payload)) {
      return false;
    }
    const authorityRevision = String(payload.attach_revision || 0);
    const authorityRevisionNumber = Number(payload.attach_revision || 0);
    const authorityIdentity = resolveSessionAttachmentIdentity(payload);
    const authorityChanged = (
      !attachedOptimizedManifestUrlRef.current
      || mobileAttachedManifestRevisionRef.current !== authorityRevision
      || mobileAttachedEpochRef.current !== authorityIdentity
    );
    if (authorityChanged) {
      return true;
    }
    return (
      Number(payload.client_attach_revision || 0) < authorityRevisionNumber
      && route2LastAttachAttemptRevisionRef.current === authorityRevisionNumber
      && Date.now() - route2LastAttachAttemptAtRef.current >= ROUTE2_ATTACH_RETRY_MS
    );
  }

  function maybeAttachRoute2Authority(
    payload,
    {
      autoplay = false,
      targetPosition = null,
      preserveAuthority = true,
      resetSeekPreparation = true,
    } = {},
  ) {
    if (!isRoute2AttachReady(payload) || !route2AttachmentNeedsReattach(payload)) {
      return false;
    }
    armMobileManifestAttachment(payload, {
      autoplay,
      targetPosition: targetPosition != null ? targetPosition : resolveMobileAuthorityPosition(payload),
      forceReattach: Boolean(attachedOptimizedManifestUrlRef.current),
      preserveAuthority,
      resetSeekPreparation,
    });
    return true;
  }

  function completeRoute2LocalTargetTransition(payload, targetPosition) {
    syncMobilePlaybackState(payload);
    const video = videoRef.current;
    pendingSeekPhaseRef.current = "committing";
    setPendingSeekPhase("committing");
    if (!video) {
      finalizeRetargetVisibility(null, {
        resumePlayback: mobileResumeAfterReadyRef.current,
        committedPosition: targetPosition,
      });
      return;
    }
    if (Math.abs((video.currentTime || 0) - targetPosition) <= 0.25 && video.readyState >= 2) {
      finalizeRetargetVisibility(video, {
        resumePlayback: mobileResumeAfterReadyRef.current,
        committedPosition: targetPosition,
      });
      return;
    }
    mobileAwaitingTargetSeekRef.current = true;
    try {
      video.currentTime = targetPosition;
    } catch {
      finalizeRetargetVisibility(video, {
        resumePlayback: mobileResumeAfterReadyRef.current,
        committedPosition: targetPosition,
      });
      return;
    }
    actualMediaElementTimeRef.current = targetPosition;
    setActualMediaElementTime(targetPosition);
    setPlaybackPosition(targetPosition);
  }

  function finalizeRetargetVisibility(video, { resumePlayback, committedPosition = null } = {}) {
    mobileRetargetTransitionRef.current = false;
    setMobileFrozenFrameUrl("");
    clearOptimizedPlaybackPending();
    setPlaybackError("");
    setSeekNotice("");
    setPlaybackStatus(browserStreamLabelTitle);
    const nextCommittedPosition =
      committedPosition != null
        ? committedPosition
        : (video?.currentTime || mobileLastStablePositionRef.current || 0);
    mobileLastStablePositionRef.current = nextCommittedPosition;
    committedPlayheadSecondsRef.current = nextCommittedPosition;
    setCommittedPlayheadSeconds(nextCommittedPosition);
    mobilePendingTargetRef.current = null;
    mobileSeekPendingRef.current = false;
    pendingSeekPhaseRef.current = "idle";
    setPendingSeekPhase("idle");
    requestedTargetSecondsRef.current = nextCommittedPosition;
    setRequestedTargetSeconds(nextCommittedPosition);
    mobileResumeAfterReadyRef.current = false;
    mobilePlayerCanPlayRef.current = true;
    setMobilePlayerCanPlay(true);
    setMobileLifecycleStateValue("attached");
    if (resumePlayback && video?.paused) {
      video.play().catch((requestError) => {
        const normalized = (requestError?.message || "").toLowerCase();
        if (
          normalized.includes("gesture") ||
          normalized.includes("notallowed") ||
          normalized.includes("denied") ||
          normalized.includes("not allowed")
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
    mobileAttachedEpochRef.current = payload.epoch;
    const video = videoRef.current;
    const targetPosition =
      requestedTargetSecondsRef.current != null
        ? requestedTargetSecondsRef.current
        : mobilePendingTargetRef.current != null
        ? mobilePendingTargetRef.current
        : payload.target_position_seconds;
    const shouldResume = mobileResumeAfterReadyRef.current;
    const manifestRevision = payload.manifest_revision || String(payload.epoch);
    const sessionManifestUrl = buildSessionManifestUrl(payload.manifest_url, manifestRevision);
    if (attachedOptimizedManifestUrlRef.current !== sessionManifestUrl) {
      pendingSeekPhaseRef.current = "committing";
      setPendingSeekPhase("committing");
      armMobileManifestAttachment(payload, {
        autoplay: shouldResume,
        targetPosition,
        forceReattach: true,
        preserveAuthority: true,
      });
      return;
    }
    setPlaybackError("");
    setPlaybackStatus("Preparing target playback");
    pendingSeekPhaseRef.current = "committing";
    setPendingSeekPhase("committing");
    if (!video) {
      finalizeRetargetVisibility(null, {
        resumePlayback: false,
        committedPosition: targetPosition,
      });
      return;
    }
    if (targetPosition <= 0.5 && video.readyState >= 2) {
      finalizeRetargetVisibility(video, {
        resumePlayback: shouldResume,
        committedPosition: targetPosition,
      });
      return;
    }
    if (Math.abs((video.currentTime || 0) - targetPosition) <= 0.25 && video.readyState >= 2) {
      finalizeRetargetVisibility(video, {
        resumePlayback: shouldResume,
        committedPosition: targetPosition,
      });
      return;
    }
    mobileAwaitingTargetSeekRef.current = true;
    video.currentTime = targetPosition;
    actualMediaElementTimeRef.current = targetPosition;
    setActualMediaElementTime(targetPosition);
    setPlaybackPosition(targetPosition);
  }

  function resolveMobileCommittedPosition(payload = mobileSessionRef.current) {
    if (committedPlayheadSecondsRef.current > 0) {
      return committedPlayheadSecondsRef.current;
    }
    if (payload?.committed_playhead_seconds > 0) {
      return payload.committed_playhead_seconds;
    }
    if (payload?.last_stable_position_seconds > 0) {
      return payload.last_stable_position_seconds;
    }
    return 0;
  }

  function resolveMobileAuthorityPosition(payload = mobileSessionRef.current) {
    if (requestedTargetSecondsRef.current != null && pendingSeekPhaseRef.current !== "idle") {
      return requestedTargetSecondsRef.current;
    }
    if (committedPlayheadSecondsRef.current > 0) {
      return committedPlayheadSecondsRef.current;
    }
    if (actualMediaElementTimeRef.current > 0) {
      return actualMediaElementTimeRef.current;
    }
    if (payload?.last_stable_position_seconds > 0) {
      return payload.last_stable_position_seconds;
    }
    return payload?.target_position_seconds || 0;
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
      const response = await apiRequest(
        activeSession.heartbeat_url || `${browserPlaybackSessionRoot}/sessions/${activeSession.session_id}/heartbeat`,
        {
        method: "POST",
        data: payload,
        },
      );
      syncMobilePlaybackState(response);
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

  function maybeAcknowledgeRoute2Attachment({ playing = null, force = false } = {}) {
    const activeSession = mobileSessionRef.current;
    if (!isRoute2AttachReady(activeSession)) {
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

  function maybeStartRoute2SupplyRecovery(payload) {
    if (!isRoute2SessionPayload(payload) || !payload?.stalled_recovery_needed) {
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
        payload = await apiRequest(
          activeSession.status_url || `${browserPlaybackSessionRoot}/sessions/${activeSession.session_id}`,
        );
      } catch {
        const recoveryTarget = resolveMobileCommittedPosition(activeSession);
        payload = await apiRequest(`${browserPlaybackSessionRoot}/sessions`, {
          method: "POST",
          data: {
            item_id: Number(itemId),
            profile: activeSession.profile || "mobile_1080p",
            start_position_seconds: recoveryTarget,
            ...(explicitRoute2Session ? { engine_mode: "route2" } : {}),
          },
        });
      }
      if (payload.state === "failed" || payload.state === "expired" || payload.state === "stopped") {
        const recoveryTarget = resolveMobileCommittedPosition(payload);
        payload = await apiRequest(`${browserPlaybackSessionRoot}/sessions`, {
          method: "POST",
          data: {
            item_id: Number(itemId),
            profile: payload.profile || activeSession.profile || "mobile_1080p",
            start_position_seconds: recoveryTarget,
            ...((isRoute2SessionPayload(payload) || explicitRoute2Session) ? { engine_mode: "route2" } : {}),
          },
        });
      }
      syncMobilePlaybackState(payload);
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
        const payload = await apiRequest(
          mobileSessionRef.current?.status_url || `${browserPlaybackSessionRoot}/sessions/${sessionId}`,
        );
        if (
          pollToken !== mobilePollTokenRef.current ||
          currentItemIdRef.current !== itemId
        ) {
          return;
        }
        syncMobilePlaybackState(payload);
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

  async function ensureMobileSessionReady(payload, { autoplay = false, targetPosition = null } = {}) {
    syncMobilePlaybackState(payload);
    if (payload.last_error && payload.state === "failed") {
      return;
    }
    if (isRoute2SessionPayload(payload)) {
      if (isRoute2AttachReady(payload)) {
        armMobileManifestAttachment(payload, {
          autoplay,
          targetPosition: targetPosition != null ? targetPosition : resolveRoute2AttachPosition(payload),
          resetSeekPreparation: true,
        });
        return;
      }
      mobileAutoplayPendingRef.current = autoplay;
      mobilePendingTargetRef.current =
        targetPosition != null ? targetPosition : resolveRoute2AttachPosition(payload);
      scheduleMobilePlaybackPoll(
        payload.session_id,
        Math.max(1000, Math.round((payload.status_poll_seconds || 1) * 1000)),
      );
      return;
    }
    if (payload.playback_commit_ready) {
      armMobileManifestAttachment(payload, {
        autoplay,
        targetPosition,
        resetSeekPreparation: true,
      });
      return;
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
  }

  async function startMobileOptimizedPlayback({ autoplay = true, playbackMode = "lite" } = {}) {
    const flowGeneration = playbackFlowRef.current;
    stopMobilePlaybackPolling();
    const targetPosition = Math.max(
      0,
      requestedTargetSecondsRef.current != null
        ? requestedTargetSecondsRef.current
        : browserStartPositionRef.current || 0,
    );
    const payload = await apiRequest(`${browserPlaybackSessionRoot}/sessions`, {
      method: "POST",
      data: {
        item_id: Number(itemId),
        profile: browserPlaybackProfile,
        start_position_seconds: targetPosition,
        playback_mode: getPlaybackMode(playbackMode),
      },
    });
    if (flowGeneration !== playbackFlowRef.current || currentItemIdRef.current !== itemId) {
      releasePlaybackSession(
        payload.stop_url,
        `${browserPlaybackSessionRoot}/sessions/${payload.session_id}/stop`,
      );
      return null;
    }
    return ensureMobileSessionReady(payload, {
      autoplay,
      targetPosition,
    });
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
      || (video?.currentTime || 0);
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
      if (Math.abs((video.currentTime || 0) - stablePosition) > 0.25) {
        try {
          video.currentTime = stablePosition;
        } catch {
          // Keep the current element time if Safari refuses this stabilizing rewind.
        }
      }
      actualMediaElementTimeRef.current = stablePosition;
      setActualMediaElementTime(stablePosition);
    }
    const payload = await apiRequest(activeSession.seek_url, {
      method: "POST",
      data: {
        target_position_seconds: targetPosition,
        last_stable_position_seconds: stablePosition,
        playing_before_seek: resumeAfterReady,
      },
    });
    syncMobilePlaybackState(payload);
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

  async function restoreBrowserPlaybackSource({ autoplay = false } = {}) {
    const flowId = playbackFlowRef.current;
    browserPlayRequestedRef.current = autoplay;
    setPlaybackError("");
    setSeekNotice("");
    setPlaybackStatus("Checking playback compatibility");
    browserStartPositionRef.current = resumableStartPosition > 0 ? resumableStartPosition : 0;
    requestedTargetSecondsRef.current = browserStartPositionRef.current;
    setRequestedTargetSeconds(browserStartPositionRef.current);

    const latestPlayback = await apiRequest(buildPlaybackDecisionPath(forceHlsRef.current));
    if (flowId !== playbackFlowRef.current || currentItemIdRef.current !== itemId) {
      return;
    }
    syncPlaybackState(latestPlayback);
    await startMobileOptimizedPlayback({
      autoplay,
      playbackMode: playbackModeIntentRef.current,
    });
  }

  async function restoreActiveBrowserPlaybackSession() {
    const payload = await apiRequest(`${browserPlaybackSessionRoot}/items/${itemId}/active`);
    if (!payload) {
      return false;
    }
    setPlaybackError("");
    setSeekNotice("");
    setBrowserResumeModalOpen(false);
    syncMobilePlaybackState(payload);
    const targetPosition = resolveMobileAuthorityPosition(payload);
    if (isRoute2SessionPayload(payload)) {
      if (isRoute2AttachReady(payload)) {
        armMobileManifestAttachment(payload, {
          autoplay: false,
          targetPosition,
          resetSeekPreparation: true,
        });
      } else {
        setOptimizedPlaybackPending(true);
        scheduleMobilePlaybackPoll(
          payload.session_id,
          Math.max(1000, Math.round((payload.status_poll_seconds || 1) * 1000)),
        );
      }
      return true;
    }
    if (payload.playback_commit_ready) {
      armMobileManifestAttachment(payload, {
        autoplay: false,
        targetPosition,
        resetSeekPreparation: true,
      });
    } else {
      setOptimizedPlaybackPending(true);
      scheduleMobilePlaybackPoll(
        payload.session_id,
        Math.max(1000, Math.round((payload.status_poll_seconds || 1) * 1000)),
      );
    }
    return true;
  }

  function requestStopBrowserPlayback() {
    setBrowserResumeModalOpen(false);
    setBrowserStopModalOpen(true);
  }

  async function confirmStopBrowserPlayback() {
    const activeSession = mobileSessionRef.current;
    setBrowserStopModalOpen(false);
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
      await apiRequest(
        activeSession.stop_url || `${browserPlaybackSessionRoot}/sessions/${activeSession.session_id}/stop`,
        { method: "POST" },
      );
    } catch (requestError) {
      setPlaybackError(requestError.message || `Failed to stop ${browserPlaybackLabelTitle}`);
    }
  }

  function buildPlaybackDecisionPath(forceHls = false) {
    return forceHls
      ? `/api/playback/${itemId}?force_hls=1`
      : `/api/playback/${itemId}`;
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
        const current = await apiRequest(buildPlaybackDecisionPath(forceHls));
        if (
          flowId !== playbackFlowRef.current ||
          currentItemIdRef.current !== itemId ||
          pollGeneration !== playbackPollGenerationRef.current
        ) {
          return;
        }
        const waitForComplete = iosMobile && optimizedVodRequiredRef.current;
        const readyForAttach =
          current.manifest_ready &&
          current.hls_url &&
          (!waitForComplete ||
            current.manifest_complete ||
            (current.generated_duration_seconds || 0) >= IOS_OPTIMIZED_READY_SECONDS);
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
          current.manifest_complete ||
          ["busy", "completed", "failed", "disabled"].includes(current.transcode_status)
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
      payload.manifest_ready &&
      payload.hls_url &&
      (!waitForComplete ||
        payload.manifest_complete ||
        (payload.generated_duration_seconds || 0) >= IOS_OPTIMIZED_READY_SECONDS);
    forceHlsRef.current = shouldForceHls;

    const startPayload = await apiRequest(`/api/playback/${itemId}/start`, {
      method: "POST",
      data: { force_hls: shouldForceHls },
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
      !startPayload.manifest_complete &&
      !["busy", "completed", "failed", "disabled"].includes(startPayload.transcode_status)
    ) {
      startPlaybackPolling(shouldForceHls, flowId);
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function loadDetails() {
      playbackFlowRef.current += 1;
      currentItemIdRef.current = itemId;
      setLoading(true);
      setError("");
      setPlaybackError("");
      setSeekNotice("");
      resetIosExternalAppState();
      setGlobalHiddenActionMessage("");
      setGlobalHiddenActionError("");
      setPlaybackStatus("Checking playback compatibility");
      setStreamSource(null);
      setPlayback(null);
      setMobileSession(null);
      setPlaybackPosition(0);
      mobilePlayerCanPlayRef.current = false;
      mobileWarmupProbeActiveRef.current = false;
      mobileWarmupPlaybackObservedRef.current = false;
      mobileWarmupStartPositionRef.current = 0;
      mobileRetargetTransitionRef.current = false;
      mobileLastStablePositionRef.current = 0;
      setMobilePlayerCanPlay(false);
      setMobileFrozenFrameUrl("");
      setPlayerMeasuredDuration(0);
      clearOptimizedPlaybackPending();
      setDesktopPlayback(null);
      setVlcLaunchPending(false);
      setVlcLaunchMessage("");
      setVlcLaunchError("");
      fallbackAttemptedRef.current = false;
      forceHlsRef.current = false;
      optimizedVodRequiredRef.current = false;
      resumeAppliedRef.current = false;
      pendingResumeRef.current = 0;
      browserStartPositionRef.current = 0;
      setBrowserResumeModalOpen(false);
      clearPlaybackResources();
      resetMobilePlaybackState();
      if (videoRef.current) {
        videoRef.current.pause();
        videoRef.current.removeAttribute("src");
        videoRef.current.load();
      }
      try {
        const itemPayload = await apiRequest(`/api/library/item/${itemId}`);
        if (cancelled) {
          return;
        }
        setItem(itemPayload);
        if (itemPayload.hidden_globally) {
          setProgress(null);
          setPlayback(null);
          setDesktopPlayback(null);
          setLoading(false);
          return;
        }
        if (itemPayload.hidden_for_user) {
          setProgress(null);
          setPlayback(null);
          setDesktopPlayback(null);
          setLoading(false);
          return;
        }
        const progressPayload = await apiRequest(`/api/progress/${itemId}`);
        if (cancelled) {
          return;
        }
        setProgress(progressPayload);
        let playbackPayload = null;
        if (iosMobile) {
          setPlayback(null);
        } else {
          playbackPayload = await apiRequest(`/api/playback/${itemId}`);
          if (cancelled) {
            return;
          }
          syncPlaybackState(playbackPayload);
        }
        if (desktopPlatform) {
          try {
            const desktopPayload = await apiRequest(
              `/api/desktop-playback/${itemId}?platform=${desktopPlatform}&same_host=${localDevLoopback ? "1" : "0"}`,
            );
            if (!cancelled) {
              setDesktopPlayback(desktopPayload);
            }
          } catch (desktopError) {
            if (!cancelled) {
              setVlcLaunchError(desktopError.message || "Failed to resolve desktop VLC playback");
            }
          }
        }

        const restoredActiveBrowserPlayback = await restoreActiveBrowserPlaybackSession();
        if (cancelled) {
          return;
        }
        if (restoredActiveBrowserPlayback && iosMobile) {
          return;
        }
      } catch (requestError) {
        if (!cancelled) {
          setError(requestError.message || "Failed to load media item");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    loadDetails();
    return () => {
      cancelled = true;
      playbackFlowRef.current += 1;
      currentItemIdRef.current = itemId;
      clearPlaybackResources();
      resetMobilePlaybackState();
    };
  }, [desktopPlatform, iosMobile, itemId, localDevLoopback, detailRefreshKey]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !streamSource) {
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
        if (isRoute2SessionPayload(mobileSessionRef.current)) {
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
          currentPlayback?.mode === "hls" &&
          !currentPlayback?.manifest_complete &&
          currentPlayback?.transcode_status !== "failed" &&
          currentPlayback?.transcode_status !== "disabled";
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

    video.addEventListener("error", handlePlaybackFailure);

    function maybeAutoplay() {
      if (!browserPlayRequestedRef.current) {
        return;
      }
      browserPlayRequestedRef.current = false;
      video.play().catch((requestError) => {
        const message = requestError?.message || "";
        const normalized = message.toLowerCase();
        const looksLikeGestureLoss =
          iosMobile &&
          optimizedVodRequiredRef.current &&
          (normalized.includes("gesture") ||
            normalized.includes("notallowed") ||
            normalized.includes("denied") ||
            normalized.includes("not allowed"));
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

    if (streamSource.mode === "direct") {
      video.addEventListener("loadedmetadata", maybeAutoplay, { once: true });
      video.src = streamSource.url;
      video.load();
      return () => {
        video.removeEventListener("error", handlePlaybackFailure);
        video.removeEventListener("loadedmetadata", maybeAutoplay);
      };
    }

    const nativeHlsSupport =
      video.canPlayType("application/vnd.apple.mpegurl") ||
      video.canPlayType("application/x-mpegURL");

    const useManualMobileAutoplay = iosMobile && Boolean(mobileSessionRef.current);

    if (nativeHlsSupport) {
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

    if (!Hls.isSupported()) {
      setPlaybackError("This browser cannot play HLS fallback streams");
      return () => {
        video.removeEventListener("error", handlePlaybackFailure);
      };
    }

    const hls = new Hls();
    hlsRef.current = hls;
    hls.loadSource(streamSource.url);
    hls.attachMedia(video);
    hls.on(Hls.Events.MANIFEST_PARSED, maybeAutoplay);
    hls.on(Hls.Events.ERROR, (_event, data) => {
      if (data.fatal) {
        if (isRoute2SessionPayload(mobileSessionRef.current)) {
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

  const resumePosition = useMemo(() => {
    if (!progress || progress.completed) {
      return 0;
    }
    return progress.position_seconds || item?.resume_position_seconds || 0;
  }, [item, progress]);

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
    if (!resumePosition) {
      return 0;
    }
    if (fullDuration > 0 && resumePosition >= fullDuration - COMPLETION_GRACE_SECONDS) {
      return 0;
    }
    return resumePosition;
  }, [fullDuration, resumePosition]);

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

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !item) {
      return undefined;
    }
    playbackOpenedReportedRef.current = false;

    function updatePlayerMetrics() {
      const actualTime = video.currentTime || 0;
      actualMediaElementTimeRef.current = actualTime;
      setActualMediaElementTime(actualTime);
      const displayTime =
        mobileSessionRef.current &&
        pendingSeekPhaseRef.current !== "idle" &&
        requestedTargetSecondsRef.current != null
          ? requestedTargetSecondsRef.current
          : actualTime;
      setPlaybackPosition(displayTime);
      const currentPlayback = playbackStateRef.current;
      const measuredDuration = readFiniteDuration(video);
      const shouldIgnoreMeasuredDuration =
        currentPlayback?.mode === "hls" &&
        (!currentPlayback?.manifest_complete || !currentPlayback?.expected_duration_seconds);
      setPlayerMeasuredDuration(shouldIgnoreMeasuredDuration ? 0 : measuredDuration);
    }

    async function pushProgress(completed = false) {
      const persistedDuration = fullDuration > 0
        ? fullDuration
        : readFiniteDuration(video);
      if (!persistedDuration && video.currentTime <= 0) {
        return;
      }
      const playbackMode =
        iosMobile && mobileSessionRef.current
          ? "experimental_playback"
          : "browser_playback";
      const payload = await apiRequest(`/api/progress/${item.id}`, {
        method: "POST",
        data: {
          position_seconds: video.currentTime,
          duration_seconds: persistedDuration || null,
          completed,
          playback_mode: playbackMode,
        },
      });
      setProgress(payload);
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
      if (!navigator.sendBeacon || (!persistedDuration && video.currentTime <= 0)) {
        flushProgress(completed);
        return;
      }
      const playbackMode =
        iosMobile && mobileSessionRef.current
          ? "experimental_playback"
          : "browser_playback";
      const body = JSON.stringify({
        position_seconds: video.currentTime,
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
      const payload = await apiRequest(`/api/progress/${item.id}/event`, {
        method: "POST",
        data: {
          event_type: eventType,
          playback_mode: resolvePlaybackTrackingMode(),
          position_seconds: video.currentTime,
          duration_seconds: persistedDuration || null,
        },
      });
      setProgress(payload);
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
        playback?.mode === "hls" &&
        !playback?.manifest_complete &&
        safeResume > availableDuration - SEEK_HEADROOM_SECONDS
      ) {
        pendingResumeRef.current = safeResume;
        setSeekNotice(`Resuming at ${formatDuration(safeResume)} once that part is prepared.`);
        return;
      }
      video.currentTime = safeResume;
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
        video.currentTime > 0.5 &&
        (!iosMobile
          || !mobileSessionRef.current
          || (mobilePlayerCanPlayRef.current
            && !mobileSeekPendingRef.current
            && !mobileWarmupProbeActiveRef.current));
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
        !iosMobile ||
        !mobileSessionRef.current ||
        mobileSeekPendingRef.current ||
        !mobilePlayerCanPlayRef.current ||
        video.paused ||
        mobileRecoveryInFlightRef.current
      ) {
        return;
      }
      const currentSession = mobileSessionRef.current;
      const bufferedAhead = bufferedRunwaySeconds();
      const backendAhead = currentSession?.ahead_runway_seconds || 0;
      const refillInProgress = Boolean(currentSession?.refill_in_progress);
      const hardStarvation =
        currentSession?.stalled_recovery_needed ||
        currentSession?.starvation_risk ||
        (backendAhead <= 3 && bufferedAhead <= 0.75 && !refillInProgress);
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
          !mobileSessionRef.current ||
          mobileSeekPendingRef.current ||
          video.paused ||
          mobileRecoveryInFlightRef.current
        ) {
          return;
        }
        const latestSession = mobileSessionRef.current;
        const latestBufferedAhead = bufferedRunwaySeconds();
        const shouldRecover =
          Boolean(latestSession?.stalled_recovery_needed) ||
          Boolean(latestSession?.starvation_risk) ||
          ((latestSession?.ahead_runway_seconds || 0) <= 2 && latestBufferedAhead <= 0.5);
        if (!shouldRecover) {
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
      maybeAcknowledgeRoute2Attachment({ playing: !video.paused, force: true });
      if (mobilePendingTargetRef.current != null && mobileSessionRef.current) {
        const pendingTarget = mobilePendingTargetRef.current;
        video.currentTime = pendingTarget;
        setPlaybackPosition(pendingTarget);
        actualMediaElementTimeRef.current = pendingTarget;
        setActualMediaElementTime(pendingTarget);
        mobileAwaitingTargetSeekRef.current = pendingTarget > 0.5;
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
            mobilePendingTargetRef.current != null ? mobilePendingTargetRef.current : video.currentTime;
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
                normalized.includes("gesture") ||
                normalized.includes("notallowed") ||
                normalized.includes("denied") ||
                normalized.includes("not allowed")
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
              normalized.includes("gesture") ||
              normalized.includes("notallowed") ||
              normalized.includes("denied") ||
              normalized.includes("not allowed")
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
      maybeAcknowledgeRoute2Attachment({ playing: !video.paused });
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
      maybeAcknowledgeRoute2Attachment({ playing: !video.paused });
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
      const currentTime = video.currentTime || 0;
      mobileAwaitingTargetSeekRef.current = false;
      actualMediaElementTimeRef.current = currentTime;
      setActualMediaElementTime(currentTime);
      if (
        pendingSeekPhaseRef.current === "committing" &&
        requestedTargetSecondsRef.current != null &&
        Math.abs(currentTime - requestedTargetSecondsRef.current) <= 0.75
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
          committedPosition: currentTime,
        });
        return;
      }
      if (!mobileSeekPendingRef.current && !mobileRetargetTransitionRef.current) {
        mobileLastStablePositionRef.current = currentTime;
        committedPlayheadSecondsRef.current = currentTime;
        setCommittedPlayheadSeconds(currentTime);
        requestedTargetSecondsRef.current = currentTime;
        setRequestedTargetSeconds(currentTime);
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
      if (currentTime > 0.5) {
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
	          mobileLastStablePositionRef.current = video.currentTime || 0;
	          committedPlayheadSecondsRef.current = video.currentTime || 0;
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
              : playbackStateRef.current?.mode === "hls" &&
                  !playbackStateRef.current?.manifest_complete &&
                  playbackStateRef.current?.transcode_status !== "completed"
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
      const targetPosition = video.currentTime || 0;
      const readyWindowEnd = Math.max((currentSession.ready_end_seconds || 0) - SEEK_HEADROOM_SECONDS, 0);
      if (
        targetPosition >= (currentSession.ready_start_seconds || 0) &&
        targetPosition <= readyWindowEnd
      ) {
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
        maybeAcknowledgeRoute2Attachment({ playing: true, force: true });
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
            : playbackStateRef.current?.mode === "hls" &&
                !playbackStateRef.current?.manifest_complete &&
                playbackStateRef.current?.transcode_status !== "completed"
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
    resumePosition,
    streamSource,
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
      playback?.mode === "hls" &&
      !playback?.manifest_complete &&
      pendingResume > availableDuration - SEEK_HEADROOM_SECONDS
    ) {
      return;
    }
    video.currentTime = pendingResume;
    setPlaybackPosition(pendingResume);
    pendingResumeRef.current = 0;
    resumeAppliedRef.current = true;
    setSeekNotice((current) => (
      current.startsWith("Resuming at ") ? "" : current
    ));
  }, [availableDuration, playback?.manifest_complete, playback?.mode, streamSource]);

  function launchBrowserPlaybackFrom(startPositionSeconds, playbackMode = "lite") {
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
    resetIosExternalAppState();
    forceHlsRef.current = false;
    optimizedVodRequiredRef.current = false;
    setPlayerMeasuredDuration(0);
    setPlaybackPosition(browserStartPositionRef.current);
    setOptimizedPlaybackPending(true);
    setBrowserResumeModalOpen(false);
    setBrowserStopModalOpen(false);
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
    startMobileOptimizedPlayback({
      autoplay: playbackModeIntentRef.current !== "full",
      playbackMode: playbackModeIntentRef.current,
    }).catch((requestError) => {
      clearOptimizedPlaybackPending();
      setPlaybackError(requestError.message || `Failed to start ${browserPlaybackLabel}`);
    });
  }

  function beginBrowserPlaybackFlow(playbackMode = "lite") {
    const video = videoRef.current;
    playbackModeIntentRef.current = getPlaybackMode(playbackMode);
    setPlaybackModeIntent(playbackModeIntentRef.current);
    setPlaybackError("");
    clearOptimizedPlaybackPending();
    resetIosExternalAppState();
    if (optimizedPlaybackPending || mobileSessionRef.current) {
      requestStopBrowserPlayback();
      return;
    }
    if (streamSource && video?.currentSrc) {
      browserPlayRequestedRef.current = false;
      video.play().catch((requestError) => {
        setPlaybackError(requestError.message || `Failed to start ${browserPlaybackLabel}`);
      });
      return;
    }
    if (resumableStartPosition > 0) {
      setBrowserResumeModalOpen(true);
      return;
    }
    launchBrowserPlaybackFrom(0, playbackModeIntentRef.current);
  }

  function handleStartLitePlayback() {
    beginBrowserPlaybackFlow("lite");
  }

  function handleStartFullPlayback() {
    beginBrowserPlaybackFlow("full");
  }

  function handleResumeBrowserPlayback() {
    launchBrowserPlaybackFrom(resumableStartPosition, playbackModeIntentRef.current);
  }

  function handleStartBrowserPlaybackFromBeginning() {
    launchBrowserPlaybackFrom(0, playbackModeIntentRef.current);
  }

  async function handleProviderReconnect() {
    if (
      providerReconnectPending
      || providerReconnectModal.provider !== "google_drive"
      || !providerReconnectModal.allowReconnect
      || !item
    ) {
      return;
    }
    setProviderReconnectPending(true);
    const currentUrl = new URL(window.location.href);
    currentUrl.searchParams.delete("googleDriveStatus");
    currentUrl.searchParams.delete("googleDriveMessage");
    const returnPath = `${currentUrl.pathname}${currentUrl.search}${currentUrl.hash}`;
    saveProviderAuthIntent({
      provider: "google_drive",
      actionType: providerReconnectModal.actionType || "desktop_vlc_handoff",
      mediaItemId: Number(item.id),
      platform: desktopPlayback?.platform || desktopPlatform || null,
      returnPath,
    });
    try {
      const payload = await apiRequest("/api/cloud-libraries/google/connect", {
        method: "POST",
        data: {
          return_path: returnPath,
        },
      });
      window.location.assign(payload.authorization_url);
    } catch (requestError) {
      setProviderReconnectPending(false);
      setProviderReconnectModal((current) => ({
        ...current,
        errorMessage: requestError.message || "Failed to start Google Drive reconnect.",
      }));
    }
  }

  async function handleOpenInVlc(options = {}) {
    const { isProviderRetry = false } = options;
    if (!item) {
      return;
    }
    if (!desktopPlayback) {
      setVlcLaunchError("Desktop VLC playback details are still loading.");
      return;
    }

    const hadBrowserPlaybackSession = Boolean(
      mobileSessionRef.current
      || optimizedPlaybackPending
      || streamSource
      || attachedOptimizedManifestUrlRef.current,
    );
    setVlcLaunchPending(true);
    setVlcLaunchError("");
    setVlcLaunchMessage("");
    if (hadBrowserPlaybackSession) {
      stopBrowserPlaybackForDesktopHandoff();
    }
    try {
      if (desktopPlayback.open_supported || desktopPlayback.same_host_launch || desktopPlayback.open_method === "spawn_vlc") {
        const payload = await apiRequest(`/api/desktop-playback/${item.id}/open`, {
          method: "POST",
          data: {
            platform: desktopPlayback.platform,
            same_host: localDevLoopback,
          },
        });
        setVlcLaunchMessage(payload.message || "VLC launched.");
        return;
      }

      if (desktopPlayback.handoff_supported) {
        const payload = await apiRequest(`/api/desktop-playback/${item.id}/handoff`, {
          method: "POST",
          data: {
            platform: desktopPlayback.platform,
            device_id: desktopDeviceId,
          },
        });
        if (!payload?.protocol_url) {
          throw new Error("Desktop VLC handoff did not return a launch URL.");
        }
        setVlcLaunchMessage(payload.message || "Launching installed VLC via the Elvern desktop opener.");
        window.location.assign(payload.protocol_url);
        return;
      }

      window.location.assign(desktopPlayback.playlist_url);
        setVlcLaunchMessage(
        desktopPlayback.used_backend_fallback
          ? "Downloaded a VLC playlist that falls back to a short-lived Elvern URL because no direct source mapping is configured for this desktop platform."
          : "Downloaded a VLC playlist that points at the mapped direct source for this desktop platform.",
      );
    } catch (requestError) {
      const providerAuthRequirement = getProviderAuthRequirement(requestError);
      if (providerAuthRequirement) {
        setVlcLaunchError("");
        setVlcLaunchMessage("");
        openProviderReconnectModal(providerAuthRequirement, "desktop_vlc_handoff");
        return;
      }
      setVlcLaunchError(requestError.message || "Failed to open VLC");
      if (hadBrowserPlaybackSession && !isProviderRetry) {
        restoreActiveBrowserPlaybackSession().catch(() => {
          // If the prior browser session cannot be restored, preserve the VLC handoff error only.
        });
      }
    } finally {
      setVlcLaunchPending(false);
    }
  }

  async function handleCopyVlcTarget() {
    if (!desktopPlayback?.vlc_target) {
      return;
    }
    if (!navigator.clipboard?.writeText) {
      setVlcLaunchError("Clipboard access is not available in this browser.");
      return;
    }
    try {
      await navigator.clipboard.writeText(desktopPlayback.vlc_target);
      setVlcLaunchMessage("VLC target copied.");
      setVlcLaunchError("");
    } catch (copyError) {
      setVlcLaunchError(copyError.message || "Failed to copy the VLC target");
    }
  }

  function retryIosExternalAppLaunch() {
    if (!iosAppLaunchUrl) {
      return;
    }
    window.location.assign(iosAppLaunchUrl);
  }

  async function handleCopyIosPlaybackUrl() {
    if (!iosAppPlaybackUrl) {
      return;
    }
    if (!navigator.clipboard?.writeText) {
      setIosAppLaunchError("Clipboard access is not available here. Use the short-lived playback URL field below.");
      return;
    }
    try {
      await navigator.clipboard.writeText(iosAppPlaybackUrl);
      setIosAppLaunchMessage(
        `Short-lived playback URL copied. Open ${iosAppTarget || "the external app"} and paste the URL there if Safari does not hand it off automatically.`,
      );
      setIosAppLaunchError("");
    } catch (copyError) {
      setIosAppLaunchError(copyError.message || "Failed to copy the short-lived playback URL");
    }
  }

  async function handleOpenInIosExternalApp(targetApp) {
    if (!item) {
      return;
    }
    const appLabel = targetApp === "infuse" ? "Infuse" : "VLC";
    clearOptimizedPlaybackPending();
    optimizedVodRequiredRef.current = false;
    playbackFlowRef.current += 1;
    attachedOptimizedManifestUrlRef.current = "";
    setIosAppLaunchPending(true);
    setIosAppLaunchError("");
    setIosAppLaunchMessage("");
    setIosAppLaunchUrl("");
    setIosAppPlaybackUrl("");
    setIosAppTarget(appLabel);
    setIosTransportDebug(null);
    try {
      const sessionPayload = await apiRequest(`/api/native-playback/${item.id}/session`, {
        method: "POST",
        data: {
          external_player: targetApp === "infuse" ? "infuse" : "vlc",
          client_name:
            targetApp === "infuse"
              ? "Elvern iOS Infuse Handoff"
              : "Elvern iOS VLC Handoff",
          requested_transport_mode: "single_best_path",
          caller_surface: detectIosExternalCallerSurface(),
          current_path_class: "unknown",
          trusted_network_context: false,
          allow_browser_fallback: true,
        },
      });
      if (!sessionPayload.stream_url) {
        throw new Error("External app handoff did not return a playback URL");
      }
      setIosTransportDebug(normalizeIosTransportDebug(sessionPayload));
      const successUrl =
        targetApp === "infuse"
          ? buildIosExternalAppCallbackUrl({ app: "infuse", result: "success" })
          : "";
      const errorUrl =
        targetApp === "infuse"
          ? buildIosExternalAppCallbackUrl({ app: "infuse", result: "error" })
          : "";
      const launchUrl =
        targetApp === "infuse"
          ? buildInfuseLaunchUrl(sessionPayload.stream_url, {
              successUrl,
              errorUrl,
            })
          : buildIosVlcLaunchUrl(sessionPayload.stream_url);
      stopBrowserPlaybackForDesktopHandoff(`Handing off to ${appLabel}`);
      setIosAppLaunchUrl(launchUrl);
      setIosAppPlaybackUrl(sessionPayload.stream_url);
      if (targetApp === "infuse") {
        saveIosExternalAppLaunchState({
          itemId,
          app: "infuse",
          launchUrl,
          playbackUrl: sessionPayload.stream_url,
        });
      }
      setIosAppLaunchMessage(
        targetApp === "infuse"
          ? "Trying to open Infuse with a short-lived Elvern playback URL. Infuse may require Infuse Pro for some formats, and if it cannot continue the handoff Elvern will bring you back here with the fallback URL still ready."
          : "Trying to open VLC with a short-lived original playback URL. This works best on fast home or local networks. Large remux or original files may pause remotely, so use Lite Playback or Full Playback on slower school or WAN connections.",
      );
      window.location.assign(launchUrl);
    } catch (requestError) {
      if (targetApp === "infuse") {
        clearIosExternalAppLaunchState({ itemId, app: "infuse" });
      }
      setIosTransportDebug(null);
      setIosAppLaunchError(requestError.message || `Failed to open ${appLabel}`);
    } finally {
      setIosAppLaunchPending(false);
    }
  }

  async function handleHideMovie() {
    if (!item || browserPlaybackSessionActive) {
      return;
    }
    setHiddenActionPending(true);
    setHiddenActionError("");
    setHiddenActionMessage("");
    try {
      const payload = await apiRequest(`/api/user-hidden-items/${item.id}`, {
        method: "POST",
      });
      stopBrowserPlaybackForDesktopHandoff("This movie is hidden for your account");
      setItem((current) => (current ? { ...current, hidden_for_user: true } : current));
      setHiddenActionMessage(
        payload.message || "This movie is hidden for your account. Restore it from Settings > Hidden for me.",
      );
    } catch (requestError) {
      setHiddenActionError(requestError.message || "Failed to hide this movie");
    } finally {
      setHiddenActionPending(false);
    }
  }

  async function handleShowMovieAgain() {
    if (!item) {
      return;
    }
    setHiddenActionPending(true);
    setHiddenActionError("");
    setHiddenActionMessage("");
    try {
      const payload = await apiRequest(`/api/user-hidden-items/${item.id}`, {
        method: "DELETE",
      });
      setHiddenActionMessage(payload.message || "This movie is visible again");
      setDetailRefreshKey((current) => current + 1);
    } catch (requestError) {
      setHiddenActionError(requestError.message || "Failed to show this movie again");
    } finally {
      setHiddenActionPending(false);
    }
  }

  async function handleHideMovieForEveryone() {
    if (!item || !isAdmin || browserPlaybackSessionActive) {
      return;
    }
    setGlobalHiddenActionPending(true);
    setGlobalHiddenActionError("");
    setGlobalHiddenActionMessage("");
    try {
      const payload = await apiRequest(`/api/admin/global-hidden-items/${item.id}`, {
        method: "POST",
      });
      setItem((current) => (current ? { ...current, hidden_globally: true } : current));
      setGlobalHiddenActionMessage(payload.message || "This movie is hidden for everyone");
    } catch (requestError) {
      setGlobalHiddenActionError(requestError.message || "Failed to hide this movie for everyone");
    } finally {
      setGlobalHiddenActionPending(false);
    }
  }

  async function handleShowMovieForEveryone() {
    if (!item || !isAdmin) {
      return;
    }
    setGlobalHiddenActionPending(true);
    setGlobalHiddenActionError("");
    setGlobalHiddenActionMessage("");
    try {
      const payload = await apiRequest(`/api/admin/global-hidden-items/${item.id}`, {
        method: "DELETE",
      });
      setGlobalHiddenActionMessage(payload.message || "This movie is visible again for everyone");
      setDetailRefreshKey((current) => current + 1);
    } catch (requestError) {
      setGlobalHiddenActionError(requestError.message || "Failed to show this movie again for everyone");
    } finally {
      setGlobalHiddenActionPending(false);
    }
  }

  if (loading) {
    return <LoadingView label="Loading player..." />;
  }

  if (error || !item) {
    return (
      <section className="page-section page-section--detail">
        <p className="form-error">{error || "Media item not found"}</p>
        <Link
          className="ghost-button ghost-button--inline"
          onClick={prepareLibraryReturnNavigation}
          state={libraryReturnLinkState}
          to={libraryReturnPath}
        >
          Back to library
        </Link>
      </section>
    );
  }

  const detailTitle = getMovieCardTitle(item);

  if (item.hidden_globally && isAdmin) {
    return (
      <section className="page-section page-section--detail">
        <div className="section-header">
          <div>
            <p className="eyebrow">Player</p>
            <h1>{detailTitle}</h1>
          </div>
          <Link
            className="ghost-button ghost-button--inline"
            onClick={prepareLibraryReturnNavigation}
            state={libraryReturnLinkState}
            to={libraryReturnPath}
          >
            Back to library
          </Link>
        </div>

        <div className="player-card hidden-item-state">
          <div className="hidden-item-state__copy">
            <p className="eyebrow">Admin only</p>
            <h2>This movie is hidden for everyone</h2>
            <p className="page-subnote">
              Regular users no longer see this movie in library browsing, search, continue watching, recently added, or normal detail access.
            </p>
            <div className="detail-list">
              {item.year ? <span>{item.year}</span> : null}
              {item.edition_label ? <span>{item.edition_label}</span> : null}
              <span>{formatBytes(item.file_size)}</span>
            </div>
          </div>
          <div className="player-actions">
            <button
              className="primary-button"
              disabled={globalHiddenActionPending}
              onClick={handleShowMovieForEveryone}
              type="button"
            >
              {globalHiddenActionPending ? "Restoring..." : "Show for everyone"}
            </button>
            <Link className="ghost-button ghost-button--inline" to="/settings">
              Open Hidden for everyone in Settings
            </Link>
            <Link
              className="ghost-button ghost-button--inline"
              onClick={prepareLibraryReturnNavigation}
              state={libraryReturnLinkState}
              to={libraryReturnPath}
            >
              Back to library
            </Link>
          </div>
          {globalHiddenActionError ? <p className="form-error">{globalHiddenActionError}</p> : null}
          {globalHiddenActionMessage ? <p className="page-note">{globalHiddenActionMessage}</p> : null}
        </div>
      </section>
    );
  }

  if (item.hidden_for_user) {
    return (
      <section className="page-section page-section--detail">
        <div className="section-header">
          <div>
            <p className="eyebrow">Player</p>
            <h1>{detailTitle}</h1>
          </div>
          <Link
            className="ghost-button ghost-button--inline"
            onClick={prepareLibraryReturnNavigation}
            state={libraryReturnLinkState}
            to={libraryReturnPath}
          >
            Back to library
          </Link>
        </div>

        <div className="player-card hidden-item-state">
          <div className="hidden-item-state__copy">
            <p className="eyebrow">Hidden</p>
            <h2>This movie is hidden for your account</h2>
            <p className="page-subnote">
              It stays hidden until you restore it from Settings &gt; Hidden for me, or use Show again here.
            </p>
            <div className="detail-list">
              {item.year ? <span>{item.year}</span> : null}
              {item.edition_label ? <span>{item.edition_label}</span> : null}
              <span>{formatBytes(item.file_size)}</span>
            </div>
          </div>
          <div className="player-actions">
            <button
              className="primary-button"
              disabled={hiddenActionPending}
              onClick={handleShowMovieAgain}
              type="button"
            >
              {hiddenActionPending ? "Restoring..." : "Show again"}
            </button>
            <Link className="ghost-button ghost-button--inline" to="/settings">
              Open Hidden for me in Settings
            </Link>
            <Link
              className="ghost-button ghost-button--inline"
              onClick={prepareLibraryReturnNavigation}
              state={libraryReturnLinkState}
              to={libraryReturnPath}
            >
              Back to library
            </Link>
          </div>
          {hiddenActionError ? <p className="form-error">{hiddenActionError}</p> : null}
          {hiddenActionMessage ? <p className="page-note">{hiddenActionMessage}</p> : null}
        </div>
      </section>
    );
  }

  const showIosExternalApps = iosMobile;
  const showInlinePlayer = !mobileSession || Boolean(streamSource && mobilePlayerCanPlay);
  const showMobileWarmupShell =
    Boolean(mobileSession) && (Boolean(streamSource) || Boolean(mobileFrozenFrameUrl)) && !mobilePlayerCanPlay;
  const showPlayerShell = showInlinePlayer || showMobileWarmupShell;
  const browserPlaybackSessionActive = Boolean(mobileSession) || optimizedPlaybackPending;
  const browserPlaybackPreparing = Boolean(mobileSession)
    ? !mobilePlayerCanPlay
    : optimizedPlaybackPending;
  const litePlaybackActive = browserPlaybackSessionActive && activePlaybackMode === "lite";
  const fullPlaybackActive = browserPlaybackSessionActive && activePlaybackMode === "full";
  const showMobilePreparingPlaceholder = isRoute2SessionPayload(mobileSession)
    ? !showPlayerShell && !mobileSession?.attach_ready
    : optimizedPlaybackPending || (Boolean(mobileSession) && !mobilePlayerCanPlay);
  const hideActionsDisabled = browserPlaybackSessionActive || hiddenActionPending || globalHiddenActionPending;
  const showPrimaryStatusPill = isImportantPlaybackStatus(playbackStatus);
  const showPlaybackReasonPill = isImportantPlaybackReason(playback?.reason);
  const prepareEstimateDisplay = (() => {
    if (!showMobilePreparingPlaceholder || !isRoute2SessionPayload(mobileSession)) {
      return {
        value: "EST --:--",
        tone: "estimating",
      };
    }
    const rawEstimateSeconds = getSessionModeEstimateSeconds(mobileSession);
    if (
      rawEstimateSeconds == null
      || !Number.isFinite(rawEstimateSeconds)
      || rawEstimateSeconds < 0
      || prepareEstimateObservedAtMs <= 0
    ) {
      return {
        value: "EST --:--",
        tone: "estimating",
      };
    }
    const baseEstimateSeconds = rawEstimateSeconds;
    const elapsedSeconds = Math.max(0, (prepareEstimateNowMs - prepareEstimateObservedAtMs) / 1000);
    const remainingSeconds = Math.max(0, baseEstimateSeconds - elapsedSeconds);
    return {
      value: `EST ${formatEstimateDuration(remainingSeconds)}`,
      tone: resolvePrepareEstimateTone(remainingSeconds),
    };
  })();
  const statusPillClassName =
    mobileSession || playback?.mode === "direct"
      ? "status-pill"
      : "status-pill status-pill--live";
  const preparedDurationLabel = availableDuration > 0 ? formatDuration(availableDuration) : "0:00";
  const mobileCacheRangesLabel =
    mobileSession?.cache_ranges?.length
      ? mobileSession.cache_ranges
        .slice(0, 3)
        .map(([start, end]) => formatTimeRange(start, end))
        .join(", ")
      : "";
  const optimizedProgressNote =
    mobileSession
      ? mobileSeekPendingRef.current || mobileSession.pending_target_seconds != null
        ? `Preparing reusable cached media around ${formatDuration(
            mobilePendingTargetRef.current != null
              ? mobilePendingTargetRef.current
              : mobileSession.target_position_seconds,
          )}.`
        : mobileCacheRangesLabel
          ? `Reusable cached ranges: ${mobileCacheRangesLabel}${mobileSession.cache_ranges.length > 3 ? "..." : ""}.`
          : availableDuration <= 0
            ? `Elvern is preparing ${browserPlaybackLabel}.`
            : fullDuration > 0
              ? `Cached playback around ${formatTimeRange(mobileSession.ready_start_seconds || 0, mobileSession.ready_end_seconds || 0)} of ${formatDuration(fullDuration)}.`
              : `Cached playback around ${formatTimeRange(mobileSession.ready_start_seconds || 0, mobileSession.ready_end_seconds || 0)}.`
      : playback?.mode === "hls"
      ? playback.manifest_complete
        ? "Full movie is ready for optimized playback."
        : availableDuration <= 0
          ? "Elvern is preparing optimized playback."
        : fullDuration > 0
          ? `Prepared through ${preparedDurationLabel} of ${formatDuration(fullDuration)} while Elvern transcodes ahead.`
          : `Prepared through ${preparedDurationLabel} while Elvern transcodes ahead.`
      : "Full movie is available for direct playback.";

  return (
    <section className="page-section page-section--detail">
      {providerReconnectModal.open ? (
        <div
          aria-labelledby="provider-reconnect-modal-title"
          aria-modal="true"
          className="browser-resume-modal"
          role="dialog"
        >
          <div
            aria-hidden="true"
            className="browser-resume-modal__backdrop"
            onClick={providerReconnectPending ? undefined : closeProviderReconnectModal}
          />
          <div className="browser-resume-modal__card">
            <p className="eyebrow">Provider connection</p>
            <h2 id="provider-reconnect-modal-title">{providerReconnectModal.title}</h2>
            <p className="page-subnote">{providerReconnectModal.message}</p>
            {providerReconnectModal.errorMessage ? (
              <p className="form-error">{providerReconnectModal.errorMessage}</p>
            ) : null}
            <div className="browser-resume-modal__actions">
              {providerReconnectModal.allowReconnect ? (
                <>
                  <button
                    className="primary-button"
                    disabled={providerReconnectPending}
                    onClick={handleProviderReconnect}
                    type="button"
                  >
                    {providerReconnectPending ? "Reconnecting..." : "Reconnect"}
                  </button>
                  <button
                    className="ghost-button"
                    disabled={providerReconnectPending}
                    onClick={closeProviderReconnectModal}
                    type="button"
                  >
                    Cancel
                  </button>
                </>
              ) : (
                <button
                  className="ghost-button"
                  onClick={closeProviderReconnectModal}
                  type="button"
                >
                  OK
                </button>
              )}
            </div>
          </div>
        </div>
      ) : null}
      {browserResumeModalOpen ? (
        <div
          aria-labelledby="browser-resume-modal-title"
          aria-modal="true"
          className="browser-resume-modal"
          role="dialog"
        >
          <div
            aria-hidden="true"
            className="browser-resume-modal__backdrop"
            onClick={() => setBrowserResumeModalOpen(false)}
          />
          <div className="browser-resume-modal__card">
            <p className="eyebrow">{browserPlaybackLabelTitle}</p>
            <h2 id="browser-resume-modal-title">Choose where to start</h2>
            <div className="browser-resume-modal__actions">
              <button
                className="primary-button"
                onClick={handleResumeBrowserPlayback}
                type="button"
              >
                Resume at {formatDuration(resumableStartPosition)}
              </button>
              <button
                className="ghost-button"
                onClick={handleStartBrowserPlaybackFromBeginning}
                type="button"
              >
                Start from beginning
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {browserStopModalOpen ? (
        <div
          aria-labelledby="browser-stop-modal-title"
          aria-modal="true"
          className="browser-resume-modal"
          role="dialog"
        >
          <div
            aria-hidden="true"
            className="browser-resume-modal__backdrop"
            onClick={() => setBrowserStopModalOpen(false)}
          />
          <div className="browser-resume-modal__card">
            <p className="eyebrow">{browserPlaybackLabelTitle}</p>
            <h2 id="browser-stop-modal-title">Stop {browserPlaybackLabelTitle}?</h2>
            <div className="browser-resume-modal__actions">
              <button
                className="ghost-button ghost-button--danger"
                onClick={confirmStopBrowserPlayback}
                type="button"
              >
                Stop {browserPlaybackLabelTitle}
              </button>
              <button
                className="ghost-button"
                onClick={() => setBrowserStopModalOpen(false)}
                type="button"
              >
                Keep preparing
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <div className="section-header">
        <div>
          <p className="eyebrow">Player</p>
          <h1>{detailTitle}</h1>
        </div>
        <Link
          className="ghost-button ghost-button--inline"
          onClick={prepareLibraryReturnNavigation}
          state={libraryReturnLinkState}
          to={libraryReturnPath}
        >
          Back to library
        </Link>
      </div>

      <div className="player-card">
        <div className="player-status">
          <div className="status-stack">
            {showPrimaryStatusPill ? (
              <span className={statusPillClassName}>
                {playbackStatus}
              </span>
            ) : null}
            {showPlaybackReasonPill ? <span className="status-pill">{playback.reason}</span> : null}
          </div>
          <div className="player-actions">
            {desktopPlatform ? (
              <button
                className="primary-button"
                disabled={vlcLaunchPending || !desktopPlayback}
                onClick={handleOpenInVlc}
                type="button"
              >
                {vlcLaunchPending
                  ? "Opening VLC..."
                  : "Open in VLC (Fastest)"}
              </button>
            ) : null}
            {showIosExternalApps ? (
              <>
                <button
                  className={litePlaybackActive ? "ghost-button ghost-button--danger" : "ghost-button"}
                  disabled={iosAppLaunchPending || fullPlaybackActive}
                  onClick={litePlaybackActive ? requestStopBrowserPlayback : handleStartLitePlayback}
                  type="button"
                >
                  {litePlaybackActive ? "Stop Lite Playback" : "Lite Playback"}
                </button>
                <button
                  className={fullPlaybackActive ? "ghost-button ghost-button--danger" : "ghost-button"}
                  disabled={iosAppLaunchPending || litePlaybackActive}
                  onClick={fullPlaybackActive ? requestStopBrowserPlayback : handleStartFullPlayback}
                  type="button"
                >
                  {fullPlaybackActive ? "Stop Full Playback" : "Full Playback"}
                </button>
                <button
                  className="ghost-button ghost-button--subtle"
                  disabled={iosAppLaunchPending || optimizedPlaybackPending}
                  onClick={() => handleOpenInIosExternalApp("vlc")}
                  type="button"
                >
                  {iosAppLaunchPending && iosAppTarget === "VLC"
                    ? "Opening VLC..."
                    : "Open in VLC (Fastest)"}
                </button>
                <button
                  className="ghost-button ghost-button--subtle"
                  disabled={iosAppLaunchPending || optimizedPlaybackPending}
                  onClick={() => handleOpenInIosExternalApp("infuse")}
                  type="button"
                >
                  {iosAppLaunchPending && iosAppTarget === "Infuse"
                    ? "Opening Infuse..."
                    : "Open in Infuse (Pro)"}
                </button>
              </>
            ) : null}
            {!showIosExternalApps ? (
              <>
                <button
                  className={litePlaybackActive ? "ghost-button ghost-button--danger" : "ghost-button"}
                  disabled={fullPlaybackActive}
                  onClick={litePlaybackActive ? requestStopBrowserPlayback : handleStartLitePlayback}
                  type="button"
                >
                  {litePlaybackActive ? "Stop Lite Playback" : "Lite Playback"}
                </button>
                <button
                  className={fullPlaybackActive ? "ghost-button ghost-button--danger" : "ghost-button"}
                  disabled={litePlaybackActive}
                  onClick={fullPlaybackActive ? requestStopBrowserPlayback : handleStartFullPlayback}
                  type="button"
                >
                  {fullPlaybackActive ? "Stop Full Playback" : "Full Playback"}
                </button>
              </>
            ) : null}
            {isAdmin ? (
              <button
                className="ghost-button ghost-button--danger"
                disabled={hideActionsDisabled}
                onClick={handleHideMovieForEveryone}
                type="button"
              >
                {globalHiddenActionPending ? "Hiding globally..." : "Hide for everyone"}
              </button>
            ) : null}
            <button
              className="ghost-button ghost-button--subtle"
              disabled={hideActionsDisabled}
              onClick={handleHideMovie}
              type="button"
            >
              {hiddenActionPending ? "Hiding..." : "Hide for me"}
            </button>
          </div>
          {showMobilePreparingPlaceholder ? (
            <div className="playback-pending-indicator" role="status">
              <span className="spinner spinner--inline" aria-hidden="true" />
              <div>
                <p className="page-note">Preparing {browserPlaybackLabel}</p>
                <div className="playback-pending-est" aria-live="polite">
                  <strong className={`playback-pending-est__value playback-pending-est__value--${prepareEstimateDisplay.tone}`}>
                    {prepareEstimateDisplay.value}
                  </strong>
                </div>
              </div>
            </div>
          ) : null}
          {playbackError ? <p className="form-error">{playbackError}</p> : null}
          {globalHiddenActionError ? <p className="form-error">{globalHiddenActionError}</p> : null}
          {globalHiddenActionMessage ? <p className="page-note">{globalHiddenActionMessage}</p> : null}
          {hiddenActionError ? <p className="form-error">{hiddenActionError}</p> : null}
          {hiddenActionMessage ? <p className="page-note">{hiddenActionMessage}</p> : null}
          {iosAppLaunchError ? <p className="form-error">{iosAppLaunchError}</p> : null}
          {iosAppLaunchMessage ? <p className="page-note">{iosAppLaunchMessage}</p> : null}
          {iosTransportDebug ? (
            <div className="native-handoff-debug" role="status">
              <p className="native-handoff-debug__title">Temporary transport debug</p>
              <div className="native-handoff-debug__grid">
                <div className="native-handoff-debug__row">
                  <span className="native-handoff-debug__label">Selected player</span>
                  <span className="native-handoff-debug__value">{iosTransportDebug.selectedPlayer}</span>
                </div>
                <div className="native-handoff-debug__row">
                  <span className="native-handoff-debug__label">Selected mode</span>
                  <span className="native-handoff-debug__value">{iosTransportDebug.selectedMode}</span>
                </div>
                <div className="native-handoff-debug__row">
                  <span className="native-handoff-debug__label">Primary target</span>
                  <span className="native-handoff-debug__value">{iosTransportDebug.primaryTargetKind}</span>
                </div>
                <div className="native-handoff-debug__row">
                  <span className="native-handoff-debug__label">Reason code</span>
                  <span className="native-handoff-debug__value">{iosTransportDebug.reasonCode}</span>
                </div>
              </div>
            </div>
          ) : null}
          {vlcLaunchError ? <p className="form-error">{vlcLaunchError}</p> : null}
          {vlcLaunchMessage ? <p className="page-note">{vlcLaunchMessage}</p> : null}
          {iosAppPlaybackUrl ? (
            <div className="native-handoff">
              <p className="native-handoff__label">
                Short-lived playback URL for {iosAppTarget}. This is the clean fallback when Safari opens the app but cannot guarantee autoplay inside it.
              </p>
              <input
                className="native-handoff__url"
                readOnly
                type="text"
                value={iosAppPlaybackUrl}
              />
              <div className="player-actions">
                <button
                  className="ghost-button"
                  onClick={retryIosExternalAppLaunch}
                  type="button"
                >
                  Try {iosAppTarget} again
                </button>
                <button
                  className="ghost-button"
                  onClick={handleCopyIosPlaybackUrl}
                  type="button"
                >
                  Copy short-lived playback URL
                </button>
                <a
                  className="ghost-button"
                  href={iosAppPlaybackUrl}
                >
                  Open short-lived playback URL
                </a>
              </div>
            </div>
          ) : null}
          {desktopPlayback?.notes?.length ? (
            <div className="desktop-playback-notes">
              {desktopPlayback.notes.map((note) => (
                <p className="page-note" key={note}>
                  {note}
                </p>
              ))}
            </div>
          ) : null}
          {desktopPlayback && !desktopPlayback.open_supported && !desktopPlayback.handoff_supported ? (
            <div className="native-handoff">
              <p className="native-handoff__label">
                VLC target is ready for this desktop platform. Download the playlist or copy the target only when you actually need the direct handoff details.
              </p>
              <div className="player-actions">
                <a
                  className="ghost-button"
                  href={desktopPlayback.playlist_url}
                >
                  Download VLC Playlist
                </a>
                <button
                  className="ghost-button"
                  onClick={handleCopyVlcTarget}
                  type="button"
                >
                  Copy VLC Target
                </button>
              </div>
            </div>
          ) : null}
        </div>

        {showPlayerShell ? (
          <div className="player-shell">
            {mobileFrozenFrameUrl && (mobileRetargetTransitionRef.current || !mobilePlayerCanPlay) ? (
              <img
                alt=""
                aria-hidden="true"
                className="player-frozen-frame"
                src={mobileFrozenFrameUrl}
              />
            ) : null}
            <video
              key={videoElementKey}
              className={mobileSession && !mobilePlayerCanPlay ? "player player--warmup" : "player"}
              controls={
                !mobileSession
                || mobilePlayerCanPlay
                || (iosMobile && activePlaybackMode === "lite" && Boolean(streamSource))
              }
              playsInline
              preload="metadata"
              ref={videoRef}
            />
          </div>
        ) : null}
        {(streamSource || optimizedPlaybackPending || seekNotice) ? (
          <div className="player-runtime-notes">
            <p className="page-note">{optimizedProgressNote}</p>
            {seekNotice ? <p className="page-note">{seekNotice}</p> : null}
          </div>
        ) : null}

        <div className="detail-grid">
          <div className="detail-block">
            <h2>Playback</h2>
            <div className="detail-list">
              <span>{formatDuration(item.duration_seconds)}</span>
              <span>{formatBytes(item.file_size)}</span>
              {resumePosition > 0 ? <span>Resume at {formatDuration(resumePosition)}</span> : null}
            </div>
          </div>

          <div className="detail-block">
            <h2>Video</h2>
            <div className="detail-list">
              <span>
                {item.width && item.height
                  ? `${item.width} x ${item.height}`
                  : "Unknown resolution"}
              </span>
              <span>{item.video_codec || "Unknown video codec"}</span>
              <span>{item.container || "Unknown container"}</span>
            </div>
          </div>

          <div className="detail-block">
            <h2>Audio & subtitles</h2>
            <div className="detail-list">
              <span>{item.audio_codec || "Unknown audio codec"}</span>
              <span>
                {item.subtitles.length > 0
                  ? `${item.subtitles.length} subtitle track(s) indexed`
                  : "No subtitle tracks indexed"}
              </span>
              {playback?.mode === "hls" ? <span>Automatic HLS fallback</span> : null}
            </div>
          </div>

          <div className="detail-block">
            <h2>Source file</h2>
            <div className="detail-list">
              <span>{item.source_label || (item.source_kind === "cloud" ? "Cloud" : "DGX")}</span>
              <span>{item.original_filename}</span>
              <span>
                {item.source_kind === "cloud"
                  ? (item.library_source_name
                    ? `Google Drive source: ${item.library_source_name}`
                    : "Streamed from Google Drive")
                  : "Stored under the configured private media root"}
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
