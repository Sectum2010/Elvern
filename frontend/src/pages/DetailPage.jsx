import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { LoadingView } from "../components/LoadingView";
import { apiRequest } from "../lib/api";
import {
  getSessionModeEstimateSeconds,
  isIOSMobileBrowser,
  isHlsSessionPayload as isSharedHlsSessionPayload,
} from "../lib/browserPlayback";
import { getOrCreateDeviceId } from "../lib/device";
import { formatBytes, formatDuration } from "../lib/format";
import { useBrowserPlaybackController } from "../features/playback/useBrowserPlaybackController";
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

export function DetailPage() {
  const { itemId } = useParams();
  const location = useLocation();
  const { user } = useAuth();
  const [item, setItem] = useState(null);
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState("");
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
  const {
    videoRef,
    mobileRetargetTransitionRef,
    mobileSeekPendingRef,
    mobileSession,
    streamSource,
    mobilePlayerCanPlay,
    mobileFrozenFrameUrl,
    playback,
    playbackError,
    seekNotice,
    playbackStatus,
    playbackModeIntent,
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
    browserPlaybackSessionActive,
    hasAnyBrowserPlaybackArtifacts,
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
    stopCurrentBrowserPlaybackSession,
  } = useBrowserPlaybackController({
    itemId,
    item,
    progress,
    iosMobile,
    onProgressChange: setProgress,
  });
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

  function resetIosExternalAppState() {
    setIosAppLaunchMessage("");
    setIosAppLaunchError("");
    setIosAppLaunchUrl("");
    setIosAppPlaybackUrl("");
    setIosAppTarget("");
  }

  function isRoute2SessionPayload(payload = mobileSession) {
    return isSharedHlsSessionPayload(payload);
  }

  function stopBrowserPlaybackForDesktopHandoff(statusMessage = "Handing off to VLC") {
    cancelBrowserPlaybackRequest();
    clearPlaybackResources();
    const video = videoRef.current;
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }
    clearPlaybackStreamSource();
    clearPlaybackError();
    setSeekNoticeValue("");
    setPlaybackStatusValue(statusMessage);
    clearOptimizedPlaybackPending();
  }

  function prepareForExternalPlayerHandoff() {
    clearOptimizedPlaybackPending();
    resetPendingPlaybackPreparation();
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
    let cancelled = false;

    async function loadDetails() {
      prepareControllerForLoad(itemId);
      setLoading(true);
      setError("");
      resetIosExternalAppState();
      setGlobalHiddenActionMessage("");
      setGlobalHiddenActionError("");
      setDesktopPlayback(null);
      setVlcLaunchPending(false);
      setVlcLaunchMessage("");
      setVlcLaunchError("");
      setBrowserResumeModalOpen(false);
      setBrowserStopModalOpen(false);
      try {
        const itemPayload = await apiRequest(`/api/library/item/${itemId}`);
        if (cancelled) {
          return;
        }
        setItem(itemPayload);
        if (itemPayload.hidden_globally) {
          setProgress(null);
          setLoading(false);
          return;
        }
        if (itemPayload.hidden_for_user) {
          setProgress(null);
          setLoading(false);
          return;
        }
        const progressPayload = await apiRequest(`/api/progress/${itemId}`);
        if (cancelled) {
          return;
        }
        setProgress(progressPayload);
        if (!iosMobile) {
          const playbackPayload = await apiRequest(`/api/playback/${itemId}`);
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
      clearPlaybackResources();
      resetMobilePlaybackState();
    };
  }, [desktopPlatform, iosMobile, itemId, localDevLoopback, detailRefreshKey]);

  function requestStopBrowserPlayback() {
    setBrowserResumeModalOpen(false);
    setBrowserStopModalOpen(true);
  }

  async function confirmStopBrowserPlayback() {
    setBrowserStopModalOpen(false);
    await stopCurrentBrowserPlaybackSession();
  }

  function beginBrowserPlaybackFlow(playbackMode = "lite") {
    setPlaybackModeIntentValue(playbackMode);
    clearPlaybackError();
    clearOptimizedPlaybackPending();
    resetIosExternalAppState();
    if (optimizedPlaybackPending || mobileSession) {
      requestStopBrowserPlayback();
      return;
    }
    if (streamSource && videoRef.current?.currentSrc) {
      playExistingBrowserSource();
      return;
    }
    if (resumableStartPosition > 0) {
      setBrowserResumeModalOpen(true);
      return;
    }
    setBrowserResumeModalOpen(false);
    setBrowserStopModalOpen(false);
    void startBrowserPlaybackFrom(0, playbackMode);
  }

  function handleStartLitePlayback() {
    beginBrowserPlaybackFlow("lite");
  }

  function handleStartFullPlayback() {
    beginBrowserPlaybackFlow("full");
  }

  function handleResumeBrowserPlayback() {
    setBrowserResumeModalOpen(false);
    setBrowserStopModalOpen(false);
    resetIosExternalAppState();
    void startBrowserPlaybackFrom(resumableStartPosition, playbackModeIntent);
  }

  function handleStartBrowserPlaybackFromBeginning() {
    setBrowserResumeModalOpen(false);
    setBrowserStopModalOpen(false);
    resetIosExternalAppState();
    void startBrowserPlaybackFrom(0, playbackModeIntent);
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

    const hadBrowserPlaybackSession = hasAnyBrowserPlaybackArtifacts;
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
    prepareForExternalPlayerHandoff();
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
