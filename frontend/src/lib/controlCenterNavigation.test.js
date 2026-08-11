import { describe, expect, test, vi } from "vitest";

import {
  CONTROL_CENTER_BEFORE_NAVIGATION_EVENT,
  requestControlCenterNavigation,
} from "./controlCenterNavigation.js";

describe("control center navigation guard", () => {
  test("allows navigation when no active editor blocks it", () => {
    expect(requestControlCenterNavigation("/admin/security", vi.fn())).toBe(true);
  });

  test("lets an active editor block navigation and retain a continuation", () => {
    const proceed = vi.fn();
    const listener = vi.fn((event) => event.preventDefault());
    window.addEventListener(CONTROL_CENTER_BEFORE_NAVIGATION_EVENT, listener);

    expect(requestControlCenterNavigation("/settings/library", proceed)).toBe(false);
    expect(listener).toHaveBeenCalledOnce();
    expect(listener.mock.calls[0][0].detail).toEqual({
      destination: "/settings/library",
      proceed,
    });

    window.removeEventListener(CONTROL_CENTER_BEFORE_NAVIGATION_EVENT, listener);
  });
});
