import test from "node:test";
import assert from "node:assert/strict";

import {
  buildLibraryReturnState,
  clearLibraryReturnPending,
  extractLibraryReturnState,
  normalizeLibraryReturnTarget,
  readLibraryReturnTarget,
  rememberLibraryReturnTarget,
} from "./libraryNavigation.js";

function withSessionStorage(callback) {
  const storage = new Map();
  const previousWindow = global.window;
  global.window = {
    sessionStorage: {
      getItem(key) {
        return storage.has(key) ? storage.get(key) : null;
      },
      setItem(key, value) {
        storage.set(key, String(value));
      },
    },
  };
  try {
    return callback();
  } finally {
    global.window = previousWindow;
  }
}

test("library return state stores exact card instance and viewport ratios", () => {
  const state = buildLibraryReturnState({
    listPath: "/library/cloud",
    anchorItemId: "42",
    anchorInstanceKey: "series:dragon:42",
    scrollY: "1200",
    anchorViewportRatioY: "0.38",
    anchorViewportRatioX: "0.25",
    viewportWidth: "820",
    viewportHeight: "1180",
    railKey: "series:dragon",
    railScrollLeft: "144",
  });

  assert.deepEqual(state, {
    libraryReturn: {
      listPath: "/library/cloud",
      anchorItemId: 42,
      anchorInstanceKey: "series:dragon:42",
      scrollY: 1200,
      pendingRestore: false,
      anchorViewportRatioY: 0.38,
      anchorViewportRatioX: 0.25,
      viewportWidth: 820,
      viewportHeight: 1180,
      railKey: "series:dragon",
      railScrollLeft: 144,
    },
  });
});

test("library return state remains backward compatible with item-id-only payloads", () => {
  assert.deepEqual(
    normalizeLibraryReturnTarget({
      listPath: "/library",
      anchorItemId: 99,
      scrollY: 450,
      pendingRestore: true,
    }),
    {
      listPath: "/library",
      anchorItemId: 99,
      anchorInstanceKey: null,
      scrollY: 450,
      pendingRestore: true,
      anchorViewportRatioY: null,
      anchorViewportRatioX: null,
      viewportWidth: null,
      viewportHeight: null,
      railKey: null,
      railScrollLeft: null,
    },
  );
});

test("library return state normalizes invalid values safely", () => {
  assert.deepEqual(
    normalizeLibraryReturnTarget({
      listPath: "/not-library",
      anchorItemId: -1,
      anchorInstanceKey: "   ",
      scrollY: -20,
      anchorViewportRatioY: "nope",
      viewportWidth: 0,
      railScrollLeft: -5,
    }),
    {
      listPath: "/library",
      anchorItemId: null,
      anchorInstanceKey: null,
      scrollY: 0,
      pendingRestore: false,
      anchorViewportRatioY: null,
      anchorViewportRatioX: null,
      viewportWidth: null,
      viewportHeight: null,
      railKey: null,
      railScrollLeft: null,
    },
  );
});

test("session return target preserves enhanced exact-instance fields", () => withSessionStorage(() => {
  const remembered = rememberLibraryReturnTarget({
    listPath: "/library",
    anchorItemId: 12,
    anchorInstanceKey: "continue:12",
    scrollY: 700,
    anchorViewportRatioY: 0.42,
    railKey: "continue",
    railScrollLeft: 64,
  });

  assert.deepEqual(readLibraryReturnTarget(), remembered);
  assert.equal(clearLibraryReturnPending()?.pendingRestore, false);
}));

test("location state is enriched from matching session target", () => withSessionStorage(() => {
  rememberLibraryReturnTarget({
    listPath: "/library",
    anchorItemId: 12,
    anchorInstanceKey: "series:exact:12",
    scrollY: 900,
    anchorViewportRatioY: 0.31,
    railKey: "series:exact",
    railScrollLeft: 88,
  });

  const extracted = extractLibraryReturnState({
    libraryReturn: {
      listPath: "/library",
      anchorItemId: 12,
      scrollY: 300,
    },
  });

  assert.equal(extracted.anchorInstanceKey, "series:exact:12");
  assert.equal(extracted.anchorViewportRatioY, 0.31);
  assert.equal(extracted.railKey, "series:exact");
  assert.equal(extracted.railScrollLeft, 88);
}));
