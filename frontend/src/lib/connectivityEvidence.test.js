import { describe, expect, test, vi } from "vitest";

import {
  classifyConnectivityEvidence,
  probePublicConnectivity,
} from "./connectivityEvidence.js";
import {
  CONNECTIVITY_BACKEND_UNREACHABLE,
  CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE,
  CONNECTIVITY_HEALTHY,
  CONNECTIVITY_INTERNET_OFFLINE,
} from "./startupConnection.js";


describe("connectivity evidence", () => {
  test("uses explicit browser offline evidence without probing public Internet", () => {
    expect(classifyConnectivityEvidence({ browserOffline: true })).toBe(CONNECTIVITY_INTERNET_OFFLINE);
  });

  test("distinguishes VPN, offline, backend, and healthy evidence", () => {
    expect(classifyConnectivityEvidence({ frontendReachable: false, publicInternetReachable: true }))
      .toBe(CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE);
    expect(classifyConnectivityEvidence({ frontendReachable: false, publicInternetReachable: false }))
      .toBe(CONNECTIVITY_INTERNET_OFFLINE);
    expect(classifyConnectivityEvidence({ frontendReachable: true, backendReachable: false }))
      .toBe(CONNECTIVITY_BACKEND_UNREACHABLE);
    expect(classifyConnectivityEvidence({ frontendReachable: true, backendReachable: true }))
      .toBe(CONNECTIVITY_HEALTHY);
  });

  test("falls back to VPN/origin unreachable when no public probe is configured", () => {
    expect(classifyConnectivityEvidence({
      frontendReachable: false,
      publicInternetReachable: null,
    })).toBe(CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE);
  });

  test("public probe omits credentials, referrer, cache, and private application context", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    const result = await probePublicConnectivity({
      fetchImpl,
      url: "https://probe.operator.example/connectivity",
      timeoutMs: 1_000,
    });

    expect(result).toBe(true);
    expect(fetchImpl).toHaveBeenCalledWith(
      "https://probe.operator.example/connectivity",
      expect.objectContaining({
        cache: "no-store",
        credentials: "omit",
        mode: "cors",
        referrerPolicy: "no-referrer",
        signal: expect.any(AbortSignal),
      }),
    );
    const options = fetchImpl.mock.calls[0][1];
    expect(options).not.toHaveProperty("headers");
    expect(options).not.toHaveProperty("body");
  });
});
