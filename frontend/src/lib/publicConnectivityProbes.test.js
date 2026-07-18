import { afterEach, describe, expect, test, vi } from "vitest";

import {
  createPublicConnectivityProbeRunner,
  DEFAULT_PUBLIC_CONNECTIVITY_PROBES,
  hashPublicConnectivityProbeRegistry,
  PUBLIC_PROBE_CONFIRMATION_DELAY_MS,
  PUBLIC_PROBE_TRUST_MAX_AGE_MS,
  PUBLIC_PROBE_TRUST_STORAGE_KEY,
  resolvePublicConnectivityProbeRegistry,
} from "./publicConnectivityProbes.js";


function createStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: vi.fn((key) => values.get(key) ?? null),
    removeItem: vi.fn((key) => values.delete(key)),
    setItem: vi.fn((key, value) => values.set(key, String(value))),
  };
}


function trustedRecord(probes, now = Date.now()) {
  return JSON.stringify({
    schema_version: 1,
    endpoint_list_hash: hashPublicConnectivityProbeRegistry(probes),
    last_successful_at: now,
    last_successful_endpoint_id: probes[0]?.id || null,
  });
}


describe("public connectivity probe registry", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("uses the three ordered defaults when no operator override exists", () => {
    const probes = resolvePublicConnectivityProbeRegistry({ pluralValue: "", singularValue: "" });

    expect(probes).toEqual(DEFAULT_PUBLIC_CONNECTIVITY_PROBES);
    expect(probes.map(({ id, url, expectedStatuses }) => ({ id, url, expectedStatuses }))).toEqual([
      {
        id: "cloudflare-trace",
        url: "https://www.cloudflare.com/cdn-cgi/trace",
        expectedStatuses: [200],
      },
      {
        id: "ipify-api64",
        url: "https://api64.ipify.org/",
        expectedStatuses: [200],
      },
      {
        id: "httpbin-204",
        url: "https://httpbin.org/status/204",
        expectedStatuses: [204],
      },
    ]);
  });

  test("plural config wins, validates schemes, deduplicates, preserves order, and supports none", () => {
    const probes = resolvePublicConnectivityProbeRegistry({
      pluralValue: JSON.stringify([
        "https://one.example/probe",
        "javascript:alert(1)",
        "https://one.example/probe",
        "https://two.example/status",
      ]),
      singularValue: "https://legacy.example/probe",
    });

    expect(probes.map((probe) => probe.url)).toEqual([
      "https://one.example/probe",
      "https://two.example/status",
    ]);
    expect(resolvePublicConnectivityProbeRegistry({
      pluralValue: "none",
      singularValue: "https://legacy.example/probe",
    })).toEqual([]);
  });

  test("legacy singular config remains supported", () => {
    const probes = resolvePublicConnectivityProbeRegistry({
      pluralValue: "",
      singularValue: "https://legacy.example/probe",
    });

    expect(probes).toHaveLength(1);
    expect(probes[0]).toMatchObject({ id: "operator-1", url: "https://legacy.example/probe" });
  });
});


describe("ordered public connectivity probes", () => {
  test("stops after the primary succeeds and never reads a response body", async () => {
    const text = vi.fn();
    const json = vi.fn();
    const clone = vi.fn();
    const fetchImpl = vi.fn().mockResolvedValue({ status: 200, ok: true, text, json, clone });
    const runner = createPublicConnectivityProbeRunner({
      fetchImpl,
      probes: DEFAULT_PUBLIC_CONNECTIVITY_PROBES,
      storage: createStorage(),
    });

    const result = await runner.probeConfirmed({ confirmationDelayMs: 0 });

    expect(result).toMatchObject({ internetState: "online", endpointId: "cloudflare-trace" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl).toHaveBeenCalledWith(
      DEFAULT_PUBLIC_CONNECTIVITY_PROBES[0].url,
      expect.objectContaining({
        method: "GET",
        cache: "no-store",
        credentials: "omit",
        mode: "cors",
        referrerPolicy: "no-referrer",
        signal: expect.any(AbortSignal),
      }),
    );
    expect(text).not.toHaveBeenCalled();
    expect(json).not.toHaveBeenCalled();
    expect(clone).not.toHaveBeenCalled();
  });

  test("falls back in order and stops when a backup succeeds", async () => {
    const fetchImpl = vi.fn((url) => {
      if (url === DEFAULT_PUBLIC_CONNECTIVITY_PROBES[1].url) {
        return Promise.resolve({ status: 200, ok: true });
      }
      return Promise.reject(new TypeError("unreachable"));
    });
    const runner = createPublicConnectivityProbeRunner({
      fetchImpl,
      probes: DEFAULT_PUBLIC_CONNECTIVITY_PROBES,
      storage: createStorage(),
    });

    const result = await runner.probeConfirmed({ confirmationDelayMs: 0 });

    expect(result).toMatchObject({ internetState: "online", endpointId: "ipify-api64" });
    expect(fetchImpl.mock.calls.map(([url]) => url)).toEqual([
      DEFAULT_PUBLIC_CONNECTIVITY_PROBES[0].url,
      DEFAULT_PUBLIC_CONNECTIVITY_PROBES[1].url,
    ]);
  });

  test("accepts the tertiary only at its expected 204 status", async () => {
    const fetchImpl = vi.fn((url) => {
      if (url === DEFAULT_PUBLIC_CONNECTIVITY_PROBES[2].url) {
        return Promise.resolve({ status: 204, ok: true });
      }
      return Promise.resolve({ status: 503, ok: false });
    });
    const runner = createPublicConnectivityProbeRunner({
      fetchImpl,
      probes: DEFAULT_PUBLIC_CONNECTIVITY_PROBES,
      storage: createStorage(),
    });

    await expect(runner.probeConfirmed({ confirmationDelayMs: 0 })).resolves.toMatchObject({
      internetState: "online",
      endpointId: "httpbin-204",
    });
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });

  test("one failed round is not enough and two unverified failed rounds stay unknown", async () => {
    vi.useFakeTimers();
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("unreachable"));
    const runner = createPublicConnectivityProbeRunner({
      fetchImpl,
      probes: DEFAULT_PUBLIC_CONNECTIVITY_PROBES,
      storage: createStorage(),
    });

    const pending = runner.probeConfirmed();
    await vi.advanceTimersByTimeAsync(PUBLIC_PROBE_CONFIRMATION_DELAY_MS - 1);
    expect(fetchImpl).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(1);

    await expect(pending).resolves.toMatchObject({ internetState: "unknown", trusted: false, rounds: 2 });
    expect(fetchImpl).toHaveBeenCalledTimes(6);
    vi.useRealTimers();
  });

  test("two failed rounds become offline only for a currently trusted endpoint list", async () => {
    const now = Date.now();
    const storage = createStorage({
      [PUBLIC_PROBE_TRUST_STORAGE_KEY]: trustedRecord(DEFAULT_PUBLIC_CONNECTIVITY_PROBES, now),
    });
    const runner = createPublicConnectivityProbeRunner({
      fetchImpl: vi.fn().mockRejectedValue(new TypeError("unreachable")),
      probes: DEFAULT_PUBLIC_CONNECTIVITY_PROBES,
      storage,
      now: () => now,
    });

    await expect(runner.probeConfirmed({ confirmationDelayMs: 0 })).resolves.toMatchObject({
      internetState: "offline",
      trusted: true,
      rounds: 2,
    });
  });

  test("changed endpoint hash and stale trust records are rejected", async () => {
    const now = Date.now();
    const changed = resolvePublicConnectivityProbeRegistry({
      pluralValue: '["https://changed.example/probe"]',
      singularValue: "",
    });
    const storage = createStorage({
      [PUBLIC_PROBE_TRUST_STORAGE_KEY]: trustedRecord(DEFAULT_PUBLIC_CONNECTIVITY_PROBES, now - PUBLIC_PROBE_TRUST_MAX_AGE_MS - 1),
    });
    const runner = createPublicConnectivityProbeRunner({
      fetchImpl: vi.fn().mockRejectedValue(new TypeError("unreachable")),
      probes: changed,
      storage,
      now: () => now,
    });

    await expect(runner.probeConfirmed({ confirmationDelayMs: 0 })).resolves.toMatchObject({
      internetState: "unknown",
      trusted: false,
    });
    expect(storage.removeItem).toHaveBeenCalledWith(PUBLIC_PROBE_TRUST_STORAGE_KEY);
  });

  test("cools a failing primary only after three fallback-validated failures", async () => {
    let now = 10_000;
    const fetchImpl = vi.fn((url) => Promise.resolve(
      url === DEFAULT_PUBLIC_CONNECTIVITY_PROBES[0].url
        ? { status: 503, ok: false }
        : { status: 200, ok: true },
    ));
    const runner = createPublicConnectivityProbeRunner({
      fetchImpl,
      probes: DEFAULT_PUBLIC_CONNECTIVITY_PROBES,
      storage: createStorage(),
      now: () => now,
    });

    await runner.probeChain();
    await runner.probeChain();
    await runner.probeChain();

    expect(runner.getEndpointStates()["cloudflare-trace"]).toMatchObject({
      circuitState: "open",
      consecutiveFailureCount: 3,
      cooldownUntil: now + (5 * 60 * 1000),
    });

    fetchImpl.mockClear();
    await runner.probeChain();
    expect(fetchImpl.mock.calls[0][0]).toBe(DEFAULT_PUBLIC_CONNECTIVITY_PROBES[1].url);
  });

  test("half-opens after cooldown, closes on success, and reopens on fallback-validated failure", async () => {
    let now = 20_000;
    let primaryHealthy = false;
    const fetchImpl = vi.fn((url) => Promise.resolve(
      url === DEFAULT_PUBLIC_CONNECTIVITY_PROBES[0].url && !primaryHealthy
        ? { status: 503, ok: false }
        : { status: 200, ok: true },
    ));
    const runner = createPublicConnectivityProbeRunner({
      fetchImpl,
      probes: DEFAULT_PUBLIC_CONNECTIVITY_PROBES,
      storage: createStorage(),
      now: () => now,
    });
    await runner.probeChain();
    await runner.probeChain();
    await runner.probeChain();

    now += 5 * 60 * 1000;
    primaryHealthy = true;
    await runner.probeChain();
    expect(runner.getEndpointStates()["cloudflare-trace"]).toMatchObject({
      circuitState: "closed",
      consecutiveFailureCount: 0,
      cooldownUntil: 0,
    });

    primaryHealthy = false;
    await runner.probeChain();
    await runner.probeChain();
    await runner.probeChain();
    now += 5 * 60 * 1000;
    await runner.probeChain();
    expect(runner.getEndpointStates()["cloudflare-trace"].circuitState).toBe("open");
  });

  test("a full-chain outage never cools down every endpoint", async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ status: 503, ok: false });
    const runner = createPublicConnectivityProbeRunner({
      fetchImpl,
      probes: DEFAULT_PUBLIC_CONNECTIVITY_PROBES,
      storage: createStorage(),
    });

    for (let round = 0; round < 5; round += 1) {
      await runner.probeChain();
    }

    expect(Object.values(runner.getEndpointStates())).toEqual([
      expect.objectContaining({ circuitState: "closed", cooldownUntil: 0 }),
      expect.objectContaining({ circuitState: "closed", cooldownUntil: 0 }),
      expect.objectContaining({ circuitState: "closed", cooldownUntil: 0 }),
    ]);
    expect(fetchImpl).toHaveBeenCalledTimes(15);
  });

  test("primary and secondary failures both count when tertiary succeeds", async () => {
    const fetchImpl = vi.fn((url) => Promise.resolve(
      url === DEFAULT_PUBLIC_CONNECTIVITY_PROBES[2].url
        ? { status: 204, ok: true }
        : { status: 503, ok: false },
    ));
    const runner = createPublicConnectivityProbeRunner({
      fetchImpl,
      probes: DEFAULT_PUBLIC_CONNECTIVITY_PROBES,
      storage: createStorage(),
    });

    await runner.probeChain();

    expect(runner.getEndpointStates()["cloudflare-trace"].consecutiveFailureCount).toBe(1);
    expect(runner.getEndpointStates()["ipify-api64"].consecutiveFailureCount).toBe(1);
    expect(runner.getEndpointStates()["httpbin-204"].consecutiveFailureCount).toBe(0);
  });
});
