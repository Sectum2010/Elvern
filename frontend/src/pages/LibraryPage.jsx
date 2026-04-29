import { startTransition, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { EmptyState } from "../components/EmptyState";
import { LoadingView } from "../components/LoadingView";
import { MediaCard } from "../components/MediaCard";
import { ProviderReconnectModal } from "../components/ProviderReconnectModal";
import { SeriesRail } from "../components/SeriesRail";
import { apiRequest } from "../lib/api";
import { useActiveBrowserPlaybackItemId } from "../lib/browserPlayback";
import {
  clearLibraryCloudReconnectDismissal,
  dismissLibraryCloudReconnectPrompt,
  formatCompletedRescanWarning,
  formatRescanBannerText,
  getCloudReconnectPrompt,
  hasCloudSyncWarning,
  isCloudReconnectRequired,
  readLibraryCloudReconnectDismissed,
} from "../lib/cloudSyncStatus";
import {
  clearLibraryReturnPending,
  readLibraryReturnTarget,
} from "../lib/libraryNavigation";
import { startGoogleDriveReconnect } from "../lib/providerAuth";
import { detectClientPlatform } from "../lib/platformDetection";
import {
  packIpadPortraitSeriesRailRows,
  packSeriesRailRows,
} from "../lib/seriesRails";
import { getSmartPosterOrientation } from "../lib/smartPosterLoading";
import {
  canUpdateStableViewportAnchor,
  captureCenterMovieAnchor,
  captureViewportAnchorCandidates,
  computeAnchorRestoreScrollTop,
  computeRestoreVerificationCorrection,
  formatViewportAnchorDebug,
  formatViewportAnchorCandidateListDebug,
  getLayoutViewportMeasurement,
  getOrientationRestoreRefinementDelayMs,
  getRestoreViewportMeasurement,
  isLibraryOrientationRestorePlatform,
  isRestoreAttemptStale,
  isUserRestoreCancellationEvent,
  isVisualViewportZoomed,
  MAX_ORIENTATION_RESTORE_CORRECTIONS,
  requestTemporaryViewportScaleReset,
  resolveStableOrientationAnchor,
  restoreHorizontalRailPosition,
  selectLibraryReturnRestoreTarget,
  selectPreferredOrientationRestoreTarget,
  shouldRecoverZoomedLibraryRotation,
  shouldLogViewportAnchorDebug,
} from "../lib/viewportAnchor";


function MediaGrid({
  items,
  activeBrowserPlaybackItemId = null,
  smartPosterLoadingEnabled = false,
  sectionKey = "library",
}) {
  return (
    <div className="media-grid">
      {items.map((item) => (
        <MediaCard
          backgroundPlaybackActive={activeBrowserPlaybackItemId === item.id}
          cardInstanceKey={`${sectionKey}:${item.id}`}
          item={item}
          key={item.id}
          smartPosterLoadingEnabled={smartPosterLoadingEnabled}
        />
      ))}
    </div>
  );
}

function formatMovieCount(count) {
  return `${count} ${count === 1 ? "movie" : "movies"}`;
}

function isIpadPortraitLibraryViewport() {
  if (typeof window === "undefined") {
    return false;
  }
  return detectClientPlatform() === "ipad"
    && window.matchMedia("(min-width: 740px)").matches;
}

function useIpadPortraitLibraryLayout() {
  const [enabled, setEnabled] = useState(() => isIpadPortraitLibraryViewport());

  useEffect(() => {
    function updateLayoutMode() {
      setEnabled(isIpadPortraitLibraryViewport());
    }

    updateLayoutMode();
    window.addEventListener("resize", updateLayoutMode);
    window.addEventListener("orientationchange", updateLayoutMode);
    return () => {
      window.removeEventListener("resize", updateLayoutMode);
      window.removeEventListener("orientationchange", updateLayoutMode);
    };
  }, []);

  return enabled;
}

export function LibraryPage() {
  const { refreshAuth } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const activeBrowserPlaybackItemId = useActiveBrowserPlaybackItemId();
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const [settings, setSettings] = useState({
    hide_duplicate_movies: true,
    hide_recently_added: false,
  });
  const [loading, setLoading] = useState(true);
  const [rescanPending, setRescanPending] = useState(false);
  const [providerReconnectPending, setProviderReconnectPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [cloudLibraries, setCloudLibraries] = useState({
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
  });
  const [showCloudReconnectModal, setShowCloudReconnectModal] = useState(false);
  const [library, setLibrary] = useState({
    items: [],
    series_rails: [],
    cloud_series_rails: [],
    continue_watching: [],
    recently_added: [],
    total_items: 0,
    scan_in_progress: false,
  });
  const [sourceCounts, setSourceCounts] = useState({ local: 0, cloud: 0 });
  const cloudSyncWarningRef = useRef("");
  const scanRunningRef = useRef(false);
  const orientationAnchorsRef = useRef([]);
  const latestCenterMovieAnchorRef = useRef(null);
  const lastStableLibraryAnchorRef = useRef(null);
  const pendingOrientationAnchorRef = useRef(null);
  const orientationRef = useRef(null);
  const orientationLastMeasurementRef = useRef(null);
  const orientationRestoreTimerRef = useRef(0);
  const orientationRestoreFrameOneRef = useRef(0);
  const orientationRestoreFrameTwoRef = useRef(0);
  const orientationRestoreRefineTimerRef = useRef(0);
  const orientationSampleFrameRef = useRef(0);
  const orientationRestoreLockRef = useRef(false);
  const orientationViewportChangeActiveRef = useRef(false);
  const orientationRestoreTokenRef = useRef(0);
  const orientationRestoreCorrectionCountRef = useRef(0);
  const orientationUserIntentVersionRef = useRef(0);
  const orientationSamplerRef = useRef(() => {});
  const orientationDebugLogAtRef = useRef(0);
  const libraryReturnRestoreKeyRef = useRef("");
  const useIpadPortraitSeriesPacking = useIpadPortraitLibraryLayout();
  const isPhoneClient = useMemo(() => {
    if (typeof navigator === "undefined") {
      return false;
    }
    const userAgent = navigator.userAgent || "";
    return /iphone|ipod|android.+mobile|windows phone/i.test(userAgent);
  }, []);
  const continueWatchingLimit = 6;
  const continueWatchingItems = useMemo(
    () => library.continue_watching.map((item) => {
      if (
        !isPhoneClient
        || (item.source_kind || "local") !== "cloud"
        || !item.progress_seconds
        || item.progress_duration_seconds
        || !item.duration_seconds
      ) {
        return item;
      }
      return {
        ...item,
        progress_duration_seconds: item.duration_seconds,
      };
    }),
    [isPhoneClient, library.continue_watching],
  );
  const visibleContinueWatchingItems = useMemo(
    () => continueWatchingItems.slice(0, continueWatchingLimit),
    [continueWatchingItems, continueWatchingLimit],
  );
  const showContinueWatchingSection = visibleContinueWatchingItems.length > 0;
  const visibleSeriesRails = useMemo(
    () => [
      ...(library.series_rails || []),
      ...(library.cloud_series_rails || []),
    ],
    [library.cloud_series_rails, library.series_rails],
  );
  const seriesRailItemIds = useMemo(
    () => new Set(
      visibleSeriesRails.flatMap((rail) => (rail.items || []).map((item) => item.id)),
    ),
    [visibleSeriesRails],
  );
  const visibleLibraryGridItems = useMemo(
    () => library.items.filter((item) => !seriesRailItemIds.has(item.id)),
    [library.items, seriesRailItemIds],
  );
  const packedSeriesRailRows = useMemo(
    () => (useIpadPortraitSeriesPacking
      ? packIpadPortraitSeriesRailRows(visibleSeriesRails)
      : packSeriesRailRows(visibleSeriesRails)),
    [useIpadPortraitSeriesPacking, visibleSeriesRails],
  );
  const cloudReconnectPrompt = useMemo(
    () => getCloudReconnectPrompt(cloudLibraries),
    [cloudLibraries],
  );

  async function loadLibrary({ signal, silent = false } = {}) {
    if (!silent) {
      startTransition(() => {
        setLoading(true);
      });
    }
    setError("");
    try {
      const target = deferredQuery.trim()
        ? `/api/library/search?q=${encodeURIComponent(deferredQuery.trim())}`
        : "/api/library";
      const payload = await apiRequest(target, { signal });
      if (!deferredQuery.trim()) {
        const nextSourceCounts = (payload.items || []).reduce(
          (counts, item) => {
            if ((item.source_kind || "local") === "cloud") {
              counts.cloud += 1;
            } else {
              counts.local += 1;
            }
            return counts;
          },
          { local: 0, cloud: 0 },
        );
        setSourceCounts(nextSourceCounts);
      }
      if (scanRunningRef.current && !payload.scan_in_progress) {
        if (cloudSyncWarningRef.current) {
          setError(formatCompletedRescanWarning(cloudSyncWarningRef.current));
          setNotice("");
        } else {
          setNotice("Library scan completed.");
        }
      }
      scanRunningRef.current = Boolean(payload.scan_in_progress);
      setLibrary(payload);
    } catch (requestError) {
      if (requestError.name === "AbortError") {
        return;
      }
      if (requestError.status === 401) {
        await refreshAuth();
        return;
      }
      setError(requestError.message || "Failed to load library");
    } finally {
      if (!silent) {
        setLoading(false);
      }
    }
  }

  async function loadLibrarySettings({ signal } = {}) {
    try {
      const payload = await apiRequest("/api/user-settings", { signal });
      setSettings(payload);
    } catch (requestError) {
      if (requestError.name === "AbortError") {
        return;
      }
      if (requestError.status === 401) {
        await refreshAuth();
      }
    }
  }

  async function loadCloudLibrariesHealth({ signal } = {}) {
    try {
      const payload = await apiRequest("/api/cloud-libraries", { signal });
      setCloudLibraries(payload);
      if (isCloudReconnectRequired(payload)) {
        setShowCloudReconnectModal(!readLibraryCloudReconnectDismissed());
      } else {
        clearLibraryCloudReconnectDismissal();
        setShowCloudReconnectModal(false);
      }
    } catch (requestError) {
      if (requestError.name === "AbortError") {
        return;
      }
      if (requestError.status === 401) {
        await refreshAuth();
      }
    }
  }

  async function handleCloudReconnect() {
    if (providerReconnectPending) {
      return;
    }
    setProviderReconnectPending(true);
    setError("");
    try {
      const currentUrl = new URL(window.location.href);
      currentUrl.searchParams.delete("googleDriveStatus");
      currentUrl.searchParams.delete("googleDriveMessage");
      const returnPath = `${currentUrl.pathname}${currentUrl.search}${currentUrl.hash}`;
      await startGoogleDriveReconnect({ returnPath });
    } catch (requestError) {
      setError(requestError.message || "Failed to start Google Drive reconnect");
    } finally {
      setProviderReconnectPending(false);
    }
  }

  function handleDismissCloudReconnectPrompt() {
    dismissLibraryCloudReconnectPrompt();
    setShowCloudReconnectModal(false);
  }

  useEffect(() => {
    const controller = new AbortController();
    loadLibrarySettings({ signal: controller.signal });
    loadLibrary({ signal: controller.signal });
    return () => {
      controller.abort();
    };
  }, [deferredQuery]);

  useEffect(() => {
    const controller = new AbortController();
    loadCloudLibrariesHealth({ signal: controller.signal });
    return () => {
      controller.abort();
    };
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const statusValue = params.get("googleDriveStatus");
    const statusMessage = params.get("googleDriveMessage");
    if (!statusValue && !statusMessage) {
      return;
    }
    if (statusValue === "connected") {
      clearLibraryCloudReconnectDismissal();
      setShowCloudReconnectModal(false);
      setNotice(statusMessage || "Google Drive connected.");
      setError("");
      void loadCloudLibrariesHealth();
    } else {
      setError(statusMessage || "Google Drive reconnect failed.");
      setNotice("");
    }
    const nextParams = new URLSearchParams(location.search);
    nextParams.delete("googleDriveStatus");
    nextParams.delete("googleDriveMessage");
    navigate(
      {
        pathname: location.pathname,
        search: nextParams.toString() ? `?${nextParams.toString()}` : "",
        hash: location.hash,
      },
      { replace: true },
    );
  }, [location.hash, location.pathname, location.search, navigate]);

  useEffect(() => {
    if (!library.scan_in_progress) {
      return undefined;
    }
    const intervalId = window.setInterval(() => {
      loadLibrary({ silent: true });
    }, 2500);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [library.scan_in_progress, deferredQuery]);

  useEffect(() => {
    if (typeof window === "undefined" || typeof document === "undefined") {
      return undefined;
    }
    const orientationPlatform = detectClientPlatform();
    if (!isLibraryOrientationRestorePlatform(orientationPlatform)) {
      return undefined;
    }
    const visualViewport = window.visualViewport || null;
    const MAJOR_VIEWPORT_CHANGE_PX = 140;

    function logOrientationAnchorDebug(message, details = {}) {
      if (!shouldLogViewportAnchorDebug()) {
        return;
      }
      const now = typeof performance !== "undefined" && typeof performance.now === "function"
        ? performance.now()
        : Date.now();
      if ((now - orientationDebugLogAtRef.current) < 1000) {
        return;
      }
      orientationDebugLogAtRef.current = now;
      console.info("[orientation-anchor]", {
        message,
        ...details,
      });
    }

    function readMeasurement() {
      return getLayoutViewportMeasurement({ viewportWindow: window });
    }

    function readOrientation(measurement = readMeasurement()) {
      return getSmartPosterOrientation({
        width: measurement.width,
        height: measurement.height,
      });
    }

    function clearPendingOrientationRestore(includeSampleFrame = false) {
      if (orientationRestoreTimerRef.current) {
        window.clearTimeout(orientationRestoreTimerRef.current);
        orientationRestoreTimerRef.current = 0;
      }
      if (orientationRestoreFrameOneRef.current) {
        window.cancelAnimationFrame(orientationRestoreFrameOneRef.current);
        orientationRestoreFrameOneRef.current = 0;
      }
      if (orientationRestoreFrameTwoRef.current) {
        window.cancelAnimationFrame(orientationRestoreFrameTwoRef.current);
        orientationRestoreFrameTwoRef.current = 0;
      }
      if (orientationRestoreRefineTimerRef.current) {
        window.clearTimeout(orientationRestoreRefineTimerRef.current);
        orientationRestoreRefineTimerRef.current = 0;
      }
      if (includeSampleFrame && orientationSampleFrameRef.current) {
        window.cancelAnimationFrame(orientationSampleFrameRef.current);
        orientationSampleFrameRef.current = 0;
      }
    }

    function isMajorViewportChange(nextMeasurement) {
      const previousMeasurement = orientationLastMeasurementRef.current;
      if (!previousMeasurement) {
        return false;
      }
      return (
        Math.abs(nextMeasurement.width - previousMeasurement.width) >= MAJOR_VIEWPORT_CHANGE_PX
        || Math.abs(nextMeasurement.height - previousMeasurement.height) >= MAJOR_VIEWPORT_CHANGE_PX
      );
    }

    function sampleLatestCenterMovieAnchor(reason = "sample") {
      if (orientationRestoreLockRef.current || orientationViewportChangeActiveRef.current) {
        return latestCenterMovieAnchorRef.current;
      }
      const measurement = readMeasurement();
      orientationLastMeasurementRef.current = measurement;
      if (!canUpdateStableViewportAnchor({
        platform: orientationPlatform,
        restoreInProgress: orientationRestoreLockRef.current || orientationViewportChangeActiveRef.current,
        viewportWindow: window,
      })) {
        return latestCenterMovieAnchorRef.current;
      }
      const nextAnchor = captureCenterMovieAnchor({
        doc: document,
        viewportWindow: window,
        orientation: readOrientation(measurement),
      });
      if (nextAnchor?.itemId) {
        latestCenterMovieAnchorRef.current = nextAnchor;
        lastStableLibraryAnchorRef.current = nextAnchor;
        logOrientationAnchorDebug("latest center movie anchor updated", {
          reason,
          latestCenterMovieItemId: nextAnchor.itemId,
        });
      }
      return nextAnchor || latestCenterMovieAnchorRef.current;
    }

    function scheduleCenterMovieAnchorSample({ reason = "sample", immediate = false } = {}) {
      if (immediate) {
        clearPendingOrientationRestore(true);
        return sampleLatestCenterMovieAnchor(reason);
      }
      if (
        orientationSampleFrameRef.current
        || orientationRestoreLockRef.current
        || orientationViewportChangeActiveRef.current
      ) {
        return latestCenterMovieAnchorRef.current;
      }
      orientationSampleFrameRef.current = window.requestAnimationFrame(() => {
        orientationSampleFrameRef.current = 0;
        sampleLatestCenterMovieAnchor(reason);
      });
      return latestCenterMovieAnchorRef.current;
    }

    function freezePendingOrientationAnchor(reason = "orientation_start") {
      if (pendingOrientationAnchorRef.current?.itemId) {
        return pendingOrientationAnchorRef.current;
      }
      const capturedAnchor = isVisualViewportZoomed({ viewportWindow: window })
        ? null
        : captureCenterMovieAnchor({
          doc: document,
          viewportWindow: window,
          orientation: readOrientation(),
        });
      const stableAnchor = resolveStableOrientationAnchor({
        lastStableAnchor: lastStableLibraryAnchorRef.current,
        latestAnchor: latestCenterMovieAnchorRef.current,
        isZoomed: isVisualViewportZoomed({ viewportWindow: window }),
        capturedAnchor,
      });
      pendingOrientationAnchorRef.current = stableAnchor?.itemId ? stableAnchor : null;
      logOrientationAnchorDebug("frozen orientation anchor", {
        reason,
        latestCenterMovieItemId: latestCenterMovieAnchorRef.current?.itemId || null,
        frozenOrientationAnchorItemId: pendingOrientationAnchorRef.current?.itemId || null,
      });
      return pendingOrientationAnchorRef.current;
    }

    function captureFallbackOrientationAnchors({ allowSeriesQueryFallback = false } = {}) {
      if (isVisualViewportZoomed({ viewportWindow: window })) {
        return orientationAnchorsRef.current;
      }
      const nextAnchors = captureViewportAnchorCandidates({
        doc: document,
        viewportWindow: window,
        allowSeriesQueryFallback,
        orientation: readOrientation(),
      });
      if (nextAnchors.length > 0) {
        orientationAnchorsRef.current = nextAnchors;
      }
      return nextAnchors;
    }

    function completeOrientationRestore() {
      clearPendingOrientationRestore();
      orientationRestoreLockRef.current = false;
      orientationViewportChangeActiveRef.current = false;
      pendingOrientationAnchorRef.current = null;
      orientationAnchorsRef.current = [];
      orientationRestoreCorrectionCountRef.current = 0;
      scheduleCenterMovieAnchorSample({ reason: "restore_complete" });
    }

    function cancelOrientationRestore(reason, details = {}) {
      const latestCenterMovieItemId = latestCenterMovieAnchorRef.current?.itemId || null;
      const frozenOrientationAnchorItemId = pendingOrientationAnchorRef.current?.itemId || null;
      clearPendingOrientationRestore(true);
      orientationRestoreTokenRef.current += 1;
      orientationRestoreLockRef.current = false;
      orientationViewportChangeActiveRef.current = false;
      pendingOrientationAnchorRef.current = null;
      orientationAnchorsRef.current = [];
      orientationRestoreCorrectionCountRef.current = 0;
      if (reason) {
        logOrientationAnchorDebug("cancelled", {
          reason,
          canceledByUserInteraction: reason === "user_interaction",
          latestCenterMovieItemId,
          frozenOrientationAnchorItemId,
          ...details,
        });
      }
    }

    function scheduleOrientationRestoreVerification(scheduledToken, scheduledUserIntentVersion) {
      orientationRestoreFrameOneRef.current = window.requestAnimationFrame(() => {
        orientationRestoreFrameOneRef.current = 0;
        orientationRestoreFrameTwoRef.current = window.requestAnimationFrame(() => {
          orientationRestoreFrameTwoRef.current = 0;
          orientationRestoreRefineTimerRef.current = window.setTimeout(() => {
            orientationRestoreRefineTimerRef.current = 0;
            verifyOrientationRestore(scheduledToken, scheduledUserIntentVersion);
          }, getOrientationRestoreRefinementDelayMs());
        });
      });
    }

    function resolveOrientationRestoreTarget() {
      const frozenAnchor = pendingOrientationAnchorRef.current;
      if (!frozenAnchor?.itemId && !orientationAnchorsRef.current.length) {
        captureFallbackOrientationAnchors({ allowSeriesQueryFallback: true });
      }
      return selectPreferredOrientationRestoreTarget({
        frozenAnchor,
        fallbackAnchors: frozenAnchor?.itemId ? [] : orientationAnchorsRef.current,
        doc: document,
      });
    }

    function verifyOrientationRestore(scheduledToken, scheduledUserIntentVersion) {
      if (isRestoreAttemptStale({
        scheduledToken,
        activeToken: orientationRestoreTokenRef.current,
        scheduledUserIntentVersion,
        currentUserIntentVersion: orientationUserIntentVersionRef.current,
      })) {
        completeOrientationRestore();
        return;
      }
      const { anchor, targetNode } = resolveOrientationRestoreTarget();
      if (!targetNode) {
        completeOrientationRestore();
        return;
      }
      const measurement = getRestoreViewportMeasurement({ viewportWindow: window });
      const correctionTop = computeRestoreVerificationCorrection({
        anchor,
        currentScrollY: window.scrollY,
        targetRectTop: targetNode.getBoundingClientRect().top,
        targetRectHeight: targetNode.getBoundingClientRect().height,
        viewportMeasurement: measurement,
        correctionCount: orientationRestoreCorrectionCountRef.current,
        maxCorrections: MAX_ORIENTATION_RESTORE_CORRECTIONS,
      });
      if (!Number.isFinite(correctionTop)) {
        completeOrientationRestore();
        return;
      }
      orientationRestoreCorrectionCountRef.current += 1;
      logOrientationAnchorDebug("restore verification correction", {
        orientation: readOrientation(),
        latestCenterMovieItemId: latestCenterMovieAnchorRef.current?.itemId || null,
        frozenOrientationAnchorItemId: pendingOrientationAnchorRef.current?.itemId || null,
        selectedRestoreAnchor: formatViewportAnchorDebug(anchor),
        restoreTargetItemId: anchor?.itemId || null,
        restoreTargetScrollY: correctionTop,
        correctionCount: orientationRestoreCorrectionCountRef.current,
      });
      window.scrollTo({
        top: correctionTop,
        behavior: "auto",
      });
      if (orientationRestoreCorrectionCountRef.current >= MAX_ORIENTATION_RESTORE_CORRECTIONS) {
        completeOrientationRestore();
        return;
      }
      scheduleOrientationRestoreVerification(scheduledToken, scheduledUserIntentVersion);
    }

    function attemptOrientationRestore(scheduledToken, scheduledUserIntentVersion) {
      if (isRestoreAttemptStale({
        scheduledToken,
        activeToken: orientationRestoreTokenRef.current,
        scheduledUserIntentVersion,
        currentUserIntentVersion: orientationUserIntentVersionRef.current,
      })) {
        completeOrientationRestore();
        return;
      }
      const { anchor, targetNode, source } = resolveOrientationRestoreTarget();
      if (!targetNode) {
        logOrientationAnchorDebug("restore skipped missing target", {
          source,
          latestCenterMovieItemId: latestCenterMovieAnchorRef.current?.itemId || null,
          frozenOrientationAnchorItemId: pendingOrientationAnchorRef.current?.itemId || null,
          candidates: formatViewportAnchorCandidateListDebug(orientationAnchorsRef.current),
        });
        completeOrientationRestore();
        return;
      }
      const measurement = getRestoreViewportMeasurement({ viewportWindow: window });
      const nextTop = computeAnchorRestoreScrollTop({
        anchor,
        currentScrollY: window.scrollY,
        targetRectTop: targetNode.getBoundingClientRect().top,
        viewportMeasurement: measurement,
      });
      if (!Number.isFinite(nextTop)) {
        completeOrientationRestore();
        return;
      }
      logOrientationAnchorDebug("restore attempt", {
        source,
        orientation: readOrientation(measurement),
        latestCenterMovieItemId: latestCenterMovieAnchorRef.current?.itemId || null,
        frozenOrientationAnchorItemId: pendingOrientationAnchorRef.current?.itemId || null,
        selectedRestoreAnchor: formatViewportAnchorDebug(anchor),
        restoreTargetItemId: anchor?.itemId || null,
        restoreTargetScrollY: nextTop,
        correctionCount: orientationRestoreCorrectionCountRef.current,
      });
      window.scrollTo({
        top: nextTop,
        behavior: "auto",
      });
      scheduleOrientationRestoreVerification(scheduledToken, scheduledUserIntentVersion);
    }

    function scheduleOrientationRestore({ zoomedRotationRecovery = false } = {}) {
      clearPendingOrientationRestore(true);
      orientationRestoreLockRef.current = true;
      orientationRestoreTokenRef.current += 1;
      orientationRestoreCorrectionCountRef.current = 0;
      const scheduledToken = orientationRestoreTokenRef.current;
      const scheduledUserIntentVersion = orientationUserIntentVersionRef.current;
      if (zoomedRotationRecovery) {
        const resetRequested = requestTemporaryViewportScaleReset({
          doc: document,
          viewportWindow: window,
        });
        logOrientationAnchorDebug("zoomed rotation recovery requested", {
          resetRequested,
          frozenOrientationAnchorItemId: pendingOrientationAnchorRef.current?.itemId || null,
        });
      }
      orientationRestoreTimerRef.current = window.setTimeout(() => {
        orientationRestoreTimerRef.current = 0;
        orientationRestoreFrameOneRef.current = window.requestAnimationFrame(() => {
          orientationRestoreFrameOneRef.current = 0;
          orientationRestoreFrameTwoRef.current = window.requestAnimationFrame(() => {
            orientationRestoreFrameTwoRef.current = 0;
            attemptOrientationRestore(scheduledToken, scheduledUserIntentVersion);
          });
        });
      }, zoomedRotationRecovery ? 260 : 70);
    }

    function handleViewportShift(event) {
      const measurement = readMeasurement();
      const nextOrientation = readOrientation(measurement);
      const orientationChanged = orientationRef.current !== null && nextOrientation !== orientationRef.current;
      const majorViewportChange = isMajorViewportChange(measurement);
      const zoomedRotationRecovery = shouldRecoverZoomedLibraryRotation({
        platform: orientationPlatform,
        viewportWindow: window,
        orientationChanged,
        majorViewportChange,
        eventType: event?.type || "",
      });
      if (orientationRef.current === null) {
        orientationRef.current = nextOrientation;
        orientationLastMeasurementRef.current = measurement;
        scheduleCenterMovieAnchorSample({ reason: "initial_measurement", immediate: true });
        return;
      }
      orientationLastMeasurementRef.current = measurement;
      if (!orientationChanged && !majorViewportChange && event?.type !== "orientationchange") {
        scheduleCenterMovieAnchorSample({ reason: "stable_resize" });
        return;
      }
      if (!orientationViewportChangeActiveRef.current) {
        freezePendingOrientationAnchor(event?.type || "viewport_change");
        orientationAnchorsRef.current = [];
      }
      orientationViewportChangeActiveRef.current = true;
      orientationRef.current = nextOrientation;
      scheduleOrientationRestore({ zoomedRotationRecovery });
    }

    function handleUserOrientationInteraction(event) {
      if (!isUserRestoreCancellationEvent({ type: event.type, key: event.key })) {
        return;
      }
      if (
        !orientationRestoreLockRef.current
        && !orientationRestoreTimerRef.current
        && !orientationRestoreFrameOneRef.current
        && !orientationRestoreFrameTwoRef.current
        && !orientationRestoreRefineTimerRef.current
      ) {
        return;
      }
      orientationUserIntentVersionRef.current += 1;
      cancelOrientationRestore("user_interaction", {
        eventType: event.type,
        key: event.key || null,
      });
    }

    orientationSamplerRef.current = scheduleCenterMovieAnchorSample;
    const initialMeasurement = readMeasurement();
    orientationRef.current = readOrientation(initialMeasurement);
    orientationLastMeasurementRef.current = initialMeasurement;
    scheduleCenterMovieAnchorSample({ reason: "mount", immediate: true });
    window.addEventListener("scroll", scheduleCenterMovieAnchorSample, { passive: true });
    window.addEventListener("resize", handleViewportShift);
    window.addEventListener("orientationchange", handleViewportShift);
    window.addEventListener("touchstart", handleUserOrientationInteraction, { passive: true });
    window.addEventListener("touchmove", handleUserOrientationInteraction, { passive: true });
    window.addEventListener("wheel", handleUserOrientationInteraction, { passive: true });
    window.addEventListener("pointerdown", handleUserOrientationInteraction, { passive: true });
    window.addEventListener("keydown", handleUserOrientationInteraction);
    visualViewport?.addEventListener("resize", handleViewportShift);
    return () => {
      orientationSamplerRef.current = () => {};
      window.removeEventListener("scroll", scheduleCenterMovieAnchorSample);
      window.removeEventListener("resize", handleViewportShift);
      window.removeEventListener("orientationchange", handleViewportShift);
      window.removeEventListener("touchstart", handleUserOrientationInteraction);
      window.removeEventListener("touchmove", handleUserOrientationInteraction);
      window.removeEventListener("wheel", handleUserOrientationInteraction);
      window.removeEventListener("pointerdown", handleUserOrientationInteraction);
      window.removeEventListener("keydown", handleUserOrientationInteraction);
      visualViewport?.removeEventListener("resize", handleViewportShift);
      clearPendingOrientationRestore(true);
      orientationRestoreLockRef.current = false;
      orientationViewportChangeActiveRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (loading || typeof window === "undefined" || typeof document === "undefined") {
      return;
    }
    if (!isLibraryOrientationRestorePlatform(detectClientPlatform())) {
      return;
    }
    orientationSamplerRef.current?.({
      reason: "library_content_loaded",
      immediate: false,
    });
  }, [
    loading,
    library.total_items,
    visibleContinueWatchingItems.length,
    visibleLibraryGridItems.length,
    packedSeriesRailRows.length,
  ]);

  useEffect(() => {
    if (loading || typeof window === "undefined" || typeof document === "undefined") {
      return undefined;
    }
    const rememberedTarget = readLibraryReturnTarget();
    const shouldRestore = Boolean(location.state?.restoreLibraryReturn) || Boolean(rememberedTarget?.pendingRestore);
    if (!shouldRestore || !rememberedTarget || rememberedTarget.listPath !== location.pathname) {
      return undefined;
    }
    const restoreKey = [
      location.pathname,
      rememberedTarget.anchorInstanceKey || rememberedTarget.anchorItemId || "none",
      rememberedTarget.anchorViewportRatioY ?? "none",
      rememberedTarget.scrollY,
    ].join(":");
    if (libraryReturnRestoreKeyRef.current === restoreKey) {
      return undefined;
    }
    libraryReturnRestoreKeyRef.current = restoreKey;
    const timerId = window.setTimeout(() => {
      window.requestAnimationFrame(() => {
        const { anchor, targetNode } = selectLibraryReturnRestoreTarget(rememberedTarget, {
          doc: document,
        });
        if (targetNode) {
          restoreHorizontalRailPosition({
            targetNode,
            railKey: rememberedTarget.railKey,
            railScrollLeft: rememberedTarget.railScrollLeft,
          });
          const nextTop = computeAnchorRestoreScrollTop({
            anchor,
            currentScrollY: window.scrollY,
            targetRectTop: targetNode.getBoundingClientRect().top,
            viewportMeasurement: getRestoreViewportMeasurement({ viewportWindow: window }),
          });
          window.scrollTo({
            top: Number.isFinite(nextTop) ? nextTop : rememberedTarget.scrollY,
            behavior: "auto",
          });
        } else if (rememberedTarget.scrollY > 0) {
          window.scrollTo({ top: rememberedTarget.scrollY, behavior: "auto" });
        } else {
          window.scrollTo({ top: 0, behavior: "auto" });
        }
        clearLibraryReturnPending();
      });
    }, 0);
    return () => {
      window.clearTimeout(timerId);
    };
  }, [library.items, loading, location.pathname, location.state]);

  async function handleRescan() {
    setRescanPending(true);
    setError("");
    setNotice("");
    try {
      const payload = await apiRequest("/api/library/rescan", { method: "POST" });
      const nextCloudSyncWarning = hasCloudSyncWarning(payload.cloud_sync)
        ? String(payload.cloud_sync?.message || "").trim()
        : "";
      cloudSyncWarningRef.current = nextCloudSyncWarning;
      if (payload?.cloud_sync?.reconnect_required && !readLibraryCloudReconnectDismissed()) {
        setShowCloudReconnectModal(true);
      }
      if (nextCloudSyncWarning) {
        setError(formatRescanBannerText(payload));
        setNotice("");
      } else {
        setNotice(formatRescanBannerText(payload));
      }
      setLibrary((current) => ({ ...current, scan_in_progress: payload.running }));
      scanRunningRef.current = Boolean(payload.running);
      await loadLibrary({ silent: true });
    } catch (requestError) {
      setError(requestError.message || "Unable to start scan");
    } finally {
      setRescanPending(false);
    }
  }

  const isSearching = deferredQuery.trim().length > 0;
  const libraryDevice = detectClientPlatform() === "ipad" ? "ipad" : undefined;

  return (
    <section className="page-section page-section--library" data-library-device={libraryDevice}>
      <ProviderReconnectModal
        allowReconnect
        message={cloudReconnectPrompt?.message || ""}
        onClose={handleDismissCloudReconnectPrompt}
        onReconnect={handleCloudReconnect}
        onSecondary={handleDismissCloudReconnectPrompt}
        open={showCloudReconnectModal && Boolean(cloudReconnectPrompt)}
        reconnectLabel="Reconnect Google Drive"
        reconnectPending={providerReconnectPending}
        secondaryLabel="Later"
        title={cloudReconnectPrompt?.title || "Reconnect Google Drive"}
      />

      <div className="topbar library-desktop-hero" aria-label="Library overview">
        <p className="eyebrow library-desktop-hero__eyebrow">Private Media Library</p>
        <div className="library-desktop-hero__row">
          <div className="library-desktop-hero__brand">
            <Link className="brand" to="/library">
              Elvern
            </Link>
            <span className="status-pill">{library.total_items} indexed</span>
          </div>
          <label className="search-field library-desktop-hero__search library-desktop-hero__search--desktop">
            <span className="sr-only">Search library</span>
            <input
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search title or filename"
              type="search"
              value={query}
            />
          </label>
          <button
            className="ghost-button"
            disabled={rescanPending}
            onClick={handleRescan}
            type="button"
          >
            {rescanPending ? "Starting scan..." : "Rescan library"}
          </button>
        </div>
      </div>

      <div className="library-mobile-search-card">
        <label className="search-field library-desktop-hero__search library-desktop-hero__search--mobile">
          <span className="sr-only">Search library</span>
          <input
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search title or filename"
            type="search"
            value={query}
          />
        </label>
      </div>

      <div className="library-focus-entry">
        <Link className="library-focus-entry__link" to="/library/local">
          <span className="library-focus-entry__label">Local</span>
          <span className="library-focus-entry__meta">{formatMovieCount(sourceCounts.local)}</span>
        </Link>
        <Link className="library-focus-entry__link" to="/library/cloud">
          <span className="library-focus-entry__label">Cloud</span>
          <span className="library-focus-entry__meta">{formatMovieCount(sourceCounts.cloud)}</span>
        </Link>
      </div>

      {cloudReconnectPrompt ? (
        <section className="content-section">
          <div className="section-header section-header--compact">
            <h2>Google Drive reconnect required</h2>
          </div>
          <p className="form-error">{cloudReconnectPrompt.message}</p>
          <div className="player-actions">
            <button
              className="primary-button"
              disabled={providerReconnectPending}
              onClick={handleCloudReconnect}
              type="button"
            >
              {providerReconnectPending ? "Connecting..." : "Reconnect Google Drive"}
            </button>
          </div>
        </section>
      ) : null}

      {notice ? <p className="page-note">{notice}</p> : null}
      {error ? <p className="form-error">{error}</p> : null}

      {loading ? <LoadingView label="Loading library..." /> : null}

      {!loading && isSearching ? (
        library.items.length > 0 ? (
          <div className="content-stack">
            <div className="section-header section-header--compact">
              <h2>Search results</h2>
            </div>
            <MediaGrid
              activeBrowserPlaybackItemId={activeBrowserPlaybackItemId}
              items={library.items}
              sectionKey="search-results"
              smartPosterLoadingEnabled
            />
          </div>
        ) : (
          <EmptyState
            title="No matches yet"
            description="Try a different title fragment, filename, or clear the search field."
          />
        )
      ) : null}

      {!loading && !isSearching ? (
        <div className="content-stack">
          {showContinueWatchingSection ? (
            <section className="content-section">
              <div className="section-header section-header--compact">
                <h2>Continue watching</h2>
              </div>
              <MediaGrid
                activeBrowserPlaybackItemId={activeBrowserPlaybackItemId}
                items={visibleContinueWatchingItems}
                sectionKey="continue-watching"
                smartPosterLoadingEnabled
              />
            </section>
          ) : null}

          {packedSeriesRailRows.map((row) => (
            <div
              className={[
                "series-rail-pack-row",
                row.layout ? `series-rail-pack-row--${row.layout}` : "",
              ].filter(Boolean).join(" ")}
              key={row.key}
            >
              {row.blocks.map((block) => (
                <div
                  className="series-rail-pack-block"
                  key={block.key}
                  style={{ "--series-rail-pack-span": String(block.slots) }}
                >
                  <SeriesRail
                    activeBrowserPlaybackItemId={activeBrowserPlaybackItemId}
                    desktopSlots={block.slots < 6 ? block.slots : null}
                    enableTouchReleaseAssist
                    rail={block.rail}
                    sectionKey={`series:${block.rail.key}`}
                    smartPosterLoadingEnabled
                  />
                </div>
              ))}
            </div>
          ))}

          {!settings.hide_recently_added && library.recently_added.length > 0 ? (
            <section className="content-section">
              <div className="section-header section-header--compact">
                <h2>Recently added</h2>
              </div>
              <MediaGrid
                activeBrowserPlaybackItemId={activeBrowserPlaybackItemId}
                items={library.recently_added}
                sectionKey="recently-added"
                smartPosterLoadingEnabled
              />
            </section>
          ) : null}

          <section className="content-section">
            <div className="section-header section-header--compact">
              <h2>Other Movies</h2>
            </div>
            {visibleLibraryGridItems.length > 0 ? (
            <MediaGrid
              activeBrowserPlaybackItemId={activeBrowserPlaybackItemId}
              items={visibleLibraryGridItems}
              sectionKey="other-movies"
              smartPosterLoadingEnabled
            />
            ) : (
              <EmptyState
                title="No media indexed yet"
                description="Point ELVERN_MEDIA_ROOT at your movies folder, then run a rescan."
              />
            )}
          </section>
        </div>
      ) : null}
    </section>
  );
}
