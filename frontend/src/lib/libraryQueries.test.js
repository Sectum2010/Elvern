import { afterEach, describe, expect, test, vi } from "vitest";

import {
  buildLibraryQueryKey,
  buildLibraryShadowV2QueryKey,
  buildLibraryV2QueryKey,
  invalidateLibraryQueries,
  invalidateLibraryQueriesForIdentity,
  isLibraryQueryKey,
  LIBRARY_QUERY_GC_TIME_MS,
  LIBRARY_QUERY_STALE_TIME_MS,
  matchesLibraryQueryProtectedIdentity,
  normalizeLibraryQueryIdentity,
  patchLibraryProgressCaches,
  patchLibraryProgressStateCaches,
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
      genres: ["adventure"],
      qualities: ["gold"],
      sort: "az",
      query: "Akira",
    });
  });

  test("uses the same canonical Unicode genres for URL views and query identity", () => {
    expect(normalizeLibraryQueryIdentity({
      userId: 42,
      role: "user",
      genres: ["Ｓｃｉ－Ｆｉ", "Action", "action", "Éclair"],
    }).genres).toEqual(["action", "sci-fi", "éclair"]);
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
    expect(buildLibraryV2QueryKey(base)).toEqual([
      "library",
      "v2",
      { ...normalizeLibraryQueryIdentity(base), query: "" },
    ]);
    expect(buildLibraryShadowV2QueryKey(base)[1]).toBe("shadow-v2");
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

  test("patches only the normalized v2 entity and preserves section membership", async () => {
    const key = buildLibraryV2QueryKey({ userId: 1, role: "user", category: "movies" });
    const sections = {
      item_ids: [42, 7],
      series_rails: [{ key: "rail", item_ids: [42, 7] }],
      cloud_series_rails: [],
      continue_watching_item_ids: [42],
      recently_added_item_ids: [7, 42],
    };
    queryClient.setQueryData(key, {
      schema_version: "library-summary-v2",
      revision: "authoritative-revision",
      items_by_id: {
        "42": { id: 42, title: "Akira", progress_seconds: 1 },
        "7": { id: 7, title: "Arrival", progress_seconds: 9 },
      },
      sections,
    });

    const result = await patchLibraryProgressCaches({
      media_item_id: 42,
      position_seconds: 120,
      duration_seconds: 900,
      completed: false,
    });
    const payload = queryClient.getQueryData(key);

    expect(result.patchedQueryCount).toBe(1);
    expect(payload.items_by_id["42"]).toMatchObject({
      id: 42,
      title: "Akira",
      progress_seconds: 120,
      progress_duration_seconds: 900,
      completed: false,
    });
    expect(payload.items_by_id["7"].progress_seconds).toBe(9);
    expect(payload.sections).toBe(sections);
    expect(payload.revision).toBe("authoritative-revision");
    expect(queryClient.getQueryState(key)?.isInvalidated).toBe(true);
  });

  test("central invalidation covers v1, v2, and shadow without deleting payloads", async () => {
    const identity = { userId: 1, role: "user", category: "movies" };
    const keys = [
      buildLibraryQueryKey(identity),
      buildLibraryV2QueryKey(identity),
      buildLibraryShadowV2QueryKey(identity),
    ];
    keys.forEach((key, index) => queryClient.setQueryData(key, { marker: index }));

    await invalidateLibraryQueries({ refetchType: "none" });

    keys.forEach((key, index) => {
      expect(queryClient.getQueryData(key)).toEqual({ marker: index });
      expect(queryClient.getQueryState(key)?.isInvalidated).toBe(true);
    });
  });

  test("protected identity matching requires exact normalized user and role", () => {
    const key = buildLibraryV2QueryKey({ userId: " 7 ", role: " Standard_User " });

    expect(matchesLibraryQueryProtectedIdentity(key, { userId: 7, role: "standard_user" })).toBe(true);
    expect(matchesLibraryQueryProtectedIdentity(key, { userId: 8, role: "standard_user" })).toBe(false);
    expect(matchesLibraryQueryProtectedIdentity(key, { userId: 7, role: "admin" })).toBe(false);
    expect(matchesLibraryQueryProtectedIdentity(key, { userId: "", role: "standard_user" })).toBe(false);
    expect(matchesLibraryQueryProtectedIdentity(["library", "v2", {}], { userId: 7, role: "standard_user" })).toBe(false);
    expect(matchesLibraryQueryProtectedIdentity(["other"], { userId: 7, role: "standard_user" })).toBe(false);
  });

  test("identity-scoped invalidation leaves another user's cache untouched", async () => {
    const userAKey = buildLibraryV2QueryKey({ userId: 7, role: "user", category: "movies" });
    const userBKey = buildLibraryV2QueryKey({ userId: 8, role: "user", category: "movies" });
    queryClient.setQueryData(userAKey, { marker: "a" });
    queryClient.setQueryData(userBKey, { marker: "b" });

    await invalidateLibraryQueriesForIdentity({ userId: 7, role: "user", refetchType: "none" });

    expect(queryClient.getQueryState(userAKey)?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(userBKey)?.isInvalidated).toBe(false);
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
      predicate: expect.any(Function),
      type: "active",
    });
  });

  test("authoritative progress patch is identity-scoped and never refreshes summaries itself", async () => {
    const userAV1Key = buildLibraryQueryKey({ userId: 1, role: "user", category: "movies" });
    const userAV2Key = buildLibraryV2QueryKey({ userId: 1, role: "user", category: "movies" });
    const userAShadowKey = buildLibraryShadowV2QueryKey({ userId: 1, role: "user", category: "movies" });
    const userBV1Key = buildLibraryQueryKey({ userId: 2, role: "user", category: "movies" });
    const userBV2Key = buildLibraryV2QueryKey({ userId: 2, role: "user", category: "movies" });
    const userBV1Payload = {
      items: [{ id: 42, progress_seconds: 31, progress_duration_seconds: 900, completed: false }],
      continue_watching: [{ id: 42, progress_seconds: 31, progress_duration_seconds: 900, completed: false }],
    };
    const userBV2Payload = {
      schema_version: "library-summary-v2",
      items_by_id: { "42": { id: 42, progress_seconds: 31, progress_duration_seconds: 900, completed: false } },
      sections: { item_ids: [42], continue_watching_item_ids: [42] },
    };
    queryClient.setQueryData(userAV1Key, {
      items: [{ id: 42, progress_seconds: 120, progress_duration_seconds: 900, completed: false }],
      continue_watching: [{ id: 42, progress_seconds: 120, progress_duration_seconds: 900, completed: false }],
    });
    queryClient.setQueryData(userAV2Key, {
      schema_version: "library-summary-v2",
      items_by_id: { "42": { id: 42, progress_seconds: 120, progress_duration_seconds: 900, completed: true } },
      sections: { item_ids: [42], continue_watching_item_ids: [42] },
    });
    queryClient.setQueryData(userAShadowKey, {
      schema_version: "library-summary-v2",
      items_by_id: { "42": { id: 42, progress_seconds: 120, progress_duration_seconds: 900, completed: false } },
      sections: { item_ids: [42], continue_watching_item_ids: [42] },
    });
    queryClient.setQueryData(userBV1Key, userBV1Payload, { updatedAt: 1111 });
    queryClient.setQueryData(userBV2Key, userBV2Payload, { updatedAt: 2222 });
    const userBV1UpdatedAt = queryClient.getQueryState(userBV1Key)?.dataUpdatedAt;
    const userBV2UpdatedAt = queryClient.getQueryState(userBV2Key)?.dataUpdatedAt;
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    const result = await patchLibraryProgressStateCaches({
      items: [{ id: 42, progress_seconds: 0, progress_duration_seconds: 901, completed: false }],
    }, { userId: 1, role: "user" });

    expect(result).toEqual({ patchedQueryCount: 3, membershipMayHaveChanged: true });
    expect(queryClient.getQueryData(userAV1Key).items[0]).toMatchObject({
      progress_seconds: 0,
      progress_duration_seconds: 901,
      completed: false,
    });
    expect(queryClient.getQueryData(userAV1Key).continue_watching[0].progress_seconds).toBe(0);
    expect(queryClient.getQueryData(userAV2Key).items_by_id["42"]).toMatchObject({
      progress_seconds: 0,
      progress_duration_seconds: 901,
      completed: false,
    });
    expect(queryClient.getQueryData(userAShadowKey).items_by_id["42"].progress_seconds).toBe(0);
    expect(queryClient.getQueryData(userBV1Key)).toBe(userBV1Payload);
    expect(queryClient.getQueryData(userBV2Key)).toBe(userBV2Payload);
    expect(queryClient.getQueryState(userBV1Key)?.dataUpdatedAt).toBe(userBV1UpdatedAt);
    expect(queryClient.getQueryState(userBV2Key)?.dataUpdatedAt).toBe(userBV2UpdatedAt);
    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  test("authoritative progress patch refuses to mutate caches without a complete identity", async () => {
    const key = buildLibraryV2QueryKey({ userId: 1, role: "user", category: "movies" });
    const payload = {
      schema_version: "library-summary-v2",
      items_by_id: { "42": { id: 42, progress_seconds: 120, completed: false } },
      sections: { item_ids: [42] },
    };
    queryClient.setQueryData(key, payload);

    await expect(patchLibraryProgressStateCaches({
      items: [{ id: 42, progress_seconds: 0, progress_duration_seconds: 900, completed: false }],
    })).resolves.toEqual({ patchedQueryCount: 0, membershipMayHaveChanged: false });

    expect(queryClient.getQueryData(key)).toBe(payload);
  });
});
