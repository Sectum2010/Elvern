import test from "node:test";
import assert from "node:assert/strict";

import {
  buildMediaItemAnchor,
  captureCenterMovieAnchor,
  computeAnchorRestoreScrollTop,
  computeRestoreScrollTop,
  computeRestoreVerificationCorrection,
  getViewportMeasurement,
  isRestoreAttemptStale,
  isUserRestoreCancellationEvent,
  selectPreferredOrientationRestoreTarget,
  VIEWPORT_ANCHOR_MEDIA_ITEM,
} from "./viewportAnchor.js";

function createElement({
  mediaItemId = null,
  seriesRailKey = null,
  rectTop = 0,
  rectLeft = 0,
  rectHeight = 120,
  rectWidth = 90,
} = {}) {
  return {
    closest(selector) {
      if (selector === "[data-library-item-id]" && mediaItemId !== null) {
        return this;
      }
      if (selector === "[data-series-rail-key]" && seriesRailKey !== null) {
        return this;
      }
      return null;
    },
    getBoundingClientRect() {
      return {
        top: rectTop,
        left: rectLeft,
        height: rectHeight,
        width: rectWidth,
      };
    },
    getAttribute(name) {
      if (name === "data-library-item-id") {
        return mediaItemId === null ? null : String(mediaItemId);
      }
      if (name === "data-series-rail-key") {
        return seriesRailKey === null ? null : String(seriesRailKey);
      }
      return null;
    },
  };
}

function createProbeDocument(probeMap) {
  return {
    elementFromPoint(x, y) {
      const key = `${Math.round(x)}:${Math.round(y)}`;
      return probeMap.get(key) || null;
    },
    querySelector(selector) {
      const itemMatch = selector.match(/^\[data-library-item-id="(.+)"\]$/);
      if (itemMatch) {
        return Array.from(probeMap.values()).find(
          (node) => node?.getAttribute?.("data-library-item-id") === itemMatch[1],
        ) || null;
      }
      const railMatch = selector.match(/^\[data-series-rail-key="(.+)"\]$/);
      if (railMatch) {
        return Array.from(probeMap.values()).find(
          (node) => node?.getAttribute?.("data-series-rail-key") === railMatch[1],
        ) || null;
      }
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
}

test("center anchor tracker returns the primary center media item", () => {
  const centerNode = createElement({
    mediaItemId: "41",
    rectTop: 220,
    rectLeft: 120,
    rectHeight: 140,
    rectWidth: 96,
  });
  const doc = createProbeDocument(new Map([
    ["195:360", centerNode],
  ]));

  const anchor = captureCenterMovieAnchor({
    doc,
    viewportWindow: {
      innerWidth: 390,
      innerHeight: 800,
      scrollX: 0,
      scrollY: 1200,
      visualViewport: {
        width: 390,
        height: 800,
        offsetTop: 0,
        offsetLeft: 0,
      },
    },
    orientation: "portrait",
  });

  assert.equal(anchor?.anchorType, VIEWPORT_ANCHOR_MEDIA_ITEM);
  assert.equal(anchor?.itemId, "41");
  assert.equal(anchor?.sampleKey, "center");
});

test("fallback sample point finds a media item when the center point misses", () => {
  const upperNode = createElement({
    mediaItemId: "52",
    rectTop: 140,
    rectLeft: 90,
    rectHeight: 140,
    rectWidth: 96,
  });
  const doc = createProbeDocument(new Map([
    ["195:280", upperNode],
  ]));

  const anchor = captureCenterMovieAnchor({
    doc,
    viewportWindow: {
      innerWidth: 390,
      innerHeight: 800,
      scrollX: 0,
      scrollY: 700,
      visualViewport: {
        width: 390,
        height: 800,
        offsetTop: 0,
        offsetLeft: 0,
      },
    },
    orientation: "portrait",
  });

  assert.equal(anchor?.itemId, "52");
  assert.equal(anchor?.sampleKey, "upper");
});

test("media item anchor is preferred over a series rail at the same sample point", () => {
  const node = createElement({
    mediaItemId: "88",
    seriesRailKey: "rail-a",
    rectTop: 260,
    rectLeft: 110,
    rectHeight: 140,
    rectWidth: 96,
  });
  const doc = createProbeDocument(new Map([
    ["195:360", node],
  ]));

  const anchor = captureCenterMovieAnchor({
    doc,
    viewportWindow: {
      innerWidth: 390,
      innerHeight: 800,
      scrollX: 0,
      scrollY: 900,
      visualViewport: {
        width: 390,
        height: 800,
        offsetTop: 0,
        offsetLeft: 0,
      },
    },
    orientation: "portrait",
  });

  assert.equal(anchor?.anchorType, VIEWPORT_ANCHOR_MEDIA_ITEM);
  assert.equal(anchor?.itemId, "88");
});

test("frozen stable anchor is preferred over orientation-time capture", () => {
  const frozenAnchor = buildMediaItemAnchor({
    itemId: "stable",
    rectTop: 220,
    rectLeft: 40,
    rectHeight: 140,
    rectWidth: 96,
    viewportHeight: 800,
    viewportWidth: 390,
    scrollY: 1500,
    orientation: "portrait",
  });
  const fallbackAnchor = buildMediaItemAnchor({
    itemId: "fallback",
    rectTop: 300,
    rectLeft: 180,
    rectHeight: 140,
    rectWidth: 96,
    viewportHeight: 390,
    viewportWidth: 844,
    scrollY: 1500,
    orientation: "landscape",
  });
  const stableNode = createElement({ mediaItemId: "stable", rectTop: 120 });
  const fallbackNode = createElement({ mediaItemId: "fallback", rectTop: 240 });
  const doc = createProbeDocument(new Map([
    ["1:1", stableNode],
    ["2:2", fallbackNode],
  ]));

  const result = selectPreferredOrientationRestoreTarget({
    frozenAnchor,
    fallbackAnchors: [fallbackAnchor],
    doc,
  });

  assert.equal(result.source, "frozen");
  assert.equal(result.anchor?.itemId, "stable");
  assert.equal(result.targetNode, stableNode);
});

test("direct restore computes the scroll target from item id anchor ratio", () => {
  const anchor = buildMediaItemAnchor({
    itemId: "141",
    rectTop: 200,
    rectLeft: 40,
    rectHeight: 140,
    rectWidth: 96,
    viewportHeight: 800,
    viewportWidth: 390,
    scrollY: 1200,
    orientation: "portrait",
  });
  const measurement = getViewportMeasurement({
    viewportWindow: {
      innerWidth: 390,
      innerHeight: 800,
      scrollX: 0,
      scrollY: 1200,
      visualViewport: {
        width: 390,
        height: 800,
        offsetTop: 0,
        offsetLeft: 0,
      },
    },
  });

  assert.equal(
    computeAnchorRestoreScrollTop({
      anchor,
      currentScrollY: 1200,
      targetRectTop: 340,
      viewportMeasurement: measurement,
    }),
    1340,
  );
});

test("missing target returns null and does not fall back to a guessed scroll", () => {
  const frozenAnchor = buildMediaItemAnchor({
    itemId: "missing",
    rectTop: 220,
    rectLeft: 0,
    rectHeight: 140,
    rectWidth: 96,
    viewportHeight: 800,
    viewportWidth: 390,
    scrollY: 1000,
    orientation: "portrait",
  });

  const result = selectPreferredOrientationRestoreTarget({
    frozenAnchor,
    fallbackAnchors: [],
    doc: { querySelector() { return null; } },
  });

  assert.equal(result.source, "frozen_missing");
  assert.equal(result.anchor, null);
  assert.equal(result.targetNode, null);
});

test("no scroll-to-top fallback is produced when restore inputs are missing", () => {
  assert.equal(
    computeRestoreScrollTop({
      currentScrollY: 900,
      targetRectTop: null,
      viewportRatioY: 0.25,
      viewportHeight: 800,
      viewportOffsetTop: 0,
    }),
    null,
  );
  assert.equal(
    computeAnchorRestoreScrollTop({
      anchor: null,
      currentScrollY: 900,
      targetRectTop: null,
      viewportMeasurement: {
        width: 390,
        height: 800,
        offsetTop: 0,
        offsetLeft: 0,
        scrollY: 900,
        scrollX: 0,
      },
    }),
    null,
  );
});

test("verification correction computes a second scroll only when the error exceeds tolerance", () => {
  const anchor = buildMediaItemAnchor({
    itemId: "200",
    rectTop: 160,
    rectLeft: 0,
    rectHeight: 120,
    rectWidth: 96,
    viewportHeight: 800,
    viewportWidth: 390,
    scrollY: 1000,
    orientation: "portrait",
  });
  const measurement = {
    width: 390,
    height: 800,
    offsetTop: 0,
    offsetLeft: 0,
    scrollY: 1000,
    scrollX: 0,
  };

  assert.equal(
    computeRestoreVerificationCorrection({
      anchor,
      currentScrollY: 1000,
      targetRectTop: 320,
      targetRectHeight: 120,
      viewportMeasurement: measurement,
      correctionCount: 0,
    }),
    1160,
  );
  assert.equal(
    computeRestoreVerificationCorrection({
      anchor,
      currentScrollY: 1000,
      targetRectTop: 210,
      targetRectHeight: 120,
      viewportMeasurement: measurement,
      correctionCount: 0,
    }),
    null,
  );
});

test("verification correction does not run more than the max correction count", () => {
  const anchor = buildMediaItemAnchor({
    itemId: "300",
    rectTop: 160,
    rectLeft: 0,
    rectHeight: 120,
    rectWidth: 96,
    viewportHeight: 800,
    viewportWidth: 390,
    scrollY: 1000,
    orientation: "portrait",
  });

  assert.equal(
    computeRestoreVerificationCorrection({
      anchor,
      currentScrollY: 1000,
      targetRectTop: 320,
      targetRectHeight: 120,
      viewportMeasurement: {
        width: 390,
        height: 800,
        offsetTop: 0,
        offsetLeft: 0,
        scrollY: 1000,
        scrollX: 0,
      },
      correctionCount: 2,
      maxCorrections: 2,
    }),
    null,
  );
});

test("user interaction cancels pending restore", () => {
  assert.equal(isUserRestoreCancellationEvent({ type: "touchstart" }), true);
  assert.equal(isUserRestoreCancellationEvent({ type: "pointerdown" }), true);
  assert.equal(isUserRestoreCancellationEvent({ type: "keydown", key: "PageDown" }), true);
  assert.equal(
    isRestoreAttemptStale({
      scheduledToken: 3,
      activeToken: 3,
      scheduledUserIntentVersion: 1,
      currentUserIntentVersion: 2,
    }),
    true,
  );
});

test("layout-only scroll does not cancel restore", () => {
  assert.equal(isUserRestoreCancellationEvent({ type: "scroll" }), false);
  assert.equal(
    isRestoreAttemptStale({
      scheduledToken: 5,
      activeToken: 5,
      scheduledUserIntentVersion: 2,
      currentUserIntentVersion: 2,
    }),
    false,
  );
});
