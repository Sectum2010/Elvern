import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  CONNECTIVITY_BACKEND_UNREACHABLE,
  CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE,
  CONNECTIVITY_HEALTHY,
  CONNECTIVITY_INTERNET_OFFLINE,
  createStartupConnectionController,
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
    const controller = createStartupConnectionController({ fetchImpl });

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
    const controller = createStartupConnectionController({ fetchImpl });

    controller.start();
    await vi.advanceTimersByTimeAsync(STARTUP_HEALTH_PROBE_INTERVAL_MS * 3);
    expect(fetchImpl).toHaveBeenCalledTimes(1);

    resolveProbe(new Response("ok", { status: 200 }));
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(STARTUP_HEALTH_PROBE_INTERVAL_MS);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    controller.stop();
  });

  test("a successful health response enters connected immediately", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response("ok", { status: 200 }));
    const controller = createStartupConnectionController({ fetchImpl });

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

  test("stops fixed health polling after the application becomes healthy", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response("ok", { status: 200 }));
    const controller = createStartupConnectionController({ fetchImpl });

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
    const controller = createStartupConnectionController({ fetchImpl });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);

    expect(controller.getSnapshot().classification).toBe(CONNECTIVITY_BACKEND_UNREACHABLE);
    controller.stop();
  });

  test("classifies an unreachable frontend as frontend or VPN unreachable", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("unreachable"));
    const controller = createStartupConnectionController({ fetchImpl });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);

    expect(controller.getSnapshot().classification).toBe(CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE);
    controller.stop();
  });

  test("browser offline is classified immediately without advancing to Oops", async () => {
    const navigatorObject = { onLine: false };
    const fetchImpl = vi.fn();
    const controller = createStartupConnectionController({ fetchImpl, navigatorObject });

    controller.start();
    await vi.advanceTimersByTimeAsync(STARTUP_UNREACHABLE_DELAY_MS * 2);

    expect(controller.getSnapshot().classification).toBe(CONNECTIVITY_INTERNET_OFFLINE);
    expect(controller.getSnapshot().status).toBe("connecting");
    expect(fetchImpl).not.toHaveBeenCalled();
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
    const controller = createStartupConnectionController({ fetchImpl, requireApplicationReady: true });

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

  test("application readiness is required without resetting the original 60 second deadline", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response("ok", { status: 200 }));
    const controller = createStartupConnectionController({
      fetchImpl,
      requireApplicationReady: true,
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

  test("retry probes immediately and starts a new full connecting window", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("offline"));
    const controller = createStartupConnectionController({ fetchImpl });
    controller.start();
    await vi.advanceTimersByTimeAsync(STARTUP_UNREACHABLE_DELAY_MS);
    expect(controller.getSnapshot().status).toBe("unreachable");

    const callsBeforeRetry = fetchImpl.mock.calls.length;
    await controller.retry();
    expect(fetchImpl).toHaveBeenCalledTimes(callsBeforeRetry + 1);
    expect(controller.getSnapshot().status).toBe("connecting");

    await vi.advanceTimersByTimeAsync(STARTUP_UNREACHABLE_DELAY_MS - 1);
    expect(controller.getSnapshot().status).toBe("connecting");

    await vi.advanceTimersByTimeAsync(1);
    expect(controller.getSnapshot().status).toBe("unreachable");
    controller.stop();
  });

  test("online and visible events request an immediate probe", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("offline"));
    const controller = createStartupConnectionController({ fetchImpl });
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
    const controller = createStartupConnectionController({ fetchImpl });
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
    const controller = createStartupConnectionController({ fetchImpl });

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
});
