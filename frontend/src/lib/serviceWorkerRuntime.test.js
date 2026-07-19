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


function createFakeIndexedDB() {
  const databases = new Map();
  const controls = { failOpen: false, failReads: false, failWrites: false };

  function clone(value) {
    return value == null ? value : structuredClone(value);
  }

  function databaseState(name) {
    if (!databases.has(name)) {
      databases.set(name, { stores: new Map(), version: 0 });
    }
    return databases.get(name);
  }

  function createTransaction(state, storeName, mode) {
    const transaction = {
      error: null,
      onabort: null,
      oncomplete: null,
      onerror: null,
      _failed: false,
      _pending: 0,
    };
    const store = state.stores.get(storeName);

    function fail(error) {
      if (transaction._failed) return;
      transaction._failed = true;
      transaction.error = error;
      transaction.onerror?.({ target: transaction });
      transaction.onabort?.({ target: transaction });
    }

    function maybeComplete() {
      queueMicrotask(() => {
        if (!transaction._failed && transaction._pending === 0) {
          transaction.oncomplete?.({ target: transaction });
        }
      });
    }

    function request(operation, { write = false } = {}) {
      const result = { error: null, onsuccess: null, onerror: null, result: undefined };
      transaction._pending += 1;
      queueMicrotask(() => {
        if ((write && controls.failWrites) || (!write && controls.failReads)) {
          const error = new Error(write ? "write failed" : "read failed");
          result.error = error;
          result.onerror?.({ target: result });
          transaction._pending -= 1;
          fail(error);
          return;
        }
        try {
          result.result = clone(operation());
          result.onsuccess?.({ target: result });
        } catch (error) {
          result.error = error;
          result.onerror?.({ target: result });
          fail(error);
        } finally {
          transaction._pending -= 1;
          maybeComplete();
        }
      });
      return result;
    }

    transaction.objectStore = () => ({
      delete(key) {
        return request(() => store.delete(String(key)), { write: true });
      },
      get(key) {
        return request(() => store.get(String(key)));
      },
      getAll() {
        return request(() => [...store.values()]);
      },
      put(value, key) {
        return request(() => {
          store.set(String(key), clone(value));
          return key;
        }, { write: true });
      },
    });
    maybeComplete();
    return transaction;
  }

  const indexedDB = {
    controls,
    open(name, version) {
      const request = {
        error: null,
        onerror: null,
        onupgradeneeded: null,
        onsuccess: null,
        result: null,
      };
      queueMicrotask(() => {
        if (controls.failOpen) {
          request.error = new Error("open failed");
          request.onerror?.({ target: request });
          return;
        }
        const state = databaseState(name);
        const needsUpgrade = Number(version) > state.version;
        const db = {
          close() {},
          createObjectStore(storeName) {
            if (!state.stores.has(storeName)) state.stores.set(storeName, new Map());
            return {};
          },
          objectStoreNames: {
            contains(storeName) { return state.stores.has(storeName); },
          },
          transaction(storeName, mode) {
            return createTransaction(state, storeName, mode);
          },
        };
        request.result = db;
        if (needsUpgrade) {
          state.version = Number(version);
          request.onupgradeneeded?.({ target: request });
        }
        request.onsuccess?.({ target: request });
      });
      return request;
    },
    records(dbName, storeName) {
      return [...(databaseState(dbName).stores.get(storeName)?.values() || [])].map(clone);
    },
  };
  return indexedDB;
}


function createHarness(fetchImpl, {
  cacheKeys = [],
  indexedDB = createFakeIndexedDB(),
  scope = "https://elvern.test/abc23456/",
} = {}) {
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
    registration: { scope },
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
    indexedDB,
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

  async function arm(clientId = "client-a", expiresAt = Date.now() + 15_000, nonce = "0123456789abcdef0123456789abcdef") {
    const postMessage = vi.fn();
    let pending = Promise.resolve();
    listeners.get("message")({
      source: { id: clientId },
      ports: [{ postMessage }],
      data: {
        type: "ELVERN_ARM_RECOVERY_NAVIGATION",
        schema_version: 1,
        nonce,
        expires_at: expiresAt,
      },
      waitUntil(value) { pending = Promise.resolve(value); },
    });
    await pending;
    return postMessage;
  }

  async function activate() {
    let pending;
    listeners.get("activate")({ waitUntil(value) { pending = Promise.resolve(value); } });
    await pending;
  }

  return { activate, arm, caches, indexedDB, navigate };
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
    const armReply = await harness.arm("client-a");
    expect(armReply).toHaveBeenCalledWith({
      type: "ELVERN_RECOVERY_NAVIGATION_ARMED",
      schema_version: 1,
      nonce: "0123456789abcdef0123456789abcdef",
      accepted: true,
      durability: "durable",
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
    await harness.arm("offline-client");

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
    await harness.arm("client-a");

    const otherClientResponse = harness.navigate({ clientId: "client-b" });
    await vi.advanceTimersByTimeAsync(8_000);
    expect((await otherClientResponse).headers.get("X-Elvern-Offline-Shell")).toBe("1");

    await vi.advanceTimersByTimeAsync(7_001);
    const expiredResponse = harness.navigate({ clientId: "client-a" });
    await vi.advanceTimersByTimeAsync(8_000);
    expect((await expiredResponse).headers.get("X-Elvern-Offline-Shell")).toBe("1");
  });

  test("an arm survives worker restart, belongs to one scope and client, and is consumed once", async () => {
    const indexedDB = createFakeIndexedDB();
    const firstWorker = createHarness(vi.fn(), { indexedDB });
    const reply = await firstWorker.arm("offline-client");
    expect(reply).toHaveBeenCalledWith(expect.objectContaining({
      accepted: true,
      durability: "durable",
    }));

    const recoveryNetwork = deferred();
    const restartedWorker = createHarness(vi.fn()
      .mockImplementationOnce(() => recoveryNetwork.promise)
      .mockImplementation(() => new Promise(() => {})), { indexedDB });
    const recoveryResponse = restartedWorker.navigate({
      clientId: "replacement-client",
      replacesClientId: "offline-client",
    });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(restartedWorker.caches.match).not.toHaveBeenCalled();
    recoveryNetwork.resolve(new Response("recovered", {
      status: 200,
      headers: { "X-Elvern-App-Shell": "1" },
    }));
    expect(await (await recoveryResponse).text()).toBe("recovered");

    const secondResponse = restartedWorker.navigate({ clientId: "offline-client" });
    await vi.advanceTimersByTimeAsync(8_000);
    expect((await secondResponse).headers.get("X-Elvern-Offline-Shell")).toBe("1");

  });

  test("another dynamic scope cannot consume a durable arm", async () => {
    const indexedDB = createFakeIndexedDB();
    const sourceScope = createHarness(vi.fn(), { indexedDB });
    await sourceScope.arm("offline-client");
    const otherScope = createHarness(vi.fn(() => new Promise(() => {})), {
      indexedDB,
      scope: "https://elvern.test/bcd23456/",
    });

    const otherScopeResponse = otherScope.navigate({ clientId: "offline-client" });
    await vi.advanceTimersByTimeAsync(8_000);

    expect((await otherScopeResponse).headers.get("X-Elvern-Offline-Shell")).toBe("1");
    expect(indexedDB.records("elvern-service-worker-state-v1", "recovery_arms"))
      .toEqual([expect.objectContaining({ scope_identity: "/abc23456/" })]);
  });

  test("activation removes expired arms and the store remains bounded to thirty-two records", async () => {
    const indexedDB = createFakeIndexedDB();
    const worker = createHarness(vi.fn(), { indexedDB });
    await worker.arm("expired-client", Date.now() + 1_000, "expired-arm-nonce-0123456789");
    for (let index = 0; index < 35; index += 1) {
      await worker.arm(
        `client-${index}`,
        Date.now() + 15_000,
        `bounded-arm-nonce-${String(index).padStart(3, "0")}-0123456789`,
      );
    }

    expect(indexedDB.records("elvern-service-worker-state-v1", "recovery_arms")).toHaveLength(32);
    await vi.advanceTimersByTimeAsync(1_001);
    const restartedWorker = createHarness(vi.fn(), { indexedDB });
    await restartedWorker.activate();

    expect(indexedDB.records("elvern-service-worker-state-v1", "recovery_arms"))
      .not.toEqual(expect.arrayContaining([expect.objectContaining({ source_client_id: "expired-client" })]));
  });

  test("durable write failure rejects the ACK instead of claiming an in-memory arm", async () => {
    const indexedDB = createFakeIndexedDB();
    indexedDB.controls.failWrites = true;
    const harness = createHarness(vi.fn(), { indexedDB });

    const reply = await harness.arm("client-a");

    expect(reply).toHaveBeenCalledWith(expect.objectContaining({
      accepted: false,
      durability: "unavailable",
    }));
  });

  test("durable read failure safely falls back to the normal eight second path", async () => {
    const indexedDB = createFakeIndexedDB();
    const firstWorker = createHarness(vi.fn(), { indexedDB });
    await firstWorker.arm("client-a");
    indexedDB.controls.failReads = true;
    const restartedWorker = createHarness(vi.fn(() => new Promise(() => {})), { indexedDB });

    const responsePromise = restartedWorker.navigate({ clientId: "client-a" });
    await vi.advanceTimersByTimeAsync(8_000);

    expect((await responsePromise).headers.get("X-Elvern-Offline-Shell")).toBe("1");
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
