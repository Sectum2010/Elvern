import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  createIOSViewportCoordinator,
  IOS_VIEWPORT_COORDINATOR_API_KEY,
  IOS_VIEWPORT_RESET_CONTENT,
} from "./iosViewportCoordinator.js";


class MockVisualViewport extends EventTarget {
  constructor({ width = 390, height = 844, scale = 1, offsetTop = 0, offsetLeft = 0 } = {}) {
    super();
    Object.assign(this, { width, height, scale, offsetTop, offsetLeft });
  }

  update(values) {
    Object.assign(this, values);
    this.dispatchEvent(new Event("resize"));
  }
}


function installViewport({ width = 390, height = 844, visualViewport = null } = {}) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
  Object.defineProperty(window, "innerHeight", { configurable: true, value: height });
  Object.defineProperty(document.documentElement, "clientWidth", { configurable: true, value: width });
  Object.defineProperty(document.documentElement, "clientHeight", { configurable: true, value: height });
  Object.defineProperty(window, "visualViewport", {
    configurable: true,
    value: visualViewport || new MockVisualViewport({ width, height }),
  });
  document.head.innerHTML = '<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover, shrink-to-fit=no">';
  document.body.innerHTML = '<main class="login-screen"><input data-auth-field type="text"></main>';
  return window.visualViewport;
}


describe("iOS viewport coordinator", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    window[IOS_VIEWPORT_COORDINATOR_API_KEY]?.stop?.();
    delete window[IOS_VIEWPORT_COORDINATOR_API_KEY];
    vi.useRealTimers();
    vi.restoreAllMocks();
    document.documentElement.removeAttribute("style");
    document.documentElement.removeAttribute("data-elvern-ios-viewport");
    document.documentElement.removeAttribute("data-elvern-keyboard-open");
  });

  test("keeps stable root height while the keyboard and focus autozoom shrink the live viewport", async () => {
    const visualViewport = installViewport();
    const coordinator = createIOSViewportCoordinator({
      windowObject: window,
      documentObject: document,
      platform: "iphone",
    });
    coordinator.start();
    await vi.advanceTimersByTimeAsync(40);
    const stableHeight = coordinator.getSnapshot().stableViewport.height;

    const input = document.querySelector("input");
    input.focus();
    visualViewport.update({ height: 500, scale: 1.2, offsetTop: 20 });
    await vi.advanceTimersByTimeAsync(20);

    expect(coordinator.getSnapshot()).toMatchObject({
      editableFocused: true,
      keyboardOpen: true,
      focusAutozoom: true,
      restoreGateOpen: false,
    });
    expect(coordinator.getSnapshot().stableViewport.height).toBe(stableHeight);
    expect(document.documentElement.style.getPropertyValue("--app-stable-viewport-height"))
      .toBe(`${stableHeight}px`);
    expect(document.documentElement.style.getPropertyValue("--app-live-viewport-height"))
      .toBe("500px");
  });

  test("does not open the restore gate when the settle fallback fires during editable focus", async () => {
    const visualViewport = installViewport();
    const coordinator = createIOSViewportCoordinator({
      windowObject: window,
      documentObject: document,
      platform: "iphone",
      settleTimeoutMs: 100,
    });
    coordinator.start();
    const input = document.querySelector("input");
    input.focus();
    visualViewport.update({ height: 500, scale: 1.2, offsetTop: 20 });

    await vi.advanceTimersByTimeAsync(150);

    expect(coordinator.getSnapshot()).toMatchObject({
      editableFocused: true,
      restoreGateOpen: false,
    });
  });

  test("recognizes iPad desktop-style UA and does not reset an unfocused user pinch", () => {
    const visualViewport = installViewport({ width: 1024, height: 1366 });
    const coordinator = createIOSViewportCoordinator({
      windowObject: window,
      documentObject: document,
      navigatorObject: {
        userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)",
        platform: "MacIntel",
        maxTouchPoints: 5,
      },
    });
    coordinator.start();
    visualViewport.update({ height: 900, scale: 1.35 });

    expect(coordinator.getSnapshot().isIOS).toBe(true);
    expect(coordinator.requestNormalization({ reason: "auth-exit" })).toBe(false);
    expect(document.querySelector('meta[name="viewport"]').content).not.toContain("maximum-scale");
  });

  test("auth exit blurs once, performs one scoped autozoom reset, and settles with a hard bound", async () => {
    const visualViewport = installViewport();
    const coordinator = createIOSViewportCoordinator({
      windowObject: window,
      documentObject: document,
      platform: "iphone",
      authExitTimeoutMs: 1_000,
    });
    coordinator.start();
    const input = document.querySelector("input");
    input.focus();
    visualViewport.update({ height: 480, scale: 1.25 });
    const settled = coordinator.settleAuthExit();

    expect(document.activeElement).not.toBe(input);
    visualViewport.update({ height: 844, scale: 1, offsetTop: 0 });
    await vi.advanceTimersByTimeAsync(350);

    await expect(settled).resolves.toBe(true);
    expect(coordinator.getSnapshot()).toMatchObject({
      resetGeneration: 1,
      restoreGateOpen: true,
      state: "stable",
    });
  });

  test("a newer reset generation prevents an old timer from restoring stale meta content", async () => {
    installViewport();
    const coordinator = createIOSViewportCoordinator({
      windowObject: window,
      documentObject: document,
      platform: "iphone",
    });
    coordinator.start();

    expect(coordinator.requestNormalization({ force: true, reason: "test" })).toBe(true);
    await vi.advanceTimersByTimeAsync(100);
    expect(coordinator.requestNormalization({ force: true, reason: "test" })).toBe(true);
    await vi.advanceTimersByTimeAsync(80);
    expect(document.querySelector('meta[name="viewport"]').content).toBe(IOS_VIEWPORT_RESET_CONTENT);
    await vi.advanceTimersByTimeAsync(100);
    expect(document.querySelector('meta[name="viewport"]').content).not.toContain("maximum-scale");
  });
});
