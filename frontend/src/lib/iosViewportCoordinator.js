import { detectClientPlatform, isIOSClientPlatform } from "./platformDetection.js";


export const IOS_VIEWPORT_COORDINATOR_API_KEY = "__elvernIOSViewportCoordinator";
export const LEGACY_VIEWPORT_SYNC_API_KEY = "__elvernRequestViewportNormalization";
export const IOS_VIEWPORT_DEBUG_STORAGE_KEY = "elvern_ios_viewport_debug";
export const IOS_VIEWPORT_STABLE_EVENT = "elvern:ios-viewport-stable";
export const IOS_VIEWPORT_BASE_CONTENT = "width=device-width, initial-scale=1.0, viewport-fit=cover, shrink-to-fit=no";
export const IOS_VIEWPORT_RESET_CONTENT = `${IOS_VIEWPORT_BASE_CONTENT}, maximum-scale=1.0`;

const DEFAULT_STABLE_SAMPLE_COUNT = 2;
const DEFAULT_STABLE_SAMPLE_DELAY_MS = 32;
const DEFAULT_SETTLE_TIMEOUT_MS = 1_200;
const DEFAULT_AUTH_EXIT_TIMEOUT_MS = 1_200;
const DEFAULT_RESET_DURATION_MS = 180;
const KEYBOARD_MIN_HEIGHT_PX = 120;
const SCALE_TOLERANCE = 0.02;
const SAMPLE_TOLERANCE_PX = 3;
const FOCUS_AUTOZOOM_WINDOW_MS = 1_500;
const DEBUG_THROTTLE_MS = 250;


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


function orientationFor(viewport) {
  return viewport.width > viewport.height ? "landscape" : "portrait";
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
} = {}) {
  const isIOS = isIOSClientPlatform(platform);
  const root = documentObject?.documentElement || null;
  const viewportMeta = documentObject?.querySelector?.('meta[name="viewport"]') || null;
  const originalViewportContent = viewportMeta?.getAttribute("content") || IOS_VIEWPORT_BASE_CONTENT;
  const listeners = new Set();
  const stableByOrientation = new Map();
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
  let orientationChanging = false;
  let settling = isIOS;
  let lastDebugAt = 0;
  let snapshot = {
    isIOS,
    platform,
    state: isIOS ? "settling" : "stable",
    stableViewport: readLayoutViewport(windowObject, documentObject),
    liveViewport: readLiveViewport(windowObject),
    keyboardOpen: false,
    editableFocused: false,
    focusAutozoom: false,
    restoreGateOpen: !isIOS,
    resetGeneration: 0,
  };

  stableByOrientation.set(orientationFor(snapshot.stableViewport), snapshot.stableViewport);

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
      root.style.setProperty("--app-viewport-bleed", `${Math.max(240, Math.round(stable.height * 0.38))}px`);
    }
    if (live.height > 0) {
      root.style.setProperty("--app-live-viewport-height", `${live.height}px`);
    }
    root.style.setProperty("--app-visual-viewport-offset-top", `${live.offsetTop}px`);
    root.style.setProperty("--app-visual-viewport-offset-left", `${live.offsetLeft}px`);
    root.style.setProperty("--app-visual-viewport-scale", String(live.scale));
    root.style.setProperty("--app-viewport-offset-left", `${live.offsetLeft}px`);
    root.style.setProperty("--app-viewport-offset-right", `${offsetRight}px`);
    root.dataset.viewportOrientation = orientationFor(layout);
    if (platform === "iphone") {
      root.dataset.deviceShell = "iphone";
    }
    setBooleanDataset("elvernIosViewport", isIOS);
    setBooleanDataset("elvernKeyboardOpen", nextSnapshot.keyboardOpen);
    setBooleanDataset("elvernEditableFocused", nextSnapshot.editableFocused);
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
      orientation: orientationFor(layout),
      innerWidth: windowObject.innerWidth,
      innerHeight: windowObject.innerHeight,
      clientWidth: layout.width,
      clientHeight: layout.height,
      visualViewport: { ...snapshot.liveViewport },
      stableViewport: { ...snapshot.stableViewport },
      keyboardOpen: snapshot.keyboardOpen,
      editableFocused: snapshot.editableFocused,
      state: snapshot.state,
      resetGeneration,
      restoreGateOpen: snapshot.restoreGateOpen,
    });
  }

  function resolveState({ keyboardOpen, editableFocused, focusAutozoom }) {
    if (orientationChanging) return "orientation_changing";
    if (focusAutozoom) return "focus_autozoom";
    if (keyboardOpen) return "keyboard_open";
    if (editableFocused) return "editable_focused";
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
    const orientation = orientationFor(viewport);
    stableByOrientation.set(orientation, viewport);
    settling = false;
    orientationChanging = false;
    stableCandidate = null;
    stableCandidateCount = 0;
    clearSettleFallback();
    const next = {
      ...snapshot,
      stableViewport: viewport,
      state: "stable",
      keyboardOpen: false,
      editableFocused: false,
      focusAutozoom: false,
      restoreGateOpen: true,
    };
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
        sample(`${eventType}_fallback_deferred`);
        return;
      }
      const layout = readLayoutViewport(windowObject, documentObject);
      const fallback = layout.height > 0
        ? layout
        : stableByOrientation.get(orientationFor(snapshot.liveViewport)) || snapshot.stableViewport;
      markStable(fallback, `${eventType}_fallback`);
    }, settleTimeoutMs) || 0;
    emit({
      state: orientationChanging ? "orientation_changing" : "settling",
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
    const canTrustStable = isIOS
      && !editableFocused
      && !keyboardOpen
      && Math.abs(live.scale - 1) <= SCALE_TOLERANCE
      && !orientationChanging
      && !resetActive;
    if (canTrustStable) {
      const candidate = layout.height > 0 ? layout : { width: live.width, height: live.height };
      if (
        stableCandidate
        && Math.abs(candidate.width - stableCandidate.width) <= SAMPLE_TOLERANCE_PX
        && Math.abs(candidate.height - stableCandidate.height) <= SAMPLE_TOLERANCE_PX
      ) {
        stableCandidateCount += 1;
      } else {
        stableCandidate = candidate;
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
    }
    const orientation = orientationFor(layout);
    const stableViewport = stableByOrientation.get(orientation) || snapshot.stableViewport;
    const restoreGateOpen = !isIOS || (!settling && !orientationChanging && !editableFocused && !keyboardOpen && !focusAutozoom);
    const next = {
      ...snapshot,
      stableViewport,
      liveViewport: live,
      keyboardOpen,
      editableFocused,
      focusAutozoom,
      restoreGateOpen,
      state: resolveState({ keyboardOpen, editableFocused, focusAutozoom }),
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
    requestSample("focusin");
  }

  function handleFocusOut() {
    windowObject?.setTimeout?.(() => {
      if (isEditable(documentObject?.activeElement)) {
        requestSample("focus_switch");
        return;
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

  async function settleAuthExit() {
    if (!isIOS) {
      return true;
    }
    const activeElement = documentObject?.activeElement;
    if (isEditable(activeElement)) {
      sample("auth_exit_pre_blur");
      activeElement.blur();
    }
    beginSettling("auth_exit");
    await nextAnimationFrame(windowObject);
    await nextAnimationFrame(windowObject);
    if (focusAutozoomObserved && !authResetPerformed) {
      requestNormalization({ reason: "auth-exit" });
    }
    return waitForStable(authExitTimeoutMs);
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
