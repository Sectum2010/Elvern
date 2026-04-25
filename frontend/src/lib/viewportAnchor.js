export const VIEWPORT_ANCHOR_MEDIA_ITEM = "media_item";
export const VIEWPORT_ANCHOR_SERIES_RAIL = "series_rail";
export const VIEWPORT_ANCHOR_PROBE_RATIO_Y = 0.42;
export const CENTER_MOVIE_VIEWPORT_RATIO_Y = 0.45;
export const MAX_ORIENTATION_RESTORE_CORRECTIONS = 2;

const DEBUG_STORAGE_KEY = "elvern_smart_poster_debug";
const DEFAULT_REFINE_RESTORE_DELAY_MS = 150;
const CENTER_MOVIE_SAMPLE_POINTS = Object.freeze([
  { key: "center", xRatio: 0.5, yRatio: 0.45 },
  { key: "upper", xRatio: 0.5, yRatio: 0.35 },
  { key: "lower", xRatio: 0.5, yRatio: 0.55 },
  { key: "left", xRatio: 0.35, yRatio: 0.45 },
  { key: "right", xRatio: 0.65, yRatio: 0.45 },
]);
const LANDSCAPE_SAMPLE_POINTS = Object.freeze([
  { key: "center", xRatio: 0.5, yRatio: 0.42 },
  { key: "upper", xRatio: 0.5, yRatio: 0.3 },
  { key: "lower", xRatio: 0.5, yRatio: 0.58 },
  { key: "left", xRatio: 0.35, yRatio: 0.42 },
  { key: "right", xRatio: 0.65, yRatio: 0.42 },
]);
const PORTRAIT_SAMPLE_POINTS = Object.freeze([
  { key: "center", xRatio: 0.5, yRatio: 0.42 },
  { key: "upper", xRatio: 0.5, yRatio: 0.3 },
  { key: "lower", xRatio: 0.5, yRatio: 0.58 },
]);
const USER_CANCEL_EVENT_TYPES = new Set(["touchstart", "touchmove", "wheel", "pointerdown"]);
const KEYBOARD_SCROLL_KEYS = new Set([
  "ArrowUp",
  "ArrowDown",
  "PageUp",
  "PageDown",
  "Home",
  "End",
  " ",
]);

function nowMs() {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return performance.now();
  }
  return Date.now();
}

function toFiniteNumber(value, fallback = 0) {
  return Number.isFinite(value) ? Number(value) : fallback;
}

function safeSelectorValue(value) {
  return String(value).replaceAll('"', '\\"');
}

function dedupeAnchors(anchors) {
  const seen = new Set();
  return anchors.filter((anchor) => {
    const dedupeKey = `${anchor.anchorType}:${anchor.instanceKey || anchor.itemId || anchor.railKey || anchor.sampleKey || "unknown"}`;
    if (seen.has(dedupeKey)) {
      return false;
    }
    seen.add(dedupeKey);
    return true;
  });
}

export function getViewportMeasurement({
  viewportWindow = typeof window !== "undefined" ? window : null,
} = {}) {
  if (!viewportWindow) {
    return {
      width: 0,
      height: 0,
      offsetTop: 0,
      offsetLeft: 0,
      scrollY: 0,
      scrollX: 0,
    };
  }
  const visualViewport = viewportWindow.visualViewport;
  return {
    width: toFiniteNumber(visualViewport?.width, viewportWindow.innerWidth || 0),
    height: toFiniteNumber(visualViewport?.height, viewportWindow.innerHeight || 0),
    offsetTop: toFiniteNumber(visualViewport?.offsetTop, 0),
    offsetLeft: toFiniteNumber(visualViewport?.offsetLeft, 0),
    scrollY: toFiniteNumber(viewportWindow.scrollY, 0),
    scrollX: toFiniteNumber(viewportWindow.scrollX, 0),
  };
}

export function getViewportSamplePoints({ orientation = "portrait" } = {}) {
  return orientation === "landscape"
    ? LANDSCAPE_SAMPLE_POINTS
    : PORTRAIT_SAMPLE_POINTS;
}

export function getCenterMovieSamplePoints() {
  return CENTER_MOVIE_SAMPLE_POINTS;
}

function buildBaseAnchor({
  anchorType,
  viewportRatioY,
  viewportRatioX,
  rectTopRelativeToVisualViewport,
  rectLeftRelativeToVisualViewport,
  cardRectTop,
  cardRectLeft,
  cardRectHeight,
  cardRectWidth,
  scrollY,
  orientation,
  captureTime,
  sampleKey,
}) {
  if (!Number.isFinite(viewportRatioY)) {
    return null;
  }
  const resolvedCaptureTime = toFiniteNumber(captureTime, nowMs());
  return {
    anchorType,
    viewportRatioY,
    viewportRatioX: Number.isFinite(viewportRatioX) ? viewportRatioX : null,
    rectTopRelativeToVisualViewport: Number.isFinite(rectTopRelativeToVisualViewport)
      ? rectTopRelativeToVisualViewport
      : null,
    rectLeftRelativeToVisualViewport: Number.isFinite(rectLeftRelativeToVisualViewport)
      ? rectLeftRelativeToVisualViewport
      : null,
    cardRectTop: Number.isFinite(cardRectTop) ? cardRectTop : null,
    cardRectLeft: Number.isFinite(cardRectLeft) ? cardRectLeft : null,
    cardRectHeight: Number.isFinite(cardRectHeight) ? cardRectHeight : null,
    cardRectWidth: Number.isFinite(cardRectWidth) ? cardRectWidth : null,
    scrollY: toFiniteNumber(scrollY, 0),
    orientation: orientation || "portrait",
    captureTime: resolvedCaptureTime,
    capturedAt: resolvedCaptureTime,
    sampleKey: sampleKey || "center",
  };
}

export function buildMediaItemAnchor({
  itemId,
  instanceKey = null,
  rectTop,
  rectLeft = 0,
  rectHeight = 0,
  rectWidth = 0,
  viewportHeight,
  viewportWidth = 0,
  viewportOffsetTop = 0,
  viewportOffsetLeft = 0,
  scrollY,
  orientation,
  captureTime = nowMs(),
  sampleKey = "center",
} = {}) {
  if (itemId === undefined || itemId === null || !viewportHeight) {
    return null;
  }
  const rectTopRelativeToVisualViewport = rectTop - viewportOffsetTop;
  const rectLeftRelativeToVisualViewport = rectLeft - viewportOffsetLeft;
  const baseAnchor = buildBaseAnchor({
    anchorType: VIEWPORT_ANCHOR_MEDIA_ITEM,
    viewportRatioY: rectTopRelativeToVisualViewport / viewportHeight,
    viewportRatioX: viewportWidth ? (rectLeftRelativeToVisualViewport / viewportWidth) : null,
    rectTopRelativeToVisualViewport,
    rectLeftRelativeToVisualViewport,
    cardRectTop: rectTop,
    cardRectLeft: rectLeft,
    cardRectHeight: rectHeight,
    cardRectWidth: rectWidth,
    scrollY,
    orientation,
    captureTime,
    sampleKey,
  });
  if (!baseAnchor) {
    return null;
  }
  return {
    ...baseAnchor,
    itemId: String(itemId),
    instanceKey: instanceKey ? String(instanceKey) : null,
  };
}

export function buildSeriesRailAnchor({
  railKey,
  rectTop,
  rectLeft = 0,
  rectHeight = 0,
  rectWidth = 0,
  viewportHeight,
  viewportWidth = 0,
  viewportOffsetTop = 0,
  viewportOffsetLeft = 0,
  scrollY,
  orientation,
  captureTime = nowMs(),
  sampleKey = "center",
} = {}) {
  if (!railKey || !viewportHeight) {
    return null;
  }
  const rectTopRelativeToVisualViewport = rectTop - viewportOffsetTop;
  const rectLeftRelativeToVisualViewport = rectLeft - viewportOffsetLeft;
  const baseAnchor = buildBaseAnchor({
    anchorType: VIEWPORT_ANCHOR_SERIES_RAIL,
    viewportRatioY: rectTopRelativeToVisualViewport / viewportHeight,
    viewportRatioX: viewportWidth ? (rectLeftRelativeToVisualViewport / viewportWidth) : null,
    rectTopRelativeToVisualViewport,
    rectLeftRelativeToVisualViewport,
    cardRectTop: rectTop,
    cardRectLeft: rectLeft,
    cardRectHeight: rectHeight,
    cardRectWidth: rectWidth,
    scrollY,
    orientation,
    captureTime,
    sampleKey,
  });
  if (!baseAnchor) {
    return null;
  }
  return {
    ...baseAnchor,
    railKey: String(railKey),
  };
}

export function chooseViewportAnchor({
  mediaItemId = null,
  mediaInstanceKey = null,
  mediaRectTop = null,
  mediaRectLeft = 0,
  seriesRailKey = null,
  seriesRectTop = null,
  seriesRectLeft = 0,
  viewportHeight = 0,
  viewportWidth = 0,
  viewportOffsetTop = 0,
  viewportOffsetLeft = 0,
  scrollY = 0,
  orientation = "portrait",
  sampleKey = "center",
  captureTime = nowMs(),
} = {}) {
  const mediaAnchor = buildMediaItemAnchor({
    itemId: mediaItemId,
    instanceKey: mediaInstanceKey,
    rectTop: mediaRectTop,
    rectLeft: mediaRectLeft,
    viewportHeight,
    viewportWidth,
    viewportOffsetTop,
    viewportOffsetLeft,
    scrollY,
    orientation,
    captureTime,
    sampleKey,
  });
  if (mediaAnchor) {
    return mediaAnchor;
  }
  return buildSeriesRailAnchor({
    railKey: seriesRailKey,
    rectTop: seriesRectTop,
    rectLeft: seriesRectLeft,
    viewportHeight,
    viewportWidth,
    viewportOffsetTop,
    viewportOffsetLeft,
    scrollY,
    orientation,
    captureTime,
    sampleKey,
  });
}

export function getViewportAnchorId(anchor) {
  if (!anchor) {
    return null;
  }
  if (anchor.anchorType === VIEWPORT_ANCHOR_MEDIA_ITEM) {
    return anchor.instanceKey || anchor.itemId || null;
  }
  return anchor.railKey || null;
}

export function formatViewportAnchorDebug(anchor) {
  if (!anchor) {
    return "none";
  }
  return `${anchor.anchorType}:${getViewportAnchorId(anchor) || "unknown"}`;
}

export function formatViewportAnchorCandidateListDebug(anchors = []) {
  if (!anchors.length) {
    return [];
  }
  return anchors.map((anchor) => formatViewportAnchorDebug(anchor));
}

export function shouldLogViewportAnchorDebug() {
  if (typeof window === "undefined") {
    return false;
  }
  try {
    return window.localStorage.getItem(DEBUG_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function computeRestoreScrollTop({
  currentScrollY = 0,
  targetRectTop = null,
  viewportRatioY = null,
  viewportHeight = 0,
  viewportOffsetTop = 0,
  innerHeight = 0,
}) {
  const effectiveViewportHeight = viewportHeight || innerHeight;
  if (!Number.isFinite(targetRectTop) || !Number.isFinite(viewportRatioY) || !effectiveViewportHeight) {
    return null;
  }
  return Math.max(
    0,
    toFiniteNumber(currentScrollY, 0)
      + targetRectTop
      - toFiniteNumber(viewportOffsetTop, 0)
      - (effectiveViewportHeight * viewportRatioY),
  );
}

export function computeAnchorRestoreScrollTop({
  anchor = null,
  targetRectTop = null,
  currentScrollY = 0,
  viewportWindow = typeof window !== "undefined" ? window : null,
  viewportMeasurement = null,
  defaultViewportRatioY = CENTER_MOVIE_VIEWPORT_RATIO_Y,
} = {}) {
  const measurement = viewportMeasurement || getViewportMeasurement({ viewportWindow });
  if (!measurement.height) {
    return null;
  }
  return computeRestoreScrollTop({
    currentScrollY,
    targetRectTop,
    viewportRatioY: Number.isFinite(anchor?.viewportRatioY)
      ? anchor.viewportRatioY
      : defaultViewportRatioY,
    viewportHeight: measurement.height,
    viewportOffsetTop: measurement.offsetTop,
  });
}

export function getRestoreCorrectionTolerancePx({
  targetRectHeight = 0,
} = {}) {
  return Math.max(toFiniteNumber(targetRectHeight, 0) * 0.35, 64);
}

export function computeRestoreVerificationCorrection({
  anchor = null,
  targetRectTop = null,
  targetRectHeight = 0,
  currentScrollY = 0,
  viewportWindow = typeof window !== "undefined" ? window : null,
  viewportMeasurement = null,
  correctionCount = 0,
  maxCorrections = MAX_ORIENTATION_RESTORE_CORRECTIONS,
  defaultViewportRatioY = CENTER_MOVIE_VIEWPORT_RATIO_Y,
} = {}) {
  if (correctionCount >= maxCorrections) {
    return null;
  }
  const measurement = viewportMeasurement || getViewportMeasurement({ viewportWindow });
  if (!measurement.height || !Number.isFinite(targetRectTop)) {
    return null;
  }
  const viewportRatioY = Number.isFinite(anchor?.viewportRatioY)
    ? anchor.viewportRatioY
    : defaultViewportRatioY;
  const desiredRectTop = measurement.offsetTop + (measurement.height * viewportRatioY);
  const restoreErrorPx = targetRectTop - desiredRectTop;
  const tolerancePx = getRestoreCorrectionTolerancePx({ targetRectHeight });
  if (Math.abs(restoreErrorPx) <= tolerancePx) {
    return null;
  }
  return Math.max(0, toFiniteNumber(currentScrollY, 0) + restoreErrorPx);
}

export function isRestoreAttemptStale({
  scheduledToken = 0,
  activeToken = 0,
  scheduledUserIntentVersion = 0,
  currentUserIntentVersion = 0,
} = {}) {
  return (
    scheduledToken !== activeToken
    || scheduledUserIntentVersion !== currentUserIntentVersion
  );
}

export function isUserRestoreCancellationEvent({
  type = "",
  key = "",
} = {}) {
  if (USER_CANCEL_EVENT_TYPES.has(type)) {
    return true;
  }
  if (type === "keydown") {
    return KEYBOARD_SCROLL_KEYS.has(key);
  }
  return false;
}

function nearestSeriesRailNode(doc, probeY) {
  const seriesNodes = Array.from(doc.querySelectorAll("[data-series-rail-key]"));
  if (!seriesNodes.length) {
    return null;
  }
  return seriesNodes.reduce((bestNode, candidateNode) => {
    const candidateRect = candidateNode.getBoundingClientRect();
    const candidateCenter = candidateRect.top + (candidateRect.height / 2);
    if (!bestNode) {
      return candidateNode;
    }
    const bestRect = bestNode.getBoundingClientRect();
    const bestCenter = bestRect.top + (bestRect.height / 2);
    return Math.abs(candidateCenter - probeY) < Math.abs(bestCenter - probeY)
      ? candidateNode
      : bestNode;
  }, null);
}

function anchorFromProbeNode({
  probeNode,
  sampleKey,
  measurement,
  orientation,
  captureTime,
}) {
  const mediaNode = probeNode?.closest?.("[data-library-item-id]") || null;
  if (mediaNode) {
    const rect = mediaNode.getBoundingClientRect();
    return buildMediaItemAnchor({
      itemId: mediaNode.getAttribute("data-library-item-id"),
      instanceKey: mediaNode.getAttribute("data-library-card-instance-key"),
      rectTop: rect.top,
      rectLeft: rect.left,
      rectHeight: rect.height,
      rectWidth: rect.width,
      viewportHeight: measurement.height,
      viewportWidth: measurement.width,
      viewportOffsetTop: measurement.offsetTop,
      viewportOffsetLeft: measurement.offsetLeft,
      scrollY: measurement.scrollY,
      orientation,
      captureTime,
      sampleKey,
    });
  }
  const seriesNode = probeNode?.closest?.("[data-series-rail-key]") || null;
  if (!seriesNode) {
    return null;
  }
  const rect = seriesNode.getBoundingClientRect();
  return buildSeriesRailAnchor({
    railKey: seriesNode.getAttribute("data-series-rail-key"),
    rectTop: rect.top,
    rectLeft: rect.left,
    rectHeight: rect.height,
    rectWidth: rect.width,
    viewportHeight: measurement.height,
    viewportWidth: measurement.width,
    viewportOffsetTop: measurement.offsetTop,
    viewportOffsetLeft: measurement.offsetLeft,
    scrollY: measurement.scrollY,
    orientation,
    captureTime,
    sampleKey,
  });
}

export function captureCenterMovieAnchor({
  doc = typeof document !== "undefined" ? document : null,
  viewportWindow = typeof window !== "undefined" ? window : null,
  orientation = "portrait",
} = {}) {
  if (!doc || !viewportWindow || typeof doc.elementFromPoint !== "function") {
    return null;
  }
  const measurement = getViewportMeasurement({ viewportWindow });
  if (!measurement.width || !measurement.height) {
    return null;
  }
  const captureTime = nowMs();
  for (const sample of CENTER_MOVIE_SAMPLE_POINTS) {
    const probeX = measurement.offsetLeft + (measurement.width * sample.xRatio);
    const probeY = measurement.offsetTop + (measurement.height * sample.yRatio);
    const probeNode = doc.elementFromPoint(probeX, probeY);
    const mediaNode = probeNode?.closest?.("[data-library-item-id]") || null;
    if (!mediaNode) {
      continue;
    }
    const rect = mediaNode.getBoundingClientRect();
    return buildMediaItemAnchor({
      itemId: mediaNode.getAttribute("data-library-item-id"),
      instanceKey: mediaNode.getAttribute("data-library-card-instance-key"),
      rectTop: rect.top,
      rectLeft: rect.left,
      rectHeight: rect.height,
      rectWidth: rect.width,
      viewportHeight: measurement.height,
      viewportWidth: measurement.width,
      viewportOffsetTop: measurement.offsetTop,
      viewportOffsetLeft: measurement.offsetLeft,
      scrollY: measurement.scrollY,
      orientation,
      captureTime,
      sampleKey: sample.key,
    });
  }
  return null;
}

export function captureViewportAnchorCandidates({
  doc = typeof document !== "undefined" ? document : null,
  viewportWindow = typeof window !== "undefined" ? window : null,
  allowSeriesQueryFallback = false,
  orientation = "portrait",
} = {}) {
  if (!doc || !viewportWindow || typeof doc.elementFromPoint !== "function") {
    return [];
  }
  const measurement = getViewportMeasurement({ viewportWindow });
  if (!measurement.width || !measurement.height) {
    return [];
  }
  const samplePoints = getViewportSamplePoints({ orientation });
  const captureTime = nowMs();
  const mediaAnchors = [];
  const seriesAnchors = [];

  samplePoints.forEach((sample) => {
    const probeX = measurement.offsetLeft + (measurement.width * sample.xRatio);
    const probeY = measurement.offsetTop + (measurement.height * sample.yRatio);
    const probeNode = doc.elementFromPoint(probeX, probeY);
    const nextAnchor = anchorFromProbeNode({
      probeNode,
      sampleKey: sample.key,
      measurement,
      orientation,
      captureTime,
    });
    if (!nextAnchor) {
      return;
    }
    if (nextAnchor.anchorType === VIEWPORT_ANCHOR_MEDIA_ITEM) {
      mediaAnchors.push(nextAnchor);
      return;
    }
    seriesAnchors.push(nextAnchor);
  });

  const uniqueMediaAnchors = dedupeAnchors(mediaAnchors);
  if (uniqueMediaAnchors.length > 0) {
    return uniqueMediaAnchors;
  }

  const uniqueSeriesAnchors = dedupeAnchors(seriesAnchors);
  if (uniqueSeriesAnchors.length > 0) {
    return uniqueSeriesAnchors;
  }

  if (!allowSeriesQueryFallback) {
    return [];
  }
  const fallbackSample = samplePoints[0] || { key: "center", yRatio: VIEWPORT_ANCHOR_PROBE_RATIO_Y };
  const fallbackProbeY = measurement.offsetTop + (measurement.height * fallbackSample.yRatio);
  const fallbackSeriesNode = nearestSeriesRailNode(doc, fallbackProbeY);
  if (!fallbackSeriesNode) {
    return [];
  }
  const rect = fallbackSeriesNode.getBoundingClientRect();
  const fallbackAnchor = buildSeriesRailAnchor({
    railKey: fallbackSeriesNode.getAttribute("data-series-rail-key"),
    rectTop: rect.top,
    rectLeft: rect.left,
    rectHeight: rect.height,
    rectWidth: rect.width,
    viewportHeight: measurement.height,
    viewportWidth: measurement.width,
    viewportOffsetTop: measurement.offsetTop,
    viewportOffsetLeft: measurement.offsetLeft,
    scrollY: measurement.scrollY,
    orientation,
    captureTime,
    sampleKey: fallbackSample.key,
  });
  return fallbackAnchor ? [fallbackAnchor] : [];
}

export function captureViewportAnchor(options = {}) {
  return captureViewportAnchorCandidates(options)[0] || null;
}

export function findViewportAnchorTarget(anchor, {
  doc = typeof document !== "undefined" ? document : null,
} = {}) {
  if (!anchor || !doc) {
    return null;
  }
  if (anchor.anchorType === VIEWPORT_ANCHOR_MEDIA_ITEM && anchor.itemId) {
    if (anchor.instanceKey) {
      const exactNode = doc.querySelector(
        `[data-library-card-instance-key="${safeSelectorValue(anchor.instanceKey)}"]`,
      );
      if (exactNode) {
        return exactNode;
      }
    }
    return doc.querySelector(`[data-library-item-id="${safeSelectorValue(anchor.itemId)}"]`);
  }
  if (anchor.anchorType === VIEWPORT_ANCHOR_SERIES_RAIL && anchor.railKey) {
    return doc.querySelector(`[data-series-rail-key="${safeSelectorValue(anchor.railKey)}"]`);
  }
  return null;
}

export function selectRestoreAnchorCandidate(anchors = [], {
  doc = typeof document !== "undefined" ? document : null,
} = {}) {
  for (const anchor of anchors) {
    const targetNode = findViewportAnchorTarget(anchor, { doc });
    if (targetNode) {
      return {
        anchor,
        targetNode,
      };
    }
  }
  return {
    anchor: null,
    targetNode: null,
  };
}

export function selectPreferredOrientationRestoreTarget({
  frozenAnchor = null,
  fallbackAnchors = [],
  doc = typeof document !== "undefined" ? document : null,
} = {}) {
  if (frozenAnchor?.anchorType === VIEWPORT_ANCHOR_MEDIA_ITEM) {
    const targetNode = findViewportAnchorTarget(frozenAnchor, { doc });
    if (!targetNode) {
      return {
        anchor: null,
        targetNode: null,
        source: "frozen_missing",
      };
    }
    return {
      anchor: frozenAnchor,
      targetNode,
      source: "frozen",
    };
  }
  const fallbackSelection = selectRestoreAnchorCandidate(fallbackAnchors, { doc });
  return {
    anchor: fallbackSelection.anchor,
    targetNode: fallbackSelection.targetNode,
    source: fallbackSelection.targetNode ? "fallback" : "fallback_missing",
  };
}

export function getOrientationRestoreRefinementDelayMs() {
  return DEFAULT_REFINE_RESTORE_DELAY_MS;
}
