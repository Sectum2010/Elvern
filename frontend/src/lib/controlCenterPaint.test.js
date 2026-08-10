import { afterEach, beforeEach, describe, expect, test } from "vitest";
import {
  applyControlCenterPaint,
  CONTROL_CENTER_PAINT_CLASS,
} from "./controlCenterPaint.js";


describe("controlCenterPaint", () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="elvern-app-paint-floor"></div><div id="root"></div>';
  });

  afterEach(() => {
    applyControlCenterPaint({ active: false, documentObject: document });
    document.body.innerHTML = "";
  });

  test("synchronizes the pre-React paint across every viewport background layer", () => {
    applyControlCenterPaint({ active: true, documentObject: document, theme: "light" });

    const targets = [
      document.documentElement,
      document.body,
      document.getElementById("root"),
      document.getElementById("elvern-app-paint-floor"),
    ];
    for (const target of targets) {
      expect(target).toHaveClass(CONTROL_CENTER_PAINT_CLASS);
      expect(target.dataset.elvernControlCenterTheme).toBe("light");
      expect(target.style.getPropertyValue("--elvern-control-center-paint")).toBe("#f3e9d8");
    }
  });

  test("uses the safe light paint for an unknown theme", () => {
    applyControlCenterPaint({ active: true, documentObject: document, theme: "unknown" });

    expect(document.body.dataset.elvernControlCenterTheme).toBe("light");
    expect(document.body.style.getPropertyValue("--elvern-control-center-paint")).toBe("#f3e9d8");
  });

  test("removes all route-scoped paint state when leaving the Control Center", () => {
    applyControlCenterPaint({ active: true, documentObject: document, theme: "dark" });
    applyControlCenterPaint({ active: false, documentObject: document });

    for (const target of [
      document.documentElement,
      document.body,
      document.getElementById("root"),
      document.getElementById("elvern-app-paint-floor"),
    ]) {
      expect(target).not.toHaveClass(CONTROL_CENTER_PAINT_CLASS);
      expect(target.dataset.elvernControlCenterTheme).toBeUndefined();
      expect(target.style.getPropertyValue("--elvern-control-center-paint")).toBe("");
    }
  });
});
