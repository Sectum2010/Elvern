import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { LoadingView } from "../components/LoadingView";
import { ProviderReconnectModal } from "../components/ProviderReconnectModal";
import { apiRequest } from "../lib/api";
import {
  getSessionModeEstimateSeconds,
  isIOSMobileBrowser,
  isHlsSessionPayload as isSharedHlsSessionPayload,
  resolveBrowserPlaybackSessionRoot,
} from "../lib/browserPlayback";
import { getOrCreateDeviceId } from "../lib/device";
import { formatBytes, formatDuration } from "../lib/format";
import { detectDesktopPlatform } from "../lib/platformDetection";
import {
  resolveDetailVlcActionRoute,
  shouldShowDesktopBrowserSeekControl,
  shouldShowMacAppFullscreenControl,
  shouldShowMacHlsWindowControls,
} from "../lib/playbackRouting";
import { useBrowserPlaybackController } from "../features/playback/useBrowserPlaybackController";
import {
  extractLibraryReturnState,
  readLibraryReturnTarget,
  rememberLibraryReturnTarget,
} from "../lib/libraryNavigation";
import { resolveBrowserPlaybackPlayerViewState } from "../lib/browserPlaybackPlayerState";
import { resolveAuthoritativeBrowserPlaybackResumePosition } from "../lib/browserPlaybackResume";
import { getMovieCardTitle } from "../lib/movieTitles";
import { getCloudReconnectPrompt, isCloudReconnectRequired } from "../lib/cloudSyncStatus";
import {
  clearProviderAuthIntent,
  getProviderAuthRequirement,
  readProviderAuthIntent,
  saveProviderAuthIntent,
  shouldGuardGoogleDriveAction,
  startGoogleDriveReconnect,
} from "../lib/providerAuth";
import {
  buildActivePlaybackConflictPrompt,
  getActivePlaybackWorkerConflict,
} from "../lib/playbackWorkerOwnership";


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
const NATIVE_TRANSPORT_DEBUG_STORAGE_KEY = "elvern_native_transport_debug";
const DEBUG_FLAG_ENABLED_VALUES = new Set(["1", "true", "yes", "on"]);
const DEBUG_FLAG_DISABLED_VALUES = new Set(["0", "false", "no", "off"]);
const PROVIDER_ACTION_BROWSER_LITE = "browser_playback_lite";
const PROVIDER_ACTION_BROWSER_FULL = "browser_playback_full";
const PROVIDER_ACTION_DESKTOP_VLC = "desktop_vlc_handoff";
const PROVIDER_ACTION_IOS_VLC = "ios_external_vlc_handoff";
const PROVIDER_ACTION_IOS_INFUSE = "ios_external_infuse_handoff";
const PROVIDER_RECONNECT_CONTINUE_LABEL = "Continue anyway";
const DESKTOP_PLAYBACK_HIDDEN_NOTE_PREFIXES = [
  "No mapped direct source is configured",
  "On the Elvern host, clicking Open in VLC launches the installed VLC app directly",
  "Cloud libraries use a secure backend stream fallback for desktop VLC in this phase.",
];
const EMPTY_CLOUD_LIBRARIES = {
  google: {
    enabled: false,
    connected: false,
    connection_status: "not_configured",
    reconnect_required: false,
    provider_auth_required: false,
    stale_state_warning: null,
    status_message: "",
  },
  my_libraries: [],
  shared_libraries: [],
};


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

function isIosTransportDebugEnabled(search = "") {
  if (typeof window === "undefined") {
    return false;
  }
  const params = new URLSearchParams(search || window.location.search || "");
  const queryValue = params.get("transport_debug") || params.get("native_transport_debug");
  const normalizedQueryValue = (queryValue || "").trim().toLowerCase();
  if (DEBUG_FLAG_ENABLED_VALUES.has(normalizedQueryValue)) {
    return true;
  }
  if (DEBUG_FLAG_DISABLED_VALUES.has(normalizedQueryValue)) {
    return false;
  }
  try {
    const storedValue = (window.localStorage.getItem(NATIVE_TRANSPORT_DEBUG_STORAGE_KEY) || "").trim().toLowerCase();
    return DEBUG_FLAG_ENABLED_VALUES.has(storedValue);
  } catch {
    return false;
  }
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
  const providerReconnectContinuationRef = useRef(null);
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
    secondaryLabel: "Close",
  });
  const [cloudLibraries, setCloudLibraries] = useState(EMPTY_CLOUD_LIBRARIES);
  const [cloudLibrariesLoaded, setCloudLibrariesLoaded] = useState(false);
  const [iosAppLaunchPending, setIosAppLaunchPending] = useState(false);
  const [iosAppLaunchMessage, setIosAppLaunchMessage] = useState("");
  const [iosAppLaunchError, setIosAppLaunchError] = useState("");
  const [iosAppLaunchUrl, setIosAppLaunchUrl] = useState("");
  const [iosAppPlaybackUrl, setIosAppPlaybackUrl] = useState("");
  const [iosAppTarget, setIosAppTarget] = useState("");
  const [iosTransportDebug, setIosTransportDebug] = useState(null);
  const [browserResumeModalOpen, setBrowserResumeModalOpen] = useState(false);
  const [browserResumePromptPosition, setBrowserResumePromptPosition] = useState(0);
  const [browserStopModalOpen, setBrowserStopModalOpen] = useState(false);
  const [playbackConflictModal, setPlaybackConflictModal] = useState(null);
  const [playbackConflictPending, setPlaybackConflictPending] = useState(false);
  const [macHlsWindowSeekDraft, setMacHlsWindowSeekDraft] = useState(null);
  const [desktopSeekDraft, setDesktopSeekDraft] = useState(null);
  const [macAppFullscreenActive, setMacAppFullscreenActive] = useState(false);
  const [macAppFullscreenError, setMacAppFullscreenError] = useState("");
  const playerShellRef = useRef(null);
  const macHlsWindowSeekCommitPendingRef = useRef(false);
  const desktopSeekCommitPendingRef = useRef(false);
  const [infoModalOpen, setInfoModalOpen] = useState(false);
  const [mediaLibraryReferenceInfo, setMediaLibraryReferenceInfo] = useState(null);
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
  const browserPlaybackSessionRoot = resolveBrowserPlaybackSessionRoot();
  const showIosTransportDebug = iosTransportDebug && isIosTransportDebugEnabled(location.search);
  const localDevLoopback = isLocalDevelopmentLoopback(desktopPlatform);
  const desktopDeviceId = useMemo(() => getOrCreateDeviceId(), []);
  const isAdmin = user?.role === "admin";

  useEffect(() => {
    if (typeof document === "undefined") {
      return undefined;
    }
    const readFullscreenElement = () => (
      document.fullscreenElement
      || document.webkitFullscreenElement
      || null
    );
    const syncMacFullscreenState = () => {
      const shell = playerShellRef.current;
      const active = Boolean(shell && readFullscreenElement() === shell);
      setMacAppFullscreenActive(active);
      if (!active) {
        setMacAppFullscreenError("");
      }
    };

    document.addEventListener("fullscreenchange", syncMacFullscreenState);
    document.addEventListener("webkitfullscreenchange", syncMacFullscreenState);
    syncMacFullscreenState();
    return () => {
      document.removeEventListener("fullscreenchange", syncMacFullscreenState);
      document.removeEventListener("webkitfullscreenchange", syncMacFullscreenState);
    };
  }, []);

  useEffect(() => {
    if (typeof document === "undefined") {
      return undefined;
    }
    const className = "elvern-player-fullscreen-active";
    const body = document.body;
    if (macAppFullscreenActive) {
      document.documentElement.classList.add(className);
      body?.classList.add(className);
    } else {
      document.documentElement.classList.remove(className);
      body?.classList.remove(className);
    }
    return () => {
      document.documentElement.classList.remove(className);
      body?.classList.remove(className);
    };
  }, [macAppFullscreenActive]);

  const {
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
    playerLocalPosition,
    playerLocalDuration,
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
    seekBrowserPlaybackTo,
    seekBrowserPlaybackWindowTo,
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
    providerReconnectContinuationRef.current = null;
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
      secondaryLabel: "Close",
    });
    setProviderReconnectPending(false);
  }

  function openProviderReconnectModal(
    requirement,
    actionType,
    {
      errorMessage = "",
      secondaryLabel = "Close",
      onSecondaryAction = null,
    } = {},
  ) {
    providerReconnectContinuationRef.current = onSecondaryAction;
    setProviderReconnectModal({
      open: true,
      provider: requirement.provider,
      title: requirement.title,
      message: requirement.message,
      actionType,
      allowReconnect: requirement.allowReconnect !== false,
      requiresAdmin: requirement.requiresAdmin === true,
      errorMessage,
      secondaryLabel,
    });
  }

  async function loadCloudLibrariesHealth({ signal } = {}) {
    try {
      const payload = await apiRequest("/api/cloud-libraries", { signal });
      setCloudLibraries(payload);
      setCloudLibrariesLoaded(true);
      return payload;
    } catch (requestError) {
      if (requestError?.name === "AbortError") {
        return null;
      }
      return null;
    }
  }

  async function maybeGuardCloudProviderAction({ actionType, onContinue }) {
    if (!item) {
      return false;
    }
    let nextCloudLibraries = cloudLibraries;
    if (!cloudLibrariesLoaded) {
      const refreshed = await loadCloudLibrariesHealth();
      if (refreshed) {
        nextCloudLibraries = refreshed;
      }
    }
    if (
      !shouldGuardGoogleDriveAction({
        itemSourceKind: item.source_kind,
        reconnectRequired: isCloudReconnectRequired(nextCloudLibraries),
      })
    ) {
      return false;
    }
    const prompt = getCloudReconnectPrompt(nextCloudLibraries) || {
      title: "Reconnect Google Drive",
      message: "Google Drive reconnect is required. Cloud movies may be stale until you reconnect.",
    };
    openProviderReconnectModal(
      {
        provider: "google_drive",
        title: prompt.title,
        message: prompt.message,
        allowReconnect: true,
      },
      actionType,
      {
        secondaryLabel: PROVIDER_RECONNECT_CONTINUE_LABEL,
        onSecondaryAction: onContinue,
      },
    );
    return true;
  }

  function retrySavedProviderAction(actionType) {
    switch (actionType) {
      case PROVIDER_ACTION_BROWSER_LITE:
        clearPlaybackError();
        setError("");
        void beginBrowserPlaybackFlow("lite", { skipReconnectGuard: true });
        return true;
      case PROVIDER_ACTION_BROWSER_FULL:
        clearPlaybackError();
        setError("");
        void beginBrowserPlaybackFlow("full", { skipReconnectGuard: true });
        return true;
      case PROVIDER_ACTION_DESKTOP_VLC:
        setVlcLaunchError("");
        setVlcLaunchMessage("Google Drive reconnected. Retrying VLC handoff.");
        void handleOpenInVlc({ isProviderRetry: true, skipReconnectGuard: true });
        return true;
      case PROVIDER_ACTION_IOS_VLC:
        setIosAppLaunchError("");
        setIosAppLaunchMessage("Google Drive reconnected. Retrying VLC handoff.");
        void handleOpenInIosExternalApp("vlc", { skipReconnectGuard: true });
        return true;
      case PROVIDER_ACTION_IOS_INFUSE:
        setIosAppLaunchError("");
        setIosAppLaunchMessage("Google Drive reconnected. Retrying Infuse handoff.");
        void handleOpenInIosExternalApp("infuse", { skipReconnectGuard: true });
        return true;
      default:
        return false;
    }
  }

  function handleProviderReconnectSecondaryAction() {
    const continuation = providerReconnectContinuationRef.current;
    closeProviderReconnectModal();
    if (continuation) {
      void continuation();
    }
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
    let cancelled = false;

    async function loadMediaLibraryReferenceInfo() {
      if (!user) {
        if (!cancelled) {
          setMediaLibraryReferenceInfo(null);
        }
        return;
      }

      try {
        if (user.role === "admin") {
          const payload = await apiRequest("/api/admin/media-library-reference");
          if (!cancelled) {
            setMediaLibraryReferenceInfo({
              sharedDefault: payload.effective_value || payload.default_value || "Not set",
              privateValue: null,
              effectiveValue: payload.effective_value || payload.default_value || "Not set",
            });
          }
          return;
        }

        const payload = await apiRequest("/api/user-settings");
        if (!cancelled) {
          setMediaLibraryReferenceInfo({
            sharedDefault: payload.media_library_reference_shared_default_value || "Not set",
            privateValue: payload.media_library_reference_private_value || null,
            effectiveValue:
              payload.media_library_reference_effective_value
              || payload.media_library_reference_shared_default_value
              || "Not set",
          });
        }
      } catch {
        if (!cancelled) {
          setMediaLibraryReferenceInfo(null);
        }
      }
    }

    loadMediaLibraryReferenceInfo();
    return () => {
      cancelled = true;
    };
  }, [user?.id, user?.role]);

  useEffect(() => {
    if (!item || (item.source_kind || "local") !== "cloud") {
      setCloudLibraries(EMPTY_CLOUD_LIBRARIES);
      setCloudLibrariesLoaded(false);
      return undefined;
    }
    const controller = new AbortController();
    setCloudLibrariesLoaded(false);
    void loadCloudLibrariesHealth({ signal: controller.signal });
    return () => {
      controller.abort();
    };
  }, [item?.id, item?.source_kind]);

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
    const actionType = String(pendingIntent.actionType || "");
    if (providerReconnectResult.status !== "connected") {
      setProviderReconnectPending(false);
      openProviderReconnectModal(
        {
          provider: "google_drive",
          title: "Google Drive connection expired",
          message: "Reconnect Google Drive to continue this action.",
        },
        actionType,
        {
          errorMessage: providerReconnectResult.message || "Google Drive reconnect was cancelled or failed.",
          secondaryLabel: PROVIDER_RECONNECT_CONTINUE_LABEL,
          onSecondaryAction: () => retrySavedProviderAction(actionType),
        },
      );
      setProviderReconnectResult(null);
      return;
    }
    if (
      !item
      || (actionType === PROVIDER_ACTION_DESKTOP_VLC && !desktopPlayback)
    ) {
      return;
    }
    clearProviderAuthIntent();
    closeProviderReconnectModal();
    void loadCloudLibrariesHealth();
    setProviderReconnectResult(null);
    retrySavedProviderAction(actionType);
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
      setBrowserResumePromptPosition(0);
      setBrowserStopModalOpen(false);
      setPlaybackConflictModal(null);
      setPlaybackConflictPending(false);
      setInfoModalOpen(false);
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

  useEffect(() => {
    if (!infoModalOpen || typeof window === "undefined") {
      return undefined;
    }

    function handleKeyDown(event) {
      if (event.key === "Escape") {
        setInfoModalOpen(false);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [infoModalOpen]);

  useEffect(() => {
    if (
      typeof window === "undefined"
      || (!browserResumeModalOpen && !browserStopModalOpen && !playbackConflictModal)
    ) {
      return undefined;
    }

    function handleKeyDown(event) {
      if (event.key !== "Escape") {
        return;
      }
      if (playbackConflictPending) {
        return;
      }
      if (playbackConflictModal) {
        setPlaybackConflictModal(null);
        return;
      }
      if (browserStopModalOpen) {
        setBrowserStopModalOpen(false);
        return;
      }
      if (browserResumeModalOpen) {
        setBrowserResumeModalOpen(false);
        setBrowserResumePromptPosition(0);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [browserResumeModalOpen, browserStopModalOpen, playbackConflictModal, playbackConflictPending]);

  function requestStopBrowserPlayback() {
    setBrowserResumeModalOpen(false);
    setBrowserResumePromptPosition(0);
    setBrowserStopModalOpen(true);
  }

  async function confirmStopBrowserPlayback() {
    setBrowserStopModalOpen(false);
    await stopCurrentBrowserPlaybackSession();
  }

  async function resolveLatestBrowserResumeStartPosition() {
    if (!item) {
      return resumableStartPosition;
    }
    const progressPayload = await apiRequest(`/api/progress/${item.id}`);
    setProgress(progressPayload);
    return resolveAuthoritativeBrowserPlaybackResumePosition({
      progressPayload,
      durationSeconds: fullDuration || item?.duration_seconds || progressPayload?.duration_seconds || 0,
    });
  }

  function closePlaybackConflictModal() {
    if (playbackConflictPending) {
      return;
    }
    setPlaybackConflictModal(null);
  }

  async function beginRequestedBrowserPlayback(startPositionSeconds, playbackMode) {
    const requestedMovieTitle = getMovieCardTitle(item);
    await startBrowserPlaybackFrom(startPositionSeconds, playbackMode, {
      onActivePlaybackConflict: (activeConflict) => {
        setPlaybackConflictModal({
          ...activeConflict,
          requestedMovieTitle,
          requestedPlaybackMode: playbackMode,
          requestedStartPositionSeconds: startPositionSeconds,
        });
      },
    });
  }

  async function handleTerminatePlaybackConflict() {
    if (!playbackConflictModal?.activeSessionId || playbackConflictPending) {
      return;
    }
    setPlaybackConflictPending(true);
    clearPlaybackError();
    setError("");
    try {
      await apiRequest(
        `${browserPlaybackSessionRoot}/sessions/${encodeURIComponent(playbackConflictModal.activeSessionId)}/stop`,
        { method: "POST" },
      );
      const pendingRequest = playbackConflictModal;
      setPlaybackConflictModal(null);
      await beginRequestedBrowserPlayback(
        pendingRequest.requestedStartPositionSeconds,
        pendingRequest.requestedPlaybackMode,
      );
    } catch (requestError) {
      const activePlaybackConflict = getActivePlaybackWorkerConflict(requestError);
      if (activePlaybackConflict) {
        setPlaybackConflictModal((current) => (current ? {
          ...current,
          ...activePlaybackConflict,
        } : current));
      }
      setPlaybackError(requestError.message || "Failed to terminate the current background preparation");
    } finally {
      setPlaybackConflictPending(false);
    }
  }

  async function beginBrowserPlaybackFlow(playbackMode = "lite", { skipReconnectGuard = false } = {}) {
    const actionType =
      playbackMode === "full"
        ? PROVIDER_ACTION_BROWSER_FULL
        : PROVIDER_ACTION_BROWSER_LITE;
    if (!skipReconnectGuard) {
      const blocked = await maybeGuardCloudProviderAction({
        actionType,
        onContinue: () => beginBrowserPlaybackFlow(playbackMode, { skipReconnectGuard: true }),
      });
      if (blocked) {
        return;
      }
    }
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
    let latestResumeStartPosition = resumableStartPosition;
    try {
      latestResumeStartPosition = await resolveLatestBrowserResumeStartPosition();
    } catch (requestError) {
      console.error("Failed to refresh latest playback progress before browser start", requestError);
    }
    if (latestResumeStartPosition > 0) {
      setBrowserResumePromptPosition(latestResumeStartPosition);
      setBrowserResumeModalOpen(true);
      return;
    }
    setBrowserResumePromptPosition(0);
    setBrowserResumeModalOpen(false);
    setBrowserStopModalOpen(false);
    void beginRequestedBrowserPlayback(0, playbackMode);
  }

  function handleStartLitePlayback() {
    void beginBrowserPlaybackFlow("lite");
  }

  function handleStartFullPlayback() {
    void beginBrowserPlaybackFlow("full");
  }

  function handleResumeBrowserPlayback() {
    const nextResumeStartPosition = browserResumePromptPosition > 0
      ? browserResumePromptPosition
      : resumableStartPosition;
    setBrowserResumeModalOpen(false);
    setBrowserResumePromptPosition(0);
    setBrowserStopModalOpen(false);
    resetIosExternalAppState();
    void beginRequestedBrowserPlayback(nextResumeStartPosition, playbackModeIntent);
  }

  function handleStartBrowserPlaybackFromBeginning() {
    setBrowserResumeModalOpen(false);
    setBrowserResumePromptPosition(0);
    setBrowserStopModalOpen(false);
    resetIosExternalAppState();
    void beginRequestedBrowserPlayback(0, playbackModeIntent);
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
      actionType: providerReconnectModal.actionType || PROVIDER_ACTION_DESKTOP_VLC,
      mediaItemId: Number(item.id),
      platform: desktopPlayback?.platform || desktopPlatform || null,
      returnPath,
    });
    try {
      await startGoogleDriveReconnect({ returnPath });
    } catch (requestError) {
      setProviderReconnectPending(false);
      setProviderReconnectModal((current) => ({
        ...current,
        errorMessage: requestError.message || "Failed to start Google Drive reconnect.",
      }));
    }
  }

  async function handleOpenInVlc(options = {}) {
    const {
      isProviderRetry = false,
      skipReconnectGuard = false,
      suppressProviderAuthModal = false,
    } = options;
    if (!item) {
      return;
    }
    if (!skipReconnectGuard) {
      const blocked = await maybeGuardCloudProviderAction({
        actionType: PROVIDER_ACTION_DESKTOP_VLC,
        onContinue: () => handleOpenInVlc({
          skipReconnectGuard: true,
          suppressProviderAuthModal: true,
        }),
      });
      if (blocked) {
        return;
      }
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
        setVlcLaunchMessage(
          desktopPlayback.used_backend_fallback
            ? "Sent handoff to the desktop helper. The helper will preflight the backend stream URL before launching VLC."
            : (payload.message || "Launching installed VLC via the Elvern desktop opener."),
        );
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
        if (suppressProviderAuthModal) {
          setVlcLaunchError(providerAuthRequirement.message || requestError.message || "Google Drive reconnect is required.");
          setVlcLaunchMessage("");
          if (hadBrowserPlaybackSession && !isProviderRetry) {
            restoreActiveBrowserPlaybackSession().catch(() => {
              // Preserve the reconnect-required error if browser playback cannot be restored.
            });
          }
          return;
        }
        setVlcLaunchError("");
        setVlcLaunchMessage("");
        openProviderReconnectModal(providerAuthRequirement, PROVIDER_ACTION_DESKTOP_VLC, {
          secondaryLabel: PROVIDER_RECONNECT_CONTINUE_LABEL,
          onSecondaryAction: () => handleOpenInVlc({
            skipReconnectGuard: true,
            suppressProviderAuthModal: true,
          }),
        });
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

  async function handleOpenInIosExternalApp(
    targetApp,
    {
      skipReconnectGuard = false,
      suppressProviderAuthModal = false,
    } = {},
  ) {
    if (!item) {
      return;
    }
    const actionType =
      targetApp === "infuse"
        ? PROVIDER_ACTION_IOS_INFUSE
        : PROVIDER_ACTION_IOS_VLC;
    if (!skipReconnectGuard) {
      const blocked = await maybeGuardCloudProviderAction({
        actionType,
        onContinue: () => handleOpenInIosExternalApp(targetApp, {
          skipReconnectGuard: true,
          suppressProviderAuthModal: true,
        }),
      });
      if (blocked) {
        return;
      }
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
          : "Trying to open VLC for original-quality playback with a short-lived Elvern URL. This works best on strong home or local Wi-Fi. For weaker or less stable connections, use Lite Playback or Full Playback in the browser.",
      );
      window.location.assign(launchUrl);
    } catch (requestError) {
      const providerAuthRequirement = getProviderAuthRequirement(requestError);
      if (targetApp === "infuse") {
        clearIosExternalAppLaunchState({ itemId, app: "infuse" });
      }
      setIosTransportDebug(null);
      if (providerAuthRequirement) {
        if (suppressProviderAuthModal) {
          setIosAppLaunchError(providerAuthRequirement.message || requestError.message || `Failed to open ${appLabel}`);
          setIosAppLaunchMessage("");
          return;
        }
        setIosAppLaunchError("");
        setIosAppLaunchMessage("");
        openProviderReconnectModal(providerAuthRequirement, actionType, {
          secondaryLabel: PROVIDER_RECONNECT_CONTINUE_LABEL,
          onSecondaryAction: () => handleOpenInIosExternalApp(targetApp, {
            skipReconnectGuard: true,
            suppressProviderAuthModal: true,
          }),
        });
        return;
      }
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

  const subtitleTracks = Array.isArray(item.subtitles) ? item.subtitles : [];
  const detailSourceLabel = item.source_label || (item.source_kind === "cloud" ? "Cloud" : "DGX");
  const sourceDescription = item.source_kind === "cloud"
    ? (item.library_source_name
      ? `Google Drive source: ${item.library_source_name}`
      : "Streamed from Google Drive")
    : "Stored under the configured private media root";
  const sharedMediaLibraryReference = mediaLibraryReferenceInfo?.sharedDefault || "Loading...";
  const myPrivateMediaLibraryReference = mediaLibraryReferenceInfo
    ? (mediaLibraryReferenceInfo.privateValue || "Not set")
    : "Loading...";
  const effectiveMediaLibraryReference = mediaLibraryReferenceInfo?.effectiveValue || "Loading...";
  const visibleDesktopPlaybackNotes = Array.isArray(desktopPlayback?.notes)
    ? desktopPlayback.notes.filter(
        (note) => !DESKTOP_PLAYBACK_HIDDEN_NOTE_PREFIXES.some((prefix) => note.startsWith(prefix)),
      )
    : [];

  const showIosExternalApps = iosMobile;
  const vlcActionRoute = resolveDetailVlcActionRoute({
    desktopPlatform,
    iosMobile,
    desktopPlayback,
  });
  const {
    browserPlaybackPreparing,
    playerClassName,
    showMobilePreparingPlaceholder,
    showPlayerShell,
    videoControlsEnabled,
  } = resolveBrowserPlaybackPlayerViewState({
    activePlaybackMode,
    iosMobile,
    mobileFrozenFrameUrl,
    mobilePlayerCanPlay,
    mobileSession,
    optimizedPlaybackPending,
    streamSource,
  });
  const litePlaybackActive = browserPlaybackSessionActive && activePlaybackMode === "lite";
  const fullPlaybackActive = browserPlaybackSessionActive && activePlaybackMode === "full";
  const hideActionsDisabled = browserPlaybackSessionActive || hiddenActionPending || globalHiddenActionPending;
  const showPrimaryStatusPill = isImportantPlaybackStatus(playbackStatus);
  const showPlaybackReasonPill = isImportantPlaybackReason(playback?.reason);
  const showMacHlsWindowSeekControl = shouldShowMacHlsWindowControls({
    desktopPlatform,
    iosMobile,
    showPlayerShell,
    hasMobileSession: Boolean(mobileSession),
    playerLocalDuration,
  });
  const macHlsWindowSeekPosition = Math.max(
    0,
    Math.min(
      playerLocalDuration || 0,
      macHlsWindowSeekDraft != null ? macHlsWindowSeekDraft : playerLocalPosition || 0,
    ),
  );
  const macHlsWindowSeekProgressPercent = playerLocalDuration > 0
    ? Math.min(100, Math.max(0, (macHlsWindowSeekPosition / playerLocalDuration) * 100))
    : 0;
  const macHlsWindowStartSeconds = Math.max(mobileSession?.ready_start_seconds || 0, 0);
  const macHlsWindowPositionLabel = `${formatDuration(macHlsWindowSeekPosition)} / ${formatDuration(playerLocalDuration)}`;
  const macHlsWindowRangeLabel = formatTimeRange(
    macHlsWindowStartSeconds,
    macHlsWindowStartSeconds + Math.max(playerLocalDuration || 0, 0),
  );
  const showDesktopBrowserSeekControl = shouldShowDesktopBrowserSeekControl({
    desktopPlatform,
    iosMobile,
    showPlayerShell,
    hasMobileSession: Boolean(mobileSession),
    fullDuration,
  });
  const showMacAppFullscreenControl = shouldShowMacAppFullscreenControl({
    desktopPlatform,
    iosMobile,
    showPlayerShell,
  });
  const resolvedPlayerClassName = showMacAppFullscreenControl
    ? `${playerClassName} player--app-fullscreen-managed`
    : playerClassName;
  const playerShellClassName = [
    "player-shell",
    showMacAppFullscreenControl ? "player-shell--app-fullscreen" : "",
    macAppFullscreenActive ? "player-shell--app-fullscreen-active" : "",
  ].filter(Boolean).join(" ");
  const desktopSeekPosition = Math.max(
    0,
    Math.min(
      fullDuration || 0,
      desktopSeekDraft != null ? desktopSeekDraft : playbackPosition || 0,
    ),
  );
  const desktopSeekProgressPercent = fullDuration > 0
    ? Math.min(100, Math.max(0, (desktopSeekPosition / fullDuration) * 100))
    : 0;
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
  const effectiveBrowserResumePromptPosition = browserResumePromptPosition > 0
    ? browserResumePromptPosition
    : resumableStartPosition;
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

  function normalizeMacHlsWindowSeekValue(value) {
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) {
      return 0;
    }
    if (playerLocalDuration > 0) {
      return Math.min(playerLocalDuration, Math.max(0, numericValue));
    }
    return Math.max(0, numericValue);
  }

  function commitMacHlsWindowSeek(value) {
    if (!showMacHlsWindowSeekControl || macHlsWindowSeekCommitPendingRef.current) {
      return;
    }
    const targetPosition = normalizeMacHlsWindowSeekValue(value);
    macHlsWindowSeekCommitPendingRef.current = true;
    setMacHlsWindowSeekDraft(null);
    seekBrowserPlaybackWindowTo(targetPosition);
    window.requestAnimationFrame(() => {
      macHlsWindowSeekCommitPendingRef.current = false;
    });
  }

  function normalizeDesktopSeekValue(value) {
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) {
      return 0;
    }
    if (fullDuration > 0) {
      return Math.min(fullDuration, Math.max(0, numericValue));
    }
    return Math.max(0, numericValue);
  }

  function commitDesktopBrowserSeek(value) {
    if (!showDesktopBrowserSeekControl || desktopSeekCommitPendingRef.current) {
      return;
    }
    const targetPosition = normalizeDesktopSeekValue(value);
    desktopSeekCommitPendingRef.current = true;
    setDesktopSeekDraft(null);
    seekBrowserPlaybackTo(targetPosition).finally(() => {
      desktopSeekCommitPendingRef.current = false;
    });
  }

  async function toggleMacAppFullscreen() {
    if (!showMacAppFullscreenControl || typeof document === "undefined") {
      return;
    }
    const shell = playerShellRef.current;
    if (!shell) {
      return;
    }
    setMacAppFullscreenError("");
    const activeElement = document.fullscreenElement || document.webkitFullscreenElement || null;
    try {
      if (activeElement === shell) {
        if (document.exitFullscreen) {
          await document.exitFullscreen();
        } else if (document.webkitExitFullscreen) {
          document.webkitExitFullscreen();
        }
        return;
      }
      if (shell.requestFullscreen) {
        await shell.requestFullscreen();
      } else if (shell.webkitRequestFullscreen) {
        shell.webkitRequestFullscreen();
      } else {
        throw new Error("Fullscreen is not available in this browser.");
      }
    } catch (fullscreenError) {
      setMacAppFullscreenError(
        fullscreenError?.message || "Fullscreen is not available in this browser.",
      );
    }
  }

  return (
    <section className="page-section page-section--detail">
      <ProviderReconnectModal
        allowReconnect={providerReconnectModal.allowReconnect}
        errorMessage={providerReconnectModal.errorMessage}
        message={providerReconnectModal.message}
        onClose={closeProviderReconnectModal}
        onReconnect={handleProviderReconnect}
        onSecondary={handleProviderReconnectSecondaryAction}
        open={providerReconnectModal.open}
        reconnectLabel="Reconnect Google Drive"
        reconnectPending={providerReconnectPending}
        secondaryLabel={providerReconnectModal.secondaryLabel}
        title={providerReconnectModal.title}
      />
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
                Resume at {formatDuration(effectiveBrowserResumePromptPosition)}
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
      {playbackConflictModal ? (
        <div
          aria-labelledby="playback-conflict-modal-title"
          aria-modal="true"
          className="browser-resume-modal"
          role="dialog"
        >
          <div
            aria-hidden="true"
            className="browser-resume-modal__backdrop"
            onClick={closePlaybackConflictModal}
          />
          <div className="browser-resume-modal__card detail-info-modal__card playback-worker-choice-modal">
            <div className="detail-info-modal__copy">
              <p className="eyebrow detail-info-modal__eyebrow">PLAYBACK WORKER</p>
              <p className="detail-info-modal__title" id="playback-conflict-modal-title">
                {buildActivePlaybackConflictPrompt(
                  playbackConflictModal.activeMovieTitle,
                  playbackConflictModal.requestedMovieTitle,
                )}
              </p>
            </div>
            <div className="browser-resume-modal__actions playback-worker-choice-modal__actions">
              <button
                className="primary-button"
                disabled={playbackConflictPending}
                onClick={closePlaybackConflictModal}
                type="button"
              >
                Keep Preparing
              </button>
              <button
                className="ghost-button ghost-button--danger"
                disabled={playbackConflictPending}
                onClick={handleTerminatePlaybackConflict}
                type="button"
              >
                Terminate Process
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {infoModalOpen ? (
        <div
          aria-labelledby="detail-info-modal-title"
          aria-modal="true"
          className="browser-resume-modal"
          role="dialog"
        >
          <div
            aria-hidden="true"
            className="browser-resume-modal__backdrop"
            onClick={() => setInfoModalOpen(false)}
          />
          <div className="browser-resume-modal__card detail-info-modal__card">
            <div className="detail-info-modal__header">
              <div className="detail-info-modal__copy">
                <p className="eyebrow detail-info-modal__eyebrow">Info</p>
                <h2 className="detail-info-modal__title" id="detail-info-modal-title">{detailTitle}</h2>
              </div>
              <button
                className="ghost-button ghost-button--inline detail-info-modal__close"
                onClick={() => setInfoModalOpen(false)}
                type="button"
              >
                Close
              </button>
            </div>

            <div className="detail-info-modal__body">
              <div className="detail-grid detail-grid--modal">
                <div className="detail-block">
                  <h2>Playback</h2>
                  <div className="detail-list">
                    <span>{formatDuration(item.duration_seconds)}</span>
                    <span>{formatBytes(item.file_size)}</span>
                    {resumePosition > 0 ? <span>Resume at {formatDuration(resumePosition)}</span> : null}
                    {playback?.mode === "hls" ? <span>Automatic HLS fallback</span> : null}
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
                      {subtitleTracks.length > 0
                        ? `${subtitleTracks.length} subtitle track(s) indexed`
                        : "No subtitle tracks indexed"}
                    </span>
                  </div>
                  {subtitleTracks.length > 0 ? (
                    <div className="detail-subtitle-list">
                      {subtitleTracks.map((subtitle, index) => {
                        const subtitleLabel = [
                          subtitle.language || null,
                          subtitle.title || null,
                          subtitle.codec || null,
                          subtitle.disposition_default ? "Default" : null,
                        ].filter(Boolean).join(" · ");
                        return (
                          <span className="status-pill" key={subtitle.id || `${subtitleLabel}-${index}`}>
                            {subtitleLabel || "Subtitle track"}
                          </span>
                        );
                      })}
                    </div>
                  ) : null}
                </div>

                <div className="detail-block">
                  <h2>Source file</h2>
                  <div className="detail-source-group">
                    <div className="detail-list">
                      <span>{detailSourceLabel}</span>
                    </div>
                    <p className="detail-path">{item.original_filename}</p>
                    <p className="page-subnote">{sourceDescription}</p>
                  </div>
                </div>

                <div className="detail-block">
                  <h2>Media Library Reference</h2>
                  {isAdmin ? (
                    <div className="detail-reference-group">
                      <div className="detail-reference-group__item">
                        <div className="detail-list">
                          <span>Shared default</span>
                        </div>
                        <p className="detail-path">{sharedMediaLibraryReference}</p>
                      </div>
                      <p className="page-subnote">
                        This is the default reference used unless a user sets a private override.
                      </p>
                    </div>
                  ) : (
                    <div className="detail-reference-group">
                      <div className="detail-reference-group__item">
                        <div className="detail-list">
                          <span>Shared default</span>
                        </div>
                        <p className="detail-path">{sharedMediaLibraryReference}</p>
                      </div>
                      <div className="detail-reference-group__item">
                        <div className="detail-list">
                          <span>My private reference</span>
                        </div>
                        <p className="detail-path">{myPrivateMediaLibraryReference}</p>
                      </div>
                      <div className="detail-reference-group__item">
                        <div className="detail-list">
                          <span>Using now</span>
                        </div>
                        <p className="detail-path">{effectiveMediaLibraryReference}</p>
                      </div>
                    </div>
                  )}
                </div>
              </div>
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
            {vlcActionRoute.surface.startsWith("desktop") ? (
              <button
                className="primary-button"
                disabled={vlcLaunchPending || !desktopPlayback}
                onClick={handleOpenInVlc}
                type="button"
              >
                {vlcLaunchPending
                  ? "Opening VLC..."
                  : "Open in VLC"}
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
                    : "Open in VLC"}
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
          {showIosTransportDebug ? (
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
          {visibleDesktopPlaybackNotes.length ? (
            <div className="desktop-playback-notes">
              {visibleDesktopPlaybackNotes.map((note) => (
                <p className="page-note" key={note}>
                  {note}
                </p>
              ))}
            </div>
          ) : null}
          {desktopPlayback?.open_method === "protocol_helper" && !desktopPlayback?.same_host_launch ? (
            <div className="native-handoff">
              <p className="native-handoff__label">
                This desktop uses the client-side Elvern VLC Opener for Open in VLC. Server install does not register it on this device. If clicking Open in VLC does nothing or fails silently, open Install to download or update the helper, test the protocol handler, and check whether VLC was detected here.
              </p>
              <div className="player-actions">
                <Link className="ghost-button" to="/install">
                  Open Helper Setup
                </Link>
              </div>
            </div>
          ) : null}
          {desktopPlayback?.used_backend_fallback && (desktopPlatform === "mac" || desktopPlatform === "windows") ? (
            <div className="native-handoff">
              <p className="native-handoff__label">
                {desktopPlatform === "mac" ? "Mac direct path mapping" : "Windows direct path mapping"} is not configured, so Open in VLC is using a backend stream fallback. The helper now verifies that exact URL from this desktop before launching VLC; a helper launch alone does not prove VLC playback opened.
              </p>
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
          <div className={playerShellClassName} ref={playerShellRef}>
            <div className="player-fullscreen-surface">
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
                className={resolvedPlayerClassName}
                controls={videoControlsEnabled}
                controlsList={showMacAppFullscreenControl ? "nofullscreen nodownload noremoteplayback" : undefined}
                disablePictureInPicture={showMacAppFullscreenControl ? true : undefined}
                playsInline
                preload="metadata"
                ref={videoRef}
              />
            </div>
            {showMacAppFullscreenControl ? (
              <button
                className="player-app-fullscreen-button"
                onClick={toggleMacAppFullscreen}
                type="button"
              >
                {macAppFullscreenActive ? "Exit fullscreen" : "Fullscreen"}
              </button>
            ) : null}
          </div>
        ) : null}
        {macAppFullscreenError ? <p className="form-error">{macAppFullscreenError}</p> : null}
        {showMacHlsWindowSeekControl ? (
          <div className="mac-hls-window-seek" aria-label="Current HLS window seek controls">
            <div className="mac-hls-window-seek__controls">
              <button
                className="mac-hls-window-seek__button"
                disabled={macHlsWindowSeekPosition <= 0}
                onClick={() => commitMacHlsWindowSeek(macHlsWindowSeekPosition - 10)}
                type="button"
              >
                -10s
              </button>
              <div className="mac-hls-window-seek__labels" aria-hidden="true">
                <span>Window {macHlsWindowPositionLabel}</span>
                {macHlsWindowRangeLabel ? <span>{macHlsWindowRangeLabel}</span> : null}
              </div>
              <button
                className="mac-hls-window-seek__button"
                disabled={macHlsWindowSeekPosition >= playerLocalDuration}
                onClick={() => commitMacHlsWindowSeek(macHlsWindowSeekPosition + 10)}
                type="button"
              >
                +10s
              </button>
            </div>
            <input
              aria-label="Seek current playback window"
              className="mac-hls-window-seek__range"
              max={Math.max(1, Math.round(playerLocalDuration))}
              min="0"
              onBlur={(event) => {
                if (macHlsWindowSeekDraft != null) {
                  commitMacHlsWindowSeek(event.currentTarget.value);
                }
              }}
              onChange={(event) => {
                setMacHlsWindowSeekDraft(normalizeMacHlsWindowSeekValue(event.currentTarget.value));
              }}
              onKeyUp={(event) => {
                if (["ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"].includes(event.key)) {
                  commitMacHlsWindowSeek(event.currentTarget.value);
                }
              }}
              onPointerCancel={() => {
                setMacHlsWindowSeekDraft(null);
              }}
              onPointerUp={(event) => {
                commitMacHlsWindowSeek(event.currentTarget.value);
              }}
              step="1"
              style={{ "--mac-hls-window-progress": `${macHlsWindowSeekProgressPercent}%` }}
              type="range"
              value={Math.round(macHlsWindowSeekPosition)}
            />
          </div>
        ) : null}
        {showDesktopBrowserSeekControl ? (
          <div className="desktop-browser-seek" aria-label="Movie seek controls">
            <input
              aria-label="Seek movie position"
              className="desktop-browser-seek__range"
              max={Math.max(1, Math.round(fullDuration))}
              min="0"
              onBlur={(event) => {
                if (desktopSeekDraft != null) {
                  commitDesktopBrowserSeek(event.currentTarget.value);
                }
              }}
              onChange={(event) => {
                setDesktopSeekDraft(normalizeDesktopSeekValue(event.currentTarget.value));
              }}
              onKeyUp={(event) => {
                if (["ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"].includes(event.key)) {
                  commitDesktopBrowserSeek(event.currentTarget.value);
                }
              }}
              onPointerCancel={() => {
                setDesktopSeekDraft(null);
              }}
              onPointerUp={(event) => {
                commitDesktopBrowserSeek(event.currentTarget.value);
              }}
              step="1"
              style={{ "--desktop-seek-progress": `${desktopSeekProgressPercent}%` }}
              type="range"
              value={Math.round(desktopSeekPosition)}
            />
          </div>
        ) : null}
        {(streamSource || optimizedPlaybackPending || seekNotice) ? (
          <div className="player-runtime-notes">
            <p className="page-note">{optimizedProgressNote}</p>
            {seekNotice ? <p className="page-note">{seekNotice}</p> : null}
          </div>
        ) : null}

        <div className="detail-secondary-actions">
          <button
            className="ghost-button"
            onClick={() => setInfoModalOpen(true)}
            type="button"
          >
            Info
          </button>
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

      </div>
    </section>
  );
}
