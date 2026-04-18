import { useCallback, useEffect, useMemo, useState } from "react";
import { apiRequest } from "../lib/api";
import { getOrCreateDeviceId } from "../lib/device";


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
  if (typeof navigator === "undefined") {
    return "linux";
  }
  const agent = (navigator.userAgent || "").toLowerCase();
  const platform = (navigator.platform || "").toLowerCase();
  const maxTouchPoints = Number(navigator.maxTouchPoints || 0);
  const iPadDesktopClassAgent =
    maxTouchPoints > 1 && (agent.includes("macintosh") || platform.includes("mac"));

  if (agent.includes("iphone") || agent.includes("ipod")) {
    return "iphone";
  }
  if (agent.includes("ipad") || iPadDesktopClassAgent) {
    return "ipad";
  }
  if (agent.includes("android")) {
    return "android";
  }
  if (agent.includes("windows")) {
    return "windows";
  }
  if (agent.includes("macintosh") || (agent.includes("mac os x") && !agent.includes("iphone") && !agent.includes("ipad"))) {
    return "mac";
  }
  if (agent.includes("linux") || platform.includes("linux") || agent.includes("x11")) {
    return "linux";
  }
  return "linux";
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
  return platform === "windows" || platform === "mac" || platform === "linux";
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
    default:
      return "Linux";
  }
}

function releaseLabel(release) {
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
      return "Not required on this Linux desktop";
    case "up_to_date":
      return "Up to date";
    case "update_available":
      return "Update available";
    case "release_unavailable":
      return "Installer package unavailable";
    default:
      return "Install state unknown";
  }
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
      status: "installed",
      lastCheckedAt: null,
    };
  }

  if (typeof value === "object" && value !== null) {
    const normalizedStatus =
      value.status === "installed" || value.status === "could_not_verify" || value.status === "not_verified"
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
    return normalizeMobileAppInstallState(JSON.parse(raw));
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
  if (appState.status === "installed") {
    return "Open";
  }
  return platform === "android" ? "Get" : "Test";
}

function mobileAppStatusLabel(appState) {
  if (appState.status === "installed") {
    return "Installed";
  }
  if (appState.status === "could_not_verify" || appState.status === "not_verified") {
    return "Could not verify open";
  }
  return "Install status unverified";
}

function mobileAppStatusCopy(appState, platform) {
  if (appState.status === "installed") {
    return "Verified by successfully opening the app scheme from this device.";
  }
  if (appState.status === "could_not_verify" || appState.status === "not_verified") {
    return "A failed test only means the web could not confirm a successful app handoff.";
  }
  if (platform === "android") {
    return "Mobile web cannot reliably verify installed Android apps here.";
  }
  return "Use Test to try the app scheme. Only a successful handoff marks the app as installed.";
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
      label: "Installed",
      copy: detectionPath
        ? `Verified by a grounded ${platform === "linux" ? "host-side" : "desktop helper"} VLC lookup at ${detectionPath}.`
        : `Verified by a grounded ${platform === "linux" ? "host-side" : "desktop helper"} VLC lookup.`,
    };
  }
  if (detectionState === "not_detected") {
    return {
      label: "Not detected",
      copy: platform === "linux"
        ? "Elvern could not find VLC on this Linux host."
        : "The desktop helper last reported that VLC was not detected on this device.",
    };
  }
  return {
    label: "Install state unknown",
    copy: "Install state unknown",
  };
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

  if (platform === "linux") {
    return {
      empty: true,
      description: "Nothing is required for this setup.",
      recommendedRelease: null,
    };
  }

  const recommendedRelease = status?.latest_releases?.find((release) => release.recommended)
    || status?.latest_releases?.[0]
    || null;
  return {
    empty: false,
    description: platform === "linux"
      ? "Linux same-host playback keeps using installed VLC directly. No helper install is required for the supported Linux baseline."
      : "Elvern VLC Opener keeps desktop VLC handoff working cleanly on this platform.",
    recommendedRelease,
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

  function markOpened() {
    if (finished) {
      return;
    }
    finished = true;
    const nextState = {
      status: "installed",
      lastCheckedAt: Date.now(),
    };
    writeMobileAppStatus(statusKey, nextState);
    onStatusChange(statusKey, nextState);
    cleanup();
  }

  function handleVisibilityChange() {
    if (document.visibilityState === "hidden") {
      markOpened();
    }
  }

  function handlePageHide() {
    markOpened();
  }

  function handleWindowBlur() {
    blurConfirmTimer = window.setTimeout(() => {
      if (finished) {
        return;
      }
      if (document.visibilityState === "hidden" || !document.hasFocus()) {
        markOpened();
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
    cleanup();
    const nextState = {
      status: "could_not_verify",
      lastCheckedAt: Date.now(),
    };
    writeMobileAppStatus(statusKey, nextState);
    onStatusChange(statusKey, nextState);
  }, 2200);
}

function openMobileAppScheme(openUrl) {
  if (typeof window === "undefined" || !openUrl) {
    return;
  }
  window.location.assign(openUrl);
}


export function InstallPage() {
  const platform = useMemo(() => detectInstallPlatform(), []);
  const iosStoreRegion = useMemo(() => detectIosStoreRegion(), []);
  const isDesktop = isDesktopPlatform(platform);
  const deviceId = useMemo(() => (isDesktop ? getOrCreateDeviceId() : ""), [isDesktop]);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(isDesktop);
  const [appCheckPendingKey, setAppCheckPendingKey] = useState("");
  const [desktopVerifyPending, setDesktopVerifyPending] = useState(false);
  const [requiredPanelExpanded, setRequiredPanelExpanded] = useState(false);
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
    setError("");
    try {
      const params = new URLSearchParams({ platform });
      if (deviceId) {
        params.set("device_id", deviceId);
      }
      const payload = await apiRequest(`/api/desktop-helper/status?${params.toString()}`);
      setStatus(payload);
      return payload;
    } catch (requestError) {
      setError(requestError.message || "Failed to load install status");
      return null;
    } finally {
      if (showLoading) {
        setLoading(false);
      }
    }
  }, [deviceId, isDesktop, platform]);

  useEffect(() => {
    if (!isDesktop) {
      return undefined;
    }

    let cancelled = false;

    async function loadStatus() {
      const payload = await loadDesktopStatus();
      if (!cancelled && payload) {
        setStatus(payload);
      }
    }

    loadStatus();
    return () => {
      cancelled = true;
    };
  }, [isDesktop, loadDesktopStatus]);

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
      window.location.href = app.primary_url;
      return;
    }
    if (platform === "iphone" || platform === "ipad") {
      const currentStatus = mobileAppStatus[app.mobile_status_key] || normalizeMobileAppInstallState(null);
      if (currentStatus.status === "installed") {
        openMobileAppScheme(app.open_url);
        return;
      }
      setAppCheckPendingKey(app.mobile_status_key);
      verifyMobileAppInstall({
        openUrl: app.open_url,
        statusKey: app.mobile_status_key,
        onStatusChange: handleMobileAppStatusChange,
      });
      return;
    }
    window.location.href = app.primary_url;
  }

  async function handleDesktopVlcVerify() {
    if (!isDesktop || !deviceId) {
      return;
    }
    setDesktopVerifyPending(true);
    setError("");
    const previousCheckedAt = status?.vlc_detection_checked_at || "";
    const previousState = status?.vlc_detection_state || "";
    try {
      const payload = await apiRequest("/api/desktop-helper/verify", {
        method: "POST",
        data: {
          platform,
          device_id: deviceId,
        },
      });
      if (payload.status) {
        setStatus(payload.status);
        return;
      }
      if (!payload.protocol_url) {
        await loadDesktopStatus({ showLoading: false });
        return;
      }
      window.location.assign(payload.protocol_url);
      const deadline = Date.now() + 8000;
      while (Date.now() < deadline) {
        await new Promise((resolve) => window.setTimeout(resolve, 900));
        const refreshed = await loadDesktopStatus({ showLoading: false });
        if (!refreshed) {
          continue;
        }
        if (
          (refreshed.vlc_detection_checked_at || "") !== previousCheckedAt
          || (refreshed.vlc_detection_state || "") !== previousState
        ) {
          break;
        }
      }
    } catch (requestError) {
      setError(requestError.message || "Failed to verify VLC");
    } finally {
      setDesktopVerifyPending(false);
    }
  }

  return (
    <section className="page-section">
      <div className="section-header">
        <div>
          <p className="eyebrow">Install</p>
          <h1>Install apps for this device</h1>
          <p className="page-subnote">
            Detected platform: {platformLabel(platform)}
          </p>
        </div>
      </div>

      {error ? <p className="form-error">{error}</p> : null}

      {showRequiredSection ? (
        <section className="page-section">
          <details
            className="settings-card install-section-card settings-card--wide settings-disclosure"
            open={requiredPanelExpanded}
            onToggle={(event) => setRequiredPanelExpanded(event.currentTarget.open)}
          >
            <summary className="settings-disclosure__summary">
              <div className="settings-disclosure__header">
                <span className="settings-disclosure__title">Required</span>
                {requiredSection.description ? (
                  <span className="settings-disclosure__copy">{requiredSection.description}</span>
                ) : null}
              </div>
              <div className="settings-disclosure__summary-meta">
                <span className="status-pill">
                  {loading ? "Checking..." : stateCopy(status?.state)}
                </span>
              </div>
            </summary>

            <div className="install-list">
              <article className="install-card install-card--wide">
                <div className="install-card__copy">
                  <h3>Elvern VLC Opener</h3>
                  {loading ? <p className="page-note">Checking install status…</p> : null}
                  {status ? (
                    <>
                      <div className="status-row">
                        <span>Status</span>
                        <strong>{stateCopy(status.state)}</strong>
                      </div>
                      <div className="status-row">
                        <span>Detected platform</span>
                        <strong>{status.platform}</strong>
                      </div>
                      <div className="status-row">
                        <span>Last seen helper version</span>
                        <strong>{status.last_seen_helper_version || "Unknown"}</strong>
                      </div>
                      <div className="status-row">
                        <span>Device ID</span>
                        <strong>{status.device_id || deviceId || "Unknown"}</strong>
                      </div>
                    </>
                  ) : null}
                  {requiredSection.recommendedRelease ? (
                    <div className="install-card__actions">
                      <a
                        className="primary-button"
                        href={requiredSection.recommendedRelease.download_url}
                      >
                        {status?.state === "update_available" ? "Download update" : "Download installer"}
                      </a>
                      <p className="page-subnote">
                        {releaseLabel(requiredSection.recommendedRelease)} · Version {requiredSection.recommendedRelease.version} · {formatBytes(requiredSection.recommendedRelease.size_bytes)}
                      </p>
                    </div>
                  ) : null}
                  {status?.notes?.length ? (
                    <div className="install-card__notes">
                      {status.notes.map((note) => (
                        <p className="page-subnote" key={note}>
                          {note}
                        </p>
                      ))}
                    </div>
                  ) : null}
                </div>
              </article>
            </div>
          </details>
        </section>
      ) : null}

      {isDesktop && desktopRecommendedApp ? (
        <section className="page-section">
          <div className="section-header section-header--compact">
            <div>
              <h2>Recommended</h2>
              <p className="page-subnote">
                Desktop VLC check and download for this device.
              </p>
            </div>
          </div>

          <div className="install-vlc-card-row">
            <article className="install-card install-card--vlc">
              <div className="install-card__copy">
                <div className="install-card__header">
                  <h3>{desktopRecommendedApp.name}</h3>
                  <span className="status-pill">
                    {desktopVlc?.label || "Install status unavailable"}
                  </span>
                </div>
                <p className="page-note">{desktopRecommendedApp.description}</p>
                <p className="page-subnote">
                  {desktopVlc?.copy || "Browsers cannot verify local install state here."}
                </p>
                <div className="install-card__actions">
                  <button
                    className="primary-button"
                    disabled={desktopVerifyPending}
                    onClick={handleDesktopVlcVerify}
                    type="button"
                  >
                    {desktopVerifyPending ? "Verifying..." : "Verify"}
                  </button>
                  <a
                    className="ghost-button ghost-button--inline"
                    href={desktopRecommendedApp.primary_url}
                  >
                    Download
                  </a>
                </div>
                <p className="page-subnote">
                  Last checked: {formatLastChecked(status?.vlc_detection_checked_at)}
                </p>
              </div>
            </article>
          </div>
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
                        >
                          App Store
                        </a>
                      ) : !app.mobile_status_key ? (
                        <a
                          className="ghost-button ghost-button--inline"
                          href={app.primary_url}
                        >
                          Download
                        </a>
                      ) : null}
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
