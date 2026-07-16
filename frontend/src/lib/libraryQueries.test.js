import { afterEach, describe, expect, test, vi } from "vitest";

import {
  buildLibraryQueryKey,
  invalidateLibraryQueries,
  isLibraryQueryKey,
  LIBRARY_QUERY_GC_TIME_MS,
  LIBRARY_QUERY_STALE_TIME_MS,
  normalizeLibraryQueryIdentity,
  patchLibraryProgressCaches,
} from "./libraryQueries";
import { queryClient } from "./queryClient";


describe("library query identity", () => {
  afterEach(() => {
    queryClient.clear();
    vi.restoreAllMocks();
  });

  test("uses the fixed Phase 1 cache lifetimes", () => {
    expect(LIBRARY_QUERY_STALE_TIME_MS).toBe(5 * 60 * 1000);
    expect(LIBRARY_QUERY_GC_TIME_MS).toBe(4 * 60 * 60 * 1000);
  });

  test("normalizes every protected identity and view field", () => {
    expect(normalizeLibraryQueryIdentity({
      userId: " 42 ",
      role: " Admin ",
      category: " Anime ",
      source: " Cloud ",
      genre: " Adventure ",
      quality: " Gold ",
      sort: " AZ ",
      query: " Akira ",
    })).toEqual({
      userId: "42",
      role: "admin",
      category: "anime",
      source: "cloud",
      genre: "Adventure",
      quality: "gold",
      sort: "az",
      query: "Akira",
    });
  });

  test("keeps users, roles, searches, and view filters in distinct exact keys", () => {
    const base = {
      userId: 1,
      role: "user",
      category: "movies",
      source: "all",
      genre: "",
      quality: "all",
      sort: "smart",
      query: "",
    };
    const baseKey = buildLibraryQueryKey(base);

    expect(baseKey).not.toEqual(buildLibraryQueryKey({ ...base, userId: 2 }));
    expect(baseKey).not.toEqual(buildLibraryQueryKey({ ...base, role: "admin" }));
    expect(baseKey).not.toEqual(buildLibraryQueryKey({ ...base, category: "anime" }));
    expect(baseKey).not.toEqual(buildLibraryQueryKey({ ...base, query: "Akira" }));
    expect(isLibraryQueryKey(baseKey)).toBe(true);
  });

  test("central invalidation marks every library view stale without removing data", async () => {
    const firstKey = buildLibraryQueryKey({ userId: 1, role: "user", category: "movies" });
    const secondKey = buildLibraryQueryKey({ userId: 1, role: "user", category: "anime" });
    queryClient.setQueryData(firstKey, { items: [{ id: 1 }] });
    queryClient.setQueryData(secondKey, { items: [{ id: 2 }] });

    await invalidateLibraryQueries({ refetchType: "none" });

    expect(queryClient.getQueryData(firstKey)).toEqual({ items: [{ id: 1 }] });
    expect(queryClient.getQueryData(secondKey)).toEqual({ items: [{ id: 2 }] });
    expect(queryClient.getQueryState(firstKey)?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(secondKey)?.isInvalidated).toBe(true);
  });

  test("patches every existing item instance without inserting, deleting, or reordering", async () => {
    const key = buildLibraryQueryKey({ userId: 1, role: "user", category: "movies" });
    const target = { id: 42, title: "Akira", progress_seconds: 1, custom: "keep" };
    const unrelated = { id: 7, title: "Arrival", progress_seconds: 9 };
    queryClient.setQueryData(key, {
      items: [target, unrelated],
      series_rails: [{ key: "local", items: [target, unrelated] }],
      cloud_series_rails: [{ key: "cloud", items: [target] }],
      continue_watching: [unrelated, target],
      recently_added: [target, unrelated],
    });
    const refetchSpy = vi.spyOn(queryClient, "refetchQueries");

    const result = await patchLibraryProgressCaches({
      media_item_id: 42,
      position_seconds: 120,
      duration_seconds: 900,
      completed: false,
    });
    const payload = queryClient.getQueryData(key);

    expect(result).toEqual({ patchedQueryCount: 1, markedStale: true, activeRefetched: false });
    expect(payload.items.map((item) => item.id)).toEqual([42, 7]);
    expect(payload.items[0]).toMatchObject({
      id: 42,
      custom: "keep",
      progress_seconds: 120,
      progress_duration_seconds: 900,
      completed: false,
    });
    expect(payload.items[1]).toBe(unrelated);
    expect(payload.series_rails[0].items[0].progress_seconds).toBe(120);
    expect(payload.cloud_series_rails[0].items[0].progress_seconds).toBe(120);
    expect(payload.continue_watching.map((item) => item.id)).toEqual([7, 42]);
    expect(payload.continue_watching[1].progress_seconds).toBe(120);
    expect(payload.recently_added[0].progress_seconds).toBe(120);
    expect(queryClient.getQueryState(key)?.isInvalidated).toBe(true);
    expect(refetchSpy).not.toHaveBeenCalled();
  });

  test("completion may perform one explicit silent active refetch", async () => {
    const key = buildLibraryQueryKey({ userId: 1, role: "user", category: "movies" });
    queryClient.setQueryData(key, { items: [{ id: 42, title: "Akira" }] });
    const refetchSpy = vi.spyOn(queryClient, "refetchQueries").mockResolvedValue();

    const result = await patchLibraryProgressCaches({
      media_item_id: 42,
      position_seconds: 0,
      duration_seconds: 900,
      completed: true,
    }, { refetchActiveOnCompletion: true });

    expect(result.activeRefetched).toBe(true);
    expect(refetchSpy).toHaveBeenCalledTimes(1);
    expect(refetchSpy).toHaveBeenCalledWith({
      queryKey: ["library", "v1"],
      type: "active",
    });
  });
});
