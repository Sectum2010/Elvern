import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  createIOSViewportCoordinator,
  IOS_POST_KEYBOARD_QUARANTINE_MS,
  IOS_VIEWPORT_COORDINATOR_API_KEY,
  IOS_VIEWPORT_RESET_CONTENT,
  IOS_VIEWPORT_SUSPICIOUS_SHRINK_MIN_PX,
  IOS_VIEWPORT_SUSPICIOUS_SHRINK_RATIO,
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
    await vi.advanceTimersByTimeAsync(950);

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

  test("exports bounded quarantine and suspicious-shrink thresholds", () => {
    expect(IOS_POST_KEYBOARD_QUARANTINE_MS).toBeGreaterThanOrEqual(600);
    expect(IOS_POST_KEYBOARD_QUARANTINE_MS).toBeLessThanOrEqual(800);
    expect(IOS_VIEWPORT_SUSPICIOUS_SHRINK_MIN_PX).toBe(64);
    expect(IOS_VIEWPORT_SUSPICIOUS_SHRINK_RATIO).toBe(0.08);
  });

  test("does not accept a repeated keyboard-shrunken layout height after blur", async () => {
    const visualViewport = installViewport();
    const coordinator = createIOSViewportCoordinator({
      windowObject: window,
      documentObject: document,
      platform: "iphone",
      postKeyboardQuarantineMs: 100,
      settleTimeoutMs: 240,
    });
    coordinator.start();
    await vi.advanceTimersByTimeAsync(40);
    const trustedHeight = coordinator.getSnapshot().stableViewport.height;

    const input = document.querySelector("input");
    input.focus();
    visualViewport.update({ height: 480, scale: 1.2, offsetTop: 18 });
    await vi.advanceTimersByTimeAsync(20);
    input.blur();
    Object.defineProperty(document.documentElement, "clientHeight", { configurable: true, value: 600 });
    visualViewport.update({ height: 600, scale: 1, offsetTop: 0 });

    await vi.advanceTimersByTimeAsync(180);
    expect(coordinator.getSnapshot()).toMatchObject({
      restoreGateOpen: false,
      suspiciousShrink: true,
    });
    expect(coordinator.getSnapshot().stableViewport.height).toBe(trustedHeight);

    await vi.advanceTimersByTimeAsync(100);
    expect(coordinator.getSnapshot()).toMatchObject({
      restoreGateOpen: true,
      stableViewport: expect.objectContaining({ height: trustedHeight }),
    });
  });

  test("post-keyboard quarantine blocks stable acceptance until scale and offsets settle", async () => {
    const visualViewport = installViewport();
    const coordinator = createIOSViewportCoordinator({
      windowObject: window,
      documentObject: document,
      platform: "iphone",
      postKeyboardQuarantineMs: 100,
      settleTimeoutMs: 400,
    });
    coordinator.start();
    await vi.advanceTimersByTimeAsync(40);

    const input = document.querySelector("input");
    input.focus();
    visualViewport.update({ height: 500, scale: 1.15, offsetTop: 16 });
    input.blur();
    await vi.advanceTimersByTimeAsync(99);
    expect(coordinator.getSnapshot()).toMatchObject({
      postKeyboardQuarantine: true,
      restoreGateOpen: false,
    });

    visualViewport.update({ height: 844, scale: 1, offsetTop: 0 });
    await vi.advanceTimersByTimeAsync(80);
    expect(coordinator.getSnapshot()).toMatchObject({
      postKeyboardQuarantine: false,
      restoreGateOpen: true,
      state: "stable",
    });
  });

  test("paint floor remains at the trusted height while live and layout viewports are contaminated", async () => {
    const visualViewport = installViewport();
    const coordinator = createIOSViewportCoordinator({
      windowObject: window,
      documentObject: document,
      platform: "ipad",
    });
    coordinator.start();
    await vi.advanceTimersByTimeAsync(40);
    const trustedHeight = coordinator.getSnapshot().stableViewport.height;

    const input = document.querySelector("input");
    input.focus();
    Object.defineProperty(document.documentElement, "clientHeight", { configurable: true, value: 620 });
    visualViewport.update({ height: 580, scale: 1.1, offsetTop: 20 });
    await vi.advanceTimersByTimeAsync(20);

    expect(document.documentElement.style.getPropertyValue("--app-paint-viewport-height"))
      .toBe(`${trustedHeight}px`);
  });

  test("coalesces duplicate auth-exit requests for one focus interaction", async () => {
    const visualViewport = installViewport();
    const coordinator = createIOSViewportCoordinator({
      windowObject: window,
      documentObject: document,
      platform: "iphone",
      postKeyboardQuarantineMs: 20,
      authExitTimeoutMs: 400,
    });
    coordinator.start();
    const input = document.querySelector("input");
    input.focus();
    visualViewport.update({ height: 500, scale: 1.2, offsetTop: 10 });

    const first = coordinator.settleAuthExit();
    const second = coordinator.settleAuthExit();
    expect(second).toBe(first);

    visualViewport.update({ height: 844, scale: 1, offsetTop: 0 });
    await vi.advanceTimersByTimeAsync(450);
    await expect(first).resolves.toBe(true);
    expect(coordinator.getSnapshot().resetGeneration).toBe(1);
  });

  test("keeps independent trusted portrait and landscape viewport heights", async () => {
    const visualViewport = installViewport();
    Object.defineProperty(window, "orientation", { configurable: true, value: 0 });
    const coordinator = createIOSViewportCoordinator({
      windowObject: window,
      documentObject: document,
      platform: "ipad",
    });
    coordinator.start();
    await vi.advanceTimersByTimeAsync(40);

    Object.defineProperty(window, "orientation", { configurable: true, value: 90 });
    Object.defineProperty(document.documentElement, "clientWidth", { configurable: true, value: 844 });
    Object.defineProperty(document.documentElement, "clientHeight", { configurable: true, value: 390 });
    visualViewport.update({ width: 844, height: 390, scale: 1, offsetTop: 0 });
    window.dispatchEvent(new Event("orientationchange"));
    await vi.advanceTimersByTimeAsync(180);
    expect(coordinator.getSnapshot().stableViewport.height).toBe(390);

    Object.defineProperty(window, "orientation", { configurable: true, value: 0 });
    Object.defineProperty(document.documentElement, "clientWidth", { configurable: true, value: 390 });
    Object.defineProperty(document.documentElement, "clientHeight", { configurable: true, value: 844 });
    visualViewport.update({ width: 390, height: 844, scale: 1, offsetTop: 0 });
    window.dispatchEvent(new Event("orientationchange"));
    await vi.advanceTimersByTimeAsync(180);
    expect(coordinator.getSnapshot().stableViewport.height).toBe(844);
  });

  test("standalone PWA auth contamination cannot shorten a same-orientation trusted height", async () => {
    vi.stubGlobal("matchMedia", vi.fn((query) => ({ matches: query === "(display-mode: standalone)" })));
    const visualViewport = installViewport();
    const coordinator = createIOSViewportCoordinator({
      windowObject: window,
      documentObject: document,
      platform: "iphone",
      postKeyboardQuarantineMs: 20,
      settleTimeoutMs: 120,
    });
    coordinator.start();
    await vi.advanceTimersByTimeAsync(40);

    const input = document.querySelector("input");
    input.focus();
    visualViewport.update({ height: 500, scale: 1.15, offsetTop: 12 });
    input.blur();
    Object.defineProperty(document.documentElement, "clientHeight", { configurable: true, value: 810 });
    visualViewport.update({ height: 810, scale: 1, offsetTop: 0 });
    await vi.advanceTimersByTimeAsync(180);

    expect(coordinator.getSnapshot()).toMatchObject({
      restoreGateOpen: true,
      stableViewport: expect.objectContaining({ height: 844 }),
    });
    vi.unstubAllGlobals();
  });

  test("Safari can accept a legitimate stable browser-chrome height change outside Auth contamination", async () => {
    const visualViewport = installViewport();
    const coordinator = createIOSViewportCoordinator({
      windowObject: window,
      documentObject: document,
      platform: "iphone",
    });
    coordinator.start();
    await vi.advanceTimersByTimeAsync(40);

    Object.defineProperty(document.documentElement, "clientHeight", { configurable: true, value: 780 });
    visualViewport.update({ height: 780, scale: 1, offsetTop: 0 });
    await vi.advanceTimersByTimeAsync(80);

    expect(coordinator.getSnapshot()).toMatchObject({
      restoreGateOpen: true,
      stableViewport: expect.objectContaining({ height: 780 }),
    });
  });
});
