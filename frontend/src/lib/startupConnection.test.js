import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
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
    expect(fetchImpl).toHaveBeenCalledWith(
      "/health",
      expect.objectContaining({ cache: "no-store", signal: expect.any(AbortSignal) }),
    );
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
    expect(controller.getSnapshot()).toEqual({
      status: "connecting",
      serviceReachable: true,
    });

    await vi.advanceTimersByTimeAsync(STARTUP_UNREACHABLE_DELAY_MS - 1);
    expect(controller.getSnapshot().status).toBe("connecting");

    await vi.advanceTimersByTimeAsync(1);
    expect(controller.getSnapshot()).toEqual({
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
    expect(controller.getSnapshot()).toEqual({
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
});
