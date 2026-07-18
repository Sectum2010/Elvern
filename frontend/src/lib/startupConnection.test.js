import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  CONNECTIVITY_BACKEND_UNREACHABLE,
  CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE,
  CONNECTIVITY_EVIDENCE_INSUFFICIENT,
  CONNECTIVITY_HEALTHY,
  CONNECTIVITY_INTERNET_OFFLINE,
  createStartupConnectionController,
  DESKTOP_CONNECTIVITY_WATCHDOG_INTERVAL_MS,
  STARTUP_HEALTH_PROBE_INTERVAL_MS,
  STARTUP_UNREACHABLE_DELAY_MS,
} from "./startupConnection.js";


describe("startup connection controller", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  test("starts connecting and never shows unreachable before 60 seconds", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("offline"));
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(controller.getSnapshot().status).toBe("connecting");

    await vi.advanceTimersByTimeAsync(STARTUP_UNREACHABLE_DELAY_MS - 1);
    expect(controller.getSnapshot().status).toBe("connecting");

    await vi.advanceTimersByTimeAsync(1);
    expect(controller.getSnapshot().status).toBe("unreachable");
    controller.stop();
  });

  test("inherits elapsed cold-start time from the static bootstrap shell", async () => {
    vi.setSystemTime(new Date("2026-07-17T00:00:30Z"));
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("offline"));
    const controller = createStartupConnectionController({
      fetchImpl,
      initialOutageStartedAt: new Date("2026-07-17T00:00:00Z").getTime(),
      publicConnectivityProbes: [],
    });

    controller.start();
    await vi.advanceTimersByTimeAsync(STARTUP_UNREACHABLE_DELAY_MS / 2 - 1);
    expect(controller.getSnapshot().status).toBe("connecting");

    await vi.advanceTimersByTimeAsync(1);
    expect(controller.getSnapshot().status).toBe("unreachable");
    controller.stop();
  });

  test("probes every 10 seconds without overlapping requests", async () => {
    let resolveProbe;
    const fetchImpl = vi.fn(() => new Promise((resolve) => {
      resolveProbe = resolve;
    }));
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });

    controller.start();
    await vi.advanceTimersByTimeAsync(STARTUP_HEALTH_PROBE_INTERVAL_MS * 3);
    expect(fetchImpl).toHaveBeenCalledTimes(1);

    resolveProbe(new Response("ok", { status: 200 }));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(STARTUP_HEALTH_PROBE_INTERVAL_MS);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    resolveProbe(new Response("ok", { status: 200 }));
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(STARTUP_HEALTH_PROBE_INTERVAL_MS);
    expect(fetchImpl).toHaveBeenCalledTimes(3);
    controller.stop();
  });

  test("a successful health response enters connected immediately", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response("ok", { status: 200 }));
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);

    expect(controller.getSnapshot().status).toBe("connected");
    expect(controller.getSnapshot().classification).toBe(CONNECTIVITY_HEALTHY);
    expect(fetchImpl).toHaveBeenCalledWith(
      "/_elvern/frontend-health",
      expect.objectContaining({ cache: "no-store", signal: expect.any(AbortSignal) }),
    );
    expect(fetchImpl).toHaveBeenCalledWith(
      "/health",
      expect.objectContaining({ cache: "no-store", signal: expect.any(AbortSignal) }),
    );
    controller.stop();
  });

  test("stops recovery polling after the application becomes healthy on non-desktop", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response("ok", { status: 200 }));
    const controller = createStartupConnectionController({ fetchImpl, platform: "iphone", publicConnectivityProbes: [] });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    const callsAfterStartup = fetchImpl.mock.calls.length;

    await vi.advanceTimersByTimeAsync(STARTUP_HEALTH_PROBE_INTERVAL_MS * 4);
    expect(fetchImpl).toHaveBeenCalledTimes(callsAfterStartup);
    controller.stop();
  });

  test("classifies frontend-up backend-down without claiming VPN certainty", async () => {
    const fetchImpl = vi.fn((path) => Promise.resolve(new Response(
      path === "/_elvern/frontend-health" ? null : "unavailable",
      { status: path === "/_elvern/frontend-health" ? 204 : 503 },
    )));
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);

    expect(controller.getSnapshot().classification).toBe(CONNECTIVITY_BACKEND_UNREACHABLE);
    controller.stop();
  });

  test("classifies an unreachable frontend without public evidence as insufficient evidence", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("unreachable"));
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);

    expect(controller.getSnapshot().classification).toBe(CONNECTIVITY_EVIDENCE_INSUFFICIENT);
    controller.stop();
  });

  test("classifies frontend failure with public success as VPN/origin unreachable", async () => {
    const fetchImpl = vi.fn((path) => {
      if (path === "https://probe.operator.example/connectivity") {
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      return Promise.reject(new TypeError("frontend unavailable"));
    });
    const controller = createStartupConnectionController({
      fetchImpl,
      publicConnectivityProbeUrl: "https://probe.operator.example/connectivity",
    });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);

    expect(controller.getSnapshot().classification).toBe(CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE);
    controller.stop();
  });

  test("requires two public probe failures before classifying frontend failure as offline", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("unavailable"));
    const controller = createStartupConnectionController({
      fetchImpl,
      publicConnectivityProbeUrl: "https://probe.operator.example/connectivity",
      publicProbeConfirmationDelayMs: 25,
    });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(controller.getSnapshot().classification).not.toBe(CONNECTIVITY_INTERNET_OFFLINE);

    await vi.advanceTimersByTimeAsync(24);
    expect(controller.getSnapshot().classification).not.toBe(CONNECTIVITY_INTERNET_OFFLINE);

    await vi.advanceTimersByTimeAsync(1);
    expect(controller.getSnapshot().classification).toBe(CONNECTIVITY_INTERNET_OFFLINE);
    expect(fetchImpl.mock.calls.filter(([path]) => path === "https://probe.operator.example/connectivity"))
      .toHaveLength(2);
    controller.stop();
  });

  test("runs a non-overlapping healthy watchdog every 8 seconds on desktop only", async () => {
    let releaseWatchdog;
    const fetchImpl = vi.fn((path) => {
      if (fetchImpl.mock.calls.length > 2 && path === "/_elvern/frontend-health") {
        return new Promise((resolve) => {
          releaseWatchdog = resolve;
        });
      }
      return Promise.resolve(new Response(null, { status: 204 }));
    });
    const controller = createStartupConnectionController({
      fetchImpl,
      platform: "windows",
      publicConnectivityProbes: [],
    });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    const startupCalls = fetchImpl.mock.calls.length;

    await vi.advanceTimersByTimeAsync(DESKTOP_CONNECTIVITY_WATCHDOG_INTERVAL_MS * 3);
    expect(fetchImpl).toHaveBeenCalledTimes(startupCalls + 1);

    releaseWatchdog(new Response(null, { status: 204 }));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchImpl).toHaveBeenCalledTimes(startupCalls + 2);
    controller.stop();
  });

  test("does not run the healthy watchdog on phone or tablet platforms", async () => {
    for (const platform of ["iphone", "ipad", "android"]) {
      const fetchImpl = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
      const controller = createStartupConnectionController({ fetchImpl, platform, publicConnectivityProbes: [] });
      controller.start();
      await vi.advanceTimersByTimeAsync(0);
      const startupCalls = fetchImpl.mock.calls.length;
      await vi.advanceTimersByTimeAsync(DESKTOP_CONNECTIVITY_WATCHDOG_INTERVAL_MS * 2);
      expect(fetchImpl).toHaveBeenCalledTimes(startupCalls);
      controller.stop();
    }
  });

  test("browser offline is classified immediately and shows Oops after 60 seconds", async () => {
    const navigatorObject = { onLine: false };
    const fetchImpl = vi.fn();
    const controller = createStartupConnectionController({ fetchImpl, navigatorObject, publicConnectivityProbes: [] });

    controller.start();
    await vi.advanceTimersByTimeAsync(STARTUP_UNREACHABLE_DELAY_MS - 1);

    expect(controller.getSnapshot().classification).toBe(CONNECTIVITY_INTERNET_OFFLINE);
    expect(controller.getSnapshot().status).toBe("connecting");
    expect(fetchImpl.mock.calls.every(([path]) => path === "/_elvern/frontend-health")).toBe(true);

    await vi.advanceTimersByTimeAsync(1);
    expect(controller.getSnapshot().status).toBe("unreachable");
    controller.stop();
  });

  test("runtime failures never clear the permanent ready latch", async () => {
    let healthy = true;
    const fetchImpl = vi.fn((path) => {
      if (healthy) {
        return Promise.resolve(new Response("ok", { status: 200 }));
      }
      return path === "/_elvern/frontend-health"
        ? Promise.resolve(new Response(null, { status: 204 }))
        : Promise.reject(new TypeError("backend unavailable"));
    });
    const controller = createStartupConnectionController({
      fetchImpl,
      requireApplicationReady: true,
      publicConnectivityProbes: [],
    });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    controller.reportApplicationReady();
    expect(controller.getSnapshot().runtimeReady).toBe(true);

    healthy = false;
    await controller.reportFailure();
    expect(controller.getSnapshot().runtimeReady).toBe(true);
    expect(controller.getSnapshot().classification).toBe(CONNECTIVITY_BACKEND_UNREACHABLE);

    await vi.advanceTimersByTimeAsync(STARTUP_UNREACHABLE_DELAY_MS);
    expect(controller.getSnapshot().status).toBe("unreachable");
    expect(controller.getSnapshot().runtimeReady).toBe(true);
    controller.stop();
  });

  test("runtime true-offline state never advances to unreachable", async () => {
    const navigatorObject = { onLine: true };
    const fetchImpl = vi.fn().mockResolvedValue(new Response("ok", { status: 200 }));
    const controller = createStartupConnectionController({ fetchImpl, navigatorObject, publicConnectivityProbes: [] });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(controller.getSnapshot().runtimeReady).toBe(true);

    navigatorObject.onLine = false;
    window.dispatchEvent(new Event("offline"));
    await vi.advanceTimersByTimeAsync(STARTUP_UNREACHABLE_DELAY_MS * 2);

    expect(controller.getSnapshot()).toMatchObject({
      classification: CONNECTIVITY_INTERNET_OFFLINE,
      runtimeReady: true,
      status: "connected",
    });
    controller.stop();
  });

  test("an offline login failure uses the 60 second offline Oops path", async () => {
    const navigatorObject = { onLine: true };
    const fetchImpl = vi.fn().mockResolvedValue(new Response("ok", { status: 200 }));
    const controller = createStartupConnectionController({ fetchImpl, navigatorObject, publicConnectivityProbes: [] });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    navigatorObject.onLine = false;
    await controller.reportFailure({ forceOfflineOops: true });

    await vi.advanceTimersByTimeAsync(STARTUP_UNREACHABLE_DELAY_MS - 1);
    expect(controller.getSnapshot()).toMatchObject({
      classification: CONNECTIVITY_INTERNET_OFFLINE,
      offlineOopsRequired: true,
      runtimeReady: true,
      status: "connecting",
    });

    await vi.advanceTimersByTimeAsync(1);
    expect(controller.getSnapshot().status).toBe("unreachable");
    controller.stop();
  });

  test("an offline login failure confirmed by previously trusted public evidence also uses offline Oops", async () => {
    const navigatorObject = { onLine: true };
    const publicUrl = "https://probe.operator.example/connectivity";
    let publicReachable = true;
    const fetchImpl = vi.fn((path) => {
      if (path === publicUrl && publicReachable) {
        return Promise.resolve({ status: 204, ok: true });
      }
      return Promise.reject(new TypeError("unavailable"));
    });
    const controller = createStartupConnectionController({
      fetchImpl,
      navigatorObject,
      publicConnectivityProbeUrl: publicUrl,
      publicProbeConfirmationDelayMs: 10,
    });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    controller.reportApplicationReady();
    publicReachable = false;
    const failureProbe = controller.reportFailure({ forceOfflineOops: true });
    await vi.advanceTimersByTimeAsync(10);
    await failureProbe;

    expect(controller.getSnapshot()).toMatchObject({
      classification: CONNECTIVITY_INTERNET_OFFLINE,
      offlineOopsRequired: true,
      runtimeReady: true,
      status: "connecting",
    });
    await vi.advanceTimersByTimeAsync(STARTUP_UNREACHABLE_DELAY_MS);
    expect(controller.getSnapshot().status).toBe("unreachable");
    controller.stop();
  });

  test("hidden desktop pages stop the watchdog and visible pages probe immediately", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    const controller = createStartupConnectionController({
      fetchImpl,
      platform: "linux",
      publicConnectivityProbes: [],
    });
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    controller.start();
    await vi.advanceTimersByTimeAsync(0);

    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
    const callsWhileHidden = fetchImpl.mock.calls.length;
    await vi.advanceTimersByTimeAsync(DESKTOP_CONNECTIVITY_WATCHDOG_INTERVAL_MS * 2);
    expect(fetchImpl).toHaveBeenCalledTimes(callsWhileHidden);

    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchImpl).toHaveBeenCalledTimes(callsWhileHidden + 2);
    controller.stop();
  });

  test("application readiness is required without resetting the original 60 second deadline", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response("ok", { status: 200 }));
    const controller = createStartupConnectionController({
      fetchImpl,
      requireApplicationReady: true,
      publicConnectivityProbes: [],
    });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(controller.getSnapshot()).toMatchObject({
      status: "connecting",
      serviceReachable: true,
    });

    await vi.advanceTimersByTimeAsync(STARTUP_UNREACHABLE_DELAY_MS - 1);
    expect(controller.getSnapshot().status).toBe("connecting");

    await vi.advanceTimersByTimeAsync(1);
    expect(controller.getSnapshot()).toMatchObject({
      status: "unreachable",
      serviceReachable: true,
    });
    controller.stop();
  });

  test("an application response completes startup after health permits the app to mount", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response("ok", { status: 200 }));
    const controller = createStartupConnectionController({
      fetchImpl,
      requireApplicationReady: true,
      publicConnectivityProbes: [],
    });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(controller.getSnapshot().status).toBe("connecting");

    controller.reportApplicationReady();
    expect(controller.getSnapshot()).toMatchObject({
      status: "connected",
      serviceReachable: true,
    });
    controller.stop();
  });

  test("retry probes immediately without clearing the current document Oops latch", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("offline"));
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });
    controller.start();
    await vi.advanceTimersByTimeAsync(STARTUP_UNREACHABLE_DELAY_MS);
    expect(controller.getSnapshot().status).toBe("unreachable");

    const callsBeforeRetry = fetchImpl.mock.calls.length;
    await controller.retry();
    expect(fetchImpl).toHaveBeenCalledTimes(callsBeforeRetry + 1);
    expect(controller.getSnapshot().status).toBe("unreachable");
    controller.stop();
  });

  test("online and visible events request an immediate probe", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("offline"));
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    controller.start();
    await vi.advanceTimersByTimeAsync(0);

    window.dispatchEvent(new Event("online"));
    await vi.advanceTimersByTimeAsync(0);
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(0);

    expect(fetchImpl).toHaveBeenCalledTimes(3);
    expect(controller.getSnapshot().status).toBe("connecting");
    controller.stop();
  });

  test("stop removes event listeners and aborts pending work", async () => {
    const fetchImpl = vi.fn(() => new Promise(() => {}));
    const removeWindowListener = vi.spyOn(window, "removeEventListener");
    const removeDocumentListener = vi.spyOn(document, "removeEventListener");
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });
    controller.start();
    controller.stop();

    expect(removeWindowListener).toHaveBeenCalledWith("online", expect.any(Function));
    expect(removeWindowListener).toHaveBeenCalledWith("offline", expect.any(Function));
    expect(removeDocumentListener).toHaveBeenCalledWith("visibilitychange", expect.any(Function));
  });

  test("a stopped probe cannot clear a newly restarted probe", async () => {
    let requestCount = 0;
    const fetchImpl = vi.fn((_path, options) => {
      requestCount += 1;
      if (requestCount === 1) {
        return new Promise((_resolve, reject) => {
          options.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        });
      }
      return Promise.resolve(new Response("ok", { status: 200 }));
    });
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });

    controller.start();
    controller.stop();
    controller.start();
    await vi.advanceTimersByTimeAsync(0);

    expect(fetchImpl.mock.calls.map(([path]) => path)).toEqual([
      "/_elvern/frontend-health",
      "/_elvern/frontend-health",
      "/health",
    ]);
    expect(controller.getSnapshot()).toMatchObject({
      status: "connected",
      classification: CONNECTIVITY_HEALTHY,
    });
    controller.stop();
  });

  test("same-host frontend and backend success cannot clear a trusted public outage latch", async () => {
    const publicUrl = "https://probe.operator.example/connectivity";
    let publicReachable = true;
    const fetchImpl = vi.fn((path) => {
      if (path === publicUrl) {
        return publicReachable
          ? Promise.resolve({ status: 204, ok: true })
          : Promise.reject(new TypeError("public Internet unavailable"));
      }
      return Promise.resolve(new Response(null, { status: 204 }));
    });
    const controller = createStartupConnectionController({
      fetchImpl,
      platform: "linux",
      publicConnectivityProbeUrl: publicUrl,
      publicProbeConfirmationDelayMs: 0,
    });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(controller.getSnapshot()).toMatchObject({
      internetState: "online",
      internetOutageLatched: false,
      frontendState: "reachable",
      backendState: "reachable",
    });

    publicReachable = false;
    await vi.advanceTimersByTimeAsync(DESKTOP_CONNECTIVITY_WATCHDOG_INTERVAL_MS);
    expect(controller.getSnapshot()).toMatchObject({
      internetState: "offline",
      internetOutageLatched: true,
      frontendState: "reachable",
      backendState: "reachable",
      runtimeReady: true,
    });

    await vi.advanceTimersByTimeAsync(10 * 60 * 1000);
    expect(controller.getSnapshot().internetOutageLatched).toBe(true);
    controller.stop();
  });

  test("navigator online only triggers evidence collection and cannot clear the public outage latch", async () => {
    const publicUrl = "https://probe.operator.example/connectivity";
    const navigatorObject = { onLine: true };
    let publicReachable = true;
    const fetchImpl = vi.fn((path) => {
      if (path === publicUrl) {
        return publicReachable
          ? Promise.resolve({ status: 204, ok: true })
          : Promise.reject(new TypeError("public Internet unavailable"));
      }
      return Promise.resolve(new Response(null, { status: 204 }));
    });
    const controller = createStartupConnectionController({
      fetchImpl,
      navigatorObject,
      platform: "linux",
      publicConnectivityProbeUrl: publicUrl,
      publicProbeConfirmationDelayMs: 0,
    });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    publicReachable = false;
    await vi.advanceTimersByTimeAsync(DESKTOP_CONNECTIVITY_WATCHDOG_INTERVAL_MS);
    expect(controller.getSnapshot().internetOutageLatched).toBe(true);

    window.dispatchEvent(new Event("online"));
    await vi.advanceTimersByTimeAsync(0);
    expect(controller.getSnapshot().internetOutageLatched).toBe(true);

    publicReachable = true;
    window.dispatchEvent(new Event("online"));
    await vi.advanceTimersByTimeAsync(0);
    expect(controller.getSnapshot()).toMatchObject({
      internetState: "online",
      internetOutageLatched: false,
    });
    controller.stop();
  });

  test("unverified public failures plus an unreachable frontend use the generic evidence-insufficient state", async () => {
    const controller = createStartupConnectionController({
      fetchImpl: vi.fn().mockRejectedValue(new TypeError("unreachable")),
      publicConnectivityProbeUrl: "https://probe.operator.example/connectivity",
      publicProbeConfirmationDelayMs: 0,
    });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);

    expect(controller.getSnapshot()).toMatchObject({
      internetState: "unknown",
      publicProbeTrusted: false,
      frontendState: "unreachable",
      backendState: "unknown",
      classification: CONNECTIVITY_EVIDENCE_INSUFFICIENT,
    });
    controller.stop();
  });
});
