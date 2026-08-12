import { afterEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "./api.js";
import {
  buildControlCenterResourceQueryKey,
  controlCenterResourcePath,
  fetchControlCenterResource,
  runControlCenterRecoveryTasks,
} from "./controlCenterQueries.js";
import { clearProtectedQueryCache, queryClient } from "./queryClient.js";

vi.mock("./api.js", () => ({ apiRequest: vi.fn() }));

afterEach(() => {
  queryClient.clear();
  vi.clearAllMocks();
});

describe("Control Center resource cache", () => {
  test("isolates protected data by user, role, resource, platform, and device", () => {
    expect(buildControlCenterResourceQueryKey({
      userId: 4,
      role: "ADMIN",
      resource: "desktopHelper",
      platform: "Mac",
      deviceId: "device-a",
    })).toEqual([
      "control-center",
      "v1",
      {
        userId: "4",
        role: "admin",
        resource: "desktophelper",
        platform: "mac",
        deviceId: "device-a",
      },
    ]);
  });

  test("builds an encoded desktop helper status request", () => {
    expect(controlCenterResourcePath({
      resource: "desktopHelper",
      platform: "linux",
      deviceId: "device / one",
    })).toBe("/api/desktop-helper/status?platform=linux&device_id=device+%2F+one");
  });

  test("owns the desktop Age restrictions endpoint as one protected resource", () => {
    expect(controlCenterResourcePath({ resource: "ageGroups" }))
      .toBe("/api/library/age-groups");
  });

  test("deduplicates concurrent requests for the same protected resource", async () => {
    let resolveRequest;
    apiRequest.mockReturnValue(new Promise((resolve) => {
      resolveRequest = resolve;
    }));
    const options = { userId: 1, role: "admin", resource: "system" };
    const first = fetchControlCenterResource(options);
    const second = fetchControlCenterResource(options);
    resolveRequest({ total_media_items: 12 });

    await expect(first).resolves.toEqual({ total_media_items: 12 });
    await expect(second).resolves.toEqual({ total_media_items: 12 });
    expect(apiRequest).toHaveBeenCalledTimes(1);
  });

  test("is removed with protected caches on logout or identity change", () => {
    const key = buildControlCenterResourceQueryKey({
      userId: 1,
      role: "admin",
      resource: "system",
    });
    queryClient.setQueryData(key, { total_media_items: 12 });
    clearProtectedQueryCache();
    expect(queryClient.getQueryData(key)).toBeUndefined();
  });

  test("bounds visible-resource recovery to two concurrent requests", async () => {
    let active = 0;
    let maximum = 0;
    let started = 0;
    const releases = [];
    const tasks = Array.from({ length: 5 }, () => () => new Promise((resolve) => {
      started += 1;
      active += 1;
      maximum = Math.max(maximum, active);
      releases.push(() => {
        active -= 1;
        resolve();
      });
    }));

    const recovery = runControlCenterRecoveryTasks(tasks);
    await vi.waitFor(() => expect(releases).toHaveLength(2));
    while (started < tasks.length) {
      const previousStarted = started;
      releases.shift()();
      await vi.waitFor(() => expect(started).toBe(previousStarted + 1));
    }
    while (releases.length) releases.shift()();
    await recovery;

    expect(maximum).toBe(2);
  });
});
