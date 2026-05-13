import { describe, expect, test } from "vitest";

import {
  deriveVideoFitModeGestureChange,
  deriveVideoFitModeFromPinch,
  measureTouchDistance,
  normalizeVideoFitMode,
  readStoredVideoFitMode,
} from "./playerFitMode.js";

describe("player fit mode helpers", () => {
  test("normalizes unknown values to standard-fit", () => {
    expect(normalizeVideoFitMode("zoom-fill")).toBe("zoom-fill");
    expect(normalizeVideoFitMode("fill-cover")).toBe("zoom-fill");
    expect(normalizeVideoFitMode("fill")).toBe("zoom-fill");
    expect(normalizeVideoFitMode("standard-fit")).toBe("standard-fit");
    expect(normalizeVideoFitMode("default-fit")).toBe("standard-fit");
    expect(normalizeVideoFitMode("fit")).toBe("standard-fit");
    expect(normalizeVideoFitMode("cover")).toBe("standard-fit");
  });

  test("new player sessions default to standard-fit even when old storage says fill", () => {
    const storage = {
      getItem: () => "fill",
    };

    expect(readStoredVideoFitMode(storage)).toBe("standard-fit");
  });

  test("measures two-touch distance", () => {
    expect(measureTouchDistance([
      { clientX: 0, clientY: 0 },
      { clientX: 3, clientY: 4 },
    ])).toBe(5);
  });

  test("pinch outward selects zoom-fill", () => {
    expect(deriveVideoFitModeFromPinch({
      startDistance: 100,
      currentDistance: 140,
      currentMode: "standard-fit",
    })).toBe("zoom-fill");
  });

  test("pinch inward selects standard-fit", () => {
    expect(deriveVideoFitModeFromPinch({
      startDistance: 140,
      currentDistance: 100,
      currentMode: "zoom-fill",
    })).toBe("standard-fit");
  });

  test("small pinch movement keeps current mode", () => {
    expect(deriveVideoFitModeFromPinch({
      startDistance: 100,
      currentDistance: 112,
      currentMode: "zoom-fill",
    })).toBe("zoom-fill");
  });

  test("one pinch gesture commits only one mode change", () => {
    const gesture = {
      startDistance: 100,
      startMode: "standard-fit",
      hasCommittedModeChange: false,
    };
    const first = deriveVideoFitModeGestureChange({
      gesture,
      currentDistance: 140,
    });
    expect(first).toEqual({
      changed: true,
      hasCommittedModeChange: true,
      nextMode: "zoom-fill",
    });
    const second = deriveVideoFitModeGestureChange({
      gesture: { ...gesture, hasCommittedModeChange: true },
      currentDistance: 70,
    });
    expect(second.changed).toBe(false);
    expect(second.hasCommittedModeChange).toBe(true);
    expect(second.nextMode).toBe("standard-fit");
  });
});
