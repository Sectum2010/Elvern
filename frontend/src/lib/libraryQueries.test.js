import { afterEach, describe, expect, test } from "vitest";

import {
  buildLibraryQueryKey,
  invalidateLibraryQueries,
  isLibraryQueryKey,
  LIBRARY_QUERY_GC_TIME_MS,
  LIBRARY_QUERY_STALE_TIME_MS,
  normalizeLibraryQueryIdentity,
} from "./libraryQueries";
import { queryClient } from "./queryClient";


describe("library query identity", () => {
  afterEach(() => {
    queryClient.clear();
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
});
