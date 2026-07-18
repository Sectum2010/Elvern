// @vitest-environment node

import vm from "node:vm";
import { readFileSync } from "node:fs";

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";


const source = readFileSync(new URL("../../public/sw.js", import.meta.url), "utf8")
  .replace("__ELVERN_OFFLINE_SHELL_REVISION__", "a".repeat(64));


class WorkerRequest {
  constructor(input, init = {}) {
    this.url = typeof input === "string" ? input : input.url;
    this.method = init.method || input.method || "GET";
    this.mode = init.mode || input.mode || "navigate";
    this.cache = init.cache || input.cache;
  }
}


function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}


function createHarness(fetchImpl, { cacheKeys = [] } = {}) {
  const listeners = new Map();
  const caches = {
    add: vi.fn(),
    delete: vi.fn(),
    keys: vi.fn().mockResolvedValue(cacheKeys),
    match: vi.fn(() => Promise.resolve(new Response("offline", { status: 200 }))),
    open: vi.fn().mockResolvedValue({ add: vi.fn().mockResolvedValue(undefined) }),
  };
  const self = {
    addEventListener: vi.fn((name, callback) => listeners.set(name, callback)),
    clients: { claim: vi.fn().mockResolvedValue(undefined) },
    location: { origin: "https://elvern.test" },
    registration: { scope: "https://elvern.test/abc23456/" },
    skipWaiting: vi.fn().mockResolvedValue(undefined),
  };
  vm.runInNewContext(source, {
    Date,
    Headers,
    Map,
    Promise,
    Request: WorkerRequest,
    Response,
    Set,
    TypeError,
    URL,
    caches,
    clearTimeout,
    fetch: fetchImpl,
    self,
    setTimeout,
  });

  function navigate({
    clientId = "client-a",
    replacesClientId = "",
    url = "https://elvern.test/abc23456/library",
  } = {}) {
    let responsePromise;
    listeners.get("fetch")({
      clientId,
      replacesClientId,
      request: new WorkerRequest(url, { method: "GET", mode: "navigate" }),
      respondWith(value) { responsePromise = Promise.resolve(value); },
    });
    return responsePromise;
  }

  function arm(clientId = "client-a", expiresAt = Date.now() + 15_000) {
    const postMessage = vi.fn();
    listeners.get("message")({
      source: { id: clientId },
      ports: [{ postMessage }],
      data: {
        type: "ELVERN_ARM_RECOVERY_NAVIGATION",
        schema_version: 1,
        nonce: "0123456789abcdef0123456789abcdef",
        expires_at: expiresAt,
      },
    });
    return postMessage;
  }

  async function activate() {
    let pending;
    listeners.get("activate")({ waitUntil(value) { pending = Promise.resolve(value); } });
    await pending;
  }

  return { activate, arm, caches, navigate };
}


describe("service worker navigation runtime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-18T00:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  test.each([5_000, 7_900])("keeps waiting for a normal SPA response at %dms", async (delayMs) => {
    const network = deferred();
    const harness = createHarness(vi.fn(() => network.promise));
    const responsePromise = harness.navigate();

    await vi.advanceTimersByTimeAsync(delayMs);
    network.resolve(new Response("app", { status: 200 }));
    const response = await responsePromise;

    expect(await response.text()).toBe("app");
    expect(response.headers.get("X-Elvern-Offline-Shell")).toBeNull();
    expect(harness.caches.match).not.toHaveBeenCalled();
  });

  test("falls back only when the normal eight second handoff expires", async () => {
    const network = deferred();
    const harness = createHarness(vi.fn(() => network.promise));
    const responsePromise = harness.navigate();

    await vi.advanceTimersByTimeAsync(7_999);
    expect(harness.caches.match).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);

    const response = await responsePromise;
    expect(response.headers.get("X-Elvern-Offline-Shell")).toBe("1");
    network.reject(new TypeError("late network failure"));
    await vi.runAllTimersAsync();
  });

  test("an armed recovery navigation can succeed after ten seconds and consumes its token once", async () => {
    const firstNetwork = deferred();
    const secondNetwork = deferred();
    const fetchImpl = vi.fn()
      .mockImplementationOnce(() => firstNetwork.promise)
      .mockImplementationOnce(() => secondNetwork.promise);
    const harness = createHarness(fetchImpl);
    const armReply = harness.arm("client-a");
    expect(armReply).toHaveBeenCalledWith({
      type: "ELVERN_RECOVERY_NAVIGATION_ARMED",
      schema_version: 1,
      nonce: "0123456789abcdef0123456789abcdef",
      accepted: true,
    });
    const recoveryResponse = harness.navigate({ clientId: "client-a" });

    await vi.advanceTimersByTimeAsync(10_000);
    expect(harness.caches.match).not.toHaveBeenCalled();
    firstNetwork.resolve(new Response("recovered", {
      status: 200,
      headers: { "X-Elvern-App-Shell": "1" },
    }));
    expect(await (await recoveryResponse).text()).toBe("recovered");

    const ordinaryResponse = harness.navigate({ clientId: "client-a" });
    await vi.advanceTimersByTimeAsync(8_000);
    expect((await ordinaryResponse).headers.get("X-Elvern-Offline-Shell")).toBe("1");
    secondNetwork.reject(new TypeError("late failure"));
    await vi.runAllTimersAsync();
  });

  test("a replacement navigation consumes the arm belonging to the offline document client", async () => {
    const harness = createHarness(vi.fn().mockResolvedValue(new Response("recovered", {
      status: 200,
      headers: { "X-Elvern-App-Shell": "1" },
    })));
    harness.arm("offline-client");

    const response = await harness.navigate({
      clientId: "new-navigation-client",
      replacesClientId: "offline-client",
    });

    expect(await response.text()).toBe("recovered");
    expect(harness.caches.match).not.toHaveBeenCalled();
  });

  test("an arm cannot be consumed by another client and expires after fifteen seconds", async () => {
    const fetchImpl = vi.fn(() => new Promise(() => {}));
    const harness = createHarness(fetchImpl);
    harness.arm("client-a");

    const otherClientResponse = harness.navigate({ clientId: "client-b" });
    await vi.advanceTimersByTimeAsync(8_000);
    expect((await otherClientResponse).headers.get("X-Elvern-Offline-Shell")).toBe("1");

    await vi.advanceTimersByTimeAsync(7_001);
    const expiredResponse = harness.navigate({ clientId: "client-a" });
    await vi.advanceTimersByTimeAsync(8_000);
    expect((await expiredResponse).headers.get("X-Elvern-Offline-Shell")).toBe("1");
  });

  test("activation removes only old revisions for the current dynamic scope", async () => {
    const currentScope = encodeURIComponent("/abc23456/");
    const otherScope = encodeURIComponent("/bcd23456/");
    const currentName = `elvern-offline-shell-${"a".repeat(64)}:${currentScope}`;
    const oldCurrent = `elvern-offline-shell-old:${currentScope}`;
    const validOther = `elvern-offline-shell-other:${otherScope}`;
    const unrelated = "another-pwa-cache";
    const harness = createHarness(vi.fn(), {
      cacheKeys: [currentName, oldCurrent, validOther, unrelated],
    });

    await harness.activate();

    expect(harness.caches.delete).toHaveBeenCalledWith(oldCurrent);
    expect(harness.caches.delete).not.toHaveBeenCalledWith(currentName);
    expect(harness.caches.delete).not.toHaveBeenCalledWith(validOther);
    expect(harness.caches.delete).not.toHaveBeenCalledWith(unrelated);
  });
});
