import { detectClientPlatform, isIOSClientPlatform } from "./platformDetection.js";
import {
  getIOSViewportWidthBucket,
  readMatchingIOSViewportGeometry,
  writeIOSViewportGeometry,
} from "./iosViewportGeometry.js";


export const IOS_VIEWPORT_COORDINATOR_API_KEY = "__elvernIOSViewportCoordinator";
export const LEGACY_VIEWPORT_SYNC_API_KEY = "__elvernRequestViewportNormalization";
export const IOS_VIEWPORT_DEBUG_STORAGE_KEY = "elvern_ios_viewport_debug";
export const IOS_VIEWPORT_STABLE_EVENT = "elvern:ios-viewport-stable";
export const IOS_VIEWPORT_BASE_CONTENT = "width=device-width, initial-scale=1.0, viewport-fit=cover, shrink-to-fit=no";
export const IOS_VIEWPORT_RESET_CONTENT = `${IOS_VIEWPORT_BASE_CONTENT}, maximum-scale=1.0`;
export const IOS_VIEWPORT_SUSPICIOUS_SHRINK_MIN_PX = 64;
export const IOS_VIEWPORT_SUSPICIOUS_SHRINK_RATIO = 0.08;
export const IOS_POST_KEYBOARD_QUARANTINE_MS = 700;
export const IOS_VIEWPORT_SETTLE_MAX_MS = 1_500;

const DEFAULT_STABLE_SAMPLE_COUNT = 2;
const DEFAULT_STABLE_SAMPLE_DELAY_MS = 32;
const DEFAULT_SETTLE_TIMEOUT_MS = IOS_VIEWPORT_SETTLE_MAX_MS;
const DEFAULT_AUTH_EXIT_TIMEOUT_MS = IOS_VIEWPORT_SETTLE_MAX_MS;
const DEFAULT_RESET_DURATION_MS = 180;
const KEYBOARD_MIN_HEIGHT_PX = 120;
const SCALE_TOLERANCE = 0.02;
const SAMPLE_TOLERANCE_PX = 3;
const FOCUS_AUTOZOOM_WINDOW_MS = 1_500;
const DEBUG_THROTTLE_MS = 250;
const OFFSET_TOLERANCE_PX = 1;


function toFinite(value, fallback = 0) {
  return Number.isFinite(Number(value)) ? Number(value) : fallback;
}


function isEditable(element) {
  return Boolean(element?.matches?.("input, textarea, select, [contenteditable='true']"));
}


function isAuthEditable(element) {
  return isEditable(element) && Boolean(element.closest?.(".login-screen, .totp-setup-card"));
}


function readLayoutViewport(windowObject, documentObject) {
  const root = documentObject?.documentElement;
  return {
    width: Math.round(toFinite(root?.clientWidth, windowObject?.innerWidth || 0)),
    height: Math.round(toFinite(root?.clientHeight, windowObject?.innerHeight || 0)),
  };
}


function readLiveViewport(windowObject) {
  const viewport = windowObject?.visualViewport;
  return {
    width: Math.round(toFinite(viewport?.width, windowObject?.innerWidth || 0)),
    height: Math.round(toFinite(viewport?.height, windowObject?.innerHeight || 0)),
    offsetTop: toFinite(viewport?.offsetTop, 0),
    offsetLeft: toFinite(viewport?.offsetLeft, 0),
    scale: toFinite(viewport?.scale, 1),
  };
}


function orientationFor(viewport, windowObject = globalThis.window) {
  const screenOrientation = String(windowObject?.screen?.orientation?.type || "").toLowerCase();
  if (screenOrientation.startsWith("landscape")) {
    return "landscape";
  }
  if (screenOrientation.startsWith("portrait")) {
    return "portrait";
  }
  const legacyOrientation = Number(windowObject?.orientation);
  if (legacyOrientation === 90 || legacyOrientation === -90) {
    return "landscape";
  }
  if (legacyOrientation === 0 || legacyOrientation === 180 || legacyOrientation === -180) {
    return "portrait";
  }
  return viewport.width > viewport.height ? "landscape" : "portrait";
}


function isStandaloneDisplay(windowObject, navigatorObject) {
  return navigatorObject?.standalone === true
    || windowObject?.matchMedia?.("(display-mode: standalone)")?.matches === true;
}


function readScreenGeometry(windowObject) {
  return {
    width: Math.round(toFinite(windowObject?.screen?.width, 0)),
    height: Math.round(toFinite(windowObject?.screen?.height, 0)),
  };
}


function measureLargeViewportHeight(documentObject) {
  if (!documentObject?.body?.appendChild || !documentObject?.createElement) {
    return 0;
  }
  const probe = documentObject.createElement("div");
  probe.setAttribute("aria-hidden", "true");
  probe.style.cssText = "position:fixed;inset:0 auto auto -10000px;width:1px;height:100lvh;pointer-events:none;visibility:hidden";
  documentObject.body.appendChild(probe);
  const height = Math.round(toFinite(probe.getBoundingClientRect?.().height, 0));
  probe.remove();
  return height;
}


function screenDerivedPaintFloor({ layout, orientation, screen, standalone }) {
  if (!standalone || !screen.width || !screen.height) {
    return 0;
  }
  const expectedWidth = orientation === "portrait"
    ? Math.min(screen.width, screen.height)
    : Math.max(screen.width, screen.height);
  if (Math.abs(layout.width - expectedWidth) >= 64) {
    return 0;
  }
  return orientation === "portrait"
    ? Math.max(screen.width, screen.height)
    : Math.min(screen.width, screen.height);
}


function nextAnimationFrame(windowObject) {
  return new Promise((resolve) => {
    const request = windowObject?.requestAnimationFrame
      || ((callback) => windowObject?.setTimeout?.(callback, 16));
    request?.(() => resolve());
  });
}


export function createIOSViewportCoordinator({
  windowObject = globalThis.window,
  documentObject = globalThis.document,
  navigatorObject = globalThis.navigator,
  platform = detectClientPlatform({
    userAgent: navigatorObject?.userAgent,
    platform: navigatorObject?.platform,
    maxTouchPoints: navigatorObject?.maxTouchPoints,
  }),
  stableSampleCount = DEFAULT_STABLE_SAMPLE_COUNT,
  stableSampleDelayMs = DEFAULT_STABLE_SAMPLE_DELAY_MS,
  settleTimeoutMs = DEFAULT_SETTLE_TIMEOUT_MS,
  authExitTimeoutMs = DEFAULT_AUTH_EXIT_TIMEOUT_MS,
  resetDurationMs = DEFAULT_RESET_DURATION_MS,
  postKeyboardQuarantineMs = IOS_POST_KEYBOARD_QUARANTINE_MS,
} = {}) {
  const isIOS = isIOSClientPlatform(platform);
  const root = documentObject?.documentElement || null;
  const viewportMeta = documentObject?.querySelector?.('meta[name="viewport"]') || null;
  const originalViewportContent = viewportMeta?.getAttribute("content") || IOS_VIEWPORT_BASE_CONTENT;
  const listeners = new Set();
  const trustedViewportByOrientation = new Map();
  const standaloneDisplay = isStandaloneDisplay(windowObject, navigatorObject);
  const displayMode = standaloneDisplay ? "standalone" : "browser";
  const initialLayout = readLayoutViewport(windowObject, documentObject);
  const initialLive = readLiveViewport(windowObject);
  const initialOrientation = orientationFor(initialLayout, windowObject);
  const screenGeometry = readScreenGeometry(windowObject);
  const largeViewportHeight = measureLargeViewportHeight(documentObject);
  const persistedGeometry = isIOS
    ? readMatchingIOSViewportGeometry({
      storage: windowObject?.localStorage,
      now: Date.now(),
      platform,
      displayMode,
      orientation: initialOrientation,
      layoutWidth: initialLayout.width,
      screenWidth: screenGeometry.width,
      screenHeight: screenGeometry.height,
    })
    : null;
  const initialScreenFloor = screenDerivedPaintFloor({
    layout: initialLayout,
    orientation: initialOrientation,
    screen: screenGeometry,
    standalone: standaloneDisplay,
  });
  let physicalPaintFloorHeight = Math.max(
    initialLayout.height,
    largeViewportHeight,
    initialScreenFloor,
    Number(persistedGeometry?.physical_paint_floor_height) || 0,
  );
  const initialReferenceHeight = Math.max(
    Number(persistedGeometry?.trusted_layout_height) || 0,
    initialScreenFloor,
    largeViewportHeight,
  );
  const initialShrinkPixels = initialReferenceHeight - initialLayout.height;
  const initialShrinkRatio = initialReferenceHeight > 0 ? initialShrinkPixels / initialReferenceHeight : 0;
  let initialSuspiciousShrink = Boolean(
    isIOS
    && initialShrinkPixels > 0
    && (
      initialShrinkPixels >= IOS_VIEWPORT_SUSPICIOUS_SHRINK_MIN_PX
      || initialShrinkRatio >= IOS_VIEWPORT_SUSPICIOUS_SHRINK_RATIO
    )
  );
  const restoredViewport = persistedGeometry
    ? {
      width: persistedGeometry.trusted_layout_width,
      height: persistedGeometry.trusted_layout_height,
    }
    : null;
  let started = false;
  let pendingFrame = 0;
  let stableSampleTimer = 0;
  let settleFallbackTimer = 0;
  let orientationTimer = 0;
  let resetRestoreTimer = 0;
  let resetGeneration = 0;
  let resetActive = false;
  let stableCandidate = null;
  let stableCandidateCount = 0;
  let focusStartedAt = 0;
  let focusBaseline = null;
  let authFocusActive = false;
  let focusAutozoomObserved = false;
  let authResetPerformed = false;
  let authContaminationActive = false;
  let authInteractionGeneration = 0;
  let authSettleGeneration = -1;
  let authSettlePromise = null;
  let preFocusTrustedViewport = null;
  let postKeyboardQuarantineUntil = 0;
  let suspiciousShrink = false;
  let orientationChanging = false;
  let settling = isIOS;
  let lastDebugAt = 0;
  let snapshot = {
    isIOS,
    platform,
    state: isIOS
      ? (initialSuspiciousShrink
        ? "initial_suspicious_shrink"
        : (restoredViewport ? "geometry_restored" : "initial_provisional"))
      : "stable",
    stableViewport: restoredViewport || initialLayout,
    liveViewport: initialLive,
    keyboardOpen: false,
    editableFocused: false,
    focusAutozoom: false,
    postKeyboardQuarantine: false,
    suspiciousShrink: false,
    initialSuspiciousShrink,
    physicalPaintFloorHeight,
    geometryRestored: Boolean(restoredViewport),
    restoreGateOpen: !isIOS,
    resetGeneration: 0,
  };

  function emit(next) {
    const previousGate = snapshot.restoreGateOpen;
    snapshot = { ...snapshot, ...next, resetGeneration };
    listeners.forEach((listener) => listener(snapshot));
    if (!previousGate && snapshot.restoreGateOpen) {
      windowObject?.dispatchEvent?.(new CustomEvent(IOS_VIEWPORT_STABLE_EVENT));
    }
  }

  function setBooleanDataset(key, value) {
    if (!root) {
      return;
    }
    if (value) {
      root.dataset[key] = "true";
    } else {
      delete root.dataset[key];
    }
  }

  function applyRootMetrics(nextSnapshot) {
    if (!root) {
      return;
    }
    const stable = nextSnapshot.stableViewport;
    const live = nextSnapshot.liveViewport;
    const layout = readLayoutViewport(windowObject, documentObject);
    const offsetRight = Math.max(0, layout.width - live.width - live.offsetLeft);
    if (stable.height > 0) {
      root.style.setProperty("--app-stable-viewport-height", `${stable.height}px`);
      root.style.setProperty("--app-viewport-height", `${stable.height}px`);
      root.style.setProperty("--app-paint-viewport-height", `${stable.height}px`);
      root.style.setProperty("--app-viewport-bleed", `${Math.max(240, Math.round(stable.height * 0.38))}px`);
    }
    if (nextSnapshot.physicalPaintFloorHeight > 0) {
      root.style.setProperty(
        "--app-physical-paint-floor-height",
        `${nextSnapshot.physicalPaintFloorHeight}px`,
      );
    }
    if (live.height > 0) {
      root.style.setProperty("--app-live-viewport-height", `${live.height}px`);
    }
    root.style.setProperty("--app-visual-viewport-offset-top", `${live.offsetTop}px`);
    root.style.setProperty("--app-visual-viewport-offset-left", `${live.offsetLeft}px`);
    root.style.setProperty("--app-visual-viewport-scale", String(live.scale));
    root.style.setProperty("--app-viewport-offset-left", `${live.offsetLeft}px`);
    root.style.setProperty("--app-viewport-offset-right", `${offsetRight}px`);
    root.dataset.viewportOrientation = orientationFor(layout, windowObject);
    if (platform === "iphone") {
      root.dataset.deviceShell = "iphone";
    }
    setBooleanDataset("elvernIosViewport", isIOS);
    setBooleanDataset("elvernKeyboardOpen", nextSnapshot.keyboardOpen);
    setBooleanDataset("elvernEditableFocused", nextSnapshot.editableFocused);
    setBooleanDataset("elvernPostKeyboardQuarantine", nextSnapshot.postKeyboardQuarantine);
    setBooleanDataset("elvernSuspiciousShrink", nextSnapshot.suspiciousShrink);
    setBooleanDataset("elvernViewportSettling", !nextSnapshot.restoreGateOpen);
  }

  function debug(eventType) {
    if (!isIOS || !windowObject?.localStorage || Date.now() - lastDebugAt < DEBUG_THROTTLE_MS) {
      return;
    }
    try {
      if (windowObject.localStorage.getItem(IOS_VIEWPORT_DEBUG_STORAGE_KEY) !== "1") {
        return;
      }
    } catch {
      return;
    }
    lastDebugAt = Date.now();
    const layout = readLayoutViewport(windowObject, documentObject);
    console.debug("Elvern iOS viewport", {
      eventType,
      orientation: orientationFor(layout, windowObject),
      innerWidth: windowObject.innerWidth,
      innerHeight: windowObject.innerHeight,
      clientWidth: layout.width,
      clientHeight: layout.height,
      visualViewport: { ...snapshot.liveViewport },
      stableViewport: { ...snapshot.stableViewport },
      keyboardOpen: snapshot.keyboardOpen,
      editableFocused: snapshot.editableFocused,
      postKeyboardQuarantine: snapshot.postKeyboardQuarantine,
      suspiciousShrink: snapshot.suspiciousShrink,
      initialSuspiciousShrink: snapshot.initialSuspiciousShrink,
      physicalPaintFloorHeight: snapshot.physicalPaintFloorHeight,
      state: snapshot.state,
      resetGeneration,
      restoreGateOpen: snapshot.restoreGateOpen,
    });
  }

  function resolveState({
    keyboardOpen,
    editableFocused,
    focusAutozoom,
    postKeyboardQuarantine,
    hasSuspiciousShrink,
  }) {
    if (orientationChanging) return "orientation_changing";
    if (initialSuspiciousShrink) return "initial_suspicious_shrink";
    if (focusAutozoom) return "focus_autozoom";
    if (keyboardOpen) return "keyboard_open";
    if (editableFocused) return "editable_focused";
    if (postKeyboardQuarantine) return "post_keyboard_quarantine";
    if (hasSuspiciousShrink) return "suspicious_shrink";
    if (settling) return "settling";
    return "stable";
  }

  function clearSettleFallback() {
    if (settleFallbackTimer) {
      windowObject?.clearTimeout?.(settleFallbackTimer);
      settleFallbackTimer = 0;
    }
  }

  function markStable(viewport, eventType) {
    const orientation = orientationFor(viewport, windowObject);
    initialSuspiciousShrink = false;
    physicalPaintFloorHeight = Math.max(physicalPaintFloorHeight, viewport.height);
    trustedViewportByOrientation.set(orientation, viewport);
    settling = false;
    orientationChanging = false;
    suspiciousShrink = false;
    authContaminationActive = false;
    postKeyboardQuarantineUntil = 0;
    preFocusTrustedViewport = null;
    stableCandidate = null;
    stableCandidateCount = 0;
    if (stableSampleTimer) {
      windowObject?.clearTimeout?.(stableSampleTimer);
      stableSampleTimer = 0;
    }
    clearSettleFallback();
    const next = {
      ...snapshot,
      stableViewport: viewport,
      state: "stable",
      keyboardOpen: false,
      editableFocused: false,
      focusAutozoom: false,
      postKeyboardQuarantine: false,
      suspiciousShrink: false,
      initialSuspiciousShrink: false,
      physicalPaintFloorHeight,
      geometryRestored: false,
      restoreGateOpen: true,
    };
    if (isIOS) {
      writeIOSViewportGeometry({
        storage: windowObject?.localStorage,
        record: {
          schema_version: 1,
          platform,
          display_mode: displayMode,
          orientation,
          width_bucket: getIOSViewportWidthBucket(viewport.width),
          screen_width: screenGeometry.width,
          screen_height: screenGeometry.height,
          trusted_layout_width: viewport.width,
          trusted_layout_height: viewport.height,
          physical_paint_floor_height: physicalPaintFloorHeight,
          updated_at: Date.now(),
        },
      });
    }
    applyRootMetrics(next);
    emit(next);
    debug(eventType);
  }

  function beginSettling(eventType = "settling") {
    if (!isIOS) {
      return;
    }
    settling = true;
    stableCandidate = null;
    stableCandidateCount = 0;
    clearSettleFallback();
    settleFallbackTimer = windowObject?.setTimeout?.(() => {
      settleFallbackTimer = 0;
      if (isEditable(documentObject?.activeElement)) {
        beginSettling(`${eventType}_fallback_deferred`);
        return;
      }
      const layout = readLayoutViewport(windowObject, documentObject);
      const orientation = orientationFor(layout, windowObject);
      const trusted = trustedViewportByOrientation.get(orientation)
        || (preFocusTrustedViewport?.orientation === orientation ? preFocusTrustedViewport : null)
        || restoredViewport
        || snapshot.stableViewport;
      const fallback = initialSuspiciousShrink
        ? (restoredViewport || { width: layout.width, height: physicalPaintFloorHeight })
        : (authContaminationActive || suspiciousShrink
        ? { width: trusted.width, height: trusted.height }
        : (layout.height > 0 ? layout : { width: trusted.width, height: trusted.height }));
      markStable(fallback, `${eventType}_fallback`);
    }, settleTimeoutMs) || 0;
    emit({
      state: orientationChanging
        ? "orientation_changing"
        : (initialSuspiciousShrink ? "initial_suspicious_shrink" : "settling"),
      restoreGateOpen: false,
    });
    requestSample(eventType);
  }

  function sample(eventType = "sample") {
    pendingFrame = 0;
    const layout = readLayoutViewport(windowObject, documentObject);
    const live = readLiveViewport(windowObject);
    const activeElement = documentObject?.activeElement;
    const editableFocused = isEditable(activeElement);
    const keyboardGap = Math.max(0, layout.height - live.height - Math.max(0, live.offsetTop));
    const keyboardOpen = isIOS
      && editableFocused
      && keyboardGap >= Math.max(KEYBOARD_MIN_HEIGHT_PX, Math.round(layout.height * 0.15));
    const focusAutozoom = isIOS
      && editableFocused
      && authFocusActive
      && Date.now() - focusStartedAt <= FOCUS_AUTOZOOM_WINDOW_MS
      && toFinite(focusBaseline?.scale, 1) <= 1 + SCALE_TOLERANCE
      && live.scale > 1.05
      && live.height <= toFinite(focusBaseline?.height, live.height) - 60;
    if (focusAutozoom) {
      focusAutozoomObserved = true;
    }
    const orientation = orientationFor(layout, windowObject);
    const trustedViewport = trustedViewportByOrientation.get(orientation) || snapshot.stableViewport;
    const preFocusBaseline = preFocusTrustedViewport?.orientation === orientation
      ? preFocusTrustedViewport
      : null;
    const candidate = layout.height > 0 ? layout : { width: live.width, height: live.height };
    if (initialSuspiciousShrink) {
      const referenceHeight = Math.max(
        Number(restoredViewport?.height) || 0,
        screenDerivedPaintFloor({
          layout,
          orientation,
          screen: screenGeometry,
          standalone: standaloneDisplay,
        }),
        largeViewportHeight,
      );
      const initialGap = referenceHeight - candidate.height;
      const initialRatio = referenceHeight > 0 ? initialGap / referenceHeight : 0;
      initialSuspiciousShrink = initialGap > 0 && (
        initialGap >= IOS_VIEWPORT_SUSPICIOUS_SHRINK_MIN_PX
        || initialRatio >= IOS_VIEWPORT_SUSPICIOUS_SHRINK_RATIO
      );
    }
    const shrinkPixels = preFocusBaseline ? preFocusBaseline.height - candidate.height : 0;
    const shrinkRatio = preFocusBaseline?.height > 0 ? shrinkPixels / preFocusBaseline.height : 0;
    suspiciousShrink = Boolean(
      authContaminationActive
      && preFocusBaseline
      && shrinkPixels > 0
      && (
        standaloneDisplay
        || shrinkPixels >= IOS_VIEWPORT_SUSPICIOUS_SHRINK_MIN_PX
        || shrinkRatio >= IOS_VIEWPORT_SUSPICIOUS_SHRINK_RATIO
      )
    );
    const viewportNeutral = Math.abs(live.scale - 1) <= SCALE_TOLERANCE
      && Math.abs(live.offsetTop) <= OFFSET_TOLERANCE_PX
      && Math.abs(live.offsetLeft) <= OFFSET_TOLERANCE_PX;
    const postKeyboardQuarantine = Boolean(
      authContaminationActive
      && (
        Date.now() < postKeyboardQuarantineUntil
        || editableFocused
        || !viewportNeutral
      )
    );
    const canTrustStable = isIOS
      && !editableFocused
      && !keyboardOpen
      && viewportNeutral
      && !postKeyboardQuarantine
      && !suspiciousShrink
      && !orientationChanging
      && !resetActive;
    const canTrustInitialCandidate = canTrustStable && !initialSuspiciousShrink;
    if (canTrustInitialCandidate) {
      const measuredCandidate = {
        ...candidate,
        liveHeight: live.height,
        liveWidth: live.width,
      };
      if (
        stableCandidate
        && Math.abs(measuredCandidate.width - stableCandidate.width) <= SAMPLE_TOLERANCE_PX
        && Math.abs(measuredCandidate.height - stableCandidate.height) <= SAMPLE_TOLERANCE_PX
        && Math.abs(measuredCandidate.liveWidth - stableCandidate.liveWidth) <= SAMPLE_TOLERANCE_PX
        && Math.abs(measuredCandidate.liveHeight - stableCandidate.liveHeight) <= SAMPLE_TOLERANCE_PX
      ) {
        stableCandidateCount += 1;
      } else {
        stableCandidate = measuredCandidate;
        stableCandidateCount = 1;
      }
      if (stableCandidateCount >= stableSampleCount) {
        markStable(candidate, eventType);
        return;
      }
      if (!stableSampleTimer) {
        stableSampleTimer = windowObject?.setTimeout?.(() => {
          stableSampleTimer = 0;
          sample("stable_confirmation");
        }, stableSampleDelayMs) || 0;
      }
    } else {
      stableCandidate = null;
      stableCandidateCount = 0;
      if (settling && !stableSampleTimer) {
        stableSampleTimer = windowObject?.setTimeout?.(() => {
          stableSampleTimer = 0;
          sample("settle_observation");
        }, stableSampleDelayMs) || 0;
      }
    }
    const stableViewport = trustedViewport;
    const restoreGateOpen = !isIOS || (
      !settling
      && !orientationChanging
      && !editableFocused
      && !keyboardOpen
      && !focusAutozoom
      && !postKeyboardQuarantine
      && !suspiciousShrink
    );
    const next = {
      ...snapshot,
      stableViewport,
      liveViewport: live,
      keyboardOpen,
      editableFocused,
      focusAutozoom,
      postKeyboardQuarantine,
      suspiciousShrink,
      initialSuspiciousShrink,
      physicalPaintFloorHeight,
      restoreGateOpen,
      state: resolveState({
        keyboardOpen,
        editableFocused,
        focusAutozoom,
        postKeyboardQuarantine,
        hasSuspiciousShrink: suspiciousShrink,
      }),
    };
    applyRootMetrics(next);
    emit(next);
    debug(eventType);
  }

  function requestSample(eventType = "event") {
    if (pendingFrame) {
      return;
    }
    const request = windowObject?.requestAnimationFrame
      || ((callback) => windowObject?.setTimeout?.(callback, 16));
    pendingFrame = request?.(() => sample(eventType)) || 0;
  }

  function handleFocusIn(event) {
    if (!isEditable(event.target)) {
      return;
    }
    authFocusActive = isAuthEditable(event.target);
    focusStartedAt = Date.now();
    focusBaseline = readLiveViewport(windowObject);
    focusAutozoomObserved = false;
    authResetPerformed = false;
    if (authFocusActive) {
      const layout = readLayoutViewport(windowObject, documentObject);
      const orientation = orientationFor(layout, windowObject);
      const trusted = trustedViewportByOrientation.get(orientation) || snapshot.stableViewport;
      preFocusTrustedViewport = {
        orientation,
        width: trusted.width,
        height: trusted.height,
        scale: focusBaseline.scale,
        offsetTop: focusBaseline.offsetTop,
        timestamp: Date.now(),
        standalone: standaloneDisplay,
      };
      authContaminationActive = true;
      postKeyboardQuarantineUntil = 0;
      suspiciousShrink = false;
      authInteractionGeneration += 1;
      authSettleGeneration = -1;
      authSettlePromise = null;
    }
    requestSample("focusin");
  }

  function handleFocusOut() {
    windowObject?.setTimeout?.(() => {
      if (isEditable(documentObject?.activeElement)) {
        requestSample("focus_switch");
        return;
      }
      if (authContaminationActive) {
        postKeyboardQuarantineUntil = Date.now() + Math.max(0, postKeyboardQuarantineMs);
      }
      authFocusActive = false;
      beginSettling("focusout");
    }, 0);
  }

  function handleOrientationChange() {
    if (!isIOS) {
      requestSample("orientationchange");
      return;
    }
    orientationChanging = true;
    beginSettling("orientationchange");
    windowObject?.clearTimeout?.(orientationTimer);
    orientationTimer = windowObject?.setTimeout?.(() => {
      orientationTimer = 0;
      orientationChanging = false;
      requestSample("orientation_settle");
    }, 80) || 0;
  }

  function handlePageResume() {
    if (documentObject?.visibilityState === "hidden") {
      return;
    }
    beginSettling("page_resume");
  }

  function requestNormalization({ force = false, reason = "coordinator" } = {}) {
    if (!isIOS || !viewportMeta || isEditable(documentObject?.activeElement)) {
      return false;
    }
    if (!force && !focusAutozoomObserved) {
      return false;
    }
    if (reason === "auth-exit" && authResetPerformed) {
      return false;
    }
    if (reason === "auth-exit") {
      authResetPerformed = true;
    }
    resetGeneration += 1;
    const generation = resetGeneration;
    windowObject?.clearTimeout?.(resetRestoreTimer);
    resetActive = true;
    beginSettling(`${reason}_reset`);
    viewportMeta.setAttribute("content", IOS_VIEWPORT_RESET_CONTENT);
    emit({ resetGeneration });
    resetRestoreTimer = windowObject?.setTimeout?.(() => {
      if (generation !== resetGeneration) {
        return;
      }
      resetRestoreTimer = 0;
      resetActive = false;
      viewportMeta.setAttribute("content", originalViewportContent || IOS_VIEWPORT_BASE_CONTENT);
      beginSettling(`${reason}_reset_complete`);
    }, resetDurationMs) || 0;
    return true;
  }

  async function waitForStable(timeoutMs) {
    if (snapshot.restoreGateOpen) {
      return true;
    }
    return new Promise((resolve) => {
      let timeoutId = 0;
      const unsubscribe = subscribe((next) => {
        if (!next.restoreGateOpen) {
          return;
        }
        windowObject?.clearTimeout?.(timeoutId);
        unsubscribe();
        resolve(true);
      });
      timeoutId = windowObject?.setTimeout?.(() => {
        unsubscribe();
        resolve(false);
      }, timeoutMs) || 0;
    });
  }

  function settleAuthExit() {
    if (!isIOS) {
      return Promise.resolve(true);
    }
    if (authSettlePromise && authSettleGeneration === authInteractionGeneration) {
      return authSettlePromise;
    }
    authSettleGeneration = authInteractionGeneration;
    authSettlePromise = (async () => {
      const activeElement = documentObject?.activeElement;
      if (isEditable(activeElement)) {
        sample("auth_exit_pre_blur");
        activeElement.blur();
      }
      if (authContaminationActive) {
        postKeyboardQuarantineUntil = Math.max(
          postKeyboardQuarantineUntil,
          Date.now() + Math.max(0, postKeyboardQuarantineMs),
        );
      }
      beginSettling("auth_exit");
      await nextAnimationFrame(windowObject);
      await nextAnimationFrame(windowObject);
      if (focusAutozoomObserved && !authResetPerformed) {
        requestNormalization({ reason: "auth-exit" });
      }
      return waitForStable(authExitTimeoutMs);
    })();
    return authSettlePromise;
  }

  function requestSettledViewportSync({ resetViewport = false } = {}) {
    beginSettling("legacy_sync");
    if (resetViewport) {
      requestNormalization({ reason: "legacy_sync" });
    }
  }

  function start() {
    if (started) {
      return;
    }
    started = true;
    if (windowObject) {
      windowObject[IOS_VIEWPORT_COORDINATOR_API_KEY] = api;
      windowObject[LEGACY_VIEWPORT_SYNC_API_KEY] = requestSettledViewportSync;
    }
    documentObject?.addEventListener?.("focusin", handleFocusIn);
    documentObject?.addEventListener?.("focusout", handleFocusOut);
    documentObject?.addEventListener?.("visibilitychange", handlePageResume);
    windowObject?.addEventListener?.("resize", requestSample, { passive: true });
    windowObject?.addEventListener?.("orientationchange", handleOrientationChange, { passive: true });
    windowObject?.addEventListener?.("pageshow", handlePageResume, { passive: true });
    windowObject?.visualViewport?.addEventListener?.("resize", requestSample, { passive: true });
    windowObject?.visualViewport?.addEventListener?.("scroll", requestSample, { passive: true });
    applyRootMetrics(snapshot);
    if (isIOS) {
      beginSettling("start");
    } else {
      sample("start");
    }
  }

  function stop() {
    if (!started) {
      return;
    }
    started = false;
    windowObject?.cancelAnimationFrame?.(pendingFrame);
    windowObject?.clearTimeout?.(stableSampleTimer);
    windowObject?.clearTimeout?.(settleFallbackTimer);
    windowObject?.clearTimeout?.(orientationTimer);
    windowObject?.clearTimeout?.(resetRestoreTimer);
    pendingFrame = 0;
    stableSampleTimer = 0;
    settleFallbackTimer = 0;
    orientationTimer = 0;
    resetRestoreTimer = 0;
    documentObject?.removeEventListener?.("focusin", handleFocusIn);
    documentObject?.removeEventListener?.("focusout", handleFocusOut);
    documentObject?.removeEventListener?.("visibilitychange", handlePageResume);
    windowObject?.removeEventListener?.("resize", requestSample);
    windowObject?.removeEventListener?.("orientationchange", handleOrientationChange);
    windowObject?.removeEventListener?.("pageshow", handlePageResume);
    windowObject?.visualViewport?.removeEventListener?.("resize", requestSample);
    windowObject?.visualViewport?.removeEventListener?.("scroll", requestSample);
    if (windowObject?.[IOS_VIEWPORT_COORDINATOR_API_KEY] === api) {
      delete windowObject[IOS_VIEWPORT_COORDINATOR_API_KEY];
      delete windowObject[LEGACY_VIEWPORT_SYNC_API_KEY];
    }
    if (viewportMeta) {
      viewportMeta.setAttribute("content", originalViewportContent || IOS_VIEWPORT_BASE_CONTENT);
    }
  }

  function subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  const api = {
    getSnapshot: () => snapshot,
    isRestoreGateOpen: () => snapshot.restoreGateOpen,
    requestNormalization,
    requestSettledViewportSync,
    sample,
    settleAuthExit,
    start,
    stop,
    subscribe,
  };
  return api;
}


export function installIOSViewportCoordinator(options = {}) {
  const windowObject = options.windowObject || globalThis.window;
  const existing = windowObject?.[IOS_VIEWPORT_COORDINATOR_API_KEY];
  if (existing) {
    return existing;
  }
  const coordinator = createIOSViewportCoordinator(options);
  coordinator.start();
  return coordinator;
}


export function getIOSViewportCoordinator(windowObject = globalThis.window) {
  return windowObject?.[IOS_VIEWPORT_COORDINATOR_API_KEY] || null;
}


export function isIOSViewportRestoreGateOpen(windowObject = globalThis.window) {
  return getIOSViewportCoordinator(windowObject)?.isRestoreGateOpen?.() ?? true;
}


export async function settleAuthViewportBeforeNavigation(windowObject = globalThis.window) {
  return getIOSViewportCoordinator(windowObject)?.settleAuthExit?.() ?? true;
}
