import { describe, expect, test, vi } from "vitest";

import {
  buildServiceWorkerRegistration,
  registerElvernServiceWorker,
} from "./serviceWorkerRegistration.js";


describe("service worker dynamic-prefix registration", () => {
  test("uses document.baseURI for both script and scope", () => {
    expect(buildServiceWorkerRegistration("https://elvern.test/abc23456/")).toEqual({
      scriptUrl: "https://elvern.test/abc23456/sw.js",
      scope: "/abc23456/",
    });
  });

  test("registration failures are controlled and do not block application startup", async () => {
    const warn = vi.fn();
    const serviceWorker = { register: vi.fn().mockRejectedValue(new Error("unsupported")) };
    const { registerElvernServiceWorker } = await import("./serviceWorkerRegistration.js");

    await expect(registerElvernServiceWorker({
      baseUri: "https://elvern.test/abc23456/",
      serviceWorker,
      warn,
    })).resolves.toBeNull();
    expect(warn).toHaveBeenCalledOnce();
  });

  test("keeps the active prefix registration and removes only stale Elvern sw.js scopes", async () => {
    const activeRegistration = {
      scope: "https://elvern.test/abc23456/",
      active: { scriptURL: "https://elvern.test/abc23456/sw.js" },
      installing: null,
      waiting: null,
      update: vi.fn().mockResolvedValue(undefined),
      unregister: vi.fn(),
    };
    const staleElvernRegistration = {
      scope: "https://elvern.test/old23456/",
      active: { scriptURL: "https://elvern.test/old23456/sw.js" },
      unregister: vi.fn().mockResolvedValue(true),
    };
    const unrelatedRegistration = {
      scope: "https://elvern.test/another-app/",
      active: { scriptURL: "https://elvern.test/another-app/worker.js" },
      unregister: vi.fn(),
    };
    const serviceWorker = {
      register: vi.fn().mockResolvedValue(activeRegistration),
      getRegistrations: vi.fn().mockResolvedValue([
        activeRegistration,
        staleElvernRegistration,
        unrelatedRegistration,
      ]),
    };

    await registerElvernServiceWorker({
      baseUri: "https://elvern.test/abc23456/",
      serviceWorker,
    });

    expect(staleElvernRegistration.unregister).toHaveBeenCalledOnce();
    expect(activeRegistration.update).toHaveBeenCalledOnce();
    expect(activeRegistration.unregister).not.toHaveBeenCalled();
    expect(unrelatedRegistration.unregister).not.toHaveBeenCalled();
  });

  test("a background update failure keeps the active worker and does not fail registration", async () => {
    const warn = vi.fn();
    const activeRegistration = {
      scope: "https://elvern.test/abc23456/",
      active: { scriptURL: "https://elvern.test/abc23456/sw.js" },
      installing: null,
      waiting: null,
      update: vi.fn().mockRejectedValue(new Error("temporarily offline")),
    };
    const serviceWorker = {
      register: vi.fn().mockResolvedValue(activeRegistration),
      getRegistrations: vi.fn().mockResolvedValue([activeRegistration]),
    };

    await expect(registerElvernServiceWorker({
      baseUri: "https://elvern.test/abc23456/",
      serviceWorker,
      warn,
    })).resolves.toBe(activeRegistration);

    expect(warn).toHaveBeenCalledWith(
      "Elvern offline recovery update check failed; the current worker remains available.",
    );
  });
});
