import { describe, expect, test } from "vitest";

import {
  deriveVideoFitModeFromPinch,
  measureTouchDistance,
  normalizeVideoFitMode,
  readStoredVideoFitMode,
} from "./playerFitMode.js";

describe("player fit mode helpers", () => {
  test("normalizes unknown values to default-fit", () => {
    expect(normalizeVideoFitMode("fill-cover")).toBe("fill-cover");
    expect(normalizeVideoFitMode("fill")).toBe("fill-cover");
    expect(normalizeVideoFitMode("default-fit")).toBe("default-fit");
    expect(normalizeVideoFitMode("fit")).toBe("default-fit");
    expect(normalizeVideoFitMode("cover")).toBe("default-fit");
  });

  test("new player sessions default to default-fit even when old storage says fill", () => {
    const storage = {
      getItem: () => "fill",
    };

    expect(readStoredVideoFitMode(storage)).toBe("default-fit");
  });

  test("measures two-touch distance", () => {
    expect(measureTouchDistance([
      { clientX: 0, clientY: 0 },
      { clientX: 3, clientY: 4 },
    ])).toBe(5);
  });

  test("pinch outward selects fill-cover", () => {
    expect(deriveVideoFitModeFromPinch({
      startDistance: 100,
      currentDistance: 140,
      currentMode: "default-fit",
    })).toBe("fill-cover");
  });

  test("pinch inward selects default-fit", () => {
    expect(deriveVideoFitModeFromPinch({
      startDistance: 140,
      currentDistance: 100,
      currentMode: "fill-cover",
    })).toBe("default-fit");
  });

  test("small pinch movement keeps current mode", () => {
    expect(deriveVideoFitModeFromPinch({
      startDistance: 100,
      currentDistance: 112,
      currentMode: "fill-cover",
    })).toBe("fill-cover");
  });
});
