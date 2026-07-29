import { useLayoutEffect, useRef } from "react";

import {
  clearLibraryReturnPending,
  readLibraryReturnTarget,
} from "./libraryNavigation";
import {
  computeAnchorRestoreScrollTop,
  getRestoreViewportMeasurement,
  restoreHorizontalRailPosition,
  selectLibraryReturnRestoreTarget,
} from "./viewportAnchor";
import { detectClientDeviceClass, detectClientPlatform } from "./platformDetection";


export const DESKTOP_LIBRARY_RETURN_TOLERANCE_PX = 8;
export const DESKTOP_LIBRARY_RETURN_MAX_CORRECTIONS = 2;
const DESKTOP_LIBRARY_RETURN_SETTLE_MS = 160;
const DESKTOP_LIBRARY_RETURN_MAX_LIFETIME_MS = 1200;
const DESKTOP_LIBRARY_RETURN_DEBUG_KEY = "elvern_library_return_debug";
const DESKTOP_SCROLL_KEYS = new Set([
  "ArrowDown",
  "ArrowLeft",
  "ArrowRight",
  "ArrowUp",
  "End",
  "Home",
  "PageDown",
  "PageUp",
  " ",
]);


function debugEnabled(viewportWindow) {
  try {
    return viewportWindow?.localStorage?.getItem(DESKTOP_LIBRARY_RETURN_DEBUG_KEY) === "1";
  } catch {
    return false;
  }
}


function debugLog(viewportWindow, message, values = {}) {
  if (!debugEnabled(viewportWindow)) {
    return;
  }
  const safeValues = Object.fromEntries(
    Object.entries(values).filter(([, value]) => (
      value === null
      || value === undefined
      || typeof value === "boolean"
      || typeof value === "number"
      || typeof value === "string"
    )),
  );
  viewportWindow.console?.debug?.(`[Elvern library return] ${message}`, safeValues);
}


function maxDocumentScrollTop(doc, viewportWindow) {
  const documentElement = doc?.documentElement;
  const body = doc?.body;
  const scrollHeight = Math.max(
    Number(documentElement?.scrollHeight || 0),
    Number(body?.scrollHeight || 0),
  );
  const viewportHeight = Number(
    documentElement?.clientHeight
    || viewportWindow?.innerHeight
    || 0,
  );
  return Math.max(0, scrollHeight - viewportHeight);
}


function targetSource(target, targetNode) {
  if (!targetNode) {
    return "missing";
  }
  const instanceKey = targetNode.getAttribute?.("data-library-card-instance-key");
  if (target?.anchorInstanceKey && instanceKey === String(target.anchorInstanceKey)) {
    return "exact_instance";
  }
  return "item_fallback";
}


function targetDiagnostics(targetNode, viewportWindow) {
  const rect = targetNode?.getBoundingClientRect?.();
  const measurement = getRestoreViewportMeasurement({ viewportWindow });
  return {
    actualScrollY: Number(viewportWindow?.scrollY || 0),
    targetRectTop: Number(rect?.top || 0),
    targetRectHeight: Number(rect?.height || 0),
    targetViewportRatioY: measurement.height ? Number(rect?.top || 0) / measurement.height : null,
    viewportHeight: Number(measurement.height || 0),
    viewportOffsetTop: Number(measurement.offsetTop || 0),
  };
}


export function isDesktopLibraryReturnPlatform({ platform, deviceClass } = {}) {
  return (
    deviceClass === "desktop"
    && ["windows", "mac", "macos", "linux"].includes(String(platform || "").toLowerCase())
  );
}


export function logDesktopLibraryReturnCapture({ target, cardNode = null } = {}) {
  if (typeof window === "undefined" || !target) {
    return;
  }
  const platform = detectClientPlatform();
  const deviceClass = detectClientDeviceClass();
  if (!isDesktopLibraryReturnPlatform({ platform, deviceClass }) || !debugEnabled(window)) {
    return;
  }
  const rect = cardNode?.getBoundingClientRect?.();
  const measurement = getRestoreViewportMeasurement({ viewportWindow: window });
  const instanceKey = target.anchorInstanceKey || null;
  debugLog(window, "library card captured", {
    platform,
    deviceClass,
    listPath: target.listPath,
    anchorItemId: target.anchorItemId,
    anchorInstanceKey: instanceKey,
    sectionKey: instanceKey?.includes(":") ? instanceKey.split(":").slice(0, -1).join(":") : null,
    railKey: target.railKey,
    railScrollLeft: target.railScrollLeft,
    scrollY: target.scrollY,
    cardRectTop: Number(rect?.top || 0),
    cardRectHeight: Number(rect?.height || 0),
    viewportHeight: Number(measurement.height || 0),
    viewportOffsetTop: Number(measurement.offsetTop || 0),
    anchorViewportRatioY: target.anchorViewportRatioY,
    documentCardTop: Number(rect?.top || 0) + Number(window.scrollY || 0),
  });
}


export function createDesktopLibraryReturnRestoreTransaction({
  target,
  rootNode = null,
  doc = typeof document !== "undefined" ? document : null,
  viewportWindow = typeof window !== "undefined" ? window : null,
  backgroundFetching = false,
  settingsPending = false,
  onComplete = () => {},
} = {}) {
  let targetNode = null;
  let anchor = null;
  let source = "missing";
  let correctionCount = 0;
  let finished = false;
  let cancelled = false;
  let finishReason = null;
  let internalScroll = false;
  let startedAt = 0;
  let lastProgrammaticScrollAt = 0;
  let lastProgrammaticScrollY = null;
  let frameOne = 0;
  let frameTwo = 0;
  let settleTimer = 0;
  let lifetimeTimer = 0;
  let externalPending = {
    backgroundFetching: Boolean(backgroundFetching),
    settingsPending: Boolean(settingsPending),
  };
  const listeners = [];

  function snapshot() {
    return {
      cancelled,
      correctionCount,
      finished,
      finishReason,
      targetSource: source,
    };
  }

  function clearScheduledWork() {
    if (frameOne) {
      viewportWindow?.cancelAnimationFrame?.(frameOne);
      frameOne = 0;
    }
    if (frameTwo) {
      viewportWindow?.cancelAnimationFrame?.(frameTwo);
      frameTwo = 0;
    }
    if (settleTimer) {
      viewportWindow?.clearTimeout?.(settleTimer);
      settleTimer = 0;
    }
    if (lifetimeTimer) {
      viewportWindow?.clearTimeout?.(lifetimeTimer);
      lifetimeTimer = 0;
    }
  }

  function removeListeners() {
    listeners.splice(0).forEach(([eventName, listener]) => {
      viewportWindow?.removeEventListener?.(eventName, listener);
    });
  }

  function finish(reason, { wasCancelled = false } = {}) {
    if (finished) {
      return;
    }
    finished = true;
    cancelled = wasCancelled;
    finishReason = reason;
    clearScheduledWork();
    removeListeners();
    rootNode?.removeAttribute?.("data-library-return-restoring");
    debugLog(viewportWindow, "transaction finished", {
      reason,
      cancelled,
      correctionCount,
      targetSource: source,
    });
    onComplete({ reason, cancelled, correctionCount, targetSource: source });
  }

  function performScroll(reason, allowCorrection = false) {
    if (finished || !viewportWindow || !doc) {
      return { clamped: false, errorPx: 0 };
    }
    if (!targetNode) {
      internalScroll = true;
      viewportWindow.scrollTo?.({ top: Number(target?.scrollY || 0), behavior: "auto" });
      lastProgrammaticScrollAt = viewportWindow.performance?.now?.() ?? Date.now();
      lastProgrammaticScrollY = Number(target?.scrollY || 0);
      internalScroll = false;
      debugLog(viewportWindow, "target missing; restored saved scroll", {
        reason,
        requestedScrollTop: Number(target?.scrollY || 0),
        actualScrollY: Number(viewportWindow.scrollY || 0),
      });
      return { clamped: false, errorPx: 0 };
    }

    const measurement = getRestoreViewportMeasurement({ viewportWindow });
    const rect = targetNode.getBoundingClientRect();
    const requestedScrollTop = computeAnchorRestoreScrollTop({
      anchor,
      currentScrollY: viewportWindow.scrollY,
      targetRectTop: rect.top,
      viewportMeasurement: measurement,
    });
    const maximumScrollTop = maxDocumentScrollTop(doc, viewportWindow);
    const resolvedScrollTop = Math.min(
      Math.max(0, Number.isFinite(requestedScrollTop) ? requestedScrollTop : Number(target?.scrollY || 0)),
      maximumScrollTop,
    );
    const clamped = Number.isFinite(requestedScrollTop)
      && requestedScrollTop > maximumScrollTop + DESKTOP_LIBRARY_RETURN_TOLERANCE_PX;
    internalScroll = true;
    viewportWindow.scrollTo?.({ top: resolvedScrollTop, behavior: "auto" });
    lastProgrammaticScrollAt = viewportWindow.performance?.now?.() ?? Date.now();
    lastProgrammaticScrollY = resolvedScrollTop;
    internalScroll = false;
    const diagnostics = targetDiagnostics(targetNode, viewportWindow);
    const expectedTop = measurement.offsetTop + (measurement.height * anchor.viewportRatioY);
    const errorPx = diagnostics.targetRectTop - expectedTop;
    if (allowCorrection) {
      correctionCount += 1;
    }
    debugLog(viewportWindow, reason, {
      ...diagnostics,
      requestedScrollTop,
      resolvedScrollTop,
      maximumScrollTop,
      clamped,
      errorPx,
      correctionCount,
    });
    return { clamped, errorPx };
  }

  function verify(reason) {
    if (finished || !targetNode) {
      return;
    }
    const measurement = getRestoreViewportMeasurement({ viewportWindow });
    const rect = targetNode.getBoundingClientRect();
    const expectedTop = measurement.offsetTop + (measurement.height * anchor.viewportRatioY);
    const errorPx = rect.top - expectedTop;
    debugLog(viewportWindow, reason, {
      ...targetDiagnostics(targetNode, viewportWindow),
      errorPx,
      correctionCount,
      backgroundFetching: externalPending.backgroundFetching,
      settingsPending: externalPending.settingsPending,
    });
    if (
      Math.abs(errorPx) > DESKTOP_LIBRARY_RETURN_TOLERANCE_PX
      && correctionCount < DESKTOP_LIBRARY_RETURN_MAX_CORRECTIONS
    ) {
      const result = performScroll(`${reason}_correction`, true);
      if (result.clamped) {
        finish("near_bottom_clamp");
        return;
      }
    }
  }

  function scheduleSettleVerification() {
    if (finished || externalPending.backgroundFetching || externalPending.settingsPending) {
      return;
    }
    if (settleTimer) {
      viewportWindow.clearTimeout(settleTimer);
    }
    settleTimer = viewportWindow.setTimeout(() => {
      settleTimer = 0;
      verify("settle_verification");
      if (!finished) {
        finish("stable");
      }
    }, DESKTOP_LIBRARY_RETURN_SETTLE_MS);
  }

  function scheduleFrameVerification(reason = "layout_change") {
    if (finished || frameOne || frameTwo) {
      return;
    }
    frameOne = viewportWindow.requestAnimationFrame(() => {
      frameOne = 0;
      debugLog(viewportWindow, "first_animation_frame", targetDiagnostics(targetNode, viewportWindow));
      frameTwo = viewportWindow.requestAnimationFrame(() => {
        frameTwo = 0;
        verify(`${reason}_second_animation_frame`);
        scheduleSettleVerification();
      });
    });
  }

  function cancelFromUser(event) {
    if (finished || internalScroll) {
      return;
    }
    if (event?.type === "keydown" && !DESKTOP_SCROLL_KEYS.has(event.key)) {
      return;
    }
    if (event?.type === "scroll") {
      const currentTime = viewportWindow.performance?.now?.() ?? Date.now();
      const currentScrollY = Number(viewportWindow.scrollY || 0);
      const inInitialNativeScrollWindow = currentTime - startedAt <= 240;
      const matchesRecentProgrammaticScroll = (
        currentTime - lastProgrammaticScrollAt <= 120
        && Number.isFinite(lastProgrammaticScrollY)
        && Math.abs(currentScrollY - lastProgrammaticScrollY) <= 1
      );
      if (inInitialNativeScrollWindow || matchesRecentProgrammaticScroll) {
        return;
      }
    }
    finish("user_interaction", { wasCancelled: true });
  }

  function addCancellationListeners() {
    ["wheel", "pointerdown", "mousedown", "touchstart", "scroll", "keydown"].forEach((eventName) => {
      viewportWindow.addEventListener(eventName, cancelFromUser, { passive: eventName !== "keydown" });
      listeners.push([eventName, cancelFromUser]);
    });
  }

  return {
    start() {
      if (finished || !target || !doc || !viewportWindow) {
        return snapshot();
      }
      rootNode?.setAttribute?.("data-library-return-restoring", "true");
      startedAt = viewportWindow.performance?.now?.() ?? Date.now();
      addCancellationListeners();
      ({ anchor, targetNode } = selectLibraryReturnRestoreTarget(target, { doc }));
      source = targetSource(target, targetNode);
      debugLog(viewportWindow, "transaction started", {
        mountScrollY: Number(viewportWindow.scrollY || 0),
        anchorItemId: target.anchorItemId,
        anchorInstanceKey: target.anchorInstanceKey,
        railKey: target.railKey,
        railScrollLeft: target.railScrollLeft,
        savedScrollY: target.scrollY,
        anchorViewportRatioY: target.anchorViewportRatioY,
        targetSource: source,
      });
      if (targetNode) {
        restoreHorizontalRailPosition({
          targetNode,
          railKey: target.railKey,
          railScrollLeft: target.railScrollLeft,
        });
      }
      const result = performScroll("initial_restore");
      if (!targetNode) {
        finish("target_missing_fallback");
        return snapshot();
      }
      if (result.clamped) {
        finish("near_bottom_clamp");
        return snapshot();
      }
      scheduleFrameVerification("initial_restore");
      lifetimeTimer = viewportWindow.setTimeout(() => {
        verify("maximum_lifetime_verification");
        finish("maximum_lifetime");
      }, DESKTOP_LIBRARY_RETURN_MAX_LIFETIME_MS);
      return snapshot();
    },
    cancel(reason = "disposed") {
      finish(reason, { wasCancelled: reason === "user_interaction" });
    },
    dispose() {
      if (finished) {
        return;
      }
      finished = true;
      finishReason = "disposed";
      clearScheduledWork();
      removeListeners();
      rootNode?.removeAttribute?.("data-library-return-restoring");
      debugLog(viewportWindow, "transaction disposed", {
        correctionCount,
        targetSource: source,
      });
    },
    getSnapshot: snapshot,
    notifyLayoutChange(reason = "layout_change") {
      if (!finished) {
        scheduleFrameVerification(reason);
        scheduleSettleVerification();
      }
    },
    setExternalPending(nextPending = {}) {
      externalPending = {
        backgroundFetching: Boolean(nextPending.backgroundFetching),
        settingsPending: Boolean(nextPending.settingsPending),
      };
      if (!externalPending.backgroundFetching && !externalPending.settingsPending) {
        scheduleFrameVerification("external_data_settled");
        scheduleSettleVerification();
      }
    },
  };
}


export function useDesktopLibraryReturnRestore({
  enabled,
  currentListPath,
  locationState,
  loading,
  rootRef,
  platform,
  deviceClass,
  navigationType = null,
  protectedIdentity = {},
  queryState = {},
  settingsState = {},
} = {}) {
  const transactionRef = useRef(null);
  const completedRestoreKeyRef = useRef("");

  useLayoutEffect(() => {
    if (
      !enabled
      || loading
      || !isDesktopLibraryReturnPlatform({ platform, deviceClass })
      || typeof window === "undefined"
      || typeof document === "undefined"
    ) {
      return undefined;
    }
    const target = readLibraryReturnTarget(protectedIdentity);
    const shouldRestore = Boolean(locationState?.restoreLibraryReturn) || Boolean(target?.pendingRestore);
    if (!shouldRestore || !target || target.listPath !== currentListPath) {
      return undefined;
    }
    const restoreKey = [
      currentListPath,
      target.anchorInstanceKey || target.anchorItemId || "none",
      target.anchorViewportRatioY ?? "none",
      target.scrollY,
    ].join(":");
    if (completedRestoreKeyRef.current === restoreKey || transactionRef.current) {
      return undefined;
    }
    debugLog(window, "restore context", {
      platform,
      deviceClass,
      navigationType,
      exactQueryCacheExists: Boolean(queryState.hasExactData),
      queryFresh: Boolean(queryState.isFresh),
      queryFetching: Boolean(queryState.isFetching),
      queryDataUpdatedAt: Number(queryState.dataUpdatedAt || 0),
      settingsHasData: Boolean(settingsState.hasData),
      settingsPending: Boolean(settingsState.isPending),
    });
    const transaction = createDesktopLibraryReturnRestoreTransaction({
      target,
      rootNode: rootRef?.current || null,
      doc: document,
      viewportWindow: window,
      backgroundFetching: Boolean(queryState.isFetching),
      settingsPending: Boolean(settingsState.isPending),
      onComplete: (result) => {
        completedRestoreKeyRef.current = restoreKey;
        transactionRef.current = null;
        clearLibraryReturnPending(protectedIdentity);
        debugLog(window, "pending return cleared", result);
      },
    });
    transactionRef.current = transaction;
    transaction.start();
    return () => {
      if (transactionRef.current === transaction) {
        transaction.dispose();
        transactionRef.current = null;
      }
    };
  }, [
    currentListPath,
    deviceClass,
    enabled,
    loading,
    locationState,
    navigationType,
    platform,
    protectedIdentity?.role,
    protectedIdentity?.userId,
    rootRef,
  ]);

  useLayoutEffect(() => {
    const transaction = transactionRef.current;
    if (!transaction) {
      return;
    }
    transaction.setExternalPending({
      backgroundFetching: Boolean(queryState.isFetching),
      settingsPending: Boolean(settingsState.isPending),
    });
    transaction.notifyLayoutChange("query_or_settings_commit");
  }, [
    queryState.dataUpdatedAt,
    queryState.isFetching,
    settingsState.hasData,
    settingsState.isPending,
  ]);

  return transactionRef;
}
