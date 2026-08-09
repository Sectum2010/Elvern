import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  apiRequest,
  isAbortError,
  isTransientNetworkError,
} from "../../lib/api";
import { getOrCreateDeviceId } from "../../lib/device";
import {
  buildMacTerminalInstallCommand,
  copyTextToClipboard,
  isPackageLevelDesktopHelperRelease,
} from "../../lib/desktopHelperInstall";
import {
  detectClientPlatform,
  isDesktopClientPlatform,
} from "../../lib/platformDetection";
import { PAGE_RESUME_EVENT } from "../../lib/pageResume.js";
import {
  getConnectivityIncidentRecoveryGeneration,
  subscribeConnectivityRecovery,
} from "../../lib/connectivityRecoveryStore.js";


const IOS_APP_LINKS = {
  us: {
    vlc: "https://apps.apple.com/us/app/vlc-media-player/id650377962",
    infuse: "https://apps.apple.com/us/app/infuse-video-player/id1136220934",
  },
  cn: {
    vlc: "https://apps.apple.com/cn/app/vlc-media-player/id650377962",
    infuse: "https://apps.apple.com/cn/app/infuse-video-player/id1136220934",
  },
};
const ANDROID_VLC_LINK = "https://play.google.com/store/apps/details?id=org.videolan.vlc";
const DESKTOP_VLC_LINKS = {
  windows: "https://www.videolan.org/vlc/download-windows.html",
  mac: "https://www.videolan.org/vlc/download-macosx.html",
  linux: "https://www.videolan.org/vlc/",
};
const MOBILE_APP_STATUS_PREFIX = "elvern-install-app-status:";


function detectInstallPlatform() {
  return detectClientPlatform();
}

function detectIosStoreRegion() {
  if (typeof navigator === "undefined") {
    return "us";
  }
  const languages = [navigator.language, ...(navigator.languages || [])]
    .filter(Boolean)
    .map((entry) => String(entry).toLowerCase());
  const localeSuggestsChina = languages.some((entry) => entry === "zh-cn" || entry.startsWith("zh-hans-cn"));
  const timezone = typeof Intl !== "undefined"
    ? Intl.DateTimeFormat().resolvedOptions().timeZone || ""
    : "";
  return localeSuggestsChina || timezone === "Asia/Shanghai" ? "cn" : "us";
}

function isDesktopPlatform(platform) {
  return isDesktopClientPlatform(platform);
}

function platformLabel(platform) {
  switch (platform) {
    case "iphone":
      return "iPhone / iOS";
    case "ipad":
      return "iPad / iPadOS";
    case "android":
      return "Android";
    case "windows":
      return "Windows";
    case "mac":
      return "macOS";
    case "linux":
      return "Linux";
    default:
      return "Unknown";
  }
}

function releaseLabel(release) {
  if (release.package_target === "windows-x64") {
    return "Windows x64";
  }
  if (release.package_target === "macos-dual-arch") {
    return "macOS";
  }
  if (release.package_target === "linux-universal") {
    return "Linux";
  }
  if (release.runtime_id === "win-x64") {
    return "Windows x64";
  }
  if (release.runtime_id === "osx-arm64") {
    return "macOS Apple Silicon";
  }
  if (release.runtime_id === "osx-x64") {
    return "macOS Intel";
  }
  return release.runtime_id;
}

function formatBytes(value) {
  if (!value || value <= 0) {
    return "Unknown size";
  }
  const units = ["B", "KB", "MB", "GB"];
  let current = value;
  let unitIndex = 0;
  while (current >= 1024 && unitIndex < units.length - 1) {
    current /= 1024;
    unitIndex += 1;
  }
  return `${current.toFixed(current >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function stateCopy(state) {
  switch (state) {
    case "helper_not_required":
      return "Not required on this Elvern host";
    case "up_to_date":
      return "Ready";
    case "update_available":
      return "Helper update available";
    case "release_unavailable":
      return "Installer unavailable";
    default:
      return "Helper not verified";
  }
}

function helperStatusCopy(status) {
  if (!status) {
    return "Could not verify";
  }
  if (status.vlc_detection_state === "not_detected") {
    return "VLC not found";
  }
  return stateCopy(status.state);
}

function normalizeMobileAppInstallState(value) {
  if (!value) {
    return {
      status: "unverified",
      lastCheckedAt: null,
    };
  }

  if (value === "opened") {
    return {
      status: "could_not_verify",
      lastCheckedAt: null,
    };
  }

  if (typeof value === "object" && value !== null) {
    const normalizedStatus =
      value.status === "could_not_verify" || value.status === "not_verified"
        ? value.status
        : "unverified";
    const normalizedLastCheckedAt = Number.isFinite(Number(value.lastCheckedAt))
      ? Number(value.lastCheckedAt)
      : null;
    return {
      status: normalizedStatus,
      lastCheckedAt: normalizedLastCheckedAt,
    };
  }

  return {
    status: "unverified",
    lastCheckedAt: null,
  };
}

function readMobileAppStatus(key) {
  if (typeof window === "undefined") {
    return normalizeMobileAppInstallState(null);
  }
  try {
    const raw = window.localStorage.getItem(`${MOBILE_APP_STATUS_PREFIX}${key}`);
    if (!raw) {
      return normalizeMobileAppInstallState(null);
    }
    const parsed = JSON.parse(raw);
    const normalized = normalizeMobileAppInstallState(parsed);
    if (parsed === "opened" || parsed?.status === "installed") {
      writeMobileAppStatus(key, normalized);
    }
    return normalized;
  } catch {
    return normalizeMobileAppInstallState(null);
  }
}

function writeMobileAppStatus(key, value) {
  if (typeof window === "undefined") {
    return;
  }
  try {
    if (!value || value.status === "unverified") {
      window.localStorage.removeItem(`${MOBILE_APP_STATUS_PREFIX}${key}`);
      return;
    }
    window.localStorage.setItem(`${MOBILE_APP_STATUS_PREFIX}${key}`, JSON.stringify(value));
  } catch {
    // Ignore localStorage write failures.
  }
}

function mobileAppButtonLabel(appState, platform) {
  return platform === "android" ? "Get" : "Test";
}

function mobileAppStatusLabel(appState) {
  if (appState.status === "could_not_verify" || appState.status === "not_verified") {
    return "Could not verify open";
  }
  return "Install status unverified";
}

function mobileAppStatusCopy(appState, platform) {
  if (appState.status === "could_not_verify" || appState.status === "not_verified") {
    return "A failed test only means the web could not confirm a successful app handoff.";
  }
  if (platform === "android") {
    return "Mobile web cannot reliably verify installed Android apps here.";
  }
  return "Use Test to try the app scheme. Safari cannot always prove whether the app opened, so Elvern will not mark it installed unless a future verifiable signal exists.";
}

function formatLastChecked(lastCheckedAt) {
  if (!lastCheckedAt) {
    return "Never";
  }
  try {
    const candidate = typeof lastCheckedAt === "number" ? new Date(lastCheckedAt) : new Date(String(lastCheckedAt));
    if (Number.isNaN(candidate.getTime())) {
      return "Never";
    }
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }).format(candidate);
  } catch {
    return "Never";
  }
}

function desktopVlcStatus(status, platform) {
  const detectionState = status?.vlc_detection_state || "detection_unavailable";
  const detectionPath = status?.vlc_detection_path || "";
  if (detectionState === "installed") {
    return {
      label: "VLC detected",
      copy: detectionPath
        ? `Verified by a grounded ${platform === "linux" && status?.same_host ? "host-side" : "desktop helper"} VLC lookup at ${detectionPath}.`
        : `Verified by a grounded ${platform === "linux" && status?.same_host ? "host-side" : "desktop helper"} VLC lookup.`,
    };
  }
  if (detectionState === "not_detected") {
    return {
      label: "Not detected",
      copy: platform === "linux" && status?.same_host
        ? "Elvern could not find VLC on this Linux host."
        : "The desktop helper last reported that VLC was not detected on this device.",
    };
  }
  return {
    label: "VLC not verified",
    copy: platform === "linux" && status?.same_host
      ? "Run the host check below to confirm whether Elvern can see VLC on this Linux machine."
      : "Run the helper test below. Elvern only knows local VLC state after the client-side helper calls back from this device.",
  };
}

function desktopHelperTestButtonLabel(platform, status) {
  return platform === "linux" && status?.same_host ? "Check VLC on this host" : "Test helper";
}

function desktopHelperTestCopy(platform, status) {
  if (platform === "linux" && status?.same_host) {
    return "This is a host-side VLC lookup only. It does not install or register anything.";
  }
  return "Test desktop helper opens a short-lived elvern-vlc:// verify link and waits briefly for the helper to call back to Elvern.";
}

function desktopHelperFeedbackForStatus(platform, status) {
  if (!status) {
    return "";
  }
  if (platform === "linux" && status?.same_host) {
    if (status.vlc_detection_state === "installed") {
      return "Elvern confirmed VLC on this Linux host.";
    }
    if (status.vlc_detection_state === "not_detected") {
      return "Elvern refreshed the Linux host check, but VLC was not detected.";
    }
    return "Elvern refreshed the Linux host VLC check.";
  }

  if (status.vlc_detection_state === "installed") {
    return "The desktop helper called back to Elvern and reported VLC detection on this device.";
  }
  if (status.vlc_detection_state === "not_detected") {
    return "The desktop helper called back to Elvern, but it reported that VLC was not detected on this device.";
  }
  return "The desktop helper called back to Elvern, but local VLC detection is still unavailable.";
}

function buildRecommendedApps(platform, iosStoreRegion) {
  if (platform === "iphone" || platform === "ipad") {
    const links = IOS_APP_LINKS[iosStoreRegion];
    return [
      {
        id: "vlc-ios",
        name: "VLC",
        description: "Raw-file external playback app for iPhone and iPad.",
        primary_url: links.vlc,
        mobile_status_key: "ios-vlc",
        open_url: "vlc://",
      },
      {
        id: "infuse-ios",
        name: "Infuse",
        description: "Optional iPhone/iPad playback app. Some formats may require Infuse Pro.",
        primary_url: links.infuse,
        mobile_status_key: "ios-infuse",
        open_url: "infuse://",
      },
    ];
  }

  if (platform === "android") {
    return [
      {
        id: "vlc-android",
        name: "VLC",
        description: "Recommended Android playback app.",
        primary_url: ANDROID_VLC_LINK,
        mobile_status_key: "android-vlc",
        open_url: null,
      },
    ];
  }

  if (platform === "unknown") {
    return [];
  }

  return [
    {
      id: `vlc-${platform}`,
      name: "VLC",
      description: "Recommended desktop playback app for local opening and playlist fallback.",
      primary_url: DESKTOP_VLC_LINKS[platform] || DESKTOP_VLC_LINKS.linux,
      mobile_status_key: null,
      open_url: null,
    },
  ];
}

function buildRequiredSection(platform, status) {
  if (!isDesktopPlatform(platform)) {
    return {
      empty: true,
      description: "Nothing is required for this setup.",
    };
  }

  const packageRelease = status?.latest_releases?.find(isPackageLevelDesktopHelperRelease) || null;
  const recommendedRelease = packageRelease
    || status?.latest_releases?.find((release) => release.recommended)
    || status?.latest_releases?.[0]
    || null;
  return {
    empty: false,
    description: status?.helper_required === false
      ? "Open in VLC uses the Elvern host directly on this same-host Linux session."
      : "Elvern VLC Opener receives secure handoffs and opens installed VLC on this device.",
    recommendedRelease,
    packageRelease,
    legacyReleases: packageRelease
      ? []
      : (status?.latest_releases || []).filter((release) => release.id !== recommendedRelease?.id),
  };
}

function verifyMobileAppInstall({ openUrl, statusKey, onStatusChange }) {
  if (typeof window === "undefined") {
    return;
  }
  if (!openUrl) {
    onStatusChange(statusKey, {
      status: "could_not_verify",
      lastCheckedAt: Date.now(),
    });
    return;
  }

  let finished = false;
  let fallbackTimer = 0;
  let blurConfirmTimer = 0;

  function cleanup() {
    if (fallbackTimer) {
      window.clearTimeout(fallbackTimer);
      fallbackTimer = 0;
    }
    if (blurConfirmTimer) {
      window.clearTimeout(blurConfirmTimer);
      blurConfirmTimer = 0;
    }
    document.removeEventListener("visibilitychange", handleVisibilityChange);
    window.removeEventListener("pagehide", handlePageHide);
    window.removeEventListener("blur", handleWindowBlur);
  }

  function markCouldNotVerify() {
    if (finished) {
      return;
    }
    finished = true;
    const nextState = {
      status: "could_not_verify",
      lastCheckedAt: Date.now(),
    };
    writeMobileAppStatus(statusKey, nextState);
    onStatusChange(statusKey, nextState);
    cleanup();
  }

  function handleVisibilityChange() {
    if (document.visibilityState === "hidden") {
      markCouldNotVerify();
    }
  }

  function handlePageHide() {
    markCouldNotVerify();
  }

  function handleWindowBlur() {
    blurConfirmTimer = window.setTimeout(() => {
      if (finished) {
        return;
      }
      if (document.visibilityState === "hidden" || !document.hasFocus()) {
        markCouldNotVerify();
      }
    }, 250);
  }

  document.addEventListener("visibilitychange", handleVisibilityChange);
  window.addEventListener("pagehide", handlePageHide, { once: true });
  window.addEventListener("blur", handleWindowBlur);
  window.location.assign(openUrl);

  fallbackTimer = window.setTimeout(() => {
    if (finished) {
      return;
    }
    markCouldNotVerify();
  }, 2200);
}

export function InstallSettingsPanel({ presentation = "default" }) {
  const platform = useMemo(() => detectInstallPlatform(), []);
  const iosStoreRegion = useMemo(() => detectIosStoreRegion(), []);
  const isDesktop = isDesktopPlatform(platform);
  const deviceId = useMemo(() => (isDesktop ? getOrCreateDeviceId() : ""), [isDesktop]);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(isDesktop);
  const [appCheckPendingKey, setAppCheckPendingKey] = useState("");
  const [desktopVerifyPending, setDesktopVerifyPending] = useState(false);
  const [desktopVerifyFeedback, setDesktopVerifyFeedback] = useState("");
  const [terminalCommandFeedback, setTerminalCommandFeedback] = useState("");
  const [reconnecting, setReconnecting] = useState(false);
  const statusRefreshOperationRef = useRef(null);
  const drainPendingStatusRefreshRef = useRef(() => {});
  const pendingPassiveRefreshRef = useRef(false);
  const pendingRecoveryRefreshRef = useRef(0);
  const statusRequestControllerRef = useRef(null);
  const statusRequestGenerationRef = useRef(0);
  const statusFailureRef = useRef(null);
  const statusRef = useRef(null);
  const verifyPendingRef = useRef(false);
  const verifyGenerationRef = useRef(0);
  const verifyPollTimerRef = useRef(0);
  const verifyPollResolveRef = useRef(null);
  const verifyRequestControllerRef = useRef(null);
  const mountedRef = useRef(true);
  const lastRecoveryGenerationRef = useRef(0);
  const [mobileAppStatus, setMobileAppStatus] = useState(() => ({
    "ios-vlc": readMobileAppStatus("ios-vlc"),
    "ios-infuse": readMobileAppStatus("ios-infuse"),
  }));

  const loadDesktopStatus = useCallback(async ({ showLoading = true } = {}) => {
    if (!isDesktop) {
      setLoading(false);
      setStatus(null);
      setError("");
      return null;
    }
    if (showLoading) {
      setLoading(true);
    }
    const requestGeneration = statusRequestGenerationRef.current + 1;
    statusRequestGenerationRef.current = requestGeneration;
    statusRequestControllerRef.current?.abort();
    const controller = new AbortController();
    statusRequestControllerRef.current = controller;
    try {
      const params = new URLSearchParams({ platform });
      if (deviceId) {
        params.set("device_id", deviceId);
      }
      const payload = await apiRequest(`/api/desktop-helper/status?${params.toString()}`, {
        signal: controller.signal,
        abortOnPageHide: true,
      });
      if (!mountedRef.current || statusRequestGenerationRef.current !== requestGeneration) {
        return null;
      }
      setStatus(payload);
      setError("");
      setReconnecting(false);
      statusFailureRef.current = null;
      return payload;
    } catch (requestError) {
      if (
        isAbortError(requestError)
        || !mountedRef.current
        || statusRequestGenerationRef.current !== requestGeneration
      ) {
        return null;
      }
      if (isTransientNetworkError(requestError)) {
        statusFailureRef.current = requestError;
        setReconnecting(true);
        setError((current) => (statusRef.current ? current : "Elvern could not load Helper status."));
      } else {
        statusFailureRef.current = null;
        setReconnecting(false);
        setError(requestError.message || "Failed to load install status");
      }
      return null;
    } finally {
      if (
        statusRequestControllerRef.current === controller
        && statusRequestGenerationRef.current === requestGeneration
      ) {
        statusRequestControllerRef.current = null;
      }
      if (mountedRef.current && statusRequestGenerationRef.current === requestGeneration) {
        setLoading(false);
      }
    }
  }, [deviceId, isDesktop, platform]);

  const cancelVerifyLifecycle = useCallback(() => {
    verifyGenerationRef.current += 1;
    verifyPendingRef.current = false;
    if (mountedRef.current) {
      setDesktopVerifyPending(false);
    }
    verifyRequestControllerRef.current?.abort();
    verifyRequestControllerRef.current = null;
    if (verifyPollTimerRef.current) {
      window.clearTimeout(verifyPollTimerRef.current);
      verifyPollTimerRef.current = 0;
    }
    if (verifyPollResolveRef.current) {
      verifyPollResolveRef.current(false);
      verifyPollResolveRef.current = null;
    }
  }, []);

  const refreshDesktopStatus = useCallback(async ({ reason = "passive", showLoading = false } = {}) => {
    if (!isDesktop || document.visibilityState === "hidden") {
      return null;
    }
    if (verifyPendingRef.current) {
      if (reason === "recovery") {
        pendingRecoveryRefreshRef.current = Math.max(
          pendingRecoveryRefreshRef.current,
          lastRecoveryGenerationRef.current,
        );
      } else {
        pendingPassiveRefreshRef.current = true;
      }
      return null;
    }
    const currentOperation = statusRefreshOperationRef.current;
    if (currentOperation) {
      if (reason === "recovery") {
        pendingRecoveryRefreshRef.current = Math.max(
          pendingRecoveryRefreshRef.current,
          lastRecoveryGenerationRef.current,
        );
      } else if (reason !== "initial") {
        pendingPassiveRefreshRef.current = true;
      }
      return currentOperation.promise;
    }
    let operation;
    operation = loadDesktopStatus({ showLoading })
      .then((payload) => {
        if (payload && mountedRef.current) {
          // A successful status response completed after any queued signal and
          // therefore already observes the recovered/resumed service state.
          pendingRecoveryRefreshRef.current = 0;
          pendingPassiveRefreshRef.current = false;
        }
        return payload;
      })
      .finally(() => {
        if (statusRefreshOperationRef.current?.promise === operation) {
          statusRefreshOperationRef.current = null;
          drainPendingStatusRefreshRef.current();
        }
      });
    statusRefreshOperationRef.current = { promise: operation, reason };
    return operation;
  }, [isDesktop, loadDesktopStatus]);

  const drainPendingStatusRefresh = useCallback(() => {
    if (
      !mountedRef.current
      || !isDesktop
      || document.visibilityState === "hidden"
      || verifyPendingRef.current
      || statusRefreshOperationRef.current
    ) {
      return;
    }
    if (pendingRecoveryRefreshRef.current > 0) {
      pendingRecoveryRefreshRef.current = 0;
      pendingPassiveRefreshRef.current = false;
      void refreshDesktopStatus({ reason: "recovery", showLoading: false });
      return;
    }
    if (pendingPassiveRefreshRef.current) {
      pendingPassiveRefreshRef.current = false;
      void refreshDesktopStatus({ reason: "resume", showLoading: false });
    }
  }, [isDesktop, refreshDesktopStatus]);

  const cancelStatusLifecycle = useCallback(() => {
    pendingRecoveryRefreshRef.current = 0;
    pendingPassiveRefreshRef.current = false;
    statusRequestGenerationRef.current += 1;
    statusRequestControllerRef.current?.abort();
    statusRequestControllerRef.current = null;
  }, []);

  useEffect(() => {
    drainPendingStatusRefreshRef.current = drainPendingStatusRefresh;
    return () => {
      drainPendingStatusRefreshRef.current = () => {};
    };
  }, [drainPendingStatusRefresh]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      cancelStatusLifecycle();
      cancelVerifyLifecycle();
    };
  }, [cancelStatusLifecycle, cancelVerifyLifecycle]);

  useEffect(() => {
    const handlePageHide = () => {
      cancelStatusLifecycle();
      cancelVerifyLifecycle();
    };
    window.addEventListener("pagehide", handlePageHide);
    return () => window.removeEventListener("pagehide", handlePageHide);
  }, [cancelStatusLifecycle, cancelVerifyLifecycle]);

  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  useEffect(() => {
    if (!isDesktop) {
      return undefined;
    }

    let cancelled = false;

    async function loadStatus() {
      const payload = await refreshDesktopStatus({ reason: "initial", showLoading: true });
      if (!cancelled && payload) {
        setStatus(payload);
      }
    }

    loadStatus();
    return () => {
      cancelled = true;
    };
  }, [isDesktop, refreshDesktopStatus]);

  useEffect(() => {
    if (!isDesktop) {
      return undefined;
    }

    const refreshStatus = async () => {
      await refreshDesktopStatus({ reason: "resume", showLoading: false });
    };
    window.addEventListener(PAGE_RESUME_EVENT, refreshStatus);
    return () => {
      window.removeEventListener(PAGE_RESUME_EVENT, refreshStatus);
    };
  }, [isDesktop, refreshDesktopStatus]);

  useEffect(() => {
    function recoverStatus() {
      const failure = statusFailureRef.current;
      const generation = getConnectivityIncidentRecoveryGeneration(
        failure?.incidentId,
        failure?.failureId,
      );
      if (
        !isDesktop
        || !reconnecting
        || generation <= lastRecoveryGenerationRef.current
      ) {
        return;
      }
      lastRecoveryGenerationRef.current = generation;
      if (verifyPendingRef.current) {
        pendingRecoveryRefreshRef.current = generation;
        return;
      }
      void refreshDesktopStatus({ reason: "recovery", showLoading: false });
    }
    const unsubscribe = subscribeConnectivityRecovery(recoverStatus);
    recoverStatus();
    return unsubscribe;
  }, [isDesktop, reconnecting, refreshDesktopStatus]);

  const requiredSection = useMemo(() => buildRequiredSection(platform, status), [platform, status]);
  const showRequiredSection = !requiredSection.empty;
  const recommendedApps = useMemo(() => buildRecommendedApps(platform, iosStoreRegion), [iosStoreRegion, platform]);
  const desktopVlc = useMemo(
    () => (isDesktop ? desktopVlcStatus(status, platform) : null),
    [isDesktop, platform, status],
  );
  const desktopRecommendedApp = useMemo(
    () => (isDesktop ? recommendedApps.find((app) => !app.mobile_status_key) || null : null),
    [isDesktop, recommendedApps],
  );
  const mobileRecommendedApps = useMemo(
    () => recommendedApps.filter((app) => app.mobile_status_key),
    [recommendedApps],
  );

  function handleMobileAppStatusChange(statusKey, nextStatus) {
    setMobileAppStatus((current) => ({
      ...current,
      [statusKey]: nextStatus,
    }));
    setAppCheckPendingKey("");
  }

  function handleRecommendedAppAction(app) {
    if (!app.mobile_status_key && !isDesktop) {
      window.open(app.primary_url, "_blank", "noopener,noreferrer");
      return;
    }
    if (platform === "iphone" || platform === "ipad") {
      setAppCheckPendingKey(app.mobile_status_key);
      verifyMobileAppInstall({
        openUrl: app.open_url,
        statusKey: app.mobile_status_key,
        onStatusChange: handleMobileAppStatusChange,
      });
      return;
    }
    window.open(app.primary_url, "_blank", "noopener,noreferrer");
  }

  async function handleDesktopVlcVerify() {
    if (!isDesktop || !deviceId) {
      return;
    }
    cancelVerifyLifecycle();
    const verifyGeneration = verifyGenerationRef.current;
    setDesktopVerifyPending(true);
    verifyPendingRef.current = true;
    statusRequestGenerationRef.current += 1;
    statusRequestControllerRef.current?.abort();
    const verifyController = new AbortController();
    verifyRequestControllerRef.current = verifyController;
    setError("");
    setDesktopVerifyFeedback("");
    const previousCheckedAt = status?.vlc_detection_checked_at || "";
    const previousState = status?.vlc_detection_state || "";
    try {
      const payload = await apiRequest("/api/desktop-helper/verify", {
        method: "POST",
        signal: verifyController.signal,
        data: {
          platform,
          device_id: deviceId,
        },
      });
      if (
        !mountedRef.current
        || verifyGenerationRef.current !== verifyGeneration
      ) {
        return;
      }
      if (payload.status) {
        setStatus(payload.status);
        setDesktopVerifyFeedback(desktopHelperFeedbackForStatus(platform, payload.status));
        return;
      }
      if (!payload.protocol_url) {
        const refreshed = await loadDesktopStatus({ showLoading: false });
        if (refreshed) {
          setDesktopVerifyFeedback(desktopHelperFeedbackForStatus(platform, refreshed));
        }
        return;
      }
      setDesktopVerifyFeedback(
        "Trying the client-side helper now. If nothing opens and this page does not update, the protocol handler is probably not installed or not registered on this device.",
      );
      window.location.assign(payload.protocol_url);
      const deadline = Date.now() + 8000;
      let callbackSeen = false;
      while (Date.now() < deadline) {
        const shouldContinue = await new Promise((resolve) => {
          verifyPollResolveRef.current = resolve;
          verifyPollTimerRef.current = window.setTimeout(() => {
            verifyPollTimerRef.current = 0;
            verifyPollResolveRef.current = null;
            resolve(true);
          }, 900);
        });
        if (
          !shouldContinue
          || !mountedRef.current
          || verifyGenerationRef.current !== verifyGeneration
        ) {
          return;
        }
        const refreshed = await loadDesktopStatus({ showLoading: false });
        if (
          !mountedRef.current
          || verifyGenerationRef.current !== verifyGeneration
        ) {
          return;
        }
        if (!refreshed) {
          continue;
        }
        if (
          (refreshed.vlc_detection_checked_at || "") !== previousCheckedAt
          || (refreshed.vlc_detection_state || "") !== previousState
        ) {
          callbackSeen = true;
          setDesktopVerifyFeedback(desktopHelperFeedbackForStatus(platform, refreshed));
          break;
        }
      }
      if (
        !callbackSeen
        && mountedRef.current
        && verifyGenerationRef.current === verifyGeneration
      ) {
        setDesktopVerifyFeedback(
          "No helper check-back reached Elvern yet. If nothing opened, install or re-register the helper on this device and try again.",
        );
      }
    } catch (requestError) {
      if (
        mountedRef.current
        && verifyGenerationRef.current === verifyGeneration
        && !isAbortError(requestError)
      ) {
        setError(requestError.message || "Failed to verify VLC");
      }
    } finally {
      if (verifyRequestControllerRef.current === verifyController) {
        verifyRequestControllerRef.current = null;
      }
      if (verifyGenerationRef.current !== verifyGeneration) {
        return;
      }
      verifyPendingRef.current = false;
      if (mountedRef.current) {
        setDesktopVerifyPending(false);
      }
      drainPendingStatusRefreshRef.current();
    }
  }

  async function handleCopyMacTerminalCommand() {
    if (!requiredSection.packageRelease) {
      return;
    }
    setTerminalCommandFeedback("");
    try {
      const command = buildMacTerminalInstallCommand(requiredSection.packageRelease);
      await copyTextToClipboard(command);
      setTerminalCommandFeedback("Copied");
    } catch (copyError) {
      setTerminalCommandFeedback(copyError.message || "Could not copy the Terminal install command.");
    }
  }

  const helperDownloadLabel = platform === "mac"
    ? "Download for macOS"
    : platform === "linux"
      ? "Download for Linux"
      : "Download for Windows";

  if (presentation === "meridian") {
    return (
      <article className="meridian-card meridian-helper-card">
        <div className="meridian-helper-card__header">
          <span aria-hidden="true" className="meridian-icon-tile meridian-icon-tile--play">▶</span>
          <span className="meridian-row-copy">
            <strong>Elvern VLC Opener</strong>
            <small>{requiredSection.description || "Open media in VLC through the verified Elvern desktop handoff."}</small>
          </span>
          <span className="meridian-status-pill meridian-status-pill--neutral">Detected platform: {platformLabel(platform)}</span>
          <span className={`meridian-status-pill meridian-status-pill--${status?.vlc_detection_state === "installed" ? "success" : "neutral"}`}>
            {loading ? "Checking" : helperStatusCopy(status)}
          </span>
        </div>
        {error ? <p className="meridian-inline-error" role="alert">{error}</p> : null}
        {reconnecting ? <p className="meridian-muted-copy">Reconnecting…</p> : null}
        <div className="meridian-actions">
          <button
            className="meridian-pill-button meridian-pill-button--primary"
            disabled={desktopVerifyPending}
            onClick={handleDesktopVlcVerify}
            type="button"
          >
            {desktopVerifyPending ? "Checking…" : desktopHelperTestButtonLabel(platform, status)}
          </button>
          {requiredSection.recommendedRelease && status?.helper_required !== false ? (
            <a className="meridian-pill-button" href={requiredSection.recommendedRelease.download_url}>{helperDownloadLabel}</a>
          ) : desktopRecommendedApp ? (
            <a className="meridian-pill-button" href={desktopRecommendedApp.primary_url} rel="noopener noreferrer" target="_blank">Download VLC</a>
          ) : null}
        </div>
        <p className="meridian-helper-card__notes">
          {desktopHelperTestCopy(platform, status)}<br />
          {desktopVlc?.copy}<br />
          Last checked: {formatLastChecked(status?.vlc_detection_checked_at)}
        </p>
        {desktopVerifyFeedback ? <p aria-live="polite" className="meridian-inline-feedback" role="status">{desktopVerifyFeedback}</p> : null}
        <details className="meridian-details">
          <summary>Details</summary>
          <dl>
            <div><dt>Package version</dt><dd>{requiredSection.recommendedRelease?.version || "Unavailable"}</dd></div>
            <div><dt>Last seen helper version</dt><dd>{status?.last_seen_helper_version || "Unknown"}</dd></div>
            <div><dt>Last callback</dt><dd>{formatLastChecked(status?.last_seen_helper_at)}</dd></div>
            <div><dt>Reported architecture</dt><dd>{status?.last_seen_helper_arch || "Unknown"}</dd></div>
            <div><dt>Package target</dt><dd>{requiredSection.recommendedRelease?.package_target || "Unavailable"}</dd></div>
            <div><dt>Runtime</dt><dd>{status?.runtime_included ? "Included" : "Not reported"}</dd></div>
            <div><dt>Device ID</dt><dd>{status?.device_id || deviceId || "Unknown"}</dd></div>
          </dl>
        </details>
      </article>
    );
  }

  return (
    <section className="page-section">
      <div className="section-header">
        <div>
          <p className="eyebrow">Install</p>
          <h1>{isDesktop ? "Install apps and helper for this device" : "Install apps for this device"}</h1>
          <p className="page-subnote">
            {platform === "unknown"
              ? "Platform could not be detected."
              : `Detected platform: ${platformLabel(platform)}`}
          </p>
        </div>
      </div>

      {error ? (
        <div>
          <p className="form-error">{error}</p>
          {!status ? (
            <button className="ghost-button ghost-button--inline" onClick={() => loadDesktopStatus()} type="button">
              Retry
            </button>
          ) : null}
        </div>
      ) : null}
      {reconnecting ? <p className="page-subnote">Reconnecting…</p> : null}

      {showRequiredSection ? (
        <section className="page-section">
          <article className="settings-card install-section-card settings-card--wide desktop-helper-card">
            <div className="install-card__header">
              <div>
                <h2>Elvern VLC Opener</h2>
                <p className="page-subnote">{requiredSection.description}</p>
              </div>
              <span className="status-pill">{loading ? "Checking" : helperStatusCopy(status)}</span>
            </div>

            {requiredSection.recommendedRelease && status?.helper_required !== false ? (
              <div className="install-card__actions">
                <a className="primary-button" href={requiredSection.recommendedRelease.download_url}>
                  {helperDownloadLabel}
                </a>
                <p className="page-subnote">
                  Version {requiredSection.recommendedRelease.version} · {formatBytes(requiredSection.recommendedRelease.size_bytes)}
                  {requiredSection.packageRelease ? " · Runtime included" : ""}
                </p>
                {platform === "mac" && requiredSection.packageRelease ? (
                  <p className="page-subnote">Includes Apple Silicon and Intel versions. The installer selects automatically.</p>
                ) : null}
                {platform === "linux" && requiredSection.packageRelease ? (
                  <p className="page-subnote">Includes x64 and ARM64 builds for glibc and musl. The installer selects automatically.</p>
                ) : null}
              </div>
            ) : null}

            <div className="install-card__actions install-card__actions--inline">
              <button
                className="primary-button"
                disabled={desktopVerifyPending}
                onClick={handleDesktopVlcVerify}
                type="button"
              >
                {desktopVerifyPending ? "Checking..." : desktopHelperTestButtonLabel(platform, status)}
              </button>
              {desktopRecommendedApp ? (
                <a
                  className="ghost-button ghost-button--inline"
                  href={desktopRecommendedApp.primary_url}
                  rel="noopener noreferrer"
                  target="_blank"
                >
                  Download VLC
                </a>
              ) : null}
            </div>
            <p className="page-subnote">{desktopHelperTestCopy(platform, status)}</p>
            <p className="page-subnote">{desktopVlc?.copy}</p>
            <p className="page-subnote">Last checked: {formatLastChecked(status?.vlc_detection_checked_at)}</p>
            <p aria-live="polite" className="page-note desktop-helper-card__feedback" role="status">
              {desktopVerifyFeedback}
            </p>

            {requiredSection.legacyReleases.length ? (
              <details className="desktop-helper-card__details">
                <summary>More options...</summary>
                <div className="desktop-helper-list">
                  {requiredSection.legacyReleases.map((release) => (
                    <article className="desktop-helper-release" key={release.id}>
                      <div className="desktop-helper-release__meta">
                        <h3>{releaseLabel(release)}{release.recommended ? " (Recommended)" : ""}</h3>
                        <p className="page-subnote">Version {release.version} · {formatBytes(release.size_bytes)}</p>
                      </div>
                      <a className="ghost-button ghost-button--inline" href={release.download_url}>Download</a>
                    </article>
                  ))}
                </div>
              </details>
            ) : null}

            {platform === "mac" && requiredSection.packageRelease ? (
              <details className="desktop-helper-card__details">
                <summary>Installation help</summary>
                <p className="page-subnote">The app installs to ~/Applications/Elvern VLC Opener.app without administrator access.</p>
                <button className="ghost-button ghost-button--inline" onClick={handleCopyMacTerminalCommand} type="button">
                  Copy Terminal install command
                </button>
                {terminalCommandFeedback ? (
                  <p aria-live="polite" className="page-subnote" role="status">{terminalCommandFeedback}</p>
                ) : null}
                <p className="page-subnote">If macOS still blocks the verified installer, use System Settings → Privacy &amp; Security → Open Anyway.</p>
              </details>
            ) : null}

            <details className="desktop-helper-card__details">
              <summary>Details</summary>
              <div className="status-row"><span>Package version</span><strong>{requiredSection.recommendedRelease?.version || "Unavailable"}</strong></div>
              <div className="status-row"><span>Last seen helper version</span><strong>{status?.last_seen_helper_version || "Unknown"}</strong></div>
              <div className="status-row"><span>Last callback</span><strong>{formatLastChecked(status?.last_seen_helper_at)}</strong></div>
              <div className="status-row"><span>Reported architecture</span><strong>{status?.last_seen_helper_arch || "Unknown"}</strong></div>
              <div className="status-row"><span>Package target</span><strong>{requiredSection.recommendedRelease?.package_target || "Unavailable"}</strong></div>
              <div className="status-row"><span>Runtime</span><strong>{status?.runtime_included ? "Included" : "Not reported"}</strong></div>
              <div className="status-row">
                <span>Package binding</span>
                <strong>
                  {requiredSection.recommendedRelease?.package_binding === "compatible"
                    ? "Compatible"
                    : requiredSection.recommendedRelease?.package_binding === "incompatible"
                      ? "Incompatible"
                      : "Legacy unverified"}
                </strong>
              </div>
              <div className="status-row"><span>Device ID</span><strong>{status?.device_id || deviceId || "Unknown"}</strong></div>
              {status?.notes?.map((note) => <p className="page-subnote" key={note}>{note}</p>)}
            </details>
          </article>
        </section>
      ) : null}

      {mobileRecommendedApps.length ? (
        <section className="page-section">
          <article className="settings-card install-section-card settings-card--wide">
            <div className="install-section-card__header">
              <h2>Recommended Apps</h2>
              <p className="page-subnote">
                Platform-aware app installs and downloads for this device.
              </p>
            </div>

            <div className="install-app-grid">
              {mobileRecommendedApps.map((app) => {
              const appStatus = app.mobile_status_key
                ? mobileAppStatus[app.mobile_status_key] || normalizeMobileAppInstallState(null)
                : normalizeMobileAppInstallState(null);
              const statusLabel = app.mobile_status_key
                ? mobileAppStatusLabel(appStatus)
                : desktopVlc?.label || "Install status unavailable";
              const statusCopy = app.mobile_status_key
                ? mobileAppStatusCopy(appStatus, platform)
                : desktopVlc?.copy || "Browsers cannot verify local install state here.";
              const buttonLabel = app.mobile_status_key
                ? (appCheckPendingKey === app.mobile_status_key
                  ? "Testing..."
                  : mobileAppButtonLabel(appStatus, platform))
                : (desktopVerifyPending ? "Verifying..." : "Verify");
              return (
                <article className="install-card install-card--app" key={app.id}>
                  <div className="install-card__copy">
                    <div className="install-card__header">
                      <h3>{app.name}</h3>
                      <span className="status-pill">
                        {statusLabel}
                      </span>
                    </div>
                    <p className="page-note">{app.description}</p>
                    <p className="page-subnote">{statusCopy}</p>
                    <div className="install-card__actions">
                      <button
                        className="primary-button"
                        disabled={app.mobile_status_key ? appCheckPendingKey === app.mobile_status_key : desktopVerifyPending}
                        onClick={() => (app.mobile_status_key ? handleRecommendedAppAction(app) : handleDesktopVlcVerify())}
                        type="button"
                      >
                        {buttonLabel}
                      </button>
                      {platform === "iphone" || platform === "ipad" ? (
                        <a
                          className="ghost-button ghost-button--inline"
                          href={app.primary_url}
                          rel="noopener noreferrer"
                          target="_blank"
                        >
                          App Store
                        </a>
                      ) : !app.mobile_status_key ? (
                        <a
                          className="ghost-button ghost-button--inline"
                          href={app.primary_url}
                          rel="noopener noreferrer"
                          target="_blank"
                        >
                          Download
                        </a>
                      ) : (
                        <a
                          className="ghost-button ghost-button--inline"
                          href={app.primary_url}
                          rel="noopener noreferrer"
                          target="_blank"
                        >
                          Google Play
                        </a>
                      )}
                    </div>
                    {app.mobile_status_key ? (
                      <p className="page-subnote">
                        Last checked: {formatLastChecked(appStatus.lastCheckedAt)}
                      </p>
                    ) : !app.mobile_status_key ? (
                      <p className="page-subnote">
                        Last checked: {formatLastChecked(status?.vlc_detection_checked_at)}
                      </p>
                    ) : null}
                  </div>
                </article>
              );
              })}
            </div>
          </article>
        </section>
      ) : null}
    </section>
  );
}
