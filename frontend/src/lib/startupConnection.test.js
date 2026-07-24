import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  CONNECTIVITY_RECOVERED_EVENT,
  CONNECTIVITY_BACKEND_UNREACHABLE,
  CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE,
  CONNECTIVITY_EVIDENCE_INSUFFICIENT,
  CONNECTIVITY_HEALTHY,
  CONNECTIVITY_INTERNET_OFFLINE,
  classifyStartupHealthResponse,
  createStartupConnectionController,
  DESKTOP_CONNECTIVITY_WATCHDOG_INTERVAL_MS,
  FAST_OOPS_CONFIRMATION_DELAY_MS,
  STARTUP_HEALTH_PROBE_INTERVAL_MS,
  STARTUP_MANUAL_SERVICE_RECOVERY_STORAGE_KEY,
  STARTUP_UNREACHABLE_DELAY_MS,
} from "./startupConnection.js";
import {
  getConnectivityRecoverySnapshot,
  registerConnectivityFailure,
  resetConnectivityRecoveryStoreForTests,
} from "./connectivityRecoveryStore.js";


function healthResponse(path, status = path === "/_elvern/frontend-health" ? 204 : 200) {
  const header = path === "/_elvern/frontend-health"
    ? "X-Elvern-Frontend-Health"
    : "X-Elvern-Backend-Health";
  return new Response(null, { status, headers: { [header]: "1" } });
}


describe("startup connection controller", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetConnectivityRecoveryStoreForTests();
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

    resolveProbe(healthResponse("/_elvern/frontend-health"));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(STARTUP_HEALTH_PROBE_INTERVAL_MS);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    resolveProbe(healthResponse("/health"));
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(STARTUP_HEALTH_PROBE_INTERVAL_MS);
    expect(fetchImpl).toHaveBeenCalledTimes(3);
    controller.stop();
  });

  test("a failure opened during an older in-flight probe gets an immediate bounded follow-up", async () => {
    const pending = [];
    const fetchImpl = vi.fn((path) => new Promise((resolve) => {
      pending.push({ path, resolve });
    }));
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchImpl).toHaveBeenCalledTimes(1);

    const failure = registerConnectivityFailure();
    void controller.reportFailure({ failureId: failure.failureId });

    pending.shift().resolve(healthResponse("/_elvern/frontend-health"));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    pending.shift().resolve(healthResponse("/health"));
    await vi.advanceTimersByTimeAsync(0);

    // The first probe began before the failure and cannot close it. Its
    // completion immediately starts one follow-up instead of waiting 10s.
    expect(getConnectivityRecoverySnapshot().active).toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(3);
    pending.shift().resolve(healthResponse("/_elvern/frontend-health"));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchImpl).toHaveBeenCalledTimes(4);
    pending.shift().resolve(healthResponse("/health"));
    await vi.advanceTimersByTimeAsync(0);

    expect(getConnectivityRecoverySnapshot()).toMatchObject({
      active: false,
      latestRecoveredFailureId: failure.failureId,
    });
    expect(fetchImpl).toHaveBeenCalledTimes(4);
    controller.stop();
  });

  test("a successful health response enters connected immediately", async () => {
    const fetchImpl = vi.fn((path) => Promise.resolve(healthResponse(path)));
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

  test.each([
    ["frontend 204 marker", "/_elvern/frontend-health", 204, "X-Elvern-Frontend-Health", "http_success", true],
    ["backend 200 marker", "/health", 200, "X-Elvern-Backend-Health", "http_success", true],
    ["missing marker", "/health", 200, null, "marker_missing", false],
    ["redirected HTML fallback", "/health", 200, "Content-Type", "marker_missing", false],
    ["404", "/health", 404, null, "unexpected_http_status", false],
    ["401", "/health", 401, null, "unexpected_http_status", false],
    ["403", "/health", 403, null, "unexpected_http_status", false],
    ["503", "/health", 503, null, "http_unhealthy", false],
    ["swapped frontend marker", "/health", 204, "X-Elvern-Frontend-Health", "marker_missing", false],
    ["swapped backend marker", "/_elvern/frontend-health", 200, "X-Elvern-Backend-Health", "marker_missing", false],
  ])("classifies strict health response: %s", (_label, path, status, header, reason, reachable) => {
    const headers = header
      ? { [header]: header === "Content-Type" ? "text/html" : "1" }
      : {};

    expect(classifyStartupHealthResponse(path, new Response(null, { status, headers }))).toEqual({
      reachable,
      reason,
      status,
    });
  });

  test.each([
    ["missing marker", 200, null],
    ["not found", 404, null],
    ["unauthorized", 401, null],
    ["forbidden", 403, null],
    ["frontend marker on backend response", 204, "frontend"],
  ])("rejects backend health response with %s", async (_label, status, markerKind) => {
    const fetchImpl = vi.fn((path) => {
      if (path === "/_elvern/frontend-health") {
        return Promise.resolve(healthResponse(path));
      }
      const headers = markerKind === "frontend" ? { "X-Elvern-Frontend-Health": "1" } : {};
      return Promise.resolve(new Response(null, { status, headers }));
    });
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);

    expect(controller.getSnapshot().serviceReachable).toBe(false);
    expect(controller.getSnapshot().backendState).toBe("unreachable");
    controller.stop();
  });

  test("a verified recovery clears the Oops latch so a second outage can latch again", async () => {
    let backendHealthy = true;
    const fetchImpl = vi.fn((path) => Promise.resolve(
      path === "/health" && !backendHealthy
        ? healthResponse(path, 503)
        : healthResponse(path)
    ));
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(controller.getSnapshot()).toMatchObject({ status: "connected", oopsLatched: false });

    backendHealthy = false;
    const firstOutage = controller.reportFailure();
    await vi.advanceTimersByTimeAsync(FAST_OOPS_CONFIRMATION_DELAY_MS);
    await firstOutage;
    const firstGeneration = controller.getSnapshot().outageGeneration;
    expect(controller.getSnapshot()).toMatchObject({ status: "unreachable", oopsLatched: true });

    backendHealthy = true;
    await controller.retry();
    expect(controller.getSnapshot()).toMatchObject({
      status: "connected",
      oopsLatched: false,
      oopsLatchedGeneration: 0,
    });

    backendHealthy = false;
    const secondOutage = controller.reportFailure();
    await vi.advanceTimersByTimeAsync(FAST_OOPS_CONFIRMATION_DELAY_MS);
    await secondOutage;
    expect(controller.getSnapshot()).toMatchObject({ status: "unreachable", oopsLatched: true });
    expect(controller.getSnapshot().outageGeneration).toBe(firstGeneration + 1);
    controller.stop();
  });

  test("dispatches one sanitized recovery event per real runtime outage generation", async () => {
    let backendHealthy = true;
    const recoveryEvents = [];
    const handleRecovered = (event) => recoveryEvents.push(event.detail);
    window.addEventListener(CONNECTIVITY_RECOVERED_EVENT, handleRecovered);
    const fetchImpl = vi.fn((path) => Promise.resolve(
      path === "/health" && !backendHealthy
        ? healthResponse(path, 503)
        : healthResponse(path)
    ));
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });

    try {
      controller.start();
      await vi.advanceTimersByTimeAsync(0);
      expect(recoveryEvents).toEqual([]);

      backendHealthy = false;
      const outage = controller.reportFailure();
      await vi.advanceTimersByTimeAsync(FAST_OOPS_CONFIRMATION_DELAY_MS);
      await outage;
      const outageGeneration = controller.getSnapshot().outageGeneration;

      backendHealthy = true;
      await controller.retry();
      await controller.retry();

      expect(recoveryEvents).toEqual([{
        generation: outageGeneration,
        incidentId: expect.any(Number),
        previousClassification: "service_unreachable",
        recoveredThroughFailureId: expect.any(Number),
      }]);
      expect(recoveryEvents[0]).not.toHaveProperty("url");
      expect(recoveryEvents[0]).not.toHaveProperty("user");
    } finally {
      controller.stop();
      window.removeEventListener(CONNECTIVITY_RECOVERED_EVENT, handleRecovered);
    }
  });

  test("a transport incident with green health emits exactly one recovery event even when serviceReachable was already true", async () => {
    const recoveryEvents = [];
    const handler = (event) => recoveryEvents.push(event.detail);
    window.addEventListener(CONNECTIVITY_RECOVERED_EVENT, handler);
    const fetchImpl = vi.fn((path) => Promise.resolve(healthResponse(path)));
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });

    try {
      controller.start();
      await vi.advanceTimersByTimeAsync(0);
      expect(controller.getSnapshot()).toMatchObject({
        status: "connected",
        serviceReachable: true,
        runtimeReady: true,
      });
      expect(recoveryEvents).toEqual([]);

      await controller.reportFailure();
      await vi.advanceTimersByTimeAsync(0);

      expect(recoveryEvents).toHaveLength(1);
      expect(recoveryEvents[0].previousClassification).toBe("service_unreachable");
      expect(recoveryEvents[0].generation).toBeGreaterThan(0);
      expect(Object.keys(recoveryEvents[0]).sort()).toEqual([
        "generation",
        "incidentId",
        "previousClassification",
        "recoveredThroughFailureId",
      ]);
    } finally {
      controller.stop();
      window.removeEventListener(CONNECTIVITY_RECOVERED_EVENT, handler);
    }
  });

  test("three overlapping transport failures coalesce into one recovery event", async () => {
    const recoveryEvents = [];
    const handler = (event) => recoveryEvents.push(event.detail);
    window.addEventListener(CONNECTIVITY_RECOVERED_EVENT, handler);
    const fetchImpl = vi.fn((path) => Promise.resolve(healthResponse(path)));
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });

    try {
      controller.start();
      await vi.advanceTimersByTimeAsync(0);

      const first = controller.reportFailure();
      const second = controller.reportFailure();
      const third = controller.reportFailure();
      await Promise.all([first, second, third]);
      await vi.advanceTimersByTimeAsync(0);

      expect(recoveryEvents).toHaveLength(1);
    } finally {
      controller.stop();
      window.removeEventListener(CONNECTIVITY_RECOVERED_EVENT, handler);
    }
  });

  test("a probe cycle without any reported transport failure never opens an incident or emits recovery", async () => {
    const recoveryEvents = [];
    const handler = (event) => recoveryEvents.push(event.detail);
    window.addEventListener(CONNECTIVITY_RECOVERED_EVENT, handler);
    const fetchImpl = vi.fn((path) => Promise.resolve(healthResponse(path)));
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });

    try {
      controller.start();
      await vi.advanceTimersByTimeAsync(0);
      await controller.probe();
      await controller.probe();
      await vi.advanceTimersByTimeAsync(0);

      expect(recoveryEvents).toEqual([]);
    } finally {
      controller.stop();
      window.removeEventListener(CONNECTIVITY_RECOVERED_EVENT, handler);
    }
  });

  test("a second independent transport incident receives a strictly newer generation", async () => {
    const recoveryEvents = [];
    const handler = (event) => recoveryEvents.push(event.detail);
    window.addEventListener(CONNECTIVITY_RECOVERED_EVENT, handler);
    const fetchImpl = vi.fn((path) => Promise.resolve(healthResponse(path)));
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });

    try {
      controller.start();
      await vi.advanceTimersByTimeAsync(0);

      await controller.reportFailure();
      await vi.advanceTimersByTimeAsync(0);
      await controller.reportFailure();
      await vi.advanceTimersByTimeAsync(0);

      expect(recoveryEvents).toHaveLength(2);
      expect(recoveryEvents[1].generation).toBeGreaterThan(recoveryEvents[0].generation);
    } finally {
      controller.stop();
      window.removeEventListener(CONNECTIVITY_RECOVERED_EVENT, handler);
    }
  });

  test("an unhealthy probe after a transport incident escalates into the existing outage behavior without a premature recovery", async () => {
    let backendHealthy = true;
    const recoveryEvents = [];
    const handler = (event) => recoveryEvents.push(event.detail);
    window.addEventListener(CONNECTIVITY_RECOVERED_EVENT, handler);
    const fetchImpl = vi.fn((path) => Promise.resolve(
      path === "/health" && !backendHealthy ? healthResponse(path, 503) : healthResponse(path),
    ));
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });

    try {
      controller.start();
      await vi.advanceTimersByTimeAsync(0);

      backendHealthy = false;
      const outage = controller.reportFailure();
      await vi.advanceTimersByTimeAsync(FAST_OOPS_CONFIRMATION_DELAY_MS);
      await outage;

      expect(controller.getSnapshot()).toMatchObject({
        classification: CONNECTIVITY_BACKEND_UNREACHABLE,
        oopsLatched: true,
      });
      expect(recoveryEvents).toEqual([]);

      backendHealthy = true;
      await controller.retry();
      await vi.advanceTimersByTimeAsync(0);
      expect(recoveryEvents).toHaveLength(1);
      expect(recoveryEvents[0].previousClassification).toBe("service_unreachable");
    } finally {
      controller.stop();
      window.removeEventListener(CONNECTIVITY_RECOVERED_EVENT, handler);
    }
  });

  test("a transport incident abandoned by stop() cannot emit a late recovery event", async () => {
    const recoveryEvents = [];
    const handler = (event) => recoveryEvents.push(event.detail);
    window.addEventListener(CONNECTIVITY_RECOVERED_EVENT, handler);
    let releaseHealth = null;
    const fetchImpl = vi.fn((path) => {
      if (releaseHealth) {
        return new Promise((resolve) => {
          releaseHealth = () => resolve(healthResponse(path));
        });
      }
      return Promise.resolve(healthResponse(path));
    });
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });

    try {
      controller.start();
      await vi.advanceTimersByTimeAsync(0);

      releaseHealth = () => {};
      const pending = controller.reportFailure();
      controller.stop();
      if (typeof releaseHealth === "function") releaseHealth();
      await pending;
      await vi.advanceTimersByTimeAsync(0);

      expect(recoveryEvents).toEqual([]);
    } finally {
      controller.stop();
      window.removeEventListener(CONNECTIVITY_RECOVERED_EVENT, handler);
    }
  });

  test("a VPN-style outage can recover before an independent backend outage", async () => {
    const publicUrl = "https://probe.operator.example/connectivity";
    let frontendHealthy = true;
    let backendHealthy = true;
    const fetchImpl = vi.fn((path) => {
      if (path === publicUrl) return Promise.resolve(new Response(null, { status: 204 }));
      if (path === "/_elvern/frontend-health" && !frontendHealthy) {
        return Promise.reject(new TypeError("frontend unavailable"));
      }
      if (path === "/health" && !backendHealthy) {
        return Promise.resolve(healthResponse(path, 503));
      }
      return Promise.resolve(healthResponse(path));
    });
    const controller = createStartupConnectionController({
      fetchImpl,
      publicConnectivityProbeUrl: publicUrl,
    });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    frontendHealthy = false;
    const vpnOutage = controller.reportFailure();
    await vi.advanceTimersByTimeAsync(FAST_OOPS_CONFIRMATION_DELAY_MS);
    await vpnOutage;
    const vpnGeneration = controller.getSnapshot().outageGeneration;
    expect(controller.getSnapshot()).toMatchObject({
      classification: CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE,
      oopsLatched: true,
    });

    frontendHealthy = true;
    await controller.retry();
    expect(controller.getSnapshot()).toMatchObject({ status: "connected", oopsLatched: false });

    backendHealthy = false;
    const backendOutage = controller.reportFailure();
    await vi.advanceTimersByTimeAsync(FAST_OOPS_CONFIRMATION_DELAY_MS);
    await backendOutage;
    expect(controller.getSnapshot()).toMatchObject({
      classification: CONNECTIVITY_BACKEND_UNREACHABLE,
      oopsLatched: true,
      outageGeneration: vpnGeneration + 1,
    });
    controller.stop();
  });

  test("a confirmation timer from a stopped lifecycle cannot latch after restart", async () => {
    let frontendHealthy = true;
    const fetchImpl = vi.fn((path) => {
      if (path === "/_elvern/frontend-health" && !frontendHealthy) {
        return Promise.reject(new TypeError("frontend unavailable"));
      }
      return Promise.resolve(healthResponse(path));
    });
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    frontendHealthy = false;
    const staleOutage = controller.reportFailure();
    await vi.advanceTimersByTimeAsync(FAST_OOPS_CONFIRMATION_DELAY_MS - 1);
    controller.stop();
    frontendHealthy = true;
    controller.start();
    await vi.advanceTimersByTimeAsync(1);
    await staleOutage;
    await vi.advanceTimersByTimeAsync(0);

    expect(controller.getSnapshot()).toMatchObject({ status: "connected", oopsLatched: false });
    controller.stop();
  });

  test("stops recovery polling after the application becomes healthy on non-desktop", async () => {
    const fetchImpl = vi.fn((path) => Promise.resolve(healthResponse(path)));
    const controller = createStartupConnectionController({ fetchImpl, platform: "iphone", publicConnectivityProbes: [] });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    const callsAfterStartup = fetchImpl.mock.calls.length;

    await vi.advanceTimersByTimeAsync(STARTUP_HEALTH_PROBE_INTERVAL_MS * 4);
    expect(fetchImpl).toHaveBeenCalledTimes(callsAfterStartup);
    controller.stop();
  });

  test("classifies frontend-up backend-down without claiming VPN certainty", async () => {
    const fetchImpl = vi.fn((path) => Promise.resolve(
      healthResponse(path, path === "/_elvern/frontend-health" ? 204 : 503)
    ));
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);

    expect(controller.getSnapshot().classification).toBe(CONNECTIVITY_BACKEND_UNREACHABLE);
    expect(controller.getSnapshot().status).toBe("connecting");
    await vi.advanceTimersByTimeAsync(FAST_OOPS_CONFIRMATION_DELAY_MS);
    expect(controller.getSnapshot()).toMatchObject({
      status: "unreachable",
      oopsEvidenceReason: "conclusive_backend_unreachable",
    });
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
    expect(controller.getSnapshot().status).toBe("connecting");
    await vi.advanceTimersByTimeAsync(FAST_OOPS_CONFIRMATION_DELAY_MS - 1);
    expect(controller.getSnapshot().status).toBe("connecting");
    await vi.advanceTimersByTimeAsync(1);
    expect(controller.getSnapshot()).toMatchObject({
      status: "unreachable",
      oopsEvidenceReason: "conclusive_frontend_unreachable",
    });
    controller.stop();
  });

  test("does not latch when a hard frontend failure recovers in the confirmation round", async () => {
    let frontendAttempts = 0;
    const publicUrl = "https://probe.operator.example/connectivity";
    const fetchImpl = vi.fn((path) => {
      if (path === publicUrl) return Promise.resolve(new Response(null, { status: 204 }));
      if (path === "/_elvern/frontend-health") {
        frontendAttempts += 1;
        return frontendAttempts === 1
          ? Promise.reject(new TypeError("frontend unavailable"))
          : Promise.resolve(healthResponse(path));
      }
      return Promise.resolve(healthResponse(path));
    });
    const controller = createStartupConnectionController({
      fetchImpl,
      publicConnectivityProbeUrl: publicUrl,
    });

    controller.start();
    await vi.advanceTimersByTimeAsync(FAST_OOPS_CONFIRMATION_DELAY_MS);

    expect(controller.getSnapshot()).toMatchObject({
      status: "connected",
      oopsLatched: false,
    });
    controller.stop();
  });

  test("two untrusted public probe failures remain insufficient evidence", async () => {
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
    await Promise.resolve();
    expect(controller.getSnapshot().classification).toBe(CONNECTIVITY_EVIDENCE_INSUFFICIENT);
    expect(controller.getSnapshot().publicEvidenceReason).toBe("probe_failure_unverified");
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
      return Promise.resolve(healthResponse(path));
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

    releaseWatchdog(healthResponse("/_elvern/frontend-health"));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchImpl).toHaveBeenCalledTimes(startupCalls + 2);
    controller.stop();
  });

  test("does not run the healthy watchdog on phone or tablet platforms", async () => {
    for (const platform of ["iphone", "ipad", "android"]) {
      const fetchImpl = vi.fn((path) => Promise.resolve(healthResponse(path)));
      const controller = createStartupConnectionController({ fetchImpl, platform, publicConnectivityProbes: [] });
      controller.start();
      await vi.advanceTimersByTimeAsync(0);
      const startupCalls = fetchImpl.mock.calls.length;
      await vi.advanceTimersByTimeAsync(DESKTOP_CONNECTIVITY_WATCHDOG_INTERVAL_MS * 2);
      expect(fetchImpl).toHaveBeenCalledTimes(startupCalls);
      controller.stop();
    }
  });

  test("browser offline is classified and latched immediately before runtime readiness", async () => {
    const navigatorObject = { onLine: false };
    const fetchImpl = vi.fn();
    const controller = createStartupConnectionController({ fetchImpl, navigatorObject, publicConnectivityProbes: [] });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);

    expect(controller.getSnapshot().classification).toBe(CONNECTIVITY_INTERNET_OFFLINE);
    expect(controller.getSnapshot().publicEvidenceReason).toBe("browser_explicit_offline");
    expect(controller.getSnapshot().status).toBe("unreachable");
    expect(controller.getSnapshot().oopsEvidenceReason).toBe("conclusive_browser_offline");
    expect(fetchImpl.mock.calls.every(([path]) => path === "/_elvern/frontend-health")).toBe(true);
    controller.stop();
  });

  test("runtime failures never clear the permanent ready latch", async () => {
    let healthy = true;
    const fetchImpl = vi.fn((path) => {
      if (healthy) {
        return Promise.resolve(healthResponse(path));
      }
      return path === "/_elvern/frontend-health"
        ? Promise.resolve(healthResponse(path))
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
    const fetchImpl = vi.fn((path) => Promise.resolve(healthResponse(path)));
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

  test("an explicit offline login failure latches the offline Oops immediately", async () => {
    const navigatorObject = { onLine: true };
    const fetchImpl = vi.fn((path) => Promise.resolve(healthResponse(path)));
    const controller = createStartupConnectionController({ fetchImpl, navigatorObject, publicConnectivityProbes: [] });

    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    navigatorObject.onLine = false;
    await controller.reportFailure({ forceOfflineOops: true });

    expect(controller.getSnapshot()).toMatchObject({
      classification: CONNECTIVITY_INTERNET_OFFLINE,
      offlineOopsRequired: true,
      runtimeReady: true,
      status: "unreachable",
      oopsEvidenceReason: "conclusive_browser_offline",
    });
    controller.stop();
  });

  test("a trusted public failure plus hard origin failure confirms offline Oops early", async () => {
    const navigatorObject = { onLine: true };
    const publicUrl = "https://probe.operator.example/connectivity";
    let publicReachable = true;
    const fetchImpl = vi.fn((path) => {
      if (publicReachable) {
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
    await vi.advanceTimersByTimeAsync(FAST_OOPS_CONFIRMATION_DELAY_MS);
    await vi.advanceTimersByTimeAsync(10);
    await failureProbe;

    expect(controller.getSnapshot()).toMatchObject({
      classification: CONNECTIVITY_INTERNET_OFFLINE,
      offlineOopsRequired: true,
      runtimeReady: true,
      status: "unreachable",
      oopsEvidenceReason: "conclusive_trusted_public_failure",
    });
    controller.stop();
  });

  test("hidden desktop pages stop the watchdog and visible pages probe immediately", async () => {
    const fetchImpl = vi.fn((path) => Promise.resolve(healthResponse(path)));
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
    const fetchImpl = vi.fn((path) => Promise.resolve(healthResponse(path)));
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
    const fetchImpl = vi.fn((path) => Promise.resolve(healthResponse(path)));
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
      return Promise.resolve(healthResponse(_path));
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
      return Promise.resolve(healthResponse(path));
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

  test("consumes one verified manual service recovery across reload when public probes are blocked", async () => {
    const publicUrl = "https://probe.operator.example/connectivity";
    const values = new Map();
    const storage = {
      getItem: (key) => values.get(key) ?? null,
      removeItem: (key) => values.delete(key),
      setItem: (key, value) => values.set(key, String(value)),
    };
    let publicReachable = true;
    const fetchImpl = vi.fn((path) => {
      if (path === publicUrl) {
        return publicReachable
          ? Promise.resolve({ status: 204, ok: true })
          : Promise.reject(new TypeError("public probe blocked"));
      }
      return Promise.resolve(healthResponse(path));
    });
    const firstController = createStartupConnectionController({
      fetchImpl,
      publicConnectivityProbeUrl: publicUrl,
      publicProbeConfirmationDelayMs: 0,
      publicProbeStorage: storage,
      recoveryStorage: storage,
    });
    firstController.start();
    await vi.advanceTimersByTimeAsync(0);
    firstController.stop();

    storage.setItem(STARTUP_MANUAL_SERVICE_RECOVERY_STORAGE_KEY, "1");
    publicReachable = false;
    const recoveredController = createStartupConnectionController({
      fetchImpl,
      publicConnectivityProbeUrl: publicUrl,
      publicProbeConfirmationDelayMs: 0,
      publicProbeStorage: storage,
      recoveryStorage: storage,
    });
    recoveredController.start();
    await vi.advanceTimersByTimeAsync(0);

    expect(storage.getItem(STARTUP_MANUAL_SERVICE_RECOVERY_STORAGE_KEY)).toBeNull();
    expect(recoveredController.getSnapshot()).toMatchObject({
      serviceReachable: true,
      runtimeReady: true,
      offlineOopsRequired: false,
      status: "connected",
    });
    recoveredController.stop();
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
      return Promise.resolve(healthResponse(path));
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
      publicEvidenceReason: "probe_failure_unverified",
      publicProbeTrusted: false,
      frontendState: "unreachable",
      backendState: "unknown",
      classification: CONNECTIVITY_EVIDENCE_INSUFFICIENT,
    });
    controller.stop();
  });
});
