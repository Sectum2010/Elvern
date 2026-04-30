import assert from "node:assert/strict";
import test from "node:test";

import { resolveSeriesRailViewportKind } from "./seriesRailViewport.js";

test("phone portrait uses phone portrait rail behavior", () => {
  assert.equal(
    resolveSeriesRailViewportKind({
      deviceClass: "phone",
      isLandscape: false,
    }),
    "phone-portrait",
  );
});

test("phone landscape uses phone landscape rail behavior", () => {
  assert.equal(
    resolveSeriesRailViewportKind({
      deviceClass: "phone",
      isLandscape: true,
    }),
    "phone-landscape",
  );
});

test("tablet and desktop keep existing desktop rail behavior", () => {
  assert.equal(
    resolveSeriesRailViewportKind({
      deviceClass: "tablet",
      isLandscape: false,
    }),
    "desktop",
  );
  assert.equal(
    resolveSeriesRailViewportKind({
      deviceClass: "desktop",
      isLandscape: true,
    }),
    "desktop",
  );
});

test("missing device class keeps existing desktop rail behavior", () => {
  assert.equal(resolveSeriesRailViewportKind(), "desktop");
});
