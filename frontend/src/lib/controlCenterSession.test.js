import { beforeEach, describe, expect, test, vi } from "vitest";

import {
  clearControlCenterSessionState,
  readControlCenterTab,
  readControlCenterTheme,
  writeControlCenterTab,
  writeControlCenterTheme,
} from "./controlCenterSession.js";

describe("Control Center session UI state", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  test("theme and independent tabs survive refresh-style reads in sessionStorage", () => {
    expect(writeControlCenterTheme("dark")).toBe(true);
    expect(writeControlCenterTab("settings", "cloud-sharing")).toBe(true);
    expect(writeControlCenterTab("admin", "logs")).toBe(true);
    expect(readControlCenterTheme()).toBe("dark");
    expect(readControlCenterTab("settings")).toBe("cloud-sharing");
    expect(readControlCenterTab("admin")).toBe("logs");
    expect(window.localStorage.getItem("elvern:control-center:theme")).toBeNull();
  });

  test("invalid values fall back safely", () => {
    window.sessionStorage.setItem("elvern:control-center:theme", "sepia");
    window.sessionStorage.setItem("elvern:control-center:settings-tab", "admin-secrets");
    expect(readControlCenterTheme()).toBe("light");
    expect(readControlCenterTab("settings")).toBe("appearance");
  });

  test("auth session reset clears theme and both tab memories", () => {
    writeControlCenterTheme("mixed");
    writeControlCenterTab("settings", "library");
    writeControlCenterTab("admin", "security");
    const listener = vi.fn();
    window.addEventListener("elvern:control-center-session-reset", listener);
    clearControlCenterSessionState();
    expect(readControlCenterTheme()).toBe("light");
    expect(readControlCenterTab("settings")).toBe("appearance");
    expect(readControlCenterTab("admin")).toBe("overview");
    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener("elvern:control-center-session-reset", listener);
  });
});
