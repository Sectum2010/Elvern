import { describe, expect, test } from "vitest";

import {
  deriveVideoFitModeFromPinch,
  measureTouchDistance,
  normalizeVideoFitMode,
} from "./playerFitMode.js";

describe("player fit mode helpers", () => {
  test("normalizes unknown values to fit", () => {
    expect(normalizeVideoFitMode("fill")).toBe("fill");
    expect(normalizeVideoFitMode("fit")).toBe("fit");
    expect(normalizeVideoFitMode("cover")).toBe("fit");
  });

  test("measures two-touch distance", () => {
    expect(measureTouchDistance([
      { clientX: 0, clientY: 0 },
      { clientX: 3, clientY: 4 },
    ])).toBe(5);
  });

  test("pinch outward selects fill", () => {
    expect(deriveVideoFitModeFromPinch({
      startDistance: 100,
      currentDistance: 140,
      currentMode: "fit",
    })).toBe("fill");
  });

  test("pinch inward selects fit", () => {
    expect(deriveVideoFitModeFromPinch({
      startDistance: 140,
      currentDistance: 100,
      currentMode: "fill",
    })).toBe("fit");
  });

  test("small pinch movement keeps current mode", () => {
    expect(deriveVideoFitModeFromPinch({
      startDistance: 100,
      currentDistance: 112,
      currentMode: "fill",
    })).toBe("fill");
  });
});
